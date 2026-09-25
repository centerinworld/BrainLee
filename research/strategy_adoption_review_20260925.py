#!/usr/bin/env python3
"""HANDOFF §12 R1 (research venv): benchmark-relative adoption review of every strategy, with multiple-testing correction.

Inputs (research/extract_adoption_inputs_20260925.py): strategy_period_equity, adoption_runs, adoption_trades, virtual_trades, benchmark.
A. Backtest strategies (strategy_center run-set, 26 strategies x 6 periods; engine equity is cost-inclusive via backtest_common._tx_cost)
   1 OOS excess: annualised CAGR minus KOSPI CAGR over 2025-01-01.. (cost-net)              > 0
   2 DSR (Bailey & Lopez de Prado 2014) of the full-history daily Sharpe, N trials          > 0.95
   3 PBO (CSCV, S=16, Sharpe metric) of the strategy SET                                     < 0.5   (a property of the selection procedure)
   4 trailing-12m trade expectancy (trades exited in the last 12 months of data)             > 0
B. Virtual (paper) strategies from peak_holding closed trades: profit_pct is GROSS (verified: sell/buy-1 == profit_pct), so the operating
   cost model (fee 0.015%/leg, sell tax 0.18%, market-cap slippage tiers) is deducted; excess vs KOSPI over each holding period.
Outputs: research_outputs/strategy_adoption_review_20260925.{md,csv}
"""
from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
OOS_START = "2025-01-01"
N_TRIALS_STRATEGIES, N_TRIALS_RUNS = 38, 3319          # distinct strategies / total backtest_runs rows explored
FEE, TAX = 0.00015, 0.0018
TIERS = [(10_000, 0.001), (1_000, 0.002), (100, 0.004), (0, 0.008)]


# ---------------------------------------------------------------- statistics
def cagr(r: pd.Series) -> float:
    r = r.dropna()
    return float((1 + r).prod() ** (252 / len(r)) - 1) if len(r) > 20 else np.nan


def mdd(r: pd.Series) -> float:
    eq = (1 + r.fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min())


def sharpe_daily(r: pd.Series) -> float:
    r = r.dropna()
    return float(r.mean() / r.std()) if len(r) > 20 and r.std() > 0 else np.nan


def expected_max_sr(n_trials: int, var_sr: float) -> float:
    """SR0: expected maximum Sharpe among n_trials independent trials with true SR = 0 (Bailey & Lopez de Prado)."""
    g = 0.5772156649
    return math.sqrt(var_sr) * ((1 - g) * stats.norm.ppf(1 - 1 / n_trials) + g * stats.norm.ppf(1 - 1 / (n_trials * math.e)))


def dsr(r: pd.Series, n_trials: int, var_sr: float) -> float:
    r = r.dropna()
    t = len(r)
    if t < 30 or r.std() == 0:
        return np.nan
    sr = r.mean() / r.std()
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    denom = math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr ** 2, 1e-12))
    return float(stats.norm.cdf((sr - expected_max_sr(n_trials, var_sr)) * math.sqrt(t - 1) / denom))


def pbo_cscv(mat: np.ndarray, s: int = 16) -> dict:
    """Probability of Backtest Overfitting via CSCV. mat = T x N daily returns (common dates). Sharpe metric."""
    t, n = mat.shape
    m = t // s
    mat = mat[: m * s].reshape(s, m, n)
    sum_, sq_ = mat.sum(1), (mat ** 2).sum(1)                         # s x n
    logits, best_oos = [], []
    for is_idx in itertools.combinations(range(s), s // 2):
        oos_idx = [i for i in range(s) if i not in is_idx]
        def sharpe(idx):
            cnt = m * len(idx)
            mean = sum_[list(idx)].sum(0) / cnt
            var = sq_[list(idx)].sum(0) / cnt - mean ** 2
            return mean / np.sqrt(np.maximum(var, 1e-18))
        sr_is, sr_oos = sharpe(is_idx), sharpe(oos_idx)
        star = int(np.argmax(sr_is))
        omega = (stats.rankdata(sr_oos)[star]) / (n + 1)
        logits.append(math.log(omega / (1 - omega)))
        best_oos.append(sr_oos[star])
    lam = np.array(logits)
    return {"pbo": float((lam <= 0).mean()), "splits": len(lam), "median_logit": float(np.median(lam)),
            "mean_oos_sr_of_is_best_daily": float(np.mean(best_oos)), "n_strategies": n, "obs": int(m * s)}


def slip(mcap_억: float) -> float:
    return next(rate for thr, rate in TIERS if mcap_억 >= thr)


# ---------------------------------------------------------------- backtest strategies
def stitched_returns(eq: pd.DataFrame) -> pd.DataFrame:
    """daily returns per strategy; where periods overlap, the period with the latest start owns the date."""
    out = {}
    for strat, g in eq.groupby("strategy"):
        parts = []
        for (period, start), h in g.groupby(["period", "start"]):
            h = h.sort_values("date")
            r = h.set_index(pd.to_datetime(h.date)).equity.astype(float).pct_change().iloc[1:]
            parts.append(pd.DataFrame({"r": r, "start": start, "period": period}))
        p = pd.concat(parts).reset_index(names="date").sort_values(["date", "start"]).drop_duplicates("date", keep="last")
        out[strat] = p.set_index("date").r
    return pd.DataFrame(out).sort_index()


def main() -> None:
    eq = pd.read_parquet(IN / "strategy_period_equity.parquet")
    runs = pd.read_parquet(IN / "adoption_runs.parquet")
    trades = pd.read_parquet(IN / "adoption_trades.parquet")
    virt = pd.read_parquet(IN / "virtual_trades.parquet")
    bm_close = pd.read_parquet(IN / "benchmark.parquet").close
    bm_close.index = pd.to_datetime(bm_close.index)
    bm = bm_close.pct_change().dropna()

    R = stitched_returns(eq)
    sr_all = R.apply(sharpe_daily)
    var_sr = float(sr_all.var())
    common = R.drop(columns=["composite"], errors="ignore").dropna(how="any")
    pbo = pbo_cscv(common.to_numpy())
    pbo_full_span = (common.index.min().date(), common.index.max().date())

    src = eq.drop_duplicates(["strategy", "run_id"]).groupby("strategy").source.apply(lambda s: float((s == "engine").mean()))
    yrs = runs.assign(y=lambda d: (pd.to_datetime(d.end) - pd.to_datetime(d.start)).dt.days / 365.25).groupby("strategy").y.sum()
    ntr = runs.groupby("strategy").total_trades.sum()

    tr = trades.dropna(subset=["profit_pct"]).copy()
    tr["exit"] = pd.to_datetime(tr.exit_date, errors="coerce")
    last = R.index.max()
    t12 = tr[tr.exit > last - pd.DateOffset(months=12)].groupby("strategy").profit_pct.agg(["mean", "count"])

    rows = []
    for s in R.columns:
        r = R[s].dropna()
        oos = r[r.index >= OOS_START]
        b_oos = bm.reindex(oos.index).fillna(0)
        b_full = bm.reindex(r.index).fillna(0)
        active = oos - b_oos
        beta = float(np.cov(oos, b_oos)[0, 1] / b_oos.var()) if len(oos) > 20 else np.nan
        rows.append({
            "strategy": s, "days": len(r), "engine_share": round(src.get(s, np.nan), 2),
            "cagr_pct": round(cagr(r) * 100, 1), "mdd_pct": round(mdd(r) * 100, 1), "sharpe_ann": round(sharpe_daily(r) * math.sqrt(252), 2),
            "kospi_cagr_same_span_pct": round(cagr(b_full) * 100, 1),
            "oos_days": len(oos), "oos_cagr_pct": round(cagr(oos) * 100, 1), "oos_kospi_cagr_pct": round(cagr(b_oos) * 100, 1),
            "oos_excess_pct": round((cagr(oos) - cagr(b_oos)) * 100, 1),
            "oos_info_ratio": round(float(active.mean() / active.std() * math.sqrt(252)), 2) if active.std() > 0 else np.nan,
            "oos_beta": round(beta, 2),
            "dsr_n38": round(dsr(r, N_TRIALS_STRATEGIES, var_sr), 3), "dsr_n3319": round(dsr(r, N_TRIALS_RUNS, var_sr), 3),
            "trades_per_year": round(ntr.get(s, 0) / yrs.get(s, np.nan), 0),
            "t12m_trades": int(t12["count"].get(s, 0)), "t12m_expectancy_pct": round(float(t12["mean"].get(s, np.nan)), 2),
        })
    df = pd.DataFrame(rows).sort_values("oos_excess_pct", ascending=False)
    df["c1_oos_excess"] = df.oos_excess_pct > 0
    df["c2_dsr"] = df.dsr_n38 > 0.95
    df["c3_pbo"] = pbo["pbo"] < 0.5
    df["c4_t12m_ev"] = df.t12m_expectancy_pct > 0
    # 엔진 산출 곡선은 신뢰. 거래로그 재구성(mtm_reconstructed) 곡선은 엔진이 보고한 MDD와 대조해 검증된 경우에만 신뢰한다:
    # golden_cross·contract_momentum은 6개 구간 MDD 평균 절대 차이 0.8%p(최대 3.7%p)로 검증됨(2026-09-25). earnings_conviction·se_momentum은 엔진 MDD가 없어 미검증,
    # turnaround는 실현손익 계단 곡선이라 미실현 낙폭을 반영하지 못함.
    FIDELITY_VERIFIED = {"golden_cross", "contract_momentum"}
    df["curve_ok"] = (df.engine_share >= 0.99) | df.strategy.isin(FIDELITY_VERIFIED)
    df["adopt"] = df[["c1_oos_excess", "c2_dsr", "c3_pbo", "c4_t12m_ev", "curve_ok"]].all(axis=1)
    df.to_csv(OUT / "strategy_adoption_review_20260925.csv", index=False)

    # ---------------------------------------------------------------- virtual strategies
    v = virt.dropna(subset=["profit_pct", "buy_price"]).copy()
    v["entry"] = pd.to_datetime(v.entry_date, errors="coerce")
    v["exit"] = pd.to_datetime(v.sold_at.str[:10], errors="coerce")
    v["cost_pct"] = v.mcap_억.apply(lambda m: (2 * (FEE + slip(float(m))) + TAX) * 100)
    v["net_pct"] = v.profit_pct - v.cost_pct

    def bm_ret(a, b):
        ia, ib = bm_close.index.searchsorted(a, side="right") - 1, bm_close.index.searchsorted(b, side="right") - 1
        return (bm_close.iloc[ib] / bm_close.iloc[ia] - 1) * 100 if ia >= 0 and ib >= 0 and not (pd.isna(a) or pd.isna(b)) else np.nan
    v["kospi_pct"] = [bm_ret(a, b) for a, b in zip(v.entry, v.exit)]
    v["excess_pct"] = v.net_pct - v.kospi_pct
    crash = (v.exit >= "2026-07-01") & (v.exit <= "2026-09-23")
    vr = []
    for s, g in v.groupby("strategy"):
        n = len(g)
        ex = g.excess_pct.dropna()
        tstat = float(ex.mean() / (ex.std() / math.sqrt(len(ex)))) if len(ex) > 2 and ex.std() > 0 else np.nan
        vr.append({"strategy": s, "n": n, "win_pct": round((g.net_pct > 0).mean() * 100, 1), "gross_ev_pct": round(g.profit_pct.mean(), 2),
                   "net_ev_pct": round(g.net_pct.mean(), 2), "kospi_same_period_pct": round(g.kospi_pct.mean(), 2),
                   "net_excess_ev_pct": round(g.excess_pct.mean(), 2), "excess_t": round(tstat, 2) if not np.isnan(tstat) else np.nan,
                   "net_ev_crash_0701_0923": round(g[crash.reindex(g.index)].net_pct.mean(), 2) if crash.reindex(g.index).any() else np.nan,
                   "n_crash": int(crash.reindex(g.index).sum())})
    vdf = pd.DataFrame(vr).sort_values("n", ascending=False)
    vdf["verdict"] = np.where(vdf.n < 30, "판정 불가(표본<30)",
                              np.where((vdf.net_ev_pct > 0) & (vdf.net_excess_ev_pct > 0), "조건부 유지", "불합격"))
    vdf.to_csv(OUT / "strategy_adoption_review_virtual_20260925.csv", index=False)

    # ---------------------------------------------------------------- report
    pd.set_option("display.width", 250)
    passed = df[df.adopt].strategy.tolist()
    md = ["# 전략 채택 기준 평가 (R1) — 2026-09-25", "",
          "본 문서는 시스템 검증 결과이며 투자 권유가 아니다. 기준(모두 충족): ① 표본 외(2025-01~) 비용 차감 초과수익 > 0 ② DSR > 0.95 ③ PBO < 0.5 ④ 최근 12개월 거래 기대값 > 0.", "",
          "## 요약",
          f"- 백테스트 전략 {len(df)}개 중 **4개 기준을 모두 통과한 전략: {len(passed)}개** {passed if passed else ''}",
          f"- ① 통과 {int(df.c1_oos_excess.sum())}개 / ② 통과 {int(df.c2_dsr.sum())}개 / ④ 통과 {int(df.c4_t12m_ev.sum())}개. **①을 통과한 전략 {df[df.c1_oos_excess].strategy.tolist()} 중 곡선을 신뢰할 수 있는(엔진 곡선 또는 엔진 MDD로 검증된 재구성 곡선) 것은 {int((df.c1_oos_excess & df.curve_ok).sum())}개({df[df.c1_oos_excess & df.curve_ok].strategy.tolist()})** — 그 전략들도 DSR이 0.95에 크게 못 미쳐 채택되지 않는다. 나머지(미검증 재구성·계단 곡선)는 판정을 유보한다. **③ PBO = {pbo['pbo']:.2f}** (전략 {pbo['n_strategies']}개, 공통 구간 {pbo_full_span[0]}~{pbo_full_span[1]} {pbo['obs']}거래일, CSCV {pbo['splits']:,}분할) → {'기준(<0.5) 충족' if pbo['pbo'] < 0.5 else '기준 미충족: 전략 집합에서 IS 최고 전략이 OOS에서 중앙값 이하가 되는 비율이 절반 이상 — 선택 절차 자체가 과최적화 위험'}.",
          f"- DSR은 탐색 규모에 민감하다: N=38(전략 수)과 N=3,319(실행 수)를 모두 표기(N={N_TRIALS_STRATEGIES}를 판정에 사용). 전략 간 일별 Sharpe 분산 V={var_sr:.2e}.",
          f"- KOSPI 보유(같은 표본 외 구간) CAGR: {df.oos_kospi_cagr_pct.iloc[0]}% — 표본 외 구간은 {int(df.oos_days.max())}거래일(약 {df.oos_days.max()/252:.1f}년)로 짧아 ①의 통계적 힘이 약하다.", "",
          "## A. 백테스트 전략 (strategy_center 선정 실행 세트)", "",
          df[["strategy", "cagr_pct", "mdd_pct", "sharpe_ann", "oos_cagr_pct", "oos_kospi_cagr_pct", "oos_excess_pct", "oos_info_ratio", "oos_beta",
              "dsr_n38", "dsr_n3319", "t12m_expectancy_pct", "t12m_trades", "trades_per_year", "engine_share", "curve_ok", "adopt"]].to_markdown(index=False), "",
          "engine_share = 자본곡선이 엔진 산출인 실행 비율(나머지는 거래로그+가격 MTM 근사). t12m = 자료 마지막 12개월(2025-04~2026-03)에 청산된 거래의 평균 손익%(엔진 비용 포함).", "",
          "## B. 가상매매(paper) 전략 — 실제 진입 기록 (peak_holding 청산 309건, 2026-05-04~09-25)", "",
          "`profit_pct`는 비용 미차감 값(매도/매수-1과 일치)이라 운영 비용 모델(수수료 0.015%/편도·매도세 0.18%·시총 구간 슬리피지)을 차감했다. 비교 기준은 같은 보유 기간의 KOSPI.", "",
          vdf.to_markdown(index=False), "",
          "표본이 2026-05 이후 약 5개월(급락 창 07~09 포함)이라 DSR·12개월 롤링 판정은 불가하고, n<30 전략은 판정하지 않는다. 판정은 순기대값>0 & KOSPI 대비 초과>0이며 '조건부 유지'도 표본 부족으로 채택이 아니다.", "",
          "## 해석과 한계", "",
          "- 엔진 곡선은 비용 포함(`backtest_common._tx_cost`)이지만 `mtm_reconstructed` 곡선은 고정 크기 근사이므로 engine_share가 낮은 전략의 위험지표는 신뢰도가 낮다.",
          "- 2025-01~2026-03은 KOSPI가 급등한 구간이라 베타가 낮은 전략은 구조적으로 초과수익이 음수가 되기 쉽다. 위험 조정 초과(정보비율)와 함께 본다.",
          "- 신호가 없거나 거래 수가 적은 전략(t12m_trades 작음)의 기대값은 노이즈가 크다.",
          "- 불합격 전략의 신규 진입 shadow 전환은 **제안이며 사용자 승인 전에는 실행하지 않는다.** peak_holding 보유분은 청산하지 않는다."]
    (OUT / "strategy_adoption_review_20260925.md").write_text("\n".join(md), encoding="utf-8")
    print(df[["strategy", "oos_excess_pct", "dsr_n38", "t12m_expectancy_pct", "adopt"]].to_string(index=False))
    print("PBO", pbo); print(vdf.to_string(index=False))


if __name__ == "__main__":
    main()
