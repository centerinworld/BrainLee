# AGI-01 — 소스 레지스트리·원문 보관·권한·시점 계약 (문서 계약 초안)

- 작성: 2026-10-11 09:24 KST · 담당: Code Doer · 상태: **초안 = `review` 대기** (Planner 대행 검증 대상)
- 입력 정본: [AGI_00_SYSTEM_BASELINE_20261010.md](AGI_00_SYSTEM_BASELINE_20261010.md) (2026-10-10 23:34:33 KST 라이브 읽기 전용 실측)
- 상위 계획: [Improvement_Stock_System.md](Improvement_Stock_System.md) §5, §6, §8, §14, §15, §18
- 이 문서는 **계약 초안 문서**다. 코드·DDL·마이그레이션·수집 실행·서비스 재시작·DB write를 포함하지 않는다. 아래 DDL은 논리 명세이며 적용은 별도 승인 절차를 요구한다.

---

## 1. 범위와 비범위

| 구분 | 내용 |
|---|---|
| 범위 | AGI-02/03/04(수집·보관·파이프라인)와 AGI-05/06/07/10/11(소비)가 공유할 **원문·소스·시간·권한·완전성 계약**. 기존 테이블 재사용/확장/신설 경계, 멱등키, PIT·quality 차단, API 소비 경계, 보관/백업, DDL·rollback 규칙, 테스트 수용 기준 |
| 비범위 | 수집 실행, 스키마 적용, 데이터 이관·백필, 화면 구현, 외부 API 계약 체결, 모델/OCR 비용 확정. D02/D07/D08 미결정 값(범위·예산·권한)의 추정 확정 |
| 금지(이번 단계) | 코드 작성·수정, DDL 적용, DB write, 수집 실행, 서비스 재시작, 기존 61개 dirty 파일(2026-10-11 09:2x 실측 `git status --short` 61행) 덮어쓰기·일괄 stage/commit |
| 산출물 | 이 문서 1개 + `Open_Items_AGI.md` 상태 기록(AGI-01 `review`). 그 외 파일 변경 없음 |

---

## 2. 이 계약이 근거로 삼는 실측 (2026-10-11 09:2x KST, 읽기 전용)

실행 경로: `runtime/venv/bin/python`, `db_compat.connect_primary_db(readonly=True, timeout=30)`, 최초 `SHOW transaction_read_only` = **`on`**. 모든 수치는 라이브 PostgreSQL `stock_dashboard` 실측이며 문서 초안의 추정치가 아니다.

| # | 실측 사실 | 계약에 미치는 영향 |
|---|---|---|
| M1 | `telegram_channel_posts`: 11,453행 / 15채널, `UNIQUE(channel_id, message_id)` 존재, `msg_date` 2024-11-09 23:57:52 ~ 2026-10-10 23:23:52 | 재사용 가능한 자연키가 이미 있다. §5 멱등키의 기준 |
| M2 | 같은 테이블의 **시간 컬럼 2종 포맷 불일치**: `msg_date` = `'2026-10-10 03:41:30'`(공백 구분, 오프셋 없음), `collected_at` = `'2026-10-11T08:35:19'`(ISO T, 오프셋 없음) | §4에서 정규화 규칙 없이는 정렬·비교·PIT 판정이 깨진다 |
| M3 | `report_files`: 29,158행, **UNIQUE 제약 0개**(PK `id`만), `report_date` 2013-01-01 ~ **2030-09-01**, 2026-12-31 초과 4행 | 재사용 시 멱등키 부재 + 미래일자 오염 → §5·§8 |
| M4 | `global_macro_data`: 73,522행 / 181 코드, `date` 1960-01-01 ~ **2031-12-31**, 초과 20행 = `{US,EU,JP,CN}_GDP_GROWTH_WEO` | WEO는 **전망치**다. 관측치와 같은 컬럼·같은 테이블에 섞여 있다 → §8에서 값 종류 분리 강제 |
| M5 | `quant_major_indicator_series`: 220,822행 / 299 키, `period` ~ **2031-12-31**(초과 122행), `quality` 기본값 `'raw'`, `quant_major_indicator_catalog` 302행(`source_system`·`collector_path`·`exactness`·`enabled` 보유) | 카탈로그는 있으나 품질 enum 계약이 "raw" 하나뿐이다 |
| M6 | `quant_major_indicator_series`의 `(indicator_key, period)` 중복 그룹 **26,494**, 그러나 `(indicator_key, period, series_name, source_name)` 중복 **0**. 예: `epic:7:36` 2026-07 = 286행, `series_name` 286종(`전라선_무궁화` 등) | **`(key, period)`를 자연키로 쓰면 안 된다.** `series_name`이 개체 축이다 |
| M7 | `hypothesis_research_registry` 1행(PK `research_id`), `hypothesis_research_runs` 1행(PK `run_id`, `record_json`·`publishable`) | AGI-09의 승인 버전·연결 계약을 담기엔 부족하다(§AGI-09 §7) |
| M8 | `/api/research/*` prefix는 **이미 점유**: `routes/research_lab.py` → `main.py:249`, `factor-validation`·`quantstats`·`strategy-decay`·`price-integrity`·`us-minervini` (OpenAPI 503 path 실측) | 계획 §14의 `/api/research/documents` 제안은 네임스페이스 충돌 → §9에서 해소 |
| M9 | `company_analysis_profiles` PK `stock_code` 434행, `evidence_json` 보유 / `company_peer_relations` 2,110행 | 기업 프로필 재사용 근거. 근거 필드가 JSON blob이므로 claim 단위 계보는 신설 필요 |
| M10 | `global_macro_events` 30행(`forecast`·`previous`·`actual`·`surprise_*`·`status`·`source`), `global_macro_event_reactions` 437행(`window`·`base_date`·`end_date`·`return_pct`) | AGI-10의 이벤트 축은 재사용 후보. 발표시각·vintage 계약은 여전히 신설 |
| M11 | `quant_indicator_signal_events` 132행(`generated_at`, `telegram_sent`) / `telegram_stock_mentions` 21행(`period_start`·`period_end`·`mention_count`) | 기존 신호·관심도 산출물이 **입력 분모 없이** 결과만 남는다 → §6 완전성 계약의 필요 근거 |
| M12 | 작업 트리 dirty 61파일, HEAD `d08d692` | AGI 산출물은 신규 경로 한정 원칙 유지 |

---

## 3. 재사용 / 확장 / 신설 경계 (논리 테이블 대조표)

원칙: **재사용은 "이미 계약을 만족할 때"만 한다.** 만족하지 않으면 기존 테이블에 의미를 덧붙이지 않고 신설하고, 기존 테이블은 레거시 참조로 동결한다.

| 계획 §14 논리 테이블 | 기존 실물 | 실측 판정 | 계약 결정 |
|---|---|---|---|
| `research_sources` | 없음 | — | **신설**. 소스 소유자·범위·권한 등급·커서·watermark |
| `ingestion_runs` | 부분 유사: `collection_job_runs`(스케줄러 원장), `global_macro_collection_log`, `dart_report_item_collection_log` | 재사용 불가(각자 다른 스키마·소유자) | **신설** + 기존 원장과 **합성 금지**(AGI-00 §3 마지막 행). 소스별 원장을 그대로 보존하고, AGI는 run_id로 참조만 |
| `research_documents` / `revisions` | `telegram_channel_posts`(M1·M2), `report_files`(M3) | **시드로만 재사용**. revision·tombstone·발표시각·권한 컬럼 없음 | **신설**. 기존 테이블은 `legacy_*` 소스의 원문 참조 대상. `document_id`는 신설 쪽에만 존재 |
| `document_metrics` | 없음(`views`·`forwards`가 `telegram_channel_posts`에 관측시점 없이 단일 컬럼) | 재사용 불가 | **신설**(시계열). 기존 `views`/`forwards`는 최신 관측값으로만 취급, 0으로 간주 금지(`NULL` ≠ 0) |
| `entities` / `aliases` / `relationships` | `company_mapping_profile`, `company_product_mix`, `stock_universe`(종목 마스터) | 부분 재사용 | **신설** + 종목 마스터는 `stock_code` 조인으로 참조. 별칭·이력·confidence는 신설 |
| `evidence_claims` | `company_analysis_profiles.evidence_json`(M9) | 재사용 불가(claim 단위 위치·시각·종류 없음) | **신설**. JSON blob은 요약 캐시로 남기고 정본으로 쓰지 않음 |
| `company_report_versions` | 없음 | — | **신설** |
| `macro_vintages` / `events` | `global_macro_data`(M4), `global_macro_events`(M10), `quant_major_indicator_series`(M5·M6) | **읽기 재사용만**. vintage·값 종류(관측/전망)·발표시각 컬럼 없음 | **신설**(vintage 축). 기존 테이블은 `source_ref`로 참조하고 **직접 upsert 금지** |
| `research_experiments` / `outcomes` | `hypothesis_research_registry`/`runs`(M7), `backtest_runs`, `strategy_regime_policy` | 부분 재사용 | **확장 판정 대기**: AGI-13 착수 시 `hypothesis_research_*`에 연결 컬럼을 추가할지, 신설 `research_experiments`로 흡수할지 결정(§14 Q3) |
| `opportunities` / `reviews` | 없음 | — | **신설** |
| `owner_policy_versions` / `theses` | 없음 | — | **신설** → AGI-09 계약 |
| `allocation_runs` / `execution_intents` | 기존 주문·가상매매 원장(`peak_trade`, `portfolio_tx`, 키움 주문 경로) | 별개 계층 | **신설**, 기존 주문 원장과는 `intent_id` 참조만. 리서치 서비스에 주문 자격증명 금지(계획 §13.3) |
| `blog_drafts` / `publications` / `alert_outbox` | 없음 | — | **신설** |

**동결 규칙**: 위 표에서 "재사용"으로 표시되지 않은 기존 테이블은 AGI 파이프라인이 **쓰기 대상으로 삼지 않는다**. 기존 수집기(`telegram_collector.py`, `report_store.py`, `scheduler._loop_*`)의 동작·스케줄은 이 계약으로 바뀌지 않는다.

---

## 4. 시간 4종 계약 (AGI-00 §4-2의 구체화)

모든 AGI 사실 레코드는 아래 4개 시각을 **분리 보존**한다. 서로 대체하지 않는다.

| 필드 | 의미 | 원천 | 불명 시 규칙 |
|---|---|---|---|
| `period_start` / `period_end` | 대상 기간(값이 서술하는 구간) | 원문·지표 정의 | 불명이면 `NULL` + `quality='period_unknown'` |
| `published_at` | 원천 발표시각 | 원문 메타·API 발표시각 | **수집시각을 하한으로 대체하되 `published_at_source='collected_at_lower_bound'`로 표기**. 발표시각인 척하지 않는다 |
| `first_seen_at` | 시스템 최초 인지시각 | 수집기 최초 저장 시각 | 항상 기록(대체 불가) |
| `stored_at` | 저장시각 | DB write 시각 | 항상 기록 |

저장 규칙:

1. 저장은 **UTC(`timestamptz`)**, 표시는 KST. 기존 테이블의 TEXT 시각(M2)을 그대로 신설 테이블에 복사하지 않는다.
2. 신설 테이블은 `timestamptz`만 쓴다. 기존 테이블 조회 시 `+09:00`로 해석한다는 가정을 **명시적 변환 함수 한 곳**에서만 한다(암묵 파싱 금지).
3. 정렬·비교·PIT 판정은 반드시 `timestamptz` 비교로 수행한다. TEXT 시각 문자열 비교는 금지(M2: 두 포맷이 섞이면 사전식 정렬이 실제 순서와 다르다).
4. 하루 경계가 필요한 판정(휴장·장중)은 `trading_calendar.is_kr_trading_day()`를 단일 출처로 쓴다.
5. 동일 사건의 시각 충돌(원천 발표시각 < 대상기간, 발표시각 > 수집시각 등)은 **거부가 아니라 플래그**로 남긴다(`time_conflict` 큐). 근거 없는 값은 NULL 유지.

---

## 5. 멱등키·revision·tombstone

| 계층 | 멱등키 | 근거 |
|---|---|---|
| 소스 | `source_id` = `(source_kind, source_owner, source_native_id)` | 소스 소유자 단일화(계획 §15) |
| 문서 | `document_id` = `(source_id, external_id)` | 계획 §14 |
| 개정 | `revision_hash` = §5-1 정규 알고리즘, `revision_no` 단조 증가 | 본문/첨부 변경 = 새 revision |
| 첨부 | `attachment_hash` = sha256(바이트), `attachment_key` = 문서 내 정규화 상대경로 | 파일 식별 + 해시 정렬 기준 |
| 지표 값 | `(series_id, observation_period, vintage)` | AGI-00 §4-2, M4·M6 |
| 실험 | `hypothesis_id` + 데이터/코드/설정 해시 | 계획 §13.1 |

규칙:

1. **같은 배치 3회 재실행 → 엔티티 중복 0**(계획 §18). 재실행 시 `revision_hash` 동일하면 revision을 추가하지 않는다. ⚠️ 적용 범위는 **엔티티 한정**이다 — 실행 원장(`ingestion_runs`)은 매 실행 새 행이 정상이며, 두 계층의 구분은 §5-2에 고정한다.
2. 삭제·비공개는 `tombstone`(삭제 사유·관측시각)으로 기록하고 행을 지우지 않는다. **권한 오류를 삭제로 기록하는 것을 금지**한다(`access_state ∈ {ok, deleted, restricted, unavailable}` 별도 컬럼).
3. 기존 테이블 재사용 시 자연키 부재(M3)를 신설 쪽 키로 대체한다. `report_files`를 멱등키 없이 upsert하는 경로는 AGI 범위에서 금지.
4. `quant_major_indicator_series`(M6) 참조 시 `series_name`을 키에 포함한다. `(indicator_key, period)` 단독 조회는 "복수 개체"를 반환할 수 있음을 계약으로 명시.
5. 커서는 `(source_id, last_cursor, last_run_id, last_success_at)`로 저장하고 **중단 후 재개 결과가 동일**해야 한다.

### 5-1. `revision_hash` 정규 알고리즘 (첨부 정렬 규칙 포함)

구현자·수집기·검증기가 같은 입력에 **항상 같은 해시**를 내도록 순서를 못박는다. 파일시스템 열거 순서·DB 반환 순서·로케일에 의존하면 안 된다.

```
text_norm   = NFC(개행 \n 통일) → 연속 공백 정리 → trim          # 본문 정규화
text_digest = sha256(utf8(text_norm))

attachment_key  = 문서 루트 기준 상대경로, '/' 구분, NFC, 선행 './' 제거
                  (경로가 없는 첨부는 f"__nopath__/{attachment_index:04d}")
attachment_digest = sha256(바이트 원문)                            # 재인코딩·재압축 금지

entry(i) = attachment_key + "\x1f" + attachment_digest
entries  = sorted(entry(i) for i in 첨부)   # 정렬 키: (attachment_key, attachment_digest)의
                                            # 바이트 오름차순, 대소문자 구분, 로케일 비의존
entries  = dedupe(entries)                  # 동일 (key,digest) 중복은 1건으로 축약

revision_hash = sha256( utf8( "v1\n" + text_digest + "\n" + "\n".join(entries) ) )
                # 첨부 0건이면 join 결과는 빈 문자열(구분자·플레이스홀더 추가 금지)
```

규칙:

- **정렬은 위 1개 규칙만 있다.** 어떤 코드도 "받은 순서 그대로" 이어붙이지 않는다.
- `attachment_key`가 해시에 포함되므로 **내용이 같은 파일의 경로 변경(rename)은 새 revision**이다(`change_kind='attachment_renamed'`). 같은 내용의 서로 다른 두 경로는 **별개 항목**으로 센다.
- 해시 접두 `v1\n`은 알고리즘 버전이다. 알고리즘을 바꾸면 접두를 올리고, 접두가 다른 해시를 같은 revision으로 취급하지 않는다.
- `change_kind ∈ {new, text_edit, attachment_added, attachment_removed, attachment_renamed, mixed}`를 revision 행에 기록한다(무엇이 바뀌었는지 해시만으로 알 수 없으므로).
- 첨부 파일 자체는 `attachment_digest` 기준으로 **1회만** 저장한다(같은 바이트 중복 저장 금지).

### 5-2. 실행 원장과 엔티티의 구분 (중복 0의 정확한 범위)

| 계층 | 재실행 시 동작 | 근거 |
|---|---|---|
| `ingestion_runs`(실행) | **매 실행 새 `run_id`.** 3회 실행 = 3행. 중복 제거하지 않는다 | 실행 증거(시각·호스트·결과)는 사라지면 재현 불가 |
| `research_documents`·`revisions`·첨부·`evidence_claims`·수치 행(엔티티) | `revision_hash`/자연키 동일 → **추가 0**. 실행 행은 `run_id`로 링크만 | 계획 §18 "같은 배치 3회 재실행 중복 0" |
| 수집 카운터 | 각 run은 `stored_count`(신규) / `skipped.duplicate`(기존)로 구분 기록 | "중복 0"이 통계를 가리지 않게 |

⇒ 재실행 검증(T1)은 **엔티티 중복 0 + run 3행 + 3행째의 `skipped.duplicate` 합계가 2행째와 동일**을 함께 확인한다.

---

## 6. 완전성·watermark·coverage

1. 소스마다 `requested_from`, `oldest_available`, `last_collected_at`, `page_cursor`, `listed_count`, `stored_count`, `skipped{deleted,restricted,duplicate,error}`를 기록한다(계획 §5.1).
2. `stored_count / listed_count`만으로 완전성을 주장하지 않는다. **원천 목록을 확보하지 못한 소스는 `coverage='denominator_unknown'`** 으로 표시하고 "완전 수집" 표기를 금지한다(계획 §18).
3. 메시지 ID 간격을 누락 건수로 단정하지 않는다.
4. AGI-05의 관심도 분모는 `coverage`가 `known`이고 `watermark`가 신선한 소스 집합으로 한정한다. 소스 집합이 바뀌면 증가율 비교를 중단하고 **신규 출현**으로 표시한다(계획 §6 표 4행).
5. 커버리지 저하 시 파생 신호는 `quality_warning`과 함께 보류한다(계획 §6 마지막 문단).

기존 자산 한계 명시: `telegram_stock_mentions`(M11)는 `period_start/period_end`와 건수만 있고 **어떤 문서 집합에서 계산됐는지가 없다** → AGI-05는 이 값을 이력 비교에 재사용하지 않는다.

---

## 7. 권한·보관·외부 전송 (3분리)

권한은 **문서 단위 + 소스 단위**로 각각 분리한다(AGI-00 §4-5).

| 권한 축 | 값 | 기본값 |
|---|---|---|
| `store_original` | `yes` / `metadata_only` / `no` | `metadata_only` |
| `external_model_send` | `allowed` / `redacted` / `denied` | `denied` |
| `publish_summary` | `allowed` / `owner_approval` / `denied` | `denied` |
| `retention_class` | `permanent` / `term` / `ephemeral` | `term` |

규칙:

1. D02/D07/D08 확정 전에는 위 값의 **추정 기본값을 적용**하고, 소스 등록 시 사용자 확인 없이 `allowed`로 올리지 않는다.
2. 원문 텍스트에 포함된 지시문은 **데이터**로 취급하며 모델의 파일·게시·주문 권한을 바꾸지 못한다(계획 §15).
3. 원문과 메타데이터는 **복구 대상**, 임베딩·요약 캐시는 **재생성 대상**으로 분리한다(계획 §5.2).
4. 동일 SSD 사본은 백업으로 보지 않는다. 별도 매체/원격 백업 위치·보존 기간은 D07.
5. 개인 대화·비공개 보고서는 기본 제외(계획 §5.1, D02).

---

## 8. PIT·quality fail-closed 게이트

소비(전략·화면·백테스트·알림) 전에 아래를 평가하고, 하나라도 실패하면 **차단(fail-closed)** 한다.

| 게이트 | 조건 | 실패 시 |
|---|---|---|
| G1 시간 완결 | `published_at` 존재 또는 `published_at_source='collected_at_lower_bound'`, `first_seen_at` 존재 | `pit_eligible=false`, 백테스트·알림 사용 금지 |
| G2 미래값 차단 | `observation_period`/`published_at` > 평가시점(`as_of`) | 사용 금지. M4·M5의 2031·M3의 2030 실측 초과행(4+20+122행)은 **원천 대조 전까지 미래 데이터로 취급** |
| G3 값 종류 | `value_kind ∈ {observed, forecast, estimate, reported_fact}`. 전망치를 관측치 컬럼에 넣지 않음 | `forecast`를 실적으로 쓰는 소비 경로 차단(M4: WEO 전망 혼입 실측) |
| G4 quality | `quality ∈ {verified, cross_checked, single_source, raw, stale, unknown, period_unknown}` | `verified`/`cross_checked` 외에는 승격·경보 사용 금지. M5의 기본 `'raw'`는 승격 불가 등급 |
| G5 커버리지 | `coverage='known'` + watermark 신선 | 파생 신호 보류 + 경고 표시 |
| G6 숫자 정본 | 재무 수치는 FINANCIAL_STATEMENTS 정본 경로만 사용 | 미확정 숫자로 보고서·실험·비중 승격 금지(AGI-00 §3 4행) |
| G7 스냅샷 세대 | 응답에 `snapshot_generation_time` + 입력 watermark 포함 | 미표기 응답은 소비 금지(AGI-00 §4-3) |

추가 요구: 평가 결과를 **레코드에 남긴다**(`pit_eligible`, `quality`, `gate_failures`). "화면에서 걸렀다"는 이유로 게이트가 통과한 것처럼 기록하지 않는다.

---

## 9. 소비 경계 (API·화면)

1. **네임스페이스 충돌 해소(M8)**: `/api/research/*`는 기존 `research-lab` 라우터가 점유한다. AGI 제안 API는 이 prefix를 재사용하지 않는다. **확정(Planner 결정, 2026-10-11): 네임스페이스는 `/api/agi/*`** — 예: `/api/agi/documents`, `/api/agi/attention`, `/api/agi/companies/{code}/versions`, `/api/agi/theses`, `/api/agi/opportunities`, `/api/agi/blog-drafts`, `/api/agi/allocations`. 기존 `/api/research/*`(research-lab)의 스키마·부작용·소유 테이블은 AGI가 상속하지 않으며, 그 경로를 재사용하지 않는다.
2. **조회는 무부작용**: GET은 저장된 결과만 반환한다. 캐시·프로필 영속화를 동반할 수 있는 기존 경로(AGI-00 §2 기업 프로필 주석)를 읽기 전용으로 가정하지 않는다. 생성·재생성은 인증된 작업 API가 `job_id`를 반환하고 상태를 별도 조회한다.
3. 모든 조회 응답은 `as_of`, `snapshot_generation_time`, `source_watermarks`, `quality_summary`, `unconfirmed_fields`를 포함한다(계획 §8, AGI-00 §4-3).
4. 공개 화면에는 `publish_summary != denied`인 근거만 노출하고, 보유·개인 메모·비공개 원문·미발행 글·주문 기능은 인증된 연구 화면에 둔다(계획 §4).
5. AGI API는 기존 정본 수집 경로(DART/FnGuide/네이버/가격)를 **덮어쓰지 않는다**. 숫자는 참조만 한다(계획 §15 첫 행).

---

## 10. 저장 경로·retention·백업

1. 원문 제안 경로(계획 §5.2, **이번 단계 미생성**): `research_archive/{raw/<source>/<year>/<month>/<document_id>/<revision>/, parsed/, exports/, manifests/}`.
2. 기존 `reports/`, `telegram_media/`, `postgresql16/`, `stock.db`, `logs/`, `browser_profiles/`, `antigravity_workspace/venv/`는 **삭제·이동 금지**.
3. `manifests/`에 수집 요약(소스·범위·건수·실패·해시·수집시각)을 남기고 `ingestion_runs`와 `run_id`로 연결한다.
4. 보존: 원문 revision·claim·정책 버전·실험 결과·게시 이력은 **삭제하지 않는다**. 운영 로그·캐시만 `retention_class='term'`으로 정리 가능.
5. 복원 검증은 별도 매체에서 표본 복원으로 수행하고 결과를 기록한다(계획 §18 "운영" 행).

---

## 11. DDL·마이그레이션·rollback 규칙

1. **rollback 없는 DDL 금지**(AGI-00 §5). 신설/확장마다 down 경로를 같은 승인 단위로 제출한다.
2. 이 계약 단계에서는 **어떤 DDL도 적용하지 않는다**. 적용은 별도 실행 계약(Planner 승인)으로만 한다.
3. DDL 원칙:
   - 신설 테이블은 위 §4~§7의 키·시간·권한 컬럼을 **1급 컬럼**으로 갖는다(JSON blob 단독 저장 금지).
   - 멱등키는 DB 제약(`UNIQUE`)으로 강제한다. 애플리케이션 레벨 upsert만으로 대체하지 않는다(M3의 재발 방지).
   - 기존 테이블에는 **컬럼 추가만** 허용하고 이름 변경·타입 변경·삭제는 금지한다. 추가 시 `DEFAULT` 없는 `NOT NULL` 금지.
   - 인덱스는 조회 패턴별로 명시하고, TEXT 날짜 컬럼을 함수로 감싼 조건(`substr(date,1,10)=?`)은 금지한다(runtime/AGENTS.md §8).
4. backfill은 신설 테이블에만, `run_id`·건수·실패를 원장에 기록하고, 기존 테이블 소급 수정은 별도 정정 절차(FINANCIAL_STATEMENTS §2-5)로만 한다.
5. 마이그레이션 스크립트는 멱등(재실행 무해)해야 하며, 실패 시 부분 적용을 남기지 않도록 트랜잭션 단위를 명시한다.

---

## 12. 비용·캐시

1. 예산 상한: 호출/토큰/월 상한을 설정하고, 소진 시 **원문 수집·품질 점검 우선, 비필수 요약 연기**(계획 §15).
2. 캐시 키: `(원문 해시, 추출기 버전, 모델, 프롬프트 버전)`. 동일 키는 재호출하지 않는다.
3. 월 비용 산식: `신규/수정 문서 수 × 평균 입출력 토큰 × 단가 + OCR 페이지 비용 + 구독 + 저장·백업`. 7일 표본 추정. **현재 단가·공급자 계약 미확정**(D07).

---

## 13. 테스트 수용 기준 (구현 시 검증 목록)

문서 단계에서 기준만 고정한다. 구현 시 이 목록이 회귀 테스트로 내려간다.

| ID | 검증 | 통과 조건 |
|---|---|---|
| T1 | 멱등 | 같은 배치 3회 재실행 → **엔티티**(문서·revision·첨부·claim·수치) 중복 0 + 실행 원장 3행 + 3행째 `skipped.duplicate` 합계가 2행째와 동일(§5-2) |
| T2 | 개정/삭제 | 본문 수정 → revision 1개 추가, 기존 revision 불변 / 접근 불가 → tombstone 1건, `access_state` 구분 |
| T3 | 재개 | 커서 중단 후 재개 → `stored_count`·`revision_hash` 집합 동일 |
| T4 | 시간 4종 | 발표시각 불명 문서가 `collected_at_lower_bound`로 표기되고 PIT 판정 결과가 기대와 일치. M2처럼 포맷이 다른 입력 2종을 넣어 정렬이 UTC 기준임을 확인 |
| T5 | 미래값 | M3/M4/M5의 2026-12-31 초과 행을 주입 → G2에서 차단, 소비 응답에 노출 0 |
| T6 | 값 종류 | `forecast`(WEO)를 실적 컬럼에 넣는 경로 → 실패 |
| T7 | 권한 | `publish_summary='denied'` 문서가 공개 초안·공개 API에 포함 0. `external_model_send='denied'` 문서가 외부 전송 경로에 포함 0 |
| T8 | 완전성 표기 | 원천 목록 없는 소스가 `denominator_unknown`으로 표시되고 "완전" 문자열 미포함 |
| T9 | 자연키 | `(indicator_key, period)`로 값을 고르는 코드 경로가 없음(M6 회귀) |
| T10 | DDL | down 스크립트 없이 제출된 마이그레이션 0. 적용 없이 dry-run으로 스키마 diff 산출 |
| T11 | 무부작용 조회 | GET 호출 전후 테이블 행수·`max(id)` 지문 동일 |
| T12 | 게이트 기록 | 모든 소비 응답에 `quality`·`pit_eligible`·`snapshot_generation_time`·watermark가 존재 |
| T13 | 원장 비합성 | AGI 실패가 기존 원장의 기존 실패를 덮거나 `success`로 바꾸지 않음 |
| T14 | `revision_hash` 결정성 | 같은 문서+첨부를 **역순·무작위 순서**로 2회 수집 → 두 `revision_hash` 동일. 내용 동일·경로만 변경 → 새 revision + `change_kind='attachment_renamed'`. 경로 없는 첨부 → `__nopath__/0001` 규칙 일치. 첨부 0건 → 빈 join 결과(플레이스홀더 미추가) |

---

## 14. 판정 필요 (Planner/사용자)

| Q | 질문 | 기본 권고 |
|---|---|---|
| Q1 | AGI API prefix | ✅ **확정** — `/api/agi/*` 신설, 기존 `/api/research/*`(research-lab)는 유지(Planner 결정 2026-10-11). 잔여 판정 없음 |
| Q2 | `report_files`·`telegram_channel_posts`를 신설 `research_documents`로 **이관**할지, 참조만 할지 | 참조만(이관은 중복 저장·정정 전파 위험) |
| Q3 | `hypothesis_research_registry/runs`(M7)를 AGI-13 실험 원장으로 **확장**할지 **신설**할지 | AGI-13 착수 시 결정(현 단계 보류) |
| Q4 | `quant_major_indicator_series`를 AGI-10 vintage 계약의 원천으로 채택할지 | 카탈로그(`source_system`·`collector_path`) 확인 후 결정 |
| Q5 | M4의 `*_GDP_GROWTH_WEO` 20행 처리 | 원천 대조 전 `forecast` 라벨 부여 + 신호 입력 차단 |

---

## 15. 변경 기록

| 날짜 | 변경 |
|---|---|
| 2026-10-11 09:24 KST | AGI-01 계약 초안 작성. AGI-00 베이스라인 + 2026-10-11 읽기 전용 재측정(M1~M12) 근거. 코드·DDL·DB write·수집·재시작 없음 |
| 2026-10-11 10:0x KST | Planner 검증 BLOCK 반영(문서 전용): §5-1 `revision_hash` 정규 알고리즘(첨부 정렬·`attachment_key`·알고리즘 버전 접두·`change_kind`), §5-2 실행 원장↔엔티티 구분(재실행 중복 0의 범위), §9-1 API prefix를 `/api/agi/*`로 **확정**(Planner 결정), §14 Q1 종결, 수용 테스트 T1 정밀화 + T14(해시 결정성) 추가 |
