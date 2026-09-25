#!/usr/bin/env python3
"""Repair daily bars of 2026-09-14..09-23 that were stored from a pre-closing-auction snapshot.

Found 2026-09-24 while validating pykrx 1.2.9: price_history vs KRX-official (marcap through 09-21, pykrx after) showed
0% close mismatches up to 09-11, ~3% on 09-14..09-18 and 67% on 09-21 (1,796 rows; opens identical, close/high/low/volume
differ, median 0.6%, p90 2%) and ~70% on 09-22/23 - the signature of a snapshot taken before the 15:20-15:30 closing
auction saved as the final bar. Sources (both KRX-official, whole-won):
  09-14..09-21  marcap raw OHLCV (exact)
  09-22..09-23  pykrx get_market_ohlcv per stock (marcap ends 09-21); accepted only if valid OHLC and within 8% of the stored
                close (a larger gap would mean a different event, not the closing-auction effect).
Every changed row is backed up in price_history_fix_backup and logged in data_fix_log. --apply to write."""
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

MARCAP_RANGE = ("2026-09-14", "2026-09-21")
PYKRX_DATES = ("2026-09-22", "2026-09-23")


def valid(o, h, l, c, v):
    if not all(x == x and x >= 0 for x in (o, h, l, c, v)) or c <= 0:
        return False
    if v == 0 and o == h == l == 0:
        return True
    return o > 0 and l > 0 and h >= max(o, l, c) and l <= min(o, h, c) and all(float(x).is_integer() for x in (o, h, l, c))


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=900)
    live = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,date,open,high,low,close,volume FROM price_history WHERE stock_code ~ '^[0-9]{6}$' AND date>=? AND date<=? AND close>0",
        (MARCAP_RANGE[0], PYKRX_DATES[1])).fetchall()], columns=["code", "date", "o", "h", "l", "c", "v"])
    fixes = []  # (code,date,old(o,h,l,c,v),new(o,h,l,c,v),source)
    m = pd.read_parquet(ensure_year(2026), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
    m["Date"] = m["Date"].astype(str).str[:10]
    j = live[live.date <= MARCAP_RANGE[1]].merge(m, left_on=["code", "date"], right_on=["Code", "Date"])
    for r in j.itertuples():
        new = (float(r.Open), float(r.High), float(r.Low), float(r.Close), float(r.Volume))
        old = (float(r.o), float(r.h), float(r.l), float(r.c), float(r.v))
        if new != old and valid(*new):
            fixes.append((r.code, r.date, old, new, "marcap"))
    n_marcap = len(fixes)
    codes = sorted(live[live.date >= PYKRX_DATES[0]].code.unique())
    from pykrx import stock

    def fetch(code):
        try:
            return code, stock.get_market_ohlcv("20260922", "20260923", code)
        except Exception:  # noqa: BLE001
            return code, None
    stored = {(r.code, r.date): (float(r.o), float(r.h), float(r.l), float(r.c), float(r.v)) for r in live[live.date >= PYKRX_DATES[0]].itertuples()}
    failed = 0
    # sequential on purpose: run inside a ThreadPoolExecutor pykrx calls stalled for 10+ minutes (0.03 s/call when sequential)
    if True:
        for code, k in map(fetch, codes):
            if k is None or k.empty:
                failed += 1
                continue
            for d, row in k.iterrows():
                iso = str(d.date())
                old = stored.get((code, iso))
                if old is None:
                    continue
                new = (float(row["시가"]), float(row["고가"]), float(row["저가"]), float(row["종가"]), float(row["거래량"]))
                if new != old and valid(*new) and abs(new[3] / old[3] - 1) <= 0.08:
                    fixes.append((code, iso, old, new, "pykrx"))
    print({"marcap_fixes": n_marcap, "pykrx_fixes": len(fixes) - n_marcap, "pykrx_codes": len(codes), "pykrx_failed": failed, "apply": apply}, flush=True)
    if not apply or not fixes:
        return
    run_id = f"recent_close_official_fix_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = "pre-closing-auction snapshot stored as final daily bar (2026-09-14..23): replaced by KRX-official OHLCV (marcap/pykrx)"
    conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    conn.executemany("""INSERT INTO price_history_fix_backup (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
        [(run_id, c, d, *old, *new, reason + f" [{src}]", now) for c, d, old, new, src in fixes])
    conn.executemany("UPDATE price_history SET open=?,high=?,low=?,close=?,volume=? WHERE stock_code=? AND date=?",
                     [(*new, c, d) for c, d, old, new, src in fixes])
    conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
        VALUES(?,?,?,?,?,?,?,?,?)""", (now, "price_history", "2026-09-14..23 daily bars from pre-close snapshot", len(fixes),
        "UPDATE OHLCV = KRX-official (marcap 09-14..21, pykrx 09-22..23)", "close/high/low/volume off by ~0.6% median (p90 2%)",
        "official whole-won OHLCV", reason, run_id))
    conn.commit()
    print({"run_id": run_id, "updated": len(fixes)})


if __name__ == "__main__":
    main("--apply" in sys.argv)
