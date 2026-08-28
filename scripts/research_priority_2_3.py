#!/usr/bin/env python3
"""Priority 2/3 research runs.

2) V-GC + V-RECOVERY overlap backtest.
3) BigQuery-style parameter grid search. If BigQuery is not usable locally,
   run the same grid logic on local price_history.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs" / "priority_2_3_20260704"

FEE_PER_LEG = 0.00015
SELL_TAX = 0.00180
SLIP_TIERS = [(10_000, 0.001), (1_000, 0.002), (100, 0.004), (0, 0.008)]


def tx_cost(mkt_cap_100m: float) -> tuple[float, float]:
    slip = next(r for thr, r in SLIP_TIERS if (mkt_cap_100m or 0) >= thr)
    return FEE_PER_LEG + slip, FEE_PER_LEG + SELL_TAX + slip


def net_return(entry: float, exit_: float, mkt_cap_100m: float) -> float:
    buy_r, sell_r = tx_cost(mkt_cap_100m)
    return ((exit_ * (1 - sell_r)) / (entry * (1 + buy_r)) - 1.0) * 100.0


def max_drawdown(values: list[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v / peak - 1.0) * 100.0)
    return round(mdd, 2)


def ensure_backtest_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY,
            run_id TEXT UNIQUE,
            name TEXT,
            start_date TEXT,
            end_date TEXT,
            per_stock REAL DEFAULT 10000000,
            max_pos INTEGER DEFAULT 10,
            status TEXT DEFAULT 'running',
            total_return_pct REAL,
            ann_return_pct REAL,
            win_rate REAL,
            total_trades INTEGER,
            profit_trades INTEGER,
            max_drawdown_pct REAL,
            trades_json TEXT,
            equity_json TEXT,
            summary_text TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            strategy TEXT DEFAULT 'combo'
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(backtest_runs)")}
    if "strategy" not in cols:
        conn.execute("ALTER TABLE backtest_runs ADD COLUMN strategy TEXT DEFAULT 'combo'")
    conn.commit()


def load_universe(conn: sqlite3.Connection, start: str, end: str, min_mktcap: float) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT DISTINCT p.stock_code, su.stock_name, su.market, su.market_cap AS mkt_cap_100m
        FROM price_history p
        JOIN stock_universe su ON su.stock_code=p.stock_code
        WHERE p.date BETWEEN ? AND ?
          AND p.close >= 500
          AND su.market IN ('KOSPI','KOSDAQ')
          AND su.market_cap >= ?
          AND LENGTH(p.stock_code)=6
          AND COALESCE(su.secugrp_nm,'') NOT LIKE '%ETF%'
          AND COALESCE(su.secugrp_nm,'') NOT LIKE '%ETN%'
          AND COALESCE(su.kind_stkcert_nm,'') NOT LIKE '%ETF%'
          AND COALESCE(su.kind_stkcert_nm,'') NOT LIKE '%ETN%'
        """,
        conn,
        params=(start, end, min_mktcap),
    )


def load_prices(conn: sqlite3.Connection, codes: list[str], warmup: str, end: str) -> pd.DataFrame:
    chunks = []
    for i in range(0, len(codes), 400):
        batch = codes[i : i + 400]
        ph = ",".join("?" for _ in batch)
        chunks.append(
            pd.read_sql_query(
                f"""
                SELECT stock_code, date, close, COALESCE(volume,0) AS volume,
                       COALESCE(trade_amount,0) AS trade_amount
                FROM price_history
                WHERE stock_code IN ({ph})
                  AND date >= ?
                  AND date <= ?
                  AND close > 0
                ORDER BY stock_code, date
                """,
                conn,
                params=batch + [warmup, end],
            )
        )
    df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    if df.empty:
        return df
    df["date"] = df["date"].astype(str).str.slice(0, 10)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for code, g in df.groupby("stock_code", sort=False):
        g = g.sort_values("date").copy()
        c = g["close"]
        v = g["volume"]
        g["ma20"] = c.rolling(20).mean()
        g["ma60"] = c.rolling(60).mean()
        g["ma200"] = c.rolling(200).mean()
        g["vol20"] = v.rolling(20).mean()
        g["vol5"] = v.rolling(5).mean()
        g["high52"] = c.rolling(252).max()
        g["low52"] = c.rolling(252).min()
        g["ma60_gap"] = c / g["ma60"] - 1.0
        g["min_ma60_gap_60d"] = g["ma60_gap"].rolling(60).min()
        g["ret126"] = c / c.shift(126) - 1.0
        g["artifact"] = (c / c.shift(1)).gt(2.0) | (c / c.shift(1)).lt(0.5)
        g["has_artifact"] = bool(g["artifact"].any())
        prev_ok = (g["ma20"].shift(1) <= g["ma60"].shift(1))
        now_ok = (g["ma20"] > g["ma60"])
        g["gc_today"] = prev_ok & now_ok
        g["gc_recent"] = g["gc_today"].rolling(15, min_periods=1).max().astype(bool)
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def run_overlap_backtest(
    start: str = "2021-01-01",
    end: str = "2026-07-03",
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    min_mktcap: float = 500.0,
    recovery_min_drop: float = -0.65,
    recovery_max_drop: float = -0.20,
    recovery_now_min: float = -0.03,
    recovery_now_max: float = 0.25,
    vol_ratio: float = 1.2,
    stop_pct: float = -0.15,
    trail_pct: float = -0.25,
    max_hold: int = 300,
) -> dict:
    conn = sqlite3.connect(DB, timeout=120)
    ensure_backtest_table(conn)
    warmup = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=420)).strftime("%Y-%m-%d")
    uni = load_universe(conn, start, end, min_mktcap)
    prices = add_indicators(load_prices(conn, uni["stock_code"].tolist(), warmup, end))
    prices = prices.merge(uni, on="stock_code", how="left")
    prices = prices[~prices["has_artifact"]].copy()
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].copy()
    prices = prices.drop_duplicates(["stock_code", "date"], keep="last")

    prices["v_gc"] = (
        prices["gc_recent"]
        & (prices["ma20"] > prices["ma60"])
        & (prices["vol5"] >= prices["vol20"] * vol_ratio)
    )
    prices["v_recovery"] = (
        prices["min_ma60_gap_60d"].between(recovery_min_drop, recovery_max_drop)
        & prices["ma60_gap"].between(recovery_now_min, recovery_now_max)
        & (prices["close"] >= prices["ma60"] * (1.0 + recovery_now_min))
    )
    prices["entry_signal"] = prices["v_gc"] & prices["v_recovery"]
    signal_df = prices[prices["entry_signal"]].copy()
    signal_map = {
        d: g.sort_values(["ret126", "mkt_cap_100m"], ascending=[False, True]).to_dict("records")
        for d, g in signal_df.groupby("date")
    }
    px_map = {code: g.set_index("date").to_dict("index") for code, g in prices.groupby("stock_code")}
    dates = sorted(prices["date"].unique())

    cash = per_stock * max_positions
    positions: dict[str, dict] = {}
    trades = []
    equity = []
    month_new: dict[str, int] = {}
    max_new_per_month = max_positions

    for day in dates:
        to_sell = []
        for code, p in list(positions.items()):
            row = px_map.get(code, {}).get(day)
            if not row:
                continue
            close = float(row["close"])
            p["peak"] = max(p["peak"], close)
            ret = close / p["entry"] - 1.0
            trail = close / p["peak"] - 1.0
            reason = None
            if ret <= stop_pct:
                reason = "손절"
            elif ret > 0.05 and trail <= trail_pct:
                reason = "Trail"
            elif p["hold"] >= max_hold:
                reason = "만료"
            if reason:
                net_pct = net_return(p["entry"], close, p["mkt_cap_100m"])
                pnl = p["cost"] * net_pct / 100.0
                cash += p["cost"] + pnl
                trades.append({
                    "stock_code": code,
                    "stock_name": p["stock_name"],
                    "entry_date": p["entry_date"],
                    "exit_date": day,
                    "entry_price": round(p["entry"], 2),
                    "exit_price": round(close, 2),
                    "profit_pct": round(net_pct, 2),
                    "profit_amt": round(pnl),
                    "hold_days": p["hold"],
                    "exit_reason": reason,
                    "market": p["market"],
                })
                to_sell.append(code)
            else:
                p["hold"] += 1
        for code in to_sell:
            positions.pop(code, None)

        ym = day[:7]
        if cash >= per_stock and len(positions) < max_positions:
            for r in signal_map.get(day, []):
                code = r["stock_code"]
                if code in positions:
                    continue
                if month_new.get(ym, 0) >= max_new_per_month:
                    break
                close = float(r["close"])
                if close <= 0:
                    continue
                cost = min(per_stock, cash)
                cash -= cost
                positions[code] = {
                    "entry": close,
                    "entry_date": day,
                    "cost": cost,
                    "peak": close,
                    "hold": 1,
                    "mkt_cap_100m": float(r.get("mkt_cap_100m") or 500),
                    "stock_name": r.get("stock_name") or code,
                    "market": r.get("market"),
                }
                month_new[ym] = month_new.get(ym, 0) + 1
                if cash < per_stock or len(positions) >= max_positions:
                    break

        mtm = cash
        for code, p in positions.items():
            row = px_map.get(code, {}).get(day)
            if row:
                mtm += p["cost"] * (float(row["close"]) / p["entry"])
            else:
                mtm += p["cost"]
        equity.append({"date": day, "equity": round(mtm, 2)})

    if dates:
        last = dates[-1]
        for code, p in list(positions.items()):
            row = px_map.get(code, {}).get(last)
            if not row:
                continue
            close = float(row["close"])
            net_pct = net_return(p["entry"], close, p["mkt_cap_100m"])
            pnl = p["cost"] * net_pct / 100.0
            cash += p["cost"] + pnl
            trades.append({
                "stock_code": code,
                "stock_name": p["stock_name"],
                "entry_date": p["entry_date"],
                "exit_date": last,
                "entry_price": round(p["entry"], 2),
                "exit_price": round(close, 2),
                "profit_pct": round(net_pct, 2),
                "profit_amt": round(pnl),
                "hold_days": p["hold"],
                "exit_reason": "잔존",
                "market": p["market"],
            })

    start_cap = per_stock * max_positions
    total_return = (cash / start_cap - 1.0) * 100.0
    days = max((datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days, 1)
    cagr = ((cash / start_cap) ** (365.0 / days) - 1.0) * 100.0
    wins = [t for t in trades if t["profit_pct"] > 0]
    losses = [t for t in trades if t["profit_pct"] <= 0]
    gross_profit = sum(t["profit_amt"] for t in wins)
    gross_loss = -sum(t["profit_amt"] for t in losses)
    summary = {
        "strategy": "v_gc_and_v_recovery",
        "definition": {
            "v_gc": f"MA20/MA60 golden cross within 15d, MA20>MA60, vol5 >= vol20*{vol_ratio}",
            "v_recovery": f"prior 60d MA60 gap between {recovery_min_drop:.0%} and {recovery_max_drop:.0%}, current gap {recovery_now_min:.0%}~{recovery_now_max:.0%}",
            "sell": f"trail {trail_pct:.0%}, stop {stop_pct:.0%}, max_hold {max_hold}d",
        },
        "scope": {
            "start": start,
            "end": end,
            "universe_stocks": int(uni["stock_code"].nunique()),
            "signal_rows": int(len(signal_df)),
            "signal_stocks": int(signal_df["stock_code"].nunique()) if len(signal_df) else 0,
        },
        "metrics": {
            "total_return_pct": round(total_return, 2),
            "cagr_pct": round(cagr, 2),
            "max_drawdown_pct": max_drawdown([e["equity"] for e in equity]),
            "trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "avg_trade_pct": round(sum(t["profit_pct"] for t in trades) / len(trades), 2) if trades else 0,
            "median_trade_pct": round(float(pd.Series([t["profit_pct"] for t in trades]).median()), 2) if trades else 0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        },
        "top_trades": sorted(trades, key=lambda x: x["profit_pct"], reverse=True)[:30],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "priority2_v_gc_recovery_backtest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame(trades).to_csv(OUT_DIR / "priority2_v_gc_recovery_trades.csv", index=False)

    run_id = f"p2_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_runs
        (run_id,name,start_date,end_date,per_stock,max_pos,status,total_return_pct,ann_return_pct,
         win_rate,total_trades,profit_trades,max_drawdown_pct,summary_text,trades_json,equity_json,strategy)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            "2순위 V-GC+V-RECOVERY 교차 앙상블",
            start,
            end,
            per_stock,
            max_positions,
            "done",
            summary["metrics"]["total_return_pct"],
            summary["metrics"]["cagr_pct"],
            summary["metrics"]["win_rate_pct"],
            summary["metrics"]["trades"],
            len(wins),
            summary["metrics"]["max_drawdown_pct"],
            json.dumps(summary, ensure_ascii=False),
            json.dumps(trades[:200], ensure_ascii=False),
            json.dumps(equity, ensure_ascii=False),
            "v_gc_and_v_recovery",
        ),
    )
    conn.commit()
    conn.close()
    summary["backtest_run_id"] = run_id
    return summary


@dataclass
class GridParam:
    cross_days: int
    vol_ratio: float
    min_drop: float
    max_drop: float
    now_min: float
    now_max: float
    hold_days: int


def eval_grid(prices: pd.DataFrame, param: GridParam) -> dict | None:
    df = prices
    df["v_gc"] = (
        df[f"gc_recent_{param.cross_days}"]
        & (df["ma20"] > df["ma60"])
        & (df["vol5"] >= df["vol20"] * param.vol_ratio)
    )
    df["v_recovery"] = (
        df["min_ma60_gap_60d"].between(param.min_drop, param.max_drop)
        & df["ma60_gap"].between(param.now_min, param.now_max)
    )
    signals = df[df["v_gc"] & df["v_recovery"]].copy()
    if len(signals) < 40:
        return None
    col = f"fwd_{param.hold_days}d_ret_pct"
    s = signals[col].dropna()
    if len(s) < 40:
        return None
    gp = s[s > 0].sum()
    gl = -s[s <= 0].sum()
    return {
        **asdict(param),
        "signals": int(len(signals)),
        "trades": int(len(s)),
        "avg_return_pct": round(float(s.mean()), 2),
        "median_return_pct": round(float(s.median()), 2),
        "win_rate_pct": round(float((s > 0).mean() * 100), 2),
        "double_rate_pct": round(float((s >= 100).mean() * 100), 2),
        "triple_rate_pct": round(float((s >= 200).mean() * 100), 2),
        "loss_30_rate_pct": round(float((s <= -30).mean() * 100), 2),
        "profit_factor": round(float(gp / gl), 2) if gl > 0 else None,
        "profit_score": round(float(s.mean() * math.sqrt(len(s))), 4),
    }


def run_local_grid(start: str = "2021-01-01", end: str = "2026-07-03", min_mktcap: float = 500.0) -> dict:
    conn = sqlite3.connect(DB, timeout=120)
    warmup = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=420)).strftime("%Y-%m-%d")
    uni = load_universe(conn, start, end, min_mktcap)
    prices = add_indicators(load_prices(conn, uni["stock_code"].tolist(), warmup, end)).merge(uni, on="stock_code", how="left")
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end) & (~prices["has_artifact"])].copy()
    prices = prices.drop_duplicates(["stock_code", "date"], keep="last")
    for cross_days in (10, 15, 20):
        prices[f"gc_recent_{cross_days}"] = (
            prices.groupby("stock_code", sort=False)["gc_today"]
            .transform(lambda s: s.rolling(cross_days, min_periods=1).max())
            .astype(bool)
        )
    for hold in (60, 126, 252):
        prices[f"fwd_{hold}d_ret_pct"] = prices.groupby("stock_code", sort=False)["close"].shift(-hold) / prices["close"] * 100.0 - 100.0
    conn.close()

    rows = []
    params = [
        GridParam(*p)
        for p in product(
            [10, 15, 20],
            [1.0, 1.2, 1.5],
            [-0.65, -0.55],
            [-0.20, -0.15],
            [-0.05, -0.03, 0.00],
            [0.15, 0.25, 0.35],
            [60, 126, 252],
        )
    ]
    for idx, param in enumerate(params, start=1):
        row = eval_grid(prices, param)
        if row:
            rows.append(row)
        if idx % 100 == 0:
            print(f"[grid] {idx}/{len(params)} candidates, valid={len(rows)}", flush=True)

    df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_csv = OUT_DIR / "priority3_local_grid_all.csv"
    top_csv = OUT_DIR / "priority3_local_grid_top50.csv"
    if not df.empty:
        df.sort_values(["profit_score", "avg_return_pct", "trades"], ascending=[False, False, False]).to_csv(all_csv, index=False)
        df.sort_values(["profit_score", "avg_return_pct", "trades"], ascending=[False, False, False]).head(50).to_csv(top_csv, index=False)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {"start": start, "end": end, "universe_stocks": int(uni["stock_code"].nunique()), "grid_params": len(params)},
        "note": "BigQuery grid substitute over local price_history. Same parameter-search intent; no external BQ upload required.",
        "top_by_profit_score": df.sort_values(["profit_score", "avg_return_pct"], ascending=[False, False]).head(20).to_dict("records") if not df.empty else [],
        "files": {"all": str(all_csv), "top50": str(top_csv)},
    }
    (OUT_DIR / "priority3_local_grid_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["priority2", "priority3", "all"], default="all")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-07-03")
    args = ap.parse_args()
    out = {}
    if args.mode in ("priority2", "all"):
        print("[priority2] V-GC + V-RECOVERY overlap backtest", flush=True)
        out["priority2"] = run_overlap_backtest(args.start, args.end)
    if args.mode in ("priority3", "all"):
        print("[priority3] local BigQuery-style grid search", flush=True)
        out["priority3"] = run_local_grid(args.start, args.end)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
