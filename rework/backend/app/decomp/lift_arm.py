"""ARM 32비트(ARM·Thumb-2) → IR 리프터(Capstone 상세 정보 사용).

r0–r12·sp·lr 는 4바이트 위치로 두고, pc 는 읽으면 명령 주소 + 8(ARM) 또는 + 4(Thumb) 상수다. 조건 플래그는 가짜 위치 'F' 에 `fl(...)` 로 담아 두었다가
조건 분기·조건 실행이 ARM 조건 이름(eq ne hs lo mi pl vs vc hi ls ge lt gt le)으로 읽을 때 ir.reduce_cc 가 비교식으로 푼다.
조건 실행(`moveq`·`bxne`·Thumb IT 블록)은 한 명령을 두 조각으로 쪼개 `if (조건) { 본문 }` 이 되게 한다(func.py 의 'split' 규약: 머리 조각은 명령 주소,
본문 조각은 명령 주소 + 1).
"""
from __future__ import annotations

import re

import capstone.arm as carm
from capstone.arm import ARM_OP_REG as OP_REG, ARM_OP_IMM as OP_IMM, ARM_OP_MEM as OP_MEM

from . import ir
from .ir import C, X, S, loc, mem, add, sub, binop, unop, zext, sext, trunc, cmp, ite, flags, cc, mask

FLAGS = "F"
ARM_CC = {1: "eq", 2: "ne", 3: "hs", 4: "lo", 5: "mi", 6: "pl", 7: "vs", 8: "vc", 9: "hi", 10: "ls", 11: "ge", 12: "lt", 13: "gt", 14: "le"}
CC_SUFFIX = {1: ("eq",), 2: ("ne",), 3: ("hs", "cs"), 4: ("lo", "cc"), 5: ("mi",), 6: ("pl",), 7: ("vs",), 8: ("vc",), 9: ("hi",), 10: ("ls",),
             11: ("ge",), 12: ("lt",), 13: ("gt",), 14: ("le",)}
ALIAS = {"ip": "r12", "fp": "r11", "sb": "r9", "sl": "r10", "r13": "sp", "r14": "lr", "r15": "pc"}
S_OPS = {"add", "sub", "rsb", "and", "orr", "eor", "bic", "orn", "mov", "mvn", "lsl", "lsr", "asr", "ror", "mul", "mla", "adc", "sbc", "rsc", "neg",
         "umull", "smull", "umlal", "smlal"}
SFT_ASR, SFT_LSL, SFT_LSR, SFT_ROR, SFT_RRX = 1, 2, 3, 4, 5


class Unsupported(Exception):
    pass


class _ImmOp:
    """합쳐진 회전 즉값 피연산자(Capstone 피연산자 모양을 흉내 낸다)."""
    type = OP_IMM

    class shift:
        type = 0
        value = 0

    def __init__(self, imm):
        self.imm = imm


class A32Lifter:
    full = 4
    SP = "sp"

    def __init__(self, cs, thumb: bool, read=None, is_const=None):
        self.cs = cs
        self.thumb = thumb
        self.bits = 32
        self.read = read
        self.is_const = is_const
        self.div_state = None
        self.wide = False                      # 자리올림 연쇄(adc·sbc)·64비트 곱셈누적이 있어 32비트 위치만으로는 값을 다 나타내지 못함
        self._tmp = 0

    # ---- 레지스터 -----------------------------------------------------------------
    def tmp(self, sz: int = 4) -> X:
        self._tmp += 1
        return loc(f"t{self._tmp}", sz)

    def reg_name(self, rid) -> str:
        n = self.cs.reg_name(rid)
        if n is None:
            raise Unsupported("reg")
        n = ALIAS.get(n, n)
        if re.match(r"^r\d+$", n) or n in ("sp", "lr", "pc"):
            return n
        raise Unsupported(n)

    def read_reg(self, rid, insn) -> X:
        n = self.reg_name(rid)
        if n == "pc":
            return C((insn.address + (4 if self.thumb else 8)) & 0xFFFFFFFF, 4)
        return loc(n, 4)

    def write_reg(self, rid, val: X, addr: int, insn=None) -> S:
        n = self.reg_name(rid)
        if n == "pc":
            raise Unsupported("write pc")
        return S("asg", addr, loc(n, 4), val if val.sz == 4 else (trunc(val, 4) if val.sz > 4 else zext(val, 4)))

    def load_mem(self, a: X, sz: int) -> X:
        if a.op == "c" and self.is_const is not None and self.read is not None and sz in (1, 2, 4) and self.is_const(a.aux):
            data = self.read(a.aux, sz)
            if data and len(data) == sz:
                return C(int.from_bytes(data, "little"), sz)
        return mem(a, sz)

    def load_literal(self, a: X, sz: int) -> X:
        """`ldr r0, [pc, #off]` 의 리터럴 풀 값: 풀이 코드 구역 안에 있어도 바뀌지 않는 상수이므로 파일에서 읽어 접는다."""
        if a.op == "c" and self.read is not None and sz in (1, 2, 4):
            data = self.read(a.aux, sz)
            if data and len(data) == sz:
                return C(int.from_bytes(data, "little"), sz)
        return mem(a, sz)

    # ---- 피연산자 -----------------------------------------------------------------
    def _shift(self, v: X, kind: int, amt) -> X:
        if kind == SFT_RRX:
            return binop("or", binop("shr", v, C(1, 1), 4), binop("shl", zext(cc("hs", loc(FLAGS, 1)), 4), C(31, 1), 4), 4)
        if isinstance(amt, X):                                        # 레지스터로 시프트: 아래 바이트만 쓴다
            cnt = trunc(amt, 1)
        else:
            if amt == 0:
                return v
            cnt = C(amt, 1)
        op = {SFT_LSL: "shl", SFT_LSR: "shr", SFT_ASR: "sar", SFT_ROR: "ror"}.get(kind)
        if op is None:
            raise Unsupported("shift")
        return binop(op, v, cnt, 4)

    def opv(self, op, insn) -> X:
        """데이터 처리 명령의 피연산자 값(레지스터는 시프트 적용)."""
        if op.type == OP_IMM:
            return C(op.imm & 0xFFFFFFFF, 4)
        if op.type == OP_REG:
            v = self.read_reg(op.reg, insn)
            st = op.shift.type
            if st:
                if st >= 6:                                         # ASR_REG … RRX_REG: 시프트 양이 레지스터
                    kind = {6: SFT_ASR, 7: SFT_LSL, 8: SFT_LSR, 9: SFT_ROR, 10: SFT_RRX}[st]
                    v = self._shift(v, kind, self.read_reg(op.shift.value, insn))
                else:
                    v = self._shift(v, st, op.shift.value)
            return v
        raise Unsupported("operand")

    def mem_addr(self, op, insn) -> X:
        m = op.mem
        if m.base and self.reg_name(m.base) == "pc":                     # 리터럴: Align(pc, 4) + 변위
            pc = (insn.address + (4 if self.thumb else 8)) & ~3 & 0xFFFFFFFF
            a = C(pc, 4)
        else:
            a = self.read_reg(m.base, insn) if m.base else None
        if m.index:
            ix = self.read_reg(m.index, insn)
            if op.shift.type:                                         # [rn, rm, lsl #2] 의 시프트는 피연산자 쪽에 붙는다
                ix = self._shift(ix, op.shift.type, op.shift.value)
            if op.subtracted:
                ix = unop("neg", ix)
            a = ix if a is None else binop("add", a, ix, 4)
        if m.disp:
            a = C(m.disp & 0xFFFFFFFF, 4) if a is None else binop("add", a, C(m.disp, 4), 4)
        return a if a is not None else C(0, 4)

    # ---- 진입점 -------------------------------------------------------------------
    def lift(self, insn) -> list:
        """조건 없는 명령 하나(또는 자기 조건을 직접 다루는 분기)를 문장으로. 조건 실행은 lift_function 이 묶어서 처리한다."""
        if self._is_data(insn):
            return self._data_stmt(insn)
        if re.fullmatch(r"it[te]{0,3}", insn.mnemonic):
            return []                                                # IT 는 뒤따르는 명령들의 cc 로 이미 펼쳐져 있다
        try:
            return self._lift(insn, self.base_mnemonic(insn))
        except (Unsupported, KeyError, IndexError, ValueError):
            return [self.unknown(insn)]

    def unknown(self, insn) -> S:
        uses, defs = set(), set()

        def canon(rid):
            try:
                n = self.reg_name(rid)
            except Unsupported:
                return None
            return None if n == "pc" else n

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
        return S("unk", insn.address, extra={"text": text, "uses": sorted(uses), "defs": sorted(defs), "writes_flags": bool(insn.update_flags)})

    # ---- 조건 실행 -----------------------------------------------------------------
    @staticmethod
    def _inv(code: int) -> int:
        return code + 1 if code & 1 else code - 1

    def _is_cond(self, insn, base: str) -> bool:
        return insn.cc not in (0, 15) and base not in ("b", "cbz", "cbnz") and not re.fullmatch(r"it[te]{0,3}", insn.mnemonic)

    def _sets_flags(self, insn, base: str) -> bool:
        return bool(insn.update_flags) or base in ("cmp", "cmn", "tst", "teq")

    @staticmethod
    def _is_data(insn) -> bool:
        """Capstone SKIPDATA 가 만든 의사 명령(해석할 수 없는 바이트)인가."""
        try:
            insn.cc
            return False
        except Exception:                                           # noqa: BLE001
            return True

    def _data_stmt(self, insn):
        return [S("unk", insn.address, extra={"text": f"{insn.mnemonic} {insn.op_str}".strip() or ".word", "uses": [], "defs": [], "writes_flags": False})]

    def lift_function(self, insns) -> list:
        """함수 전체를 [(주소, 크기, 문장들)] 로. 같은 조건(또는 그 반대 조건)이 이어지는 명령은 하나의 `if (조건) {…} else {…}` 로 묶는다.
        머리 조각은 첫 명령의 첫 바이트(주소 A, 크기 1), 참 조각은 A+1, 거짓 조각은 첫 거짓 명령의 주소에 놓아 블록 시작 주소가 겹치지 않게 한다."""
        targets = set()
        insns = [i for i in insns]
        for ins in insns:
            if self._is_data(ins):
                continue
            if self.base_mnemonic(ins) in ("b", "cbz", "cbnz"):
                for op in ins.operands:
                    if op.type == OP_IMM:
                        targets.add(op.imm & 0xFFFFFFFF)
        out, i, n = [], 0, len(insns)
        while i < n:
            ins = insns[i]
            if self._is_data(ins):
                out.append((ins.address, ins.size, self._data_stmt(ins)))
                i += 1
                continue
            base = self.base_mnemonic(ins)
            if not self._is_cond(ins, base):
                out.append((ins.address, ins.size, self.lift(ins)))
                i += 1
                continue
            if base in ("cmp", "cmn", "tst", "teq"):                   # 조건부 비교: 플래그를 `조건 ? 새 플래그 : 옛 플래그` 로 (ccmp 와 같다)
                out.append((ins.address, ins.size, self._cond_compare(ins, base)))
                i += 1
                continue
            run, els, j = [ins], [], i + 1
            while j < n:
                nx = insns[j]
                if self._is_data(nx):
                    break
                nb = self.base_mnemonic(nx)
                prev = (els or run)[-1]
                if self._sets_flags(prev, self.base_mnemonic(prev)) or nx.address != prev.address + prev.size or nx.address in targets \
                        or not self._is_cond(nx, nb) or nb in ("cmp", "cmn", "tst", "teq"):
                    break
                if nx.cc == ins.cc and not els:
                    run.append(nx)
                elif nx.cc == self._inv(ins.cc):
                    els.append(nx)
                else:
                    break
                j += 1
            out.extend(self._cond_parts(ins.cc, run, els))
            i = j
        return out

    def _cond_compare(self, insn, base) -> list:
        a = insn.address
        try:
            plain = self._lift(insn, base)
        except (Unsupported, KeyError, IndexError, ValueError):
            return [self.unknown(insn)]
        if len(plain) != 1 or plain[0].k != "asg" or plain[0].dst.aux != FLAGS:
            return [self.unknown(insn)]
        newf = plain[0].src
        return [S("asg", a, loc(FLAGS, 1), ite(cc(ARM_CC[insn.cc], loc(FLAGS, 1)), newf, loc(FLAGS, 1)))]

    def _body(self, group) -> list:
        stmts = []
        for ins in group:
            stmts.extend(self.lift_body(ins))
        return stmts

    def lift_body(self, insn) -> list:
        try:
            return self._lift(insn, self.base_mnemonic(insn))
        except (Unsupported, KeyError, IndexError, ValueError):
            return [self.unknown(insn)]

    def _cond_parts(self, code: int, run, els) -> list:
        first, last = run[0], (els or run)[-1]
        A, B = first.address, last.address + last.size
        cond = cc(ARM_CC[code], loc(FLAGS, 1))
        e0 = els[0].address if els else None
        head = (A, 1, [S("cj", A, src=cond, t=A + 1, f=e0 if els else B)])
        parts = [head]
        for start, size, group in ((A + 1, (e0 if els else B) - (A + 1), run), (e0, B - e0 if els else 0, els)):
            if not group:
                continue
            stmts = self._body(group)
            if not stmts or stmts[-1].k not in ("jmp", "ret", "ij", "noret", "cj", "sw"):
                stmts.append(S("jmp", group[-1].address, t=B))
            parts.append((start, size, stmts))
        return parts

    def base_mnemonic(self, insn) -> str:
        """조건 접미사·`.w`·`s`(플래그 갱신) 를 뗀 기본 니모닉."""
        m = insn.mnemonic
        for suf in (".w", ".n"):
            if m.endswith(suf):
                m = m[:-2]
        if insn.cc not in (0, 15):
            for suf in CC_SUFFIX.get(insn.cc, ()):
                if m.endswith(suf) and len(m) > len(suf):
                    m = m[:-len(suf)]
                    break
        if insn.update_flags and m.endswith("s") and m[:-1] in S_OPS:
            m = m[:-1]
        return m

    # ---- 본체 ---------------------------------------------------------------------
    DP = ("add", "sub", "rsb", "and", "orr", "eor", "bic", "mov", "mvn", "cmp", "cmn", "tst", "teq", "adc", "sbc", "rsc", "orn")

    @classmethod
    def _norm_ops(cls, insn, m):
        """데이터 처리 명령에서 `#imm8, #회전` 두 즉값으로 나뉘어 온 회전 즉값(링커가 만든 비정규 인코딩 등)을 값 하나로 합친다."""
        ops = list(insn.operands)
        if m not in cls.DP:
            return ops
        out = []
        for o in ops:
            if o.type == OP_IMM and out and out[-1].type == OP_IMM and not isinstance(out[-1], _ImmOp):
                v = out[-1].imm & 0xFF
                r = o.imm & 31
                out[-1] = _ImmOp(((v >> r) | (v << (32 - r))) & 0xFFFFFFFF if r else v)
            else:
                out.append(o)
        return out

    def _lift(self, insn, m) -> list:
        ops = self._norm_ops(insn, m)
        a = insn.address
        n = len(ops)
        sets_flags = bool(insn.update_flags)
        out: list = []

        def asg(rid, val):
            out.append(self.write_reg(rid, val, a, insn))

        def setf(fl):
            out.append(S("asg", a, loc(FLAGS, 1), fl))

        if m in ("nop", "dmb", "dsb", "isb", "yield", "wfe", "wfi", "sev", "sevl", "pld", "pldw", "pli", "cpsid", "cpsie", "bkpt_", "hint"):
            return []
        if m in ("udf", "bkpt", "trap"):
            return [S("noret", a)]
        # ---- 분기 ----
        if m == "b":
            if insn.cc in (0, 15):
                return [S("jmp", a, t=ops[0].imm)]
            return [S("cj", a, src=cc(ARM_CC[insn.cc], loc(FLAGS, 1)), t=ops[0].imm, f=a + insn.size)]
        if m in ("cbz", "cbnz"):
            v = self.read_reg(ops[0].reg, insn)
            return [S("cj", a, src=cmp("eq" if m == "cbz" else "ne", v, C(0, 4)), t=ops[1].imm, f=a + insn.size)]
        if m in ("bl", "blx") and ops[0].type == OP_IMM:
            return [self._call(insn, C(ops[0].imm & 0xFFFFFFFF, 4))]
        if m == "blx":
            return [self._call(insn, self.read_reg(ops[0].reg, insn))]
        if m == "bx":
            if self.reg_name(ops[0].reg) == "lr":
                return [S("ret", a)]
            return [S("ij", a, src=self.read_reg(ops[0].reg, insn))]
        # ---- 이동·상수 ----
        if m in ("mov", "movw"):
            d = ops[0]
            if self.reg_name(d.reg) == "pc":
                if n > 1 and ops[1].type == OP_REG and self.reg_name(ops[1].reg) == "lr":
                    return [S("ret", a)]
                return [S("ij", a, src=self.opv(ops[1], insn))]
            v = self.opv(ops[1], insn)
            asg(d.reg, v)
            if sets_flags:
                setf(flags("logic", (self.read_reg(d.reg, insn),), 4))
            return out
        if m == "mvn":
            v = unop("not", self.opv(ops[1], insn))
            asg(ops[0].reg, v)
            if sets_flags:
                setf(flags("logic", (self.read_reg(ops[0].reg, insn),), 4))
            return out
        if m == "movt":
            old = self.read_reg(ops[0].reg, insn)
            asg(ops[0].reg, binop("or", binop("and", old, C(0xFFFF, 4), 4), C((ops[1].imm & 0xFFFF) << 16, 4), 4))
            return out
        if m == "adr":
            return [self.write_reg(ops[0].reg, C((self._pc_aligned(insn) + ops[1].imm) & 0xFFFFFFFF, 4), a)] if ops[1].type == OP_IMM else self._raise()
        # ---- 산술·논리 ----
        if m in ("add", "sub", "rsb", "and", "orr", "eor", "bic", "orn", "adc", "sbc", "rsc", "neg"):
            return self._alu(insn, m, ops, a, sets_flags)
        if m in ("cmp", "cmn", "tst", "teq"):
            A, B = self.opv(ops[0], insn), self.opv(ops[1], insn)
            if m == "cmp":
                setf(flags("sub", (A, B), 4))
            elif m == "cmn":
                setf(flags("add", (A, B, binop("add", A, B, 4)), 4))
            elif m == "tst":
                setf(flags("logic", (binop("and", A, B, 4),), 4))
            else:
                setf(flags("logic", (binop("xor", A, B, 4),), 4))
            return out
        if m in ("lsl", "lsr", "asr", "ror", "rrx"):
            d = ops[0]
            if n == 2 and ops[1].type == OP_REG and ops[1].shift.type:       # ARM: `lsl r0, r1, #2` 는 시프트가 피연산자에 붙어 온다
                v = self.opv(ops[1], insn)
            elif m == "rrx":
                v = self._shift(self.read_reg(ops[1].reg, insn), SFT_RRX, 0)
            else:
                kind = {"lsl": SFT_LSL, "lsr": SFT_LSR, "asr": SFT_ASR, "ror": SFT_ROR}[m]
                srcop, amtop = (ops[1], ops[2]) if n == 3 else (ops[0], ops[1])      # Thumb 2주소형: lsrs r0, r1 = r0 >>= r1
                src = self.read_reg(srcop.reg, insn)
                v = self._shift(src, kind, (amtop.imm & 0xFF) if amtop.type == OP_IMM else self.read_reg(amtop.reg, insn))
            asg(d.reg, v)
            if sets_flags:
                setf(flags("logic", (self.read_reg(d.reg, insn),), 4))
            return out
        if m in ("mul", "mla", "mls"):
            if n == 2:                                                # Thumb: muls r0, r1 = r0 *= r1
                ops = [ops[0], ops[0], ops[1]]
            x, y = self.read_reg(ops[1].reg, insn), self.read_reg(ops[2].reg, insn)
            prod = binop("mul", x, y, 4)
            if m == "mla":
                prod = binop("add", prod, self.read_reg(ops[3].reg, insn), 4)
            elif m == "mls":
                prod = binop("sub", self.read_reg(ops[3].reg, insn), prod, 4)
            asg(ops[0].reg, prod)
            if sets_flags:
                setf(flags("logic", (self.read_reg(ops[0].reg, insn),), 4))
            return out
        if m in ("umull", "smull"):
            x, y = self.read_reg(ops[2].reg, insn), self.read_reg(ops[3].reg, insn)
            lo, hi = binop("mul", x, y, 4), binop("mulhu" if m == "umull" else "mulhs", x, y, 4)
            tx, ty = self.tmp(), self.tmp()
            out.append(S("asg", a, tx, x))
            out.append(S("asg", a, ty, y))
            asg(ops[0].reg, binop("mul", tx, ty, 4))
            asg(ops[1].reg, binop("mulhu" if m == "umull" else "mulhs", tx, ty, 4))
            return out
        if m in ("smmul", "smmla", "smmls"):                         # 곱의 위쪽 32비트(부호 있음)
            hi = binop("mulhs", self.read_reg(ops[1].reg, insn), self.read_reg(ops[2].reg, insn), 4)
            if m == "smmla":
                hi = binop("add", self.read_reg(ops[3].reg, insn), hi, 4)
            elif m == "smmls":
                hi = binop("sub", self.read_reg(ops[3].reg, insn), hi, 4)
            asg(ops[0].reg, hi)
            return out
        if m in ("umlal", "smlal"):                                  # RdHi:RdLo += Rn * Rm
            signed = m == "smlal"
            self.wide = True
            tl, th, tx, ty = self.tmp(), self.tmp(), self.tmp(), self.tmp()
            out.append(S("asg", a, tl, self.read_reg(ops[0].reg, insn)))
            out.append(S("asg", a, th, self.read_reg(ops[1].reg, insn)))
            out.append(S("asg", a, tx, self.read_reg(ops[2].reg, insn)))
            out.append(S("asg", a, ty, self.read_reg(ops[3].reg, insn)))
            lo = binop("add", tl, binop("mul", tx, ty, 4), 4)
            carry = zext(cmp("ult", lo, tl), 4)
            asg(ops[0].reg, lo)
            asg(ops[1].reg, binop("add", binop("add", th, binop("mulhs" if signed else "mulhu", tx, ty, 4), 4), carry, 4))
            return out
        if m.startswith(("smul", "smla")) and len(m) == 6 and m[4:] in ("bb", "bt", "tb", "tt"):
            def half(rid, top):
                v = self.read_reg(rid, insn)
                return sext(trunc(binop("shr", v, C(16, 1), 4) if top else v, 2), 4)
            prod = binop("mul", half(ops[1].reg, m[4] == "t"), half(ops[2].reg, m[5] == "t"), 4)
            if m.startswith("smla"):
                prod = binop("add", prod, self.read_reg(ops[3].reg, insn), 4)
            asg(ops[0].reg, prod)
            return out
        if m in ("sdiv", "udiv"):
            asg(ops[0].reg, binop("sdiv" if m == "sdiv" else "udiv", self.read_reg(ops[1].reg, insn), self.read_reg(ops[2].reg, insn), 4))
            return out
        if m in ("uxtab", "uxtah", "sxtab", "sxtah"):                 # rd = rn + 확장(rm ror n)
            w = 1 if m.endswith("b") else 2
            rot = ops[3].imm if n > 3 and ops[3].type == OP_IMM else (ops[2].shift.value if ops[2].shift.type == SFT_ROR else 0)
            v = trunc(self._ror(self.read_reg(ops[2].reg, insn), rot), w)
            asg(ops[0].reg, binop("add", self.read_reg(ops[1].reg, insn), zext(v, 4) if m[0] == "u" else sext(v, 4), 4))
            return out
        if m in ("tbb", "tbh"):                                       # 표 분기: pc + 2 * 표[인덱스]  (표는 명령 바로 뒤)
            pcv = (a + 4) & 0xFFFFFFFF
            mo = ops[0]
            idx = self.read_reg(mo.mem.index, insn)
            esz = 1 if m == "tbb" else 2
            if esz == 2:
                idx = binop("shl", idx, C(1, 1), 4)
            ent = zext(mem(binop("add", C(pcv, 4), idx, 4), esz), 4)
            return [S("ij", a, src=binop("add", binop("mul", ent, C(2, 4), 4), C(pcv, 4), 4))]
        if m in ("uxtb", "uxth", "sxtb", "sxth"):
            w = 1 if m.endswith("b") else 2
            src = self.opv(ops[1], insn) if n == 2 else self._ror(self.read_reg(ops[1].reg, insn), ops[2].imm if ops[2].type == OP_IMM else 0)
            v = trunc(src, w)
            asg(ops[0].reg, zext(v, 4) if m[0] == "u" else sext(v, 4))
            return out
        if m in ("ubfx", "sbfx"):
            src = self.read_reg(ops[1].reg, insn)
            lsb, width = ops[2].imm, ops[3].imm
            if m == "ubfx":
                r = binop("and", binop("shr", src, C(lsb, 1), 4), C((1 << width) - 1, 4), 4)
            else:
                r = binop("sar", binop("shl", src, C(32 - lsb - width, 1), 4), C(32 - width, 1), 4)
            asg(ops[0].reg, r)
            return out
        if m in ("bfi", "bfc"):
            old = self.read_reg(ops[0].reg, insn)
            lsb, width = (ops[1].imm, ops[2].imm) if m == "bfc" else (ops[2].imm, ops[3].imm)
            fm = (1 << width) - 1
            cleared = binop("and", old, C(~(fm << lsb) & 0xFFFFFFFF, 4), 4)
            if m == "bfc":
                asg(ops[0].reg, cleared)
            else:
                src = self.read_reg(ops[1].reg, insn)
                asg(ops[0].reg, binop("or", cleared, binop("shl", binop("and", src, C(fm, 4), 4), C(lsb, 1), 4), 4))
            return out
        if m in ("clz", "rbit", "rev", "rev16", "revsh"):
            asg(ops[0].reg, X("bi", (self.read_reg(ops[1].reg, insn),), 4, m))
            return out
        # ---- 메모리 ----
        if m in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrd", "str", "strb", "strh", "strd", "ldrt", "strt", "ldrbt", "strbt"):
            return self._memop(insn, m, ops, a)
        if m in ("ldm", "ldmia", "ldmdb", "ldmda", "ldmib", "stm", "stmia", "stmdb", "stmda", "stmib", "push", "pop", "vpush", "vpop"):
            return self._block(insn, m, ops, a)
        raise Unsupported(m)

    def _raise(self):
        raise Unsupported("operand")

    def _pc_aligned(self, insn) -> int:
        return ((insn.address + (4 if self.thumb else 8)) & ~3) & 0xFFFFFFFF

    def _ror(self, v: X, amt: int) -> X:
        return binop("ror", v, C(amt, 1), 4) if amt else v

    def _call(self, insn, target: X) -> S:
        return S("call", insn.address, dst=loc("r0", 4), src=X("call", (target,), 4, {"args": None}))

    def _alu(self, insn, m, ops, a, sets_flags):
        out = []
        d = ops[0]
        if self.reg_name(d.reg) == "pc":                              # add pc, pc, r0, lsl #2 : 분기표 점프
            if m != "add" or len(ops) != 3 or sets_flags:
                raise Unsupported("alu pc")
            return [S("ij", a, src=binop("add", self.opv(ops[1], insn), self.opv(ops[2], insn), 4))]
        if m == "neg":
            A, B, kind = C(0, 4), self.opv(ops[1], insn), "sub"
        elif len(ops) == 2:                                         # add r0, r1  (Thumb 2주소 형태)
            A, B, kind = self.read_reg(d.reg, insn), self.opv(ops[1], insn), m
        else:
            A, B, kind = self.opv(ops[1], insn), self.opv(ops[2], insn), m
        if kind == "rsb":
            A, B, kind = B, A, "sub"
        if kind == "orn":
            B, kind = unop("not", B), "orr"
        if sets_flags:                                              # 대상이 피연산자를 읽을 수 있으므로 연산 전 값을 임시 변수에 잡는다
            ta, tb = self.tmp(), self.tmp()
            out.append(S("asg", a, ta, A))
            out.append(S("asg", a, tb, B))
            A, B = ta, tb
        if kind in ("adc", "sbc", "rsc"):
            self.wide = True
            carry = zext(cc("hs", loc(FLAGS, 1)), 4)
            if kind == "adc":
                R = binop("add", binop("add", A, B, 4), carry, 4)
            elif kind == "sbc":
                R = binop("sub", binop("sub", A, B, 4), binop("sub", C(1, 4), carry, 4), 4)
            else:
                R = binop("sub", binop("sub", B, A, 4), binop("sub", C(1, 4), carry, 4), 4)
            out.append(self.write_reg(d.reg, R, a, insn))
            if sets_flags:
                out.append(S("asg", a, loc(FLAGS, 1), flags("unk", (), 1)))
            return out
        op = {"add": "add", "sub": "sub", "and": "and", "orr": "or", "eor": "xor", "bic": "and"}[kind]
        if kind == "bic":
            B = unop("not", B)
        R = binop(op, A, B, 4)
        out.append(self.write_reg(d.reg, R, a, insn))
        if sets_flags:
            newv = self.read_reg(d.reg, insn)
            if op == "sub":
                out.append(S("asg", a, loc(FLAGS, 1), flags("sub", (A, B, newv), 4)))
            elif op == "add":
                out.append(S("asg", a, loc(FLAGS, 1), flags("add", (A, B, newv), 4)))
            else:
                out.append(S("asg", a, loc(FLAGS, 1), flags("logic", (newv,), 4)))
        return out

    def _memop(self, insn, m, ops, a):
        out = []
        load = m.startswith("ld")
        dbl = m in ("ldrd", "strd")
        mem_i = 2 if dbl else 1
        mop = ops[mem_i]
        msz = 1 if m.endswith(("b", "bt", "sb")) else 2 if m.endswith(("h", "sh")) else 4
        sx = m in ("ldrsb", "ldrsh")
        regs = [ops[0].reg] + ([ops[1].reg] if dbl else [])
        if mop.type != OP_MEM:
            raise Unsupported("memop")
        post = len(ops) > mem_i + 1 and ops[mem_i + 1].type in (OP_IMM, OP_REG) and insn.writeback and getattr(insn, "post_index", False)
        addr = self.mem_addr(mop, insn)
        if post and mop.mem.base:
            addr = self.read_reg(mop.mem.base, insn)                     # 되돌려 쓰기 전 주소로 접근한다
        base_name = self.reg_name(mop.mem.base) if mop.mem.base else None
        clob = load and base_name is not None and any(self.reg_name(r) == base_name for r in regs)
        if clob or (insn.writeback and load):
            t = self.tmp()
            out.append(S("asg", a, t, addr))
            addr = t
        ret_pc = False
        literal = base_name == "pc" and not mop.mem.index
        for k, rid in enumerate(regs):
            ea = addr if k == 0 else binop("add", addr, C(4 * k, 4), 4)
            if load:
                v = self.load_literal(ea, msz) if literal else self.load_mem(ea, msz)
                if msz < 4:
                    v = sext(v, 4) if sx else zext(v, 4)
                if self.reg_name(rid) == "pc":
                    if msz == 4 and not dbl and not insn.writeback and base_name != "sp":      # ldr pc, [pc, r0, lsl #2] : 주소 표 점프
                        out.append(S("ij", a, src=v))
                        return out
                    if base_name != "sp" or not insn.writeback or msz != 4:                    # ldr pc, [sp], #4  (= pop {pc})
                        raise Unsupported("ldr pc")
                    ret_pc = True
                    out.append(S("asg", a, loc("lr", 4), v))
                    continue
                out.append(self.write_reg(rid, v, a, insn))
            else:
                val = self.read_reg(rid, insn)
                out.append(S("asg", a, mem(ea, msz), trunc(val, msz) if msz < 4 else val))
        if insn.writeback and base_name is not None:
            if post:
                off = ops[mem_i + 1]
                delta = C(off.imm & 0xFFFFFFFF, 4) if off.type == OP_IMM else self.opv(off, insn)
                if off.type == OP_REG and off.subtracted:
                    delta = unop("neg", delta)
                out.append(S("asg", a, loc(base_name, 4), binop("add", loc(base_name, 4), delta, 4)))
            else:
                out.append(S("asg", a, loc(base_name, 4), t if (clob or (insn.writeback and load)) else addr))
        if ret_pc:
            out.append(S("ret", a))
        return out

    def _block(self, insn, m, ops, a):
        """ldm/stm/push/pop: 번호가 낮은 레지스터가 낮은 주소에 놓인다."""
        out = []
        if m in ("vpush", "vpop"):
            raise Unsupported("vfp")
        if m == "push":
            base, mode, wb, load, regs = "sp", "db", True, False, ops
        elif m == "pop":
            base, mode, wb, load, regs = "sp", "ia", True, True, ops
        else:
            load = m.startswith("ldm")
            mode = m[3:] if len(m) > 3 else "ia"
            if mode not in ("ia", "ib", "da", "db"):
                raise Unsupported(m)
            base, wb, regs = self.reg_name(ops[0].reg), bool(insn.writeback), ops[1:]
        rl = [self.reg_name(o.reg) for o in regs]
        rl.sort(key=lambda r: {"sp": 13, "lr": 14, "pc": 15}.get(r) or int(r[1:]))
        n = 4 * len(rl)
        start = {"ia": 0, "ib": 4, "da": 4 - n, "db": -n}[mode]
        delta = n if mode in ("ia", "ib") else -n
        b = loc(base, 4)
        if wb and mode == "db" and base not in rl:                    # push: 먼저 sp 를 내리고 새 sp 기준으로 저장한다(스택 분석이 읽기 쉬운 꼴)
            out.append(S("asg", a, b, binop("add", b, C(delta, 4), 4)))
            ref, start, wb = b, 0, False
        elif (load and base in rl) or (wb and mode not in ("ia", "db")):
            ref = self.tmp()
            out.append(S("asg", a, ref, b))
        else:
            ref = b
        for k, r in enumerate(rl):
            ea = binop("add", ref, C(start + 4 * k, 4), 4) if start + 4 * k else ref
            if load:
                out.append(S("asg", a, loc("lr" if r == "pc" else r, 4), self.load_mem(ea, 4)))
            else:
                out.append(S("asg", a, mem(ea, 4), self.read_reg_named(r, insn)))
        if wb:
            out.append(S("asg", a, b, binop("add", ref, C(delta, 4), 4)))
        if load and "pc" in rl:
            out.append(S("ret", a))
        return out

    def read_reg_named(self, name: str, insn) -> X:
        if name == "pc":
            return C((insn.address + (4 if self.thumb else 8)) & 0xFFFFFFFF, 4)
        return loc(name, 4)
