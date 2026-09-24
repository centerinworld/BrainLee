#!/usr/bin/env python3
"""Fetch FinanceDataReader (Naver, whole-won) OHLCV for every 6-digit code that still
has fractional OHLC rows in price_history after the marcap repair (mostly ETFs/ETNs,
which marcap does not cover; pykrx is currently returning empty frames for all
tickers, so it cannot be used). Resumable: one parquet per code under
data_cache/fdr_fractional/, skipped if already present. Read-only w.r.t. the DB.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "data_cache" / "fdr_fractional"
START, END = "2019-01-01", "2026-09-24"


def main() -> None:
    import FinanceDataReader as fdr

    OUT.mkdir(parents=True, exist_ok=True)
    conn = connect_primary_db(timeout=120)
    codes = [r[0] for r in conn.execute(
        """SELECT DISTINCT stock_code FROM price_history
           WHERE stock_code ~ '^[0-9]{6}$'
             AND (close<>ROUND(close) OR open<>ROUND(open) OR high<>ROUND(high) OR low<>ROUND(low))
           ORDER BY 1"""
    ).fetchall()]
    conn.close()
    print(f"{len(codes)} codes to fetch", flush=True)
    ok = empty = err = 0
    for i, code in enumerate(codes, 1):
        path = OUT / f"{code}.parquet"
        if path.exists():
            continue
        try:
            df = fdr.DataReader(code, START, END)
            if df is None or df.empty:
                pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"]).to_parquet(path)
                empty += 1
            else:
                df = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
                df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
                df.to_parquet(path)
                ok += 1
        except Exception as exc:  # noqa: BLE001 - keep going, record and retry on next run
            err += 1
            print(f"ERR {code}: {repr(exc)[:120]}", flush=True)
        if i % 25 == 0:
            print(f"[{i}/{len(codes)}] ok={ok} empty={empty} err={err}", flush=True)
        time.sleep(0.3)
    print(f"done ok={ok} empty={empty} err={err}", flush=True)


if __name__ == "__main__":
    main()
