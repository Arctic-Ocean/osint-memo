#pragma once
// IPlatform 의 Windows 구현: 커서 조작, 모니터 목록, 까치가 물어 오는 창(사진/메모).
#include <windows.h>

#include <random>
#include <string>
#include <vector>

#include "../magpie_draw.h"
#include "../pet.h"

class Audio;

class WinPlatform : public IPlatform {
 public:
  WinPlatform(HINSTANCE inst, const std::wstring& baseDir, Audio& audio, const MagpiePalette& pal, float uiScale,
              HICON bigIcon, HICON smallIcon, uint32_t seed);

  Vec2 cursorPos() override;
  void setCursorPos(Vec2 p) override;
  std::unique_ptr<ICarriedWindow> spawnWindow(CarryKind kind, Vec2 anchorAt) override;
  void playSound(SoundId id) override;

  // 오버레이 창들을 항상 맨 위로 (아래→위 순서로 넘겨줌)
  void setOverlays(std::vector<HWND> bottomToTop) { overlays_ = std::move(bottomToTop); }
  void raiseOverlays();

  // 모니터 목록 (주 모니터가 0번)
  static void queryScreens(std::vector<RectF>& work, std::vector<RectF>& monitors);

  int spawned = 0, spawnFailed = 0;  // 진단용

 private:
  HBITMAP pickImage(int& w, int& h);
  std::wstring pickNote();

  HINSTANCE inst_;
  std::wstring baseDir_;
  Audio& audio_;
  MagpiePalette pal_;
  float ui_;
  std::mt19937 rng_;
  std::vector<HWND> overlays_;
};
