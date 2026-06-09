"""
Alpha1 Custom Action Nodes

Callsign: Alpha1
Core: Proportional Navigation + Energy Management + State Memory
       + Adaptive Counter-Strategy (opponent-state-aware)
"""

import csv
import logging
import time
from pathlib import Path

import py_trees

logger = logging.getLogger(__name__)


class BaseAction(py_trees.behaviour.Behaviour):
    """Custom action base class"""

    def __init__(self, name: str):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(key="observation", access=py_trees.common.Access.READ)
        self.blackboard.register_key(key="action", access=py_trees.common.Access.WRITE)

    def set_action(self, delta_altitude_idx: int, delta_heading_idx: int, delta_velocity_idx: int):
        self.blackboard.action = [delta_altitude_idx, delta_heading_idx, delta_velocity_idx]


def _heading_from_tau(tau_deg: float, gain: float = 1.0) -> int:
    """Convert tau angle (degrees) to heading index [0-8] with proportional gain.

    tau_deg: target angle in degrees (-180 to 180)
    gain: proportional gain multiplier
    Returns heading index: 0=hard left(-90) ... 4=straight ... 8=hard right(+90)
    """
    cmd = tau_deg * gain
    idx = int(round(cmd / 22.5)) + 4
    return max(0, min(8, idx))


def _heading_pd(tau_deg: float, tau_rate: float,
                kp: float = 0.8, kd: float = 0.3) -> int:
    """PD controller on tau: proportional + derivative."""
    cmd = kp * tau_deg + kd * tau_rate
    idx = int(round(cmd / 22.5)) + 4
    return max(0, min(8, idx))


class PNPursuit(BaseAction):
    """PN-enhanced pursuit with energy management.

    Heading: PD controller on tau_deg (proportional + derivative).
    Altitude: Situation-aware (closing vs turning fight).
    Speed: ATA-aware — max speed when pointing at enemy, decel in turns.
    """

    def __init__(self, name: str = "PNPursuit",
                 kp: float = 1.5,
                 kd: float = 0.5,
                 close_range: float = 1500.0,
                 wez_max: float = 914.0,
                 wez_min: float = 152.0,
                 far_range: float = 4000.0):
        super().__init__(name)
        self.kp = kp
        self.kd = kd
        self.close_range = close_range
        self.wez_max = wez_max
        self.wez_min = wez_min
        self.far_range = far_range
        self.prev_tau = None

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau = obs.get("tau_deg", 0.0) * 180.0
            ata = obs.get("ata_deg", 0.5) * 180.0
            distance = obs.get("distance_ft", 10000.0)
            alt_gap = obs.get("alt_gap_ft", 0.0)
            altitude = obs.get("ego_altitude_ft", 5000.0)
            closure = obs.get("closure_rate_kts", 0.0)

            # --- 2-circle 발산 감지: 멀어지는 중 + tau 큼 → Lag Pursuit ---
            if closure < -20 and abs(tau) > 60:
                heading_idx = _heading_from_tau(tau, gain=0.4)
                self.prev_tau = tau
                self.set_action(2, heading_idx, 4)
                return py_trees.common.Status.SUCCESS

            # --- HEADING: PD on tau ---
            if self.prev_tau is not None:
                tau_rate = (tau - self.prev_tau) / 0.2
                kp = self.kp * (1.5 if distance < self.close_range else 1.0)
                heading_idx = _heading_pd(tau, tau_rate, kp, self.kd)
            else:
                heading_idx = _heading_from_tau(tau, self.kp)
            self.prev_tau = tau

            # --- ALTITUDE: situation-dependent ---
            if altitude < 800:
                delta_alt = 4  # Emergency climb (Hard Deck ~305m)
            elif ata < 30 and distance > self.wez_max:
                # Pointing at enemy but far: dive slightly for speed, close fast
                delta_alt = 1 if altitude > 2000 else 2
            elif ata > 90:
                # Turning fight (enemy behind): maintain altitude, don't bleed energy climbing
                delta_alt = 2
            elif alt_gap > 200:
                delta_alt = 2  # Have altitude advantage: maintain
            elif alt_gap > -100:
                delta_alt = 2  # Roughly same altitude: maintain (save energy for turns)
            else:
                delta_alt = 3  # Below enemy: climb to reduce disadvantage

            # --- SPEED: ATA-aware ---
            if distance < self.wez_min:
                delta_vel = 0  # Too close: hard brake
            elif distance < self.wez_max and ata < 20:
                delta_vel = 1  # In WEZ + on target: decelerate for stable aim
            elif ata < 30 and distance > self.close_range:
                delta_vel = 4  # Pointing at enemy + far: max speed to close!
            elif ata > 60 and distance < self.close_range:
                delta_vel = 1  # Turning fight + close: decelerate for tighter turn
            elif distance < self.close_range:
                delta_vel = 2  # Close: maintain
            elif distance < self.far_range:
                delta_vel = 3  # Mid: accelerate
            else:
                delta_vel = 4  # Far: max speed

            # Closure rate override: if enemy is opening, push harder
            if closure < -30 and distance > self.wez_max:
                delta_vel = max(delta_vel, 4)

            self.set_action(delta_alt, heading_idx, delta_vel)
            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"PNPursuit error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE


class PNAttack(BaseAction):
    """Close-range precision engagement for Gun WEZ.

    Tighter PD gains for precision aiming.
    Speed management for stable gun platform.
    WEZ awareness: ATA < 12deg, 152-914m.
    """

    def __init__(self, name: str = "PNAttack",
                 kp: float = 1.2,
                 kd: float = 0.5):
        super().__init__(name)
        self.kp = kp
        self.kd = kd
        self.prev_tau = None

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau = obs.get("tau_deg", 0.0) * 180.0
            ata = obs.get("ata_deg", 0.5) * 180.0
            distance = obs.get("distance_ft", 1000.0)
            alt_gap = obs.get("alt_gap_ft", 0.0)
            closure = obs.get("closure_rate_kts", 0.0)

            # --- HEADING: tight PD ---
            if self.prev_tau is not None:
                tau_rate = (tau - self.prev_tau) / 0.2
                heading_idx = _heading_pd(tau, tau_rate, self.kp, self.kd)
            else:
                heading_idx = _heading_from_tau(tau, self.kp)
            self.prev_tau = tau

            # --- ALTITUDE: minimal, keep stable ---
            if alt_gap > 200:
                delta_alt = 2  # We're above: maintain
            elif alt_gap > -100:
                delta_alt = 3  # Slightly below: climb gently
            else:
                delta_alt = 2  # Don't chase vertically during attack

            # --- SPEED: decelerate for stable gun platform ---
            if distance < 152:
                delta_vel = 0  # Too close, hard brake
            elif distance < 400:
                # Very close: adjust based on closure
                delta_vel = 0 if closure > 30 else 1
            elif distance < 700:
                delta_vel = 1  # Optimal WEZ range: decelerate
            elif distance < 914:
                delta_vel = 2  # Edge of WEZ: maintain
            else:
                delta_vel = 3  # Just outside WEZ: close in

            self.set_action(delta_alt, heading_idx, delta_vel)
            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"PNAttack error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE


class EnergyRecovery(BaseAction):
    """Energy recovery maneuver when energy state is low.

    Trades altitude for speed or maintains level flight
    while tracking enemy with relaxed heading control.
    """

    def __init__(self, name: str = "EnergyRecovery",
                 min_velocity: float = 200.0,
                 critical_velocity: float = 150.0):
        super().__init__(name)
        self.min_velocity = min_velocity
        self.critical_velocity = critical_velocity

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau = obs.get("tau_deg", 0.0) * 180.0
            velocity = obs.get("ego_vc_kts", 200.0)
            altitude = obs.get("ego_altitude_ft", 5000.0)

            # Heading: relaxed tracking (save energy, don't hard turn)
            heading_idx = _heading_from_tau(tau, 0.5)

            # Altitude/Speed: trade altitude for speed if needed
            if velocity < self.critical_velocity:
                delta_alt = 1  # Dive to gain speed
                delta_vel = 4  # Max accelerate
            elif velocity < self.min_velocity:
                delta_alt = 1 if altitude > 1500 else 2
                delta_vel = 4  # Accelerate
            else:
                delta_alt = 2  # Maintain altitude
                delta_vel = 3  # Accelerate

            # Hard Deck safety
            if altitude < 800:
                delta_alt = 4
                delta_vel = 3

            self.set_action(delta_alt, heading_idx, delta_vel)
            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"EnergyRecovery error: {e}")
            self.set_action(2, 4, 3)
            return py_trees.common.Status.FAILURE


# ============================================================
# StepLogger — Per-step obs recorder for analysis
# ============================================================

_OBS_KEYS = [
    'distance_ft', 'ego_altitude_ft', 'ego_vc_kts', 'alt_gap_ft',
    'ata_deg', 'aa_deg', 'hca_deg', 'tau_deg',
    'relative_bearing_deg', 'side_flag',
    'closure_rate_kts', 'turn_rate_degs', 'in_39_line', 'overshoot_risk',
    'tc_type', 'energy_advantage', 'energy_diff_ft',
    'alt_advantage', 'spd_advantage',
]


class StepLogger(BaseAction):
    """Records per-step observation dict to a CSV file.

    Always returns FAILURE so the parent Selector falls through
    to the actual combat logic. Place as the FIRST child of the
    root Selector to capture every BT tick.

    Usage in YAML:
        - type: Action
          name: StepLogger
          params:
            log_dir: logs/alpha1

    Remove from YAML for competition — no overhead when absent.
    """

    def __init__(self, name: str = "StepLogger", log_dir: str = "logs/alpha1"):
        super().__init__(name)
        self._log_dir = Path(log_dir)
        self._log_file = None
        self._writer = None
        self._step = 0

    def _ensure_open(self):
        if self._writer is not None:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        fname = self._log_dir / f"steps_{int(time.time())}.csv"
        self._log_file = open(fname, 'w', newline='', encoding='utf-8')
        self._writer = csv.DictWriter(
            self._log_file,
            fieldnames=['step'] + _OBS_KEYS,
            extrasaction='ignore',
        )
        self._writer.writeheader()

    def update(self) -> py_trees.common.Status:
        try:
            self._ensure_open()
            obs = self.blackboard.observation
            row = {k: obs.get(k, '') for k in _OBS_KEYS}
            row['step'] = self._step
            self._step += 1
            self._writer.writerow(row)
            self._log_file.flush()
        except Exception as e:
            logger.debug(f"StepLogger error: {e}")

        # Write neutral action (overwritten by next Selector child)
        self.set_action(2, 4, 2)
        return py_trees.common.Status.FAILURE  # always fail → Selector continues

    def terminate(self, new_status):
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
            self._writer = None


# ============================================================
# AdaptiveAction — Opponent-state-aware counter-strategy
# ============================================================

class AdaptiveAction(BaseAction):
    """Reactive counter-strategy action.

    Reads 19 observation features at each step, classifies
    the current tactical situation, and selects the optimal
    (delta_alt, delta_heading, delta_vel) response.

    Situation hierarchy:
      1. OFFENSIVE  — we're at opp's 6 o'clock (aa < 60°, ata < 60°)
                       → LeadPursuit-style: cut corners, close aggressively
      2. DEFENSIVE  — opp is at our 6 o'clock (aa > 140°)
                       → BreakTurn-style: hard evasive turn
      3. OVERSHOOT  — about to pass the opponent
                       → HighYoYo-style: climb + lag to reset
      4. 1-CIRCLE   — close range, same-direction turn (HCA < 90°)
                       → tight turn, decelerate for turn rate
      5. 2-CIRCLE   — close range, opposing turns (HCA > 90°)
                       → energy preservation, wider arc
      6. DEFAULT    — lag pursuit (energy-efficient, set up WEZ)
    """

    def __init__(self, name: str = "AdaptiveAction"):
        super().__init__(name)
        self._prev_tau = None

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            aa       = obs.get('aa_deg', 0.5) * 180.0    # 0=opp rear(offense), 180=opp front(defense)
            ata      = obs.get('ata_deg', 0.5) * 180.0  # 0=pointing at opp
            tau      = obs.get('tau_deg', 0.0) * 180.0  # heading correction to opp
            distance = obs.get('distance_ft', 5000.0)
            tc_type  = obs.get('tc_type', '2-circle')
            overshoot = obs.get('overshoot_risk', False)
            side_flag = int(obs.get('side_flag', 0))
            alt_gap   = obs.get('alt_gap_ft', 0.0)       # positive = opp above
            ego_alt   = obs.get('ego_altitude_ft', 5000.0)
            closure   = obs.get('closure_rate_kts', 0.0)
            energy_diff = obs.get('energy_diff_ft', 0.0)

            # Derivative for PD heading control
            if self._prev_tau is not None:
                tau_rate = (tau - self._prev_tau) / 0.2
            else:
                tau_rate = 0.0
            self._prev_tau = tau

            # ── Situation Classification ──

            if aa < 60 and ata < 70:
                # OFFENSIVE: we have rear-aspect position
                da, dh, dv = self._offensive(tau, tau_rate, alt_gap, distance, ata)

            elif aa > 140:
                # DEFENSIVE: opponent has our 6 o'clock
                da, dh, dv = self._defensive(side_flag, ego_alt)

            elif overshoot:
                # OVERSHOOT RISK: too fast, about to pass opponent
                da, dh, dv = self._high_yoyo(tau, side_flag)

            elif distance < 3000:
                if tc_type == '1-circle':
                    # 1-circle: tight turn to cut inside
                    da, dh, dv = self._one_circle(tau, tau_rate)
                else:
                    # 2-circle: energy fight, wider arc
                    da, dh, dv = self._two_circle(tau, energy_diff, alt_gap)

            else:
                # DEFAULT: lag pursuit — energy-efficient, set up WEZ
                da, dh, dv = self._lag_pursuit(tau, tau_rate, alt_gap, distance, closure, ego_alt)

            self.set_action(da, dh, dv)
            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"AdaptiveAction error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE

    # ── Sub-maneuver implementations ──

    def _offensive(self, tau, tau_rate, alt_gap, distance, ata):
        """We have rear-aspect advantage: lead pursuit to cut corners."""
        # Lead heading (gain > 1 → head toward where opp will be)
        kp = 1.3 if distance > 2000 else 1.1
        dh = _heading_pd(tau, tau_rate, kp=kp, kd=0.2)

        # Altitude: climb toward opp if they're above, maintain if we're above
        if alt_gap > 150:
            da = 3
        elif alt_gap < -150:
            da = 2
        else:
            da = 2

        # Speed: moderate to avoid overshoot
        if ata < 20 and distance < 1500:
            dv = 2   # about to be in WEZ: stable platform
        elif distance > 3000:
            dv = 4   # far: close fast
        else:
            dv = 3

        return da, dh, dv

    def _defensive(self, side_flag, ego_alt):
        """Opponent has rear-aspect: hard break turn."""
        # Hard turn OPPOSITE to opponent's side
        if side_flag == 1:      # opp on our right → hard left
            dh = 0
        elif side_flag == -1:   # opp on our left → hard right
            dh = 8
        else:
            dh = 0  # default hard left

        da = 1 if ego_alt > 1000 else 2  # slight descent for speed
        dv = 4  # max speed

        return da, dh, dv

    def _high_yoyo(self, tau, side_flag):
        """Overshoot risk: High Yo-Yo — climb and lag to reset."""
        dh = _heading_from_tau(tau, gain=0.5)  # lag behind opp
        da = 4   # climb
        dv = 1   # decelerate (tighter turn, overshoot prevention)
        return da, dh, dv

    def _one_circle(self, tau, tau_rate):
        """1-circle turn fight: tight inside turn."""
        dh = _heading_pd(tau, tau_rate, kp=1.5, kd=0.3)
        da = 2   # maintain altitude for max turn rate
        dv = 1   # decelerate → tighter turn radius
        return da, dh, dv

    def _two_circle(self, tau, energy_diff, alt_gap):
        """2-circle turn fight: energy fight, wider arc."""
        dh = _heading_from_tau(tau, gain=0.9)  # softer turn

        # Climb if we have energy advantage (altitude trade-off)
        if energy_diff > 500:
            da = 3
        elif alt_gap < -200:
            da = 2  # we're above: maintain
        else:
            da = 2

        dv = 3  # maintain speed (energy fight)
        return da, dh, dv

    def _lag_pursuit(self, tau, tau_rate, alt_gap, distance, closure, ego_alt):
        """Default lag pursuit: energy-efficient, follow from behind."""
        dh = _heading_pd(tau, tau_rate, kp=0.8, kd=0.3)

        # Altitude management
        if ego_alt < 800:
            da = 4  # emergency climb
        elif alt_gap > 200:
            da = 3  # climb to match opp
        elif alt_gap < -200:
            da = 2  # maintain our altitude advantage
        else:
            da = 2

        # Speed: close when far, moderate when near
        if distance > 4000:
            dv = 4
        elif distance > 2000:
            dv = 3
        elif closure < -20:  # distance opening
            dv = 4
        else:
            dv = 2

        return da, dh, dv


# ============================================================
# CityHunter 전용 신규 액션 노드
# NODE_REFERENCE.md UE4 BT 인사이트 기반 구현
# ============================================================

class OvershootAvoidance(BaseAction):
    """오버슈트 위험 시 자동 Lag/HighYoYo 전환.

    선회율 < 3deg/s  → HighYoYo (상승 + 감속 + Lag)
    빠른 접근+근거리 → 급감속 + Lag
    일반             → 상승 + 감속
    """

    def __init__(self, name: str = "OvershootAvoidance"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau       = obs.get("tau_deg", 0.0) * 180.0
            turn_rate = obs.get("turn_rate_degs", 10.0)
            closure   = obs.get("closure_rate_kts", 0.0)
            distance  = obs.get("distance_ft", 3000.0)

            lag_heading = _heading_from_tau(tau, gain=0.5)

            if turn_rate < 3.0:
                # HighYoYo: climb hard + lag + decel
                self.set_action(4, lag_heading, 1)
            elif closure > 50 and distance < 914:
                # Fast closing + very close: hard brake + lag
                self.set_action(2, lag_heading, 0)
            else:
                # General: climb + decel
                self.set_action(3, lag_heading, 1)

            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"OvershootAvoidance error: {e}")
            self.set_action(3, 4, 1)
            return py_trees.common.Status.FAILURE


class TCFight(BaseAction):
    """선회 유형(1/2-circle) 기반 전술 자동 분기.

    1-circle (HCA < 90) → 급선회 + 감속 (선회반경 최소화)
    2-circle (HCA > 90) → 에너지 유지 + 넓은 호 (에너지 전투)
    """

    def __init__(self, name: str = "TCFight"):
        super().__init__(name)
        self.prev_tau = None

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau     = obs.get("tau_deg", 0.0) * 180.0
            tc_type = obs.get("tc_type", "2-circle")

            tau_rate = 0.0
            if self.prev_tau is not None:
                tau_rate = (tau - self.prev_tau) / 0.2
            self.prev_tau = tau

            if tc_type == "1-circle":
                dh = _heading_pd(tau, tau_rate, kp=1.5, kd=0.3)
                self.set_action(2, dh, 1)  # maintain alt + decel
            else:
                dh = _heading_from_tau(tau, gain=0.9)
                self.set_action(3, dh, 3)  # slight climb + maintain speed

            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"TCFight error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE


class EnergyFight(BaseAction):
    """에너지 상태 기반 최적 전술 자동 선택.

    고도 우세 → 하강 공격 (위치 에너지 → 속도로 전환)
    속도 우세 → 가속 추격
    에너지 열세 → 상승 회복
    """

    def __init__(self, name: str = "EnergyFight"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation

            tau           = obs.get("tau_deg", 0.0) * 180.0
            alt_advantage = obs.get("alt_advantage", False)
            spd_advantage = obs.get("spd_advantage", False)

            dh = _heading_from_tau(tau, gain=1.0)

            if alt_advantage:
                self.set_action(1, dh, 4)  # descend + max speed
            elif spd_advantage:
                self.set_action(2, dh, 4)  # maintain alt + max speed
            else:
                self.set_action(3, dh, 3)  # climb + accelerate

            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"EnergyFight error: {e}")
            self.set_action(2, 4, 3)
            return py_trees.common.Status.FAILURE


# ============================================================
# CounterattackOnOvershoot
# 근거: 한국어 시뮬레이션 교범 §방어BFM §오버슈트/§선회 중
#       No Guts No Glory §방어원칙17
#       Art of the Kill §방어BFM
# ============================================================

class CounterattackOnOvershoot(BaseAction):
    """수세 중 적기 오버슈트 감지 → 즉시 역공격.

    교범: 시저스에서 공격기가 앞으로 밀려나는 순간이 반전 타이밍.
    overshoot_risk=True AND closure_rate<-20 → 적기 통과 중 → LeadPursuit 방향+최대가속.
    """

    def __init__(self, name: str = "CounterattackOnOvershoot"):
        super().__init__(name)
        self.prev_tau = None

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            tau = obs.get("tau_deg", 0.0) * 180.0
            closure_rate = obs.get("closure_rate_kts", 0.0)
            overshoot_risk = obs.get("overshoot_risk", False)

            tau_rate = 0.0
            if self.prev_tau is not None:
                tau_rate = (tau - self.prev_tau) / 0.2
            self.prev_tau = tau

            if overshoot_risk and closure_rate < -20:
                # 적기 오버슛 통과 중 → PD 제어로 공격 방향 + 최대가속
                dh = _heading_pd(tau, tau_rate, kp=1.5, kd=0.3)
                self.set_action(2, dh, 4)
            else:
                # 일반 추적 유지
                dh = _heading_from_tau(tau, gain=1.0)
                self.set_action(2, dh, 3)

            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"CounterattackOnOvershoot error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE


# ============================================================
# DisengageAfterCross
# 근거: 한국어 시뮬레이션 교범 §방어BFM §이탈
# ============================================================

class DisengageAfterCross(BaseAction):
    """교차 후 직선 가속 이탈.

    교차 전(ata_deg < 150°): 기수 유지+가속으로 교차 진행
    교차 후(ata_deg ≥ 150°): 직진+최대가속으로 이탈
    근거: 한국어 교범 §방어BFM §이탈
    """

    def __init__(self, name: str = "DisengageAfterCross"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            ata_deg = obs.get("ata_deg", 0.0) * 180.0
            tau = obs.get("tau_deg", 0.0) * 180.0

            if ata_deg > 150:
                self.set_action(2, 4, 4)  # 교차 완료: 직진+최대가속 이탈
            else:
                dh = _heading_from_tau(tau, gain=1.0)
                self.set_action(2, dh, 4)  # 교차 전: 기수 유지+가속

            return py_trees.common.Status.SUCCESS

        except Exception as e:
            logger.warning(f"DisengageAfterCross error: {e}")
            self.set_action(2, 4, 2)
            return py_trees.common.Status.FAILURE
