#!/usr/bin/env python3
"""Repair globally contaminated price series in restart-safe small batches."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.repair_selected_price_basis import _invalid_ohlcv_codes, run

OUT = ROOT / "research_outputs" / "global_price_basis_repair_latest.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cutoff", default="2018-12-31")
    parser.add_argument("--max-batches", type=int, default=100)
    args = parser.parse_args()

    attempted: set[str] = set()
    batches: list[dict] = []
    started_at = datetime.now().isoformat(timespec="seconds")
    initial = sorted(_invalid_ohlcv_codes())
    for _ in range(max(1, args.max_batches)):
        remaining = sorted(_invalid_ohlcv_codes())
        candidates = [code for code in remaining if code not in attempted]
        if not candidates:
            break
        group = candidates[: max(1, args.batch_size)]
        payload = run(
            apply=True,
            workers=args.workers,
            only_codes=set(group),
            manual_cutoff=args.cutoff,
        )
        attempted.update(group)
        item = {
            "batch_id": payload["batch_id"],
            "target_codes": payload["target_codes"],
            "eligible_codes": payload["eligible_codes"],
            "ineligible_codes": payload["ineligible_codes"],
            "request_errors": payload["request_errors"],
        }
        batches.append(item)
        report = {
            "started_at": started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cutoff": args.cutoff,
            "initial_invalid_codes": len(initial),
            "attempted_codes": len(attempted),
            "remaining_invalid_codes": len(_invalid_ohlcv_codes()),
            "batches": batches,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(item, ensure_ascii=False), flush=True)

    final_remaining = sorted(_invalid_ohlcv_codes())
    final = {
        "started_at": started_at,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "cutoff": args.cutoff,
        "initial_invalid_codes": len(initial),
        "attempted_codes": len(attempted),
        "remaining_invalid_codes": len(final_remaining),
        "remaining_codes": final_remaining,
        "batches": batches,
    }
    OUT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in final.items() if k not in {"batches", "remaining_codes"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
