#!/usr/bin/env python3
"""Exposure of the strategy_center backtests to QUARANTINED price rows (price_jump_audit classification 'quarantined_basis', return_usable=0).

backtest_common.py reads price_history directly at 12 places and does not filter return_usable, so rows whose price basis could not be verified (mostly fractional
pre-2024 values of 118 codes) can enter the backtests. Before changing those 12 queries (which would re-baseline every stored result) this report measures how many
closed backtest trades (data_cache/research/adoption_trades.parquet, from research/extract_adoption_inputs_20260925.py) hold a stock through a quarantined date.
Output: research_outputs/quarantine_backtest_exposure_20260926.md"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

conn = connect_primary_db(readonly=True, timeout=300)
q = pd.DataFrame([tuple(r) for r in conn.execute(
    "SELECT stock_code, CAST(event_date AS TEXT) FROM price_jump_audit WHERE classification='quarantined_basis' AND return_usable=0").fetchall()],
    columns=["stock_code", "event_date"])
t = pd.read_parquet(ROOT / "data_cache" / "research" / "adoption_trades.parquet")
t = t[t.entry_date.str.len() == 10].copy()
t["exit_date"] = t.exit_date.where(t.exit_date.str.len() == 10, "2999-12-31")
qs = q.groupby("stock_code").event_date.apply(list).to_dict()

def hit(r):
    ds = qs.get(r.stock_code)
    return bool(ds) and any(r.entry_date <= d <= r.exit_date for d in ds)

t["exposed"] = [hit(r) for r in t.itertuples()]
n, k = len(t), int(t.exposed.sum())
by = t.groupby("strategy").exposed.agg(["sum", "count"]).assign(pct=lambda d: (d["sum"] / d["count"] * 100).round(2)).sort_values("pct", ascending=False)
pnl_all = t.profit_pct.dropna().mean()
pnl_exp = t[t.exposed].profit_pct.dropna().mean()
lines = ["# 백테스트의 격리 가격행 노출 — 2026-09-26", "",
         f"- 격리(quarantined_basis, return_usable=0) 이벤트 {len(q):,}행 / {q.stock_code.nunique()}종목 (연도별 대부분 2019~2023)",
         f"- strategy_center 선정 실행의 청산 거래 {n:,}건 중 격리 날짜를 보유 구간에 포함하는 거래: **{k:,}건 ({k / n * 100:.2f}%)**",
         f"- 노출 거래 평균 수익률 {pnl_exp:.2f}% vs 전체 {pnl_all:.2f}%", "", "## 전략별 노출", "", "| 전략 | 노출 거래 | 전체 거래 | 노출 % |", "|---|---:|---:|---:|", *[f"| {i} | {int(r['sum'])} | {int(r['count'])} | {r['pct']} |" for i, r in by.iterrows()], "",
         "## 해석", "",
         "- 노출 비율이 작고 특정 전략에 편중되지 않으면 백테스트 재기준화(12개 조회 수정 → 모든 저장 결과 재실행) 대신 격리 종목·구간을 신호 후보에서 제외하는 가드를 권고한다.",
         "- 노출이 큰 전략은 그 전략만 재실행해 차이를 확인한다."]
out = ROOT / "research_outputs" / "quarantine_backtest_exposure_20260926.md"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"trades={n:,} exposed={k:,} ({k / n * 100:.2f}%) | mean pnl exposed {pnl_exp:.2f}% vs all {pnl_all:.2f}%")
print(by.head(8).to_string())
