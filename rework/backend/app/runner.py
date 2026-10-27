"""빌드·테스트 러너: 작업 폴더의 소스를 컴파일·실행·시험한다.

복구한 의사코드를 고쳐 다시 컴파일하고 돌려 보는 것이 목적이라, 실행되는 코드는 **신뢰할 수 없다고 본다**(악성코드에서 나온 코드일 수 있다).
그래서 기본은 디버거와 같은 bubblewrap 격리 안에서 돌린다:

  1. 작업 폴더를 격리의 /work 로 **복사**한다(.git·데이터 폴더·심볼릭 링크·너무 큰 파일은 빼고). 원본은 건드리지 않는다.
  2. 명령(`sh -c`)을 /work 에서 실행한다. 네트워크 차단·읽기 전용 최소 루트·자원 한도·seccomp 가 걸려 있다.
  3. 끝나면 **새로 생기거나 바뀐 일반 파일만** 검증해서 원본 폴더로 되가져온다(빌드 결과물 등). 심볼릭 링크·장치 파일·폴더 밖 경로·`.git`·크기 초과는
     버리고, 실행 중에 편집기에서 먼저 바뀐 파일은 덮어쓰지 않는다. 삭제는 기본으로 되가져오지 않는다.

격리를 쓸 수 없거나 사용자가 끈 경우(신뢰하는 코드일 때)에는 작업 폴더에서 직접 실행한다. 이때도 환경 변수는 비워 서버의 토큰·API 키가 새지 않게 한다.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time

from . import sandbox as sbx

COPY_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".idea", ".vscode-test"}
MAX_COPY_FILES = 8000
MAX_COPY_BYTES = 192 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024            # 복사해 들일 파일 하나·되가져올 파일 하나의 상한
MAX_BACK_BYTES = 192 * 1024 * 1024
OUTPUT_LIMIT = 2 * 1024 * 1024               # 출력 버퍼(앞부분이 넘치면 버린다)
MAX_COMMAND = 4096
DEFAULT_TIMEOUT = 300
MAX_TIMEOUT = 3600
HOST_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM", "TMPDIR")


class RunError(Exception):
    pass


# ---- 작업 감지 -------------------------------------------------------------------
def _read_head(path: str, limit: int = 64 * 1024) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def detect_tasks(root: str) -> list:
    """폴더 내용으로 쓸 만한 작업(빌드·시험·실행)을 추측해 목록으로 돌려준다. [{id,label,kind,command}]"""
    tasks: list = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return tasks
    files = [n for n in names if os.path.isfile(os.path.join(root, n))]

    def add(kind, label, command):
        tasks.append({"id": f"{kind}:{len(tasks)}", "kind": kind, "label": label, "command": command})

    mk = next((n for n in ("GNUmakefile", "makefile", "Makefile") if n in files), None)
    if mk:
        text = _read_head(os.path.join(root, mk))
        targets = []
        for m in re.finditer(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:(?![=])", text, re.M):
            t = m.group(1)
            if t not in targets and not t.startswith(".") and "%" not in t:
                targets.append(t)
        add("build", "make", "make")
        for t in targets[:14]:
            if t in ("all", "default"):
                continue
            add("test" if t in ("test", "check", "tests") else "build", f"make {t}", f"make {t}")
    if "CMakeLists.txt" in files:
        add("build", "CMake 빌드", "cmake -S . -B build && cmake --build build")
        add("test", "CTest", "ctest --test-dir build --output-on-failure")
    for sh in ("build.sh", "test.sh", "run.sh"):
        if sh in files:
            add("test" if sh == "test.sh" else "build", f"sh {sh}", f"sh {sh}")
    cfiles = [n for n in files if n.endswith((".c", ".cc", ".cpp", ".cxx"))]
    if cfiles and not mk and "CMakeLists.txt" not in files:
        for n in cfiles[:12]:
            stem = n.rsplit(".", 1)[0]
            cc = "gcc" if n.endswith(".c") else "g++"
            add("build", f"컴파일: {n}", f"{cc} -g -O0 -Wall -Wextra {n} -o {stem}")
            add("run", f"컴파일 후 실행: {n}", f"{cc} -g -O0 -Wall -Wextra {n} -o {stem} && ./{stem}")
        if len(cfiles) > 1:
            add("build", "모든 .c 한 번에 컴파일", "gcc -g -O0 -Wall -Wextra *.c -o a.out")
    pys = [n for n in files if n.endswith(".py")]
    has_tests = any(n.startswith("test_") or n.endswith("_test.py") for n in pys) or os.path.isdir(os.path.join(root, "tests"))
    if has_tests:
        add("test", "pytest", "python3 -m pytest -q")
        add("test", "unittest", "python3 -m unittest discover -v")
    for n in pys[:8]:
        if not n.startswith("test_"):
            add("run", f"실행: {n}", f"python3 {n}")
    return tasks


# ---- 진단(문제) 파싱 ---------------------------------------------------------------
_DIAG = re.compile(r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<sev>fatal error|error|warning|note)\s*:\s*(?P<msg>.*)$")
_PYTB = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_LINK = re.compile(r"^(?P<file>[^\s:][^:]*?):\(\.[^)]*\):\s*(?P<msg>.+)$")
_GENERIC_ERR = re.compile(r"^(?:collect2|clang(?:\+\+)?|gcc|g\+\+|make(?:\[\d+\])?|cc1|ld)(?::\s+|:\s+\*\*\*\s+)(?P<msg>(?:fatal )?error:.*|\[.*\]\s*Error \d+.*|.*\*\*\*.*)$", re.I)
_ENTER = re.compile(r"""(?:Entering|Leaving) directory ['`](?P<dir>[^'`]+)['`]""")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def parse_problems(text: str, root_in_sandbox: str = "/work", limit: int = 300) -> list:
    """컴파일러·파이썬 출력에서 `파일:줄:열: 종류: 메시지` 를 뽑는다. 경로는 작업 폴더 기준 상대 경로로 바꾼다."""
    out: list = []
    cwd = ""                                           # make -C 로 들어간 하위 폴더(작업 폴더 기준)
    seen = set()
    for raw in _ANSI.sub("", text).splitlines():
        m = _ENTER.search(raw)
        if m and raw.lstrip().startswith(("make", "gmake")):
            d = m.group("dir").rstrip("/")
            if "Entering" in raw:
                cwd = d[len(root_in_sandbox):].lstrip("/") if d.startswith(root_in_sandbox) else ""
            else:
                cwd = ""
            continue
        ld = _LINK.match(raw)
        if ld:                                          # `a.c:(.text+0x13): undefined reference to ...` (줄 번호 없음)
            f = ld.group("file")
            if f.startswith(root_in_sandbox + "/"):
                f = f[len(root_in_sandbox) + 1:]
            if not os.path.isabs(f) and ".." not in f.split("/"):
                key = (f, 1, "ld", ld.group("msg"))
                if key not in seen:
                    seen.add(key)
                    out.append({"file": f, "line": 1, "col": 1, "severity": "error", "message": ld.group("msg").strip()})
            continue
        gen = _GENERIC_ERR.match(raw)
        if gen:                                          # 위치 없는 도구 오류(collect2·make)
            msg = gen.group("msg").strip()
            key = ("", 0, "gen", msg)
            if key not in seen:
                seen.add(key)
                out.append({"file": "", "line": 0, "col": 0, "severity": "error", "message": msg})
            continue
        d_ = _DIAG.match(raw)
        if d_:
            f = d_.group("file").strip()
            if f.startswith(root_in_sandbox + "/"):
                f = f[len(root_in_sandbox) + 1:]
            elif not os.path.isabs(f) and cwd:
                f = os.path.normpath(os.path.join(cwd, f))
            f = f.lstrip("./") if f.startswith("./") else f
            if os.path.isabs(f) or ".." in f.split("/"):
                continue                                # 시스템 헤더 등 작업 폴더 밖
            key = (f, d_.group("line"), d_.group("col"), d_.group("sev"), d_.group("msg"))
            if key in seen:
                continue
            seen.add(key)
            out.append({"file": f, "line": int(d_.group("line")), "col": int(d_.group("col") or 1),
                        "severity": {"fatal error": "error"}.get(d_.group("sev"), d_.group("sev")), "message": d_.group("msg").strip()})
        else:
            p = _PYTB.match(raw)
            if p:
                f = p.group("file")
                if f.startswith(root_in_sandbox + "/"):
                    f = f[len(root_in_sandbox) + 1:]
                if os.path.isabs(f) or ".." in f.split("/"):
                    continue
                key = (f, p.group("line"), "tb")
                if key in seen:
                    continue
                seen.add(key)
                out.append({"file": f, "line": int(p.group("line")), "col": 1, "severity": "trace", "message": "파이썬 호출 추적"})
        if len(out) >= limit:
            break
    return out


_SUMMARY_PATTERNS = (
    re.compile(r"(?P<s>=+ .*?(?:passed|failed|error|no tests ran).*? =+)\s*$", re.I),            # pytest
    re.compile(r"(?P<s>\d+% tests passed, \d+ tests failed out of \d+)", re.I),                    # ctest
    re.compile(r"(?P<s>Ran \d+ tests? in [\d.]+s)", re.I),                                         # unittest
    re.compile(r"(?P<s>^(?:OK|FAILED)(?: \(.*\))?$)", re.M),
)


def summarize(text: str, exit_code, status: str) -> str:
    tail = "\n".join(_ANSI.sub("", text).splitlines()[-60:])
    found = []
    for pat in _SUMMARY_PATTERNS:
        m = None
        for m in pat.finditer(tail):
            pass
        if m:
            found.append(m.group("s").strip("= ").strip())
    head = {"done": ("성공" if exit_code == 0 else f"실패(종료 코드 {exit_code})"), "timeout": "시간 초과로 중단", "stopped": "사용자가 중단",
            "limit": "자원 한도 초과로 중단", "error": "실행하지 못함"}.get(status, status)
    return head + (" · " + " · ".join(found[:3]) if found else "")


# ---- 복사(들이기·되가져오기) ----------------------------------------------------------
def _skip_zone(rel: str, protected: list, root: str) -> bool:
    parts = rel.split("/")
    if any(p in COPY_SKIP_DIRS for p in parts):
        return True
    full = os.path.join(root, rel)
    return any(os.path.commonpath([full, p]) == p for p in protected if p)


def copy_in(root: str, dest: str, uid: int | None, gid: int | None, protected: list) -> dict:
    """작업 폴더를 dest 로 복사한다. 반환: 스냅샷 {상대경로: (크기, mtime_ns)}, 빠뜨린 것 목록."""
    snap: dict = {}
    skipped: list = []
    total = 0
    count = 0
    root = os.path.realpath(root)
    protected = [os.path.realpath(p) for p in protected if p]
    for cur, dirs, files in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(cur, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep = []
        for d in dirs:
            rel = f"{rel_dir}/{d}" if rel_dir else d
            full = os.path.join(cur, d)
            if os.path.islink(full) or _skip_zone(rel, protected, root):
                skipped.append(rel + "/")
                continue
            keep.append(d)
            os.makedirs(os.path.join(dest, rel), exist_ok=True)
            if uid is not None:
                os.chown(os.path.join(dest, rel), uid, gid)
        dirs[:] = keep
        for f in files:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            full = os.path.join(cur, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or _skip_zone(rel, protected, root):
                skipped.append(rel)
                continue
            if st.st_size > MAX_FILE_BYTES:
                skipped.append(f"{rel} ({st.st_size >> 20}MB 초과)")
                continue
            count += 1
            total += st.st_size
            if count > MAX_COPY_FILES or total > MAX_COPY_BYTES:
                raise RunError(f"작업 폴더가 너무 큽니다(파일 {MAX_COPY_FILES}개·{MAX_COPY_BYTES >> 20}MB 이하만 격리로 복사합니다). 하위 폴더를 작업 폴더로 여세요.")
            tgt = os.path.join(dest, rel)
            shutil.copy2(full, tgt)
            if uid is not None:
                os.chown(tgt, uid, gid)
            snap[rel] = (st.st_size, st.st_mtime_ns)
    return {"snapshot": snap, "skipped": skipped, "files": count, "bytes": total}


def _safe_rel(rel: str) -> bool:
    if not rel or rel.startswith("/") or "\\" in rel or any(ord(c) < 32 or c == "\x7f" for c in rel):
        return False                                     # 개행·제어 문자가 든 이름은 화면·셸에서 속임수가 되므로 되가져오지 않는다
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return all(p not in ("", ".", "..") and len(p) <= 255 for p in rel.split("/"))


def copy_back(job_dir: str, root: str, snapshot: dict, protected: list, sync_deletes: bool = False) -> list:
    """샌드박스가 만든 결과를 작업 폴더로 되가져온다. 반환: [{path, status, size?}] (status: new·modified·conflict·skipped·deleted)."""
    report: list = []
    root = os.path.realpath(root)
    protected = [os.path.realpath(p) for p in protected if p]
    total = 0
    present = set()
    for cur, dirs, files in os.walk(job_dir, followlinks=False):
        rel_dir = os.path.relpath(cur, job_dir)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(cur, d))]
        for f in files:
            rel = f"{rel_dir}/{f}" if rel_dir else f
            src = os.path.join(cur, f)
            try:
                st = os.lstat(src)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                report.append({"path": rel, "status": "skipped", "reason": "일반 파일이 아님(링크·장치)"})
                continue
            present.add(rel)
            old = snapshot.get(rel)
            if old is not None and old == (st.st_size, st.st_mtime_ns):
                continue                                                      # 그대로
            if not _safe_rel(rel) or _skip_zone(rel, protected, root):
                report.append({"path": rel, "status": "skipped", "reason": "금지 구역(.git·데이터 폴더 등)"})
                continue
            if st.st_size > MAX_FILE_BYTES:
                report.append({"path": rel, "status": "skipped", "reason": f"{st.st_size >> 20}MB 초과"})
                continue
            total += st.st_size
            if total > MAX_BACK_BYTES:
                report.append({"path": rel, "status": "skipped", "reason": "되가져오는 총량 초과"})
                continue
            dst = os.path.join(root, rel)
            parent = os.path.dirname(dst)
            try:
                os.makedirs(parent, exist_ok=True)
                if os.path.realpath(parent) != parent and not os.path.realpath(parent).startswith(root + os.sep) \
                        and os.path.realpath(parent) != root:
                    report.append({"path": rel, "status": "skipped", "reason": "폴더 밖을 가리킴"})
                    continue
                if os.path.islink(dst):
                    report.append({"path": rel, "status": "skipped", "reason": "대상이 심볼릭 링크"})
                    continue
                exists = os.path.exists(dst)
                if exists:
                    cur_st = os.stat(dst)
                    if old is None or (cur_st.st_size, cur_st.st_mtime_ns) != old:
                        report.append({"path": rel, "status": "conflict", "reason": "실행 중에 원본이 먼저 바뀌어 덮어쓰지 않음"})
                        continue
                tmp = os.path.join(parent, f".rework-run-{os.getpid()}-{threading.get_ident()}.tmp")
                shutil.copyfile(src, tmp)
                os.chmod(tmp, 0o755 if st.st_mode & 0o111 else 0o644)
                os.replace(tmp, dst)
                report.append({"path": rel, "status": "modified" if exists else "new", "size": st.st_size})
            except OSError as e:
                report.append({"path": rel, "status": "skipped", "reason": str(e)})
    if sync_deletes:
        for rel, old in snapshot.items():
            if rel in present or not _safe_rel(rel):
                continue
            dst = os.path.join(root, rel)
            try:
                cur_st = os.lstat(dst)
                if stat.S_ISREG(cur_st.st_mode) and (cur_st.st_size, cur_st.st_mtime_ns) == old:
                    os.unlink(dst)
                    report.append({"path": rel, "status": "deleted"})
            except OSError:
                pass
    return report


# ---- 작업 ------------------------------------------------------------------------
class Job:
    def __init__(self, command: str, root: str):
        self.command, self.root = command, root
        self.id = int(time.time() * 1000)
        self.status = "running"            # running | done | timeout | stopped | limit | error
        self.exit_code = None
        self.sandboxed = False
        self.sandbox_info = None
        self.started = time.time()
        self.ended = None
        self.error = None
        self._buf: list = []
        self._len = 0
        self._dropped = 0                  # 앞에서 버린 글자 수
        self._lock = threading.Lock()
        self.problems: list = []
        self.changes: list = []
        self.copy_info: dict = {}
        self.summary = ""
        self.proc = None
        self.launch = None
        self._stop_reason = None

    def push(self, text: str):
        with self._lock:
            self._buf.append(text)
            self._len += len(text)
            while self._len > OUTPUT_LIMIT and len(self._buf) > 1:
                d = self._buf.pop(0)
                self._len -= len(d)
                self._dropped += len(d)

    def read(self, since: int = 0) -> tuple:
        """since(전체 출력 기준 글자 위치) 이후의 텍스트와 새 위치. 버려진 앞부분은 건너뛴다."""
        with self._lock:
            text = "".join(self._buf)
            base = self._dropped
        start = max(0, since - base)
        return text[start:], base + len(text), base > since

    def full_text(self) -> str:
        with self._lock:
            return "".join(self._buf)

    def snapshot(self, since: int = 0) -> dict:
        text, nxt, lost = self.read(since)
        end = self.ended or time.time()
        return {"id": self.id, "command": self.command, "status": self.status, "exit_code": self.exit_code,
                "sandboxed": self.sandboxed, "sandbox": self.sandbox_info, "output": text, "offset": nxt, "output_truncated": lost,
                "elapsed": round(end - self.started, 2), "error": self.error, "problems": self.problems, "changes": self.changes,
                "copy": {k: v for k, v in self.copy_info.items() if k != "snapshot"}, "summary": self.summary}


class Runner:
    """한 번에 작업 하나."""

    def __init__(self):
        self.job: Job | None = None
        self._lock = threading.Lock()

    # ---- 시작 ----
    def start(self, root: str, command: str, policy: sbx.SandboxPolicy, protected: list, *, timeout: int = DEFAULT_TIMEOUT,
              sandbox: bool = True, sync_deletes: bool = False) -> Job:
        if not isinstance(command, str) or not command.strip() or "\0" in command or len(command) > MAX_COMMAND:
            raise RunError("실행할 명령이 올바르지 않습니다(비었거나 너무 깁니다).")
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        with self._lock:
            if self.job is not None and self.job.status == "running":
                raise RunError("이미 실행 중인 작업이 있습니다. 끝나길 기다리거나 중단하세요.")
            job = Job(command.strip(), root)
            self.job = job
        try:
            if sandbox:
                self._start_sandboxed(job, root, policy, protected, timeout, sync_deletes)
            else:
                self._start_direct(job, root, timeout)
        except BaseException as e:                                    # noqa: BLE001
            job.status = "error"
            job.error = str(e)
            job.ended = time.time()
            job.summary = summarize("", None, "error")
            if isinstance(e, (RunError, sbx.SandboxError, OSError)):
                raise e if isinstance(e, RunError) else RunError(str(e))
            raise
        return job

    def _start_sandboxed(self, job, root, policy, protected, timeout, sync_deletes):
        pol = policy
        if not pol.enabled:
            raise RunError("격리가 꺼져 있습니다. 격리 없이 실행하려면 ‘격리 없이 실행’을 선택하세요.")
        launch = sbx.prepare_job(pol, ["/bin/sh", "-c", job.command])
        job.launch = launch
        try:
            info = copy_in(root, launch.job_dir, launch.uid, launch.uid if launch.uid is not None else None, protected)
            job.copy_info = info
            proc = subprocess.Popen(launch.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    pass_fds=launch.pass_fds, start_new_session=True, **launch.popen_kwargs)
        except BaseException:
            launch.cleanup()
            raise
        job.proc = proc
        job.sandboxed = True
        job.sandbox_info = launch.info

        def on_limit(reason):
            job._stop_reason = ("limit", reason)
            job.push(f"\n[격리] {reason} 을(를) 넘어 작업을 중단합니다.\n")
            self._kill(job)

        launch.attach(proc, on_limit=on_limit)
        threading.Thread(target=self._pump, args=(job, timeout, sync_deletes, protected), daemon=True, name="rework-run").start()

    def _start_direct(self, job, root, timeout):
        env = {k: os.environ[k] for k in HOST_ENV_KEEP if k in os.environ}
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        env["HOME"] = env.get("HOME") or root
        job.proc = subprocess.Popen(["/bin/sh", "-c", job.command], cwd=root, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        job.sandboxed = False
        job.push("[주의] 격리 없이 이 컴퓨터에서 직접 실행합니다.\n")
        threading.Thread(target=self._pump, args=(job, timeout, False, []), daemon=True, name="rework-run").start()

    # ---- 출력 수집·마무리 ----
    def _pump(self, job: Job, timeout: int, sync_deletes: bool, protected: list):
        proc = job.proc
        timer = threading.Timer(timeout, lambda: (setattr(job, "_stop_reason", ("timeout", f"{timeout}초")), self._kill(job)))
        timer.daemon = True
        timer.start()
        import codecs
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = os.read(proc.stdout.fileno(), 8192)
                if not chunk:
                    break
                job.push(dec.decode(chunk))
            job.push(dec.decode(b"", final=True))
            proc.wait()
        except Exception as e:                                          # noqa: BLE001
            job.push(f"\n[러너 오류] {e}\n")
        finally:
            timer.cancel()
        job.exit_code = proc.returncode
        reason = job._stop_reason
        try:
            if job.sandboxed and job.launch is not None:
                job.launch.reap()
                # 실패하거나 중단된 경우에도 만들어진 파일은 검증해서 가져온다(컴파일 결과 등)
                job.changes = copy_back(job.launch.job_dir, job.root, job.copy_info.get("snapshot", {}), protected, sync_deletes)
        except Exception as e:                                          # noqa: BLE001
            job.push(f"\n[러너 오류] 결과를 되가져오지 못했습니다: {e}\n")
        finally:
            if job.launch is not None:
                job.launch.cleanup()
        job.ended = time.time()
        job.status = reason[0] if reason else "done"
        text = job.full_text()
        job.problems = parse_problems(text)
        job.summary = summarize(text, job.exit_code, job.status)
        if reason and reason[0] == "timeout":
            job.summary = f"시간 초과({reason[1]})로 중단"

    def _kill(self, job: Job):
        proc = job.proc
        if proc is None:
            return
        if job.launch is not None:
            job.launch.reap()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.kill()
        except OSError:
            pass

    def stop(self) -> bool:
        job = self.job
        if job is None or job.status != "running":
            return False
        job._stop_reason = job._stop_reason or ("stopped", "사용자")
        self._kill(job)
        return True

    def state(self, since: int = 0) -> dict:
        job = self.job
        if job is None:
            return {"status": "none"}
        return job.snapshot(since)

    def shutdown(self):
        try:
            self.stop()
        except Exception:                                               # noqa: BLE001
            pass
