#!/usr/bin/env python3
"""Run US Minervini variants on today's surviving S&P 500 members only.

This is intentionally a current-survivor study.  Results must retain the
engine's ``survivorship_bias=true`` label and are not PIT universe evidence.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from us_backtest_common import (
    USBacktestConfig, USTarget, load_us_bars, load_us_membership_intervals,
    load_us_security_outcomes, membership_eligibility, run_us_backtest,
)


def trend_template(bars, spy_bars) -> tuple[bool, float]:
    if len(bars) < 252 or len(spy_bars) < 127:
        return False, -999.0
    closes = [x.close for x in bars]
    current = closes[-1]
    ma50 = sum(closes[-50:]) / 50
    ma150 = sum(closes[-150:]) / 150
    ma200 = sum(closes[-200:]) / 200
    ma200_20 = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else 0
    low52, high52 = min(closes[-252:]), max(closes[-252:])
    stock_6m = current / closes[-127] - 1
    spy_6m = spy_bars[-1].close / spy_bars[-127].close - 1
    passed = (
        current > ma50 > ma150 > ma200
        and ma200_20 > 0 and ma200 / ma200_20 - 1 >= 0.01
        and current / low52 - 1 >= 0.30
        and current / high52 - 1 >= -0.25
        and stock_6m - spy_6m >= 0.15
    )
    return passed, stock_6m - spy_6m


def vcp_pass(bars) -> bool:
    if len(bars) < 100:
        return False
    import numpy as np
    from scipy.signal import argrelextrema, savgol_filter
    sample = bars[-100:]
    prices = np.asarray([x.close for x in sample], dtype=float)
    smoothed = savgol_filter(prices, 11, 3)
    peaks = argrelextrema(smoothed, np.greater, order=3)[0]
    troughs = argrelextrema(smoothed, np.less, order=3)[0]
    events = sorted([(i, "p", prices[i]) for i in peaks] + [(i, "t", prices[i]) for i in troughs])
    depths = []
    peak = None
    for _, kind, value in events:
        if kind == "p":
            peak = value
        elif peak is not None and peak > value:
            depths.append((peak - value) / peak)
            peak = None
    if len(depths) < 2 or depths[-1] >= depths[-2] * 0.8:
        return False
    volumes = np.asarray([x.volume for x in sample], dtype=float)
    if volumes[-60:-10].mean() > 0 and volumes[-10:].mean() > volumes[-60:-10].mean() * 0.7:
        return False
    ranges = np.asarray([(x.high - x.low) / x.close for x in sample], dtype=float)
    if ranges[-60:-10].mean() > 0 and ranges[-10:].mean() > ranges[-60:-10].mean() * 0.65:
        return False
    return True


def _nearest_year_ago(rows, latest):
    target = int(latest["period_end"][:4]) - 1
    same_year = [x for x in rows if int(x["period_end"][:4]) == target]
    if not same_year:
        return None
    month_day = latest["period_end"][5:]
    return min(same_year, key=lambda x: abs(int(x["period_end"][5:7]) - int(month_day[:2])))


def sepa_pass(rows: list[dict], day: str) -> bool:
    available = [x for x in rows if x["avail_date"] <= day]
    quarters_by_end: dict[str, dict] = {}
    for row in available:
        if row["period_type"] != "quarter":
            continue
        old = quarters_by_end.get(row["period_end"])
        completeness = sum(row.get(k) is not None for k in ("revenue", "operating_income", "eps", "opm"))
        old_completeness = sum(old.get(k) is not None for k in ("revenue", "operating_income", "eps", "opm")) if old else -1
        if completeness > old_completeness:
            quarters_by_end[row["period_end"]] = row
    quarters = sorted(quarters_by_end.values(), key=lambda x: x["period_end"])
    if len(quarters) < 5:
        return False
    latest = quarters[-1]
    year_ago = _nearest_year_ago(quarters[:-1], latest)
    if not year_ago:
        return False
    eps, old_eps = latest.get("eps"), year_ago.get("eps")
    rev, old_rev = latest.get("revenue"), year_ago.get("revenue")
    if not eps or not old_eps or old_eps <= 0 or eps / old_eps - 1 < 0.20:
        return False
    if rev and old_rev and old_rev > 0 and rev / old_rev - 1 < 0.15:
        return False
    if latest.get("opm") is not None and quarters[-2].get("opm") is not None:
        if latest["opm"] <= quarters[-2]["opm"]:
            return False
    annual = [x for x in available if x["period_type"] == "annual" and x.get("roe") is not None]
    if annual and sorted(annual, key=lambda x: x["period_end"])[-1]["roe"] < 17:
        return False
    return True


def load_financials(tickers, conn) -> dict[str, list[dict]]:
    placeholders = ",".join("?" for _ in tickers)
    cur = conn.execute(f"""SELECT ticker,period_end,period_type,revenue,operating_income,
                                  eps,roe,opm,avail_date
                             FROM us_financial_data
                            WHERE ticker IN ({placeholders}) AND avail_date IS NOT NULL
                            ORDER BY ticker,avail_date,period_end""", tuple(tickers))
    names = [x[0] for x in cur.description]
    out = defaultdict(list)
    for row in cur.fetchall():
        item = dict(zip(names, tuple(row)))
        out[item["ticker"]].append(item)
    return dict(out)


def make_signal(financials, spy_bars, *, use_sepa: bool, use_vcp: bool, top_n: int):
    spy_dates = [x.date for x in spy_bars]

    def signal(day, histories):
        spy = spy_bars[:bisect_right(spy_dates, day)]
        ranked = []
        for ticker, bars in histories.items():
            passed, score = trend_template(bars, spy)
            if not passed or (use_sepa and not sepa_pass(financials.get(ticker, []), day)):
                continue
            if use_vcp and not vcp_pass(bars):
                continue
            ranked.append((score, ticker))
        selected = [ticker for _, ticker in sorted(ranked, reverse=True)[:top_n]]
        return [USTarget(ticker, 1 / len(selected)) for ticker in selected] if selected else []
    return signal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2022-01-03")
    parser.add_argument("--end", default="2026-09-25")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--universe-mode", choices=("current", "pit"), default="current")
    parser.add_argument("--output")
    args = parser.parse_args()
    conn = connect_primary_db(readonly=True, timeout=120)
    try:
        interval_meta = None
        intervals = None
        if args.universe_mode == "pit":
            intervals, interval_meta = load_us_membership_intervals(
                args.start, args.end, "S&P500", conn=conn,
            )
            tickers = sorted({x[0] for x in intervals})
        else:
            latest = conn.execute("SELECT MAX(date) FROM us_price_history WHERE ticker='SPY'").fetchone()[0]
            tickers = [str(r[0]) for r in conn.execute("""SELECT DISTINCT m.ticker
                FROM us_stock_meta m JOIN us_price_history p ON p.ticker=m.ticker
                WHERE m.index_name='S&P500' GROUP BY m.ticker HAVING MAX(p.date)>=? ORDER BY m.ticker""",
                (latest,)).fetchall()]
        outcomes = load_us_security_outcomes(tickers, conn=conn) if intervals else []
        tickers = sorted(set(tickers) | {x.successor_ticker for x in outcomes if x.successor_ticker})
        financials = load_financials(tickers, conn)
        bars, load_quality = load_us_bars(tickers + ["SPY"], args.start, args.end, conn=conn)
    finally:
        conn.close()
    variants = {
        "trend_template": (False, False),
        "sepa": (True, False),
        "sepa_vcp": (True, True),
    }
    results = {}
    eligibility_fn = membership_eligibility(intervals) if intervals else None
    reference_complete = bool(
        interval_meta and str(interval_meta["first_date"]) <= args.start
        and interval_meta["covers_end"]
    ) if eligibility_fn else True
    for name, (use_sepa, use_vcp) in variants.items():
        config = USBacktestConfig(
            args.start, args.end, max_positions=args.top, rebalance="week_start",
            universe_name="S&P500",
            universe_mode="point_in_time" if intervals else "current_membership",
        )
        result = run_us_backtest(
            bars, config, make_signal(
                financials, bars["SPY"], use_sepa=use_sepa, use_vcp=use_vcp, top_n=args.top,
            ),
            eligibility_fn,
            eligibility_reference_complete=reference_complete,
            security_outcomes=outcomes,
        )
        results[name] = {"metrics": result.metrics, "quality": result.quality,
                         "trade_count": len(result.trades), "data_fingerprint": result.data_fingerprint}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "start": args.start, "end": args.end,
        "universe": ("point-in-time S&P500 membership" if intervals else
                     "current surviving S&P500 members with price on latest SPY session"),
        "survivorship_bias": not bool(intervals and reference_complete),
        "universe_ticker_count": len(tickers), "load_quality": load_quality,
        "membership_reference": interval_meta,
        "variants": results,
    }
    default_name = ("us_minervini_pit_20260927.json" if intervals else
                    "us_minervini_survivors_20260926.json")
    path = Path(args.output or (ROOT / "research_outputs" / default_name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
