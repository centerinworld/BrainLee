#!/usr/bin/env python3
"""Replace leftover yfinance-adjusted decimal closes on ETFs (2026-03-31~04-07 incident
leftovers that neither marcap nor the naver snapshot covers) with KRX integer OHLCV
from pykrx (/tmp/etf_pykrx.jsonl, fetched read-only). Only ETFs whose pykrx/decimal
close ratio stays within 0.985..1.10 on every overlapping row (min 20) are touched,
which rules out split-adjusted or otherwise different-basis series. Dry-run default;
--apply writes with backup (price_history_fix_backup) + data_fix_log."""
from __future__ import annotations
import json, statistics, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from price_integrity import invalid_ohlcv  # noqa: E402

SRC = Path("/tmp/etf_pykrx.jsonl")
REASON = ("ETF decimal close (yfinance auto_adjust incident leftover) replaced with pykrx KRX "
          "integer OHLCV; pykrx/decimal close ratio within 0.985..1.10 on all overlapping rows")

def run(apply: bool) -> dict:
    conn = connect_primary_db(timeout=300)
    run_id = f"etf_decimal_pykrx_repair_20260924_{datetime.now().strftime('%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    tot = {"etfs": 0, "rows": 0, "skipped_basis": 0, "skipped_invalid": 0}
    try:
        for line in open(SRC):
            d = json.loads(line)
            if not d.get("rows"):
                continue
            code = d["code"]
            pk = {r[0]: r for r in d["rows"]}
            cur = conn.execute("""SELECT date::text,open,high,low,close,volume FROM price_history
                                  WHERE stock_code=? AND close>0 AND close!=FLOOR(close)""", (code,)).fetchall()
            ratios = [pk[x[0][:10]][4] / x[4] for x in cur if x[0][:10] in pk and pk[x[0][:10]][4] > 0]
            if len(ratios) < 20 or min(ratios) < 0.985 or max(ratios) > 1.10:
                tot["skipped_basis"] += 1
                continue
            backup, ups = [], []
            for dt, o, h, l, c, v in cur:
                r = pk.get(dt[:10])
                if not r:
                    continue
                new = tuple(float(x) for x in r[1:6])
                if invalid_ohlcv(*new):
                    tot["skipped_invalid"] += 1
                    continue
                old = tuple(float(x) if x is not None else None for x in (o, h, l, c, v))
                backup.append((run_id, code, dt[:10], *old, *new, REASON, now))
                ups.append((*new, code, dt[:10], c))
            if not ups:
                continue
            tot["etfs"] += 1; tot["rows"] += len(ups)
            if apply:
                conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
                conn.executemany("""INSERT INTO price_history_fix_backup
                    (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                     new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", backup)
                conn.executemany("""UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
                                    WHERE stock_code=? AND date::text=? AND close=?""", ups)
                conn.commit()
        if apply and tot["rows"]:
            conn.execute("""INSERT INTO data_fix_log
                (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (now, "price_history", f"leftover ETF decimal closes, {tot['etfs']} ETFs, pykrx basis-verified",
                 tot["rows"], "UPDATE price_history SET OHLCV = pykrx integer OHLCV WHERE close non-integer",
                 "close != FLOOR(close) on ETF rows", "pykrx integer OHLCV for same (stock_code,date)",
                 "pykrx get_market_ohlcv_by_date", run_id))
            conn.commit()
    finally:
        conn.close()
    tot.update(run_id=run_id, dry_run=not apply)
    return tot

if __name__ == "__main__":
    print(json.dumps(run("--apply" in sys.argv), ensure_ascii=False, indent=2))
