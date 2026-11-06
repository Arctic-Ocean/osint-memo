#pragma once
// waveOut 위의 아주 작은 믹서.
//  - 합성 소리는 여러 개가 겹쳐도 같이 재생된다.
//  - 1초 이상 조용하면 장치를 일시정지 → CPU 0, 절전 방해 없음.
//  - Assets\Sound\Call|Bite|Step 폴더에 mp3/wav 를 넣으면 그 소리를 대신 쓴다 (MCI 재생).
#include <windows.h>
#include <mmsystem.h>

#include <random>
#include <string>
#include <vector>

#include "../pet.h"

class Audio {
 public:
  ~Audio() { shutdown(); }
  void init(const std::wstring& soundDir, bool silent, uint32_t seed);
  void play(SoundId id);
  void shutdown();

 private:
  static constexpr int kRate = 44100;
  static constexpr int kBufSamples = 1024;  // ~23ms
  static constexpr int kBufs = 4;
  static constexpr int kKinds = 3;
  static constexpr int kVariants = 3;

  struct Voice {
    const float* s;
    size_t n, pos;
    float vol;
  };

  static DWORD WINAPI threadMain(LPVOID self);
  void pump();
  void mix(int16_t* out);

  bool silent_ = true;
  bool started_ = false;
  std::mt19937 rng_;
  std::vector<float> bank_[kKinds][kVariants];
  float vol_[kKinds] = {0.55f, 0.75f, 0.14f};
  std::vector<std::wstring> custom_[kKinds];

  HWAVEOUT wo_ = nullptr;
  HANDLE ev_ = nullptr, thread_ = nullptr;
  volatile LONG run_ = 0;
  WAVEHDR hdr_[kBufs] = {};
  int16_t buf_[kBufs][kBufSamples] = {};
  CRITICAL_SECTION cs_;
  bool csInit_ = false;
  std::vector<Voice> voices_;
  bool paused_ = false;
  int idleBufs_ = 0;
};
