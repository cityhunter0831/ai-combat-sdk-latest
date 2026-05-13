"""
AI Combat Match Runner - 행동트리 기반 매치 실행 스크립트

참가자는 행동트리(YAML)와 커스텀 노드(Python)를 제출하여 대전합니다.
"""

import sys
import argparse
import yaml
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.match.runner import BehaviorTreeMatch  # noqa: E402

# 한국 시간대 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

# 설정 로드
def load_config():
    """매치 설정 로드"""
    config_file = PROJECT_ROOT / "config" / "match_config.yaml"
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    else:
        # 기본 설정 반환
        return {
            'default': {
                'rounds': 1,
                'scenario': 'bt_vs_bt',
                'max_steps': 0,  # 0 = 자동(5분, runner_core에서 Hz 기반 동적 계산)
                'verbose': True
            },
            'scenarios': ['bt_vs_bt', 'tail_chase'],
            'output': {
                'banner_width': 70,
                'show_replay_path': True,
                'show_round_summary': True
            },
            'paths': {
                'replay_dir': 'replays',
                'submissions_dir': 'submissions',
                'examples_dir': 'examples'
            }
        }


def get_tree_path(name: str) -> str:
    """행동트리 파일 경로 결정
    
    Args:
        name: 에이전트 이름 또는 파일 경로
        
    Returns:
        str: 행동트리 파일 절대 경로
        
    탐색 순서:
        0. 직접 경로 (경로 구분자 포함 시: 절대 경로 또는 PROJECT_ROOT 기준 상대 경로)
        1. submissions/{name}/{name}.yaml
        2. submissions/{name}.yaml
        3. examples/{name}.yaml
        4. examples/{name}/{name}.yaml
    """
    # 경로 구분자가 포함된 경우 직접 경로로 처리
    if "/" in name or "\\" in name or Path(name).is_absolute():
        direct_path = Path(name)
        if not direct_path.is_absolute():
            direct_path = PROJECT_ROOT / name
        if direct_path.exists():
            return str(direct_path.resolve())
        raise FileNotFoundError(f"Behavior tree file not found: {name}")
    
    # submissions 폴더 확인 (sub-dir: submissions/{name}/{name}.yaml)
    submission_path = PROJECT_ROOT / "submissions" / name / f"{name}.yaml"
    if submission_path.exists():
        return str(submission_path)
    
    # submissions 폴더 확인 (flat: submissions/{name}.yaml)
    submission_flat_path = PROJECT_ROOT / "submissions" / f"{name}.yaml"
    if submission_flat_path.exists():
        return str(submission_flat_path)
    
    # examples 폴더 확인 (flat: examples/{name}.yaml)
    example_path = PROJECT_ROOT / "examples" / f"{name}.yaml"
    if example_path.exists():
        return str(example_path)
    
    # examples 폴더 확인 (sub-dir: examples/{name}/{name}.yaml)
    example_subdir_path = PROJECT_ROOT / "examples" / name / f"{name}.yaml"
    if example_subdir_path.exists():
        return str(example_subdir_path)
    
    raise FileNotFoundError(f"Behavior tree file not found: {name}")


def run_match(
    agent1: str,
    agent2: str,
    rounds: int = None,
    scenario: str = None,
    max_steps: int = None,
    verbose: bool = None,
    log_csv: str = None,
) -> list:
    """두 행동트리 간 매치 실행
    
    Args:
        agent1: 첫 번째 에이전트 이름
        agent2: 두 번째 에이전트 이름
        rounds: 라운드 수 (config 기본값 사용)
        scenario: 시나리오 이름 (config 기본값 사용)
        max_steps: 최대 스텝 (config 기본값 사용)
        verbose: 상세 출력 여부 (config 기본값 사용)
        log_csv: CSV 로그 파일 경로 (None이면 저장 안 함)
        
    Returns:
        list: 매치 결과 객체 리스트
    """
    # 설정 로드
    config = load_config()
    default_config = config.get('default', {})
    output_config = config.get('output', {})
    paths_config = config.get('paths', {})
    
    # 기본값 설정 (config에서 가져오기)
    if rounds is None:
        rounds = default_config.get('rounds', 1)
    if scenario is None:
        scenario = default_config.get('scenario', 'bt_vs_bt')
    if max_steps is None:
        max_steps = default_config.get('max_steps', 1500)
    if verbose is None:
        verbose = default_config.get('verbose', True)
    
    banner_width = output_config.get('banner_width', 70)
    show_replay_path = output_config.get('show_replay_path', True)
    show_round_summary = output_config.get('show_round_summary', True)
    
    print("\n" + "=" * banner_width)
    print("  AI Combat Match")
    print("=" * banner_width)
    print(f"\nAgent 1: {agent1}")
    print(f"Agent 2: {agent2}")
    print(f"Rounds: {rounds}")
    print(f"Scenario: {scenario}")
    print()

    try:
        tree1 = get_tree_path(agent1)
        tree2 = get_tree_path(agent2)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return []

    # 에이전트 이름 추출 (파일 경로에서 stem만 사용)
    agent1_name = Path(tree1).stem
    agent2_name = Path(tree2).stem

    config_name = f"1v1/NoWeapon/{scenario}"
    results = []
    
    for round_num in range(1, rounds + 1):
        if rounds > 1 and show_round_summary:
            print(f"\n{'='*banner_width}")
            print(f"  Round {round_num}/{rounds}")
            print(f"{'='*banner_width}\n")
        
        start_time = time.time()
        
        replay_dir = PROJECT_ROOT / paths_config.get('replay_dir', 'replays')
        replay_dir.mkdir(exist_ok=True)
        timestamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
        replay_path = replay_dir / f"{timestamp}_{agent1_name}_vs_{agent2_name}.acmi"

        # CSV 로그 경로 결정 (타임스탬프 + 에이전트명 포함)
        csv_path = None
        if log_csv:
            log_dir = Path(log_csv)
            log_dir.mkdir(parents=True, exist_ok=True)
            if rounds > 1:
                csv_path = str(log_dir / f"{timestamp}_{agent1_name}_vs_{agent2_name}_round{round_num}.csv")
            else:
                csv_path = str(log_dir / f"{timestamp}_{agent1_name}_vs_{agent2_name}.csv")
        
        match = BehaviorTreeMatch(
            tree1_file=tree1,
            tree2_file=tree2,
            config_name=config_name,
            max_steps=max_steps,
            tree1_name=agent1_name,
            tree2_name=agent2_name,
            log_csv=csv_path,
        )

        print(f"{agent1_name} vs {agent2_name}")
        
        try:
            result = match.run(replay_path=str(replay_path), verbose=verbose)
        except Exception as e:
            print(f"❌ 매치 실행 실패: {e}")
            continue

        # 호환성 있는 속성 이름 사용
        steps = getattr(result, 'steps', getattr(result, 'total_steps', 'N/A'))
        elapsed_time = getattr(result, 'elapsed_time', getattr(result, 'duration_seconds', 'N/A'))
        tree1_reward = getattr(result, 'tree1_reward', 0.0)
        tree2_reward = getattr(result, 'tree2_reward', 0.0)
        winner = getattr(result, 'winner', 'unknown')

        if show_replay_path:
            print(f"  리플레이: {replay_path.name}")

        # 표준화된 결과 객체 (딕셔너리 사용으로 클래스 중복 제거)
        match_result = {
            'winner': winner,
            'total_steps': steps if steps != 'N/A' else 0,
            'duration_seconds': elapsed_time if elapsed_time != 'N/A' else 0,
            'tree1_reward': tree1_reward,
            'tree2_reward': tree2_reward,
            'success': True,
        }
        results.append(match_result)
        
        elapsed = time.time() - start_time
        print(f"\nRound {round_num} 완료 (소요 시간: {elapsed:.2f}초)")
    
    # 전체 결과 요약
    if rounds > 1 and results and show_round_summary:
        print("\n" + "=" * banner_width)
        print("  전체 결과 요약")
        print("=" * banner_width)
        
        agent1_wins = sum(1 for r in results if r['winner'] == "tree1")
        agent2_wins = sum(1 for r in results if r['winner'] == "tree2")
        draws = sum(1 for r in results if r['winner'] == "draw")
        
        print(f"\n{agent1}: {agent1_wins}승")
        print(f"{agent2}: {agent2_wins}승")
        print(f"무승부: {draws}")
        
        if agent1_wins > agent2_wins:
            print(f"\n🏆 승자: {agent1}")
        elif agent2_wins > agent1_wins:
            print(f"\n🏆 승자: {agent2}")
        else:
            print("\n🤝 무승부")
    
    print("\n" + "=" * banner_width)
    print("🎉 매치 완료!")
    print("=" * banner_width + "\n")

    return results


def main():
    config = load_config()
    default_config = config.get('default', {})
    scenarios = config.get('scenarios', ['bt_vs_bt', 'tail_chase'])
    
    parser = argparse.ArgumentParser(
        description="AI Combat Match Runner - 행동트리 기반 매치 실행",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python run_match.py --agent1 ace_fighter --agent2 simple_fighter
  python run_match.py --agent1 sample_behavior_tree --agent2 aggressive_fighter --rounds 3
  python run_match.py --agent1 my_submission --agent2 ace_fighter --scenario tail_chase
  
로깅 예시:
  python run_match.py --agent1 eagle1 --agent2 simple --log-csv
        """
    )
    
    parser.add_argument('--agent1', type=str, required=True, help='Agent 1 이름 (submissions/ 또는 examples/ 폴더)')
    parser.add_argument('--agent2', type=str, required=True, help='Agent 2 이름')
    parser.add_argument('--rounds', type=int, default=default_config.get('rounds', 1), 
                        help=f'라운드 수 (기본값: {default_config.get("rounds", 1)})')
    parser.add_argument('--scenario', type=str, default=default_config.get('scenario', 'bt_vs_bt'), 
                        choices=scenarios, 
                        help=f'시나리오 (기본값: {default_config.get("scenario", "bt_vs_bt")})')
    parser.add_argument('--max-steps', type=int, default=default_config.get('max_steps', 0), 
                        help='최대 스텝 수 (기본값: 0 = 자동, 5분을 현재 Hz로 환산)')
    parser.add_argument('--quiet', action='store_true', help='상세 출력 비활성화')
    parser.add_argument('--log-csv', type=str, nargs='?', const='logs', default=None,
                        help='CSV 로그 저장 폴더 (기본값: logs) - 파일명은 자동 생성')
    
    args = parser.parse_args()
    
    run_match(
        agent1=args.agent1,
        agent2=args.agent2,
        rounds=args.rounds,
        scenario=args.scenario,
        max_steps=args.max_steps,
        verbose=not args.quiet,
        log_csv=args.log_csv,
    )


if __name__ == "__main__":
    main()
