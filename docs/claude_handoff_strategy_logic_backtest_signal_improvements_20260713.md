# Claude handoff: strategy logic, backtest, and signal-capture improvements

Date: 2026-07-13 (Asia/Seoul)  
Scope: `backtest.py`, `routes/backtest.py`, Strategy Center frontend, live signal generation  
Overall assessment: **Needs revision before any strategy is described as production-ready**

## 1. Executive conclusion

The project has many useful hypotheses, but strategy comparisons are not yet apples-to-apples. There are 21 public `run_backtest*` functions and at least eight independent portfolio loops. Execution timing, cash handling, position limits, universe construction, market-cap treatment, costs, and result metadata differ by engine.

Do not improve headline returns by adding more filters until every compared strategy uses one immutable backtest contract. The immediate objective is not the highest historical return. It is the best repeatable **net return relative to drawdown and concentration**, under information that was actually available at the time.

## 2. P0 findings

### P0-1. The frontend currently contains contradictory strategy truth

- `frontend/src/App.jsx:1047` shows V-GC as rejected: avg6 `-0.6%`.
- `frontend/src/App.jsx:2050` still shows V-GC avg6 `+43.5%` and `top3:true`.
- `frontend/src/App.jsx:2076` contains the newer period vector `[81.8,-52.5,-10.6,-23.6,-24.7,26.2]`.
- `frontend/src/App.jsx:2090` still shows the earlier continuous result `+358.79%`.
- `routes/backtest.py:745` rejects V-GC, while `routes/backtest.py:766` still describes the old `+71.1%`, 6/6-positive result.

Required fix: create one backend result registry and render every Strategy Center view from it. Remove duplicated hard-coded arrays and descriptions.

### P0-2. Results have no immutable methodology identity

`backtest_runs` can contain several materially different runs under the same `strategy` name. A later UI query can mix legacy same-close, next-open, fixed-slot, dynamic-slot, current-market-cap, and as-of approximations.

Add these mandatory columns or an attached `backtest_run_specs` table:

```text
engine_version, strategy_version, git_commit, data_snapshot_at,
signal_timing, execution_timing, price_basis, universe_version,
market_cap_mode, initial_cash, allocation_rule, max_positions_rule,
fee_model, slippage_model, impact_model, benchmark,
parameter_json, feature_availability_policy, run_hash, supersedes_run_id
```

The frontend must select an explicit `run_hash`, never merely the latest row for a strategy and period.

### P0-3. Backtest engines are still inconsistent

Already moved toward next-open/cash-aware execution:

- `_run_portfolio`
- `_run_generic_backtest`
- `run_backtest_golden_cross`
- `run_backtest_deep_recovery`
- `run_backtest_low_base_breakout`

Still requiring migration or direct proof of equivalence:

- `_run_generic_backtest_with_sc` (fixed positions; separate behavior)
- `run_backtest_v12`
- `run_backtest_v8`
- `run_backtest_regime_adaptive`
- `run_backtest_composite`
- `run_backtest_meta_v2`
- `run_backtest_sector`
- `run_backtest_recovery`
- `run_backtest_high_profit_compound`
- `run_backtest_turnaround`

Several of these loops still create entries or exits using the same day's close and/or fixed `max_positions`. Migrate them to one shared order simulator before rerunning headline matrices.

### P0-4. `asof_mktcap` is useful but not fully point-in-time

Current approximation often uses historical close multiplied by **current** `stock_universe.shares_issued`, and the stock universe is primarily today's listed universe. This does not remove:

- delisted-company survivorship bias;
- historical share-count changes;
- mergers, spin-offs, relistings, and code changes;
- historical market/sector classification changes.

Build `security_master_history` with listing/delisting dates, historical shares, market, sector, ETF/ETN status and tradability intervals. Eligibility must be evaluated for each signal date.

### P0-5. Corporate actions cannot be handled by dropping the whole stock

Many engines discard a stock if any daily ratio exceeds a split threshold. This removes genuine winners and creates selection bias. Use the canonical corporate-action-adjusted OHLCV series and retain raw prices for execution verification. Mark questionable intervals, not the entire security history.

## 3. One mandatory backtest contract

Every Strategy Center strategy should satisfy all items below:

1. Features are calculated after day D close using only rows with `available_at <= D close`.
2. Market orders generated on D execute at D+1 open plus modeled slippage; if not tradable, remain pending or expire according to a recorded rule.
3. Limit-up, limit-down, suspension, zero volume, delisting and management designation are executable-state constraints.
4. Shares are integers. Cash can never be negative. Fees and taxes are deducted from cash at execution.
5. One KRW 100m account is shared across all signals. No strategy may allocate KRW 100m independently per stock.
6. Dynamic expansion must be explicit: if the ticket is KRW 10m, KRW 110m equity permits at most 11 tickets. Do not mix this with `equity / 10` ticket resizing under the same label.
7. Positions compete on the same day using a deterministic ranking and stable tie-breaker.
8. Forced closing uses the last genuinely tradable price, with a delisting/recovery policy. Never silently use stale marks.
9. All calculations use adjusted analytical prices, while fills use executable raw prices consistently transformed for corporate actions.
10. The run fails if any feature availability, cash, duplicate-position or signal-before-fill assertion fails.

Create contract tests for each point. A strategy cannot receive a green `verified` badge without passing them.

## 4. Data and feature improvements

### 4.1 Publication-time ledger

Extend `data_availability_ledger` so every feature lookup resolves through one API. Avoid scattered fixed lags such as 45 days or two months when an exact filing/publication timestamp exists. Fallback lags must carry `availability_quality=fallback` and be tested separately.

### 4.2 Point-in-time financials

- Restatements must not overwrite what investors originally knew.
- Store original filing, amendment filing, effective availability time and value version.
- Use consolidated/separate accounting consistently.
- Prevent annual and fourth-quarter double counting.

### 4.3 Tradable universe

Daily eligibility should include listing status, ETF/ETN exclusion, preferred/SPAC/reit policy, suspension, management designation, liquidation trading, buy restriction, minimum turnover and free-float market cap.

### 4.4 Exposure-weighted alternative indicators

HS trade, quant indicators, orders and commodity signals should affect a stock only when mapping confidence and business exposure are material. Use:

```text
effective_signal = normalized_indicator_change
                 * direction_sign
                 * revenue_or_profit_exposure
                 * mapping_confidence
                 * freshness_weight
```

Do not give a full-strength signal when the linked business is a minor revenue contributor. Unknown exposure should lower confidence, not default to 100%.

## 5. Buy-signal improvements

### 5.1 Separate eligibility, trigger and ranking

- Eligibility: tradable universe, liquidity, financial safety, data confidence.
- Trigger: a new event or state transition, not a condition that remains true for months.
- Ranking: expected return, downside risk, exposure, catalyst freshness and portfolio diversification.

This prevents repeated buying of stale signals and makes attribution possible.

### 5.2 Detect transitions, not levels alone

Examples:

- volume/turnover acceleration from baseline, with persistence confirmation;
- indicator red-to-yellow-to-green transition;
- earnings revision breadth and magnitude change;
- order backlog acceleration relative to revenue, not backlog presence alone;
- insider purchase size relative to salary, ownership and free float;
- foreign/institutional flow normalized by free float and turnover;
- deep-drawdown stabilization: volatility contraction, failed new low and reclaim, not merely distance from the high.

### 5.3 Add signal cooldown and lifecycle

Store `first_seen`, `last_confirmed`, `invalidated_at`, `cooldown_until`, and `signal_family`. A stock should not generate multiple independent buys from the same unchanged fact.

### 5.4 Portfolio-aware ranking

Ranking should penalize correlated exposures, duplicated sectors, illiquidity and common catalysts. A slightly weaker independent signal may improve account-level return more than the tenth semiconductor signal.

## 6. Sell-signal improvements

One trail/stop template should not be reused across unrelated hypotheses.

- Trend strategy: exit on trend failure or volatility-adjusted trailing stop.
- Earnings/catalyst strategy: exit when estimate revisions, orders or margin direction deteriorates.
- Deep recovery: exit when the recovery thesis fails, a new low occurs, or balance-sheet risk rises.
- Indicator-linked strategy: exit when the indicator reverses after publication, exposure changes, or the stock no longer responds.

Use ATR/realized-volatility scaling and a maximum account-risk budget. Test stop gaps at next tradable price. Evaluate time stops by opportunity cost, but do not tighten exits solely to improve win rate; report whether large positive tails are being removed.

## 7. Validation design

### 7.1 Stop optimizing on the six displayed windows

Use nested walk-forward validation:

- development: 2020-2022;
- validation: 2023-2024;
- untouched test: 2025 onward;
- historical holdout: 2015-2019 after point-in-time universe coverage is ready.

Parameter selection occurs only inside the development window. The displayed six windows become descriptive stress periods, not repeated optimization targets.

### 7.2 Correct for multiple testing

Record every attempted parameter set, including failed experiments. Report deflated Sharpe/probability of backtest overfitting or, at minimum, the number of trials and a bootstrap confidence interval. A result found after 80 experiments should not be judged like a single prespecified hypothesis.

### 7.3 Required robustness tests

- next-open and next-close fills;
- fees/slippage at 1x, 2x and stressed levels;
- no top winner, no top five winners;
- sector-neutral and max-sector-weight variants;
- current-universe versus historical-universe comparison;
- parameter +/-20% sensitivity;
- bootstrap trade-order and block-bootstrap return paths;
- capacity at KRW 100m, 500m and 1bn;
- benchmark excess return against KOSPI/KOSDAQ and an investable equal-weight universe.

## 8. Metrics the frontend must show

Do not rank by average return or win rate alone. Show:

- continuous total return and CAGR;
- MDD, Calmar, Sortino and time under water;
- profit factor and expectancy per trade;
- turnover, fees, slippage and estimated capacity;
- worst month/year and worst stress window;
- top-1/top-5 profit contribution;
- exposure by sector and market regime;
- benchmark return and alpha;
- number of trades and confidence interval;
- methodology badge and exact `run_hash`;
- data-quality coverage and point-in-time confidence.

Frontend status must be one of: `legacy`, `execution_strict`, `point_in_time_approx`, `point_in_time_verified`, `forward_validated`. A generic green `verified` badge is too broad.

## 9. Live signal and forward validation

Every production candidate must register its signal before the outcome is known through `live_signal_tracker.py`. Save the exact feature snapshot, rank, rejected alternatives, intended order, actual tradability and hypothetical fill. Compare live shadow performance against the same-date backtest after 1/5/20/60/120/252 sessions.

No backfilled signal may be labeled live. A strategy becomes eligible for manual trading review only after a prespecified forward window and minimum sample count.

## 10. Recommended implementation sequence

1. Freeze strategy edits for one short integration window and add run/version metadata.
2. Remove duplicated frontend constants; create a single backend strategy-result registry.
3. Build the shared strict order simulator and migrate all remaining engines.
4. Build historical security/universe/share-count eligibility.
5. Re-run the entire matrix from one data snapshot.
6. Add concentration, benchmark, cost and robustness reports.
7. Reopen strategy-level feature experiments only after steps 1-6 pass.
8. Register finalists in immutable forward tracking.

## 11. Acceptance checklist for Claude

- [ ] One code path executes all compared strategies.
- [ ] No same-day close fills after close-derived signals.
- [ ] No negative cash, fractional shares or silent stale-price exits.
- [ ] Historical universe and corporate actions are handled explicitly.
- [ ] Every feature proves `available_at <= signal_at`.
- [ ] Every displayed number points to one immutable run hash.
- [ ] No contradictory strategy values remain across frontend tabs and routes.
- [ ] Six-window, continuous, walk-forward and historical holdout results are separated.
- [ ] Top-winner concentration and stressed-cost results are visible.
- [ ] Live signals are registered before outcomes and reconciled automatically.

## 12. Current decision stance

- V-GC: rejected as a production strategy pending historical-universe and forward validation. Current files contain mutually inconsistent historical claims; use the latest conservative rejection until a versioned rerun is complete.
- V-DEEP: not suitable for always-on standalone use based on strict execution results.
- V-LOWBASE: potentially useful only with a prespecified regime gate; requires point-in-time rerun.
- V4 flow momentum and other strong continuous performers: promising research candidates, not yet directly comparable until the historical universe and all engines share the same contract.

Do not restore an older headline return merely because it is larger. Preserve legacy runs for audit, mark them superseded, and show only the selected immutable run in the main Strategy Center.

## 13. Codex follow-up applied on 2026-07-13 evening

The following integration fixes were applied while Claude continued the overheat-filter research:

- Corrected V-GC run metadata from `execution_timing=same_close` to `next_open`; its simulator already queues close-D signals and fills at the next trading day's open.
- Replaced the stale V-GC `avg6 +43.5%` audit card with the current conservative state: baseline `-0.6%`, overheat-filter variant `+17.8%`, positive in 3/6 periods, research/shadow status only.
- Updated the V-GC function documentation so the superseded `+71.1%` result is explicitly identified as affected by current-universe/current-market-cap lookahead.
- Extended `GET /api/backtest/matrix` with `run_id`, engine version, git commit, signal/execution timing, market-cap mode, universe version, allocation rule, fee model, run hash, and a methodology status/warning.
- The Strategy Center matrix now shows an orange `명세 없음` marker for unversioned results and does not award a best-performance star to those cells.

Database audit at the time of this update:

- completed rows in `backtest_runs`: 2,079;
- rows in `backtest_run_specs`: 0;
- standard-period cells returned by the matrix API: 149;
- therefore all 149 currently displayed cells are `legacy_unversioned` until rerun.

Do not backfill run specs by guessing old parameters. Re-run a strategy with the current engine and exact parameters to create a trustworthy spec. V-RECOVERY currently still executes close-derived signals at the same close and correctly records `same_close`; its `+22.4%` result must remain labeled research/legacy until migrated to next-open execution and rerun. V-GC's `+17.8%` value also must not be promoted to a selected result until the six versioned runs exist in `backtest_run_specs`.
