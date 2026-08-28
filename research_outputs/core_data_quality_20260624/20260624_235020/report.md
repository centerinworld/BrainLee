# Core Data Quality Audit & Repair - 2026-06-24

## Scope

- SQLite DB: `/Applications/stock_dashboard/stock.db`
- Dynamic audit: all non-system tables, row counts, bad stock codes, future-dated rows
- Core audit: price, daily KRX-like prices, stock universe, financial/cashflow keys, investor flow consistency, Kiwoom market fields, repaired KRX index rows

## Applied Repairs

- No repairs applied.

## Before vs After

| Check | Before | After |
|---|---:|---:|
| `price_history_invalid_ohlcv_numeric` | 61,386 | 61,386 |
| `price_history_trade_amount_missing_repairable` | 0 | 0 |
| `price_history_invalid_repairable_from_stock_price_daily` | 0 | 0 |
| `stock_price_daily_invalid_ohlcv_numeric` | 33,470 | 33,470 |
| `stock_price_daily_invalid_repairable_from_price_history` | 0 | 0 |
| `stock_universe_invalid_ohlcv_numeric` | 1,957 | 1,957 |
| `stock_universe_trading_value_missing_repairable` | 0 | 0 |
| `financial_duplicate_keys` | 0 | 0 |
| `cashflow_duplicate_keys` | 0 | 0 |
| `financial_quarter_eq_annual_q1_q3` | 32 | 32 |
| `cashflow_quarter_eq_annual_q1_q3` | 497 | 497 |
| `investor_trading_daily_net_inconsistent` | 0 | 0 |
| `kiwoom_investor_daily_invalid_market_fields` | 2,196,898 | 2,196,898 |
| `index_invalid_ohlcv` | 0 | 0 |
| `index_rows_after_today` | 0 | 0 |

## Residual Issues

- Remaining invalid OHLC rows are not overwritten unless another table has an internally valid same-code/same-date row.
- Financial and cash-flow quarter-equals-annual cases are reported but not arithmetically overwritten; they require source-level rebuild from DART/FnGuide snapshots.
- `kiwoom_investor_daily` market-field gaps are large and should be handled by source refresh or query-time filtering, not by synthetic prices.
- `trade_amount` backfill in `price_history` is derived as `close * volume`, so it is suitable for liquidity filtering but should not be treated as official exchange turnover when official KRX turnover exists elsewhere.

## Output Files

- Summary JSON: `/Applications/stock_dashboard/research_outputs/core_data_quality_20260624/20260624_235020/summary.json`
- Table row counts: `/Applications/stock_dashboard/research_outputs/core_data_quality_20260624/20260624_235020/before/table_row_counts.csv`
- Future-dated rows: `/Applications/stock_dashboard/research_outputs/core_data_quality_20260624/20260624_235020/before/future_dated_rows.csv`
- Bad stock-code rows: `/Applications/stock_dashboard/research_outputs/core_data_quality_20260624/20260624_235020/before/bad_stock_code_rows.csv`
