#!/usr/bin/env python3
"""Repair the decimal-close rows left over from the 2026-03-31~04-07 yfinance
auto_adjust/INSERT-OR-IGNORE incident that the marcap repair could not match
(see hermes.md '최우선 발견').

Only stocks whose Naver snapshot agrees (close within 0.5%/1 won) with >=99% of
their EXISTING integer-close rows (min 20 comparisons) are touched - that per-stock
check proves Naver is on the same price basis as price_history for that stock.
ETFs / stocks where Naver disagrees are left alone. Only rows whose close is
non-integer are replaced, with Naver's integer OHLCV for the same date.
Dry-run by default; --apply writes. Backup -> price_history_fix_backup, log ->
data_fix_log, write-guard flag set explicitly.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import invalid_ohlcv  # noqa: E402

SNAP = ROOT / "research_outputs" / "price_snapshot_repair" / "20260911T200447"
ANALYSIS = Path("/tmp/remaining_decimal_analysis.json")
REASON = ("decimal close (yfinance auto_adjust 2026-03-31~04-07 incident, not marcap-matchable) "
          "replaced with naver integer OHLCV; per-stock naver basis agreement >=99% verified")


def run(apply: bool) -> dict:
    good = [g[0] for g in json.loads(ANALYSIS.read_text())["good"]]
    conn = connect_primary_db(timeout=300)
    run_id = f"remaining_decimal_naver_repair_20260924_{datetime.now().strftime('%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    total = {"stocks": 0, "rows": 0, "skipped_invalid": 0, "skipped_no_naver": 0}
    try:
        if apply:
            conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        for code in good:
            nv = {r[1]: r for r in json.loads(gzip.decompress((SNAP / f"{code}.json.gz").read_bytes()))}
            cur = conn.execute(
                """SELECT date::text, open,high,low,close,volume FROM price_history
                   WHERE stock_code=? AND close>0 AND close != FLOOR(close)""", (code,)).fetchall()
            backup, updates = [], []
            for d, o, h, l, c, v in cur:
                d = d[:10]
                n = nv.get(d)
                if not n or not n[5]:
                    total["skipped_no_naver"] += 1
                    continue
                new = tuple(float(x) for x in (n[2], n[3], n[4], n[5], n[6]))
                if invalid_ohlcv(*new) or any(x != int(x) for x in new[:4]):
                    total["skipped_invalid"] += 1
                    continue
                old = tuple(float(x) if x is not None else None for x in (o, h, l, c, v))
                backup.append((run_id, code, d, *old, *new, REASON, now))
                updates.append((*new, code, d, c))
            if not updates:
                continue
            total["stocks"] += 1
            total["rows"] += len(updates)
            if apply:
                conn.executemany(
                    """INSERT INTO price_history_fix_backup
                       (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", backup)
                conn.executemany(
                    """UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
                       WHERE stock_code=? AND date::text=? AND close=?""", updates)
                conn.commit()
                conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        if apply and total["rows"]:
            conn.execute(
                """INSERT INTO data_fix_log
                   (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                    new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
                (now, "price_history",
                 f"leftover decimal-close rows from 2026-03-31~04-07 incident, {total['stocks']} stocks "
                 "where naver basis agrees >=99% with existing integer rows",
                 total["rows"],
                 "UPDATE price_history SET OHLCV = naver snapshot integer OHLCV WHERE close non-integer",
                 "close != FLOOR(close) (impossible for a real KRX print)",
                 "naver integer OHLCV for same (stock_code,date)",
                 "research_outputs/price_snapshot_repair/20260911T200447 naver snapshots", run_id))
            conn.commit()
    finally:
        conn.close()
    total.update(run_id=run_id, dry_run=not apply)
    return total


if __name__ == "__main__":
    print(json.dumps(run("--apply" in sys.argv), ensure_ascii=False, indent=2))
