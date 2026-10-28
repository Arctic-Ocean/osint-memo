"""제어 흐름 구조화: 기본블록 그래프 → if/else · while · do-while · break/continue · 남는 곳은 goto.

원칙은 '항상 맞게'다. 구조로 옮길 수 있는 모양(지배/후지배 관계로 분기가 다시 만나는 곳, 자연 루프)만 구조화하고,
그렇지 않은 간선은 라벨과 goto 로 남긴다. 그래서 어떤 그래프를 줘도 의미가 같은 코드가 나온다.

AST 노드(튜플):
 ('s', S)                        문장(대입·호출·알 수 없는 명령)
 ('if', cond, then, else)        ('while', cond|None, body)    ('dowhile', body, cond)
 ('break',) ('continue',) ('goto', addr) ('label', addr) ('ret', expr|None) ('noret',)
 ('switch', expr, [(값 목록, 본문)], 기본 본문)
"""
from __future__ import annotations

from . import ir
from .ir import S, X


def fold_conditions(blocks: dict, entry: int) -> bool:
    """&& · || 로 합칠 수 있는 조건 분기 쌍을 합친다(CFG 를 직접 고친다). 바뀌면 True."""
    changed_any = False
    changed = True
    while changed:
        changed = False
        for a in list(blocks):
            A = blocks.get(a)
            if A is None or A.term.k != "cj":
                continue
            for side in ("t", "f"):
                bid = getattr(A.term, side)
                if bid == a or bid == entry:
                    continue
                B = blocks.get(bid)
                if B is None or B.stmts or B.term.k != "cj" or B.preds != [a]:
                    continue
                other = A.term.f if side == "t" else A.term.t
                if other == bid:
                    continue
                ca, cb = A.term.src, B.term.src
                bt, bf = B.term.t, B.term.f
                new = None
                if side == "t":                     # A 가 참이면 B 로
                    if bf == other:                 # (ca && cb) → bt
                        new = (ir.land(ca, cb), bt, other)
                    elif bt == other:               # (ca && !cb) → bf
                        new = (ir.land(ca, ir.invert(cb)), bf, other)
                else:                               # A 가 거짓이면 B 로
                    if bt == other:                 # 참 → other, (ca || cb) → other, 아니면 bf
                        new = (ir.lor(ca, cb), other, bf)
                    elif bf == other:               # (ca || !cb) → other, 아니면 bt
                        new = (ir.lor(ca, ir.invert(cb)), other, bt)
                if new is None:
                    continue
                cond, t, f = new
                A.term = S("cj", A.term.addr, src=cond, t=t, f=f)
                del blocks[bid]
                changed = changed_any = True
                _relink(blocks)
                break
    return changed_any


def _strip(e):
    while e.op in ("zext", "sext", "trunc"):
        e = e.args[0]
    return e


def _same_leaf(blocks: dict, p: int, q: int) -> bool:
    """내용 없는 두 반환 블록(return 복제본)이 같은 값을 돌려주면 같은 목적지로 본다."""
    a, b = blocks.get(p), blocks.get(q)
    if a is None or b is None or a.stmts or b.stmts or a.term.k != "ret" or b.term.k != "ret":
        return False
    return a.term.src == b.term.src


def fold_switch_bounds(blocks: dict, entry: int) -> None:
    """`if (idx > N) default; else switch(idx)` 모양을 하나의 switch 로 합친다."""
    from .cfg import relink
    changed = True
    while changed:
        changed = False
        for a in list(blocks):
            X0 = blocks.get(a)
            if X0 is None or X0.term.k != "cj":
                continue
            c = X0.term.src
            if c.op not in ("ugt", "uge", "ule", "ult") or c.args[1].op != "c":
                continue
            inrange = X0.term.f if c.op in ("ugt", "uge") else X0.term.t
            dflt = X0.term.t if c.op in ("ugt", "uge") else X0.term.f
            Y = blocks.get(inrange)
            if Y is None or Y.stmts or Y.term.k != "sw" or Y.preds != [a] or inrange == a:
                continue
            if _strip(Y.term.src) != _strip(c.args[0]):
                continue
            if Y.term.t != dflt and not _same_leaf(blocks, Y.term.t, dflt):
                continue
            X0.term = S("sw", Y.term.addr, src=Y.term.src, cases=Y.term.cases, t=dflt)
            del blocks[inrange]
            relink(blocks)
            changed = True
            break


def _relink(blocks):
    from .cfg import relink
    relink(blocks)


def simplify_cfg(blocks: dict, entry: int) -> None:
    """내용 없는 jmp 만 있는 블록을 건너뛰고, 한 줄로 이어지는 블록을 합친다(전체를 한 번에 처리해 블록 수에 선형)."""
    from .cfg import relink, prune
    relink(blocks)
    folded = False                                       # 0) 조건이 상수인 분기(x87 fprem 되풀이 루프 등)는 가는 쪽만 남긴다
    for a, b in blocks.items():
        t = b.term
        if t.k == "cj" and t.src.op == "c":
            b.term = S("jmp", t.addr, t=t.t if t.src.aux else t.f)
            folded = True
    if folded:
        relink(blocks)
        prune(blocks, entry)
        relink(blocks)
    # 1) 내용 없는 jmp 블록 → 최종 목적지로 전달(체인·순환 안전)
    fwd = {}
    for a, b in blocks.items():
        if a != entry and not b.stmts and b.term.k == "jmp" and b.term.t != a:
            fwd[a] = b.term.t

    def resolve(t):
        seen = set()
        while t in fwd and t not in seen:
            seen.add(t)
            t = fwd[t]
        return t

    if fwd:
        for a, b in blocks.items():
            t = b.term
            if t.k == "jmp":
                nt = resolve(t.t)
                if nt != t.t:
                    b.term = S("jmp", t.addr, t=nt)
            elif t.k == "cj":
                nt, nf = resolve(t.t), resolve(t.f)
                if (nt, nf) != (t.t, t.f):
                    b.term = S("jmp", t.addr, t=nt) if nt == nf else S("cj", t.addr, src=t.src, t=nt, f=nf)
            elif t.k == "sw":
                t.cases = [(v, resolve(x)) for v, x in t.cases]
                if t.t is not None:
                    t.t = resolve(t.t)
        relink(blocks)
        for a in list(fwd):
            if a in blocks and not blocks[a].preds and a != entry:
                del blocks[a]
        relink(blocks)
    # 2) 단일 후속·단일 선행 블록 합치기
    for a in list(blocks):
        b = blocks.get(a)
        if b is None:
            continue
        while b.term.k == "jmp":
            t = b.term.t
            c = blocks.get(t)
            if c is None or t == a or t == entry or len(c.preds) != 1:
                break
            b.stmts = b.stmts + c.stmts
            b.term = c.term
            b.end = c.end
            del blocks[t]
            # c 의 후속들의 선행 목록에서 t 를 a 로 바꾼다
            for sx in c.succs:
                sb = blocks.get(sx)
                if sb is not None:
                    sb.preds = [a if p == t else p for p in sb.preds]
            b.succs = list(c.succs)
    relink(blocks)


class Loop:
    __slots__ = ("header", "exit", "cont", "body", "kind", "latch")

    def __init__(self, header, exit, cont, body, kind):
        self.header, self.exit, self.cont, self.body, self.kind = header, exit, cont, body, kind
        self.latch = None


class Structurer:
    def __init__(self, blocks: dict, graph, entry: int):
        self.blocks = blocks
        self.g = graph
        self.entry = entry
        self.visited: set = set()
        self.loops: list = []
        self.pending: list = []
        self.goto_targets: set = set()
        self.ipdom = self._postdominators()
        self.headers = set(graph.loops)
        self.case_next: list = []
        self.sw_depth: list = []                       # 열려 있는 switch 마다 그때의 루프 깊이(switch 안의 break 는 switch 만 빠져나가기 때문)
        self.check = lambda: None
        self._tick = 0

    # ---- 후지배자 --------------------------------------------------------------------
    def _postdominators(self):
        blocks = self.blocks
        EXIT = -1
        succs = {a: list(b.succs) or [EXIT] for a, b in blocks.items()}
        succs[EXIT] = []
        preds = {a: [] for a in succs}
        for a, ss in succs.items():
            for s in ss:
                preds[s].append(a)
        # EXIT 에서 역방향 DFS 후위 순서
        seen, order = set(), []
        stack = [(EXIT, iter(preds[EXIT]))]
        seen.add(EXIT)
        while stack:
            n, it = stack[-1]
            for p in it:
                if p not in seen:
                    seen.add(p)
                    stack.append((p, iter(preds[p])))
                    break
            else:
                order.append(n)
                stack.pop()
        order.reverse()
        idx = {n: i for i, n in enumerate(order)}
        ipd = {EXIT: EXIT}
        changed = True
        while changed:
            changed = False
            for n in order[1:]:
                new = None
                for s in succs[n]:
                    if s in ipd:
                        if new is None:
                            new = s
                        else:
                            a, b = s, new
                            while a != b:
                                while idx[a] > idx[b]:
                                    a = ipd[a]
                                while idx[b] > idx[a]:
                                    b = ipd[b]
                            new = a
                if new is not None and ipd.get(n) != new:
                    ipd[n] = new
                    changed = True
        return {a: (None if v == EXIT else v) for a, v in ipd.items() if a != EXIT}

    # ---- 진입점 ---------------------------------------------------------------------
    def run(self) -> list:
        seq = self.emit(self.entry, None)
        # goto 로만 닿는 블록을 함수 끝에 라벨과 함께 붙인다
        guard = 0
        while self.pending and guard < 10000:
            guard += 1
            t = self.pending.pop()
            if t in self.visited or t not in self.blocks:
                continue
            seq.extend(self.emit(t, None))
        return self.finish(seq)

    # ---- 블록 순회 ---------------------------------------------------------------------
    def edge_to(self, t, follow):
        """간선 t 를 따라갈 때: (노드 목록, 이어서 방출할 블록 또는 None)."""
        if t == follow:
            return [], None
        if self.case_next and t == self.case_next[-1]:
            return [("fallthrough",)], None
        if self.loops:
            L = self.loops[-1]
            if t == L.cont:
                return [("continue",)], None
            if t == L.exit:
                if self.sw_depth and self.sw_depth[-1] == len(self.loops):          # switch 안에서 루프를 벗어나는 break 는 C 에서 switch 만 끝내므로 goto 로
                    if t in self.blocks:
                        self.goto_targets.add(t)
                        if t not in self.visited:
                            self.pending.append(t)
                        return [("goto", t)], None
                return [("break",)], None
            for outer in self.loops[:-1]:                          # 바깥 루프의 계속·탈출 목표로 가는 간선은 goto 로 둔다
                if t == outer.cont or t == outer.exit:
                    if t in self.blocks:
                        self.goto_targets.add(t)
                        if t not in self.visited:
                            self.pending.append(t)
                        return [("goto", t)], None
        if t not in self.blocks:
            return [("noret",)], None
        return [], t

    def emit(self, b, follow):
        seq = []
        self._tick += 1
        if self._tick & 255 == 0:
            self.check()
        while True:
            if b is None or b == follow:
                break
            blk = self.blocks[b]
            if b in self.visited:
                self.goto(seq, b)
                break
            if b in self.headers and not any(L.header == b for L in self.loops):
                node, nxt = self.emit_loop(b, follow)
                seq.append(("label", b))
                seq.extend(node)
                b = nxt
                continue
            self.visited.add(b)
            seq.append(("label", b))
            for s in blk.stmts:
                seq.append(("s", s))
            t = blk.term
            if t.k == "ret":
                seq.append(("ret", t.src))
                break
            if t.k == "noret":
                seq.append(("noret",))
                break
            if t.k == "jmp":
                nodes, nxt = self.edge_to(t.t, follow)
                if nxt is not None and nxt in self.visited and not self.is_back_target(nxt):
                    self.goto(seq, nxt)
                    break
                seq.extend(nodes)
                if nxt is None:
                    break
                b = nxt
                continue
            if t.k == "cj":
                b = self.emit_cond(blk, seq, follow)
                continue
            if t.k == "sw":
                b = self.emit_switch(blk, seq, follow)
                continue
            if t.k == "ij":
                seq.append(("ijump", t.src, t.addr))
            else:
                seq.append(("comment", "해석하지 못한 분기"))
            break
        return seq

    def is_back_target(self, n):
        return False

    def goto(self, seq, t):
        if t in self.blocks:
            self.goto_targets.add(t)
            if t not in self.visited:
                self.pending.append(t)
            seq.append(("goto", t))
        else:
            seq.append(("noret",))

    def region_of(self, t, follow):
        """간선 t 로 시작하는 구간을 방출한다."""
        nodes, nxt = self.edge_to(t, follow)
        if nxt is None:
            return nodes
        if nxt in self.visited:
            out = list(nodes)
            self.goto(out, nxt)
            return out
        return nodes + self.emit(nxt, follow)

    def merge_point(self, blk, follow):
        m = self.ipdom.get(blk.start)
        if m is None or m == blk.start or m not in self.blocks:
            return None
        if self.loops:
            L = self.loops[-1]
            if m not in L.body:
                return None
        return m

    def emit_cond(self, blk, seq, follow):
        t = blk.term
        cond, tt, ff = t.src, t.t, t.f
        m = self.merge_point(blk, follow)
        if m is not None and m in self.visited and m != follow:
            m = None
        stop = m if m is not None else follow
        then_seq = self.region_of(tt, stop)
        else_seq = self.region_of(ff, stop)
        if not then_seq and else_seq:
            cond, then_seq, else_seq = ir.invert(cond), else_seq, then_seq
        seq.append(("if", cond, then_seq, else_seq, t.addr))
        if m is None:
            return None
        if m == follow:
            return None
        if self.case_next and m == self.case_next[-1]:
            seq.append(("fallthrough",))
            return None
        return m

    # ---- 루프 -----------------------------------------------------------------------------
    def emit_loop(self, h, follow):
        g = self.g
        body = g.loops[h]
        blk = self.blocks[h]
        latches = [a for a, s in g.back_edges if s == h]
        exits = []
        for u in body:
            for v in self.blocks[u].succs:
                if v not in body and v not in exits:
                    exits.append(v)
        t = blk.term
        # A. 머리에서 검사하는 while
        if t.k == "cj" and ((t.t in body) != (t.f in body)):
            cont_succ, exit_succ = (t.t, t.f) if t.t in body else (t.f, t.t)
            cond = t.src if t.t in body else ir.invert(t.src)
            L = Loop(h, exit_succ, h, body, "while")
            self.loops.append(L)
            self.visited.add(h)
            pre = [("s", s) for s in blk.stmts]
            inner = self.region_of(cont_succ, None)
            self.loops.pop()
            inner = self.strip_continue(inner)
            if pre:
                node = ("while", None, pre + [("if", ir.invert(cond), [("break",)], [], t.addr)] + inner, t.addr)
            else:
                node = ("while", cond, inner, t.addr)
            return [node], exit_succ if exit_succ in self.blocks else None
        # B. 아래에서 검사하는 do-while
        if len(latches) == 1:
            l = latches[0]
            lt = self.blocks[l].term
            if lt.k == "cj" and ((lt.t == h) != (lt.f == h)):
                other = lt.f if lt.t == h else lt.t
                if other not in body:
                    cond = lt.src if lt.t == h else ir.invert(lt.src)
                    L = Loop(h, other, l, body, "dowhile")
                    L.latch = l
                    self.loops.append(L)
                    inner = self.emit_dowhile_body(h, l)
                    self.loops.pop()
                    inner = self.strip_continue(inner)
                    return [("dowhile", inner, cond, lt.addr)], other if other in self.blocks else None
        # C. 일반 무한 루프 + break
        exit_b = exits[0] if exits else None
        L = Loop(h, exit_b, h, body, "forever")
        self.loops.append(L)
        inner = self.emit_body_from(h)
        self.loops.pop()
        inner = self.strip_continue(inner)
        return [("while", None, inner, blk.start)], exit_b if exit_b in self.blocks else None

    def emit_body_from(self, h):
        """머리 블록 자신부터(루프 재진입 검사 없이) 본문을 방출한다."""
        blk = self.blocks[h]
        self.visited.add(h)
        seq = [("label", h)] + [("s", s) for s in blk.stmts]
        t = blk.term
        if t.k == "ret":
            seq.append(("ret", t.src))
        elif t.k == "noret":
            seq.append(("noret",))
        elif t.k == "jmp":
            seq.extend(self.region_of(t.t, None))
        elif t.k == "ij":
            seq.append(("ijump", t.src, t.addr))
        elif t.k == "sw":                                         # switch 가 곧 루프 머리(`for (;;) switch (c) { … continue; }` 꼴)
            nxt = self.emit_switch(blk, seq, None)
            if nxt is not None:
                seq.extend(self.emit(nxt, None))
        elif t.k == "cj":
            then_seq = self.region_of(t.t, None)
            else_seq = self.region_of(t.f, None)
            c = t.src
            if not then_seq and else_seq:
                c, then_seq, else_seq = ir.invert(c), else_seq, then_seq
            seq.append(("if", c, then_seq, else_seq, t.addr))
        else:
            seq.append(("comment", "해석하지 못한 분기"))
        return seq

    def emit_dowhile_body(self, h, l):
        # h 에서 시작해 l 직전까지 방출(l 의 문장은 조건 앞에 붙인다)
        seq = []
        if h == l:
            blk = self.blocks[h]
            self.visited.add(h)
            seq.append(("label", h))
            seq.extend(("s", s) for s in blk.stmts)
            return seq
        seq.extend(self.emit(h, l))
        blk = self.blocks[l]
        self.visited.add(l)
        seq.append(("label", l))
        seq.extend(("s", s) for s in blk.stmts)
        return seq

    def strip_continue(self, seq):
        while seq and seq[-1] == ("continue",):
            seq = seq[:-1]
        return seq

    # ---- switch --------------------------------------------------------------------------
    def emit_switch(self, blk, seq, follow):
        t = blk.term
        m = self.merge_point(blk, follow)
        if m is not None and m in self.visited and m != follow:
            m = None
        stop = m if m is not None else follow
        groups = {}
        dflt = t.t
        for v, tg in t.cases:
            if dflt is not None and tg == dflt:                    # 기본 갈래와 같은 곳으로 가는 값(표의 빈 칸)은 따로 적지 않는다
                continue
            groups.setdefault(tg, []).append(v)
        if dflt is not None and dflt != stop:
            groups.setdefault(dflt, []).append("default")
        order = sorted(groups, key=lambda x: (x not in self.blocks, x))
        cases = []
        for i, tg in enumerate(order):
            nxt = order[i + 1] if i + 1 < len(order) else None
            if tg == stop:
                body = []
            else:
                self.case_next.append(nxt)
                self.sw_depth.append(len(self.loops))
                body = self.region_of(tg, stop)
                self.sw_depth.pop()
                self.case_next.pop()
            if not self._ends_flow(body) and not (body and body[-1][0] == "fallthrough") and (stop is not None or follow is not None
                                                                                          or nxt is not None):
                body = body + [("break",)]
            elif body and body[-1][0] == "fallthrough":
                body = body[:-1] + [("comment", "fallthrough")]
            cases.append((groups[tg], body))
        seq.append(("switch", t.src, self._tidy_cases(cases), [], t.addr))
        if m is None or m == follow:
            return None
        return m

    @staticmethod
    def _node_key(n):
        k = n[0]
        if k == "ret":
            return ("ret", n[1])
        if k in ("break", "continue", "noret"):
            return (k,)
        if k == "goto":
            return ("goto", n[1])
        if k == "s" and n[1].k in ("asg", "call"):
            return ("s", n[1].k, n[1].dst, n[1].src)
        return None

    def _private(self, body) -> bool:
        """이 case 본문의 라벨로 다른 곳에서 뛰어올 수 없다(선행 블록이 switch 하나뿐)."""
        return all(n[0] != "label" or len(self.blocks[n[1]].preds) <= 1 for n in body if n[0] == "label" and n[1] in self.blocks)

    @classmethod
    def _same_body(cls, a, b) -> bool:
        a = [n for n in a if n[0] != "label"]
        b = [n for n in b if n[0] != "label"]
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            kx, ky = cls._node_key(x), cls._node_key(y)
            if kx is None or kx != ky:
                return False
        return True

    def _tidy_cases(self, cases):
        """같은 동작을 하는 case 를 묶고(기본 분기와 같으면 그쪽으로 흡수) case 값 순서로 정렬한다.
        fallthrough 로 이어지는 case 들은 한 덩어리로 보고 순서를 바꾸지 않는다."""
        chains, cur = [], []
        for vals, body in cases:
            cur.append((vals, body))
            if self._ends_flow(body):
                chains.append(cur)
                cur = []
        if cur:
            chains.append(cur)
        merged = []
        for ch in chains:
            if len(ch) == 1:
                for m in merged:
                    if len(m) == 1 and self._same_body(m[0][1], ch[0][1]) and self._private(ch[0][1]):
                        m[0] = (m[0][0] + ch[0][0], m[0][1])
                        break
                else:
                    merged.append(list(ch))
            else:
                merged.append(ch)
        out = []
        for ch in merged:
            if len(ch) == 1 and "default" in ch[0][0]:
                ch[0] = (["default"], ch[0][1])

        def key(ch):
            vs = [v for vals, _ in ch for v in vals if v != "default"]
            has_default = any("default" in vals for vals, _ in ch)
            return (1 if has_default else 0, min(vs) if vs else 0)

        for ch in sorted(merged, key=key):
            out.extend(ch)
        return out

    @staticmethod
    def _ends_flow(seq):
        return bool(seq) and seq[-1][0] in ("break", "continue", "goto", "ret", "noret")

    # ---- 마무리: 쓰이지 않는 라벨 제거 ---------------------------------------------------------
    def finish(self, seq):
        used = set(self.goto_targets)
        seen_labels = set()

        def clean(nodes):
            out = []
            dead = False
            for n in nodes:
                k = n[0]
                if k == "label":
                    if n[1] in used and n[1] not in seen_labels:
                        seen_labels.add(n[1])
                        out.append(n)
                        dead = False                                 # 라벨은 goto 로 닿을 수 있다
                    continue
                if dead:
                    continue
                if k in ("ret", "goto", "break", "continue", "noret", "ijump"):
                    out.append(n)
                    dead = True
                    continue
                if k == "if":
                    th, el = clean(n[2]), clean(n[3])
                    if th and el and self._ends_flow(th):             # then 이 끝나는 흐름이면 else 를 밖으로 꺼낸다
                        out.append(("if", n[1], th, [], n[4]))
                        out.extend(el)
                    else:
                        out.append(("if", n[1], th, el, n[4]))
                elif k == "while":
                    out.append(("while", n[1], clean(n[2]), n[3]))
                elif k == "dowhile":
                    out.append(("dowhile", clean(n[1]), n[2], n[3]))
                elif k == "switch":
                    out.append(("switch", n[1], [(v, clean(b)) for v, b in n[2]], None if n[3] is None else clean(n[3]), n[4]))
                else:
                    out.append(n)
            return out

        return self.to_for(clean(seq))

    # ---- for 문 복원 ---------------------------------------------------------------------
    @staticmethod
    def _has_continue(nodes) -> bool:
        for n in nodes:
            k = n[0]
            if k == "continue":
                return True
            if k == "if" and (Structurer._has_continue(n[2]) or Structurer._has_continue(n[3])):
                return True
            if k == "switch" and any(Structurer._has_continue(b) for _, b in n[2]):
                return True
        return False

    def to_for(self, nodes):
        out = []
        for n in nodes:
            k = n[0]
            if k == "if":
                n = ("if", n[1], self.to_for(n[2]), self.to_for(n[3]), n[4])
            elif k == "while":
                n = ("while", n[1], self.to_for(n[2]), n[3])
            elif k == "dowhile":
                n = ("dowhile", self.to_for(n[1]), n[2], n[3])
            elif k == "switch":
                n = ("switch", n[1], [(v, self.to_for(b)) for v, b in n[2]], n[3], n[4])
            if n[0] == "while" and n[1] is not None and n[2] and out and out[-1][0] == "s":
                init, body = out[-1][1], n[2]
                last = body[-1]
                if (init.k == "asg" and init.dst.op == "var" and last[0] == "s" and last[1].k == "asg"
                        and last[1].dst.op == "var" and last[1].dst.aux == init.dst.aux
                        and last[1].src.op in ("add", "sub") and last[1].src.args[0] == init.dst
                        and last[1].src.args[1].op == "c"
                        and any(x.op == "var" and x.aux == init.dst.aux for x in ir.walk(n[1]))
                        and not self._has_continue(body[:-1])):
                    out.pop()
                    out.append(("for", init, n[1], last[1], body[:-1], n[3]))
                    continue
            out.append(n)
        return out
