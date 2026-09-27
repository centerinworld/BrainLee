# 주식 대시보드 — Codex 필수 참조 문서

## 필수 행동 규칙

- 작업 시작 시 이 문서를 먼저 확인한다.
- 새 파일, API, DB 스키마, 프론트 컴포넌트/패널, 스케줄러, 버그 수정, 설정/환경변수, 기존 동작 변경이 있으면 이 문서를 업데이트한다.
- DB/API/컴포넌트 위치가 이 문서에 있으면 불필요한 전체 파일 탐색을 줄인다.

## 프로젝트 위치

- stock_dashboard 백엔드/DB: `/Applications/stock_dashboard`
- CEO 브리핑 프론트/프록시: `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform`
- 주요 글로벌 인텔리전스 백엔드: `/Applications/stock_dashboard/routes/global_macro.py`
- CEO 브리핑 글로벌 인텔리전스 화면: `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/frontend/kai.js`
- CEO 브리핑 글로벌 인텔리전스 프록시: `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/backend/main.py`

## 글로벌 인텔리전스 API 추가 현황

`/api/global-macro/*`:

- `GET /insights`: 변동률, z-score 이상치, 조합 신호, 이벤트 반응, 상관관계, 시장 국면, 리드-래그 요약
- `GET /insights/regime`: 매크로 기반 시장 국면/위험 점수
- `GET /insights/lead-lag`: 주요 지표와 `KR_KOSPI` 등 목표 지수의 리드-래그 상관관계
- `GET /commodities`: 원자재/환율/원유재고/FAO 식품가격지수 최신값
- `GET /commodities/correlations`: 원자재-국내 섹터 상관관계
- `GET /events/reactions`: 경제 이벤트 발표 후 주요 지수/팩터 반응
- `POST /collect?source=reb_housing`: 한국부동산원 R-ONE 공개 통계에서 한국 주택매매가격지수 수집
- `POST /collect?source=global_financial`: FRED 기반 글로벌 금융여건 확장 수집(ECB/BOJ 금리, 하이일드·Baa 스프레드, NFCI, 기대인플레, 30Y/3M 금리)
- `POST /collect?source=dram_spot`: TrendForce/DRAMeXchange 공개 DRAM Spot Price 표에서 실제 D램 현물가(Session Average)를 수집. `MQ_DRAM_PROXY` 수출단가 proxy와 별개.
- `POST /collect?source=market_quant`: `quant_major_indicator_catalog/series`에 이미 있는 주요 퀀트 지표를 글로벌 인텔리전스 `MARKET_QUANT`로 브릿지. 중복 외부 수집 금지, 없는 지표만 신규 수집 후보로 추가.

## CEO 브리핑 일정 API 현황

`ceo-briefing-platform/backend/main.py`:

- `GET /calendar/{page_id}`: 일정 목록 조회
- `POST /calendar/{page_id}/events`: 일정 추가
- `PUT /calendar/{page_id}/events/{event_id}`: 일정 수정
- `DELETE /calendar/{page_id}/events/{event_id}`: 일정 삭제

일정 DB는 `/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/data/ceo_briefing.db`의 `calendar_events` 테이블을 사용한다. 관리자 화면 런타임은 `frontend/admin-console-runtime-v2.js`이며, `frontend/index.html`이 이 파일을 로드한다.

## CEO 브리핑 AI 설정 현황

`ceo-briefing-platform/backend/main.py`:

- `GET /openai-settings`: 현재 AI 공급자, 모델, 저장 키 여부 조회
- `PUT /openai-settings`: AI 공급자(`openai`/`gemini`), 요약 모델, 분류 모델, API 키 저장

AI 호출은 `ceo-briefing-platform/backend/services/rss_ingest.py`의 `chat_completion_content()`를 통한다. `ai_provider=gemini`이면 Google Gemini OpenAI 호환 Chat Completions 엔드포인트를 사용하고, 키는 `app_settings.gemini_api_key`에 저장한다. 기존 OpenAI 키는 `app_settings.openai_api_key`에 유지한다.

## 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-09-03 | 트릴리온 소스 인텔리전스 PostgreSQL 미러 복구: `source_intelligence_sources`·`source_intelligence_mentions` 테이블 및 인덱스를 PostgreSQL 방식으로 생성하고 SSD 최신 스냅샷 692건을 업서트. 의견 API가 저장된 `excerpt`를 읽어 실제 언급 요지를 우선 표기하도록 보강. |
| 2026-09-01 | 트릴리온 소스 인텔리전스 주기를 주 1회에서 매일 20:00으로 변경. 전수 분석 스냅샷은 PostgreSQL 미러 오류와 독립적으로 외장 SSD `research_outputs/trillion_us_biotech/latest.json`에 먼저 저장하도록 수집기 순서를 보강했다. |
| 2026-08-30 | 13F 화면 성능 수정: `routes/us_13f.py`는 화면 요청에서 SEC를 동기 수집하지 않고 외장 SSD `data/us_13f_cache.json` 저장 스냅샷을 즉시 반환한다. 수동 갱신은 백그라운드, 스케줄러 `미국13F거물공시`(매일 07:12)는 실제 SEC 재계산을 수행한다. App.jsx에 저장본/갱신중 상태 표시 추가. |
| 2026-08-30 | 스톡 대시보드 미국 바이오 탭에 소스 의견 요약 표를 추가: 원문 대신 티커별 `강력 긍정/긍정/관찰/혼재/부정/강력 부정` 분류, 출처·기준일·요약을 표시한다. `MERO`를 바이오 티커 목록에 추가하고, `source_intelligence_mentions`/`source_intelligence_sources` 범용 스키마 및 `/api/source-intelligence/opinions` 요약 API를 추가했다. `scheduler.py`는 매주 일요일 10:00 트릴리온을 전수 재검토한다. |
| 2026-08-30 | `scripts/ops/analyze_trillion_us_biotech.py` 추가: 트릴리온 채널 전체 이력을 티커 우선으로 스캔해 미국 바이오 종목을 탐지. SEC 법인명 기반 후보 보강, `$TICKER`·`#TICKER`·거래소 표기는 별도 우선 검토 큐에 보존, 일반 문맥 후보도 삭제하지 않고 미해결 목록으로 저장. 운영자 명시 의견과 기관 보유·유입 관찰, FDA/임상 촉매, 위험 의견을 분리한다. 결과는 외장 런타임 `research_outputs/trillion_us_biotech/latest.json`에 저장. |
| 2026-08-29 | 저장소 위치 전환: `/Applications/stock_dashboard`를 `/Volumes/Realtek_NVME/stock_dashboard/runtime` 심볼릭 링크로 전환. 대용량 `logs`·`reports`, `stock.db`는 외장 NVMe에 유지하며 메인 드라이브 원본을 삭제해 이후 프로젝트 데이터가 외장 저장소에 기록되도록 구성. 8000 API와 5173 프론트 정상 응답 확인. |
| 2026-08-29 | CEO 브리핑 AI 공급자 설정 추가: `ai_provider`, `gemini_api_key`를 도입해 기존 OpenAI 키와 Gemini 키를 분리 저장. RSS 기사 분류·요약·중복판정·텔레그램 Top-N 선별 호출을 공통 `chat_completion_content()`로 통합하고 Gemini 선택 시 Google OpenAI 호환 엔드포인트를 사용하도록 수정. 관리자 콘솔 page6를 `AI 설정`으로 변경하고 Provider 선택 및 Gemini 키 입력 UI 추가. 검증: backend `py_compile`, frontend `node --check` OK. |
| 2026-07-17 | StockAnalysis 헤더 수급/프로그램/대차잔고 UI 정렬 및 숫자 포맷 수정: `/Applications/stock_dashboard/frontend/src/App.jsx`에서 우측 패널을 카드형 grid로 재구성해 위치 어긋남 해결, `fmtPanelDate()` 추가로 수급 기준일·프로그램 기준일·대차 기준일 포맷을 `YYYY-MM-DD`로 통일. 프로그램 순매수 금액 signed formatter에 `만원` 단위를 추가해 `-2,000,000` 같은 raw 원화 노출을 `-200만원` 형식으로 보정. 검증: frontend `npm run build` OK. |
| 2026-07-17 | 글로벌 인텔리전스 시장형 퀀트 지표 2차 확장: 기존 `quant_major_indicator_series`에 있던 KOSPI/KOSDAQ 시장폭(상승종목비율·중앙수익률), 52주 신고가-신저가 스프레드, 거래량 3배 종목수, 총거래대금, 투자자 예탁금, 신용공여 잔고, 평균 신용잔고비율, 외국인/기관/개인 순매수 총량, 공매도/대차잔고, KOSPI/KOSDAQ 프로그램 순매수, 종목 프로그램 상위10 집중도 등 22개 지표를 `MARKET_QUANT`에 추가 브릿지. `MARKET_QUANT`는 49개 지표/31,338건, 전체 글로벌 매크로는 152개 지표/54,021건. 8000/8011 stats 정상 확인. |
| 2026-07-17 | D램 가격을 proxy가 아닌 실제 현물가로 수집하도록 보강: `collectors/dram_spot_collector.py` 신규 추가. TrendForce/DRAMeXchange 공개 DRAM Spot Price 표에서 `DDR5 16Gb 4800/5600`, `DDR5 16Gb eTT`, `DDR4 16Gb 3200`, `DDR4 8Gb 3200`, `DDR4 8Gb eTT`, `DDR3 4Gb 1600/1866` Session Average 6개를 수집해 `MARKET_QUANT/DRAM_SPOT` 및 `quant_major_indicator_series`에 actual spot으로 저장. `/api/global-macro/collect?source=dram_spot` 및 `all` 연결. 2026-07-17 기준 `DDR4 8Gb 3200` 현물가 40.50달러 적재, 전체 글로벌 매크로 130개 지표/27,920 데이터포인트 확인. |
| 2026-07-17 | 글로벌 인텔리전스에 주식시장 주요 퀀트 지표 연결: `collectors/market_quant_bridge_collector.py` 신규 추가, 기존 `quant_major_indicator_series`에서 D램 가격 대리지표(`MQ_DRAM_PROXY`), 메모리/시스템 반도체 수출액·단가, 반도체 장비/특수가스/PCB, 이차전지, 조선, 전력기기, 항공/방산, BDI/BCI/BPI/BSI, 철광석, 열연강판 proxy, 유연탄, SMP, 미국 리그 수 등 21개 지표 5,231건을 `MARKET_QUANT` 카테고리로 브릿지. `/api/global-macro/collect?source=market_quant` 및 `all`에 연결. 전체 글로벌 매크로 124개 지표/27,914 데이터포인트, 8000/8011 stats 정상 확인. |
| 2026-07-17 | 글로벌 인텔리전스 데이터 확장 계속 진행: `collectors/global_financial_conditions_collector.py` 신규 추가, `/api/global-macro/collect`의 `global_financial` 및 `all` source에 연결. FRED 공식 API로 `EU_ECB_RATE`, `JP_BOJ_RATE`, `US_HY_SPREAD`, `US_BAA_SPREAD`, `US_NFCI`, `US_10Y_BREAKEVEN`, `US_30Y_YIELD`, `US_3M_YIELD` 5,065건 적재. `collectors/world_bank_collector.py`에 세계 수출/수입 물량 증가율과 합성 `GLOBAL_TRADE_VOL` 계산 추가, World Bank 255건 재수집. 전체 글로벌 매크로 103개 지표/22,683 데이터포인트, CEO 프록시 8011 stats 정상 확인. 남은 빈 코드: `CN_EXPORT`, `CN_PMI_MFG`, `EU_PMI_MFG`, `US_ISM_MFG`. |
| 2026-07-11 | CEO 브리핑 일정 기능 전체 점검 및 수정: `backend/db_access.py`에 `calendar_events` 런타임 생성 DDL 보강, 일정 수정/삭제 함수 추가, 일정 ID 충돌 방지. `backend/main.py`에 `PUT/DELETE /calendar/{page_id}/events/{event_id}` 추가. `backend/services/google_calendar.py`에 Google Calendar update/delete 추가. `frontend/admin-console-runtime-v2.js` 일정 행 Edit/Delete 버튼, 수정 폼, POST/PUT 분기 저장 추가, `frontend/index.html` JS 캐시버스터 갱신. 검증: DB CRUD 및 8011 HTTP 생성→수정→조회→삭제 OK, Terminal에서 `keepalive_backend.sh` 실행 후 8011/8012 리슨 및 일정 CRUD 재검증 OK, 프론트/백엔드 문법 OK. |
| 2026-07-16 | 글로벌 인텔리전스 데이터 수집 계속 진행: KOSIS 키 invalid로 막힌 한국 주택매매가격지수를 한국부동산원 R-ONE 공개 통계(`A_2024_00045`) 기반 `collectors/reb_housing_collector.py` 신규 수집기로 보강, `/api/global-macro/collect`의 `all` 및 `reb_housing` source에 연결. Week3 FOMC 일정은 이미 이벤트 테이블에 수집되어 있었으나 진행률이 고정 `False`라 `fomc_ready` 계산으로 수정. World Bank/FRED/ECOS/REB/Yahoo/OECD/IMF/Events/Reactions/FAO/EIA 재수집 완료, Week2~Week6 모두 done 확인. |
| 2026-07-11 | 글로벌 인텔리전스 인사이트 확장: `/api/global-macro/insights/regime` 및 `/insights/lead-lag` 신규, `/insights`와 `/dashboard.__signals`에 시장 국면·리드-래그 포함. `ceo-briefing-platform/backend/main.py` 프록시 추가, `frontend/kai.js` 자동 인사이트 패널에 국면/리드래그 카드 추가. 검증: stock_dashboard TestClient 4개 API 200, 프론트/백엔드 문법 OK. |
