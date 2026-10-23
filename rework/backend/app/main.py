"""rework 백엔드 (FastAPI).

로컬 전용 도구: uvicorn을 127.0.0.1에 바인딩해서만 쓴다.
프론트엔드(정적 파일)와 분석 API를 함께 서빙한다.
"""
from __future__ import annotations

import hmac
import io
import os
import re
import shutil
import sys
import threading
import contextlib
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .model import function_to_dict, section_to_dict
from .loader import load, LoadError
from .analysis import Program
from .project import Project
from .decompile import Decompiler
from .ai import AIConfig, chat as ai_chat
from .debug import DebugSession, DebugError, LOG_TAIL_CHARS, HAVE_POSIX_DEBUGGER
from . import sandbox as sbx
from . import __version__

# ---- 경로 ---------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))                   # PyInstaller 등으로 묶인 설치본인가
ROOT = getattr(sys, "_MEIPASS", None) if FROZEN else os.path.abspath(os.path.join(HERE, "..", ".."))   # rework/
ROOT = ROOT or os.path.abspath(os.path.join(HERE, "..", ".."))
FRONTEND = os.environ.get("REWORK_FRONTEND") or os.path.join(ROOT, "frontend")
# 데이터(프로젝트 주석 DB·업로드·AI 설정). 설치본은 쓸 수 없는 번들 안이 아니라 사용자 폴더를 기본으로 쓴다.
DATA_DIR = os.environ.get("REWORK_DATA") or (os.path.join(os.path.expanduser("~"), ".rework", "data") if FROZEN
                                             else os.path.join(ROOT, "data"))
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
if os.name == "posix" and (FROZEN or os.environ.get("REWORK_DESKTOP") == "1"):
    try:                                                       # 설치본의 사용자 데이터(AI 키·프로젝트)는 본인만 열 수 있게
        os.chmod(DATA_DIR, 0o700)
    except OSError:
        pass
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# 분석(열기·업로드)할 수 있는 파일의 최대 크기. 서버는 파일을 통째로 메모리에 올리고 파싱 결과가 그 몇 배가 되므로
# 상한이 없으면 큰 파일 하나로 서버(와 거기서 도는 디버깅 세션)가 메모리 부족으로 죽을 수 있다.
try:
    MAX_FILE_MB = max(1, int(os.environ.get("REWORK_MAX_FILE_MB", "512")))
except ValueError:
    MAX_FILE_MB = 512
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
FUNCTIONS_PAGE_MAX = 50000          # /api/functions 한 번에 돌려줄 최대 항목 수

# 로컬 전용 서버: Host 헤더가 로컬 주소가 아니면 거절한다(DNS rebinding 으로 외부 웹페이지가
# 이 API 를 부르는 것 — 예: 격리를 끄고 프로그램 실행 — 을 막는다). 원격 접속이 필요하면
# REWORK_ALLOWED_HOSTS=호스트1,호스트2 로 명시한다.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"} | {
    h.strip().lower() for h in os.environ.get("REWORK_ALLOWED_HOSTS", "").split(",") if h.strip()}


def _host_only(host: str) -> str | None:
    """Host 헤더에서 호스트 이름만 뽑는다. 형식이 올바르지 않으면 None(`user@host` 같은 값은 거부)."""
    host = host.strip().lower()
    if not host or len(host) > 255:
        return None
    m = re.fullmatch(r"(\[[0-9a-f:.]+\]|[a-z0-9._-]+)(?::\d{1,5})?", host)
    return m.group(1) if m else None


@asynccontextmanager
async def lifespan(_app):
    # 서버 종료(Ctrl+C·SIGTERM) 시 gdb와 디버깅 대상 프로세스가 고아로 남지 않게 정리한다.
    try:
        yield
    finally:
        _teardown_debug()


# 자동 문서(/api/docs·/redoc)는 외부 CDN 의 스크립트를 앱 출처에서 실행하므로 끈다
app = FastAPI(title="rework", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


# ---- 세션 상태 ----------------------------------------------------------
class State:
    def __init__(self):
        self.programs: dict[str, Program] = {}   # sha -> Program
        self.current: str | None = None
        self.debug: DebugSession | None = None   # 활성 디버그 세션(한 번에 하나)
        self.dbg_lock = threading.RLock()        # 세션 시작·종료·교체를 직렬화

    def prog(self) -> Program:
        if not self.current or self.current not in self.programs:
            raise HTTPException(400, "열린 바이너리가 없습니다. 먼저 파일을 여세요.")
        return self.programs[self.current]

    def dbg(self) -> DebugSession:
        if not self.debug or not self.debug._alive:
            raise HTTPException(400, "디버그 세션이 없습니다. 먼저 디버깅을 시작하세요.")
        return self.debug


STATE = State()


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 선택 기능: REWORK_TOKEN(또는 run.py --require-token)이 있으면 모든 /api/ 요청에 토큰이 필요하다. 이 컴퓨터의 다른 사용자·
# 프로세스(그리고 network=host 로 127.0.0.1 에 닿는 샌드박스 대상)가 API 를 부르지 못하게 한다. 토큰은 주소의 ?token= 으로
# 한 번 전달하면 HttpOnly·SameSite=Strict 쿠키로 저장되고, 스크립트는 X-Rework-Token 헤더를 쓴다.
AUTH_TOKEN = os.environ.get("REWORK_TOKEN") or None
if AUTH_TOKEN is not None and not AUTH_TOKEN.isascii():
    # 헤더·쿠키·주소를 거치며 인코딩이 달라지므로 ASCII 만 받는다(run.py --require-token 이 만드는 토큰은 ASCII 다)
    raise RuntimeError("REWORK_TOKEN 은 ASCII 문자(영문·숫자·기호)로만 구성해야 합니다.")


def _token_ok(given) -> bool:
    """상수 시간 비교. 비 ASCII·이상한 값이 와도 예외 없이 거부한다."""
    try:
        return hmac.compare_digest(str(given).encode("utf-8", "surrogateescape"), AUTH_TOKEN.encode("utf-8"))
    except Exception:                                      # noqa: BLE001
        return False


_SEC_HEADERS = {
    "X-Frame-Options": "DENY",
    # UI 는 같은 출처의 스크립트·스타일만 쓰고 인라인 스크립트·eval 이 없다: 분석 대상 파일에서 온 문자열이 어떻게든 HTML 로 해석되더라도
    # 스크립트가 실행되지 않게 하는 마지막 방어선(그리고 다른 출처로 데이터를 보내지 못하게 connect-src 도 같은 출처로 제한).
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                               "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; "
                               "form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def _local_only_same_origin(request, call_next):
    """① Host 가 로컬 주소가 아니면 거절(DNS rebinding). ② 상태를 바꾸는 요청이 다른 출처의 웹페이지에서 온 것이면
    거절(CSRF) — 브라우저가 붙이는 Sec-Fetch-Site·Origin 헤더는 페이지가 위조할 수 없다. multipart 업로드나 본문 없는
    POST 는 CORS 사전 요청 없이 보낼 수 있어 FastAPI 의 JSON 검사만으로는 막히지 않는다. curl·스크립트처럼 그 헤더가
    없는 요청은 통과한다. ③ 토큰 인증이 켜져 있으면 /api/ 에 토큰을 요구한다. ④ 프레임 삽입·MIME 스니핑을 막는 헤더."""
    def deny(code: int, msg: str):
        r = JSONResponse({"detail": msg}, status_code=code)
        for k, v in _SEC_HEADERS.items():
            r.headers[k] = v
        return r

    hdrs = request.headers
    for name in ("host", "origin", "sec-fetch-site"):       # 같은 헤더가 여러 개면 서버·프록시마다 해석이 달라질 수 있다
        if len(hdrs.getlist(name)) > 1:
            return deny(400, f"{name} 헤더가 여러 개입니다.")
    host = hdrs.get("host", "")
    if _host_only(host) not in _ALLOWED_HOSTS:
        return deny(403, "허용되지 않은 Host 입니다. 로컬 주소(127.0.0.1·localhost)로 접속하세요.")
    if request.method in _UNSAFE_METHODS or request.url.path.startswith("/api/"):
        # 상태를 바꾸는 요청은 물론 GET API 도 다른 출처가 부르지 못하게 한다: 쿠키(SameSite)는 '사이트' 단위라 같은 호스트의
        # 다른 포트에서 도는 서비스의 페이지가 보낸 요청에도 실려 가기 때문이다(포트는 사이트 구분에 쓰이지 않는다).
        site = hdrs.get("sec-fetch-site")
        origin = hdrs.get("origin")
        cross = site is not None and site not in ("same-origin", "none")
        if origin is not None:
            m = re.fullmatch(r"(https?)://([^/]+)", origin.strip())
            cross = cross or not m or m.group(1) != request.url.scheme or m.group(2).lower() != host.strip().lower()
        if cross:
            return deny(403, "다른 출처의 페이지에서 보낸 요청은 허용하지 않습니다.")
    if AUTH_TOKEN:
        path = request.url.path
        given = request.query_params.get("token")
        if path == "/" and given is not None:
            if _token_ok(given):
                r = RedirectResponse("/", status_code=303)
                r.set_cookie("rework_token", AUTH_TOKEN, httponly=True, samesite="strict", path="/")
                for k, v in _SEC_HEADERS.items():
                    r.headers[k] = v
                return r
            return deny(401, "토큰이 올바르지 않습니다.")
        if path.startswith("/api/"):
            tok = hdrs.get("x-rework-token") or request.cookies.get("rework_token") or ""
            if not _token_ok(tok):
                return deny(401, "인증이 필요합니다. 서버 콘솔에 출력된 주소(?token=…)로 접속하거나 X-Rework-Token 헤더를 보내세요.")
    resp = await call_next(request)
    for k, v in _SEC_HEADERS.items():
        resp.headers[k] = v
    return resp


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    """처리되지 않은 예외: 경로·트레이스백을 응답에 싣지 않고(서버 로그에는 남는다) 보안 헤더를 붙여 돌려준다.
    (Starlette 의 기본 500 응답은 위 미들웨어를 거치지 않아 헤더가 빠진다.)"""
    r = JSONResponse({"detail": "서버 내부 오류가 발생했습니다. 서버 로그를 확인하세요."}, status_code=500)
    for k, v in _SEC_HEADERS.items():
        r.headers[k] = v
    return r


class _UploadLimit:
    """/api/upload 의 요청 본문이 MAX_FILE_BYTES 를 넘으면 읽던 중에도 끊고 413 을 돌려준다(순수 ASGI 라 본문을 버퍼링하지 않는다).
    FastAPI 는 본문을 읽다 난 예외를 400 으로 바꿔 버리므로, 한도를 넘는 순간 여기서 직접 413 을 보내고 앱에는 연결이
    끊긴 것으로 보이게 한 뒤 앱이 뒤늦게 내는 응답은 버린다."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/upload":
            return await self.app(scope, receive, send)
        limit = MAX_FILE_BYTES + (1 << 20)                  # multipart 머리글 몫
        rejected = False

        async def reject():
            body = ('{"detail":"파일이 너무 큽니다(최대 %d MB). REWORK_MAX_FILE_MB 로 늘릴 수 있습니다."}' % MAX_FILE_MB).encode()
            hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close")] + [(k.lower().encode(), v.encode()) for k, v in _SEC_HEADERS.items()]
            await send({"type": "http.response.start", "status": 413, "headers": hdrs})
            await send({"type": "http.response.body", "body": body})

        cl = dict(scope["headers"]).get(b"content-length", b"")
        if cl.isdigit() and int(cl) > limit:
            return await reject()
        total = 0

        async def limited_receive():
            nonlocal total, rejected
            if rejected:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > limit:
                    rejected = True
                    await reject()
                    return {"type": "http.disconnect"}
            return msg

        async def guarded_send(msg):
            if not rejected:
                await send(msg)

        await self.app(scope, limited_receive, guarded_send)


app.add_middleware(_UploadLimit)


_ADDR_MAX = (1 << 64) - 1


def parse_addr(v) -> int:
    if isinstance(v, bool):
        raise HTTPException(400, "주소 형식이 아닙니다.")
    if isinstance(v, int):
        n = v
    else:
        s = str(v).strip().lower()
        try:
            n = int(s, 16) if s.startswith("0x") else int(s, 0)
        except ValueError:
            try:
                n = int(s, 16)
            except ValueError:
                raise HTTPException(400, f"주소 형식이 아닙니다: {v}")
    if not (0 <= n <= _ADDR_MAX):
        raise HTTPException(400, f"주소 범위를 벗어났습니다: {v}")
    return n


def _need(payload: dict, key: str):
    """필수 키가 없으면 KeyError(500) 대신 400."""
    if not isinstance(payload, dict) or key not in payload:
        raise HTTPException(400, f"{key} 가 필요합니다.")
    return payload[key]


def _str_field(payload: dict, key: str, default: str = "", maxlen: int = 4096) -> str:
    """문자열 필드를 꺼내 검증한다(문자열이 아니거나, 짝 없는 서로게이트처럼 JSON·SQLite 로 못 나가는 값은 400)."""
    v = payload.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str):
        raise HTTPException(400, f"{key} 는 문자열이어야 합니다.")
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(400, f"{key} 에 올바르지 않은 문자가 있습니다.")
    if len(v) > maxlen:
        raise HTTPException(400, f"{key} 가 너무 깁니다(최대 {maxlen}자).")
    return v


# ---- 파일 열기 ----------------------------------------------------------
PROGRAMS_KEEP = 8          # 메모리·SQLite 연결(fd)에 남겨 둘 열어 본 파일 수. 넘으면 가장 오래 안 쓴 것부터 닫는다


def _remember_program(sha: str, prog: Program):
    """열어 본 파일을 보관하되 개수를 제한한다(서로 다른 파일을 계속 열어도 fd·메모리가 무한히 늘지 않게)."""
    old = STATE.programs.pop(sha, None)          # 다시 열었다면 맨 뒤(가장 최근)로
    STATE.programs[sha] = prog
    if old is not None and old is not prog:
        old.project.close()
    while len(STATE.programs) > PROGRAMS_KEEP:
        victim = next(k for k in STATE.programs if k != sha)
        STATE.programs.pop(victim).project.close()


def _open_path(path: str) -> dict:
    if not isinstance(path, str):
        raise HTTPException(400, "path 는 문자열이어야 합니다.")
    try:
        size = os.path.getsize(path)
    except (OSError, ValueError):
        size = 0                                  # 없는 파일 등은 load() 가 알맞은 오류를 낸다
    if size > MAX_FILE_BYTES:
        raise HTTPException(400, f"파일이 너무 큽니다({size >> 20}MB > 최대 {MAX_FILE_MB}MB). "
                                 "서버를 시작할 때 REWORK_MAX_FILE_MB 로 상한을 늘릴 수 있습니다.")
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
    _remember_program(model.sha256, prog)
    STATE.current = model.sha256
    return model.summary()


def _teardown_debug():
    """활성 디버그 세션을 종료한다. 디버깅 중인 바이너리는 실행 중이라
    파일이 잠겨(ETXTBSY) 덮어쓸 수 없으므로, 파일을 건드리기 전에 호출한다."""
    with STATE.dbg_lock:
        sess, STATE.debug = STATE.debug, None
        if sess is not None:
            try:
                sess.stop()   # 대상 프로세스가 사라질 때까지 기다린 뒤 반환한다
            except Exception:
                pass


@app.post("/api/open")
def api_open(payload: dict = Body(...)):
    path = _str_field(payload, "path")
    if not path or "\0" in path:
        raise HTTPException(400, "path가 필요합니다.")
    _teardown_debug()
    return _open_path(os.path.expanduser(path))


def _save_upload(dest: str, src) -> None:
    """src(파일 객체)를 조각으로 읽어 임시 파일에 쓴 뒤 원자적으로 교체한다. 같은 이름의 바이너리가 아직 실행 중이어도
    (open-for-write만 막는) ETXTBSY 없이 디렉터리 항목만 바꾼다. 통째로 메모리에 올리지 않는다."""
    tmp = f"{dest}.part-{os.urandom(4).hex()}"
    try:
        with open(tmp, "wb") as fh:
            src.seek(0)
            shutil.copyfileobj(src, fh, 1 << 20)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "") or "upload.bin"
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:                    # 파일 이름이 올바른 UTF-8 이 아니면 JSON 으로 나갈 수 없다
        name = name.encode("utf-8", "replace").decode("utf-8")
    dest = os.path.join(UPLOADS_DIR, name)
    # 종료 대기·파일 쓰기는 블로킹이므로 이벤트 루프 밖(스레드풀)에서 수행한다
    await run_in_threadpool(_teardown_debug)
    try:
        await run_in_threadpool(_save_upload, dest, file.file)
    except OSError as e:
        raise HTTPException(400, f"파일 저장 실패: {e}")
    return _open_path(dest)


# ---- 조회 API -----------------------------------------------------------
@app.get("/api/about")
def api_about():
    """앱 정보(데스크톱 셸·정보 화면용)."""
    import platform as _pf
    return {"name": "rework", "version": __version__, "desktop": os.environ.get("REWORK_DESKTOP") == "1",
            "frozen": FROZEN, "platform": sys.platform, "python": _pf.python_version(),
            "debugger_supported": HAVE_POSIX_DEBUGGER, "max_file_mb": MAX_FILE_MB,
            "wsl": bool(os.environ.get("WSL_DISTRO_NAME")), "data_dir": DATA_DIR}


@app.get("/api/info")
def api_info():
    return STATE.prog().model.summary()


@app.get("/api/sections")
def api_sections():
    return [section_to_dict(s) for s in STATE.prog().model.sections]


@app.get("/api/functions")
def api_functions(query: str = "", offset: int = 0, limit: int = 300):
    offset = max(0, offset)
    limit = max(0, min(limit, FUNCTIONS_PAGE_MAX))
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
    func = parse_addr(_need(payload, "func"))
    slot = _str_field(payload, "slot", maxlen=256)
    prog.project.set_varname(func, slot, _str_field(payload, "name", maxlen=256))
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
    addr = parse_addr(_need(payload, "addr"))
    prog.project.set_name(addr, _str_field(payload, "name", maxlen=256))
    return {"addr": addr, "name": prog.name_for(addr)}


@app.post("/api/comment")
def api_comment(payload: dict = Body(...)):
    prog = STATE.prog()
    addr = parse_addr(_need(payload, "addr"))
    prog.project.set_comment(addr, _str_field(payload, "text", maxlen=65536))
    return {"addr": addr, "comment": prog.project.get_comment(addr)}


@app.post("/api/bookmark")
def api_bookmark(payload: dict = Body(...)):
    prog = STATE.prog()
    addr = parse_addr(_need(payload, "addr"))
    on = prog.project.toggle_bookmark(addr, _str_field(payload, "label", maxlen=256))
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


# ---- 격리 실행(샌드박스) -----------------------------------------------
SANDBOX_CFG = sbx.SandboxConfig(os.path.join(DATA_DIR, "sandbox.json"))


def _sandbox_report() -> dict:
    status = sbx.probe()
    return {"status": status.to_dict(), "policy": SANDBOX_CFG.policy.to_dict(),
            "report": sbx.describe(SANDBOX_CFG.policy, status, potential=True),
            "guard": {"ready": status.guard_ready, "reason": sbx.guard_unavailable_reason(status)},
            "debugger": {"supported": HAVE_POSIX_DEBUGGER,
                         "reason": None if HAVE_POSIX_DEBUGGER else
                         "이 운영체제(Windows)에서는 디버거를 쓸 수 없습니다. 디버깅은 리눅스(또는 데스크톱 앱의 WSL2 모드)에서 지원합니다."},
            "server": {"token_auth": bool(AUTH_TOKEN)}}


@app.get("/api/sandbox/status")
def api_sandbox_status():
    return _sandbox_report()


@app.post("/api/sandbox/recheck")
def api_sandbox_recheck():
    sbx.probe(force=True)
    return _sandbox_report()


@app.post("/api/sandbox/config")
def api_sandbox_config(payload: dict = Body(...)):
    try:
        SANDBOX_CFG.save(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _sandbox_report()


# ---- 디버거 (GDB/MI) ---------------------------------------------------
def _dbg_guard(fn):
    try:
        return fn()
    except DebugError as e:
        raise HTTPException(400, str(e))


@app.post("/api/debug/start")
def api_debug_start(payload: dict = Body(...)):
    prog = STATE.prog()
    if not HAVE_POSIX_DEBUGGER:
        raise HTTPException(400, "이 운영체제(Windows)에서는 디버거를 쓸 수 없습니다. 디버깅은 리눅스(또는 데스크톱 앱의 WSL2 모드)에서 "
                                 "지원합니다. 정적 분석(디스어셈블·디컴파일)은 그대로 쓸 수 있습니다.")
    if prog.model.fmt != "ELF":
        raise HTTPException(400, f"{prog.model.fmt} 형식은 이 컴퓨터의 gdb 로 실행·디버깅할 수 없습니다(리눅스 ELF 만 지원). "
                                 "정적 분석(디스어셈블·디컴파일)은 그대로 쓸 수 있습니다.")
    if not shutil.which("gdb"):
        raise HTTPException(400, "GDB를 찾을 수 없습니다. 설치한 뒤 다시 시도하세요 (예: apt install gdb).")
    try:                           # 저장된 정책 위에 이번 요청의 값을 덮는다(저장하지 않음)
        policy = SANDBOX_CFG.effective(payload.get("sandbox"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if policy.enabled and policy.network == "host" and not AUTH_TOKEN:
        # 호스트 네트워크를 공유하면 대상이 127.0.0.1 의 이 서버 API 에 닿아 격리를 끄고 다시 시작하는 것까지 할 수 있다
        raise HTTPException(400, "호스트 네트워크 공유는 토큰 인증이 켜져 있을 때만 쓸 수 있습니다. "
                                 "서버를 `run.py --require-token` 으로 다시 실행하세요.")
    with STATE.dbg_lock:           # 동시 시작·정지가 세션을 새거나 교차 종료하지 않도록 직렬화
        _teardown_debug()
        try:
            sess = DebugSession(prog.model.path, program=prog, sandbox=policy)
            state = sess.start()
        except DebugError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(500, f"디버거 시작 실패: {e}")
        STATE.debug = sess
        # 요청된 중단점(함수명 등) 설치. 실패한 것은 응답에 실어 사용자에게 알린다
        # (스트립된 바이너리에서 main 을 못 찾는 경우 등).
        failed = []
        for loc in payload.get("breakpoints", []):
            try:
                sess.break_insert(str(loc))
            except DebugError as e:
                failed.append({"location": str(loc), "error": str(e)})
        return {"state": state, "breakpoints": _dbg_guard(sess.breakpoints),
                "failed_breakpoints": failed}


@app.post("/api/debug/stop")
def api_debug_stop():
    _teardown_debug()
    return {"ok": True}


@app.get("/api/debug/state")
async def api_debug_state():
    # 스레드풀이 느린 분석 요청으로 가득 차도 상태 조회는 막히지 않도록 이벤트 루프에서 바로 처리한다(블로킹 호출 없음)
    if not STATE.debug:
        return {"state": {"status": "none", "alive": False}}
    return {"state": STATE.debug.snapshot()}


@app.post("/api/debug/exec")
def api_debug_exec(payload: dict = Body(...)):
    sess = STATE.dbg()
    action = payload.get("action", "")
    fns = {"run": lambda: sess.run(payload.get("args", "")),
           "continue": sess.cont, "step": sess.step, "next": sess.next,
           "stepi": sess.stepi, "nexti": sess.nexti, "finish": sess.finish,
           "interrupt": sess.interrupt}
    if action not in fns:
        raise HTTPException(400, f"알 수 없는 동작: {action}")
    return {"state": _dbg_guard(fns[action])}


@app.post("/api/debug/break")
def api_debug_break(payload: dict = Body(...)):
    sess = STATE.dbg()
    op = payload.get("op", "add")
    if op == "add":
        loc = payload.get("location") or payload.get("addr")
        return _dbg_guard(lambda: sess.break_insert(str(loc)))
    if op == "delete":
        return _dbg_guard(lambda: sess.break_delete(_need(payload, "number")))
    if op == "toggle":
        return _dbg_guard(lambda: sess.break_toggle(_need(payload, "number"), payload.get("enable", True)))
    raise HTTPException(400, f"알 수 없는 op: {op}")


@app.get("/api/debug/breakpoints")
def api_debug_breakpoints():
    return _dbg_guard(STATE.dbg().breakpoints)


@app.get("/api/debug/registers")
def api_debug_registers():
    return {"registers": _dbg_guard(STATE.dbg().registers)}


@app.get("/api/debug/stack")
def api_debug_stack():
    sess = STATE.dbg()
    frames = _dbg_guard(sess.stack)
    return {"stack": frames, "truncated": sess._stack_truncated}


@app.get("/api/debug/locals")
def api_debug_locals():
    return _dbg_guard(STATE.dbg().locals)


@app.get("/api/debug/disasm")
def api_debug_disasm():
    return _dbg_guard(lambda: STATE.dbg().disassemble())


@app.post("/api/debug/eval")
def api_debug_eval(payload: dict = Body(...)):
    return _dbg_guard(lambda: STATE.dbg().evaluate(payload.get("expr", "")))


@app.post("/api/debug/setvar")
def api_debug_setvar(payload: dict = Body(...)):
    sess = STATE.dbg()
    return _dbg_guard(lambda: sess.set_var(_need(payload, "expr"), _need(payload, "value")))


@app.post("/api/debug/setreg")
def api_debug_setreg(payload: dict = Body(...)):
    sess = STATE.dbg()
    return _dbg_guard(lambda: sess.set_register(_need(payload, "name"), _need(payload, "value")))


@app.get("/api/debug/mem")
def api_debug_mem(addr: str, count: int = 128):
    return _dbg_guard(lambda: STATE.dbg().read_memory(addr, min(count, 1 << 14)))


@app.post("/api/debug/writemem")
def api_debug_writemem(payload: dict = Body(...)):
    sess = STATE.dbg()
    return _dbg_guard(lambda: sess.write_memory(_need(payload, "addr"), _need(payload, "hex")))


@app.post("/api/debug/console")
def api_debug_console(payload: dict = Body(...)):
    return _dbg_guard(lambda: STATE.dbg().console_cmd(payload.get("cmd", "")))


def _stdin_args(payload: dict) -> tuple:
    """/api/debug/stdin 본문 검증. 옵션은 진짜 불리언만 받는다(`"false"` 문자열이 True 가 되면 안 된다)."""
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(400, "text는 문자열이어야 합니다.")
    flags = {}
    for key, default in (("newline", True), ("escapes", False), ("eof", False)):
        v = payload.get(key, default)
        if not isinstance(v, bool):
            raise HTTPException(400, f"{key}는 true/false 여야 합니다.")
        flags[key] = v
    return text, flags["newline"], flags["escapes"], flags["eof"]


@app.post("/api/debug/stdin")
def api_debug_stdin(payload: dict = Body(...)):
    """대상 프로그램의 stdin 으로 입력을 보낸다(전용 pty)."""
    sess = STATE.dbg()
    text, newline, escapes, eof = _stdin_args(payload)
    return _dbg_guard(lambda: sess.send_input(text, newline=newline, escapes=escapes, eof=eof))


@app.get("/api/debug/output")
def api_debug_output(clear: bool = False, full: bool = False):
    """콘솔 패널이 쓰는 건 섞은 로그(merged)뿐이다. 뒤쪽 일정 글자 수만 돌려줘서(대상이 출력을 쏟아내도) 응답과
    JSON 이스케이프 비용이 커지지 않게 한다. target·console 은 full=true(또는 clear=true)일 때만.
    세션이 한도·디버거 사망으로 끝난 뒤에도 다음 시작 전까지는 마지막 로그를 돌려준다(왜 끝났는지 볼 수 있게)."""
    sess = STATE.debug
    if sess is None:
        raise HTTPException(400, "디버그 세션이 없습니다. 먼저 디버깅을 시작하세요.")
    out = {"merged": sess.merged_output(tail=LOG_TAIL_CHARS)}
    if full or clear:
        out["target"] = sess.target_output(clear=clear, tail=LOG_TAIL_CHARS)
        out["console"] = sess.console_output(tail=LOG_TAIL_CHARS)
    return out


# ---- 로컬 파일 탐색(파일 열기 UI용) ------------------------------------
@app.get("/api/fs")
def api_fs(path: str = ""):
    path = os.path.expanduser(path) if path else os.path.expanduser("~")
    if "\0" in path:
        raise HTTPException(400, "경로에 올바르지 않은 문자가 있습니다.")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        path = os.path.dirname(path) or "/"
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            try:
                name.encode("utf-8")
            except UnicodeEncodeError:          # 올바른 UTF-8 이 아닌 이름은 JSON 으로 낼 수 없다(목록 전체가 500 이 되지 않게 건너뜀)
                continue
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
        drives = []
        if os.name == "nt":                       # Windows: 다른 드라이브로 옮겨 갈 수 있게 드라이브 목록을 함께 준다
            import string
            drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
        return {"path": path, "parent": os.path.dirname(path), "entries": entries, "drives": drives}
    except PermissionError:
        raise HTTPException(403, "권한이 없는 디렉터리입니다.")


# ---- 프론트엔드 ---------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


if os.path.isdir(FRONTEND):
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
