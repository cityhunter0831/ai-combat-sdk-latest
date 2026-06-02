"""대진표 생성기

- generate_round_robin: 예선 리그
- generate_single_elimination: legacy (순서대로 매칭, 단순)
- generate_seeded_bracket: 표준 시딩 (1 vs N, 2 vs N-1 ...) + bye 자동 처리
- generate_next_round: 이전 라운드 완료 매치에서 승자를 뽑아 다음 라운드 매치 생성
"""

import logging
from typing import List, Dict, Optional
from itertools import combinations
from .models import Team, Match, MatchPhase, MatchStatus

logger = logging.getLogger(__name__)


class BracketGenerator:
    """대진표 생성기"""

    @staticmethod
    def generate_round_robin(teams: List[Team], phase: MatchPhase = MatchPhase.QUALIFICATION) -> List[Match]:
        """리그전 (Round Robin) 대진표 생성 — 모든 팀이 서로 1회씩 대전"""
        matches = []
        team_ids = [t.id for t in teams]
        pairs = list(combinations(team_ids, 2))

        for i, (t1, t2) in enumerate(pairs):
            match_id = f"{phase.value}_{t1}_vs_{t2}_{i+1}"
            matches.append(Match(
                id=match_id,
                team1_id=t1,
                team2_id=t2,
                phase=phase
            ))

        return matches

    @staticmethod
    def generate_single_elimination(teams: List[Team], phase: MatchPhase = MatchPhase.SEMIFINALS) -> List[Match]:
        """싱글 엘리미네이션 (legacy, 단순 순서 매칭).

        호환성 유지를 위해 보존. 신규 코드는 generate_seeded_bracket 사용을 권장.
        """
        matches = []
        n_teams = len(teams)

        if n_teams < 2:
            return []

        if n_teams % 2 != 0:
            logger.warning(
                f"홀수 팀({n_teams}팀)으로 싱글 엘리미네이션 대진표를 생성합니다. "
                f"마지막 팀 '{teams[-1].id}'은(는) 부전승 처리 없이 제외됩니다."
            )

        for i in range(0, n_teams, 2):
            if i + 1 < n_teams:
                match_id = f"{phase.value}_{teams[i].id}_vs_{teams[i+1].id}_{i//2 + 1}"
                matches.append(Match(
                    id=match_id,
                    team1_id=teams[i].id,
                    team2_id=teams[i+1].id,
                    phase=phase
                ))

        return matches

    @staticmethod
    def generate_seeded_bracket(
        seeded_teams: List[Team],
        phase: MatchPhase,
    ) -> List[Match]:
        """표준 시딩 토너먼트 1라운드 매치 생성.

        시딩 규칙:
          - seeded_teams[0]는 1번 시드 (예선 1위), [N-1]은 N번 시드
          - 매치는 (1 vs N), (2 vs N-1), ... 순서로 짝지음 → 상위 시드가 약체와 만남
          - 홀수 팀이면 1번 시드부터 차례로 bye 처리 (다음 라운드 직행)
            bye 매치는 team2_id=None + status=COMPLETED + winner=team1로 생성

        Args:
            seeded_teams: 예선 결과 순위로 정렬된 팀 (상위가 [0])
            phase: 이번 라운드 phase (QUARTERFINALS / SEMIFINALS / FINALS)

        Returns:
            Match 리스트 (bye 매치 포함)
        """
        from .models import MatchResult as TMatchResult

        matches: List[Match] = []
        n = len(seeded_teams)
        if n < 2:
            return []

        # 다음 2의 제곱수로 패딩 — 홀수/비표준 인원 시 bye 슬롯 생성.
        # 슬롯 0..N-1에 시드 0..N-1을 배치, 슬롯 N..bracket_size-1은 빈 슬롯.
        # 빈 슬롯과 페어가 된 팀은 1라운드 bye(자동 진출).
        bracket_size = 1
        while bracket_size < n:
            bracket_size *= 2
        empty_slot_indices = set(range(n, bracket_size))

        # 표준 시딩 페어: (slot 0, slot N-1), (slot 1, slot N-2), ...
        # 상위 시드(0)는 가장 약한 시드(N-1)와 만남 → 빈 슬롯이 있으면 상위 시드가 bye를 받음.
        for i in range(bracket_size // 2):
            slot_a = i
            slot_b = bracket_size - 1 - i

            a_empty = slot_a in empty_slot_indices
            b_empty = slot_b in empty_slot_indices

            if a_empty and b_empty:
                # 양쪽 다 빈 슬롯 — 매치 자체가 없음 (다음 라운드도 빈 슬롯)
                continue

            if a_empty:
                # 슬롯 a가 비었으면 슬롯 b의 팀이 자동 진출
                team = seeded_teams[slot_b]
                _add_bye_match(matches, team, phase, slot=i + 1)
                continue
            if b_empty:
                team = seeded_teams[slot_a]
                _add_bye_match(matches, team, phase, slot=i + 1)
                continue

            # 정상 매치
            team_a = seeded_teams[slot_a]
            team_b = seeded_teams[slot_b]
            match_id = f"{phase.value}_{team_a.id}_vs_{team_b.id}_{i+1}"
            matches.append(Match(
                id=match_id,
                team1_id=team_a.id,
                team2_id=team_b.id,
                phase=phase,
            ))

        return matches

    @staticmethod
    def generate_next_round(
        prev_round_matches: List[Match],
        next_phase: MatchPhase,
    ) -> List[Match]:
        """이전 라운드 완료 매치에서 승자를 뽑아 다음 라운드 매치 생성.

        - 이전 라운드는 매치 ID 순서(=시드 순서)대로 결과 처리
        - 승자 페어: (winner[0], winner[1]), (winner[2], winner[3]) ...
        - 무승부 매치가 있으면 ValueError (결승 토너먼트에선 결판이 필요)

        Args:
            prev_round_matches: 직전 라운드 매치 리스트 (모두 COMPLETED 상태여야 함)
            next_phase: 다음 라운드 phase

        Returns:
            다음 라운드 Match 리스트
        """
        # 매치 ID로 정렬 (생성 순서 보존)
        ordered = sorted(prev_round_matches, key=lambda m: m.id)
        winner_ids: List[str] = []
        for m in ordered:
            if m.status != MatchStatus.COMPLETED:
                raise ValueError(f"이전 라운드 매치가 완료되지 않음: {m.id} ({m.status.value})")
            if m.result is None or m.result.winner_id is None:
                raise ValueError(f"결승 토너먼트에서 무승부 불허: {m.id}")
            winner_ids.append(m.result.winner_id)

        if len(winner_ids) < 2:
            return []
        if len(winner_ids) % 2 != 0:
            raise ValueError(f"승자 수가 홀수({len(winner_ids)}) — 라운드 짝수 매치가 아님")

        matches: List[Match] = []
        for i in range(0, len(winner_ids), 2):
            t1, t2 = winner_ids[i], winner_ids[i + 1]
            match_id = f"{next_phase.value}_{t1}_vs_{t2}_{i//2 + 1}"
            matches.append(Match(
                id=match_id,
                team1_id=t1,
                team2_id=t2,
                phase=next_phase,
            ))
        return matches


def _add_bye_match(matches: List[Match], team: Team, phase: MatchPhase, slot: int) -> None:
    """bye 매치 추가 — team1만 있고 즉시 COMPLETED, winner=team1."""
    from .models import MatchResult as TMatchResult
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    match_id = f"{phase.value}_bye_{team.id}_{slot}"
    m = Match(
        id=match_id,
        team1_id=team.id,
        team2_id="__BYE__",
        phase=phase,
        status=MatchStatus.COMPLETED,
        completed_at=datetime.now(KST),
    )
    m.result = TMatchResult(
        match_id=match_id,
        winner_id=team.id,
        duration=0.0,
        replay_path="",
        log_path="",
        scores={f"{team.id}_hp": 100.0, "victory_condition": "bye"},
    )
    matches.append(m)
