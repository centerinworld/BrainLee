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

## 처리 결과 (2026-09-24 후속)
- 무상증자 138건 event_date 정정 완료(`scripts/fix_bonus_issue_event_date_20260924.py --verify-dart -1 --apply`, run_id `bonus_event_date_fix_20260924_184810_3dffdb`). 백업 `corporate_action_events_backup_20260924`. DART 신주배정기준일 대조: 일치 122 / 불일치 1(187660, 제외) / 확인불가 16.
- 수집기 근본 수정: `collectors/dart_equity_issue_collector.py` — BONUS는 직전 거래일(권리락일) 저장(연말 폐장일 처리 포함, 정정 138건과 100% 일치).
- turnaround 재등록 성공(스위트 0f3275a34dc8f8d6). regime_adaptive는 CR홀딩스(000480) 회사분할 이벤트(2023-04-13, 100,400→8,800원, `review_required`, 계수 없음)로 게이트 거부 — DART 분할계획서로 계수 확정 필요.
- 잔여: 무상증자 15건 + rights_issue 515건(정합 54/-1일 52/-2일 143/+일 107/미확인 138) 분석·정정.


## 후속 처리 (2026-09-24 저녁)
1. **company_split 153건 분해**: 단순 액면분할('주식분할결정') 86건 + 진짜 회사분할 67건. 액면분할 중 **56건을 stock_split으로 재분류·확정**
   (`scripts/confirm_stock_split_from_company_split_20260924.py`, run_id `split_confirm_20260924_185859_053ac1`, 백업 `corporate_action_events_backup_20260924_split`).
   조건: 공시명 '주식분할'·철회 아님, 비율≥1.5, 이벤트 당일 가격단절이 분할비율과 ±25% 이내(거래정지 재개 첫날은 기준가 대비 ±30% 가격제한폭 안에서 형성되므로 10~25% 편차가 정상), ±3일 내 기확정 이벤트 없음(이중적용 방지).
   나머지 30건(가격단절 불일치 14·비율<1.5/결측 16)과 진짜 회사분할 67건은 `review_required` 유지(계 97건).
2. **CR홀딩스(000480, 2023-04-13) 확정**: 인적분할(존속 0.7036208 : 신설 0.2963792, DART 20230616000516) + 액면분할 1:10 동시 →
   KRX 수정주가 방식 계수 = 0.7036208/10 = **0.07036208**(기준가 7,064원, 재개 첫날 8,800원 +24.6% 정합). run_id `cr_holdings_split_20260924`. 이후 regime_adaptive 재등록 재시도.
3. **무상증자 잔여**: 정정 후 -1일 오프셋 잔여 3건(099750, 415380는 같은 계수의 이벤트가 하루 간격으로 중복 확정, 187660은 DART 기준일 상이로 제외).
   중복 쌍은 다른 세션의 백테스트 계수 로더/뷰(A8, 완전 동일 계수 병합)가 읽기 시점에 병합하므로 DB는 변경하지 않음.
4. **±5일 내 factor_confirmed 쌍 88건**(동일계수 약 51건: rights_issue↔rights_issue 26, rights_or_other_issue↔rights_issue 18 등) — 같은 사건이 서로 다른 파이프라인(DART_equity_issue / marcap·shares)에서 두 번 확정된 흔적. 읽기 시점 병합에 의존 중이므로, 병합 로직이 바뀌면 이중 적용이 되살아남 → 장기적으로 DB 차원의 중복 정리 필요.
5. **유상증자(rights_issue 515건)는 자동 정정하지 않음**: (a) 일반공모·제3자배정은 권리락이 없어 '단절 미확인'(138건)이 정상, (b) 계수가 0.9대인 건은 시장의 우연한 하락과 구분되지 않아 가격단절만으로 날짜를 옮기면 오정정 위험, (c) DART 신주배정기준일 파싱이 표본 60건 중 3건만 성공(문서 구조 상이).
   권장: 주주배정 유상증자만 분리(공시 '주주배정' 표기)하고, 그 부분집합에 대해 무상증자와 같은 방식(DART 기준일 대조 + 가격단절)으로 별도 검증.

## 재등록 최종 (2026-09-24 밤)
- regime_adaptive 재등록 성공(스위트 f30035042d15e5dd). 게이트 통과에 순서대로 필요했던 조치: ① CR홀딩스 000480 계수 확정 ② 두산 000150 2024-11-07 격리(`recurring_splice_auto_confirmation_invalidated`) 사유를 Naver 대조 후 기본 등급으로 하향 + `audit_price_jumps_and_build_canonical.py --require-postgres` 재빌드(`price_jump_audit`은 이 재빌드가 있어야 격리 변경이 반영됨) ③ 만호제강 001080(1:10 액면분할, 계수 0.1)·삼양홀딩스 000070(인적분할 존속비율 0.9039233, KRX 방식, 재상장 첫날 기준가 대비 -33% 잔여 한계 명시) 확정.
- 세 전략 최종 6기간 평균: turnaround +25.8%(최신 +101.6%는 단일 종목 의존), regime_adaptive +9.95%, composite +4.75% — 모두 `retired` 유지.
- 교훈: 미확정 기업행위는 '가격 단절 + DART 원문 + 이중적용 가드' 3중 근거로만 확정했고, 확정 불가(진짜 회사분할 66건·가격불일치 액면분할 30건·유상증자 515건)는 review_required로 유지.

## 추가 처리 (2026-09-25 새벽)
- **유상증자 분류**: DART 유상증자결정 829건 원문의 증자방식 분류(`scripts/classify_rights_issue_method_20260924.py`, 테이블 `rights_issue_method_20260924`, CSV `research_outputs/rights_issue_method_20260924.csv`): 제3자배정 614·미상/기타 ~150·일반공모 23·**주주배정 32**·주주우선공모 4.
  → 권리락이 있는 것은 4%뿐. 제3자배정·일반공모 637건은 이벤트 ±1일 가격단절이 9.4%(무작위 대조 7.4%)라 실제 가격 효과 없음 → `corporate_action_no_price_effect`에 등재해 조정 제외(로더·뷰·수집기 반영, 테스트 `tests/test_corp_action_no_price_effect.py`).
  영향: 199종목, 계수 평균 0.839(최소 0.179). 제외 후 재등록 결과 turnaround +25.8%→+4.1%, regime_adaptive +9.95%→+0.4%.
- **주주배정 32건**은 DART 신주배정기준일 추출 성공(36건 기준일 확보) — 권리락 오프셋 검증은 이 부분집합에서만 의미 있음(미수행, 다음 과제).
- **잔여 액면분할 30건**: 15건 확정(허용치를 가격제한폭 ±30%에 맞춰 ±32%로, 재개 첫날 상한가 케이스 포함), 10건은 주식수 불변인 가짜 이벤트(DART 실제 분할 상장일이 다른 날짜), 5건은 가격 불일치로 유지.
- **주주배정 오프셋 측정(36건, 기준일 확보분)**: 실제 단절일 − DART 신주배정기준일(거래일) 분포 = -1일 5 / -2일 6 / 0일 2 / +1~+6일 10 / 단절 미확인 11. event_date는 확인된 25건 전부 DART 기준일과 동일.
  규칙이 하나로 수렴하지 않음 — (a) 같은 종목이 기준일을 바꿔 여러 번 재공시(예 066430 event 4건) (b) 계수 0.75~0.95는 시장의 우연한 변동과 구분 불가 (c) 표본 소수 → **자동 정정하지 않음**. 정정하려면 종목별로 실제 권리락일(KRX 공시 또는 시초가 기준가 산정)을 확인해야 함.

