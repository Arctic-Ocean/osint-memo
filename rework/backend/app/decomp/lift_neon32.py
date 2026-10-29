"""ARM32 Advanced SIMD(NEON) 벡터 명령 → IR.

q 레지스터는 d 레지스터 두 개(q(j) = d(2j), d(2j+1))이고 d 는 VFP 와 같은 64비트 위치 `d(k)` 다(lift_arm_vfp.py). 그래서 d 한 개가 벡터 한 조각이고,
q 는 (하위 d, 상위 d) 두 조각이다. 원소별 연산은 조각마다 한 번씩 적용하고(ir.vop), 재배치는 vshuf·vdup·vpack·vsetlane, 원소 꺼내기는 vlane 이다
(ARM64 와 같은 IR, 연산 만드는 법은 neon_core.py 를 함께 쓴다).
"""
from __future__ import annotations

import re

from capstone.arm import ARM_OP_REG as OP_REG, ARM_OP_IMM as OP_IMM, ARM_OP_MEM as OP_MEM, ARM_OP_FP as OP_FP

from . import ir
from .ir import C, X, S, loc, mem, binop, unop, zext, sext, trunc, cmp, ite, vop, vlane, vpack, vdup, vshuf, vsetlane, pair
from . import neon_core as nc
from .neon_core import NeonUnsupported

NOT_NEON = object()

# 접미사 → (원소 폭, 부호/실수)
def parse_dt(t: str):
    m = re.fullmatch(r"([iusfp]?)(8|16|32|64)", t)
    if not m:
        return None
    k, bits = m.group(1), int(m.group(2))
    cls = {"s": "s", "u": "u", "i": "s", "f": "f", "p": "u", "": "u"}[k]
    return bits // 8, cls


# NEON 에만 있는 기본 이름
NEON_ONLY = {"vld1", "vld2", "vld3", "vld4", "vst1", "vst2", "vst3", "vst4", "vdup", "vext", "vzip", "vuzp", "vtrn", "vrev16", "vrev32", "vrev64",
             "vmovl", "vmovn", "vtbl", "vtbx", "vbsl", "vbit", "vbif", "vand", "vorr", "veor", "vbic", "vorn", "vmvn", "vcnt", "vclz", "vcls",
             "vpadd", "vpmax", "vpmin", "vpaddl", "vpadal", "vabd", "vqadd", "vqsub", "vhadd", "vrhadd", "vhsub", "vrshr", "vshr", "vshl", "vsra",
             "vsli", "vsri", "vshrn", "vqmovn", "vqmovun", "vaddl", "vaddw", "vsubl", "vsubw", "vmull", "vmlal", "vmlsl", "vaddhn", "vtst",
             "vceq", "vcge", "vcgt", "vcle", "vclt", "vacge", "vacgt", "vmax", "vmin", "vrecpe", "vrsqrte", "vrecps", "vrsqrts", "vqabs", "vqneg",
             "vabdl", "vabal", "vaba", "vqdmulh", "vrshl", "vqshl", "vcreate", "vswp", "vmaxnm", "vminnm", "vrintn", "vrintz", "vrintm", "vrintp", "vrinta", "vrintx"}
# VFP 와 이름을 나누는 것: 레지스터가 s 이거나 자료형이 f64 이면 VFP
SHARED = {"vadd", "vsub", "vmul", "vmla", "vmls", "vfma", "vfms", "vneg", "vabs", "vmov", "vcvt", "vsqrt"}
INT_BIN = {"vadd": "add", "vsub": "sub", "vmul": "mul", "vmax": "max", "vmin": "min", "vabd": "abd", "vqadd": "qadd", "vqsub": "qsub",
           "vhadd": "hadd", "vrhadd": "rhadd", "vshl": "shl", "vceq": "ceq", "vcgt": "cgt", "vcge": "cge", "vtst": "tst"}
FLT_BIN = {"vadd": "fadd", "vsub": "fsub", "vmul": "fmul", "vmax": "fmax", "vmin": "fmin", "vabd": "fabd", "vceq": "fceq", "vcgt": "fcgt", "vcge": "fcge",
           "vacgt": "facgt", "vacge": "facge", "vmaxnm": "fmaxnm", "vminnm": "fminnm"}


class A32Neon:
    """A32Vfp 에 섞어 쓴다(vread·vwrite·vinfo·is_vreg·read_reg·write_reg·mem_addr·load_mem·tmp·mode_of 사용)."""

    # ---- 피연산자 ------------------------------------------------------------------------
    def nhalves(self, op):
        """벡터 레지스터 피연산자의 조각 목록: d → [d], q → [d(2j), d(2j+1)]."""
        kind, idx = self.vinfo(op.reg)
        if kind == "q":
            return [self.vread("d", 2 * idx), self.vread("d", 2 * idx + 1)]
        if kind == "d":
            return [self.vread("d", idx)]
        raise NeonUnsupported("reg")

    def nsize(self, op) -> int:
        return 16 if self.vinfo(op.reg)[0] == "q" else 8

    def nout(self, a, d, pieces):
        kind, idx = self.vinfo(d.reg)
        out = []
        base = 2 * idx if kind == "q" else idx
        for i, p in enumerate(pieces):
            out += self.vwrite(a, "d", base + i, p)
        return out

    def nelem(self, op, esz):
        """원소 지정 피연산자 `d2[3]` 의 (조각 번호 안, 조각 안 번호): d 하나가 8바이트라 조각은 그 d 다."""
        kind, idx = self.vinfo(op.reg)
        return self.vread("d", idx), op.vector_index

    # ---- 진입점 --------------------------------------------------------------------------
    def neon32_lift(self, insn, head, dts, ops, a):
        """NEON 명령이면 문장 목록(해석 못하면 None), VFP 명령이면 NOT_NEON."""
        if not self._is_neon(head, dts, ops):
            return NOT_NEON
        try:
            return self._neon32(insn, head, dts, ops, a)
        except (NeonUnsupported, TypeError, AttributeError, KeyError, IndexError, ValueError, StopIteration):      # 해석 못 하는 형식은 그 명령만 미해석으로 둔다
            return None

    def _is_neon(self, head, dts, ops) -> bool:
        vregs = [o for o in ops if o.type == OP_REG and self.vinfo(o.reg) is not None]
        if any(self.vinfo(o.reg)[0] == "q" for o in vregs):
            return True
        if head in NEON_ONLY:
            return True
        if head in SHARED:
            if not vregs:
                return False
            if head == "vmov":
                # NEON: q 레지스터, vmov.i8/i16/i32/i64 즉값, vmov.f32 d0, #imm, 레인 vmov.8/.16/.u8/.s16, d/s 복사는 VFP
                lane = any(o.type == OP_REG and o.vector_index >= 0 for o in ops)
                if lane:
                    return bool(dts) and dts[0] not in ("32", "")
                if ops and ops[-1].type in (OP_IMM, OP_FP) and ops[0].type == OP_REG and self.vinfo(ops[0].reg) is not None:
                    k0 = self.vinfo(ops[0].reg)[0]
                    f0 = dts[0] if dts else ""
                    return k0 != "s" and f0 != "f64"
                return False
            first = dts[0] if dts else ""
            if first == "f64":
                return False
            if head == "vcvt":
                return any(self.vinfo(o.reg)[0] == "d" for o in vregs) and all(self.vinfo(o.reg)[0] != "s" for o in vregs)
            if any(self.vinfo(o.reg)[0] == "s" for o in vregs):
                return False
            return True
        return False

    # ---- 본체 ---------------------------------------------------------------------------
    def _neon32(self, insn, m, dts, ops, a):
        n = len(ops)
        d = ops[0]
        dt = parse_dt(dts[0]) if dts else None
        if m in ("vld1", "vld2", "vld3", "vld4", "vst1", "vst2", "vst3", "vst4"):
            return self._n32_ldst(insn, m, dts, ops, a)
        if m == "vmov" and n == 2 and ops[1].type in (OP_IMM, OP_FP):                              # vmov.iN q0, #imm / vmov.f32 d0, #1.0
            return self._n32_movi(insn, m, dts, ops, a, invert=False)
        if m == "vmvn" and n == 2 and ops[1].type == OP_IMM:
            return self._n32_movi(insn, m, dts, ops, a, invert=True)
        if m in ("vorr", "vbic") and n == 2 and ops[1].type == OP_IMM:                             # vorr.i32 q0, #imm
            esz = dt[0]
            pat = nc.imm_pattern(ops[1].imm, esz) if esz < 8 else ops[1].imm
            pieces = self.nhalves(d)
            if m == "vorr":
                return self.nout(a, d, [binop("or", p, C(pat, 8), 8) for p in pieces])
            return self.nout(a, d, [binop("and", p, C(~pat & ir.mask(8), 8), 8) for p in pieces])
        if m == "vmov" and n == 2 and self.is_vreg(ops[1]) and ops[1].vector_index < 0 and d.vector_index < 0:     # vmov q0, q1 / d0, d1
            if self.nsize(d) != self.nsize(ops[1]):
                raise NeonUnsupported(m)
            return self.nout(a, d, self.nhalves(ops[1]))
        if m == "vmov" and n == 2 and d.type == OP_REG and not self.is_vreg(d) and self.is_vreg(ops[1]) and ops[1].vector_index >= 0:     # vmov.u8 r0, d0[3]
            return self._n32_lane_to_core(insn, dts, ops, a)
        if m == "vmov" and n == 2 and self.is_vreg(d) and d.vector_index >= 0:                                                     # vmov.8 d0[3], r0
            return self._n32_core_to_lane(insn, dts, ops, a)
        if m == "vmov" and n == 3 and not self.is_vreg(ops[0]):                                         # vmov r0, r1, d0 은 VFP 쪽
            raise NeonUnsupported(m)
        if m in ("vand", "vorr", "veor", "vbic", "vorn") and n == 3:
            x, y = self.nhalves(ops[1]), self.nhalves(ops[2])
            op = {"vand": "and", "vorr": "or", "veor": "xor", "vbic": "and", "vorn": "or"}[m]
            return self.nout(a, d, [binop(op, p, unop("not", q) if m in ("vbic", "vorn") else q, 8) for p, q in zip(x, y)])
        if m in ("vorr", "vand", "veor") and n == 2:                                                    # 두 피연산자 형식(목적지가 첫 입력)
            x, y = self.nhalves(d), self.nhalves(ops[1])
            op = {"vand": "and", "vorr": "or", "veor": "xor"}[m]
            return self.nout(a, d, [binop(op, p, q, 8) for p, q in zip(x, y)])
        if m == "vmvn" and n == 2:
            return self.nout(a, d, [unop("not", p) for p in self.nhalves(ops[1])])
        if m in ("vbsl", "vbit", "vbif") and n == 3:
            dd, nn, mm = self.nhalves(d), self.nhalves(ops[1]), self.nhalves(ops[2])

            def sel(mask_, p, q):
                return binop("or", binop("and", p, mask_, 8), binop("and", q, unop("not", mask_), 8), 8)
            if m == "vbsl":
                return self.nout(a, d, [sel(x, y, z) for x, y, z in zip(dd, nn, mm)])
            if m == "vbit":
                return self.nout(a, d, [sel(z, y, x) for x, y, z in zip(dd, nn, mm)])
            return self.nout(a, d, [sel(z, x, y) for x, y, z in zip(dd, nn, mm)])
        if dt is None and m not in ("vtbl", "vtbx", "vswp", "vcnt"):
            raise NeonUnsupported(m)
        if m == "vswp" and n == 2:
            x, y = self.nhalves(d), self.nhalves(ops[1])
            return self.nout(a, d, y) + self.nout(a, ops[1], x)
        esz, cls = dt if dt else (1, "u")
        total = self.nsize(d)
        flt = cls == "f"
        # ---- 원소별 이항 ----
        if (m in INT_BIN or m in FLT_BIN) and n == 3 and ops[2].type == OP_REG:
            return self._n32_bin(a, d, ops, m, esz, cls, total)
        if m in ("vcle", "vclt") and n == 3 and ops[2].type == OP_REG:                                  # 피연산자를 바꿔 vcge/vcgt
            sw = {"vcle": "vcge", "vclt": "vcgt"}[m]
            return self._n32_bin(a, d, [d, ops[2], ops[1]], sw, esz, cls, total)
        if m in ("vmla", "vmls", "vfma", "vfms", "vmlal", "vmlsl") and not m.endswith("l"):
            return self._n32_mla(a, d, ops, m, esz, cls, total)
        if m in ("vabs", "vneg") and n == 2:
            src = self.nhalves(ops[1])
            if flt:
                return self.nout(a, d, [nc.fun_op("fabs" if m == "vabs" else "fneg", esz, p) for p in src])
            return self.nout(a, d, [nc.un_op("abs" if m == "vabs" else "neg", cls, esz, p) for p in src])
        if m in ("vcnt", "vclz", "vcls") and n == 2:
            nm = {"vcnt": "cnt", "vclz": "clz"}.get(m)
            if nm is None:
                raise NeonUnsupported(m)
            return self.nout(a, d, [nc.un_op(nm, "u", esz, p) for p in self.nhalves(ops[1])])
        if m in ("vceq", "vcgt", "vcge", "vcle", "vclt") and n == 3 and ops[2].type == OP_IMM:           # 0 과 비교
            return self._n32_cmpz(a, d, ops, m, esz, cls)
        if m in ("vshl", "vshr", "vsra", "vrshr") and n == 3 and ops[2].type == OP_IMM:
            return self._n32_shift(a, d, ops, m, esz, cls)
        if m in ("vrev16", "vrev32", "vrev64") and n == 2:
            blk = {"vrev16": 2, "vrev32": 4, "vrev64": 8}[m]
            if blk <= esz:
                raise NeonUnsupported(m)
            return self.nout(a, d, nc.rev_halves(blk, esz, self.nhalves(ops[1])))
        if m == "vdup" and n == 2:
            return self._n32_dup(insn, a, d, ops[1], esz, total)
        if m == "vext" and n == 4:
            return self.nout(a, d, nc.ext_halves(ops[3].imm * esz if esz > 1 else ops[3].imm, self.nhalves(ops[1]), self.nhalves(ops[2]), total))
        if m in ("vzip", "vuzp", "vtrn") and n == 2:
            return self._n32_zip(a, m, d, ops[1], esz, total)
        if m == "vmovl" and n == 2:
            return self._n32_movl(a, d, ops[1], esz, cls)
        if m in ("vmovn", "vqmovn", "vqmovun") and n == 2:
            if m != "vmovn":
                raise NeonUnsupported(m)
            return self._n32_movn(a, d, ops[1], esz)
        if m in ("vaddl", "vsubl", "vmull", "vabdl") and n == 3:
            return self._n32_wide3(a, d, ops, m, esz, cls)
        if m in ("vaddw", "vsubw") and n == 3:
            return self._n32_widew(a, d, ops, m, esz, cls)
        if m in ("vmlal", "vmlsl") and n == 3:
            return self._n32_wide3(a, d, ops, m, esz, cls)
        if m in ("vpadd", "vpmax", "vpmin") and n == 3:
            return self._n32_pairwise(a, d, ops, m, esz, cls)
        if m == "vshrn" and n == 3:
            return self._n32_movn(a, d, ops[1], esz, shift=ops[2].imm)
        if m == "vcvt" and n in (2, 3):
            return self._n32_cvt(a, d, ops, dts, n)
        if m in ("vrecpe", "vrsqrte") and n == 2:
            nm = "frecpe" if m == "vrecpe" else "frsqrte"
            return self.nout(a, d, [nc.fun_op(nm, esz, p) for p in self.nhalves(ops[1])])
        raise NeonUnsupported(m)

    # ---- 연산별 -------------------------------------------------------------------------
    def _bcast_scalar(self, op, esz, cls):
        """by-scalar 피연산자 `d2[3]` 를 한 조각에 편 값."""
        half, i = self.nelem(op, esz)
        sc = vlane(half, i, esz, cls)
        return sc if esz == 8 else vdup(sc, esz, cls)

    def _n32_bin(self, a, d, ops, m, esz, cls, total):
        flt = cls == "f"
        table = FLT_BIN if flt else INT_BIN
        nm = table[m]
        x = self.nhalves(ops[1])
        if len(ops) < 3:
            raise NeonUnsupported(m)
        if ops[2].vector_index >= 0:                                                                # by-scalar
            y = [self._bcast_scalar(ops[2], esz, cls)] * len(x)
        else:
            y = self.nhalves(ops[2])
        if nm in ("max", "min", "abd", "qadd", "qsub", "hadd", "rhadd", "shl", "cgt", "cge") and cls == "f":
            raise NeonUnsupported(m)
        c = cls
        if nm in ("ceq", "tst", "add", "sub", "mul"):
            c = "s"
        return self.nout(a, d, [nc.bin_op(nm, c, esz, p, q, flt) for p, q in zip(x, y)])

    def _n32_mla(self, a, d, ops, m, esz, cls, total):
        flt = cls == "f"
        acc = self.nhalves(d)
        x = self.nhalves(ops[1])
        y = [self._bcast_scalar(ops[2], esz, cls)] * len(x) if ops[2].vector_index >= 0 else self.nhalves(ops[2])
        if m in ("vfma", "vfms"):
            nm = "fmla" if m == "vfma" else "fmls"
            return self.nout(a, d, [nc.mla_op(nm, esz, True, ac, p, q) for ac, p, q in zip(acc, x, y)])
        nm = "mla" if m == "vmla" else "mls"
        if flt:                                                                                      # vmla.f32 는 융합이 아니다
            return self.nout(a, d, [nc.mla_nofuse_op(nm, esz, ac, p, q) for ac, p, q in zip(acc, x, y)])
        return self.nout(a, d, [nc.mla_op(nm, esz, False, ac, p, q) for ac, p, q in zip(acc, x, y)])

    def _n32_cmpz(self, a, d, ops, m, esz, cls):
        zero = C(0, 8)
        flt = cls == "f"
        nm = {"vceq": "ceq", "vcgt": "cgt", "vcge": "cge", "vcle": "cge", "vclt": "cgt"}[m]
        swap = m in ("vcle", "vclt")
        if flt:
            nm = {"ceq": "fceq", "cgt": "fcgt", "cge": "fcge"}[nm]
        src = self.nhalves(ops[1])
        c = "s" if cls != "u" else "s"
        return self.nout(a, d, [nc.bin_op(nm, c, esz, zero, p, flt) if swap else nc.bin_op(nm, c, esz, p, zero, flt) for p in src])

    def _n32_shift(self, a, d, ops, m, esz, cls):
        n = ops[2].imm
        src = self.nhalves(ops[1])
        if m == "vshl":
            return self.nout(a, d, [nc.shift_op("shl", n, esz, cls, p) for p in src])
        if m == "vrshr":
            raise NeonUnsupported(m)
        if m == "vsra":
            acc = self.nhalves(d)
            return self.nout(a, d, [nc.shift_op("sra", n, esz, cls, p, ac) for p, ac in zip(src, acc)])
        return self.nout(a, d, [nc.shift_op("shr", n, esz, cls, p) for p in src])

    def _n32_movi(self, insn, m, dts, ops, a, invert):
        d, s = ops
        dt = parse_dt(dts[0]) if dts else None
        if dt is None:
            raise NeonUnsupported(m)
        esz = dt[0]
        v = nc.imm_pattern(s.imm, esz) if esz < 8 else (s.imm & ir.mask(8))
        if dts[0] == "i64":
            mt = re.search(r"#(0x[0-9a-fA-F]+|\d+)\s*$", insn.op_str)
            v = (int(mt.group(1), 0) if mt else s.imm) & ir.mask(8)
        if invert:
            v = ~v & ir.mask(8)
        if dts[0] == "f32" and s.type == OP_FP:
            v = nc.fp_imm_bits(s.fp, 4)
        return self.nout(a, d, [C(v, 8)] * (2 if self.nsize(d) == 16 else 1))

    def _n32_dup(self, insn, a, d, s, esz, total):
        n_p = 2 if total == 16 else 1
        if s.type == OP_REG and not self.is_vreg(s):                                                  # vdup.32 q0, r0
            v = self.read_reg(s.reg, insn)
            sc = trunc(v, esz) if v.sz > esz else v
            h = vdup(sc, esz, "u") if esz < 8 else sc
            return self.nout(a, d, [h] * n_p)
        if self.is_vreg(s) and s.vector_index >= 0:                                                   # vdup.16 d0, d1[2]
            half, i = self.nelem(s, esz)
            sc = vlane(half, i, esz, "u")
            h = vdup(sc, esz, "u") if esz < 8 else sc
            return self.nout(a, d, [h] * n_p)
        raise NeonUnsupported("vdup")

    def _n32_zip(self, a, m, d, s, esz, total):
        """vzip/vuzp/vtrn: 두 피연산자를 제자리에서 바꾼다(목적지 둘)."""
        kd, kn = d, s
        x, y = self.nhalves(kd), self.nhalves(kn)
        if m == "vzip":
            lo = nc.zip_indices("zip1", total, esz)
            hi = nc.zip_indices("zip2", total, esz)
        elif m == "vuzp":
            lo = nc.zip_indices("uzp1", total, esz)
            hi = nc.zip_indices("uzp2", total, esz)
        else:
            lo = nc.zip_indices("trn1", total, esz)
            hi = nc.zip_indices("trn2", total, esz)
        if esz == 8 and total == 8:
            raise NeonUnsupported(m)
        o1 = nc.perm_halves(lo, x, y, esz, total)
        o2 = nc.perm_halves(hi, x, y, esz, total)
        # d 쪽이 '1' 결과, s 쪽이 '2' 결과 (읽기를 모두 끝낸 뒤 쓴다: 위 perm_halves 가 이미 원본 식으로 만들어 두었다)
        t1 = [self.tmp(8) for _ in o1]
        t2 = [self.tmp(8) for _ in o2]
        out = [S("asg", a, t, v) for t, v in zip(t1 + t2, o1 + o2)]
        out += self.nout(a, kd, t1) + self.nout(a, kn, t2)
        return out

    def _src_lanes32(self, op, esz, high=False):
        pieces = self.nhalves(op)
        return [vlane(pieces[1 if high and len(pieces) > 1 else 0], j, esz, "u") for j in range(8 // esz)]

    def _n32_movl(self, a, d, s, esz, cls):
        ext = sext if cls == "s" else zext
        wide = 2 * esz
        lanes = [ext(l, wide) for l in self._src_lanes32(s, esz)]
        return self.nout(a, d, self._pack_wide(lanes, wide))

    def _pack_wide(self, lanes, esz):
        per = 8 // esz
        if esz == 8:
            return lanes[:2]
        return [vpack(lanes[i:i + per], esz, "s") for i in range(0, len(lanes), per)]

    def _n32_movn(self, a, d, s, esz, shift=None):
        """좁히기(vmovn.i32 d0, q1): 소스 q 의 모든 원소를 절반 폭으로. esz 는 소스 원소 폭."""
        half = esz // 2
        lanes = []
        for p in self.nhalves(s):
            for j in range(8 // esz):
                v = vlane(p, j, esz, "u")
                if shift:
                    v = binop("shr", v, C(shift, 1), esz)
                lanes.append(trunc(v, half))
        piece = vpack(lanes, half, "u") if half < 8 else lanes[0]
        return self.nout(a, d, [piece])

    def _n32_wide3(self, a, d, ops, m, esz, cls):
        ext = sext if cls == "s" else zext
        wide = 2 * esz
        x, y = ops[1], ops[2]
        wa = [ext(l, wide) for l in self._src_lanes32(x, esz)]
        if y.vector_index >= 0:
            half, i = self.nelem(y, esz)
            wb = [ext(vlane(half, i, esz, "u"), wide)] * len(wa)
        else:
            wb = [ext(l, wide) for l in self._src_lanes32(y, esz)]
        f = {"vaddl": "add", "vsubl": "sub", "vmull": "mul", "vmlal": "mul", "vmlsl": "mul", "vabdl": "sub"}[m]
        lanes = [binop(f, p, q, wide) for p, q in zip(wa, wb)]
        pieces = self._pack_wide(lanes, wide)
        if m in ("vmlal", "vmlsl"):
            acc = self.nhalves(d)
            add = (lambda p, q: binop("add", p, q, 8)) if wide == 8 else (lambda p, q: vop("add", (p, q), wide, "s"))
            sub = (lambda p, q: binop("sub", p, q, 8)) if wide == 8 else (lambda p, q: vop("sub", (p, q), wide, "s"))
            f2 = add if m == "vmlal" else sub
            pieces = [f2(ac, p) for ac, p in zip(acc, pieces)]
        if m == "vabdl":
            raise NeonUnsupported(m)
        return self.nout(a, d, pieces)

    def _n32_widew(self, a, d, ops, m, esz, cls):
        ext = sext if cls == "s" else zext
        wide = 2 * esz
        wb = [ext(l, wide) for l in self._src_lanes32(ops[2], esz)]
        pieces = self._pack_wide(wb, wide)
        x = self.nhalves(ops[1])
        f = (lambda p, q: binop("add" if m == "vaddw" else "sub", p, q, 8)) if wide == 8 else (lambda p, q: vop("add" if m == "vaddw" else "sub", (p, q), wide, "s"))
        return self.nout(a, d, [f(p, q) for p, q in zip(x, pieces)])

    def _n32_pairwise(self, a, d, ops, m, esz, cls):
        if self.nsize(d) != 8:
            raise NeonUnsupported(m)                                                                 # NEON 의 쌍별 연산은 d 레지스터만
        nm = {"vpadd": "padd", "vpmax": "pmax", "vpmin": "pmin"}[m]
        if cls == "f":
            nm = {"padd": "fpadd", "pmax": "fpmax", "pmin": "fpmin"}[nm]
        x, y = self.nhalves(ops[1])[0], self.nhalves(ops[2])[0]
        if esz == 8:
            raise NeonUnsupported(m)
        return self.nout(a, d, [vop(nm, (x, y), esz, cls)])

    def _n32_cvt(self, a, d, ops, dts, n):
        if len(dts) != 2 or n != 2:
            raise NeonUnsupported("vcvt")
        td, ts = dts
        nm = {("f32", "s32"): "scvtf", ("f32", "u32"): "ucvtf", ("s32", "f32"): "fcvtzs", ("u32", "f32"): "fcvtzu"}.get((td, ts))
        if nm is None:
            raise NeonUnsupported("vcvt")
        return self.nout(a, d, [vop(nm, (p,), 4, "f") for p in self.nhalves(ops[1])])

    def _n32_lane_to_core(self, insn, dts, ops, a):
        d, s = ops
        dt = parse_dt(dts[0]) if dts else None
        if dt is None:
            raise NeonUnsupported("vmov")
        esz = dt[0]
        half, i = self.nelem(s, esz)
        sc = vlane(half, i, esz, "s")
        v = sext(sc, 4) if (dts[0][0] == "s" and esz < 4) else (zext(sc, 4) if esz < 4 else sc)
        return [self.write_reg(d.reg, v, a, insn)]

    def _n32_core_to_lane(self, insn, dts, ops, a):
        d, s = ops
        dt = parse_dt(dts[0]) if dts else None
        if dt is None or s.type != OP_REG or self.is_vreg(s):
            raise NeonUnsupported("vmov")
        esz = dt[0]
        half, i = self.nelem(d, esz)
        v = self.read_reg(s.reg, insn)
        sc = trunc(v, esz) if v.sz > esz else v
        kind, idx = self.vinfo(d.reg)
        newv = vsetlane(half, sc, i, esz, "u") if esz < 8 else sc
        return self.vwrite(a, "d", idx, newv)

    # ---- 메모리 -------------------------------------------------------------------------
    def _n32_ldst(self, insn, m, dts, ops, a):
        regs = [o for o in ops if o.type == OP_REG and self.is_vreg(o)]
        mi = next(i for i, o in enumerate(ops) if o.type == OP_MEM)
        mop = ops[mi]
        post = ops[mi + 1] if mi + 1 < len(ops) else None
        load = m.startswith("vld")
        nn = int(m[3])
        dt = parse_dt(dts[0]) if dts else None
        if dt is None:
            raise NeonUnsupported(m)
        esz = dt[0]
        base = self.reg_name(mop.mem.base)
        ea = loc(base, 4)
        out = []
        # 레지스터 목록: 연속한 d 들(`{d0, d1}`); 레인 형식 `{d0[3]}`; 모든 레인 `{d0[]}`
        txt = insn.op_str
        lane_idx = None
        mt = re.search(r"\{[^}]*\[(\d*)\]\s*\}", txt)
        allin = mt is not None and mt.group(1) == ""
        if mt is not None and mt.group(1) != "":
            lane_idx = int(mt.group(1))
        names = re.findall(r"d(\d+)", txt[:txt.index("}")]) if "}" in txt else []
        dregs = [int(x) for x in names]
        if "-" in txt[:txt.index("}")] if "}" in txt else False:                                    # {d0-d3}
            lo_, hi_ = int(names[0]), int(names[-1])
            dregs = list(range(lo_, hi_ + 1))
        if not dregs:
            raise NeonUnsupported(m)
        # 이중 간격 레지스터(`{d0, d2}`)는 간격 2 로
        stride = (dregs[1] - dregs[0]) if len(dregs) > 1 else 1
        nregs = len(dregs)
        xfer = 0
        if lane_idx is not None:                                                                       # 레인 형식: nn 개 원소를 서로 다른 레지스터의 한 레인에
            per = 8 // esz
            for k in range(nn):
                reg = dregs[k] if k < nregs else dregs[0] + k * stride
                addr = ea if k == 0 else binop("add", ea, C(k * esz, 4), 4)
                cur = self.vread("d", reg)
                if load:
                    v = self.load_mem(addr, esz)
                    out += self.vwrite(a, "d", reg, vsetlane(cur, v, lane_idx, esz, "u") if esz < 8 else v)
                else:
                    out.append(S("asg", a, mem(addr, esz), vlane(cur, lane_idx, esz, "u")))
            xfer = nn * esz
        elif allin:                                                                                    # vld1.32 {d0[], d1[]}, [r0] / vld2.8 {d0[], d1[]}, [r0]
            for k in range(nn):
                addr = ea if k == 0 else binop("add", ea, C(k * esz, 4), 4)
                v = self.load_mem(addr, esz)
                h = vdup(v, esz, "u") if esz < 8 else v
                targets = dregs if nn == 1 else [dregs[k]]
                for r in targets:
                    out += self.vwrite(a, "d", r, h)
            xfer = nn * esz
        else:
            per = 8 // esz
            G = nregs // nn if nn > 1 else 1                                                           # 16바이트(= nn 개 d) 묶음 수
            xfer = nregs * 8
            if load:
                raw = [self.load_mem(binop("add", ea, C(8 * c, 4), 4) if c else ea, 8) for c in range(nregs)]
                if nn == 1:
                    for k, r in enumerate(dregs):
                        out += self.vwrite(a, "d", r, raw[k])
                else:
                    tmps = []
                    for c in range(nregs):
                        t = self.tmp(8)
                        out.append(S("asg", a, t, raw[c]))
                        tmps.append(t)
                    chunks = {c: tmps[c] for c in range(nregs)}
                    for g in range(G):
                        for r_i in range(nn):
                            srcs = []
                            for i in range(per):
                                j = i * nn + r_i
                                srcs.append((g * nn + (j * esz) // 8, ((j * esz) % 8) // esz))
                            out += self.vwrite(a, "d", dregs[r_i * G + g], nc.gather_pieces(chunks, srcs, esz))
            else:
                pieces = {r_i: self.vread("d", r) for r_i, r in enumerate(dregs)}
                if nn == 1:
                    for k, r in enumerate(dregs):
                        out.append(S("asg", a, mem(binop("add", ea, C(8 * k, 4), 4) if k else ea, 8), pieces[k]))
                else:
                    for g in range(G):
                        for ci in range(nn):                                                            # 이 묶음의 ci 번째 8바이트 조각
                            srcs = []
                            for l in range(per):
                                j = ci * per + l
                                r_i, i = j % nn, j // nn
                                srcs.append((r_i * G + g, i))
                            val = nc.gather_pieces(pieces, [(k, l) for k, l in srcs], esz)
                            c = g * nn + ci
                            out.append(S("asg", a, mem(binop("add", ea, C(8 * c, 4), 4) if c else ea, 8), val))
        # 후위 증가: `!` (전송 크기) 또는 `, rM`
        if insn.writeback:
            out.append(S("asg", a, loc(base, 4), binop("add", loc(base, 4), C(xfer, 4), 4)))
        elif post is not None and post.type == OP_REG:
            out.append(S("asg", a, loc(base, 4), binop("add", loc(base, 4), self.read_reg(post.reg, insn), 4)))
        return out
