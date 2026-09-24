#!/usr/bin/env python3
"""Repair unresolved_active_common jumps whose price_history rows disagree with marcap raw.

For each unresolved audit event (previous_date -> event_date) the marcap raw closes at those two
dates are compared with price_history. If marcap's own ratio is inside the KRX price-limit band
(i.e. the raw market series has no jump there) but price_history's rows differ from marcap, the
jump is a data artefact and the differing row(s) are replaced by marcap OHLCV (whole-won, valid
shape only). If marcap shows the same jump the event is real and is left for corporate-action
classification. Backup + data_fix_log like the other repair scripts. --apply to write.
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
from price_integrity import price_band  # noqa: E402


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=900)
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        """SELECT stock_code,event_date,previous_date FROM price_jump_audit
           WHERE classification='unresolved_active_common'""").fetchall()], columns=["code", "d", "pd"])
    m = pd.concat([pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
                   for y in range(2010, 2027)], ignore_index=True)
    m["Date"] = m["Date"].astype(str).str[:10]
    m = m.dropna()
    ohlc = m[["Open", "High", "Low", "Close"]]
    ok = ((ohlc == ohlc.round()).all(axis=1) & (m.Close > 0) & (m.Volume >= 0)
          & (((m.Open > 0) & (m.Low > 0) & (m.High >= m[["Open", "Low", "Close"]].max(axis=1))
              & (m.Low <= m[["Open", "High", "Close"]].min(axis=1)))
             | ((m.Volume == 0) & (m.Open == 0) & (m.High == 0) & (m.Low == 0))))
    m = m[ok]
    cur = m.rename(columns={"Code": "code", "Date": "d", "Close": "mec"})[["code", "d", "mec"]]
    prv = m.rename(columns={"Code": "code", "Date": "pd", "Close": "mpc"})[["code", "pd", "mpc"]]
    j = ev.merge(cur, on=["code", "d"]).merge(prv, on=["code", "pd"])
    j["lo"] = j.d.map(lambda x: price_band(x)[0]); j["hi"] = j.d.map(lambda x: price_band(x)[1])
    j = j[(j.mec / j.mpc >= j.lo - 1e-9) & (j.mec / j.mpc <= j.hi + 1e-9)]
    targets = pd.concat([j[["code", "d"]].rename(columns={"d": "date"}),
                         j[["code", "pd"]].rename(columns={"pd": "date"})]).drop_duplicates()
    tgt = targets.merge(m.rename(columns={"Code": "code", "Date": "date"}), on=["code", "date"])
    print({"events_with_smooth_marcap": len(j), "target_rows": len(tgt)})

    conn.execute("DROP TABLE IF EXISTS unres_fix_staging")
    conn.execute("""CREATE TEMP TABLE unres_fix_staging (stock_code TEXT, date TEXT, open DOUBLE PRECISION,
        high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION)""")
    cur_ = conn._connection.cursor()
    with cur_.copy("COPY unres_fix_staging FROM STDIN") as cp:
        for row in tgt[["code", "date", "Open", "High", "Low", "Close", "Volume"]].itertuples(index=False, name=None):
            cp.write_row(row)
    conn.execute("CREATE INDEX ON unres_fix_staging (stock_code, date)")
    frm = """FROM price_history ph JOIN unres_fix_staging m ON m.stock_code=ph.stock_code AND m.date=ph.date
             WHERE ph.open<>m.open OR ph.high<>m.high OR ph.low<>m.low OR ph.close<>m.close"""
    n = conn.execute(f"SELECT count(*) {frm}").fetchone()[0]
    print({"rows_to_fix": n, "apply": apply})
    if apply and n:
        run_id = f"unresolved_jump_marcap_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")
        reason = "unresolved jump where marcap raw series is smooth: replace disagreeing price_history rows (hermes.md 2026-09-24)"
        conn.execute(f"""INSERT INTO price_history_fix_backup
            (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
             new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
            SELECT ?, ph.stock_code, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume,
                   m.open, m.high, m.low, m.close, m.volume, ?, ? {frm}""", (run_id, reason, now))
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.execute("""UPDATE price_history ph SET open=m.open,high=m.high,low=m.low,close=m.close,volume=m.volume
            FROM unres_fix_staging m WHERE m.stock_code=ph.stock_code AND m.date=ph.date
              AND (ph.open<>m.open OR ph.high<>m.high OR ph.low<>m.low OR ph.close<>m.close)""")
        conn.execute(
            """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                 new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "price_history", "unresolved jump rows replaced by marcap raw", n,
             "UPDATE OHLCV = marcap raw where marcap series has no jump", "rows disagreeing with marcap raw",
             "marcap raw OHLCV", reason, run_id))
        conn.commit()
        print({"run_id": run_id})
    conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
