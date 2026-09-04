# Tenbagger PIT validation handoff (2026-08-11)

## Final decision

The historical tenbagger logic is not production-ready. Automatic trading must
remain disabled. The old `heuristic_score >= 55` rule is an anti-signal in all
three evaluation periods, and no tested rule reaches the 15% first-alert
tenbagger precision gate.

This is a data-backed conclusion, not a discretionary stock recommendation.

Path-risk validation later added maximum adverse excursion, loss before the
eventual peak, payoff-to-pain, and days to 3x/5x/10x. Of 33 rules that passed
the original row/sector gates, only one beat the cohort baseline on both
payoff-to-pain and pre-peak loss in train, validation, and final evaluation:
20-day net supply >= KRW 1bn plus operating-profit growth >= 100%. In the final
evaluation its stock-level first alerts had 2.13% 10x precision, 16.31% 3x,
8.51% 5x, -9.6% median pre-peak loss, and 5.70 median payoff-to-pain. This is
still exploratory and is not a production promotion.

The subsequent fixed-rule calendar-year cohort test rejected annual stability.
The candidate produced a tenbagger hit in 4 of 5 years, zero in 2021, and beat
the market cohort's payoff-to-pain in only 3 of 5 years. Therefore the final
sustainable production-candidate count is zero.

An equal-weight monthly cross-sectional rank of operating-profit growth,
20-day supply, and revenue growth was also tested to remove fixed-threshold
scale sensitivity. Its top decile improved 2024 outcomes to 25.37% 3x and
11.94% 5x, but produced zero tenbagger hits in 2020 and 2021. It was rejected
for the same regime-instability reason.

## Point-in-time dataset

- PostgreSQL table: `strategy_feature_snapshot_pit_v2`
- Rows: 187,543
- Stocks: 2,691
- Labeled 24-month rows: 125,077
- Duplicate snapshot keys: 0
- Security-master interval violations: 0
- Research-ready rows after cause and price-quality exclusions: 112,100
- Durable business-backed positive rows/stocks: 241 / 55
- Delisted-stock Naver backfill: 214 stocks, 230,291 staged rows, 200,586
  previously missing rows inserted without overwriting existing prices
- Before-2019 delisted price coverage remains incomplete.

The PIT rebuild lowered the raw 24-month 10x rate from 1.505% in the old
current-listed snapshot to approximately 1.386%. This confirms that the old
dataset was optimistic because of survivorship bias.

## Confirmed research signals

All figures below are 2024 evaluation-period, stock-level first-alert results.
They are research tags only.

| Signal | Alerts | 10x | 3x | 5x | Median peak |
|---|---:|---:|---:|---:|---:|
| supply >= KRW 1bn + operating-profit growth >= 100% | 141 | 2.13% | 16.31% | 8.51% | 62.5% |
| turnover >= KRW 1bn + operating-profit growth >= 100% | 261 | 1.92% | 13.41% | 6.90% | 43.6% |
| supply >= KRW 1bn + revenue growth >= 20% | 253 | 1.58% | 20.16% | 9.49% | 61.3% |
| operating-profit growth >= 100% | 430 | 1.16% | 12.33% | 4.88% | 44.5% |
| supply >= KRW 300m + operating turnaround | 300 | 1.00% | 20.00% | 5.00% | 65.8% |

Cash-flow, dilution, inventory digestion, and CEO-buy confirmation rules were
also tested with conservative point-in-time availability. None passed the
promotion gate. Some improved one period but had zero winners or insufficient
sector breadth in another period. They must not be converted into hard filters.

## Method corrections

- Correction disclosures are excluded from historical dilution counts.
- Dilution events and insider events are deduplicated.
- CEO direct purchases are separated from general insider purchases.
- Historical market cap uses point-in-time issued-share history.
- Price labels do not forward-fill beyond a stock's last trading date.
- Windows containing extreme price jumps are excluded from clean historical
  outcome evidence.
- Every candidate now records stock-level first-alert precision, Wilson 95%
  confidence interval, 3x/5x hit rates, median peak return, winner count, and
  sector breadth.
- Every candidate also records 24-month maximum loss, loss before its eventual
  peak, payoff-to-pain, and median days to 3x/5x/10x.
- Validation and final evaluation gates require first-alert winner and sector
  breadth, preventing repeated monthly rows or one-sector concentration from
  creating false confidence.

## Reproduction

```bash
cd /Applications/stock_dashboard
venv/bin/python scripts/build_strategy_research_dataset.py \
  --snapshot-table strategy_feature_snapshot_pit_v2
venv/bin/python scripts/discover_historical_tenbagger_signals.py \
  --snapshot-table strategy_feature_snapshot_pit_v2
venv/bin/python scripts/research_tenbagger_confirmation_filters.py \
  --snapshot-table strategy_feature_snapshot_pit_v2
venv/bin/python scripts/research_tenbagger_walkforward_cohorts.py \
  --snapshot-table strategy_feature_snapshot_pit_v2
venv/bin/python scripts/verify_tenbagger_postgres.py
```

Expected final verifier state: `ok=true`, PIT duplicate rows `0`, security
interval violations `0`, `production_ready=false`,
`auto_trading_allowed=false`, and no promoted confirmation rules.

## Evidence

- `research_outputs/historical_tenbagger_signal_discovery.json`
- `research_outputs/historical_tenbagger_signal_discovery.md`
- `research_outputs/tenbagger_confirmation_filters_20260811.json`
- `research_outputs/tenbagger_confirmation_filters_20260811.md`
- `research_outputs/tenbagger_survivorship_bias_20260811.json`
- `research_outputs/tenbagger_walkforward_cohorts_20260811.json`
- `research_outputs/tenbagger_walkforward_cohorts_20260811.md`
- `research_outputs/postgres_cutover/delisted_price_backfill_latest.json`
- `research_outputs/postgres_cutover/tenbagger_verification_latest.json`

## Next valid research step

Do not add more hand-tuned filters to the reused 2024 evaluation period. The
next defensible improvement is to preserve these five signal families, collect
future outcomes without changing thresholds, and run a genuinely unseen
walk-forward evaluation. Until enough new outcomes mature, optimize ranking and
review workflow only; do not claim higher tenbagger hit probability or enable
trade execution.
