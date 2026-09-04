# 2026 Q2 financial data audit and recovery

## Result

- Database: PostgreSQL primary
- Active ordinary-share universe: 2,675 stocks
- OpenDART half-year filers for `(2026.06)`: 2,508 stocks
- Verified Q2 financial rows loaded: 2,508 stocks (100% of filers)
- Missing filed stocks: 0
- Verified Q2 cash-flow rows: 2,508 stocks
- Operating cash flow present: 2,507 stocks
- CapEx present: 2,347 stocks
- Revenue present: 2,487 stocks (21 null; 17 are financial/SPAC issuers)
- Net income present: 2,499 stocks
- Balance-sheet core present: 2,505 stocks
- Duplicate stock/period/report-type groups: 0
- Full 10-rule data-integrity suite: all pass

## Root causes fixed

1. `latest_reported_quarter()` returned Q1 during August because the month comparisons were in the wrong order. The quarterly integrity run on August 21 therefore never targeted Q2.
2. The daily job called `legacy_dart_recollect.py`, whose default range ended at 2025 and only repaired `legacy_collected` rows. It was not a current-quarter collector.
3. Legacy parsing allowed repeated `ifrs-full_Equity` rows from the statement of changes in equity to overwrite the balance-sheet total equity.
4. `ifrs-full_GrossProfit` rows labelled `영업수익` were treated as a low-priority profit field instead of revenue; half-year loss labels and split continuing/discontinued profit also lacked fallbacks.
5. A separate legacy process, `scratch/backfill_h1_2026_v2_20260822.py`, and its parent shell were still running and overwrote repaired values. Both were stopped and the script is now explicitly disabled.
6. The existing 2025 Poongsan Holdings row had revenue parsed from a revenue sub-account. It was restored from the OpenDART consolidated annual statement, and the full integrity suite then passed.

## Data semantics

- `financial_data` Q2 income fields use OpenDART `thstrm_amount`, which is the standalone second-quarter amount.
- `cash_flow_data` Q2 fields use cumulative half-year amounts and are marked `value_type='cumulative'`.
- Verified rows are marked `data_source='dart_q2_verified'`.
- CFS is preferred; OFS is used only when no consolidated statement is available.

## Residual source exceptions

- `008040`: OpenDART OFS reports zero assets and liabilities while equity is non-zero.
- `352770`: OpenDART values have a balance identity difference of about 2.5% of assets.

These two source-level exceptions are retained without invented corrections. All other verified rows pass the 2% balance identity threshold.

## Reproduction

```bash
python scripts/backfill_dart_q2_financials.py --year 2026 --workers 3
python scripts/audit_q2_financial_coverage.py --year 2026 --quarter 2 \
  --output scratch/q2_financial_audit_20260822_final.json
python scripts/ops/data_integrity_check.py --out-dir scratch/q2_data_integrity_final
```

The scheduler now refreshes the filing list, runs the verified Q2 backfill, and then runs the quarterly integrity check. The daily DART recollection path also resumes any still-unverified Q2 filers before historical repair work.
