"""
Match Runner - CSV 로깅 및 step_callback 레이어

핵심 매치 로직은 runner_core.MatchCore (.pyd 보호)에 위임합니다.
참가자는 이 파일을 참고하여 step_callback을 구현할 수 있습니다.
"""

from pathlib import Path
from typing import Optional, Callable
import sys
import csv

from .runner_core import MatchCore
from .result import MatchResult


def _print(msg: str):
    """Windows cp949 환경에서도 UTF-8로 안전하게 출력"""
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((msg + '\n').encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()


class BehaviorTreeMatch:
    """두 행동트리 간 대전 실행 (공개 인터페이스)

    핵심 매치 로직은 MatchCore에 위임되며,
    이 클래스는 CSV 로깅 및 step_callback 레이어를 담당합니다.
    """

    # CSV 컬럼 순서 정의 (에이전트별로 반복)
    _CSV_COLUMNS = [
        "step",
        "sim_time_sec",       # 실제 시뮬레이션 시간 (초) — ACMI 타임스탬프와 동일
        "agent_id",
        "tree_name",
        # --- 위치/자세 (ACMI T필드 + HDG) ---
        "ego_altitude_ft",
        "ego_vc_kts",         # CAS (계기속도) — ACMI CAS 필드와 동일 속도
        "heading_deg",        # ACMI HDG 필드
        "roll_deg",
        "pitch_deg",
        # --- 에너지 ---
        "specific_energy_ft",
        "ps_fts",
        # --- 교전 기하학 (ACMI Distance/ATA/AA/HCA/TAU/ClosureRate) ---
        "distance_ft",
        "ata_deg",
        "aa_deg",
        "hca_deg",
        "tau_deg",
        "alt_gap_ft",
        "closure_rate_kts",
        "turn_rate_degs",
        "in_39_line",
        "overshoot_risk",
        "tc_type",
        "ata_lead_deg",
        "tau_lead_deg",
        "side_flag",
        "energy_diff_ft",
        # --- BFM 분류 ---
        "bfm_situation",
        # --- 체력/데미지 (ACMI Health/InWEZ) ---
        "ego_health",
        "enm_health",
        "ego_damage_dealt",
        "enm_damage_dealt",
        "in_wez",
        "enm_in_wez",
        # --- 보상 (ACMI Reward) ---
        "reward",
        # [BT] 고수준 액션 (이산 인덱스)
        "action_altitude",
        "action_heading",
        "action_velocity",
        # [RNN] 저수준 제어 명령 (ACMI RollControlInput 등)
        "aileron",
        "elevator",
        "rudder",
        "throttle",
        # [JSBSim] 실제 서보 위치 (ACMI RollControlPosition 등)
        "servo_aileron",
        "servo_elevator",
        "servo_rudder",
        # --- 노드 활성화 (ACMI ActiveNode/NodePath) ---
        "active_node",
        "active_nodes_path",
    ]

    def __init__(
        self,
        tree1_file: str,
        tree2_file: str,
        config_name: str = "1v1/NoWeapon/bt_vs_bt",
        max_steps: int = 0,
        tree1_name: Optional[str] = None,
        tree2_name: Optional[str] = None,
        step_callback: Optional[Callable] = None,
        log_csv: Optional[str] = None,
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
            step_callback: 매 틱마다 호출되는 콜백 함수 (선택)
                시그니처: callback(step, agent_id, obs, action, low_level_action,
                                   reward, health, active_nodes, bfm_situation)
            log_csv: CSV 로그 파일 경로 (선택, None이면 저장 안 함)
                예: "logs/match_log.csv"
            seed: 결정론적 매치를 위한 시드. 같은 (tree pair, seed)는 같은 결과를 보장.
                  None이면 비결정론 (개발/디버깅 편의용).
            wall_clock_timeout_sec: 매치당 실제 시간 상한(초). JSBSim/BT hang으로부터
                  매치를 강제 종료시킨다. None이면 비활성. 기본 60초.
        """
        self.tree1_file = tree1_file
        self.tree2_file = tree2_file
        self.config_name = config_name
        self.max_steps = max_steps
        self.tree1_name = tree1_name
        self.tree2_name = tree2_name
        self.step_callback = step_callback
        self.log_csv = log_csv
        self.seed = seed
        self.wall_clock_timeout_sec = wall_clock_timeout_sec

    def run(
        self,
        replay_path: Optional[str] = None,
        verbose: bool = False,
    ) -> MatchResult:
        """매치 실행

        Args:
            replay_path: Tacview 리플레이 저장 경로 (None이면 저장 안 함)
            verbose: 상세 출력 여부

        Returns:
            MatchResult 객체
        """
        # CSV 파일 초기화
        csv_file = None
        csv_writer = None
        if self.log_csv:
            log_path = Path(self.log_csv)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            csv_file = open(log_path, 'w', newline='', encoding='utf-8')
            csv_writer = csv.DictWriter(csv_file, fieldnames=self._CSV_COLUMNS)
            csv_writer.writeheader()

        # step_hook 클로저: MatchCore의 매 스텝 후 CSV/콜백 실행
        _csv_writer = csv_writer
        _step_callback = self.step_callback
        _tree1_file = self.tree1_file
        _tree2_file = self.tree2_file

        def _step_hook(step, task1, task2, health1, health2,
                       action1, action2, reward1, reward2, debug_info, env):
            for i, (task_i, agent_id_i, action_i, reward_i, h_self, h_enm) in enumerate([
                (task1, env.ego_ids[0], action1, reward1, health1, health2),
                (task2, env.enm_ids[0], action2, reward2, health2, health1),
            ]):
                obs_i = task_i.blackboard.observation
                ll_act = getattr(task_i, '_last_low_level_action',
                                 {"aileron": 0.0, "elevator": 0.0, "rudder": 0.0, "throttle": 0.5})
                active_nodes_i = task_i.get_last_active_nodes()
                bfm_i = str(obs_i.get("bfm_situation", ""))

                active_node_name = ""
                active_nodes_path = ""
                if active_nodes_i:
                    success_nodes = [n for n, s in active_nodes_i if s == 'SUCCESS']
                    active_node_name = success_nodes[-1] if success_nodes else ""
                    active_nodes_path = ">".join([n for n, s in active_nodes_i])

                tree_name_i = Path(_tree1_file).stem if i == 0 else Path(_tree2_file).stem

                if _csv_writer is not None:
                    try:
                        # [JSBSim] 실제 서보 위치 읽기 (ACMI RollControlPosition 등)
                        try:
                            from src.simulation.envs.JSBSim.core.catalog import JsbsimCatalog as _prp
                            _agent = env.agents[agent_id_i]
                            _srv_ail = float(_agent.get_property_value(_prp.fcs_left_aileron_pos_norm) or 0.0)
                            _srv_ele = float(_agent.get_property_value(_prp.fcs_elevator_pos_norm) or 0.0)
                            _srv_rud = float(_agent.get_property_value(_prp.fcs_rudder_pos_norm) or 0.0)
                        except Exception:
                            _srv_ail = _srv_ele = _srv_rud = 0.0

                        row = {
                            "step": step + 1,
                            "sim_time_sec": round((step + 1) * float(env.time_interval), 3),
                            "agent_id": agent_id_i,
                            "tree_name": tree_name_i,
                            "ego_altitude_ft": obs_i.get("ego_altitude_ft", ""),
                            "ego_vc_kts": obs_i.get("ego_vc_kts", ""),
                            "heading_deg": obs_i.get("heading_deg", ""),
                            "roll_deg": obs_i.get("roll_deg", ""),
                            "pitch_deg": obs_i.get("pitch_deg", ""),
                            "specific_energy_ft": obs_i.get("specific_energy_ft", ""),
                            "ps_fts": obs_i.get("ps_fts", ""),
                            "distance_ft": obs_i.get("distance_ft", ""),
                            "ata_deg": obs_i.get("ata_deg", ""),
                            "aa_deg": obs_i.get("aa_deg", ""),
                            "hca_deg": obs_i.get("hca_deg", ""),
                            "tau_deg": obs_i.get("tau_deg", ""),
                            "alt_gap_ft": obs_i.get("alt_gap_ft", ""),
                            "closure_rate_kts": obs_i.get("closure_rate_kts", ""),
                            "turn_rate_degs": obs_i.get("turn_rate_degs", ""),
                            "in_39_line": obs_i.get("in_39_line", ""),
                            "overshoot_risk": obs_i.get("overshoot_risk", ""),
                            "tc_type": obs_i.get("tc_type", ""),
                            "ata_lead_deg": obs_i.get("ata_lead_deg", ""),
                            "tau_lead_deg": obs_i.get("tau_lead_deg", ""),
                            "side_flag": obs_i.get("side_flag", ""),
                            "energy_diff_ft": obs_i.get("energy_diff_ft", ""),
                            "bfm_situation": bfm_i,
                            "ego_health": h_self.current_health,
                            "enm_health": h_enm.current_health,
                            "ego_damage_dealt": h_self.total_damage_dealt,
                            "enm_damage_dealt": h_enm.total_damage_dealt,
                            "in_wez": debug_info.get('in_wez1' if i == 0 else 'in_wez2', False) if debug_info and 'in_wez1' in debug_info else False,
                            "enm_in_wez": debug_info.get('in_wez2' if i == 0 else 'in_wez1', False) if debug_info and 'in_wez1' in debug_info else False,
                            "reward": reward_i,
                            "action_altitude": int(action_i[0]),
                            "action_heading": int(action_i[1]),
                            "action_velocity": int(action_i[2]),
                            "aileron": ll_act.get("aileron", ""),
                            "elevator": ll_act.get("elevator", ""),
                            "rudder": ll_act.get("rudder", ""),
                            "throttle": ll_act.get("throttle", ""),
                            "servo_aileron": _srv_ail,
                            "servo_elevator": _srv_ele,
                            "servo_rudder": _srv_rud,
                            "active_node": active_node_name,
                            "active_nodes_path": active_nodes_path,
                        }
                        _csv_writer.writerow(row)
                    except Exception as _csv_err:
                        print(f"[CSV] row write error step={step}: {_csv_err}")

                if _step_callback is not None:
                    try:
                        _step_callback(
                            step=step,
                            agent_id=agent_id_i,
                            obs=obs_i,
                            action=action_i.tolist(),
                            low_level_action=ll_act,
                            reward=reward_i,
                            health={"ego": h_self.current_health, "enm": h_enm.current_health},
                            active_nodes=active_nodes_i,
                            bfm_situation=bfm_i,
                        )
                    except Exception as e:
                        print(f"[Runner] step_callback error (step={step}, agent={agent_id_i}): {e}")
                        import traceback
                        traceback.print_exc()

        # MatchCore에 step_hook 주입하여 실행
        core = MatchCore(
            tree1_file=self.tree1_file,
            tree2_file=self.tree2_file,
            config_name=self.config_name,
            max_steps=self.max_steps,
            tree1_name=self.tree1_name,
            tree2_name=self.tree2_name,
            step_hook=_step_hook if (csv_writer is not None or self.step_callback is not None) else None,
            seed=self.seed,
            wall_clock_timeout_sec=self.wall_clock_timeout_sec,
        )

        try:
            result = core.run(replay_path=replay_path, verbose=verbose)
        finally:
            if csv_file is not None:
                csv_file.close()
                if self.log_csv:
                    _print(f"  CSV 로그 저장: {self.log_csv}")

        # task/health 참조를 외부에서 접근 가능하도록 유지
        self.task1 = core.task1
        self.task2 = core.task2
        self.health1 = core.health1
        self.health2 = core.health2

        return result
