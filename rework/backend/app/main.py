"""rework 백엔드 (FastAPI).

로컬 전용 도구: uvicorn을 127.0.0.1에 바인딩해서만 쓴다.
프론트엔드(정적 파일)와 분석 API를 함께 서빙한다.
"""
from __future__ import annotations

import io
import os
import contextlib
import traceback

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .model import function_to_dict, section_to_dict
from .loader import load, LoadError
from .analysis import Program
from .project import Project
from .decompile import Decompiler
from .ai import AIConfig, chat as ai_chat

# ---- 경로 ---------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))        # rework/
FRONTEND = os.path.join(ROOT, "frontend")
DATA_DIR = os.environ.get("REWORK_DATA", os.path.join(ROOT, "data"))
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

app = FastAPI(title="rework", docs_url="/api/docs", openapi_url="/api/openapi.json")


# ---- 세션 상태 ----------------------------------------------------------
class State:
    def __init__(self):
        self.programs: dict[str, Program] = {}   # sha -> Program
        self.current: str | None = None

    def prog(self) -> Program:
        if not self.current or self.current not in self.programs:
            raise HTTPException(400, "열린 바이너리가 없습니다. 먼저 파일을 여세요.")
        return self.programs[self.current]


STATE = State()


def parse_addr(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v).strip().lower()
    try:
        return int(s, 16) if s.startswith("0x") else int(s, 0)
    except ValueError:
        try:
            return int(s, 16)
        except ValueError:
            raise HTTPException(400, f"주소 형식이 아닙니다: {v}")


# ---- 파일 열기 ----------------------------------------------------------
def _open_path(path: str) -> dict:
    try:
        model, raw, lief_bin = load(path)
    except LoadError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"로드 실패: {e}")

    db_path = os.path.join(PROJECTS_DIR, f"{model.sha256}.sqlite")
    project = Project(db_path, model.sha256, meta={
        "name": model.name, "path": model.path, "fmt": model.fmt, "arch": model.arch,
    })
    prog = Program(model, raw, lief_bin, project)
    STATE.programs[model.sha256] = prog
    STATE.current = model.sha256
    return model.summary()


@app.post("/api/open")
def api_open(payload: dict = Body(...)):
    path = payload.get("path", "")
    if not path:
        raise HTTPException(400, "path가 필요합니다.")
    return _open_path(os.path.expanduser(path))


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    dest = os.path.join(UPLOADS_DIR, os.path.basename(file.filename or "upload.bin"))
    with open(dest, "wb") as fh:
        fh.write(await file.read())
    return _open_path(dest)


# ---- 조회 API -----------------------------------------------------------
@app.get("/api/info")
def api_info():
    return STATE.prog().model.summary()


@app.get("/api/sections")
def api_sections():
    return [section_to_dict(s) for s in STATE.prog().model.sections]


@app.get("/api/functions")
def api_functions(query: str = "", offset: int = 0, limit: int = 300):
    prog = STATE.prog()
    prog.build_xrefs()
    items = prog.model.functions
    if query:
        q = query.lower()
        items = [f for f in items
                 if q in (prog.name_for(f.vaddr) or f.display_name).lower()
                 or q in f"{f.vaddr:x}"]
    total = len(items)
    page = items[offset:offset + limit]
    out = []
    for f in page:
        d = function_to_dict(f)
        d["name"] = prog.name_for(f.vaddr) or f.display_name
        d["size"] = prog.function_size(f)
        out.append(d)
    return {"total": total, "offset": offset, "items": out}


@app.get("/api/function/{addr}")
def api_function(addr: str):
    return STATE.prog().disassemble_function(parse_addr(addr))


@app.get("/api/decompile/{addr}")
def api_decompile(addr: str):
    prog = STATE.prog()
    return Decompiler(prog).decompile(parse_addr(addr))


@app.post("/api/varname")
def api_varname(payload: dict = Body(...)):
    prog = STATE.prog()
    func = parse_addr(payload["func"])
    slot = str(payload.get("slot", ""))
    prog.project.set_varname(func, slot, payload.get("name", ""))
    return {"func": func, "slot": slot, "names": prog.project.get_varnames(func)}


@app.get("/api/strings")
def api_strings(query: str = "", offset: int = 0, limit: int = 500):
    prog = STATE.prog()
    items = prog.strings
    if query:
        q = query.lower()
        items = [s for s in items if q in s.value.lower()]
    total = len(items)
    page = items[offset:offset + limit]
    return {"total": total, "offset": offset, "items": [
        {"addr": s.vaddr, "offset": s.offset, "value": s.value, "encoding": s.encoding}
        for s in page
    ]}


@app.get("/api/symbols")
def api_symbols(query: str = "", offset: int = 0, limit: int = 500):
    prog = STATE.prog()
    items = prog.model.symbols
    if query:
        q = query.lower()
        items = [s for s in items if q in s.name.lower()]
    total = len(items)
    page = items[offset:offset + limit]
    return {"total": total, "offset": offset, "items": [
        {"name": s.name, "addr": s.vaddr, "size": s.size, "kind": s.kind,
         "is_import": s.is_import} for s in page
    ]}


@app.get("/api/imports")
def api_imports():
    return [{"name": s.name, "addr": s.vaddr, "library": s.library}
            for s in STATE.prog().model.imports]


@app.get("/api/exports")
def api_exports():
    return [{"name": s.name, "addr": s.vaddr} for s in STATE.prog().model.exports]


@app.get("/api/xrefs/{addr}")
def api_xrefs(addr: str):
    return STATE.prog().xrefs_to(parse_addr(addr))


@app.get("/api/hex")
def api_hex(addr: str, length: int = 256):
    return STATE.prog().hexdump(parse_addr(addr), min(length, 1 << 16))


@app.get("/api/navigate")
def api_navigate(addr: str):
    return STATE.prog().classify(parse_addr(addr))


# ---- 주석(리네임·코멘트·북마크) ----------------------------------------
@app.post("/api/rename")
def api_rename(payload: dict = Body(...)):
    prog = STATE.prog()
    addr = parse_addr(payload["addr"])
    prog.project.set_name(addr, payload.get("name", ""))
    return {"addr": addr, "name": prog.name_for(addr)}


@app.post("/api/comment")
def api_comment(payload: dict = Body(...)):
    prog = STATE.prog()
    addr = parse_addr(payload["addr"])
    prog.project.set_comment(addr, payload.get("text", ""))
    return {"addr": addr, "comment": prog.project.get_comment(addr)}


@app.post("/api/bookmark")
def api_bookmark(payload: dict = Body(...)):
    prog = STATE.prog()
    addr = parse_addr(payload["addr"])
    on = prog.project.toggle_bookmark(addr, payload.get("label", ""))
    return {"addr": addr, "bookmarked": on}


@app.get("/api/bookmarks")
def api_bookmarks():
    return STATE.prog().project.bookmarks()


# ---- 스크립팅 콘솔 ------------------------------------------------------
@app.post("/api/script")
def api_script(payload: dict = Body(...)):
    """분석가 자신의 파이썬 스크립트를 로드된 바이너리에 대해 실행.

    rk 객체로 모델·디스어셈블러·헬퍼에 접근한다. 로컬 전용 도구이고
    분석가 본인의 스크립트이므로 격리하지 않는다(대상 바이너리 실행과는 다름).
    """
    prog = STATE.prog()
    code = payload.get("code", "")
    api = _script_api(prog)
    buf = io.StringIO()
    result = {"ok": True, "output": "", "error": None}
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<rework-script>", "exec"),
                 {"rk": api, "__name__": "__rework_script__"})
    except Exception:
        result["ok"] = False
        result["error"] = traceback.format_exc()
    result["output"] = buf.getvalue()
    return result


def _script_api(prog: Program):
    class RK:
        model = prog.model
        program = prog
        dis = prog.dis

        @staticmethod
        def functions():
            return prog.model.functions

        @staticmethod
        def disasm(addr, size=64):
            return prog.dis.disasm(parse_addr(addr), size)

        @staticmethod
        def func(addr):
            return prog.disassemble_function(parse_addr(addr))

        @staticmethod
        def read(addr, size):
            return prog.dis.read(parse_addr(addr), size)

        @staticmethod
        def name(addr):
            return prog.name_for(parse_addr(addr))

        @staticmethod
        def rename(addr, name):
            prog.project.set_name(parse_addr(addr), name)

        @staticmethod
        def comment(addr, text):
            prog.project.set_comment(parse_addr(addr), text)

        @staticmethod
        def strings():
            return prog.strings

        @staticmethod
        def xrefs(addr):
            return prog.xrefs_to(parse_addr(addr))

    return RK()


# ---- AI 채팅 -----------------------------------------------------------
AI_CFG = AIConfig(os.path.join(DATA_DIR, "ai.json"))


@app.get("/api/ai/config")
def api_ai_config_get():
    return AI_CFG.public()


@app.post("/api/ai/config")
def api_ai_config_set(payload: dict = Body(...)):
    AI_CFG.save(payload)
    return AI_CFG.public()


@app.post("/api/ai/chat")
def api_ai_chat(payload: dict = Body(...)):
    messages = payload.get("messages", [])
    system = payload.get("system", "")
    if not messages:
        raise HTTPException(400, "messages가 필요합니다.")
    return ai_chat(AI_CFG, messages, system)


# ---- 로컬 파일 탐색(파일 열기 UI용) ------------------------------------
@app.get("/api/fs")
def api_fs(path: str = ""):
    path = os.path.expanduser(path) if path else os.path.expanduser("~")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        path = os.path.dirname(path) or "/"
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                is_dir = os.path.isdir(full)
                size = os.path.getsize(full) if not is_dir else 0
            except OSError:
                continue
            if name.startswith("."):
                continue
            entries.append({"name": name, "path": full, "dir": is_dir, "size": size})
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return {"path": path, "parent": os.path.dirname(path), "entries": entries}
    except PermissionError:
        raise HTTPException(403, "권한이 없는 디렉터리입니다.")


# ---- 프론트엔드 ---------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
