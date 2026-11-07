# Windows 에서 빌드 (Visual Studio 또는 MinGW):
#   cmake -S . -B build
#   cmake --build build --config Release
cmake_minimum_required(VERSION 3.16)
project(DesktopMagpie LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
  # VC++ 재배포 패키지 없이 돌도록 런타임을 정적으로
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

add_executable(DesktopMagpie WIN32
  src/config.cpp
  src/magpie_draw.cpp
  src/pet.cpp
  src/synth.cpp
  src/win/main.cpp
  src/win/win_platform.cpp
  src/win/audio_win.cpp
  src/win/log.cpp
  res/app.rc)

target_compile_definitions(DesktopMagpie PRIVATE UNICODE _UNICODE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(DesktopMagpie PRIVATE winmm ole32 windowscodecs uuid shell32 gdi32 user32)

if(MSVC)
  target_compile_options(DesktopMagpie PRIVATE /utf-8 /W3)
  target_link_options(DesktopMagpie PRIVATE /MANIFEST:NO)  # 매니페스트는 app.rc 에 들어 있음
elseif(MINGW)
  target_compile_options(DesktopMagpie PRIVATE -municode)
  target_link_options(DesktopMagpie PRIVATE -municode -static -s)
endif()

# 실행 파일 옆에 config.ini 와 Close_Magpie.bat 복사
add_custom_command(TARGET DesktopMagpie POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different ${CMAKE_SOURCE_DIR}/dist/config.ini $<TARGET_FILE_DIR:DesktopMagpie>
  COMMAND ${CMAKE_COMMAND} -E copy_if_different ${CMAKE_SOURCE_DIR}/dist/Close_Magpie.bat $<TARGET_FILE_DIR:DesktopMagpie>)
