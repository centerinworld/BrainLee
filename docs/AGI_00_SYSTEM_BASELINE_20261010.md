# AGI-00 — 라이브 시스템·품질·재사용 베이스라인

- 조사 시각: **2026-10-10 23:34:33 KST**
- 범위: `Open_Items_AGI.md` AGI-00. **읽기 전용** 조사만 수행했다. 코드·스키마·수집 설정·DB 데이터·서비스 상태는 변경하지 않았다.
- 목적: AGI-01(소스/시간/권한 계약) 및 AGI-09(owner 원칙 원장)이 기존 런타임과 중복되거나 현재 품질 결함을 우회하지 않도록, 실제 실행 경계와 재사용 가능 자산·차단 조건을 고정한다.

## 1. 실제 운영 경계

| 항목 | 실측 결과 | 판정 |
|---|---|---|
| API 프로세스 | PID `2936`, `uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1` | 라이브 API |
| CWD / 리스너 | `/Volumes/Realtek_NVME/stock_dashboard/runtime`, `127.0.0.1:8000` | 이 트리만 운영 판정 기준 |
| Primary DB | PostgreSQL `stock_dashboard`, PostgreSQL 16.15 | PostgreSQL만 primary; `connect_primary_db(readonly=True)`으로 조사 |
| API 상태 | `/api/dashboard/stats` = `db_path: postgresql`, 응답 정상 | 단, `price_records: 0`은 API 응답 의미를 AGI 기능의 원시 가격 행수로 사용하면 안 됨 |
| 작업 트리 | 다수의 기존 미커밋 파일과 실행 중인 전체 pytest 프로세스가 존재 | AGI 작업은 신규 파일/경로 한정; 기존 변경 덮어쓰기·일괄 stage/commit 금지 |

## 2. 재사용 가능 자산 — 확인된 코드·DB·API

| 영역 | 실제 자산 | 실측 규모/경로 | AGI 재사용 판정 |
|---|---|---|---|
| 텔레그램 원문 | `telegram_channel_posts`, `telegram_channels`, `telegram_collector.py`, `routes/telegram.py` | 15 채널, 11,425 posts; 2024-11-09~2026-10-09 | AGI-02의 **시드**로만 재사용. 현재 upsert는 원문 revision/tombstone/완전성 분모 계약을 제공하지 않으므로 그대로 전체 보관 완료로 간주 금지 |
| 보고서 | `report_files`, `telegram_collector.py` | 29,110 rows | 파일·메시지 키 재사용 가능. `report_date` 최대 `2030-09-01`이라 날짜 품질/의미를 AGI-01에서 사전 검증해야 함 |
| 기업 프로필 | `company_analysis_profiles`, `company_peer_relations`, `routes/company_intelligence.py` | 434 profiles, 2,110 peer relations | 기존 조회·피어 표현을 재사용. GET 경로가 캐시 및 profile persistence를 동반할 수 있다는 코드 주석이 있어, AGI의 신규 조회 API는 무부작용 조회/생성 job을 분리해야 함 |
| 거시 | `global_macro_data`, `quant_major_indicator_series`, `routes/global_macro.py`, 기존 collector | 73,516 rows / 181 codes; 220,818 rows / 299 keys | AGI-10의 소스 후보. 양 테이블 `MAX(date/period)=2031-12-31`은 예상 경로/상수인지 실제 관측치인지 미판정이므로, AGI 신호/백테스트 입력은 `released_at`·vintage·현재시점 상한 검증 전 사용 금지 |
| 가설·실험 | `hypothesis_research_registry`, `hypothesis_research_runs`, strategy-research API | 각 1 row; `/api/backtest/strategy-research/{rebuild,summary}` | 재사용 조사 대상은 있으나 AGI-09/13의 정책·가설·승인 버전 원장으로 충분하다고 확인되지 않음 |
| Stock LLM | `routes/llm_proxy.py` → `127.0.0.1:8888` | 관리자 게이트가 있는 역방향 프록시 | 접근 프록시일 뿐 원문 권한/인덱스/근거 계약의 증거가 아님. AGI-04에서 Brian_RAG 중복 저장 여부를 독립 조사 |

### 현재 공개 API 표면 (AGI 관련)

확인된 범주는 텔레그램(`/api/telegram/posts`, mentions, RAG), 기업 정보(`/api/company-intelligence/*`), 거시(`/api/global-macro/*`), 퀀트 지표(`/api/quant-major-indicators/*`), 기존 리서치(`/api/research/*`)다. **AGI 제안 API**(`/api/research/documents`, attention, report versions, theses, opportunities, blog drafts, allocations)는 아직 존재하지 않는다. AGI-01에서 기존 `/api/research/*`의 스키마·부작용·소유 테이블을 대조하기 전 prefix 재사용을 확정하지 않는다.

## 3. 수집/운영과 품질 차단 조건

| 발견 | 근거 | AGI 영향 / 차단 규칙 |
|---|---|---|
| 과거 텔레그램 관심도는 비활성 | `telegram_monitor.py:35` `TELEGRAM_MONITOR_DISABLED=True`; OPEN_ITEMS A8 | 옛 monitor 재가동 금지. AGI-05는 AGI-04 원문/revision/coverage 계약 이후 새 실험으로만 시작 |
| 글로벌 거시·퀀트 브릿지 운영 중 | scheduler `_loop_global_macro_daily` 756, `_loop_quant_major_indicators_daily` 6664 | 새 AGI collector를 먼저 만들지 말고 source owner·upsert key·발표시각·vintage를 AGI-01에 명시 |
| 기존 blog / source / RAG 작업 존재 | scheduler `_loop_sector_blog` 2414, `_loop_source_intelligence_weekly` 3831, `_loop_investment_decision_rag` 6042 | 기존 잡을 재시작/확장하지 않음. AGI-00 후 별도 계약에서 입력·출력·권한·중복 실행을 대조 |
| 재무/가격 품질 게이트 미종결 | `OPEN_ITEMS` C3-2/C3-4, C11; FINANCIAL/PLAN은 OFS 후보 작업 BLOCKED | AGI 기업 보고서·AGI-07/13/14는 quality/pit 상태를 표시하고, 미확정 숫자로 전략·비중·주문 승격 금지 |
| 라이브 상태에도 실패/경고가 존재 | 10-10 API 응답: 데이터계약점검 failed, 재무무결성일일 failed, FnGuideDART전종목검증 failed, 미국 factor collection warning, listed_company_info missing | AGI-15가 원장/실패 전파를 새로 숨기면 안 됨. AGI-01부터 기존 원장과 새 잡의 실패 상태를 합성하지 말고 원천별로 보존 |

## 4. AGI-01/09으로 넘기는 확정 입력과 비결정 사항

### AGI-01 계약 설계에서 반드시 봉인할 항목

1. 기존 테이블을 재사용할 때와 신설할 때의 경계: `telegram_channel_posts`/`report_files`는 레거시 원문 참조용, AGI `research_documents` 계열은 revision·tombstone·권한·cursor·수집 분모를 보장해야 한다.
2. 시간 4종: 대상기간, 원천 발표시각, 시스템 최초 인지시각, 저장시각. 불명 발표시각은 수집시각을 보수적 하한으로 쓴다.
3. 모든 AGI 전략·화면 소비 전 `quality`, source watermark, snapshot generation time, PIT eligibility를 평가하며, 누락/만료/평가오류는 거래·알림·백테스트에 fail-closed한다.
4. `global_macro_data`와 `quant_major_indicator_series`의 2031 날짜는 source-specific 조사·샘플 대조 전에는 미래 데이터로 취급하고 신호 계산에서 차단한다.
5. 개인 대화·비공개 보고서·외부 모델 전송·게시 권한은 document/source 단위로 분리한다. D02/D07/D08 전에는 범위를 추정하지 않는다.

### AGI-09 설계의 고정 전제

- D01만 결정됨: 회복 선행(A)과 미발견 성장(B)을 별도 연구 가설로 관리한다.
- 원문 → 해석 → 측정지표 → 반증 → 실험 ID → 결과 → 채택/기각 → 적용 버전을 불변 이력으로 둔다.
- 전략/비중/주문 승격은 `SIGNAL_RULES.md`, C11 PIT 해결 상태, AGI-13/14/15 게이트의 후속 조건이다. 이번 단계에서 매수/매도·목표 비중을 만들지 않는다.

## 5. 다음 단계와 보류

- **다음 owner:** Code Doer는 이 베이스라인만 입력으로 **AGI-01 및 AGI-09의 문서 계약 초안**을 작성할 수 있다. 코드·DDL·수집·서비스 재시작·외부 호출·DB write는 허용하지 않는다.
- **Planner 검증:** 계약에는 기존 테이블/신설 논리 테이블 대조표, primary DB 경로, idempotency key, retention/권한, PIT/quality 차단, rollback 없는 DDL 금지, 테스트 수용 기준을 포함해야 한다.
- **명시 보류:** OFS 본문 후보 적용은 PLAN §7의 10-10 23:30 BLOCKED 상태를 유지한다. 이 문서는 그 작업의 승인·재개 근거가 아니다.
