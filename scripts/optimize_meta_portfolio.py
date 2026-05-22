#!/usr/bin/env python3
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DB_PATH = "/Applications/stock_dashboard/stock.db"
OUT_PATH = Path("/Applications/stock_dashboard/scratch/meta_portfolio_optimization_2026-05-19.json")

# 전략 후보 (v12/combo는 기본 제외)
STRATS = ["v_trend", "v11", "v5", "v1_value", "v2", "v10", "v8"]

# 5개 검증 구간
PERIODS = [
    ("2020-03-01", "2021-11-30", "offensive"),
    ("2021-12-01", "2022-10-31", "defensive"),
    ("2022-11-01", "2023-10-31", "offensive"),
    ("2023-11-01", "2024-12-31", "offensive"),
    ("2024-06-01", "2025-05-31", "defensive"),
]


@dataclass
class EvalResult:
    score: float
    avg_ret: float
    min_ret: float
    avg_mdd: float
    neg_count: int
    period_returns: dict[str, float]
    period_mdds: dict[str, float]
    w_off: dict[str, float]
    w_def: dict[str, float]


def _load_auto_matrix() -> dict[tuple[str, str], dict[str, dict[str, float]]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT start_date, end_date, strategy, total_return_pct, max_drawdown_pct
        FROM backtest_runs
        WHERE name LIKE 'AUTO %' AND status='done'
        """
    ).fetchall()
    conn.close()
    mat: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
    for r in rows:
        key = (r["start_date"], r["end_date"])
        mat.setdefault(key, {})
        mat[key][r["strategy"]] = {
            "ret": float(r["total_return_pct"] or 0.0),
            "mdd": float(r["max_drawdown_pct"] or 0.0),
        }
    return mat


def _rand_weights(strats: list[str], cash_min: float, cash_max: float) -> dict[str, float]:
    cash = random.uniform(cash_min, cash_max)
    raw = [random.random() for _ in strats]
    s = sum(raw) or 1.0
    left = 1.0 - cash
    w = {k: (v / s) * left for k, v in zip(strats, raw)}
    w["cash"] = cash
    return w


def _portfolio_ret_mdd(
    period_data: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> tuple[float, float]:
    # 단순 선형 결합 근사치
    ret = 0.0
    mdd = 0.0
    for s, w in weights.items():
        if s == "cash":
            continue
        if s not in period_data:
            continue
        ret += w * period_data[s]["ret"]
        mdd += w * abs(period_data[s]["mdd"])
    return ret, -mdd


def _evaluate(mat: dict[tuple[str, str], dict[str, dict[str, float]]], w_off: dict[str, float], w_def: dict[str, float]) -> EvalResult:
    pr: dict[str, float] = {}
    pm: dict[str, float] = {}
    for s, e, rg in PERIODS:
        data = mat[(s, e)]
        w = w_off if rg == "offensive" else w_def
        r, m = _portfolio_ret_mdd(data, w)
        pr[f"{s}~{e}"] = round(r, 4)
        pm[f"{s}~{e}"] = round(m, 4)
    vals = list(pr.values())
    mdds = list(pm.values())
    avg_ret = sum(vals) / len(vals)
    min_ret = min(vals)
    avg_mdd = sum(mdds) / len(mdds)
    neg_count = sum(1 for v in vals if v < 0)

    # 보수적 목적함수: 손실구간 최소화 + 최소수익 최대화 + 평균수익
    score = (
        avg_ret
        + 0.9 * min_ret
        + 0.3 * avg_mdd
        - 8.0 * neg_count
    )
    return EvalResult(
        score=score,
        avg_ret=avg_ret,
        min_ret=min_ret,
        avg_mdd=avg_mdd,
        neg_count=neg_count,
        period_returns=pr,
        period_mdds=pm,
        w_off=w_off,
        w_def=w_def,
    )


def optimize(n_iter: int = 120000) -> dict:
    random.seed(42)
    mat = _load_auto_matrix()

    best: EvalResult | None = None
    for _ in range(n_iter):
        # 공격구간: 현금 최소화
        w_off = _rand_weights(STRATS, cash_min=0.0, cash_max=0.12)
        # 방어구간: 현금 크게
        w_def = _rand_weights(STRATS, cash_min=0.15, cash_max=0.45)
        # 방어구간에서 v11, v1_value 최소 비중 강제
        if w_def.get("v11", 0) + w_def.get("v1_value", 0) < 0.35:
            continue
        # 방어구간에서 v12 없음(애초 제외), v5/v10 합 비중 제한
        if w_def.get("v5", 0) + w_def.get("v10", 0) > 0.15:
            continue

        ev = _evaluate(mat, w_off, w_def)
        if best is None or ev.score > best.score:
            best = ev

    assert best is not None

    # 기준선: combo
    baseline = {}
    for s, e, _ in PERIODS:
        d = mat[(s, e)]["combo"]
        baseline[f"{s}~{e}"] = {"ret": d["ret"], "mdd": d["mdd"]}

    # 비교용 단일전략 best(avg_ret)도 계산
    single_stats = {}
    for strat in STRATS:
        rets = [mat[(s, e)][strat]["ret"] for s, e, _ in PERIODS]
        mdds = [mat[(s, e)][strat]["mdd"] for s, e, _ in PERIODS]
        single_stats[strat] = {
            "avg_ret": round(sum(rets) / len(rets), 4),
            "min_ret": round(min(rets), 4),
            "avg_mdd": round(sum(mdds) / len(mdds), 4),
            "neg_count": sum(1 for r in rets if r < 0),
        }
    best_single = sorted(single_stats.items(), key=lambda kv: kv[1]["avg_ret"], reverse=True)[0]

    result = {
        "iterations": n_iter,
        "periods": [{"start": s, "end": e, "regime": rg} for s, e, rg in PERIODS],
        "optimized_meta": {
            "score": round(best.score, 4),
            "avg_ret": round(best.avg_ret, 4),
            "min_ret": round(best.min_ret, 4),
            "avg_mdd": round(best.avg_mdd, 4),
            "neg_count": best.neg_count,
            "weights_offensive": {k: round(v, 4) for k, v in best.w_off.items()},
            "weights_defensive": {k: round(v, 4) for k, v in best.w_def.items()},
            "period_returns": best.period_returns,
            "period_mdds": best.period_mdds,
        },
        "baseline_combo": baseline,
        "single_strategy_stats": single_stats,
        "best_single_by_avg_return": {"strategy": best_single[0], **best_single[1]},
    }
    return result


def main() -> None:
    result = optimize()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(OUT_PATH))
    print(json.dumps(result["optimized_meta"], ensure_ascii=False))


if __name__ == "__main__":
    main()
