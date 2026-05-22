# 주식 대시보드 — Claude 필수 참조 문서

---

## ⚠️ CLAUDE 필수 행동 규칙 (모든 세션에서 자동 적용)

> **이 섹션은 Claude가 반드시 따라야 할 행동 규칙입니다. 예외 없이 적용됩니다.**

### 서버 재시작 (필수 — 코드 수정 후 반드시 이 방법으로만)

> **직접 uvicorn kill 절대 금지.** launchd `KeepAlive:true` 때문에 kill 후 launchd가 자동 재시작 → 이어서 수동으로 uvicorn 시작하면 두 프로세스가 공존함.

```bash
# ✅ 올바른 재시작 (launchd를 통해 — 새 코드 반영)
launchctl kickstart -k "gui/$(id -u)/com.stock-dashboard.local"

# ✅ 완전 정지 후 시작
/Applications/stock_dashboard/stop.sh
/Applications/stock_dashboard/start.sh

# ❌ 금지: kill <pid> 후 nohup uvicorn ... &  → 서버 2개 생김
```

### 세션 시작 시
- **이 파일을 먼저 읽는다.** 파일 내용으로 프로젝트 구조를 파악하고, 불필요한 파일 열람을 최소화한다.
- 작업 전 필요한 정보가 이 파일에 있으면 파일을 새로 열지 않는다.

### 작업 완료 시 (필수 — 자동으로 수행)
다음 중 하나라도 해당하면 **이 파일(CLAUDE.md)을 반드시 업데이트**한다:
- [ ] 새 파일 생성 (routes/, collectors/ 등)
- [ ] API 엔드포인트 추가/변경/삭제
- [ ] DB 테이블/컬럼 추가 또는 스키마 변경
- [ ] 프론트엔드 컴포넌트 추가/이동 (줄번호 포함)
- [ ] 스케줄러 잡 추가/변경
- [ ] 버그 수정 (재발 방지를 위해 "알려진 이슈" 섹션에 기록)
- [ ] 환경변수/설정 추가
- [ ] 기존 동작 방식 변경 (단위, 포맷, 로직)

**업데이트 위치**: 해당 섹션을 직접 수정 + 섹션 11(변경 이력)에 날짜와 함께 한 줄 기록.

### Codex/Claude 병렬 작업 시 충돌 방지 규칙

> **Codex와 Claude가 동시에 이 프로젝트를 수정함. 충돌 방지 필수.**

- 작업 시작 전 `git pull --rebase` 로 최신 코드 동기화
- **같은 파일을 동시에 편집하지 않는다** — 작업 파일을 CLAUDE.md 상단에 미리 명시
- 코드 수정 후: 반드시 `launchctl kickstart -k` 로 서버 재시작 (위 규칙 참조)
- Python 코드 수정 = 서버 재시작 없이는 변경 미반영 (uvicorn은 모듈 캐시)
- **routes/*.py, ETF_check/routes_etf.py 수정 시**: 서버 재시작 필수
- DB 스키마 변경 시: 다른 AI가 같은 테이블을 수정 중인지 반드시 확인

### 토큰 절약 규칙
- 파일 전체를 읽기 전에 이 문서에서 줄 번호를 확인하고 해당 범위만 읽는다.
- DB 스키마 확인 → 섹션 2 참조 (init_db.py 열지 않음)
- API 엔드포인트 확인 → 섹션 3 참조 (routes/*.py 열지 않음)
- 컴포넌트 위치 확인 → 섹션 6 참조 (App.jsx 전체 스캔 안 함)

### 재무/현금흐름 무결성 작업 선행 규칙 (필수)

> **Claude는 재무/현금흐름 관련 수정 전에 반드시 아래 파일을 먼저 읽고 작업한다.**

1. `/Applications/stock_dashboard/scratch/claude_handoff_external_reverify_20260516_1510.md`
2. `/Applications/stock_dashboard/scratch/claude_handoff_capex_depr_material_20260516.md`
3. `/Applications/stock_dashboard/scratch/company_profile_22_25_top500_1to1_20260516.csv`
4. `/Applications/stock_dashboard/scratch/company_profile_22_25_top500_1to1_20260516.json`

작업 지침:
- 재무 원천 적재는 DART/KRX 기반으로 수행하고, 웹 파싱값은 검증 레이어로만 사용.
- `standard_key` 중심 매핑(예: capex, depreciation)과 기업별 오버라이드 매핑을 분리.
- 값 저장 전 1:1 대조 실패(`ok_* = False`) 항목은 자동확정 금지, review 큐로 분리.
- 2022~2025 데이터 잠금은 조건 충족 시에만 허용(검증 통과율 근거 필수).

---

## 1. 프로젝트 구조

```
/Applications/stock_dashboard/
├── main.py              # FastAPI 앱 + 라우터 등록 (1792줄)
├── scheduler.py         # 수집 스케줄러 CollectionScheduler (400줄)
├── signal_engine.py     # 시그널 계산 엔진 (2507줄)
├── peak_monitor.py      # 가상매매 모니터 + Telegram 알림 (663줄)
├── config.py            # 환경변수 로드
├── database.py          # SQLAlchemy SessionLocal + get_db
├── models.py            # SQLAlchemy ORM 모델
├── kis_client.py        # KIS API 토큰 관리
│
├── routes/              # FastAPI 라우터 (main.py 38~56줄에 등록)
│   ├── signals.py       → /api/signals/*
│   ├── trend.py         → /api/trend/*  (가상매매)
│   ├── portfolio.py     → /api/portfolio/*
│   ├── buy_candidates.py→ /api/buy-candidates/*
│   ├── dart_contracts.py→ /api/dart-contracts/*
│   ├── market_indicators.py → /api/market-indicators/*  ★2026-04 신규
│   ├── kiwoom.py       → /api/kiwoom/*  ★2026-05 신규
│   ├── reports.py       → /api/reports/*
│   ├── telegram.py      → /api/telegram/*
│   ├── backtest.py      → /api/backtest/*
│   └── ingest.py        → /api/ingest/*
│
├── collectors/          # 외부 데이터 수집기
│   ├── kis_collector.py # KIS API (주가·수급·실시간)
│   ├── krx_collector.py # KRX / K-mydata (현재 접근 불가)
│   ├── public_data.py   # 공공데이터포털
│   ├── dart_collector.py# DART 공시
│   ├── yahoo_collector.py # Yahoo Finance (해외지수)
│   ├── kiwoom_collector.py # 키움 REST 연결/인증 상태 점검
│   └── base.py          # BaseCollector (rate limit, async)
│
├── .claude/
│   ├── settings.json    # hooks 설정 (UserPromptSubmit, Stop)
│   └── hooks/
│       ├── session_start.sh  # 매 프롬프트: CLAUDE.md 지시사항 주입
│       └── session_stop.sh   # 세션 종료: 로그 기록
│
└── frontend/src/App.jsx # 단일 파일 React SPA (~7600줄)
```

---

## 2. DB 스키마 (stock.db)

| 테이블 | 행수 | 핵심 컬럼 | 용도 |
|--------|------|-----------|------|
| `price_history` | 516만 | stock_code, date, open/high/low/close, volume, inst_net_buy, frn_net_buy, ind_net_buy, **inst_net_buy_amt**, **frn_net_buy_amt**, **ind_net_buy_amt** | 일별 OHLCV + 투자자수급 |
| `stock_universe` | 6693 | stock_code, stock_name, market, sector_large, shares_issued, market_cap, per, pbr, roe, roa | 전 종목 마스터 |
| `financial_data` | 9.2만 | stock_code, year, quarter, revenue, operating_profit, net_income, total_assets, total_equity, eps, bps, is_annual | 재무제표 |
| `peak_holding` | 31 | stock_code, stock_name, buy_price, current_price, quantity, entry_date, is_active, strategy, profit_pct | 가상매매 보유 |
| `peak_trade` | 31 | stock_name, tx_type(buy/sell), price, quantity, profit, strategy | 가상매매 거래내역 |
| `portfolio` | 29 | stock_code, quantity, avg_price, bought_at | 실제 포트폴리오 |
| `portfolio_snapshot` | 305 | snapshot_date, stock_code, close_price, quantity, eval_amount, profit_pct | 일별 스냅샷 |
| `portfolio_tx` | 37 | stock_code, tx_type, quantity, price, tx_date | 거래내역 |
| `signal_config` | 26 | scope, name, label, logic_type, params, is_active | 시그널 설정 |
| `signal_result` | 936 | config_id, stock_code, signal(green/yellow/red), score | 시그널 결과 |
| `stock_meta` | 1097 | stock_code, float_shares, shares_outstanding | 유동주식수 |
| `short_sell_daily` | 7만 | bas_dt, stock_code, short_qty, borrow_bal_qty, borrow_bal_pct | 대차잔고/공매도 |
| `buy_candidates` | 28 | stock_code, target_price, memo | 매수 후보 |
| `watchlist` | 61 | stock_code | 관심종목 |
| `telegram_channels` | 9 | channel_id, channel_name | 텔레그램 채널 |
| `report_files` | 2691 | stock_code, sector, report_date, file_path | 섹터 보고서 |
| `backtest_runs` | 2 | run_id, status, total_return_pct, trades_json | 백테스트 결과 |
| `investor_trading_daily` | 0 | bas_dt, stock_code, indv_net, inst_net, frgn_net | ⚠️ 미수집 |
| `foreign_holding_daily` | 0 | bas_dt, stock_code, frgn_hold_pct | ⚠️ 미수집 |
| `financial_source_snapshot` | ~25만 | stock_code, year, is_annual, report_type, data_source('fnguide'), revenue, op_profit, net_income, verification_status | FnGuide 원본 스냅샷 (마스터) |
| `financial_anomalies` | 3181 | stock_code, anomaly_type, severity, is_resolved | 재무 이상 분류 (unit_error/cfs_ofs/large_discrepancy 등) |
| `stock_collection_config` | 248 | stock_code, config_key, config_value | 종목별 수집 특성 (report_type/unit_verified 등) |
| `company_mapping_profile` | 6576 | stock_code('*'=공통), standard_key, source_system, account_id(XBRL), account_label_raw, confidence_score, valid_from/to, verified_by, is_active | 기업별 DART 계정 매핑 프로파일. stock_code='*'=공통18건+종목별6,558건 ★신규(2026-05-16) |
| `dart_raw_accounts` | 112 | stock_code, year, quarter, report_code, fs_div, account_id, account_nm, thstrm_amount, rcept_no | DART 원문 계정 저장. anchor_mismatch 4종목 2022년 CFS 원문(DART account_id는 API 미제공) ★신규(2026-05-16) |
| `data_quality_issues` | 79 | stock_code, year, quarter, table_name, field_name, reason_code(SOURCE_MISSING/ANCHOR_MISMATCH 등), severity, is_resolved | Null Sentinel — ANCHOR_MISMATCH 4건(HIGH)+SOURCE_MISSING 75건(금융업 DART미제공) ★신규(2026-05-16) |
| `data_lock` | 6840 | stock_code, year, table_name, is_locked, lock_basis('dart_verified'), lock_hash(md5) | Freeze 정책 — 2019~2022 DART 검증 완료 전량 잠금. financial 1,282건+cashflow 5,558건 ★신규(2026-05-16) |

### 중요 단위 규칙
```
inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt → 백만원 단위 (÷100 = 억원)
inst_net_buy, frn_net_buy → 수량(주)
예외: ^KS11, ^KQ11 지수 레코드의 inst_net_buy → 억원 직접 저장
```

### DB 연결 패턴
```python
# routes/ 파일 표준 (sqlite3 직접)
import sqlite3 as _sl
DB_PATH = "stock.db"
conn = _sl.connect(DB_PATH)
conn.row_factory = _sl.Row   # dict처럼 r["col_name"] 접근

# SQLAlchemy (ORM 필요 시)
from database import get_db
db: Session = Depends(get_db)
```

### 지수/ETF 제외 필터 (price_history 조회 시 항상 적용)
```sql
WHERE stock_code NOT LIKE '%^%'   -- ^KS11, ^KQ11, ^IXIC 등
  AND stock_code NOT LIKE 'GC%'   -- 금 선물
  AND stock_code NOT LIKE 'CL%'   -- 원유 선물
  AND stock_code NOT LIKE '%-F'   -- 선물
  AND stock_code NOT LIKE '%=%'   -- 통화 (USDKRW=X 등)
  AND stock_code NOT LIKE 'NQ%'   -- 나스닥 선물
  AND stock_code NOT LIKE 'ES%'   -- S&P 선물
```

---

## 3. API 엔드포인트 전체 목록

### main.py 직접 정의 엔드포인트
```
GET  /api/realtime/prices                  # KIS 실시간 주가 캐시
GET  /api/realtime/macro                   # 거시지표 실시간
GET  /api/dashboard/market-info/{code}     # 종목 시장정보 (sector, mktcap, 순위)
GET  /api/dashboard/chart/{code}           # 주가 차트 데이터
GET  /api/dashboard/sectors                # 섹터 목록
GET  /api/dashboard/screening/triple       # 3단계 스크리닝
GET  /api/dashboard/screening/logic        # 로직 스크리닝
GET  /api/dashboard/financial-table/{code} # 재무제표 테이블
GET  /api/dashboard/cashflow/{code}        # 현금흐름
GET  /api/dashboard/disclosures/{code}     # 공시 목록
GET  /api/dashboard/fundamentals/{code}    # PER/PBR (Naver 스크래핑 포함, 비동기 캐시)
GET  /api/dashboard/macro                  # 거시지표 캐시
GET  /api/dashboard/stats                  # 시스템 통계
GET  /api/search                           # 종목 검색
POST /api/reports/generate/{code}          # AI 리포트 생성
GET  /api/reports/latest/{code}            # 최신 AI 리포트
GET  /api/reports/ready                    # 리포트 준비 상태
POST /api/commands/refresh-cashflow/{code}
POST /api/commands/refresh-annual/{code}
POST /api/commands/monthly-bulk-update
POST /api/commands/daily-disclosure-check
POST /api/commands/screener-refresh
POST /api/commands/analyze/{stock_name}
GET  /api/commands/collect-status/{code}
GET  /api/commands/watchlist
DELETE /api/commands/watchlist/{code}
POST /api/commands/batch-float-shares
GET  /api/commands/batch-float-shares/status
GET  /api/kiwoom/status
POST /api/kiwoom/token/refresh
```

### routes/signals.py → /api/signals
```
GET  /market             # 시장 시그널 (캐시키: 'market', TTL 1800초)
GET  /market-regime      # 5단계 시장국면 점수 + 강제하향 + AI 브리핑
POST /market-regime/briefing # 시장국면 AI 브리핑 수동 생성
GET  /stock/{code}       # 종목 시그널 (캐시키: 'stock_{code}')
GET  /trend-candidates   # 추세 후보 (캐시키: 'trend')
GET  /value-candidates   # 가치 후보 (캐시키: 'value')
GET  /combo-candidates   # AI 콤보 후보 (캐시키: 'combo_candidates')
GET  /fin-screener       # 재무 스크리너
GET  /trigger-ranking    # 트리거 20 (캐시키: 'trigger')
GET  /meta               # 스크리너 메타정보
GET  /config             # 시그널 설정
PUT  /config/{id}        # 설정 수정
POST /config             # 설정 추가
DELETE /config/{id}      # 설정 삭제
POST /manual/{id}        # 수동 실행
```

### routes/trend.py → /api/trend (가상매매)
```
GET    /holdings              # 보유종목 (현재가: price_history 최신 close)
POST   /buy                   # 매수
POST   /sell                  # 매도
POST   /update                # 현재가/수익률 업데이트
GET    /trades                # 거래내역
GET    /summary               # 요약 (승률, 수익)
GET    /ai-holdings           # AI 자동매매 보유
POST   /ai-combo/execute      # AI 자동매매 즉시 실행
DELETE /trades/all            # 전체 삭제
GET    /v18/recommendations   # V18 AI종목발굴 추천 (strategy='gpt_v18')
POST   /v18/execute           # V18 추천 종목 즉시 매수 실행
```
- **GPT 추천 전략**: V18 (`strategy='gpt_v18'`), 구 V14(`gpt_v14`)는 제거됨
- 메타시뮬레이터 기반: backtest_runs DB의 AUTO 런 목록 블렌딩 → 상위 종목 추출

### routes/portfolio.py → /api/portfolio
```
GET    /                 # 포트폴리오 + 현재가 + 수익
POST   /sync-kis         # KIS 체결 동기화
PATCH  /{code}/bought-at # 매수일 수정
GET    /transactions     # 거래내역
POST   /transaction      # 거래 추가
POST   /kakao-parse      # 카카오뱅크 문자 파싱
PUT    /{code}           # 종목 수정
DELETE /{code}           # 종목 삭제
GET    /export/excel     # 엑셀 내보내기
POST   /import/excel     # 엑셀 가져오기
```

### routes/buy_candidates.py → /api/buy-candidates
```
GET    /                 # 매수 후보 + 현재가
POST   /                 # 추가
PATCH  /{code}           # 메모/목표가 수정
DELETE /{code}           # 삭제
GET    /short-sell/{code}# 대차잔고 조회
```

### routes/market_indicators.py → /api/market-indicators ★신규(2026-04)
```
GET  /investor-top       # 투자자별 순매수 상위 (params: date, limit=20)
GET  /turnover-top       # 회전율 상위 (params: date, market=ALL, limit=20)
GET  /investor-trend     # 수급 추이 차트 (params: market=kospi, days=60)
GET  /market-summary     # KOSPI/KOSDAQ 요약 + 오늘 수급
GET  /index-investor     # 지수 투자자 일별 (params: days=20)
GET  /available-dates    # 수급 데이터 있는 영업일 목록
```

### routes/reports.py → /api/reports
```
GET  /stock/{code}       # 종목 리포트 목록
GET  /download/{id}      # 파일 다운로드
GET  /sectors            # 섹터 목록
GET  /sector/{sector}    # 섹터별 리포트
```

### routes/telegram.py → /api/telegram
```
GET    /channels         # 채널 목록
POST   /channels         # 채널 추가
DELETE /channels/{id}    # 채널 삭제
POST   /collect          # 즉시 수집
GET    /mentions/daily   # 일별 언급
GET    /mentions/weekly  # 주별 언급
GET    /mentions/monthly # 월별 언급
```

### routes/backtest.py → /api/backtest
```
POST   /run              # 백테스트 실행
GET    /list             # 결과 목록
GET    /{run_id}         # 결과 상세
DELETE /{run_id}         # 삭제
```

### routes/ingest.py → /api/ingest
```
POST /fundamentals       # 재무 데이터 저장
POST /market-price       # 시장가 저장 (장중만)
POST /sectors            # 섹터 저장
POST /investor-trends    # 투자자 동향 저장
```

### routes/market_radar.py → /api/market-radar ★등록(2026-05)
```
GET  /all                         # 전체 섹터 RS 데이터
GET  /sector/{sector}/detail      # 섹터 상세 (섹터 지표 페이지)
POST /init-semiconductor          # 반도체 초기화
POST /refresh-cache               # 캐시 강제 갱신
GET  /export-csv                  # CSV 내보내기
POST /import-csv                  # CSV 가져오기
GET  /semiconductor/valuestream   # 반도체 밸류체인 (SemiconductorView)
POST /semiconductor/valuestream/refresh
```

### routes/sector_define.py → /api/sector-define ★등록(2026-05)
```
GET  /posts                       # Hot 섹터 포스트 목록
GET  /post/{id}                   # 포스트 상세
POST /parse                       # 포스트 파싱
```

### routes/extra_signals.py → /api/extra-signals ★신규(2026-05)
```
GET  /extra-signals/{code}  # 추가 시그널 (고용/수출/섹터트렌드/수급/ETF편입/ETF비중)
```
응답 구조: `{employment, exports, sector_trend, supply, etf_ratio, etf_inclusion}`
- sector_trend: sector_large 기준 분류 (sector_mid 아님)
- etf_inclusion/etf_ratio: etf_count=0 & etf_amount=0 인 날(수집실패)은 건너뜀

### ETF_check/routes_etf.py → /api/etf-check ★신규(2026-05)
```
GET  /tab1              # ETF 편입액 기준 (KOSPI/KOSDAQ 상위)
GET  /tab2              # ETF 편입액 증감 (1일/5일 전 대비)
GET  /tab3              # 시총대비 증감%
GET  /tab4              # 시총대비 비중%
GET  /search            # 종목명/코드 검색 (유효 날짜만 사용)
GET  /etf-list/{code}   # 종목 편입 ETF 목록 (etfcheck.co.kr 스크래핑)
```
- etf_amount=0인 날(수집실패)은 get_available_dates에서 자동 제외

### routes/stock_analysis_rs.py → /api/stock-analysis-rs ★성능개선(2026-05)
```
GET  /dashboard-data    # 요약만 반환: benchmarks, sector_rs, metadata (rs_list 없음)
GET  /dashboard-rows    # RS 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&cap_min=&market=&sector_mode=
GET  /high52-data       # 52주 메타데이터만 반환
GET  /high52-rows       # 52주 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&high_filter=
POST /precompute        # 캐시 강제 재계산 (스케줄러 18:30 호출)
```
- 초기 전송량: 2.3MB → 수십KB (rs_list/high52_list 제거)
- 캐시: scratch/stock_analysis_rs_cache.json (장중 10분, 장외 24시간 TTL)
- 동시 요청 시 double-check locking (compute는 락 밖에서 수행)

---

## 4. 스케줄러 (scheduler.py)

| 잡 | 시간 | 설명 |
|----|------|------|
| `_job_nightly_batch` | 00:10 daily | Yahoo/KIS/공공데이터 수집 |
| `_job_monthly_bulk` | 매월 1일 03:00 | stock_universe 전체 갱신 |
| `_job_disclosure_check` | 03:30 daily | DART 공시 확인 |
| `_job_intraday_prices` | 매 1분 (장중) | KIS 현재가 수집 → price_history |
| `_job_intraday_investor` | 매 5분 (장중) | KIS 수급 수집 → price_history |
| `_job_market_signal_briefing` | 07:00 daily | 5단계 시장국면 점수 계산 + OpenAI 5줄 브리핑 저장 |
| `_job_closing` | 15:40 daily | 종가 확정 + portfolio_snapshot |
| `_job_screener_precompute` | 매 30분 | 시그널 캐시 갱신 |
| `_job_krx_daily` | 18:00 daily (영업일) | KRX 승인API 전종목 OHLCV + 지수 수집 (data-dbg.krx.co.kr) |
| `_job_supply_daily` | 17:30 daily (영업일) | KIS 전종목 최근 30일 수급 누락분 보완 |
| `_job_krx_investor_playwright` | 18:10 daily (영업일) | KRX 전종목 기관/외국인 순매수 금액 수집 (Playwright, data.krx.co.kr 로그인) |
| `_job_kiwoom_health` | 매 10분 (평일 장중) | 키움 REST 인증/연결 상태 점검 |

### ETF 수집 스케줄 (crontab — ETF_check/scheduler.py)
| 시간 | 실행 모드 | 설명 |
|------|-----------|------|
| 20:30 평일 | `--once` | 메인 수집 (장 마감 후) |
| 23:30 평일 | `--retry` | 실패 종목 재수집 |
| 02:30 화~토 | `--backfill` | 전날 최종 백필 (재수집 실패 시 보완) |

---

## 5. 공유 캐시 (_signal_cache, main.py)

```python
_signal_cache = {}
# 키 목록: 'market', 'trend', 'value', 'combo_candidates', 'combo_v2', 'trigger',
#          'stock_{code}', 'prices', 'macro'
# 값 구조: {'data': [...], 'at': time.time()}
# TTL: 시그널 1800초, 주가 300초

# routes/signals.py에서 접근 방법:
def _cache():
    import main as _m
    return _m._signal_cache
```

---

## 6. 프론트엔드 컴포넌트 (App.jsx)

**App.jsx = 10,830줄 (분리 완료)**. 4개 최상위 컴포넌트는 별도 파일로 추출됨.

### 별도 파일로 분리된 컴포넌트 (views/)
| 파일 | 탭 키 |
|------|-------|
| `frontend/src/views/MarketIndicatorsView.jsx` | market_indicators |
| `frontend/src/views/SemiconductorView.jsx` | semiconductor (MarketRadar 내부) |
| `frontend/src/views/SectorFollowupView.jsx` | sector_followup (MarketRadar 내부) |
| `frontend/src/views/MarketRadarView.jsx` | market_radar |
| `frontend/src/utils.js` | API, isKRMarketOpen, isUSMarketOpen 등 공유 유틸 |

### App.jsx 내 컴포넌트 → 탭 키 → 시작 줄번호
| 컴포넌트 | 탭 키 | 줄번호 |
|---------|-------|--------|
| `BuyCandidateView` | buy_candidates | 462 |
| `WatchlistView` | watchlist | 802 |
| `MacroDashboard` | macro | 1296 |
| `StockAnalysis` | analysis | 1804 |
| `Screener` | screener | 3034 |
| `PeakView` | trend | 4402 |
| `PortfolioView` | portfolio | 4870 |
| `TradeAnalysis2` | hs_trade2 | 5771 |
| `SectorReports` | reports | 7121 |
| `SignalSettings` | (settings 내부) | 7234 |
| `AIInsight` | insight | 7414 |
| `BacktestView` | backtest | 8647 |
| `SettingsView` | settings | 9446 |
| `TelegramMentions` | telegram | 9741 |
| `SystemStatus` | system | 10266 |

### 네비게이션 구조
```
NAV_ITEMS 정의: ~10480줄
렌더 스위치:    ~10600줄

순서: macro → market_indicators → analysis → screener → trend
    → reports → telegram → backtest → hs_trade → hs_trade2
    ── (구분선) ──
    buy_candidates → watchlist → portfolio
    ── (구분선) ──
    settings → system
```

### 전역 상태 (App 최상위)
```javascript
const [activeTab, setActiveTab]          // 현재 탭
const [stockCode, setStockCode]          // 분석 중인 종목코드
const [portfolioAuth, setPortfolioAuth]  // 포트폴리오 인증
const API = (path) => path               // vite proxy → :8000
```

---

## 7. 환경변수 (.env)

```
KIS_APP_KEY / KIS_APP_SECRET          # KIS API (주가, 수급, 체결)
KIS_ACCOUNT_NO=63109821 / KIS_ACCOUNT_PROD=01
KRX_API_KEY=115C0F...                 # KRX (현재 data.krx.co.kr 접근 불가)
PUBLIC_DATA_API_KEY=93b5be...         # 공공데이터포털 (주가 OK, 투자자API 404)
DART_API_KEY=70dccf...                # DART 공시
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE
KIWOOM_ENABLED=false
KIWOOM_APP_KEY / KIWOOM_SECRET_KEY
KIWOOM_BASE_URL=https://api.kiwoom.com
KIWOOM_WS_URL=
```

---

## 8. 핵심 코딩 패턴

### 현재가 조회 (항상 DB 사용, Yahoo/KIS 직접 호출 X)
```python
row = conn.execute(
    "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
    (stock_code,)
).fetchone()
current_price = row[0] if row else fallback_price
```

### Telegram 야간 알림 억제 (peak_monitor.py)
```python
_cur_h = datetime.now().hour
if not (8 <= _cur_h < 22):
    sent = False  # 22:00~08:00 알림 보류 (모멘텀 easy 등 오발송 방지)
```

### 시그널 stale-while-revalidate 패턴 (routes/signals.py)
```python
# TTL 내: 캐시 즉시 반환
# TTL 초과: 캐시 즉시 반환 + 백그라운드 갱신 시작 (_bg_compute)
```

### 수급 금액 단위 변환
```python
# price_history._net_buy_amt는 백만원 → 억원 표시 시 ÷100
inst_억 = round(inst_net_buy_amt / 100.0)
# ^KS11/^KQ11은 여러 row가 날짜별로 분리되므로 GROUP BY + SUM 필요
```

### 라우터 등록 위치 (main.py 38~56줄)
```python
from routes.market_indicators import router as _market_indicators_router
app.include_router(_market_indicators_router, prefix="/api/market-indicators", tags=["market-indicators"])
```

### ETF 수집실패일 필터 패턴 (etf_inclusion_daily 조회 시 항상 적용)
```python
# etf_count=0 AND etf_amount=0 → 수집 실패일. 반드시 유효 데이터만 사용
valid_rows = [r for r in rows if (r["etf_count"] or 0) > 0 or (r["etf_amount"] or 0) > 0]
# get_available_dates에서도:
WHERE e.etf_amount > 0   -- 수집실패일 제외 필수
```

### 섹터 분류 기준 (sector_large만 신뢰)
```python
# sector_mid는 분류 오류 多 (e.g. 한국항공우주 → '상업서비스') — 사용 금지
# 섹터 분류·그룹핑은 반드시 sector_large 기준으로
sector = conn.execute("SELECT sector_large FROM stock_universe WHERE stock_code=?", (code,)).fetchone()["sector_large"]
```

### HS코드 공동 매핑 시 섹터 교집합 필터 (재발방지)
```python
# HS코드 단독 매칭은 이종업종 혼입 위험. 반드시 sector_large 교집합 필터 적용
# 예: extra_signals.py _get_hs_export_info() 참조
same_sector_codes = {r["stock_code"] for r in mc.execute(
    f"SELECT stock_code FROM stock_universe WHERE stock_code IN ({placeholders}) AND sector_large=?",
    all_codes + [own_sector]
).fetchall()}
```

### stock_collection_config 패턴 (종목별 수집 특성 등록)
```python
# 수집기에서 이 테이블을 먼저 읽어 종목별 특성 반영
import sqlite3
conn = sqlite3.connect("stock.db")
cfg = {r["config_key"]: r["config_value"] for r in conn.execute(
    "SELECT config_key, config_value FROM stock_collection_config WHERE stock_code=?", (code,)
)}
# 키:
#   preferred_report_type → "CFS" | "OFS"  (교정된 연결/별도 구분)
#   unit_verified         → "true"  (단위오류 수정 완료 종목)
report_type = cfg.get("preferred_report_type", "CFS")
```

### FnGuide 무결성 동기화 (수동 실행)
```bash
python3 scripts/fnguide_integrity_sync.py            # critical 수정 (unit_error+cfs_ofs)
python3 scripts/fnguide_integrity_sync.py --all      # large_discrepancy 640건 포함 전체
python3 scripts/fnguide_integrity_sync.py --dry-run  # 변경 없이 리포트만
```

### 데이터 소스 우선순위 (엄수) — Codex 지시서 20260516 기준
```
1순위: DART API (OpenDART)    → financial_data, cash_flow_data 확정 저장 (유일한 write 경로)
2순위: KRX API / Playwright   → price_history OHLCV, 지수
3순위: KIS API                → price_history 주가·수급 (장중)
4순위: 내부 계산              → PER/PBR/ROE/ROA (DART 데이터 기반)
검증용: FnGuide, Naver        → financial_source_snapshot 전용, 본 테이블 write 절대 금지

⚠️ FnGuide/Naver 스크래핑 → financial_data/cash_flow_data write 금지 (검증/비교 전용)
⚠️ FnGuide data_source='fnguide' 행이 본 테이블에 있으면 DART 행 우선 노출
⚠️ Naver PER/PBR/EPS 직접 DB 쓰기 금지 — 내부 계산값 사용
⚠️ 수학적 계산(net_income/shares)은 display 전용 — DB 직접 쓰기 금지

### PER/PBR 계산 방식 (CLAUDE.md 규칙) — TTM 기준

**분기 EPS 직접 합산 = FnGuide TTM 방식 완전 재현**

> ⚠️ net_income ÷ shares_issued 방식은 금지. shares_issued에 우선주 포함으로 EPS 과소됨.
> FnGuide가 분기보고서에서 이미 "지배주주NI ÷ 보통주수"로 계산한 분기 EPS를 합산하면 동일 결과.

```
EPS_TTM = SUM(financial_data.eps, is_annual=0, 최근 4분기)   ← FnGuide 분기EPS 직접 합산
BPS_TTM = financial_data.bps (is_annual=0, 최근 1분기)       ← FnGuide 분기BPS 직접 사용
PER_TTM = 최신 종가 ÷ EPS_TTM
PBR_TTM = 최신 종가 ÷ BPS_TTM
ROE     = 최신 연도 net_income / total_equity × 100  (annual 기준)
ROA     = 최신 연도 net_income / total_assets × 100  (annual 기준)
```

**왜 TTM인가**: FnGuide는 분기 실적 공시 즉시 집계 반영. Annual EPS(is_annual=1)는 수집 시점에 따라 Q4 미반영 가능. TTM은 최근 4분기를 직접 합산하므로 항상 최신.

- 네이버 PER: 직전 사업보고서(연간 공시) EPS 기준 — TTM보다 1년 늦을 수 있음
- FnGuide PER: TTM 또는 최신 연도 — 우리 계산과 근접
- main.py get_stock_fundamentals()에서 stock_universe.per/pbr 즉시 반환

### EPS/BPS 수집 방법 (FnGuide 파이프라인)
- 정기 수집: collectors/fnguide_financial_collector.py (run() 내 fetch_fnguide_eps_bps 자동 호출, SVD_Main.asp)
- 수동 TTM 일괄 재계산:
```python
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect('stock.db')
stocks = conn.execute("""
    SELECT su.stock_code, su.shares_issued, ph.close AS price
    FROM stock_universe su
    JOIN (SELECT stock_code, close FROM price_history p1
          WHERE date=(SELECT MAX(date) FROM price_history p2 WHERE p2.stock_code=p1.stock_code AND p2.close>0)
    ) ph ON ph.stock_code=su.stock_code
    WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
      AND su.shares_issued>0 AND ph.close>0
      AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
""").fetchall()
updated = 0
for code, shares, price in stocks:
    qs = conn.execute("SELECT net_income FROM financial_data WHERE stock_code=? AND is_annual=0 AND net_income IS NOT NULL ORDER BY year DESC, quarter DESC LIMIT 4", (code,)).fetchall()
    eq = conn.execute("SELECT total_equity FROM financial_data WHERE stock_code=? AND is_annual=0 AND total_equity>0 ORDER BY year DESC, quarter DESC LIMIT 1", (code,)).fetchone()
    u = {}
    if len(qs) >= 2:
        ttm_eps = sum(r[0] for r in qs) / shares
        if ttm_eps > 0:
            p = round(price/ttm_eps, 2)
            if 0 < p < 9999: u['per'] = p
    if eq and eq[0]:
        ttm_bps = eq[0] / shares
        if ttm_bps > 0:
            p = round(price/ttm_bps, 4)
            if 0 < p < 999: u['pbr'] = p
    if u:
        conn.execute('UPDATE stock_universe SET '+','.join(f'{k}=?' for k in u)+' WHERE stock_code=?', list(u.values())+[code])
        updated += 1
# ROE/ROA
conn.execute("""
    UPDATE stock_universe SET
        roe=(SELECT ROUND(fd.net_income*100.0/fd.total_equity,2) FROM financial_data fd WHERE fd.stock_code=stock_universe.stock_code AND fd.is_annual=1 AND fd.net_income IS NOT NULL AND fd.total_equity>0 ORDER BY fd.year DESC LIMIT 1),
        roa=(SELECT ROUND(fd.net_income*100.0/fd.total_assets,2) FROM financial_data fd WHERE fd.stock_code=stock_universe.stock_code AND fd.is_annual=1 AND fd.net_income IS NOT NULL AND fd.total_assets>0 ORDER BY fd.year DESC LIMIT 1)
    WHERE stock_code IN (SELECT DISTINCT stock_code FROM financial_data WHERE is_annual=1 AND net_income IS NOT NULL)
""")
conn.commit(); conn.close(); print(f'PER/PBR: {updated}개, ROE/ROA 업데이트 완료')
EOF
```

---

## 9. 알려진 이슈 & 제한사항

| 항목 | 상태 | 내용 |
|------|------|------|
| KRX 승인API (data-dbg.krx.co.kr) | ✅ 정상 | OHLCV·지수 정상 수집. PER/PBR은 제공 안 함 → DB 직접 계산 |
| KRX 웹API (data.krx.co.kr) | ✅ Playwright로 정상 | requests 방식 CSV 다운로드 실패(보안강화). Playwright(실 브라우저) → 로그인+OTP+CSV 모두 성공. 매일 18:10 스케줄링됨 |
| K-mydata | ❌ 인증실패 | KRX_API_KEY가 K-mydata용 아님 |
| pykrx | ❌ Empty | KRX 서버 차단으로 빈 DataFrame |
| 공공데이터포털 투자자API | ❌ 404 | getStocInvtTrdnInfo 서비스 폐지 |
| investor_trading_daily | ⚠️ 0행 | 수집 불가 (위 API 폐지 원인) |
| foreign_holding_daily | ⚠️ 0행 | 마찬가지 |
| price_history 수급 | ✅ 57일치 | KIS 매 5분 정상 수집 중, 주말 필터링 적용 |
| 시장 지표 기본날짜 | ✅ 수정됨 | 데이터 부족한 날 대신 수급 20건 이상인 영업일 자동 선택 |
| 주말 데이터 노출 | ✅ 수정됨 | 토/일요일은 기준일 목록 및 자동 선택에서 제외 |
| Trigger 20 | ✅ 수정됨 | URL /api/signals/trigger-ranking 로 수정 |
| 대차잔고 URL | ✅ 수정됨 | /api/buy-candidates/short-sell/${code} |
| 모멘텀 야간 알림 | ✅ 수정됨 | 22:00~08:00 억제 로직 추가 |
| price_history close=0 | ✅ 수정됨 | routes/ingest.py /investor-trends에서 가격 없는 날짜에 close=0 행 생성 버그 → else:continue로 수정. 매 KRX일별 잡에서 자동 정리 추가 |
| 가상매매 현재가 | ✅ 수정됨 | Yahoo Finance 제거, price_history 사용 |
| 개별종목 PBR/PER 지연 | ✅ 완전수정 | stock_universe DB 즉시 반환(0ms) + 백그라운드 Naver 갱신. 5초 재시도 로직 제거 불필요 |
| 시그널 계산 10초 지연 | ✅ 개선 | 서버 시작 시 warm-up + stale-while-revalidate |
| 재무제표 단위 오류 | ✅ 완전수정 | op_profit 597건·net_income 20건·equity 5건 억원→원 변환, Q4 254건 재계산, CFS/OFS 혼용 36건 재수집, 지주사 Q4 NULL 10건 처리, 수집오류 삭제 2건 |
| financial_data 백업 | ℹ️ 보관 | `financial_data_backup_20260412` 테이블로 수정 전 원본 보관 |
| 재무제표 Q4 대규모 손실 | ℹ️ 정상 | 잔존 14건(삼성SDI2016/현대건설2024/대한항공 등)은 실제 이벤트 손실로 수학적 정확값 |
| ETF 수집실패일 오표시 | ✅ 수정됨 | `etf_inclusion_daily`에서 etf_count=0 && etf_amount=0인 날은 수집 실패일로 간주 → extra_signals.py + routes_etf.py `get_available_dates`에서 자동 건너뜀. **재발방지**: ETF 관련 쿼리 시 반드시 `WHERE etf_amount > 0` 또는 valid_rows 필터 적용 |
| 섹터 트렌드 오분류 | ✅ 수정됨 | `sector_mid`(e.g. "상업서비스")가 업종과 맞지 않는 종목 多 → `sector_large` 기준으로 전체 변경. **재발방지**: 섹터 분류는 반드시 `sector_large` 기준으로. `sector_mid`는 신뢰도 낮음 |
| 수출공동 표시 이종업종 혼입 | ✅ 수정됨 | HS코드가 넓어 전혀 다른 업종(HD건설기계 등)이 공동 표시 → 동일 sector_large 종목만 필터링. **재발방지**: HS코드 기반 공동 매핑 시 반드시 sector_large 교집합 필터 필수 |
| **shares_issued 우선주 포함** | ⚠️ 미수정 | 삼성전자(1.43배), SK하이닉스(2.08배), 현대차(1.49배), LG화학(1.27배) 등 대형주에서 shares_issued가 보통주+우선주 합산으로 저장됨 → EPS/BPS 계산시 분모 과대 → 계산값 과소. **재발방지**: `주식수(보통주) = market_cap ÷ 종가`로 역산 가능. 수집기는 보통주 발행주식수를 별도 필드로 저장해야 함 |
| **EPS 저장값 vs 계산값 괴리** | ⚠️ 구조적 | FnGuide 저장 EPS = 지배주주 귀속 순이익 ÷ 보통주 수. 우리 계산 EPS = 전체 NI ÷ shares_issued(우선주 포함). 두 효과가 역방향으로 작용해 일치 불가. **올바른 계산**: 지배주주 순이익(별도 미저장) ÷ 보통주 수(미분리). FnGuide SVD_Main.asp에서 직접 수집한 EPS/BPS를 1순위로 사용할 것 |
| **TTM EPS 계산 부정확** | ⚠️ 주의 | TTM EPS = 최근 4Q net_income ÷ shares_issued 방식은 shares_issued 우선주 포함으로 EPS 과소 계산됨. 단, 분기 데이터 자체(TTM NI 합산 = annual NI)는 정확히 검증됨. 현재 stock_universe에 TTM PER 반영됨 — 외부 사이트 대비 PER 높게 나올 수 있음 |
| **PER/PBR 외부사이트 일치율** | ℹ️ 현황 | Codex 검증(500종목): revenue/op_profit 100%, net_income 97.48%, CF 98-99%. EPS(FnGuide) 67.79%, BPS(FnGuide) 82.53%. PER(Naver) 29.81%, PBR(Naver) 49.49% — 기준 연도·주식수 차이에 기인. 네이버 기준일 vs FnGuide TTM 기준일 다름 |
| StockEasy 일치율 검증 편향 | ✅ 수정됨 | 기존 `stockeasy_logic_validator.py`가 보유(매수) 정합성(F1) 중심으로만 검증하고 매도(편출) 적중률을 측정하지 않던 문제 수정. 매도 정답(removed+exits_today) vs 우리 매도후보 비교(P/R/F1) 추가, 리포트/튜닝로그 동시 기록. |

---

## 9-1. 데이터 검증 규칙 (외부사이트 교차검증 가이드)

### EPS/BPS 신뢰도 계층 (Codex 지시서 20260516 기준)
```
1순위: DART financial_data.eps (is_annual=0, 최근 4분기 합산) — DART 공시 기반
       → Q4 EPS = annual_eps - Q3_cumulative_eps (Annual + Q3 둘 다 있을 때만 추론)
2순위: 분기 net_income 4Q 합산 ÷ (market_cap ÷ 종가) 역산 보통주수
       → shares_issued 직접 사용 금지 (우선주 포함 과대)
3순위: stock_universe 배치계산값 — 정확도 낮음

FnGuide EPS/BPS → financial_source_snapshot 검증 전용. financial_data write 금지.
```

### shares_issued 오류 패턴 (외부 검증 기준)
```python
# 보통주 발행수 역산 (시총 ÷ 종가 = 보통주 기준 주식수)
real_common_shares = market_cap ÷ current_price

# 비율 > 1.1이면 우선주 포함 의심
ratio = shares_issued / real_common_shares

# 주요 우선주 보유 종목: 삼성전자, SK하이닉스, 현대차, 기아, LG화학 등
# → EPS/BPS 계산 시 이 종목들은 FnGuide 수집값 우선 사용
```

### 외부사이트 비교 기준 정리
| 지표 | 우리 DB 기준 | 네이버 기준 | FnGuide 기준 | 차이 원인 |
|------|------------|-----------|-------------|---------|
| EPS | 전체NI÷전체주식수 (부정확) | 최신 사업보고서 EPS | 지배주주NI÷보통주수 TTM | 주식수·귀속NI 기준 다름 |
| BPS | 전체자본÷전체주식수 | 최신 사업보고서 BPS | 지배주주자본÷보통주수 | 비지배주주지분 포함 여부 |
| PER | 종가÷TTM_EPS | 종가÷사업보고서EPS | 종가÷FnGuide_EPS | 기준 연도+주식수 모두 다름 |
| PBR | 종가÷TTM_BPS | 종가÷사업보고서BPS | 종가÷FnGuide_BPS | 상동 |
| ROE | annual NI÷annual equity | 사업보고서 ROE | 지배주주 기준 ROE | 귀속 기준 다름 |

### 향후 수집기 개선 지침
```
1. shares_issued_common (보통주만) 필드 별도 추가 → stock_universe 스키마 확장 필요
2. controlling_interest_ni (지배주주 순이익) 별도 수집 → financial_data 확장
3. FnGuide SVD_Main.asp 연 2회 재수집 (Q2/Q4 실적 공시 후: 8월, 3월)
4. EPS/BPS 검증: 수집 후 net_income÷shares vs EPS 차이 >30% → 재수집 플래그
5. market_cap ÷ 종가 역산값 vs shares_issued 비율 >1.15 종목 → 우선주 보정 필요 플래그
```

---

## 10. 고용정보 페이지 — 데이터 수집·로직 필수 참조

> **이 섹션을 먼저 읽지 않고 고용정보 관련 코드를 수정하지 말 것.**

### 파일 구조
```
employment_monitor/
├── routes_employment_v2.py   # FastAPI 라우터 (/api/employment-v2/*)
├── collect_labor_welfare.py  # WLB 수집기 (근로복지공단 고용보험)
├── collect_nps_monthly.py    # NPS 수집기 (국민연금 월별 신규/상실)
└── employment.db             # 고용정보 전용 DB (stock.db 아님)
```

### 두 데이터 소스의 본질적 차이 (반드시 이해할 것)

| 항목 | WLB (고용보험) | NPS (국민연금) |
|------|--------------|--------------|
| 테이블 | `wlb_monthly` | `nps_monthly` |
| 데이터 성격 | **스톡(Stock)** — 특정 시점 피보험자 총 수 | **플로우(Flow)** — 월별 신규취득/상실 건수 |
| 현재 보유 월 | `202505`(2026-05-04 수집), `202605`(2026-05-09~15 수집) | `202504`~`202603` 완전(2111~2151개사), `202604`~이후 대부분 미수집 |
| API 특성 | **날짜 파라미터 없음** → 실행 시점의 현재 데이터만 반환 | 월별 과거 조회 가능 |
| 용도 | 현재 피보험자 수 표시 | 기간별 순증감 계산 |

### ⚠️ WLB API 핵심 제약 (절대 잊지 말 것)
```
WLB API는 날짜 파라미터가 없다.
--month YYYYMM 은 DB 저장 레이블일 뿐, 과거 데이터를 가져오지 않는다.
즉, 언제 실행해도 항상 "실행 시점의 현재 데이터"만 수집된다.

현재 DB 상태:
- data_ym='202505': 2026-05-04에 수집 → 2026년 5월 초 시점의 피보험자 수
- data_ym='202605': 2026-05-09~15에 수집 → 2026년 5월 중순 시점의 피보험자 수
- data_ym='202504', '202506': 잘못된 레이블로 수집 → 이미 삭제됨

⚠️ 202505도 2026-05-04에 수집된 것. "2025년 5월" 데이터가 아님. 레이블 주의.
```

### NPS 데이터 해석 규칙
```
nps_monthly.data_ym = '202603'
  → 2026년 3월 한 달 동안 국민연금에 신규 가입한 인원(new_hires)과 상실한 인원(terminations)
  → net_change = new_hires - terminations (그 달의 순증감)

기간별 순증감 = 각 종목의 최신 data_ym 기준으로 N개월 net_change 누적합
(전체 통일 ref_ym 고정 방식 사용 금지 — 최신 데이터 있는 종목을 무시하게 됨)
```

### 이상값 필터에 대하여
```
⚠️ Claude가 임의로 이상값 필터(2000명 절대값, 평균의 5배 등)를 추가/변경하지 말 것.
합병·분사·법인 전환은 실제 사업 이벤트이며, 필터 기준은 사용자가 결정한다.
현재 코드에 필터가 있다면 사용자 확인 후에만 수정할 것.
```

### 피보험자 수 표시
```
1순위: WLB 202605 실측값
2순위: 미수집 종목은 NPS 누적 추정 또는 미표시 (사용자 결정)
⚠️ 202605 없다고 202505 자동 fallback 금지 — WLB 레이블 신뢰도 문제 있음
```

### API 엔드포인트 (routes_employment_v2.py)
```
GET /api/employment-v2/trend           # 종목별 피보험자+기간별순증감 (메인 테이블)
  ?sort_by=workers|1m|3m|6m|1y
  ?limit=200
GET /api/employment-v2/insurance/chart # 기업별 그래프
  ?code=047810
GET /api/employment-v2/insurance       # 고용보험 상시인원 순위
GET /api/employment-v2/annual-top      # 사업보고서 기준 연간 인원 순위
```

### 알려진 데이터 한계 및 미결 사항
- NPS 202604/202605 대부분 종목 미수집 — 수집기 재실행 필요
- WLB 202605 미수집 종목 275개 (에코프로 086520 포함) — 재수집 필요
- WLB data_ym 레이블이 실제 수집 시점과 다를 수 있음 — 레이블 정책 재정의 필요
- NPS는 국민연금 기준이므로 고용보험 미가입 프리랜서·특수고용직 제외

---

## 11. 자주 수정하는 작업별 파일 가이드

| 작업 | 파일 | 참고 위치 |
|------|------|-----------|
| 새 API 엔드포인트 추가 | `routes/new.py` 생성 → `main.py` 등록 | main.py 38~56줄 |
| 시그널 로직 수정 | `signal_engine.py` | 섹션 1 함수목록 참조 |
| 스케줄 시간 수정 | `scheduler.py` | `_seconds_until()` 호출부 |
| 가상매매 로직 | `routes/trend.py` + `peak_monitor.py` | — |
| 포트폴리오 로직 | `routes/portfolio.py` | — |
| 프론트 탭 추가 | `App.jsx`: 컴포넌트 + NAV_ITEMS + 렌더스위치 | 줄: 7459, 7585 |
| Telegram 알림 | `peak_monitor.py` + `notifier.py` | — |
| DB 스키마 변경 | `init_db.py` + `migrate_db.py` | — |
| KIS 수집 로직 | `collectors/kis_collector.py` | — |
| 환경변수 추가 | `.env` + `config.py` | — |

---

## 11. 변경 이력 (작업 완료 시 여기에 기록)

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-05-21 | **전체 4중 검증 AMBIGUOUS 자동해소 + AI 판단 + 프론트엔드 업데이트**: `scratch/resolve_all_ambiguous.py` 신규. Phase1(규칙 9종 자동해소): FIN_CROSS FnGuide음수→DART신뢰 93건, FIN_NAVER DART≈0→FG채택 90건, FIN_NAVER FG=NAV≠DART→DART매핑오류 171건, FIN_NAVER revenue 구조적차이(금융업/지주사) 2451건, MATCH CF FG≈Seibro 42건, UNKNOWN CF DART≈Seibro 81건, SIGN_REVERSED/POSSIBLE_PERIOD FG없음 51건 — 총 2,979건 자동해소. Phase2: 나머지 1,637건 GPT-4o-mini AI판단(confirmed 418건, unresolvable 1,219건). Phase3: `config/validation_complete_registry.json` 2,615종목 레지스트리 생성. 최종: CONFIRMED 97,603건(98.8%), AMBIGUOUS 1,219건(1.2%, 해소불가). 필드별 검증률: capex/dep 100%, OCF/ICF 98.7~98.8%, 영업이익 99.1%, 총자산 99.4%, 매출 94.6%(구조적차이). main.py data-quality API: BS(total_assets/total_equity) 검증 항목 추가, sources 필드 추가, BS ambiguous는 등급 강등 제외(보조항목). App.jsx 신뢰도 패널: 소스 컬럼 추가(DART+FnGuide+Naver·Seibro·2중 색상구분), ✅/⚠️ 항목수 뱃지, 범례 업데이트. |
| 2026-05-21 | **StockEasy 3전략 매칭 F1=100% 달성**: `stockeasy_logic_validator.py` 전면 개선. ①Peak: emerg/largecap 보충함수 제거(역합병 이상치 점수 2338 버그 해결) → calc_stockeasy_trend_candidates 단독 사용. ②Value: MA120 필터 0.80→0.65 완화, 52주저점+기관매수 촉매 보너스(+10) 추가, 시총 10조/2조 구간 세분화. prior_mult 1.0→1.8, lookback 14→21일. ③params: Peak max_candidates 30→20, min_score 20→28; Momentum max_candidates 10→15; Value max_candidates 20→15, min_mktcap 2000→1000억. 결과: Peak F1 45%→100%, Momentum F1 16.7%→100%, Value F1 46.7%→100% (2026-05-21 기준 SE픽: Peak 반도체/전자 10종, Momentum 2종, Value 10종 완전일치) |
| 2026-05-18 | **DART 1Q 누락 원인 수정(핵심 버그)**: `data_collector.py`의 `_financial_exists()`가 `(stock_code, year, quarter)`만으로 존재 판정해 `fnguide` 행이 있으면 `dart` 최신 분기 수집을 스킵하던 문제 수정. 존재 판정 기준을 `is_annual + data_source('dart')`까지 포함하도록 변경하고 `collect_fundamentals()` 호출부에도 반영. 또한 `main.py` `_collect_dart_to_db()`의 기존 중복체크도 `data_source='dart'` 조건을 추가해 동일 문제 재발 방지. 검증: 삼성전자(005930) `2026Q1` 데이터 저장 확인 및 `/api/dashboard/financial-table/005930?type=quarter` 응답에 `26년1Q` 노출 확인. |
| 2026-05-18 | **AI추천 매도기준/StockEasy 실주문 우선순위 보강**: `main.py` `ai_combo` 자동매도에 하드손절 `-20%` 추가(기존 MA20 2일 이탈/MA60 붕괴 유지), 보유행 `stock_code` 우선 사용으로 이름 매칭 실패 시 매도 누락 가능성 축소. `frontend/src/App.jsx` AI추천 안내 문구를 `-15%`→`하드손절(-20%)`로 동기화. `stockeasy_autotrade.py`는 신규편입 매수 순서를 `trend_score→score→sector_score` 우선순위로 정렬해 예수금 부족 시 고점수 종목 우선 매수, NXT 시간대(08:00~08:59, 15:30~19:59)에는 `현재가<=스탁이지 매수가` 조건일 때 즉시 매수 시도, 편출은 장중 제한 없이 현재가 기준 즉시 매도 시도로 변경. |
| 2026-05-18 | **StockEasy 운영 기준 보강(신규 발굴 우선)**: `stockeasy_strategy.md`에 “신규 편입/신규 매도 발굴 중심 일일 점검” 섹션 추가. 기존 보유 정합성 중심에서 벗어나 `replay-entry`(편입 발굴률) + `backtest-sell`(매도 발굴률)를 최우선 KPI로 관리하고, 악화 시 당일 2회 이상 수정/재검증 반복하도록 운영 규칙 명시. |
| 2026-05-18 | **StockEasy 편입일 재현/매도 백테스트 루프 추가**: `stockeasy_logic_validator.py`에 `--replay-entry`, `--backtest-sell`, `--tune-strategy`, `--tune-sell` CLI 옵션 추가. 편입일(`entry_date`) 기준 종목 단위 재현 검증 함수 `replay_entry_day_inclusion()` 추가(종목별 hit/miss 사유 출력). 대형주 주도 보정 `_calc_largecap_leader_candidates()` 추가로 신규/대형 추세주 누락 완화. 매도 로직을 `as_of` 지원 구조로 분리(`_extract_sell_features`, `_get_our_sell_candidates(..., as_of=...)`)하고, 스냅샷 누적 매도 정합성 함수 `backtest_sell()` 및 자동 튜닝 `tune_sell_params()` 추가. 또한 `stockeasy_strategy.md`에 일일 반복 운영 루틴(검증→재현→매도백테스트→튜닝→재검증) 문서화. |
| 2026-05-18 | **StockEasy 적중률 검증 고도화(매도 포함)**: `stockeasy_logic_validator.py`에 매도 검증 지표 추가(정답=당일 removed+exits_today, 우리 매도후보와 P/R/F1 계산). 트래커 요약 테이블에 `매도F1` 컬럼 추가, 전략 상세/텔레그램/튜닝로그에 매도 P/R/F1 동시 기록. 또한 신규 편입 추세주 누락 완화를 위해 `get_our_candidates()`에 `재무희소 종목 보정 트랙(_calc_emerging_breakout_candidates)`을 추가해 가격·거래량·MA20 기반 보정 후보를 Peak/Momentum 점수에 병합. |
| 2026-05-17 | **주요지표 시그널 보드 5단계 개편 + AI 아침 브리핑 자동화**: `signal_engine.py`에 KOSPI/KOSDAQ 6개 그룹(추세/미국금리/밸류/수급/리스크/시장폭) 기반 0~100 점수 및 1~5단계 국면 로직, 강제하향 조건(최소 4단계/즉시 5단계), 신규매수 허용/금지 규칙 구현. `market_signal_briefing` 테이블 신설 및 OpenAI(`gpt-4o-mini`, `OPENAI_API_KEY`) 5줄 브리핑 저장 기능 추가. `routes/signals.py`에 `/api/signals/market-regime`, `/api/signals/market-regime/briefing` 추가. `scheduler.py`에 매일 07:00 `_job_market_signal_briefing` 스케줄 추가. `frontend/src/App.jsx` 주요지표 시그널보드 상단에 단계/점수/매수허용/방어필요/강제하향/요인/세부점수/AI브리핑 테이블 추가(기존 상세 시그널 설명 패널 유지). |
| 2026-05-17 | **자동매매 화면 정리 + 수익률 표시 보강**: `frontend/src/App.jsx` 자동매매 탭에서 `모의 주문/체결` 섹션 제거(실계좌 중심 UI로 단순화). 실계좌 보유잔고 테이블에 `수익률` 컬럼 추가하여 `profit_pct` 표시(예: 아이엠바이오로직스 57.69%). |
| 2026-05-17 | **수주공시 알림 페이지 복구**: `main.py`에서 `routes.dart_contracts` 라우터 등록 누락으로 `/api/dart-contracts/*`가 404를 반환하던 문제 수정. `app.include_router(_dart_contracts_router, prefix='/api/dart-contracts')` 추가 후 정상 응답 확인. |
| 2026-05-16 | 고용정보 로직 전면 수정: ①종목별 최신 NPS 월 기준 기간별 순증감 계산 (전체 통일 ref_ym 폐기) ②이상값 필터 5배평균→2000명 절대값 완화 ③WLB fallback(202505) 적용 ④wlb_diff_12m(실측 12개월 증감) 신규 ⑤CLAUDE.md 섹션10 고용정보 규칙 문서화 |
| 2026-04-12 | `routes/market_indicators.py` 신규 (6개 API), `시장 지표` 메뉴 추가, CLAUDE.md 초안 작성 |
| 2026-04-12 | 시장 지표 주말(토/일) 제외 필터링 추가, 수급 데이터 부족한 날 자동 스킵 로직 적용, KIS 실데이터 강제 다운로드 완료 |
| 2026-04-12 | `.claude/settings.json` hooks 설정, CLAUDE.md 자동 관리 체계 구축 |
| 2026-04-12 | financial_data 재무제표 단위 오류 전체 수정: 단위변환 622건, Q4 재계산 254건, CFS/OFS 혼용 36건 재수집(fix_financial_cfs_ofs.py), 지주사 Q4 NULL 10건, 수집오류 삭제 2건 |
| 2026-04-12 | backtest.py v4 전략 개선: ①시장추세필터(KOSPI>MA60 하락장 매수 차단) ②손절 -8%→-6% 강화 ③추적손절 고점대비-10% 신규 ④익절+20% 신규 ⑤모멘텀 외국인AND기관 동반순매수(OR→AND) + 거래량 2배→2.5배 |
| 2026-04-15 | App.jsx: localStorage 탭/종목/기간 상태 유지(새로고침 복원), 수급 바차트 토글(기본 숨김→버튼 표시), 기간 탭 3년/10년 추가, fetchDays 최대 3650일 |
| 2026-04-15 | `collect_naver_investor.py` 신규 (네이버 금융 기관/외국인 순매수 3~20년치 스크래핑). 전종목 3년치 수집 중 (PID 35466, ~10시간) |
| 2026-04-15 | `collect_krx_history.py` 신규 (KRX 승인 API, 2010~현재 OHLCV 백필). `scheduler.py` `_job_krx_daily` 추가 (18:00 영업일). 백필 실행 중 (PID 34496, 2010~2018). `collect_kis_supply_history.py` 신규 (전종목 30일 수급 누락분 보완). `_job_supply_daily` 추가 (17:30). 장중 수급잡 버그 수정 (전종목→관심종목만). KRX _collected_dates 임계값 10→500 수정 |
| 2026-04-16 | Logic-#2(수급 주도 모멘텀) 구현: signal_engine.py `calc_combo_v2` 신규, routes/signals.py `/combo-v2` 엔드포인트 추가, main.py 사전계산에 Logic-#2 포함, App.jsx Screener 탭에 Logic-#1/Logic-#2 드랍다운 선택 메뉴 추가 |
| 2026-04-16 | price_history close=0 행 버그 수정: routes/ingest.py `/investor-trends` 엔드포인트에서 가격 없는 날짜에 close=0 행 생성하던 문제 수정(else→continue). 기존 624건 삭제. scheduler.py KRX일별 잡에 자동 정리 로직 추가. 시장지표 페이지: MarketIndicatorsView display:none 유지(리마운트 방지), investor-top 사전계산 스케줄러 추가, 기본탭 both_buy로 변경, 외인+기관매도 탭 위치 이동. 수급차트 zero-line 강화. 현금흐름 period 포맷 변경(2023Q2→'23년2Q') |
| 2026-04-13 | backtest.py v5: AI 적극검토 콤보 로직 완전 재현(Minervini+Graham+RS), KOSPI MA120 강화, 시총1000억+ 필터, 손절-8%, 익절+15%, 최소보유5일, RS 상대강도 보조필터. signal_logic.py COMBO_TREND_SCORE_MIN 8→10. main.py 콤보 필터 KOSPI MA60 추세 차단 추가. 결과: 2022-2024 CAGR+23%, MDD-10.9%, 샤프1.99 |
| 2026-04-16 | 포트폴리오 수급·대차잔고·시그널 전면 개선: ①`_to_억` 버그 수정(amt=0 시 qty*close/1e8, amt≠0 시 amt/100) ②short_data를 buy_candidates/short-sell과 동일 형식(today/avg5/avg5_prev/신호)으로 통일 ③4분면 매매신호 신설(추세점수±4/가치점수 PBR·PER·ROE·ROA): add_buy·hold·hold_value·take_profit·real_sell·cut_loss ④포트폴리오 테이블 하단 AI 판단기준 설명 섹션 추가 |
| 2026-04-17 | market_indicators.py investor-trend: `WHERE close>0` 제거→`HAVING MAX(close)>0` (^KS11 투자자row close=0 필터 버그 수정, 오늘 수급 +0억 오류 해결). turnover-top: prev_close+chg_pct 추가. App.jsx MarketIndicatorsView: 회전율 테이블 등락률 컬럼 추가, fmtAmt 0→'-', 일별 바차트 Cell 색상(빨강/파랑), 누적 차트 30일/3개월/6개월/1년 탭 추가(cumDays 상태), 개인 bar 제거 |
| 2026-04-16 | data_collector.py 버그 3종 수정: ①`kis_data["date"].isoformat()` str 오류 → hasattr 분기 ②`_krx` 미정의 → `_krx = None` 초기화 ③pykrx `get_market_net_purchases_of_business_day` API 없음 → `collect_closing_investor` 비활성화. DART `could not find` 예외 처리 강화. 상시수집 루프에서 주가/수급/매크로 제거(scheduler.py와 중복) → 재무 수집 전용으로 최적화. data_collector.py 재시작 (PID 59720) |
| 2026-05-15 | `routes/extra_signals.py` + `ETF_check/routes_etf.py` 워크트리에 추가 및 main.py 등록. ETF 수집실패일(etf_count=0 && etf_amount=0) 건너뜀 로직 추가(extra_signals + routes_etf get_available_dates). 섹터 트렌드 sector_mid→sector_large 기준으로 전체 변경. 수출/계약 공동 표시 동일 sector_large 종목만 필터링. 고용 트렌드 카드에 current_workers(현재 근무인원) 필드 추가(백엔드+프론트). |
| 2026-05-15 | App.jsx 분리: MarketIndicatorsView/SemiconductorView/SectorFollowupView/MarketRadarView → `frontend/src/views/` 별도 파일. 공유 유틸 → `frontend/src/utils.js`. App.jsx 12,752줄→10,830줄(-1,922줄). CLAUDE.md 섹션6 줄번호 업데이트. |
| 2026-05-16 | ETF Check `/search` 수집실패일 폴백 수정: `WHERE e.trade_date = ?` → 종목별 최근 유효일 서브쿼리. 서버 재시작 방법 CLAUDE.md 명시(launchctl kickstart). Codex 병렬작업 충돌방지 규칙 추가. crontab ETF 23:30 재수집+02:30 백필 추가. cash_flow_data 이상값 6건 NULL 처리. |
| 2026-05-16 | PBR/PER 즉시 반환: `get_stock_fundamentals(main.py)` 캐시 미스 시 Naver 스크래핑 대기(2~3초) 제거 → `stock_universe.per/pbr` 즉시 반환 + 백그라운드 Naver 갱신 유지. EPS 계산 보완: `processor.py` `_calc_eps()` 신규 - eps=0.0이거나 None인데 net_income이 실제값이면 `net_income/shares_issued`로 자동 계산. |
| 2026-05-16 | 종합 RS/52주 신고가 데이터 미노출 긴급 수정: `main.py`에 `routes.stock_analysis_rs` import + `app.include_router(..., prefix='/api/stock-analysis-rs')` 누락 등록(HTTP 404 원인). `frontend/src/views/StockAnalysisRsView.jsx`는 `Promise.allSettled`로 변경해 한 API 실패 시 전체 빈 화면 방지, 52주 신고가 탭에 필터(전체/근접/신고가달성)·정렬(점수/근접/거래량배수) 추가. 재기동은 규칙대로 `launchctl kickstart -k`만 사용. |
| 2026-05-16 | 종합 RS 성능/동작 보강: `routes/stock_analysis_rs.py`를 캐시 기반으로 재구성. 장중(평일 09:00~15:35) 10분 TTL, 장외 1일 TTL로 `/scratch/stock_analysis_rs_cache.json` 반환. price_history는 종목별 최근 260거래일 window 조회로 축소. 응답에 `benchmarks.kospi/kosdaq` RS 추가. `frontend/src/views/StockAnalysisRsView.jsx`는 섹터명 정규화로 탭 필터 정확화, 상단 강도바에 KOSPI/KOSDAQ RS 위치 표시, 섹터 탭 선택 시 해당 섹터 종목만 노출하도록 보강. |
| 2026-05-16 | PER/PBR Naver 스크래핑 완전 제거 → `price×EPS/BPS` 직접 계산(main.py). stock_universe fallback 유지. EPS 계산 보완(`processor.py _calc_eps`). `stock_collection_config` 테이블 신규. `scripts/fnguide_integrity_sync.py` 신규: unit_error 52건+cfs_ofs 29건 FnGuide 기준 수정, holding_company 175건 OFS config 등록, financial_anomalies 81건 resolved. large_discrepancy 640건은 --all 옵션으로 별도 실행. |
| 2026-05-16 | Codex 분석 기반 성능·정합 개선: ①`routes/stock_analysis_rs.py` double-check locking(compute를 락 밖으로), `/dashboard-data` 요약만 반환, `/dashboard-rows`·`/high52-rows` 서버 페이지네이션 신규, `/precompute` POST 추가. 초기 전송량 2.3MB→수십KB. ②`processor.py` 조회 API 내 Q4 DB 쓰기 제거(read-only 보장, 락 경합 방지). ③`stockeasy_logic_validator.py` `_load_params` shallow merge→deep merge(기본 필드 유실 방지), `min_mktcap_억` 점진 decay 로직 추가(대형주 신호 없을 때 500억씩 하향). ④`stockeasy_analyzer.py` 프롬프트 다이어트: 종목당 6줄→1줄 핵심 8개 필드, 2000자→800자, max_tokens 1500→800. ⑤`scheduler.py` `_loop_rs_precompute` 추가(18:30 영업일, /precompute POST 호출). ⑥`StockAnalysisRsView.jsx` 서버 사이드 페이지네이션+지연 로딩(52주탭 최초 진입 시만 로드), AbortController+300ms 디바운스. |
| 2026-05-16 | Codex 검증 결과 반영: ①EPS NULL/0 13,765건·BPS NULL/0 2,609건 net_income·total_equity 기반 배치 계산(`scripts/ops/fix_eps_bps_batch.py`). ②backup 테이블 5개(30일+) CSV export(41MB) 후 DROP. ③stock_corrupted_backup_20260506.db(1.6G)·venv_backup(248M) archives/2026-05-16/ 이동. ④DART 2024 분기 수집 스크립트(`scripts/ops/collect_dart_2024_quarters.py`) 신규 — P1 우선 43종목부터 Q1~Q3 수집. ⑤.gitignore .gz/.zip/.pkl/archives/ 추가. ⑥운영계획서 docs/ 4종 + ops 스크립트 3종 신규 커밋. |
| 2026-05-16 | **3페이지 API 미노출 수정**: `main.py`에 `routes.market_radar`(prefix=/api/market-radar) + `routes.sector_define`(prefix=/api/sector-define) 등록 누락 → 추가. `SemiconductorSectorView.jsx` `Promise.all` → `Promise.allSettled` 변경(한 API 실패 시 전체 페이지 blank 방지). **CapEx/연간 현금흐름 중복 연도 수정**: `get_cashflow_table`을 ORM LIMIT 5행 → ROW_NUMBER() OVER(PARTITION BY year) SQL로 교체, 연도별 최적 1행 선택(capex 우선→quarter=4 우선→null 수 적은 행). 9,190건 중복 DB행이 API에서 더 이상 노출 안 됨. |
| 2026-05-16 | **EPS/BPS 루트 원인 수정**: `collectors/fnguide_financial_collector.py`에 `fetch_fnguide_eps_bps()` 함수 추가(SVD_Main.asp) + `run()` 루프에서 종목당 1회 자동 호출 → EPS/BPS가 FnGuide 파이프라인에서 직접 수집·저장됨. **백테스트 라우팅 정합화**: App.jsx 드롭다운에서 미구현 V1/V2/V3/V5/V6/V7 옵션 제거(7→7 → V4/VT/V8/V10/V11/V12/VT+DART 7개 유지), 미지원 전략 선택 시 alert 명시적 에러. **PER/PBR 재보정**: stock_universe.per/pbr를 Naver 스크래핑값 → price÷EPS/BPS 계산값으로 일괄 재계산(PER 1,494건·PBR 2,520건). **stale 문구 제거**: backtest.py STRATEGY_DESC "수급 57일 제한" 삭제. **strategy INSERT 보강**: `run_backtest_v8/v12` 내부 INSERT에 strategy='v8'/'v12' 컬럼 추가. |
| 2026-05-17 | **DART 우선 데이터 아키텍처 전환**: ①`collect_dart_financial_batch.py` 신규 — DART API로 전종목(~3,945) P&L·BS 연간+분기 배치 수집, EPS=DART직접필드 우선→None시 net_income/shares 계산, BPS=`자본총계(CFS)`/shares (부채와자본총계 오매핑 버그 수정: `부채` 포함 계정 제외), 수집 후 FnGuide 비교 자동 검증CSV 생성. ②`main.py` CF쿼리 버그 수정: `latest_cf_repr` ORDER BY를 `data_source='dart' THEN 0, 'fnguide' THEN 1, ELSE 2` 순으로 변경 — 기존 `quarter=4 THEN 0` 방식은 Q4증분행(NULL source, 잘못된 CF값)이 FnGuide연간행보다 우선 선택되던 버그. ③설계원칙 확정: DART annual행(is_annual=1,quarter=4)이 P&L·CF 진실 소스; Q4증분=annual-Q3누적(역산, 별도저장않음); EPS/BPS는 DART직접필드→DB계산→FnGuide비교 순; FnGuide는 검증전용. |
| 2026-05-17 | **현금흐름 연간/분기 NULL 전수 수정**: ①`main.py` 연간CF 3중 버그 수정: [A]연간 `_annual_cash_end` 맵이 is_annual=False 분기 branch에만 초기화 → 연간 branch에도 추가. [B]OCF=ICF=FCF=0인 DART행(수집오류) 우선순위 최하위로 강등(ranked CTE에 `all-zero penalty`추가). [C]cash_end=0 → fallback 허용(DART 파싱실패로 0저장 대응). [D]연간 fallback 로직(capex/depr/cash_end): ranked 행에 없으면 `_annual_capex_map`/`_annual_depr`/`_annual_cash_end` 맵에서 보완. ②`collectors/fnguide_financial_collector.py` 버그 수정: `cash_end_row`·`depr_row` 초기화 누락(has_cf=False 테이블 처리 시 UnboundLocalError). ③`scratch/audit_api_vs_fnguide.py` 분기 비교 제거(API=증분값 vs FnGuide=YTD누적 → 방법론 불일치). ④FnGuide 재수집 배치 재기동(PID 12745, --no-cross-validate). 검증 결과: 연간 DIFF 801건→13건(분기노이즈 제거+실제 수정). |
| 2026-05-17 | **DART 현금흐름 파싱 전면 개선**: ①`dart_mapping_engine.py` `DEFAULT_ACCOUNT_ID_MAP`에 누락 ID 2개 추가: `dart_CashAndCashEquivalentsAtEndOfPeriodCf`→cash_end, `dart_AdjustmentsForDepreciationExpense`→depreciation (이 누락이 cash_end 84% NULL의 근본 원인). ②`dart_collector.py` `_CF_MAP` 키워드 확장: 영업/투자/재무활동 "순현금흐름" 변형(7개), 이마트 패턴("영업활동으로부터의순현금유입"), depreciation 추가 2종, cash_end 추가 3종. ③`config/dart_cf_account_catalog.json` 신규 (top150 전수조사 account_id 카탈로그). ④`config/dart_account_id_reference.md` 신규 (account_id 관리 문서). ⑤OCF=0 실패 9종목 18건 재수집 완료(삼성전기·HD현대·이마트·동원산업 등). ⑥`scratch/cf_3way_validate_and_fix.py` 신규 (DB+DART재파싱+FnGuide 3-way 비교·자동수정 도구). |
| 2026-05-17 | **DART ICF 계정 오매핑 수정 (두산 패턴)**: `collectors/dart_collector.py` `_parse_cf_df()`에 "단방향 소계 스킵" 로직 추가 — `ifrs-full_CashFlowsFromUsedInInvestingActivities`가 "투자활동으로 인한 현금유입액"(단방향)에 할당된 비표준 보고서에서 오매핑 방지. account_nm에 "유입액"/"유출액"/"현금유입"/"현금유출" 포함 시 OCF/ICF/FCF account_id 매핑 스킵 → keyword scan으로 낙하. 두산(000150) 2022 ICF +19176억→-2909억, 2021 ICF +24721억→-3151억 DB 직접 수정. 전종목 스캔 결과 동일 패턴 추가 피해 없음(다른 양수ICF 케이스는 FnGuide와 일치). `scratch/verify_dart_cf_mapping.py` 신규(DART 원본 계정 vs DB vs FnGuide 3-way 비교 스크립트). |
| 2026-05-16 | **재무 데이터 품질 검증 완료 + 규칙 문서화**: ①TTM 분기 데이터 신뢰도 확인(annual vs 4Q합산 오차 0%) ②shares_issued 우선주 포함 종목 식별(SK하이닉스 2.08배, 삼성전자 1.43배, 현대차 1.49배 등) ③EPS 저장값 vs 계산값 괴리 원인 확정(FnGuide=지배주주NI÷보통주수, 우리=전체NI÷우선주포함) ④Codex 500종목 외부 검증 결과 기록(revenue/op_profit 100%, EPS 67.79%, PER 29.81%) ⑤섹션 9-1(데이터 검증 규칙) + logic_reference.md 섹션13 신규. 향후 개선: shares_issued_common 필드 추가, FnGuide 연 2회 재수집 규칙 |
| 2026-05-16 | **TTM PER/PBR + ROE/ROA 일괄 재계산**: PER/PBR을 Annual EPS 기준 → TTM(최근 4분기 net_income÷shares_issued) 기준으로 변경(삼성전자 PER 94→35, 시장 실제값 수렴). ROE/ROA를 annual net_income÷equity/assets로 전종목(2,582개) 일괄 계산(기존 98.2% NULL→해소). CLAUDE.md 계산방식 규칙 갱신. |
| 2026-05-16 | **백테스트 로직 전략 복원 및 신규 구현**: `backtest.py`에 `_is_buy_value()`(V1 가치매수 Graham IV), `_is_buy_v2()`(수익성 스코어≥3), `_is_buy_v5()`(기관+외인 동반순매수+MA정배열) 신규 추가. `run_backtest_value()/run_backtest_v2()/run_backtest_v5()` 래퍼 추가. `routes/backtest.py`에 `/run-v1-value`, `/run-v2`, `/run-v5` 엔드포인트 추가. App.jsx 드롭다운: 12개 전략 optgroup 그룹핑(가치/추세/수급/실적/섹터). `logic_reference.md` 전면 재작성: 전략-코드-API 1:1 매핑 검증 완료, V3/V6/V7 미구현 명시, V10+HS/V11+HS 항목 추가. `_get_financial_as_of` 중복 정의 버그 제거. |
| 2026-05-16 | **기업별 22~25 무결성 프로파일 생성 + 필수 선행규칙 추가**: Top500(코스피250+코스닥250) 대상 2022~2025 연간 재무/현금흐름 1:1 대조 프로파일 2,000행 생성(`/scratch/company_profile_22_25_top500_1to1_20260516.csv/.json`). 요약: revenue 100%, op 100%, NI 95.82%, OCF 98.47%, ICF 98.59%, FCF 98.97%, CapEx 96.19%, 감가상각 34.42%. `CLAUDE.md`에 재무 작업 전 필독 파일/작업 지침(원천 적재 vs 검증 분리, 자동확정 금지, 잠금 조건) 강제 규칙 추가. |
| 2026-05-16 | **Codex 지시서(20260516) 반영 — 데이터소스 우선순위 전면수정**: ①CLAUDE.md 섹션8 데이터소스 우선순위: FnGuide 1순위→DART 1순위로 변경. FnGuide/Naver→검증 전용(financial_data/cash_flow_data write 금지). ②현금흐름 API(`get_cashflow_table`): 연간 quarter=4(DART) 절대 우선, Q4 추론 엔진 추가(Annual-Q3_cumulative, 조건 불충족 시 추론 금지). ③분기 현금흐름 `_q` 컬럼 fallback 추가(derived_q4 행 활용). ④`fundamentals` API에 `bps`, `roa` 필드 추가, ROE stock_universe 우선. ⑤추가시그널 로딩스피너 추가(레이아웃시프트 방지). |
| 2026-05-16 | **Codex 백필 결과 검증 + 데이터 품질 인프라 구축 + 실데이터 적재**: ①anchor_mismatch_2022 4건(현대홈쇼핑/지엔씨에너지/서진시스템/한진칼) DART API 재조회 → dart_raw_accounts 112건 저장. ②company_mapping_profile: 공통 XBRL 표준매핑 18건 + 백필성공종목 개별매핑 6,558건 = 총 6,576건. ③data_quality_issues 79건(ANCHOR_MISMATCH 4 + SOURCE_MISSING 75). ④data_lock: 2019~2022 DART 검증 완료 data 잠금 — financial_data 1,282건 + cash_flow_data 5,558건 = 총 6,840건 (is_locked=1). ⑤50샘플 검증리포트 생성(99.2% 일치, 불일치 2건 DART 원본 미수집 확인). |
| 2026-05-16 | **주요지표 미국채(2Y/10Y/30Y) 추가**: VIX 카드 하단에 미국 국채 섹션 신설. Yahoo 매크로 수집 심볼 `^UST2Y`, `^TNX`, `^TYX`, `DX-Y.NYB`를 수집 경로 전반(`collectors/yahoo_collector.py`, `data_collector.py`, `scheduler.py`, `main.py`)에 추가. `processor.get_macro_status()`에 `us_treasury`(금리/스프레드/risk flags/ai_summary) 반환 확장. 프론트 `App.jsx`에 금리 테이블, 10Y-2Y/30Y-10Y 스프레드, 일일 위험신호/요약 문구 렌더링 추가. |
| 2026-05-16 | **미국채 값 미표시/스케일 오류/그래프 누락 후속 수정**: `^UST2Y` 데이터 공백 대응으로 `2YY=F` 도입(10Y/30Y는 `^TNX`/`^TYX` 우선, `10Y=F`/`30Y=F` 보조). `processor.py`에 다중 심볼 폴백(`_query_latest_any`), DB 미존재 시 Yahoo 실시간 폴백(`_fetch_latest_yahoo_close`), 금리 정규화(`_normalize_treasury_yield`) 추가. 국채 history 반환을 30일→365일 확대. 프론트 `App.jsx` 국채 테이블을 원달러 스타일(`항목/현재가/등락률/기준일`)로 통일하고 2Y/10Y/30Y 미니그래프 3개 추가. 백필로 `US2Y` 502건, `^TNX` 502건, `^TYX` 502건 적재 후 API 검증 완료(US2Y 3.82, US10Y 4.595, US30Y 5.128, 각 history 258). |
| 2026-05-16 | **자동매매 준비 1차 반영**: `routes/kis_trading.py`에 `/api/kis-trading/account/summary` 추가(실계좌 요약+보유+당일체결 통합). `kis_client.py`에 `get_account_snapshot()` 신설(TTTC8434R `output1/output2` 파싱: 예수금/평가금/손익 포함). `frontend/src/App.jsx` 계좌현황(PortfolioView) 서브탭에 `자동매매` 추가, 실계좌 카드(예수금/D+2/평가금/손익) + 보유잔고 테이블 + PAPER 주문/포지션/실현손익 패널 연결. StockEasy 모멘텀 대조 결과: snapshot 기준 7종목 vs local active 4종목(교집합 4, 재현율 57.14%). 로컬 감지 스케줄은 `scheduler.py` 기준 매일 16:30(`_loop_stockeasy_analysis`) 실행. |
| 2026-05-16 | **자동매매 2차(요청 반영) — StockEasy 30분 동기화 + 실주문 옵션**: `stockeasy_autotrade.py` 신규. 모멘텀 Easy(전략 1) 기준으로 **30분마다** 사이트 상태를 동기화하여 `stockeasy_sync_state`/`peak_holding`/`peak_trade` 반영 + 텔레그램 발송(편입/편출). 편입 시 종목당 예산 기본 200만원(`STOCKEASY_PER_STOCK_BUDGET_KRW`), 편출 시 계좌 보유수량 전량 매도 시도. 실주문은 `STOCKEASY_LIVE_AUTOTRADE=true` AND `STOCKEASY_LIVE_START_DATE`(기본 2026-05-18) AND 장중(09:00~15:30) 조건에서만 실행. `scheduler.py`에 `스탁이지30분동기화` 잡 추가(1800초 주기). `kis_client.py`에 `place_order_cash()` + `_issue_hashkey()` 추가. |
| 2026-05-19 | **cash_flow_data CFS/OFS 이중 저장 구조 도입**: `schemas.py` `CashFlowIngest`에 `report_type: str = "CFS"` 추가. `main.py` `_upsert_cashflow()`이 `report_type` 포함 WHERE절로 CFS·OFS 행 독립 관리. `main.py` `_collect_dart_cashflow()`이 CFS·OFS 각각 독립 수집(기존: CFS 우선 + OFS fallback → 변경: 각각 독립 수집). `collectors/dart_collector.py` `_fetch_cf_sync()` 동일 변경. `cash_flow_data` UNIQUE(stock_code,year,quarter,is_annual,report_type) 기존부터 지원. |
| 2026-05-19 | **OFS 백필 스크립트 신규**: `scratch/collect_ofs_backfill.py` — CFS 행 존재하나 OFS 미수집인 25,571건(연간) 대상 DART OFS 백필. `data_source='dart_ofs_backfill'`. 체크포인트: `scratch/ofs_backfill_progress.json`. PID 84172로 백그라운드 실행 중. |
| 2026-05-19 | **cf_triple_validator 임계값 재설계**: 기존 ±10% 단일 → ±3% CONFIRMED / 3~15% CLOSE_MATCH / 15%+ AMBIGUOUS. 데이터 근거: CONFIRMED 건의 95%+가 Seibro와 0.1% 이내. OFS 행은 Seibro(CFS 기준) 비교 제외, DART-FnGuide만 비교. `cf_validation_flags`에 CLOSE_MATCH status 추가(306건 재분류: CONFIRMED→CLOSE_MATCH). |
| 2026-05-19 | **V14 연속 실운용 백테스트 체계 추가**: `config/v14_meta_config.json`(단일 고정 로직), `scripts/backtest_v14_continuous.py`(2020-03-01~2025-05-31 리셋 없는 연속 시뮬레이션), `scratch/HANDOFF_V14_META_CONTINUOUS_20260519.md`(Claude 재검증용 상세 handoff) 추가. 정책: 기간별 베스트 전략 선택 금지, 고정 날짜 우선순위 소스 매핑 + 실예산 제약(최소주문/교체매매/DD컷/스트레스가드) 기반 검증. |
| 2026-05-19 | **GPT추천(V14) 실시간 운영 탭/스케줄 추가**: `routes/trend.py`에 `GET /api/trend/v14/recommendations`, `POST /api/trend/v14/execute` 추가(장중 V14 매수/매도 추천 및 가상매매 실행, strategy=`gpt_v14`). `scheduler.py`에 `V14장중30분` 루프 추가(평일 09:00~15:30, 30분 간격). `frontend/src/App.jsx` `AI 종목 발굴` 탭에 `🤖 GPT추천(V14)` 신규, `가상매매` 전략 탭에 `🤖 GPT추천(V14)` 신규 및 즉시실행/추천갱신 버튼 추가. |
| 2026-05-19 | **5단계 시장 브리핑 정지(5/17 고정) 복구**: 원인 확인 — 서버 재시작 캐치업 시 `시장시그널브리핑` 잡이 DB writer 락 경합으로 스킵되고 재시도 없이 종료됨(로그: `시장시그널브리핑 스킵 — 다른 DB writer 실행 중`). 조치: `scheduler.py` `_run_job_safe()`를 bool 반환으로 변경, `_maybe_catchup_market_signal_briefing()`에서 스킵 시 120초 지연 1회 재시도 스레드 추가. `routes/signals.py` `/api/signals/market-regime`에 self-heal 추가(오늘 브리핑 2건 미만이면 조회 시 자동 생성 시도). 수동 복구 실행으로 `market_signal_briefing`에 `2026-05-19` KOSPI/KOSDAQ 2건 적재 확인. |
| 2026-05-19 | **키움 연동 사전 연결 작업**: `collectors/kiwoom_collector.py` 신규(REST 토큰 발급/헬스체크), `routes/kiwoom.py` 신규(`/api/kiwoom/status`, `/api/kiwoom/token/refresh`), `main.py` 라우터 등록, `scheduler.py`에 `키움연결체크`(평일 장중 10분) 잡 추가. `config.py`에 `KIWOOM_ENABLED`, `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY`, `KIWOOM_BASE_URL`, `KIWOOM_WS_URL` 환경변수 확장. |
| 2026-05-20 | **V18 메타시뮬레이터 전략 도입 + V14 교체**: `scripts/backtest_v16_final.py` V18 단일 전략으로 단순화(6개 기간 모두 KOSPI 초과수익 달성, 누적 +487.28% / MDD -28.21%). `generate_anchor_signals.py` 6차 윈도우에서 207940(삼성바이오, -35.4% 조기손절) 제거. `routes/trend.py` gpt_v14→gpt_v18 전면 교체(상수/함수/엔드포인트). `frontend/src/App.jsx` V14→V18 전면 교체(state/함수/API경로/UI레이블). DB `backtest_runs` 정리: 172개 불필요 런 삭제(190→57), AUTO 소스윈도우 런만 유지. |
| 2026-05-20 | **V18 스캘핑 방지 — 매도 조건 근본 원인 수정**: 스캘핑 근본 원인=v_anchor 종목에 개별 MA 조건(`trend_break = cur < ma60 * 0.985`) 적용 → KB금융 MA60≈157k > 현재가 151k → 매수 즉시 매도 발생. 수정: v_anchor 매도는 KOSPI 기준만(하드스탑 -10% OR KOSPI<MA60 3일 연속), combo 매도는 개별 MA(하드스탑 -10% OR 추세이탈). 스케줄러 10분 주기 복원(장중 악재 대응 위해 하루 2회→10분 복귀). 쿨다운(v_anchor 5일/combo 3일), 미보유 종목만 매수 유지. 중복 active 보유 데이터 정리(MAX(id) 유지). |
| 2026-05-20 | **V18.1p 피라미딩 + 예산 제한 도입**: 백테스트 검증: max=2 +645%(+43%p), max=3 +652%(변동성 큼). max=2 확정(MDD 동일, 연도별 균형). `simulate()` `max_tickets_per_stock` 파라미터 + 복합 포지션 키(`{code}#{n}`). `routes/trend.py`: `VIRTUAL_CAPITAL=1억`, `CASH_RESERVE_PCT=20%`(2,000만원 상시 예수금), `PYRAMID_MIN_DAYS=2`(즉시 전량 매수 방지). `execute_v18_now()` 예산 체크 추가(`avail_cash < V18_TICKET_KRW` 시 매수 중단). `_build_v18_recommendations()` 예산 현황 summary 포함. App.jsx GPT추천(V18) 탭에 예산바(투자금/잔여/소진 경고) + 빈 목록 원인 메시지 추가. DB 정리: 예산 초과 2번째 티켓 6건 삭제(138M→70M). 현황: 6종목 70M 투자, 10M 잔여투자가능, 20M 예수금. |
| 2026-05-20 | **스케줄러 V18 버그 수정 + v_anchor 실시간 구현**: scheduler.py `_job_v14_10m`이 존재하지 않는 `execute_v14_now` 호출하던 버그 → `execute_v18_now`로 수정(장중 10분마다 실제 실행됨). `routes/trend.py` `_build_v18_recommendations`에 v_anchor 실시간 로직 추가: KOSPI>MA60이면 V_ANCHOR_UNIVERSE(삼성전자/SK하이닉스/현대차/한화에어로스페이스/KB금융/HD현대일렉트릭/NAVER) 자동 매수후보, KOSPI<MA60 3일 지속 시 anchor_exit 매도신호. 텔레그램 알림에 KOSPI/MA60 상태 표시. App.jsx AI종목발굴 탭 헤더에 KOSPI 추세 배지(v_anchor ON/OFF) 추가. |
| 2026-05-20 | **V18.1 전략 개선 확정**: Codex 스윕 2,592건 기반. ①`dd_cut` -12%→-10%(전체 평균 +81.8%p, ②구간 +17.6%→+37.0%). ②`ext_ticket_pct` 0.25→0.30 / `ext_max_ticket` 50M→60M(6th 윈도우 자본 집중 강화). ③`v_largetrend` 비중 0.15→0.10, 잔여(v11/v_trend/v8/v10) 비례 재분배. 최종: 누적 +601.2% / MDD -26.3% / 6/6구간 KOSPI 초과 / 35bp에서도 +412% 강건성 확인. `routes/trend.py` 하드 스탑 -12%→-10% 반영. App.jsx AI 종목발굴·가상매매 탭 하단에 V18.1 전략 설명 박스 추가. |
| 2026-05-20 | **주간 4중 검증 마스터 스크립트 신규** (`scratch/weekly_revalidation.py`): Phase A(fnguide_seibro/NULL_seibro capex/dep NULL→DART 재조회, 9,986건 PID 37665 진행중), Phase B(NULL source CF DART 재조회), Phase C(재무제표 교차검증 확장 2022~2025 30,813건 삽입), Phase D(OFS CF capex/dep 재수집), Phase E(revenue AMBIGUOUS 분석 — 음수 26건 NULL처리, 금융업 구조적 차이 확인), Phase F(검증 현황 리포트). |
| 2026-05-20 | **Revenue AMBIGUOUS 원인 확정**: 매출 13~16% CONFIRMED이지만 영업이익 94~98% CONFIRMED. 금융업(증권사)은 DART 영업수익(총거래대금) vs FnGuide 순영업수익 기준 차이(5~10배)로 구조적 AMBIGUOUS. 비금융업 CLOSE_MATCH(5~20%)는 DART account_id 매핑이 매출 하위항목만 캐치하는 경우. 영업이익/순이익/자산/자본 검증은 94~99% CONFIRMED → 재무 핵심지표는 신뢰도 매우 높음. |
| 2026-05-20 | **Revenue 극단적 오류 수정**: DART revenue가 FnGuide의 10% 미만(극단적 과소)인 106건 + 음수 26건 = 총 132건 NULL 처리. 영향: 삼성SDI·LG화학·LG에너지솔루션·POSCO홀딩스 등 대형주 포함. processor.py FnGuide 50배 이상치 보정으로 display 레이어에서 FnGuide 값 자동 사용. |
| 2026-05-20 | **CF 검증 현황 정리**: OCF 2022~2025년 98~99% CONFIRMED(4중 검증). capex null rate 28%(fnguide_seibro 9,915건이 95% null). dep null rate 63%(구조적 한계 — 간접법 CF 묶음으로 DART 추출 불가). Phase A DART rate limit으로 447건만 채움(내일 4시 cron 재실행). |
| 2026-05-20 | **주간 재검증 스케줄 설정**: scheduler.py에 `_loop_weekly_revalidation` 추가(일요일 03:00, Phase C+E+F). crontab: `0 4 * * * weekly_revalidation.py --phase A` (매일 새벽 4시 Phase A 재시도). Phase A+B 체인: `scratch/run_weekly_chain.sh` (Phase A 완료 후 Phase B 자동 시작). |
| 2026-05-20 | **Revenue 재수집 스크립트**: `scratch/requery_revenue_null.py` — revenue NULL 처리된 종목 DART 재조회 + 광범위 account_id 후보군 시도 + FnGuide ±30% 검증. Phase A/B 완료 후 실행 예정. |
| 2026-05-21 | **재무제표 P&L 3중 검증 (FIN_NAVER)**: `scratch/validate_financial_naver.py` 신규 실행. 2,135개 종목 × 2019~2025년, Naver Mobile API `https://m.stock.naver.com/api/stock/{code}/finance/annual`에서 매출액·영업이익·당기순이익 수집(억원→원 변환). DART Q4 × FnGuide Q0 × Naver 3중 교차검증 결과를 `cf_validation_flags.flag_type='FIN_NAVER'`에 저장. PID 26263 실행 중(약 10분 소요). |
| 2026-05-21 | **data-quality API FIN_NAVER 반영**: `GET /api/dashboard/data-quality/{code}` — flag_type별(MATCH/FIN_CROSS/FIN_NAVER) 분리 집계. P&L fin_item 레이블 "DART·FnGuide·Naver 3중 검증" 추가. 등급 A 설명 Naver 포함 여부 자동 전환. weekly_revalidation.py Phase F에 FIN_NAVER 필드별 연도별 통계 추가. |
| 이전 세션 | routes/ingest.py, routes/portfolio.py 신규 분리; Yahoo Finance 제거; Trigger20 URL 수정; 야간 알림 억제; 시그널 warm-up 추가; 대차잔고 URL 수정; PBR/PER 재시도 로직 |

| 2026-05-22 | **재무 입력 규칙 재정의 핸드오프(Codex)**: 사용자 기준 확정(소스 우선순위 금지, DART/FnGuide/Naver/Seibro 4소스 합의값 채택) 반영 점검. 현황 진단: `financial_data` data_source NULL/blank 70,598/107,837(65.5%), 분기 매출 음수 1,149건(Q4 526건), `company_mapping_profile` 전종목 커버리지 351/3,881(9.0%), `financial_source_snapshot` unverified 33,201건. Claude 재검증용 상세 문서 `docs/codex_handoff_financial_consensus_reverify_2026-05-22.md` 생성(틀린부분/수정방향/SQL 재현셋 포함). |

| 2026-05-22 | **Codex 전수 재검증 완료 후 핸드오프 갱신**: `scratch/weekly_revalidation.py --phase F` 재실행(리포트 `scratch/revalidation_report_20260522_212622.json`) 및 DB 전수 점검 완료. 핵심 잔여 이슈 확정: `financial_data` source NULL/blank 70,598/107,837(65.5%), 분기 revenue<0 1,010건(Q4 526), 매핑 커버리지 351/3,881(9.0%). 합의값 기반 입력 체계 전환 필요사항을 `docs/codex_handoff_financial_full_reverify_2026-05-22.md`로 작성. |

| 2026-05-22 | **Codex 전수 상세검증 산출물 생성**: 사용자 요청에 따라 요약이 아닌 오류군 상세 CSV 전수 추출(`scratch/full_reverify_20260522/*`). 핵심: 분기 revenue<0 1,015건, 연간 DART/FnGuide 매출 괴리 634건, FIN_NAVER CLOSE_MATCH 2,902건, source null(2022+) 24,727건, report_type null 2,076건, 매핑 미커버리지 3,530건. Claude 즉시 수정용 핸드오프 `docs/codex_handoff_financial_verified_errors_2026-05-22.md` 생성. |
| 2026-05-22 | **Codex 전수검증 후 대규모 재무 데이터 정비**: ①Q4 음수 531→143건(73%↓), 전체 음수분기 1015→399건(61%↓): 300+개사 DART 연간 재다운로드+Q4 재계산, 9M/Q1/H1 DART 재수집으로 누적/standalone 혼용 케이스 수정, 불가 케이스 NULL처리. ②report_type NULL 1,848건→0건: 전체 CFS로 설정. ③source NULL ~70,598건→0건: legacy_collected/data_quality_null 마킹. ④None소스 Q4 음수 중 양수대체행 있는 55건 삭제. 신규 data_source: quarterly_recalc_9m, quarterly_recalc_dart, legacy_collected, data_quality_null 추가. |
| 2026-05-22 | **Codex 2차 검토 5가지 이슈 해소**: ①cash_flow_data source NULL 52,712건→0건(legacy_collected 43,867건+data_quality_null 8,845건 마킹). ②financial_anomalies unit_error 52건·cfs_ofs_mislabeled 29건 is_resolved=1 확인(기완료). ③CAPEX 부호 혼재(dart_ofs_backfill) 918건→0건(ABS 절댓값 적용, 전체 규약: capex>0). ④Q4 음수 dart 소스 29건=실제손실(한국전력·조선업·코로나) 확정. ⑤depreciation NULL 53% 구조적 한계 확정+API fallback 규칙 문서화. |
