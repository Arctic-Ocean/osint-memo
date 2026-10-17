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
