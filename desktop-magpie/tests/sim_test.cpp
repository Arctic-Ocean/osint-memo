// 리눅스에서 돌리는 검증 도구 (Windows 없이 까치의 두뇌/그림/소리를 확인).
//   sim_test sim  <outdir>        가짜 바탕화면에서 240초 시뮬레이션 + 검사 + 프레임 덤프
//   sim_test art  <out.ppm>       여러 방향의 까치 그림 시트
//   sim_test icon <out.rgba> <n>  n×n 아이콘 (RGBA, straight alpha)
//   sim_test wav  <outdir>        합성 소리를 WAV 로 저장
//   sim_test defcfg               기본 config.ini 내용 출력
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "../src/config.h"
#include "../src/magpie_draw.h"
#include "../src/pet.h"
#include "../src/raster.h"
#include "../src/synth.h"

namespace {

int g_fail = 0;
#define CHECK(cond, ...)                         \
  do {                                           \
    if (!(cond)) {                               \
      std::printf("FAIL: " __VA_ARGS__);         \
      std::printf("\n");                         \
      ++g_fail;                                  \
    }                                            \
  } while (0)

struct FakeWinState {
  CarryKind kind;
  Vec2 tl, size, anchor;
  bool carried = true;
  bool alive = true;
};

class FakeWin : public ICarriedWindow {
 public:
  explicit FakeWin(std::shared_ptr<FakeWinState> s) : s_(std::move(s)) {}
  bool alive() const override { return s_->alive; }
  Vec2 size() const override { return s_->size; }
  Vec2 anchor() const override { return s_->anchor; }
  void moveAnchorTo(Vec2 p) override { s_->tl = p - s_->anchor; }
  void release() override { s_->carried = false; }

 private:
  std::shared_ptr<FakeWinState> s_;
};

class FakePlatform : public IPlatform {
 public:
  Vec2 cursor{960.f, 540.f};
  int setCursorCount = 0;
  int sounds[kSoundKinds] = {};
  std::vector<std::shared_ptr<FakeWinState>> wins;

  Vec2 cursorPos() override { return cursor; }
  void setCursorPos(Vec2 p) override {
    cursor = p;
    ++setCursorCount;
  }
  std::unique_ptr<ICarriedWindow> spawnWindow(CarryKind kind, Vec2 at) override {
    auto s = std::make_shared<FakeWinState>();
    s->kind = kind;
    s->size = kind == CarryKind::Image ? Vec2{380.f, 330.f} : Vec2{350.f, 250.f};
    s->anchor = {s->size.x * 0.5f, 16.f};
    s->tl = at - s->anchor;
    wins.push_back(s);
    return std::make_unique<FakeWin>(s);
  }
  void playSound(SoundId id) override { ++sounds[(int)id]; }
};

void writePPM(const std::string& path, const uint32_t* px, int stride, int x0, int y0, int w, int h) {
  FILE* f = std::fopen(path.c_str(), "wb");
  if (!f) return;
  std::fprintf(f, "P6\n%d %d\n255\n", w, h);
  std::vector<unsigned char> row((size_t)w * 3);
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      uint32_t v = px[(size_t)(y0 + y) * stride + (x0 + x)];
      row[x * 3 + 0] = (unsigned char)(v >> 16);
      row[x * 3 + 1] = (unsigned char)(v >> 8);
      row[x * 3 + 2] = (unsigned char)v;
    }
    std::fwrite(row.data(), 1, row.size(), f);
  }
  std::fclose(f);
}

void renderScene(Canvas& c, const Pet& pet, const FakePlatform& plat, const MagpiePalette& pal) {
  c.clear(0xFF3A6EA5u);
  c.fill(Box({960.f, 1060.f}, {960.f, 20.f}, 0.f), rgba(28, 28, 34));
  for (const auto& w : plat.wins) {
    if (!w->alive) continue;
    Vec2 ctr = w->tl + w->size * 0.5f;
    c.fill(Box(ctr, w->size * 0.5f, 6.f), rgba(60, 60, 70), 1.f);
    c.fill(Box(ctr, w->size * 0.5f, 6.f), rgba(250, 250, 250));
    c.fill(Box(w->tl + Vec2{w->size.x * 0.5f, 16.f}, {w->size.x * 0.5f, 16.f}, 6.f), rgba(225, 228, 235));
    RGBAf body = w->kind == CarryKind::Image ? rgba(170, 200, 230) : rgba(255, 255, 255);
    c.fill(Box(ctr + Vec2{0.f, 16.f}, w->size * 0.5f - Vec2{10.f, 26.f}, 2.f), body);
  }
  for (const auto& fp : pet.footprints())
    drawFootprint(c, fp.pos, fp.angle, pet.unit(), withAlpha(pal.mud, pal.mud.a * footprintAlpha(fp.age)));
  drawMagpie(c, pet.pose(), pal);
  Vec2 k = plat.cursor;
  Triangle arrow(k, k + Vec2{0.f, 19.f}, k + Vec2{13.f, 13.f});
  c.fill(arrow, rgba(0, 0, 0), 1.2f);
  c.fill(arrow, rgba(255, 255, 255));
}

// 다리 길이 = 엉덩이 바로 아래 바닥 지점 ~ 발 (단위). 거미 다리 검사용.
float legLength(const MagpiePose& p, int i) {
  Vec2 f = fromAngle(p.heading), r{-f.y, f.x};
  float side = i == 0 ? -1.f : 1.f;
  Vec2 hip = p.pos + r * (side * 4.2f * p.unit) + f * (0.5f * p.unit);
  return length(p.feet[i] - hip) / p.unit;
}

// 사용자가 화난 까치 주위로 커서를 빙빙 돌리는 상황 + 창을 내려놓는 상황
void runStress(const Config& baseCfg, float unit, const RectF& work, const RectF& mon) {
  const float dt = 1.f / 60.f;
  float worstLeg = 0.f;
  for (int trial = 0; trial < 6; ++trial) {
    FakePlatform plat;
    Pet pet(baseCfg, plat, unit, 900u + trial);
    pet.setScreens({work}, {mon});
    for (int i = 0; i < 120; ++i) pet.update(dt);
    pet.poke();
    float ang = 0.f;
    const float radius = (50.f + 25.f * trial) * unit, spin = 3.f + 1.5f * trial;  // 반지름, 돌리는 속도(rad/s)
    for (int i = 0; i < 60 * 8; ++i) {
      if (!pet.holdingMouse()) {
        ang += spin * dt;
        plat.cursor = pet.position() + Vec2{std::cos(ang), std::sin(ang)} * radius;
      }
      pet.update(dt);
      MagpiePose p = pet.pose();
      worstLeg = std::max({worstLeg, legLength(p, 0), legLength(p, 1)});
    }
  }
  std::printf("stress: longest leg while circling cursor = %.1f units (normal ~0-6)\n", worstLeg);
  CHECK(worstLeg < 9.f, "spider legs: leg stretched to %.1f units", worstLeg);

  float worstSpin = 0.f, worstDropTime = 0.f;
  for (int trial = 0; trial < 12; ++trial) {
    FakePlatform plat;
    Pet pet(baseCfg, plat, unit, 500u + trial);
    pet.setScreens({work}, {mon});
    for (int i = 0; i < 60; ++i) pet.update(dt);
    pet.forceTask(trial % 2 ? TaskId::CollectImage : TaskId::CollectNote);
    float spin = 0.f, carryTime = 0.f, prevH = pet.pose().heading;
    for (int i = 0; i < 60 * 60 && pet.task() != TaskId::Wander; ++i) {
      pet.update(dt);
      float h = pet.pose().heading;
      if (pet.carrying()) {
        spin += std::fabs(wrapAngle(h - prevH));
        carryTime += dt;
      }
      prevH = h;
    }
    worstSpin = std::max(worstSpin, spin);
    worstDropTime = std::max(worstDropTime, carryTime);
  }
  std::printf("stress: while dragging a window, most turning = %.1f rad (%.2f turns), longest drag %.1fs\n",
              worstSpin, worstSpin / (2.f * kPi), worstDropTime);
  CHECK(worstSpin < kPi, "magpie spins while dropping a window (%.1f rad)", worstSpin);
  CHECK(worstDropTime < 25.f, "window drag took too long (%.1fs) - probably circling", worstDropTime);
}

int runSim(const std::string& outdir) {
  Config cfg;
  cfg.minWander = 3.f;
  cfg.maxWander = 5.f;
  cfg.firstWander = 4.f;
  const float unit = 1.15f;
  FakePlatform plat;
  Pet pet(cfg, plat, unit, 12345u);
  const RectF work{0.f, 0.f, 1920.f, 1040.f}, mon{0.f, 0.f, 1920.f, 1080.f};
  pet.setScreens({work}, {mon});
  const MagpiePalette pal = makePalette(cfg);

  std::vector<uint32_t> buf(1920 * 1080);
  Canvas c;
  c.px = buf.data();
  c.w = 1920;
  c.h = 1080;
  c.stride = 1920;

  const float dt = 1.f / 60.f;
  const int frames = 240 * 60;
  std::map<TaskId, int> taskCount;
  std::map<TaskId, float> firstStart;
  TaskId prev = pet.task();
  firstStart[prev] = 0.f;
  int grabs = 0, maxPrints = 0;
  bool prevHold = false;
  Vec2 userTarget{700.f, 400.f};
  float userPause = 0.f;
  unsigned userRng = 7u;
  auto urand = [&]() {
    userRng = userRng * 1103515245u + 12345u;
    return (float)((userRng >> 8) & 0xFFFF) / 65535.f;
  };
  int dumped = 0;
  float minX = 1e9f, maxX = -1e9f, minY = 1e9f, maxY = -1e9f;
  float worstFootSlip = 0.f;
  float maxWalkSpeed = 0.f, maxChaseSpeed = 0.f, sinceChase = 10.f;
  Vec2 prevPos = pet.position();

  for (int i = 0; i < frames; ++i) {
    float t = i * dt;
    // 가짜 사용자: 커서를 이리저리 움직임 (까치가 물고 있을 때도 버팀)
    if (userPause > 0.f) {
      userPause -= dt;
    } else {
      Vec2 d = userTarget - plat.cursor;
      float l = length(d);
      if (l < 5.f) {
        userTarget = {100.f + urand() * 1700.f, 100.f + urand() * 850.f};
        userPause = urand() * 2.f;
      } else {
        plat.cursor += d * (std::min(l, 450.f * dt) / l);
      }
    }

    MagpiePose before = pet.pose();
    pet.update(dt);
    MagpiePose after = pet.pose();
    // 땅에 붙은 발(들리지 않은 발)은 미끄러지면 안 됨
    for (int f = 0; f < 2; ++f)
      if (before.footLift[f] == 0.f && after.footLift[f] == 0.f)
        worstFootSlip = std::max(worstFootSlip, length(after.feet[f] - before.feet[f]));

    Vec2 p = pet.position();
    CHECK(std::isfinite(p.x) && std::isfinite(p.y), "NaN position at t=%.2f", t);
    {
      float sp = length(p - prevPos) / dt;
      if (pet.task() == TaskId::NabMouse) {
        if (!pet.holdingMouse()) maxChaseSpeed = std::max(maxChaseSpeed, sp);
      } else if (t > 2.f) {  // 추격 직후 감속 구간은 제외
        if (sinceChase > 1.f) maxWalkSpeed = std::max(maxWalkSpeed, sp);
      }
      sinceChase = pet.task() == TaskId::NabMouse ? 0.f : sinceChase + dt;
      prevPos = p;
    }
    minX = std::min(minX, p.x);
    maxX = std::max(maxX, p.x);
    minY = std::min(minY, p.y);
    maxY = std::max(maxY, p.y);
    maxPrints = std::max(maxPrints, (int)pet.footprints().size());

    if (pet.holdingMouse() && !prevHold) {
      ++grabs;
      std::printf("[%6.2f] grabbed mouse at (%.0f,%.0f)\n", t, plat.cursor.x, plat.cursor.y);
    }
    if (pet.holdingMouse()) {
      Vec2 tip = magpieBeakTip(pet.pose());
      CHECK(length(tip - plat.cursor) < 0.01f, "cursor not at beak while held (t=%.2f)", t);
    }
    prevHold = pet.holdingMouse();

    TaskId cur = pet.task();
    if (cur != prev) {
      std::printf("[%6.2f] %s -> %s  pos=(%.0f,%.0f)\n", t, taskName(prev), taskName(cur), p.x, p.y);
      ++taskCount[cur];
      if (!firstStart.count(cur)) firstStart[cur] = t;
      prev = cur;
    }

    // 각 작업이 처음 나왔을 때 처음 9초를 6fps 로 덤프
    if (!outdir.empty() && i % 10 == 0) {
      for (auto& kv : firstStart) {
        if (kv.first == TaskId::Wander) continue;
        float since = t - kv.second;
        if (since >= 0.f && since < 9.f) {
          renderScene(c, pet, plat, pal);
          int cw = 760, ch = 480;
          int x0 = (int)std::lround(p.x) - cw / 2, y0 = (int)std::lround(p.y) - ch / 2 - 20;
          x0 = std::max(0, std::min(1920 - cw, x0));
          y0 = std::max(0, std::min(1080 - ch, y0));
          char name[256];
          std::snprintf(name, sizeof name, "%s/%s_%03d.ppm", outdir.c_str(), taskName(kv.first), (int)(since * 6.f + 0.5f));
          writePPM(name, buf.data(), 1920, x0, y0, cw, ch);
          ++dumped;
        }
      }
    }
  }

  // 전체 화면 한 장 (마지막 상태)
  if (!outdir.empty()) {
    renderScene(c, pet, plat, pal);
    writePPM(outdir + "/overview.ppm", buf.data(), 1920, 0, 0, 1920, 1080);
  }

  std::printf("\n--- summary ---\n");
  for (auto& kv : taskCount) std::printf("%-13s x%d\n", taskName(kv.first), kv.second);
  std::printf("grabs=%d setCursor=%d windows=%zu maxFootprints=%d frames=%d\nsounds:", grabs, plat.setCursorCount,
              plat.wins.size(), maxPrints, dumped);
  for (int k = 0; k < kSoundKinds; ++k) std::printf(" %s=%d", soundName((SoundId)k), plat.sounds[k]);
  std::printf("\n");
  std::printf("pos range x[%.0f..%.0f] y[%.0f..%.0f]  worst planted-foot slip=%.3fpx\n", minX, maxX, minY, maxY,
              worstFootSlip);

  CHECK(taskCount[TaskId::NabMouse] > 0, "NabMouse never ran");
  CHECK(taskCount[TaskId::CollectImage] > 0, "CollectImage never ran");
  CHECK(taskCount[TaskId::CollectNote] > 0, "CollectNote never ran");
  CHECK(taskCount[TaskId::TrackMud] > 0, "TrackMud never ran");
  CHECK(grabs > 0, "never grabbed the mouse");
  CHECK(maxPrints > 10, "too few footprints (%d)", maxPrints);
  for (int k = 0; k < kSoundKinds; ++k) CHECK(plat.sounds[k] > 0, "sound %s never played", soundName((SoundId)k));
  CHECK(plat.sounds[(int)SoundId::Call] > 5 && plat.sounds[(int)SoundId::Step] > 20, "sounds not played enough");

  // 클릭하면 쫓아오기: 어슬렁거릴 때 찌르면 바로 NabMouse
  {
    FakePlatform p2;
    Pet pk(cfg, p2, unit, 777u);
    pk.setScreens({work}, {mon});
    for (int i = 0; i < 120; ++i) pk.update(dt);
    CHECK(pk.task() == TaskId::Wander, "expected Wander before poke");
    CHECK(pk.poke(), "poke did not start a chase");
    CHECK(pk.task() == TaskId::NabMouse, "poke did not switch to NabMouse");
    CHECK(!pk.poke(), "second poke during chase should be ignored");
    bool grabbed = false;
    for (int i = 0; i < 60 * 12 && pk.task() == TaskId::NabMouse; ++i) {
      pk.update(dt);
      grabbed |= pk.holdingMouse();
    }
    CHECK(grabbed, "poke chase never grabbed the cursor");
  }

  // 속도: 평소 걷기는 느리게, 추격만 빠르게
  CHECK(maxWalkSpeed < 75.f * unit, "walking too fast: %.0f px/s", maxWalkSpeed);
  CHECK(maxChaseSpeed > 200.f * unit, "chase too slow: %.0f px/s", maxChaseSpeed);
  CHECK(worstFootSlip < 0.001f, "planted foot slid %.3fpx", worstFootSlip);
  CHECK(minX > -400.f && maxX < 2320.f && minY > -400.f && maxY < 1480.f, "wandered too far off screen");
  int dropped = 0;
  for (const auto& w : plat.wins) {
    if (w->carried) continue;
    ++dropped;
    bool inside = w->tl.x >= work.x0 && w->tl.y >= work.y0 && w->tl.x + w->size.x <= work.x1 &&
                  w->tl.y + w->size.y <= work.y1;
    CHECK(inside, "window dropped partly off-screen at (%.0f,%.0f)", w->tl.x, w->tl.y);
  }
  CHECK(dropped > 1, "no windows were dropped on screen");
  runStress(cfg, unit, work, mon);
  std::printf(g_fail ? "\n%d CHECK(S) FAILED\n" : "\nALL CHECKS PASSED\n", g_fail);
  return g_fail ? 1 : 0;
}

int runArt(const std::string& path) {
  const int W = 1200, H = 620;
  std::vector<uint32_t> buf(W * H);
  for (int y = 0; y < H; ++y)
    for (int x = 0; x < W; ++x) buf[y * W + x] = y < 310 ? 0xFFE8ECF0u : 0xFF2B3440u;
  Canvas c;
  c.px = buf.data();
  c.w = W;
  c.h = H;
  c.stride = W;
  Config cfg;
  MagpiePalette pal = makePalette(cfg);
  for (int row = 0; row < 2; ++row)
    for (int i = 0; i < 6; ++i) {
      float h = -kPi + i * (2 * kPi / 6) + 0.3f;
      MagpiePose p = makeStandingPose(Vec2{100.f + i * 200.f, 150.f + row * 310.f}, 2.6f, h);
      if (i == 2) p.beakOpen = 1.f;
      if (i == 4) {
        p.tailLift = 5.f;
        p.headTurn = 0.5f;
      }
      drawMagpie(c, p, pal);
      if (row == 0) drawFootprint(c, Vec2{100.f + i * 200.f, 290.f}, h, 2.6f, pal.mud);
    }
  writePPM(path, buf.data(), W, 0, 0, W, H);
  return 0;
}

int runIcon(const std::string& path, int n) {
  std::vector<uint32_t> buf((size_t)n * n, 0u);
  Canvas c;
  c.px = buf.data();
  c.w = n;
  c.h = n;
  c.stride = n;
  Config cfg;
  MagpiePalette pal = makePalette(cfg);
  pal.shadow.a = 0.f;
  float u = n / 64.f;
  MagpiePose p = makeStandingPose(Vec2{n * 0.47f, n * 0.5f}, u, -0.75f);
  p.tailSway = 0.15f;
  drawMagpie(c, p, pal);
  FILE* f = std::fopen(path.c_str(), "wb");
  if (!f) return 1;
  for (uint32_t v : buf) {
    unsigned a = v >> 24;
    auto un = [a](unsigned ch) { return (unsigned char)(a ? std::min(255u, (ch * 255u + a / 2) / a) : 0u); };
    unsigned char px[4] = {un((v >> 16) & 255), un((v >> 8) & 255), un(v & 255), (unsigned char)a};
    std::fwrite(px, 1, 4, f);
  }
  std::fclose(f);
  return 0;
}

void writeWav(const std::string& path, const std::vector<float>& s) {
  FILE* f = std::fopen(path.c_str(), "wb");
  if (!f) return;
  uint32_t dataBytes = (uint32_t)s.size() * 2, riff = 36 + dataBytes, fmtLen = 16, rate = kSynthRate,
           byteRate = kSynthRate * 2;
  uint16_t pcm = 1, ch = 1, align = 2, bits = 16;
  std::fwrite("RIFF", 1, 4, f);
  std::fwrite(&riff, 4, 1, f);
  std::fwrite("WAVEfmt ", 1, 8, f);
  std::fwrite(&fmtLen, 4, 1, f);
  std::fwrite(&pcm, 2, 1, f);
  std::fwrite(&ch, 2, 1, f);
  std::fwrite(&rate, 4, 1, f);
  std::fwrite(&byteRate, 4, 1, f);
  std::fwrite(&align, 2, 1, f);
  std::fwrite(&bits, 2, 1, f);
  std::fwrite("data", 1, 4, f);
  std::fwrite(&dataBytes, 4, 1, f);
  for (float v : s) {
    int16_t x = (int16_t)std::lround(clampf(v, -1.f, 1.f) * 32000.f);
    std::fwrite(&x, 2, 1, f);
  }
  std::fclose(f);
}

int runWav(const std::string& dir) {
  for (int k = 0; k < kSoundKinds; ++k) {
    SoundId id = (SoundId)k;
    for (int v = 0; v < soundVariants(id); ++v) {
      std::vector<float> s = synthSound(id, v);
      float peak = 0.f, rms = 0.f;
      for (float x : s) {
        peak = std::max(peak, std::fabs(x));
        rms += x * x;
      }
      rms = s.empty() ? 0.f : std::sqrt(rms / s.size());
      std::printf("%-8s %d  %.3fs peak=%.2f rms=%.3f\n", soundName(id), v + 1, (float)s.size() / kSynthRate, peak, rms);
      CHECK(!s.empty() && peak > 0.3f && peak <= 1.f && rms > 0.01f, "%s%d level out of range", soundName(id), v + 1);
      if (!dir.empty()) writeWav(dir + "/" + soundName(id) + std::to_string(v + 1) + ".wav", s);
    }
  }
  return g_fail ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
  std::string mode = argc > 1 ? argv[1] : "sim";
  std::string out = argc > 2 ? argv[2] : "";
  if (mode == "sim") return runSim(out);
  if (mode == "art") return runArt(out);
  if (mode == "icon") return runIcon(out, argc > 3 ? std::atoi(argv[3]) : 256);
  if (mode == "wav") return runWav(out);
  if (mode == "defcfg") {
    std::fputs(defaultConfigText().c_str(), stdout);
    return 0;
  }
  std::printf("usage: sim_test sim|art|icon|wav <out>\n");
  return 2;
}
