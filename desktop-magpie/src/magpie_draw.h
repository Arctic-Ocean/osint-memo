#pragma once
// 까치 그리기. 스프라이트 없이 매 프레임 도형으로 그린다 (색 변경 가능).
// 좌표계: 위에서 내려다본 바닥 평면 + 높이(z)는 화면 위쪽으로 밀어서 표현.
#include "config.h"
#include "raster.h"
#include "vec2.h"

struct MagpiePalette {
  RGBAf black, white, blue, outline;
  RGBAf leg, beakUpper, beakLower, mouth, eye, eyeRing, glint, shadow, mud;
};
MagpiePalette makePalette(const Config& cfg);

// 한 프레임의 자세. 전부 float 이라 memcmp 로 "변화 없음"을 판단할 수 있다.
struct MagpiePose {
  Vec2 pos{0.f, 0.f};  // 바닥 위치 (화면 픽셀)
  float heading = 0.f; // 바라보는 방향 (라디안)
  float unit = 1.f;    // 디자인 단위 1 = 몇 픽셀
  Vec2 feet[2];        // 발의 바닥 위치
  float footLift[2] = {0.f, 0.f};  // 발 들림 (단위)
  float bob = 0.f;        // 몸 들썩임 (단위)
  float tailSway = 0.f;   // 꼬리 좌우 (라디안)
  float tailLift = 0.f;   // 꼬리 까딱 (단위)
  float headTurn = 0.f;   // 고개 돌림 (라디안)
  float headThrust = 0.f; // 고개 내밀기 (단위)
  float beakOpen = 0.f;   // 0..1
};

void drawMagpie(Canvas& c, const MagpiePose& p, const MagpiePalette& pal);
Vec2 magpieBeakTip(const MagpiePose& p);  // 부리 끝 화면 좌표 (커서/창을 무는 지점)
void drawFootprint(Canvas& c, Vec2 pos, float angle, float unit, RGBAf col);
MagpiePose makeStandingPose(Vec2 visualCenter, float unit, float heading);
