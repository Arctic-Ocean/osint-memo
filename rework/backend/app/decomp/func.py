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
from . import x87 as x87_mod
from .dataflow import Flow, build_webs, abi_for, bits_of, FLAGS
from .propagate import Prop, used_vars
from . import structure as st_mod
from . import tables as tables_mod
from .render import Renderer


import re
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

    def _const_data(self, va: int) -> bool:
        sec = self.model.section_for(va)
        return sec is not None and not sec.is_code and not sec.writable

    # ---- 단계 1: 디코드·리프트·CFG --------------------------------------------------------
    def load(self):
        prog = self.prog
        f = prog._func_by_addr.get(self.addr) or Function(vaddr=self.addr, name="", size=0)
        size = prog.function_size(f)
        code = prog.dis.read(f.vaddr, size)
        self.lo, self.hi = f.vaddr, f.vaddr + size
        self.thumb = bool(getattr(f, "thumb", False)) and self.arch == "arm"
        cs = prog.dis.cs_at(f.vaddr, self.thumb) if self.arch == "arm" else prog.dis.cs
        insns = list(cs.disasm(code, f.vaddr))
        self.ninsns = len(insns)
        if self.ninsns > MAX_DECOMPILE_INSNS:
            raise TooLarge(self.ninsns)
        if self.arch in ("x86", "x64"):
            lifter = X86Lifter(prog.dis.cs, self.bits, prog.dis.read, self._const_data, {i.vaddr for i in self.model.imports if i.vaddr})
        elif self.arch == "arm64":
            from .lift_arm64 import A64Lifter
            lifter = A64Lifter(prog.dis.cs, self.bits, prog.dis.read, self._const_data)
        elif self.arch == "arm":
            from .lift_arm import A32Lifter
            lifter = A32Lifter(cs, self.thumb, prog.dis.read, self._const_data)
        else:
            raise Unsupported(self.arch)
        self.lifter = lifter
        if self.arch in ("x86", "x64") and any(x87_mod.is_x87(i.mnemonic) for i in insns):
            lifter.x87_setup(x87_mod.analyze(insns, f.vaddr, self._callee_fp))          # x87 스택 깊이를 명령마다 미리 구해 둔다
        lifted = []
        leaders = self._leader_addrs(insns)
        for ins in (insns if self.arch != "arm" else ()):
            if ins.address in leaders:
                lifter.div_state = None
                if lifter.x87 is not None:
                    lifter.x87_reset()
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
        if self.arch == "arm":
            lifted = lifter.lift_function(insns)
        from .switch import resolve_switches
        resolve_switches(lifted, insns, self.prog, self.lo, self.hi, self.full)
        self.abi = abi_for(self.arch, self.bits, self.model.fmt, getattr(self, "thumb", False))
        self.blocks, self.order = build_blocks(lifted, self.lo, self.hi, self.lo, self.full, self.abi.ret)
        self.entry = self.lo
        eb = self.blocks[self.entry]
        if len(self.blocks) == 1 and eb.end - eb.start <= 24 and not eb.stmts and eb.term.k == "ij" \
                and eb.term.src.op == "mem" and eb.term.src.args[0].op == "c":
            raise Thunk(eb.term.src.args[0].aux)
        self.graph = Graph(self.blocks, self.entry)
        self.sp = self.abi.sp

    def _callee_fp(self, addr):
        """호출 대상이 st(0) 에 부동소수점 값을 남기는 함수인가(True·False), 알 수 없으면 None."""
        t = C(addr, self.full)
        name = self.callee_name(t)
        pr = protos.proto(name) if name else None
        if pr is not None:
            return pr[0] in protos.FP_TYPES
        ci = self.callee_info(t)
        if ci is not None:
            return bool(ci.get("ret_fp"))
        return None

    def _leader_addrs(self, insns):
        s = set()
        for ins in insns:
            m = ins.mnemonic
            if m.startswith("j") or m in ("loop", "loope", "loopne", "call"):
                for op in ins.operands:
                    if op.type == 2:
                        s.add(op.imm & ((1 << 64) - 1))
        return s

    def _call_pop(self, s) -> int:
        """x86-32 에서 호출된 함수가 스택 인자를 치우는 바이트 수(stdcall). WinAPI 는 원형 표의 인자 폭, 같은 파일 함수는 그 함수의 `ret N`."""
        if self.arch != "x86" or self.bits != 32:
            return 0
        tgt = s.src.args[0]
        if tgt.op != "c":
            return 0
        name = self.callee_name(tgt)
        pr = protos.proto(name) if name else None
        if pr is not None:
            if self.model.fmt == "PE" and not pr[2]:
                return sum(8 if t in ("double", "long long", "unsigned long long", "int64_t", "uint64_t") else 4 for t in pr[1])
            return 0
        ci = self.callee_info(tgt)
        return (ci or {}).get("pop", 0) or 0

    def stack_pass(self):
        tr = stk_mod.StackTracker(self.blocks, self.graph, self.entry, self.abi, self._call_pop)
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
        abi = abi_for(self.arch, self.bits, self.model.fmt, getattr(self, "thumb", False))
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
        self.abi = abi_for(self.arch, self.bits, self.model.fmt, getattr(self, "thumb", False))
        self.prebind_calls()
        written = self.written_regs()
        self.dataflow_pass()
        self.infer_params()
        has_ret = any(b.term.k == "ret" for b in self.blocks.values())
        has_noret = any(b.term.k == "noret" for b in self.blocks.values())
        pop = max((b.term.extra.get("pop", 0) for b in self.blocks.values() if b.term.k == "ret" and isinstance(b.term.extra, dict)), default=0)
        nparams = max(len(self.params), pop // self.full) if pop else len(self.params)
        return {"nparams": nparams, "pop": pop, "returns": self.returns_value and not self.ret_fp, "noreturn": has_noret and not has_ret,
                "clobbers": sorted(written), "fp_params": [(k, kind) for _w, k, kind in self.fp_params], "ret_fp": self.ret_fp,
                "layout": self._stack_layout()}

    def _stack_layout(self):
        """x86-32 의 스택 인자 배치: [(바이트 폭, 실수 폭 또는 None)] — 호출하는 쪽이 double 은 8바이트 하나로 넘기도록."""
        if self.arch != "x86":
            return None
        kinds = self.fp_operand_kinds()
        lay = []
        for w, idx in self.params:
            if w is None:
                sz = self.mem_params[idx][1] if idx in self.mem_params else 4
                lay.append((sz if sz in (4, 8) else 4, None))
            else:
                sz = self.webs[w].sz
                lay.append((sz if sz in (4, 8) else 4, kinds.get(w) if kinds.get(w) == sz else None))
        return lay

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
            fixed = sum(1 for t in pr[1] if t not in protos.FP_TYPES)
            total = fixed
            if pr[2]:
                fmt = self._call_fmt(flow, s, pr, regs)
                if fmt is not None and any(k in (name or "") for k in ("printf", "scanf", "syslog", "warn", "err")):
                    total = fixed + sum(1 for k in protos.printf_kinds(fmt) if k == "i")
                else:
                    total = max(fixed, contiguous(fixed))
            return total
        return contiguous(0)

    def _call_fmt(self, flow, s, pr, regs):
        """가변 인자 함수 호출의 형식 문자열(상수일 때)."""
        fixed = len(pr[1])
        if fixed and pr[1][-1].startswith("const char *") and fixed - 1 < len(regs):
            ds = flow.reaching_at(s, regs[fixed - 1])
            if len(ds) == 1 and ds[0].kind == "asg" and ds[0].stmt.src.op == "c":
                return self.const_string(ds[0].stmt.src.aux)
        return None

    def _fp_plan(self, flow, s, blk, name, pr, ci):
        """호출의 부동소수점 인자: [(xmm 번호, 폭)]. Windows x64 는 위치가 곧 번호다. 반환형은 (인자 목록, 반환 폭 또는 None)."""
        if self.arch not in ("x64", "arm64"):
            return [], None
        win = self.abi.name == "win64"
        rk = None
        plan = []
        if pr is not None:
            if pr[0] in protos.FP_TYPES:
                rk = protos.FP_TYPES[pr[0]]
            kinds_by_pos = [protos.FP_TYPES.get(t) for t in pr[1]]
            if pr[2]:
                fmt = self._call_fmt(flow, s, pr, self.abi.arg_regs)
                if fmt is not None and any(k in (name or "") for k in ("printf", "scanf", "syslog", "warn", "err")):
                    kinds_by_pos += [8 if k == "d" else None for k in protos.printf_kinds(fmt) if k in ("i", "d")]
            if win:
                plan = [(i, k) for i, k in enumerate(kinds_by_pos) if k]
            else:
                plan = [(j, k) for j, k in enumerate([k for k in kinds_by_pos if k])]
            return plan, rk
        if ci is not None:
            rk = ci.get("ret_fp")
            fps = ci.get("fp_params") or []
            if win:
                plan = [(k, kind) for k, kind in fps]
            else:
                plan = [(j, kind) for j, (_k, kind) in enumerate(fps)]
            return plan, rk
        # 알 수 없는 호출 대상: 같은 기본 블록에서 실제로 쓴 xmm 인자 레지스터만 인자로 본다(앞 코드의 남은 값을 인자로 오해하지 않도록)
        n = 0
        stmts = blk.stmts
        while n < self.abi.fp_nargs:
            ds = flow.reaching_at(s, f"{self.abi.fpr}{n}")
            if not any(d.kind in ("asg", "unk") and d.stmt is not None and d.stmt in stmts for d in ds):
                break
            n += 1
        return [(j, 8) for j in range(n)], None

    def bind_calls(self, flow) -> bool:
        abi = self.abi
        full = self.full
        fp_ret = False
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
                ci = self.callee_info(tgt) if pr is None else None
                lay = None if (s.src.aux or {}).get("tail") else self._x86_layout(flow, s, name, pr, ci, sp)
                fp, rk = ([], None) if (s.src.aux or {}).get("tail") else self._fp_plan(flow, s, self.blocks[a], name, pr, ci)
                x87w = (s.extra or {}).get("x87ret")                      # x87: 호출이 st(0) 에 남긴 값(리프터가 호출 뒤에 새 꼭대기 칸으로 옮겨 둔다)
                if x87w:
                    rk = rk or (pr and protos.FP_TYPES.get(pr[0])) or (ci or {}).get("ret_fp") or x87w
                fp_pos = {k for k, _ in fp} if abi.name == "win64" else set()
                args = []
                pos = 0
                for i in range(total):
                    while pos in fp_pos:                                  # Windows x64: 실수 인자가 차지한 위치의 정수 레지스터는 건너뛴다
                        pos += 1
                    if pos < len(abi.arg_regs):
                        args.append(loc(abi.arg_regs[pos], full))
                    elif sp is not None:
                        shadow = 32 if abi.name == "win64" else 0
                        args.append(self._stack_arg(sp + shadow + (pos - len(abi.arg_regs)) * full, full))
                    else:
                        break
                    pos += 1
                fargs, fkinds = [], []
                for k, kind in fp:
                    x = loc(f"{abi.fpr}{k}", 8)
                    fargs.append(x if kind == 8 else ir.trunc(x, 4))
                    fkinds.append(kind)
                info = dict(s.src.aux or {})
                info.update(name=name, proto=pr, nargs=len(args), callee=ci, fp_kinds=fkinds, ret_fp=rk)
                if rk:
                    s.dst = loc(abi.fp_ret, x87w or 8)
                    fp_ret = True
                if lay is not None:                                          # x86-32: 실수·8바이트 인자가 섞인 호출은 호출 규약대로 폭을 지켜 인자를 만든다
                    args, akinds, k = [], [], 0
                    for size, kind in lay:
                        args.append(self._stack_arg(sp + k * full, size))
                        akinds.append(kind)
                        k += size // full
                    info.update(nargs=len(args), arg_kinds=akinds)
                s.src = X("call", (tgt,) + tuple(args) + tuple(fargs), full, info)
        return fp_ret

    @staticmethod
    def _slot_of(ctype: str):
        """C 형 이름 → (스택 폭, 실수 폭 또는 None). float 인자는 가변 인자가 아니면 4바이트 그대로 넘어간다."""
        t = ctype.strip()
        if t == "double":
            return 8, 8
        if t == "float":
            return 4, 4
        if t in ("long long", "unsigned long long", "int64_t", "uint64_t", "intmax_t", "uintmax_t", "off64_t"):
            return 8, None
        return 4, None

    def _x86_layout(self, flow, s, name, pr, ci, sp):
        """x86-32 호출의 스택 인자 배치. 실수·8바이트 인자가 없으면 None(기존 방식: 4바이트씩)."""
        if self.arch != "x86" or sp is None:
            return None
        lay = None
        if pr is not None:
            lay = [self._slot_of(t) for t in pr[1]]
            if pr[2]:                                                       # 가변 인자: 형식 문자열이 상수면 그 뒤 인자의 종류를 안다
                fixed = len(pr[1])
                fmt = None
                if fixed and pr[1][-1].startswith("const char *"):
                    ds = flow.reaching_at(s, stk_mod.slot_name(sp + (fixed - 1) * self.full))
                    if len(ds) == 1 and ds[0].kind == "asg" and ds[0].stmt.src.op == "c":
                        fmt = self.const_string(ds[0].stmt.src.aux)
                if fmt is not None and any(k in (name or "") for k in ("printf", "scanf", "syslog", "warn", "err")):
                    lay += [(8, 8) if k == "d" else (4, None) for k in protos.printf_kinds(fmt) if k in ("i", "d")]
                else:
                    return None
        elif ci is not None and ci.get("layout"):
            lay = list(ci["layout"])
        if not lay or not any(sz == 8 or kind for sz, kind in lay):
            return None
        for k, (size, _kind) in enumerate(lay):                              # 승격된 칸의 크기가 인자 폭과 어긋나면 기존 방식에 맡긴다
            off = sp + sum(z for z, _ in lay[:k])
            pm = self.slots["promoted"].get(off)
            if pm is not None and pm != size:
                return None
        return lay

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
        self.ret_fp = None
        x87 = getattr(self.lifter, "x87_used", False)
        if self.arch == "x86" and x87:                         # x87: ret 직전 스택 깊이가 1 이면 반환값이 부동소수점이라고 확정(리프터가 x87ret 에 대입해 둔다)
            fpk = self._fp_return_kind(flow)
            if fpk:
                self.ret_fp = fpk
                self.returns_value = rv = True
        if self.arch in ("x64", "arm64"):
            fpk = self._fp_return_kind(flow)
            if fpk:
                pf = self._ret_pure(flow, self.abi.fp_ret)
                pi = self._ret_pure(flow, self.abi.ret)
                used = self.callers_use_fp_result()
                if used is None and rv and pi is not None:
                    uint = self.callers_use_result()
                    if uint is True and not self.callers_use_fp_result():
                        used = False
                if used is True:
                    choose = True
                elif used is False and rv and pi is not None:
                    choose = False
                elif not rv or pi is None:
                    choose = True
                elif pf and not pi:
                    choose = True
                elif pi and not pf:
                    choose = False
                else:
                    fa = max((d.stmt.addr for t in self._ret_terms() for d in flow.reaching_at(t, self.abi.ret) if d.kind in ("asg", "unk")), default=-1)
                    ff = max((d.stmt.addr for t in self._ret_terms() for d in flow.reaching_at(t, self.abi.fp_ret)
                              if d.kind in ("asg", "unk", "call") and d.stmt is not None), default=-1)
                    choose = ff > fa
                if choose:
                    self.ret_fp = fpk
                    self.returns_value = rv = True
        for b in self.blocks.values():
            if b.term.k == "ret":
                if self.ret_fp:
                    if self.arch == "x86":
                        b.term.src = loc(self.abi.fp_ret, self.lifter.W)
                        continue
                    x0 = loc(self.abi.fp_ret, 8)
                    b.term.src = x0 if self.ret_fp == 8 else ir.trunc(x0, 4)
                else:
                    b.term.src = loc(self.abi.ret, self.full) if rv else None

    def _def_uses(self, flow):
        """정의 번호 → [(그 정의를 읽는 문장, 읽는 위치 이름)]."""
        m = getattr(flow, "_use_map", None)
        if m is None:
            m = {}
            for a in flow.graph.rpo:
                b = self.blocks[a]
                for s in b.stmts + [b.term]:
                    for name, mask_ in s.meta["uses"].items():
                        for i in bits_of(mask_):
                            m.setdefault(i, []).append((s, name))
            flow._use_map = m
        return m

    def _ret_pure(self, flow, reg):
        """ret 에 닿는 reg 의 실제 정의들이 모두 '결과 사슬'(자기 자신을 갱신하거나 ret 으로만 흐름)이면 True,
        조건·주소·호출 인자·다른 레지스터 계산에 쓰이면 False, 실제 정의가 없으면 None. 반환값은 이런 보조 값(반복 변수 등)이 아니라 결과 사슬 쪽이다."""
        uses = self._def_uses(flow)
        defs = [d for t in self._ret_terms() for d in flow.reaching_at(t, reg) if d.kind in ("asg", "unk", "call") and d.stmt is not None]
        if not defs:
            return None

        def flag_dead(stmt):
            ds = stmt.meta["defs"]
            return bool(ds) and all(not uses.get(x.id) for x in ds)

        def pure(d, depth):
            for (u, name) in uses.get(d.id, ()):
                if u.k == "ret":
                    continue
                if u.k == "asg" and u.dst.op == "loc":
                    dn = u.dst.aux
                    if dn == reg:
                        continue
                    if dn == FLAGS:
                        if flag_dead(u):
                            continue
                        return False
                    if dn.startswith("t") and dn[1:].isdigit() and depth < 3:
                        if all(pure(x, depth + 1) for x in u.meta["defs"]):
                            continue
                    return False
                return False
            return True

        return all(pure(d, 0) for d in defs)

    def _ret_terms(self):
        return [b.term for b in self.blocks.values() if b.term.k == "ret"]

    def _fp_return_kind(self, flow):
        """xmm0 의 하위 조각을 ret 직전에 실제로 쓰는 정의가 있으면 부동소수점 반환으로 보고 그 폭(4·8)을 돌려준다. 없으면 None."""
        found = False
        kind = None
        for t in self._ret_terms():
            for d in flow.reaching_at(t, self.abi.fp_ret):
                if d.kind == "call":
                    info = d.stmt.src.aux or {}
                    rk = info.get("ret_fp")
                    if rk:
                        found = True
                        kind = kind or rk
                elif d.kind in ("asg", "unk") and d.stmt is not None:
                    found = True
                    kind = kind or self._fp_def_width(flow, d, 0)
        if not found:
            return None
        if kind:
            return kind
        if self.arch == "x86":
            return self.lifter.W                                                   # x87: ret 직전 스택에 값이 있으면 반환값이다
        return 8 if self.callers_use_fp_result() is True else None                  # 증거가 없으면 호출자가 xmm0 을 읽을 때만

    def _fp_def_width(self, flow, d, depth):
        """정의 d 의 값이 float(4)·double(8)임을 알려 주는 증거(연산·읽기·복사 경로). 모르면 None."""
        if d.kind != "asg" or d.stmt is None or depth > 6:
            return None
        src = d.stmt.src
        if src.op == "ins" and src.aux == 4:
            return 4
        if src.op == "zext" and src.args[0].sz == 4:
            src = src.args[0]
        if src.op in ir.FOPS2 | ir.FOPS1 | ir.FOPS3 | {"i2f", "f2f"}:
            return src.sz
        if src.op == "mem" and src.sz in (4, 8):
            return src.sz
        if src.op == "c" and src.sz in (4, 8) and src.aux != 0:                      # 0 은 pxor 로도 만들어지므로 폭의 증거가 못 된다
            return src.sz
        if src.op in ("loc", "xor", "and", "trunc", "zext", "ins", "or", "ite"):
            names = [n.aux for n in ir.walk(src) if n.op == "loc"]
            for nm in names:
                for dd in flow.reaching(d.stmt, nm):
                    k = self._fp_def_width(flow, dd, depth + 1)
                    if k:
                        return k
        return None

    def _fp_ret_names(self) -> set:
        return {"xmm0"} if self.arch == "x64" else {"v0", "d0", "s0", "q0"}

    def callers_use_fp_result(self):
        """호출하는 쪽이 xmm0 을 읽는지: True/False, 호출자를 모르면 None."""
        try:
            xs = [x for x in self.prog.xrefs_to(self.addr) if x["kind"] == "call"]
        except Exception:                                   # noqa: BLE001
            return None
        if not xs:
            return None
        cs = self.prog.dis.cs
        for x in xs[:20]:
            site = x["from"]
            code = self.prog.dis.read(site, 96)
            for ins in list(cs.disasm(code, site))[1:9]:
                reads, writes = set(), set()
                for op in ins.operands:
                    if op.type == CS_OP_REG_:
                        n = cs.reg_name(op.reg)
                        if op.access & 1:
                            reads.add(n)
                        if op.access & 2:
                            writes.add(n)
                if reads & self._fp_ret_names():
                    return True
                if writes & self._fp_ret_names():
                    break
                if ins.mnemonic.startswith(("j", "ret", "call")):
                    break
        return False

    def callers_use_result(self):
        """호출하는 쪽이 반환 레지스터를 읽는지로 값을 반환하는 함수인지 판단한다. 호출자를 모르면 None."""
        try:
            xs = [x for x in self.prog.xrefs_to(self.addr) if x["kind"] == "call"]
        except Exception:                                   # noqa: BLE001
            return None
        if not xs:
            return None
        prog = self.prog
        abi = self.abi
        names = abi.ret_reads
        strip = ("bnd ", "notrack ")
        for x in xs[:20]:
            site = x["from"]
            code = prog.dis.read(site, 96)
            cs = prog.dis.cs_at(site) if self.arch == "arm" else prog.dis.cs
            insns = list(cs.disasm(code, site))[1:9]
            for ins in insns:
                reads, writes = set(), set()
                try:
                    for rid in list(ins.regs_read):
                        reads.add(cs.reg_name(rid))
                except Exception:                                # noqa: BLE001 — 코드가 아닌 바이트(SKIPDATA)
                    break
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
                if self._is_call_mn(mn):
                    break                                        # 다음 호출이 반환 레지스터를 덮어쓴다
                if self._is_exit_mn(mn, ins):
                    return True                                  # 값이 그대로 위 호출자에게 넘어가거나 분기 뒤에서 쓰일 수 있다
        return False

    _ARM_CC_RE = r"(eq|ne|hs|cs|lo|cc|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)?(\.[wn])?"

    def _is_call_mn(self, mn: str) -> bool:
        if self.arch == "arm":                                    # 'blt' 는 bl 이 아니라 b + lt 이므로 접두사 비교로는 가를 수 없다
            return re.fullmatch(r"blx?" + self._ARM_CC_RE, mn) is not None
        return any(mn.startswith(c) for c in self.abi.call_mn)

    def _is_exit_mn(self, mn: str, ins) -> bool:
        """함수를 끝내거나 갈라지는 명령(반환 레지스터 값이 그대로 넘어가거나 분기 뒤에서 쓰일 수 있다)."""
        if self.arch == "arm":
            if re.fullmatch(r"(b|bx|cbz|cbnz)" + self._ARM_CC_RE, mn):
                return True
            return re.match(r"(pop|ldm)", mn) is not None and "pc" in ins.op_str
        return mn.endswith("ret") or any(mn.startswith(c) for c in self.abi.ret_mn) or mn.startswith(self.abi.branch_prefix)

    # ---- 단계 4: 웹·전파 --------------------------------------------------------------
    def dataflow_pass(self):
        self.abi = abi_for(self.arch, self.bits, self.model.fmt, getattr(self, "thumb", False))
        self.prebind_calls()
        flow = Flow(self.blocks, self.graph, self.abi, self.out_slots, self.slots["promoted"])
        flow.run()
        if self.bind_calls(flow):                                 # 호출 반환값이 xmm0 으로 바뀌었으면 반환 판정이 새 분석을 보게 한다
            flow = Flow(self.blocks, self.graph, self.abi, self.out_slots, self.slots["promoted"])
            flow.run()
        self.bind_rets(flow)
        flow = Flow(self.blocks, self.graph, self.abi, self.out_slots, self.slots["promoted"])
        flow.run()
        again = self.drop_dead_flag_defs(flow)
        again = self.fold_partial_defs(flow) or again          # 부분 레지스터 쓰기가 옛 값을 읽는 것처럼 보이는 문제를 풀고 다시 분석
        if again:
            flow = Flow(self.blocks, self.graph, self.abi, self.out_slots, self.slots["promoted"])
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
                    if parent is not None and parent.op == "and" and any(o.op == "c" and o.aux <= ir.mask(cand[i]) for o in parent.args):
                        continue                                  # `r & 63` 처럼 작은 마스크로 가린 사용은 아래쪽 바이트만 본다
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
        self.variadic = self._strip_va_prologue(pw)
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
        if self.variadic:                                  # va_list 가 가리키는 스택 인자 영역은 매개변수가 아니다
            self.mem_params = {}
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
        if self.full == 4:                                  # 32비트에서 8바이트 스택 인자(double·long long)는 4바이트 칸 둘을 차지한다: 뒤 칸의 자리표시는 매개변수가 아니다
            covered = {i + 1 for w, i in params if w is not None and webs[w].sz == 8}
            params = [(w, i) for w, i in params if not (w is None and i in covered and i not in self.mem_params)]
        self.params = params
        self.infer_fp_params(used)

    def _strip_va_prologue(self, pw) -> bool:
        """가변 인자 함수의 프롤로그(`test al,al` 뒤에서 rdi·rsi·… 와 xmm0..7 을 레지스터 저장 영역에 쏟아 두는 저장문)를 지운다.
        그 값들은 매개변수가 아니라 `va_arg` 가 나중에 꺼낼 자리이므로, 지우지 않으면 쓰이지 않는 인자 레지스터가 모두 매개변수로 잡힌다.
        xmm 저장이 네 개 이상 보일 때만(가변 인자 프롤로그의 특징) 지우고 True 를 돌려준다."""
        if self.arch != "x64" or self.abi.name != "sysv":
            return False
        entry_webs = set(pw.values())
        flow = self.flow
        xw = {flow.web_of_def[d.id] for n, d in flow.entry_def.items() if n.startswith("xmm")}
        stores = []
        nx = 0
        for b in self.blocks.values():
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.src.op == "var":
                    if s.src.aux in xw:
                        nx += 1
                        stores.append(s)
                    elif s.src.aux in entry_webs:
                        stores.append(s)
        if nx < 8:                                        # xmm0..7 의 하위·상위 조각 저장(조각이 둘이므로 최소 8개 이상)
            return False
        dead = {id(s) for s in stores}
        for b in self.blocks.values():
            b.stmts = [s for s in b.stmts if id(s) not in dead]
        return True

    def fp_operand_kinds(self) -> dict:
        """웹 → 그 웹이 부동소수점 연산의 피연산자로 쓰인 폭(4·8). 연산에 안 쓰이면 들어 있지 않다."""
        kinds: dict = {}

        fmem: set = set()                                  # 부동소수점으로 읽고 쓰이는 메모리 칸

        def mark(e, k):
            if e.op == "trunc" and e.args[0].op == "var":
                kinds.setdefault(e.args[0].aux, e.sz)
            elif e.op == "var":
                kinds.setdefault(e.aux, k)
            elif e.op == "mem":
                fmem.add(e)

        for b in self.blocks.values():
            for s in b.stmts + [b.term]:
                for ex in stk_mod.stmt_exprs(s):
                    for n in ir.walk(ex):
                        if n.op in ir.FOPS2 or n.op in ir.FCMP_OPS:
                            sz = n.sz if n.op in ir.FOPS2 else n.args[0].sz
                            mark(n.args[0], sz)
                            mark(n.args[1], sz)
                        elif n.op in ir.FOPS1:
                            mark(n.args[0], n.sz)
                        elif n.op in ir.FOPS3:
                            for a_ in n.args:
                                mark(a_, n.sz)
                        elif n.op in ("f2i", "f2f"):
                            mark(n.args[0], n.args[0].sz)
                        elif n.op == "call":
                            ak = (n.aux or {}).get("arg_kinds") or ()
                            for a_, k_ in zip(n.args[1:], ak):          # x86-32: 스택으로 넘기는 실수 인자
                                if k_:
                                    mark(a_, k_)
        for b in self.blocks.values():                       # 인자를 메모리 칸에 복사해 두었다가 그 칸을 실수로 쓰는 경우(-O0): 복사원도 실수
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "mem" and s.src.op == "var" and s.dst in fmem:
                    kinds.setdefault(s.src.aux, s.dst.sz)
        return kinds

    def infer_fp_params(self, used):
        """xmm 인자 레지스터(SysV 는 xmm0..7, Windows x64 는 위치별 xmm0..3)로 들어오는 부동소수점 매개변수.
        정수 매개변수(self.params)와는 따로 두고, 시그니처를 만들 때만 뒤에 붙인다."""
        self.fp_params = []                      # [(웹 또는 None, xmm 번호, 폭)]
        self.fp_param_kinds = {}
        self.fp_placeholders = {}
        self.fp_entries = []                     # 시그니처용 (웹 또는 None, 번호)
        if self.arch not in ("x64", "arm64") or getattr(self, "variadic", False):
            return
        flow = self.flow
        kinds = self.fp_operand_kinds()
        cnt = self.abi.fp_nargs
        sequential = self.abi.name != "win64"                 # SysV·AAPCS64 는 정수·실수 인자 레지스터를 따로 센다
        fpr = self.abi.fpr
        xw = {}
        for k in range(cnt):
            d = flow.entry_def.get(f"{fpr}{k}")
            if d is not None:
                w = flow.web_of_def[d.id]
                if w in used and self.webs[w].entry and not self.webs[w].param:
                    xw[k] = w
        if not xw:
            return
        n_int = len(self.params)
        if sequential:
            top = max(xw)
            for k in range(top + 1):
                w = xw.get(k)
                kind = kinds.get(w, 8) if w is not None else 8
                self.fp_params.append((w, k, kind))
        else:                                    # Windows x64: 위치가 정수·실수 인자 레지스터를 함께 정한다
            int_used = {i for i, (w, _i) in enumerate(self.params) if w is not None}
            top = max(max(xw), n_int - 1)
            for k in range(top + 1):
                if k in int_used or k >= cnt:
                    continue
                w = xw.get(k)
                if w is not None:
                    self.fp_params.append((w, k, kinds.get(w, 8)))
            n_int = 0
        for pos, (w, k, kind) in enumerate(self.fp_params):
            idx = (n_int + pos) if sequential else k
            if w is not None:
                self.webs[w].param = idx
                self.fp_param_kinds[w] = kind
            else:
                self.fp_placeholders[idx] = kind
            self.fp_entries.append((w, idx))

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
        if not self.returns_value or getattr(self, "ret_fp", None):
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
        r = Renderer(self, ast, self.params + self.fp_entries, self.ret_info())
        lines = r.build()
        return r, lines

    WIDE_HELPERS = ("__aeabi_l", "__aeabi_ul", "__aeabi_d2l", "__aeabi_l2", "__ashldi3", "__lshrdi3", "__ashrdi3", "__muldi3", "__divdi3",
                    "__udivdi3", "__moddi3", "__umoddi3", "__divmoddi4")

    def _uses_wide(self) -> bool:
        """32비트 구조에서 64비트 값(레지스터 쌍)을 다루는 흔적: adc·sbc 연쇄, 64비트 곱셈누적, 64비트 연산 도우미 호출."""
        if getattr(self.lifter, "wide", False):
            return True
        for b in self.blocks.values():
            for s in b.stmts:
                if s.k == "call":
                    name = self.callee_name(s.src.args[0])
                    if name and name.startswith(self.WIDE_HELPERS):
                        return True
        return False

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
        if getattr(self.lifter, "x87_used", False):
            note = ("x87 부동소수점 연산은 80비트 확장 정밀도로 계산되므로, 이 의사코드를 SSE 로 컴파일하면 double 연산과 마지막 자리에서 달라질 수 있습니다. " + note)
        if self._uses_wide():
            note = "64비트 정수 연산(자리올림 연쇄·64비트 곱셈 도우미 호출)이 있어, 의사코드에는 하위 32비트만 나타날 수 있습니다. " + note
            inc.append("64비트 연산")
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
        if sign == "f":                                    # 부동소수점으로 읽기·쓰기: 정수 칸을 형 변환으로 본다
            return f"*({itype(sz, 'f')} *){self.addr(off)}", "f", PREC["unary"]
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
