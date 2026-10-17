"""분석 엔진.

Program: 로드된 바이너리 하나를 둘러싼 중심 객체.
모델 + raw 바이트 + lief 핸들 + 디스어셈블러 + 사용자 주석(project)을
묶고, 상호참조/문자열/함수경계 같은 파생 정보를 계산·캐시한다.
"""
from __future__ import annotations

from .model import BinaryModel, Function
from .disasm import Disassembler
from . import strings as strings_mod


# 함수 경계를 모를 때 선형 해석을 멈추는 상한(바이트)
_MAX_FUNC_SCAN = 0x4000
# xref 인덱스 구축 시 전체 명령어 상한(병적인 대형 바이너리 방지)
_MAX_XREF_INSNS = 3_000_000


class Program:
    def __init__(self, model: BinaryModel, raw: bytes, lief_bin, project=None):
        self.model = model
        self.raw = raw
        self.lief = lief_bin
        self.project = project
        self.dis = Disassembler(model.arch, model.bits, model.endian, lief_bin)

        # 인덱스
        self._func_by_addr = {f.vaddr: f for f in model.functions}
        self._sym_by_addr = {}
        for s in model.symbols:
            if s.vaddr and s.vaddr not in self._sym_by_addr:
                self._sym_by_addr[s.vaddr] = s
        self._import_by_addr = {i.vaddr: i for i in model.imports if i.vaddr}

        self.strings = strings_mod.extract(raw, model)
        self._str_by_addr = {s.vaddr: s for s in self.strings if s.vaddr}

        # xref: target_va -> list[{from, kind, func}]
        self.xref_to: dict[int, list] = {}
        self._func_size_cache: dict[int, int] = {}
        self._built = False

        # PLT 스텁 → 임포트 이름 해석 (printf/puts/strcmp 등)
        self.plt_names: dict[int, str] = {}
        self._build_plt()

    # ---- PLT 해석 -------------------------------------------------------
    def _build_plt(self):
        """GOT 슬롯 주소 → 심볼 이름을 구하고, 그 슬롯으로 점프하는
        작은 스텁(=.plt 엔트리)을 해당 임포트 이름으로 매핑한다."""
        if self.model.arch not in ("x86", "x64"):
            return
        got_to_name: dict[int, str] = {}
        try:
            for r in self.lief.relocations:
                try:
                    if r.has_symbol and r.symbol and r.symbol.name:
                        got_to_name[int(r.address)] = r.symbol.name
                except Exception:
                    continue
        except Exception:
            return
        if not got_to_name:
            return
        for f in self.model.functions:
            if f.name:                       # 이미 이름이 있으면 건드리지 않음
                continue
            insns = self.dis.disasm(f.vaddr, 24, max_insns=4)
            for ins in insns:
                if ins["flow"] in ("jmp", "cjmp"):
                    for ref in ins["refs"]:
                        nm = got_to_name.get(ref["target"])
                        if nm:
                            self.plt_names[f.vaddr] = nm
                            break
                if f.vaddr in self.plt_names:
                    break

    # ---- 함수 경계 ------------------------------------------------------

    def _next_func_addr(self, addr: int) -> int:
        nxt = None
        for a in self._func_by_addr:
            if a > addr and (nxt is None or a < nxt):
                nxt = a
        return nxt if nxt is not None else 0

    def function_size(self, f: Function) -> int:
        if f.vaddr in self._func_size_cache:
            return self._func_size_cache[f.vaddr]
        if f.size > 0:
            self._func_size_cache[f.vaddr] = f.size
            return f.size
        # 크기 미상 → 선형 해석으로 ret까지, 또는 다음 함수 시작까지
        nxt = self._next_func_addr(f.vaddr)
        cap = _MAX_FUNC_SCAN
        if nxt and 0 < nxt - f.vaddr < cap:
            cap = nxt - f.vaddr
        insns = self.dis.disasm(f.vaddr, cap)
        size = 0
        for ins in insns:
            size = ins["addr"] + ins["size"] - f.vaddr
            if ins["flow"] == "ret" and nxt == 0:
                break
            if nxt and ins["addr"] + ins["size"] >= nxt:
                break
        if size <= 0:
            size = 1
        self._func_size_cache[f.vaddr] = size
        return size

    # ---- xref 인덱스 ----------------------------------------------------

    def build_xrefs(self):
        if self._built:
            return
        total = 0
        for f in self.model.functions:
            if f.is_import:
                continue
            size = self.function_size(f)
            insns = self.dis.disasm(f.vaddr, size)
            total += len(insns)
            for ins in insns:
                for r in ins["refs"]:
                    tgt = r["target"]
                    self.xref_to.setdefault(tgt, []).append({
                        "from": ins["addr"], "kind": r["kind"], "func": f.vaddr,
                    })
            if total > _MAX_XREF_INSNS:
                break
        # 함수별 피참조 수 집계
        for f in self.model.functions:
            f.xref_to = len(self.xref_to.get(f.vaddr, []))
        self._built = True

    # ---- 이름 해석 ------------------------------------------------------

    def name_for(self, va: int) -> str | None:
        """주소를 표시용 이름으로. 사용자 리네임 > 함수 > 임포트 > 심볼."""
        if self.project:
            rn = self.project.get_name(va)
            if rn:
                return rn
        pn = self.plt_names.get(va)
        if pn:
            return pn
        f = self._func_by_addr.get(va)
        if f:
            return f.display_name
        im = self._import_by_addr.get(va)
        if im:
            return im.name
        sym = self._sym_by_addr.get(va)
        if sym:
            return sym.name
        return None

    def classify(self, va: int) -> dict:
        """주소에 무엇이 있는지 분류(네비게이션용)."""
        if va in self._func_by_addr:
            return {"type": "function", "addr": va, "name": self.name_for(va)}
        if va in self._str_by_addr:
            s = self._str_by_addr[va]
            return {"type": "string", "addr": va, "value": s.value}
        if va in self._import_by_addr:
            return {"type": "import", "addr": va, "name": self._import_by_addr[va].name}
        sec = self.model.section_for(va)
        if sec:
            return {"type": "code" if sec.is_code else "data", "addr": va, "section": sec.name}
        return {"type": "unknown", "addr": va}

    # ---- 함수 디스어셈블(화면용) ---------------------------------------

    def disassemble_function(self, addr: int) -> dict:
        f = self._func_by_addr.get(addr)
        if f is None:
            # 함수로 등록되지 않은 주소 → 임시 함수 취급
            f = Function(vaddr=addr, name="", size=0)
        size = self.function_size(f)
        raw_insns = self.dis.disasm(f.vaddr, size)
        self.build_xrefs()

        insns = []
        for ins in raw_insns:
            targets = []
            for r in ins["refs"]:
                nm = self.name_for(r["target"])
                st = self._str_by_addr.get(r["target"])
                targets.append({
                    "target": r["target"], "kind": r["kind"],
                    "name": nm, "string": st.value if st else None,
                })
            comment = self.project.get_comment(ins["addr"]) if self.project else None
            insns.append({**ins, "targets": targets, "comment": comment})

        return {
            "addr": f.vaddr,
            "name": self.name_for(f.vaddr) or f.display_name,
            "size": size,
            "xrefs": self.xrefs_to(f.vaddr),
            "instructions": insns,
        }

    def xrefs_to(self, va: int) -> list:
        self.build_xrefs()
        out = []
        for x in self.xref_to.get(va, []):
            out.append({
                "from": x["from"], "kind": x["kind"],
                "func": x["func"], "func_name": self.name_for(x["func"]),
            })
        return out

    # ---- 헥스 ----------------------------------------------------------

    def hexdump(self, va: int, length: int) -> dict:
        data = self.dis.read(va, length)
        return {"addr": va, "length": len(data), "bytes": data.hex()}
