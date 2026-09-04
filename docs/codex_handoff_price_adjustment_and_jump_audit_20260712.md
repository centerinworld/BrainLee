# Codex → Claude 핸드오프: 수정주가·자본행위 엔진 및 급변 후속 감사

작성일: 2026-07-12  
프로젝트: `/Applications/stock_dashboard`

## 1. 작업 목표

1. 액면분할·병합·무상증자·유상증자 때문에 발생하는 가격 단절을 구조화한다.
2. 수정주가와 원주가를 혼용하지 않도록 가격 기준을 명시한다.
3. 기존 `price_history`를 파괴적으로 수정하지 않고 신뢰 가능한 수익률만 분리한다.
4. 남은 비정상 급변을 분류하고 백테스트 사용 여부를 통제한다.

## 2. 핵심 정책

- `price_history`: KIS 수정주가 수집을 지향하지만 legacy 소스 혼재 위험이 있는 연구용 계열.
- `stock_price_daily`: 원주가·실제 체결가·상장주식수 검증용 계열.
- 원본 5,901,029행을 덮어쓰거나 삭제하지 않는다.
- 공시 유형과 주식수 변화 방향·비율이 일치하는 분할·병합·무상증자만 보정계수를 확정한다.
- 유상증자는 발행가격과 권리 가치가 없으면 주식수 비율만으로 보정하지 않는다.
- 미확정 급변은 `return_usable=0`으로 차단하고 원본은 보존한다.

## 3. 신규 파일

### `scripts/build_corporate_action_adjustment_engine.py`

- `price_series_registry` 생성
- `corporate_action_events` 생성
- `price_history_quality_v` 생성
- `stock_price_daily_adjusted_v` 생성
- `stock_price_daily.shares` 변화와 DART 공시를 교차 검증
- backward factor는 확정 이벤트에만 `old_shares/new_shares`로 저장

### `scripts/audit_price_jumps_and_build_canonical.py`

- 극단 가격변동 전수 분류
- `price_jump_audit` 생성
- `canonical_price_history_v` 생성
- `canonical_price_returns_v` 생성
- 안전한 연속 구간에만 `safe_daily_return` 제공
- 감사 요약: `research_outputs/price_basis_audit_20260712.json`

### 기타 수정

- `main.py`: 가격 기준 선택 API와 canonical 품질 응답
- `scheduler.py`: 매일 KRX 기본정보 갱신 뒤 보정 엔진과 급변 감사를 순차 실행
- `main.py` 자본행위 차트 API: 정규화된 주식수 변화·보정 확정 여부도 표시

## 4. 생성 DB 객체

| 객체 | 용도 |
|---|---|
| `price_series_registry` | 가격계열 기준·용도·혼재위험 정책 |
| `corporate_action_events` | 자본행위·주식수 변화·보정계수·근거·신뢰도 |
| `price_history_quality_v` | 정상/자본행위 검토/미설명 급변 분류 |
| `stock_price_daily_adjusted_v` | 확정 자본행위만 적용한 원주가 기반 수정계열 |
| `price_jump_audit` | 극단 급변별 교차검증 결과 |
| `canonical_price_history_v` | 원본 가격 + canonical 품질 + 수익률 사용 여부 |
| `canonical_price_returns_v` | 현재·직전 행이 모두 사용 가능할 때만 일간수익률 제공 |

## 5. 자본행위 엔진 결과

- 상장주식수 변화 사건: 4,222건
- 확정 보정계수: 14건
  - 무상증자 13건
  - 주식분할 1건
- 검토 필요: 4,208건
- 확정 건수가 적은 것은 의도된 보수 정책이다.

## 6. 급변 후속 감사 결과

감사 대상: `close/previous_close > 1.8` 또는 `< 0.55`, 총 4,369건.

| 분류 | 건수 | 수익률 사용 |
|---|---:|---|
| `unresolved_active_common` | 3,534 | 차단 |
| `corporate_action_or_delisting_nearby` | 337 | 차단 |
| `inactive_or_noncommon_review` | 277 | 차단 |
| `mixed_basis_or_price_corruption` | 124 | 차단 |
| `raw_source_confirmed_jump` | 78 | 허용 |
| `non_equity_symbol` | 19 | 차단 |

Canonical 결과:

- 전체 행: 5,901,029
- 행 자체 사용 가능: 5,896,733
- 안전 일간수익률: 5,889,670
- 극단 급변 중 자동 허용: 원주가에서도 같은 움직임이 확인된 78건

## 7. 명확한 혼재·오염 표본

- `006380`, 2026-03-24: `price_history` 5.000배, 원주가 0.884배
- `043220`, 2026-03-24: `price_history` 10.000배, 원주가 1.710배
- `074610`, 2026-03-24: `price_history` 10.000배, 원주가 1.243배
- `152550`, 2021년 반복: `price_history` 약 500배 상승·0.002배 하락, 원주가 약 1배

이 행들은 삭제하지 않았고 `mixed_basis_or_price_corruption`, `return_usable=0`으로 표시했다.

## 8. 차트 API 계약

`GET /api/dashboard/chart/{stock_code}?days=365&basis=<basis>`

- `research_adjusted`: 기존 연구용 계열, 혼재 위험 명시
- `canonical_research`: canonical 품질과 `return_usable` 포함
- `execution_raw`: 원주가 체결 검증용
- `confirmed_actions_adjusted`: 확정 자본행위만 보정한 원주가 계열

표본 검증:

- `006380` canonical API 75행 중 2026-03-24 1행이 `mixed_basis_or_price_corruption`, `return_usable=0`으로 반환됨.

## 9. 자동화

`scheduler.py`의 매 영업일 18:35 KRX 종목기본정보 작업 뒤:

1. `build_corporate_action_adjustment_engine.py`
2. `audit_price_jumps_and_build_canonical.py`

순서로 실행한다. 두 작업 모두 원본 가격을 수정하지 않는다.

## 10. Claude 필수 검증

1. 14개 확정 보정계수의 DART 공시·주식수 전후·비율을 전건 대조한다.
2. `317240`의 11.4669배 무상증자 매핑이 실제 단일 이벤트인지 중간 스냅샷 누락 누적인지 확인한다.
3. `072520` 유무상증자 사례에 단순 무상증자 factor를 적용해도 되는지 발행조건을 확인한다.
4. `raw_source_confirmed_jump` 78건이 실제 급등락인지 정리매매·상장폐지·저가주 오류인지 전건 또는 층화표본으로 확인한다.
5. `mixed_basis_or_price_corruption` 124건은 `stock_price_daily` 기준 교체 후보지만 원본을 바로 수정하지 말고 KIS 수정주가를 재조회한다.
6. `unresolved_active_common` 3,534건을 연도·수집 소스·종목별 반복 패턴으로 군집화한다.
7. `canonical_price_returns_v.safe_daily_return`이 차단 행 전후에서 모두 NULL인지 확인한다.
8. 기존 백테스트 주요 로더가 `canonical_price_returns_v`를 사용하도록 전환하기 전 결과 차이를 병렬 비교한다.

## 11. 남은 후속 작업

- KIS 수정주가 전 종목·전 기간 재수집용 별도 canonical 테이블 구축
- DART 원문에서 권리락일·배정기준일·효력일·상장일·발행가액 파싱
- 유상증자 TERP 보정
- 3,534개 활성 보통주 미해결 급변 원인 분류
- 모든 백테스트 함수에 `price_basis` 필수 인자 도입
- 기존 전략 결과를 canonical 계열로 재실행하고 차이를 전략센터에 표시

## 12. 검증 완료 사항

- Python 컴파일 통과
- DB 객체와 행 수 검증
- 세 보정 스크립트 반복 실행 가능
- `canonical_research` API 실응답 확인
- 백엔드 재시작 완료

## 12-1. 네이버 금융 외부 교차검증 추가

신규 파일:

- `scripts/verify_price_history_with_naver.py`
- `scripts/audit_naver_recent_price_agreement.py`
- `research_outputs/naver_price_crosscheck_20260712.json`
- `research_outputs/naver_recent_price_agreement_20260712.json`

외부 소스:

- 네이버 금융 fchart 일봉
- URL 형식: `https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=7000&requestType=0`

급변 사건 전수 검증:

- 요청 종목: 1,326개
- 검증 사건: 4,350건
- 요청 실패: 0건
- 네이버가 공공 원주가를 지지: 123건
  - `externally_confirmed_internal_corruption`
  - `return_usable=0`
- 네이버가 내부 `price_history` 급변을 지지: 29건
- 내부·공공·네이버 모두 일치: 5건
- 외부 가격 누락: 272건
- 세 계열 불일치: 3,921건
  - 가격 기준 차이가 해소되지 않았으므로 자동 허용하지 않음

최근 정상구간 일치도:

- 활성 보통주 표본: 200종목
- 비교 종가: 11,996개
- 네이버와 0.1% 이내: 97.10%
- 네이버와 1% 이내: 97.11%
- 네이버와 5% 이내: 97.11%
- 중앙 절대오차: 0.00%
- 95백분위 절대오차: 0.00%

해석:

- 최근 일반 거래일 가격은 네이버와 거의 완전히 일치한다.
- 문제는 전체 가격계열이 아니라 일부 종목의 자본행위·거래정지·소스 전환 구간에 집중돼 있다.
- `006380` 2026-03-24는 내부 5배 점프, 공공 원주가 0.884배, 네이버 1.0배로 내부 오염을 외부 확인했다.
- 외부 검증 후 `price_jump_audit` 분류를 상향하며, 신규 사건만 `--only-new`로 조회한다.
- 스케줄러는 내부 급변 감사 후 네이버 신규 사건 검증을 자동 실행한다.

Claude 추가 검증:

1. 네이버 fchart가 수정주가인지 원주가인지 자본행위 사례별로 확인한다.
2. 최근 불일치 최악 표본 `002680`, `000500`, `002630`, `001230`을 KIS·KRX와 삼중 대조한다.
3. `three_way_disagreement` 3,921건은 자동 수정하지 말고 가격 기준별 regime 군집화를 수행한다.
4. 외부 API 정책 변경·차단 시 마지막 성공 검증일과 stale 상태를 표시하도록 보강한다.
5. 네이버 단일 외부 소스에 의존하지 않도록 KIS 원주가/수정주가 이중 조회 또는 다른 독립 소스를 추가한다.

## 13. 작업 트리 주의

- 작업 시작 전부터 `main.py`, `scheduler.py`, `frontend/src/App.jsx` 등 다수 파일이 수정된 상태였다.
- 관련 없는 기존 변경을 되돌리지 말 것.
- `price_history`와 `stock_price_daily`를 단순 상호 덮어쓰기하지 말 것. 과거 감사에서 혼재가 더 악화된 전력이 있다.
