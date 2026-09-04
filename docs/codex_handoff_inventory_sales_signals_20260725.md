# Codex Handoff — 재고·매출·수주 선행신호 DB

Date: 2026-07-25

## 완료 사항

- `scripts/build_inventory_sales_signals.py`
  - `dart_cost_quarterly.inventory_assets_krw`를 재고 원천으로 사용.
  - `financial_data.revenue`, `order_backlog`, `order_contracts`를 결합.
  - `inventory_sales_signals`, `inventory_sales_signal_runs` 생성.
  - 신호 구분:
    - `build_up`: 재고 증가 + 매출/수주 확인 = 증산준비 후보.
    - `digestion`: 재고 감소 + 매출 증가 = 재고소화 후보.
    - `risk`: 재고 증가 + 매출/수주 확인 약함 = 악성재고 위험.

- `routes/inventory_sales_signals.py`
  - `GET /api/inventory-sales-signals/top`
  - `GET /api/inventory-sales-signals/stock/{stock_code}`
  - `POST /api/inventory-sales-signals/rebuild`

- `signal_engine.py`
  - `_load_inventory_sales_bonus_map(min_score=4)` 추가.
  - 최신 CFS 우선, 없으면 OFS 최신 행 사용.
  - 좋은 신호는 `bonus`, 악성재고는 `risk_penalty`로 반환.

- `tenbagger_engine.py`
  - 텐버거 촉매 점수에 재고·매출·수주 확인 보너스 반영.
  - 악성재고 위험은 catalyst에서 감점.

- `frontend/src/App.jsx`
  - `DART 수주·공급계약 공시 알림` 화면에 `재고·매출·수주` 탭 추가.
  - 종목 클릭 시 개별종목 분석 페이지로 이동.

- `scheduler.py`
  - `스크리너/스크리너사전계산`을 공용 stock.db write lock 대상에서 제외.
    - 기존에는 장시간 스크리너가 `/tmp/stock_dashboard_db_write.lock`을 잡아 신규 수집 빌드가 막힘.
  - `DART원가재고` 주간 수집 완료 후 `build_inventory_sales_signals.py` 자동 실행.

- `scripts/audit_inventory_sales_signals.py`
  - row/stock/period/good/risk/revenue 결측 분포를 점검.
  - 결과 JSON: `research_outputs/inventory_sales_signals_audit_YYYYMMDD.json`

## 현재 적재 결과

- `inventory_sales_signals`: 50,780행
- 종목 수: 2,317종목
- 기간: 2020Q1 ~ 2026Q2
- 좋은 신호 행: 10,658행
- 위험 신호 행: 2,011행
- `signal_score >= 4`: 9,803행
- `risk_score >= 4`: 1,861행

유형별 분포:

- `neutral`: 38,111행 / 2,317종목
- `digestion`: 5,710행 / 1,967종목
- `build_up`: 4,948행 / 1,833종목
- `risk`: 2,011행 / 1,061종목

## 검증 완료

- `/Applications/stock_dashboard/venv/bin/python scripts/build_inventory_sales_signals.py --since-year 2020`
- `/Applications/stock_dashboard/venv/bin/python scripts/audit_inventory_sales_signals.py`
- `/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/build_inventory_sales_signals.py scripts/audit_inventory_sales_signals.py routes/inventory_sales_signals.py main.py scheduler.py signal_engine.py tenbagger_engine.py`
- `cd frontend && npm run build`

## 주의/추가 확인

1. 자동화 등록
   - Codex automation 도구가 현재 `projectId` 요구/생성 실패로 새 자동화를 저장하지 못했음.
   - 스케줄러에는 이미 `DART원가재고` 후 자동 빌드가 들어가 있음.
   - 다음 세션에서 가능하면 `재고·매출·수주 선행신호 주간 검증` 자동화를 다시 등록.

2. 미완료 분기 제외
   - 최초 빌드에서 원천 테이블의 2026Q3/Q4 행이 섞여 최신기간이 과장됨.
   - `build_inventory_sales_signals.py`에서 분기 종료일이 오늘보다 미래인 행은 제외하도록 수정.
   - 현재 최신기간은 2026Q2.

3. QoQ 극단값
   - 예: 재고QoQ +43,686% 같은 값은 전분기 0/미세값 기저효과일 수 있음.
   - 현재는 label에 그대로 남아 있으므로, 프론트에서 `기저효과` 배지를 추가하거나 scoring cap을 더 강하게 둘지 검토.

4. 백테스트 반영
   - 텐버거 실시간 scoring에는 반영했지만, 전략센터 백테스트 조합에는 아직 별도 factor로 넣지 않음.
   - 추천 검증:
     - `build_up` 단독
     - `digestion` 단독
     - `risk` 제외 필터
     - `수주급증 + 선수금계약부채 + build_up`
     - `V-EARNINGS/V-MOONSHOT`와 교차

5. 수집 원천 보강
   - `dart_inventory_quarterly`는 현재 비어 있고, 실사용은 `dart_cost_quarterly.inventory_assets_krw`.
   - 명칭 혼선을 줄이려면 장기적으로 `dart_inventory_quarterly`를 view 또는 alias로 정리.

## Claude 검증 요청

- API 응답:
  - `/api/inventory-sales-signals/top?mode=all&min_score=4&limit=100&fs_div=CFS`
  - `/api/inventory-sales-signals/stock/005930?fs_div=CFS`
- 텐버거 후보의 `reasons`에 `📦 재고...` 또는 `⚠️ 악성재고...`가 합리적으로 붙는지 확인.
- DART 원가재고 주간 수집 후 `inventory_sales_signals`가 자동 재빌드되는지 로그 확인.
- 2026Q2 최신 샘플과 QoQ 극단값 샘플 원문 대조.
