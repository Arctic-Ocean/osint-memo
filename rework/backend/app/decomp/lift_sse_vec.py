"""x86 SSE/SSE2/SSSE3/SSE4.1 패킹 연산(정수·실수 벡터) → IR.

xmm 은 하위·상위 64비트 두 위치 `xmmN`·`xmmNh` 다(lift_x86.py). ARM NEON 과 같은 벡터 IR(원소별 연산 ir.vop, 재배치 vshuf·vdup·vpack, 원소 꺼내기 vlane)로 올려서
출력 단계(render_vec.py)가 같은 방식으로 쓴다 — 128비트 연산은 두 조각에 같은 연산을 한 번씩 적용한다.
VEX(AVX) 128비트 3피연산자 형식은 v 접두사를 떼고 같은 표로 푼다. ymm 이 끼면 풀지 않는다(윗 128비트를 모르기 때문).
"""
from __future__ import annotations

import re

from capstone.x86 import X86_OP_REG as OP_REG, X86_OP_IMM as OP_IMM, X86_OP_MEM as OP_MEM

from . import ir
from . import neon_core as nc
from .neon_core import NeonUnsupported
from .ir import C, X, S, loc, mem, binop, unop, zext, sext, trunc, cmp, ite, vop, vlane, vpack, vdup, vshuf, vsetlane, add

# 이름 → (연산, 원소 폭, 부호/실수)  ← 2피연산자(목적지가 첫 입력)
PINT = {}
for _sfx, _e in (("b", 1), ("w", 2), ("d", 4), ("q", 8)):
    PINT["padd" + _sfx] = ("add", _e, "s")
    PINT["psub" + _sfx] = ("sub", _e, "s")
    PINT["pcmpeq" + _sfx] = ("ceq", _e, "s")
    PINT["pcmpgt" + _sfx] = ("cgt", _e, "s")
PINT.update({"pmullw": ("mul", 2, "s"), "pmulld": ("mul", 4, "s"),
             "pmaxsw": ("max", 2, "s"), "pmaxub": ("max", 1, "u"), "pminsw": ("min", 2, "s"), "pminub": ("min", 1, "u"),
             "pmaxsb": ("max", 1, "s"), "pmaxsd": ("max", 4, "s"), "pmaxuw": ("max", 2, "u"), "pmaxud": ("max", 4, "u"),
             "pminsb": ("min", 1, "s"), "pminsd": ("min", 4, "s"), "pminuw": ("min", 2, "u"), "pminud": ("min", 4, "u"),
             "paddsb": ("qadd", 1, "s"), "paddsw": ("qadd", 2, "s"), "paddusb": ("qadd", 1, "u"), "paddusw": ("qadd", 2, "u"),
             "psubsb": ("qsub", 1, "s"), "psubsw": ("qsub", 2, "s"), "psubusb": ("qsub", 1, "u"), "psubusw": ("qsub", 2, "u"),
             "pavgb": ("rhadd", 1, "u"), "pavgw": ("rhadd", 2, "u")})
PABS = {"pabsb": 1, "pabsw": 2, "pabsd": 4}
# 실수 패킹: 이름 → (연산, 원소 폭)
PFLT = {"addps": ("fadd", 4), "subps": ("fsub", 4), "mulps": ("fmul", 4), "divps": ("fdiv", 4), "maxps": ("fmax", 4), "minps": ("fmin", 4),
        "addpd": ("fadd", 8), "subpd": ("fsub", 8), "mulpd": ("fmul", 8), "divpd": ("fdiv", 8), "maxpd": ("fmax", 8), "minpd": ("fmin", 8)}
CMPCODE = {"eq": 0, "lt": 1, "le": 2, "unord": 3, "neq": 4, "nlt": 5, "nle": 6, "ord": 7}
VEX_UNARY = {"pabsb", "pabsw", "pabsd", "pshufd", "pshuflw", "pshufhw", "sqrtps", "sqrtpd", "cvtdq2ps", "cvttps2dq", "cvtps2dq",
             "pextrw", "pextrb", "pextrd", "pextrq", "pmovmskb", "movmskps", "movmskpd"}
SHIFT_IMM = {"psllw": ("shl", 2), "pslld": ("shl", 4), "psllq": ("shl", 8), "psrlw": ("shr", 2), "psrld": ("shr", 4), "psrlq": ("shr", 8),
             "psraw": ("sar", 2), "psrad": ("sar", 4)}


class X86Vec:
    """X86Lifter 에 섞어 쓴다(is_xmm·xmm_name·lanes_read·lanes_write·tmp·mem_addr·load_mem·read_op·write_op 사용)."""

    # ---- 읽기·쓰기 -----------------------------------------------------------------------
    def vec_ok(self, ops) -> bool:
        """ymm 이 끼면 풀지 않는다."""
        for o in ops:
            if o.type == OP_REG:
                n = self.cs.reg_name(o.reg) or ""
                if n.startswith("ymm"):
                    return False
        return True

    def vec_store(self, insn, dst, lo, hi):
        """(하위, 상위) 결과를 쓴다. 두 결과가 목적지 레지스터를 읽으면 임시 값을 거친다."""
        a = insn.address
        if dst.type == OP_REG:
            n = self.xmm_name(dst.reg)
            names = {n, n + "h"}

            def reads(e):
                return any(x.op == "loc" and x.aux in names for x in ir.walk(e))
            if reads(lo) or reads(hi):
                t0, t1 = self.tmp(8), self.tmp(8)
                return [S("asg", a, t0, lo), S("asg", a, t1, hi)] + self.lanes_write(insn, dst, t0, t1)
        return self.lanes_write(insn, dst, lo, hi)

    # ---- 진입 -------------------------------------------------------------------------------
    def _sse_vec(self, insn, m, ops):
        """해당하면 문장 목록, 아니면 None."""
        if not self.vec_ok(ops):
            return None
        try:
            return self._sse_vec_inner(insn, m, list(ops))
        except (NeonUnsupported, TypeError, AttributeError, KeyError, IndexError, ValueError, StopIteration):
            return None

    def _sse_vec_inner(self, insn, m, ops):
        a = insn.address
        # VEX 128비트: vpaddd x0, x1, x2 → 목적지 x0 = x1 op x2 (v 를 떼고 첫 입력을 목적지와 분리해 본다)
        vex = m.startswith("v") and m[1:] in self._vec_names()
        if vex and m[1:] in VEX_UNARY:                                    # 입력이 하나인 명령은 피연산자 모양이 SSE 와 같다
            m = m[1:]
            vex = False
        elif vex:
            if len(ops) < 3:
                return None
            m = m[1:]
            d, s1, rest = ops[0], ops[1], ops[2:]
        elif m.startswith("v"):
            return None
        if not vex:
            d, s1, rest = ops[0], ops[0], ops[1:]
        n = len(ops)
        if not self.is_xmm(d):
            return self._vec_to_gpr(insn, m, ops)
        x = self.lanes_read(insn, s1)
        # ---- 원소별 정수 2항 ----
        if m in PINT and len(rest) == 1:
            nm, esz, cls = PINT[m]
            y = self.lanes_read(insn, rest[0])
            return self.vec_store(insn, d, *[nc.bin_op(nm, cls, esz, p, q, False) for p, q in zip(x, y)])
        if m in PABS and len(rest) == 1:
            esz = PABS[m]
            y = self.lanes_read(insn, rest[0])
            return self.vec_store(insn, d, *[nc.un_op("abs", "s", esz, p) for p in y])
        if m == "pmuludq" and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            return self.vec_store(insn, d, *[binop("mul", zext(trunc(p, 4), 8), zext(trunc(q, 4), 8), 8) for p, q in zip(x, y)])
        if m in ("pmulhw", "pmulhuw") and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            ext = sext if m == "pmulhw" else zext
            outs = []
            for p, q in zip(x, y):
                lanes = [trunc(binop("shr", binop("mul", ext(vlane(p, j, 2, "u"), 4), ext(vlane(q, j, 2, "u"), 4), 4), C(16, 1), 4), 2) for j in range(4)]
                outs.append(vpack(lanes, 2, "u"))
            return self.vec_store(insn, d, *outs)
        if m == "pmaddwd" and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            outs = []
            for p, q in zip(x, y):
                dws = []
                for j in range(2):
                    t0 = binop("mul", sext(vlane(p, 2 * j, 2, "s"), 4), sext(vlane(q, 2 * j, 2, "s"), 4), 4)
                    t1 = binop("mul", sext(vlane(p, 2 * j + 1, 2, "s"), 4), sext(vlane(q, 2 * j + 1, 2, "s"), 4), 4)
                    dws.append(binop("add", t0, t1, 4))
                outs.append(vpack(dws, 4, "s"))
            return self.vec_store(insn, d, *outs)
        # ---- 마스크로 고르기: vblendvpd d, a, b, m (VEX) · blendvpd d, b, xmm0 (SSE) → 마스크 원소의 부호 비트가 1 이면 b, 아니면 a ----
        if m in ("blendvpd", "blendvps", "pblendvb") and len(rest) == 2:
            esz = {"blendvpd": 8, "blendvps": 4, "pblendvb": 1}[m]
            y = self.lanes_read(insn, rest[0])
            k = self.lanes_read(insn, rest[1])
            outs = []
            for p, q, mk in zip(x, y, k):
                if esz == 8 and mk.op == "neg" and mk.args[0].op == "zext" and mk.args[0].args[0].sz == 1:     # 비교 결과(0 또는 -1)로 만든 마스크
                    outs.append(ite(mk.args[0].args[0], q, p))
                    continue
                sel = nc.bin_op("cgt", "s", esz, C(0, 8), mk, False)
                outs.append(binop("or", binop("and", sel, q, 8), binop("and", unop("not", sel), p, 8), 8))
            return self.vec_store(insn, d, *outs)
        # ---- 시프트 ----
        if m in SHIFT_IMM and len(rest) == 1:
            kind, esz = SHIFT_IMM[m]
            return self._vec_shift(insn, d, x, rest[0], kind, esz)
        if m in ("pslldq", "psrldq") and len(rest) == 1 and rest[0].type == OP_IMM:
            k = rest[0].imm
            zero = [C(0, 8), C(0, 8)]
            if k >= 16:
                return self.vec_store(insn, d, C(0, 8), C(0, 8))
            if m == "psrldq":
                o = nc.ext_halves(k, list(x), zero, 16)
            else:
                o = nc.ext_halves(16 - k, zero, list(x), 16) if k else list(x)
            return self.vec_store(insn, d, *o)
        # ---- 재배치 ----
        if m == "pshufd" and len(rest) == 2 and rest[1].type == OP_IMM:
            src = self.lanes_read(insn, rest[0])
            imm = rest[1].imm & 0xff
            sel = [(imm >> (2 * i)) & 3 for i in range(4)]
            return self.vec_store(insn, d, *nc.perm_halves(sel, list(src), [], 4, 16))
        if m in ("pshuflw", "pshufhw") and len(rest) == 2 and rest[1].type == OP_IMM:
            src = self.lanes_read(insn, rest[0])
            imm = rest[1].imm & 0xff
            sel = tuple((imm >> (2 * i)) & 3 for i in range(4))
            if m == "pshuflw":
                return self.vec_store(insn, d, vshuf(src[0], src[0], sel, 2, "u"), src[1])
            return self.vec_store(insn, d, src[0], vshuf(src[1], src[1], sel, 2, "u"))
        if m.startswith(("punpckl", "punpckh")) and len(rest) == 1:
            esz = {"bw": 1, "wd": 2, "dq": 4, "qdq": 8}[m[7:]]
            y = self.lanes_read(insn, rest[0])
            idx = nc.zip_indices("zip2" if m.startswith("punpckh") else "zip1", 16, esz)
            return self.vec_store(insn, d, *nc.perm_halves(idx, list(x), list(y), esz, 16))
        if m in ("unpcklps", "unpckhps", "unpcklpd", "unpckhpd") and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            esz = 4 if m.endswith("ps") else 8
            idx = nc.zip_indices("zip2" if m.startswith("unpckh") else "zip1", 16, esz)
            return self.vec_store(insn, d, *nc.perm_halves(idx, list(x), list(y), esz, 16))
        if m == "shufpd" and len(rest) == 2 and rest[1].type == OP_IMM:
            y = self.lanes_read(insn, rest[0])
            imm = rest[1].imm
            return self.vec_store(insn, d, x[imm & 1], y[(imm >> 1) & 1])
        if m == "shufps" and len(rest) == 2 and rest[1].type == OP_IMM:
            y = self.lanes_read(insn, rest[0])
            imm = rest[1].imm & 0xff
            sel = [(imm >> (2 * i)) & 3 for i in range(4)]
            lo = nc.gather_pieces({(0, 0): x[0], (0, 1): x[1]}, [((0, s // 2), s % 2) for s in sel[:2]], 4)
            hi = nc.gather_pieces({(1, 0): y[0], (1, 1): y[1]}, [((1, s // 2), s % 2) for s in sel[2:]], 4)
            return self.vec_store(insn, d, lo, hi)
        if m == "palignr" and len(rest) == 2 and rest[1].type == OP_IMM:
            y = self.lanes_read(insn, rest[0])
            k = rest[1].imm
            if k >= 32:
                return self.vec_store(insn, d, C(0, 8), C(0, 8))
            if k >= 16:
                zero = [C(0, 8), C(0, 8)]
                return self.vec_store(insn, d, *nc.ext_halves(k - 16, list(x), zero, 16))
            return self.vec_store(insn, d, *nc.ext_halves(k, list(y), list(x), 16))
        if m in ("packsswb", "packuswb", "packssdw", "packusdw") and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            return self._vec_pack(insn, d, m, x, y)
        # ---- 실수 패킹 ----
        if m in PFLT and len(rest) == 1:
            nm, esz = PFLT[m]
            y = self.lanes_read(insn, rest[0])
            if esz == 8 and nm in ("fmax", "fmin"):                      # maxpd(a, b) = a > b ? a : b (C 의 fmax 와 NaN 처리가 다르다)
                return self.vec_store(insn, d, *[X(nm, (p, q), 8) for p, q in zip(x, y)])
            return self.vec_store(insn, d, *[nc.bin_op(nm, "f", esz, p, q, True) for p, q in zip(x, y)])
        if m in ("sqrtps", "sqrtpd") and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            esz = 4 if m == "sqrtps" else 8
            return self.vec_store(insn, d, *[nc.fun_op("fsqrt", esz, p) for p in y])
        if m.startswith("cmp") and m[-2:] in ("ps", "pd") and len(rest) >= 1:
            return self._vec_cmpf(insn, m, d, x, rest)
        if m in ("cvtdq2ps", "cvttps2dq", "cvtps2dq") and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            if m == "cvtdq2ps":
                return self.vec_store(insn, d, *[vop("scvtf", (p,), 4, "f") for p in y])
            if m == "cvttps2dq":
                return self.vec_store(insn, d, *[vop("fcvtzs", (p,), 4, "f") for p in y])
            return self.vec_store(insn, d, *[vop("fcvtzs", (vop("frintn", (p,), 4, "f"),), 4, "f") for p in y])
        # ---- 원소 넣기·꺼내기 ----
        if m in ("pinsrw", "pinsrb", "pinsrd", "pinsrq") and len(rest) == 2 and rest[1].type == OP_IMM:
            esz = {"w": 2, "b": 1, "d": 4, "q": 8}[m[-1]]
            per = 8 // esz
            idx = rest[1].imm & (16 // esz - 1)
            v = self.read_op(insn, rest[0], max(esz, 4) if rest[0].type == OP_REG else esz)
            sc = trunc(v, esz) if v.sz > esz else v
            half = idx // per
            cur = list(x)
            cur[half] = vsetlane(cur[half], sc, idx % per, esz, "u") if esz < 8 else sc
            return self.vec_store(insn, d, cur[0], cur[1])
        return None

    def _vec_names(self):
        if not hasattr(self, "_vec_name_set"):
            names = set(PINT) | set(PABS) | set(PFLT) | set(SHIFT_IMM) | {
                "pmuludq", "pmulhw", "pmulhuw", "pmaddwd", "pslldq", "psrldq", "pshufd", "pshuflw", "pshufhw", "punpcklbw", "punpcklwd", "punpckldq",
                "punpcklqdq", "punpckhbw", "punpckhwd", "punpckhdq", "punpckhqdq", "unpcklps", "unpckhps", "shufps", "palignr", "packsswb",
                "packuswb", "packssdw", "packusdw", "sqrtps", "sqrtpd", "cvtdq2ps", "cvttps2dq", "cvtps2dq", "pinsrw", "pinsrb", "pinsrd", "pinsrq",
                "pextrw", "pextrb", "pextrd", "pextrq", "pmovmskb", "movmskps", "movmskpd", "unpcklpd", "unpckhpd", "shufpd", "blendvpd", "blendvps", "pblendvb"}
            names |= {f"cmp{c}{t}" for c in CMPCODE for t in ("ps", "pd")} | {"cmpps", "cmppd"}
            self._vec_name_set = names
        return self._vec_name_set

    # ---- 세부 ---------------------------------------------------------------------------------
    def _vec_shift(self, insn, d, x, cnt_op, kind, esz):
        bits = 8 * esz
        if cnt_op.type == OP_IMM:
            n = cnt_op.imm & 0xff
            if n >= bits:
                if kind == "sar":
                    n = bits - 1
                else:
                    return self.vec_store(insn, d, C(0, 8), C(0, 8))
            if n == 0:
                return self.vec_store(insn, d, x[0], x[1])
            k = {"shl": "shl", "shr": "shr", "sar": "shr"}[kind]
            cls = "s" if kind == "sar" else "u"
            return self.vec_store(insn, d, *[nc.shift_op(k, n, esz, cls, p) for p in x])
        # 개수가 xmm/메모리의 아래 64비트: 폭 이상이면 밀어내기(논리)/부호 채우기(산술)
        c = self.lanes_read(insn, cnt_op)[0]
        big = cmp("ugt", c, C(bits - 1, 8))
        c8 = ite(big, C(bits if kind != "sar" else bits - 1, 8), c)
        outs = []
        for p in x:
            if esz == 8:
                r = ite(big, C(0, 8), binop("shl" if kind == "shl" else "shr", p, trunc(c8, 1), 8))
            else:
                cnt = trunc(c8, esz)
                neg = unop("neg", cnt) if kind != "shl" else cnt
                r = vop("shl", (p, vdup(neg, esz, "s")), esz, "s" if kind == "sar" else "u")
            outs.append(r)
        return self.vec_store(insn, d, *outs)

    def _vec_pack(self, insn, d, m, x, y):
        """포화 좁히기: 입력 둘의 모든 원소를 절반 폭으로 눌러 담는다(결과 하위 = x 의 원소들, 상위 = y 의 원소들)."""
        src = 2 if m.endswith("wb") else 4
        dst = src // 2
        signed_out = m.startswith("packss")
        lo_v, hi_v = (-(1 << (8 * dst - 1)), (1 << (8 * dst - 1)) - 1) if signed_out else (0, (1 << (8 * dst)) - 1)
        lanes = []
        for piece in (x[0], x[1], y[0], y[1]):
            for j in range(8 // src):
                v = vlane(piece, j, src, "s")
                lo_c, hi_c = C(lo_v, src), C(hi_v, src)
                r = ite(cmp("slt", v, lo_c), lo_c, ite(cmp("sgt", v, hi_c), hi_c, v))
                lanes.append(trunc(r, dst))
        per = 8 // dst
        halves = [vpack(lanes[i:i + per], dst, "u") for i in range(0, len(lanes), per)]
        return self.vec_store(insn, d, halves[0], halves[1])

    def _vec_cmpf(self, insn, m, d, x, rest):
        t = m[-2:]
        esz = 4 if t == "ps" else 8
        if m in ("cmpps", "cmppd"):
            if len(rest) != 2 or rest[1].type != OP_IMM:
                raise NeonUnsupported(m)
            code = rest[1].imm & 7
            y = self.lanes_read(insn, rest[0])
        else:
            nm = m[3:-2]
            if nm not in CMPCODE:
                raise NeonUnsupported(m)
            code = CMPCODE[nm]
            y = self.lanes_read(insn, rest[0])
        if code in (3, 7):
            raise NeonUnsupported(m)                                       # unordered/ordered 는 아직
        base = {0: ("fceq", False), 1: ("fcgt", True), 2: ("fcge", True), 4: ("fceq", False), 5: ("fcgt", True), 6: ("fcge", True)}[code]
        nm_, swap = base
        outs = []
        for p, q in zip(x, y):
            a_, b_ = (q, p) if swap else (p, q)
            r = nc.bin_op(nm_, "f", esz, a_, b_, True)
            if code >= 4:
                r = unop("not", r)
            outs.append(r)
        return self.vec_store(insn, d, *outs)

    def _vec_to_gpr(self, insn, m, ops):
        """벡터에서 정수 레지스터로: pextr*·pmovmskb·movmskps/pd."""
        a = insn.address
        if m in ("pextrw", "pextrb", "pextrd", "pextrq") and len(ops) == 3 and self.is_xmm(ops[1]) and ops[2].type == OP_IMM:
            esz = {"w": 2, "b": 1, "d": 4, "q": 8}[m[-1]]
            per = 8 // esz
            idx = ops[2].imm & (16 // esz - 1)
            lo, hi = self.lanes_read(insn, ops[1])
            sc = vlane(hi if idx >= per else lo, idx % per, esz, "u")
            if ops[0].type == OP_REG:
                full = self.reg_info(ops[0].reg)[1]
                sc = zext(sc, full) if sc.sz < full else sc
                return [self.write_op(insn, ops[0], sc)]
            return [self.write_op(insn, ops[0], sc)]
        if m in ("pmovmskb", "movmskps", "movmskpd") and len(ops) == 2 and ops[0].type == OP_REG and self.is_xmm(ops[1]):
            lo, hi = self.lanes_read(insn, ops[1])
            esz = 1 if m == "pmovmskb" else (4 if m == "movmskps" else 8)
            full = self.reg_info(ops[0].reg)[1]
            acc = None
            k = 0
            for piece in (lo, hi):
                for j in range(8 // esz):
                    lane = vlane(piece, j, esz, "u")
                    bit = zext(binop("shr", lane, C(8 * esz - 1, 1), esz), full) if esz > 1 or True else None
                    term = binop("shl", bit, C(k, 1), full) if k else bit
                    acc = term if acc is None else binop("or", acc, term, full)
                    k += 1
            return [self.write_op(insn, ops[0], acc)]
        return None
