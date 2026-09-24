#!/usr/bin/env python3
"""Second-pass repair of the 2026-03-31~04-07 yfinance auto_adjust incident.

The first pass (apply_bulk_marcap_interpolation_repair_20260923.py) only keyed on a
fractional `close`. A follow-up scan for the new write-guard invariant (6-digit KRX
codes must have whole-won O/H/L/C) found 315,912 more rows in 2,413 codes:
  * rows with an integer close but fractional open/high/low (close matched, OHL did not)
  * ~281K rows in ~715 ETF/ETN codes that marcap (common stocks only) never covered.

Sources (both whole-won, unadjusted for dividends):
  --source marcap : data_cache/marcap/marcap-YYYY.parquet (2019..2026)
  --source fdr    : data_cache/fdr_fractional/<code>.parquet (FinanceDataReader/Naver,
                    fetched by fetch_fdr_history_for_fractional_codes_20260924.py;
                    pykrx returns empty frames for every ticker right now)
Acceptance rules (never invent a value; anything not accepted stays untouched):
  * replacement row must be all-integer, close>0, volume>=0, valid OHLC shape
  * existing row with integer close but fractional O/H/L -> replace only if source close is within
    +-30% of it (a larger gap means a split/other basis difference, not this incident: left for review)
  * existing row with fractional close -> replace (marcap), or for fdr only when the code is
    "anchored": among dates where our own row is already fully integer (a real print),
    >=95% (min 20 rows) match the source close exactly, proving the source is on our raw basis;
    codes without enough anchor rows need every replaced row within +-30% of the adjusted value.
Backup+update are server-side set operations; every batch is logged to price_history_fix_backup
and data_fix_log. --apply to write; default is a dry run that reports counts.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script  # noqa: E402

BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS price_history_fix_backup (
  run_id TEXT NOT NULL, stock_code TEXT NOT NULL, date TEXT NOT NULL,
  old_open DOUBLE PRECISION, old_high DOUBLE PRECISION, old_low DOUBLE PRECISION,
  old_close DOUBLE PRECISION, old_volume DOUBLE PRECISION,
  new_open DOUBLE PRECISION, new_high DOUBLE PRECISION, new_low DOUBLE PRECISION,
  new_close DOUBLE PRECISION, new_volume DOUBLE PRECISION,
  reason TEXT NOT NULL, fixed_at TEXT NOT NULL,
  PRIMARY KEY(run_id, stock_code, date)
)
"""
FRACTIONAL = ("(ph.open<>ROUND(ph.open) OR ph.high<>ROUND(ph.high) "
              "OR ph.low<>ROUND(ph.low) OR ph.close<>ROUND(ph.close))")


def load_source(source: str) -> pd.DataFrame:
    if source == "marcap":
        from marcap_client import ensure_year
        frames = []
        for year in range(2019, 2027):
            df = pd.read_parquet(ensure_year(year), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
            frames.append(df.rename(columns={"Code": "stock_code", "Date": "date"}))
        df = pd.concat(frames, ignore_index=True)
        df["date"] = df["date"].astype(str).str[:10]
    else:
        frames = []
        for path in sorted((ROOT / "data_cache" / "fdr_fractional").glob("*.parquet")):
            part = pd.read_parquet(path)
            if part.empty:
                continue
            part.insert(0, "stock_code", path.stem)
            frames.append(part.rename(columns={"Date": "date"}))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["stock_code", "date", "Open", "High", "Low", "Close", "Volume"])
    df = df.rename(columns=str.lower).dropna(subset=["stock_code", "date", "open", "high", "low", "close", "volume"])
    ints = (df[["open", "high", "low", "close"]] == df[["open", "high", "low", "close"]].round()).all(axis=1)
    valid_shape = ((df.open > 0) & (df.low > 0) & (df.high >= df[["open", "low", "close"]].max(axis=1))
                   & (df.low <= df[["open", "high", "close"]].min(axis=1))) | \
                  ((df.volume == 0) & (df.open == 0) & (df.high == 0) & (df.low == 0))
    return df[ints & (df.close > 0) & (df.volume >= 0) & valid_shape].copy()


def anchor_filter(conn, df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """For fdr: keep only codes proven to be on our raw basis, or ratio-checked rows."""
    codes = sorted(df.stock_code.unique())
    live = pd.DataFrame(conn.execute(
        """SELECT stock_code, date, close,
                  (open=ROUND(open) AND high=ROUND(high) AND low=ROUND(low) AND close=ROUND(close)) AS all_int
           FROM price_history WHERE stock_code = ANY(?) AND date>='2019-01-01'""", (codes,)
    ).fetchall(), columns=["stock_code", "date", "live_close", "all_int"])
    merged = df.merge(live, on=["stock_code", "date"], how="inner")
    anchor = merged[merged.all_int.astype(bool)]
    stats = anchor.groupby("stock_code").apply(lambda g: pd.Series({"n": len(g), "match": (g.close == g.live_close).mean()}),
                                                include_groups=False)
    anchored = set(stats[(stats.n >= 20) & (stats.match >= 0.95)].index)
    frac = merged[~merged.all_int.astype(bool)].copy()
    frac["ratio_ok"] = (frac.close / frac.live_close - 1).abs() <= 0.30
    unanchored_ok = frac[(~frac.stock_code.isin(anchored)) & frac.ratio_ok][["stock_code", "date"]]
    keep = df.merge(pd.concat([
        merged[merged.stock_code.isin(anchored)][["stock_code", "date"]], unanchored_ok]).drop_duplicates(),
        on=["stock_code", "date"])
    return keep, {"codes_in_source": len(codes), "anchored_codes": len(anchored),
                  "unanchored_rows_ratio_ok": len(unanchored_ok), "rows_kept": len(keep)}


def run(source: str, dry_run: bool) -> dict:
    conn = connect_primary_db(timeout=600)
    try:
        native_script(conn, BACKUP_DDL)
        df = load_source(source)
        info = {"source": source, "source_rows_valid_integer": len(df)}
        if source == "fdr":
            df, extra = anchor_filter(conn, df)
            info.update(extra)

        conn.execute("DROP TABLE IF EXISTS residual_staging")
        conn.execute("""CREATE TEMP TABLE residual_staging (stock_code TEXT, date TEXT, open DOUBLE PRECISION,
            high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION)""")
        raw = getattr(conn, "_connection", None) or conn
        cur = raw.cursor()
        with cur.copy("COPY residual_staging (stock_code,date,open,high,low,close,volume) FROM STDIN") as copy:
            for row in df[["stock_code", "date", "open", "high", "low", "close", "volume"]].itertuples(index=False, name=None):
                copy.write_row(row)
        conn.execute("CREATE INDEX ON residual_staging (stock_code, date)")

        cond = f"""ph.stock_code ~ '^[0-9]{{6}}$' AND {FRACTIONAL}
                   AND (ph.close<>ROUND(ph.close) OR ABS(m.close/ph.close-1)<=0.30)"""
        join = "FROM price_history ph JOIN residual_staging m ON ph.stock_code=m.stock_code AND ph.date=m.date"
        candidates = conn.execute(f"SELECT count(*) {join} WHERE {cond}").fetchone()[0]
        info.update({"candidates": candidates, "dry_run": dry_run})
        if dry_run:
            return info

        run_id = f"residual_fractional_repair_{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        reason = (f"second-pass yfinance auto_adjust incident repair ({source}): fractional OHLC on a 6-digit KRX code "
                  "replaced with whole-won source values (see hermes.md 2026-09-24)")
        conn.execute(f"""INSERT INTO price_history_fix_backup
              (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
               new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
            SELECT ?, ph.stock_code, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume,
                   m.open, m.high, m.low, m.close, m.volume, ?, ?
            {join} WHERE {cond} ON CONFLICT (run_id, stock_code, date) DO NOTHING""", (run_id, reason, now))
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.execute(f"""UPDATE price_history ph SET open=m.open, high=m.high, low=m.low, close=m.close, volume=m.volume
            FROM residual_staging m WHERE ph.stock_code=m.stock_code AND ph.date=m.date
              AND {cond}""")
        conn.execute(
            """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                 new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "price_history", f"residual fractional OHLC repair ({source})", candidates,
             "UPDATE price_history SET OHLCV = whole-won source values WHERE 6-digit code has fractional OHLC",
             "fractional open/high/low/close (impossible for a real KRX print)",
             f"{source} OHLCV matched by (stock_code,date)", reason, run_id))
        conn.commit()
        info["run_id"] = run_id
        return info
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["marcap", "fdr"], required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(args.source, not args.apply), ensure_ascii=False, indent=2, default=str))
