#!/usr/bin/env python3
"""Record WHY each remaining coverage_gap cannot be filled, so it is not re-reviewed on every audit run.

For every audit event still classified coverage_gap: the missing trading days (price_trading_calendar between
previous_date and event_date) are checked against every available source (marcap rows, FinanceDataReader). If any source
supplies them the row is reported (run the fill scripts instead); otherwise the gap is classified with the strongest
evidence available and written to price_coverage_gap_reviewed:
  trading_halt        DART 거래정지/정지해제 disclosure within [-30d,+10d] of the gap (local dart_disclosures)
  delisted            DART 상장폐지 disclosure, or marcap/FDR have no rows for the code after previous_date
  dormant_no_record   gap >30 days and no source has any trading record in it
  no_source_data      gap <=30 days, no halt/delisting evidence, and no source covers the days (ETF/ETN or thin listing)
The audit turns matching rows into classification coverage_gap_reviewed (return_usable stays 0). --apply to write.
"""
from __future__ import annotations

import bisect
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import ensure_year  # noqa: E402
import importlib.util  # noqa: E402
_sp = importlib.util.spec_from_file_location("fcg", Path(__file__).with_name("fill_coverage_gaps_20260924.py"))
_fm = importlib.util.module_from_spec(_sp); _sp.loader.exec_module(_fm)
valid_rows = _fm.valid_rows


def main(apply: bool) -> None:
    import FinanceDataReader as fdr
    conn = connect_primary_db(timeout=900)
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,event_date,previous_date FROM price_jump_audit WHERE classification='coverage_gap'").fetchall()],
        columns=["code", "d", "p"])
    cal = [r[0] for r in conn.execute("SELECT date FROM price_trading_calendar ORDER BY date").fetchall()]
    mall = pd.concat([pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
                      for y in range(2010, 2027)]).dropna()
    mall["Date"] = mall["Date"].astype(str).str[:10]
    mvalid = valid_rows(mall.rename(columns={"Code": "code", "Date": "date", "Open": "open", "High": "high", "Low": "low",
                                             "Close": "close", "Volume": "volume"}))
    mset = set(zip(mvalid.code, mvalid.date)); mpresent = set(zip(mall.Code, mall.Date)); mcodes = set(mall.Code)
    mlast = mall.groupby("Code").Date.max().to_dict()
    fdr_cache: dict = {}
    fdr_frames: dict = {}
    rows, fill_possible, stats = [], 0, {}
    for r in ev.itertuples():
        days = cal[bisect.bisect_right(cal, r.p):bisect.bisect_left(cal, r.d)]
        gap = (pd.Timestamp(r.d) - pd.Timestamp(r.p)).days
        if any((r.code, x) in mset for x in days):
            fill_possible += 1
            continue
        marcap_invalid = [x for x in days if (r.code, x) in mpresent]
        if r.code not in fdr_cache:
            try:
                df = fdr.DataReader(r.code, "2010-01-01", "2026-09-24")
                if df is None or df.empty:
                    fdr_cache[r.code] = (set(), set())
                else:
                    d2 = df.reset_index().rename(columns=str.lower)
                    d2["date"] = pd.to_datetime(d2["date"]).dt.strftime("%Y-%m-%d")
                    fdr_cache[r.code] = (set(valid_rows(d2)["date"]), set(d2["date"]))
                    fdr_frames[r.code] = d2
            except Exception:  # noqa: BLE001
                fdr_cache[r.code] = None
        fdv = fdr_cache[r.code]
        fd = fdv[1] if fdv else None
        fdr_valid_days = [x for x in days if fdv and x in fdv[0]]
        if fdr_valid_days:
            x = fdr_valid_days[0]
            fr = fdr_frames.get(r.code)
            prevc = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND date<? AND close>0 ORDER BY date DESC LIMIT 1", (r.code, x)).fetchone()
            nextc = conn.execute("SELECT close FROM price_history WHERE stock_code=? AND date>? AND close>0 ORDER BY date LIMIT 1", (r.code, x)).fetchone()
            fc = float(fr.loc[fr["date"] == x, "close"].iloc[0]) if fr is not None else None
            bad = [(nb[0], fc / nb[0]) for nb in (prevc, nextc) if nb and fc and not 0.70 <= fc / nb[0] <= 1.30]
            if not bad:
                fill_possible += 1
                continue
            stats["source_basis_mismatch"] = stats.get("source_basis_mismatch", 0) + 1
            rows.append((r.code, r.d, r.p, "source_basis_mismatch",
                         f"FinanceDataReader close {fc:.0f} on {x} is x{bad[0][1]:.2f} of adjacent DB close {bad[0][0]:.0f} (outside the 0.70-1.30 price band): "
                         "source is on a different (split-adjusted) basis, so it is not inserted",
                         datetime.now().isoformat(timespec="seconds")))
            continue
        fdr_invalid = [x for x in days if fdv and x in fdv[1]]
        lo = (pd.Timestamp(r.p) - pd.Timedelta(days=30)).strftime("%Y%m%d").replace("-", "")
        hi = (pd.Timestamp(r.d) + pd.Timedelta(days=10)).strftime("%Y%m%d")
        d = conn.execute(
            """SELECT report_nm,rcept_dt,rcept_no FROM dart_disclosures WHERE stock_code=? AND
               replace(rcept_dt,'-','') BETWEEN ? AND ? AND (report_nm LIKE '%거래정지%' OR report_nm LIKE '%정지해제%'
               OR report_nm LIKE '%상장폐지%') ORDER BY rcept_dt DESC LIMIT 1""", (r.code, lo, hi)).fetchone()
        srcs = "marcap" + ("" if r.code in mcodes else "(no coverage)") + ", FinanceDataReader" + \
               ("(error)" if fd is None else "(no rows)" if not fd else "(no rows in gap)")
        if marcap_invalid or fdr_invalid:
            reason = "source_row_invalid"
            ev_txt = (f"{len(marcap_invalid)} marcap / {len(fdr_invalid)} FinanceDataReader row(s) exist for the missing day(s) but "
                      "fail OHLC validity (e.g. close>high or open/low=0) and are not inserted; a repaired candle would be invented")
        elif d and "상장폐지" in d[0]:
            reason, ev_txt = "delisted", f"DART '{d[0]}' {d[1]} rcept {d[2]}"
        elif d:
            reason, ev_txt = "trading_halt", f"DART '{d[0]}' {d[1]} rcept {d[2]}"
        elif r.code in mlast and mlast[r.code] < r.d:
            reason, ev_txt = "delisted", f"marcap last trading date {mlast[r.code]} precedes {r.d}"
        elif gap > 30:
            reason, ev_txt = "dormant_no_record", f"{gap}-day gap, no trading record in {srcs}"
        else:
            reason, ev_txt = "no_source_data", f"{len(days)} missing day(s); none present in {srcs}"
        stats[reason] = stats.get(reason, 0) + 1
        rows.append((r.code, r.d, r.p, reason, ev_txt, datetime.now().isoformat(timespec="seconds")))
    print({"events": len(ev), "fill_possible_skipped": fill_possible, "to_record": len(rows), "by_reason": stats})
    if not apply or not rows:
        return
    conn.executemany("""INSERT INTO price_coverage_gap_reviewed(stock_code,event_date,previous_date,reason,evidence,reviewed_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT (stock_code,event_date,previous_date) DO UPDATE SET reason=excluded.reason,
        evidence=excluded.evidence, reviewed_at=excluded.reviewed_at""", rows)
    conn.executemany("""UPDATE price_jump_audit SET classification='coverage_gap_reviewed', evidence=?
        WHERE stock_code=? AND event_date=? AND previous_date=? AND classification='coverage_gap'""",
                     [(f"{x[3]}: {x[4]}", x[0], x[1], x[2]) for x in rows])
    conn.commit()
    print("recorded", len(rows))


if __name__ == "__main__":
    main("--apply" in sys.argv)
