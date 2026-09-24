#!/usr/bin/env python3
"""Undo the basis mistake in apply_residual_fractional_repair_20260924.py --source fdr.

FinanceDataReader/Naver serves split-adjusted prices, while price_history's real rows and
the marcap repairs are raw (as-traded). For stocks with a split the fdr pass therefore
wrote adjusted values next to raw neighbours (e.g. 086820 2024-11-27: 5838 between 17990
and 17500) and manufactured ~4.7K fake jumps. Its 30% ratio guard compared FDR against the
*old adjusted fractional* value, so it could not see the conflict, and the earlier marcap
pass had skipped those same rows for the opposite reason (ratio >30% vs the adjusted value).

Fix: for every row written by a residual_fractional_repair_fdr run, if marcap (raw,
authoritative for common stocks) has that (code,date) and any of OHLCV differs, replace it
with marcap. Rows for codes marcap does not cover (ETFs) are reported, not touched.
--apply to write.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import ensure_year  # noqa: E402


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=900)
    frames = [pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
              for y in range(2019, 2027)]
    m = pd.concat(frames, ignore_index=True)
    m["Date"] = m["Date"].astype(str).str[:10]
    m = m.dropna()
    conn.execute("DROP TABLE IF EXISTS marcap_fix_staging")
    conn.execute("""CREATE TEMP TABLE marcap_fix_staging (stock_code TEXT, date TEXT, open DOUBLE PRECISION,
        high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION)""")
    cur = conn._connection.cursor()
    with cur.copy("COPY marcap_fix_staging FROM STDIN") as cp:
        for row in m.itertuples(index=False, name=None):
            cp.write_row(row)
    conn.execute("CREATE INDEX ON marcap_fix_staging (stock_code, date)")

    frm = """FROM price_history ph
      JOIN (SELECT DISTINCT stock_code,date FROM price_history_fix_backup
            WHERE run_id LIKE 'residual_fractional_repair_fdr%') f ON f.stock_code=ph.stock_code AND f.date=ph.date
      JOIN marcap_fix_staging m ON m.stock_code=ph.stock_code AND m.date=ph.date
      WHERE m.close>0 AND (ph.open<>m.open OR ph.high<>m.high OR ph.low<>m.low
            OR ph.close<>m.close OR ph.volume<>m.volume)"""
    n = conn.execute(f"SELECT count(*) {frm}").fetchone()[0]
    print({"rows_to_fix": n, "apply": apply})
    if apply and n:
        run_id = f"fdr_basis_conflict_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        reason = ("undo fdr split-adjusted basis on rows where marcap raw exists "
                  "(hermes.md 2026-09-24 fdr basis conflict)")
        conn.execute(f"""INSERT INTO price_history_fix_backup
            (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
             new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
            SELECT ?, ph.stock_code, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume,
                   m.open, m.high, m.low, m.close, m.volume, ?, ? {frm}""", (run_id, reason, now))
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.execute("""UPDATE price_history ph SET open=m.open,high=m.high,low=m.low,close=m.close,volume=m.volume
            FROM marcap_fix_staging m
            WHERE m.stock_code=ph.stock_code AND m.date=ph.date AND m.close>0
              AND EXISTS (SELECT 1 FROM price_history_fix_backup f WHERE f.stock_code=ph.stock_code AND f.date=ph.date
                          AND f.run_id LIKE 'residual_fractional_repair_fdr%')
              AND (ph.open<>m.open OR ph.high<>m.high OR ph.low<>m.low OR ph.close<>m.close OR ph.volume<>m.volume)""")
        conn.execute(
            """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                 new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "price_history", "fdr split-adjusted rows replaced by marcap raw", n,
             "UPDATE OHLCV = marcap raw for rows written by residual fdr pass",
             "split-adjusted (Naver) values beside raw neighbours", "marcap raw OHLCV", reason, run_id))
        conn.commit()
        print({"run_id": run_id})
    conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
