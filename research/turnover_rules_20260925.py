#!/usr/bin/env python3
"""HANDOFF §12 R3 (research venv): do turnover rules improve the cost-net result of the momentum/breakout proxy?

Proxy signal = regime_filter_momentum_20260925.py (60d high + volume>1.5x + 120d momentum>0, liquid top-1000, active), gated by the operating
regime guard (KOSPI>MA60 at the signal date). Trade-level simulator (vectorbt cannot express a minimum holding period):
  entry at next-day close; exit = 12% trailing stop (only after `min_hold` days) or 60-day time exit; fixed weight 1/20 of initial equity,
  max 20 concurrent, same-day ties by signal strength. Costs = operating model (fee 0.015%/leg, sell tax 0.18%, market-cap slippage tiers).
Rules: min_hold {0,5,10,20} days | strength top-q of that day's breakouts {1,.5,.25} | re-entry cooldown {0,10,20} days | mcap floor {0,100,1000}억.
A rule set is 'accepted' only if BOTH train (2020-06~2024-12) and valid (2025-01~) cost-net CAGR beat the baseline, Sharpe does not fall in either, AND the 2026-07~09 crash-window CAGR is not worse. Market cap is point-in-time (a latest-snapshot cap leaked survival bias and made the floor look far better).
Output: research_outputs/turnover_rules_20260925.{csv,md}
"""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
FEE, TAX, STOP, HOLD_MAX, MAXPOS = 0.00015, 0.0018, 0.12, 60, 20
TIERS = [(10_000, 0.001), (1_000, 0.002), (100, 0.004), (0, 0.008)]
TRAIN = ("2020-06-01", "2024-12-31")
VALID = ("2025-01-01", "2026-09-23")
CRASH = ("2026-07-01", "2026-09-23")


def slip(m: float) -> float:
    return next(r for t, r in TIERS if m >= t)


def build_signals():
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    bm = pd.read_parquet(IN / "benchmark.parquet").close.reindex(adj.index).ffill()
    px = adj.ffill(limit=5)
    high60 = px.rolling(60, min_periods=50).max()
    volr = vol / vol.rolling(20, min_periods=15).mean()
    mom120 = px / px.shift(120) - 1
    turn = (adj * vol).rolling(60, min_periods=40).mean()
    liquid = turn.rank(axis=1, ascending=False) <= 1000
    active = (vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5
    breakout = (px >= high60) & (volr > 1.5) & (mom120 > 0) & liquid & active
    first = breakout & ~breakout.shift(1, fill_value=False)
    gate = (bm > bm.rolling(60).mean())
    first &= gate.values[:, None]
    snap = snap[["stock_code", "snapshot_date", "market_cap_억"]].dropna().copy()
    snap["snapshot_date"] = pd.to_datetime(snap.snapshot_date)
    strength = (volr.clip(upper=10) * mom120.clip(0, 3))
    return px, first, strength, snap.sort_values("snapshot_date")


def candidates(px, first, strength, snap):
    """One row per potential entry: signal date index i (entry executes at i+1 close). Market cap is POINT-IN-TIME: the last monthly
    snapshot on/before the signal date (a latest-snapshot cap would leak survival/growth into a size floor)."""
    di, ci = np.where(first.to_numpy())
    rows = []
    prices = px.to_numpy()
    for i, j in zip(di, ci):
        if i + 2 >= len(px):
            continue
        s = strength.iat[i, j]
        rows.append((i + 1, j, float(s) if np.isfinite(s) else 0.0))
    df = pd.DataFrame(rows, columns=["t0", "j", "strength"])
    df["signal_date"] = px.index[df.t0.to_numpy() - 1]
    df["stock_code"] = px.columns[df.j.to_numpy()]
    df["n_day"] = df.groupby("t0").t0.transform("size")
    df["rank_ord"] = df.groupby("t0").strength.rank(ascending=False, method="first")
    df = pd.merge_asof(df.sort_values("signal_date"), snap.rename(columns={"snapshot_date": "signal_date"}),
                       on="signal_date", by="stock_code", direction="backward", tolerance=pd.Timedelta(days=45))
    df["mcap"] = df["market_cap_억"].fillna(50.0)          # no snapshot within 45d -> treated as small (worst slippage tier)
    return df.sort_values(["t0", "strength"], ascending=[True, False]).reset_index(drop=True), prices


def simulate(cands, prices, n_days, min_hold, q, cooldown, floor):
    pnl = np.zeros(n_days)
    cost_total, trades = 0.0, 0
    w = 1.0 / MAXPOS
    live_until: list[int] = []          # exit day index of live positions
    last_exit: dict[int, int] = {}
    sel = cands[(cands.rank_ord <= np.ceil(q * cands.n_day)) & (cands.mcap >= floor)]
    for t0, j, mc in zip(sel.t0.to_numpy(), sel.j.to_numpy(), sel.mcap.to_numpy()):
        live_until = [e for e in live_until if e > t0]
        if len(live_until) >= MAXPOS or last_exit.get(j, -10**9) + cooldown > t0:
            continue
        p = prices[t0:t0 + HOLD_MAX + 1, j]
        if len(p) < 3 or not np.isfinite(p[0]) or p[0] <= 0:
            continue
        peak, exit_i = p[0], len(p) - 1
        for i in range(1, len(p)):
            if not np.isfinite(p[i]):
                exit_i = i - 1
                break
            peak = max(peak, p[i])
            if i >= min_hold and p[i] <= peak * (1 - STOP):
                exit_i = i
                break
        path = p[: exit_i + 1]
        path = np.where(np.isfinite(path), path, path[0])
        daily = np.diff(path) / path[0] * w
        pnl[t0 + 1: t0 + 1 + len(daily)] += daily
        s = slip(mc)
        buy_c, sell_c = w * (FEE + s), w * (FEE + s + TAX)
        pnl[t0] -= buy_c
        pnl[min(t0 + exit_i, n_days - 1)] -= sell_c
        cost_total += buy_c + sell_c
        trades += 1
        end = t0 + exit_i
        live_until.append(end + 1)
        last_exit[j] = end
    return pnl, trades, cost_total


def stats(pnl: np.ndarray, idx: pd.DatetimeIndex, a: str, b: str) -> dict:
    m = (idx >= a) & (idx <= b)
    x = pnl[m]
    if len(x) < 20:
        return {"cagr": np.nan, "sharpe": np.nan, "mdd": np.nan}
    eq = 1 + np.cumsum(x)
    yrs = len(x) / 252
    cagr = (eq[-1] ** (1 / yrs) - 1) if eq[-1] > 0 else -1.0
    sd = x.std()
    return {"cagr": cagr * 100, "sharpe": float(x.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan,
            "mdd": float(((eq / np.maximum.accumulate(eq)) - 1).min() * 100)}


def main() -> None:
    px, first, strength, snap = build_signals()
    cands, prices = candidates(px, first, strength, snap)
    print('no PIT market cap within 45d:', int(cands.market_cap_억.isna().sum()), 'of', len(cands))
    n = len(px)
    idx = px.index
    print("candidate entries:", len(cands), "years:", round(n / 252, 1))
    rows = []
    for mh, q, cd, fl in itertools.product((0, 5, 10, 20), (1.0, 0.5, 0.25), (0, 10, 20), (0, 100, 1000)):
        pnl, trades, cost = simulate(cands, prices, n, mh, q, cd, fl)
        r = {"min_hold": mh, "strength_top": q, "cooldown": cd, "mcap_floor": fl, "trades": trades,
             "trades_per_year": round(trades / (n / 252)), "cost_pct_of_equity": round(cost * 100, 1)}
        for name, (a, b) in (("train", TRAIN), ("valid", VALID), ("crash", CRASH)):
            for k, v in stats(pnl, idx, a, b).items():
                r[f"{name}_{k}"] = round(v, 2) if v == v else np.nan
        rows.append(r)
    df = pd.DataFrame(rows)
    base = df[(df.min_hold == 0) & (df.strength_top == 1.0) & (df.cooldown == 0) & (df.mcap_floor == 0)].iloc[0]
    df["accepted"] = ((df.train_cagr > base.train_cagr) & (df.valid_cagr > base.valid_cagr)
                      & (df.train_sharpe >= base.train_sharpe) & (df.valid_sharpe >= base.valid_sharpe)
                      & (df.crash_cagr >= base.crash_cagr))          # 급락 창(2026-07~09)에서 악화하지 않을 것
    df["d_train_cagr"] = (df.train_cagr - base.train_cagr).round(2)
    df["d_valid_cagr"] = (df.valid_cagr - base.valid_cagr).round(2)
    df.to_csv(OUT / "turnover_rules_20260925.csv", index=False)
    acc = df[df.accepted].sort_values("valid_cagr", ascending=False)
    cols = ["min_hold", "strength_top", "cooldown", "mcap_floor", "trades_per_year", "train_cagr", "valid_cagr", "valid_sharpe",
            "valid_mdd", "crash_cagr", "d_train_cagr", "d_valid_cagr"]
    md = ["# R3 회전율 관리 규칙 (모멘텀·돌파 대용 신호, 국면 필터 ON, 운영 비용 모델) — 2026-09-25", "",
          "시스템 검증 결과이며 투자 권유가 아니다. 대용 신호 자체가 손실 신호라(R1·regime_filter 문서) 규칙의 목적은 '비용 잠식 완화'이지 수익 보장이 아니다.", "",
          f"- 후보 진입 {len(cands):,}건(국면 필터 통과), 규칙 격자 {len(df)}개. **채택 = 학습·검증 CAGR 모두 기준선보다 높고 Sharpe가 낮아지지 않으며 급락 창에서 악화하지 않는 규칙.** 시총은 신호일 직전 월간 스냅샷(시점 정합)을 쓴다 — 최신 스냅샷 시총을 쓰면 생존·성장 편향으로 하한 규칙이 과대평가됐다(시행착오 기록).",
          f"- 기준선(규칙 없음): 연 {base.trades_per_year:,.0f}회, 학습 CAGR {base.train_cagr}% / 검증 CAGR {base.valid_cagr}% / 급락창 {base.crash_cagr}%",
          f"- 채택 규칙 수: **{len(acc)}** / {len(df)}", ""]
    if len(acc):
        md += ["## 채택 후보 (검증 CAGR 순 상위 15)", "", acc[cols].head(15).to_markdown(index=False), ""]
    md += ["## 규칙별 단독 효과 (다른 규칙은 끔)", ""]
    solo = df[((df.min_hold > 0) & (df.strength_top == 1) & (df.cooldown == 0) & (df.mcap_floor == 0))
              | ((df.min_hold == 0) & (df.strength_top < 1) & (df.cooldown == 0) & (df.mcap_floor == 0))
              | ((df.min_hold == 0) & (df.strength_top == 1) & (df.cooldown > 0) & (df.mcap_floor == 0))
              | ((df.min_hold == 0) & (df.strength_top == 1) & (df.cooldown == 0) & (df.mcap_floor > 0))]
    md += [solo[cols + ["accepted"]].to_markdown(index=False), ""]
    (OUT / "turnover_rules_20260925.md").write_text("\n".join(md), encoding="utf-8")
    print(base[["trades_per_year", "train_cagr", "valid_cagr", "valid_sharpe", "crash_cagr"]].to_dict())
    print("accepted:", len(acc), "of", len(df))
    print(acc[cols].head(8).to_string(index=False))
    print(solo[cols + ["accepted"]].to_string(index=False))


if __name__ == "__main__":
    main()
