"""AST → 의사코드 줄. 식은 C 의 정수 규칙을 정확히 따르도록 필요한 곳에만 캐스트를 넣는다.

식 출력 `P(e, want)` 는 (문자열, 고유 부호 's'|'u'|None, 우선순위)를 돌려주고, 호출한 쪽이 부호를 요구(want)하면
고유 부호와 다를 때만 `(int32_t)` 같은 캐스트를 붙인다. 폭이 4바이트 미만인 연산은 C 의 정수 승격 때문에 결과를 다시 좁은 형으로 캐스트한다.
"""
from __future__ import annotations

import re

from . import ir
from . import ptrtype
from . import tables
from . import protos
from .ir import X, to_signed, mask

PREC = {"prim": 16, "unary": 14, "mul": 13, "add": 12, "shift": 11, "rel": 10, "eq": 9, "band": 8, "bxor": 7,
        "bor": 6, "land": 5, "lor": 4, "cond": 3}


def itype(sz: int, sign: str) -> str:
    return ("int" if sign == "s" else "uint") + f"{8 * sz}_t"


def c_escape(s: str) -> str:
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 32 or o == 127:
            out.append("\\x%02x" % o)
        else:
            out.append(ch)
    return "".join(out)


class Renderer:
    def __init__(self, fd, ast, params, ret_info):
        self.fd = fd
        self.prog = fd.prog
        self.webs = fd.webs
        self.ast = ast
        self.params = params                      # [(web id | None, index)]
        self.ret_sz, self.ret_sign = ret_info
        self.lines: list = []
        self.names: dict = {}                     # web id → 표시 이름
        self.slot_ids: dict = {}                  # web id → 안정적인 슬롯 id
        self.used_names = set()
        self.vsign: dict = {}
        self.cur_addr = fd.addr
        self.incomplete: list = []
        self.ptypes: dict = {}
        self.tbl_sign: dict = {}                  # 룩업 테이블 시작 주소 → 원소 부호(첫 사용 기준)

    def _all_stmts(self):
        """AST 안의 모든 문장(S)."""
        out = []

        def go(ns):
            for n in ns:
                k = n[0]
                if k == "s":
                    out.append(n[1])
                elif k == "if":
                    go(n[2])
                    go(n[3])
                elif k == "while":
                    go(n[2])
                elif k == "for":
                    out.append(n[1])
                    out.append(n[3])
                    go(n[4])
                elif k == "dowhile":
                    go(n[1])
                elif k == "switch":
                    for _, b in n[2]:
                        go(b)

        go(self.ast)
        return out

    # ================= 부호 투표 ==========================================================
    def vote(self, e: X, votes: dict):
        op = e.op
        if op in ("slt", "sle", "sgt", "sge", "sdiv", "srem", "mulhs"):
            for a in e.args:
                self._vote_one(a, "s", votes)
        elif op in ("ult", "ule", "ugt", "uge", "udiv", "urem", "mulhu"):
            for a in e.args:
                self._vote_one(a, "u", votes)
        elif op == "sar":
            self._vote_one(e.args[0], "s", votes)
        elif op == "shr":
            self._vote_one(e.args[0], "u", votes)
        elif op == "sext":
            self._vote_one(e.args[0], "s", votes)
        for a in e.args:
            self.vote(a, votes)

    @staticmethod
    def _vote_one(a, sign, votes):
        if a.op == "var":
            d = votes.setdefault(a.aux, {"s": 0, "u": 0})
            d[sign] += 1

    # ================= 이름·형 ============================================================
    def var_nodes(self, nodes):
        """AST 에 나타나는 변수들을 처음 나타나는 순서로."""
        seen = []
        seen_set = set()

        def add(e):
            for n in ir.walk(e):
                if n.op == "var" and n.aux not in seen_set:
                    seen_set.add(n.aux)
                    seen.append(n.aux)

        def go(ns):
            for n in ns:
                k = n[0]
                if k == "s":
                    s = n[1]
                    if s.k == "asg":
                        if s.dst.op == "var" and s.dst.aux not in seen_set:
                            seen_set.add(s.dst.aux)
                            seen.append(s.dst.aux)
                        elif s.dst.op == "mem":
                            add(s.dst.args[0])
                        add(s.src)
                    elif s.k == "call":
                        if s.dst is not None and s.dst.aux not in seen_set:
                            seen_set.add(s.dst.aux)
                            seen.append(s.dst.aux)
                        add(s.src)
                    elif s.k == "unk":
                        for v in s.extra.get("usevars", ()) + s.extra.get("defvars", ()):
                            if v.aux not in seen_set:
                                seen_set.add(v.aux)
                                seen.append(v.aux)
                elif k == "if":
                    add(n[1])
                    go(n[2])
                    go(n[3])
                elif k == "while":
                    if n[1] is not None:
                        add(n[1])
                    go(n[2])
                elif k == "for":
                    go([("s", n[1])])
                    add(n[2])
                    go([("s", n[3])])
                    go(n[4])
                elif k == "dowhile":
                    go(n[1])
                    add(n[2])
                elif k == "switch":
                    add(n[1])
                    for _, b in n[2]:
                        go(b)
                    if n[3]:
                        go(n[3])
                elif k == "ret" and n[1] is not None:
                    add(n[1])

        go(nodes)
        return seen

    def prepare(self):
        fd = self.fd
        order = self.var_nodes(self.ast)
        pset = {w for w, _ in self.params if w is not None}
        for w in pset:
            if w not in order:
                order.append(w)
        votes: dict = {}

        def walk_ast(ns):
            for n in ns:
                k = n[0]
                if k == "s":
                    s = n[1]
                    if s.k == "asg":
                        self.vote(s.src, votes)
                        if s.dst.op == "mem":
                            self.vote(s.dst.args[0], votes)
                    elif s.k == "call":
                        self.vote(s.src, votes)
                elif k == "if":
                    self.vote(n[1], votes)
                    walk_ast(n[2])
                    walk_ast(n[3])
                elif k == "while":
                    if n[1] is not None:
                        self.vote(n[1], votes)
                    walk_ast(n[2])
                elif k == "for":
                    walk_ast([("s", n[1])])
                    self.vote(n[2], votes)
                    walk_ast([("s", n[3])])
                    walk_ast(n[4])
                elif k == "dowhile":
                    walk_ast(n[1])
                    self.vote(n[2], votes)
                elif k == "switch":
                    self.vote(n[1], votes)
                    for _, b in n[2]:
                        walk_ast(b)
                    if n[3]:
                        walk_ast(n[3])
                elif k == "ret" and n[1] is not None:
                    self.vote(n[1], votes)

        walk_ast(self.ast)
        for w in order:
            web = self.webs[w]
            v = votes.get(w)
            if v and v["s"] != v["u"]:
                sign = "s" if v["s"] > v["u"] else "u"
            else:
                sign = "s" if web.sz >= 4 else "u"
            self.vsign[w] = sign
        # 포인터 형: 메모리 접근의 주소식에서 추정
        exprs, copies, derived = [], [], []

        def collect(ns):
            for n in ns:
                k = n[0]
                if k == "s":
                    s_ = n[1]
                    if s_.k == "asg":
                        exprs.append(s_.src)
                        if s_.dst.op == "mem":
                            exprs.append(s_.dst)
                        elif s_.src.op == "var":
                            copies.append((s_.dst.aux, s_.src.aux))
                        elif s_.src.op == "add":
                            bs, ix, _o, rest = ptrtype.split_addr(s_.src, fd.full)
                            if len(bs) == 1 and not rest and s_.dst.op == "var":
                                derived.append((s_.dst.aux, bs[0].aux))
                    elif s_.k == "call":
                        exprs.append(s_.src)
                elif k == "if":
                    exprs.append(n[1])
                    collect(n[2])
                    collect(n[3])
                elif k == "while":
                    if n[1] is not None:
                        exprs.append(n[1])
                    collect(n[2])
                elif k == "for":
                    collect([("s", n[1])])
                    exprs.append(n[2])
                    collect([("s", n[3])])
                    collect(n[4])
                elif k == "dowhile":
                    collect(n[1])
                    exprs.append(n[2])
                elif k == "switch":
                    exprs.append(n[1])
                    for _, b in n[2]:
                        collect(b)
                elif k == "ret" and n[1] is not None:
                    exprs.append(n[1])

        collect(self.ast)
        induction = set()
        for e0 in self._all_stmts():
            if e0.k == "asg" and e0.dst.op == "var" and e0.src.op in ("add", "sub") and e0.src.args[0] == e0.dst \
                    and e0.src.args[1].op == "c":
                induction.add(e0.dst.aux)
        params_ = {w for w, _ in self.params if w is not None}
        self.ptypes = ptrtype.infer(exprs, fd.full, self.webs, induction, params_)
        ptrtype.propagate_copies(copies, self.ptypes, self.webs)
        for dst, src in derived:                                  # v = p + 오프셋 → v 도 p 와 같은 종류의 포인터
            if src in self.ptypes and dst not in self.ptypes and self.webs[dst].sz == fd.full:
                self.ptypes[dst] = self.ptypes[src]
        ptrtype.propagate_copies(copies, self.ptypes, self.webs)
        for w, pt in self.ptypes.items():
            pt.signed = "s" if pt.esz >= 4 else "u"
        # for 문의 반복 변수는 i, j, k.. 로 부른다(사용자가 바꾸지 않은 변수만)
        self.loopvars = []

        def find_for(ns):
            for n in ns:
                k = n[0]
                if k == "for":
                    if n[1].dst.aux not in self.loopvars:
                        self.loopvars.append(n[1].dst.aux)
                    find_for(n[4])
                elif k == "if":
                    find_for(n[2])
                    find_for(n[3])
                elif k == "while":
                    find_for(n[2])
                elif k == "dowhile":
                    find_for(n[1])
                elif k == "switch":
                    for _, b in n[2]:
                        find_for(b)

        find_for(self.ast)
        # 이름: 매개변수 → arg1.., 슬롯 → var_xx, 레지스터 → v1..
        counter = 0
        pidx = {w: i for w, i in self.params if w is not None}
        for w in order:
            web = self.webs[w]
            if w in pidx:
                slot = f"arg{pidx[w] + 1}"
                default = slot
            elif web.loc.startswith("slot:"):
                off = int(web.loc[5:])
                base = fd.frame if fd.frame is not None else 0
                rel = off - base
                slot = (f"var_{-rel:x}" if rel < 0 else f"arg_{rel:x}")
                default = slot
            else:
                counter += 1
                slot = f"v_{web.loc}_{self._first_def_addr(w):x}"
                default = f"v{counter}"
            if w in self.loopvars and w not in pidx:
                letters = "ijklmn"
                k_ = self.loopvars.index(w)
                default = letters[k_] if k_ < len(letters) else f"i{k_}"
            base_slot = slot
            k = 2
            while slot in self.slot_ids.values():
                slot = f"{base_slot}_{k}"
                k += 1
            self.slot_ids[w] = slot
            name = fd.varmap.get(slot, default)
            self.names[w] = self._unique_name(name)
        fd.stack_names.param_by_off = {}
        for i, (w, idx) in enumerate(self.params):
            if w is None:
                nm = fd.varmap.get(f"arg{idx + 1}", f"arg{idx + 1}")
                self.names[("p", idx)] = self._unique_name(nm)
                self.slot_ids[("p", idx)] = f"arg{idx + 1}"
                if idx in fd.mem_params:
                    off, sz = fd.mem_params[idx]
                    fd.stack_names.param_by_off[off] = (sz, self.names[("p", idx)])

    def _first_def_addr(self, w) -> int:
        return self.fd.first_def_addr.get(w, 0)

    def _unique_name(self, name: str) -> str:
        base = name
        k = 2
        while name in self.used_names:
            name = f"{base}_{k}"
            k += 1
        self.used_names.add(name)
        return name

    # ================= 식 =================================================================
    def lit(self, v: int, sz: int, sign) -> str:
        n = to_signed(v, sz) if sign == "s" else v & mask(sz)
        if abs(n) < 4096:
            return str(n)
        return ("-" if n < 0 else "") + hex(abs(n))

    def sym_const(self, v: int, sz: int):
        """상수가 가리키는 문자열·심볼 이름(주소로 쓰였을 때)."""
        if sz != self.fd.full or v < 0x1000:
            return None
        s = self.fd.const_string(v)
        if s is not None and len(s) <= 200:
            return '"' + c_escape(s) + '"'
        nm = self.prog.name_for(v)
        if nm and re.match(r"^[A-Za-z_][A-Za-z0-9_@.$]*$", nm):
            return nm.replace("@", "_").replace(".", "_").replace("$", "_")
        return None

    def vname(self, w) -> str:
        return self.names[w]

    def pointee(self, pt) -> str:
        return itype(pt.esz, pt.signed) if pt.kind == "arr" else "uint8_t"

    def decl_type(self, w) -> str:
        if w in self.ptypes:
            return self.pointee(self.ptypes[w]) + " *"
        return itype(self.webs[w].sz, self.vsign[w]) + " "

    def fit(self, e: X, want, prec=0) -> str:
        """요청 부호로 맞춘 문자열(필요하면 괄호·캐스트)."""
        text, sign, p = self.P(e, want)
        if want in ("s", "u") and sign and sign != want and e.sz in (1, 2, 4, 8):
            text = f"({itype(e.sz, want)}){self._paren(text, p, PREC['unary'])}"
            p = PREC["unary"]
        return self._paren(text, p, prec)

    @staticmethod
    def _paren(text, p, need):
        return f"({text})" if p < need else text

    def narrow_cast(self, e, text, sign):
        """폭 1·2 바이트 연산의 결과를 C 의 int 승격 뒤 다시 좁은 형으로 되돌린다."""
        return f"({itype(e.sz, sign or 'u')})({text})"

    def P(self, e: X, want=None):
        op = e.op
        sz = e.sz
        if op == "c":
            sym = self.sym_const(e.aux, sz) if (want is None and sz >= 4) else None
            if sym is not None:
                return sym, None, PREC["prim"]
            sign = want or ("s" if to_signed(e.aux, sz) < 0 and abs(to_signed(e.aux, sz)) < 4096 else None)
            return self.lit(e.aux, sz, sign), None, (PREC["unary"] if (sign == "s" and to_signed(e.aux, sz) < 0) else PREC["prim"])
        if op == "var":
            if e.aux in self.ptypes and want != "ptr":
                return f"(uint64_t){self.vname(e.aux)}", "u", PREC["unary"]
            return self.vname(e.aux), self.vsign[e.aux], PREC["prim"]
        if op == "stk":
            return self.stk_addr(e), "u", PREC["add"] if "+" in self.stk_addr(e) else PREC["unary"]
        if op == "mem":
            return self.load(e, want)
        if op in ("add", "sub", "mul", "and", "or", "xor", "shl", "shr", "sar", "sdiv", "udiv", "srem", "urem"):
            return self.binary(e, want)
        if op in ("neg", "not"):
            t, s, p = self.P(e.args[0], want)
            inner = self._paren(t, p, PREC["unary"])
            sym = "-" if op == "neg" else "~"
            text = f"{sym}{inner}"
            if sz < 4:
                return self.narrow_cast(e, text, want or s), want or s, PREC["unary"]
            return text, s, PREC["unary"]
        if op in ir.CMP_OPS:
            sg = {"slt": "s", "sle": "s", "sgt": "s", "sge": "s", "ult": "u", "ule": "u", "ugt": "u", "uge": "u"}.get(op)
            sym = {"eq": "==", "ne": "!=", "slt": "<", "ult": "<", "sle": "<=", "ule": "<=", "sgt": ">", "ugt": ">",
                   "sge": ">=", "uge": ">="}[op]
            a, b = e.args
            if op in ("eq", "ne"):
                # 포인터 변수끼리·0 과의 비교는 포인터 그대로
                if a.op == "var" and a.aux in self.ptypes and ((b.op == "c" and b.aux == 0) or (b.op == "var" and b.aux in self.ptypes)):
                    ta = self.vname(a.aux)
                    if b.op == "c":
                        return (f"!{ta}" if op == "eq" else ta), None, PREC["unary"]
                    return f"{ta} {sym} {self.vname(b.aux)}", None, PREC["eq"]
                # 비교 대상이 0 이면 `!x`·`x` 로 줄인다
                if b.op == "c" and b.aux == 0 and a.sz >= 1:
                    t, s, p = self.P(a)
                    if op == "eq":
                        return "!" + self._paren(t, p, PREC["unary"]), None, PREC["unary"]
                    if a.op in ir.BOOL_OPS or (a.op == "var" and self.webs[a.aux].sz == 1 and False):
                        return t, None, p
                ta, sa, pa = self.P(a)
                tb, sb, pb = self.P(b, sa)
                return f"{self._paren(ta, pa, PREC['rel'] + 1)} {sym} {self._paren(tb, pb, PREC['rel'] + 1)}", None, PREC["eq"]
            ta = self.fit(a, sg, PREC["rel"] + 1)
            tb = self.fit(b, sg, PREC["rel"] + 1)
            return f"{ta} {sym} {tb}", None, PREC["rel"]
        if op == "land":
            return f"{self.cond(e.args[0], PREC['land'])} && {self.cond(e.args[1], PREC['land'] + 1)}", None, PREC["land"]
        if op == "lor":
            return f"{self.cond(e.args[0], PREC['lor'])} || {self.cond(e.args[1], PREC['lor'] + 1)}", None, PREC["lor"]
        if op == "lnot":
            return "!" + self.cond(e.args[0], PREC["unary"]), None, PREC["unary"]
        if op == "zext":
            inner = e.args[0]
            if inner.op in ir.BOOL_OPS:
                return self.P(inner)
            t = self.fit(inner, "u", PREC["unary"])
            return f"({itype(sz, 'u')}){t}", "u", PREC["unary"]
        if op == "sext":
            inner = e.args[0]
            t = self.fit(inner, "s", PREC["unary"])
            return f"({itype(sz, 's')}){t}", "s", PREC["unary"]
        if op == "trunc":
            inner = e.args[0]
            if inner.op == "call":                       # 반환형이 이미 이 폭이면 캐스트가 필요 없다
                pr = (inner.aux or {}).get("proto")
                if pr is not None and sz == 4 and protos.ret_width(pr[0]) == 4:
                    sg = "u" if pr[0].startswith(("unsigned", "uint")) or pr[0] in ("DWORD", "UINT", "ULONG") else "s"
                    t = self.call_text(inner)
                    if want and want != sg:
                        return f"({itype(sz, want)}){t}", want, PREC["unary"]
                    return t, sg, PREC["prim"]
            t, s, p = self.P(inner)
            sign = want or s or "u"
            return f"({itype(sz, sign)}){self._paren(t, p, PREC['unary'])}", sign, PREC["unary"]
        if op == "ite":
            c, a, b = e.args
            ta, sa, pa = self.P(a, want)
            tb, sb, pb = self.P(b, want or sa)
            return f"{self.cond(c, PREC['cond'] + 1)} ? {self._paren(ta, pa, PREC['cond'] + 1)} : {self._paren(tb, pb, PREC['cond'])}", sa, PREC["cond"]
        if op == "ins":
            old, val = e.args
            part = e.aux
            m = mask(part)
            to, so, po = self.P(old, "u")
            tv, sv, pv = self.P(val, "u")
            return (f"({self._paren(to, po, PREC['band'] + 1)} & {hex(mask(sz) ^ m)}) | (({itype(sz, 'u')}){self._paren(tv, pv, PREC['unary'])})",
                    "u", PREC["bor"])
        if op == "mulhs" or op == "mulhu":
            s = "s" if op == "mulhs" else "u"
            ta, tb = self.fit(e.args[0], s), self.fit(e.args[1], s)
            return f"__{op}{8 * sz}({ta}, {tb})", s, PREC["prim"]
        if op == "bi":
            t, sg, p_ = self.P(e.args[0], "u")
            if e.aux == "rev":
                return f"__builtin_bswap{8 * sz}({t})", "u", PREC["prim"]
            return f"__{e.aux}{8 * sz}({t})", "u", PREC["prim"]
        if op == "call":
            if self._ptr_ret(e):
                return f"(uint64_t){self.call_text(e)}", "u", PREC["unary"]
            return self.call_text(e), None, PREC["prim"]
        if op == "canary":
            return "__stack_chk_guard", "u", PREC["prim"]
        if op == "seg":
            return f"__{e.aux}base", "u", PREC["prim"]
        if op == "cc":
            self.incomplete.append(f"조건 코드 {e.aux}")
            return f"/* cond:{e.aux} */ 0", None, PREC["prim"]
        if op == "fl":
            self.incomplete.append("플래그")
            return "/* flags */ 0", None, PREC["prim"]
        if op == "opaque":
            return "/* ? */ 0", None, PREC["prim"]
        return f"/* {op} */ 0", None, PREC["prim"]

    def cond(self, e: X, prec) -> str:
        """조건으로 쓰이는 식: 불리언이 아니면 `x != 0` 대신 `x` 로."""
        t, s, p = self.P(e)
        return self._paren(t, p, prec)

    def binary(self, e: X, want):
        op, sz = e.op, e.sz
        a, b = e.args
        sym = {"add": "+", "sub": "-", "mul": "*", "and": "&", "or": "|", "xor": "^", "shl": "<<", "shr": ">>", "sar": ">>",
               "sdiv": "/", "udiv": "/", "srem": "%", "urem": "%"}[op]
        prec = {"add": "add", "sub": "add", "mul": "mul", "and": "band", "or": "bor", "xor": "bxor", "shl": "shift",
                "shr": "shift", "sar": "shift", "sdiv": "mul", "udiv": "mul", "srem": "mul", "urem": "mul"}[op]
        pr = PREC[prec]
        if op == "and" and a.op == "trunc" and b.op == "c" and b.aux <= mask(a.sz):        # (uint8_t)x & 1 → x & 1 (값이 같다)
            ta, sa, pa = self.P(a.args[0], want)
            return f"{self._paren(ta, pa, pr)} & {self.lit(b.aux, a.args[0].sz, None)}", sa, pr
        need = {"sdiv": "s", "srem": "s", "sar": "s", "udiv": "u", "urem": "u", "shr": "u"}.get(op)
        if op == "add" and b.op == "c" and to_signed(b.aux, sz) < 0 and abs(to_signed(b.aux, sz)) < (1 << 20):
            sym = "-"
            b = ir.C(-to_signed(b.aux, sz), sz)
        if op in ("shl", "shr", "sar"):
            ta = self.fit(a, need or want, pr if op != "shl" else pr)
            tb, sb, pb = self.P(b)
            tb = self._paren(tb, pb, pr + 1)
            text = f"{ta} {sym} {tb}"
            sign = need or (self.P(a)[1])
        else:
            w = need or want
            ta, sa, pa = self.P(a, w)
            tb, sb, pb = self.P(b, w or sa)
            if need and sa and sa != need:
                ta = self.fit(a, need, pr)
                sa = need
            else:
                ta = self._paren(ta, pa, pr)
            if need and sb and sb != need:
                tb = self.fit(b, need, pr + 1)
            else:
                tb = self._paren(tb, pb, pr + 1)
            text = f"{ta} {sym} {tb}"
            sign = need or (sa or sb)
            if sa == "u" or sb == "u":
                sign = need or "u"
        if sz < 4:
            return self.narrow_cast(e, text, want or sign), want or sign, PREC["unary"]
        if want and sign and sign != want:
            return f"({itype(sz, want)})({text})", want, PREC["unary"]
        return text, sign, pr

    def load(self, e: X, want):
        sz = e.sz
        addr = e.args[0]
        sign = want or ("s" if sz >= 4 else "u")
        if addr.op == "stk":
            return self.mem_slot(addr.aux, sz, sign)
        tb = self.table_load(e, sign)
        if tb is not None:
            return tb
        pd = self.ptr_deref(addr, sz, sign)
        if pd is not None:
            return pd
        t = self.addr_text(addr)
        return f"*({itype(sz, sign)} *){t}", sign, PREC["unary"]

    def table_load(self, e: X, sign):
        tabs = getattr(self.fd, "tables", None)
        if not tabs:
            return None
        m = tables.match(e.args[0], e.sz, self.fd.full)
        if m is None or m[0] not in tabs:
            return None
        base, idx = m
        sg = self.tbl_sign.setdefault(base, sign)
        return f"tbl_{base:x}[{self.index_text(idx)}]", sg, PREC["prim"]

    def table_decls(self):
        fd = self.fd
        out = []
        for base in sorted(self.tbl_sign):
            info = fd.tables[base]
            sg = self.tbl_sign[base]
            vals = tables.values(fd, base, info["esz"], info["n"], sg == "s")
            if vals is None:
                continue
            head = f"static const {itype(info['esz'], sg)} tbl_{base:x}[{info['n']}] = {{"
            if len(vals) <= 8:
                out.append((1, head + " " + ", ".join(map(str, vals)) + " };"))
            else:
                out.append((1, head))
                for i in range(0, len(vals), 8):
                    out.append((2, ", ".join(map(str, vals[i:i + 8])) + ("," if i + 8 < len(vals) else "")))
                out.append((1, "};"))
        return out

    def addr_text(self, a: X) -> str:
        t, s, p = self.P(a)
        return self._paren(t, p, PREC["unary"])

    # ---- 포인터 접근 ---------------------------------------------------------------------
    def index_text(self, ix: X) -> str:
        if ix.op == "add" and ix.args[1].op == "c" and ix.args[0].op in ("sext", "zext") and ix.args[0].args[0].op == "var":
            inner = ix.args[0].args[0]
            c = to_signed(ix.args[1].aux, ix.sz)
            if abs(c) < 1 << 20 and self.vsign.get(inner.aux) == ("s" if ix.args[0].op == "sext" else "u") and inner.aux not in self.ptypes:
                return f"{self.vname(inner.aux)} + {c}" if c > 0 else f"{self.vname(inner.aux)} - {-c}"
        if ix.op == "var":
            return self.vname(ix.aux)
        if ix.op == "sext" and ix.args[0].op == "var" and self.vsign.get(ix.args[0].aux) == "s" and ix.args[0].aux not in self.ptypes:
            return self.vname(ix.args[0].aux)
        if ix.op == "zext" and ix.args[0].op == "var" and self.vsign.get(ix.args[0].aux) == "u" and ix.args[0].aux not in self.ptypes:
            return self.vname(ix.args[0].aux)
        t, s, p = self.P(ix, "s")
        return t if p >= PREC["add"] else f"({t})" if False else t

    def _split(self, addr: X):
        bases, idxs, off, rest = ptrtype.split_addr(addr, self.fd.full)
        if rest or not bases:
            return None
        base, idxs = ptrtype.pick_base(bases, idxs, self.ptypes)
        if len(idxs) > 1:
            return None
        if base.op == "stk":
            return base, off, (idxs[0] if idxs else None)
        if base.aux not in self.ptypes:
            return None
        return base, off, (idxs[0] if idxs else None)

    def ptr_deref(self, addr: X, sz: int, sign):
        sp = self._split(addr)
        if sp is None:
            return None
        base, off, idx = sp
        if base.op == "stk":
            iv = self.fd.stack_names.find(base.aux)
            if iv is None or iv[3] != "array" or iv[2] != sz or idx is None or idx[1] != sz or (base.aux - iv[0]) % sz:
                return None
            k = (base.aux - iv[0]) // sz
            it = self.index_text(idx[0])
            if k:
                it = f"{it} + {k}"
            return f"locals.{self.fd.stack_names._nm(iv[0])}[{it}]", "s" if sz >= 4 else "u", PREC["prim"]
        pt = self.ptypes[base.aux]
        nm = self.vname(base.aux)
        T = itype(sz, sign)
        if pt.kind == "arr":
            esz = pt.esz
            if sz == esz and off % esz == 0 and (idx is None or idx[1] == esz):
                psign = pt.signed
                if idx is None:
                    k = off // esz
                    return (f"{nm}[{k}]" if k else f"*{nm}"), psign, (PREC["prim"] if k else PREC["unary"])
                it = self.index_text(idx[0])
                if off:
                    it = f"{it} + {off // esz}" if off > 0 else f"{it} - {-off // esz}"
                return f"{nm}[{it}]", psign, PREC["prim"]
            parts = f"(uint8_t *){nm}" + (f" + {off}" if off > 0 else f" - {-off}" if off else "")
            if idx is not None:
                parts += f" + {self._paren(self.P(idx[0])[0], self.P(idx[0])[2], PREC['mul'] + 1)}" + (f" * {idx[1]}" if idx[1] != 1 else "")
            return f"*({T} *)({parts})", sign, PREC["unary"]
        parts = nm + (f" + {off}" if off > 0 else f" - {-off}" if off else "")
        if idx is not None:
            t, s_, p_ = self.P(idx[0])
            parts += f" + {self._paren(t, p_, PREC['mul'] + 1)}" + (f" * {idx[1]}" if idx[1] != 1 else "")
        return f"*({T} *)({parts})", sign, PREC["unary"]

    def ptr_text(self, e: X, dst_pt=None):
        """포인터 값으로 쓰이는 식(대입 대상이 포인터 변수일 때 등)의 문자열과 우선순위."""
        if e.op == "var" and e.aux in self.ptypes:
            return self.vname(e.aux), PREC["prim"], self.ptypes[e.aux]
        if e.op == "c" and e.aux == 0:
            return "0", PREC["prim"], None
        if e.op == "stk":
            return self.fd.stack_names.addr_pretty(e.aux), PREC["unary"], None
        sp = self._split(e)
        if sp is not None and sp[0].op != "stk":
            base, off, idx = sp
            pt = self.ptypes[base.aux]
            nm = self.vname(base.aux)
            if pt.kind == "arr" and off % pt.esz == 0 and (idx is None or idx[1] == pt.esz):
                text = nm
                if idx is not None:
                    text += f" + {self.index_text(idx[0])}"
                if off:
                    text += f" + {off // pt.esz}" if off > 0 else f" - {-off // pt.esz}"
                return text, PREC["add"], pt
            text = (f"(uint8_t *){nm}" if pt.kind == "arr" else nm) + (f" + {off}" if off > 0 else f" - {-off}" if off else "")
            if idx is not None:
                t, s_, p_ = self.P(idx[0])
                text += f" + {self._paren(t, p_, PREC['mul'] + 1)}" + (f" * {idx[1]}" if idx[1] != 1 else "")
            return text, PREC["add"], None
        return None

    def call_text(self, e: X) -> str:
        info = e.aux or {}
        tgt = e.args[0]
        name = info.get("name")
        if tgt.op == "c":
            nm = name or f"sub_{tgt.aux:x}"
            nm = re.sub(r"[^A-Za-z0-9_]", "_", nm)
            callee = nm
        else:
            t, s, p = self.P(tgt, "u")
            callee = f"((uint64_t (*)()){t})" if True else t
        args = []
        pr = info.get("proto")
        ptypes_ = pr[1] if pr is not None else []
        for i, a in enumerate(e.args[1:]):
            if a.op == "zext" and a.args[0].sz >= 4:
                a = a.args[0]
            want_ptr = i < len(ptypes_) and "*" in ptypes_[i]
            pt = self.ptr_text(a) if a.op in ("var", "stk", "add") else None
            if pt is not None and (a.op != "add" or pt[2] is None or True) and not (a.op == "var" and a.aux not in self.ptypes):
                args.append(self._paren(pt[0], pt[1], PREC["cond"]))
                continue
            t, s, p = self.P(a)
            if want_ptr and not t.startswith('"') and t != "0":              # 원형이 포인터를 받는데 정수 식이면 형을 맞춘다
                t, p = f"(void *){self._paren(t, p, PREC['unary'])}", PREC["unary"]
            args.append(self._paren(t, p, PREC["cond"]))
        return f"{callee}({', '.join(args)})"

    @staticmethod
    def _ptr_ret(e: X) -> bool:
        pr = (e.aux or {}).get("proto")
        return pr is not None and "*" in pr[0]

    # ---- 스택 주소·메모리 변수 ----------------------------------------------------------
    def stk_addr(self, e: X) -> str:
        return self.fd.stack_names.addr(e.aux)

    def mem_slot(self, off: int, sz: int, sign):
        return self.fd.stack_names.load(off, sz, sign)

    # ================= 문장 ================================================================
    def emit(self, indent, text, kind="stmt", addr=None):
        self.lines.append({"addr": addr if addr is not None else self.cur_addr, "indent": indent, "text": text, "kind": kind})

    def assign_text(self, dst: X, src: X) -> str:
        if dst.op == "var":
            w = dst.aux
            if w in self.ptypes:
                pt = self.ptypes[w]
                if pt.kind == "arr" and src.op == "add" and src.args[0] == dst and src.args[1].op == "c":     # p = p + n → p++ / p += n
                    c = to_signed(src.args[1].aux, src.sz)
                    if c != 0 and c % pt.esz == 0:
                        k = c // pt.esz
                        nm = self.vname(w)
                        return f"{nm}++;" if k == 1 else f"{nm}--;" if k == -1 else f"{nm} += {k};" if k > 0 else f"{nm} -= {-k};"
                r = self.ptr_text(src)
                T = f"{self.pointee(pt)} *"
                if r is not None and (r[2] is pt or (r[2] is not None and r[2].kind == pt.kind and r[2].esz == pt.esz) or src.op == "c"):
                    return f"{self.vname(w)} = {r[0]};"
                if r is not None:
                    return f"{self.vname(w)} = ({T}){self._paren(r[0], r[1], PREC['unary'])};"
                t, s_, p_ = self.P(src)
                return f"{self.vname(w)} = ({T}){self._paren(t, p_, PREC['unary'])};"
            nm = self.vname(w)
            if src.op in ("add", "sub", "mul", "and", "or", "xor", "shl") and src.args[0] == dst and src.sz >= 4:
                rhs = src.args[1]
                if rhs.op == "c" and src.op in ("add", "sub") and abs(to_signed(rhs.aux, src.sz)) == 1 and src.sz >= 4:
                    step = to_signed(rhs.aux, src.sz) * (1 if src.op == "add" else -1)
                    return f"{nm}++;" if step == 1 else f"{nm}--;" if step == -1 else f"{nm} += {step};"
                sym = {"add": "+=", "sub": "-=", "mul": "*=", "and": "&=", "or": "|=", "xor": "^=", "shl": "<<="}[src.op]
                if src.op == "add" and rhs.op == "c" and to_signed(rhs.aux, src.sz) < 0:
                    sym, rhs = "-=", ir.C(-to_signed(rhs.aux, src.sz), src.sz)
                return f"{nm} {sym} {self.fit(rhs, self.vsign[w])};"
            text = self.fit(src, self.vsign[w])
            return f"{nm} = {text};"
        # 메모리 저장
        addr = dst.args[0]
        sz = dst.sz
        sign = "s" if sz >= 4 else "u"
        val = self.fit(src, sign)
        if addr.op == "stk":
            t, s, p = self.mem_slot(addr.aux, sz, sign)
            return f"{t} = {val};"
        pd = self.ptr_deref(addr, sz, sign)
        if pd is not None:
            psign = pd[1] or sign
            if psign != sign:
                val = self.fit(src, psign)
            return f"{pd[0]} = {val};"
        return f"*({itype(sz, sign)} *){self.addr_text(addr)} = {val};"

    def stmt_text(self, s) -> str:
        if s.k == "asg":
            return self.assign_text(s.dst, s.src)
        if s.k == "call":
            call = self.call_text(s.src)
            if s.dst is not None:
                w = s.dst.aux
                if self._ptr_ret(s.src) and w not in self.ptypes:
                    return f"{self.vname(w)} = (uint64_t){call};"
                return f"{self.vname(w)} = {call};"
            return f"{call};"
        if s.k == "unk":
            self.incomplete.append(s.extra["text"])
            return f"/* {s.extra['text']} */"
        return f"/* {s.k} */"

    def block(self, nodes, indent):
        i = 0
        while i < len(nodes):
            n = nodes[i]
            k = n[0]
            if k == "label":
                self.emit(max(indent - 1, 0), f"L_{n[1]:x}:", "label", n[1])
            elif k == "s":
                s = n[1]
                self.cur_addr = s.addr
                self.emit(indent, self.stmt_text(s), "stmt", s.addr)
            elif k == "if":
                self.emit_if(n, indent, "if")
            elif k == "while":
                cond = n[1]
                head = "while (1) {" if cond is None else f"while ({self.cond(cond, 0)}) {{"
                self.emit(indent, head, "ctrl", n[3])
                self.block(n[2], indent + 1)
                self.emit(indent, "}", "ctrl", n[3])
            elif k == "for":
                init = self.stmt_text(n[1]).rstrip(";")
                inc = self.stmt_text(n[3]).rstrip(";")
                self.emit(indent, f"for ({init}; {self.cond(n[2], 0)}; {inc}) {{", "ctrl", n[5])
                self.block(n[4], indent + 1)
                self.emit(indent, "}", "ctrl", n[5])
            elif k == "dowhile":
                self.emit(indent, "do {", "ctrl", n[3])
                self.block(n[1], indent + 1)
                self.emit(indent, f"}} while ({self.cond(n[2], 0)});", "ctrl", n[3])
            elif k == "break":
                self.emit(indent, "break;", "ctrl")
            elif k == "continue":
                self.emit(indent, "continue;", "ctrl")
            elif k == "goto":
                self.emit(indent, f"goto L_{n[1]:x};", "goto", n[1])
            elif k == "ret":
                if n[1] is None or not self.fd.returns_value:
                    self.emit(indent, "return;" if not self.fd.returns_value else "return 0;", "stmt")
                else:
                    self.emit(indent, f"return {self.ret_value(n[1])};", "stmt")
            elif k == "noret":
                pass
            elif k == "ijump":
                t, sg, p = self.P(n[1], "u")
                self.incomplete.append("간접 점프")
                self.emit(indent, f"goto *(void *){self._paren(t, p, PREC['unary'])};  /* 해석하지 못한 간접 점프 */", "goto", n[2])
            elif k == "fallthrough":
                self.emit(indent, "/* fallthrough */", "comment")
            elif k == "comment":
                self.emit(indent, f"/* {n[1]} */", "comment")
            elif k == "switch":
                self.emit_switch(n, indent)
            i += 1

    def ret_value(self, e: X) -> str:
        sz, sign = self.ret_sz, self.ret_sign
        if e.sz != sz:
            e = ir.trunc(e, sz) if e.sz > sz else ir.zext(e, sz)
        return self.fit(e, sign)

    def emit_if(self, n, indent, word):
        cond, th, el = n[1], n[2], n[3]
        self.emit(indent, f"{word} ({self.cond(cond, 0)}) {{", "ctrl", n[4])
        self.block(th, indent + 1)
        while el and len(el) == 1 and el[0][0] == "if":
            n2 = el[0]
            self.emit(indent, f"}} else if ({self.cond(n2[1], 0)}) {{", "ctrl", n2[4])
            self.block(n2[2], indent + 1)
            el = n2[3]
        if el:
            self.emit(indent, "} else {", "ctrl", n[4])
            self.block(el, indent + 1)
        self.emit(indent, "}", "ctrl", n[4])

    def emit_switch(self, n, indent):
        expr, cases, addr = n[1], n[2], n[4]
        self.emit(indent, f"switch ({self.fit(expr, 's')}) {{", "ctrl", addr)
        for vals, body in cases:
            for v in vals:
                self.emit(indent, "default:" if v == "default" else f"case {v}:", "ctrl", addr)
            self.block(body, indent + 1)
        self.emit(indent, "}", "ctrl", addr)

    # ================= 전체 ================================================================
    def build(self):
        self.prepare()
        fd = self.fd
        name = fd.func_name
        ret_t = itype(self.ret_sz, self.ret_sign) if fd.returns_value else "void"
        ptxt = []
        for w, idx in self.params:
            if w is None:
                psz = fd.mem_params[idx][1] if idx in fd.mem_params else fd.full
                ptxt.append(f"{itype(psz if psz in (1, 2, 4, 8) else fd.full, 's')} {self.names[('p', idx)]}")
            else:
                ptxt.append(f"{self.decl_type(w)}{self.names[w]}")
        sig = f"{ret_t} {name}({', '.join(ptxt) if ptxt else 'void'})"
        self.emit(0, sig, "sig", fd.addr)
        self.emit(0, "{", "brace", fd.addr)
        pset = {w for w, _ in self.params if w is not None}
        decls = []
        for w, nm in self.names.items():
            if isinstance(w, tuple) or w in pset:
                continue
            decls.append((w, f"{self.decl_type(w)}{nm};"))
        for text in fd.stack_names.declarations():
            self.emit(1, text, "decl", fd.addr)
        for w, text in decls:
            self.emit(1, text, "decl", fd.addr)
        if decls or fd.stack_names.has_decls():
            pass
        pos = len(self.lines)
        self.block(self.ast, 1)
        if self.tbl_sign:
            ins = [{"addr": fd.addr, "indent": ind, "text": txt, "kind": "decl"} for ind, txt in self.table_decls()]
            self.lines[pos:pos] = ins
        self.emit(0, "}", "brace", fd.addr)
        return self.lines
