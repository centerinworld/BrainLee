# Claude Handoff: C3-C6 Shared Backtest Infrastructure

Date: 2026-07-14

## Honest status

| Item | Status | Result |
|---|---|---|
| C3 point-in-time security master | PARTIAL | KOSPI/KOSDAQ listing intervals are exact for the published suite, but 11,272 share intervals remain approximate. Do not promote to PIT verified. |
| C4 immutable run identity | PASS for the selected V10 report | Six period runs are grouped under suite hash `0ffcc2caa2f1fe7f`. Other strategies remain intentionally unselected and hidden until rerun. |
| C5 merged execution simulator | PASS backend / UI intentionally blocked | One KRW 100m account, duplicate merge, sells-before-buys, dynamic tickets, strategy budgets, sector cap, fees/tax/slippage and persisted attribution are implemented. Weighted-return UI is disabled. |
| C6 automatic labels and gates | PASS | Status is machine-derived. Missing fee/spec/artifact/PIT coverage downgrades automatically. |

## C3 changes

- Added `collectors/krx_security_reference_collector.py`.
- KRX Open API current KOSPI/KOSDAQ base information is loaded with listing dates and shares.
- `FinanceDataReader:KRX-DELISTING` provides labelled listing/delisting intervals.
- Current ETF and ETN lists are explicit exclusions; Yahoo chart metadata is only a secondary product-classification fallback.
- Loaded 126,893 official KRX month-end share snapshots for 2015-2019. These remain `official_month_end_snapshot_approx`; no daily interpolation is claimed.
- `security_master.py` now resolves multiple listing intervals and historical share intervals, excludes ETF/ETN, and avoids reusing old-code share snapshots across a later relisting.
- Strategy Center policy is explicit: KOSPI/KOSDAQ only. Other markets remain in the canonical master but are not admitted by the generic strategy engine.

Current master:

- 4,175 intervals
- 1,157 ETF/ETN exclusions
- 3,018 tradable equity intervals
- 61 global approximate listing intervals, all outside the published KOSPI/KOSDAQ strategy universe
- 22,219 compressed share intervals / 3,002 stocks
- Published PIT artifact: 0 approximate listing intervals, 11,272 approximate share intervals, 0 eligible rows without shares

The public-data V3 stock issuance endpoint was identified but the configured key returns HTTP 403. It cannot be used until that service is approved for the key.

## C4 changes

- `run_registry.py` now stores immutable verification artifacts, selected reports, run sets and run-set members.
- A Strategy Center run set requires all six standard periods.
- Every member must share one source snapshot and one code fingerprint.
- `routes/backtest.py /matrix` returns one suite hash on every V10 card/cell and retains each component hash as `component_run_hash`.
- Selected suite: `0ffcc2caa2f1fe7f`, status `point_in_time_approx`.
- Six returns: `+107.42%, -18.78%, -5.29%, +10.08%, +7.58%, +53.61%`.
- The frontend no longer contains the old manual audit objects or hard-coded V12/V-RECOVERY performance blocks. Continuous and weighted-combination results stay disabled until backed by selected runs.
- Strategy Center selectors and the matrix now show only strategies returned by the selected-suite API. Unselected strategies are hidden instead of being rendered as misleading `0%` rows.

## C5 changes

- `merged_simulator.py` implements one cash account with deterministic event order.
- Same-stock signals create one position with all contributing strategies recorded.
- Sells release cash before ranked buys; same-day sell/rebuy is rejected.
- Optional `strategy_budget_weights` and `max_sector_positions` are enforced with explicit rejection reasons.
- Final persisted smoke run: `bc260cbbcf237a43`, status `execution_strict`, cash delta `0.0`.
- Full events, ledger, open attribution, capital owner and sector ownership are persisted.

## C6 changes

- Status order: `legacy` < `execution_strict` < `point_in_time_approx` < `point_in_time_verified` < `forward_validated`.
- A PIT artifact cannot override a failed execution contract. Removing the fee model downgrades a previously PIT-verified test run to legacy.
- A hash proves identity only. Green requires exact PIT coverage and its artifact on that exact hash.

## Verification

```text
PYTHONPATH=. venv/bin/python scripts/test_portfolio_engine.py
ALL PASS

PYTHONPATH=. venv/bin/python scripts/test_strict_shared_simulator.py
C3-C6 ALL PASS

frontend/npm run build
PASS

matrix suite check
6 periods, shared hash 0ffcc2caa2f1fe7f, status point_in_time_approx
```

## Claude follow-up

1. Do not label the suite green while approximate share intervals remain.
2. Obtain approval for `GetStocIssuInfoService_V3`, then backfill exact issuance dates/shares and rerun the six-period suite.
3. Rerun other Strategy Center strategies into six-period suites before selecting them. Missing selection is preferable to displaying legacy rows.
4. Build a frontend order-ledger view for combined runs only after real timestamped component candidate orders are exported. Do not restore weighted-return arithmetic.
5. Review whether KONEX should have a separate strategy policy; do not silently mix it into KOSPI/KOSDAQ results.
