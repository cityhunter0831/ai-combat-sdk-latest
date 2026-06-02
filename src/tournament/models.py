import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, TYPE_CHECKING
from enum import Enum
from datetime import datetime, timezone, timedelta

if TYPE_CHECKING:
    from src.match.result import MatchResult as GameResult

# 한국 시간대 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)

class MatchPhase(Enum):
    QUALIFICATION = "qualification"  # 예선 (리그)
    QUARTERFINALS = "quarterfinals"  # 8강
    SEMIFINALS = "semifinals"        # 4강
    THIRD_PLACE = "third_place"      # 3·4위전
    FINALS = "finals"                # 결승
    TEST = "test"                    # 테스트

class MatchStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"

@dataclass
class Team:
    id: str
    name: str
    submission_path: str  # YAML 파일 경로
    wins: int = 0
    losses: int = 0
    draws: int = 0
    total_hp_remaining: float = 0.0  # 완료된 매치의 잔여 HP 합산
    
    @property
    def total_matches(self) -> int:
        return self.wins + self.losses + self.draws
    
    @property
    def win_rate(self) -> float:
        if self.total_matches == 0:
            return 0.0
        return self.wins / self.total_matches

    @property
    def avg_hp_remaining(self) -> float:
        if self.total_matches == 0:
            return 0.0
        return self.total_hp_remaining / self.total_matches

@dataclass
class MatchResult:
    """토너먼트 매치 결과 (게임 결과 + 메타데이터)"""
    match_id: str
    winner_id: Optional[str]  # None이면 무승부
    duration: float
    replay_path: str
    log_path: str
    scores: Dict[str, float]  # 팀별 점수/데미지 등
    game_result: Optional[Dict] = None # 원본 게임 결과 데이터

    @staticmethod
    def from_game_result(match_id: str, game_result: 'GameResult', team1_id: str, team2_id: str) -> 'MatchResult':
        """게임 결과 객체로부터 토너먼트 결과 객체 생성"""
        winner_id = None
        if game_result.winner == "tree1":
            winner_id = team1_id
        elif game_result.winner == "tree2":
            winner_id = team2_id
        elif game_result.winner != "draw":
            logger.warning(
                f"[{match_id}] 예상치 못한 winner 값: '{game_result.winner}'. "
                "무승부로 처리합니다. (예상값: 'tree1', 'tree2', 'draw')"
            )
            
        scores = {
            team1_id: game_result.tree1_reward,
            team2_id: game_result.tree2_reward,
            f"{team1_id}_hp": getattr(game_result, 'tree1_health', 100.0),
            f"{team2_id}_hp": getattr(game_result, 'tree2_health', 100.0),
            "victory_condition": getattr(game_result, 'victory_condition', 'unknown'),
        }
        
        return MatchResult(
            match_id=match_id,
            winner_id=winner_id,
            duration=game_result.duration_seconds,
            replay_path=game_result.replay_file or "",
            log_path="", # 로그 경로는 별도 관리 필요 시 추가
            scores=scores,
            game_result=game_result.to_dict()
        )

@dataclass
class Match:
    id: str
    team1_id: str
    team2_id: str
    phase: MatchPhase
    status: MatchStatus = MatchStatus.PENDING
    result: Optional[MatchResult] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(KST))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def __str__(self):
        return f"[{self.phase.value}] {self.team1_id} vs {self.team2_id} ({self.status.value})"
