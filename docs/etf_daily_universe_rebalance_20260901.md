# ETF daily universe and rebalance verification (2026-09-01)

## Coverage

- `865` is the validated KIS master count of ETFs listed in Korea on the date.
- It does not include ETNs or ETFs listed on overseas exchanges.
- The count is refreshed for every new trading date and is not hard-coded.
- `etf_universe_daily` pins the exact ticker/ISIN universe and hash by date.
- A retry reuses the pinned universe, so a historical date is not rewritten with a later listing set.
- An abnormal universe change above 10% is rejected before KRX PDF collection.

## Daily operation

- 21:15: refresh/pin universe and collect the complete KRX PDF for every ETF.
- 02:15: retry failed/empty PDFs using the same pinned universe.
- 23:00 and 03:00: collect supported official issuer fallbacks without disguising stale data.
- 23:30 and 03:30: validate completeness, raw-file presence, hashes, and duplicates.
- 23:40 and 03:40: compare each healthy ETF with its own latest healthy snapshot.

## Change semantics

- `added`: component newly entered an ETF.
- `removed`: component left an ETF.
- `quantity_rebalance`: component quantity changed outside a common basket scaling factor.
- `basket_rescale`: most changed components moved by the same proportional factor.
- `valuation_drift`: weight changed while component quantity did not.

Only `added`, `removed`, and `quantity_rebalance` are counted as actionable structural events.
All categories remain queryable for audit.

## 2026-09-01 verification

- Universe: 865, with 0 additions and 0 removals versus the prior pinned universe.
- KRX exact PDF: 864 successful, 1 empty, 0 request errors, 58,466 component rows.
- Exact publication was blocked because `489010` was empty at KRX.
- PLUS official fallback for `489010`: 12 components, latest effective date 2026-08-26, status `stale`.
- Per-ETF comparison: 864 ETFs.
- Added 254, removed 320, quantity rebalance 2,310.
- Basket rescale 4,445 and valuation drift 11,814 are recorded separately.
- The optimized daily comparison completes in about 1.2 seconds on the current database.
