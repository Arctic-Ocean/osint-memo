"""NEON 리프터(ARM64·ARM32)가 함께 쓰는, 레지스터 이름과 무관한 벡터 식 만들기.

모든 함수는 64비트 한 조각(8바이트 식)들을 받아 식을 돌려준다. 128비트 벡터는 (하위, 상위) 두 조각이고, 원소별 연산은 조각마다 한 번씩 적용한다.
원소 폭 esz(바이트)가 8 이면 조각 하나가 원소 하나라서 평범한 64비트 정수·실수 연산이다.
cls: 's' 부호 있는 정수 · 'u' 부호 없는 정수 · 'f' 실수.
"""
from __future__ import annotations

import struct

from . import ir
from .ir import C, X, binop, unop, zext, sext, trunc, cmp, ite, vop, vlane, vpack, vdup, vshuf


class NeonUnsupported(Exception):
    pass


FLT_FN = {"fadd": "fadd", "fsub": "fsub", "fmul": "fmul", "fdiv": "fdiv", "fmax": "fcmax", "fmin": "fcmin", "fmaxnm": "fcmax", "fminnm": "fcmin"}


def cmp_op(nm: str, cls: str, esz: int, x: X, y: X) -> X:
    """원소별 비교(결과는 원소 전체가 1 인 마스크)."""
    if esz == 8:
        c = {"ceq": "eq", "cgt": "sgt" if cls == "s" else "ugt", "cge": "sge" if cls == "s" else "uge"}.get(nm)
        if c is None:                                                                      # tst
            return unop("neg", zext(cmp("ne", binop("and", x, y, 8), C(0, 8)), 8))
        return unop("neg", zext(cmp(c, x, y), 8))
    return vop(nm, (x, y), esz, cls)


def un_op(nm: str, cls: str, esz: int, x: X) -> X:
    if esz == 8:
        if nm == "neg":
            return unop("neg", x)
        if nm == "abs":
            return ite(cmp("slt", x, C(0, 8)), unop("neg", x), x)
        raise NeonUnsupported(nm)
    return vop(nm, (x,), esz, cls)


def fun_op(nm: str, esz: int, x: X) -> X:
    if esz == 8:
        if nm in ("fneg", "fabs", "fsqrt"):
            return X(nm, (x,), 8)
        mode = {"frintn": "rint", "frintm": "floor", "frintp": "ceil", "frintz": "trunc", "frinta": "round", "frintx": "rint"}.get(nm)
        if mode is None:
            raise NeonUnsupported(nm)
        return X("fround", (x,), 8, mode)
    return vop(nm, (x,), esz, "f")


def bin_op(nm: str, cls: str, esz: int, x: X, y: X, flt: bool) -> X:
    """원소별 이항 연산 한 조각."""
    if esz == 8:
        if not flt and nm in ("add", "sub"):
            return binop(nm, x, y, 8)
        if flt and nm in FLT_FN:
            return X(FLT_FN[nm], (x, y), 8)
        if flt and nm == "fabd":
            return X("fabs", (X("fsub", (x, y), 8),), 8)
        if flt and nm in ("facgt", "facge"):
            c = "fgt" if nm == "facgt" else "fge"
            return unop("neg", zext(X(c, (X("fabs", (x,), 8), X("fabs", (y,), 8)), 1), 8))
        if not flt and nm in ("ceq", "cgt", "cge", "tst"):
            return cmp_op(nm, cls, esz, x, y)
        if not flt and nm in ("max", "min"):
            c = {("max", "s"): "sgt", ("max", "u"): "ugt", ("min", "s"): "slt", ("min", "u"): "ult"}[(nm, cls)]
            return ite(cmp(c, x, y), x, y)
        if flt and nm in ("fceq", "fcgt", "fcge"):
            c = {"fceq": "feq", "fcgt": "fgt", "fcge": "fge"}[nm]
            return unop("neg", zext(X(c, (x, y), 1), 8))
        raise NeonUnsupported(nm)
    if flt and esz != 4:
        raise NeonUnsupported(nm)
    return vop(nm, (x, y), esz, "f" if flt else cls)


def mla_op(nm: str, esz: int, flt: bool, acc: X, x: X, y: X) -> X:
    """nm: mla·mls(정수) · fmla·fmls(실수, 한 번만 반올림하는 융합)."""
    if esz == 8:
        if not flt:
            raise NeonUnsupported(nm)
        p = x if nm == "fmla" else X("fneg", (x,), 8)
        return X("ffma", (p, y, acc), 8)
    return vop(nm, (acc, x, y), esz, "f" if flt else "s")


def mla_nofuse_op(nm: str, esz: int, acc: X, x: X, y: X) -> X:
    """ARM32 의 vmla.f32·vmls.f32(곱을 한 번 반올림하고 더한다 — 융합이 아님)."""
    if esz == 8:
        p = X("fmul", (x, y), 8)
        return X("fadd" if nm == "mla" else "fsub", (acc, p), 8)
    p = vop("fmul", (x, y), esz, "f")
    return vop("fadd" if nm == "mla" else "fsub", (acc, p), esz, "f")


def shift_op(kind: str, n: int, esz: int, cls: str, x: X, acc: X | None = None) -> X:
    """kind: shl · shr · sra (오른쪽 시프트 뒤 누적). cls 's'/'u' 가 산술/논리 시프트를 정한다."""
    if esz == 8:
        if kind == "shl":
            return binop("shl", x, C(n, 1), 8)
        if n >= 64:
            n = 63 if cls == "s" else 64
        sh = binop("shr" if cls == "u" else "sar", x, C(n, 1), 8)
        return binop("add", acc, sh, 8) if kind == "sra" else sh
    if kind == "shl":
        return vop("shl_n", (x, C(n, 1)), esz, "s")
    if kind == "sra":
        return vop("sra_n", (acc, x, C(n, 1)), esz, cls)
    return vop("shr_n", (x, C(n, 1)), esz, cls)


def imm_pattern(v: int, esz: int) -> int:
    """즉값 v(폭 esz)를 모든 원소에 되풀이한 64비트 값."""
    v &= ir.mask(esz)
    return sum(v << (8 * esz * i) for i in range(8 // esz))


def fp_imm_bits(fp: float, esz: int) -> int:
    bits = struct.unpack("<Q", struct.pack("<d", fp))[0] if esz == 8 else struct.unpack("<I", struct.pack("<f", fp))[0]
    return bits if esz == 8 else (bits | bits << 32)


# ---- 원소 재배치 -----------------------------------------------------------------------
def zip_indices(m: str, total: int, esz: int) -> list:
    """zip1/2·uzp1/2·trn1/2 의 결과 원소가 입력 (x 전체 + y 전체) 이어붙임에서 가져오는 번호들. x 는 0..n-1, y 는 n..2n-1."""
    n = total // esz
    if m.startswith("zip"):
        base = 0 if m == "zip1" else n // 2
        out = []
        for j in range(n // 2):
            out += [base + j, n + base + j]
        return out
    off = 0 if m.endswith("1") else 1
    if m.startswith("uzp"):
        return [2 * j + off for j in range(n)]
    out = []
    for j in range(n // 2):
        out += [2 * j + off, n + 2 * j + off]
    return out


def gather_pieces(pieces: dict, srcs: list, esz: int) -> X:
    """srcs: [((조각 키), 조각 안 원소 번호)] → 벡터 한 조각. 입력 조각이 둘 이하면 shufflevector, 더 많으면 원소를 모아 묶는다."""
    per = 8 // esz
    keys = []
    for k, _ in srcs:
        if k not in keys:
            keys.append(k)
    if esz == 8:
        return pieces[srcs[0][0]]
    if len(keys) <= 2:
        second = keys[1] if len(keys) > 1 else keys[0]
        return vshuf(pieces[keys[0]], pieces[second], [keys.index(k) * per + l for k, l in srcs], esz, "s")
    return vpack([vlane(pieces[k], l, esz, "s") for k, l in srcs], esz, "s")


def perm_halves(idx_full: list, xs: list, ys: list, esz: int, total: int) -> list:
    """전체 원소 번호표(x 전체 뒤에 y 전체)대로 섞은 결과 조각들. xs·ys 는 입력의 (하위, 상위) 조각 목록."""
    n = total // esz
    per = 8 // esz
    pieces = {}
    for h, p in enumerate(xs):
        pieces[(0, h)] = p
    for h, p in enumerate(ys):
        pieces[(1, h)] = p
    outs = []
    for h in range(2 if total == 16 else 1):
        srcs = []
        for i in idx_full[h * per:(h + 1) * per]:
            side = 0 if i < n else 1
            j = i % n
            srcs.append(((side, j // per), j % per))
        outs.append(gather_pieces(pieces, srcs, esz))
    return outs


def ext_halves(k: int, xs: list, ys: list, total: int) -> list:
    """ext #k(바이트): 두 벡터를 이어 붙인 것에서 k 바이트 뒤부터 total 바이트."""
    pieces = {}
    seq = list(xs) + list(ys)
    for i, p in enumerate(seq):
        pieces[i] = p
    outs = []
    for h in range(2 if total == 16 else 1):
        srcs = []
        for j in range(8):
            b = k + h * 8 + j
            srcs.append((b // 8, b % 8))
        outs.append(gather_pieces(pieces, srcs, 1))
    return outs


def rev_halves(blk: int, esz: int, halves: list) -> list:
    """rev16/32/64: 블록(blk 바이트) 안에서 원소 순서를 뒤집는다."""
    per = 8 // esz
    per_blk = blk // esz
    idx = tuple(j - j % per_blk + (per_blk - 1 - j % per_blk) for j in range(per))
    return [vshuf(h, h, idx, esz, "s") for h in halves]
