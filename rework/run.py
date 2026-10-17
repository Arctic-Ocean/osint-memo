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
    args = ap.parse_args()

    import uvicorn
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=os.path.join(os.path.dirname(__file__), "backend"))


if __name__ == "__main__":
    main()
