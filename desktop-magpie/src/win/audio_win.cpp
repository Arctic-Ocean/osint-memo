#include "audio_win.h"

#include <mmsystem.h>

#include <algorithm>

#include "../synth.h"

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

void Audio::init(const std::wstring& soundDir, bool silent, uint32_t seed) {
  silent_ = silent;
  rng_.seed(seed);
  if (silent_) return;

  for (int v = 0; v < kVariants; ++v) {
    bank_[0][v] = synthCall(seed + 11u * v + 1u);
    bank_[1][v] = synthBite(seed + 13u * v + 2u);
    bank_[2][v] = synthStep(seed + 17u * v + 3u);
  }

  // 사용자 소리 파일 (있으면 합성음 대신 사용)
  static const wchar_t* kDirs[kKinds] = {L"Call", L"Bite", L"Step"};
  static const int kMciVol[kKinds] = {800, 900, 250};
  int aliasN = 0;
  for (int k = 0; k < kKinds; ++k) {
    for (const auto& path : listSoundFiles(soundDir + L"\\" + kDirs[k])) {
      std::wstring alias = L"magpie_snd_" + std::to_wstring(aliasN++);
      std::wstring cmd = L"open \"" + path + L"\" type mpegvideo alias " + alias;
      if (mciSendStringW(cmd.c_str(), nullptr, 0, nullptr) != 0) continue;
      mciSendStringW((L"setaudio " + alias + L" volume to " + std::to_wstring(kMciVol[k])).c_str(), nullptr, 0,
                     nullptr);
      custom_[k].push_back(alias);
    }
  }

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
  if (waveOutOpen(&wo_, WAVE_MAPPER, &fmt, (DWORD_PTR)ev_, 0, CALLBACK_EVENT) != MMSYSERR_NOERROR) {
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
  SetEvent(ev_);
}

void Audio::play(SoundId id) {
  if (silent_) return;
  const int k = (int)id;
  if (!custom_[k].empty()) {
    const std::wstring& alias = custom_[k][rng_() % custom_[k].size()];
    mciSendStringW((L"play " + alias + L" from 0").c_str(), nullptr, 0, nullptr);
    return;
  }
  if (!started_) return;
  const std::vector<float>& s = bank_[k][rng_() % kVariants];
  EnterCriticalSection(&cs_);
  if (voices_.size() < 16) voices_.push_back({s.data(), s.size(), 0, vol_[k]});
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
