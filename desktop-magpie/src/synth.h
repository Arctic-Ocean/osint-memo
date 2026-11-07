#pragma once
// 소리를 파일 없이 코드로 합성한다 (44.1kHz 모노 float, -1..1).
// 종류마다 여러 가지 버전(1, 2, 3...)이 있고, 재생할 때 무작위로 고른다.
#include <vector>

#include "sound_ids.h"

constexpr int kSynthRate = 44100;

int soundVariants(SoundId id);                         // 그 종류의 버전 수
std::vector<float> synthSound(SoundId id, int variant); // variant: 0 .. soundVariants-1
