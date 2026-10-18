"""디버거(backend/app/debug.py) 회귀 테스트.

실제 GDB로 samples/demo_elf 를 디버깅하며 아래를 검증한다.
- 정상 흐름과 런타임 조작(rax=1 로 'denied' → 'access granted')
- MI 명령 주입 차단(개행), 따옴표·역슬래시 인용
- 시작 실패 시 fd·스레드·프로세스 정리, 멱등/동시 stop, 대상 프로세스 종료 확인
- 리더 스레드 예외 내성, gdb 외부 종료 시 status, 종료 후 재실행, 실행 대기 반환
- MI 파서: 8진 이스케이프, 짝 안 맞는 괄호(무한 루프 방지), 퍼징

실행:  python tests/test_debugger.py [시나리오 이름...]   (GDB 필요, 없으면 건너뜀)
pytest 로도 돌릴 수 있다(test_debugger_suite).
"""
import importlib.util, os, sys, threading, time, shutil, tempfile, io, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
path = os.path.join(ROOT, "backend", "app", "debug.py")
want = set(a for a in sys.argv[1:] if not a.startswith("-"))
spec = importlib.util.spec_from_file_location("dbgmod", path)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DebugSession, DebugError = m.DebugSession, m.DebugError
DEMO = os.path.join(ROOT, "samples", "demo_elf")
RESULTS = []


def scenario(name):
    def deco(fn):
        def run():
            if want and name not in want:
                return
            try:
                out = fn()
                RESULTS.append((name, "PASS", out or ""))
            except Exception as e:
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
        run.__name__ = name
        SCEN.append(run)
        return fn
    return deco

SCEN = []
nfds = lambda: len(os.listdir("/proc/self/fd"))


@scenario("basic-flow")
def _():
    s = DebugSession(DEMO); s.start()
    s.break_insert("main"); s.break_insert("check")
    s.run("wrongpass"); s.cont(); s.finish()
    s.set_register("rax", "1")
    s.cont(); time.sleep(0.25)
    out = s.target_output(); s.stop()
    assert "access granted" in out, out
    return repr(out)


@scenario("newline-injection")
def _():
    payloads = [
        ("evaluate", lambda s: s.evaluate('1"\n-exec-continue\n"')),      # 따옴표로 닫고 다음 줄에 명령
        ("set_var", lambda s: s.set_var("argc", "1\n-exec-continue")),    # 비인용 경로
        ("console", lambda s: s.console_cmd("print 1\n-exec-continue")),
    ]
    notes = []
    for name, call in payloads:
        s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
        try:
            call(s)
        except DebugError:
            pass
        time.sleep(0.6)
        st = s.snapshot()["status"]
        s.stop()
        assert st == "stopped", f"{name}: 주입된 -exec-continue 가 실행됨 (status={st})"
        notes.append(f"{name}:차단")
    return ", ".join(notes)


@scenario("backslash-quote")
def _():
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    out = s.console_cmd('print "back\\\\slash"')["output"]
    ev = s.evaluate('sizeof("ab")')["value"]
    s.stop()
    assert "back\\\\slash" in out, out
    assert ev == "3", ev
    return f"console={out.strip()!r} sizeof={ev}"


@scenario("start-failure-no-leak")
def _():
    base = nfds(); tb = threading.active_count()
    old = os.environ["PATH"]; os.environ["PATH"] = "/nonexistent"
    try:
        for _ in range(20):
            s = DebugSession(DEMO)
            try:
                s.start()
            except Exception:
                pass
    finally:
        os.environ["PATH"] = old
    time.sleep(0.3)
    leaked = nfds() - base
    assert leaked == 0, f"fd {leaked}개 누수"
    return f"fd 누수 0 (20회 실패 시작), threads {tb}->{threading.active_count()}"


@scenario("concurrent-stop")
def _():
    base = nfds()
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    errs = []
    def go():
        try: s.stop()
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    s.stop()  # 멱등
    time.sleep(0.2)
    assert not errs, errs
    assert nfds() - base == 0, f"fd {nfds()-base}개 남음"
    return "예외 없음, fd 정리됨"


@scenario("stop-kills-inferior-no-etxtbsy")
def _():
    d = tempfile.mkdtemp(); p = os.path.join(d, "demo_copy")
    shutil.copy(DEMO, p); os.chmod(p, 0o755)
    s = DebugSession(p); s.start(); s.break_insert("main"); s.run("x")
    pid = s.inferior_pid
    s.stop()
    # 곧바로 덮어쓰기 (ETXTBSY면 OSError)
    with open(p, "wb") as fh:
        fh.write(b"overwritten")
    alive = os.path.exists(f"/proc/{pid}") and open(f"/proc/{pid}/stat").read().split(")")[-1].split()[0] != "Z"
    assert not alive, "대상 프로세스가 살아 있음"
    return f"inferior pid={pid} 종료, 즉시 덮어쓰기 성공"


@scenario("snapshot-race")
def _():
    s = DebugSession(DEMO)
    s.last_stop = {"frame": {"x": 1}, "reason": "r"}
    stop = threading.Event(); errs = []
    def flip():
        while not stop.is_set():
            s.last_stop = None
            s.last_stop = {"frame": {"x": 1}, "reason": "r"}
    t = threading.Thread(target=flip); t.start()
    try:
        for _ in range(200000):
            try: s.snapshot()
            except Exception as e:
                errs.append(repr(e)); break
    finally:
        stop.set(); t.join()
    assert not errs, errs[0]
    return "20만 회 경합 중 예외 없음"


@scenario("pending-race")
def _():
    """리더의 결과 디스패치 vs 타임아웃 경로의 pop 경합 (KeyError 재현 시도)"""
    s = DebugSession(DEMO)
    errs = []
    def timeout_path(tok):
        lock = getattr(s, "_plock", None)
        if lock:
            with lock: s._pending.pop(tok, None)
        else:
            s._pending.pop(tok, None)
    for rnd in range(30000):
        tok = str(rnd)
        slot = {"event": threading.Event(), "result": None}
        s._pending[tok] = slot
        t = threading.Thread(target=timeout_path, args=(tok,))
        t.start()
        try:
            s._handle_line(f"{tok}^done")
        except Exception as e:
            errs.append(f"round {rnd}: {type(e).__name__}"); t.join(); break
        t.join()
    assert not errs, errs[0]
    return "3만 라운드 예외 없음"


@scenario("reader-survives-exception")
def _():
    s = DebugSession(DEMO)
    class FakeOut:
        def __init__(self, lines): self.lines = list(lines)
        def readline(self): return self.lines.pop(0) if self.lines else ""
    class FakeProc:
        def __init__(self): self.stdout = FakeOut(["BOOM\n", "7^done,value=\"ok\"\n"])
        def poll(self): return None
    s.proc = FakeProc(); s._alive = True
    slot = {"event": threading.Event(), "result": None}
    s._pending["7"] = slot
    orig = s._handle_line
    calls = {"n": 0}
    def flaky(line):
        calls["n"] += 1
        if line.startswith("BOOM"):
            raise RuntimeError("파서 오류 시뮬레이션")
        return orig(line)
    s._handle_line = flaky
    t = threading.Thread(target=s._read_loop); t.start(); t.join(2)
    assert slot["event"].is_set(), "리더가 예외로 죽어 이후 결과를 못 받음"
    assert slot["result"]["class"] == "done"
    return "예외 후에도 다음 결과 레코드 처리됨"


@scenario("gdb-killed-externally")
def _():
    import signal
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    os.kill(s.proc.pid, signal.SIGKILL)
    for _ in range(50):
        if not s.snapshot()["alive"]: break
        time.sleep(0.1)
    snap = s.snapshot()
    s.stop()
    assert snap["alive"] is False, snap
    assert snap["status"] == "exited", f"status가 {snap['status']} 에 고착"
    return f"alive=False status={snap['status']}"


@scenario("rerun-after-exit")
def _():
    s = DebugSession(DEMO); s.start(); s.break_insert("main")
    st = s.run("a"); assert st["status"] == "stopped", st
    st = s.cont()
    for _ in range(30):
        if st["status"] == "exited": break
        time.sleep(0.1); st = s.snapshot()
    assert st["status"] == "exited" and st["exit_code"] == "0", st
    st = s.run("b")                       # 종료 뒤 다시 실행
    assert st["status"] == "stopped" and st["exit_code"] is None, st
    out_frame = st["frame"]["func"]
    s.stop()
    return f"재실행 후 {out_frame}에서 정지, exit_code 초기화"


@scenario("exec-timeout-returns-running")
def _():
    # 입력을 기다리는 프로그램: 중단점 없이 run → EXEC_WAIT 안에 안 멈추면 running 으로 돌려줘야 한다
    if not shutil.which("gcc"):
        return "gcc 없음: 건너뜀"
    d = tempfile.mkdtemp(); src = os.path.join(d, "w.c"); exe = os.path.join(d, "w")
    open(src, "w").write("#include <unistd.h>\nint main(){char c; read(0,&c,1); return 0;}\n")
    import subprocess
    subprocess.check_call(["gcc", "-g", "-o", exe, src])
    s = DebugSession(exe); s.start()
    s.__class__.EXEC_WAIT = 1.5
    t0 = time.time()
    st = s._exec("-exec-run", wait_stop=True, timeout=1.5)
    dt = time.time() - t0
    st2 = s.snapshot()
    s.stop()
    assert st["status"] == "running" and st2["status"] == "running", (st, st2)
    assert dt < 4, dt
    return f"{dt:.1f}s 만에 status=running 반환(요청이 붙들리지 않음)"


@scenario("start-settings-failure-cleanup")
def _():
    base_fd = nfds(); base_th = threading.active_count()
    orig = DebugSession._send
    def failing(self, cmd, timeout=10.0):
        if cmd.startswith("-gdb-set confirm"):
            raise DebugError("설정 명령 응답 시간 초과(시뮬레이션)")
        return orig(self, cmd, timeout)
    DebugSession._send = failing
    sessions = []
    try:
        for _ in range(5):
            s = DebugSession(DEMO); sessions.append(s)
            try: s.start()
            except DebugError: pass
    finally:
        DebugSession._send = orig
    time.sleep(0.5)
    alive = [x.proc.pid for x in sessions if x.proc and x.proc.poll() is None]
    leaked_fd = nfds() - base_fd; leaked_th = threading.active_count() - base_th
    assert not alive, f"gdb 프로세스 {len(alive)}개 남음"
    assert leaked_fd == 0, f"fd {leaked_fd}개 누수"
    assert leaked_th == 0, f"스레드 {leaked_th}개 누수"
    return "5회 실패 시작: gdb 0개, fd 누수 0, 스레드 누수 0"


_ECHO_BIN = []


def _echo_bin():
    """한 줄을 읽어 'got:<줄>' 을 찍는 작은 프로그램(없으면 None). EOF 면 'EOF' 출력 후 exit 1."""
    if _ECHO_BIN:
        return _ECHO_BIN[0]
    if not shutil.which("gcc"):
        _ECHO_BIN.append(None); return None
    import subprocess
    d = tempfile.mkdtemp(); src = os.path.join(d, "echo.c"); exe = os.path.join(d, "echobin")
    open(src, "w").write(
        '#include <stdio.h>\nint main(void){char b[8192];puts("name?");fflush(stdout);'
        'if(!fgets(b,sizeof b,stdin)){puts("EOF");return 1;}printf("got:%s",b);return 0;}\n')
    subprocess.check_call(["gcc", "-g", "-O0", "-o", exe, src])
    _ECHO_BIN.append(exe)
    return exe


def _wait_exit(s, secs=5.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        st = s.snapshot()
        if st["status"] == "exited":
            return st
        time.sleep(0.05)
    return s.snapshot()


def _run_waiting(exe):
    """중단점 없이 실행해 fgets 에서 입력을 기다리는 상태로 만든다."""
    s = DebugSession(exe); s.start()
    st = s._exec("-exec-run", wait_stop=True, timeout=1.0)
    assert st["status"] == "running", st
    return s


@scenario("stdin-line")
def _():
    exe = _echo_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = _run_waiting(exe)
    r = s.send_input("hello")
    st = _wait_exit(s); time.sleep(0.2)
    out = s.target_output(); s.stop()
    assert st["status"] == "exited" and st["exit_code"] == "0", st
    assert "got:hello" in out, repr(out)
    return f"{r['bytes']}바이트 전달, 출력={out.strip()!r}"


@scenario("stdin-eof")
def _():
    exe = _echo_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = _run_waiting(exe)
    s.send_input("", newline=False, eof=True)
    st = _wait_exit(s); time.sleep(0.2)
    out = s.target_output(); s.stop()
    assert st["exit_code"] == "1", st            # GDB 의 8진 "01" 이 아니라 10진 1
    assert "EOF" in out, repr(out)
    return f"EOF 전달 → 프로그램이 EOF 를 읽고 exit {st['exit_code']}"


@scenario("stdin-escapes-and-utf8")
def _():
    exe = _echo_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = _run_waiting(exe)
    s.send_input("A\\x42\\x43", escapes=True)             # \x42 → 'B', \x43 → 'C'
    _wait_exit(s); time.sleep(0.2); out1 = s.target_output(); s.stop()
    assert "got:ABC" in out1, repr(out1)
    s = _run_waiting(exe)
    s.send_input("안녕 rework")                              # UTF-8 한글 왕복
    _wait_exit(s); time.sleep(0.2); out2 = s.target_output(); s.stop()
    assert "got:안녕 rework" in out2, repr(out2)
    return "\\xHH 이스케이프와 한글(UTF-8) 입력이 그대로 전달됨"


_LEN_BIN = []


def _len_bin():
    """read() 한 번에 받은 바이트 수와 마지막 바이트를 알려 주는 프로그램."""
    if _LEN_BIN:
        return _LEN_BIN[0]
    if not shutil.which("gcc"):
        _LEN_BIN.append(None); return None
    import subprocess
    d = tempfile.mkdtemp(); src = os.path.join(d, "len.c"); exe = os.path.join(d, "lenbin")
    open(src, "w").write(
        '#include <stdio.h>\n#include <unistd.h>\nint main(void){static char b[100000];puts("ready");'
        'fflush(stdout);ssize_t n=read(0,b,sizeof b-1);printf("\\nread=%zd last=0x%02x\\n",n,n>0?(unsigned char)b[n-1]:0);'
        'return 0;}\n')
    subprocess.check_call(["gcc", "-O0", "-o", exe, src])
    _LEN_BIN.append(exe)
    return exe


def _received(s):
    import re
    _wait_exit(s); time.sleep(0.2)
    mm = re.search(r"read=(-?\d+)", s.target_output())
    return int(mm.group(1)) if mm else None


@scenario("stdin-long-line-limit")
def _():
    """쿠킹 모드 pty 는 한 줄이 4095바이트를 넘으면 조용히 버린다 → 보내기 전에 거부해야 한다."""
    exe = _len_bin()
    if not exe: return "gcc 없음: 건너뜀"
    # 한계 이내(4095 + 개행)는 한 바이트도 안 잃고 전달된다
    s = DebugSession(exe); s.start(); s._exec("-exec-run", wait_stop=True, timeout=1.0)
    s.send_input("A" * m.CANON_LINE_MAX)
    got = _received(s); s.stop()
    assert got == m.CANON_LINE_MAX + 1, f"한계 이내 입력이 잘림: read={got}"
    # 한계를 넘는 줄은 거부(그대로 보냈다면 프로그램은 4096바이트만 받는다)
    s = DebugSession(exe); s.start(); s._exec("-exec-run", wait_stop=True, timeout=1.0)
    try:
        s.send_input("A" * (m.CANON_LINE_MAX + 1)); raise AssertionError("긴 줄이 거부돼야 함")
    except DebugError as e:
        assert "4095" in str(e) and "<" in str(e), str(e)
    # 줄바꿈 없이 나눠 보낸 조각도 합쳐서 계산한다
    s.send_input("B" * 3000, newline=False)
    try:
        s.send_input("C" * 2000, newline=False); raise AssertionError("누적 초과가 거부돼야 함")
    except DebugError:
        pass
    s.send_input("D" * 1000)                                  # 3000+1000 은 한계 이내 → 줄바꿈으로 확정
    got2 = _received(s); s.stop()
    assert got2 == 4001, f"조각 결합 결과가 이상함: read={got2}"
    return f"4095바이트 줄은 손실 없이 전달(read={got}), 4096바이트·누적 초과는 거부, 조각 결합 read={got2}"


@scenario("stdin-write-deadline")
def _():
    """대상이 입력을 안 읽어 쓰기가 막혀도(EAGAIN) 대기 상한 안에 오류로 돌아와야 한다."""
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    real_os = m.os
    class _Shim:
        def __getattr__(self, name): return getattr(real_os, name)
        def write(self, fd, data):
            if fd == s._pty_master: raise BlockingIOError(11, "EAGAIN")
            return real_os.write(fd, data)
    old_wait = DebugSession.INPUT_WAIT
    m.os = _Shim(); DebugSession.INPUT_WAIT = 0.4
    t0 = time.time(); err = None
    try:
        s.send_input("hello")
    except DebugError as e:
        err = str(e)
    finally:
        m.os = real_os; DebugSession.INPUT_WAIT = old_wait
    dt = time.time() - t0
    s.stop()
    assert err and "가득" in err, f"가득 참 오류가 없음: {err}"
    assert dt < 3, f"{dt:.1f}s 걸림(대기 상한 초과)"
    return f"EAGAIN 지속 시 {dt:.2f}s 안에 '{err[:18]}…' 오류"


@scenario("output-order")
def _():
    """프로그램 출력과 gdb 메시지는 도착 순서대로 섞여야 한다(입력→결과를 한눈에 보도록)."""
    exe = _echo_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = DebugSession(exe); s.start(); s.break_insert("main"); s.run("")
    s.cont()                                     # main → fgets 대기
    time.sleep(0.4)
    s.send_input("ordered")
    _wait_exit(s); time.sleep(0.4)
    log = s.merged_output(); s.stop()
    idx = [log.find(k) for k in ("Breakpoint 1, main", "name?", "got:ordered", "exited normally")]
    assert all(i >= 0 for i in idx), (idx, log[-200:])
    assert idx == sorted(idx), f"순서가 어긋남: {idx}"
    return "Breakpoint → name? → got:ordered → exited 순으로 섞임"


@scenario("stdin-limits-and-closed")
def _():
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    for bad, kw in (("x" * (m.MAX_INPUT + 1), {"newline": False}), ("", {"newline": False})):
        try:
            s.send_input(bad, **kw); raise AssertionError("거부돼야 함")
        except DebugError:
            pass
    s.stop()
    try:
        s.send_input("late")
        raise AssertionError("종료된 세션에 전송이 거부돼야 함")
    except DebugError:
        pass
    return "너무 큰 입력·빈 입력·종료된 세션 모두 DebugError"


@scenario("stdin-args-redirect")
def _():
    exe = _echo_bin()
    if not exe: return "gcc 없음: 건너뜀"
    f = os.path.join(tempfile.mkdtemp(), "in.txt"); open(f, "w").write("from-file\n")
    s = DebugSession(exe); s.start(); s.run(f"< {f}")
    st = _wait_exit(s); time.sleep(0.2); out = s.target_output(); s.stop()
    assert "got:from-file" in out, repr(out)
    return "실행 인자 `< 파일` 로 입력 파일 연결"


@scenario("stdin-concurrent-stop")
def _():
    # 입력을 보내는 중에 세션을 종료해도 DebugError 외의 예외·행이 없어야 한다
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    errs = []
    def spam():
        for _ in range(300):
            try: s.send_input("z" * 100, newline=False)
            except DebugError: pass
            except Exception as e: errs.append(repr(e)); return
    t = threading.Thread(target=spam); t.start()
    time.sleep(0.05); s.stop(); t.join(10)
    assert not t.is_alive(), "입력 스레드가 멈추지 않음"
    assert not errs, errs
    return "전송 중 stop(): 예외·행 없음"


@scenario("input-escape-decoder")
def _():
    d = m.decode_input_escapes
    cases = {r"hello": b"hello", r"a\nb": b"a\nb", r"A\x42C": b"ABC", r"\033[0m": b"\x1b[0m",
             r"back\\slash": b"back\\slash", r"\x4": b"\\x4", r"\q": b"\\q", "한글": "한글".encode(),
             r"\0": b"\x00", r"end\\": b"end\\"}
    for src, want in cases.items():
        assert d(src) == want, (src, d(src), want)
    return f"{len(cases)}개 경계 케이스 통과"


@scenario("exit-code-decimal")
def _():
    d = m._decode_exit_code
    cases = {"0": "0", "01": "1", "07": "7", "010": "8", "012": "10", "0377": "255", None: None}
    for src, want in cases.items():
        assert d(src) == want, (src, d(src), want)
    return "GDB 8진 종료코드를 10진으로 변환(010→8, 012→10, 0377→255)"


_PROBE_SRC = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>
static void hexline(const unsigned char *b, int n) {
    printf("GOT[%d]:", n);
    for (int i = 0; i < n; i++) printf(" %02x", b[i]);
    printf("\n"); fflush(stdout);
}
int main(int argc, char **argv) {
    const char *mode = argc > 1 ? argv[1] : "hex";
    struct termios t; tcgetattr(0, &t);
    if (!strcmp(mode, "state")) {
        printf("STATE ICANON=%d ECHO=%d ISIG=%d IXON=%d ICRNL=%d\n", !!(t.c_lflag & ICANON), !!(t.c_lflag & ECHO),
               !!(t.c_lflag & ISIG), !!(t.c_iflag & IXON), !!(t.c_iflag & ICRNL));
        fflush(stdout); return 0;
    }
    if (!strcmp(mode, "rawleave")) {
        struct termios r = t; r.c_lflag &= ~(ICANON | ECHO); tcsetattr(0, TCSANOW, &r);
        puts("raw"); fflush(stdout); char c; read(0, &c, 1); _exit(0);
    }
    if (!strcmp(mode, "rawcooked")) {
        int n = atoi(argc > 2 ? argv[2] : "5000");
        struct termios r = t; cfmakeraw(&r); tcsetattr(0, TCSANOW, &r);
        puts("raw\r"); fflush(stdout);
        static unsigned char b[70000]; int got = 0;
        while (got < n) { int k = read(0, b + got, n - got); if (k <= 0) break; got += k; }
        tcsetattr(0, TCSANOW, &t);
        printf("cooked got=%d\n", got); fflush(stdout);
        char line[8192];
        if (fgets(line, sizeof line, stdin)) printf("LINE:%s", line); else puts("LINE:EOF");
        fflush(stdout); return 0;
    }
    if (!strcmp(mode, "kor")) { for (int i = 0; i < 3000; i++) printf("가"); printf("\nDONE\n"); fflush(stdout); return 0; }
    puts("ready"); fflush(stdout);
    unsigned char b[8192];
    for (;;) {
        int n = read(0, b, sizeof b);
        if (n <= 0) { puts("EOF"); fflush(stdout); return n < 0; }
        hexline(b, n);
        if (n >= 4 && !memcmp(b, "quit", 4)) return 0;
    }
}
"""
_PROBE_BIN = []


def _probe_bin():
    """모드별로 동작하는 터미널 탐침: state / rawleave / rawcooked N / kor / (기본) hex."""
    if _PROBE_BIN:
        return _PROBE_BIN[0]
    if not shutil.which("gcc"):
        _PROBE_BIN.append(None); return None
    import subprocess
    d = tempfile.mkdtemp(); src = os.path.join(d, "ttyprobe.c"); exe = os.path.join(d, "ttyprobe")
    open(src, "w", encoding="utf-8").write(_PROBE_SRC)
    subprocess.check_call(["gcc", "-g", "-O0", "-o", exe, src])
    _PROBE_BIN.append(exe)
    return exe


def _wait_text(s, pattern, secs=6.0):
    import re
    t0 = time.time()
    while time.time() - t0 < secs:
        if re.search(pattern, s.merged_output()):
            return True
        time.sleep(0.05)
    return False


def _got_bytes(s):
    import re
    out = []
    for mm in re.finditer(r"GOT\[(\d+)\]:((?: [0-9a-f]{2})+)", s.merged_output()):
        out += [int(x, 16) for x in mm.group(2).split()]
    return out


def _wait_status(s, status, secs=6.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if s.snapshot()["status"] == status:
            return True
        time.sleep(0.05)
    return False


@scenario("stdin-byte-fidelity")
def _():
    """보낸 바이트 == 프로그램이 받은 바이트. 쿠킹 모드가 해석하는 제어 바이트를 모두 섞어도 변형·삼킴·시그널·출력 정지가 없어야 한다."""
    exe = _probe_bin()
    if not exe: return "gcc 없음: 건너뜀"
    payload = r"A\x03\x04\x0d\x11\x12\x13\x15\x16\x17\x1a\x1c\x7fB"
    expected = [0x41, 3, 4, 13, 0x11, 0x12, 0x13, 0x15, 0x16, 0x17, 0x1a, 0x1c, 0x7f, 0x42, 10]
    s = DebugSession(exe); s.start(); s.run(""); _wait_text(s, "ready")
    r = s.send_input(payload, escapes=True)
    assert _wait_text(s, r"GOT\["), "프로그램이 입력을 받지 못함"
    time.sleep(0.3)
    assert s.snapshot()["status"] == "running", f"제어 바이트가 시그널로 디버기를 멈춤: {s.snapshot()['status']}"
    got = _got_bytes(s)
    assert got == expected, f"바이트가 달라짐: 받음={[hex(x) for x in got]}"
    # ^S(0x13) 가 출력을 얼리지 않았다면 EOF 후 'EOF' 가 찍히고 프로그램이 끝난다
    s.send_input("", newline=False, eof=True)
    assert _wait_text(s, "EOF") and _wait_status(s, "exited"), "^S 로 대상 출력이 멈췄거나 EOF 가 전달되지 않음"
    s.stop()
    return f"제어 바이트 13종 포함 {len(expected)}바이트가 그대로 전달({r['quoted']}개 인용), 시그널·출력 정지 없음"


@scenario("stdin-signal-chars-in-text")
def _():
    """escapes 를 끈 일반 텍스트에 ^C/^Z/^\\ 문자가 섞여도(붙여넣기) 대상에 시그널이 가지 않는다."""
    exe = _probe_bin()
    if not exe: return "gcc 없음: 건너뜀"
    for ch in ("\x03", "\x1a", "\x1c"):
        s = DebugSession(exe); s.start(); s.run(""); _wait_text(s, "ready")
        s.send_input("pay" + ch + "load")
        assert _wait_text(s, r"GOT\["), "입력이 전달되지 않음"
        time.sleep(0.25)
        st = s.snapshot()
        assert st["status"] == "running", f"0x{ord(ch):02x} 가 시그널로 디버기를 멈춤: {st['status']} {st['stop_reason']}"
        want = [ord(c) for c in "pay" + ch + "load"] + [10]
        assert _got_bytes(s) == want, [hex(x) for x in _got_bytes(s)]
        s.stop()
    return "^C ^Z ^\\ 가 데이터로 전달되고 디버기는 계속 실행"


@scenario("stdin-reset-on-run")
def _():
    """이전 실행에서 안 읽힌 입력과 바뀐 터미널 모드가 다음 실행으로 새지 않는다. 실행 중이 아닐 때의 입력은 거부."""
    exe = _probe_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = DebugSession(exe); s.start()
    try:
        s.send_input("before-run"); raise AssertionError("실행 전 전송이 거부돼야 함")
    except DebugError:
        pass
    s.run(""); _wait_text(s, "ready")
    s.send_input("quit"); s.send_input("leftover-1"); s.send_input("leftover-2")
    assert _wait_status(s, "exited"), "프로그램이 quit 으로 끝나지 않음"
    try:
        s.send_input("typed-after-exit"); raise AssertionError("종료 뒤 전송이 거부돼야 함")
    except DebugError:
        pass
    n0 = len(s.merged)
    s.run(""); time.sleep(0.8)
    fresh = "".join(s.merged[n0:])
    assert "GOT[" not in fresh, f"이전 실행의 입력이 새 실행으로 샘: {fresh[-120:]!r}"
    s.stop()
    # raw 모드로 바꾸고 복원 없이 끝난 프로그램 → 다음 실행은 기본 상태로 시작
    s = DebugSession(exe); s.start(); s.run("rawleave"); _wait_text(s, "raw")
    s.send_input("k", newline=False); assert _wait_status(s, "exited")
    s.run("state"); time.sleep(0.8)
    import re
    st = re.findall(r"STATE.*", s.merged_output())[-1]
    s.stop()
    assert "ICANON=1" in st and "ECHO=1" in st and "ISIG=0" in st and "IXON=0" in st and "ICRNL=0" in st, st
    return f"입력 큐·termios 초기화됨({st})"


@scenario("stdin-raw-mode-recovery")
def _():
    """raw 모드에서 줄바꿈 없이 4095바이트 넘게 보낸 뒤 cooked 로 돌아와도 입력이 영구히 막히지 않는다."""
    exe = _probe_bin()
    if not exe: return "gcc 없음: 건너뜀"
    s = DebugSession(exe); s.start(); s.run("rawcooked 5000"); _wait_text(s, "raw")
    s.send_input("x" * 5000, newline=False)                  # raw: 줄 길이 제한 없음
    assert s._line_carry == 0, s._line_carry
    assert _wait_text(s, "cooked got=5000"), "프로그램이 5000바이트를 다 받지 못함"
    time.sleep(0.2)
    s.send_input("hello")                                    # cooked 복귀 후에도 정상 전송
    assert _wait_text(s, "LINE:hello"), s.merged_output()[-120:]
    s.stop()
    s = DebugSession(exe); s.start(); s.run("rawcooked 10"); _wait_text(s, "raw")
    try:
        s.send_input("", newline=False, eof=True); raise AssertionError("raw 에서 EOF 는 거부돼야 함")
    except DebugError as e:
        assert "raw" in str(e)
    s.stop()
    return "raw 5000바이트 후 cooked 복귀·'hello' 전달 성공, raw 에서의 EOF 는 안내와 함께 거부"


@scenario("stdin-partial-write-carry")
def _():
    """쓰기가 중간에 막혀 일부만 전달되면, 전달된 만큼만 줄 길이에 반영한다(다음 줄이 조용히 잘리지 않게)."""
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    real_os = m.os; calls = {"n": 0}
    class _Shim:
        def __getattr__(self, name): return getattr(real_os, name)
        def write(self, fd, data):
            if fd != s._pty_master: return real_os.write(fd, data)
            calls["n"] += 1
            if calls["n"] == 1: return real_os.write(fd, data[:10])    # 처음엔 10바이트만 받는다
            raise BlockingIOError(11, "EAGAIN")
    old = DebugSession.INPUT_WAIT
    m.os = _Shim(); DebugSession.INPUT_WAIT = 0.3
    try:
        try:
            s.send_input("A" * 50, newline=False); raise AssertionError("부분 전달 오류가 나야 함")
        except DebugError as e:
            msg = str(e)
    finally:
        m.os = real_os; DebugSession.INPUT_WAIT = old
    assert "10/50" in msg, msg
    assert s._line_carry == 10, f"전달된 10바이트만 반영돼야 함: carry={s._line_carry}"
    try:
        s.send_input("B" * 4090, newline=False); raise AssertionError("남은 조각과 합쳐 한계 초과가 거부돼야 함")
    except DebugError as e:
        assert "4100" in str(e), str(e)
    s.send_input("", newline=True)                           # 개행으로 줄을 확정하면 다시 0
    assert s._line_carry == 0
    s.stop()
    return "부분 전달 10/50바이트 → carry=10, 다음 줄은 4100바이트로 정확히 거부, 개행 후 복구"


@scenario("stdin-limit-accounting")
def _():
    """줄 길이 계산은 프로그램이 실제로 받는 줄과 일치해야 한다(제어 바이트는 데이터, 줄 경계는 개행뿐)."""
    import re
    exe = _probe_bin()
    if not exe: return "gcc 없음: 건너뜀"
    def reads(s):
        return [int(x) for x in re.findall(r"GOT\[(\d+)\]", s.merged_output())]
    # (a) ^V(0x16) 를 포함한 두 줄: 예전엔 ^V 가 삼켜져 약 3900바이트가 조용히 사라졌다
    s = DebugSession(exe); s.start(); s.run(""); _wait_text(s, "ready")
    s.send_input("A" * 4000 + r"\x16\n" + "B" * 4000, escapes=True)
    t0 = time.time()
    while time.time() - t0 < 6 and len(reads(s)) < 2: time.sleep(0.05)
    a = reads(s); s.stop()
    assert a == [4002, 4001], f"두 줄이 손실 없이 전달돼야 함: {a}"
    # (b) 4095바이트 + EOF: 예전엔 EOF 문자를 길이에 넣어 거부했다. 실제로는 4095바이트가 그대로 넘어간다
    s = DebugSession(exe); s.start(); s.run(""); _wait_text(s, "ready")
    s.send_input("A" * 4095, newline=False, eof=True)
    t0 = time.time()
    while time.time() - t0 < 6 and not reads(s): time.sleep(0.05)
    b = reads(s); s.stop()
    assert b == [4095], f"4095바이트가 EOF 로 확정돼 전달돼야 함: {b}"
    # (c) CR 은 이제 줄 경계가 아니라 데이터: 3000+CR+3000 은 6001바이트 한 줄이므로 거부된다
    s = DebugSession(exe); s.start(); s.run(""); _wait_text(s, "ready")
    try:
        s.send_input("A" * 3000 + r"\r" + "B" * 3000, escapes=True); raise AssertionError("6001바이트 한 줄은 거부돼야 함")
    except DebugError as e:
        assert "6001" in str(e), str(e)
    s.stop()
    return "인용된 ^V 줄은 4002+4001 무손실, 4095+EOF 는 허용, CR 은 데이터로 계산(6001 거부)"


@scenario("utf8-chunk-boundary")
def _():
    """read() 청크 경계에서 한글이 잘려도 깨지지 않는다(청크를 직접 만들어 타이밍과 무관하게 검증)."""
    s = DebugSession(DEMO)
    s._alive = True
    r, w = os.pipe()
    th = threading.Thread(target=s._pty_loop, args=(r,), daemon=True); th.start()
    data = ("가나다" * 5).encode("utf-8")           # 한 글자 3바이트
    for cut in (1, 2, 4, 8):                          # 글자 한가운데서 끊는다
        os.write(w, data[:cut]); time.sleep(0.35)     # 별도 read() 로 읽히도록 간격을 둔다
        os.write(w, data[cut:]); time.sleep(0.35)
    s._alive = False; th.join(2); os.close(r); os.close(w)
    out = s.merged_output()
    assert out == ("가나다" * 5) * 4, repr(out[:60])
    assert "\ufffd" not in out
    # 실제 프로그램 출력(9000바이트)도 확인
    exe = _probe_bin()
    if exe:
        s2 = DebugSession(exe); s2.start(); s2.run("kor"); _wait_text(s2, "DONE"); time.sleep(0.2)
        o2 = s2.merged_output(); s2.stop()
        assert o2.count("가") == 3000 and "\ufffd" not in o2, (o2.count("가"), o2.count("\ufffd"))
    return "글자 중간에서 끊긴 4가지 경계 모두 복원, 실제 출력 3000자도 U+FFFD 0개"


@scenario("stdin-input-validation")
def _():
    """잘못된 입력은 500 이 아니라 명확한 오류가 된다(서로게이트·불리언 아닌 옵션·실행 전)."""
    s = DebugSession(DEMO); s.start(); s.break_insert("main"); s.run("x")
    try:
        s.send_input("bad\\ud800".encode().decode("unicode_escape"), newline=False)
        raise AssertionError("짝 없는 서로게이트가 거부돼야 함")
    except DebugError as e:
        assert "유니코드" in str(e), str(e)
    s.stop()
    import importlib
    os.environ.setdefault("REWORK_DATA", tempfile.mkdtemp())
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    api = importlib.import_module("app.main")
    ok = api._stdin_args({"text": "hi", "newline": False, "escapes": True, "eof": False})
    assert ok == ("hi", False, True, False), ok
    for bad in ({"text": 1}, {"text": "a", "newline": "false"}, {"text": "a", "eof": 1}, {"text": "a", "escapes": None}):
        try:
            api._stdin_args(bad); raise AssertionError(f"{bad} 가 거부돼야 함")
        except Exception as e:
            assert getattr(e, "status_code", None) == 400, (bad, e)
    return "서로게이트는 DebugError, 불리언이 아닌 옵션은 HTTP 400"


@scenario("parser-escapes")
def _():
    got = m._parse_cstring('"\\033[0mX\\000Y\\a\\x41\\"\\\\"', 0)[0]
    assert got == '\x1b[0mX\x00Y\x07A"\\', repr(got)
    nested = m.parse_mi_results(',a={b=[{c="1"},{c="2"}],d="x,y"},e=[]')
    assert nested == {"a": {"b": [{"c": "1"}, {"c": "2"}], "d": "x,y"}, "e": []}, nested
    return "8진·16진·제어 이스케이프와 중첩 구조 정상"


@scenario("parser-no-hang")
def _():
    import random
    # 짝 안 맞는 괄호가 파서를 무한 루프에 빠뜨리지 않아야 한다
    for bad in (',regs=[}]', ',frame={a="1"]', ',x=[[[}}}]', ',y={]]]'):
        t = time.time(); m.parse_mi_results(bad)
        assert time.time() - t < 0.5, f"{bad!r} 가 오래 걸림"
    random.seed(7)
    alpha = list('{}[]",=\\ abc01\n') + ['\\033', '\\"', 'x=', 'a={', 'b=[', '"q"']
    t0 = time.time()
    for _ in range(20000):
        s = "," + "".join(random.choice(alpha) for _ in range(random.randint(1, 40)))
        try:
            m.parse_mi_results(s)
        except RecursionError:
            pass
    assert time.time() - t0 < 10, "퍼징이 너무 오래 걸림(루프 의심)"
    return f"퍼징 2만 건 {time.time() - t0:.2f}s, 멈춤·예외 없음"



def main():
    if shutil.which("gdb") is None:
        print("SKIP  GDB 가 설치되어 있지 않아 디버거 테스트를 건너뜁니다.")
        return 0
    for fn in SCEN:
        fn()
    if not RESULTS:
        print("실행할 시나리오가 없습니다."); return 1
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, st, out in RESULTS:
        print(f"{st:4} {n:<{w}}  {out}")
        bad += st != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_debugger_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
