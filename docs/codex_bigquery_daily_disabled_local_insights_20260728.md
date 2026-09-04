# Codex BigQuery Daily Disabled + Local Insights — 2026-07-28

## 결론

사용자 요청에 따라 BigQuery 매일 자동 refresh는 기본 비활성화했다. 이미 업로드된 BigQuery 데이터는 삭제하지 않았고, 명시적으로 환경변수를 켠 경우에만 다시 동작한다.

## 변경 사항

### 1. BigQuery 자동 실행 중지

파일: `scheduler.py`

다음 루프/잡에 환경변수 가드를 추가했다.

- `ENABLE_BIGQUERY_DAILY=1`일 때만 `_loop_bigquery_sync`, `_job_bigquery_sync` 실행
- `ENABLE_BQ_TRIPLE_PIPELINE=1`일 때만 `_loop_bq_triple_pipeline`, `_job_bq_triple_pipeline` 실행
- `ENABLE_BQ_MORNING_ALERT=1`일 때만 `_loop_bq_morning_alert`, `_job_bq_morning_alert` 실행

기본값은 모두 `0`이므로 로컬 앱을 재시작해도 BigQuery refresh/query 기반 자동 작업은 실행되지 않는다.

### 2. 로컬 인사이트 생성기 추가

파일: `scripts/generate_trigger_discovery_insights.py`

BigQuery를 호출하지 않고 로컬 SQLite `trigger_discovery_*` 테이블만 사용해서 리포트를 생성한다.

생성 파일:

- `research_outputs/trigger_discovery_insights_20260728.json`
- `research_outputs/trigger_discovery_insights_20260728.md`

입력 데이터 상태:

- 이벤트: 108,652건
- 종목 연결: 313,596건
- forward return: 878,303건
- available_date: 2020-01-20 ~ 2026-07-28

### 3. 후보 품질 보정

초기 결과에서 위안/달러, 유로/달러, S&P 500 같은 광범위 거시 지표가 종목 노출도 0% 상태로 상단 후보에 올라왔다. 이는 매수 후보라기보다 시장 배경에 가깝다.

따라서 최근 후보 표는 다음 기준을 적용했다.

- 매출/이익/비용 노출도 중 하나가 5% 이상인 종목만 표시
- 노출도 0% 거시 신호는 종목 매수 후보에서 제외

보정 후 최근 후보는 산업용 피팅/밸브, 선박용 디젤엔진, PCB 수출입처럼 실제 품목 노출도가 있는 종목군 중심으로 바뀌었다.

## 검증

실행 명령:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scheduler.py scripts/generate_trigger_discovery_insights.py
/Applications/stock_dashboard/venv/bin/python scripts/generate_trigger_discovery_insights.py
```

결과:

- 컴파일 통과
- 로컬 인사이트 리포트 생성 성공
- BigQuery 호출 없음

## 다음 제안

BigQuery는 매일 refresh 대상이 아니라 월 1~2회 또는 대규모 조합 탐색이 필요할 때만 수동 실행하는 연구용 엔진으로 두는 것이 적절하다.

일상 운영은 다음 순서가 낫다.

1. 로컬 DB에서 매일 수집/신호 생성
2. 로컬 trigger discovery 리포트로 최근 후보 감시
3. 후보가 일정 수준 이상 쌓이면 BigQuery로 대규모 조합 탐색을 수동 실행
4. BigQuery 결과는 즉시 로컬 DB/프론트엔드로 내려받고 자동 refresh는 다시 끔
