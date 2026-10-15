# 실행 계약 — OFS 사업보고서 본문 후보 40칸 안전 적용

**발행:** Planner Bot, 2026-10-10 22:xx KST  
**담당:** Code Doer 구현 → Checker 독립 검토 → Planner 최종 승인  
**우선순위:** `PLAN_99_99_NUMERIC_20261009.md` §7, 10-10 10:41~10:43의 다음 작업 ②  
**상태:** 구현/테스트만 승인. **프로덕션 DB `--apply` 실행은 금지**(후속 사람/Planner 승인 필요).

## 1. 범위와 불변 조건

입력은 다음의 이미 산출된 후보 파일 하나다.

```
research_outputs/financial_rereview_20261002/ofs_doc_candidates_20261010_104125.csv
```

- 입력 41칸 중 `fg_match_2m == True`인 **40칸만** 적용 대상이다.
- `065150 / 2023 / OFS / operating_cf`의 1,000배 불일치 후보 1칸은 **어떠한 경우에도 쓰지 않는다**. 결과에 `rejected_source_scale`로 별도 남긴다.
- 대상은 `quarter=0`, `fs=OFS`인 **연간값만**이다. 이 작업은 Q1~Q4 행을 만들거나 계산하지 않으며, CFS/외화/비12월/잠금/정정·환산 보호 데이터를 우회하지 않는다.
- 문서 본문은 DART 후보일 뿐이다. 실행 시점에 FnGuide가 여전히 존재하고 후보와 **절대 2,000,000원 이하**로 일치할 때에만 진행한다. CSV에 기록된 과거 판정만 신뢰하지 않는다.
- 기존 DB 값이 NULL/행 없음인 칸만 채운다. 기존 비NULL 값은 같아도 no-op, 다르면 `held_existing_value`로 남기고 덮어쓰지 않는다.
- 테스트·dry-run·기본 실행은 PostgreSQL 라이브 DB에 **쓰지 않아야 한다**. 라이브 `--apply`는 별도 승인 전 실행하지 않는다.

## 2. 산출물·파일 범위

신규 파일만 허용한다.

- `scripts/review/apply_ofs_doc_candidates_20261010.py`
- `tests/test_apply_ofs_doc_candidates_20261010.py`

필요 최소 변경만 다음 문서에 기록한다.

- `docs/PLAN_99_99_NUMERIC_20261009.md` §7: 계약, dry-run 실측, 적용 금지 상태, 다음 owner.
- `docs/OPEN_ITEMS.md` C3-2: 본문 후보 40칸은 이 계약으로 분리되어 적용 대기라고 명시.
- `AGENTS.md`: 새 스크립트/테스트 및 동작 변경, 변경 이력 한 줄.

기존 `apply_ofs_extra_20261009.py`의 원문 JSONL 경로나 광범위 OFS 적용 동작을 재활용·변경하지 않는다. 이 CSV에 대해서만 동작하는 별도 실행기여야 한다.

## 3. 구현 계약

### CLI

```bash
./venv/bin/python scripts/review/apply_ofs_doc_candidates_20261010.py
./venv/bin/python scripts/review/apply_ofs_doc_candidates_20261010.py --apply
```

- `--apply`가 없으면 read-only dry-run이며 DB write SQL/commit을 실행하지 않는다.
- 기본 CSV 경로는 위 입력이다. 테스트를 위해 `--input-csv <path>`와 `--output <path>`를 허용해도 된다.
- 결과 JSON의 run_id는 충돌하지 않는 시각 포함 이름이어야 하며, `--apply`가 0변경이면 `_apply_noop`처럼 기존 성공 산출물을 덮지 않는 이름을 쓴다.

### 필수 사전 게이트(각 후보칸)

1. CSV 스키마·`fs=OFS`·`quarter=0`·필드 allowlist(손익/재무상태 6개 + 현금흐름 3개)·유한 수치인지 검사한다.
2. `fg_match_2m`가 true여도 매 실행 `financial_field_verification`에서 해당 키의 FnGuide 값을 다시 읽고 `abs(candidate - fnguide) <= 2_000_000`인지 확인한다. FnGuide 없음/불일치면 `held_fnguide_changed_or_missing`.
3. 데이터 잠금, 보고통화 외화, 비12월 결산, 단위오류, 재작성/환산 보호 규칙은 기존 안전 경로와 동등하거나 더 엄격하게 적용한다. 제외된 항목은 이유를 결과에 기록한다.
4. 입력의 `doc`와 `parse_reason=section4_table`는 provenance로 결과·fix log에 보존한다. 절대경로·원시 값 등 민감정보는 남기지 않는다.

### 쓰기 경로(`--apply` 승인 후에만)

- PostgreSQL primary connection helper와 표준 `data_write_gate`/canonical upsert 경로만 쓴다. 직접 SQLite 연결, raw SQLite DDL, UI/API 변경 금지.
- 기존 행 업데이트/신규 연간 행 삽입은 후보값이 검증된 **해당 field만** 바꾼다. 연간 두 벌이 존재하는 경우 canonical 대표행 규칙을 깨지 말고 동기화 전에 동일한 값/대표결정을 명시한다.
- 변경 전 행(또는 NULL/행 없음이라는 상태), 변경 필드, run_id, CSV/doc provenance, FnGuide 값, 판정 이유를 감사 가능하게 남긴다. 기존 프로젝트의 `financial_fix_log`/`cashflow_fix_log`, `data_fix_log`, 백업 테이블 관례를 사용한다.
- canonical 동기화는 표준 gate를 통과한 행만 수행한다. 거부된 canonical 행은 원장을 되돌리지 말고 `canonical_refused`로 결과에 남긴다.

### 필수 결과 JSON

다음 카운터와 샘플/키 목록을 포함한다.

- `input_total`, `csv_fg_match`, `rejected_source_scale`, `invalid_input`
- `eligible`, `held_fnguide_changed_or_missing`, `held_existing_value`, `excluded_by_safety_gate`
- `planned_financial_fields`, `planned_cashflow_fields`, `planned_new_rows`, `planned_updates`, `noop`
- `applied_*` (apply일 때), `readback_checked`, `readback_mismatch`, `canonical_sync`(synced/refused/mismatch)
- 후보별 `stock_code/year/quarter/fs/field`, CSV doc, candidate, fresh FnGuide, decision reason.

## 4. 테스트/증거 수용 기준

신규 pytest는 temporary DB/fixture만 이용하며, 최소 아래 케이스를 모두 검증한다.

1. 기본 실행이 DB write/commit 없이 계획만 만든다.
2. `--apply` 없이 기존 행·canonical·log가 바뀌지 않는다.
3. FnGuide 200만원 이내 후보가 NULL 칸을 계획/적용하고 값·provenance를 readback한다.
4. CSV true여도 재조회 FnGuide가 없거나 200만원 초과면 hold한다.
5. `065150` 1,000배 행은 절대 적용하지 않는다.
6. 기존 non-NULL 불일치 행은 덮어쓰지 않는다.
7. 유효하지 않은 field/fs/quarter/non-finite 값은 fail-closed다.
8. lock/외화/비12월/단위·보호 조건은 write하지 않는다.
9. canonical gate 거부가 audit 결과로 남고 성공이라고 위장되지 않는다.
10. 결과 파일이 실제 계획/적용 카운터 및 dry-run/`_apply_noop` 충돌 방지를 정확히 표현한다.

필수 실행 증거:

```bash
./venv/bin/python -m pytest tests/test_apply_ofs_doc_candidates_20261010.py -q
./venv/bin/python -m py_compile scripts/review/apply_ofs_doc_candidates_20261010.py
./venv/bin/python scripts/review/apply_ofs_doc_candidates_20261010.py
```

마지막 dry-run은 live PostgreSQL 읽기 전용으로 실행하되 **DB 쓰기 0**을 결과·전후 검증으로 입증한다. Code Doer는 변경 파일, diff, 명령별 원문 출력, JSON 절대 경로, `git status --short`를 @checker에게 넘긴다. `--apply`는 실행하지 않는다.

## 5. Checker 및 Planner 게이트

- Checker는 CSV 41행/40 일치/1 scale rejection을 프로그램적으로 재계산하고, dry-run이 PostgreSQL primary runtime path를 읽었는지, 테스트가 실제 write guard·canonical refusal을 다루는지 독립 확인한다.
- Planner는 Checker의 양성 판정, clean scope, live dry-run 증거를 받은 후에만 커밋 후보로 승인한다. 커밋/푸시는 다른 미커밋 공유 작업과 분리하여 경로 지정 staging만 사용한다.
- 적용 승인 후에만 별도 apply/runbook을 발행하고, apply 직전 재-dry-run → idempotent apply → live primary readback → post-apply dry-run에서 추가 insert/update 0을 확인한다.
