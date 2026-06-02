# 노드 & 파라미터 레퍼런스

행동트리에서 사용 가능한 모든 노드와 파라미터 상세 설명입니다.

> 💡 **팁**: 모든 파라미터는 기본값이 설정되어 있어 생략 가능합니다. 전술에 맞게 필요한 파라미터만 조정하세요.

---

## 고수준 액션 공간 (5×9×5)

모든 액션 노드는 내부적으로 아래 3개의 이산 인덱스를 `Blackboard`에 설정합니다.

| 축 | 인덱스 | 의미 |
|----|--------|------|
| **delta_altitude** (5) | 0=급하강, 1=하강, 2=유지, 3=상승, 4=급상승 | 고도 변화 명령 |
| **delta_heading** (9) | 0=급좌(-90°) ~ 4=직진 ~ 8=급우(+90°) | 방향 변화 명령 |
| **delta_velocity** (5) | 0=급감속, 1=감속, 2=유지, 3=가속, 4=급가속 | 속도 변화 명령 |

> **(선택) Tactical Maneuver 출력**: 225개 이산 격자 대신 연속값 maneuver 리스트를 출력할 수 있다.
> `BaseAction.set_maneuver([Maneuver(ManeuverType.BANK_HOLD, -30.0, 1.0), ...])` — 6타입
> (`BANK_HOLD`/`PITCH_HOLD`/`SPEED_HOLD`/`ALT_HOLD`/`ATA_TRACK`/`HEADING_HOLD`). `AICOMBAT_CONTROLLER=classical`
> 의 PD 제어기가 정밀 추종한다. `set_action()` 은 자동으로 maneuver로 변환되어 기존 트리도 동작.
> 자세히는 [제어기정리.md](../../docs/제어기정리.md) Slide 3, [maneuvers.py](../../src/behavior_tree/maneuvers.py).

---

## 복합 노드 (Composites)

| 노드 | 설명 | `params` |
|-----|------|----------|
| `Selector` | 자식 중 하나 성공 시 성공 (OR 논리) | `memory: false` (기본) |
| `Sequence` | 모든 자식 성공 시 성공 (AND 논리) | `memory: false` (기본) |

> **`memory: true`**: RUNNING 중인 자식 노드부터 재개 — 장기 기동 보호에 필수.  
> 조건 재검사를 건너뛰므로, **긴급 회피 등 최상위 Selector는 `memory: false` 유지** 권장.

```yaml
# 장기 기동 사용 패턴
- type: Sequence
  name: ImmelmannSequence
  params:
    memory: true          # ← RUNNING 동안 조건 재검사 안 함
  children:
    - type: Condition
      name: HasAltitudeAdvantage
    - type: Action
      name: ImmelmannTurn   # RUNNING 반환 노드
      params:
        duration_steps: 12
```

---

## 조건 노드 (Conditions)

> 🔁 **Condition 평가 빈도** — 모든 표준 조건은 `BaseCondition`을 상속하며 runner의 20 Hz subtick 대상이다. 즉 매 env.step(50 ms)마다 한 번씩 `update()`가 호출되어 짧은 이벤트도 놓치지 않는다. BT tick(10 Hz)에서는 트리 traversal 중 추가로 평가된다. 자세한 내용은 GUIDE.md §12 참조.

### 거리 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `EnemyInRange` | `max_distance_ft=16404.0` | `max_distance_ft` (ft) | 적이 지정 거리 이내 |
| `DistanceBelow` | `threshold_ft=9843.0` | `threshold_ft` (ft) | 거리 < 임계값 |
| `DistanceAbove` | `threshold_ft=6562.0` | `threshold_ft` (ft) | 거리 > 임계값 |

### 고도/속도 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `AltitudeAbove` | `min_altitude_ft=9843.0` | `min_altitude_ft` (ft) | 고도 ≥ 지정값 |
| `AltitudeBelow` | `min_altitude_ft=3281.0` | `min_altitude_ft` (ft) ⚠️ 이름이 `min_`이지만 의미는 **상한** (이 값 이하인지 검사) | 고도 ≤ 지정값 |
| `BelowHardDeck` | `threshold_ft=1000.0` | `threshold_ft` (ft) | 고도 < 임계값 (Hard Deck 위반 위험) |
| `VelocityAbove` | `min_velocity_kts=389.0` | `min_velocity_kts` (kts) | 속도 ≥ 지정값 |
| `VelocityBelow` | `max_velocity_kts=778.0` | `max_velocity_kts` (kts) | 속도 ≤ 지정값 |

> ⚠️ **Hard Deck**: 고도 1000ft 이하 시 즉시 패배. `BelowHardDeck` + `ClimbTo`를 행동트리 최상단에 배치하세요.

### BFM 상황 조건

BFM 상황은 `CombatGeometry`(ATA, AA, HCA, 에너지, TC타입)를 기반으로 자동 분류됩니다.

| 노드 | 분류 기준 | 설명 |
|-----|----------|------|
| `IsOffensiveSituation` | ATA<45°, AA<100°, 거리 0.3~3NM + 에너지/3-9Line 우세 | OBFM - 공격 유리 상황 |
| `IsDefensiveSituation` | AA>90°, ATA>60° 또는 에너지 열세+접근 중 | DBFM - 방어 필요 상황 |
| `IsNeutralSituation` | HCA>90° 또는 원거리 또는 2-circle 선회 | HABFM - 정면/고측면 대등 상황 |

### 각도 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `ATAAbove` | `threshold_deg=60.0` | `threshold_deg` (°) | ATA > 임계값 (적이 측면/후방) |
| `ATABelow` | `threshold_deg=30.0` | `threshold_deg` (°) | ATA < 임계값 (적이 전방) |
| `UnderThreat` | `aa_threshold_deg=120.0` | `aa_threshold_deg` (°) | AA > 임계값 (적 정면 노출 위험) |
| `LOSAbove` | `threshold_deg=15.0` | `threshold_deg` (°) | LOS 각도 > 임계값 |
| `LOSBelow` | `threshold_deg=15.0` | `threshold_deg` (°) | LOS 각도 < 임계값 |
| `InEnemyWEZ` | `max_distance_ft=9843.0`, `max_los_angle_deg=30.0` | `max_distance_ft` (ft), `max_los_angle_deg` (°) | 적 WEZ 내에 있음 |
| `AspectAngleAbove` | `threshold_deg=35.0` | `threshold_deg` (°) | AA > 임계값 — 적이 **기동 중**(측면/전면 제시) |
| `AspectAngleBelow` | `threshold_deg=35.0` | `threshold_deg` (°) | AA < 임계값 — 적이 **비기동 직진**, 아군이 6시 점유 |

> **ATA**: 0°=적이 정면, 90°=적이 측면, 180°=적이 후방  
> **AA**: 0°=내가 적 후방(안전), 180°=내가 적 정면(위험)

### 에너지 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `EnergyHighPs` | `threshold_fts=0.0` | `threshold_fts` | Ps(Specific Excess Power) > 임계값 |
| `SpecificEnergyAbove` | `threshold_ft=16404.0` | `threshold_ft` (ft) | He(고도+v²/2g) ≥ 임계값 |
| `IsMerged` | `merge_threshold_ft=1640.0` | `merge_threshold_ft` (ft) | 거리 < 임계값 (근접 교전) |

### 전술 인사이트 기반 조건 (신규)

#### 전술 상태 조건

| 노드 | 기본값 | 설명 |
|-----|--------|------|
| `Is39Line` | - | 적이 내 3-9 라인 안(ATA < 90°) — 공격 우위 위치 |
| `IsOvershootRisk` | - | 오버슈트 위험 감지 (빠른 접근 + 근거리 + 낮은 ATA/선회율) |
| `IsTargetInSight` | - | 적이 시야 내 (ATA < 90°, `Is39Line`과 동일) |
| `IsOneCircle` | - | 1-circle 선회 상황 (HCA < 90°, 같은 방향 선회) |
| `IsTwoCircle` | - | 2-circle 선회 상황 (HCA > 90°, 반대 방향 선회) |

#### 에너지 우세 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `IsEnergyAdvantage` | - | - | 종합 에너지(He = h + v²/2g) 우세 |
| `IsAltAdvantage` | - | - | 고도 우세 (내 고도 > 적 고도) |
| `IsSpdAdvantage` | - | - | 속도 우세 (내 속도 > 적 속도) |
| `EnergyDiffAbove` | `threshold_ft=1640.0` | `threshold_ft` (ft) | 에너지 차이 > 임계값 |

#### 접근/선회율 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `ClosureRateAbove` | `threshold_kts=97.2` | `threshold_kts` (kts) | 접근 속도 > 임계값 (양수=접근 중) |
| `ClosureRateBelow` | `threshold_kts=0.0` | `threshold_kts` (kts) | 접근 속도 < 임계값 (멀어지는 중 감지) |
| `TurnRateAbove` | `threshold_degs=5.0` | `threshold_degs` (°/s) | 선회율 > 임계값 (기동 여유 있음) |

#### 고도차 게이트 조건

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `AltGapAbove` | `threshold_ft=0.0` | `threshold_ft` (ft) | 아군−적 고도차 > 임계값 (수직 우위) |
| `AltGapBelow` | `threshold_ft=0.0` | `threshold_ft` (ft) | 아군−적 고도차 < 임계값 (수직 열세) |

---

## 액션 노드 (Actions)

### 기본 기동

| 노드 | 내부 액션 | 설명 |
|-----|----------|------|
| `MaintainAltitude` | `(2, 4, 2)` | 고도·방향·속도 모두 유지 |
| `Accelerate` | `(2, 4, 4)` | 급가속 |
| `Decelerate` | `(2, 4, 0)` | 급감속 |
| `Straight` | `(2, 4, 2)` | 직진 유지 |
| `TurnLeft` | `(2, 2, 2)` / `(2, 0, 2)` | 중좌회전 / `intensity="hard"` 시 급좌회전 |
| `TurnRight` | `(2, 6, 2)` / `(2, 8, 2)` | 중우회전 / `intensity="hard"` 시 급우회전 |

**파라미터:**
- `TurnLeft`, `TurnRight`: `intensity` — `"normal"` (기본) 또는 `"hard"`

### 고도 기동

| 노드 | 기본값 | 파라미터 | 설명 |
|-----|--------|---------|------|
| `ClimbTo` | `target_altitude_ft=19685.0` | `target_altitude_ft` (ft) | 목표 고도로 상승/하강 |
| `DescendTo` | `target_altitude_ft=13123.0` | `target_altitude_ft` (ft) | 목표 고도로 하강/상승 |
| `VerticalLead` | `altitude_idx=4`, `velocity_idx=2`, `use_hard_turn=False` | 위 파라미터 | Lufbery 평형에서 적과 동방향 선회 + 급상승으로 **수직 우위** 점유 (이후 다이브 공격) |
| `AltitudeAdvantage` | `target_advantage_ft=1640.0` | `target_advantage_ft` (ft) | 적보다 지정 고도 우위 유지 |

> `ClimbTo`/`DescendTo`는 고도차 >656ft 시 급기동, >328ft 시 일반기동, 이하 시 유지.

### 추적 기동 (OBFM)

#### `Pursue` — 적 추적 (종합 추적)

거리·고도·방위각·ATA를 종합 판단하여 최적 기동을 자동 선택합니다.

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `close_range_ft` | 6562.0 ft | 근중거리 판정 |
| `very_close_range_ft` | 4921.0 ft | 근거리 판정 |
| `far_range_ft` | 13123.0 ft | 원거리 판정 (이상 시 급가속) |
| `mid_far_range_ft` | 8202.0 ft | 중원거리 판정 |
| `alt_gap_fast_ft` | 656.0 ft | 급기동 고도차 임계값 |
| `alt_gap_normal_ft` | 328.0 ft | 일반기동 고도차 임계값 |
| `bearing_straight_deg` | 5.0 ° | 직진 판정 방위각 |
| `bearing_hard_deg` | 60.0 ° | 급회전 판정 방위각 |
| `bearing_strong_deg` | 30.0 ° | 강회전 판정 방위각 |
| `bearing_medium_deg` | 15.0 ° | 중회전 판정 방위각 |
| `ata_lost_deg` | 60.0 ° | 적 놓침 판정 ATA (이상 시 급감속+급회전) |
| `ata_side_deg` | 30.0 ° | 적 측면 판정 ATA (이상 시 감속) |

```yaml
# 공격적 추적
- type: Action
  name: Pursue
  params:
    close_range_ft: 8202
    bearing_straight_deg: 3
    ata_lost_deg: 45

# 보수적 추적
- type: Action
  name: Pursue
  params:
    far_range_ft: 16404
    alt_gap_fast_ft: 984
```

#### `LeadPursuit` — 선도 추적

`relative_bearing_deg`와 `ata_deg` 기반으로 적의 미래 위치를 향해 선회합니다. Gun WEZ(±12°, 500~3000ft) 진입에 최적화.

> **내부 동작**: `lead_factor = clamp(ATA × 0.01, 0, 1)` — ATA가 클수록(조준 벗어남) 더 앞을 노리도록 선회 임계값을 스케일링하고, ATA가 작을수록(WEZ 근접) 정밀 조준 모드로 전환됩니다.  
> ATA < 15° + 거리 < 3281ft(~1km) 동시 충족 시 WEZ 정밀 조준 모드 자동 전환.

#### `PurePursuit` — 순수 추적

`side_flag` 기반으로 적의 현재 위치를 향해 직접 추적합니다. ATA를 0으로 유지하는 것이 목표입니다.

#### `LagPursuit` — 지연 추적

`tau_deg`(롤 보정 목표 위치각) 기반으로 적의 진행 방향 반대편(뒤쪽)을 추적합니다.

> **목적**: 오버슈트 방지 · 에너지 우위 확보 · 안전한 추적 거리 유지  
> **내부 동작**: `lag_offset_factor = 0.5 × clamp(ATA × 0.01, 0, 1)` — ATA가 클수록 더 뒤를 추적하여 오버슈트를 방지하며, ATA가 작아질수록 Pure Pursuit에 수렴합니다.

### 방어 기동 (DBFM)

#### `DefensiveManeuver` — AA 기반 방어 기동

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `critical_aa_threshold_deg` | 45.0 ° | 매우 위험 AA 임계값 (이하 시 급회피) |
| `danger_aa_threshold_deg` | 90.0 ° | 위험 AA 임계값 (이하 시 중간 회피) |
| `alt_gap_threshold_ft` | 492.0 ft | 고도 변경 임계값 |

```yaml
- type: Action
  name: DefensiveManeuver
  params:
    critical_aa_threshold_deg: 60
    danger_aa_threshold_deg: 100
```

#### `BreakTurn` — 급선회 회피

`side_flag` 반대 방향으로 급우/급좌회전 + 하강 + 급가속. 선회율 극대화.

#### `DefensiveSpiral` — 방어 나선

강선회 + 고도 조절 + 급가속으로 나선형 회피. 고도 4921ft 이하 시 상승 전환.

### 에너지 기동

| 노드 | 파라미터 | 설명 |
|-----|---------|------|
| `ClimbingTurn` | `direction="left"/"right"/"auto"` | 상승하며 선회 (에너지 저장) |
| `DescendingTurn` | `direction="left"/"right"/"auto"` | 하강하며 선회 (속도 획득) |
| `BarrelRoll` | - | 적의 조준을 피하면서 에너지 손실 최소화. 상승선회↔하강선회 반복. **RUNNING 반환** — `memory: true` Sequence 권장 |
| `HighYoYo` | - | Lufbery Circle 탈출 · OBFM 오버슈트 방지. 고도↑ 에너지 저장 후 속도로 변환. ATA < 30° 또는 속도 < 311kts 시 하강 전환. **RUNNING 반환** |
| `LowYoYo` | - | 속도 열세 시 공격 거리 축소. 급하강+급가속으로 속도 확보 후 상승. 속도 > 544kts 시 상승 전환, 속도 < 428kts 시 재하강. **RUNNING 반환** |

> `direction="auto"`: `side_flag` 기반으로 적 방향 자동 선택  
> **RUNNING 반환 노드**: phase 상태가 tick 간 유지됨. 부모 Sequence 조건이 실패하면 기동이 중단(`terminate()` 호출)됩니다.

### 정면 교전 기동 (HABFM)

| 노드 | 설명 |
|-----|------|
| `OneCircleFight` | 적 방향으로 급선회 + 감속 (작은 반경, 선회 우위 시) |
| `TwoCircleFight` | 적 반대 방향으로 약선회 + 급가속 (큰 반경, 에너지 우위 시) |
| `GunAttack` | `relative_bearing_deg` 기반 초정밀 조준. Gun WEZ(±12°, 500~3000ft) 내에서 ±2° 이하 조준 정밀도를 목표로 합니다. 파라미터: `lead_factor` (기본 1.2, 선도량 조절) |

### 회피 기동

| 노드 | 설명 |
|-----|------|
| `Evade` | `side_flag` 반대 방향으로 강선회 + 가속 |

### 전술 인사이트 기반 액션

| 노드 | 설명 |
|-----|------|
| `OvershootAvoidance` | 오버슈트 위험 시 자동 Lag/HighYoYo 전환. 선회율 < 3°/s → 급상승+선회+감속(HighYoYo 패턴). 접근속도 > 155.6kts + 거리 < 3281ft → 급감속+TAU 방향 유지(즉시 Lag). 그 외 → 일반 Lag 기동 |
| `EnergyFight` | 에너지 상태 기반 최적 전술 자동 선택. 고도우세→하강공격, 속도우세→가속추격, 열세→상승회복 |
| `TCFight` | 선회 유형(1/2-circle) 기반 전술 자동 분기. 1-circle→급선회+감속, 2-circle→에너지유지+재접근 |

### 고급 3차원 기동

#### 완결형 기동 — `TimedAction` 기반 (`RUNNING` → `SUCCESS`)

`duration_steps` tick 동안 실행 후 `SUCCESS`를 반환합니다. **반드시 `memory: true` Sequence 안에 배치**하세요.

| 노드 | 기본 duration | 기동 원리 | 결과 |
|-----|-------------|---------|------|
| `Loop` | 15 ticks (3s) | 수직 원형 기동. 급상승→최고점→급하강 | 속도↓, 방향 유지 |
| `ImmelmannTurn` | 12 ticks (2.4s) | 루프 상반부 급상승(60%) + 최고점에서 적 방향으로 180° 롤아웃(40%) | 방향 180° 전환, 고도↑, 속도↓ |
| `SplitS` | 10 ticks (2s) | 배면 진입+방향 전환(30%) + 루프 하반부 급하강(70%) | 방향 180° 전환, 고도↓, 속도↑ |
| `HammerHead` | 15 ticks (3s) | 수직 상승+감속(33%) → 실속 직전 급감속(22%) → 요잉 전환(15%) → 급하강+급가속(30%) | 급격한 방향 전환, 고도↑ 후 하강 |

**공통 파라미터:** `duration_steps` (int, 기본값 각 노드마다 상이) — 기동 소요 tick 수. **`duration_steps` 는 5 Hz 기준으로 작성**하며 베이스 클래스가 실제 BT tick rate(10 Hz, 100 ms)로 자동 환산합니다. 즉 `duration_steps: 15` 는 항상 **3초** 입니다 (자세한 내용은 본 문서 하단 `TimedAction` 섹션 참조).

> `ImmelmannTurn`, `SplitS`, `HammerHead`는 기동 시작 시점의 `side_flag`를 캡처하여 방향을 고정합니다.

#### 반응형 연속 기동 — `BaseAction` 기반 (`SUCCESS` 반환)

매 tick 상황을 재평가하며 명령을 내립니다. `memory: true` 없이도 조건 지속 시 자연히 연속 실행됩니다.

| 노드 | 기동 원리 | 설명 |
|-----|---------|------|
| `SliceTurn` | 얕은 하강 뱅크턴 | 기수를 수평선보다 약간 아래로 내린 채 하강하며 뱅크턴. 에너지 손실 최소화 |
| `SpiralDive` | 급강하 나선형 선회 | 깊은 각도로 급강하하며 배럴롤. 속도 빠르게 회복, 추격 이탈 |
| `SpiralClimb` | 상승 나선형 선회 | 높은 속도·파워가 필요한 극한 기동. 에너지 우위 유지하며 고도↑ |

---

### 장기 기동 설계 — `TimedAction` (커스텀 노드용)

**N tick 동안 RUNNING → 완료 시 SUCCESS**를 자동 처리하는 베이스 클래스입니다.

```python
from src.behavior_tree.nodes.actions import TimedAction

class MyManeuver(TimedAction):
    def __init__(self, name="MyManeuver", duration_steps=15):
        super().__init__(name=name, duration_steps=duration_steps)

    def on_start(self):
        """기동 시작 시 1회 호출 — blackboard 접근 가능 (선택)"""
        self._side = self.blackboard.observation.get("side_flag", 0)

    def execute(self, step: int, total: int):
        """매 tick 호출 — set_action() 반드시 호출 (필수)"""
        if step <= total // 2:
            self.set_action(4, 4, 1)   # 상승 단계
        else:
            turn = 1 if self._side < 0 else 7
            self.set_action(0, turn, 4)  # 하강 단계

    def on_finish(self, status):
        """기동 완료·중단 시 1회 호출 (선택)"""
        pass
```

| 메서드 | 필수 | 설명 |
|-------|------|------|
| `execute(step, total)` | **필수** | 매 tick 실행. `step`은 1부터 시작 |
| `on_start()` | 선택 | 기동 시작 시 1회 초기화 |
| `on_finish(status)` | 선택 | 완료(`SUCCESS`) 또는 외부 중단(`INVALID`) 시 정리 |

> 📐 **duration_steps는 5 Hz 기준 — 자동 환산**
>
> `duration_steps`는 `REFERENCE_TICK_HZ = 5.0` 기준으로 작성한다. 실제 BT tick rate(10 Hz)에 맞춰 베이스 클래스가 자동 비례 환산하므로, `duration_steps: 15`는 항상 **3초**를 의미한다. 시간 계층의 전체 그림은 GUIDE.md §12 참조.

---

## YAML 사용 예제

```yaml
name: "my_agent"
description: "BFM 기반 전술"

tree:
  type: Selector
  children:
    # 1. Hard Deck 회피 (필수 - 최상단 배치)
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

---

## 관측값 (Blackboard `observation`)

> 📖 **전체 키 목록, 단위 규약(`_ft`/`_kts`/`_deg`), 부호 규약, Breaking Change 안내는 [`BLACKBOARD_REFERENCE.md`](./BLACKBOARD_REFERENCE.md)를 참조하세요.**

---

## 주요 용어

| 용어 | 설명 |
|-----|------|
| **ATA** | Antenna Train Angle — 내 속도 벡터와 적 방향 사이 각도 (0°=정면조준) |
| **AA** | Aspect Angle — 적 기준 내 위치 각도 (0°=적 후방 안전, 180°=정면 위협) |
| **HCA** | Heading Crossing Angle — 두 기체의 진행 방향 교차 각도 |
| **TAU** | 롤 각도를 고려한 목표 위치 각도 |
| **WEZ** | Weapon Engagement Zone — Gun WEZ: ATA < 12°, 500~3000ft (ATA 0°·거리 500ft일수록 데미지 최대) |
| **BFM** | Basic Fighter Maneuvers — OBFM(공격)/DBFM(방어)/HABFM(정면) |
| **Hard Deck** | 최저 안전 고도 1000ft (위반 시 즉시 패배) |
| **Ps** | Specific Excess Power — 기체의 잉여 에너지 (>0이면 가속/상승 여유 있음) |
