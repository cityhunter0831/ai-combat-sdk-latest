"""
CityHunter Custom Condition Nodes

구현된 신규 조건 노드 (NODE_REFERENCE.md UE4 BT 인사이트 기반):
  - IsOvershootRisk  : 오버슈트 위험 여부 (blackboard overshoot_risk)
  - ClosureRateAbove : 접근 속도 > 임계값
  - EnergyDiffAbove  : 에너지 차이 > 임계값 (양수=아군 우세)
"""

import py_trees


class BaseCondition(py_trees.behaviour.Behaviour):
    """커스텀 조건 노드 공통 베이스."""

    def __init__(self, name: str):
        super().__init__(name)
        self.blackboard = self.attach_blackboard_client()
        self.blackboard.register_key(
            key="observation", access=py_trees.common.Access.READ
        )


class IsOvershootRisk(BaseCondition):
    """오버슈트 위험 여부.

    blackboard observation['overshoot_risk'] (bool) 를 직접 읽는다.
    빠른 접근 + 근거리 + 낮은 ATA/선회율 조합 시 True.
    """

    def __init__(self, name: str = "IsOvershootRisk"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("overshoot_risk", False):
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class ClosureRateAbove(BaseCondition):
    """접근 속도 > 임계값.

    blackboard observation['closure_rate'] (양수=접근 중) 를 읽는다.
    params:
        threshold_kts (float): 임계 접근 속도 (기본값 97.2)
    """

    def __init__(self, name: str = "ClosureRateAbove", threshold_kts: float = 97.2):
        super().__init__(name)
        self.threshold_kts = threshold_kts

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            closure = obs.get("closure_rate_kts", 0.0)
            if closure > self.threshold_kts:
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class EnergyDiffAbove(BaseCondition):
    """에너지 차이 > 임계값 (아군 에너지 우세 확인).

    blackboard observation['energy_diff'] (ft, 양수=아군 우세) 를 읽는다.
    params:
        threshold_ft (float): 에너지 차이 임계값 (기본값 1640)
    """

    def __init__(self, name: str = "EnergyDiffAbove", threshold_ft: float = 1640.0):
        super().__init__(name)
        self.threshold_ft = threshold_ft

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            energy_diff = obs.get("energy_diff_ft", 0.0)
            if energy_diff > self.threshold_ft:
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class IsEnergyAdvantage(BaseCondition):
    """아군 에너지 우세 여부 (energy_diff > 0)."""

    def __init__(self, name: str = "IsEnergyAdvantage"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("energy_diff_ft", 0.0) > 0.0:
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class IsAltAdvantage(BaseCondition):
    """아군 고도 우세 여부 (alt_advantage == True)."""

    def __init__(self, name: str = "IsAltAdvantage"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("alt_advantage", False):
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class IsTwoCircle(BaseCondition):
    """선회 유형이 2-circle인지 확인 (tc_type == '2-circle')."""

    def __init__(self, name: str = "IsTwoCircle"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("tc_type", "2-circle") == "2-circle":
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class IsOneCircle(BaseCondition):
    """선회 유형이 1-circle인지 확인 (tc_type == '1-circle')."""

    def __init__(self, name: str = "IsOneCircle"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("tc_type", "2-circle") == "1-circle":
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class IsSpdAdvantage(BaseCondition):
    """아군 속도 우세 여부 (spd_advantage == True)."""

    def __init__(self, name: str = "IsSpdAdvantage"):
        super().__init__(name)

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            if obs.get("spd_advantage", False):
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE


class ClosureRateBelow(BaseCondition):
    """접근 속도 < 임계값 (거리가 벌어지는 중).

    params:
        threshold_kts (float): 임계값 (기본값 0.0 → 거리 증가 중)
    """

    def __init__(self, name: str = "ClosureRateBelow", threshold_kts: float = 0.0):
        super().__init__(name)
        self.threshold_kts = threshold_kts

    def update(self) -> py_trees.common.Status:
        try:
            obs = self.blackboard.observation
            closure = obs.get("closure_rate_kts", 0.0)
            if closure < self.threshold_kts:
                return py_trees.common.Status.SUCCESS
            return py_trees.common.Status.FAILURE
        except Exception:
            return py_trees.common.Status.FAILURE
