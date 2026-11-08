#include "pet.h"

#include <algorithm>
#include <cmath>

namespace {
// 속도는 "디자인 단위/초" (unit 을 곱하면 픽셀/초). 평소엔 느긋하게, 쫓아갈 때만 빠르게.
constexpr float kWalkSpeed = 55.f;
constexpr float kRunSpeed = 240.f;       // 커서 쫓아갈 때
constexpr float kDragSpeed = 48.f;       // 창 끌고 올 때
constexpr float kMouseDragSpeed = 55.f;  // 커서 물고 끌고 갈 때
constexpr float kMudSpeed = 62.f;
constexpr float kTurnRate = 5.5f;           // 라디안/초
constexpr float kHopStart = 110.f;          // 이 속도보다 빠르면 깡충 뛰기
constexpr float kHopStop = 85.f;            // 이 속도보다 느려지면 다시 걷기
constexpr float kLegReach = 5.f;            // 발이 이만큼 뒤처지면 바로 한 걸음
constexpr float kLegMax = 7.f;              // 들린 발은 이 길이를 넘지 않음
constexpr float kFootprintLife = 16.f;      // 발자국 수명 (초)
constexpr float kFootprintFade = 5.f;       // 마지막 몇 초 동안 흐려짐
constexpr float kGrabRadius = 12.f;         // 부리 끝 ~ 커서 거리 (단위)
constexpr size_t kMaxFootprints = 400;

int nearestRect(const std::vector<RectF>& v, Vec2 p) {
  int best = 0;
  float bd = 1e30f;
  for (size_t i = 0; i < v.size(); ++i) {
    const RectF& r = v[i];
    float dx = std::max({r.x0 - p.x, 0.f, p.x - r.x1});
    float dy = std::max({r.y0 - p.y, 0.f, p.y - r.y1});
    float d = dx * dx + dy * dy;
    if (d < bd) {
      bd = d;
      best = (int)i;
    }
  }
  return best;
}
}  // namespace

const char* taskName(TaskId t) {
  switch (t) {
    case TaskId::Wander: return "Wander";
    case TaskId::NabMouse: return "NabMouse";
    case TaskId::CollectImage: return "CollectImage";
    case TaskId::CollectNote: return "CollectNote";
    case TaskId::TrackMud: return "TrackMud";
  }
  return "?";
}

float footprintAlpha(float age) {
  if (age < kFootprintLife - kFootprintFade) return 1.f;
  return clampf((kFootprintLife - age) / kFootprintFade, 0.f, 1.f);
}

Pet::Pet(const Config& cfg, IPlatform& plat, float unit, uint32_t seed)
    : cfg_(cfg), plat_(plat), u_(unit), rng_(seed) {
  wanderTimer_ = cfg_.firstWander;
  callTimer_ = rnd(4.f, 10.f);
}

void Pet::setScreens(std::vector<RectF> work, std::vector<RectF> monitors) {
  if (work.empty()) work.push_back({0.f, 0.f, 1920.f, 1080.f});
  if (monitors.size() != work.size()) monitors = work;
  work_ = std::move(work);
  mon_ = std::move(monitors);
  if (!placed_) {
    placeAtStart();
    placed_ = true;
  }
}

// 시작: 주 모니터 왼쪽/오른쪽 가장자리에서 바로 보이게 나타나 "까악" 하고 걸어 들어온다
void Pet::placeAtStart() {
  const RectF& w = work_[0];
  bool fromLeft = rnd01() < 0.5f;
  float y = rnd(w.y0 + w.h() * 0.35f, w.y0 + w.h() * 0.8f);
  pos_ = {fromLeft ? w.x0 + 30.f * u_ : w.x1 - 30.f * u_, y};
  heading_ = fromLeft ? 0.f : kPi;
  Vec2 f = fromAngle(heading_), r{-f.y, f.x};
  feet_[0].pos = pos_ - r * (4.2f * u_);
  feet_[1].pos = pos_ + r * (4.2f * u_);
  float tx = fromLeft ? w.x0 + w.w() * rnd(0.15f, 0.4f) : w.x1 - w.w() * rnd(0.15f, 0.4f);
  goTo({tx, clampf(y + rnd(-80.f, 80.f) * u_, w.y0 + 60.f * u_, w.y1 - 30.f * u_)}, kWalkSpeed);
  task_ = TaskId::Wander;
  sub_ = 0;
  call();
}

void Pet::update(float dt) {
  if (dt <= 0.f || work_.empty()) return;
  time_ += dt;
  think(dt);
  move(dt);
  updateLegs(dt);
  animate(dt);
  ageFootprints(dt);

  // 무엇을 물고 있으면 매 프레임 부리 끝으로 끌어온다
  Vec2 tip = beakTip();
  if (holdingMouse_) plat_.setCursorPos(tip);
  if (carried_ && carried_->alive()) carried_->moveAnchorTo(tip);
}

// ---------------- 작업 (상태 머신) ----------------

void Pet::think(float dt) {
  switch (task_) {
    case TaskId::Wander: thinkWander(dt); break;
    case TaskId::NabMouse: thinkNab(dt); break;
    case TaskId::CollectImage:
    case TaskId::CollectNote: thinkCollect(dt); break;
    case TaskId::TrackMud: thinkMud(dt); break;
  }
}

// 어슬렁: 목적지까지 걷고 → 잠깐 두리번 → 반복. 시간이 다 되면 다른 작업.
void Pet::thinkWander(float dt) {
  wanderTimer_ -= dt;
  callTimer_ -= dt;
  if (callTimer_ <= 0.f) {
    call();
    callTimer_ = rnd(6.f, 18.f);
  }
  if (onScreen()) {
    if (cfg_.canAttackMouse && cfg_.attackRandomly && rnd01() < dt / 40.f) {
      startTask(TaskId::NabMouse);
      return;
    }
    if (wanderTimer_ <= 0.f) {
      startTask(pickTask());
      return;
    }
  }
  if (sub_ == 0) {
    if (!hasTarget_ || arrived(6.f)) {
      stop();
      sub_ = 1;
      subTimer_ = rnd(0.6f, 2.8f);
      tailFlick_ = 1.f;
      lookPhase_ = rnd(0.f, 6.28f);
    }
  } else {
    subTimer_ -= dt;
    if (subTimer_ <= 0.f) {
      goTo(randomScreenPoint(60.f * u_), kWalkSpeed);
      sub_ = 0;
    }
  }
}

// 마우스 습격: 커서로 돌진 → 물기 → 몇 초 동안 끌고 다니기 → 놓기
void Pet::thinkNab(float dt) {
  taskTimer_ += dt;
  if (sub_ == 0) {
    Vec2 cur = plat_.cursorPos();
    // 부리는 몸보다 위(화면상 위쪽)에 있으니, 커서 바로 "아래 바닥" 지점을 기준으로 생각한다:
    // 그 지점을 바라보고 부리 길이만큼 떨어져 서면 부리 끝이 정확히 커서에 닿음.
    const Vec2 o = beakOffsetFor(0.f);  // 오른쪽을 볼 때: x = 부리 길이, -y = 부리 높이
    const Vec2 ground = cur + Vec2{0.f, -o.y};
    Vec2 toG = ground - pos_;
    Vec2 face = length(toG) > 0.5f * u_ ? normalize(toG) : fromAngle(heading_);
    goTo(ground - face * o.x, kRunSpeed);
    lookAt_ = true;
    lookPoint_ = ground;
    if (length(beakTip() - cur) < kGrabRadius * u_) {
      plat_.playSound(SoundId::Bite);
      holdingMouse_ = true;
      backward_ = true;
      lookAt_ = false;
      sub_ = 1;
      taskTimer_ = 0.f;
      subTimer_ = rnd(2.5f, 4.5f);
      dragTarget_ = randomPointNear(pos_, 250.f * u_, 550.f * u_);
    } else if (taskTimer_ > 10.f) {
      endTask();  // 너무 오래 못 잡으면 포기
    }
  } else {
    goTo(dragTarget_, kMouseDragSpeed);
    if (taskTimer_ > subTimer_ || arrived(8.f)) {
      holdingMouse_ = false;
      call();
      endTask();
    }
  }
}

// 창 가져오기: 가까운 화면 가장자리 밖으로 나감 → 창 생성 → 뒷걸음으로 끌고 들어옴 → 내려놓기
void Pet::thinkCollect(float dt) {
  taskTimer_ += dt;
  if (sub_ == 0) {
    if (arrived(10.f) || taskTimer_ > 20.f) {
      CarryKind kind = task_ == TaskId::CollectImage ? CarryKind::Image : CarryKind::Note;
      carried_ = plat_.spawnWindow(kind, beakTip());
      if (!carried_) {
        endTask();
        return;
      }
      plat_.playSound(SoundId::Bite);
      dropBeak_ = pickDropPoint(*carried_);
      {  // 내려놓을 자리를 처음 한 번만 계산 (뒷걸음이라 창 쪽 = 지나온 쪽을 보고 있게 됨)
        Vec2 travel = normalize(dropBeak_ - beakTip());
        dropPos_ = dropBeak_ - beakOffsetFor(std::atan2(-travel.y, -travel.x));
      }
      backward_ = true;
      sub_ = 1;
      taskTimer_ = 0.f;
    }
  } else {
    if (!carried_ || !carried_->alive()) {
      carried_.reset();
      endTask();
      return;
    }
    goTo(dropPos_, kDragSpeed);
    if (arrived(4.f) || taskTimer_ > 30.f) {
      carried_->release();
      carried_.reset();
      plat_.playSound(SoundId::Drop);
      call();
      endTask();
    }
  }
}

// 진흙 발자국: 한동안 짧게짧게 돌아다니며 발자국을 남김
void Pet::thinkMud(float dt) {
  taskTimer_ -= dt;
  if (taskTimer_ <= 0.f) {
    endTask();
    return;
  }
  if (sub_ == 0) {
    if (!hasTarget_ || arrived(6.f)) {
      stop();
      sub_ = 1;
      subTimer_ = rnd(0.15f, 0.7f);
    }
  } else {
    subTimer_ -= dt;
    if (subTimer_ <= 0.f) {
      goTo(randomPointNear(pos_, 120.f * u_, 380.f * u_), kMudSpeed);
      sub_ = 0;
    }
  }
}

void Pet::startTask(TaskId t) {
  task_ = t;
  lastTask_ = t;
  sub_ = 0;
  subTimer_ = 0.f;
  taskTimer_ = 0.f;
  backward_ = false;
  lookAt_ = false;
  switch (t) {
    case TaskId::Wander: break;
    case TaskId::NabMouse:
      voice(SoundId::Chatter);
      plat_.playSound(SoundId::Flap);
      break;
    case TaskId::CollectImage:
    case TaskId::CollectNote: goTo(exitPoint(), kWalkSpeed); break;
    case TaskId::TrackMud:
      muddy_ = true;
      taskTimer_ = rnd(9.f, 14.f);
      sub_ = 1;
      break;
  }
}

void Pet::endTask() {
  holdingMouse_ = false;
  backward_ = false;
  lookAt_ = false;
  muddy_ = false;
  if (carried_) {
    carried_->release();
    carried_.reset();
  }
  task_ = TaskId::Wander;
  sub_ = 1;
  subTimer_ = rnd(0.5f, 1.5f);
  stop();
  wanderTimer_ = rnd(cfg_.minWander, cfg_.maxWander);
}

TaskId Pet::pickTask() {
  TaskId opts[4];
  int n = 0;
  if (cfg_.canAttackMouse) opts[n++] = TaskId::NabMouse;
  opts[n++] = TaskId::CollectImage;
  opts[n++] = TaskId::CollectNote;
  opts[n++] = TaskId::TrackMud;
  for (int i = 0; i < 6; ++i) {
    TaskId t = opts[rndi(n)];
    if (t != lastTask_) return t;
  }
  return opts[0];
}

// ---------------- 몸 ----------------

// 목표 쪽으로 서서히 가속 (스티어링). 목표 근처에선 감속.
void Pet::move(float dt) {
  Vec2 desired{0.f, 0.f};
  if (hasTarget_) {
    Vec2 d = target_ - pos_;
    float dist = length(d);
    if (dist > 0.5f) {
      float sp = maxSpeed_ * u_ * clampf(dist / (50.f * u_), 0.2f, 1.f);
      desired = d * (sp / dist);
    }
  }
  float k = 1.f - std::exp(-8.f * dt);
  vel_ += (desired - vel_) * k;
  pos_ += vel_ * dt;

  float prev = heading_;
  const float spd = length(vel_);
  const bool near = hasTarget_ && length(target_ - pos_) < 30.f * u_;
  if (lookAt_ && (near || spd < 25.f * u_) && length(lookPoint_ - pos_) > 0.5f * u_) {
    // 거의 다 왔으면 이동 방향이 아니라 물 대상 쪽으로 몸을 돌림
    Vec2 d = lookPoint_ - pos_;
    heading_ = approachAngle(heading_, std::atan2(d.y, d.x), kTurnRate * 1.3f * dt);
  } else if (spd > 6.f * u_) {
    float want = std::atan2(vel_.y, vel_.x) + (backward_ ? kPi : 0.f);
    heading_ = approachAngle(heading_, want, kTurnRate * dt);
  }
  turnRate_ = wrapAngle(heading_ - prev) / dt;
}

// 절차적 걷기: 발은 바닥에 고정, 몸이 멀어지면 한 발씩 앞으로 내딛는다.
// 빨리 달릴 땐 두 발을 모아 깡충깡충 뛴다. 어떤 경우에도 다리가 kLegMax 보다 길어지지 않는다.
void Pet::updateLegs(float dt) {
  const Vec2 f = fromAngle(heading_), r{-f.y, f.x};
  const float spd = length(vel_);
  Vec2 ideal[2];
  for (int i = 0; i < 2; ++i) ideal[i] = pos_ + r * ((i == 0 ? -1.f : 1.f) * 4.2f * u_) + f * (0.5f * u_);

  // ---- 깡충 뛰기 ----
  if (!hopping_ && spd > kHopStart * u_) {
    hopping_ = true;
    hopPhase_ = 0.f;
    for (Foot& ft : feet_) ft.t = 1.f;
  } else if (hopping_ && spd < kHopStop * u_) {
    hopping_ = false;  // 발은 지금 자리에 그대로 내려놓고, 걷기 로직이 이어받음
    hopLift_ = 0.f;
    for (Foot& ft : feet_) {
      ft.lift = 0.f;
      ft.t = 1.f;
    }
  }
  if (hopping_) {
    constexpr float kAir = 0.7f;  // 한 번 뛸 때 공중에 있는 비율
    const float rate = clampf(spd / (38.f * u_), 4.f, 6.5f);  // 초당 뛰는 횟수
    const float prev = hopPhase_ - std::floor(hopPhase_);
    hopPhase_ += rate * dt;
    const float ph = hopPhase_ - std::floor(hopPhase_);
    const bool landed = (prev < kAir && ph >= kAir) || (ph < prev && prev < kAir);
    if (landed) {  // 착지: 발을 몸보다 살짝 앞에 딛어서, 땅에 있는 동안 몸이 발 위를 지나가게
      const float ground = (1.f - kAir) / rate;
      for (int i = 0; i < 2; ++i) hopPlant_[i] = ideal[i] + vel_ * (ground * 0.5f);
    }
    if (ph < kAir) {  // 공중: 발은 몸 아래에 붙어서 같이 뜸
      hopLift_ = std::sin(kPi * ph / kAir) * (3.f + spd / u_ * 0.01f);
      for (int i = 0; i < 2; ++i) {
        feet_[i].pos = ideal[i];
        feet_[i].lift = hopLift_;
      }
    } else {  // 땅: 발 고정
      hopLift_ = 0.f;
      for (int i = 0; i < 2; ++i) {
        feet_[i].pos = hopPlant_[i];
        feet_[i].lift = 0.f;
      }
    }
    if (landed) {
      for (int i = 0; i < 2; ++i) {
        if (muddy_) prints_.push_back({hopPlant_[i], heading_, 0.f});
      }
      if (muddy_) printsChanged_ = true;
      plat_.playSound(SoundId::Step);
    }
    return;
  }

  // ---- 걷기 ----
  const float stepDur = clampf(0.19f - spd / u_ * 0.0004f, 0.12f, 0.19f);
  const float reach = kLegReach * u_, maxLeg = kLegMax * u_;
  // 발이 땅에 닿을 때 몸이 있을 자리(이동 + 회전까지 예측)
  auto idealAt = [&](int i, float ahead) {
    float h = heading_ + clampf(turnRate_, -kTurnRate, kTurnRate) * ahead;
    Vec2 ff = fromAngle(h), rr{-ff.y, ff.x};
    return pos_ + vel_ * ahead + rr * ((i == 0 ? -1.f : 1.f) * 4.2f * u_) + ff * (0.5f * u_);
  };
  auto startStep = [&](int i, float dur) {
    Foot& ft = feet_[i];
    Vec2 lead{0.f, 0.f};  // 몸보다 살짝 앞에 딛기
    if (spd > 1.f) lead = vel_ * (std::min(reach * 0.5f, spd * dur * 0.45f) / spd);
    ft.from = ft.pos;
    ft.to = idealAt(i, dur) + lead;
    ft.t = 0.f;
    ft.dur = dur;
    ft.height = 2.2f + spd / u_ * 0.012f;
  };

  bool stepping = false;
  for (int i = 0; i < 2; ++i) {
    Foot& ft = feet_[i];
    if (ft.t >= 1.f) continue;
    ft.t = std::min(1.f, ft.t + dt / ft.dur);
    float e = ft.t * ft.t * (3.f - 2.f * ft.t);
    ft.pos = lerp(ft.from, ft.to, e);
    ft.lift = std::sin(kPi * ft.t) * ft.height;
    if (ft.t >= 1.f) {
      ft.pos = ft.to;
      ft.lift = 0.f;
      land(i);
    } else {
      stepping = true;
    }
  }

  // 너무 뒤처진 발은 다른 발이 걷는 중이어도 바로 따라붙음 (빠르게 돌 때)
  for (int i = 0; i < 2; ++i)
    if (feet_[i].t >= 1.f && length(feet_[i].pos - ideal[i]) > reach) {
      startStep(i, stepDur * 0.6f);
      stepping = true;
    }

  if (!stepping) {
    const float thresh = std::max(2.5f * u_, spd * stepDur * 0.75f);
    int best = -1;
    float bestD = thresh;
    for (int i = 0; i < 2; ++i) {
      float d = length(ideal[i] - feet_[i].pos);
      if (d > bestD) {
        bestD = d;
        best = i;
      }
    }
    if (best >= 0) startStep(best, stepDur);
  }

  // 안전장치: 들려 있는 발은 다리 길이 상한을 넘지 않게 (땅에 붙은 발은 미끄러뜨리지 않고 위에서 바로 걷게 함)
  for (int i = 0; i < 2; ++i) {
    if (feet_[i].t >= 1.f) continue;
    Vec2 d = feet_[i].pos - ideal[i];
    float L = length(d);
    if (L > maxLeg) feet_[i].pos = ideal[i] + d * (maxLeg / L);
  }
}

void Pet::land(int i) {
  if (muddy_) {
    prints_.push_back({feet_[i].pos, heading_, 0.f});
    if (prints_.size() > kMaxFootprints) prints_.erase(prints_.begin());
    printsChanged_ = true;
  }
  if (length(vel_) > 20.f * u_) plat_.playSound(SoundId::Step);
}

void Pet::animate(float dt) {
  // "까악 까악" 할 때 부리 두 번 벌리기
  float open = 0.f;
  if (callT_ >= 0.f) {
    callT_ += dt;
    if (callT_ > 0.56f)
      callT_ = -1.f;
    else
      open = std::max(0.f, std::sin(callT_ * kPi / 0.14f));
  }
  if (holdingMouse_ || carried_) open = std::max(open, 0.3f);
  beakOpen_ += (open - beakOpen_) * (1.f - std::exp(-25.f * dt));

  // 멈춰 있을 땐 두리번
  float look = 0.f;
  if (task_ == TaskId::Wander && sub_ == 1 && !hasTarget_) {
    float s = std::sin(time_ * 1.3f + lookPhase_);
    look = s > 0.35f ? 0.55f : (s < -0.35f ? -0.55f : 0.f);
  }
  headTurn_ += (look - headTurn_) * (1.f - std::exp(-7.f * dt));

  tailFlick_ = std::max(0.f, tailFlick_ - dt * 2.5f);
  float swayTarget = std::sin(time_ * 2.1f) * 0.05f - clampf(turnRate_ * 0.05f, -0.35f, 0.35f);
  tailSway_ += (swayTarget - tailSway_) * (1.f - std::exp(-6.f * dt));
}

void Pet::ageFootprints(float dt) {
  if (prints_.empty()) return;
  for (auto& p : prints_) p.age += dt;
  size_t dead = 0;
  while (dead < prints_.size() && prints_[dead].age >= kFootprintLife) ++dead;
  if (dead) {
    prints_.erase(prints_.begin(), prints_.begin() + (long)dead);
    printsChanged_ = true;
  }
}

bool Pet::footprintsFading() const {
  return !prints_.empty() && prints_.front().age > kFootprintLife - kFootprintFade;
}

void Pet::call() { voice(SoundId::Call); }

void Pet::voice(SoundId id) {
  plat_.playSound(id);
  callT_ = 0.f;
}

bool Pet::poke() {
  if (task_ == TaskId::NabMouse) return false;  // 이미 쫓는 중
  if (carried_) {                               // 창 끌고 오는 중엔 투덜대기만
    voice(SoundId::Chatter);
    return false;
  }
  muddy_ = false;
  backward_ = false;
  startTask(TaskId::NabMouse);
  return true;
}

MagpiePose Pet::pose() const {
  MagpiePose p;
  p.pos = pos_;
  p.heading = heading_;
  p.unit = u_;
  for (int i = 0; i < 2; ++i) {
    p.feet[i] = feet_[i].pos;
    p.footLift[i] = feet_[i].lift;
  }
  p.bob = hopping_ ? hopLift_ : (feet_[0].lift + feet_[1].lift) * 0.2f;
  p.tailSway = tailSway_;
  p.tailLift = std::sin(tailFlick_ * kPi) * 5.f;
  p.headTurn = headTurn_;
  p.headThrust = beakOpen_ * 1.5f;
  p.beakOpen = beakOpen_;
  p.legs = cfg_.showLegs ? 1.f : 0.f;
  p.bodyZ = cfg_.showLegs ? kBodyZLegs : kBodyZDots;
  return p;
}

Vec2 Pet::beakOffsetFor(float heading) const {
  MagpiePose p = pose();
  p.heading = heading;
  p.headTurn = 0.f;
  return magpieBeakTip(p) - pos_;
}

// ---------------- 화면 헬퍼 ----------------

bool Pet::onScreen() const {
  for (const RectF& r : work_)
    if (r.contains(pos_, 20.f * u_)) return true;
  return false;
}

Vec2 Pet::randomScreenPoint(float margin) {
  int idx = nearestRect(work_, pos_);
  if (work_.size() > 1 && rnd01() < 0.15f) idx = rndi((int)work_.size());
  const RectF& a = work_[idx];
  float x0 = a.x0 + margin, x1 = a.x1 - margin, y0 = a.y0 + margin, y1 = a.y1 - margin;
  if (x1 < x0) x0 = x1 = a.center().x;
  if (y1 < y0) y0 = y1 = a.center().y;
  return {rnd(x0, x1), rnd(y0, y1)};
}

Vec2 Pet::randomPointNear(Vec2 p, float rmin, float rmax) {
  const RectF& a = work_[nearestRect(work_, p)];
  const float m = 40.f * u_;
  for (int i = 0; i < 12; ++i) {
    Vec2 q = p + fromAngle(rnd(0.f, 2.f * kPi)) * rnd(rmin, rmax);
    if (a.contains(q, m)) return q;
  }
  return {clampf(p.x, a.x0 + m, std::max(a.x0 + m, a.x1 - m)), clampf(p.y, a.y0 + m, std::max(a.y0 + m, a.y1 - m))};
}

// 지금 있는 모니터에서 가장 가까운 "진짜 바깥" 지점 (다른 모니터 위가 아닌 곳)
Vec2 Pet::exitPoint() const {
  const RectF& m = mon_[nearestRect(mon_, pos_)];
  const float margin = 90.f * u_;
  const Vec2 cands[4] = {
      {m.x0 - margin, pos_.y}, {m.x1 + margin, pos_.y}, {pos_.x, m.y0 - margin}, {pos_.x, m.y1 + margin}};
  int best = -1;
  float bestD = 1e30f;
  for (int i = 0; i < 4; ++i) {
    bool covered = false;
    for (const RectF& o : mon_)
      if (o.contains(cands[i], -margin * 0.5f)) covered = true;
    if (covered) continue;
    float d = length(cands[i] - pos_) * (i >= 2 ? 1.6f : 1.f);  // 좌우 가장자리를 선호
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best >= 0 ? cands[best] : cands[0];
}

// 창이 화면 안에 다 들어오는 위치 중, 너무 멀지 않은 곳
Vec2 Pet::pickDropPoint(const ICarriedWindow& w) {
  const RectF& a = work_[nearestRect(work_, pos_)];
  const Vec2 sz = w.size(), an = w.anchor();
  const float pad = 24.f * u_;
  float x0 = a.x0 + pad + an.x, x1 = a.x1 - pad - (sz.x - an.x);
  float y0 = a.y0 + pad + an.y, y1 = a.y1 - pad - (sz.y - an.y);
  if (x1 < x0) x0 = x1 = a.x0 + an.x;
  if (y1 < y0) y0 = y1 = a.y0 + an.y;
  Vec2 best{rnd(x0, x1), rnd(y0, y1)};
  float bestScore = 1e30f;
  for (int i = 0; i < 12; ++i) {
    Vec2 q{rnd(x0, x1), rnd(y0, y1)};
    float d = length(q - pos_);
    float score = d < 250.f * u_ ? 250.f * u_ - d : (d > 700.f * u_ ? d - 700.f * u_ : 0.f);
    if (score < bestScore) {
      bestScore = score;
      best = q;
      if (score == 0.f) break;
    }
  }
  return best;
}
