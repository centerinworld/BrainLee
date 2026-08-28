#!/usr/bin/env python3
import sqlite3
import json
from dataclasses import dataclass
from typing import Dict, List
import pandas as pd
import numpy as np

DB = "/Applications/stock_dashboard/stock.db"
START = "2024-01-01"
END = "2026-05-29"

CAPITAL = 100_000_000
TICKET = 10_000_000
MAX_POS = 10

# 보수적 거래비용 가정
BUY_FEE_RATE = 0.00015
SELL_FEE_RATE = 0.00015
SELL_TAX_RATE = 0.0018
BUY_SLIPPAGE_BPS = 8
SELL_SLIPPAGE_BPS = 8

BASE_STOP = -10.0
BASE_TP1 = 10.0
BASE_TP2 = 20.0

ENH_STOP = -10.0
ENH_TP1 = 10.0
ENH_TP2 = 20.0
ENH_BREAKEVEN = 0.0
ENH_TRAIL = 8.0
ENH_TIME_DAYS = 12
ENH_TIME_MIN_PROFIT = 0.0
ENH_ATR_STOP_K = 2.0
DAILY_LOSS_GUARD = -99.0


@dataclass
class Pos:
    code: str
    name: str
    entry_date: str
    buy: float
    qty: int
    tp1_done: bool = False
    max_profit: float = 0.0


def load_data() -> pd.DataFrame:
    conn = sqlite3.connect(DB)
    q = """
    WITH u AS (
      SELECT stock_code,
             COALESCE(MAX(CASE WHEN market IN ('KOSPI','KOSDAQ') THEN market END), MAX(market)) AS market,
             MAX(stock_name) AS stock_name
      FROM stock_universe
      GROUP BY stock_code
    )
    SELECT p.stock_code, u.stock_name, u.market,
           date(p.date) AS d,
           p.open, p.high, p.low, p.close, p.volume,
           COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) AS trade_amount,
           COALESCE(p.inst_net_buy,0) AS inst_net_buy,
           COALESCE(p.frn_net_buy,0) AS frn_net_buy
    FROM price_history p
    JOIN u ON u.stock_code = p.stock_code
    WHERE date(p.date) BETWEEN ? AND ?
      AND u.market IN ('KOSPI','KOSDAQ')
      AND p.close > 0 AND p.open > 0 AND p.volume > 0
    """
    df = pd.read_sql_query(q, conn, params=[START, END])
    conn.close()
    df["d"] = pd.to_datetime(df["d"])
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["stock_code", "d"]).copy()
    g = df.groupby("stock_code", group_keys=False)

    df["ret_pct"] = (df["close"] / df["open"] - 1.0) * 100.0
    df["body_pct"] = (np.abs(df["close"] - df["open"]) / df["open"]) * 100.0

    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=60).mean())

    df["vol20"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    df["vol_ratio"] = df["volume"] / df["vol20"]

    df["ta20"] = g["trade_amount"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    df["ta_ratio"] = df["trade_amount"] / df["ta20"]

    prev_close = g["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = np.maximum(tr1, np.maximum(tr2, tr3))
    df["atr14"] = g["tr"].transform(lambda s: s.rolling(14, min_periods=14).mean())
    df["atr_pct"] = (df["atr14"] / df["close"]) * 100.0

    df["entry_flag"] = (
        (df["close"] > df["ma20"]) & (df["ma20"] > df["ma60"]) &
        (df["close"] > df["open"]) &
        (df["ret_pct"] >= 5.0) &
        (df["body_pct"] >= 3.0) &
        (df["vol_ratio"] >= 1.8) &
        (df["ta_ratio"] >= 1.5) &
        (df["trade_amount"] >= 5_000_000_000) &
        (df["inst_net_buy"] > 0) & (df["frn_net_buy"] > 0)
    )

    df["score"] = (
        df["ret_pct"].clip(0, 20) * 1.2 +
        df["vol_ratio"].clip(0, 5) * 6.0 +
        df["ta_ratio"].clip(0, 5) * 5.0 +
        ((df["inst_net_buy"] > 0).astype(float) + (df["frn_net_buy"] > 0).astype(float)) * 3.0
    )
    return df


def calc_metrics(equity_curve: List[float], trades: List[dict], start: pd.Timestamp, end: pd.Timestamp):
    if not equity_curve:
        return {}
    init = CAPITAL
    final = equity_curve[-1]
    total_return = (final / init - 1.0) * 100.0
    days = max(1, (end - start).days)
    years = days / 365.25
    cagr = ((final / init) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0

    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (v / peak - 1.0) * 100.0
        if dd < mdd:
            mdd = dd

    closed = [t for t in trades if t["type"] == "sell"]
    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] < 0]
    win_rate = (len(wins) / len(closed) * 100.0) if closed else 0.0
    avg_win = np.mean([t["profit"] for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t["profit"] for t in losses])) if losses else 0.0
    pf = (sum(t["profit"] for t in wins) / abs(sum(t["profit"] for t in losses))) if losses else (999.0 if wins else 0.0)

    rets = pd.Series(equity_curve).pct_change().dropna()
    sharpe = 0.0
    if len(rets) > 3 and rets.std() > 0:
        sharpe = (rets.mean() / rets.std()) * np.sqrt(252)

    return {
        "final_capital": round(final),
        "total_return_pct": round(total_return, 2),
        "cagr_pct": round(cagr, 2),
        "mdd_pct": round(mdd, 2),
        "sharpe": round(float(sharpe), 2),
        "trades": len(closed),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(float(pf), 2),
        "avg_win": round(float(avg_win)),
        "avg_loss": round(float(avg_loss)),
    }


def simulate(df: pd.DataFrame, enhanced: bool) -> dict:
    by_day = {d: g for d, g in df.groupby("d")}
    all_days = sorted(by_day.keys())

    pos: Dict[str, Pos] = {}
    cash = float(CAPITAL)
    trades: List[dict] = []
    equity_curve = []

    prev_equity = float(CAPITAL)

    for day in all_days:
        rows = by_day[day]
        row_map = {r.stock_code: r for r in rows.itertuples(index=False)}

        mtm = cash
        for p in pos.values():
            r = row_map.get(p.code)
            px = float(r.close) if r is not None else p.buy
            mtm += px * p.qty
        day_ret_pct = (mtm / prev_equity - 1.0) * 100.0 if prev_equity > 0 else 0.0
        day_loss_guard_on = enhanced and (day_ret_pct <= DAILY_LOSS_GUARD)

        to_remove = []
        for code, p in list(pos.items()):
            r = row_map.get(code)
            if r is None:
                continue
            cur = float(r.close)
            pct = (cur / p.buy - 1.0) * 100.0
            p.max_profit = max(p.max_profit, pct)

            atr_pct = float(r.atr_pct) if pd.notna(r.atr_pct) else 0.0
            dyn_stop = -max(4.0, ENH_ATR_STOP_K * atr_pct) if enhanced and atr_pct > 0 else -999.0

            sell_qty = 0
            reason = None
            stop_th = min(ENH_STOP, dyn_stop) if enhanced else BASE_STOP
            if pct <= stop_th:
                sell_qty = p.qty; reason = "stop"
            elif enhanced and p.tp1_done:
                if pct < ENH_BREAKEVEN:
                    sell_qty = p.qty; reason = "breakeven"
                elif (pct - p.max_profit) <= -ENH_TRAIL:
                    sell_qty = p.qty; reason = "trail"
                else:
                    hold_days = (day - pd.Timestamp(p.entry_date)).days
                    if hold_days >= ENH_TIME_DAYS and pct < ENH_TIME_MIN_PROFIT:
                        sell_qty = p.qty; reason = "time"

            if sell_qty == 0 and pct >= (ENH_TP2 if enhanced else BASE_TP2):
                sell_qty = p.qty; reason = "tp2"
            elif sell_qty == 0 and (pct >= (ENH_TP1 if enhanced else BASE_TP1)) and (not p.tp1_done):
                sell_qty = max(1, int(np.ceil(p.qty * 0.5)))
                p.tp1_done = True
                reason = "tp1"

            if sell_qty > 0:
                sell_px = cur * (1.0 - SELL_SLIPPAGE_BPS / 10000.0)
                gross = sell_px * sell_qty
                fee = gross * SELL_FEE_RATE
                tax = gross * SELL_TAX_RATE
                proceeds = gross - fee - tax
                cash += proceeds
                profit = (sell_px - p.buy) * sell_qty - fee - tax
                trades.append({
                    "date": day.strftime("%Y-%m-%d"), "type": "sell", "code": code,
                    "price": sell_px, "qty": sell_qty, "profit": profit, "reason": reason
                })
                p.qty -= sell_qty
                if p.qty <= 0:
                    to_remove.append(code)

        for code in to_remove:
            pos.pop(code, None)

        if not day_loss_guard_on:
            can_slots = MAX_POS - len(pos)
            if can_slots > 0 and cash >= TICKET:
                cands = rows[rows["entry_flag"]].sort_values("score", ascending=False)
                for r in cands.itertuples(index=False):
                    if can_slots <= 0 or cash < TICKET:
                        break
                    code = r.stock_code
                    if code in pos:
                        continue
                    close_px = float(r.close)
                    buy_px = close_px * (1.0 + BUY_SLIPPAGE_BPS / 10000.0)
                    qty = int(TICKET // buy_px)
                    if qty <= 0:
                        continue
                    gross = buy_px * qty
                    fee = gross * BUY_FEE_RATE
                    cost = gross + fee
                    if cost > cash:
                        continue
                    cash -= cost
                    pos[code] = Pos(code=code, name=r.stock_name or code, entry_date=day.strftime("%Y-%m-%d"), buy=buy_px, qty=qty)
                    trades.append({
                        "date": day.strftime("%Y-%m-%d"), "type": "buy", "code": code,
                        "price": buy_px, "qty": qty, "profit": -fee, "reason": "entry"
                    })
                    can_slots -= 1

        eq = cash
        for p in pos.values():
            r = row_map.get(p.code)
            px = float(r.close) if r is not None else p.buy
            eq += px * p.qty
        equity_curve.append(eq)
        prev_equity = eq

    if all_days:
        last_day = all_days[-1]
        rows = by_day[last_day]
        row_map = {r.stock_code: r for r in rows.itertuples(index=False)}
        for code, p in list(pos.items()):
            r = row_map.get(code)
            cur = float(r.close) if r is not None else p.buy
            sell_px = cur * (1.0 - SELL_SLIPPAGE_BPS / 10000.0)
            gross = sell_px * p.qty
            fee = gross * SELL_FEE_RATE
            tax = gross * SELL_TAX_RATE
            proceeds = gross - fee - tax
            cash += proceeds
            profit = (sell_px - p.buy) * p.qty - fee - tax
            trades.append({
                "date": last_day.strftime("%Y-%m-%d"), "type": "sell", "code": code,
                "price": sell_px, "qty": p.qty, "profit": profit, "reason": "eod"
            })
            pos.pop(code, None)
        equity_curve[-1] = cash

    metrics = calc_metrics(equity_curve, trades, all_days[0], all_days[-1]) if all_days else {}
    metrics["mode"] = "enhanced" if enhanced else "baseline"
    return metrics


def main():
    df = load_data()
    feats = build_features(df)
    feats = feats[feats["d"] >= pd.Timestamp(START)].copy()

    base = simulate(feats, enhanced=False)
    enh = simulate(feats, enhanced=True)
    out = {
        "period": {"start": START, "end": END},
        "cost_assumption": {
            "buy_fee_rate": BUY_FEE_RATE,
            "sell_fee_rate": SELL_FEE_RATE,
            "sell_tax_rate": SELL_TAX_RATE,
            "buy_slippage_bps": BUY_SLIPPAGE_BPS,
            "sell_slippage_bps": SELL_SLIPPAGE_BPS,
        },
        "baseline": base,
        "enhanced": enh,
        "delta": {k: round(enh.get(k, 0) - base.get(k, 0), 2) for k in ["total_return_pct", "cagr_pct", "mdd_pct", "sharpe", "win_rate_pct", "profit_factor"]}
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
