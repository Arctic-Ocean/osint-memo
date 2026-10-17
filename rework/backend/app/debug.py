"""GDB/MI 기반 동적 디버거 엔진.

로드된 바이너리를 GDB의 Machine Interface(MI)로 제어한다. 중단점, 실행 제어
(run·continue·step·next·stepi·finish·interrupt), 레지스터·스택·지역변수 조회,
그리고 **런타임 변수·레지스터·메모리 쓰기**를 지원한다.

주의: 디버깅은 대상 바이너리를 **실제로 실행**한다. 신뢰할 수 없는 파일은
격리된 VM/컨테이너에서 다뤄야 한다(이 도구는 격리를 제공하지 않는다).
서버는 127.0.0.1에만 바인딩되는 로컬 전용이다.

구현 메모:
- MI 명령에 토큰(숫자 접두)을 붙여 결과 레코드(`<tok>^done` 등)와 매칭한다.
- 리더 스레드가 gdb stdout을 계속 읽어 결과는 대기 중 명령에 전달하고,
  비동기 레코드(`*stopped` 등)는 상태에 반영한다.
- 대상 프로그램의 입출력은 별도 pty로 분리해 MI 채널 오염을 막고 캡처한다.
"""
from __future__ import annotations

import atexit
import os
import pty
import select
import signal
import subprocess
import threading
import time
import weakref


class DebugError(Exception):
    pass


def mi_quote(s: str) -> str:
    """MI c-string 인용. 역슬래시·큰따옴표를 이스케이프하고, 줄바꿈 계열은 거부한다
    (개행이 섞이면 MI 명령이 둘로 갈라져 세션이 어긋난다)."""
    s = str(s)
    if any(c in s for c in "\r\n\x00"):
        raise DebugError("개행·NUL 문자는 사용할 수 없습니다.")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# 살아 있는 세션 추적: 서버가 종료돼도 gdb·대상 프로세스가 고아로 남지 않게 한다.
_LIVE_SESSIONS = weakref.WeakSet()


@atexit.register
def _cleanup_all_sessions():
    for sess in list(_LIVE_SESSIONS):
        try:
            sess.stop()
        except Exception:
            pass


# ---- MI 값 파서 ----------------------------------------------------------
# MI 결과는 `name=value` 목록이며, value는 const("..."), tuple({...}), list([...]).

_SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f",
               "v": "\v", "e": "\x1b", '"': '"', "\\": "\\"}
_OCT = "01234567"
_HEX = "0123456789abcdefABCDEF"


def _parse_cstring(s: str, i: int):
    # s[i] == '"'
    i += 1
    out = []
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 1
            if i >= n:
                break
            e = s[i]
            if e in _OCT:
                # gdb는 비인쇄 바이트를 8진 \NNN(최대 3자리)으로 낸다: \033 → ESC
                j = i
                while j < n and j < i + 3 and s[j] in _OCT:
                    j += 1
                out.append(chr(int(s[i:j], 8) & 0xFF))
                i = j
                continue
            if e == "x" and i + 1 < n and s[i + 1] in _HEX:
                j = i + 1
                while j < n and j < i + 3 and s[j] in _HEX:
                    j += 1
                out.append(chr(int(s[i + 1:j], 16)))
                i = j
                continue
            out.append(_SIMPLE_ESC.get(e, e))
        elif c == '"':
            i += 1
            return "".join(out), i
        else:
            out.append(c)
        i += 1
    return "".join(out), i


def _parse_value(s: str, i: int):
    if i >= len(s):
        return "", i
    c = s[i]
    if c == '"':
        return _parse_cstring(s, i)
    if c == "{":
        return _parse_tuple(s, i)
    if c == "[":
        return _parse_list(s, i)
    # bareword (드물게 비인용 값)
    j = i
    while j < len(s) and s[j] not in ',}]':
        j += 1
    return s[i:j], j


def _parse_result(s: str, i: int):
    """name=value 를 파싱해 (name, value, new_i)."""
    j = i
    while j < len(s) and s[j] not in "=,}]":
        j += 1
    name = s[i:j]
    if j < len(s) and s[j] == "=":
        val, k = _parse_value(s, j + 1)
        return name, val, k
    return name, None, j   # 이름만(값 없음)


def _parse_tuple(s: str, i: int):
    # s[i] == '{'
    i += 1
    d = {}
    n = len(s)
    while i < n and s[i] != "}":
        start = i
        name, val, i = _parse_result(s, i)
        if name or val is not None:
            d[name] = val
        if i < n and s[i] == ",":
            i += 1
        if i == start:
            i += 1   # 짝이 안 맞는 닫는 괄호(`]`) 등 진행이 없는 입력은 한 글자 건너뛴다
    if i < n and s[i] == "}":
        i += 1
    return d, i


def _parse_list(s: str, i: int):
    # s[i] == '['
    i += 1
    out = []
    n = len(s)
    while i < n and s[i] != "]":
        start = i
        if s[i] in '"{[':
            val, i = _parse_value(s, i)
            out.append(val)
        else:
            # name=value 형태의 요소(예: frame={...}) → value만 취함
            name, val, i = _parse_result(s, i)
            if val is not None or name:
                out.append(val if val is not None else name)
        if i < n and s[i] == ",":
            i += 1
        if i == start:
            i += 1   # 짝이 안 맞는 닫는 괄호(`}`) 등 진행이 없는 입력은 한 글자 건너뛴다
    if i < n and s[i] == "]":
        i += 1
    return out, i


def parse_mi_results(s: str) -> dict:
    """`,`로 시작하는 결과 꼬리(예: `,bkpt={...},foo="x"`)를 dict로."""
    d = {}
    i = 0
    n = len(s)
    while i < n:
        if s[i] == ",":
            i += 1
            continue
        name, val, i = _parse_result(s, i)
        if name:
            d[name] = val
        else:
            i += 1
    return d


# ---- 디버그 세션 ---------------------------------------------------------
class DebugSession:
    def __init__(self, binary_path: str, program=None):
        if not os.path.isfile(binary_path):
            raise DebugError(f"파일이 없습니다: {binary_path}")
        self.path = binary_path
        self.program = program          # 정적 분석 Program(이름/바이어스 해석용)
        self.proc = None
        self._lock = threading.Lock()   # 토큰 발급 + stdin 쓰기 직렬화
        self._plock = threading.Lock()  # _pending 접근 보호(I/O 중에는 잡지 않는다)
        self._stop_lock = threading.Lock()
        self._closed = False            # stop() 완료 여부(멱등성)
        self._closing = False
        self._token = 0
        self._pending = {}              # token -> {"event": Event, "result": dict}
        self.inferior_pid = None
        self.console = []               # gdb 콘솔/로그 스트림(~, &)
        self.target_out = []            # 대상 프로그램 stdout/stderr(pty)
        self.events = []                # 최근 비동기 이벤트 로그
        self.status = "loaded"          # loaded|running|stopped|exited|error
        self.last_stop = None           # 마지막 *stopped 정보
        self.exit_code = None
        self._stopped_ev = threading.Event()
        self._pty_master = None
        self._reader = None
        self._pty_reader = None
        self._alive = False
        self.load_bias = None           # 런타임주소 - 정적주소(PIE 보정)

    # ---- 수명 ----------------------------------------------------------
    def start(self):
        # 디버깅은 대상을 실행하므로 소유자 실행 비트를 보장한다
        # (업로드 사본은 0644로 저장돼 실행 권한이 없을 수 있음).
        try:
            st = os.stat(self.path)
            os.chmod(self.path, st.st_mode | 0o100)
        except Exception:
            pass
        master, slave = pty.openpty()
        self._pty_master = master
        self._pty_slave = slave   # 세션 동안 열어 둔다(조기 EOF 방지)
        try:
            self.proc = subprocess.Popen(
                ["gdb", "--quiet", "--nx", "--interpreter=mi3", self.path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True)  # 새 프로세스 그룹(정리용)
            self._alive = True
            _LIVE_SESSIONS.add(self)
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            self._pty_reader = threading.Thread(target=self._pty_loop, args=(master,), daemon=True)
            self._pty_reader.start()
            time.sleep(0.15)   # 배너 소화
            # 대상 입출력을 pty로 연결(MI 채널 오염 방지 + 캡처).
            # slave fd를 계속 열어 둬야 inferior가 열기 전 master가 EOF를 받지 않는다.
            try:
                tty_name = os.ttyname(slave)
                self._send(f"-inferior-tty-set {tty_name}", timeout=2)
            except Exception:
                pass
            self._send("-gdb-set mi-async on", timeout=2)
            self._send("-gdb-set pagination off", timeout=2)
            self._send("-gdb-set confirm off", timeout=2)
        except BaseException:
            # gdb 미설치(FileNotFoundError)·설정 실패 등: fd·프로세스를 남기지 않는다
            self.stop()
            raise
        return self.snapshot()

    def stop(self):
        """세션 종료. 멱등이며 동시 호출에 안전하다. 반환 시점에 gdb와 대상 프로세스가
        사라져 있도록 기다린다(실행 중 바이너리를 곧바로 덮어써도 ETXTBSY가 나지 않게)."""
        with self._stop_lock:
            if self._closed:
                return
            self._closed = True
            self._closing = True
            self._alive = False
            proc = self.proc
            try:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.stdin.write("-gdb-exit\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                self._kill_all()
                # 리더 스레드가 끝난 뒤에만 fd를 닫는다(번호 재사용으로 남의 fd를 읽는 경쟁 방지)
                for t in (self._reader, self._pty_reader):
                    if t is not None and t is not threading.current_thread():
                        t.join(timeout=2.0)
                if proc is not None:
                    for stream in (proc.stdin, proc.stdout):
                        try:
                            if stream:
                                stream.close()
                        except Exception:
                            pass
                for fd_attr in ("_pty_master", "_pty_slave"):
                    fd = getattr(self, fd_attr, None)
                    setattr(self, fd_attr, None)
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                _LIVE_SESSIONS.discard(self)
            self.status = "exited"

    def _kill_all(self):
        """gdb 프로세스 그룹과 대상 프로세스를 확실히 종료하고 사라질 때까지 기다린다."""
        proc = self.proc
        if proc is not None:
            try:
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=3.0)
            except Exception:
                pass
        pid = self.inferior_pid
        if pid:
            self._kill_inferior(pid)

    def _kill_inferior(self, pid: int):
        """대상 프로세스가 아직 남아 있으면(gdb가 비정상 종료한 경우) 종료한다.
        pid 재사용을 피하려고 /proc/<pid>/exe 가 디버깅 대상 파일일 때만 죽인다."""
        def _is_ours():
            try:
                exe = os.readlink(f"/proc/{pid}/exe")
            except OSError:
                return False
            if exe.endswith(" (deleted)"):
                exe = exe[: -len(" (deleted)")]
            return os.path.realpath(exe) == os.path.realpath(self.path)

        def _gone():
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    stat = fh.read()
                return stat[stat.rindex(")") + 2:][:1] == "Z"   # 좀비는 파일을 잡지 않는다
            except (OSError, ValueError):
                return True

        if _gone() or not _is_ours():
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not _gone():
            time.sleep(0.02)

    # ---- 리더 스레드 ---------------------------------------------------
    def _read_loop(self):
        try:
            while self._alive:
                try:
                    line = self.proc.stdout.readline()
                except Exception:
                    break
                if line == "":
                    break
                try:
                    self._handle_line(line.rstrip("\n"))
                except Exception:
                    continue   # 한 줄의 파싱·상태 예외가 리더를 죽이지 못하게 한다
        finally:
            self._alive = False
            # gdb가 끝났으면 디버깅도 끝난 것이다(status가 running에 고착되지 않게)
            if self.status in ("loaded", "running", "stopped"):
                self.status = "exited"
            # 어떤 이유로 끝나든 대기 중인 명령과 stop 대기자를 깨운다
            with self._plock:
                pending = list(self._pending.values())
                self._pending.clear()
            for slot in pending:
                slot["result"] = {"class": "error", "results": {"msg": "gdb 종료됨"}}
                slot["event"].set()
            self._stopped_ev.set()

    def _pty_loop(self, master):
        while self._alive and not self._closing:
            try:
                ready, _, _ = select.select([master], [], [], 0.2)
                if not ready:
                    continue
                data = os.read(master, 4096)
            except (OSError, ValueError):
                break
            if not data:
                break
            text = data.decode("utf-8", "replace")
            self.target_out.append(text)
            if len(self.target_out) > 2000:
                del self.target_out[:1000]

    def _handle_line(self, line: str):
        if not line or line == "(gdb) ":
            return
        # 토큰(선택) + 레코드 종류 문자
        i = 0
        while i < len(line) and line[i].isdigit():
            i += 1
        token = line[:i] or None
        if i >= len(line):
            return
        kind = line[i]
        rest = line[i + 1:]
        if kind == "^":
            # 결과 레코드: class[,results]
            cls, _, tail = rest.partition(",")
            results = parse_mi_results("," + tail) if tail else {}
            if token:
                with self._plock:
                    slot = self._pending.pop(token, None)
                if slot is not None:
                    slot["result"] = {"class": cls, "results": results}
                    slot["event"].set()
        elif kind == "*":
            cls, _, tail = rest.partition(",")
            results = parse_mi_results("," + tail) if tail else {}
            self._on_exec_async(cls, results)
        elif kind == "=":
            cls, _, tail = rest.partition(",")
            results = parse_mi_results("," + tail) if tail else {}
            self._on_notify_async(cls, results)
        elif kind in "~@&":
            # 스트림 출력(인용 c-string)
            try:
                val, _ = _parse_cstring(rest, 0)
            except Exception:
                val = rest
            if kind == "@":
                self.target_out.append(val)
            else:
                self.console.append(val)
                if len(self.console) > 2000:
                    del self.console[:1000]

    def _on_exec_async(self, cls, results):
        if cls == "running":
            self.status = "running"
            self.last_stop = None
        elif cls == "stopped":
            reason = results.get("reason", "")
            self.last_stop = results
            if reason.startswith("exited"):
                self.status = "exited"
                self.exit_code = results.get("exit-code", "0" if reason == "exited-normally" else None)
            else:
                self.status = "stopped"
                self._update_bias(results.get("frame"))
            self.events.append({"t": "stopped", "reason": reason,
                                "frame": results.get("frame")})
            if len(self.events) > 500:
                del self.events[:250]
            self._stopped_ev.set()

    def _on_notify_async(self, cls, results):
        self.events.append({"t": cls})
        if cls == "thread-group-started":
            try:
                self.inferior_pid = int(results.get("pid"))
            except (TypeError, ValueError):
                pass
        elif cls == "thread-group-exited":
            self.inferior_pid = None
            self.status = "exited"
            self.exit_code = results.get("exit-code")
            self._stopped_ev.set()

    def _update_bias(self, frame):
        """PIE 로드 바이어스 추정: 멈춘 함수의 런타임 주소 - 정적 주소."""
        if not frame or not self.program:
            return
        func = frame.get("func")
        addr = frame.get("addr")
        if not func or not addr:
            return
        try:
            rt = int(addr, 16)
        except Exception:
            return
        st = self.program._func_name_to_addr(func) if hasattr(self.program, "_func_name_to_addr") else None
        if st:
            self.load_bias = rt - st

    # ---- 명령 전송 -----------------------------------------------------
    def _send(self, mi_command: str, timeout: float = 10.0) -> dict:
        if self._closed or not self._alive or not self.proc or self.proc.poll() is not None:
            raise DebugError("디버그 세션이 활성 상태가 아닙니다.")
        # 모든 MI 명령이 지나는 단일 지점: 개행이 섞이면 명령이 둘로 갈라져
        # 토큰 없는 두 번째 명령이 실행되고 결과 매칭이 어긋난다.
        if any(c in mi_command for c in "\r\n\x00"):
            raise DebugError("MI 명령에 개행·NUL 문자를 포함할 수 없습니다.")
        with self._lock:
            self._token += 1
            tok = str(self._token)
            ev = threading.Event()
            # 슬롯 객체 참조를 보관한다. 리더 스레드가 _pending에서 pop하더라도
            # 같은 dict 객체에 result를 채우므로 여기서 읽을 수 있다.
            slot = {"event": ev, "result": None}
            with self._plock:
                self._pending[tok] = slot
            try:
                self.proc.stdin.write(f"{tok}{mi_command}\n")
                self.proc.stdin.flush()
            except Exception as e:
                with self._plock:
                    self._pending.pop(tok, None)
                raise DebugError(f"명령 전송 실패: {e}")
        if not ev.wait(timeout):
            with self._plock:
                self._pending.pop(tok, None)
            raise DebugError(f"명령 응답 시간 초과: {mi_command}")
        res = slot.get("result") or {}
        if res.get("class") == "error":
            raise DebugError(res.get("results", {}).get("msg", "gdb 오류"))
        return res

    def console_cmd(self, text: str) -> dict:
        """사용자가 입력한 원시 gdb 명령(MI로 래핑)."""
        before = len(self.console)
        self._send(f'-interpreter-exec console {mi_quote(text)}', timeout=20)
        return {"output": "".join(self.console[before:])}

    # ---- 실행 제어 -----------------------------------------------------
    # 멈출 때까지 요청을 오래 붙들지 않는다: 시간 안에 안 멈추면 status=running 으로
    # 돌려주고, 프론트가 상태를 폴링해 *stopped 를 잡는다(입력 대기·긴 루프 등).
    EXEC_WAIT = 8.0

    def _exec(self, mi_command: str, wait_stop: bool, timeout: float = EXEC_WAIT) -> dict:
        if wait_stop:
            self._stopped_ev.clear()
        self._send(mi_command, timeout=10)
        if wait_stop:
            # 시간 초과 시 status를 직접 쓰지 않는다: 리더가 *running/*stopped 로 갱신하며,
            # 여기서 덮어쓰면 방금 도착한 stopped 를 running 으로 되돌릴 수 있다.
            self._stopped_ev.wait(timeout)
        return self.snapshot()

    def run(self, args: str = "") -> dict:
        if args:
            self._send(f"-exec-arguments {args}")
        self.exit_code = None   # 종료 뒤 다시 실행할 때 이전 종료코드가 남지 않게
        return self._exec("-exec-run", wait_stop=True)

    def cont(self):   return self._exec("-exec-continue", wait_stop=True)
    def step(self):   return self._exec("-exec-step", wait_stop=True)
    def next(self):   return self._exec("-exec-next", wait_stop=True)
    def stepi(self):  return self._exec("-exec-step-instruction", wait_stop=True)
    def nexti(self):  return self._exec("-exec-next-instruction", wait_stop=True)
    def finish(self): return self._exec("-exec-finish", wait_stop=True)

    def interrupt(self):
        # 실행 중인 inferior를 멈춘다
        self._stopped_ev.clear()
        try:
            self._send("-exec-interrupt --all", timeout=5)
        except DebugError:
            # 폴백: inferior에 SIGINT
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
            except Exception:
                pass
        self._stopped_ev.wait(5)
        return self.snapshot()

    # ---- 중단점 --------------------------------------------------------
    def break_insert(self, location: str) -> dict:
        res = self._send(f"-break-insert {location}")
        return res.get("results", {}).get("bkpt", {})

    def break_delete(self, number) -> dict:
        self._send(f"-break-delete {number}")
        return {"deleted": number}

    def break_toggle(self, number, enable: bool) -> dict:
        self._send(f"-break-{'enable' if enable else 'disable'} {number}")
        return {"number": number, "enabled": enable}

    def breakpoints(self) -> list:
        res = self._send("-break-list")
        table = res.get("results", {}).get("BreakpointTable", {})
        body = table.get("body", []) if isinstance(table, dict) else []
        out = []
        for b in body:
            if isinstance(b, dict):
                out.append({"number": b.get("number"), "addr": b.get("addr"),
                            "func": b.get("func"), "file": b.get("file"),
                            "line": b.get("line"), "enabled": b.get("enabled") == "y",
                            "what": b.get("what"), "times": b.get("times")})
        return out

    # ---- 상태 조회 -----------------------------------------------------
    def registers(self) -> list:
        names = self._send("-data-list-register-names").get("results", {}).get("register-names", [])
        vals = self._send("-data-list-register-values x").get("results", {}).get("register-values", [])
        by_num = {}
        for v in vals:
            if isinstance(v, dict):
                by_num[v.get("number")] = v.get("value")
        out = []
        for idx, nm in enumerate(names):
            if not nm:
                continue
            out.append({"name": nm, "value": by_num.get(str(idx))})
        return out

    def stack(self) -> list:
        frames = self._send("-stack-list-frames").get("results", {}).get("stack", [])
        out = []
        for f in frames:
            if isinstance(f, dict):
                out.append({"level": f.get("level"), "addr": f.get("addr"),
                            "func": f.get("func"), "file": f.get("file"),
                            "line": f.get("line")})
        return out

    def locals(self) -> dict:
        # 지역변수 + 인자(값 포함)
        res = self._send('-stack-list-variables --all-values')
        variables = res.get("results", {}).get("variables", [])
        out = []
        for v in variables:
            if isinstance(v, dict):
                out.append({"name": v.get("name"), "value": v.get("value"),
                            "arg": v.get("arg") == "1"})
        return {"variables": out}

    def evaluate(self, expr: str) -> dict:
        res = self._send(f'-data-evaluate-expression {mi_quote(expr)}')
        return {"expr": expr, "value": res.get("results", {}).get("value")}

    def disassemble(self, around: bool = True, count: int = 48) -> dict:
        """현재 pc 주변을 디스어셈블(런타임 주소)."""
        # mode 2: opcode 포함. $pc부터 뽑아 선두 명령 정렬이 어긋나지 않게 한다.
        span = max(32, count * 4)
        res = self._send(f'-data-disassemble -s $pc -e "$pc + {span}" -- 2')
        insns = res.get("results", {}).get("asm_insns", [])
        out = []
        for ins in insns:
            if isinstance(ins, dict):
                out.append({"addr": ins.get("address"), "func": ins.get("func-name"),
                            "offset": ins.get("offset"), "inst": ins.get("inst"),
                            "opcodes": ins.get("opcodes")})
        return {"instructions": out}

    # ---- 런타임 쓰기(변수·레지스터·메모리 조작) -----------------------
    def set_var(self, expr: str, value: str) -> dict:
        self._send(f'-gdb-set var {expr} = {value}')
        # 결과 재평가
        try:
            nv = self.evaluate(expr).get("value")
        except DebugError:
            nv = None
        return {"expr": expr, "value": nv}

    def set_register(self, name: str, value: str) -> dict:
        reg = name if name.startswith("$") else f"${name}"
        self._send(f'-gdb-set {reg} = {value}')
        nv = self.evaluate(reg).get("value")
        return {"name": name, "value": nv}

    def read_memory(self, addr: str, count: int = 128) -> dict:
        res = self._send(f'-data-read-memory-bytes {addr} {count}')
        mem = res.get("results", {}).get("memory", [])
        contents = ""
        begin = addr
        if mem and isinstance(mem[0], dict):
            contents = mem[0].get("contents", "")
            begin = mem[0].get("begin", addr)
        return {"addr": begin, "bytes": contents}

    def write_memory(self, addr: str, hexbytes: str) -> dict:
        hexbytes = hexbytes.replace(" ", "")
        self._send(f'-data-write-memory-bytes {addr} {hexbytes}')
        return {"addr": addr, "written": len(hexbytes) // 2}

    # ---- 스냅샷 --------------------------------------------------------
    def snapshot(self) -> dict:
        # last_stop은 리더 스레드가 언제든 None으로 바꿀 수 있으므로 한 번만 읽는다
        ls = self.last_stop
        frame = ls.get("frame") if ls else None
        proc = self.proc
        return {
            "status": self.status,
            "alive": self._alive and proc is not None and proc.poll() is None,
            "stop_reason": ls.get("reason") if ls else None,
            "frame": frame,
            "exit_code": self.exit_code,
            "load_bias": self.load_bias,
            "pid": proc.pid if proc else None,
        }

    def target_output(self, clear: bool = False) -> str:
        text = "".join(self.target_out)
        if clear:
            self.target_out.clear()
        return text

    def console_output(self) -> str:
        return "".join(self.console)
