# Claude handoff: mandatory backtest core checks

Date: 2026-07-13 (Asia/Seoul)  
Scope: Strategy Center backtests, shared portfolio execution, point-in-time data, result identity, frontend verification status  
Status: **0 passed / 2 partial / 4 failed at the inspection snapshot**

## 1. Purpose

This is a blocking verification list, not a general improvement proposal. Do not mark a strategy as `strict`, `verified`, or production-ready merely because it uses next-open fills or has been rerun. All six checks below must be proven independently.

Claude may be editing these files concurrently. Re-read the current code and database before changing anything, preserve unrelated work, and replace the snapshot evidence below with a fresh result after each fix.

## 2. Snapshot summary

| ID | Mandatory check | Current result | Blocking reason |
|---|---|---|---|
| C1 | V-SECTOR actual cash, costs, and compounding | FAIL | Realized P&L accumulator is used instead of a cash ledger |
| C2 | KRW 110m permits the 11th KRW 10m ticket | PARTIAL | Generic engines expand, shared `CashPortfolio` and several strategies remain capped at 10 |
| C3 | Historical shares and delisted names are point-in-time | FAIL | Current universe/current shares remain in backtest queries |
| C4 | Every screen uses the same selected `run_hash` | FAIL | `backtest_run_specs` is empty and Strategy Center values are hard-coded |
| C5 | Strategy combinations share one KRW 100m account | FAIL | Frontend computes weighted return averages, not merged orders |
| C6 | Verification badges are derived automatically | PARTIAL | Matrix status is automatic; main strategy-card labels are hard-coded |

## 3. C1: V-SECTOR cash ledger, costs, and compounding

### Current evidence

- `backtest.py:5876-5880` initializes `capital = 0.0` as cumulative realized P&L, not KRW 100m cash.
- `backtest.py:5900-5906` adds sell P&L but does not return sale proceeds to cash or deduct tax/fees/slippage.
- `backtest.py:5908-5920` opens positions without debiting cash and uses a fixed `max_positions` check.
- `backtest.py:6191-6197` reports `capital / (per_stock * max_positions)` rather than final account equity divided by initial equity.

Next-open timing alone does not make this strict execution.

### Required implementation

1. Route V-SECTOR through the shared order/account simulator or prove behavioral equivalence with contract tests.
2. Start with exactly KRW 100m cash and keep an auditable cash ledger for every fill.
3. Debit buy gross value plus fees and credit sell proceeds net of fees/tax.
4. Apply the recorded slippage model to executable prices.
5. Mark account equity daily as `cash + tradable marked positions`.
6. Reject unaffordable orders; cash must never be negative.

### Acceptance tests

- Sum of all ledger cash movements reconciles exactly to ending cash.
- `final_equity = cash + marked_open_positions` and `total_return = final_equity / 100_000_000 - 1`.
- A zero-return buy/sell round trip produces a negative net return equal to modeled costs.
- Removing one profitable trade changes subsequent purchasing power and fills.

## 4. C2: dynamic ticket expansion

### Current evidence

- `backtest.py:3017-3027` implements an equity-based dynamic limit for the generic engine.
- V-DEEP and V-LOWBASE contain similar local logic.
- `portfolio_engine.py:10-23` still defaults to `max_positions=10` and rejects an 11th position regardless of equity.
- A direct check with KRW 110m cash bought ten KRW 10m positions and rejected the 11th, leaving KRW 10m idle.
- `scripts/test_portfolio_engine.py` does not assert 10-to-11 expansion; its passing result is therefore insufficient.

### Required contract

Use one unambiguous rule across every strategy:

```text
ticket_budget = KRW 10m
position_limit = floor(marked_equity / ticket_budget)
initial KRW 100m -> at most 10 tickets
marked equity KRW 110m -> at most 11 tickets
```

Do not silently mix this with resizing each of ten positions to 10% of equity. Record the allocation rule in the run specification.

### Acceptance tests

- At KRW 109,999,999, the 11th ticket is rejected.
- At KRW 110,000,000, the 11th ticket is eligible if cash and ranking allow it.
- Unrealized gains may expand the limit only if the recorded methodology explicitly permits it.
- A later drawdown must not force an arbitrary sale solely because the calculated limit falls.
- Run the same test against every public strategy entry point, not only `CashPortfolio`.

## 5. C3: point-in-time universe and historical shares

### Database snapshot

As of this inspection:

| Dataset | Coverage |
|---|---:|
| Current `stock_universe` six-digit names | 2,693 stocks |
| `price_history` six-digit names | 4,175 stocks |
| Historical price names absent from current universe | 1,482 stocks |
| `stock_base_info_history` | 5,389 rows / 2,706 stocks / 2026-05-08 to 2026-07-10 |
| `stock_base_info_changes` | 4,222 rows / 1,749 stocks / 2020-01-03 to 2026-07-09 |
| `corporate_action_events` | 4,232 rows / 1,750 stocks |
| Corporate-action rows with `factor_confirmed` | 14 rows |

`stock_base_info_changes` and corporate-action records exist, but backtests do not consistently consume them. `point_in_time.py` validates date contracts; it does not construct the historical tradable universe.

### Required implementation

1. Build a canonical daily/as-of security master containing listing interval, delisting interval, market, security type, ETF/ETN flag, tradability, historical shares and classification.
2. Resolve shares with `effective_from <= signal_at < effective_to`.
3. Include securities that existed on the signal date even if they are now delisted.
4. Exclude securities that were not yet listed on the signal date.
5. Handle code changes, mergers, spin-offs, preferred shares, SPAC/REIT policy, suspension and liquidation trading explicitly.
6. Make unresolved corporate-action intervals fail or downgrade the run; do not accept `review_required` rows as verified adjustments.

### Acceptance tests

- Sample at least 20 delisted names and prove their eligibility before delisting and ineligibility afterward.
- Sample at least 20 share-count changes and prove market cap uses the shares known on each date.
- Compare current-universe and historical-universe returns and publish the survivorship-bias delta.
- No point-in-time verified run may read `stock_universe.shares_issued` without an as-of resolver.

## 6. C4: immutable run identity across backend and frontend

### Current evidence

- `backtest_run_specs` currently contains 0 rows, 0 distinct hashes and 0 strategies.
- `routes/backtest.py:1054-1124` joins run specs and treats a missing hash as `legacy_unversioned`.
- `frontend/src/App.jsx:2053-2122` contains hard-coded strategy metadata, six-window returns and continuous returns.
- Therefore cards, period tables, continuous results and matrix cells can show values from different methodologies.

### Required implementation

1. Make run-spec creation mandatory before a completed run can be published.
2. Compute `run_hash` from the canonicalized methodology, parameters, engine version, source snapshot and git commit.
3. Introduce one selected-run registry keyed by strategy and report type.
4. Every frontend result must load by explicit `run_hash`; do not use `latest strategy row` as identity.
5. Retain old runs as immutable legacy/superseded records.
6. Remove `STRATEGIES`, `PERIOD_RETURNS` and `CONTINUOUS_RETURNS` as numerical sources after backend migration.

### Acceptance tests

- Clicking a strategy shows one `run_hash` shared by its card, six-window table, continuous report, methodology panel and downloadable data.
- Changing the selected run updates all those surfaces together.
- Missing or mixed hashes block comparison and show a neutral warning, never a green badge.
- API test fails if a completed/published result has no run spec.

## 7. C5: strategy combinations must use one account

### Current evidence

- `frontend/src/App.jsx:2714-2721` calculates a weighted average of strategy period returns.
- `frontend/src/App.jsx:2855-2863` compounds those blended period returns.
- This does not model duplicate stock signals, simultaneous orders, cash competition, ranking, exits, position limits or costs.

The current widget is a return-mixture illustration, not a portfolio backtest, and must not be labeled as expected account return.

### Required implementation

1. Merge timestamped candidate orders from the selected strategies.
2. Execute all candidates through one KRW 100m account and one deterministic ranking/tie-break rule.
3. Deduplicate the same stock and record which strategies contributed to the order.
4. Define allocation, strategy budget, sector cap and conflict rules for simultaneous buy/sell signals.
5. Apply shared transaction costs, cash constraints and dynamic ticket expansion.
6. Persist the combined run with its own `run_hash` and full order attribution.

### Acceptance tests

- Two strategies buying the same stock produce one position, not two invisible allocations.
- Eleven simultaneous KRW 10m orders with KRW 100m result in ten ranked fills and one explicit rejection.
- A sale releases cash before later eligible orders according to the documented event order.
- Combined return can be reproduced from the transaction ledger, not from weighted component returns.

## 8. C6: automatic verification labels and regression gates

### Current evidence

- `routes/backtest.py:1112-1123` derives matrix status from `run_hash`, but hash presence alone is not full verification.
- `frontend/src/App.jsx:2053-2074` hard-codes labels such as `엄격검증 완료`, `엄격체결`, and `엄격+as-of 검증`.
- `research_governance.validate_research_record` and `point_in_time.validate_strict_trade_contract` are not enforced in the publish path.
- The existing portfolio test requires `PYTHONPATH=.` and does not cover the 11th-ticket requirement.

### Required status model

Derive status from machine-readable gates, for example:

```text
legacy
execution_strict
point_in_time_approx
point_in_time_verified
forward_validated
```

A `run_hash` proves identity, not correctness. A green status requires the relevant contract-test report, point-in-time coverage threshold and data-quality checks to be attached to that exact hash.

### Acceptance tests

- Deliberately remove a run spec, fee model, as-of universe or contract-test artifact and prove the badge downgrades automatically.
- No strategy label in React source may assert verification as a literal string.
- CI/regression tests cover cash reconciliation, next-bar fills, integer shares, duplicate positions, dynamic slots, feature availability and stale-price exits.

## 9. Required rerun and comparison output

After C1-C6 are fixed, rerun all Strategy Center strategies from one frozen data snapshot. Provide a machine-readable artifact containing:

```text
strategy, run_hash, git_commit, data_snapshot_at,
signal_timing, execution_timing, universe_version,
initial_cash, allocation_rule, fee_model, slippage_model,
period_return, continuous_return, CAGR, MDD, turnover,
fees, rejected_orders, stale_price_exits, trade_count,
top1_profit_contribution, top5_profit_contribution,
verification_status, verification_evidence
```

Keep old headline values visible only in an audit/history view. Do not overwrite or silently relabel them.

## 10. Fresh verification commands

Run these against `/Applications/stock_dashboard/stock.db` after implementation:

```bash
sqlite3 -header -column stock.db "
SELECT COUNT(*) AS specs,
       COUNT(DISTINCT run_hash) AS hashes,
       COUNT(DISTINCT strategy) AS strategies
FROM backtest_run_specs;"

sqlite3 -header -column stock.db "
SELECT COUNT(DISTINCT p.stock_code) AS historical_not_current
FROM price_history p
LEFT JOIN stock_universe u ON u.stock_code=p.stock_code
WHERE p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
  AND u.stock_code IS NULL;"

PYTHONPATH=. python3 scripts/test_portfolio_engine.py
python3 -m py_compile backtest.py routes/backtest.py portfolio_engine.py point_in_time.py research_governance.py
npm --prefix frontend run build
```

Add targeted automated tests before declaring completion; the commands above alone do not prove the six contracts.

## 11. Claude completion report format

For each C1-C6 item report:

1. `PASS`, `PARTIAL`, or `FAIL`.
2. Exact changed files and functions.
3. Test name and output artifact.
4. Before/after result and reason for any material return change.
5. Remaining bias or approximation.
6. Exact selected `run_hash` shown in the frontend.

Do not close this handoff while any main Strategy Center card displays a result that cannot be traced to an immutable run and reconciled to a single-account ledger.
