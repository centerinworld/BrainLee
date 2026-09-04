# Codex Handoff — Macro Quant Scheduler + Strategy Candidate Check (2026-07-28)

## What changed
- Added `scripts/ops/collect_global_macro_daily.py`.
  - Runs fast-moving global macro sources: Yahoo macro, FRED, global financial conditions, EIA oil supply, market-quant bridge, macro events, event reactions.
  - Manual verification at 2026-07-28 22:15 KST succeeded:
    - `yahoo_macro` 552 rows
    - `fred` 289 rows
    - `global_financial` 5,055 rows
    - `eia_oil` 4,572 rows
    - `market_quant_bridge` 31,332 rows
    - `macro_events` 30 rows
    - `macro_event_reactions` 240 rows

- Updated `scheduler.py`.
  - Added daily `글로벌매크로수집` at 06:45.
  - Added daily `퀀트주요지표일일` at 19:35.
  - Added weekly Monday `거시지표백테스트` at 07:50 with `--promote`.

- Updated `scripts/ops/quant_indicators_cron.py`.
  - `--mode daily` now runs `collect_global_macro_quant_bridge`, so `global_macro_data` flows into the Quant Major Indicators menu every day.

- Updated `collection_health.py`.
  - Added `global_macro_fast` dataset contract for fast daily macro indicators.
  - Added `quant_macro_bridge` dataset contract for `macro:*` indicators in `quant_major_indicator_series`.
  - Both contracts were healthy after the manual run.

## Verification
- `py_compile` passed for:
  - `scheduler.py`
  - `collection_health.py`
  - `scripts/ops/collect_global_macro_daily.py`
  - `scripts/ops/quant_indicators_cron.py`
  - `scripts/ops/backtest_macro_indicator_candidates.py`

- Quant daily manual run succeeded:
  - Market breadth, 52-week breadth, short balance, BOK base rate, global macro bridge, DART casino, Paradise segment drop all completed.
  - `macro:*` bridge upserted 97 macro indicators into Quant Major.

- Route-function verification:
  - `/api/quant-major-indicators/catalog?status=ready_existing` equivalent returned 223 ready indicators.
  - 97 of them are `macro:*`.
  - `macro:COMM_COPPER` returned latest values through 2026-07-28.

- Macro candidate backtest:
  - Run id: `macro_candidate_bt_20260728_221712`
  - 43 indicator-sector combinations evaluated.
  - 4,697 trade observations.
  - 21 combinations passed and were promoted.

## High-signal promoted candidates to review
- `macro:KR_TRADE_BALANCE` × 전력기기: avg 60d +31.9%, hit rate 79.2%, PF 10.73.
- `macro:CN_CLI_OECD` × 반도체: avg 60d +30.25%, hit rate 65.0%, PF 6.90.
- `macro:COMM_COPPER` × 전력기기: avg 60d +29.51%, hit rate 86.4%, PF 26.79.
- `macro:US_NFCI` × 바이오: avg 60d +24.0%, hit rate 62.7%, PF 10.53.
- `macro:KR_EXPORT` × 반도체: avg 60d +22.49%, hit rate 68.3%, PF 5.39.

## Current data status checked
- HS monthly confirmed data:
  - `customs_monthly_record`: 1,342,434 rows.
  - Latest confirmed period: 2026-06.
  - 2026-01 through 2026-06 all have about 10k rows each.

- Order contract/backlog:
  - `order_contracts`: 10,049 rows, latest `rcept_dt` 2026-07-28.
  - `order_backlog`: 13,346 rows, latest 2026-Q1.

## Follow-up checks for Claude
- Confirm whether promoted macro pairs should be shown explicitly in Strategy Center, or only used as auxiliary filters in `quant_indicator_signal_engine.py`.
- Review possible look-ahead in monthly macro availability. Current backtest uses conservative monthly availability rules, but source-specific publication lag can be tightened further.
- The local API server was not running during Codex verification, so HTTP `curl` checks failed with connection refused. Route functions and DB-backed payloads were verified directly.
