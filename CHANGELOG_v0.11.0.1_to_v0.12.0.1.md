# Changelog: v0.11.0.1 → v0.12.0.1

**릴리즈 날짜:** 2026-06-02

## 변경사항

- refactor: BFM 전술 분기 재설계 — Aspect Angle 기반 기동/비기동 표적 분리
- feat: red5 튜닝 하니스 추가 + AspectAngleAbove 조건 노드
- test: 다중 시드 풀리그 재실행 — red5 vs red1 격추 재현, red2/red4 매치 결과 변동
- fix: UnderThreat 의미 정정 + red4 ThreatResponse를 InEnemyWEZ로
- test: 다중 시드 풀리그 검증 — red5 단독 1위 결정적 입증
- feat: Lufbery Circle 해결 — OneCircleFight maneuver native + 조건 정밀화
- feat: BT output 인터페이스 재설계 — Tactical Maneuvers (Z-series)
- feat: NN 완전 우회 — Classical PD Controller로 저수준 제어 대체
- test: red5 라운드 로빈 재실행 — 성능 변동 및 red3 vs red5 결과 변화 기록
- feat: red5 v7.0.0→v4.0.0 롤백 — Lufbery 탈출 실험 종료, 최선 안정 버전 복귀
- feat: red5 v7.0.0 복귀 — Lufbery 평형 안정화 우선, 펄스 탈출 보류
- feat: red5 v10.1.0 — LufberyEscape 버스트 지속 시간 연장 및 쿨다운 단축
- feat: red5 v10.0.0 — LufberyEscape 펄스 사이클로 코너 스피드 회복 및 평형 탈출
- feat: red5 v8.1.0 — VerticalLead 다이브 전환 임계값 완화 및 속도 증강
- feat: red5 v8.0.0 — AltGapAbove 조건으로 VerticalLead 자동 다이브 전환
- feat: red5 v7.0.0 — VerticalLead로 Lufbery 평형 위 수직 우위 확보
- feat: red5 v4.0.0 — 공격적 LeadPursuit 조기 진입 + 라운드 로빈 검증 도구 추가
- docs: 단위 흐름 분석 문서 후속 검증 결과 추가 — P0~P3 수정 회귀 테스트 완료
- docs: Hard Deck 임계값 및 AA 정의 표준화 — P0/P1/P2 권장 수정 적용 완료
- feat: 단위 흐름 분석 문서 추가 — P0 energy_state 부호 반전 버그 발견
- feat: 속도 하한 거버너 추가 — STALL 누적 방지 안전장치
- feat: ZIP 심볼릭 링크 차단 및 Supabase client lazy 초기화
- feat: 참가자 Python 코드 AST 보안 검사 시스템 추가
- docs: Claude Code 작업 가이드 추가 (워크스페이스 전체 컨텍스트)
- refactor: Elo 레이팅 시스템 제거 및 평균 HP 기반 순위 체계로 전환
- docs: SDK 참가자용 룰북 및 변경이력 문서 추가
- docs: 룰북, 변경이력, 토너먼트 가이드 문서 추가
- feat: 매치 감사 시스템 추가 — 결정론적 재실행 및 결과 검증
- feat: 적 정보 비공개 정책 적용 및 리더보드 정렬 규칙 개선
- feat: 매치 결정론 및 BT 예외 격리 시스템 추가
- 비행 안전 모니터 추가 및 심판 시스템 통합
- README 및 문서에 확정된 시간 계층 규칙 추가
- 10Hz 및 20Hz BT 실험 결과 데이터 추가

