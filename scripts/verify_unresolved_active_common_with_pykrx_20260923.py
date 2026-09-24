#!/usr/bin/env python3
"""Cross-check unresolved_active_common price jumps against pykrx (KRX official).

Reuses scripts/verify_price_history_with_naver.py's exact philosophy and table
(external_price_verification, external_source='pykrx' this time) - even a
confirmed match does NOT flip return_usable back to 1 (a second source
reproducing the same jump proves it isn't OUR data bug, not that the jump is
a usable investment return - that still needs separate corporate-action
evidence). This only sharpens the label from the generic "unresolved" bucket
into externally_confirmed_internal_corruption / externally_confirmed_price_
jump_review, matching the classification vocabulary the Naver script already
established, so results from both sources are directly comparable.

Scope: this session's remaining time doesn't cover all 1,223 distinct stock
codes in the unresolved_active_common queue (6,352 rows, 2010-2026) - batches
by stock_code (one pykrx fetch per stock covers all its flagged rows) and
processes the highest-row-count stocks first for maximum coverage per call.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, verification_fingerprint  # noqa: E402

PROTECTED_FROM_OVERRIDE = (
    "confirmed_corporate_action",
    "corporate_action_pending_confirmation",
    "corporate_action_or_delisting_nearby",
    "non_equity_symbol", "coverage_gap", "invalid_ohlcv", "invalid_previous_price", "quarantined_basis",
)

DDL = """
CREATE TABLE IF NOT EXISTS external_price_verification (
  stock_code TEXT NOT NULL,
  event_date TEXT NOT NULL,
  external_source TEXT NOT NULL,
  external_previous_date TEXT,
  external_previous_close REAL,
  external_event_close REAL,
  external_price_ratio REAL,
  agreement_class TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  input_fingerprint TEXT,
  verified_at TEXT NOT NULL,
  PRIMARY KEY(stock_code,event_date,external_source)
)
"""


def close_match(a: float | None, b: float | None, tolerance: float = 0.005) -> bool:
    return bool(a and b and abs(a - b) / max(abs(b), 0.01) <= tolerance)


def fetch_pykrx_history(code: str, start: str, end: str) -> dict:
    from pykrx import stock
    df = stock.get_market_ohlcv(start.replace("-", ""), end.replace("-", ""), code)
    return {idx.strftime("%Y-%m-%d"): float(row["종가"]) for idx, row in df.iterrows()} if len(df) else {}


def run(limit_stocks: int, dry_run: bool) -> dict:
    conn = connect_primary_db(timeout=120)
    native_script(conn, DDL)

    top_codes = [
        r[0] for r in conn.execute(
            """SELECT stock_code FROM price_jump_audit
               WHERE classification='unresolved_active_common'
               GROUP BY stock_code ORDER BY COUNT(*) DESC LIMIT ?""",
            (limit_stocks,),
        ).fetchall()
    ]

    promoted = 0
    agreement_counts: dict[str, int] = {}
    errors = []
    checked = 0

    for i, code in enumerate(top_codes):
        audits = conn.execute(
            """SELECT stock_code,event_date,previous_date,previous_close,event_close,price_ratio,
                      public_previous_close,public_event_close,public_price_ratio,classification,
                      matched_event_type,matched_report_name
               FROM price_jump_audit WHERE stock_code=? AND classification='unresolved_active_common'""",
            (code,),
        ).fetchall()
        if not audits:
            continue
        dates = [a[1] for a in audits]
        start = (datetime.strptime(min(dates), "%Y-%m-%d") - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (datetime.strptime(max(dates), "%Y-%m-%d") + timedelta(days=3)).strftime("%Y-%m-%d")
        try:
            history = fetch_pykrx_history(code, start, end)
        except Exception as exc:
            errors.append(f"{code}: {exc}")
            continue
        time.sleep(0.15)

        for row in audits:
            (stock_code, event_date, previous_date, previous_close, event_close, price_ratio,
             public_previous_close, public_event_close, public_price_ratio, classification,
             matched_event_type, matched_report_name) = row
            checked += 1
            event_close_ext = history.get(event_date)
            previous_close_ext = history.get(previous_date) if previous_date else None
            ratio = (
                event_close_ext / previous_close_ext
                if event_close_ext and previous_close_ext else None
            )
            internal_match = close_match(ratio, price_ratio)
            public_match = close_match(ratio, public_price_ratio)
            if not ratio:
                agreement, confidence = "external_missing", 0.0
            elif internal_match and public_match:
                agreement, confidence = "all_three_agree", 0.98
            elif internal_match:
                agreement, confidence = "pykrx_confirms_price_history", 0.9
            elif public_match:
                agreement, confidence = "pykrx_confirms_public_raw", 0.95
            else:
                agreement, confidence = "three_way_disagreement", 0.3
            agreement_counts[agreement] = agreement_counts.get(agreement, 0) + 1

            evidence = (
                f"internal={price_ratio}; public={public_price_ratio if public_price_ratio is not None else 'NA'}; "
                f"pykrx={ratio if ratio is not None else 'NA'}"
            )
            now = datetime.now().isoformat(timespec="seconds")
            fp = verification_fingerprint({
                "stock_code": stock_code, "event_date": event_date, "previous_date": previous_date,
                "previous_close": previous_close, "event_close": event_close, "price_ratio": price_ratio,
                "public_previous_close": public_previous_close, "public_event_close": public_event_close,
                "public_price_ratio": public_price_ratio, "matched_event_type": matched_event_type,
                "matched_report_name": matched_report_name,
            })

            if not dry_run:
                conn.execute(
                    """INSERT INTO external_price_verification
                       (stock_code,event_date,external_source,external_previous_date,external_previous_close,
                        external_event_close,external_price_ratio,agreement_class,confidence,evidence,
                        input_fingerprint,verified_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(stock_code,event_date,external_source) DO UPDATE SET
                         external_previous_date=excluded.external_previous_date,
                         external_previous_close=excluded.external_previous_close,
                         external_event_close=excluded.external_event_close,
                         external_price_ratio=excluded.external_price_ratio,
                         agreement_class=excluded.agreement_class,confidence=excluded.confidence,
                         evidence=excluded.evidence,input_fingerprint=excluded.input_fingerprint,
                         verified_at=excluded.verified_at""",
                    (stock_code, event_date, "pykrx", previous_date, previous_close_ext, event_close_ext,
                     ratio, agreement, confidence, evidence, fp, now),
                )
                current = conn.execute(
                    "SELECT classification FROM price_jump_audit WHERE stock_code=? AND event_date=?",
                    (stock_code, event_date),
                ).fetchone()
                if current and current[0] not in PROTECTED_FROM_OVERRIDE:
                    if agreement == "pykrx_confirms_public_raw" and confidence >= 0.9:
                        if current[0] != "externally_confirmed_internal_corruption":
                            conn.execute(
                                """UPDATE price_jump_audit SET classification='externally_confirmed_internal_corruption',
                                   return_usable=0, evidence=COALESCE(evidence,'')||'; pykrx confirms public raw series'
                                   WHERE stock_code=? AND event_date=?""",
                                (stock_code, event_date),
                            )
                            promoted += 1
                    elif agreement in ("pykrx_confirms_price_history", "all_three_agree") and confidence >= 0.9:
                        if current[0] != "externally_confirmed_price_jump_review":
                            conn.execute(
                                """UPDATE price_jump_audit SET classification='externally_confirmed_price_jump_review',
                                   return_usable=0, evidence=COALESCE(evidence,'')||'; pykrx agrees; economic return requires separate validation'
                                   WHERE stock_code=? AND event_date=?""",
                                (stock_code, event_date),
                            )
                            promoted += 1
        if not dry_run:
            conn.commit()
        if (i + 1) % 20 == 0:
            print(f"progress: {i+1}/{len(top_codes)} stocks, {checked} rows checked, {promoted} promoted")

    conn.close()
    return {
        "stocks_processed": len(top_codes), "rows_checked": checked,
        "promoted": promoted, "agreement": agreement_counts,
        "errors": errors[:20], "error_count": len(errors), "dry_run": dry_run,
    }


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-stocks", type=int, default=300)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.limit_stocks, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
