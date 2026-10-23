#!/usr/bin/env python3
"""Monaco 에디터(AMD 최소 빌드)를 frontend/vendor/monaco 에 내려받아 둔다 — 오프라인에서도 쓰도록 저장소에 포함하는 파일을 만드는 스크립트.

  python3 scripts/vendor_monaco.py

고정한 버전의 npm 타르볼을 받아 **무결성(sha512)을 확인**한 뒤 필요한 것만 추려 넣는다:
  - vs/ 아래 로더·편집기·기본 언어·언어 서비스, 한국어 번역(nls.messages.ko.js)만(다른 언어 번역 제외)
  - worker.js : 웹 워커 진입점(같은 출처 파일이라 CSP 가 `blob:` 을 허용하지 않아도 된다)
  - LICENSE·ThirdPartyNotices.txt
버전을 올릴 때는 VERSION·INTEGRITY 를 함께 바꾸고 UI(탐색기·편집기)를 다시 시험한다.
(0.53 이상은 번들 구조가 바뀌어 이 로더 방식으로 뜨지 않는다 — 올리기 전에 반드시 확인.)
"""
import base64
import hashlib
import io
import os
import shutil
import sys
import tarfile
import urllib.request

VERSION = "0.52.2"
INTEGRITY = "sha512-GEQWEZmfkOGLdd3XK8ryrfWz3AIP8YymVXiPHEdewrUq7mh0qrKrfHLNCXcbB6sTnMLnOZ3ztSiKcciFUkIJwQ=="
URL = f"https://registry.npmjs.org/monaco-editor/-/monaco-editor-{VERSION}.tgz"

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(HERE), "frontend", "vendor", "monaco")

WORKER_JS = """// Monaco 웹 워커 진입점(같은 출처 파일). 편집기 코어·언어 서비스가 이 워커 안에서 돌아 화면이 멈추지 않는다.
self.MonacoEnvironment = { baseUrl: self.location.origin + '/vendor/monaco/' };
importScripts(self.location.origin + '/vendor/monaco/vs/base/worker/workerMain.js');
"""


def main():
    print(f"다운로드: {URL}")
    with urllib.request.urlopen(URL, timeout=120) as r:
        data = r.read()
    algo, _, b64 = INTEGRITY.partition("-")
    got = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if got != b64:
        sys.exit(f"무결성 검사 실패: 기대 {b64[:16]}… 실제 {got[:16]}… — 받은 파일을 쓰지 않습니다.")
    print(f"무결성 확인됨 ({algo})")
    shutil.rmtree(DEST, ignore_errors=True)
    os.makedirs(DEST)
    n = size = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            name = m.name
            if name in ("package/LICENSE", "package/ThirdPartyNotices.txt"):
                rel = os.path.basename(name)
            elif name.startswith("package/min/vs/"):
                rel = name[len("package/min/"):]
                base = os.path.basename(rel)
                if base.startswith("nls.messages.") and base != "nls.messages.ko.js":
                    continue                                   # 한국어 번역만 둔다
            else:
                continue
            if os.path.normpath(rel).startswith(".."):         # 타르볼 경로 탈출 방지
                sys.exit(f"의심스러운 경로: {name}")
            out = os.path.join(DEST, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with tf.extractfile(m) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            n += 1
            size += m.size
    with open(os.path.join(DEST, "worker.js"), "w", encoding="utf-8") as fh:
        fh.write(WORKER_JS)
    with open(os.path.join(DEST, "VERSION"), "w", encoding="utf-8") as fh:
        fh.write(VERSION + "\n")
    print(f"완료: {n}개 파일, {size / 1e6:.1f} MB → {DEST}")


if __name__ == "__main__":
    main()
