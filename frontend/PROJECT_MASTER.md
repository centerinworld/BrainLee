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
