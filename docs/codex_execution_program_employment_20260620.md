# Codex 실행 기록: 프로그램매매/인력 데이터 보강 (2026-06-20)

## 사용자 요청

이전 답변의 5번/6번 실행:

5. 프로그램 매매 데이터 수집
6. 인력 증가 데이터 보강

## 5. 프로그램 매매 데이터 수집 결과

대상 테이블:

- `program_trading_daily`

실행 명령:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/collect_krx_program_trading.py --start 20260615 --end 20260619
```

결과:

- 일반 실행: `https://data.krx.co.kr/` 접속 단계에서 30초 timeout.
- 권한 상승 재실행: 동일하게 `Page.goto: Timeout 30000ms exceeded`.
- `curl -I https://data.krx.co.kr/`: 15초 timeout.
- 로그인 없는 직접 API POST 테스트:
  - `MDCSTAT05301` KOSPI 프로그램매매
  - `MDCSTAT05401` KOSDAQ 프로그램매매
  - HTTP 200은 오지만 JSON이 아니라 KRX HTML 세션/차단 페이지가 반환됨.

현재 상태:

| 테이블 | 행 수 |
|---|---:|
| `program_trading_daily` | 0 |

판정:

- 현재 환경에서는 KRX `data.krx.co.kr` 접속/세션 발급이 되지 않아 프로그램매매 수집이 실패했다.
- 기존 `CLAUDE.md`의 2026-06-13/2026-06-16 기록과 동일하게 KRX 로그인/세션 정책 변경 또는 네트워크 응답 불안정 문제가 지속된다.
- 스케줄러에는 이미 `KRX프로그램매매` 18:20 잡이 등록되어 있으나, KRX 접속 정상화 전까지 자동수집도 실패할 가능성이 높다.

후속 조치:

1. KRX 사이트 접속 정상화 확인 후 재실행.
2. `mdc.client_session` 쿠키 발급 실패 여부를 Playwright screenshot/debug 로그로 남기도록 수집기 보강.
3. KRX 외 대체 소스가 있으면 우선 검토. 단, 차익/비차익 프로그램 순매수는 KRX 원천 의존도가 높다.

## 6. 인력 증가 데이터 보강 결과

### 6-1. DART 직원현황 수집

실행 명령:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/collect_dart_ch_data.py --limit 100 --skip-existing
```

결과:

- 첫 대상 `466910` 처리 후 DART API 키 전체 소진으로 중단.
- 저장 0건.

현재 상태:

| 테이블 | 행 수 | 종목 수 |
|---|---:|---:|
| `dart_employee_count` | 391 | 391 |

판정:

- DART 직원현황은 API 한도 회복 후 재실행 필요.

### 6-2. NPS/고용 데이터 외부 API 테스트

실행 명령:

```bash
/Applications/stock_dashboard/venv/bin/python employment_monitor/fetch_nps_2years.py --dry-run
```

결과:

- 공공데이터 NPS API가 HTTP 500 `Unexpected errors` 반환.
- 외부 API 신규 수집은 실패.

### 6-3. 로컬 고용 DB → stock DB 동기화

확인 결과 `employment_monitor/employment.db`에는 이미 월별 고용/NPS 데이터가 존재했다.

로컬 원천:

| 테이블 | 행 수 |
|---|---:|
| `employment_company` | 3,919 |
| `nps_monthly` | 27,641 |
| `wlb_monthly` | 5,113 |

stock DB의 `nps_workplace_monthly`는 0건이었으므로, 로컬 `employment_monitor/employment.db.nps_monthly`를 `stock.db.nps_workplace_monthly`로 동기화했다.

추가 스크립트:

```text
scripts/sync_employment_nps_to_stock_db.py
```

실행 명령:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/sync_employment_nps_to_stock_db.py
/Applications/stock_dashboard/venv/bin/python scripts/sync_employment_nps_to_stock_db.py
```

실행 결과:

```json
{
  "source_rows": 27641,
  "upserted": 27641,
  "target_total_rows": 27641,
  "target_stocks": 2160,
  "min_ym": "202504",
  "max_ym": "202605"
}
```

최종 상태:

| 테이블 | 행 수 | 종목 수 | 기간 |
|---|---:|---:|---|
| `nps_workplace_monthly` | 27,641 | 2,160 | 2025-04 ~ 2026-05 |
| `dart_employee_count` | 391 | 391 | 연간/반기 일부 |

주의:

- `nps_workplace_monthly`에 동기화된 값은 `nw_acqzr_cnt` 신규 가입자, `lss_jnngp_cnt` 상실 가입자 중심이다.
- 총 재직자 수가 아니라 월별 유입/이탈 흐름이다.
- `raw_base_json.source='employment_db.nps_monthly'`로 원천을 표시했다.

## 결론

- 5번 프로그램매매: 실행했으나 KRX 접속/세션 문제로 수집 실패, 테이블 0건 유지.
- 6번 인력 증가: 외부 API 신규 수집은 실패했지만, 로컬에 존재하던 NPS 월별 증감 27,641건을 stock DB로 동기화 완료.
