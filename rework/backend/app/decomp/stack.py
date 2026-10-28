"""스택 프레임 분석.

1) track_stack — sp·bp 가 '진입 시 sp + 상수' 인 곳을 흐름 따라 추적해, 식 안의 레지스터를 `stk(off)` 로 바꾼다.
   그러면 모든 스택 접근이 `mem(stk(off))` 꼴로 정규화된다(프레임 포인터가 있든 없든, push/pop 이 있든 없든 같다).
2) promote_slots — 주소가 밖으로 새지 않는 스칼라 슬롯을 레지스터처럼 다루는 위치('slot:off')로 승격한다.
   주소를 넘기거나(&x) 배열처럼 산술에 쓰이는 슬롯은 승격하지 않고 '메모리 변수'로 남긴다(의미를 그대로 보존).
"""
from __future__ import annotations

from . import ir
from .ir import X, S, C, loc, stk, mem, subst, to_signed



def map_stmt(s: S, f) -> None:
    """문장 안의 모든 식을 f(식) → 식 으로 바꾼다(제자리)."""
    k = s.k
    if k == "asg":
        if s.dst.op == "mem":
            s.dst = ir.rebuild(s.dst, (f(s.dst.args[0]),))
        s.src = f(s.src)
    elif k == "call":
        s.src = f(s.src)
    elif k in ("cj", "ij", "sw", "ret") and s.src is not None:
        s.src = f(s.src)


def stmt_exprs(s: S):
    """문장이 읽는 식들(대입 대상 메모리의 주소 포함)."""
    k = s.k
    if k == "asg":
        if s.dst.op == "mem":
            yield s.dst.args[0]
        yield s.src
    elif k == "call":
        yield s.src
    elif k in ("cj", "ij", "sw", "ret") and s.src is not None:
        yield s.src


# ---- 1. 심볼릭 sp 추적 ----------------------------------------------------------------
def _aeval(e: X, env: dict):
    op = e.op
    if op == "stk":
        return e.aux
    if op == "loc":
        return env.get(e.aux)
    if op == "add" and e.args[1].op == "c":
        v = _aeval(e.args[0], env)
        return None if v is None else v + to_signed(e.args[1].aux, e.sz)
    return None


class StackTracker:
    def __init__(self, blocks: dict, graph, entry: int, abi, call_pop=None):
        self.blocks = blocks
        self.graph = graph
        self.entry = entry
        self.abi = abi
        self.sp = abi.sp
        self.outs: dict = {}
        self.call_pop = call_pop            # 호출문 → 피호출자가 치우는 인자 바이트(stdcall). 모르면 0

    def _transfer(self, b, env: dict, rewrite: bool):
        def sub_loc(e):
            if e.op == "loc" and e.aux in env:
                return stk(env[e.aux], e.sz)
            return None

        for s in b.stmts + [b.term]:
            if rewrite:
                map_stmt(s, lambda e: subst(e, sub_loc))
                if s.k == "call":
                    s.extra = dict(s.extra) if isinstance(s.extra, dict) else {}
                    s.extra["sp"] = env.get(self.sp)
            if s.k == "asg" and s.dst.op == "loc":
                v = _aeval(s.src, env)
                if v is None:
                    env.pop(s.dst.aux, None)
                else:
                    env[s.dst.aux] = v
            elif s.k == "call":
                pop = self.call_pop(s) if self.call_pop is not None and self.sp in env else 0
                for r in list(env):
                    if r not in self.abi.callee_saved:
                        env.pop(r, None)
                if pop and self.sp in env:
                    env[self.sp] += pop
            elif s.k == "unk":
                for r in s.extra["defs"]:
                    env.pop(r, None)
        return env

    def _in_env(self, a):
        if a == self.entry:
            return {self.sp: 0}
        env = None
        for p in self.blocks[a].preds:
            po = self.outs.get(p)
            if po is None:
                continue
            env = dict(po) if env is None else {r: v for r, v in env.items() if po.get(r) == v}
        return env

    def run(self):
        rpo = self.graph.rpo
        for _ in range(60):
            changed = False
            for a in rpo:
                env = self._in_env(a)
                if env is None:
                    continue
                out = self._transfer(self.blocks[a], dict(env), False)
                if self.outs.get(a) != out:
                    self.outs[a] = out
                    changed = True
            if not changed:
                break
        frame = None
        for a in rpo:
            env = self._in_env(a)
            if env is None:
                env = {}
            b = self.blocks[a]
            if frame is None:
                e2 = dict(env)
                for s in b.stmts:
                    if s.k == "asg" and s.dst.op == "loc" and s.dst.aux in self.abi.fps:
                        v = _aeval(s.src, e2)
                        if v is not None:
                            frame = v
                            break
                    if s.k == "asg" and s.dst.op == "loc":
                        v = _aeval(s.src, e2)
                        if v is None:
                            e2.pop(s.dst.aux, None)
                        else:
                            e2[s.dst.aux] = v
            self._transfer(b, dict(env), True)
        return frame


def _addr_valued(e: X) -> bool:
    """식의 값이 스택 주소인가(메모리에서 읽어 온 값은 아니다)."""
    if e.op == "mem":
        return False
    if e.op == "stk":
        return True
    return any(_addr_valued(a) for a in e.args)


def drop_dead_bookkeeping(blocks: dict, abi) -> None:
    """sp·bp 갱신이나 스택 주소를 레지스터에 담았다가 다시 쓰이지 않는 대입을 지운다(슬롯의 주소 유출 판정이 부풀지 않게)."""
    while True:
        read = set(abi.arg_regs) | {abi.ret}                  # 호출 인자·반환값은 아직 묶이기 전이라 문장에 드러나지 않지만 읽힌다
        for b in blocks.values():
            for s in b.stmts + [b.term]:
                for e in stmt_exprs(s):
                    for n in ir.walk(e):
                        if n.op == "loc":
                            read.add(n.aux)
                if s.k == "unk":
                    read.update(s.extra["uses"])
        removed = False
        for b in blocks.values():
            keep = []
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "loc" and s.dst.aux not in read and (
                        s.dst.aux == abi.sp or s.dst.aux in abi.fps or _addr_valued(s.src)):
                    removed = True
                    continue
                keep.append(s)
            b.stmts = keep
        if not removed:
            return


# ---- 1b. 넓은 스택 인자 합치기 ------------------------------------------------------------
def merge_wide_params(blocks: dict, abi) -> bool:
    """x86-32 의 gcc -O0 은 double 인자를 `mov eax,[ebp+8]; mov [ebp-8],eax; mov eax,[ebp+12]; mov [ebp-4],eax` 처럼 4바이트씩 지역 칸에 복사한 뒤
    그 칸을 8바이트(`fld qword [ebp-8]`)로 읽는다. 이 복사 한 쌍을 8바이트 하나(`[ebp-8] = [ebp+8]` 8바이트)로 합쳐, 인자가 double 하나로 보이게 한다.
    지역 칸이 8바이트로 읽히고, 두 인자 칸이 나란하며, 복사에 쓴 레지스터가 그 뒤에 다시 쓰이지 않을 때만 바꾼다."""
    if abi.full != 4:
        return False
    base = abi.stack_arg_base
    wide_read = set()
    for b in blocks.values():
        for st in b.stmts + [b.term]:
            for e in stmt_exprs(st):
                for n in ir.walk(e):
                    if n.op == "mem" and n.sz == 8 and n.args[0].op == "stk" and n.args[0].aux < 0:
                        wide_read.add(n.args[0].aux)
    if not wide_read:
        return False

    def names_used(st):
        out = set()
        for e in stmt_exprs(st):
            for n in ir.walk(e):
                if n.op == "loc":
                    out.add(n.aux)
        return out

    def exposed_elsewhere(me, reg):
        """다른 블록이 reg 를 정의하기 전에 읽는 곳이 있나(이 블록 뒤로 값이 살아 있을 수 있다)."""
        for blk in blocks.values():
            if blk is me:
                continue
            for st in blk.stmts + [blk.term]:
                if reg in names_used(st):
                    return True
                if st.k == "asg" and st.dst.op == "loc" and st.dst.aux == reg:
                    break
        return False

    changed = False
    for b in blocks.values():
        st = b.stmts
        i = 0
        while i < len(st):
            s1 = st[i]
            if not (s1.k == "asg" and s1.dst.op == "mem" and s1.dst.sz == 4 and s1.dst.args[0].op == "stk" and s1.src.op == "loc"
                    and s1.dst.args[0].aux in wide_read):
                i += 1
                continue
            L = s1.dst.args[0].aux
            r1 = s1.src.aux

            def load_of(upto, reg):
                """upto 앞쪽에서 reg 를 마지막으로 정의한 문장이 `reg = [인자칸 4바이트]` 이면 (인덱스, 칸 오프셋)."""
                for k in range(upto - 1, -1, -1):
                    d = st[k]
                    if d.k == "asg" and d.dst.op == "loc" and d.dst.aux == reg:
                        if d.src.op == "mem" and d.src.sz == 4 and d.src.args[0].op == "stk" and d.src.args[0].aux >= base:
                            return k, d.src.args[0].aux
                        return None
                    if d.k in ("call", "unk"):
                        return None
                return None

            l1 = load_of(i, r1)
            if l1 is None:
                i += 1
                continue
            A = l1[1]
            j = None
            for k in range(i + 1, len(st)):
                d = st[k]
                if d.k in ("call", "unk"):
                    break
                if d.k == "asg" and d.dst.op == "mem" and d.dst.sz == 4 and d.dst.args[0].op == "stk" and d.dst.args[0].aux == L + 4 \
                        and d.src.op == "loc":
                    j = k
                    break
            if j is None:
                i += 1
                continue
            s2 = st[j]
            r2 = s2.src.aux
            l2 = load_of(j, r2)
            if l2 is None or l2[1] != A + 4:
                i += 1
                continue
            # 레지스터가 이 복사 뒤에 다시 읽히지 않는지: 두 적재문은 지우고, 저장문 둘은 8바이트 하나로
            def dead_after(k0, reg, skip):
                for k in range(k0 + 1, len(st)):
                    d = st[k]
                    if d is skip:
                        continue
                    if d.k == "asg" and d.dst.op == "loc" and d.dst.aux == reg and d.src.op == "ins" and d.src.args[0].op == "loc" \
                            and d.src.args[0].aux == reg and reg not in {n.aux for n in ir.walk(d.src.args[1]) if n.op == "loc"}:
                        return True                                    # `fnstsw ax` 같은 부분 쓰기는 옛 값을 보는 것처럼 보이지만 이 값을 읽는 것이 아니다
                    if reg in names_used(d):
                        return False
                    if d.k == "asg" and d.dst.op == "loc" and d.dst.aux == reg:
                        return True
                    if d.k == "call":
                        return reg in abi.clobbers                         # 호출이 호출자 보존 레지스터를 덮어쓴다(x86-32 cdecl 은 레지스터로 인자를 넘기지 않는다)
                    if d.k == "unk":
                        return False
                return not exposed_elsewhere(b, reg)

            if not (dead_after(l1[0], r1, s1) and dead_after(l2[0], r2, s2)):
                i += 1
                continue
            s1.dst = ir.mem(ir.stk(L, 4), 8)
            s1.src = ir.mem(ir.stk(A, 4), 8)
            drop = {id(s2), id(st[l1[0]]), id(st[l2[0]])}
            b.stmts = st = [d for d in st if id(d) not in drop]
            changed = True
            i = 0
    return changed


# ---- 2. 슬롯 승격 ---------------------------------------------------------------------
def slot_name(off: int) -> str:
    return f"slot:{off}"


def find_save_slots(blocks, entry, abi) -> set:
    """콜리 세이브드 레지스터를 진입 직후 저장했다가 되돌리는 칸(push rbx … pop rbx).
    저장은 진입 블록에서 그 레지스터를 건드리기 전의 `slot ← reg` 하나뿐이고, 그 칸의 다른 접근은 같은 레지스터로 되읽는 `reg ← slot` 뿐이다.
    이런 칸은 지역 변수의 주소가 새는 것과 무관하므로 구조체 `locals` 에 끌려 들어가지 않게 따로 가려낸다."""
    eb = blocks.get(entry)
    if eb is None:
        return set()
    cand: dict = {}                                    # 칸 → 레지스터
    written = set()
    for s in eb.stmts:
        if s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.src.op == "loc" and s.src.aux in abi.callee_saved \
                and s.src.sz == abi.full and s.src.aux not in written and s.dst.sz == abi.full and s.dst.args[0].aux not in cand:
            cand[s.dst.args[0].aux] = s.src.aux
        if s.k == "asg" and s.dst.op == "loc":
            written.add(s.dst.aux)
        elif s.k == "unk":
            written.update(s.extra.get("defs", ()))
    if not cand:
        return set()
    bad: set = set()

    def scan(e: X):
        for n in ir.walk(e):
            if n.op == "mem" and n.args[0].op == "stk" and n.args[0].aux in cand:
                bad.add(n.args[0].aux)
            elif n.op == "stk" and n.aux in cand:
                bad.add(n.aux)

    for b in blocks.values():
        for s in b.stmts + [b.term]:
            if s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.dst.args[0].aux in cand \
                    and s.src.op == "loc" and s.src.aux == cand[s.dst.args[0].aux]:
                if b is not eb:
                    bad.add(s.dst.args[0].aux)             # 진입 블록 밖에서 다시 저장하면 일반 변수다
                continue
            if s.k == "asg" and s.dst.op == "loc" and s.src.op == "mem" and s.src.args[0].op == "stk" and s.src.args[0].aux in cand \
                    and s.dst.aux == cand[s.src.args[0].aux]:
                continue
            for e in stmt_exprs(s):
                scan(e)
    return {o for o in cand if o not in bad}


def promote_slots(blocks: dict, abi, frame_top: int = 0, frame_known: bool = True, entry=None) -> dict:
    """반환: {'promoted': {off: size}, 'memvars': {off: {sizes}}, 'escapes': [(off, kind)], 'canary': {off}}"""
    stmts = [s for b in blocks.values() for s in b.stmts]
    terms = [b.term for b in blocks.values()]

    # 카나리(스택 보호기) 슬롯: 저장은 지우고 읽기는 canary 값으로 — 검사 분기가 상수로 접혀 사라진다
    canary = set()
    for s in stmts:
        if s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.src.op == "canary":
            canary.add(s.dst.args[0].aux)
    if canary:
        def deref_canary(e):
            if e.op == "mem" and e.args[0].op == "stk" and e.args[0].aux in canary:
                return X("canary", (), e.sz)
            return None
        for b in blocks.values():
            b.stmts = [s for s in b.stmts
                       if not (s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.dst.args[0].aux in canary)]
            for s in b.stmts + [b.term]:
                map_stmt(s, lambda e: subst(e, deref_canary))
        stmts = [s for b in blocks.values() for s in b.stmts]

    direct: dict = {}                      # off → {size, ...}
    escapes: list = []                     # (off, 'addr' | 'arith')

    arr_hints: dict = {}                   # 배열 기준 오프셋 → {(접근 크기, 색인 배율)}

    def scan(e: X, under_arith: bool = False):
        if e.op == "mem" and e.args[0].op == "add" and e.args[0].args[0].op == "stk":
            idx = e.args[0].args[1]
            sc = None
            if idx.op == "mul" and idx.args[1].op == "c":
                sc = idx.args[1].aux
            elif idx.op == "shl" and idx.args[1].op == "c":
                sc = 1 << idx.args[1].aux
            if sc is not None:
                arr_hints.setdefault(e.args[0].args[0].aux, set()).add((e.sz, sc))
        if e.op == "mem" and e.args[0].op == "stk":
            direct.setdefault(e.args[0].aux, set()).add(e.sz)
            return
        if e.op == "stk":
            escapes.append((e.aux, "arith" if under_arith else "addr"))
            return
        arith = e.op in ("add", "sub", "mul", "shl") and any(a.op == "stk" for a in e.args)
        for a in e.args:
            scan(a, arith)

    bookkeeping = ({abi.sp} | abi.fps) if frame_known else {abi.sp}      # 프레임 포인터를 쓰지 않는 함수의 rbp/ebp 는 범용 레지스터다
    raw_sp_reads = False
    for s in stmts + terms:
        if s.k == "asg" and s.dst.op == "loc" and s.dst.aux in bookkeeping and s.src.op == "stk":
            continue                                                  # 프레임 포인터 설정(mov rbp, rsp)은 주소 유출이 아니다
        for e in stmt_exprs(s):
            if any(n.op == "loc" and n.aux in bookkeeping for n in ir.walk(e)) and not (
                    s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.src.op == "loc"):
                raw_sp_reads = True                                   # sp/bp 값을 모르는 채 쓰는 곳(alloca·스택 정렬 등)
        if s.k == "asg" and s.dst.op == "mem":
            if s.dst.args[0].op == "stk":
                direct.setdefault(s.dst.args[0].aux, set()).add(s.dst.sz)
            else:
                scan(s.dst.args[0])
            scan(s.src)
        else:
            for e in stmt_exprs(s):
                scan(e)

    maxsz = {o: max(z) for o, z in direct.items()}
    memvars = set()
    for o, z in direct.items():                                  # 한 위치에 서로 다른 크기로 접근
        if len(z) > 1:
            memvars.add(o)
    offs = sorted(direct)
    for i, o in enumerate(offs):                                 # 구간이 겹치는 슬롯
        for p in offs[i + 1:]:
            if p >= o + maxsz[o]:
                break
            memvars.add(o)
            memvars.add(p)
    saves = find_save_slots(blocks, entry if entry is not None else next(iter(blocks)), abi)
    esc_pts = sorted({x for x, _ in escapes})
    for x, kind in escapes:
        # 주소를 받은 쪽이 몇 바이트를 만질지(구조체·배열 전체인지) 알 수 없으므로, 그 주소 위쪽의 지역 슬롯은 모두 메모리로 둔다
        for o in offs:
            if o + maxsz[o] > x and o < frame_top and o not in saves:
                memvars.add(o)
    if raw_sp_reads:                                             # 추적하지 못한 스택 접근이 섞여 있으면 승격하지 않는다
        memvars.update(offs)
    promoted = {o: maxsz[o] for o in offs if o not in memvars}
    if promoted:
        def to_slot(e):
            if e.op == "mem" and e.args[0].op == "stk" and e.args[0].aux in promoted:
                return loc(slot_name(e.args[0].aux), e.sz)
            return None
        for b in blocks.values():
            for s in b.stmts + [b.term]:
                if s.k == "asg" and s.dst.op == "mem" and s.dst.args[0].op == "stk" and s.dst.args[0].aux in promoted:
                    s.dst = loc(slot_name(s.dst.args[0].aux), s.dst.sz)
                map_stmt(s, lambda e: subst(e, to_slot))
    return {"promoted": promoted, "memvars": {o: direct[o] for o in memvars}, "escapes": escapes, "canary": canary,
            "arr_hints": arr_hints}
