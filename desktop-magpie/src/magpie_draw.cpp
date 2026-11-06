#include "magpie_draw.h"

#include <algorithm>
#include <cmath>

namespace {

RGBAf toRGBAf(Color c) { return rgba(c.r, c.g, c.b, 1.f); }

// 까치 기준 좌표계: a = 앞쪽, b = 오른쪽, z = 높이 (모두 디자인 단위)
struct Basis {
  Vec2 pos, f, r;
  float u;
  Vec2 ground(float a, float b) const { return pos + f * (a * u) + r * (b * u); }
  Vec2 P(float a, float b, float z) const { return ground(a, b) + Vec2{0.f, -z * u}; }
};

Basis basisOf(const MagpiePose& p) {
  Vec2 f = fromAngle(p.heading);
  return {p.pos, f, Vec2{-f.y, f.x}, p.unit};
}

constexpr float kBodyZ = 17.f;

struct HeadGeo {
  Vec2 neckBase, center, dir, perp, beakBase, tip;
};

HeadGeo headGeo(const MagpiePose& p) {
  const Basis B = basisOf(p);
  const float u = p.unit;
  const float bz = kBodyZ + p.bob;
  HeadGeo h;
  h.dir = rotate(B.f, p.headTurn);
  h.perp = {-h.dir.y, h.dir.x};
  h.neckBase = B.P(6.f, 0.f, bz + 2.f);
  Vec2 cg = B.ground(8.5f, 0.f) + h.dir * ((4.5f + p.headThrust) * u);
  h.center = cg + Vec2{0.f, -(bz + 5.5f) * u};
  h.beakBase = h.center + h.dir * (4.8f * u);
  h.tip = h.center + h.dir * (12.5f * u) + Vec2{0.f, 0.5f * u};
  return h;
}

// 발가락 4개 (앞 3 + 뒤 1). 겹치는 부분이 진해지지 않게 합집합으로 한 번에 칠한다.
void drawToes(Canvas& c, Vec2 foot, Vec2 dir, float u, RGBAf col, float r) {
  Capsule t0(foot, foot + dir * (4.6f * u), r * u);
  Capsule t1(foot, foot + rotate(dir, 0.62f) * (3.8f * u), r * u);
  Capsule t2(foot, foot + rotate(dir, -0.62f) * (3.8f * u), r * u);
  Capsule t3(foot, foot - dir * (2.0f * u), r * u);
  float e = 5.5f * u;
  c.fillFn(foot.x - e, foot.y - e, foot.x + e, foot.y + e, col, [&](Vec2 p) {
    return std::min(std::min(t0.eval(p), t1.eval(p)), std::min(t2.eval(p), t3.eval(p)));
  });
}

// shape 을 clip 도형 안쪽(inset 만큼 더 안쪽)으로만 칠한다
template <class S, class C>
void fillClipped(Canvas& c, const S& shape, const C& clip, float inset, RGBAf col) {
  Bounds b = shape.bounds();
  c.fillFn(b.x0, b.y0, b.x1, b.y1, col,
           [&](Vec2 p) { return std::max(shape.eval(p), clip.eval(p) + inset); });
}

}  // namespace

MagpiePalette makePalette(const Config& cfg) {
  Config def;
  const Config& src = cfg.useCustomColors ? cfg : def;
  MagpiePalette p;
  p.black = toRGBAf(src.black);
  p.white = toRGBAf(src.white);
  p.blue = toRGBAf(src.blue);
  p.outline = toRGBAf(src.outline);
  p.leg = rgba(0x48, 0x48, 0x50);
  p.beakUpper = rgba(0x34, 0x34, 0x3b);
  p.beakLower = rgba(0x24, 0x24, 0x29);
  p.mouth = rgba(0x8a, 0x3a, 0x44);
  p.eye = rgba(0x08, 0x08, 0x0b);
  p.eyeRing = mixColor(p.black, p.white, 0.25f);
  p.glint = rgba(255, 255, 255, 0.9f);
  p.shadow = rgba(0, 0, 0, 0.2f);
  p.mud = rgba(0x5b, 0x41, 0x2b, 0.85f);
  return p;
}

Vec2 magpieBeakTip(const MagpiePose& p) { return headGeo(p).tip; }

void drawMagpie(Canvas& c, const MagpiePose& p, const MagpiePalette& pal) {
  const Basis B = basisOf(p);
  const float u = p.unit;
  const float bz = kBodyZ + p.bob;
  const float ow = std::max(1.f, 1.05f * u);  // 외곽선 두께 (픽셀)
  const Vec2 up{0.f, -u};

  // 1) 그림자
  c.fill(Ellipse(B.P(-5.f, 0.f, 0.f), 23.f * u, 12.5f * u, p.heading), pal.shadow);

  // 2) 다리 + 발가락
  for (int i = 0; i < 2; ++i) {
    float side = i == 0 ? -1.f : 1.f;
    Vec2 hip = B.P(0.5f, side * 4.2f, bz - 4.f);
    Vec2 foot = p.feet[i] + Vec2{0.f, -p.footLift[i] * u};
    drawToes(c, foot, B.f, u, pal.leg, 0.85f);
    c.fill(Capsule(hip, foot, 1.25f * u), pal.leg);
  }

  // 3) 몸 도형들 계산
  Vec2 tailBaseG = B.ground(-8.f, 0.f);
  Vec2 tailDir = rotate(-B.f, p.tailSway);
  Vec2 tailBase = tailBaseG + Vec2{0.f, -(bz + 0.5f) * u};
  Vec2 tailTip = tailBaseG + tailDir * (28.f * u) + Vec2{0.f, -(bz - 1.f + p.tailLift) * u};
  RoundCone tail(tailBase, tailTip, 3.3f * u, 4.7f * u);
  Ellipse body(B.P(0.f, 0.f, bz), 15.f * u, 10.f * u, p.heading);
  HeadGeo H = headGeo(p);
  Capsule neck(H.neckBase, H.center, 5.2f * u);
  Circle head(H.center, 6.6f * u);

  const float open = clampf(p.beakOpen, 0.f, 1.f);
  Vec2 tipUp = H.tip + up * (open * 2.6f);
  Vec2 tipLow = H.tip - up * (open * 2.2f) - H.dir * (open * 1.2f * u);
  Triangle beakUp(H.beakBase + H.perp * (2.5f * u) + up * 0.3f, H.beakBase - H.perp * (2.5f * u) + up * 0.3f,
                  tipUp);
  Triangle beakLow(H.beakBase + H.perp * (2.1f * u) - up * 0.4f, H.beakBase - H.perp * (2.1f * u) - up * 0.4f,
                   tipLow);

  // 4) 실루엣 외곽선 (전체 윤곽만 한 번에)
  c.fill(tail, pal.outline, ow);
  c.fill(body, pal.outline, ow);
  c.fill(neck, pal.outline, ow);
  c.fill(head, pal.outline, ow);
  c.fill(beakLow, pal.outline, ow);
  c.fill(beakUp, pal.outline, ow);

  // 5) 꼬리 (검정 + 푸른 광택)
  c.fill(tail, pal.black);
  Vec2 td = normalize(tailTip - tailBase);
  c.fill(RoundCone(tailBase + td * (6.f * u), tailTip - td * (2.5f * u), 1.2f * u, 2.3f * u),
         withAlpha(pal.blue, 0.6f));

  // 6) 몸통 + 날개 광택 + 흰 어깨깃
  c.fill(body, pal.black);
  for (int i = 0; i < 2; ++i) {
    float s = i == 0 ? -1.f : 1.f;
    Ellipse wing(B.P(-8.f, s * 4.0f, bz + 0.4f), 8.5f * u, 2.8f * u, p.heading + s * 0.22f);
    fillClipped(c, wing, body, 1.2f * u, withAlpha(pal.blue, 0.85f));
  }
  for (int i = 0; i < 2; ++i) {
    float s = i == 0 ? -1.f : 1.f;
    Ellipse patch(B.P(0.5f, s * 6.6f, bz + 0.3f), 9.5f * u, 2.7f * u, p.heading + s * 0.12f);
    fillClipped(c, patch, body, 0.8f * u, pal.white);
  }

  // 7) 목, 머리, 눈
  c.fill(neck, pal.black);
  c.fill(head, pal.black);
  for (int i = 0; i < 2; ++i) {
    float s = i == 0 ? -1.f : 1.f;
    Vec2 e = H.center + rotate(H.dir, s * 1.0f) * (4.2f * u) + up * 0.9f;
    c.fill(Circle(e, 1.9f * u), pal.eyeRing);
    c.fill(Circle(e, 1.4f * u), pal.eye);
    c.fill(Circle(e + Vec2{-0.45f * u, -0.45f * u}, 0.55f * u), pal.glint);
  }

  // 8) 부리 (벌리면 입 안이 보임)
  c.fill(beakLow, pal.beakLower);
  if (open > 0.05f) {
    Triangle mouth(H.beakBase + H.perp * (1.6f * u), H.beakBase - H.perp * (1.6f * u),
                   (tipUp + tipLow) * 0.5f - H.dir * (2.f * u));
    c.fill(mouth, withAlpha(pal.mouth, std::min(1.f, open * 3.f)));
  }
  c.fill(beakUp, pal.beakUpper);
}

void drawFootprint(Canvas& c, Vec2 pos, float angle, float unit, RGBAf col) {
  drawToes(c, pos, fromAngle(angle), unit, col, 0.8f);
}

MagpiePose makeStandingPose(Vec2 visualCenter, float unit, float heading) {
  MagpiePose p;
  Vec2 f = fromAngle(heading), r{-f.y, f.x};
  // 까치 그림의 시각적 중심은 바닥 위치보다 뒤쪽(꼬리 쪽)·위쪽에 있다
  p.pos = visualCenter + f * (9.f * unit) + Vec2{0.f, 10.f * unit};
  p.heading = heading;
  p.unit = unit;
  p.feet[0] = p.pos + r * (-4.2f * unit) + f * (0.5f * unit);
  p.feet[1] = p.pos + r * (4.2f * unit) + f * (0.5f * unit);
  return p;
}
