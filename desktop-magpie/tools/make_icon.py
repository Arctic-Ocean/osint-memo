#!/usr/bin/env python3
"""까치 아이콘(.ico)을 만든다: sim_test 로 크기별로 따로 렌더링한 뒤 PNG 로 묶음.

사용법: python3 tools/make_icon.py build/sim_test res/magpie.ico
"""
import io
import os
import struct
import subprocess
import sys
import tempfile

from PIL import Image

SIZES = [16, 24, 32, 48, 64, 256]


def main() -> None:
    sim, out = sys.argv[1], sys.argv[2]
    pngs = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in SIZES:
            raw = os.path.join(tmp, f"icon{n}.rgba")
            subprocess.run([sim, "icon", raw, str(n)], check=True)
            with open(raw, "rb") as f:
                img = Image.frombytes("RGBA", (n, n), f.read())
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            pngs.append((n, buf.getvalue()))

    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for n, data in pngs:
        dim = 0 if n >= 256 else n
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(out, "wb") as f:
        f.write(header + entries + blobs)
    print(f"wrote {out} ({', '.join(str(n) for n, _ in pngs)})")


if __name__ == "__main__":
    main()
