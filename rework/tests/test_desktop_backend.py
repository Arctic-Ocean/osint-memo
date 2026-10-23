"""데스크톱 앱이 쓰는 백엔드 모드의 회귀 테스트.

- `run.py --desktop --port 0`: 운영체제가 고른 포트를 `REWORK_PORT=` 로 알리고, 토큰 없이는 API 가 401, 표준 입력이 닫히면 스스로 종료
- `--desktop` 은 REWORK_TOKEN 이 없으면 시작하지 않는다(토큰 없는 데스크톱 서버가 뜨지 않게)
- 데이터 폴더는 본인만(0700), API 키가 든 ai.json 은 0600
- 응답마다 엄격한 CSP(같은 출처 스크립트만, 인라인·eval 없음)
- Windows 처럼 pty·termios·fcntl·resource·pwd 가 없어도 서버가 임포트되고 정적 분석 API 는 동작하며, 디버거는 분명한 안내로 거절
- PyInstaller 로 묶인(frozen) 경우 프런트엔드는 번들 안에서, 데이터는 사용자 폴더에서 찾는다

실행:  .venv/bin/python tests/test_desktop_backend.py
"""
import http.client, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "bin", "python")
PY = PY if os.path.exists(PY) else sys.executable
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", str(detail)))


def req(port, method, path, token=None, body=None):
    for _ in range(60):
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = {"X-Rework-Token": token} if token else {}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            out = r.read()
            hdr = {k.lower(): v for k, v in r.getheaders()}
            c.close()
            return r.status, out, hdr
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("서버가 응답하지 않음")


def start_desktop(data, token, via_stdin=True):
    env = {k: v for k, v in os.environ.items() if k != "REWORK_TOKEN"}
    env["REWORK_DATA"] = data
    args = [PY, os.path.join(ROOT, "run.py"), "--desktop", "--port", "0"]
    if via_stdin:
        args.append("--token-stdin")
    else:
        env["REWORK_TOKEN"] = token
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
    if via_stdin:
        p.stdin.write(token + "\n")
        p.stdin.flush()
    t0 = time.time()
    while time.time() - t0 < 30:
        line = p.stdout.readline()
        if not line:
            break
        if line.startswith("REWORK_PORT="):
            return p, int(line.split("=", 1)[1])
    p.kill()
    raise RuntimeError("REWORK_PORT 를 받지 못함")


def wait_exit(p, secs):
    t0 = time.time()
    while p.poll() is None and time.time() - t0 < secs:
        time.sleep(0.1)
    return p.poll(), time.time() - t0


def desktop_section():
    data = os.path.join(tempfile.mkdtemp(prefix="rework-desk-"), "data")        # 아직 없는 폴더: 서버가 만든다
    tok = "tk-" + os.urandom(8).hex()
    p, port = start_desktop(data, tok)
    p2 = port2 = None
    try:
        check("포트 0 → 운영체제가 고른 포트를 REWORK_PORT 로 알림", 1024 < port < 65536, port)
        st, _, _ = req(port, "GET", "/api/about")
        check("토큰 없이 API 는 401", st == 401, st)
        st, body, hdr = req(port, "GET", "/api/about", tok)
        about = json.loads(body)
        environ = open(f"/proc/{p.pid}/environ", "rb").read()
        check("표준 입력으로 받은 토큰은 프로세스 환경(/proc/<pid>/environ)에도 명령줄에도 없다",
              tok.encode() not in environ and b"REWORK_TOKEN" not in environ and tok.encode() not in open(f"/proc/{p.pid}/cmdline", "rb").read(), "")
        check("토큰이 있으면 200, 데스크톱 모드 표시", st == 200 and about["desktop"] is True and about["version"], body[:120])
        check("데이터 폴더가 0700 으로 만들어짐", os.stat(data).st_mode & 0o777 == 0o700, oct(os.stat(data).st_mode & 0o777))
        st, _, _ = req(port, "POST", "/api/ai/config", tok, {"provider": "ollama", "api_key": "sk-secret"})
        mode = os.stat(os.path.join(data, "ai.json")).st_mode & 0o777
        check("API 키가 든 ai.json 은 0600", st == 200 and mode == 0o600, oct(mode))
        csp = hdr.get("content-security-policy", "")
        check("엄격한 CSP(같은 출처 스크립트만·default-src none·인라인 스크립트 없음)",
              "default-src 'none'" in csp and "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
              and "script-src 'self' 'unsafe-inline'" not in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp, csp[:140])
        st, body, hdr2 = req(port, "GET", "/")
        check("UI 페이지에도 CSP", st == 200 and "script-src 'self'" in hdr2.get("content-security-policy", ""))
        html = body.decode("utf-8")
        check("UI 에 인라인 스크립트·인라인 이벤트 핸들러가 없다(CSP 와 양립)", "<script>" not in html and " onclick=" not in html and "javascript:" not in html)
        # 두 번째 서버는 다른 포트
        data2 = os.path.join(tempfile.mkdtemp(prefix="rework-desk2-"), "data")
        p2, port2 = start_desktop(data2, tok + "2")
        check("두 서버는 서로 다른 포트", port2 != port, f"{port} / {port2}")
        shutil.rmtree(os.path.dirname(data2), ignore_errors=True)
        p.stdin.close()
        code, dt = wait_exit(p, 20)
        check("표준 입력이 닫히면(부모 사망) 정상 종료", code == 0 and dt < 15, f"code={code} {dt:.1f}s")
        p2.stdin.close()
        wait_exit(p2, 20)
    finally:
        for q in (p, p2):
            if q is not None and q.poll() is None:
                q.kill()
        shutil.rmtree(os.path.dirname(data), ignore_errors=True)
    r = subprocess.run([PY, os.path.join(ROOT, "run.py"), "--desktop", "--port", "0"], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "REWORK_TOKEN": ""})
    check("--desktop 은 토큰이 없으면 시작하지 않음", r.returncode != 0 and "토큰" in r.stderr, (r.stderr or r.stdout).strip()[:80])
    for label, inp in (("짧은 토큰", "abc\n"), ("비 ASCII 토큰", "한글토큰한글토큰한글토큰한글토큰\n"), ("빈 입력", "")):
        r = subprocess.run([PY, os.path.join(ROOT, "run.py"), "--desktop", "--token-stdin", "--port", "0"], input=inp, capture_output=True,
                           text=True, timeout=30, env={k: v for k, v in os.environ.items() if k != "REWORK_TOKEN"})
        check(f"--token-stdin: {label}은 거부", r.returncode != 0 and "토큰" in r.stderr, (r.stderr or "").strip()[:70])
    d3 = os.path.join(tempfile.mkdtemp(prefix="rework-desk3-"), "data")
    p3, port3 = start_desktop(d3, "env-token-0123456789", via_stdin=False)
    try:
        st, _, _ = req(port3, "GET", "/api/about", "env-token-0123456789")
        check("환경 변수 토큰(스크립트·개발용)도 여전히 받는다", st == 200, st)
    finally:
        p3.stdin.close()
        wait_exit(p3, 20)
        if p3.poll() is None:
            p3.kill()
        shutil.rmtree(os.path.dirname(d3), ignore_errors=True)


def windows_like_section():
    code = r'''
import os, sys, json, tempfile
for m in ("fcntl", "pty", "termios", "resource", "pwd", "grp"):
    sys.modules[m] = None                      # Windows 처럼 이 모듈들이 없다
os.environ["REWORK_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(%r, "backend"))
import app.main as M
from app import debug
from fastapi import HTTPException
out = {"posix": debug.HAVE_POSIX_DEBUGGER}
try:
    debug.DebugSession("/bin/true").start()
except debug.DebugError as e:
    out["start"] = str(e)
out["report"] = M._sandbox_report()["debugger"]
src = os.path.join(%r, "samples", "demo_elf")
info = M.api_open({"path": src})
try:
    M.api_debug_start({})
except HTTPException as e:
    out["api_start"] = [e.status_code, e.detail]
out["static"] = [info["counts"]["functions"], len(M.api_functions(query="", offset=0, limit=50)["items"])]
fid = [f for f in M.api_functions(query="main", offset=0, limit=50)["items"] if f["name"] == "main"][0]["vaddr"]
out["decompile"] = len(M.api_decompile(hex(fid))["lines"])
print("JSON:" + json.dumps(out, ensure_ascii=False))
''' % (ROOT, ROOT)
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=120)
    line = next((l for l in r.stdout.splitlines() if l.startswith("JSON:")), None)
    if not line:
        check("Windows 처럼 POSIX 모듈이 없어도 서버가 임포트된다", False, (r.stderr or r.stdout)[-300:])
        return
    o = json.loads(line[5:])
    check("Windows 처럼 POSIX 모듈이 없어도 서버가 임포트되고 정적 분석(열기·함수·의사코드)이 동작",
          o["posix"] is False and o["static"][0] >= 10 and o["static"][1] >= 10 and o["decompile"] > 3, o)
    check("디버거 시작은 분명한 안내와 함께 거절(세션·400)", "Windows" in o["start"] and o["api_start"][0] == 400 and "Windows" in o["api_start"][1], o["start"][:60])
    check("격리 상태 보고가 ‘디버거 미지원’을 알려 UI 가 시작 버튼 대신 안내를 보임", o["report"]["supported"] is False and o["report"]["reason"], o["report"])


def frozen_section():
    home = tempfile.mkdtemp(prefix="rework-frozen-home-")
    bundle = tempfile.mkdtemp(prefix="rework-frozen-bundle-")
    os.makedirs(os.path.join(bundle, "frontend"))
    open(os.path.join(bundle, "frontend", "index.html"), "w").write("<html>bundle</html>")
    code = r'''
import os, sys, json
sys.frozen = True
sys._MEIPASS = %r
sys.path.insert(0, os.path.join(%r, "backend"))
import app.main as M
print("JSON:" + json.dumps({"frontend": M.FRONTEND, "data": M.DATA_DIR, "frozen": M.FROZEN}))
''' % (bundle, ROOT)
    env = {k: v for k, v in os.environ.items() if k not in ("REWORK_DATA", "REWORK_FRONTEND")}
    env["HOME"] = home
    try:
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=60, env=env)
        line = next((l for l in r.stdout.splitlines() if l.startswith("JSON:")), None)
        o = json.loads(line[5:]) if line else {}
        check("동결 실행: 프런트엔드는 번들 안, 데이터는 사용자 폴더(~/.rework/data)",
              o.get("frontend") == os.path.join(bundle, "frontend") and o.get("data") == os.path.join(home, ".rework", "data") and o.get("frozen") is True,
              o or (r.stderr or r.stdout)[-200:])
        check("동결 실행: 데이터 폴더가 0700", os.path.isdir(os.path.join(home, ".rework", "data"))
              and os.stat(os.path.join(home, ".rework", "data")).st_mode & 0o777 == 0o700)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(bundle, ignore_errors=True)


def main():
    for fn in (desktop_section, windows_like_section, frozen_section):
        try:
            fn()
        except Exception as e:                                  # noqa: BLE001
            import traceback
            check(fn.__name__, False, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, s, d in RESULTS:
        print(f"{s:4} {n:<{w}}  {d[:240]}")
        bad += s != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_desktop_backend_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
