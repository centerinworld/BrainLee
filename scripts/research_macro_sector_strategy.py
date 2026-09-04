#!/usr/bin/env python3
"""Research V-MACRO-SECTOR strategy.

The strategy tests whether recently added macro/quant indicators can become
actual Strategy Center logic instead of remaining only explanatory context.

Two modes are evaluated:
- static_promoted: uses pairs promoted by the latest macro pair backtest. This
  is useful as a ceiling check but has selection look-ahead.
- walk_forward: at each rebalance, a macro pair is eligible only if its prior
  signal outcomes already met the promotion criteria.
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

from scripts.ops.backtest_macro_indicator_candidates import (
    direction_to_light,
    parse_period_available_date,
    period_yoy_key,
)
from scripts.ops.quant_indicator_signal_engine import classify_signal


DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
TCOST = 0.004
MAX_ENTRY_GAP_DAYS = 10


@dataclass(frozen=True)
class PairKey:
    indicator_key: str
    sector_name: str


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def pct(curr: float, base: float) -> float | None:
    if not base:
        return None
    return curr / base - 1.0


def summarize_monthly(rows: list[dict], label: str) -> dict:
    if not rows:
        return {"label": label, "months": 0}
    equity = 100_000_000.0
    peak = equity
    dds = []
    month_returns = []
    for row in rows:
        r = float(row["net_ret"] or 0)
        month_returns.append(r)
        equity *= 1.0 + r
        peak = max(peak, equity)
        dds.append(equity / peak - 1.0)
    wins = [r for r in month_returns if r > 0]
    avg = sum(month_returns) / len(month_returns)
    stdev = pd.Series(month_returns).std(ddof=0)
    return {
        "label": label,
        "months": len(rows),
        "invested_months": sum(1 for r in rows if r.get("positions")),
        "total_return_pct": round((equity / 100_000_000.0 - 1.0) * 100, 2),
        "avg_monthly_pct": round(avg * 100, 2),
        "median_monthly_pct": round(median(month_returns) * 100, 2),
        "win_month_pct": round(len(wins) / len(month_returns) * 100, 2),
        "max_drawdown_pct": round(min(dds) * 100, 2) if dds else None,
        "sharpe_monthly": round((avg / stdev) * math.sqrt(12), 2) if stdev and stdev > 0 else None,
        "ending_equity": round(equity),
    }


def load_pair_rules(c: sqlite3.Connection) -> dict[PairKey, sqlite3.Row]:
    return {
        PairKey(r["indicator_key"], r["sector_name"]): r
        for r in c.execute(
            """
            SELECT indicator_key, sector_name, direction_mode, note
            FROM indicator_sector_direction_rules
            WHERE indicator_key LIKE 'macro:%'
            """
        )
    }


def load_mappings(c: sqlite3.Connection) -> dict[PairKey, list[sqlite3.Row]]:
    rows = c.execute(
        """
        SELECT m.stock_code, m.stock_name, m.sector_name, m.indicator_key, m.indicator_name,
               m.confidence, m.revenue_exposure_pct, m.profit_exposure_pct,
               m.cost_exposure_pct, m.mapping_status, q.sector_name AS signal_sector
        FROM cafe_stock_indicator_mappings m
        JOIN cafe_quant_indicator_mappings q
          ON q.indicator_key=m.indicator_key AND q.sector_name=m.sector_name
        WHERE m.indicator_key LIKE 'macro:%'
          AND m.mapping_status IN ('candidate_macro_context', 'confirmed_macro_signal')
        ORDER BY m.indicator_key, q.sector_name, m.confidence DESC
        """
    ).fetchall()
    out: dict[PairKey, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        out[PairKey(row["indicator_key"], row["signal_sector"])].append(row)
    return out


def load_static_promoted(c: sqlite3.Connection) -> set[PairKey]:
    latest = c.execute("SELECT MAX(created_at) FROM macro_signal_backtest_results").fetchone()[0]
    rows = c.execute(
        """
        SELECT indicator_key, sector_name
        FROM macro_signal_backtest_results
        WHERE created_at=? AND pass_flag=1
        """,
        (latest,),
    ).fetchall()
    return {PairKey(r["indicator_key"], r["sector_name"]) for r in rows}


def load_macro_events(c: sqlite3.Connection, rules: dict[PairKey, sqlite3.Row], mappings: dict[PairKey, list[sqlite3.Row]]) -> list[dict]:
    series_pairs = c.execute(
        """
        SELECT DISTINCT s.indicator_key, s.series_name, cat.epic_indicator_name
        FROM quant_major_indicator_series s
        JOIN quant_major_indicator_catalog cat ON cat.indicator_key=s.indicator_key
        WHERE s.indicator_key LIKE 'macro:%'
        ORDER BY s.indicator_key, s.series_name
        """
    ).fetchall()
    all_events = []
    mapping_keys_by_indicator: dict[str, list[PairKey]] = defaultdict(list)
    for key in mappings:
        mapping_keys_by_indicator[key.indicator_key].append(key)

    for pair in series_pairs:
        rows = c.execute(
            """
            SELECT period, value
            FROM quant_major_indicator_series
            WHERE indicator_key=? AND series_name=? AND value IS NOT NULL
            ORDER BY period
            """,
            (pair["indicator_key"], pair["series_name"]),
        ).fetchall()
        if len(rows) < 14:
            continue
        by_period = {r["period"]: float(r["value"]) for r in rows}
        history: list[float] = []
        previous_value: float | None = None
        for row in rows:
            value = float(row["value"])
            yoy = by_period.get(period_yoy_key(row["period"]) or "")
            signal_type, strength, _mom, _yoy_pct, _z = classify_signal(value, previous_value, yoy, history)
            history.append(value)
            previous_value = value
            if not signal_type:
                continue
            available_date = parse_period_available_date(row["period"])
            if not available_date:
                continue
            for key in mapping_keys_by_indicator.get(pair["indicator_key"], []):
                rule = rules.get(key)
                light = direction_to_light(signal_type, rule["direction_mode"] if rule else None)
                if light != "green":
                    continue
                all_events.append(
                    {
                        "pair": key,
                        "indicator_name": pair["epic_indicator_name"] or key.indicator_key,
                        "series_name": pair["series_name"],
                        "period": row["period"],
                        "available_date": available_date,
                        "signal_type": signal_type,
                        "signal_strength": float(strength or 0),
                    }
                )
    all_events.sort(key=lambda e: (e["available_date"], e["pair"].indicator_key, e["pair"].sector_name))
    return all_events


def trading_months(c: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = c.execute(
        """
        SELECT DISTINCT substr(date,1,7) ym
        FROM price_history
        WHERE date BETWEEN ? AND ?
        ORDER BY ym
        """,
        (start, end),
    ).fetchall()
    return [r["ym"] for r in rows]


def month_entry_exit(c: sqlite3.Connection, stock_code: str, ym: str) -> dict | None:
    start = f"{ym}-01"
    end = (pd.Period(ym, "M").end_time.date()).isoformat()
    rows = c.execute(
        """
        SELECT date, open, close, trade_amount
        FROM price_history
        WHERE stock_code=? AND date BETWEEN ? AND ? AND open>0 AND close>0
        ORDER BY date
        """,
        (stock_code, start, end),
    ).fetchall()
    if len(rows) < 3:
        return None
    first = rows[0]
    last = rows[-1]
    avg_turnover = sum(float(r["trade_amount"] or 0) for r in rows[-20:]) / min(len(rows), 20)
    return {
        "entry_date": first["date"],
        "exit_date": last["date"],
        "entry_open": float(first["open"]),
        "exit_close": float(last["close"]),
        "avg_turnover": avg_turnover,
        "ret": pct(float(last["close"]), float(first["open"])),
    }


def stock_momentum(c: sqlite3.Connection, stock_code: str, before_date: str) -> dict:
    rows = c.execute(
        """
        SELECT close, trade_amount
        FROM price_history
        WHERE stock_code=? AND date<?
        ORDER BY date DESC
        LIMIT 130
        """,
        (stock_code, before_date),
    ).fetchall()
    if len(rows) < 60:
        return {"ret60": None, "ret120": None, "turnover20": None}
    close0 = float(rows[0]["close"])
    ret60 = pct(close0, float(rows[59]["close"])) if len(rows) >= 60 else None
    ret120 = pct(close0, float(rows[119]["close"])) if len(rows) >= 120 else None
    turnover20 = sum(float(r["trade_amount"] or 0) for r in rows[:20]) / min(len(rows), 20)
    return {"ret60": ret60, "ret120": ret120, "turnover20": turnover20}


def price_path_after(
    c: sqlite3.Connection,
    stock_code: str,
    available_date: str,
    horizon_rows: int,
    max_gap_days: int = MAX_ENTRY_GAP_DAYS,
) -> list[sqlite3.Row]:
    try:
        avail = pd.Timestamp(available_date)
    except Exception:
        return []
    upper = (avail + pd.Timedelta(days=horizon_rows * 2 + max_gap_days)).strftime("%Y-%m-%d")
    rows = c.execute(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code=? AND date>=? AND date<=? AND close>0
        ORDER BY date
        LIMIT ?
        """,
        (stock_code, available_date, upper, horizon_rows + 1),
    ).fetchall()
    if not rows:
        return []
    try:
        gap = (pd.Timestamp(str(rows[0]["date"])[:10]) - avail).days
    except Exception:
        return []
    if gap > max_gap_days:
        return []
    return rows


def event_outcomes(c: sqlite3.Connection, events: list[dict], mappings: dict[PairKey, list[sqlite3.Row]]) -> list[dict]:
    outcomes = []
    seen = set()
    for event in events:
        for stock in mappings.get(event["pair"], []):
            key = (event["pair"], event["period"], stock["stock_code"])
            if key in seen:
                continue
            seen.add(key)
            # Need 60 trading observations after availability for prior validation.
            path = price_path_after(c, stock["stock_code"], event["available_date"], 60)
            if len(path) < 61:
                continue
            entry = float(path[0]["close"])
            ret60 = pct(float(path[60]["close"]), entry)
            mdd60 = min(pct(float(row["close"]), entry) for row in path[:61])
            outcomes.append({
                "pair": event["pair"],
                "signal_available": event["available_date"],
                "outcome_available": path[60]["date"],
                "stock_code": stock["stock_code"],
                "ret60": ret60,
                "mdd60": mdd60,
            })
    outcomes.sort(key=lambda o: o["outcome_available"])
    return outcomes


def pair_prior_stats(outcomes: list[dict], as_of: str, min_obs: int = 20) -> dict[PairKey, dict]:
    bucket: dict[PairKey, list[dict]] = defaultdict(list)
    for o in outcomes:
        if o["outcome_available"] < as_of:
            bucket[o["pair"]].append(o)
    stats = {}
    for pair, vals in bucket.items():
        if len(vals) < min_obs:
            continue
        rets = [float(v["ret60"]) for v in vals if v["ret60"] is not None]
        mdds = [float(v["mdd60"]) for v in vals if v["mdd60"] is not None]
        gains = sum(r for r in rets if r > 0)
        losses = abs(sum(r for r in rets if r < 0))
        avg = sum(rets) / len(rets)
        hit = sum(1 for r in rets if r > 0) / len(rets) * 100
        pf = gains / losses if losses else 999
        avg_mdd = sum(mdds) / len(mdds) if mdds else None
        passed = avg >= 0.03 and hit >= 55 and pf >= 1.3 and (avg_mdd is None or avg_mdd >= -0.25)
        if passed:
            stats[pair] = {"obs": len(rets), "avg60": avg, "hit60": hit, "pf60": pf, "avg_mdd": avg_mdd}
    return stats


def run_portfolio(
    c: sqlite3.Connection,
    label: str,
    months: list[str],
    events: list[dict],
    mappings: dict[PairKey, list[sqlite3.Row]],
    eligible_static: set[PairKey] | None,
    outcomes: list[dict],
    top_n: int,
    lookback_days: int = 150,
    require_kospi_ma6: bool = False,
) -> dict:
    monthly = []
    date_rows = c.execute(
        """
        SELECT DISTINCT date
        FROM price_history
        WHERE date BETWEEN ? AND ?
        ORDER BY date
        """,
        (f"{months[0]}-01", pd.Period(months[-1], "M").end_time.date().isoformat()),
    ).fetchall()
    first_date_by_month: dict[str, str] = {}
    for row in date_rows:
        ym = str(row["date"])[:7]
        first_date_by_month.setdefault(ym, row["date"])
    kospi_rows = c.execute(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code='^KS11' AND date BETWEEN '2019-01-01' AND ?
          AND close>0
        ORDER BY date
        """,
        (pd.Period(months[-1], "M").end_time.date().isoformat(),),
    ).fetchall()
    kospi_regime: dict[str, bool] = {}
    if kospi_rows:
        kdf = pd.DataFrame([dict(r) for r in kospi_rows])
        kdf["date"] = pd.to_datetime(kdf["date"])
        kdf["ym"] = kdf["date"].dt.to_period("M").astype(str)
        kdf["close"] = pd.to_numeric(kdf["close"], errors="coerce")
        monthly_k = kdf.loc[kdf.groupby("ym")["date"].idxmax()].sort_values("date").copy()
        monthly_k["ma6"] = monthly_k["close"].rolling(6, min_periods=4).mean()
        monthly_k["ok"] = monthly_k["close"] >= monthly_k["ma6"]
        prev_ok = monthly_k.set_index("ym")["ok"].shift(1)
        kospi_regime = {str(k): bool(v) for k, v in prev_ok.dropna().items()}
    month_px_cache: dict[tuple[str, str], dict | None] = {}
    momentum_cache: dict[tuple[str, str], dict] = {}
    prior_stats_cache: dict[str, dict[PairKey, dict]] = {}

    def cached_month_px(code: str, ym: str) -> dict | None:
        key = (code, ym)
        if key not in month_px_cache:
            month_px_cache[key] = month_entry_exit(c, code, ym)
        return month_px_cache[key]

    def cached_momentum(code: str, before_date: str) -> dict:
        key = (code, before_date)
        if key not in momentum_cache:
            momentum_cache[key] = stock_momentum(c, code, before_date)
        return momentum_cache[key]

    for ym in months:
        # signal must be known before first trading day in execution month.
        first_date = first_date_by_month.get(ym)
        if not first_date:
            continue
        if require_kospi_ma6 and not kospi_regime.get(ym, True):
            monthly.append({
                "month": ym,
                "positions": 0,
                "gross_ret": 0.0,
                "net_ret": 0.0,
                "picked": [],
                "skip_reason": "kospi_below_ma6",
            })
            continue
        cutoff = pd.Timestamp(first_date)
        start = (cutoff - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        active_events = [e for e in events if start <= e["available_date"] < first_date]
        if eligible_static is not None:
            pair_stats = {p: {"obs": 999, "avg60": 0.08, "hit60": 65.0, "pf60": 2.0} for p in eligible_static}
        else:
            if first_date not in prior_stats_cache:
                prior_stats_cache[first_date] = pair_prior_stats(outcomes, first_date)
            pair_stats = prior_stats_cache[first_date]

        candidates = []
        seen_codes = set()
        for event in active_events:
            pair = event["pair"]
            if pair not in pair_stats:
                continue
            stat = pair_stats[pair]
            for stock in mappings.get(pair, []):
                code = stock["stock_code"]
                if code in seen_codes:
                    continue
                px = cached_month_px(code, ym)
                if not px or px["ret"] is None:
                    continue
                mom = cached_momentum(code, first_date)
                if (mom.get("turnover20") or 0) < 2_000_000_000:
                    continue
                ret60 = mom.get("ret60") or 0
                ret120 = mom.get("ret120") or 0
                if ret60 < -0.25:
                    continue
                exposure = max(
                    float(stock["revenue_exposure_pct"] or 0),
                    float(stock["profit_exposure_pct"] or 0),
                    float(stock["cost_exposure_pct"] or 0),
                )
                score = (
                    float(event["signal_strength"] or 0) * 0.8
                    + min(float(stat["avg60"]) * 100, 30) * 0.12
                    + min(float(stat["hit60"]), 90) * 0.04
                    + min(float(stat["pf60"]), 20) * 0.08
                    + float(stock["confidence"] or 0) * 3
                    + min(exposure, 60) * 0.025
                    + max(min(ret60, 1.5), -0.5) * 2
                    + max(min(ret120, 2.0), -0.5)
                )
                candidates.append({
                    "stock_code": code,
                    "stock_name": stock["stock_name"],
                    "pair": f"{pair.indicator_key}|{pair.sector_name}",
                    "indicator_name": event["indicator_name"],
                    "signal_available": event["available_date"],
                    "score": score,
                    "ret": px["ret"],
                    "entry_date": px["entry_date"],
                    "exit_date": px["exit_date"],
                })
                seen_codes.add(code)
        candidates.sort(key=lambda x: x["score"], reverse=True)
        picked = candidates[:top_n]
        gross_ret = sum(float(p["ret"]) for p in picked) / len(picked) if picked else 0.0
        net_ret = gross_ret - TCOST if picked else 0.0
        monthly.append({
            "month": ym,
            "positions": len(picked),
            "gross_ret": gross_ret,
            "net_ret": net_ret,
            "picked": picked,
        })
    return {"summary": summarize_monthly(monthly, label), "monthly": monthly}


def main() -> None:
    c = conn()
    rules = load_pair_rules(c)
    mappings = load_mappings(c)
    events = load_macro_events(c, rules, mappings)
    static_promoted = load_static_promoted(c)
    outcomes = event_outcomes(c, events, mappings)
    months = trading_months(c, "2020-03-01", "2026-06-30")

    results = []
    for top_n in (3, 5, 8, 10):
        results.append(run_portfolio(c, f"static_promoted_top{top_n}", months, events, mappings, static_promoted, outcomes, top_n))
        results.append(run_portfolio(c, f"walk_forward_top{top_n}", months, events, mappings, None, outcomes, top_n))
    for top_n in (5, 8, 10):
        results.append(run_portfolio(
            c,
            f"walk_forward_top{top_n}_kospi_ma6",
            months,
            events,
            mappings,
            None,
            outcomes,
            top_n,
            require_kospi_ma6=True,
        ))

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": "V-MACRO-SECTOR",
        "assumptions": {
            "execution": "monthly rebalance, first open to month-end close",
            "transaction_cost": TCOST,
            "static_promoted_warning": "uses latest promoted pair list; ceiling check only because pair selection has look-ahead",
            "walk_forward": "pair eligibility uses only prior 60-trading-day outcomes available before rebalance",
        },
        "data": {
            "macro_events": len(events),
            "event_outcomes_60d": len(outcomes),
            "mapped_pairs": len(mappings),
            "static_promoted_pairs": len(static_promoted),
            "months": len(months),
        },
        "summaries": [r["summary"] for r in results],
        "results": results,
    }
    OUT_DIR.mkdir(exist_ok=True)
    out_json = OUT_DIR / "macro_sector_strategy_backtest_20260728.json"
    out_md = OUT_DIR / "macro_sector_strategy_backtest_20260728.md"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# V-MACRO-SECTOR Backtest",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- macro_events: {len(events)}",
        f"- event_outcomes_60d: {len(outcomes)}",
        f"- mapped_pairs: {len(mappings)}",
        f"- static_promoted_pairs: {len(static_promoted)}",
        "",
        "| strategy | months | invested | total % | win month % | MDD % | sharpe |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in payload["summaries"]:
        lines.append(
            f"| {s['label']} | {s.get('months',0)} | {s.get('invested_months',0)} | "
            f"{s.get('total_return_pct')} | {s.get('win_month_pct')} | {s.get('max_drawdown_pct')} | {s.get('sharpe_monthly')} |"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(out_json), "md": str(out_md), "summaries": payload["summaries"]}, ensure_ascii=False, indent=2))
    c.close()


if __name__ == "__main__":
    main()
