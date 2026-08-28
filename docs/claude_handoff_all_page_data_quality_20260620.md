# Claude Handoff — 전체 페이지 데이터 품질 점검 및 수집 보강 (2026-06-20)

## 결론 요약

- 전체 페이지 핵심 데이터셋 27개를 점검했다.
- 1차 감사: OK 12 / 수집필요 10 / 검토필요 4 / 누락 0
- 보강 후 재감사: OK 14 / 수집필요 8 / 검토필요 4 / 누락 0
- Codex 추가 보강 후 재감사: OK 23 / 수집필요 4 / 검토필요 0 / 누락 0
- 즉시 수집 완료/진행:
  - KIS OHLCV 수집기 버그 수정 후 2026-06-19 샘플 정상 갱신 확인
  - 공공데이터 최근 백필 2026-06-09~2026-06-20 실행
  - 프로그램 매매 시장/종목 백필 진행 및 기준선 충족
  - Kiwoom 상장정보/신용/외국인지분 phase2 보강 작업 실행 중
  - Kiwoom 종목별 수급 2020~현재 백필 실행 중
- 감사 기준/데이터 정리:
  - `stock_price_daily`, `investor_trading_daily`, `foreign_holding_daily`, `short_sell_daily`, `financial_data`, `cash_flow_data`는 운영 fallback/canonical 테이블을 감사에 반영하도록 수정
  - `consensus_targets` 실제 중복 534건 정리 완료, 백업 테이블 `consensus_targets_duplicate_backup_20260620` 생성
- 재무 무결성 10개 핵심 검사: ALL PASS
- 단, 사업 데이터 파서 감사에서는 수주잔고/세그먼트/원재료비 관련 검토 CSV가 생성됨.

## 생성된 근거 파일

- 전체 페이지 감사:
  - `/Applications/stock_dashboard/scripts/audit_all_page_data_quality.py`
  - `/Applications/stock_dashboard/research_outputs/all_page_data_quality_20260620.md`
  - `/Applications/stock_dashboard/research_outputs/all_page_data_quality_20260620.json`
  - `/Applications/stock_dashboard/scripts/cleanup_consensus_duplicates.py`
  - `/Applications/stock_dashboard/research_outputs/consensus_duplicate_cleanup_20260620.json`
- 보강 실행:
  - `/Applications/stock_dashboard/scripts/run_page_data_remediation_20260620.py`
  - `/Applications/stock_dashboard/scripts/run_page_data_remediation_phase2_20260620.py`
  - `/Applications/stock_dashboard/scripts/backfill_kiwoom_investor_2020_2021.py`
  - `/Applications/stock_dashboard/run/page_data_remediation_20260620/`
  - `/Applications/stock_dashboard/run/kiwoom_investor_2020_2021/`
- 사업 데이터 오류 핸드오프 CSV:
  - `/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/`

## 즉시 수정/수집한 내용

### 1. KIS OHLCV 수집기 버그 수정

- 파일: `/Applications/stock_dashboard/collect_kis_ohlcv.py`
- 원인: `stock_universe.market` 조건이 `유가증권/코스닥`만 허용해서 현재 DB의 `KOSPI/KOSDAQ` 종목 2693개를 0개로 인식했다.
- 조치:
  - `market IN ('유가증권','코스닥','KOSPI','KOSDAQ')`로 수정
  - 수집 검증 출력이 오늘 날짜만 보던 문제를 `end_str` 기준 검증일로 수정
- 검증:
  - `20260619`, 50종목 샘플에서 신규 307건 / 갱신 1193건 확인

### 2. 공공데이터 최근 백필 실행

- 명령:
  - `public_data_collector.py --backfill --start 20260609 --end 20260620`
- 결과:
  - `stock_price_daily`: 828,073행 → 842,087행
  - `short_sell_daily`: 최신 구간 대차 데이터 재수집
- 확인된 문제:
  - 공공데이터 투자자/외국인보유 엔드포인트가 404 응답
  - 해당 영역은 Kiwoom 테이블이 최신성을 보완 중

### 3. 프로그램 매매 수집

- 파일:
  - `/Applications/stock_dashboard/scripts/collect_broker_program_trading.py`
- 조치:
  - 범위 수집 `--start/--end`
  - 전체 종목 `--all-stocks`
  - 이어받기 `--skip-existing`
  - 반환된 여러 일자 저장 `--save-all-returned`
  - 소스별 시장 원본 테이블 `broker_program_market_daily` 추가
- 현재 결과:
  - `broker_program_market_daily`: 4004행, 2020-01-01~2026-06-19
  - `broker_program_stock_daily`: 52,801행, 2640종목, 2026-05-21~2026-06-19
- 실행 중:
  - `com.stock-dashboard.programtrading2020backfill`
  - `com.stock-dashboard.programtradingstocks`

### 4. Kiwoom 보강 phase2 실행 중

- label:
  - `com.stock-dashboard.page-data-remediation-phase2`
- 현재 단계:
  - `kiwoom_stock_universe limit=2693`
- 이미 확인된 Kiwoom 테이블:
  - `kiwoom_credit_balance`: 3,496,880행, 2198종목, 2019-02-18~2026-06-17
  - `kiwoom_foreign_flow`: 129,711행, 2198종목, 2026-03-12~2026-06-19

### 5. Kiwoom 수급 2020~현재 백필 실행 중

- 파일:
  - `/Applications/stock_dashboard/scripts/backfill_kiwoom_investor_2020_2021.py`
  - `/Applications/stock_dashboard/run/com.stock-dashboard.kiwoom-investor-2020-2021.plist`
- label:
  - `com.stock-dashboard.kiwoom-investor-2020-2021`
- 방식:
  - Kiwoom `ka10059`를 `base_dt=20260619`, `max_pages=20`으로 호출
  - 종목별 약 2,000거래일을 내려받아 2020~현재 구간의 `kiwoom_investor_daily`와 `investor_trading_daily`를 동시 보강
  - 1종목 샘플에서 `2020-01-02`까지 저장 확인
- 시작 직후 확인:
  - 대상 2645종목, 이어받기 기준 todo 2644종목
  - `investor_trading_daily` 2020년 데이터가 0건에서 496건/2종목까지 증가 확인
  - 진행 로그: `/Applications/stock_dashboard/run/kiwoom_investor_2020_2021/launchd.log`
- 23:15 KST 확인:
  - `investor_trading_daily`: 738,571행, 2206종목, 2020-01-02~2026-06-19
  - 2020년: 13,392행/54종목
  - 2021년: 13,486행/56종목
  - 2022년: 14,004행/57종목
  - 2023년: 13,965행/57종목
  - 2024년: 13,908행/57종목
  - 2025년: 429,397행/2199종목
  - 2026년: 240,419행/2206종목

## 재감사 결과: 남은 이슈

### 실제 수집 또는 실행 완료 대기 필요

1. `stock_universe`
   - 상태: stale
   - 기간: 2026-03-26~2026-03-27
   - 조치: Kiwoom phase2에서 ka10001 전종목 갱신 중
   - Claude 확인: `base_date` 자체는 KRX 기준일이라 stale로 보일 수 있음. `updated_at/base_info_updated_at`와 실제 밸류 필드 갱신 여부를 별도 판정해야 함.

2. `dart_material_purchase`
   - 상태: 2652행, 2021~2024, 1072종목
   - 현재 DART 2020~2026 백필 실행 중이므로 완료 후 재감사 필요
   - Claude 확인: 2020년/2025년/2026년 누락 여부 및 제조업 우선 커버리지 확인.

3. `dart_employee_count`
   - 상태: 1247행, 2020~2024, 저용량
   - 조치: 감사식의 `acmtn_dscd NULL` 오탐은 수정 완료, 실제 중복은 없음
   - Claude 확인: 임직원 수집 범위를 2020~2026 전체 상장사로 확대할지, 제조업/텐버거 후보 우선으로 확대할지 확정.

4. `radar_price_cache`
   - 상태: 2026-05-07 이후 stale
   - 조치: phase2에서 market_radar refresh-cache 실행 예정

### 감사에서 정상 커버리지로 반영된 항목

1. `stock_price_daily`
   - `market_cap` 결측은 `price_history` + `stock_universe`로 보완 가능.
2. `investor_trading_daily` / `foreign_holding_daily`
   - 공공데이터 미신청/지연 구간은 `kiwoom_investor_daily`, `kiwoom_foreign_flow`로 보완.
3. `short_sell_daily.borrow_bal_amt`
   - 종목별 API의 잔고금액 결측은 `short_rank_daily.lnb_bal`로 보완.
4. `financial_data` / `cash_flow_data`
   - raw 중복 grain은 `canonical_financial_data`, `canonical_cashflow_data`에서 해소.
5. `consensus_targets`
   - `report_idx`가 없는 중복 레코드 534건 삭제 완료.
   - 삭제 전 중복 묶음은 `consensus_targets_duplicate_backup_20260620`에 보존.

## 사업 데이터 파서 감사 핵심 결과

파일 위치:
- `/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/`

요약:
- `material_annual_outliers`: 0
- `cost_structure_ratio_mismatch`: 13
- `dart_cost_quarterly_bad_values`: 27
- `dart_cost_quarterly_bad_context`: 34
- `dart_backlog_quarterly_bad_parse`: 900
- `order_backlog_bad_values`: 314
- `segment_revenue_fake_is_accounts`: 0
- `segment_revenue_breakdown_rows`: 12
- `segment_revenue_consolidated_mismatch`: 5067
- `raw_table_usage_in_code`: 113

Claude 우선 검증:
1. `dart_backlog_quarterly_bad_parse.csv`
   - 날짜/기간 숫자를 수주금액으로 오인한 케이스가 많다.
   - 예: `2026.1.1` 같은 날짜가 원 단위 금액처럼 파싱됨.
2. `order_backlog_bad_values.csv`
   - `backlog_normalized`가 1억원 미만으로 들어간 행 다수.
   - 수주잔고 신호에는 제외하거나 재파싱 필요.
3. `segment_revenue_consolidated_mismatch.csv`
   - 중국/외국계 `900xxx` 중심 단위 불일치 가능성이 큼.
   - `segment_revenue` 단위는 백만원인데 `financial_data`는 원 단위라 비교 로직/저장 단위 재확인 필요.
4. `raw_table_usage_in_code.csv`
   - 화면/API가 raw table을 직접 쓰는 지점이 있다.
   - 가능하면 `v_*_clean` 또는 canonical 테이블로 라우팅 필요.

## Claude에게 요청할 다음 작업

1. 전체 페이지 API가 raw table이 아니라 canonical/clean/fallback 테이블을 쓰는지 확인.
2. `market-indicators` 페이지:
   - 투자자/외국인지분은 공공데이터 실패 시 Kiwoom 테이블 fallback 사용.
   - 대차 금액 컬럼 매핑 재점검.
3. `tenbagger` 페이지:
   - 프로그램 매매 신호를 `broker_program_market_daily`, `broker_program_stock_daily`에서 읽도록 연결.
   - 매입재료비/수주잔고/세그먼트는 오류 CSV의 flagged row 제외 또는 confidence filter 적용.
4. `financial-table/cashflow` API:
   - `financial_data`, `cash_flow_data` 직접 사용 지점은 canonical 우선으로 변경.
5. phase2 완료 후 `/Applications/stock_dashboard/scripts/audit_all_page_data_quality.py` 재실행.
6. DART 2020~2026 백필 완료 후 `dart_material_purchase`, `dart_employee_count` 커버리지 재확인.

## 상태 확인 명령

```bash
launchctl print gui/$(id -u)/com.stock-dashboard.page-data-remediation-phase2
launchctl print gui/$(id -u)/com.stock-dashboard.programtrading2020backfill
launchctl print gui/$(id -u)/com.stock-dashboard.programtradingstocks
launchctl print gui/$(id -u)/com.stock-dashboard.dart2020backfill

tail -f /Applications/stock_dashboard/run/page_data_remediation_20260620/phase2.log
tail -f /Applications/stock_dashboard/run/program_trading_backfill_20260620/market_2020_20260620.launchd.log
tail -f /Applications/stock_dashboard/run/program_trading_backfill_20260620/stocks_latest_kiwoom.launchd.log

/Applications/stock_dashboard/venv/bin/python /Applications/stock_dashboard/scripts/audit_all_page_data_quality.py
```

## 2026-06-21 재확인 및 추가 보강

### 최신 전체 감사

- 실행 시각: 2026-06-21 17:20 KST
- 최신 결과: OK 25 / 수집필요 2 / 검토필요 0 / 누락 0
- 감사 파일:
  - `/Applications/stock_dashboard/research_outputs/all_page_data_quality_20260620.md`
  - `/Applications/stock_dashboard/research_outputs/all_page_data_quality_20260620.json`

### 완료 확인

1. Kiwoom 수급 2020~현재
   - `com.stock-dashboard.kiwoom-investor-2020-2021` 종료, exit code 0
   - 저장: 4,478,431행
   - 2020~2021 window: 1,051,452행 / 2,213종목 / 2020-01-02~2021-12-30
2. Kiwoom 종목정보/신용/외국인지분/마켓레이더 phase2
   - `com.stock-dashboard.page-data-remediation-phase2` 종료, exit code 0
   - 종목정보: 2,693종목 갱신 성공
   - 신용잔고: 3,450,513행 신규/갱신
   - Kiwoom 외국인 흐름: 107,150행 신규/갱신
   - 마켓레이더 refresh 성공
3. 마켓 레이더 해외 가격
   - 운영 소스는 `radar_price_cache`가 아니라 `/Applications/us_market_dashboard/us_market.db`의 `us_price_history`
   - 확인값: 653,046행 / 680종목 / 2021-05-03~2026-06-19
   - 감사 스크립트도 이 fallback을 정상 커버리지로 반영하도록 수정.
4. BigQuery 일일 동기화 보강
   - `bigquery_sync.py` daily full-refresh 목록에 아래 핵심 테이블 추가:
     - `broker_program_market_daily`, `broker_program_stock_daily`
     - `segment_revenue`
     - `dart_employee_count`
     - `dart_cost_quarterly`, `dart_inventory_quarterly`, `dart_bs_items`, `dart_sga_annual`

### 남은 수집필요 2개

1. `dart_material_purchase`
   - 현재: 2,652행 / 1,072종목 / 2021~2024
   - 원인: 2020~2026 백필 중 `corpCode.xml` 캐시가 없어 대상 1,257개가 전부 `no_corp`로 종료.
   - 조치: `collectors/dart_material_purchase_collector.py`가 `corpCode.xml`을 자동 다운로드하도록 수정.
2. `dart_employee_count`
   - 현재: 1,247행 / 1,009종목 / 2020~2024
   - 조치: `scripts/collect_dart_ch_extra.py` 수집 범위를 2020~2026으로 확대.

### 추가 자동 보강 작업

- 새 스크립트:
  - `/Applications/stock_dashboard/scripts/run_dart_missing_business_retry_20260621.sh`
- 새 launchd:
  - `com.stock-dashboard.dart-missing-business-retry`
  - `/Applications/stock_dashboard/run/com.stock-dashboard.dart-missing-business-retry.plist`
- 동작:
  - 30분마다 기존 `com.stock-dashboard.dart2020backfill`이 끝났는지 확인
  - 기존 DART 백필이 실행 중이면 즉시 대기 종료
  - 종료 후 `dart_material_purchase`와 `dart_employee_count` 보강을 한 번 실행하고 최종 감사를 재실행
- 현재 상태:
  - 등록 완료, 아직 실행 전
  - 현재 DART 2020 백필이 실행 중이라 보강 작업은 대기 상태

## 2026-06-21 매입재료비/수주잔고 추가 점검 및 수집 대기열 보강

### 현재 커버리지

1. `dart_material_purchase`
   - 2,652행 / 1,072종목 / 2021~2024
   - 연도별:
     - 2021: 1행 / 1종목
     - 2022: 828행 / 828종목
     - 2023: 899행 / 899종목
     - 2024: 924행 / 924종목
   - 2020, 2025, 2026은 사실상 비어 있음.
2. `order_backlog`
   - 6,774행 / 855종목 / 2016~2026
   - 2020: 248행 / 71종목
   - 2021: 279행 / 80종목
   - 2022: 760행 / 465종목
   - 2023: 1,580행 / 453종목
   - 2024: 1,782행 / 504종목
   - 2025: 1,998행 / 570종목
   - 2026: 122행 / 122종목
3. `dart_backlog_quarterly`
   - 1,507행 / 287종목 / 2016~2026
   - 2020: 1행 / 1종목
   - 2021: 10행 / 10종목
   - 2022: 204행 / 95종목
   - 2023: 279행 / 104종목
   - 2024: 315행 / 128종목
   - 2025: 531행 / 200종목
   - 2026: 163행 / 163종목

### 원인

- 매입재료비는 기존 수집 대상이 특정 섹터와 시가총액 500억~10조 구간으로 좁혀져 있었다.
- 2026-06-21 새벽 DART 백필의 매입재료비 단계는 `/tmp/CORPCODE.xml` 누락으로 `no_corp=1257` 상태로 바로 종료됐다.
- 로컬에는 DART 원문 전문 캐시가 충분하지 않다.
  - `dart_disclosure_cache`: 20행
  - `dart_raw_accounts`: 112행
  - 따라서 대량 보강은 DART `corpCode/list/document` API 재호출이 필요하다.
- 현재 2020~2026 DART 장기 백필은 `collect_dart_segment_breakdown.py` 단계에서 API 한도 초과를 반복하고 있었다.

### Codex 조치

1. `collectors/dart_material_purchase_collector.py`
   - `corpCode.xml`이 없으면 DART API로 자동 다운로드하도록 수정.
   - 수집 대상을 제조/소재 일부 섹터에서 국내 보통주 전체 중심으로 확대.
   - 제조 관련 섹터를 우선 처리하되, 섹터 누락 종목도 후순위로 포함.
   - SPAC/ETF/ETN/리츠/우선주성 이름은 제외.
2. `scripts/run_dart_missing_business_retry_20260621.sh`
   - 수주잔고 재수집 단계를 추가:
     - `python -m collectors.dart_backlog_collector --year-from 2020 --year-to 2026 --limit 100000`
   - 매입재료비 2020~2026 재수집 단계 유지:
     - `python -m collectors.dart_material_purchase_collector --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000`
   - DART 한도 소진 후 즉시 재실패하지 않도록 `.wait_until_epoch` 대기 장치 추가.
3. 현재 한도 초과 루프 정리
   - `com.stock-dashboard.dart2020backfill` PID 2230 종료.
   - 종료 사유: segment breakdown 단계에서 DART API 한도 초과만 반복하며 다음 보강 작업을 막고 있었음.
4. 재시도 에이전트 확인
   - `com.stock-dashboard.dart-missing-business-retry` kickstart 완료.
   - 현재 정상 메시지:
     - `DART API quota wait is active until Mon Jun 22 00:10:00 KST 2026`
   - 2026-06-22 00:10 KST 이후 30분 주기로 자동 실행 예정.

### Claude 검증 포인트

1. 2026-06-22 00:10 KST 이후 아래 로그 확인:
   - `/Applications/stock_dashboard/run/dart_missing_business_retry_20260621/summary.log`
   - `/Applications/stock_dashboard/run/dart_missing_business_retry_20260621/01_backlog_2020_2026_retry.log`
   - `/Applications/stock_dashboard/run/dart_missing_business_retry_20260621/02_material_purchase_2020_2026_retry.log`
   - `/Applications/stock_dashboard/run/dart_missing_business_retry_20260621/04_final_page_data_audit.log`
2. 재수집 완료 후 아래 쿼리로 커버리지 비교:

```sql
SELECT year, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks
FROM dart_material_purchase
GROUP BY year
ORDER BY year;

SELECT year, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks
FROM order_backlog
GROUP BY year
ORDER BY year;

SELECT fiscal_year, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks
FROM dart_backlog_quarterly
GROUP BY fiscal_year
ORDER BY fiscal_year;
```

3. 수주잔고는 값이 들어와도 파싱 오류 가능성이 큰 테이블이다.
   - `backlog_confidence >= 0.8`
   - `backlog_amount_krw >= 100000000`
   - 날짜/기간 숫자 오파싱 CSV 제외
   - 위 조건을 적용한 clean view를 매수 시그널에 우선 연결해야 한다.

## 2026-06-21 KIS/Kiwoom 대체 수집 가능성 점검

### 결론

- `매입재료비`와 `수주잔고`는 KIS/Kiwoom에서 직접 제공하는 정형 필드로 확인되지 않았다.
- 두 값은 사업보고서/분반기보고서 본문 또는 주석성 테이블에 들어가는 항목이라, 원천 수집은 DART 정기보고서 원문 파싱을 유지해야 한다.
- KIS는 보조 재무 데이터로 활용할 수 있는 API가 있다.
  - `국내주식 손익계산서`: `/uapi/domestic-stock/v1/finance/income-statement`, `FHKST66430200`
  - `국내주식 대차대조표`: `/uapi/domestic-stock/v1/finance/balance-sheet`, `FHKST66430100`
  - `국내주식 재무비율`: `/uapi/domestic-stock/v1/finance/financial-ratio`, `FHKST66430300`
  - `국내주식 수익성비율`: `/uapi/domestic-stock/v1/finance/profit-ratio`, `FHKST66430400`
  - `국내주식 성장성비율`: `/uapi/domestic-stock/v1/finance/growth-ratio`, `FHKST66430800`
  - `종합 시황/공시(제목)`: `/uapi/domestic-stock/v1/quotations/news-title`, `FHKST01011800`
- KIS 손익계산서에는 `sale_cost`(매출원가)가 있으나, 이는 `매입재료비`가 아니다.
  - 매출원가는 제품/상품/재공품/노무비/제조경비 등을 포함할 수 있어 원재료 매입액 급증 신호의 대체값으로 쓰면 오탐 가능성이 크다.
  - 다만 DART 매입재료비 파싱 실패 종목의 보조 검증 지표로는 사용할 수 있다.
- KIS 공시 제목 API는 제목/분류 중심이라 수주잔고 금액이나 원재료 매입액을 제공하지 않는다.
- 현재 로컬 Kiwoom 연동은 `ka10001`, `ka10008`, `ka10013`, `ka10059`, `ka90010`, `ka90013` 중심이다.
  - 종목 기본정보, 외국인, 신용, 투자자 수급, 프로그램 매매는 수집 가능.
  - 매입재료비/수주잔고 원문 수집 대체 소스로는 부적합.

### 후속 권장

1. KIS 재무 API는 별도 보조 테이블로 수집 가능:
   - `kis_income_statement`
   - `kis_balance_sheet`
   - `kis_financial_ratio`
2. `sale_cost`는 `material_purchase_krw` 대체값으로 직접 합치지 말고, 아래처럼 보조 feature로만 사용:
   - `sales_cost_yoy`
   - `gross_margin_change`
   - `material_purchase_missing_but_sales_cost_surge`
3. 매입재료비/수주잔고의 최종 원천은 계속 DART:
   - `dart_material_purchase`
   - `order_backlog`
   - `dart_backlog_quarterly`
