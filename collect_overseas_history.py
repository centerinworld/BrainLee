"""
collect_overseas_history.py — 해외 주요 종목 20년치 히스토리 다운로드

대상:
  • 해외 개별 종목 (Logic #6 동조효과 분석용) — 32종목
  • 글로벌 주요 지수 — S&P500, NASDAQ, Dow, Nikkei, TAIEX, SOX (반도체)

특이사항:
  • 저장 대상: price_history (stock_code, date, open/high/low/close, volume)
  • yfinance 사용 (무료, 15분 지연)
  • 서버에서 403 차단 시 재시도 3회 + exponential backoff
  • 이미 있는 날짜는 SKIP (ON CONFLICT DO NOTHING)
  • 실행: python collect_overseas_history.py [--start 2005-01-01] [--symbols NVDA AMD]

실행 시간: 종목당 ~3초 → 약 40종목 × 3초 = 2분
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DB_PATH = Path(__file__).parent / "stock.db"

# ── 수집 대상 ─────────────────────────────────────────────────────
# 개별 종목 (시장 동조효과 분석용)
INDIVIDUAL_STOCKS = [
    # 반도체
    "NVDA", "AMD", "AVGO", "MU", "AMAT", "LRCX", "KLAC", "TSM", "8035.T",
    # 2차전지
    "TSLA", "ALB", "LTHM", "6981.T",
    # 전력인프라
    "ETN", "VRT", "PWR", "HUBB",
    # 제약/바이오
    "LLY", "NVO", "ABBV", "MRNA",
    # 방산
    "LMT", "NOC", "RTX", "HII",
    # 해운
    "ZIM", "STNG", "SBLK",
    # 에너지/소재
    "XOM", "CVX", "FCX", "RIO",
]

# 글로벌 지수 (동조효과 기준점)
INDEX_SYMBOLS = [
    "^GSPC",   # S&P 500
    "^IXIC",   # NASDAQ Composite
    "^DJI",    # Dow Jones
    "^SOX",    # Philadelphia Semiconductor Index (반도체 선행지표)
    "^N225",   # Nikkei 225
    "^TWII",   # TAIEX (대만 가권지수)
    "^HSI",    # Hang Seng (홍콩)
    "^STOXX50E",  # Euro Stoxx 50
]

ALL_SYMBOLS = INDIVIDUAL_STOCKS + INDEX_SYMBOLS

# 섹터 메타 (정보 참고용 — price_history에는 symbol만 저장)
SYMBOL_META = {
    "NVDA": "반도체", "AMD": "반도체", "AVGO": "반도체", "MU": "반도체",
    "AMAT": "반도체장비", "LRCX": "반도체장비", "KLAC": "반도체장비",
    "TSM": "반도체", "8035.T": "반도체장비",
    "TSLA": "2차전지", "ALB": "2차전지", "LTHM": "2차전지", "6981.T": "2차전지",
    "ETN": "전력장비", "VRT": "전력장비", "PWR": "전력장비", "HUBB": "전력장비",
    "LLY": "제약", "NVO": "제약", "ABBV": "제약", "MRNA": "바이오",
    "LMT": "방산", "NOC": "방산", "RTX": "방산", "HII": "방산",
    "ZIM": "해운", "STNG": "해운", "SBLK": "해운",
    "XOM": "에너지", "CVX": "에너지", "FCX": "소재", "RIO": "소재",
    "^GSPC": "지수", "^IXIC": "지수", "^DJI": "지수", "^SOX": "지수",
    "^N225": "지수", "^TWII": "지수", "^HSI": "지수", "^STOXX50E": "지수",
}


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def _download_one(sym: str, start: str, end: str, retries: int = 3) -> list[dict]:
    """yfinance로 단일 종목 다운로드. 실패 시 재시도."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance 미설치: pip install yfinance")
        return []

    for attempt in range(retries):
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(start=start, end=end, auto_adjust=True)
            if df is None or df.empty:
                logger.warning(f"[{sym}] 데이터 없음 (attempt {attempt+1})")
                time.sleep(2 ** attempt)
                continue

            rows = []
            for ts, row in df.iterrows():
                try:
                    d = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
                    cl = float(row.get("Close", 0) or 0)
                    if cl <= 0:
                        continue
                    rows.append({
                        "date":   d.isoformat(),
                        "open":   float(row.get("Open",   0) or 0),
                        "high":   float(row.get("High",   0) or 0),
                        "low":    float(row.get("Low",    0) or 0),
                        "close":  cl,
                        "volume": float(row.get("Volume", 0) or 0),
                    })
                except Exception as e:
                    logger.debug(f"[{sym}] 행 파싱 오류: {e}")
            return rows

        except Exception as e:
            logger.warning(f"[{sym}] attempt {attempt+1} 실패: {e}")
            time.sleep(2 ** attempt)

    return []


def _get_existing_dates(conn: sqlite3.Connection, sym: str, start: str) -> set[str]:
    rows = conn.execute(
        "SELECT date FROM price_history WHERE stock_code=? AND date>=?",
        (sym, start)
    ).fetchall()
    return {r[0] for r in rows}


def _save_rows(conn: sqlite3.Connection, sym: str, rows: list[dict], existing: set[str]) -> int:
    saved = 0
    for r in rows:
        if r["date"] in existing:
            continue
        try:
            conn.execute("""
                INSERT INTO price_history
                    (stock_code, date, open, high, low, close, volume,
                     inst_net_buy, frn_net_buy, ind_net_buy,
                     inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt)
                VALUES (?,?,?,?,?,?,?,0,0,0,0,0,0)
                ON CONFLICT(stock_code, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high,
                    low=excluded.low, close=excluded.close,
                    volume=excluded.volume
            """, (sym, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
            saved += 1
        except Exception as e:
            logger.debug(f"[{sym}] INSERT 오류: {e}")
    return saved


def collect_history(
    symbols: list[str] | None = None,
    start_date: str = "2005-01-01",
    end_date: str | None = None,
) -> dict[str, int]:
    """
    지정 종목의 역사적 가격 데이터를 price_history에 저장.
    Returns: {symbol: saved_rows}
    """
    symbols  = symbols or ALL_SYMBOLS
    end_date = end_date or date.today().isoformat()

    conn = _db()
    result: dict[str, int] = {}

    logger.info(f"수집 시작 — {len(symbols)}종목, {start_date}~{end_date}")

    for sym in symbols:
        try:
            existing = _get_existing_dates(conn, sym, start_date)
            rows     = _download_one(sym, start_date, end_date)
            saved    = _save_rows(conn, sym, rows, existing)
            conn.commit()
            result[sym] = saved
            logger.info(f"[{sym:12s}] 총 {len(rows):5d}행 수신 → {saved:5d}행 신규 저장"
                        f"  (기존 {len(existing)}행)")
        except Exception as e:
            logger.error(f"[{sym}] 오류: {e}")
            result[sym] = 0
        time.sleep(0.8)   # rate limit

    conn.close()
    total = sum(result.values())
    logger.info(f"완료 — 총 {total:,}행 신규 저장")
    return result


def check_coverage() -> None:
    """현재 DB에서 각 종목별 데이터 현황 출력."""
    conn = _db()
    print(f"\n{'Symbol':15s} {'Min Date':12s} {'Max Date':12s} {'Count':8s} {'Sector'}")
    print("-" * 65)
    for sym in ALL_SYMBOLS:
        row = conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(*) FROM price_history WHERE stock_code=?", (sym,)
        ).fetchone()
        if row and row[2]:
            print(f"{sym:15s} {row[0]:12s} {row[1]:12s} {row[2]:8,d}  {SYMBOL_META.get(sym,'')}")
        else:
            print(f"{sym:15s} {'(없음)':12s} {'(없음)':12s} {'0':>8s}  {SYMBOL_META.get(sym,'')}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="해외 주가 20년치 히스토리 다운로드")
    parser.add_argument("--start",   default="2005-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end",     default=None,          help="종료일 (기본: 오늘)")
    parser.add_argument("--symbols", nargs="*",             help="종목 지정 (없으면 전체)")
    parser.add_argument("--check",   action="store_true",   help="현황만 출력")
    args = parser.parse_args()

    if args.check:
        check_coverage()
    else:
        collect_history(
            symbols=args.symbols or None,
            start_date=args.start,
            end_date=args.end,
        )
