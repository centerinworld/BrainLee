#!/usr/bin/env python3
"""2025-05~2026-05 발견 신호의 월별 동적 검증."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
FEATURE_PATH = ROOT / "research_outputs" / "market2x_signal_dataset.parquet"
OUT_DIR = ROOT / "research_outputs" / "bull_winner_discovery_20260624"
BENCHMARK_UNIVERSE_AVG = 0.4771974631395281
YEARS = (pd.Timestamp("2026-05-29") - pd.Timestamp("2025-05-02")).days / 365.25


def load_signals() -> dict:
    feat = pd.read_parquet(FEATURE_PATH)
    feat["date"] = pd.to_datetime(feat["date"], format="mixed")
    feat = feat[(feat.date >= pd.Timestamp("2025-04-30")) & (feat.date <= pd.Timestamp("2026-05-29"))].copy()
    feat["ym"] = feat.date.dt.to_period("M")
    feat = feat.sort_values(["stock_code", "date"]).groupby(["ym", "stock_code"], as_index=False).tail(1)

    rank_cols = [
        "import_value",
        "borrow_bal_qty",
        "high52",
        "import_yoy",
        "fin_roe",
        "fin_net_margin",
        "avg_turnover20",
        "export_value",
        "export_yoy",
    ]
    for c in rank_cols:
        feat[c + "_rank"] = feat.groupby("ym")[c].rank(pct=True)

    # A: 많이 오른 종목의 핵심 공통점.
    feat["score_A"] = (
        0.25 * feat.import_value_rank.fillna(0)
        + 0.20 * feat.borrow_bal_qty_rank.fillna(0)
        + 0.15 * feat.high52_rank.fillna(0)
        + 0.15 * feat.import_yoy_rank.fillna(0)
        + 0.15 * feat.fin_roe_rank.fillna(0)
        + 0.10 * feat.avg_turnover20_rank.fillna(0)
    )
    feat["sig_A"] = (
        (feat.import_value_rank >= 0.75)
        & (feat.borrow_bal_qty_rank >= 0.75)
        & (feat.high52_rank >= 0.65)
        & (feat.import_yoy.fillna(-9) >= 0)
        & (feat.fin_roe.fillna(-9) > 0)
    )

    # D: 같은 방향이지만 더 넓은 품질/수입 증가형.
    feat["score_D"] = (
        0.25 * feat.import_value_rank.fillna(0)
        + 0.20 * feat.import_yoy_rank.fillna(0)
        + 0.20 * feat.fin_roe_rank.fillna(0)
        + 0.15 * feat.fin_net_margin_rank.fillna(0)
        + 0.20 * feat.avg_turnover20_rank.fillna(0)
    )
    feat["sig_D"] = (
        (feat.import_value_rank >= 0.70)
        & (feat.import_yoy_rank >= 0.55)
        & (feat.fin_roe.fillna(-9) > 0)
        & (feat.avg_turnover20_rank >= 0.65)
    )

    out = {}
    for strat, score_col, sig_col in [("A_import_borrow_high_quality", "score_A", "sig_A"), ("D_import_yoy_quality_liquid", "score_D", "sig_D")]:
        out[strat] = {}
        for d, g in feat[feat[sig_col]].groupby("date"):
            picks = g.sort_values(score_col, ascending=False).head(10)
            out[strat][pd.Timestamp(d).date().isoformat()] = picks[
                ["stock_code", "stock_name", score_col]
            ].values.tolist()
    return out


def load_prices(codes: list[str]) -> dict[str, pd.DataFrame]:
    conn = sqlite3.connect(DB_PATH)
    rows = []
    for i in range(0, len(codes), 500):
        batch = codes[i : i + 500]
        ph = ",".join("?" for _ in batch)
        rows.extend(
            conn.execute(
                f"""
                SELECT stock_code, date, close
                FROM price_history
                WHERE stock_code IN ({ph})
                  AND date BETWEEN '2025-05-01' AND '2026-05-29'
                  AND close > 0
                ORDER BY stock_code, date
                """,
                batch,
            ).fetchall()
        )
    conn.close()
    df = pd.DataFrame(rows, columns=["stock_code", "date", "close"])
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    out = {}
    for sc, g in df.groupby("stock_code"):
        g = g.sort_values("date").copy()
        g["ma10"] = g.close.rolling(10).mean()
        g["ma20"] = g.close.rolling(20).mean()
        g["ma20_slope5"] = g.ma20 / g.ma20.shift(5) - 1
        out[sc] = g.reset_index(drop=True)
    return out


def run_backtest(signals: dict, prices: dict[str, pd.DataFrame], strat: str, exit_rule: str, max_pos: int = 10) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    all_dates = sorted({d for p in prices.values() for d in p.date.unique()})
    signal_dates = set(signals[strat])
    cash = 1.0
    per_slot = 1.0 / max_pos
    positions = {}
    equity = []
    trades = []

    for day in all_dates:
        ds = pd.Timestamp(day).date().isoformat()
        for sc, pos in list(positions.items()):
            row = prices[sc][prices[sc].date == day]
            if row.empty:
                continue
            r = row.iloc[0]
            pos["peak_price"] = max(pos["peak_price"], r.close)
            pos["days"] += 1
            ret = r.close / pos["entry_price"] - 1
            dd = r.close / pos["peak_price"] - 1
            sell = False
            reason = ""
            if exit_rule == "ma20_slope" and pos["days"] >= 20 and r.close < r.ma20 and r.ma20_slope5 < 0:
                sell, reason = True, "ma20_slope_down"
            elif exit_rule == "ma10_dd10" and pos["days"] >= 10 and r.close < r.ma10 and dd <= -0.10:
                sell, reason = True, "ma10_dd10"
            elif exit_rule == "dd15" and pos["days"] >= 10 and dd <= -0.15:
                sell, reason = True, "dd15"
            if sell:
                cash += pos["shares"] * r.close
                trades.append(
                    {
                        "stock_code": sc,
                        "stock_name": pos["stock_name"],
                        "entry": pos["entry_date"],
                        "exit": ds,
                        "entry_price": pos["entry_price"],
                        "exit_price": r.close,
                        "ret": ret,
                        "reason": reason,
                    }
                )
                del positions[sc]

        if ds in signal_dates:
            for sc, name, score in signals[strat][ds]:
                if len(positions) >= max_pos:
                    break
                if sc in positions or sc not in prices:
                    continue
                row = prices[sc][prices[sc].date == day]
                if row.empty or cash < per_slot:
                    continue
                price = float(row.iloc[0].close)
                cash -= per_slot
                positions[sc] = {
                    "stock_name": name,
                    "entry_date": ds,
                    "entry_price": price,
                    "shares": per_slot / price,
                    "peak_price": price,
                    "days": 0,
                }

        value = cash
        for sc, pos in positions.items():
            row = prices[sc][prices[sc].date == day]
            if not row.empty:
                value += pos["shares"] * float(row.iloc[0].close)
        equity.append({"date": ds, "equity": value})

    last_day = all_dates[-1]
    last_ds = pd.Timestamp(last_day).date().isoformat()
    for sc, pos in list(positions.items()):
        row = prices[sc][prices[sc].date == last_day]
        if row.empty:
            continue
        r = row.iloc[0]
        ret = r.close / pos["entry_price"] - 1
        cash += pos["shares"] * r.close
        trades.append(
            {
                "stock_code": sc,
                "stock_name": pos["stock_name"],
                "entry": pos["entry_date"],
                "exit": last_ds,
                "entry_price": pos["entry_price"],
                "exit_price": r.close,
                "ret": ret,
                "reason": "end",
            }
        )
    tdf = pd.DataFrame(trades)
    eq = pd.DataFrame(equity)
    total = float(eq.equity.iloc[-1] - 1)
    mdd = float((eq.equity / eq.equity.cummax() - 1).min())
    result = {
        "strategy": strat,
        "exit_rule": exit_rule,
        "total_return": total,
        "cagr": (1 + total) ** (1 / YEARS) - 1 if total > -1 else -1,
        "alpha_vs_universe_avg": total - BENCHMARK_UNIVERSE_AVG,
        "mdd": mdd,
        "trades": int(len(tdf)),
        "win_rate": float((tdf.ret > 0).mean()) if len(tdf) else 0.0,
        "avg_trade_ret": float(tdf.ret.mean()) if len(tdf) else 0.0,
        "median_trade_ret": float(tdf.ret.median()) if len(tdf) else 0.0,
        "exit_reasons": tdf.reason.value_counts().to_dict() if len(tdf) else {},
    }
    return result, tdf, eq


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    signals = load_signals()
    codes = sorted({x[0] for sig in signals.values() for rows in sig.values() for x in rows})
    prices = load_prices(codes)
    summaries = []
    for strat in signals:
        for exit_rule in ["ma20_slope", "ma10_dd10", "dd15", "none"]:
            result, trades, equity = run_backtest(signals, prices, strat, exit_rule)
            summaries.append(result)
            key = f"{strat}__{exit_rule}"
            trades.to_csv(OUT_DIR / f"backtest_{key}_trades.csv", index=False)
            equity.to_csv(OUT_DIR / f"backtest_{key}_equity.csv", index=False)
    out = pd.DataFrame(summaries).sort_values("total_return", ascending=False)
    out.to_csv(OUT_DIR / "dynamic_candidate_backtests.csv", index=False)
    (OUT_DIR / "dynamic_candidate_backtests.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
