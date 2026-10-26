"""함수 하나를 디컴파일하는 파이프라인 오케스트레이터.

디코드 → 리프트 → 기본블록 → 스택 추적·슬롯 승격 → 데이터플로(웹·전파·죽은 코드 제거) → 구조화 → 출력
"""
from __future__ import annotations

from ..model import Function
from . import ir
from .ir import X, S, C, loc, Block
from .cfg import build_blocks, Graph, relink, prune
from .lift_x86 import X86Lifter
from . import stack as stk_mod
from .stack import stmt_exprs as stmt_exprs_
from . import protos
from .dataflow import Flow, build_webs, abi_for, bits_of, FLAGS
from .propagate import Prop, used_vars
from . import structure as st_mod
from . import tables as tables_mod
from .render import Renderer


import time

CS_OP_REG_ = 1
CS_OP_MEM_ = 3

MAX_DECOMPILE_INSNS = 20000          # 이보다 큰 함수는 의사코드를 만들지 않는다(분석 시간이 함수 크기에 따라 급격히 늘어난다)
TIME_BUDGET = 25.0                    # 한 함수에 쓰는 최대 시간(초)


class Unsupported(Exception):
    pass


class TooLarge(Exception):
    pass


class TooComplex(Exception):
    pass


class Thunk(Exception):
    """임포트 스텁(PLT/IAT 중계 점프)."""


def fmt_stmt(s: S) -> str:
    k = s.k
    if k == "asg":
        return f"{s.dst!r} := {s.src!r}"
    if k == "call":
        return f"{s.dst!r} := {s.src!r}"
    if k == "cj":
        return f"if {s.src!r} goto {s.t:#x} else {s.f:#x}"
    if k == "jmp":
        return f"goto {s.t:#x}"
    if k == "ret":
        return f"return {s.src!r}"
    if k == "ij":
        return f"goto *{s.src!r}"
    if k == "sw":
        return f"switch {s.src!r} {[(v, hex(t)) for v, t in s.cases]} default {s.t}"
    if k == "unk":
        return f"UNK {s.extra['text']}"
    return k


def dump(blocks, order) -> str:
    out = []
    for a in order:
        if a not in blocks:
            continue
        b = blocks[a]
        out.append(f"-- {a:#x}  preds={[hex(p) for p in b.preds]}")
        for s in b.stmts:
            out.append("   " + fmt_stmt(s))
        out.append("   " + fmt_stmt(b.term))
    return "\n".join(out)


class FuncDecomp:
    def __init__(self, prog, addr: int):
        self.prog = prog
        self.addr = addr
        self.model = prog.model
        self.arch = self.model.arch
        self.bits = self.model.bits
        self.full = 8 if self.bits == 64 else 4
        self.deadline = time.monotonic() + TIME_BUDGET

    # ---- 단계 1: 디코드·리프트·CFG --------------------------------------------------------
    def load(self):
        prog = self.prog
        f = prog._func_by_addr.get(self.addr) or Function(vaddr=self.addr, name="", size=0)
        size = prog.function_size(f)
        code = prog.dis.read(f.vaddr, size)
        self.lo, self.hi = f.vaddr, f.vaddr + size
        insns = list(prog.dis.cs.disasm(code, f.vaddr))
        self.ninsns = len(insns)
        if self.ninsns > MAX_DECOMPILE_INSNS:
            raise TooLarge(self.ninsns)
        if self.arch in ("x86", "x64"):
            lifter = X86Lifter(prog.dis.cs, self.bits)
        elif self.arch == "arm64":
            from .lift_arm64 import A64Lifter
            lifter = A64Lifter(prog.dis.cs, self.bits, prog.dis.read)
        else:
            raise Unsupported(self.arch)
        self.lifter = lifter
        lifted = []
        leaders = self._leader_addrs(insns)
        for ins in insns:
            if ins.address in leaders:
                lifter.div_state = None
            stmts = lifter.lift(ins)
            parts = [(ins.address, ins.size, stmts)]
            if stmts and stmts[0].k == "split":
                # `rep movs/stos` 처럼 한 명령이 루프로 풀리는 경우: 첫 문장이 조각 경계(주소)를 알려 준다. 조각은 명령 바이트 범위 안의 주소를 쓴다.
                cuts = stmts[0].extra["cuts"]
                body = stmts[1:]
                parts = []
                prev = ins.address
                for cut in cuts:
                    parts.append((prev, cut - prev, [x for x in body if x.addr == prev]))
                    prev = cut
                parts.append((prev, ins.size - (prev - ins.address), [x for x in body if x.addr == prev]))
            lifted.extend(parts)
        from .switch import resolve_switches
        resolve_switches(lifted, insns, self.prog, self.lo, self.hi, self.full)
        self.abi = abi_for(self.arch, self.bits, self.model.fmt)
        self.blocks, self.order = build_blocks(lifted, self.lo, self.hi, self.lo, self.full, self.abi.ret)
        self.entry = self.lo
        eb = self.blocks[self.entry]
        if len(self.blocks) == 1 and eb.end - eb.start <= 24 and not eb.stmts and eb.term.k == "ij" \
                and eb.term.src.op == "mem" and eb.term.src.args[0].op == "c":
            raise Thunk(eb.term.src.args[0].aux)
        self.graph = Graph(self.blocks, self.entry)
        self.sp = self.abi.sp

    def _leader_addrs(self, insns):
        s = set()
        for ins in insns:
            m = ins.mnemonic
            if m.startswith("j") or m in ("loop", "loope", "loopne", "call"):
                for op in ins.operands:
                    if op.type == 2:
                        s.add(op.imm & ((1 << 64) - 1))
        return s

    def stack_pass(self):
        tr = stk_mod.StackTracker(self.blocks, self.graph, self.entry, self.abi)
        self.frame = tr.run()
        stk_mod.drop_dead_bookkeeping(self.blocks, self.abi)
        self.slots = stk_mod.promote_slots(self.blocks, self.abi, self.frame if self.frame is not None else 0, self.frame is not None, self.entry)
        # 바깥으로 넘기는 인자 칸: 승격된 슬롯 중 어디서도 읽히지 않는 것(저장만 되는 칸)
        read = set()
        for b in self.blocks.values():
            for s in b.stmts + [b.term]:
                for e in stk_mod.stmt_exprs(s):
                    for n in ir.walk(e):
                        if n.op == "loc" and n.aux.startswith("slot:"):
                            read.add(int(n.aux[5:]))
        self.out_slots = {o: sz for o, sz in self.slots["promoted"].items() if o not in read and o < 0}

    # ---- 단계 2: 돌아오지 않는 호출 ------------------------------------------------------
    def callee_name(self, target: X):
        if target.op == "c":
            return self.prog.name_for(target.aux)
        return None

    def check_time(self):
        if time.monotonic() > self.deadline:
            raise TooComplex("시간 초과")

    def noreturn_pass(self):
        changed = False
        for b in self.blocks.values():
            for i, s in enumerate(b.stmts):
                if s.k == "call":
                    name = self.callee_name(s.src.args[0])
                    ci = self.callee_info(s.src.args[0]) if not (name and protos.proto(name)) else None
                    if (name and protos.is_noreturn(name)) or (ci is not None and ci["noreturn"]):
                        b.stmts = b.stmts[:i + 1]
                        b.term = S("noret", s.addr)
                        s.extra = dict(s.extra or {}, noret=True)
                        changed = True
                        break
        if changed:
            relink(self.blocks)
            prune(self.blocks, self.entry)
            self.graph = Graph(self.blocks, self.entry)

    # ---- 단계 3: 호출 인자·반환값 묶기 ---------------------------------------------------
    def const_string(self, addr: int):
        """주소의 NUL 로 끝나는 출력 가능한 문자열(원본 바이트에서 읽어 줄바꿈도 그대로 보존)."""
        sec = self.model.section_for(addr)
        if sec is None or sec.is_code or sec.writable and False:
            return None
        data = self.prog.dis.read(addr, 400)
        if not data:
            return None
        end = data.find(b"\0")
        if end <= 0:
            return None
        try:
            txt = data[:end].decode("utf-8")
        except UnicodeDecodeError:
            return None
        return txt if all(ch.isprintable() or ch in "\n\t\r" for ch in txt) else None

    def callee_info(self, target: X):
        """같은 바이너리 안의 함수 호출이면 그 함수를 분석해 (인자 수·값 반환 여부·돌아오지 않음)을 돌려준다(캐시)."""
        if target.op != "c":
            return None
        addr = target.aux
        prog = self.prog
        if addr in prog.plt_names:
            return None
        f = prog._func_by_addr.get(addr)
        if f is None or f.is_import:
            return None
        cache = prog.__dict__.setdefault("_dsum", {})
        if addr in cache:
            return cache[addr]
        active = prog.__dict__.setdefault("_dsum_active", set())
        if addr in active or len(active) >= 6:
            return None
        active.add(addr)
        try:
            info = FuncDecomp(prog, addr).summary()
        except Exception:                                  # noqa: BLE001 — 요약 실패는 휴리스틱으로 대체
            info = None
        finally:
            active.discard(addr)
        cache[addr] = info
        return info

    def written_regs(self):
        """이 함수(와 호출하는 함수들)가 실제로 건드리는 호출자 보존 대상 레지스터. 같은 단위의 gcc 는 호출 규약상 파괴되는 레지스터라도
        피호출 함수가 건드리지 않음을 알면 호출 너머로 살려 쓴다(IPA-RA) — 실제 동작을 따라야 값이 맞는다."""
        abi = abi_for(self.arch, self.bits, self.model.fmt)
        w = set()
        for b in self.blocks.values():
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "loc":
                    w.add(s.dst.aux)
                elif s.k == "call":
                    clob = (s.extra or {}).get("clobbers")
                    w.update(clob if clob is not None else abi.clobbers)
                elif s.k == "unk":
                    w.update(s.extra["defs"])
                    if s.extra.get("writes_flags"):
                        w.add(FLAGS)
        return (w & abi.clobbers) | {FLAGS}

    def prebind_calls(self):
        """호출마다 피호출 함수 요약을 붙여 둔다(도달 정의 분석이 호출의 파괴 레지스터를 알 수 있게)."""
        for b in self.blocks.values():
            for s in b.stmts:
                if s.k == "call":
                    ci = self.callee_info(s.src.args[0])
                    if ci is not None:
                        s.extra = dict(s.extra or {})
                        s.extra["clobbers"] = set(ci["clobbers"])

    def summary(self) -> dict:
        self.load()
        self.stack_pass()
        self.noreturn_pass()
        self.abi = abi_for(self.arch, self.bits, self.model.fmt)
        self.prebind_calls()
        written = self.written_regs()
        self.dataflow_pass()
        self.infer_params()
        has_ret = any(b.term.k == "ret" for b in self.blocks.values())
        has_noret = any(b.term.k == "noret" for b in self.blocks.values())
        return {"nparams": len(self.params), "returns": self.returns_value, "noreturn": has_noret and not has_ret,
                "clobbers": sorted(written)}

    def _real(self, defs) -> bool:
        return any(d.kind in ("asg", "call", "unk") for d in defs)

    def _stack_arg(self, off: int, sz: int) -> X:
        if off in self.slots["promoted"]:
            return loc(stk_mod.slot_name(off), self.slots["promoted"][off])
        return ir.mem(ir.stk(off, self.full), sz)

    def _arity(self, flow, s, name, pr, sp):
        abi = self.abi
        regs = abi.arg_regs
        full = self.full

        def has_reg(i):
            return self._real(flow.reaching_at(s, regs[i]))

        shadow = 32 if abi.name == "win64" else 0

        def has_stack(k):
            if sp is None:
                return False
            off = sp + shadow + k * full
            return off in self.out_slots and self._real(flow.reaching_at(s, stk_mod.slot_name(off)))

        def contiguous(start):
            n = start
            while True:
                if n < len(regs):
                    if not has_reg(n):
                        break
                elif not has_stack(n - len(regs)):
                    break
                n += 1
                if n > 24:
                    break
            return n

        info = self.callee_info(s.src.args[0]) if pr is None else None
        if info is not None:
            return info["nparams"]
        if pr is not None:
            fixed = len(pr[1])
            total = fixed
            if pr[2]:
                fmt = None
                if fixed and pr[1][-1].startswith("const char *") and fixed - 1 < len(regs):
                    ds = flow.reaching_at(s, regs[fixed - 1])
                    if len(ds) == 1 and ds[0].kind == "asg" and ds[0].stmt.src.op == "c":
                        fmt = self.const_string(ds[0].stmt.src.aux)
                if fmt is not None and any(k in (name or "") for k in ("printf", "scanf", "syslog", "warn", "err")):
                    total = fixed + protos.printf_specs(fmt)
                else:
                    total = max(fixed, contiguous(fixed))
            return total
        return contiguous(0)

    def bind_calls(self, flow):
        abi = self.abi
        full = self.full
        for a in flow.graph.rpo:
            for s in self.blocks[a].stmts:
                if s.k != "call":
                    continue
                tgt = s.src.args[0]
                name = self.callee_name(tgt)
                pr = protos.proto(name)
                sp = (s.extra or {}).get("sp")
                if (s.src.aux or {}).get("tail"):
                    total = len(pr[1]) if pr is not None else self._tail_arity(flow, s)
                else:
                    total = self._arity(flow, s, name, pr, sp)
                args = []
                for i in range(total):
                    if i < len(abi.arg_regs):
                        args.append(loc(abi.arg_regs[i], full))
                    elif sp is not None:
                        shadow = 32 if abi.name == "win64" else 0
                        args.append(self._stack_arg(sp + shadow + (i - len(abi.arg_regs)) * full, full))
                    else:
                        break
                info = dict(s.src.aux or {})
                info.update(name=name, proto=pr, nargs=len(args), callee=self.callee_info(tgt) if pr is None else None)
                s.src = X("call", (tgt,) + tuple(args), full, info)

    def _tail_arity(self, flow, s):
        n = 0
        while n < len(self.abi.arg_regs):
            ds = flow.reaching_at(s, self.abi.arg_regs[n])
            if not any(d.kind in ("asg", "call", "unk", "entry") for d in ds):
                break
            n += 1
        return n

    def bind_rets(self, flow):
        rv = False
        for a in flow.graph.rpo:
            t = self.blocks[a].term
            if t.k != "ret":
                continue
            for d in flow.reaching_at(t, self.abi.ret):
                if d.kind in ("asg", "unk"):
                    rv = True
                elif d.kind == "call":
                    info = d.stmt.src.aux or {}
                    pr = info.get("proto")
                    ci = info.get("callee")
                    if pr is None and ci is not None:
                        rv = rv or ci["returns"]
                    else:
                        rv = rv or (pr is None and bool(info.get("tail"))) or (pr is not None and pr[0] != "void")
        name = self.prog.name_for(self.addr) or ""
        if name == "main":
            rv = True
        elif rv and name != "":
            used = self.callers_use_result()
            if used is False:
                rv = False
        elif rv:
            used = self.callers_use_result()
            if used is False:
                rv = False
        self.returns_value = rv
        for b in self.blocks.values():
            if b.term.k == "ret":
                b.term.src = loc(self.abi.ret, self.full) if rv else None

    def callers_use_result(self):
        """호출하는 쪽이 반환 레지스터를 읽는지로 값을 반환하는 함수인지 판단한다. 호출자를 모르면 None."""
        try:
            xs = [x for x in self.prog.xrefs_to(self.addr) if x["kind"] == "call"]
        except Exception:                                   # noqa: BLE001
            return None
        if not xs:
            return None
        prog = self.prog
        cs = prog.dis.cs
        abi = self.abi
        names = abi.ret_reads
        strip = ("bnd ", "notrack ")
        for x in xs[:20]:
            site = x["from"]
            code = prog.dis.read(site, 96)
            insns = list(cs.disasm(code, site))[1:9]
            for ins in insns:
                reads, writes = set(), set()
                for rid in list(ins.regs_read):
                    reads.add(cs.reg_name(rid))
                for op in ins.operands:
                    if op.type == CS_OP_REG_:
                        n = cs.reg_name(op.reg)
                        if op.access & 1:
                            reads.add(n)
                        if op.access & 2:
                            writes.add(n)
                    elif op.type == CS_OP_MEM_:
                        for rid in (op.mem.base, op.mem.index):
                            if rid:
                                reads.add(cs.reg_name(rid))
                if reads & names:
                    return True
                if writes & names:
                    break
                mn = ins.mnemonic
                for p_ in strip:
                    if mn.startswith(p_):
                        mn = mn[len(p_):]
                if any(mn.startswith(c) for c in abi.call_mn):
                    break                                        # 다음 호출이 반환 레지스터를 덮어쓴다
                if mn.endswith("ret") or any(mn.startswith(c) for c in abi.ret_mn) or mn.startswith(abi.branch_prefix):
                    return True                                  # 값이 그대로 위 호출자에게 넘어가거나 분기 뒤에서 쓰일 수 있다
        return False

    # ---- 단계 4: 웹·전파 --------------------------------------------------------------
    def dataflow_pass(self):
        self.abi = abi_for(self.arch, self.bits, self.model.fmt)
        self.prebind_calls()
        flow = Flow(self.blocks, self.graph, self.abi, self.out_slots)
        flow.run()
        self.bind_calls(flow)
        self.bind_rets(flow)
        flow = Flow(self.blocks, self.graph, self.abi, self.out_slots)
        flow.run()
        again = self.drop_dead_flag_defs(flow)
        again = self.fold_partial_defs(flow) or again          # 부분 레지스터 쓰기가 옛 값을 읽는 것처럼 보이는 문제를 풀고 다시 분석
        if again:
            flow = Flow(self.blocks, self.graph, self.abi, self.out_slots)
            flow.run()
        self.flow = flow
        self.webs = build_webs(flow)
        self.prop = Prop(self.blocks, self.graph, self.webs)
        self.prop.check = self.check_time
        self.prop.run()

    def drop_dead_flag_defs(self, flow) -> bool:
        """아무도 읽지 않는 플래그 대입을 지운다. `inc/dec` 의 플래그식은 CF 를 위해 이전 플래그를 읽기 때문에, 죽은 대입이 남으면
        그 이전 플래그와 한 변수로 묶여 불필요한 불리언 변수·대입이 생긴다."""
        used = 0
        for a in self.graph.rpo:
            b = self.blocks[a]
            for s in b.stmts + [b.term]:
                m = s.meta["uses"].get(FLAGS)
                if m:
                    used |= m
        changed = False
        for a in self.graph.rpo:
            b = self.blocks[a]
            keep = []
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "loc" and s.dst.aux == FLAGS and not (used >> s.meta["defs"][0].id) & 1:
                    changed = True
                    continue
                keep.append(s)
            b.stmts = keep
        return changed

    def fold_partial_defs(self, flow) -> bool:
        """`dl = *p` 같은 부분 쓰기는 레지스터 전체의 옛 값에 의존하는 `r = ins(r, 새 값)` 로 올라온다. 그 정의를 읽는 곳이 모두
        아래쪽 몇 바이트(trunc)만 쓰거나 같은 종류의 부분 쓰기의 옛 값 자리뿐이면 옛 값은 아무도 볼 수 없으므로 `r = zext(새 값)` 로 바꾼다
        (그러면 진입 값이 사용된 것으로 잡혀 가짜 매개변수가 생기는 일이 없다). 호출 인자·반환이 이미 묶인 뒤의 도달 정의를 쓴다."""
        stmts = []
        for a in self.graph.rpo:
            b = self.blocks[a]
            stmts.extend(b.stmts + [b.term])
        cand = {}
        for s in stmts:
            if s.k == "asg" and s.dst.op == "loc" and s.src.op == "ins" and s.src.args[0].op == "loc" and s.src.args[0].aux == s.dst.aux:
                cand[s.meta["defs"][0].id] = s.src.aux
        if not cand:
            return False
        bad = set()
        deps = []

        def visit(e, parent, s, owner):
            if e.op == "loc":
                m = s.meta["uses"].get(e.aux, 0)
                for i in bits_of(m):
                    if i not in cand:
                        continue
                    if parent is not None and parent.op == "trunc" and parent.sz <= cand[i]:
                        continue
                    if parent is not None and parent.op == "ins" and parent.args[0] is e and owner is not None:
                        deps.append((i, owner))
                        continue
                    bad.add(i)
                return
            for c in e.args:
                visit(c, e, s, owner)

        for s in stmts:
            if s.k == "unk":
                for name in s.extra["uses"]:
                    for i in bits_of(s.meta["uses"].get(name, 0)):
                        bad.add(i)
                continue
            if s.k == "asg" and s.dst.op == "loc" and s.src.op == "ins" and s.src.args[0].op == "loc" and s.src.args[0].aux == s.dst.aux:
                owner = s.meta["defs"][0].id
                visit(s.src.args[0], s.src, s, owner)
                visit(s.src.args[1], s.src, s, None)
                continue
            for e in stmt_exprs_(s):
                visit(e, None, s, None)
        W = set(cand) - bad
        changed = True
        while changed:
            changed = False
            for i, owner in deps:
                if i in W and owner not in W:
                    W.discard(i)
                    changed = True
        if not W:
            return False
        for s in stmts:
            if s.k == "asg" and s.dst.op == "loc" and s.src.op == "ins" and s.meta["defs"][0].id in W:
                val = s.src.args[1]
                s.src = ir.zext(val, s.dst.sz) if val.sz < s.dst.sz else val
        return True

    def run_to_vars(self):
        self.load()
        self.stack_pass()
        self.noreturn_pass()
        self.dataflow_pass()

    # ---- 단계 5: 매개변수·반환형 ----------------------------------------------------------
    def infer_params(self):
        abi = self.abi
        webs = self.webs
        flow = self.flow
        entry_blk = self.blocks[self.entry]
        self.prop.collect()
        pw: dict = {}                                    # 인자 번호 → web
        # 진입 웹(인자 레지스터·스택 인자)
        for i, r in enumerate(abi.arg_regs):
            d = flow.entry_def.get(r)
            if d is not None:
                pw[i] = flow.web_of_def[d.id]
        base = abi.stack_arg_base
        self.arg_base = base
        for name, d in flow.entry_def.items():
            if name.startswith("slot:"):
                off = int(name[5:])
                if off >= base and (off - base) % self.full == 0:
                    pw[len(abi.arg_regs) + (off - base) // self.full] = flow.web_of_def[d.id]
        # 스택 슬롯으로 흘려 보관(-O0 의 인자 저장): 진입 블록의 `v := 인자` 를 매개변수로 합친다
        entry_webs = {w: i for i, w in pw.items()}
        keep = []
        for s in entry_blk.stmts:
            if s.k == "asg" and s.dst.op == "var" and s.src.op == "var" and s.src.aux in entry_webs \
                    and self.prop.use_count[s.src.aux] == 1 and s.dst.aux != s.src.aux \
                    and webs[s.dst.aux].param is None and not webs[s.dst.aux].entry \
                    and webs[s.dst.aux].sz == webs[s.src.aux].sz and not any(
                        s.dst.aux in {n.aux for n in used_vars(x)} for x in keep):
                webs[s.dst.aux].param = entry_webs[s.src.aux]
                webs[s.dst.aux].extra_defs += 1                  # 호출자가 넘긴 값이 진입 시점의 정의
                pw[entry_webs[s.src.aux]] = s.dst.aux
                continue
            keep.append(s)
        entry_blk.stmts = keep
        used = set()
        for a in self.graph.rpo:
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                for n in used_vars(s):
                    used.add(n.aux)
        # 메모리에 남은(주소가 쓰이는) 스택 인자도 매개변수다
        self.mem_params = {}
        for off, sizes in self.slots["memvars"].items():
            if off >= base and (off - base) % self.full == 0:
                self.mem_params[len(abi.arg_regs) + (off - base) // self.full] = (off, max(sizes))
        for x, kind in self.slots["escapes"]:
            if x >= base and (x - base) % self.full == 0:
                self.mem_params.setdefault(len(abi.arg_regs) + (x - base) // self.full, (x, self.full))
        maxi = max(self.mem_params, default=-1)
        for i, w in pw.items():
            if w in used or webs[w].param is not None:
                maxi = max(maxi, i)
        params = []
        for i in range(maxi + 1):
            w = pw.get(i)
            if i in self.mem_params:
                params.append((None, i))
                continue
            if w is not None and (w in used or webs[w].param is not None):
                webs[w].param = i
                params.append((w, i))
            else:
                params.append((None, i))
        self.params = params

    def ret_info(self):
        if getattr(self, "ret_width", None):
            return (self.ret_width, "s")
        sz = 0
        for b in self.blocks.values():
            t = b.term
            if t.k == "ret" and t.src is not None:
                e = t.src
                if e.op in ("zext", "sext"):
                    n = e.args[0].sz
                elif e.op == "c":
                    v = ir.to_signed(e.aux, e.sz)
                    n = 4 if -(1 << 31) <= v < (1 << 31) else 8
                else:
                    n = e.sz
                sz = max(sz, n)
        return (sz or 4, "s")

    def materialize_flags(self) -> bool:
        """전파로 풀리지 않은 `cc(코드, 플래그 변수)` 를, 플래그를 정의하는 모든 곳에서 미리 계산해 둔 불리언 변수로 바꾼다."""
        found = {}
        def_stmts = {}
        for a in self.graph.rpo:
            for s in self.blocks[a].stmts:
                if s.k == "asg" and s.dst.op == "var" and self.webs[s.dst.aux].loc == FLAGS:
                    def_stmts.setdefault(s.dst.aux, []).append((a, s))
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                for e in stk_mod.stmt_exprs(s):
                    for n in ir.walk(e):
                        if n.op == "cc" and n.args[0].op == "var":
                            found.setdefault((n.args[0].aux, n.aux), None)
        if not found:
            return False
        nxt = max(self.webs) + 1
        made = {}
        for (fw, code) in found:
            defs = def_stmts.get(fw, [])
            reduced = []
            ok = bool(defs)
            for a, s in defs:
                if not ir.is_flagsrc(s.src):
                    ok = False
                    break
                r = ir.reduce_cc(code, s.src)
                if r is None:
                    ok = False
                    break
                reduced.append((a, s, r))
            if not ok:
                continue
            from .dataflow import Web
            w = Web(nxt, "B", 1)
            w.sz = 1
            self.webs[nxt] = w
            for a, s, r in reduced:
                blk = self.blocks[a]
                i = blk.stmts.index(s)
                blk.stmts.insert(i + 1, S("asg", s.addr, ir.var(nxt, 1), r))
            made[(fw, code)] = nxt
            nxt += 1
        if not made:
            return False
        def fix(e):
            if e.op == "cc" and e.args[0].op == "var" and (e.args[0].aux, e.aux) in made:
                return ir.var(made[(e.args[0].aux, e.aux)], 1)
            return None
        for a in self.graph.rpo:
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                stk_mod.map_stmt(s, lambda e: ir.subst(e, fix))
        return True

    def value_width(self, e, seen=None):
        """식이 가질 수 있는 값을 담는 데 필요한 폭(바이트): 반환형 추정용."""
        seen = seen if seen is not None else set()
        if e.op == "c":
            return 4 if e.aux < (1 << 32) else 8
        if e.op in ("zext", "sext"):
            if e.op == "zext" and e.sz == self.full and e.args[0].sz < 4:         # `xor eax,eax; setcc al` 처럼 레지스터 전체를 0 으로 만든 값은 int 로 돌려준다
                return 4
            return e.args[0].sz
        if e.op == "call":
            pr = (e.aux or {}).get("proto")
            rw = protos.ret_width(pr[0]) if pr is not None else None
            return rw if rw == 4 else e.sz
        if e.op == "var":
            v = e.aux
            if v in seen:
                return 0
            seen.add(v)
            w = self.webs[v]
            if w.entry or w.extra_defs:
                return e.sz
            best = 0
            for d in self._defs_of(v):
                best = max(best, self.value_width(d, seen))
            return best or e.sz
        return e.sz

    def _defs_of(self, v):
        out = []
        for b in self.blocks.values():
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "var" and s.dst.aux == v:
                    out.append(s.src)
                elif s.k in ("call", "unk"):
                    if s.k == "call" and s.dst is not None and s.dst.aux == v:
                        pr = (s.src.aux or {}).get("proto")
                        rw = protos.ret_width(pr[0]) if pr is not None else None
                        out.append(ir.C(0, rw) if rw == 4 else ir.var(v, self.full))
        return out

    def trim_return(self):
        """반환값을 읽는 폭을 정해 ret 의 식을 그 폭으로 자른다(변수 폭 줄이기가 이어서 작동한다)."""
        if not self.returns_value:
            return
        w = 0
        for b in self.blocks.values():
            if b.term.k == "ret" and b.term.src is not None:
                w = max(w, self.value_width(b.term.src))
        w = w if w in (1, 2, 4, 8) else (4 if w < 4 else 8)
        self.ret_width = w
        for b in self.blocks.values():
            if b.term.k == "ret" and b.term.src is not None and b.term.src.sz > w:
                b.term.src = ir.trunc(b.term.src, w)

    def apply_divconst(self):
        from .divconst import rewrite_with
        self.prop.collect()

        def resolve(v):
            if not self.prop.single_def(v) or self.webs[v].entry:
                return None
            e = self.prop.def_site[v][2].src
            return None if ir.has_op(e, ("mem", "call", "fl", "cc")) else e

        rw = rewrite_with(resolve)
        for a in self.graph.rpo:
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                stk_mod.map_stmt(s, rw)

    def dup_returns(self) -> bool:
        """내용 없는 반환 블록을 앞 블록들에 복제한다.
        - `v = e; goto end; end: return v;` → `return e;` (v 가 그 반환에서만 쓰일 때, 해당 앞 블록 단위로)
        - 반환값이 상수(또는 void)면 분기·switch 간선까지 간선마다 새 반환 블록으로 갈라 `return 0;` 이 제자리에 놓이게 한다."""
        self.prop.collect()
        changed = False
        counter = -1
        for a, R in list(self.blocks.items()):
            if R.stmts or R.term.k != "ret" or a == self.entry:
                continue
            src = R.term.src
            vs = {n.aux for n in ir.walk(src) if n.op == "var"} if src is not None else set()
            if len(vs) > 1:
                continue
            v = next(iter(vs)) if vs else None
            if v is not None and self.prop.use_count[v] != 1:
                continue
            for p in list(R.preds):
                P = self.blocks[p]
                t = P.term
                if v is not None:
                    if t.k == "jmp" and P.stmts and P.stmts[-1].k == "asg" and P.stmts[-1].dst.op == "var" and P.stmts[-1].dst.aux == v:
                        e = P.stmts.pop().src
                        P.term = S("ret", t.addr, src=ir.subst(src, lambda n, v=v, e=e: e if n.op == "var" and n.aux == v else None))
                        changed = True
                    continue
                if t.k == "jmp":
                    P.term = S("ret", t.addr, src=src)
                    changed = True
                    continue

                def clone():
                    nonlocal counter
                    c = Block(counter)
                    counter -= 1
                    c.term = S("ret", t.addr, src=src)
                    c.end = c.start
                    self.blocks[c.start] = c
                    return c.start

                if t.k == "cj":
                    nt = clone() if t.t == a else t.t
                    nf = clone() if t.f == a else t.f
                    P.term = S("cj", t.addr, src=t.src, t=nt, f=nf)
                    changed = True
                elif t.k == "sw":
                    t.cases = [(val, clone() if x == a else x) for val, x in t.cases]
                    if t.t == a:
                        t.t = clone()
                    changed = True
        if changed:
            relink(self.blocks)
            prune(self.blocks, self.entry)
        return changed

    # ---- 단계 6: 구조화·출력 ----------------------------------------------------------
    def finish(self, varmap=None):
        self.varmap = varmap or {}
        self.func_name = self.prog.name_for(self.addr) or f"sub_{self.addr:x}"
        self.func_name = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in self.func_name)
        self.infer_params()
        self.prop.run()
        if self.materialize_flags():
            self.prop.run()
        self.trim_return()
        self.apply_divconst()
        self.prop.run()
        if self.dup_returns():
            self.graph = Graph(self.blocks, self.entry)
            self.prop.graph = self.graph
            self.prop.run()
        st_mod.simplify_cfg(self.blocks, self.entry)
        self.graph = Graph(self.blocks, self.entry)
        self.prop.graph = self.graph
        self.prop.run()
        st_mod.fold_switch_bounds(self.blocks, self.entry)
        st_mod.fold_conditions(self.blocks, self.entry)
        st_mod.simplify_cfg(self.blocks, self.entry)
        self.graph = Graph(self.blocks, self.entry)
        self.first_def_addr = {}
        for b in self.blocks.values():
            for s in b.stmts:
                dv = []
                if s.k == "asg" and s.dst.op == "var":
                    dv = [s.dst.aux]
                elif s.k == "call" and s.dst is not None:
                    dv = [s.dst.aux]
                elif s.k == "unk":
                    dv = [v.aux for v in s.extra.get("defvars", ())]
                for v in dv:
                    if v not in self.first_def_addr or s.addr < self.first_def_addr[v]:
                        self.first_def_addr[v] = s.addr
        self.stack_names = StackNames(self)
        self.tables = tables_mod.find(self)
        stz = st_mod.Structurer(self.blocks, self.graph, self.entry)
        stz.check = self.check_time
        ast = stz.run()
        r = Renderer(self, ast, self.params, self.ret_info())
        lines = r.build()
        return r, lines

    def result(self, r, lines) -> dict:
        """API 응답 형식."""
        variables = []
        pnames = []
        for w, idx in self.params:
            key = w if w is not None else ("p", idx)
            pnames.append({"name": r.names[key], "slot": r.slot_ids[key]})
        for key, nm in r.names.items():
            variables.append({"slot": r.slot_ids[key], "name": nm})
        for iv in self.stack_names.fields:
            nm = self.stack_names.name_of(iv[0])
            variables.append({"slot": nm, "name": self.varmap.get(nm, nm)})
        sig = lines[0]["text"] if lines else self.func_name
        note = "휴리스틱 의사코드입니다. 정확한 의미는 디스어셈블리로 확인하세요."
        inc = list(dict.fromkeys(r.incomplete))
        if inc:
            note = f"일부 명령어를 해석하지 못해 이 의사코드는 부정확할 수 있습니다({', '.join(inc[:3])}{' 등' if len(inc) > 3 else ''}). " + note
        return {"addr": self.addr, "name": self.func_name, "signature": sig, "lines": lines, "variables": variables,
                "params": pnames, "note": note, "incomplete": inc}


class StackNames:
    """메모리에 남은 스택 변수(주소가 밖으로 새는 지역 변수·구조체·배열)를 하나의 구조체 `locals` 로 선언한다.

    주소를 받은 쪽이 인접한 칸을 함께 만질 수 있으므로(구조체·배열), 이런 칸들을 따로따로 선언하면 C 컴파일러가 배치를 바꿔 의미가 달라진다.
    그래서 오프셋을 그대로 보존하는 구조체 하나에 모으고, 칸은 `locals.var_10` 처럼 이름으로 접근한다."""

    def __init__(self, fd):
        self.fd = fd
        slots = fd.slots
        mv = slots["memvars"]
        self.base = fd.frame if fd.frame is not None else 0
        top = self.base if fd.frame is not None else 0
        ivs = []                                          # (start, end, esz, kind)
        argb = getattr(fd, "arg_base", None)
        self.arg_base = argb
        for o in sorted(mv):
            if argb is not None and o >= argb:
                continue                                  # 인자 영역은 매개변수로 선언한다
            sizes = mv[o]
            ivs.append([o, o + max(sizes), max(sizes) if len(sizes) == 1 else 1, "scalar" if len(sizes) == 1 else "bytes"])
        # 겹치는 칸은 바이트 덩어리로 합친다
        ivs.sort()
        merged = []
        for iv in ivs:
            if merged and iv[0] < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], iv[1])
                merged[-1][2] = 1
                merged[-1][3] = "bytes"
            else:
                merged.append(iv)
        ivs = merged
        # 직접 접근 없이 주소만 쓰이는 지점(버퍼)·배열 첨자로 쓰이는 지점
        arith = {x for x, k in slots["escapes"] if k == "arith"}
        for x, kind in sorted(set(slots["escapes"])):
            if argb is not None and x >= argb:
                continue
            inside = next((iv for iv in ivs if iv[0] <= x < iv[1]), None)
            if inside is None:
                nxt = next((iv[0] for iv in ivs if iv[0] > x), None)
                end = nxt if nxt is not None else max(top, x + 1)
                ivs.append([x, max(end, x + 1), 1, "bytes"])
                ivs.sort()
            elif x in arith and inside[0] == x and inside[3] == "scalar":
                nxt = next((iv[0] for iv in ivs if iv[0] > x), None)
                end = nxt if nxt is not None else max(top, inside[1])
                esz = inside[2]
                if (end - x) % esz == 0 and end - x > esz:
                    inside[1] = end
                    inside[3] = "array"
        # 배열 원소 크기: 색인 배율과 접근 크기가 같은 접근들이 일관되면 그 크기, 아니면 바이트
        hints = slots.get("arr_hints", {})
        for iv in ivs:
            if iv[3] == "array" or (iv[3] == "bytes" and iv[0] in arith):
                hs = hints.get(iv[0])
                if hs and len({h for h in hs}) == 1:
                    sz, sc = next(iter(hs))
                    if sz == sc and (iv[1] - iv[0]) % sz == 0 and iv[0] % sz == 0:
                        iv[2] = sz
                        iv[3] = "array"
        self.fields = ivs
        self.by_off = {iv[0]: iv for iv in ivs}

    # ---- 이름 ----------------------------------------------------------------------------
    def name_of(self, off):
        rel = off - self.base
        return f"var_{-rel:x}" if rel < 0 else f"arg_{rel:x}"

    def _nm(self, o):
        n = self.name_of(o)
        return self.fd.varmap.get(n, n)

    param_by_off: dict = {}

    def param_at(self, off):
        """인자 영역 주소 off 를 포함하는 메모리 매개변수 (시작, 크기, 이름) 또는 None."""
        for o, (sz, nm) in self.param_by_off.items():
            if o <= off < o + max(sz, 1):
                return o, sz, nm
        return None

    def find(self, off):
        for iv in self.fields:
            if iv[0] <= off < iv[1]:
                return iv
        return None

    def has_decls(self):
        return bool(self.fields)

    # ---- 식 ------------------------------------------------------------------------------
    def _base_ptr(self):
        return "(uint8_t *)&locals"

    def addr(self, off) -> str:
        """정수(바이트 주소)로 쓰이는 스택 주소: 항상 uint8_t * 로 만들어 덧셈이 바이트 단위가 되게 한다."""
        pa = self.param_at(off)
        if pa is not None:
            return f"(uint8_t *)&{pa[2]}" if off == pa[0] else f"((uint8_t *)&{pa[2]} + {off - pa[0]})"
        iv = self.find(off)
        if iv is None:
            return f"({self._base_ptr()} + {off - (self.fields[0][0] if self.fields else 0)})" if self.fields else f"(uint8_t *)__frame + {off}"
        nm = self._nm(iv[0])
        head = f"(uint8_t *)locals.{nm}" if iv[3] in ("array", "bytes") else f"(uint8_t *)&locals.{nm}"
        return head if off == iv[0] else f"({head} + {off - iv[0]})"

    def addr_pretty(self, off) -> str:
        pa = self.param_at(off)
        if pa is not None:
            return f"&{pa[2]}" if off == pa[0] else self.addr(off)
        iv = self.find(off)
        if iv is None:
            return self.addr(off)
        nm = self._nm(iv[0])
        if iv[3] in ("array", "bytes"):
            if off == iv[0]:
                return f"locals.{nm}"
            if iv[2] > 1 and (off - iv[0]) % iv[2] == 0:
                return f"&locals.{nm}[{(off - iv[0]) // iv[2]}]"
            return self.addr(off)
        return f"&locals.{nm}" if off == iv[0] else self.addr(off)

    def load(self, off, sz, sign):
        from .render import itype, PREC
        pa = self.param_at(off)
        if pa is not None:
            if off == pa[0] and sz == pa[1]:
                return pa[2], sign, PREC["prim"]
            return f"*({itype(sz, sign)} *){self.addr(off)}", sign, PREC["unary"]
        iv = self.find(off)
        if iv is not None:
            nm = self._nm(iv[0])
            if iv[3] == "scalar" and off == iv[0] and sz == iv[2]:
                return f"locals.{nm}", sign, PREC["prim"]
            if iv[3] == "array" and iv[2] == sz and (off - iv[0]) % sz == 0:
                return f"locals.{nm}[{(off - iv[0]) // sz}]", sign, PREC["prim"]
        return f"*({itype(sz, sign)} *){self.addr(off)}", sign, PREC["unary"]

    def declarations(self):
        """선언 줄 목록: 구조체 한 덩어리(여러 줄)."""
        from .render import itype
        if not self.fields:
            return []
        out = ["struct {"]
        pos = self.fields[0][0]
        pad = 0
        for iv in self.fields:
            if iv[0] > pos:
                pad += 1
                out.append(f"    uint8_t _pad{pad}[{iv[0] - pos}];")
            nm = self._nm(iv[0])
            esz = iv[2]
            count = (iv[1] - iv[0]) // esz if iv[3] != "scalar" else 1
            t = itype(esz, "s" if esz >= 4 else "u")
            out.append(f"    {t} {nm}[{count}];" if iv[3] in ("array", "bytes") else f"    {t} {nm};")
            pos = iv[1]
        packed = any(iv[0] % max(iv[2], 1) for iv in self.fields)
        out.append("} __attribute__((packed)) locals;" if packed else "} locals;")
        return out
