#!/usr/bin/env python3
"""Comprehensive full-history price_history vs Naver comparison, all KR common stocks.

Earlier targeted fixes this session (2020-2021 window, unresolved_active_common's
flagged jump dates) repeatedly ran into the same problem: fixing only the
boundary day of a corrupted run leaves the rest of the run untouched, because
consecutive equally-wrong days don't trigger a day-over-day jump detector.
Confirmed again after the 20260918 unresolved_active_common fix: 000040's
2014-12-30 is now correct but 2014-12-26/29 (not flagged by any jump detector)
are still wrong.

Rather than continue chasing individual ripples, this does a single
comprehensive close-price comparison across ALL stock/date pairs where both
price_history and a Naver full-history snapshot
(research_outputs/price_snapshot_repair/20260911T200447/{code}.json.gz) have
data - no jump-detection pre-filter, so it also catches "quiet" corrupted runs
that a ratio-based scanner would miss entirely.

Applies the same exclusion methodology validated repeatedly this session:
  - skip if ratio matches a clean split fraction (2x/3x/.../100x or reciprocal,
    +/-3%) - likely a real adjustment-basis difference, not corruption
  - skip if there's a corporate_action_events row within +/-3 days

Threshold: a flat >2% deviation was tried first and produced ~1.1M candidates
in the first 1,000 stocks alone (extrapolates to several million) - almost
entirely noise from minor dividend-adjustment/rounding differences between
providers accumulated over 10+ years of daily data, nothing like the 10x/17x/
500x-magnitude corruption found elsewhere this session. Raised to >15% to
keep the signal-to-noise ratio usable while still catching genuine large-scale
corruption (every confirmed case this session was >30% off, most >2x).

Output is a candidate list only (this script does not write to price_history).
Given the O(10M) scale, this streams per-stock rather than loading everything
into memory at once, and is meant to be run in the background.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

SNAP_DIR = ROOT / "research_outputs" / "price_snapshot_repair" / "20260911T200447"
OUT_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "full_naver_scan_mismatches_20260918.json"

CLEAN_FRACTIONS = [2, 3, 4, 5, 6, 8, 10, 20, 25, 50, 100,
                   1/2, 1/3, 1/4, 1/5, 1/6, 1/8, 1/10, 1/20, 1/25, 1/50, 1/100]


def is_clean(ratio: float) -> bool:
    return any(abs(ratio / f - 1) < 0.03 for f in CLEAN_FRACTIONS)


def main() -> None:
    conn = connect_primary_db(timeout=600)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM price_history WHERE stock_code ~ '^[0-9A-Z]{6}$' ORDER BY stock_code"
    ).fetchall()]
    print(f"scanning {len(codes)} stocks...", flush=True)

    all_ca = {}
    for row in conn.execute("SELECT stock_code, event_date::text FROM corporate_action_events"):
        all_ca.setdefault(row[0], []).append(row[1])

    mismatches = []
    processed = 0
    skipped_no_snapshot = 0

    for code in codes:
        processed += 1
        if processed % 500 == 0:
            print(f"  {processed}/{len(codes)} processed, {len(mismatches)} mismatches so far", flush=True)

        path = SNAP_DIR / f"{code}.json.gz"
        if not path.exists():
            skipped_no_snapshot += 1
            continue
        nv_rows = json.loads(gzip.decompress(path.read_bytes()))
        nv = {r[1]: r[5] for r in nv_rows if r[5]}  # date -> close

        ph_rows = conn.execute(
            "SELECT date::text, close FROM price_history WHERE stock_code=? AND close IS NOT NULL AND close > 0",
            (code,),
        ).fetchall()

        ca_dates = all_ca.get(code, [])
        ca_dt = [datetime.strptime(d, "%Y-%m-%d") for d in ca_dates] if ca_dates else []

        for day, close in ph_rows:
            nv_close = nv.get(day)
            if not nv_close:
                continue
            ratio = float(close) / nv_close
            if abs(ratio - 1) < 0.15:
                continue
            if is_clean(ratio):
                continue
            if ca_dt:
                d0 = datetime.strptime(day, "%Y-%m-%d")
                if any(abs((d0 - cd).days) <= 3 for cd in ca_dt):
                    continue
            mismatches.append({"stock_code": code, "date": day, "ph_close": float(close),
                                "naver_close": nv_close, "ratio": ratio})

    print(f"done. {len(mismatches)} mismatches across scan, {skipped_no_snapshot} stocks had no snapshot", flush=True)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(mismatches, ensure_ascii=False), encoding="utf-8")
    print(f"written to {OUT_FILE}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
