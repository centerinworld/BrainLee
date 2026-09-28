#!/usr/bin/env python3
"""Resumable FnGuide (wcomp JSON API) quarterly snapshot collector for a given year (default 2026), CFS then OFS-fallback.
Why not collectors/fnguide_financial_collector.py: it keeps one PostgreSQL transaction open across the 3s rate-limited HTTP calls, so PG
kills it (idle-in-transaction timeout) and nothing is saved ("connection is closed") - the reason 2026 Q2 was never collected. This driver
fetches first, then writes each stock in its own short connection. Cash-flow calls are skipped (4 requests/stock instead of 6). Only
financial_source_snapshot is written (data_source='fnguide'); financial_data is NOT touched. Stops at the FNGUIDE daily quota
(api_rate_limiter, unchanged) and resumes next day (stocks with today's-or-later snapshot for the year/quarter are skipped)."""
import sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import collectors.fnguide_financial_collector as m  # noqa: E402
from api_rate_limiter import api_limiter, _API_CONFIG  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
QTR = int(sys.argv[2]) if len(sys.argv) > 2 else 2
_orig = m._fnguide_json
def _no_cf(endpoint, cmp_cd, freq_typ, consol_typ):
    return ([], []) if endpoint == "getFinCashFlow" else _orig(endpoint, cmp_cd, freq_typ, consol_typ)
m._fnguide_json = _no_cf

def quota_left():
    return _API_CONFIG["FNGUIDE"]["daily_limit"] - api_limiter._states["FNGUIDE"].count_today

def main():
    conn = connect_primary_db(timeout=120, readonly=True)
    codes = [r[0] for r in conn.execute("""
        SELECT su.stock_code FROM stock_universe su
        WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ') AND COALESCE(su.stock_type,'보통주')='보통주'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%' AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
          AND su.stock_code ~ '^[0-9]{6}$'
          AND NOT EXISTS (SELECT 1 FROM financial_source_snapshot s WHERE s.stock_code=su.stock_code AND s.data_source='fnguide'
                          AND s.year=? AND s.quarter=? AND s.fetched_at >= '2026-09-26')
        ORDER BY su.stock_code""", (YEAR, QTR)).fetchall()]
    conn.close()
    done = empty = 0
    for code in codes:
        if quota_left() < 12:
            print("quota reached; resume later", flush=True); break
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = None
        for rt in ("CFS", "OFS"):
            r = m.fetch_fnguide_all(code, rt, annual_only=False)
            qd = (r or {}).get("quarterly", {}).get(YEAR, {}) if r else {}
            if qd.get(QTR):
                got = (rt, r["source_url"], qd); break
        if not got:
            empty += 1
            # record a marker-less miss by writing nothing; retried on next run (bounded by quota)
            continue
        rt, url, qd = got
        c = connect_primary_db(timeout=60)
        for q, data in qd.items():
            if data and q != 4:
                m.save_snapshot(c, code, YEAR, q, 0, rt, url, data, fetched_at)
        c.commit(); c.close(); done += 1
        if (done + empty) % 50 == 0:
            print(done, empty, len(codes), "quota_left", quota_left(), flush=True)
    print("finished", done, "empty", empty, "remaining", len(codes) - done - empty, flush=True)

if __name__ == "__main__":
    main()
