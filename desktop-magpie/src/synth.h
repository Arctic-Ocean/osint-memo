#pragma once
// 소리를 파일 없이 코드로 합성한다 (44.1kHz 모노 float, -1..1).
#include <cstdint>
#include <vector>

constexpr int kSynthRate = 44100;

std::vector<float> synthCall(uint32_t seed);  // "까악 까악" 울음
std::vector<float> synthBite(uint32_t seed);  // "딱" 무는 소리
std::vector<float> synthStep(uint32_t seed);  // 톡 발소리
