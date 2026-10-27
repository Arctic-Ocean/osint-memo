"""노트북 커널(backend/app/notebook.py) 회귀 시험 + HTTP API(/api/nb/*) 시험.

- 상태 유지·마지막 식 표시·`_`·표준 출력/오류 캡처·오류는 사용자 프레임만·문법 오류
- 스레드별 출력 분리(셀 밖의 다른 스레드 출력이 섞이지 않음)·SystemExit 가 서버를 죽이지 않음
- 중단(`while True: pass`)·재시작(낡은 출력 무시)·동시 실행 거부·출력 상한·폴링 커서(이미 준 이벤트는 바뀌지 않음)
- API: 실제 서버에서 rk(열린 바이너리) 사용, 교차 출처 거부, 입력 검증

실행:  python tests/test_notebook.py [시나리오 이름...]     pytest 로도 돌릴 수 있다(test_notebook_suite)
"""
import http.client, json, os, shutil, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app import notebook as nb      # noqa: E402

want = set(a for a in sys.argv[1:] if not a.startswith("-"))
RESULTS, SCEN = [], []


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


def kernel(**ns):
    return nb.Kernel(lambda: {"__name__": "__main__", **ns})


def run_cell(k, code, timeout=10):
    k.start(code)
    t0 = time.time()
    while k.running and time.time() - t0 < timeout:
        time.sleep(0.01)
    assert not k.running, "셀이 끝나지 않음"
    return k.poll(0)


def texts(p, kind="stream", name=None):
    return "".join(e["text"] for e in p["events"] if e["output_type"] == kind and (name is None or e.get("name") == name))


@scenario("state-persists-and-last-expr")
def _():
    k = kernel()
    run_cell(k, "x = 41\ndef f(n): return n + 1")
    p = run_cell(k, "x = f(x)\nx")
    ev = [e for e in p["events"] if e["output_type"] == "execute_result"]
    assert len(ev) == 1 and ev[0]["data"]["text/plain"] == "42" and ev[0]["execution_count"] == 2, p
    p = run_cell(k, "_ + 1")                                         # IPython 의 `_`
    assert [e["data"]["text/plain"] for e in p["events"]] == ["43"], p
    p = run_cell(k, "y = 5")                                         # 대입은 출력 없음
    assert p["events"] == [] and p["status"] == "ok" and p["execution_count"] == 4, p
    p = run_cell(k, "None")
    assert p["events"] == [], "None 은 표시하지 않는다"
    return "변수·함수 유지, 마지막 식 Out, _ , 실행 번호"


@scenario("stdout-stderr-capture")
def _():
    k = kernel()
    p = run_cell(k, "import sys\nprint('hello', 1+1)\nprint('oops', file=sys.stderr)\nsys.stdout.write('no newline')")
    assert texts(p, name="stdout") == "hello 2\nno newline" and texts(p, name="stderr") == "oops\n", p
    return "stdout/stderr 구분"


@scenario("error-shows-only-user-frames")
def _():
    k = kernel()
    p = run_cell(k, "def boom():\n    return 1/0\n\nboom()")
    err = [e for e in p["events"] if e["output_type"] == "error"][0]
    tb = "".join(err["traceback"])
    assert err["ename"] == "ZeroDivisionError" and "boom" in tb and "notebook.py" not in tb and "<cell>" in tb, err
    assert p["status"] == "error"
    p = run_cell(k, "def (:")
    err = [e for e in p["events"] if e["output_type"] == "error"][0]
    assert err["ename"] == "SyntaxError" and "notebook.py" not in "".join(err["traceback"]), err
    p = run_cell(k, "1 + 1")                                         # 오류 뒤에도 커널은 산다
    assert p["status"] == "ok"
    return "트레이스백은 셀 코드만, 문법 오류 보고, 이후에도 정상"


@scenario("output-routed-per-thread")
def _():
    import io
    k = kernel()
    nb._routers()
    router = nb._ROUTERS["stdout"]
    real, router._orig = router._orig, io.StringIO()               # 서버 쪽 출력이 도착하는 곳을 잡아 둔다(시험 출력이 어지러워지지 않게)
    stop = threading.Event()

    def noisy():
        while not stop.is_set():
            print("SERVER-LOG-LINE")
            time.sleep(0.002)
    t = threading.Thread(target=noisy, daemon=True); t.start()
    try:
        p = run_cell(k, "import time\nfor i in range(30):\n    print('cell', i)\n    time.sleep(0.003)")
    finally:
        stop.set(); t.join()
        server_side, router._orig = router._orig.getvalue(), real
    out = texts(p)
    assert "SERVER-LOG-LINE" not in out and out.count("cell ") == 30, out[:200]
    assert server_side.count("SERVER-LOG-LINE") > 5 and "cell " not in server_side, "원래 스트림으로는 서버 출력만 가야 한다"
    return "다른 스레드의 print 는 셀 출력에 섞이지 않음"


@scenario("system-exit-contained")
def _():
    k = kernel()
    p = run_cell(k, "import sys\nsys.exit(3)")
    err = [e for e in p["events"] if e["output_type"] == "error"][0]
    assert err["ename"] == "SystemExit" and k.status == "error"
    p = run_cell(k, "'alive'")
    assert p["events"][0]["data"]["text/plain"] == "'alive'"
    return "sys.exit 가 서버를 끝내지 못함"


@scenario("input-does-not-block")
def _():
    import io
    import os
    import threading as _th
    # 서버의 stdin 이 영원히 막히는 파이프여도(데스크톱 앱) input() 이 멈추지 않고 바로 오류를 낸다
    r, w = os.pipe()
    orig = sys.stdin
    sys.stdin = os.fdopen(r, "r")
    try:
        k = kernel()
        t0 = time.time()
        p = run_cell(k, "input('값? ')", timeout=5)
        err = [e for e in p["events"] if e["output_type"] == "error"]
        assert err and err[0]["ename"] == "EOFError" and "input()" in err[0]["evalue"], p
        assert time.time() - t0 < 3, "input() 이 막혔다"
        p = run_cell(k, "import sys\nsys.stdin.readline()", timeout=5)
        assert [e for e in p["events"] if e["output_type"] == "error"][0]["ename"] == "EOFError", p
        assert k.status == "error"
        p = run_cell(k, "1 + 1")                                       # 오류 뒤에도 커널은 산다
        assert p["events"][0]["data"]["text/plain"] == "2"
        # 다른 스레드(서버 등)의 stdin 은 원래 스트림 그대로
        os.write(w, b"hello\n")
        got = []
        th = _th.Thread(target=lambda: got.append(sys.stdin.readline()))
        th.start(); th.join(3)
        assert got == ["hello\n"], got
    finally:
        sys.stdin = orig
        os.close(w)
    return "input()·sys.stdin.readline() 이 EOFError(막히지 않음), 다른 스레드의 stdin 은 그대로"


@scenario("interrupt")
def _():
    k = kernel()
    k.start("import time\nn = 0\nwhile True:\n    n += 1")
    time.sleep(0.4)
    assert k.running and k.interrupt() is True
    t0 = time.time()
    while k.running and time.time() - t0 < 5:
        time.sleep(0.02)
    p = k.poll(0)
    assert not k.running and p["status"] == "interrupted", p
    assert any(e["output_type"] == "error" and e["ename"] == "KeyboardInterrupt" for e in p["events"])
    assert k.interrupt() is False                                     # 실행 중이 아니면 거짓
    p = run_cell(k, "n > 0")
    assert p["events"][0]["data"]["text/plain"] == "True", "중단돼도 이미 바뀐 변수는 남는다"
    return "무한 루프 중단, 커널 재사용 가능"


@scenario("restart-clears-and-ignores-stale-output")
def _():
    k = kernel()
    run_cell(k, "secret = 1")
    k.start("import time\nfor i in range(200):\n    print('late', i)\n    time.sleep(0.01)")
    time.sleep(0.15)
    k.restart()
    time.sleep(0.8)
    p = k.poll(0)
    assert p["events"] == [] and not k.running and p["execution_count"] == 0, p      # 낡은 스레드의 출력이 새 세션에 섞이지 않음
    p = run_cell(k, "secret")
    assert p["events"][0]["output_type"] == "error" and p["events"][0]["ename"] == "NameError", p
    return "상태 초기화, 재시작 뒤 낡은 출력 무시"


@scenario("one-at-a-time-and-validation")
def _():
    k = kernel()
    k.start("import time; time.sleep(1.5)")
    for bad in ["1", 5, None]:
        try:
            k.start(bad)
        except nb.KernelError:
            continue
        raise AssertionError(f"실행 중에 받아들여짐: {bad!r}")
    k.interrupt()
    while k.running:
        time.sleep(0.02)
    try:
        k.start("x" * (nb.MAX_CODE + 1))
    except nb.KernelError:
        pass
    else:
        raise AssertionError("너무 긴 코드 허용")
    try:
        k.start(123)
    except nb.KernelError:
        return "동시 실행·잘못된 입력 거부"
    raise AssertionError("문자열이 아닌 코드 허용")


@scenario("output-cap")
def _():
    k = kernel()
    p = run_cell(k, "print('x' * 3_000_000)\nprint('after')", timeout=20)
    total = len(texts(p))
    assert total < nb.OUTPUT_LIMIT + 2000 and "너무 많아" in texts(p, name="stderr"), (total, texts(p, name='stderr'))
    return f"출력 {total >> 10}KB 에서 자르고 알림"


@scenario("poll-cursor-never-mutates-delivered-events")
def _():
    k = kernel()
    k.start("import time\nfor i in range(40):\n    print('line', i)\n    time.sleep(0.01)")
    got, since = [], 0
    while True:
        p = k.poll(since)
        for e in p["events"]:
            assert e["index"] >= since
            got.append(e)
        since = p["total"]
        if not p["running"]:
            p = k.poll(since)
            got.extend(p["events"])
            break
        time.sleep(0.05)
    text = "".join(e["text"] for e in got if e["output_type"] == "stream")
    assert text == "".join(f"line {i}\n" for i in range(40)), text[:80]
    return f"이벤트 {len(got)}개로 40줄이 빠짐·중복 없이 전달"


# ---- HTTP API --------------------------------------------------------------------
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self):
        self.port = free_port()
        self.data = tempfile.mkdtemp(prefix="rk-nb-data-")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "run.py"), "--port", str(self.port)],
                                     env={**os.environ, "REWORK_DATA": self.data}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                self.req("GET", "/api/nb/info")
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("서버가 뜨지 않음")

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw

    def run_cell(self, code, timeout=20):
        st, r = self.req("POST", "/api/nb/exec", {"code": code})
        assert st == 200, (st, r)
        since, events = 0, []
        t0 = time.time()
        while time.time() - t0 < timeout:
            st, p = self.req("GET", f"/api/nb/poll?since={since}")
            events.extend(p["events"])
            since = p["total"]
            if not p["running"]:
                st, p = self.req("GET", f"/api/nb/poll?since={since}")
                events.extend(p["events"])
                return events
            time.sleep(0.05)
        raise AssertionError("셀이 끝나지 않음")

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.data, ignore_errors=True)


@scenario("api-rk-with-binary")
def _():
    srv = Server()
    try:
        ev = srv.run_cell("rk")                                              # 바이너리가 없을 때는 안내
        assert "열린 바이너리 없음" in ev[0]["data"]["text/plain"], ev
        ev = srv.run_cell("len(rk.functions())")
        assert ev[0]["output_type"] == "error" and "열린 바이너리가 없습니다" in "".join(ev[0]["traceback"]), ev
        demo = os.path.join(ROOT, "samples", "demo_elf")
        st, r = srv.req("POST", "/api/open", {"path": demo})
        assert st == 200, r
        ev = srv.run_cell("fs = rk.functions()\nlen(fs)")
        n = int(ev[0]["data"]["text/plain"])
        assert n > 3, ev
        ev = srv.run_cell("names = [rk.name(f.vaddr) for f in fs]\n'main' in names")
        assert ev[0]["data"]["text/plain"] == "True", ev
        ev = srv.run_cell("m = [f for f in fs if rk.name(f.vaddr) == 'main'][0]\nprint(rk.decompile(m.vaddr))")
        out = "".join(e["text"] for e in ev if e["output_type"] == "stream")
        assert "main" in out and "{" in out, out[:200]
        ev = srv.run_cell("rk.table([{'name': rk.name(f.vaddr), 'size': f.size} for f in fs[:3]])")
        out = "".join(e["text"] for e in ev if e["output_type"] == "stream")
        assert out.splitlines()[0].startswith("name") and out.splitlines()[1].startswith("----"), out
        ev = srv.run_cell("n2 = len(fs)")                                       # 앞 셀의 변수가 API 호출 사이에도 유지
        ev = srv.run_cell("n2")
        assert int(ev[0]["data"]["text/plain"]) == n
        st, info = srv.req("POST", "/api/nb/restart")
        assert st == 200 and info["execution_count"] == 0, info
        ev = srv.run_cell("n2")
        assert ev[0]["ename"] == "NameError", ev
    finally:
        srv.close()
    return f"함수 {n}개, rk.decompile·table, 재시작"


@scenario("api-guards")
def _():
    srv = Server()
    try:
        bad = [("POST", "/api/nb/exec", {"code": 5}), ("POST", "/api/nb/exec", {}), ("POST", "/api/nb/exec", {"code": "x" * (nb.MAX_CODE + 1)})]
        res = [srv.req(*b)[0] for b in bad]
        assert res == [400, 400, 400], res
        st, _ = srv.req("POST", "/api/nb/exec", {"code": "1"}, headers={"Origin": "http://evil.example"})
        assert st == 403, st
        st, _ = srv.req("POST", "/api/nb/exec", {"code": "1"}, headers={"Sec-Fetch-Site": "cross-site"})
        assert st == 403, st
        srv.req("POST", "/api/nb/exec", {"code": "import time; time.sleep(3)"})
        st, r = srv.req("POST", "/api/nb/exec", {"code": "1"})
        assert st == 409, (st, r)
        st, r = srv.req("POST", "/api/nb/interrupt")
        assert st == 200 and r["interrupted"] is True, r
    finally:
        srv.close()
    return "입력 검증 400, 교차 출처 403, 실행 중 재실행 409, 중단"


def main():
    for fn in SCEN:
        fn()
    if not RESULTS:
        print("실행할 시나리오가 없습니다."); return 1
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, st, out in RESULTS:
        print(f"{st:4} {n:<{w}}  {out}")
        bad += st == "FAIL"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_notebook_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
