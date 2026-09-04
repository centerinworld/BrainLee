# ETF full PDF collection design

## Source order

1. KRX `MDCSTAT05001` complete Portfolio Deposit File by ETF and trading date.
2. KIS ETF master for the active ETF universe and ISIN mapping.
3. KIS top-30 component API only for discrepancy diagnostics.  It must not be
   used to confirm absence.
4. The legacy ETF Check dataset remains a comparison source until KRX full PDF
   collection has complete published snapshots.

## Data guarantees

- Every raw KRX response is retained under `ETF_check/raw_pdf/YYYYMMDD` as
  deterministic gzip JSON with a SHA-256 hash.
- All components are retained, including cash, futures, overseas assets, and
  non-six-digit identifiers.
- `etf_pdf_full_snapshot` records one status for every active ETF.
- `etf_pdf_full_component` contains normalized rows and original row JSON.
- `etf_pdf_full_publication` is written only when every active ETF has a
  non-empty successful snapshot.
- Failed and empty snapshots remain visible and are retried on the next run.
- `confirmed_not_included` is possible only for a published complete date.

## Operations

- Primary run: weekdays after the market close.
- Retry run: early next morning for snapshots that are missing, empty, or
  failed. Successful ETF files are skipped unless `--force` is supplied.
- A filesystem lock prevents overlapping runs.
- KRX login/session failure marks the run `source_unavailable`; it never
  overwrites a previously published complete snapshot.

## Current blocker

On 2026-08-30 the KRX login page returned "서비스 제공 불가능 / 일시적 접근
불안정".  The collector is implemented and fails closed, but the initial full
snapshot cannot be published until KRX authentication is available again.
