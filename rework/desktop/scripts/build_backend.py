#!/usr/bin/env python3
"""PyInstaller 로 분석 백엔드(run.py + backend/ + frontend/)를 실행 파일 하나로 묶는다.

  python3 scripts/build_backend.py                # 이 운영체제용 → build/backend-linux 또는 build/backend-win
  python3 scripts/build_backend.py --target wsl   # 리눅스에서: Windows 설치본이 WSL2 에서 쓸 리눅스 백엔드 → build/backend-wsl

PyInstaller 는 크로스 컴파일을 하지 못하므로 각 운영체제에서 따로 돌려야 한다(CI 워크플로가 이를 한다).
결과 폴더에는 `rework-backend`(Windows 는 .exe)와 그 옆의 라이브러리가 들어 있고, 만든 직후 실제로 띄워 보는 스모크 테스트를 한다.
"""
import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
REPO = os.path.dirname(DESKTOP)
IS_WIN = sys.platform == "win32"
PYINSTALLER_VERSION = "6.22.3"


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.check_call(cmd, **kw)


def smoke(exe: str) -> None:
    """만든 실행 파일을 데스크톱 모드로 띄워 REWORK_PORT·토큰 인증·정적 UI·표준 입력 EOF 종료를 확인한다."""
    token = "smoke-" + os.urandom(6).hex()
    data = tempfile.mkdtemp(prefix="rework-smoke-")
    env = {k: v for k, v in os.environ.items() if k != "REWORK_TOKEN"}
    env["REWORK_DATA"] = data
    p = subprocess.Popen([exe, "--desktop", "--token-stdin", "--port", "0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=env, text=True)
    p.stdin.write(token + "\n")                              # 셸과 같은 방식: 토큰은 표준 입력 첫 줄로
    p.stdin.flush()
    try:
        port = None
        t0 = time.time()
        while time.time() - t0 < 60:
            line = p.stdout.readline()
            if not line:
                break
            print("  backend:", line.rstrip())
            if line.startswith("REWORK_PORT="):
                port = int(line.split("=", 1)[1])
                break
        if port is None:
            raise SystemExit("스모크 실패: REWORK_PORT 를 받지 못했습니다.")

        def get(path, headers=None):
            for _ in range(60):
                try:
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    c.request("GET", path, headers=headers or {})
                    r = c.getresponse()
                    return r.status, r.read()
                except OSError:
                    time.sleep(0.25)
            raise SystemExit("스모크 실패: 서버가 응답하지 않습니다.")
        st, _ = get("/api/about")
        if st != 401:
            raise SystemExit(f"스모크 실패: 토큰 없이 /api/about 이 {st}(401 이어야 함)")
        st, body = get("/api/about", {"X-Rework-Token": token})
        if st != 200 or b'"frozen":true' not in body.replace(b" ", b""):
            raise SystemExit(f"스모크 실패: /api/about {st} {body[:120]!r}")
        st, body = get("/")
        if st != 200 or b"rework" not in body:
            raise SystemExit(f"스모크 실패: 정적 UI 를 서빙하지 못함 {st}")
        st, body = get("/js/app.js")
        if st != 200 or len(body) < 1000:
            raise SystemExit("스모크 실패: 프런트엔드 파일이 번들에 없음")
        for asset, minimum in (("/vendor/monaco/vs/loader.js", 1000), ("/vendor/monaco/vs/editor/editor.main.js", 100000),
                               ("/vendor/monaco/vs/nls.messages.ko.js", 1000), ("/vendor/monaco/worker.js", 50)):
            st, body = get(asset)
            if st != 200 or len(body) < minimum:
                raise SystemExit(f"스모크 실패: 편집기(Monaco) 파일이 번들에 없음: {asset} {st}")
        folder = tempfile.mkdtemp(prefix="rework-smoke-ws-")
        try:
            with open(os.path.join(folder, "a.c"), "w", encoding="utf-8") as fh:
                fh.write("int main(void){return 0;}\n")
            hdr = {"X-Rework-Token": token, "Content-Type": "application/json"}
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/api/ws/open", json.dumps({"path": folder}), hdr)
            r = c.getresponse(); r.read()
            if r.status != 200:
                raise SystemExit(f"스모크 실패: 작업 폴더 열기 {r.status}")
            st, body = get("/api/ws/file?path=a.c", {"X-Rework-Token": token})
            if st != 200 or "int main" not in json.loads(body)["text"]:
                raise SystemExit(f"스모크 실패: 작업 폴더 파일 읽기 {st}")
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("POST", "/api/ws/close", "", hdr)
            c.getresponse().read()
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        p.stdin.close()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            raise SystemExit("스모크 실패: 표준 입력을 닫아도 종료하지 않음")
        print(f"스모크 통과: 포트 {port}, 토큰 401/200, UI·JS·Monaco 서빙, 작업 폴더 API, stdin EOF 로 종료(코드 {p.returncode})")
    finally:
        if p.poll() is None:
            p.kill()
        shutil.rmtree(data, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["native", "wsl"], default="native",
                    help="wsl: 리눅스에서 만들어 Windows 설치본에 넣을 WSL2 용 백엔드")
    ap.add_argument("--python", default=sys.executable, help="venv 를 만들 파이썬")
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--keep-venv", action="store_true", default=True)
    args = ap.parse_args()

    name = "wsl" if args.target == "wsl" else ("win" if IS_WIN else "linux")
    if args.target == "wsl" and IS_WIN:
        raise SystemExit("--target wsl 은 리눅스에서 만듭니다.")
    build = os.path.join(DESKTOP, "build")
    venv = os.path.join(build, f"venv-{name}")
    out = os.path.join(build, f"backend-{name}")
    work = os.path.join(build, f"pyi-{name}")
    os.makedirs(build, exist_ok=True)

    if not os.path.isdir(venv):
        run([args.python, "-m", "venv", venv])
    py = os.path.join(venv, "Scripts" if IS_WIN else "bin", "python" + (".exe" if IS_WIN else ""))
    run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    lock = os.path.join(REPO, "backend", "requirements.lock")          # 릴리스는 테스트를 통과한 고정 버전으로(공급망·재현성)
    run([py, "-m", "pip", "install", "--quiet", "-r", lock if os.path.exists(lock) else os.path.join(REPO, "backend", "requirements.txt"),
         f"pyinstaller=={PYINSTALLER_VERSION}"])

    for d in (out, work):
        shutil.rmtree(d, ignore_errors=True)
    sep = ";" if IS_WIN else ":"
    run([py, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "rework-backend",
         "--distpath", work, "--workpath", os.path.join(work, "work"), "--specpath", work,
         "--paths", os.path.join(REPO, "backend"),
         "--add-data", f"{os.path.join(REPO, 'frontend')}{sep}frontend",
         "--collect-submodules", "uvicorn", "--collect-submodules", "app",
         "--hidden-import", "multipart", "--hidden-import", "python_multipart",
         "--exclude-module", "tkinter", "--exclude-module", "pytest",
         os.path.join(REPO, "run.py")])
    shutil.copytree(os.path.join(work, "rework-backend"), out)
    shutil.rmtree(work, ignore_errors=True)
    exe = os.path.join(out, "rework-backend" + (".exe" if IS_WIN else ""))
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
    print(f"백엔드 번들: {out} ({size / 1e6:.0f} MB)")
    if not args.no_smoke and not (args.target == "wsl" and IS_WIN):
        smoke(exe)


if __name__ == "__main__":
    main()
