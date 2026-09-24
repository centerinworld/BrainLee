# 가격 무결성 게이트 수정 (2026-09-09, blindspot_audit_20260909 후속)

읽기 전용 감사(`research_outputs/blindspot_audit_20260909/findings.md`)가 지적한 5개 항목 중
"우선 조치" 4개를 코드로 수정하고 운영 PostgreSQL에 실제 적용했다. KIS 수집 로직은 건드리지 않았다
(별도 진행 중인 작업과 분리).

## 1. 확정 기업행위 오허용 차단 (`scripts/verify_price_history_with_naver.py`)
외부(Naver) 검증이 `return_usable=1`로 승격시키는 UPDATE가 `corporate_action_or_delisting_nearby`만
보호하고 `confirmed_corporate_action`은 보호하지 않아, 두 가격 공급자가 같은 기업행위발 점프를
동일하게 보여주면 확정 기업행위조차 "사용 가능"으로 뒤집혔다(009310/011080/204630/222810 4건 확인).
`PROTECTED_FROM_OVERRIDE`(confirmed_corporate_action, corporate_action_pending_confirmation,
corporate_action_or_delisting_nearby, non_equity_symbol)로 승격 자체를 차단하도록 재작성.
승격 판정을 블랭킷 SQL UPDATE에서 종목별 Python 루프로 바꿔, 매 실행마다 "현재" classification을
기준으로 재판정한다(과거처럼 이미 정확한 분류에 evidence 문자열이 무한히 append되는 부작용도 제거).

또 하나의 기업행위 판정 공백을 `scripts/audit_price_jumps_and_build_canonical.py`에서 발견·수정:
`corporate_action_events`에 매칭은 됐지만 `adjustment_status='review_required'`(미확정)인 경우
분류 로직이 이를 무시하고 raw-source 일치만으로 `raw_source_confirmed_jump`(usable=1)까지
떨어질 수 있었다(011080 2026-05-07 사례). 신규 분류 `corporate_action_pending_confirmation`
(usable=0)을 `confirmed_corporate_action`과 `corporate_action_or_delisting_nearby` 사이에 추가.

## 2. 낡은 외부 판정 재사용 차단 (지문 기반 무효화)
`external_price_verification`이 (종목,사건일)만으로 "이미 검증됨"을 판단해 `--only-new`에서
영구히 재검증을 건너뛰었다. 가격/직전일/매칭된 기업행위가 바뀌어도 감지 못해, 036220은
비교 시작일이 2011-09-05(검증 당시)에서 2016-05-04(현재)로 바뀌었는데도 옛 비율(1.869배)로
현재 비율(8.206배)을 승인한 상태로 5개월 이상 방치돼 있었다.
`price_integrity.verification_fingerprint()`(SHA256, previous_date/previous_close/event_close/
price_ratio/public_*/matched_event_type/matched_report_name 해시, POLICY_VERSION 포함,
`classification`은 검증 스크립트 자신이 쓰는 값이라 의도적으로 제외)를 `input_fingerprint`
컬럼으로 저장하고, 저장된 지문이 현재 audit 행과 다르면 무조건 재검증하도록 변경.
재실행 결과 036220은 올바른 비교 기간(2016-05-04 기준)으로 재검증되어 정상 승격됨을 확인.

⚠️ 부수 버그: `external_price_verification`이 이미 존재하는 테이블이라 `ALTER TABLE ADD COLUMN`이
컬럼을 물리적으로 맨 끝에 추가했는데, 포지셔널 `INSERT ... VALUES(...)`가 DDL 텍스트상의 컬럼
순서를 가정해 `verified_at`/`input_fingerprint` 값이 서로 뒤바뀌어 들어갈 뻔했다 — 컬럼명을
명시한 INSERT로 수정.

## 3. 안전 계열(canonical view)을 운영 PostgreSQL에 실제 생성 + 실사용 연결
`price_integrity.py`(오늘 미커밋 상태로 존재하던 신규 모듈)가 이미 이 문제를 겨냥해 작성돼
있었다: PostgreSQL에서 `PostgresCompatConnection.executescript()`는 `CREATE TABLE/VIEW`류
DDL을 조용히 스킵하는데(레거시 SQLite 시작 스크립트가 실수로 스키마를 재적용하는 것을 막기 위한
설계), 기존 감사 스크립트들은 이 사실을 모른 채 `if not IS_POSTGRES:` 로 통째로 건너뛰고
있었다 — 그 결과 `canonical_price_history_v`/`canonical_price_returns_v`/`price_history_quality_v`/
`price_trading_calendar`/`price_integrity_quarantine`가 운영 DB에 전혀 없었다.
`price_integrity.native_script()`(Postgres에서는 원시 psycopg 커서로 우회 실행)를 실제로
호출하는 `scripts/apply_price_integrity_schema.py`를 신규 작성해 실행 — 8,449,029행 전체에
대해 뷰가 정상 생성됨을 확인(quality_status 분포: invalid_ohlcv 1,711 / coverage_gap 23,341 /
unexplained_jump 3,908 / suspended 66 / invalid_previous_price 4 / insufficient_history 4,352 /
normal 8,415,647). 기존 `audit_price_jumps_and_build_canonical.py`가 같은 뷰 이름을 다른
정의로 재정의하던 충돌도 제거(그 스크립트는 이제 `price_jump_audit` 테이블만 소유).

`scripts/audit_selected_strategy_price_integrity.py`(선택된 26개 전략의 보유기간 오염 검사,
`run_registry.py`의 `price_integrity` 게이트 원천)가 좁은 `price_jump_audit.return_usable=0`
대신 `canonical_price_history_v.return_usable=0`을 조회하도록 수정 — 감사표에 아예 기록된 적
없는 날짜(구형 1.8배/0.55배 문턱을 벗어나지 못해 스캔조차 안 된 unexplained_jump·coverage_gap·
invalid_ohlcv)도 이제 잡힌다. **재실행 결과 26개 전략 전부(0→26건) 최소 1개 이상의 오염된
보유기간이 새로 발견되어 `price_integrity` 아티팩트가 fail로 전환, `routes/backtest.py`의
스위트 상태가 `legacy`로 강등된다.** 오염 비율 자체는 낮음(예: v11 483건 중 4건=0.8%,
composite 684건 중 23건=3.4%) — 전량 오탐이 아니라 "존재하지만 옅게 퍼진" 실제 데이터 결함으로
판단됨. 전부-아니면-전무(zero-tolerance) 통과 기준을 유지할지, 중대도/비중 임계값을 둘지는
정책 결정이 필요해 이번 세션에서 임의로 바꾸지 않았다.

## 4. 접합(splice) 사례 — 대한항공(003490)/005440은 구조적으로 자동 차단됨, 나머지는 미해결
findings.md가 지적한 003490/005440의 2018-12-24 접합 오류(`INSERT OR IGNORE`가 빈 날짜만
채우면서 서로 다른 배치의 가격 기준이 이어붙은 사례, `naver_price_history_backfill`로 확인)는
가격을 직접 고치지 않았다 — `price_series_registry`에 이미 "naver_price_history_backfill로
기존 price_history 행을 덮어쓰지 말 것" 정책이 명시돼 있어 이를 따랐다. 대신 두 종목의 2018-12-24
행은 ±30% 문턱을 실제로 벗어나므로 위 3번 작업만으로 이미 `unexplained_jump`/`return_usable=0`으로
자동 차단되고, `canonical_price_returns_v`의 `safe_daily_return`도 12-24→12-26 구간까지 연쇄
차단됨을 확인(LAG 기반 전일 return_usable 체크).
findings.md가 함께 지적한 "±30% 미만인 접합 후보"(2018-12-24/2019-01-02 경계 802건, 삼성전자
2018-12-28→2019-01-02 -16.37% 등)는 이번에 손대지 않았다 — 개별 검증 없이는 정상적인 등락과
구분 불가하다는 findings.md 자체의 경고를 따름. `price_integrity_quarantine` 테이블은 이번에
스키마만 생성되고 비어 있다(0건) — 개별 검증이 끝난 사례를 넣는 용도로 남겨둠.

## 실행한 스크립트 순서 (재현 방법)
```bash
python3 scripts/audit_price_jumps_and_build_canonical.py       # 1) 기본 분류 재구축
python3 scripts/verify_price_history_with_naver.py --only-new  # 2) 외부검증 오버레이(지문 기반)
python3 scripts/apply_price_integrity_schema.py                 # 3) canonical view 재생성
python3 scripts/audit_selected_strategy_price_integrity.py     # 4) 선택전략 게이트 재판정
```
1)과 2)는 항상 이 순서로 실행해야 한다 — 2)는 1)이 다시 계산한 "현재" classification 위에만
오버레이를 얹는다(가정을 뒤집는 로직 없이 매번 base가 이깁니다).

## 남은 과제
- 접합 후보 802건+2022년 클러스터 개별 검증(가격 미수정, quarantine 후보로만 사용).
- `backtest_common.py`의 원시 OHLCV 로더는 이번에 손대지 않음 — 실제 신뢰 판정은
  `audit_selected_strategy_price_integrity.py` 후속감사 게이트가 담당하는 기존 아키텍처를 그대로 사용.
- 26개 전략 전부 `legacy` 강등에 대한 임계값/정책 결정(전부-아니면-전무 유지 여부).

## 5. 2026-09-11 후속: 2018-12-24~28 접합 오류 340종목 복구 실행 완료
위 4번의 "802건대 접합 후보" 중 003490/005440과 같은 패턴(naver_price_history_backfill과
수년간 정확히 일치하다가 좁은 구간에서만 안정적 비율로 벌어지는 경우)을 보수적으로 선별하는
`scripts/apply_20181224_splice_repair.py`를 작성해 실행했다. 선정 기준: 종목 전체 이력 중
단발성 에피소드(10거래일 이하) 1건만, 직전 5거래일+ 정확 일치(±2%) 이력 보유, 기업행위 무매칭,
깨끗한 분할비율(2x/5x/10x 등)에 가깝지 않음, ratio 0.3~3.0배(003490/005440과 동일 규모)만 —
10배 이상 극단값 125건은 미확정 분할 가능성을 배제 못해 이번엔 손대지 않고 제외.

dry-run으로 340종목·1,343행을 확인 후 실행(`run_id=splice_repair_naver_backfill_20260911_202212`).
원본값은 `price_history_fix_backup`에 보존, `data_fix_log`에 기록. 이후 파이프라인을 문서 순서대로
재실행(`audit_price_jumps_and_build_canonical.py` → `verify_price_history_with_naver.py --only-new`
→ `apply_price_integrity_schema.py` → `audit_selected_strategy_price_integrity.py`) —
`price_integrity_quarantine`에 정확히 1,343건이 반영됨을 확인. composite 전략 오염비율이
3.36%→3.07%로 소폭 개선됐으나, 26개 전략 전부 여전히 `failed`(오염 원인 대부분은 이번에
손대지 않은 coverage_gap/unexplained_jump 등 다른 부류) — 위 "남은 과제"의 정책 결정은 미해결.

## 6. 2026-09-11 후속(2차): 802건 후보 정밀검토 + 극단비율 22건 추가복구 + 2022년 클러스터 원인확인
`scripts/extend_naver_backfill_20181101_20190228.py` 신규 — naver_price_history_backfill이
2018-12-28에서 끊겨 있어(2015-2018 전용 백필) 2019-01-02 경계·재수렴 검증이 불가능했음.
2018-11-01~2019-02-28로 2,713종목/161,265행 확장 수집(price_history는 건드리지 않음).

**극단비율 22건 추가복구**: 5번 항목에서 미확정 제외했던 125건(0.3~3.0배 밖, 최대 76배)을
확장수집 데이터로 재검증 — 2019-01-02~02-15 구간 재수렴 비율이 0.9~1.1인 22건만 안전하다고
판정(`scripts/apply_20181224_splice_repair_extreme.py`, 88행). 나머지 103건은 재수렴 데이터
없음(6건) 또는 지속적 괴리·다른 소스측 점프 등 이질적 패턴(97건)이라 손대지 않음 — 예:
001230은 에피소드와 이후 구간의 괴리비율이 서로 다르고(5.27배→4.42배), 009730은 naver 자체가
2019-01-02부터 10배 뛰는 등 실제 미확정 기업행위 가능성을 배제 못함.

**802건(2018-12-24 146건+2019-01-02 656건, ±10~30%) 정밀검토**: findings.md가 "확정 불가"로
남겼던 원시 임계값 후보를 naver 대조 방법론(직전 5일+ 정확일치·안정적 비율·기업행위 무매칭·
클린 분할비율 아님)으로 재검사한 결과 **11건만 실제 후보**로 좁혀졌고, 그중 10건은 재수렴
데이터 부재 또는 정상 시장변동/거래정지 등 다른 원인으로 확인되어 제외, **227100(프로브잇)
1건만** 003490/005440과 동일한 접합오류로 확정·복구(`apply_20181224_splice_repair_227100.py`,
4행). 나머지 800건 가량이 실제 데이터 오류가 아니라는 것은, 하루 등락률만으로는 접합오류를
식별할 수 없고 반드시 독립 소스 대조가 필요하다는 findings.md의 경고를 재확인시켜준다.

**2022년 클러스터**: read-only 조사로 근본원인 확정(2018-12-24와 동일한 배치접합, `created_at`
증거로 확인) — 상세는 [docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md](CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md).
검증 인프라 미비(2022년 구간 naver 백필 없음) + 동시 세션 부하로 수정은 다음 세션 과제로 이월.

**부수 발견 — 쓰기감시 트리거의 교차세션 영향**: `audit_price_jumps_and_build_canonical.py`가
설치하는 `price_history_basis_write_guard` 트리거(과거 날짜 price_history 쓰기를 전부
`price_integrity_quarantine`에 기록)가, 동시에 실행 중이던 다른 세션(`claude/wonderful-swirles-156532`)의
정상적인 KRX 결측일 백필 쓰기까지 전부 격리 대상으로 잡아 160만 건 이상 쌓임 — 데이터 손상은
아니고 설계대로 "검증 안 된 경로의 쓰기"를 잡아낸 것이지만, canonical view의 return_usable
해석 범위가 크게 넓어짐. 일괄 검증/승격 여부는 사용자 지시로 보류.
