// 데스크톱 까치 — Windows 진입점.
//
// 구조 (원본 데스크톱 펫들과 같은 원리, 다만 가볍게):
//   [발자국 레이어]  투명·클릭통과·항상 위 창. 발자국이 있을 때만 보이고, 바뀔 때만 다시 그린다.
//   [까치 레이어]    까치 주변만 덮는 작은 투명 창(약 190×190). 까치를 따라 움직인다.
//                   → 화면 전체를 매 프레임 다시 그리는 방식보다 그릴 픽셀이 수백 배 적다.
//   게임 루프: 메시지 처리 → 두뇌/몸 업데이트 → 변한 게 있을 때만 그리기 → 다음 프레임까지 잠들기
//
// 진단: 실행할 때마다 exe 옆에 DesktopMagpie.log 를 남긴다.
// 자가 진단: DesktopMagpie.exe --selftest [초]  → 모든 행동을 차례로 시키고,
//            까치가 실제 화면에 보이는지까지 확인한 뒤 종료 코드로 결과를 알려준다 (0 = 통과).
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
#include "log.h"
#include "win_platform.h"

namespace {

constexpr const char* kVersion = "1.0.3";
constexpr float kQuitHoldSeconds = 1.2f;  // ESC 를 이만큼 누르고 있으면 종료
constexpr UINT WM_TRAY = WM_APP + 1;
constexpr UINT WM_MAGPIE_QUIT = WM_APP + 2;  // Close_Magpie.bat (DesktopMagpie.exe --quit) 가 보내는 "집에 가" 신호
constexpr UINT ID_TRAY_QUIT = 1001;
const wchar_t* const kOverlayClass = L"DesktopMagpieOverlay";
const wchar_t* const kControlClass = L"DesktopMagpieControl";
const wchar_t* const kTitle = L"데스크톱 까치";

UINT g_taskbarCreated = 0;
bool g_screensDirty = true;
bool g_trayWanted = false;
bool g_trayAdded = false;
bool g_selftest = false;
HICON g_trayIcon = nullptr;
HWND g_clickable = nullptr;  // 클릭할 수 있는 까치 창 (ChaseWhenClicked=True 일 때)
bool g_poked = false;        // 까치가 클릭됨

// ---- 오류 처리 ----

void showError(const std::wstring& msg) {
  if (g_selftest) return;  // 자동 테스트 중엔 대화상자로 멈추지 않음
  std::wstring full = msg;
  if (!mlog::path().empty()) full += L"\n\n자세한 내용: " + mlog::path();
  MessageBoxW(nullptr, full.c_str(), kTitle, MB_ICONERROR | MB_OK | MB_TOPMOST | MB_SETFOREGROUND);
}

int fatal(const char* what, const wchar_t* userMsg) {
  DWORD err = GetLastError();
  mlog::line("FATAL: %s (GetLastError=%lu)", what, err);
  showError(userMsg);
  mlog::close();
  return 1;
}

LONG WINAPI crashFilter(EXCEPTION_POINTERS* ep) {
  const DWORD code = ep->ExceptionRecord->ExceptionCode;
  void* addr = ep->ExceptionRecord->ExceptionAddress;
  HMODULE mod = nullptr;
  GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                     (LPCWSTR)addr, &mod);
  wchar_t name[MAX_PATH] = L"?";
  if (mod) GetModuleFileNameW(mod, name, MAX_PATH);
  mlog::line("CRASH: exception 0x%08lX at %s+0x%llx", code, mlog::utf8(name).c_str(),
             (unsigned long long)((const char*)addr - (const char*)mod));
  showError(L"까치가 오류로 멈췄어요.\nDesktopMagpie.log 파일을 보내주시면 고칠게요.");
  mlog::close();
  ExitProcess(3);
  return EXCEPTION_EXECUTE_HANDLER;
}

// ---- 레이어드 윈도우 (투명 창) + 그 뒤의 DIB ----
struct Layer {
  HWND hwnd = nullptr;
  HDC dc = nullptr;
  HBITMAP bmp = nullptr;
  HGDIOBJ oldBmp = nullptr;
  uint32_t* bits = nullptr;
  int capW = 0, capH = 0;
  bool shown = false;
  int failures = 0;
  const char* name = "";

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
    if (!nb) {
      mlog::line("%s: CreateDIBSection %dx%d failed (err=%lu)", name, nw, nh, GetLastError());
      return false;
    }
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
    GdiFlush();  // DIB 비트를 직접 만지기 전에 GDI 작업을 끝내 둔다
    Canvas c;
    c.px = bits;
    c.w = w;
    c.h = h;
    c.stride = capW;
    c.origin = origin;
    return c;
  }

  bool present(int x, int y, int w, int h, BYTE alpha) {
    POINT dst{x, y}, src{0, 0};
    SIZE sz{w, h};
    BLENDFUNCTION bf{AC_SRC_OVER, 0, alpha, AC_SRC_ALPHA};
    if (!UpdateLayeredWindow(hwnd, nullptr, &dst, &sz, dc, &src, 0, &bf, ULW_ALPHA)) {
      if (++failures <= 5) mlog::line("%s: UpdateLayeredWindow failed (err=%lu)", name, GetLastError());
      return false;
    }
    if (!shown) {
      ShowWindow(hwnd, SW_SHOWNOACTIVATE);
      shown = true;
      mlog::line("%s: first frame shown at (%d,%d) size %dx%d", name, x, y, w, h);
    }
    return true;
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

std::wstring tempDir() {
  wchar_t buf[MAX_PATH + 1];
  DWORD n = GetTempPathW(MAX_PATH + 1, buf);
  std::wstring s(buf, n);
  while (!s.empty() && (s.back() == L'\\' || s.back() == L'/')) s.pop_back();
  return s;
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
    mlog::line("config: loaded %s", mlog::utf8(path).c_str());
  } else {
    // 없으면 기본 설정 파일을 만들어 준다
    h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h != INVALID_HANDLE_VALUE) {
      std::string t = defaultConfigText();
      DWORD wrote = 0;
      WriteFile(h, t.data(), (DWORD)t.size(), &wrote, nullptr);
      CloseHandle(h);
      mlog::line("config: not found, wrote defaults to %s", mlog::utf8(path).c_str());
    } else {
      mlog::line("config: not found and could not create (err=%lu), using defaults", GetLastError());
    }
  }
  return cfg;
}

void logWindowsVersion() {
  using RtlGetVersionFn = LONG(WINAPI*)(OSVERSIONINFOW*);
  HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
  auto fn = ntdll ? (RtlGetVersionFn)(void*)GetProcAddress(ntdll, "RtlGetVersion") : nullptr;
  OSVERSIONINFOW v = {};
  v.dwOSVersionInfoSize = sizeof(v);
  if (fn && fn(&v) == 0)
    mlog::line("windows %lu.%lu build %lu", v.dwMajorVersion, v.dwMinorVersion, v.dwBuildNumber);
  SYSTEM_INFO si;
  GetNativeSystemInfo(&si);
  mlog::line("cpu arch %u, %lu logical processors", (unsigned)si.wProcessorArchitecture, si.dwNumberOfProcessors);
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

double nowSeconds() { return mlog::seconds(); }

LRESULT CALLBACK OverlayProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  switch (m) {
    case WM_NCHITTEST:
      // 까치 몸(투명하지 않은 픽셀)만 클릭을 받고, 나머지는 아래 창으로 통과
      return h == g_clickable ? HTCLIENT : HTTRANSPARENT;
    case WM_MOUSEACTIVATE:
      return MA_NOACTIVATE;
    case WM_SETCURSOR:
      if (h == g_clickable) {
        SetCursor(LoadCursor(nullptr, IDC_HAND));
        return TRUE;
      }
      break;
    case WM_LBUTTONDOWN:
    case WM_RBUTTONDOWN:
      if (h == g_clickable) g_poked = true;
      return 0;
  }
  return DefWindowProcW(h, m, w, l);
}

// DesktopMagpie.exe --quit : 실행 중인 까치(들)에게 종료 신호를 보낸다.
// taskkill 과 달리 관리자 권한으로 실행된 까치도 끌 수 있고, exe 이름이 바뀌어도 된다.
int quitRunning() {
  std::vector<HWND> wins;
  for (HWND w = FindWindowExW(nullptr, nullptr, kControlClass, nullptr); w;
       w = FindWindowExW(nullptr, w, kControlClass, nullptr))
    wins.push_back(w);
  if (wins.empty()) return 0;
  for (HWND w : wins) PostMessageW(w, WM_MAGPIE_QUIT, 0, 0);
  for (int i = 0; i < 40; ++i) {
    bool any = false;
    for (HWND w : wins) any = any || IsWindow(w);
    if (!any) return 0;
    Sleep(100);
  }
  int left = 0;  // 신호에 응답이 없으면 강제 종료
  for (HWND w : wins) {
    if (!IsWindow(w)) continue;
    DWORD pid = 0;
    GetWindowThreadProcessId(w, &pid);
    HANDLE p = OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, FALSE, pid);
    if (p && TerminateProcess(p, 0))
      WaitForSingleObject(p, 2000);
    else
      ++left;
    if (p) CloseHandle(p);
  }
  if (left)
    MessageBoxW(nullptr, L"까치를 끄지 못했어요.\nESC 키를 1초 정도 꾹 눌러 주세요.", kTitle,
                MB_ICONWARNING | MB_OK | MB_TOPMOST);
  return left ? 1 : 0;
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
  mlog::line("tray icon: %s", g_trayAdded ? "added" : "FAILED");
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
        if (cmd == ID_TRAY_QUIT) {
          mlog::line("quit: tray menu");
          PostQuitMessage(0);
        }
      }
      return 0;
    case WM_MAGPIE_QUIT:
      mlog::line("quit: requested by Close_Magpie.bat (--quit)");
      PostQuitMessage(0);
      return 0;
    case WM_DISPLAYCHANGE:
    case WM_SETTINGCHANGE:  // 해상도/작업표시줄 변경
      g_screensDirty = true;
      break;
  }
  return DefWindowProcW(h, m, w, l);
}

// 자가 진단: 화면을 실제로 캡처해서, 우리가 그린 불투명 픽셀이 화면에 그대로 보이는지 비율로 확인
double measureOnScreen(const Layer& L, int x, int y, int w, int h, int& total) {
  total = 0;
  HDC screen = GetDC(nullptr);
  if (!screen) return 0.0;
  HDC mem = CreateCompatibleDC(screen);
  BITMAPINFO bi = {};
  bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
  bi.bmiHeader.biWidth = w;
  bi.bmiHeader.biHeight = -h;
  bi.bmiHeader.biPlanes = 1;
  bi.bmiHeader.biBitCount = 32;
  bi.bmiHeader.biCompression = BI_RGB;
  void* p = nullptr;
  HBITMAP bmp = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &p, nullptr, 0);
  double ratio = 0.0;
  if (bmp && mem) {
    HGDIOBJ old = SelectObject(mem, bmp);
    BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY | CAPTUREBLT);
    GdiFlush();
    const uint32_t* shot = static_cast<const uint32_t*>(p);
    int match = 0;
    for (int j = 0; j < h; ++j)
      for (int i = 0; i < w; ++i) {
        uint32_t c = L.bits[(size_t)j * L.capW + i];
        if ((c >> 24) != 255u) continue;
        ++total;
        uint32_t s = shot[(size_t)j * w + i];
        int db = std::abs((int)(c & 255) - (int)(s & 255));
        int dg = std::abs((int)((c >> 8) & 255) - (int)((s >> 8) & 255));
        int dr = std::abs((int)((c >> 16) & 255) - (int)((s >> 16) & 255));
        if (db < 24 && dg < 24 && dr < 24) ++match;
      }
    ratio = total ? (double)match / total : 0.0;
    SelectObject(mem, old);
  }
  if (bmp) DeleteObject(bmp);
  if (mem) DeleteDC(mem);
  ReleaseDC(nullptr, screen);
  return ratio;
}

struct SelfTest {
  bool on = false;
  double limit = 120.0;
  const TaskId plan[4] = {TaskId::CollectImage, TaskId::CollectNote, TaskId::TrackMud, TaskId::NabMouse};
  int next = 0;  // 0..3 = plan, 4 = 까치 클릭해 보기, 5 = 끝
  double clickAt = 0.0;
  bool pokeChased = false;
  bool visChecked = false;
  double visRatio = -1.0;
  int visPixels = 0;
  bool grabbed = false;
  size_t maxPrints = 0;
};

}  // namespace

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, PWSTR, int) {
  SelfTest st;
  {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; argv && i < argc; ++i) {
      if (lstrcmpiW(argv[i], L"--quit") == 0) {
        LocalFree(argv);
        return quitRunning();  // 로그도 건드리지 않고 바로 끝
      }
      if (lstrcmpiW(argv[i], L"--selftest") == 0) {
        st.on = true;
        if (i + 1 < argc) {
          double v = _wtof(argv[i + 1]);
          if (v > 0) st.limit = v;
        }
      }
    }
    if (argv) LocalFree(argv);
  }
  g_selftest = st.on;
  SetUnhandledExceptionFilter(crashFilter);

  const std::wstring dir = exeDir();
  mlog::open(dir + L"\\DesktopMagpie.log", tempDir() + L"\\DesktopMagpie.log");
  mlog::line("Desktop Magpie %s start%s", kVersion, st.on ? " (selftest)" : "");
  mlog::line("exe dir: %s", mlog::utf8(dir).c_str());
  logWindowsVersion();

  enableDpiAwareness();
  Config cfg = loadConfig(dir);
  if (st.on) {  // 자가 진단에선 정해진 순서로만 행동하게
    cfg.firstWander = cfg.minWander = cfg.maxWander = 9999.f;
    cfg.attackRandomly = false;
    cfg.canAttackMouse = true;
  }
  if (FAILED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED))) mlog::line("CoInitializeEx failed");
  timeBeginPeriod(1);

  const float dpi = systemDpiScale();
  const float unit = 1.25f * dpi * cfg.scale;
  const MagpiePalette pal = makePalette(cfg);
  const uint32_t seed = (uint32_t)GetTickCount64() ^ (GetCurrentProcessId() << 16);
  mlog::line("dpi scale %.2f, unit %.2f, fps %d, sounds %s, attack mouse %s", dpi, unit, cfg.frameRate,
             cfg.silenceSounds ? "off" : "on", cfg.canAttackMouse ? "on" : "off");

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
  if (!RegisterClassExW(&wc)) return fatal("RegisterClassEx(overlay)", L"창을 만들 수 없어요.");
  wc.lpfnWndProc = ControlProc;
  wc.lpszClassName = kControlClass;
  wc.hIcon = bigIcon;
  wc.hIconSm = smallIcon;
  if (!RegisterClassExW(&wc)) return fatal("RegisterClassEx(control)", L"창을 만들 수 없어요.");

  // 투명 + 클릭 통과 + Alt+Tab/작업표시줄에서 숨김 + 포커스 안 뺏음 + 항상 위
  const DWORD overlayEx = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST;
  Layer prints, magpie;
  prints.name = "tracks layer";
  magpie.name = "magpie layer";
  prints.hwnd = CreateWindowExW(overlayEx, kOverlayClass, L"Desktop Magpie Tracks", WS_POPUP, 0, 0, 1, 1, nullptr,
                                nullptr, inst, nullptr);
  if (!prints.hwnd) return fatal("CreateWindowEx(tracks)", L"투명 창을 만들 수 없어요.");
  // ChaseWhenClicked 면 까치 창은 클릭을 받는다 (투명한 픽셀은 여전히 통과)
  const DWORD magpieEx = cfg.chaseWhenClicked ? (overlayEx & ~(DWORD)WS_EX_TRANSPARENT) : overlayEx;
  magpie.hwnd = CreateWindowExW(magpieEx, kOverlayClass, L"Desktop Magpie", WS_POPUP, 0, 0, 1, 1, nullptr, nullptr,
                                inst, nullptr);
  if (cfg.chaseWhenClicked) g_clickable = magpie.hwnd;
  if (!magpie.hwnd) return fatal("CreateWindowEx(magpie)", L"투명 창을 만들 수 없어요.");
  HWND control = CreateWindowExW(WS_EX_TOOLWINDOW, kControlClass, L"Desktop Magpie Control", WS_POPUP, 0, 0, 0, 0,
                                 nullptr, nullptr, inst, nullptr);
  if (!control) return fatal("CreateWindowEx(control)", L"창을 만들 수 없어요.");
  {  // 관리자 권한으로 실행돼도 Close_Magpie.bat 의 종료 신호는 받도록
    using FilterFn = BOOL(WINAPI*)(HWND, UINT, DWORD, void*);
    HMODULE user = GetModuleHandleW(L"user32.dll");
    auto filter = user ? (FilterFn)(void*)GetProcAddress(user, "ChangeWindowMessageFilterEx") : nullptr;
    if (filter) filter(control, WM_MAGPIE_QUIT, 1 /* MSGFLT_ALLOW */, nullptr);
  }
  mlog::line("windows created (click to chase: %s)", cfg.chaseWhenClicked ? "on" : "off");

  g_taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
  g_trayWanted = cfg.showTrayIcon;
  if (g_trayWanted) addTray(control);

  Audio audio;
  audio.init(dir + L"\\Assets\\Sound", cfg.silenceSounds, seed);
  WinPlatform plat(inst, dir, audio, pal, dpi, bigIcon, smallIcon, seed);
  plat.setOverlays({prints.hwnd, magpie.hwnd});
  Pet pet(cfg, plat, unit, seed);

  const int petSize = (int)std::ceil(150.f * unit);
  if (!magpie.reserve(petSize, petSize)) return fatal("magpie bitmap", L"그림 버퍼를 만들 수 없어요.");

  const double frame = 1.0 / cfg.frameRate;
  const double start = nowSeconds();
  double last = start, next = start, lastScreens = -100.0, lastRaise = 0.0, lastBeat = start, lastTaskEnd = start;
  float escHold = 0.f, printsTimer = 0.f;
  MagpiePose lastPose;
  bool havePose = false;
  BYTE lastAlpha = 0;
  long long frames = 0, draws = 0;
  size_t lastMonitorCount = 0;
  TaskId prevTask = TaskId::Wander;
  bool prevHold = false;
  int exitCode = 0;

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
    ++frames;

    if (g_screensDirty || now - lastScreens > 3.0) {
      std::vector<RectF> work, mons;
      WinPlatform::queryScreens(work, mons);
      if (mons.size() != lastMonitorCount) {
        lastMonitorCount = mons.size();
        for (size_t i = 0; i < mons.size(); ++i)
          mlog::line("monitor %zu: (%.0f,%.0f)-(%.0f,%.0f) work (%.0f,%.0f)-(%.0f,%.0f)%s", i, mons[i].x0, mons[i].y0,
                     mons[i].x1, mons[i].y1, work[i].x0, work[i].y0, work[i].x1, work[i].y1,
                     i == 0 ? " primary" : "");
        if (mons.empty()) mlog::line("monitor list EMPTY - using fallback 1920x1080");
      }
      pet.setScreens(work, mons);
      g_screensDirty = false;
      lastScreens = now;
    }

    // ESC 길게 누르기 → 종료. 포커스가 없는 창이라 키 이벤트 대신 키 '상태'를 직접 확인.
    if (!st.on && (GetAsyncKeyState(VK_ESCAPE) & 0x8000))
      escHold += dt;
    else
      escHold = std::max(0.f, escHold - dt * 3.f);
    if (escHold >= kQuitHoldSeconds) {
      mlog::line("quit: ESC held");
      break;
    }

    if (g_poked) {
      g_poked = false;
      const bool chase = pet.poke();
      mlog::line("magpie clicked -> %s", chase ? "chasing the cursor" : "busy, only grumbles");
      if (chase && st.on && st.next == 5) st.pokeChased = true;
    }

    pet.update(dt);

    // 상태 변화 기록
    if (pet.task() != prevTask) {
      Vec2 p = pet.position();
      mlog::line("task: %s -> %s at (%.0f,%.0f)", taskName(prevTask), taskName(pet.task()), p.x, p.y);
      if (pet.task() == TaskId::Wander) lastTaskEnd = now;
      prevTask = pet.task();
    }
    if (pet.holdingMouse() != prevHold) {
      prevHold = pet.holdingMouse();
      mlog::line("%s", prevHold ? "grabbed the mouse cursor" : "released the mouse cursor");
      if (prevHold) st.grabbed = true;
    }
    st.maxPrints = std::max(st.maxPrints, pet.footprints().size());

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
    const int px = (int)std::lround(pose.pos.x - petSize * 0.5f);
    const int py = (int)std::lround(pose.pos.y - petSize * 0.5f - 10.f * unit);
    if (!havePose || alpha != lastAlpha || std::memcmp(&pose, &lastPose, sizeof pose) != 0) {
      Canvas c = magpie.canvas(petSize, petSize, Vec2{(float)px, (float)py});
      c.clear();
      drawMagpie(c, pose, pal);
      if (magpie.present(px, py, petSize, petSize, alpha)) ++draws;
      lastPose = pose;
      lastAlpha = alpha;
      havePose = true;
    }

    // 다른 '항상 위' 창에 덮이지 않도록 가끔 맨 위로
    if (now - lastRaise > 0.5) {
      plat.raiseOverlays();
      lastRaise = now;
    }

    if (now - lastBeat > 10.0) {
      Vec2 p = pet.position();
      mlog::line("alive: %lld frames, %lld drawn, task %s at (%.0f,%.0f)", frames, draws, taskName(pet.task()), p.x,
                 p.y);
      lastBeat = now;
    }

    // ---- 자가 진단 ----
    if (st.on) {
      const RectF prim{0.f, 0.f, (float)GetSystemMetrics(SM_CXSCREEN), (float)GetSystemMetrics(SM_CYSCREEN)};
      const bool fullyVisible = px >= prim.x0 && py >= prim.y0 && px + petSize <= prim.x1 && py + petSize <= prim.y1;
      if (!st.visChecked && now - start > 1.5 && fullyVisible && magpie.shown) {
        Sleep(300);  // 데스크톱 합성이 이번 프레임을 화면에 올릴 때까지 대기 (까치는 이 동안 멈춰 있음)
        st.visRatio = measureOnScreen(magpie, px, py, petSize, petSize, st.visPixels);
        st.visChecked = true;
        mlog::line("visibility check: %.1f%% of %d opaque magpie pixels found on screen", st.visRatio * 100.0,
                   st.visPixels);
        next = nowSeconds();
        last = next;
      }
      const bool idle = pet.task() == TaskId::Wander && now - lastTaskEnd > 1.0;
      if (st.visChecked && idle && st.next < 4) {
        mlog::line("selftest: start %s", taskName(st.plan[st.next]));
        if (st.plan[st.next] == TaskId::NabMouse)  // 커서를 화면 가운데에 두고 잡게 함
          SetCursorPos((int)(prim.x1 * 0.5f), (int)(prim.y1 * 0.5f));
        pet.forceTask(st.plan[st.next++]);
      }
      else if (st.visChecked && idle && st.next == 4) {
        st.next = 5;
        if (cfg.chaseWhenClicked) {  // 까치 몸통 한가운데를 진짜 마우스 클릭으로 눌러 봄
          const Vec2 body = pose.pos + Vec2{0.f, -17.f * unit};
          SetCursorPos((int)std::lround(body.x), (int)std::lround(body.y));
          INPUT in[2] = {};
          in[0].type = in[1].type = INPUT_MOUSE;
          in[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
          in[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
          SendInput(2, in, sizeof(INPUT));
          Sleep(60);  // 클릭이 처리될 동안 까치를 멈춰 둠
          mlog::line("selftest: click the magpie at (%.0f,%.0f)", body.x, body.y);
        }
        st.clickAt = nowSeconds();
      }
      // 방금 작업을 시작했을 수 있으니 상태를 다시 확인
      const bool finished = st.next == 5 && pet.task() == TaskId::Wander && now - lastTaskEnd > 1.0 &&
                            now - st.clickAt > 2.0;
      if (finished || now - start > st.limit) {
        bool ok = true;
        auto req = [&](bool cond, const char* what) {
          mlog::line("  [%s] %s", cond ? "ok" : "FAIL", what);
          ok = ok && cond;
        };
        mlog::line("selftest summary after %.1fs:", now - start);
        req(finished, "all four tasks ran to completion");
        if (cfg.chaseWhenClicked) req(st.pokeChased, "clicking the magpie makes it chase");
        req(draws > 0 && magpie.failures == 0 && prints.failures == 0, "frames presented without errors");
        req(st.visChecked && st.visRatio >= 0.8, "magpie actually visible on screen");
        req(plat.spawned >= 2 && plat.spawnFailed == 0, "image + note windows created");
        req(st.grabbed, "mouse cursor grabbed");
        req(st.maxPrints > 0, "footprints drawn");
        mlog::line("SELFTEST %s (%lld frames, %.1f fps)", ok ? "PASS" : "FAIL", frames, frames / (now - start));
        exitCode = ok ? 0 : 2;
        break;
      }
    }
  }

  mlog::line("exiting (%lld frames, %lld drawn)", frames, draws);
  removeTray(control);
  audio.shutdown();
  prints.destroy();
  magpie.destroy();
  DestroyWindow(control);
  timeEndPeriod(1);
  CoUninitialize();
  mlog::close();
  return exitCode;
}
