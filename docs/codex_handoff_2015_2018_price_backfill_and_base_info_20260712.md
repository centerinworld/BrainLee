# Codex → Claude 핸드오프: 2015~2018 OHLCV 및 종목기본정보 복구

작성일: 2026-07-12

## 요청 배경

Claude가 2015~2018 홀드아웃을 재실행하려 했으나 `price_history`가 사실상 비어 있음을 확인했다. 또한 `stock_base_info_changes`가 0건이었다.

## 백필 전 상태

국내 6자리 종목 기준:

| 연도 | 행 | 종목 |
|---|---:|---:|
| 2015 | 0 | 0 |
| 2016 | 0 | 0 |
| 2017 | 0 | 0 |
| 2018 | 7,891 | 1,976 |

2015~2017년에 존재하던 12개 내외 데이터는 지수·해외 심볼이었고 국내 6자리 종목 데이터가 아니었다.

## 수집 방법

- 스크립트: `scripts/backfill_naver_ohlcv_2015_2018.py`
- 소스: 네이버 금융 fchart 일봉
- 조회 코드 집합:
  - `stock_universe`
  - `price_history`
  - `dart_disclosures`
  - `stock_price_daily`
  - 위 테이블의 6자리 코드 합집합
- 요청 코드: 4,284개
- 요청 오류: 0건
- 날짜: 2015-01-01~2018-12-31
- 네이버 staging 테이블에 먼저 저장 후 `price_history`에는 `INSERT OR IGNORE`만 수행
- 기존 가격 행은 덮어쓰지 않음

## 수집 결과

- staging: `naver_price_history_backfill`
- staging 행: 2,238,984
- staging 종목: 2,662
- `price_history` 신규 누락행: 2,231,093

| 연도 | 행 | 종목 | 거래일 |
|---|---:|---:|---:|
| 2015 | 502,254 | 2,139 | 248 |
| 2016 | 541,154 | 2,297 | 246 |
| 2017 | 575,478 | 2,474 | 243 |
| 2018 | 620,098 | 2,655 | 244 |

`price_series_registry`에 `naver_price_history_backfill` 출처·용도·비덮어쓰기 정책을 등록했다.

## 외부 가격 신뢰 근거

백필 전 별도 검사에서 최근 활성 보통주 200종목·11,996개 종가를 네이버와 비교했다.

- 1% 이내 일치: 97.11%
- 중앙 절대오차: 0.00%
- 95백분위 절대오차: 0.00%

단, 자본행위 구간은 가격 기준이 다를 수 있으므로 canonical 급변 감사를 재실행했다.

## 백필 후 품질 재생성

다음 순서로 재실행 완료:

1. `build_corporate_action_adjustment_engine.py`
2. `audit_price_jumps_and_build_canonical.py`
3. `verify_price_history_with_naver.py --only-new`

현재 급변 감사 대상은 4,953건이다. 백필로 과거 데이터가 늘어 새 급변 후보가 추가됐고, 외부 검증 신규 사건 1,136건을 추가 확인했다.

감사 테이블 재생성 버그도 수정했다. 백필로 더 이상 급변이 아니게 된 사건이 과거 `price_jump_audit`에 남지 않도록 매 실행 시 현재 급변 집합으로 snapshot rebuild한다.

## stock_base_info 수집 복구

### KRX 기준 스냅샷

- 수집기: `collectors/krx_isu_base_info.py`
- 2026-07-10 KRX 스냅샷 수동 실행 완료
- 갱신: 2,677종목
- 스냅샷: 2,690종목
- 전체 history: 5,389행 / 2,706종목
- 보유 스냅샷 날짜: 2026-05-08, 2026-07-10

두 스냅샷 간격이 14일을 넘어 직접 diff는 0건이었다. 향후에는 스케줄러가 매 영업일 18:35 실행하므로 일별 변경이 기록된다.

### 과거 상장주식수 변경 백필

- 스크립트: `scripts/backfill_stock_base_info_changes.py`
- 원천: `corporate_action_events` (`stock_price_daily.shares` + DART 정규화)
- `stock_base_info_changes`: 4,222건 / 1,749종목
- 기간: 2020-01-03~2026-07-09
- 추가 필드:
  - `source`
  - `confidence`
  - `evidence_report_name`
- 변경 유형: `shares_issued`

## Claude 검증 및 다음 실행

1. 2015~2018 연도별 거래일·종목 수를 위 표와 재대조한다.
2. 각 연도 무작위 30종목을 네이버 원문과 비교한다.
3. 상장폐지 종목이 충분히 포함됐는지 당시 KRX 상장 목록과 비교해 생존편향을 측정한다.
4. `canonical_price_returns_v.safe_daily_return`을 사용해 2015~2018 홀드아웃을 재실행한다.
5. `price_history` 직접 수익률과 canonical 수익률 결과를 병렬 비교한다.
6. 2015~2018 네이버 데이터가 수정주가인지 자본행위 표본으로 확인한다.
7. `stock_base_info_changes`의 4,222건 중 확정 보정 14건과 검토대상을 구분한다.
8. 앞으로 2영업일 연속 KRX 스냅샷이 쌓인 뒤 실제 `changes` 생성 여부를 확인한다.

## 주의사항

- staging 테이블을 삭제하지 말 것. 외부 원천과 수집시각을 증명하는 provenance다.
- 기존 `price_history` 행을 네이버 값으로 덮어쓰지 말 것.
- 네이버 백필에는 종목별 상장 전·상장폐지 후 데이터가 없으므로 홀드아웃 universe를 당시 시점 기준으로 구성해야 한다.
- BigQuery `price_history` 전체 동기화는 현재 코드상 최근 5년 cutoff를 사용하므로 2015~2018 백필이 자동 업로드되지 않는다. BigQuery 홀드아웃을 사용할 경우 동기화 cutoff를 별도로 수정한 뒤 전체 재적재해야 한다.

## 검증 완료

- 수집 스크립트 컴파일 통과
- 4,284코드 네이버 요청 오류 0
- 연도별 243~248 거래일 확인
- staging과 실제 삽입 행 수 확인
- KRX 스냅샷 수집 성공
- `stock_base_info_changes` 4,222건 적재 확인
- canonical·외부 가격 감사 재실행 완료

