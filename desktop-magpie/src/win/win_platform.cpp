#include "win_platform.h"

#include <wincodec.h>

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>

#include "audio_win.h"

namespace {

const wchar_t* const kImageClass = L"DesktopMagpieImage";
const wchar_t* const kNoteClass = L"DesktopMagpieNote";

struct ImageWinData {
  HBITMAP bmp = nullptr;
  int w = 0, h = 0;
};

struct NoteWinData {
  std::wstring text;
  HFONT font = nullptr;
  HWND edit = nullptr;
};

// ---- 창 프로시저 ----

LRESULT CALLBACK ImageProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (m == WM_NCCREATE) {
    auto* cs = reinterpret_cast<CREATESTRUCTW*>(l);
    SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)cs->lpCreateParams);
  }
  auto* d = reinterpret_cast<ImageWinData*>(GetWindowLongPtrW(h, GWLP_USERDATA));
  switch (m) {
    case WM_ERASEBKGND:
      return 1;
    case WM_PAINT: {
      PAINTSTRUCT ps;
      HDC dc = BeginPaint(h, &ps);
      if (d && d->bmp) {
        HDC mem = CreateCompatibleDC(dc);
        HGDIOBJ old = SelectObject(mem, d->bmp);
        BitBlt(dc, 0, 0, d->w, d->h, mem, 0, 0, SRCCOPY);
        SelectObject(mem, old);
        DeleteDC(mem);
      }
      EndPaint(h, &ps);
      return 0;
    }
    case WM_NCDESTROY:
      if (d) {
        if (d->bmp) DeleteObject(d->bmp);
        delete d;
        SetWindowLongPtrW(h, GWLP_USERDATA, 0);
      }
      break;
  }
  return DefWindowProcW(h, m, w, l);
}

LRESULT CALLBACK NoteProc(HWND h, UINT m, WPARAM w, LPARAM l) {
  if (m == WM_NCCREATE) {
    auto* cs = reinterpret_cast<CREATESTRUCTW*>(l);
    SetWindowLongPtrW(h, GWLP_USERDATA, (LONG_PTR)cs->lpCreateParams);
  }
  auto* d = reinterpret_cast<NoteWinData*>(GetWindowLongPtrW(h, GWLP_USERDATA));
  switch (m) {
    case WM_CREATE: {
      auto* cs = reinterpret_cast<CREATESTRUCTW*>(l);
      d->edit = CreateWindowExW(0, L"EDIT", d->text.c_str(),
                                WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL | ES_WANTRETURN, 0, 0, 0, 0, h,
                                (HMENU)(INT_PTR)1, cs->hInstance, nullptr);
      if (d->edit && d->font) SendMessageW(d->edit, WM_SETFONT, (WPARAM)d->font, TRUE);
      return 0;
    }
    case WM_SIZE:
      if (d && d->edit) {
        int pad = 6;
        MoveWindow(d->edit, pad, pad, std::max(0, (int)LOWORD(l) - 2 * pad), std::max(0, (int)HIWORD(l) - 2 * pad),
                   TRUE);
      }
      return 0;
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT:
      SetBkColor((HDC)w, RGB(255, 255, 255));
      return (LRESULT)GetStockObject(WHITE_BRUSH);
    case WM_SETFOCUS:
      if (d && d->edit) SetFocus(d->edit);
      return 0;
    case WM_NCDESTROY:
      if (d) {
        if (d->font) DeleteObject(d->font);
        delete d;
        SetWindowLongPtrW(h, GWLP_USERDATA, 0);
      }
      break;
  }
  return DefWindowProcW(h, m, w, l);
}

// ---- 까치가 물고 다니는 창 ----

class WinCarried : public ICarriedWindow {
 public:
  WinCarried(HWND h, Vec2 size, Vec2 anchor) : h_(h), size_(size), anchor_(anchor) {}
  bool alive() const override { return IsWindow(h_) != FALSE; }
  Vec2 size() const override { return size_; }
  Vec2 anchor() const override { return anchor_; }
  void moveAnchorTo(Vec2 p) override {
    int x = (int)std::lround(p.x - anchor_.x), y = (int)std::lround(p.y - anchor_.y);
    if (x == lastX_ && y == lastY_) return;  // 안 움직였으면 호출도 안 함
    lastX_ = x;
    lastY_ = y;
    SetWindowPos(h_, nullptr, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
  }
  void release() override {
    // 끌고 오는 동안만 '항상 위'. 내려놓으면 보통 창이 된다.
    if (alive())
      SetWindowPos(h_, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
  }

 private:
  HWND h_;
  Vec2 size_, anchor_;
  int lastX_ = INT_MIN, lastY_ = INT_MIN;
};

// ---- 파일 헬퍼 ----

std::vector<std::wstring> listFiles(const std::wstring& dir, std::initializer_list<const wchar_t*> exts) {
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
    for (const wchar_t* e : exts)
      if (ext == e) out.push_back(dir + L"\\" + name);
  } while (FindNextFileW(h, &fd));
  FindClose(h);
  return out;
}

bool readAll(const std::wstring& path, std::string& out) {
  HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
  if (h == INVALID_HANDLE_VALUE) return false;
  LARGE_INTEGER sz;
  bool ok = GetFileSizeEx(h, &sz) && sz.QuadPart < (1 << 20);
  if (ok) {
    out.resize((size_t)sz.QuadPart);
    DWORD got = 0;
    ok = out.empty() || (ReadFile(h, &out[0], (DWORD)out.size(), &got, nullptr) && got == out.size());
  }
  CloseHandle(h);
  return ok;
}

// UTF-8(BOM 유무) / UTF-16LE(BOM) / 시스템 코드페이지(CP949 등) 순서로 해석
std::wstring decodeText(const std::string& b) {
  if (b.size() >= 2 && (unsigned char)b[0] == 0xFF && (unsigned char)b[1] == 0xFE)
    return std::wstring((const wchar_t*)(b.data() + 2), (b.size() - 2) / 2);
  size_t skip = (b.size() >= 3 && (unsigned char)b[0] == 0xEF && (unsigned char)b[1] == 0xBB &&
                 (unsigned char)b[2] == 0xBF)
                    ? 3
                    : 0;
  const char* p = b.data() + skip;
  int n = (int)(b.size() - skip);
  if (n <= 0) return L"";
  UINT cp = CP_UTF8;
  int len = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, p, n, nullptr, 0);
  if (len <= 0) {
    cp = CP_ACP;
    len = MultiByteToWideChar(CP_ACP, 0, p, n, nullptr, 0);
  }
  std::wstring w((size_t)std::max(0, len), L'\0');
  if (len > 0) MultiByteToWideChar(cp, 0, p, n, &w[0], len);
  return w;
}

std::wstring toCRLF(const std::wstring& s) {
  std::wstring o;
  o.reserve(s.size() + 16);
  for (size_t i = 0; i < s.size(); ++i) {
    if (s[i] == L'\r') continue;
    if (s[i] == L'\n') o += L'\r';
    o += s[i];
  }
  return o;
}

HBITMAP createDib(int w, int h, uint32_t** bits) {
  BITMAPINFO bi = {};
  bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
  bi.bmiHeader.biWidth = w;
  bi.bmiHeader.biHeight = -h;  // top-down
  bi.bmiHeader.biPlanes = 1;
  bi.bmiHeader.biBitCount = 32;
  bi.bmiHeader.biCompression = BI_RGB;
  void* p = nullptr;
  HBITMAP b = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &p, nullptr, 0);
  *bits = static_cast<uint32_t*>(p);
  return b;
}

template <class T>
void safeRelease(T*& p) {
  if (p) {
    p->Release();
    p = nullptr;
  }
}

// WIC 로 png/jpg/gif/bmp 를 읽어서 maxW×maxH 안에 맞게 줄인 비트맵 (흰 바탕에 합성)
HBITMAP loadImageFile(const std::wstring& path, int maxW, int maxH, int minSide, int& ow, int& oh) {
  IWICImagingFactory* fac = nullptr;
  IWICBitmapDecoder* dec = nullptr;
  IWICBitmapFrameDecode* frame = nullptr;
  IWICBitmapScaler* sc = nullptr;
  IWICFormatConverter* conv = nullptr;
  HBITMAP result = nullptr;
  do {
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_IWICImagingFactory,
                                (void**)&fac)))
      break;
    if (FAILED(fac->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand,
                                              &dec)))
      break;
    if (FAILED(dec->GetFrame(0, &frame))) break;
    UINT w = 0, h = 0;
    if (FAILED(frame->GetSize(&w, &h)) || !w || !h) break;
    float s = std::min({1.f, (float)maxW / w, (float)maxH / h});
    if (std::max(w, h) * s < minSide) s = std::min({(float)maxW / w, (float)maxH / h, (float)minSide / std::max(w, h)});
    UINT tw = std::max(1u, (UINT)std::lround(w * s)), th = std::max(1u, (UINT)std::lround(h * s));
    if (FAILED(fac->CreateBitmapScaler(&sc))) break;
    if (FAILED(sc->Initialize(frame, tw, th, WICBitmapInterpolationModeFant))) break;
    if (FAILED(fac->CreateFormatConverter(&conv))) break;
    if (FAILED(conv->Initialize(sc, GUID_WICPixelFormat32bppPBGRA, WICBitmapDitherTypeNone, nullptr, 0.0,
                                WICBitmapPaletteTypeCustom)))
      break;
    uint32_t* bits = nullptr;
    HBITMAP bmp = createDib((int)tw, (int)th, &bits);
    if (!bmp) break;
    if (FAILED(conv->CopyPixels(nullptr, tw * 4, tw * th * 4, (BYTE*)bits))) {
      DeleteObject(bmp);
      break;
    }
    for (size_t i = 0; i < (size_t)tw * th; ++i) {  // premultiplied → 흰 바탕 위 합성
      uint32_t v = bits[i], inv = 255u - (v >> 24);
      bits[i] = 0xFF000000u | (std::min(255u, ((v >> 16) & 255u) + inv) << 16) |
                (std::min(255u, ((v >> 8) & 255u) + inv) << 8) | std::min(255u, (v & 255u) + inv);
    }
    result = bmp;
    ow = (int)tw;
    oh = (int)th;
  } while (false);
  safeRelease(conv);
  safeRelease(sc);
  safeRelease(frame);
  safeRelease(dec);
  safeRelease(fac);
  return result;
}

// 그림 파일이 없을 때: 까치 자화상 + "까악!"
HBITMAP renderPortrait(const MagpiePalette& pal, float s, int& ow, int& oh) {
  int w = (int)std::lround(240 * s), h = (int)std::lround(200 * s);
  uint32_t* bits = nullptr;
  HBITMAP bmp = createDib(w, h, &bits);
  if (!bmp) return nullptr;
  std::fill(bits, bits + (size_t)w * h, 0xFFF3F6FAu);
  Canvas c;
  c.px = bits;
  c.w = w;
  c.h = h;
  c.stride = w;
  float u = 2.2f * s;
  MagpiePose p = makeStandingPose(Vec2{w * 0.5f, h * 0.62f}, u, -0.35f);
  p.beakOpen = 1.f;
  p.headThrust = 1.5f;
  p.tailLift = 4.f;
  drawMagpie(c, p, pal);

  HDC dc = CreateCompatibleDC(nullptr);
  HGDIOBJ oldBmp = SelectObject(dc, bmp);
  HFONT f = CreateFontW(-(int)std::lround(24 * s), 0, 0, 0, FW_BOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                        OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Malgun Gothic");
  HGDIOBJ oldFont = SelectObject(dc, f);
  SetBkMode(dc, TRANSPARENT);
  SetTextColor(dc, RGB(40, 40, 48));
  RECT rc{0, (LONG)std::lround(10 * s), w, (LONG)std::lround(44 * s)};
  DrawTextW(dc, L"까악!", -1, &rc, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
  SelectObject(dc, oldFont);
  DeleteObject(f);
  SelectObject(dc, oldBmp);
  DeleteDC(dc);
  ow = w;
  oh = h;
  return bmp;
}

const wchar_t* const kBuiltinNotes[] = {
    L"까악!\r\n반짝이는 거 있으면 나 줘.",
    L"이 커서 이제 내 거다.",
    L"오늘 할 일\r\n1. 돌아다니기\r\n2. 마우스 훔치기\r\n3. 또 돌아다니기",
    L"까치가 울면 반가운 손님이 온대요.\r\n그 손님이 바로 저예요.",
    L"창 정리는 제가 해드릴게요.\r\n방식은 제 마음대로.",
    L"ESC 를 꾹 누르면 집에 갈게요.\r\n...아마도.",
    L"바닥에 발자국 남긴 거 저 아니에요.",
};

BOOL CALLBACK monitorEnum(HMONITOR hm, HDC, LPRECT, LPARAM lp) {
  auto* v = reinterpret_cast<std::vector<MONITORINFO>*>(lp);
  MONITORINFO mi = {};
  mi.cbSize = sizeof(mi);
  if (GetMonitorInfoW(hm, &mi)) v->push_back(mi);
  return TRUE;
}

RectF toRectF(const RECT& r) { return {(float)r.left, (float)r.top, (float)r.right, (float)r.bottom}; }

}  // namespace

WinPlatform::WinPlatform(HINSTANCE inst, const std::wstring& baseDir, Audio& audio, const MagpiePalette& pal,
                         float uiScale, HICON bigIcon, HICON smallIcon, uint32_t seed)
    : inst_(inst), baseDir_(baseDir), audio_(audio), pal_(pal), ui_(uiScale), rng_(seed) {
  WNDCLASSEXW wc = {};
  wc.cbSize = sizeof(wc);
  wc.hInstance = inst;
  wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
  wc.hIcon = bigIcon;
  wc.hIconSm = smallIcon;
  wc.hbrBackground = (HBRUSH)GetStockObject(WHITE_BRUSH);
  wc.lpfnWndProc = ImageProc;
  wc.lpszClassName = kImageClass;
  RegisterClassExW(&wc);
  wc.lpfnWndProc = NoteProc;
  wc.lpszClassName = kNoteClass;
  RegisterClassExW(&wc);
}

Vec2 WinPlatform::cursorPos() {
  POINT p;
  if (!GetCursorPos(&p)) return {0.f, 0.f};
  return {(float)p.x, (float)p.y};
}

void WinPlatform::setCursorPos(Vec2 p) { SetCursorPos((int)std::lround(p.x), (int)std::lround(p.y)); }

void WinPlatform::playSound(SoundId id) { audio_.play(id); }

void WinPlatform::raiseOverlays() {
  for (HWND w : overlays_)
    SetWindowPos(w, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER);
}

void WinPlatform::queryScreens(std::vector<RectF>& work, std::vector<RectF>& monitors) {
  std::vector<MONITORINFO> v;
  EnumDisplayMonitors(nullptr, nullptr, monitorEnum, (LPARAM)&v);
  std::stable_partition(v.begin(), v.end(), [](const MONITORINFO& m) { return (m.dwFlags & MONITORINFOF_PRIMARY) != 0; });
  work.clear();
  monitors.clear();
  for (const auto& m : v) {
    work.push_back(toRectF(m.rcWork));
    monitors.push_back(toRectF(m.rcMonitor));
  }
}

HBITMAP WinPlatform::pickImage(int& w, int& h) {
  // 매번 다시 훑는다 → 실행 중에 그림을 넣어도 바로 반영
  auto files = listFiles(baseDir_ + L"\\Assets\\Images", {L".png", L".jpg", L".jpeg", L".gif", L".bmp"});
  std::shuffle(files.begin(), files.end(), rng_);
  for (const auto& f : files) {
    HBITMAP b = loadImageFile(f, (int)(420 * ui_), (int)(360 * ui_), (int)(160 * ui_), w, h);
    if (b) return b;
  }
  return renderPortrait(pal_, ui_, w, h);
}

std::wstring WinPlatform::pickNote() {
  auto files = listFiles(baseDir_ + L"\\Assets\\Notes", {L".txt"});
  if (!files.empty()) {
    std::string bytes;
    if (readAll(files[rng_() % files.size()], bytes)) {
      std::wstring t = toCRLF(decodeText(bytes));
      if (!t.empty()) return t;
    }
  }
  return kBuiltinNotes[rng_() % (sizeof(kBuiltinNotes) / sizeof(kBuiltinNotes[0]))];
}

std::unique_ptr<ICarriedWindow> WinPlatform::spawnWindow(CarryKind kind, Vec2 at) {
  const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU;
  const DWORD ex = WS_EX_TOPMOST;
  int cw = 0, ch = 0;
  const wchar_t* cls;
  const wchar_t* title;
  void* data;
  if (kind == CarryKind::Image) {
    auto* d = new ImageWinData();
    d->bmp = pickImage(d->w, d->h);
    if (!d->bmp) {
      delete d;
      return nullptr;
    }
    cw = d->w;
    ch = d->h;
    cls = kImageClass;
    title = L"까치가 물어 온 사진";
    data = d;
  } else {
    auto* d = new NoteWinData();
    d->text = pickNote();
    d->font = CreateFontW(-(int)std::lround(16 * ui_), 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                          OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Malgun Gothic");
    cw = (int)std::lround(340 * ui_);
    ch = (int)std::lround(200 * ui_);
    cls = kNoteClass;
    title = L"까치의 메모";
    data = d;
  }

  RECT rc{0, 0, cw, ch};
  AdjustWindowRectEx(&rc, style, FALSE, ex);
  const int ww = rc.right - rc.left, wh = rc.bottom - rc.top;
  const Vec2 anchor{ww * 0.5f, std::max(4.f, -rc.top * 0.55f)};  // 제목 표시줄 가운데를 문다
  const int x = (int)std::lround(at.x - anchor.x), y = (int)std::lround(at.y - anchor.y);
  HWND h = CreateWindowExW(ex, cls, title, style, x, y, ww, wh, nullptr, nullptr, inst_, data);
  if (!h) return nullptr;  // WM_NCDESTROY 가 data 를 정리함
  ShowWindow(h, SW_SHOWNOACTIVATE);
  UpdateWindow(h);
  raiseOverlays();
  return std::make_unique<WinCarried>(h, Vec2{(float)ww, (float)wh}, anchor);
}
