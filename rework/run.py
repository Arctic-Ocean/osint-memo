#!/usr/bin/env python3
"""rework 실행기. 기본적으로 127.0.0.1(로컬 전용)에 바인딩한다."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


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


def main():
    ap = argparse.ArgumentParser(description="rework — 리버스 엔지니어링 워크벤치")
    ap.add_argument("--host", default="127.0.0.1", help="바인딩 호스트 (기본 로컬 전용)")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--require-token", action="store_true",
                    help="모든 /api 요청에 무작위 토큰을 요구한다(같은 컴퓨터의 다른 사용자·프로세스 차단, network=host 사용에 필요)")
    args = ap.parse_args()
    if args.require_token and not os.environ.get("REWORK_TOKEN"):
        import secrets
        os.environ["REWORK_TOKEN"] = secrets.token_urlsafe(24)
    if os.environ.get("REWORK_TOKEN"):
        print(f"\n토큰 인증이 켜져 있습니다. 이 주소로 접속하세요:\n  http://{args.host}:{args.port}/?token={os.environ['REWORK_TOKEN']}\n", flush=True)

    elif args.host in ("127.0.0.1", "localhost", "::1"):
        others = _other_login_accounts()
        if others:
            print("\n주의: 이 컴퓨터에는 다른 로그인 계정이 있습니다(" + ", ".join(others[:5]) + (" 등" if len(others) > 5 else "") +
                  "). 토큰 인증이 꺼져 있어 그 계정들도 127.0.0.1 로 이 서버의 API(스크립트 실행 포함)를 부를 수 있습니다.\n"
                  "공유 컴퓨터라면 `--require-token` 으로 실행하세요.\n", flush=True)

    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=os.path.join(os.path.dirname(__file__), "backend"))


if __name__ == "__main__":
    main()
