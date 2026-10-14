# AGI-09 — owner 원칙 원장·A/B/C 가설·변경 관리 계약 (문서 계약 초안)

- 작성: 2026-10-11 09:24 KST · 담당: Code Doer · 상태: **초안 = `review` 대기** (Planner 대행 검증 대상)
- 입력 정본: [AGI_00_SYSTEM_BASELINE_20261010.md](AGI_00_SYSTEM_BASELINE_20261010.md) §4 (AGI-09 고정 전제)
- 필수 연계 정본: [Improvement_Stock_System.md](Improvement_Stock_System.md) §9, §13, §18 · [SIGNAL_RULES.md](SIGNAL_RULES.md) §5·§6 · [FINANCIAL_STATEMENTS.md](FINANCIAL_STATEMENTS.md) §2-2·§9-2-7·§9-2-8 · [Open_Items_AGI.md](Open_Items_AGI.md) D01·D04·D05
- 이 문서는 **계약 초안 문서**다. 코드·DDL·DB write·수집·서비스 재시작을 포함하지 않는다. 이 단계에서 **매수/매도 신호·목표 비중·주문 규칙을 만들지 않는다**(AGI-00 §4).

---

## 1. 범위와 비범위

| 구분 | 내용 |
|---|---|
| 범위 | ① owner 투자 원칙의 **원문·해석·버전·효력 이력** ② A/B/C 연구 가설 카드와 **측정지표·반증 조건** ③ 가설↔실험↔결과↔채택/기각↔적용 버전의 **불변 연결** ④ 시스템이 스스로 원칙·임계값을 바꾸지 못하게 하는 **변경 관리 규칙** ⑤ 소비(AGI-13/14) 전 **품질·PIT 차단** |
| 비범위 | A/B/C 전략의 실제 구현·백테스트 실행(AGI-13), 비중·주문 산출(AGI-14/17), 위험 한도 수치 확정(D04), 자동화 전환 범위(D05), 신호 규칙 변경(그 권한은 `SIGNAL_RULES.md` §6) |
| 금지(이번 단계) | 코드·DDL·DB write·수집·재시작. 기존 dirty 61파일(2026-10-11 09:2x 실측) 덮어쓰기·일괄 stage/commit |
| 산출물 | 이 문서 1개 + `Open_Items_AGI.md` 상태 기록(AGI-09 `review`). 그 외 파일 변경 없음 |

---

## 2. 고정 전제 (AGI-00 §4에서 그대로 승계)

1. **D01만 결정됨**(2026-10-10 사용자 "1, 2번 주식 모두") — 회복 선행(A)과 미발견 성장(B)을 **별도 연구 가설**로 관리한다. 세부 임계값은 실험 대상이며 확정값이 아니다.
2. 원문 → 해석 → 측정지표 → 반증 → 실험 ID → 결과 → 채택/기각 → 적용 버전을 **불변 이력**으로 둔다.
3. 전략·비중·주문 승격은 `SIGNAL_RULES.md`, C11 PIT 해결 상태, AGI-13/14/15 게이트의 **후속 조건**이다.
4. 이번 단계에서 매수/매도·목표 비중을 만들지 않는다.

### 2-1. 원칙 원문 (변형 금지 대상)

`Improvement_Stock_System.md` §9.1의 5개 원문을 **verbatim**으로 원장에 적재한다. 요약·의역을 원문 컬럼에 넣지 않는다.

| 원문 ID(제안) | 원문(verbatim) | 확정 해석(2026-10-10) |
|---|---|---|
| OP-01 | "잃지 않는 투자를 한다" | 손실 회피가 최우선 운용 목표. 최대낙폭·하방위험·자금 고갈·집중위험 관리. **"무손실 보장"으로 표현 금지** |
| OP-02 | "좋은 주식보다는 안좋은 주식을 사전에 투자한다" | ① 실적 부진 + 개선 조짐, ② 좋은 기업 + 시장 관심 이전 — **두 유형 모두**(D01) |
| OP-03 | "주식의 가장 좋은 지표는 성장성, 성장하는 주식, 이익의 질과 크기가 커지는 회사이다" | 이익 규모·성장 지속성·현금전환·일회성 이익을 함께 평가. 저기저 성장률 단독 채택 금지 |
| OP-04 | "현 시점에 가장 맞는 투자방법 … 2~3가지 복합적 투자" | 국면별 검증 전략 2~3개 조합 + 종목·산업·위험 중복·상관 확인 |
| OP-05 | "주식/현금 비중 → 완전 자동화" | 최종 목표는 비중~대사 자동화. L0~L4 승격 단계를 둠 |

---

## 3. 원칙 원장 논리 스키마 (신설)

기존 테이블 중 owner 원칙·해석·승인 버전을 담는 객체는 **없다**(AGI-00 §2, 2026-10-11 재확인: `owner|polic|thes` 패턴에 해당하는 실물 없음. 유사 이름 `strategy_regime_policy`는 16행 국면별 적합도 표로 목적이 다르다).

| 논리 테이블 | 키 | 핵심 필드 | 불변 규칙 |
|---|---|---|---|
| `owner_policy_versions` | `(policy_id, version)` | `verbatim_text`, `source_ref`(대화·문서·시각), `effective_from`, `created_at`, `supersedes_version`, `status ∈ {draft, active, retired}` | `verbatim_text`는 수정 불가. 변경은 **새 version** |
| `policy_interpretations` | `(policy_id, version, interpretation_version)` | `interpretation_text`, `decided_by`, `decided_at`, `evidence_ref`, `scope`, `status` | 해석 변경도 새 버전. 과거 해석 조회 가능 |
| `policy_measurements` | `(policy_id, version, metric_id)` | `metric_name`, `definition`, `unit`, `data_source_ref`, `pit_eligible`, `quality`, `known_limits` | 지표 정의 없는 원칙은 **측정 불가로 표시**하고 전략 입력에 쓰지 않음 |
| `policy_falsifiers` | `(policy_id, version, falsifier_id)` | `falsifier_text`, `observable`, `deadline`, `status` | OP-01·OP-03에 **필수**. 없으면 `incomplete` |
| `policy_change_log` | `(policy_id, from_version, to_version)` | `change_reason`, `decided_by`, `decided_at`, `impact_scope`, `revalidation_ids` | append-only. 삭제·수정 금지 |

연결 체인(전부 ID 참조, 문자열 재기술 금지):

```
owner_policy_versions → policy_interpretations → policy_measurements
            → hypotheses(A/B/C) → experiments(AGI-13) → outcomes
            → verdict(채택/기각) → applied_version(전략·설정 버전) → 영향 소비자 목록
```

### 3-1. append-only를 **DB에서 강제**하는 규칙 (권한 + 소유권 분리 + trigger + 운영 통제 + 검증)

"문서에 append-only라고 썼다"만으로는 강제되지 않는다. 다만 **PostgreSQL trigger는 privileged 행위자에 대한 보안 경계가 될 수 없다** — 테이블 소유자와 superuser는 `ALTER TABLE … DISABLE TRIGGER`, `DROP TRIGGER`, `SET session_replication_role`(일반 trigger 무시), DDL로 스스로 우회한다. 따라서 보증 대상과 보증 밖 계층을 분리해 fail-closed로 적는다(Planner 지적, 2026-10-11 수정).

**보증 범위(먼저 고정)**
- **보증 대상 = 최소권한 앱 롤 한 개.** 이 롤은 append-only 테이블에 대해 직접 DML·`TRUNCATE`·`COPY`·`SET ROLE`·소유권·superuser 멤버십·사전정의 롤 멤버십 어느 경로로도 수정할 수 없음을 카탈로그 단언 + 실패 시도로 입증한다(§3-1-1·2·6).
- **보증 밖 = 소유자·superuser·복원(dump/`COPY` 재적재)·break-glass.** 이 계층은 **보안 보증이 아니라 운영 통제 대상**이며 §3-1-5의 사전승인·감사·사후 재검증으로만 다룬다. "trigger가 있으니 우회 불가"라는 서술은 이 문서 어디에도 쓰지 않는다.

1. **권한(1차 — 앱 롤에 대한 실제 보안 경계)**: append-only 테이블(`owner_policy_versions`, `policy_interpretations`, `policy_measurements`, `policy_falsifiers`, `policy_change_log`, `hypothesis_cards`, `experiment_runs`, `experiment_outcomes`, `*_transitions`)에 대해 애플리케이션 롤은 **`INSERT` + `SELECT`만** 부여받는다. DDL 승인 단위에 다음을 포함한다.
   ```
   REVOKE UPDATE, DELETE, TRUNCATE ON <append_only_table> FROM <app_role>;
   GRANT  INSERT, SELECT                ON <append_only_table> TO   <app_role>;
   ```
   수집·파이프라인 롤과 조회 롤을 같은 롤로 묶지 않는다. `COPY <t> FROM …`은 `INSERT` 권한을 요구하므로 grant 우회 경로가 아니다(이전 초안의 "COPY가 grant를 우회한다" 서술은 오류 — 정정). `INSERT … ON CONFLICT DO UPDATE`도 `UPDATE` 권한을 요구하므로 1차에서 차단된다.
2. **소유권·롤 멤버십 분리(2차 — 필수, 1차의 실효 조건)**: 1차는 **앱 롤이 우회 경로를 갖지 않을 때만** 성립한다.
   - 테이블 소유자를 앱 롤로 두지 않는다(`ALTER TABLE <t> OWNER TO <ddl_owner>` — DDL 전용 롤, 예: `agi_ddl_owner`). 앱 롤이 소유자면 자기 테이블에 `GRANT`를 다시 주거나 trigger를 끌 수 있어 1차가 자기모순이 된다.
   - 앱 롤은 `rolsuper = false`, `<ddl_owner>` 및 권한 보유 롤의 **멤버가 아님**(`pg_auth_members` 폐쇄), PG14+ 사전정의 롤 `pg_write_all_data`의 **멤버가 아님**(이 롤은 명시 grant 없이 전 테이블 쓰기를 부여하므로 멤버십만으로 1차가 무력화된다).
3. **trigger(3차 — 사고·회귀 탐지, 보안 경계 아님)**: `BEFORE UPDATE OR DELETE … FOR EACH ROW` + `BEFORE TRUNCATE` statement trigger로 `raise_append_only()`를 건다. 목적은 ① grant를 나중에 잘못 되돌리는 **회귀** ② `TRUNCATE`(row trigger를 타지 않음) 차단이다. trigger는 **`ENABLE ALWAYS`**(`tgenabled='A'`)로 생성해 `session_replication_role = replica`에서도 동작하게 한다. 소유자·superuser의 `DISABLE TRIGGER`/`DROP TRIGGER`는 여전히 가능하며, 그 잔여 위험이 §3-1-5와 T15/T16이 존재하는 이유다.
4. **상태 전이도 UPDATE 금지(구조적 해법)**: 가설 상태·verdict 변경은 `..._transitions` 테이블에 **새 행**을 넣는 방식으로만 표현한다. 따라서 **append-only 테이블 중 UPDATE 권한이 필요한 테이블이 하나도 없다** — "예외적으로 UPDATE 허용" 목록을 만들지 않는다.
5. **privileged break-glass 경계 = 운영 통제(보증 밖, fail-closed)**: 소유자·superuser·복원 경로로 append-only 테이블을 건드리는 행위(`ALTER TABLE … DISABLE TRIGGER`/`DROP TRIGGER`, `SET session_replication_role`, 직접 DML, dump 재적재)는 전부 다음을 만족해야 한다.
   - (a) **전용 롤**: 앱 롤과 분리된 break-glass 롤(앱 롤에 미부여, 자격증명 별도 보관).
   - (b) **사전 승인**: owner + Planner 2인 승인 + 사유·대상 테이블·행 범위·시간창 기록(사후 승인 금지).
   - (c) **세션 감사 — 가정 금지, 서버측 사전 설정 + 권한 있는 감사 세션**: `log_statement`는 **SUSET(수퍼유저 설정) 파라미터**이므로 **break-glass 롤이 세션에서 스스로 `SET`할 수 있다고 가정하지 않는다**. 작업 **전에** 다음 중 하나로 보장된 상태에서만 수행한다 — ① `ALTER SYSTEM SET log_statement='all'` + reload(superuser) ② `ALTER ROLE agi_breakglass SET log_statement='all'`(역할별 설정은 `pg_db_role_setting`에 영속되고 세션 시작 시 적용) ③ PG15+ `GRANT SET ON PARAMETER log_statement TO agi_breakglass` 후 권한 있는 감사 세션이 `SET`. 작업 세션은 시작 시 `current_setting('log_statement') = 'all'`을 확인하고 **아니면 작업을 시작하지 않는다**(fail-closed). 추가로 **canary 확인**을 통과해야 한다: 표식 문장(`SELECT 'agi-audit-canary-<run_id>'`)을 실행하고, 그 문장이 서버 로그에 실제로 기록됐음을 **로그 읽기 권한을 가진 감사 owner**가 `pg_current_logfile()` 경로에서 확인한 뒤에만 본 작업을 시작한다(설정이 "돼 있을 것"이라는 가정 금지; `ALTER SYSTEM`·로그 읽기 모두 privileged 행위이므로 그것도 (d)에 기록). `log_statement='all'`은 **그 세션의 문장만** 남기므로 privileged 작업은 감사 세션 **안에서만** 하고 세션 밖 경로는 허용하지 않는다. 로그는 작업 후 별도 보관한다(우회 흔적의 유일한 원천). ⚠️ **환경 전제(미확인)**: `pg_current_logfile()`은 `logging_collector=on`(stderr/csvlog 수집)일 때만 경로를 돌려준다 — 라이브 PG(Homebrew 16.15)에서 NULL이면 canary 확인이 **원리적으로 불가**하므로, 감사 보장은 ① `logging_collector=on`/`log_destination` 설정 ② 관리 롤이 읽을 수 있는 외부 싱크(syslog/journald/별도 수집기) 중 하나로 성립해야 한다. 이 전제가 확인되기 전 T15는 `blocked-by-environment`로 표시하며, "설정됐을 것"으로 통과시키지 않는다(§11 Q7).
   - (d) **원장 기록**: 위 행위 각각을 `privileged_access_log`(신설, append-only)에 1행 남긴다(누가·언제·무엇을·승인 ID·영향 키 범위).
   - (e) **사후 재검증**: 작업 직후 §3-1-6 실효 권한 단언(재귀 멤버십 포함) + append-only 테이블 해시 체인 재계산을 실행하고 결과를 같은 원장에 붙인다.
   - (f) **fail-closed**: (a)~(e) 중 하나라도 결손이면 그 영향 구간을 `unverified`로 표시하고 **P4~P9 승격·실험 채택을 금지**한다. 감사 기록 없이 privileged 쓰기가 탐지된 경우도 같은 처리다.
6. **검증(실효 권한 단언 + 재귀 멤버십 폐쇄 + 실패 시도)**: 카탈로그 목록만으로는 실효 권한을 증명하지 못한다(Planner 지적, 2026-10-11).
   - **실효 판정은 `has_table_privilege`로 한다** — `information_schema.role_table_grants`는 *부여받은 grant 목록*만 보여 주고 **PUBLIC grant와 상속 권한을 실효 기준으로 판정하지 않는다**. `has_table_privilege(app_role, '<schema>.<t>', 'UPDATE'|'DELETE'|'TRUNCATE')` = **전부 false**(PUBLIC·상속 포함 계산).
   - **컬럼 단위 권한도 막는다**: `has_any_column_privilege(app_role, '<t>', 'UPDATE')` = **false**. `has_table_privilege`는 컬럼 레벨 권한을 보지 않으므로, 컬럼 UPDATE grant 하나만 있어도 append-only가 부분 수정으로 깨진다. DDL 의무에 `REVOKE UPDATE, DELETE, TRUNCATE ON <t> FROM PUBLIC`을 포함한다.
   - **재귀 멤버십 폐쇄**: `pg_auth_members`를 재귀로 순회해 앱 롤에서 도달 가능한 모든 롤 `r`에 대해 `pg_has_role(app_role, r, 'USAGE')`(상속=즉시 사용 가능) **또는** `pg_has_role(app_role, r, 'SET')`(SET ROLE 경유)가 true이면, 그 롤이 대상 테이블의 UPDATE/DELETE/TRUNCATE를 보유하지 않아야 한다. PG16의 `pg_auth_members`는 `inherit_option`·`set_option`을 가지므로 **`inherit_option=false`라도 `set_option=true`면 우회 경로이므로 `'SET'` 검사를 생략하지 않는다**(종전 `pg_has_role(...,'MEMBER')` 단독 검사로는 이 구멍이 남는다). 폐쇄 안의 어떤 롤도 `rolsuper = true`가 아니어야 한다.
   - `information_schema.role_table_grants`(앱 롤의 UPDATE/DELETE/TRUNCATE 행 0)는 **감사 기록용**으로만 남긴다 — 판정 근거는 위 실효 함수다.
   - `pg_trigger`에 `UPDATE`·`DELETE`·`TRUNCATE` trigger가 테이블별로 존재하고 `tgenabled = 'A'`
   - 실제 시도: `UPDATE <t> …` / `DELETE FROM <t>` / `TRUNCATE <t>` / `SET ROLE` 경유 시도 → `permission denied`(성공하면 실패)
   - ⚠️ **검증 하네스는 반드시 앱 롤로 실행한다.** 소유자/superuser로 돌린 실패는 grant 문제를 숨긴다 — 증거가 되지 못한다. 반대로 같은 시도를 소유자/superuser로 돌려 **성공하는 것은 정상**(보증 밖 계층의 존재 증거)이며, 이를 결함으로 오판하지 않는다. 다만 그 행위가 감사 기록 없이 일어났다면 §3-1-5(f)의 운영 통제 위반으로 기록한다.
7. 이 계약 단계에서는 **적용하지 않는다**(§1 금지 조항). 위 1~5는 DDL 승인 단위에 함께 제출할 의무 항목이고, 6은 그 DDL의 수용 기준이다.

**우회 경로 대조표(어느 계층이 실제로 막는가)**

| 우회 경로 | §3-1-1 권한 | §3-1-2 소유권·멤버십 | §3-1-3 trigger | 실제 최종 방어선 |
|---|---|---|---|---|
| 앱 롤 직접 `UPDATE`/`DELETE`/`TRUNCATE` | **차단** | — | 예외 | 권한(카탈로그 단언) |
| `INSERT … ON CONFLICT DO UPDATE` | **차단**(UPDATE 필요) | — | 예외 | 권한 |
| `COPY … FROM`(행 추가) | 차단 아님(원래 `INSERT` 필요 — 허용 경로) | — | INSERT trigger가 아님(무관) | — |
| `SET ROLE` / 롤 멤버십(`pg_write_all_data` 포함) | 무력(상속) | **차단**(멤버십 폐쇄 단언) | 예외 | 롤 멤버십 단언 |
| 앱 롤이 테이블 소유자 | 무력(소유자 권한) | **차단**(`OWNER TO` 분리) | 예외 | 소유권 단언 |
| 소유자/superuser `DISABLE TRIGGER`·`DROP TRIGGER` | 무력 | 무력 | **무력** | **보증 밖** — break-glass 감사·사후 재검증(§3-1-5, T15/T16) |
| `SET session_replication_role = replica` | 유지 | 유지 | 약화 → **`ENABLE ALWAYS`로 복원** | `tgenabled='A'` 단언 |
| dump/`COPY` 재적재(복원) | — | 무력(복원 롤 권한) | 무력 | **보증 밖** — 운영 통제(§3-1-5) |

---

## 4. A/B/C 가설 카드 계약

| 필드 | 필수 | 내용 |
|---|---|---|
| `hypothesis_id` | ✔ | 예: `H-A-001` |
| `policy_refs` | ✔ | 근거 원칙 버전(OP-xx + version) |
| `type` | ✔ | `A`(회복 선행) / `B`(미발견 성장) / `C`(추세 확인) |
| `statement` | ✔ | 검증 가능한 문장(관찰 가능한 조건 포함) |
| `universe_rule` | ✔ | 시점별 유니버스 정의(상장폐지 포함·생존편향 차단) |
| `entry_conditions` | ✔ | 파라미터는 **실험 파라미터**로 명시. 고정 규칙으로 표기 금지 |
| `exit_conditions` | ✔ | 손절·추적손절·기간 등. 미정이면 `null` + 사유 |
| `measurement_ids` | ✔ | `policy_measurements` 참조. 없으면 승격 불가 |
| `falsifier` | ✔ | "이 판단이 틀렸음을 보여 줄 신호". 없으면 카드 생성 거부 |
| `expected_checkpoints` | ✔ | 20/60/120거래일 등 **사전 정의** 복기 시점 |
| `known_conflicts` | ✔ | 기존 전략·다른 가설과의 중복·상관 위험 |
| `status` | ✔ | `draft / planned / running / verdict / retired` |
| `experiment_ids` | 조건 | `running` 이후 필수 |

규칙:

1. 가설 대비 **반증 조건 없는 카드 금지**(계획 §16 "반증 중심 연구"). 없으면 `incomplete`.
2. A·B는 별도 가설이며, 하나의 진입 조건으로 합치지 않는다(D01). 합치는 실험을 하려면 근거와 함께 새 카드로 만든다.
3. C를 조합 후보로 쓸 때 A/B와의 상관·하락기 손실·회전비용을 검증 항목에 포함한다(계획 §9.2).
4. "관심도가 낮다"를 "저평가"로 등치하는 조건은 거부한다(계획 §9.2-B).
5. 적자→흑자 전환은 성장률이 아니라 **절대 변화 + 흑자전환 이벤트**로 표현한다(계획 §8-3).
6. 카드의 지표는 가격·재무 정본 경로(canonical view, `return_usable`, FnGuide/DART 확정)를 참조하고, 임계값은 실험 파라미터로 남긴다.

---

## 5. 변경 관리 (시스템 자율 변경 금지)

1. **시스템은 실적 부진을 이유로 owner 원칙·운영 임계값을 스스로 바꾸지 못한다**(계획 §9.2 마지막 문단). 자동 변경 경로가 코드에 있으면 그 자체를 결함으로 본다.
2. 신호 수치·규칙 변경은 **`SIGNAL_RULES.md` §6 절차**를 단일 경로로 쓴다: *"숫자는 원 전략 채택값 — 바꾸려면 백테스트 재검증과 사용자 승인 후 이 문서 변경 이력에 기록"*. §5의 차이를 늘리거나 줄일 때도 같다.
3. 적용 버전은 `applied_version`으로 기록하고, 그 버전을 쓰는 실험·화면·알림·게시물을 **영향 소비자 목록**으로 역추적한다(계획 §16 "자료 계보와 정정 전파").
4. 원칙 변경 시 `revalidation_ids`에 재검증 대상 실험을 기록한다. 재검증 전에는 해당 실험 결과로 승격하지 않는다.
5. 과거 버전·기각 실험·실패 사례는 **삭제하지 않는다**(계획 §16 "실패한 실험 보존").
6. 리서치 서비스에는 주문 자격증명을 주지 않는다(계획 §13.3). 원칙 원장은 주문 권한을 갖지 않는다.

---

## 6. 재사용 / 신설 대조표

| 논리 대상 | 기존 실물(2026-10-11 실측) | 판정 | 계약 결정 |
|---|---|---|---|
| 원칙·해석·승인 버전 | 없음 | — | **신설**(§3) |
| 가설 카드 | `hypothesis_research_registry`(1행: `research_id`, `title`, `hypothesis`, `verdict`, `status`, `latest_run_id`, `validation_json`) | 부분 유사, **1행뿐** | 이번 단계 **신설 안 함**. AGI-13 착수 시 **확장 vs 신설은 §11 Q2**로 판정. 그때까지 `h.*`에 가설 카드를 밀어넣지 않는다 |
| 실험 실행 기록 | `hypothesis_research_runs`(1행: `run_id`, `record_json`, `publishable`), `backtest_runs` | 재사용 후보 | AGI-13에서 `record_json`→정규 컬럼 확장 또는 신설 결정(§11 Q2) |
| 국면별 전략 적합도 | `strategy_regime_policy`(16행: `strategy_family`, `market_regime`, `suitability_score`, `action`, `rationale`, `updated_at`) | 재사용 후보 | 국면→전략 매핑 표로 **읽기 재사용**. 승인 버전·근거 원칙 컬럼이 없으므로 정책 정본으로 쓰지 않는다 |
| 공통 매매 신호 | `trend_rules.py` + `SIGNAL_RULES.md` | 정본 | **복제 금지**. AGI는 참조·표시만. 규칙 변경은 SIGNAL_RULES §6 |
| 품질·PIT 상태 | `OPEN_ITEMS` C3-2/C3-4/C11, `PLAN_99_99_NUMERIC` OFS 후보 **BLOCKED** | 미종결 | 차단 조건으로 사용(§7) |

---

## 7. 품질·PIT 차단 (승격 게이트)

AGI-09는 **판단 기록** 계층이므로, 아래가 실패하면 기록은 하되 **승격·실험 채택·비중 반영을 금지**한다.

| 게이트 | 조건 | 실패 처리 |
|---|---|---|
| P1 원칙 근거 | `policy_refs`가 유효한 `active` 버전 | 카드 `draft` 유지 |
| P2 측정 가능 | `measurement_ids` 존재 + 정의·단위·데이터 출처 명시 | `not_measurable` 표시, 실험 금지 |
| P3 반증 존재 | `falsifier` + 관측 가능 조건 | 카드 생성 거부 |
| P4 PIT | 실험의 입력이 당시 공개값(AGI-01 G1~G3) | 승격 금지, `pit_ineligible` |
| P5 숫자 정본 | 재무 수치가 FINANCIAL_STATEMENTS 정본·확정 상태 기준 | 미확정 숫자로 결과 확정 금지(C3-2/C3-4) |
| P6 전략 정합 | 신호가 `SIGNAL_RULES`·`trend_rules.py`와 모순 없음 | 결과 `conflicting`으로 표시, 채택 보류 |
| P7 표본·비용 | 관측 기간·표본 수·결측 제외 수·분모·비용·신뢰구간 표기(계획 §10.3) | 미표기 결과는 `unverified` |
| P8 우위 일관성 | 비교군 대비 **일관된** 우위. 기간별 엇갈림은 우위로 보지 않음 | 채택 보류 |
| P9 append-only 무결성 | §3-1-6 **실효 권한 단언**(`has_table_privilege`·`has_any_column_privilege` false + 재귀 멤버십 폐쇄) + append-only 해시 체인 재계산이 마지막 검증 이후 **통과**(그 사이 privileged 쓰기가 있었다면 §3-1-5의 사전승인·감사(canary 포함)·사후 재검증 기록 완비) | 영향 구간 `unverified` 표시, **P4~P9 승격·실험 채택 금지** |

P8의 근거(실측 정본): `SIGNAL_RULES.md` §5의 6기간 검증에서 계좌현황 규칙이 나은 기간 **3/6**, 원 전략 avg6 −7.86% vs 계좌현황 avg6 −0.30% — "**일관된 우위 없음**"으로 결론된 사례가 정본에 남아 있다. AGI-09는 이 패턴(평균 개선·기간별 엇갈림)을 채택 근거로 쓰지 않는다.

---

## 8. 불변 이력·멱등

1. 모든 원칙·해석·가설·결과는 **append-only + 해시**(`content_hash = sha256(정규화 본문)`)로 저장한다. 수정은 새 버전. **강제 방법은 §3-1**이며, 그중 **최소권한 앱 롤에 대한 실효 보증은 권한 + 소유권·롤 멤버십 분리**(§3-1-1·2), trigger는 사고·회귀 탐지(§3-1-3), 소유자/superuser/복원 경로는 **보증 밖 운영 통제**(§3-1-5 — 위반 시 `unverified`·승격 금지)다. 문서 서술만으로는 append-only로 간주하지 않는다.
2. `verdict`(채택/기각/보류)는 **판정 시점·판정자·근거 실험 ID**를 함께 기록한다. 판정 근거 없는 상태 변경 거부.
3. **실행과 결과를 분리한다(Planner 지적 ④ 해소)**: ① `experiment_runs`(실행)는 append-only — 같은 설정으로 3회 실행하면 **run 3행**이 정상이고, 2·3행째는 `duplicate_of_run_id`(첫 실행)와 `config_hash`로 연결한다. ② `experiment_outcomes`(결과)는 `(hypothesis_id, config_hash, evaluation_window, data_snapshot_hash)`가 자연키 — 같은 키면 **행을 추가하지 않고** 기존 outcome에 `run_id` 링크만 남긴다. ⇒ "재실행 = 새 run_id"와 "중복 결과 0"은 모순이 아니라 **서로 다른 계층의 규칙**이다. 기존 outcome의 `verdict`는 재실행으로 바뀌지 않는다(변경은 새 outcome 키 또는 `verdict_transitions` 신규 행).
4. 가설 상태 전이는 `draft → planned → running → verdict → retired`만 허용하고, 역방향은 사유와 함께 새 전이로 기록한다.
5. 원칙 원문(OP-01~05) 해시는 문서 정본과 대조 가능해야 한다(원문 표류 방지).

---

## 9. 소비 경계

1. 화면: "투자 원칙·실험" 화면은 `원칙 → 해석 → 측정지표 → 실험 결과 → 채택/기각 이유`를 한 흐름으로 보여 준다(계획 §4 표).
2. 조회는 무부작용. 생성·재생성은 인증된 작업 API + `job_id`.
3. 모든 응답은 현재 유효 원칙 버전과 품질 상태를 함께 반환한다. 미확정 숫자·`not_measurable` 지표는 **표시하되 승격 가능 표기 금지**.
4. AGI API prefix는 AGI-01 §9의 결정(Q1)을 따른다.
5. 이 계약은 어떤 매수/매도 신호도 정의하지 않는다. 최종 실행 판단은 사람이며, 자동 주문은 AGI-17·D04/D05 승인 이후의 별도 계약이다.

---

## 10. 테스트 수용 기준 (구현 시 검증 목록)

| ID | 검증 | 통과 조건 |
|---|---|---|
| T1 | 원문 불변 | OP-01~05 `verbatim_text` 수정 시도 → 거부. 변경은 새 version |
| T2 | 해석 버전 | 해석 변경 → 과거 해석 조회 가능, 과거 버전으로 소급 적용 0 |
| T3 | 자율 변경 차단 | 시스템이 임계값/원칙을 스스로 바꾸는 경로 0(코드 검사 + 테스트) |
| T4 | 반증 필수 | `falsifier` 없는 카드 생성 → 실패 |
| T5 | 측정 불가 | `measurement_ids` 없는 원칙으로 실험 생성 → 실패(`not_measurable`) |
| T6 | 체인 추적 | 임의 결과에서 원칙 원문까지 역추적 성공, 중간 버전 누락 0 |
| T7 | 승격 게이트 | P4/P5/P7 실패 입력 → 승격 거부 + 사유 기록 |
| T8 | P8 일관성 | 기간별 엇갈리는 결과(§7 근거 사례 재현) → 채택 불가 |
| T9 | 재실행 | 같은 `config_hash`로 3회 실행 → `experiment_runs` **3행** + `experiment_outcomes` **1행** + 기존 `verdict` 불변 + 2·3행째가 `duplicate_of_run_id`로 연결(§8-3) |
| T10 | SIGNAL_RULES 정합 | `SIGNAL_RULES.md` §6을 거치지 않은 신호 수치 변경 0 |
| T11 | 무부작용 조회 | GET 전후 행수·`max(id)` 지문 동일 |
| T12 | 삭제 금지 | 기각 실험·구버전 원칙 물리 삭제 0 |
| T13 | append-only 강제 — 앱 롤 보증(실효 권한 기준) | **앱 롤(최소 권한)**로 `UPDATE`/`DELETE`/`TRUNCATE` 및 `SET ROLE` 경유 시도 → 예외 또는 `permission denied`. 단언은 **카탈로그 목록이 아니라 실효 함수**로: `has_table_privilege(app_role,t,'UPDATE'|'DELETE'|'TRUNCATE')` **전부 false**(PUBLIC·상속 포함) + `has_any_column_privilege(app_role,t,'UPDATE')` false + **재귀 멤버십 폐쇄**(도달 가능한 모든 `r`에 대해 `pg_has_role(app_role,r,'USAGE')` 또는 `'SET'`가 true면 `r`이 해당 권한 미보유, 폐쇄 내 superuser 0) + 소유자 ≠ 앱 롤 + `rolsuper=false` + `pg_write_all_data` 미멤버 + `pg_trigger` 존재·`tgenabled='A'`. `role_table_grants` 0행은 **감사 기록용**으로만 첨부. ⚠️ 소유자/수퍼유저 롤 실행 결과는 증거로 인정하지 않음(§3-1-6). **보증 대상은 앱 롤 1개뿐**이며, 소유자/superuser/복원 경로는 **보안 보증 대상이 아니라 운영 통제 대상**이다 — 그 경로의 우회 시도가 성공하는 것은 결함이 아니다(T15·T16) |
| T14 | 전이 기록 | 원칙·가설·verdict 변경이 `*_transitions` **신규 행**으로만 발생하고 append-only 테이블 UPDATE 0. ⚠️ T14의 0건 조건도 **앱 롤 기준**이며, privileged 경로의 전이는 §3-1-5 원장(T15)으로 판정한다 |
| T15 | break-glass 운영 통제 | 소유자/superuser/복원 경로의 `ALTER TABLE … DISABLE TRIGGER`·`DROP TRIGGER`·`SET session_replication_role`·직접 DML이 ① 전용 break-glass 롤(앱 롤 멤버십 0) ② owner+Planner 사전 승인 기록 ③ **감사 로깅 보장** ④ `privileged_access_log` 1행 중 하나라도 없이 실행되면 **통제 위반**으로 판정. ③은 **가정 금지**: `log_statement`는 SUSET이므로 ㉠ `ALTER SYSTEM` ㉡ `ALTER ROLE … SET`(`pg_db_role_setting` 행 실재) ㉢ `GRANT SET ON PARAMETER` 중 하나로 작업 **전에** 보장되고, 세션 진입 시 `current_setting('log_statement')='all'` + **canary 문장이 서버 로그에 실제 기록됨**(감사 owner가 `pg_current_logfile()` 경로에서 확인)을 통과해야 한다. 미보장 상태의 수행은 그 자체가 통제 위반. 통과 조건 = 통제 위반 0 + 기록 완비. ⚠️ **이 테스트의 통과는 보안 보증이 아니라 운영 통제 준수**를 뜻한다("trigger로 privileged 행위자를 막았다"는 주장 금지) |
| T16 | 우회 후 재검증(fail-closed) | privileged 작업 직후 §3-1-6 **실효 권한 단언** + append-only 해시 체인 재계산 → 불일치 0. 불일치 또는 감사 기록(승인·canary·`privileged_access_log`) 누락 시 그 키 범위가 `unverified`로 표시되고 P4~P9 승격이 **거부**됨(거부하지 않으면 실패) |

---

## 11. 판정 필요 (Planner/사용자)

| Q | 질문 | 기본 권고 |
|---|---|---|
| Q1 | OP-01~05의 `source_ref`(원문 출처 대화·문서·시각)를 무엇으로 확정할지 | ⏸ **미확정 유지(Planner 결정, 2026-10-11)** — 사용자 근거가 확인될 때까지 `source_ref`는 NULL/`unverified`로 두고 추정하지 않는다. 해당 원칙 카드로 실험을 시작하지 않는다 |
| Q2 | `hypothesis_research_registry/runs`를 **확장**할지 **신설**할지 | AGI-13 착수 시 결정. 현 단계는 신설 테이블 설계만 |
| Q3 | A/B/C 세부 임계값의 최초 후보값 | AGI-13 실험 파라미터로만. 이 문서에서 수치 확정 금지 |
| Q4 | 원칙 버전 승인권자/절차(`decided_by`) | owner 단독 승인 + Planner 기록 권고 |
| Q5 | `policy_falsifiers`의 관측 지표를 어느 데이터 소스로 볼지 | AGI-01 소스 계약 확정 후 |
| Q6 | break-glass 롤 명명·승인자·감사 보관 위치(§3-1-5) | ✅ **계약 기본값으로 승인(Planner, 2026-10-11)**: 롤 `agi_breakglass`(앱 롤 미부여, 자격증명 별도) · **owner+Planner 사전 2인 승인** · `privileged_access_log` 신설 + **별도 PG 감사 로그 보관**. 단 **실제 롤·권한·테이블 생성(DDL)은 별도 승인 전 금지** — 이 승인은 계약 값 확정이며 실행 승인이 아니다 |
| Q7 | PG 감사 로그 싱크를 `pg_current_logfile()`로 확인할 수 있는지(`logging_collector` 상태) | 기본 권고: 다음 턴 **읽기 전용**으로 `SHOW logging_collector; SHOW log_destination; SELECT pg_current_logfile();` 실행 → NULL이면 ① `logging_collector=on` 설정 또는 ② 외부 싱크(syslog/journald/별도 수집기)를 감사 저장소로 확정. 확정 전 T15는 `blocked-by-environment`(§3-1-5(c)) |

---

## 12. 변경 기록

| 날짜 | 변경 |
|---|---|
| 2026-10-11 09:24 KST | AGI-09 계약 초안 작성. AGI-00 §4 고정 전제 + D01 결정 + SIGNAL_RULES §5·§6 실측 근거. 코드·DDL·DB write·수집·재시작 없음 |
| 2026-10-11 10:0x KST | Planner 검증 BLOCK 반영(문서 전용): §3-1 append-only를 **DB에서 강제**(권한 분리 + trigger + 카탈로그 단언, 검증 하네스는 최소권한 앱 롤), §8-1 강제 방법 명시·§8-3 실행/결과 계층 분리로 "새 run_id" ↔ "중복 결과 0" 모순 해소, 수용 테스트 T9 정밀화 + T13(append-only 강제)·T14(전이 기록) 추가 |
| 2026-10-11 09:4x KST | Planner 3차 지적 반영(문서 전용, 코드·DDL·DB write·수집·재시작 0, DB 접속 0): ① **§3-1-5(c) 감사 로깅 = 가정 금지로 재작성** — `log_statement`는 SUSET이므로 break-glass 롤이 세션에서 스스로 `SET`할 수 있다고 가정하지 않고, 작업 전 서버측 보장(`ALTER SYSTEM` / `ALTER ROLE … SET`(=`pg_db_role_setting`) / PG15+ `GRANT SET ON PARAMETER`) + `current_setting('log_statement')='all'` 확인 + **canary 문장의 서버 로그 실기록 확인**(감사 owner, `pg_current_logfile()`)을 통과해야 시작. 미보장 수행은 통제 위반 ② **§3-1-6 실효 권한 단언으로 교체** — `role_table_grants`는 목록일 뿐 PUBLIC·상속 실효를 증명하지 못하므로 `has_table_privilege` 3권한 false + `has_any_column_privilege('UPDATE')` false + **재귀 멤버십 폐쇄**(`pg_has_role` `'USAGE'`/`'SET'` 양쪽, PG16 `inherit_option=false`·`set_option=true` 경로 포함) + `REVOKE … FROM PUBLIC` DDL 의무로 대체(카탈로그 목록은 감사 기록용으로 강등) ③ T13/T15/T16·P9 문구를 위 기준으로 정정 ④ **Q6 승인 반영**(`agi_breakglass`·owner+Planner 사전 2인 승인·`privileged_access_log`+별도 PG 감사 로그, 단 롤·권한 생성 DDL은 별도 승인 전 금지) |
| 2026-10-11 09:5x KST | **DeepSeek 요금 구간 정정(정본 오류 수정)**: 종전 기록 "평일 피크 KST 09:30~다음날 01:30 / 비피크 01:30~09:30"은 **폐지된 V3/R1 off-peak 할인** 기준이었다. 공식 문서(api-docs.deepseek.com/quick_start/pricing, verbatim: *"Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese public holidays. All other hours are off-peak, including weekends and Chinese public holidays in full."*)에 따라 **피크 = 평일 KST 10:00–13:00·15:00–19:00**(UTC 01:00–04:00·06:00–10:00), 그 외 전 시간 + 주말·중국 공휴일 전일이 비피크. 발행 스케줄 19:15 KST는 피크 종료 직후라 유지 ⑥ §3-1-5(c) 감사 로그의 **환경 전제**(`logging_collector` off → `pg_current_logfile()` NULL이면 canary 확인 불가) 명시 + §11 **Q7** 추가 |
