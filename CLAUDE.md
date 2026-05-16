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
│   ├── market_indicators.py → /api/market-indicators/*  ★2026-04 신규
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
| `_job_closing` | 15:40 daily | 종가 확정 + portfolio_snapshot |
| `_job_screener_precompute` | 매 30분 | 시그널 캐시 갱신 |
| `_job_krx_daily` | 18:00 daily (영업일) | KRX 승인API 전종목 OHLCV + 지수 수집 (data-dbg.krx.co.kr) |
| `_job_supply_daily` | 17:30 daily (영업일) | KIS 전종목 최근 30일 수급 누락분 보완 |
| `_job_krx_investor_playwright` | 18:10 daily (영업일) | KRX 전종목 기관/외국인 순매수 금액 수집 (Playwright, data.krx.co.kr 로그인) |

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
| 2026-05-15 | `routes/extra_signals.py` + `ETF_check/routes_etf.py` 워크트리에 추가 및 main.py 등록. ETF 수집실패일(etf_count=0 && etf_amount=0) 건너뜀 로직 추가(extra_signals + routes_etf get_available_dates). 섹터 트렌드 sector_mid→sector_large 기준으로 전체 변경. 수출/계약 공동 표시 동일 sector_large 종목만 필터링. 고용 트렌드 카드에 current_workers(현재 근무인원) 필드 추가(백엔드+프론트). |
| 2026-05-15 | App.jsx 분리: MarketIndicatorsView/SemiconductorView/SectorFollowupView/MarketRadarView → `frontend/src/views/` 별도 파일. 공유 유틸 → `frontend/src/utils.js`. App.jsx 12,752줄→10,830줄(-1,922줄). CLAUDE.md 섹션6 줄번호 업데이트. |
| 2026-05-16 | ETF Check `/search` 수집실패일 폴백 수정: `WHERE e.trade_date = ?` → 종목별 최근 유효일 서브쿼리. 서버 재시작 방법 CLAUDE.md 명시(launchctl kickstart). Codex 병렬작업 충돌방지 규칙 추가. crontab ETF 23:30 재수집+02:30 백필 추가. cash_flow_data 이상값 6건 NULL 처리. |
| 2026-05-16 | PBR/PER 즉시 반환: `get_stock_fundamentals(main.py)` 캐시 미스 시 Naver 스크래핑 대기(2~3초) 제거 → `stock_universe.per/pbr` 즉시 반환 + 백그라운드 Naver 갱신 유지. EPS 계산 보완: `processor.py` `_calc_eps()` 신규 - eps=0.0이거나 None인데 net_income이 실제값이면 `net_income/shares_issued`로 자동 계산. |
| 2026-05-16 | 종합 RS/52주 신고가 데이터 미노출 긴급 수정: `main.py`에 `routes.stock_analysis_rs` import + `app.include_router(..., prefix='/api/stock-analysis-rs')` 누락 등록(HTTP 404 원인). `frontend/src/views/StockAnalysisRsView.jsx`는 `Promise.allSettled`로 변경해 한 API 실패 시 전체 빈 화면 방지, 52주 신고가 탭에 필터(전체/근접/신고가달성)·정렬(점수/근접/거래량배수) 추가. 재기동은 규칙대로 `launchctl kickstart -k`만 사용. |
| 2026-05-16 | 종합 RS 성능/동작 보강: `routes/stock_analysis_rs.py`를 캐시 기반으로 재구성. 장중(평일 09:00~15:35) 10분 TTL, 장외 1일 TTL로 `/scratch/stock_analysis_rs_cache.json` 반환. price_history는 종목별 최근 260거래일 window 조회로 축소. 응답에 `benchmarks.kospi/kosdaq` RS 추가. `frontend/src/views/StockAnalysisRsView.jsx`는 섹터명 정규화로 탭 필터 정확화, 상단 강도바에 KOSPI/KOSDAQ RS 위치 표시, 섹터 탭 선택 시 해당 섹터 종목만 노출하도록 보강. |
| 2026-05-16 | PER/PBR Naver 스크래핑 완전 제거 → `price×EPS/BPS` 직접 계산(main.py). stock_universe fallback 유지. EPS 계산 보완(`processor.py _calc_eps`). `stock_collection_config` 테이블 신규. `scripts/fnguide_integrity_sync.py` 신규: unit_error 52건+cfs_ofs 29건 FnGuide 기준 수정, holding_company 175건 OFS config 등록, financial_anomalies 81건 resolved. large_discrepancy 640건은 --all 옵션으로 별도 실행. |
| 2026-05-16 | Codex 분석 기반 성능·정합 개선: ①`routes/stock_analysis_rs.py` double-check locking(compute를 락 밖으로), `/dashboard-data` 요약만 반환, `/dashboard-rows`·`/high52-rows` 서버 페이지네이션 신규, `/precompute` POST 추가. 초기 전송량 2.3MB→수십KB. ②`processor.py` 조회 API 내 Q4 DB 쓰기 제거(read-only 보장, 락 경합 방지). ③`stockeasy_logic_validator.py` `_load_params` shallow merge→deep merge(기본 필드 유실 방지), `min_mktcap_억` 점진 decay 로직 추가(대형주 신호 없을 때 500억씩 하향). ④`stockeasy_analyzer.py` 프롬프트 다이어트: 종목당 6줄→1줄 핵심 8개 필드, 2000자→800자, max_tokens 1500→800. ⑤`scheduler.py` `_loop_rs_precompute` 추가(18:30 영업일, /precompute POST 호출). ⑥`StockAnalysisRsView.jsx` 서버 사이드 페이지네이션+지연 로딩(52주탭 최초 진입 시만 로드), AbortController+300ms 디바운스. |
| 2026-05-16 | Codex 검증 결과 반영: ①EPS NULL/0 13,765건·BPS NULL/0 2,609건 net_income·total_equity 기반 배치 계산(`scripts/ops/fix_eps_bps_batch.py`). ②backup 테이블 5개(30일+) CSV export(41MB) 후 DROP. ③stock_corrupted_backup_20260506.db(1.6G)·venv_backup(248M) archives/2026-05-16/ 이동. ④DART 2024 분기 수집 스크립트(`scripts/ops/collect_dart_2024_quarters.py`) 신규 — P1 우선 43종목부터 Q1~Q3 수집. ⑤.gitignore .gz/.zip/.pkl/archives/ 추가. ⑥운영계획서 docs/ 4종 + ops 스크립트 3종 신규 커밋. |
| 이전 세션 | routes/ingest.py, routes/portfolio.py 신규 분리; Yahoo Finance 제거; Trigger20 URL 수정; 야간 알림 억제; 시그널 warm-up 추가; 대차잔고 URL 수정; PBR/PER 재시도 로직 |
