# AI Combat SDK — 행동 트리 기반 공중전 에이전트 개발 가이드

> JSBSim F-16 시뮬레이터에서 행동 트리(BT)를 설계하여 1대1 공중전에서 승리하는 자율 에이전트를 만드는 과정을 다룹니다.

---

## 1. 프로젝트 개요

### 1.1 무엇을 만드는가?

F-16 전투기 2대가 공중에서 1대1로 싸우는 시뮬레이션에서, **행동 트리(Behavior Tree)** 를 설계하여 상대를 이기는 자율 에이전트를 만듭니다.

```
┌──────────────┐          ┌──────────────┐
│   내 에이전트  │  ◄────►  │  상대 에이전트  │
│  (golden.yaml) │   교전   │  (ace.yaml)   │
└──────┬───────┘          └──────┬───────┘
       │                         │
       ▼                         ▼
┌──────────────────────────────────────┐
│     JSBSim F-16 물리 시뮬레이터       │
│   (고도, 속도, 선회율, 중력 등 실제   │
│    비행역학 기반 시뮬레이션)           │
└──────────────────────────────────────┘
```

### 1.2 승패 조건

매 스텝 단일 판정 — 위가 먼저:

| 우선순위 | 조건 | 결과 |
|---------|------|------|
| 1 | **Hard Deck 위반** (고도 < 1,000 ft) | 위반자 즉시 패배 |
| 2 | **비행 안전 위반** (9 G·5s / spin·3s / stall·10s 누적) | 위반자 즉시 패배 |
| 3 | 상대 HP ≤ 0 | 승리 |
| 4 | BT 실행 예외 | 위반자 DISQUALIFY |
| 5 | Wall-clock 60s 초과 | 무승부 (안전장치) |
| 6 | 시간 종료 + HP 우위 | HP 많은 쪽 승리 |
| 6′ | 시간 종료 + HP 동일 | 무승부 |

> ⚠️ **매치 시뮬 시간**: 300초 (20 Hz 기준 6,000 스텝, `max_steps: 0` = 자동). **실제 시간 상한**: 60초 (JSBSim hang 안전장치).

> 📘 **전체 룰북**: 비행 안전 임계값·정보 비공개 정책·결정론·랭킹 등 상세는 [RULEBOOK.md](RULEBOOK.md) 참조.

### 1.2.1 BT가 관측 가능한 매치 상태 (정보 비공개 정책)

룰북 §4.3에 따라 **적의 HP/입은 데미지는 BT에 노출되지 않는다.** BT는 자기 중심 관측과 교전 기하로만 적 상태를 추정해야 한다.

| 노출 | 키 | 설명 |
|---|---|---|
| ✅ | `ego_health` | 자기 현재 HP |
| ✅ | `ego_damage_dealt` | 자기가 가한 누적 데미지 |
| ✅ | `ego_damage_received` | 자기가 받은 누적 데미지 |
| ✅ | `in_wez` | 자기가 적을 WEZ에 잡고 있음 |
| ✅ | `enm_in_wez` | 적이 자기를 WEZ에 잡고 있음 (위협 신호) |
| ❌ | `enm_health` | **노출 안 됨** — BT observation에서 제거됨 |
| ❌ | `enm_damage_*` | **노출 안 됨** |

이 외에 교전 기하(`ata_deg`, `aa_deg`, `distance_ft`, `closure_rate_kts` 등)와 BFM 분류는 모두 노출. CSV/ACMI 로그(운영자 사후 분석용)에는 `enm_health` 등이 여전히 기록되지만, 그건 BT가 실시간으로 보는 채널이 아니다.

### 1.3 피해(데미지)를 주는 방법 — WEZ (Weapon Engagement Zone)

상대에게 피해를 주려면 **Gun WEZ** 조건을 충족해야 합니다:

```
조건 1: 거리 500 ~ 3000 ft 이내
조건 2: ATA(Antenna Train Angle) < 12°  (= 기수 앞 ±12° 이내)

→ 두 조건 모두 충족 시 최대 25 HP/s × 거리계수 × 각도계수 × 0.05s (시뮬 스텝당, 20Hz)
```

> 📌 **WEZ 진입**: ATA < 12° 시 WEZ 활성화, ATA가 0°에 가깝을수록 각도계수 증가로 데미지 최대화. (`WEZ_MAX_ANGLE_DEG = 12.0` in `src/utils/units.py`)

```
        나(←기수 방향)
         ╲  ATA=5°
          ╲
           ● 적 (거리 2000ft)

   → WEZ 활성! 피해 가함
```

---

## 2. 행동 트리(Behavior Tree) 기초

### 2.1 BT란?

행동 트리는 에이전트의 의사결정을 **트리 구조**로 표현하는 방법입니다. **BT의 action 선택 tick은 10Hz (0.1초 주기, 시뮬 2스텝마다 1회)** 로 루트부터 순회하며, 현재 상황에 맞는 행동을 선택합니다. condition 노드의 `update()`는 별도로 매 시뮬 스텝(20Hz, 0.05초)마다 호출되어 짧은 이벤트를 놓치지 않습니다.

### 2.2 핵심 노드 3가지

```
┌─────────────────────────────────────────────────────┐
│  Selector (선택자)                                    │
│  "자식 중 하나라도 성공하면 성공" (OR 논리)             │
│                                                      │
│  자식1 실패 → 자식2 시도 → 자식3 시도 → ...            │
│  자식1 성공 → 즉시 성공 반환 (나머지 건너뜀)            │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Sequence (순차)                                      │
│  "모든 자식이 성공해야 성공" (AND 논리)                 │
│                                                      │
│  자식1 성공 → 자식2 시도 → 자식3 시도 → ...            │
│  자식1 실패 → 즉시 실패 반환 (나머지 건너뜀)            │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│  Leaf (잎 노드)                                       │
│                                                      │
│  Condition: 조건 확인 (거리 < 3000ft?) → 성공/실패     │
│  Action:    행동 수행 (Pursue, BreakTurn 등) → 성공    │
└─────────────────────────────────────────────────────┘
```

### 2.3 BT의 핵심 원리: 우선순위 기반 행동 선택

Selector의 자식 순서 = **우선순위**입니다.

```yaml
tree:
  type: Selector          # 위에서 아래로 순회
  children:
    - 분기1: 생존 (최우선)    # 추락 직전이면 → 무조건 상승
    - 분기2: 공격            # WEZ 조건 충족 시 → 사격
    - 분기3: 추적            # 평상시 → 적 추적
```

**매 tick(0.1초) 동작 흐름**:
1. 분기1 조건 확인 → 고도 위험? → YES → 상승 (이번 tick 끝)
2. 분기1 조건 확인 → 고도 안전? → NO → 분기2로 이동
3. 분기2 조건 확인 → WEZ 충족? → YES → 사격 (이번 tick 끝)
4. 분기2 조건 확인 → WEZ 불충족? → NO → 분기3으로 이동
5. 분기3: 기본 추적 실행 (이번 tick 끝)

---

## 3. 프로젝트 구조

### 3.1 디렉토리 구조

```
ai-combat-sdk/
│
├── src/                              # SDK 코어 (수정 불가, .pyd 컴파일)
│   ├── behavior_tree/                # BT 엔진
│   │   └── nodes/                    # 빌트인 Condition + Action 노드
│   ├── simulation/                   # JSBSim F-16 물리 모델
│   └── match/
│       └── runner.py                 # 매치 실행 API
│
├── scripts/
│   └── run_match.py                  # 매치 실행 CLI
│
├── tools/
│   ├── validate_agent.py             # 에이전트 검증 도구
│   └── test_agent.py                 # 로컬 테스트 도구
│
├── examples/                         # 내장 상대 에이전트 (6종)
│   ├── simple.yaml                   # 초보 (Pursue만 사용)
│   ├── aggressive.yaml               # 공격적 (빠른 추적)
│   ├── defensive.yaml                # 방어적 (회피 중심)
│   ├── ace.yaml                      # 최강 (BFM 상황 인식)
│   ├── eagle1/                       # 커스텀 노드 포함
│   └── viper1/                       # 커스텀 노드 포함
│
├── submissions/                      # ★ 참가자가 만드는 에이전트 ★
│   └── my_agent/
│       ├── my_agent.yaml             # BT 정의 파일
│       └── nodes/
│           ├── __init__.py
│           └── custom_actions.py     # (선택) 커스텀 액션 노드
│
├── config/
│   ├── match_config.yaml             # 매치 설정 (자동 6000스텝 = 5분 @ 20Hz, bt_vs_bt)
│   ├── match_rules.yaml              # 매치 규칙 (Hard Deck, HP 등)
│   ├── tournament_config.yaml        # 토너먼트 설정
│   └── wez_params.yaml               # WEZ 규칙 (ATA<12°, 500~3000ft)
│
├── replays/                          # 매치 리플레이 (.acmi)
└── docs/
    ├── GUIDE.md                      # (본 문서) 튜토리얼 · 전략 가이드
    ├── NODE_REFERENCE.md             # 노드 & 파라미터 레퍼런스
    ├── BLACKBOARD_REFERENCE.md       # 관측값 키 레퍼런스 (단위·부호)
    └── VSCODE_SETUP.md               # VSCode/Windsurf 환경 설정
```

### 3.2 참가자가 작성하는 파일

```
submissions/my_agent/
├── my_agent.yaml              # ← 필수: 행동 트리 정의
└── nodes/
    ├── __init__.py             # ← 빈 파일
    └── custom_actions.py       # ← 선택: 커스텀 액션 (Python)
```

**빌트인 노드만으로도 충분히 강한 에이전트를 만들 수 있습니다.** 커스텀 액션은 고급 기능입니다.

---

## 4. YAML로 BT 작성하기

### 4.1 가장 간단한 BT (simple.yaml)

```yaml
name: "my_first_agent"
description: "Hard Deck 회피 + 기본 추적"

tree:
  type: Selector
  children:
    # 1. Hard Deck 회피 (필수! 최상단 배치)
    - type: Sequence
      children:
        - type: Condition
          name: BelowHardDeck
        - type: Action
          name: ClimbTo
          params:
            target_altitude_ft: 3000

    # 2. 기본 추적
    - type: Action
      name: Pursue
```

이 BT의 동작:
- 고도가 낮으면 → 3000ft까지 상승
- 그 외에는 → 적을 추적

### 4.2 단계적 BT 개선 과정

#### Level 1: 기본 (2개 분기)

```yaml
Selector
├── HardDeckAvoidance: BelowHardDeck → ClimbTo
└── Default: Pursue
```

#### Level 2: 공격 추가 (3개 분기)

```yaml
Selector
├── HardDeckAvoidance: BelowHardDeck → ClimbTo
├── GunAttack: dist∈[500,3000] AND ATA<12° → GunAttack  # 추가! (정밀 사격 시 4° 이하 권장)
└── Default: Pursue
```

#### Level 3: 방어 추가 (4개 분기)

```yaml
Selector
├── HardDeckAvoidance: BelowHardDeck → ClimbTo
├── GunAttack: dist∈[500,3000] AND ATA<4° → GunAttack
├── Defense: IsDefensiveSituation → BreakTurn              # 추가!
└── Default: Pursue
```

#### Level 4: 상황별 전술 분화 (6개 분기) — Golden BT

```yaml
Selector
├── HardDeckAvoidance   # 생존 (최우선)
├── GunEngagement       # WEZ 내 정밀 사격
├── ThreatResponse      # 적 WEZ 감지 시 선제 회피
├── OffensivePress      # 공세 상황 압박
├── CloseCombat         # 근거리 전투
└── FarPursuit+AltAdv   # 원거리 추적 + 고도 우위
```

### 4.3 사용 가능한 주요 노드

#### 조건(Condition) 노드

| 노드 | 파라미터 | 의미 |
|------|---------|------|
| `BelowHardDeck` | `threshold_ft` | 내 고도 < 임계값 |
| `DistanceBelow` | `threshold_ft` | 적과 거리 < 임계값 |
| `DistanceAbove` | `threshold_ft` | 적과 거리 > 임계값 |
| `ATABelow` | `threshold_deg` | ATA < 임계값 (적이 전방, WEZ는 12°; 정밀 사격에는 더 좁게 설정 권장) |
| `ATAAbove` | `threshold_deg` | ATA > 임계값 (적이 측면/후방) |
| `IsOffensiveSituation` | — | 공격 유리 상황 |
| `IsDefensiveSituation` | — | 방어 필요 상황 |
| `InEnemyWEZ` | `max_distance_ft`, `max_los_angle_deg` | 적이 나를 WEZ에 포착 |
| `UnderThreat` | `aa_threshold_deg` | 위협 상황 |
| `EnemyInRange` | `max_distance_ft` | 적이 범위 내 |

#### 액션(Action) 노드 — 추적 기동

| 노드 | 설명 | 특성 |
|------|------|------|
| `Pursue` | 종합 추적 (12개 파라미터 튜닝 가능) | 안정적, 적응적 |
| `LeadPursuit` | 적의 예측 위치로 선회 | 공세적, WEZ 진입 빠름 |
| `LagPursuit` | 적의 후방 추적 | 보수적, 에너지 보존 |
| `PurePursuit` | 적의 현재 위치로 직접 추적 | 단순 |
| `GunAttack` | 정밀 조준 사격 | WEZ 내 최적화 |

#### 커스텀 노드 (예제)

**Golden BT 커스텀 액션**:
- `PNAttack`: PD 제어기 기반 정밀 사격 (kp=1.2, kd=0.5)
- `PNPursuit`: PN 강화 추적 + 에너지 관리
- `AdaptiveAction`: BFM 상황별 반응형 대응
- `EnergyRecovery`: 에너지 회복 기동

**Viper1 커스텀 액션**:
- `ViperStrike`: TAU 기반 정밀 추적
- `EnergyManeuver`: 에너지 관리 기동

**Viper1 커스텀 조건** ⭐ **신규**:
- `HighEnergyState`: 고에너지 상태 확인
- `LowEnergyState`: 저에너지 상태 확인
- `OptimalAttackPosition`: 최적 공격 위치 확인

#### 액션(Action) 노드 — 방어/기동

| 노드 | 설명 | 반환 |
|------|------|------|
| `BreakTurn` | 급선회 회피 (선회율 극대화) | SUCCESS |
| `DefensiveManeuver` | AA 기반 방어 기동 | SUCCESS |
| `HighYoYo` | Lufbery 탈출·OBFM 오버슈트 방지. 고도 에너지 저장후 속도 변환 | **RUNNING** |
| `LowYoYo` | 속도 열세 시 공격 거리 축소. 급하강+가속 후 상승 | **RUNNING** |
| `BarrelRoll` | 적 조준을 피하면서 에너지 손실 최소. 상승↔하강 선회 반복 | **RUNNING** |
| `ClimbTo` | 목표 고도로 상승 | SUCCESS |
| `AltitudeAdvantage` | 적보다 높은 고도 유지 | SUCCESS |

**고급 3차원 기동 (aircombat.pe.kr 기반)**:

| 노드 | 설명 | 반환 | `memory: true` 필요 |
|------|------|------|------|
| `Loop` | 수직 원형 기동. 상승→최고점→하강 (15 ticks) | **RUNNING** | ✅ |
| `ImmelmannTurn` | 루프 상반부 후 180° 전환. 고도↑·속도↓ (12 ticks) | **RUNNING** | ✅ |
| `SplitS` | 배면 후 급하강. 고도↓·속도↑ (10 ticks) | **RUNNING** | ✅ |
| `HammerHead` | 수직 상승→실속 직전 요잉→급하강 (15 ticks) | **RUNNING** | ✅ |
| `SliceTurn` | 에너지 손실 최소 엄니 하강 뱅크턴 | SUCCESS | - |
| `SpiralDive` | 나선형 급강하. 속도 회복, 추격 이탈 | SUCCESS | - |
| `SpiralClimb` | 나선형 상승. 에너지 우위 유지하며 고도↑ | SUCCESS | - |

### 4.4 Golden BT — 전체 YAML 예시

```yaml
name: golden
version: "2.0"
description: "Golden State v2"
tree:
  type: Selector
  children:
  # 1. Hard Deck 회피 (최우선)
  - type: Sequence
    name: HardDeckAvoidance
    children:
    - type: Condition
      name: BelowHardDeck
      params:
        threshold_ft: 4075
    - type: Action
      name: ClimbTo
      params:
        target_altitude_ft: 7792

  # 2. WEZ 내 정밀 사격
  - type: Sequence
    name: GunEngagement
    children:
    - type: Condition
      name: DistanceBelow
      params:
        threshold_ft: 3000           # WEZ 최대 거리
    - type: Condition
      name: DistanceAbove
      params:
        threshold_ft: 500            # WEZ 최소 거리
    - type: Condition
      name: ATABelow
      params:
        threshold_deg: 4.4           # 매우 정밀한 조준
    - type: Action
      name: PNAttack                 # 커스텀 PD 제어기 기반 사격

  # 3. 적 WEZ 감지 시 선제 회피
  - type: Sequence
    name: ThreatResponse
    children:
    - type: Condition
      name: InEnemyWEZ
      params:
        max_distance_ft: 2047
        max_los_angle_deg: 11.9
    - type: Action
      name: BreakTurn

  # 4. 공세 상황 압박
  - type: Sequence
    name: OffensivePress
    children:
    - type: Condition
      name: DistanceBelow
      params:
        threshold_ft: 11939
    - type: Condition
      name: IsOffensiveSituation
    - type: Action
      name: Pursue

  # 5. 근거리 전투
  - type: Sequence
    name: CloseCombat
    children:
    - type: Condition
      name: DistanceBelow
      params:
        threshold_ft: 13123
    - type: Action
      name: LeadPursuit

  # 6. 원거리 — 추적 + 고도 우위
  - type: Parallel
    name: FarPursuitWithAltitude
    policy: SuccessOnOne
    children:
    - type: Action
      name: LeadPursuit
    - type: Action
      name: AltitudeAdvantage
      params:
        target_advantage_ft: 1857
```

---

## 5. 튜토리얼: 다운로드부터 첫 승리까지

### 5.1 Step 1 — 프로젝트 다운로드

```bash
# Git으로 SDK 다운로드
git clone https://github.com/rokafa-daslab/ai-combat-sdk.git

# 프로젝트 폴더로 이동
cd ai-combat-sdk
```

> Git이 없다면 GitHub 페이지에서 **Code → Download ZIP** 으로 다운로드 후 압축 해제

### 5.2 Step 2 — Python 환경 설정

**Python 3.14** 이 필요합니다. (SDK의 .pyd 컴파일 파일이 3.14 전용)

```bash
# Python 버전 확인
python --version
# → Python 3.14.x 가 나와야 합니다

# 가상환경 생성 (권장)
python -m venv .venv

# 가상환경 활성화
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Windows (cmd):
.venv\Scripts\activate.bat
# Linux / Mac:
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.txt
```

**설치되는 주요 패키지:**

| 패키지 | 용도 |
|--------|------|
| `jsbsim` | F-16 비행역학 시뮬레이터 |
| `py_trees` | 행동 트리 엔진 |
| `torch` | 저수준 정책 실행 (SDK 내부) |
| `gymnasium` | 시뮬레이션 환경 인터페이스 |
| `pyyaml` | YAML 파일 파싱 |

> ⚠️ **시스템 요구사항**: Python 3.14 필수

### 5.3 Step 3 — 내 에이전트 폴더 만들기

```bash
# submissions 폴더 안에 내 에이전트 폴더 생성
mkdir -p submissions/my_agent/nodes
```

**폴더 구조:**

```
submissions/
└── my_agent/                    ← 폴더명 = 에이전트 이름
    ├── my_agent.yaml            ← 행동 트리 정의 (필수)
    └── nodes/
        └── __init__.py          ← 빈 파일 (필수)
```

`__init__.py` 는 빈 파일로 만듭니다:

```bash
# Windows
type nul > submissions/my_agent/nodes/__init__.py
# Linux / Mac
touch submissions/my_agent/nodes/__init__.py
```

### 5.4 Step 4 — 첫 번째 BT 작성

`submissions/my_agent/my_agent.yaml` 파일을 텍스트 에디터로 만듭니다:

```yaml
name: "my_agent"
description: "나의 첫 번째 AI 전투기"

tree:
  type: Selector
  children:
    # 1. Hard Deck 회피 (필수! 최상단 배치)
    - type: Sequence
      children:
        - type: Condition
          name: BelowHardDeck
        - type: Action
          name: ClimbTo
          params:
            target_altitude_ft: 3000

    # 2. 기본 추적
    - type: Action
      name: Pursue
```

### 5.5 Step 5 — 에이전트 검증

매치를 돌리기 전에, YAML에 오류가 없는지 검증합니다:

```bash
python tools/validate_agent.py submissions/my_agent/my_agent.yaml
```

**성공 시 출력:**

```
✅ 검증 통과: my_agent
  - 노드 수: 4
  - 조건 노드: 1
  - 액션 노드: 2
```

**실패 시 출력 예시:**

```
❌ 검증 실패: my_agent
  - 알 수 없는 노드: "Purseu"  (오타!)
  - 라인 15: params에 잘못된 키: "threshold"  → "threshold_ft" 사용
```

### 5.6 Step 6 — 첫 매치 실행

```bash
# 가장 약한 상대 (simple)와 1라운드
python scripts/run_match.py --agent1 my_agent --agent2 simple
```

**출력 예시:**

```
======================================================================
  AI Combat Match
======================================================================

Agent 1: my_agent
Agent 2: simple
Rounds: 1
Scenario: bt_vs_bt

my_agent vs simple
매치 시작:
  Tree 1: my_agent.yaml -> ego_id=A0100
  Tree 2: simple.yaml -> enm_id=B0100
  Config: 1v1/NoWeapon/bt_vs_bt
  Max steps: 6000
  Health: 100.0 HP each

  ... (스텝별 로그) ...

매치 완료:
  승자: my_agent [health_adv]     ← 내가 이겼다!
  스텝: 6000 / 6000
  소요 시간: 5.80초
  my_agent: 100.0 HP              ← 내 남은 체력
  simple: 84.1 HP                 ← 상대 남은 체력
  리플레이: 20260305_143022_my_agent_vs_simple.acmi

======================================================================
🎉 매치 완료!
======================================================================
```

### 5.7 Step 7 — 로깅 옵션 활용

매치 실행 시 **로깅 옵션**을 사용하면 전투 데이터를 수집하여 분석할 수 있습니다:

```bash
# 전체 데이터 CSV 로그 저장 (45개 컬럼)
python scripts/run_match.py --agent1 my_agent --agent2 simple --log-csv
```

**로그 파일 위치**: `logs/YYYYMMDD_HHMMSS_agent1_vs_agent2.csv`

**콘솔 실시간 출력 예시** (verbose 모드):
```
[   1] A0100 | BFM=HABFM | HP=100.0/100.0 | Dmg= 0.0 | WEZ=False | Dist=3298m ATA= 90.0deg | Node=LeadPursuit
[  50] A0100 | BFM=OBFM  | HP=100.0/ 95.3 | Dmg= 4.7 | WEZ=True  | Dist= 850m ATA=  3.2deg | Node=PNAttack
[ 100] A0100 | BFM=DBFM  | HP= 92.1/100.0 | Dmg= 0.0 | WEZ=False | Dist=1200m ATA= 65.0deg | Node=BreakTurn
```

> 💡 **Tip**: 로그를 수집하면 어느 시점에 WEZ 진입했는지, 어떤 노드가 실행되었는지 정확히 파악할 수 있습니다!

### 5.8 Step 8 — 여러 상대와 대전

```bash
# 3라운드씩 여러 상대와 테스트 (로그 수집)
python scripts/run_match.py --agent1 my_agent --agent2 simple --rounds 3 --log-csv
python scripts/run_match.py --agent1 my_agent --agent2 aggressive --rounds 3 --log-csv
python scripts/run_match.py --agent1 my_agent --agent2 defensive --rounds 3 --log-csv
python scripts/run_match.py --agent1 my_agent --agent2 ace --rounds 3 --log-csv
python scripts/run_match.py --agent1 my_agent --agent2 eagle1 --rounds 3 --log-csv
python scripts/run_match.py --agent1 my_agent --agent2 viper1 --rounds 3 --log-csv
```

**상대 난이도 (약한 순):**

| 상대 | 난이도 | 전략 특성 |
|------|--------|----------|
| `simple` | ★☆☆☆☆ | Hard Deck 회피 + Pursue만 |
| `defensive` | ★★☆☆☆ | 방어 위주, 회피 많음 |
| `aggressive` | ★★★☆☆ | 매우 공격적, 빠른 추적 |
| `eagle1` | ★★★★☆ | 커스텀 노드 사용 |
| `viper1` | ★★★★☆ | 커스텀 노드 사용 |
| `ace` | ★★★★★ | BFM 상황 인식 + 에너지 관리 |

### 5.9 Step 9 — 리플레이로 전투 분석

매치마다 `replays/` 폴더에 `.acmi` 파일이 저장됩니다.

**TacView 뷰어로 3D 재생:**

1. [TacView](https://www.tacview.net/) 다운로드 (무료 버전 사용 가능)
2. `replays/` 폴더의 `.acmi` 파일을 TacView로 열기
3. 3D로 전투 상황을 재생하며 분석

**분석 포인트:**
- 어느 시점에 WEZ에 진입했는가?
- 상대는 어떻게 회피했는가?
- Hard Deck 근처에서 어떤 일이 발생했는가?
- 어느 분기(행동)가 발동되었는가?

### 5.10 Step 10 — 로그 데이터 분석

로그 파일을 분석하여 BT 성능을 정량적으로 평가할 수 있습니다:

**주요 분석 지표**:

| 지표 | 확인 방법 | 의미 |
|------|----------|------|
| **WEZ 진입률** | `in_wez=True` 비율 | 공격 기회 확보 능력 |
| **데미지 효율** | `ego_damage_dealt` 증가 속도 | 실제 타격 성공률 |
| **BFM 상황 분포** | `OBFM/DBFM/HABFM` 비율 | 전투 주도권 |
| **활성 노드 빈도** | `active_node` 분포 | 실제 실행된 전술 |
| **평균 교전 거리** | `distance` 평균값 | 전투 스타일 |

**Excel/Python으로 분석**:
```python
import pandas as pd

# CSV 로드
df = pd.read_csv('logs/20260312_083135_eagle1_vs_golden_callback.csv')

# 내 에이전트만 필터링
my_data = df[df['agent_id'] == 'A0100']

# WEZ 진입률 계산
wez_rate = my_data['in_wez'].sum() / len(my_data) * 100
print(f"WEZ 진입률: {wez_rate:.1f}%")

# 평균 데미지
avg_damage = my_data['ego_damage_dealt'].max()
print(f"총 데미지: {avg_damage:.1f} HP")

# 가장 많이 실행된 노드
print(my_data['active_node'].value_counts())
```

### 5.11 Step 11 — BT 개선 반복

```
┌──────────────┐
│  BT 수정      │ ← YAML 파일 편집
└──────┬───────┘
       ▼
┌──────────────┐
│  검증         │ ← validate_agent.py
└──────┬───────┘
       ▼
┌──────────────┐
│  매치 실행    │ ← run_match.py
└──────┬───────┘
       ▼
┌──────────────┐
│  결과 분석    │ ← 승패, HP, 리플레이 확인
└──────┬───────┘
       │ 개선 아이디어
       └─────────→ 처음으로 돌아가기
```

**개선 방향 예시:**
- simple에게 지면 → Hard Deck 분기 확인, 기본 추적 변경
- aggressive에게 지면 → WEZ 사격 분기 추가, 방어 분기 추가
- ace에게 지면 → 상황별 전술 분화, 파라미터 정밀 조정

### 5.12 Step 12 — 제출

```bash
# 제출용 zip 파일 생성
# Windows (PowerShell)
Compress-Archive -Path submissions/my_agent -DestinationPath my_agent.zip

# Linux / Mac
cd submissions && zip -r ../my_agent.zip my_agent/ && cd ..
```

**zip 구조 확인:**

```
my_agent.zip
└── my_agent/
    ├── my_agent.yaml           # 행동 트리 정의
    └── nodes/
        ├── __init__.py
        └── custom_actions.py   # (선택사항)
```

> 리더보드 제출: https://ai-pilot.boramae.club/leaderboard

### 5.13 자주 발생하는 오류와 해결

| 오류 메시지 | 원인 | 해결 |
|------------|------|------|
| `ModuleNotFoundError: No module named 'py_trees'` | 의존성 미설치 | `pip install -r requirements.txt` |
| `Behavior tree file not found: my_agent` | 경로 오류 | `submissions/my_agent/my_agent.yaml` 확인 |
| `unexpected keyword argument 'threshold'` | 파라미터명 오류 | `threshold_ft`, `threshold_deg` 등 접미사 확인 |
| `__init__() got an unexpected keyword argument` | 노드에 없는 파라미터 | `docs/NODE_REFERENCE.md` 참조 |
| `Hard Deck 위반 — 즉시 패배` | BelowHardDeck 분기 누락 | BT 최상단에 Hard Deck 회피 추가 |
| `Python version mismatch` | Python 3.14가 아님 | Python 3.14 설치 후 재시도 |

---

## 6. 승리하는 BT를 만드는 전략

### 6.1 핵심 원칙

```
원칙 1: Hard Deck 회피는 최상단에 (즉사 방지)
원칙 2: 단순한 BT가 복잡한 BT를 이긴다 (17노드 > 51노드)
원칙 3: WEZ 조건을 정밀하게 설정하라 (넓으면 헛사격, 좁으면 기회 놓침)
원칙 4: 상대를 Hard Deck으로 유도하는 것이 가장 효과적
원칙 5: 방어보다 공격이 유리 (BreakTurn은 필요할 때만)
```

### 6.2 단계별 개선 접근

#### Step 1: 기본 에이전트 작성

```yaml
tree:
  type: Selector
  children:
    - type: Sequence
      children:
        - type: Condition
          name: BelowHardDeck
        - type: Action
          name: ClimbTo
          params:
            target_altitude_ft: 3000
    - type: Action
      name: Pursue
```

→ 먼저 simple, defensive를 이길 수 있는지 확인

#### Step 2: WEZ 사격 분기 추가

```yaml
    # HardDeck 분기 바로 아래에 추가
    - type: Sequence
      children:
        - type: Condition
          name: DistanceBelow
          params:
            threshold_ft: 3000
        - type: Condition
          name: DistanceAbove
          params:
            threshold_ft: 500
        - type: Condition
          name: ATABelow
          params:
            threshold_deg: 4           # WEZ 범위는 12°이나 정밀 사격에는 좁게 설정
        - type: Action
          name: GunAttack
```

→ 적을 추적하다가 WEZ 조건 충족 시 정밀 사격으로 전환

#### Step 3: 상황별 전술 분화

- `IsOffensiveSituation` → 적극적 추적 (`LeadPursuit`)
- `IsDefensiveSituation` → 회피 (`BreakTurn`)
- 거리별 다른 추적 방법 사용

#### Step 4: 파라미터 튜닝

- `threshold_ft` 값 조정 (언제 어떤 분기가 발동하는지)
- `Pursue` 노드의 12개 파라미터 튜닝
- 여러 상대를 대상으로 반복 테스트

### 6.3 우리가 발견한 핵심 사실

| 발견 | 설명 |
|------|------|
| **Pursue > LeadPursuit** (급기동 상대) | 예측 위치 추적 시 오버슈트 발생 → Pursue가 안정적 |
| **Hard Deck 유도 = 최고의 전술** | 공격적 추격 시 상대가 1000ft 이하 진입 → 즉사 |
| **InEnemyWEZ → BreakTurn 핵심** | 적의 WEZ에 잡히기 전에 선제 회피 → 생존율 결정적 향상 |
| **거리 3639m 공세 전환** | 이 거리에서 Pursue로 전환하면 ace/aggressive 동시 격파 |
| **고도 우위 병행** | 원거리에서 AltitudeAdvantage 병행 → 에너지 우위 확보 |
| **단순함이 최강** | 6개 분기 BT가 8개 분기 BT보다 강함 (불필요한 방어 분기 제거) |

---

## 7. 공중전 용어 정리

### 7.1 각도 개념

```
            적기 ●
           ╱    ╲
     ATA  ╱      ╲  AA
         ╱        ╲
  나(→) ●──────────● 적(←)
        기수방향     기수방향

ATA (Antenna Train Angle):
  0°  = 적이 내 정면 (조준 가능!)
  90° = 적이 내 측면
  180°= 적이 내 후방

AA (Aspect Angle):
  0°  = 내가 적의 후방에 위치 (유리!)
  90° = 내가 적의 측면에 위치
  180°= 내가 적의 정면에 위치 (위험!)
```

### 7.2 BFM (Basic Fighter Maneuvers)

| 상황 | 조건 | 권장 전술 |
|------|------|---------|
| **OBFM** (공격 유리) | ATA 작음, AA 작음 | LeadPursuit, GunAttack |
| **DBFM** (방어 필요) | AA 큼, 적이 후방 추격 | BreakTurn, DefensiveManeuver |
| **HABFM** (정면 대등) | 정면 교차 상황 | OneCircleFight, HighYoYo |

### 7.3 추적 기동 비교

```
        LeadPursuit (선도 추적)
        ╱ 적의 미래 위치를 향해 선회
       ╱  → 빠른 WEZ 진입
      ╱   → 급기동 시 오버슈트 위험
     ●

        PurePursuit (순수 추적)
        │ 적의 현재 위치를 향해 직진
        │ → 안정적이나 느린 수렴
        │
        ●

        LagPursuit (지연 추적)
         ╲ 적의 후방을 향해 선회
          ╲→ 에너지 보존
           ╲→ 보수적, 기동 여유
            ●
```

---

## 8. 검증 체크리스트

### 8.1 제출 전 검증 순서

```
□ Step 1: YAML 문법 검증
  $ python tools/validate_agent.py submissions/my_agent/my_agent.yaml

□ Step 2: simple 상대 테스트 (3라운드)
  $ python scripts/run_match.py --agent1 my_agent --agent2 simple --rounds 3
  → 목표: 3승

□ Step 3: aggressive 상대 테스트
  $ python scripts/run_match.py --agent1 my_agent --agent2 aggressive --rounds 3
  → 목표: 2승 이상

□ Step 4: ace 상대 테스트 (가장 강한 상대)
  $ python scripts/run_match.py --agent1 my_agent --agent2 ace --rounds 3
  → 목표: 2승 이상

□ Step 5: 전체 상대 종합 테스트
  → 6종 × 3라운드 = 18매치
  → 목표: 15승 이상
```

### 8.2 흔한 실수와 해결

| 실수 | 증상 | 해결 |
|------|------|------|
| Hard Deck 분기 누락 | 자기가 추락해서 패배 | 최상단에 BelowHardDeck 추가 |
| WEZ 조건 너무 넓음 | ATA 30° 등 → 명중률 저하 | ATA 4° 이하로 정밀 조준 |
| 방어 분기 과다 | 회피만 하다가 무승부 | 방어 분기 축소, 공격 우선 |
| 파라미터 이름 오타 | 매치 실행 시 에러 | validate_agent.py로 사전 검증 |
| 분기 순서 잘못 | 낮은 우선순위 행동이 먼저 발동 | 생존 > 공격 > 방어 > 기본 순서 |

### 8.3 제출 파일 구조

```
my_agent.zip
└── my_agent/
    ├── my_agent.yaml           # 행동 트리 정의
    └── nodes/                   # (선택) 커스텀 노드
        ├── __init__.py
        └── custom_actions.py
```

---

## 9. 고급: 커스텀 노드 (Python)

빌트인 노드로 부족할 때, Python으로 직접 **액션 또는 조건 노드**를 만들 수 있습니다.

### 9.1 커스텀 액션 노드 기본 구조 — `BaseAction` (단일 tick 완료형)

```python
import py_trees
from src.behavior_tree.nodes.actions import BaseAction

class MyAction(BaseAction):
    def __init__(self, name="MyAction", my_param=100.0):
        super().__init__(name)
        self.my_param = my_param
    
    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            
            # 관측값 추출
            distance = obs.get("distance_ft", 10000.0)
            ata_deg = obs.get("ata_deg", 90.0)  # 실제 도(°) 단위, 0°~180°
            
            # 로직 구현
            if distance < self.my_param:
                delta_altitude_idx = 3  # 상승
                delta_heading_idx = 4   # 직진
                delta_velocity_idx = 2  # 유지
            else:
                delta_altitude_idx = 2
                delta_heading_idx = 6
                delta_velocity_idx = 3
            
            self.set_action(delta_altitude_idx, delta_heading_idx, delta_velocity_idx)
            return py_trees.common.Status.SUCCESS
            
        except Exception as e:
            # 실패 시 기본 동작
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE
```

### 9.2 커스텀 액션 노드 — `TimedAction` (장기 기동, N tick RUNNING)

N tick 동안 RUNNING을 반환하고 완료 시 SUCCESS를 자동 제어하는 베이스 클래스입니다.

```python
from src.behavior_tree.nodes.actions import TimedAction

class CustomImmelmann(TimedAction):
    def __init__(self, name="CustomImmelmann", duration_steps=14):
        super().__init__(name=name, duration_steps=duration_steps)

    def on_start(self):
        """tick 0 시작 시 1회 호출 — blackboard 접근 가능 (선택)"""
        self._side = self.blackboard.observation.get("side_flag", 0)

    def execute(self, step: int, total: int):
        """tick마다 호출 — set_action() 반드시 호출 (필수). step: 1~total"""
        if step <= int(total * 0.6):
            self.set_action(4, 4, 1)           # 급상승 + 직진 + 감속
        else:
            turn = 1 if self._side < 0 else 7
            self.set_action(4, turn, 2)         # 급상승 유지 + 선회

    def on_finish(self, status):
        """SUCCESS 또는 외부 중단(INVALID) 시 1회 호출 (선택)"""
        pass
```

**YAML 사용 시 `memory: true` 필수:**

```yaml
- type: Sequence
  name: ImmelmannBranch
  params:
    memory: true          # ← RUNNING 동안 조건 재검사 안 함
  children:
    - type: Condition
      name: IsAltAdvantage  # 기동 진입 조건
    - type: Action
      name: CustomImmelmann # RUNNING 반환 — 완료까지 Sequence 유지
      params:
        duration_steps: 14
```

| 메서드 | 필수 | 설명 |
|-------|------|------|
| `execute(step, total)` | **필수** | 매 tick 실행. `step`은 1부터 시작 |
| `on_start()` | 선택 | 기동 시작 시 1회 초기화 |
| `on_finish(status)` | 선택 | `SUCCESS`(완료) 또는 `INVALID`(외부 중단) 시 정리 |

> 📐 **duration_steps 단위 — 자동 환산됨**
>
> `duration_steps`는 **5 Hz 기준**(REFERENCE_TICK_HZ)으로 작성한다. 실제 BT tick rate(10 Hz)에 따라 베이스 클래스가 자동으로 비례 환산하므로, `duration_steps: 15`는 어느 tick rate에서도 동일한 **실 시간 3초**를 의미한다. 즉:
> - `duration_steps: 5` → 1초
> - `duration_steps: 15` → 3초
> - `duration_steps: 25` → 5초
>
> 따라서 기존에 작성된 YAML / 커스텀 노드는 코드 변경 없이 그대로 동작한다. 시간 계층의 전체 그림은 §12.1 참조.

### 9.3 커스텀 조건 노드 ⭐ **신규**

**커스텀 condition은 반드시 `BaseCondition`을 상속한다.** 이렇게 해야 runner의 20 Hz subtick(매 env.step 직후) 대상으로 자동 등록되어 짧은 이벤트를 놓치지 않는다. 자세한 활용 예시는 §12.3 참조.

```python
import py_trees
from src.behavior_tree.nodes.conditions import BaseCondition

class OptimalAttackPosition(BaseCondition):
    """최적 공격 위치 확인 (stateless 조건)

    조건:
    - 거리: 800m ~ 2500m
    - ATA: < 30도
    - 고도 우위: > 0m
    """

    def __init__(self, name="OptimalAttackPosition"):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(key="observation", access=py_trees.common.Access.READ)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            distance = obs.get("distance_ft", 10000.0)  # ft
            ata_deg = obs.get("ata_deg", 180.0)  # 실제 도(°) 단위
            alt_gap_ft = obs.get("alt_gap_ft", 0.0)  # ft

            # 조건 검사
            if 2625 < distance < 8202 and abs(ata_deg) < 30 and alt_gap_ft > 0:
                return py_trees.common.Status.SUCCESS
            else:
                return py_trees.common.Status.FAILURE

        except Exception as e:
            return py_trees.common.Status.FAILURE
```

> 💡 **stateful condition(edge detection / debounce / hysteresis)을 만들고 싶다면** — `update()` 안에 인스턴스 변수로 직전 값/카운터를 보관한다. runner가 20 Hz로 `update()`를 호출하므로 50 ms 간격 이벤트도 포착 가능. 완전한 예시(WEZ 진입 edge, ATA 통과 카운터)는 §12.3 참조.

### 9.4 PD 제어기 패턴 (Golden BT 핵심)

Golden BT는 **PD 제어기**를 사용하여 정밀 제어를 구현합니다:

```python
def _heading_pd(tau_deg: float, tau_rate: float,
                kp: float = 0.8, kd: float = 0.3) -> int:
    """PD controller on tau: proportional + derivative."""
    cmd = kp * tau_deg + kd * tau_rate  # P항 + D항
    idx = int(round(cmd / 22.5)) + 4     # -90°~+90° → 0~8
    return max(0, min(8, idx))

class PNAttack(BaseAction):
    """WEZ 내 정밀 사격 (PD 제어기)"""
    
    def __init__(self, name="PNAttack", kp=1.2, kd=0.5):
        super().__init__(name)
        self.kp = kp
        self.kd = kd
        self.prev_tau = None
    
    def update(self) -> py_trees.common.Status:
        obs = self.blackboard.observation
        
        tau = obs.get("tau_deg", 0.0)  # 실제 도(°) 단위, -180°~180°
        distance = obs.get("distance_ft", 3000.0)
        
        # TAU 변화율 계산 (D항)
        if self.prev_tau is not None:
            tau_rate = (tau - self.prev_tau) / 0.1  # dt=0.1s (BT tick 주기, 10Hz)
            heading_idx = _heading_pd(tau, tau_rate, self.kp, self.kd)
        else:
            heading_idx = int(round(tau * self.kp / 22.5)) + 4
        
        self.prev_tau = tau
        
        # 거리별 속도 제어
        if distance < 500:
            delta_vel = 0  # 감속
        elif distance < 1312:
            delta_vel = 1
        else:
            delta_vel = 2
        
        self.set_action(2, heading_idx, delta_vel)
        return py_trees.common.Status.SUCCESS
```

**PD 제어기 장점**:
- **P항** (kp × tau): 현재 오차 보정
- **D항** (kd × tau_rate): 변화율 예측 → 오버슈트 방지
- **결과**: 더 정밀한 조준, 더 높은 WEZ 진입률

### 9.5 에너지 상태 계산

```python
class HighEnergyState(py_trees.behaviour.Behaviour):
    """고에너지 상태 확인
    
    에너지 = 고도 + 속도² / 20
    (간단한 비에너지 근사)
    """
    
    def __init__(self, name="HighEnergyState", threshold_ft=12000):
        super().__init__(name)
        self.threshold = threshold_ft  # ft (에너지 근사값)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(key="observation", access=py_trees.common.Access.READ)
    
    def update(self) -> py_trees.common.Status:
        obs = self.blackboard.observation
        velocity = obs.get("ego_vc_kts", 389.0)  # kts
        altitude = obs.get("ego_altitude_ft", 16404.0)  # ft
        
        energy = altitude + (velocity ** 2) / 20
        
        if energy > self.threshold:
            return py_trees.common.Status.SUCCESS
        else:
            return py_trees.common.Status.FAILURE
```

### 9.6 단위 변환 주의

**관측값 단위**:
- **거리**: `ft` (feet) - `distance_ft`, `alt_gap_ft`
- **속도**: `kts` (knots) - `ego_vc_kts`, `closure_rate_kts`
- **각도**: `도(°)` - `ata_deg`, `tau_deg` → **변환 없이** 바로 사용

**변환 예시**:
```python
# 각도: 실제 도(°) 단위로 저장됨 (변환 불필요)
ata_deg = obs.get("ata_deg", 0.0)    # 0°~180°
tau_deg = obs.get("tau_deg", 0.0)    # -180°~180°

# 거리 변환 (m → ft)
distance_m = 1000
distance_ft = distance_m * 3.28084  # 3280.84 ft

# 속도 변환 (m/s → kts)
velocity_ms = 200
velocity_kts = velocity_ms * 1.94384  # 388.77 kts
```

### 9.7 YAML에서 커스텀 노드 사용

```yaml
tree:
  type: Selector
  children:
    # 커스텀 조건 노드
    - type: Sequence
      children:
        - type: Condition
          name: OptimalAttackPosition  # ← custom_conditions.py의 클래스명
        - type: Action
          name: PNAttack               # ← custom_actions.py의 클래스명
          params:
            kp: 1.2
            kd: 0.5
    
    # 커스텀 액션 노드
    - type: Action
      name: ViperStrike
      params:
        close_range: 1000.0
        wez_range: 600.0
```

### 9.8 파일 구조

```
submissions/my_agent/
├── my_agent.yaml
└── nodes/
    ├── __init__.py
    ├── custom_actions.py    # 커스텀 액션 노드
    └── custom_conditions.py # 커스텀 조건 노드 (선택사항)
```

### 9.9 실제 예제 참고

**Golden BT**: `submissions/golden/nodes/custom_actions.py`
- PNAttack, PNPursuit, AdaptiveAction, EnergyRecovery

**Viper1**: `examples/viper1/nodes/`
- `custom_actions.py`: ViperStrike, EnergyManeuver
- `custom_conditions.py`: HighEnergyState, LowEnergyState, OptimalAttackPosition

> 💡 **Tip**: Golden BT의 PD 제어기 패턴을 참고하면 더 정밀한 제어를 구현할 수 있습니다!

---

## 10. 개발 도구 활용

### 10.1 에이전트 일괄 테스트 (test_agent.py)

여러 상대와 자동으로 대전하여 통계를 수집합니다:

#### 기본 사용법

```bash
# 단일 상대와 테스트
python tools/test_agent.py my_agent --opponent ace --rounds 3

# 모든 상대와 자동 테스트 (6종)
python tools/test_agent.py my_agent --all-opponents --rounds 3

# CSV 로깅 포함 (추천!)
python tools/test_agent.py my_agent --all-opponents --rounds 3 --log-csv
```

#### 주요 옵션

| 옵션 | 설명 | 예시 |
|------|------|------|
| `--opponent` | 특정 상대 지정 | `--opponent ace` |
| `--all-opponents` | 6종 상대 모두와 대전 | `--all-opponents` |
| `--rounds` | 라운드 수 | `--rounds 5` |
| `--log-csv` | CSV 로그 저장 | `--log-csv` 또는 `--log-csv my_logs` |
| `--verbose` | 상세 출력 | `--verbose` |

#### 출력 예시

```
🎮 에이전트 테스트 시작
   에이전트: my_agent
   라운드: 3
   CSV 로그: logs/

============================================================
🎯 vs simple
============================================================

--- Round 1/3 ---
[   1] A0100 | BFM=HABFM | HP=100.0/100.0 | ...
✅ 승리!

--- Round 2/3 ---
✅ 승리!

--- Round 3/3 ---
✅ 승리!

📊 vs simple: 3W / 0D / 0L

============================================================
🎯 vs aggressive
============================================================
...

============================================================
🏆 최종 결과
============================================================

에이전트: my_agent

vs simple      : 3W / 0D / 0L
vs aggressive  : 2W / 1D / 0L
vs defensive   : 3W / 0D / 0L
vs ace         : 1W / 0D / 2L
vs eagle1      : 2W / 0D / 1L
vs viper1      : 1W / 1D / 1L

📊 전체: 12W / 2D / 4L (승률: 66.7%)
============================================================
```

#### 장점

✅ **자동화**: 6종 상대와 자동으로 대전
✅ **로깅 통합**: `--log-csv`로 CSV 저장, 콘솔에 실시간 출력
✅ **통계 요약**: 상대별 전적 및 전체 승률 자동 계산
✅ **파이프라이닝 불필요**: 결과가 콘솔에 바로 출력되므로 `> output.txt` 필요 없음

### 10.2 고급 개발 워크플로우

#### 워크플로우 1: 반복 개선

```bash
# 1. 현재 BT 성능 측정 (로그 포함)
python tools/test_agent.py my_agent --all-opponents --rounds 5 --log-csv baseline

# 2. 약점 분석 (로그 파일 확인)
# baseline/ 폴더의 CSV 파일들을 Python/Excel로 분석

# 3. 파라미터 수정 후 재테스트
python tools/test_agent.py my_agent --all-opponents --rounds 5 --log-csv improved

# 4. 결과 비교
# baseline/ vs improved/ 폴더의 로그 비교
```

#### 워크플로우 2: 상대별 특화 전략

```bash
# 1. 모든 상대와 대전하며 상세 로그 수집 (한 번에!)
python tools/test_agent.py my_bt --all-opponents --rounds 5 --log-csv analysis

# 2. 로그 분석하여 상대별 약점 파악
# analysis/ 폴더의 CSV 파일들을 Python/Excel로 분석

# 3. 상대 특성에 맞는 분기 추가
# (예: ace 상대로는 에너지 관리 강화, aggressive 상대로는 방어 강화)

# 4. 전체 재검증
python tools/test_agent.py my_agent --all-opponents --rounds 10 --log-csv
```

### 10.3 성능 분석 팁

**로그 데이터로 확인할 핵심 지표**:

1. **WEZ 진입률**: `in_wez=True` 비율이 높을수록 공격 기회가 많음
2. **데미지 효율**: 같은 WEZ 진입률에서 더 많은 데미지를 주는가?
3. **BFM 상황 분포**: OBFM(공격) > HABFM(중립) > DBFM(방어) 순으로 유리
4. **평균 교전 거리**: WEZ 범위(500-3000ft) 내에서 유지되는가?
5. **활성 노드 빈도**: 의도한 전술이 실제로 실행되는가?

**상대별 승률 목표**:
- simple: 100% (필수)
- defensive, aggressive: 80% 이상
- eagle1, viper1: 60% 이상
- ace: 50% 이상 (가장 어려움)

---

## 10.5 Golden State 학습 방법론

### 핵심 아이디어

SDK의 **상세 로깅 데이터**를 활용하여 **Golden BT의 성공 패턴**을 학습하고, 이를 자신의 BT 개발에 적용합니다.

#### 왜 SDK 로깅이 적합한가?

SDK CSV 로그는 **49개 컬럼**의 풍부한 데이터를 제공합니다:

**핵심 데이터**:
- `active_node`, `active_nodes_path`: **BT 실행 경로** (어떤 노드가 실행되었는가)
- `distance_ft`, `ata_deg`, `aa_deg`: **전술 기하** (교전 상황)
- `specific_energy_ft`, `energy_diff_ft`: **에너지 관리** (우위 판단)
- `in_wez`, `enm_in_wez`: **WEZ 상태** (공격/방어 기회)
- `bfm_situation`: **BFM 분류** (OBFM/DBFM/HABFM)
- `ata_lead_deg`, `tau_lead_deg`: **미래 예측** (1초 후 상태)

**기존 콜백 로그 대비 장점**:
- ✅ **BT 구조 친화적**: 전체 노드 경로 추적 가능
- ✅ **세부 전술 데이터**: 비행역학, 에너지, 전술 기하 모두 포함
- ✅ **시계열 패턴**: 노드 전환 시퀀스 분석 가능

### 학습 프레임워크

**(time, node, action) → golden_state** 상관관계 학습:

```
시간(step) + 활성 노드(active_node) + 액션(action)
    ↓
Golden State (성공적인 전투 상태)
```

**Golden State 정의**:
1. WEZ 진입 성공: `in_wez=True` + `ego_damage_dealt` 증가
2. 에너지 우위: `energy_advantage=True`
3. BFM 우위: `bfm_situation=OBFM`
4. 안전한 위치: `enm_in_wez=False`

### 실전 활용 워크플로우

#### Step 1: Golden BT 로그 수집

```bash
# Golden BT vs 모든 상대 (충분한 샘플)
python tools/test_agent.py golden --all-opponents --rounds 20 --log-csv golden_analysis

# 특정 상대 집중 분석
python scripts/run_match.py --agent1 golden --agent2 ace --rounds 50 --log-csv golden_vs_ace
```

#### Step 2: 패턴 분석

```python
import pandas as pd
import glob

# 모든 Golden BT 로그 로드
golden_files = glob.glob("golden_analysis/*_golden_vs_*.csv")
all_data = []

for file in golden_files:
    df = pd.read_csv(file)
    golden_data = df[df['tree_name'] == 'golden']
    all_data.append(golden_data)

golden_df = pd.concat(all_data, ignore_index=True)

# Golden State 추출: WEZ 진입 성공 순간
golden_states = golden_df[
    (golden_df['in_wez'] == True) &
    (golden_df['ego_damage_dealt'] > 0) &
    (golden_df['enm_in_wez'] == False)
]

print(f"Golden State 발견: {len(golden_states)} / {len(golden_df)} ({len(golden_states)/len(golden_df)*100:.1f}%)")

# 어떤 노드가 Golden State를 만드는가?
node_success = golden_states['active_node'].value_counts()
print("\n=== Golden State를 만드는 노드 Top 5 ===")
print(node_success.head(5))

# Golden State의 평균 전술 파라미터
print("\n=== Golden State 평균 파라미터 ===")
print(f"평균 거리: {golden_states['distance_ft'].mean():.0f} ft")
print(f"평균 ATA: {golden_states['ata_deg'].mean():.1f}°")
print(f"평균 에너지 차이: {golden_states['energy_diff_ft'].mean():.0f} ft")
```

**출력 예시**:
```
Golden State 발견: 1,847 / 18,000 (10.3%)

=== Golden State를 만드는 노드 Top 5 ===
PNAttack        632  (34.2%)
LeadPursuit     526  (28.5%)
Pursue          290  (15.7%)
GunAttack       215  (11.6%)
HighYoYo        184  (10.0%)

=== Golden State 평균 파라미터 ===
평균 거리: 782 ft
평균 ATA: 3.8°
평균 에너지 차이: +1,234 ft
```

#### Step 3: 학습된 패턴을 BT에 적용

**발견된 패턴**:
- PNAttack은 거리 600-900ft, ATA < 5°에서 최고 효율
- LeadPursuit → PNAttack 전환이 가장 성공적
- 에너지 우위 유지가 WEZ 진입률 2배 향상

**BT 설계에 반영**:
```yaml
tree:
  type: Selector
  children:
    # Golden Pattern 1: 정밀 사격 (PNAttack)
    - type: Sequence
      children:
        - type: Condition
          name: DistanceBelow
          params:
            threshold_ft: 900  # Golden State 평균 782ft + 여유
        - type: Condition
          name: DistanceAbove
          params:
            threshold_ft: 600
        - type: Condition
          name: ATABelow
          params:
            threshold_deg: 5  # Golden State 평균 3.8° + 여유
        - type: Action
          name: PNAttack  # ← Golden State 최빈 노드!

    # Golden Pattern 2: 근접 추적 (LeadPursuit)
    - type: Sequence
      children:
        - type: Condition
          name: DistanceBelow
          params:
            threshold_ft: 1500
        - type: Action
          name: LeadPursuit  # ← Golden State 2위 노드
```

#### Step 4: 검증 및 반복

```bash
# 1. 새 BT 테스트
python tools/test_agent.py my_learned_bt --all-opponents --rounds 10 --log-csv my_bt_test

# 2. 성능 비교
python tools/test_agent.py golden --all-opponents --rounds 10 --log-csv golden_baseline

# 3. 로그 비교 분석
# my_bt_test/ vs golden_baseline/ 폴더의 CSV 파일 비교
```

### 고급 분석: 노드 전환 패턴

```python
# Golden BT의 노드 전환 확률 학습
from collections import defaultdict

transitions = defaultdict(lambda: defaultdict(int))

for i in range(len(golden_df) - 1):
    current = golden_df.loc[i, 'active_node']
    next_node = golden_df.loc[i+1, 'active_node']
    transitions[current][next_node] += 1

# LeadPursuit 다음에 어떤 노드가 오는가?
for next_node, count in sorted(
    transitions['LeadPursuit'].items(), 
    key=lambda x: x[1], 
    reverse=True
)[:5]:
    total = sum(transitions['LeadPursuit'].values())
    print(f"{next_node:20s}: {count/total*100:.1f}%")
```

**출력 예시**:
```
LeadPursuit         : 45.2%  (계속 추적)
PNAttack            : 28.7%  (사격 전환)
Pursue              : 12.3%  (기본 추적)
BreakTurn           : 8.5%   (방어 전환)
HighYoYo            : 5.3%   (에너지 관리)
```

### 핵심 장점

1. **데이터 기반 학습**: 수작업 시행착오 → 체계적 패턴 분석
2. **BT 구조 친화적**: `active_nodes_path`로 정확한 실행 경로 추적
3. **상대별 맞춤 전략**: 각 상대에 대한 Golden BT의 다른 접근법 학습
4. **정량적 검증**: 학습된 패턴의 실제 효과 측정 가능

> 📘 **상세 가이드**: `GOLDEN_STATE_LEARNING.md` 참조

---

## 11. 우리의 최적화 과정 (연구 결과)

### 11.1 수작업 설계의 한계

```
v1: Hard Deck + Pursue                      → simple만 이김
v2: + WEZ 사격 추가                          → 3종 이김
v3: + LeadPursuit 근거리 전환                → 4종 이김
v4: + IsOffensiveSituation 분기              → ace에 약함
v5: + InEnemyWEZ 회피 + 파라미터 조정         → 5종 이김
v6: 종합 최적화 (수작업 한계)                  → 17W/1D/0L

문제: ace를 이기면 aggressive에 지고,
      aggressive를 이기면 ace에 진다 → 트레이드오프!
```

### 11.2 자동 최적화로 해결

```
┌─────────────────────────────────────┐
│  Stage 1: LHS 탐색 (30분)            │
│  204개 후보 무작위 생성 + 평가         │
│  → 상위 10개 선별                     │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│  Stage 2: Hill Climbing (37분)       │
│  10개 시작점 × 15개 이웃 = 160 후보   │
│  → 상위 5개 선별                      │
└──────────────┬──────────────────────┘
               ▼
┌─────────────────────────────────────┐
│  Stage 3: 최종 검증 (13분)            │
│  5개 × 5라운드 × 6상대 = 150 매치     │
│  → 챔피언 1개 선정                    │
└──────────────┬──────────────────────┘
               ▼
         Golden BT 탄생!
        120전 120승 (100%)
```

### 11.3 Golden BT의 핵심 발견

수작업으로 불가능했던 **ace vs aggressive 트레이드오프**를 자동 최적화가 해결:

```
핵심: offensive_press_distance = 3639m

거리 > 3639m → LeadPursuit (원거리 추적)
거리 < 3639m AND 공세적 → Pursue (적응적 추적)
거리 < 4000m → LeadPursuit (근거리 전투)

이 조합이 ace의 정밀함과 aggressive의 돌진을
동시에 상대할 수 있는 최적점!
```

---

## 12. 실행 모델 (Execution Model)

이 섹션은 매치 중 BT/RNN/env.step/로깅이 어느 주기로 돌아가는지, CSV/ACMI 행에 들어가는 값이 어느 빈도로 갱신되는지를 정확히 설명한다. **BT를 튜닝하거나 로그를 분석할 때 반드시 이해해야 한다**.

### 12.1 시간 계층

```
JSBSim 물리       : 60 Hz   (sim_freq=60, dt=1/60s)
env.step          : 20 Hz   (agent_interaction_steps=3, dt=0.05s)
Condition subtick : 20 Hz   (러너가 매 env.step 직후 BaseCondition.update() 호출)
BT tick (action)  : 10 Hz   (러너 내부 캐시: 2 env.step당 1번 BT 결정)
RNN 추론          :  5 Hz   (저수준 정책 캐시: 4 env.step당 1번 forward)
WEZ 콘            : 12°
TimedAction       : 5 Hz 기준 YAML → 실 BT tick rate로 자동 환산 (§9.2)
```

각 계층의 의미:

| 계층 | 빈도 | 무엇이 일어나는가 |
|------|------|------------------|
| **물리** | 60 Hz | JSBSim이 항공기 상태(위치/자세/속도/가속도)를 적분 |
| **env.step** | 20 Hz | 행동을 환경에 전달하고 3 sim step 만큼 물리 진행. 보상/종료 판정 |
| **condition subtick** | 20 Hz | observation 갱신 + 트리의 모든 `BaseCondition.update()` 호출 (액션은 제외) |
| **BT tick** | 10 Hz | `tree.tick_once()` 전체 실행 → action 노드까지 평가 → 새 고수준 명령 출력 |
| **RNN 추론** | 5 Hz | BaselineActor(GRU+MLP)가 고수준 명령 → 저수준 4채널 제어로 변환. 사이는 ZOH |

핵심 invariants:
- 모든 빈도는 `env.time_interval`에서 자동 산출됨 — `agent_interaction_steps` 값을 바꾸면 BT/RNN tick 비율은 동일하게 유지됨
- BT 한 번의 결정은 다음 RNN tick(최대 200 ms 후)에 반영됨. 결정-제어 latency = 100~200 ms
- RNN은 **재학습 없음**: 학습 분포(0.2 s 결정 주기) 보존을 위해 항상 5 Hz로 throttle

### 12.2 CSV / ACMI 갱신 빈도

CSV와 ACMI는 **둘 다 매 env.step마다 1행/1프레임**으로 기록된다. 즉 **20 Hz**. 단 컬럼별로 *값이 실제 갱신되는 빈도*는 출처에 따라 다르다.

매치 길이가 300 s일 때 CSV 행 수: `300 × 20 × 2 agents = 12,000 행`.

| 출처 | 값 갱신 빈도 | CSV 컬럼 / ACMI 필드 |
|------|-------------|---------------------|
| JSBSim 물리 상태 | 20 Hz | `step`(1~N), `sim_time_sec`, `ego_altitude_ft`, `ego_vc_kts`, `heading_deg`, `roll_deg`, `pitch_deg` |
| JSBSim 서보 응답 | 20 Hz | `servo_aileron`, `servo_elevator`, `servo_rudder` (실제 조종면 위치) |
| CombatGeometry 계산 (env.step마다) | 20 Hz | `distance_ft`, `ata_deg`, `aa_deg`, `hca_deg`, `tau_deg`, `closure_rate_kts`, `turn_rate_degs` |
| BT blackboard services (tick_conditions에서 갱신) | 20 Hz | `/Distance_ft`, `/CurrentRoll_deg`, `/ClosureRate_kts`, `/In39Line`, `ps_fts`, `bfm_situation` |
| BT 액션 출력 (BT tick에서만 갱신) | **10 Hz** (2행마다 같은 값 반복) | `action_altitude`, `action_heading`, `action_velocity` |
| 저수준 제어 (RNN 출력, RNN tick에서만 갱신) | **5 Hz** (4행마다 같은 값 반복) | `aileron`, `elevator`, `rudder`, `throttle`, ACMI의 `RollControlInput` 등 |
| 활성 BT 노드 (BT tick에서만 갱신) | **10 Hz** | `active_node`, `active_nodes_path` |
| WEZ 판정 / 데미지 / HP | 20 Hz | `in_wez`, `enm_in_wez`, `ego_health`, `enm_health`†, `ego_damage_dealt`, `enm_damage_dealt`† |
| 보상 | 20 Hz | `reward` (RL 비활성 — 항상 0)‡ |

> † **참가자 주의**: `enm_health`/`enm_damage_dealt`는 CSV/ACMI(운영자 사후 분석용)에만 기록된다. **BT가 실시간 관측하는 blackboard에는 노출되지 않는다** (§1.2.1).
>
> ‡ RL 학습 미사용으로 `reward` 컬럼은 0 고정. 매치 결과는 §1.2 판정으로만 결정.

분석 시 필터링 팁:
```python
import pandas as pd
df = pd.read_csv('logs/match.csv')
my = df[df['agent_id'] == 'A0100']

# BT 결정 시점만 (10 Hz): action 값이 새로 갱신된 행만 (step은 1-indexed)
bt_ticks = my[my['step'] % 2 == 1]

# RNN 추론 시점만 (5 Hz): 저수준 제어가 새로 갱신된 행만
rnn_ticks = my[my['step'] % 4 == 1]

# 액션 노드 전환 시점만
node_changes = my[my['active_node'] != my['active_node'].shift()]
```

CSV 크기가 부담스러우면 매치 실행 스크립트에서 다운샘플링이 가능하지만, 일반적으로 20 Hz 그대로 두는 것을 권장한다 — PS 차분, BFM 전환 검출, 짧은 WEZ 통과 이벤트 분석에 유리.

### 12.3 20 Hz Condition Subtick 활용 (참가자 사용 패턴)

기본적으로 conditional 노드는 BT tick(10 Hz)에서 평가되지만, runner가 매 env.step마다(20 Hz) 트리에 포함된 `BaseCondition` 인스턴스의 `update()`를 **추가로** 한 번 더 호출한다. 이를 활용하면 짧은 이벤트(예: 빠른 WEZ 통과, 순간적 ATA 임계 통과)를 놓치지 않고 감지하는 stateful condition을 작성할 수 있다.

핵심 규칙:
1. 커스텀 condition은 `BaseCondition`을 상속해야 한다 (`from src.behavior_tree.nodes.conditions import BaseCondition`)
2. `update()` 내부에서 stateful 로직(이전 값 기억, 카운터, 디바운스)을 구현
3. 액션 선택은 여전히 10 Hz에서만 일어남 — condition이 `SUCCESS`를 자주 반환해도 액션은 다음 BT tick까지 보류됨
4. 부수효과(blackboard 쓰기, 인스턴스 변수 갱신)는 20 Hz로 발생

예시: WEZ 진입 edge detection (50 ms 정밀)
```python
from src.behavior_tree.nodes.conditions import BaseCondition
import py_trees

class WEZEntryEdge(BaseCondition):
    """WEZ 진입 순간만 SUCCESS — 20 Hz로 평가되므로 짧은 통과도 포착."""
    def __init__(self, name="WEZEntryEdge"):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(key="observation", access=py_trees.common.Access.READ)
        self._prev_in_wez = False

    def update(self):
        in_wez = self.blackboard.observation.get("in_wez", False)
        edge = in_wez and not self._prev_in_wez
        self._prev_in_wez = in_wez
        return py_trees.common.Status.SUCCESS if edge else py_trees.common.Status.FAILURE
```

예시: ATA 임계 통과 횟수 카운터
```python
class ATABelowThresholdCounter(BaseCondition):
    """ATA가 임계값 아래로 통과한 횟수가 N 이상이면 SUCCESS (20 Hz 정밀)."""
    def __init__(self, name="ATABelowThresholdCounter", threshold_deg=15.0, min_count=3):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(key="observation", access=py_trees.common.Access.READ)
        self.threshold_deg = threshold_deg
        self.min_count = min_count
        self._prev_above = True
        self._count = 0

    def update(self):
        ata = self.blackboard.observation.get("ata_deg", 180.0)
        below = ata < self.threshold_deg
        if below and self._prev_above:
            self._count += 1
        self._prev_above = not below
        return py_trees.common.Status.SUCCESS if self._count >= self.min_count else py_trees.common.Status.FAILURE
```

> ⚠️ 주의 — `update()`가 무거우면 20 Hz로 호출되므로 매치 성능에 영향. 무거운 계산은 BT tick 노드에서 수행하고, condition은 가벼운 stateful 로직만 두는 것을 권장.

---

## 부록 A: 주요 파라미터 요약

### 핵심 규칙 요약

| 항목 | 값 | 비고 |
|------|-----|------|
| **Hard Deck** | 1000ft (304.8m) | `HARD_DECK_M = feet_to_meters(1000)` |
| **최대 스텝** | 6,000 스텝 = 300초 | `max_steps: 0` (자동, env.time_interval 기반 5분 산출) |
| **WEZ 거리** | 500~3000ft | 152~914m |
| **WEZ 각도** | ATA < 12° | 실제 데미지 발생 조건 (ATA 0°에 가까울수록 데미지 증가) |
| **기본 DPS** | 25 HP/s | 거리·각도 계수 적용 |
| **초기 체력** | 100 HP | - |

> ⚠️ **Hard Deck 주의**: 실제 화늘 교전 제한고도는 **1,000ft (304.8m)**입니다. BT 설계 시 `threshold_ft: 3281` (1,000m) 등 여유값을 두어 안전하게 설계하는 것을 권장합니다.

### 파라미터 이름 규칙 (SDK v0.5.3)

| 단위 | 접미사 | 예시 |
|------|--------|------|
| 거리/고도 | `_ft` | `threshold_ft: 3000` |
| 각도 | `_deg` | `threshold_deg: 15` |
| 속도 | `_kts` | `min_velocity_kts: 389` |

### 단위 변환 참조

| 단위 | 환산 |
|------|------|
| 1 m | 3.28084 ft |
| 1 NM | 6076.12 ft |
| 1 m/s | 1.94384 kts |
| 1000 ft | 304.8 m |

---

*AI-Pilot 연구팀 | 2026년 3월*
