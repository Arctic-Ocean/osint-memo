#pragma once
// waveOut 위의 아주 작은 믹서.
//  - 합성 소리는 여러 개가 겹쳐도 같이 재생된다.
//  - 1초 이상 조용하면 장치를 일시정지 → CPU 0, 절전 방해 없음.
//  - Assets\Sound\<종류>\ 폴더(Call, Chatter, Bite, Step, Flap, Drop)에 mp3/wav 를 넣으면
//    그 종류는 합성음 대신 그 파일들 중 하나를 무작위로 재생한다 (MCI).
#include <windows.h>
#include <mmsystem.h>

#include <random>
#include <string>
#include <vector>

#include "../sound_ids.h"

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
  std::vector<std::vector<float>> bank_[kSoundKinds];  // 종류별 여러 버전
  int last_[kSoundKinds] = {};                          // 같은 버전 연속 재생 피하기
  std::vector<std::wstring> custom_[kSoundKinds];

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
