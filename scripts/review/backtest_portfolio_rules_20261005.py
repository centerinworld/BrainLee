#!/usr/bin/env python3
"""계좌현황 매도 규칙(SIGNAL_RULES 5절 조합) vs 원 전략(모멘텀Easy) 백테스트 비교(2026-10-05).
같은 진입(모멘텀Easy 주도섹터·실적가속)·같은 기간에 매도 규칙만 바꿔 두 번 돌린다. 결과: backtest_runs + research_outputs/signal_rules_backtest_20261005.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtest_strategies.se_momentum import run_backtest_se_momentum  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

PERIODS = [("2020-03-01", "2026-09-30")]
if "--six" in sys.argv:  # 2026-10-06: 표준 6기간(routes/backtest.py STANDARD_PERIODS)으로 다기간 검증
    PERIODS = [("2020-03-01", "2021-11-30"), ("2021-12-01", "2022-10-31"), ("2022-11-01", "2023-10-31"),
               ("2023-11-01", "2024-12-31"), ("2024-06-01", "2025-05-31"), ("2025-06-01", "2026-03-31")]
out = []
for s, e in PERIODS:
    for mode in ("original", "portfolio_rules"):
        rid = run_backtest_se_momentum(s, e, exit_mode=mode, run_name=f"SIGNAL_RULES 비교 {mode} {s[:7]}~{e[:7]}")
        c = connect_primary_db(readonly=True)
        r = c.execute("SELECT total_return_pct, ann_return_pct, max_drawdown_pct, win_rate, total_trades FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
        out.append({"period": f"{s}~{e}", "exit_mode": mode, "run_id": rid, **dict(zip(["total_return_pct", "ann_return_pct", "max_drawdown_pct", "win_rate", "total_trades"], tuple(r) if r else [None] * 5))})
        print(out[-1], flush=True)
(ROOT / "research_outputs" / ("signal_rules_backtest_6p_20261006.json" if "--six" in sys.argv else "signal_rules_backtest_20261005.json")).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
