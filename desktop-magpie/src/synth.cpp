#include "synth.h"

#include <algorithm>
#include <cmath>
#include <random>

namespace {

constexpr float kTau = 6.28318530718f;

// RBJ biquad (Transposed Direct Form II)
struct Biquad {
  float b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0, z1 = 0, z2 = 0;
  static Biquad bandpass(float f, float q) {
    float w = kTau * f / kSynthRate, al = std::sin(w) / (2.f * q), a0 = 1.f + al;
    Biquad b;
    b.b0 = al / a0;
    b.b1 = 0.f;
    b.b2 = -al / a0;
    b.a1 = -2.f * std::cos(w) / a0;
    b.a2 = (1.f - al) / a0;
    return b;
  }
  static Biquad highpass(float f, float q) {
    float w = kTau * f / kSynthRate, al = std::sin(w) / (2.f * q), cw = std::cos(w), a0 = 1.f + al;
    Biquad b;
    b.b0 = (1.f + cw) / 2.f / a0;
    b.b1 = -(1.f + cw) / a0;
    b.b2 = (1.f + cw) / 2.f / a0;
    b.a1 = -2.f * cw / a0;
    b.a2 = (1.f - al) / a0;
    return b;
  }
  static Biquad lowpass(float f, float q) {
    float w = kTau * f / kSynthRate, al = std::sin(w) / (2.f * q), cw = std::cos(w), a0 = 1.f + al;
    Biquad b;
    b.b0 = (1.f - cw) / 2.f / a0;
    b.b1 = (1.f - cw) / a0;
    b.b2 = (1.f - cw) / 2.f / a0;
    b.a1 = -2.f * cw / a0;
    b.a2 = (1.f - al) / a0;
    return b;
  }
  float run(float x) {
    float y = b0 * x + z1;
    z1 = b1 * x - a1 * y + z2;
    z2 = b2 * x - a2 * y;
    return y;
  }
};

void normalize(std::vector<float>& s, float peak) {
  float m = 0.f;
  for (float v : s) m = std::max(m, std::fabs(v));
  if (m < 1e-6f) return;
  float k = peak / m;
  for (float& v : s) v *= k;
}

}  // namespace

std::vector<float> synthCall(uint32_t seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<float> uni(-1.f, 1.f), jit(-1.f, 1.f);
  const int syll = 2 + (int)(rng() % 2);
  const float sylDur = 0.15f + 0.02f * jit(rng);
  const float gap = 0.085f;
  const float total = syll * sylDur + (syll - 1) * gap + 0.03f;
  std::vector<float> out((size_t)(total * kSynthRate), 0.f);

  Biquad f1 = Biquad::bandpass(1650.f, 3.5f), f2 = Biquad::bandpass(2900.f, 4.5f), f3 = Biquad::bandpass(950.f, 2.f);
  float phase = 0.f;
  for (int k = 0; k < syll; ++k) {
    const size_t start = (size_t)(k * (sylDur + gap) * kSynthRate);
    const float p0 = 1000.f * (1.f + 0.06f * jit(rng)) * (1.f - 0.04f * k);
    const size_t n = (size_t)(sylDur * kSynthRate);
    for (size_t i = 0; i < n && start + i < out.size(); ++i) {
      float t = (float)i / kSynthRate, x = t / sylDur;
      // 살짝 올라갔다가 쭉 떨어지는 음정 + 떨림
      float f = p0 * (x < 0.12f ? 0.92f + 0.08f * x / 0.12f : 1.f - 0.28f * (x - 0.12f) / 0.88f);
      f *= 1.f + 0.02f * std::sin(kTau * 35.f * t);
      phase += f / kSynthRate;
      phase -= std::floor(phase);
      float saw = 2.f * phase - 1.f;
      float rasp = uni(rng) * (phase < 0.25f ? 0.75f : 0.35f);  // 거친 숨소리
      float src = saw + rasp;
      float env = std::min(1.f, t / 0.008f) * (x < 0.75f ? 1.f : (1.f - x) / 0.25f);
      float y = f1.run(src) + 0.6f * f2.run(src) + 0.5f * f3.run(src) + 0.08f * src;
      out[start + i] += y * env;
    }
  }
  normalize(out, 0.85f);
  return out;
}

std::vector<float> synthBite(uint32_t seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(0.09f * kSynthRate);
  std::vector<float> out(n);
  Biquad hp = Biquad::highpass(2500.f, 0.7f);
  float phase = 0.f;
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i / kSynthRate;
    float noise = uni(rng);
    float click = noise * std::exp(-t / 0.0025f);
    float crack = hp.run(noise) * std::exp(-t / 0.012f) * 0.6f;
    float f = 300.f * std::exp(-t / 0.03f) + 90.f;
    phase += f / kSynthRate;
    float thump = std::sin(kTau * phase) * std::exp(-t / 0.035f) * 0.8f;
    out[i] = click + crack + thump;
  }
  normalize(out, 0.9f);
  return out;
}

std::vector<float> synthStep(uint32_t seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<float> uni(-1.f, 1.f);
  const size_t n = (size_t)(0.045f * kSynthRate);
  std::vector<float> out(n);
  Biquad lp = Biquad::lowpass(1800.f, 0.7f);
  const float f0 = 150.f + 30.f * uni(rng);
  float phase = 0.f;
  for (size_t i = 0; i < n; ++i) {
    float t = (float)i / kSynthRate;
    phase += f0 / kSynthRate;
    out[i] = lp.run(uni(rng)) * std::exp(-t / 0.006f) * 0.7f + std::sin(kTau * phase) * std::exp(-t / 0.015f) * 0.6f;
  }
  normalize(out, 0.6f);
  return out;
}
