"""점프 테이블(switch) 복원.

간접 점프 `jmp reg` 직전의 계산식을 블록 안에서 기호적으로 풀어, 점프 테이블을 읽는 모양이면(절대 주소 표 · 위치 독립 상대 표)
표를 읽어 `sw` 종결자로 바꾼다. 인덱스의 상한은 앞선 `cmp idx, N; ja default` 에서 얻는다.
"""
from __future__ import annotations

from . import ir
from .ir import X, S, C, loc, subst, to_signed, mask

SWITCH_MAX = 4096


def _strip(e: X) -> X:
    while e.op in ("zext", "sext", "trunc"):
        e = e.args[0]
    return e


def _addr_parts(a: X):
    """주소식을 (기준 식, 상수) 로 쪼갠다."""
    if a.op == "add" and a.args[1].op == "c":
        return a.args[0], ir.to_signed(a.args[1].aux, a.sz)
    return a, 0


def _disjoint(a1: X, s1: int, a2: X, s2: int) -> bool:
    b1, c1 = _addr_parts(a1)
    b2, c2 = _addr_parts(a2)
    if b1 != b2:
        return False
    return c1 + s1 <= c2 or c2 + s2 <= c1


class _Env:
    """직선 코드를 기호적으로 실행: 위치(loc) 값과 같은 주소식의 메모리 값을 따라간다(스택에 잠시 맡긴 인덱스를 되찾기 위해)."""

    def __init__(self):
        self.regs = {}
        self.mem = []                       # [(주소식, 크기, 값)]

    def ev(self, e: X) -> X:
        if e.op == "loc":
            return self.regs.get(e.aux, e)
        if not e.args:
            return e
        new = tuple(self.ev(a) for a in e.args)
        e2 = ir.rebuild(e, new) if any(n is not o for n, o in zip(new, e.args)) else e
        if e2.op == "mem":
            for addr, sz, val in reversed(self.mem):
                if addr == e2.args[0] and sz == e2.sz:
                    return val
        return e2

    def step(self, s: S):
        if s.k == "asg":
            if s.dst.op == "loc":
                self.regs[s.dst.aux] = self.ev(s.src)
            elif s.dst.op == "mem":
                addr = self.ev(s.dst.args[0])
                val = self.ev(s.src)
                self.mem = [m for m in self.mem if _disjoint(m[0], m[1], addr, s.dst.sz)]
                self.mem.append((addr, s.dst.sz, val))
        elif s.k in ("call", "unk"):
            self.regs.clear()
            self.mem.clear()


def _match_table(E: X, A: X, full: int):
    """점프 대상식 E 가 `base + step * ext(mem[table + idx * esz])` 꼴(표를 읽어 기준 주소에 더하는 모양)이면 읽는 방법을 돌려준다.
    base=0·step=1·확장 없음 이면 절대 주소 표, 4바이트 부호 확장이면 위치 독립 상대 표, 1바이트 오프셋이면 ARM64 의 짧은 표다."""
    sA = _strip(A)

    def is_index(x: X, scale: int) -> bool:
        if x.op == "mul" and x.args[1].op == "c" and x.args[1].aux == scale:
            return _strip(x.args[0]) == sA
        if x.op == "shl" and x.args[1].op == "c" and (1 << x.args[1].aux) == scale:
            return _strip(x.args[0]) == sA
        return scale == 1 and _strip(x) == sA

    base, step, inner = 0, 1, E
    if E.op == "add":
        l, r = E.args
        if r.op == "c":
            base, inner = r.aux, l
        elif l.op == "c":
            base, inner = l.aux, r
        else:
            return None
    if inner.op == "mul" and inner.args[1].op == "c":
        step, inner = inner.args[1].aux, inner.args[0]
    elif inner.op == "shl" and inner.args[1].op == "c":
        step, inner = 1 << inner.args[1].aux, inner.args[0]
    signed = None
    if inner.op in ("sext", "zext"):
        signed, inner = inner.op == "sext", inner.args[0]
    if inner.op != "mem":
        return None
    esz, addr = inner.sz, inner.args[0]
    if addr.op != "add":
        return None
    l, r = addr.args
    if r.op == "c":
        table, idxt = r.aux, l
    elif l.op == "c":
        table, idxt = l.aux, r
    else:
        return None
    if not is_index(idxt, esz):
        return None
    return {"base": base, "step": step, "esz": esz, "signed": bool(signed), "table": table}


def resolve_switches(lifted: list, insns: list, prog, lo: int, hi: int, full: int) -> int:
    """lifted: [(addr, size, [S])] 를 제자리에서 고친다. 바꾼 간접 점프의 수를 돌려준다."""
    count = 0
    for i, (addr, size, stmts) in enumerate(lifted):
        if not stmts or stmts[-1].k != "ij":
            continue
        ij = stmts[-1]
        # 가까운 앞쪽에서 조건 분기와 그 플래그 계산을 찾는다
        cj_i = None
        for j in range(i - 1, max(-1, i - 40), -1):
            t = lifted[j][2][-1] if lifted[j][2] else None
            if t is not None and t.k == "cj":
                cj_i = j
                break
            if t is not None and t.k in ("jmp", "ret", "ij", "noret"):
                break
        if cj_i is None:
            continue
        cj = lifted[cj_i][2][-1]
        if not (cj.src.op == "cc" and cj.src.aux in ("a", "ae", "nbe", "nb", "hi", "hs")):
            continue
        # 플래그 대입 문장(cj 앞쪽에서 가장 가까운 F := fl)
        fpos = None
        for j in range(cj_i, max(-1, cj_i - 12), -1):
            for k in range(len(lifted[j][2]) - 1, -1, -1):
                s = lifted[j][2][k]
                if s.k == "asg" and s.dst.op == "loc" and s.dst.aux == "F":
                    fpos = (j, k)
                    break
            if fpos:
                break
        if not fpos:
            continue
        fs = lifted[fpos[0]][2][fpos[1]]
        fl = fs.src
        if fl.op != "fl" or fl.aux != "sub" or len(fl.args) < 2 or fl.args[1].op != "c":
            continue
        A, N = fl.args[0], fl.args[1].aux
        n_cases = N + 1 if cj.src.aux in ("a", "nbe", "hi") else N
        if n_cases <= 0 or n_cases > SWITCH_MAX:
            continue
        # 조건 분기보다 앞쪽(같은 직선 구간)부터 기호 실행해 인덱스가 스택을 거쳐 오더라도 되찾는다
        w0 = fpos[0]
        for j in range(fpos[0] - 1, max(-1, fpos[0] - 12), -1):
            t_ = lifted[j][2][-1] if lifted[j][2] else None
            if t_ is not None and t_.k in ("jmp", "cj", "ret", "ij", "noret"):
                break
            w0 = j
        env = _Env()
        A_env = None
        for j in range(w0, i + 1):
            for k, s_ in enumerate(lifted[j][2]):
                if j == i and k == len(lifted[j][2]) - 1:
                    break
                if s_.k in ("jmp", "cj", "ret", "noret"):
                    continue
                if (j, k) == fpos:
                    A_env = env.ev(A)
                env.step(s_)
        if A_env is None:
            continue
        E = env.ev(ij.src)
        A_eq = A_env
        if _strip(A_env).op == "loc" and _strip(A_env).aux.startswith("t"):
            tname = _strip(A_env).aux
            found = None
            for j in range(fpos[0], max(-1, fpos[0] - 3), -1):
                for s_ in reversed(lifted[j][2]):
                    if s_.k == "asg" and s_.dst.op == "loc" and s_.dst.aux == tname:
                        found = s_.src
                        break
                if found is not None:
                    break
            if found is not None:
                A_eq = found
        m = _match_table(E, A_eq, full) or _match_table(E, A, full)
        if m is None:
            continue
        esz = m["esz"]
        raw = prog.dis.read(m["table"], esz * n_cases)
        if len(raw) < esz * n_cases:
            continue
        cases = []
        ok = True
        for idx in range(n_cases):
            v = int.from_bytes(raw[idx * esz:(idx + 1) * esz], "little", signed=m["signed"])
            tgt = (m["base"] + v * m["step"]) & mask(full)
            if not (lo <= tgt < hi):
                ok = False
                break
            cases.append((idx, tgt))
        if not ok:
            continue
        # 인덱스를 담을 임시 위치를 F 대입 바로 뒤에 둔다. A 가 `x - K` 이면 x 로 스위치하고 케이스 값에 K 를 더한다.
        base_x, K = A, 0
        if A.op == "add" and A.args[1].op == "c" and A.sz >= 4:
            base_x, K = A.args[0], -to_signed(A.args[1].aux, A.sz)
        tmp = loc(f"sw{addr:x}", base_x.sz)
        lifted[fpos[0]][2].insert(fpos[1] + 1, S("asg", fs.addr, tmp, base_x))
        sw_cases = [(to_signed((v + K) & mask(base_x.sz), base_x.sz), tg) for v, tg in cases]
        stmts[-1] = S("sw", ij.addr, src=tmp, cases=sw_cases, t=cj.t)
        count += 1
    return count
