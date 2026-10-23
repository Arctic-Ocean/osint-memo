#!/usr/bin/env python3
"""rework 실행기. 기본적으로 127.0.0.1(로컬 전용)에 바인딩한다.

데스크톱 앱(Electron 셸)은 이 실행기를 `--desktop --port 0` 으로 부른다:
  - `--port 0` : 운영체제가 빈 포트를 고르고, 이미 바인딩한 소켓으로 서버를 띄운다(포트를 먼저 알아내 닫았다 다시 여는 경쟁이 없다).
    고른 포트는 표준 출력의 `REWORK_PORT=<번호>` 한 줄로 알린다.
  - `--desktop` : 토큰(셸이 만든 무작위 값)이 반드시 있어야 하고(없으면 시작하지 않는다), 토큰을 출력하지 않으며,
    표준 입력이 닫히면(부모인 셸이 죽으면) 스스로 종료해 고아 프로세스(와 그 안의 샌드박스)가 남지 않게 한다.
  - `--token-stdin` : 토큰을 환경 변수가 아니라 표준 입력 첫 줄로 받는다(환경 변수는 같은 사용자의 다른 프로세스가 읽을 수 있다).
"""
import argparse
import os
import socket
import sys
import threading

if getattr(sys, "frozen", False):                       # PyInstaller 설치본: 모듈은 이미 묶여 있다
    pass
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))


def _other_login_accounts():
    """이 컴퓨터에서 로그인할 수 있는 다른 사용자 계정(다른 사용자가 127.0.0.1 로 이 서버에 접속할 수 있다는 경고용)."""
    try:
        import pwd
    except ImportError:                                    # Windows
        return []
    me = os.geteuid()
    out = []
    for e in pwd.getpwall():
        if e.pw_uid == me or e.pw_uid == 65534:
            continue
        if (e.pw_uid >= 1000 or e.pw_uid == 0) and not e.pw_shell.endswith(("nologin", "false")):
            out.append(e.pw_name)
    return out


def _restore_host_library_path():
    """PyInstaller 는 번들 라이브러리를 찾게 LD_LIBRARY_PATH 를 바꿔 놓는다. 그대로 두면 이 프로세스가 띄우는 시스템 프로그램(gdb·bwrap)이
    번들된 라이브러리를 대신 읽어 깨질 수 있으므로, 원래 값으로 되돌린다(glibc 는 시작 때 한 번만 읽으므로 이 프로세스에는 영향이 없다)."""
    if not getattr(sys, "frozen", False):
        return
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = os.environ.get(var + "_ORIG")
        if orig is not None:
            os.environ[var] = orig
        else:
            os.environ.pop(var, None)


def _bind(host: str, port: int):
    """port 가 0 이면 호스트에 미리 바인딩한 소켓을 돌려준다(그 외에는 None — uvicorn 이 직접 바인딩)."""
    if port != 0:
        return None, port
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(fam, socket.SOCK_STREAM)
    sock.bind((host, 0))                                   # SO_REUSEADDR 는 쓰지 않는다(Windows 에서는 포트 가로채기를 허용한다)
    sock.set_inheritable(True)
    return sock, sock.getsockname()[1]


def _watch_stdin(server):
    """표준 입력이 EOF 가 될 때까지 기다렸다가 서버를 정상 종료시킨다(lifespan 이 디버그 세션·샌드박스를 정리한다).
    정상 종료가 지연되면 일정 시간 뒤 강제로 끝낸다(샌드박스는 --die-with-parent 로 따라 죽는다)."""
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except Exception:                                      # noqa: BLE001
        pass
    server.should_exit = True
    t = threading.Timer(10.0, lambda: os._exit(0))
    t.daemon = True
    t.start()


def main():
    ap = argparse.ArgumentParser(description="rework — 리버스 엔지니어링 워크벤치")
    ap.add_argument("--host", default="127.0.0.1", help="바인딩 호스트 (기본 로컬 전용)")
    ap.add_argument("--port", type=int, default=8777, help="포트(0 이면 운영체제가 고르고 REWORK_PORT=<번호> 를 출력)")
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--require-token", action="store_true",
                    help="모든 /api 요청에 무작위 토큰을 요구한다(같은 컴퓨터의 다른 사용자·프로세스 차단, network=host 사용에 필요)")
    ap.add_argument("--desktop", action="store_true",
                    help="데스크톱 셸이 띄우는 모드(토큰 필수·토큰 비출력·표준 입력이 닫히면 종료)")
    ap.add_argument("--token-stdin", action="store_true",
                    help="토큰을 표준 입력 첫 줄로 받는다(환경 변수는 같은 사용자의 다른 프로세스가 /proc/<pid>/environ 으로 읽을 수 있다)")
    args = ap.parse_args()

    _restore_host_library_path()
    if args.token_stdin:
        line = sys.stdin.buffer.readline().decode("utf-8", "replace").strip()
        if len(line) < 16 or not line.isascii():
            sys.exit("--token-stdin: 표준 입력 첫 줄에 16자 이상의 ASCII 토큰이 필요합니다.")
        os.environ["REWORK_TOKEN"] = line
    if args.desktop:
        if not os.environ.get("REWORK_TOKEN"):
            sys.exit("--desktop 은 토큰(REWORK_TOKEN 환경 변수 또는 --token-stdin)이 필요합니다.")
        os.environ["REWORK_DESKTOP"] = "1"
    elif args.require_token and not os.environ.get("REWORK_TOKEN"):
        import secrets
        os.environ["REWORK_TOKEN"] = secrets.token_urlsafe(24)
    if os.environ.get("REWORK_TOKEN") and not args.desktop:
        print(f"\n토큰 인증이 켜져 있습니다. 이 주소로 접속하세요:\n  http://{args.host}:{args.port}/?token={os.environ['REWORK_TOKEN']}\n", flush=True)
    elif not os.environ.get("REWORK_TOKEN") and args.host in ("127.0.0.1", "localhost", "::1"):
        others = _other_login_accounts()
        if others:
            print("\n주의: 이 컴퓨터에는 다른 로그인 계정이 있습니다(" + ", ".join(others[:5]) + (" 등" if len(others) > 5 else "") +
                  "). 토큰 인증이 꺼져 있어 그 계정들도 127.0.0.1 로 이 서버의 API(스크립트 실행 포함)를 부를 수 있습니다.\n"
                  "공유 컴퓨터라면 `--require-token` 으로 실행하세요.\n", flush=True)

    import uvicorn
    if args.reload:                                        # 개발용: 문자열 임포트가 필요하다
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=True,
                    app_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
        return

    from app.main import app
    sock, port = _bind(args.host, args.port)
    config = uvicorn.Config(app, host=args.host, port=port, timeout_graceful_shutdown=5,
                            access_log=not args.desktop)           # 데스크톱: 요청마다 한 줄씩 쌓이는 접근 로그는 끈다(로그 파일이 부풀지 않게)
    server = uvicorn.Server(config)
    if args.desktop:
        threading.Thread(target=_watch_stdin, args=(server,), daemon=True, name="rework-stdin-watch").start()
    if sock is not None:
        print(f"REWORK_PORT={port}", flush=True)
    server.run(sockets=[sock] if sock is not None else None)


if __name__ == "__main__":
    main()
