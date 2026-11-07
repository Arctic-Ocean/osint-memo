#include "log.h"

#include <windows.h>

#include <cstdarg>
#include <cstdio>

namespace mlog {
namespace {
FILE* g_file = nullptr;
std::wstring g_path;
LARGE_INTEGER g_start = {}, g_freq = {};

FILE* tryOpen(const std::wstring& p) {
  if (p.empty()) return nullptr;
  FILE* f = _wfopen(p.c_str(), L"wb");
  if (f) g_path = p;
  return f;
}
}  // namespace

double seconds() {
  if (!g_freq.QuadPart) {
    QueryPerformanceFrequency(&g_freq);
    QueryPerformanceCounter(&g_start);
  }
  LARGE_INTEGER t;
  QueryPerformanceCounter(&t);
  return (double)(t.QuadPart - g_start.QuadPart) / (double)g_freq.QuadPart;
}

bool open(const std::wstring& preferred, const std::wstring& fallback) {
  seconds();
  g_file = tryOpen(preferred);
  if (!g_file) g_file = tryOpen(fallback);
  if (g_file) std::fputs("\xEF\xBB\xBF", g_file);  // 메모장에서 한글이 깨지지 않게 BOM
  return g_file != nullptr;
}

void line(const char* fmt, ...) {
  if (!g_file) return;
  std::fprintf(g_file, "[%8.3f] ", seconds());
  va_list ap;
  va_start(ap, fmt);
#if defined(__MINGW32__)
  __mingw_vfprintf(g_file, fmt, ap);  // 옛 msvcrt printf 는 %zu 등을 몰라서 MinGW 표준 구현을 씀
#else
  std::vfprintf(g_file, fmt, ap);
#endif
  va_end(ap);
  std::fputs("\r\n", g_file);
  std::fflush(g_file);
}

const std::wstring& path() { return g_path; }

std::string utf8(const std::wstring& w) {
  if (w.empty()) return {};
  int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0, nullptr, nullptr);
  std::string s((size_t)(n > 0 ? n : 0), '\0');
  if (n > 0) WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
  return s;
}

void close() {
  if (g_file) std::fclose(g_file);
  g_file = nullptr;
}
}  // namespace mlog
