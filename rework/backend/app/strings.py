"""문자열 추출: raw 바이트에서 ASCII / UTF-16LE 문자열을 뽑고 VA로 매핑."""
from __future__ import annotations

import re

from .model import BinaryModel, StringItem

_ASCII = rb"[\x20-\x7e]{%d,}"
_UTF16 = rb"(?:[\x20-\x7e]\x00){%d,}"


def _offset_to_va(model: BinaryModel, off: int) -> int:
    for s in model.sections:
        if s.offset and s.offset <= off < s.offset + s.size:
            return s.vaddr + (off - s.offset)
    return 0


def extract(raw: bytes, model: BinaryModel, min_len: int = 4, limit: int = 50000) -> list:
    out: list[StringItem] = []
    for m in re.finditer(_ASCII % min_len, raw):
        try:
            val = m.group().decode("ascii")
        except Exception:
            continue
        out.append(StringItem(vaddr=_offset_to_va(model, m.start()),
                              offset=m.start(), value=val, encoding="ascii"))
        if len(out) >= limit:
            break
    for m in re.finditer(_UTF16 % min_len, raw):
        try:
            val = m.group().decode("utf-16-le")
        except Exception:
            continue
        out.append(StringItem(vaddr=_offset_to_va(model, m.start()),
                              offset=m.start(), value=val, encoding="utf-16"))
        if len(out) >= limit:
            break
    out.sort(key=lambda s: (s.vaddr == 0, s.vaddr, s.offset))
    return out
