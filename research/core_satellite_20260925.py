#!/usr/bin/env python3
"""HANDOFF §12 R2 (research venv): core-satellite simulation on the virtual ledger only (no live_orders / KIS path).

Core = KOSPI index (proxy for a KOSPI200 tracker; no inverse/leveraged). Satellite candidates = the best AVAILABLE sleeves, none of which passed the R1
adoption criteria: engine-curve strategies (v11, v2, v_trend) over 2020-12~2026-03 and the R4/R5 3-factor composite (low_vol + earn_yield + sue_op,
top quintile equal weight, monthly, net of 1.0% x turnover) over 2023-01~2026-08. Satellite weight 0/20/30/50%, monthly rebalance.
Output research_outputs/core_satellite_20260925.{csv,md}. Design only - adoption is the owner's decision.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
WEIGHTS = (0.0, 0.2, 0.3, 0.5, 1.0)
H = 20


def monthly_from_daily(r: pd.Series) -> pd.Series:
    r = r.dropna()
    return (1 + r).groupby(r.index.to_period("M")).prod() - 1


def metrics(m: pd.Series, bench: pd.Series) -> dict:
    m, b = m.dropna(), bench.reindex(m.index)
    eq = (1 + m).cumprod()
    yrs = len(m) / 12
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    bcagr = (1 + b).prod() ** (1 / yrs) - 1
    act = m - b
    return {"cagr_pct": round(cagr * 100, 1), "kospi_cagr_pct": round(bcagr * 100, 1), "excess_cagr_pct": round((cagr - bcagr) * 100, 1),
            "sharpe": round(m.mean() / m.std() * np.sqrt(12), 2), "mdd_pct": round(float((eq / eq.cummax() - 1).min() * 100), 1),
            "worst_month_pct": round(m.min() * 100, 1), "info_ratio": round(float(act.mean() / act.std() * np.sqrt(12)), 2) if act.std() > 0 else np.nan,
            "months": len(m)}


def composite_sleeve() -> pd.Series:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    src = pd.read_parquet(IN / "alpha_sources.parquet")
    for d in (snap, src):
        d["snapshot_date"] = pd.to_datetime(d.snapshot_date)
    last = adj.apply(lambda s: s.last_valid_index())
    delisted = set(last[last < adj.index.max() - pd.Timedelta(days=20)].index)
    p = adj.reindex(pd.bdate_range(adj.index.min(), adj.index.max())).ffill(limit=5)
    for c in delisted:
        p[c] = adj[c].reindex(p.index).ffill()
    rv = np.log(p).diff().rolling(60, min_periods=40).std()
    fwd = p.shift(-H) / p - 1
    df = snap[["snapshot_date", "stock_code", "per", "avg_turnover_20d_억"]].merge(src[["snapshot_date", "stock_code", "sue_op"]],
                                                                             on=["snapshot_date", "stock_code"], how="left")
    dates = pd.DatetimeIndex(sorted(df.snapshot_date.unique()))
    gi = dict(zip(dates, p.index[p.index.searchsorted(dates, side="right") - 1]))
    df["low_vol"] = [-rv.at[gi[d], c] if c in rv.columns else np.nan for d, c in zip(df.snapshot_date, df.stock_code)]
    df["fwd"] = [fwd.at[gi[d], c] if c in fwd.columns else np.nan for d, c in zip(df.snapshot_date, df.stock_code)]
    df["earn_yield"] = np.where(df.per > 0, 1 / df.per, np.nan)
    df = df[df["avg_turnover_20d_억"] > 0.5]
    for c in ("low_vol", "earn_yield", "sue_op"):
        df[c + "_r"] = df.groupby("snapshot_date")[c].rank(pct=True)
    df["score"] = df[["low_vol_r", "earn_yield_r", "sue_op_r"]].mean(axis=1, skipna=True)
    out, prev = {}, None
    for d, g in df.dropna(subset=["score", "fwd"]).groupby("snapshot_date"):
        if len(g) < 300 or d < pd.Timestamp("2023-01-01"):
            continue
        top = g[g.score.rank(pct=True) > 0.8]
        names = set(top.stock_code)
        turn = 1.0 if prev is None else 1 - len(names & prev) / len(names)
        out[d] = top.fwd.mean() - turn * 0.010
        prev = names
    s = pd.Series(out)
    s.index = s.index.to_period("M")
    return s


def main() -> None:
    bm = pd.read_parquet(IN / "benchmark.parquet").close
    bm.index = pd.to_datetime(bm.index)
    core_m = monthly_from_daily(bm.pct_change())
    eq = pd.read_parquet(IN / "strategy_period_equity.parquet")
    sleeves = {}
    for strat in ("v11", "v2", "v_trend"):
        g = eq[eq.strategy == strat]
        parts = []
        for (period, start), h in g.groupby(["period", "start"]):
            h = h.sort_values("date")
            parts.append(pd.DataFrame({"r": h.set_index(pd.to_datetime(h.date)).equity.astype(float).pct_change().iloc[1:], "start": start}))
        r = pd.concat(parts).reset_index(names="date").sort_values(["date", "start"]).drop_duplicates("date", keep="last").set_index("date").r
        sleeves[f"{strat} (엔진 곡선)"] = monthly_from_daily(r)
    sleeves["3팩터 합성(월간, 비용 차감)"] = composite_sleeve()
    rows = []
    for name, sat in sleeves.items():
        idx = sat.dropna().index.intersection(core_m.index)
        sat, core = sat.reindex(idx), core_m.reindex(idx)
        for w in WEIGHTS:
            m = w * sat + (1 - w) * core
            rows.append({"sleeve": name, "sat_weight": w, **metrics(m, core)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "core_satellite_20260925.csv", index=False)
    pd.set_option("display.width", 220)
    print(df.to_string(index=False))
    md = ["# R2 코어-위성 시뮬레이션 (가상 시뮬레이션만) — 2026-09-25", "",
          "시스템 설계 검토이며 투자 권유가 아니다. 코어=KOSPI 지수(KOSPI200 추종 ETF의 대용, 인버스·레버리지 제외), 위성=R1 기준을 통과한 전략이 없어 **가용한 최선의 슬리브**로 대체했다. "
          "월간 리밸런싱, 위성 비중 0/20/30/50/100%. `live_orders`·KIS 페이퍼 경로는 사용하지 않았다.", ""]
    for name, g in df.groupby("sleeve", sort=False):
        md += [f"## {name}", "", g.drop(columns="sleeve").to_markdown(index=False), ""]
    md += ["## 해석", "",
           "- **일관된 효과는 하나뿐이다: 위성 비중이 늘수록 변동성·MDD·최악의 달이 줄어든다**(v11 MDD -34.6%→-23.5%@50%, 합성 -22.2%→-9.0%@50%). 코어 KOSPI 대비 낙폭 방어 수단으로는 유효하다.",
           "- 초과수익은 슬리브에 따라 부호가 갈린다. 엔진 곡선 전략(2020-12~2026-03, 64개월)은 100% 기준 +2.3~5.6%p이나 **정보비율 0.02~0.14로 통계적으로 무의미**하고, 이 전략들은 26개 중 사후에 고른 것이라 선택 편향이 있다(R1: 4개 기준 통과 0개, PBO 0.58, 표본 외 2025+에서는 KOSPI 대비 -22~-57%p).",
           "- 3팩터 합성(2023-01~2026-08, KOSPI가 대형주 주도로 급등한 구간)은 비중을 늘릴수록 초과수익이 음(-3.8%p@20% … -26.6%@100%)이다 — 롱온리 동일가중 위성은 시총가중 지수 랠리를 따라가지 못한다.",
           "- 결론: 위성은 '수익 개선'이 아니라 **낙폭 완화 목적**으로만 정당화된다. R1 기준을 통과한 전략이 아직 없으므로 위성 비중은 0%(또는 실험용 소액 가상)를 유지하고, 늘리는 결정은 통과 전략이 생긴 뒤 표본 외 검증 결과로 한다(운영 원칙 1). 이 문서는 설계안이며 운영 반영은 사용자 결정이다."]
    (OUT / "core_satellite_20260925.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
