"""ARM64 Advanced SIMD(NEON) 벡터 명령 → IR.

v0..v31 은 하위·상위 64비트 두 위치 `v0`·`v0h` 다(lift_arm64_fp.py). 한 위치는 폭 esz 원소 8/esz 개의 벡터 한 조각으로 보고,
128비트 배열(16b·8h·4s·2d)의 원소별 명령은 두 조각에 같은 연산을 한 번씩 적용한다. 64비트 배열(8b·4h·2s)은 하위 조각만 쓰고 상위 64비트를 0 으로 만든다.
원소별 정수·실수 연산은 ir.vop(이름, 인자, esz, 부호/실수), 원소 재배치는 vshuf·vdup·vpack·vsetlane, 원소 꺼내기는 vlane 이다.
원소 폭이 8바이트(2d)이면 조각 하나가 원소 하나이므로 평범한 64비트 정수·실수 연산이다.
"""
from __future__ import annotations

from capstone import arm64 as c64
from capstone.arm64 import ARM64_OP_REG as OP_REG, ARM64_OP_IMM as OP_IMM, ARM64_OP_MEM as OP_MEM, ARM64_OP_FP as OP_FP

from . import ir
from .ir import C, X, S, loc, mem, binop, unop, zext, sext, trunc, cmp, ite, vop, vlane, vpack, vdup, vshuf, vsetlane

# Capstone 배열 상수 → (원소 폭, 전체 바이트)
VAS = {1: (1, 16), 2: (1, 8), 5: (2, 16), 6: (2, 8), 9: (4, 16), 10: (4, 8), 12: (8, 16), 13: (8, 8)}

# 이름 → (vop 이름, 부호/실수)
INT_BIN = {"add": ("add", "s"), "sub": ("sub", "s"), "mul": ("mul", "s"), "smax": ("max", "s"), "umax": ("max", "u"), "smin": ("min", "s"),
           "umin": ("min", "u"), "sabd": ("abd", "s"), "uabd": ("abd", "u"), "sqadd": ("qadd", "s"), "uqadd": ("qadd", "u"),
           "sqsub": ("qsub", "s"), "uqsub": ("qsub", "u"), "shadd": ("hadd", "s"), "uhadd": ("hadd", "u"), "srhadd": ("rhadd", "s"),
           "urhadd": ("rhadd", "u"), "sshl": ("shl", "s"), "ushl": ("shl", "u"), "cmeq": ("ceq", "s"), "cmgt": ("cgt", "s"), "cmge": ("cge", "s"),
           "cmhi": ("cgt", "u"), "cmhs": ("cge", "u"), "cmtst": ("tst", "s")}
FLT_BIN = {"fadd": "fadd", "fsub": "fsub", "fmul": "fmul", "fdiv": "fdiv", "fmax": "fmax", "fmin": "fmin", "fmaxnm": "fmaxnm", "fminnm": "fminnm",
           "fabd": "fabd", "fcmeq": "fceq", "fcmgt": "fcgt", "fcmge": "fcge", "facgt": "facgt", "facge": "facge"}
INT_UN = {"neg": ("neg", "s"), "abs": ("abs", "s"), "clz": ("clz", "s"), "cnt": ("cnt", "u"), "rbit": ("rbit", "u")}
FLT_UN = {"fneg": "fneg", "fabs": "fabs", "fsqrt": "fsqrt", "frintn": "frintn", "frintm": "frintm", "frintp": "frintp", "frintz": "frintz",
          "frinta": "frinta", "frintx": "frintx", "frecpe": "frecpe", "frsqrte": "frsqrte"}
CMP_ZERO = {"cmeq": ("ceq", "s"), "cmgt": ("cgt", "s"), "cmge": ("cge", "s"), "cmle": ("cge", "s"), "cmlt": ("cgt", "s")}
CVT = {"scvtf": "scvtf", "ucvtf": "ucvtf", "fcvtzs": "fcvtzs", "fcvtzu": "fcvtzu"}


class NeonUnsupported(Exception):
    pass


class A64Neon:
    """A64Fp 에 섞어 쓴다(self.fp_info·is_fpop·lanes_of·lanes_write·fwrite·read_reg·write_reg·mem_addr·tmp·load_mem·reg_sz 사용)."""

    # ---- 피연산자 ------------------------------------------------------------------------
    def vshape(self, op):
        """벡터 피연산자의 (원소 폭, 전체 바이트). 스칼라·원소 지정이면 None."""
        if op.type != OP_REG or not self.is_fpop(op) or op.vas == 0:
            return None
        return VAS.get(op.vas)

    def vhalves(self, op, total=None):
        """벡터 레지스터 피연산자의 (하위, 상위 또는 None) 조각."""
        k, n = self.fp_info(op.reg)
        lo = loc(f"v{n}", 8)
        hi = loc(f"v{n}h", 8)
        total = total or (VAS[op.vas][1] if op.vas in VAS else 16)
        return (lo, hi if total == 16 else None)

    def vout(self, a, d, lo, hi, total):
        """대상 벡터에 쓴다: 64비트 배열이면 상위 64비트를 0 으로."""
        k, n = self.fp_info(d.reg)
        return [S("asg", a, loc(f"v{n}", 8), lo), S("asg", a, loc(f"v{n}h", 8), hi if total == 16 else C(0, 8))]

    def velem(self, op, esz):
        """원소 지정 피연산자 `v1.s[2]` 의 (조각, 조각 안 번호)."""
        k, n = self.fp_info(op.reg)
        per = 8 // esz
        i = op.vector_index
        return (loc(f"v{n}" if i < per else f"v{n}h", 8), i % per)

    def _bcast_elem(self, op, esz, cls):
        """by-element 피연산자를 한 조각으로 편 값(모든 원소가 같은 값)."""
        half, i = self.velem(op, esz)
        sc = vlane(half, i, esz, cls)
        if esz == 8:
            return sc
        return vdup(sc, esz, cls)

    def lanewise(self, a, d, fn, srcs, esz, total):
        """srcs: 연산에 들어가는 (하위, 상위) 쌍들 또는 상수 식. fn(조각 인자들) → 식."""
        outs = []
        for h in range(2 if total == 16 else 1):
            outs.append(fn([s[h] if isinstance(s, tuple) else s for s in srcs]))
        return self.vout(a, d, outs[0], outs[1] if len(outs) > 1 else None, total)

    # ---- 진입점 --------------------------------------------------------------------------
    def _neon(self, insn, m, ops, a):
        """벡터 명령이면 문장 목록, 해석하지 못하면 None."""
        import re
        # Capstone 은 원소 지정 피연산자 `v0.s[0]` 의 원소 폭(vas)을 번호가 0 일 때 비워 두기도 한다 → 텍스트의 `.s[` 에서 읽는다
        sizes = [{"b": 1, "h": 2, "s": 4, "d": 8}[c_] for c_ in re.findall(r"\.([bhsd])\[", insn.op_str)]
        self._esz_by_op = {}
        k = 0
        for o in ops:
            if o.type == OP_REG and self.is_fpop(o) and o.vector_index >= 0 and k < len(sizes):
                self._esz_by_op[id(o)] = sizes[k]
                k += 1
        try:
            return self._neon_inner(insn, m, ops, a)
        except (NeonUnsupported, TypeError, AttributeError, KeyError, IndexError, ValueError, StopIteration):      # 해석 못 하는 형식은 그 명령만 미해석으로 둔다
            return None

    def _neon_inner(self, insn, m, ops, a):
        n = len(ops)
        d = ops[0]
        if m in ("ld1", "ld2", "ld3", "ld4", "st1", "st2", "st3", "st4", "ld1r", "ld2r", "ld3r", "ld4r"):
            return self._neon_ldst(insn, m, ops, a)
        sh = self.vshape(d)
        if sh is None:
            if d.type == OP_REG and self.is_fpop(d) and d.vector_index >= 0:             # ins v0.s[1], w1 / v1.s[2] (목적지가 한 원소)
                r = self._neon_perm(insn, m, ops, a, 0, 0)
                if r is not None:
                    return r
            return self._neon_scalar_dest(insn, m, ops, a)
        esz, total = sh
        per = 8 // esz
        # ---- 비트 연산(원소와 무관) ----
        if m in ("and", "orr", "eor", "bic", "orn") and n == 3 and self.vshape(ops[2]):
            l1, l2 = self.vhalves(ops[1], total), self.vhalves(ops[2], total)
            op = {"and": "and", "orr": "or", "eor": "xor", "bic": "and", "orn": "or"}[m]

            def fn(x):
                y = unop("not", x[1]) if m in ("bic", "orn") else x[1]
                return binop(op, x[0], y, 8)
            return self.lanewise(a, d, fn, [l1, l2], esz, total)
        if m in ("bic", "orr") and n == 2 and ops[1].type == OP_IMM:                       # bic/orr v0.4s, #imm{, lsl #n}
            imm = self._imm_pattern(ops[1], esz)
            old = self.vhalves(d, total)
            op = (lambda x: binop("and", x, C(~imm & ir.mask(8), 8), 8)) if m == "bic" else (lambda x: binop("or", x, C(imm, 8), 8))
            return self.lanewise(a, d, lambda x: op(x[0]), [old], esz, total)
        if m in ("not", "mvn") and n == 2:
            return self.lanewise(a, d, lambda x: unop("not", x[0]), [self.vhalves(ops[1], total)], esz, total)
        if m in ("bsl", "bit", "bif") and n == 3:                                            # 비트 선택
            dd, nn, mm = self.vhalves(d, total), self.vhalves(ops[1], total), self.vhalves(ops[2], total)

            def sel(mask_, x, y):                                                           # mask 가 1 인 비트는 x, 0 인 비트는 y
                return binop("or", binop("and", x, mask_, 8), binop("and", y, unop("not", mask_), 8), 8)
            if m == "bsl":
                return self.lanewise(a, d, lambda x: sel(x[0], x[1], x[2]), [dd, nn, mm], esz, total)
            if m == "bit":
                return self.lanewise(a, d, lambda x: sel(x[2], x[1], x[0]), [dd, nn, mm], esz, total)
            return self.lanewise(a, d, lambda x: sel(x[2], x[0], x[1]), [dd, nn, mm], esz, total)
        # ---- 상수 만들기 ----
        if m in ("movi", "mvni", "fmov") and n == 2:
            return self._neon_imm(insn, m, ops, a, esz, total)
        # ---- 원소별 정수 이항 ----
        if m in INT_BIN and n == 3 and ops[2].type == OP_REG:
            nm, cls = INT_BIN[m]
            return self._neon_bin(a, d, ops, esz, total, nm, cls, flt=False)
        if m in FLT_BIN and n == 3 and ops[2].type == OP_REG:
            return self._neon_bin(a, d, ops, esz, total, FLT_BIN[m], "f", flt=True)
        if m in ("fcmeq", "fcmgt", "fcmge", "fcmle", "fcmlt") and n == 3 and ops[2].type in (OP_FP, OP_IMM):         # 0.0 과 비교
            nm = {"fcmeq": "fceq", "fcmgt": "fcgt", "fcmge": "fcge", "fcmle": "fcge", "fcmlt": "fcgt"}[m]
            src = self.vhalves(ops[1], total)
            swap = m in ("fcmle", "fcmlt")
            zero = C(0, 8)

            def fz(x):
                if esz == 8:
                    c = {"fceq": "feq", "fcgt": "fgt", "fcge": "fge"}[nm]
                    return unop("neg", zext(X(c, (zero, x[0]) if swap else (x[0], zero), 1), 8))
                return vop(nm, (zero, x[0]) if swap else (x[0], zero), esz, "f")
            return self.lanewise(a, d, fz, [src], esz, total)
        if m in CMP_ZERO and n == 3 and ops[2].type == OP_IMM:
            nm, cls = CMP_ZERO[m]
            src = self.vhalves(ops[1], total)
            zero = C(0, 8)
            if m in ("cmle", "cmlt"):                                                       # 0 과 바꿔 비교: x <= 0 ⇔ 0 >= x
                return self.lanewise(a, d, lambda x: self._cmpop(nm, cls, esz, zero, x[0]), [src], esz, total)
            return self.lanewise(a, d, lambda x: self._cmpop(nm, cls, esz, x[0], zero), [src], esz, total)
        if m in INT_UN and n == 2:
            nm, cls = INT_UN[m]
            src = self.vhalves(ops[1], total)
            return self.lanewise(a, d, lambda x: self._unop(nm, cls, esz, x[0]), [src], esz, total)
        if m in FLT_UN and n == 2:
            src = self.vhalves(ops[1], total)
            return self.lanewise(a, d, lambda x: self._funop(FLT_UN[m], esz, x[0]), [src], esz, total)
        if m in CVT and n in (2, 3):
            return self._neon_cvt(a, d, ops, m, esz, total)
        if m in ("mla", "mls", "fmla", "fmls") and n == 3:
            return self._neon_mla(a, d, ops, m, esz, total)
        if m in ("shl", "sshr", "ushr", "ssra", "usra") and n == 3 and ops[2].type == OP_IMM:
            return self._neon_shift(a, d, ops, m, esz, total)
        # ---- 재배치 ----
        if m in ("dup", "ins", "mov") or m in ("zip1", "zip2", "uzp1", "uzp2", "trn1", "trn2", "ext", "rev16", "rev32", "rev64"):
            r = self._neon_perm(insn, m, ops, a, esz, total)
            if r is not None:
                return r
        # ---- 폭 바꾸기 ----
        if m in ("sshll", "ushll", "sshll2", "ushll2", "sxtl", "uxtl", "sxtl2", "uxtl2", "xtn", "xtn2", "saddl", "uaddl", "saddl2", "uaddl2",
                 "ssubl", "usubl", "ssubl2", "usubl2", "smull", "umull", "smull2", "umull2", "saddw", "uaddw", "saddw2", "uaddw2", "smlal",
                 "umlal", "smlal2", "umlal2", "smlsl", "umlsl", "smlsl2", "umlsl2", "shrn", "shrn2", "uaddlp", "saddlp"):
            r = self._neon_widen(insn, m, ops, a)
            if r is not None:
                return r
        if m in ("addp", "smaxp", "umaxp", "sminp", "uminp", "faddp", "fmaxp", "fminp", "fmaxnmp", "fminnmp") and n == 3:
            return self._neon_pairwise(a, d, ops, m, esz, total)
        raise NeonUnsupported(m)

    # ---- 헬퍼: 연산 만들기 -----------------------------------------------------------------
    def _imm_pattern(self, op, esz):
        v = op.imm
        if op.shift.type == c64.ARM64_SFT_LSL and op.shift.value:
            v <<= op.shift.value
        elif op.shift.type == c64.ARM64_SFT_MSL and op.shift.value:
            v = (v << op.shift.value) | ((1 << op.shift.value) - 1)
        v &= ir.mask(esz)
        return sum(v << (8 * esz * i) for i in range(8 // esz))

    def _cmpop(self, nm, cls, esz, x, y):
        if esz == 8:                                                                         # 원소 하나: 비교 결과를 전체 비트 마스크로
            c = {"ceq": "eq", "cgt": "sgt" if cls == "s" else "ugt", "cge": "sge" if cls == "s" else "uge"}.get(nm)
            if c is None:                                                                    # cmtst
                return unop("neg", zext(cmp("ne", binop("and", x, y, 8), C(0, 8)), 8))
            return unop("neg", zext(cmp(c, x, y), 8))
        return vop(nm, (x, y), esz, cls)

    def _unop(self, nm, cls, esz, x):
        if esz == 8:
            if nm == "neg":
                return unop("neg", x)
            if nm == "abs":
                return ite(cmp("slt", x, C(0, 8)), unop("neg", x), x)
            raise NeonUnsupported(nm)
        return vop(nm, (x,), esz, cls)

    def _funop(self, nm, esz, x):
        if esz == 8:
            if nm in ("fneg", "fabs", "fsqrt"):
                return X(nm, (x,), 8)
            mode = {"frintn": "rint", "frintm": "floor", "frintp": "ceil", "frintz": "trunc", "frinta": "round", "frintx": "rint"}.get(nm)
            if mode is None:
                raise NeonUnsupported(nm)
            return X("fround", (x,), 8, mode)
        return vop(nm, (x,), esz, "f")

    def _neon_bin(self, a, d, ops, esz, total, nm, cls, flt):
        l1 = self.vhalves(ops[1], total)
        if ops[2].vector_index >= 0:                                                         # by-element: 둘째 인자는 한 원소를 모두에 편 것
            l2 = self._bcast_elem(ops[2], esz, "f" if flt else cls)
        else:
            l2 = self.vhalves(ops[2], total)
        if esz == 8:
            ints = {"add": "add", "sub": "sub"}
            fl = {"fadd": "fadd", "fsub": "fsub", "fmul": "fmul", "fdiv": "fdiv", "fmax": "fcmax", "fmin": "fcmin", "fmaxnm": "fcmax", "fminnm": "fcmin"}
            if not flt and nm in ints:
                return self.lanewise(a, d, lambda x: binop(ints[nm], x[0], x[1], 8), [l1, l2], esz, total)
            if flt and nm in fl:
                return self.lanewise(a, d, lambda x: X(fl[nm], (x[0], x[1]), 8), [l1, l2], esz, total)
            if flt and nm == "fabd":
                return self.lanewise(a, d, lambda x: X("fabs", (X("fsub", (x[0], x[1]), 8),), 8), [l1, l2], esz, total)
            if flt and nm in ("facgt", "facge"):
                c = "fgt" if nm == "facgt" else "fge"
                return self.lanewise(a, d, lambda x: unop("neg", zext(X(c, (X("fabs", (x[0],), 8), X("fabs", (x[1],), 8)), 1), 8)), [l1, l2], esz, total)
            if not flt and nm in ("ceq", "cgt", "cge", "tst"):
                return self.lanewise(a, d, lambda x: self._cmpop(nm, cls, esz, x[0], x[1]), [l1, l2], esz, total)
            if not flt and nm in ("max", "min"):
                cc_ = {("max", "s"): "sgt", ("max", "u"): "ugt", ("min", "s"): "slt", ("min", "u"): "ult"}[(nm, cls)]
                return self.lanewise(a, d, lambda x: ite(cmp(cc_, x[0], x[1]), x[0], x[1]), [l1, l2], esz, total)
            if nm == "fceq" or nm == "fcgt" or nm == "fcge":
                c = {"fceq": "feq", "fcgt": "fgt", "fcge": "fge"}[nm]
                return self.lanewise(a, d, lambda x: unop("neg", zext(X(c, (x[0], x[1]), 1), 8)), [l1, l2], esz, total)
            raise NeonUnsupported(nm)
        if esz == 4 and flt:
            return self.lanewise(a, d, lambda x: vop(nm, (x[0], x[1]), 4, "f"), [l1, l2], esz, total)
        if flt:
            raise NeonUnsupported(nm)
        return self.lanewise(a, d, lambda x: vop(nm, (x[0], x[1]), esz, cls), [l1, l2], esz, total)

    def _neon_mla(self, a, d, ops, m, esz, total):
        flt = m.startswith("f")
        acc = self.vhalves(d, total)
        l1 = self.vhalves(ops[1], total)
        l2 = self._bcast_elem(ops[2], esz, "f" if flt else "s") if ops[2].vector_index >= 0 else self.vhalves(ops[2], total)
        if esz == 8:
            if not flt:
                raise NeonUnsupported(m)

            def fn(x):
                p = x[1] if m == "fmla" else X("fneg", (x[1],), 8)
                return X("ffma", (p, x[2], x[0]), 8)
            return self.lanewise(a, d, fn, [acc, l1, l2], esz, total)
        return self.lanewise(a, d, lambda x: vop(m, (x[0], x[1], x[2]), esz, "f" if flt else "s"), [acc, l1, l2], esz, total)

    def _neon_cvt(self, a, d, ops, m, esz, total):
        src = self.vhalves(ops[1], total)
        if len(ops) == 3 and ops[2].type == OP_IMM:                                          # 고정 소수점 변환은 아직
            raise NeonUnsupported(m)
        if esz == 8:
            mk = {"scvtf": lambda x: X("i2f", (x,), 8, "s"), "ucvtf": lambda x: X("i2f", (x,), 8, "u"),
                  "fcvtzs": lambda x: X("f2i", (x,), 8, "s"), "fcvtzu": lambda x: X("f2i", (x,), 8, "u")}[m]
            return self.lanewise(a, d, lambda x: mk(x[0]), [src], esz, total)
        if esz != 4:
            raise NeonUnsupported(m)
        return self.lanewise(a, d, lambda x: vop(CVT[m], (x[0],), 4, "f"), [src], esz, total)

    def _neon_shift(self, a, d, ops, m, esz, total):
        n = ops[2].imm
        src = self.vhalves(ops[1], total)
        sgn = "u" if m.startswith("u") else "s"
        acc = m in ("ssra", "usra")
        if esz == 8:
            if m == "shl":
                fn = lambda x: binop("shl", x[0], C(n, 1), 8)                               # noqa: E731
            else:
                sop = "shr" if sgn == "u" else "sar"
                if n >= 64:                                                                  # 오른쪽으로 64칸: 부호 있으면 부호 비트로 가득, 없으면 0
                    n = 63 if sgn == "s" else 64
                if acc:
                    fn = lambda x: binop("add", x[1], binop(sop, x[0], C(n, 1), 8), 8)      # noqa: E731
                else:
                    fn = lambda x: binop(sop, x[0], C(n, 1), 8)                              # noqa: E731
            srcs = [src] + ([self.vhalves(d, total)] if acc else [])
            return self.lanewise(a, d, fn, srcs, esz, total)
        if m == "shl":
            return self.lanewise(a, d, lambda x: vop("shl_n", (x[0], C(n, 1)), esz, "s"), [src], esz, total)
        if acc:
            return self.lanewise(a, d, lambda x: vop("sra_n", (x[1], x[0], C(n, 1)), esz, sgn), [src, self.vhalves(d, total)], esz, total)
        return self.lanewise(a, d, lambda x: vop("shr_n", (x[0], C(n, 1)), esz, sgn), [src], esz, total)

    def _neon_imm(self, insn, m, ops, a, esz, total):
        d, s = ops
        if s.type == OP_FP:                                                                   # fmov v.2d/.4s, #fp
            import struct
            bits = struct.unpack("<Q", struct.pack("<d", s.fp))[0] if esz == 8 else struct.unpack("<I", struct.pack("<f", s.fp))[0]
            v = bits if esz == 8 else (bits | bits << 32)
            return self.vout(a, d, C(v, 8), C(v, 8), total)
        if s.type != OP_IMM:
            raise NeonUnsupported(m)
        if esz == 8 and m in ("movi", "mvni"):                                                # movi v.2d, #imm64(바이트 마스크로 펼친 값)
            v = s.imm & ir.mask(8)
        else:
            v = self._imm_pattern(s, esz)
        if m == "mvni":
            v = ~v & ir.mask(8)
        return self.vout(a, d, C(v, 8), C(v, 8), total)

    # ---- 재배치 ----------------------------------------------------------------------------
    def _neon_perm(self, insn, m, ops, a, esz, total):
        n = len(ops)
        d = ops[0]
        per = 8 // esz if esz else 1
        nl = total // esz if esz else 1                      # 전체 원소 수
        cls = "s"
        if m == "dup" and n == 2:
            s = ops[1]
            if s.type == OP_REG and not self.is_fpop(s):                                       # dup v.4s, w0
                v = self.read_reg(s.reg)
                sc = trunc(v, esz) if v.sz > esz else v
                h = vdup(sc, esz, cls) if esz < 8 else sc
                return self.vout(a, d, h, h, total)
            if self.is_fpop(s) and s.vector_index >= 0:                                        # dup v.4s, v1.s[2]
                half, i = self.velem(s, esz)
                sc = vlane(half, i, esz, cls)
                h = vdup(sc, esz, cls) if esz < 8 else sc
                return self.vout(a, d, h, h, total)
            return None
        if m in ("ins", "mov") and n == 2 and self.is_fpop(d) and d.vector_index >= 0 and self.vshape(d) is None:      # ins v0.s[1], w1 / v1.s[2]
            k, nn = self.fp_info(d.reg)
            esz2 = self._elem_size(d)
            if esz2 is None:
                return None
            half, i = self.velem(d, esz2)
            s = ops[1]
            if s.type == OP_REG and not self.is_fpop(s):
                v = self.read_reg(s.reg)
                sc = trunc(v, esz2) if v.sz > esz2 else v
            elif self.is_fpop(s) and s.vector_index >= 0:
                sh2, j = self.velem(s, esz2)
                sc = vlane(sh2, j, esz2, "s")
            else:
                return None
            newv = vsetlane(half, sc, i, esz2, "s") if esz2 < 8 else sc
            return [S("asg", a, half, newv)]
        if m in ("zip1", "zip2", "uzp1", "uzp2", "trn1", "trn2") and n == 3 and self.vshape(ops[2]):
            return self._zip_like(a, d, ops, m, esz, total)
        if m == "ext" and n == 4 and ops[3].type == OP_IMM:
            return self._ext(a, d, ops, esz, total)
        if m in ("rev16", "rev32", "rev64") and n == 2:
            blk = {"rev16": 2, "rev32": 4, "rev64": 8}[m]
            if blk <= esz:
                return None
            per_blk = blk // esz
            src = self.vhalves(ops[1], total)
            idx = tuple(j - j % per_blk + (per_blk - 1 - j % per_blk) for j in range(per))
            return self.lanewise(a, d, lambda x: vshuf(x[0], x[0], idx, esz, "s"), [src], esz, total)
        return None

    def _elem_size(self, op):
        """원소 지정 피연산자 `v0.s[1]` 의 원소 폭."""
        e = getattr(self, "_esz_by_op", {}).get(id(op))
        if e is not None:
            return e
        return {c64.ARM64_VAS_1B: 1, c64.ARM64_VAS_1H: 2, c64.ARM64_VAS_1S: 4, c64.ARM64_VAS_1D: 8}.get(op.vas)

    def _zip_like(self, a, d, ops, m, esz, total):
        """zip/uzp/trn: 입력 두 벡터의 원소를 섞는다. 출력 한 조각이 입력 두 조각에서만 오도록 인덱스를 잡는다."""
        n = total // esz
        per = 8 // esz
        x, y = ops[1], ops[2]
        xl, yl = self.vhalves(x, total), self.vhalves(y, total)
        # 전체 원소 번호: x 는 0..n-1, y 는 n..2n-1
        if m.startswith("zip"):
            base = 0 if m == "zip1" else n // 2
            full = []
            for j in range(n // 2):
                full += [base + j, n + base + j]
        elif m.startswith("uzp"):
            off = 0 if m == "uzp1" else 1
            full = [2 * j + off for j in range(n)]
        else:
            off = 0 if m == "trn1" else 1
            full = []
            for j in range(n // 2):
                full += [2 * j + off, n + 2 * j + off]
        outs = []
        for h in range(2 if total == 16 else 1):
            idxs = full[h * per:(h + 1) * per]
            # 각 인덱스가 어느 입력 조각에서 오는지
            srcs = []
            for i in idxs:
                reg = xl if i < n else yl
                j = i % n
                srcs.append((reg[j // per] if reg[j // per] is not None else C(0, 8), j % per, (0 if i < n else 1, j // per)))
            uniq = []
            for _, _, key in srcs:
                if key not in uniq:
                    uniq.append(key)
            if len(uniq) > 2:
                raise NeonUnsupported(m)
            while len(uniq) < 2:
                uniq.append(uniq[0])
            piece = {k: (xl if k[0] == 0 else yl)[k[1]] for k in uniq}
            sel = []
            for (_, lane, key) in srcs:
                sel.append(uniq.index(key) * per + lane)
            outs.append(vshuf(piece[uniq[0]], piece[uniq[1]], sel, esz, "s"))
        return self.vout(a, d, outs[0], outs[1] if len(outs) > 1 else None, total)

    def _ext(self, a, d, ops, esz, total):
        k = ops[3].imm                                                                         # 바이트 단위(8b/16b 배열)
        xl, yl = self.vhalves(ops[1], total), self.vhalves(ops[2], total)
        flat = []                                                                              # 이어 붙인 조각들 [x.lo, x.hi, y.lo, y.hi]
        pieces = list(xl) + list(yl)
        pieces = [p for p in pieces if p is not None]
        per = 8
        outs = []
        for h in range(2 if total == 16 else 1):
            idx = [k + h * per + j for j in range(per)]                                       # 바이트 번호(연결된 두 벡터 안)
            srcs = []
            for i in idx:
                srcs.append((i // per, i % per))
            uniq = []
            for pc, _ in srcs:
                if pc not in uniq:
                    uniq.append(pc)
            if len(uniq) > 2:
                raise NeonUnsupported("ext")
            while len(uniq) < 2:
                uniq.append(uniq[0])
            sel = [uniq.index(pc) * per + off for pc, off in srcs]
            outs.append(vshuf(pieces[uniq[0]], pieces[uniq[1]], sel, 1, "u"))
        return self.vout(a, d, outs[0], outs[1] if len(outs) > 1 else None, total)

    # ---- 폭 바꾸기 --------------------------------------------------------------------------
    def _pack_halves(self, lanes, esz):
        """원소 스칼라 목록(폭 esz) → 벡터 조각 (하위, 상위 또는 None). 원소가 조각 하나를 채우는 폭(8)이면 각각이 조각이다."""
        per = 8 // esz
        if esz == 8:
            return lanes[0], (lanes[1] if len(lanes) > 1 else None)
        halves = [vpack(lanes[i:i + per], esz, "s") for i in range(0, len(lanes), per)]
        return halves[0], (halves[1] if len(halves) > 1 else None)

    def _src_lanes(self, op, esz, total, upper):
        """벡터 피연산자의 (사용할) 원소 스칼라들: 128비트 배열이면 upper 에 따라 상위/하위 조각만, 64비트 배열이면 그 조각."""
        lo, hi = self.vhalves(op, total)
        piece = hi if (upper and hi is not None) else lo
        return [vlane(piece, j, esz, "s") for j in range(8 // esz)]

    def _neon_widen(self, insn, m, ops, a):
        d = ops[0]
        n = len(ops)
        upper = m.endswith("2")
        base = m[:-1] if upper else m
        if base in ("xtn", "shrn"):
            ds = self.vshape(d)
            ss = self.vshape(ops[1])
            if ds is None or ss is None:
                raise NeonUnsupported(m)
            esz_d, _ = ds
            esz_s, total_s = ss
            lo, hi = self.vhalves(ops[1], total_s)
            lanes = []
            for piece in (lo, hi):
                if piece is None:
                    continue
                for j in range(8 // esz_s):
                    v = vlane(piece, j, esz_s, "s")
                    if base == "shrn":
                        v = binop("shr", v, C(ops[2].imm, 1), esz_s)
                    lanes.append(trunc(v, esz_d))
            half = self._pack_halves(lanes, esz_d)[0]
            k, nn = self.fp_info(d.reg)
            if upper:
                return [S("asg", a, loc(f"v{nn}h", 8), half)]
            return [S("asg", a, loc(f"v{nn}", 8), half), S("asg", a, loc(f"v{nn}h", 8), C(0, 8))]
        if base in ("saddlp", "uaddlp"):
            raise NeonUnsupported(m)
        # 넓히는 연산: 목적지 원소 폭 = 원본의 두 배
        ds = self.vshape(d)
        if ds is None:
            raise NeonUnsupported(m)
        esz_d, total_d = ds
        esz_s = esz_d // 2
        sg = "u" if base.startswith("u") else "s"
        ext = sext if sg == "s" else zext

        def wide(op, upper_):
            """좁은 피연산자의 넓힌 원소 스칼라들."""
            if op.vector_index >= 0:                                                         # by-element: 한 원소를 모두에
                half, i = self.velem(op, esz_s)
                one = ext(vlane(half, i, esz_s, "s"), esz_d)
                return [one] * (8 // esz_s)
            total = VAS[op.vas][1]
            return [ext(l, esz_d) for l in self._src_lanes(op, esz_s, total, upper_)]
        if base in ("sshll", "ushll", "sxtl", "uxtl"):
            sh = ops[2].imm if n > 2 else 0
            lanes = [binop("shl", w, C(sh, 1), esz_d) if sh else w for w in wide(ops[1], upper)]
            lo, hi = self._pack_halves(lanes, esz_d)
            return self.vout(a, d, lo, hi, 16)
        if base in ("saddl", "uaddl", "ssubl", "usubl", "smull", "umull"):
            wa, wb = wide(ops[1], upper), wide(ops[2], upper)
            f = {"saddl": "add", "uaddl": "add", "ssubl": "sub", "usubl": "sub", "smull": "mul", "umull": "mul"}[base]
            lanes = [binop(f, x, y, esz_d) for x, y in zip(wa, wb)]
            lo, hi = self._pack_halves(lanes, esz_d)
            return self.vout(a, d, lo, hi, 16)
        if base in ("saddw", "uaddw"):
            wb = wide(ops[2], upper)
            lanes_hi = self.vhalves(ops[1], 16)
            lo, hi = self._pack_halves(wb, esz_d)
            return self.vout(a, d, self._vadd(lanes_hi[0], lo, esz_d), self._vadd(lanes_hi[1], hi, esz_d), 16)
        if base in ("smlal", "umlal", "smlsl", "umlsl"):
            wa, wb = wide(ops[1], upper), wide(ops[2], upper)
            prod = [binop("mul", x, y, esz_d) for x, y in zip(wa, wb)]
            plo, phi = self._pack_halves(prod, esz_d)
            acc = self.vhalves(d, 16)
            f = self._vadd if base.endswith("lal") else self._vsub
            return self.vout(a, d, f(acc[0], plo, esz_d), f(acc[1], phi, esz_d), 16)
        raise NeonUnsupported(m)

    def _vadd(self, x, y, esz):
        return binop("add", x, y, 8) if esz == 8 else vop("add", (x, y), esz, "s")

    def _vsub(self, x, y, esz):
        return binop("sub", x, y, 8) if esz == 8 else vop("sub", (x, y), esz, "s")

    def _neon_pairwise(self, a, d, ops, m, esz, total):
        flt = m.startswith("f")
        nm = {"addp": "padd", "smaxp": "pmax", "umaxp": "pmax", "sminp": "pmin", "uminp": "pmin", "faddp": "fpadd",
              "fmaxp": "fpmax", "fminp": "fpmin", "fmaxnmp": "fpmax", "fminnmp": "fpmin"}[m]
        cls = "f" if flt else ("u" if m.startswith("u") else "s")
        x, y = self.vhalves(ops[1], total), self.vhalves(ops[2], total)
        if esz == 8:
            op2 = {"addp": lambda p, q: binop("add", p, q, 8), "faddp": lambda p, q: X("fadd", (p, q), 8),
                   "fmaxp": lambda p, q: X("fcmax", (p, q), 8), "fminp": lambda p, q: X("fcmin", (p, q), 8),
                   "fmaxnmp": lambda p, q: X("fcmax", (p, q), 8), "fminnmp": lambda p, q: X("fcmin", (p, q), 8)}.get(m)
            if op2 is None:
                raise NeonUnsupported(m)
            return self.vout(a, d, op2(x[0], x[1]), op2(y[0], y[1]) if total == 16 else None, total)
        if total == 16:                                                                      # 128비트: 하위 결과는 첫 입력의 두 조각, 상위 결과는 둘째 입력의 두 조각
            return self.vout(a, d, vop(nm, (x[0], x[1]), esz, cls), vop(nm, (y[0], y[1]), esz, cls), 16)
        return self.vout(a, d, vop(nm, (x[0], y[0]), esz, cls), None, 8)

    # ---- 스칼라 결과(벡터 → GPR/스칼라 FP 레지스터) -------------------------------------------
    def _neon_scalar_dest(self, insn, m, ops, a):
        n = len(ops)
        d = ops[0]
        s = ops[1] if n > 1 else None
        if m in ("umov", "smov", "mov") and n == 2 and s is not None and self.is_fpop(s) and s.vector_index >= 0 and not self.is_fpop(d):
            esz = self._elem_size(s)
            if esz is None:
                return None
            half, i = self.velem(s, esz)
            sc = vlane(half, i, esz, "s")
            dsz = self.reg_sz(d.reg)
            if m == "smov":
                v = sext(sc, dsz) if sc.sz < dsz else sc
            else:
                v = zext(sc, dsz) if sc.sz < dsz else sc
            return [self.write_reg(d.reg, v, a)]
        if m in ("mov", "dup") and n == 2 and s is not None and self.is_fpop(d) and self.is_fpop(s) and s.vector_index >= 0 and d.vector_index < 0:
            esz = self._elem_size(s)                                                       # mov s3, v1.s[1]: 원소를 스칼라 FP 레지스터로
            if esz is None:
                return None
            half, i = self.velem(s, esz)
            return self.fwrite(a, d, vlane(half, i, esz, "s"))
        if m in ("addv", "smaxv", "sminv", "umaxv", "uminv", "saddlv", "uaddlv", "fmaxv", "fminv", "fmaxnmv", "fminnmv") and n == 2 and self.vshape(s):
            return self._reduce(a, d, s, m)
        if m in ("addp", "faddp", "fmaxp", "fminp", "fmaxnmp", "fminnmp") and n == 2 and self.vshape(s):
            return self._scalar_pairwise(a, d, s, m)
        raise NeonUnsupported(m)

    def _reduce(self, a, d, s, m):
        esz, total = self.vshape(s)
        lo, hi = self.vhalves(s, total)
        flt = m.startswith("f")
        dk = self.fp_info(d.reg)[0]
        if esz == 8:
            raise NeonUnsupported(m)
        if m in ("addv",):
            nm, cls = "add", "s"
        elif m in ("smaxv", "umaxv"):
            nm, cls = "max", m[0]
        elif m in ("sminv", "uminv"):
            nm, cls = "min", m[0]
        elif flt:
            nm, cls = {"fmaxv": "fmax", "fminv": "fmin", "fmaxnmv": "fmaxnm", "fminnmv": "fminnm"}[m], "f"
        elif m in ("saddlv", "uaddlv"):                                                          # 원소를 두 배 폭으로 넓혀 모두 더한다
            wide = 2 * esz
            ext = sext if m[0] == "s" else zext
            acc = None
            for piece in (lo, hi):
                if piece is None:
                    continue
                for j in range(8 // esz):
                    t = ext(vlane(piece, j, esz, "s"), wide)
                    acc = t if acc is None else binop("add", acc, t, wide)
            return self.fwrite(a, d, acc)
        else:
            raise NeonUnsupported(m)
        comb = lo if hi is None else vop(nm, (lo, hi), esz, cls)
        r = X("vredf" if flt else "vred", (comb,), esz, ({"add": "addv", "max": "maxv", "min": "minv", "fmax": "maxv", "fmin": "minv",
                                                          "fmaxnm": "maxnmv", "fminnm": "minnmv"}[nm], esz, cls))
        return self.fwrite(a, d, r)

    def _scalar_pairwise(self, a, d, s, m):
        esz, total = self.vshape(s)
        lo, hi = self.vhalves(s, total)
        if esz != 8:
            raise NeonUnsupported(m)
        op = {"addp": lambda x, y: binop("add", x, y, 8), "faddp": lambda x, y: X("fadd", (x, y), 8)}.get(m)
        if op is None:
            raise NeonUnsupported(m)
        return self.fwrite(a, d, op(lo, hi))


    # ---- 구조 로드·스토어(ld1–ld4·st1–st4·ldNr) ------------------------------------------------
    def _neon_ldst(self, insn, m, ops, a):
        regs = [o for o in ops if o.type == OP_REG and self.is_fpop(o)]
        mi = next(i for i, o in enumerate(ops) if o.type == OP_MEM)
        mop = ops[mi]
        post = ops[mi + 1] if mi + 1 < len(ops) else None
        load = m.startswith("ld")
        rep = m.endswith("r")
        nreg = len(regs)
        nn = int(m[2])
        ea = self.mem_addr(mop)
        out = []
        if nreg == 0 or nn != nreg and not (m == "ld1" or m == "st1"):
            raise NeonUnsupported(m)
        r0 = regs[0]
        lane_idx = max(r.vector_index for r in regs)
        lane_form = lane_idx >= 0
        xfer = 0                                                                               # 전송 바이트 수(후위 증가의 기본 증분)
        if lane_form:                                                                          # ld1 {v0.s}[2], [x0] / st2 {v0.s, v1.s}[1], [x0]
            import re
            mt = re.search(r"\{v\d+\.([bhsd])", insn.op_str)
            if mt is None:
                raise NeonUnsupported(m)
            esz = {"b": 1, "h": 2, "s": 4, "d": 8}[mt.group(1)]
            xfer = nreg * esz
            for k, r in enumerate(regs):
                kk, rn = self.fp_info(r.reg)
                per_ = 8 // esz
                half, i = loc(f"v{rn}" if lane_idx < per_ else f"v{rn}h", 8), lane_idx % per_
                addr = ea if k == 0 else binop("add", ea, C(k * esz, 8), 8)
                if load:
                    v = self.load_mem(addr, esz)
                    out.append(S("asg", a, half, vsetlane(half, v, i, esz, "s") if esz < 8 else v))
                else:
                    out.append(S("asg", a, mem(addr, esz), vlane(half, i, esz, "s")))
        elif rep:                                                                              # ld1r {v0.4s}, [x0]
            esz, total = VAS[r0.vas]
            xfer = nreg * esz
            for k, r in enumerate(regs):
                addr = ea if k == 0 else binop("add", ea, C(k * esz, 8), 8)
                v = self.load_mem(addr, esz)
                h = vdup(v, esz, "s") if esz < 8 else v
                out += self.vout(a, r, h, h, total)
        else:
            esz, total = VAS[r0.vas]
            per = 8 // esz
            L = total // esz
            nchunk = (nreg if nn == 1 else nn) * total // 8
            xfer = nchunk * 8
            if load:
                raw = [self.load_mem(binop("add", ea, C(8 * c, 8), 8) if c else ea, 8) for c in range(nchunk)]
                if nn == 1:                                                                    # ld1 여러 레지스터: 연속된 메모리
                    for k, r in enumerate(regs):
                        lo = raw[k * (total // 8)]
                        hi = raw[k * (total // 8) + 1] if total == 16 else None
                        out += self.vout(a, r, lo, hi, total)
                else:
                    # 모든 읽기가 끝난 뒤에 레지스터에 쓴다(주소·원본 조각이 레지스터를 읽을 수 있으므로 임시 값에 먼저)
                    tmps = []
                    for c in range(nchunk):
                        t = self.tmp(8)
                        out.append(S("asg", a, t, raw[c]))
                        tmps.append(t)
                    for r_i, r in enumerate(regs):
                        halves = []
                        for h in range(2 if total == 16 else 1):
                            srcs = []
                            for i in range(h * per, (h + 1) * per):
                                j = i * nn + r_i
                                srcs.append(((j * esz) // 8, ((j * esz) % 8) // esz))
                            halves.append(self._gather([tmps[c] for c in range(nchunk)], srcs, esz))
                        out += self.vout(a, r, halves[0], halves[1] if len(halves) > 1 else None, total)
            else:
                pieces = {}
                for r_i, r in enumerate(regs):
                    lo, hi = self.vhalves(r, total)
                    pieces[(r_i, 0)] = lo
                    if hi is not None:
                        pieces[(r_i, 1)] = hi
                if nn == 1:
                    for k, r in enumerate(regs):
                        out.append(S("asg", a, mem(binop("add", ea, C(k * total, 8), 8) if k else ea, 8), pieces[(k, 0)]))
                        if total == 16:
                            out.append(S("asg", a, mem(binop("add", ea, C(k * total + 8, 8), 8), 8), pieces[(k, 1)]))
                else:
                    for c in range(nchunk):
                        srcs = []
                        for l in range(per):
                            j = c * per + l
                            r_i, i = j % nn, j // nn
                            srcs.append(((r_i, i // per), i % per))
                        val = self._gather_keyed(pieces, srcs, esz)
                        out.append(S("asg", a, mem(binop("add", ea, C(8 * c, 8), 8) if c else ea, 8), val))
        if insn.writeback:                                                                     # 후위 증가(Capstone 은 st 계열의 즉값 증분 피연산자를 알려 주지 않는다 → 전송 크기)
            bname = self.reg_info(mop.mem.base)[0]
            if post is None:
                delta = C(xfer, 8)
            else:
                delta = C(post.imm, 8) if post.type == OP_IMM else self.read_reg(post.reg)
            out.append(S("asg", a, loc(bname, 8), binop("add", loc(bname, 8), delta, 8)))
        return out

    def _gather(self, chunks, srcs, esz):
        """chunks[c] 의 l 번째 원소들을 순서대로 모아 벡터 한 조각을 만든다. 입력 조각이 둘 이하면 shufflevector."""
        per = 8 // esz
        uniq = []
        for c, _ in srcs:
            if c not in uniq:
                uniq.append(c)
        if esz == 8:
            return chunks[srcs[0][0]]
        if len(uniq) <= 2:
            second = uniq[1] if len(uniq) > 1 else uniq[0]
            return vshuf(chunks[uniq[0]], chunks[second], [uniq.index(c) * per + l for c, l in srcs], esz, "s")
        return vpack([vlane(chunks[c], l, esz, "s") for c, l in srcs], esz, "s")

    def _gather_keyed(self, pieces, srcs, esz):
        per = 8 // esz
        keys = []
        for k, _ in srcs:
            if k not in keys:
                keys.append(k)
        if esz == 8:
            return pieces[srcs[0][0]]
        if len(keys) <= 2:
            second = keys[1] if len(keys) > 1 else keys[0]
            return vshuf(pieces[keys[0]], pieces[second], [keys.index(k) * per + l for k, l in srcs], esz, "s")
        return vpack([vlane(pieces[k], l, esz, "s") for k, l in srcs], esz, "s")
