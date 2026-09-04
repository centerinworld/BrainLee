# Codex handoff: strict backtests and stock evidence (2026-07-12)

## Completed

- Added individual-stock decision evidence UI (`StockDecisionEvidencePanel.jsx`).
- Added latest market-regime, explainable-signal, and live-outcome APIs in `main.py`.
- Migrated V-DEEP and V-LOWBASE away from same-day close execution.
- Orders are now generated after the daily close and filled at the next available trading-day open.
- V-LOWBASE no longer permits fractional shares.
- Both strategies use actual cash and expand the position limit as equity crosses additional KRW 10m units (KRW 100m -> 10 slots; KRW 110m -> 11 slots).
- Re-ran six market windows and the continuous 2020-03-01 through 2026-07-10 window.
- Replaced stale frontend figures for these two strategies.

## Strict results

| Strategy | Bull | Bear | Recovery | AI rally | Recent | Latest | Six-window avg | Continuous |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V-DEEP | 75.81% | -12.89% | 10.58% | -22.95% | -25.81% | -6.91% | 2.97% | 19.13% |
| V-LOWBASE | 53.51% | -20.33% | -1.52% | -1.52% | -0.18% | 0.00% | 4.99% | 96.82% |

### Generic-engine migration

The shared generic engine now deducts actual cash, rejects unaffordable orders, uses integer shares, and expands slots only when marked equity crosses another KRW 10m unit.

| Strategy | Bull | Bear | Recovery | AI rally | Recent | Latest | Continuous | Continuous MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V1 trend | 111.25% | 10.16% | 21.47% | 7.48% | 29.33% | 30.38% | 157.16% | -20.61% |
| V2 value | 66.61% | 3.37% | 9.56% | 6.39% | 11.69% | 24.03% | 89.03% | -25.34% |
| V3 financial quality | 56.75% | -0.60% | 3.87% | 7.50% | 6.20% | 50.93% | 153.08% | -13.00% |
| V4 flow momentum | 136.38% | -11.61% | 15.13% | 15.31% | 1.95% | 38.37% | 234.75% | -9.19% |
| V6 earnings explosion | 88.56% | -16.22% | 7.29% | -0.32% | 12.47% | 59.88% | 246.44% | -30.40% |
| V7 earnings acceleration | 64.14% | -11.05% | 26.05% | -0.91% | 7.20% | 55.92% | 116.57% | -25.63% |
| V8 52-week breakout | 143.32% | -10.65% | -7.69% | 9.72% | 19.87% | 51.02% | 225.88% | -16.30% |

Raw results are in `data/strict_generic_matrix_20260712.json`. Re-run with `PYTHONPATH=. python3 scripts/run_strict_generic_matrix.py`.

### Claude golden-cross changes re-audit

Claude's version added cash tracking, MDD, sector-ranking controls, compounding and as-of market-cap sensitivity options. It still bought and sold at the signal-day close and retained a fixed ten-position cap, so the prior frontend `[130.8, 58.2, 74.7, 13.9, 33.1, 116.0]` result is retired.

After next-open execution, integer shares, actual cash and dynamic slots:

| Bull | Bear | Recovery | AI rally | Recent | Latest | Six-window avg | Continuous | MDD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 65.99% | -30.42% | 8.40% | 32.80% | 42.40% | 141.73% | 43.48% | 358.79% | -37.81% |

The continuous `asof_mktcap=True` sensitivity returned 252.45% with MDD -49.30%. This is not a full point-in-time universe because currently listed stocks and current shares remain inputs. Treat V-GC as a high-risk research strategy, not a validated production strategy.

Database run IDs use `strict_deep_*_260712`, `strict_low_*_260712`, and the two `*_continuous_260712` IDs.

## Review conclusions

- V-DEEP is not suitable for always-on standalone use. Its prior headline result was materially optimistic.
- V-LOWBASE retains a positive continuous result, but its isolated bear-market loss requires a market-regime gate.
- The remaining strategies still use several older execution engines. Do not label all Strategy Center results as strict until each engine has next-bar execution, integer shares, cash constraints, and dynamic reinvestment.

## Claude verification checklist

1. Recalculate every strict run directly from `backtest_runs` and inspect sampled trades for signal date < fill date.
2. Confirm delisted/suspended names do not silently retain stale marks at forced close.
3. Confirm adjusted OHLC consistency around corporate actions before accepting extreme winners.
4. Add an explicit bull/sideways regime gate experiment for V-LOWBASE without optimizing on the six displayed windows.
5. Migrate the remaining high-priority Strategy Center engines to the same execution contract and retain old runs as legacy, not comparable results.
6. Register production signals through `live_signal_tracker.py`; do not backfill them as if they were live.
