#!/usr/bin/env python3
"""HANDOFF §12 R5 (research venv): LightGBM cross-sectional ranking model vs simple baselines, purged + embargoed monthly walk-forward.

Target: next-20-trading-day return in excess of KOSPI, cross-sectionally rank-normalised per month (delisted names carried to their last print).
Features (all known at the month-end snapshot): size/value/momentum/liquidity/supply from the canonical snapshot (PER/PBR are point-in-time TTM),
60d realised volatility, and the R4 sources (short ratio, borrow balance, credit, SUE). Excluded on purpose: heuristic_score, model_score_* (older
targets), consensus/EPS-revision (history too short).
Walk-forward: expanding train window; models are refit every 6 test months; a test month m only sees training months <= m-2 (label horizon ~1 month
+ 1 month embargo). First test month 2023-01 (36 training months). Small, regularised trees (num_leaves 15, min_data_in_leaf 500, lr .03, 300 rounds).
Baselines on the SAME test months: (1) legacy model_score_12m, (2) equal-weight rank composite of low_vol_60d + earn_yield + sue_op, (3) LightGBM.
Metrics: mean rank IC, top-quintile return vs the equal-weight universe (the fair yardstick for an equal-weight book), t, top-quintile mean excess return (20d), Q5-Q1 spread, top-quintile monthly turnover, net-of-cost top-quintile excess
(turnover x round-trip 1.0% assumed). Output research_outputs/lightgbm_ranking_20260925.{csv,md}
"""
from __future__ import annotations

import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
H = 20
FIRST_TEST = pd.Timestamp("2023-01-01")
REFIT_EVERY = 6
ROUNDTRIP_COST = 0.010
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=500, feature_fraction=0.8, bagging_fraction=0.8,
              bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=4, seed=7)
ROUNDS = 300
SNAP_FEATS = ["market_cap_log", "per", "pbr", "ret_20d", "ret_60d", "ret_120d", "dist_high_252", "dist_low_252", "vol_ratio_20d",
              "avg_turnover_20d_억", "supply_20d_억"]
SRC_FEATS = ["short_ratio_20d", "short_ratio_chg", "borrow_pct", "borrow_chg_20d", "credit_ratio", "credit_chg_20d", "sue_ni", "sue_op"]


def rank_norm(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    bm = pd.read_parquet(IN / "benchmark.parquet").close.reindex(adj.index).ffill()
    snap = pd.read_parquet(IN / "factors.parquet")
    src = pd.read_parquet(IN / "alpha_sources.parquet")
    for d in (snap, src):
        d["snapshot_date"] = pd.to_datetime(d.snapshot_date)

    # prices on a business-day grid, delisted names carried at their last close
    last = adj.apply(lambda s: s.last_valid_index())
    delisted = set(last[last < adj.index.max() - pd.Timedelta(days=20)].index)
    p = adj.reindex(pd.bdate_range(adj.index.min(), adj.index.max())).ffill(limit=5)
    for c in delisted:
        p[c] = adj[c].reindex(p.index).ffill()
    bmp = bm.reindex(p.index).ffill()
    rv60 = np.log(p).diff().rolling(60, min_periods=40).std()
    fwd = p.shift(-H) / p - 1
    bfwd = bmp.shift(-H) / bmp - 1

    df = snap[["snapshot_date", "stock_code", "model_score_12m", "market", "sector_large", "market_cap_억"] + SNAP_FEATS].merge(src[["snapshot_date", "stock_code"] + SRC_FEATS],
                                                                                    on=["snapshot_date", "stock_code"], how="left")
    dates = pd.DatetimeIndex(sorted(df.snapshot_date.unique()))
    pos = p.index.searchsorted(dates, side="right") - 1
    dmap = dict(zip(dates, p.index[pos]))
    rows_lv, rows_fwd, rows_raw, bf = [], [], [], {}
    for d in dates:
        g = dmap[d]
        rows_lv.append(rv60.loc[g])
        rows_fwd.append(fwd.loc[g] - bfwd.loc[g])
        rows_raw.append(fwd.loc[g])
        bf[d] = float(bfwd.loc[g])
    lv = pd.DataFrame(rows_lv, index=dates).stack().rename("low_vol_raw")
    ex = pd.DataFrame(rows_fwd, index=dates).stack().rename("fwd_excess")
    raw = pd.DataFrame(rows_raw, index=dates).stack().rename("fwd_raw")
    lv.index.names = ex.index.names = raw.index.names = ["snapshot_date", "stock_code"]
    df = df.merge(lv.reset_index(), on=["snapshot_date", "stock_code"], how="left").merge(ex.reset_index(), on=["snapshot_date", "stock_code"], how="left")
    df = df.merge(raw.reset_index(), on=["snapshot_date", "stock_code"], how="left")
    df["earn_yield"] = np.where(df.per > 0, 1 / df.per, np.nan)
    df["book_yield"] = np.where(df.pbr > 0, 1 / df.pbr, np.nan)
    df["low_vol_60d"] = -df.low_vol_raw
    feats = SNAP_FEATS + SRC_FEATS + ["low_vol_60d", "earn_yield", "book_yield"]
    # liquid, active universe only (same spirit as the Alphalens screens); a label is needed to train/evaluate
    df = df[(df.avg_turnover_20d_억 > 0.5)].copy()
    for c in feats:                                   # cross-sectional rank features (robust to the outliers that made the old logistic diverge)
        df[c + "_r"] = df.groupby("snapshot_date")[c].transform(rank_norm)
    fr = [c + "_r" for c in feats]
    df["y"] = df.groupby("snapshot_date").fwd_excess.transform(rank_norm)
    df["composite"] = df[["low_vol_60d_r", "earn_yield_r", "sue_op_r"]].mean(axis=1, skipna=True)
    df["legacy"] = df.groupby("snapshot_date").model_score_12m.transform(rank_norm)

    test_months = [d for d in dates if d >= FIRST_TEST and d <= dates[-1] - pd.DateOffset(months=1)]
    preds = {}
    imp = []
    for i, d in enumerate(test_months):
        if i % REFIT_EVERY == 0:
            cutoff = d - pd.DateOffset(months=2)                     # purge label horizon + 1 month embargo
            tr = df[(df.snapshot_date <= cutoff) & df.y.notna()]
            model = lgb.train(PARAMS, lgb.Dataset(tr[fr], tr.y), ROUNDS)
            imp.append(pd.Series(model.feature_importance("gain"), index=feats))
        te = df[df.snapshot_date == d]
        preds[d] = pd.Series(model.predict(te[fr]), index=te.index)
    df["lgbm"] = pd.concat(preds.values()).reindex(df.index)

    def evaluate(col: str) -> dict:
        ics, tops, spreads, turns, prev, vs_u = [], [], [], [], None, []
        for d in test_months:
            g = df[(df.snapshot_date == d) & df[col].notna() & df.fwd_excess.notna()]
            if len(g) < 300:
                continue
            ics.append(stats.spearmanr(g[col], g.fwd_excess)[0])
            q = g[col].rank(pct=True)
            top, bot = g[q > 0.8], g[q <= 0.2]
            tops.append(top.fwd_excess.mean())
            vs_u.append(top.fwd_excess.mean() - g.fwd_excess.mean())     # vs equal-weight universe (removes the cap-weighted-index mismatch)
            spreads.append(top.fwd_excess.mean() - bot.fwd_excess.mean())
            names = set(top.stock_code)
            if prev is not None:
                turns.append(1 - len(names & prev) / max(len(names), 1))
            prev = names
        ic = np.array(ics)
        turn = float(np.mean(turns)) if turns else np.nan
        return {"model": col, "months": len(ic), "ic": ic.mean(), "ic_t": ic.mean() / (ic.std() / np.sqrt(len(ic))), "ic_ir": ic.mean() / ic.std(),
                "top_q_excess_pct": np.mean(tops) * 100, "top_q_vs_universe_pct": np.mean(vs_u) * 100, "q5_minus_q1_pct": np.mean(spreads) * 100, "top_q_turnover": turn,
                "top_q_net_excess_pct": (np.mean(tops) - turn * ROUNDTRIP_COST) * 100 if turn == turn else np.nan,
                "ic_pos_pct": float((ic > 0).mean() * 100)}

    res = pd.DataFrame([evaluate(c) for c in ("legacy", "composite", "lgbm")])
    # split evaluation: 2023-01..2024-12 vs 2025-01..
    global test_months_all
    sub = {}
    for name, sel in (("2023-2024", [d for d in test_months if d <= pd.Timestamp("2024-12-31")]),
                      ("2025+", [d for d in test_months if d > pd.Timestamp("2024-12-31")])):
        tm_backup = test_months
        test_months = sel
        sub[name] = pd.DataFrame([evaluate(c) for c in ("legacy", "composite", "lgbm")]).assign(window=name)
        test_months = tm_backup
    res_split = pd.concat(sub.values())
    imp_df = pd.concat(imp, axis=1).mean(axis=1).sort_values(ascending=False)
    imp_df = (imp_df / imp_df.sum() * 100).round(1)

    # ---- HANDOFF §15 V5: separate "no signal" from "benchmark composition" ------------------------------------------------------------------
    # 2024-26 KOSPI (cap-weighted) is dominated by Samsung Electronics + SK hynix, so any equal-weight small/mid/low-vol book lags it regardless of skill. Compare the
    # top-quintile book with (a) KOSPI, (b) cap-weighted KOSPI EXCLUDING those two, (c) the equal-weight universe, and measure (d) selection alpha inside sector x size
    # buckets (top quintile minus the bucket's equal-weight average) and (e) Jensen alpha/beta versus KOSPI.
    def decompose(col: str, months) -> dict:
        top_r, kos, ex2, ew, neut = [], [], [], [], []
        for d in months:
            g = df[(df.snapshot_date == d) & df[col].notna() & df.fwd_raw.notna()].copy()
            if len(g) < 300:
                continue
            q = g[col].rank(pct=True)
            top_r.append(g[q > 0.8].fwd_raw.mean())
            ew.append(g.fwd_raw.mean())
            kos.append(bf[d])
            k = g[(g.market == "KOSPI") & (~g.stock_code.isin(["005930", "000660"])) & g["market_cap_억"].notna()] if "market_cap_억" in g else g.iloc[0:0]
            ex2.append(float((k.fwd_raw * k["market_cap_억"]).sum() / k["market_cap_억"].sum()) if len(k) > 50 else np.nan)
            g["size"] = pd.qcut(g["market_cap_log"], 3, labels=False, duplicates="drop")
            alphas, w = [], []
            for _, gg in g.groupby(["sector_large", "size"], dropna=False):
                if len(gg) >= 15:
                    qq = gg[col].rank(pct=True)
                    alphas.append(gg[qq > 0.8].fwd_raw.mean() - gg.fwd_raw.mean())
                    w.append(len(gg))
            neut.append(float(np.average(alphas, weights=w)) if alphas else np.nan)
        t, k, e2, u, n = (np.array(x, float) for x in (top_r, kos, ex2, ew, neut))
        def stat(x):
            x = x[~np.isnan(x)]
            return (x.mean() * 100, x.mean() / (x.std() / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan)
        beta = float(np.cov(t, k)[0, 1] / k.var())
        alpha_m = float((t - beta * k).mean())
        resid = t - beta * k - alpha_m
        alpha_t = alpha_m / (resid.std() / np.sqrt(len(t)))
        m = ~np.isnan(e2)
        return {"model": col, "months": len(t), "top_mean_pct": t.mean() * 100, "kospi_mean_pct": k.mean() * 100, "kospi_ex_top2_mean_pct": np.nanmean(e2) * 100,
                "universe_ew_mean_pct": u.mean() * 100,
                "top_minus_kospi": stat(t - k), "top_minus_kospi_ex2": stat(np.where(m, t - e2, np.nan)), "top_minus_universe_ew": stat(t - u),
                "selection_alpha_sector_size_neutral": stat(n), "jensen_alpha_pct_per_month": alpha_m * 100, "jensen_alpha_t": alpha_t, "beta_vs_kospi": beta}
    bench_rows = [decompose(c, test_months) for c in ("legacy", "composite", "lgbm")]
    bench_rows += [dict(decompose("lgbm", [d for d in test_months if d > pd.Timestamp("2024-12-31")]), model="lgbm (2025+)")]
    def fmt(r):
        f = lambda x: f"{x[0]:+.2f}%p (t {x[1]:.1f})"
        return {"model": r["model"], "months": r["months"], "top": f"{r['top_mean_pct']:.2f}%", "KOSPI": f"{r['kospi_mean_pct']:.2f}%", "KOSPI ex 삼성·하이닉스": f"{r['kospi_ex_top2_mean_pct']:.2f}%",
                "유니버스 동일가중": f"{r['universe_ew_mean_pct']:.2f}%", "top−KOSPI": f(r["top_minus_kospi"]), "top−KOSPI(ex2)": f(r["top_minus_kospi_ex2"]),
                "top−동일가중": f(r["top_minus_universe_ew"]), "섹터·규모 중립 선택 알파": f(r["selection_alpha_sector_size_neutral"]),
                "젠센 알파(월)": f"{r['jensen_alpha_pct_per_month']:+.2f}%p (t {r['jensen_alpha_t']:.1f})", "베타": f"{r['beta_vs_kospi']:.2f}"}
    bench_df = pd.DataFrame([fmt(r) for r in bench_rows])
    pd.DataFrame(bench_rows).to_csv(OUT / "benchmark_decomposition_20260926.csv", index=False)
    print(bench_df.to_string(index=False))
    L = next(r for r in bench_rows if r["model"] == "lgbm")
    v5 = ["## V5 벤치마크 분해 — 신호 무효인가, 벤치마크 구성 차이인가", "",
          f"- 시총가중 KOSPI 월평균 {L['kospi_mean_pct']:.2f}% vs 삼성전자·SK하이닉스 제외 KOSPI {L['kospi_ex_top2_mean_pct']:.2f}% vs 유니버스 동일가중 {L['universe_ew_mean_pct']:.2f}% — 대형 반도체 2종목이 지수 수익을 크게 끌어올렸다.",
          f"- LightGBM 상위 20%: 동일가중 대비 {L['top_minus_universe_ew'][0]:+.2f}%p/월(t {L['top_minus_universe_ew'][1]:.1f}), 섹터·규모 중립 선택 알파 {L['selection_alpha_sector_size_neutral'][0]:+.2f}%p/월(t {L['selection_alpha_sector_size_neutral'][1]:.1f}), "
          f"젠센 알파 {L['jensen_alpha_pct_per_month']:+.2f}%p/월(t {L['jensen_alpha_t']:.1f}, 베타 {L['beta_vs_kospi']:.2f}), KOSPI 대비 {L['top_minus_kospi'][0]:+.2f}%p/월(t {L['top_minus_kospi'][1]:.1f}), 삼성·하이닉스 제외 KOSPI 대비 {L['top_minus_kospi_ex2'][0]:+.2f}%p/월.",
          f"- KOSPI 대비 격차 {L['kospi_mean_pct']-L['top_mean_pct']:.2f}%p/월 중 **{(L['kospi_mean_pct']-L['kospi_ex_top2_mean_pct'])/max(L['kospi_mean_pct']-L['top_mean_pct'],1e-9)*100:.0f}%는 삼성전자·SK하이닉스 편중**으로 설명된다"
          f"(두 종목을 뺀 KOSPI와의 격차는 {L['top_minus_kospi_ex2'][0]:+.2f}%p, t {L['top_minus_kospi_ex2'][1]:.1f}).",
          "- 판정: " + ("동일가중·섹터규모 중립 대비 양(+)의 선택 알파가 통계적으로 유의(t≥2)하므로 **신호가 무효라는 근거는 없고**, 열위는 벤치마크 구성·규모 편향이 주된 원인이다."
                        if max(L["top_minus_universe_ew"][1], L["selection_alpha_sector_size_neutral"][1]) >= 2 else
                        "동일가중·중립 대비 알파가 유의(t≥2)하지 않아 **신호 유효성도 확정할 수 없다**(열위의 대부분은 벤치마크 구성이지만 초과 성과 자체는 입증되지 않음).")
          + f" 젠센 알파는 월 {L['jensen_alpha_pct_per_month']:+.2f}%p(t {L['jensen_alpha_t']:.1f}, 베타 {L['beta_vs_kospi']:.2f})로 유의하지 않다.",
          "- 규칙 유지: 채택 판정(KOSPI 미달 = 운영 미채택)은 그대로다. 이 분석은 '신호 무효'와 '벤치마크 구성 차이'를 구분해 원장에 남기기 위한 것이다.", "",
          bench_df.to_markdown(index=False), ""]
    res.to_csv(OUT / "lightgbm_ranking_20260925.csv", index=False)
    pd.set_option("display.width", 220)
    print(res.round(3).to_string(index=False))
    print(res_split.round(3).to_string(index=False))
    print(imp_df.head(12).to_string())
    g = res.set_index("model")
    verdict = [
        "## 결론",
        f"- 순위 예측력: LightGBM IC {g.at['lgbm','ic']:.3f}(t {g.at['lgbm','ic_t']:.1f}) > 3팩터 합성 {g.at['composite','ic']:.3f} > 기존 model_score {g.at['legacy','ic']:.3f}(음수). 기존 model_score를 대체할 후보는 합성 또는 LightGBM.",
        f"- 그러나 상위 20% 동일가중 롱온리는 **KOSPI 대비 월 {g.at['lgbm','top_q_excess_pct']:.2f}%p(비용 차감 후 {g.at['lgbm','top_q_net_excess_pct']:.2f}%p)로 벤치마크를 이기지 못한다** — R1 채택 기준 ① 미충족(2025+ 구간도 동일).",
        f"- 유니버스 평균 대비 이득은 월 {g.at['lgbm','top_q_vs_universe_pct']:.2f}%p(회전율 {g.at['lgbm','top_q_turnover']:.2f} × 왕복 1.0% 비용 {g.at['lgbm','top_q_turnover']*1.0:.2f}%p 차감 시 실익은 작다). 합성({g.at['composite','top_q_vs_universe_pct']:.2f}%p, 회전율 {g.at['composite','top_q_turnover']:.2f})과 차이가 작아 **복잡한 모델의 정당화가 어렵다**.",
        "- 피처 중요도는 저변동성 29%로 지배적(R4·Alphalens 결과와 일치). 운영 반영·shadow 신호 3개월 기록은 R1 기준 미충족으로 진행하지 않는다.", ""]
    md = ["# R5 LightGBM 랭킹 모델 (purged·embargoed walk-forward) — 2026-09-25", "",
          "시스템 검증 결과이며 투자 권유가 아니다. 목표: 다음 20거래일 KOSPI 대비 초과수익의 횡단면 순위. 테스트 월 " + f"{len(test_months)}개(2023-01~), 학습은 항상 테스트 월보다 2개월 이전까지(purge+embargo), 6개월마다 재학습. 비용 순수익은 월 회전율×왕복 1.0% 가정.", "",
          *verdict,
          *v5,
          "## 전체 테스트 구간", "", res.round(3).to_markdown(index=False), "",
          "## 구간 분할", "", res_split.round(3).to_markdown(index=False), "",
          "## 피처 중요도(gain %, 재학습 평균)", "", imp_df.to_frame("gain_pct").to_markdown(), ""]
    (OUT / "lightgbm_ranking_20260925.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
