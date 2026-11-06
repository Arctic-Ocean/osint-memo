#!/usr/bin/env bash
# 리눅스에서 OS 독립 부분(두뇌/그림/소리)을 빌드하고 검사한다.
#   ./tests/run_tests.sh [출력폴더]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$here/build/test-out}"
mkdir -p "$here/build" "$out"
g++ -O2 -std=c++17 -Wall -Wextra -Werror \
  "$here/tests/sim_test.cpp" "$here/src/pet.cpp" "$here/src/magpie_draw.cpp" \
  "$here/src/config.cpp" "$here/src/synth.cpp" -o "$here/build/sim_test"
# dist/config.ini 가 코드의 기본값과 같은지
"$here/build/sim_test" defcfg | cmp - "$here/dist/config.ini" && echo "dist/config.ini matches defaults"
"$here/build/sim_test" wav "$out"
"$here/build/sim_test" sim "$out"
