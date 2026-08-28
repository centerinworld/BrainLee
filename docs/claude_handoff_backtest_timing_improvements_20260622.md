# Claude Handoff: Backtest Timing Integrity and Improvement Direction

Date: 2026-06-22 KST
Author: Codex
Related audit: `docs/codex_audit_virtual_backtest_timing_20260622.md`

## Executive summary

Codex reviewed the current virtual trading/backtest timing logic.  The main conclusion is that the current daily backtest outputs should not be treated as production-grade performance yet.

The largest issue is not the signal idea itself.  The issue is execution timing.  Several daily backtest engines calculate signals using values that are only known after the market close, then buy at that same day's close.  This affects 52-week high, RSI/RA or relative strength, daily volume surge, daily investor flow, and KOSPI market-regime filters.

Financial statement timing is in better shape.  `backtest.py` already joins `financial_data` to `fin_disclosure_dates.avail_date`, and the database has 41,894 rows of disclosure availability records.  However, some research scripts still apply generic lags to material cost, backlog, financial, cashflow, employment, export, and quant data.  Generic lags are acceptable for rough research but not enough for final strategy validation.

## Codex opinion

The promising tenbagger and winner-pattern results should be preserved as candidate ideas, but all headline performance numbers must be rerun under strict timing rules before being shown as reliable.

The right path is not to abandon the strategy.  The right path is to separate:

1. Signal discovery: find features that were common in big winners.
2. Strict tradability validation: confirm those features were actually knowable before the simulated trade.
3. Execution realism: buy on the next tradable bar after the signal becomes available.
4. Capital/risk realism: include position count, budget, slippage, liquidity, MDD, and stop behavior.

If a strategy only works with same-day close execution after using same-day close/volume data, it should be rejected or downgraded.  If it survives next-day execution, it becomes a serious candidate.

## Highest priority fixes

### P0 - Change daily backtests to next-bar execution

Current problem:

- `backtest.py` documents same-day close execution.
- The buy signal uses current-day close, current-day full volume, same-day supply, and market close data.
- The portfolio then enters at the same day's close.

Required change:

- Signal date: after close of day `D`.
- Buy execution: day `D+1` open, if open exists.
- Conservative fallback: day `D+1` close, flagged as `execution_price_type='next_close_fallback'`.
- Rotation replacement should also execute on `D+1`, not day `D`.
- Sell signals based on close should also execute on the next tradable bar unless an intraday stop model based on high/low is explicitly implemented.

Implementation suggestion:

- Add a helper:

```python
def next_trading_index(dates: list[str], i: int) -> int | None:
    return i + 1 if i + 1 < len(dates) else None
```

- Load `open` prices from `price_history` wherever strict execution is required.
- Store both `signal_date` and `entry_date`.
- Store `signal_price` separately from `entry_price`.

Suggested trade fields:

```text
signal_date
entry_date
entry_price
entry_price_type
feature_available_at_max
has_lookahead_violation
availability_quality
```

### P0 - Add strict timing mode and label old results

Do not overwrite existing exploratory results.  Add a strict mode and compare.

Suggested modes:

| Mode | Description | Status |
|---|---|---|
| `legacy_same_close` | Current behavior. Signal and buy both at day `D` close. | Research only |
| `strict_next_open` | Signal after day `D` close, buy day `D+1` open. | Preferred |
| `strict_next_close` | Signal after day `D` close, buy day `D+1` close. | Conservative fallback |

Any UI/API table showing backtest results should label legacy results as `non_strict_timing`.

### P1 - Use exact DART receipt dates for material cost and backlog

Current problem:

- `scripts/discover_market2x_signals.py` maps quarterly material/backlog data by `quarter_end + 2 months`.
- But source tables already have DART receipt dates:
  - `dart_cost_quarterly.source_rcept_dt`
  - `dart_backlog_quarterly.source_rcept_dt`
  - `dart_report_items_quarterly.rcept_no`, joinable to `dart_disclosures.rcept_dt`

Required change:

- For DART-derived material cost and backlog, set `available_at = source_rcept_dt + 1 trading day`.
- Convert that date to `signal_month` only after availability is computed.
- Keep `quarter_end + 2 months` only as fallback and label it clearly:

```text
availability_quality = exact_disclosure | fallback_lag
```

Important:

- Affected rows should not enter a signal month earlier than their true DART availability.
- Recompute material cost QoQ/YoY only among rows already available at the signal date.

### P1 - Quant indicators need available date metadata

Current problem:

- `quant_major_indicator_series` has `period` and `updated_at`.
- It does not have `source_published_at` or `available_at`.
- Research code currently assumes `period_month + 1`.

Required change:

- Add one of these:
  - `available_at` column to `quant_major_indicator_series`, or
  - separate table `quant_major_indicator_availability`.

Recommended schema:

```sql
CREATE TABLE IF NOT EXISTS quant_major_indicator_availability (
    indicator_key TEXT NOT NULL,
    period TEXT NOT NULL,
    series_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_published_at TEXT,
    available_at TEXT NOT NULL,
    availability_quality TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (indicator_key, period, series_name, source_name)
);
```

Availability rules:

- Daily market-derived data: next trading day after observation date.
- Monthly public statistics: official release date if known.
- If official release date is not available: use conservative source-specific fallback and mark it `fallback_lag`.
- Scraped data with no publication metadata: use `updated_at` as earliest known local availability, or a conservative lag.

### P1 - Historical valuation must be recomputed as-of

Current problem:

- `valuation_history` has `period_end` and `updated_at`, but no availability date.
- Live display is fine.
- Historical backtest usage is unsafe if it reads precomputed PBR/PER percentiles without as-of filtering.

Required change:

- For historical strategy tests, compute PBR/PER using:
  - price at or before signal date
  - EPS/BPS from financial statements available at or before signal date
- PBR percentile should only compare against historical valuation observations that were also available by the signal date.

### P2 - Employment and export data should get source-specific availability

Current status:

- Export data uses a two-month delay.  This is conservative and acceptable for research.
- Employment data uses one-month delay.  This may be acceptable, but should be validated against source release timing.

Required change:

- Add an availability ledger for employment/NPS/WLB/customs data.
- If exact provider publication date is hard to obtain, use a deliberately conservative lag and label it.

## Files and code areas to inspect first

### Daily backtest execution

- `backtest.py`
  - same-day close documented near top comment
  - `_is_buy_signal`
  - `_run_portfolio`
  - `run_backtest`
  - `run_backtest_v5`
  - `run_backtest_v8`
  - `run_backtest_regime_adaptive`
  - `run_backtest_composite`
  - `run_backtest_meta_v2`

### Winner-pattern research

- `scripts/discover_market2x_signals.py`
  - `load_export_monthly`
  - `load_employment_features`
  - `load_financial_features`
  - `load_cashflow_features`
  - `load_backlog_features`
  - `load_material_features`
  - `quant_indicator_lifts`

- `scripts/research_winner_pattern_strategy.py`
  - next-month execution appears directionally safer, but should consume strict feature availability.

### Live signal display

- `tenbagger_engine.py`
  - mostly current/live usage, so not the same problem as historical backtest.
  - do not reuse live functions for historical tests unless they accept `as_of_date`.

## Acceptance criteria

Before a backtest result is considered valid:

1. Every feature used for a signal has an `available_at` or documented fallback availability date.
2. `feature_available_at_max <= signal_date`.
3. If the signal uses close/volume/day-end market data, execution is not same-day close.
4. Buy execution is `next_open` or conservative `next_close`.
5. Sell execution timing is documented separately for close-based exits and intraday stops.
6. Backtest output records timing metadata per trade.
7. Strategy summary separates:
   - `legacy_same_close`
   - `strict_next_open`
   - `strict_next_close`
8. Performance table includes CAGR/OOS/MDD/win rate/trade count after costs and slippage.

## Suggested validation run

Run the same tenbagger candidate strategy in three modes:

| Mode | Expected purpose |
|---|---|
| Legacy same-close | Reproduce current result for baseline comparison |
| Strict next-open | Main production candidate |
| Strict next-close | Conservative survivability test |

Then compare:

- total return
- CAGR
- OOS return
- MDD
- win rate
- number of trades
- average hold period
- worst 10 trades
- performance by market regime

If strict next-open and strict next-close both remain meaningfully above market return, the strategy is robust.  If performance collapses only after the timing fix, the previous result was timing leakage.

## Final recommendation

Do not tune further thresholds until timing integrity is fixed.  Optimizing before this patch risks optimizing a look-ahead artifact.

The next best action is:

1. Implement strict next-bar execution in daily backtests.
2. Add/propagate `available_at` for material cost, backlog, valuation, and quant indicators.
3. Rerun the tenbagger strategy matrix under strict modes.
4. Only then resume threshold optimization and signal combination search.
