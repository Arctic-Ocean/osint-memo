"""식 전파·죽은 코드 제거·변수 폭 줄이기.

웹으로 갈라진 변수는 정의가 하나뿐이면 '값'처럼 다룰 수 있다. 사용처가 하나뿐이거나 상수·복사인 정의는 사용처로 밀어 넣어
식으로 합치고, 어디서도 쓰이지 않는 정의는 지운다. 의미를 바꾸지 않도록 다음을 지킨다.
 - 메모리 읽기·호출을 품은 식은 같은 블록 안에서, 사이에 부작용 있는 문장이 없을 때만 옮긴다.
 - 정의가 둘 이상인 변수(루프로 갱신되는 값)를 피연산자로 가진 식은 같은 블록 안에서 그 변수가 다시 정의되지 않을 때만 옮긴다.
 - 다른 블록으로 옮기려면 정의 블록이 사용 블록을 지배해야 하고, 식은 순수하며 피연산자가 모두 단일 정의여야 한다.
"""
from __future__ import annotations

import re

from collections import defaultdict

from . import ir
from .ir import X, S, var, subst, walk, has_op, reduce_cc, is_flagsrc
from .stack import map_stmt, stmt_exprs

PURE_BAD = ("mem", "canary", "seg", "call", "opaque")


def used_vars(s: S):
    for e in stmt_exprs(s):
        for n in walk(e):
            if n.op == "var":
                yield n
    if s.k == "unk":
        yield from s.extra.get("usevars", ())


def def_vars(s: S):
    if s.k == "asg" and s.dst.op == "var":
        return [s.dst.aux]
    if s.k == "call" and s.dst is not None:
        return [s.dst.aux]
    if s.k == "unk":
        return [v.aux for v in s.extra.get("defvars", ())]
    return []


def is_impure(s: S) -> bool:
    if s.k == "asg":
        return s.dst.op == "mem"
    return s.k in ("call", "unk")


class Prop:
    check = staticmethod(lambda: None)

    def __init__(self, blocks: dict, graph, webs: dict):
        self.blocks = blocks
        self.graph = graph
        self.webs = webs

    # ---- 정보 수집 ------------------------------------------------------------------
    def collect(self):
        self.use_count = defaultdict(int)
        self.use_sites = defaultdict(list)
        self.def_count = defaultdict(int)
        self.def_site = {}
        self.seqs = {}
        for a in self.graph.rpo:
            b = self.blocks[a]
            seq = b.stmts + [b.term]
            self.seqs[a] = seq
            for idx, s in enumerate(seq):
                if s.k == "nop":
                    continue
                for n in used_vars(s):
                    self.use_count[n.aux] += 1
                    self.use_sites[n.aux].append((a, idx, s))
                for v in def_vars(s):
                    self.def_count[v] += 1
                    if s.k == "asg":
                        self.def_site[v] = (a, idx, s)

    def single_def(self, v) -> bool:
        w = self.webs[v]
        return self.def_count[v] + w.extra_defs == 1 and v in self.def_site

    def stable(self, v) -> bool:
        """값이 바뀌지 않는 변수: 정의가 하나뿐이거나, 들어올 때의 값(매개변수)을 한 번도 다시 쓰지 않는 경우."""
        if self.single_def(v):
            return True
        w = self.webs[v]
        return bool(w.entry) and self.def_count[v] == 0 and w.extra_defs == 1

    # ---- 안전 조건 --------------------------------------------------------------------
    def can_move(self, expr: X, d_site, u_site, reads_call=False) -> bool:
        da, di, ds = d_site
        ua, ui, us = u_site
        vs = {n.aux for n in walk(expr) if n.op == "var"}
        reads_mem = has_op(expr, PURE_BAD)
        if da == ua:
            if ui <= di:
                return False
            seq = self.seqs[da]
            between = [x for x in seq[di + 1:ui] if x.k != "nop"]
            for w in vs:
                if not self.stable(w):
                    for x in between:
                        if w in def_vars(x):
                            return False
            if reads_mem:
                for x in between:
                    if is_impure(x):
                        return False
            return True
        if reads_mem:
            return False
        if any(not self.stable(w) for w in vs):
            return False
        return self.graph.dominates(da, ua)

    # ---- 한 라운드 --------------------------------------------------------------------
    def round(self) -> bool:
        self.collect()
        changed = False
        touched = set()

        # (1) 플래그 소비: cc(코드, F) → 직접 비교식
        for a in self.graph.rpo:
            seq = self.seqs[a]
            for idx, s in enumerate(seq):
                if s.k == "nop" or id(s) in touched:
                    continue
                ccs = []
                for e in stmt_exprs(s):
                    for n in walk(e):
                        if n.op == "cc" and n.args[0].op == "var":
                            ccs.append(n)
                if not ccs:
                    continue
                repl = {}
                for n in ccs:
                    v = n.args[0].aux
                    if not self.single_def(v):
                        continue
                    d = self.def_site[v]
                    fl = d[2].src
                    if not is_flagsrc(fl):
                        continue
                    r = reduce_cc(n.aux, fl)
                    if r is None or d[2] is s:
                        continue
                    if self.can_move(r, d, (a, idx, s)):
                        repl[n] = r
                if repl:
                    map_stmt(s, lambda e, repl=repl: subst(e, lambda n: repl.get(n) if n.op == "cc" else None))
                    touched.add(id(s))
                    changed = True

        if changed:                       # 플래그를 풀면 피연산자 변수의 사용처가 늘므로, 사용 횟수를 다시 센 뒤 다음 라운드에서 전파한다
            for a in self.graph.rpo:
                b = self.blocks[a]
                b.stmts = [s for s in b.stmts if s.k != "nop"]
            return True

        # (2) 정의 → 사용처 전파
        for a in self.graph.rpo:
            seq = self.seqs[a]
            for idx, s in enumerate(seq):
                if s.k != "asg" or s.dst.op != "var" or id(s) in touched:
                    continue
                v = s.dst.aux
                if not self.single_def(v) or self.webs[v].entry:
                    continue
                src = s.src
                if is_flagsrc(src):
                    continue
                n = self.use_count[v]
                if n == 0:
                    continue
                if any(x.aux == v for x in walk(src) if x.op == "var"):
                    continue
                d = (a, idx, s)
                atom = src.op == "c" or (src.op == "var" and self.stable(src.aux)) \
                    or (src.op in ("zext", "sext", "trunc") and src.args[0].op == "c")
                if not atom and src.op == "var" and n > 1 and not self.webs[src.aux].entry:
                    # 복사 v = w (w 는 여러 번 정의되는 변수): 사용이 모두 같은 블록의 뒤쪽이고 그 사이 w 가 다시 정의되지 않으면 w 로 바꾼다
                    sites = self.use_sites[v]
                    if all(u[0] == a and u[1] > idx for u in sites):
                        last = max(u[1] for u in sites)
                        between = [x for x in seq[idx + 1:last] if x.k != "nop"]
                        if not any(src.aux in def_vars(x) for x in between) and not any(id(u[2]) in touched for u in sites):
                            for (_, _, u) in sites:
                                map_stmt(u, lambda e, v=v, src=src: subst(e, lambda x: src if x.op == "var" and x.aux == v else None))
                                touched.add(id(u))
                            s.k = "nop"
                            touched.add(id(s))
                            changed = True
                            continue
                if atom:
                    sites = self.use_sites[v]
                    if any(id(u[2]) in touched for u in sites):
                        continue
                    for (_, _, u) in sites:
                        map_stmt(u, lambda e, v=v, src=src: subst(e, lambda x: src if x.op == "var" and x.aux == v else None))
                        if u.k == "unk":
                            u.extra["usevars"] = [x for x in u.extra["usevars"] if x.aux != v]
                        touched.add(id(u))
                    s.k = "nop"
                    touched.add(id(s))
                    changed = True
                elif n == 1:
                    u_site = self.use_sites[v][0]
                    u = u_site[2]
                    if id(u) in touched or u.k == "unk":
                        continue
                    if not self.can_move(src, d, u_site):
                        continue
                    map_stmt(u, lambda e, v=v, src=src: subst(e, lambda x: src if x.op == "var" and x.aux == v else None))
                    s.k = "nop"
                    touched.add(id(s))
                    touched.add(id(u))
                    changed = True

        # (2b) 호출 결과 전파: v = f(...) 의 유일한 사용이 같은 블록의 바로 다음 부작용 지점이면 식으로 합친다
        for a in self.graph.rpo:
            seq = self.seqs[a]
            for idx, s in enumerate(seq):
                if s.k != "call" or s.dst is None or id(s) in touched:
                    continue
                v = s.dst.aux
                if self.def_count[v] + self.webs[v].extra_defs != 1:
                    continue
                if self.use_count[v] != 1 or (s.extra or {}).get("noinline"):
                    continue
                u_site = self.use_sites[v][0]
                ua, ui, u = u_site
                if ua != a or ui <= idx or id(u) in touched or u.k == "unk":
                    continue
                between = [x for x in seq[idx + 1:ui] if x.k != "nop"]
                if any(is_impure(x) for x in between):
                    continue
                if any(x.k == "asg" and any(w in def_vars(x) for w in {n.aux for n in walk(s.src) if n.op == "var"})
                       for x in between):
                    continue
                if any(has_op(e, ("mem", "call")) for e in stmt_exprs(u)):
                    continue
                map_stmt(u, lambda e, v=v, src=s.src: subst(e, lambda x: src if x.op == "var" and x.aux == v else None))
                s.k = "nop"
                touched.add(id(s))
                touched.add(id(u))
                changed = True

        for a in self.graph.rpo:
            b = self.blocks[a]
            b.stmts = [s for s in b.stmts if s.k != "nop"]
        return changed

    # ---- 죽은 코드 제거(생존 분석) ----------------------------------------------------------
    def dce(self) -> bool:
        """부작용이 있는 문장이 읽는 변수에서 거슬러 올라가며 살아 있는 정의만 남긴다(자기 자신만 읽는 순환도 지운다)."""
        defs_of = defaultdict(list)
        roots = []
        for a in self.graph.rpo:
            b = self.blocks[a]
            for s in b.stmts + [b.term]:
                if s.k == "asg" and s.dst.op == "var":
                    defs_of[s.dst.aux].append(s)
                elif s.k == "call" and s.dst is not None:
                    defs_of[s.dst.aux].append(s)
                    roots.append(s)
                else:
                    roots.append(s)
        live_vars = set()
        needed = set()
        work = list(roots)
        while work:
            s = work.pop()
            if id(s) in needed:
                continue
            needed.add(id(s))
            for n in used_vars(s):
                if n.aux not in live_vars:
                    live_vars.add(n.aux)
                    for d in defs_of.get(n.aux, ()):
                        if id(d) not in needed:
                            work.append(d)
        changed = False
        for a in self.graph.rpo:
            b = self.blocks[a]
            keep = []
            for s in b.stmts:
                if s.k == "asg" and s.dst.op == "var" and id(s) not in needed:
                    changed = True
                    continue
                if s.k == "call" and s.dst is not None and s.dst.aux not in live_vars:
                    s.dst = None
                    changed = True
                keep.append(s)
            b.stmts = keep
        return changed

    # ---- 변수 폭 줄이기 -----------------------------------------------------------------
    def narrow(self) -> bool:
        """변수를 읽는 곳이 모두 아래쪽 k 바이트만 쓰면(trunc), 그 변수를 k 바이트로 줄이고 정의의 오른쪽 식도 잘라 맞춘다."""
        consumed = {}                                   # var → 읽는 최대 폭 (전체를 읽으면 변수 폭)
        bad = set()

        def visit(e, parent):
            if e.op == "var":
                k = parent.sz if (parent is not None and parent.op == "trunc") else e.sz
                consumed[e.aux] = max(consumed.get(e.aux, 0), k)
                return
            for c in e.args:
                visit(c, e)

        def_sites = defaultdict(list)
        for a in self.graph.rpo:
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                for e in stmt_exprs(s):
                    visit(e, None)
                if s.k == "asg" and s.dst.op == "var":
                    def_sites[s.dst.aux].append(s)
                elif s.k == "call" and s.dst is not None:
                    def_sites[s.dst.aux].append(s)
                elif s.k == "unk":
                    for n in s.extra.get("usevars", ()):
                        bad.add(n.aux)
                    for n in s.extra.get("defvars", ()):
                        bad.add(n.aux)
        cand = {}
        for v, sites in def_sites.items():
            w = self.webs[v]
            if v in bad or w.loc == "F" or v not in consumed:
                continue
            k = consumed[v]
            if k < w.sz:
                cand[v] = k
        for v, w in self.webs.items():                  # 정의 문장이 없는 진입 변수(매개변수)
            if v not in def_sites and v not in bad and v in consumed and w.loc != "F" and consumed[v] < w.sz and w.entry \
                    and not re.fullmatch(r"d\d+", w.loc):          # ARM VFP d 레지스터 인자는 아래 절반만 써도 double 인자다(레지스터 종류가 호출 규약을 정한다)
                cand[v] = consumed[v]
        if not cand:
            return False

        def fix(e):
            if e.op == "trunc" and e.args[0].op == "var" and e.args[0].aux in cand:
                v = e.args[0].aux
                return ir.trunc(var(v, cand[v]), e.sz)
            if e.op == "var" and e.aux in cand:
                return var(e.aux, cand[e.aux])
            return None

        for a in self.graph.rpo:
            for s in self.blocks[a].stmts + [self.blocks[a].term]:
                map_stmt(s, lambda e: subst(e, fix))
                if s.k == "asg" and s.dst.op == "var" and s.dst.aux in cand:
                    k = cand[s.dst.aux]
                    s.src = ir.trunc(s.src, k)
                    s.dst = var(s.dst.aux, k)
                elif s.k == "call" and s.dst is not None and s.dst.aux in cand:
                    s.dst = var(s.dst.aux, cand[s.dst.aux])
        for v, k in cand.items():
            self.webs[v].sz = k
        return True

    # ---- 반복 ----------------------------------------------------------------------------
    def run(self, max_rounds: int = 40):
        for _ in range(max_rounds):
            self.check()
            c1 = self.round()
            c2 = self.dce()
            c3 = self.narrow()
            if not (c1 or c2 or c3):
                break
