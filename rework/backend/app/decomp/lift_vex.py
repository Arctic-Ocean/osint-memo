"""x86 VEX(AVX·AVX2·FMA3) 인코딩 → IR.

SSE 는 목적지가 첫 입력을 겸하지만(`addsd x0, x1` : x0 = x0 + x1) VEX 3피연산자 형식은 둘이 따로다
(`vaddsd x0, x1, x2` : x0.하위 = x1.하위 + x2.하위, 나머지 위쪽 비트는 x1 에서). 이 파일은 그 차이를 처리한다.

- 스칼라(sd·ss): 산술·min/max·sqrt·변환·비교(32가지 술어)·reg-reg mov
- FMA3: vfmadd/vfmsub/vfnmadd/vfnmsub × 132/213/231 × sd·ss·pd·ps(xmm)
- 128비트 논리·브로드캐스트·화살표 이동 (vpxor·vandps·vpbroadcastd·vmovddup·vmovlhps …)
- 2피연산자 형식(vmovaps·vmovd·vcvttsd2si·vucomisd·vpshufd …)은 v 를 떼고 SSE 경로에 맡긴다
- 128비트 패킹 정수·실수 연산은 lift_sse_vec.py 가 v 접두사를 보고 푼다

ymm(256비트)이 끼는 명령은 풀지 않는다 — 윗 128비트 위치가 없는 동안은 알 수 없는 명령으로 남긴다.
`vzeroupper` 는 xmm 만 모델링하므로 아무 일도 하지 않는다.
"""
from __future__ import annotations

import re

from capstone.x86 import X86_OP_REG as OP_REG, X86_OP_IMM as OP_IMM, X86_OP_MEM as OP_MEM

from . import ir
from .ir import C, X, S, loc, binop, unop, zext, trunc, insert, vdup
from . import neon_core as nc
from .neon_core import NeonUnsupported

SC_ARITH = {"add": "fadd", "sub": "fsub", "mul": "fmul", "div": "fdiv", "min": "fmin", "max": "fmax"}
LOGIC = {"xorps": "xor", "xorpd": "xor", "pxor": "xor", "andps": "and", "andpd": "and", "pand": "and",
         "orps": "or", "orpd": "or", "por": "or", "andnps": "andn", "andnpd": "andn", "pandn": "andn"}
# vcmp<이름>sd/ss 의 술어 번호 (0..31)
CMP_PRED = {"eq": 0, "lt": 1, "le": 2, "unord": 3, "neq": 4, "nlt": 5, "nle": 6, "ord": 7,
            "eq_uq": 8, "nge": 9, "ngt": 10, "false": 11, "neq_oq": 12, "ge": 13, "gt": 14, "true": 15,
            "eq_os": 16, "lt_oq": 17, "le_oq": 18, "unord_s": 19, "neq_us": 20, "nlt_uq": 21, "nle_uq": 22, "ord_s": 23,
            "eq_us": 24, "nge_uq": 25, "ngt_uq": 26, "false_os": 27, "neq_os": 28, "ge_oq": 29, "gt_oq": 30, "true_us": 31}
TWO_OP = {"movaps", "movups", "movapd", "movupd", "movdqa", "movdqu", "lddqu", "movntdq", "movntps", "movntpd", "movd", "movq",
          "movsd", "movss", "movhps", "movhpd", "movlps", "movlpd", "comisd", "ucomisd", "comiss", "ucomiss", "cvttsd2si", "cvttss2si"}
FMA_RE = re.compile(r"^(fmadd|fmsub|fnmadd|fnmsub)(132|213|231)(sd|ss|pd|ps)$")
BROADCAST = {"pbroadcastb": 1, "pbroadcastw": 2, "pbroadcastd": 4, "pbroadcastq": 8, "broadcastss": 4, "movddup": 8}


def fcond(A: X, B: X, pred: int) -> X:
    """비교 술어(0..31, 16 이상은 신호 방식만 다르다) → 불리언 식. A·B 는 같은 폭의 IEEE 비트 패턴."""
    p = pred & 15
    feq = X("feq", (A, B), 1)
    flt = X("flt", (A, B), 1)
    fle = X("fle", (A, B), 1)
    uno = X("funord", (A, B), 1)
    return [feq, flt, fle, uno, ir.invert(feq), ir.invert(flt), ir.invert(fle), ir.invert(uno),
            ir.lor(feq, uno), ir.invert(X("fle", (B, A), 1)), ir.invert(X("flt", (B, A), 1)), C(0, 1),
            ir.land(ir.invert(feq), ir.invert(uno)), X("fle", (B, A), 1), X("flt", (B, A), 1), C(1, 1)][p]


class X86Vex:
    """X86Lifter 에 섞어 쓴다(X86Vec 의 lanes_read·vec_store·vec_ok 와 X86Lifter 의 f_read·is_xmm·xmm_name 사용)."""

    def vex_lift(self, insn, m, ops):
        """VEX 로 인코딩된 명령이면 문장 목록, 아니면 None."""
        if len(m) < 4 or m[0] != "v":
            return None
        if m == "vzeroupper":
            return []
        base = m[1:]
        if not self.vec_ok(ops):
            return None
        try:
            r = self._vex(insn, base, list(ops))
        except (NeonUnsupported, TypeError, AttributeError, KeyError, IndexError, ValueError, StopIteration):
            return None
        if r is not None:
            return r
        return self._sse_vec(insn, m, ops)

    def _vex(self, insn, base, ops):
        n = len(ops)
        if n == 2 and base in TWO_OP:                                         # SSE 형식과 피연산자가 같다: v 만 뗀다
            return self._sse(insn, base, ops)
        if n == 0 or not self.is_xmm(ops[0]):
            return None
        mm = FMA_RE.match(base)
        if mm and n == 3:
            return self._vex_fma(insn, mm, ops)
        if base in LOGIC and n == 3:
            return self._vex_logic(insn, base, ops)
        if base in BROADCAST and n == 2:
            return self._vex_broadcast(insn, base, ops)
        if base in ("movsd", "movss", "movhps", "movhpd", "movlps", "movlpd"):
            if n == 3 and self.is_xmm(ops[1]):
                return self._vex_move3(insn, base, ops)
            return None
        if n >= 3 and self.is_xmm(ops[1]):
            r = self._vex_scalar(insn, base, ops)
            if r is not None:
                return r
        if n == 3 and base in ("movlhps", "movhlps") and self.is_xmm(ops[1]) and self.is_xmm(ops[2]):
            x = self.lanes_read(insn, ops[1])
            y = self.lanes_read(insn, ops[2])
            return self.vec_store(insn, ops[0], x[0], y[0]) if base == "movlhps" else self.vec_store(insn, ops[0], y[1], x[1])
        return None

    # ---- 스칼라 ---------------------------------------------------------------------------
    def _vex_sc_store(self, insn, d, s1, val, sz):
        """스칼라 결과를 쓴다: 아래 sz 바이트 = val, 나머지는 첫 입력 s1 에서."""
        a = insn.address
        nd, n1 = self.xmm_name(d.reg), self.xmm_name(s1.reg)
        lo = val if sz == 8 else insert(loc(n1, 8), val)
        out = [S("asg", a, loc(nd, 8), lo)]
        if nd != n1:
            out.append(S("asg", a, loc(nd + "h", 8), loc(n1 + "h", 8)))
        return out

    def _vex_scalar(self, insn, base, ops):
        n = len(ops)
        d, s1 = ops[0], ops[1]
        if base[-2:] not in ("sd", "ss"):
            return None
        sz = 8 if base.endswith("sd") else 4
        s2 = ops[2]
        if base[:3] in SC_ARITH and len(base) == 5 and n == 3:
            val = X(SC_ARITH[base[:3]], (self.f_read(insn, s1, sz), self.f_read(insn, s2, sz)), sz)
            return self._vex_sc_store(insn, d, s1, val, sz)
        if base in ("sqrtsd", "sqrtss") and n == 3:
            return self._vex_sc_store(insn, d, s1, X("fsqrt", (self.f_read(insn, s2, sz),), sz), sz)
        if base in ("cvtsi2sd", "cvtsi2ss") and n == 3:
            return self._vex_sc_store(insn, d, s1, X("i2f", (self.read_op(insn, s2),), sz, "s"), sz)
        if base == "cvtss2sd" and n == 3:
            return self._vex_sc_store(insn, d, s1, X("f2f", (self.f_read(insn, s2, 4),), 8), 8)
        if base == "cvtsd2ss" and n == 3:
            return self._vex_sc_store(insn, d, s1, X("f2f", (self.f_read(insn, s2, 8),), 4), 4)
        if base.startswith("cmp") and ((n == 3 and base[3:-2] in CMP_PRED) or (n == 4 and ops[3].type == OP_IMM and base[3:-2] == "")):
            pred = CMP_PRED[base[3:-2]] if n == 3 else ops[3].imm & 31
            A, B = self.f_read(insn, s1, sz), self.f_read(insn, s2, sz)
            c = fcond(A, B, pred)
            return self._vex_sc_store(insn, d, s1, unop("neg", zext(c, sz)), sz)    # 참이면 모든 비트 1
        return None

    def _vex_move3(self, insn, base, ops):
        """vmovsd·vmovss reg-reg(3피연산자) · vmovhps/lps/hpd/lpd 메모리 적재(3피연산자)."""
        d, s1, s2 = ops
        nd, n1 = self.xmm_name(d.reg), self.xmm_name(s1.reg)
        a = insn.address
        if base in ("movsd", "movss"):
            if not self.is_xmm(s2):
                return None
            v = loc(self.xmm_name(s2.reg), 8)
            return self._vex_sc_store(insn, d, s1, v if base == "movsd" else trunc(v, 4), 8 if base == "movsd" else 4)
        if s2.type != OP_MEM:
            return None
        v = self.load_mem(self.mem_addr(insn, s2), 8)
        lo, hi = loc(n1, 8), loc(n1 + "h", 8)
        if base.startswith("movh"):
            return self.vec_store(insn, d, lo, v)
        return self.vec_store(insn, d, v, hi)

    # ---- FMA3 ---------------------------------------------------------------------------
    def _vex_fma(self, insn, mm, ops):
        kind, order, t = mm.group(1), mm.group(2), mm.group(3)
        d, s2, s3 = ops
        if not (self.is_xmm(s2)):
            return None
        neg_prod = kind in ("fnmadd", "fnmsub")
        neg_acc = kind in ("fmsub", "fnmsub")
        esz = 8 if t.endswith("d") else 4
        scalar = t[0] == "s"

        def pick(slot):
            return {"d": d, "s2": s2, "s3": s3}[slot]
        # 132: d*s3 + s2 · 213: s2*d + s3 · 231: s2*s3 + d  → (곱 두 개, 더할 것)
        ma, mb, mc = {"132": ("d", "s3", "s2"), "213": ("s2", "d", "s3"), "231": ("s2", "s3", "d")}[order]
        if scalar:
            A, B, Cc = (self.f_read(insn, pick(k), esz) for k in (ma, mb, mc))
            if neg_prod:
                A = X("fneg", (A,), esz)
            if neg_acc:
                Cc = X("fneg", (Cc,), esz)
            return [self.f_write(insn, d, X("ffma", (A, B, Cc), esz), esz)]
        pa, pb, pc = (self.lanes_read(insn, pick(k)) for k in (ma, mb, mc))
        outs = []
        for A, B, Cc in zip(pa, pb, pc):
            if neg_prod:
                A = nc.fun_op("fneg", esz, A)
            if neg_acc:
                Cc = nc.fun_op("fneg", esz, Cc)
            outs.append(nc.mla_op("fmla", esz, True, Cc, A, B))
        return self.vec_store(insn, d, *outs)

    # ---- 논리·브로드캐스트 -----------------------------------------------------------------
    def _vex_logic(self, insn, base, ops):
        op = LOGIC[base]
        d, s1, s2 = ops
        if not self.is_xmm(s1):
            return None
        Z = C(0, 8)
        if op == "xor" and s2.type == OP_REG and s1.reg == s2.reg:
            return self.vec_store(insn, d, Z, Z)
        l1, h1 = self.lanes_read(insn, s1)
        if base in ("xorps", "xorpd", "andps", "andpd") and s2.type == OP_MEM:
            mk = self._mask_lanes(insn, s2)
            if mk is not None:                                                    # 부동소수점 부호·절댓값 마스크
                mlo, mhi = mk
                lo2 = None
                if op == "xor" and mlo == 0x8000000000000000:
                    lo2 = X("fneg", (l1,), 8)
                elif op == "xor" and mlo == 0x80000000:
                    lo2 = insert(l1, X("fneg", (trunc(l1, 4),), 4))
                elif op == "and" and mlo == 0x7fffffffffffffff:
                    lo2 = X("fabs", (l1,), 8)
                elif op == "and" and mlo == 0x7fffffff:
                    lo2 = zext(X("fabs", (trunc(l1, 4),), 4), 8)
                if lo2 is not None:
                    return self.vec_store(insn, d, lo2, binop(op, h1, C(mhi, 8), 8))
        l2, h2 = self.lanes_read(insn, s2)
        if op == "andn":
            l1, h1 = unop("not", l1), unop("not", h1)
            op = "and"
        return self.vec_store(insn, d, binop(op, l1, l2, 8), binop(op, h1, h2, 8))

    def _vex_broadcast(self, insn, base, ops):
        d, src = ops
        esz = BROADCAST[base]
        if src.type == OP_REG:
            if not self.is_xmm(src):
                return None
            lo, _ = self.lanes_read(insn, src)
            e = lo if esz == 8 else trunc(lo, esz)
        elif src.type == OP_MEM:
            e = self.load_mem(self.mem_addr(insn, src), esz)
        else:
            return None
        piece = e if esz == 8 else vdup(e, esz, "u")
        return self.vec_store(insn, d, piece, piece)
