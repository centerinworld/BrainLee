#!/usr/bin/env python3
"""Refresh KIS orderbook/restriction evidence for explicit order candidates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kis_client import kis_client  # noqa: E402
from live_trading_data import record_execution_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stock_codes", nargs="+")
    args = parser.parse_args()
    results = []
    for value in args.stock_codes:
        code = str(value).zfill(6)
        quote = kis_client.get_current_price(code) or {}
        orderbook = kis_client.get_orderbook(code) or {}
        ok = bool(quote.get("close") and orderbook.get("bid1") and orderbook.get("ask1"))
        if ok:
            record_execution_snapshot(code, quote, orderbook)
        results.append({"stock_code": code, "ok": ok})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

