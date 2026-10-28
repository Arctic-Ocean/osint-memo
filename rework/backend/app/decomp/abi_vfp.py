"""ARM32 AAPCS-VFP(하드 플로트) 실수 인자·반환 규약.

실수 인자는 s0–s15 / d0–d7 에 담긴다: float 은 비어 있는 s 레지스터 중 가장 낮은 것, double 은 비어 있는 짝수 쌍(d 레지스터) 중 가장 낮은 것.
(그래서 `f(float a, double b, float c)` 는 a=s0, b=d1, c=s1 이다.) 반환은 float 가 s0, double 이 d0 이다.
정수 인자는 r0–r3 와 스택이라 이 모듈과 무관하다.

매개변수는 레지스터 순서로 나열한다: 같은 규칙으로 배정하면 같은 레지스터가 나오므로 호출하는 쪽과 받는 쪽이 서로 일관된다
(C 원형의 선언 순서와 다를 수 있다 — 정수·실수 레지스터를 따로 세는 x86-64·ARM64 와 같은 한계).
"""
from __future__ import annotations

from . import ir
from .ir import X, S, loc
from .stack import stmt_exprs


def alloc(kinds) -> list:
    """kind 목록(4=float, 8=double) → 각 인자의 ('s', 번호) | ('d', 번호) | None(스택)."""
    free = [True] * 16
    out = []
    full = False
    for kd in kinds:
        if not full:
            if kd == 4:
                n = next((i for i in range(16) if free[i]), None)
                if n is not None:
                    free[n] = False
                    out.append(("s", n))
                    continue
            else:
                n = next((i for i in range(0, 16, 2) if free[i] and free[i + 1]), None)
                if n is not None:
                    free[n] = free[n + 1] = False
                    out.append(("d", n // 2))
                    continue
            full = True                                           # 자리가 없으면 이후 실수 인자는 전부 스택
        out.append(None)
    return out


def fill(ent: dict) -> list:
    """{s 단위 위치: (값, kind)} → 레지스터 순서의 [(값 또는 None, kind)]; 빈 곳은 같은 규칙으로 배정되도록 자리표시(None)를 채운다."""
    if not ent:
        return []
    top = max(ent)
    seq = []
    p = 0
    while p <= top:
        if p in ent:
            v, kd = ent[p]
            seq.append((v, kd))
            p += kd // 4
        elif p % 2 == 0 and (p + 1) not in ent and any(q > p + 1 for q in ent):
            seq.append((None, 8))
            p += 2
        else:
            seq.append((None, 4))
            p += 1
    return seq


def entry_params(fd, used) -> list:
    """함수가 진입 때 읽는 실수 인자: [(웹 또는 None, kind)] (레지스터 순서)."""
    flow, lf = fd.flow, fd.lifter
    ent = {}
    for k in range(8):
        if lf.mode_of(k) == "d":
            names = [(f"d{k}", 2 * k, 8)]
        else:
            names = [(f"s{2 * k}", 2 * k, 4), (f"s{2 * k + 1}", 2 * k + 1, 4)]
        for nm, pos, kd in names:
            d = flow.entry_def.get(nm)
            if d is None:
                continue
            w = flow.web_of_def[d.id]
            if w in used and fd.webs[w].entry and not fd.webs[w].param:
                ent[pos] = (w, kd)
    return fill(ent)


def defined_args(fd, flow, s, blk) -> list:
    """호출 대상을 모를 때: 같은 기본 블록에서 실제로 값을 쓴 실수 인자 레지스터만 인자로 본다. 반환은 kind 목록."""
    lf = fd.lifter
    stmts = blk.stmts
    ent = {}
    for k in range(8):
        if lf.mode_of(k) == "d":
            names = [(f"d{k}", 2 * k, 8)]
        else:
            names = [(f"s{2 * k}", 2 * k, 4), (f"s{2 * k + 1}", 2 * k + 1, 4)]
        for nm, pos, kd in names:
            ds = flow.reaching_at(s, nm)
            if any(d.kind in ("asg", "unk") and d.stmt is not None and d.stmt in stmts for d in ds):
                ent[pos] = (True, kd)
    return [kd for _v, kd in fill(ent)]


def arg_exprs(fd, kinds) -> list:
    """호출 쪽에서 kind 목록에 해당하는 레지스터 값 식들(배정은 alloc)."""
    lf = fd.lifter
    out = []
    for kd, slot in zip(kinds, alloc(kinds)):
        if slot is None:
            out.append(None)
        else:
            out.append(lf.vread(slot[0], slot[1]))
    return out


def ret_name(fd) -> str:
    """이 함수에서 첫 칸이 d 모드면 반환 레지스터 위치는 d0, s 모드면 s0 (double 이면 s0·s1 둘)."""
    return "d0" if fd.lifter.mode_of(0) == "d" else "s0"


def ret_expr(fd, kind) -> X:
    lf = fd.lifter
    if not fd.abi.hardfp:                                          # softfp·soft: float 은 r0, double 은 r0:r1
        return loc("r0", 4) if kind == 4 else ir.pair(loc("r0", 4), loc("r1", 4))
    return lf.vread("d", 0) if kind == 8 else lf.vread("s", 0)


def call_result(fd, s, rk) -> list:
    """호출 문장 s 의 반환 실수값을 레지스터 위치로 받는다. 추가로 뒤에 붙일 문장 목록을 돌려준다."""
    lf = fd.lifter
    a = s.addr
    if not fd.abi.hardfp:                                          # softfp·soft: float 은 r0 에 그대로, double 은 r0:r1 을 받아 쪼갠다
        if rk == 8:
            t = lf.tmp(8)
            s.dst = t
            return [S("asg", a, loc("r0", 4), ir.trunc(t, 4)), S("asg", a, loc("r1", 4), ir.hi_half(t))]
        s.dst = loc("r0", 4)
        return []
    if rk == 8:
        if lf.mode_of(0) == "d":
            s.dst = loc("d0", 8)
            return []
        t = lf.tmp(8)                                              # s 모드 칸: 받은 double 을 s0·s1 로 쪼갠다
        s.dst = t
        return [S("asg", a, loc("s0", 4), ir.trunc(t, 4)), S("asg", a, loc("s1", 4), ir.hi_half(t))]
    if lf.mode_of(0) == "s":
        s.dst = loc("s0", 4)
        return []
    t = lf.tmp(4)                                                  # d 모드 칸의 float 반환: 아래 32비트가 값(위쪽은 쓰레기라 0 으로)
    s.dst = t
    return [S("asg", a, loc("d0", 8), ir.zext(t, 8))]


# ---- softfp·soft: r0:r1 / r2:r3 로 오는 double 인자 --------------------------------------------
def pair_entry_params(fd):
    """진입 블록에서 아직 건드리지 않은 r0:r1 (또는 r2:r3) 을 `vmov dN, rA, rB`(= pair)로 읽으면 그 둘은 double 인자 하나다.
    진입 맨 앞에 `rA = dr 의 아래 절반, rB = 위 절반` 을 넣어 8바이트 진입 값 `dr0`/`dr2` 하나가 매개변수가 되게 한다
    (그 뒤 `pair(rA, rB)` 는 식 접기로 `dr` 로 되돌아온다)."""
    if fd.arch != "arm" or fd.abi.hardfp or not getattr(fd.lifter, "vmode", None):
        return
    eb = fd.blocks.get(fd.entry)
    if eb is None:
        return
    written = set()
    cand = []
    for st in eb.stmts:
        for e in stmt_exprs(st):
            for n in ir.walk(e):
                if n.op == "pair" and n.args[0].op == "loc" and n.args[1].op == "loc":
                    a, b = n.args[0].aux, n.args[1].aux
                    if (a, b) in (("r0", "r1"), ("r2", "r3")) and a not in written and b not in written and a not in cand:
                        cand.append(a)
        if st.k == "asg" and st.dst.op == "loc":
            written.add(st.dst.aux)
        elif st.k in ("call", "unk", "ij", "sw"):
            break
    if not cand:
        return
    addr = eb.stmts[0].addr if eb.stmts else fd.entry
    pre = []
    for a in cand:
        b = f"r{int(a[1:]) + 1}"
        d = loc("dr" + a[1:], 8)
        pre.append(S("asg", addr, loc(a, 4), ir.trunc(d, 4)))
        pre.append(S("asg", addr, loc(b, 4), ir.hi_half(d)))
    eb.stmts = pre + eb.stmts
