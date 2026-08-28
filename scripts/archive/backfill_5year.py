"""
backfill_5year.py — 전종목 5년치 주가 데이터 Yahoo Finance 수집

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 backfill_5year.py [--limit 100]  # limit: 테스트용 종목 수 제한
"""

import sqlite3
import time
import argparse
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "stock.db")


def get_stock_list(conn, limit=None):
    """price_history 테이블의 6자리 한국 종목코드 목록 반환 (stock_universe에서 시장 정보 포함)."""
    rows = conn.execute("""
        SELECT DISTINCT ph.stock_code,
               COALESCE(su.stock_name, sm.stock_name, ph.stock_code) AS stock_name,
               su.market
        FROM price_history ph
        LEFT JOIN stock_universe su ON ph.stock_code = su.stock_code
        LEFT JOIN stock_meta sm ON ph.stock_code = sm.stock_code
        WHERE LENGTH(ph.stock_code) = 6
          AND ph.stock_code GLOB '[0-9]*'
        GROUP BY ph.stock_code
        ORDER BY ph.stock_code
    """).fetchall()

    if limit:
        rows = rows[:limit]
    return rows


def get_ticker(stock_code: str, market_type: str) -> list:
    """종목 코드에서 시도할 야후 파이낸스 티커 목록 반환."""
    market_type = (market_type or '').upper()
    if '코스닥' in market_type or 'KOSDAQ' in market_type or 'KQ' in market_type:
        return [f"{stock_code}.KQ", f"{stock_code}.KS"]
    else:
        # KOSPI 우선, 없으면 KOSDAQ 시도
        return [f"{stock_code}.KS", f"{stock_code}.KQ"]


def fetch_price_data(ticker: str):
    """yfinance로 5년치 일봉 데이터 다운로드."""
    try:
        import yfinance as yf
        df = yf.download(ticker, period="5y", interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # yfinance 최신 버전: MultiIndex 컬럼 → 1단계 flatten
        if hasattr(df.columns, 'levels'):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        return df
    except Exception:
        return None


def insert_price_data(conn, stock_code: str, df) -> int:
    """DataFrame을 price_history에 INSERT OR IGNORE로 저장. 삽입된 행 수 반환."""
    if df is None or df.empty:
        return 0

    inserted = 0
    cursor = conn.cursor()

    for idx, row in df.iterrows():
        try:
            date_str = str(idx)[:10]  # YYYY-MM-DD
            open_v   = float(row['Open'])   if 'Open'   in row and row['Open']   == row['Open'] else None
            high_v   = float(row['High'])   if 'High'   in row and row['High']   == row['High'] else None
            low_v    = float(row['Low'])    if 'Low'    in row and row['Low']    == row['Low']  else None
            close_v  = float(row['Close'])  if 'Close'  in row and row['Close']  == row['Close'] else None
            volume_v = int(row['Volume'])   if 'Volume' in row and row['Volume'] == row['Volume'] else None

            if close_v is None or close_v <= 0:
                continue

            cursor.execute("""
                INSERT OR IGNORE INTO price_history
                    (stock_code, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (stock_code, date_str, open_v, high_v, low_v, close_v, volume_v))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception:
            continue

    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="5년치 주가 데이터 Yahoo Finance 수집")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 종목 수 제한")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("종목 목록 조회 중...")
    stocks = get_stock_list(conn, limit=args.limit)
    total = len(stocks)
    print(f"대상 종목: {total}개\n")

    total_new_rows = 0
    updated_count  = 0

    for idx, (stock_code, stock_name, market_type) in enumerate(stocks, 1):
        tickers = get_ticker(stock_code, market_type)
        new_rows = 0

        for ticker in tickers:
            df = fetch_price_data(ticker)
            if df is not None and not df.empty:
                new_rows = insert_price_data(conn, stock_code, df)
                break

        if new_rows > 0:
            updated_count += 1

        total_new_rows += new_rows

        # 진행 상황 출력 (100개마다)
        if idx % 100 == 0 or idx == total:
            pct = idx / total * 100
            print(f"진행: {idx}/{total} ({pct:.1f}%) - {stock_code} {stock_name} → {new_rows}건")

        # Rate limiting
        time.sleep(0.2)

    conn.close()

    print("\n" + "=" * 60)
    print(f"[완료] 업데이트 종목: {updated_count}/{total}개")
    print(f"[완료] 신규 삽입 행: {total_new_rows:,}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
