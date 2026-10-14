# Stock System 확장 — 미결 작업 목록

작성: 2026-10-10 KST · 최근 검토: 2026-10-13 KST · 담당: Codex / 이효준 owner
상세 정본: [Improvement_Stock_System.md](Improvement_Stock_System.md)
전체 시스템 색인: [OPEN_ITEMS.md](OPEN_ITEMS.md)

## 1. 관리 원칙

이 문서는 Stock Lab·Key Indicator 확장 계획의 실행 목록이다. 기존 숫자 정정·전략 결함 작업을 복제하거나 완료로 재판정하지 않는다. 계획 이후 AGI-02 기반 및 AGI-03 어댑터가 부분 구현됐다. **2026-10-13 독립 검토: 운영 AGI 테이블 적용·조회 API 응답 확인, 수집 4테이블 각 0행. R1~R4 결함은 실수집 전 수정 필요.** 상세는 [AGI_REVIEW_20261013.md](AGI_REVIEW_20261013.md).

상태는 `planned`(계획), `in-progress`, `blocked`(명시된 선행 조건 미충족), `review`, `done`, `deferred`로 기록한다. 실제 작업 시작 시 담당·시작일을 추가하고 완료 시 코드/산출물 경로·검증 건수·실행 ID·잔여 한계를 남긴다. 증거 없는 완료 처리는 하지 않는다. 우선순위 P0/P1/P2는 중요도이며 상세 계획의 단계 P0~P5와 다르다.

사용자 결정은 구현 전체를 막지 않는다. 계정·위험 한도·게시처럼 그 결정에 직접 의존하는 부분만 대기하고, 스키마·오프라인 표본·검증 도구·초안 작업은 진행할 수 있다. 후속 구현·운영 범위는 최신 상세 계획과 개별 통제 기록을 따른다. 2026-10-13 요청은 Hermes 작업 검토다.

## 2. 사용자 결정과 확인 사항

| ID | 상태 | 결정 항목 | 현재 해석·제안 | 필요한 시점 / 의존 작업 |
|---|---|---|---|---|
| D01 | done | “안좋은 주식을 사전에 투자” 의미 | **2026-10-10 사용자: “1, 2번 주식 모두”**. ① 실적 부진→개선 조짐, ② 좋은 기업→시장 관심 이전의 두 전략으로 확정. 각 전략의 세부 임계값은 실험 대상 | AGI-09/13에 반영 완료(설계) |
| D02 | open | 보관할 블로그·텔레그램 범위 | 블로그 URL·텔레그램 소스 목록, 전체 과거 접근 범위, 채널/그룹/개인 대화 포함 여부. 초기안은 기존 활성 소스·개인 대화 제외, 최근 90일 우선 후 과거 전체. **실측(2026-10-11)**: `telegram_channels.channel_id` 는 숫자 id 가 아니라 **설정 키(표시명/username)** 이고 실제 엔티티는 `entity_hint`(`-100…`=채널·슈퍼그룹, `@Valuefs1`=공개 username, NULL 8행)에 있다. 활성 14개 소스는 **전부 공개**로 판정되고 개인 대화는 이 목록에 없다 → "개인 대화 제외" 기본값은 현재 구조적으로 성립(제외 0건). 미분류는 등록하지 않는다(fail-closed) | 신규 수집 착수 전 AGI-02/03; 기존 설정 목록 읽기는 가능 |
| D03 | done | 기업·시장 우선순위 | **2026-10-13 사용자 승인("D03 지정") — 실제 보유 10종목 전부 + OSAT(반도체 후공정) 5종목 = 15개 시범 집합으로 확정**(근거: 운영 `portfolio` 테이블의 실제 보유분을 그대로 사용, 신규 선정 없음). 보유: 에이엘티(172670)·아이엠티(451220)·에스티아이(039440)·티에프이(425420)·월덱스(101160)·에스지헬스케어(398120)·큐리오시스(494120)·리브스메드(491000)·LS에코에너지(229640)·AJ네트웍스(095570). OSAT 비교군(신규 선정, 사용자 재확인 전 임시): 네패스(033640)·네패스아크(330860)·하나마이크론(067310)·SFA반도체(036540)·윈팩(097800). 해외 비교는 이 15개의 근거가 쌓인 뒤 후속 결정 | AGI-06/07 시범 데이터로 사용 가능 |
| D04 | open | 운용 제약 | 투자 기간, 최대 낙폭·손실, 최소 현금, 종목/산업/국가/통화 집중 한도, 레버리지, 거래 비용·계좌 제약. 미확정 수치를 운영에 사용하지 않음 | AGI-14 운영 정책 확정 전, AGI-17 필수 |
| D05 | open | 완전 자동 운용 전환 범위 | 계좌·자산·자금 한도·주문 권한·중단/복구·자동 재개 조건과 L3/L4 전환. 최종 목표는 완전 자동화로 기록됨 | AGI-17 실거래 전환 전. 모의·그림자 설계는 진행 가능 |
| D06 | open | 블로그·알림 운영 | 내 네이버 블로그 주소·글 스타일, 초안 검토/게시 방식, 알림 받을 목적지·시간·긴급 조건. 초기안은 게시용 초안과 개인 일일 요약 | AGI-08 게시 연동/AGI-16 외부 발송 전 |
| D07 | open | API·저장·백업 예산 | 월 모델/OCR·데이터 예산, 외부 모델에 보낼 수 있는 비공개 자료 범위, 보관 기간·첨부 범위, 별도 백업 위치 | AGI-04/15 운영 설정 전. 7일 표본 비용 추정 제안 |
| D08 | open | 외국계 보고서 접근 범위 | 이용 중인 기관/서비스·다운로드 가능 자료·계약상 저장/모델 전송/공개 요약 범위 | AGI-11 소스 연결 전 |

D02~D08을 지금 모두 답해야 계획서를 사용할 수 있는 것은 아니다. 착수 단계에 필요한 결정부터 확인한다. 계정 비밀번호·API 키는 문서에 적지 않고 연결 설정에서 처리한다.

## 3. 실행 백로그

작업별 최신 상태는 아래 표를 따른다. ‘담당’은 착수 시 확정할 역할이다. 완료 조건의 상세 수치와 공통 품질 기준은 계획서 §18을 따른다.

**연속 실행 원칙(2026-10-11):** Planner는 `review` 또는 `blocked` 결과를 받은 즉시 다음 독립·무해 작업의 owner와 완료 산출물을 지정한다. 선행 차단이 전체 백로그를 멈추게 해서는 안 되며, 각 handoff에는 다음 owner·차단 조건·재검증 명령을 남긴다. 이 원칙을 지키는지 독립 점검하는 durable schedule이 없으면 야간/세션 종료 전에는 완료가 아니라 운영 결함으로 기록한다. 단, live DB write·수집·재시작·외부 발송·주문은 기존 승인 규칙을 그대로 따른다.

**DeepSeek 피크/비피크 시간대(2026-10-11 정정):** 피크(2배 요금) = **평일 KST 10:00–13:00·15:00–19:00**(= UTC 01:00–04:00·06:00–10:00, 월~금, 중국 공휴일 제외) / 비피크 = 그 외 전 시간 **+ 주말·중국 공휴일 전일**. 출처: `api-docs.deepseek.com/quick_start/pricing` verbatim — *"Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, Monday through Friday, excluding Chinese public holidays. All other hours are off-peak, including weekends and Chinese public holidays in full."* (2026-08-16부터 V4 peak/off-peak 과금, 종전 V3/R1 off-peak 할인은 폐지 → 종전에 적었던 "평일 09:30~익일 01:30 피크"는 **폐지된 구간**이라 정정). DeepSeek 의존 코딩은 비피크에 배치하고, 발행 스케줄 19:15 KST는 피크 종료(19:00) 직후라 그대로 유지한다. 현재 발행 스케줄 `stock-dashboard-deepseek-offpeak-dispatch`는 모델 미고정(unpinned)이라 DeepSeek 모델 자체를 기술적으로 보장하지 못한다 — 그래서 위 연속 실행은 명시 handoff를 1차 수단으로 하고 스케줄은 누락 감지용 2차 안전망으로 둔다.

| ID | 우선 | 작업·산출물 | 선행 조건 | 담당 역할 | 완료 증거 | 상태 |
|---|---|---|---|---|---|---|
| AGI-00 | P0 | 기존 시스템·품질 이슈·작업 중 변경 조사, 재사용/확장 지도 | runtime/AGENTS 및 최신 정본 재독 | 설계·백엔드 | `AGI_00_SYSTEM_BASELINE_20261010.md`: PID/CWD/PG primary, 파일·테이블·API·잡·기존 미결 영향/차단 조건을 2026-10-10 23:34 KST 읽기 전용으로 실측 | done |
| AGI-01 | P0 | 소스 레지스트리·논리 스키마·권한·시점 계약 | AGI-00 | 데이터·백엔드 | **문서 계약 초안** `AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`: 재사용/확장/신설 대조표(M1~M12 실측), 시간 4종, 멱등키·revision·tombstone, 권한 3분리, PIT·quality 게이트 G1~G7, API prefix 충돌 해소, DDL·rollback 규칙, 수용기준 T1~T13 | review |
| AGI-02 | P0 | 텔레그램 전체 과거 백필·신규/수정 수집·커서 확장 | AGI-01, D02 | 수집 | **슬라이스 구현 + migration 0001 `public` 적용(2026-10-11), 실수집 미착수**: `agi/` 패키지(revision_hash §5-1 정규 알고리즘·시간 4종 단일 변환점·migration up/down 4테이블+원장·멱등 upsert·커서 재개) + `routes/agi.py`(공개 무부작용 GET + **owner 전용** 비공개 뷰) + `scripts/migrate_agi_research_core.py`(dry-run 기본) + 테스트 3파일 61 passed(실 PG16 임시 스키마, teardown drop). 재실행 중복 0·수정→revision 추가·중단재개 동일성·공개 뷰 비공개/denied 차단 검증 완료. **해시 범위 = 본문 전용(첨부 바이트는 AGI-04)**. **공개 노출 = 명시 allowlist(2026-10-11 회귀 수정)**: `PUBLIC_SCOPES=('public_channel','blog','report')` 외 등급(`other`/미분류, 신규 scope 포함)은 상태값 조합과 무관하게 목록·상세·watermark 에서 0건(fail-closed, 12조합 테스트). **2026-10-13 사용자 승인으로 실수집 1차 완료**: migration 0002 적용(§7) + 활성 14소스 전부 `backfill()` 백필 11,546건(중복 0, 전부 success, 본문은 D07 미결로 metadata_only 유지 → 0건 저장, 공개 노출은 여전히 0건). 남은 것: 접근 불가 구간 기록(실제로 겪은 사례 아직 없음)·본문 보관 수준(D07) 결정 | in-progress |
| AGI-03 | P0 | 블로그 소스별 발견·원문 보관 어댑터 | AGI-01, D02 | 수집 | 어댑터·HTTP 정책·합성 네이버 목록 파서 부분 구현. 10-13 AGI 전체 252 tests + 12 subtests 통과(R1~R4 수정·회귀 테스트 포함 — [검토서](AGI_REVIEW_20261013.md) §7). 원천 응답/실수집 검증은 여전히 미완 | in-progress |
| AGI-04 | P0 | PDF/OCR·기업 식별·주장/근거·검색·revision 파이프라인 | AGI-01; AGI-02/03 표본, D07은 외부 전송 전 | 데이터·리서치 | 층화 표본 기업 식별/주장 검증, 원문 위치 복원, 권한 밖 검색 결과 0. **AGI-02 슬라이스가 남긴 첨부 바이트 해시 경로(`attachment_digest = sha256(바이트 원문)`)를 이 항목이 인수한다**(Planner 확정 2026-10-11) | planned |
| AGI-05 | P1 | 관심도·독립 출처·방향성·서사 변화 화면 | AGI-04 | 데이터·프런트 | 고정 분모 비교, 중복군·동음이의어·신규 소스 효과 테스트, 결측/커버리지 표시 | planned |
| AGI-06 | P0 | 지속 갱신 기업 보고서·근거·차이·버전 조회 | AGI-04, D03; 숫자 품질 계약 | 백엔드·리서치·프런트 | **2026-10-13 설계안 작성**([AGI_06_COMPANY_REPORT_DESIGN_20261013.md](AGI_06_COMPANY_REPORT_DESIGN_20261013.md)) — 실제 증권사 리포트 2건을 읽고 구성(헤더·분기Review·추정변경·Snapshot표)을 기존 테이블(financial_data·segment_revenue·company_product_mix·analyst_pdf_extracts·dart_disclosures·sector_taxonomy peer_group)에 매핑, 갱신 트리거·revision 버전 설계. **2026-10-13 2차: 업종별 4~5건 리포트(반도체·금융지주·의료기기·조선)를 읽고 업종별 정형 폼 구성 + 실제 DB 숫자로 샘플 보고서 3건(하나마이크론·리브스메드·AJ네트웍스) 수동 작성**([AGI_06_SECTOR_REPORT_SAMPLES_20261013.md](AGI_06_SECTOR_REPORT_SAMPLES_20261013.md)) — 의료기기는 해외 규제 승인 추적이 국내 공시만으로 안 되고, 복합 사업(렌탈 등)은 업종 템플릿이 따로 없어 부문별 분해가 필요함을 확인. **2026-10-14 자동 조립·저장 1차 구현 완료**(사용자 지시 "설계만 하지 말고 리포트를 구성해봐" → "네, 진행해줘"; 중간에 "업종별 애널리스트 리포트를 더 넓게 읽고 하라"는 지시를 받아 LS에코에너지·SK텔레콤·LG화학 리포트 3건을 추가로 읽고 템플릿을 3종 더 확장(소재/밸류체인·배당/유틸리티·지주 SOTP, 총 7종)한 뒤 구현): `agi/company_report.py`(섹션 7종 조립·업종 7분류·콘텐츠 해시 기반 revision) + migration **0003**(`company_reports`/`company_report_sections`, `public` 적용 완료) + 테스트 15개. **실제 보유 18종목 전부 생성**(하나마이크론 3Q25 실제 매출 4,324억·영업이익 384억이 참고했던 메리츠 리포트 숫자와 정확히 일치 확인). 실측 중 데이터 품질 결함 1건 발견·수정: `order_backlog`에 전부 NULL인 과거 placeholder 행이 있어 AJ네트웍스가 "모양만 보고" order_backlog_industry로 오분류됨 — 값 존재 여부로 판정하도록 수정. 공개 화면·자동 트리거(실적발표/공시 감지)는 아직 없음(이번 호출은 수동 1회 실행). **2026-10-14 후속 — 조회 API 추가**: `routes/agi.py`에 `GET /api/agi/company-reports`(목록)·`GET /api/agi/company-reports/{code}`(1건, 섹션별 최신 revision) 신설 — owner 전용으로 시작(내용 자체는 기존 공개 대시보드 숫자를 조립한 것뿐이라 민감하지 않지만, 이 조립 로직이 아직 검증 전이라 공개 전환은 보류). SELECT 전용, 쓰기 엔드포인트 없음. `main.py`는 건드리지 않음(기존 `/api/agi` 등록을 그대로 재사용). 테스트 5개 추가(`tests/test_agi_company_report_api.py`). `scripts/safe_restart_backend.sh`로 재시작(고아 프로세스 없음, 200 확인) → 실제 토큰 인증으로 18종목 응답 확인(`count=18`, 하나마이크론 067310 상세 포함). `gen_api_doc.py`/`gen_db_doc.py` 재생성(`/api/agi` 5→7개), CLAUDE.md §12 기록. **2026-10-14 자동 갱신 트리거 추가**: launchd `com.stock-dashboard.agi-company-report-refresh`(매일 07:00, DART 00:20·FnGuide 04:00·재무무결성일일 06:20 이후) — `scripts/review/agi_company_report_refresh_20261014.py`가 `company_reports ∪ portfolio ∪ buy_candidates` 종목을 매일 재조립한다. 개별 이벤트(실적발표/공시)를 감지하지 않고 **매일 다시 조립 + 섹션별 콘텐츠 해시**로 바뀐 것만 새 revision — 안 바뀌면 쓰기 0건. 1차 실행으로 47종목(기존 18 + 포트폴리오·매수후보 신규 29) 생성, 2차 실행(변경 없음) 0건 쓰기로 확인. `launchctl load` 완료(`launchctl list`에 등록). **2026-10-14 공개 화면 추가**(사용자 지시 "Stock Lab에 공개화면 만들어줘"): `routes/agi.py`의 `/company-reports`·`/company-reports/{code}`를 owner 전용에서 **공개(인증 없음)**로 전환(`require_owner` 제거, `/documents`와 동급 — 내용이 이미 공개 대시보드 숫자를 조립한 것뿐이라 비민감 판단) + `agi_status()` 라우트 목록·테스트(`tests/test_agi_company_report_api.py`, `tests/test_agi_documents_api.py`) 갱신. 프런트: `frontend/src/views/CompanyReportsView.jsx` 신설(종목 목록 검색 + 상세: 개요·분기실적(QoQ)·마진괴리 플래그·사업구성·최근공시·업종고유 섹션, `index.css` CSS 변수 사용), `hub/modules.js`의 Stock Lab '종목 발굴' 섹션과 `App.jsx`(lazy import·NAV_DEFS·렌더 스위치)에 `company_reports` 탭 등록. `npm run build` 완료 → `scripts/safe_restart_backend.sh` 재시작(고아 프로세스 없음, 200) → 실서비스 도메인(`stock.leanguy.cloud`)에서 인증 없이 `GET /api/agi/company-reports` 200·count=47 확인. `gen_api_doc.py` 재생성. **2026-10-14 외부 검토(Codex) 5건 반영**: ① launchd 자동 갱신이 등록만 되고 실행 이력 0건(`runs=0`)이던 것을 `launchctl start`로 1회 실제 트리거해 확인(`runs=1`, exit 0, 19개 섹션 변경 반영, 로그 정상) — 매일 07:00에도 같은 경로로 돈다. ② `store_original='no'` 정책 스킵이 콘텐츠 중복(`skipped_duplicate`)에 잘못 합산되던 결함 수정: migration **0004**(`ingestion_runs`/`research_sources.skipped_no_store_policy`, `public` 적용 완료)로 전용 컬럼 신설, `agi/repository.py`(`finish_run`/`increment_source_progress`)·`agi/service.py`(`_store_post`가 bool→`"stored"`/`"duplicate"`/`"policy_skipped"` 3상태 반환, `ingest_source`/`ingest_specific_messages`/`backfill`이 카운터 분리)·`agi/adapters/blog.py`(기존에 인메모리로만 분리돼 있던 `not_stored_by_policy`를 이제 DB에도 기록)까지 전부 수정, 회귀 테스트 2개 추가(`test_agi_research_ingestion_contracts.py`). ③ `tests/test_agi_company_report_api.py`가 운영 DB의 067310 실존 데이터에 의존하던 것을 제거 — `stock_code='999999'`(실제 KRX 코드 아님, 존재 확인됨) 전용 픽스처를 테스트 setUp에서 만들고 tearDown에서 지우는 자기완결형으로 재작성(신규/초기화 환경에서도 통과). ④ 설계 문서 `AGI_06_COMPANY_REPORT_DESIGN_20261013.md`가 "구현 전"이라고 적혀 있던 걸 "2026-10-14 1차 구현 완료"로 갱신 + §7 다음 단계에 투자포인트/리스크 섹션 미구현 사실 명시. ⑤ 투자포인트·성장성·리스크·매수매도 판단 섹션은 **아직 범위 밖**(AGI-07과 연결해 확장 필요, 사용자 확인 전 임의 확장 안 함) — 현재는 "기반 저장·갱신·공개 조회까지 완료된 1차 버전"으로 본다. 테스트 전체(블로그·텔레그램·마이그레이션·기업보고서) 280 passed 확인, `safe_restart_backend.sh` 재시작(200), `gen_db_doc.py` 재생성. 남은 것: 투자포인트/리스크 섹션 확장(AGI-07), OSAT inferred 2종목 승격 여부, 관리(쓰기) 화면 여부, D07(본문 보관) 결정 | in-progress |
| AGI-07 | P1 | 실적/가격 4분면·OSAT 사전·피어 비교·통계 원장 | AGI-06, D03, 기존 PIT/가격 품질 조건 | 데이터·전략 | 당시 공개값·기업행위·CFS/OFS·단위·비교 불가 처리, 관측/제외 수·통계 결과 | planned |
| AGI-08 | P1 | 네이버용 초안·출처·차트·검토·게시 이력 | AGI-06; D06은 게시 연동 전 | 리서치·프런트 | 초안 3종(기업/전략/거시) 검토, 숫자/근거 누락·비공개 원문 공개 0, 게시 상태 전이 | planned |
| AGI-09 | P0 | owner 원칙 원장·A/B/C 가설·변경 관리 | D01 완료, AGI-00 | 전략·owner | **문서 계약 초안** `AGI_09_OWNER_POLICY_LEDGER_CONTRACT_20261011.md`: 원문 verbatim 원장(OP-01~05)·해석/측정/반증 버전, A/B/C 카드 필수필드, 불변 연결 체인, 시스템 자율 변경 금지(SIGNAL_RULES §6 단일경로), 승격 게이트 P1~P9, 수용기준 T1~T16 (append-only 보증은 앱 롤 한정 + 소유권/롤 멤버십 분리, privileged 경로는 break-glass 운영 통제) | review |
| AGI-10 | P1 | 기존 거시지표 조사·빈티지·이벤트·Key Indicator 연동 계약 | AGI-01; 외부 저장소 규칙·구조 확인 | 데이터·연동 | 지표 소유자/코드/단위/발표시각 매핑, 개정·지연·장애 계약 테스트 | planned |
| AGI-11 | P1 | 외국계 보고서 수집·번역·전망 변경·주장 구조화 | AGI-04, D08 | 수집·리서치 | 허용 원문으로 시범 검증, 페이지별 근거, 실제값/전망 구분·번역 대조 | planned |
| AGI-12 | P1 | 글로벌 투자 기회 카드·채권/환율 시나리오·추적 | AGI-07/10, AGI-11 또는 허용 공개 보고서 | 리서치·데이터 | 브라질/일본 채권 템플릿, 금리/환율/비용 가정·반증·실패 사례·데이터 공백 표시 | planned |
| AGI-13 | P0 | 전략 A/B/C 및 거시·관심도 증분 실험 | AGI-05/07/09/10, 기존 전략 결함/PIT 조건 | 전략·검증 | 시점·비용·상장폐지·평가구간 분리, 비교군·기각 실험·재현 해시 | planned |
| AGI-14 | P0 | 전략 조합·주식/현금 목표 비중·그림자/모의 운용 | AGI-13; D04는 운영 정책 확정 전 | 전략·운용 | 비중/한도/중복 종목/가용 현금 테스트, 고정 비중 비교, 모의 체결·대사 로그 | planned |
| AGI-15 | P0 | 작업 원장·비용 상한·권한·별도 백업·복원 | AGI-01, D07; 각 단계와 병행 | 운영·백엔드 | 실패 전파·재시도·예산 소진·디스크 부족·권한·복원 훈련 증거 | planned |
| AGI-16 | P1 | 일일/긴급 브리핑·중복 억제·주간 복기 | AGI-06/10/12, D06은 발송 전 | 리서치·백엔드 | 이벤트별 멱등키, 오탐·미탐·지연·정정 테스트, 화면 미리보기 | planned |
| AGI-17 | P2 | 제한 실거래→완전 자동 운용 | AGI-14/15, D04/D05, L2 표본·기간 조건 충족 | 운용·검증·owner | 소액 전환 승인 기록, 중복 주문 0·부분체결·재시작 대사·수동 정지·복구 훈련 | planned |

AGI-15는 마지막에 몰아서 하는 작업이 아니다. P1부터 원문·DB 백업과 잡 원장을 적용하고, 기능 확대 때마다 비용·권한·복구 검증을 함께 한다. AGI-17의 낮은 초기 우선순위는 목표 제외가 아니라 선행 검증이 많다는 뜻이다.

## 4. 기존 작업과의 연결·충돌 방지

| 기존 항목 | 이 계획에 미치는 영향 | 처리 원칙 |
|---|---|---|
| OPEN_ITEMS A8 텔레그램 관심도 중단 | 새 관심도 요구와 운영 상태가 다름 | 중단 기록 유지, 신규 요구로 재설계; AGI-05에서 검증 후 별도 활성화 |
| OPEN_ITEMS C3-2/C3-4, FINANCIAL §9-2-7/8 | 미확인 재무·정정·연결/별도 기준 | 보고서에 품질·결측 표시, 해당 숫자 의존 실험/운용 차단; 기존 정정 작업 별도 유지 |
| OPEN_ITEMS C11, Stock_Strategy의 PIT·후보 일관성 | 새 실험의 신뢰도 | AGI-13 착수 때 최신 해결 여부 확인. 과거 문서 상태만으로 해결/미해결 단정 금지 |
| SIGNAL_RULES §5/6 | 기존 신호 조합의 한계·변경 절차 | 공통 신호 복제 금지, 규칙 변경은 재검증·승인·정본 갱신 |
| OPEN_ITEMS A9·작업 트리 프런트 변경 | 신규 화면과 충돌 가능 | 기존 변경 소유·상태 확인, 범위 한정 편집, 일괄 커밋/덮어쓰기 금지 |
| Key Indicator·Brian_RAG 별도 프로젝트 | 현 runtime만으로 외부 수정 불가 | API/권한/스키마 계약부터 확정, 각 저장소 규칙 확인 후 별도 구현 |

## 5. 다음 실행 묶음

1. AGI-00: 운영 변경 없는 읽기 전용 현황 조사. 사용 테이블·수집 잡·API·기존 기능·미결 의존성을 표로 확정한다.
2. AGI-01/09: 원문/주장/시점/권한 계약과 owner 정책 v1을 설계한다. D01의 두 투자 유형을 실험 카드로 만든다.
3. D02/D03: 기존 설정을 보여 주고 시범 소스·기업을 선택한다. 새 자격증명이 필요한 항목만 연결한다.
4. AGI-02/03/04/06/08의 작은 흐름으로 ‘원문→기업 갱신→블로그 초안’을 검증한다. AGI-15의 멱등·복구·비용 측정을 함께 한다.
5. MVP 검토 후 P3/P4로 확대한다. 전체 원문 백필은 신규 수집을 방해하지 않는 별도 큐에서 계속한다.

## 6. 완료·변경 로그

| 날짜 | 구분 | 내용·증거 |
|---|---|---|
| 2026-10-10 | 계획 완료 | Improvement_Stock_System.md 작성: 요구 4.1~5.4 매핑, 기존 기능 재사용, 데이터/화면/API/검증/자동화 승격/단계별 노력 추정 |
| 2026-10-10 | 결정 완료 | D01: 사용자 “1, 2번 주식 모두”를 회복 선행·미발견 성장으로 반영 |
| 2026-10-10 | 색인 연결 | 기존 OPEN_ITEMS.md에 이 문서와 상세 계획 연결. 기존 미결 작업·운영 설정은 변경 없음 |
| 2026-10-10 23:34 KST | AGI-00 완료 | 읽기 전용 라이브 베이스라인 `AGI_00_SYSTEM_BASELINE_20261010.md` 작성. PID 2936/CWD runtime/PostgreSQL primary를 확인하고, 텔레그램·기업·거시 재사용 경계, 미래 날짜/실패 원장/PIT·품질 차단을 AGI-01/09의 선행 계약으로 고정. 코드·DB·수집·재시작 없음 |
| 2026-10-11 09:24 KST | AGI-01/09 초안 작성 | Code Doer가 AGI-00만 입력으로 두 문서 계약 초안 작성 — `AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`(대조표·시간 4종·멱등키·권한·G1~G7·DDL/rollback·T1~T13), `AGI_09_OWNER_POLICY_LEDGER_CONTRACT_20261011.md`(OP-01~05 원문 원장·A/B/C 카드·승격 게이트 P1~P8·T1~T12). 두 항목 `review`로 이관, Planner 대행 검증 대기. 근거는 AGI-00 + 2026-10-11 읽기 전용 재측정(M1~M12: `transaction_read_only=on`). 코드·DDL·DB write·수집·재시작 없음 |
| 2026-10-11 10:0x KST | AGI-01/09 BLOCK 수정 | Planner 독립 검증 `BLOCK` 4건 반영(문서 전용): ① AGI 문서 세트를 `runtime/AGENTS.md` 신규 섹션 + 변경 이력에 기록 ② `revision_hash` 정규 알고리즘(첨부해시 **정렬 규칙**·`attachment_key`·알고리즘 버전 접두·`change_kind`)과 실행원장↔엔티티 구분을 AGI-01 §5-1·§5-2로 고정 ③ append-only를 **DB에서 강제**(앱 롤 `INSERT/SELECT`만 + `REVOKE UPDATE/DELETE/TRUNCATE` + `raise_append_only()` trigger + 카탈로그 단언, 검증 하네스는 **최소권한 앱 롤**로 실행해야 증거) — AGI-09 §3-1 ④ "재실행=새 run_id" vs "중복 결과 0" 모순 해소 = `experiment_runs`는 run 3행(append-only) / `experiment_outcomes`는 자연키 1행. 수용 테스트 AGI-01 T1·T14, AGI-09 T9·T13·T14 추가. `/api/agi/*`는 Planner 결정으로 확정, OP-01~05 `source_ref`는 사용자 근거 확인 전 **미확정 유지**. 코드·DDL·DB write·수집·재시작 없음 |
| 2026-10-11 09:29 KST | Planner 운영 결함·즉시 시정 | OFS의 `BLOCKED`를 전체 AGI 실행 중지로 과대 해석하고, `review` 재제출 뒤 다음 owner/산출물/재검증을 지시하지 않았으며 durable scheduler도 **0개**였다. 따라서 작업이 불필요하게 멈췄다. 즉시 (a) 이 문서에 연속 실행 원칙을 고정, (b) AGI 계획에 남아 있던 `/api/research/*` API 제안을 확정 namespace `/api/agi/*`로 정정, (c) AGI-01/09 계약의 독립 검증과 AGI-02/03의 읽기 전용 착수계획을 병렬 handoff한다. OFS `--apply`와 live write/수집/재시작 금지는 유지한다. |
| 2026-10-11 09:5x KST | Planner 3차 지적 반영 + 요금구간 정정(문서 전용) | ① AGI-09 §3-1-5(c)를 **감사 로깅 가정 금지**로 재작성(`log_statement`=SUSET → 작업 전 `ALTER SYSTEM`/`ALTER ROLE … SET`(`pg_db_role_setting`)/PG15+ `GRANT SET ON PARAMETER` + 세션 `current_setting('log_statement')='all'` + **canary 문장의 서버 로그 실기록 확인**(감사 owner, `pg_current_logfile()`), 미보장 수행 = 통제 위반) ② AGI-09 §3-1-6을 **실효 권한 단언**으로 교체(`has_table_privilege` 3권한 false + `has_any_column_privilege('UPDATE')` false + 재귀 멤버십 폐쇄 `pg_has_role` `'USAGE'`·`'SET'` + `REVOKE … FROM PUBLIC` 의무, `role_table_grants`는 감사 기록용으로 강등) ③ T13/T15/T16·P9 정합 수정 ④ **Q6 승인 반영**(`agi_breakglass`·앱 롤 미부여·owner+Planner 사전 2인 승인·`privileged_access_log`+별도 PG 감사 로그; 롤·권한 생성 DDL은 별도 승인 전 금지) ⑤ **DeepSeek 요금구간 정정**: 공식 문서 verbatim 기준 피크 = 평일 **KST 10:00–13:00·15:00–19:00**, 그 외 전 시간 + 주말·중국 공휴일 전일이 비피크(종전 "평일 09:30~익일 01:30"은 폐지된 V3/R1 할인 기준). `runtime/AGENTS.md`(섹션+이력 2행)·`docs/AGI_09_…`(§3-1·§7·§10·§11·§12)·`Open_Items_AGI.md`(§3 문단) 동시 반영. 코드·DDL·DB write·수집·재시작 없음, DB 접속 0 |
| 2026-10-11 10:5x KST | **AGI-02 첫 수직 슬라이스 구현(live 미적용)** | Planner handoff 이행. 신규 `agi/` 패키지 — `revision.py`(계약 §5-1 해시 정규 알고리즘: NFC·개행/공백 정규화, `attachment_key` `__nopath__/NNNN`, `(key,digest)` **바이트** 정렬, 중복 축약, `v1\n` 접두, `change_kind` 분류)·`timeutil.py`(M2 두 TEXT 포맷의 **단일 변환점**, 신설은 `timestamptz` 만)·`migrations/m0001_research_core.py`+`migrations/runner.py`(`research_sources`/`ingestion_runs`/`research_documents`/`research_document_revisions` up·down + `agi_schema_migrations` 원장, 멱등·스키마 무관 checksum·드리프트 거부·방향별 1트랜잭션)·`repository.py`/`service.py`(ON CONFLICT 멱등 upsert, 실행원장↔엔티티 분리, 커서 재개, 읽기 전용 텔레그램 시드)·`routes/agi.py`(`GET /api/agi/documents`, `/documents/{id}`, `/status` — 읽기 전용 연결, `as_of`·`snapshot_generation_time`·`source_watermarks`·`quality_summary`·`unconfirmed_fields` 봉투, 미적용 시 500 대신 `applied:false`)·CLI `scripts/migrate_agi_research_core.py`(기본 dry-run, `--apply` 별도 승인). 검증: 테스트 3파일 **55 passed** — **실 PostgreSQL 16** 일회용 스키마(`agi_slice_test_*`, teardown `DROP SCHEMA CASCADE`)에서 실제 DDL 실행(UNIQUE 23505·FK 23503·CHECK 23514 실측), 3회 재실행 중복 0·이전 run 행 불변, 본문수정→revision 1개 추가, 배치 중단 후 재개 == 1회 실행, GET 전후 지문 동일, 레거시 `telegram_*` **쓰기 0**(문장 기록기). live migration/apply·실수집·재시작 없음, `public` 무변경(AGI 신설 테이블 부재 실측) |
| 2026-10-11 10:47 KST | **AGI-02 migration 0001 `public` 적용(Planner 승인 범위)** | `scripts/migrate_agi_research_core.py --apply --applied-by "code-doer (planner-approved 2026-10-11)"` → `status=applied`, checksum `bba1c012…`, `applied_at 2026-10-11 10:47:13.841382+09:00`. read-back: `public` BASE TABLE **553 → 558(+5 = 정확히 신설 4테이블+원장)**, ledger 1행(checksum == 현재 DDL), 데이터 4테이블 **0행**(실수집 0), 기본 deny 값 전부 일치(`store_original=metadata_only`/`external_model_send=denied`/`publish_summary=denied`/`retention_class=term`/`is_private=false`), PK5·UNIQUE4·FK5·CHECK13·명명 인덱스4, owner=`stock_dashboard`·`relacl=NULL`·PUBLIC 롤 4권한 전부 false. 재적용 → `skipped`(ledger 1행 유지). down dry-run 5문장 미실행. 재기동 없음(PID 2936 유지, `/api/agi/status` HTTP 404 = 재기동 전 예상) — 코드 경로는 함수 직접 호출로 검증(`applied:true`, 공개 `count:0`). 적용에 따라 적용 전제를 단언하던 테스트 1건(IsolationTests)을 전후 동일성 검사로 재작성, AGI 3파일 **73 passed**. 실수집·publish 승격·외부 전송 0 |
| 2026-10-11 11:5x KST | **AGI-02 공개 노출 allowlist 전환(Planner 지적 — denylist → allowlist, fail-closed)** | `document_visibility_clauses()`/`source_visibility_clauses()` 가 `scope <> 'private_chat'` **denylist** 였던 결함을 수정. Planner 판정대로 `other`(공개 미증명 미분류) 소스가 `is_private=false` + `publish_summary='allowed'` 가 되면 공개될 수 있었고, 스키마에 새 scope 가 추가되면 조용히 공개로 승격됐다. 이제 노출은 **명시 allowlist** 다 — `agi.repository.PUBLIC_SCOPES = ('public_channel','blog','report')` 단일 출처, `public_scope_clause()` 가 목록·상세·소스(watermark) **3경로 전부**의 SQL 에 `scope IN (...)` 를 넣고, allowlist 밖 값과 NULL 은 자동 배제된다(`NULL IN (…)` 은 참이 아님). `NON_PUBLIC_SCOPES=('private_chat','other')` 는 이름만 붙인 비공개 집합이며, 회귀 테스트가 **스키마 CHECK 값 − allowlist == NON_PUBLIC_SCOPES** 를 강제해 새 scope 등장 시 공개 여부 결정을 요구한다. 응답 감사 보강: 공개 봉투에 `public_scopes`, 항목·watermark 에 `source_scope`/`scope`(응답만으로 경계 확인 가능). 회귀 테스트 11건 신설(공개 목록·상세·watermark 3경로 0건 / `scope`×`publish_summary`×`is_private` **12조합** 전부 0건 / 양성 대조 3종 공개 / SQL 문장에 allowlist 실림 + denylist 부재 + 관리자 경로엔 allowlist 없음 / CHECK 드리프트로 `newsletter` 를 넣어도 비공개 / CHECK-allowlist 차집합 가드 / NULL·미지값 SQL 의미론). 검증: AGI 3파일 **72 passed**(61 → +11), `public` 무변경(AGI 테이블 0·임시 스키마 0·BASE TABLE 553), OpenAPI **508 경로 불변**·AGI 파라미터에 `include_private` 부재. live migration/apply·실수집·재시작 없음 |
| 2026-10-11 11:0x KST | **AGI-02 Planner P0 BLOCK 수정(권한 경계)** | 첫 제출이 공개 `/api/agi/documents` 에 `include_private` **외부 쿼리 파라미터**를 열어 계약 §9-4("개인 대화·비공개 원문은 인증된 연구 화면")를 위반했다 — 지적대로 결함. 2층으로 수정: (a) 공개 경로에서 노출 파라미터 **완전 제거**, 노출 등급은 경로로만 결정하고 필터는 **SQL 단계**(`is_private=false AND publish_summary <> 'denied'`)로 강제 → 인증·터널 상태와 무관하게 성립 (b) 비공개·미승인 뷰를 **owner 전용** `/api/agi/admin/documents[/{id}]` 로 분리 — ①경로가 기존 게이트에서 `owner` 등급(`security_gate.OWNER_GET_PATTERN` 의 `/admin/`, 미들웨어 강제) ②핸들러가 `security_gate._owner_ok` 재확인 → 실패 시 401 `owner_required`. 응답 봉투에 `visibility`·`revision_hash_scope:"body_only"`·note 추가(첨부 바이트는 **AGI-04 확정**, AGI-04 행에 인수 명시). 회귀 테스트 6건 신설: 공개 목록/상세가 denied·private 차단, `include_private=true` 무시, 관리자 경로 무인증 401, `access_level` 분류 고정(admin=owner/public=public), 두 경로 상호 우회 불가, 권한을 올리면 공개 노출(필터가 '무조건 0건'이 아님). 검증: AGI 3파일 **61 passed**, OpenAPI **503 → 508**(AGI 5개 경로). live migration/apply·실수집·재시작 없음 |

부분 구현과 migration 적용 증거는 있으며, 실수집·외부 게시·발송·자동매매의 완료 증거는 없다. 향후 완료된 작업도 표에서 지우지 않고 실행/검증 기록을 추가한다.


## 7. 2026-10-13 독립 검토 후속

[AGI_REVIEW_20261013.md](AGI_REVIEW_20261013.md): 기존 AGI 테스트 243개·하위 12개 통과, 추가 합성 사례에서 아래 결함 재현. 운영 public 데이터는 변경하지 않았다.

| 검토 ID | 관련 작업 | 수정할 항목 | 상태 |
|---|---|---|---|
| R1 (P1) | AGI-02/03/15 | metadata_only 원문 저장 차단 및 공통 권한 강제 | done |
| R2 (P1) | AGI-02/03 | A→B→A 복귀 시 현재 revision·관측 이력 보존 | done |
| R3 (P1) | AGI-03/15 | 상세 응답의 공개→비공개 전환을 공개 조회에 반영 | done |
| R4 (P1) | AGI-03 | 마지막 페이지와 전체 목록 완전성 구분, 삭제 스윕 제한 | done |

**2026-10-13 21:36 (Code Doer) R1~R4 수정 완료** — `agi/repository.py`(공통 저장 계층 우회 방지 검사 + 현재 포인터 항상 갱신)·`agi/adapters/blog.py`(store_original 적용·상세 비공개 전이·`page_index`/`ingest_full_listing`)·`agi/adapters/naver_blog_listing.py`(`page_index` 전달)·`agi/service.py`(텔레그램 경로도 동일 권한 적용). 회귀 테스트 9개 추가(`tests/test_agi_blog_adapter.py::ExternalReview20261013Tests`), 재현 스크립트 재실행 결과 4건 모두 `-> fixed`. AGI 전체 7파일 252 passed·12 subtests(기존 243 유지 + 9 신규). 운영 public 데이터·실수집·재시작 없음.

**2026-10-13 2차 독립 검토(review_agi_fixes_20261013.py) — 경계 사례 3건 추가 발견, 전부 수정 완료**

| 검토 ID | 심각도 | 결함 | 상태 |
|---|---|---|---|
| P1a | P1 | 항목 상한(`entry_limit`)으로 잘린 중간 페이지가 `truncated=True` 만 보고 `ingest_full_listing` 의 "다음 페이지 있음"으로 승인됨 — 그 페이지 자신의 항목도 빠졌을 수 있는데 완전한 목록으로 오판 | done |
| P1b | P1 | `store_original='no'` 소스에 **body_text=None** 으로 `repo.store_revision()` 을 직접 불러도(어댑터의 `should_create_revision_for_policy` 를 거치지 않고) revision 행이 생성됨 — 공통 저장 계층 검사가 `body_text is not None` 일 때만 정책을 조회해서 우회 가능 | done |
| P2 | P2 | A→B→A 복귀가 현재 포인터는 되돌리지만(R2), 그 revision 이 **다시 관측됐다는 사실**(시각·run)이 어디에도 안 남음 | done |

수정: ① `agi/adapters/blog.py`·`agi/adapters/naver_blog_listing.py` — `ListingPage.page_continues`(hasNextPage 류 마커로 "다음 페이지 있음"을 선언한 경우만 True) 신설, `ingest_full_listing` 의 중간 페이지 검증에 `page_continues` 요구 추가(entry_limit 잘림은 False라 더 이상 승인 안 됨). ② `agi/repository.py` — `store_revision()` 이 `body_text` 값과 무관하게 **항상** 소스 정책을 조회하고, `store_original='no'` 면 어떤 호출이든 즉시 거부. ③ migration **0002**(`agi/migrations/m0002_revision_observation.py`, `research_document_revisions.last_observed_at`/`last_observed_run_id`, 컬럼 2개만 추가·데이터 문장 없음, 기본 dry-run·m0001 과 동일한 checksum 드리프트 거부) + `ResearchRepository._mark_revision_observed()`(컬럼이 없는 스키마에서는 포인터 갱신만 하고 건너뜀 — fail-soft). `scripts/migrate_agi_research_core.py` 의 `--direction down` 을 적용 역순으로 수정(이후 migration 이 먼저 내려가야 한다).

검증: 회귀 테스트 3개 추가, AGI 전체 7파일 **255 passed·12 subtests**(252+3). 재현 스크립트(`scripts/review/reproduce_agi_review_20261013.py`)에 추가 재현 3건을 더해 재실행 — 전체 7건 모두 `-> fixed`. `public` 에는 **읽기**(dry-run 계획 확인)만 했고 migration 0002 는 아직 적용(`--apply`)하지 않았다 — m0001 과 같은 사람 승인 단위로 별도 승인 후 적용.

**2026-10-13 21:5x (사용자 승인 "승인해, 이어서 미결된거 계속해") migration 0002 적용 + AGI-02 텔레그램 실수집 1차 완료**

① `scripts/migrate_agi_research_core.py --apply` 로 `public` 에 0002 적용(`research_document_revisions.last_observed_at`/`last_observed_run_id` 컬럼 2개만 추가, 기존 행 0개 영향 — 적용 전 4테이블 전부 0행 확인됨). ② `ResearchIngestionService.seed_sources_from_telegram()` 으로 `telegram_channels` 활성 14개 전부 등록(개인 대화·미분류 제외 0건 — D02 초안의 실측과 일치). ③ 14개 소스 전부 `backfill()` 완전 수집: **listed=stored=11,546, 중복 0, run 29개 전부 success**. `telegram_channel_posts` 실제 행수(11,656)와의 차 110건은 `telegram_channels` 에 등록되지 않은 orphan 채널(`stoner8740`, 수집 대상 아님)이라 정상. `store_original` 기본값 `metadata_only` 를 그대로 둬 **본문은 0건 저장**(D07 미결 — 보관 기간·본문 보관 범위는 별도 결정 전까지 올리지 않는다), `access_state` 전부 `ok`. 레거시 테이블은 읽기만 했다(행수 불변 확인). 공개 조회(`/api/agi/documents`)는 모든 소스 `publish_summary='denied'` 기본값이라 여전히 0건(의도된 fail-closed). 운영 FastAPI 프로세스는 재시작하지 않음 — DB 상태와 무관하며, `/api/agi/status` 의 migrations 목록만 다음 재시작 전까지 0001만 보인다(기능 영향 없음).

**아직 넘지 않은 미결**: 네이버 블로그 등 외부 네트워크 수집은 ① `/PostList.naver` robots 차단 해제의 명시 승인(`CollectionApproval`, `approved_by`/`approval_ref` 증거 필드 필요) ② 수집 대상 블로그 주소 — 둘 다 이 대화에서 구체적으로 주어지지 않아 보류했다(사용자에게 재확인 요청). 텔레그램 D02 는 실측대로 적용을 완료했으나, 표의 공식 상태는 "전체 과거 백필 1차 완료"로 갱신하고 D02 자체는 열어 둔다(블로그 쪽 범위가 아직 미정).

**2026-10-13 22:1x (사용자 지시 "특정 블로그글만 수집할 수 있도록 구성, 텔레그램도 동일") 선택 수집 기능 신설 — ① 블로그 측 robots 차단 우회 자체를 소거**

전체 목록(PostList.naver) 탐색 대신 **호출자가 이미 알고 있는 글만** 가져오는 경로를 추가했다.
- `BlogAdapter.ingest_known_posts(conn, source_id, entries, fetch)` — 네이버 **상세 페이지**(`/{blog_id}/{log_no}`)는 robots **허용** 경로다(이 패키지가 이미 문서화해 둔 사실). `PostList.naver`(차단 경로)를 전혀 거치지 않으므로 **`CollectionApproval` 승인이 필요 없다** — 블로그 쪽 첫 번째 미결(①)이 이 모드에서는 해소된다. 둘째 미결(② 수집 대상 블로그 주소/글 주소)은 여전히 필요하다 — 받으면 즉시 실행 가능.
- `ResearchIngestionService.ingest_specific_messages(conn, source_id, message_ids)` — `telegram_channel_posts`에서 지정한 `message_id`만 읽어 저장한다(전체 채널 백필 아님). 순차 커서를 쓰지 않으므로 `page_cursor`/이후 `backfill` 재개 지점을 건드리지 않는다.
- 둘 다 완전성(=전체 목록 확보) 주장을 **절대** 하지 않는다 — 내부적으로 `coverage='denominator_unknown'` 강제, 스윕(없는 글=삭제) 없음. 손으로 고른 일부를 전체로 오인해 나머지를 삭제하는 사고를 구조적으로 막는다.
- 검증: 회귀 테스트 4개 추가(`tests/test_agi_blog_adapter.py::SelectiveIngestionTests` 2개, `tests/test_agi_research_ingestion_contracts.py` 2개 — 후자는 실제 텔레그램 레거시 데이터로 "건너뛴 message_id는 저장 안 됨"을 확인). AGI 전체 7파일 **259 passed·12 subtests**(255+4).
- 블로그 쪽은 아직 **어떤 네트워크 요청도 보내지 않았다** — 실제 수집 대상(블로그 주소/글 URL)을 받아야 실행할 수 있다.

다음 담당·산출물·차단 조건·재검증 명령은 검토서 §5. 최신 계획의 P0-SREIG 우선순위·격리 후보 규칙은 유지한다.

**2026-10-13 22:3x (사용자 지시 "글 수집은 천천히, 나머지 미결 작업 진행") — D03 확정, AGI-04~17 잔여는 전부 D-결정 대기임을 확인**

AGI-04~17을 점검한 결과 전부 D03~D08 중 하나 이상에 의존해 데이터 없이 코드만 진행할 수 있는 항목이 없었다. 사용자가 D03 해결을 선택 — 위 D03 행대로 실제 보유 10종목 + OSAT 5종목(임시, 신규 선정이라 재확인 필요)을 AGI-06/07 시범 집합으로 기록했다. **AGI-06/07 본체 구현(지속 갱신 기업 보고서·실적/가격 4분면)은 아직 시작하지 않았다** — 이 결정 하나로는 두 항목 모두 "planned" 상태를 유지하며, 실제 구현은 별도의 더 큰 작업이라 범위·우선순위를 다시 확인한 뒤 착수한다. D04~D08은 여전히 open.

**2026-10-13 23:0x (사용자 지시 7건 — 관리화면·비교군 추가/제거·보고서 구성 연구·애널리스트 리포트 참고·지속 갱신·사업포트폴리오/공시 연동·비교군 확장) 처리**

① 관리 화면은 아직 없다(블로그/텔레그램 소스도, 비교군도 — 전부 스크립트/DB 편집). ② 비교군 추가·제거는 가능하다 — `scripts/rebuild_sector_taxonomy.py`의 `curated_osat`(또는 신규 `curated_*`) dict 하나만 고치고 재실행하면 되고, **지금 실제로 LB세미콘(061970)·두산테스나(131970) 2종목을 추가해 OSAT 비교군을 5→7종목으로 확장**했다(두산테스나는 WaferTest 92%+PKGTest 6%로 verified, LB세미콘은 공정 용어가 직접 없어 inferred). ③~⑥ `reports/` 실제 리포트(메리츠 하나마이크론 3Q25 Review, OSAT 산업 노트) 2건을 읽고 구성·갱신 설계([AGI_06_COMPANY_REPORT_DESIGN_20261013.md](AGI_06_COMPANY_REPORT_DESIGN_20261013.md)) 작성 — 보고서 섹션을 기존 운영 테이블에 매핑하고, 실적 발표·공시·애널리스트 추정·뉴스·사업 포트폴리오·비교군 재구성 6종 갱신 트리거와 AGI-01 revision 개념 재사용(섹션별 버전) 을 제안했다. ⑦은 ②에서 실행(OSAT 확장)으로 선례를 만들었다 — 같은 방식으로 다른 산업도 반복 가능. **실제 보고서 저장·조립 파이프라인 구현은 다음 단계**(설계 문서 §7 참고).
