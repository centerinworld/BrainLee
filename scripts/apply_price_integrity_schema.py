#!/usr/bin/env python3
"""Create/refresh the price-integrity safety objects (tables + canonical views).

Idempotent — safe to re-run any time price_history, corporate_action_events,
or price_jump_audit changes. This is the only place that should build
price_trading_calendar / price_integrity_quarantine / price_verification_state /
price_ingestion_quarantine / price_history_quality_v / canonical_price_history_v /
canonical_price_returns_v. Run price jump audit + Naver verification first so
canonical_price_history_v picks up their latest classifications.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
import price_integrity as pi  # noqa: E402


def run() -> dict:
    conn = connect_primary_db(timeout=60)
    try:
        pi.ensure_schema(conn)
        pi.install_write_guard(conn)
        pi.refresh_calendar(conn)
        pi.rebuild_views(conn)
        conn.commit()
        counts = {}
        for name in ("price_trading_calendar", "price_integrity_quarantine",
                     "price_verification_state", "price_ingestion_quarantine"):
            counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        quality = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT quality_status, COUNT(*) FROM price_history_quality_v GROUP BY quality_status"
            ).fetchall()
        }
        total = sum(quality.values())
        for view in ("price_history_quality_v", "canonical_price_history_v", "canonical_price_returns_v"):
            counts[view] = total
        return {"policy_version": pi.POLICY_VERSION, "row_counts": counts, "quality_status_breakdown": quality}
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
