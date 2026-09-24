# 인계: corporate_action_events 무상증자 event_date 1일 오프셋 (2026-09-24)

**발견 경로**: turnaround/regime_adaptive 재등록(`scripts/rerun_all_after_audit_rebuild.py`)이 가격 무결성 게이트에서 거부됨
(신진에스엠 138070 2022-08-08, 솔루스첨단소재 336370 2024-01-08 — 각 run 에 `confirmed_corporate_action` 1건).

**증거** (`scripts/audit_corp_action_event_date_offset_20260924.py`, 읽기 전용, 결과 CSV `research_outputs/corp_action_event_date_offset_20260924.csv`):
- factor_confirmed(<0.9) 796건 중 `bonus_issue` 218건: **153건은 실제 가격 단절이 event_date 보다 정확히 1거래일 앞섬**, 정합(0일)은 4건.
  `stock_split`은 10건 중 9건이 정합 → 무상증자만 event_date가 권리락일이 아니라 배정기준일로 들어간 것으로 추정.
- 예: 신진에스엠 8,590(8/05) → 4,550(8/08) 단절, 이벤트는 8/09(factor 0.5126). 솔루스첨단소재 27,000(1/05) → 15,380(1/08), 이벤트 1/09(factor 0.5000).
- `rights_issue`(515건)는 정합 54 / -1일 52 / -2일 143 / +일 107 / 미확인 138로 혼재 — 별도 분석 필요(권리락 규칙이 다름).

**영향**: 보정계수가 하루 늦게 적용돼 단절일 다음 하루가 인위적 왜곡(이미 낮아진 가격에 계수를 한 번 더 곱함) → 그 구간을 보유한 백테스트 손익 왜곡, 게이트가 해당 run 을 거부.
turnaround·regime_adaptive 재등록이 막혀 두 전략은 기존(구) 스위트가 계속 선택 상태(현재 등급: turnaround `validation_queue`, regime_adaptive `retired` — 새 결과와 방향 동일).

**제안(미적용, 다른 세션이 `corporate_action_events`를 작업 중이라 충돌 회피)**:
1. bonus_issue 중 판정 `하루 이른 단절(-1)`인 153건의 event_date 를 실제 단절일로 정정(백업 테이블 + `data_fix_log`/run_id, 샘플 10건 이상 DART 배정기준일·권리락일 대조 후).
2. `(stock_code, event_date, event_type)` 유일성 충돌, 하류 소비자(`backtest_common._load_corp_action_factors`, `price_history_quality_v`, `stock_price_daily_adjusted_v`) 확인.
3. 정정 후 `python3 scripts/rerun_all_after_audit_rebuild.py --strategies turnaround,regime_adaptive --selected-by ... --note ...` 재실행.
4. 근본 수정: `register_corporate_events_from_dart_20260924.py` 등 이벤트 생성 로직이 무상증자에 배정기준일 대신 권리락일(=기준일 직전 거래일)을 쓰도록.
