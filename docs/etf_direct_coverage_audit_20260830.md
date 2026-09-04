# ETF direct collection coverage audit (2026-08-30)

## Decision

The direct KIS collector is not a complete replacement for the current ETF
Check dataset.  It is safe as an independent positive-membership verifier and
ETF investor-flow source.  It is not safe to prove that a stock is held by no
ETF because the component endpoint returns a partial top-N list.

## Evidence

- Active ETF universe: 865
- ETF flow collection: 865 ETFs, 25,951 rows, 2026-07-14 through 2026-08-28
- Component calls: 865 succeeded, 0 failed
- Component rows: 8,689 from 454 ETFs
- Partial component snapshots: 454
- Empty or unresolved snapshots: 410
- Known domestic stocks observed directly: 762
- Legacy ETF Check positive stocks: 1,520
- Positive-stock overlap: 759
- Legacy positives missing from direct top-N lists: 761
- Direct positives recorded as zero by legacy ETF Check: 3
- Exact ETF-count matches within the overlap: 41 of 759
- Direct/legacy median ETF-count ratio: 12.8%
- Direct/legacy median estimated-amount ratio: 30.5%

The three observed legacy-zero discrepancies are 068050 (팬엔터테인먼트),
262260 (에이프로), and 408900 (스튜디오미르).

## ALT (172670)

Legacy ETF Check reports zero ETFs on 2026-08-28.  The direct top-N component
dataset also has no ALT row.  This is `not_observed_unconfirmed`, not confirmed
absence, because all 865 component lists are not complete.

## Required product semantics

- `included`: one or more direct component rows exist.
- `confirmed_not_included`: every active ETF has a complete component list and
  the stock is absent.  This verdict is currently unavailable.
- `not_observed_unconfirmed`: no direct row exists but at least one ETF list is
  partial, unresolved, or missing.
- `source_unavailable`: there is no usable snapshot status.

The table `etf_pdf_snapshot_status` now persists ETF-level evidence so an empty
component result cannot silently disappear.  The helper
`ETF_check/etf_membership_coverage.py` returns the verdict, coverage summary,
observed holdings, and the latest legacy ETF Check value together.

## Remaining dependency

A complete KRX PDF source or another official endpoint that returns every
component is required before zero-membership can be independently confirmed.
Until then the current ETF Check should remain the broad source, and the direct
source should be used for positive confirmation, discrepancy alerts, and ETF
investor flows.
