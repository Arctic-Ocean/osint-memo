"""로컬 웹 서버 방어 회귀 테스트 — Host 헤더(DNS rebinding)와 교차 출처 요청(CSRF).

실제 서버(run.py)를 임시 데이터 폴더·빈 포트로 띄워 http.client 로 헤더를 직접 만든다.
- Host 가 로컬 주소가 아니면(userinfo·접미사 속임수·점 붙이기·빈 값 포함) 403
- 다른 출처의 웹페이지가 보낸 상태 변경 요청(Origin 불일치·Sec-Fetch-Site 가 same-origin/none 이 아님)은 403 —
  multipart 업로드와 본문 없는 POST 는 CORS 사전 요청 없이 보낼 수 있어 FastAPI 의 JSON 검사로는 막히지 않는다.
- curl·스크립트처럼 그 헤더가 없는 요청과 같은 출처 요청은 통과

실행:  .venv/bin/python tests/test_web_security.py
"""
import asyncio, http.client, json, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = []
LAST = {"headers": {}}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(port, method, path, headers=None, body=None, host="__default__"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    if host == "__default__":
        host = f"127.0.0.1:{port}"
    if host is not None:
        c.putheader("Host", host)
    items = headers.items() if isinstance(headers, dict) else (headers or [])
    for k, v in items:
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    LAST["headers"] = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, data


MULTIPART = (b"--XB\r\nContent-Disposition: form-data; name=\"file\"; filename=\"csrf_evil.bin\"\r\n"
             b"Content-Type: application/octet-stream\r\n\r\nEVIL\r\n--XB--\r\n")
MP_HEADERS = {"Content-Type": "multipart/form-data; boundary=XB"}


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


def token_section():
    """토큰 인증(--require-token / REWORK_TOKEN): 같은 컴퓨터의 다른 사용자·프로세스(와 network=host 대상)가 API 를 못 부른다."""
    data = tempfile.mkdtemp(prefix="rework-web-tok-")
    port = free_port()
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    tok = "T0ken-" + os.urandom(6).hex()
    proc = subprocess.Popen([py, os.path.join(ROOT, "run.py"), "--port", str(port)],
                            env={**os.environ, "REWORK_DATA": data, "REWORK_TOKEN": tok},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                if req(port, "GET", "/")[0] in (200, 303):
                    break
            except OSError:
                time.sleep(0.2)
        st, _ = req(port, "GET", "/api/sandbox/status")
        check("토큰 없이 API 호출은 401", st == 401, f"{st}")
        st, _ = req(port, "POST", "/api/debug/stop", {}, b"")
        check("토큰 없이 POST 도 401", st == 401, f"{st}")
        st, _ = req(port, "GET", "/api/sandbox/status", {"X-Rework-Token": "wrong"})
        check("틀린 토큰 헤더는 401", st == 401, f"{st}")
        st, body = req(port, "GET", "/api/sandbox/status", {"X-Rework-Token": tok})
        check("올바른 토큰 헤더는 통과하고 서버가 토큰 인증 켜짐을 알림", st == 200 and json.loads(body)["server"]["token_auth"] is True, f"{st}")
        st, _ = req(port, "GET", "/?token=wrong")
        check("틀린 ?token= 은 401", st == 401, f"{st}")
        st, _ = req(port, "GET", f"/?token={tok}")
        cookie = LAST["headers"].get("set-cookie", "")
        check("올바른 ?token= 은 HttpOnly·SameSite=Strict 쿠키를 주고 이동", st == 303 and "rework_token=" in cookie
              and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict"), f"{st} {cookie[:80]}")
        ck = cookie.split(";")[0]
        st, _ = req(port, "GET", "/api/sandbox/status", {"Cookie": ck})
        check("쿠키만으로도 통과(브라우저 UI)", st == 200, f"{st}")
        st, _ = req(port, "GET", "/api/sandbox/status", {"Cookie": ck}, host="evil.com")
        check("토큰이 있어도 Host 위조는 403", st == 403, f"{st}")
        st, _ = req(port, "POST", "/api/debug/stop", {"Cookie": ck, "Origin": "http://evil.example"}, b"")
        check("토큰(쿠키)이 실려도 교차 출처 POST 는 403", st == 403, f"{st}")
        # 같은 호스트의 다른 포트에서 도는 서비스의 페이지: SameSite=Strict 쿠키도 같은 '사이트'라 실려 오지만 출처 검사에서 막힌다
        r = [req(port, "GET", "/api/debug/output?clear=true", {"Cookie": ck, "Sec-Fetch-Site": "same-site"})[0],
             req(port, "GET", "/api/sandbox/status", {"Cookie": ck, "Origin": f"http://127.0.0.1:{port + 1}"})[0]]
        check("쿠키가 실려도 다른 포트 페이지의 GET 은 403(쿠키는 포트를 구분하지 않는다)", r == [403, 403], str(r))
        r = [req(port, "GET", "/api/sandbox/status", [("X-Rework-Token", "T\xed\x95\x9c")])[0],       # 비 ASCII 값은 500 이 아니라 401
             req(port, "GET", "/api/sandbox/status", {"Cookie": "rework_token=\"\\355\\225\\234\""})[0],
             req(port, "GET", "/?token=%ED%95%9C")[0]]
        check("비 ASCII 토큰 값은 500 이 아니라 401", r == [401, 401, 401], str(r))
        st, _ = req(port, "POST", "/api/open", {"X-Rework-Token": tok, "Content-Type": "application/json"},
                    json.dumps({"path": os.path.join(ROOT, "samples", "demo_elf")}).encode())
        st, body = req(port, "POST", "/api/debug/start", {"X-Rework-Token": tok, "Content-Type": "application/json"},
                       json.dumps({"breakpoints": [], "sandbox": {"network": "host"}}).encode())
        check("토큰 인증이 켜져 있으면 network=host 시작 가능", st == 200, f"{st} {body[:120]!r}")
        req(port, "POST", "/api/debug/stop", {"X-Rework-Token": tok}, b"")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(data, ignore_errors=True)


def _start_server(env_extra):
    data = tempfile.mkdtemp(prefix="rework-web-x-")
    port = free_port()
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    proc = subprocess.Popen([py, os.path.join(ROOT, "run.py"), "--port", str(port)],
                            env={**os.environ, "REWORK_DATA": data, **env_extra},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 20:
        try:
            if req(port, "GET", "/api/sandbox/status")[0] == 200:
                return proc, port, data
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("서버가 뜨지 않음")


def _stop_server(proc, data):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(data, ignore_errors=True)


def _raw_upload(port, total, chunked):
    """multipart 본문 total 바이트를 소켓으로 직접 보낸다. (상태 줄 또는 None, 보낸 바이트)"""
    head_body = b'--XB\r\nContent-Disposition: form-data; name="file"; filename="big.bin"\r\n\r\n'
    tail = b"\r\n--XB--\r\n"
    pad = max(0, total - len(head_body) - len(tail))
    s = socket.create_connection(("127.0.0.1", port), timeout=15)
    hdr = (f"POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: multipart/form-data; boundary=XB\r\n"
           + ("Transfer-Encoding: chunked\r\n" if chunked else f"Content-Length: {len(head_body) + pad + len(tail)}\r\n") + "\r\n")
    sent = 0
    try:
        s.sendall(hdr.encode())
        if not chunked:
            s.sendall(head_body[:200])                          # 머리글만 보내고 응답을 먼저 확인(큰 본문을 다 보내지 않는다)
            s.settimeout(5)
            try:
                return s.recv(4096).split(b"\r\n", 1)[0].decode("latin-1") or None, 0
            except (socket.timeout, OSError):
                return None, 0
        body = head_body + b"\0" * pad + tail
        for i in range(0, len(body), 1 << 18):
            piece = body[i:i + (1 << 18)]
            s.sendall(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
            sent += len(piece)
        s.sendall(b"0\r\n\r\n")
    except OSError:
        pass                                                    # 서버가 중간에 끊었다
    s.settimeout(5)
    try:
        return s.recv(4096).split(b"\r\n", 1)[0].decode("latin-1") or None, sent
    except (socket.timeout, OSError):
        return None, sent


def upload_limit_section():
    """업로드 크기 상한(REWORK_MAX_FILE_MB): 서버 메모리·디스크를 큰 본문 하나로 채우지 못하게 한다."""
    proc, port, data = _start_server({"REWORK_MAX_FILE_MB": "1"})
    try:
        uploads = os.path.join(data, "uploads")
        line, _ = _raw_upload(port, 4 << 20, chunked=False)
        check("Content-Length 가 상한을 넘으면 본문을 받기 전에 413", bool(line) and " 413 " in line, str(line))
        line, sent = _raw_upload(port, 8 << 20, chunked=True)
        check("청크 전송도 상한을 넘으면 끊고 413(또는 연결 종료) — 끝까지 받지 않는다",
              (line is None or " 413 " in line) and sent < (8 << 20), f"{line} sent={sent}")
        left = [n for n in os.listdir(uploads)] if os.path.isdir(uploads) else []
        check("상한을 넘긴 업로드는 파일을 남기지 않음", left == [], str(left))
        st, body = req(port, "POST", "/api/upload", MP_HEADERS, MULTIPART)
        check("상한 안의 업로드는 정상 처리(잘못된 형식이라 400)", st == 400, f"{st} {body[:80]!r}")
        big = os.path.join(data, "big.bin")
        with open(big, "wb") as fh:
            fh.truncate(3 << 20)
        st, body = req(port, "POST", "/api/open", {"Content-Type": "application/json"}, json.dumps({"path": big}).encode())
        check("열기도 상한을 넘으면 400(분석하지 않음)", st == 400 and "너무 큽니다" in body.decode(), f"{st} {body[:100]!r}")
    finally:
        _stop_server(proc, data)


def _asgi(app, method, path, headers=(), body=b""):
    """서버 없이 ASGI 앱을 직접 한 번 호출한다. (상태, 헤더 dict, 본문)"""
    out = {"status": None, "headers": {}, "body": b""}

    async def run():
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
                out["headers"] = {k.decode().lower(): v.decode() for k, v in msg["headers"]}
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
                 "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
                 "headers": [(b"host", b"127.0.0.1:8000")] + [(k.lower().encode(), v.encode()) for k, v in headers],
                 "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000)}
        try:
            await app(scope, receive, send)
        except Exception:                                       # noqa: BLE001  Starlette 은 응답을 보낸 뒤 예외를 다시 던진다
            pass
    asyncio.run(run())
    return out["status"], out["headers"], out["body"]


def unhandled_section():
    """처리되지 않은 예외가 나도 500 응답에 보안 헤더가 붙고 경로·트레이스백이 새지 않는다."""
    data = tempfile.mkdtemp(prefix="rework-web-unh-")
    code = r"""
import os, sys, json
sys.path.insert(0, os.path.join(%r, "backend"))
sys.path.insert(0, %r)
from app.main import app
from starlette.routing import Route
from starlette.responses import PlainTextResponse
def boom(request):
    raise RuntimeError("비밀 경로 /home/someone/secret")
app.router.routes.insert(0, Route("/api/__boom", boom))
import test_web_security as T
st, h, body = T._asgi(app, "GET", "/api/__boom")
print(json.dumps({"st": st, "h": h, "body": body.decode()}))
""" % (ROOT, HERE)
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60,
                           env={**os.environ, "REWORK_DATA": data})
        got = json.loads(r.stdout.strip().splitlines()[-1])
        h = got["h"]
        check("처리되지 않은 예외는 500 이고 보안 헤더가 붙는다",
              got["st"] == 500 and h.get("x-frame-options") == "DENY" and h.get("x-content-type-options") == "nosniff"
              and "frame-ancestors" in h.get("content-security-policy", ""), f"{got['st']} {h}")
        check("500 응답 본문에 예외 내용·경로가 없다", "secret" not in got["body"] and "/home" not in got["body"] and "Traceback" not in got["body"], got["body"][:100])
    except Exception as e:                                      # noqa: BLE001
        check("처리되지 않은 예외 응답 검사", False, f"{type(e).__name__}: {e} {(locals().get('r') and r.stderr[-300:])}")
    finally:
        shutil.rmtree(data, ignore_errors=True)


def bad_token_env_section():
    """비 ASCII REWORK_TOKEN 은 시작할 때 분명한 오류로 거부한다(조용히 모든 요청을 500 으로 만들지 않는다)."""
    data = tempfile.mkdtemp(prefix="rework-web-bt-")
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    try:
        r = subprocess.run([py, "-c", "import sys; sys.path.insert(0, %r); import app.main" % os.path.join(ROOT, "backend")],
                           capture_output=True, text=True, timeout=60, env={**os.environ, "REWORK_DATA": data, "REWORK_TOKEN": "한글토큰"})
        check("비 ASCII REWORK_TOKEN 은 시작 때 거부", r.returncode != 0 and "ASCII" in r.stderr, r.stderr.strip().splitlines()[-1][:120] if r.stderr else "")
    finally:
        shutil.rmtree(data, ignore_errors=True)


def main():
    data = tempfile.mkdtemp(prefix="rework-web-")
    port = free_port()
    py = os.path.join(ROOT, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    proc = subprocess.Popen([py, os.path.join(ROOT, "run.py"), "--port", str(port)],
                            env={**os.environ, "REWORK_DATA": data}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                if req(port, "GET", "/api/sandbox/status")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("서버가 뜨지 않음")
        P = f"{port}"

        # ---- Host 헤더 ----
        bad_hosts = ["evil.com", "127.0.0.1.evil.com", "localhost.evil.com", "localhost:80@evil.com",
                     "127.0.0.1:80@evil.com", "localhost.", "", "::1", "LOCALHOST.evil.com", "localhost evil.com",
                     "localhost/evil", "[::1", "evil.com:80", "127.0.0.1:99999999999"]
        leaked = []
        for h in bad_hosts:
            try:
                st, _ = req(port, "GET", "/api/sandbox/status", host=h)
            except Exception as e:                    # noqa: BLE001  연결 거부도 거절의 한 형태
                continue
            if st != 403:
                leaked.append((h, st))
        check("Host 속임수 14종은 모두 403", not leaked, f"통과해 버린 값: {leaked}")
        good = [f"127.0.0.1:{P}", f"localhost:{P}", f"[::1]:{P}", f"LOCALHOST:{P}", "127.0.0.1", "localhost"]
        refused = [(h, req(port, "GET", "/api/sandbox/status", host=h)[0]) for h in good]
        check("정상 로컬 Host 6종은 허용", all(st == 200 for _, st in refused), str(refused))
        st, _ = req(port, "GET", "/api/sandbox/status", host=None)
        check("Host 헤더 없음은 403", st == 403, f"{st}")
        st, _ = req(port, "GET", "/api/sandbox/status", headers={"X-Forwarded-Host": "127.0.0.1"}, host="evil.com")
        check("X-Forwarded-Host 로 속일 수 없음", st == 403, f"{st}")

        # ---- 교차 출처 상태 변경 요청 ----
        uploads = os.path.join(data, "uploads")
        cases = [
            ("다른 출처 Origin", {"Origin": "http://evil.example"}),
            ("Sec-Fetch-Site: cross-site", {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}),
            ("Sec-Fetch-Site: same-site(다른 포트의 같은 호스트)", {"Sec-Fetch-Site": "same-site"}),
            ("Origin: null(샌드박스 iframe·file://)", {"Origin": "null"}),
            ("같은 호스트 다른 표기(localhost ≠ 127.0.0.1)", {"Origin": f"http://localhost:{P}"}),
            ("다른 포트의 같은 호스트", {"Origin": f"http://127.0.0.1:{int(P) + 1}"}),
        ]
        for name, hdr in cases:
            r1, _ = req(port, "POST", "/api/upload", {**MP_HEADERS, **hdr}, MULTIPART)
            r2, _ = req(port, "POST", "/api/debug/stop", hdr, b"")
            r3, _ = req(port, "POST", "/api/sandbox/recheck", hdr, b"")
            r4, _ = req(port, "POST", "/api/sandbox/config", {**hdr, "Content-Type": "application/json"}, b'{"memory_mb":128}')
            check(f"차단: {name}", (r1, r2, r3, r4) == (403, 403, 403, 403), f"upload={r1} stop={r2} recheck={r3} config={r4}")
        check("차단된 교차 출처 업로드는 파일을 만들지 않음",
              not os.path.exists(uploads) or not os.listdir(uploads), str(os.listdir(uploads) if os.path.exists(uploads) else []))
        st, body = req(port, "GET", "/api/sandbox/status")
        check("차단된 설정 변경은 적용되지 않음", json.loads(body)["policy"]["memory_mb"] == 2048)

        # ---- 정상 요청은 통과 ----
        same = {"Origin": f"http://127.0.0.1:{P}", "Sec-Fetch-Site": "same-origin"}
        ok = [req(port, "POST", "/api/debug/stop", same, b"")[0],
              req(port, "POST", "/api/debug/stop", {}, b"")[0],                              # curl·스크립트(헤더 없음)
              req(port, "POST", "/api/debug/stop", {"Sec-Fetch-Site": "none"}, b"")[0]]       # 주소창 직접 이동
        check("같은 출처·헤더 없음·none 은 통과", ok == [200, 200, 200], str(ok))
        st, _ = req(port, "POST", "/api/upload", {**MP_HEADERS, **same}, MULTIPART)
        check("같은 출처 업로드는 처리됨(서버가 받아 분석 시도)", st != 403, f"{st}")
        # GET API 도 다른 출처가 부르지 못한다: 쿠키(SameSite)는 포트를 구분하지 않아, 같은 호스트의 다른 포트에서 도는 서비스의
        # 페이지가 보낸 GET 에도 실려 가기 때문이다. 헤더가 없는 curl·스크립트와 정적 페이지(/)는 영향이 없다.
        gets = {}
        for name, hdr in [("cross-site", {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"}),
                          ("same-site(다른 포트)", {"Sec-Fetch-Site": "same-site"}),
                          ("다른 포트 Origin", {"Origin": f"http://127.0.0.1:{int(P) + 1}"})]:
            gets[name] = (req(port, "GET", "/api/sandbox/status", hdr)[0], req(port, "GET", "/api/debug/output?clear=true", hdr)[0])
        check("GET API 도 다른 출처·다른 포트에서는 403", all(v == (403, 403) for v in gets.values()), str(gets))
        okg = [req(port, "GET", "/api/sandbox/status", h)[0] for h in ({}, same, {"Sec-Fetch-Site": "none"})]
        check("GET API 는 같은 출처·헤더 없음·none 이면 통과", okg == [200, 200, 200], str(okg))
        st, _ = req(port, "GET", "/", {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
        check("정적 페이지(/)는 다른 출처에서도 열림(API 가 아님)", st == 200, f"{st}")

        # ---- 보안 헤더·문서·중복 헤더·스킴 ----
        st, _ = req(port, "GET", "/")
        h = LAST["headers"]
        check("응답에 프레임 삽입 차단·nosniff 헤더", h.get("x-frame-options") == "DENY" and "frame-ancestors 'none'" in h.get("content-security-policy", "")
              and h.get("x-content-type-options") == "nosniff" and h.get("referrer-policy") == "no-referrer", str({k: h.get(k) for k in ("x-frame-options", "content-security-policy")}))
        st, _ = req(port, "GET", "/api/sandbox/status")
        check("API 응답에도 보안 헤더", LAST["headers"].get("x-frame-options") == "DENY")
        docs = [req(port, "GET", u)[0] for u in ("/api/docs", "/redoc", "/api/openapi.json", "/docs")]
        check("외부 CDN 스크립트를 쓰는 자동 문서는 꺼져 있음", all(c == 404 for c in docs), str(docs))
        dup = [req(port, "POST", "/api/debug/stop", [("Origin", f"http://127.0.0.1:{P}"), ("Origin", "http://evil.example")], b"")[0],
               req(port, "POST", "/api/debug/stop", [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site")], b"")[0],
               req(port, "GET", "/api/sandbox/status", [("Host", "evil.com")])[0]]
        check("같은 헤더가 여러 개인 요청은 400", dup == [400, 400, 400], str(dup))
        st, _ = req(port, "POST", "/api/debug/stop", {"Origin": f"https://127.0.0.1:{P}"}, b"")
        check("스킴이 다른 Origin(https)은 거부", st == 403, f"{st}")
        # network=host 는 토큰 인증이 꺼져 있으면 시작하지 않는다(대상이 127.0.0.1 의 API 에 닿아 격리를 끌 수 있으므로)
        st, body = req(port, "POST", "/api/open", {"Content-Type": "application/json"},
                       json.dumps({"path": os.path.join(ROOT, "samples", "demo_elf")}).encode())
        st, body = req(port, "POST", "/api/debug/start", {"Content-Type": "application/json"},
                       json.dumps({"breakpoints": [], "sandbox": {"network": "host"}}).encode())
        check("토큰 인증 없이 network=host 는 400", st == 400 and "토큰" in body.decode(), f"{st} {body[:120]!r}")
        req(port, "POST", "/api/debug/stop", {}, b"")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(data, ignore_errors=True)
    token_section()
    upload_limit_section()
    unhandled_section()
    bad_token_env_section()
    w = max(len(n) for n, _, _ in RESULTS)
    bad = 0
    for n, s, d in RESULTS:
        print(f"{s:4} {n:<{w}}  {d}")
        bad += s != "PASS"
    print(f"\n== {len(RESULTS) - bad}/{len(RESULTS)} 통과 ==")
    return bad


def test_web_security_suite():
    RESULTS.clear()
    assert main() == 0


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
