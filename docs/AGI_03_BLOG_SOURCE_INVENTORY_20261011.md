# AGI-03 착수용 읽기 전용 인벤토리 — 블로그 소스별 발견·원문 보관 어댑터

작성: 2026-10-11 KST · 담당: Code Doer (@code-doer) · 상태: **review** (Planner 판정 대기)
정본 의존: `Improvement_Stock_System.md`(§0 동결 규칙) · `Open_Items_AGI.md`(AGI-03, D02, 연속 실행 원칙) ·
`AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`(§3 재사용 경계, §4 시간 4종, §5 멱등키·revision·tombstone, §7 권한 3분리)

> **이 문서의 범위**: AGI-03("블로그 소스별 발견·원문 보관 어댑터") 착수에 필요한 **읽기 전용 실측 인벤토리**다.
> 신규 코드 작성 0 · DDL/DB 쓰기 0 · 실수집 0 · 재기동 0 · 커밋 0.
> 예외 2건을 숨기지 않고 명시한다:
> ① §5의 기존 초안 어댑터 회귀 테스트 3파일 실행(`127 passed`)은 **임시 스키마(`agi_slice_test_*`)에 DDL 후 DROP** 한다 — 실행 후 `public` BASE TABLE 558 불변·잔여 테스트 스키마 0건을 재조회로 확인했다.
> ② 네트워크는 **robots.txt 4건 + RSS 피드 1건 GET(HTTP 상태·바이트 수·항목 수만 판독)** 뿐이며 **문서 본문은 1건도 받지 않았다**(§3).

---

## 1. 결론 요약

1. **기존 "블로그 보관"은 3개의 분리된 파이프라인**이다 — ①네이버 블로그(`sector_posts`) ②네이버 카페(`cafe_signal_posts`/`_bodies`) ③텔레그램 채널 15개(`telegram_channel_posts`) + 증권사 리포트(`report_files`). **공통 어댑터·공통 멱등키·공통 시간/권한 스키마가 없다.**
2. **원문(본문·미디어) 실보관은 사실상 없다.** 네이버 블로그는 AI 요약만 저장하고 본문을 버린다. 텔레그램은 본문 텍스트는 있으나 `photo_path` 가 **0/11,453건(전부 빈값)**이다. 리포트는 `file_path` 29,158행 중 **실존 3,422행(11.7%)**으로, 25,736행이 죽은 경로 `/Applications/stock_dashboard/reports/` 를 가리킨다.
3. **robots 실측(우리 UA 기준)에서 네이버 블로그의 발견 경로·RSS 피드·카페 전체가 차단**된다. 그런데 라이브 잡 `HOT섹터블로그`(매일 07:00)는 **차단 경로 `/PostList.naver` 를 매일 호출**한다. AGI-03의 robots 게이트를 켜면 그 소스는 **0건**이 된다 — 정책 유지/우회/공식 API 전환은 owner 결정 사항(§6 B3).
4. **revision 이력과 삭제(tombstone) 판정을 갖춘 기존 파이프라인은 0개**다. 유일하게 카페 파이프라인만 `content_hash` + `ON CONFLICT (cafe_id, article_id) DO UPDATE` 로 **변경 감지**를 하지만 **최신값만 남기고 이력은 보관하지 않는다**(distinct hash 7,567 / 7,600행).
5. **권한(공개/비공개·외부전송·게시) 컬럼을 가진 기존 테이블은 0개**다. AGI 신설 `research_sources` 만 3등급 분리(`store_original`/`external_model_send`/`publish_summary`)를 갖고 있으며 현재 **0행**이다.
6. **워킹트리에 AGI-03 어댑터 초안이 미추적 상태로 이미 존재한다**(`agi/adapters/*`, 테스트 127 passed, 프로덕션 배선 0·커밋 0). `Open_Items_AGI.md` 의 AGI-03 표기(`planned`)와 실제 트리 상태가 다르므로 **Planner 판정이 필요**하다(§5).

---

## 2. 소스 인벤토리 (라이브 실측)

| # | 소스 | 기존 수집기 (잡) | 저장 경로 (PG `public`, 실측 행수) | 발견 방식 | 원문 보관 | revision | 삭제 감지 | 권한 컬럼 | 시간 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **네이버 블로그** `going_tothe_moon` (categoryNo=7, 3자 블로그) | `Sector_define/blog_parser.py::run_parser` — 잡 `HOT섹터블로그` **매일 07:00** | `sector_posts` **149행** (2026-04-01 ~ 2026-10-06, `ai_summary` 149/149) + `sector_stocks` | `blog.naver.com/PostList.naver?blogId=…&categoryNo=7` HTML → `logNo=(\d{10,})` 정규식, 최신 30개 한도. 상세는 `m.blog.naver.com/{blogId}/{logNo}` → `#mainFrame` → `blog.naver.com{src}` → `.se-viewer`/`#postViewArea` | **본문 미보관**. `ai_summary`(OpenAI 요약)만 저장. 이미지 수집은 요약 입력용으로만 사용 후 폐기 | **없음**(신규 INSERT만, 종목 0건이면 `ai_summary` UPDATE) | **없음**(목록에서 사라진 글 처리 없음) | **없음** | `post_date` TEXT |
| 2 | **네이버 카페** `cafe.naver.com` cafe_id `30099599` (지표상회) | `scripts/ops/naver_cafe_signal_pipeline.py` — 잡 `카페시그널주간`(월 07:10) · `카페시그널월간`(1일 07:15) | `cafe_signal_posts` **8,225행** (2020-05-08 ~ 2026-08-23) · `cafe_signal_post_bodies` **7,600행** (`content_text` + `content_hash`) | 게시판 목록 + 본문 페이지 스크래핑 (`전체글` 6,933 · `업종 및 기업 분석` 1,128 · `업종 지표 활용법` 100 · `지표 추가 & 아이디어 제안` 64) | **본문 텍스트 보관**(`content_text`) — 기존 3경로 중 유일 | **부분**: `content_hash`(title+body) + `ON CONFLICT … DO UPDATE` = 변경 감지 O, **이력 보관 X**(최신 1본). distinct hash 7,567/7,600 | **없음** | **없음** | `published_at`/`fetched_at` TEXT |
| 3 | **텔레그램 소스 15개** (체리형부 `@Valuefs1`·`-100…` 6종, DOC_POOL, gaoshoukorea, petrojimmy, gatubang, jake8lee, 선진짱 주식공부방, sunstudy1004, yigreport, stoner8740) | `telegram_collector.py` + `telegram_store.py`; 진입 = `scripts/ops/collect_cherry_latest_channel.py`(잡 `체리형부최신채널` 08:45), `scripts/ops/refresh_cherry_family_pipeline.py`, `routes/telegram.py POST /api/telegram/collect` | `telegram_channel_posts` **11,453행 / 15채널** (2024-11-09 23:57:52 ~ 2026-10-10 23:23:52) · 레거시 중복 `telegram_messages` **13,361행** | MTProto 수집 (채널별 증분) | **본문 텍스트 보관**(`text` 11,453/11,453). **사진 원문 0건**(`photo_path` 비어있음). 문서는 `has_document`/`file_name` 메타만 | **없음**(자연키 `UNIQUE(channel_id, message_id)`, 편집 이력 없음) | **없음**(삭제/삭제표시 미감지) | **없음** (`telegram_channels` 에 공개/비공개 등급 컬럼 부재. `entity_hint` 로 채널/username 유추) | `msg_date`·`collected_at` TEXT, **포맷 2종 혼재**(AGI-01 M2) |
| 4 | **증권사 리포트(PDF)** — 텔레그램 경유 | 리포트 수집 경로 (`report_files` 적재) | `report_files` **29,158행** · `file_path` 29,158행 | 채널 메시지의 첨부 | **파일 경로만**. 실존 **3,422 / 29,158 (11.7%)** — 나머지 25,736행은 죽은 경로 `/Applications/stock_dashboard/reports/` | **없음** | **없음** | **없음** | `report_date` TEXT(**미래일자 오염**: max 2030-09-01), UNIQUE 제약 **0개**(AGI-01 M3) |
| 5 | 채널 종합분석 글 · AI 리포트 | `scripts/ops/refresh_detailed_analysis_multi_channel.py` · `routes/detailed_analysis.py` | `detailed_analysis_posts` **55행**(`content_md`, `source`) · `ai_analysis_reports` **16행** | 다중 채널 종합 | 본문 md 보관 | 없음 | 없음 | 없음 | `created_at`/`updated_at` TEXT |
| 6 | **AGI 신설 코어**(AGI-02 적용분) | (실수집 0) | `research_sources` **0** · `ingestion_runs` **0** · `research_documents` **0** · `research_document_revisions` **0** · `agi_schema_migrations` **1** | — | 스키마는 준비됨: `store_original` 기본 `metadata_only`, `external_model_send`·`publish_summary` 기본 `denied`(fail-closed) | `revision_hash` UNIQUE + `change_kind` | `access_state`(ok/deleted/restricted/unavailable) tombstone | **3등급 분리 보유** | 전부 `TIMESTAMPTZ` |

공통 실측: `public` BASE TABLE **558** (= AGI-02 적용 시점과 동일), 임시 테스트 스키마 잔여 **0**.

---

## 3. 로봇 접근성 실측 — "발견" 경로가 정책으로 막혀 있다

방법: 우리 제품 토큰 `stock-dashboard-agi-blog`(현행 초안 `FetchPolicy.user_agent` 와 동일)로 robots.txt 를 직접 받아
**초안 어댑터의 자체 파서**(`agi.adapters.collection_policy.parse_robots` → `RobotsRules.allows`, RFC 9309 최장일치·Allow 우선)로 판정했다.
(요청 5건: robots 4 + RSS 1. 문서 본문 0건.)

| 호스트 | 경로 | 판정 | 비고 |
|---|---|---|---|
| `blog.naver.com` | `/PostList.naver` (**현행 발견 경로**) | **차단** | robots.txt `User-agent: *` 에 `Disallow: /PostList.naver` 명시 |
| `blog.naver.com` | `/PostView.naver`, `/going_tothe_moon`, `/going_tothe_moon/{logNo}` | 허용 | 상세(본문) 페이지는 허용 |
| `m.blog.naver.com` | `/going_tothe_moon/{logNo}`, `/PostView.naver` | 허용 | 모바일 상세도 허용 |
| `m.blog.naver.com` | `/PostList…`(nhn 계열) | 차단 | |
| `rss.blog.naver.com` | `/going_tothe_moon` | **차단** | robots.txt 첫 줄 `# Block everything for every crawler`, `User-agent: * Disallow: /` → **RSS 발견 채널 사용 불가** |
| `cafe.naver.com` | `/f-e/cafe/30099599`, `/ArticleRead.nhn`, `/ca-fe/web/cafes/…` | **차단** | `User-agent: * Disallow: /` (= 카페 전체 크롤 금지) |

실측된 RSS 피드 자체는 살아 있다(HTTP 200 · 69,388B · `<item>` 50건, `going_tothe_moon`). 그러나 **robots 가 우리 그룹을 전면 차단**하므로
초안 어댑터의 `RobotsGate` 를 켜면 이 경로는 `FetchPolicyError(CODE_ROBOTS_DISALLOWED)` → 문서는 `unavailable`, 사유 `policy_blocked:robots_disallowed` 로 기록되고 **본문은 받지 않는다**(설계대로 fail-closed).

> **정책 충돌(Planner·owner 판정 필요)**: 현행 프로덕션 `HOT섹터블로그` 는 매일 07:00에 `Disallow` 경로(`/PostList.naver`)를 호출한다.
> AGI-03 어댑터로 이 소스를 옮기면 **수집 0건**이 정상 동작이 된다. 선택지는 ①정책 준수(수집 포기) ②공식 경로(네이버 검색 오픈API 등, 키·한도 = D07) ③명시적 예외 승인 후 수집.
> 어느 쪽이든 **소스 성격(3자 블로그)과 재배포/외부전송 범위**가 함께 확정돼야 한다(§6 B3·B4·B5).

---

## 4. 저장 경로·원문 보관 실태 (파손 지점)

| 대상 | 실측 | 의미 |
|---|---|---|
| `sector_posts` | 149행 중 `sector_stocks` 0건 **33행** | 파싱 실패분이 조용히 남아 있다(재파싱 로직 존재, 상세분석 아님 판정 시 텔레그램 미발송) |
| `telegram_channel_posts.photo_path` | 비어있지 않은 값 **0 / 11,453** | 사진 원문 보관 0건 — "모든 글 보관" 요구(4.2)의 미디어 축이 비어 있다 |
| `report_files.file_path` | 실존 **3,422 / 29,158 (11.7%)**, 25,736행이 `/Applications/stock_dashboard/reports/` | 죽은 경로. AGI-04(첨부 바이트) 착수 전에 **복원 가능/불가 판정**이 필요 |
| `cafe_signal_post_bodies` | 7,600행 / distinct hash 7,567, `article_id` 중복 0 | 본문 보관 + 변경 감지는 유일하게 구현됨, **이력은 미보관** |
| 전역 | 블로그/카페/텔레그램 어느 경로에도 **원문 파일 아카이브 디렉터리 없음** (`data/`=`collection_health.db`·CSV, `data_cache/`=parquet, `research_outputs/`=분석 산출물) | "NVME 원문 보관함"(계획 4.2)은 **아직 존재하지 않는다** |

---

## 5. 워킹트리 초안 코드 현황 (미추적·미커밋)

`git status`: `?? agi/` · `?? routes/agi.py` · `?? tests/*agi*` (AGI-02 적용분 + 아래 초안이 전부 미추적 상태)

| 파일 | 줄수 | 역할 |
|---|---|---|
| `agi/adapters/blog.py` | 604 | 어댑터 본체. `ListingEntry`/`ListingPage`(**complete/truncated → 삭제 스윕 허용 여부**), `PostDetail`, `FetchResult`, `BlogAdapter.ensure_source`(scope=`blog`, 기본 `publish_summary=denied`), `ingest_page`(목록→원문→revision), `_mark`, `_store_revision`, `_sweep_deletions` |
| `agi/adapters/blog_fetch.py` | 676 | 실제 HTTP 구현. 스킴/호스트/포트/userinfo allowlist, **DNS 리바인딩 차단(IP 고정 연결)**, 리다이렉트 재검증, 크기·content-type 상한, 전체 예산(`total_timeout_s`), robots 준수, 도메인별 예의 지연 |
| `agi/adapters/collection_policy.py` | 371 | robots.txt(RFC 9309) 파싱·판정, `Deadline` 예산, 예의 지연 계산 |
| `agi/adapters/errors.py` | 43 | 정책 차단 사유 코드 17종(`FetchPolicyError`) — 자유문장 금지, 집계 가능 |
| `tests/test_agi_blog_adapter.py` | 40 tests | 발견/원문/revision/삭제·권한 |
| `tests/test_agi_blog_http_fetcher.py` | 35 tests | `127.0.0.1` 일회용 `http.server` 만 사용, **외부 접속 0** |
| `tests/test_agi_collection_policy.py` | 52 tests | robots/지연/예산, **외부 접속 0** |

검증: `pytest` 위 3파일 **127 passed (24.14s)** — 실행 후 `public` 무변경, 임시 스키마 잔여 0.

**AGI-03 완료 조건 대비 커버리지**

| AGI-03 요구 | 초안 코드 상태 |
|---|---|
| 소스별 **발견** | ⚠️ **부분** — 발견은 "목록 1페이지 + 완전성 근거"라는 **주입형 입력**으로만 정의됨. **네이버/카페/텔레그램별 실제 목록 파서는 미구현**(`parse_listing` 은 소스 중립 dict 파서) |
| 원문 보관 | ✅ 본문 텍스트 + 미디어 참조(절대 URL) 보관, 첨부 **바이트**는 AGI-04 인계(해시 범위 = 본문 전용) |
| revision | ✅ `revision_hash`(단일 정규 알고리즘) + `change_kind` + UNIQUE 멱등 |
| 삭제 처리 | ✅ 목록의 `withdrawn` 마커 + **완전 목록일 때만** 스윕(불완전하면 판정 보류 = fail-closed) |
| 권한 처리 | ✅ `is_private` 항목은 **본문을 가져오지 않음**, 정책 차단 ≠ 서버 상태 코드(사유 코드 분리) |
| 검색 결과 한계 표시 | ✅ `ListingPage.truncated` + `limitation` → `coverage='denominator_unknown'` + `limitations[]` 를 실행 요약에 노출 |
| 프로덕션 배선 | ❌ 없음 — `agi.adapters` 를 import 하는 프로덕션 코드/스케줄러 잡 **0건**(테스트 외) |

---

## 6. D02(및 D06·D07) 없이 구현 불가한 항목 — 정확한 차단 목록

D02 = "보관할 블로그·텔레그램 범위"(소스 목록, 전체 과거 접근 범위, 채널/그룹/개인 대화 포함 여부).

| ID | 차단 항목 | 왜 결정이 선행돼야 하는가 | 해제 시 산출물 |
|---|---|---|---|
| **B1** | **시범 블로그 목록 확정** | `research_sources` 행을 만들려면 `source_owner`/`source_native_id`/`requested_from` 이 확정돼야 한다. 현재 관측된 3자 블로그는 1개(`going_tothe_moon`)뿐이고 owner 자신의 블로그 주소(D06)는 아직 없다 | 시범 소스 1~3개의 등록 + 1회 발견(목록만) |
| **B2** | **전체 과거 백필 범위** | 초기안 "최근 90일 우선 후 과거 전체"의 **어디까지**가 `requested_from`/`oldest_available` 이 되고, 어디부터 무료/유료 한도가 걸리는지 미정 | `requested_from` 확정 + 백필 큐 설계 |
| **B3** | **robots 차단 소스 처리 방침** (네이버 블로그 발견 경로·RSS·카페 전체) | 정책 준수 = 수집 0건, 우회 = 명시 승인 필요, 공식 API = 키/한도 결정 필요. **현재 레거시 잡은 매일 차단 경로를 호출 중**(§3) | 소스 성격별 접근 계약(허용 경로·한도·UA) |
| **B4** | **원문 보관 등급(`store_original`)** | 3자 블로그·카페 본문의 **전체 보관 여부**(저작권/약관)와 보관 기간. AGI 스키마 기본값은 `metadata_only`(fail-closed) | 소스별 `store_original`/`retention_class` 값 |
| **B5** | **외부 모델 전송(`external_model_send`)** | 현행 `blog_parser` 는 본문 **전체를 OpenAI 에 전송**해 요약한다. AGI 기본값은 `denied`. 허용 여부·범위(요약/번역/OCR) 미정 — D07(비공개 자료 범위·예산)과 결합 | 소스별 전송 등급 + redaction 규칙 |
| **B6** | **게시 요약(`publish_summary`)** | 블로그 초안/게시(AGI-08, D06)로 이어지는 노출 등급은 기본 `denied` 유지가 맞는지 확인 필요 | 소스별 게시 등급 |
| **B7** | **텔레그램 개인 대화 포함 여부** | 실측: 활성 14소스는 전부 공개(`-100…` 채널·슈퍼그룹 6, `@Valuefs1` 공개 username 1, `sunstudy1004`/`yigreport`/`jake8lee`/`gaoshoukorea`/`gatubang`/`petrojimmy`/`DOC_POOL` 은 `entity_hint` NULL = 성격 미분류 8행). **미분류는 등록 보류(fail-closed)** | 미분류 8행의 공개/비공개 판정 |
| **B8** | **보관 기간·백업 위치** | D07(보관 기간·첨부 범위·별도 백업) 미확정 상태에서 원문 보관함을 만들면 파손된 `report_files`(11.7% 실존)를 반복한다 | `retention_class` 기본값 + 백업 정책 |

**D02 와 무관하게 지금 진행 가능한 것**: 스키마 재사용(이미 적용됨) · 어댑터 로직 · **소스별 목록/상세 파서를 로컬 HTTP 픽스처 TDD 로 구현**(외부 접속 0) · 회귀 테스트.
**D02 없이 절대 진행 불가**: 어떤 소스든 **실수집·소스 등록·본문 보관**.

---

## 7. 다음 슬라이스 제안 (D02 미해제 상태에서도 무해)

1. **(제안 1 · 2026-10-11 구현 완료) `agi/adapters/naver_blog_listing.py` — 발견 파서를 픽스처 TDD 로 분리**
   구현: 목록 HTML → `ListingPage(entries, complete, truncated, limitation)`. **네트워크·DB 접촉 0**(순수 함수, stdlib `html.parser`).
   완전성은 목록이 스스로 말한 경우(`isLastPage`/`hasNextPage` 표시)에만 참이 되고, 항목을 잘라내면(`max_entries`) 완전 주장이 금지된다(거짓 tombstone 방지).
   `build_listing_url()` 은 `CollectionApproval` 없이는 **항상 예외**이고, 테스트가 모듈 소스를 검사해 네트워크 수입/호출이 생기면 실패한다.
   픽스처는 **합성**(`FIXTURE_PROVENANCE="synthetic"`) — B3 승인 후 실표본 캡처로 교체해야 실응답 호환이 검증된다.
   검증: `tests/test_agi_naver_blog_listing.py` **43 passed**(RED 6건 → 수정), AGI 전체 **243 passed + 12 subtests**, `py_compile` OK, `git diff --check` OK, 프로덕션 배선 0, DB 무변경(public 558·AGI 4테이블 0행·잔여 테스트 스키마 0).
2. **(제안 2) `agi/adapters/legacy_telegram_source.py` — 기존 `telegram_channel_posts` 를 읽기 전용 입력으로 변환**
   이미 보관된 11,453행을 **재수집 없이** 어댑터 입력(목록/원문)으로 매핑해 revision·삭제·권한 경로를 실제 데이터로 검증.
   `public` 무변경(읽기 전용) · 실수집 0.
3. **(제안 3) 초안 어댑터 커밋 분리** — `agi/adapters/*` + 3 테스트 파일을 **경로 지정 스테이징**으로만 커밋(§5). 프로덕션 배선은 하지 않는다.

---

## 8. 재현 명령 (읽기 전용)

```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime

# ① 소스별 원문 보관 실태 (읽기 전용 트랜잭션)
./venv/bin/python - <<'PY'
import re, os, psycopg
from pathlib import Path
url = next(re.match(r'\s*POSTGRES_DATABASE_URL\s*=\s*(.+)', l).group(1).strip().strip('\'"')
           for l in Path('.env').read_text().splitlines() if l.strip().startswith('POSTGRES_DATABASE_URL'))
with psycopg.connect(url.replace('+psycopg',''), connect_timeout=10) as c:
    cur = c.cursor(); cur.execute("BEGIN READ ONLY")
    cur.execute("SELECT file_path FROM public.report_files WHERE coalesce(file_path,'')<>''")
    ps = [r[0] for r in cur.fetchall()]
    print("report_files 실존:", sum(os.path.exists(p) for p in ps), "/", len(ps))
    for t in ("sector_posts","cafe_signal_posts","cafe_signal_post_bodies","telegram_channel_posts",
              "research_sources","research_documents","research_document_revisions"):
        cur.execute(f"SELECT count(*) FROM public.{t}"); print(t, cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM public.telegram_channel_posts WHERE coalesce(photo_path,'')<>''")
    print("telegram 사진 원문:", cur.fetchone()[0])
PY

# ② robots 접근성 (초안 어댑터의 자체 파서로 판정 — 본문 수집 0)
./venv/bin/python - <<'PY'
import urllib.request
from agi.adapters.collection_policy import parse_robots
UA = "stock-dashboard-agi-blog/1 (research; contact owner)"
def fetch(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=12) as r:
        return r.read().decode("utf-8", "replace")
for host, path in (("blog.naver.com","/PostList.naver"), ("blog.naver.com","/going_tothe_moon/224265571184"),
                   ("rss.blog.naver.com","/going_tothe_moon"), ("cafe.naver.com","/f-e/cafe/30099599")):
    rules = parse_robots(fetch(f"https://{host}/robots.txt"))
    print(host, path, "allowed=", rules.allows(path, product_token="stock-dashboard-agi-blog"))
PY

# ③ 초안 어댑터 회귀 테스트 (임시 스키마 DDL 후 DROP · public 무변경)
./venv/bin/python -m pytest tests/test_agi_blog_adapter.py tests/test_agi_blog_http_fetcher.py \
                              tests/test_agi_collection_policy.py -q
```

**미해결/주의**: ①이 문서는 소스 상태의 스냅샷이다(라이브 트리·DB는 계속 움직인다) — 판정 시각을 함께 기록해야 한다.
②`report_files` 의 죽은 경로 25,736행 복원 가능성은 **미확인**(별도 판정 항목).
③`agi/adapters/*` 는 아직 **미추적**이므로 이 문서의 §5 서술은 워킹트리 상태에 종속된다.
