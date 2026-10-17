"""정규화된 바이너리 모델.

lief/pefile 등 포맷별 라이브러리의 차이를 흡수해, 상위 계층(분석·API)이
포맷과 무관하게 다룰 수 있는 단순한 구조로 바꾼다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Section:
    name: str
    vaddr: int          # 가상 주소(VA). 0이면 매핑되지 않는 섹션.
    size: int           # 가상 크기
    offset: int         # 파일 오프셋
    is_code: bool = False
    readable: bool = True
    writable: bool = False
    executable: bool = False

    def contains(self, va: int) -> bool:
        return self.vaddr != 0 and self.vaddr <= va < self.vaddr + self.size


@dataclass
class Symbol:
    name: str
    vaddr: int
    size: int = 0
    kind: str = ""       # func / object / section / ...
    is_import: bool = False
    library: str = ""


@dataclass
class Function:
    vaddr: int
    name: str
    size: int = 0
    is_import: bool = False
    is_export: bool = False
    is_thunk: bool = False
    # 분석으로 채워지는 값들
    xref_to: int = 0     # 이 함수를 참조하는 곳의 수

    @property
    def display_name(self) -> str:
        return self.name or f"sub_{self.vaddr:x}"


@dataclass
class StringItem:
    vaddr: int
    offset: int
    value: str
    encoding: str = "ascii"   # ascii / utf-16


@dataclass
class BinaryModel:
    path: str
    name: str
    sha256: str
    size: int
    fmt: str             # ELF / PE / MACHO
    arch: str            # x86 / x64 / arm / arm64 / mips / unknown
    bits: int
    endian: str          # little / big
    entrypoint: int
    image_base: int
    sections: list = field(default_factory=list)      # list[Section]
    functions: list = field(default_factory=list)     # list[Function]
    symbols: list = field(default_factory=list)       # list[Symbol]
    imports: list = field(default_factory=list)        # list[Symbol]
    exports: list = field(default_factory=list)        # list[Symbol]

    def section_for(self, va: int) -> Optional[Section]:
        for s in self.sections:
            if s.contains(va):
                return s
        return None

    def summary(self) -> dict:
        code_sz = sum(s.size for s in self.sections if s.is_code)
        return {
            "path": self.path,
            "name": self.name,
            "sha256": self.sha256,
            "size": self.size,
            "fmt": self.fmt,
            "arch": self.arch,
            "bits": self.bits,
            "endian": self.endian,
            "entrypoint": self.entrypoint,
            "image_base": self.image_base,
            "counts": {
                "sections": len(self.sections),
                "functions": len(self.functions),
                "symbols": len(self.symbols),
                "imports": len(self.imports),
                "exports": len(self.exports),
                "code_size": code_sz,
            },
        }


def section_to_dict(s: Section) -> dict:
    d = asdict(s)
    return d


def function_to_dict(f: Function) -> dict:
    d = asdict(f)
    d["display_name"] = f.display_name
    return d
