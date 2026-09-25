#!/usr/bin/env python3
"""HANDOFF §12 R4 (research venv): Alphalens screening of candidate alpha sources before any feature adoption.

Same pipeline as alphalens_split_events_20260925.py (weekday price grid with delisted names carried, sector-neutral variant, train <= 2024-12 /
valid 2025-01~). Extra: short-selling factors are also split by the short-selling BAN (2023-11-06 .. 2025-03-30) because the ban changes what
the short ratio means. Verdict per factor (candidate needs ALL): sign kept between train and valid, |full-sample non-overlapping t| >= 2 on 20D
or 60D, and valid |IC| >= 0.02. Factors with too little history are marked insufficient.
Output: research_outputs/alpha_sources_20260925.{csv,md}
"""
from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path

import alphalens as al
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
from alphalens_compat import tolerant_freq  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
spec = importlib.util.spec_from_file_location("split", Path(__file__).with_name("alphalens_split_events_20260925.py"))
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)
SPLIT = pd.Timestamp("2024-12-31")
BAN = (pd.Timestamp("2023-11-06"), pd.Timestamp("2025-03-30"))
SHORT_FACTORS = {"short_ratio_20d", "short_ratio_chg", "borrow_pct", "borrow_chg_20d"}


def tstat(s: pd.Series, step: int = 1) -> tuple[float, float, int]:
    s = s.dropna().iloc[::step]
    if len(s) < 3 or s.std() == 0:
        return np.nan, np.nan, len(s)
    return float(s.mean()), float(s.mean() / (s.std() / np.sqrt(len(s)))), len(s)


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    src = pd.read_parquet(IN / "alpha_sources.parquet")
    src["snapshot_date"] = pd.to_datetime(src.snapshot_date)
    prices, _ = split.build_prices(adj)
    sector = snap.dropna(subset=["sector_large"]).drop_duplicates("stock_code", keep="last").set_index("stock_code").sector_large.to_dict()
    sector = {c: sector.get(c, "미분류") for c in adj.columns}
    active = (vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5
    rows = []
    for name in [c for c in src.columns if c not in ("snapshot_date", "stock_code")]:
        fac = src.dropna(subset=[name]).set_index(["snapshot_date", "stock_code"])[name]
        fac.index.names = ["date", "asset"]
        fac = fac[~fac.index.duplicated()]
        # heavy ties (zero short/credit balances) make quantile binning drop most rows -> break ties with tiny fixed-seed noise (rank IC unaffected otherwise)
        rng = np.random.default_rng(7)
        fac = fac + rng.normal(0, max(float(fac.std()) * 1e-9, 1e-12), len(fac))
        fac = fac[[bool(active.at[d, a]) if (d in active.index and a in active.columns) else False for d, a in fac.index]]
        n_dates = fac.index.get_level_values(0).nunique()
        if n_dates < 8:
            rows.append({"factor": name, "verdict": "insufficient_history", "n_dates": n_dates})
            continue
        try:
            with tolerant_freq():
                data = al.utils.get_clean_factor_and_forward_returns(fac, prices, quantiles=5, periods=(20, 60), max_loss=0.7,
                                                                groupby=sector, binning_by_group=False)
        except Exception as exc:  # noqa: BLE001
            rows.append({"factor": name, "verdict": f"error {repr(exc)[:80]}", "n_dates": n_dates})
            continue
        for kind, sn in (("raw", False), ("sector_neutral", True)):
            ic = al.performance.factor_information_coefficient(data, group_adjust=sn)
            for h, step in ((20, 1), (60, 3)):
                s = ic[f"{h}D"]
                subsets = {"all": s, "train": s[s.index <= SPLIT], "valid": s[s.index > SPLIT]}
                if name in SHORT_FACTORS:
                    subsets["ban"] = s[(s.index >= BAN[0]) & (s.index <= BAN[1])]
                    subsets["no_ban"] = s[(s.index < BAN[0]) | (s.index > BAN[1])]
                r = {"factor": name, "ic_kind": kind, "horizon": f"{h}D", "n_dates": n_dates}
                for k, v in subsets.items():
                    m, t, n = tstat(v, step)
                    r[f"{k}_ic"], r[f"{k}_t"], r[f"{k}_n"] = m, t, n
                qr = al.performance.mean_return_by_quantile(data, by_group=False)[0]
                q = qr[f"{h}D"] if f"{h}D" in qr.columns else qr.iloc[:, 0]
                r["q5_minus_q1_pct"] = float((q.iloc[-1] - q.iloc[0]) * 100) if len(q) >= 2 else np.nan
                rows.append(r)
    df = pd.DataFrame(rows)
    if "all_t" in df:
        good = df.dropna(subset=["all_t"]).copy()
        good["sign_kept"] = np.sign(good.train_ic) == np.sign(good.valid_ic)
        good["cand_row"] = good.sign_kept & (good.all_t.abs() >= 2) & (good.valid_ic.abs() >= 0.02)
        df = df.merge(good[["factor", "ic_kind", "horizon", "sign_kept", "cand_row"]], on=["factor", "ic_kind", "horizon"], how="left")
        verdict = df.groupby("factor").cand_row.any().map({True: "candidate", False: "rejected"})
        prior = df["verdict"] if "verdict" in df else pd.Series(np.nan, index=df.index)
        df["verdict"] = df.factor.map(verdict).fillna(prior)
    df.to_csv(OUT / "alpha_sources_20260925.csv", index=False)
    pd.set_option("display.width", 250)
    show = df.dropna(subset=["all_ic"])[["factor", "ic_kind", "horizon", "all_ic", "all_t", "train_ic", "train_t", "valid_ic", "valid_t", "sign_kept", "q5_minus_q1_pct", "verdict"]]
    print(show.round(3).to_string(index=False))
    extra = df[df.factor.isin(SHORT_FACTORS) & (df.ic_kind == "raw")][["factor", "horizon", "ban_ic", "ban_t", "no_ban_ic", "no_ban_t"]]
    print(extra.round(3).to_string(index=False))
    print(df[df.verdict.astype(str).str.startswith(("insufficient", "error"))][["factor", "verdict", "n_dates"]].drop_duplicates().to_string(index=False))

    # ---- orthogonalized IC: does the factor carry information beyond size / recent return / liquidity-volume controls?
    controls = ["market_cap_log", "ret_20d", "ret_60d", "ret_120d", "dist_high_252", "vol_ratio_20d"]
    sn = snap.copy()
    sn["snapshot_date"] = pd.to_datetime(sn.snapshot_date)
    merged = src.merge(sn[["snapshot_date", "stock_code"] + controls], on=["snapshot_date", "stock_code"], how="left")
    orth_rows = []
    for name in df[df.verdict == "candidate"].factor.unique():
        res = []
        for d, g in merged.dropna(subset=[name] + controls).groupby("snapshot_date"):
            if len(g) < 200:
                continue
            X = g[controls].to_numpy(float)
            X = (X - X.mean(0)) / np.where(X.std(0) == 0, 1, X.std(0))
            X = np.c_[np.ones(len(X)), np.clip(X, -4, 4)]
            y = g[name].rank(pct=True).to_numpy(float)
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            res.append(pd.Series(y - X @ beta, index=pd.MultiIndex.from_arrays([[d] * len(g), g.stock_code], names=["date", "asset"])))
        if not res:
            continue
        fac = pd.concat(res)
        fac = fac[~fac.index.duplicated()]
        fac = fac[[bool(active.at[d, a]) if (d in active.index and a in active.columns) else False for d, a in fac.index]]
        try:
            with tolerant_freq():
                data = al.utils.get_clean_factor_and_forward_returns(fac, prices, quantiles=5, periods=(20, 60), max_loss=0.7,
                                                                groupby=sector, binning_by_group=False)
        except Exception as exc:  # noqa: BLE001
            orth_rows.append({"factor": name, "horizon": "err", "note": repr(exc)[:80]})
            continue
        ic = al.performance.factor_information_coefficient(data, group_adjust=False)
        for h, step in ((20, 1), (60, 3)):
            ser = ic[f"{h}D"]
            r = {"factor": name, "horizon": f"{h}D"}
            for k, v in {"all": ser, "train": ser[ser.index <= SPLIT], "valid": ser[ser.index > SPLIT]}.items():
                m, t, n = tstat(v, step)
                r[f"{k}_ic"], r[f"{k}_t"] = m, t
            r["sign_kept"] = bool(np.sign(r["train_ic"]) == np.sign(r["valid_ic"]))
            r["orth_pass"] = bool(r["sign_kept"] and abs(r["all_t"]) >= 2 and abs(r["valid_ic"]) >= 0.02)
            orth_rows.append(r)
    orth = pd.DataFrame(orth_rows)
    orth.to_csv(OUT / "alpha_sources_orth_20260925.csv", index=False)
    print("== orthogonalized (controls: " + ", ".join(controls) + ")")
    print(orth.round(3).to_string(index=False))

    md = ["# R4 미사용 알파 원천 검증 (Alphalens) — 2026-09-25", "",
          "시스템 검증 결과이며 투자 권유가 아니다. 통과 조건: 학습·검증 부호 유지 + 전체 |t(비겹침)|≥2 + 검증 |IC|≥0.02. 학습 ≤2024-12 / 검증 2025-01~, 폐지 종목 포함, 섹터중립 병기.", "",
          "## 결과 (원시 IC, 20D/60D)", "", show.round(3).to_markdown(index=False), "",
          "## 공매도 금지 기간(2023-11-06~2025-03-30) 분리", "", extra.round(3).to_markdown(index=False), "",
          "## 직교화 검증 — 시총·수익률(20/60/120일)·52주 고점 거리·거래량비 제거 후 IC (원시 통과 후보만)", "", (orth.round(3).to_markdown(index=False) if len(orth) else "없음"), ""]
    (OUT / "alpha_sources_20260925.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
