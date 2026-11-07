#include "audio_win.h"

#include <mmsystem.h>

#include <algorithm>
#include <cstring>

#include "../synth.h"
#include "log.h"

namespace {

std::vector<std::wstring> listSoundFiles(const std::wstring& dir) {
  std::vector<std::wstring> out;
  WIN32_FIND_DATAW fd;
  HANDLE h = FindFirstFileW((dir + L"\\*").c_str(), &fd);
  if (h == INVALID_HANDLE_VALUE) return out;
  do {
    if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
    std::wstring name = fd.cFileName;
    size_t dot = name.find_last_of(L'.');
    if (dot == std::wstring::npos) continue;
    std::wstring ext = name.substr(dot);
    for (auto& c : ext) c = (wchar_t)towlower(c);
    if (ext == L".mp3" || ext == L".wav") out.push_back(dir + L"\\" + name);
  } while (FindNextFileW(h, &fd));
  FindClose(h);
  std::sort(out.begin(), out.end());
  return out;
}

}  // namespace

namespace {
// 종류별 음량 (발소리는 작게)
constexpr float kVolume[kSoundKinds] = {0.55f, 0.5f, 0.75f, 0.14f, 0.35f, 0.5f};
constexpr int kMciVolume[kSoundKinds] = {800, 750, 900, 250, 550, 750};
}  // namespace

void Audio::init(const std::wstring& soundDir, bool silent, uint32_t seed) {
  silent_ = silent;
  rng_.seed(seed);
  if (silent_) {
    mlog::line("audio: silenced by config");
    return;
  }

  // 합성음: 종류마다 여러 버전
  for (int k = 0; k < kSoundKinds; ++k) {
    SoundId id = (SoundId)k;
    for (int v = 0; v < soundVariants(id); ++v) bank_[k].push_back(synthSound(id, v));
    last_[k] = -1;
  }

  // 사용자 소리 파일 (있으면 그 종류는 합성음 대신 사용)
  int aliasN = 0;
  std::string summary;
  for (int k = 0; k < kSoundKinds; ++k) {
    std::wstring sub(soundName((SoundId)k), soundName((SoundId)k) + std::strlen(soundName((SoundId)k)));
    for (const auto& path : listSoundFiles(soundDir + L"\\" + sub)) {
      std::wstring alias = L"magpie_snd_" + std::to_wstring(aliasN++);
      std::wstring cmd = L"open \"" + path + L"\" type mpegvideo alias " + alias;
      if (mciSendStringW(cmd.c_str(), nullptr, 0, nullptr) != 0) {
        mlog::line("could not open sound file: %s", mlog::utf8(path).c_str());
        continue;
      }
      mciSendStringW((L"setaudio " + alias + L" volume to " + std::to_wstring(kMciVolume[k])).c_str(), nullptr, 0,
                     nullptr);
      custom_[k].push_back(alias);
    }
    summary += std::string(" ") + soundName((SoundId)k) + "=" + std::to_string(custom_[k].size());
  }
  mlog::line("custom sound files:%s", summary.c_str());

  InitializeCriticalSection(&cs_);
  csInit_ = true;
  ev_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  WAVEFORMATEX fmt = {};
  fmt.wFormatTag = WAVE_FORMAT_PCM;
  fmt.nChannels = 1;
  fmt.nSamplesPerSec = kRate;
  fmt.wBitsPerSample = 16;
  fmt.nBlockAlign = 2;
  fmt.nAvgBytesPerSec = kRate * 2;
  MMRESULT mr = waveOutOpen(&wo_, WAVE_MAPPER, &fmt, (DWORD_PTR)ev_, 0, CALLBACK_EVENT);
  if (mr != MMSYSERR_NOERROR) {
    mlog::line("audio: waveOutOpen failed (%u) - running without synthesized sound", (unsigned)mr);
    wo_ = nullptr;
    return;  // 소리 장치가 없어도 프로그램은 계속 돈다
  }
  for (int i = 0; i < kBufs; ++i) {
    hdr_[i].lpData = (LPSTR)buf_[i];
    hdr_[i].dwBufferLength = kBufSamples * 2;
    waveOutPrepareHeader(wo_, &hdr_[i], sizeof(WAVEHDR));
    hdr_[i].dwFlags |= WHDR_DONE;  // 스레드가 첫 바퀴에 채워서 보냄
  }
  run_ = 1;
  thread_ = CreateThread(nullptr, 0, threadMain, this, 0, nullptr);
  started_ = thread_ != nullptr;
  mlog::line("audio: %s", started_ ? "ok" : "thread failed");
  SetEvent(ev_);
}

void Audio::play(SoundId id) {
  if (silent_) return;
  const int k = (int)id;
  if (k < 0 || k >= kSoundKinds) return;
  if (!custom_[k].empty()) {
    const std::wstring& alias = custom_[k][rng_() % custom_[k].size()];
    mciSendStringW((L"play " + alias + L" from 0").c_str(), nullptr, 0, nullptr);
    return;
  }
  if (!started_ || bank_[k].empty()) return;
  // 무작위 버전 (바로 전과 같은 건 피함)
  int n = (int)bank_[k].size();
  int v = (int)(rng_() % n);
  if (n > 1 && v == last_[k]) v = (v + 1 + (int)(rng_() % (n - 1))) % n;
  last_[k] = v;
  const std::vector<float>& s = bank_[k][v];
  EnterCriticalSection(&cs_);
  if (voices_.size() < 16) voices_.push_back({s.data(), s.size(), 0, kVolume[k]});
  idleBufs_ = 0;
  if (paused_) {
    // 쌓여 있던 무음 버퍼를 버리고 바로 재생 (지연 최소화)
    waveOutReset(wo_);
    waveOutRestart(wo_);
    paused_ = false;
  }
  LeaveCriticalSection(&cs_);
  SetEvent(ev_);
}

DWORD WINAPI Audio::threadMain(LPVOID self) {
  Audio* a = static_cast<Audio*>(self);
  while (a->run_) {
    WaitForSingleObject(a->ev_, 250);
    if (!a->run_) break;
    a->pump();
  }
  return 0;
}

void Audio::pump() {
  EnterCriticalSection(&cs_);
  for (int i = 0; i < kBufs; ++i) {
    WAVEHDR& h = hdr_[i];
    if (!(h.dwFlags & WHDR_DONE)) continue;
    mix(buf_[i]);
    h.dwFlags &= ~WHDR_DONE;
    waveOutWrite(wo_, &h, sizeof(WAVEHDR));
    if (voices_.empty()) ++idleBufs_;
  }
  if (!paused_ && voices_.empty() && idleBufs_ > 45) {
    waveOutPause(wo_);
    paused_ = true;
  }
  LeaveCriticalSection(&cs_);
}

void Audio::mix(int16_t* out) {
  float acc[kBufSamples] = {};
  for (Voice& v : voices_) {
    size_t n = std::min<size_t>(kBufSamples, v.n - v.pos);
    for (size_t i = 0; i < n; ++i) acc[i] += v.s[v.pos + i] * v.vol;
    v.pos += n;
  }
  voices_.erase(std::remove_if(voices_.begin(), voices_.end(), [](const Voice& v) { return v.pos >= v.n; }),
                voices_.end());
  for (int i = 0; i < kBufSamples; ++i) {
    float x = std::max(-1.f, std::min(1.f, acc[i]));
    out[i] = (int16_t)(x * 32000.f);
  }
}

void Audio::shutdown() {
  if (started_) {
    run_ = 0;
    SetEvent(ev_);
    WaitForSingleObject(thread_, 2000);
    CloseHandle(thread_);
    thread_ = nullptr;
    started_ = false;
  }
  if (wo_) {
    waveOutReset(wo_);
    for (int i = 0; i < kBufs; ++i) waveOutUnprepareHeader(wo_, &hdr_[i], sizeof(WAVEHDR));
    waveOutClose(wo_);
    wo_ = nullptr;
  }
  if (ev_) {
    CloseHandle(ev_);
    ev_ = nullptr;
  }
  if (csInit_) {
    DeleteCriticalSection(&cs_);
    csInit_ = false;
  }
  bool anyCustom = false;
  for (auto& c : custom_) anyCustom |= !c.empty();
  if (anyCustom) mciSendStringW(L"close all", nullptr, 0, nullptr);
  for (auto& c : custom_) c.clear();
}
