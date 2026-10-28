"""SIMD(NEON) 벡터 식의 C 출력.

IR 은 128비트 벡터 레지스터를 64비트 두 조각으로 들고(ir.py 의 '벡터' 절 참고) 한 조각을 폭 esz 원소 8/esz 개의 벡터 값으로 본다.
출력 단계에서 '어느 변수·메모리가 벡터인지'(vkind·vmem)를 벡터 연산이 쓰이는 방식으로 추정해 `int32x2_t` 같은 ACLE 형으로 선언하고,
원소별 연산은 C 연산자(`a + b`)나 ACLE 내장 함수(`vmax_s32(a, b)`)로 쓴다. 정수 식과 섞이는 곳은 `__builtin_bit_cast` 로 비트를 옮긴다.
"""
from __future__ import annotations

from . import ir
from .ir import X, mask

PREC = {"prim": 16, "unary": 14, "mul": 13, "add": 12, "shift": 11, "rel": 10, "eq": 9, "band": 8, "bxor": 7,
        "bor": 6, "land": 5, "lor": 4, "cond": 3}

TN = {(1, "s"): "int8x8_t", (1, "u"): "uint8x8_t", (2, "s"): "int16x4_t", (2, "u"): "uint16x4_t", (4, "s"): "int32x2_t",
      (4, "u"): "uint32x2_t", (4, "f"): "float32x2_t", (8, "s"): "int64x1_t", (8, "u"): "uint64x1_t"}
SF = {(1, "s"): "s8", (1, "u"): "u8", (2, "s"): "s16", (2, "u"): "u16", (4, "s"): "s32", (4, "u"): "u32", (4, "f"): "f32",
      (8, "s"): "s64", (8, "u"): "u64"}
SC = {(1, "s"): "int8_t", (1, "u"): "uint8_t", (2, "s"): "int16_t", (2, "u"): "uint16_t", (4, "s"): "int32_t", (4, "u"): "uint32_t",
      (4, "f"): "float", (8, "s"): "int64_t", (8, "u"): "uint64_t"}

# 원소별 연산 이름 → 부호와 무관한 연산('?')·부호에 따라 달라지는 연산·비교·변환
AGNOSTIC = {"add", "sub", "mul", "neg", "mla", "mls", "shl_n", "clz", "cnt", "rbit", "padd", "not", "rev"}
FLOATOPS = {"fadd", "fsub", "fmul", "fdiv", "fneg", "fabs", "fsqrt", "fmax", "fmin", "fmaxnm", "fminnm", "fabd", "fmla", "fmls",
            "fpadd", "fpmax", "fpmin", "frintn", "frintm", "frintp", "frintz", "frinta", "frintx", "frecpe", "frsqrte"}
COMPARES = {"ceq", "cgt", "cge", "tst"}
FCOMPARES = {"fceq", "fcgt", "fcge", "facgt", "facge"}
CONVS = {"scvtf": ((4, "s"), (4, "f")), "ucvtf": ((4, "u"), (4, "f")), "fcvtzs": ((4, "f"), (4, "s")), "fcvtzu": ((4, "f"), (4, "u"))}
_OPER = {"add": "{0} + {1}", "sub": "{0} - {1}", "mul": "{0} * {1}", "fadd": "{0} + {1}", "fsub": "{0} - {1}", "fmul": "{0} * {1}",
         "fdiv": "{0} / {1}", "neg": "-{0}", "fneg": "-{0}", "not": "~{0}", "mla": "{0} + {1} * {2}", "mls": "{0} - {1} * {2}",
         "shl_n": "{0} << {1}"}
_PREC_OPER = {"add": "add", "sub": "add", "mul": "mul", "fadd": "add", "fsub": "add", "fmul": "mul", "fdiv": "mul", "neg": "unary", "fneg": "unary",
              "not": "unary", "mla": "add", "mls": "add", "shl_n": "shift"}
_INTR = {"max": "vmax", "min": "vmin", "abd": "vabd", "qadd": "vqadd", "qsub": "vqsub", "hadd": "vhadd", "rhadd": "vrhadd", "abs": "vabs",
         "clz": "vclz", "cnt": "vcnt", "rbit": "vrbit", "padd": "vpadd", "pmax": "vpmax", "pmin": "vpmin", "ceq": "vceq", "cgt": "vcgt",
         "cge": "vcge", "tst": "vtst", "shl": "vshl", "fabs": "vabs", "fsqrt": "vsqrt", "fmax": "vmax", "fmin": "vmin", "fmaxnm": "vmaxnm",
         "fminnm": "vminnm", "fabd": "vabd", "fmla": "vfma", "fmls": "vfms", "fpadd": "vpadd", "fpmax": "vpmax", "fpmin": "vpmin",
         "fceq": "vceq", "fcgt": "vcgt", "fcge": "vcge", "facgt": "vcagt", "facge": "vcage", "frintn": "vrndn", "frintm": "vrndm",
         "frintp": "vrndp", "frintz": "vrnd", "frinta": "vrnda", "frintx": "vrndx", "frecpe": "vrecpe", "frsqrte": "vrsqrte",
         "rev": "vrev", "shr_n": "vshr_n", "sra_n": "vsra_n"}
_SIGNED_ONLY = {"abs", "qabs"}


def _agn(vt):
    return vt


def vop_types(e: X):
    """벡터 연산 e 의 (결과 형, [인자 형들]). 형은 (esz, cls) 이고, 상황에 따라 정해지는 부호는 cls=None, 벡터가 아닌 인자(즉값 등)는 None."""
    op = e.op
    if op == "vop":
        name, esz, cls = e.aux
        if name in CONVS:
            a, r = CONVS[name]
            return r, [a]
        if name in COMPARES:
            return (esz, "u"), [(esz, cls if name != "ceq" and name != "tst" else None)] * 2
        if name in FCOMPARES:
            return (esz, "u"), [(esz, "f")] * 2
        if name == "shl":
            return (esz, cls), [(esz, cls), (esz, "s")]
        if name in ("shl_n", "shr_n"):
            return (esz, cls if name == "shr_n" else None), [(esz, cls if name == "shr_n" else None), None]
        if name == "sra_n":
            return (esz, cls), [(esz, cls), (esz, cls), None]
        if name in FLOATOPS:
            n = 3 if name in ("fmla", "fmls") else (1 if name in ("fneg", "fabs", "fsqrt", "frintn", "frintm", "frintp", "frintz", "frinta", "frintx",
                                                                    "frecpe", "frsqrte") else 2)
            return (esz, "f"), [(esz, "f")] * n
        n = 3 if name in ("mla", "mls") else (1 if name in ("neg", "abs", "clz", "cnt", "rbit", "not", "rev") else 2)
        c = None if name in AGNOSTIC else cls
        return (esz, c), [(esz, c)] * n
    if op == "vshuf":
        idx, esz, cls = e.aux
        return (esz, cls), [(esz, cls), (esz, cls)]
    if op == "vdup":
        return (e.aux[0], e.aux[1]), [None]
    if op == "vpack":
        return (e.aux[0], e.aux[1]), [None] * len(e.args)
    if op == "vsetlane":
        return (e.aux[1], e.aux[2]), [(e.aux[1], e.aux[2]), None]
    return None, []


class VecRender:
    """Renderer 에 섞어 쓰는 벡터 출력."""

    # ---- 추정 ----------------------------------------------------------------------------
    def vec_prepare(self, roots, asgs):
        self.vkind = {}                                    # 웹 → (esz, cls)
        self.vmem = {}                                     # 8바이트 메모리 접근 → (esz, cls)
        self.has_vec = False                               # 이 함수에 벡터 연산이 있는가
        webs = self.webs
        used = False
        for r in roots:
            if any(n.op in ir.VEC_OPS or n.op in ir.VEC_LANE_OPS for n in ir.walk(r)):
                used = True
                break
        if not used:
            return
        self.has_vec = True

        def concrete(vt, default="s"):
            if vt is None:
                return None
            return (vt[0], vt[1] or default)

        def put(w, vt):
            if w in self.vkind or w in self.fkind or vt is None or vt[1] is None:
                return False
            if webs[w].sz != 8 or w in self.ptypes_guard():
                return False
            self.vkind[w] = vt
            return True

        def mark(e, vt):
            if vt is None:
                return False
            if e.op == "var":
                return put(e.aux, vt) if vt[1] is not None else False
            if e.op == "mem" and e.sz == 8 and vt[1] is not None and e not in self.vmem:
                self.vmem[e] = vt
                return True
            return False

        nodes = []
        for r in roots:
            for n in ir.walk(r):
                if n.op in ir.VEC_OPS or n.op in ("vlane", "vlanef", "vred", "vredf"):
                    nodes.append(n)
        for n in nodes:                                    # 연산이 요구하는 인자 형(부호가 정해진 것만)
            if n.op in ir.VEC_OPS:
                _res, args = vop_types(n)
                for a, t in zip(n.args, args):
                    mark(a, t)
            else:
                mark(n.args[0], (n.aux[1], n.aux[2] if n.aux[2] != "f" else "f") if n.op in ("vlane", "vlanef") else (n.aux[1], n.aux[2]))
        changed = True
        rounds = 0
        while changed and rounds < 8:
            changed = False
            rounds += 1
            for dst, src in asgs:
                t = None
                if src.op in ir.VEC_OPS:
                    t = vop_types(src)[0]
                    if t is not None and t[1] is None:                    # 부호 무관 연산: 첫 인자의 형을 따른다
                        a0 = src.args[0]
                        t = self.vkind.get(a0.aux) if a0.op == "var" else (self.vmem.get(a0) if a0.op == "mem" else None)
                        if t is None and dst.op == "var":
                            t = self.vkind.get(dst.aux)
                elif src.op == "var" and src.aux in self.vkind:
                    t = self.vkind[src.aux]
                elif src.op == "mem" and src in self.vmem:
                    t = self.vmem[src]
                if dst.op == "var":
                    if t is not None and t[1] is not None and dst.aux not in self.vkind:
                        changed |= put(dst.aux, t)
                    elif dst.aux in self.vkind:
                        kk = self.vkind[dst.aux]
                        if src.op == "var" and src.aux not in self.vkind:
                            changed |= put(src.aux, kk)
                        elif src.op == "mem" and src.sz == 8 and src not in self.vmem:
                            self.vmem[src] = kk
                            changed = True
                        elif src.op in ir.VEC_OPS and vop_types(src)[0] is not None and vop_types(src)[0][1] is None:
                            for a_ in src.args:                          # 부호 무관 연산의 인자도 도착지의 형으로
                                if a_.op in ("var", "mem"):
                                    changed |= mark(a_, (kk[0], kk[1]))
                elif dst.op == "mem" and dst.sz == 8:
                    if t is not None and t[1] is not None and dst not in self.vmem:
                        self.vmem[dst] = t
                        changed = True
                    elif dst in self.vmem and src.op == "var" and src.aux not in self.vkind:
                        changed |= put(src.aux, self.vmem[dst])
        for n in nodes:                                    # 끝까지 정해지지 않은 인자는 부호 있는 정수로
            if n.op in ir.VEC_OPS:
                res, args = vop_types(n)
                for a, t in zip(n.args, args):
                    if t is not None and t[1] is None:
                        mark(a, (t[0], "s"))
        for dst, src in asgs:
            if dst.op == "var" and dst.aux not in self.vkind and src.op in ir.VEC_OPS:
                res = vop_types(src)[0]
                if res is not None:
                    put(dst.aux, (res[0], res[1] or "s"))

    def ptrs_guard_dummy(self):
        return set()

    def ptypes_guard(self):
        return getattr(self, "ptypes", {}) if hasattr(self, "ptypes") else {}

    # ---- 출력 ----------------------------------------------------------------------------
    def vtype_name(self, vt) -> str:
        return TN[(vt[0], vt[1] or "s")]

    def is_vec_expr(self, e: X) -> bool:
        return e.op in ir.VEC_OPS

    def vec_decl(self, w):
        vt = self.vkind.get(w)
        return None if vt is None else TN[vt] + " "

    def _scal(self, x: X, esz: int, cls: str) -> str:
        """벡터 원소로 들어가는 스칼라 인자의 C 문자열."""
        if cls == "f":
            return self.PF(x, esz)[0]
        t = self.fit(x if x.sz == esz else (ir.trunc(x, esz) if x.sz > esz else ir.zext(x, esz)), cls or "s", PREC["cond"] + 1)
        return t

    def PV(self, e: X, vt):
        """식 e 를 벡터 형 vt=(esz, cls) 의 값으로 쓴 문자열과 우선순위."""
        vt = (vt[0], vt[1] or "s")
        ty = TN[vt]
        op = e.op
        if op in ir.VEC_OPS:
            res, _ = vop_types(e)
            rcls = res[1]
            text, p = self._vec_text(e, vt if rcls is None else (res[0], rcls))
            if rcls is not None and (res[0], rcls) != vt:
                return f"__builtin_bit_cast({ty}, {text})", PREC["prim"]
            return text, p
        if op == "var":
            if self.vkind.get(e.aux) == vt:
                return self.vname(e.aux), PREC["prim"]
            if e.aux in self.ptypes:
                return f"__builtin_bit_cast({ty}, (uint64_t){self.vname(e.aux)})", PREC["prim"]
            return f"__builtin_bit_cast({ty}, {self.vname(e.aux)})", PREC["prim"]
        if op == "c":
            lanes = [(e.aux >> (8 * vt[0] * i)) & mask(vt[0]) for i in range(8 // vt[0])]
            if vt[1] == "f":
                from .render_fp import fp_literal
                lit = [fp_literal(x, 4) for x in lanes]
            else:
                lit = [str(ir.to_signed(x, vt[0]) if vt[1] == "s" else x) + ("" if vt[0] < 8 else ("LL" if vt[1] == "s" else "ULL")) for x in lanes]
            if len(set(lit)) == 1 and len(lit) > 1:
                return f"vdup_n_{SF[vt]}({lit[0]})", PREC["prim"]
            return f"(({ty}){{{', '.join(lit)}}})", PREC["prim"]
        if op == "mem" and e.sz == 8:
            addr = e.args[0]
            if addr.op == "c":                                                   # 읽기 전용 데이터의 상수 벡터는 값으로
                v = self._const_load(addr.aux)
                if v is not None:
                    return self.PV(ir.C(v, 8), vt)
            return f"vld1_{SF[vt]}(({SC[vt]} *){self.addr_text(addr)})", PREC["prim"]
        t, s, p = self.P(e if e.sz == 8 else (ir.trunc(e, 8) if e.sz > 8 else ir.zext(e, 8)), "u")
        return f"__builtin_bit_cast({ty}, (uint64_t){self._paren(t, p, PREC['unary'])})", PREC["prim"]

    def _vec_text(self, e: X, vt):
        op = e.op
        if op == "vop":
            return self._vop_text(e, vt)
        if op == "vshuf":
            idx, esz, cls = e.aux
            a, _ = self.PV(e.args[0], (esz, cls))
            b, _ = self.PV(e.args[1], (esz, cls))
            return f"__builtin_shufflevector({a}, {b}, {', '.join(map(str, idx))})", PREC["prim"]
        if op == "vdup":
            esz, cls = e.aux
            return f"vdup_n_{SF[(esz, cls)]}({self._scal(e.args[0], esz, cls)})", PREC["prim"]
        if op == "vpack":
            esz, cls = e.aux
            return f"(({TN[(esz, cls)]}){{{', '.join(self._scal(x, esz, cls) for x in e.args)}}})", PREC["prim"]
        if op == "vsetlane":
            idx, esz, cls = e.aux
            v, _ = self.PV(e.args[0], (esz, cls))
            return f"vset_lane_{SF[(esz, cls)]}({self._scal(e.args[1], esz, cls)}, {v}, {idx})", PREC["prim"]
        return "0", PREC["prim"]

    def _vop_text(self, e: X, vt):
        name, esz, cls = e.aux
        res, argt = vop_types(e)
        cl = res[1] or vt[1]
        at = [(t[0], t[1] or vt[1]) if t is not None else None for t in argt]
        parts = []
        for a, t in zip(e.args, at):
            if t is None:                                                   # 즉값(시프트 양 등)
                parts.append(self.fit(a, "u", PREC["unary"]))
            else:
                parts.append(self.PV(a, t))
        if name in _OPER:
            texts = [t if isinstance(t, str) else self._paren(t[0], t[1], PREC["unary"]) for t in parts]
            pr = PREC[_PREC_OPER[name]]
            if name in ("neg", "fneg", "not"):
                return _OPER[name].format(*texts), PREC["unary"]
            return _OPER[name].format(*[self._paren_s(t, e.args[i], pr) for i, t in enumerate(texts)]), pr
        if name in CONVS:
            a, r = CONVS[name]
            return f"vcvt_{SF[r]}_{SF[a]}({parts[0][0]})", PREC["prim"]
        texts = [t if isinstance(t, str) else t[0] for t in parts]
        sfx = SF[(at[0] or res)[0:2]] if at and at[0] is not None else SF[(esz, cl)]
        if name in ("ceq", "tst"):
            sfx = SF[(esz, at[0][1])]
        if name == "shl":
            sfx = SF[(esz, cl)]
        return f"{_INTR[name]}_{sfx}({', '.join(texts)})", PREC["prim"]

    def _paren_s(self, t, arg: X, pr):
        """연산자 식의 피연산자: 이미 문자열이면 연산자 우선순위에 맞게 괄호를 씌운다(벡터 식은 우선순위를 알 수 없으니 단순하면 그대로)."""
        if t.replace("_", "").replace(".", "").isalnum():
            return t
        if t.endswith(")") and "(" in t and t.index("(") > 0 and t[:t.index("(")].replace("_", "").isalnum():
            return t
        return f"({t})"

    def _const_load(self, addr: int):
        sec = self.fd.model.section_for(addr)
        if sec is None or sec.is_code or sec.writable:
            return None
        data = self.fd.prog.dis.read(addr, 8)
        if not data or len(data) < 8:
            return None
        return int.from_bytes(data, "little" if self.fd.model.endian != "big" else "big")

    def bits_of_vec(self, e: X):
        """벡터 식을 정수 문맥에서 쓸 때: 비트를 그대로 옮긴다."""
        res, _ = vop_types(e)
        vt = (res[0], res[1] or "s")
        t, p = self.PV(e, vt)
        return f"__builtin_bit_cast(uint64_t, {t})", "u", PREC["prim"]

    def lane_text(self, e: X):
        """vlane·vlanef·vred: 벡터에서 꺼낸 스칼라."""
        if e.op in ("vlane", "vlanef"):
            idx, esz, cls = e.aux
            v, _ = self.PV(e.args[0], (esz, cls))
            return f"vget_lane_{SF[(esz, cls)]}({v}, {idx})", cls
        name, esz, cls = e.aux
        v, _ = self.PV(e.args[0], (esz, cls))
        return f"v{name}_{SF[(esz, cls)]}({v})", cls

    def vec_assign_text(self, dst: X, src: X):
        """대입 대상이 벡터 웹·벡터 메모리이면 문장 문자열, 아니면 None."""
        if dst.op == "var" and dst.aux in self.vkind:
            t, p = self.PV(src, self.vkind[dst.aux])
            return f"{self.vname(dst.aux)} = {t};"
        if dst.op == "mem" and dst.sz == 8 and (dst in self.vmem or (src.op in ir.VEC_OPS) or (src.op == "var" and src.aux in self.vkind)):
            vt = self.vmem.get(dst) or (self.vkind.get(src.aux) if src.op == "var" else None) or (
                (lambda r: (r[0], r[1] or "s"))(vop_types(src)[0]) if src.op in ir.VEC_OPS else None)
            if vt is None:
                return None
            val, _ = self.PV(src, vt)
            return f"vst1_{SF[vt]}(({SC[vt]} *){self.addr_text(dst.args[0])}, {val});"
        return None
