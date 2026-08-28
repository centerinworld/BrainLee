"""
Yahoo Finance 기반 거시지표 수집기 (환율·원자재·지수)
무료, API 키 불필요
"""
import sqlite3, logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

# (yahoo_ticker, our_code)
YAHOO_MACRO = [
    # ── 환율 ──────────────────────────────────────────────
    ("USDKRW=X",  "KR_USD_KRW"),
    ("JPY=X",     "JP_USD_JPY"),
    ("USDCNY=X",  "CN_USD_CNY"),
    ("EURUSD=X",  "EU_EUR_USD"),
    ("DX-Y.NYB",  "US_DXY"),
    # ── 원자재 ────────────────────────────────────────────
    ("CL=F",      "COMM_OIL_WTI"),
    ("BZ=F",      "COMM_OIL_BRENT"),
    ("GC=F",      "COMM_GOLD"),
    ("HG=F",      "COMM_COPPER"),
    ("NG=F",      "COMM_NATURAL_GAS"),
    # ── 주요 지수 ─────────────────────────────────────────
    ("^GSPC",     "US_SP500"),
    ("^VIX",      "US_VIX"),
    ("^KS11",     "KR_KOSPI"),
    ("^N225",     "JP_NIKKEI"),
    ("^FTSE",     "EU_FTSE"),
    ("^GDAXI",    "EU_DAX"),
    # ── 채권 ──────────────────────────────────────────────
    ("^TNX",      "US_10Y_YIELD_YH"),  # Yahoo 10년 국채
]


def collect_yahoo_macro(lookback_days: int = 365) -> int:
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return 0

    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    total = 0

    for ticker, our_code in YAHOO_MACRO:
        try:
            tk = yf.Ticker(ticker)
            df = tk.history(start=start, interval="1d")
            if df.empty:
                continue
            df = df.reset_index()
            rows = []
            for _, row in df.iterrows():
                date = row["Date"].strftime("%Y-%m-%d") if hasattr(row["Date"], "strftime") else str(row["Date"])[:10]
                val = float(row["Close"]) if row["Close"] == row["Close"] else None
                if val is None:
                    continue
                rows.append((date, val))

            rows.sort(key=lambda x: x[0])
            for i, (date, val) in enumerate(rows):
                prev = rows[i - 1][1] if i > 0 else None
                chg = ((val - prev) / abs(prev) * 100) if prev else None
                conn.execute("""
                    INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_code, date) DO UPDATE SET
                        value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
                """, (our_code, date, val, prev, chg))
                total += 1
        except Exception as e:
            logger.warning(f"Yahoo macro [{ticker}] failed: {e}")
            continue

    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"Yahoo macro collected {total} records")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('yahoo_macro', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_yahoo_macro()
    print(f"Yahoo 매크로 수집 완료: {n}건")
