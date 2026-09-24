"""Thin, cached client for FinanceData/marcap (KRX daily market-cap dataset,
1995-present, Close/Volume/Marcap/Stocks per stock per trading day).

Added 2026-09-21 after GitHub research (user request: "pykrx/FinanceDataReader
같이 우리 시스템에 도움이 되는게 있다면 더 추가") turned up a concrete gap this
fills: pykrx and FinanceDataReader both apply split-back-adjustment even to
old dates, which made 10 `invalid_ohlcv` rows from 2011-2013 look like basis
mismatches against our own raw price_history (see hermes.md, "invalid_ohlcv
38건 중 27건 완결"). marcap's own `Close` column is NOT retroactively adjusted
(confirmed: it matches our raw price_history exactly for all 8 stocks/dates in
that investigation), and its `Stocks` (shares outstanding) column lets real
share-count corporate actions be detected directly - independent of any
adjustment algorithm's own correctness. This is the same technique the
existing `stock_price_daily_shares` detector in
scripts/build_corporate_action_adjustment_engine.py uses against our own
`stock_price_daily` table; marcap extends that back to 1995, well before our
own shares-outstanding tracking starts.

The dataset itself lives in the marcap GitHub repo as one parquet file per
year (~15-20MB each) under data/marcap-{year}.parquet - not a pip package.
This client downloads and caches individual year files on first use rather
than cloning the ~1.8GB full repo.
"""
from __future__ import annotations

import functools
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent / "data_cache" / "marcap"
RAW_URL = "https://github.com/FinanceData/marcap/raw/master/data/marcap-{year}.parquet"


def _year_file(year: int) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"marcap-{year}.parquet"


def ensure_year(year: int, *, force: bool = False) -> Path:
    """Download one year's parquet file into the local cache if not already present."""
    path = _year_file(year)
    if path.exists() and not force:
        return path
    resp = requests.get(RAW_URL.format(year=year), timeout=60)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


@functools.lru_cache(maxsize=32)
def _load_year(year: int) -> pd.DataFrame:
    path = ensure_year(year)
    df = pd.read_parquet(path)
    df["Date"] = df["Date"].astype(str)
    return df


def marcap_data(start: str, end: str, code: str | None = None) -> pd.DataFrame:
    """Same shape/intent as marcap's own `marcap_data(start, end, code=...)` helper,
    reimplemented here against the per-year cache instead of the full repo clone."""
    start_year, end_year = int(start[:4]), int(end[:4])
    frames = [_load_year(y) for y in range(start_year, end_year + 1)]
    full = pd.concat(frames, ignore_index=True)
    mask = (full["Date"] >= start) & (full["Date"] <= end)
    if code:
        mask &= full["Code"] == code
    return full.loc[mask].sort_values("Date").reset_index(drop=True)


def shares_history(code: str, start_year: int, end_year: int) -> pd.DataFrame:
    """Date/Close/Stocks series for one stock, for cheap discontinuity scanning."""
    df = marcap_data(f"{start_year}-01-01", f"{end_year}-12-31", code=code)
    return df[["Date", "Close", "Stocks"]].reset_index(drop=True)
