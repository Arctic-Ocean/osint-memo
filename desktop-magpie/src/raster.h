#pragma once
// 아주 작은 SDF(부호 거리 함수) 기반 소프트웨어 래스터라이저.
// 도형마다 "이 점이 도형 경계에서 얼마나 떨어져 있나"를 계산해서
// 경계 1픽셀 구간을 부드럽게 칠한다 → 안티에일리어싱이 공짜로 따라옴.
// 출력은 UpdateLayeredWindow 가 그대로 먹는 32bit BGRA (premultiplied alpha).
#include <algorithm>
#include <cmath>
#include <cstdint>

#include "vec2.h"

struct RGBAf {
  float r = 0.f, g = 0.f, b = 0.f, a = 1.f;
};
inline RGBAf rgba(int r, int g, int b, float a = 1.f) { return {r / 255.f, g / 255.f, b / 255.f, a}; }
inline RGBAf mixColor(RGBAf x, RGBAf y, float t) {
  return {x.r + (y.r - x.r) * t, x.g + (y.g - x.g) * t, x.b + (y.b - x.b) * t, x.a + (y.a - x.a) * t};
}
inline RGBAf withAlpha(RGBAf c, float a) {
  c.a = a;
  return c;
}

struct Bounds {
  float x0, y0, x1, y1;
};

// ---- 도형들: eval(p) < 0 이면 안쪽 ----
struct Circle {
  Vec2 c;
  float r;
  Circle(Vec2 c_, float r_) : c(c_), r(r_) {}
  float eval(Vec2 p) const { return length(p - c) - r; }
  Bounds bounds() const { return {c.x - r, c.y - r, c.x + r, c.y + r}; }
};

struct Capsule {  // 끝이 둥근 굵은 선
  Vec2 a, b;
  float r;
  Capsule(Vec2 a_, Vec2 b_, float r_) : a(a_), b(b_), r(r_) {}
  float eval(Vec2 p) const {
    Vec2 pa = p - a, ba = b - a;
    float bb = dot(ba, ba);
    float h = bb > 1e-8f ? clampf(dot(pa, ba) / bb, 0.f, 1.f) : 0.f;
    return length(pa - ba * h) - r;
  }
  Bounds bounds() const {
    return {std::min(a.x, b.x) - r, std::min(a.y, b.y) - r, std::max(a.x, b.x) + r, std::max(a.y, b.y) + r};
  }
};

struct Ellipse {  // 회전된 타원 (ra: angle 방향 반지름, rb: 수직 방향 반지름)
  Vec2 c;
  float ra, rb, ca, sa;
  Ellipse(Vec2 c_, float ra_, float rb_, float angle)
      : c(c_), ra(ra_), rb(rb_), ca(std::cos(angle)), sa(std::sin(angle)) {}
  float eval(Vec2 p) const {
    Vec2 d = p - c;
    float x = d.x * ca + d.y * sa;
    float y = -d.x * sa + d.y * ca;
    float qx = x / ra, qy = y / rb;
    float k0 = std::sqrt(qx * qx + qy * qy);
    float px = x / (ra * ra), py = y / (rb * rb);
    float k1 = std::sqrt(px * px + py * py);
    if (k1 < 1e-8f) return -std::min(ra, rb);
    return k0 * (k0 - 1.f) / k1;  // 경계 근처에서 정확한 근사
  }
  Bounds bounds() const {
    float ex = std::sqrt(ra * ra * ca * ca + rb * rb * sa * sa);
    float ey = std::sqrt(ra * ra * sa * sa + rb * rb * ca * ca);
    return {c.x - ex, c.y - ey, c.x + ex, c.y + ey};
  }
};

struct RoundCone {  // 양 끝 반지름이 다른 캡슐 (꼬리용)
  Vec2 a, b;
  float ra, rb;
  RoundCone(Vec2 a_, Vec2 b_, float ra_, float rb_) : a(a_), b(b_), ra(ra_), rb(rb_) {}
  float eval(Vec2 p) const {
    Vec2 pb = b - a, p0 = p - a;
    float h = dot(pb, pb);
    float dr = ra - rb;
    if (h <= dr * dr + 1e-6f) return std::min(length(p0) - ra, length(p - b) - rb);
    Vec2 q{dot(p0, Vec2{pb.y, -pb.x}) / h, dot(p0, pb) / h};
    q.x = std::fabs(q.x);
    Vec2 c{std::sqrt(h - dr * dr), dr};
    float k = c.x * q.y - c.y * q.x;
    float m = dot(c, q);
    float n = dot(q, q);
    if (k < 0.f) return std::sqrt(h * n) - ra;
    if (k > c.x) return std::sqrt(std::max(0.f, h * (n + 1.f - 2.f * q.y))) - rb;
    return m - ra;
  }
  Bounds bounds() const {
    return {std::min(a.x - ra, b.x - rb), std::min(a.y - ra, b.y - rb), std::max(a.x + ra, b.x + rb),
            std::max(a.y + ra, b.y + rb)};
  }
};

struct Triangle {
  Vec2 p0, p1, p2;
  Triangle(Vec2 a, Vec2 b, Vec2 c) : p0(a), p1(b), p2(c) {}
  float eval(Vec2 p) const {
    Vec2 e0 = p1 - p0, e1 = p2 - p1, e2 = p0 - p2;
    Vec2 v0 = p - p0, v1 = p - p1, v2 = p - p2;
    auto seg = [](Vec2 v, Vec2 e) {
      float ee = dot(e, e);
      float t = ee > 1e-10f ? clampf(dot(v, e) / ee, 0.f, 1.f) : 0.f;
      return v - e * t;
    };
    Vec2 pq0 = seg(v0, e0), pq1 = seg(v1, e1), pq2 = seg(v2, e2);
    float s = (e0.x * e2.y - e0.y * e2.x) >= 0.f ? 1.f : -1.f;
    float dx = std::min({dot(pq0, pq0), dot(pq1, pq1), dot(pq2, pq2)});
    float dy = std::min({s * (v0.x * e0.y - v0.y * e0.x), s * (v1.x * e1.y - v1.y * e1.x),
                         s * (v2.x * e2.y - v2.y * e2.x)});
    return -std::sqrt(dx) * (dy >= 0.f ? 1.f : -1.f);
  }
  Bounds bounds() const {
    return {std::min({p0.x, p1.x, p2.x}), std::min({p0.y, p1.y, p2.y}), std::max({p0.x, p1.x, p2.x}),
            std::max({p0.y, p1.y, p2.y})};
  }
};

struct Box {  // 둥근 모서리 사각형 (테스트 장면용)
  Vec2 c, half;
  float round;
  Box(Vec2 c_, Vec2 half_, float round_) : c(c_), half(half_), round(round_) {}
  float eval(Vec2 p) const {
    float qx = std::fabs(p.x - c.x) - half.x + round;
    float qy = std::fabs(p.y - c.y) - half.y + round;
    float ox = std::max(qx, 0.f), oy = std::max(qy, 0.f);
    return std::sqrt(ox * ox + oy * oy) + std::min(std::max(qx, qy), 0.f) - round;
  }
  Bounds bounds() const { return {c.x - half.x, c.y - half.y, c.x + half.x, c.y + half.y}; }
};

// ---- 캔버스 ----
class Canvas {
 public:
  uint32_t* px = nullptr;  // BGRA premultiplied, top-down
  int w = 0, h = 0;        // 그릴 영역 크기
  int stride = 0;          // 한 줄의 픽셀 수 (>= w)
  Vec2 origin{0.f, 0.f};   // 픽셀 (0,0) 의 화면 좌표. 모든 그리기 호출은 화면 좌표를 쓴다.

  void clear(uint32_t value = 0) {
    for (int y = 0; y < h; ++y) std::fill(px + (size_t)y * stride, px + (size_t)y * stride + w, value);
  }

  // grow > 0 이면 도형을 그만큼 부풀려서 칠한다 (외곽선용)
  template <class S>
  void fill(const S& s, RGBAf c, float grow = 0.f) {
    Bounds b = s.bounds();
    fillFn(b.x0 - grow, b.y0 - grow, b.x1 + grow, b.y1 + grow, c, [&](Vec2 p) { return s.eval(p) - grow; });
  }

  template <class F>
  void fillFn(float bx0, float by0, float bx1, float by1, RGBAf c, F&& sdfFn) {
    if (c.a <= 0.f) return;
    int x0 = std::max(0, (int)std::floor(bx0 - origin.x) - 1);
    int y0 = std::max(0, (int)std::floor(by0 - origin.y) - 1);
    int x1 = std::min(w, (int)std::ceil(bx1 - origin.x) + 1);
    int y1 = std::min(h, (int)std::ceil(by1 - origin.y) + 1);
    if (x0 >= x1 || y0 >= y1) return;
    const float sr = c.r * c.a * 255.f, sg = c.g * c.a * 255.f, sb = c.b * c.a * 255.f, sa = c.a * 255.f;
    for (int y = y0; y < y1; ++y) {
      uint32_t* row = px + (size_t)y * stride;
      const float wy = origin.y + (float)y + 0.5f;
      for (int x = x0; x < x1; ++x) {
        float cov = 0.5f - sdfFn(Vec2{origin.x + (float)x + 0.5f, wy});
        if (cov <= 0.f) continue;
        if (cov > 1.f) cov = 1.f;
        blend(row[x], sr * cov, sg * cov, sb * cov, sa * cov);
      }
    }
  }

 private:
  static void blend(uint32_t& dst, float r, float g, float b, float a) {
    const float inv = 1.f - a / 255.f;
    const uint32_t d = dst;
    auto ch = [inv](float src, uint32_t dv) {
      float v = src + (float)dv * inv + 0.5f;
      return (uint32_t)(v > 255.f ? 255.f : v);
    };
    dst = ch(b, d & 255u) | (ch(g, (d >> 8) & 255u) << 8) | (ch(r, (d >> 16) & 255u) << 16) |
          (ch(a, d >> 24) << 24);
  }
};
