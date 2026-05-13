"""
step_callback 실시간 모니터링 예제

BT 개발 중 매 틱의 상태를 콘솔에서 즉시 확인하는 용도.
전체 데이터 분석은 --log-csv 옵션의 CSV 파일을 사용하세요.
"""


def create_console_monitor():
    """
    매 틱마다 핵심 전투 상황을 콘솔에 출력하는 step_callback을 생성합니다.

    Returns:
        step_callback 함수
    """
    def monitor(step, agent_id, obs, action, low_level_action, reward, health, active_nodes, bfm_situation):
        """매 틱마다 핵심 상태를 콘솔에 출력"""
        try:
            bfm_str = str(bfm_situation) if bfm_situation else ""
            distance_ft = obs.get("distance_ft", 0)
            ata_deg = obs.get("ata_deg", 0.0)

            active_node = ""
            if active_nodes:
                success_nodes = [n for n, s in active_nodes if s == 'SUCCESS']
                active_node = success_nodes[-1] if success_nodes else ""

            print(f"[{step:4d}] {agent_id} | "
                  f"BFM={bfm_str:20} | "
                  f"HP={health['ego']:5.1f}/{health['enm']:5.1f} | "
                  f"WEZ={obs.get('in_wez', False)} | "
                  f"Dist={distance_ft:5.0f}ft ATA={ata_deg:5.1f}deg | "
                  f"Act={action} | "
                  f"Node={active_node}")
        except Exception as e:
            print(f"[모니터 오류] step={step}, agent={agent_id}: {e}")

    return monitor


# 사용 예제
if __name__ == "__main__":
    from src.match.runner import BehaviorTreeMatch

    match = BehaviorTreeMatch(
        tree1_file="examples/eagle1/eagle1.yaml",
        tree2_file="examples/simple.yaml",
        step_callback=create_console_monitor(),
        log_csv="logs/match_data.csv",
    )
    match.run(verbose=True)
