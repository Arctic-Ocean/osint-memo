"""제어 흐름 그래프: 기본블록 구성, 지배(dominator) 트리, 자연 루프."""
from __future__ import annotations

from . import ir
from .ir import S, Block, C, loc, X

TERMS = ("jmp", "cj", "ret", "ij", "noret", "sw")


def build_blocks(insns, lo: int, hi: int, entry: int, full: int, ret_reg: str = "rax"):
    """insns: [(addr, size, [S...])] (주소 순). 반환: (blocks: {start: Block}, order: 만든 순서의 start 목록)."""
    leaders = {entry}
    for addr, size, stmts in insns:
        if not stmts:
            continue
        t = stmts[-1]
        if t.k in TERMS:
            nxt = addr + size
            leaders.add(nxt)
            if t.k == "jmp" and lo <= t.t < hi:
                leaders.add(t.t)
            elif t.k == "cj":
                if lo <= t.t < hi:
                    leaders.add(t.t)
                leaders.add(t.f)
            elif t.k == "sw":
                for _, tg in t.cases:
                    if lo <= tg < hi:
                        leaders.add(tg)
                if lo <= t.t < hi:
                    leaders.add(t.t)
    blocks: dict = {}
    order = []
    cur = None
    last_addr = entry
    for addr, size, stmts in insns:
        if addr in leaders:
            if cur is not None and cur.term is None:                 # 직전 블록이 종결자 없이 다음 블록으로 이어짐
                cur.term = S("jmp", last_addr, t=addr)
            cur = Block(addr)
            blocks[addr] = cur
            order.append(addr)
        if cur is None:
            continue
        last_addr = addr
        cur.end = addr + size
        for s in stmts:
            if s.k in TERMS:
                cur.term = s
            elif cur.term is None:
                cur.stmts.append(s)
    # 끝까지 종결자가 없는 마지막 블록(함수가 다음 코드로 흘러감) → 막는다
    for b in blocks.values():
        if b.term is None:
            b.term = S("noret", b.end)
    # 종결자 정리: 범위 밖 분기 → 꼬리 호출
    for b in list(blocks.values()):
        t = b.term
        if t.k == "jmp":
            if not (lo <= t.t < hi) or t.t not in blocks:
                b.stmts.append(_tail_call(t.addr, t.t, full, ret_reg))
                b.term = S("ret", t.addr)
                b.tail = True
        elif t.k == "cj":
            for attr in ("t", "f"):
                tg = getattr(t, attr)
                if tg not in blocks:
                    ext = Block(tg)
                    ext.stmts.append(_tail_call(t.addr, tg, full, ret_reg))
                    ext.term = S("ret", t.addr)
                    ext.tail = True
                    blocks[tg] = ext
                    order.append(tg)
    for b in blocks.values():
        b.succs = succs_of(b.term)
    prune(blocks, entry)
    return blocks, [o for o in order if o in blocks]


def _tail_call(addr, target, full, ret_reg="rax"):
    return S("call", addr, dst=loc(ret_reg, full),
             src=X("call", (C(target, full),), full, {"args": None, "tail": True}))


def succs_of(t) -> list:
    if t.k == "jmp":
        return [t.t]
    if t.k == "cj":
        return [t.t, t.f] if t.t != t.f else [t.t]
    if t.k == "sw":
        out = []
        for _, tg in t.cases:
            if tg not in out:
                out.append(tg)
        if t.t is not None and t.t not in out:
            out.append(t.t)
        return out
    return []


def prune(blocks, entry):
    """entry 에서 닿지 않는 블록을 지우고 간선을 다시 잇는다."""
    seen = set()
    stack = [entry]
    while stack:
        a = stack.pop()
        if a in seen or a not in blocks:
            continue
        seen.add(a)
        stack.extend(blocks[a].succs)
    for a in list(blocks):
        if a not in seen:
            del blocks[a]
    relink(blocks)


def relink(blocks):
    for b in blocks.values():
        b.preds = []
    for a, b in blocks.items():
        b.succs = [s for s in succs_of(b.term) if s in blocks]
        for s in b.succs:
            blocks[s].preds.append(a)


class Graph:
    """RPO·지배자·루프 정보를 한곳에 모은다."""

    def __init__(self, blocks: dict, entry: int):
        self.blocks = blocks
        self.entry = entry
        self.rpo = self._rpo()
        self.index = {a: i for i, a in enumerate(self.rpo)}
        self.idom = self._dominators()
        self._dom_children = None
        self.back_edges = [(a, s) for a in self.rpo for s in blocks[a].succs
                           if s in self.index and self.dominates(s, a)]
        self.loops = self._loops()

    def _rpo(self):
        seen, post = set(), []
        stack = [(self.entry, iter(self.blocks[self.entry].succs))]
        seen.add(self.entry)
        while stack:
            a, it = stack[-1]
            for s in it:
                if s not in seen and s in self.blocks:
                    seen.add(s)
                    stack.append((s, iter(self.blocks[s].succs)))
                    break
            else:
                post.append(a)
                stack.pop()
        post.reverse()
        return post

    def _dominators(self):
        idom = {self.entry: self.entry}
        idx = self.index
        changed = True
        while changed:
            changed = False
            for a in self.rpo[1:]:
                new = None
                for p in self.blocks[a].preds:
                    if p in idom:
                        new = p if new is None else self._intersect(p, new, idom)
                if new is not None and idom.get(a) != new:
                    idom[a] = new
                    changed = True
        return idom

    def _intersect(self, a, b, idom):
        idx = self.index
        while a != b:
            while idx[a] > idx[b]:
                a = idom[a]
            while idx[b] > idx[a]:
                b = idom[b]
        return a

    def dominates(self, a, b) -> bool:
        """a 가 b 를 지배(자신 포함)."""
        if a not in self.idom or b not in self.idom:
            return False
        while True:
            if a == b:
                return True
            nb = self.idom[b]
            if nb == b:
                return False
            b = nb

    def _loops(self):
        """헤더 → 루프 본체 블록 집합(같은 헤더로 가는 되돌이 간선들을 합친다)."""
        loops: dict = {}
        for tail, head in self.back_edges:
            body = loops.setdefault(head, {head})
            stack = [tail]
            while stack:
                n = stack.pop()
                if n in body:
                    continue
                body.add(n)
                stack.extend(self.blocks[n].preds)
        return loops

    def loop_depth(self):
        d = {a: 0 for a in self.rpo}
        for head, body in self.loops.items():
            for n in body:
                d[n] += 1
        return d
