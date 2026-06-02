import logging
import yaml
import json
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .models import Team, Match, MatchPhase, MatchStatus, MatchResult
from .bracket import BracketGenerator
from .persistence import TournamentPersistence
from src.submission.runner import SubmissionRunner

# 한국 시간대 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)

class TournamentManager:
    """토너먼트 진행 관리자"""
    
    def __init__(self, workspace_root: str, data_dir: str = "tournament_data"):
        self.workspace_root = Path(workspace_root)
        self.persistence = TournamentPersistence(str(self.workspace_root / data_dir))
        self.runner = SubmissionRunner(str(self.workspace_root))
        
        # 설정 로드
        self.config = self._load_config()
        
        self.teams: Dict[str, Team] = {}
        self.matches: List[Match] = []
        self.results: Dict[str, MatchResult] = {}
        
        # 결과 저장 디렉토리 생성
        self.replays_dir = self.workspace_root / self.config.get('paths', {}).get('replay_dir', 'replays')
        self.replays_dir.mkdir(exist_ok=True)
        
        # 새로 생성된 리플레이 파일 목록
        self.new_replay_files: List[str] = []
        
        # 데이터 로드
        self._load_data()
    
    def _load_config(self) -> Dict:
        """토너먼트 설정 로드"""
        config_file = self.workspace_root / "config" / "tournament_config.yaml"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        else:
            # 기본 설정 반환
            return {
                'match': {
                    'config_name': '1v1/NoWeapon/bt_vs_bt',
                    'max_steps': 1500
                },
                'paths': {
                    'replay_dir': 'replays'
                }
            }
        
    def _load_data(self):
        """저장된 데이터 로드"""
        self.teams = self.persistence.load_teams()
        self.matches = self.persistence.load_matches()
        
        # results 딕셔너리 재구성
        for match in self.matches:
            if match.result:
                self.results[match.id] = match.result
                
        logger.info(f"데이터 로드 완료: 팀 {len(self.teams)}개, 매치 {len(self.matches)}개")

    def _save_data(self):
        """데이터 저장"""
        self.persistence.save_teams(self.teams)
        self.persistence.save_matches(self.matches)
        
    def register_team(self, team_id: str, name: str, submission_path: str) -> bool:
        """팀 등록"""
        if team_id in self.teams:
            logger.warning(f"이미 등록된 팀 ID입니다: {team_id}")
            return False
            
        path = Path(submission_path)
        if not path.is_absolute():
            path = (self.workspace_root / path).resolve()
        if not path.exists():
            logger.error(f"제출 파일을 찾을 수 없습니다: {submission_path}")
            return False
            
        self.teams[team_id] = Team(id=team_id, name=name, submission_path=str(path))
        self._save_data() # 저장
        logger.info(f"팀 등록 완료: {name} ({team_id})")
        return True
        
    def list_teams(self) -> List[Team]:
        """등록된 팀 목록 반환 (등록 순)"""
        return list(self.teams.values())

    def remove_team(self, team_id: str) -> bool:
        """팀 삭제
        
        완료된 매치가 있는 팀은 삭제할 수 없습니다.
        
        Returns:
            True이면 삭제 성공
        """
        if team_id not in self.teams:
            logger.error(f"등록되지 않은 팀입니다: {team_id}")
            return False
        
        involved = [
            m for m in self.matches
            if (m.team1_id == team_id or m.team2_id == team_id)
            and m.status == MatchStatus.COMPLETED
        ]
        if involved:
            logger.error(f"완료된 매치가 있어 삭제할 수 없습니다: {team_id} ({len(involved)}경기)")
            return False
        
        # 해당 팀의 pending/error 매치도 함께 제거
        self.matches = [
            m for m in self.matches
            if m.team1_id != team_id and m.team2_id != team_id
        ]
        del self.teams[team_id]
        self._save_data()
        logger.info(f"팀 삭제 완료: {team_id}")
        return True

    def reset_matches(self) -> int:
        """모든 매치 데이터를 초기화하고 팀 통계를 리셋합니다.
        
        팀 등록 정보는 유지됩니다.
        
        Returns:
            삭제된 매치 수
        """
        count = len(self.matches)
        self.matches = []
        self.results = {}
        
        # 팀 통계 초기화
        for team in self.teams.values():
            team.wins = 0
            team.draws = 0
            team.losses = 0
            team.total_hp_remaining = 0.0
        
        # new_replays.json 파일 삭제
        new_replays_file = self.workspace_root / "tournament_data" / "new_replays.json"
        if new_replays_file.exists():
            new_replays_file.unlink()
            logger.info("new_replays.json 파일 삭제 완료")
        
        self._save_data()
        logger.info(f"매치 데이터 초기화 완료: {count}개 삭제, 팀 통계 리셋")
        return count

    def add_missing_matches(self) -> int:
        """등록된 팀 중 아직 대전하지 않은 조합의 매치를 예선에 추가
        
        이미 매치(pending/running/completed/error)가 존재하는 조합은 건너뜁니다.
        
        Returns:
            새로 추가된 매치 수
        """
        team_list = list(self.teams.values())
        if len(team_list) < 2:
            logger.warning("팀이 부족하여 매치를 추가할 수 없습니다.")
            return 0

        # 기존 매치 조합 수집 (순서 무관)
        existing_pairs = set()
        for m in self.matches:
            existing_pairs.add(frozenset([m.team1_id, m.team2_id]))

        new_matches = []
        for i in range(len(team_list)):
            for j in range(i + 1, len(team_list)):
                pair = frozenset([team_list[i].id, team_list[j].id])
                if pair not in existing_pairs:
                    new_matches.extend(
                        BracketGenerator.generate_round_robin(
                            [team_list[i], team_list[j]], MatchPhase.QUALIFICATION
                        )
                    )
                    existing_pairs.add(pair)

        if not new_matches:
            logger.info("추가할 신규 매치 조합이 없습니다.")
            return 0

        self.matches.extend(new_matches)
        self._save_data()
        logger.info(f"신규 매치 {len(new_matches)}개 추가 완료")
        return len(new_matches)

    def create_qualification_round(self) -> int:
        """예선 리그 대진표 생성
        
        Returns:
            새로 생성된 매치 수 (0이면 생성 실패 또는 이미 존재)
        """
        team_list = list(self.teams.values())
        if len(team_list) < 2:
            logger.warning("팀이 부족하여 예선을 시작할 수 없습니다.")
            return 0
        
        # 기존 예선 매치가 있는지 확인 (중복 생성 방지)
        existing_qual = [m for m in self.matches if m.phase == MatchPhase.QUALIFICATION]
        if existing_qual:
            logger.warning(f"이미 예선 대진표가 존재합니다 ({len(existing_qual)}경기). 중복 생성을 건너뜁니다.")
            return 0
            
        new_matches = BracketGenerator.generate_round_robin(team_list, MatchPhase.QUALIFICATION)
        self.matches.extend(new_matches)
        self._save_data() # 저장
        logger.info(f"예선 대진표 생성 완료: 총 {len(new_matches)} 경기")
        return len(new_matches)
        
    def run_pending_matches(self):
        """대기 중인 매치 실행"""
        pending_matches = [m for m in self.matches if m.status == MatchStatus.PENDING]
        
        if not pending_matches:
            logger.info("대기 중인 경기가 없습니다.")
            print("  대기 중인 경기가 없습니다.")
            return
            
        total = len(pending_matches)
        logger.info(f"총 {total} 경기를 시작합니다.")
        
        # 새 리플레이 파일 목록 초기화
        self.new_replay_files = []
        
        try:
            for i, match in enumerate(pending_matches, 1):
                team1_name = self.teams[match.team1_id].name
                team2_name = self.teams[match.team2_id].name
                print(f"  [{i}/{total}] {team1_name} vs {team2_name} ...", end="", flush=True)
                
                import time
                match_start = time.time()
                self._run_single_match(match)
                match_elapsed = time.time() - match_start
                
                # 결과 표시
                if match.status == MatchStatus.COMPLETED and match.result:
                    if match.result.winner_id:
                        winner_name = self.teams[match.result.winner_id].name
                        print(f" {winner_name} 승 ({match_elapsed:.1f}s)")
                    else:
                        print(f" 무승부 ({match_elapsed:.1f}s)")
                elif match.status == MatchStatus.ERROR:
                    print(f" 오류 ({match_elapsed:.1f}s)")
                else:
                    print(f" ({match_elapsed:.1f}s)")
                
                self._save_data() # 매 경기 완료 후 1회 저장
        finally:
            self.runner.cleanup()
            # 새로 생성된 리플레이 파일 목록 저장
            self._save_new_replays_list()
            
    def _run_single_match(self, match: Match):
        """단일 매치 실행 및 결과 처리"""
        match.status = MatchStatus.RUNNING
        match.started_at = datetime.now(KST)
        logger.info(f"경기 시작: {match}")
        
        team1 = self.teams[match.team1_id]
        team2 = self.teams[match.team2_id]
        
        # ACMI 파일명에 날짜/시간 정보 포함
        timestamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
        replay_file = self.replays_dir / f"{timestamp}_{match.team1_id}_vs_{match.team2_id}.acmi"
        
        # SubmissionRunner를 통해 실행 준비 (검증 및 임시 경로 생성)
        agent1_path = self.runner.prepare_agent(team1.submission_path, team1.id)
        agent2_path = self.runner.prepare_agent(team2.submission_path, team2.id)
        
        if not agent1_path or not agent2_path:
            logger.error(f"에이전트 준비 실패: {match}")
            match.status = MatchStatus.ERROR
            match.completed_at = datetime.now(KST)
            return

        try:
            # 실제 매치 실행
            from src.match.runner import BehaviorTreeMatch
            from src.match.seeding import derive_seed

            game_match = BehaviorTreeMatch(
                tree1_file=agent1_path, # 준비된 경로 사용
                tree2_file=agent2_path,
                config_name=self.config.get('match', {}).get('config_name', '1v1/NoWeapon/bt_vs_bt'),
                max_steps=self.config.get('match', {}).get('max_steps', 2000),
                tree1_name=team1.name,
                tree2_name=team2.name,
                seed=derive_seed(match.id),
            )
            
            # 매치 실행 (verbose=False로 로그 최소화)
            game_result = game_match.run(replay_path=str(replay_file), verbose=False)
            
            # 결과 처리
            match.status = MatchStatus.COMPLETED
            match.completed_at = datetime.now(KST)
            
            # 게임 결과를 토너먼트 결과로 변환
            result = MatchResult.from_game_result(
                match_id=match.id,
                game_result=game_result,
                team1_id=team1.id,
                team2_id=team2.id
            )
            
            match.result = result
            self.results[match.id] = result
            
            self._update_team_stats(match, result)
            
            # 생성된 리플레이 파일 기록
            if replay_file.exists():
                self.new_replay_files.append(replay_file.name)
            
            if result.winner_id:
                winner_name = self.teams[result.winner_id].name
                logger.info(f"경기 종료: 승자 {winner_name} (Duration: {result.duration:.1f}s)")
            else:
                logger.info(f"경기 종료: 무승부 (Duration: {result.duration:.1f}s)")
                
        except Exception as e:
            logger.error(f"경기 실행 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
            match.status = MatchStatus.ERROR
            match.completed_at = datetime.now(KST) # 오류 발생 시각 기록
            
    def _update_team_stats(self, match: Match, result: MatchResult):
        """경기 결과에 따른 팀 통계 업데이트 (승/무/패, 잔여 HP 누적)."""
        t1 = self.teams[match.team1_id]
        t2 = self.teams[match.team2_id]

        if result.winner_id == t1.id:
            t1.wins += 1
            t2.losses += 1
        elif result.winner_id == t2.id:
            t2.wins += 1
            t1.losses += 1
        else:
            t1.draws += 1
            t2.draws += 1

        # 잔여 HP 누적 (평균 HP 산정용)
        scores = result.scores
        t1.total_hp_remaining += scores.get(f"{t1.id}_hp", 100.0)
        t2.total_hp_remaining += scores.get(f"{t2.id}_hp", 100.0)
    
    def _save_new_replays_list(self):
        """새로 생성된 리플레이 파일 목록을 JSON 파일로 저장"""
        if not self.new_replay_files:
            return
        
        new_replays_file = self.workspace_root / "tournament_data" / "new_replays.json"
        with open(new_replays_file, 'w', encoding='utf-8') as f:
            json.dump(self.new_replay_files, f, ensure_ascii=False, indent=2)
        
        logger.info(f"새 리플레이 파일 목록 저장: {len(self.new_replay_files)}개")
            
    def get_leaderboard(self) -> List[Team]:
        """순위 반환 (룰북 정의 우선순위)

        정렬 키 (상위가 먼저):
          1. 승점 (승 × 3 + 무 × 1)
          2. 평균 잔여 HP (총 잔여 HP / 매치 수) — 매치 수가 다른 팀 간 공정 비교

        주의: 직접 대결(head-to-head) 결과는 sort_key로는 결정 불가하므로
        2팀 동률 시 별도 `head_to_head_winner()`로 수동 적용한다.
        """
        def sort_key(t: Team):
            points = t.wins * 3 + t.draws * 1
            return (points, t.avg_hp_remaining)
        return sorted(self.teams.values(), key=sort_key, reverse=True)

    def head_to_head_winner(self, team_a_id: str, team_b_id: str) -> Optional[str]:
        """두 팀의 직접 대결 결과를 누적 승점으로 비교 → 우세 팀 ID 반환.

        2팀 동률 시 룰북상 2순위 tiebreaker로 사용 (평균 HP보다 위).
        승점 동률이면 None (무승부).
        """
        a_pts = 0
        b_pts = 0
        for m in self.matches:
            if m.status != MatchStatus.COMPLETED or m.result is None:
                continue
            pair = {m.team1_id, m.team2_id}
            if pair != {team_a_id, team_b_id}:
                continue
            w = m.result.winner_id
            if w == team_a_id:
                a_pts += 3
            elif w == team_b_id:
                b_pts += 3
            else:
                a_pts += 1
                b_pts += 1
        if a_pts > b_pts:
            return team_a_id
        if b_pts > a_pts:
            return team_b_id
        return None

