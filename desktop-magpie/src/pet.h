#pragma once
// 까치의 두뇌 + 몸 (OS 와 무관한 순수 로직).
// OS 와 닿는 부분(커서, 창, 소리)은 IPlatform 을 통해서만 한다 → 리눅스에서도 시뮬레이션 테스트 가능.
#include <cstdint>
#include <memory>
#include <random>
#include <vector>

#include "config.h"
#include "magpie_draw.h"
#include "sound_ids.h"
#include "vec2.h"

enum class CarryKind { Image, Note };
enum class TaskId { Wander, NabMouse, CollectImage, CollectNote, TrackMud };
const char* taskName(TaskId t);

// 까치가 물고 다니는 창
class ICarriedWindow {
 public:
  virtual ~ICarriedWindow() = default;
  virtual bool alive() const = 0;          // 사용자가 닫았으면 false
  virtual Vec2 size() const = 0;           // 창 전체 크기 (픽셀)
  virtual Vec2 anchor() const = 0;         // 무는 지점 (창 왼쪽 위 기준)
  virtual void moveAnchorTo(Vec2 p) = 0;   // 무는 지점이 p 에 오도록 창 이동
  virtual void release() = 0;              // 내려놓기
};

class IPlatform {
 public:
  virtual ~IPlatform() = default;
  virtual Vec2 cursorPos() = 0;
  virtual void setCursorPos(Vec2 p) = 0;
  // anchorAt 위치(보통 화면 밖)에 창을 만들어서 돌려준다. 실패하면 nullptr.
  virtual std::unique_ptr<ICarriedWindow> spawnWindow(CarryKind kind, Vec2 anchorAt) = 0;
  virtual void playSound(SoundId id) = 0;
};

struct Footprint {
  Vec2 pos;
  float angle;
  float age;
};
float footprintAlpha(float age);  // 1 → 0 (수명 끝)

class Pet {
 public:
  Pet(const Config& cfg, IPlatform& plat, float unit, uint32_t seed);

  // 모니터 목록. work = 작업표시줄 뺀 영역, monitors = 모니터 전체 영역. 첫 번째가 주 모니터.
  void setScreens(std::vector<RectF> work, std::vector<RectF> monitors);
  void update(float dt);

  MagpiePose pose() const;
  Vec2 position() const { return pos_; }
  float unit() const { return u_; }
  TaskId task() const { return task_; }
  bool holdingMouse() const { return holdingMouse_; }
  bool carrying() const { return carried_ != nullptr; }
  const std::vector<Footprint>& footprints() const { return prints_; }
  bool footprintsFading() const;
  bool takeFootprintsChanged() {
    bool c = printsChanged_;
    printsChanged_ = false;
    return c;
  }
  void forceTask(TaskId t) { startTask(t); }  // 테스트용
  // 사용자가 까치를 클릭함 → 화나서 커서를 쫓아감. 쫓기 시작했으면 true.
  bool poke();

 private:
  struct Foot {
    Vec2 pos, from, to;
    float t = 1.f, dur = 0.15f, lift = 0.f, height = 2.f;
  };

  // 작업(상태 머신)
  void think(float dt);
  void thinkWander(float dt);
  void thinkNab(float dt);
  void thinkCollect(float dt);
  void thinkMud(float dt);
  void startTask(TaskId t);
  void endTask();
  TaskId pickTask();

  // 몸
  void move(float dt);
  void updateLegs(float dt);
  void land(int foot);
  void animate(float dt);
  void ageFootprints(float dt);
  void call();
  void voice(SoundId id);  // 소리 + 부리 벌리기

  // 이동 헬퍼
  void goTo(Vec2 p, float speed) {
    target_ = p;
    maxSpeed_ = speed;
    hasTarget_ = true;
  }
  void stop() { hasTarget_ = false; }
  bool arrived(float eps) const { return hasTarget_ && length(target_ - pos_) < eps * u_; }
  Vec2 beakTip() const { return magpieBeakTip(pose()); }
  // heading 방향을 볼 때 부리 끝이 몸(pos_)에서 얼마나 떨어져 있나. 현재 방향과 무관 → 빙글빙글 도는 피드백이 없음
  Vec2 beakOffsetFor(float heading) const;

  // 화면 헬퍼
  bool onScreen() const;
  Vec2 randomScreenPoint(float margin);
  Vec2 randomPointNear(Vec2 p, float rmin, float rmax);
  Vec2 exitPoint() const;
  Vec2 pickDropPoint(const ICarriedWindow& w);
  void placeAtStart();

  float rnd(float a, float b) { return std::uniform_real_distribution<float>(a, b)(rng_); }
  float rnd01() { return rnd(0.f, 1.f); }
  int rndi(int n) { return (int)std::uniform_int_distribution<int>(0, n - 1)(rng_); }

  Config cfg_;
  IPlatform& plat_;
  float u_;
  std::mt19937 rng_;
  std::vector<RectF> work_, mon_;
  bool placed_ = false;

  // 몸 상태
  Vec2 pos_{0.f, 0.f}, vel_{0.f, 0.f};
  float heading_ = 0.f, turnRate_ = 0.f, time_ = 0.f;
  Foot feet_[2];
  Vec2 target_{0.f, 0.f};
  float maxSpeed_ = 0.f;
  bool hasTarget_ = false;
  bool backward_ = false;  // 뒷걸음 (뭔가 끌고 갈 때)
  bool lookAt_ = false;    // 가까이 오면 이동 방향 대신 lookPoint_ 쪽을 봄 (커서 물기 직전)
  Vec2 lookPoint_{0.f, 0.f};

  // 애니메이션
  float callT_ = -1.f, beakOpen_ = 0.f, headTurn_ = 0.f, lookPhase_ = 0.f;
  float tailFlick_ = 0.f, tailSway_ = 0.f;

  // 작업 상태
  TaskId task_ = TaskId::Wander, lastTask_ = TaskId::Wander;
  int sub_ = 0;
  float subTimer_ = 0.f, taskTimer_ = 0.f, wanderTimer_ = 0.f, callTimer_ = 0.f;
  bool holdingMouse_ = false;
  bool muddy_ = false;
  Vec2 dragTarget_{0.f, 0.f}, dropBeak_{0.f, 0.f}, dropPos_{0.f, 0.f};

  // 빨리 달릴 땐 두 발 모아 깡충깡충 (까치는 실제로 그렇게 뜀)
  bool hopping_ = false;
  float hopPhase_ = 0.f, hopLift_ = 0.f;
  Vec2 hopPlant_[2];
  std::unique_ptr<ICarriedWindow> carried_;

  std::vector<Footprint> prints_;
  bool printsChanged_ = false;
};
