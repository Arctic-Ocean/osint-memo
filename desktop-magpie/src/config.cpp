#include "config.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>

namespace {

std::string trim(const std::string& s) {
  size_t a = 0, b = s.size();
  while (a < b && std::isspace((unsigned char)s[a])) ++a;
  while (b > a && std::isspace((unsigned char)s[b - 1])) --b;
  return s.substr(a, b - a);
}

std::string lower(std::string s) {
  for (char& c : s) c = (char)std::tolower((unsigned char)c);
  return s;
}

bool parseBool(const std::string& v, bool def) {
  std::string l = lower(v);
  if (l == "true" || l == "1" || l == "yes" || l == "on") return true;
  if (l == "false" || l == "0" || l == "no" || l == "off") return false;
  return def;
}

float parseFloat(const std::string& v, float def) {
  char* end = nullptr;
  float f = std::strtof(v.c_str(), &end);
  return end != v.c_str() ? f : def;
}

void parseColor(const std::string& v, Color& c) {
  std::string s = v;
  if (!s.empty() && s[0] == '#') s = s.substr(1);
  if (s.size() != 6) return;
  char* end = nullptr;
  unsigned long x = std::strtoul(s.c_str(), &end, 16);
  if (end == nullptr || *end != '\0') return;
  c = {(uint8_t)(x >> 16), (uint8_t)(x >> 8), (uint8_t)x};
}

}  // namespace

void parseConfig(const std::string& textIn, Config& cfg) {
  std::string text = textIn;
  if (text.size() >= 3 && (unsigned char)text[0] == 0xEF && (unsigned char)text[1] == 0xBB &&
      (unsigned char)text[2] == 0xBF)
    text = text.substr(3);

  size_t pos = 0;
  while (pos <= text.size()) {
    size_t nl = text.find('\n', pos);
    if (nl == std::string::npos) nl = text.size();
    std::string line = trim(text.substr(pos, nl - pos));
    pos = nl + 1;
    if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
    size_t eq = line.find('=');
    if (eq == std::string::npos) continue;
    std::string key = lower(trim(line.substr(0, eq)));
    std::string val = trim(line.substr(eq + 1));

    if (key == "silencesounds") cfg.silenceSounds = parseBool(val, cfg.silenceSounds);
    else if (key == "task_canattackmouse") cfg.canAttackMouse = parseBool(val, cfg.canAttackMouse);
    else if (key == "attackrandomly") cfg.attackRandomly = parseBool(val, cfg.attackRandomly);
    else if (key == "chasewhenclicked") cfg.chaseWhenClicked = parseBool(val, cfg.chaseWhenClicked);
    else if (key == "showlegs") cfg.showLegs = parseBool(val, cfg.showLegs);
    else if (key == "usecustomcolors") cfg.useCustomColors = parseBool(val, cfg.useCustomColors);
    else if (key == "magpieblack") parseColor(val, cfg.black);
    else if (key == "magpiewhite") parseColor(val, cfg.white);
    else if (key == "magpieblue") parseColor(val, cfg.blue);
    else if (key == "magpieoutline") parseColor(val, cfg.outline);
    else if (key == "minwanderingtimeseconds") cfg.minWander = parseFloat(val, cfg.minWander);
    else if (key == "maxwanderingtimeseconds") cfg.maxWander = parseFloat(val, cfg.maxWander);
    else if (key == "firstwandertimeseconds") cfg.firstWander = parseFloat(val, cfg.firstWander);
    else if (key == "scale") cfg.scale = parseFloat(val, cfg.scale);
    else if (key == "framerate") cfg.frameRate = (int)parseFloat(val, (float)cfg.frameRate);
    else if (key == "showtrayicon") cfg.showTrayIcon = parseBool(val, cfg.showTrayIcon);
  }

  // 말도 안 되는 값 방지
  cfg.minWander = std::max(1.f, cfg.minWander);
  cfg.maxWander = std::max(cfg.minWander, cfg.maxWander);
  cfg.firstWander = std::max(0.f, cfg.firstWander);
  cfg.scale = std::min(4.f, std::max(0.3f, cfg.scale));
  cfg.frameRate = std::min(240, std::max(15, cfg.frameRate));
}

std::string defaultConfigText() {
  return "Version_DoNotEdit=1\r\n"
         "SilenceSounds=False\r\n"
         "Task_CanAttackMouse=True\r\n"
         "AttackRandomly=False\r\n"
         "ChaseWhenClicked=True\r\n"
         "ShowLegs=False\r\n"
         "UseCustomColors=False\r\n"
         "MagpieBlack=#1d1d24\r\n"
         "MagpieWhite=#f4f4f0\r\n"
         "MagpieBlue=#2d5c9e\r\n"
         "MagpieOutline=#6e6e7a\r\n"
         "MinWanderingTimeSeconds=20\r\n"
         "MaxWanderingTimeSeconds=40\r\n"
         "FirstWanderTimeSeconds=20\r\n"
         "Scale=1.0\r\n"
         "FrameRate=60\r\n"
         "ShowTrayIcon=False\r\n";
}
