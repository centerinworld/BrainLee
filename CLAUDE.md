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
├── scheduler.py         # 수집 스케줄러 CollectionScheduler (400줄)
├── signal_engine.py     # 시그널 계산 엔진 (2507줄)
├── peak_monitor.py      # 가상매매 모니터 + Telegram 알림 (663줄)
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
| `tenbagger_results` | - | run_time, run_type, stock_code, stock_name, total_score, score_detail(JSON), reasons(JSON), ai_analysis, current_price, market_cap, per, pbr, roe, revenue_growth, op_growth, op_margin, inst_net_10d, frn_net_10d, telegram_sent | 텐버거 발굴 결과 |
| `investor_trading_daily` | 0 | bas_dt, stock_code, indv_net, inst_net, frgn_net | ⚠️ 미수집 |
| `foreign_holding_daily` | 0 | bas_dt, stock_code, frgn_hold_pct | ⚠️ 미수집 |
| `radar_semiconductor_override` | 148 | sort_order, lv0, lv1, lv2, company_name, ticker, country_raw, country_flag, lv2_investment_view, company_insight | 시장 레이더 반도체 기업 목록 |
| `radar_sector_override` | 409 | sort_order, lv0, lv1, lv2, company_name, ticker, country_raw, country_flag, lv2_investment_view, company_insight | 시장 레이더 기타 섹터 기업 목록 |
| `radar_market_cache` | 192 | ticker, market_cap, per, pbr, updated_at | 시장 레이더 시총/PBR/PER 캐시 |
| `radar_price_cache` | - | ticker, rn, close, trade_date | 해외 주식 가격 이력 캐시 (yfinance 2년치) |
| `sector_posts` | - | id, title, blog_url, post_date, ai_summary, telegram_sent, created_at | [신규] 섹터 팔로우업 블로그 포스트 |
| `sector_stocks` | - | id, post_id, category(lv1), stock_name(lv2), stock_code, ref_price, memo | [신규] 섹터별 종목 매핑 |

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
```

### routes/market_radar.py → /api/market-radar ★신규(2026-04)
```
GET  /sector/{sector}/detail  # 섹터 세부 (sections, 기간별 주가변동, 시그널)
GET  /all                     # 전체 섹터 시그널 요약
POST /init-semiconductor      # 반도체 기업 목록 DB 초기화 (최초 1회)
POST /refresh-cache           # yfinance로 해외주식 가격 2년치 + 시총/PBR/PER 갱신 (백그라운드)
```
### Sector Define API → /api/sector-define ★신규(2026-04)
```
GET  /posts                   # 블로그 포스트 목록
GET  /post/{post_id}          # 포스트 상세 + 섹션별 종목 + 실시간 시세 보완
POST /parse                   # 블로그 즉시 파싱 (백그라운드)
POST /init                    # DB 테이블 초기화
```
섹터 키: semiconductor / battery / power_infra / pharma / defense / shipbuilding / energy

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
GET  /yearly             # 연도별 고용인원 (25/24/23년 가로 배치 + 증감률)
GET  /chart              # 기업별 고용 추이 차트 (쿼리: query=종목명)
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
| `SystemStatus` | system | ~8795 |
| `EmploymentView` | employment | 8842 |
| `EtfCheckView` | etf_check | 외부 파일 (`frontend/src/EtfCheckView.jsx`) |

### 네비게이션 구조
```
NAV_ITEMS 정의: 9152줄
렌더 스위치:    ~9283줄

순서: macro → market_indicators → market_radar → analysis → semiconductor_sector → hot_sector
    → screener → tenbagger → trend → reports → telegram → backtest → hs_trade2 → employment → etf_check
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
