# Codex Handoff — Local-first Research + Risk Data Quality (2026-07-29)

## Context
Claude 의견을 반영해 BigQuery를 1순위로 두지 않고, 현재 규모에서는 SQLite+pandas 기반 검증/백테스트를 우선 강화했다. BigQuery는 전종목×전기간×수백 팩터 대규모 그리드서치가 필요할 때만 수동 연구용으로 남긴다.

## Completed

### 1. BigQuery daily refresh disabled / local research cycle added
- `scheduler.py`에서 BigQuery 자동 실행은 env gate로 비활성화되어 있음.
  - `ENABLE_BIGQUERY_DAILY=1`일 때만 BigQuery daily sync.
  - `ENABLE_BQ_TRIPLE_PIPELINE=1`, `ENABLE_BQ_MORNING_ALERT=1`도 명시 opt-in.
- 서버 재시작 로그 확인:
  - `[BigQuery동기화] 자동 실행 비활성화`
  - `[BQ아침알림] 자동 실행 비활성화`
- 신규 로컬 연구 사이클:
  - `scripts/run_local_research_cycle.py`
  - BigQuery 호출 없이 아래 검증을 순차 실행.
  - 실행 결과: `research_outputs/local_research_cycle_20260729.md`

실행 결과:

|step|ok|elapsed_sec|
|---|---:|---:|
|dilution_quality|True|27.93|
|segment_dilution_audit|True|0.06|
|macro_indicator_backtest|True|0.55|
|trigger_discovery_build|True|107.56|
|trigger_discovery_insights|True|6.23|
|external_provider_sample_audit|True|0.06|

### 2. Segment/product exposure coverage re-audited
- `scripts/audit_segment_dilution_coverage.py` 보강.
- 출력:
  - `research_outputs/segment_dilution_coverage_20260729.md`
  - `research_outputs/segment_dilution_coverage_20260729.json`

핵심 판정:
- `segment_revenue`의 “어떤 데이터라도 있는 종목”은 2,561종목으로 95% 수준.
- 하지만 대부분은 `연결전체` 행이다.
- 실제 제품/사업부/지역 breakdown이 있는 종목은 319종목, 전체 2,608종목 기준 12.23%.
- explicit `revenue_pct`가 있는 행은 486행.

따라서 “95% 커버”는 프론트/전략에서 그대로 노출하면 안 되고, 제품 노출도 기반 신호에는 `breakdown coverage`를 별도로 써야 한다.

### 3. Dilution/mezzanine quality classification added
- 신규 스크립트:
  - `scripts/classify_dilution_event_quality.py`
- `dilution_events`에 품질 분류 컬럼 추가:
  - `risk_amount_status`
  - `risk_event_bucket`
  - `risk_use_note`
  - `risk_classified_at`
- 인덱스 추가:
  - `idx_de_risk_bucket`
  - `idx_de_amount_status`
- 출력:
  - `research_outputs/dilution_event_quality_20260729.md`
  - `research_outputs/dilution_event_quality_20260729.json`

분류 결과:

|status|rows|usage|
|---|---:|---|
|amount_confirmed|12,065|금액 기반 리스크/시총대비 조달규모/풋옵션 현금부족 계산 가능|
|not_amount_applicable|3,512|무상증자, 결과/청약, 만기전취득, 자기사채, 소각, 가격조정 등 금액 결측이 정상|
|amount_missing_event_usable|2,235|건수/희석률/경고 플래그만 사용, 금액 계산 제외|

이제 `dart_disclosure_parse`의 금액 0% 문제는 전부 “수집 실패”로 보면 안 된다. 상당수는 비조달/중복/가격조정/만기전취득 계열이라 금액 결측이 정상일 수 있다.

### 4. Risk logic patched to use dilution quality
수정 파일:
- `routes/kis_trading.py`
  - `_gate_dilution_risk`에서 `risk_amount_status='not_amount_applicable'` 제외.
  - 사유 문구를 `최근 1년 실질 희석 이벤트 N건`으로 변경.
- `routes/tenbagger.py`
  - `dilution_risk_count_1y`, `dilution_risk_count_3y` 추가.
  - `dilution_events` 응답에 `risk_amount_status`, `risk_event_bucket`, `risk_use_note` 추가.
  - turnaround/tenbagger dilution map도 `not_amount_applicable` 제외.
- `scripts/research_strategy_overlay_expansion.py`
  - 금액 기반 리스크 플래그는 `risk_amount_status='amount_confirmed'`만 사용.

검증:
- `py_compile` 통과.
- 서버 재시작 후 API 확인:
  - `/api/tenbagger/stock-insight/082270`
  - `dilution_count_1y=14`, `dilution_risk_count_1y=13`
  - `dilution_count_3y=25`, `dilution_risk_count_3y=24`
  - 개별 이벤트에 `risk_amount_status`, `risk_event_bucket`, `risk_use_note` 반환 확인.

### 5. External/free API providers kept behind sample validation
- 신규 스크립트:
  - `scripts/audit_external_provider_samples.py`
- 출력:
  - `research_outputs/external_provider_sample_audit_20260729.md`
  - `research_outputs/external_provider_sample_audit_20260729.json`
- FMP/Finnhub/TwelveData/AlphaVantage API key는 현재 감지되지 않아 호출하지 않음.
- KoreanTickers/FnSpace류 유료 도입은 실제 소형주 샘플 커버리지 확인 전 보류.

로컬 analyst PDF coverage:

|bucket|stocks|covered|coverage|
|---|---:|---:|---:|
|1조원+|67|37|55.22%|
|3000억~1조|75|27|36.00%|
|1000억~3000억|152|34|22.37%|
|500억~1000억|153|33|21.57%|
|500억 미만|2,161|109|5.04%|

숨은 진주 후보군인 500억 미만은 로컬 커버도 5.04%에 불과하므로, 유료 API도 이 구간 샘플 10~30종목의 실제 컨센서스/세그먼트/목표가 제공 여부를 확인한 뒤 연결해야 한다.

## Files Added/Changed By Codex
- `scripts/classify_dilution_event_quality.py`
- `scripts/run_local_research_cycle.py`
- `scripts/audit_external_provider_samples.py`
- `scripts/audit_segment_dilution_coverage.py`
- `routes/kis_trading.py`
- `routes/tenbagger.py`
- `scripts/research_strategy_overlay_expansion.py`
- `research_outputs/dilution_event_quality_20260729.*`
- `research_outputs/segment_dilution_coverage_20260729.*`
- `research_outputs/external_provider_sample_audit_20260729.*`
- `research_outputs/local_research_cycle_20260729.*`
- `research_outputs/trigger_discovery_insights_20260729.*`

## Claude Verification Checklist
1. `risk_amount_status='not_amount_applicable'`가 자동매매/텐버거 리스크 점수에서 제외되는지 재검증.
2. `amount_missing_event_usable`은 금액 계산에는 쓰지 않고, 이벤트 빈도/희석률/경고 플래그에만 쓰는지 확인.
3. 세그먼트 노출도 UI/전략 문구에서 “95% 커버” 대신 “breakdown coverage 12.23%”를 별도 표기하도록 검토.
4. `segment_revenue` 상위 미커버 종목(SK하이닉스, 삼성바이오로직스, 삼성생명, KB금융 등)은 연결전체가 빠진 것인지, 사업부 breakdown이 없는 것인지 분리 확인.
5. 외부 API 도입 전 500억 미만/500~1000억/1000~3000억 샘플 종목의 실제 coverage를 먼저 확인.

## Recommended Next Work
1. `segment_revenue`의 `연결전체`와 진짜 사업부/제품/지역 세그먼트를 UI와 전략에서 명확히 분리한다.
2. 제품 매출 비중이 없는 지표-종목 매핑은 “약한 연결”로 낮은 가중치를 준다.
3. 희석 리스크 프론트엔드에는 총 이벤트 수와 실질 리스크 이벤트 수를 동시에 표시한다.
4. 자동매매 매수 차단에는 `amount_confirmed` 중심, 경고/감점에는 `amount_missing_event_usable`까지 포함한다.
5. BigQuery는 매일 refresh하지 말고 월 1~2회 대형 factor search 또는 사용자가 명시 요청한 연구에만 실행한다.
