# Codex Handoff — New Quality Factor Validation 2026-07-26

## Scope

Validate the newly built DART/quality signal axes against `strategy_feature_snapshot`.

- Order contracts / recent large sales contracts
- Contract advance / contract liability growth
- Inventory-sales-order cycle
- Cash conversion quality

This is an event-study validation, not a full execution portfolio backtest.

## Validation Method

- Source DB: `stock.db`
- Snapshot table: `strategy_feature_snapshot`
- Sample cutoff: `snapshot_date <= 2025-06-30`
- Sample rows: 154,856
- Stocks: 2,605
- PIT approximation:
  - Quarterly DART-derived factors become tradable 60 days after quarter end.
  - Order-contract disclosures use actual `rcept_dt`.
- Script:
  - `/Applications/stock_dashboard/scripts/research_new_quality_factor_validation.py`
- Outputs:
  - `/Applications/stock_dashboard/research_outputs/new_quality_factor_validation_20260726.csv`
  - `/Applications/stock_dashboard/research_outputs/new_quality_factor_validation_20260726.json`
  - `/Applications/stock_dashboard/research_outputs/new_quality_factor_validation_20260726.md`

## Key Results

Baseline all snapshots:

- avg 12M forward max return: +73.33%
- median 12M: +32.07%
- 12M 3x rate: 7.27%
- -30% loss rate: 0.26%

Promising alpha candidates:

- `advance_good`
  - n=508
  - avg12 +102.94%
  - median12 +49.76%
  - 3x 13.58%
  - loss30 0.00%
  - Interpretation: small sample but clearly strong. Promote as a catalyst bonus, not a standalone full strategy yet.
- `order_recent`
  - n=6,689
  - avg12 +79.83%
  - median12 +35.64%
  - 3x 9.46%
  - loss30 0.30%
  - Interpretation: moderate alpha. Good as a confirmation bonus, especially when paired with existing model/sector filters.

Risk/exclusion candidates:

- `exclude_quality_risk`
  - avg12 +73.68% vs baseline +73.33%
  - median12 +33.04% vs baseline +32.07%
  - loss30 0.20% vs baseline 0.26%
  - PF 339.85 vs baseline 288.20
  - Interpretation: risk exclusion improves median, tail loss, and profit factor modestly.

Not alpha as currently defined:

- `cash_good`
  - avg12 +64.19%, below baseline
  - loss30 0.16%, better than baseline
  - Interpretation: defensive quality flag. It reduces downside but should not be used as a strong buy trigger.
- `inventory_good`
  - avg12 +70.74%, below baseline
  - loss30 0.66%, worse than baseline
  - Interpretation: too broad/noisy. Needs sector-specific thresholds before promotion.

Model interaction:

- `model_top_decile`: avg12 +109.96%, 3x 12.78%
- `model_top_decile_no_risk`: avg12 +111.44%, 3x 12.90%, loss30 0.57%
- `model_top_decile_quality_no_risk`: avg12 +111.82%, but 3x 12.29% and loss30 0.85%

Interpretation: removing quality-risk flags helps the model slightly. Adding broad quality-good flags does not improve the top-decile model enough to justify a hard filter.

## Strategy Center Monthly Ranking Check

After the event-study result, Codex also tested the more Strategy-Center-like use case: monthly top candidate ranking.

Monthly Top20 results:

- `monthly_top20_model`
  - avg12 +160.95%
  - median12 +28.42%
  - 3x 14.39%
  - loss30 1.21%
- `monthly_top20_quality_balanced`
  - avg12 +152.45%
  - median12 +28.18%
  - 3x 13.86%
  - loss30 1.21%
- `monthly_top20_catalyst_strong`
  - avg12 +154.22%
  - median12 +28.32%
  - 3x 14.09%
  - loss30 1.21%
- `monthly_top20_model_no_risk_pool`
  - avg12 +143.32%
  - 3x 14.17%

Monthly Top10:

- `monthly_top10_model`
  - avg12 +134.61%
  - 3x 12.27%
- `monthly_top10_quality_balanced`
  - avg12 +136.01%
  - 3x 12.12%

Conclusion:

- Simple quality/catalyst score blending does **not** improve Strategy Center Top20 ranking.
- Top10 balanced quality improves average return very slightly, but 3x rate falls, so it is not robust enough to promote.
- These indicators should be shown as supporting evidence on Strategy Center / candidate detail, not merged into the core ranking score yet.

## Follow-up Sweep: Data-Selected Auxiliary Overlay

Codex then ran a broader train/test sweep:

- Script: `/Applications/stock_dashboard/scripts/research_quality_overlay_sweep.py`
- Output:
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_sweep_20260726.json`
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_sweep_20260726.csv`
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_sweep_20260726.md`
- Train end: 2024-06-30
- Test: 2024-07-01~2025-06-30 snapshot labels
- Swept combinations: 2,880 per TopN

Result:

- Top20:
  - baseline test avg12 +424.37%, 3x 33.75%
  - best robust overlay avg12 +435.72%, 3x 35.00%
  - Interpretation: positive but incremental.
- Top10:
  - baseline test avg12 +370.97%, 3x 26.67%
  - selected robust overlay avg12 +420.79%, 3x 27.50%
  - loss30 unchanged at 0.00%

Selected auxiliary overlay:

- Predicate: `quality_risk_count == 0 OR advance_good OR order_recent`
- Score:
  - `model_score_12m`
  - `+0.10 * advance_good`
  - `+0.06 * order_recent`
  - `+0.01 * cash_good`
  - `-0.02 * inventory_good`

Interpretation:

- `order_recent` is the main useful selected overlay.
- `cash_good` can be a very small positive quality tiebreaker.
- `inventory_good` should be a small negative tiebreaker in Strategy Center ranking, because broad inventory build-up was not alpha.
- `advance_good` is still a good event-study catalyst, but in this latest Top10 sweep the chosen weight is not very sensitive to it.

Implementation:

- `/api/backtest/strategy-research/summary` now includes:
  - `quality_overlay_sweep`
  - `current_rankings.quality_overlay_top10`
- Strategy Center frontend now shows:
  - sweep result
  - current auxiliary Top5 candidates
  - explicit note that this is an auxiliary ranking, not a replacement for core ML ranking.

Ledger:

- id=101
- `STRATEGY_CENTER_QUALITY_OVERLAY_TOP10`
- verdict: `PROMOTE_AUXILIARY_TOP10_NOT_CORE_RANKING`

## Execution Backtest Override

After the label/ranking sweep, Codex ran an execution-style monthly rebalance backtest.

- Script: `/Applications/stock_dashboard/scripts/backtest_quality_overlay_monthly.py`
- Execution: next trading day's open after the monthly snapshot
- Rebalance: monthly equal weight
- Cost: 0.4% per invested month
- Outputs:
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_monthly_backtest_20260726.json`
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_monthly_backtest_20260726.md`
  - `/Applications/stock_dashboard/research_outputs/quality_overlay_monthly_backtest_summary_20260726.csv`

Critical result:

- Test 2024H2~2026:
  - Model Top10: +173.85%, MDD -13.37%
  - Overlay Top10: +30.38%, MDD -34.10%
  - Model Top20: +75.08%, MDD -15.42%
  - Overlay Top20: +6.17%, MDD -27.06%

Final verdict:

- The quality overlay **must not be promoted as a buy ranking**.
- The forward-label sweep was misleading because it used maximum forward return labels; it did not prove realizable entry/exit performance.
- Keep the signals only as explanatory catalyst/risk evidence:
  - recent order contracts
  - advance/contract liability
  - cash conversion
  - inventory caution
- Strategy Center frontend was corrected to mark the auxiliary overlay as execution-backtest failed.

Ledger:

- id=102
- `STRATEGY_CENTER_QUALITY_OVERLAY_EXECUTION_BT`
- verdict: `REJECT_EXECUTION_BACKTEST_FAILED`

## Recommended System Changes

Adopt:

- Keep `contract_advance_signals` as a positive catalyst bonus.
- Keep `order_contracts` recent material contract as a positive catalyst bonus.
- Keep inventory/cash risk flags as weak risk controls.
- Show the validation result in Strategy Center, but keep the main monthly ranking model unchanged for now.
- Promote the data-selected auxiliary Top10 overlay as a separate Strategy Center panel.

Do not adopt yet:

- Do not use `cash_good` as a direct buy signal.
- Do not use `inventory_good` as a direct buy signal across all sectors.
- Do not require `quality_good_count >= 1` as a hard filter for tenbagger candidates.
- Do not use a simple additive quality/catalyst overlay in Strategy Center Top20 ranking yet.
- Do not replace the core ML ranking with the auxiliary overlay.

Next validation required:

- Run execution-strict portfolio backtests for:
  - base model top decile
  - base model top decile + advance bonus
  - base model top decile + recent order bonus
  - base model top decile - inventory/cash risk penalty
  - combined weighted version
- Build a candidate-detail explanation layer:
  - "positive catalyst": advance/order contract
  - "risk caution": inventory/cash risk
  - "not direct buy signal": broad cash/inventory good flags
- Test by market regime and sector because inventory build-up can mean different things for manufacturing, shipbuilding, semiconductors, biotech, and retail.

## Verification

Passed:

- `python -m py_compile scripts/research_new_quality_factor_validation.py`
- `python scripts/research_new_quality_factor_validation.py`
- `python -m py_compile routes/backtest.py scripts/research_new_quality_factor_validation.py`
- `npm run build`
- `/api/backtest/strategy-research/summary` includes `quality_factor_validation`
- `/api/backtest/strategy-research/summary` includes `quality_overlay_sweep`
- `/api/backtest/strategy-research/summary` includes 10 current `quality_overlay_top10` rows

Important caveat:

- This validation uses forward maximum return labels. It proves candidate signal quality, but not realizable portfolio return. It must be followed by execution backtests with capital allocation, sell rules, slippage, and date-safe availability.
