# Dashboard page performance handoff (2026-08-11)

## Scope

- Account page API fan-out and query count reduction.
- Non-blocking cold-cache behavior for expensive screeners and auto candidate analysis.
- PostgreSQL-safe data-status aggregation.
- Whole-API response compression and slow-request observability.

## Changes

- `routes/portfolio.py`: replaced direct primary SQLite access, batched price/supply/valuation/short data, fixed text transaction dates, and increased short-lived response caching.
- `routes/company_intelligence.py`: portfolio comparison is cached for five minutes.
- `frontend/src/App.jsx`: company comparison loads on demand, cold analysis endpoints poll lightweight cache responses, and tab changes use `startTransition`.
- `routes/signals.py` and `main.py`: financial/triple/combo endpoints share caches and no longer perform 20-45 second calculations inside HTTP requests.
- `routes/buy_candidates.py`: auto-board generation runs once in the background and serves cached/stale data immediately.
- `routes/tenbagger.py`: PostgreSQL data status uses catalog estimates instead of full-table `COUNT(DISTINCT)` scans. Returned entries include `approximate: true`.
- `main.py`: responses over 1 KB use gzip; APIs slower than one second are logged as `[느린 API]` and expose `Server-Timing`.

## Verification baseline

- `/api/portfolio`: about 8 seconds before, 1.2-1.8 seconds cold and milliseconds warm after.
- `/api/portfolio/transactions`: HTTP 500 before, HTTP 200 after.
- `/api/signals/combo-v2`: 22 seconds before, about 0.3 seconds on a cold cache response after.
- `/api/signals/fin-screener`, `/api/dashboard/screening/triple`, `/api/buy-candidates/auto-board`: over 45 seconds or timeout before, about 0.3 seconds on cold cache responses after.
- `/api/tenbagger/data-status`: 11.6-12.9 seconds before, about 0.05 seconds after.
- `/api/quant-major-indicators/catalog`: 337 KB identity payload; about 41 KB with gzip.
- Python compilation and Vite production build passed.

## Recheck and rollback

Run `venv/bin/python scripts/audit_page_performance.py` after server startup. Cold analysis responses may initially be empty with `refreshing: true`; the frontend polls until background calculation completes.

To roll back only this performance layer, revert the files listed above and remove `scripts/audit_page_performance.py`. PostgreSQL data and schemas are not mutated by these changes.
