# Codex Handoff — V-MACRO-SECTOR Strategy Research (2026-07-28)

## User question
User asked whether Strategy Center is truly out of improvement ideas after adding macro, HS, order backlog, contract, and quant indicators. Claude reportedly said further return improvement is unlikely.

Codex view: simple parameter squeezing of existing strategies may be near exhaustion, but recently added indicators are not fully converted into Strategy Center logic. I built and backtested a new candidate strategy: `V-MACRO-SECTOR`.

## Files
- Repro script: `/Applications/stock_dashboard/scripts/research_macro_sector_strategy.py`
- JSON output: `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.json`
- Markdown output: `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.md`

## Strategy logic
`V-MACRO-SECTOR` uses macro/quant indicators as a sector timing engine.

1. Generate green macro signals from `quant_major_indicator_series` using the existing `classify_signal` logic.
2. Interpret each signal using `indicator_sector_direction_rules`.
   - Example: `COMM_COPPER` higher is good for `전력기기`.
   - Example: `US_NFCI` higher is bad for `바이오`, so falling/benign financial conditions are treated as green.
3. Connect the signal to stocks through `cafe_stock_indicator_mappings` and `cafe_quant_indicator_mappings`.
4. Candidate ranking score uses:
   - macro signal strength
   - prior pair performance
   - pair hit rate/profit factor
   - stock mapping confidence
   - exposure fields when available
   - 60d/120d price momentum
   - liquidity gate: recent 20-day turnover >= 20억원
   - crash gate: 60d return must not be below -25%
5. Rebalance monthly:
   - buy at first open of month
   - sell at month-end close
   - transaction cost: 0.4% monthly round-trip assumption
6. Two validation modes:
   - `static_promoted`: uses latest promoted macro pairs. This is only a ceiling check because pair selection has look-ahead.
   - `walk_forward`: at each rebalance, a pair is eligible only if prior 60-trading-day outcomes already passed promotion criteria. This is the useful version.

## Backtest summary
Period: 2020-03 through 2026-06, 76 monthly rebalances.

| Variant | Total Return | MDD | Win Month | Note |
|---|---:|---:|---:|---|
| static_promoted_top3 | +1301.87% | -41.78% | 51.32% | ceiling only, look-ahead pair selection |
| static_promoted_top5 | +1229.72% | -44.51% | 56.58% | ceiling only |
| walk_forward_top3 | +358.23% | -52.28% | 51.32% | high drawdown |
| walk_forward_top5 | +394.92% | -46.20% | 56.58% | aggressive |
| walk_forward_top8 | +406.07% | -43.20% | 56.58% | best aggressive candidate |
| walk_forward_top10 | +302.82% | -41.49% | 57.89% | diluted |
| walk_forward_top5_kospi_ma6 | +286.59% | -24.55% | 34.21% | defensive |
| walk_forward_top8_kospi_ma6 | +311.61% | -21.54% | 35.53% | best risk-adjusted candidate |
| walk_forward_top10_kospi_ma6 | +226.06% | -21.15% | 36.84% | defensive, diluted |

## Interpretation
This is not proof-ready production logic yet, but it is not a dead end.

The most promising forms:
- Aggressive: `walk_forward_top8`, +406.07%, MDD -43.20%.
- Defensive: `walk_forward_top8_kospi_ma6`, +311.61%, MDD -21.54%.

The defensive version is more suitable for Strategy Center candidate display because it improves survivability. The aggressive version can be shown as a high-risk research strategy.

## Important caveats for Claude to verify
1. Stock mapping look-ahead:
   - Current `cafe_stock_indicator_mappings` may reflect knowledge collected after historical months. The walk-forward pair eligibility is time-safe, but the stock universe mapping itself is probably current-state mapped.
   - Need historical/as-of mapping if available, or mark as `mapping_current_state_bias`.

2. Macro availability lag:
   - `parse_period_available_date` is conservative but generic.
   - Monthly macro sources need source-specific publication lags before final promotion.

3. Price execution:
   - Uses monthly first open to month-end close.
   - No intramonth stop/trailing logic yet.
   - Need compare against existing shared simulator conventions if Strategy Center requires daily position ledger.

4. Liquidity:
   - 20억원 turnover gate is applied from prior data.
   - Need verify small-cap candidates do not create unrealistic fills.

5. Current result should be treated as `research_candidate`, not a selected production strategy, until:
   - as-of stock mapping is addressed,
   - source-specific macro availability lags are added,
   - same framework benchmark comparison is completed,
   - frontend disclosure labels are added.

## Recommended next implementation
Add to Strategy Center as a research tab, not as a live recommendation yet:

- `V-MACRO-SECTOR 공격형`
  - use `walk_forward_top8`
  - label: high return/high MDD

- `V-MACRO-SECTOR 방어형`
  - use `walk_forward_top8_kospi_ma6`
  - label: better risk control

## Prompt for Claude
Please verify Codex's new `V-MACRO-SECTOR` research:

1. Review `/Applications/stock_dashboard/scripts/research_macro_sector_strategy.py`.
2. Re-run `/Applications/stock_dashboard/venv/bin/python /Applications/stock_dashboard/scripts/research_macro_sector_strategy.py`.
3. Check whether the walk-forward pair eligibility truly avoids pair-selection look-ahead.
4. Audit current-state stock mapping bias from `cafe_stock_indicator_mappings`.
5. Check macro publication lag assumptions in `parse_period_available_date`.
6. Compare `walk_forward_top8` and `walk_forward_top8_kospi_ma6` against existing Strategy Center selected runs under the same execution model.
7. If acceptable, add the two variants as Strategy Center research candidates with clear methodology warnings, not production recommendations.
