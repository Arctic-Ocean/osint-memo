"""읽기 전용 데이터의 룩업 테이블 복원.

clang -O1 이상은 `switch` 의 각 case 가 상수를 돌려주면 분기 대신 `.rodata` 의 배열을 색인해 읽는다.
`*(int32_t *)(idx * 4 + 0x200170)` 처럼 상수 주소와 색인만 있는 읽기를, 색인 범위를 막는 지배 분기(`if (idx > 7) return …`)에서
테이블 길이를 알아낼 수 있을 때 `tbl_200170[idx]` 와 상수 배열 선언으로 바꿔 보여 준다. 길이를 알 수 없으면 그대로 둔다(의미를 지어내지 않는다).
"""
from __future__ import annotations

from . import ir
from . import ptrtype
from .ir import X, mask

MAX_ENTRIES = 1024


def match(addr: X, sz: int, full: int):
    """주소식이 `상수 + 색인 * sz` 면 (상수 주소, 색인식), 아니면 None."""
    if sz not in (1, 2, 4, 8):
        return None
    bases, idxs, off, rest = ptrtype.split_addr(addr, full)
    if bases or rest or len(idxs) != 1 or idxs[0][1] != sz:
        return None
    return off & mask(full), idxs[0][0]


def _strip(e: X) -> X:
    while e.op == "zext":
        e = e.args[0]
    return e


def _facts(c: X):
    """c 가 참일 때 반드시 성립하는 단순 비교들."""
    if c.op in ir.CMP_OPS:
        yield c
    elif c.op == "land":
        yield from _facts(c.args[0])
        yield from _facts(c.args[1])


def _bound(c: X, idx: X):
    """비교 c(참) 가 색인 idx 의 상한을 주면 테이블 길이."""
    if c.op not in ("ult", "ule") or c.args[1].op != "c":
        return None
    if _strip(c.args[0]) != _strip(idx):
        return None
    k = c.args[1].aux & mask(c.args[1].sz)
    return k if c.op == "ult" else k + 1


def length_for(fd, block_addr: int, idx: X):
    """블록 block_addr 에서 색인 idx 를 막는 지배 분기들 중 가장 좁은 길이(없으면 None)."""
    g = fd.graph
    best = None
    a = block_addr
    seen = 0
    while a in g.idom and seen < 64:
        seen += 1
        pa = g.idom[a]
        if pa == a:
            break
        d = fd.blocks[pa]
        t = d.term
        if t.k == "cj" and t.t != t.f:
            for side, tgt in ((True, t.t), (False, t.f)):
                if g.dominates(tgt, block_addr) and len(fd.blocks[tgt].preds) == 1:
                    cond = t.src if side else ir.invert(t.src)
                    for f in _facts(cond):
                        n = _bound(f, idx)
                        if n is not None and (best is None or n < best):
                            best = n
        a = pa
    return best


def find(fd) -> dict:
    """{테이블 시작 주소: {esz, n}}. 모든 접근에서 길이를 알 수 있고 원소 크기가 같은 테이블만."""
    model = fd.model
    full = fd.full
    found: dict = {}
    bad: set = set()
    for a in fd.graph.rpo:
        b = fd.blocks[a]
        for s in b.stmts + [b.term]:
            for ex in _exprs(s):
                for n in ir.walk(ex):
                    if n.op != "mem":
                        continue
                    m = match(n.args[0], n.sz, full)
                    if m is None:
                        continue
                    base, idx = m
                    sec = model.section_for(base)
                    if sec is None or sec.is_code or sec.writable or not sec.readable:
                        continue
                    ln = length_for(fd, a, idx)
                    if ln is None or ln < 1 or ln > MAX_ENTRIES or not sec.contains(base + ln * n.sz - 1):
                        bad.add(base)
                        continue
                    cur = found.get(base)
                    if cur is not None and cur["esz"] != n.sz:
                        bad.add(base)
                        continue
                    if cur is None:
                        found[base] = {"esz": n.sz, "n": ln}
                    else:
                        cur["n"] = max(cur["n"], ln)
    for base in bad:
        found.pop(base, None)
    return found


def _exprs(s):
    if s.k == "asg":
        if s.dst.op == "mem":
            yield s.dst.args[0]
        yield s.src
    elif s.k == "call":
        yield s.src
        if s.dst is not None and s.dst.op == "mem":
            yield s.dst.args[0]
    elif s.src is not None:
        yield s.src


def values(fd, base: int, esz: int, n: int, signed: bool):
    data = fd.prog.dis.read(base, esz * n)
    if len(data) < esz * n:
        return None
    little = fd.model.endian != "big"
    return [int.from_bytes(data[i * esz:(i + 1) * esz], "little" if little else "big", signed=signed) for i in range(n)]
