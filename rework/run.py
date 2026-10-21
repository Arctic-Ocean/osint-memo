#!/usr/bin/env python3
"""rework 실행기. 기본적으로 127.0.0.1(로컬 전용)에 바인딩한다."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))


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

    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=os.path.join(os.path.dirname(__file__), "backend"))


if __name__ == "__main__":
    main()
