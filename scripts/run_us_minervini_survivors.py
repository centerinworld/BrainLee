#!/usr/bin/env python3
"""Run US Minervini research variants.

Default mode is the research-grade candidate requested for the strategy
center: Nasdaq-100 point-in-time membership from 2007 onward with QQQ as the
benchmark.  ``--universe-mode current`` is retained only for explicit
survivor-bias sensitivity checks.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from us_market_data import aggregate_weekly_ohlcv
from us_backtest_common import (
    USBacktestConfig, USTarget, load_us_bars, load_us_membership_intervals,
    load_us_security_outcomes, membership_eligibility, run_us_backtest,
)


RS_WEIGHTS = {"12m": 0.40, "6m": 0.20, "3m": 0.20, "1m": 0.20}
RS_LOOKBACKS = {"12m": 252, "6m": 126, "3m": 63, "1m": 21}


STRATEGY_SPEC = {
    "strategy_family": "minervini_us",
    "modules": ["trend_template", "sepa", "weekly_vcp"],
    "trend_template": {
        "ma_alignment": "close > ma50 > ma150 > ma200",
        "ma200_20_session_change_min": 0.01,
        "above_52w_low_min": 0.30,
        "below_52w_high_max": 0.25,
        "weighted_rs_percentile_min": 70.0,
        "benchmark_weighted_rs_min": 0.0,
    },
    "relative_strength": {
        "universe_percentile": "weighted 12/6/3/1 month close return percentile within PIT universe",
        "benchmark_excess": "same weighted return minus benchmark weighted return",
        "weights": RS_WEIGHTS,
    },
    "sepa": {
        "eps_yoy_min": 0.20,
        "revenue_yoy_min": 0.15,
        "opm_expansion": True,
        "roe_min_pct": 17.0,
        "analyst_estimates": "not_implemented",
    },
    "weekly_vcp": {
        "source": "stored adjusted daily OHLCV aggregated to weekly",
        "lookback_weeks": 26,
        "min_contractions": 2,
        "next_depth_max_ratio": 0.80,
        "volume_dry_recent_vs_prior_max": 0.70,
        "atr_tight_recent_vs_prior_max": 0.65,
    },
    "execution": {
        "signal": "close_D",
        "fill": "next_market_session_open",
        "close_fallback_allowed": False,
    },
}
STRATEGY_SPEC_HASH = hashlib.sha256(
    json.dumps(STRATEGY_SPEC, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _weighted_return(bars) -> float | None:
    if len(bars) < max(RS_LOOKBACKS.values()) + 1:
        return None
    current = bars[-1].close
    score = 0.0
    for key, lookback in RS_LOOKBACKS.items():
        before = bars[-lookback - 1].close
        if before <= 0:
            return None
        score += RS_WEIGHTS[key] * (current / before - 1)
    return score


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda x: (x[1], x[0]))
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 100.0}
    return {ticker: rank * 100.0 / (n - 1) for rank, (ticker, _) in enumerate(ordered)}


def trend_template(bars, benchmark_bars, rs_snapshot: dict | None = None) -> tuple[bool, float]:
    if len(bars) < 252 or len(benchmark_bars) < 253:
        return False, -999.0
    closes = [x.close for x in bars]
    current = closes[-1]
    ma50 = sum(closes[-50:]) / 50
    ma150 = sum(closes[-150:]) / 150
    ma200 = sum(closes[-200:]) / 200
    ma200_20 = sum(closes[-220:-20]) / 200 if len(closes) >= 220 else 0
    low52, high52 = min(closes[-252:]), max(closes[-252:])
    stock_rs = _weighted_return(bars)
    benchmark_rs = _weighted_return(benchmark_bars)
    if stock_rs is None or benchmark_rs is None:
        return False, -999.0
    rs_percentile = float((rs_snapshot or {}).get("percentile", 0.0))
    benchmark_excess = stock_rs - benchmark_rs
    passed = (
        current > ma50 > ma150 > ma200
        and ma200_20 > 0 and ma200 / ma200_20 - 1 >= 0.01
        and current / low52 - 1 >= 0.30
        and current / high52 - 1 >= -0.25
        and rs_percentile >= 70.0
        and benchmark_excess >= 0.0
    )
    return passed, rs_percentile * 0.01 + benchmark_excess


def vcp_pass(bars) -> bool:
    if len(bars) < 130:
        return False
    import numpy as np
    from scipy.signal import argrelextrema, savgol_filter

    daily_rows = [(x.date, x.open, x.high, x.low, x.close, x.volume) for x in bars]
    weekly = aggregate_weekly_ohlcv(daily_rows)
    if len(weekly) < 26:
        return False
    sample = weekly[-26:]
    prices = np.asarray([x[4] for x in sample], dtype=float)
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
    volumes = np.asarray([x[5] for x in sample], dtype=float)
    if len(volumes) >= 12 and volumes[-12:-2].mean() > 0 and volumes[-2:].mean() > volumes[-12:-2].mean() * 0.7:
        return False
    ranges = np.asarray([(x[2] - x[3]) / x[4] for x in sample], dtype=float)
    if len(ranges) >= 12 and ranges[-12:-2].mean() > 0 and ranges[-2:].mean() > ranges[-12:-2].mean() * 0.65:
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


def make_signal(financials, benchmark_bars, *, use_sepa: bool, use_vcp: bool, top_n: int, signal_audit: list):
    benchmark_dates = [x.date for x in benchmark_bars]

    def signal(day, histories):
        benchmark = benchmark_bars[:bisect_right(benchmark_dates, day)]
        rs_values = {
            ticker: score for ticker, bars in histories.items()
            if (score := _weighted_return(bars)) is not None
        }
        rs_percentiles = _percentile_ranks(rs_values)
        benchmark_rs = _weighted_return(benchmark)
        ranked = []
        for ticker, bars in histories.items():
            rs_snapshot = {
                "weighted_return": rs_values.get(ticker),
                "percentile": rs_percentiles.get(ticker, 0.0),
                "benchmark_weighted_return": benchmark_rs,
                "benchmark_excess": (
                    rs_values[ticker] - benchmark_rs
                    if ticker in rs_values and benchmark_rs is not None else None
                ),
            }
            passed, score = trend_template(bars, benchmark, rs_snapshot)
            if not passed or (use_sepa and not sepa_pass(financials.get(ticker, []), day)):
                continue
            if use_vcp and not vcp_pass(bars):
                continue
            ranked.append((score, ticker, rs_snapshot))
        top_ranked = sorted(ranked, key=lambda x: (x[0], x[1]), reverse=True)[:top_n]
        selected = [ticker for _, ticker, _ in top_ranked]
        signal_audit.append({
            "signal_date": day,
            "selected": [
                {
                    "ticker": ticker,
                    "rs_percentile": round((rs or {}).get("percentile", 0.0), 4),
                    "weighted_rs": round((rs or {}).get("weighted_return") or 0.0, 6),
                    "benchmark_excess": round((rs or {}).get("benchmark_excess") or 0.0, 6),
                }
                for _, ticker, rs in top_ranked
            ],
        })
        return [USTarget(ticker, 1 / len(selected)) for ticker in selected] if selected else []
    return signal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2007-02-01")
    parser.add_argument("--end", default="2026-09-25")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--universe-mode", choices=("current", "pit"), default="pit")
    parser.add_argument("--index", default="NASDAQ100")
    parser.add_argument("--benchmark", default="QQQ")
    parser.add_argument("--output")
    args = parser.parse_args()
    conn = connect_primary_db(readonly=True, timeout=120)
    try:
        interval_meta = None
        intervals = None
        if args.universe_mode == "pit":
            intervals, interval_meta = load_us_membership_intervals(
                args.start, args.end, args.index, conn=conn,
            )
            tickers = sorted({x[0] for x in intervals})
        else:
            latest = conn.execute("SELECT MAX(date) FROM us_price_history WHERE ticker=?", (args.benchmark,)).fetchone()[0]
            tickers = [str(r[0]) for r in conn.execute("""SELECT DISTINCT m.ticker
                FROM us_stock_meta m JOIN us_price_history p ON p.ticker=m.ticker
                WHERE m.index_name=? GROUP BY m.ticker HAVING MAX(p.date)>=? ORDER BY m.ticker""",
                (args.index, latest)).fetchall()]
        outcomes = load_us_security_outcomes(tickers, conn=conn) if intervals else []
        tickers = sorted(set(tickers) | {x.successor_ticker for x in outcomes if x.successor_ticker})
        financials = load_financials(tickers, conn)
        bars, load_quality = load_us_bars(tickers + [args.benchmark], args.start, args.end, conn=conn)
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
        signal_audit = []
        config = USBacktestConfig(
            args.start, args.end, max_positions=args.top, rebalance="week_start",
            benchmark=args.benchmark,
            universe_name=args.index,
            universe_mode="point_in_time" if intervals else "current_membership",
        )
        result = run_us_backtest(
            bars, config, make_signal(
                financials, bars[args.benchmark], use_sepa=use_sepa, use_vcp=use_vcp,
                top_n=args.top, signal_audit=signal_audit,
            ),
            eligibility_fn,
            eligibility_reference_complete=reference_complete,
            security_outcomes=outcomes,
        )
        result.config["strategy"] = f"minervini_us_{name}"
        result.config["strategy_spec_hash"] = STRATEGY_SPEC_HASH
        results[name] = {"metrics": result.metrics, "quality": result.quality,
                         "trade_count": len(result.trades), "data_fingerprint": result.data_fingerprint,
                         "run_id": result.run_id, "sample_signals": signal_audit[-12:]}
    variant_qualities = [x.get("quality", {}) for x in results.values()]
    research_grade = bool(variant_qualities) and all(q.get("research_grade") for q in variant_qualities)
    residual_survivorship_risk = any(q.get("survivorship_bias") for q in variant_qualities)
    pit_price_coverage_values = [
        q.get("pit_price_coverage_min") for q in variant_qualities
        if q.get("pit_price_coverage_min") is not None
    ]
    pit_price_coverage_min = min(pit_price_coverage_values) if pit_price_coverage_values else None
    validation_grade = (
        "research_grade" if research_grade else
        "pit_reference_blocked_by_price_coverage" if intervals and reference_complete else
        "sensitivity_only"
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "start": args.start, "end": args.end,
        "universe": (f"point-in-time {args.index} membership" if intervals else
                     f"current surviving {args.index} members with price on latest {args.benchmark} session"),
        "index": args.index,
        "benchmark": args.benchmark,
        "strategy_spec": STRATEGY_SPEC,
        "strategy_spec_hash": STRATEGY_SPEC_HASH,
        "survivorship_bias": residual_survivorship_risk,
        "pit_reference_complete": bool(intervals and reference_complete),
        "pit_price_coverage_min": pit_price_coverage_min,
        "research_grade": research_grade,
        "validation_grade": validation_grade,
        "forward_validated": False,
        "forward_validation_rule": "Shadow account requires at least 60 days and 20 completed trades before promotion.",
        "universe_ticker_count": len(tickers), "load_quality": load_quality,
        "membership_reference": interval_meta,
        "variants": results,
    }
    default_name = ("us_minervini_nasdaq100_pit_latest.json" if intervals else
                    "us_minervini_survivors_latest.json")
    path = Path(args.output or (ROOT / "research_outputs" / default_name))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
