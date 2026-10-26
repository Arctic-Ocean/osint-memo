"""휴리스틱 디컴파일러 — 의사코드(pseudo-C) 생성.

진짜 디컴파일러(Ghidra 등)처럼 완전한 타입·제어흐름 복원을 하지는 않는다.
대신 x86/x64 명령어를 사람이 읽기 쉬운 의사코드로 끌어올리고(lifting),
기본블록/CFG를 구성해 '단순한 if / 반복'만 구조화한다. 나머지는 항상
옳은 `goto 라벨;` 형태로 떨어뜨려 결과가 틀리지 않게 한다.

목표: 분석가가 함수의 큰 흐름과 호출·문자열·조건을 빠르게 파악하도록 돕는 것.
세밀한 복원은 디스어셈블리 뷰와 병행해서 본다.
"""
from __future__ import annotations

import bisect
import re

# ---- 산술/논리 2항 연산 매핑 (Intel mnemonic -> C 연산자) ----------------
MAX_DECOMPILE_INSNS = 20000       # 이보다 큰 함수는 의사코드를 만들지 않는다(시간이 함수 크기에 따라 급격히 늘어난다)

_BINOP = {
    "add": "+", "sub": "-", "and": "&", "or": "|", "xor": "^",
    "shl": "<<", "sal": "<<", "shr": ">>", "sar": ">>",
    "imul": "*", "mul": "*",
}

# cmp/test 뒤에 오는 조건분기(jcc) -> C 비교 연산자
#   서명/부호 없음 구분은 주석으로만 남기고 연산자는 단순화한다.
_CC = {
    "e": "==", "z": "==", "ne": "!=", "nz": "!=",
    "g": ">", "nle": ">", "ge": ">=", "nl": ">=",
    "l": "<", "nge": "<", "le": "<=", "ng": "<=",
    "a": ">", "nbe": ">", "ae": ">=", "nb": ">=",
    "b": "<", "nae": "<", "be": "<=", "na": "<=",
    "s": "< 0", "ns": ">= 0",
}

# x64 System V 인자 레지스터 패밀리 (호출 인자 추정용)
_ARGREGS = [
    ("rdi", "edi", "di", "dil"),
    ("rsi", "esi", "si", "sil"),
    ("rdx", "edx", "dx", "dl"),
    ("rcx", "ecx", "cx", "cl"),
    ("r8", "r8d", "r8w", "r8b"),
    ("r9", "r9d", "r9w", "r9b"),
]
_ARG_CANON = {}
for _i, _fam in enumerate(_ARGREGS):
    for _r in _fam:
        _ARG_CANON[_r] = _i

_RET_REG = {64: "rax", 32: "eax"}

_PROLOGUE_SKIP = {"push", "pop", "leave", "nop", "endbr64", "endbr32", "hlt",
                  "fnop", "ud2"}


def _split_ops(op_str: str):
    """op_str를 최상위 콤마 기준으로 피연산자로 나눈다([..] 안의 콤마 보호)."""
    out, depth, cur = [], 0, ""
    for ch in op_str:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


class _LegacyDecompiler:
    def __init__(self, program):
        self.prog = program
        self.bits = program.model.bits
        self._varmap: dict[str, str] = {}
        self._slots: set[str] = set()

    # ---- 공개 진입점 ----------------------------------------------------
    def decompile(self, addr: int) -> dict:
        fn = self.prog.disassemble_function(addr)
        insns = fn["instructions"]
        name = fn["name"]

        # 함수-지역 변수 리네임 맵 로드
        self._varmap = {}
        self._slots = set()
        if self.prog.project:
            self._varmap = self.prog.project.get_varnames(addr)

        if self.prog.model.arch not in ("x86", "x64"):
            # 리프팅은 x86 계열만. 그 외는 디스어셈블리를 의사코드 자리에 안내.
            lines = [{"addr": addr, "indent": 0,
                      "text": f"/* {self.prog.model.arch} 의사코드는 아직 미지원 — 디스어셈블리 보기를 쓰세요 */",
                      "kind": "comment"}]
            return {"addr": addr, "name": name, "signature": f"{name}()",
                    "lines": lines, "note": "x86/x64만 의사코드를 생성합니다."}

        if len(insns) > MAX_DECOMPILE_INSNS:
            # 기본블록 계획·방출은 함수 크기에 따라 급격히 느려진다: 병적으로 큰 함수는 의사코드를 만들지 않고 알린다
            lines = [{"addr": addr, "indent": 0, "kind": "comment",
                      "text": f"/* 함수가 너무 커서({len(insns)}개 명령어 > {MAX_DECOMPILE_INSNS}) 의사코드를 만들지 않습니다 — 디스어셈블리 보기를 쓰세요 */"}]
            return {"addr": addr, "name": name, "signature": f"{name}()", "lines": lines, "variables": [],
                    "note": "너무 큰 함수라 의사코드를 생략했습니다."}
        blocks = self._basic_blocks(insns)
        lines = self._emit(name, blocks, insns)
        variables = [{"slot": s, "name": self._varmap.get(s, s)}
                     for s in sorted(self._slots)]
        return {
            "addr": addr,
            "name": name,
            "signature": self._signature(name, blocks),
            "lines": lines,
            "variables": variables,
            "note": "휴리스틱 의사코드입니다. 정확한 의미는 디스어셈블리로 확인하세요.",
        }

    # ---- 기본블록 / CFG -------------------------------------------------
    def _basic_blocks(self, insns):
        if not insns:
            return []
        addrs = [i["addr"] for i in insns]
        addr_set = set(addrs)
        by_addr = {i["addr"]: i for i in insns}
        lo, hi = addrs[0], addrs[-1] + insns[-1]["size"]

        leaders = {addrs[0]}
        for ins in insns:
            flow = ins["flow"]
            nxt = ins["addr"] + ins["size"]
            if flow in ("jmp", "cjmp"):
                for t in ins["targets"]:
                    if t["kind"] in ("jump",) and lo <= t["target"] < hi:
                        leaders.add(t["target"])
                if nxt in addr_set:
                    leaders.add(nxt)        # cjmp의 fallthrough
            elif flow == "ret":
                if nxt in addr_set:
                    leaders.add(nxt)
        leaders = sorted(a for a in leaders if a in addr_set)

        blocks = []
        for bi, start in enumerate(leaders):
            end = leaders[bi + 1] if bi + 1 < len(leaders) else hi
            body = insns[bisect.bisect_left(addrs, start):bisect.bisect_left(addrs, end)]
            if not body:
                continue
            last = body[-1]
            succ = []
            jmp_t = None
            for t in last["targets"]:
                if t["kind"] in ("jump",) and t["target"] in addr_set:
                    jmp_t = t["target"]
            nxt = last["addr"] + last["size"]
            if last["flow"] == "ret":
                pass
            elif last["flow"] == "jmp":
                if jmp_t is not None:
                    succ.append(("jmp", jmp_t))
            elif last["flow"] == "cjmp":
                if jmp_t is not None:
                    succ.append(("taken", jmp_t))
                if nxt in addr_set:
                    succ.append(("fall", nxt))
            else:
                if nxt in addr_set:
                    succ.append(("fall", nxt))
            blocks.append({
                "start": start, "end": end, "body": body,
                "succ": succ, "last": last,
            })

        # 인바운드 edge 수 집계
        inbound = {b["start"]: 0 for b in blocks}
        for b in blocks:
            for _, tgt in b["succ"]:
                if tgt in inbound:
                    inbound[tgt] += 1
        for b in blocks:
            b["inbound"] = inbound[b["start"]]
        return blocks

    # ---- 의사코드 방출 --------------------------------------------------
    def _emit(self, name, blocks, insns):
        lines = []
        by_start = {b["start"]: idx for idx, b in enumerate(blocks)}
        used_labels = set()

        # 1차: 어떤 라벨이 실제 goto로 쓰일지 결정(단순 if 접기 후 남는 것)
        plan = self._plan(blocks, by_start)
        for step in plan:
            if step[0] == "label_use":
                used_labels.add(step[1])

        def add(addr, indent, text, kind="stmt"):
            lines.append({"addr": addr, "indent": indent, "text": text, "kind": kind})

        add(blocks[0]["start"] if blocks else 0, 0,
            f"{self._signature(name, blocks)}", "sig")
        add(blocks[0]["start"] if blocks else 0, 0, "{", "brace")

        for step in plan:
            kind = step[0]
            if kind == "label":
                b = step[1]
                if b["start"] in used_labels:
                    add(b["start"], 0, f"L_{b['start']:x}:", "label")
                for stmt in self._lift_block(b):
                    add(stmt[0], 1 + stmt[2], stmt[1], stmt[3])
            elif kind == "if_block":
                b, cond, inner, indent = step[1], step[2], step[3], step[4]
                add(b["last"]["addr"], 1, f"if ({cond}) {{", "ctrl")
                for ib in inner:
                    for stmt in self._lift_block(ib):
                        add(stmt[0], 2 + stmt[2], stmt[1], stmt[3])
                add(b["last"]["addr"], 1, "}", "ctrl")
            elif kind == "goto":
                add(step[1], 1, f"goto L_{step[2]:x};", "goto")
            elif kind == "label_use":
                pass
        add(0, 0, "}", "brace")
        return lines

    def _plan(self, blocks, by_start):
        """블록 순회 계획. 단순 전방 if(한 블록 스킵)만 접고 나머지는 goto."""
        plan = []
        i = 0
        n = len(blocks)
        consumed = set()
        while i < n:
            b = blocks[i]
            if b["start"] in consumed:
                i += 1
                continue
            last = b["last"]
            collapsed = False
            if last["flow"] == "cjmp" and len(b["succ"]) == 2:
                taken = next((t for k, t in b["succ"] if k == "taken"), None)
                fall = next((t for k, t in b["succ"] if k == "fall"), None)
                # 전방 분기 + 스킵되는 블록이 바로 다음 1개 + 그 블록의 유일한 진입
                if (taken is not None and fall is not None and taken > last["addr"]
                        and i + 1 < n and blocks[i + 1]["start"] == fall
                        and i + 2 <= n):
                    skip = blocks[i + 1]
                    # taken이 skip 블록의 끝(=그 다음 블록 시작)과 같아야 '그 블록만' 건너뜀
                    after = blocks[i + 2]["start"] if i + 2 < n else skip["end"]
                    if taken == after and skip["inbound"] == 1 and not self._falls_out(skip):
                        cond = self._condition(b, invert=True)  # taken=스킵이므로 조건 반전
                        plan.append(("if_block", b, cond, [skip], 1))
                        consumed.add(skip["start"])
                        i += 2
                        collapsed = True
            if collapsed:
                continue
            # 일반 블록 방출
            plan.append(("label", b))
            # 종료 처리
            if last["flow"] == "cjmp" and len(b["succ"]) == 2:
                taken = next((t for k, t in b["succ"] if k == "taken"), None)
                if taken is not None:
                    cond = self._condition(b, invert=False)
                    plan.append(("raw_if_goto", last["addr"], taken, cond))
                    plan.append(("label_use", taken))
            elif last["flow"] == "jmp" and b["succ"]:
                tgt = b["succ"][0][1]
                nxt = blocks[i + 1]["start"] if i + 1 < n else None
                if tgt != nxt:
                    plan.append(("goto", last["addr"], tgt))
                    plan.append(("label_use", tgt))
            i += 1
        # raw_if_goto를 실제 라인으로 바꾸되, if(cond) goto 형태로
        final = []
        for step in plan:
            if step[0] == "raw_if_goto":
                final.append(("ifgoto", step[1], step[2], step[3]))
                final.append(("label_use", step[2]))
            else:
                final.append(step)
        # ifgoto를 goto 처리 흐름에 통합 (emit에서 다루도록 if_block/goto만 쓰므로 변환)
        out = []
        for step in final:
            if step[0] == "ifgoto":
                out.append(("label", {"start": step[1], "end": step[1],
                                      "body": [], "succ": [], "last": None,
                                      "_ifgoto": (step[3], step[2])}))
            else:
                out.append(step)
        return out

    def _falls_out(self, blk):
        """블록이 ret/jmp로 끝나 자연스러운 fallthrough가 아닌지."""
        return blk["last"]["flow"] in ("ret", "jmp")

    # ---- 조건 복원 ------------------------------------------------------
    def _condition(self, block, invert=False):
        last = block["last"]
        mnem = last["mnemonic"]
        cc = mnem[1:] if mnem.startswith("j") else ""
        # 직전 cmp/test 찾기
        cmp_ins = None
        for ins in reversed(block["body"][:-1]):
            if ins["mnemonic"] in ("cmp", "test"):
                cmp_ins = ins
                break
            if ins["mnemonic"].startswith("j"):
                break
        op = _CC.get(cc, None)
        if cmp_ins is None or op is None:
            base = f"조건:{cc}" if cc else "조건"
            return f"!({base})" if invert else base
        ops = _split_ops(cmp_ins["op_str"])
        a = self._operand(ops[0]) if ops else "?"
        b = self._operand(ops[1]) if len(ops) > 1 else "0"
        if cmp_ins["mnemonic"] == "test" and len(ops) == 2 and ops[0] == ops[1]:
            # test x,x  → x (==0 / !=0)
            if op == "==":
                cond = f"{a} == 0"
            elif op == "!=":
                cond = f"{a} != 0"
            else:
                cond = f"{a} {op}"
        else:
            if op.endswith("0"):   # js/jns 류
                cond = f"{a} {op}"
            else:
                cond = f"{a} {op} {b}"
        if invert:
            cond = self._invert(cond)
        return cond

    def _invert(self, cond: str) -> str:
        repl = [(" == ", " \u0000!= "), (" != ", " == "), ("\u0000!= ", " != "),
                (" >= ", " \u0001< "), (" <= ", " \u0002> "),
                (" > ", " <= "), (" < ", " >= "),
                ("\u0001< ", " < "), ("\u0002> ", " > ")]
        for a, b in repl:
            if a in cond:
                return cond.replace(a, b, 1)
        return f"!({cond})"

    # ---- 블록 리프팅 ----------------------------------------------------
    def _lift_block(self, blk):
        """(addr, text, extra_indent, kind) 리스트."""
        if blk.get("_ifgoto"):
            cond, tgt = blk["_ifgoto"]
            return [(blk["start"], f"if ({cond}) goto L_{tgt:x};", 0, "goto")]
        out = []
        argvals = {}      # arg index -> rendered value
        for ins in blk["body"]:
            m = ins["mnemonic"]
            ops = _split_ops(ins["op_str"])
            cmt = ins.get("comment")

            if m in _PROLOGUE_SKIP:
                continue
            if m in ("cmp", "test"):
                continue  # 조건은 분기에서 복원
            if m.startswith("j") and ins["flow"] in ("jmp", "cjmp"):
                continue  # 분기는 _plan/_emit이 처리
            if ins["flow"] == "ret":
                reg = _RET_REG.get(self.bits, "eax")
                out.append((ins["addr"], f"return {reg};", 0, "stmt"))
                continue

            text = self._lift_one(ins, ops, argvals)
            self._track_args(ins, ops, argvals)
            if text is None:
                continue
            if cmt:
                text = f"{text}  // {cmt}"
            out.append((ins["addr"], text, 0, "stmt"))
        return out

    def _track_args(self, ins, ops, argvals, clobber=False):
        """System V 인자 레지스터에 마지막으로 쓰인 값을 추적."""
        if clobber or ins is None:
            return
        mnem = ins["mnemonic"]
        if mnem in ("mov", "movzx", "movsx", "movabs", "lea", "xor") and len(ops) == 2:
            dst = ops[0]
            idx = _ARG_CANON.get(dst)
            if idx is not None:
                if mnem == "xor" and ops[0] == ops[1]:
                    argvals[idx] = "0"
                elif mnem == "lea":
                    argvals[idx] = self._resolved_ref(ins, lea=True) or self._operand(ops[1], lea=True)
                else:
                    argvals[idx] = self._resolved_ref(ins, lea=False) or self._operand(ops[1])

    def _resolved_ref(self, ins, lea=False):
        """rip-상대 등으로 해석된 이름/문자열을 의사코드 소스로 렌더(없으면 None)."""
        for t in ins.get("targets", []):
            if t["kind"] in ("call", "jump"):
                continue
            if t.get("string") is not None:
                s = t["string"]
                if len(s) > 40:
                    s = s[:40] + "…"
                return '"' + s.replace('"', '\\"') + '"'
            if t.get("name"):
                return f"&{t['name']}" if lea else t["name"]
        return None

    def _lift_one(self, ins, ops, argvals):
        m = ins["mnemonic"]
        if m == "call":
            return self._lift_call(ins, argvals)
        # 프레임 설정/해제(프롤로그·에필로그)는 흐름에 무의미하므로 생략
        if m == "mov" and len(ops) == 2 and ops[0] in ("rbp", "ebp") and ops[1] in ("rsp", "esp"):
            return None
        if m in ("sub", "add") and len(ops) == 2 and ops[0] in ("rsp", "esp"):
            return None
        # 인자 레지스터로의 대입은 호출부에 인자로 합쳐 보여주므로 단독 출력 생략
        if m in ("mov", "movzx", "movsx", "movabs", "lea") and ops and ops[0] in _ARG_CANON:
            return None
        if m in ("mov", "movzx", "movsx", "movabs"):
            if len(ops) == 2:
                src = self._resolved_ref(ins, lea=False) or self._operand(ops[1])
                return f"{self._operand(ops[0])} = {src};"
        if m == "lea" and len(ops) == 2:
            src = self._resolved_ref(ins, lea=True) or self._operand(ops[1], lea=True)
            return f"{self._operand(ops[0])} = {src};"
        if m in _BINOP and len(ops) == 2:
            d, s = self._operand(ops[0]), self._operand(ops[1])
            if m == "xor" and ops[0] == ops[1]:
                return f"{d} = 0;"
            return f"{d} {_BINOP[m]}= {s};"
        if m in ("inc",) and ops:
            return f"{self._operand(ops[0])}++;"
        if m in ("dec",) and ops:
            return f"{self._operand(ops[0])}--;"
        if m == "neg" and ops:
            d = self._operand(ops[0])
            return f"{d} = -{d};"
        if m == "not" and ops:
            d = self._operand(ops[0])
            return f"{d} = ~{d};"
        if m in ("sete", "setne", "setg", "setl", "setge", "setle", "seta", "setb") and ops:
            return f"{self._operand(ops[0])} = ({m[3:]});"
        if m in ("cdqe", "cqo", "cdq", "cwde"):
            return None
        # 알 수 없는 명령 → 원형 주석으로 보존(틀린 의사코드 방지)
        raw = f"{m} {ins['op_str']}".strip()
        return f"/* {raw} */"

    def _lift_call(self, ins, argvals):
        callee = None
        for t in ins.get("targets", []):
            if t["kind"] == "call":
                callee = t.get("name") or f"sub_{t['target']:x}"
        if callee is None:
            # 간접 호출: op_str 자체(레지스터/메모리)
            callee = f"(*{self._operand(ins['op_str'])})"
        args = []
        k = 0
        while k in argvals:
            args.append(argvals[k])
            k += 1
        argvals.clear()
        call = f"{callee}({', '.join(args)})"
        reg = _RET_REG.get(self.bits, "eax")
        return f"{reg} = {call};"

    # ---- 피연산자 렌더 --------------------------------------------------
    def _operand(self, op: str, lea=False) -> str:
        op = op.strip()
        # 크기 지시어 제거
        op = re.sub(r"^(byte|word|dword|qword|xmmword|tbyte)\s+ptr\s+", "", op)
        op = re.sub(r"^(byte|word|dword|qword)\s+", "", op)
        mem = re.match(r"^\[(.+)\]$", op)
        if mem:
            inner = mem.group(1).replace(" ", "")
            # rbp/ebp 상대 스택 변수
            mb = re.match(r"^r?bp([+-])(0x[0-9a-f]+|\d+)$", inner)
            if mb:
                sign = mb.group(1)
                off = int(mb.group(2), 16) if mb.group(2).startswith("0x") else int(mb.group(2))
                slot = f"arg_{off:x}" if sign == "+" else f"var_{off:x}"
                self._slots.add(slot)
                var = self._varmap.get(slot, slot)
                return f"&{var}" if lea else var
            # rip 상대 → 해석된 이름/문자열이 refs에 있으면 그걸로(호출부에서 처리됨)
            if inner.startswith("rip"):
                return f"&data" if lea else f"*(data)"
            return f"&({inner})" if lea else f"*({inner})"
        # 즉값/레지스터/심볼
        return op

    def _signature(self, name, blocks) -> str:
        # 인자 개수는 추정하지 않고 이름만. 반환형은 미상 표기.
        return f"undefined {name}()"


class Decompiler:
    """공개 진입점. x86/x64 는 IR 기반 파이프라인(decomp/)으로, 그 밖의 구조는 예전 방식(또는 안내 문구)으로 처리한다."""

    def __init__(self, program):
        self.prog = program

    def decompile(self, addr: int) -> dict:
        prog = self.prog
        if prog.model.arch in ("x86", "x64", "arm64"):
            from .decomp.func import FuncDecomp, TooLarge, TooComplex, Thunk
            try:
                varmap = prog.project.get_varnames(addr) if prog.project else {}
                fd = FuncDecomp(prog, addr)
                fd.run_to_vars()
                renderer, lines = fd.finish(varmap)
                return fd.result(renderer, lines)
            except Thunk as t:
                name = prog.name_for(addr) or f"sub_{addr:x}"
                target = prog.name_for(t.args[0]) or hex(t.args[0])
                lines = [{"addr": addr, "indent": 0, "kind": "comment",
                          "text": f"/* 임포트 스텁: 외부 함수 {name} 를 호출하는 중계 코드입니다 */"}]
                return {"addr": addr, "name": name, "signature": f"{name}()", "lines": lines, "variables": [],
                        "note": "PLT/IAT 스텁입니다. 실제 코드는 외부 라이브러리에 있습니다."}
            except (TooLarge, TooComplex) as e:
                why = (f"함수가 너무 커서({e.args[0]}개 명령어 > {MAX_DECOMPILE_INSNS})" if isinstance(e, TooLarge)
                       else "분석 시간이 너무 오래 걸려")
                name = prog.name_for(addr) or f"sub_{addr:x}"
                lines = [{"addr": addr, "indent": 0, "kind": "comment",
                          "text": f"/* {why} 의사코드를 만들지 않습니다 — 디스어셈블리 보기를 쓰세요 */"}]
                return {"addr": addr, "name": name, "signature": f"{name}()", "lines": lines, "variables": [],
                        "note": "너무 크거나 복잡한 함수라 의사코드를 생략했습니다."}
            except Exception as e:                      # noqa: BLE001 — 새 파이프라인이 실패해도 분석 화면은 계속 쓸 수 있어야 한다
                import logging
                logging.getLogger("rework.decompile").warning("새 디컴파일러 실패, 예전 방식으로 대체: %r", e, exc_info=True)
                out = _LegacyDecompiler(prog).decompile(addr)
                out["note"] = f"(새 디컴파일러가 이 함수를 처리하지 못해 단순 방식으로 대체했습니다: {type(e).__name__}) " + out.get("note", "")
                return out
        return _LegacyDecompiler(prog).decompile(addr)
