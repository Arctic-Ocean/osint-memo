"""x86 / x86-64 → IR 리프터(Capstone 상세 정보 사용).

명령어 하나를 IR 문장 몇 개로 풀어 쓴다. 레지스터는 전체 폭의 이름(rax 등)으로 정규화하고, 부분 레지스터 읽기는 trunc,
쓰기는 ins(부분 삽입)·zext(x64 의 32비트 쓰기)로 표현한다. 플래그는 가짜 위치 'F' 에 `fl(...)` 식으로 담아 두었다가
조건 분기·setcc·cmovcc 가 읽을 때 직접 비교식으로 푼다(ir.reduce_cc).
알 수 없는 명령어는 'unk' 문장으로 남겨 읽고 쓰는 레지스터만 보존한다.
"""
from __future__ import annotations

from capstone import CS_OP_REG, CS_OP_IMM, CS_OP_MEM
from capstone import x86 as cx

from . import ir
from .ir import C, X, S, loc, mem, add, sub, binop, unop, zext, sext, trunc, cmp, ite, insert, flags, cc, mask

FLAGS = "F"
CC_NAMES = {"o", "no", "b", "c", "nae", "ae", "nb", "nc", "e", "z", "ne", "nz", "be", "na", "a", "nbe", "s", "ns", "p", "pe",
            "np", "po", "l", "nge", "ge", "nl", "le", "ng", "g", "nle"}


class Unsupported(Exception):
    pass


def build_regs(bits: int) -> dict:
    t = {}
    if bits == 64:
        for n in ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp"):
            t[n] = (n, 8, 0)
        for i in range(8, 16):
            t[f"r{i}"] = (f"r{i}", 8, 0)
            t[f"r{i}d"] = (f"r{i}", 4, 0)
            t[f"r{i}w"] = (f"r{i}", 2, 0)
            t[f"r{i}b"] = (f"r{i}", 1, 0)
        for base in ("a", "b", "c", "d"):
            c = f"r{base}x"
            t[f"e{base}x"] = (c, 4, 0)
            t[f"{base}x"] = (c, 2, 0)
            t[f"{base}l"] = (c, 1, 0)
            t[f"{base}h"] = (c, 1, 8)
        for base in ("si", "di", "bp", "sp"):
            c = f"r{base}"
            t[f"e{base}"] = (c, 4, 0)
            t[base] = (c, 2, 0)
            t[f"{base}l"] = (c, 1, 0)
        t["rip"] = ("rip", 8, 0)
    else:
        for base in ("a", "b", "c", "d"):
            c = f"e{base}x"
            t[c] = (c, 4, 0)
            t[f"{base}x"] = (c, 2, 0)
            t[f"{base}l"] = (c, 1, 0)
            t[f"{base}h"] = (c, 1, 8)
        for base in ("si", "di", "bp", "sp"):
            c = f"e{base}"
            t[c] = (c, 4, 0)
            t[base] = (c, 2, 0)
        t["eip"] = ("eip", 4, 0)
    return t


def cc_of(mnem: str, prefix: str) -> str | None:
    s = mnem[len(prefix):]
    return s if s in CC_NAMES else None


class X86Lifter:
    def __init__(self, cs, bits: int):
        self.cs = cs
        self.bits = bits
        self.full = 8 if bits == 64 else 4
        self.regs = build_regs(bits)
        self.SP = "rsp" if bits == 64 else "esp"
        self.BP = "rbp" if bits == 64 else "ebp"
        self.div_state = None            # 직전 cdq/cqo(sext) 또는 edx=0(zero) 상태
        self._tmp = 0

    def tmp(self, sz: int) -> X:
        self._tmp += 1
        return loc(f"t{self._tmp}", sz)

    # ---- 레지스터 -----------------------------------------------------------------
    def reg_info(self, rid):
        name = self.cs.reg_name(rid)
        if name is None:
            raise Unsupported("reg")
        info = self.regs.get(name)
        if info is None:
            raise Unsupported(name)
        return info

    def read_reg(self, rid) -> X:
        canon, sz, shift = self.reg_info(rid)
        full = loc(canon, self.full)
        if shift:
            return trunc(binop("shr", full, C(shift, 1), self.full), sz)
        return trunc(full, sz) if sz != self.full else full

    def write_reg(self, rid, val: X, addr: int) -> S:
        canon, sz, shift = self.reg_info(rid)
        full = loc(canon, self.full)
        if shift:
            raise Unsupported("ah")
        if sz == self.full:
            src = val
        elif sz == 4 and self.bits == 64:
            src = zext(val, 8)
        else:
            src = insert(full, val)
        return S("asg", addr, full, src)

    # ---- 피연산자 -----------------------------------------------------------------
    def mem_addr(self, insn, op) -> X:
        m = op.mem
        if m.base == cx.X86_REG_RIP:
            return C(insn.address + insn.size + m.disp, self.full)
        a = None
        if m.base:
            a = self.read_reg(m.base)
            if a.sz != self.full:
                a = zext(a, self.full)
        if m.index:
            ix = self.read_reg(m.index)
            if ix.sz != self.full:
                ix = zext(ix, self.full)
            if m.scale != 1:
                ix = binop("mul", ix, C(m.scale, self.full), self.full)
            a = ix if a is None else binop("add", a, ix, self.full)
        disp = m.disp
        if a is None:
            a = C(disp, self.full)
        elif disp:
            a = binop("add", a, C(disp, self.full), self.full)
        if m.segment == cx.X86_REG_FS or m.segment == cx.X86_REG_GS:
            seg = "fs" if m.segment == cx.X86_REG_FS else "gs"
            a = binop("add", X("seg", (), self.full, seg), a, self.full)
        return a

    def is_canary(self, addr: X) -> bool:
        return addr.op == "add" and addr.args[0].op == "seg" and addr.args[0].aux == "fs" \
            and addr.args[1].op == "c" and addr.args[1].aux in (0x28, 0x14)

    def read_op(self, insn, op, sz=None) -> X:
        if op.type == CS_OP_REG:
            return self.read_reg(op.reg)
        if op.type == CS_OP_IMM:
            return C(op.imm, sz or op.size)
        if op.type == CS_OP_MEM:
            a = self.mem_addr(insn, op)
            if self.is_canary(a):
                return X("canary", (), op.size)
            return mem(a, sz or op.size)
        raise Unsupported("operand")

    def write_op(self, insn, op, val: X) -> S:
        if op.type == CS_OP_REG:
            return self.write_reg(op.reg, val, insn.address)
        if op.type == CS_OP_MEM:
            return S("asg", insn.address, mem(self.mem_addr(insn, op), op.size), val)
        raise Unsupported("dest")

    def is_stackish(self, op) -> bool:
        return op.type == CS_OP_REG and self.reg_info(op.reg)[0] == self.SP

    # ---- 진입점 -------------------------------------------------------------------
    def lift(self, insn) -> list:
        try:
            out = self._lift(insn)
        except (Unsupported, KeyError, IndexError):
            out = [self.unknown(insn)]
        self._track_div(insn)
        return out

    def unknown(self, insn) -> S:
        """해석하지 못한 명령어: 읽고 쓰는 레지스터만 접근 속성(op.access)과 암묵 레지스터로 보존한다."""
        uses, defs = set(), set()

        def canon(rid):
            n = self.cs.reg_name(rid)
            return self.regs[n][0] if n in self.regs else None

        for rid in list(insn.regs_read):
            c = canon(rid)
            if c:
                uses.add(c)
        for rid in list(insn.regs_write):
            c = canon(rid)
            if c:
                defs.add(c)
        for op in insn.operands:
            if op.type == CS_OP_REG:
                c = canon(op.reg)
                if c:
                    if op.access & 1:
                        uses.add(c)
                    if op.access & 2:
                        defs.add(c)
                        if not (op.access & 1) and self.reg_info(op.reg)[1] < self.full and not (self.bits == 64 and self.reg_info(op.reg)[1] == 4):
                            uses.add(c)                       # 부분 쓰기는 옛 값에 의존
            elif op.type == CS_OP_MEM:
                for rid in (op.mem.base, op.mem.index):
                    if rid:
                        c = canon(rid)
                        if c:
                            uses.add(c)
        text = f"{insn.mnemonic} {insn.op_str}".strip()
        wf = any(self.cs.reg_name(r) in ("eflags", "rflags") for r in insn.regs_write)
        return S("unk", insn.address, extra={"text": text, "uses": sorted(uses - {"rip", "eip"}), "defs": sorted(defs - {"rip", "eip"}),
                                            "writes_flags": wf})

    def _track_div(self, insn):
        m = insn.mnemonic
        if m in ("cdq", "cqo"):
            self.div_state = "sext"
        elif m in ("xor", "mov") and len(insn.operands) == 2 and insn.operands[0].type == CS_OP_REG \
                and self.cs.reg_name(insn.operands[0].reg) in ("edx", "rdx") \
                and ((m == "xor" and insn.operands[1].type == CS_OP_REG and insn.operands[1].reg == insn.operands[0].reg)
                     or (m == "mov" and insn.operands[1].type == CS_OP_IMM and insn.operands[1].imm == 0)):
            self.div_state = "zero"
        else:
            written = {self.cs.reg_name(r) for r in insn.regs_write}
            if written & {"eax", "rax", "ax", "al", "edx", "rdx", "dx", "dl"}:
                self.div_state = None

    # ---- 본체 ---------------------------------------------------------------------
    def _lift(self, insn) -> list:
        m = insn.mnemonic
        if " " in m:                                   # Capstone 은 접두사를 니모닉에 붙여 준다: `notrack jmp`, `bnd call`, `rep stosd`
            toks = m.split()
            while len(toks) > 1 and toks[0] in ("notrack", "bnd", "data16", "cs", "ds", "es", "ss"):
                toks.pop(0)
            if len(toks) > 1:
                raise Unsupported(m)                   # rep·lock 등은 알 수 없는 명령으로 보존한다
            m = toks[0]
        ops = insn.operands
        a = insn.address
        n = len(ops)
        out = []
        asg = lambda d, s: out.append(S("asg", a, d, s))

        if m in ("nop", "endbr64", "endbr32", "fnop", "pause", "ud2", "int3", "hlt", "lfence", "mfence", "sfence", "prefetcht0",
                 "prefetchnta", "prefetcht1", "prefetcht2", "prefetchw", "cld", "std", "nopw", "nopl", "data16", "xgetbv"):
            if m in ("hlt", "ud2", "int3"):
                return [S("noret", a)]
            return []
        if m.startswith("nop"):
            return []

        # -- 데이터 이동 --
        if m in ("mov", "movabs"):
            return [self.write_op(insn, ops[0], self.read_op(insn, ops[1], ops[0].size))]
        if m in ("movzx", "movsx", "movsxd", "movsbl", "movzbl", "movswl", "movzwl"):
            src = self.read_op(insn, ops[1])
            dsz = ops[0].size
            v = zext(src, dsz) if m.startswith("movz") else sext(src, dsz)
            return [self.write_op(insn, ops[0], v)]
        if m == "lea":
            return [self.write_op(insn, ops[0], self._fit(self.mem_addr(insn, ops[1]), ops[0].size))]
        if m == "xchg":
            x, y = self.read_op(insn, ops[0]), self.read_op(insn, ops[1])
            if ops[0].type == CS_OP_MEM or ops[1].type == CS_OP_MEM:
                raise Unsupported("xchg mem")
            return [self.write_op(insn, ops[0], y), self.write_op(insn, ops[1], x)]
        if m == "push":
            v = self.read_op(insn, ops[0], self.full)
            if v.sz != self.full:
                v = sext(v, self.full)
            sp = loc(self.SP, self.full)
            return [S("asg", a, mem(sub(sp, C(self.full, self.full)), self.full), v),          # 값·주소는 옛 sp 로 계산한 뒤
                    S("asg", a, sp, sub(sp, C(self.full, self.full)))]
        if m == "pop":
            sp = loc(self.SP, self.full)
            first = self.write_op(insn, ops[0], mem(sp, self.full)) if not self.is_stackish(ops[0]) else None
            if first is None:
                raise Unsupported("pop sp")
            return [first, S("asg", a, sp, add(sp, C(self.full, self.full)))]
        if m == "leave":
            sp, bp = loc(self.SP, self.full), loc(self.BP, self.full)
            return [S("asg", a, sp, bp), S("asg", a, bp, mem(loc(self.SP, self.full), self.full)),
                    S("asg", a, sp, add(loc(self.SP, self.full), C(self.full, self.full)))]
        if m == "cdqe":
            return [S("asg", a, loc("rax", 8), sext(trunc(loc("rax", 8), 4), 8))]
        if m == "cwde":
            return [self.write_reg_named("eax", sext(self.rn("ax"), 4), a)]
        if m == "cbw":
            return [self.write_reg_named("ax", sext(self.rn("al"), 2), a)]
        if m == "cdq":
            return [self.write_reg_named("edx", binop("sar", self.rn("eax"), C(31, 1), 4), a)]
        if m == "cqo":
            return [S("asg", a, loc("rdx", 8), binop("sar", loc("rax", 8), C(63, 1), 8))]
        if m == "cwd":
            return [self.write_reg_named("dx", binop("sar", self.rn("ax"), C(15, 1), 2), a)]

        # -- 산술·논리 --
        if m in ("add", "sub", "and", "or", "xor", "adc", "sbb"):
            sz = ops[0].size
            B = self.read_op(insn, ops[1], sz)
            if B.sz != sz:
                B = sext(B, sz)
            if m in ("adc", "sbb"):
                A = self.read_op(insn, ops[0])
                carry = zext(cc("b", loc(FLAGS, 1)), sz)
                R = binop("add" if m == "adc" else "sub", binop("add" if m == "adc" else "sub", A, B, sz), carry, sz)
                return [self.write_op(insn, ops[0], R), S("asg", a, loc(FLAGS, 1), flags("unk", (), 1))]
            if m == "xor" and ops[0].type == CS_OP_REG and ops[1].type == CS_OP_REG and ops[0].reg == ops[1].reg:
                return [self.write_op(insn, ops[0], C(0, sz)), S("asg", a, loc(FLAGS, 1), flags("logic", (C(0, sz),), sz))]
            if m in ("add", "sub", "and") and ops[0].type == CS_OP_REG and self.is_stackish(ops[0]):
                R = binop(m, self.read_op(insn, ops[0]), B, sz)            # sp 조정(sub rsp, N 등)의 플래그는 쓰이지 않는다
                return [self.write_op(insn, ops[0], R)]
            return self._rmw(insn, ops[0], B, m, sz)
        if m in ("cmp", "test"):
            sz = ops[0].size
            pre = []
            A = self.read_op(insn, ops[0])
            B = self.read_op(insn, ops[1], sz)
            if B.sz != sz:
                B = sext(B, sz)
            if ops[0].type == CS_OP_MEM:                       # 메모리 피연산자는 임시 변수에 한 번 읽어 두고 플래그가 그걸 가리키게 한다
                t = self.tmp(sz)
                pre.append(S("asg", a, t, A))
                A = t
            if ops[1].type == CS_OP_MEM:
                t = self.tmp(sz)
                pre.append(S("asg", a, t, B))
                B = t
            if m == "cmp":
                return pre + [S("asg", a, loc(FLAGS, 1), flags("sub", (A, B), sz))]
            return pre + [S("asg", a, loc(FLAGS, 1), flags("logic", (binop("and", A, B, sz),), sz))]
        if m in ("inc", "dec"):
            sz = ops[0].size
            return self._rmw(insn, ops[0], C(1, sz), m, sz)
        if m == "neg":
            return self._rmw(insn, ops[0], None, "neg", ops[0].size)
        if m == "not":
            return [self.write_op(insn, ops[0], unop("not", self.read_op(insn, ops[0])))]
        if m == "imul":
            if n == 1:
                return self._widening_mul(insn, ops[0], signed=True)
            sz = ops[0].size
            if n == 2:
                A, B = self.read_op(insn, ops[0]), self.read_op(insn, ops[1], sz)
            else:
                A, B = self.read_op(insn, ops[1], sz), self.read_op(insn, ops[2], sz)
            if B.sz != sz:
                B = sext(B, sz)
            return [self.write_op(insn, ops[0], binop("mul", A, B, sz)), S("asg", a, loc(FLAGS, 1), flags("unk", (), 1))]
        if m == "mul":
            return self._widening_mul(insn, ops[0], signed=False)
        if m in ("div", "idiv"):
            return self._divide(insn, ops[0], signed=(m == "idiv"))
        if m in ("shl", "sal", "shr", "sar", "rol", "ror"):
            sz = ops[0].size
            if n == 1:
                cnt = C(1, 1)
            elif ops[1].type == CS_OP_IMM:
                cnt = C(ops[1].imm & (63 if sz == 8 else 31), 1)
            else:
                cnt = binop("and", self.read_op(insn, ops[1]), C(63 if sz == 8 else 31, 1), 1)
            op = {"sal": "shl"}.get(m, m)
            return self._rmw(insn, ops[0], cnt, op, sz)
        if m == "bswap":
            raise Unsupported("bswap")

        # -- 조건 --
        if m.startswith("set"):
            code = cc_of(m, "set")
            if code is None:
                raise Unsupported(m)
            return [self.write_op(insn, ops[0], cc(code, loc(FLAGS, 1)))]
        if m.startswith("cmov"):
            code = cc_of(m, "cmov")
            if code is None:
                raise Unsupported(m)
            sz = ops[0].size
            old = self.read_op(insn, ops[0])
            return [self.write_op(insn, ops[0], ite(cc(code, loc(FLAGS, 1)), self.read_op(insn, ops[1], sz), old))]

        # -- 제어 흐름 --
        if m == "call":
            return [self._call(insn, ops[0])]
        if m == "ret" or m == "retf":
            return [S("ret", a)]
        if m == "jmp":
            if ops[0].type == CS_OP_IMM:
                return [S("jmp", a, t=ops[0].imm & ir.mask(self.full))]
            return [S("ij", a, src=self.read_op(insn, ops[0], self.full))]
        if m in ("jrcxz", "jecxz", "jcxz"):
            r = self.rn({"jrcxz": "rcx", "jecxz": "ecx", "jcxz": "cx"}[m])
            return [S("cj", a, src=cmp("eq", r, C(0, r.sz)), t=ops[0].imm & ir.mask(self.full), f=a + insn.size)]
        if m.startswith("j"):
            code = cc_of(m, "j")
            if code is None:
                raise Unsupported(m)
            return [S("cj", a, src=cc(code, loc(FLAGS, 1)), t=ops[0].imm & ir.mask(self.full), f=a + insn.size)]
        if m in ("loop", "loope", "loopne"):
            c = "rcx" if self.bits == 64 else "ecx"
            cl = loc(c, self.full)
            dec = S("asg", a, cl, sub(cl, C(1, self.full)))
            return [dec, S("cj", a, src=cmp("ne", loc(c, self.full), C(0, self.full)), t=ops[0].imm & ir.mask(self.full),
                           f=a + insn.size)]
        raise Unsupported(m)

    # ---- 보조 ---------------------------------------------------------------------
    def rn(self, name: str) -> X:
        canon, sz, shift = self.regs[name]
        full = loc(canon, self.full)
        return trunc(full, sz) if sz != self.full else full

    def write_reg_named(self, name: str, val: X, addr: int) -> S:
        canon, sz, shift = self.regs[name]
        full = loc(canon, self.full)
        if sz == self.full:
            src = val
        elif sz == 4 and self.bits == 64:
            src = zext(val, 8)
        else:
            src = insert(full, val)
        return S("asg", addr, full, src)

    def _rmw(self, insn, dst_op, B, m, sz):
        """읽고-고치고-쓰는 명령(add/sub/and/or/xor/inc/dec/neg/시프트). 연산 전 값은 임시 변수에 잡아 두어,
        플래그 식이 대입 뒤에도 옛 값을 가리킬 수 있게 한다."""
        a = insn.address
        out = []
        A = self.read_op(insn, dst_op)
        ta = self.tmp(sz)
        out.append(S("asg", a, ta, A))
        if B is not None and dst_op.type == CS_OP_REG:                      # add eax, eax 처럼 B 가 dst 를 읽으면 같이 잡는다
            canon = self.reg_info(dst_op.reg)[0]
            if any(n.aux == canon for n in ir.walk(B) if n.op == "loc"):
                tb = self.tmp(B.sz)
                out.append(S("asg", a, tb, B))
                B = tb
        if m in ("inc", "dec"):
            R = binop("add", ta, C(1 if m == "inc" else -1, sz), sz)
        elif m == "neg":
            R = unop("neg", ta)
        else:
            R = binop(m, ta, B, sz)
        out.append(self.write_op(insn, dst_op, R))
        newv = R if dst_op.type == CS_OP_MEM else self.read_op(insn, dst_op)
        if m in ("add", "sub"):
            fl = flags(m, (ta, B, newv), sz)
        elif m in ("and", "or", "xor"):
            fl = flags("logic", (newv,), sz)
        elif m in ("inc", "dec"):
            fl = flags(m, (ta, loc(FLAGS, 1), newv), sz)
        elif m == "neg":
            fl = flags("neg", (ta, newv), sz)
        elif m in ("shl", "shr", "sar") and B is not None and B.op == "c" and B.aux != 0:
            fl = flags("res", (newv,), sz)
        else:
            fl = flags("unk", (), 1)
        out.append(S("asg", a, loc(FLAGS, 1), fl))
        return out

    def _fit(self, v: X, sz: int) -> X:
        return v if v.sz == sz else (trunc(v, sz) if v.sz > sz else zext(v, sz))

    def _call(self, insn, op) -> S:
        a = insn.address
        if op.type == CS_OP_IMM:
            tgt = C(op.imm & ir.mask(self.full), self.full)
        else:
            tgt = self.read_op(insn, op, self.full)
        return S("call", a, dst=loc("rax" if self.bits == 64 else "eax", self.full), src=X("call", (tgt,), self.full, {"args": None}))

    def _widening_mul(self, insn, op, signed):
        a = insn.address
        sz = op.size
        if sz == 1:
            raise Unsupported("mul8")
        acc = self.rn("rax" if sz == 8 else "eax" if sz == 4 else "ax")
        B = self.read_op(insn, op)
        lo = binop("mul", acc, B, sz)
        hi = binop("mulhs" if signed else "mulhu", acc, B, sz)
        lo_name = {8: "rax", 4: "eax", 2: "ax"}[sz]
        hi_name = {8: "rdx", 4: "edx", 2: "dx"}[sz]
        return [self.write_reg_named(hi_name, hi, a), self.write_reg_named(lo_name, lo, a),
                S("asg", a, loc(FLAGS, 1), flags("unk", (), 1))]

    def _divide(self, insn, op, signed):
        a = insn.address
        sz = op.size
        if sz == 1:
            raise Unsupported("div8")
        names = {8: ("rax", "rdx"), 4: ("eax", "edx"), 2: ("ax", "dx")}[sz]
        acc, hi = self.rn(names[0]), self.rn(names[1])
        B = self.read_op(insn, op)
        st = self.div_state
        if sz == 8 and st not in ("sext", "zero"):
            raise Unsupported("div128")
        if st == "sext" and signed:
            num, den = acc, B
            q, r = binop("sdiv", num, den, sz), binop("srem", num, den, sz)
        elif st == "zero" and not signed:
            q, r = binop("udiv", acc, B, sz), binop("urem", acc, B, sz)
        elif st == "zero" and signed:
            # 상위가 0 이면 피제수는 부호 없는 값이지만 최상위 비트가 서 있으면 음수로 해석된다 → 일반식으로
            wsz = sz * 2
            wide = binop("or", binop("shl", zext(hi, wsz), C(8 * sz, 1), wsz), zext(acc, wsz), wsz)
            q = trunc(binop("sdiv", wide, sext(B, wsz), wsz), sz)
            r = trunc(binop("srem", wide, sext(B, wsz), wsz), sz)
        else:
            wsz = sz * 2
            wide = binop("or", binop("shl", zext(hi, wsz), C(8 * sz, 1), wsz), zext(acc, wsz), wsz)
            if signed:
                q = trunc(binop("sdiv", wide, sext(B, wsz), wsz), sz)
                r = trunc(binop("srem", wide, sext(B, wsz), wsz), sz)
            else:
                q = trunc(binop("udiv", wide, zext(B, wsz), wsz), sz)
                r = trunc(binop("urem", wide, zext(B, wsz), wsz), sz)
        if st not in ("sext", "zero"):                    # 일반식은 상·하위 입력을 둘 다 읽으므로 임시 변수를 거친다
            tq, tr = loc(f"t{a:x}q", sz), loc(f"t{a:x}r", sz)
            return [S("asg", a, tq, q), S("asg", a, tr, r), self.write_reg_named(names[0], tq, a),
                    self.write_reg_named(names[1], tr, a), S("asg", a, loc(FLAGS, 1), flags("unk", (), 1))]
        return [self.write_reg_named(names[1], r, a), self.write_reg_named(names[0], q, a),
                S("asg", a, loc(FLAGS, 1), flags("unk", (), 1))]
