"""데이터플로 분석: 도달 정의 → 웹(변수) 분리 → 식 전파·죽은 코드 제거.

위치(loc: 레지스터·플래그·스택 슬롯·임시값)를 '도달 정의' 분석으로 풀어, 같은 사용에 닿는 정의들을 한 변수(웹)로 묶는다.
이렇게 하면 SSA 의 φ 나 되돌림(out-of-SSA) 복사 없이도 서로 무관한 레지스터 재사용이 다른 변수로 갈라진다.
이후 정의가 하나뿐인 변수는 안전한 조건에서 사용처로 식을 밀어 넣는다(전파).
"""
from __future__ import annotations

from collections import defaultdict

from . import ir
from .ir import X, S, C, var, subst, walk, has_op
from .stack import map_stmt, stmt_exprs

FLAGS = "F"


class Abi:
    def __init__(self, arg_regs, ret, clobbers, full, name, sp, fps, callee_saved, stack_arg_base, ret_reads,
                 call_mn=("call",), ret_mn=("ret",), branch_prefix=("j",), fpr="xmm", fp_nargs=8):
        self.arg_regs = arg_regs
        self.ret = ret
        self.clobbers = set(clobbers)
        self.full = full
        self.name = name
        self.sp = sp                         # 스택 포인터 위치 이름
        self.fps = set(fps)                  # 프레임 포인터로 쓰이는 레지스터
        self.callee_saved = set(callee_saved)
        self.stack_arg_base = stack_arg_base  # 진입 시 sp 기준 첫 스택 인자의 오프셋
        self.ret_reads = set(ret_reads)      # 반환 레지스터를 가리키는 Capstone 이름들
        self.call_mn = call_mn
        self.ret_mn = ret_mn
        self.branch_prefix = branch_prefix
        self.fpr = fpr                       # 부동소수점 레지스터 위치 접두사(xmm·v)
        self.fp_nargs = fp_nargs             # 부동소수점 인자 레지스터 수
        self.fp_ret = f"{fpr}0"
        self.hardfp = False                  # ARM32: 실수 인자·반환을 VFP 레지스터로 주고받는가(AAPCS-VFP)


def xmm_lanes(lo: int, hi: int, prefix: str = "xmm", skip=()):
    """부동소수점·벡터 레지스터 번호 lo..hi-1 의 (하위·상위 64비트) 위치 이름들(x86 은 xmm, ARM64 는 v)."""
    out = set()
    for i in range(lo, hi):
        if i in skip:
            continue
        out.add(f"{prefix}{i}")
        out.add(f"{prefix}{i}h")
    return out


def vfp_regs(hard_only: bool = False):
    """ARM32 VFP 의 호출 중 값이 사라지는(caller-saved) 위치 이름들: d0–d7·d16–d31 와 s0–s15(= d0–d7 의 절반들). d8–d15(s16–s31) 는 보존된다."""
    out = {f"d{i}" for i in list(range(8)) + list(range(16, 32))}
    out |= {f"s{i}" for i in range(16)}
    return out


def is_fp_loc(name: str) -> bool:
    return (name.startswith("xmm") and name[3:].rstrip("h").isdigit()) or (name.startswith("v") and name[1:].rstrip("h").isdigit()) \
        or (name[:1] == "d" and name[1:].isdigit())


def loc_size(name: str, abi: "Abi") -> int:
    """위치 이름의 기본 폭(플래그 1바이트, xmm 한 조각 8바이트, 그 밖에는 레지스터 폭)."""
    if name == FLAGS or name == "FPF":
        return 1
    if name in ("dr0", "dr2"):                                    # ARM softfp: r0:r1 / r2:r3 에 담겨 오는 double 인자(진입 때만 쓰는 의사 위치)
        return 8
    if is_fp_loc(name):
        return 8
    return abi.full


def abi_for(arch: str, bits: int, fmt: str, thumb: bool = False, hardfp: bool = False) -> Abi:
    if arch == "x64":
        common = dict(sp="rsp", fps=("rbp",), callee_saved=("rbx", "rbp", "r12", "r13", "r14", "r15", "rsp"),
                      ret_reads=("rax", "eax", "ax", "al", "ah"))
        if fmt == "PE":
            return Abi(["rcx", "rdx", "r8", "r9"], "rax", {"rax", "rcx", "rdx", "r8", "r9", "r10", "r11", FLAGS} | xmm_lanes(0, 6), 8, "win64",
                       stack_arg_base=40, fp_nargs=4, **common)
        return Abi(["rdi", "rsi", "rdx", "rcx", "r8", "r9"], "rax",
                   {"rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11", FLAGS} | xmm_lanes(0, 16), 8, "sysv", stack_arg_base=8, **common)
    if arch == "arm64":
        return Abi([f"x{i}" for i in range(8)], "x0", {f"x{i}" for i in range(19)} | {"x30", FLAGS} | xmm_lanes(0, 32, "v", skip=range(8, 16)), 8,
                   "aapcs64", sp="sp", fps=("x29",), callee_saved={f"x{i}" for i in range(19, 30)} | {"sp"}, stack_arg_base=0,
                   ret_reads=("x0", "w0"), call_mn=("bl", "blr"), ret_mn=("ret",), branch_prefix=("b", "cb", "tb"), fpr="v", fp_nargs=8)
    if arch == "arm":
        abi = Abi(["r0", "r1", "r2", "r3"], "r0", {"r0", "r1", "r2", "r3", "r12", "lr", FLAGS, "FPF"} | vfp_regs(), 4,
                  "aapcs-vfp" if hardfp else "aapcs",
                  sp="sp", fps=("r7",) if thumb else ("r11",),
                  callee_saved={f"r{i}" for i in range(4, 12)} | {"sp"} | {f"d{i}" for i in range(8, 16)} | {f"s{i}" for i in range(16, 32)},
                  stack_arg_base=0, ret_reads=("r0",), call_mn=("bl", "blx"), ret_mn=("bx", "pop", "ldm"), branch_prefix=("b",),
                  fpr="d", fp_nargs=8)
        abi.hardfp = hardfp
        abi.fp_ret = "d0"
        return abi
    abi = Abi([], "eax", {"eax", "ecx", "edx", FLAGS} | xmm_lanes(0, 8), 4, "cdecl", sp="esp", fps=("ebp",),
              callee_saved=("ebx", "ebp", "esi", "edi", "esp"), stack_arg_base=4, ret_reads=("eax", "ax", "al", "ah"))
    abi.fp_ret = "x87ret"                       # x86-32 는 부동소수점 반환값이 st(0) 에 남는다(리프터가 ret 직전에 이 위치에 대입해 준다)
    return abi


class Def:
    __slots__ = ("id", "loc", "kind", "stmt", "sz")

    def __init__(self, id, loc, kind, stmt, sz):
        self.id, self.loc, self.kind, self.stmt, self.sz = id, loc, kind, stmt, sz


def use_names(s: S) -> set:
    names = set()
    for e in stmt_exprs(s):
        for n in walk(e):
            if n.op == "loc":
                names.add(n.aux)
    if s.k == "unk":
        names.update(s.extra["uses"])
    return names


def stmt_defs(s: S, abi: Abi) -> list:
    if s.k == "asg" and s.dst.op == "loc":
        return [(s.dst.aux, s.dst.sz, "asg")]
    if s.k == "call":
        out = []
        if s.dst is not None:
            out.append((s.dst.aux, s.dst.sz, "call"))
        clob = (s.extra or {}).get("clobbers")
        for c in sorted(clob if clob is not None else abi.clobbers):
            if s.dst is None or c != s.dst.aux:
                out.append((c, loc_size(c, abi), "clobber"))
        return out
    if s.k == "unk":
        out = [(r, loc_size(r, abi), "unk") for r in s.extra["defs"]]
        if s.extra.get("writes_flags"):
            out.append((FLAGS, 1, "unk"))
        return out
    return []


def bits_of(m: int):
    while m:
        low = m & -m
        yield low.bit_length() - 1
        m ^= low


class Flow:
    """도달 정의 분석."""

    def __init__(self, blocks: dict, graph, abi: Abi, promoted: dict | None = None, slot_sizes: dict | None = None):
        self.blocks = blocks
        self.graph = graph
        self.abi = abi
        self.promoted = promoted or {}          # 승격 슬롯 오프셋 → 크기(호출이 바깥 인자 영역을 소비한다고 모델링)
        self.slot_sizes = slot_sizes or {}      # 모든 승격 슬롯의 크기(진입 값을 가진 인자 칸이 8바이트일 수 있다)

    def run(self):
        blocks, rpo, abi = self.blocks, self.graph.rpo, self.abi
        self.defs: list = []
        locs = set()
        stmt_defs_list = {}
        for a in rpo:
            b = blocks[a]
            for s in b.stmts + [b.term]:
                locs.update(use_names(s))
                lst = []
                dl = stmt_defs(s, abi)
                if s.k == "call" and s.extra and s.extra.get("sp") is not None:
                    sp = s.extra["sp"]
                    for o, osz in self.promoted.items():
                        if sp <= o < sp + abi.full * 16:
                            dl.append((f"slot:{o}", osz, "clobber"))
                for (l, sz, kind) in dl:
                    locs.add(l)
                    d = Def(len(self.defs), l, kind, s, sz)
                    self.defs.append(d)
                    lst.append(d)
                stmt_defs_list[id(s)] = lst
        self.entry_def = {}
        for l in sorted(locs):
            sz = loc_size(l, abi)
            if l.startswith("slot:") and int(l[5:]) in self.slot_sizes:
                sz = self.slot_sizes[int(l[5:])]
            d = Def(len(self.defs), l, "entry", None, sz)
            self.defs.append(d)
            self.entry_def[l] = d
        self.mask_of_loc: dict = defaultdict(int)
        for d in self.defs:
            self.mask_of_loc[d.loc] |= 1 << d.id
        mol = self.mask_of_loc
        gen, kill = {}, {}
        for a in rpo:
            b = blocks[a]
            g = 0
            k = 0
            for s in b.stmts + [b.term]:
                for d in stmt_defs_list[id(s)]:
                    m = mol[d.loc]
                    k |= m
                    g = (g & ~m) | (1 << d.id)
            gen[a], kill[a] = g, k
        entry_bits = 0
        for d in self.entry_def.values():
            entry_bits |= 1 << d.id
        IN = {a: 0 for a in rpo}
        OUT = {a: 0 for a in rpo}
        IN[self.graph.entry] = entry_bits
        changed = True
        while changed:
            changed = False
            for a in rpo:
                b = blocks[a]
                if a == self.graph.entry:
                    inn = entry_bits
                    for p in b.preds:
                        inn |= OUT.get(p, 0)
                else:
                    inn = 0
                    for p in b.preds:
                        inn |= OUT.get(p, 0)
                out = gen[a] | (inn & ~kill[a])
                if inn != IN[a] or out != OUT[a]:
                    IN[a], OUT[a] = inn, out
                    changed = True
        self.IN = IN
        # 문장별 사용 → 도달 정의 마스크
        for a in rpo:
            b = blocks[a]
            R = IN[a]
            for s in b.stmts + [b.term]:
                names = use_names(s)
                s.meta = {"uses": {l: R & mol[l] for l in names}, "defs": stmt_defs_list[id(s)]}
                if s.k in ("call", "ret"):
                    s.meta["R"] = R
                for d in stmt_defs_list[id(s)]:
                    R = (R & ~mol[d.loc]) | (1 << d.id)

    def reaching_at(self, s: S, loc_name: str) -> list:
        """call·ret 문장 s 직전에 loc 에 닿는 정의들(문장이 그 loc 를 읽지 않아도 된다)."""
        m = s.meta["R"] & self.mask_of_loc.get(loc_name, 0)
        return [self.defs[i] for i in bits_of(m)]

    def reaching(self, s: S, loc_name: str) -> list:
        """문장 s 가 읽는 loc 에 닿는 정의들."""
        m = s.meta["uses"].get(loc_name)
        if m is None:
            return []
        return [self.defs[i] for i in bits_of(m)]


class Web:
    __slots__ = ("id", "loc", "sz", "entry", "extra_defs", "defs", "name", "param", "kind")

    def __init__(self, id, loc, sz):
        self.id, self.loc, self.sz = id, loc, sz
        self.entry = False
        self.extra_defs = 0          # 문장이 없는 정의(진입·호출 파괴)의 수
        self.defs = []
        self.name = None
        self.param = None
        self.kind = None


def build_webs(flow: Flow) -> dict:
    """도달 정의를 웹으로 묶고 IR 의 loc 를 var 로 바꾼다. 반환: {web id: Web}."""
    defs = flow.defs
    parent = list(range(len(defs)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    members = [1 << i for i in range(len(defs))]            # 루트별 구성원 비트마스크(이미 합친 비트는 다시 훑지 않는다)

    def union_mask(m):
        first = (m & -m).bit_length() - 1
        rep = find(first)
        rest = m & ~members[rep]
        while rest:
            low = rest & -rest
            o = low.bit_length() - 1
            rest ^= low
            ro = find(o)
            if ro == rep:
                continue
            parent[ro] = rep
            members[rep] |= members[ro]
            rest &= ~members[ro]

    for a in flow.graph.rpo:
        b = flow.blocks[a]
        for s in b.stmts + [b.term]:
            for l, m in s.meta["uses"].items():
                if m:
                    union_mask(m)
    webs: dict = {}
    web_of_def = {}
    flow.web_of_def = web_of_def
    for d in defs:
        r = find(d.id)
        w = webs.get(r)
        if w is None:
            w = webs[r] = Web(r, d.loc, d.sz)
        w.sz = max(w.sz, d.sz) if d.loc != FLAGS else 1
        w.defs.append(d)
        if d.kind == "entry":
            w.entry = True
            w.extra_defs += 1
        elif d.kind == "clobber":
            w.extra_defs += 1
        web_of_def[d.id] = r
    # 같은 loc 의 웹은 크기를 맞춘다(승격 슬롯·임시값은 정확한 크기 하나)
    for a in flow.graph.rpo:
        b = flow.blocks[a]
        for s in b.stmts + [b.term]:
            umap = {}
            for l, m in s.meta["uses"].items():
                first = next(bits_of(m), None)
                if first is not None:
                    umap[l] = web_of_def[first]
            f = lambda e, umap=umap: subst(e, lambda n: var(umap[n.aux], n.sz) if n.op == "loc" and n.aux in umap else None)
            map_stmt(s, f)
            ds = s.meta["defs"]
            if s.k == "asg" and s.dst.op == "loc":
                s.dst = var(web_of_def[ds[0].id], s.dst.sz)
            elif s.k == "call":
                if s.dst is not None:
                    s.dst = var(web_of_def[ds[0].id], s.dst.sz)
            elif s.k == "unk":
                s.extra["usevars"] = [var(umap[l], loc_size(l, flow.abi)) for l in sorted(umap)]
                s.extra["defvars"] = [var(web_of_def[d.id], d.sz) for d in ds]
    return webs
