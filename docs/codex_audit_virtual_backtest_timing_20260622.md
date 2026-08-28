# Codex Audit: Backtest Timing and Data Availability

Date: 2026-06-22 KST

## Bottom line

The current backtest stack is not uniformly production-safe yet.  Financial statement timing is mostly handled in `backtest.py` through `fin_disclosure_dates.avail_date`, but several strategy/backtest paths still use end-of-day indicators and then execute at the same day close.  That is a practical look-ahead risk because 52-week position, RA/relative strength, market regime, volume surge, RSI, and daily supply are only fully known after the close.

The monthly winner-pattern research path is safer because it forms signals at month end and evaluates next-month execution.  However, some low-frequency features in that path still use generic lags instead of actual provider/disclosure availability dates.

## Checked tables

| Table | Rows checked | Date coverage | Timing status |
|---|---:|---|---|
| `fin_disclosure_dates` | 41,894 | 2016-07-05 to 2026-06-02 | Good. Stores DART disclosure date and next-day `avail_date`. |
| `dart_disclosures` | 458,051 | 2016-05-20 to 2026-06-19 | Good source for exact disclosure timing. |
| `financial_data` | 191,872 | 2015Q4 to 2026Q1 | No native available date, but `backtest.py` joins `fin_disclosure_dates`. |
| `valuation_history` | 63,512 | 2019-03-31 to 2026-03-31 | Risky for historical backtest if used directly; no disclosure/available date. |
| `quant_major_indicator_series` | 136,640 | 1960-01 to 202606 | Risky/medium. Has `period` and `updated_at`, but no source publish/available date. |
| `dart_cost_quarterly` | 4,800 with `source_rcept_dt` | 2021-03-29 to 2026-03-23 | Good source timing exists but is not always used by research code. |
| `dart_backlog_quarterly` | 1,507 with `source_rcept_dt` | 2022-05-16 to 2026-06-15 | Good source timing exists but is not always used by research code. |
| `dart_report_items_quarterly` | 53,845 with linked DART date | 2020-05-15 to 2026-06-02 | Good source timing can be reconstructed through `rcept_no`. |

## Findings

### P0 - Same-day close execution is not a valid strict simulation

`backtest.py` explicitly says buy execution is same-day close.  The buy signal then uses same-day close, same-day volume, 52-week high including current close, RSI including current close, same-day supply, and KOSPI regime from current close.

Evidence:

- `backtest.py:37` documents same-day close execution.
- `backtest.py:311` computes 52-week high through `i + 1`, including the signal day's close.
- `backtest.py:316` computes RSI through the signal day.
- `backtest.py:321-325` compares the signal day's full volume to prior average.
- `backtest.py:346-347` sums supply through the signal day.
- `backtest.py:522-526` enters the position at that same day's close.
- `backtest.py:847-849` builds KOSPI market regime from the same day's close.

Required fix:

- Treat all close/volume/RA/52-week/market-regime signals as available after market close.
- Execute new buys at the next tradable bar, preferably next trading day open.  If open is missing, use next trading day close with a conservative slippage flag.
- Also execute rotation replacement on the next bar, not at the signal day's close.
- Keep sell logic explicit: if stop-loss/MA break is based on close, it can only be executed next day open/close unless an intraday high/low stop model is implemented.

### P1 - Financial statement timing is mostly correct in `backtest.py`, but not universal

`backtest.py` has a good mechanism:

- `backtest.py:101-108` loads `fin_disclosure_dates`.
- `backtest.py:137-160` returns only financial rows with `avail_date <= target_date`.
- `backtest.py:749-771` joins `financial_data` to `fin_disclosure_dates` and falls back to statutory deadlines.

Remaining concern:

- The fallback for Q4 quarterly data is `year+1-02-15`.  If a row actually represents annual full-year information or data only present in the business report, this can be too early.  Annual rows are correctly delayed to `year+1-03-31`, but Q4 quarterly rows should be checked source-by-source.
- Other scripts that use `financial_data` may not always join `fin_disclosure_dates`.

Required fix:

- Make `avail_date` a required field in reusable feature builders, not an optional convention.
- Prefer `fin_disclosure_dates` or `dart_disclosures.rcept_dt + 1 trading day` over formula dates.
- Add an assertion to every strict backtest: no feature row may have `available_at > signal_date`.

### P1 - Material cost and backlog have exact DART receipt dates but research code uses generic lag

`scripts/discover_market2x_signals.py` maps quarterly features to `quarter_end + 2 months`:

- Financial features: `scripts/discover_market2x_signals.py:291`
- Cashflow features: `scripts/discover_market2x_signals.py:329`
- Backlog features: `scripts/discover_market2x_signals.py:355`
- Cost structure features: `scripts/discover_market2x_signals.py:385`
- DART material cost features: `scripts/discover_market2x_signals.py:420`

This is conservative for many Q1/Q2/Q3 disclosures, but it ignores exact `source_rcept_dt` that already exists in `dart_cost_quarterly` and `dart_backlog_quarterly`.  It may be too early or too late depending on the company/report.

Required fix:

- For `dart_cost_quarterly`, set `signal_month` from `source_rcept_dt + 1 trading day`, not from `quarter_signal_month`.
- For `dart_backlog_quarterly`, same.
- For `order_backlog` and `cost_structure`, join back to DART source tables or add `available_at` during collection.
- Keep the generic lag only as a fallback when exact disclosure dates are unavailable, and mark such rows `availability_quality='fallback_lag'`.

### P1 - Quant indicators need publish/available date metadata

`quant_major_indicator_series` has `period` and `updated_at`, but not a source-specific publication date.  `scripts/discover_market2x_signals.py:701` assumes `signal_month = period_month + 1`.

That is acceptable as a rough research delay, but it is not strict enough for a production-grade backtest because:

- Some monthly macro/public data are released weeks later.
- Some daily market-derived indicators are available after the same trading day close.
- Some scraped indicators may only be known when the collection script ran, which is `updated_at`, not `period`.

Required fix:

- Add `available_at` or `source_published_at` to `quant_major_indicator_series`.
- For market-derived daily values, use next trading day.
- For public monthly/weekly statistics, use the official publication date if available; otherwise use a conservative source-specific calendar.
- In strict backtests, use `available_at <= signal_date`, never `period <= signal_period` alone.

### P1 - `valuation_history` is unsafe for historical PBR/PER percentile backtests unless reconstructed as-of

`valuation_history` stores `period_end`, `updated_at`, and precomputed valuation values.  It has no disclosure/availability date.  Current live display/use is fine, but historical backtests must not read the latest completed valuation table as if every row was known at period end.

Required fix:

- Rebuild historical valuation from price at signal date plus EPS/BPS available as of signal date.
- For PBR percentile, use only prior valuation observations whose financial denominator was already available.

### P2 - Monthly winner-pattern research is directionally safer, but still needs an availability ledger

`scripts/research_winner_pattern_strategy.py` and `scripts/discover_market2x_signals.py` are better aligned with realistic execution because price-derived monthly signals are formed at month end and returns are evaluated from the next month.  Export data also uses a two-month delay (`scripts/discover_market2x_signals.py:104-105`).

Remaining concern:

- Employment data uses `period + 1 month` (`scripts/discover_market2x_signals.py:176`, `189`, `201`) without proving the exact source release date.
- Quant indicators use `period + 1 month`.
- Material/backlog exact DART dates should replace generic lags.

## Recommended strict backtest contract

Every strategy result shown to the user should record these fields:

| Field | Meaning |
|---|---|
| `signal_date` | Date on which the signal is evaluated after all required data are available. |
| `decision_time` | `after_close`, `pre_open`, or `intraday`. |
| `execution_date` | Actual simulated trade date. |
| `execution_price_type` | `next_open`, `next_close`, `same_close`, etc. |
| `feature_available_at_max` | Latest available timestamp among all features used. |
| `has_lookahead_violation` | True if any feature availability is after `signal_date`. |
| `availability_quality` | `exact_disclosure`, `exact_provider`, `next_day_market`, or `fallback_lag`. |

For production ranking, only `has_lookahead_violation = 0` and `execution_price_type in ('next_open', 'next_close')` should be accepted.

## Immediate implementation order

1. Patch daily backtest engines so buy/rotation entries execute on the next trading day, not same-day close.
2. Add a reusable `available_at` filter to all feature-loading functions.
3. Change material cost and backlog feature builders to use `source_rcept_dt + 1 trading day`.
4. Add `available_at` to `quant_major_indicator_series`, or create `quant_major_indicator_availability` keyed by `(indicator_key, period, series_name, source_name)`.
5. Recompute all headline strategy returns after the strict timing patch.  Prior OOS/CAGR/MDD values should be labeled "non-strict timing" until rerun.

## Claude handoff

Do not trust the current same-day-close daily backtest output as final.  The first validation rerun should compare:

- Current mode: signal day close -> signal day close execution.
- Strict mode A: signal day after close -> next trading day open execution.
- Strict mode B: signal day after close -> next trading day close execution.

If the strategy survives both strict modes after costs/slippage, then it can be considered a candidate.  If performance collapses, the previous result was mainly a timing artifact.
