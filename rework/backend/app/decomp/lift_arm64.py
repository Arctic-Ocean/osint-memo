"""ARM64(AArch64) → IR 리프터(Capstone 상세 정보 사용).

x0–x30 은 전체 폭(8바이트) 위치 'x0'… 로 두고, w 레지스터 읽기는 trunc, 쓰기는 zext 로 푼다(32비트 쓰기는 윗부분을 0 으로 만든다).
xzr/wzr 는 읽으면 0, 쓰면 버린다. 조건 플래그는 가짜 위치 'F' 에 `fl(...)` 로 담고, 조건 실행(csel·b.cond·ccmp…)은 ARM 조건 이름
(eq ne hs lo mi pl vs vc hi ls ge lt gt le)을 그대로 `cc` 에 실어 ir.reduce_cc 가 푼다.
"""
from __future__ import annotations

import re

from capstone import arm64 as c64
from capstone.arm64 import (ARM64_OP_REG as OP_REG, ARM64_OP_IMM as OP_IMM, ARM64_OP_MEM as OP_MEM)

from . import ir
from .ir import C, X, S, loc, mem, add, sub, binop, unop, zext, sext, trunc, cmp, ite, flags, cc, mask

FLAGS = "F"
ARM_CC = {1: "eq", 2: "ne", 3: "hs", 4: "lo", 5: "mi", 6: "pl", 7: "vs", 8: "vc", 9: "hi", 10: "ls", 11: "ge", 12: "lt",
          13: "gt", 14: "le", 15: "al", 16: "nv"}
EXT = {c64.ARM64_EXT_UXTB: (1, False), c64.ARM64_EXT_UXTH: (2, False), c64.ARM64_EXT_UXTW: (4, False), c64.ARM64_EXT_UXTX: (8, False),
       c64.ARM64_EXT_SXTB: (1, True), c64.ARM64_EXT_SXTH: (2, True), c64.ARM64_EXT_SXTW: (4, True), c64.ARM64_EXT_SXTX: (8, True)}


class Unsupported(Exception):
    pass


class A64Lifter:
    full = 8
    SP = "sp"

    def __init__(self, cs, bits, read=None):
        self.cs = cs
        self.bits = bits
        self.read = read                 # 코드 안의 리터럴 풀을 읽는 함수(va, 크기) → bytes
        self.div_state = None
        self._tmp = 0

    # ---- 레지스터 -----------------------------------------------------------------
    def tmp(self, sz: int) -> X:
        self._tmp += 1
        return loc(f"t{self._tmp}", sz)

    def reg_info(self, rid):
        """Capstone 레지스터 번호 → (정규 위치 이름 | None(영 레지스터), 폭)"""
        n = self.cs.reg_name(rid)
        if n is None:
            raise Unsupported("reg")
        if n in ("xzr", "wzr"):
            return None, 8 if n == "xzr" else 4
        if n in ("sp", "wsp"):
            return "sp", 8 if n == "sp" else 4
        if n == "fp":
            return "x29", 8
        if n == "lr":
            return "x30", 8
        m = re.match(r"^([xw])(\d+)$", n)
        if m:
            return f"x{m.group(2)}", 8 if m.group(1) == "x" else 4
        raise Unsupported(n)

    def read_reg(self, rid) -> X:
        name, sz = self.reg_info(rid)
        if name is None:
            return C(0, sz)
        v = loc(name, 8)
        return trunc(v, 4) if sz == 4 else v

    def write_reg(self, rid, val: X, addr: int):
        name, sz = self.reg_info(rid)
        if name is None:
            return None
        if sz == 4:
            val = zext(trunc(val, 4) if val.sz > 4 else val, 8)
        return S("asg", addr, loc(name, 8), val)

    def reg_sz(self, rid) -> int:
        return self.reg_info(rid)[1]

    # ---- 피연산자 -----------------------------------------------------------------
    def _shift(self, v: X, kind: int, amt: int) -> X:
        if amt == 0:
            return v
        n = v.sz
        if kind == c64.ARM64_SFT_LSL:
            return binop("shl", v, C(amt, 1), n)
        if kind == c64.ARM64_SFT_LSR:
            return binop("shr", v, C(amt, 1), n)
        if kind == c64.ARM64_SFT_ASR:
            return binop("sar", v, C(amt, 1), n)
        if kind == c64.ARM64_SFT_ROR:
            return binop("ror", v, C(amt, 1), n)
        raise Unsupported("shift")

    def opv(self, op, sz: int) -> X:
        """데이터 처리 명령의 피연산자 값(레지스터는 시프트·확장 적용, 즉값은 시프트 반영)을 sz 바이트로."""
        if op.type == OP_IMM:
            v = op.imm
            if op.shift.type == c64.ARM64_SFT_LSL and op.shift.value:
                v <<= op.shift.value
            return C(v, sz)
        if op.type == OP_REG:
            v = self.read_reg(op.reg)
            if op.ext:
                w, signed = EXT[op.ext]
                v = trunc(v, w) if v.sz > w else v
                v = sext(v, sz) if signed else zext(v, sz)
            elif v.sz != sz:
                v = zext(v, sz) if v.sz < sz else trunc(v, sz)
            if op.shift.type and op.shift.value:
                v = self._shift(v, op.shift.type, op.shift.value)
            return v
        raise Unsupported("operand")

    def mem_addr(self, op, insn=None) -> X:
        m = op.mem
        a = None
        if m.base:
            a = self.read_reg(m.base)
            if a.sz != 8:
                a = zext(a, 8)
        if m.index:
            ix = self.read_reg(m.index)
            if op.ext:
                w, signed = EXT[op.ext]
                ix = trunc(ix, w) if ix.sz > w else ix
                ix = sext(ix, 8) if signed else zext(ix, 8)
            elif ix.sz != 8:
                ix = zext(ix, 8)
            if op.shift.type == c64.ARM64_SFT_LSL and op.shift.value:
                ix = binop("mul", ix, C(1 << op.shift.value, 8), 8)
            a = ix if a is None else binop("add", a, ix, 8)
        if m.disp:
            a = C(m.disp, 8) if a is None else binop("add", a, C(m.disp, 8), 8)
        return a if a is not None else C(0, 8)

    @staticmethod
    def is_canary(addr: X) -> bool:
        return addr.op == "add" and addr.args[0].op == "seg" and addr.args[1].op == "c" and addr.args[1].aux == 0x28

    # ---- 진입점 -------------------------------------------------------------------
    def lift(self, insn) -> list:
        try:
            return self._lift(insn)
        except (Unsupported, KeyError, IndexError, ValueError):
            return [self.unknown(insn)]

    def unknown(self, insn) -> S:
        uses, defs = set(), set()

        def canon(rid):
            try:
                n, _ = self.reg_info(rid)
                return n
            except Unsupported:
                return None

        for rid in list(insn.regs_read):
            c = canon(rid)
            if c:
                uses.add(c)
        for rid in list(insn.regs_write):
            c = canon(rid)
            if c:
                defs.add(c)
        for op in insn.operands:
            if op.type == OP_REG:
                c = canon(op.reg)
                if c:
                    if op.access & 1:
                        uses.add(c)
                    if op.access & 2:
                        defs.add(c)
            elif op.type == OP_MEM:
                for rid in (op.mem.base, op.mem.index):
                    if rid:
                        c = canon(rid)
                        if c:
                            uses.add(c)
        text = f"{insn.mnemonic} {insn.op_str}".strip()
        return S("unk", insn.address, extra={"text": text, "uses": sorted(uses), "defs": sorted(defs),
                                            "writes_flags": bool(insn.update_flags)})

    # ---- 본체 ---------------------------------------------------------------------
    def _lift(self, insn) -> list:
        m = insn.mnemonic
        ops = insn.operands
        a = insn.address
        out: list = []

        def emit(s):
            if s is not None:
                out.append(s)

        def setf(fl):
            out.append(S("asg", a, loc(FLAGS, 1), fl))

        if m in ("nop", "hint", "dmb", "dsb", "isb", "yield", "wfe", "wfi", "sev", "sevl", "prfm", "csdb", "bti", "paciasp", "autiasp",
                 "pacibsp", "autibsp", "xpaclri"):
            return []
        if m in ("brk", "udf", "hlt"):
            return [S("noret", a)]
        if m == "ret":
            return [S("ret", a)]
        if m == "b":
            return [S("jmp", a, t=ops[0].imm)]
        if m.startswith("b."):
            code = ARM_CC[insn.cc]
            return [S("cj", a, src=cc(code, loc(FLAGS, 1)), t=ops[0].imm, f=a + insn.size)]
        if m == "bl":
            return [self._call(insn, C(ops[0].imm, 8))]
        if m == "blr":
            return [self._call(insn, self.read_reg(ops[0].reg))]
        if m == "br":
            return [S("ij", a, src=self.read_reg(ops[0].reg))]
        if m in ("cbz", "cbnz"):
            v = self.read_reg(ops[0].reg)
            return [S("cj", a, src=cmp("eq" if m == "cbz" else "ne", v, C(0, v.sz)), t=ops[1].imm, f=a + insn.size)]
        if m in ("tbz", "tbnz"):
            v = self.read_reg(ops[0].reg)
            bit = ops[1].imm
            t = binop("and", binop("shr", v, C(bit, 1), v.sz), C(1, v.sz), v.sz)
            return [S("cj", a, src=cmp("eq" if m == "tbz" else "ne", t, C(0, v.sz)), t=ops[2].imm, f=a + insn.size)]

        # ---- 이동·상수 ----
        if m in ("mov", "movz", "movn"):
            dsz = self.reg_sz(ops[0].reg)
            if ops[1].type == OP_IMM:
                v = ops[1].imm << (ops[1].shift.value if ops[1].shift.type == c64.ARM64_SFT_LSL else 0)
                if m == "movn":
                    v = ~v
                src = C(v, dsz)
            else:
                src = self.opv(ops[1], dsz)
            return [self.write_reg(ops[0].reg, src, a)]
        if m == "movk":
            dsz = self.reg_sz(ops[0].reg)
            sh = ops[1].shift.value if ops[1].shift.type == c64.ARM64_SFT_LSL else 0
            old = self.read_reg(ops[0].reg)
            return [self.write_reg(ops[0].reg, binop("or", binop("and", old, C(~(0xFFFF << sh), dsz), dsz), C(ops[1].imm << sh, dsz), dsz), a)]
        if m in ("adrp", "adr"):
            return [self.write_reg(ops[0].reg, C(ops[1].imm, 8), a)]

        # ---- 산술·논리 ----
        if m in ("add", "sub", "adds", "subs", "cmp", "cmn", "neg", "negs", "and", "orr", "eor", "bic", "orn", "eon", "ands", "bics",
                 "tst", "mvn"):
            return self._alu(insn, m, ops, a)
        if m in ("adc", "sbc", "adcs", "sbcs", "ngc", "ngcs"):
            raise Unsupported("adc")
        if m in ("lsl", "lsr", "asr", "ror", "lslv", "lsrv", "asrv", "rorv"):
            dsz = self.reg_sz(ops[0].reg)
            op = {"lsl": "shl", "lsr": "shr", "asr": "sar", "ror": "ror", "lslv": "shl", "lsrv": "shr", "asrv": "sar", "rorv": "ror"}[m]
            x = self.opv(ops[1], dsz)
            if ops[2].type == OP_IMM:
                cnt = C(ops[2].imm & (63 if dsz == 8 else 31), 1)
            else:
                cnt = binop("and", trunc(self.opv(ops[2], dsz), 1) if dsz > 1 else self.opv(ops[2], dsz), C(63 if dsz == 8 else 31, 1), 1)
            return [self.write_reg(ops[0].reg, binop(op, x, cnt, dsz), a)]
        if m in ("mul", "mneg", "madd", "msub", "smull", "umull", "smaddl", "umaddl", "smsubl", "umsubl", "smnegl", "umnegl"):
            dsz = self.reg_sz(ops[0].reg)
            if m in ("smull", "umull", "smaddl", "umaddl", "smsubl", "umsubl", "smnegl", "umnegl"):
                ex = sext if m.startswith("s") else zext
                n, mm = ex(self.read_reg(ops[1].reg), 8), ex(self.read_reg(ops[2].reg), 8)
                prod = binop("mul", n, mm, 8)
                if m in ("smull", "umull"):
                    r = prod
                elif m in ("smnegl", "umnegl"):
                    r = unop("neg", prod)
                elif m in ("smaddl", "umaddl"):
                    r = binop("add", self.read_reg(ops[3].reg), prod, 8)
                else:
                    r = binop("sub", self.read_reg(ops[3].reg), prod, 8)
                return [self.write_reg(ops[0].reg, r, a)]
            n, mm = self.opv(ops[1], dsz), self.opv(ops[2], dsz)
            prod = binop("mul", n, mm, dsz)
            if m == "mul":
                r = prod
            elif m == "mneg":
                r = unop("neg", prod)
            elif m == "madd":
                r = binop("add", self.opv(ops[3], dsz), prod, dsz)
            else:
                r = binop("sub", self.opv(ops[3], dsz), prod, dsz)
            return [self.write_reg(ops[0].reg, r, a)]
        if m in ("smulh", "umulh"):
            return [self.write_reg(ops[0].reg, binop("mulhs" if m == "smulh" else "mulhu", self.read_reg(ops[1].reg), self.read_reg(ops[2].reg), 8), a)]
        if m in ("sdiv", "udiv"):
            dsz = self.reg_sz(ops[0].reg)
            return [self.write_reg(ops[0].reg, binop("sdiv" if m == "sdiv" else "udiv", self.opv(ops[1], dsz), self.opv(ops[2], dsz), dsz), a)]
        if m in ("sxtb", "sxth", "sxtw", "uxtb", "uxth", "uxtw"):
            dsz = self.reg_sz(ops[0].reg)
            w = {"b": 1, "h": 2, "w": 4}[m[-1]]
            src = trunc(self.read_reg(ops[1].reg), w) if self.read_reg(ops[1].reg).sz > w else self.read_reg(ops[1].reg)
            return [self.write_reg(ops[0].reg, sext(src, dsz) if m[0] == "s" else zext(src, dsz), a)]
        if m in ("ubfx", "sbfx", "ubfiz", "sbfiz", "bfi", "bfxil", "bfc"):
            return self._bitfield(insn, m, ops, a)
        if m == "extr":
            dsz = self.reg_sz(ops[0].reg)
            lsb = ops[3].imm
            hi, lo = self.opv(ops[1], dsz), self.opv(ops[2], dsz)
            if lsb == 0:
                return [self.write_reg(ops[0].reg, lo, a)]
            return [self.write_reg(ops[0].reg, binop("or", binop("shl", hi, C(8 * dsz - lsb, 1), dsz), binop("shr", lo, C(lsb, 1), dsz), dsz), a)]
        if m in ("clz", "rbit", "rev", "rev16", "rev32"):
            dsz = self.reg_sz(ops[0].reg)
            return [self.write_reg(ops[0].reg, X("bi", (self.read_reg(ops[1].reg),), dsz, m), a)]

        # ---- 조건 선택 ----
        if m in ("csel", "csinc", "csinv", "csneg", "cset", "csetm", "cinc", "cinv", "cneg"):
            dsz = self.reg_sz(ops[0].reg)
            code = ARM_CC[insn.cc]
            cond = cc(code, loc(FLAGS, 1))
            if m == "cset":
                r = zext(cond, dsz)
            elif m == "csetm":
                r = ite(cond, C(-1, dsz), C(0, dsz))
            elif m in ("cinc", "cinv", "cneg"):
                x = self.opv(ops[1], dsz)
                alt = binop("add", x, C(1, dsz), dsz) if m == "cinc" else unop("not", x) if m == "cinv" else unop("neg", x)
                r = ite(cond, alt, x)
            else:
                n, mm = self.opv(ops[1], dsz), self.opv(ops[2], dsz)
                alt = mm if m == "csel" else binop("add", mm, C(1, dsz), dsz) if m == "csinc" else unop("not", mm) if m == "csinv" else unop("neg", mm)
                r = ite(cond, n, alt)
            return [self.write_reg(ops[0].reg, r, a)]
        if m in ("ccmp", "ccmn"):
            dsz = self.reg_sz(ops[0].reg)
            code = ARM_CC[insn.cc]
            A, B = self.read_reg(ops[0].reg), self.opv(ops[1], dsz)
            tmpl = flags("sub", (A, B), dsz) if m == "ccmp" else flags("add", (A, B, binop("add", A, B, dsz)), dsz)
            setf(ite(cc(code, loc(FLAGS, 1)), tmpl, flags("nzcv", (C(ops[2].imm, 1),), 1)))
            return out

        # ---- 메모리 ----
        if m in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrsw", "ldur", "ldurb", "ldurh", "ldursb", "ldursh", "ldursw", "str", "strb",
                 "strh", "stur", "sturb", "sturh", "ldp", "stp", "ldpsw"):
            return self._memop(insn, m, ops, a)
        raise Unsupported(m)

    # ---- 보조 ---------------------------------------------------------------------
    def _call(self, insn, target: X) -> S:
        return S("call", insn.address, dst=loc("x0", 8), src=X("call", (target,), 8, {"args": None}))

    def _alu(self, insn, m, ops, a):
        out = []
        sets_flags = m in ("adds", "subs", "cmp", "cmn", "negs", "ands", "tst", "bics")
        no_dest = m in ("cmp", "cmn", "tst")
        i0 = 0 if no_dest else 1
        d = None if no_dest else ops[0].reg
        dsz = self.reg_sz(d) if d is not None else self.reg_sz(ops[0].reg)
        if m in ("neg", "negs"):
            A, B = C(0, dsz), self.opv(ops[1], dsz)
            kind = "sub"
        elif m == "mvn":
            return [self.write_reg(d, unop("not", self.opv(ops[1], dsz)), a)]
        else:
            idx = 0 if no_dest else 1
            A, B = self.opv(ops[idx], dsz), self.opv(ops[idx + 1], dsz)
            kind = {"add": "add", "adds": "add", "cmn": "add", "sub": "sub", "subs": "sub", "cmp": "sub", "and": "and", "ands": "and",
                    "tst": "and", "orr": "or", "eor": "xor", "bic": "and", "bics": "and", "orn": "or", "eon": "xor"}[m]
            if m in ("bic", "bics", "orn", "eon"):
                B = unop("not", B)
        # 대상 레지스터를 피연산자가 읽는 경우 연산 전 값을 임시 변수에 잡는다(플래그가 옛 값을 가리켜야 한다)
        if sets_flags and not no_dest:
            ta, tb = self.tmp(dsz), self.tmp(dsz)
            out.append(S("asg", a, ta, A))
            out.append(S("asg", a, tb, B))
            A, B = ta, tb
        if kind in ("add", "sub"):
            R = binop(kind, A, B, dsz)
        else:
            R = binop(kind, A, B, dsz)
        if not no_dest:
            w = self.write_reg(d, R, a)
            if w is not None:
                out.append(w)
        if sets_flags:
            newv = self.read_reg(d) if (not no_dest and self.reg_info(d)[0] is not None) else R
            if kind == "sub":
                out.append(S("asg", a, loc(FLAGS, 1), flags("sub", (A, B) if no_dest else (A, B, newv), dsz)))
            elif kind == "add":
                out.append(S("asg", a, loc(FLAGS, 1), flags("add", (A, B, newv if not no_dest else R), dsz)))
            else:
                out.append(S("asg", a, loc(FLAGS, 1), flags("logic", (newv if not no_dest else R,), dsz)))
        return out

    def _bitfield(self, insn, m, ops, a):
        dsz = self.reg_sz(ops[0].reg)
        bits = 8 * dsz
        if m == "bfc":
            lsb, width = ops[1].imm, ops[2].imm
            old = self.read_reg(ops[0].reg)
            return [self.write_reg(ops[0].reg, binop("and", old, C(~(((1 << width) - 1) << lsb), dsz), dsz), a)]
        src = self.opv(ops[1], dsz)
        lsb, width = ops[2].imm, ops[3].imm
        fmask = (1 << width) - 1
        if m == "ubfx":
            r = binop("and", binop("shr", src, C(lsb, 1), dsz), C(fmask, dsz), dsz)
        elif m == "sbfx":
            r = binop("sar", binop("shl", src, C(bits - lsb - width, 1), dsz), C(bits - width, 1), dsz)
        elif m == "ubfiz":
            r = binop("shl", binop("and", src, C(fmask, dsz), dsz), C(lsb, 1), dsz)
        elif m == "sbfiz":
            r = binop("shl", binop("sar", binop("shl", src, C(bits - width, 1), dsz), C(bits - width, 1), dsz), C(lsb, 1), dsz)
        elif m == "bfi":
            old = self.read_reg(ops[0].reg)
            r = binop("or", binop("and", old, C(~(fmask << lsb), dsz), dsz), binop("shl", binop("and", src, C(fmask, dsz), dsz), C(lsb, 1), dsz), dsz)
        else:                                                              # bfxil
            old = self.read_reg(ops[0].reg)
            r = binop("or", binop("and", old, C(~fmask, dsz), dsz), binop("and", binop("shr", src, C(lsb, 1), dsz), C(fmask, dsz), dsz), dsz)
        return [self.write_reg(ops[0].reg, r, a)]

    def _memop(self, insn, m, ops, a):
        out = []
        pair = m in ("ldp", "stp", "ldpsw")
        mem_i = 2 if pair else 1
        mop = ops[mem_i]
        load = m.startswith("ld")
        # 접근 폭·확장
        base = m.replace("ur", "r") if m.startswith(("ldur", "stur")) else m
        if base in ("ldrb", "strb"):
            msz, sx = 1, False
        elif base in ("ldrh", "strh"):
            msz, sx = 2, False
        elif base == "ldrsb":
            msz, sx = 1, True
        elif base == "ldrsh":
            msz, sx = 2, True
        elif base == "ldrsw" or m == "ldpsw":
            msz, sx = 4, True
        else:
            msz, sx = self.reg_sz(ops[0].reg), False
        if mop.type == OP_IMM and not pair:                                # ldr x0, <리터럴 풀>
            if self.read is None or not load:
                raise Unsupported("literal")
            raw = self.read(mop.imm, msz)
            if len(raw) < msz:
                raise Unsupported("literal")
            v = int.from_bytes(raw, "little")
            val = C(v, msz)
            dsz = self.reg_sz(ops[0].reg)
            val = (sext(val, dsz) if sx else zext(val, dsz)) if msz < dsz else val
            return [self.write_reg(ops[0].reg, val, a)]
        addr = self.mem_addr(mop)
        post = len(ops) > mem_i + 1 and ops[mem_i + 1].type == OP_IMM
        regs = [ops[0].reg] + ([ops[1].reg] if pair else [])
        # 주소 레지스터를 대상이 덮어쓰는 경우를 위해 주소를 임시 변수에 잡는다
        base_name = self.reg_info(mop.mem.base)[0] if mop.mem.base else None
        clobbers = base_name is not None and any(self.reg_info(r)[0] == base_name for r in regs) if load else False
        if clobbers or (insn.writeback and not post and load):
            t = self.tmp(8)
            out.append(S("asg", a, t, addr))
            addr = t
        for k, rid in enumerate(regs):
            ea = addr if k == 0 else binop("add", addr, C(k * msz, 8), 8)
            if load:
                v = mem(ea, msz)
                dsz = self.reg_sz(rid)
                if msz < dsz:
                    v = sext(v, dsz) if sx else zext(v, dsz)
                w = self.write_reg(rid, v, a)
                if w is not None:
                    out.append(w)
            else:
                val = self.read_reg(rid)
                if val.sz > msz:
                    val = trunc(val, msz)
                out.append(S("asg", a, mem(ea, msz), val))
        if insn.writeback:
            bname = self.reg_info(mop.mem.base)[0]
            if post:
                out.append(S("asg", a, loc(bname, 8), binop("add", loc(bname, 8), C(ops[mem_i + 1].imm, 8), 8)))
            else:
                out.append(S("asg", a, loc(bname, 8), binop("add", loc(bname, 8), C(mop.mem.disp, 8), 8)))
        return out
