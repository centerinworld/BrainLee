# Codex Handoff: Fill-Timing Artifact Recheck (2026-07-28)

## Context

During Codex verification of the expanded Strategy Center overlay research, a
material backtest artifact was found. Some monthly signal rows were filled much
later than the intended next-trading-day execution window.

This matters because a monthly signal dated in one period can accidentally buy a
stock weeks or years later if the stock has sparse/missing `price_history` rows.
That can create false strategy performance.

## Affected Script

- `/Applications/stock_dashboard/scripts/research_strategy_overlay_expansion.py`

## Outputs To Inspect

- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_20260728.json`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_20260728.md`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_summary_20260728.csv`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_picks_20260728.csv`

## Original Artifact Found

Before the final fix, the picks file contained rows where:

- signal month: `2023-02-28`
- entry date: `2023-03-30`
- entry gap: 30 calendar days
- repeated stock sample: `056730 / CNT85`

This was not acceptable for a monthly next-open backtest because it behaves like
a delayed fill on a sparse/suspended listing rather than a realistic next-trading
execution.

An even earlier run also showed a more severe artifact: a 2020 monthly signal
could fill years later when the selected stock did not have nearby price rows.

## Codex Fix Applied

The `next_open` helper now accepts a `before_or_equal` limit.

Current execution rule in `research_strategy_overlay_expansion.py`:

- entry: first trading open after monthly signal date
- entry must be no later than signal date + 10 calendar days
- exit: first trading open after the next monthly signal date
- exit must be no later than next signal date + 10 calendar days
- otherwise the position is skipped

Relevant code area:

- `next_open(...)`
- `run_monthly(...)`

## Codex Post-Fix QA

Codex ran:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/research_strategy_overlay_expansion.py
/Applications/stock_dashboard/venv/bin/python scripts/research_strategy_overlay_expansion.py
```

Then checked the generated picks:

```python
import pandas as pd
p = pd.read_csv("research_outputs/strategy_overlay_expansion_picks_20260728.csv")
p["month_dt"] = pd.to_datetime(p.month)
p["entry_dt"] = pd.to_datetime(p.entry_date)
p["entry_gap"] = (p.entry_dt - p.month_dt).dt.days
print(p.entry_gap.max())
print((p.entry_gap > 10).sum())
```

Observed result after final fix:

- maximum entry gap: `9` days
- rows with entry gap above 10 days: `0`
- no multi-year fill artifacts remained in the generated picks file

## Result Impact

The stricter fill rule materially reduced the apparent attractiveness of earlier
overlay candidates.

Most important change:

- `Top8 + order/backlog overlay` initially looked strong.
- After strict fill-window enforcement, it no longer survived as a robust
  production candidate.
- It should **not** be promoted to Strategy Center as a high-return strategy.

Final surviving candidate was defensive only:

- `Top20 catalyst_or_flow + KOSPI regime + program/supply overlay`
- test period 2024H2-2026: `+3.56%`
- MDD: `-14.46%`
- baseline Top20 same period: `-53.72%`, MDD `-49.53%`

Interpretation:

- This is a defensive cash-deployment/risk-reduction overlay candidate.
- It is not a high-return standalone strategy.

## Claude Recheck Instructions

Please recheck the following when available:

1. Re-run the script and confirm no generated pick has:
   - entry gap > 10 calendar days
   - exit gap > 10 calendar days from the next monthly signal date
   - entry date years after signal date
2. Search specifically for the earlier sample:
   - `stock_code=056730`
   - `stock_name=CNT85`
   - `month=2023-02-28`
   - verify it is either excluded or filled within the final rule.
3. Audit whether `strategy_feature_snapshot` contains names with stale/sparse
   `price_history` and whether those should be excluded before ranking.
4. Check other monthly backtest scripts for the same bug pattern:
   - selecting a signal on date T
   - using the first available future price without a maximum allowed gap
5. If the same bug exists elsewhere, standardize a shared helper:
   - `next_tradable_open(code, signal_date, max_gap_days=10)`
   - return `None` when no realistic fill exists
6. Do not compare old overlay runs generated before this fix with the final
   conservative run.

## Suggested Status Label

Use this status in Strategy Center or docs:

`체결일 검증 완료: 10일 초과 지연체결 제외`

Do not use:

`수주/잔고 고수익 전략 검증 완료`

