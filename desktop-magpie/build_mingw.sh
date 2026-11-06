#!/usr/bin/env bash
# 리눅스(또는 MSYS2)에서 MinGW-w64 로 Windows용 exe 를 만들고 배포 폴더/zip 까지 만든다.
#   ./build_mingw.sh
# 결과: build/DesktopMagpie/ (exe + config.ini + Close_Magpie.bat + Assets 폴더), build/DesktopMagpie.zip
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"

CXX="${CXX:-}"
if [[ -z "$CXX" ]]; then
  for c in x86_64-w64-mingw32-g++-posix x86_64-w64-mingw32-g++ g++; do
    if command -v "$c" >/dev/null; then CXX="$c"; break; fi
  done
fi
WINDRES="${WINDRES:-$(command -v x86_64-w64-mingw32-windres || command -v windres)}"

obj="build/win"
mkdir -p "$obj"
"$WINDRES" -I res res/app.rc -O coff -o "$obj/app.res"
"$CXX" -O2 -std=c++17 -municode -mwindows \
  -DUNICODE -D_UNICODE -DNOMINMAX -DWIN32_LEAN_AND_MEAN \
  -Wall -Wextra \
  src/config.cpp src/magpie_draw.cpp src/pet.cpp src/synth.cpp \
  src/win/main.cpp src/win/win_platform.cpp src/win/audio_win.cpp \
  "$obj/app.res" -o "$obj/DesktopMagpie.exe" \
  -static -s -lwinmm -lole32 -lwindowscodecs -luuid -lshell32 -lgdi32 -luser32

dist="build/DesktopMagpie"
rm -rf "$dist" build/DesktopMagpie.zip
mkdir -p "$dist/Assets/Images" "$dist/Assets/Notes" "$dist/Assets/Sound/Call" "$dist/Assets/Sound/Bite" "$dist/Assets/Sound/Step"
cp "$obj/DesktopMagpie.exe" dist/config.ini dist/Close_Magpie.bat dist/읽어주세요.txt "$dist/"
(cd build && zip -qr DesktopMagpie.zip DesktopMagpie)
ls -la "$obj/DesktopMagpie.exe" build/DesktopMagpie.zip
