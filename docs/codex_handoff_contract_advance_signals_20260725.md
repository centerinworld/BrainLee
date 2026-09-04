# Codex Handoff — 계약부채/선수금 선행신호 DB 구축 (2026-07-25)

## 목적
- 수주공시/수주잔고 외에 실적 인식 전 선행 신호가 될 수 있는 `계약부채`, `선수금`, `계약자산/미청구공사` 흐름을 별도 DB화했다.
- 핵심 해석:
  - `gross_customer_funding = contract_liabilities + advances_received`
  - `net_customer_funding = gross_customer_funding - contract_assets`
  - 고객에게서 먼저 받은 돈/청구 초과분이 커지면 향후 매출 인식 가능성을 추적할 수 있다.

## 신규 테이블
- `contract_advance_signals`
  - stock/period/fs_div별 파생 신호.
  - 주요 컬럼: `contract_liabilities`, `advances_received`, `contract_assets`, `gross_customer_funding`, `net_customer_funding`, `gross_to_revenue_pct`, `gross_qoq_pct`, `gross_yoy_pct`, `signal_score`, `signal_label`, `quality_flag`.
- `contract_advance_signal_runs`
  - 재구축 실행 이력.

## 신규 스크립트
- `scripts/build_contract_advance_signals.py`
  - 원천: `dart_report_items_quarterly`
  - 수집 대상 metric:
    - `contract_liabilities`
    - `advances_received`
    - `contract_assets`
  - 금융/보험성 계약부채는 `financial_like_excluded`로 제외.
  - 2020년 이후 재구축 명령:
    - `/Applications/stock_dashboard/venv/bin/python scripts/build_contract_advance_signals.py --since-year 2020`
- `scripts/audit_contract_advance_signals.py`
  - 테이블 존재/커버리지/최신성/positive signal/API 전제 점검.
  - 리포트:
    - `research_outputs/contract_advance_signal_audit_YYYYMMDD.md`
    - `research_outputs/contract_advance_signal_audit_YYYYMMDD.json`

## API
- `routes/contract_advance_signals.py`
- 등록 경로:
  - `GET /api/contract-advance-signals/top`
  - `GET /api/contract-advance-signals/stock/{stock_code}`
  - `POST /api/contract-advance-signals/rebuild`

## 전략 연결
- `signal_engine._load_contract_advance_bonus_map(min_score=4)` 추가.
  - CFS 최신값 우선, 없으면 OFS.
  - 반환: `bonus`, `label`, `signal_score`, `gross_to_revenue_pct`, `gross_qoq_pct`, `gross_yoy_pct`, `period`, `fs_div`.
- `tenbagger_engine.py`
  - 촉매 점수에 `선수성부채` 보너스 반영.

## 스케줄러
- `scheduler.py`
  - 기존 `DART임직원CH` 주간 보강 작업 성공 후 `build_contract_advance_signals.py --since-year 2020` 자동 실행.
- Codex 자동화
  - 이름: `선수금·계약부채 선행신호 주간 검증`
  - id: `automation-2`
  - 실행: 매주 일요일 04:20

## 현재 구축 상태
- `contract_advance_signals`: 2,071행
- 종목 수: 63개
- 기간: 2020~2026
- signal_score > 0: 1,245행
- 금융/보험성 제외: 166행
- 최신 2026Q1 상위 예:
  - HD건설기계
  - 원익IPS
  - 에코프로
  - 포스코퓨처엠
  - 대한항공

## 검증
- `/Applications/stock_dashboard/venv/bin/python scripts/audit_contract_advance_signals.py`
  - 결과: `contract_advance_signals audit OK`
- Python compile 통과.
- `npm run build` 통과.

## 주의점
- QoQ가 수천 퍼센트로 보이는 경우는 전분기 기저가 아주 작아서 발생할 수 있다.
- 프론트에서는 `gross_qoq_pct >= 1000%`이고 `gross_to_revenue_pct < 10%`이면 `기저효과`로 표시한다.
- 실제 투자 신호로는 QoQ 단독보다 `매출 대비 비중`, `수주공시 급증`, `기관/외국인 수급`, `가격 위치`와 함께 보아야 한다.
