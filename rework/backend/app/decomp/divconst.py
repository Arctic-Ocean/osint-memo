"""상수로 나누기 복원.

컴파일러는 `x / 7` 을 곱셈·시프트·보정 뺄셈으로 바꿔 놓는다. 변수 하나만 쓰는 곱셈/시프트 식을 입력 몇십 개로 직접 계산해 보고,
`x / d`(부호 있음·없음) 와 모든 입력에서 같은 d 를 찾으면 그 식을 나눗셈으로 되돌린다. 우연히 맞을 수 없도록 경계값과 무작위 값을 함께 쓴다.
"""
from __future__ import annotations

import random

from . import ir
from .ir import X, mask, to_signed, _fold

_ARITH_OK = {"add", "sub", "mul", "and", "or", "xor", "shl", "shr", "sar", "neg", "not", "zext", "sext", "trunc", "mulhs", "mulhu",
             "var", "c"}


def _eval(e: X, env: dict):
    op = e.op
    if op == "c":
        return e.aux
    if op == "var":
        return env[e.aux] & mask(e.sz)
    a = [_eval(x, env) for x in e.args]
    sz = e.sz
    if op in ("add", "sub", "mul", "and", "or", "xor", "shl", "shr", "sar", "mulhs", "mulhu"):
        r = _fold(op, a[0], a[1], sz)
        return 0 if r is None else r
    if op == "neg":
        return (-a[0]) & mask(sz)
    if op == "not":
        return (~a[0]) & mask(sz)
    if op == "zext":
        return a[0] & mask(sz)
    if op == "sext":
        return to_signed(a[0], e.args[0].sz) & mask(sz)
    if op == "trunc":
        return a[0] & mask(sz)
    raise ValueError(op)


def _vectors(sz: int, signed: bool):
    m = mask(sz)
    sm = 1 << (8 * sz - 1)
    base = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 16, 17, 31, 33, 63, 64, 99, 100, 101, 127, 128, 129, 255, 256, 1000,
            4095, 4096, 65535, 65536, 1 << 20, sm - 1, sm, sm + 1, m, m - 1, m - 2, m // 3, m // 7, m // 10]
    rng = random.Random(0xD1F)
    vals = set(base)
    for v in base:
        vals.add((-v) & m)
    for _ in range(60):
        vals.add(rng.getrandbits(8 * sz))
    return sorted(vals)


def _candidates(consts, sz):
    """큰 상수(마법수) M 과 시프트 s 에서 나올 법한 나눗셈 수 d 후보."""
    out = set()
    bits = 8 * sz
    ms = set()
    for c in consts:
        low = c & mask(sz)
        ms.update((c, low, low + (1 << bits)))       # 부호 있는 마법수는 음수로 보일 수 있다(보정 덧셈을 쓰는 형태)
    for M in ms:
        if M < (1 << (bits // 2)):
            continue
        for s in range(0, bits + 2):
            d = round((1 << (bits + s)) / M) if M else 0
            for dd in (d - 1, d, d + 1):
                if 2 <= dd <= (1 << 24):
                    out.add(dd)
    return sorted(out)


def _expand(e: X, resolve, depth=0):
    """단일 정의 순수 변수를 정의식으로 펼친 식(평가용)."""
    if e.op == "var" and resolve is not None and depth < 8:
        d = resolve(e.aux)
        if d is not None:
            return _expand(d, resolve, depth + 1)
        return e
    if not e.args:
        return e
    new = tuple(_expand(x, resolve, depth) for x in e.args)
    return ir.rebuild(e, new) if any(n is not o for n, o in zip(new, e.args)) else e


def try_divconst(e: X, resolve=None):
    """e 가 (한 변수에 대한) 상수 나누기 식이면 대체할 식, 아니면 None."""
    if e.op not in ("sub", "sar", "shr", "add", "trunc", "zext", "sext") or e.sz not in (4, 8):
        return None
    ee = _expand(e, resolve)
    vars_ = {n.aux: n for n in ir.walk(ee) if n.op == "var"}
    if len(vars_) != 1:
        return None
    nodes = list(ir.walk(ee))
    if any(n.op not in _ARITH_OK for n in nodes) or len(nodes) > 60:
        return None
    if not any(n.op in ("mul", "mulhs", "mulhu") for n in nodes):
        return None
    consts = {n.aux for n in nodes if n.op == "c" and n.sz in (4, 8)}
    sz = e.sz
    (vid, vnode), = vars_.items()
    if vnode.sz < sz:
        return None
    cands = _candidates(consts, vnode.sz if vnode.sz >= sz else sz)
    if not cands:
        return None
    xs = _vectors(vnode.sz, True)
    try:
        results = [(x, _eval(ee, {vid: x})) for x in xs]
    except (ValueError, KeyError):
        return None
    for d in cands:
        for kind in ("s", "u"):
            ok = True
            for x, got in results:
                xv = x & mask(sz)
                want = _fold("sdiv" if kind == "s" else "udiv", xv, d, sz)
                if want is None or want != got:
                    ok = False
                    break
            if ok:
                xe = vnode if vnode.sz == sz else ir.trunc(vnode, sz)
                return ir.binop("sdiv" if kind == "s" else "udiv", xe, ir.C(d, sz), sz)
    return None


def try_divisible(e: X):
    """`(x * M) >u T` 꼴의 '나누어떨어지는가' 검사를 `x % d != 0` 으로."""
    if e.op not in ("ugt", "ule", "ult", "uge") or e.args[1].op != "c" or e.args[0].op != "mul" or e.args[0].args[1].op != "c":
        return None
    x, M = e.args[0].args[0], e.args[0].args[1].aux
    T = e.args[1].aux
    sz = e.args[0].sz
    n = 8 * sz
    if M % 2 == 0 or sz not in (4, 8):
        return None
    d = pow(M, -1, 1 << n)
    if not (2 <= d <= (1 << 24)) or d % 2 == 0:
        return None
    lim = ((1 << n) - 1) // d
    op = e.op
    if op in ("ugt", "ule") and T == lim:
        cond = ir.cmp("eq", ir.binop("srem", x, ir.C(d, sz), sz), ir.C(0, sz))        # x*M <= lim ⇔ d | x
        return cond if op == "ule" else ir.invert(cond)
    if op in ("ult", "uge") and T == lim + 1:
        cond = ir.cmp("eq", ir.binop("srem", x, ir.C(d, sz), sz), ir.C(0, sz))
        return cond if op == "ult" else ir.invert(cond)
    return None


def rewrite_with(resolve):
    def rewrite(e: X) -> X:
        """식 전체를 아래에서 위로 훑으며 상수 나누기·나누어떨어짐 검사를 되돌린다."""
        if not e.args:
            return e
        new = tuple(rewrite(a) for a in e.args)
        e2 = ir.rebuild(e, new) if any(n is not o for n, o in zip(new, e.args)) else e
        r = try_divisible(e2) if e2.op in ir.CMP_OPS else try_divconst(e2, resolve)
        return r if r is not None else e2
    return rewrite


def rewrite(e: X) -> X:
    return rewrite_with(None)(e)
