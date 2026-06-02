"""
Match Core - 매치 실행 핵심 로직 (보호 대상)

이 모듈은 Cython으로 컴파일되어 SDK에 .pyd 형태로 배포됩니다.
"""

from pathlib import Path
from typing import Optional, Callable, Dict
import sys
import time
import numpy as np
from datetime import datetime, timezone, timedelta

from src.simulation.envs.JSBSim.envs import SingleCombatEnv
from src.simulation.envs.JSBSim.core.catalog import JsbsimCatalog as _prp
from ..behavior_tree.task import BehaviorTreeTask
from .result import MatchResult
from src.control.health_manager import HealthGauge
from src.control.flight_safety_monitor import FlightSafetyMonitor
from .judge import MatchJudge, VictoryCondition
from ..utils.units import meters_to_feet
from .wez_engine import calculate_wez_damage
from .acmi_formatter import build_full_frame
from .replay_writer import ReplayWriter

KST = timezone(timedelta(hours=9))


def _print(msg: str):
    """Windows cp949 환경에서도 UTF-8로 안전하게 출력"""
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + '\n').encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()


class MatchCore:
    """매치 실행 핵심 로직"""

    def __init__(
        self,
        tree1_file: str,
        tree2_file: str,
        config_name: str = "1v1/NoWeapon/bt_vs_bt",
        max_steps: int = 1000,
        tree1_name: Optional[str] = None,
        tree2_name: Optional[str] = None,
        step_hook: Optional[Callable] = None,
        realtime_server=None,
        realtime_pacing: bool = False,
        seed: Optional[int] = None,
        wall_clock_timeout_sec: Optional[float] = 60.0,
    ):
        """
        Args:
            tree1_file: 첫 번째 행동트리 YAML 파일
            tree2_file: 두 번째 행동트리 YAML 파일
            config_name: LAG 환경 설정 이름
            max_steps: 최대 스텝 수
            tree1_name: 첫 번째 에이전트 이름 (선택)
            tree2_name: 두 번째 에이전트 이름 (선택)
            step_hook: 매 스텝 후 runner.py에서 호출되는 내부 훅
                시그니처: hook(step, task1, task2, health1, health2,
                              action1, action2, reward1, reward2, debug_info, env)
            realtime_server: TacviewRealtimeServer 인스턴스 (None이면 실시간 중계 비활성)
            realtime_pacing: True이면 실시간 페이싱 적용 (dt=0.2s 간격)
            seed: 결정론적 매치를 위한 시드 (None이면 비결정론). 같은 (tree pair, seed)는
                  반드시 동일한 결과를 만든다. 토너먼트 운영자는 match.id로부터 안정적인
                  정수 시드를 도출하여 전달해야 한다.
            wall_clock_timeout_sec: 매치당 실제 시간 상한(초). 시뮬레이션 시간이 아닌
                  wall-clock 기준으로 측정하여 JSBSim 폭주·BT 무한루프로부터 보호한다.
                  None이면 비활성(개발/디버깅 편의용). 기본값 60초.
                  realtime_pacing=True일 때는 비활성 권장.
        """
        self.tree1_file = tree1_file
        self.tree2_file = tree2_file
        self.config_name = config_name
        self.max_steps = max_steps
        self.tree1_name = tree1_name or Path(tree1_file).stem
        self.tree2_name = tree2_name or Path(tree2_file).stem
        self.step_hook = step_hook
        self.realtime_server = realtime_server
        self.realtime_pacing = realtime_pacing
        self.seed = seed
        # realtime_pacing이면 매치 wall-clock이 실제 시뮬레이션 시간(>5분)을 따라가므로
        # 자동으로 timeout을 무력화.
        self.wall_clock_timeout_sec = None if realtime_pacing else wall_clock_timeout_sec

        self.task1: Optional[BehaviorTreeTask] = None
        self.task2: Optional[BehaviorTreeTask] = None
        self.health1: Optional[HealthGauge] = None
        self.health2: Optional[HealthGauge] = None
        self._last_wez_debug: Optional[Dict] = None

    def run(
        self,
        replay_path: Optional[str] = None,
        verbose: bool = False,
    ) -> MatchResult:
        """매치 실행"""
        start_time = datetime.now(KST)
        # ACMI ReferenceTime: 오늘 날짜 UTC 12:00:00 고정
        # Tacview에서 #0.0 = 12:00:00.000 → 경과시간 = 표시시간 - 12:00:00
        _today_utc = datetime.now(timezone.utc).date()
        _acmi_ref = datetime(_today_utc.year, _today_utc.month, _today_utc.day,
                             12, 0, 0, tzinfo=timezone.utc)

        env = SingleCombatEnv(self.config_name)

        # 결정론적 매치: seed가 주어지면 모든 RNG 시드 고정.
        # JSBSim FDM 자체는 deterministic이므로 외부 RNG만 통제하면 충분.
        if self.seed is not None:
            import random as _random
            env.seed(int(self.seed))
            np.random.seed(int(self.seed) & 0xFFFFFFFF)
            _random.seed(int(self.seed))

        tree1_name = self.tree1_name
        tree2_name = self.tree2_name

        env.tree1_name = tree1_name
        env.tree2_name = tree2_name

        # max_steps=0이면 5분(300초)을 env.time_interval로 나눠 동적 계산.
        # Hz 변경에 무관하게 항상 동일한 실제 경기 시간을 보장.
        _MAX_DURATION_SEC = 300.0
        if self.max_steps <= 0:
            self.max_steps = max(1, int(round(_MAX_DURATION_SEC / float(env.time_interval))))
        env.config.max_steps = self.max_steps
        self.task1 = BehaviorTreeTask(env.config, tree_file=self.tree1_file)
        self.task2 = BehaviorTreeTask(env.config, tree_file=self.tree2_file)
        task1 = self.task1
        task2 = self.task2

        self.health1 = HealthGauge(initial_health=100.0)
        self.health2 = HealthGauge(initial_health=100.0)
        health1 = self.health1
        health2 = self.health2

        judge = MatchJudge(max_steps=self.max_steps)
        safety_monitor = FlightSafetyMonitor()

        if verbose:
            print("매치 시작:")
            print(f"  Tree 1: {Path(self.tree1_file).name} -> ego_id={env.ego_ids[0]}")
            print(f"  Tree 2: {Path(self.tree2_file).name} -> enm_id={env.enm_ids[0]}")
            print(f"  Config: {self.config_name}")
            print(f"  Max steps: {self.max_steps}")
            print(f"  Health: {health1.current_health} HP each")

        obs = env.reset()

        # 실시간 텔레메트리 매치 시작
        if self.realtime_server is not None:
            self.realtime_server.start_match(
                title=f"BT Match: {tree1_name} vs {tree2_name}",
                blue_id=env.ego_ids[0],
                red_id=env.enm_ids[0],
                blue_name=tree1_name,
                red_name=tree2_name,
            )

        replay_writer = None
        if replay_path:
            replay_path = Path(replay_path)
            if replay_path.exists():
                replay_path.unlink()
            with open(replay_path, 'w', encoding='utf-8-sig') as f:
                f.write("FileType=text/acmi/tacview\n")
                f.write("FileVersion=2.2\n")
                f.write("0,Author=AI-Combat Platform\n")
                f.write(f"0,Title=Behavior Tree Match: {tree1_name} vs {tree2_name}\n")
                f.write(f"0,ReferenceTime={_acmi_ref.strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
                f.write(f"0,Comments=Tree1={tree1_name}, Tree2={tree2_name}\n")
                f.write("0,Category=AI Dogfight\n")
                f.write("#0.0\n")
                ego_uid = env.ego_ids[0]
                enm_uid = env.enm_ids[0]
                f.write(f"{ego_uid},Type=Air+FixedWing,Name=F-16,Pilot={tree1_name},Color=Blue\n")
                f.write(f"{enm_uid},Type=Air+FixedWing,Name=F-16,Pilot={tree2_name},Color=Red\n")
            env._create_records = True  # env.render() 헤더 덮어쓰기 방지
            replay_writer = ReplayWriter(str(replay_path))
            replay_writer.start()

        _replay_prev_nodes: Dict[str, str] = {}
        total_reward_1 = 0.0
        total_reward_2 = 0.0
        step_count = 0
        done = False
        winner = None
        victory_condition = None
        next_step_time = time.perf_counter() if self.realtime_pacing else 0

        # Wall-clock timeout — JSBSim 폭주·BT 무한루프 안전장치
        _wc_start = time.perf_counter()
        _wc_limit = self.wall_clock_timeout_sec

        # 확정 주파수 (2026-05-26 A/B 검증): BT 10 Hz · RNN 5 Hz · env.step 20 Hz.
        # docs/BT_TICK_RATE_ANALYSIS.md §10 참조. 본 BT_TICK_EVERY는 env.time_interval에서
        # 동적 산출해 step rate 변경에도 자동 정합. AICOMBAT_BT_HZ 환경변수는 참가자가
        # 자기 트리의 Hz 민감도를 자가 진단할 때만 사용 (토너먼트는 디폴트 10 Hz 고정).
        # RNN(저수준 정책) 5 Hz 캐시는 HierarchicalSingleCombatTask.normalize_action에서 처리.
        import os as _os
        _bt_hz = float(_os.getenv('AICOMBAT_BT_HZ', '10'))
        BT_TICK_EVERY = max(1, round((1.0 / _bt_hz) / float(env.time_interval)))
        bt_tick_counter = BT_TICK_EVERY  # 첫 스텝에서 즉시 BT tick 실행
        action1 = None
        action2 = None

        # BT 실행 예외 시 즉시 매치 종료에 사용. 안전 기본 액션 (수평비행 유지).
        _SAFE_ACTION = np.array([2, 4, 2])
        _bt_disqualify: Optional[str] = None  # "agent1" | "agent2" | "draw"

        while not done and step_count < self.max_steps:
            if bt_tick_counter >= BT_TICK_EVERY:
                bt_tick_counter = 0
                # BT 예외 격리 — 한 쪽이 죽어도 매치 전체가 죽지 않게.
                # 예외 발생 BT는 DISQUALIFY로 즉시 패배 처리.
                try:
                    action1 = task1.get_high_level_action(env, env.ego_ids[0])
                except Exception as _bt_err1:
                    _print(f"[MatchCore] tree1 BT 예외 step={step_count}: {type(_bt_err1).__name__}: {_bt_err1}")
                    action1 = _SAFE_ACTION
                    _bt_disqualify = "agent1"
                try:
                    action2 = task2.get_high_level_action(env, env.enm_ids[0])
                except Exception as _bt_err2:
                    _print(f"[MatchCore] tree2 BT 예외 step={step_count}: {type(_bt_err2).__name__}: {_bt_err2}")
                    action2 = _SAFE_ACTION
                    _bt_disqualify = "draw" if _bt_disqualify == "agent1" else "agent2"
            bt_tick_counter += 1

            action = np.array([action1, action2])
            obs, reward, dones, info = env.step(action)

            # env.task._lowlevel_action_cache → task1/task2 동기화
            # env.step은 env.task.normalize_action만 호출하므로
            # 별도 BehaviorTreeTask 객체의 _last_low_level_action은 자동 갱신되지 않음.
            _ll_cache = getattr(env.task, '_lowlevel_action_cache', {})
            for _tsk, _aid in [(task1, env.ego_ids[0]), (task2, env.enm_ids[0])]:
                if _aid in _ll_cache:
                    _na = _ll_cache[_aid]
                    _tsk._last_low_level_action = {
                        "aileron": float(_na[0]),
                        "elevator": float(_na[1]),
                        "rudder": float(_na[2]),
                        "throttle": float(_na[3]),
                    }

            # 20 Hz condition subtick: blackboard 갱신(/Distance_ft, PS, BFM 등) +
            # BaseCondition.update() 호출. 액션 노드와 TimedAction 카운터는 영향 없음.
            try:
                task1.tick_conditions(env, env.ego_ids[0])
                task2.tick_conditions(env, env.enm_ids[0])
            except Exception as _tc_err:
                print(f"[MatchCore] tick_conditions error step={step_count}: {_tc_err}")

            control_inputs = {}
            prp = _prp
            for agent_id in [env.ego_ids[0], env.enm_ids[0]]:
                agent = env.agents[agent_id]
                try:
                    aileron_cmd = agent.get_property_value(prp.fcs_aileron_cmd_norm)
                    elevator_cmd = agent.get_property_value(prp.fcs_elevator_cmd_norm)
                    rudder_cmd = agent.get_property_value(prp.fcs_rudder_cmd_norm)
                    throttle_cmd = agent.get_property_value(prp.fcs_throttle_cmd_norm)
                    control_inputs[agent_id] = [aileron_cmd, elevator_cmd, rudder_cmd, throttle_cmd]
                except (AttributeError, KeyError, ValueError, TypeError):
                    control_inputs[agent_id] = [0.0, 0.0, 0.0, 0.5]

            dt = env.time_interval
            damage1, damage2, debug_info = self._calculate_wez_damage(env, dt)
            self._last_wez_debug = debug_info

            if verbose and step_count % 50 == 0 and debug_info:
                if 'error' in debug_info:
                    print(f"  [WEZ] Error: {debug_info['error']}")
                else:
                    print(f"  [WEZ] dist={meters_to_feet(debug_info['distance']):.0f}ft, "
                          f"ata1={debug_info['ata1']:.1f}, ata2={debug_info['ata2']:.1f}, "
                          f"dmg1={damage1:.2f}, dmg2={damage2:.2f}")

            if damage1 > 0:
                health1.take_damage(damage1, step_count)
                health2.deal_damage(damage1)
                if replay_writer:
                    replay_writer.write(f"0,Event=Bookmark|{env.enm_ids[0]}|[Red] HIT! {damage1:.2f} HP\n")
            if damage2 > 0:
                health2.take_damage(damage2, step_count)
                health1.deal_damage(damage2)
                if replay_writer:
                    replay_writer.write(f"0,Event=Bookmark|{env.ego_ids[0]}|[Blue] HIT! {damage2:.2f} HP\n")

            # 비행 안전 카운터 누적 (Hard Deck 다음 우선순위)
            safety_monitor.update(env, dt)

            # 단일 판정 — Hard Deck > Safety > HP > Timeout(시간 종료는 루프 종료 후 별도)
            try:
                _ego_pos = env.agents[env.ego_ids[0]].get_position()
                _enm_pos = env.agents[env.enm_ids[0]].get_position()
                _alt1_m = float(_ego_pos[2])
                _alt2_m = float(_enm_pos[2])
            except (AttributeError, KeyError, ValueError, TypeError):
                _alt1_m = _alt2_m = float('inf')  # 측정 실패 시 Hard Deck 미위반

            _j_winner, _j_cond = judge.judge(
                health1.current_health, health2.current_health,
                _alt1_m, _alt2_m, step_count,
                safety_monitor=safety_monitor,
                agent1_id=env.ego_ids[0],
                agent2_id=env.enm_ids[0],
            )
            if _j_winner is not None:
                winner = "draw" if _j_winner == "draw" else ("tree1" if _j_winner == "agent1" else "tree2")
                victory_condition = _j_cond
                done = True
                if replay_writer:
                    if _j_cond == VictoryCondition.HARD_DECK_VIOLATION:
                        _viol_uid = env.enm_ids[0] if winner == "tree1" else env.ego_ids[0]
                        replay_writer.write(f"0,Event=Bookmark|{_viol_uid}|[Hard Deck] 고도 위반 — {winner} 승리\n")
                    elif _j_cond in (VictoryCondition.OVERLOAD, VictoryCondition.SPIN, VictoryCondition.STALL):
                        _viol_uid = env.enm_ids[0] if winner == "tree1" else env.ego_ids[0]
                        replay_writer.write(f"0,Event=Bookmark|{_viol_uid}|[Safety] {_j_cond.value} — {winner} 승리\n")
                    elif _j_cond == VictoryCondition.SIMULTANEOUS_KO:
                        replay_writer.write("0,Event=Bookmark|[Simultaneous KO] 동시 격추 — 무승부\n")
                    elif _j_cond == VictoryCondition.DRAW:
                        replay_writer.write("0,Event=Bookmark|[Draw] 동시 위반 — 무승부\n")

            # BT 실행 예외 → 즉시 DISQUALIFY (judge·wall-clock보다 강한 우선순위로 처리)
            if not done and _bt_disqualify is not None:
                if _bt_disqualify == "agent1":
                    winner = "tree2"
                elif _bt_disqualify == "agent2":
                    winner = "tree1"
                else:
                    winner = "draw"
                victory_condition = VictoryCondition.DISQUALIFY
                done = True
                if replay_writer:
                    if winner == "draw":
                        replay_writer.write("0,Event=Bookmark|[DISQUALIFY] 양측 BT 실행 예외 — 무승부\n")
                    else:
                        _viol_uid = env.enm_ids[0] if winner == "tree1" else env.ego_ids[0]
                        replay_writer.write(f"0,Event=Bookmark|{_viol_uid}|[DISQUALIFY] BT 실행 예외 — {winner} 승리\n")

            # Wall-clock timeout 체크 — judge 다음, step_hook 이전
            if not done and _wc_limit is not None and (time.perf_counter() - _wc_start) > _wc_limit:
                winner = "draw"
                victory_condition = VictoryCondition.WALL_CLOCK_TIMEOUT
                done = True
                _print(f"[MatchCore] wall-clock timeout {_wc_limit:.1f}s 초과 — 매치 강제 종료")
                if replay_writer:
                    replay_writer.write(f"0,Event=Bookmark|[Wall-clock timeout] {_wc_limit:.0f}s 초과 — 강제 종료\n")

            reward1 = 0.0
            reward2 = 0.0
            if isinstance(reward, np.ndarray):
                if reward.ndim == 2 and reward.shape[0] >= 2:
                    reward1 = float(reward[0, 0])
                    reward2 = float(reward[1, 0])
                elif reward.ndim == 1 and len(reward) >= 2:
                    reward1 = float(reward[0])
                    reward2 = float(reward[1])
                else:
                    reward1 = float(reward.flatten()[0]) if reward.size > 0 else 0.0
            else:
                reward1 = float(reward)

            _in_wez1 = debug_info.get('in_wez1', False) if debug_info and 'in_wez1' in debug_info else False
            _in_wez2 = debug_info.get('in_wez2', False) if debug_info and 'in_wez2' in debug_info else False
            # 적 HP/적이 받은 데미지는 전달하지 않는다 (룰북 정보 비공개 정책).
            # reward 채널 없음 — RL 학습 미사용.
            _inject1 = getattr(task1, 'inject_match_state', None)
            if _inject1:
                _inject1(
                    ego_health=health1.current_health,
                    ego_damage_dealt=health1.total_damage_dealt,
                    ego_damage_received=health1.total_damage_taken,
                    in_wez=_in_wez1,
                    enm_in_wez=_in_wez2,
                )
            _inject2 = getattr(task2, 'inject_match_state', None)
            if _inject2:
                _inject2(
                    ego_health=health2.current_health,
                    ego_damage_dealt=health2.total_damage_dealt,
                    ego_damage_received=health2.total_damage_taken,
                    in_wez=_in_wez2,
                    enm_in_wez=_in_wez1,
                )

            # step_hook: runner.py의 CSV/콜백 레이어가 주입하는 훅
            if self.step_hook is not None:
                try:
                    self.step_hook(
                        step=step_count,
                        task1=task1,
                        task2=task2,
                        health1=health1,
                        health2=health2,
                        action1=action1,
                        action2=action2,
                        reward1=reward1,
                        reward2=reward2,
                        debug_info=debug_info,
                        env=env,
                    )
                except Exception as _hook_err:
                    print(f"[MatchCore] step_hook error step={step_count}: {_hook_err}")

            # ── BT 노드 정보 수집 (파일 + 실시간 공용) ──
            _bt_info: Dict[str, dict] = {}
            for _aid, _tsk, _tname, _clr in [
                (env.ego_ids[0], task1, tree1_name, 'Blue'),
                (env.enm_ids[0], task2, tree2_name, 'Red'),
            ]:
                _node_info: dict = {
                    'color': _clr, 'tree_name': _tname,
                    'active_node': '', 'node_path': '',
                }
                if hasattr(_tsk, 'get_last_active_nodes'):
                    _active = _tsk.get_last_active_nodes()
                    if _active:
                        _an = [n for n, s in _active if s == 'SUCCESS']
                        if _an:
                            _node_info['active_node'] = _an[-1]
                            _node_info['node_path'] = ">".join([n for n, s in _active])
                _bt_info[_aid] = _node_info

            _health_map = {
                env.ego_ids[0]: health1.current_health,
                env.enm_ids[0]: health2.current_health,
            }
            _reward_map = {env.ego_ids[0]: reward1, env.enm_ids[0]: reward2}

            # ── 프레임 공통 생성 (리플레이 & 실시간 텔레메트리) ──
            _frame = None
            if replay_writer or self.realtime_server is not None:
                try:
                    _frame = build_full_frame(
                        env=env,
                        sim_time=(step_count + 1) * env.time_interval,
                        control_inputs=control_inputs,
                        wez_debug=self._last_wez_debug,
                        health_map=_health_map,
                        reward_map=_reward_map,
                        bt_info=_bt_info,
                        step_count=step_count,
                        max_steps=self.max_steps,
                        use_extended_log=True,
                        prev_node_map=_replay_prev_nodes,
                    )
                except Exception:
                    pass

            # ── Tacview 리플레이 기록 (비동기, build_full_frame 사용) ──
            if replay_writer and _frame:
                try:
                    replay_writer.write(_frame)
                except Exception:
                    pass

            # ── 실시간 텔레메트리 프레임 전송 ──
            if self.realtime_server is not None and _frame:
                try:
                    self.realtime_server.send_frame(_frame)
                except Exception:
                    pass

            total_reward_1 += reward1
            total_reward_2 += reward2
            step_count += 1
            if not done:
                done = dones.any() if isinstance(dones, np.ndarray) else dones

            if verbose and step_count % 50 == 0:
                print(f"  Step {step_count}: reward={reward}")

            # 실시간 페이싱
            if self.realtime_pacing:
                next_step_time += env.time_interval
                sleep_time = next_step_time - time.perf_counter()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                elif sleep_time < -0.05:
                    next_step_time = time.perf_counter()

        if replay_writer:
            replay_writer.stop()
        env.close()

        # 실시간 텔레메트리 매치 종료
        if self.realtime_server is not None:
            winner_display = tree1_name if winner == "tree1" else tree2_name if winner == "tree2" else "무승부"
            self.realtime_server.end_match(winner=winner_display)

        # max_steps 도달로 while loop이 끝났다면 judge에 timeout 분기 위임
        if winner is None:
            try:
                _ego_pos = env.agents[env.ego_ids[0]].get_position()
                _enm_pos = env.agents[env.enm_ids[0]].get_position()
                _alt1_final = float(_ego_pos[2])
                _alt2_final = float(_enm_pos[2])
            except (AttributeError, KeyError, ValueError, TypeError):
                _alt1_final = _alt2_final = float('inf')
            _j_winner, _j_cond = judge.judge(
                health1.current_health, health2.current_health,
                _alt1_final, _alt2_final, self.max_steps,
                safety_monitor=safety_monitor,
                agent1_id=env.ego_ids[0],
                agent2_id=env.enm_ids[0],
            )
            if _j_winner is None:
                winner = "draw"
                victory_condition = VictoryCondition.TIMEOUT
            else:
                winner = "draw" if _j_winner == "draw" else ("tree1" if _j_winner == "agent1" else "tree2")
                victory_condition = _j_cond

        end_time = datetime.now(KST)
        duration = (end_time - start_time).total_seconds()

        result = MatchResult(
            tree1_file=self.tree1_file,
            tree2_file=self.tree2_file,
            winner=winner,
            total_steps=step_count,
            tree1_reward=float(total_reward_1),
            tree2_reward=float(total_reward_2),
            replay_file=str(replay_path) if replay_path else None,
            duration_seconds=duration,
            timestamp=start_time.isoformat(),
        )
        result.tree1_health = health1.current_health
        result.tree2_health = health2.current_health
        result.tree1_damage_dealt = health1.total_damage_dealt
        result.tree2_damage_dealt = health2.total_damage_dealt
        result.victory_condition = victory_condition.value if victory_condition else VictoryCondition.TIMEOUT.value

        winner_display = tree1_name if winner == "tree1" else tree2_name if winner == "tree2" else "무승부"
        _print("\n매치 완료:")
        _print(f"  승자: {winner_display} [{result.victory_condition}]")
        _print(f"  스텝: {step_count} / {self.max_steps}")
        _print(f"  소요 시간: {duration:.2f}초")
        _print(f"  {tree1_name}: {health1.current_health:.1f} HP")
        _print(f"  {tree2_name}: {health2.current_health:.1f} HP")

        return result

    def _calculate_wez_damage(self, env, dt: float) -> tuple:
        """Gun WEZ 체크 및 데미지 계산 (wez_engine 위임)"""
        try:
            ego_sim = env.agents[env.ego_ids[0]]
            enm_sim = env.agents[env.enm_ids[0]]

            ep = ego_sim.get_position()
            np_ = enm_sim.get_position()
            ev = ego_sim.get_velocity()
            nv = enm_sim.get_velocity()

            result = calculate_wez_damage(
                ego_pos=[ep[0], ep[1], -ep[2]],
                enm_pos=[np_[0], np_[1], -np_[2]],
                ego_vel=[ev[0], ev[1], -ev[2]],
                enm_vel=[nv[0], nv[1], -nv[2]],
                ego_roll=float(ego_sim.get_rpy()[0]),
                enm_roll=float(enm_sim.get_rpy()[0]),
                dt=float(dt),
            )
            return result['damage1'], result['damage2'], result
        except (AttributeError, KeyError, ValueError, TypeError) as e:
            return 0.0, 0.0, {'error': str(e)}
