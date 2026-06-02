"""매치 감사 도구 — 의심 매치 재실행 및 결과 비교

결정론적 매치(F-2) 덕에 동일 (BT pair, seed)는 동일 결과를 만든다.
이의제기/감사 시 원본 매치의 시드를 그대로 사용해 재실행 → 결과 일치 여부 검증.

불일치 사유 (가능한 원인):
- 코드 변경 (시뮬레이터, 판정 로직)
- BT 파일 변경 (참가자 파일 교체 — 부정행위 의심)
- 환경 변경 (JSBSim 버전 등)
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List


@dataclass(frozen=True)
class MatchSnapshot:
    """비교 가능한 매치 결과의 핵심 필드만 추출한 스냅샷."""
    winner: Optional[str]            # "tree1" | "tree2" | "draw" | None
    total_steps: int
    tree1_health: float
    tree2_health: float
    tree1_damage_dealt: float
    tree2_damage_dealt: float
    victory_condition: Optional[str]

    @staticmethod
    def from_result(r) -> "MatchSnapshot":
        return MatchSnapshot(
            winner=getattr(r, "winner", None),
            total_steps=int(getattr(r, "total_steps", 0)),
            tree1_health=round(float(getattr(r, "tree1_health", 0.0)), 6),
            tree2_health=round(float(getattr(r, "tree2_health", 0.0)), 6),
            tree1_damage_dealt=round(float(getattr(r, "tree1_damage_dealt", 0.0)), 6),
            tree2_damage_dealt=round(float(getattr(r, "tree2_damage_dealt", 0.0)), 6),
            victory_condition=getattr(r, "victory_condition", None),
        )

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "MatchSnapshot":
        """저장된 game_result dict (MatchResult.to_dict 형태)에서 스냅샷 복원."""
        return MatchSnapshot(
            winner=d.get("winner"),
            total_steps=int(d.get("total_steps", 0)),
            tree1_health=round(float(d.get("tree1_health", 0.0)), 6),
            tree2_health=round(float(d.get("tree2_health", 0.0)), 6),
            tree1_damage_dealt=round(float(d.get("tree1_damage_dealt", 0.0)), 6),
            tree2_damage_dealt=round(float(d.get("tree2_damage_dealt", 0.0)), 6),
            victory_condition=d.get("victory_condition"),
        )


@dataclass(frozen=True)
class AuditReport:
    """감사 결과"""
    match_id: str
    matched: bool
    original: MatchSnapshot
    rerun: MatchSnapshot
    diffs: List[str]   # 불일치 필드명 리스트

    def summary(self) -> str:
        if self.matched:
            return f"[OK] {self.match_id} — 원본과 재실행 결과 완전 일치 ({self.original.winner})"
        return (
            f"[MISMATCH] {self.match_id} — 불일치 필드: {self.diffs}\n"
            f"  original: {asdict(self.original)}\n"
            f"  rerun:    {asdict(self.rerun)}"
        )


def compare_snapshots(original: MatchSnapshot, rerun: MatchSnapshot) -> List[str]:
    """두 스냅샷 비교 → 불일치 필드명 리스트 반환 (빈 리스트 = 완전 일치)"""
    diffs = []
    for field in ("winner", "total_steps", "tree1_health", "tree2_health",
                  "tree1_damage_dealt", "tree2_damage_dealt", "victory_condition"):
        if getattr(original, field) != getattr(rerun, field):
            diffs.append(field)
    return diffs


def audit_match(
    match_id: str,
    tree1_file: str,
    tree2_file: str,
    seed: int,
    original_snapshot: MatchSnapshot,
    *,
    config_name: str = "1v1/NoWeapon/bt_vs_bt",
    max_steps: int = 2000,
    wall_clock_timeout_sec: Optional[float] = 60.0,
) -> AuditReport:
    """매치 재실행 후 원본 스냅샷과 비교.

    실제 매치 실행이 무거우므로 운영자가 명시적으로 호출하는 용도.
    """
    from src.match.runner import BehaviorTreeMatch

    m = BehaviorTreeMatch(
        tree1_file=tree1_file,
        tree2_file=tree2_file,
        config_name=config_name,
        max_steps=max_steps,
        seed=seed,
        wall_clock_timeout_sec=wall_clock_timeout_sec,
    )
    rerun_result = m.run(verbose=False)
    rerun = MatchSnapshot.from_result(rerun_result)
    diffs = compare_snapshots(original_snapshot, rerun)
    return AuditReport(
        match_id=match_id,
        matched=(len(diffs) == 0),
        original=original_snapshot,
        rerun=rerun,
        diffs=diffs,
    )
