#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

PERIODS = [
    ("2020-03-01", "2021-11-30", "offensive"),
    ("2021-12-01", "2022-10-31", "defensive"),
    ("2022-11-01", "2023-10-31", "offensive"),
    ("2023-11-01", "2024-12-31", "offensive"),
    ("2024-06-01", "2025-05-31", "defensive"),
]

NON_OVERLAP_CHAIN_IDX = [0, 1, 2, 4]  # P1->P2->P3->P5


@dataclass
class SimResult:
    start_cap: float
    end_cap: float
    ret_pct: float
    mdd_pct: float
    buys: int
    sells: int
    switches: int
    skips_cash: int
    peak_open: int
    avg_ticket: float
    stopouts: int


def d(s: str) -> date:
    y, m, dd = map(int, s.split("-"))
    return date(y, m, dd)


def daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def load_matrix(db_path: str) -> dict[tuple[str, str], dict[str, list[dict]]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT start_date,end_date,strategy,trades_json
        FROM backtest_runs
        WHERE status='done' AND name LIKE 'AUTO %'
        """
    ).fetchall()
    conn.close()

    mat: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(dict)
    for r in rows:
        key = (r["start_date"], r["end_date"])
        data = json.loads(r["trades_json"]) if r["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        mat[key][r["strategy"]] = trades
    return mat


def normalize_candidates(trades_by_strategy: dict[str, list[dict]], weights: dict[str, float], sell_mode: str):
    entries: dict[str, dict[str, dict]] = defaultdict(dict)
    for strat, w in weights.items():
        if strat == "cash" or w <= 0:
            continue
        for t in trades_by_strategy.get(strat, []):
            en = t.get("entry_date")
            ex = t.get("exit_date")
            ep = float(t.get("entry_price") or 0)
            xp = float(t.get("exit_price") or 0)
            code = t.get("stock_code") or t.get("code")
            if not en or not ex or ep <= 0 or xp <= 0 or not code:
                continue
            c = entries[en].get(code)
            if c is None:
                c = {
                    "code": code,
                    "name": t.get("stock_name") or code,
                    "entry_date": en,
                    "entry_price": ep,
                    "score": 0.0,
                    "contrib": [],
                    "exit_dates": [],
                    "exit_prices": [],
                }
                entries[en][code] = c
            c["score"] += w
            c["contrib"].append(strat)
            c["exit_dates"].append(ex)
            c["exit_prices"].append(xp)

    out = defaultdict(list)
    for en, by_code in entries.items():
        for _, c in by_code.items():
            z = c.copy()
            if sell_mode == "earliest":
                k = min(range(len(c["exit_dates"])), key=lambda i: c["exit_dates"][i])
            elif sell_mode == "latest":
                k = max(range(len(c["exit_dates"])), key=lambda i: c["exit_dates"][i])
            else:  # median
                med = sorted(c["exit_dates"])[len(c["exit_dates"]) // 2]
                k = min(range(len(c["exit_dates"])), key=lambda i: abs((d(c["exit_dates"][i]) - d(med)).days))
            z["exit_date"] = c["exit_dates"][k]
            z["exit_price"] = c["exit_prices"][k]
            z.pop("exit_dates", None)
            z.pop("exit_prices", None)
            out[en].append(z)
        out[en].sort(key=lambda x: (x["score"], x["entry_price"]), reverse=True)
    return out


def simulate_period(
    start: str,
    end: str,
    trades_by_strategy: dict[str, list[dict]],
    weights: dict[str, float],
    start_cap: float,
    sell_mode: str = "median",
    reevaluate_days: int = 1,
    min_ticket: float = 5_000_000,
    max_ticket: float = 12_000_000,
    ticket_pct: float = 0.10,
    switch_threshold: float = 0.08,
    dd_cut: float = -0.12,
    cooldown_days: int = 3,
):
    entries = normalize_candidates(trades_by_strategy, weights, sell_mode)

    cash = float(start_cap)
    open_pos: dict[str, dict] = {}
    equity_curve = []
    peak_eq = start_cap
    cooldown_until: date | None = None

    buys = sells = switches = skips_cash = stopouts = 0
    deployed = 0.0
    peak_open = 0

    for day in daterange(d(start), d(end)):
        ds = day.isoformat()

        # natural exits
        closing = [c for c, p in open_pos.items() if p["exit_date"] <= ds]
        for code in closing:
            p = open_pos.pop(code)
            cash += p["qty"] * p["exit_price"]
            sells += 1

        # portfolio DD hard cut
        eq_mark = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        peak_eq = max(peak_eq, eq_mark)
        dd_now = (eq_mark / peak_eq - 1.0) if peak_eq > 0 else 0.0
        if dd_now <= dd_cut and open_pos:
            for code, p in list(open_pos.items()):
                cash += p["qty"] * p["entry_price"]
                sells += 1
                open_pos.pop(code, None)
            stopouts += 1
            cooldown_until = day + timedelta(days=cooldown_days)

        allow_entry = cooldown_until is None or day > cooldown_until

        if allow_entry and ((day.toordinal() - d(start).toordinal()) % max(1, reevaluate_days) == 0):
            cands = entries.get(ds, [])

            # switch-first pass: replace weakest holding if materially better candidate arrives
            if cands and open_pos:
                held_sorted = sorted(open_pos.values(), key=lambda p: p["score"])
                weakest = held_sorted[0]
                for c in cands:
                    if c["code"] in open_pos:
                        continue
                    if c["score"] >= weakest["score"] + switch_threshold:
                        # sell weakest, buy candidate
                        cash += weakest["qty"] * weakest["entry_price"]
                        sells += 1
                        open_pos.pop(weakest["code"], None)
                        switches += 1
                        break

            eq_now = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
            ticket_cap = min(max_ticket, max(min_ticket, eq_now * ticket_pct))

            for c in cands:
                if c["code"] in open_pos:
                    continue
                alloc = min(ticket_cap, cash)
                if alloc < min_ticket:
                    skips_cash += 1
                    continue
                qty = int(alloc // c["entry_price"])
                if qty <= 0:
                    skips_cash += 1
                    continue
                cost = qty * c["entry_price"]
                if cost < min_ticket or cash < cost:
                    skips_cash += 1
                    continue
                cash -= cost
                deployed += cost
                buys += 1
                open_pos[c["code"]] = {
                    "code": c["code"],
                    "qty": qty,
                    "entry_price": c["entry_price"],
                    "exit_price": c["exit_price"],
                    "exit_date": c["exit_date"],
                    "score": c["score"],
                }

        peak_open = max(peak_open, len(open_pos))
        eq = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        equity_curve.append(eq)

    # final liquidation
    for _, p in list(open_pos.items()):
        cash += p["qty"] * p["exit_price"]
        sells += 1
    open_pos.clear()

    end_cap = cash
    ret_pct = (end_cap / start_cap - 1.0) * 100.0 if start_cap > 0 else 0.0

    mdd = 0.0
    if equity_curve:
        peak = equity_curve[0]
        for v in equity_curve + [end_cap]:
            if v > peak:
                peak = v
            dd = (v / peak - 1.0) * 100.0
            if dd < mdd:
                mdd = dd

    avg_ticket = deployed / max(1, buys)
    return SimResult(
        start_cap=start_cap,
        end_cap=end_cap,
        ret_pct=ret_pct,
        mdd_pct=mdd,
        buys=buys,
        sells=sells,
        switches=switches,
        skips_cash=skips_cash,
        peak_open=peak_open,
        avg_ticket=avg_ticket,
        stopouts=stopouts,
    )


def run_all(db_path: str, weight_path: str, out_path: str):
    mat = load_matrix(db_path)
    cfg = json.load(open(weight_path, "r", encoding="utf-8"))
    W = cfg["weights"]

    sell_modes = ["earliest", "median", "latest"]
    reevaluate_days_opts = [1, 3]
    dd_cuts = [-0.10, -0.12, -0.15]

    trials = []
    for sell_mode in sell_modes:
        for rd in reevaluate_days_opts:
            for dd_cut in dd_cuts:
                period_rows = []
                for s, e, rg in PERIODS:
                    res = simulate_period(
                        s,
                        e,
                        mat[(s, e)],
                        W[rg],
                        start_cap=100_000_000,
                        sell_mode=sell_mode,
                        reevaluate_days=rd,
                        min_ticket=5_000_000,
                        max_ticket=12_000_000,
                        ticket_pct=0.10,
                        switch_threshold=0.08,
                        dd_cut=dd_cut,
                        cooldown_days=3,
                    )
                    period_rows.append((s, e, rg, res))

                avg_ret = sum(r.ret_pct for *_, r in period_rows) / len(period_rows)
                worst_ret = min(r.ret_pct for *_, r in period_rows)
                avg_mdd = sum(r.mdd_pct for *_, r in period_rows) / len(period_rows)
                avg_peak = sum(r.peak_open for *_, r in period_rows) / len(period_rows)
                total_switch = sum(r.switches for *_, r in period_rows)
                total_buys = sum(r.buys for *_, r in period_rows)

                # non-overlap chain
                cap = 100_000_000.0
                chain_rows = []
                for idx in NON_OVERLAP_CHAIN_IDX:
                    s, e, rg = PERIODS[idx]
                    rr = simulate_period(
                        s,
                        e,
                        mat[(s, e)],
                        W[rg],
                        start_cap=cap,
                        sell_mode=sell_mode,
                        reevaluate_days=rd,
                        min_ticket=5_000_000,
                        max_ticket=12_000_000,
                        ticket_pct=0.10,
                        switch_threshold=0.08,
                        dd_cut=dd_cut,
                        cooldown_days=3,
                    )
                    chain_rows.append((s, e, rg, rr))
                    cap = rr.end_cap

                chain_ret = (cap / 100_000_000 - 1.0) * 100.0
                chain_worst = min(r.ret_pct for *_, r in chain_rows)
                chain_avg_mdd = sum(r.mdd_pct for *_, r in chain_rows) / len(chain_rows)

                # objective: robust > not just avg return
                score = (
                    avg_ret * 0.35
                    + chain_ret * 0.30
                    + worst_ret * 0.20
                    + chain_worst * 0.10
                    + avg_mdd * 0.15
                    - avg_peak * 0.8
                )

                trials.append({
                    "params": {"sell_mode": sell_mode, "reevaluate_days": rd, "dd_cut": dd_cut},
                    "summary": {
                        "score": round(score, 4),
                        "avg_ret": round(avg_ret, 4),
                        "worst_ret": round(worst_ret, 4),
                        "avg_mdd": round(avg_mdd, 4),
                        "avg_peak_open": round(avg_peak, 2),
                        "total_buys": total_buys,
                        "total_switches": total_switch,
                        "chain_nonoverlap_ret": round(chain_ret, 4),
                        "chain_nonoverlap_worst": round(chain_worst, 4),
                        "chain_nonoverlap_avg_mdd": round(chain_avg_mdd, 4),
                    },
                    "period_reset": [
                        {
                            "start": s,
                            "end": e,
                            "regime": rg,
                            "ret": round(r.ret_pct, 4),
                            "mdd": round(r.mdd_pct, 4),
                            "end_cap": round(r.end_cap),
                            "buys": r.buys,
                            "switches": r.switches,
                            "peak_open": r.peak_open,
                            "avg_ticket": round(r.avg_ticket),
                            "stopouts": r.stopouts,
                        }
                        for s, e, rg, r in period_rows
                    ],
                    "chain_nonoverlap": [
                        {
                            "start": s,
                            "end": e,
                            "regime": rg,
                            "start_cap": round(r.start_cap),
                            "end_cap": round(r.end_cap),
                            "ret": round(r.ret_pct, 4),
                            "mdd": round(r.mdd_pct, 4),
                            "buys": r.buys,
                            "switches": r.switches,
                            "peak_open": r.peak_open,
                            "stopouts": r.stopouts,
                        }
                        for s, e, rg, r in chain_rows
                    ],
                })

    trials.sort(key=lambda x: x["summary"]["score"], reverse=True)
    best = trials[0]

    out = {
        "source": "meta_real_v2_backtest",
        "db_path": db_path,
        "weight_path": weight_path,
        "assumptions": {
            "start_capital": 100000000,
            "min_ticket": 5000000,
            "max_ticket": 12000000,
            "ticket_pct": 0.10,
            "switch_threshold": 0.08,
            "cooldown_days": 3,
            "periods": PERIODS,
            "chain_nonoverlap_indices": NON_OVERLAP_CHAIN_IDX,
        },
        "best": best,
        "top5": trials[:5],
        "trial_count": len(trials),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(out_path)
    print(json.dumps(best["params"], ensure_ascii=False))
    print(json.dumps(best["summary"], ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
    ap.add_argument("--weights", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/config/meta_strategy_weights.json")
    ap.add_argument("--out", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/meta_real_v2_result_2026-05-19.json")
    args = ap.parse_args()
    run_all(args.db, args.weights, args.out)
