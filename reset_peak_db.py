"""
reset_peak_db.py — Peak DB 정리 + 재수집

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 reset_peak_db.py

처리 내용:
  1. PeakHolding / PeakTrade 전체 삭제 (hold_days=0 포함)
  2. 스탁이지 Peak 페이지 재스크래핑
  3. 현재 보유종목 전체 DB에 새로 저장
  4. 결과 출력
"""

import sys, json, os
sys.path.insert(0, '/Applications/stock_dashboard')

import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime

# ── DB 직접 연결 ──────────────────────────────────────────────
from database import SessionLocal
import models

db = SessionLocal()

# ── Step 1: 기존 데이터 전체 삭제 ────────────────────────────
print("=" * 55)
print("Step 1: 기존 Peak 데이터 삭제")
print("=" * 55)

deleted_trades   = db.query(models.PeakTrade).delete()
deleted_holdings = db.query(models.PeakHolding).delete()
db.commit()
print(f"  PeakTrade   삭제: {deleted_trades}건")
print(f"  PeakHolding 삭제: {deleted_holdings}건")

# ── Step 2: 스탁이지 스크래핑 ────────────────────────────────
print("\nStep 2: 스탁이지 Peak 페이지 스크래핑")
print("=" * 55)

import config

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer":    "https://stockeasy.intellio.kr/",
})

# 세션 쿠키 로드
SESSION_FILE = '/Applications/stock_dashboard/stockeasy_session.json'
if os.path.exists(SESSION_FILE):
    with open(SESSION_FILE) as f:
        for k, v in json.load(f).items():
            s.cookies.set(k, v)
    print("  세션 쿠키 로드 완료")
else:
    # 새로 로그인
    r_login = s.post(
        "https://stockeasy.intellio.kr/api/auth/login",
        json={"email": config.STOCKEASY_EMAIL, "password": config.STOCKEASY_PASSWORD},
        headers={"Content-Type": "application/json"}, timeout=15
    )
    print(f"  로그인 응답: {r_login.status_code} {r_login.text[:100]}")

r = s.get("https://stockeasy.intellio.kr/strategy-room/peak", timeout=15)
print(f"  페이지 상태: {r.status_code} | URL: {r.url}")

# ── Step 3: 파싱 ──────────────────────────────────────────────
print("\nStep 3: 파싱")
print("=" * 55)

soup = BeautifulSoup(r.text, "html.parser")

def _num(txt):
    t = txt.strip().replace(",","").replace("%","").replace("원","").strip()
    try: return float(t)
    except: return 0.0

def _days(txt):
    t = re.sub(r"[^0-9]", "", txt)
    try: return int(t)
    except: return 0

def _date(txt):
    t = txt.strip()
    if "/" in t:
        parts = t.split("/")
        if len(parts) == 2:
            return f"2026-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    return t

def _parse_sector(txt):
    t = txt.strip()
    m = re.match(r'^(.+?)(\d{1,3})$', t)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return t, 0

holdings = []
exits    = []

tables = soup.select("table")
print(f"  테이블 수: {len(tables)}")

for table in tables:
    headers = [th.get_text(strip=True) for th in table.select("th")]
    header_str = " ".join(headers)
    is_holding = "현재가" in header_str
    is_exit    = "매도가" in header_str
    if not is_holding and not is_exit:
        continue

    print(f"\n  {'보유' if is_holding else '이탈'} 테이블 헤더: {headers}")
    rows_data = []
    for row in table.select("tr"):
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if not cells or len(cells) < 5:
            continue
        if cells[0] in ("섹터", "종목명"):
            continue

        if len(cells) >= 7:
            sector_raw = cells[0]
            stock_name = cells[1]
            buy_price  = _num(cells[2])
            price2     = _num(cells[3])
            entry_date = _date(cells[4])
            hold_days  = _days(cells[5])
            profit_pct = _num(cells[6])
        elif len(cells) == 6:
            sector_raw = ""
            stock_name = cells[0]
            buy_price  = _num(cells[1])
            price2     = _num(cells[2])
            entry_date = _date(cells[3])
            hold_days  = _days(cells[4])
            profit_pct = _num(cells[5])
        else:
            print(f"  스킵 (컬럼수 {len(cells)}): {cells}")
            continue

        sector, score = _parse_sector(sector_raw)

        if not stock_name or stock_name in ("종목명",):
            continue

        row_data = {
            "name": stock_name, "sector": sector, "sector_score": score,
            "buy_price": buy_price, "entry_date": entry_date,
            "hold_days": hold_days, "profit_pct": profit_pct,
        }
        if is_holding:
            row_data["current_price"] = price2
            holdings.append(row_data)
        else:
            row_data["sell_price"] = price2
            exits.append(row_data)

        print(f"  [{stock_name}] 섹터={sector}({score}) 매수={buy_price:,.0f} "
              f"가격2={price2:,.0f} 편입={entry_date} 보유={hold_days}일")

print(f"\n  보유 {len(holdings)}종목 / 이탈 {len(exits)}종목 파싱 완료")

# ── Step 4: DB 저장 ───────────────────────────────────────────
print("\nStep 4: DB 저장")
print("=" * 55)

now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
MAX_BUDGET = 10_000_000

saved = 0
for h in holdings:
    price = h.get("current_price") or h.get("buy_price") or 0
    qty   = int(MAX_BUDGET // price) if price > 0 else 0

    holding = models.PeakHolding(
        stock_name    = h["name"],
        sector        = h["sector"],
        sector_score  = h["sector_score"],
        buy_price     = h["buy_price"],
        current_price = price,
        quantity      = qty,
        entry_date    = h["entry_date"],
        hold_days     = h["hold_days"],
        profit_pct    = h["profit_pct"],
        detected_at   = now_str,
        updated_at    = now_str,
    )
    db.add(holding)
    db.flush()

    db.add(models.PeakTrade(
        holding_id   = holding.id,
        stock_name   = h["name"],
        sector       = h["sector"],
        tx_type      = "buy",
        price        = h["buy_price"],
        quantity     = qty,
        total_amount = round(h["buy_price"] * qty),
        tx_at        = now_str,
    ))
    saved += 1
    print(f"  저장: [{h['name']}] 섹터={h['sector']}({h['sector_score']}) "
          f"매수={h['buy_price']:,.0f} 현재={price:,.0f} 수량={qty}")

db.commit()

# ── 이탈 종목도 기록 ──────────────────────────────────────────
for ex in exits:
    buy_p  = ex["buy_price"]
    sell_p = ex.get("sell_price", buy_p)
    qty    = int(MAX_BUDGET // buy_p) if buy_p > 0 else 0
    profit = round((sell_p - buy_p) * qty)
    pct    = round((sell_p - buy_p) / buy_p * 100, 2) if buy_p else 0

    holding = models.PeakHolding(
        stock_name    = ex["name"],
        sector        = ex["sector"],
        sector_score  = ex["sector_score"],
        buy_price     = buy_p,
        current_price = sell_p,
        sell_price    = sell_p,
        quantity      = qty,
        entry_date    = ex["entry_date"],
        hold_days     = ex["hold_days"],
        profit_pct    = ex["profit_pct"],
        detected_at   = now_str,
        updated_at    = now_str,
        sold_at       = now_str,
    )
    db.add(holding)
    db.flush()

    db.add(models.PeakTrade(
        holding_id   = holding.id,
        stock_name   = ex["name"],
        sector       = ex["sector"],
        tx_type      = "sell",
        price        = sell_p,
        quantity     = qty,
        total_amount = round(sell_p * qty),
        profit       = profit,
        profit_pct   = pct,
        tx_at        = now_str,
    ))
    saved += 1
    print(f"  저장(이탈): [{ex['name']}] 매수={buy_p:,.0f} 매도={sell_p:,.0f} 손익={profit:+,.0f}원")

db.commit()
db.close()

print(f"\n완료! 총 {saved}건 저장 (보유 {len(holdings)} + 이탈 {len(exits)})")
print("\n이제 peak_monitor.py를 재시작하세요:")
print("  python3 peak_monitor.py --once  # 테스트")
print("  python3 peak_monitor.py         # 상시 실행")
