# Codex Handoff — Frontend Signal/Data Integrity Audit (2026-07-17)

## 목적

사용자가 지적한 섹터 지표/반도체 화면처럼, 프론트엔드에 표시되는 신호가 실제 최근 가격/수급 상황과 어긋나는 문제가 다른 페이지에도 있는지 점검하고 수정했다.

핵심 결론:

- 신호 계산 오류는 재감사 기준 0건.
- 남은 경고는 `price_history`에 존재하는 부분 적재 날짜 4건이다.
- 랭킹/섹터/RS/신호 API는 완전 적재 가격 기준일을 사용하도록 방어했다.
- 수집 보강 필요 데이터는 2건: NPS 월별, DART 임직원.

## 수정 파일

이번 Codex 수정 범위:

- `routes/sector_rotation.py`
  - 이미 직전 작업에서 수정됨.
  - 최신 가격 기준일을 전 종목 적재일로 제한.
  - 4주/12주 RS가 약한 섹터는 `ENTRY_NOW`로 표시하지 않도록 차단.
  - 반도체는 현재 `EARLY_WATCH / WATCH`로 내려감.

- `routes/market_radar.py`
  - 이미 직전 작업에서 수정됨.
  - 반도체 밸류체인 가격 기준을 최신 완전 적재일로 고정.
  - 실시간 가격은 검증된 Kiwoom overlay만 반영.

- `collectors/kiwoom_collector.py`
  - 이미 직전 작업에서 수정됨.
  - Kiwoom realtime quote FID 파싱 오류 수정.
  - 0 값 메시지가 유효한 현재가를 덮어쓰지 않도록 방어.

- `scripts/repair_kiwoom_realtime_quote_from_raw.py`
  - 이미 직전 작업에서 추가됨.
  - raw Kiwoom quote에서 `kiwoom_realtime_quote` 보정.

- `routes/tenbagger.py`
  - 이번 작업에서 수정.
  - `risk_adjusted_score`, `price_risk_label`, `price_return_1m`, `price_return_3m`, `price_risk_flags` 추가.
  - 최근 1개월/3개월 급락 후보가 원점수만으로 상단에 보이지 않도록 위험보정점수 기준 정렬.
  - `screener-v2`, `screener-v3`, 최신 결과, 회차 상세에 동일 가격위험 보정 적용.

- `routes/market_indicators.py`
  - 이번 작업에서 수정.
  - `_latest_trade_date()`가 부분 적재일을 잡지 않도록 `COUNT(DISTINCT stock_code) >= 2000` 기준으로 변경.
  - `get_turnover_top()`, `get_turnover_breakout_signals()`에 FastAPI `Query` 객체 직접 호출 방어 추가.

- `routes/stock_analysis_rs.py`
  - 이번 작업에서 수정.
  - RS/52주 고점 대시보드 계산 기준일을 최신 완전 적재일로 고정.
  - `_fetch_recent_prices()`와 benchmark series 조회에 `as_of` 필터 추가.
  - 메타데이터 `target_date`도 완전 적재 기준일을 표시.

- `scripts/audit_signal_page_logic.py`
  - 이번 작업에서 추가/수정.
  - 섹터 로테이션, 거래대금 돌파, 텐버거, RS 캐시의 신호 논리 오류를 감사.
  - 텐버거는 위험보정점수 기준으로 재검증.

- `frontend/src/App.jsx`
  - 이번 작업에서 수정.
  - 텐버거 카드에 위험보정점수/원점수/가격위험 라벨 표시.

- `frontend/src/views/TenbaggerProjectView.jsx`
  - 이번 작업에서 수정.
  - 텐버거 프로젝트 후보 표와 스크리너 표에 위험보정점수와 가격위험 표시.

## 검증 결과

실행 완료:

```bash
cd /Applications/stock_dashboard
venv/bin/python -m py_compile routes/stock_analysis_rs.py routes/market_indicators.py routes/tenbagger.py scripts/audit_signal_page_logic.py
venv/bin/python scripts/audit_signal_page_logic.py
venv/bin/python scripts/audit_all_page_data_quality.py
cd /Applications/stock_dashboard/frontend && npm run build
cd /Applications/stock_dashboard && git diff --check
./stop.sh && ./start.sh
```

결과:

- `py_compile`: 통과
- `npm run build`: 통과
- `git diff --check`: 통과
- 서버 재시작: 성공
  - backend: `http://127.0.0.1:8000`
  - frontend: `http://127.0.0.1:5173`

## API 스모크 테스트 결과

### 섹터 로테이션

```text
GET /api/sector-rotation/leadership?months=36&top_n=3
as_of: 2026-07-16
summary: {'entry_now': 0, 'watch': 7, 'leading': 0, 'sectors': 10}

반도체: EARLY_WATCH / WATCH / rs4w -1.4 / rs12w -5.7
기판패키지: EARLY_WATCH / WATCH / rs4w -1.8 / rs12w -14.9
화장품/뷰티: EARLY_WATCH / WATCH / rs4w +32.9 / rs12w -13.4
```

### 텐버거

```text
GET /api/tenbagger/results?limit=5
run_time: 2026-07-17 12:00:08

091590 남화토건: total 83.0 / adjusted 83.0 / 가격 정상 / 3M +34.9
088130 동아엘텍: total 78.0 / adjusted 70.0 / 단기급락 확인 / 3M -33.9
008970 KBI동양철관: total 70.0 / adjusted 60.0 / 가격확인 필요 / 3M -43.6
015360 INVENI: total 57.0 / adjusted 57.0 / 가격 정상 / 3M -11.5
101360 에코앤드림: total 71.0 / adjusted 53.0 / 회피: 가격붕괴 / 3M -63.2
```

### 거래대금 돌파

```text
GET /api/market-indicators/turnover-breakout-signals?top_n=5
trade_date: 2026-07-16
candidates: 0
scan_pool_count: 200
tick_covered_count: 199
minute_covered_count: 199
avg20_volume_covered_count: 200
```

### RS/52주 고점

```text
GET /api/stock-analysis-rs/dashboard-data
success: true
target_date: 2026-07-16
count: 2607

GET /api/stock-analysis-rs/high52-data
success: true
target_date: 2026-07-16
count: 2693
new_high_count: 45
near_high_count: 75
```

## 감사 리포트

생성 파일:

- `research_outputs/signal_page_logic_audit_20260717.md`
- `research_outputs/signal_page_logic_audit_20260717.json`
- `research_outputs/all_page_data_quality_20260717.md`
- `research_outputs/all_page_data_quality_20260717.json`

신호 로직 감사 결과:

```text
total_findings: 4
critical: 0
high: 4
medium: 0
low: 0
```

남은 4건은 모두 `price_history`의 부분 적재 날짜 자체:

- `2026-07-17`: 202종목
- `2026-07-12`: 7종목
- `2026-07-11`: 1종목
- `2026-07-05`: 7종목

해석:

- 이 날짜들이 DB에 남아 있는 것은 맞다.
- 다만 이번 수정으로 섹터/랭킹/RS/텐버거 보정 API는 완전 적재일 또는 검증 overlay 기준을 사용하도록 방어했다.
- Claude는 추가로 다른 라우트의 다종목 랭킹 API가 `MAX(date)`를 직접 쓰는지 계속 점검하면 된다.

전체 데이터 품질 감사 결과:

```text
OK 29
수집필요 2
검토필요 0
누락 0
```

수집 필요:

- `nps_workplace_monthly`
  - 기간: `202504 ~ 202605`
  - 이슈: `stale:77d>75d`
  - 수집기: `employment_monitor.collect_nps_workplace`

- `dart_employee_count`
  - 기간: `2020 ~ 2025`
  - 이슈: `low_volume:1330<5000`
  - 수집기: DART employee collector

## Claude 검증 체크리스트

1. 프론트에서 섹터 지표 페이지 확인
   - 반도체가 `진입`으로 보이지 않고 `초기 관찰` 또는 `WATCH`로 표시되는지 확인.
   - 기준일이 `2026-07-16`인지 확인.

2. 텐버거 화면 확인
   - 점수 옆에 위험보정점수/원점수/가격위험 라벨이 보이는지 확인.
   - 에코앤드림이 `회피: 가격붕괴`로 보이는지 확인.
   - KBI동양철관이 `가격확인 필요`로 보이는지 확인.

3. 시장지표/거래대금 돌파 확인
   - `/api/market-indicators/turnover-breakout-signals`가 500 에러 없이 응답하는지 확인.
   - 기준일이 부분 적재일 `2026-07-17`이 아니라 `2026-07-16`인지 확인.

4. RS/52주 고점 확인
   - `/api/stock-analysis-rs/dashboard-data`
   - `/api/stock-analysis-rs/high52-data`
   - 두 API 모두 `metadata.target_date == 2026-07-16`인지 확인.

5. 추가 수집 필요
   - NPS 월별 최신화.
   - DART 임직원 데이터 확대.

## 주의사항

작업 트리에 Claude/사용자/기존 작업 변경이 매우 많이 섞여 있다. 이번 Codex 변경 외 파일을 되돌리지 말 것.

특히 `frontend/src/App.jsx`는 기존 대규모 변경이 포함되어 있어 diff가 매우 크다. 이번 Codex가 직접 수정한 부분은 텐버거 카드의 점수/가격위험 표시 영역이다.

## 추가 수집 보강 실행 결과 (2026-07-17 13:26~13:53)

사용자 요청으로 남은 수집 보강 2건을 즉시 실행했다.

### NPS 월별

실행:

```bash
cd /Applications/stock_dashboard
venv/bin/python employment_monitor/collect_nps_monthly.py --collect --limit 0
venv/bin/python scripts/sync_employment_nps_to_stock_db.py
```

결과:

- `employment_monitor.collect_nps_monthly`가 감지한 최신 공개월: `202605`
- `202606`은 현재 NPS API에서 아직 최신월로 제공되지 않음.
- `202605` 누락분 61건 추가 저장.
- `stock.db.nps_workplace_monthly` 동기화 완료.

보강 전후:

```text
before: 29,704 rows / 2,160 stocks / 202504~202605
after : 29,765 rows / 2,184 stocks / 202504~202605
```

감사상 `stale:77d>75d`는 남아 있다. 이는 감사 기준이 월초 기준으로 75일을 초과했기 때문이며, 수집 실패라기보다 NPS 공개 최신월이 `202605`인 현재 데이터 한계로 봐야 한다. Claude는 `audit_all_page_data_quality.py`의 NPS freshness 기준을 “자료 공개 지연 월간 데이터”에 맞게 월말/공개월 기준으로 재조정할지 검토할 것.

### DART 임직원

실행:

```bash
cd /Applications/stock_dashboard
venv/bin/python -m py_compile scripts/collect_dart_ch_data.py
venv/bin/python scripts/collect_dart_ch_data.py --limit 2200 --skip-existing --employee-only
```

수정:

- `scripts/collect_dart_ch_data.py`
  - DART `empSttus.json` 응답 `013`/`014`(자료 없음)을 API 한도소진처럼 처리하던 버그 수정.
  - 이제 자료 없음은 빈 `list`로 반환하고 다음 종목으로 계속 진행한다.

결과:

- 기존에는 자료 없음 종목 하나에서 “API 키 전체 소진”으로 오판하고 중단됨.
- 수정 후 2,200개 대상 끝까지 완료.
- 신규 직원수 저장 2건.

보강 전후:

```text
before: 1,330 rows / 1,010 stocks / 2020~2025
after : 1,332 rows / 1,011 stocks / 2020~2025
```

연도별 현황:

```text
2025: 83 rows
2024: 1,009 rows
2023: 206 rows
2022: 11 rows
2021: 15 rows
2020: 8 rows
```

감사상 `low_volume:1332<5000`은 남아 있다. 이번 실행 결과 미수집 종목 대부분은 DART 직원현황 자료가 없거나 정기보고서 형태가 맞지 않는 종목으로 보인다. Claude 후속 검증:

1. `--skip-existing` 없이 기존 2024 보유 종목의 2025 사업보고서/반기보고서 보강을 별도로 실행할 것.
2. `collect_employee()`가 사업보고서 `11011`만 성공하면 반기 `11012`를 스킵하는 로직이 의도에 맞는지 확인할 것.
3. 2025 coverage를 우선 늘리는 별도 스크립트 또는 `--year 2025 --missing-year-only` 옵션 추가 검토.
4. DART `empSttus` 미제공 종목은 감사 임계값 5,000행의 현실성을 재검토할 것.

### 재감사

실행:

```bash
venv/bin/python scripts/audit_all_page_data_quality.py
```

결과:

```text
total: 31
ok: 29
needs_collection: 2
unstable_or_needs_review: 0
missing: 0
```

남은 2건:

- `nps_workplace_monthly`: `29765`, `202504 ~ 202605`, `stale:77d>75d`
- `dart_employee_count`: `1332`, `2020 ~ 2025`, `low_volume:1332<5000`

## 2026-07-17 추가: 텐버거/후보군 가격위험 보정 확대 감사

사용자 요청: “텐버거 후보 가격위험 보정 위와 같은거 더 없나 확인”.

### 수정 파일

- `scripts/audit_signal_page_logic.py`
  - 후보/매수 신호 API들을 실제 HTTP 응답 기준으로 공통 감사하는 `audit_candidate_price_risk()` 추가.
  - 검사 기준:
    - 고점수 또는 매수성 신호.
    - 최근 1개월 `<= -20%` 또는 3개월 `<= -35%`.
    - `price_risk`, `price_risk_label`, `price_risk_penalty`, `risk_adjusted_score` 누락 여부.
  - 대상 API 예:
    - `/api/signals/*-candidates`, `/api/signals/v10/v11/v12`, `/api/signals/kiwoom-conditions`
    - `/api/trend/gc|rec|v18|turnover/recommendations`
    - `/api/tenbagger/custom-filter`, `undervalued-filter`, `turnaround-filter`, `recovery-candidates`, `action-signals`
    - `/api/dart-contracts/signals`, `/api/cafe-signals/stock-trade-signals`, `/api/buy-candidates`

- `routes/tenbagger.py`
  - `_enrich_tenbagger_price_risk()`가 `total_score`뿐 아니라 `score`, `combined_score`, `signal_score`도 기준 점수로 사용할 수 있게 확장.
  - `/api/tenbagger/recovery-candidates`
    - 가격위험 필드 추가.
    - `risk_adjusted_score` 우선 정렬.
  - `/api/tenbagger/action-signals`
    - 가격위험 필드 추가.
    - 가격위험 보정 후 기준 미달 또는 `AVOID`는 `buy_signal=false`, `buy_strength=관망`으로 강등.

- `routes/cafe_signals.py`
  - `/api/cafe-signals/stock-trade-signals`
    - 1개월/3개월 가격수익률과 가격위험 필드 추가.
    - 지표 신호는 매수 후보여도 가격위험이 `WATCH_PRICE`/`AVOID`이면 `관찰/yellow`로 강등.

### 검증 결과

실행:

```bash
venv/bin/python -m py_compile routes/tenbagger.py routes/cafe_signals.py scripts/audit_signal_page_logic.py
git diff --check
./stop.sh
./start.sh
venv/bin/python scripts/audit_signal_page_logic.py
```

결과:

```text
signal_page_logic_audit_20260717.md
critical: 0
high: 4
medium: 0
low: 4
```

해석:

- `high 4`: 기능 오류가 아니라 `price_history`의 부분 적재 파티션 경고.
  - 2026-07-17: 203종목
  - 2026-07-12: 7종목
  - 2026-07-11: 1종목
  - 2026-07-05: 7종목
  - 기존 주요 신호 API들은 `latest_full_price_date=2026-07-16` 기준 또는 검증된 realtime overlay로 보정되어 있음.
- `low 4`: 가격 급락 후보가 존재하지만 이미 가격위험 배지/감점이 붙은 관찰 항목.
  - 동아엘텍, KBI동양철관, 에코앤드림, 코나아이.

API 스모크:

```text
/api/tenbagger/action-signals?limit=10
buy_count 0
033340 좋은사람들: risk_adjusted_score 47, price_risk AVOID, buy_signal=false

/api/cafe-signals/stock-trade-signals?limit=100
082740 한화엔진: 관찰/yellow, 단기급락 확인
277810 레인보우로보틱스: 관찰/yellow, 가격확인 필요
039130 하나투어: 관찰/yellow, 단기급락 확인
```

Claude 후속 확인:

1. 프론트엔드에서 텐버거 `recovery-candidates`, `action-signals`, 카페/퀀트 종목 신호에 `price_risk_label`, `risk_adjusted_score`, `price_return_1m`, `price_return_3m`가 잘 표시되는지 확인.
2. `audit_signal_page_logic.py`의 `low` 항목은 오류가 아니라 보호장치 작동 확인용이다. 리포트 UI에서 “관찰” 섹션으로 분리하면 더 좋다.
3. 부분 적재 파티션은 삭제하지 말고, 각 API가 최신 완전 적재일을 쓰는지 계속 감사할 것.

### 2026-07-17 후속 추가 점검

사용자 후속 질문: “추가 오류나 개선 필요사항은 없어?”

추가 발견 및 수정:

- `routes/cafe_signals.py`
  - 카페/퀀트 종목 신호의 `risk_adjusted_score` 계산에서 원점수가 음수인 매도·위험 종목이 `0`으로 완화되는 오류 수정.
  - 수정 후 음수 점수 종목은 가격위험이 있으면 더 낮은 점수로 조정된다.
  - 예: 태광 `score -3.15`, `가격확인 필요`, `risk_adjusted_score -4.15`.

- `frontend/src/views/CafeSignalsView.jsx`
  - 종목 매수·매도 시그널 표에 `가격위험` 열 추가.
  - `risk_adjusted_score`, 원점수, `price_risk_label`, 1개월/3개월 수익률, 관찰 강등 사유 표시.
  - 종목 상세 패널에도 동일한 가격위험 배지 표시.

- `scripts/audit_signal_page_logic.py`
  - 다수 후보 API 연속 호출 시 냉시작/경합으로 생기던 허위 `endpoint_error`를 줄이기 위해 HTTP 감사 타임아웃을 25초에서 60초로 상향.

검증:

```bash
venv/bin/python -m py_compile routes/cafe_signals.py scripts/audit_signal_page_logic.py
cd frontend && npm run build
git diff --check
venv/bin/python scripts/audit_signal_page_logic.py
```

최종 감사 결과:

```text
critical: 0
high: 4   # 부분 적재 파티션 경고만 남음
medium: 0
low: 4    # 가격위험 보호장치 작동 확인용 관찰 항목
```

## 2026-07-17 추가: 퀀트 주요지표/EPIC 커버리지 점검

사용자 질문: “퀀트지표가 너무 적은거 같은데? 추가로 확대할거 없어? EPIC 사이트에 있는 주요 지표들은 모두 연결한거야?”

### 판정

- 화면이 적어 보였던 주된 이유는 `QuantMajorIndicatorsView.jsx`가 `ready_existing` 완료 지표를 기본 목록에서 제외하고 부분/미완료 지표만 보여주고 있었기 때문.
- DB 기준 실제 카탈로그:
  - 전체 확장 카탈로그: `180`개
  - 시계열 보유 지표: `177`개
  - 전체 시계열 행: `160,676`행
- API 화면 기준:
  - `/api/quant-major-indicators/catalog` 응답 `count`: `180`
  - 연결/부분연결/이벤트형 부분연결: `178`
  - 미연결/원천중단: `2`
- EPIC 계열:
  - EPIC 계열 카탈로그: `102`개
  - 연결 완료/부분연결: `101`개
  - 미연결: `1`개
  - 미연결 항목: `epic:19:104 싱가포르 석유제품 재고 추이 (주)`

### 수정 파일

- `frontend/src/views/QuantMajorIndicatorsView.jsx`
  - `지표별` 기본 화면에서 완료 지표까지 포함한 전체 카탈로그를 표시하도록 수정.
  - 상단 카드를 `전체`, `연결`, `부분`, `미연결` 기준으로 변경.
  - 분류표를 `분류별 미완료 지표 현황`에서 `분류별 전체 지표 현황`으로 변경하고 `연결완료` 컬럼 추가.
  - 상태 필터에서 `연결완료`도 선택 가능하게 변경.

- `scripts/ops/sync_quant_major_indicators.py`
  - 전체 동기화가 외부 원천/대용량 계산 하나 때문에 멈추지 않도록 기존 히스토리 재사용 보호 추가.
  - 추가 보호 대상:
    - KIA 미국 모델별 판매 `epic:0:57`
    - KRIC 철도 노선별 여객 `epic:7:36`
    - KAMA 자동차 판매/점유율/모델/글로벌 등록 지표 `epic:0:*`
    - 시장폭/거래량/52주 breadth `public:21:*`
    - 관세청 섹터 수출입 확장 `public:23:*`
    - 서울 지하철 이용량 `epic:22:9`, `epic:22:10`

### 남은 보강 후보

1. `epic:19:104 싱가포르 석유제품 재고 추이`
   - 현재 `Enterprise Singapore StatLink / 유료 원천 후보`로 분류.
   - 무료/공식 API 또는 대체 프록시가 필요.
   - 후보 프록시: EIA Singapore oil product stock이 있으면 우선, 없으면 Singapore refinery margin/ARA inventory/WTI-Brent crack spread 계열로 대체 검토.

2. `cafe:11:2645:regulation 중국/인도 배기가스 규제 이벤트`
   - 데이터 시계열이 아니라 정책 이벤트 성격.
   - `global_macro_events` 또는 별도 `policy_event_indicator_series`로 이벤트형 지표 모델을 추가하는 것이 적절.
   - 현재 화면 연결률에는 `partial_existing`으로 포함되지만, 시계열 행은 `0`개가 정상이다. 숫자 차이를 오류로 보지 말 것.

3. `cafe:11:2716 광고미디어 업종 지표`
   - KOBACO KAI가 2026년 사업 종료로 `source_discontinued`.
   - 대체 후보: 방송광고비/온라인광고 경기지표/광고대행사 실적/네이버·카카오 광고 매출 proxy.

### 검증

```bash
sqlite3 stock.db "SELECT status, COUNT(*) FROM quant_major_indicator_catalog GROUP BY status"
sqlite3 stock.db "SELECT COUNT(DISTINCT indicator_key), COUNT(*) FROM quant_major_indicator_series"
cd frontend && npm run build
git diff --check
```

결과:

```text
ready_existing          126
ready_existing_partial   46
partial_existing          6
source_discontinued       1
new_collector_needed      1

catalog_count: 180
series_keys: 177
series_rows: 160676
```

## 2026-07-17 추가: 글로벌 매크로/원자재 지표 역브릿지 확장

사용자 추가 요청: “더 추가하고 계속해”

### 확장 내용

- 기존 `global_macro_categories/global_macro_data`에 이미 수집되어 있으나 `quant_major_indicator_catalog`에 없던 매크로/원자재/시장 지표를 `macro:<indicator_code>` 형태로 퀀트 주요지표에 역브릿지.
- `MARKET_QUANT` 카테고리는 제외했다. 이유는 `collectors/market_quant_bridge_collector.py`가 이미 퀀트 → 글로벌 매크로 방향으로 보내는 지표라, 다시 가져오면 DRAM/HS Trade 지표가 중복된다.
- 신규 카테고리:
  - `epic_category_code=24`
  - 프론트 표시명: `글로벌 매크로/원자재`

### 적재 결과

```text
quant_major_indicator_catalog: 180 -> 277
macro:* 카탈로그: 97
macro:* 시계열: 22,653 rows
전체 연결/부분연결: 275 / 277
```

대표 신규 지표:

- `macro:KR_USD_KRW` 원/달러 환율
- `macro:US_HY_SPREAD` 미국 하이일드 스프레드
- `macro:US_10Y_BREAKEVEN` 미국 10년 기대인플레이션
- `macro:US_VIX` VIX 공포지수
- `macro:COMM_COPPER` 구리 가격
- `macro:COMM_OIL_WTI` WTI 원유 가격
- `macro:OIL_STOCKS_EX_SPR` 미국 원유재고(SPR 제외)
- `macro:GLOBAL_FOOD_PRICE` FAO 식품가격지수
- `macro:KR_CONSTRUCTION_ORDER` 건설수주액(실질)
- `macro:KR_INVENTORY_CYCLE` 재고순환지표

### 수정 파일

- `scripts/ops/sync_quant_major_indicators.py`
  - `collect_global_macro_quant_bridge()` 추가.
  - `macro:%` 시계열 재적재 흐름 추가.
  - 매일 동기화 시 `global_macro_data`의 독립 매크로 지표가 퀀트 주요지표로 자동 반영됨.

- `frontend/src/views/QuantMajorIndicatorsView.jsx`
  - 카테고리 24를 `글로벌 매크로/원자재`로 표시.

- `scripts/ops/quant_indicator_signal_engine.py`
  - 기존에는 `cafe_stock_indicator_mappings`가 있는 지표만 신호 계산.
  - `macro:%`는 종목 매핑이 없어도 레짐 신호로 계산하도록 확장.
  - 종목 매핑이 없는 매크로 신호 메시지는 “종목 매수 후보는 별도 노출도 매핑 확인 필요”로 표시. 텔레그램 매수 후보 발송은 기존처럼 검증된 종목 노출이 있을 때만 허용.

- `routes/cafe_signals.py`
  - `/api/cafe-signals/indicator-traffic-lights`가 `macro:%` 지표도 포함하도록 변경.

### 검증

```bash
python3 -m py_compile scripts/ops/sync_quant_major_indicators.py scripts/ops/quant_indicator_signal_engine.py routes/cafe_signals.py
cd frontend && npm run build
python3 scripts/ops/quant_indicator_signal_engine.py --limit-events 80
```

결과:

```text
global macro bridge: 97 indicators / 22,653 rows
signal engine: checked_pairs=333, signals=104, inserted=18, telegram_sent=0
catalog API: count=277, connected=275, macro=97
indicator traffic API: items=300, macro=97
```

### Claude 확인 포인트

1. `macro:*` 지표는 레짐/위험 신호로 먼저 쓰고, 종목 매수 후보로 승격하려면 `cafe_stock_indicator_mappings` 또는 별도 매크로→섹터/종목 노출도 매핑이 필요하다.
2. `series_direction_mode()`는 아직 간단한 문자열 규칙이다. 금리/재고/원가성 지표는 대체로 맞지만 원유·구리·환율처럼 업종별로 방향이 달라지는 지표는 별도 룰 테이블이 필요하다.
3. `macro:KR_KOSPI_KOSIS`와 `macro:KR_KOSPI`처럼 유사 지표가 존재한다. 둘 다 원천이 다르므로 유지했지만, 화면에서는 중복 후보로 표시될 수 있다.
4. 텔레그램 발송은 이번 실행에서 하지 않았다. `telegram_sent=0` 정상.

## 2026-07-17 추가: 매크로 지표 → 섹터/종목 후보 매핑 보강

사용자 후속 요청: “계속해”

### 목적

앞 단계에서 `macro:*` 지표 97개를 퀀트 주요지표에 추가했지만, 그 자체로는 “시장 레짐 신호”에 가까웠다. 이번 단계에서는 매크로 지표를 투자에 쓸 수 있게 `섹터 → 종목 후보`까지 연결했다.

### 수정 파일

- `scripts/ops/sync_cafe_quant_mappings.py`
  - `MACRO_SECTOR_TO_INDICATORS` 추가.
  - 예:
    - `macro:COMM_COPPER` → 전력기기, 철강/비철, 2차전지
    - `macro:KR_EXPORT`, `macro:KR_TRADE_BALANCE` → 반도체, 자동차, 조선/해운, 전력기기
    - `macro:KR_BASE_RATE`, `macro:KR_YIELD_SPREAD` → 금융, 은행, 보험, 증권, 리츠/부동산
    - `macro:GLOBAL_FOOD_PRICE`, `macro:COMM_WHEAT` → 음식료/소비재

- `scripts/ops/sync_cafe_stock_indicator_mappings.py`
  - `MACRO_STOCK_SECTOR_RULES` 추가.
  - StockEasy 섹터 멤버십을 이용해 매크로 민감 종목 후보를 자동 생성.
  - `mapping_status='candidate_macro_context'`
  - `importance_level='unknown_macro_sensitive'`
  - 매출/이익/원가 비중은 확정하지 않음.

### 적재 결과

```text
cafe_quant_indicator_mappings total: 104
macro sector mappings: 55

cafe_stock_indicator_mappings total: 1,169
macro stock candidate mappings: 486

macro signal events: 32
quant_stock_trade_signal_snapshots today: 10
```

대표 예시:

- `macro:COMM_COPPER 구리 가격`
  - 후보 섹터: 전력기기, 철강/비철, 2차전지, 음극재/소재
  - 후보 종목 예: 가온전선, 대한전선, LS, LS ELECTRIC, 산일전기, POSCO홀딩스, 고려아연, 삼성SDI, 포스코퓨처엠

- `macro:KR_TRADE_BALANCE 한국 무역수지`
  - 후보 섹터: 반도체, 자동차, 조선/해운, 전력기기, 정유/화학
  - 후보 종목 예: 삼성전자, SK하이닉스, 현대차, HD현대중공업

- `macro:KR_YIELD_SPREAD 장단기금리차`
  - 후보 섹터: 은행, 보험, 증권, 금융
  - 후보 종목 예: KB금융, 신한지주, 하나금융지주, 삼성생명, 삼성화재

### 안전장치

- 매크로 후보는 `candidate_macro_context`로만 저장한다.
- `routes/cafe_signals.py`의 `/stock-trade-signals`는 여전히 `confirmed_exposure`와 `confirmed_relationship`만 사용한다.
- 따라서 매크로 후보 486건은 화면의 참고 후보/맥락으로는 보이지만, 자동 매수/매도 점수에는 아직 들어가지 않는다.
- 현재 확인:

```text
stock-trade-signals macro: 0
counts: buy 3 / sell_risk 3 / watch 4
telegram_sent: 0
```

### 검증

```bash
python3 -m py_compile scripts/ops/sync_cafe_quant_mappings.py scripts/ops/sync_cafe_stock_indicator_mappings.py
python3 scripts/ops/sync_cafe_quant_mappings.py
python3 scripts/ops/sync_cafe_stock_indicator_mappings.py
python3 scripts/ops/quant_indicator_signal_engine.py --limit-events 120
venv/bin/python scripts/ops/snapshot_quant_stock_trade_signals.py
cd frontend && npm run build
```

API 확인:

```text
/api/cafe-signals/quant-mappings: items 104, macro 55
/api/cafe-signals/stock-indicator-mappings: macro 317 visible within first 1000 rows, DB total 486
/api/cafe-signals/quant-indicator-signals: first 30 중 macro 29
/api/cafe-signals/stock-trade-signals: macro 0, confirmed-only 유지
```

### 다음 개선 후보

1. `candidate_macro_context`를 바로 매수 신호에 넣지 말고, 백테스트로 검증된 조합만 `macro_confirmed_factor` 같은 별도 상태로 승격할 것.
2. 원자재/환율은 업종별 방향성이 다르므로 `indicator_direction_by_sector` 룰 테이블을 만드는 것이 필요하다.
   - 예: 구리 상승은 전선 판가에는 긍정, 원가 비중이 큰 제조업에는 부정일 수 있음.
3. 매크로 후보 종목은 현재 섹터 멤버십 기반이라 매출 비중이 없다. DART 세그먼트/원재료 테이블과 연결해 노출도를 확인한 뒤 확정 후보로 승격해야 한다.

## 2026-07-17 추가: 섹터별 지표 방향성 룰 적용

사용자 후속 요청: “계속해”

### 배경

이전 단계에서 매크로/원자재 지표를 섹터와 종목 후보에 연결했지만, 같은 지표라도 섹터별 해석이 다르다.

예:

- 구리 가격 상승
  - 전력기기/전선: 판가 상승 가능성 → 우호
  - 2차전지/소재: 원가 부담 가능성 → 부정
- 소맥/FAO 식품가격 상승
  - 음식료: 원가 부담 → 부정
- 금리 상승
  - 은행: NIM에는 우호 가능
  - 리츠/부동산: 할인율/조달비용 부담 → 부정

### 수정 파일

- `scripts/ops/sync_cafe_quant_mappings.py`
  - `MACRO_SECTOR_DIRECTION_RULES` 46개 추가.
  - `indicator_sector_direction_rules` 테이블 생성/갱신.

- `routes/cafe_signals.py`
  - `_sector_direction_rule()`, `_apply_sector_direction()`, `_signal_score()` 추가.
  - `/api/cafe-signals/sector-traffic-lights`에서 섹터별 방향성 룰을 적용해 점수와 신호등 재계산.
  - `/api/cafe-signals/stock-trade-signals`에도 향후 매크로 확정 승격 시 같은 방향성 룰이 적용되도록 훅 추가.

- `frontend/src/views/CafeSignalsView.jsx`
  - 섹터 상세 카드의 상위 지표에 `direction_note` 표시.
  - 사용자가 왜 초록/빨강/주의인지 바로 볼 수 있게 함.

### 적재/검증 결과

```text
indicator_sector_direction_rules: 46
frontend build: pass
python compile: pass
```

API 확인 예:

```text
음식료: red / -3.303
  macro:COMM_WHEAT red -3.303
  방향성: 소맥 가격 상승은 제분/제과/라면/사료 원가 부담

건설/건자재: green / +3.174
전력기기: yellow / 0.000
금융: yellow / 0.000
```

### 중요한 설계 판단

- `indicator-traffic-lights`는 지표 자체의 전역 신호를 보여준다.
- `sector-traffic-lights`는 동일 지표라도 섹터별 방향성 룰을 적용한 신호를 보여준다.
- 매크로 후보는 여전히 `candidate_macro_context`라 자동 매매점수에는 들어가지 않는다.
- 향후 특정 매크로 룰이 백테스트로 검증되면 그때만 `macro_confirmed_factor` 같은 상태로 승격하는 것이 안전하다.

## 2026-07-17 추가: EPIC/매크로 지표 전체 연결 및 확장 연구

사용자 후속 요청: “추가로 더 확장하고 개선방법도 연구해”

### 수정 파일

- `scripts/ops/sync_cafe_quant_mappings.py`
  - EPIC/글로벌 매크로 지표를 퀀트지표 섹터 체계에 추가 연결.
  - 신규 섹터 묶음 추가: `글로벌 경기/무역`, `한국 경기`, `미국 매크로`, `중국 매크로`, `유럽/일본 매크로`.
  - 매크로 방향성 룰을 171개까지 확장.

- `scripts/ops/sync_cafe_stock_indicator_mappings.py`
  - 금리/신용/환율/글로벌 경기/식품가격/소비 지표의 종목 후보 매핑 확대.
  - 매크로 종목 후보는 계속 `candidate_macro_context` + `unknown_macro_sensitive`로 유지.

- `scripts/ops/audit_macro_quant_coverage.py`
  - 매크로/퀀트 연결 현황, 미연결 지표, 방향성 룰 누락, 종목 매핑 상태를 Markdown 리포트로 생성.

### 실행 결과

```text
python3 scripts/ops/sync_cafe_quant_mappings.py
=> {"upserted": 202, "direction_rules": 171}

python3 scripts/ops/sync_cafe_stock_indicator_mappings.py
=> {"candidates": 681, "upserted": 1621}

python3 scripts/ops/quant_indicator_signal_engine.py --limit-events 240
=> {"checked_pairs": 333, "signals": 104, "inserted": 0, "telegram_sent": 0}
```

### 최종 커버리지

```text
quant_major_indicator_catalog total: 277
macro catalog: 97
macro with sector mapping: 97 / 97
macro with direction rule: 97 / 97
macro with stock mapping: 52 / 97
candidate macro stock mappings: 936
macro signal events: 32
```

섹터별 매크로 방향성 룰 누락은 0건으로 정리됨.

### 생성된 연구/감사 문서

- `docs/macro_quant_expansion_research_20260717.md`

### 클로드 검증 요청

1. `candidate_macro_context` 936건은 자동 매수 신호에 넣지 말고, 노출도/백테스트 검증 전까지 종목 설명 맥락으로만 표시되는지 확인.
2. `indicator_sector_direction_rules`의 방향성이 과도하게 단순화된 지표 확인.
   - 특히 환율, 금리, 원유, CPI는 섹터별로 효과가 엇갈릴 수 있음.
3. 다음 승격 실험:
   - 지표 발표일 기준 +20/+60/+120거래일 수익률.
   - 섹터 대비 초과수익.
   - 최대낙폭, hit rate, profit factor.
   - 통과 조합만 `confirmed_macro_signal` 또는 별도 상태로 승격.
4. 종목 노출도 보강:
   - `segment_revenue`, `cost_structure`, `dart_material_purchase`를 사용해 매출/원가/이익 노출 비중 추정.
   - 매출 비중이 낮은 지표는 종목 매수 신호로 쓰지 않도록 가중치 제한.

## 2026-07-17 추가: 매크로 후보 백테스트 및 전략 신호 반영

사용자 후속 요청: “검토한 사항이 로직의 개선과 백테스트 결과에 반영되었나요?” → “계속해”

### 수정 파일

- `scripts/ops/backtest_macro_indicator_candidates.py`
  - 매크로 후보 조합을 가격 히스토리로 검증.
  - 월간 지표는 발표 지연을 감안해 해당 월 초일 +35일 이후 첫 거래일 진입.
  - 연간 지표는 +120일, 일간 지표는 +1일 이후 첫 거래일 진입.
  - +20/+60/+120 거래일 수익률, 60일 MDD, 60일 profit factor 계산.
  - 통과 기준:
    - 관측치 30건 이상
    - 이벤트 3개 이상
    - 종목 2개 이상
    - 60일 평균수익률 3% 이상
    - 60일 중앙값 0% 이상
    - 60일 승률 55% 이상
    - 60일 profit factor 1.3 이상
    - 평균 60일 MDD -25% 이상

- `routes/cafe_signals.py`
  - `/api/cafe-signals/macro-signal-backtests` 추가.
  - `/api/cafe-signals/stock-trade-signals`가 `confirmed_macro_signal`을 낮은 가중치로 반영.
  - 종목 매핑과 섹터 매핑 조인을 `indicator_key + sector_name`으로 제한해 중복 드라이버 제거.
  - 수동 수집 후 매크로 후보 백테스트/승격이 같이 실행되도록 연결.

- `scripts/ops/sync_cafe_stock_indicator_mappings.py`
  - 매핑 재생성 후 최신 백테스트 통과 조합을 다시 적용.
  - 일일 동기화가 `confirmed_macro_signal`을 `candidate_macro_context`로 되돌리는 문제 수정.

- `frontend/src/views/CafeSignalsView.jsx`
  - 퀀트지표 통합 패널에 “매크로 백테스트 통과 조합” 표 추가.
  - 종목별 탭의 기본 필터에 `confirmed_macro_signal` 포함.

### 실행 결과

```text
python3 scripts/ops/backtest_macro_indicator_candidates.py --min-obs 30 --promote
=> results 42, trades 4433, passed_pairs 21, promoted true

python3 scripts/ops/sync_cafe_stock_indicator_mappings.py
=> macro_promotions_reapplied 108

macro mapping status:
candidate_macro_context / unknown_macro_sensitive: 828
confirmed_macro_signal / macro_backtested: 108

snapshot_quant_stock_trade_signals.py
=> snapshots 26, watch 16, buy 0, sell_risk 0
```

현재 실시간 신호는 중복 제거 후 모두 `관찰`이다. 이는 매크로 신호 가중치를 낮게 두고, 기존 가격위험/시장확인 필터를 유지했기 때문이다. 억지 매수/매도 후보로 올리지 않는 것이 맞다.

### 대표 통과 조합

```text
한국 무역수지 → 전력기기: 60일 평균 +31.90%, 중앙값 +16.06%, 승률 79.2%, PF 10.73
구리 가격 → 전력기기: 60일 평균 +29.51%, 중앙값 +26.76%, 승률 86.4%, PF 26.79
중국 OECD CLI → 반도체: 60일 평균 +29.03%, 중앙값 +6.23%, 승률 60.0%, PF 7.86
한국 수출 → 반도체: 60일 평균 +21.99%, 중앙값 +14.85%, 승률 70.0%, PF 6.18
미국 BAA 스프레드 → 금융: 60일 평균 +14.11%, 중앙값 +10.59%, 승률 80.5%, PF 13.02
```

### 남은 검증 포인트

1. 이 백테스트는 발표일 원천별 실제 공시 timestamp가 아니라 보수적 지연일을 쓴다. 원천별 실제 발표 캘린더가 생기면 재검증 필요.
2. 현재는 동일섹터 종목 전체 후보 검증이다. 종목별 매출/원가 노출도 기반 가중 검증이 다음 단계.
3. `confirmed_macro_signal`은 전략 신호에 낮은 가중치로만 반영했다. 실전 매수 후보 승격은 가격/수급 확인 조건을 추가로 통과해야 한다.
