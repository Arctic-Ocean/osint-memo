// 데스크톱 까치 — Windows 진입점.
//
// 구조 (원본 데스크톱 펫들과 같은 원리, 다만 가볍게):
//   [발자국 레이어]  투명·클릭통과·항상 위 창. 발자국이 있을 때만 보이고, 바뀔 때만 다시 그린다.
//   [까치 레이어]    까치 주변만 덮는 작은 투명 창(약 170×170). 까치를 따라 움직인다.
//                   → 화면 전체를 매 프레임 다시 그리는 방식보다 그릴 픽셀이 수백 배 적다.
//   게임 루프: 메시지 처리 → 두뇌/몸 업데이트 → 변한 게 있을 때만 그리기 → 다음 프레임까지 잠들기
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <mmsystem.h>
#include <objbase.h>
#include <shellapi.h>

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>

#include "../config.h"
#include "../magpie_draw.h"
#include "../pet.h"
#include "../raster.h"
#include "audio_win.h"
#include "win_platform.h"

namespace {

constexpr float kQuitHoldSeconds = 1.2f;  // ESC 를 이만큼 누르고 있으면 종료
constexpr UINT WM_TRAY = WM_APP + 1;
constexpr UINT ID_TRAY_QUIT = 1001;
const wchar_t* const kOverlayClass = L"DesktopMagpieOverlay";
const wchar_t* const kControlClass = L"DesktopMagpieControl";

UINT g_taskbarCreated = 0;
bool g_screensDirty = true;
bool g_trayWanted = false;
bool g_trayAdded = false;
HICON g_trayIcon = nullptr;

// ---- 레이어드 윈도우 (투명 창) + 그 뒤의 DIB ----
struct Layer {
  HWND hwnd = nullptr;
  HDC dc = nullptr;
  HBITMAP bmp = nullptr;
  HGDIOBJ oldBmp = nullptr;
  uint32_t* bits = nullptr;
  int capW = 0, capH = 0;
  bool shown = false;

  bool reserve(int w, int h) {
    if (bits && w <= capW && h <= capH) return true;
    int nw = std::max(w, capW), nh = std::max(h, capH);
    if (bits) {  // 커질 때는 여유를 둬서 자주 재할당하지 않게
      nw = std::max(nw, capW + capW / 4);
      nh = std::max(nh, capH + capH / 4);
    }
    BITMAPINFO bi = {};
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = nw;
    bi.bmiHeader.biHeight = -nh;
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;
    void* p = nullptr;
    HBITMAP nb = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &p, nullptr, 0);
    if (!nb) return false;
    if (!dc) dc = CreateCompatibleDC(nullptr);
    if (bmp) {
      SelectObject(dc, oldBmp);
      DeleteObject(bmp);
    }
    oldBmp = SelectObject(dc, nb);
    bmp = nb;
    bits = static_cast<uint32_t*>(p);
    capW = nw;
    capH = nh;
    return true;
  }

  Canvas canvas(int w, int h, Vec2 origin) const {
    Canvas c;
    c.px = bits;
    c.w = w;
    c.h = h;
    c.stride = capW;
    c.origin = origin;
    return c;
  }

  void present(int x, int y, int w, int h, BYTE alpha) {
    POINT dst{x, y}, src{0, 0};
    SIZE sz{w, h};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, alpha, AC_SRC_ALPHA};
    UpdateLayeredWindow(hwnd, nullptr, &dst, &sz, dc, &src, 0, &bf, ULW_ALPHA);
    if (!shown) {
      ShowWindow(hwnd, SW_SHOWNOACTIVATE);
      shown = true;
    }
  }

  void hide() {
    if (shown) {
      ShowWindow(hwnd, SW_HIDE);
      shown = false;
    }
  }

  void destroy() {
    if (hwnd) DestroyWindow(hwnd);
    if (dc) {
      if (bmp) SelectObject(dc, oldBmp);
      DeleteDC(dc);
    }
    if (bmp) DeleteObject(bmp);
    *this = Layer();
  }
};

std::wstring exeDir() {
  wchar_t buf[MAX_PATH * 2];
  DWORD n = GetModuleFileNameW(nullptr, buf, (DWORD)(sizeof(buf) / sizeof(buf[0])));
  std::wstring s(buf, n);
  size_t k = s.find_last_of(L"\\/");
  return k == std::wstring::npos ? L"." : s.substr(0, k);
}

Config loadConfig(const std::wstring& dir) {
  Config cfg;
  const std::wstring path = dir + L"\\config.ini";
  HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
  if (h != INVALID_HANDLE_VALUE) {
    std::string text;
    LARGE_INTEGER sz;
    if (GetFileSizeEx(h, &sz) && sz.QuadPart > 0 && sz.QuadPart < (1 << 16)) {
      text.resize((size_t)sz.QuadPart);
      DWORD got = 0;
      if (!ReadFile(h, &text[0], (DWORD)text.size(), &got, nullptr)) got = 0;
      text.resize(got);
    }
    CloseHandle(h);
    parseConfig(text, cfg);
  } else {
    // 없으면 기본 설정 파일을 만들어 준다
    h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
      std::string t = defaultConfigText();
      DWORD wrote = 0;
      WriteFile(h, t.data(), (DWORD)t.size(), &wrote, nullptr);
      CloseHandle(h);
    }
  }
  return cfg;
}

void enableDpiAwareness() {
  // 매니페스트에도 적혀 있지만, 혹시 빠졌을 때를 대비
  using SetCtxFn = BOOL(WINAPI*)(HANDLE);
  HMODULE user = GetModuleHandleW(L"user32.dll");
  auto setCtx = user ? (SetCtxFn)(void*)GetProcAddress(user, "SetProcessDpiAwarenessContext") : nullptr;
  if (setCtx && setCtx((HANDLE)-4)) return;  // PER_MONITOR_AWARE_V2
  SetProcessDPIAware();
}

float systemDpiScale() {
  HDC dc = GetDC(nullptr);
  int d = dc ? GetDeviceCaps(dc, LOGPIXELSX) : 96;
  if (dc) ReleaseDC(nullptr, dc);
  return (d > 0 ? d : 96) / 96.f;
}

double nowSeconds() {
  static LARGE_INTEGER freq = [] {
    LARGE_INTEGER f;
    QueryPerformanceFrequency(&f);
    return f;
  }();
  LARGE_INTEGER t;
  QueryPerformanceCounter(&t);
  return (double)t.QuadPart / (double)freq.QuadPart;
}

LRESULT CALLBACK OverlayProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  switch (m) {
    case WM_NCHITTEST:
      return HTTRANSPARENT;  // 클릭은 아래 창으로
    case WM_MOUSEACTIVATE:
      return MA_NOACTIVATE;
  }
  return DefWindowProcW(h, m, w, l);
}

void addTray(HWND hwnd) {
  NOTIFYICONDATAW nid = {};
  nid.cbSize = sizeof(nid);
  nid.hWnd = hwnd;
  nid.uID = 1;
  nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
  nid.uCallbackMessage = WM_TRAY;
  nid.hIcon = g_trayIcon;
  lstrcpynW(nid.szTip, L"데스크톱 까치 (ESC 길게 = 종료)", (int)(sizeof(nid.szTip) / sizeof(nid.szTip[0])));
  g_trayAdded = Shell_NotifyIconW(NIM_ADD, &nid) != FALSE;
}

void removeTray(HWND hwnd) {
  if (!g_trayAdded) return;
  NOTIFYICONDATAW nid = {};
  nid.cbSize = sizeof(nid);
  nid.hWnd = hwnd;
  nid.uID = 1;
  Shell_NotifyIconW(NIM_DELETE, &nid);
  g_trayAdded = false;
}

LRESULT CALLBACK ControlProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (g_taskbarCreated && m == g_taskbarCreated) {  // 탐색기가 재시작되면 트레이 아이콘 다시 등록
    if (g_trayWanted) addTray(h);
    return 0;
  }
  switch (m) {
    case WM_TRAY:
      if (LOWORD(l) == WM_RBUTTONUP || LOWORD(l) == WM_LBUTTONUP) {
        POINT pt;
        GetCursorPos(&pt);
        HMENU menu = CreatePopupMenu();
        AppendMenuW(menu, MF_STRING, ID_TRAY_QUIT, L"까치 집에 보내기 (종료)");
        SetForegroundWindow(h);
        UINT cmd = (UINT)TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY, pt.x, pt.y, 0, h, nullptr);
        DestroyMenu(menu);
        PostMessageW(h, WM_NULL, 0, 0);
        if (cmd == ID_TRAY_QUIT) PostQuitMessage(0);
      }
      return 0;
    case WM_DISPLAYCHANGE:
    case WM_SETTINGCHANGE:  // 해상도/작업표시줄 변경
      g_screensDirty = true;
      break;
  }
  return DefWindowProcW(h, m, w, l);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
  enableDpiAwareness();
  const std::wstring dir = exeDir();
  const Config cfg = loadConfig(dir);
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  timeBeginPeriod(1);

  const float dpi = systemDpiScale();
  const float unit = 1.25f * dpi * cfg.scale;
  const MagpiePalette pal = makePalette(cfg);
  const uint32_t seed = (uint32_t)GetTickCount64() ^ (GetCurrentProcessId() << 16);

  HICON bigIcon = (HICON)LoadImageW(inst, MAKEINTRESOURCEW(1), IMAGE_ICON, GetSystemMetrics(SM_CXICON),
                                    GetSystemMetrics(SM_CYICON), 0);
  HICON smallIcon = (HICON)LoadImageW(inst, MAKEINTRESOURCEW(1), IMAGE_ICON, GetSystemMetrics(SM_CXSMICON),
                                      GetSystemMetrics(SM_CYSMICON), 0);
  if (!bigIcon) bigIcon = LoadIcon(nullptr, IDI_APPLICATION);
  if (!smallIcon) smallIcon = bigIcon;
  g_trayIcon = smallIcon;

  WNDCLASSEXW wc = {};
  wc.cbSize = sizeof(wc);
  wc.hInstance = inst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.lpfnWndProc = OverlayProc;
  wc.lpszClassName = kOverlayClass;
  RegisterClassExW(&wc);
  wc.lpfnWndProc = ControlProc;
  wc.lpszClassName = kControlClass;
  wc.hIcon = bigIcon;
  wc.hIconSm = smallIcon;
  RegisterClassExW(&wc);

  // 투명 + 클릭 통과 + Alt+Tab/작업표시줄에서 숨김 + 포커스 안 뺏음 + 항상 위
  const DWORD overlayEx = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST;
  Layer prints, magpie;
  prints.hwnd = CreateWindowExW(overlayEx, kOverlayClass, L"Desktop Magpie Tracks", WS_POPUP, 0, 0, 1, 1, nullptr,
                                nullptr, inst, nullptr);
  magpie.hwnd = CreateWindowExW(overlayEx, kOverlayClass, L"Desktop Magpie", WS_POPUP, 0, 0, 1, 1, nullptr, nullptr,
                                inst, nullptr);
  HWND control = CreateWindowExW(WS_EX_TOOLWINDOW, kControlClass, L"Desktop Magpie Control", WS_POPUP, 0, 0, 0, 0,
                                 nullptr, nullptr, inst, nullptr);
  if (!prints.hwnd || !magpie.hwnd || !control) return 1;

  g_taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
  g_trayWanted = cfg.showTrayIcon;
  if (g_trayWanted) addTray(control);

  Audio audio;
  audio.init(dir + L"\\Assets\\Sound", cfg.silenceSounds, seed);
  WinPlatform plat(inst, dir, audio, pal, dpi, bigIcon, smallIcon, seed);
  plat.setOverlays({prints.hwnd, magpie.hwnd});
  Pet pet(cfg, plat, unit, seed);

  const int petSize = (int)std::ceil(150.f * unit);
  if (!magpie.reserve(petSize, petSize)) return 1;

  const double frame = 1.0 / cfg.frameRate;
  double last = nowSeconds(), next = last, lastScreens = -100.0, lastRaise = 0.0;
  float escHold = 0.f, printsTimer = 0.f;
  MagpiePose lastPose;
  bool havePose = false;
  BYTE lastAlpha = 0;

  bool running = true;
  while (running) {
    MSG msg;
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
      if (msg.message == WM_QUIT) {
        running = false;
        break;
      }
      TranslateMessage(&msg);
      DispatchMessageW(&msg);
    }
    if (!running) break;

    double now = nowSeconds();
    if (now < next) {  // 다음 프레임까지 잠들기 (메시지가 오면 깸)
      DWORD ms = (DWORD)((next - now) * 1000.0);
      if (ms > 0) MsgWaitForMultipleObjectsEx(0, nullptr, ms, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
      continue;
    }
    next += frame;
    if (next < now) next = now + frame;  // 많이 밀렸으면 따라잡으려 하지 않음
    const float dt = (float)std::min(0.05, now - last);
    last = now;

    if (g_screensDirty || now - lastScreens > 3.0) {
      std::vector<RectF> work, mons;
      WinPlatform::queryScreens(work, mons);
      pet.setScreens(work, mons);
      g_screensDirty = false;
      lastScreens = now;
    }

    // ESC 길게 누르기 → 종료. 포커스가 없는 창이라 키 이벤트 대신 키 '상태'를 직접 확인.
    if (GetAsyncKeyState(VK_ESCAPE) & 0x8000)
      escHold += dt;
    else
      escHold = std::max(0.f, escHold - dt * 3.f);
    if (escHold >= kQuitHoldSeconds) break;

    pet.update(dt);

    // 발자국 레이어: 새 발자국이 생겼거나 흐려지는 중일 때만 (최대 10Hz) 다시 그림
    printsTimer += dt;
    const bool printsChanged = pet.takeFootprintsChanged();
    const auto& fps = pet.footprints();
    if (fps.empty()) {
      prints.hide();
    } else if (printsChanged || (printsTimer >= 0.1f && pet.footprintsFading())) {
      printsTimer = 0.f;
      float x0 = 1e30f, y0 = 1e30f, x1 = -1e30f, y1 = -1e30f;
      for (const auto& f : fps) {
        x0 = std::min(x0, f.pos.x);
        y0 = std::min(y0, f.pos.y);
        x1 = std::max(x1, f.pos.x);
        y1 = std::max(y1, f.pos.y);
      }
      const float pad = 8.f * unit;
      const int bx = (int)std::floor(x0 - pad), by = (int)std::floor(y0 - pad);
      const int bw = (int)std::ceil(x1 + pad) - bx, bh = (int)std::ceil(y1 + pad) - by;
      if (prints.reserve(bw, bh)) {
        Canvas c = prints.canvas(bw, bh, Vec2{(float)bx, (float)by});
        c.clear();
        for (const auto& f : fps)
          drawFootprint(c, f.pos, f.angle, unit, withAlpha(pal.mud, pal.mud.a * footprintAlpha(f.age)));
        prints.present(bx, by, bw, bh, 255);
      }
    }

    // 까치 레이어: 자세가 바뀌었을 때만 다시 그림
    const MagpiePose pose = pet.pose();
    const BYTE alpha = (BYTE)std::lround(255.f * (1.f - 0.85f * escHold / kQuitHoldSeconds));
    if (!havePose || alpha != lastAlpha || std::memcmp(&pose, &lastPose, sizeof pose) != 0) {
      const int x = (int)std::lround(pose.pos.x - petSize * 0.5f);
      const int y = (int)std::lround(pose.pos.y - petSize * 0.5f - 10.f * unit);
      Canvas c = magpie.canvas(petSize, petSize, Vec2{(float)x, (float)y});
      c.clear();
      drawMagpie(c, pose, pal);
      magpie.present(x, y, petSize, petSize, alpha);
      lastPose = pose;
      lastAlpha = alpha;
      havePose = true;
    }

    // 다른 '항상 위' 창에 덮이지 않도록 가끔 맨 위로
    if (now - lastRaise > 0.5) {
      plat.raiseOverlays();
      lastRaise = now;
    }
  }

  removeTray(control);
  audio.shutdown();
  prints.destroy();
  magpie.destroy();
  DestroyWindow(control);
  timeEndPeriod(1);
  CoUninitialize();
  return 0;
}
