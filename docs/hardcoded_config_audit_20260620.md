# 하드코딩 설정 감사 및 최적화 기록 — 2026-06-20

## 조치 완료

- 공공데이터 API 키 직접 하드코딩 제거
  - `jinju_apt_tracker.py`
  - `collect_short_5years.py`
  - `employment_monitor/collect_nps_monthly.py`
  - `employment_monitor/collect_labor_welfare.py`
- 민감값이 남아 있던 백업/문서 파일 정리
  - `config.py.save`: 로그인/텔레그램 값 환경변수 참조로 변경
  - `PROJECT_MASTER.md`: 실제 계정 예시를 placeholder로 변경
- `.env`만 가볍게 읽는 공용 유틸 추가
  - `env_utils.py`
  - 기존 `config.py`의 필수 키 검증 부작용 없이 작은 수집기에서 `PUBLIC_DATA_API_KEY`를 읽을 수 있게 함
- 반복 감사 스크립트 추가
  - `scripts/audit_hardcoded_config.py`
  - 민감값은 마스킹하고 파일/라인/분류만 출력
- 공용 경로 중앙화 1차 적용
  - `stock_universe.py`: 프로젝트 루트 하드코딩 제거
  - `financial_profiles.py`: 프로파일 JSON 경로를 `env_utils.BASE_DIR` 기반으로 변경
  - `collect_dart_financial_batch.py`: DB/scratch/sys.path 경로를 `env_utils.BASE_DIR` 기반으로 변경
  - `routes/tenbagger.py`: 텐버거 API DB 경로를 `env_utils.BASE_DIR` 기반으로 변경

## 1차 감사 결과

- `secret_literal`: 0
- `absolute_project_path`: 1,797
- `localhost_url`: 462
- `fixed_year_or_date`: 74,553

해석:
- 민감 키/토큰/비밀번호 형태의 직접 하드코딩은 제거 완료.
- 남은 절대 경로는 오래된 디버그 스크립트, 운영 셸 스크립트, 개별 수집기, 문서성 파일에 많이 분포.
- 날짜/연도는 데이터 검증 기록과 백필 기간 상수가 섞여 있어, 운영 로직 기본값과 과거 기록을 분리해서 줄여야 함.

## 남은 최적화 후보

- 절대 경로
  - `/Applications/stock_dashboard`, `/Users/brainlee`가 여러 실행 스크립트와 과거 문서에 남아 있음.
  - 우선순위는 현재 운영 수집기, launchd/cron 대상, 백테스트 실행 스크립트, 과거 디버그 스크립트 순서.
- 고정 기간/날짜
  - `2020~2026`, 특정 거래일, 특정 월 기준값이 수집기와 검증 스크립트에 산재.
  - 데이터 백필용 고정값과 운영 로직용 기본값을 분리해야 함.
- localhost URL
  - 개발 서버 확인용 값은 허용 가능하지만, 운영 스크립트는 환경변수 기반 `BACKEND_URL`, `FRONTEND_URL`로 통일하는 것이 좋음.

## 운영 규칙

1. 신규 API 키, 계정, 토큰은 코드/문서에 쓰지 않고 `.env` 또는 시스템 환경변수만 사용한다.
2. 수집 시작일은 `--start`, `--years`, `TENBAGGER_DATA_START_YEAR` 같은 명시 설정으로 받는다.
3. DB 경로는 가능하면 `db_utils.STOCK_DB_PATH` 또는 `Path(__file__).resolve()` 기반으로 계산한다.
4. 큰 리팩터링 전에는 아래 명령으로 새 하드코딩을 먼저 확인한다.

```bash
python3 scripts/audit_hardcoded_config.py
```
