"""바이너리 로더: 파일을 읽어 BinaryModel로 정규화한다.

주 엔진은 lief(ELF/PE/Mach-O 공통). 가상 주소→바이트 매핑도 lief에 맡긴다.
"""
from __future__ import annotations

import hashlib
import os

import lief

from .model import BinaryModel, Section, Symbol, Function


# ---- 아키텍처 판별 -------------------------------------------------------

def _canon_arch(name: str, bits: int) -> str:
    n = name.upper()
    if "X86_64" in n or "AMD64" in n or n == "EM_X86_64":
        return "x64"
    if "AARCH64" in n or "ARM64" in n:
        return "arm64"
    if n in ("I386", "X86", "INTEL") or "386" in n or "IA32" in n:
        return "x86"
    if "ARM" in n:
        return "arm64" if bits == 64 else "arm"
    if "MIPS" in n:
        return "mips"
    if "PPC" in n or "POWERPC" in n:
        return "ppc"
    if "RISCV" in n or "RISC-V" in n:
        return "riscv"
    # bits만으로 x86 계열 추정
    if bits == 64:
        return "x64"
    if bits == 32:
        return "x86"
    return "unknown"


def _detect_elf(b) -> tuple[str, int, str]:
    h = b.header
    cls = str(h.identity_class)
    bits = 64 if "64" in cls else 32
    endian = "little" if "LSB" in str(h.identity_data) else "big"
    mt = str(h.machine_type).split(".")[-1]
    return _canon_arch(mt, bits), bits, endian


def _detect_pe(b) -> tuple[str, int, str]:
    machine = str(getattr(b.header, "machine", "")).split(".")[-1]
    bits = 32
    try:
        # PE32+ 판별
        magic = str(b.optional_header.magic)
        if "PE32_PLUS" in magic or "x64" in machine.lower() or "AMD64" in machine.upper():
            bits = 64
    except Exception:
        if "AMD64" in machine.upper() or "ARM64" in machine.upper():
            bits = 64
    if "ARM64" in machine.upper() or "AARCH64" in machine.upper():
        arch, bits = "arm64", 64
    elif "AMD64" in machine.upper() or "X64" in machine.upper():
        arch, bits = "x64", 64
    elif "ARMNT" in machine.upper() or "ARM" in machine.upper():
        arch, bits = "arm", 32
    elif "I386" in machine.upper() or "386" in machine.upper():
        arch, bits = "x86", 32
    else:
        arch = _canon_arch(machine, bits)
    return arch, bits, "little"


def _detect_macho(b) -> tuple[str, int, str]:
    cpu = str(getattr(b.header, "cpu_type", "")).split(".")[-1]
    bits = 64 if ("64" in cpu or "X86_64" in cpu or "ARM64" in cpu) else 32
    return _canon_arch(cpu, bits), bits, "little"


# ---- 섹션 권한 ----------------------------------------------------------

def _elf_section_perms(s) -> tuple[bool, bool, bool]:
    # (r, w, x)
    try:
        flags = s.flags
        FL = lief.ELF.Section.FLAGS
        x = bool(flags & int(FL.EXECINSTR))
        w = bool(flags & int(FL.WRITE))
        return True, w, x
    except Exception:
        return True, False, s.name == ".text"


def _pe_section_perms(s) -> tuple[bool, bool, bool]:
    try:
        ch = s.characteristics
        CH = lief.PE.Section.CHARACTERISTICS
        r = bool(ch & int(CH.MEM_READ))
        w = bool(ch & int(CH.MEM_WRITE))
        x = bool(ch & int(CH.MEM_EXECUTE)) or bool(ch & int(CH.CNT_CODE))
        return r or True, w, x
    except Exception:
        return True, False, s.name in (".text", "CODE")


def _macho_section_perms(s) -> tuple[bool, bool, bool]:
    name = (s.name or "").lower()
    return True, False, "text" in name or "stub" in name


# ---- 메인 로더 ----------------------------------------------------------

class LoadError(Exception):
    pass


def load(path: str) -> tuple[BinaryModel, bytes, object]:
    """path를 로드해 (BinaryModel, raw_bytes, lief_binary)를 돌려준다."""
    if not os.path.isfile(path):
        raise LoadError(f"파일이 없습니다: {path}")

    with open(path, "rb") as fh:
        raw = fh.read()

    sha = hashlib.sha256(raw).hexdigest()

    b = lief.parse(path)
    if b is None:
        raise LoadError("이 파일을 바이너리로 해석하지 못했습니다 (ELF/PE/Mach-O 아님).")

    fmt = str(b.format).split(".")[-1]  # ELF / PE / MACHO

    if fmt == "ELF":
        arch, bits, endian = _detect_elf(b)
        perms_fn = _elf_section_perms
    elif fmt == "PE":
        arch, bits, endian = _detect_pe(b)
        perms_fn = _pe_section_perms
    elif fmt in ("MACHO", "MACH_O"):
        fmt = "MACHO"
        arch, bits, endian = _detect_macho(b)
        perms_fn = _macho_section_perms
    else:
        arch, bits, endian = _canon_arch("", 0), 0, "little"
        perms_fn = lambda s: (True, False, False)

    image_base = int(getattr(b, "imagebase", 0) or 0)

    model = BinaryModel(
        path=path,
        name=os.path.basename(path),
        sha256=sha,
        size=len(raw),
        fmt=fmt,
        arch=arch,
        bits=bits,
        endian=endian,
        entrypoint=int(b.entrypoint),
        image_base=image_base,
    )

    # 섹션
    for s in b.sections:
        try:
            va = int(s.virtual_address)
        except Exception:
            va = 0
        r, w, x = perms_fn(s)
        model.sections.append(Section(
            name=s.name or "",
            vaddr=va,
            size=int(getattr(s, "size", 0) or getattr(s, "virtual_size", 0) or 0),
            offset=int(getattr(s, "offset", 0) or 0),
            is_code=x,
            readable=r, writable=w, executable=x,
        ))

    # 함수 시드: lief abstract functions (심볼/eh_frame/exports 기반)
    seen = set()
    for f in b.functions:
        addr = int(getattr(f, "address", 0) or 0)
        if addr == 0 or addr in seen:
            continue
        seen.add(addr)
        model.functions.append(Function(
            vaddr=addr,
            name=f.name or "",
            size=int(getattr(f, "size", 0) or 0),
        ))

    # 심볼
    for s in b.symbols:
        try:
            val = int(getattr(s, "value", 0) or 0)
        except Exception:
            val = 0
        name = s.name or ""
        if not name:
            continue
        is_imp = (val == 0)
        model.symbols.append(Symbol(
            name=name, vaddr=val, size=int(getattr(s, "size", 0) or 0),
            kind=str(getattr(s, "type", "")).split(".")[-1].lower(),
            is_import=is_imp,
        ))

    # 임포트 / 익스포트 (포맷별)
    _collect_imports_exports(b, fmt, model)

    # 익스포트를 함수 시드로 보강
    export_addrs = {e.vaddr for e in model.exports if e.vaddr}
    func_addrs = {f.vaddr for f in model.functions}
    for e in model.exports:
        if e.vaddr and e.vaddr not in func_addrs:
            model.functions.append(Function(vaddr=e.vaddr, name=e.name, is_export=True))
            func_addrs.add(e.vaddr)

    # 엔트리포인트도 함수로
    if model.entrypoint and model.entrypoint not in func_addrs:
        model.functions.append(Function(vaddr=model.entrypoint, name="entry", size=0))

    for f in model.functions:
        if f.vaddr in export_addrs:
            f.is_export = True

    model.functions.sort(key=lambda f: f.vaddr)
    model.symbols.sort(key=lambda s: s.vaddr)

    return model, raw, b


def _collect_imports_exports(b, fmt: str, model: BinaryModel) -> None:
    if fmt == "ELF":
        try:
            for f in b.imported_functions:
                model.imports.append(Symbol(name=f.name, vaddr=int(getattr(f, "value", 0) or 0),
                                            kind="func", is_import=True))
        except Exception:
            pass
        try:
            for f in b.exported_functions:
                model.exports.append(Symbol(name=f.name, vaddr=int(getattr(f, "address", 0) or 0),
                                            kind="func"))
        except Exception:
            pass
    elif fmt == "PE":
        try:
            for imp in b.imports:
                lib = imp.name or ""
                for e in imp.entries:
                    model.imports.append(Symbol(
                        name=e.name or f"ordinal_{getattr(e,'ordinal','?')}",
                        vaddr=int(getattr(e, "iat_address", 0) or 0),
                        kind="func", is_import=True, library=lib))
        except Exception:
            pass
        try:
            if b.has_exports:
                base = model.image_base
                for e in b.get_export().entries:
                    model.exports.append(Symbol(
                        name=e.name or f"ordinal_{e.ordinal}",
                        vaddr=base + int(getattr(e, "address", 0) or 0),
                        kind="func"))
        except Exception:
            pass
    elif fmt == "MACHO":
        try:
            for f in b.imported_functions:
                model.imports.append(Symbol(name=f.name, vaddr=0, kind="func", is_import=True))
        except Exception:
            pass
        try:
            for f in b.exported_functions:
                model.exports.append(Symbol(name=f.name, vaddr=int(getattr(f, "address", 0) or 0),
                                            kind="func"))
        except Exception:
            pass
