# Changelog 2026 — 경진대회 플랫폼 강화

본 문서는 **2026 공군사관생도 AI Pilot 경진대회** 운영을 위해 진행된 플랫폼 강화 작업의 요약이다.
공식 룰은 [RULEBOOK.md](RULEBOOK.md), 운영 가이드는 [TOURNAMENT.md](TOURNAMENT.md) 참조.

## 작업 요약

| 주차 | 작업 영역 | 신규 기능 수 | 신규 테스트 |
|---|---|---|---|
| Week 1 | 룰 악용 차단 + 안정성 | 4 | 29 |
| Week 2 | 공정성 + 정리 | 3 | 18 |
| Week 3 | 통합 검증 + 운영 도구 | 3 | 22 |
| **합계** | | **10** | **69** |

전체 69개 테스트 통과. SDK 빌드 무관(`scripts/build_sdk.py`로 재빌드 필요).

---

## Week 1: 룰 악용 차단 + 매치 안정성

### EX-1: 비행 안전 강제 종료
**문제**: BT가 9G 락업·스핀·실속을 무한 유지해 매치를 시간초과(타임아웃 무승부)로 끌고 가는 게이밍 가능.

**해결**: 신규 [`FlightSafetyMonitor`](../src/control/flight_safety_monitor.py) — 매 스텝 G·roll rate·CAS 측정, 임계 누적 시간 초과 시 위반자 패배.

| 사유 | 임계 | 누적 시간 |
|---|---|---|
| OVERLOAD | \|n_pilot\| > 9 G | 5 초 |
| SPIN | \|roll rate\| > 360°/s | 3 초 |
| STALL | CAS < 100 kts | 10 초 |

누적은 연속이 아닌 **매치 전체 누적** — 짧게 끊어 들어갔다 나오는 회피 차단.

### F-2: 결정론적 매치
**문제**: 매 매치마다 `np_random.shuffle`로 Blue/Red 위치 swap + 시드 미고정 → 동일 BT pair 재실행 시 결과 변동.

**해결**:
- [`singlecombat_env.reset_simulators`](../src/simulation/envs/JSBSim/envs/singlecombat_env.py)의 shuffle 제거 (Blue=서쪽 고정)
- 신규 [`src/match/seeding.derive_seed`](../src/match/seeding.py) — `match.id`로부터 SHA-256 → 32비트 시드. PYTHONHASHSEED 무관 안정성.
- MatchCore에 `seed` 파라미터 추가, `env.seed/np.random.seed/random.seed` 일괄 적용.

**효과**: 같은 (BT pair, seed)는 winner·steps·HP까지 완전 일치. 감사 도구의 기반.

### EX-4: Wall-clock Timeout
**문제**: JSBSim 폭주·BT 무한루프 시 매치가 영원히 안 끝남.

**해결**: MatchCore에 `wall_clock_timeout_sec` 파라미터 (기본 60초). 매 스텝 `time.perf_counter()` 검사. 초과 시 무승부(`wall_clock_timeout`)로 강제 종료. realtime_pacing 시 자동 비활성.

### EX-3: BT 예외 격리
**문제**: 참가자 BT가 `get_high_level_action()`에서 예외를 던지면 매치 전체 다운.

**해결**: BT tick 호출을 try/except로 격리. 예외 발생 시:
- 해당 BT는 즉시 **DISQUALIFY**
- 상대 BT가 승리
- 매치는 정상 종료

양측 동시 예외 시 무승부. Python 한계로 외부 timeout은 불가능 → 무한루프는 EX-4의 wall-clock으로만 차단.

---

## Week 2: 공정성 + 코드 정리

### EX-2: 적 정보 비공개
**문제**: BT가 `inject_match_state`로 `enm_health`·`enm_damage_*`를 직접 관측 가능 → "적 HP < 30이면 다이브 유도" 같은 메타 게이밍 가능.

**해결**: `inject_match_state` 시그니처에서 다음 3개 키 제거:
- `enm_health` (적 현재 HP)
- `enm_damage_dealt` (자기가 받은 누적과 동치, 중복)
- `enm_damage_received` (적 HP 역산 가능)

남은 5개 채널: `ego_health`, `ego_damage_dealt`, `ego_damage_received`, `in_wez`, `enm_in_wez`.

**Note**: CSV/ACMI 로그(운영자용)에는 enm_health가 여전히 기록됨 — 사후 분석에만 사용.

### F-3: 동률 처리 명확화
**문제**: 리더보드 정렬이 `(승점, ELO)`만 사용. `total_hp_remaining` 누적 카운터는 있으나 정렬에 미사용 → 매치 수가 다른 팀 간 불공정.

**해결**:
- 정렬 키 변경: `(승점, 평균 잔여 HP, ELO)` — `Team.avg_hp_remaining` 활용
- 신규 `head_to_head_winner(team_a, team_b)` 헬퍼 — 2팀 동률 시 운영자 수동 tiebreaker

### Phase 1: RL Reward 시스템 비활성
**문제**: `reward_functions/` 전체가 RL 학습용. BT 경진대회는 reward 사용 안 함 → dead code.

**해결**:
- `SingleCombatTask.reward_functions = []` (빈 리스트로 설정)
- [`bt_vs_bt.yaml`](../src/simulation/envs/JSBSim/configs/1v1/NoWeapon/bt_vs_bt.yaml)의 `*Reward_*` 파라미터 제거
- `inject_match_state`에서 `reward` 인자 제거
- `reward_functions/` 폴더는 legacy yaml 호환을 위해 보존

매 스텝 reward는 항상 0. 매치 결과는 judge 판정으로만 결정.

---

## Week 3: 통합 검증 + 운영 도구

### 비행 안전 시나리오 통합 검증
[`test/test_safety_scenarios.py`](../test/test_safety_scenarios.py) — monkey patch로 강제 액션 주입 후 실제 JSBSim 매치 실행.

- Hard Deck dive: 강제 `[Δalt=0, Δhdg=4, Δv=4]` → 620 스텝(4.8초)에서 `HARD_DECK_VIOLATION` 발화 검증
- 수평비행: 안전 위반 사유 미발화 확인

### UX-2: 결승 토너먼트 브라켓
**문제**: 기존 `bracket.py`는 round-robin과 단순 단일 토너먼트만 제공. 표준 시딩/bye 미지원.

**해결**: [`BracketGenerator`](../src/tournament/bracket.py)에 추가:
- `generate_seeded_bracket(seeded_teams, phase)` — 표준 시딩 (1vN, 2vN-1...) + bye 자동 처리
- `generate_next_round(prev, next_phase)` — 라운드 진행 + 무승부 거부

`MatchPhase` enum 확장: `QUARTERFINALS`, `THIRD_PLACE` 추가.

### UX-3: 매치 감사 도구
**기반**: F-2 결정론.

**해결**: 신규 [`src/tournament/audit.py`](../src/tournament/audit.py) + CLI [`scripts/audit_match.py`](../scripts/audit_match.py).

- `MatchSnapshot` — 비교 가능 필드만 추출 (부동소수점 6자리 반올림)
- `audit_match(...)` — 동일 시드로 재실행 후 원본과 비교 → 일치/불일치 + 어느 필드가 달라졌는지 보고
- CLI: `python scripts/audit_match.py <match_id>`

이의제기 대응 시 코드 변경·BT 파일 변경·환경 변경 진단에 사용.

---

## 신규 VictoryCondition 사유

| 사유 | enum value | 의미 |
|---|---|---|
| `OVERLOAD` | `overload` | 9G 누적 5초 초과 |
| `SPIN` | `spin` | 360°/s roll rate 누적 3초 초과 |
| `STALL` | `stall` | CAS<100kts 누적 10초 초과 |
| `SIMULTANEOUS_KO` | `simultaneous_ko` | 양측 HP 동시 0 |
| `DRAW` | `draw` | 양측 동시 위반 (Hard Deck/Safety/예외) |
| `WALL_CLOCK_TIMEOUT` | `wall_clock_timeout` | 매치 실제 시간 60초 초과 (안전장치) |
| `DISQUALIFY` | `disqualify` | BT 실행 예외 |

기존 사유 보존: `HEALTH_ZERO`, `HEALTH_ADVANTAGE`, `HARD_DECK_VIOLATION`, `TIMEOUT`, `NONE`.

---

## 단일 판정 경로 (판정 우선순위)

기존: `runner_core`에 HP/Hard Deck 자체 분기 + `MatchJudge`에 또 다른 분기 (중복).

신규: 모든 판정을 `MatchJudge.judge()` 단일 호출에 위임. 우선순위:

```
Hard Deck > 비행 안전(Overload/Spin/Stall) > HP zero > DISQUALIFY > Wall-clock > Timeout
```

`runner_core`의 중복 분기 제거 → -50 LoC.

---

## 테스트 스위트

| 파일 | 케이스 | 영역 |
|---|---|---|
| `test_judge_safety.py` | 20 | Judge 우선순위 + FlightSafetyMonitor 임계·누적 |
| `test_seeding.py` | 7 | derive_seed 결정론 |
| `test_match_determinism.py` | 2 | 실제 매치 결정론 (통합) |
| `test_wall_clock_timeout.py` | 3 | wall-clock 안전장치 |
| `test_bt_isolation.py` | 3 | BT 예외 격리 (monkey patch) |
| `test_info_hiding.py` | 4 | 적 정보 비공개 (BT observation) |
| `test_safety_scenarios.py` | 2 | Hard Deck dive 실제 매치 |
| `test_bracket.py` | 13 | 결승 시딩 + bye + next_round |
| `test_audit.py` | 7 | 감사 도구 비교 로직 |
| `test_leaderboard.py` | 8 | 정렬 + head-to-head |
| **합계** | **69** | |

실행:
```bash
for f in test/test_*.py; do python $f; done
```

---

## 저수준 제어 실험 — Classical PD 제어기 + BT output 재설계 (2026-06)

> 모두 **선택적**이며 기본 동작(RNN 저수준 정책)은 그대로다. 후방호환 유지.

### CT-1: Classical PD 제어기 (NN 우회)
**배경**: 학습된 RNN이 G-load 균형을 위해 throttle을 자동 감속시켜 코너 스피드(~400 kts) 미달 평형에 갇히는 현상.

**해결**: `AICOMBAT_CONTROLLER=classical` 설정 시 RNN을 우회하고 [`src/control/classical_controller.py`](../src/control/classical_controller.py) 의 stateless PD가 조종면을 직접 생성. BT 5×9×5 → 연속 setpoint(PITCH ±20° / BANK ±65° / SPEED 220~450 kts) 매핑 + 저역통과(α=0.85) 추종. 자세한 설명은 [제어기정리.md](제어기정리.md) Slide 3.

### CT-2: Tactical Maneuver 출력 인터페이스 (Z-series)
**배경**: 225개 이산 격자가 복잡 트리의 전술 의도를 격자화로 손실.

**해결**: [`src/behavior_tree/maneuvers.py`](../src/behavior_tree/maneuvers.py) — BT 노드가 6타입(`BANK_HOLD`/`PITCH_HOLD`/`SPEED_HOLD`/`ALT_HOLD`/`ATA_TRACK`/`HEADING_HOLD`) 연속 maneuver 리스트를 `BaseAction.set_maneuver()` 로 출력. Classical 제어기가 weighted blend로 정밀 추종. `set_action()` 은 자동 변환되어 기존 트리도 동작.

### CT-3: 신규 BT 노드
- **액션**: `VerticalLead`(수직 우위 다이브), `OneCircleFight`/`TwoCircleFight`(rate fight), `OvershootAvoidance`(오버슛 회피).
- **조건**: `AspectAngleAbove`/`AspectAngleBelow`(적 기동/비기동 판정), `AltGapAbove`/`AltGapBelow`(고도차 게이트), `ClosureRateAbove`/`ClosureRateBelow`.
- 노드 카탈로그: [sdk/docs/NODE_REFERENCE.md](../sdk/docs/NODE_REFERENCE.md).

### CT-4: red5 전술 트리 진화 + 평가 도구
- red5(고급)를 Aspect Angle 기반 기동/비기동 표적 분리로 재구성(`ManeuveringTargetLead`/`NonManeuveringEnergyConvert`).
- 평가 도구: [`scripts/redteam_round_robin.py`](../scripts/redteam_round_robin.py), [`scripts/redteam_multi_seed.py`](../scripts/redteam_multi_seed.py), [`scripts/eval_red5.py`](../scripts/eval_red5.py), [`scripts/measure_jsbsim_trim.py`](../scripts/measure_jsbsim_trim.py), [`scripts/test_classical_stability.py`](../scripts/test_classical_stability.py).

---

## 변경된 핵심 파일

| 파일 | 변경 유형 |
|---|---|
| `src/control/flight_safety_monitor.py` | 신규 |
| `src/match/seeding.py` | 신규 |
| `src/tournament/audit.py` | 신규 |
| `scripts/audit_match.py` | 신규 |
| `docs/RULEBOOK.md` | 신규 |
| `docs/CHANGELOG_2026.md` | 신규 (본 문서) |
| `src/match/judge.py` | 재작성 (단일 진실, 7개 신규 사유) |
| `src/match/runner_core.py` | 자체 분기 제거, judge 통합, seed/timeout/예외 격리 |
| `src/match/runner.py` | seed·timeout 인자 전파 |
| `src/behavior_tree/task.py` | inject 시그니처 축소 (enm 채널·reward 제거) |
| `src/tournament/manager.py` | head_to_head_winner, 정렬 키 3-튜플, match.id 시드 |
| `src/tournament/bracket.py` | seeded_bracket + next_round |
| `src/tournament/models.py` | QUARTERFINALS/THIRD_PLACE phase 추가 |
| `src/simulation/envs/JSBSim/envs/singlecombat_env.py` | shuffle 제거 |
| `src/simulation/envs/JSBSim/tasks/singlecombat_task.py` | reward_functions=[] |
| `src/simulation/envs/JSBSim/configs/1v1/NoWeapon/bt_vs_bt.yaml` | Reward 파라미터 제거 |
| `src/match/runner_human_vs_bt.py` | inject 시그니처 변경 반영 |
| `sdk/docs/GUIDE.md` | §1.2 판정·§1.2.1 BT 채널 정책 갱신 |
| `docs/TOURNAMENT.md` | §10·§11 결승/감사 섹션 추가 |
| `src/control/classical_controller.py` | 신규 (CT-1, Classical PD) |
| `src/control/mid_level_autopilot.py` | 신규 (미들 레이어 PD, 실험) |
| `src/behavior_tree/maneuvers.py` | 신규 (CT-2, Tactical Maneuver) |
| `src/behavior_tree/nodes/actions.py` | VerticalLead·OneCircleFight·TwoCircleFight·OvershootAvoidance 등 추가, maneuver-native 재작성 |
| `src/behavior_tree/nodes/conditions.py` | AspectAngleAbove/Below·AltGapAbove/Below 등 추가 |
| `src/simulation/envs/JSBSim/tasks/singlecombat_task.py` | classical 제어 분기(`_use_classical_controller`) 추가 |

---

## SDK 재배포

코드 변경(특히 `runner_core.py`·`judge.py`·`task.py`)이 SDK에 반영되려면:

```bash
python scripts/build_sdk.py
```

`ai-combat-sdk/` 폴더로 빌드 산출물이 갱신된다. 참가자는 SDK 재다운로드 필요.
