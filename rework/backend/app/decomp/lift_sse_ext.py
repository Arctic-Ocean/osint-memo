"""x86 SSSE3·SSE4.1·AVX2 패킹 연산 중 원소를 가로질러 섞거나 폭이 바뀌는 것들 → IR (lift_sse_vec.py 가 이어서 부른다).

- `pmaddubsw`(부호 없는 바이트 × 부호 있는 바이트, 이웃 둘을 포화 합산)·`pmulhrsw`(반올림 곱 상위)·`pmuldq`(짝수 번째 부호 있는 32비트 곱 → 64비트)
- `phaddw/d`·`phsubw/d`·`phaddsw`·`phsubsw`·`haddps/pd`·`hsubps/pd`: 이웃 원소끼리 계산해 (첫 입력 → 아래 조각, 둘째 입력 → 위 조각) 으로
- `pblendw`·`blendps/pd`·`pblendd`(즉값 마스크 블렌드. ymm 은 칸마다 마스크의 해당 비트를 쓴다)
- `roundps/pd`(즉값 반올림 방식: 가장 가까운 짝수·내림·올림·버림)
- `ptest`(ZF = (a & b) == 0, CF = (~a & b) == 0 — 플래그 종류 `ptest`)
"""
from __future__ import annotations

from capstone.x86 import X86_OP_IMM as OP_IMM

from . import neon_core as nc
from .ir import C, X, S, loc, binop, unop, zext, sext, trunc, cmp, ite, vlane, vpack, flags

HADD = {"phaddw": (2, "add"), "phaddd": (4, "add"), "phsubw": (2, "sub"), "phsubd": (4, "sub"), "phaddsw": (2, "qadd"), "phsubsw": (2, "qsub")}
HADDF = {"haddps": ("fadd", 4), "hsubps": ("fsub", 4), "haddpd": ("fadd", 8), "hsubpd": ("fsub", 8)}
BLEND = {"pblendw": 2, "blendps": 4, "blendpd": 8, "pblendd": 4}
ROUND_MODE = {0: "rint", 1: "floor", 2: "ceil", 3: "trunc"}
ROUND_VEC = {"rint": "frintn", "floor": "frintm", "ceil": "frintp", "trunc": "frintz"}
EXT_NAMES = {"pmaddubsw", "pmulhrsw", "pmuldq", "roundps", "roundpd", "ptest"} | set(HADD) | set(HADDF) | set(BLEND)


def round_mode(imm: int) -> str:
    """roundXX 의 즉값: 비트 2 가 서 있으면 현재 반올림 모드(기본: 가장 가까운 짝수), 아니면 아래 두 비트."""
    return "rint" if imm & 4 else ROUND_MODE[imm & 3]


def _clamp16(t: X) -> X:
    """4바이트 값 t 를 int16 범위로 포화해 2바이트로."""
    lo, hi = C(-32768, 4), C(32767, 4)
    return trunc(ite(cmp("slt", t, lo), lo, ite(cmp("sgt", t, hi), hi, t)), 2)


class X86VecExt:
    def _sse_ext(self, insn, m, d, x, rest):
        """해당하면 문장 목록, 아니면 None. x 는 첫 입력의 (아래, 위) 조각."""
        if m == "pmaddubsw" and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            outs = []
            for p, q in zip(x, y):
                ws = []
                for j in range(4):
                    t = [binop("mul", zext(vlane(p, 2 * j + k, 1, "u"), 4), sext(vlane(q, 2 * j + k, 1, "s"), 4), 4) for k in (0, 1)]
                    ws.append(_clamp16(binop("add", t[0], t[1], 4)))
                outs.append(vpack(ws, 2, "s"))
            return self.vec_store(insn, d, *outs)
        if m == "pmulhrsw" and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            outs = []
            for p, q in zip(x, y):
                ws = []
                for j in range(4):
                    t = binop("mul", sext(vlane(p, j, 2, "s"), 4), sext(vlane(q, j, 2, "s"), 4), 4)
                    ws.append(trunc(binop("sar", binop("add", binop("sar", t, C(14, 1), 4), C(1, 4), 4), C(1, 1), 4), 2))
                outs.append(vpack(ws, 2, "s"))
            return self.vec_store(insn, d, *outs)
        if m == "pmuldq" and len(rest) == 1:
            y = self.lanes_read(insn, rest[0])
            return self.vec_store(insn, d, *[binop("mul", sext(trunc(p, 4), 8), sext(trunc(q, 4), 8), 8) for p, q in zip(x, y)])
        if m in HADD and len(rest) == 1:
            esz, op = HADD[m]
            y = self.lanes_read(insn, rest[0])
            per = 8 // esz
            outs = []
            for src in (x, y):
                es = [vlane(src[k // per], k % per, esz, "s") for k in range(2 * per)]
                sums = []
                for i in range(per):
                    a, b = es[2 * i], es[2 * i + 1]
                    if op == "add":
                        sums.append(binop("add", a, b, esz))
                    elif op == "sub":
                        sums.append(binop("sub", a, b, esz))
                    else:                                                           # 포화(워드만)
                        t = binop("add" if op == "qadd" else "sub", sext(a, 4), sext(b, 4), 4)
                        sums.append(_clamp16(t))
                outs.append(vpack(sums, esz, "s"))
            return self.vec_store(insn, d, *outs)
        if m in HADDF and len(rest) == 1:
            fop, esz = HADDF[m]
            y = self.lanes_read(insn, rest[0])
            outs = []
            for src in (x, y):
                if esz == 8:
                    outs.append(X(fop, (src[0], src[1]), 8))
                else:
                    es = [vlane(src[k // 2], k % 2, 4, "f") for k in range(4)]
                    outs.append(vpack([X(fop, (es[0], es[1]), 4), X(fop, (es[2], es[3]), 4)], 4, "f"))
            return self.vec_store(insn, d, *outs)
        if m in BLEND and len(rest) == 2 and rest[1].type == OP_IMM:
            esz = BLEND[m]
            y = self.lanes_read(insn, rest[0])
            nel = 16 // esz
            base = 0 if m == "pblendw" else self._lane * nel
            per = 8 // esz
            outs = []
            for pc in range(2):
                bits = [(rest[1].imm >> (base + pc * per + j)) & 1 for j in range(per)]
                if all(b == bits[0] for b in bits):
                    outs.append((y if bits[0] else x)[pc])
                else:
                    outs.append(vpack([vlane((y if b else x)[pc], j, esz, "u") for j, b in enumerate(bits)], esz, "u"))
            return self.vec_store(insn, d, *outs)
        if m in ("roundps", "roundpd") and len(rest) == 2 and rest[1].type == OP_IMM:
            mode = round_mode(rest[1].imm)
            y = self.lanes_read(insn, rest[0])
            if m == "roundpd":
                return self.vec_store(insn, d, *[X("fround", (p,), 8, mode) for p in y])
            return self.vec_store(insn, d, *[nc.fun_op(ROUND_VEC[mode], 4, p) for p in y])
        return None

    def _ptest(self, insn, ops):
        """ptest·vptest: ZF = (a & b) == 0 · CF = (~a & b) == 0."""
        a_p = self.pieces_read(insn, ops[0])
        b_p = self.pieces_read(insn, ops[1])
        r1 = r2 = None
        for p, q in zip(a_p, b_p):
            t1 = binop("and", p, q, 8)
            t2 = binop("and", unop("not", p), q, 8)
            r1 = t1 if r1 is None else binop("or", r1, t1, 8)
            r2 = t2 if r2 is None else binop("or", r2, t2, 8)
        return [S("asg", insn.address, loc("F", 1), flags("ptest", (r1, r2), 8))]
