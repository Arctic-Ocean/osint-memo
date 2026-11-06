#pragma once
// config.ini 읽기. 형식은 원본처럼 섹션 없는 key=value.
#include <cstdint>
#include <string>

struct Color {
  uint8_t r = 0, g = 0, b = 0;
};

struct Config {
  bool silenceSounds = false;
  bool canAttackMouse = true;   // Task_CanAttackMouse: 마우스 훔치기 작업 사용 여부
  bool attackRandomly = false;  // AttackRandomly: 돌아다니다가도 아무 때나 마우스 습격
  bool useCustomColors = false;
  Color black{0x1d, 0x1d, 0x24};
  Color white{0xf4, 0xf4, 0xf0};
  Color blue{0x2d, 0x5c, 0x9e};
  Color outline{0x6e, 0x6e, 0x7a};
  float minWander = 20.f;    // MinWanderingTimeSeconds
  float maxWander = 40.f;    // MaxWanderingTimeSeconds
  float firstWander = 20.f;  // FirstWanderTimeSeconds
  float scale = 1.f;         // 까치 크기 배율
  int frameRate = 60;        // 초당 프레임 (낮추면 더 가벼워짐)
  bool showTrayIcon = false; // 트레이 아이콘 (우클릭 → 종료)
};

// text 를 읽어서 cfg 에 덮어쓴다. 모르는 키는 무시.
void parseConfig(const std::string& text, Config& cfg);
// config.ini 가 없을 때 만들어 줄 기본 내용 (CRLF)
std::string defaultConfigText();
