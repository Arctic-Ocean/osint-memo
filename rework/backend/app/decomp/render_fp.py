"""부동소수점 식의 C 출력.

IR 은 float·double 값을 IEEE 비트 패턴(폭 4·8 의 정수 식)으로 들고 다닌다. 출력 단계에서 '어느 변수·메모리가 부동소수점인지'(fkind·fpmem)를
산술·비교·변환이 쓰이는 방식으로 추정하고, 그 식은 `double`/`float` 로 자연스럽게 쓴다. 정수와 섞이는 곳은 `__builtin_bit_cast` 로 비트를 그대로 옮긴다.
"""
from __future__ import annotations

import math
import struct

from . import ir
from .ir import X, mask

PREC = {"prim": 16, "unary": 14, "mul": 13, "add": 12, "shift": 11, "rel": 10, "eq": 9, "band": 8, "bxor": 7,
        "bor": 6, "land": 5, "lor": 4, "cond": 3}

FTYPE = {4: "float", 8: "double", 10: "long double"}
_SUF = {4: "f", 8: "", 10: "l"}                                                            # C 라이브러리 함수 접미사(sinf·sin·sinl)
_MATH = {"fsin": "sin", "fcos": "cos", "ftan": "tan", "flog2": "log2", "fexp2": "exp2", "fatan2": "atan2", "fmod": "fmod", "fremainder": "remainder"}
_BIN = {"fadd": ("+", "add"), "fsub": ("-", "add"), "fmul": ("*", "mul"), "fdiv": ("/", "mul")}
_CMP = {"feq": ("==", "eq"), "fne": ("!=", "eq"), "flt": ("<", "rel"), "fle": ("<=", "rel"), "fgt": (">", "rel"), "fge": (">=", "rel")}
FP_VALUE_OPS = ir.FOPS2 | ir.FOPS1 | ir.FOPS3 | {"i2f", "f2f"}          # 부동소수점 값을 만드는 연산


def _ext_literal(bits: int) -> str:
    """x87 80비트 확장 정밀도 비트 패턴 → C long double 상수(소수 21자리면 64비트 가수가 왕복된다)."""
    from decimal import Decimal, getcontext
    from .x87 import ext_to_float
    mant, se = bits & ((1 << 64) - 1), (bits >> 64) & 0xFFFF
    sign, exp = se >> 15, se & 0x7FFF
    if exp == 0x7FFF:
        return "NAN" if mant << 1 & ((1 << 64) - 1) else ("-" if sign else "") + "INFINITY"
    if mant == 0:
        return "-0.0L" if sign else "0.0L"
    getcontext().prec = 60
    v = Decimal(mant) * (Decimal(2) ** (exp - 16383 - 63 if exp else -16382 - 63))
    for digits in range(1, 23):                                            # 다시 80비트로 읽었을 때 같은 값이 되는 가장 짧은 자릿수
        t = f"{v:.{digits}g}"
        if _decimal_to_ext(Decimal(t)) == (exp, mant):
            break
    if "." not in t and "e" not in t.lower():
        t += ".0"
    return ("-" if sign else "") + t + "L"


def _decimal_to_ext(d) -> tuple:
    """양의 십진수 → (80비트 지수 필드, 64비트 가수)(가장 가까운 짝수로 반올림). 확장 정밀도 범위를 벗어나면 (0, 0)."""
    from fractions import Fraction
    f = Fraction(d)
    if f <= 0:
        return (0, 0)
    e = f.numerator.bit_length() - f.denominator.bit_length()               # f 는 대략 2^e
    if Fraction(2) ** e > f:
        e -= 1
    # 정규화: 1 <= f / 2^e < 2, 가수 = f / 2^e * 2^63
    scaled = f / (Fraction(2) ** e) * (1 << 63)
    m = scaled.numerator // scaled.denominator
    rem = scaled - m
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and m & 1):
        m += 1
    if m >> 64:
        m >>= 1
        e += 1
    biased = e + 16383
    return (biased, m) if 0 < biased < 0x7FFF else (0, 0)


def fp_literal(bits: int, kind: int) -> str:
    """IEEE 비트 패턴 → C 부동소수점 상수(왕복이 정확한 가장 짧은 표기)."""
    if kind == 10:
        return _ext_literal(bits)
    if kind == 8:
        v = struct.unpack("<d", (bits & mask(8)).to_bytes(8, "little"))[0]
        if math.isnan(v):
            return "NAN"
        if math.isinf(v):
            return "INFINITY" if v > 0 else "-INFINITY"
        t = repr(v)
        return t if ("." in t or "e" in t or "n" in t) else t + ".0"
    v = struct.unpack("<f", (bits & mask(4)).to_bytes(4, "little"))[0]
    if math.isnan(v):
        return "NAN"
    if math.isinf(v):
        return "INFINITY" if v > 0 else "-INFINITY"
    for prec in range(1, 10):
        t = f"{v:.{prec}g}"
        if struct.pack("<f", float(t)) == (bits & mask(4)).to_bytes(4, "little"):
            break
    if "." not in t and "e" not in t:
        t += ".0"
    return t + "f"


class FpRender:
    """Renderer 에 섞어 쓰는 부동소수점 출력."""

    # ---- 추정 ----------------------------------------------------------------------------
    def fp_prepare(self, roots, asgs):
        """roots: 모든 식의 뿌리, asgs: [(대입 대상, 원천)]. 부동소수점으로 쓰이는 웹(fkind)과 메모리 접근(fpmem)을 정한다."""
        fd = self.fd
        fk: dict = {}
        fpmem: set = set()
        webs = self.webs

        def put(w, kind):
            if w in fk:
                return False
            if webs[w].sz != kind:
                return False
            fk[w] = kind
            return True

        for w, kind in getattr(fd, "fp_param_kinds", {}).items():
            put(w, kind)
        if getattr(fd, "ret_fp", None):
            for b in fd.blocks.values():
                if b.term.k == "ret" and b.term.src is not None and b.term.src.op == "var":
                    put(b.term.src.aux, fd.ret_fp)

        def mark(e, kind):
            if e.op == "var":
                put(e.aux, kind)
            elif e.op == "mem":
                fpmem.add(e)

        for r in roots:
            for n in ir.walk(r):
                op = n.op
                if op in ir.FOPS2:
                    mark(n.args[0], n.sz)
                    mark(n.args[1], n.sz)
                elif op in ir.FOPS3:
                    for a_ in n.args:
                        mark(a_, n.sz)
                elif op in ir.FOPS1:
                    mark(n.args[0], n.sz)
                elif op in ("f2i", "f2f"):
                    mark(n.args[0], n.args[0].sz)
                elif op in ir.FCMP_OPS:
                    mark(n.args[0], n.args[0].sz)
                    mark(n.args[1], n.args[1].sz)
                elif op == "call":
                    kinds = (n.aux or {}).get("fp_kinds") or ()
                    nfp = len(kinds)
                    if nfp:
                        for a, k in zip(n.args[len(n.args) - nfp:], kinds):
                            mark(a, k)
                    for a, k in zip(n.args[1:], (n.aux or {}).get("arg_kinds") or ()):          # x86-32: 스택 실수 인자
                        if k:
                            mark(a, k)
        changed = True
        rounds = 0
        while changed and rounds < 8:
            changed = False
            rounds += 1
            for dst, src in asgs:
                k = None
                if src.op in FP_VALUE_OPS:
                    k = src.sz
                elif src.op == "var" and src.aux in fk:
                    k = fk[src.aux]
                elif src.op == "call" and (src.aux or {}).get("ret_fp"):
                    k = src.aux["ret_fp"]
                if dst.op == "var":
                    if k and dst.aux not in fk and put(dst.aux, k):
                        changed = True
                    elif dst.aux in fk:
                        kk = fk[dst.aux]
                        if src.op == "var" and src.aux not in fk and put(src.aux, kk):
                            changed = True
                        elif src.op == "mem" and src not in fpmem and src.sz == kk:
                            fpmem.add(src)
                            changed = True
                elif dst.op == "mem":
                    if k and dst not in fpmem and dst.sz == k:
                        fpmem.add(dst)
                        changed = True
                    elif dst in fpmem and src.op == "var" and src.aux not in fk and put(src.aux, dst.sz):
                        changed = True
        self.fkind = fk
        self.fpmem = fpmem

    def fkind_of(self, e: X):
        """식이 부동소수점 값이면 폭(4·8), 아니면 None."""
        if e.op in FP_VALUE_OPS:
            return e.sz
        if e.op == "var":
            return self.fkind.get(e.aux)
        if e.op == "call" and (e.aux or {}).get("ret_fp"):
            return e.aux["ret_fp"]
        if e.op == "mem" and e in self.fpmem:
            return e.sz
        return None

    # ---- 출력 ----------------------------------------------------------------------------
    def bits_of_fp(self, e: X):
        """부동소수점 식을 정수 문맥에서 쓸 때: 비트 그대로 옮긴다."""
        kind = self.fkind_of(e) or e.sz
        t, p = self.PF(e, kind)
        if kind == 10:                                                      # 80비트는 정수 형에 비트를 그대로 옮길 수 없다: 값을 64비트 정수로 바꿔 쓴다(정수 문맥에서 쓰이는 일은 드물다)
            return f"(uint64_t)({t})", "u", PREC["unary"]
        ty = "uint64_t" if kind == 8 else "uint32_t"
        return f"__builtin_bit_cast({ty}, {t})", "u", PREC["prim"]

    def fp_cmp_text(self, e: X):
        op = e.op
        A, B = e.args
        kind = A.sz
        ta, pa = self.PF(A, kind)
        tb, pb = self.PF(B, kind)
        if op == "funord":
            return f"__builtin_isunordered({ta}, {tb})", None, PREC["prim"]
        sym, pr = _CMP[op]
        return f"{self._paren(ta, pa, PREC[pr] + 1)} {sym} {self._paren(tb, pb, PREC[pr] + 1)}", None, PREC[pr]

    def PF(self, e: X, kind: int):
        """폭 kind(4 float · 8 double)의 부동소수점 값으로 쓴 식 → (문자열, 우선순위)."""
        op = e.op
        ty = FTYPE[kind]
        if op in _BIN and e.sz == kind:
            sym, pr = _BIN[op]
            ta, pa = self.PF(e.args[0], kind)
            tb, pb = self.PF(e.args[1], kind)
            return f"{self._paren(ta, pa, PREC[pr])} {sym} {self._paren(tb, pb, PREC[pr] + 1)}", PREC[pr]
        if op in ("fcmin", "fcmax") and e.sz == kind:
            ta, pa = self.PF(e.args[0], kind)
            tb, pb = self.PF(e.args[1], kind)
            nm = ("fmin" if op == "fcmin" else "fmax") + _SUF[kind]
            return f"{nm}({ta}, {tb})", PREC["prim"]
        if op == "ffma" and e.sz == kind:
            ts = [self.PF(a_, kind)[0] for a_ in e.args]
            return f"fma{_SUF[kind]}({', '.join(ts)})", PREC["prim"]
        if op == "fround" and e.sz == kind:
            t, p = self.PF(e.args[0], kind)
            return f"{e.aux}{_SUF[kind]}({t})", PREC["prim"]
        if op in ("fmin", "fmax") and e.sz == kind:
            ta, pa = self.PF(e.args[0], kind)
            tb, pb = self.PF(e.args[1], kind)
            sym = "<" if op == "fmin" else ">"
            ta1, tb1 = self._paren(ta, pa, PREC["rel"] + 1), self._paren(tb, pb, PREC["rel"] + 1)
            return f"{ta1} {sym} {tb1} ? {ta1} : {tb1}", PREC["cond"]
        if op in ir.FMATH1 and e.sz == kind:                                       # x87 초월함수: sin·cos·tan·log2·exp2
            t, p = self.PF(e.args[0], kind)
            return f"{_MATH[op]}{_SUF[kind]}({t})", PREC["prim"]
        if op in ir.FMATH2 and e.sz == kind:
            ta, pa = self.PF(e.args[0], kind)
            tb, pb = self.PF(e.args[1], kind)
            return f"{_MATH[op]}{_SUF[kind]}({ta}, {tb})", PREC["prim"]
        if op == "fsqrt" and e.sz == kind:
            t, p = self.PF(e.args[0], kind)
            return f"sqrt{_SUF[kind]}({t})", PREC["prim"]
        if op == "fabs" and e.sz == kind:
            t, p = self.PF(e.args[0], kind)
            return f"fabs{_SUF[kind]}({t})", PREC["prim"]
        if op == "fneg" and e.sz == kind:
            t, p = self.PF(e.args[0], kind)
            return f"-{self._paren(t, p, PREC['unary'])}", PREC["unary"]
        if op == "i2f" and e.sz == kind:
            src = e.args[0]
            t = self.fit(src, e.aux if e.aux in ("s", "u") else "s", PREC["unary"])
            return f"({ty}){t}", PREC["unary"]
        if op == "f2f" and e.sz == kind:
            t, p = self.PF(e.args[0], e.args[0].sz)
            return f"({ty}){self._paren(t, p, PREC['unary'])}", PREC["unary"]
        if op == "c" and e.sz == kind:
            return fp_literal(e.aux, kind), (PREC["unary"] if fp_literal(e.aux, kind).startswith("-") else PREC["prim"])
        if op == "var" and self.fkind.get(e.aux) == kind:
            return self.vname(e.aux), PREC["prim"]
        if op == "mem" and e.sz == kind:
            addr = e.args[0]
            if addr.op == "c":                                        # 읽기 전용 데이터의 상수
                lit = self.fp_const_load(addr.aux, kind)
                if lit is not None:
                    return lit, (PREC["unary"] if lit.startswith("-") else PREC["prim"])
            if addr.op == "stk":
                t, s, p = self.mem_slot(addr.aux, kind, "f")
                return t, p
            pd = self.ptr_deref(addr, kind, "f")
            if pd is not None:
                return pd[0], pd[2]
            return f"*({ty} *){self.addr_text(addr)}", PREC["unary"]
        if op == "call" and (e.aux or {}).get("ret_fp") == kind:
            return self.call_text(e), PREC["prim"]
        if op == "ite" and e.sz == kind:
            c = self.cond(e.args[0], PREC["cond"] + 1)
            ta, pa = self.PF(e.args[1], kind)
            tb, pb = self.PF(e.args[2], kind)
            return f"{c} ? {self._paren(ta, pa, PREC['cond'] + 1)} : {self._paren(tb, pb, PREC['cond'])}", PREC["cond"]
        if op == "xor" and e.sz == kind and e.args[1].op == "c" and e.args[1].aux == 1 << (8 * kind - 1):        # 부호 비트 뒤집기 = 부호 반전
            t, p = self.PF(e.args[0], kind)
            return f"-{self._paren(t, p, PREC['unary'])}", PREC["unary"]
        if op == "and" and e.sz == kind and e.args[1].op == "c" and e.args[1].aux == mask(kind) >> 1:               # 부호 비트 지우기 = 절댓값
            t, p = self.PF(e.args[0], kind)
            return f"fabs{_SUF[kind]}({t})", PREC["prim"]
        # 그 밖에는 비트 패턴을 그대로 부동소수점으로 본다
        if kind == 10:
            t, s, p = self.P(e if e.sz == 8 else (ir.trunc(e, 8) if e.sz > 8 else ir.zext(e, 8)), "u")
            return f"__builtin_bit_cast(double, (uint64_t){self._paren(t, p, PREC['unary'])})", PREC["prim"]
        ity = "uint64_t" if kind == 8 else "uint32_t"
        if e.sz != kind:
            e = ir.trunc(e, kind) if e.sz > kind else ir.zext(e, kind)
        t, s, p = self.P(e, "u")
        return f"__builtin_bit_cast({ty}, ({ity}){self._paren(t, p, PREC['unary'])})", PREC["prim"]

    def fp_const_load(self, addr: int, kind: int):
        """읽기 전용 구역의 상수 주소에서 읽은 값을 C 상수로(쓰기 가능 구역이면 None)."""
        sec = self.fd.model.section_for(addr)
        if sec is None or sec.is_code or sec.writable:
            return None
        data = self.fd.prog.dis.read(addr, kind)
        if not data or len(data) < kind:
            return None
        return fp_literal(int.from_bytes(data, "little" if self.fd.model.endian != "big" else "big"), kind)
