"""
backtest.py -- re-export shim (2026-09-03 split).

The 35 strategy engines that used to live in this single 14,098-line file were
moved to backtest_common.py (shared infra) and backtest_strategies/*.py (one
file per strategy) for token-cost/maintainability reasons. Every name that used
to be importable as `from backtest import X` or `backtest.X` is re-exported
here unchanged, so no caller anywhere else in the codebase needed to change.
"""
import argparse

from backtest_common import *  # noqa: F401,F403 -- re-export DB_PATH, sqlite3, all shared helpers, etc.
from backtest_common import (
    sqlite3, DB_PATH, WARMUP_DAYS, logger, _DatabaseRouter,
)

from backtest_strategies.base import run_backtest
from backtest_strategies.composite import run_backtest_composite
from backtest_strategies.contract_momentum import run_backtest_contract_momentum
from backtest_strategies.deep_recovery import run_backtest_deep_recovery
from backtest_strategies.dual_conviction import run_backtest_dual_conviction
from backtest_strategies.earnings_conviction import run_backtest_earnings_conviction
from backtest_strategies.earnings_supply_discovery import run_backtest_earnings_supply_discovery
from backtest_strategies.extreme_dd_volume import run_backtest_extreme_dd_volume
from backtest_strategies.golden_cross import run_backtest_golden_cross
from backtest_strategies.hidden_rev import run_backtest_hidden_rev
from backtest_strategies.high_profit_compound import run_backtest_high_profit_compound
from backtest_strategies.low_base_breakout import run_backtest_low_base_breakout
from backtest_strategies.magic_formula import run_backtest_magic_formula
from backtest_strategies.megatrend import run_backtest_megatrend
from backtest_strategies.meta_v2 import run_backtest_meta_v2
from backtest_strategies.moonshot_turnaround import run_backtest_moonshot_turnaround
from backtest_strategies.patent_catalyst import run_backtest_patent_catalyst
from backtest_strategies.peak_easy import run_backtest_peak_easy
from backtest_strategies.peg import run_backtest_peg
from backtest_strategies.recovery import run_backtest_recovery
from backtest_strategies.regime_adaptive import run_backtest_regime_adaptive
from backtest_strategies.se_momentum import run_backtest_se_momentum
from backtest_strategies.sector import run_backtest_sector
from backtest_strategies.segment_revenue_divergence import run_backtest_segment_revenue_divergence
from backtest_strategies.turnaround import run_backtest_turnaround
from backtest_strategies.v1 import run_backtest_v1
from backtest_strategies.v10 import run_backtest_v10
from backtest_strategies.v10_hs import run_backtest_v10_hs
from backtest_strategies.v11 import run_backtest_v11
from backtest_strategies.v11_hs import run_backtest_v11_hs
from backtest_strategies.v12 import run_backtest_v12
from backtest_strategies.v1_dart import run_backtest_v1_dart
from backtest_strategies.v2 import run_backtest_v2
from backtest_strategies.v5 import run_backtest_v5
from backtest_strategies.v8 import run_backtest_v8
from backtest_strategies.value import run_backtest_value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',     default='2023-04-01')
    parser.add_argument('--end',       default='2025-12-31')
    parser.add_argument('--per-stock', type=float, default=10_000_000)
    parser.add_argument('--max-pos',   type=int,   default=10)
    parser.add_argument('--version',   default='V4', choices=['V4','V8','V10','V11','V12'])

    print(f"백테스트 시작 ({args.version}): {args.start} ~ {args.end}  "
          f"(종목당 {args.per_stock:,.0f}원, 최대 {args.max_pos}종목)")

    fn_map = {'V4': run_backtest, 'V8': run_backtest_v8,
              'V10': run_backtest_v10, 'V11': run_backtest_v11,
              'V12': run_backtest_v12}
    rid = fn_map[args.version](args.start, args.end,
                                per_stock=args.per_stock, max_positions=args.max_pos)
    print(f"완료! run_id={rid}")
    conn = sqlite3.connect(DB_PATH, timeout=120)
    row  = conn.execute("SELECT summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    conn.close()
    if row: print(row[0])
