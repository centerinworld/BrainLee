# DART API Key Rotation Policy (2026-06-20)

## User-confirmed operating rule

- The project has three DART API keys configured:
  - `DART_API_KEY`
  - `DART_API_KEY2`
  - `DART_API_KEY3`
- Do not ask again whether multiple DART keys exist.
- DART collectors should use all configured keys through rotation/failover.
- Never hard-code DART key values in source files.
- If one key returns DART status `020` or a quota/limit message, mark that key exhausted for the run and continue with the next configured key.

## Implementation

- Shared helper: `dart_key_manager.py`
  - `get_dart_api_keys()`
  - `RotatingOpenDartReader`
  - `DartKeyRotator`
- `config.py` exposes `DART_API_KEYS` as a de-duplicated list.

## Updated collectors

- `collect_dart_financial_batch.py`
- `data_collector.py`
- `collectors/dart_material_purchase_collector.py`
- `collectors/dart_backlog_collector.py`
- `scripts/collect_inventory_from_dart.py`
- `scripts/collect_dart_segment_breakdown.py`
- `scripts/collect_dart_ch_data.py`
- `scripts/collect_dart_ch_extra.py`

## Notes for Claude/Codex handoff

- Treat single-key DART usage as a bug unless the script is archived or intentionally one-off.
- Logs may show masked key labels only, such as `KEY1(...abcd)`. Never print full key values.
- For high-volume collection, prefer request-based collectors using `DartKeyRotator` or `OpenDartReader` collectors using `RotatingOpenDartReader`.
