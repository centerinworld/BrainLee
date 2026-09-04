# Codex Handoff — 현금전환 품질 신호 DB

Date: 2026-07-25

## 목적

수주/선수금/재고 신호 이후, 자동매매에서 반드시 봐야 하는 품질 축으로 `현금전환 품질`을 추가했다.

핵심 질문:

- 매출과 순이익이 실제 영업현금흐름으로 전환되는가?
- 흑자인데 영업현금흐름이 음수인가?
- 매출채권이 매출보다 빠르게 쌓여 회수 리스크가 커지는가?

## 완료 사항

- `scripts/build_cash_conversion_signals.py`
  - `financial_data`: 매출, 영업이익, 순이익
  - `cash_flow_data`: 영업CF, CAPEX, FCF
  - `dart_bs_items`: 매출채권
  - 위 3개 원천을 결합해 `cash_conversion_signals` 생성.

- `routes/cash_conversion_signals.py`
  - `GET /api/cash-conversion-signals/top`
  - `GET /api/cash-conversion-signals/stock/{stock_code}`
  - `POST /api/cash-conversion-signals/rebuild`

- `signal_engine.py`
  - `_load_cash_conversion_bonus_map(min_score=4)` 추가.
  - 최신 CFS 우선으로 좋은 신호는 `bonus`, 현금전환 위험은 `risk_penalty` 반환.

- `tenbagger_engine.py`
  - 현금전환 양호는 catalyst 보너스.
  - 현금전환 위험은 catalyst 감점.
  - 보수적으로 최대 +4 / -4까지만 반영.

- `frontend/src/App.jsx`
  - `DART 수주·공급계약 공시 알림` 화면에 `현금전환` 탭 추가.
  - 매출채권 미수집 종목은 “현금흐름 중심 판정”으로 명시.

- `scheduler.py`
  - `현금흐름배치` 성공 후 `build_cash_conversion_signals.py` 자동 실행.
  - `DART임직원CH` 성공 후에도 재실행해 매출채권 보강분 반영.

- `scripts/audit_cash_conversion_signals.py`
  - row/stock/period/good/risk/missing receivable 분포 점검.

## 현재 적재 결과

- `cash_conversion_signals`: 59,480행
- 종목 수: 2,563종목
- 기간: 2020Q1 ~ 2026Q1
- 좋은 현금전환: 32,401행
- 현금전환 위험: 14,750행
- `signal_score >= 4`: 32,401행
- `risk_score >= 4`: 14,750행
- 매출채권 미수집 행: 57,170행
- 4분기 rolling OCF 마진 산출 행: 43,635행
- 4분기 rolling FCF 마진 산출 행: 37,897행

유형별 분포:

- `cash_quality`: 32,401행 / 2,484종목
- `cash_risk`: 14,750행 / 2,404종목
- `neutral`: 12,329행 / 2,317종목

## 검증 완료

- `/Applications/stock_dashboard/venv/bin/python scripts/build_cash_conversion_signals.py --since-year 2020`
- `/Applications/stock_dashboard/venv/bin/python scripts/audit_cash_conversion_signals.py`
- `/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/build_cash_conversion_signals.py scripts/audit_cash_conversion_signals.py routes/cash_conversion_signals.py main.py scheduler.py signal_engine.py tenbagger_engine.py`
- `cd frontend && npm run build`
- API 확인:
  - `/api/cash-conversion-signals/top?mode=all&min_score=4&limit=5&fs_div=CFS`
  - latest_period: `2026Q1`

## 주의/추가 확인

1. 매출채권 커버리지
   - `dart_bs_items.item_key='trade_receivable'` 커버리지는 93~94종목 수준.
   - 따라서 대부분 종목은 영업CF/FCF 중심 판정이다.
   - Claude는 `dart_report_items_quarterly.trade_receivable`와 `dart_bs_items`를 통합해 매출채권 커버리지를 100종목 이상으로 확대 가능한지 확인.
   - 2026-07-26 Codex 수정: 매출채권/매출 비율은 분기 매출 그대로가 아니라 `매출채권 / (분기매출 × 4)`로 계산하도록 변경. 재무상태표 잔액과 분기 flow를 직접 나눠 4배 과장되던 오탐을 줄였다.
   - `dart_report_items_quarterly` 보강분은 유동/단기/일반 매출채권을 우선하고 장기/기타/총액성 항목은 후순위로 선택.

2. 현금흐름 계절성
   - 2026-07-25 추가 개선으로 최근 4분기 rolling OCF/FCF 마진을 반영했다.
   - `rolling4_ocf_margin_pct < 0`이고 최근 4분기 중 OCF+ 분기가 1개 이하이면 위험점수에 반영한다.
   - 단일 분기 OCF 음수보다 안정적이지만, 건설/조선 업종은 프로젝트별 운전자본 변동이 커서 백테스트에서 업종별 threshold 검증 필요.

3. 백테스트 반영 필요
   - 현재 실시간 텐버거 scoring에는 반영.
   - 전략센터 백테스트에는 아직 factor로 명시 투입하지 않음.
   - 추천 조합:
     - `cash_quality` 단독
     - `cash_risk 제외`
     - `V-EARNINGS/V-MOONSHOT + cash_quality`
     - `수주급증 + 선수금계약부채 + cash_risk 제외`
     - `재고 build_up + cash_quality`

4. 프론트 확인
   - 수주공시 화면의 `현금전환` 탭에서 위험/양호 종목이 정상 표시되는지 확인.
   - 종목 클릭 시 개별종목 페이지 이동 정상 여부 확인.

## Claude 검증 요청

- `cash_conversion_signals`의 최신 CFS/OFS 우선순위가 적절한지 확인.
- 흑자 + OCF 음수 감점이 업종별로 너무 과도하지 않은지 이벤트 스터디로 확인.
- 매출채권 데이터 확대 가능성 확인.
- `_load_cash_conversion_bonus_map`이 텐버거 후보 `reasons`에 합리적으로 붙는지 샘플 20종목 확인.
