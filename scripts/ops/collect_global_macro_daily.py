#!/usr/bin/env python3
"""Daily global macro refresh for quant and strategy signals.

This script keeps the fast-moving macro sources fresh, then refreshes the
market-quant bridge so downstream quant indicators can consume the same data.
Slower monthly/annual sources remain in their existing collectors.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def run_step(name: str, fn, *args, **kwargs) -> dict:
    started = time.time()
    try:
        records = fn(*args, **kwargs)
        return {
            "name": name,
            "status": "ok",
            "records": int(records or 0),
            "elapsed_seconds": round(time.time() - started, 2),
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "failed",
            "records": 0,
            "elapsed_seconds": round(time.time() - started, 2),
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh daily global macro data.")
    parser.add_argument("--yahoo-lookback-days", type=int, default=120)
    parser.add_argument("--fred-lookback-years", type=int, default=3)
    parser.add_argument("--include-monthly", action="store_true", help="Also run ECOS/OECD/IMF monthly-style sources.")
    args = parser.parse_args()

    from collectors.eia_oil_supply_collector import collect_eia_oil_supply
    from collectors.fred_collector import collect_fred
    from collectors.global_financial_conditions_collector import collect_global_financial_conditions
    from collectors.global_macro_event_collector import collect_global_macro_events
    from collectors.global_macro_event_reaction_collector import collect_global_macro_event_reactions
    from collectors.market_quant_bridge_collector import collect_market_quant_bridge
    from collectors.yahoo_macro_collector import collect_yahoo_macro

    steps = [
        ("yahoo_macro", collect_yahoo_macro, (args.yahoo_lookback_days,), {}),
        ("fred", collect_fred, (args.fred_lookback_years,), {}),
        ("global_financial", collect_global_financial_conditions, (3,), {}),
        ("eia_oil", collect_eia_oil_supply, (), {}),
        ("market_quant_bridge", collect_market_quant_bridge, (), {}),
        ("macro_events", collect_global_macro_events, (), {}),
        ("macro_event_reactions", collect_global_macro_event_reactions, (), {}),
    ]

    if args.include_monthly:
        from collectors.ecos_collector import collect_ecos
        from collectors.imf_weo_collector import collect_imf_weo
        from collectors.oecd_cli_collector import collect_oecd_cli

        steps.extend([
            ("ecos", collect_ecos, (10,), {}),
            ("oecd_cli", collect_oecd_cli, (5,), {}),
            ("imf_weo", collect_imf_weo, (), {}),
        ])

    results = [run_step(name, fn, *a, **kw) for name, fn, a, kw in steps]
    payload = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
        "ok": all(item["status"] == "ok" for item in results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
