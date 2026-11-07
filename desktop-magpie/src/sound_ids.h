#pragma once
// 소리 종류. Assets\Sound\<이름>\ 폴더 이름과 같다.
enum class SoundId { Call = 0, Chatter, Bite, Step, Flap, Drop, Count };

constexpr int kSoundKinds = (int)SoundId::Count;

inline const char* soundName(SoundId id) {
  switch (id) {
    case SoundId::Call: return "Call";        // 까악
    case SoundId::Chatter: return "Chatter";  // 깍깍깍깍 (화났을 때)
    case SoundId::Bite: return "Bite";        // 뽁 (물기)
    case SoundId::Step: return "Step";        // 톡 (발소리)
    case SoundId::Flap: return "Flap";        // 파닥 (뛰기 시작)
    case SoundId::Drop: return "Drop";        // 통 (창 내려놓기)
    case SoundId::Count: break;
  }
  return "?";
}
