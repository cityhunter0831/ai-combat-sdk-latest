# AI Combat Platform 룰북

본 문서는 **2026 공군사관생도 AI Pilot 경진대회**의 공식 룰을 정의한다.
참가자 BT 작성, 운영자 토너먼트 진행, 심사자 이의제기 대응의 단일 기준 문서.

---

## 1. 매치 개요

- **형식**: 1v1 BT 에이전트 대전
- **기체**: F-16 (JSBSim 1.2.3)
- **장소**: 가상 공역 (battle field center: lon 120°, lat 60°, alt 0)
- **시뮬레이션 시간**: 최대 300초 (max_steps 1500 @ env 20 Hz)
- **실제 시간 상한**: 매치당 60초 (wall-clock 안전장치)

---

## 2. 초기 조건

| 항목 | Blue (A0100) | Red (B0100) |
|---|---|---|
| 위치 | 서쪽 9시 (lon 119.991°) | 동쪽 3시 (lon 120.009°) |
| 위도 | 60.0° | 60.0° |
| 고도 | 15,000 ft | 15,000 ft |
| 헤딩 | 180° (남쪽) | 0° (북쪽) |
| 초기 속도 | 600 fps (≈ 355 kts) | 600 fps |
| 초기 HP | 100 | 100 |

**초기 위치 swap 없음.** 동일 BT 쌍의 결과는 위치 운에 좌우되지 않도록 위치 셔플 제거. 같은 시드는 항상 같은 결과 (§10 참조).

---

## 3. 제어 계층

3계층 분리 — "무엇을(What)"과 "어떻게(How)"의 분리.

| 계층 | 주기 | 입력 → 출력 |
|---|---|---|
| 전술 (BT) | **10 Hz** | Blackboard 관측 → 고수준 액션 인덱스 [Δalt, Δhdg, Δv] |
| 명령 (RNN) | **5 Hz** | 명령 3 + 자기상태 9 → [aileron, elevator, rudder, throttle] |
| 물리 (JSBSim) | 60 Hz FDM, 20 Hz env.step | 4채널 조종 명령 → 6-DOF dynamics |

세 주파수는 정수배 관계라 위상 어긋남 없음. RNN은 PPO 사전학습된 정책으로, 매치 중 학습되지 않는다 (추론만).

> **(참고) 선택적 Classical PD 제어기**: 명령 계층은 기본값이 위 RNN이나, 평가·연구용으로
> `AICOMBAT_CONTROLLER=classical` 설정 시 RNN을 우회하고 PD 제어기([classical_controller.py](../src/control/classical_controller.py))를
> 사용할 수 있다. 공식 매치는 기본(RNN)으로 운영하며, BT가 연속 maneuver를 출력하면(`set_maneuver`)
> 이 PD 경로가 더 정밀히 추종한다. 자세히는 [제어기정리.md](제어기정리.md) Slide 3.

---

## 4. 행동 트리(BT) 인터페이스

### 4.1 액션 공간 (5 × 9 × 5 = 225)

| 축 | 인덱스 | 명령 |
|---|---|---|
| Altitude (Δh) | 0–4 | DIVE / DESCEND / MAINTAIN / CLIMB / CLIMB_FAST |
| Heading (Δψ) | 0–8 | HARD_L · MED_L · SLOW_L · STRAIGHT(4) · SLOW_R · MED_R · HARD_R (±π/2 범위) |
| Velocity (Δv) | 0–4 | BRAKE / DECEL / MAINTAIN / ACCEL / ACCEL_FAST |

### 4.2 BT가 관측 가능한 상태 (블랙보드)

매 BT tick(10 Hz)에서 다음 정보가 갱신된다:

**자기 상태 (15차원 정규화 벡터)** + **교전 기하**:
- 자기: 고도, roll/pitch sin/cos, body velocity, CAS
- 상대: ATA, AA, HCA, TAU, distance, alt_gap, closure rate, turn rate, side_flag
- 파생: in_39_line, overshoot_risk, tc_type, energy_advantage, alt/spd advantage
- BFM 상황: OBFM / DBFM / HABFM

**매치 상태** (Runner에서 매 틱 inject):
- `ego_health` — 자기 현재 HP
- `ego_damage_dealt` — 자기가 가한 누적 데미지
- `ego_damage_received` — 자기가 받은 누적 데미지
- `in_wez` — 자기가 적을 WEZ에 잡고 있음
- `enm_in_wez` — 적이 자기를 WEZ에 잡고 있음 (위협 신호)

### 4.3 BT가 관측할 수 없는 정보 (정보 비대칭 차단)

룰북 정책상 **적 HP·적이 입은 데미지는 노출되지 않는다.** 적의 상태는 위 5개 채널과 교전 기하로만 추정해야 한다.

- ❌ `enm_health` (제거)
- ❌ `enm_damage_dealt` (제거)
- ❌ `enm_damage_received` (제거)

---

## 5. 무기 시스템 (Gun WEZ)

연속 데미지 적분 모델.

$$\frac{dHP}{dt} = D_0 \cdot f_R(R) \cdot f_{ATA}(ATA)$$

| 파라미터 | 값 |
|---|---|
| 기준 피해율 $D_0$ | 25 HP/s |
| 사거리 $R$ | 500 ft (최대) ~ 3,000 ft (0) — 선형 감쇠 |
| 각도 $f_{ATA}$ | ATA 0° (최대) ~ 12° (0) — 선형 감쇠 |
| WEZ 외 | 0 |

WEZ에 들어간 순간부터 매 스텝 데미지 누적. 후미 추격이 아닌 **정밀 조준**이 효율적.

---

## 6. 비행 안전 규정

비정상 비행 상태가 누적 임계 시간을 초과하면 **즉시 패배**.

| 사유 | 조건 | 누적 시간 | victory_condition |
|---|---|---|---|
| 과부하 (OVERLOAD) | \|n_pilot\| > 9.0 G | 5.0 초 | `overload` |
| 스핀 (SPIN) | \|roll rate\| > 360°/s | 3.0 초 | `spin` |
| 실속 (STALL) | CAS < 100 kts | 10.0 초 | `stall` |

**누적은 연속 시간이 아닌 매치 전체 누적**. 짧게 끊어 들어갔다 나오는 게이밍 차단.
양쪽이 동시에 안전 위반이면 무승부(`draw`).

---

## 7. Hard Deck

최저 안전 고도 **1,000 ft (304.8 m)**. 위반 즉시 패배.
양쪽 동시 위반 시 무승부.

---

## 8. 승패 판정

매 스텝 단일 판정 경로. **우선순위 (위가 먼저)**:

1. **HARD_DECK_VIOLATION** — 1,000 ft 미만 강하
2. **OVERLOAD / SPIN / STALL** — §6 안전 위반
3. **HEALTH_ZERO** — HP ≤ 0
4. **DISQUALIFY** — BT가 매 tick 호출에서 예외를 던짐 (§11.2)
5. **WALL_CLOCK_TIMEOUT** — 매치 실제 시간 60초 초과 (안전장치, §11.1)
6. **TIMEOUT** — max_steps 도달
   - HP 우위 측 승리 (`health_adv`)
   - HP 동률이면 무승부 (`timeout`)

### 동시 사건 처리

| 상황 | 결과 |
|---|---|
| 양측 HP 동시 0 | `simultaneous_ko` (무승부) |
| 양측 Hard Deck 동시 위반 | `draw` |
| 양측 안전 위반 동시 발화 | `draw` |
| 양측 BT 동시 예외 | `draw` (DISQUALIFY) |

---

## 9. 토너먼트 진행

### 9.1 예선 — Round Robin

전 팀이 서로 1회씩 대전. N팀이면 N×(N−1)/2 매치.

### 9.2 결승 — Single Elimination (표준 시딩)

예선 상위 N팀(보통 8) 진출. 표준 시딩으로 1번 시드가 약체와 만나도록:

- 8팀 8강: (1v8), (2v7), (3v6), (4v5)
- 4팀 4강: (1v4), (2v3)
- 2팀 결승: (1v2)

비2제곱 인원이면 **상위 시드부터 bye(부전승)** 자동 부여.
이전 라운드 승자가 다음 라운드 매치를 형성. **결승 토너먼트는 무승부 불허** (무승부 결과 시 ValueError).

---

## 10. 결정론·재현성

### 10.1 시드 도출

매치 ID로부터 SHA-256 해시 앞 4바이트를 32비트 시드로 사용.
PYTHONHASHSEED와 무관하게 어느 환경에서도 동일 매치 ID → 동일 시드.

```
seed = sha256(match.id + "\x00").digest()[:4] |> big-endian int
```

매치 시작 시 `env.seed(s)`, `np.random.seed(s)`, `random.seed(s)` 일괄 적용.

### 10.2 결정론 보장 범위

같은 `(tree1, tree2, seed)`는 **모든 결과 필드가 완전 일치**한다:
- winner, total_steps, tree1/2_health, tree1/2_damage_dealt, victory_condition

### 10.3 매치 감사

이의제기 시 운영자는 동일 시드로 재실행해 원본과 비교 가능:

```bash
python scripts/audit_match.py <match_id>
```

불일치 시 가능한 원인: 코드 변경, BT 파일 변경(부정행위 의심), JSBSim 버전 변경.

---

## 11. 매치 안전장치

### 11.1 Wall-clock timeout (60초)

JSBSim 폭주·BT 무한루프 등으로 매치가 영원히 안 끝나는 상황 방지.
시뮬레이션 시간(max_steps)과는 별도로 실제 시간 60초 초과 시 무승부(`wall_clock_timeout`)로 강제 종료.

### 11.2 BT 예외 격리

참가자 BT의 `get_high_level_action()` 호출에서 예외 발생 시:
- 해당 BT는 즉시 **DISQUALIFY**
- 상대 BT가 승리
- 매치 전체가 죽지 않음

양측 동시 예외 시 무승부(DISQUALIFY).

### 11.3 BT tick 무한루프

Python 한계상 외부에서 실행 중 코드를 끊을 방법이 없다. 무한루프 BT는 §11.1 wall-clock timeout으로만 차단되며, 매치는 무승부로 종료된다 (한쪽 실격이 아님).

---

## 12. 랭킹 산정

### 12.1 정렬 우선순위 (상위가 먼저)

1. **승점** (승 × 3 + 무 × 1)
2. **평균 잔여 HP** (총 잔여 HP / 매치 수) — 매치 수가 다른 팀 간 공정 비교

### 12.2 2팀 동률 보조

위 2-키로도 동률이면 **직접 대결(head-to-head)** 결과로 결정 (관리자 수동 적용).
직접 대결도 동률이면 무승부 처리.

3팀 이상 동률은 자동 정렬 키(평균 HP)로 결정.

---

## 13. RL 학습 비활성

본 플랫폼은 **BT 기반 경진대회 전용**. RL 학습 인프라(reward 함수, gradient 등)는 사용되지 않는다.
- `task.reward_functions = []` (빈 리스트)
- 매 스텝 reward는 항상 0
- 매치 결과는 reward 신호와 무관하게 §8 판정으로 결정

RNN 정책은 사전학습 모델로 추론만 사용.

---

## 14. 변경 이력 (2026-05~06 업데이트)

| 항목 | 변경 |
|---|---|
| 신규 BT 노드 | 조건 `AspectAngleAbove/Below`·`AltGapAbove/Below`, 액션 `VerticalLead`·`OneCircleFight`/`TwoCircleFight`·`OvershootAvoidance` (2026-06) |
| 선택적 Classical PD 제어기 | `AICOMBAT_CONTROLLER=classical` 로 RNN 우회 가능 (§3, 평가·연구용) (2026-06) |
| Tactical Maneuver 출력 | BT가 연속 maneuver 리스트를 출력하는 `set_maneuver()` 인터페이스 (2026-06) |
| 비행 안전 규정 | OVERLOAD/SPIN/STALL 누적 판정 신규 도입 (§6) |
| 적 정보 노출 | enm_health 등 3개 채널 제거 (§4.3) |
| 매치 결정론 | match.id 기반 시드 도입, np_random.shuffle 제거 (§10) |
| Wall-clock timeout | 60초 안전장치 신규 (§11.1) |
| BT 예외 격리 | DISQUALIFY 사유 신규 (§11.2) |
| 동률 처리 | 평균 잔여 HP 정렬 키 추가 (§12) |
| RL reward 시스템 | 비활성 (§13) |
| 결승 토너먼트 | 표준 시딩 + bye 자동 처리 (§9.2) |
| 매치 감사 | 의심 매치 재실행 스크립트 신규 (§10.3) |

---

## 15. 관련 파일

| 항목 | 위치 |
|---|---|
| 판정 로직 | `src/match/judge.py` |
| 비행 안전 모니터 | `src/control/flight_safety_monitor.py` |
| 매치 실행 | `src/match/runner_core.py` |
| WEZ 데미지 | `src/match/wez_engine.py` |
| HP 시스템 | `src/control/health_manager.py` |
| 시드 도출 | `src/match/seeding.py` |
| 결승 브라켓 | `src/tournament/bracket.py` |
| 감사 도구 | `src/tournament/audit.py`, `scripts/audit_match.py` |
| 매치 설정 | `src/simulation/envs/JSBSim/configs/1v1/NoWeapon/bt_vs_bt.yaml` |
| Classical PD 제어기 (선택) | `src/control/classical_controller.py` |
| Tactical Maneuver 인터페이스 | `src/behavior_tree/maneuvers.py` |
| 테스트 스위트 | `test/test_*.py` (총 69 케이스) |
