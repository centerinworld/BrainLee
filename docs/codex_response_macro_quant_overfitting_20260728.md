# Codex Response: Macro/Quant Overfitting Recheck (2026-07-28)

## Source Reviewed

Claude handoff reviewed:

- `/Applications/stock_dashboard/docs/claude_handoff_codex_macro_quant_overfitting_20260728.md`

Claude's critique was valid. Codex rechecked the code and data directly and
applied/confirmed the required safeguards.

## Files Checked / Updated

- `/Applications/stock_dashboard/scripts/ops/backtest_macro_indicator_candidates.py`
- `/Applications/stock_dashboard/scripts/research_macro_sector_strategy.py`
- `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.json`
- `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.md`

## Fixes / Confirmed Safeguards

### 1. Duplicate trade rows

`macro_signal_backtest_trades` had previously allowed repeated rows across
reruns. The script now:

- deletes previous macro candidate rows before rerun
- uses `INSERT OR REPLACE`
- has a unique index:
  - `(run_id, indicator_key, sector_name, stock_code, signal_period, available_date)`

Verification for final run:

```text
run_id: macro_candidate_bt_20260728_231655
rows: 3215
distinct signal rows: 3215
```

### 2. Fill-timing artifact prevention

`price_path()` now applies:

- max entry gap: 10 calendar days
- calendar upper bound for forward path:
  - `available_date + max_horizon * 2 + max_gap_days`

This prevents sparse/suspended stock price histories from silently turning a
60/120-trading-row return into a much longer calendar-period return.

Verification for final run:

```text
max entry gap: 10 days
entry gap > 10 days: 0
trade rows: 3215
```

`research_macro_sector_strategy.py` was also updated with the same
`price_path_after(...)` style guard for event outcome construction.

### 3. Train/test promotion gate

`passes()` now requires pre-2023 training evidence:

- `WALK_FORWARD_CUTOFF = 2023-01-01`
- `MIN_TRAIN_OBSERVATIONS = 5`
- training `avg_ret_60d` must be positive

This blocks single-regime candidates such as:

- `COMM_COPPER × 전력기기`
- `US_BAA_SPREAD × 금융`
- `CN_CLI_OECD × 반도체`

from being promoted only because they were observed in a favorable recent
window.

## Final Macro Candidate Rerun

Command:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/ops/backtest_macro_indicator_candidates.py --promote
```

Result:

```json
{
  "run_id": "macro_candidate_bt_20260728_231655",
  "results": 42,
  "trades": 3215,
  "passed_pairs": 9,
  "promoted": true
}
```

Final promoted macro pairs:

| indicator | sector | obs | avg60 | hit60 | PF60 | train_n | train_avg60 |
|---|---|---:|---:|---:|---:|---:|---:|
| `KR_TRADE_BALANCE` | 전력기기 | 72 | 31.90 | 79.17 | 10.73 | 18 | 10.02 |
| `KR_TRADE_BALANCE` | 반도체 | 97 | 19.77 | 69.07 | 7.61 | 24 | 13.52 |
| `KR_EXPORT` | 반도체 | 41 | 22.49 | 68.29 | 5.39 | 28 | 5.41 |
| `GLOBAL_FOOD_PRICE` | 음식료 | 50 | 12.11 | 64.00 | 5.11 | 50 | 12.11 |
| `GLOBAL_FOOD_SUGAR` | 음식료 | 47 | 11.60 | 59.57 | 4.40 | 41 | 12.82 |
| `KR_USD_KRW` | 자동차 | 348 | 9.27 | 61.21 | 3.63 | 273 | 1.20 |
| `KR_TRADE_BALANCE` | 조선/해운 | 114 | 10.48 | 64.04 | 3.57 | 24 | 10.98 |
| `US_RETAIL_SALES` | 유통 | 85 | 7.36 | 60.00 | 2.76 | 65 | 4.58 |
| `KR_EXPORT` | 조선/해운 | 57 | 5.28 | 56.14 | 2.06 | 39 | 4.14 |

Rejected examples now correctly failed:

- `COMM_COPPER × 전력기기`: train_n `0`
- `US_BAA_SPREAD × 금융`: train_n `0`
- `US_HY_SPREAD × 바이오`: train_n `0`
- `US_NFCI × 바이오`: train_n `0`

## V-MACRO-SECTOR Rerun

Command:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/research_macro_sector_strategy.py
```

Output:

- `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.json`
- `/Applications/stock_dashboard/research_outputs/macro_sector_strategy_backtest_20260728.md`

Updated data:

```text
macro_events: 1093
event_outcomes_60d: 3470
mapped_pairs: 69
static_promoted_pairs: 9
```

Key conservative results:

| strategy | total | MDD |
|---|---:|---:|
| walk_forward_top5 | +358.87% | -41.15% |
| walk_forward_top8 | +334.09% | -40.08% |
| walk_forward_top5_kospi_ma6 | +346.27% | -22.53% |
| walk_forward_top8_kospi_ma6 | +341.97% | -16.74% |

Interpretation:

- Static promoted results remain look-ahead ceiling checks.
- Walk-forward plus KOSPI MA6 is the only version worth further research.
- Still not production-ready because stock mapping is current-state and may
  contain hindsight from later collected cafe/sector knowledge.

## Codex Final Judgment

Claude's overfitting warning was correct and materially changed the candidate
set:

- macro promoted pairs reduced from 21 to 9
- duplicate trades removed
- fill timing guarded
- single-regime macro pairs blocked

Do not expose the previous 21 promoted pairs in Strategy Center.

Allowed status:

`거시지표 후보: 학습/검증 분리 1차 통과`

Not allowed:

`검증 완료된 고수익 매크로 전략`

