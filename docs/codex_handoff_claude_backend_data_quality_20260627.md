# Claude Handoff: Backend Data Quality Follow-up - 2026-06-27

## Context

Codex rechecked backend data quality after repairing the 2026-06-26 residual errors.

The following high-priority integrity issues are already fixed and should stay at zero:

- financial quarter value equal/near-equal to annual
- cash-flow quarter value equal to annual
- Kiwoom investor abnormal price/volume/trade amount
- valuation internal mismatch
- FnGuide financial/cash-flow snapshot gaps
- financial/cash-flow Q4 anomaly rows
- consensus duplicate grain

Current verification state:

```bash
python3 scripts/ops/data_integrity_check.py
# ALL PASS, 0 issues

python3 scripts/ops/full_accuracy_audit.py
# core integrity counts are 0

python3 scripts/audit_all_page_data_quality.py
# total 27 / ok 25 / needs_collection 2 / review 0 / missing 0
```

Reference docs:

- `docs/remaining_data_error_fix_20260626.md`
- `docs/backend_data_recheck_20260627.md`

## Files Added Or Modified By Codex

Scripts:

- `scripts/ops/fix_remaining_data_errors_20260626.py`
- `scripts/ops/fix_full_accuracy_quarter_anomalies_20260626.py`
- `scripts/ops/full_accuracy_audit.py`
- `scripts/cleanup_consensus_duplicates.py`

Docs:

- `docs/remaining_data_error_fix_20260626.md`
- `docs/backend_data_recheck_20260627.md`
- this handoff doc

## Already Fixed

### 1. Residual Financial/Cash-flow/Kiwoom/Valuation/Snapshot Errors

Primary repair run:

- script: `scripts/ops/fix_remaining_data_errors_20260626.py`
- run id: `20260626_225911`

Final no-op verification run:

- run id: `20260626_231052`
- all primary checks before/after: 0

Important backup tables:

- `data_quality_backup_fin_q_eq_annual_20260626_225911`
- `data_quality_backup_cf_q_eq_annual_20260626_225911`
- `data_quality_backup_kiwoom_market_fields_20260626_225911`
- `data_quality_backup_stock_universe_valuation_20260626_225911`
- `data_quality_backup_missing_fnguide_fin_snapshot_20260626_225911`
- `data_quality_backup_missing_fnguide_cf_snapshot_20260626_225911`
- `data_quality_backup_cf_q_eq_annual_second_20260626_2301`
- `data_quality_backup_fin_q_approx_annual_20260626_2306`

### 2. Q4 Anomaly Rows

Repair script:

```bash
python3 scripts/ops/fix_full_accuracy_quarter_anomalies_20260626.py
```

Run id: `20260626_231300`

Result:

- financial quarter anomaly: 78 -> 0
- cash-flow quarter anomaly: 328 -> 0

Backups:

- `data_quality_backup_fin_q4_anomaly_20260626_231300`
- `data_quality_backup_cf_q4_anomaly_20260626_231300`

### 3. Consensus Duplicate Grain

Repair script:

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

## Remaining Work For Claude

### A. Complete DART Coverage Backfill

Current `audit_all_page_data_quality.py` still reports two `needs_collection` items:

| Table | Current rows after partial top-up | Audit threshold | Current status |
| --- | ---: | ---: | --- |
| `dart_material_purchase` | 2,655 | 3,000 | needs_collection |
| `dart_employee_count` | 1,324 | 5,000 | needs_collection |

Codex started these commands and confirmed rows were increasing, but stopped them because a full run is long:

```bash
python3 -m collectors.dart_material_purchase_collector --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000
python3 scripts/collect_dart_ch_extra.py --limit 10000
```

Claude should:

1. Run these collectors to completion, preferably one at a time to reduce API/DB contention.
2. Track row counts before/after:

```sql
SELECT COUNT(*), COUNT(DISTINCT stock_code), MIN(year), MAX(year)
FROM dart_material_purchase;

SELECT COUNT(*), COUNT(DISTINCT stock_code), MIN(year), MAX(year)
FROM dart_employee_count;
```

3. Re-run:

```bash
python3 scripts/audit_all_page_data_quality.py
```

Success criteria:

- `needs_collection` becomes 0, or
- if DART source genuinely has no more records, document the source limitation and update the audit threshold/status logic so it does not falsely flag completed collection.

### B. Define A Price Basis Policy Before Touching `price_history`

`full_accuracy_audit.py` still emits:

- `price_extreme_changes`: 3,930 rows

Important: Codex tested repairing these by syncing `price_history` from `stock_price_daily`. This made the audit count worse and was rolled back.

Reason:

- `price_history` and `stock_price_daily` appear to represent different price bases in affected ranges.
- Some rows look like adjusted-price series, while `stock_price_daily` is closer to unadjusted public daily OHLCV.
- Simple overwrites create a mixed adjusted/unadjusted time series and can corrupt backtests.

Backup/rollback log:

- trial backup: `data_quality_backup_price_outlier_public_daily_20260627_090045`
- broader trial backup: `data_quality_backup_price_public_sync_20260627_090612`
- rollback log repair name: `rollback_public_daily_price_sync_mixed_adjustment_series`

Claude should not blindly overwrite `price_history` with `stock_price_daily`.

Recommended options:

1. Keep `price_history` as adjusted-price series and change `full_accuracy_audit.py` to distinguish corporate-action/adjusted-series jumps from true data errors.
2. Build a separate unadjusted execution OHLCV table from `stock_price_daily` or KRX/KIS for fill-price simulation.
3. Add a `price_basis` or source metadata field/table so strategies explicitly choose adjusted vs unadjusted prices.

Minimum diagnostic queries:

```sql
-- Current broad jump count used by full_accuracy_audit.py
WITH d AS (
  SELECT stock_code, date, close,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) prev_close
  FROM price_history
)
SELECT COUNT(*)
FROM d
WHERE prev_close > 0
  AND ABS((close - prev_close) / prev_close) >= 0.45;
```

```sql
-- Same-day disagreement with public daily source, sampled by stock
SELECT ph.stock_code, COUNT(*) AS diff_rows,
       MIN(ph.date) AS min_date, MAX(ph.date) AS max_date
FROM price_history ph
JOIN stock_price_daily sp
  ON sp.stock_code = ph.stock_code
 AND sp.bas_dt = REPLACE(substr(ph.date, 1, 10), '-', '')
WHERE ph.close > 0
  AND sp.close_price > 0
  AND ABS(sp.close_price - ph.close) / MAX(ABS(ph.close), 1) > 0.05
GROUP BY ph.stock_code
ORDER BY diff_rows DESC
LIMIT 50;
```

Success criteria:

- Do not force `price_extreme_changes` to 0 by corrupting the series.
- Either:
  - classify expected corporate-action/adjusted jumps separately and leave only true unexplained errors, or
  - create a clean unadjusted table and update backtests/execution-price logic to use that table where appropriate.

### C. Decide Orphan Reference Policy

`full_accuracy_audit.py` still emits review flags:

- `orphan_price`: 1,179
- `orphan_financial`: 21

Assessment from Codex:

- These are mostly historical/special codes not present in current `stock_universe`.
- This should not be fixed by deleting source history.
- It needs a reference policy: either maintain a historical/security master table or change the audit to join against an all-time universe/security map.

Claude should:

1. Inspect top orphan codes:

```bash
head -30 scratch/full_accuracy_audit/orphan_price_*.csv
head -30 scratch/full_accuracy_audit/orphan_financial_*.csv
```

2. Decide whether to:
   - add a historical `security_master` table,
   - preserve delisted/preferred/ETF history with explicit type metadata,
   - or adjust the audit to only flag orphan rows inside the active tradable universe.

Success criteria:

- No valid historical records are deleted merely because they are not in current `stock_universe`.
- Audit output distinguishes “true orphan bad FK” from “historical/non-active security”.

## Do Not Regress These Checks

After any Claude changes, run all of:

```bash
python3 scripts/ops/data_integrity_check.py
python3 scripts/ops/full_accuracy_audit.py
python3 scripts/audit_all_page_data_quality.py
```

Expected minimum:

- `data_integrity_check.py`: ALL PASS
- `valuation_internal_mismatch`: 0
- `investor_flow_missing_3y`: 0
- `financial_quarter_anomaly`: 0
- `cashflow_quarter_anomaly`: 0
- `financial_snapshot_gap`: 0
- `cashflow_snapshot_gap`: 0
- `price_duplicates`: 0
- `price_invalid_ohlcv`: 0
- `consensus_targets` duplicate grain: 0

## Operational Notes

- Backend server is expected to be running through `scripts/serve_foreground.sh`.
- Long DART collectors may hold DB locks. Run collectors one at a time if lock contention appears.
- Preserve all existing backup tables until Claude’s fixes are verified.
- Do not use destructive git/database commands.
- If modifying audit logic, document whether the change is a real repair or a reclassification of a review flag.
