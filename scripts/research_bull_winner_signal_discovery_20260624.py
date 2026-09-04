#!/usr/bin/env python3
"""
2025-05~2026-05 대세상승장 승자 특성 탐색.

목표:
1) 먼저 많이 상승한 종목의 공통 피처를 찾는다.
2) 같은 1차 신호를 가졌지만 하락/부진한 종목과의 차이를 찾는다.
3) 고정 익절이 아니라 고점 이후 추세가 꺾이는 매도 특징을 데이터로 비교한다.

주의:
- 신호 피처는 2025-04-30 snapshot만 사용한다. 2025-05 이후 수익률/타깃 컬럼은
  피처에 넣지 않는다.
- 2026-06-24에 KRX 공식 지수값으로 ^KS11/^KQ11/^KS200/^KQ150을 정정했다.
  이 분석의 시장 기준은 종목 universe 분포이며, 지수 벤치마크는 별도 재산출한다.
"""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
FEATURE_PATH = ROOT / "research_outputs" / "market2x_signal_dataset.parquet"
OUT_DIR = ROOT / "research_outputs" / "bull_winner_discovery_20260624"

SIGNAL_SNAPSHOT = pd.Timestamp("2025-04-30")
ENTRY_DATE = "2025-05-02"
EXIT_DATE = "2026-05-29"
MIN_CLOSE = 1000
MIN_AVG_TRADE_AMT = 200_000_000
MIN_MARKET_CAP = 30_000_000_000  # 원 단위가 아닌 경우 parquet 값을 그대로 비교하지 않음

TARGET_PREFIXES = ("fwd_", "target_", "market_")
TARGET_COLUMNS = {
    "ret_d",
    "date",
    "month",
    "signal_month",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "volume_x",
    "volume_y",
    "trade_amount_x",
    "trade_amount_y",
    "entry_close",
    "exit_close",
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def nearest_price(conn: sqlite3.Connection, date: str, direction: str) -> pd.DataFrame:
    op = ">=" if direction == "forward" else "<="
    order = "ASC" if direction == "forward" else "DESC"
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT stock_code, date, close, volume, trade_amount,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date {order}) rn
            FROM price_history
            WHERE date {op} ? AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND close > 0
        )
        SELECT stock_code, date, close, volume, trade_amount
        FROM ranked WHERE rn=1
        """,
        (date,),
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def daily_price_panel(conn: sqlite3.Connection, codes: list[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    chunks = []
    for i in range(0, len(codes), 500):
        batch = codes[i : i + 500]
        ph = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT stock_code, date, open, high, low, close, volume,
                   COALESCE(inst_net_buy_amt, inst_net_buy * close, 0) inst_amt,
                   COALESCE(frn_net_buy_amt, frn_net_buy * close, 0) frn_amt,
                   CASE WHEN COALESCE(trade_amount, 0) > 0
                        THEN trade_amount ELSE volume * close END trade_amount
            FROM price_history
            WHERE stock_code IN ({ph}) AND date BETWEEN ? AND ? AND close > 0
            ORDER BY stock_code, date
            """,
            (*batch, "2025-01-01", EXIT_DATE),
        ).fetchall()
        chunks.extend(dict(r) for r in rows)
    df = pd.DataFrame(chunks)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="mixed")
    return df


def build_universe() -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = db()
    entry = nearest_price(conn, ENTRY_DATE, "forward").rename(
        columns={"date": "entry_date", "close": "entry_close"}
    )
    exit_ = nearest_price(conn, EXIT_DATE, "backward").rename(
        columns={"date": "exit_date", "close": "exit_close"}
    )
    uni = pd.read_sql_query(
        """
        SELECT stock_code, COALESCE(stock_name, stock_code) stock_name, market,
               stock_type, market_cap, sector_large, sector_mid, sector_small
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        """,
        conn,
    )
    # Entry 직전 20일 거래대금.
    liq = pd.read_sql_query(
        """
        SELECT stock_code,
               AVG(CASE WHEN COALESCE(trade_amount, 0) > 0
                        THEN trade_amount ELSE volume * close END) avg_trade_amt20,
               MAX(CASE WHEN ABS(close / LAG_CLOSE - 1.0) > 0.70 THEN 1 ELSE 0 END) has_split_like_jump
        FROM (
          SELECT stock_code, date, close, volume, trade_amount,
                 LAG(close) OVER (PARTITION BY stock_code ORDER BY date) LAG_CLOSE
          FROM price_history
          WHERE date BETWEEN '2025-03-28' AND '2025-05-02'
            AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        )
        GROUP BY stock_code
        """,
        conn,
    )
    conn.close()

    ret = entry.merge(exit_, on="stock_code", how="inner")
    ret["period_ret"] = ret["exit_close"] / ret["entry_close"] - 1
    ret = ret.merge(uni, on="stock_code", how="left").merge(liq, on="stock_code", how="left")
    bad_name = (
        "스팩|SPAC|리츠|우선주|우B|우\\)|우$|KODEX|TIGER|ACE|SOL|PLUS|RISE|HANARO|"
        "KOSEF|KBSTAR|ARIRANG|TIMEFOLIO|ETN|레버리지|인버스|선물|채권|액티브|합성"
    )
    ret = ret[
        ret["stock_name"].notna()
        & ret["market"].notna()
        & (ret["entry_close"] >= MIN_CLOSE)
        & (ret["avg_trade_amt20"].fillna(0) >= MIN_AVG_TRADE_AMT)
        & (ret["has_split_like_jump"].fillna(0) == 0)
        & (~ret["stock_name"].fillna("").str.contains(bad_name, regex=True))
    ].copy()
    ret["rank_pct"] = ret["period_ret"].rank(pct=True)
    return ret, ret.sort_values("period_ret", ascending=False)


def load_snapshot_features(returns: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_parquet(FEATURE_PATH)
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    snap = df[df["date"] <= SIGNAL_SNAPSHOT].sort_values(["stock_code", "date"])
    snap = snap.groupby("stock_code", as_index=False).tail(1)
    merged = returns.merge(snap, on="stock_code", how="left", suffixes=("", "_feat"))
    return merged


def numeric_feature_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if c in TARGET_COLUMNS or c in {"period_ret", "rank_pct", "entry_close", "exit_close"}:
            continue
        if c.endswith("_feat"):
            continue
        if any(c.startswith(p) for p in TARGET_PREFIXES):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def feature_table(df: pd.DataFrame, features: list[str], label_col: str) -> pd.DataFrame:
    rows = []
    y = df[label_col].astype(bool)
    base_rate = y.mean()
    for c in features:
        s = pd.to_numeric(df[c], errors="coerce")
        coverage = s.notna().mean()
        if coverage < 0.08 or s.nunique(dropna=True) < 8:
            continue
        win = s[y]
        rest = s[~y]
        if win.notna().sum() < 8 or rest.notna().sum() < 30:
            continue
        q80 = s.quantile(0.8)
        q20 = s.quantile(0.2)
        top = y[s >= q80]
        bot = y[s <= q20]
        iqr = s.quantile(0.75) - s.quantile(0.25)
        std = s.std()
        denom = iqr if iqr and not math.isclose(iqr, 0) else std
        effect = (win.median() - rest.median()) / denom if denom and not math.isclose(denom, 0) else np.nan
        corr = s.rank(pct=True).corr(df["period_ret"].rank(pct=True))
        rows.append(
            {
                "feature": c,
                "coverage": coverage,
                "winner_median": win.median(),
                "rest_median": rest.median(),
                "effect_iqr": effect,
                "rank_corr_return": corr,
                "top20_threshold": q80,
                "top20_winner_rate": top.mean() if len(top) else np.nan,
                "bottom20_winner_rate": bot.mean() if len(bot) else np.nan,
                "lift_top20_vs_base": (top.mean() / base_rate - 1) if len(top) and base_rate else np.nan,
                "base_winner_rate": base_rate,
                "n_top20": int((s >= q80).sum()),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["lift_top20_vs_base", "rank_corr_return"], ascending=False)


def eval_rule(df: pd.DataFrame, clauses: list[tuple[str, str, float]]) -> dict:
    mask = pd.Series(True, index=df.index)
    for c, op, thr in clauses:
        s = pd.to_numeric(df[c], errors="coerce")
        if op == ">=":
            mask &= s >= thr
        elif op == "<=":
            mask &= s <= thr
        else:
            raise ValueError(op)
    pick = df[mask].copy()
    if pick.empty:
        return {"n": 0}
    return {
        "n": int(len(pick)),
        "avg_ret": float(pick["period_ret"].mean()),
        "median_ret": float(pick["period_ret"].median()),
        "hit_50": float((pick["period_ret"] >= 0.5).mean()),
        "hit_100": float((pick["period_ret"] >= 1.0).mean()),
        "loss_rate": float((pick["period_ret"] < 0).mean()),
        "top_decile_rate": float(pick["big_winner"].mean()),
        "clauses": "; ".join(f"{c}{op}{thr:.4g}" for c, op, thr in clauses),
        "stocks": ", ".join(
            pick.sort_values("period_ret", ascending=False)
            .head(12)
            .apply(lambda r: f"{r['stock_name']}({r['period_ret']:.0%})", axis=1)
        ),
    }


def discover_rules(df: pd.DataFrame, feature_rank: pd.DataFrame) -> pd.DataFrame:
    candidates: list[tuple[str, str, float]] = []
    for _, r in feature_rank.head(35).iterrows():
        c = r["feature"]
        if pd.isna(r["rank_corr_return"]):
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if r["rank_corr_return"] >= 0:
            for q in (0.55, 0.65, 0.75):
                candidates.append((c, ">=", float(s.quantile(q))))
        else:
            for q in (0.25, 0.35, 0.45):
                candidates.append((c, "<=", float(s.quantile(q))))
    rows = []
    base_avg = df["period_ret"].mean()
    for k in (1, 2, 3):
        for combo in itertools.combinations(candidates[:45], k):
            # 같은 feature 중복 방지
            if len({x[0] for x in combo}) < len(combo):
                continue
            res = eval_rule(df, list(combo))
            if res.get("n", 0) < 18:
                continue
            if res["avg_ret"] <= base_avg:
                continue
            res["alpha_vs_universe_avg"] = res["avg_ret"] - base_avg
            rows.append(res)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["score"] = (
        out["alpha_vs_universe_avg"] * 1.5
        + out["median_ret"] * 0.8
        + out["hit_100"] * 0.8
        - out["loss_rate"] * 0.7
    )
    return out.sort_values(["score", "avg_ret"], ascending=False).drop_duplicates("clauses").head(60)


def failure_contrast(df: pd.DataFrame, rule: str, features: list[str]) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for clause in rule.split("; "):
        if ">=" in clause:
            c, v = clause.split(">=")
            mask &= pd.to_numeric(df[c], errors="coerce") >= float(v)
        elif "<=" in clause:
            c, v = clause.split("<=")
            mask &= pd.to_numeric(df[c], errors="coerce") <= float(v)
    subset = df[mask].copy()
    if subset.empty:
        return pd.DataFrame()
    subset["selected_winner"] = subset["period_ret"] >= max(0.5, subset["period_ret"].median())
    rows = []
    for c in features:
        s = pd.to_numeric(subset[c], errors="coerce")
        if s.notna().sum() < 12 or s.nunique(dropna=True) < 5:
            continue
        good = s[subset["selected_winner"]]
        bad = s[~subset["selected_winner"]]
        if good.notna().sum() < 5 or bad.notna().sum() < 5:
            continue
        iqr = s.quantile(0.75) - s.quantile(0.25)
        if not iqr:
            iqr = s.std()
        rows.append(
            {
                "feature": c,
                "winner_median_in_signal": good.median(),
                "failed_median_in_signal": bad.median(),
                "effect_iqr": (good.median() - bad.median()) / iqr if iqr else np.nan,
                "rank_corr_return_in_signal": s.rank(pct=True).corr(subset["period_ret"].rank(pct=True)),
            }
        )
    return pd.DataFrame(rows).sort_values("effect_iqr", ascending=False)


def sell_signal_analysis(winners: pd.DataFrame) -> pd.DataFrame:
    conn = db()
    panel = daily_price_panel(conn, winners["stock_code"].tolist())
    conn.close()
    rows = []
    if panel.empty:
        return pd.DataFrame()
    for sc, g in panel.groupby("stock_code"):
        g = g.sort_values("date").copy()
        entry_rows = g[g["date"] >= pd.Timestamp(ENTRY_DATE)]
        if len(entry_rows) < 80:
            continue
        entry_price = entry_rows.iloc[0]["close"]
        g["ret"] = g["close"] / entry_price - 1
        g["ma10"] = g["close"].rolling(10).mean()
        g["ma20"] = g["close"].rolling(20).mean()
        g["ma60"] = g["close"].rolling(60).mean()
        g["ma20_slope5"] = g["ma20"] / g["ma20"].shift(5) - 1
        g["low10_prev"] = g["low"].shift(1).rolling(10).min()
        g["low20_prev"] = g["low"].shift(1).rolling(20).min()
        g["peak_ret_so_far"] = g["ret"].cummax()
        g["drawdown_from_peak"] = (g["close"] / g["close"].cummax()) - 1
        post = g[g["date"] >= pd.Timestamp(ENTRY_DATE)].copy()
        peak_idx = post["ret"].idxmax()
        peak_date = post.loc[peak_idx, "date"]
        peak_ret = post.loc[peak_idx, "ret"]
        if peak_ret < 0.5:
            continue

        signals = {
            "close_below_ma10": post["close"] < post["ma10"],
            "close_below_ma20": post["close"] < post["ma20"],
            "close_below_ma20_slope_down": (post["close"] < post["ma20"]) & (post["ma20_slope5"] < 0),
            "close_below_ma60": post["close"] < post["ma60"],
            "break_10d_low": post["close"] < post["low10_prev"],
            "break_20d_low": post["close"] < post["low20_prev"],
            "drawdown_10pct_from_peak": post["drawdown_from_peak"] <= -0.10,
            "drawdown_15pct_from_peak": post["drawdown_from_peak"] <= -0.15,
            "drawdown_20pct_from_peak": post["drawdown_from_peak"] <= -0.20,
        }
        for name, sig in signals.items():
            after_peak = post[(post["date"] >= peak_date) & sig.fillna(False)]
            anytime = post[sig.fillna(False)]
            row = {
                "stock_code": sc,
                "peak_date": str(peak_date.date()),
                "peak_ret": peak_ret,
                "signal": name,
            }
            if not after_peak.empty:
                ex = after_peak.iloc[0]
                row.update(
                    {
                        "exit_date_after_peak": str(ex["date"].date()),
                        "delay_days_after_peak": int((ex["date"] - peak_date).days),
                        "exit_ret_after_peak": ex["ret"],
                        "capture_after_peak": ex["ret"] / peak_ret if peak_ret else np.nan,
                    }
                )
            if not anytime.empty:
                ex2 = anytime.iloc[0]
                row.update(
                    {
                        "first_exit_date": str(ex2["date"].date()),
                        "first_exit_ret": ex2["ret"],
                        "first_capture": ex2["ret"] / peak_ret if peak_ret else np.nan,
                        "first_before_peak": bool(ex2["date"] < peak_date),
                    }
                )
            rows.append(row)
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw
    agg = (
        raw.groupby("signal")
        .agg(
            n=("stock_code", "count"),
            after_peak_coverage=("exit_ret_after_peak", lambda x: x.notna().mean()),
            median_delay_days=("delay_days_after_peak", "median"),
            median_capture_after_peak=("capture_after_peak", "median"),
            median_first_capture=("first_capture", "median"),
            early_exit_rate=("first_before_peak", "mean"),
        )
        .reset_index()
    )
    return agg.sort_values(
        ["median_capture_after_peak", "early_exit_rate"], ascending=[False, True]
    )


def md_table(df: pd.DataFrame, n: int = 20) -> str:
    if df.empty:
        return "(none)"
    view = df.head(n).copy()
    for c in view.columns:
        if pd.api.types.is_float_dtype(view[c]):
            view[c] = view[c].map(lambda x: "" if pd.isna(x) else f"{x:.4g}")
    cols = list(view.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row[c]).replace("|", "/") for c in cols) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    returns, ranked = build_universe()
    df = load_snapshot_features(returns)

    # Label definitions.
    q90 = df["period_ret"].quantile(0.90)
    q95 = df["period_ret"].quantile(0.95)
    df["big_winner"] = df["period_ret"] >= q90
    df["super_winner"] = df["period_ret"] >= q95
    df["double_or_more"] = df["period_ret"] >= 1.0
    df["loser"] = df["period_ret"] < 0

    features = numeric_feature_columns(df)
    feature_rank = feature_table(df, features, "big_winner")
    rules = discover_rules(df, feature_rank)

    # Failure contrast for the best rule only.
    if not rules.empty:
        contrast = failure_contrast(df, rules.iloc[0]["clauses"], features)
    else:
        contrast = pd.DataFrame()

    winners = df[df["big_winner"]].sort_values("period_ret", ascending=False)
    sell = sell_signal_analysis(winners.head(120))

    summary = {
        "period": {"entry": ENTRY_DATE, "exit": EXIT_DATE, "signal_snapshot": str(SIGNAL_SNAPSHOT.date())},
        "universe": {
            "n": int(len(df)),
            "avg_return": float(df["period_ret"].mean()),
            "median_return": float(df["period_ret"].median()),
            "top_decile_threshold": float(q90),
            "top_5pct_threshold": float(q95),
            "loss_rate": float((df["period_ret"] < 0).mean()),
            "double_or_more_rate": float((df["period_ret"] >= 1.0).mean()),
        },
        "top_winners": winners.head(30)[
            ["stock_code", "stock_name", "period_ret", "sector_large", "sector_mid", "market_cap"]
        ].to_dict("records"),
        "top_features": feature_rank.head(30).to_dict("records") if not feature_rank.empty else [],
        "top_rules": rules.head(20).to_dict("records") if not rules.empty else [],
        "sell_signals": sell.to_dict("records") if not sell.empty else [],
        "data_quality_notes": [
            "KRX index rows (^KS11, ^KQ11, ^KS200, ^KQ150) were repaired from the official KRX API on 2026-06-24.",
            "2026-06-24 index rows were removed because KRX did not yet provide official daily index OHLC for that date.",
            "Feature snapshot uses the latest integrated row on or before 2025-04-30 to avoid using 2025-05~2026-05 future returns.",
            "Rules are discovery candidates, not production strategies; they require rolling/monthly out-of-sample validation next.",
        ],
    }

    ranked.to_csv(OUT_DIR / "period_return_ranking.csv", index=False)
    df.to_parquet(OUT_DIR / "analysis_snapshot.parquet", index=False)
    feature_rank.to_csv(OUT_DIR / "winner_feature_rank.csv", index=False)
    if not rules.empty:
        rules.to_csv(OUT_DIR / "candidate_rule_screen.csv", index=False)
    if not contrast.empty:
        contrast.to_csv(OUT_DIR / "same_signal_failure_contrast.csv", index=False)
    if not sell.empty:
        sell.to_csv(OUT_DIR / "sell_signal_peak_capture.csv", index=False)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Bull Winner Signal Discovery 2026-06-24",
        "",
        f"- Signal snapshot: {SIGNAL_SNAPSHOT.date()}",
        f"- Return window: {ENTRY_DATE} ~ {EXIT_DATE}",
        f"- Universe: {len(df):,} liquid common stocks",
        f"- Universe avg return: {df['period_ret'].mean():.1%}",
        f"- Universe median return: {df['period_ret'].median():.1%}",
        f"- Top decile threshold: {q90:.1%}",
        f"- Top 5% threshold: {q95:.1%}",
        "",
        "## Top Winner Features",
        md_table(feature_rank, 20),
        "",
        "## Candidate Rule Screens",
        md_table(rules, 20),
        "",
        "## Same-Signal Failure Contrast",
        md_table(contrast, 20),
        "",
        "## Sell Signal Peak Capture",
        md_table(sell, 20),
        "",
        "## Data Quality Notes",
        "- KRX index rows were repaired from official KRX API values on 2026-06-24.",
        "- 2026-06-24 index rows were removed because official KRX daily index OHLC was not available yet.",
        "- Candidate rules are discovery outputs only. A production logic needs rolling validation next.",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(summary["universe"], ensure_ascii=False, indent=2))
    print("\nTop rules:")
    print(rules.head(8).to_string(index=False) if not rules.empty else "none")
    print("\nOutputs:", OUT_DIR)


if __name__ == "__main__":
    main()
