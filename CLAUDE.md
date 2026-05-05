# 주식 대시보드 — Claude 필수 참조 문서

---

## ⚠️ CLAUDE 필수 행동 규칙 (모든 세션에서 자동 적용)

> **이 섹션은 Claude가 반드시 따라야 할 행동 규칙입니다. 예외 없이 적용됩니다.**

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

### 토큰 절약 규칙
- 파일 전체를 읽기 전에 이 문서에서 줄 번호를 확인하고 해당 범위만 읽는다.
- DB 스키마 확인 → 섹션 2 참조 (init_db.py 열지 않음)
- API 엔드포인트 확인 → 섹션 3 참조 (routes/*.py 열지 않음)
- 컴포넌트 위치 확인 → 섹션 6 참조 (App.jsx 전체 스캔 안 함)

---

## 1. 프로젝트 구조

```
/Applications/stock_dashboard/
├── main.py              # FastAPI 앱 + 라우터 등록 (1792줄)
├── scheduler.py         # 수집 스케줄러 CollectionScheduler (~1000줄)
├── signal_engine.py     # 시그널 계산 엔진 (2507줄)
├── peak_monitor.py      # 가상매매 모니터 + Telegram 알림 (663줄)
├── stockeasy_analyzer.py# ★2026-05 스탁이지 3전략 AI 역추론 분석기 (scrape+DB+AI+TG)
├── config.py            # 환경변수 로드
├── database.py          # SQLAlchemy SessionLocal + get_db
├── models.py            # SQLAlchemy ORM 모델
├── kis_client.py        # KIS API 토큰 관리
│
├── routes/              # FastAPI 라우터 (main.py 38~60줄에 등록)
│   ├── signals.py       → /api/signals/*
│   ├── trend.py         → /api/trend/*  (가상매매)
│   ├── portfolio.py     → /api/portfolio/*
│   ├── buy_candidates.py→ /api/buy-candidates/*
│   ├── market_indicators.py → /api/market-indicators/*  ★2026-04 신규
│   ├── tenbagger.py     → /api/tenbagger/*              ★2026-04 신규
│   ├── dart_contracts.py→ /api/dart-contracts/*         ★2026-05 신규
│   ├── reports.py       → /api/reports/*
│   ├── telegram.py      → /api/telegram/*
│   ├── backtest.py      → /api/backtest/*
│   └── ingest.py        → /api/ingest/*
│
├── tenbagger_engine.py  # 텐버거 발굴 엔진 (스코어링+OpenAI+텔레그램)
├── Sector_define/       # 섹터 팔로우업 (블로그 파싱+AI 분석)
│   ├── blog_parser.py   # 네이버 블로그 "돈의흐름 팔로잉" 파서
│   ├── routes_sector.py # /api/sector-define/*
│   ├── SectorFollowup.jsx # 프론트엔드 뷰 (React)
│   └── init_db.py       # DB 초기화 스크립트
├── ETF_check/           # ETF 모니터링 서브앱 ★2026-05 신규
│   ├── routes_etf.py    # /api/etf-check/* (main.py에 sys.path로 등록)
│   ├── init_db.py       # etf_check.db 초기화
│   └── etf_check.db     # ETF 포지션/수익 데이터
├── employment_monitor/  # 고용정보(NPS) 서브앱 ★2026-04 신규
│   ├── routes_employment_v2.py  # /api/employment-v2/* (main.py에 sys.path로 등록)
│   ├── fetch_nps_2years.py      # NPS 2년치 강제 수집
│   ├── update_nps_daily.py      # 일별 신규 NPS 자동 업데이트
│   └── employment.db            # NPS 고용 데이터 (별도 DB)
│
├── collectors/          # 외부 데이터 수집기
│   ├── dart_contract_collector.py # DART 수주·공급계약 공시 수집+AI분석 ★2026-05 신규
│   ├── kis_collector.py # KIS API (주가·수급·실시간)
│   ├── krx_collector.py # KRX / K-mydata (현재 접근 불가)
│   ├── public_data.py   # 공공데이터포털
│   ├── dart_collector.py# DART 공시
│   ├── yahoo_collector.py # Yahoo Finance (해외지수)
│   └── base.py          # BaseCollector (rate limit, async)
│
├── .claude/
│   ├── settings.json    # hooks 설정 (UserPromptSubmit, Stop)
│   └── hooks/
│       ├── session_start.sh  # 매 프롬프트: CLAUDE.md 지시사항 주입
│       └── session_stop.sh   # 세션 종료: 로그 기록
│
└── frontend/src/App.jsx # 단일 파일 React SPA (~9366줄)
```

### 연관 외부 프로젝트

```
/Applications/us_market_dashboard/          ★ 미국 주식 대시보드 (포트 8002) ★2026-05 신규
├── main.py             # FastAPI 앱 — 포트 8002, /api/us/* 전체
├── scheduler.py        # 독립 스케줄러 (가격/재무/펀더멘탈 주기적 갱신)
├── init_db.py          # DB 스키마 초기화
├── backfill.py         # CLI 백필 도구 (--mode universe|prices|financials|indices|all)
├── start.sh            # 서버 시작 스크립트
├── collectors/
│   ├── universe.py     # S&P500(Wikipedia) + NASDAQ-100 + NASDAQ All 종목 수집
│   └── yfinance_collector.py  # yfinance 기반 가격/재무/펀더멘탈 수집
├── routes/
│   ├── market.py       # /api/us/market/* (overview, sector-perf, top-movers, indices/chart)
│   ├── stocks.py       # /api/us/stocks/* (search, /{ticker}/info|chart|financials|comparison)
│   └── screener.py     # /api/us/screener/ (8개 필터, 정렬, 페이지네이션)
├── frontend/           # React+Vite SPA (포트 5174 dev, dist/ prod)
│   └── src/App.jsx     # 4탭: 시장개요/종목분석/스크리너/시스템
└── us_market.db        # 독립 SQLite DB (us_universe, us_price_history, us_financial_data, ...)
  ※ 완전 독립 앱. stock.db와 무관. yfinance 무료 데이터만 사용.
  ※ S&P500 503종목 + NASDAQ-100 101종목 = 516종목 유니버스 (2026-05-04 기준)
  ※ 가격: 5년치(2021~) 백필 중. 재무/펀더멘탈: 분기별 수집 중
  ※ 시작: bash /Applications/us_market_dashboard/start.sh (백엔드 8002)
          cd frontend && npm run dev (프론트엔드 5174)

/Applications/sector_radar/               ★ 독립 FastAPI 앱 (포트 8001)
├── api.py          # FastAPI 앱 — /Applications/stock_dashboard/stock.db 도 참조
├── collector.py    # 섹터별 대표 종목 주가 + 집계 수집
├── scheduler.py    # 매일 18:30 자동 수집
├── init_db.py      # sector_radar.db 초기화
└── sector_radar.db # companies, prices, sector_daily, company_fundamentals
  ※ 현재 stock_dashboard main.py 에 mount되어 있지 않음 — 별도 실행 필요 시 uvicorn api:app --port 8001

/Applications/stock_dashboard/hs_trade_lab/   ★ HS코드 무역통계 분석 서브앱
├── app/            # SQLAlchemy 모델 + 분석 로직 패키지
│   ├── models.py   # CustomsMonthlyRecord, HSCodeCompanyMap 등
│   ├── analytics.py# 섹터별 수출입 분석
│   └── config.py   # ROOT_STOCK_DB = /Applications/stock_dashboard/stock.db 참조
├── semiconductor_value_lab/  ★ 반도체 밸류체인 분석 서브앱
│   ├── fastapi_app.py # 독립 FastAPI (DB: semiconductor_value_lab.db)
│   ├── scripts/rebuild_cache.py
│   └── data/semiconductor_value_lab.db
└── data/           # HS무역통계 SQLite DB

  ※ semiconductor_value_lab은 stock_dashboard main.py 38~62줄에 sub-app으로 마운트됨
     prefix: /api/semicon-lab (확인 필요)
```

---

## 2. DB 스키마 (stock.db)

| 테이블 | 행수 | 핵심 컬럼 | 용도 |
|--------|------|-----------|------|
| `price_history` | 516만 | stock_code, date, open/high/low/close, volume, inst_net_buy, frn_net_buy, ind_net_buy, **inst_net_buy_amt**, **frn_net_buy_amt**, **ind_net_buy_amt** | 일별 OHLCV + 투자자수급 |
| `stock_universe` | 6693 | stock_code, stock_name, market, sector_large, shares_issued, market_cap, per, pbr, roe, roa | 전 종목 마스터 |
| `financial_data` | 9.2만 | stock_code, year, quarter, revenue, operating_profit, net_income, total_assets, total_equity, eps, bps, is_annual | 재무제표 (per/pbr 컬럼 없음 — stock_universe에 보관) |
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
| `tenbagger_results` | - | run_time, run_type, stock_code, stock_name, total_score, score_detail(JSON), reasons(JSON), ai_analysis, current_price, market_cap, per, pbr, roe, revenue_growth, op_growth, op_margin, inst_net_10d, frn_net_10d, telegram_sent | 텐버거 발굴 결과 |
| `investor_trading_daily` | 0 | bas_dt, stock_code, indv_net, inst_net, frgn_net | ⚠️ 미수집 |
| `foreign_holding_daily` | 0 | bas_dt, stock_code, frgn_hold_pct | ⚠️ 미수집 |
| `radar_semiconductor_override` | 148 | sort_order, lv0, lv1, lv2, company_name, ticker, country_raw, country_flag, lv2_investment_view, company_insight | 시장 레이더 반도체 기업 목록 |
| `radar_sector_override` | 409 | sort_order, lv0, lv1, lv2, company_name, ticker, country_raw, country_flag, lv2_investment_view, company_insight | 시장 레이더 기타 섹터 기업 목록 |
| `radar_market_cache` | 192 | ticker, market_cap, per, pbr, updated_at | 시장 레이더 시총/PBR/PER 캐시 |
| `radar_price_cache` | - | ticker, rn, close, trade_date | 해외 주식 가격 이력 캐시 (yfinance 2년치) |
| `sector_posts` | - | id, title, blog_url, post_date, ai_summary, telegram_sent, created_at | [신규] 섹터 팔로우업 블로그 포스트 |
| `sector_stocks` | - | id, post_id, category(lv1), stock_name(lv2), stock_code, ref_price, memo | [신규] 섹터별 종목 매핑 |
| `short_rank_daily` | 5527+ | bas_dt, isin_cd, stock_code, stock_name, lnb_ccl_stck_cnt, rcal_rdpt_stck_cnt, rdpt_stck_cnt, lnb_rman_stck_cnt, lnb_bal, lnb_scrt_dcd | ★2026-05-03 대차종목순위 (금융위 V2) |
| `short_monthly_stat` | 200+ | bas_dt, lnb_tl_tr_ta, lnb_tl_tr_tt, lnb_rman_stck_ba, lnb_rman_stck_ba_rto | ★2026-05-03 월별대차 집계 |
| `short_foreign_balance` | 200+ | bas_dt, domst_stck_lnb_cnt, frgn_stck_lnb_cnt, domst_stck_lnb_ba, frgn_stck_lnb_ba | ★2026-05-03 내외국인 대차잔고 비교 |
| `short_foreign_trade` | 4500+ | bas_dt, domst_lnb_tt, frgn_lnb_tt, domst_rdpt_tt, frgn_rdpt_tt | ★2026-05-03 내외국인 대차거래량 (일별) |
| `stockeasy_analysis` | - | strategy, analyzed_at, holdings_cnt, exits_cnt, analysis_text, holdings_json, exits_json | ★2026-05-04 스탁이지 전략 일별 분석 누적 |
| `dart_contracts` | 38+ | rcept_no(UNIQUE), stock_code, stock_name, disclosed_at(YYYYMMDD), report_nm, contract_amount, contract_unit, contract_amount_krw, revenue_base, contract_ratio_pct, counterparty, counterparty_country, is_overseas(0/1), contract_start, contract_end, contract_type, ai_score(0~100), ai_summary, ai_signal(강한매수/매수/관망/주의), signal_strength(1~5), telegram_sent, raw_text, created_at | ★2026-05-04 DART 단일판매·공급계약 공시 + AI시그널 |

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
```

### routes/signals.py → /api/signals
```
GET  /market             # 시장 시그널 (캐시키: 'market', TTL 1800초)
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
GET  /v10-earnings-explosion  # V10 이익폭발 발굴 (캐시키: 'v10_earnings', TTL 4시간) ★2026-05-04
GET  /v11-turnaround          # V11 흑자전환 발굴 (캐시키: 'v11_turnaround', TTL 4시간) ★2026-05-04
GET  /v12-sector-megatrend    # V12 섹터대세 발굴 (캐시키: 'v12_sector', TTL 2시간) ★2026-05-04
```

### routes/trend.py → /api/trend (가상매매)
```
GET    /holdings         # 보유종목 (현재가: price_history 최신 close)
POST   /buy              # 매수
POST   /sell             # 매도
POST   /update           # 현재가/수익률 업데이트
GET    /trades           # 거래내역
GET    /summary          # 요약 (승률, 수익)
GET    /ai-holdings      # AI 자동매매 보유
POST   /ai-combo/execute # AI 자동매매 즉시 실행
DELETE /trades/all       # 전체 삭제
```

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

### routes/dart_contracts.py → /api/dart-contracts ★신규(2026-05-04)
```
GET  /list               # 수주공시 목록 (params: days=30, min_signal=1, signal_type, contract_type, is_overseas, limit=50)
GET  /stats              # 시그널별/강도별 통계 요약
GET  /signals            # 전략 연계 시그널 (stock_code별 dict, V8/V11 통합용)
GET  /{rcept_no}         # 공시 상세 + 주가차트 + 기관수급 + HS수출트렌드
POST /refresh            # 즉시 수집 (당일 공시 재스캔, 백그라운드, params: days=1, min_signal=2)
POST /backfill           # 과거 N일 백필 (params: days=30, 텔레그램 발송 없음)
```
시그널 강도 기준:
★5 = 해외 + 매출대비≥30% + AI점수≥80
★4 = 매출대비≥20% OR (해외 + ≥10%)
★3 = 해외 + AI≥60 OR 매출대비≥10%
★2 = AI점수≥50 OR 해외계약
★1 = 그 외
ai_signal: 강한매수(★4+해외)/매수(★3+)/관망(★2)/주의(★1 또는 계약해지)

### routes/tenbagger.py → /api/tenbagger ★신규(2026-04)
```
GET  /results            # 최신 발굴 회차 결과 (limit=20)
GET  /history            # 발굴 이력 회차 목록 (limit=30)
GET  /run-history        # 특정 run_time 상세 (param: run_time)
POST /run                # 수동 발굴 실행 (백그라운드)
GET  /status             # 마지막 실행 상태
```

### routes/market_indicators.py → /api/market-indicators ★신규(2026-04)
```
GET  /investor-top       # 투자자별 순매수 상위 (params: date, limit=20)
GET  /turnover-top       # 회전율 상위 (params: date, market=ALL, limit=20)
GET  /investor-trend     # 수급 추이 차트 (params: market=kospi, days=60)
GET  /market-summary     # KOSPI/KOSDAQ 요약 + 오늘 수급
GET  /index-investor     # 지수 투자자 일별 (params: days=20)
GET  /available-dates    # 수급 데이터 있는 영업일 목록
GET  /short-dates        # 대차종목순위 수집 날짜 목록 ★2026-05-03
GET  /short-rank         # 대차종목순위 (params: date, limit=50, sort_by) ★2026-05-03
GET  /short-history      # 종목별 대차잔고 추이 (params: code/name, days=60) ★2026-05-03
GET  /short-foreign      # 내외국인 대차잔고+거래량 (params: days=120) ★2026-05-03
GET  /short-monthly      # 월별 대차 집계 (params: months=24) ★2026-05-03
```

### routes/market_radar.py → /api/market-radar ★신규(2026-04)
```
GET  /sector/{sector}/detail  # 섹터 세부 (sections, 기간별 주가변동, 시그널)
GET  /all                     # 전체 섹터 시그널 요약
POST /init-semiconductor      # 반도체 기업 목록 DB 초기화 (최초 1회)
POST /refresh-cache           # yfinance로 해외주식 가격 2년치 + 시총/PBR/PER 갱신 (백그라운드)
GET  /export-csv              # 섹터 종목 CSV 내보내기 (?sector=semiconductor)
POST /import-csv              # CSV 업로드 → DB Upsert + 신규 ticker 가격캐시 갱신 (form: file, sector)
```
### Sector Define API → /api/sector-define ★신규(2026-04)
```
GET  /posts                   # 블로그 포스트 목록
GET  /post/{post_id}          # 포스트 상세 + 섹션별 종목 + 실시간 시세 보완
POST /parse                   # 블로그 즉시 파싱 (백그라운드)
POST /init                    # DB 테이블 초기화
```
섹터 키: semiconductor / battery / power_infra / nuclear / pharma / defense / construction / shipbuilding / shipping / automotive / energy / steel / it_hardware / telecom / finance
DB lv0 매핑: power_infra→전력산업, defense→K-방산, shipbuilding→조선/해양, nuclear→원자력, construction→산업재/건설, shipping→해운

### routes/tenbagger.py → /api/tenbagger ★신규(2026-04)
```
POST /run                     # 텐버거 발굴 실행 (백그라운드)
GET  /latest                  # 최신 발굴 결과
GET  /history                 # 회차별 목록
GET  /run/{run_time}          # 특정 회차 상세
DELETE /run/{run_time}        # 회차 삭제
```

### ETF_check/routes_etf.py → /api/etf-check ★2026-05 신규
```
GET  /holdings           # ETF 보유 종목 + 현재가
GET  /summary            # ETF 전체 요약 (평가금액, 수익률)
GET  /refresh            # 보유 종목 현재가 갱신
```
(sys.path.append로 ETF_check/ 경로 추가, etf_check.db 사용)

### employment_monitor/routes_employment_v2.py → /api/employment-v2 ★2026-04 신규
```
GET  /yearly             # 근로복지공단 WLB 피보험자 랭킹 (sort_by=count|workplace|name)
GET  /trend              # WLB 피보험자 전체 목록 (sort_by=workers|1m|3m|6m|1y, limit=500)
                         # 응답: total_workers/workplace_cnt/wlb_diff_1m/3m/6m/1y + has_wlb/has_nps
GET  /chart              # NPS 차트 (쿼리: query=종목명, 현재 데이터 없음)
GET  /insurance          # employment_company 상시인원 목록 (sort_by=count|name)
GET  /insurance/chart    # 기업별 employment_company 월별 추이 (code=종목코드)
GET  /annual-trend       # 기업명 검색 → 연간 고용인원 히스토리 (q=기업명, 2023~2025)  ★신규
GET  /annual-top         # 전체 기업 연간 랭킹 (limit, sort_by=latest|growth|name)       ★신규
```
(sys.path.append로 employment_monitor/ 경로 추가, employment.db 사용)

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
POST   /run              # 백테스트 실행 (V4 AI콤보 기본)
POST   /run-v1           # V1 트렌드 백테스트 (MA정배열+RSI+거래량) ★2026-05-04 신규
POST   /run-v1-dart      # V1+DART 백테스트 (V1 + 수주공시 90일필터) ★2026-05-04 신규
POST   /run-v8           # V8 수출선행 백테스트 (HS무역통계+NPS고용) ★2026-05-04 신규
POST   /run-v10          # V10 이익폭발 백테스트 ★2026-05-04 신규
POST   /run-v10-hs       # V10+HS 수출필터 백테스트 ★2026-05-04 신규
POST   /run-v11          # V11 흑자전환 백테스트 ★2026-05-04 신규
POST   /run-v11-hs       # V11+HS 수출필터 백테스트 ★2026-05-04 신규
POST   /run-v12          # V12 섹터대세 백테스트 ★2026-05-04 신규
GET    /list             # 결과 목록
GET    /matrix           # 전략×기간 비교 매트릭스 (V1~V12 × 6기간) ★2026-05 신규
GET    /{run_id}         # 결과 상세
DELETE /{run_id}         # 삭제
```

#### 백테스트 결과 요약 — 7전략 × 5기간 (★2026-05-04 최신화, 사용자 지정 기간)
> 한국 시장 사이클 5구간. 전체 결과: logic_reference.md 참조.

| 전략 | 20.3~21.11(코로나회복) | 21.12~22.10(고점하락) | 22.11~23.10(회복) | 23.11~24.12(AI반도체) | 24.6~25.5(최근) |
|------|---------|---------|---------|---------|---------|
| V트렌드(MA정배열) | 41.9%/-11% | **-23.5%/-22%** | 20.9%/-7% | **+82.5%/-6%** | 5.8%/-13% |
| V11 (흑자전환) | 36.6%/-12% | **+75.7%/-23%** | -6.2%/-23% | 13.4%/-17% | 8.6%/-21% |
| V10 (이익폭발) | 44.9%/-7% | -18.1%/-17% | -8.0%/-15% | 11.5%/-10% | 11.9%/-15% |
| V8 (수출선행) | 25.0%/-9% | -11.5%/-14% | 7.8%/-14% | 4.3%/-13% | 5.1%/-15% |
| V트렌드+DART | -2.0%/-5% | 9.1%/-5% | 26.4%/-10% | 18.5%/-5% | -1.0%/-4% |
| V12 (섹터대세) | 23.4%/-14% | -6.1%/-6% | -4.4%/-20% | 18.4%/-19% | ⚠️-32.7%/-49% |
| AI콤보(combo) | **58.1%/-7%** | -27.5%/-26% | **42.6%/-7%** | 30.1%/-9% | 23.1%/-9% |

※ 기간 정의: ①코로나회복 2020-03~2021-11 ②고점하락 2021-12~2022-10 ③회복 2022-11~2023-10 ④AI/반도체 2023-11~2024-12 ⑤최근(중복) 2024-06~2025-05
※ V11 시장필터 없음: 흑자전환은 하락장에서도 매수 (`use_market_filter=False`) — 고점하락 기간 +75.7% 최고
※ AI콤보: 코로나회복·회복 기간 최강. V트렌드: AI/반도체 랠리 최강(+82.5%)
※ ROUTES: PERIOD_LABELS / CORE_PERIODS → routes/backtest.py 322~336줄

### routes/ingest.py → /api/ingest
```
POST /fundamentals       # 재무 데이터 저장
POST /market-price       # 시장가 저장 (장중만)
POST /sectors            # 섹터 저장
POST /investor-trends    # 투자자 동향 저장
```

---

## 4. 스케줄러 (scheduler.py)

| 잡 | 시간 | 설명 |
|----|------|------|
| `_job_nightly_batch` | 00:10 daily | Yahoo/KIS/공공데이터 수집 |
| `_job_monthly_bulk` | 매월 1일 03:00 | stock_universe 전체 갱신 |
| `_job_disclosure_check` | 03:30 daily | DART 공시 확인 |
| `_job_intraday_prices` | 매 1분 (장중) | KIS 현재가 수집 → price_history |
| `_job_intraday_investor` | 매 5분 (장중) | KIS 수급 수집 → price_history |
| `_job_closing` | 15:40 daily | 종가 확정 + portfolio_snapshot |
| `_job_screener_precompute` | 매 30분 | 시그널 캐시 갱신 |
| `_job_krx_daily` | 18:00 daily (영업일) | KRX API 전종목 OHLCV + 지수 수집 (KRX 데이터 확정 시간 고려) |
| `_job_supply_daily` | 17:30 daily (영업일) | KIS 전종목 최근 30일 수급 누락분 보완 |
| `_job_tenbagger` (morning) | 09:00 daily (영업일) | 텐버거 후보 발굴 + OpenAI 분석 + 텔레그램 |
| `_job_tenbagger` (noon)    | 12:00 daily (영업일) | 오전 수급 반영 텐버거 발굴 |
| `_job_tenbagger` (afternoon)| 15:00 daily (영업일) | 장 종료 전 최종 텐버거 발굴 |
| `_job_supply_21` | 21:00 daily | KIS 전종목 수급 2차 보완 (장 마감 후 확정치) |
| `_job_radar_prices` | 22:00 daily | yfinance 해외 주식 가격 radar_price_cache 갱신 |
| ~~`_job_nps_daily`~~ | ~~06:00 daily~~ | ⛔ 비활성 — apis.data.go.kr DNS 차단 + 연간 총인원은 월별 차이 없어 무의미 |
| `_job_sector_blog` | 07:00 daily | 네이버 블로그 HOT섹터 신규 포스트 파싱 |
| `_job_stockeasy_analysis` | 16:30 daily | ★스탁이지 3전략 역추론 분석 + 텔레그램 리포트 |
| `_job_stockeasy_weekly` | 일요일 09:00 | ★스탁이지 주간 전략 패턴 요약 리포트 |
| ~~`_job_insurance_monthly`~~ | ~~매월 5일 02:00~~ | ⛔ 비활성 — 연간 총인원 수집. 월별 차이 없어 신호 의미 없음 (사용자 요청 제거) |
| `_job_dart_contracts` | 08:00/13:00/17:00 daily | ★DART 수주·공급계약 공시 수집 + AI분석 + 텔레그램 ★2026-05-04 신규 |
| `ETF_check/scheduler.py` (별도 프로세스) | **20:30 daily (영업일)** | ★ETF 포지션 현황 수집 (etfcheck.co.kr 스크래핑, playwright). PID 별도. 공휴일 자동 스킵. ★2026-05-04 00:10→20:30 수정 |

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

모든 컴포넌트가 `frontend/src/App.jsx` 단일 파일 (~9366줄).
외부 분리 컴포넌트: `EmploymentYearlyView.jsx`, `EtfCheckView.jsx`, `NpsTrendView.jsx` (별도 파일).

### 컴포넌트 → 탭 키 → 시작 줄번호
| 컴포넌트 | 탭 키 | 줄번호 |
|---------|-------|--------|
| `SectorFollowupView` | hot_sector | 649 |
| `MarketRadarView` | market_radar | 912 |
| `BuyCandidateView` | buy_candidates | 392 |
| `WatchlistView` | watchlist | 732 |
| `MacroDashboard` | macro | 1228 |
| `StockAnalysis` | analysis | 1678 |
| `Screener` | screener | 2432 |
| `PeakView` | trend | 3592 |
| `PortfolioView` | portfolio | 4056 |
| `TradeAnalysis2` | hs_trade2 | 4869 |
| `SectorReports` | reports | 5748 |
| `SignalSettings` | (settings 내부) | 5851 |
| `AIInsight` | insight | 6031 |
| `BacktestView` | backtest | 6084 |
| `SettingsView` | settings | 6413 |
| `TelegramMentions` | telegram | 6708 |
| `MarketIndicatorsView` | market_indicators | 6978 |
| `TenbaggerView` | tenbagger | 7024 |
| `DartContractView` | dart_contracts | ~8529 (MegatrendView 직전) |
| `MegatrendView` | megatrend | ~8729 |
| `SystemStatus` | system | ~8795 |
| `EmploymentView` | employment | 8842 |
| `EtfCheckView` | etf_check | 외부 파일 (`frontend/src/EtfCheckView.jsx`) |

### 네비게이션 구조
```
NAV_ITEMS 정의: 9152줄
렌더 스위치:    ~9283줄

순서: macro → market_indicators → market_radar → analysis → semiconductor_sector → hot_sector
    → screener → tenbagger → megatrend → trend → reports → telegram → backtest → hs_trade2 → employment → etf_check
    ── (구분선) ──
    buy_candidates → portfolio
    ── (구분선) ──
    settings → system → watchlist
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

---

## 9. 알려진 이슈 & 제한사항

| 항목 | 상태 | 내용 |
|------|------|------|
| HS trade_series_cache 정기 동기화 | ⚠️ 수동 | 매월 1회 `cd hs_trade_lab && python3 scripts/backfill_trade_series_cache.py` 실행 필요. customs_monthly_record 130만건 → trade_series_cache 18,119건(285종목). scheduler.py에 자동화 미등록. |
| DART 5년치 백필 | 🔄 진행중 | PID 77610, `/tmp/dart_backfill_2022.log`. 2022-01-01~2026-05-04, 53청크 × 30일. 완료 시 ~1,500건+ 예상. 완료 후 V10/V11 보너스 점수 자동 강화. (이전 PID 76118: 청크 12부터 supply_daily 잡과 DB락 충돌로 실패 → 재시작. _save_contract에 retry 로직 추가됨) |
| NPS 고용 데이터 품질 | ⚠️ 주의 | `employment_company` 테이블: 2023-12·2025-12는 사업보고서 기반(직접고용), 2026-05는 NPS API 기반(자회사 포함). **2026-05 데이터와 이전 연도 비교 금지** — `_load_nps_employment_bonus_map()`은 `2025-12 vs 2023-12`만 비교. 2026-05를 사용하면 한화손해보험+1169% 같은 거짓 급등 발생. |
| NPS 보너스 제거 | ✅ 제거 | 연간 총인원(employment_company)은 월별 차이값이 없어 선행 신호로 무의미. `_load_nps_employment_bonus_map()` 함수는 보존하나 V1/V10/V11/가치주/텐버거에서 모두 제거. 월별 신규취득/상실 데이터(nps_workplace_monthly)는 apis.data.go.kr DNS 차단으로 수집 불가 → 테이블 삭제. |
| 수급현황 amt 누락 버그 | ✅ 수정 | `_job_supply_daily()` UPDATE WHERE 조건이 `inst_net_buy=0`(수량) 기준이었음 → 장중 intraday 잡이 수량(qty)을 먼저 기록하면 daily 잡이 skip → `inst_net_buy_amt`(금액) 미기록. 수정: `inst_net_buy_amt=0` 기준으로 변경. |
| KOSPI200 (^KS200) 누락 | ✅ Naver fallback | KRX승인 API가 서브인덱스를 간헐적으로 0건 반환. `_fetch_naver_index('KPI200')` + `_collect_derivative_indices()` 추가 → KRX 실패 시 Naver Finance fchart로 자동 수집. |
| KOSDAQ150 (^KQ150) 누락 | ⚠️ 미해결 | Naver Finance `KSQ150` 심볼이 `<protocol />` 빈 XML 반환 (미지원). KRX승인 API 간헐적 0건. 안정적 수집처 없음. 현재로서는 수집 누락 허용 상태. |
| 공시정보 DART 쿼터 | ✅ DB fallback | DART API `status:020` 쿼터 초과 시 `dart_disclosure_cache` DB에서 최근 데이터 제공. 메모리 → DART API → DB 3계층 우선순위. |
| DART 역사데이터 금액 파싱 | ⚠️ 제한 | 2021~2022년 DART 문서는 HTML 구조가 달라 `_extract_amounts()`가 금액 0건 파싱. ★2 이상 신호는 해외계약 감지(is_overseas)로만 발생(2021년 63건, 2022년 15건). **보너스 맵은 최근 90일만 참조하므로 실운영에 영향 없음.** 향후 역사 데이터 품질 개선 시 regex 패턴 업데이트 필요. |
| KRX 데이터포털 | ❌ 차단 | POST→LOGOUT, 자동화 IP 차단 |
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
| 개별종목 PBR/PER 지연 | ✅ 개선 | 5초 후 재시도 로직 추가 (App.jsx) |
| 시그널 계산 10초 지연 | ✅ 개선 | 서버 시작 시 warm-up + stale-while-revalidate |
| 재무제표 단위 오류 | ✅ 완전수정 | op_profit 597건·net_income 20건·equity 5건 억원→원 변환, Q4 254건 재계산, CFS/OFS 혼용 36건 재수집, 지주사 Q4 NULL 10건 처리, 수집오류 삭제 2건 |
| financial_data 백업 | ℹ️ 보관 | `financial_data_backup_20260412` 테이블로 수정 전 원본 보관 |
| 재무제표 Q4 대규모 손실 | ℹ️ 정상 | 잔존 14건(삼성SDI2016/현대건설2024/대한항공 등)은 실제 이벤트 손실로 수학적 정확값 |
| KRX 지수 서브인덱스 덮어쓰기 | ✅ 수정됨 | scheduler.py·collect_krx_history.py에서 `key in idx_nm` 방식이 "코스닥 기술성장기업부"(10497)·"코스피 중형주"(5014)로 ^KQ11/^KS11을 덮어쓰던 버그 → INDEX_EXACT_MAP 정확한 이름 매칭으로 수정. 과거 데이터 60일치 재수정 완료 |
| KOSDAQ150 지수 | ✅ 수집 시작 | ^KQ150 (Yahoo 없음). KRX API "코스닥 150" 행 → ^KQ150으로 저장 (scheduler.py·collect_krx_history.py). processor.py·main.py에 KOSDAQ150 추가. 종합현황 파생지수 우측 패널에 표시 |
| 대차잔고 V1 API | ⚠️ DNS 차단 | `apis.data.go.kr` V1 getStocLendBorrInfo가 이 서버에서 DNS 차단. V2(`GetStocLendBorrInfoService_V2`)로 전환했으나 동일 도메인. ⚠️ 같은 도메인이므로 차단 시 V2도 실패할 수 있음 |
| 대차잔고 V2 우선수집 | ✅ 전환 | `collectors/public_data.py::fetch_short_sell_v2()` 추가. V2는 날짜 필터 미지원 — 마지막 페이지 역순 탐색으로 최신 날짜 수집. V1 fallback 유지. `short_sell_daily` 000000 bogus rows 18건 삭제. scheduler.py gap detection에 `WHERE stock_code!='000000'` 추가 |
| HOT 섹터 블로그 파서 | ✅ 개선 | `blog_parser.py`: categoryNo 49→7 수정, CSS→regex logNo 추출, `parse_blog_post_local()` OpenAI fallback 추가. 93개 종목 추출(18개 포스트). OpenAI key 없어도 종목 표시 가능 |
| V8 KOSPI 시장필터 공백 | ⚠️ 구조적 한계 | price_history에 ^KS11이 2019~2021 3년치 누락 → V8/V10/V11 백테스트의 `market_bullish` 필터가 해당 기간 중 True(매수허용)로 기본값 처리됨. 전략 간 동일하게 적용되어 비교 일관성은 유지. |
| V8 유니버스 협소 | ⚠️ 구조적 한계 | hs_code_company_map에 매핑된 종목 94개만 V8 백테스트 대상. 대부분 대형 수출주(삼성전자·현대차 등)로 시장이 이미 선반영. 미드캡 수출주 확대 시 알파 개선 여지 있음. |

---

## 10. 자주 수정하는 작업별 파일 가이드

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
| 2026-05-05(2) | **[고용정보 탭 전면 개편 — 피보험자 기간 대비 컬럼 + 연간 추이 차트]** ①`routes_employment_v2.py`: `get_trend_data()`에 WLB 전체 월 로딩(`wlb_by_ym`) + `wlb_diff_1m/3m/6m/1y` 증감 필드 추가. `get_nps_trend()` `sort_by=1m/3m/6m/1y` WLB diff 기준 정렬 분기 추가. `/annual-trend?q=` 신규(기업명 검색→연간 history). `/annual-top?sort_by=` 신규(전체 기업 연간 랭킹, 2023/2024/2025 3년 비교, diff_1y/2y 포함). ②`NpsTrendView.jsx` 재작성: 기간 탭 5개(피보험자수/1개월/3개월/6개월/1년) 클릭 시 서버에서 해당 기준 정렬 재요청 + 선택 기간 컬럼 강조. 나머지 기간 컬럼 참고용 표시. 데이터 없을 때 "매월 수집 누적 중" 안내. ③`App.jsx` 📈 국민연금 월별 차트 탭 완전 대체 → 연간 고용인원 추이 탭: 기업 검색(annual-trend) + 1/2/3년 선택 + Area 선그래프, 전체 랭킹 테이블(annual-top, 행 클릭 시 차트). 빌드 완료. |
| 2026-05-05 | **[주요지표 선물정보 복구 + 변화율 소수점 개선]** ①`processor.py::get_macro_status()` index 루프에 `("^KS200","KOSPI200")`, `("^KQ150","KOSDAQ150")` 추가 — KOSPI200/KOSDAQ150이 API 응답에 포함되지 않던 근본 버그 수정. ②App.jsx MacroDashboard: NASDAQ/S&P500, KOSPI200/KOSDAQ150, KOSPI/KOSDAQ 변화율 표시 로직에 `Math.abs(chgPct)<0.1`이면 `toFixed(2)` 사용(기존 항상 toFixed(1)→0.0% 오표시 방지). 예: 0.04%→"0.04%"로 표시. 빌드 완료. |
| 2026-05-04(9) | **[고용정보 탭 WLB 피보험자 현황으로 전환 + 버그 수정 다수]** ①`routes_employment_v2.py::get_trend_data()`: `res.append()`에 `total_workers`/`workplace_cnt`/`wlb_data_ym` WLB 필드 추가. `get_nps_trend()`: `sort_by=workers` 신규 기본값 — wlb_monthly 피보험자 인원순 정렬. NPS diff 없을 때 WLB 전체 제공 fallback. 응답에 `wlb_data_ym`/`has_nps`/`has_wlb` 메타 추가. ②`NpsTrendView.jsx` 전면 재작성: 피보험자(명)/사업장 수 주 컬럼, 국민연금 순증가는 토글 옵션, 검색/정렬 버튼 UI, WLB 기준 월 헤더 표시, 삼성전자 190,607명 정상 표시. ③`App.jsx::EmploymentView`: 탭 레이블 변경(연도별→🏭 고용보험 랭킹(WLB), 월별고용변동→📈 국민연금 월별차트, 인원증가→👥 기업별 피보험자 현황), 하드코딩 "2026년 2월 2,194개 사업장" 문구 제거. ④`scheduler.py`: `_job_supply_daily()` UPDATE 조건 버그 수정 — `inst_net_buy=0` 체크에서 `inst_net_buy_amt=0` 체크로 변경(장중 qty만 기록된 row가 amt 업데이트 skip 되던 문제). ⑤`scheduler.py::_startup_catchup()` 신규: 서버시작 30초 후 KOSPI200/KOSDAQ150 누락 감지→Naver Finance fchart 수집, inst_net_buy_amt 50행 미만 감지→supply_daily 즉시 재실행. ⑥`scheduler.py::_fetch_naver_index()` + `_collect_derivative_indices()` 신규: KPI200→^KS200 Naver fchart 수집(KOSDAQ150은 KSQ150 응답이 빈 XML로 미수집). ⑦`main.py` 공시 엔드포인트: DB 3계층 캐시 추가(메모리→DART API→dart_disclosure_cache DB fallback). 빌드 완료. |
| 2026-05-04(8) | **[시스템 상태 페이지 전면 개편 + 데이터 최신성 카드]** `main.py::get_stats()`: `data_freshness` 객체 추가(kr_price_latest/hs_confirmed_latest/hs_estimated_latest/wlb_collected_at/wlb_data_ym/us_price_latest/us_stock_count). App.jsx SystemStatus: ①DATA_SOURCES 테이블에 `주가(미국)` 행 신규, `고용/연금` NPS→WLB 근로복지공단 변경(매일20:30 자동), `수출입(확정)`/`수출입(추정)` 2행으로 분리, `최신 기준` 열 추가(노란색 freshness 표시). ②`데이터 최신성` 카드 섹션 신규: 한국주가/미국주가/수출확정/수출추정/WLB수집일/WLB기준월 6개 카드. 프론트엔드 빌드 완료. |
| 2026-05-04(7) | **[백테스트 전략명 체계화]** `routes/backtest.py`: STRATEGY_LABELS(v5/v12 추가, v2→재무성장, v3→추세단독, v4→복합콤보, v6→추세+재무, v7→가치+모멘텀, v_trend→VT MA정배열, v_dart→VT+DART필터), ALL_STRATEGIES 순서 v1~v12→VT→combo 순번제. App.jsx BacktestView 드롭다운+카드 V1~V12 순 재정렬. |
| 2026-05-04(7) | **[근로복지공단 고용보험 수집 + 고용정보 페이지 전면 개편]** `employment_monitor/collect_labor_welfare.py`: 3,400만건 스캔 8스레드, 2,275개 상장사 매칭, 삼성전자 190,607명. DB: `wlb_monthly`+`wlb_meta`. `scheduler.py::_loop_wlb_monthly`: 매일 20:30 totalCount 변화 감지 → 변화 시 수집. `routes_employment_v2.py::get_yearly_employment`: wlb_monthly 전환. `EmploymentYearlyView.jsx`: 피보험자수/사업장수 랭킹+검색+요약카드로 재작성. 날짜 '2026-04-30' hardcoding 제거 → DB MAX(fetched_at) 동적 표시. US DB 점검: 516개 종목 정상. |
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
| 2026-04-26 | `tenbagger_engine.py` 신규 (6축 스코어링+OpenAI분석+텔레그램), `routes/tenbagger.py` 신규 (5개 API), `TenbaggerView` 컴포넌트 추가 (App.jsx 7024줄), NAV에 텐버거헌터 메뉴 추가, scheduler.py에 09:00/12:00/15:00 발굴 잡 추가, `tenbagger_results` DB 테이블 자동 생성 |
| 2026-04-28 | NASDAQ/S&P500 갱신 누락 버그 수정(main.py): KOSPI guard가 US 지수까지 스킵하던 버그 → us_only=True 분기로 수정. 가상매매 수익률% 추가, AI 로직 v1/v2 라벨 정비, localStorage 캐시로 접속 즉시 표시, 개별종목 애널리스트 보고서 복구, 섹터보고서 종목 링크 추가 |
| 2026-04-29 | git pull 8개 커밋 적용 (오전 수정사항 반영). radar_semiconductor_override 테이블에 lv2_investment_view/company_insight 컬럼 누락 버그 수정(ALTER TABLE). NASDAQ/S&P500/DOW 4/24이후 누락분 yfinance로 직접 보완. /Applications/sector_radar/ 및 hs_trade_lab 외부 프로젝트 CLAUDE.md 섹션1 기록. radar_price_cache / market_radar API 엔드포인트 섹션3 기록 |
| 2026-04-29 | **[데이터 수정]** ^KS11 1453건·^KQ11 1017건 Yahoo Finance로 재수정(KRX 서브인덱스 덮어쓰기 피해). 수급추이 차트 oscillation 해소. collect_kis_supply_history.py 재시작(PID 29278, 4/29 수급 수집중) |
| 2026-04-29 | 메뉴명 변경(종합현황→주요지표, 시장지표→수급현황, 시장레이더→섹터지표, AI종목→AI종목발굴, 텐버거헌터→텐버거헌터, 이모지제거), 관심종목 시스템상태 아래 이동. `routes/employment.py` 신규(3개 API, employment_monitor/employment.db 연결). `EmploymentView` 컴포넌트 추가(연도별고용+NPS월별변동). AI콤보 매수가 당일종가우선 수정. 가상매매 SummaryCards 1줄 유지 수정 |
| 2026-04-30 | **[버그수정]** `hs_trade_lab/semiconductor_value_lab/fastapi_app.py`: `load_latest_stock_rows`가 `sqlite3.Row` 반환→`dict` 변환 누락으로 `.get()` AttributeError 발생→`dict(row)` 변환으로 수정. 반도체섹터/관심종목 전종목 149개 정상 표시됨. Hot섹터 안내메시지 개선(OpenAI API key 필요 안내). |
| 2026-04-30 | 현금흐름표 수집 대상 코스피/코스닥 보통주로 제한: `collect_dart_cashflow_batch.py`가 `stock_meta`/`stock_universe` 시장구분을 기준으로 KOSPI/KOSDAQ만 수집하고, 월간 스케줄은 `--fill-missing --years 5`로 부분 누락도 보강. 섹터보고서 정리: 개별종목 보고서는 `stock_code`를 채워 종목 탭으로 이동하고, 섹터 보고서 API는 순수 섹터 보고서만 노출. 같은 날짜+파일크기 중복 보고서 제거 스크립트 추가. |
| 2026-04-29 | `Sector_define/routes_sector.py` → `routes/sector_define.py` 복사+등록(/api/sector-define). `SectorFollowupView` 컴포넌트 App.jsx에 통합(MarketRadarView 위). 메뉴 "Hot 섹터" 추가(반도체섹터 아래). sector_posts/sector_stocks 테이블 stock.db에 자동생성. 시장레이더 PriceCell 국가별 통화기호 추가($·¥·NT$·€). 수급현황 fmtAmt 단위 수정(천억→조 통일). 시스템상태 페이지 데이터소스 테이블화(17개 항목). `EmploymentView`/`SectorFollowupView` CLAUDE.md에 영구 기록 |
| 2026-04-29 | 시장 레이더 디자인 전면 개편: Level2 rowspan 그룹화, PriceCell(주가↑%↓ 2행) 형식, Level2 그룹 신호 집계(과반수 방향), PBR/PER 한국종목 stock_universe에서 보완(routes/market_radar.py _fetch_market_map) |
| 2026-04-29 | **[근본버그 수정]** KRX 지수 저장 필터 `key in idx_nm` 방식이 모든 서브인덱스를 ^KQ11/^KS11에 덮어쓰는 버그 수정 → scheduler.py·collect_krx_history.py 모두 정확한 이름 매칭(`INDEX_EXACT_MAP`)으로 변경. 원인: KRX API 응답의 마지막 매칭 행이 "코스닥 기술성장기업부"(10497)·"코스피 중형주"(5014)였음. KOSDAQ150 지수(`^KQ150`)를 별도 코드로 신규 저장 시작. main.py·processor.py에 `^KQ150`→`KOSDAQ150` 추가. 종합현황 파생지수 패널 KOSPI200/KOSDAQ150 2열로 분리. 잘못된 과거 ^KS11/^KQ11 데이터 60일치 KRX API로 재수정. 시장지표 available-dates 필터 cnt>=1→cnt>=20 (수집 중인 날 제외). |
| 2026-05-03 | `ETF_check/routes_etf.py` 신규 (`/api/etf-check`, etf_check.db). `employment_monitor/routes_employment_v2.py` 신규 (`/api/employment-v2`, employment.db). main.py: sys.path.append로 두 서브앱 등록. `EtfCheckView.jsx` 탭 신규, NAV에 ETF 모니터링 추가. `start.sh`/`stop.sh` 서버 관리 스크립트, `launchd/` macOS 서비스 설정, `scripts/` 유틸 스크립트 추가. `.claude/settings.json` 복원(삭제→복원). App.jsx ~9366줄로 증가(+921줄). |
| 이전 세션 | routes/ingest.py, routes/portfolio.py 신규 분리; Yahoo Finance 제거; Trigger20 URL 수정; 야간 알림 억제; 시그널 warm-up 추가; 대차잔고 URL 수정; PBR/PER 재시도 로직 |
| 2026-04-29 | **[신규 기능]** 섹터 팔로우업(Sector Follow-up) 개발: 네이버 블로그 "돈의흐름 팔로잉" 자동 파싱(AI 추출), 텔레그램 알림, 실시간 주가/PBR/PER 연동 테이블 구현. Sector_define 폴더 생성 및 main.py/App.jsx 통합 완료. |
| 2026-05-01 | **[기능 개선]** 고용정보 대시보드 국민연금(NPS) 데이터로 전면 개편: ① 사업보고서 데이터 의존성 100% 제거 ② 연도별 고용인원 → 연도별 순증가로 변경 및 26년 4월 등 년도별 누적 순증가 표시 ③ 월별 고용변동 및 인원증가 기업별 랭킹 탭에 `업데이트: 2026-04-19` 기준일자 헤더 표시 ④ 에이엘티 등 데이터 없는 기업 NPS 순증가 데이터로 강제 차트 표시 ⑤ `fetch_nps_2years.py` 2년치 강제 수집 스크립트 작성 ⑥ `update_nps_daily.py` 일별 신규 데이터 발생 시에만 전체 상장사 자동 업데이트 스크립트 추가 |
| 2026-05-01 | **[근본버그 수정]** `fetch_nps_2years.py` NPS 수집 데이터 저장 안 되던 문제: SQLite `ATTACH DATABASE`로 읽기전용(644) `stock.db`를 연결하면 전체 커넥션이 read-only 전환되어 `employment.db` INSERT도 차단됨. 수정: `stock.db`는 `file:...?mode=ro` URI 별도 읽기전용 커넥션으로 분리, `employment.db`는 독립 쓰기 커넥션으로 분리. ⚠️ **SQLite ATTACH 주의**: 읽기전용 DB를 ATTACH하면 writable DB도 쓰기 불가 → 반드시 별도 커넥션 분리. |
| 2026-05-01 | **[UI 버그 수정]** 주요지표 KOSPI200/KOSDAQ150 파생지수 레이블 중복 표시 제거: `Cell label="KOSPI200"` 과 외부 `<span>📈 KOSPI200</span>` 이중 노출 → `Cell label=""` 으로 수정 (App.jsx 2655, 2659줄) |
| 2026-05-01 | **[데이터 수집 파이프라인 근본 개편]** KRX/data.go.kr 서버 접근 차단으로 인한 지수 미업데이트 문제 전면 수정: ① `data_collector.py::backfill_index_history()` 대상에 **KOSDAQ150(^KQ150), NASDAQ(^IXIC), S&P500(^GSPC)** 추가 ② `collect_macro_data()` 글로벌 지표에 NASDAQ/S&P500 추가 ③ KRX 지수 0건 수집 시 **Yahoo Finance 자동 fallback** (`scheduler.py::_job_krx_daily`) ④ KOSDAQ150은 Yahoo 미지원 → **pykrx 우선, 차단 시 자동 skip** (KRX DNS 차단으로 pykrx도 불가) ⑤ `main.py::_realtime_fetch_macro()`에서 `^KQ150` Yahoo 다운로드 skip + pykrx로 별도 처리 |
| 2026-05-01 | **[네트워크 차단 이슈 기록]** 이 서버에서 외부 접속 불가 도메인 목록 (DNS 실패): `apis.data.go.kr` (금융위원회 공공데이터포털), `data.krx.co.kr` (KRX 웹, pykrx 공매도/대차 API), `data-dbg.krx.co.kr` (KRX 승인 API, 응답 0건). **접속 가능**: Yahoo Finance (yfinance), KIS API (oauth.koreainvestment.com), Naver Finance (finance.naver.com), DART (opendart.fss.or.kr). **영향**: 대차잔고(short_sell_daily), KRX 지수(^KQ150) 수집 불가. **해결 방향**: macOS 네트워크 설정 또는 /etc/hosts에서 차단 도메인 DNS 우회 설정 필요 (`networksetup -setdnsservers Wi-Fi 8.8.8.8 8.8.4.4`). |
| 2026-05-01 | **[스케줄러 개선]** `scheduler.py::_job_public_data()` Gap 자동감지 + 백필 로직 추가: `short_sell_daily` 테이블의 `MAX(bas_dt)` 확인 후 오늘까지 누락된 영업일 목록을 생성해 순차 수집. 실패(0건) 시 즉시 중단하여 불필요한 API 호출 방지. DNS 차단 상황에서는 서버 로그에 경고 메시지 출력. |
| 2026-05-01 | **[근본버그 수정] 공휴일 0% 오버라이트 방지**: backfill_index_history(), _collect_macro_yahoo(), _realtime_fetch_macro()에서 오늘 날짜 copy-to-today 로직 제거. DataCollector._is_kr_trading_day() 추가: 주말·공휴일이면 한국 지수(^KS11,^KQ11,^KS200,^KQ150) DB 저장 차단. ⚠️ _KR_HOLIDAYS set 매년 갱신 필요(data_collector.py line ~340). DB정리: sqlite3 stock.db "DELETE FROM price_history WHERE date>='2026-05-01' AND stock_code IN ('^KS11','^KQ11','^IXIC','^GSPC');" |
| 2026-05-03 | **[성능 최적화]** signal_engine.py: ①`_build_sector_activation_map()` TTL 캐시(3600초) + 전종목 벌크 IN 쿼리(75→1개) ②`calc_value_candidates()` N+1 루프(~2000쿼리) → 벌크 2쿼리+Python Graham IV ③BB width numpy 벡터화(`sliding_window_view`). App.jsx: MacroDashboard 클록 타이머 제거, PeakView 폴링 시장개장 시에만 실행(`isKRMarketOpen()`), sd_macroCache TTL 4시간(장중 1시간) 적용 |
| 2026-05-03 | **[섹터 지표 전면 개편]** MarketRadarView(App.jsx 912줄): ①열 순서 변경(국가→종목명→시총→Level2→신호→가격→PBR/PER) ②Level2 그룹 내 KR vs 해외 분리+파란 구분선 ③Level2별 신호 KR/해외 각각 집계 ④섹션 제목행 음영 처리 ⑤종목명 nowrap+ellipsis+hover tooltip(company_insight) ⑥scroll 없는 fixed-width 테이블 ⑦CSV 다운로드/업로드 버튼. routes/market_radar.py: `GET /export-csv`, `POST /import-csv` 신규 |
| 2026-05-03 | **[섹터 지표 탭 확장 + 정렬 수정]** SECTOR_KEY_MAP/SECTOR_META에 8개 섹터 추가(power_infra→전력산업, defense→K-방산, shipbuilding→조선/해양, nuclear→원자력, construction→산업재/건설, shipping→해운, automotive→자동차, it_hardware→IT/하드웨어, steel→철강/비철금속, telecom→통신/플랫폼, finance→금융/지주). RADAR_SECTORS 프론트 탭 7→15개 확장. 테이블 정렬 해외 주식 먼저 → 한국 주식 순서로 수정(buildGroups/renderGroups lv2 rowspan 시작행 변경). |
| 2026-05-03 | **[섹터 지표 UI 선 스타일 + 데이터]** LV2_BORDER 강화(2px solid rgba(59,130,246,0.85)), KR_OVS_BORDER 가는 점선(1px solid rgba(100,160,255,0.6)), 섹션 제목행 아래 굵은 실선(borderBottom 2px) 추가. `updated_date` 필드 API 응답 추가(radar_price_cache MAX(trade_date)), 테이블 위에 업데이트 날짜 표시. Kioxia 티커 Unlisted→285A.T로 수정, TSE 2년치 가격 다운로드. sector_insights_master.csv로 반도체/전력산업 섹션설명 DB 업데이트(lv2_investment_view). 반도체 `radar_semiconductor_override` 50행 메모리 설명, 98행 장비설명 추가. |
| 2026-05-03 | **[섹터 지표 Sticky 헤더]** MarketRadarView: ①섹터 탭+제목 행 sticky(top:0, zIndex:40, 단일행 overflowX:auto), ②테이블 `<th>` sticky(top:86px, zIndex:20), ③섹션 타이틀 `<td>` sticky(top:120px, zIndex:15). glass-panel `overflowX:'hidden'`→`'clip'`으로 변경(sticky trap 방지). KR_OVS_BORDER 1.5px solid rgba(80,140,255,0.75)로 강화. STICKY_TABS_H=86, STICKY_HEAD_H=120 상수 정의. |
| 2026-05-03 | **[수급추이 데이터 버그 수정]** routes/market_indicators.py `get_investor_trend()`: ^KS11/^KQ11 row(5건만 존재) 대신 개별 종목 price_history JOIN stock_universe.market 집계로 교체. KOSPI 투자자 순매수 정상 데이터 반환. |
| 2026-05-03 | **[KOSPI200/KOSDAQ150 수집 수정]** main.py `_realtime_fetch_macro()`: pykrx(KRX서버 차단) 대신 KRX 승인 API(`idx/kospi_dd_trd`, `idx/kosdaq_dd_trd`)로 ^KS200·^KQ150 수집. 최근 5영업일 백필 실행. |
| 2026-05-03 | **[전 페이지 Sticky 테이블 헤더]** index.css `#main-scroll table thead tr th` sticky CSS 추가(top:0, z-index:10, background opaque). `<div id="main-scroll">` 부여. 모든 glass-panel `overflow:auto/hidden` → `overflow:clip` 일괄 변경(sticky containment 방지). EtfCheckView.jsx thStyle에 직접 sticky 추가. |
| 2026-05-03 | **[주요지표 업데이트 날짜]** MacroDashboard 상단 상태바: "300초 자동 갱신 + KOSPI(날짜) KOSDAQ(날짜) NASDAQ(날짜) S&P500(날짜)" 형식으로 변경. |
| 2026-05-03 | **[Naver 밸류에이션 stock_universe 동기화]** collect_naver_fundamentals.py save() 함수: stock_universe.per/pbr/eps 갱신(financial_data에는 per/pbr 컬럼 없음 — INSERT/UPDATE 제거). scheduler.py `_job_naver_fundamentals` `--missing` 제거(매일 전종목 갱신). |
| 2026-05-03 | **[현금흐름표 전종목 백필]** collect_dart_cashflow_batch.py DB 잠금 retry 로직 추가(지수 백오프 최대 5회). `--missing --years 5` 백그라운드 실행. 완료 후 `--fill-missing --years 5` 실행 권장(lock 충돌로 누락된 분기 보완). |
| 2026-05-03 | **[PBR/PER 개별종목 DB 캐시]** main.py `_get_cached_valuation()`: in-memory miss 시 stock_universe.per/pbr/eps DB fallback 추가. 서버 재시작 후에도 Naver 스크래핑 없이 즉시 반환. collect_naver_fundamentals.py timeout=30 추가, --missing 기준을 stock_universe로 수정. |
| 2026-05-03 | **[섹터 지표 KR/해외 구분 강화]** renderGroups: 해외↔국내 경계에 `🇰🇷 국내` 구분 행(연두 배경) 추가, 국내 주식 행 연한 녹색 배경(`rgba(34,197,94,0.03)`) 적용. |
| 2026-05-03 | **[Sticky 헤더 트랩 근본 수정]** `overflowX:'auto'` 래퍼 div → `overflowX:'auto', overflowY:'clip'`으로 변경. `overflow-y: clip`은 수직 scroll container를 생성하지 않아 sticky 요소가 #main-scroll를 정상 참조. 해당 위치: MarketIndicatorsView(3곳), TenbaggerView(1곳). |
| 2026-05-03 | **[수급현황 공휴일 필터링]** routes/market_indicators.py에 `_KR_HOLIDAYS` 집합 + `_is_kr_trading_day()` 추가. investor-trend/index-investor/available-dates 모두 주말+공휴일 제외. price_history에서 주말 ghost 155건+근로자의날 129건 삭제. |
| 2026-05-03 | **[섹터 지표 테이블 정렬 버그 수정]** renderGroups() lv2 셀 `rowSpan={total}` → `rowSpan={total + (hasKR && hasOvs ? 1 : 0)}`. 구분 행(`🇰🇷 국내`) 삽입 시 rowspan 1 초과로 열 밀림 현상 수정. |
| 2026-05-03 | **[환율 DB 저장 + 섹터 지표 시총 원화 변환]** data_collector.py global_symbols에 JPYKRW=X/TWDKRW=X/EURKRW=X/HKDKRW=X 추가(매일 수집). price_history에 현재값 수동 입력(JPY:9.35, TWD:46.48, EUR:1723.5, HKD:187.35). routes/market_radar.py `_get_fx_rates()` 신규: price_history에서 최신 환율 조회 후 1시간 캐시. `_to_krw()` 신규: 외화 시총→KRW 변환. `_build_stock_item`에 `market_cap_krw` 필드 추가. App.jsx `fmtMktCap` 단순화 → 항상 KRW 표시. |
| 2026-05-03 | **[고용정보 NPS 버그 수정]** `routes_employment_v2.py`: pivot_table `.fillna(0)` 제거 → NaN 유지로 0 오표시 해소. `_trend_data_cache_at` TTL 3600초 추가. `/trend` 엔드포인트 필터 강화(`_has_val` 함수). `NpsTrendView.jsx` 기본 정렬 `diff_1m`→`diff_0m`(데이터가 있는 202602 기준). |
| 2026-05-03 | **[NPS 자동 업데이트 스케줄러 추가]** `scheduler.py`에 `_loop_nps_daily`/`_job_nps_daily` 추가: 매일 06:00 `update_nps_daily.py` 실행. 완료 후 `_trend_data_cache` 자동 무효화. 스레드 목록에 "NPS고용업데이트" 등록. |
| 2026-05-03 | **[HOT 섹터 4월 데이터 stock.db 입력]** `Sector_define/april_data.json` 18개 포스트를 `stock.db`의 `sector_posts` 테이블에 삽입. `SectorFollowupView`(App.jsx 649줄)는 `/api/sector-define/posts`로 정상 조회됨. HOT 섹터 페이지에 2026-04-01~04-26 포스트 18건 표시. |
| 2026-05-03 | **[알려진 이슈 — NPS 2년치 미수집]** `nps_workplace_monthly`에 202602 2,194건만 존재. 원인: ① 2026-04-19 회사명 기반 수집으로 202602 수집 ② `fetch_nps_2years.py` (구버전)는 `stock_bizno_map` 29개 기업만 대상 → 0건 수집 ③ 현재 NPS API(`apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService`) HTTP 500 반환 중 (서버 장애 또는 서비스키 미등록). 수정: `fetch_nps_2years.py` 완전 재작성 — 202602에 있는 2194개 기업의 `wkpl_nm`으로 24개월 백필, `--dry-run`으로 API 연결 테스트 가능. API 복구 시 `python3 employment_monitor/fetch_nps_2years.py` 실행. |
| 2026-05-03 | **[HOT 섹터 UI+파서 개선]** App.jsx SectorFollowupView: 탭 X(삭제)버튼 제거, 날짜 제거 + 핵심 명칭 14자만 표시(hover=전체), 종목 없을 때 안내 메시지+OpenAI 설정 안내. `blog_parser.py run_parser(reprocess_empty=True)`: OpenAI 미설정 시 명확한 경고, 기존 포스트 중 sector_stocks 없는 것 재파싱 지원. scheduler.py `_loop_sector_blog`/`_job_sector_blog` 추가: 매일 07:00 자동 블로그 파싱. ⚠️ 종목 자동 추출은 .env에 `OPENAI_API_KEY` 설정 필요. |
| 2026-05-03 | **[대차잔고 V2 API 전환 + 000000 bogus row 수정]** `collectors/public_data.py`: V2 API(`https://apis.data.go.kr/1160100/GetStocLendBorrInfoService_V2`) 추가. V2는 날짜 필터 미지원으로 마지막 페이지부터 역순 수집. ISIN→code 변환 `isin[3:9]`. V1 필터에 `code == "000000"` skip 조건 추가. `fetch_short_sell()`은 V2 우선, 실패 시 V1 fallback. `scheduler.py` gap 감지 쿼리에 `WHERE stock_code != '000000'` 추가. 기존 bogus rows 18건 삭제. ⚠️ V2 API도 `apis.data.go.kr` 도메인 — 환경에 따라 DNS 차단 가능. 차단 시 macOS `networksetup -setdnsservers` 설정 또는 VPN 필요. |
| 2026-05-03 | **[개별종목 대차잔고 최종 업데이트 날짜 표시]** App.jsx ~3304줄: 대차잔고 섹션에 `shortData.latest_date` 표시(노란색 소자). 백엔드 `/api/buy-candidates/short-sell/{code}`는 이미 `latest_date` 필드 반환 중. |
| 2026-05-03 | **[고용정보 페이지 개선]** App.jsx EmploymentView: ① 구 API(`/api/employment/nps-monthly`, `/api/employment/company-list`) 의존성 완전 제거 ② 월별 고용변동 탭 → 기업 검색 차트 + 수집 현황 안내(202602 2194개)로 단순화 ③ 1개월 데이터인 경우 ⚠️ 경고 표시 ④ NpsTrendView.jsx: `activeMonthCols`로 데이터 있는 컬럼만 동적 표시(현재 이번달 1열), 데이터 커버리지 경고 배지 추가. SectorFollowupView: ① 탭 `overflowX:'auto'` → `flexWrap:'wrap'`(스크롤바 제거, 2-3줄 자동 래핑) ② `extractKey()` 스마트 제목 추출(괄호/조사 제거, 14자 이내): "AI 인프라 기업의 독주 흐름"→"AI 인프라 기업", "국내 로봇관련주 원페이퍼"→"국내 로봇관련주" |
| 2026-05-03 | **[금융위 V2 대차정보 API 신규 수집 + 수급현황 탭 확장]** `collectors/public_data.py`: `_SHORT_V2_RANK/MONTH/FBAL/FTRAD` 4개 상수 추가. `fetch_short_rank()`, `fetch_short_monthly()`, `fetch_short_foreign_balance()`, `fetch_short_foreign_trade()` 신규. `collect_short_all_for_date()` 병렬 수집 래퍼. **버그**: `getStLendAndBorrItemRank_V2`의 `isinCd`가 6자리 코드 형식("000020")으로 반환 → `elif len(isin)==6 and isin.isdigit()` 분기 추가. DB 4개 테이블 신규 생성(`short_rank_daily`, `short_monthly_stat`, `short_foreign_balance`, `short_foreign_trade`). `routes/market_indicators.py`: 5개 대차 API 엔드포인트 추가(`/short-dates`, `/short-rank`, `/short-history`, `/short-foreign`, `/short-monthly`). App.jsx `MarketIndicatorsView`: 탭 3개 추가(대차종목순위/대차거래현황/내외국인대차), 9개 상태변수 추가. `scheduler.py::_job_public_data()`: `collect_short_all_for_date()` 호출 추가. 초기 데이터: 2026-04-08~29 16 영업일치 백필(대차종목순위 5527건, 내외국인거래량 4523건). |
| 2026-05-03 | **[HOT 섹터 블로그 파서 로컬 매칭 fallback]** `Sector_define/blog_parser.py`: ① `scrape_blog_list()` categoryNo 49→7(올바른 "돈의흐름 팔로잉" 카테고리), CSS 셀렉터→`re.findall(r"logNo=(\d{10,})")` regex 추출. ② `parse_blog_post_local()` 신규: stock_universe 종목명 길이 내림차순으로 longest-match 스캔. ZWSP(`​`) 단락 분리. `■□◆` 대형 헤더, `-종목명(설명)` 대시 헤더 패턴 인식. ③ `_parse_post()` 신규: OpenAI key 있으면 AI 우선 → 없으면 로컬 매칭 자동 fallback. 기존 18개 포스트 재파싱 → 93개 종목 추출(12개 포스트에 한국 종목, 6개는 글로벌 컨텐츠). |
| 2026-05-04 | **[스탁이지 전략 AI 분석기 + 스케줄러 통합]** `stockeasy_analyzer.py` 신규 (620줄): ① 3전략(Peak/모멘텀/벨류) 페이지 스크래핑 ② 각 종목 DB 데이터(기술지표·수급·재무) 수집 ③ OpenAI 분석(없으면 규칙기반 fallback) ④ 텔레그램 리포트 전송 ⑤ `stockeasy_analysis` DB 테이블 저장. `run_weekly_summary()` 주간 누적 패턴 요약 추가. `scheduler.py`: `_loop_stockeasy_analysis`(매일 16:30) + `_loop_stockeasy_weekly`(매주 일요일 09:00) 추가. `routes/backtest.py`: `GET /matrix` 엔드포인트 추가 (V1~V8 × 6기간 CAGR/MDD 비교). `BacktestView`(App.jsx 6084줄) 전략×기간 매트릭스 뷰 추가. `peak_monitor.py` 재시작(PID 29824) — 4월 26일 이후 중단 → 05-04 08:44부터 정상 동작 확인(모멘텀 4종목 매도 감지). |
| 2026-05-04 | **[stockeasy_analyzer 편입일 기준 분석 수정]** `get_stock_data(name, entry_date)` 이미 `date<=entry_date` 사용 중 확인. 편출 종목(`exits`)에 `entry_stock_data`(편입일 기준) + `exit_stock_data`(편출일 기준) 두 개 데이터 세트 추가. `_rule_based_analysis()`: 편출 종목 표시에 RSI/기관수급 편입→편출 변화 비교 추가. AI 프롬프트에 "⚠️ 편입일 당시 데이터" 명시. |
| 2026-05-04 | **[V10/V11/V12 대세종목 발굴 전략 신규]** `signal_engine.py`: `calc_earnings_explosion()`(V10-이익폭발: OP YoY>80%, Rev YoY>30%, 2분기 연속성장), `calc_turnaround_momentum()`(V11-흑자전환: 과거 OP<20억→현재 OP>50억 2분기), `calc_sector_megatrend()`(V12-섹터대세: KOSPI 3M 대비 섹터 alpha>15%) 신규 추가. `routes/signals.py`: `/v10-earnings-explosion`, `/v11-turnaround`, `/v12-sector-megatrend` 3개 엔드포인트 추가. `App.jsx`: `MegatrendView` 컴포넌트 추가(~8529줄), NAV에 "🚀 대세 종목 발굴" 메뉴 추가(텐버거헌터 아래). V10 32개 후보(프로텍 OP+636%, 에스티팜 +317%), V11 21개(엘앤에프·이수페타시스), V12 40개(전기장비·반도체 섹터). |
| 2026-05-04 | **[V10/V11/V12 백테스트 구현 + logic_reference.md 문서화]** `backtest.py`: `_is_buy_v10()`, `_is_buy_v11()`, `_run_backtest_v12()`, `run_backtest_v10/v11/v12()`, `_run_generic_backtest()` 추가. `routes/backtest.py`: `/run-v10`, `/run-v11`, `/run-v12` 3개 API 추가, matrix strategy_order에 v10/v11/v12 추가. **버그수정**: `_calc_metrics()` 복소수 오류(end_val<0) 방지, V10/V11 재무 인덱스 버그(op=fin[4]→fin[3], rev=fin[3]→fin[2]) 수정. **백테스트 결과**: V10 CAGR 7.0%/MDD-25.5%, V11 CAGR 8.8%/MDD-19.5%, V11 2022-2023 구간 CAGR **56.2%**/MDD-15.2% (이수페타시스·한화에어로스페이스 유형 포착). V12 섹터대세는 후행성 신호 문제로 CAGR 1.5% (개선 필요). `logic_reference.md` 섹션5~7에 V10/V11/V12 전략 상세 문서화. |
| 2026-05-04 | **[US 미국 주식 대시보드 신규 구축]** `/Applications/us_market_dashboard/` 독립 FastAPI 앱(포트 8002). yfinance 무료 데이터 기반. S&P500 503종목 + NASDAQ-100 101종목 = 516종목 유니버스. DB: `us_market.db`(5개 테이블). API: `/api/us/market/*`, `/api/us/stocks/*`, `/api/us/screener/*`. Frontend: React+Vite SPA (포트 5174, 4탭: 시장개요/종목분석/스크리너/시스템). 데이터 백필: 가격 5년치(565K+행), 재무/펀더멘탈 수집 중. 백필 도구 `backfill.py --mode prices|financials|fundamentals|indices|all`. |
| 2026-05-04 | **[DART 수주·공급계약 공시 수집 시스템 신규 구축]** `collectors/dart_contract_collector.py` 신규(ZIP 문서 파싱+AI분석+텔레그램): ① pblntf_ty 필터 없이 전체 공시에서 "단일판매·공급계약" 키워드 추출(B 타입 쿼리 시 0건 반환 버그 수정) ② DART 문서 API ZIP binary(PK 헤더) 압축해제→HTML 태그 제거→본문 추출 ③ `_get_api_key()` sys.path 조작+.env 직접 읽기로 subprocess 실행 환경 호환 ④ 시그널 강도 ★1~★5 (해외+매출비중+AI점수 복합) ⑤ `collect_dart_contracts(days, min_signal)` 메인 수집 함수 ⑥ `get_recent_contract_signals(days)` V8/V11 전략 연동용. `routes/dart_contracts.py` 신규(6개 API). `main.py` 라우터 등록. `scheduler.py` 08:00/13:00/17:00 수집 잡 추가. `App.jsx` `DartContractView` 컴포넌트 + NAV "📋 수주공시 알림" 추가. 38건 수집(★5: 제닉스로보틱스/PS일렉트로닉스/미래산업). **핵심 이슈 기록**: App.jsx JSX에서 `YoY>10%` 미이스케이프 문자(`>`)로 vite 빌드 실패 → `YoY≥10%`로 수정, 서버 재시작. |
| 2026-05-04 | **[수주공시+HS수출 보너스 점수 기존 전략 통합]** `signal_engine.py`: ① `_load_contract_bonus_map(days=90)` 신규 — dart_contracts에서 stock_code별 ★1~★5 보너스 점수 맵(TTL 1시간) ② `_load_hs_export_bonus_map(months=6)` 신규 — HS수출 최근 3개월 vs 이전 3개월 성장률로 +1~+4점(TTL 4시간) ③ `calc_trend_candidates()` V1: 수주/HS 보너스 reasons에 📋/🚢 레이블로 추가 ④ `calc_earnings_explosion()` V10: 수주공시 ×2배 가중, HS수출 +1배 ⑤ `calc_turnaround_momentum()` V11: 수주공시 ×2배, HS수출 ×2배 가중 (흑자전환에 수출증가가 핵심 드라이버). `tenbagger_engine.py`: `_score_stock()`에 `contract_hs_bonus` 축 추가 (최대 +10점). **보너스 원칙**: 독립 전략(V13) 신규 생성 대신 기존 V1/V10/V11/텐버거에 가산점으로 통합. |
| 2026-05-04 | **[HS 수출 데이터 대폭 확장]** `hs_trade_lab/scripts/backfill_trade_series_cache.py` 실행: trade_series_cache 8,080건→**18,119건** (+125%). 실수출 데이터 종목: 108종목→**285종목** (+164%). HS 보너스 맵 적용 종목: 60종목→**192종목** (+220%). 배경: customs_monthly_record 130만건 원본 데이터가 있었으나 trade_series_cache 동기화가 미실행 상태였음. 재실행 권장: 매월 1회 `python3 hs_trade_lab/scripts/backfill_trade_series_cache.py`. |
| 2026-05-04 | **[DART 5년치 백필 + HS/NPS 보너스 점수 통합]** ① `collect_dart_contracts(skip_ai, bgn_str, end_str)` 파라미터 추가 — 규칙기반 skip_ai=True 모드 추가 ② `hs_trade_lab/scripts/backfill_trade_series_cache.py` 실행 → trade_series_cache 8,080→18,119건(+125%), HS 보너스 적용 종목 73→285종목 확대 ③ `signal_engine.py`에 3중 보너스 시스템 추가: `_load_contract_bonus_map()`(TTL 1hr), `_load_hs_export_bonus_map()`(TTL 4hr), `_load_nps_employment_bonus_map()`(TTL 24hr). V1트렌드·V10·V11·가치주에 모두 적용. 저평가주 ×2배 가중. ④ `tenbagger_engine.py`에 `growth_bonus` 6번째 축 추가(+15~-5점). ⑤ DART 백필 재시작(PID 77610): 2022-01-01~현재, 53청크, `/tmp/dart_backfill_2022.log`. 2021년 399건 이미 수집됨. ⑥ `_save_contract()` DB 락 retry 로직 추가(지수백오프 최대 6회, busy_timeout 60초). |
| 2026-05-04 | **[NPS 보너스 완전 제거 + 수집 중단]** 사용자 요청: 연간 총인원(employment_company)은 월별 차이값 없어 선행신호 불가. ① `signal_engine.py`: V1트렌드·V10·V11·가치주에서 `_load_nps_employment_bonus_map()` 호출 전부 제거 ② `tenbagger_engine.py`: growth_bonus를 DART수주+HS수출만으로 변경 (+10점 max) ③ `scheduler.py`: `_loop_nps_daily`(06:00) + `_loop_insurance_monthly`(매월5일) 비활성화 ④ `employment.db`: `nps_workplace_monthly`(2203건, apis.data.go.kr 차단으로 수집불가)·`stock_bizr_no_map`(1627건) 테이블 삭제. 월별 신규취득자/상실가입자 데이터가 의미있으나 API 차단 상태. |
| 2026-05-04 | **[보너스 효과 백테스트 신규 추가]** `backtest.py`: `run_backtest_v10_hs()`, `run_backtest_v11_hs()`, `_run_generic_backtest_with_sc()` 추가. HS 수출 YoY ≥ N% 조건 필터 추가 버전. `routes/backtest.py`: `/run-v10-hs`, `/run-v11-hs` 엔드포인트 신규. 2020-2025 V10/V11 vs V10+HS/V11+HS 비교 백테스트 실행 중 (결과 대기). |
| 2026-05-04(2) | **[V1 트렌드 백테스트 + V1+DART 검증 + NPS 월별 수집기 재설계]** ① `backtest.py`: `_is_buy_v1()` 신규(MA20>MA60>MA120, RSI42-72, 거래량×1.3, 52주고점-30%), `run_backtest_v1()`, `_load_dart_signal_map()`, `run_backtest_v1_dart()` 추가 ② `routes/backtest.py`: `/run-v1`, `/run-v1-dart` 엔드포인트 추가 ③ **결과**: V1 CAGR 17.69%/MDD-31.6%, V1+DART필터 CAGR 9.48%/MDD-7.6% → DART를 하드필터로 쓰면 CAGR 하락, 기존 보너스점수 방식 유지 결정 ④ V10+HS(CAGR 11.9%/MDD-39.4%) vs V11+HS(CAGR 12.5%/MDD-7.0%) 비교 완료: HS필터가 V11엔 효과적, V10엔 역효과 ⑤ `employment_monitor/collect_nps_monthly.py` 전면 재작성: biz_no_6(6자리) 방식→회사명+정확한이름매칭 방식으로 전환, `_ENG_TO_KOR` 영문→한글 변환 테이블(SK→에스케이, NAVER→네이버 등), `_name_patterns()` 8패턴 시도, `_find_latest_seq()` exact-match 최신seq 탐색. ⑥ NPS seq 매핑 수집 중 (PID 88497, 상위 300종목) |
| 2026-05-04 | **[V8 수출선행 전략 실제 구현 — HS무역통계+NPS고용 연동]** `backtest.py`: ① `HS_DB_PATH`/`EMP_DB_PATH` 상수 추가 ② `_load_trade_signals()` HS무역DB에서 종목별 월별 수출금액 로드 ③ `_load_employment_signals()` NPS고용DB에서 종목별 월별 순증가 로드 ④ `_get_export_yoy()` 수출 YoY 계산 ⑤ `_date_to_ym(lag_months=2)` 2개월 발표 지연 보정 ⑥ `_run_backtest_v8()` 전체 포트폴리오 시뮬레이션 구현 ⑦ **데이터 품질 필터** 추가: 단일일 주가변동 >3배 또는 <0.2배 종목 skip (DI동일 001530 가격 데이터 오염 방지) ⑧ 매수신호: 수출YoY 변곡점(avg_recent≥8% + 가속도 +20%p 이상) + 가격 MA60 ±20% 범위 + RSI 42~65 + OP>0 + 고용순증가(선택) ⑨ **수출주도 청산**: 30일 이후 수출YoY<-5% 2개월 연속이면 "수출감소청산". `routes/backtest.py`: `/run-v8` 엔드포인트 추가. `App.jsx` BacktestView: 전략 선택 드롭다운 추가(V4/V8/V10/V11/V12), 기본 시작일 2018-01-01로 변경. **결과**: CAGR 4.12%, MDD -18.9%, P/L비율 2.67, 승률 35.7% (94종목 소우주 기인). **핵심 발견**: 원래 V8은 V4 로직을 실행(데이터 미연동); 이번에 최초로 실제 선행지표 연동 구현. |
| 2026-05-04(3) | **[백테스트 매트릭스 정리 + NPS 월별 보너스 복구 + 과거 소급수집]** ① `routes/backtest.py`: V5(수급모멘텀)/V12(섹터대세) 매트릭스에서 제거, `v_trend`(V트렌드 MA정배열)/`v_dart`(V트렌드+DART필터) 추가, `STRATEGY_DESC` 신규(각 전략 한줄 설명), strategy_order=`["v1","v2","v3","v4","v6","v7","v8","v_trend","v_dart","v10","v11","combo"]`, 매트릭스 응답에 `desc` 필드 포함 ② DB: `d273d064`(V1트렌드 CAGR17.69%) strategy=`v_trend`, `7e3962da`(V1+DART) strategy=`v_dart`로 업데이트 ③ `employment_monitor/collect_nps_monthly.py`: `build_historical_data(months_back=36)` 신규 — nps_seq_map 회사 전체에 ALL seqs 조회(최대 5페이지)→최신seq=현재월/다음seq=3개월전...형식 소급 수집. `--historical`, `--months-back` argparse 플래그 추가. dateutil 의존성 추가 ④ `signal_engine.py`: `_load_nps_monthly_bonus_map()` 신규 (collect_nps_monthly.get_nps_bonus_map() 래퍼, TTL 24시간, 최대 +2점), `calc_trend_candidates()`(V1추세)에만 NPS월별 보너스 적용 (V10/V11/가치주/텐버거는 제외 유지) ⑤ NPS 과거 수집: `python3 employment_monitor/collect_nps_monthly.py --historical` 실행 필요 (API 장애 시 작동 안 함) |
| 2026-05-04(4) | **[백테스트 매트릭스 최종 정리 — 핵심 5전략 × 4기간]** ① ^KS11(KOSPI) 데이터 2011~2021 yfinance 백필(2536건 추가) → 모든 기간 정확한 시장필터 적용 가능 ② `backtest.py::_run_generic_backtest()`: sim_dates fallback 추가(^KS11 없으면 전체 종목 날짜 사용) ③ V트렌드 전체 기간 재실행(^KS11 완전 데이터 기반): 2018~2025 CAGR 15.7%(↑ 구 11.35%), 2020~2021 CAGR 29.0%(0→복구), 2022~2023 -7.9%, 2023~2025 53.5% ④ V11 runs strategy='v11' 업데이트(de725d1f/ec80f5fa/84a54eeb/2f975c20) ⑤ `routes/backtest.py`: `CORE_STRATEGIES=["v_trend","v2","v11","v4","v1"]`, `CORE_PERIODS=["2018~2025","2020~2021","2022~2023","2023~2025"]` 상수화. 매트릭스 쿼리 ORDER BY created_at(최신우선) 변경. 2010년대 기간 자동 제외(PERIOD_LABELS에 없는 기간 skip). ⑥ App.jsx BacktestView: strategy_order 기반 필터링(v5/v12/v8등 자동 숨김), stratDesc→백엔드 s.desc 사용, 강조색 v8→v_trend 변경 ⑦ `logic_reference.md`: 핵심 5전략 × 4기간 매트릭스 최신화, V트렌드 섹션 신규(섹션3) |
| 2026-05-04(6) | **[5기간 백테스트 + 포지션 로테이션 구현]** ① `routes/backtest.py` PERIOD_LABELS/CORE_PERIODS 전면 교체: 4구간(2018~)→사용자지정 5구간(20.3~21.11 / 21.12~22.10 / 22.11~23.10 / 23.11~24.12 / 24.6~25.5). 구버전 호환 엔트리 보존. ② `backtest.py::_run_portfolio()`: 포지션 로테이션 구현 — 포지션 가득 찼을 때 새 신호 종목이 `_score_entry` 기준 최하위 보유 종목 대비 15% 이상 우세하면 교체(5일 이상 보유 조건). ③ `_run_generic_backtest()`: `strategy_key` 파라미터 추가 — INSERT 시 strategy 컬럼 정확하게 저장(기존 DEFAULT 'combo' 버그 수정). `run_backtest_v1`→'v_trend', `run_backtest_v10`→'v10', `run_backtest_v11`→'v11' strategy_key 지정. ④ 7전략 × 5기간 = 35회 백테스트 실행 완료. ⑤ App.jsx BacktestView: V11 설명에 "시장필터 없음" 명시, V트렌드/V11 새 기간 결과 반영. ⑥ NPS API 접근 확인: DNS 차단 해제(apis.data.go.kr 접속 가능), 그러나 `getPdAcctoSttusInfoSearchV2` 엔드포인트가 stdrYm 무관하게 동일값 반환(시계열 미지원). `_loop_nps_daily` 계속 비활성 유지. |
| 2026-05-04(10) | **[ETF 스케줄러 + 고용정보 탭 종합 개편 + 주요지표 버그수정]** ①`ETF_check/scheduler.py`: 수집 시간 00:10(새벽)→**20:30(저녁)** 변경, `is_trading_day()` 전날 기준→오늘 기준, `_KR_HOLIDAYS` set 추가(공휴일 스킵), playwright 설치(pip). 백그라운드 시작(PID 18646). 내일(5/5 어린이날) 스킵, 5/6부터 자동수집. 수동수집 trigger(PID 18741, 4035종목, 삼성전자 37조원 정상). ②`processor.py::get_macro_status()`: 인덱스 루프에 `("^KS200","KOSPI200")/("^KQ150","KOSDAQ150")` 추가 → 주요지표 선물정보 카드 복구. ③`App.jsx`: NASDAQ/S&P500 변화율 소수2자리 정밀도 수정(`Math.abs(chgPct)<0.1` 시 `toFixed(2)` 사용, 0.04%→"0.04%" 표시). Cell 컴포넌트도 동일 수정. ④고용정보 탭: `EmploymentView` 3탭 재편(고용보험랭킹WLB/기업별피보험자현황/국민연금월별차트), 국민연금 탭 연간 추이+기업 검색 차트로 전환(`/annual-top`, `/annual-trend` API 연동). ⑤`NpsTrendView.jsx` 5기간 정렬 탭(피보험자수/1M/3M/6M/1Y 대비) + 국민연금토글 분리. ⑥`routes_employment_v2.py`: `/annual-top`·`/annual-trend` 엔드포인트 신규, `/trend` sort_by 확장(workers/1m/3m/6m/1y), wlb_diff 계산 로직 추가. |
| 2026-05-04(5) | **[백테스트 대규모 개선 — 월별 매수한도 + HS 보너스 + ALL전략 매트릭스 + V11 시장필터 수정]** ① `backtest.py::_score_entry()` 신규: 거래량×0.35 + RSI점수×0.35 + MA20진입품질×0.20 + HS수출보너스×0.10. V1-V8(`_run_portfolio`)에서 후보 정렬 후 상위 선택에 사용. ② `_run_portfolio`에 `max_new_per_month=10` 추가: 월 신규 매수 최대 10종목 제한 + 최고점수 종목 우선선택 ③ `_run_generic_backtest`에 `max_new_per_month` + `use_market_filter` 파라미터 추가 ④ **V11 시장필터 완전 제거** (`use_market_filter=False`): 흑자전환 전략은 하락장에서도 매수해야 함 — 2022~2023 CAGR 2.29% → **41.1%** 로 복구 (원인: KOSPI<MA120 하락장에 62.1% 거래일이 차단됨). `use_market_filter=False` 로그 "시장필터 없음" 표시 ⑤ `routes/backtest.py` ALL_STRATEGIES 14개 전략 모두 매트릭스 표시: `["v_trend","v2","v11","v4","v1","v10","v8","v_dart","v6","v7","v3","v12","v5","combo"]` ⑥ `App.jsx BacktestView` 전략별 상세 설명 섹션 페이지 하단 추가 (전략별 매수/매도 조건, 특징) ⑦ V10/V11/V12/v10_hs/v11_hs `routes/backtest.py` INSERT의 `strategy='combo'` 버그 수정 → 각각 올바른 strategy 값으로 수정 ⑧ DB 타임아웃 30초→120초 상향 (scheduler 수급수집과 동시실행 시 lock 방지) ⑨ V11 전략 결과 최신화: 2018~2025 CAGR **15.2%**/MDD-19%, 2020~2021 32.0%/MDD-14%, 2022~2023 **41.1%**/MDD-27%, 2023~2025 3.9%/MDD-24% |
