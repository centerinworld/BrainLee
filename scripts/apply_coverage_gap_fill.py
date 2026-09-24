#!/usr/bin/env python3
"""Fill genuine price_history collection gaps using naver_price_history_backfill.

A "coverage_gap" (price_history_quality_v) is a real broad-market trading day
(per price_trading_calendar) strictly between two consecutive price_history
rows for a stock, where that stock has no row at all. Most such gaps (~1.7M)
are NOT collection failures - they're illiquid/small-cap stocks that simply
didn't trade that day, and naver_price_history_backfill has no data for them
either (nothing to fill: fabricating an OHLCV row for a day with zero trades
would be wrong). Only gaps where naver_price_history_backfill actually has a
value are genuine "we should have collected this but didn't" cases - a much
smaller, safe-to-fill set (see
research_outputs/price_integrity_remediation_20260909/coverage_gap_fill_candidates_20260911.json).

This INSERTs missing rows only (ON CONFLICT DO NOTHING - never touches an
existing row; that's what apply_20181224_splice_repair.py is for). Investor-
flow columns (inst_net_buy etc.) are left NULL since naver has no such data.

Run it, then rebuild the audit/canonical views:
  python3 scripts/apply_coverage_gap_fill.py --apply
  python3 scripts/audit_price_jumps_and_build_canonical.py
  python3 scripts/verify_price_history_with_naver.py --only-new
  python3 scripts/audit_selected_strategy_price_integrity.py
"""
from __future__ import annotations

import json
import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import manifest_gap_fill_status  # noqa: E402

CANDIDATES_FILE = (
    ROOT / "research_outputs" / "price_integrity_remediation_20260909"
    / "coverage_gap_fill_candidates_20260911.json"
)
CANDIDATES_SHA256 = "55af50c371b1f32c4544ce0e5b19b067d0de380dcfbbc8263da4e1b27ff63977"


def run(dry_run: bool = False) -> dict:
    if hashlib.sha256(CANDIDATES_FILE.read_bytes()).hexdigest() != CANDIDATES_SHA256:
        raise RuntimeError("candidate manifest fingerprint mismatch; rebuild and review it")
    candidates = json.loads(CANDIDATES_FILE.read_text())
    run_id = f"coverage_gap_fill_naver_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    conn = connect_primary_db(timeout=180)
    try:
        # Live collectors run continuously on this DB, so a gap saved to the
        # candidates file a few minutes ago may already be filled by now -
        # ON CONFLICT DO NOTHING below makes a race safe either way.
        rows = []
        skipped = {}
        for item in candidates:
            values = (item["open"],item["high"],item["low"],item["close"],item["volume"])
            status = manifest_gap_fill_status(conn,item["stock_code"],item["missing_date"],values)
            if status == 'ready':
                rows.append((item["stock_code"],item["missing_date"],*values))
            else:
                skipped[status] = skipped.get(status,0)+1
        result = {"run_id": run_id, "candidate_rows": len(candidates), "ready_rows": len(rows),
                  "skipped_rows": skipped, "dry_run": dry_run}
        if dry_run:
            return result

        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")

        inserted = 0
        for stock_code, date, o, h, l, c, v in rows:
            cur = conn.execute(
                """INSERT INTO price_history(stock_code,date,open,high,low,close,volume)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT DO NOTHING""",
                (stock_code, date, o, h, l, c, v),
            )
            inserted += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        conn.execute(
            """INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,
                old_value_summary,new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                "Genuine collection gaps: broad-market trading days between two existing "
                "price_history rows for a stock where that stock had no row at all, and "
                "naver_price_history_backfill independently has a real OHLCV value for it",
                inserted,
                "INSERT INTO price_history(...) ON CONFLICT DO NOTHING using "
                "naver_price_history_backfill OHLCV for the missing (stock_code,date) pairs",
                "no row existed (NULL)",
                "inserted from naver_price_history_backfill (investor-flow columns left NULL - "
                "naver has no such data)",
                "naver_price_history_backfill (Naver Finance fchart)", run_id,
            ),
        )
        conn.commit()
        result["inserted"] = inserted
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=not args.apply), ensure_ascii=False, indent=2))
