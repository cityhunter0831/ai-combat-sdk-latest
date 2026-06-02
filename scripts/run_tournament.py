"""
토너먼트 실행 스크립트

사용법:
    python scripts/run_tournament.py init
    python scripts/run_tournament.py register --team-id team1 --name "Team Alpha" --file submissions/team1.yaml
    python scripts/run_tournament.py teams list
    python scripts/run_tournament.py teams remove --team-id team1
    python scripts/run_tournament.py start --round qualification
    python scripts/run_tournament.py add-matches
    python scripts/run_tournament.py run
    python scripts/run_tournament.py leaderboard
"""

import sys
import os
import argparse
import yaml
import time
from pathlib import Path

# Windows cp949 환경에서 이모지 출력 오류 방지
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tournament.manager import TournamentManager

# 설정 로드
def load_config():
    """토너먼트 설정 로드"""
    config_file = PROJECT_ROOT / "config" / "tournament_config.yaml"
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    else:
        # 기본 설정 반환
        return {
            'default': {
                'data_dir': 'tournament_data',
                'max_steps': 2000,
                'config_name': '1v1/NoWeapon/bt_vs_bt',
                'verbose': False
            },
            'messages': {
                'init_success': '✅ 토너먼트 시스템이 초기화되었습니다. (데이터 경로: {data_dir})',
                'register_success': '✅ 팀 등록 완료: {name} ({team_id})',
                'register_fail': '❌ 팀 등록 실패 (로그 확인)',
                'qualification_created': '✅ 예선 라운드 대진표가 생성되었습니다. (신규 {count} 경기)',
                'qualification_exists': '⚠️  대진표를 생성하지 못했습니다. (팀 부족 또는 이미 존재)',
                'match_start': '🚀 매치 실행을 시작합니다...',
                'match_complete': '✅ 모든 대기 중인 매치가 처리되었습니다.'
            }
        }

def main():
    config = load_config()
    default_config = config.get('default', {})
    messages = config.get('messages', {})
    
    parser = argparse.ArgumentParser(description="AI Combat Tournament System")
    subparsers = parser.add_subparsers(dest="command", help="명령어")
    
    # init 명령어
    init_parser = subparsers.add_parser("init", help="토너먼트 초기화")
    init_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'), 
                            help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    
    # register 명령어
    reg_parser = subparsers.add_parser("register", help="팀 등록")
    reg_parser.add_argument("--team-id", required=True, help="팀 ID (영문/숫자)")
    reg_parser.add_argument("--name", required=True, help="팀 이름")
    reg_parser.add_argument("--file", required=True, help="에이전트 파일 경로")
    reg_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                            help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")

    # teams 명령어 (목록/삭제)
    teams_parser = subparsers.add_parser("teams", help="팀 관리 (list / remove)")
    teams_sub = teams_parser.add_subparsers(dest="teams_command", help="팀 관리 명령")

    teams_list_parser = teams_sub.add_parser("list", help="등록된 팀 목록 조회")
    teams_list_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                                   help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")

    teams_remove_parser = teams_sub.add_parser("remove", help="팀 삭제")
    teams_remove_parser.add_argument("--team-id", required=True, help="삭제할 팀 ID")
    teams_remove_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                                     help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    
    # reset-matches 명령어 (매치 초기화, 팀 유지)
    reset_parser = subparsers.add_parser("reset-matches", help="모든 매치 초기화 (팀 등록 유지)")
    reset_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                              help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    reset_parser.add_argument("--yes", action="store_true", help="확인 프롬프트 건너뜀")

    # add-matches 명령어 (신규 팀 추가 후 누락 매치 자동 생성)
    add_matches_parser = subparsers.add_parser("add-matches", help="미대전 조합 매치 자동 추가")
    add_matches_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                                    help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")

    # start 명령어 (대진표 생성)
    start_parser = subparsers.add_parser("start", help="라운드 시작 (대진표 생성)")
    start_parser.add_argument("--round", default="qualification", choices=["qualification"], help="라운드 종류")
    start_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                            help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    
    # run 명령어 (매치 실행)
    run_parser = subparsers.add_parser("run", help="대기 중인 매치 실행")
    run_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                          help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    
    # leaderboard 명령어
    lb_parser = subparsers.add_parser("leaderboard", help="리더보드 조회")
    lb_parser.add_argument("--data-dir", default=default_config.get('data_dir', 'data'),
                          help=f"데이터 저장 디렉토리 (기본값: {default_config.get('data_dir', 'data')})")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return

    # Manager 초기화
    data_dir = getattr(args, "data_dir", default_config.get('data_dir', 'data'))
    manager = TournamentManager(str(PROJECT_ROOT), data_dir=data_dir)
    
    if args.command == "init":
        print(messages.get('init_success', '✅ 토너먼트 시스템이 초기화되었습니다.').format(data_dir=manager.persistence.data_dir))
        
    elif args.command == "teams":
        if not args.teams_command:
            teams_parser.print_help()
        elif args.teams_command == "list":
            teams = manager.list_teams()
            if not teams:
                print("등록된 팀이 없습니다.")
            else:
                width = 72
                print(f"\n{'팀 ID':<15} {'팀 이름':<20} {'파일 경로'}")
                print("-" * width)
                for t in teams:
                    path_display = Path(t.submission_path).relative_to(PROJECT_ROOT) if Path(t.submission_path).is_relative_to(PROJECT_ROOT) else t.submission_path
                    print(f"{t.id:<15} {t.name:<20} {path_display}")
                print(f"\n총 {len(teams)}개 팀 등록됨")
        elif args.teams_command == "remove":
            if manager.remove_team(args.team_id):
                print(f"[OK] 팀 삭제 완료: {args.team_id}")
            else:
                print(f"[FAIL] 팀 삭제 실패: {args.team_id} (로그 확인)")
                sys.exit(1)

    elif args.command == "register":
        submission_path = str(Path(args.file).resolve())
        if manager.register_team(args.team_id, args.name, submission_path):
            print(messages.get('register_success', '✅ 팀 등록 완료: {name} ({team_id})').format(name=args.name, team_id=args.team_id))
        else:
            print(messages.get('register_fail', '❌ 팀 등록 실패 (로그 확인)'))
            sys.exit(1)
            
    elif args.command == "reset-matches":
        if not args.yes:
            confirm = input("[WARNING] 모든 매치 데이터와 팀 통계가 초기화됩니다. 계속하시겠습니까? (yes/N): ")
            if confirm.strip().lower() != "yes":
                print("[CANCEL] 취소되었습니다.")
                return
        count = manager.reset_matches()
        print(f"[OK] 매치 {count}개 초기화 완료. 팀 등록 및 통계가 리셋되었습니다.")
        print("[NEXT] 'add-matches' 명령어로 새 대진표를 생성하세요.")

    elif args.command == "add-matches":
        count = manager.add_missing_matches()
        if count > 0:
            print(f"[OK] 신규 매치 {count}개가 추가되었습니다. 'run' 명령어로 실행하세요.")
        else:
            print("[INFO] 추가할 신규 매치 조합이 없습니다. (모든 팀이 이미 대전함)")

    elif args.command == "start":
        if args.round == "qualification":
            new_count = manager.create_qualification_round()
            if new_count > 0:
                print(messages.get('qualification_created', '✅ 예선 라운드 대진표가 생성되었습니다. (신규 {count} 경기)').format(count=new_count))
            else:
                print(messages.get('qualification_exists', '⚠️  대진표를 생성하지 못했습니다. (팀 부족 또는 이미 존재)'))
            
    elif args.command == "run":
        print(messages.get('match_start', '🚀 매치 실행을 시작합니다...'))
        start_time = time.time()
        manager.run_pending_matches()
        elapsed = time.time() - start_time
        minutes, seconds = divmod(elapsed, 60)
        if minutes >= 1:
            print(f"\n⏱️  총 경과 시간: {int(minutes)}분 {seconds:.1f}초")
        else:
            print(f"\n⏱️  총 경과 시간: {seconds:.1f}초")
        print(messages.get('match_complete', '✅ 모든 대기 중인 매치가 처리되었습니다.'))
        
    elif args.command == "leaderboard":
        leaderboard = manager.get_leaderboard()
        print("\n[LEADERBOARD]")
        header = f"{'#':<4} {'Team':<18} {'Win':<5} {'Draw':<6} {'Loss':<6} {'Pts':<5} {'Avg HP':<8}"
        sep = "-" * len(header)
        print("=" * len(header))
        print(header)
        print(sep)

        for rank, team in enumerate(leaderboard, 1):
            points = team.wins * 3 + team.draws
            print(f"{rank:<4} {team.name:<18} {team.wins:<5} {team.draws:<6} {team.losses:<6} {points:<5} {team.avg_hp_remaining:<8.1f}")
        
        print("=" * len(header))
        print()

if __name__ == "__main__":
    main()
