# 데스크톱 까치 (Desktop Magpie)

Windows용 데스크톱 펫. 까치가 화면을 돌아다니면서 마우스 커서를 물어 가고, 화면 밖에서 사진·메모 창을 끌고 오고, 진흙 발자국을 남깁니다.
Desktop Goose 같은 장난 프로그램과 하는 일은 비슷하지만 캐릭터, 그림, 소리, 코드는 모두 새로 만들었습니다.

- 실행 파일 하나(약 370KB). .NET이나 VC++ 런타임이 필요 없고 Windows 기본 DLL만 씁니다.
- 기존 `config.ini` 형식(`key=value`)과 주요 키(`SilenceSounds`, `Task_CanAttackMouse`, `AttackRandomly`, `Min/Max/FirstWander...`)를 그대로 읽습니다.
- 종료: **ESC를 1초 정도 꾹** 누르거나 `Close_Magpie.bat`을 실행합니다.

## 가볍게 만든 방법

| | 흔한 방식 | 데스크톱 까치 |
|---|---|---|
| 그리는 창 | 화면 전체 크기 투명 창 | 까치 주변만 덮는 작은 창(약 188×188), 까치를 따라 이동 |
| 다시 그리는 때 | 매 프레임 | 자세가 바뀌었을 때만 |
| 발자국 | 같은 전체 화면 창에 매번 | 발자국이 있을 때만 별도 창, 바뀔 때만(최대 10Hz) |
| 대기 | 타이머 | 다음 프레임까지 잠들기(`MsgWaitForMultipleObjectsEx`) |
| 소리 | — | 1초 동안 조용하면 오디오 장치 일시정지(CPU 0, 절전 방해 없음) |
| 그리기 | GDI+ | 자체 SDF 래스터라이저(안티에일리어싱 포함). 0.14ms/프레임 |

`FrameRate=30`으로 낮추면 더 가벼워집니다.

## 구조

```
src/
  vec2.h            벡터·사각형·각도 유틸
  raster.h          SDF 래스터라이저 (원, 캡슐, 타원, 삼각형, 둥근 원뿔)
  magpie_draw.*     까치 그리기 (도형 조합, 색 변경 가능), 발자국
  pet.*             두뇌 + 몸: 스티어링 이동, 절차적 다리, 작업 상태 머신
  synth.*           소리 합성 ("까악", 무는 소리, 발소리) — 파일 없이 코드로 생성
  config.*          config.ini 파서
  win/main.cpp      진입점: 투명·클릭통과 오버레이 창, 게임 루프, ESC 종료, 트레이
  win/win_platform.* 커서 조작, 모니터 목록, 물어 오는 창(사진/메모)
  win/audio_win.*   waveOut 믹서 + 사용자 mp3/wav (MCI)
  win/log.*         진단 로그 (DesktopMagpie.log)
tests/sim_test.cpp  리눅스에서 두뇌/그림/소리 검증 (가짜 바탕화면 시뮬레이션)
```

`pet.cpp`는 OS를 모릅니다. 커서·창·소리는 `IPlatform` 인터페이스를 통해서만 다루기 때문에, 같은 코드를 테스트에서는 가짜 바탕화면 위에서 돌립니다.

### 작업(상태 머신)

```
Wander(어슬렁, Min~MaxWanderingTimeSeconds) ──시간 끝──▶ 무작위 선택
  ├─ NabMouse     커서로 돌진 → 물기 → 2.5~4.5초 끌고 다니기
  ├─ CollectImage 가까운 화면 밖으로 → 사진 창 생성 → 뒷걸음으로 끌고 와서 내려놓기
  ├─ CollectNote  위와 같고 메모 창
  └─ TrackMud     9~14초 동안 진흙 발자국 (16초 뒤 사라짐)
```

## 빌드

리눅스(또는 MSYS2)에서 MinGW-w64로 빌드:

```bash
./build_mingw.sh
```

`build/DesktopMagpie/` 폴더와 `build/DesktopMagpie.zip`이 만들어집니다.

Windows에서 Visual Studio로 빌드:

```bash
cmake -S . -B build
```

```bash
cmake --build build --config Release
```

아이콘(`res/magpie.ico`)은 까치 그리기 코드로 만듭니다: `python3 tools/make_icon.py build/sim_test res/magpie.ico`

## 테스트

```bash
./tests/run_tests.sh
```

240초 동안 가짜 바탕화면에서 시뮬레이션하고 다음을 검사합니다.
- 모든 작업이 실행되는지
- 커서를 문 동안 커서가 정확히 부리 끝에 있는지
- 땅에 붙은 발이 미끄러지지 않는지
- 내려놓은 창이 화면 안에 완전히 들어오는지
- 합성한 소리의 음량이 정상 범위인지

## 진단

- 실행할 때마다 exe 옆에 `DesktopMagpie.log`가 생깁니다(쓸 수 없으면 `%TEMP%`). 시작 과정, 모니터, 오디오, 작업 변화, 오류, 충돌 위치가 남습니다.
- `DesktopMagpie.exe --selftest [초]`: 사진 → 메모 → 진흙 → 마우스 순서로 모든 행동을 시키고, **화면을 실제로 캡처해서 까치가 보이는지**까지 확인한 뒤 종료합니다. 종료 코드 0이면 통과입니다.
- GitHub Actions(`.github/workflows/desktop-magpie.yml`)가 MinGW 빌드와 MSVC 빌드를 진짜 Windows에서 `--selftest`로 돌립니다.

## 꾸미기 (exe 옆 `Assets` 폴더)

| 폴더 | 넣을 것 |
|---|---|
| `Assets\Images\` | png/jpg/gif/bmp → 까치가 물어 오는 사진 (없으면 까치 자화상) |
| `Assets\Notes\` | txt → 메모 창 내용 (없으면 기본 문구) |
| `Assets\Sound\Call\`, `Bite\`, `Step\` | mp3/wav → 합성음 대신 재생 |

## 설정 (`config.ini`)

| 키 | 기본값 | 설명 |
|---|---|---|
| `SilenceSounds` | False | 소리 끄기 |
| `Task_CanAttackMouse` | True | 마우스 훔치기 사용 |
| `AttackRandomly` | False | 어슬렁거리다가도 아무 때나 마우스 습격 |
| `UseCustomColors` | False | 아래 `Magpie*` 색 사용 |
| `MagpieBlack` / `MagpieWhite` / `MagpieBlue` / `MagpieOutline` | | 몸 / 어깨깃 / 날개·꼬리 광택 / 외곽선 색 |
| `MinWanderingTimeSeconds` | 20 | 장난 사이 최소 대기 |
| `MaxWanderingTimeSeconds` | 40 | 장난 사이 최대 대기 |
| `FirstWanderTimeSeconds` | 20 | 첫 장난까지 대기 |
| `Scale` | 1.0 | 크기 배율 |
| `FrameRate` | 60 | 초당 프레임 |
| `ShowTrayIcon` | False | 트레이 아이콘 (우클릭 → 종료) |
