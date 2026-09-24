# ETF KRX/TIGER 교차검증 핸드오프 (2026-09-12)

## 결론

- 2026-09-10부터 발생한 ETF Check 동등성 실패의 첫 원인은 KRX 구성종목 코드가 일부 6자리 코드에서 ISIN으로 변경된 형식 회귀였다.
- ISIN을 과거의 유일한 1:1 매핑으로 복원한 뒤 80종목 표본의 편입 ETF 개수와 편입 여부가 모두 100% 일치했다.
- 잔존 실패 `435420`은 KRX가 109개에서 52개로 줄어든 응답을 반복 반환해 절단으로 판단했으나, TIGER 공식 자료가 같은 날짜에 51개 자산과 비중합 100.02%를 제공했다. KRX 52개는 공식 51개에 설정현금액 한 행이 추가된 구조로, 정상적인 대규모 리밸런싱으로 판단한다.

## 구현

- `ETF_check/full_pdf_collector.py`
  - ISIN 코드 정규화, 불완전 응답 재시도, 실패 원문과 SHA-256 보존, 실패 시 잔존 구성종목 삭제, 잘못된 완료 발행 취소.
- `ETF_check/issuer_pdf_fallback.py`
  - TIGER 공식 `pdfListAjax.ajax` 어댑터 추가.
  - HTML의 선언 총건수와 실제 행수를 비교하고 원문/해시를 별도 fallback 테이블에 보존.
  - 해당 날짜의 공식 자료, 원문 해시, 구성종목 건수, 국내 6자리 종목 0건을 모두 검증한 경우만 국내 편입 계산 예외로 인정.
- `ETF_check/etf_parity_cutover_v2.py`, `ETF_check/verify_daily_pipeline.py`, `ETF_check/publish_direct_stock_daily.py`
  - 전체 KRX 완전성과 국내 종목 계산 완전성을 분리.
  - 검증된 해외자산 전용 ETF 예외만 국내 편입 계산에 포함.
- `ETF_check/etf_universe_sync_v3.py`
  - ZIP 파서를 네트워크 호출과 분리해 영문 포함 ETF 코드 테스트가 외부 서버 상태에 의존하지 않도록 수정.

## 검증 결과

- 기준일: `20260911`
- ETF 유니버스: 1,168개
- KRX 정상: 1,167개
- TIGER 공식 해외전용 예외: 1개 (`435420`)
- ETF Check 표본: 80/80 성공
- 편입 교집합: 100%
- ETF 개수 정확 일치: 100%
- 금액 상관계수: 0.9976
- 총액 비율: 1.0259
- 중앙 SMAPE: 2.76%
- 화면용 게시: 2,693종목, 편입 양수 1,364종목, 가격/시총 커버리지 100%
- ETF 테스트: 50건 통과
- SQLite `PRAGMA integrity_check`: `ok`

## 복원

- ISIN 복구 전: `ETF_check/backups/etf_check.pre_pdf_repair_20260911.db`
- TIGER fallback 적용 전: `ETF_check/backups/etf_check.pre_tiger_fallback_20260912.db`
- 복원 시 실행 중인 ETF 배치를 먼저 중지하고 SQLite `.restore` 또는 `.backup` 명령으로 운영 DB를 교체한다.

## 남은 운영 조건

- 2026-09-07~11 최근 5영업일이 연속 통과해 소스 모드는 `krx_primary`로 자동 전환됐다.
- `etf_primary_service.py`는 검증된 운용사 예외를 포함하는 최신 `etf_direct_stock_publication` 날짜를 사용한다. KRX 100% 발행일만 찾다가 2026-09-09로 후퇴하던 오류를 수정했다.
- `full_krx_publication=false`는 숨기지 않는다. 국내 종목 편입 계산만 TIGER 공식 자료로 완전성이 입증됐다.
- 다음 배치부터 실패 KRX 응답도 원문과 해시가 자동 저장된다.
- 실제 API 확인: `/api/etf-check/etf-list/005930`은 `source=KRX_KIS_DIRECT`, `base_date=20260911`을 반환한다.
