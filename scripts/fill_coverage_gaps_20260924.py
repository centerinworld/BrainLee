#!/usr/bin/env python3
"""Fill price_history rows that are missing on real trading days (audit class `coverage_gap`).

Scope: audit events whose gap between previous_date and event_date is <=30 calendar days (longer gaps are
suspensions/relistings, not missing data). Missing (code,date) pairs = price_trading_calendar dates strictly
between the two. Sources, never invented:
  * marcap (raw, KRX-sourced) for common stocks
  * FinanceDataReader (Naver) for codes marcap does not cover (ETF/ETN), accepted only if the value sits
    within the KRX daily price limit of BOTH the nearest previous and next existing DB close, applied per
    direction (prev: 0.70..1.30, next: 1/1.30..1/0.70 — see fdr_band_ok), which guards against Naver's
    split-adjusted basis
Rows must be whole-won, close>0, valid OHLC shape (or suspension marker). Inserts go through the write guard
with app.price_basis_checked=1; each insert is recorded in price_history_fix_backup with NULL old_* values
(rollback = delete rows listed there). --apply to write.
"""
from __future__ import annotations

import bisect
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import ensure_year  # noqa: E402
from price_integrity import price_band  # noqa: E402  ← date-sensitive KRX limit policy (single source)

CACHE = ROOT / "data_cache" / "fdr_gap"

BAND_TOL = 1e-9   # numerical tolerance only, mirrors price_integrity.outside_band — never a relaxation


def valid_rows(df: pd.DataFrame) -> pd.DataFrame:
    o = df[["open", "high", "low", "close"]]
    ints = (o == o.round()).all(axis=1)
    shape = ((df.open > 0) & (df.low > 0) & (df.high >= df[["open", "low", "close"]].max(axis=1))
             & (df.low <= df[["open", "high", "close"]].min(axis=1))) | \
            ((df.volume == 0) & (df.open == 0) & (df.high == 0) & (df.low == 0))
    return df[ints & shape & (df.close > 0) & (df.volume >= 0)]


def gap_days(calendar, previous_date, event_date):
    """Market days strictly BETWEEN the two dates: the half-open interval (previous_date, event_date).

    Half-open on purpose. previous_date already has a price_history row and event_date is the row the
    audit is complaining about; the only days that may be inserted are the ones in between, so a
    re-run can never re-propose either endpoint.
    """
    return calendar[bisect.bisect_right(calendar, previous_date):bisect.bisect_left(calendar, event_date)]


def fdr_band_ok(close, prev_close, next_close, day, *, next_day=None):
    """Adjusted-basis guard for the FDR/Naver fallback source — the DAILY PRICE LIMIT IN FORCE ON THE DAY.

    Naver serves a split-adjusted series, so its value for a gap day is only usable when every step it forms with a
    neighbouring DB close is a legal one-day move. A KRX price limit is a limit on the *price for one day*, so the
    check is BOTH date-sensitive and direction-aware (`price_integrity.price_band` is the single source of the policy:
    ±15% before 2015-06-15, ±30% on/after it):

      candidate -> previous close:  the step C[day]/C[day-1] is limited by price_band(day)
      candidate -> next close:      the step C[next_day]/C[day] is limited by price_band(next_day), so the
                                    candidate/next ratio must lie in [1/hi, 1/lo] of THAT day's band.

    Consequences that a single flat ratio band gets wrong, and that this signature makes impossible to get wrong:

      * a -30% limit-down next day gives close/next = 1/0.70 = 1.4286, so [0.70, 1.30] applied to both sides refuses
        ordinary limit-down days as "basis splices" and accepts next-day rises beyond the limit (measured live on
        2015-2018 small caps: a dozen such rows);
      * before 2015-06-15 the limit is ±15%, so a ±30% band would wave through moves that were impossible then
        (measured live: the 2015-01-02..2015-06-12 window of `suspension_gap_fill_20260924_130903`);
      * a row whose next candle crosses the 2015-06-15 policy change must be judged with the NEXT day's band.

    Both `day` and `next_day` are required whenever the corresponding comparison happens: with no date there is no
    knowable limit, and guessing one would either accept an illegal move or refuse a legal one silently. Callers that
    cannot supply a date are programming errors, so this raises instead of returning a quiet verdict.
    """
    if not day:
        raise ValueError("fdr_band_ok needs the candidate day - the KRX price limit is date-sensitive")
    if prev_close is None and next_close is None:
        return False
    if prev_close is not None:
        lo, hi = price_band(day)
        if not (lo - BAND_TOL <= close / prev_close <= hi + BAND_TOL):
            return False
    if next_close is not None:
        if not next_day:
            raise ValueError("fdr_band_ok needs next_day when a next close is supplied")
        lo, hi = price_band(next_day)
        if not (1.0 / hi - BAND_TOL <= close / next_close <= 1.0 / lo + BAND_TOL):
            return False
    return True


def missing_pairs(conn, max_gap: int = 30) -> pd.DataFrame:
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,event_date,previous_date FROM price_jump_audit WHERE classification='coverage_gap'"
    ).fetchall()], columns=["code", "d", "p"])
    ev = ev[(pd.to_datetime(ev.d) - pd.to_datetime(ev.p)).dt.days <= max_gap]
    cal = [r[0] for r in conn.execute("SELECT date FROM price_trading_calendar ORDER BY date").fetchall()]
    rows = [(c, x) for c, d, p in zip(ev.code, ev.d, ev.p) for x in gap_days(cal, p, d)]
    miss = pd.DataFrame(rows, columns=["code", "date"]).drop_duplicates()
    existing = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,date FROM price_history WHERE stock_code = ANY(?)", (sorted(miss.code.unique()),)
    ).fetchall()], columns=["code", "date"])
    existing["e"] = 1
    miss = miss.merge(existing, on=["code", "date"], how="left")
    return miss[miss.e.isna()].drop(columns="e")


def marcap_rows(miss: pd.DataFrame) -> pd.DataFrame:
    m = pd.concat([pd.read_parquet(ensure_year(y), columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
                   for y in range(2010, 2027)], ignore_index=True)
    m["Date"] = m["Date"].astype(str).str[:10]
    m = m.rename(columns={"Code": "code", "Date": "date", "Open": "open", "High": "high", "Low": "low",
                          "Close": "close", "Volume": "volume"}).dropna()
    return valid_rows(miss.merge(m, on=["code", "date"]))


def fdr_rows(conn, miss: pd.DataFrame) -> pd.DataFrame:
    import FinanceDataReader as fdr
    CACHE.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (code, g) in enumerate(miss.groupby("code"), 1):
        path = CACHE / f"{code}.parquet"
        if path.exists():
            df = pd.read_parquet(path)
        else:
            try:
                df = fdr.DataReader(code, g.date.min(), g.date.max())
                df = df.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]] if df is not None and not df.empty \
                    else pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
            except Exception as exc:  # noqa: BLE001
                print(f"fdr {code} ERR {repr(exc)[:80]}", flush=True)
                continue
            df.to_parquet(path)
            time.sleep(0.3)
        if df.empty:
            continue
        df = df.rename(columns=str.lower).rename(columns={"date": "date"})
        df["code"] = code
        cand = valid_rows(g.merge(df, on=["code", "date"]))
        for _, r in cand.iterrows():
            prev = conn.execute("SELECT date,close FROM price_history WHERE stock_code=? AND date<? AND close>0 ORDER BY date DESC LIMIT 1",
                                (code, r.date)).fetchone()
            nxt = conn.execute("SELECT date,close FROM price_history WHERE stock_code=? AND date>? AND close>0 ORDER BY date LIMIT 1",
                               (code, r.date)).fetchone()
            if fdr_band_ok(r.close, prev[1] if prev else None, nxt[1] if nxt else None, r.date,
                           next_day=str(nxt[0]) if nxt else None):
                out.append(r[["code", "date", "open", "high", "low", "close", "volume"]])
        if i % 100 == 0:
            print(f"fdr {i} codes, accepted {len(out)}", flush=True)
    return pd.DataFrame(out)


def main(apply: bool, max_gap: int = 30) -> None:
    conn = connect_primary_db(timeout=900)
    miss = missing_pairs(conn, max_gap)
    print({"missing_pairs": len(miss), "codes": miss.code.nunique()}, flush=True)
    a = marcap_rows(miss)
    rest = miss.merge(a[["code", "date"]], on=["code", "date"], how="left", indicator=True)
    rest = rest[rest["_merge"] == "left_only"][["code", "date"]]
    b = fdr_rows(conn, rest)
    ins = pd.concat([a.assign(src="marcap"), b.assign(src="fdr")], ignore_index=True) if len(b) else a.assign(src="marcap")
    print({"from_marcap": len(a), "from_fdr": len(b), "total_insert": len(ins)}, flush=True)
    if not apply or ins.empty:
        return
    run_id = f"coverage_gap_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = "coverage_gap fill: missing trading-day row inserted from marcap raw / FDR (band-checked) - rollback = delete these rows"
    conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    conn.executemany(
        "INSERT INTO price_history(stock_code,date,open,high,low,close,volume,created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT (stock_code,date) DO NOTHING",
        [(r.code, r.date, r.open, r.high, r.low, r.close, r.volume, now) for r in ins.itertuples()])
    conn.executemany(
        """INSERT INTO price_history_fix_backup (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
           new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES(?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)
           ON CONFLICT DO NOTHING""",
        [(run_id, r.code, r.date, r.open, r.high, r.low, r.close, r.volume, reason + f" [{r.src}]", now) for r in ins.itertuples()])
    conn.execute(
        """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
             new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
        (now, "price_history", "coverage_gap fill", len(ins), "INSERT missing trading-day rows", "row absent",
         "marcap raw / FDR band-checked OHLCV", reason, run_id))
    conn.commit()
    print({"run_id": run_id})
    conn.close()


if __name__ == "__main__":
    mg = int(sys.argv[sys.argv.index("--max-gap") + 1]) if "--max-gap" in sys.argv else 30
    main("--apply" in sys.argv, mg)
