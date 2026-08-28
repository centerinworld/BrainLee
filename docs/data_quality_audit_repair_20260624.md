# Data Quality Audit & Repair - 2026-06-24

## Scope

- Database: `/Applications/stock_dashboard/stock.db`
- Audit script: `scripts/ops/audit_and_repair_core_data_quality_20260624.py`
- Final audit run: `research_outputs/core_data_quality_20260624/20260624_235555/summary.json`
- Core tables checked: `price_history`, `stock_price_daily`, `stock_universe`, `financial_data`, `cash_flow_data`, `investor_trading_daily`, `kiwoom_investor_daily`, plus all non-system tables for row counts, future-dated rows, and malformed stock codes.

## Repairs Applied

All destructive changes were backed up first. Repair history is also recorded in `data_quality_repair_log`.

| Table | Repair | Rows | Backup table |
|---|---:|---:|---|
| `price_history` | Invalid OHLCV repaired from `stock_price_daily` | 469 | `data_quality_backup_price_history_ohlcv_20260624_232721` |
| `stock_price_daily` | Invalid OHLCV repaired from `price_history` | 9,631 | `data_quality_backup_stock_price_daily_ohlcv_20260624_232721` |
| `price_history` | Missing/zero `trade_amount` backfilled as `close * volume` | 5,743,148 | `data_quality_backup_price_history_trade_amount_20260624_232721` |
| `stock_universe` | Missing `trading_value` backfilled as `close * volume` | 1 | `data_quality_backup_stock_universe_trading_value_20260624_232721` |
| `price_history` | Unofficial index rows removed: KOSDAQ150 pre-official launch and unavailable latest-day rows | 1,371 | `data_quality_backup_index_unofficial_20260624_2352` |
| `price_history` | Unrepairable invalid numeric OHLCV rows removed | 61,386 | `data_quality_backup_price_history_invalid_ohlcv_20260624_2356` |
| `stock_price_daily` | Unrepairable invalid numeric OHLCV rows removed | 33,470 | `data_quality_backup_stock_price_daily_invalid_ohlcv_20260624_2356` |
| `stock_universe` | Invalid OHLCV repaired from `price_history` or nulled when no valid source existed | 1,957 | `data_quality_backup_stock_universe_invalid_ohlcv_20260624_2358` |
| `stock_universe` | Remaining invalid OHLCV repaired from `stock_price_daily` or nulled | 24 | `data_quality_backup_stock_universe_invalid_ohlcv_20260624_2359` |
| `price_history` | Second-pass removal of unofficial 2026-06-24 index rows | 2 | `data_quality_backup_index_unofficial_20260624_2359` |

KRX official index repair was also rerun:

- `scripts/repair_krx_index_price_history.py --start 2022-01-01 --end 2024-12-31 --sleep 0`
- 2,940 index rows saved/updated across `^KS11`, `^KQ11`, `^KS200`, `^KQ150`.
- The earlier partial 2010+ repair fixed older mixed-source rows up to the remaining 2022-2024 window, which was then completed by the focused rerun.

## Final Core Audit

Final run: `research_outputs/core_data_quality_20260624/20260624_235555/summary.json`

| Check | Final count |
|---|---:|
| `price_history_invalid_ohlcv_numeric` | 0 |
| `price_history_trade_amount_missing_repairable` | 0 |
| `stock_price_daily_invalid_ohlcv_numeric` | 0 |
| `stock_universe_invalid_ohlcv_numeric` | 0 |
| `index_invalid_ohlcv` | 0 |
| `index_rows_after_today` | 0 |
| `financial_duplicate_keys` | 0 |
| `cashflow_duplicate_keys` | 0 |
| `investor_trading_daily_net_inconsistent` | 0 |
| `future_dated_table_columns` | 0 |

## Residual Issues Not Auto-Repaired

These were intentionally not overwritten because there was no sufficiently reliable replacement source in the DB.

| Issue | Count | Action |
|---|---:|---|
| `financial_quarter_eq_annual_q1_q3` | 32 | Rebuild from DART/FnGuide source snapshots; do not infer values arithmetically. |
| `cashflow_quarter_eq_annual_q1_q3` | 497 | Rebuild from DART/FnGuide source snapshots; cumulative-to-quarter conversion needs source-level validation. |
| `kiwoom_investor_daily_invalid_market_fields` | 2,196,898 | Keep investor flow rows, but do not trust `close_pric`, `acc_trde_qty`, or `acc_trde_prica`; price fields need source refresh or query-time exclusion. |
| `bad_stock_code_tables` | 27 | Mostly non-equity symbols, index/currency codes, or auxiliary tables; review before applying stock-code constraints globally. |

## Operational Rule

For future strategy discovery and backtests:

- Use `price_history` only after `open/high/low/close` validity filters.
- Treat derived `price_history.trade_amount = close * volume` as a liquidity proxy, not official KRX turnover.
- Exclude Kiwoom market fields from price/volume features unless refreshed.
- Exclude the 32 financial and 497 cash-flow residual rows until the source-level rebuild resolves them.
