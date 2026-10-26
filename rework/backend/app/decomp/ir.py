"""디컴파일러 중간표현(IR) — 식(X)과 문장(S), 그리고 식을 늘 정규형으로 만드는 생성 함수들.

식은 모두 `X` 하나로 표현한다(op, args, sz, aux). sz 는 결과의 바이트 수이고, 값은 해당 크기의 부호 없는 정수로 본다.
비교는 sz=1 의 0/1 값을 낸다(피연산자 크기는 args 의 sz). 모든 생성 함수는 상수 접기·항등원 제거를 하므로,
한 번 만든 식은 늘 '줄여진' 상태이고 치환(subst)으로 다시 만들어도 마찬가지다.
"""
from __future__ import annotations


def mask(sz: int) -> int:
    return (1 << (8 * sz)) - 1


def to_signed(v: int, sz: int) -> int:
    v &= mask(sz)
    return v - (1 << (8 * sz)) if v >> (8 * sz - 1) else v


class X:
    __slots__ = ("op", "args", "sz", "aux", "_h")

    def __init__(self, op, args=(), sz=8, aux=None):
        self.op = op
        self.args = tuple(args)
        self.sz = sz
        self.aux = aux
        self._h = None

    def __eq__(self, o):
        return (self is o) or (isinstance(o, X) and self.op == o.op and self.sz == o.sz and self.aux == o.aux
                               and self.args == o.args)

    def __hash__(self):
        h = self._h
        if h is None:
            aux = self.aux
            if isinstance(aux, dict):                       # 호출 식의 부가 정보(딕셔너리): 같은 값이면 같은 해시가 되는 필드만
                aux = (aux.get("name"), aux.get("nargs"), aux.get("tail"))
            h = self._h = hash((self.op, self.sz, aux, self.args))
        return h

    def __repr__(self):
        if self.op == "c":
            return f"{self.aux:#x}:{self.sz}"
        if self.op in ("loc", "var"):
            return f"{self.aux}:{self.sz}" if self.op == "loc" else f"v{self.aux}:{self.sz}"
        if self.op == "stk":
            return f"stk({self.aux:+d})"
        a = ", ".join(map(repr, self.args))
        return f"{self.op}{self.sz}({a}{'; ' + repr(self.aux) if self.aux is not None else ''})"

    @property
    def is_const(self):
        return self.op == "c"


# ---- 연산 분류 ----------------------------------------------------------------
ARITH = {"add", "sub", "mul", "and", "or", "xor", "shl", "shr", "sar", "rol", "ror", "sdiv", "udiv", "srem", "urem",
         "mulhs", "mulhu"}
COMMUTATIVE = {"add", "mul", "and", "or", "xor"}
CMP_OPS = {"eq", "ne", "slt", "sle", "sgt", "sge", "ult", "ule", "ugt", "uge"}
CMP_INV = {"eq": "ne", "ne": "eq", "slt": "sge", "sge": "slt", "sle": "sgt", "sgt": "sle",
           "ult": "uge", "uge": "ult", "ule": "ugt", "ugt": "ule"}
CMP_SWAP = {"eq": "eq", "ne": "ne", "slt": "sgt", "sgt": "slt", "sle": "sge", "sge": "sle",
            "ult": "ugt", "ugt": "ult", "ule": "uge", "uge": "ule"}
# 부동소수점: 값은 IEEE 비트 패턴(폭 4 = float, 8 = double)으로 정수와 같은 방식으로 들고 다닌다.
FOPS2 = {"fadd", "fsub", "fmul", "fdiv", "fmin", "fmax"}
FOPS1 = {"fsqrt", "fneg", "fabs"}
FCONV = {"i2f", "f2i", "f2f"}                # i2f: 정수→float(aux 's'|'u' 는 원본 부호) · f2i: float→정수(0 쪽으로 자름) · f2f: float↔double
FOPS = FOPS2 | FOPS1 | FCONV
FCMP_OPS = {"feq", "fne", "flt", "fle", "fgt", "fge", "funord"}                      # 불리언(NaN 이면 funord·fne 만 참)
BOOL_OPS = CMP_OPS | {"land", "lor", "lnot"} | FCMP_OPS


# ---- 기본 생성자 ----------------------------------------------------------------
def C(v: int, sz: int = 8) -> X:
    return X("c", (), sz, v & mask(sz))


def loc(name: str, sz: int) -> X:
    return X("loc", (), sz, name)


def var(vid: int, sz: int) -> X:
    return X("var", (), sz, vid)


def stk(off: int, sz: int = 8) -> X:
    return X("stk", (), sz, off)


def mem(addr: X, sz: int) -> X:
    return X("mem", (addr,), sz)


def is_bool(e: X) -> bool:
    return e.op in BOOL_OPS


# ---- 상수 접기 -----------------------------------------------------------------
def _fold(op, a, b, sz):
    m = mask(sz)
    if op == "add":
        return (a + b) & m
    if op == "sub":
        return (a - b) & m
    if op == "mul":
        return (a * b) & m
    if op == "and":
        return a & b
    if op == "or":
        return a | b
    if op == "xor":
        return a ^ b
    bits = 8 * sz
    if op == "shl":
        return (a << (b & (bits - 1 if bits >= 32 else 31))) & m if b < bits else 0
    if op == "shr":
        return (a >> b) if b < bits else 0
    if op == "sar":
        return (to_signed(a, sz) >> min(b, bits - 1)) & m
    if op == "rol":
        b %= bits
        return ((a << b) | (a >> (bits - b))) & m if b else a
    if op == "ror":
        b %= bits
        return ((a >> b) | (a << (bits - b))) & m if b else a
    if op == "udiv":
        return (a // b) & m if b else None
    if op == "urem":
        return (a % b) & m if b else None
    if op in ("sdiv", "srem"):
        if b == 0:
            return None
        sa, sb = to_signed(a, sz), to_signed(b, sz)
        q = abs(sa) // abs(sb)
        if (sa < 0) != (sb < 0):
            q = -q
        return (q if op == "sdiv" else sa - q * sb) & m
    if op == "mulhs":
        return ((to_signed(a, sz) * to_signed(b, sz)) >> bits) & m
    if op == "mulhu":
        return ((a * b) >> bits) & m
    return None


def _fold_cmp(op, a, b, sz):
    sa, sb = to_signed(a, sz), to_signed(b, sz)
    return {"eq": a == b, "ne": a != b, "slt": sa < sb, "sle": sa <= sb, "sgt": sa > sb, "sge": sa >= sb,
            "ult": a < b, "ule": a <= b, "ugt": a > b, "uge": a >= b}[op]


# ---- 식 생성(정규화 포함) ---------------------------------------------------------
def binop(op: str, a: X, b: X, sz: int | None = None) -> X:
    """산술·논리 2항 연산. 상수는 오른쪽으로 모으고 항등원을 지운다."""
    sz = sz or a.sz
    if a.op == "c" and b.op == "c":
        r = _fold(op, a.aux, b.aux if op not in ("shl", "shr", "sar", "rol", "ror") else b.aux, sz)
        if r is not None:
            return C(r, sz)
    if op in COMMUTATIVE and a.op == "c" and b.op != "c":
        a, b = b, a
    if op == "sub":
        if a == b:
            return C(0, sz)
        if b.op == "c":
            return binop("add", a, C(-b.aux, sz), sz)
        if b.op == "mul" and b.args[1].op == "c" and b.args[0].op in ("sdiv", "udiv") and b.args[0].args[0] == a \
                and b.args[0].args[1] == b.args[1]:                                # x - (x / d) * d → x % d
            return X("srem" if b.args[0].op == "sdiv" else "urem", (a, b.args[1]), sz)
    if op == "add":
        if b.op == "c":
            if b.aux == 0:
                return a
            if a.op == "add" and a.args[1].op == "c":                    # (x + c1) + c2
                return binop("add", a.args[0], C(a.args[1].aux + b.aux, sz), sz)
            if a.op == "stk":
                return stk(a.aux + to_signed(b.aux, sz), sz)
            if a.op == "add" and a.args[0].op == "stk":                      # (stk + 항) + c → stk(+c) + 항
                return binop("add", stk(a.args[0].aux + to_signed(b.aux, sz), sz), a.args[1], sz)
        elif b.op == "mul" and b.args[1].op == "c" and b.args[0] == a:                    # x + x*k → x*(k+1)
            return binop("mul", a, C(b.args[1].aux + 1, sz), sz)
        elif a.op == "mul" and a.args[1].op == "c" and a.args[0] == b:
            return binop("mul", b, C(a.args[1].aux + 1, sz), sz)
        elif a.op == "sub" and a.args[1] == b:                            # (x - y) + y
            return a.args[0]
        elif b.op == "sub" and b.args[1] == a:
            return b.args[0]
        elif b.op == "neg" and b.args[0].sz == sz:
            return binop("sub", a, b.args[0], sz)
    elif op == "mul":
        if b.op == "c":
            if b.aux == 1:
                return a
            if b.aux == 0:
                return C(0, sz)
            if a.op == "mul" and a.args[1].op == "c":
                return binop("mul", a.args[0], C(a.args[1].aux * b.aux, sz), sz)
            if b.aux == mask(sz) and sz >= 1:                              # x * -1
                return unop("neg", a)
    elif op == "and":
        if b.op == "c":
            if b.aux == 0:
                return C(0, sz)
            if b.aux == mask(sz):
                return a
            for k in (1, 2, 4):
                if k < sz and b.aux == mask(k):                        # x & 0xff.. → 아래 k 바이트를 0 확장
                    return zext(trunc(a, k), sz)
        if a == b:
            return a
        if b.op == "c" and sz in (4, 8) and b.aux == (mask(sz) >> 1) and _looks_fp(a):                # x & 부호 제외 비트 → fabs(x)
            return X("fabs", (a,), sz)
        if b.op == "c" and a.op == "neg" and a.args[0].op == "zext" and a.args[0].args[0].op in BOOL_OPS and a.sz == sz:   # (비교 마스크) & c
            cond = a.args[0].args[0]
            return zext(cond, sz) if b.aux == 1 else ite(cond, b, C(0, sz))
        # 한쪽이 좁은 값의 0 확장이면 다른 쪽도 그 폭만 보면 된다(위쪽 쓰레기 비트가 결과에 영향을 못 준다)
        if a.op == "zext" and a.args[0].sz < sz:
            k = a.args[0].sz
            return zext(binop("and", a.args[0], trunc(b, k), k), sz)
        if b.op == "zext" and b.args[0].sz < sz:
            k = b.args[0].sz
            return zext(binop("and", trunc(a, k), b.args[0], k), sz)
    elif op == "or":
        if b.op == "c":
            if b.aux == 0:
                return a
            if b.aux == mask(sz):
                return C(mask(sz), sz)
        if a == b:
            return a
        sel = _mask_select(a, b)                                          # (x & m) | (y & ~m), m = 비교 결과 마스크 → 비교 ? x : y
        if sel is not None:
            return sel
    elif op == "xor":
        if b.op == "c" and b.aux == 0:
            return a
        if a == b:
            return C(0, sz)
        if b.op == "c" and sz in (4, 8) and b.aux == (1 << (8 * sz - 1)) and _looks_fp(a):          # x ^ 부호 비트 → -x
            return X("fneg", (a,), sz)
    elif op in ("shl", "shr", "sar", "rol", "ror"):
        if b.op == "c":
            if b.aux == 0:
                return a
            if op == "shl" and a.op == "shl" and a.args[1].op == "c" and a.args[1].aux + b.aux < 8 * sz:
                return binop("shl", a.args[0], C(a.args[1].aux + b.aux, b.sz), sz)
            if op == "shl" and b.aux >= 8 * sz:
                return C(0, sz)
            if op == "shr" and b.aux >= 8 * sz:
                return C(0, sz)
        if a.op == "c" and a.aux == 0 and op in ("shl", "shr"):
            return C(0, sz)
    elif op in ("udiv", "sdiv"):
        if b.op == "c" and b.aux == 1:
            return a
    elif op in ("urem", "srem"):
        if b.op == "c" and b.aux == 1:
            return C(0, sz)
    return X(op, (a, b), sz)


def _looks_fp(a: X) -> bool:
    """이 식이 부동소수점 값처럼 쓰이나(부호 비트 마스크를 fneg·fabs 로 바꿔도 되나): 연산 결과이거나 xmm 위치를 읽는다."""
    return a.op in FOPS or (a.op in ("loc", "var") and False) or (a.op == "loc" and str(a.aux).startswith("xmm"))


def _mask_m(x: X):
    """`neg(zext(불리언))` 꼴의 비교 마스크면 그 불리언."""
    if x.op == "neg" and x.args[0].op == "zext" and x.args[0].args[0].op in BOOL_OPS:
        return x.args[0].args[0]
    return None


def _mask_select(a: X, b: X):
    """SSE 의 마스크 선택: (x & m) | (~m & y)  또는  (x & m) | (y & ~m) → m 의 조건 ? x : y."""
    if a.op != "and" or b.op != "and":
        return None
    for (p, q) in ((a, b), (b, a)):
        for pi in (0, 1):
            cond = _mask_m(p.args[pi])
            if cond is None:
                continue
            x = p.args[1 - pi]
            for qi in (0, 1):
                nq = q.args[qi]
                if nq.op == "not" and _mask_m(nq.args[0]) == cond:
                    return ite(cond, x, q.args[1 - qi])
    return None


def add(a, b, sz=None):
    return binop("add", a, b, sz)


def sub(a, b, sz=None):
    return binop("sub", a, b, sz)


def unop(op: str, a: X) -> X:
    if a.op == "c":
        if op == "neg":
            return C(-a.aux, a.sz)
        if op == "not":
            return C(~a.aux, a.sz)
    if a.op == op:                                                      # --x, ~~x
        return a.args[0]
    if op == "neg" and a.op == "sub":
        return binop("sub", a.args[1], a.args[0], a.sz)
    if op == "not" and a.op in CMP_OPS and a.sz == 1:
        return X(CMP_INV[a.op], a.args, 1)
    return X(op, (a,), a.sz)


def zext(a: X, sz: int) -> X:
    if a.sz == sz:
        return a
    if a.sz > sz:
        return trunc(a, sz)
    if a.op == "c":
        return C(a.aux, sz)
    if a.op == "zext":
        return X("zext", (a.args[0],), sz)
    return X("zext", (a,), sz)


def sext(a: X, sz: int) -> X:
    if a.sz == sz:
        return a
    if a.sz > sz:
        return trunc(a, sz)
    if a.op == "c":
        return C(to_signed(a.aux, a.sz), sz)
    if a.op == "sext":
        return X("sext", (a.args[0],), sz)
    if a.op == "zext" and a.sz > a.args[0].sz:                           # 위쪽 비트가 0 이므로 부호 확장도 0 확장
        return X("zext", (a.args[0],), sz)
    if a.op in BOOL_OPS:
        return X("zext", (a,), sz)
    return X("sext", (a,), sz)


_TRUNC_DIST = {"add", "sub", "mul", "and", "or", "xor"}


def trunc(a: X, sz: int) -> X:
    if a.sz == sz:
        return a
    if a.sz < sz:
        return zext(a, sz)
    if a.op == "c":
        return C(a.aux, sz)
    if a.op in ("zext", "sext"):
        inner = a.args[0]
        if inner.sz == sz:
            return inner
        if inner.sz > sz:
            return trunc(inner, sz)
        return X(a.op, (inner,), sz)
    if a.op == "trunc":
        return trunc(a.args[0], sz)
    if a.op in _TRUNC_DIST:
        return binop(a.op, trunc(a.args[0], sz), trunc(a.args[1], sz), sz)
    if a.op == "shl":
        return binop("shl", trunc(a.args[0], sz), a.args[1], sz)
    if a.op in ("neg", "not"):
        return unop(a.op, trunc(a.args[0], sz))
    if a.op == "ite":
        return ite(a.args[0], trunc(a.args[1], sz), trunc(a.args[2], sz))
    if a.op == "ins":                                                    # 부분 쓰기: 아래 sz 바이트만 보면 삽입한 값
        part = a.aux
        if sz <= part:
            return trunc(a.args[1], sz)
    if a.op == "shr" and a.args[1].op == "c" and a.args[1].aux + 8 * sz <= 8 * a.args[0].sz and a.args[1].aux % 8 == 0 \
            and False:
        pass
    return X("trunc", (a,), sz)


def cmp(op: str, a: X, b: X) -> X:
    sz = a.sz
    if a.op == "c" and b.op == "c":
        return C(1 if _fold_cmp(op, a.aux, b.aux, sz) else 0, 1)
    if a.op == "c" and b.op != "c":
        a, b, op = b, a, CMP_SWAP[op]
    if a == b:
        return C(1 if op in ("eq", "sle", "sge", "ule", "uge") else 0, 1)
    if b.op == "c":
        c = b.aux
        # 불리언(0/1) 값과 0/1 의 비교
        if a.op in BOOL_OPS and a.sz == 1 and sz == 1:
            if (op == "ne" and c == 0) or (op == "eq" and c == 1):
                return a
            if (op == "eq" and c == 0) or (op == "ne" and c == 1):
                return invert(a)
        if a.op == "zext" and a.args[0].op in BOOL_OPS and op in ("eq", "ne") and c in (0, 1):
            return cmp(op, a.args[0], C(c, 1))
        # 부호 없는 값은 0 보다 작을 수 없다 등
        if op == "ult" and c == 0:
            return C(0, 1)
        if op == "uge" and c == 0:
            return C(1, 1)
        if op in ("eq", "ne") and a.op in ("add",) and a.args[1].op == "c":     # (x + c1) == c2 → x == c2 - c1
            return cmp(op, a.args[0], C(c - a.args[1].aux, sz))
        if op in ("eq", "ne") and a.op == "sub" and a.args[1].op == "c":
            return cmp(op, a.args[0], C(c + a.args[1].aux, sz))
        if op in ("eq", "ne") and a.op == "xor" and a.args[1].op == "c":
            return cmp(op, a.args[0], C(c ^ a.args[1].aux, sz))
        if op in ("eq", "ne") and a.op == "neg":
            return cmp(op, a.args[0], C(-c, sz))
        if op in ("eq", "ne") and a.op == "not":
            return cmp(op, a.args[0], C(~c, sz))
        if op in ("eq", "ne") and a.op == "zext" and c <= mask(a.args[0].sz):
            return cmp(op, a.args[0], C(c, a.args[0].sz))
        if op in ("eq", "ne") and a.op == "zext" and c > mask(a.args[0].sz):
            return C(1 if op == "ne" else 0, 1)
        if op in ("eq", "ne") and a.op == "sext" and to_signed(c, sz) in range(-(1 << (8 * a.args[0].sz - 1)),
                                                                              1 << (8 * a.args[0].sz - 1)):
            return cmp(op, a.args[0], C(c, a.args[0].sz))
    if op in ("eq", "ne") and a.op == "sub" and b.op == "c" and b.aux == 0:         # (x - y) == 0
        return cmp(op, a.args[0], a.args[1])
    return X(op, (a, b), 1)


def invert(c: X) -> X:
    """불리언 식의 부정."""
    if c.op == "c":
        return C(0 if c.aux else 1, 1)
    if c.op in CMP_OPS:
        return X(CMP_INV[c.op], c.args, 1)
    if c.op == "lnot":
        return c.args[0]
    if c.op in FCMP_OPS:                                  # NaN 때문에 반대 비교로 바꿀 수 없다: !(a < b) 는 a >= b 가 아니다
        return X("lnot", (c,), 1)
    if c.op == "land":
        return lor(invert(c.args[0]), invert(c.args[1]))
    if c.op == "lor":
        return land(invert(c.args[0]), invert(c.args[1]))
    return cmp("eq", c, C(0, c.sz))


def _chain(e: X, op: str, out: list) -> list:
    if e.op == op:
        _chain(e.args[0], op, out)
        _chain(e.args[1], op, out)
    else:
        out.append(e)
    return out


def _fcmp_fold(items: list, conj: bool) -> list:
    """ucomisd 뒤의 `jp` + `jne`(=같지 않음)·`jnp` + `je`(=같음) 짝을 C 의 `a != b`·`a == b` 로 합친다.
    ZF=1 은 같거나 정렬 불가라 `!(a < b || a > b)`, 정렬 불가는 `funord` 로 풀려 있으므로 세 항이 모두 있으면 한 비교가 된다."""
    for e in list(items):
        if e.op == ("funord" if not conj else "lnot") and (not conj or (e.args[0].op == "funord")):
            u = e if not conj else e.args[0]
            A, B = u.args
            lt, gt = X("flt", (A, B), 1), X("fgt", (A, B), 1)
            need = [lt, gt] if not conj else [X("lnot", (lt,), 1), X("lnot", (gt,), 1)]
            if all(n in items for n in need):
                rest = [x for x in items if x is not e and x not in need]
                return rest + [X("fne" if not conj else "feq", (A, B), 1)]
    return items


def land(a: X, b: X) -> X:
    if a.op == "c":
        return b if a.aux else C(0, 1)
    if b.op == "c":
        return a if b.aux else C(0, 1)
    if a == b:
        return a
    if (a.op in FCMP_OPS or b.op in FCMP_OPS or a.op == "lnot" or b.op == "lnot" or a.op == "land" or b.op == "land"):
        items = _chain(b, "land", _chain(a, "land", []))
        if any(x.op == "lnot" and x.args[0].op in FCMP_OPS for x in items):
            folded = _fcmp_fold(items, True)
            if folded is not items:
                r = folded[0]
                for x in folded[1:]:
                    r = X("land", (r, x), 1)
                return r
    return X("land", (a, b), 1)


def lor(a: X, b: X) -> X:
    if a.op == "c":
        return C(1, 1) if a.aux else b
    if b.op == "c":
        return C(1, 1) if b.aux else a
    if a == b:
        return a
    if (a.op in FCMP_OPS or b.op in FCMP_OPS or a.op == "lor" or b.op == "lor"):
        items = _chain(b, "lor", _chain(a, "lor", []))
        if any(x.op == "funord" for x in items):
            folded = _fcmp_fold(items, False)
            if folded is not items:
                r = folded[0]
                for x in folded[1:]:
                    r = X("lor", (r, x), 1)
                return r
    return X("lor", (a, b), 1)


def truth(e: X) -> X:
    """임의의 값을 '0 이 아닌가' 불리언으로."""
    if e.op in BOOL_OPS:
        return e
    return cmp("ne", e, C(0, e.sz))


def _as_bool(x: X):
    """0/1 상수·불리언 식·불리언의 0 확장을 불리언 식으로(아니면 None)."""
    if x.op == "c" and x.aux in (0, 1):
        return C(x.aux, 1)
    if x.op in BOOL_OPS:
        return x
    if x.op == "zext" and x.args[0].op in BOOL_OPS:
        return x.args[0]
    return None


def ite(c: X, a: X, b: X) -> X:
    if c.op == "c":
        return a if c.aux else b
    if a == b:
        return a
    if c.op == "lnot":
        return ite(c.args[0], b, a)
    if a.sz == b.sz and c.op in BOOL_OPS:                                   # 값이 모두 0/1 이면 &&·|| 로(부동소수점 비교의 NaN 처리 짝을 합치기 위해서도 필요)
        ba, bb = _as_bool(a), _as_bool(b)
        if ba is not None and bb is not None and (ba.op == "c" or bb.op == "c") and (ba.op != "c" or bb.op != "c"):
            r = _bool_ite(c, ba, bb)
            return r if a.sz == 1 else zext(r, a.sz)
    return X("ite", (c, a, b), a.sz)


def insert(old: X, val: X) -> X:
    """old(전체 폭) 의 아래쪽 val.sz 바이트를 val 로 바꾼 값(부분 레지스터 쓰기)."""
    if val.sz == old.sz:
        return val
    if old.op == "c" and old.aux == 0:
        return zext(val, old.sz)
    return X("ins", (old, val), old.sz, val.sz)


# ---- 플래그 -----------------------------------------------------------------------
def flags(kind: str, args, sz: int) -> X:
    return X("fl", tuple(args), sz, kind)


def cc(code: str, fl: X) -> X:
    return X("cc", (fl,), 1, code)


def _zf(r):
    return cmp("eq", r, C(0, r.sz))


def _sf(r):
    return cmp("slt", r, C(0, r.sz))


ARM_CODES = {"eq", "hs", "lo", "mi", "pl", "vs", "vc", "hi", "ls", "lt", "gt", "al", "nv"}
_ARM_TO_X86_SUB = {"eq": "e", "ne": "ne", "hs": "ae", "lo": "b", "hi": "a", "ls": "be", "mi": "s", "pl": "ns", "vs": "o", "vc": "no",
                   "ge": "ge", "lt": "l", "gt": "g", "le": "le"}


def _reduce_arm(code: str, fl: X):
    """ARM 조건 이름을 플래그 원천 종류에 맞춰 푼다(ARM 의 C 는 뺄셈에서는 '빌림 없음', 덧셈에서는 '자리올림')."""
    if code == "al":
        return C(1, 1)
    if code == "nv":
        return C(0, 1)
    kind, a = fl.aux, fl.args
    sz = fl.sz
    if kind == "nzcv":
        v = a[0].aux if a and a[0].op == "c" else None
        if v is None:
            return None
        N, Z, Cc, V = bool(v & 8), bool(v & 4), bool(v & 2), bool(v & 1)
        res = {"eq": Z, "ne": not Z, "hs": Cc, "lo": not Cc, "mi": N, "pl": not N, "vs": V, "vc": not V, "hi": Cc and not Z,
               "ls": (not Cc) or Z, "ge": N == V, "lt": N != V, "gt": (not Z) and N == V, "le": Z or N != V}.get(code)
        return None if res is None else C(1 if res else 0, 1)
    if kind == "sub":
        return reduce_cc(_ARM_TO_X86_SUB[code], fl)
    if kind == "add":
        x, y, r = a[0], a[1], a[2]
        carry = cmp("ult", r, x)
        zf = _zf(r)
        if code in ("eq", "ne", "mi", "pl"):
            return reduce_cc({"eq": "e", "ne": "ne", "mi": "s", "pl": "ns"}[code], fl)
        if code == "hs":
            return carry
        if code == "lo":
            return invert(carry)
        if code == "hi":
            return land(carry, invert(zf))
        if code == "ls":
            return lor(invert(carry), zf)
        if code in ("vs", "vc"):
            ovf = cmp("slt", binop("and", binop("xor", x, r), binop("xor", y, r)), C(0, sz))
            return ovf if code == "vs" else invert(ovf)
        return reduce_cc({"ge": "ge", "lt": "l", "gt": "g", "le": "le"}[code], fl)
    if kind == "logic":
        r = a[0]
        if code in ("eq", "ne", "mi", "pl"):
            return reduce_cc({"eq": "e", "ne": "ne", "mi": "s", "pl": "ns"}[code], fl)
        if code in ("hs", "hi", "vs"):
            return C(0, 1)
        if code in ("lo", "ls", "vc"):
            return C(1, 1)
        return reduce_cc({"ge": "ge", "lt": "l", "gt": "g", "le": "le"}[code], fl)
    return None


def is_flagsrc(e: X) -> bool:
    return e.op == "fl" or (e.op == "ite" and is_flagsrc(e.args[1]) and is_flagsrc(e.args[2]))


def reduce_cc(code: str, fl: X):
    """조건 코드 `code` 를 플래그 원천 `fl` 에서 직접 비교식으로 푼다. 풀 수 없으면 None.

    fl 의 인자 배치: sub → (A, B[, R]) · add → (A, B, R) · logic → (R) · inc/dec → (A, 이전 플래그, R) · neg → (A, R) · res → (R).
    R 은 '연산이 끝난 뒤의 값'이다. e/ne/s/ns 는 R 만으로 풀리므로(대입 뒤에도 안정적) 가능하면 R 을 쓴다."""
    if fl.op == "ite":                               # 조건부 플래그(ccmp): 두 쪽을 각각 풀어 불리언 ite 로
        ra, rb = reduce_cc(code, fl.args[1]), reduce_cc(code, fl.args[2])
        if ra is None or rb is None:
            return None
        return _bool_ite(fl.args[0], ra, rb)
    if code in ARM_CODES or fl.aux == "nzcv":
        return _reduce_arm(code, fl)
    kind, a = fl.aux, fl.args
    sz = fl.sz
    if code in ARM_CODES_SHARED and kind in ("sub", "add", "logic") and False:
        pass
    if code in ("no", "ns", "np", "nc", "nl", "nge", "nle", "ng", "nb", "nae", "nbe", "na", "po") or \
            (code.startswith("n") and code[1:] in ("o", "s", "p", "c")):
        base = {"nl": "ge", "nge": "l", "nle": "g", "ng": "le", "nb": "ae", "nae": "b", "nbe": "a", "na": "be",
                "po": "p"}.get(code, code[1:])
        if code in ("nl", "nge", "nle", "ng", "nb", "nae", "nbe", "na", "po"):
            return reduce_cc(base, fl)
        r = reduce_cc(base, fl)
        return invert(r) if r is not None else None
    code = {"z": "e", "nz": "ne", "c": "b", "pe": "p"}.get(code, code)

    def by_result(r):
        if code == "e":
            return _zf(r)
        if code == "ne":
            return invert(_zf(r))
        if code == "s":
            return _sf(r)
        if code == "ns":
            return invert(_sf(r))
        return None

    if kind == "sub":
        x, y = a[0], a[1]
        if len(a) > 2 and code in ("e", "ne", "s"):
            return by_result(a[2])
        table = {"e": "eq", "ne": "ne", "l": "slt", "ge": "sge", "le": "sle", "g": "sgt",
                 "b": "ult", "ae": "uge", "be": "ule", "a": "ugt"}
        if code in table:
            return cmp(table[code], x, y)
        r = a[2] if len(a) > 2 else sub(x, y)
        if code == "s":
            return _sf(r)
        if code == "o":
            return cmp("slt", binop("and", binop("xor", x, y), binop("xor", x, r)), C(0, sz))
        return None
    if kind == "logic":                           # test / and / or / xor: CF=OF=0
        r = a[0]
        table = {"e": ("eq", 0), "ne": ("ne", 0), "s": ("slt", 0), "l": ("slt", 0), "ge": ("sge", 0), "le": ("sle", 0),
                 "g": ("sgt", 0), "be": ("eq", 0), "a": ("ne", 0)}
        if code in table:
            op, c = table[code]
            return cmp(op, r, C(c, sz))
        if code in ("b", "o"):
            return C(0, 1)
        if code == "ae":
            return C(1, 1)
        return None
    if kind == "add":
        x, y, r = a[0], a[1], a[2]
        if code in ("e", "ne", "s"):
            return by_result(r)
        if code == "b":
            return cmp("ult", r, x)
        if code == "ae":
            return cmp("uge", r, x)
        if code == "be":
            return lor(cmp("ult", r, x), _zf(r))
        if code == "a":
            return land(cmp("uge", r, x), invert(_zf(r)))
        ovf = cmp("slt", binop("and", binop("xor", x, r), binop("xor", y, r)), C(0, sz))
        return _signed_cc(code, r, ovf)
    if kind in ("inc", "dec"):
        x, prev, r = a[0], a[1], a[2]
        if code in ("e", "ne", "s"):
            return by_result(r)
        ovf = cmp("eq", r if kind == "inc" else x, C(1 << (8 * sz - 1), sz))
        if code in ("o", "l", "ge", "le", "g"):
            return _signed_cc(code, r, ovf)
        if prev is not None and prev.op == "fl" and code in ("b", "ae"):          # CF 는 이전 플래그를 그대로
            return reduce_cc(code, prev)
        return None
    if kind == "neg":
        x, r = a[0], a[1]
        if code in ("e", "ne", "s"):
            return by_result(r)
        if code == "b":
            return cmp("ne", x, C(0, sz))
        if code == "ae":
            return cmp("eq", x, C(0, sz))
        return None
    if kind == "res":                             # 시프트 등: ZF·SF 만 안다
        return by_result(a[0]) if code in ("e", "ne", "s") else None
    if kind == "fcmp":                            # comiss/ucomisd: 같음·정렬 불가 → ZF=1, 작음·정렬 불가 → CF=1, 정렬 불가 → PF=1
        A, B = a
        lt, gt = X("flt", (A, B), 1), X("fgt", (A, B), 1)
        if code == "a":
            return gt
        if code == "ae":
            return X("fge", (A, B), 1)
        if code == "b":
            return invert(X("fge", (A, B), 1))
        if code == "be":
            return invert(gt)
        if code == "p":
            return X("funord", (A, B), 1)
        if code == "ne":
            return lor(lt, gt)
        if code == "e":
            return land(invert(lt), invert(gt))
        return None
    if kind == "bt":                              # bt/bts/btr/btc: CF = 검사한 비트(인자 하나가 그 비트 값 0/1)
        bit = a[0]
        if code == "b":
            return cmp("ne", bit, C(0, bit.sz))
        if code == "ae":
            return cmp("eq", bit, C(0, bit.sz))
        return None
    return None


ARM_CODES_SHARED = {"ne", "ge", "le"}


def _bool_ite(c, a, b):
    if a.op == "c" and b.op == "c":
        if a.aux == b.aux:
            return a
        return c if a.aux else invert(c)
    if b.op == "c":
        return land(c, a) if not b.aux else lor(invert(c), a)
    if a.op == "c":
        return lor(c, b) if a.aux else land(invert(c), b)
    return ite(c, a, b)


def _signed_cc(code, r, ovf):
    sf = _sf(r)
    if code == "o":
        return ovf
    if code == "l":
        return _xor_b(sf, ovf)
    if code == "ge":
        return invert(_xor_b(sf, ovf))
    if code == "le":
        return lor(_zf(r), _xor_b(sf, ovf))
    if code == "g":
        return land(invert(_zf(r)), invert(_xor_b(sf, ovf)))
    return None


def _xor_b(a: X, b: X) -> X:
    return cmp("ne", a, b)


# ---- 순회·치환 --------------------------------------------------------------------
def rebuild(e: X, args) -> X:
    """같은 op 로 자식만 바꿔 다시 만든다(정규화 규칙을 다시 적용)."""
    op = e.op
    if tuple(args) == e.args:
        return e
    if op in ARITH:
        return binop(op, args[0], args[1], e.sz)
    if op in ("neg", "not"):
        return unop(op, args[0])
    if op in CMP_OPS:
        return cmp(op, args[0], args[1])
    if op == "zext":
        return zext(args[0], e.sz)
    if op == "sext":
        return sext(args[0], e.sz)
    if op == "trunc":
        return trunc(args[0], e.sz)
    if op == "ite":
        return ite(args[0], args[1], args[2])
    if op == "land":
        return land(args[0], args[1])
    if op == "lor":
        return lor(args[0], args[1])
    if op == "lnot":
        return invert(args[0])
    if op == "ins":
        return insert(args[0], args[1])
    if op == "cc":
        fl = args[0]
        if fl.op == "fl" or (fl.op == "ite" and is_flagsrc(fl)):
            r = reduce_cc(e.aux, fl)
            if r is not None:
                return r
        return X("cc", (fl,), 1, e.aux)
    if op == "mem":
        return X("mem", (args[0],), e.sz)
    return X(op, args, e.sz, e.aux)


def walk(e: X):
    """식의 모든 부분식(자신 포함)을 깊이 우선으로."""
    stack = [e]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.args)


def subst(e: X, fn) -> X:
    """fn(node) 가 None 이 아닌 값을 돌려주면 그 노드를 바꾼다(아래로는 더 내려가지 않음). 아니면 자식을 재귀 치환."""
    r = fn(e)
    if r is not None:
        return r
    if not e.args:
        return e
    new = tuple(subst(a, fn) for a in e.args)
    return rebuild(e, new) if any(n is not o for n, o in zip(new, e.args)) else e


def uses(e: X, kinds=("loc", "var")):
    return [n for n in walk(e) if n.op in kinds]


def has_op(e: X, ops) -> bool:
    for n in walk(e):
        if n.op in ops:
            return True
    return False


def size_of(e: X) -> int:
    n = 0
    stack = [e]
    while stack:
        x = stack.pop()
        n += 1
        stack.extend(x.args)
    return n


# ---- 문장·블록 ----------------------------------------------------------------------
class S:
    """문장. kind: asg(dst←src) · call(dst←call 식) · unk(알 수 없는 명령) · 종결: jmp cj ret ij sw noret"""
    __slots__ = ("k", "addr", "dst", "src", "t", "f", "cases", "extra", "meta")

    def __init__(self, k, addr=0, dst=None, src=None, t=None, f=None, cases=None, extra=None):
        self.meta = None
        self.k = k
        self.addr = addr
        self.dst = dst
        self.src = src
        self.t = t
        self.f = f
        self.cases = cases
        self.extra = extra

    def __repr__(self):
        return f"<{self.k} {self.addr:#x} {self.dst!r} <- {self.src!r} t={self.t} f={self.f}>"


class Block:
    __slots__ = ("start", "stmts", "term", "succs", "preds", "end", "label", "tail")

    def __init__(self, start):
        self.start = start
        self.end = start
        self.stmts = []
        self.term = None
        self.succs = []
        self.preds = []
        self.label = None
        self.tail = False

    def __repr__(self):
        return f"<Block {self.start:#x} n={len(self.stmts)} succs={[hex(s) for s in self.succs]}>"
