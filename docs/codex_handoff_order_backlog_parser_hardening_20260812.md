# Codex 핸드오프 — 수주잔고(order_backlog) 텍스트파서 잔여 이상치 1,875건 (2026-08-12)

## 배경

2026-08-09~08-12 세션(Claude)에서 재무제표/현금흐름표 파서 3곳(`dart_collector.py`/
`fnguide_financial_collector.py`/`legacy_dart_recollect.py`)의 계정 오매칭 버그를 다수
발견·수정하고, `financial_data.cash`/`eps`/`cash_flow_data.capex`/`depreciation`을
전종목 백필했다(SQLite+PostgreSQL 양쪽 반영 완료, 상세는 CLAUDE.md 섹션11 2026-08-09~
2026-08-12 항목 참조). 사용자가 이어서 "계약부채, 수주잔고 등에 대해서도 재검증"을
지시해 확인한 결과:

- **계약부채/선수금**(`scripts/build_contract_advance_signals.py`,
  `scripts/collect_dart_report_items.py`): account_id 우선 + 정확한 계정명 집합
  (`TOTAL_NAMES`) 매칭이라 구조적으로 안전. 현대건설·삼성중공업 실측 대조 확인 완료,
  **추가 조치 불필요**.
- **수주잔고**(`collectors/dart_backlog_collector.py`, `order_backlog`/
  `dart_backlog_quarterly` 테이블): 규칙기반 텍스트(정규식) 파서라 위와 근본적으로
  다른 종류의 문제. **여기가 이 핸드오프의 대상**.

## 발견한 문제의 본질

`_extract_backlog()`는 DART 공시 **원문 텍스트**(재무제표처럼 표준화된 XBRL 태그가
없는 자유서술 "주요 계약 현황"/"수주현황" 섹션)에서 정규식으로 "수주잔고" 류 키워드
근처의 숫자를 찾는다. 회사마다 표 구조가 완전히 다르고, 문서 안에 **전혀 무관한
숫자**(각주번호, 목록번호, 다른 회계노트의 값)가 키워드 근처에 우연히 섞여 있어 오채택
위험이 상시적이다.

**전종목 스캔 결과**: 같은 종목의 인접 분기 대비 20배 이상 급변하는 값이
**1,957건/13,951행(14%)**. 이번 세션에서 그중 정확히 "1,000,000원"(=1×백만원, 각주번호
"1"이 숫자로 오인식된 명백한 오염)인 284건을 찾아 조사 → **최종 82건만 해소**(30건은
정답 발견, 54건은 정정, 나머지는 NULL 처리 후 이상치 계산에서 자연히 빠짐) —
**잔여 1,875건은 미분류 상태로 남아있다.**

## 이번 세션에서 이미 고친 것 (재작업 불필요)

`collectors/dart_backlog_collector.py`에 다음 3개 방어 로직이 이미 추가돼 있다:

1. **`_is_footnote_marker(t, start, end)`**: 캡처된 숫자가 "주1)"/"(*1)"/"*1)"/"(1)"/
   "[1)" 류 각주·목록 참조번호인지 판별(콤마 포함·3자리 이상 숫자는 절대 각주가
   아니므로 자동 제외). base 패턴 루프(line ~337 근처)와 1-b/1-c 블록 양쪽에 적용됨.
2. **1-d 신규 블록**: "수주총액...매출인식액...수주잔고" 3개 헤더가 이 순서로 함께
   나오는 IFRS15 잔여이행의무 표준 공시 표를 전용 탐지 — 데이터행의 3번째 숫자(수주
   잔고 열)를 confidence 0.95로 추출. 010420 실측 검증 완료(23,803,708천원/
   20,993,171천원, 두 시점 모두 정상 시계열 확인).
3. 기존 문서화된 정상 케이스(HD한국조선해양 009540 89.09조/유진테크 084370 956.5억/
   090470 1287.96억) 전부 회귀 검증 통과.

**284건 백필 완료**: SQLite `dart_backlog_quarterly`/`order_backlog` 양쪽 반영,
PostgreSQL도 직접 재동기화 완료(30건 정정값 + 254건 NULL 동기화). **더 이상
"1,000,000원" 값은 DB에 존재하지 않는다** — 재확인 필요 없음.

## 남은 작업 — 잔여 1,875건 분류 및 수정

### 방법론(이번 세션에서 검증된, 계속 재사용할 절차)

1. `scripts/`에 임시 스크립트로 다음 쿼리를 돌려 20배 이상 급변 쌍을 뽑는다(이미
   이번 세션에서 1회 실행한 로직, 재사용 가능):
   ```python
   rows = conn.execute("""
       SELECT stock_code, fiscal_year, fiscal_quarter, backlog_amount_krw
       FROM dart_backlog_quarterly
       WHERE backlog_amount_krw IS NOT NULL AND backlog_amount_krw > 0
       ORDER BY stock_code, fiscal_year, fiscal_quarter
   """).fetchall()
   # 인접 분기 대비 ratio = max(cur/prev, prev/cur) > 20 인 쌍을 이상치로 분류
   ```
2. 이상치 표본 10~20건을 골라 `source_rcept_no`로 DART 원문을 직접 열어본다:
   ```python
   from collectors.dart_backlog_collector import _fetch_document_with_key_rotation, _extract_backlog
   text = _fetch_document_with_key_rotation(rcept_no)
   result = _extract_backlog(text)
   print(result.source_excerpt)  # 어느 후보가 채택됐는지 근거 확인
   ```
3. **판별 기준**: 실제 원문에서 해당 회사의 진짜 "수주총액/수주잔고" 표를 찾아
   수기로 정답을 확인한 뒤, ①진짜 정상적인 사업 이벤트(대형 신규수주 등)로 20배가
   실제로 맞는지, ②파서가 여전히 엉뚱한 숫자를 채택하고 있는지 구분한다.
4. 새로운 오염 패턴을 발견하면 `_extract_backlog()`에 전용 방어/블록을 추가하고,
   **반드시 기존 정상 케이스(위 4개 회귀 케이스 + 010420 3열표 + 003030 세아제강은
   의도적으로 None) 전부 재검증한 뒤** 배포한다.
5. 수정 후 **SQLite와 PostgreSQL 양쪽에 반영**할 것(아래 "중요 — Postgres 마이그레이션
   주의사항" 참조) — 이 프로젝트는 8월 10~11일경부터 PostgreSQL로 실제 라이브서버가
   이관됐다.

### 참고 — "합계" 우선탐색은 이미 시도했다가 철회된 접근이다

코드 주석(line ~319)에 남아있듯, "합계 NUMBER" 명시적 총계 우선탐색을 이전에 시도했으나
SK오션플랜트(100090)의 이자율스왑 파생상품 헤지테이블 "합 계 5,754,804"를 오채택하는
새 버그를 만들어 철회한 이력이 있다. **동일한 시도를 반복하지 말 것** — "합계"는 이
문서군에서 수주잔고와 무관한 표에도 매우 흔히 등장한다.

## 중요 — PostgreSQL 마이그레이션 주의사항 (이 세션에서 처음 발견)

이 프로젝트는 세션 도중(다른 세션/Codex 작업으로 추정, 정확한 주체는 미확인)
PostgreSQL로 실제 라이브서버 데이터소스가 이관됐다:

- `config.IS_POSTGRES` 확인 결과 현재 `True`. `serve_foreground.sh`가 `PYTHONPATH`에
  `runtime_pg_bootstrap`을 넣어 라이브서버의 모든 파이썬 프로세스에서 `sqlite3.connect()`
  자체를 PostgreSQL로 투명 리다이렉트한다(`db_compat.py`의
  `install_sqlite_primary_router()`).
- **`scripts/sync_tenbagger_postgres.py`(30분마다 자동 실행)가 `dart_backlog_quarterly`/
  `order_backlog`을 포함한 여러 테이블을 SQLite→Postgres로 동기화하지만, `WHERE
  fiscal_year >= 현재연도-3` 조건이 있어 **3년보다 오래된 데이터는 절대 자동
  동기화되지 않는다.** SQLite만 고치고 끝내면 라이브서버(Postgres)에는 영원히
  반영 안 됨 — 이번 세션에서 이 함정에 두 번(cash/eps 백필, order_backlog 백필)
  걸렸다.**
- **재발방지**: SQLite를 직접 백필한 뒤에는 반드시 `psycopg`로 PostgreSQL에 직접
  접속해(`postgresql://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard`)
  같은 값을 UPDATE하는 재동기화를 별도로 수행할 것. 또는 애초에 백필 스크립트
  자체에 `from db_compat import install_sqlite_primary_router; install_sqlite_primary_router()`를
  추가해 SQLite 경유 없이 Postgres에 직접 쓰도록 전환하는 편이 더 안전하다(이번
  세션 후반 capex/depreciation 백필은 이 방식으로 전환해 문제 없었음).
- `db_compat.py`의 `PostgresCompatCursor`는 SQLite 전용 문법(GLOB, HAVING의 SELECT
  별칭 참조 등)을 지원하지 않는 경우가 있고, `lastrowid`는 이번 세션에서 새로
  구현했다(INSERT 시 자동으로 `RETURNING id` 부착, "id"라는 정수 PK 컬럼이 있는
  테이블에서만 동작). 새 SQL을 짤 때 이 호환레이어의 지원 범위를 넘어서면 조용히
  깨질 수 있으니, **작성 후 `install_sqlite_primary_router()` 활성 상태에서 실제
  실행 검증** 없이 "동작한다"고 가정하지 말 것.

## 참고 — 그 외 이번 세션에서 발견했지만 이 핸드오프 범위 밖인 항목

- `financial_data.net_income`을 "총계"로 할지 "지배주주분"으로 할지 정책 결정 —
  ROE 계산 등 광범위 영향이 있어 사용자 승인 대기 중(CLAUDE.md 2026-08-09(추가6)
  참조). **이 핸드오프에서는 손대지 말 것.**
- `capital_stock`/`bps`/`dps`는 6~13개사 표본으로만 확인(안전 판단), `legacy_dart_recollect.py`도
  일부만 검증(net_income/operating_profit/revenue exclude, depreciation 합산만
  수정) — 전수 재검증은 아직 안 됨.
- 매일 밤 03:15 실행되는 `FnGuideDART전종목검증`(`scripts/verify_all_fnguide_dart_20260809.py`)이
  revenue/operating_profit/net_income/total_assets/total_equity/cash 6개 필드만
  자동 교차검증한다 — capex/depreciation/order_backlog는 이 자동검증 대상이 아니라서,
  이번에 고친 것들이 다시 깨져도 자동으로는 안 잡힌다. `fnguide_dart_mismatch_log`
  테이블(Postgres)로 매일 누적 추이 확인 가능.

## Codex 후속 완료 결과 (2026-08-12 22:26 KST)

- `backlog_v3` 파서를 구현하고 DART 원문 10개 표본 및 단위/파생상품/계약자산/각주/
  반복 수량·금액 표 회귀 테스트 8개를 통과했다. 단위가 비어 있는 `(단위 :)` 표는
  더 이상 고신뢰로 분류하지 않는다.
- 엄격한 인접분기 기준 재감사 결과 이상쌍은 1,653건에서 1,311건으로 342건(20.7%)
  감소했다. 나머지는 근거 없이 값을 지우지 않고 검토 대상으로 보존했다.
- `research_outputs/order_backlog_v3_backfill_20260812_221700.json`의 고정 보고서를 기준으로
  update 735, clear 36, metadata 720을 PostgreSQL과 SQLite에 동일 반영했다.
- 최종 교차검증은 양쪽 DB 각각 1,491행 검사, missing 0, mismatch 0이다.
- 원본 증거 테이블 `dart_backlog_quarterly`는 보존하고, 레거시 `order_backlog`는 신뢰도
  0.85 이상이며 고신뢰 인접분기끼리 20배를 넘지 않는 값만 제공하는 운영 투영본으로
  전환했다. 양쪽 DB 모두 원본 13,706행 중 운영 허용 3,632행으로 동일하다.
- 같은 기준으로 `dart_tenbagger_triggers_quarterly`를 전량 재구축했다. 양쪽 DB 모두
  3,408건이며, 저신뢰 9,366행과 불연속 932행은 매매 신호에서 제외된다.
- 월간 후보, 텐버거 엔진, 트리거 연구/오버레이 경로에도 동일한 최소 신뢰도와 20배
  연속성 기준을 적용했다. 새 수집 시 운영 투영본도 자동 갱신된다.
- 검증/재구축: `scripts/verify_order_backlog_v3_cutover.py`. 최종 기록:
  `research_outputs/order_backlog_v3_cutover_verification.json`.
- 복원: `scripts/restore_order_backlog_v3_backfill.py --report <고정보고서> --apply`.
  기본 실행은 dry-run이며 `--database postgres|sqlite|both`로 범위를 제한할 수 있다.
- 잔여 1,311 이상쌍은 자동 매매 입력이 아니다. 다음 연구는 다중 사업부 표의 총계 범위와
  단위 누락 공시를 기업별 시계열로 교차확인하되, 검증 전 미래 예측 로직에는 넣지 않는다.

## Codex 최종 강화 결과 (2026-08-12 22:59 KST)

- 반복표 경계를 다음 사업부·다음 단위 선언·다음 공시 섹션 앞에서 자르고, 기초/증감/수익/
  기말 표는 `합계` 행의 완전한 네 번째 숫자를 읽도록 수정했다. 긴 원 단위 숫자의 300자
  절단 오류도 제거했다.
- `백만` 오탈자와 `백만원, EA` 복합단위를 정규화하고, 단위 누락 표는 고신뢰로 인정하지
  않는다. 음수 수주잔고와 환산 전 `1990~2030.xx` 연도·날짜 후보도 원천 차단했다.
- 과거 연도·날짜 오채택 4,065행과 범용 첫 숫자 휴리스틱 저신뢰값 5,121행을 원문 근거를
  유지한 채 NULL 격리했다. 각각의 적용 전 값은 아래 고정 보고서로 복원 가능하다.
  - `research_outputs/order_backlog_year_capture_cleanup_20260812_224723.json`
  - `research_outputs/order_backlog_low_confidence_quarantine_20260812_224927.json`
- 구조화 총계 재파싱을 반복 적용해 인접분기 20배 이상 원본 이상쌍을 최초 1,653건에서
  273건으로 83.5% 줄였다. 남은 값은 삭제하지 않고 원문 연구 증거로 보존했다.
- 운영 기준을 confidence 0.85에서 **0.95**로 상향했다. 최종 운영 투영본은 1,156행,
  수주잔고 트리거는 1,131건이다. 검증된 구조화 표만 사용하기 위한 의도적 축소다.
- 최종 강화 검증 결과 PostgreSQL/SQLite 각각 보정 보고서 566행 missing 0/mismatch 0,
  `projection_quality_leaks=0`, `trigger_quality_leaks=0`이다. 즉 남은 273개 원본 이상쌍은
  후보·점수·트리거에 유입되지 않는다.
- 파서/품질 게이트 회귀 테스트는 20개이며 모두 통과했다. 자동매매는 계속 비활성화 상태다.
