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
  float dur = 0.2f;
  float f0a = 950, f0b = 1150, f0c = 880;  // 시작 / 최고 / 끝 음정 (높을수록 작고 귀여운 새)
  float rough = 0.08f;                      // 거칠기 (작게 = 맑은 목소리)
  float breath = 0.06f;                     // 바람 소리
  float F1 = 1250, F2 = 2100, F3 = 3400, F4 = 4600;  // 작은 목 = 높은 공명
  float burst = 0.25f;                      // "ㄲ" 세기 (약하게)
  float drive = 0.025f;                     // 찌그러뜨리기 (작게 = 부드럽게)
  float vibrato = 0.02f;                    // 살짝 떨림
  float attack = 0.015f;                    // 부드럽게 시작
  float level = 1.f;
};

void addCaw(std::vector<float>& out, size_t start, const Caw& p, std::mt19937& rng) {
  std::normal_distribution<float> gauss(0.f, 1.f);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(p.dur * kSynthRate);
  if (out.size() < start + n + 64) out.resize(start + n + 64, 0.f);

  // ㄲ: 짧고 가벼운 파열음
  Reson bk;
  bk.set(3500.f, 1500.f);
  const size_t burstLen = (size_t)(0.008f * kSynthRate);
  for (size_t i = 0; i < burstLen; ++i) {
    float t = (float)i * kT;
    out[start + i] += bk.run(uni(rng)) * std::exp(-t / 0.002f) * p.burst * p.level * 3.f;
  }

  // 모음 "아"
  Reson r1, r2, r3, r4;
  const size_t v0 = (size_t)(0.005f * kSynthRate);
  float phase = 0.f, period = 1.f / p.f0a, pulseAmp = 1.f, prevG = 0.f;
  int pulseIdx = 0;
  for (size_t i = 0; i < n - v0; ++i) {
    const float t = (float)i * kT, x = t / (p.dur - 0.005f);
    float f0 = x < 0.25f ? p.f0a + (p.f0b - p.f0a) * smooth01(x / 0.25f)
                         : p.f0b + (p.f0c - p.f0b) * smooth01((x - 0.25f) / 0.75f);
    f0 *= 1.f + p.vibrato * std::sin(kTau * 28.f * t);
    float onset = smooth01(t / 0.03f), close = smooth01((x - 0.82f) / 0.18f);
    if (i % 32 == 0) {
      r1.set(600.f + (p.F1 - 600.f) * onset - 300.f * close, 180.f);
      r2.set(2400.f + (p.F2 - 2400.f) * onset, 250.f);
      r3.set(p.F3, 350.f);
      r4.set(p.F4, 500.f);
    }
    phase += kT / period;
    if (phase >= 1.f) {
      phase -= 1.f;
      ++pulseIdx;
      period = (1.f / f0) * (1.f + 0.035f * p.rough * gauss(rng));
      pulseAmp = (1.f + 0.15f * p.rough * gauss(rng)) * ((pulseIdx & 1) ? 1.f - 0.55f * p.rough : 1.f);
    }
    float g = glottal(phase) * pulseAmp;
    float dg = (g - prevG) * kSynthRate / f0;
    prevG = g;
    float src = dg + uni(rng) * p.breath * (0.25f + g);
    float y = r4.run(r3.run(r2.run(r1.run(src))));
    float env = smooth01(t / p.attack) * (x < 0.7f ? 1.f : std::pow(1.f - smooth01((x - 0.7f) / 0.3f), 0.7f));
    out[start + v0 + i] += std::tanh(y * p.drive) * env * p.level;
  }
}

std::vector<float> finishVoice(std::vector<float> s) {
  OnePole hp;
  hp.lowpass(300.f);
  for (float& v : s) v = hp.hp(v);
  normalize(s, 0.85f);
  return s;
}

// 까악 (귀엽게)
std::vector<float> makeCall(int v) {
  std::mt19937 rng(1000u + (unsigned)v);
  std::vector<float> s;
  Caw c;
  switch (v) {
    case 0:  // 까악
      addCaw(s, 0, c, rng);
      break;
    case 1:  // 깍깍
      c.dur = 0.1f;
      c.f0a = 1050, c.f0b = 1250, c.f0c = 1000;
      addCaw(s, 0, c, rng);
      c.f0a = 1100, c.f0b = 1320, c.f0c = 1080;  // 두 번째가 살짝 높게
      addCaw(s, (size_t)(0.17f * kSynthRate), c, rng);
      break;
    case 2:  // 까아~? (끝이 올라감)
      c.dur = 0.32f;
      c.f0a = 900, c.f0b = 1000, c.f0c = 1180;
      c.vibrato = 0.03f;
      addCaw(s, 0, c, rng);
      break;
    default:  // 꺄!
      c.dur = 0.12f;
      c.f0a = 1250, c.f0b = 1450, c.f0c = 1200;
      c.F1 = 1350;
      addCaw(s, 0, c, rng);
      break;
  }
  return finishVoice(std::move(s));
}

// 깍깍깍깍 (화났지만 귀엽게, 점점 높아짐)
std::vector<float> makeChatter(int v) {
  std::mt19937 rng(2000u + (unsigned)v);
  std::vector<float> s;
  const int notes = v == 0 ? 5 : 7;
  float t = 0.f;
  for (int k = 0; k < notes; ++k) {
    Caw c;
    c.dur = 0.055f;
    c.f0a = 1100.f + 60.f * k;
    c.f0b = c.f0a + 150.f;
    c.f0c = c.f0a;
    c.rough = 0.12f;
    c.breath = 0.1f;
    c.burst = 0.4f;
    c.attack = 0.006f;
    c.level = 1.f - 0.05f * k;
    addCaw(s, (size_t)(t * kSynthRate), c, rng);
    t += v == 0 ? 0.085f : 0.08f - 0.004f * k;
  }
  return finishVoice(std::move(s));
}

// 뽁 (물기): 음이 위로 휙 올라가는 짧은 소리
void addPop(std::vector<float>& s, size_t at, float fa, float fb, float dur, float lvl) {
  const size_t n = (size_t)(dur * 2.5f * kSynthRate);
  if (s.size() < at + n) s.resize(at + n, 0.f);
  float ph = 0.f;
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT;
    float f = fa * std::pow(fb / fa, std::min(1.f, t / dur));
    ph += f * kT;
    float env = smooth01(t / 0.001f) * std::exp(-t / (dur * 0.45f));
    s[at + i] += (std::sin(kTau * ph) + 0.25f * std::sin(2.f * kTau * ph)) * env * lvl;
  }
}

std::vector<float> makeBite(int v) {
  std::vector<float> s;
  switch (v) {
    case 0: addPop(s, 0, 380.f, 950.f, 0.05f, 1.f); break;   // 뽁
    case 1: addPop(s, 0, 600.f, 1500.f, 0.04f, 1.f); break;  // 뾱
    case 2:                                                   // 뽁뽁
      addPop(s, 0, 420.f, 1000.f, 0.045f, 1.f);
      addPop(s, (size_t)(0.09f * kSynthRate), 480.f, 1150.f, 0.045f, 0.9f);
      break;
    default:  // 냠
      addPop(s, 0, 500.f, 560.f, 0.06f, 0.8f);
      addPop(s, (size_t)(0.07f * kSynthRate), 700.f, 760.f, 0.07f, 1.f);
      break;
  }
  normalize(s, 0.8f);
  return s;
}

// 톡 (발소리): 작고 가벼운 나무 블록 소리
std::vector<float> makeStep(int v) {
  std::mt19937 rng(4000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const float pitch[] = {1300.f, 1600.f, 1150.f, 1900.f};
  const size_t n = (size_t)(0.035f * kSynthRate);
  std::vector<float> s(n);
  Reson r;
  r.set(pitch[v % 4], 180.f);
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT;
    float click = uni(rng) * std::exp(-t / 0.0008f);
    s[i] = r.run(click) * 5.f * std::exp(-t / 0.008f) + click * 0.15f;
  }
  normalize(s, 0.5f);
  return s;
}

// 파닥 (가벼운 날갯짓)
std::vector<float> makeFlap(int v) {
  std::mt19937 rng(5000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const int beats = v == 0 ? 3 : 4;
  const float gap = 0.07f;
  const size_t n = (size_t)((beats * gap + 0.08f) * kSynthRate);
  std::vector<float> s(n, 0.f);
  Reson r;
  r.set(1600.f, 1400.f);
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i * kT, env = 0.f;
    for (int b = 0; b < beats; ++b) {
      float tb = t - b * gap;
      if (tb >= 0.f) env += (1.f - 0.18f * b) * smooth01(tb / 0.008f) * std::exp(-tb / 0.025f);
    }
    s[i] = r.run(uni(rng)) * env;
  }
  normalize(s, 0.6f);
  return s;
}

// 통 (창 내려놓기): 장난감 나무 소리
std::vector<float> makeDrop(int v) {
  std::mt19937 rng(6000u + (unsigned)v);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(0.25f * kSynthRate);
  std::vector<float> s(n, 0.f);
  auto knock = [&](size_t at, float f, float lvl) {
    Reson a, b;
    a.set(f, 40.f);
    b.set(f * 2.4f, 90.f);
    for (size_t i = 0; at + i < n; ++i) {
      float t = (float)i * kT;
      float click = i < 40 ? uni(rng) * std::exp(-t / 0.0006f) : 0.f;
      s[at + i] += (a.run(click) * 3.f + b.run(click) * 1.5f) * lvl * std::exp(-t / 0.09f);
    }
  };
  knock(0, v == 0 ? 520.f : 440.f, 1.f);
  if (v == 1) knock((size_t)(0.08f * kSynthRate), 470.f, 0.35f);  // 통.. 통
  normalize(s, 0.7f);
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
