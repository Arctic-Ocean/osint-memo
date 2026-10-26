"""포인터 형 추정: 메모리 접근의 주소식에서 '기준 포인터 변수'와 그 가리키는 원소 크기를 알아낸다.

주소식 `p + c + i*s` 를 (기준 변수 p, 상수 c, 색인 i, 배율 s) 로 분해하고, 한 변수에 대한 접근이 모두 같은 크기 원소의 배열처럼
쓰이면 `T *`(배열), 크기·오프셋이 섞이면 `uint8_t *`(구조체·바이트 버퍼)로 본다. 나머지는 식 그대로 정수로 둔다(의미를 바꾸지 않는다).
"""
from __future__ import annotations

from . import ir
from .ir import X, mask


class PtrType:
    __slots__ = ("kind", "esz", "signed", "cstr")

    def __init__(self, kind, esz, signed="u"):
        self.kind, self.esz, self.signed = kind, esz, signed
        self.cstr = False


def split_addr(e: X, full: int):
    """주소식 → (기준 var 노드들, 색인 후보 [(식, 배율)], 상수 합, 나머지 항들)."""
    consts = 0
    terms = []
    stack = [e]
    while stack:
        n = stack.pop()
        if n.op == "add":
            stack.append(n.args[0])
            if n.args[1].op == "c":
                consts += ir.to_signed(n.args[1].aux, n.sz)
            else:
                stack.append(n.args[1])
        elif n.op == "c":
            consts += ir.to_signed(n.aux, n.sz)
        else:
            terms.append(n)
    bases, idxs, rest = [], [], []
    for t in terms:
        if t.op == "stk":
            bases.append(t)
        elif t.op == "var" and t.sz == full:
            bases.append(t)
        elif t.op == "mul" and t.args[1].op == "c" and t.args[1].aux in (1, 2, 4, 8, 16):
            idxs.append((t.args[0], t.args[1].aux))
        elif t.op == "shl" and t.args[1].op == "c" and t.args[1].aux in (0, 1, 2, 3, 4):
            idxs.append((t.args[0], 1 << t.args[1].aux))
        elif t.op in ("sext", "zext", "var") and t.sz == full:
            idxs.append((t, 1))
        else:
            rest.append(t)
    return bases, idxs, consts, rest


def pick_base(bases, idxs, ptypes):
    """기준 변수 하나를 고르고 나머지 var 항은 색인(배율 1)으로 돌린다."""
    if not bases:
        return None, idxs
    chosen = None
    for b in bases:
        if b.op == "stk":
            chosen = b
            break
    for b in bases:
        if chosen is None and b.aux in ptypes:
            chosen = b
            break
    if chosen is None:
        chosen = bases[0]
    others = [(b, 1) for b in bases if b is not chosen]
    return chosen, idxs + others


def infer(all_exprs, full: int, webs, induction=frozenset(), params=frozenset()) -> dict:
    """모든 식(문장의 원천·주소·조건)을 훑어 {web id: PtrType} 를 만든다.
    기준 변수가 둘 이상 후보일 때는 반복마다 1씩 커지는 색인 변수(induction)가 아닌 쪽, 다른 접근에서 이미 기준이었던 쪽, 매개변수를 우선한다."""
    accesses = []                                      # (후보 var 노드들, 색인 후보, 상수, sz)
    definite: set = set()
    for e in all_exprs:
        for n in ir.walk(e):
            if n.op != "mem":
                continue
            bases, idxs, off, rest = split_addr(n.args[0], full)
            bases = [b for b in bases if b.op == "var" and webs[b.aux].sz == full]
            if rest or not bases:
                continue
            if len(bases) == 1:
                definite.add(bases[0].aux)
            accesses.append((bases, idxs, off, n.sz))
    ev: dict = {}
    for bases, idxs, off, sz in accesses:
        cand = sorted(bases, key=lambda b: (b.aux in induction, b.aux not in definite, b.aux not in params))
        base = cand[0]
        others = [(b, 1) for b in bases if b is not base]
        ix = idxs + others
        if len(ix) > 1:
            continue
        ev.setdefault(base.aux, []).append((off, sz, ix[0][1] if ix else None))
    out = {}
    for v, accs in ev.items():
        sizes = {sz for _, sz, _ in accs}
        if len(sizes) == 1:
            esz = next(iter(sizes))
            if all((off % esz == 0) and (sc is None or sc == esz) for off, _, sc in accs):
                out[v] = PtrType("arr", esz)
                continue
        out[v] = PtrType("bytes", 1)
    return out


def propagate_copies(assigns, ptypes, webs):
    """`v = w` 복사로 포인터 형이 퍼진다(양방향)."""
    changed = True
    rounds = 0
    while changed and rounds < 6:
        changed = False
        rounds += 1
        for dst, src in assigns:
            if dst in ptypes and src not in ptypes and webs[src].sz == webs[dst].sz:
                ptypes[src] = ptypes[dst]
                changed = True
            elif src in ptypes and dst not in ptypes and webs[dst].sz == webs[src].sz:
                ptypes[dst] = ptypes[src]
                changed = True
