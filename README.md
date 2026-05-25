# AI Combat SDK

**AI 전투기 대결 챌린지 - 참여자 개발 키트**

> 📦 **SDK 버전**: `v0.11.0.2`

행동트리(Behavior Tree) 기반으로 AI 전투기를 설계하고, 다른 참여자의 AI와 대결하세요!

---

## 💻 시스템 요구사항

- **Python 3.14**: https://www.python.org/downloads/ (설치 시 "Add Python to PATH" 체크)
- **Git**: https://git-scm.com/download/win
- **VSCode** (권장): https://code.visualstudio.com/

---

## 🚀 설치 방법

### 1단계: Fork

1. https://github.com/rokafa-daslab/ai-combat-sdk 방문
2. 우상단 **"Fork"** 버튼 클릭
3. 내 GitHub 계정에 복제 완료

### 2단계: Clone

```powershell
git clone https://github.com/[내-계정]/ai-combat-sdk.git
cd ai-combat-sdk
```

### 3단계: 환경 설정

> ⚠️ **반드시 Python 3.14로 venv를 생성하세요.** SDK 내부의 `.pyd` 바이너리는 `cp314` 전용이라 Python 3.13 이하에서는 `ImportError`가 발생합니다.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

설치 후 `python --version` 으로 `Python 3.14.x` 가 출력되는지 확인하세요.

### 4단계: VSCode에서 열기

`File → Open Folder → ai-combat-sdk 선택`

터미널을 열면 `(.venv)` 가 자동으로 표시됩니다.

> 💡 **Windows 콘솔 한글 깨짐 해결** — PowerShell 기본 코드페이지가 cp949 이라 매치 출력의 한글이 깨질 수 있습니다. 가상환경 활성화 후 한 번만 다음을 실행하세요:
> ```powershell
> $env:PYTHONIOENCODING = "utf-8"
> chcp 65001 > $null
> ```
> 영구 적용하려면 `$PROFILE` 에 두 줄을 추가하세요.

---

## 🔄 SDK 업데이트 (Sync Fork)

SDK가 업데이트되면 GitHub 페이지에서:

1. 내 Fork 저장소 방문
2. **"Sync fork"** 버튼 클릭
3. **"Update branch"** 클릭

```powershell
# 로컬에도 반영
git pull origin main
```

> ✅ `submissions/`, `replays/`, `tournament_data/` 폴더는 `.gitignore`에 의해 보호됩니다.

---

## ⚠️ Breaking Change — SDK 업데이트 시 필독

> **해당 버전**: SDK 동기화(Sync fork) 후 커스텀 노드 코드가 있는 참가자는 아래 내용을 반드시 확인하세요.

### 1. 각도 관측값 단위 변경 (정규화 → 실제 도)

`ata_deg`, `aa_deg`, `tau_deg`, `hca_deg`, `relative_bearing_deg` 값이 **정규화(0~1) → 실제 도(°)** 단위로 변경되었습니다.

```python
# ❌ 이전 코드 (이제 잘못된 결과 발생)
ata = obs.get("ata_deg", 0.0) * 180.0
tau = obs.get("tau_deg", 0.0) * 180.0

# ✅ 현재 올바른 코드 (변환 불필요)
ata = obs.get("ata_deg", 0.0)   # 0°~180°
tau = obs.get("tau_deg", 0.0)   # -180°~180°
```

### 2. 폐기 키 → 단위 접미사 키로 변경

| 폐기된 키 (❌) | 새 키 (✅) | 단위 |
|--------------|----------|------|
| `distance` | `distance_ft` | ft |
| `alt_gap` | `alt_gap_ft` | ft |
| `ego_altitude` | `ego_altitude_ft` | ft |
| `ego_vc` | `ego_vc_kts` | kts |
| `closure_rate` | `closure_rate_kts` | kts |
| `energy_diff` | `energy_diff_ft` | ft |

> 📖 **전체 키 목록 및 단위 규약**: [docs/BLACKBOARD_REFERENCE.md](docs/BLACKBOARD_REFERENCE.md)

> 📜 **버전 간 변경사항 전체 이력**은 저장소 루트의 `CHANGELOG_*.md` 파일을 참조하세요 (예: `CHANGELOG_v0.10.3_to_v0.10.4.md`).

---

## 📖 문서

- **[docs/GUIDE.md](docs/GUIDE.md)** - 첫 에이전트 만들기, 테스트, 전략 개발
- **[docs/NODE_REFERENCE.md](docs/NODE_REFERENCE.md)** - 전체 노드 레퍼런스
- **[docs/BLACKBOARD_REFERENCE.md](docs/BLACKBOARD_REFERENCE.md)** - 관측값 키 전체 레퍼런스 (단위·부호 규약)
- **[docs/VSCODE_SETUP.md](docs/VSCODE_SETUP.md)** - VSCode 환경 설정

---

## 📝 첫 번째 에이전트 만들기

`submissions/my_agent/my_agent.yaml` 파일을 생성하세요:

```yaml
name: "my_agent"
version: "1.0.0"
description: "나의 첫 번째 AI 전투기"

tree:
  type: Selector
  children:
    # 1. Hard Deck 회피 (필수! 최상단 배치)
    - type: Sequence
      children:
        - type: Condition
          name: BelowHardDeck
          params:
            threshold_ft: 3281
        - type: Action
          name: ClimbTo
          params:
            target_altitude_ft: 9843

    # 2. 공격 유리 상황 → 선도 추적
    - type: Sequence
      children:
        - type: Condition
          name: IsOffensiveSituation
        - type: Action
          name: LeadPursuit

    # 3. 방어 필요 상황 → 급선회 회피
    - type: Sequence
      children:
        - type: Condition
          name: IsDefensiveSituation
        - type: Action
          name: BreakTurn

    # 4. 기본 추적
    - type: Action
      name: Pursue
```

## ✅ 검증 및 테스트

```bash
# 에이전트 문법 검증
python tools/validate_agent.py submissions/my_agent/my_agent.yaml

# 테스트 대전
python scripts/run_match.py --agent1 my_agent --agent2 simple

# 다중 라운드 대전
python scripts/run_match.py --agent1 my_agent --agent2 eagle1 --rounds 5
```

## 📊 리플레이 분석

[TacView](https://www.tacview.net/) (무료 버전 가능)로 `replays/*.acmi` 파일을 열어 전투 상황을 3D로 분석하세요.

---

## 🏁 대회 규칙 및 승패 조건

### 승패 판정 (우선순위 순)

| 우선순위 | 조건 | 결과 |
|---------|------|------|
| 1 | 상대 체력(HP)이 0이 됨 | 승리 |
| 2 | **Hard Deck 위반** (고도 < 1,000ft) | **즉시 패배** |
| 3 | 시간 종료 (6,000 시뮬 스텝 = 300초, 시뮬 20Hz) 후 체력 우위 | 체력 많은 쪽 승리 |
| 4 | 시간 종료 후 체력 동점 | 무승부 |

> 시뮬레이션은 **20Hz (1스텝 = 0.05초)** 로 진행됩니다. 행동트리의 **action 선택 tick은 10Hz (0.1초 주기, 시뮬 2스텝마다 1회)**, condition 노드 subtick은 매 시뮬 스텝(20Hz)마다 호출됩니다. `run_match.py` 출력의 `스텝: N / 6000` 은 시뮬 스텝 기준입니다.

### 데미지 시스템 (Gun WEZ 기반)

내 기체가 아래 두 조건을 **동시에** 만족하면 상대에게 데미지가 누적됩니다:

| 조건 | 값 |
|------|-----|
| **ATA (조준 각도)** | < 12° (기수 앞 ±12° 이내) |
| **거리** | 500ft ~ 3,000ft |

- **데미지**: 최대 25 HP/s × 거리계수 × 각도계수 × 0.05s (시뮬 스텝당, 20Hz)
- **초기 체력**: 100 HP
- **전략적 의미**: ATA를 0°에 가깝게(각도계수 최대), 거리를 500ft에 가깝게(거리계수 최대) 유지할수록 빠르게 격추 가능

### 토너먼트 순위

- **승점**: 승 3점, 무 1점, 패 0점
- **동점 시**: Elo 점수로 구분 (초기 1000, K-factor 32)

---

## 📊 관측값 (Observation Space)

행동트리 노드에서 `self.blackboard.observation`으로 접근합니다.

| 키 | 단위/범위 | 설명 |
|----|----------|------|
| `distance_ft` | ft, 0~65617 | 적과의 거리 |
| `ego_altitude_ft` | ft, 0~49213 | 내 고도 |
| `ego_vc_kts` | kts, 0~778 | 내 속도 |
| `alt_gap_ft` | ft, 양수=적이 위 | 고도 차이 (적 고도 − 내 고도) |
| `ata_deg` | **0° ~ 180°** | ATA: 0°=정면조준, 180°=후방 |
| `aa_deg` | **0° ~ 180°** | AA: 0°=적 후방(안전), 180°=정면위협 |
| `hca_deg` | **0° ~ 180°** | HCA: 두 기체 진행방향 교차각 |
| `tau_deg` | **-180° ~ 180°** | TAU: 롤 고려 목표 위치각 |
| `relative_bearing_deg` | **-180° ~ 180°** | 상대 방위각: 양수=오른쪽 |
| `side_flag` | -1, 0, 1 | 적 방향: -1=왼쪽, 0=정면, 1=오른쪽 |
| `closure_rate_kts` | kts (양수=접근) | 접근 속도 |
| `turn_rate_degs` | °/s, 양수 | 선회율 |
| `in_39_line` | bool | 적이 내 3-9 라인 안 (ATA < 90°) |
| `overshoot_risk` | bool | 오버슈트 위험 여부 |
| `tc_type` | `'1-circle'`/`'2-circle'` | 선회 유형 |
| `energy_advantage` | bool | 종합 에너지 우세 |
| `energy_diff_ft` | ft | 에너지 차이 (양수=아군 우세) |
| `alt_advantage` | bool | 고도 우세 |
| `spd_advantage` | bool | 속도 우세 |

> 각도 관측값(`ata_deg`, `aa_deg`, `hca_deg`, `tau_deg`, `relative_bearing_deg`)은 **실제 도(°) 단위**입니다. 변환 없이 바로 사용하세요.

```python
# 커스텀 노드에서 사용 예시
obs = self.blackboard.observation
distance_ft = obs.get("distance_ft", 32808.0)     # ft 단위
ata_deg     = obs.get("ata_deg", 0.0)             # 실제 각도(°), 0°~180°
aa_deg      = obs.get("aa_deg", 0.0)              # 실제 각도(°), 0°~180°
alt_gap_ft  = obs.get("alt_gap_ft", 0.0)          # ft 단위 (양수=적이 위)
side_flag   = obs.get("side_flag", 0)             # -1/0/1
```

---

## 🎯 BFM 상황 분류 시스템

`IsOffensiveSituation`, `IsDefensiveSituation`, `IsNeutralSituation` 조건 노드는 `CombatGeometry`를 기반으로 자동 분류됩니다.

| 상황 | 분류 기준 | 권장 전술 |
|------|----------|----------|
| **OBFM** (공격 유리) | ATA<45°, AA<100°, 거리 0.3~3NM + 에너지/3-9Line 우세 | `LeadPursuit`, `GunAttack`, `HighYoYo`, `OvershootAvoidance` |
| **DBFM** (방어 필요) | AA>90°, ATA>60° 또는 에너지 열세+접근 중 | `BreakTurn`, `DefensiveManeuver`, `DefensiveSpiral`, `EnergyFight` |
| **HABFM** (정면 대등) | HCA>90° 또는 원거리 또는 2-circle 선회 | `OneCircleFight`, `TwoCircleFight`, `TCFight` |

### 핵심 각도 개념

모든 각도는 **NED 좌표계** (`[North, East, Down]`) 기반 3D 벡터 연산으로 계산됩니다.

---

#### ATA (Antenna Train Angle) — 안테나 조준각

**개념**: 내 속도 벡터(`v_a`)와 적까지의 시선 벡터(LOS, `ρ_a = p_t − p_a`) 사이의 각도.  
내가 적을 얼마나 정면으로 조준하고 있는지를 나타냅니다.

```
수식:
  ρ_a = p_t − p_a          (LOS 벡터: 아군→적)
  ATA = arccos( v_a · ρ_a / (|v_a| × |ρ_a|) )

해석:
  0°   = 내 기수가 적을 정확히 향함 (조준 완료, 공격 유리)
  90°  = 적이 내 측면에 위치
  180° = 적이 내 후방에 위치
```

```
         나(→)
          ↑ v_a
          |
  ATA=0°  |  ATA=90°
  [적]←───┼─────────→
          |
```

**전술적 의미**:
- ATA < 12° + 거리 500~3000ft → Gun WEZ 진입 (데미지 발생, ATA가 0°에 가깝을수록 각도계수 증가)
- `BFMClassifier`: ATA < 45° → OBFM 판정, ATA > 60° → DBFM 판정
- `LeadPursuit`: `ata_deg` 기반 WEZ 근접 시 정밀 조준 모드 전환
- `task.py`: `get_lead_params()`로 1초 후 미래 위치 기준 ATA 재계산 → `ata_lead_deg` 관측값으로 노출

---

#### AA (Aspect Angle) — 종횡비각

**개념**: 적 기준으로 내가 어느 위치에 있는지를 나타내는 각도.  
적의 속도 벡터(`v_t`)와 역-LOS 벡터(`−ρ_a`, 적→아군 방향) 사이 각도를 180°에서 뺀 값.

```
수식:
  ρ_t = −ρ_a               (역-LOS 벡터: 적→아군)
  ε   = arccos( v_t · ρ_t / (|v_t| × |ρ_t|) )
  AA  = 180° − ε

해석:
  0°   = 내가 적의 정후방(6시) → 가장 유리한 공격 위치
  90°  = 내가 적의 측면(3시/9시)
  180° = 내가 적의 정면(12시) → 적이 나를 조준 중, 가장 위험
```

```
         적(→) v_t
    ┌────┼────┐
    │    │    │
  AA=0° 적  AA=180°
  (내가  후방)  (내가 정면)
```

**전술적 의미**: AA < 100° = 공격 유리(OBFM), AA > 120° = 방어 필요(DBFM)

---

#### HCA (Heading Crossing Angle) — 진로 교차각

**개념**: 아군 속도 벡터(`v_a`)와 적 속도 벡터(`v_t`) 사이의 각도.  
두 기체가 서로 어떤 방향으로 비행하고 있는지를 나타냅니다.

```
수식:
  HCA = arccos( v_a · v_t / (|v_a| × |v_t|) )

해석:
  0°   = 두 기체가 같은 방향으로 비행 (추격 상황)
  90°  = 두 기체가 직각 방향으로 교차
  180° = 두 기체가 정면으로 마주보며 접근 (Head-on)
```

```
  HCA ≈ 0°          HCA ≈ 90°         HCA ≈ 180°
  나→  적→           나→               나→  ←적
  (추격)             적↓               (Head-on)
                   (교차)
```

**전술적 의미**: HCA > 90° → HABFM(정면/고측면) 판정, 선회 우위 확보 필요

---

#### TAU — 롤 보정 목표 위치각

**개념**: 기체의 롤(Roll) 자세를 고려하여 적이 조종사 기준으로 어느 방향에 있는지를 나타내는 각도.  
LOS 벡터를 **East-Down 평면에 2D 투영**한 뒤, 기체 Up 방향(`[0, −1]`)과의 각도를 계산하고 롤 각도를 보정합니다.

```
수식:
  b_t = [ρ_a[East], ρ_a[Down]]   (LOS의 East-Down 성분)
  b_a = [0, −1]                   (기체 Up 방향, NED에서 Down이 양수이므로 −1)
  τ   = arccos( b_t · b_a / (|b_t| × |b_a|) )
  if b_t[East] > 0: τ = −τ        (오른쪽이면 음수)
  TAU = τ + roll                  (롤 자세 보정)
  TAU = normalize(TAU, −180°~180°)

해석:
  0°   = 적이 기체 정상방(12시, 기수 위쪽)에 위치
  90°  = 적이 기체 오른쪽(3시)에 위치
  −90° = 적이 기체 왼쪽(9시)에 위치
  ±180°= 적이 기체 아래쪽(6시)에 위치
```

**전술적 의미**: `LagPursuit` 액션에서 TAU 기반으로 선회 방향 결정, 오버슈트 방지

---

#### 각도 관측값 단위 요약

| 값 | 범위 | 단위 |
|----|------|------|
| `ata_deg` | 0° ~ 180° | 도(°) |
| `aa_deg` | 0° ~ 180° | 도(°) |
| `hca_deg` | 0° ~ 180° | 도(°) |
| `tau_deg` | −180° ~ 180° | 도(°) |
| `relative_bearing_deg` | −180° ~ 180° | 도(°) |

```python
obs = self.blackboard.observation
ata = obs.get("ata_deg", 0.0)   # 0°~180° (실제 도 단위, 변환 불필요)
aa  = obs.get("aa_deg",  0.0)   # 0°~180°
hca = obs.get("hca_deg", 0.0)   # 0°~180°
tau = obs.get("tau_deg", 0.0)   # -180°~180°
```

Gun WEZ 진입 조건: ATA < 12° AND 거리 500~3000ft → 데미지 발생 (데미지는 ATA와 거리가 0에 가깝을수록 커짐)

---

## 📋 주요 명령어

```bash
# 단판 대전
python scripts/run_match.py --agent1 eagle1 --agent2 simple

# 다중 라운드 대전
python scripts/run_match.py --agent1 my_agent --agent2 eagle1 --rounds 5

# 에이전트 검증
python tools/validate_agent.py submissions/my_agent/my_agent.yaml
```

**에이전트 탐색 순서**: `submissions/{name}/{name}.yaml` → `examples/{name}.yaml` → `examples/{name}/{name}.yaml` → 직접 경로

---

## 📂 디렉토리 구조

```
ai-combat-sdk/
├── docs/
│   ├── NODE_REFERENCE.md       # 전체 노드 + 파라미터 레퍼런스
│   ├── BLACKBOARD_REFERENCE.md # 관측값 키 전체 레퍼런스 (단위·부호 규약)
│   └── GUIDE.md                # 튜토리얼 · 커스텀 노드 개발 가이드
├── tools/
│   ├── validate_agent.py       # 제출 전 검증 도구
│   └── test_agent.py           # 로컬 테스트 도구
├── examples/                   # 예제 에이전트 (테스트용)
│   ├── simple.yaml
│   ├── aggressive.yaml
│   ├── defensive.yaml
│   ├── ace/
│   │   └── ace.yaml
│   └── eagle1/
│       └── eagle1.yaml
├── submissions/                # 참가자 제출 디렉토리
│   └── my_agent/
│       ├── my_agent.yaml
│       └── nodes/             # 커스텀 노드 (선택)
├── scripts/
│   ├── run_match.py           # 대전 실행
│   └── run_tournament.py      # 토너먼트 실행
├── config/                    # 매치 설정
├── src/                       # 핵심 엔진 (컴파일된 바이너리)
├── requirements.txt
└── replays/                   # ACMI 리플레이 파일 (Tacview)
```

---

## 🌳 행동트리 빠른 참조

### 동작 원리

행동트리의 action 선택 tick은 **0.1초마다 한 번**(10Hz, 시뮬 2스텝마다 1회) 실행됩니다. 루트 노드부터 순서대로 평가하며 각 노드는 `SUCCESS` 또는 `FAILURE`를 반환합니다. Condition 노드의 `update()`는 별도로 매 시뮬 스텝(20Hz)마다 호출되어 짧은 이벤트를 놓치지 않습니다.

```
Selector (OR): 자식 중 하나라도 SUCCESS → SUCCESS, 모두 FAILURE → FAILURE
Sequence (AND): 모든 자식이 SUCCESS여야 SUCCESS, 하나라도 FAILURE → FAILURE
Condition: 조건 확인 → SUCCESS/FAILURE
Action: 액션 실행 → 항상 SUCCESS (예외 시 기본 액션 [2,4,2] 반환)
```

### 고수준 액션 공간 (5×9×5)

모든 액션 노드는 내부적으로 3개의 이산 인덱스를 출력합니다:

| 축 | 인덱스 | 의미 |
|----|--------|------|
| **고도** | 0=급하강, 1=하강, 2=유지, 3=상승, 4=급상승 | 5단계 |
| **방향** | 0=급좌(-90°), 1=강좌, 2=중좌, 3=약좌, 4=직진, 5=약우, 6=중우, 7=강우, 8=급우(+90°) | 9단계 |
| **속도** | 0=급감속, 1=감속, 2=유지, 3=가속, 4=급가속 | 5단계 |

이 고수준 명령은 사전 학습된 저수준 RNN 정책(BaselineActor)을 통해 실제 조종면(aileron, elevator, rudder, throttle)으로 자동 변환됩니다.

📚 **노드 전체 목록**: [docs/NODE_REFERENCE.md](docs/NODE_REFERENCE.md) | **개발 가이드**: [docs/GUIDE.md](docs/GUIDE.md)

---

## 📚 문서

| 문서 | 내용 |
|------|------|
| **[docs/GUIDE.md](docs/GUIDE.md)** | 튜토리얼 · 전략 개발 · 커스텀 노드 · 로깅 · 제출 방법 |
| **[docs/NODE_REFERENCE.md](docs/NODE_REFERENCE.md)** | 전체 노드 & 파라미터 레퍼런스 |
| **[docs/BLACKBOARD_REFERENCE.md](docs/BLACKBOARD_REFERENCE.md)** | 관측값 키 전체 레퍼런스 (단위·부호 규약·Breaking Change) |
| **[docs/VSCODE_SETUP.md](docs/VSCODE_SETUP.md)** | VSCode/Windsurf 환경 설정 |

---

## 📞 지원

- **GitHub Issues**: 버그 리포트 및 질문
- **예제**: `examples/`, `submissions/` 폴더 참고

---

**🛩️ 하늘을 지배할 당신의 AI를 개발하세요!**

Copyright © 2026 AI Combat Team. All rights reserved.
