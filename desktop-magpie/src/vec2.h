#pragma once
// 2D 벡터 / 사각형 / 각도 유틸리티
#include <cmath>

constexpr float kPi = 3.14159265358979f;

struct Vec2 {
  float x = 0.f, y = 0.f;
  Vec2() = default;
  constexpr Vec2(float x_, float y_) : x(x_), y(y_) {}
  Vec2 operator+(Vec2 o) const { return {x + o.x, y + o.y}; }
  Vec2 operator-(Vec2 o) const { return {x - o.x, y - o.y}; }
  Vec2 operator*(float s) const { return {x * s, y * s}; }
  Vec2 operator/(float s) const { return {x / s, y / s}; }
  Vec2 operator-() const { return {-x, -y}; }
  Vec2& operator+=(Vec2 o) { x += o.x; y += o.y; return *this; }
  Vec2& operator-=(Vec2 o) { x -= o.x; y -= o.y; return *this; }
  Vec2& operator*=(float s) { x *= s; y *= s; return *this; }
};

inline float dot(Vec2 a, Vec2 b) { return a.x * b.x + a.y * b.y; }
inline float cross(Vec2 a, Vec2 b) { return a.x * b.y - a.y * b.x; }
inline float length(Vec2 a) { return std::sqrt(dot(a, a)); }
inline Vec2 normalize(Vec2 a) {
  float l = length(a);
  return l > 1e-6f ? a / l : Vec2{0.f, 0.f};
}
inline Vec2 lerp(Vec2 a, Vec2 b, float t) { return a + (b - a) * t; }
inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline Vec2 fromAngle(float a) { return {std::cos(a), std::sin(a)}; }
inline Vec2 rotate(Vec2 v, float a) {
  float c = std::cos(a), s = std::sin(a);
  return {v.x * c - v.y * s, v.x * s + v.y * c};
}
inline float wrapAngle(float a) {
  while (a > kPi) a -= 2.f * kPi;
  while (a < -kPi) a += 2.f * kPi;
  return a;
}
// cur 에서 target 쪽으로 최대 maxStep 만큼만 회전
inline float approachAngle(float cur, float target, float maxStep) {
  float d = wrapAngle(target - cur);
  if (d > maxStep) d = maxStep;
  if (d < -maxStep) d = -maxStep;
  return wrapAngle(cur + d);
}

struct RectF {
  float x0 = 0.f, y0 = 0.f, x1 = 0.f, y1 = 0.f;
  float w() const { return x1 - x0; }
  float h() const { return y1 - y0; }
  Vec2 center() const { return {(x0 + x1) * 0.5f, (y0 + y1) * 0.5f}; }
  bool contains(Vec2 p, float inset = 0.f) const {
    return p.x >= x0 + inset && p.x <= x1 - inset && p.y >= y0 + inset && p.y <= y1 - inset;
  }
};
