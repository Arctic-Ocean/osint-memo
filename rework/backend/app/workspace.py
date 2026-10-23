"""작업 폴더(소스 편집): 폴더를 열어 파일 트리를 보고 텍스트 파일을 읽고 고치고 저장한다.

이 모듈은 '디스크에 쓰는' 기능이라 경로 처리가 전부다. 원칙:
- **작업 폴더 안에서만**: 모든 경로는 열어 둔 폴더의 상대 경로이고, 실제 경로(realpath)가 그 폴더 안에 있어야 한다.
  `..`·절대 경로·**폴더 밖을 가리키는 심볼릭 링크**는 읽기도 쓰기도 거부한다.
- **텍스트만**: NUL 이 있거나 UTF-8·CP949(한글 레거시)·UTF-16(BOM)으로 해석되지 않으면 바이너리로 보고 거부한다(편집기 대신 분석으로 열게 한다).
  읽은 인코딩 그대로 저장하므로 CP949 소스가 UTF-8 로 바뀌지 않는다. 크기 상한 5MB.
- **쓰기는 원자적·충돌 감지**: 같은 폴더의 임시 파일에 쓰고 `os.replace` 로 바꾼다(중간에 죽어도 반쯤 쓰인 파일이 없다). 읽은 뒤 다른 프로그램이
  파일을 고쳤다면(mtime 불일치) 덮어쓰지 않고 충돌로 알린다.
- **쓰기 금지 구역**: `.git`(훅으로 코드가 실행될 수 있다)·rework 데이터 폴더(AI 키·격리 설정). 작업 폴더 자체의 삭제·이동 금지.
- 폴더 루트(`/`·드라이브 루트)는 열지 않는다(트리·검색이 끝없이 커진다).
"""
from __future__ import annotations

import codecs
import fnmatch
import os
import re
import shutil
import stat
import threading
import time

MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_DIR_ENTRIES = 5000
SEARCH_MAX_RESULTS = 500
SEARCH_MAX_FILE = 1024 * 1024
SEARCH_SECONDS = 6.0
SEARCH_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
ENCODINGS = ("utf-8", "utf-8-sig", "cp949", "utf-16")
_BAD_NAME_NT = set('<>:"|?*')


def _parse_mtime(v):
    """mtime_ns 는 1.7e18 쯤이라 JSON 숫자로 주고받으면 자바스크립트(2^53 까지만 정확)에서 끝자리가 깨져
    멀쩡한 파일이 ‘바뀌었다’고 나온다. 그래서 API 에서는 십진수 문자열로 다룬다(정수도 받아 준다)."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, str)) or (isinstance(v, str) and not (v.isascii() and v.isdigit() and len(v) <= 24)):
        raise WorkspaceError(400, "expected_mtime_ns 는 정수(또는 숫자 문자열)여야 합니다.")
    return int(v)


class WorkspaceError(Exception):
    """HTTP 응답으로 바뀌는 오류(status·한국어 메시지·추가 필드)."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def decode_text(data: bytes) -> tuple[str, str]:
    """(텍스트, 인코딩). 텍스트가 아니면 WorkspaceError(415)."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    elif b"\0" in data[:8192]:
        raise WorkspaceError(415, "바이너리 파일은 편집기에서 열 수 없습니다. 분석(디스어셈블)으로 여세요.", binary=True)
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp949"), "cp949"
    except UnicodeDecodeError:
        raise WorkspaceError(415, "텍스트로 해석할 수 없는 파일입니다(UTF-8·CP949·UTF-16 이 아님). 분석으로 여세요.", binary=True)


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not name or name in (".", "..") or len(name) > 255:
        raise WorkspaceError(400, "올바르지 않은 이름입니다.")
    if "/" in name or "\0" in name or any(ord(c) < 32 for c in name):
        raise WorkspaceError(400, "이름에 쓸 수 없는 문자가 있습니다.")
    if os.name == "nt" and ("\\" in name or any(c in _BAD_NAME_NT for c in name) or name[-1] in " ."):
        raise WorkspaceError(400, "Windows 에서 쓸 수 없는 이름입니다.")
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise WorkspaceError(400, "이름에 올바르지 않은 문자가 있습니다.")
    return name


class Workspace:
    def __init__(self, protected=lambda: []):
        self._lock = threading.RLock()
        self.root: str | None = None
        self._protected = protected          # 쓰기를 막을 디렉터리 목록을 돌려주는 함수(데이터 폴더 등)

    # ---- 열기·닫기 ----------------------------------------------------------
    def open(self, path: str) -> str:
        if not isinstance(path, str) or not path or "\0" in path:
            raise WorkspaceError(400, "폴더 경로가 필요합니다.")
        try:
            real = os.path.realpath(path, strict=True)
        except OSError:
            raise WorkspaceError(404, "폴더를 찾을 수 없습니다.")
        if not os.path.isdir(real):
            raise WorkspaceError(400, "폴더가 아닙니다.")
        if os.path.dirname(real) == real:
            raise WorkspaceError(400, "최상위 폴더(/ 또는 드라이브)는 열 수 없습니다. 더 구체적인 폴더를 고르세요.")
        if not os.access(real, os.R_OK | os.X_OK):
            raise WorkspaceError(403, "이 폴더를 읽을 권한이 없습니다.")
        with self._lock:
            self.root = real
        return real

    def close(self):
        with self._lock:
            self.root = None

    def _need_root(self) -> str:
        root = self.root
        if root is None:
            raise WorkspaceError(400, "열린 작업 폴더가 없습니다. 먼저 폴더를 여세요.")
        return root

    # ---- 경로 --------------------------------------------------------------
    @staticmethod
    def _parts(rel) -> list:
        if not isinstance(rel, str) or "\0" in rel:
            raise WorkspaceError(400, "경로가 올바르지 않습니다.")
        if os.name == "nt":
            rel = rel.replace("\\", "/")
            if re.match(r"^[A-Za-z]:", rel):
                raise WorkspaceError(400, "작업 폴더 안의 상대 경로만 쓸 수 있습니다.")
        if rel.startswith("/"):
            raise WorkspaceError(400, "작업 폴더 안의 상대 경로만 쓸 수 있습니다.")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise WorkspaceError(400, "상위 폴더(..)로 벗어나는 경로는 쓸 수 없습니다.")
        try:
            rel.encode("utf-8")
        except UnicodeEncodeError:
            raise WorkspaceError(400, "경로에 올바르지 않은 문자가 있습니다.")
        return parts

    def _inside(self, real: str) -> bool:
        root = _norm(self._need_root())
        r = _norm(real)
        return r == root or r.startswith(root + os.sep)

    def _rel(self, real: str) -> str:
        r = os.path.relpath(real, self._need_root())
        return "" if r == "." else r.replace(os.sep, "/")

    def resolve(self, rel, must_exist: bool = True) -> str:
        """상대 경로 → 작업 폴더 안의 실제 경로. 폴더 밖이면 403, 없으면(must_exist) 404."""
        root = self._need_root()
        parts = self._parts(rel)
        full = os.path.join(root, *parts) if parts else root
        try:
            if must_exist:
                real = os.path.realpath(full, strict=True)
            else:
                if not parts:
                    raise WorkspaceError(400, "경로가 필요합니다.")
                real = os.path.join(os.path.realpath(os.path.dirname(full), strict=True), _check_name(parts[-1]))
        except OSError:
            raise WorkspaceError(404, "경로를 찾을 수 없습니다.")
        if not self._inside(real):
            raise WorkspaceError(403, "작업 폴더 밖을 가리키는 경로(심볼릭 링크 포함)는 사용할 수 없습니다.")
        return real

    def _writable(self, real: str):
        """쓰기·삭제·이름 바꾸기가 금지된 곳인지 검사한다."""
        root = self._need_root()
        if _norm(real) == _norm(root):
            raise WorkspaceError(403, "작업 폴더 자체는 바꿀 수 없습니다.")
        rel_parts = [p.lower() for p in os.path.relpath(real, root).split(os.sep)]
        if ".git" in rel_parts:
            raise WorkspaceError(403, ".git 폴더 안은 수정하지 않습니다(훅을 통한 코드 실행을 막기 위해). 터미널의 git 을 쓰세요.")
        for prot in self._protected():
            try:
                pr = os.path.realpath(prot)
            except OSError:
                continue
            if _norm(real) == _norm(pr) or _norm(real).startswith(_norm(pr) + os.sep):
                raise WorkspaceError(403, "rework 데이터 폴더는 편집기로 수정할 수 없습니다(AI 키·격리 설정이 들어 있습니다).")

    # ---- 트리 --------------------------------------------------------------
    def listdir(self, rel: str = "") -> dict:
        real = self.resolve(rel)
        if not os.path.isdir(real):
            raise WorkspaceError(400, "폴더가 아닙니다.")
        entries, truncated = [], False
        try:
            with os.scandir(real) as it:
                for e in it:
                    if len(entries) >= MAX_DIR_ENTRIES:
                        truncated = True
                        break
                    try:
                        link = e.is_symlink()
                        outside = False
                        if link:
                            target = os.path.realpath(e.path)
                            outside = not self._inside(target)
                            is_dir = (not outside) and os.path.isdir(target)
                        else:
                            is_dir = e.is_dir(follow_symlinks=False)
                        st = e.stat(follow_symlinks=False)
                        name = e.name
                        name.encode("utf-8")                    # JSON 으로 낼 수 없는 이름은 건너뛴다
                    except (OSError, UnicodeEncodeError):
                        continue
                    entries.append({"name": name, "dir": is_dir, "size": 0 if is_dir else st.st_size,
                                    "mtime": int(st.st_mtime), "link": link, "outside": outside})
        except PermissionError:
            raise WorkspaceError(403, "이 폴더를 읽을 권한이 없습니다.")
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower(), x["name"]))
        return {"path": self._rel(real), "entries": entries, "truncated": truncated}

    # ---- 파일 읽기·쓰기 --------------------------------------------------------
    def read(self, rel: str) -> dict:
        real = self.resolve(rel)
        st = os.stat(real)
        if not stat.S_ISREG(st.st_mode):
            raise WorkspaceError(400, "일반 파일이 아닙니다.")
        if st.st_size > MAX_TEXT_BYTES:
            raise WorkspaceError(413, f"파일이 너무 큽니다({st.st_size >> 20}MB > {MAX_TEXT_BYTES >> 20}MB).", binary=False)
        try:
            with open(real, "rb") as fh:
                data = fh.read(MAX_TEXT_BYTES + 1)
        except PermissionError:
            raise WorkspaceError(403, "이 파일을 읽을 권한이 없습니다.")
        if len(data) > MAX_TEXT_BYTES:
            raise WorkspaceError(413, f"파일이 너무 큽니다(> {MAX_TEXT_BYTES >> 20}MB).")
        text, enc = decode_text(data)
        return {"path": self._rel(real), "name": os.path.basename(real), "text": text, "encoding": enc,
                "size": len(data), "mtime_ns": str(os.stat(real).st_mtime_ns), "readonly": not os.access(real, os.W_OK)}

    def stat(self, rel: str) -> dict:
        """파일이 열어 본 뒤에 바뀌었는지 가볍게 확인하는 용도(내용은 읽지 않는다)."""
        real = self.resolve(rel)
        st = os.stat(real)
        return {"path": self._rel(real), "size": st.st_size, "mtime_ns": str(st.st_mtime_ns), "dir": stat.S_ISDIR(st.st_mode)}

    def write(self, rel: str, text, encoding: str = "utf-8", expected_mtime_ns=None, force: bool = False) -> dict:
        if encoding not in ENCODINGS:
            raise WorkspaceError(400, f"지원하지 않는 인코딩입니다: {encoding}")
        if not isinstance(text, str):
            raise WorkspaceError(400, "text 는 문자열이어야 합니다.")
        real = self.resolve(rel)
        self._writable(real)
        try:
            raw = text.encode(encoding)
        except UnicodeEncodeError:
            raise WorkspaceError(400, f"{encoding} 로 표현할 수 없는 문자가 있습니다. UTF-8 로 저장하세요.", encoding_error=True)
        if len(raw) > MAX_TEXT_BYTES:
            raise WorkspaceError(413, f"저장할 내용이 너무 큽니다(> {MAX_TEXT_BYTES >> 20}MB).")
        st = os.stat(real)
        if not stat.S_ISREG(st.st_mode):
            raise WorkspaceError(400, "일반 파일이 아닙니다.")
        expected = _parse_mtime(expected_mtime_ns)
        if expected is not None and not force and st.st_mtime_ns != expected:
            raise WorkspaceError(409, "디스크의 파일이 열어 본 뒤에 바뀌었습니다. 덮어쓰려면 ‘덮어쓰기’를 선택하세요.",
                                 conflict=True, mtime_ns=str(st.st_mtime_ns))
        tmp = os.path.join(os.path.dirname(real), f".{os.path.basename(real)}.rework-{os.urandom(4).hex()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, st.st_mode & 0o777 or 0o600)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(raw)
                    fh.flush()
                    os.fsync(fh.fileno())
                try:
                    os.chmod(tmp, st.st_mode & 0o7777 & ~(stat.S_ISUID | stat.S_ISGID))
                except OSError:
                    pass
                os.replace(tmp, real)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except PermissionError:
            raise WorkspaceError(403, "이 파일(또는 폴더)에 쓸 권한이 없습니다.")
        except OSError as e:
            raise WorkspaceError(400, f"저장하지 못했습니다: {e.strerror or e}")
        new = os.stat(real)
        return {"path": self._rel(real), "size": len(raw), "mtime_ns": str(new.st_mtime_ns)}

    # ---- 파일·폴더 조작 ------------------------------------------------------------
    def create(self, rel: str, kind: str) -> dict:
        if kind not in ("file", "dir"):
            raise WorkspaceError(400, "kind 는 file 또는 dir 이어야 합니다.")
        real = self.resolve(rel, must_exist=False)
        self._writable(real)
        if os.path.lexists(real):
            raise WorkspaceError(409, "같은 이름이 이미 있습니다.")
        try:
            if kind == "dir":
                os.mkdir(real)
            else:
                os.close(os.open(real, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        except PermissionError:
            raise WorkspaceError(403, "이 폴더에 쓸 권한이 없습니다.")
        except OSError as e:
            raise WorkspaceError(400, f"만들지 못했습니다: {e.strerror or e}")
        return {"path": self._rel(real), "dir": kind == "dir"}

    def rename(self, rel: str, to: str) -> dict:
        src = self.resolve(rel)
        self._writable(src)
        dst = self.resolve(to, must_exist=False)
        self._writable(dst)
        if os.path.lexists(dst):
            raise WorkspaceError(409, "같은 이름이 이미 있습니다.")
        if os.path.isdir(src) and (_norm(dst) + os.sep).startswith(_norm(src) + os.sep):
            raise WorkspaceError(400, "폴더를 자기 자신 안으로 옮길 수 없습니다.")
        try:
            os.rename(src, dst)
        except PermissionError:
            raise WorkspaceError(403, "권한이 없습니다.")
        except OSError as e:
            raise WorkspaceError(400, f"이름을 바꾸지 못했습니다: {e.strerror or e}")
        return {"path": self._rel(dst)}

    def delete(self, rel: str, recursive: bool = False) -> dict:
        root = self._need_root()
        parts = self._parts(rel)
        if not parts:
            raise WorkspaceError(403, "작업 폴더 자체는 삭제할 수 없습니다.")
        full = os.path.join(root, *parts)
        if os.path.islink(full):                                 # 링크는 링크만 지운다(대상은 건드리지 않는다)
            parent = self.resolve("/".join(parts[:-1]))
            self._writable(os.path.join(parent, parts[-1]))
            try:
                os.unlink(full)
            except OSError as e:
                raise WorkspaceError(400, f"삭제하지 못했습니다: {e.strerror or e}")
            return {"deleted": "/".join(parts)}
        real = self.resolve(rel)
        self._writable(real)
        try:
            if os.path.isdir(real):
                if os.listdir(real):
                    if not recursive:
                        raise WorkspaceError(409, "비어 있지 않은 폴더입니다. 안의 내용까지 지우려면 확인이 필요합니다.", not_empty=True)
                    shutil.rmtree(real)
                else:
                    os.rmdir(real)
            else:
                os.unlink(real)
        except PermissionError:
            raise WorkspaceError(403, "권한이 없습니다.")
        except OSError as e:
            raise WorkspaceError(400, f"삭제하지 못했습니다: {e.strerror or e}")
        return {"deleted": "/".join(parts)}

    # ---- 검색 ----------------------------------------------------------------
    def search(self, query: str, case_sensitive: bool = False, glob: str = "") -> dict:
        root = self._need_root()
        if not isinstance(query, str) or not query or len(query) > 200 or "\0" in query:
            raise WorkspaceError(400, "검색어는 1~200자여야 합니다.")
        if not isinstance(glob, str) or len(glob) > 100:
            raise WorkspaceError(400, "파일 패턴이 올바르지 않습니다.")
        needle = query if case_sensitive else query.lower()
        deadline = time.monotonic() + SEARCH_SECONDS
        results, scanned, truncated = [], 0, False
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SEARCH_SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
            for fn in sorted(filenames):
                if time.monotonic() > deadline or len(results) >= SEARCH_MAX_RESULTS:
                    truncated = True
                    break
                if glob and not fnmatch.fnmatch(fn, glob):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    st = os.lstat(path)
                    if not stat.S_ISREG(st.st_mode) or st.st_size > SEARCH_MAX_FILE:
                        continue
                    with open(path, "rb") as fh:
                        data = fh.read(SEARCH_MAX_FILE)
                    text, _enc = decode_text(data)
                except (OSError, WorkspaceError):
                    continue
                scanned += 1
                for ln, line in enumerate(text.splitlines(), 1):
                    hay = line if case_sensitive else line.lower()
                    col = hay.find(needle)
                    if col >= 0:
                        shown = line.strip()[:200]
                        try:
                            rel = self._rel(path)
                            rel.encode("utf-8")
                        except (ValueError, UnicodeEncodeError):
                            break
                        results.append({"path": rel, "line": ln, "col": col + 1, "text": shown})
                        if len(results) >= SEARCH_MAX_RESULTS:
                            break
            if truncated:
                break
        return {"query": query, "results": results, "files": scanned, "truncated": truncated}
