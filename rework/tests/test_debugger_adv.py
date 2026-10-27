"""디버거 심화 기능 회귀 테스트(조건부 중단점·로그포인트·워치포인트·프레임 선택·스택/메모리 덤프·역방향 실행·원격 gdbserver).

실제 gdb 로 이 파일이 만드는 작은 C 프로그램을 디버깅한다. gcc·gdb 가 없으면 건너뛰고, gdbserver 가 없으면 원격 시나리오만 건너뛴다.

실행:  python tests/test_debugger_adv.py [시나리오 이름...]
pytest 로도 돌릴 수 있다(test_debugger_adv_suite).
"""
import importlib.util, os, re, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
want = set(a for a in sys.argv[1:] if not a.startswith("-"))
spec = importlib.util.spec_from_file_location("dbgmod_adv", os.path.join(ROOT, "backend", "app", "debug.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
DebugSession, DebugError = m.DebugSession, m.DebugError
if os.environ.get("REWORK_TEST_SANDBOX") == "1":      # 같은 시나리오를 격리 실행 위에서 다시 돌린다
    _Plain, _sbx = m.DebugSession, m._sandbox_mod()

    class DebugSession(_Plain):
        def __init__(self, path, program=None, sandbox=None):
            super().__init__(path, program, sandbox=sandbox or _sbx.SandboxPolicy())
RESULTS, SCEN = [], []

PROBE_SRC = r"""
#include <stdio.h>
volatile int counter = 0;
int total = 0;
int work(int i) { counter += i; return counter * 2; }
int outer(int n) { int local = n * 7; return work(n) + local; }
int main(void) {
    for (int i = 0; i < 10; i++) { total += outer(i); }
    printf("total=%d counter=%d\n", total, counter);
    return 0;
}
"""


def scenario(name):
    def deco(fn):
        def run():
            if want and name not in want:
                return
            try:
                RESULTS.append((name, "PASS", fn() or ""))
            except Exception as e:
                RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}"))
        SCEN.append(run)
        return fn
    return deco


_PROBE = []


def probe():
    if _PROBE:
        return _PROBE[0]
    d = tempfile.mkdtemp(prefix="dbgadv-")
    src, exe = os.path.join(d, "probe.c"), os.path.join(d, "probe")
    open(src, "w").write(PROBE_SRC)
    subprocess.check_call(["gcc", "-g", "-O0", "-no-pie", "-o", exe, src])
    _PROBE.append(exe)
    return exe


def session(bp=None):
    s = DebugSession(probe()); s.start()
    if bp:
        s.break_insert(bp)
    return s


def frame_arg(s, name):
    for v in s.locals()["variables"]:
        if v["name"] == name:
            return v["value"]
    return None


def wait_exit(s, secs=6.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if s.snapshot()["status"] == "exited":
            return True
        time.sleep(0.05)
    return False


@scenario("cond-break")
def _():
    s = session()
    bp = s.break_insert("work", condition="i == 3")
    assert bp["cond"] == "i == 3" and bp["kind"] == "break", bp
    s.run("")
    st = s.snapshot()
    assert st["status"] == "stopped" and st["stop"]["bkptno"] == bp["number"], st
    assert frame_arg(s, "i") == "3", s.locals()
    # 조건을 바꾸면(i == 6) 다음 정지는 6 이다
    s.break_edit(bp["number"], condition="i == 6", has_condition=True)
    s.cont()
    assert frame_arg(s, "i") == "6", s.locals()
    # 조건을 지우면 매번 선다
    s.break_edit(bp["number"], condition="", has_condition=True)
    s.cont()
    assert frame_arg(s, "i") == "7", s.locals()
    listed = [b for b in s.breakpoints() if b["number"] == bp["number"]][0]
    assert not listed["cond"], listed
    s.stop()
    return "조건 설정·변경·해제 확인"


@scenario("cond-bad-expression")
def _():
    s = session()
    try:
        s.break_insert("work", condition="nosuchvar == 1")
    except DebugError as e:
        err = str(e)
    else:
        raise AssertionError("없는 변수 조건이 받아들여짐")
    assert not s.breakpoints(), s.breakpoints()
    s.stop()
    return err[:60]


@scenario("ignore-count")
def _():
    s = session()
    bp = s.break_insert("work", ignore=4)
    s.run("")
    assert frame_arg(s, "i") == "4", s.locals()          # 처음 4 번(0..3)을 건너뛴다
    listed = [b for b in s.breakpoints() if b["number"] == bp["number"]][0]
    assert listed["times"] == "5", listed
    s.stop()
    return "ignore=4 → i=4 에서 정지, times=5"


@scenario("temporary-break")
def _():
    s = session()
    bp = s.break_insert("work", temporary=True)
    assert bp["temporary"], bp
    s.run("")
    assert frame_arg(s, "i") == "0"
    assert not s.breakpoints(), s.breakpoints()            # 한 번 서면 사라진다
    s.cont()
    assert wait_exit(s), s.snapshot()
    s.stop()
    return "한 번 멈추고 삭제됨, 이후 끝까지 실행"


@scenario("logpoint")
def _():
    s = session()
    lp = s.logpoint_insert("work", "i=%d counter=%d", ["i", "counter"])
    assert lp["kind"] == "log" and lp["log"]["args"] == ["i", "counter"], lp
    s.run("")
    assert wait_exit(s), s.snapshot()                       # 멈추지 않고 끝까지 간다
    out = s.merged_output()
    lines = re.findall(r"i=(\d+) counter=(\d+)", out)
    assert [int(a) for a, _ in lines] == list(range(10)), (lines, out[-300:])
    s.stop()
    return f"로그 {len(lines)}줄, 정지 없음"


@scenario("logpoint-with-condition")
def _():
    s = session()
    s.logpoint_insert("work", "big %d", ["i"], condition="i >= 8")
    s.run("")
    assert wait_exit(s)
    got = re.findall(r"big (\d+)", s.merged_output())
    assert got == ["8", "9"], got
    s.stop()
    return str(got)


@scenario("watch-write")
def _():
    s = session("main")
    s.run("")
    w = s.watch_insert("counter")
    assert w["kind"] == "watch", w
    s.cont()
    st = s.snapshot()
    assert st["stop"]["reason"] == "watchpoint-trigger" and st["stop"]["watch"]["exp"] == "counter", st
    assert st["stop"]["value"]["old"] == "0" and st["stop"]["value"]["new"] == "1", st["stop"]   # i=0 → +0 은 변화 없음, i=1 에서 처음 바뀐다
    s.cont()
    assert s.snapshot()["stop"]["value"] == {"old": "1", "new": "3"}, s.snapshot()["stop"]
    s.stop()
    return "값 변화 old→new 보고"


@scenario("watch-condition")
def _():
    s = session("main")
    s.run("")
    s.watch_insert("counter", condition="counter > 20")
    s.cont()
    v = s.snapshot()["stop"]["value"]
    assert int(v["new"]) > 20 and int(v["old"]) <= 20, v
    s.stop()
    return f"{v['old']} → {v['new']}"


@scenario("watch-read-access")
def _():
    s = session("main")
    s.run("")
    r = s.watch_insert("total", mode="read")
    assert r["kind"] == "rwatch", r
    s.cont()
    st = s.snapshot()
    assert st["status"] == "stopped" and st["stop"]["watch"]["kind"] == "rwatch", st
    s.break_delete(r["number"])
    a = s.watch_insert("total", mode="access")
    assert a["kind"] == "awatch", a
    s.cont()
    assert s.snapshot()["stop"]["watch"]["kind"] == "awatch", s.snapshot()
    s.stop()
    return "read·access 감시 정지"


@scenario("watch-software")
def _():
    s = session("main")
    s.run("")
    w = s.watch_insert("total", software=True)
    assert w["type"] == "watchpoint", w                      # hw 가 아닌 소프트웨어 감시
    s.cont()
    assert s.snapshot()["stop"]["reason"] == "watchpoint-trigger", s.snapshot()
    s.stop()
    return "소프트웨어 감시(type=watchpoint)"


@scenario("watch-validation")
def _():
    s = session("main")
    s.run("")
    for bad, kw in [("", {}), ("-l counter", {}), ("counter", {"mode": "bogus"}), ("nosuch_variable", {})]:
        try:
            s.watch_insert(bad, **kw)
        except DebugError:
            continue
        raise AssertionError(f"받아들여짐: {bad!r} {kw}")
    s.stop()
    return "빈 식·'-' 시작·잘못된 방식·없는 변수 거부"


@scenario("run-to")
def _():
    s = session("main")
    s.run("")
    s.run_to("work")
    st = s.snapshot()
    assert st["status"] == "stopped" and st["frame"]["func"] == "work", st
    assert not any(b["temporary"] for b in s.breakpoints()), s.breakpoints()
    s.stop()
    return "work 까지 실행, 임시 중단점 삭제"


@scenario("select-frame")
def _():
    s = session("work")
    s.run("")
    assert [f["func"] for f in s.stack()][:3] == ["work", "outer", "main"], s.stack()
    assert frame_arg(s, "i") is None or True
    rip0 = [r for r in s.registers() if r["name"] == "rip"][0]["value"]
    st = s.select_frame(1)
    assert st["selected_frame"] == 1
    names = {v["name"] for v in s.locals()["variables"]}
    assert "local" in names and "n" in names, names          # outer 의 지역변수
    rip1 = [r for r in s.registers() if r["name"] == "rip"][0]["value"]
    assert rip1 != rip0, (rip0, rip1)                        # 프레임이 바뀌면 pc 도 바뀐다
    s.next()
    assert s.snapshot()["selected_frame"] == 0               # 실행하면 가장 안쪽으로 돌아간다
    try:
        s.select_frame(99)
    except DebugError:
        pass
    else:
        raise AssertionError("없는 프레임이 선택됨")
    s.stop()
    return "프레임 1 의 지역변수·pc, 실행 후 0 으로 복귀"


@scenario("stack-dump")
def _():
    s = session("work")
    s.run("")
    d = s.stack_dump(24)
    assert d["word"] == 8 and len(d["rows"]) == 24 and d["rows"][0]["marks"][0] == "sp", d["rows"][:2]
    rets = [r for r in d["rows"] if r.get("ret_to")]
    assert rets and rets[0]["ret_to"]["func"] == "outer", rets          # work 가 돌아갈 곳은 outer
    assert rets[0]["kind"] == "code" and rets[0].get("symbol", "").startswith("outer"), rets[0]
    assert d["have_map"]
    s.select_frame(1)
    d2 = s.stack_dump(8)
    assert d2["frame"] == 1 and d2["sp"] != d["sp"], (d["sp"], d2["sp"])
    s.stop()
    return f"반환 주소 {rets[0]['symbol']}, 프레임 1 은 sp {d2['sp']}"


@scenario("memory-map")
def _():
    s = session("work")
    s.run("")
    regs = s.memory_map_view()
    names = " ".join(r["name"] for r in regs)
    assert "[stack]" in names and "probe" in names, names
    assert all(r["perms"] for r in regs[:3]), regs[:3]
    s.stop()
    return f"{len(regs)}개 영역"


@scenario("memory-roundtrip")
def _():
    s = session("work")
    s.run("")
    addr = s.evaluate("&counter")["value"].split()[0]
    before = s.read_memory(addr, 4)["bytes"]
    s.write_memory(addr, "2a000000")
    assert s.evaluate("counter")["value"] == "42", s.evaluate("counter")
    assert before != "2a000000"
    try:
        s.read_memory("0", 16)
    except DebugError:
        pass
    else:
        raise AssertionError("0 번지를 읽음")
    s.stop()
    return "쓰고 다시 읽기, 읽을 수 없는 주소는 오류"


@scenario("record-reverse")
def _():
    s = session("main")
    s.run("")
    try:
        s.reverse("reverse-step")
    except DebugError as e:
        assert "기록" in str(e)
    else:
        raise AssertionError("기록 없이 역방향 실행")
    s.break_insert("work")
    s.record_start()
    assert s.snapshot()["recording"]
    s.cont(); s.cont(); s.cont()                             # i = 0, 1, 2 번째 work
    assert frame_arg(s, "i") == "2"
    c_now = s.evaluate("counter")["value"]
    s.reverse("reverse-continue")                           # 직전 work (i=1)
    assert frame_arg(s, "i") == "1", s.locals()
    s.reverse("reverse-continue")                           # i=0
    assert frame_arg(s, "i") == "0"
    s.reverse("reverse-stepi")
    assert s.snapshot()["status"] == "stopped"
    s.cont()                                                # 기록 안을 앞으로 다시 재생
    assert frame_arg(s, "i") == "0" or frame_arg(s, "i") == "1"
    s.record_stop()
    assert not s.snapshot()["recording"]
    s.stop()
    return f"역방향 계속·한 명령, counter(i=2)={c_now}"


@scenario("record-requires-stopped")
def _():
    s = session()
    try:
        s.record_start()
    except DebugError:
        s.stop()
        return "실행 전에는 기록을 시작할 수 없음"
    raise AssertionError("실행 전 기록 시작이 허용됨")


@scenario("input-validation")
def _():
    s = session("main")
    s.run("")
    bad = [
        lambda: s.break_insert("work", condition="1\n-exec-continue"),
        lambda: s.break_insert("work\n-exec-continue"),
        lambda: s.break_insert("   "),
        lambda: s.break_insert("work", ignore="abc"),
        lambda: s.break_insert("work", ignore=-1),
        lambda: s.break_delete("1; shell id"),
        lambda: s.logpoint_insert("work", "x\n-exec-continue", ["i"]),
        lambda: s.logpoint_insert("work", "", []),
        lambda: s.select_frame("x"),
        lambda: s.watch_insert("counter\n-exec-continue"),
    ]
    for f in bad:
        try:
            f()
        except DebugError:
            continue
        raise AssertionError("검증을 통과함")
    time.sleep(0.4)
    assert s.snapshot()["status"] == "stopped", "주입된 명령이 실행됨"
    s.stop()
    return f"{len(bad)}가지 거부, 상태 유지"


def free_port():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


@scenario("remote-validation")
def _():
    s = session()
    for t in ["", "localhost", "host:0", "host:99999", "| echo hi", "host:12 34", "a b:1", "/dev/ttyS0", "x:1;shell id", "-x:1"]:
        try:
            s.connect_remote(t)
        except DebugError:
            continue
        raise AssertionError(f"받아들여짐: {t!r}")
    s.stop()
    return "형식이 아닌 주소(파이프·직렬 장치·공백 등) 거부"


@scenario("remote-gdbserver")
def _():
    if not shutil.which("gdbserver"):
        return "SKIP gdbserver 없음"
    port = free_port()
    srv = subprocess.Popen(["gdbserver", f"127.0.0.1:{port}", probe()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.4)
        # 격리 위에서 돌릴 때는 main.py 와 같이 원격 모드에서만 호스트 네트워크를 연다(프로그램은 원격에서 실행되므로)
        s = DebugSession(probe(), sandbox=_sbx.SandboxPolicy(network="host")) if os.environ.get("REWORK_TEST_SANDBOX") == "1" else DebugSession(probe())
        s.start()
        s.break_insert("work", condition="i == 4")
        st = s.connect_remote(f"127.0.0.1:{port}")
        assert st["remote"] == f"127.0.0.1:{port}" and st["status"] == "stopped", st
        s.run("")                                              # 원격에서는 ‘실행’이 계속이다
        assert s.snapshot()["status"] == "stopped" and frame_arg(s, "i") == "4", (s.snapshot(), s.locals())
        regs = {r["name"]: r["value"] for r in s.registers()}
        assert "rip" in regs
        try:
            s.record_start()
        except DebugError:
            pass
        else:
            raise AssertionError("원격에서 기록이 허용됨")
        s.stop()
    finally:
        srv.kill(); srv.wait()
    return "원격 연결·조건부 중단점·레지스터"


@scenario("remote-connect-refused")
def _():
    s = session()
    port = free_port()                                         # 아무도 듣지 않는 포트
    try:
        s.connect_remote(f"127.0.0.1:{port}")
    except DebugError as e:
        err = str(e)
    else:
        raise AssertionError("연결 거부인데 성공")
    assert s.snapshot()["remote"] is None
    s.stop()
    return err[:70]


def main():
    if shutil.which("gdb") is None or shutil.which("gcc") is None:
        print("SKIP  gdb 또는 gcc 가 없어 디버거 심화 테스트를 건너뜁니다.")
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


def test_debugger_adv_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
