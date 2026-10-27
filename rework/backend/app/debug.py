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
import bisect
import codecs
import os
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time
import weakref

try:                                   # 디버거는 POSIX(pty·termios)에서만 동작한다 — Windows 에서도 서버·정적 분석은 뜨도록 임포트만 보호한다
    import fcntl
    import pty
    import termios
    HAVE_POSIX_DEBUGGER = True
except ImportError:                    # Windows
    fcntl = pty = termios = None
    HAVE_POSIX_DEBUGGER = False


class DebugError(Exception):
    pass


def mi_quote(s: str) -> str:
    """MI c-string 인용. 역슬래시·큰따옴표를 이스케이프하고, 줄바꿈 계열은 거부한다
    (개행이 섞이면 MI 명령이 둘로 갈라져 세션이 어긋난다)."""
    s = str(s)
    if any(c in s for c in "\r\n\x00"):
        raise DebugError("개행·NUL 문자는 사용할 수 없습니다.")
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _wait_writable(fd: int, timeout: float):
    """fd 가 쓰기 가능해질 때까지 최대 timeout 초 기다린다(select 의 FD_SETSIZE 한계가 없는 poll)."""
    p = select.poll()
    p.register(fd, select.POLLOUT)
    p.poll(max(1, int(timeout * 1000)))


def _sandbox_mod():
    """샌드박스 모듈. 패키지(app.debug)로 불러왔을 때와 테스트가 파일 경로로 직접 불러왔을 때 모두 동작."""
    try:
        from . import sandbox as mod
        return mod
    except ImportError:
        import importlib.util
        name = "rework_sandbox"
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod


MAX_INPUT = 64 * 1024        # 한 번에 대상 stdin 으로 보낼 수 있는 최대 바이트
# 쿠킹(ICANON) 모드 pty 는 한 줄이 4095바이트를 넘으면 나머지를 조용히 버린다(측정으로 확인).
CANON_LINE_MAX = 4095
_INPUT_ESC = {"n": 10, "r": 13, "t": 9, "a": 7, "b": 8, "f": 12, "v": 11, "e": 27,
              "\\": 92, '"': 34, "'": 39}
_HEX2 = re.compile(r"[0-9a-fA-F]{2}")
_OCT3 = re.compile(r"[0-7]{1,3}")


def _decode_exit_code(v):
    """GDB MI 의 exit-code 는 8진수 문자열이다(종료코드 10 → "012"). 10진 문자열로 바꾼다."""
    if v is None:
        return None
    try:
        return str(int(str(v), 8))
    except ValueError:
        return str(v)


def _cc(attrs, idx):
    """termios c_cc 항목을 정수 바이트로 돌려준다(비활성(0)이거나 형식이 다르면 None)."""
    try:
        v = attrs[6][idx]
    except (IndexError, TypeError):
        return None
    if isinstance(v, (bytes, bytearray)):
        v = v[0] if len(v) == 1 else None
    return v if isinstance(v, int) and v != 0 else None


def tty_special_bytes(attrs) -> set:
    """쿠킹 모드 라인 디시플린이 입력에서 '해석'해 버리는 바이트들.
    이 바이트를 프로그램에 그대로 전달하려면 ^V(VLNEXT)로 인용해야 한다. 개행(0x0a)은
    진짜 줄 끝이므로 제외한다. 현재 termios 설정에서 계산하므로 프로그램이 설정을 바꿔도 맞다."""
    iflag, lflag = attrs[0], attrs[3]
    idx = [termios.VEOF, termios.VEOL, termios.VERASE, termios.VKILL]
    for name in ("VEOL2", "VWERASE", "VREPRINT", "VLNEXT"):
        if hasattr(termios, name):
            idx.append(getattr(termios, name))
    if lflag & termios.ISIG:
        idx += [termios.VINTR, termios.VQUIT, termios.VSUSP]
    if iflag & termios.IXON:
        idx += [termios.VSTART, termios.VSTOP]
    out = {c for c in (_cc(attrs, i) for i in idx) if c is not None}
    if iflag & (termios.ICRNL | termios.IGNCR):
        out.add(13)
    out.discard(10)
    return out


def advance_line_carry(carry: int, data: bytes, eof: bool = False) -> int:
    """줄바꿈 없이 남은 '현재 줄'의 길이를 갱신한다. 개행은 줄을 끝내고, EOF 문자는 남은
    줄을 프로그램에 넘기므로 0 이 된다. (특수 바이트는 인용해 보내므로 길이에 1씩 더한다.)"""
    last = data.rfind(b"\n")
    carry = len(data) - last - 1 if last >= 0 else carry + len(data)
    return 0 if eof else carry


def decode_input_escapes(text: str) -> bytes:
    """대상 stdin 으로 보낼 문자열의 C 스타일 이스케이프를 바이트로 푼다.
    `\\n \\r \\t \\\\ \\xHH \\033(8진)` 등. 알 수 없는 이스케이프는 글자 그대로 둔다.
    이스케이프가 아닌 글자는 UTF-8 로 인코딩한다."""
    out = bytearray()
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            e = text[i + 1]
            if e == "x" and _HEX2.fullmatch(text[i + 2:i + 4] or "-"):
                out.append(int(text[i + 2:i + 4], 16))
                i += 4
                continue
            m = _OCT3.match(text, i + 1)
            if m:
                out.append(int(m.group(), 8) & 0xFF)
                i = m.end()
                continue
            if e in _INPUT_ESC:
                out.append(_INPUT_ESC[e])
                i += 2
                continue
        out += c.encode("utf-8")
        i += 1
    return bytes(out)


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
    # gdb 는 0x80 이상 바이트(한글 등 UTF-8)와 제어 문자를 8진 \NNN 으로 낸다. 연속한 바이트를 모아 UTF-8 로 해석해야
    # 글자가 깨지지 않는다(바이트마다 chr() 로 바꾸면 '한' 이 'í\x95\x9c' 처럼 된다).
    i += 1
    out = []
    raw = bytearray()
    n = len(s)

    def flush():
        if raw:
            out.append(raw.decode("utf-8", errors="replace"))
            raw.clear()

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
                v = int(s[i:j], 8) & 0xFF
                if v >= 0x80:
                    raw.append(v)
                else:
                    flush()
                    out.append(chr(v))
                i = j
                continue
            if e == "x" and i + 1 < n and s[i + 1] in _HEX:
                j = i + 1
                while j < n and j < i + 3 and s[j] in _HEX:
                    j += 1
                v = int(s[i + 1:j], 16)
                if v >= 0x80:
                    raw.append(v)
                else:
                    flush()
                    out.append(chr(v))
                i = j
                continue
            flush()
            out.append(_SIMPLE_ESC.get(e, e))
        elif c == '"':
            flush()
            i += 1
            return "".join(out), i
        else:
            flush()
            out.append(c)
        i += 1
    flush()
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


LOG_LIMIT_CHARS = 2_000_000        # 로그 버퍼(대상 출력·gdb 콘솔·섞은 로그)마다 보관하는 최대 글자 수
LOG_TAIL_CHARS = 262_144           # API 가 한 번에 돌려주는 최대 글자 수(JSON 이스케이프로 최대 6배까지 부풀 수 있다)
STACK_FRAMES_MAX = 256               # 콜 스택 패널에 보여 줄 최대 프레임 수(그 아래는 생략)
MI_LINE_MAX = 1 << 20              # gdb 가 보내는 한 줄의 최대 길이(넘으면 그 줄은 버린다)


class _Spawner:
    """subprocess.Popen 을 항상 같은 장수 스레드에서 실행한다. bwrap 의 --die-with-parent(PR_SET_PDEATHSIG)는
    '부모 스레드'가 끝날 때 발동하는데 웹 서버의 스레드풀 작업자는 10초쯤 놀면 정리된다 — 그 스레드가 Popen 을
    했다면 세션이 몇 초 뒤 아무 오류 없이 죽는다. 이 스레드는 서버 프로세스가 끝날 때까지 산다."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._t = None
        self._lock = threading.Lock()

    def _run(self):
        while True:
            fn, box, ev = self._q.get()
            try:
                box["v"] = fn()
            except BaseException as e:      # noqa: BLE001  호출자에게 그대로 전달한다
                box["e"] = e
            ev.set()

    def call(self, fn):
        with self._lock:
            if self._t is None or not self._t.is_alive():
                self._t = threading.Thread(target=self._run, daemon=True, name="rework-spawner")
                self._t.start()
        box: dict = {}
        ev = threading.Event()
        self._q.put((fn, box, ev))
        ev.wait()
        if "e" in box:
            raise box["e"]
        return box["v"]


_SPAWNER = _Spawner()


# ---- 디버그 세션 ---------------------------------------------------------
class DebugSession:
    def __init__(self, binary_path: str, program=None, sandbox=None):
        if not os.path.isfile(binary_path):
            raise DebugError(f"파일이 없습니다: {binary_path}")
        self.path = binary_path
        self.program = program          # 정적 분석 Program(이름/바이어스 해석용)
        self.sandbox_policy = sandbox   # SandboxPolicy(격리 실행) 또는 None(비격리)
        self.sandbox_info = None        # 실제로 적용된 격리 보고서(시작 후)
        self._launch = None             # sandbox.Launch(정리 대상)
        self._wall_timer = None
        self.proc = None
        self._lock = threading.Lock()   # 토큰 발급 + stdin 쓰기 직렬화
        self._plock = threading.Lock()  # _pending 접근 보호(I/O 중에는 잡지 않는다)
        self._stop_lock = threading.Lock()
        self._io_lock = threading.Lock()   # 대상 stdin 쓰기와 pty fd 닫기를 직렬화
        self._line_carry = 0               # 줄바꿈 없이 보낸 채 남아 있는 현재 줄의 바이트 수
        self._pty_base = None              # 실행 때마다 복원할 pty 터미널 설정
        self._closed = False            # stop() 완료 여부(멱등성)
        self._closing = False
        self._token = 0
        self._pending = {}              # token -> {"event": Event, "result": dict}
        self.inferior_pid = None
        self.console = []               # gdb 콘솔/로그 스트림(~, &)
        self.target_out = []            # 대상 프로그램 stdout/stderr(pty)
        self.merged = []                # 위 둘을 도착 순서대로 섞은 로그(콘솔 패널 표시용)
        self._chars = {"console": 0, "target": 0, "merged": 0}   # 각 버퍼의 글자 수(한도 적용용)
        self._console_abs = 0           # 지금까지 콘솔에 쌓인 글자 수(앞부분이 잘려도 증가분을 구하려는 용도)
        self._gdb_in_fd = None          # gdb 의 stdin 파이프(논블로킹)
        self._diag: list = []           # gdb·bwrap 이 MI 가 아닌 형식으로 낸 줄(시작 실패 원인 진단용, 최대 20줄)
        self.end_reason = None          # 세션이 '우리 의지와 무관하게' 끝난 이유(총량 한도·실행 시간·디버거 사망). 화면에 남겨 보여 준다
        self._stack_truncated = False
        self._logpoints = {}            # 중단점 번호 -> {template, args} (gdb 의 MI 출력에서는 되읽기 어려워 우리가 기억한다)
        self._silent = None             # 내부 질의 중이면 그 출력을 모으는 목록
        self._qlock = threading.Lock()
        self._maps = None               # 메모리 지도 캐시(멈출 때마다 비운다)
        self._word = None               # 포인터 폭(바이트)
        self.recording = False          # gdb 실행 기록(record full) 중인가
        self.remote = None              # 원격 gdbserver 주소(host:port). 로컬 실행이면 None
        self.sel_frame = 0              # 선택된 스택 프레임(0 = 가장 안쪽). 멈춤·재개 때마다 0 으로 돌아간다
        self._inferior_exited = False   # 대상이 끝났다는 *stopped,exited 를 gdb 로부터 받았는가
        self._logl = threading.Lock()
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
        if not HAVE_POSIX_DEBUGGER:
            raise DebugError("이 운영체제(Windows)에서는 디버거를 쓸 수 없습니다. 디버깅은 리눅스(또는 WSL2) 에서 지원합니다. "
                             "정적 분석(디스어셈블·디컴파일)은 그대로 쓸 수 있습니다.")
        master, slave = pty.openpty()
        # 대상이 입력을 읽지 않을 때 쓰기가 서버 스레드를 붙들지 않도록 논블로킹으로 둔다
        fcntl.fcntl(master, fcntl.F_SETFL, fcntl.fcntl(master, fcntl.F_GETFL) | os.O_NONBLOCK)
        self._pty_master = master
        self._pty_slave = slave   # 세션 동안 열어 둔다(조기 EOF 방지)
        # 입력이 '보낸 그대로' 프로그램에 닿게 터미널 해석을 끈다.
        #  - ISIG: ^C/^Z/^\\ 바이트가 대상에 시그널을 보내 디버기를 멈추거나 죽이지 않게(중단은 ⏸ 버튼)
        #  - IXON: ^S(0x13) 한 바이트로 대상 출력이 얼어붙지 않게
        #  - ICRNL/INLCR/IGNCR: 0x0d·0x0a 가 변환·삭제되지 않게
        # 남은 특수 문자(^D 0x7f ^U 등)는 send_input 이 ^V 로 인용한다.
        try:
            attrs = termios.tcgetattr(slave)
            attrs[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR | termios.IXON)
            attrs[3] &= ~termios.ISIG
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self._pty_base = attrs
        except (OSError, termios.error):
            self._pty_base = None
        try:
            gdb_args = ["--quiet", "--nx", "--interpreter=mi3", "-iex", "set auto-load off"]
            launch = None
            if self.sandbox_policy is not None and self.sandbox_policy.enabled:
                sbx = _sandbox_mod()
                try:
                    launch = sbx.prepare(self.sandbox_policy, self.path, os.ttyname(slave), gdb_args)
                except sbx.SandboxError as e:
                    raise DebugError(f"격리 실행을 시작할 수 없습니다: {e}")
                self._launch = launch
                self.sandbox_info = launch.info
                if launch.uid is not None:       # 낮춘 uid 가 전용 pty 를 열 수 있게
                    os.fchown(slave, launch.uid, -1)
                    os.fchmod(slave, 0o600)
                argv, extra = launch.argv, dict(pass_fds=launch.pass_fds, **launch.popen_kwargs)
            else:
                sbx = _sandbox_mod()
                self.sandbox_info = sbx.describe(sbx.SandboxPolicy(mode="off"))
                # 비격리는 원본을 그대로 실행하므로 소유자 실행 비트를 보장한다(업로드 사본은 0644 라 실행 권한이 없을 수 있음).
                # 격리 실행은 실행 가능한 복사본을 따로 만들기 때문에 원본의 권한을 건드리지 않는다.
                try:
                    st = os.stat(self.path)
                    os.chmod(self.path, st.st_mode | 0o100)
                except Exception:
                    pass
                argv, extra = ["gdb"] + gdb_args + [self.path], {}
            try:
                self.proc = _SPAWNER.call(lambda: subprocess.Popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    start_new_session=True, **extra))  # 새 프로세스 그룹(정리용)
            except BlockingIOError as e:
                raise DebugError(f"프로세스를 만들 수 없습니다(프로세스 수 한도·메모리 부족일 수 있습니다): {e}")
            except OSError as e:
                if launch is not None:             # bwrap 실행 파일을 못 쓰는 경우 등(권한·경로)
                    raise DebugError(f"격리 프로세스(bwrap)를 시작하지 못했습니다: {e}")
                raise
            try:
                self._gdb_in_fd = self.proc.stdin.fileno()
                os.set_blocking(self._gdb_in_fd, False)          # gdb 가 읽지 않아도 우리 쪽이 영원히 걸리지 않게
            except (OSError, ValueError):
                self._gdb_in_fd = None
            if launch is not None:
                # cgroup 에 넣고 샌드박스를 풀어 주고, 네임스페이스를 붙들고, 총량 감시를 시작한다
                launch.attach(self.proc, on_limit=self._limit_exceeded, on_kill=self._target_killed)
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
                tty_name = launch.tty_target if launch is not None else os.ttyname(slave)
                self._send(f"-inferior-tty-set {tty_name}", timeout=3)
            except Exception:
                if launch is not None:           # 격리 실행에서 tty 가 안 잡히면 대상 출력이 MI 채널로 샌다
                    raise
            self._send("-gdb-set mi-async on", timeout=2)
            self._send("-gdb-set pagination off", timeout=2)
            self._send("-gdb-set confirm off", timeout=2)
            if launch is not None:
                ok, why = sbx.verify_gdb_guard(launch, self.proc.pid)       # gdb 가 정말 덤프 불가인지 호스트에서 확인
                sbx.mark_guard(launch.info, ok, why)
                if not ok:
                    if self.sandbox_policy.require_gdb_guard:           # 기본: 확인하지 못하면 시작하지 않는다(fail closed)
                        raise DebugError(f"디버거 보호를 확인하지 못해 시작하지 않습니다: {why}. 격리는 유지하되 위험을 감수하려면 "
                                         "‘디버거 보호 없이 시작’을 선택하세요.")
                    self._log_merged(f"\n[샌드박스] 주의: 디버거 보호 미적용 — {why}\n")
            if self.sandbox_policy is not None and self.sandbox_policy.enabled and self.sandbox_policy.wall_seconds:
                self._wall_timer = threading.Timer(self.sandbox_policy.wall_seconds, self._wall_expired)
                self._wall_timer.daemon = True
                self._wall_timer.start()
        except BaseException:
            # gdb 미설치(FileNotFoundError)·설정 실패 등: fd·프로세스를 남기지 않는다
            self.stop()
            raise
        return self.snapshot()

    def _limit_exceeded(self, reason: str):
        """감시 스레드가 총량 한도 초과를 알렸다."""
        self.end_reason = f"{reason}을 넘어 세션이 종료되었습니다."
        self._log_merged(f"\n[샌드박스] {reason}을 넘어 세션을 종료합니다.\n")
        self.stop()

    def _target_killed(self, pid: int, reason: str):
        """감시 스레드가 한도를 넘은 대상 프로세스 하나를 죽였다(세션은 계속된다)."""
        self._log_merged(f"\n[샌드박스] 대상 프로세스가 {reason}을 넘어 종료되었습니다.\n")

    def _wall_expired(self):
        """격리 정책의 실행 시간 제한을 넘으면 세션을 끝낸다."""
        self.end_reason = f"실행 시간 제한({self.sandbox_policy.wall_seconds}초)을 넘어 세션이 종료되었습니다."
        self._log_merged(f"\n[샌드박스] 실행 시간 제한({self.sandbox_policy.wall_seconds}초)을 넘어 종료합니다.\n")
        self.stop()

    def stop(self):
        """세션 종료. 멱등이며 동시 호출에 안전하다. 반환 시점에 gdb와 대상 프로세스가
        사라져 있도록 기다린다(실행 중 바이너리를 곧바로 덮어써도 ETXTBSY가 나지 않게)."""
        with self._stop_lock:
            if self._closed:
                return
            self._closed = True
            self._closing = True
            self._alive = False
            if self._wall_timer is not None:
                self._wall_timer.cancel()
            proc = self.proc
            try:
                if proc is not None and proc.poll() is None:
                    try:
                        self._gdb_write("-gdb-exit\n", timeout=0.5)       # 응답 없는 gdb 에 막히지 않고 바로 kill 로 넘어간다
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
                with self._io_lock:   # 진행 중인 stdin 쓰기가 닫힌(재사용될 수 있는) fd 에 쓰지 않게
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
        if self._launch is not None:
            # 격리 실행: 대상은 별도 pid 네임스페이스 안에 있다(gdb 가 보는 pid 는 호스트 pid 가 아니다).
            # 네임스페이스에 남은 프로세스와 cgroup·복사본을 정리한다.
            self._launch.cleanup()
            self._launch = None
            return
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
                    line = self.proc.stdout.readline(MI_LINE_MAX)
                    if len(line) >= MI_LINE_MAX and not line.endswith("\n"):
                        while True:                  # 너무 긴 줄(오작동·조작된 gdb): 줄 끝까지 읽어 버리고 넘어간다
                            rest = self.proc.stdout.readline(MI_LINE_MAX)
                            if rest == "" or rest.endswith("\n"):
                                break
                        self._fail_oversized(line)   # 기다리던 명령이 있으면 시간 초과를 기다리지 않고 바로 실패시킨다
                        continue
                except Exception:
                    break
                if line == "":
                    break
                if line[0] not in "0123456789^*+=~@&(\n":       # MI 가 아닌 줄(bwrap·gdb 의 오류 메시지 등): 시작 실패 진단용으로 보관
                    self._diag.append(line.strip()[:300])
                    del self._diag[:-20]
                try:
                    self._handle_line(line.rstrip("\n"))
                except Exception:
                    continue   # 한 줄의 파싱·상태 예외가 리더를 죽이지 못하게 한다
        finally:
            self._alive = False
            died = (not self._inferior_exited) and (not self._closing) and self.status in ("loaded", "running", "stopped")
            if died:
                self.last_stop = {"reason": "debugger-died"}
                if self._launch is not None:
                    # 대상이 끝났다는 보고 없이 gdb 가 사라졌다 — 대상이 gdb 에 신호를 보내 죽였거나 gdb 가 비정상 종료한 것이다.
                    # 정상 종료와 구분해서 알린다(분석을 방해하려는 시도일 수 있다).
                    w = self._launch.watch
                    gdb_cpu = w.gdb_cpu_seconds if w is not None else 0.0
                    if self._launch.cpu_backstop and gdb_cpu >= self._launch.cpu_backstop * 0.95:
                        self.end_reason = (f"디버거(gdb) 자신의 CPU 시간이 받침 한도({self._launch.cpu_backstop}초)에 이르러 "
                                           "커널이 종료했습니다. 다시 시작하세요.")
                    else:
                        self.end_reason = ("디버거(gdb)가 대상 종료 보고 없이 사라졌습니다. 대상이 디버거를 죽이려 했거나(분석 방해) "
                                           "디버거가 비정상 종료한 것일 수 있습니다.")
                else:
                    self.end_reason = "디버거(gdb)가 대상 종료 보고 없이 비정상 종료했습니다."
                self._log_merged(f"\n[{'샌드박스' if self._launch is not None else '디버거'}] {self.end_reason}\n")
            # gdb가 끝났으면 디버깅도 끝난 것이다(status가 running에 고착되지 않게)
            if self.status in ("loaded", "running", "stopped"):
                self.status = "exited"
            launch = self._launch                  # gdb 가 사라졌다면 샌드박스에 남은 대상·cgroup·작업 폴더도 함께 정리한다
            if launch is not None and not self._closing:
                try:
                    launch.cleanup()
                except Exception:                  # noqa: BLE001
                    pass
            # 어떤 이유로 끝나든 대기 중인 명령과 stop 대기자를 깨운다
            with self._plock:
                pending = list(self._pending.values())
                self._pending.clear()
            why = "; ".join(l for l in self._diag[-3:] if l)          # bwrap·gdb 가 남긴 마지막 오류(시작 실패의 진짜 이유)
            for slot in pending:
                slot["result"] = {"class": "error", "results": {"msg": "gdb 종료됨" + (f" ({why})" if why else "")}}
                slot["event"].set()
            self._stopped_ev.set()

    def _fail_oversized(self, head: str):
        """한도(MI_LINE_MAX)를 넘는 응답 줄을 버렸다. 줄 머리의 토큰으로 기다리던 명령을 찾아 즉시 실패시킨다."""
        i = 0
        while i < len(head) and head[i].isdigit():
            i += 1
        if not i:
            return
        with self._plock:
            slot = self._pending.pop(head[:i], None)
        if slot is not None:
            slot["result"] = {"class": "error", "results": {"msg": "응답이 너무 커서 버렸습니다(1MiB 초과)"}}
            slot["event"].set()

    def _pty_loop(self, master):
        # read() 청크 경계에서 멀티바이트 문자(한글)가 잘려도 깨지지 않도록 증분 디코더를 쓴다
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        poller = select.poll()                      # select() 는 fd 1024 이상에서 실패한다(서버 fd 가 많을 때)
        try:
            poller.register(master, select.POLLIN)
        except (OSError, ValueError):
            return
        while self._alive and not self._closing:
            try:
                if not poller.poll(20):             # 20ms: 대상 출력과 gdb 메시지의 도착 순서 차이를 줄인다
                    continue
                data = os.read(master, 4096)
            except BlockingIOError:
                continue
            except (OSError, ValueError):
                break
            if not data:
                break
            text = decoder.decode(data)
            if not text:
                continue
            self._push("target", self.target_out, text)
            self._log_merged(text)

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
                self._push("target", self.target_out, val)
                self._log_merged(val)
            elif self._launch is not None and "opening /proc/self/mem file failed" in val:
                return        # 격리에서 gdb 를 덤프 불가로 만든 결과로 매번 나오는 무해한 경고(ptrace 로 메모리를 읽는다) — 콘솔에 싣지 않는다
            elif kind == "~" and self._silent is not None:
                self._silent.append(val)         # 내부 질의(info symbol·info proc mappings 등)의 출력: 콘솔에 싣지 않고 호출자에게만 준다
            else:
                self._push("console", self.console, val)
                self._console_abs += len(val)
                self._log_merged(val)

    def _on_exec_async(self, cls, results):
        if cls == "running":
            self._inferior_exited = False
            self.status = "running"
            self.last_stop = None
            self.sel_frame = 0
            self._maps = None
        elif cls == "stopped":
            reason = results.get("reason", "")
            self.last_stop = results
            self.sel_frame = 0
            self._maps = None
            if reason.startswith("exited"):
                self._inferior_exited = True
                self.recording = False
                self.status = "exited"
                self.exit_code = _decode_exit_code(
                    results.get("exit-code", "0" if reason == "exited-normally" else None))
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
        if len(self.events) > 500:                  # =thread-created 같은 알림이 폭주해도 메모리가 늘지 않게
            del self.events[:250]
        if cls == "thread-group-started":
            if self._launch is None:             # 격리 실행의 pid 는 네임스페이스 안의 번호라 호스트에서 쓰면 안 된다
                try:
                    self.inferior_pid = int(results.get("pid"))
                except (TypeError, ValueError):
                    pass
        elif cls == "thread-group-exited":
            self.inferior_pid = None
            self.recording = False
            self.status = "exited"
            self.exit_code = _decode_exit_code(results.get("exit-code"))
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
            # 로드 바이어스는 페이지 배수다. 멈춘 곳은 함수 시작이 아니라 그 안쪽이므로 (런타임 - 정적) 에서 함수 안 오프셋을 페이지 단위로 버린다.
            # (큰 함수라서 4KiB 를 넘으면 틀릴 수 있어, stack() 이 gdb 의 `info symbol` 로 정확히 다시 맞춘다.)
            self.load_bias = (rt - st) & ~0xFFF if (rt - st) > 0 else rt - st
            self._bias_func = (func, st)

    # ---- 명령 전송 -----------------------------------------------------
    def _not_active(self) -> "DebugError":
        rc = self.proc.poll() if self.proc else None
        why = f" (gdb 종료 코드 {rc})" if rc is not None else ""
        diag = " | ".join(self._diag[-5:])
        return DebugError("디버그 세션이 활성 상태가 아닙니다." + why + (f" 출력: {diag}" if diag else ""))

    def _gdb_write(self, text: str, timeout: float = 5.0):
        """gdb 의 stdin 에 쓴다. gdb 가 읽지 않아 파이프가 가득 차도 timeout 안에 오류로 돌려준다(요청 스레드가 영원히 걸리지 않게)."""
        fd = self._gdb_in_fd
        data = text.encode("utf-8", errors="replace")
        if fd is None:                                         # 논블로킹 설정에 실패한 환경: 예전 방식
            self.proc.stdin.write(text)
            self.proc.stdin.flush()
            return
        view, sent, deadline = memoryview(data), 0, time.monotonic() + timeout
        while sent < len(data):
            try:
                sent += os.write(fd, view[sent:])
            except BlockingIOError:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise DebugError("gdb 가 응답하지 않아 명령을 보낼 수 없습니다(입력 파이프가 가득 참).")
                _wait_writable(fd, min(left, 0.2))

    def _send(self, mi_command: str, timeout: float = 10.0) -> dict:
        if self._closed or not self._alive or not self.proc or self.proc.poll() is not None:
            raise self._not_active()
        if len(mi_command) > 1_000_000:
            raise DebugError("MI 명령이 너무 깁니다(1MB 초과).")
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
                self._gdb_write(f"{tok}{mi_command}\n")
            except DebugError:
                with self._plock:
                    self._pending.pop(tok, None)
                raise
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
        with self._qlock:                                    # 내부 질의와 출력이 뒤섞이지 않게 직렬화
            before = self._console_abs
            self._send(f'-interpreter-exec console {mi_quote(text)}', timeout=20)
            gained = self._console_abs - before
        return {"output": "".join(self.console)[-gained:] if gained > 0 else ""}

    def _query(self, text: str, timeout: float = 20.0) -> str:
        """gdb 콘솔 명령을 조용히 실행하고 출력만 돌려준다(사용자에게 보이는 콘솔·로그에는 남기지 않는다). 사용자 명령과 겹치지 않게 직렬화한다."""
        with self._qlock:
            self._silent = []
            try:
                self._send(f'-interpreter-exec console {mi_quote(text)}', timeout=timeout)
                time.sleep(0.01)                      # 마지막 스트림 줄이 리더 스레드에서 처리되도록 한 박자
                return "".join(self._silent)
            finally:
                self._silent = None

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
        if self.remote:                                   # 원격 대상은 이미 시작되어 멈춰 있다
            return self.cont()
        if args:
            self._send(f"-exec-arguments {args}")
        self.exit_code = None   # 종료 뒤 다시 실행할 때 이전 종료코드가 남지 않게
        self._reset_pty()       # 이전 실행에서 안 읽힌 입력·바뀐 터미널 모드가 새 실행으로 새지 않게
        return self._exec("-exec-run", wait_stop=True)

    def _reset_pty(self):
        with self._io_lock:
            slave = self._pty_slave
            if slave is None or self._closed:
                return
            try:
                termios.tcflush(slave, termios.TCIFLUSH)             # 안 읽힌 입력 버림
                if self._pty_base is not None:
                    termios.tcsetattr(slave, termios.TCSANOW, self._pty_base)   # ICANON/ECHO 등 복원
            except (OSError, termios.error):
                pass
            self._line_carry = 0

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
    # 종류 표기: break(일반) · log(dprintf) · watch(쓰기) · rwatch(읽기) · awatch(접근)
    _BP_KIND = {"dprintf": "log", "hw watchpoint": "watch", "watchpoint": "watch", "read watchpoint": "rwatch", "acc watchpoint": "awatch"}

    def _bp_dict(self, b: dict) -> dict:
        typ = b.get("type") or "breakpoint"
        num = b.get("number")
        out = {"number": num, "type": typ, "kind": self._BP_KIND.get(typ, "break"),
               "addr": b.get("addr"), "func": b.get("func"), "file": b.get("file"), "line": b.get("line"),
               "enabled": b.get("enabled") == "y", "what": b.get("what") or b.get("exp"), "times": b.get("times"),
               "cond": b.get("cond"), "ignore": b.get("ignore"), "temporary": b.get("disp") == "del"}
        log = self._logpoints.get(str(num))
        if log:
            out["log"] = log
        return out

    @staticmethod
    def _bp_location(location) -> str:
        loc = str(location if location is not None else "").strip()
        if not loc:
            raise DebugError("중단점 위치가 비어 있습니다.")
        return loc

    @staticmethod
    def _bp_count(v, what: str) -> int:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise DebugError(f"{what}는 정수여야 합니다.")
        if n < 0 or n > 1_000_000_000:
            raise DebugError(f"{what}가 범위를 벗어났습니다.")
        return n

    def break_insert(self, location: str, condition: str | None = None, ignore=None, temporary: bool = False,
                     hardware: bool = False, disabled: bool = False) -> dict:
        """중단점을 단다. condition: 참일 때만 멈춘다 · ignore: 처음 N 번은 건너뛴다 · temporary: 한 번 멈추면 지운다."""
        args = []
        if temporary:
            args.append("-t")
        if hardware:
            args.append("-h")
        if disabled:
            args.append("-d")
        if condition and str(condition).strip():
            args += ["-c", mi_quote(str(condition).strip())]
        if ignore not in (None, "", 0, "0"):
            args += ["-i", str(self._bp_count(ignore, "무시 횟수"))]
        res = self._send("-break-insert " + " ".join(args + ["--", self._bp_location(location)]))
        return self._bp_dict(res.get("results", {}).get("bkpt", {}))

    def logpoint_insert(self, location: str, template: str, args: list | None = None, condition: str | None = None) -> dict:
        """멈추지 않고 메시지만 찍는 로그포인트(gdb dprintf). template 은 printf 형식(`i=%d total=%d`), args 는 인자 식 목록."""
        tpl = str(template)
        if not tpl:
            raise DebugError("로그 메시지가 비어 있습니다.")
        if not tpl.endswith("\\n"):                          # 끝에 \n(역슬래시+n) 이 없으면 붙인다: 출력이 한 줄로 뭉치지 않게
            tpl += "\\n"
        extra = [mi_quote(str(a)) for a in (args or []) if str(a).strip()]
        cmd = ["-dprintf-insert"]
        if condition and str(condition).strip():
            cmd += ["-c", mi_quote(str(condition).strip())]
        cmd += [self._bp_location(location), mi_quote(tpl)] + extra
        res = self._send(" ".join(cmd))
        bp = res.get("results", {}).get("bkpt", {})
        self._logpoints[str(bp.get("number"))] = {"template": tpl, "args": [str(a) for a in (args or []) if str(a).strip()]}
        return self._bp_dict(bp)

    def watch_insert(self, expr: str, mode: str = "write", condition: str | None = None, location: bool = False,
                     software: bool = False) -> dict:
        """워치포인트: 식의 값이 바뀌거나(write) 읽히거나(read) 접근될 때(access) 멈춘다.
        location=True 면 식이 가리키는 *주소*를 감시한다(지역 변수가 사라져도 유지). software=True 면 하드웨어 레지스터 대신 한 명령씩 실행하며 비교한다(느림)."""
        e = str(expr).strip()
        if not e:
            raise DebugError("감시할 식이 비어 있습니다.")
        if e.startswith("-"):
            raise DebugError("감시식은 '-' 로 시작할 수 없습니다.")
        flag = {"write": "", "read": "-r ", "access": "-a "}.get(mode)
        if flag is None:
            raise DebugError(f"알 수 없는 감시 방식: {mode}")
        if location:
            flag = "-l " + flag
        if software:
            if mode != "write":
                raise DebugError("소프트웨어 감시는 쓰기(값 변경) 방식만 가능합니다.")
            self._send("-gdb-set can-use-hw-watchpoints 0")
        try:
            res = self._send("-break-watch " + flag + e)
        finally:
            if software:
                try:
                    self._send("-gdb-set can-use-hw-watchpoints 1")
                except DebugError:
                    pass
        r = res.get("results", {})
        wpt = r.get("wpt") or r.get("hw-wpt") or r.get("hw-rwpt") or r.get("hw-awpt") or {}
        num = wpt.get("number")
        if condition and str(condition).strip() and num is not None:
            try:
                self._send(f"-break-condition {int(num)} {str(condition).strip()}")
            except DebugError:
                self._send(f"-break-delete {int(num)}")        # 조건이 잘못됐으면 감시도 남기지 않는다
                raise
        for b in self.breakpoints():
            if str(b.get("number")) == str(num):
                return b
        return {"number": num, "what": wpt.get("exp"), "kind": {"": "watch"}.get("", "watch")}

    def break_delete(self, number) -> dict:
        n = self._bp_count(number, "중단점 번호")
        self._send(f"-break-delete {n}")
        self._logpoints.pop(str(n), None)
        return {"deleted": n}

    def break_toggle(self, number, enable: bool) -> dict:
        n = self._bp_count(number, "중단점 번호")
        self._send(f"-break-{'enable' if enable else 'disable'} {n}")
        return {"number": n, "enabled": enable}

    def break_edit(self, number, condition=None, ignore=None, has_condition: bool = False, has_ignore: bool = False) -> dict:
        """조건·무시 횟수를 바꾼다. 조건을 빈 문자열로 주면 지운다."""
        n = self._bp_count(number, "중단점 번호")
        if has_condition:
            self._send(f"-break-condition {n} {str(condition or '').strip()}".rstrip())
        if has_ignore:
            self._send(f"-break-after {n} {self._bp_count(ignore or 0, '무시 횟수')}")
        for b in self.breakpoints():
            if str(b.get("number")) == str(n):
                return b
        return {"number": n}

    def breakpoints(self) -> list:
        res = self._send("-break-list")
        table = res.get("results", {}).get("BreakpointTable", {})
        body = table.get("body", []) if isinstance(table, dict) else []
        live = set()
        out = []
        for b in body:
            if isinstance(b, dict):
                d = self._bp_dict(b)
                live.add(str(d["number"]))
                out.append(d)
        for k in list(self._logpoints):                  # 자동으로 사라진(임시) 로그포인트의 기록을 치운다
            if k not in live:
                self._logpoints.pop(k, None)
        return out

    def run_to(self, location: str) -> dict:
        """위치까지 실행(임시 중단점을 달고 계속). 그 전에 다른 중단점이 먼저 걸리면 거기서 멈춘다."""
        self._send("-break-insert -t -- " + self._bp_location(location))
        return self._exec("-exec-continue", wait_stop=True)

    # ---- 스택 프레임·메모리 --------------------------------------------
    def select_frame(self, level) -> dict:
        """스택 프레임을 고른다. 이후 레지스터·지역변수·디스어셈블이 그 프레임 기준으로 나온다."""
        n = self._bp_count(level, "프레임 번호")
        self._send(f"-stack-select-frame {n}")
        # gdb 는 없는 프레임 번호를 오류 없이 무시하기도 한다: 실제로 골라졌는지 확인한다
        now = self._send("-stack-info-frame").get("results", {}).get("frame", {}).get("level")
        self.sel_frame = int(now) if str(now).isdigit() else 0
        if self.sel_frame != n:
            raise DebugError(f"프레임 {n} 이(가) 없습니다.")
        return self.snapshot()

    def _word_size(self) -> int:
        if self._word:
            return self._word
        w = None
        if self.program is not None:
            try:
                w = int(self.program.model.bits) // 8
            except Exception:
                w = None
        if w not in (2, 4, 8):
            try:
                w = int(self.evaluate("sizeof(void*)").get("value"))
            except Exception:
                w = 8
        self._word = w
        return w

    _MAP_RE = re.compile(r"^\s*(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s*(.*)$")
    _PERM_RE = re.compile(r"^[r-][w-][x-][ps-]$")

    def memory_map(self, refresh: bool = False) -> list:
        """대상 프로세스의 메모리 영역(gdb `info proc mappings`). 얻지 못하면 빈 목록."""
        if self._maps is not None and not refresh:
            return self._maps
        regions = []
        try:
            out = self._query("info proc mappings")
        except DebugError:
            out = ""
        for line in out.splitlines():
            m = self._MAP_RE.match(line)
            if not m:
                continue
            start, end, size, off, rest = m.groups()
            rest = rest.strip()
            perms, name = "", rest
            parts = rest.split(None, 1)
            if parts and self._PERM_RE.match(parts[0]):
                perms, name = parts[0], (parts[1] if len(parts) > 1 else "")
            regions.append({"start": int(start, 16), "end": int(end, 16), "offset": int(off, 16), "perms": perms, "name": name.strip()})
        regions.sort(key=lambda r: r["start"])
        self._maps = regions
        return regions

    def memory_map_view(self) -> list:
        return [{"start": hex(r["start"]), "end": hex(r["end"]), "size": r["end"] - r["start"], "perms": r["perms"], "name": r["name"]}
                for r in self.memory_map(refresh=True)]

    @staticmethod
    def _region_of(regions: list, val: int):
        lo, hi = 0, len(regions)
        while lo < hi:
            mid = (lo + hi) // 2
            r = regions[mid]
            if val < r["start"]:
                hi = mid
            elif val >= r["end"]:
                lo = mid + 1
            else:
                return r
        return None

    _SYM_RE = re.compile(r"^(\S+?)(?: \+ (\d+))? in section (\S+)(?: of (.+))?$")

    def _symbol_at(self, val: int):
        """gdb `info symbol` — 주소가 어느 심볼의 몇 바이트째인가. 없으면 None."""
        try:
            out = self._query(f"info symbol {val:#x}").strip()
        except DebugError:
            return None
        m = self._SYM_RE.match(out.splitlines()[0]) if out else None
        if not m:
            return None
        name, off, sec, obj = m.groups()
        return f"{name}+{off}" if off else name

    def _c_string_preview(self, val: int, limit: int = 24):
        try:
            data = bytes.fromhex(self.read_memory(f"{val:#x}", limit).get("bytes") or "")
        except (DebugError, ValueError):
            return None
        run = bytearray()
        for b in data:
            if 32 <= b < 127:
                run.append(b)
            else:
                break
        return run.decode("ascii") if len(run) >= 3 else None

    def stack_dump(self, count: int = 48) -> dict:
        """선택한 프레임의 $sp 부터 count 워드. 각 워드가 코드·스택·힙·데이터 포인터인지, 반환 주소인지, 가리키는 문자열은 무엇인지 덧붙인다."""
        count = max(1, min(int(count), 512))
        word = self._word_size()
        sp_s = self.evaluate("$sp").get("value") or ""
        m = re.search(r"0x[0-9a-fA-F]+", sp_s)
        if not m:
            raise DebugError("스택 포인터를 읽을 수 없습니다.")
        sp = int(m.group(0), 16)
        fp = None
        try:
            fm = re.search(r"0x[0-9a-fA-F]+", self.evaluate("$fp").get("value") or "")
            fp = int(fm.group(0), 16) if fm else None
        except DebugError:
            pass
        mem = self.read_memory(f"{sp:#x}", count * word)
        raw = bytes.fromhex(mem.get("bytes") or "")
        big = False
        if self.program is not None:
            big = getattr(self.program.model, "endian", "little") == "big"
        frames = []
        try:
            frames = self.stack()
        except DebugError:
            pass
        rets = {}
        for f in frames:
            try:
                rets.setdefault(int(f["addr"], 16), (f.get("level"), f.get("func")))
            except (TypeError, ValueError):
                pass
        regions = self.memory_map()
        rows, lookups, previews = [], 0, 0
        for i in range(len(raw) // word):
            val = int.from_bytes(raw[i * word:(i + 1) * word], "big" if big else "little")
            ea = sp + i * word
            row = {"addr": hex(ea), "offset": i * word, "value": hex(val), "kind": "int", "marks": []}
            if ea == sp:
                row["marks"].append("sp")
            if fp is not None and ea == fp:
                row["marks"].append("fp")
            r = self._region_of(regions, val) if val > 0xFFFF else None
            if val in rets and r is not None:
                lvl, fn = rets[val]
                row["ret_to"] = {"level": lvl, "func": fn}
            if r is not None:
                nm = r["name"]
                if nm.startswith("[stack"):
                    row["kind"] = "stack"
                elif nm.startswith("[heap"):
                    row["kind"] = "heap"
                elif "x" in r["perms"]:
                    row["kind"] = "code"
                else:
                    row["kind"] = "data"
                row["region"] = nm or None
                if row["kind"] == "code" and lookups < 96:
                    lookups += 1
                    sym = self._symbol_at(val)
                    if sym:
                        row["symbol"] = sym
                elif row["kind"] != "code" and "r" in r["perms"] and previews < 64:
                    previews += 1
                    st = self._c_string_preview(val)
                    if st:
                        row["string"] = st
            rows.append(row)
        return {"sp": hex(sp), "fp": hex(fp) if fp is not None else None, "word": word, "rows": rows,
                "frame": self.sel_frame, "have_map": bool(regions)}

    # ---- 실행 기록·역방향 실행 -------------------------------------------
    REVERSE_CMDS = {"reverse-continue": "-exec-continue --reverse", "reverse-step": "-exec-step --reverse",
                    "reverse-next": "-exec-next --reverse", "reverse-stepi": "-exec-step-instruction --reverse",
                    "reverse-nexti": "-exec-next-instruction --reverse", "reverse-finish": "-exec-finish --reverse"}

    def record_start(self) -> dict:
        """이후 실행을 gdb 가 기록한다(record full). 기록을 시작한 시점부터 거꾸로 되돌아갈 수 있다. 느려지고 일부 명령(AVX 등)은 기록하지 못할 수 있다."""
        if self.status != "stopped":
            raise DebugError("프로그램이 멈춰 있을 때만 기록을 시작할 수 있습니다(중단점에서 멈춘 뒤 시작하세요).")
        if self.remote:
            raise DebugError("원격 대상에서는 실행 기록을 지원하지 않습니다.")
        self._send(f"-interpreter-exec console {mi_quote('record full')}", timeout=20)
        self.recording = True
        return self.snapshot()

    def record_stop(self) -> dict:
        try:
            self._send(f"-interpreter-exec console {mi_quote('record stop')}", timeout=20)
        finally:
            self.recording = False
        return self.snapshot()

    def reverse(self, action: str) -> dict:
        cmd = self.REVERSE_CMDS.get(action)
        if cmd is None:
            raise DebugError(f"알 수 없는 역방향 동작: {action}")
        if not self.recording:
            raise DebugError("먼저 ‘기록 시작’으로 실행 기록을 켜야 거꾸로 실행할 수 있습니다.")
        return self._exec(cmd, wait_stop=True)

    # ---- 원격 gdbserver ------------------------------------------------
    REMOTE_RE = re.compile(r"^(?:[A-Za-z0-9._-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]):([0-9]{1,5})$")

    def connect_remote(self, target: str, extended: bool = False) -> dict:
        """원격 gdbserver 에 붙는다(`target remote host:port`). 프로그램은 원격에서 실행되고, 이 컴퓨터의 바이너리는 심볼 해석에만 쓴다.
        `| 명령` 같은 파이프 형식과 직렬 장치 경로는 받지 않는다(명령 실행이 되므로)."""
        t = str(target or "").strip()
        m = self.REMOTE_RE.match(t)
        if not m or not (1 <= int(m.group(1)) <= 65535):
            raise DebugError("원격 주소는 host:port 형식이어야 합니다(예: 192.168.0.5:1234, [::1]:2345).")
        if self.status == "running":
            raise DebugError("실행 중에는 원격에 연결할 수 없습니다.")
        self._send("-gdb-set tcp connect-timeout 10", timeout=3)           # 응답 없는 호스트에서 오래 붙들리지 않게
        self._send(f"-target-select {'extended-remote' if extended else 'remote'} {t}", timeout=30)
        self.remote = t
        self.status = "stopped"
        frame = None
        try:
            frame = self._send("-stack-info-frame").get("results", {}).get("frame")
        except DebugError:
            pass
        self.last_stop = {"reason": "remote-connected", "frame": frame} if frame else {"reason": "remote-connected"}
        self._update_bias(frame)
        return self.snapshot()

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
        # 아주 깊은 스택(무한 재귀)은 한 줄이 MI_LINE_MAX 를 넘을 수 있으므로 위쪽 STACK_FRAMES_MAX 개만 요청한다
        frames = self._send(f"-stack-list-frames 0 {STACK_FRAMES_MAX}").get("results", {}).get("stack", [])
        self._stack_truncated = len(frames) > STACK_FRAMES_MAX
        frames = frames[:STACK_FRAMES_MAX]
        self._refine_bias(frames)
        out = []
        for f in frames:
            if isinstance(f, dict):
                out.append({"level": f.get("level"), "addr": f.get("addr"),
                            "func": f.get("func"), "file": f.get("file"),
                            "line": f.get("line")})
        return out

    def _refine_bias(self, frames):
        """가장 안쪽 프레임이 이 바이너리의 함수면, gdb 의 `info symbol` 로 함수 시작 주소를 구해 로드 바이어스를 정확히 맞춘다."""
        bf = getattr(self, "_bias_func", None)
        if not bf or not frames or self.load_bias is None or getattr(self, "_bias_exact", None) == self.last_stop_key():
            return
        func, st = bf
        try:
            f0 = frames[0]
            if f0.get("func") != func:
                return
            rt = int(f0["addr"], 16)
            m = re.match(r"^(.+?)(?:\+(\d+))?$", self._symbol_at(rt) or "")
            if not m or m.group(1) != func:
                return
            self.load_bias = (rt - int(m.group(2) or 0)) - st
            self._bias_exact = self.last_stop_key()
        except (DebugError, ValueError, KeyError, TypeError):
            pass

    def last_stop_key(self):
        ls = self.last_stop
        return id(ls) if ls else None

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

    # ---- 대상 프로그램 stdin ---------------------------------------------
    INPUT_WAIT = 3.0   # 대상이 입력을 안 읽어 버퍼가 가득 찼을 때 기다리는 최대 시간(초)

    def send_input(self, text: str = "", newline: bool = True, escapes: bool = False,
                   eof: bool = False) -> dict:
        """대상 프로그램의 stdin 으로 입력을 보낸다(전용 pty 마스터에 쓴다).

        계약: 보낸 바이트가 그대로 프로그램에 닿는다. 이를 위해 시작 시 신호·흐름제어·CR 변환을
        끄고, 쿠킹 모드가 해석하는 나머지 특수 바이트(^D 0x7f ^U 등)는 ^V 로 인용해 보낸다.
        한 줄은 줄바꿈이 있어야 프로그램에 전달되며(쿠킹 모드), `eof` 는 EOF 문자(^D)를 보낸다.
        `escapes` 가 켜져 있으면 \\xHH·\\033·\\n 같은 이스케이프를 바이트로 풀어 보낸다.
        한 줄이 4095바이트를 넘거나(터미널이 조용히 버림), 프로그램이 입력을 읽지 않아 버퍼가
        가득 차면 기다리지 않고 DebugError 를 낸다."""
        if not isinstance(text, str):
            raise DebugError("입력은 문자열이어야 합니다.")
        if len(text) > MAX_INPUT:
            raise DebugError(f"입력이 너무 큽니다({len(text)}자, 최대 {MAX_INPUT}). "
                             "큰 입력은 실행 인자에 `< 파일` 로 연결하세요.")
        try:
            raw = decode_input_escapes(text) if escapes else text.encode("utf-8")
        except UnicodeEncodeError:
            raise DebugError("유효하지 않은 유니코드 문자(짝 없는 서로게이트)가 있어 보낼 수 없습니다.")
        body = raw + (b"\n" if newline else b"")
        if not body and not eof:
            raise DebugError("보낼 입력이 없습니다.")
        if len(body) > MAX_INPUT:
            raise DebugError(f"입력이 너무 큽니다({len(body)}바이트, 최대 {MAX_INPUT}). "
                             "큰 입력은 실행 인자에 `< 파일` 로 연결하세요.")
        with self._io_lock:
            fd = self._pty_master
            if self._closed or not self._alive or fd is None:
                raise DebugError("디버그 세션이 활성 상태가 아닙니다.")
            if self.status not in ("running", "stopped"):
                raise DebugError("실행 중인 프로그램이 없습니다. ▶/↻ 로 먼저 실행하세요 "
                                 "(종료된 프로그램에 보낸 입력은 다음 실행으로 새므로 받지 않습니다).")
            try:
                attrs = termios.tcgetattr(self._pty_slave)
            except (OSError, termios.error):
                attrs = None
            canonical = attrs is None or bool(attrs[3] & termios.ICANON)

            wire = bytearray()
            ends = []                    # 원본 바이트 i 까지 보냈을 때의 와이어 길이(부분 쓰기 계산용)
            if canonical:
                # (1) 쿠킹 모드에서는 한 줄이 CANON_LINE_MAX 를 넘으면 커널이 나머지를 조용히 버린다.
                #     잘린 입력으로 분석하는 사고를 막기 위해 보내기 전에 거부한다.
                for k, seg in enumerate(body.split(b"\n")):
                    length = len(seg) + (self._line_carry if k == 0 else 0)
                    if length > CANON_LINE_MAX:
                        raise DebugError(
                            f"한 줄이 {CANON_LINE_MAX}바이트를 넘습니다({length}바이트). 터미널(pty)은 그 이상을 "
                            "조용히 버립니다. 긴 입력은 파일로 만들어 실행 인자에 `< 파일` 로 연결하세요.")
                # (2) 라인 디시플린이 해석하는 바이트는 ^V 로 인용해 그대로 닿게 한다
                quote = set()
                vlnext = None
                if attrs is not None:
                    specials = tty_special_bytes(attrs) & set(body)
                    vlnext = _cc(attrs, termios.VLNEXT) if hasattr(termios, "VLNEXT") else None
                    if specials:
                        if vlnext is None or not (attrs[3] & termios.IEXTEN):
                            raise DebugError(
                                "이 터미널 모드에서는 제어 바이트 "
                                + ", ".join(f"0x{b:02x}" for b in sorted(specials))
                                + " 를 그대로 보낼 수 없습니다(프로그램이 리터럴 인용을 껐습니다).")
                        quote = specials
                for b in body:
                    if b in quote:
                        wire.append(vlnext)
                    wire.append(b)
                    ends.append(len(wire))
                if eof:
                    wire.append(_cc(attrs, termios.VEOF) or 4 if attrs is not None else 4)
            else:
                # 프로그램이 raw 모드: 줄 단위 해석이 없으므로 그대로 쓴다(EOF 문자는 의미가 없다)
                if eof:
                    raise DebugError("프로그램이 raw 모드라 EOF 문자(^D)는 의미가 없습니다"
                                     "(프로그램은 0x04 를 데이터로 받습니다).")
                wire += body
                ends = list(range(1, len(body) + 1))

            sent = 0
            deadline = time.monotonic() + self.INPUT_WAIT
            fail = None
            while sent < len(wire):
                try:
                    sent += os.write(fd, bytes(wire[sent:]))
                except BlockingIOError:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        fail = "full"
                        break
                    _wait_writable(fd, min(left, 0.2))
                except OSError as e:
                    fail = f"전송 실패: {e}"
                    break

            # 줄 길이 상태 갱신: 실제로 전달된 원본 바이트만큼만 반영한다
            delivered = len(body) if sent >= len(wire) else bisect.bisect_right(ends, sent)
            if canonical:
                self._line_carry = advance_line_carry(
                    self._line_carry, body[:delivered], eof=eof and sent >= len(wire))
            else:
                self._line_carry = 0     # raw 로 보낸 바이트는 cooked 로 돌아오면 확정된 입력이다
            if fail == "full":
                raise DebugError(
                    f"대상 입력 버퍼가 가득 찼습니다({delivered}/{len(body)}바이트만 전달됨). "
                    "프로그램이 입력을 읽고 있는지 확인하세요. 전달된 앞부분은 이미 입력 큐에 있습니다.")
            if fail:
                raise DebugError(f"대상 입력 {fail}")
        return {"sent": len(body), "bytes": len(wire), "quoted": len(wire) - len(body) - (1 if eof else 0)}

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
            "sandbox": self.sandbox_info,
            "sandbox_live": self._sandbox_live(),
            "sandbox_leftover": self._sandbox_leftover(ls),
            "end_reason": self.end_reason,
            "stack_truncated": self._stack_truncated,
            "selected_frame": self.sel_frame,
            "stop": self._stop_detail(ls),
            "recording": self.recording and self.status in ("stopped", "running"),
            "remote": self.remote,
        }

    @staticmethod
    def _stop_detail(ls):
        """멈춘 이유의 구조화된 정보: 걸린 중단점 번호, 워치포인트의 옛 값·새 값 등."""
        if not ls:
            return None
        d = {"reason": ls.get("reason")}
        if ls.get("bkptno") is not None:
            d["bkptno"] = ls.get("bkptno")
        for key in ("wpt", "hw-rwpt", "hw-awpt"):
            w = ls.get(key)
            if isinstance(w, dict):
                d["watch"] = {"number": w.get("number"), "exp": w.get("exp"), "kind": {"wpt": "watch", "hw-rwpt": "rwatch", "hw-awpt": "awatch"}[key]}
        v = ls.get("value")
        if isinstance(v, dict):
            d["value"] = {k: v.get(k) for k in ("old", "new", "value") if k in v}
        if ls.get("signal-name"):
            d["signal"] = ls.get("signal-name")
        return d

    def _sandbox_live(self):
        """샌드박스 안에 살아 있는 프로세스 수(감시 스레드가 센 값). 대상이 끝났는데 남은 프로세스가 있는지 알리는 데 쓴다."""
        launch = self._launch
        w = launch.watch if launch is not None else None
        return w.live_count if w is not None else None

    def _sandbox_leftover(self, ls):
        """대상이 끝난 뒤에도 샌드박스 안에 남은(데몬·자식) 프로세스 수 — 끝난 시점에 새로 센다. 해당 없으면 None.
        감시 스레드의 마지막 표본은 대상이 막 끝나기 직전의 값일 수 있어 쓰지 않는다."""
        launch = self._launch
        if launch is None or self.status != "exited" or not self._alive:
            return None
        try:
            return launch.leftover()
        except Exception:                                  # noqa: BLE001
            return None

    def _push(self, name: str, buf: list, text: str):
        """로그 버퍼에 덧붙이되 글자 수를 한도 안으로 유지한다(출력 폭주가 서버 메모리를 먹지 못하게)."""
        if len(text) > LOG_LIMIT_CHARS:
            text = text[-LOG_LIMIT_CHARS:]
        with self._logl:
            buf.append(text)
            n = self._chars[name] + len(text)
            if n > LOG_LIMIT_CHARS * 5 // 4:
                drop = 0
                while n > LOG_LIMIT_CHARS and drop < len(buf) - 1:
                    n -= len(buf[drop])
                    drop += 1
                del buf[:drop]
            if len(buf) > 20000:                  # 아주 잘게 쪼개진 출력은 앞쪽 절반을 하나로 합친다(데이터는 그대로)
                half = len(buf) // 2
                buf[:half] = ["".join(buf[:half])]
            self._chars[name] = n

    def _log_merged(self, text: str):
        self._push("merged", self.merged, text)

    @staticmethod
    def _tail(buf: list, tail) -> str:
        text = "".join(buf)
        return text if tail is None or len(text) <= tail else text[-tail:]

    def merged_output(self, tail: int | None = None) -> str:
        """프로그램 출력과 gdb 메시지를 도착 순서대로 이은 텍스트(tail 이 있으면 뒤쪽 그만큼만)."""
        return self._tail(self.merged, tail)

    def target_output(self, clear: bool = False, tail: int | None = None) -> str:
        text = self._tail(self.target_out, tail)
        if clear:
            with self._logl:
                self.target_out.clear()
                self._chars["target"] = 0
        return text

    def console_output(self, tail: int | None = None) -> str:
        return self._tail(self.console, tail)
