"""ARM32 VFP(스칼라 부동소수점) → IR 리프트.

물리 레지스터는 d0–d31 (= 64비트 칸 32개). s2k 와 s2k+1 은 d(k) 의 아래·위 32비트다(k < 16). 같은 칸을 d 와 s 로 함께 부르므로,
함수 전체를 미리 훑어 칸마다 표현을 하나 고른다: 칸을 처음 건드리는 접근이 s 이면 's 모드'(32비트 위치 s(2k)·s(2k+1) 둘),
d 이면 'd 모드'(64비트 위치 d(k) 하나). 다른 쪽 이름으로 접근할 때는 모드에 맞게 이어 붙이거나(pair) 쪼갠다(trunc·shr).
float 인자 s0·s1·s2 가 서로 다른 매개변수로 보이면서 double 인자 d0 은 한 값으로 보이게 하려는 것이다.

실수 값은 IEEE 비트 패턴 정수식이고 연산은 fadd·fmul·i2f·f2i 등이다. 비교 결과(FPSCR 의 N·Z·C·V)는 'FPF' 위치에 `fl("fcmpa")` 로 두었다가
`vmrs APSR_nzcv, fpscr` 에서 정수 플래그 'F' 로 옮긴다(그 뒤 조건 실행·분기는 ARM 조건 이름으로 읽는다).
"""
from __future__ import annotations

import re
import struct

from capstone.arm import ARM_OP_REG as OP_REG, ARM_OP_IMM as OP_IMM, ARM_OP_MEM as OP_MEM, ARM_OP_FP as OP_FP

from . import ir
from .ir import C, X, S, loc, mem, binop, unop, zext, trunc, pair, ite, flags, cc, insert
from .lift_neon32 import A32Neon, NOT_NEON

FLAGS = "F"
FPF = "FPF"
VREG = re.compile(r"^([sdq])(\d+)$")
CC_NAMES = {"eq", "ne", "hs", "cs", "lo", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le"}
ARITH3 = {"vadd": "fadd", "vsub": "fsub", "vmul": "fmul", "vdiv": "fdiv"}
ROUND = {"vrintz": "trunc", "vrintm": "floor", "vrintp": "ceil", "vrinta": "round", "vrintn": "rint", "vrintx": "rint", "vrintr": "rint"}
CVT_MODE = {"a": "round", "n": "rint", "p": "ceil", "m": "floor"}


def f_bits(v: float, sz: int) -> int:
    return struct.unpack("<Q", struct.pack("<d", v))[0] if sz == 8 else struct.unpack("<I", struct.pack("<f", v))[0]


class A32Vfp(A32Neon):
    """A32Lifter 에 섞어 쓴다(self.cs·reg_name·read_reg·write_reg·mem_addr·load_mem·load_literal·tmp 사용)."""

    # ---- 레지스터 모델 --------------------------------------------------------------------
    def vfp_init(self):
        self.vmode = {}                                            # 칸 번호 → 'd' | 's'

    def vinfo(self, rid):
        n = self.cs.reg_name(rid)
        m = VREG.match(n or "")
        return (m.group(1), int(m.group(2))) if m else None

    def is_vreg(self, op) -> bool:
        return op.type == OP_REG and self.vinfo(op.reg) is not None

    @staticmethod
    def vslots(kind: str, idx: int) -> list:
        return [idx // 2] if kind == "s" else [idx] if kind == "d" else [2 * idx, 2 * idx + 1]

    def mode_of(self, slot: int) -> str:
        return self.vmode.get(slot, "d")

    def vloc_names(self, kind: str, idx: int) -> list:
        """이 레지스터가 가리키는 위치 이름들(모드에 따라)."""
        out = []
        for k in self.vslots(kind, idx):
            if self.mode_of(k) == "d":
                out.append(f"d{k}")
            else:
                out += [f"s{2 * k}", f"s{2 * k + 1}"]
        if kind == "s" and self.mode_of(idx // 2) == "s":
            return [f"s{idx}"]
        return out

    def vfp_prescan(self, insns):
        """칸마다 처음 접근한 쪽(읽기 먼저, 쓰기 나중)으로 모드를 정한다."""
        self.vmode = {}
        for ins in insns:
            try:
                m = ins.mnemonic
            except Exception:                                      # noqa: BLE001
                continue
            if not m.startswith("v"):
                continue
            ops = [o for o in ins.operands if o.type == OP_REG and self.vinfo(o.reg) is not None]
            if not ops:
                continue
            all_read = m.startswith(("vstr", "vstm", "vpush", "vcmp", "vmsr"))
            dest = None if all_read else ops[0]
            order = [o for o in ops if o is not dest]
            if dest is not None and (dest.access & 1):
                order.insert(0, dest)                             # 누적 명령(vfma·vmla …)은 목적지도 먼저 읽는다
            if dest is not None and not (dest.access & 1):
                order.append(dest)
            for o in order:
                kind, idx = self.vinfo(o.reg)
                for k in self.vslots(kind, idx):
                    if k not in self.vmode:
                        self.vmode[k] = "s" if kind == "s" else "d"

    # ---- 읽기·쓰기 ------------------------------------------------------------------------
    def vread(self, kind: str, idx: int) -> X:
        if kind == "d":
            if self.mode_of(idx) == "d":
                return loc(f"d{idx}", 8)
            return pair(loc(f"s{2 * idx}", 4), loc(f"s{2 * idx + 1}", 4))
        k, half = idx // 2, idx % 2
        if self.mode_of(k) == "s":
            return loc(f"s{idx}", 4)
        d = loc(f"d{k}", 8)
        return trunc(d, 4) if half == 0 else ir.hi_half(d)

    def vwrite(self, a: int, kind: str, idx: int, val: X) -> list:
        if kind == "d":
            if self.mode_of(idx) == "d":
                return [S("asg", a, loc(f"d{idx}", 8), val)]
            if val.op == "c":
                return [S("asg", a, loc(f"s{2 * idx}", 4), C(val.aux & 0xFFFFFFFF, 4)),
                        S("asg", a, loc(f"s{2 * idx + 1}", 4), C(val.aux >> 32, 4))]
            t = self.tmp(8)
            return [S("asg", a, t, val),
                    S("asg", a, loc(f"s{2 * idx}", 4), trunc(t, 4)),
                    S("asg", a, loc(f"s{2 * idx + 1}", 4), ir.hi_half(t))]
        k, half = idx // 2, idx % 2
        if self.mode_of(k) == "s":
            return [S("asg", a, loc(f"s{idx}", 4), val)]
        d = loc(f"d{k}", 8)
        if half == 0:
            return [S("asg", a, d, insert(d, val))]
        return [S("asg", a, d, binop("or", binop("and", d, C(0xFFFFFFFF, 8), 8), binop("shl", zext(val, 8), C(32, 1), 8), 8))]

    def vsz(self, op) -> int:
        return 4 if self.vinfo(op.reg)[0] == "s" else 8

    def vr(self, op) -> X:
        kind, idx = self.vinfo(op.reg)
        return self.vread(kind, idx)

    def vw(self, a, op, val) -> list:
        kind, idx = self.vinfo(op.reg)
        return self.vwrite(a, kind, idx, val)

    # ---- 니모닉 -------------------------------------------------------------------------
    def vfp_split(self, insn):
        """('vmov', ['f64']) 처럼 기본 니모닉과 자료형 접미사들로. 조건 접미사(vmovmi.f64)는 뗀다."""
        head, _, tail = insn.mnemonic.partition(".")
        dts = tail.split(".") if tail else []
        if insn.cc not in (0, 15):
            for suf in CC_NAMES:
                if head.endswith(suf) and len(head) > len(suf) + 1 and not head.startswith("vsel"):
                    head = head[:-len(suf)]
                    break
        return head, dts

    def vfp_lift(self, insn):
        """VFP 명령이면 문장 목록을 돌려준다. 아니면 None(호출한 쪽이 Unsupported 로 처리)."""
        head, dts = self.vfp_split(insn)
        ops = list(insn.operands)
        a = insn.address
        n = len(ops)
        nr = self.neon32_lift(insn, head, dts, ops, a)                          # NEON(Advanced SIMD) 형식이면 그쪽에서
        if nr is not NOT_NEON:
            return nr
        w = None
        if dts and dts[0] in ("f32", "f64"):
            w = 4 if dts[0] == "f32" else 8
        elif ops and self.is_vreg(ops[0]):
            w = self.vsz(ops[0])

        if head in ARITH3 and n == 3:
            return self.vw(a, ops[0], X(ARITH3[head], (self.vr(ops[1]), self.vr(ops[2])), w))
        if head == "vnmul" and n == 3:
            return self.vw(a, ops[0], X("fneg", (X("fmul", (self.vr(ops[1]), self.vr(ops[2])), w),), w))
        if head in ("vneg", "vabs", "vsqrt") and n == 2:
            return self.vw(a, ops[0], X({"vneg": "fneg", "vabs": "fabs", "vsqrt": "fsqrt"}[head], (self.vr(ops[1]),), w))
        if head in ("vmla", "vmls", "vnmla", "vnmls") and n == 3:
            acc, p = self.vr(ops[0]), X("fmul", (self.vr(ops[1]), self.vr(ops[2])), w)
            r = {"vmla": lambda: X("fadd", (acc, p), w), "vmls": lambda: X("fsub", (acc, p), w),
                 "vnmla": lambda: X("fsub", (X("fneg", (acc,), w), p), w), "vnmls": lambda: X("fadd", (X("fneg", (acc,), w), p), w)}[head]()
            return self.vw(a, ops[0], r)
        if head in ("vfma", "vfms", "vfnma", "vfnms") and n == 3:
            acc, x, y = self.vr(ops[0]), self.vr(ops[1]), self.vr(ops[2])
            ng = lambda e: X("fneg", (e,), w)                                    # noqa: E731
            r = {"vfma": X("ffma", (x, y, acc), w), "vfms": X("ffma", (ng(x), y, acc), w),
                 "vfnma": X("ffma", (ng(x), y, ng(acc)), w), "vfnms": X("ffma", (x, y, ng(acc)), w)}[head]
            return self.vw(a, ops[0], r)
        if head in ("vmaxnm", "vminnm") and n == 3:
            return self.vw(a, ops[0], X("fcmax" if head == "vmaxnm" else "fcmin", (self.vr(ops[1]), self.vr(ops[2])), w))
        if head in ROUND and n == 2:
            return self.vw(a, ops[0], X("fround", (self.vr(ops[1]),), w, ROUND[head]))
        if head in ("vcmp", "vcmpe") and n == 2:
            A = self.vr(ops[0])
            B = self.vr(ops[1]) if self.is_vreg(ops[1]) else C(0, w)
            return [S("asg", a, loc(FPF, 1), flags("fcmpa", (A, B), w))]
        if head == "vmrs":
            if n == 2 and ops[0].type == OP_REG and (self.cs.reg_name(ops[0].reg) or "").lower().startswith("apsr"):
                return [S("asg", a, loc(FLAGS, 1), loc(FPF, 1))]
            return None
        if head.startswith("vsel") and n == 3:
            code = head[4:]
            return self.vw(a, ops[0], ite(cc(code, loc(FLAGS, 1)), self.vr(ops[1]), self.vr(ops[2])))
        if head == "vmov":
            return self._vmov(insn, ops, dts, a)
        if head in ("vcvt", "vcvtr") or (head.startswith("vcvt") and len(head) == 5 and head[4] in CVT_MODE):
            return self._vcvt(insn, head, dts, ops, a)
        if head in ("vldr", "vstr"):
            return self._vldr_vstr(insn, head, ops, a)
        if head in ("vpush", "vpop", "vldmia", "vldmdb", "vstmia", "vstmdb", "vldm", "vstm"):
            return self._vblock(insn, head, ops, a)
        return None

    # ---- vmov ---------------------------------------------------------------------------
    def _vmov(self, insn, ops, dts, a):
        n = len(ops)
        if n == 2:
            d, s = ops
            if self.is_vreg(d):
                kd, idd = self.vinfo(d.reg)
                if d.vector_index >= 0 if hasattr(d, "vector_index") else False:
                    return self._vmov_lane(insn, ops, dts, a)
                if self.is_vreg(s):
                    if self.vinfo(s.reg)[0] != kd or kd == "q":
                        return None
                    return self.vw(a, d, self.vr(s))
                if s.type == OP_FP:
                    return self.vw(a, d, C(f_bits(s.fp, self.vsz(d)), self.vsz(d)))
                if s.type == OP_IMM and kd == "d":                                # vmov.i32 d16, #0 (NEON 형식의 상수 만들기)
                    v = self._neon_imm(dts, s.imm, 8)
                    return None if v is None else self.vw(a, d, C(v, 8))
                if s.type == OP_REG and kd == "s":                                # vmov s2, r0: 비트 그대로
                    return self.vw(a, d, self.read_reg(s.reg, insn))
                return None
            if d.type == OP_REG and self.is_vreg(s):
                if self.vinfo(s.reg)[0] == "s":                                   # vmov r0, s0
                    return [self.write_reg(d.reg, self.vr(s), a, insn)]
                return self._vmov_lane(insn, ops, dts, a)
            return None
        if n == 3:
            if self.is_vreg(ops[2]) and self.vinfo(ops[2].reg)[0] == "d" and ops[0].type == OP_REG and not self.is_vreg(ops[0]):    # vmov r0, r1, d0
                v = self.vr(ops[2])
                if v.op == "pair":
                    lo, hi = v.args
                    return [self.write_reg(ops[0].reg, lo, a, insn), self.write_reg(ops[1].reg, hi, a, insn)]
                t = self.tmp(8)
                return [S("asg", a, t, v), self.write_reg(ops[0].reg, trunc(t, 4), a, insn), self.write_reg(ops[1].reg, ir.hi_half(t), a, insn)]
            if self.is_vreg(ops[0]) and self.vinfo(ops[0].reg)[0] == "d" and not self.is_vreg(ops[1]):                            # vmov d0, r0, r1
                return self.vw(a, ops[0], pair(self.read_reg(ops[1].reg, insn), self.read_reg(ops[2].reg, insn)))
        return None

    def _vmov_lane(self, insn, ops, dts, a):
        """vmov.32 d0[1], r0 / vmov.32 r0, d0[1]: 32비트 레인만(그 밖의 크기·부호 확장은 NEON 쪽)."""
        if dts not in (["32"], ["i32"], ["s32"], ["u32"]):
            return None
        d, s = ops
        if self.is_vreg(d):
            kd, idd = self.vinfo(d.reg)
            if kd != "d" or s.type != OP_REG or self.is_vreg(s):
                return None
            old = self.vread("d", idd)
            li = d.vector_index
            val = self.read_reg(s.reg, insn)
            if li == 0:
                return self.vwrite(a, "d", idd, insert(old, val))
            return self.vwrite(a, "d", idd, pair(trunc(old, 4), val))
        ks, ids = self.vinfo(s.reg)
        if ks != "d":
            return None
        v = self.vread("d", ids)
        return [self.write_reg(d.reg, trunc(v, 4) if s.vector_index == 0 else ir.hi_half(v), a, insn)]

    @staticmethod
    def _neon_imm(dts, imm, size):
        """NEON 즉값 이동(vmov.iN)이 만드는 64비트 값. 모르는 형식은 None."""
        dt = dts[0] if dts else ""
        imm &= 0xFFFFFFFFFFFFFFFF
        if dt in ("i32", "s32", "u32"):
            return (imm & 0xFFFFFFFF) | ((imm & 0xFFFFFFFF) << 32)
        if dt in ("i64", "s64", "u64"):
            return imm
        if dt in ("i16", "s16", "u16"):
            v = imm & 0xFFFF
            return v | v << 16 | v << 32 | v << 48
        if dt in ("i8", "s8", "u8"):
            return (imm & 0xFF) * 0x0101010101010101
        return None

    # ---- vcvt ---------------------------------------------------------------------------
    def _vcvt(self, insn, head, dts, ops, a):
        n = len(ops)
        if len(dts) != 2 or n not in (2, 3):
            return None
        td, ts = dts
        d, s = ops[0], ops[1]
        mode = "rint" if head == "vcvtr" else (CVT_MODE.get(head[4]) if len(head) == 5 else None)
        fd, fs = td in ("f32", "f64"), ts in ("f32", "f64")
        if fd and fs:                                                         # float ↔ double
            sd, ss = (4 if td == "f32" else 8), (4 if ts == "f32" else 8)
            if sd == ss:
                return None
            return self.vw(a, d, X("f2f", (self.vr(s),), sd))
        scale = None
        if n == 3:
            if ops[2].type != OP_IMM or d.reg != s.reg:
                return None
            scale = ops[2].imm
        if fd and ts in ("s32", "u32"):                                       # 정수(s 레지스터에 든 비트) → 실수
            sd = 4 if td == "f32" else 8
            r = X("i2f", (self.vr(s),), sd, "s" if ts == "s32" else "u")
            if scale:
                r = X("fdiv", (r, C(f_bits(float(2 ** scale), sd), sd)), sd)
            return self.vw(a, d, r)
        if fs and td in ("s32", "u32"):                                       # 실수 → 정수(기본은 0 쪽으로 자름)
            ss = 4 if ts == "f32" else 8
            f = self.vr(s)
            if scale:
                f = X("fmul", (f, C(f_bits(float(2 ** scale), ss), ss)), ss)
            if mode:
                f = X("fround", (f,), ss, mode)
            return self.vw(a, d, X("f2i", (f,), 4, "s" if td == "s32" else "u"))
        return None

    # ---- 메모리 --------------------------------------------------------------------------
    def _vldr_vstr(self, insn, head, ops, a):
        r, m = ops[0], ops[1]
        if not self.is_vreg(r) or m.type != OP_MEM:
            return None
        kind, idx = self.vinfo(r.reg)
        sz = 4 if kind == "s" else 8
        addr = self.mem_addr(m, insn)
        if head == "vstr":
            return [S("asg", a, mem(addr, sz), self.vread(kind, idx))]
        base_pc = bool(m.mem.base) and self.reg_name(m.mem.base) == "pc"
        val = self.load_literal(addr, sz) if base_pc and not m.mem.index else self.load_mem(addr, sz)
        return self.vwrite(a, kind, idx, val)

    def _vblock(self, insn, head, ops, a):
        out = []
        if head in ("vpush", "vpop"):
            base, regs, wb, load, dec = "sp", ops, True, head == "vpop", head == "vpush"
        else:
            if not ops or ops[0].type != OP_REG or self.is_vreg(ops[0]):
                return None
            base, regs = self.reg_name(ops[0].reg), ops[1:]
            load = head.startswith("vld")
            dec = head.endswith("db")
            wb = bool(insn.writeback)
        regs = [o for o in regs if self.is_vreg(o)]
        if not regs:
            return None
        sizes = [4 if self.vinfo(o.reg)[0] == "s" else 8 for o in regs]
        total = sum(sizes)
        b = loc(base, 4)
        if dec:                                                               # 먼저 포인터를 내리고 그 기준으로 저장(스택 분석이 읽기 쉬운 꼴)
            out.append(S("asg", a, b, binop("add", b, C(-total, 4), 4)))
            ref, wb = b, False
        else:
            ref = b
        off = 0
        for o, sz in zip(regs, sizes):
            ea = binop("add", ref, C(off, 4), 4) if off else ref
            if load:
                out += self.vw(a, o, self.load_mem(ea, sz))
            else:
                out.append(S("asg", a, mem(ea, sz), self.vr(o)))
            off += sz
        if wb:
            out.append(S("asg", a, b, binop("add", b, C(total, 4), 4)))
        return out
