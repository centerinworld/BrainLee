# Remaining Data Error Fix - 2026-06-26

## Scope

User-reported remaining errors:

- Financial quarter values equal to annual values
- Cash-flow quarter values equal to annual values
- Kiwoom investor price/volume missing or abnormal values
- Valuation internal inconsistency
- Financial/cash-flow FnGuide snapshot gaps

Additional full accuracy audit errors fixed in the same pass:

- Financial Q4 anomaly rows
- Cash-flow Q4 anomaly rows

## Repairs Applied

### Primary Repair Script

Script: `scripts/ops/fix_remaining_data_errors_20260626.py`

Initial repair run: `20260626_225911`

| Item | Before | Repaired | After |
| --- | ---: | ---: | ---: |
| financial quarter revenue equal to annual | 32 | 32 | 0 |
| cash-flow quarter fields equal to annual | 2,296 field/row hits | 2,296 | 7 |
| Kiwoom investor market fields abnormal | 2,197,620 | 2,197,620 | 0 |
| valuation internal mismatch | 1,077 | 1,077 | 0 |
| FnGuide financial snapshot gap | 630 | 630 | 0 |
| FnGuide cash-flow snapshot gap | 1,822 | 1,198 inserted | 0 |

Follow-up exact cash-flow fix:

- Run id: `20260626_2301`
- Backup: `data_quality_backup_cf_q_eq_annual_second_20260626_2301`
- Repaired remaining cash-flow quarter/annual equality rows: 7

Follow-up approximate financial fix:

- Run id: `20260626_2306`
- Backup: `data_quality_backup_fin_q_approx_annual_20260626_2306`
- Repaired quarter revenue approximately equal to annual under integrity-check threshold: 22

Final no-op verification run:

- Run id: `20260626_231052`
- All six primary checks before/after: 0

### Q4 Recalculation

Command:

```bash
python3 scripts/recalculate_q4.py --years 2023 2024 2025
```

Result:

- `financial_data`: 8,901 rows recalculated
- `cash_flow_data`: 7,827 rows recalculated
- already correct: 1,593
- skipped: 14,187
- total fixed: 16,728

### Full Accuracy Q4 Anomaly Repair

Script: `scripts/ops/fix_full_accuracy_quarter_anomalies_20260626.py`

Run id: `20260626_231300`

| Item | Before | Repaired | After |
| --- | ---: | ---: | ---: |
| financial quarter anomaly | 78 | 78 | 0 |
| cash-flow quarter anomaly | 328 | 328 | 0 |

Backups:

- `data_quality_backup_fin_q4_anomaly_20260626_231300`
- `data_quality_backup_cf_q4_anomaly_20260626_231300`

## Final Verification

### Data Integrity Check

Command:

```bash
python3 scripts/ops/data_integrity_check.py
```

Result at `2026-06-26 23:13:09`:

- 검사 항목: 10
- 이슈 발견: 0
- 종합: `ALL PASS`

### Full Accuracy Audit

Command:

```bash
python3 scripts/ops/full_accuracy_audit.py
```

Result generated at `20260626_231309`:

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

Audit files:

- `/Applications/stock_dashboard/scratch/full_accuracy_audit/financial_quarter_anomaly_20260626_231309.csv`
- `/Applications/stock_dashboard/scratch/full_accuracy_audit/cashflow_quarter_anomaly_20260626_231309.csv`
- `/Applications/stock_dashboard/scratch/full_accuracy_audit/valuation_internal_mismatch_20260626_231309.csv`
- `/Applications/stock_dashboard/scratch/full_accuracy_audit/financial_snapshot_gap_20260626_231309.csv`
- `/Applications/stock_dashboard/scratch/full_accuracy_audit/cashflow_snapshot_gap_20260626_231309.csv`

Notes:

- `orphan_price`, `orphan_financial`, and `price_extreme_changes` are still emitted by the broader audit as review flags. They are not the five user-reported residual errors in this pass.
- `scripts/ops/audit_and_repair_core_data_quality_20260624.py` was started for an extra pass but did not finish in a reasonable time and was terminated. The two targeted verification scripts above completed successfully.
