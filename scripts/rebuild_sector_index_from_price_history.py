from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"

VALID_SECTORS = {
    "IT",
    "경기소비재",
    "금융",
    "산업재",
    "소재",
    "에너지",
    "의료",
    "통신서비스",
    "필수소비재",
    "유틸리티",
}


def _parse_day(value: str) -> datetime:
    value = value.replace("-", "")
    return datetime.strptime(value, "%Y%m%d")


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _latest_full_price_date(conn: sqlite3.Connection, min_coverage: int = 2000) -> str:
    row = conn.execute(
        """
        SELECT date
        FROM price_history
        GROUP BY date
        HAVING COUNT(DISTINCT stock_code) >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (min_coverage,),
    ).fetchone()
    if not row:
        raise RuntimeError("No full price_history date found")
    return str(row["date"])[:10]


def _default_start(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(date) AS max_date FROM sector_index_daily").fetchone()
    if row and row["max_date"]:
        start = _parse_day(str(row["max_date"])) + timedelta(days=1)
        return _iso(start)
    row = conn.execute(
        """
        SELECT MIN(date) AS min_date
        FROM price_history
        GROUP BY date
        HAVING COUNT(DISTINCT stock_code) >= 2000
        ORDER BY date
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise RuntimeError("No sector start date available")
    return str(row["min_date"])[:10]


def _market(value: str | None) -> str | None:
    text = str(value or "").upper()
    if "KOSDAQ" in text or "코스닥" in text:
        return "KOSDAQ"
    if "KOSPI" in text or "유가" in text or "거래소" in text:
        return "KOSPI"
    return None


def _load_universe(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT stock_code, market, sector_large, market_cap
        FROM stock_universe
        WHERE stock_code IS NOT NULL
          AND LENGTH(stock_code) = 6
          AND COALESCE(stock_type, '') NOT IN ('ETF', 'ETN')
          AND COALESCE(secugrp_nm, '') NOT IN ('ETF', 'ETN')
          AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
          AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
        """
    ).fetchall()
    universe: dict[str, dict] = {}
    for row in rows:
        sector = str(row["sector_large"] or "").strip()
        if not sector or sector.lower() == "nan" or sector not in VALID_SECTORS:
            continue
        market = _market(row["market"])
        if not market:
            continue
        universe[str(row["stock_code"]).zfill(6)] = {
            "market": market,
            "sector": sector,
            "market_cap": float(row["market_cap"] or 0),
        }
    return universe


def _seed_previous_sector_close(conn: sqlite3.Connection, start: str) -> dict[tuple[str, str], float]:
    rows = conn.execute(
        """
        SELECT market, sector, close
        FROM sector_index_daily
        WHERE date = (
            SELECT MAX(date)
            FROM sector_index_daily
            WHERE date < ?
        )
        """,
        (start,),
    ).fetchall()
    return {
        (str(row["market"]), str(row["sector"])): float(row["close"] or 1000)
        for row in rows
    }


def rebuild(start: str | None = None, end: str | None = None, min_price_coverage: int = 2000) -> dict:
    conn = _connect()
    try:
        if not end:
            end = _latest_full_price_date(conn, min_price_coverage)
        if not start:
            start = _default_start(conn)

        universe = _load_universe(conn)
        if not universe:
            raise RuntimeError("No stock_universe sector mapping available")

        lookback = (_parse_day(start) - timedelta(days=14)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT stock_code, date, close, open, high, low, volume
            FROM price_history
            WHERE date >= ? AND date <= ?
              AND close IS NOT NULL AND close > 0
            ORDER BY date, stock_code
            """,
            (lookback, end),
        ).fetchall()

        by_date: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            code = str(row["stock_code"]).zfill(6)
            if code in universe:
                by_date[str(row["date"])[:10]].append(row)

        previous_close: dict[str, float] = {}
        sector_close = _seed_previous_sector_close(conn, start)
        inserted = 0
        skipped_dates = 0
        start_dt = _parse_day(start)
        end_dt = _parse_day(end)

        for day in sorted(by_date):
            day_dt = _parse_day(day)
            day_rows = by_date[day]
            if day_dt < start_dt:
                for row in day_rows:
                    previous_close[str(row["stock_code"]).zfill(6)] = float(row["close"])
                continue
            if day_dt > end_dt:
                break
            if len({str(row["stock_code"]).zfill(6) for row in day_rows}) < min_price_coverage:
                skipped_dates += 1
                continue

            groups: dict[tuple[str, str], list[tuple[float, float, float, float, float, float]]] = defaultdict(list)
            for row in day_rows:
                code = str(row["stock_code"]).zfill(6)
                meta = universe.get(code)
                prev = previous_close.get(code)
                close = float(row["close"] or 0)
                if not meta or not prev or prev <= 0 or close <= 0:
                    previous_close[code] = close
                    continue
                weight = max(float(meta["market_cap"] or 0), 1.0)
                ret = close / prev - 1.0
                open_ret = float(row["open"] or close) / prev - 1.0
                high_ret = float(row["high"] or close) / prev - 1.0
                low_ret = float(row["low"] or close) / prev - 1.0
                volume = float(row["volume"] or 0)
                groups[(meta["market"], meta["sector"])].append((ret, open_ret, high_ret, low_ret, weight, volume))
                previous_close[code] = close

            for (market, sector), vals in groups.items():
                if len(vals) < 3:
                    continue
                weight_sum = sum(v[4] for v in vals) or float(len(vals))
                avg = sum(v[0] * v[4] for v in vals) / weight_sum
                avg_open = sum(v[1] * v[4] for v in vals) / weight_sum
                avg_high = sum(v[2] * v[4] for v in vals) / weight_sum
                avg_low = sum(v[3] * v[4] for v in vals) / weight_sum
                prev_sector_close = sector_close.get((market, sector), 1000.0)
                close_idx = prev_sector_close * (1.0 + avg)
                open_idx = prev_sector_close * (1.0 + avg_open)
                high_idx = prev_sector_close * (1.0 + avg_high)
                low_idx = prev_sector_close * (1.0 + avg_low)
                change = close_idx - prev_sector_close
                change_rate = avg * 100.0
                volume = sum(v[5] for v in vals)
                conn.execute(
                    """
                    INSERT INTO sector_index_daily
                    (date, market, sector, close, change_, change_rate, open_, high, low, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, market, sector) DO UPDATE SET
                        close=excluded.close,
                        change_=excluded.change_,
                        change_rate=excluded.change_rate,
                        open_=excluded.open_,
                        high=excluded.high,
                        low=excluded.low,
                        volume=excluded.volume
                    """,
                    (
                        day,
                        market,
                        sector,
                        round(close_idx, 4),
                        round(change, 4),
                        round(change_rate, 4),
                        round(open_idx, 4),
                        round(high_idx, 4),
                        round(low_idx, 4),
                        volume,
                    ),
                )
                sector_close[(market, sector)] = close_idx
                inserted += 1
            conn.commit()

        return {
            "ok": True,
            "start": start,
            "end": end,
            "inserted_or_updated": inserted,
            "skipped_dates": skipped_dates,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild derived sector_index_daily from local OHLCV.")
    parser.add_argument("--start", help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--end", help="YYYY-MM-DD or YYYYMMDD")
    parser.add_argument("--min-price-coverage", type=int, default=2000)
    args = parser.parse_args()

    start = _iso(_parse_day(args.start)) if args.start else None
    end = _iso(_parse_day(args.end)) if args.end else None
    print(rebuild(start=start, end=end, min_price_coverage=args.min_price_coverage))


if __name__ == "__main__":
    main()
