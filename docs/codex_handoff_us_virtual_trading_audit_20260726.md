# Codex Handoff — US Data / Virtual Trading Audit (2026-07-26)

## Summary

Antigravity added broad US-stock data and US support around the virtual-trading area. I audited the DB/API/frontend path and fixed the highest-risk issues found during the review.

## What Changed

1. `scripts/ops/sync_us_daily_quotes_and_factors.py`
   - Added CLI options:
     - `--batch-size`
     - `--stale-only`
     - `--stale-before`
     - `--limit`
   - Fixed stale repair target selection. The job now detects tickers whose latest `us_price_history.date` is behind the current max date.
   - Fixed `us_factor_snapshot.as_of_date`: it now uses the actual latest downloaded price date, not the script execution date.
   - Fixed single-ticker yfinance downloads. `--limit 1 --batch-size 1` now processes one ticker correctly.
   - Fixed `INSERT OR REPLACE` data loss: the sync job now preserves/writes `market_cap`, `sector`, and `industry` into `us_factor_snapshot`.

2. `main.py`
   - Tightened `/api/us/screener/presets` defaults:
     - `min_market_cap` default: USD 5B
     - `min_price` default: USD 5
   - Added `as_of_date` to US screener rows.
   - Added a quality guard to default `trend_leaders`: `op_margin > 0`.

3. `routes/trend.py`
   - Added KR/US ticker branching for virtual holdings price refresh.
   - US tickers now read latest/previous close from `us_price_history`.
   - `/api/trend/holdings` now returns:
     - `currency`
     - `market_type`
     - `price_as_of`

4. `frontend/src/App.jsx`
   - StockEasy/virtual holding display now formats USD holdings as `$...` and KR holdings as `...원`.
   - Mixed-currency summary cards show `통화혼합` instead of adding KRW and USD together.
   - Exit/status profit display no longer hardcodes `원` for US rows.

5. New audit script
   - Added `scripts/ops/audit_us_virtual_trading_readiness.py`.
   - Writes JSON reports to `research_outputs/us_virtual_trading_audit_*.json`.
   - Checks:
     - US price coverage by latest date
     - US factor freshness by `as_of_date`
     - top stale market-cap samples
     - US positions in `peak_holding`
   - recommendations for US virtual trading safeguards

6. `routes/us_virtual_trading.py`
   - Added a dedicated US virtual-trading API instead of routing US tickers through KR-only KIS paper trading.
   - New endpoints:
     - `GET /api/us-virtual/positions`
     - `POST /api/us-virtual/order`
     - `GET /api/us-virtual/orders`
     - `GET /api/us-virtual/candidates`
   - New DB tables:
     - `us_paper_orders`
     - `us_paper_positions`
     - `us_paper_realized`
     - `us_paper_cash_ledger`
   - Default paper cash seed: `US_PAPER_INITIAL_CASH_USD`, default `$100,000`.
   - Basic US risk guards:
     - block stale price dates behind the broad US latest date
     - block missing factor snapshots
     - block buy candidates below USD 1B market cap
     - block buys below 200MA

7. `frontend/src/App.jsx`
   - US stock detail screen now includes:
     - `$10,000 가상매수`
     - `보유 전량 가상매도`
     - dedicated US virtual-trading summary panel
     - USD cash/equity/P&L display
   - The panel is intentionally USD-only. A combined KRW view should be a separate conversion layer.

## Data Repair Run

Before repair:

- `us_price_history` latest `2026-07-24`: only 1 ticker.
- Broad latest date was `2026-07-21`: 3,259 tickers.
- AAPL/MSFT/TSLA and other large caps were stale at `2026-07-21`.

After full stale-only repair:

- `us_price_history`: 3,888,447 rows / 3,668 tickers.
- Latest `2026-07-24`: 3,596 tickers.
- `us_factor_snapshot`: 3,639 tickers.
- `us_factor_snapshot` at `2026-07-24`: 3,582 tickers.
- US trend screener eligible rows after default filters: 338.
- `peak_holding` US positions: 0 currently.

Latest audit file:

- `research_outputs/us_virtual_trading_audit_20260726_230503.json`
- `research_outputs/us_virtual_trading_audit_20260726_231110.json` after dedicated US virtual-trading tables were added.

## Verification Performed

Commands:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/ops/sync_us_daily_quotes_and_factors.py main.py routes/trend.py scripts/ops/audit_us_virtual_trading_readiness.py
/Applications/stock_dashboard/venv/bin/python scripts/ops/sync_us_daily_quotes_and_factors.py --stale-only --batch-size 100
/Applications/stock_dashboard/venv/bin/python scripts/ops/sync_us_daily_quotes_and_factors.py --limit 1 --batch-size 1
/Applications/stock_dashboard/venv/bin/python scripts/ops/audit_us_virtual_trading_readiness.py
npm run build
```

US virtual smoke test:

- `POST /api/us-virtual/order` buy NVDA with `$1,000` test amount — filled 4 shares.
- `POST /api/us-virtual/order` sell NVDA 4 shares — filled.
- `GET /api/us-virtual/positions` — positions returned to 0 and cash returned to `$100,000`.

API checks:

- `GET /api/us/stocks/list?limit=5` — 200
- `GET /api/us/stocks/detail/AAPL` — 200, `as_of=2026-07-24`
- `GET /api/us/stocks/chart/AAPL?days=30` — 200
- `GET /api/us/screener/presets?preset=trend_leaders&limit=5` — 200
- `GET /api/trend/holdings` — 200, returns `currency/market_type/price_as_of`
- `GET /api/us-virtual/positions` — 200
- `GET /api/us-virtual/candidates?limit=5` — 200
- `POST /api/us-virtual/order` — 200 in smoke test

Frontend:

- `npm run build` succeeded.
- Vite still reports the existing large chunk warning. It is not introduced by this patch.

## Remaining Issues / Claude Review Items

1. `kis-trading` paper APIs are still KR-only.
   - `PaperOrderIn.stock_code` requires 6 characters.
   - `_latest_price()` uses KIS/Korean `price_history`.
   - Do not route US tickers through `/api/kis-trading/paper/order`.
   - Use `/api/us-virtual/order` for US paper trading.

2. Combined KR/US performance view is still missing.
   - US paper trading now has dedicated USD tables.
   - Next step: build a combined portfolio API that reports KRW book, USD book, FX conversion, and total KRW-equivalent equity separately.

3. Some stale US tickers remain.
   - Mostly smaller/possibly stale listings, but `SATS` remained at `2026-07-17` in the latest audit.
   - Add a stale-universe cleanup job or mark symbols as inactive if yfinance repeatedly returns no data.

4. Schedule recommendation:
   - Run `scripts/ops/sync_us_daily_quotes_and_factors.py --stale-only --batch-size 100` once per US market close.
   - Run `scripts/ops/audit_us_virtual_trading_readiness.py` after that job and alert if `latest_full_date_3000` is behind the latest US trading day.

5. Screener method review:
   - Default `trend_leaders` is now safer but still simple.
   - Next improvement: add liquidity/dollar-volume, earnings quality, drawdown, and valuation ceiling bands before feeding US candidates into virtual trading.

6. US virtual candidate scoring should be backtested.
   - `/api/us-virtual/candidates` is a live candidate feed, not yet a validated return-maximizing strategy.
   - Backtest from 2021 onward using US `us_price_history`, with same-close/next-open conventions documented.

## 2026-07-27 Codex Continuation

Implemented after the first dedicated US virtual-trading pass:

1. Scheduler wiring for daily US refresh
   - `scheduler.py`
     - Added `미국일별시세팩터수집` to `_DB_WRITE_JOBS`.
     - Registered `_loop_us_daily_quotes_and_factors` in the startup job list.
     - The job runs `scripts/ops/sync_us_daily_quotes_and_factors.py --stale-only --batch-size 100`.
     - Target schedule: every day 06:30 KST, after the US market close.
   - `collection_health.py`
     - Added `us_price` dataset contract for `us_price_history`.
     - Added `us_factor` dataset contract for `us_factor_snapshot`.
     - Mapped `미국일별시세팩터수집` to both contracts.

2. Combined KR/US virtual performance API
   - `routes/us_virtual_trading.py`
   - Added `GET /api/us-virtual/combined-summary`.
   - Combines:
     - active Korean virtual holdings from `peak_holding`
     - US paper positions/cash from `us_paper_*`
     - USD/KRW from `price_history.stock_code='USDKRW=X'`
   - Falls back to `global_macro_data.KR_USD_KRW`, then `1350` only if no local FX series exists.
   - Keeps KR and US books separated in the payload, then reports total KRW-equivalent equity.

3. Frontend integrated summary
   - `frontend/src/App.jsx`
   - US virtual trading tab now fetches `/api/us-virtual/combined-summary`.
   - Added `통합 가상매매 총괄` panel showing:
     - total KRW-equivalent equity
     - total unrealized P&L
     - Korean virtual evaluation
     - US USD equity/cash converted to KRW
     - USD/KRW rate, source, and date

Latest direct function check:

```text
combined-summary fx: price_history:USDKRW=X, 2026-07-27, 1469.4000
KR active virtual positions: 35
US active virtual positions: 5
total KRW-equivalent equity: 484,178,697
total unrealized P&L: -10,444,923
```

Latest audit:

- `research_outputs/us_virtual_trading_audit_20260727_202603.json`
- Dedicated US virtual positions: 5
- Dedicated US orders: 7
- Dedicated US cash: `$50,473.62`
- Remaining issue: 20 stale top-market-cap ticker samples remain. Keep stale-only repair active after each US close.

Verification commands run:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile routes/us_virtual_trading.py main.py scheduler.py collection_health.py
/Applications/stock_dashboard/venv/bin/python -c "from routes.us_virtual_trading import combined_virtual_summary, us_paper_positions; ..."
/Applications/stock_dashboard/venv/bin/python scripts/ops/audit_us_virtual_trading_readiness.py
npm run build
```

Claude review items:

1. Confirm the scheduler loop is active in the launched desktop service after restart or launchctl kickstart.
2. Re-run `/api/us-virtual/combined-summary` through the live server after backend restart.
3. Review whether Korean `peak_holding` should be grouped by strategy or only by the currently selected strategy in the combined summary. Current implementation includes all active KR virtual holdings.
4. Backtest `/api/us-virtual/candidates` rules before treating candidate auto-buy as a proven strategy.
5. Keep the stale US ticker cleanup policy explicit: repeatedly stale yfinance symbols should be marked inactive or excluded from candidate feeds.
