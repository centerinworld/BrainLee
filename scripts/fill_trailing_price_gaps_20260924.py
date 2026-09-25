#!/usr/bin/env python3
"""Fill TRAILING price gaps (no row at all after the stock's last date) for stocks whose live collection stopped
(44 numeric-code stocks after 2026-09-11, 78 alphanumeric-code stocks after 2026-09-08). Trading days come from
price_trading_calendar. Source priority per (stock, day): marcap unadjusted OHLCV (through 2026-09-21), else
FinanceDataReader (Naver) accepted only when integer OHLC, invalid_ohlcv passes and the close is a legal one-day move
from the previous close (price_integrity.price_band). No value is invented; days no source has stay empty.
Inserted rows are recorded in price_history_fix_backup with old_* NULL (rollback = delete those rows).
Dry-run default; --apply writes."""
import json, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from price_integrity import invalid_ohlcv, price_band  # noqa: E402
END = "2026-09-23"
REASON = "trailing gap fill (live collection stopped for this code): marcap raw or FDR with price-limit band check"

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"trailing_gap_fill_20260924_{datetime.now().strftime('%H%M%S')}"
    cal = [str(r[0])[:10] for r in conn.execute(f"SELECT date FROM price_trading_calendar WHERE date::text>='2026-09-01' AND date::text<='{END}' ORDER BY date").fetchall()]
    print("calendar", cal)
    last = {r[0]: str(r[1])[:10] for r in conn.execute("""SELECT stock_code,MAX(date::text) FROM price_history WHERE stock_code ~ '^[0-9A-Z]{6}$' GROUP BY 1""").fetchall()}
    codes = sorted(c for c, d in last.items() if d < END and d >= "2026-09-01")
    mc = pd.concat([pd.read_parquet(ROOT / "data_cache/marcap/marcap-2026.parquet", columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume", "Amount"])])
    mc = mc[mc.Code.isin(codes)].copy(); mc["Date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d"); mc = mc.set_index(["Code", "Date"])
    import FinanceDataReader as fdr
    plan = []; stats = {"marcap": 0, "fdr": 0, "no_source": 0, "invalid": 0, "band_reject": 0}; nosrc = set()
    for code in codes:
        prev = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND date::text=? AND close>0", (code, last[code])).fetchone()
        prev_close = float(prev[0]) if prev else None
        days = [d for d in cal if d > last[code]]
        fd = None
        for d in days:
            row = None; src = None
            if (code, d) in mc.index:
                r = mc.loc[(code, d)]
                vals = (float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume), float(r.Amount))
                if vals[3] > 0 and not invalid_ohlcv(*vals[:5]) and all(v == int(v) for v in vals[:4]):
                    row, src = vals, "marcap"
                else:
                    stats["invalid"] += 1
            if row is None:
                if fd is None:
                    try: fd = fdr.DataReader(code, last[code], END)
                    except Exception: fd = pd.DataFrame()
                if len(fd) and pd.Timestamp(d) in fd.index:
                    r = fd.loc[pd.Timestamp(d)]
                    vals = (float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume), None)
                    lo, hi = price_band(d)
                    if vals[3] > 0 and not invalid_ohlcv(*vals[:5]) and all(v == int(v) for v in vals[:4]):
                        if prev_close and not (lo - 0.005 <= vals[3] / prev_close <= hi + 0.005):
                            stats["band_reject"] += 1
                        else:
                            row, src = vals, "fdr"
                    else:
                        stats["invalid"] += 1
            if row is None:
                stats["no_source"] += 1; nosrc.add(code); continue
            stats[src] += 1; prev_close = row[3]
            plan.append((code, d, row, src))
    print(json.dumps(stats), "stocks with plan:", len({p[0] for p in plan}), "stocks with unfilled days:", len(nosrc))
    if apply and plan:
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.executemany("""INSERT INTO price_history_fix_backup (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
            new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
            [(run_id, c, d, r[0], r[1], r[2], r[3], r[4], REASON + f" [{s}]", now) for c, d, r, s in plan])
        conn.executemany("""INSERT INTO price_history (stock_code,date,open,high,low,close,volume,trade_amount) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING""", [(c, d, r[0], r[1], r[2], r[3], r[4], r[5]) for c, d, r, s in plan])
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now, "price_history", "trailing price gaps for stocks whose live collection stopped", len(plan),
            "INSERT missing (stock,day) rows from marcap raw / FDR with price-limit band check", "no row", "marcap raw OHLCV or FDR OHLCV (band verified)",
            "marcap parquet + FinanceDataReader", run_id))
        conn.commit()
    conn.close(); print(json.dumps({"rows": len(plan), "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
