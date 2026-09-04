# Codex Handoff — order_contracts 수주잔고 급증 proxy 연결 점검 (2026-07-25)

## 배경
- Claude 작업 브랜치 `origin/claude/order-backlog-spike-analysis-ukhzzh`는 현재 작업 브랜치 기준과 크게 달라 그대로 merge하면 기존 수집기/라우트 다수가 삭제되는 위험한 diff가 있었다.
- 따라서 `routes/order_contracts.py`, `collect_order_contracts.py`만 이식하고 현재 코드 구조에 맞춰 연결했다.

## 반영 사항
- `main.py`
  - `routes.order_contracts` 라우터 등록.
  - 신규 API:
    - `GET /api/order-contracts/screener/surge`
    - `GET /api/order-contracts/stock/{stock_code}`
    - `GET /api/order-contracts/backlog/{stock_code}`
    - `POST /api/order-contracts/collect/today`
    - `POST /api/order-contracts/collect/{stock_code}`
    - `PATCH /api/order-contracts/{id}/verify`
    - `DELETE /api/order-contracts/{id}`
- `collectors/dart_collector.py`
  - `get_contract_disclosures`, `get_todays_contract_disclosures`, `parse_contract_document` 추가.
  - 중요한 수정: Claude 원안의 `OpenDartReader.list(..., kind="I")` 방식은 최근 수주공시를 0건으로 놓쳤다.
  - 현재는 기존 검증 수집기 `collectors.dart_contract_collector`의 `list.json` 페이지 순회와 금액 파서(`_fetch_dart_list`, `_is_contract_report`, `_fetch_dart_document`, `_extract_amounts`)를 재사용하도록 수정했다.
- `scheduler.py`
  - 기존 `DART수주공시`는 유지.
  - 신규 `DART수주계약`을 19:00 평일 실행으로 추가해 `order_contracts` proxy 테이블을 당일 증분 적재.
- `frontend/src/App.jsx`
  - 기존 `수주공시 알림` 페이지 안에 `수주잔고 급증` / `공시 목록` 전환 버튼 추가.
  - `수주잔고 급증`은 `/api/order-contracts/screener/surge?window_months=3&min_growth_pct=50&limit=80` 결과를 표시.

## 데이터 상태
- 새 테이블 `order_contracts` 생성 완료.
- 기존 `dart_contracts`에서 seed 완료:
  - `order_contracts`: 10,142건
  - 기간: 2021-05-31 ~ 2026-07-24
  - `parse_ok`: 6,809건
  - `verified`: 0건
- `verified=0`은 정상이다. DART 개별 공시 파싱은 서식 편차가 있어 프론트 검증 워크플로가 필요하다.

## 검증 결과
- Python compile 통과:
  - `collectors/dart_collector.py`
  - `routes/order_contracts.py`
  - `collect_order_contracts.py`
  - `scheduler.py`
  - `main.py`
- Frontend build 통과:
  - `npm run build`
- `main.app.routes`에서 `/api/order-contracts/*` 7개 경로 등록 확인.
- `GET /api/order-contracts/screener/surge` 직접 함수 호출 결과:
  - 최근 3개월 vs 직전 3개월, 50% 이상 급증 후보 204건.
  - 상위 예: 차AI헬스케어, 데이타솔루션, 디아이, 라이콤, 스마트레이더시스템.

## 추가 확인 필요
- `collect_order_contracts.py --codes 307950 --months 1` 테스트에서 공시 2건을 정상 스캔했으나, DART 전체 목록 조회가 5,000건 페이지 한도에 걸렸다.
  - 월 단위 전체 목록 조회는 누락 위험이 있다.
  - 역사 백필은 종목별 월 단위 반복보다 `dart_contracts` 기존 수집기 또는 일/주 단위 청크 수집 후 `order_contracts`로 동기화하는 방식이 낫다.
- `order_contracts`는 현재 `dart_contracts`에서 seed 했으므로 즉시 화면 계산은 가능하지만, 장기적으로는 `dart_contracts → order_contracts` 동기화 함수/잡을 명시적으로 두는 것이 좋다.
- 상위 급증 후보 중 `recent_to_revenue_pct`가 500% 이상으로 뜨는 종목은 대형 단일계약/매출 기준 불일치/파싱 오류 가능성이 있으니 `verified` 검증 우선순위를 높여야 한다.

## 2026-07-25 추가 보강
- `scripts/audit_order_contracts_proxy.py` 추가.
  - 매일 자동 태스크 `수주잔고 급증 proxy 매일 검증`이 19:20 실행.
  - `dart_contracts → order_contracts` 동기화, 최신성, parse_ok, 급증 후보 계산, 비표준 종목코드 혼입을 점검.
  - 결과는 `research_outputs/order_contracts_proxy_audit_YYYYMMDD.md/json`.
- 비표준 DART 코드 정리.
  - `0008Z0` 같은 비 6자리 숫자 코드 15건이 `order_contracts`에 들어온 것을 발견.
  - 자동 검증 스크립트에서 동기화 시 6자리 숫자 코드만 반영하고 기존 비표준 코드는 삭제하도록 수정.
  - 현재 `order_contracts`: 10,127건, 비표준 코드 0건.
- 전략 점수 연결.
  - `signal_engine._load_order_contract_surge_bonus_map(window_months=3)` 추가.
  - 최근 3개월 신규계약 합계 vs 직전 3개월 증가율, 매출 대비 비중, 공시 건수, 검증 여부를 기반으로 `bonus`, `label`, `growth_pct`, `recent_to_revenue_pct`를 반환.
  - `tenbagger_engine` 촉매 점수에 `수주급증` 보너스 연결.
  - `routes/tenbagger.py` v3 스크리너에도 `order_surge_bonus`, `order_surge` 필드 추가.
  - `routes/order_contracts.py` screener 응답에 `signal_score`, `needs_verification`, `verified_count`, `parse_ok_count` 추가.
  - `frontend/src/App.jsx` 수주잔고 급증 테이블에 `중요도`, `검증` 컬럼 추가.

## 2026-07-26 추가 수정
- `collectors/dart_contract_collector.py`
  - `해지금액(원)` 라벨도 계약금액으로 인식하도록 파서 확장.
  - `주권매매거래정지 (단일판매공급계약)` 같은 안내성 공시는 `EXCLUDE_KEYWORDS`에 `거래정지`를 추가해 proxy 대상에서 제외.
- `scripts/audit_order_contracts_proxy.py`
  - 감사 항목을 5개 요구사항 기준으로 확장:
    - `dart_contracts ↔ order_contracts` sync ratio 명시
    - 최근 7일 vs 직전 7일 `parse_ok`/금액누락 추세 비교
    - 최근 누락 사유(`document_014`, `undisclosed_dash`, 기타 파싱 실패) 분해
    - `/api/order-contracts/screener/surge` 계산 가능 여부 확인
    - 계약 수집 경로가 `kind='I'` 직접 필터가 아니라 `list.json` 페이지 순회인지 코드 기준 점검
  - 최근 14일 데이터 중 `raw_snippet`만으로 복구 가능한 parse miss를 자동 재파싱해 보정.
  - `dart_contracts → order_contracts` 동기화 시 `거래정지` 성격의 비계약 공시는 seed 대상에서 제외하고 기존 적재분도 삭제.
- 2026-07-26 감사 결과
  - `order_contracts`: 10,032건, `dart_contracts`: 10,152건, sync ratio 98.82%.
  - 최근 7일 `order_contracts` 최신일자는 2026-07-24이고 최근 7일 데이터 47건 존재.
  - 자동 복구로 `유디엠텍(20260720900656)`, `코오롱글로벌(20260722800676)` 2건의 `해지금액` 누락 복구.
  - 누락 4건은 모두 설명 가능:
    - `document_014` 2건: DART 원문 자체가 `014 파일이 존재하지 않습니다.`
    - `undisclosed_dash` 2건: 공시 본문상 `계약금액 총액(원) -`
  - `주권매매거래정지` 오분류 95건 삭제 후 최근 7일 `parse_ok`는 91.49%, 최근 금액누락률은 8.51%.
  - `/api/order-contracts/screener/surge` 후보 201건으로 계산 정상.

## 2026-08-07 추가 수정
- `collectors/dart_contract_collector.py`
  - `투자판단관련주요경영사항` 형태의 기술이전/라이선스 계약에서 `계약 금액`처럼 띄어쓰기된 라벨과 `최대 USD 365,000,000 (약 5,219억 원)` 같은 외화+원화 병기 패턴을 파싱하도록 확장.
  - 외화 금액은 임의 환율로 계산하지 않고, 공시 본문에 함께 적힌 `약 N억 원` 원화 환산값을 우선 사용.
- `scripts/audit_order_contracts_proxy.py`
  - 최근 누락 사유 분류에 `비공개 조항`/`계약금액 비공개`를 `undisclosed`로 추가해, 설명 가능한 미공시 금액을 `other_parse_miss`와 분리.
  - 최근 14일 자동 복구에서도 같은 `비공개` 케이스는 파싱 실패가 아닌 설명 가능한 누락으로 건너뛰도록 조정.

## 2026-08-10 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260810.md`
  - `research_outputs/order_contracts_proxy_audit_20260810.json`
- 결과 요약:
  - `order_contracts`: 10,107건, `dart_contracts`: 10,230건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-07이며 최근 7일 데이터 36건 존재.
  - 전체 `parse_ok` 67.99%, 최근 7일 `parse_ok` 94.44%로 유지.
  - 최근 7일 금액 누락 2건은 모두 설명 가능:
    - `20260803901043 / SG`: DART 원문 `014 파일이 존재하지 않습니다.`
    - `20260807900139 / 리가켐바이오`: 공시 본문상 계약금액 비공개(`undisclosed`) 성격
  - `/api/order-contracts/screener/surge` 후보 211건 계산 정상.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없음.
- 참고:
  - `dart_contracts`와 `order_contracts` 건수 차이 123건은 현재 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건에서 발생했다.
  - 오늘 감사에서는 critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-11 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260811.md`
  - `research_outputs/order_contracts_proxy_audit_20260811.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 감사 실행 중 `dart_contracts → order_contracts` 12건 자동 동기화가 반영됐고 삭제/자동복구는 없었다.
  - 최근 수신일은 2026-08-10이며 최근 7일 데이터 36건 존재.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 97.22%로 유지.
  - 최근 7일 금액 누락 1건은 공시 본문상 비공개(`undisclosed`)라 설명 가능했고, 급증 징후는 없었다.
  - `/api/order-contracts/screener/surge` 후보 215건 계산 정상.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없음.
- 참고:
  - 오늘도 `dart_contracts`와 `order_contracts` 건수 차이 123건은 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-12 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260812.md`
  - `research_outputs/order_contracts_proxy_audit_20260812.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 28건이 있어 최신성 기준을 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 96.43%로 유지됐다.
  - 최근 7일 금액 누락 1건은 공시 본문상 비공개(`undisclosed`)라 설명 가능했고, 누락 급증 징후는 없었다.
  - `/api/order-contracts/screener/surge` 후보 213건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘은 `dart_contracts → order_contracts` 자동 동기화 추가 반영, 삭제, 최근 파싱 복구 모두 0건이었다.
  - 건수 차이 123건은 기존과 동일하게 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-13 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260813.md`
  - `research_outputs/order_contracts_proxy_audit_20260813.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 26건이 있어 최신성 기준을 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 96.15%로 전일 대비 소폭 하락했지만 여전히 안정 범위였다.
  - 최근 7일 금액 누락 1건은 공시 본문상 비공개(`undisclosed`)라 설명 가능했고, 최근 14일 자동 복구 대상도 없었다.
  - `/api/order-contracts/screener/surge` 후보 211건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘은 `dart_contracts → order_contracts` 자동 동기화 추가 반영, 삭제, 최근 파싱 복구 모두 0건이었다.
  - 건수 차이 123건은 기존과 동일하게 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-14 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260814.md`
  - `research_outputs/order_contracts_proxy_audit_20260814.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 19건이 있어 최신성 기준을 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 94.74%였고 전주 대비 -2.48%p였지만 경고 기준에는 미달했다.
  - 최근 7일 금액 누락 1건은 `undisclosed` 성격으로 분류됐고, 최근 14일 자동 복구는 0건이었다.
  - `/api/order-contracts/screener/surge` 후보 207건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘도 `dart_contracts → order_contracts` 자동 동기화 추가 반영, 삭제, 최근 파싱 복구 모두 0건이었다.
  - 건수 차이 123건은 기존과 동일하게 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-15 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260815.md`
  - `research_outputs/order_contracts_proxy_audit_20260815.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 12건이 있어 최신성 기준을 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 100.0%로 전주 대비 +5.56%p 개선됐다.
  - 최근 7일 금액 누락은 0건이었고, 감사 실행 중 설명 가능한 누락 2건(`undisclosed` 1건, `document_014` 1건)은 자동 복구 대상에서 제외됐다.
  - `/api/order-contracts/screener/surge` 후보 207건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘은 `dart_contracts → order_contracts` 자동 동기화 추가 반영, 삭제, 최근 파싱 복구 모두 0건이었다.
  - 건수 차이 123건은 기존과 동일하게 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-16 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260816.md`
  - `research_outputs/order_contracts_proxy_audit_20260816.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 12건이 있어 최신성 기준을 계속 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 100.0%로 유지됐고 최근 7일 금액 누락은 0건이었다.
  - 감사 실행 중 자동 동기화 추가 반영, 삭제, 최근 파싱 복구는 모두 0건이었고, 설명 가능한 누락 2건(`undisclosed` 1건, `document_014` 1건)은 자동 복구 대상에서 제외됐다.
  - `/api/order-contracts/screener/surge` 후보 207건 계산이 정상 동작했고 상위 샘플도 전일과 일관됐다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘도 건수 차이 123건은 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-17 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260817.md`
  - `research_outputs/order_contracts_proxy_audit_20260817.json`
- 결과 요약:
  - `order_contracts`: 10,119건, `dart_contracts`: 10,242건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-10이고 최근 7일 데이터 12건이 있어 최신성 기준을 계속 충족했다.
  - 전체 `parse_ok` 68.03%, 최근 7일 `parse_ok` 100.0%였고 최근 7일 금액 누락은 0건이었다.
  - 감사 실행 중 자동 동기화 추가 반영, 삭제, 최근 파싱 복구는 모두 0건이었고, 설명 가능한 누락 2건(`undisclosed` 1건, `document_014` 1건)은 자동 복구 대상에서 제외됐다.
  - `/api/order-contracts/screener/surge` 후보 200건 계산이 정상 동작했고 상위 샘플도 전일과 일관된 범주였다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘도 건수 차이 123건은 정책상 제외 대상인 `거래정지` 95건과 비표준 코드 28건으로 설명된다.
  - `verified`는 1건으로 집계됐지만 감사 경고 기준에는 영향이 없었다.
  - critical/warning 이슈가 없어 코드 수정은 하지 않았다.

## 2026-08-22 감사 결과 및 수정
- 장애 원인:
  - `order_contracts`와 `dart_contracts` 최신일자가 모두 2026-08-10에 멈춰 있었다.
  - 원천 DART에는 2026-08-19, 2026-08-20, 2026-08-21 계약공시가 다수 존재했으므로 소스 부재가 아니라 로컬 수집 누락이었다.
  - 기존 수집 경로는 사실상 "오늘자만" 스캔해 서버/스케줄러 공백일이 생기면 누락 일자를 자동 복구하지 못했다.
  - 최근 12일 범위를 한 번에 조회하면 `list.json` 5,000건 안전 한도에 걸려 페이지 초과가 발생했다.
- 코드 수정:
  - `collectors/dart_contract_collector.py`
    - `collect_dart_contracts_catchup()` 추가.
    - 최근 마지막 `dart_contracts.disclosed_at` 다음 날부터 오늘까지 최대 14일을 일자별 청크로 복구한다.

## 2026-08-29 감사 결과 및 수정
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260829.md`
  - `research_outputs/order_contracts_proxy_audit_20260829.json`
- 코드 수정:
  - `db_compat.py`
    - `NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'` / `NOT GLOB '[0-9]*'`를 PostgreSQL에서 각각 `!~ '^[0-9]{6}$'` / `!~ '^[0-9]'`로 번역하도록 보강.
    - `date('now', ?)` 형태의 SQLite 동적 interval 인자를 `CURRENT_DATE + (%s)::interval`로 번역하도록 추가.
    - `rcept_dt >= date('now','-7 day')` 같은 최근성 비교가 `YYYY-MM-DD` 텍스트를 `YYYYMMDD`와 직접 비교해 0건으로 잘못 집계되던 문제를 수정. 이제 `REPLACE(SUBSTR(col::text,1,10),'-','')` 기준으로 비교해 `YYYYMMDD` / `YYYY-MM-DD` 저장 형식을 모두 허용한다.
- 결과 요약:
  - 최초 감사 실패 원인은 데이터 이상이 아니라 SQLite→PostgreSQL 호환 레이어 번역 버그였다.
  - 수정 후 감사는 `exit 0`으로 통과했다.
  - `order_contracts`: 10,224건, `dart_contracts`: 10,347건, sync ratio 98.81%.
  - 최신 `order_contracts` 일자는 2026-08-28이고 최근 7일 데이터 41건이 확인됐다.
  - 전체 `parse_ok` 68.3%, 최근 7일 `parse_ok` 95.12%, 최근 7일 금액 누락 2건(`document_014` 1건, `other_parse_miss` 1건)으로 급증 경고 기준에는 미달했다.
  - `/api/order-contracts/screener/surge` 후보 204건으로 계산 정상.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 `recent_collect.source_latest_date`는 `None`, `scanned/saved`는 0이었다. 이는 2026-08-29 당일 신규 계약공시를 찾지 못했다는 의미이며, 최신 적재일 2026-08-28 및 최근 7일 41건 존재와는 별개다.
  - 오늘 감사는 코드 수정 후 warning/critical 없이 종료됐다.

## 2026-08-23 감사 결과 및 추가 보강
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260823.md`
  - `research_outputs/order_contracts_proxy_audit_20260823.json`
- 결과 요약:
  - `order_contracts`: 10,156건, `dart_contracts`: 10,279건, sync ratio 98.8%.
  - 최근 수신일은 2026-08-21이고 최근 7일 데이터 35건이 있어 최신성 기준을 충족했다.
  - 최근 7일 `parse_ok` 97.14%, 최근 7일 금액 누락 1건으로 급격한 품질 저하는 없었다.
  - `/api/order-contracts/screener/surge` 후보 199건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 추가 보강:
  - `collectors/dart_contract_collector.py`
    - 계약공시용 DART 키를 `DART_API_KEY2 → DART_API_KEY → DART_API_KEY3` 순으로 순차 재시도하도록 수정했다.
    - `list.json`과 `document.xml` 모두 동일 fallback을 적용해 특정 키 quota 초과가 곧바로 0건 스캔/원문 미수집으로 이어지지 않게 했다.
- 참고:
  - 2026-08-23은 일요일이라 신규 계약공시 부재 가능성이 높았고, 감사 실행 시 등록된 DART 키가 모두 `사용한도를 초과하였습니다.` 상태였다.
  - fallback 후에도 전체 키가 모두 quota면 감사는 기존 로컬 데이터 기준으로 계속 진행되며, 오늘은 critical/warning 이슈 없이 `OK`로 종료됐다.
  - `scheduler.py`
    - `DART수주공시` 잡이 `collect_dart_contracts(days=1)` 대신 `collect_dart_contracts_catchup(max_backfill_days=14)`를 사용하도록 변경.
  - `collectors/dart_collector.py`
    - `get_contract_disclosures_range(start, end)` / `_fetch_contract_range_sync()` 추가.
  - `routes/order_contracts.py`
    - `collect_recent_disclosures()` 추가.
    - `/collect/today`가 실제로는 최근 마지막 `order_contracts.rcept_dt` 다음 날부터 오늘까지 최대 14일을 일자별 청크로 캐치업하도록 변경.
  - `scripts/audit_order_contracts_proxy.py`
    - 감사 시작 시 `collect_recent_disclosures(max_backfill_days=14)`를 먼저 실행하도록 변경.
    - `STALE_ORDER_CONTRACTS`는 고정 7일 규칙이 아니라 "DART source 최신일자 > order_contracts 최신일자"일 때만 경고하도록 수정.
    - 보고서에 `Recent Collect` 섹션을 추가했다.
- 2026-08-22 최종 결과:
  - `dart_contracts`: 10,279건, 최신일자 2026-08-21.
  - `order_contracts`: 10,156건, 최신일자 2026-08-21.
  - sync ratio 98.8% 유지.
  - 최근 7일 데이터 35건 존재.
  - 최근 7일 `parse_ok` 97.14%(34/35), 금액 누락 1건.
  - `/api/order-contracts/screener/surge` 후보 198건으로 계산 정상.
  - 수집 방식 점검 결과 `list.json` 페이지 순회 유지, `kind='I'` 회귀 없음.
  - 감사 명령은 `order_contracts proxy audit OK`로 종료했고 `research_outputs/order_contracts_proxy_audit_20260822.md/json`가 갱신됐다.

## 2026-08-24 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260824.md`
  - `research_outputs/order_contracts_proxy_audit_20260824.json`
- 결과 요약:
  - `order_contracts`: 10,170건, `dart_contracts`: 10,279건, sync ratio 98.94%.
  - 감사 실행 중 최근 공시 14건이 `20260822~20260824` 범위에서 자동 백필됐고 `order_contracts` 최신일자는 2026-08-24로 갱신됐다.
  - 최근 7일 데이터 49건이 존재해 최신성 기준을 충족했고 최근 7일 `parse_ok`는 97.96%(48/49)였다.
  - 최근 7일 금액 누락은 1건(2.04%)으로 급증 징후는 없었고 `missing_reason_breakdown_7d`도 `other_parse_miss` 1건만 남았다.
  - `/api/order-contracts/screener/surge` 후보 205건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 감사의 `Issues`는 `[info] BACKFILLED_RECENT_ORDER_CONTRACTS` 1건뿐이었고 warning/critical은 없었다.
  - 따라서 코드 수정은 하지 않았고 산출물과 운영 문서만 갱신했다.

## 2026-08-25 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260825.md`
  - `research_outputs/order_contracts_proxy_audit_20260825.json`
- 결과 요약:
  - `order_contracts`: 10,176건, `dart_contracts`: 10,279건, sync ratio 99.0%.
  - 감사 실행 중 `20260825~20260825` 범위 최근 공시 6건이 자동 백필됐고 `order_contracts` 최신일자는 2026-08-25로 갱신됐다.
  - 최근 7일 데이터 55건이 존재해 최신성 기준을 충족했고 최근 7일 `parse_ok`는 96.36%(53/55)였다.
  - 최근 7일 금액 누락은 2건(3.64%)으로 급증 수준은 아니었고 분해 결과는 `document_014` 1건, `other_parse_miss` 1건이었다.
  - `/api/order-contracts/screener/surge` 후보 201건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 감사의 `Issues`는 `[info] BACKFILLED_RECENT_ORDER_CONTRACTS` 1건뿐이었고 warning/critical은 없었다.
  - 따라서 코드 수정은 하지 않았고 산출물과 운영 문서만 갱신했다.

## 2026-08-26 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260826.md`
  - `research_outputs/order_contracts_proxy_audit_20260826.json`
- 결과 요약:
  - `order_contracts`: 10,185건, `dart_contracts`: 10,279건, sync ratio 99.09%.
  - 감사 실행 중 `20260826~20260826` 범위 최근 공시 9건이 자동 백필됐고 `order_contracts` 최신일자는 2026-08-26으로 갱신됐다.
  - 최근 7일 데이터 53건이 존재해 최신성 기준을 충족했고 최근 7일 `parse_ok`는 96.23%(51/53)로 전주 92.31%(12/13) 대비 개선됐다.
  - 최근 7일 금액 누락은 2건(3.77%)으로 전주 7.69% 대비 낮아졌고 분해 결과는 `document_014` 1건, `other_parse_miss` 1건이었다.
  - `/api/order-contracts/screener/surge` 후보 199건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 감사의 `Issues`는 `[info] BACKFILLED_RECENT_ORDER_CONTRACTS` 1건뿐이었고 warning/critical은 없었다.
  - 따라서 코드 수정은 하지 않았고 산출물과 운영 문서만 갱신했다.

## 2026-08-27 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260827.md`
  - `research_outputs/order_contracts_proxy_audit_20260827.json`
- 결과 요약:
  - `order_contracts`: 10,191건, `dart_contracts`: 10,279건, sync ratio 99.14%.
  - 감사 실행 중 `20260827~20260827` 범위 최근 공시 6건이 자동 백필됐고 `order_contracts` 최신일자는 2026-08-27로 갱신됐다.
  - 최근 7일 데이터 52건이 존재해 최신성 기준을 충족했고 최근 7일 `parse_ok`는 96.15%(50/52)로 전주 95.0%(19/20) 대비 소폭 개선됐다.
  - 최근 7일 금액 누락은 2건(3.85%)으로 급증 징후는 없었고 분해 결과는 `document_014` 1건, `other_parse_miss` 1건이었다.
  - `/api/order-contracts/screener/surge` 후보 201건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 감사의 `Issues`는 `[info] BACKFILLED_RECENT_ORDER_CONTRACTS` 1건뿐이었고 warning/critical은 없었다.
  - 따라서 코드 수정은 하지 않았고 산출물과 운영 문서만 갱신했다.

## 2026-08-28 감사 결과
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 산출물:
  - `research_outputs/order_contracts_proxy_audit_20260828.md`
  - `research_outputs/order_contracts_proxy_audit_20260828.json`
- 결과 요약:
  - `order_contracts`: 10,196건, `dart_contracts`: 10,279건, sync ratio 99.19%.
  - 감사 실행 중 `20260828~20260828` 범위 최근 공시 5건이 자동 백필됐고 `order_contracts` 최신일자는 2026-08-28로 갱신됐다.
  - 최근 7일 데이터 48건이 존재해 최신성 기준을 충족했고 최근 7일 `parse_ok`는 95.83%(46/48)로 안정 범위였다.
  - 최근 7일 금액 누락은 2건(4.17%)으로 전주 대비 +0.72%p였고 분해 결과는 `document_014` 1건, `other_parse_miss` 1건이었다.
  - `/api/order-contracts/screener/surge` 후보 199건 계산이 정상 동작했다.
  - 수집 방식 점검 결과 `collectors/dart_collector.py`는 계속 `list.json` 페이지 순회를 사용하며 `kind='I'` 직접 필터 회귀는 없었다.
- 참고:
  - 오늘 감사의 `Issues`는 `[info] BACKFILLED_RECENT_ORDER_CONTRACTS` 1건뿐이었고 warning/critical은 없었다.
  - 따라서 코드 수정은 하지 않았고 산출물과 운영 문서만 갱신했다.

## 2026-09-01 감사 결과 및 수정
- 감사 명령:
  - `/Applications/stock_dashboard/venv/bin/python scripts/audit_order_contracts_proxy.py`
- 발견 이슈:
  - 감사 1차 실행 중 `20260901900312` 처리에서 `could not convert string to float: '원'` 저장 오류가 1건 발생했다.
  - 원인은 `collectors/dart_contract_collector.py` 금액 파서의 정규식 그룹 순서 가정 오류였다.
  - `계약 금액 ... 123 원` 형태 패턴은 `숫자 -> 단위` 순서인데, 기존 코드는 항상 `1번 그룹=단위`, `2번 그룹=숫자`로 읽어 `float('원')`가 가능했다.
- 코드 수정:
  - `collectors/dart_contract_collector.py`
    - 계약금액 파서에서 named group(`num`, `unit`, `post_unit`)을 사용하도록 수정.
    - 단위 후행 패턴(`숫자 + 단위`)도 동일한 방식으로 읽도록 정리해 그룹 순서 의존성을 제거.
- 재검증 결과:
  - `py_compile` 통과.
  - 감사 재실행 후 저장 오류는 재현되지 않았고 `BACKFILLED_RECENT_ORDER_CONTRACTS` info와 함께 당일 누락 1건이 `order_contracts`에 반영됐다.
  - 최종 산출물:
    - `research_outputs/order_contracts_proxy_audit_20260901.md`
    - `research_outputs/order_contracts_proxy_audit_20260901.json`
  - 최종 지표:
    - `dart_contracts`: 10,370
    - `order_contracts`: 10,252
    - sync ratio: 98.86%
    - 최신일자: 2026-09-01
    - 최근 7일 rows: 54
    - 최근 7일 `parse_ok`: 90.74% (49/54)
    - 최근 7일 금액누락: 9.26% (5/54)
    - surge candidates: 202
- 참고:
  - 최근 7일 `parse_ok` 하락(-7.26%p)과 금액누락 증가(+7.26%p)는 경고 임계치(±15%p, +10%p) 이내라 자동 경고는 발생하지 않았다.
  - 최근 누락 5건 중 설명 가능한 항목은 `document_014` 1건, `undisclosed` 1건이며, 미설명 parse miss 3건은 다음 감사에서도 추세를 계속 볼 필요가 있다.
