# Codex Handoff: Strategy Overlay Expansion Research (2026-07-28)

## Objective

The user does not want to stop at the current Strategy Center logic. I added a
new research harness to test whether the newly collected stock-specific signals
can improve Strategy Center ranking and portfolio behavior.

## New File

- `/Applications/stock_dashboard/scripts/research_strategy_overlay_expansion.py`

## Generated Outputs

- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_20260728.json`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_20260728.md`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_summary_20260728.csv`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_monthly_20260728.csv`
- `/Applications/stock_dashboard/research_outputs/strategy_overlay_expansion_picks_20260728.csv`

## What It Tests

Base universe:

- `strategy_feature_snapshot`
- monthly signal snapshots
- liquid names only: 20-day average turnover >= KRW 2bn
- top 500 base model candidates per month

Overlay features:

- `order_contracts`: recent non-terminated supply/sales contract with revenue ratio
- `dart_backlog_quarterly`: recent backlog growth >= 30%
- `broker_program_stock_daily`: 20/60-day program net buy amount
- `strategy_feature_snapshot.supply_20d_억`: existing foreign/institution supply proxy
- `stockeasy_sector_rs_daily`: sector relative strength
- `dilution_events`: recent high-risk dilution event
- price-risk guard: deep 60d drawdown or far below 52w high
- optional KOSPI 6-month/3-month regime filter

Execution assumptions:

- signal: monthly snapshot
- fill: next trading day's open
- exit: next monthly snapshot's next trading day's open
- equal-weight top-N
- cost: 0.4% per invested month

## Validation Run

Command:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/research_strategy_overlay_expansion.py
/Applications/stock_dashboard/venv/bin/python scripts/research_strategy_overlay_expansion.py
```

The run completed successfully at `2026-07-28T22:47:14`.

## Codex Verification Result

Codex performed the verification directly. This did **not** find a clean
production-ready replacement strategy.

Two backtest issues were found and fixed during verification:

1. The first run allowed a 2020 signal to fill years later if the stock had no
   near-term price row. Fixed by requiring the next open to be within the
   intended rebalance window.
2. A second pass still allowed entry up to the next monthly snapshot date, which
   could be nearly a month later for suspended/sparse names. Fixed by requiring
   entry and exit fills within 10 calendar days after their signal dates.

Final conservative execution rule:

- entry: first trading open after signal date, but no later than signal date + 10 calendar days
- exit: first trading open after next monthly signal date, but no later than that date + 10 calendar days
- otherwise the position is skipped

Post-fix QA:

- `py_compile` passed.
- `strategy_overlay_expansion_picks_20260728.csv` has no entry gaps above 10 days.
- maximum entry gap after final fix: 9 days.
- no multi-year fill artifacts remain.

Final baseline after conservative fill fix:

| Period | TopN | Baseline Total | Baseline MDD |
|---|---:|---:|---:|
| 2020-2026 | 8 | -84.66% | -87.41% |
| 2020-2026 | 12 | -68.22% | -79.34% |
| 2020-2026 | 20 | +48.42% | -78.17% |
| 2024H2-2026 test | 8 | -55.87% | -48.22% |
| 2024H2-2026 test | 12 | -59.82% | -56.30% |
| 2024H2-2026 test | 20 | -53.72% | -49.53% |

Rejected candidate after stricter validation:

- `Top8 + order/backlog overlay`
- It initially looked strong, but the result was polluted by loose fill timing.
- After requiring fills within 10 calendar days, it no longer survives as a
  robust candidate.
- Do not promote it as a Strategy Center strategy.

Only surviving research candidate:

- `Top20 catalyst_or_flow + KOSPI regime + program/supply overlay`
- weights: `program=0.06`, `supply=0.03`
- predicate: at least one catalyst/flow signal
- KOSPI regime filter enabled
- test period 2024H2-2026: `+3.56%`, MDD `-14.46%`
- baseline Top20 test period: `-53.72%`, MDD `-49.53%`

Interpretation: this is not a high-return strategy. It is a **defensive
cash-deployment/risk-reduction overlay** that may help avoid bad regimes.
Candidate label should be `방어형 수급/프로그램 오버레이`, not `최고수익 전략`.

## Remaining Handoff For Claude

Claude is already busy, so this document is a result handoff, not a request to
redo Codex's work. Remaining items are lower-priority cross-checks:

1. Check whether `dart_backlog_quarterly.source_rcept_dt` is always a valid
   point-in-time availability date. If not, replace with report filing date or
   conservative quarter-end + 60 days.
2. Audit `strategy_feature_snapshot.model_score_12m` construction. Top8 and
   Top12 baseline are negative over 2020-2026, while Top20 is positive. That
   suggests rank concentration is fragile or stale.
3. If time permits, rerun the surviving defensive candidate in the shared strict
   simulator with:
   - one KRW 100m account
   - integer shares
   - cash constraints
   - tradability checks
   - actual next-open fills
   - realistic rebalance turnover
4. Do not show this as "best strategy" in Strategy Center yet. If surfaced, use
   a research badge such as `방어형 수급/프로그램 오버레이 후보`.
5. Consider a hybrid rule:
   - Top20 base model as the broad engine
   - catalyst_or_flow + regime as a defensive cash filter
   - order/backlog only as explanatory evidence, not a scoring booster for now

## Recommendation

The best next engineering step is not more free-form parameter search. It is to
promote the surviving overlay features into a versioned Strategy Center research
API so Codex can repeatedly run:

- base model
- base + order
- base + backlog
- base + order/backlog
- base + program/supply
- base + dilution exclusion
- regime on/off

under the same strict simulator contract.
