"""ARM64 부동소수점·SIMD(스칼라 d/s 와 벡터 레지스터의 데이터 이동·비트 연산) 리프트.

v0..v31 은 x86 의 xmm 과 같이 하위·상위 64비트 두 위치 `v0`·`v0h` 로 둔다. d 레지스터 쓰기는 하위 64비트를 쓰고 상위를 0 으로 만들며,
s 레지스터 쓰기는 하위 32비트만 채우고 나머지를 0 으로 만든다(x86 과 달리 윗부분을 보존하지 않는다).
값은 IEEE 비트 패턴 정수식으로 두고 부동소수점 연산은 ir 의 fadd·fmul·i2f·f2i 등으로 쓴다.
"""
from __future__ import annotations

import re
import struct

from capstone import arm64 as c64
from capstone.arm64 import ARM64_OP_REG as OP_REG, ARM64_OP_IMM as OP_IMM, ARM64_OP_MEM as OP_MEM, ARM64_OP_FP as OP_FP

from . import ir
from .ir import C, X, S, loc, mem, add, binop, unop, zext, trunc, cmp, ite, flags, cc, insert

FLAGS = "F"
FPR = re.compile(r"^([bhsdqv])(\d+)$")
VSZ = {"b": 1, "h": 2, "s": 4, "d": 8, "q": 16, "v": 16}


class A64Fp:
    """A64Lifter 에 섞어 쓴다(self.cs·read_reg·write_reg·mem_addr·tmp·reg_sz·load_mem 사용)."""

    def fp_info(self, rid):
        n = self.cs.reg_name(rid)
        m = FPR.match(n or "")
        return (m.group(1), int(m.group(2))) if m else None

    def is_fpop(self, op) -> bool:
        return op.type == OP_REG and self.fp_info(op.reg) is not None

    def lanes_of(self, rid):
        k, n = self.fp_info(rid)
        return loc(f"v{n}", 8), loc(f"v{n}h", 8)

    def fread(self, op, sz=None) -> X:
        k, n = self.fp_info(op.reg)
        sz = sz or VSZ[k]
        lo = loc(f"v{n}", 8)
        return lo if sz == 8 else trunc(lo, sz)

    def fwrite(self, a, op, val: X) -> list:
        """스칼라 쓰기(b·h·s·d): 하위 폭만 채우고 위쪽은 0."""
        k, n = self.fp_info(op.reg)
        lo = zext(val, 8) if val.sz < 8 else val
        return [S("asg", a, loc(f"v{n}", 8), lo), S("asg", a, loc(f"v{n}h", 8), C(0, 8))]

    # ---- 진입점 --------------------------------------------------------------------------
    def _fp(self, insn, m, ops, a):
        """이 명령이 부동소수점·SIMD 레지스터를 다루면 IR 문장 목록을, 아니면 None 을 돌려준다."""
        if not any(self.is_fpop(o) for o in ops):
            return None
        n = len(ops)
        scalar = all(o.vas == 0 and o.vector_index < 0 for o in ops if o.type == OP_REG)
        if m in ("ldr", "str", "ldur", "stur", "ldp", "stp", "ldnp", "stnp") and self.is_fpop(ops[0]):
            return self._fp_memop(insn, m, ops, a)
        if m == "fmov" and not (self.is_fpop(ops[0]) and ops[0].vas != 0):        # 벡터 배열에 즉값을 채우는 fmov v.2d, #1.0 은 벡터 쪽
            return self._fmov(insn, ops, a)
        if scalar:
            r = self._fp_scalar(insn, m, ops, a)
            if r is not None:
                return r
        r = self._neon(insn, m, ops, a)
        if r is not None:
            return r
        return self._simd(insn, m, ops, a)

    # ---- 스칼라 --------------------------------------------------------------------------
    def _fmov(self, insn, ops, a):
        d, s = ops[0], ops[1]
        if self.is_fpop(d) and d.vector_index >= 0:                    # fmov v0.d[1], x1
            k, nn = self.fp_info(d.reg)
            return [S("asg", a, loc(f"v{nn}h" if d.vector_index == 1 else f"v{nn}", 8), self.read_reg(s.reg))]
        if self.is_fpop(s) and s.vector_index >= 0:                    # fmov x0, v0.d[1]
            k, nn = self.fp_info(s.reg)
            return [self.write_reg(d.reg, loc(f"v{nn}h" if s.vector_index == 1 else f"v{nn}", 8), a)]
        if self.is_fpop(d):
            k, _ = self.fp_info(d.reg)
            sz = VSZ[k]
            if s.type == OP_FP:
                bits = struct.unpack("<Q", struct.pack("<d", s.fp))[0] if sz == 8 else struct.unpack("<I", struct.pack("<f", s.fp))[0]
                return self.fwrite(a, d, C(bits, sz))
            if s.type == OP_REG and self.is_fpop(s):
                return self.fwrite(a, d, self.fread(s, sz))
            v = self.read_reg(s.reg)
            return self.fwrite(a, d, v if v.sz == sz else (trunc(v, sz) if v.sz > sz else zext(v, sz)))
        k, _ = self.fp_info(s.reg)
        return [self.write_reg(d.reg, self.fread(s, VSZ[k]), a)]

    def _fp_scalar(self, insn, m, ops, a):
        n = len(ops)
        if m in ("fadd", "fsub", "fmul", "fdiv", "fmax", "fmin", "fmaxnm", "fminnm", "fnmul") and n == 3:
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            x, y = self.fread(ops[1], sz), self.fread(ops[2], sz)
            op = {"fadd": "fadd", "fsub": "fsub", "fmul": "fmul", "fdiv": "fdiv", "fmax": "fcmax", "fmin": "fcmin", "fmaxnm": "fcmax",
                  "fminnm": "fcmin", "fnmul": "fmul"}[m]
            r = X(op, (x, y), sz)
            if m == "fnmul":
                r = X("fneg", (r,), sz)
            return self.fwrite(a, ops[0], r)
        if m in ("fsqrt", "fneg", "fabs") and n == 2:
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            return self.fwrite(a, ops[0], X(m, (self.fread(ops[1], sz),), sz))
        if m in ("fmadd", "fmsub", "fnmadd", "fnmsub") and n == 4:
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            x, y, z = (self.fread(ops[i], sz) for i in (1, 2, 3))
            if m == "fmadd":
                r = X("ffma", (x, y, z), sz)                       # x*y + z
            elif m == "fmsub":
                r = X("ffma", (X("fneg", (x,), sz), y, z), sz)       # z - x*y
            elif m == "fnmadd":
                r = X("ffma", (X("fneg", (x,), sz), y, X("fneg", (z,), sz)), sz)   # -x*y - z
            else:
                r = X("ffma", (x, y, X("fneg", (z,), sz)), sz)       # x*y - z
            return self.fwrite(a, ops[0], r)
        if m.startswith("frint") and n == 2:
            mode = {"frintz": "trunc", "frintm": "floor", "frintp": "ceil", "frinta": "round", "frintn": "rint", "frinti": "rint", "frintx": "rint"}.get(m)
            if mode is None:
                return None
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            return self.fwrite(a, ops[0], X("fround", (self.fread(ops[1], sz),), sz, mode))
        if m == "fcvt" and n == 2:
            ks, kd = self.fp_info(ops[1].reg)[0], self.fp_info(ops[0].reg)[0]
            if VSZ[ks] not in (4, 8) or VSZ[kd] not in (4, 8):
                return None
            return self.fwrite(a, ops[0], X("f2f", (self.fread(ops[1], VSZ[ks]),), VSZ[kd]))
        if m in ("scvtf", "ucvtf") and n in (2, 3) and self.is_fpop(ops[0]):
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            if self.is_fpop(ops[1]):                                         # scvtf d0, d1: 부동소수점 레지스터에 담긴 정수를 변환
                ks = self.fp_info(ops[1].reg)[0]
                src = self.fread(ops[1], VSZ[ks])
            else:
                src = self.read_reg(ops[1].reg)
            r = X("i2f", (src,), sz, "s" if m == "scvtf" else "u")
            if n == 3 and ops[2].type == OP_IMM and ops[2].imm:               # 고정 소수점: 2^fbits 로 나눈다(2 의 거듭제곱이라 정확하다)
                scale = struct.unpack("<Q", struct.pack("<d", float(2 ** ops[2].imm)))[0] if sz == 8 else struct.unpack("<I", struct.pack("<f", float(2 ** ops[2].imm)))[0]
                r = X("fdiv", (r, C(scale, sz)), sz)
            return self.fwrite(a, ops[0], r)
        if m.startswith("fcvt") and m[-1] in "su" and m[4:6] in ("zs", "zu", "ms", "mu", "ps", "pu", "as", "au", "ns", "nu") and n in (2, 3):
            k, _ = self.fp_info(ops[1].reg)
            sz = VSZ[k]
            mode = {"z": None, "m": "floor", "p": "ceil", "a": "round", "n": "rint"}[m[4]]
            f = self.fread(ops[1], sz)
            if n == 3 and ops[2].type == OP_IMM and ops[2].imm:               # 고정 소수점: 2^fbits 를 곱한 뒤 자른다
                scale = struct.unpack("<Q", struct.pack("<d", float(2 ** ops[2].imm)))[0] if sz == 8 else struct.unpack("<I", struct.pack("<f", float(2 ** ops[2].imm)))[0]
                f = X("fmul", (f, C(scale, sz)), sz)
            if mode:
                f = X("fround", (f,), sz, mode)
            if self.is_fpop(ops[0]):                                         # fcvtzs d0, d1: 결과를 부동소수점 레지스터에 정수로
                kd = self.fp_info(ops[0].reg)[0]
                return self.fwrite(a, ops[0], X("f2i", (f,), VSZ[kd], "u" if m[-1] == "u" else "s"))
            dsz = self.reg_sz(ops[0].reg)
            return [self.write_reg(ops[0].reg, X("f2i", (f,), dsz, "u" if m[-1] == "u" else "s"), a)]
        if m in ("fcmp", "fcmpe") and n == 2:
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            A = self.fread(ops[0], sz)
            if ops[1].type == OP_FP:
                B = C(0, sz)                                            # fcmp d0, #0.0
            else:
                B = self.fread(ops[1], sz)
            return [S("asg", a, loc(FLAGS, 1), flags("fcmpa", (A, B), sz))]
        if m == "fcsel" and n == 3:
            k, _ = self.fp_info(ops[0].reg)
            sz = VSZ[k]
            cond = cc(self._arm_cc(insn), loc(FLAGS, 1))
            return self.fwrite(a, ops[0], ite(cond, self.fread(ops[1], sz), self.fread(ops[2], sz)))
        return None

    # ---- 메모리 --------------------------------------------------------------------------
    def _fp_memop(self, insn, m, ops, a):
        out = []
        pair = m in ("ldp", "stp", "ldnp", "stnp")
        mem_i = 2 if pair else 1
        mop = ops[mem_i]
        load = m.startswith("ld")
        regs = [ops[0]] + ([ops[1]] if pair else [])
        sz = VSZ[self.fp_info(ops[0].reg)[0]]
        if mop.type == OP_IMM and not pair:                              # ldr d0, <리터럴 풀>
            if self.read is None or not load or sz not in (4, 8, 16):
                raise ValueError("literal")
            raw = self.read(mop.imm, sz)
            if len(raw) < sz:
                raise ValueError("literal")
            if sz == 16:
                return self.lanes_write(a, regs[0], C(int.from_bytes(raw[:8], "little"), 8), C(int.from_bytes(raw[8:], "little"), 8))
            return self.fwrite(a, regs[0], C(int.from_bytes(raw, "little"), sz))
        addr = self.mem_addr(mop)
        post = len(ops) > mem_i + 1 and ops[mem_i + 1].type == OP_IMM
        if insn.writeback and not post and load:
            t = self.tmp(8)
            out.append(S("asg", a, t, addr))
            addr = t
        for k, op in enumerate(regs):
            ea = addr if k == 0 else binop("add", addr, C(k * sz, 8), 8)
            if load:
                if sz == 16:
                    out.extend(self.lanes_write(a, op, self.load_mem(ea, 8), self.load_mem(binop("add", ea, C(8, 8), 8), 8)))
                else:
                    out.extend(self.fwrite(a, op, self.load_mem(ea, sz)))
            else:
                kk, nn = self.fp_info(op.reg)
                if sz == 16:
                    out.append(S("asg", a, mem(ea, 8), loc(f"v{nn}", 8)))
                    out.append(S("asg", a, mem(binop("add", ea, C(8, 8), 8), 8), loc(f"v{nn}h", 8)))
                else:
                    out.append(S("asg", a, mem(ea, sz), self.fread(op, sz)))
        if insn.writeback:
            bname = self.reg_info(mop.mem.base)[0]
            if post:
                out.append(S("asg", a, loc(bname, 8), binop("add", loc(bname, 8), C(ops[mem_i + 1].imm, 8), 8)))
            else:
                out.append(S("asg", a, loc(bname, 8), binop("add", loc(bname, 8), C(mop.mem.disp, 8), 8)))
        return out

    def lanes_write(self, a, op, lo: X, hi: X) -> list:
        k, n = self.fp_info(op.reg)
        return [S("asg", a, loc(f"v{n}", 8), lo), S("asg", a, loc(f"v{n}h", 8), hi)]

    # ---- 벡터(데이터 이동·비트 연산만) ------------------------------------------------------
    def _simd(self, insn, m, ops, a):
        n = len(ops)
        d = ops[0]
        if m == "movi" and n == 2 and ops[1].type == OP_IMM and ops[1].imm == 0 and self.is_fpop(d):
            if VSZ[self.fp_info(d.reg)[0]] == 16:
                return self.lanes_write(a, d, C(0, 8), C(0, 8))
            return self.fwrite(a, d, C(0, 8))
        if m in ("mov", "orr") and self.is_fpop(d) and d.vector_index < 0 and all(self.is_fpop(o) and o.vector_index < 0 for o in ops[1:]) \
                and (m == "mov" or (n == 3 and ops[1].reg == ops[2].reg)):
            lo, hi = self.lanes_of(ops[1].reg)
            return self.lanes_write(a, d, lo, hi)
        if m in ("eor", "and", "orr", "bic", "orn") and n == 3 and all(self.is_fpop(o) and o.vector_index < 0 for o in ops):
            l1, h1 = self.lanes_of(ops[1].reg)
            l2, h2 = self.lanes_of(ops[2].reg)
            if m in ("bic", "orn"):
                l2, h2 = unop("not", l2), unop("not", h2)
            op = {"eor": "xor", "and": "and", "orr": "or", "bic": "and", "orn": "or"}[m]
            return self.lanes_write(a, d, binop(op, l1, l2, 8), binop(op, h1, h2, 8))
        if m in ("mov", "ins") and self.is_fpop(d) and d.vector_index >= 0 and n == 2:      # mov v0.d[1], x1 / ins v0.d[1], v1.d[0]
            k, nn = self.fp_info(d.reg)
            if d.vas != c64.ARM64_VAS_1D and d.vas != c64.ARM64_VAS_2D and d.vas != 13:
                pass
            src = ops[1]
            if src.type == OP_REG and self.is_fpop(src):
                if src.vector_index < 0:
                    return None
                ks, ns = self.fp_info(src.reg)
                v = loc(f"v{ns}h" if src.vector_index == 1 else f"v{ns}", 8)
            else:
                v = self.read_reg(src.reg)
            if v.sz != 8:
                return None
            return [S("asg", a, loc(f"v{nn}h" if d.vector_index == 1 else f"v{nn}", 8), v)]
        if m in ("mov", "dup") and self.is_fpop(d) and d.vector_index < 0 and d.vas == 0 and n == 2 and self.is_fpop(ops[1]) \
                and ops[1].vector_index >= 0:                                  # mov d3, v1.d[1]  (= dup d3, v1.d[1])
            kd = self.fp_info(d.reg)[0]
            ks, ns = self.fp_info(ops[1].reg)
            if VSZ[kd] != 8:
                return None
            return self.fwrite(a, d, loc(f"v{ns}h" if ops[1].vector_index == 1 else f"v{ns}", 8))
        if m in ("mov", "umov") and not self.is_fpop(d) and n == 2 and self.is_fpop(ops[1]) and ops[1].vector_index >= 0:   # mov x0, v0.d[1]
            ks, ns = self.fp_info(ops[1].reg)
            v = loc(f"v{ns}h" if ops[1].vector_index == 1 else f"v{ns}", 8)
            if self.reg_sz(d.reg) != 8:
                return None
            return [self.write_reg(d.reg, v, a)]
        if m == "dup" and n == 2 and self.is_fpop(d) and ops[1].type == OP_REG and not self.is_fpop(ops[1]):
            v = self.read_reg(ops[1].reg)
            if v.sz == 8 and d.vas == 12:                                        # dup v0.2d, x1
                return self.lanes_write(a, d, v, v)
        return None
