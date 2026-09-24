# 프로젝트 안티그래비티 — 마스터 수정사항 문서

> **사용법**: 새 대화에서 이 파일을 Claude에게 업로드하면 모든 수정사항을 즉시 파악합니다.
> **마지막 업데이트**: 2026-03-31 (14차 세션)

---

## 프로젝트 기본 정보

- **경로**: `/Volumes/Realtek_NVME/stock_dashboard/runtime/`
- **백엔드**: FastAPI (Python 3.11), SQLite (`stock.db`)
- **프론트엔드**: React + Vite (`frontend/src/App.jsx`)
- **venv**: Python 3.11 기반 ← **반드시 3.11 사용. Python 3.14는 오류 발생**

## 서버 실행
```bash
cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate
pkill -f uvicorn && sleep 2
nohup uvicorn main:app --host 0.0.0.0 --port 8000 >> server.log 2>&1 &
sleep 3 && tail -5 server.log
```

---

## ★★★ Claude 필독 — 최신본 확인 키워드 ★★★

```
✅ 14차 신규:
  trade_signal / trade_reason     ← 보유종목 매매신호 (main.py)
  short_sell_daily                ← 대차잔고 테이블 (stock_code=6자리, isinCd[3:9])
  weekly_price_collect.py         ← 매주 토요일 주가 자동 수집
  collect_all_prices.py           ← 전종목 주가 일괄 수집 (1회성)
  /api/short-sell/{stock_code}    ← 대차잔고 API
  _sync_kis_executions            ← KIS 체결 동기화 (stock_code만으로 조회)
  frn_net_buy / inst_net_buy      ← 포트폴리오 API 응답에 포함
  short_data                      ← 포트폴리오 API 응답에 포함
  setShortData 별도 fetch         ← Promise.all 외부에서 독립 실행 필수!
  saveTx 매도 검증                ← 보유수량 초과 경고
  종목명 검색 드롭다운             ← 거래입력 모달 (stock_code 자동완성)

✅ 13차 신규:
  ma200_filter / calc_adr         ← Market Regime 시그널
  calc_rs / calc_atr              ← 상대강도/ATR 시그널
  macd_filtered / rsi_filtered    ← 정배열 필터 적용
  system_judgment                 ← 4단계 종합 판정
  SIGNAL_GUIDE / showGuide        ← 시그널 설명 토글
  TelegramSettings                ← 텔레그램 설정 UI
  telegram_monitor_settings       ← 텔레그램 설정 DB 테이블

✅ 12차 신규:
  telegram_monitor.py             ← 오전9시/오후9시 채널 분석
  tg_daily_mentions               ← 텔레그램 종목 언급 DB
  /api/telegram/settings          ← 텔레그램 설정 API

✅ 9차까지:
  stock_universe, backfill_progress.json
  fmtAmt.*\/100, is_annual=True AND quarter=4
```

---

## ★★★ 핵심 원칙 (절대 위반 금지) ★★★

### 원칙 1. fmtAmt — /100 (백만원→억원)
```jsx
// ★★★ App.jsx 교체 시마다 반드시 확인 ★★★
const fmtAmt = (v) => {
  if(!v) return null;
  const sg = v>0?'+':'-', a = Math.abs(v);
  if(a >= 10000) return sg + Math.round(a/10000).toLocaleString('ko-KR') + '조원';
  if(a >= 100)   return sg + Math.round(a/100).toLocaleString('ko-KR') + '억원';  // /100 !!
  if(a >= 1)     return sg + Math.round(a).toLocaleString('ko-KR') + '백만원';
  return null;
};
```

### 원칙 2. 수급 단위 — 개별종목 vs 시장지수
```python
# ★★★ 절대 혼동 금지 ★★★
# 시장지수(^KS11, ^KQ11): DB에 이미 억원 단위 → /100 하면 안 됨
# 개별종목: DB에 백만원 단위 → /100 해야 억원
# signal_engine.py _calc_supply_trend():
if stock_code and stock_code not in ('^KS11', '^KQ11'):
    inst_sum = inst_sum / 100
    frn_sum  = frn_sum  / 100
```

### 원칙 3. shortData fetch — Promise.all 외부 독립 실행
```jsx
// ❌ 금지: Promise.all 안에 포함 (배열 구조 망가짐)
// ✅ 확정: 별도 fetch
fetch(API(`/api/short-sell/${selectedStock}`))
  .then(r=>r.ok?r.json():null)
  .then(d=>setShortData(d))
  .catch(()=>setShortData(null));

const [chartRes, tableRes, quarterRes, summRes, aiRes] = await Promise.all([...]);
```

### 원칙 4. KIS 체결 동기화 — stock_code 우선 조회
```python
# ❌ 금지: broker='KIS', owner='이효준' 조건으로만 조회 → 신규 행 INSERT 버그
# ✅ 확정: stock_code만으로 먼저 조회
holding = db.query(models.Portfolio).filter(
    models.Portfolio.stock_code == code,
).order_by(models.Portfolio.quantity.desc()).first()
```

### 원칙 5. 대차잔고 테이블 — short_sell_daily
```python
# ★★★ stock_lending 테이블 사용 금지 (존재하지 않음) ★★★
# ✅ 확정: short_sell_daily 테이블 사용
# ISIN → 종목코드: isinCd[3:9] (KR7XXXXXX1 구조)
# 필드명: lnbRmanStckCnt (대차잔고주수)
# signal_engine.py short_sell 로직:
rows = conn.execute("""
    SELECT borrow_bal_qty FROM short_sell_daily
    WHERE stock_code=? AND borrow_bal_qty IS NOT NULL
    ORDER BY bas_dt DESC LIMIT 10
""", (stock_code,)).fetchall()
```

### 원칙 6. cron — 환경변수 필수
```bash
# ★★★ 모든 cron에 source ~/.zshrc && 필수 ★★★
# 없으면 OPENAI_API_KEY 등 환경변수 누락으로 오류 발생
0 9,21 * * * source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && ...
```

### 원칙 7. signal_engine DB 초기화
```bash
# signal_config 충돌 시:
sqlite3 stock.db "DELETE FROM signal_config; DELETE FROM signal_result;"
python3 -c "from signal_engine import init_signal_db; init_signal_db()"
```

### 원칙 8. realtime/prices — 종목 합산 필수
```python
# ❌ 금지: result[h.stock_code] = {...}  # 동일 종목 덮어씀
# ✅ 확정: merged dict로 합산 후 계산
```

### 원칙 9. peak_holding 현재가 — price_history에서 실시간 조회
```python
# peak_holding.current_price는 고정값 → 실시간 반영 안 됨
# /api/trend/holdings에서 price_history 최신값으로 override
# 종목코드: listed_company_info → stock_universe 순서로 조회
code_row = conn.execute("SELECT stock_code FROM listed_company_info WHERE stock_name=?", (name,)).fetchone()
if not code_row:
    code_row = conn.execute("SELECT stock_code FROM stock_universe WHERE stock_name=?", (name,)).fetchone()
```

### ❌ 절대 하지 말 것
```
❌ fmtAmt에서 /10000 (→ 반드시 /100)
❌ 개별종목 수급에서 /100 누락 (→ 조원 단위로 오표시)
❌ shortData를 Promise.all 안에 포함
❌ stock_lending 테이블 참조 (존재하지 않음 → short_sell_daily 사용)
❌ KIS 동기화 시 broker/owner 조건으로만 조회 (→ 중복 INSERT)
❌ cron에서 source ~/.zshrc 누락
❌ signal_config에 구버전 항목 혼재 (→ DELETE 후 재초기화)
❌ realtime/prices에서 result[h.stock_code]= 덮어씀
❌ peak_holding.current_price 고정값 그대로 반환
❌ Q4(is_annual=True, quarter=4)를 분기 조회에서 제외
❌ 네이버 .nhn URL (→ .naver + euc-kr)
```

---

## DB 스키마 현황 (14차)

### 주요 테이블
```sql
-- 대차잔고 (14차 신규)
short_sell_daily: (id, bas_dt TEXT, stock_code TEXT, stock_name,
                   short_qty, short_amt, borrow_bal_qty, borrow_bal_amt,
                   borrow_bal_pct, created_at)
  -- bas_dt 형식: 'YYYYMMDD' (예: '20260327')
  -- stock_code: ISIN[3:9] 추출 (KR7XXXXXX1 → 6자리)
  -- 수집: public_data_collector.py collect_short_sell()
  -- API 필드: lnbRmanStckCnt (대차잔고주수)

-- 포트폴리오 API 응답 (14차 추가 필드)
portfolio response: frn_net_buy, inst_net_buy, short_data, trade_signal, trade_reason

-- 텔레그램 설정
telegram_monitor_settings: (key TEXT PRIMARY KEY, value TEXT)

-- 텔레그램 종목 언급
tg_daily_mentions: (mention_date, stock_name, market, mention_count)
```

### 기존 테이블 (유지)
```sql
stock_universe, price_history, financial_data, portfolio, portfolio_snapshot
portfolio_tx, peak_holding, peak_trade, signal_config, signal_result
report_files, telegram_messages, listed_company_info (현재 비어있음)
```

---

## 스크립트 파일 (14차 기준)

| 스크립트 | 용도 | cron |
|----------|------|------|
| `backfill_financials.py` | DART 재무 일괄 수집 | 매일 00:05 |
| `telegram_collector.py` | 텔레그램 보고서 PDF 수집 | 매일 08:30 |
| `telegram_monitor.py` | 채널 분석+종목 추출 | 매일 09:00/21:00 |
| `public_data_collector.py` | 공시/대차 데이터 수집 | 평일 18:30 |
| `weekly_price_collect.py` | 전종목 주가 주간 업데이트 | 매주 토요일 08:00 |
| `collect_all_prices.py` | 전종목 주가 일괄 수집 (1회성) | 수동 실행 |
| `peak_monitor.py` | 추세추종 모니터 | 수동/상시 |

---

## 전체 cron 설정 (14차 확정)

```bash
# 확인 명령어
crontab -l

# 확정된 cron 목록
5 0 * * * cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/backfill_financials.py >> /Volumes/Realtek_NVME/stock_dashboard/runtime/backfill_financials.log 2>&1

30 8 * * * source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_collector.py >> /Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_collector.log 2>&1

0 9,21 * * * source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_monitor.py >> /Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_monitor.log 2>&1

30 18 * * 1-5 cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/public_data_collector.py >> /Volumes/Realtek_NVME/stock_dashboard/runtime/public_data.log 2>&1

0 7 * * 1 cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/public_data_collector.py --company-only >> /Volumes/Realtek_NVME/stock_dashboard/runtime/public_data.log 2>&1

0 8 * * 6 source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/weekly_price_collect.py >> /Volumes/Realtek_NVME/stock_dashboard/runtime/weekly_collect.log 2>&1
```

---

## Signal Engine v2 (13~14차)

### 시장 시그널 (Market Regime)
| 이름 | 로직 | 비고 |
|------|------|------|
| supply_flow | 기관+외국인 3일 동반 추세 | - |
| vix_trend | VIX vs MA20 (위=Red) | 절대값→추세 변경 |
| usd_krw_trend | 환율 vs MA20 (위=Red) | 절대값→추세 변경 |
| nasdaq_ma200 | 나스닥 > 200일선 | 폴 튜더 존스 Rule |
| sp500_ma200 | S&P500 > 200일선 | - |
| kospi_ma200 | KOSPI > 200일선 | - |
| kospi_ma_align | KOSPI MA20>MA60 | 정배열 여부 |
| adr_kospi | 등락비율 20일 평균 | 75이하=과매도/120이상=과매수 |
| fear_greed | CNN 공포탐욕 (수동입력) | - |

### 종목 시그널 (4단계)
| Step | 이름 | 로직 |
|------|------|------|
| Step2 | ma_align | 5>20>60 정배열 |
| Step2 | rs_score | 3개월 수익률 vs KOSPI |
| Step2 | financials | 영업이익+YoY |
| Step2 | value | MA60+52주 AND 조건 |
| Step3 | frn_supply | 외국인 5일 누적 |
| Step3 | inst_supply | 기관 5일 누적 |
| Step3 | macd_signal | 정배열 상태에서만 유효 |
| Step3 | rsi_signal | 정배열 상태 RSI50 돌파 |
| Step3 | trend52w | -15%이내+거래량급증 |
| Step4 | atr_stop | 2×ATR 기계적 손절 |
| Step4 | vol_price | 거래량 급증 |
| Step4 | short_sell | short_sell_daily 테이블 |
| 종합 | system_judgment | 4단계 직렬 통과 |

---

## 보유종목 매매 신호 (14차 신규)

### 신호 종류
| 신호 | 조건 |
|------|------|
| 🔴 strong_sell | 수익률 -8% 이하 OR 2×ATR 이탈 OR 역배열+손실 |
| 🔴 sell | MA20이탈+RSI<45+손실 OR 고점-20%+MA60이탈 |
| 🟠 caution | 추세파괴+수익(익절고려) OR 대차급증+MA20이탈 |
| 🟡 hold | MA20 위 중립 |
| 🟢 buy | 정배열+RSI50+MACD양전환 OR MA정배열+수급양호 |
| 🟢 strong_buy | 완전정배열+MA200+RSI50~70+수급+거래급증 OR 신고가-10%+정배열 |

### 구현 위치
- `main.py` → `get_portfolio()` 함수 내 `_trade_signal` 계산 블록
- 마우스 오버 시 상세 이유 표시 (`trade_reason`)
- RSI(14일), ATR(14일), MACD, 52주 고점, MA5/20/60/200 모두 계산

---

## telegram_monitor.py 설정 (12~13차)

```python
# 모든 설정을 config.py에서 로드
import config as _cfg
TELEGRAM_API_ID   = int(getattr(_cfg, "TELEGRAM_API_ID",  0))
TELEGRAM_API_HASH = getattr(_cfg, "TELEGRAM_API_HASH", "")
TELEGRAM_SESSION  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_session"
TELEGRAM_PHONE    = getattr(_cfg, "TELEGRAM_PHONE",    "")
OPENAI_API_KEY    = getattr(_cfg, "OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
BOT_TOKEN         = getattr(_cfg, "TELEGRAM_BOT_TOKEN","")
TARGET_CHANNEL_ID = getattr(_cfg, "TELEGRAM_CHAT_ID",  "")

MONITOR_CHANNELS = ["@sunstudy1004", "@yigreport", "@DOC_POOL"]
# max_tokens=3000, messages[:50]
```

---

## 대차잔고 수집 (14차)

### 수집 로직 (public_data_collector.py)
```python
# API: 금융위원회 GetStocLendBorrInfoService
# 필드: isinCd (ISIN코드), lnbRmanStckCnt (대차잔고주수)
# KR 종목만: isinCd.startswith('KR')
# 종목코드 추출: isinCd[3:9]  ← KR7XXXXXX1 구조

# 3월 전체 수집:
python3 -c "
from public_data_collector import collect_short_sell
import sqlite3
from datetime import date, timedelta
conn = sqlite3.connect('stock.db')
d = date(2026, 3, 1)
while d <= date(2026, 3, 31):
    if d.weekday() < 5:
        collect_short_sell(conn, d.strftime('%Y%m%d'))
    d += timedelta(days=1)
conn.close()
"
```

### 신호등 로직
- 5일 합계 > 직전 5일 합계 → 🔴 증가 (공매도 압력)
- 5일 합계 < 직전 5일 합계 → 🟢 감소

---

## 주가 데이터 수집 현황 (14차)

```bash
# 현황 확인
sqlite3 stock.db "SELECT COUNT(DISTINCT stock_code) FROM price_history WHERE date >= '2024-01-01';"

# 전종목 일괄 수집 (1회성, 약 1~2시간)
nohup python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_all_prices.py > /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_prices.log 2>&1 &

# 진행 확인
tail -f /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_prices.log

# 매주 토요일 자동 업데이트 (cron 등록 완료)
# 이미 60일 이상 데이터 있는 종목만 최신 1개월 업데이트
```

---

## config.py 필수 항목
```
DART_API_KEY=...
KIS_APP_KEY=...
KIS_APP_SECRET=...
STOCKEASY_EMAIL=...
STOCKEASY_PASSWORD=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=1133750736
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=...
OPENAI_API_KEY=...  ← 마지막 줄에 직접 저장 (환경변수 fallback)
```

## Cloudflare Tunnel
```
프론트: HTTP / localhost:5173 → stock.leanquy.cloud
백엔드: HTTP / localhost:8000 → stock-api.leanquy.cloud
```

---

## 다음 대화 시작 시 체크리스트

- [ ] PROJECT_MASTER.md 업로드
- [ ] **fmtAmt /100 확인** (App.jsx — 매번 구버전으로 덮어씌워지는 버그)
- [ ] **shortData fetch Promise.all 외부 확인**
- [ ] **signal_engine short_sell → short_sell_daily 테이블 확인**
- [ ] **cron source ~/.zshrc 포함 여부 확인**
- [ ] 전종목 주가 수집 진행: `tail -5 collect_prices.log`
- [ ] signal_config 확인: `SELECT name, logic_type FROM signal_config ORDER BY scope, sort_order;`
- [ ] 수급 단위 확인: 개별종목 /100, 시장지수 그대로
- [ ] 서버 재시작 후 확인: `tail -5 server.log`

---

## ★★★ 15차 세션 수정사항 (2026-04-01) ★★★

### 1. SPA fallback 라우트 순서 문제 해결
```python
# ❌ 문제: /api/short-sell 등 API가 SPA fallback에 막힘
# ✅ 해결: API 라우트를 SPA fallback 앞에 위치시킴
# ✅ 해결: serve_spa에서 api/ 시작 경로 404 반환
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(index.html)
```

### 2. 대차잔고 로직 변경 (합계→잔고 기반)
```python
# ❌ 기존: 5/10/30일 합계 (잘못된 로직)
# ✅ 수정: 잔고 기반 신호등 (2개만 표시)
# - 금일신호: 금일잔고 vs 5일평균 비교
# - 5일신호: 5일평균 vs 직전5일평균 비교
return {
    "today": today_val,       # 금일 대차잔고
    "avg5": avg5,             # 최근 5일 평균
    "avg5_prev": avg5_prev,   # 직전 5일 평균
    "today_signal": "green" if today_val < avg5 else "red",
    "week_signal":  "green" if avg5 < avg5_prev else "red",
}
# 수량 표시: 6,337만주 / 0.63억주 형태
```

### 3. 재무제표 분기값 오류 대규모 수정
**근본 원인**: DART API Q2/Q3는 누계값인데 분기값으로 저장됨
- Q1 = 분기값 (그대로)
- Q2 실제 = Q2누계 - Q1
- Q3 실제 = Q3누계 - Q1 - Q2실제
- Q4 실제 = 연간 - Q1 - Q2실제 - Q3실제

**수정 스크립트**: `/Volumes/Realtek_NVME/stock_dashboard/runtime/fix_financial_quarters.py`
- 438건 중 97+7=104건 수정 완료
- 남은 361건은 DART API 재수집 필요 (한도 초기화 후)

**net_income=0 문제**: 분기보고서는 `당기순이익` 대신 `분기순이익`/`반기순이익` 사용
```python
# ✅ 수정된 로직
elif ("당기순이익" in acc or "분기순이익" in acc or "반기순이익" in acc) and "주당" not in acc and "지배" not in acc:
    m["net_income"] = val
```

### 4. 주요 종목 연간값 수동 수정
- 삼성전자(005930) 2016~2025 연간값 수정
- 하이닉스(000660) 2024~2025 연간+Q4 수정
- Q4 분기값 역산 저장 완료

### 5. 나스닥/S&P500 데이터 수동 수집
```bash
# 주말/장외 시 수동 실행
python3 -c "
import yfinance as yf, sqlite3
conn = sqlite3.connect('stock.db')
for code in ['^IXIC','^GSPC']:
    hist = yf.Ticker(code).history(period='5d')
    for dt, row in hist.iterrows():
        conn.execute('INSERT OR IGNORE INTO price_history ...')
conn.commit()
"
# TODO: cron에 추가 필요 (평일 미국 장 마감 후)
```

### 6. 다음 세션 TODO
- [ ] 재무제표 361건 재수집 (DART API 한도 초기화 후)
- [ ] 시그널 보드 캐시 1시간 구현
- [ ] 매크로 차트 탭 개별 동작 (KOSPI/KOSDAQ/NASDAQ/S&P500 탭 독립)
- [ ] 나스닥/S&P500 자동 수집 cron 추가
- [ ] 삼성전자 등 대형주 Q1~Q3 net_income 재수집

---

## ★★★ 16차 세션 수정사항 (2026-04-03) ★★★

### 1. SPA fallback API 차단 수정
```python
# serve_spa에서 api/ 경로 404 반환
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(index.html)
# /api/short-sell API를 SPA fallback 앞에 위치시킴
```

### 2. schemas.PriceRecord → PriceData 수정
```python
# ❌ 금지: schemas.PriceRecord (존재하지 않음)
# ✅ 확정: schemas.PriceData
# 5곳 일괄 변경 완료
```

### 3. 코스피/코스닥 날짜 형식 수정
```python
# ❌ 기존: date=_dt.combine(row_date, _dt.min.time()) → '2026-04-03 00:00:00'
# ✅ 수정: date=row_date.strftime('%Y-%m-%d') → '2026-04-03'
# price_history 테이블 날짜 정리: 52,787개 정리, 417개 중복 삭제
```

### 4. 나스닥/S&P500 자동 수집 cron 추가
```bash
# 매일 오전 6시 (화~토) 미국 장 마감 후 수집
0 6 * * 2-6 source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && \
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3 -c \
  "import yfinance as yf, sqlite3; ..." >> nasdaq_collect.log 2>&1
```

### 5. 차트 탭 개선 (데이터 재fetch 없음)
```jsx
// ❌ 기존: 탭 변경 시 fetchChartOnly 호출 → 서버 재요청
// ✅ 수정: 초기 1년치 로드 후 프론트에서 slice 필터링
const displayChartData = React.useMemo(() => {
  return chartData.slice(-chartDays);
}, [chartData, chartDays]);
// fetchChartOnly 제거, handleChartDaysChange = setChartDays만 호출
// 캔들차트/수급차트 모두 displayChartData 사용
```

### 6. 시그널 프론트엔드 1시간 캐시
```jsx
const _signalFrontCache = React.useRef({});
// 캐시 있으면 로딩 없이 즉시 표시
// 서버 캐시(1시간) + 프론트 캐시(1시간) = 이중 캐시
```

### 7. MacroDashboard React.memo 적용
```jsx
const MacroDashboard = React.memo(() => {
  // 탭 변경 시 불필요한 재렌더링 방지
});
```

### 8. 전체 cron 목록 (16차 확정)
```bash
5 0 * * * cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 backfill_financials.py
30 8 * * * source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 telegram_collector.py
0 9,21 * * * source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 telegram_monitor.py
30 18 * * 1-5 cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 public_data_collector.py
0 7 * * 1 cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 public_data_collector.py --company-only
0 8 * * 6 source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 weekly_price_collect.py
0 6 * * 2-6 source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 -c "나스닥/S&P500 수집"
```

### 9. 다음 세션 TODO
- [ ] 재무제표 361건 재수집 (DART API 한도 초기화 후 fix_financial_quarters.py 재실행)
- [ ] 삼성전자 등 대형주 Q1~Q3 net_income 재수집 (분기순이익 로직 수정 후)

### 16차 추가 수정사항

#### API 누락 수정
```python
# ❌ 누락됐던 API 추가
POST /api/trend/buy   # 신규 편입 저장
POST /api/trend/sell  # 이탈 매도 저장

# peak_holding 테이블에 profit 컬럼 없음 → profit_pct 사용
# peak_trade 테이블에 stock_code 컬럼 없음 → '' 빈값 사용
# trend/summary: SUM(profit) → SUM((sell_price-buy_price)*quantity)
```

#### peak_monitor.py DB에서 채널 로드
```python
# telegram_monitor.py MONITOR_CHANNELS를 하드코딩 → DB에서 로드
def _load_monitor_channels(db_path=None):
    conn = sqlite3.connect(db_path or "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
    rows = conn.execute("SELECT channel_id FROM telegram_channels WHERE is_active=1").fetchall()
    return ["@" + r[0].lstrip("@") for r in rows] if rows else ["@sunstudy1004",...]
MONITOR_CHANNELS = _load_monitor_channels()
```

#### peak_holding 종목 주가 자동 수집
```bash
# 매일 오후 5시 (평일) 장 마감 후 수집
0 17 * * 1-5 source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && \
  venv/bin/python3 collect_peak_prices.py >> collect_peak_prices.log 2>&1
```

#### close > 0 조건 추가
```python
# price_history 조회 시 close=0 데이터 제외
price_row = db.query(models.PriceHistory).filter(
    models.PriceHistory.stock_code == stock_code,
    models.PriceHistory.close > 0,  # ← 추가
).order_by(models.PriceHistory.date.desc()).first()
```

#### 전체 cron 최종 목록
```bash
5 0 * * *     backfill_financials.py
30 8 * * *    telegram_collector.py  
0 9,21 * * *  telegram_monitor.py
30 18 * * 1-5 public_data_collector.py
0 7 * * 1     public_data_collector.py --company-only
0 8 * * 6     weekly_price_collect.py
0 6 * * 2-6   나스닥/S&P500 Yahoo 수집
0 17 * * 1-5  collect_peak_prices.py  ← 신규
```

### 16차 최종 수정사항

#### peak_monitor.py 핵심 버그 수정
```python
# ❌ 문제: momentum/value 전략 실행 시 peak 종목을 이탈로 처리
# ✅ 수정: 이탈 감지 시 같은 strategy 종목만 처리
for db_h in db_active:
    if db_h.get("strategy","peak") != strategy:
        continue  # 다른 전략 종목 스킵

# ❌ 문제: 동일 종목 중복 편입 (entry_date 무관)
# ✅ 수정: entry_date+strategy 기준 중복 방지
dup = conn.execute("SELECT id FROM peak_holding WHERE stock_name=? AND entry_date=? AND strategy=?", ...)
if dup: UPDATE is_active=1 only
```

#### 누락 API 추가
```python
POST /api/trend/update  # 현재가/수익률 업데이트
POST /api/trend/buy     # 신규 편입
POST /api/trend/sell    # 이탈 매도
```

#### trend/holdings 실시간 가격
```python
# price_history 최신값 + KIS 실시간 시세 우선 적용
# stock_universe에서 종목코드 fallback 조회
```

#### STOCKEASY 로그인 설정
```bash
# .env 파일에 반드시 설정
STOCKEASY_EMAIL=your_email@example.com
STOCKEASY_PASSWORD=your_password
```

### 16차 최종 추가 수정 (2026-04-03 야간)

#### kis_client.py 날짜 형식 수정
```python
# ❌ 기존: datetime.now().replace(hour=0,...) → '2026-04-03 00:00:00.000000'
# ✅ 수정: datetime.now().strftime("%Y-%m-%d") → '2026-04-03'
"date": datetime.now().strftime("%Y-%m-%d")
```

#### price_history 날짜 정리 (반복 필요 시)
```bash
# close=0 삭제
sqlite3 stock.db "DELETE FROM price_history WHERE close=0 OR close IS NULL;"
# 중복 삭제 (시간 포함 날짜 vs 날짜만)
sqlite3 stock.db "DELETE FROM price_history WHERE date LIKE '% %' AND EXISTS (SELECT 1 FROM price_history p2 WHERE p2.stock_code=price_history.stock_code AND p2.date=SUBSTR(price_history.date,1,10));"
# 충돌 없는 것만 날짜 정리
sqlite3 stock.db "UPDATE price_history SET date=SUBSTR(date,1,10) WHERE date LIKE '% %' AND NOT EXISTS (SELECT 1 FROM price_history p2 WHERE p2.stock_code=price_history.stock_code AND p2.date=SUBSTR(price_history.date,1,10) AND p2.rowid!=price_history.rowid);"
# 나머지 삭제
sqlite3 stock.db "DELETE FROM price_history WHERE date LIKE '% %';"
```

#### trend/holdings 성능 개선
```python
# ❌ 기존: 매번 KIS API 직접 호출 → 느림
# ✅ 수정: price_history 최신값만 사용 (5분 배치에서 자동 업데이트)
price_row = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1", ...)
```

#### peak_monitor 전략 혼용 이탈 버그
```python
# ❌ 문제: momentum 전략 실행 시 peak 종목 이탈로 처리
# ✅ 수정: 이탈 처리 시 같은 strategy만 처리
for db_h in db_active:
    if db_h.get("strategy","peak") != strategy:
        continue
```

#### 매수후보 시그널 보드 개선
- 정렬: 목표가 도달 → 매수신호 강한 순 → 기준일1 상승률
- 기준일 헤더 클릭으로 날짜 수정 가능
- 시총: stock_universe.market_cap (억원 단위) 사용
- 기준일1/2 칸에서 날짜 제거 (헤더에만 표시)
- 목표가: "✓ 목표가 ▼X%" 또는 "▲X% 위" 표시

### 16차 야간 최종 수정 (2026-04-03 23시)

#### crud.py 날짜 형식 근본 수정
```python
# ❌ 기존: p.date를 그대로 저장 → datetime 객체 → '2026-04-03 00:00:00'
# ✅ 수정: 항상 'YYYY-MM-DD' 문자열로 정규화
if hasattr(p.date, 'strftime'):
    _date_str = p.date.strftime('%Y-%m-%d')
else:
    _date_str = str(p.date)[:10]
```

#### signal_engine 수급 단위 수정
```python
# ❌ 기존: frn_net_buy(수량/주)를 /100해서 억원으로 잘못 표시
# ✅ 수정: frn_net_buy_amt(금액/백만원)를 /100해서 억원으로 표시
# amt 없으면 qty/100 폴백
def _to_억(qty, amt, is_index):
    if amt: return amt/100 if not is_index else amt
    return qty/100 if not is_index else qty
```

#### signal_engine 로직 개선 (PBR밴드+MA200+외국인연속)
- 가치지표: PBR 1.25이하=강매수, 1.8이상=매도 (PER 제거)
- 이평선: 200일선 돌파 기반 (5>20>60 → MA200+MA60)
- 외국인수급: 5일 연속 + 환율 하락 결합
- RSI 과열: 70 → 75로 상향

#### 관련 문서
- Stock_signal_logic.md: 주식 초보자용 시그널 설명서 생성

### 17차 세션 수정사항 (2026-04-04)

#### 날짜 형식 최종 해결
- `processor.py`: `_fmt_date`, `_query_latest` 등 모든 `.strftime` 호출에 `hasattr` 체크
- `processor.py`: `_query_latest`에 `close > 0` 조건 추가
- `crud.py`: 저장 전 `strftime('%Y-%m-%d')` 변환 (String 컬럼에 datetime 저장 방지)

#### cron 최종 확정 (모두 source ~/.zshrc 포함)
```bash
5 0 * * *     source ~/.zshrc && ... backfill_financials.py
30 18 * * 1-5 source ~/.zshrc && ... public_data_collector.py
0 7 * * 1     source ~/.zshrc && ... public_data_collector.py --company-only
30 8 * * *    source ~/.zshrc && ... telegram_collector.py
0 9,21 * * *  source ~/.zshrc && ... telegram_monitor.py
0 8 * * 6     source ~/.zshrc && ... weekly_price_collect.py
0 17 * * 1-5  source ~/.zshrc && ... collect_peak_prices.py
0 6 * * 2-6   source ~/.zshrc && ... 나스닥/S&P500 (절대경로 수정)
```

#### 나스닥 cron 절대경로 수정
```bash
# ❌ 기존: sqlite3.connect('stock.db') → 상대경로로 cron에서 실패
# ✅ 수정: sqlite3.connect('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
```

#### 장외 시간 수집 최적화
```python
# 데이터 충분 + 장외 시간 → 수집 스킵
need_collect = (
    not has_history or
    not has_financial or
    _is_market_hours()
)
```

### 17차 추가 수정 (2026-04-04 오전)

#### analyze API 장중에만 KIS 저장
```python
# ❌ 기존: 장외/주말에도 KIS 현재가 저장 → 토요일 데이터 생성
# ✅ 수정: 장중에만 KIS 저장
if _is_market_hours():
    kis = _get_kis_price(stock_code)
    ...저장...
```

#### _startup_supply 주말 스킵
```python
def _startup_supply():
    if not _is_market_hours():
        logger.info("[시작수급] 장외/주말 → 수급 수집 스킵")
        return
```

#### 5분 수급 루프 날짜 비교 수정
```python
# ❌ 기존: datetime 객체로 String 컬럼 비교 → 매칭 실패 → amt 미업데이트
# ✅ 수정: 문자열로 변환 후 비교
ds_str = ds.strftime('%Y-%m-%d')
row = db.query(models.PriceHistory).filter(
    models.PriceHistory.stock_code==code,
    models.PriceHistory.date==ds_str,
).first()
```

#### 수급 헤더 amt 우선 사용
```python
# frn_net_buy_amt(금액) 우선, 없으면 frn_net_buy(수량)/100 폴백
frn_net = round(_frn_amt/100) if _frn_amt else round(_frn_qty/100)
```

#### 시그널 캐시 1시간 → 30분
```python
if not refresh and cached and (_t.time() - cached.get('at',0)) < 1800:
```

#### 핵심 파일 목록
- main.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/main.py
- App.jsx: /Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx
- signal_engine.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/signal_engine.py
- peak_monitor.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/peak_monitor.py
- crud.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/crud.py
- processor.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/processor.py
- schemas.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/schemas.py
- kis_client.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/kis_client.py
- data_collector.py: /Volumes/Realtek_NVME/stock_dashboard/runtime/data_collector.py
- DB: /Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db

---

## ★★★ 18차 세션 수정사항 (2026-04-07 ~ 2026-04-08) ★★★

> **마지막 업데이트**: 2026-04-08

### 1. hs_trade_lab 통합 (수출입 분석 탭 신규)

#### 구조
```
hs_trade_lab/               ← 독립 FastAPI 서브앱
  app/main.py               ← app = FastAPI(title="HS Trade Lab")
  app/config.py             ← STATIC_DIR, LOCAL_DB, ROOT_STOCK_DB
  static/index.html         ← /hs/static/styles.css, /hs/static/app.js 로 경로 수정
  static/app.js             ← const API_BASE = "/hs" 추가 (API 경로 prefix)
  static/styles.css         ← 독립 light-theme CSS
```

#### main.py 마운트
```python
# hs_trade_lab 서브 앱 마운트 (main.py 하단 SPA 정적 서빙 위에 추가)
try:
    from hs_trade_lab.app.main import app as _hs_app
    app.mount("/hs", _hs_app)
except Exception as _e:
    logging.getLogger(__name__).warning(f"hs_trade_lab 마운트 실패: {_e}")
```

#### Vite 프록시 추가 (vite.config.js)
```js
// ❌ 기존: /api 만 프록시 → /hs/ 요청이 Vite SPA fallback으로 처리됨 → React 앱이 iframe 안에 렌더링
// ✅ 수정: /hs 도 백엔드로 프록시
proxy: {
  '/api': { target: 'http://localhost:8000', changeOrigin: true },
  '/hs':  { target: 'http://localhost:8000', changeOrigin: true },  // 신규
}
```

#### App.jsx 탭 추가
```jsx
// navItems에 추가 (backtest 아래)
{ key: 'hs_trade', icon: <Globe size={17} style={{color:'#0b6e4f'}} />, label: '📦 수출입 분석' },

// 렌더링 (activeTab 조건부)
{activeTab === 'hs_trade' && (
  <iframe src="/hs/" title="수출입 분석"
    style={{width:'100%', height:'calc(100vh - 60px)', border:'none', display:'block'}}
  />
)}
```

#### 핵심 기능 (hs_trade_lab)
- 7개 섹터 탭 UI (반도체, 2차전지, 바이오 등)
- 섹터별 수출입 트렌드 SVG 차트
- HS코드 → 기업 매핑 (수동)
- HS코드 → 섹터 매핑 (수동)
- KRX 공식통계 데이터 자동 수집/갱신
- OpenAI 기반 섹터 요약 생성

---

### 2. 계좌현황(포트폴리오) 페이지 다중 수정

#### 2-1. 수급 데이터 표시 수정
```python
# ❌ 기존: round(sum(...), 0) → 소형주 0.14억이 0으로 반올림됨
# ✅ 수정: round(x, 1) + None sentinel 사용
frn_net = inst_net = None  # None=데이터없음, 0=있지만0
_valid_sup = [r for r in _sups if (r[1] or 0)!=0 or (r[2] or 0)!=0 or (r[3] or 0)!=0 or (r[4] or 0)!=0][:5]
if _valid_sup:
    frn_net = round(sum(_to_억_sup(r[1], r[3], r[0]) for r in _valid_sup), 1)
    inst_net = round(sum(_to_억_sup(r[2], r[4], r[0]) for r in _valid_sup), 1)
```

```jsx
// ✅ 프론트: null 체크 + 소수점 표시
const frn  = h.frn_net_buy;   // null=데이터없음, 0=있지만0
const inst = h.inst_net_buy;
if(frn == null && inst == null) return <span>-</span>;
const fmt = (v) => {
  if(v == null) return <span>-</span>;
  if(v === 0) return <span>±0</span>;
  const abs = Math.abs(v);
  const disp = abs < 10 ? abs.toFixed(1) : Math.round(abs).toLocaleString();
  return <span style={{color:v>0?'#ef4444':'#3b82f6'}}>{v>0?'+':'-'}{disp}억</span>;
};
```

#### 2-2. 대차잔고 비교 로직 수정
```jsx
// ❌ 기존: 5일 vs 직전5일 비교 → 5일>30일인데 녹색 표시
// ✅ 수정: 5일/10일은 30일 기준선과 비교, 30일은 직전30일과 비교
const lights = [
  {label:'5일',  curr:sd.bal5,  prev:sd.bal30},       // 5일 vs 30일 기준선
  {label:'10일', curr:sd.bal10, prev:sd.bal30},        // 10일 vs 30일 기준선
  {label:'30일', curr:sd.bal30, prev:sd.bal30_prev},   // 30일 vs 직전30일
];
// curr > prev → 증가추세 → 🔴 red
// curr < prev → 감소추세 → 🟢 green
```

#### 2-3. 전일 대비 손익 월요일 버그 수정
```python
# ❌ 기존: yesterday = today - 1day → 월요일에 일요일 snapshot 조회 → 0
# ✅ 수정: 가장 최근 snapshot 날짜 사용
_today_iso = _date_cls.today().isoformat()
_last_snap_date = db.query(_sqlfunc.max(models.PortfolioSnapshot.snapshot_date)).filter(
    models.PortfolioSnapshot.snapshot_date < _today_iso
).scalar()
prev_snaps = {s.stock_code: s for s in db.query(models.PortfolioSnapshot).filter(
    models.PortfolioSnapshot.snapshot_date == _last_snap_date
).all()} if _last_snap_date else {}
```

#### 2-4. 컬럼명 변경
- "신호" → "추세추종 신호"
- "수급(외/기)" → "5일수급(외/기)"

---

### 3. 추세추종 신호 NameError 수정
```python
# ❌ 문제: supply 섹션에서 import sqlite3 as _sl3 → _sl3_sup로 rename
#          trade signal 섹션에서 여전히 _sl3 사용 → NameError → except:pass → 모두 "보유"
# ✅ 수정:
_sc3 = _sl3_sup.connect("stock.db")  # _sl3 → _sl3_sup
```

---

### 4. 진입트리거 TOP20 페이지 신규

#### 백엔드 API: GET /api/trigger-ranking
```python
# 동작: 추세추종 스크리너(trend_candidates) 전체 → 진입 트리거 점수 순 정렬 → TOP20
# 반환 필드:
{
  "stock_code", "stock_name", "market", "close",
  "change_pct",        # 당일 등락률
  "score",             # 진입 트리거 총점 (/17)
  "score_detail",      # 세부 점수 [s2, s3, s4]
  "frn_today",         # 당일 외국인 수급
  "inst_today",        # 당일 기관 수급
  "frn_5d",            # 5일 외국인 누적
  "inst_5d",           # 5일 기관 누적
  "bal5", "bal10", "bal30",  # 대차잔고
  "pbr", "per",        # 밸류에이션
  "mktcap",            # 시총(억원)
  "combo_count",       # AI 스크리너 통과 개수 (1~3)
  "value_score",       # 가치스크리너 점수
  "fin_score",         # 재무스크리너 점수
}
# 캐시: _signal_cache['trigger_ranking'] 1시간
```

#### 프론트엔드 (App.jsx Screener 컴포넌트 내 탭)
```jsx
// AI 종목 탭 아래 새 탭 "🎯 진입트리거 TOP20"
// ScorePill 컴포넌트: 점수→색상 (녹색/주황/회색)
// BorCell 컴포넌트: 대차잔고 신호등
// 표시 컬럼: 순위, 종목명, 현재가, 등락, 진입점수, AI점수 3개,
//           당일/5일수급, 대차잔고, PBR/PER, 시총
```

---

### 5. 0원/음수 가격 데이터 수정

#### 발견 및 수정 (에이엘티 등)
```bash
# 0원 이하 레코드 조회
sqlite3 stock.db "SELECT stock_code, date, close FROM price_history WHERE close<=0 ORDER BY stock_code, date;"
# → 32건 발견 (에이엘티 등 금요일 데이터)

# 삭제 후 yfinance로 재수집
sqlite3 stock.db "DELETE FROM price_history WHERE stock_code=? AND close<=0"
# 재수집: yfinance period='1mo' 백필
```

---

### 6. 포트폴리오 KIS 수급 일괄 수집

#### 싸이맥스/지투지바이오/에스에이엠티 등 수급 없음 수정
```python
# 원인: 일부 종목은 yfinance로만 가격 수집 → KIS 수급 API 미호출
# 수정: 포트폴리오 전체 27종목에 KIS get_investor_trends_bulk() 일괄 호출
# 결과: 30일치 수급 데이터 저장 완료
```

---

### 7. 가격 백필 (5년 백테스트용)

```bash
# backfill_bt5y.py 실행 결과:
# 2,340개 종목 2019-01-01 이전 데이터 보유
# 1,976개 종목 백테스트 준비 완료
python3 backfill_bt5y.py
```

---

### 8. Peak Easy 수익률 0% 수정 (장중)

```python
# ❌ 원인: _minute_price_loop이 models.Portfolio 종목만 1분마다 현재가 갱신
#          peak_holding 전용 종목(포트폴리오에 없는 종목)은 전일 종가 고정
# ✅ 수정: peak_holding 활성 종목도 1분 루프에 포함
codes = set(h.stock_code for h in holdings ...)  # Portfolio

# peak_holding 활성 종목 추가
_ph_conn = sqlite3.connect("stock.db")
_ph_rows = _ph_conn.execute(
    "SELECT DISTINCT stock_code FROM peak_holding WHERE is_active=1 AND LENGTH(stock_code)=6"
).fetchall()
for (_ph_code,) in _ph_rows:
    if _ph_code and _ph_code.isdigit():
        codes.add(_ph_code)
```

---

### 9. 장중 수급 데이터 소실 수정

#### 9-1. bulk_insert_price_history 수급 보존 (crud.py)
```python
# ❌ 원인: 오늘 레코드 DELETE 후 inst_net_buy=0, frn_net_buy=0 으로 재삽입
#          → 1분마다 수급 데이터가 0으로 초기화됨
# ✅ 수정: 삭제 전 기존 수급값 읽어서 새 값이 0이면 기존값 보존
existing_sup = db.execute(
    text("SELECT inst_net_buy, frn_net_buy FROM price_history WHERE stock_code=:code AND date LIKE :pat"),
    ...
).fetchone()
if existing_sup:
    if not best.get("inst_net_buy"):
        best["inst_net_buy"] = existing_sup[0] or 0.0
    if not best.get("frn_net_buy"):
        best["frn_net_buy"]  = existing_sup[1] or 0.0
# 이후 DELETE + INSERT
```

#### 9-2. _update_supply race-condition 수정 (main.py)
```python
# ❌ 원인: _update_supply와 _bg_collect 동시 실행 →
#          _update_supply가 먼저 완료 시 price_history 레코드 없어 업데이트 실패
# ✅ 수정: 즉시 1회 + 20초 대기 후 재시도
def _update_supply(code):
    _do_update(c2)   # 즉시 1회
    _tm.sleep(20)    # _bg_collect 완료 대기
    _do_update(c2)   # 재시도
```

---

### 10. 대차잔고 새벽 배치 수정 (cron_3am.sh)

```bash
# ❌ 원인: 오전 3시에 date.today() = 오늘 → KRX 데이터 없음(장 미개장) → 수집 실패
# ✅ 수정: 전일 날짜로 수집
PREV_DATE=$(date -v-1d '+%Y%m%d')  # macOS
$PYTHON public_data_collector.py --date "$PREV_DATE"
```

```bash
# April 3~7 누락 데이터 백필 완료
python3 public_data_collector.py --date 20260403  # 1,000+건
python3 public_data_collector.py --date 20260404
python3 public_data_collector.py --date 20260407
# 결과: 2,710개 종목 April 1~7 대차잔고 정상화
```

---

### 18차 새 원칙 추가

#### 원칙 10. hs_trade_lab Vite 프록시
```
❌ /hs 프록시 없으면 iframe 안에 메인 React 앱이 렌더링됨 (이중 사이드바 버그)
✅ vite.config.js: proxy에 '/hs': { target: 'http://localhost:8000' } 반드시 포함
```

#### 원칙 11. bulk_insert_price_history 수급 보존
```
❌ _realtime_fetch_price(inst_net_buy=0.0)로 호출 → 기존 수급 초기화
✅ crud.py에서 기존 수급값 읽어 보존 처리됨 (수정 완료)
```

#### 원칙 12. _minute_price_loop — peak_holding 포함
```
❌ Portfolio 종목만 1분 루프 → peak_holding 전용 종목 수익률 0%
✅ peak_holding 활성 종목도 codes set에 추가 (수정 완료)
```

#### 원칙 13. cron_3am.sh 전일 날짜
```
❌ date.today()로 KRX 조회 → 오전 3시는 장 미개장 → 데이터 없음
✅ date -v-1d 전일 날짜로 수집 (수정 완료)
```

---

### 18차 cron 전체 목록 (최신)

```bash
5 0 * * *      source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 backfill_financials.py
30 18 * * 1-5  source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 public_data_collector.py
0 7 * * 1      source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 public_data_collector.py --company-only
30 8 * * *     source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 telegram_collector.py
0 9,21 * * *   source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 telegram_monitor.py
0 8 * * 6      source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 weekly_price_collect.py
0 6 * * 2-6    source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 -c "나스닥/S&P500 Yahoo 수집"
0 17 * * 1-5   source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 collect_peak_prices.py
0 3 * * 1-5    /Volumes/Realtek_NVME/stock_dashboard/runtime/cron_3am.sh  ← 전일 날짜로 공공데이터 수집
```

---

### 18차 다음 세션 TODO

- [ ] hs_trade_lab: KRX 공식통계 API키 설정 및 데이터 초기 수집
- [ ] 재무제표 361건 재수집 (DART API 한도 초기화 후)
- [ ] 대차잔고 수집 대상 전체 종목으로 확대 (현재 약 2,710종목)
- [ ] 진입트리거 TOP20 페이지 실시간 새로고침 인터벌 UI 추가
