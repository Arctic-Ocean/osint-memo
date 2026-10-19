"""로컬 웹 서버 방어 회귀 테스트 — Host 헤더(DNS rebinding)와 교차 출처 요청(CSRF).

실제 서버(run.py)를 임시 데이터 폴더·빈 포트로 띄워 http.client 로 헤더를 직접 만든다.
- Host 가 로컬 주소가 아니면(userinfo·접미사 속임수·점 붙이기·빈 값 포함) 403
- 다른 출처의 웹페이지가 보낸 상태 변경 요청(Origin 불일치·Sec-Fetch-Site 가 same-origin/none 이 아님)은 403 —
  multipart 업로드와 본문 없는 POST 는 CORS 사전 요청 없이 보낼 수 있어 FastAPI 의 JSON 검사로는 막히지 않는다.
- curl·스크립트처럼 그 헤더가 없는 요청과 같은 출처 요청은 통과

실행:  .venv/bin/python tests/test_web_security.py
"""
import http.client, os, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = []


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
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


MULTIPART = (b"--XB\r\nContent-Disposition: form-data; name=\"file\"; filename=\"csrf_evil.bin\"\r\n"
             b"Content-Type: application/octet-stream\r\n\r\nEVIL\r\n--XB--\r\n")
MP_HEADERS = {"Content-Type": "multipart/form-data; boundary=XB"}


def check(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))


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
        import json
        check("차단된 설정 변경은 적용되지 않음", json.loads(body)["policy"]["memory_mb"] == 2048)

        # ---- 정상 요청은 통과 ----
        same = {"Origin": f"http://127.0.0.1:{P}", "Sec-Fetch-Site": "same-origin"}
        ok = [req(port, "POST", "/api/debug/stop", same, b"")[0],
              req(port, "POST", "/api/debug/stop", {}, b"")[0],                              # curl·스크립트(헤더 없음)
              req(port, "POST", "/api/debug/stop", {"Sec-Fetch-Site": "none"}, b"")[0]]       # 주소창 직접 이동
        check("같은 출처·헤더 없음·none 은 통과", ok == [200, 200, 200], str(ok))
        st, _ = req(port, "POST", "/api/upload", {**MP_HEADERS, **same}, MULTIPART)
        check("같은 출처 업로드는 처리됨(서버가 받아 분석 시도)", st != 403, f"{st}")
        st, _ = req(port, "GET", "/api/sandbox/status", {"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
        check("GET 은 막지 않음(부작용 없음, 응답은 CORS 로 못 읽음)", st == 200, f"{st}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(data, ignore_errors=True)
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
