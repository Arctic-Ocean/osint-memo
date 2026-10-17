"""Capstone 기반 디스어셈블러.

lief 바이너리에서 VA의 바이트를 읽어 명령어로 해석하고, 분기/호출/데이터
참조 대상을 함께 뽑아낸다(상호참조·CFG 분석의 재료).
"""
from __future__ import annotations

from capstone import (
    Cs, CS_ARCH_X86, CS_ARCH_ARM, CS_ARCH_ARM64, CS_ARCH_MIPS, CS_ARCH_PPC,
    CS_MODE_32, CS_MODE_64, CS_MODE_ARM, CS_MODE_LITTLE_ENDIAN,
    CS_MODE_BIG_ENDIAN, CS_MODE_MIPS32, CS_MODE_MIPS64,
    CS_GRP_JUMP, CS_GRP_CALL, CS_GRP_RET, CS_GRP_INT, CS_GRP_IRET,
    CS_OP_IMM, CS_OP_MEM,
)
from capstone.x86 import X86_OP_MEM, X86_OP_IMM, X86_REG_RIP

_ARCH = {
    "x64": (CS_ARCH_X86, CS_MODE_64),
    "x86": (CS_ARCH_X86, CS_MODE_32),
    "arm64": (CS_ARCH_ARM64, CS_MODE_ARM),
    "arm": (CS_ARCH_ARM, CS_MODE_ARM),
    "mips": (CS_ARCH_MIPS, CS_MODE_MIPS32),
    "ppc": (CS_ARCH_PPC, CS_MODE_32),
}


class Disassembler:
    def __init__(self, arch: str, bits: int, endian: str, lief_bin):
        if arch not in _ARCH:
            raise ValueError(f"지원하지 않는 아키텍처: {arch}")
        cs_arch, cs_mode = _ARCH[arch]
        if arch == "mips" and bits == 64:
            cs_mode = CS_MODE_MIPS64
        cs_mode |= CS_MODE_BIG_ENDIAN if endian == "big" else CS_MODE_LITTLE_ENDIAN
        self.arch = arch
        self.bits = bits
        self.cs = Cs(cs_arch, cs_mode)
        self.cs.detail = True
        self.cs.skipdata = True
        self._bin = lief_bin

    # VA의 바이트 읽기 (lief가 섹션/세그먼트 매핑 처리)
    def read(self, va: int, size: int) -> bytes:
        try:
            data = self._bin.get_content_from_virtual_address(va, size)
            return bytes(data)
        except Exception:
            return b""

    def disasm(self, va: int, size: int, max_insns: int = 0):
        """va부터 size 바이트를 디스어셈블. 명령어 dict의 리스트를 돌려준다."""
        code = self.read(va, size)
        if not code:
            return []
        out = []
        for insn in self.cs.disasm(code, va):
            out.append(self._describe(insn))
            if max_insns and len(out) >= max_insns:
                break
        return out

    def _describe(self, insn) -> dict:
        refs = self._refs(insn)
        is_ret = self._has_grp(insn, CS_GRP_RET) or self._has_grp(insn, CS_GRP_IRET)
        is_call = self._has_grp(insn, CS_GRP_CALL)
        is_jump = self._has_grp(insn, CS_GRP_JUMP)
        flow = "normal"
        if is_ret:
            flow = "ret"
        elif is_call:
            flow = "call"
        elif is_jump:
            # 무조건 분기 여부(mnemonic 기준, x86)
            flow = "jmp" if insn.mnemonic in ("jmp", "b", "bx", "br") else "cjmp"
        return {
            "addr": insn.address,
            "size": insn.size,
            "bytes": insn.bytes.hex(),
            "mnemonic": insn.mnemonic,
            "op_str": insn.op_str,
            "flow": flow,
            "refs": refs,            # [{target, kind}]
        }

    def _has_grp(self, insn, grp) -> bool:
        try:
            return insn.group(grp)
        except Exception:
            return False

    def _refs(self, insn) -> list:
        """명령어가 가리키는 코드/데이터 주소를 뽑는다."""
        refs = []
        try:
            is_call = self._has_grp(insn, CS_GRP_CALL)
            is_jump = self._has_grp(insn, CS_GRP_JUMP)
            for op in insn.operands:
                if op.type == CS_OP_IMM and (is_call or is_jump):
                    kind = "call" if is_call else "jump"
                    refs.append({"target": op.imm & 0xFFFFFFFFFFFFFFFF, "kind": kind})
                elif self.arch in ("x86", "x64") and op.type == X86_OP_MEM:
                    # RIP 상대 (x64) → 데이터 주소 계산
                    if getattr(op.mem, "base", 0) == X86_REG_RIP:
                        tgt = insn.address + insn.size + op.mem.disp
                        kind = "call" if is_call else ("jump" if is_jump else "data")
                        refs.append({"target": tgt & 0xFFFFFFFFFFFFFFFF, "kind": kind})
        except Exception:
            pass
        return refs
