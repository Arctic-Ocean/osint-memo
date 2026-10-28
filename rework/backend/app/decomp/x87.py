"""x87 FPU 지원: 레지스터 스택을 정적으로 풀어 일반 변수로 만든다.

x87 의 `st(i)` 는 스택 꼭대기 기준 번호라 같은 명령이 위치에 따라 다른 레지스터를 가리킨다. 컴파일러가 만드는 코드는 어느 지점에서든 스택 깊이가
정해져 있으므로(합류 지점에서 깊이가 같다), 함수 전체에서 **명령마다 깊이**를 계산해 두면 `st(i)` 를 '바닥에서부터 몇 번째' 칸(`x87_k`)이라는
고정된 이름으로 바꿀 수 있다. 그 뒤에는 보통의 레지스터와 똑같이 데이터플로·식 전파·구조화가 돌아간다.

- 값은 기본적으로 double(8바이트)로 담는다(float 메모리는 읽을 때 double 로 넓히고 쓸 때 줄인다). `fld/fstp tbyte`(long double)가 하나라도 있는 함수는
  80비트(10바이트) 모드로 전체를 long double 로 담는다.
- `ret` 직전 깊이가 1 이면 st(0) 이 반환값이다(`x87ret` 위치에 대입해 준다). 호출된 함수가 st(0) 에 값을 남기는지는 호출 대상의 분석 결과·원형 표,
  모르면 호출 뒤 코드가 스택 밑으로 읽는지(언더플로)로 알아낸다.
- 비교(`fcom*` + `fnstsw ax` + `sahf`·`test ah,imm`·`and ah,imm`+`cmp`)는 상태 레지스터 값을 네 가지 결과(크다·작다·같다·정렬 불가)로 나눠 따로 계산한 뒤
  분기 조건을 `a < b` 같은 직접 비교로 푼다.
- `fldcw` 로 바꾼 반올림 모드는 `or ah,12`·`mov [m], imm` 같은 제어 워드 계산을 보고 따라가 `fistp` 가 잘라 내는지(C 캐스트)·가장 가까운 정수로 반올림하는지를 정한다.
"""
from __future__ import annotations

import math
import re
import struct

from capstone import CS_OP_MEM, CS_OP_REG

from . import ir
from .ir import C, X, S, loc, mem, add, binop, unop, zext, sext, trunc, cmp, ite, flags, cc

FLAGS = "F"

# ---- 니모닉 분류 ---------------------------------------------------------------------------
CONST_PUSH = {"fld1", "fldz", "fldpi", "fldl2e", "fldl2t", "fldlg2", "fldln2"}
ARITH = {"fadd": ("fadd", False), "fsub": ("fsub", False), "fsubr": ("fsub", True), "fmul": ("fmul", False),
         "fdiv": ("fdiv", False), "fdivr": ("fdiv", True)}
ARITH_P = {"faddp": ("fadd", False), "fsubp": ("fsub", False), "fsubrp": ("fsub", True), "fmulp": ("fmul", False),
           "fdivp": ("fdiv", False), "fdivrp": ("fdiv", True)}
ARITH_I = {"fiadd": ("fadd", False), "fisub": ("fsub", False), "fisubr": ("fsub", True), "fimul": ("fmul", False),
           "fidiv": ("fdiv", False), "fidivr": ("fdiv", True)}
UNARY = {"fchs", "fabs", "fsqrt", "frndint", "fsin", "fcos", "f2xm1", "fxam", "ftst"}
BINARY_KEEP = {"fscale", "fprem", "fprem1"}                 # st(0) 을 갱신, st(1) 은 그대로
BINARY_POP = {"fyl2x", "fyl2xp1", "fpatan"}                 # st(1) 을 갱신하고 pop
PUSH_UNARY = {"fptan", "fsincos", "fxtract"}
COMI = {"fcomi": 0, "fucomi": 0, "fcomip": 1, "fucomip": 1, "fcompi": 1, "fucompi": 1}
COM = {"fcom": 0, "fucom": 0, "fcomp": 1, "fucomp": 1, "fcompp": 2, "fucompp": 2, "ficom": 0, "ficomp": 1}
NOOPS = {"fnstsw", "fstsw", "fnstcw", "fstcw", "fldcw", "fnclex", "fclex", "fnop", "wait", "fwait", "ffree"}
CMOV = {"fcmovb": "b", "fcmove": "e", "fcmovbe": "be", "fcmovu": "p", "fcmovnb": "ae", "fcmovne": "ne", "fcmovnbe": "a", "fcmovnu": "np"}
UNSUPPORTED = {"fninit", "finit", "fldenv", "fnstenv", "fstenv", "frstor", "fnsave", "fsave", "fincstp", "fdecstp", "fbld", "fbstp"}
ALL = (CONST_PUSH | set(ARITH) | set(ARITH_P) | set(ARITH_I) | UNARY | BINARY_KEEP | BINARY_POP | PUSH_UNARY | set(COMI) | set(COM)
       | NOOPS | set(CMOV) | UNSUPPORTED | {"fld", "fild", "fst", "fstp", "fist", "fistp", "fisttp", "fxch", "ffreep"})

_ST = re.compile(r"st\((\d)\)")


def is_x87(mnemonic: str) -> bool:
    return mnemonic in ALL


def st_regs(insn) -> list:
    """피연산자 문자열에서 `st(i)` 번호들(캡스톤은 암묵 피연산자를 형태마다 다르게 알려 주므로 글자로 읽는다)."""
    return [int(x) for x in _ST.findall(insn.op_str)]


def mem_operand(insn):
    for op in insn.operands:
        if op.type == CS_OP_MEM:
            return op
    return None


def effect(insn):
    """(스택에 최소한 있어야 하는 깊이, 깊이 변화). 모델링하지 않는 명령은 None."""
    m = insn.mnemonic
    regs = st_regs(insn)
    mm = mem_operand(insn) is not None
    first = regs[0] if regs else None
    if m in UNSUPPORTED:
        return None
    if m in CONST_PUSH:
        return (0, 1)
    if m == "fld":
        return (0, 1) if mm else (first + 1, 1)
    if m == "fild":
        return (0, 1)
    if m in ("fst", "fist"):
        return (1 if mm or first is None else max(1, first + 1), 0)
    if m in ("fstp", "fistp", "fisttp"):
        return (1 if mm or first is None else max(1, first + 1), -1)
    if m == "ffreep":
        return (1, -1)
    if m in ARITH or m in ARITH_I:
        if mm:
            return (1, 0)
        return (max(regs) + 1 if regs else 1, 0)
    if m in ARITH_P:
        return ((first if first is not None else 1) + 1, -1)
    if m == "fxch":
        return ((first if first is not None else 1) + 1, 0)
    if m in UNARY:
        return (1, 0)
    if m in BINARY_KEEP:
        return (2, 0)
    if m in BINARY_POP:
        return (2, -1)
    if m in PUSH_UNARY:
        return (1, 1)
    if m in COMI:
        return ((first if first is not None else 1) + 1, -COMI[m])
    if m in COM:
        pops = COM[m]
        if m in ("fcompp", "fucompp"):
            return (2, -2)
        if mm:
            return (1, -pops)
        return ((first if first is not None else 1) + 1, -pops)
    if m in CMOV:
        return ((regs[-1] if regs else 1) + 1, 0)
    if m in NOOPS:
        return (0, 0)
    return None


# ---- 스택 깊이 분석 ----------------------------------------------------------------------------
class StackInfo:
    def __init__(self):
        self.depth: dict = {}          # 명령 주소 → 그 명령 직전 스택 깊이
        self.call_fp: dict = {}        # 호출 주소 → 호출된 함수가 st(0) 에 값을 남기는가
        self.ret_depth: dict = {}      # ret 주소 → 깊이
        self.conflicts: list = []      # 합류 지점에서 깊이가 달랐던 주소
        self.underflow: list = []      # 스택에 없는 칸을 읽는 명령
        self.unsupported: list = []    # 모델링하지 못한 x87 명령
        self.long_double = False
        self.W = 8


def _targets(insn):
    for op in insn.operands:
        if op.type == 2:                      # CS_OP_IMM
            return op.imm & 0xFFFFFFFFFFFFFFFF
    return None


def _lookahead_returns_fp(insns, start_idx) -> bool:
    """호출 직후부터 직선으로 따라가며, 호출이 남긴 값이 없다면 스택 밑을 읽게 되는지로 st(0) 반환 여부를 짐작한다."""
    d = 0
    for ins in insns[start_idx:start_idx + 40]:
        m = ins.mnemonic
        if m.startswith(("j", "ret", "call", "loop")) or m in ("hlt", "ud2"):
            break
        if not is_x87(m):
            continue
        e = effect(ins)
        if e is None:
            break
        need, delta = e
        if need > d:
            return True
        d += delta
    return False


def analyze(insns, entry: int, callee_fp=None) -> StackInfo:
    """함수의 명령 목록에서 명령마다 x87 스택 깊이를 구한다. callee_fp(주소) → True·False·None(모름)."""
    info = StackInfo()
    idx = {ins.address: i for i, ins in enumerate(insns)}
    for ins in insns:
        if is_x87(ins.mnemonic):
            op = mem_operand(ins)
            if op is not None and op.size == 10 and ins.mnemonic in ("fld", "fstp", "fst"):
                info.long_double = True
    info.W = 10 if info.long_double else 8
    work = [(entry, 0)]
    seen_call: dict = {}
    while work:
        addr, d = work.pop()
        while True:
            i = idx.get(addr)
            if i is None:
                break
            if addr in info.depth:
                if info.depth[addr] != d:
                    info.conflicts.append(addr)
                break
            info.depth[addr] = d
            ins = insns[i]
            m = ins.mnemonic
            nxt = addr + ins.size
            if is_x87(m):
                e = effect(ins)
                if e is None:
                    info.unsupported.append(addr)
                else:
                    need, delta = e
                    if need > d:
                        info.underflow.append(addr)
                    d = max(0, d + delta)
            elif m == "call" or m.endswith(" call"):
                tgt = _targets(ins)
                r = callee_fp(tgt) if (callee_fp is not None and tgt is not None) else None
                if r is None:
                    r = _lookahead_returns_fp(insns, i + 1)
                info.call_fp[addr] = bool(r)
                if r:
                    d += 1
            elif m == "ret" or m == "retf":
                info.ret_depth[addr] = d
                break
            elif m.split()[-1] == "jmp":
                t = _targets(ins)
                if t is not None:
                    work.append((t, d))
                break
            elif m.split()[-1].startswith("j") or m.split()[-1] in ("loop", "loope", "loopne"):
                t = _targets(ins)
                if t is not None:
                    work.append((t, d))
            elif m in ("hlt", "ud2", "int3"):
                break
            addr = nxt
    return info


# ---- 상수 ----------------------------------------------------------------------------------
_EXT_CONST = {"fldpi": (0x4000, 0xC90FDAA22168C235), "fldl2e": (0x3FFF, 0xB8AA3B295C17F0BC), "fldl2t": (0x4000, 0xD49A784BCD1B8AFE),
              "fldlg2": (0x3FFD, 0x9A209A84FBCFF799), "fldln2": (0x3FFE, 0xB17217F7D1CF79AC), "fld1": (0x3FFF, 1 << 63), "fldz": (0, 0)}
_DBL_CONST = {"fld1": 1.0, "fldz": 0.0, "fldpi": math.pi, "fldl2e": math.log2(math.e), "fldl2t": math.log2(10), "fldlg2": math.log10(2),
              "fldln2": math.log(2)}


def ext_to_float(bits: int) -> float:
    mant, se = bits & ((1 << 64) - 1), (bits >> 64) & 0xFFFF
    sign, exp = se >> 15, se & 0x7FFF
    if exp == 0x7FFF:
        return float("nan") if mant << 1 & ((1 << 64) - 1) else (float("-inf") if sign else float("inf"))
    if exp == 0 and mant == 0:
        return -0.0 if sign else 0.0
    return math.ldexp(mant, exp - 16383 - 63) * (-1 if sign else 1)


def float_to_ext(v: float) -> int:
    if v != v:
        return (0x7FFF << 64) | (0xC << 60)
    sign = 1 if math.copysign(1.0, v) < 0 else 0
    if math.isinf(v):
        return (((sign << 15) | 0x7FFF) << 64) | (1 << 63)
    if v == 0:
        return (sign << 15) << 64
    m, e = math.frexp(abs(v))                      # abs(v) = m * 2**e, 0.5 <= m < 1
    mant = int(m * (1 << 64))
    return ((((sign << 15) | (e - 1 + 16383)) << 64)) | mant


def const_to_float(bits: int, sz: int) -> float:
    if sz == 4:
        return struct.unpack("<f", (bits & 0xFFFFFFFF).to_bytes(4, "little"))[0]
    if sz == 8:
        return struct.unpack("<d", (bits & ((1 << 64) - 1)).to_bytes(8, "little"))[0]
    return ext_to_float(bits)


def float_to_const(v: float, sz: int) -> int:
    if sz == 4:
        try:
            return struct.unpack("<I", struct.pack("<f", v))[0]
        except OverflowError:
            return struct.unpack("<I", struct.pack("<f", math.copysign(math.inf, v)))[0]
    if sz == 8:
        return struct.unpack("<Q", struct.pack("<d", v))[0]
    return float_to_ext(v)


def convert_const(c: X, sz: int) -> X:
    return C(float_to_const(const_to_float(c.aux, c.sz), sz), sz)


# ---- 조건 시뮬레이션: fnstsw ax 이후 ah 를 다루는 명령들 -------------------------------------------------
# 비교 결과별 상태 워드 상위 바이트: C0(bit0)·C2(bit2)·C3(bit6)
OUTCOMES = {"gt": 0x00, "lt": 0x01, "eq": 0x40, "un": 0x45}
OUTCOME_OP = {"gt": "fgt", "lt": "flt", "eq": "feq", "un": "funord"}


def _parity(v: int) -> int:
    return 1 if bin(v & 0xFF).count("1") % 2 == 0 else 0


def _logic_flags(res: int):
    res &= 0xFF
    return (int(res == 0), res >> 7 & 1, 0, 0, _parity(res))              # zf, sf, cf, of, pf


def _sub_flags(a: int, b: int):
    a &= 0xFF
    b &= 0xFF
    res = (a - b) & 0xFF
    sa, sb, sr = a >> 7, b >> 7, res >> 7
    return (int(res == 0), sr, int(a < b), int(sa != sb and sr != sa), _parity(res))


def _cc_true(code: str, f) -> bool:
    zf, sf, cf, of, pf = f
    return {"e": zf, "z": zf, "ne": not zf, "nz": not zf, "b": cf, "c": cf, "nae": cf, "ae": not cf, "nb": not cf, "nc": not cf,
            "be": cf or zf, "na": cf or zf, "a": not (cf or zf), "nbe": not (cf or zf), "s": sf, "ns": not sf, "p": pf, "pe": pf,
            "np": not pf, "po": not pf, "o": of, "no": not of, "l": sf != of, "nge": sf != of, "ge": sf == of, "nl": sf == of,
            "le": zf or sf != of, "ng": zf or sf != of, "g": not zf and sf == of, "nle": not zf and sf == of}[code]


class AhSim:
    """`fnstsw ax` 로 상태 워드를 ax 에 꺼낸 뒤, ah 를 다루는 명령을 네 가지 비교 결과별로 따로 계산해 둔다.
    fprem 뒤에는 C2(불완전 나머지)가 0 인 결과 하나만 있다(되풀이 루프를 fmod 한 번으로 접었으므로)."""

    def __init__(self, A, B, W, done=False):
        self.A, self.B, self.W = A, B, W
        self.done = done
        self.vals = {"done": 0x00} if done else dict(OUTCOMES)
        self.flags = None

    def test(self, imm):
        self.flags = {o: _logic_flags(v & imm) for o, v in self.vals.items()}

    def and_(self, imm):
        self.vals = {o: v & imm for o, v in self.vals.items()}
        self.flags = {o: _logic_flags(v) for o, v in self.vals.items()}

    def cmp(self, imm):
        self.flags = {o: _sub_flags(v, imm) for o, v in self.vals.items()}

    def sahf(self):
        # SAHF: CF=ah.0, PF=ah.2, ZF=ah.6, SF=ah.7(0) — fcomi 와 같은 플래그
        self.flags = {o: (v >> 6 & 1, 0, v & 1, 0, v >> 2 & 1) for o, v in self.vals.items()}

    def cond(self, code: str):
        """분기 조건: 이 조건이 참이 되는 비교 결과들의 부동소수점 비교를 OR 로 묶은 불리언."""
        if self.flags is None:
            return None
        if self.done:
            return C(1 if _cc_true(code, self.flags["done"]) else 0, 1)
        outs = [o for o in OUTCOMES if _cc_true(code, self.flags[o])]
        if len(outs) == 4:
            return C(1, 1)
        if not outs:
            return C(0, 1)
        A, B = self.A, self.B
        # 자주 나오는 묶음을 한 비교로: a >= b = gt|eq, a <= b = lt|eq, a != b = lt|gt|un, a == b = eq 등
        s = set(outs)
        simple = {frozenset({"gt"}): X("fgt", (A, B), 1), frozenset({"lt"}): X("flt", (A, B), 1), frozenset({"eq"}): X("feq", (A, B), 1),
                  frozenset({"un"}): X("funord", (A, B), 1), frozenset({"gt", "eq"}): X("fge", (A, B), 1),
                  frozenset({"lt", "eq"}): X("fle", (A, B), 1), frozenset({"lt", "gt", "un"}): X("fne", (A, B), 1)}
        if frozenset(s) in simple:
            return simple[frozenset(s)]
        r = None
        for o in outs:
            t = X(OUTCOME_OP[o], (A, B), 1)
            r = t if r is None else ir.lor(r, t)
        return r


def rc_from_cw(cw: int) -> str:
    return {0: "near", 1: "down", 2: "up", 3: "trunc"}[(cw >> 10) & 3]


FROUND = {"near": "rint", "down": "floor", "up": "ceil", "trunc": "trunc"}


def _memkey(op):
    m = op.mem
    return (m.base, m.index, m.scale, m.disp, m.segment)


# ---- 리프터 믹스인 ---------------------------------------------------------------------------------
class X87Mixin:
    """X86Lifter 에 섞어 쓴다. self.x87(StackInfo) 이 있을 때만 쓰인다."""

    x87 = None

    def x87_setup(self, info: StackInfo):
        self.x87 = info
        self.W = info.W
        self.rc = "near"
        self.cw_pending = None
        self.cw_imm = {}
        self.fsw = None
        self.ahsim = None
        self.x87_used = False

    def x87_reset(self):
        self.fsw = None
        self.ahsim = None

    # ---- 보조 ----
    def _fp_mem(self, insn, op):
        """메모리 피연산자를 현재 폭(W) 의 부동소수점 값으로."""
        ad = self.mem_addr(insn, op)
        sz = op.size
        v = self.load_mem(ad, sz)
        if sz == self.W:
            return v
        if v.op == "c":
            return convert_const(v, self.W)
        return X("f2f", (v,), self.W)

    def _int_mem(self, insn, op):
        ad = self.mem_addr(insn, op)
        v = self.load_mem(ad, op.size)
        return X("i2f", (v,), self.W, "s")

    def _x87_const(self, m):
        if self.W == 10:
            se, mant = _EXT_CONST[m]
            return C((se << 64) | mant, 10)
        return C(float_to_const(_DBL_CONST[m], 8), 8)

    def x87_cond(self, code: str):
        """jcc·setcc·cmovcc 의 조건: ah 시뮬레이션이 살아 있으면 직접 비교, 아니면 플래그에서."""
        if self.ahsim is not None:
            r = self.ahsim.cond(code)
            if r is not None:
                return r
        return cc(code, loc(FLAGS, 1))

    def x87_ah_op(self, insn, m, ops):
        """`fnstsw ax` 뒤 ah(또는 ax)를 다루는 test·and·cmp·sahf. 처리했으면 문장 목록(비어 있을 수 있음), 아니면 None."""
        sim = self.ahsim
        if sim is None:
            return None
        if m == "sahf":
            sim.sahf()
            return []
        if len(ops) != 2 or ops[0].type != CS_OP_REG or ops[1].type != 2:
            return None
        name = self.cs.reg_name(ops[0].reg)
        imm = ops[1].imm
        if name == "ah":
            imm &= 0xFF
        elif name == "ax":
            if imm & 0xFF:                       # 하위 바이트(al)까지 건드리면 시뮬레이션하지 않는다
                return None
            imm = (imm >> 8) & 0xFF
        else:
            return None
        if m == "test":
            sim.test(imm)
            return []
        if m == "and":
            sim.and_(imm)
            return []
        if m == "cmp":
            sim.cmp(imm)
            return []
        return None

    # ---- 진입점 ----
    def x87_lift(self, insn):
        from .lift_x86 import Unsupported
        info = self.x87
        a = insn.address
        m = insn.mnemonic
        d = info.depth.get(a)
        if d is None or a in info.conflicts or a in info.underflow:
            raise Unsupported("x87 depth")
        e = effect(insn)
        if e is None:
            raise Unsupported(m)
        self.x87_used = True
        W = self.W
        regs = st_regs(insn)
        mop = mem_operand(insn)
        out = []
        asg = lambda dst, src: out.append(S("asg", a, dst, src))

        def st(i):
            return loc(f"x87_{d - 1 - i}", W)

        def new_top():
            return loc(f"x87_{d}", W)

        first = regs[0] if regs else None

        # -- 적재 --
        if m in CONST_PUSH:
            asg(new_top(), self._x87_const(m))
            return out
        if m == "fld":
            asg(new_top(), self._fp_mem(insn, mop) if mop is not None else st(first))
            return out
        if m == "fild":
            asg(new_top(), self._int_mem(insn, mop))
            return out
        # -- 저장 --
        if m in ("fst", "fstp"):
            pop = m == "fstp"
            if mop is not None:
                ad = self.mem_addr(insn, mop)
                sz = mop.size
                v = st(0)
                if sz != W:
                    v = X("f2f", (v,), sz)
                asg(mem(ad, sz), v)
            else:
                i = first if first is not None else 1
                if i != 0:
                    asg(st(i), st(0))
            return out
        if m in ("fist", "fistp", "fisttp"):
            ad = self.mem_addr(insn, mop)
            v = st(0)
            if m != "fisttp" and self.rc != "trunc":
                v = X("fround", (v,), W, FROUND[self.rc])
            asg(mem(ad, mop.size), X("f2i", (v,), mop.size))
            return out
        if m in ("ffreep",):
            return out
        if m == "ffree":
            return out
        # -- 산술 --
        if m in ARITH or m in ARITH_I or m in ARITH_P:
            if m in ARITH_P:
                op, rev = ARITH_P[m]
                i = first if first is not None else 1
                dst, x, y = st(i), st(i), st(0)
            elif mop is not None:
                op, rev = (ARITH_I if m in ARITH_I else ARITH)[m]
                dst, x = st(0), st(0)
                y = self._int_mem(insn, mop) if m in ARITH_I else self._fp_mem(insn, mop)
            elif len(regs) >= 2:
                op, rev = ARITH[m]
                dst, x, y = st(regs[0]), st(regs[0]), st(regs[1])
            else:
                op, rev = ARITH[m]
                dst, x, y = st(0), st(0), st(first if first is not None else 1)
            asg(dst, X(op, (y, x) if rev else (x, y), W))
            return out
        if m == "fxch":
            i = first if first is not None else 1
            if i != 0:
                t = self.tmp(W)
                asg(t, st(0))
                asg(st(0), st(i))
                asg(st(i), t)
            return out
        if m == "fchs":
            asg(st(0), X("fneg", (st(0),), W))
            return out
        if m == "fabs":
            asg(st(0), X("fabs", (st(0),), W))
            return out
        if m == "fsqrt":
            asg(st(0), X("fsqrt", (st(0),), W))
            return out
        if m == "frndint":
            asg(st(0), X("fround", (st(0),), W, FROUND[self.rc]))
            return out
        # -- 비교 --
        if m in COMI or m in COM:
            if m in COMI or mop is None:
                i = first if first is not None else 1
                B = st(i) if m not in ("fcompp", "fucompp") else st(1)
            elif m in ("ficom", "ficomp"):
                B = self._int_mem(insn, mop)
            else:
                B = self._fp_mem(insn, mop)
            if m in ("fcompp", "fucompp"):
                B = st(1)
            tA, tB = self.tmp(W), self.tmp(W)
            asg(tA, st(0))
            asg(tB, B)
            if m in COMI:
                asg(loc(FLAGS, 1), flags("fcmp", (tA, tB), W))
            else:
                self.fsw = (tA, tB)
            return out
        if m in ("fnstsw", "fstsw"):
            if self.fsw is None or not insn.operands or insn.operands[0].type != CS_OP_REG or self.cs.reg_name(insn.operands[0].reg) != "ax":
                raise Unsupported("fnstsw")
            if self.fsw == "done":
                self.ahsim = AhSim(None, None, W, done=True)
                return [self.write_reg_named("ax", C(0, 2), a)]
            A, B = self.fsw
            self.ahsim = AhSim(A, B, W)
            return [self.write_reg_named("ax", X("fsw", (A, B), 2), a)]
        if m in ("fnstcw", "fstcw"):
            return []
        if m == "fldcw":
            imm = None
            if mop is not None:
                imm = self.cw_imm.get(_memkey(mop))
            if imm is not None:
                self.rc = rc_from_cw(imm)
            elif self.cw_pending is not None:
                self.rc = self.cw_pending
            else:
                self.rc = "near"
            self.cw_pending = None
            return []
        if m in ("fnclex", "fclex", "fnop", "wait", "fwait"):
            return []
        if m in CMOV:
            i = regs[-1] if regs else 1
            asg(st(0), ite(self.x87_cond(CMOV[m]), st(i), st(0)))
            return out
        # -- 초월함수: C 라이브러리 함수로 --
        if m in ("fsin", "fcos", "f2xm1"):
            x = st(0)
            asg(x, {"fsin": X("fsin", (st(0),), W), "fcos": X("fcos", (st(0),), W),
                    "f2xm1": X("fsub", (X("fexp2", (st(0),), W), self._one()), W)}[m])
            return out
        if m == "fptan":                                                   # st(0) = tan(st(0)), 그리고 1.0 을 push
            asg(st(0), X("ftan", (st(0),), W))
            asg(new_top(), self._one())
            return out
        if m == "fsincos":                                                 # st(1) = sin, st(0) = cos (push)
            t = self.tmp(W)
            asg(t, st(0))
            asg(st(0), X("fsin", (t,), W))
            asg(new_top(), X("fcos", (t,), W))
            return out
        if m == "fpatan":                                                  # st(1) = atan2(st(1), st(0)); pop
            asg(st(1), X("fatan2", (st(1), st(0)), W))
            return out
        if m in ("fyl2x", "fyl2xp1"):                                      # st(1) = st(1) * log2(st(0)[+1]); pop
            arg = st(0) if m == "fyl2x" else X("fadd", (st(0), self._one()), W)
            asg(st(1), X("fmul", (st(1), X("flog2", (arg,), W)), W))
            return out
        if m == "fscale":                                                  # st(0) = st(0) * 2^trunc(st(1))
            asg(st(0), X("fmul", (st(0), X("fexp2", (X("fround", (st(1),), W, "trunc"),), W)), W))
            return out
        if m in ("fprem", "fprem1"):                                       # 나머지가 완전히 줄어들 때까지 되풀이하는 명령 → 한 번에 fmod
            asg(st(0), X("fmod" if m == "fprem" else "fremainder", (st(0), st(1)), W))
            self.fsw = "done"
            return out
        if m == "ftst":
            tA = self.tmp(W)
            asg(tA, st(0))
            self.fsw = (tA, C(0, W))
            return out
        raise Unsupported(m)

    def _one(self):
        return self._x87_const("fld1")

    # ---- 제어 워드 추적(주변 일반 명령에서) ----
    def x87_note_int_insn(self, insn):
        """`or ah,12`·`or eax,0xC00`·`mov word [m], imm` 같은 제어 워드 계산을 보고 다음 fldcw 의 반올림 모드를 알아 둔다."""
        m = insn.mnemonic
        ops = insn.operands
        if m == "or" and len(ops) == 2 and ops[0].type == CS_OP_REG and ops[1].type == 2:
            name = self.cs.reg_name(ops[0].reg)
            imm = ops[1].imm
            if name == "ah":
                self.cw_pending = rc_from_cw((imm & 0xFF) << 8)
            elif name in ("ax", "eax"):
                self.cw_pending = rc_from_cw(imm & 0xFFFF)
        elif m == "mov" and len(ops) == 2 and ops[0].type == CS_OP_MEM and ops[1].type == 2 and ops[0].size in (2, 4):
            self.cw_imm[_memkey(ops[0])] = ops[1].imm & 0xFFFF
        elif m == "fnstcw":
            self.cw_pending = None

    def x87_ret(self, insn):
        """ret 직전 x87 스택에 값이 하나 남아 있으면 반환값(x87ret 위치)으로 보낸다."""
        info = self.x87
        d = info.ret_depth.get(insn.address) if info is not None else None
        if d == 1:
            return [S("asg", insn.address, loc("x87ret", self.W), loc("x87_0", self.W))]
        return []

    def x87_after_call(self, insn, call_stmt):
        """호출이 st(0) 에 값을 남기면 그 값을 새 꼭대기 칸으로 옮긴다."""
        info = self.x87
        if info is None or not info.call_fp.get(insn.address):
            return []
        d = info.depth.get(insn.address, 0)
        call_stmt.extra = dict(call_stmt.extra or {}, x87ret=self.W)
        return [S("asg", insn.address, loc(f"x87_{d}", self.W), loc("x87ret", self.W))]
