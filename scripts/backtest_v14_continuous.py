#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta


def d(s: str) -> date:
    y, m, dd = map(int, s.split("-"))
    return date(y, m, dd)


def daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def blend_weights(wo: dict[str, float], wd: dict[str, float], alpha_off: float) -> dict[str, float]:
    keys = set(wo) | set(wd)
    out: dict[str, float] = {}
    for k in keys:
        out[k] = alpha_off * wo.get(k, 0.0) + (1.0 - alpha_off) * wd.get(k, 0.0)
    s = sum(v for k, v in out.items() if k != "cash")
    for k in list(out):
        if k == "cash":
            out[k] = 0.0
        else:
            out[k] = out[k] / s if s > 0 else 0.0
    return out


def resolve_source_key(day: date) -> tuple[str, str]:
    ds = day.isoformat()
    if ds <= "2021-11-30":
        return ("2020-03-01", "2021-11-30")
    if ds <= "2022-10-31":
        return ("2021-12-01", "2022-10-31")
    if ds <= "2023-10-31":
        return ("2022-11-01", "2023-10-31")
    if ds <= "2024-12-31":
        return ("2023-11-01", "2024-12-31")
    return ("2024-06-01", "2025-05-31")


def load_auto_trades(db_path: str) -> dict[tuple[str, str], dict[str, list[dict]]]:
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


def build_entries(mat: dict, src_key: tuple[str, str], weights: dict[str, float], sell_mode: str) -> dict[str, list[dict]]:
    entries: dict[str, dict[str, dict]] = defaultdict(dict)
    by_strategy = mat[src_key]

    for strat, w in weights.items():
        if strat == "cash" or w <= 0:
            continue
        for t in by_strategy.get(strat, []):
            en = t.get("entry_date")
            ex = t.get("exit_date")
            ep = float(t.get("entry_price") or 0)
            xp = float(t.get("exit_price") or 0)
            code = t.get("stock_code") or t.get("code")
            if not en or not ex or ep <= 0 or xp <= 0 or not code:
                continue
            if not (src_key[0] <= en <= src_key[1]):
                continue

            c = entries[en].get(code)
            if c is None:
                c = {"code": code, "entry_price": ep, "score": 0.0, "exits": []}
                entries[en][code] = c
            c["score"] += w
            c["exits"].append((ex, xp))

    out: dict[str, list[dict]] = {}
    for en, by_code in entries.items():
        arr: list[dict] = []
        for code, c in by_code.items():
            exits = sorted(c["exits"], key=lambda x: x[0])
            if sell_mode == "earliest":
                ex, xp = exits[0]
            elif sell_mode == "latest":
                ex, xp = exits[-1]
            else:
                ex, xp = exits[len(exits) // 2]
            arr.append(
                {
                    "code": code,
                    "entry_price": c["entry_price"],
                    "exit_date": ex,
                    "exit_price": xp,
                    "score": c["score"],
                }
            )
        arr.sort(key=lambda x: (x["score"], x["entry_price"]), reverse=True)
        out[en] = arr
    return out


def run(config_path: str, db_path: str, base_weight_path: str, out_path: str) -> dict:
    cfg = json.load(open(config_path, "r", encoding="utf-8"))
    base = json.load(open(base_weight_path, "r", encoding="utf-8"))

    wo = base["weights"]["offensive"]
    wd = base["weights"]["defensive"]
    alpha_off = float(cfg["weights"]["blend_alpha_offensive"])
    sell_mode = cfg["weights"]["sell_mode"]
    dd_cut = float(cfg["weights"]["dd_cut"])
    crash_guard = bool(cfg["weights"]["crash_guard"])

    min_ticket = float(cfg["execution_constraints"]["min_ticket_krw"])
    max_ticket = float(cfg["execution_constraints"]["max_ticket_krw"])
    ticket_pct = float(cfg["execution_constraints"]["ticket_pct_of_equity"])
    switch_th = float(cfg["execution_constraints"]["switch_threshold_score"])
    switch_stress_add = float(cfg["execution_constraints"]["switch_threshold_stress_add"])
    cooldown_days = int(cfg["execution_constraints"]["cooldown_days_after_stopout"])

    drift20_trig = float(cfg["stress_guard"]["trigger_portfolio_20d_drift"])
    ticket_mult_stress = float(cfg["stress_guard"]["ticket_pct_multiplier"])
    min_score_stress = float(cfg["stress_guard"]["min_score_for_new_entry"])

    start = d(cfg["start_date"])
    end = d(cfg["end_date"])
    cash = float(cfg["capital_krw"])
    start_cap = cash

    mat = load_auto_trades(db_path)
    weights = blend_weights(wo, wd, alpha_off)

    source_windows = [tuple(x) for x in cfg["data_windows_priority"]]
    entry_maps = {k: build_entries(mat, k, weights, sell_mode) for k in source_windows}

    open_pos: dict[str, dict] = {}
    equity_curve: list[float] = []
    peak_eq = cash
    cooldown_until = None
    dayrets = deque(maxlen=20)
    prev_eq = cash

    buys = sells = switches = skips_cash = stopouts = 0
    deployed = 0.0
    peak_open = 0
    year_end_equity: dict[int, float] = {}

    for day in daterange(start, end):
        ds = day.isoformat()
        src_key = resolve_source_key(day)
        entries = entry_maps[src_key]

        # natural exits
        for code, p in list(open_pos.items()):
            if p["exit_date"] <= ds:
                cash += p["qty"] * p["exit_price"]
                sells += 1
                open_pos.pop(code, None)

        eq_mark = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        peak_eq = max(peak_eq, eq_mark)
        dd_now = (eq_mark / peak_eq - 1.0) if peak_eq > 0 else 0.0

        drift20 = sum(dayrets)
        stress = crash_guard and (drift20 < drift20_trig)

        if dd_now <= dd_cut and open_pos:
            for code, p in list(open_pos.items()):
                cash += p["qty"] * p["entry_price"]
                sells += 1
                open_pos.pop(code, None)
            stopouts += 1
            cooldown_until = day + timedelta(days=cooldown_days)

        allow_entry = cooldown_until is None or day > cooldown_until
        if allow_entry:
            cands = entries.get(ds, [])

            # switch logic
            if cands and open_pos:
                weakest = min(open_pos.values(), key=lambda p: p["score"])
                for c in cands:
                    if c["code"] in open_pos:
                        continue
                    th = switch_th + (switch_stress_add if stress else 0.0)
                    if c["score"] >= weakest["score"] + th:
                        cash += weakest["qty"] * weakest["entry_price"]
                        sells += 1
                        switches += 1
                        open_pos.pop(weakest["code"], None)
                        break

            eq_now = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
            cur_ticket_pct = ticket_pct * (ticket_mult_stress if stress else 1.0)
            ticket_cap = min(max_ticket, max(min_ticket, eq_now * cur_ticket_pct))

            for c in cands:
                if c["code"] in open_pos:
                    continue
                if stress and c["score"] < min_score_stress:
                    continue
                alloc = min(ticket_cap, cash)
                if alloc < min_ticket:
                    skips_cash += 1
                    continue
                qty = int(alloc // c["entry_price"])
                cost = qty * c["entry_price"]
                if qty <= 0 or cost < min_ticket or cash < cost:
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
        dayret = 0.0 if prev_eq == 0 else (eq / prev_eq - 1.0)
        dayrets.append(dayret)
        prev_eq = eq

        if day.month == 12 and day.day == 31:
            year_end_equity[day.year] = eq

    # final liquidation
    for code, p in list(open_pos.items()):
        cash += p["qty"] * p["exit_price"]
        sells += 1
        open_pos.pop(code, None)

    end_cap = cash
    ret_pct = (end_cap / start_cap - 1.0) * 100.0

    # MDD
    mdd = 0.0
    if equity_curve:
        peak = equity_curve[0]
        for v in equity_curve + [end_cap]:
            if v > peak:
                peak = v
            dd = (v / peak - 1.0) * 100.0
            if dd < mdd:
                mdd = dd

    # yearly returns (continuous)
    yearly = {}
    base_cap = start_cap
    for y in [2020, 2021, 2022, 2023, 2024]:
        if y in year_end_equity:
            yr = (year_end_equity[y] / base_cap - 1.0) * 100.0
            yearly[str(y)] = round(yr, 4)
            base_cap = year_end_equity[y]
    yearly["2025"] = round((end_cap / base_cap - 1.0) * 100.0, 4)

    out = {
        "version": "V14",
        "config": cfg,
        "weights_effective": weights,
        "result": {
            "start_cap": round(start_cap),
            "end_cap": round(end_cap),
            "total_return_pct": round(ret_pct, 4),
            "max_drawdown_pct": round(mdd, 4),
            "buys": buys,
            "sells": sells,
            "switches": switches,
            "stopouts": stopouts,
            "skip_cash": skips_cash,
            "peak_open_positions": peak_open,
            "avg_ticket_krw": round(deployed / max(1, buys)),
            "yearly_return_pct": yearly,
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(out_path)
    print(json.dumps(out["result"], ensure_ascii=False))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/config/v14_meta_config.json")
    ap.add_argument("--db", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
    ap.add_argument("--base-weights", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/config/meta_strategy_weights.json")
    ap.add_argument("--out", default="/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/v14_continuous_result_2026-05-19.json")
    args = ap.parse_args()
    run(args.config, args.db, args.base_weights, args.out)
