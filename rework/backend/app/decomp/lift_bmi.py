"""x86 비트 스캔·BMI1·BMI2 정수 명령 → IR: bsf·bsr·tzcnt·lzcnt·popcnt · andn·blsr·blsmsk·blsi·bextr·bzhi · shlx·shrx·sarx·rorx · mulx · movbe.

AVX2 문자열 함수(`strlen_avx2` 등)가 `vpmovmskb` 다음에 늘 쓰는 명령들이다.

- 개수 세기는 도우미 `__tzcnt32(x)`·`__lzcnt32(x)`·`__popcnt32(x)`·`__bsr32(x)` 로 낸다(0 이면 tzcnt·lzcnt 는 폭, bsr 은 정해지지 않음). 폭은 16·32·64.
- `bsf`/`bsr` 는 원본이 0 이면 목적지가 그대로 남는다(실제 CPU 동작) — `x ? 스캔(x) : 옛 값` 으로 쓴다.
- 플래그: bsf/bsr → ZF = (원본 == 0), tzcnt·lzcnt·blsr·blsmsk → CF = (원본 == 0)·ZF = (결과 == 0)(kind `cfz`), blsi → CF = (원본 != 0)(`cfnz`),
  popcnt·andn → ZF·SF 는 결과 · CF=OF=0(`logic`), bzhi·bextr → ZF·SF 만 안다(`res`). shlx 계열은 플래그를 건드리지 않는다.
"""
from __future__ import annotations

from capstone import CS_OP_REG, CS_OP_IMM, CS_OP_MEM

from .ir import C, X, S, loc, binop, unop, zext, trunc, cmp, ite, flags, mask

FLAGS = "F"
SCAN = {"tzcnt": "tzcnt", "lzcnt": "lzcnt", "popcnt": "popcnt", "bsf": "tzcnt", "bsr": "bsr"}
BMI1 = {"andn", "blsr", "blsmsk", "blsi", "bextr"}
SHIFTX = {"shlx": "shl", "shrx": "shr", "sarx": "sar"}


class X86Bmi:
    """X86Lifter 에 섞어 쓴다(read_op·write_op·write_reg·reg_info·tmp·mem_addr·load_mem·rn 사용)."""

    def bmi_lift(self, insn, m, ops):
        """해당하면 문장 목록, 아니면 None."""
        n = len(ops)
        a = insn.address
        if m in SCAN and n == 2 and ops[0].type == CS_OP_REG and ops[0].size in (2, 4, 8):
            sz = ops[0].size
            src = self.read_op(insn, ops[1], sz)
            if src.sz != sz:
                return None
            out = []
            if src.op not in ("loc", "c"):                              # 두 번 쓰이니 임시 값으로
                t = self.tmp(sz)
                out.append(S("asg", a, t, src))
                src = t
            res = X("bi", (src,), sz, SCAN[m])
            if m in ("bsf", "bsr"):                                     # 원본이 0 이면 목적지는 그대로
                res = ite(cmp("eq", src, C(0, sz)), self.read_op(insn, ops[0], sz), res)
                out.append(self.write_op(insn, ops[0], res))
                out.append(S("asg", a, loc(FLAGS, 1), flags("logic", (src,), sz)))
                return out
            r = self.tmp(sz)
            out.append(S("asg", a, r, res))
            out.append(self.write_op(insn, ops[0], r))
            kind = "logic" if m == "popcnt" else "cfz"
            out.append(S("asg", a, loc(FLAGS, 1), flags(kind, (r,) if kind == "logic" else (src, r), sz)))
            return out
        if m in BMI1 and n in (2, 3) and ops[0].type == CS_OP_REG:
            return self._bmi1(insn, m, ops)
        if m == "bzhi" and n == 3:
            sz = ops[0].size
            x = self.read_op(insn, ops[1], sz)
            idx = binop("and", self._sized(self.read_op(insn, ops[2]), sz), C(0xff, sz), sz)
            r = ite(cmp("uge", idx, C(8 * sz, sz)), x, binop("and", x, binop("sub", binop("shl", C(1, sz), idx, sz), C(1, sz), sz), sz))
            t = self.tmp(sz)
            return [S("asg", a, t, r), self.write_op(insn, ops[0], t), S("asg", a, loc(FLAGS, 1), flags("res", (t,), sz))]
        if m in SHIFTX and n == 3:
            sz = ops[0].size
            x = self.read_op(insn, ops[1], sz)
            cnt = binop("and", self._sized(self.read_op(insn, ops[2]), sz), C(8 * sz - 1, sz), sz)
            return [self.write_op(insn, ops[0], binop(SHIFTX[m], x, cnt, sz))]
        if m == "rorx" and n == 3 and ops[2].type == CS_OP_IMM:
            sz = ops[0].size
            x = self.read_op(insn, ops[1], sz)
            return [self.write_op(insn, ops[0], X("ror", (x, C(ops[2].imm & (8 * sz - 1), 1)), sz))]
        if m == "mulx" and n == 3 and ops[0].type == CS_OP_REG and ops[1].type == CS_OP_REG:
            sz = ops[0].size
            dx = self.rn("rdx" if sz == 8 else "edx")
            src = self.read_op(insn, ops[2], sz)
            ta, tb = self.tmp(sz), self.tmp(sz)
            return [S("asg", a, ta, dx), S("asg", a, tb, src),
                    self.write_op(insn, ops[1], binop("mul", ta, tb, sz)), self.write_op(insn, ops[0], binop("mulhu", ta, tb, sz))]
        if m == "movbe" and n == 2:
            sz = ops[0].size
            if ops[0].type == CS_OP_REG and ops[1].type == CS_OP_MEM:
                return [self.write_op(insn, ops[0], X("bi", (self.read_op(insn, ops[1], sz),), sz, "rev"))]
            if ops[0].type == CS_OP_MEM and ops[1].type == CS_OP_REG:
                return [self.write_op(insn, ops[0], X("bi", (self.read_op(insn, ops[1], sz),), sz, "rev"))]
        return None

    @staticmethod
    def _sized(v: X, sz: int) -> X:
        return v if v.sz == sz else (trunc(v, sz) if v.sz > sz else zext(v, sz))

    def _bmi1(self, insn, m, ops):
        a = insn.address
        sz = ops[0].size
        n = len(ops)
        if m == "andn" and n == 3:
            r = binop("and", unop("not", self.read_op(insn, ops[1], sz)), self.read_op(insn, ops[2], sz), sz)
            t = self.tmp(sz)
            return [S("asg", a, t, r), self.write_op(insn, ops[0], t), S("asg", a, loc(FLAGS, 1), flags("logic", (t,), sz))]
        if m in ("blsr", "blsmsk", "blsi") and n == 2:
            src = self.read_op(insn, ops[1], sz)
            out = []
            if src.op not in ("loc", "c"):
                t0 = self.tmp(sz)
                out.append(S("asg", a, t0, src))
                src = t0
            dec = binop("sub", src, C(1, sz), sz)
            r = {"blsr": binop("and", src, dec, sz), "blsmsk": binop("xor", src, dec, sz),
                 "blsi": binop("and", src, unop("neg", src), sz)}[m]
            t = self.tmp(sz)
            out += [S("asg", a, t, r), self.write_op(insn, ops[0], t),
                    S("asg", a, loc(FLAGS, 1), flags("cfnz" if m == "blsi" else "cfz", (src, t), sz))]
            return out
        if m == "bextr" and n == 3:
            x = self.read_op(insn, ops[1], sz)
            ctl = self._sized(self.read_op(insn, ops[2]), sz)
            tx, tc = self.tmp(sz), self.tmp(sz)
            start = binop("and", tc, C(0xff, sz), sz)
            length = binop("and", binop("shr", tc, C(8, 1), sz), C(0xff, sz), sz)
            shifted = ite(cmp("uge", start, C(8 * sz, sz)), C(0, sz), binop("shr", tx, start, sz))
            keep = ite(cmp("uge", length, C(8 * sz, sz)), C(mask(sz), sz), binop("sub", binop("shl", C(1, sz), length, sz), C(1, sz), sz))
            r = binop("and", shifted, keep, sz)
            t = self.tmp(sz)
            return [S("asg", a, tx, x), S("asg", a, tc, ctl), S("asg", a, t, r), self.write_op(insn, ops[0], t),
                    S("asg", a, loc(FLAGS, 1), flags("res", (t,), sz))]
        return None
