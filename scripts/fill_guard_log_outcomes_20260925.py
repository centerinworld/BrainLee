#!/usr/bin/env python3
"""HANDOFF §11 S3: fill post-hoc outcomes of blocked / shadow-blocked virtual entries in virtual_guard_log.

For rows with price_at_block, ret_5d/20d/60d = close of the Nth trading day AFTER the block date / price_at_block - 1 (in %),
and kospi_ret_* the same for ^KS11 from kospi_close. A horizon stays NULL until N trading days have elapsed, so the job can run
daily and only fills what became available. Answers: "did the guard block entries that would have lost money?"
Idempotent (only NULL columns are filled). --dry-run prints without writing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HORIZONS = (5, 20, 60)


def forward_returns(base: float | None, closes_after: list[float], horizons=HORIZONS) -> dict[int, float | None]:
    """closes_after = closes of the trading days after the block date, ascending. Returns % returns per horizon (None if not elapsed)."""
    out = {}
    for n in horizons:
        if base and base > 0 and len(closes_after) >= n and closes_after[n - 1] and closes_after[n - 1] > 0:
            out[n] = round((closes_after[n - 1] / base - 1) * 100, 4)
        else:
            out[n] = None
    return out


def _closes_after(conn, code: str, day: str, limit: int = 61) -> list[float]:
    rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code=? AND date::text > ? AND close>0 ORDER BY date LIMIT ?",
        (code, day, limit)).fetchall()
    seen, out = set(), []
    for d, c in rows:
        k = str(d)[:10]
        if k not in seen:
            seen.add(k)
            out.append(float(c))
    return out


def main(dry_run: bool = False) -> int:
    from db_compat import connect_primary_db
    conn = connect_primary_db()
    rows = conn.execute(
        "SELECT id, stock_code, CAST(logged_at AS DATE)::text, price_at_block, kospi_close FROM virtual_guard_log "
        "WHERE price_at_block IS NOT NULL AND decision IN ('blocked','shadow_would_block') "
        "AND (ret_5d IS NULL OR ret_20d IS NULL OR ret_60d IS NULL)").fetchall()
    updated = 0
    for rid, code, day, price, kospi in rows:
        stock = forward_returns(price, _closes_after(conn, code, day))
        bench = forward_returns(kospi, _closes_after(conn, "^KS11", day))
        if all(v is None for v in stock.values()):
            continue
        if dry_run:
            print(rid, code, day, stock, bench)
            continue
        conn.execute(
            "UPDATE virtual_guard_log SET "
            "ret_5d=COALESCE(ret_5d,?), ret_20d=COALESCE(ret_20d,?), ret_60d=COALESCE(ret_60d,?), "
            "kospi_ret_5d=COALESCE(kospi_ret_5d,?), kospi_ret_20d=COALESCE(kospi_ret_20d,?), kospi_ret_60d=COALESCE(kospi_ret_60d,?) "
            "WHERE id=?",
            (stock[5], stock[20], stock[60], bench[5], bench[20], bench[60], rid))
        updated += 1
    if not dry_run:
        conn.commit()
    print(f"candidates={len(rows)} updated={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
