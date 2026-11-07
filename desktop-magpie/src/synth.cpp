#include "synth.h"

#include <algorithm>
#include <cmath>
#include <random>

// 소리 합성기.
// "까악"은 사람 목소리 합성과 같은 방식으로 만든다:
//   성대 펄스(낮은 음정, 일부러 고르지 않게 → 까마귀류 특유의 거친 목소리)
//   → "아" 모음의 공명(포먼트) 필터 4개 → 앞에 "ㄲ" 파열음, 끝은 "ㄱ"처럼 뚝 끊음.
namespace {

constexpr float kTau = 6.28318530718f;
constexpr float kPiS = 3.14159265359f;
constexpr float kT = 1.f / kSynthRate;

// Klatt 2극 공명기 (포먼트 하나)
struct Reson {
  float a = 1, b = 0, c = 0, y1 = 0, y2 = 0;
  void set(float f, float bw) {
    c = -std::exp(-kTau * 0.5f * bw * kT * 2.f);
    b = 2.f * std::exp(-kTau * 0.5f * bw * kT) * std::cos(kTau * f * kT);
    a = 1.f - b - c;
  }
  float run(float x) {
    float y = a * x + b * y1 + c * y2;
    y2 = y1;
    y1 = y;
    return y;
  }
};

struct OnePole {  // 1차 저역/고역 통과
  float k = 0, z = 0;
  void lowpass(float f) { k = 1.f - std::exp(-kTau * f * kT); }
  float lp(float x) { return z += k * (x - z); }
  float hp(float x) { return x - lp(x); }
};

void normalize(std::vector<float>& s, float peak) {
  float m = 0.f;
  for (float v : s) m = std::max(m, std::fabs(v));
  if (m < 1e-6f) return;
  for (float& v : s) v *= peak / m;
}

float smooth01(float x) {
  x = std::max(0.f, std::min(1.f, x));
  return x * x * (3.f - 2.f * x);
}

// Rosenberg 성대 펄스 (열림 0..0.4, 닫힘 0.4..0.56, 이후 닫힘)
float glottal(float ph) {
  if (ph < 0.4f) return 0.5f * (1.f - std::cos(kPiS * ph / 0.4f));
  if (ph < 0.56f) return std::cos(kPiS * (ph - 0.4f) / 0.32f);
  return 0.f;
}

}  // namespace

namespace {

struct Caw {
  float dur = 0.3f;
  float f0a = 560, f0b = 610, f0c = 470;  // 시작 / 최고 / 끝 음정
  float rough = 0.5f;                      // 거칠기 (펄스 들쭉날쭉)
  float breath = 0.3f;                     // 바람 소리
  float F1 = 950, F2 = 1450, F3 = 2600, F4 = 3600;
  float burst = 0.6f;                      // "ㄲ" 세기
  float level = 1.f;
};

void addCaw(std::vector<float>& out, size_t start, const Caw& p, std::mt19937& rng) {
  std::normal_distribution<float> gauss(0.f, 1.f);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(p.dur * kSynthRate);
  if (out.size() < start + n + 64) out.resize(start + n + 64, 0.f);

  // ㄲ: 짧고 높은 파열음
  Reson bk;
  bk.set(2800.f, 1200.f);
  const size_t burstLen = (size_t)(0.012f * kSynthRate);
  for (size_t i = 0; i < burstLen; ++i) {
    float t = (float)i * kT;
    out[start + i] += bk.run(uni(rng)) * std::exp(-t / 0.003f) * p.burst * p.level * 3.f;
  }

  // 모음 "아악"
  Reson r1, r2, r3, r4;
  const size_t v0 = (size_t)(0.006f * kSynthRate);  // 파열 직후 목소리 시작
  float phase = 0.f, period = 1.f / p.f0a, pulseAmp = 1.f, prevG = 0.f;
  int pulseIdx = 0;
  for (size_t i = 0; i < n - v0; ++i) {
    const float t = (float)i * kT, x = t / (p.dur - 0.006f);
    // 음정: 살짝 올라갔다가 떨어짐
    float f0 = x < 0.18f ? p.f0a + (p.f0b - p.f0a) * smooth01(x / 0.18f)
                         : p.f0b + (p.f0c - p.f0b) * smooth01((x - 0.18f) / 0.82f);
    // 포먼트: ㄲ→아 로 넘어가며 F1 올라가고 F2 내려옴, 끝에서 ㄱ 으로 닫히며 F1 내려감
    float onset = smooth01(t / 0.035f), close = smooth01((x - 0.8f) / 0.2f);
    if (i % 32 == 0) {
      r1.set(450.f + (p.F1 - 450.f) * onset - 350.f * close, 160.f);
      r2.set(1900.f + (p.F2 - 1900.f) * onset + 250.f * close, 220.f);
      r3.set(p.F3, 320.f);
      r4.set(p.F4, 450.f);
    }
    phase += kT / period;
    if (phase >= 1.f) {  // 새 성대 펄스: 떨림(jitter), 세기 흔들림(shimmer), 번갈아 약해짐(이중음)
      phase -= 1.f;
      ++pulseIdx;
      period = (1.f / f0) * (1.f + 0.035f * p.rough * gauss(rng));
      pulseAmp = (1.f + 0.15f * p.rough * gauss(rng)) * ((pulseIdx & 1) ? 1.f - 0.55f * p.rough : 1.f);
    }
    float g = glottal(phase) * pulseAmp;
    float dg = (g - prevG) * kSynthRate / f0;  // 성대 펄스의 미분 = 배음이 풍부한 소리
    prevG = g;
    float src = dg + uni(rng) * p.breath * (0.25f + g);
    float y = r4.run(r3.run(r2.run(r1.run(src))));
    float env = smooth01(t / 0.012f) * (x < 0.72f ? 1.f : std::pow(1.f - smooth01((x - 0.72f) / 0.28f), 0.6f));
    out[start + v0 + i] += std::tanh(y * 0.08f) * env * p.level;  // 살짝 찌그러뜨려 더 거칠게
  }
}

std::vector<float> finishVoice(std::vector<float> s) {
  OnePole hp;
  hp.lowpass(220.f);  // 웅웅거림 제거
  for (float& v : s) v = hp.hp(v);
  normalize(s, 0.88f);
  return s;
}

std::vector<float> makeCall(int v) {
  std::mt19937 rng(1000u + (unsigned)v);
  std::vector<float> s;
  Caw c;
  switch (v) {
    case 0:  // 까악
      addCaw(s, 0, c, rng);
      break;
    case 1:  // 까악 까악
      c.dur = 0.26f;
      addCaw(s, 0, c, rng);
      c.f0a = 530, c.f0b = 575, c.f0c = 450;
      addCaw(s, (size_t)(0.38f * kSynthRate), c, rng);
      break;
    case 2:  // 까아아악 (길고 거칠게)
      c.dur = 0.46f;
      c.f0a = 540, c.f0b = 590, c.f0c = 420;
      c.rough = 0.7f;
      c.breath = 0.4f;
      addCaw(s, 0, c, rng);
      break;
    default:  // 깍! (짧고 높게)
      c.dur = 0.17f;
      c.f0a = 640, c.f0b = 690, c.f0c = 600;
      c.F1 = 1050;
      c.burst = 0.9f;
      addCaw(s, 0, c, rng);
      break;
  }
  return finishVoice(std::move(s));
}

// 까치 특유의 "깍깍깍깍" 수다 (화났을 때)
std::vector<float> makeChatter(int v) {
  std::mt19937 rng(2000u + (unsigned)v);
  std::uniform_real_distribution<float> jit(-1.f, 1.f);
  std::vector<float> s;
  const int notes = v == 0 ? 6 : 9;
  float t = 0.f;
  for (int k = 0; k < notes; ++k) {
    Caw c;
    c.dur = 0.05f + 0.006f * jit(rng);
    c.f0a = 720 + 30 * jit(rng);
    c.f0b = c.f0a + 40;
    c.f0c = c.f0a - 60;
    c.rough = 0.7f;
    c.breath = 0.8f;
    c.F1 = 1150, c.F2 = 1900, c.F3 = 3000, c.F4 = 4200;
    c.burst = 1.f;
    c.level = 1.f - 0.04f * k;
    addCaw(s, (size_t)(t * kSynthRate), c, rng);
    t += v == 0 ? 0.075f : 0.085f - 0.003f * k;  // 1 번은 점점 빨라짐
  }
  return finishVoice(std::move(s));
}

// 딱 (부리 부딪히는 소리)
void addSnap(std::vector<float>& s, size_t at, float pitch, float heavy, std::mt19937& rng) {
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(0.07f * kSynthRate);
  if (s.size() < at + n) s.resize(at + n, 0.f);
  Reson wood;
  wood.set(pitch, 220.f);
  float ph = 0.f;
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT;
    float click = uni(rng) * std::exp(-t / 0.0015f);
    float knock = wood.run(click) * 6.f * std::exp(-t / 0.02f);
    ph += (180.f * heavy * std::exp(-t / 0.02f) + 70.f) * kT;
    float thump = std::sin(kTau * ph) * std::exp(-t / 0.03f) * 0.6f * heavy;
    s[at + i] += click * 0.8f + knock + thump;
  }
}

std::vector<float> makeBite(int v) {
  std::mt19937 rng(3000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  std::vector<float> s;
  switch (v) {
    case 0:  // 딱
      addSnap(s, 0, 1900.f, 1.f, rng);
      break;
    case 1: {  // 아작 (바삭바삭 씹는 소리)
      const float at[] = {0.f, 0.018f, 0.034f, 0.055f};
      for (int k = 0; k < 4; ++k) addSnap(s, (size_t)(at[k] * kSynthRate), 2400.f + 500.f * uni(rng), 0.4f, rng);
      break;
    }
    case 2:  // 딱딱
      addSnap(s, 0, 2000.f, 1.f, rng);
      addSnap(s, (size_t)(0.085f * kSynthRate), 1700.f, 0.9f, rng);
      break;
    default:  // 콱 (묵직하게)
      addSnap(s, 0, 1250.f, 1.8f, rng);
      break;
  }
  normalize(s, 0.9f);
  return s;
}

// 톡 (발소리) — 버전마다 바닥 울림 높이가 다름
std::vector<float> makeStep(int v) {
  std::mt19937 rng(4000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const float pitch[] = {380.f, 520.f, 300.f, 640.f};
  const size_t n = (size_t)(0.05f * kSynthRate);
  std::vector<float> s(n);
  Reson r;
  r.set(pitch[v % 4], 260.f);
  OnePole lp;
  lp.lowpass(2500.f);
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT;
    float nz = lp.lp(uni(rng)) * std::exp(-t / 0.004f);
    s[i] = nz * 0.6f + r.run(nz) * 4.f * std::exp(-t / 0.012f);
  }
  normalize(s, 0.6f);
  return s;
}

// 푸드덕 (날갯짓)
std::vector<float> makeFlap(int v) {
  std::mt19937 rng(5000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const int beats = v == 0 ? 3 : 5;
  const float gap = v == 0 ? 0.085f : 0.07f;
  const size_t n = (size_t)((beats * gap + 0.12f) * kSynthRate);
  std::vector<float> s(n, 0.f);
  Reson r;
  r.set(900.f, 900.f);
  OnePole lp;
  lp.lowpass(3000.f);
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT;
    float env = 0.f;
    for (int b = 0; b < beats; ++b) {
      float tb = t - b * gap;
      if (tb < 0.f) continue;
      env += (1.f - 0.15f * b) * smooth01(tb / 0.012f) * std::exp(-tb / 0.04f);
    }
    s[i] = r.run(lp.lp(uni(rng))) * env;
  }
  normalize(s, 0.8f);
  return s;
}

// 툭 (창 내려놓기)
std::vector<float> makeDrop(int v) {
  std::mt19937 rng(6000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(0.22f * kSynthRate);
  std::vector<float> s(n, 0.f);
  OnePole lp;
  lp.lowpass(700.f);
  auto thud = [&](size_t at, float f, float lvl) {
    float ph = 0.f;
    for (size_t i = 0; at + i < n; ++i) {
      float t = (float)i * kT;
      ph += (f * (0.55f + 0.45f * std::exp(-t / 0.03f))) * kT;
      s[at + i] += (std::sin(kTau * ph) * std::exp(-t / 0.06f) + lp.lp(uni(rng)) * std::exp(-t / 0.008f) * 1.5f) * lvl;
    }
  };
  thud(0, v == 0 ? 120.f : 95.f, 1.f);
  if (v == 1) thud((size_t)(0.07f * kSynthRate), 110.f, 0.35f);  // 살짝 튕김
  normalize(s, 0.85f);
  return s;
}

}  // namespace

int soundVariants(SoundId id) {
  switch (id) {
    case SoundId::Call: return 4;
    case SoundId::Chatter: return 2;
    case SoundId::Bite: return 4;
    case SoundId::Step: return 4;
    case SoundId::Flap: return 2;
    case SoundId::Drop: return 2;
    case SoundId::Count: break;
  }
  return 0;
}

std::vector<float> synthSound(SoundId id, int v) {
  switch (id) {
    case SoundId::Call: return makeCall(v);
    case SoundId::Chatter: return makeChatter(v);
    case SoundId::Bite: return makeBite(v);
    case SoundId::Step: return makeStep(v);
    case SoundId::Flap: return makeFlap(v);
    case SoundId::Drop: return makeDrop(v);
    case SoundId::Count: break;
  }
  return {};
}
