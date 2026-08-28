# Backend Data Recheck - 2026-06-27

## Summary

Rechecked backend data after the 2026-06-26 repair pass.

Final verification:

- `scripts/ops/data_integrity_check.py`: ALL PASS, 0 issues
- `scripts/ops/full_accuracy_audit.py`: core integrity issues remain 0
- `scripts/audit_all_page_data_quality.py`: OK 25 / needs_collection 2 / review 0 / missing 0

## Fixed In This Pass

### Consensus Duplicate Grain

Command:

```bash
python3 scripts/cleanup_consensus_duplicates.py
```

Result:

- before rows: 11,170
- duplicate rows detected: 8
- deleted rows: 8
- after rows: 11,162
- remaining duplicates: 0
- backup table: `consensus_targets_duplicate_backup_20260620`

### DART Coverage Partial Top-Up

Started DART top-up collectors and stopped them after confirming they were updating the DB, because a full run would take a long time.

Commands started:

```bash
python3 -m collectors.dart_material_purchase_collector --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000
python3 scripts/collect_dart_ch_extra.py --limit 10000
```

Observed row counts:

| Table | Before | After partial top-up |
| --- | ---: | ---: |
| `dart_material_purchase` | 2,652 | 2,655 |
| `dart_employee_count` | 1,247 | 1,324 |

These two remain `needs_collection` in the page audit because they are below audit thresholds.

## Final Audit Counts

### `data_integrity_check.py`

Run at `2026-06-27 09:07:33`:

- 검사 항목: 10
- 이슈 발견: 0
- 종합: ALL PASS

### `full_accuracy_audit.py`

Run id: `20260627_090723`

| Check | Count |
| --- | ---: |
| price_duplicates | 0 |
| price_invalid_ohlcv | 0 |
| valuation_internal_mismatch | 0 |
| investor_flow_missing_3y | 0 |
| financial_quarter_anomaly | 0 |
| cashflow_quarter_anomaly | 0 |
| financial_snapshot_gap | 0 |
| cashflow_snapshot_gap | 0 |

Review flags still emitted by the broad audit:

| Review flag | Count | Assessment |
| --- | ---: | --- |
| orphan_price | 1,179 | Mostly historical/special codes not present in current `stock_universe`; not a core integrity failure. |
| orphan_financial | 21 | Same class as orphan price; should be handled by reference-table policy, not by deleting source history. |
| price_extreme_changes | 3,930 | Broad 45% daily-move flag. Includes split/rights-adjustment and adjusted/unadjusted series effects. Not safe to fix by overwriting with `stock_price_daily`. |

### `audit_all_page_data_quality.py`

Run at `2026-06-27 09:07`:

- total: 27
- ok: 25
- needs_collection: 2
- unstable_or_needs_review: 0
- missing: 0

Remaining `needs_collection`:

- `dart_material_purchase`: 2,655 rows, threshold 3,000
- `dart_employee_count`: 1,324 rows, threshold 5,000

## Price Extreme Check Note

Tested repairing a subset of price extreme rows by syncing `price_history` from `stock_price_daily`.

Result:

- 69 rows were initially repairable by same-day public daily source.
- Expanding to affected stocks touched 10,656 rows.
- Audit count worsened because `price_history` and `stock_price_daily` represent different price bases in affected ranges.
- All trial price changes were rolled back from backup tables.

Conclusion:

Do not blindly overwrite `price_history` with `stock_price_daily` for daily jump flags. A proper fix needs an explicit price-basis policy:

- either keep adjusted series and add corporate-action-aware jump filtering,
- or build a separate unadjusted OHLCV table for execution/backtest fills.
