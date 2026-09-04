#!/usr/bin/env python3
"""
Tenbagger strategy logic review.

This reruns the v5 tenbagger strategy family with a stricter simulator:
  - 100M KRW budget, 10 independent 10M KRW slots
  - buy/sell transaction cost
  - carry-forward pricing for suspended/missing daily prices
  - high/low based 52-week drawdown
  - candidate guard variants for improvement review
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs"

PER_STOCK = 10_000_000
MAX_POS = 10
TCOST = 0.0035
STOP_LOSS = -0.15
TAKE_PROFIT = 0.60
MAX_HOLD = 200

PERIODS = [
    ("2020-03-01", "2021-11-30", "20.3~21.11 상승장"),
    ("2021-12-01", "2022-10-31", "21.12~22.10 하락장"),
    ("2022-11-01", "2023-10-31", "22.11~23.10 회복"),
    ("2023-11-01", "2024-12-31", "23.11~24.12 AI랠리"),
    ("2024-06-01", "2025-05-31", "24.6~25.5 최근"),
    ("2025-06-01", "2026-06-19", "25.6~26.6 실전기"),
]


def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB, timeout=300)


def load_prices(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    rows = conn.execute(
        """
        SELECT stock_code, date, high, low, close, volume,
               COALESCE(inst_net_buy_amt, 0), COALESCE(frn_net_buy_amt, 0)
        FROM price_history
        WHERE length(stock_code)=6 AND close>0
          AND stock_code NOT LIKE '%^%'
          AND stock_code NOT LIKE 'GC%'
          AND stock_code NOT LIKE 'CL%'
          AND stock_code NOT LIKE '%-F'
          AND stock_code NOT LIKE 'NQ%'
          AND stock_code NOT LIKE 'ES%'
        ORDER BY stock_code, date
        """
    ).fetchall()
    by_code: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_code[r[0]].append((r[1], float(r[2] or r[4]), float(r[3] or r[4]), float(r[4]), int(r[5] or 0), float(r[6]), float(r[7])))
    return dict(by_code)


def load_financials(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    rows = conn.execute(
        """
        SELECT stock_code, year, quarter, is_annual,
               revenue, operating_profit, net_income, total_equity
        FROM financial_data
        WHERE report_type='CFS'
          AND (is_annual=1 AND quarter=4 OR is_annual=0 AND quarter>0)
          AND revenue IS NOT NULL AND revenue > 0
        ORDER BY stock_code, year, quarter
        """
    ).fetchall()

    def release_date(year: int, quarter: int, is_annual: int) -> str:
        if is_annual:
            return datetime(year + 1, 3, 31).strftime("%Y-%m-%d")
        ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
        base = datetime.strptime(f"{year}-{ends.get(quarter, '12-31')}", "%Y-%m-%d")
        return (base + timedelta(days=45)).strftime("%Y-%m-%d")

    by_code: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_code[r[0]].append((release_date(r[1], r[2], r[3]), float(r[4] or 0), r[5], r[6], r[7]))
    for code in by_code:
        by_code[code].sort(key=lambda x: x[0])
    return dict(by_code)


def load_universe(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT stock_code, market, sector_large, shares_issued
        FROM stock_universe
        WHERE market IN ('KOSPI','KOSDAQ') AND length(stock_code)=6
        """
    ).fetchall()
    return {r[0]: {"market": r[1], "sector": r[2], "shares": r[3]} for r in rows}


def trading_days(conn: sqlite3.Connection, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT date FROM price_history
        WHERE stock_code='005930' AND close>0 AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        (start, end),
    ).fetchall()
    return [r[0] for r in rows]


def score_drawdown(cur: float, high52: float, low52: float, tvol: float) -> tuple[float, float | None]:
    if not cur or not high52 or high52 <= 0:
        return 0, None
    from_high = (cur / high52 - 1) * 100
    score = 0
    if -70 <= from_high <= -30:
        score = 22
    elif -85 <= from_high < -70:
        score = 20
    elif -30 < from_high <= -15:
        score = 10
    elif -15 < from_high <= -5:
        score = 4
    if low52 and low52 > 0:
        from_low = (cur / low52 - 1) * 100
        if from_low <= 5:
            score = min(score + 5, 30)
        elif from_low <= 15:
            score = min(score + 3, 30)
    if tvol < 2:
        score = max(0, score - 5)
    return max(0, score), from_high


def score_fundamental(lat: tuple | None, prev: tuple | None) -> float:
    if not lat:
        return 0
    op_cur = lat[2]
    op_prev = prev[2] if prev else None
    rev_cur = lat[1]
    rev_prev = prev[1] if prev else None
    score = 0
    if op_prev is not None and op_cur is not None:
        if (op_prev or 0) < 0 and (op_cur or 0) > 0:
            score += 20
        elif (op_prev or 0) < 0 and (op_cur or 0) < 0 and op_cur > (op_prev or 0) * 0.5:
            score += 10
    if rev_cur and rev_prev and rev_prev > 0:
        rev_growth = (rev_cur - rev_prev) / rev_prev * 100
        if rev_growth >= 50:
            score += 12
        elif rev_growth >= 30:
            score += 8
        elif rev_growth >= 15:
            score += 5
        elif rev_growth >= 5:
            score += 2
    return min(30, score)


def score_value(cur_price: float, equity: float | None, shares: float | None, market_cap_uk: float | None) -> float:
    if not cur_price or cur_price <= 0:
        return 0
    score = 0
    if equity and shares and shares > 0:
        bps = equity / shares
        if bps > 0:
            pbr = cur_price / bps
            if pbr <= 0.5:
                score += 12
            elif pbr <= 1.0:
                score += 9
            elif pbr <= 1.5:
                score += 6
            elif pbr <= 3.0:
                score += 3
    if market_cap_uk and market_cap_uk > 0:
        if 200 <= market_cap_uk <= 3000:
            score += 8
        elif market_cap_uk < 200:
            score += 4
        elif 3000 < market_cap_uk <= 10000:
            score += 3
    return min(25, score)


def score_supply(inst_10d: float, frn_10d: float) -> float:
    if inst_10d > 0 and frn_10d > 0:
        return 15 if inst_10d + frn_10d > 100 else 10
    if inst_10d > 30:
        return 10
    if inst_10d > 0:
        return 7
    if frn_10d > 30:
        return 8
    if frn_10d > 0:
        return 5
    if inst_10d < -200 or frn_10d < -200:
        return -5
    return 0


def tb_base(dd, fund, val, sup, fh):
    total = dd + fund + val + max(0, sup)
    return total >= 55, total


def tb_value(dd, fund, val, sup, fh):
    total = val * 0.6 + fund * 0.25 + dd * 0.15
    return val >= 12, total


def tb_supply_plus(dd, fund, val, sup, fh):
    sup_ok = sup >= 10
    dd_ok = fh is not None and -80 <= fh <= -20
    if not (sup_ok or dd_ok):
        return False, 0
    total = sup * 1.5 + dd + fund * 0.8 + val * 0.3
    return True, total


def tb_hybrid(dd, fund, val, sup, fh):
    sup_driven = sup >= 10 and (dd >= 10 or fund >= 10)
    value_driven = fh is not None and -80 <= fh <= -20 and fund >= 15
    combo_driven = fh is not None and -80 <= fh <= -20 and fund >= 18 and val >= 9
    if not (sup_driven or value_driven or combo_driven):
        return False, 0
    total = dd + fund + val + max(0, sup)
    if combo_driven:
        total += 8
    return total >= 45, total


def tb_hybrid_quality(dd, fund, val, sup, fh):
    ok, total = tb_hybrid(dd, fund, val, sup, fh)
    if not ok:
        return False, 0
    quality_gate = fund >= 12 or val >= 12 or dd >= 22
    if not quality_gate:
        return False, 0
    if sup < -3 and fund < 18:
        return False, 0
    return True, total


def tb_core_balance(dd, fund, val, sup, fh):
    drawdown_ok = fh is not None and -75 <= fh <= -25
    if not drawdown_ok:
        return False, 0
    core = (fund >= 12 and val >= 9) or (fund >= 18) or (val >= 15)
    if not core:
        return False, 0
    total = dd * 1.1 + fund * 1.15 + val * 0.9 + max(0, sup) * 0.8
    return total >= 48, total


STRATEGIES = {
    "tb_base": tb_base,
    "tb_value": tb_value,
    "tb_supply_plus": tb_supply_plus,
    "tb_hybrid": tb_hybrid,
    "tb_hybrid_quality": tb_hybrid_quality,
    "tb_core_balance": tb_core_balance,
}


def run_simulation(strategy_fn, prices_all, fin_all, universe, days):
    price_idx = {code: 0 for code in prices_all}
    last_price: dict[str, float] = {}
    positions: dict[str, dict] = {}
    realized = 0.0
    total_trades = 0
    profit_trades = 0
    equity_curve = []
    picks = []
    initial = PER_STOCK * MAX_POS

    def get_fin(code: str, cur_date: str):
        avail = [f for f in fin_all.get(code, []) if f[0] <= cur_date]
        return (avail[-1], avail[-2] if len(avail) >= 2 else None) if avail else (None, None)

    def features_for(code: str, idx: int):
        hist = prices_all.get(code, [])
        if idx >= len(hist):
            return None
        window = hist[max(0, idx - 260): idx + 1]
        if len(window) < 60:
            return None
        cur = window[-1][3]
        high52 = max(r[1] for r in window[-252:])
        low52 = min(r[2] for r in window[-252:] if r[2] > 0)
        avg_vol5 = sum(r[4] for r in window[-5:]) / min(5, len(window))
        tvol = avg_vol5 * cur / 1e8
        inst10 = sum(r[5] / 100.0 for r in window[-10:] if r[5] != 0)
        frn10 = sum(r[6] / 100.0 for r in window[-10:] if r[6] != 0)
        return cur, high52, low52, tvol, inst10, frn10

    for day_no, cur_date in enumerate(days):
        day_prices = {}
        for code, hist in prices_all.items():
            idx = price_idx.get(code, 0)
            while idx < len(hist) and hist[idx][0] < cur_date:
                idx += 1
            price_idx[code] = idx
            if idx < len(hist) and hist[idx][0] == cur_date:
                day_prices[code] = hist[idx]
                last_price[code] = hist[idx][3]

        to_sell = []
        for code, pos in list(positions.items()):
            cur = last_price.get(code, pos["entry"])
            pos["peak"] = max(pos["peak"], cur)
            ret = cur / pos["entry"] - 1
            hold = day_no - pos["day_no"]
            if ret <= STOP_LOSS or ret >= TAKE_PROFIT or cur / pos["peak"] - 1 <= -0.12 or hold >= MAX_HOLD:
                to_sell.append((code, cur, ret))

        for code, cur, ret in to_sell:
            del positions[code]
            realized += PER_STOCK * (ret - TCOST)
            total_trades += 1
            if ret > 0:
                profit_trades += 1

        if len(positions) < MAX_POS:
            candidates = []
            for code, row in day_prices.items():
                if code in positions:
                    continue
                uni = universe.get(code)
                if not uni:
                    continue
                f = features_for(code, price_idx[code])
                if not f:
                    continue
                cur, high52, low52, tvol, inst10, frn10 = f
                lat, prev = get_fin(code, cur_date)
                shares = uni.get("shares")
                equity = lat[4] if lat and lat[4] is not None else None
                market_cap_uk = cur * shares / 1e8 if shares and shares > 0 else None
                dd, fh = score_drawdown(cur, high52, low52, tvol)
                fund = score_fundamental(lat, prev)
                val = score_value(cur, equity, shares, market_cap_uk)
                sup = score_supply(inst10, frn10)
                selected, score = strategy_fn(dd, fund, val, sup, fh)
                if selected:
                    candidates.append((score, code, cur, dd, fund, val, sup, fh))
            candidates.sort(reverse=True)
            for score, code, cur, dd, fund, val, sup, fh in candidates[:MAX_POS - len(positions)]:
                positions[code] = {"entry": cur, "peak": cur, "day_no": day_no}
                realized -= PER_STOCK * TCOST
                picks.append({"date": cur_date, "stock_code": code, "score": round(score, 2), "dd": dd, "fund": fund, "val": val, "sup": sup, "from_high": round(fh or 0, 1)})

        unrealized = 0.0
        for code, pos in positions.items():
            cur = last_price.get(code, pos["entry"])
            unrealized += PER_STOCK * (cur / pos["entry"] - 1)
        equity_curve.append(initial + realized + unrealized)

    for code, pos in list(positions.items()):
        cur = last_price.get(code, pos["entry"])
        ret = cur / pos["entry"] - 1
        realized += PER_STOCK * (ret - TCOST)
        total_trades += 1
        if ret > 0:
            profit_trades += 1

    final = initial + realized
    total_ret = (final / initial - 1) * 100
    peak = initial
    mdd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            mdd = min(mdd, (value / peak - 1) * 100)
    wins = profit_trades / max(total_trades, 1) * 100
    return {
        "total_return_pct": round(total_ret, 2),
        "mdd_pct": round(mdd, 2),
        "win_rate": round(wins, 1),
        "total_trades": total_trades,
        "profit_trades": profit_trades,
        "pick_count": len(picks),
        "picks": picks,
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    conn = connect()
    prices = load_prices(conn)
    financials = load_financials(conn)
    universe = load_universe(conn)
    days_by_period = {label: trading_days(conn, start, end) for start, end, label in PERIODS}
    conn.close()

    results = {}
    for name, fn in STRATEGIES.items():
        period_results = {}
        for _, _, label in PERIODS:
            period_results[label] = run_simulation(fn, prices, financials, universe, days_by_period[label])
        results[name] = period_results

    summary = []
    for name, period_results in results.items():
        vals = [r["total_return_pct"] for r in period_results.values()]
        mdds = [r["mdd_pct"] for r in period_results.values()]
        positive = sum(1 for v in vals if v >= 0)
        summary.append({
            "strategy": name,
            "avg_return_pct": round(sum(vals) / len(vals), 2),
            "positive_periods": f"{positive}/{len(vals)}",
            "worst_mdd_pct": round(min(mdds), 2),
            **{label: period_results[label]["total_return_pct"] for _, _, label in PERIODS},
        })
    summary.sort(key=lambda x: (x["positive_periods"], x["avg_return_pct"]), reverse=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "settings": {
            "budget_krw": PER_STOCK * MAX_POS,
            "per_stock_krw": PER_STOCK,
            "max_positions": MAX_POS,
            "transaction_cost_roundtrip": TCOST * 2,
            "stop_loss": STOP_LOSS,
            "take_profit": TAKE_PROFIT,
            "max_hold_days": MAX_HOLD,
        },
        "summary": summary,
        "results": results,
    }
    (OUT / "tenbagger_logic_review_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    import pandas as pd
    pd.DataFrame(summary).to_csv(OUT / "tenbagger_logic_review_summary.csv", index=False)
    rows = []
    for name, periods in results.items():
        for label, r in periods.items():
            for pick in r["picks"]:
                rows.append({"strategy": name, "period": label, **pick})
    pd.DataFrame(rows).to_csv(OUT / "tenbagger_logic_review_picks.csv", index=False)
    print(json.dumps({"summary": summary[:8], "files": [
        str(OUT / "tenbagger_logic_review_results.json"),
        str(OUT / "tenbagger_logic_review_summary.csv"),
        str(OUT / "tenbagger_logic_review_picks.csv"),
    ]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
