#pragma once
// 진단 로그: exe 옆 DesktopMagpie.log (쓸 수 없으면 %TEMP%).
// "실행이 안 된다" 같은 문제를 원격으로 볼 수 있게 시작 과정과 오류를 남긴다.
#include <string>

namespace mlog {
bool open(const std::wstring& preferred, const std::wstring& fallback);
void line(const char* fmt, ...)
#if defined(__MINGW32__)
    __attribute__((format(gnu_printf, 1, 2)))  // 표준 printf 형식(%zu, %lld 등)으로 검사
#elif defined(__GNUC__)
    __attribute__((format(printf, 1, 2)))
#endif
    ;
const std::wstring& path();
std::string utf8(const std::wstring& w);
double seconds();  // 프로그램 시작 후 경과 시간
void close();
}  // namespace mlog
