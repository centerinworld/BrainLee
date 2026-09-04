# Codex Handoff: Strategy Barbell Combo Follow-up

Date: 2026-07-29
Owner: Claude
Scope: Strategy-center top-5 reallocation experiments using persisted `combined` merged-account runs

## What Was Implemented

- Added [`/Applications/stock_dashboard/scripts/research_strategy_barbell_combo.py`](/Applications/stock_dashboard/scripts/research_strategy_barbell_combo.py).
- Added [`/Applications/stock_dashboard/scripts/analyze_combo_edge_attribution.py`](/Applications/stock_dashboard/scripts/analyze_combo_edge_attribution.py).
- The script reads the latest immutable `trades_json` + `run_hash` for:
  - `sector_focus`
  - `se_momentum`
  - `earnings_conviction`
  - `recovery`
  - `golden_cross`
- It builds merged-account orders with [`/Applications/stock_dashboard/merged_simulator.py`](/Applications/stock_dashboard/merged_simulator.py), evaluates several allocation profiles, persists the result as `backtest_runs.strategy='combined'`, and writes:
  - [`/Applications/stock_dashboard/research_outputs/strategy_barbell_combo_20260729.json`](/Applications/stock_dashboard/research_outputs/strategy_barbell_combo_20260729.json)
  - [`/Applications/stock_dashboard/research_outputs/strategy_barbell_combo_20260729.md`](/Applications/stock_dashboard/research_outputs/strategy_barbell_combo_20260729.md)
- The attribution script compares the current best merged combo against the top-5 challenger and writes:
  - [`/Applications/stock_dashboard/research_outputs/combo_edge_attribution_20260729.json`](/Applications/stock_dashboard/research_outputs/combo_edge_attribution_20260729.json)
  - [`/Applications/stock_dashboard/research_outputs/combo_edge_attribution_20260729.md`](/Applications/stock_dashboard/research_outputs/combo_edge_attribution_20260729.md)
  - It now also performs an exact replay check from persisted `parameter_json.orders` and `config`, not just a fresh heuristic rebuild.
- `research_strategy_barbell_combo.py` now exposes explicit selection modes:
  - `--selection-mode full_range_compound`
  - `--selection-mode period_reset_robust`
  - plus `--full-range-start` / `--full-range-end` for horizon control

## Important Findings

### 1. Period-reset optimum and full-range optimum are different

- Period-reset search winner:
  - Offensive: `sector_focus 0.2 / se_momentum 0.4 / earnings_conviction 0.4`
  - Defensive: `sector_focus 0.2 / se_momentum 0.2 / earnings_conviction 0.6`
  - Persisted run: `cmb_1caca7f7f9fe`
  - Full-range result: `+274.69%`, MDD `-25.72%`

- Full-range direct search winner among tested top-5 reallocations:
  - Same offensive/defensive weights:
    - `sector_focus 0.2 / recovery 0.4 / golden_cross 0.4`
  - Profile name in JSON: `recovery_gc_balance`
  - Persisted run: `cmb_65867aa0f161`
  - Full-range result: `+357.92%`, MDD `-29.01%`

Interpretation:
- If the target is robustness across preset windows, the `se_momentum + earnings_conviction` blend looks best.
- If the target is raw long-range compounded return, `recovery + golden_cross` is materially stronger.

### 2. Top-5 reallocation can beat standalone `sector_focus`, but not the existing best merged combo

- Standalone reference:
  - `sector_focus` full-range: `+245.02%`

- Better than standalone:
  - `cmb_65867aa0f161`: `+357.92%`, MDD `-29.01%`
  - `cmb_221f27bc6ebf`: `+287.37%`, MDD `-31.41%`
  - `cmb_1caca7f7f9fe`: `+274.69%`, MDD `-25.72%`

- Still below current best merged combo:
  - `cmb_8d727d5b7a8f`: `+612.91%`, MDD `-34.64%`

Interpretation:
- Reallocating only the strategy-center top 5 helps.
- It does not replace the current best `v2 + sector_focus` family combo.

### 3. Adding a small `v2` sleeve did not improve the best top-5 winner

- Follow-up grid tested on 2026-07-29:
  - `sector_focus 0.2 / recovery 0.3 / golden_cross 0.3 / v2 0.2`
  - `sector_focus 0.2 / recovery 0.4 / golden_cross 0.2 / v2 0.2`
  - `sector_focus 0.2 / recovery 0.2 / golden_cross 0.4 / v2 0.2`
  - `sector_focus 0.2 / recovery 0.35 / golden_cross 0.35 / v2 0.1`
  - `sector_focus 0.2 / recovery 0.25 / golden_cross 0.25 / v2 0.3`
  - `sector_focus 0.2 / recovery 0.2 / golden_cross 0.2 / v2 0.4`

- Best `v2` hybrid from that sweep:
  - `sector_focus 0.2 / recovery 0.3 / golden_cross 0.3 / v2 0.2`
  - Return `+310.72%`
  - MDD `-27.60%`
  - Trades `699`

- Baseline still better on raw return:
  - `recovery_gc_balance`
  - Return `+357.92%`
  - MDD `-29.01%`
  - Trades `553`

Interpretation:
- `v2` improved diversification a bit, but in this sweep it diluted the strongest `recovery + golden_cross` edge more than it helped.
- That means the gap to `cmb_8d727d5b7a8f` is probably not solved by simply adding a small `v2` sleeve to the top-5 winner.

### 4. The current best merged combo wins more months, not just one lucky burst

- Added edge attribution comparing:
  - Baseline: `cmb_8d727d5b7a8f`
  - Challenger: `cmb_65867aa0f161`
  - Overlap window: `2020-04-29` to `2026-03-31`

- Main overlap result:
  - Baseline better months: `43 / 71`
  - Challenger better months: `28 / 71`
  - Net realized PnL edge in overlap: `+31,455,384.76` in favor of baseline

- Strategy-level realized PnL:
  - Baseline `v2`: `144,613,172.85`
  - Baseline `sector`: `250,897,369.50`
  - Challenger `golden_cross`: `283,919,786.26`
  - Challenger `recovery`: `73,996,724.06`

- Slot-competition / hold profile:
  - Baseline `v2`: avg hold `28.2d`, buy rejection rate `6.67%`
  - Baseline `sector`: avg hold `101.36d`, buy rejection rate `12.96%`
  - Challenger `golden_cross`: avg hold `99.96d`, buy rejection rate `26.84%`
  - Challenger `recovery`: avg hold `58.28d`, buy rejection rate `26.37%`

- Important pattern:
  - Challenger has a few very large spike months, especially `2026-03`.
  - Baseline is better in more months and loses less badly in several challenger drawdown windows.
  - Earlier total `rejections` counts mixed buy-side slot competition with sell-side `no_open_position` cleanup events. After splitting them, the real signal is still the same: challenger has materially worse buy-side rejection pressure, but the gap is smaller and more precise than the coarse total count suggested.
  - Narrowing further to `buy_rejections` date structure sharpened the failure mode:
    - Baseline total buy rejections: `63` across `30` active dates, HHI `0.047619`
    - Challenger total buy rejections: `201` across `159` active dates, HHI `0.0075`
    - Baseline collisions are concentrated into a smaller set of crowded sessions, while the challenger experiences low-grade slot pressure across far more dates.
    - Calendar view makes the contrast clearer:
      - Baseline top years: `2020 (24)`, `2021 (23)`; top quarters: `2020-Q3 (12)`, `2021-Q2 (11)`
      - Challenger top years: `2021 (43)`, `2023 (34)`, `2020 (33)`, `2025 (32)`; top quarters: `2021-Q4 (23)`, `2023-Q3 (16)`, `2021-Q1 (13)`, `2020-Q2 (12)`, `2025-Q2 (10)`
      - Baseline pressure is mostly an early-cycle phenomenon and is split between `sector` in 2020 and `v2` in 2021. Challenger crowding resurfaces repeatedly across multiple later regimes, especially through `golden_cross`.

Interpretation:
- The baseline edge is broader and more durable across time.
- The challenger is less diversified across market states than its lower MDD headline might suggest.

### 5. Exact replay validation is now built into the attribution path

- Added exact replay checks for both:
  - `cmb_8d727d5b7a8f`
  - `cmb_65867aa0f161`

- Result:
  - Baseline replay match: `True`
  - Challenger replay match: `True`

Why this matters:
- Earlier exploratory checks that reassembled combos from "latest strategy runs" were useful for hypothesis testing, but they were not the same thing as replaying the persisted combined run itself.
- The attribution report now proves that its baseline and challenger summaries match the saved combined-run spec exactly before making any interpretation.

### 6. The top-5 winner degrades sharply when extended to the latest common horizon

- Re-tested `recovery_gc_balance` with the latest common component runs ending `2026-07-28`:
  - `sector_focus`: `7fe25081`
  - `recovery`: `2581711b`
  - `golden_cross`: `9b608e80`

- Result on the extended horizon:
  - Return `+237.13%`
  - MDD `-46.85%`
  - Trades `540`
  - Rejections `522`

Interpretation:
- The earlier `+357.92% / -29.01%` result was not stable when recent months were included.
- This is the strongest evidence so far that top-5 reallocation alone is not the right replacement path for the current best merged combo.

### 7. Strategy budget caps did not rescue the latest-horizon variants

- Confirmed the current best merged combo uses the same latest source hashes that are now persisted on 2026-07-28:
  - `v2` run hash `69000dbf4ebe`
  - `sector_focus` run hash `95e8273a8c96`

- Tested whether adding `recovery` / `golden_cross` back with explicit `strategy_budget_weights` could reduce slot crowding:
  - `v2 + sector + recovery` with small recovery cap
  - `v2 + sector + golden_cross` with small golden-cross cap
  - `v2 + sector + recovery + golden_cross` with tiny or micro caps

- Result:
  - None of these budget-capped hybrids beat the plain latest-source `v2 + sector` baseline.
  - They reduced drawdown somewhat, but returns fell much more and rejected orders exploded into four digits.

Interpretation:
- The issue is not just "long-hold strategies need less capital."
- Once added, they still consume scarce opportunity flow and increase rejection pressure enough to damage the backbone combo.

### 8. Early failure mode already identified

- The first version of the script used:
  - `max_positions=10`
  - `max_sector_positions=4`
- That produced `cmb_61f71382c8ab` at only `+111.60%`, MDD `-21.83%`.
- Relaxing to:
  - `max_positions=20`
  - no sector cap
- was necessary to get meaningful merged returns.

This matters because future experiments that reintroduce tight caps may look safer while silently crushing return.

## Current Best Candidates From This Work

### A. Best raw return inside strategy-center top-5 universe

- Run: `cmb_65867aa0f161`
- Weights:
  - Offensive: `sector_focus 0.2 / recovery 0.4 / golden_cross 0.4`
  - Defensive: same
- Result:
  - Return `+357.92%`
  - MDD `-29.01%`
  - Trades `553`

### B. Better drawdown-adjusted compromise

- Run: `cmb_1caca7f7f9fe`
- Weights:
  - Offensive: `sector_focus 0.2 / se_momentum 0.4 / earnings_conviction 0.4`
  - Defensive: `sector_focus 0.2 / se_momentum 0.2 / earnings_conviction 0.6`
- Result:
  - Return `+274.69%`
  - MDD `-25.72%`
  - Trades `1190`

## Recommended Next Work For Codex

### Priority 1: explain why `cmb_8d727d5b7a8f` is still so much stronger

Goal:
- Break down the edge of `cmb_8d727d5b7a8f` versus `cmb_65867aa0f161`.

What to compare:
- Exposure concentration by calendar period
- Average holding overlap
- Rejection counts from slot competition
- Strategy contribution by month or quarter
- Whether `v2` is supplying the early-cycle entries that top-5 strategy-center set misses

Suggested output:
- `research_outputs/combo_edge_attribution_20260729.json/md`

Status:
- Basic attribution is now done.
- Next step is deeper attribution inside the baseline itself: entry timing, holding period, and buy-side slot-competition structure specifically.
- The next narrow question is no longer "why are total rejections higher?" but "which calendar regimes let `v2 + sector_focus` absorb concentrated buy pressure while `recovery + golden_cross` stays persistently crowded?"
- Current evidence already suggests the baseline survives by containing crowding to two early regime clusters (`2020-Q3`, `2021-Q2`), while the challenger keeps re-entering crowding clusters even in later windows (`2023-Q3`, `2025-Q2`).

### Priority 2: explain the missing edge instead of adding more sleeves blindly

Why:
- The first `v2` sleeve sweep already failed to beat `recovery_gc_balance`.
- Extending `recovery_gc_balance` to the latest common horizon also degraded badly.
- More weight-mixing without attribution is likely to become random search.

What to inspect next:
- Which months or quarters `cmb_8d727d5b7a8f` wins by the most
- Whether `v2` contributes earlier entries, longer holds, or lower rejection pressure
- Whether the advantage comes from trade timing, breadth, or capital slot competition
- Which exact buy-reject dates are caused mainly by `sector_focus` spikes versus `v2` spikes, and whether challenger crowding clusters around specific market states or remains diffuse

### Priority 3: separate objective functions explicitly in code

Problem:
- The same search surface is serving two different goals:
  - preset-window robustness
  - full-range compounded return

Suggested code change:
- In [`/Applications/stock_dashboard/scripts/research_strategy_barbell_combo.py`](/Applications/stock_dashboard/scripts/research_strategy_barbell_combo.py), add two named modes:
  - `period_reset_robust`
  - `full_range_compound`

Status:
- Implemented.
- The next improvement is to split the output artifact itself into separate report sections or separate files per mode so the conclusions are less mixed.

## Notes

- The script output currently prints the selected `weights_by_bucket` and selected full-range summary correctly after the last patch.
- `selected_profile` in the JSON is now the reliable source of truth.
- No frontend or API selection logic was changed in this work. This is still research-state only.
