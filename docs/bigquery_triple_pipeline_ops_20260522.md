# BigQuery 3배주 패턴 파이프라인 운영 가이드

## 1) 목적
- 로컬 DB에서 BigQuery로 적재된 데이터를 기반으로
- 매일 3배주/우상향 패턴 후보를 테이블화하고
- 아침마다 상위 후보를 텔레그램으로 수신

## 2) 추가된 스크립트
- `/Applications/stock_dashboard/scripts/bigquery_triple_pipeline.py`
  - 소스 뷰: `stock_dashboard.v_3x_candidate_screen`
  - 결과 테이블:
    - `stock_dashboard.triple_pattern_daily`
    - `stock_dashboard.triple_pattern_sector_daily`
- `/Applications/stock_dashboard/scripts/bigquery_triple_morning_alert.py`
  - 최신 `triple_pattern_daily`에서 TOP N 조회
  - 텔레그램 알림(설정 시) + 로컬 마크다운 리포트 저장

## 3) 수동 실행
```bash
cd /Applications/stock_dashboard
/Applications/stock_dashboard/venv/bin/python scripts/bigquery_triple_pipeline.py
/Applications/stock_dashboard/venv/bin/python scripts/bigquery_triple_morning_alert.py
```

## 4) 환경변수
- `BQ_PROJECT_ID` (기본: `project-d8a62269-8156-4f96-870`)
- `BQ_DATASET_ID` (기본: `stock_dashboard`)
- `TRIPLE_PATTERN_MIN_SCORE` (기본: `62`)
- `TRIPLE_PATTERN_MAX_ROWS` (기본: `200`)
- `TRIPLE_ALERT_TOP_N` (기본: `15`)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (알림 발송용)

## 5) 로컬 미의존 운영(권장)
아래 2개 잡을 GCP에서 스케줄링:

1. `23:40 KST` 파이프라인 잡 실행
2. `08:10 KST` 아침 알림 잡 실행

추천 구성:
- 실행 주체: Cloud Run Jobs (또는 Cloud Functions 2nd gen)
- 트리거: Cloud Scheduler
- 권한: Job 실행 SA에 BigQuery Data Editor + BigQuery Job User

핵심은 **아침 잡이 로컬 PC가 아니라 GCP에서 직접 BigQuery를 조회**해 결과를 발송하도록 운영하는 것입니다.

## 6) 현재 확인된 실행 결과 (2026-05-22)
- 파이프라인: 후보 `132`건 생성, 평균 스코어 `68.28`, 최고 `83.0`
- 아침 리포트 스크립트: TOP `15` 추출 성공
- 저장 리포트 예시:
  - `/Applications/stock_dashboard/reports/bq_triple_morning_20260522_232515.md`
