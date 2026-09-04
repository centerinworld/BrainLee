# ETF Check to direct KRX/KIS cutover

## State

- Initial mode: `legacy_validation`
- Required validation: 5 trading days
- Cutover mode: `krx_primary`
- Automatic rollback mode: `legacy_fallback` after two consecutive failed parity days
- The user-facing ETF endpoint keeps using ETF Check until cutover succeeds.

## Daily pass gates

- Direct ETF coverage: at least 99.5%
- Positive-stock membership Jaccard: at least 90%
- ETF-count difference within one: at least 80% of overlapping stocks
- Log-amount correlation: at least 95%
- Aggregate amount ratio: 85% to 115%
- Median symmetric amount error: at most 20%
- Legacy and direct snapshots must have the same trading date.

All gates must pass on five consecutive trading dates. A failed day resets the consecutive count.

## Amount normalization

The direct amount is calculated in 100-million-won units as:

`KRX VALU_AMT per CU * KIS listed shares / KIS CU quantity / 100,000,000`

This prevents comparing ETF Check total holding estimates with unscaled KRX CU values.

## Schedule

- 21:15 KRX full PDF collection
- 22:00 KIS scale/CU collection
- 23:50 parity evaluation and conditional cutover
- 02:15 KRX retry
- 02:45 scale/CU retry for the previous date
- 03:50 parity reevaluation
