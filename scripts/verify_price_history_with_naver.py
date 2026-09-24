#!/usr/bin/env python3
"""Cross-check audited price jumps against Naver Finance daily history."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from price_integrity import native_script, verification_fingerprint  # noqa: E402

DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "naver_price_crosscheck_20260712.json"
ITEM_RE = re.compile(r'data="([^"]+)"')

# A confirmed corporate action is a structural fact about share count, not a
# price disagreement. Two independent quote providers reproducing the same
# raw jump never overrides it — see price_integrity.POLICY_VERSION docstring.
# Any classification the audit already treats as "not a tradable return"
# (return_usable=0 for a reason other than plain unresolved ambiguity) must
# stay excluded here, or this script quietly re-opens it.
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
);
CREATE INDEX IF NOT EXISTS idx_epv_agreement ON external_price_verification(agreement_class,confidence);
"""


def fetch_history(code: str) -> tuple[str, dict[str, float], str | None]:
    from scripts.backfill_naver_ohlcv_2015_2018 import fetch
    _, rows, error = fetch(code, '20100101', datetime.now().strftime('%Y%m%d'))
    return code, {r[1].replace('-',''):r[5] for r in rows}, error


def close_match(a: float | None, b: float | None, tolerance: float = 0.005) -> bool:
    return bool(a and b and abs(a-b) / max(abs(b), 0.01) <= tolerance)


def _ensure_fingerprint_column(conn) -> None:
    """Add input_fingerprint to a table that may already exist without it.

    CREATE TABLE IF NOT EXISTS never alters an existing table, and on
    PostgreSQL, executescript() silently drops ALTER TABLE statements (they
    are treated as already-applied schema migrations) — so this needs its own
    explicit, backend-aware path via native_script rather than living in DDL.
    """
    if hasattr(conn, '_connection'):
        native_script(conn, "ALTER TABLE external_price_verification ADD COLUMN IF NOT EXISTS input_fingerprint TEXT")
    else:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(external_price_verification)")}
        if 'input_fingerprint' not in columns:
            conn.execute("ALTER TABLE external_price_verification ADD COLUMN input_fingerprint TEXT")


def run(
    conn: sqlite3.Connection,
    workers: int = 6,
    only_new: bool = False,
    codes: list[str] | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    native_script(conn, DDL)
    _ensure_fingerprint_column(conn)
    code_filter = ""
    params: tuple[str, ...] = ()
    if codes:
        code_filter = " AND stock_code IN ({})".format(",".join("?" for _ in codes))
        params = tuple(codes)
    audits = conn.execute(
        f"""SELECT stock_code,event_date,previous_date,previous_close,event_close,price_ratio,
                   public_previous_close,public_event_close,public_price_ratio,classification,
                   matched_event_type,matched_report_name
            FROM price_jump_audit WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            {code_filter}""",
        params,
    ).fetchall()
    existing_verif = {
        (r["stock_code"], r["event_date"]): r
        for r in conn.execute(
            """SELECT stock_code,event_date,agreement_class,confidence,input_fingerprint,verified_at
               FROM external_price_verification WHERE external_source='naver_finance'"""
        ).fetchall()
    }

    # The fingerprint captures the audit's *inputs* (price basis, comparison
    # dates, matched corporate-action evidence) so a backfill or a freshly
    # confirmed corporate action invalidates a stale verdict automatically,
    # even under --only-new. 'classification' is deliberately excluded from
    # the fingerprint (see price_integrity.verification_fingerprint) because
    # this very script writes it — including it would make our own prior
    # promotion look like a changed input on every subsequent run.
    fingerprints: dict[tuple[str, str], str] = {}
    codes_needing_fetch: set[str] = set()
    for row in audits:
        key = (row["stock_code"], row["event_date"])
        fp = verification_fingerprint(dict(row))
        fingerprints[key] = fp
        prior = existing_verif.get(key)
        stale = (prior is None or prior["input_fingerprint"] != fp
                 or prior["agreement_class"] == 'external_missing'
                 or (datetime.now()-datetime.fromisoformat(prior['verified_at'])).days >= 7)
        if stale or not only_new:
            codes_needing_fetch.add(row["stock_code"])

    audits_by_code: dict[str, list[sqlite3.Row]] = {}
    for row in audits:
        if row["stock_code"] in codes_needing_fetch:
            audits_by_code.setdefault(row["stock_code"], []).append(row)

    histories: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_history, code) for code in audits_by_code]
        for future in as_completed(futures):
            code, rows, error = future.result()
            histories[code] = rows
            if error:
                errors[code] = error

    now = datetime.now().isoformat(timespec="seconds")
    records = []
    agreement_counts: dict[str, int] = {}
    fresh_verif: dict[tuple[str, str], dict] = {}
    for code, rows in audits_by_code.items():
        history = histories.get(code, {})
        dates = sorted(history)
        for audit in rows:
            event_key = audit["event_date"].replace("-", "")
            previous_key = audit["previous_date"].replace("-", "") if audit["previous_date"] else None
            event_close = history.get(event_key)
            previous_close = history.get(previous_key) if previous_key else None
            previous_external_date = previous_key
            ratio = event_close / previous_close if event_close and previous_close else None
            internal_match = close_match(ratio, audit["price_ratio"])
            public_match = close_match(ratio, audit["public_price_ratio"])
            if not ratio:
                agreement, confidence = "external_missing", 0.0
            elif internal_match and public_match:
                agreement, confidence = "all_three_agree", 0.98
            elif internal_match:
                agreement, confidence = "naver_confirms_price_history", 0.9
            elif public_match:
                agreement, confidence = "naver_confirms_public_raw", 0.95
            else:
                agreement, confidence = "three_way_disagreement", 0.3
            agreement_counts[agreement] = agreement_counts.get(agreement, 0) + 1
            evidence = (
                f"internal={audit['price_ratio']}; "
                f"public={audit['public_price_ratio'] if audit['public_price_ratio'] is not None else 'NA'}; "
                f"naver={ratio if ratio is not None else 'NA'}"
            )
            key = (code, audit["event_date"])
            fp = fingerprints[key]
            fresh_verif[key] = {"agreement_class": agreement, "confidence": confidence}
            records.append((code,audit["event_date"],"naver_finance",previous_external_date,
                            previous_close,event_close,ratio,agreement,confidence,evidence,fp,now))
    conn.executemany(
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
             verified_at=excluded.verified_at""", records
    )

    # Promotion is decided in Python, per row, against the audit's *current*
    # classification — never a blanket SQL UPDATE. A confirmed (or even
    # merely pending/nearby) corporate action, a non-equity symbol, or any
    # verdict computed from since-changed inputs (fingerprint mismatch) is
    # never eligible: two quote providers reproducing the same raw jump does
    # not establish that the jump is a usable investment return.
    conn.execute("UPDATE price_jump_audit SET return_usable=0")
    promoted = reverted = 0
    for row in audits:
        key = (row["stock_code"], row["event_date"])
        current = conn.execute("SELECT * FROM price_jump_audit WHERE stock_code=? AND event_date=?" +
            (" FOR UPDATE" if hasattr(conn, '_connection') else ""), key).fetchone()
        if current is None or verification_fingerprint(dict(current)) != fingerprints[key]:
            continue
        row = current
        verif = fresh_verif.get(key) or (
            dict(existing_verif[key]) if key in existing_verif
            and existing_verif[key]["input_fingerprint"] == fingerprints[key] else None
        )
        if verif is None or row["classification"] in PROTECTED_FROM_OVERRIDE:
            continue
        confidence = float(verif["confidence"])
        agreement = verif["agreement_class"]
        if agreement == "naver_confirms_public_raw" and confidence >= 0.9:
            if row["classification"] != "externally_confirmed_internal_corruption":
                conn.execute(
                    """UPDATE price_jump_audit SET classification='externally_confirmed_internal_corruption',
                       return_usable=0, evidence=evidence||'; Naver confirms public raw series'
                       WHERE stock_code=? AND event_date=?""", (row["stock_code"], row["event_date"]))
                promoted += 1
        elif agreement in ("naver_confirms_price_history", "all_three_agree") and confidence >= 0.9:
            if row["classification"] != "externally_confirmed_price_jump_review":
                conn.execute(
                    """UPDATE price_jump_audit SET classification='externally_confirmed_price_jump_review',
                       return_usable=0, evidence=evidence||'; Naver agrees; economic return requires separate validation'
                       WHERE stock_code=? AND event_date=?""", (row["stock_code"], row["event_date"]))
                promoted += 1
    conn.commit()
    result = {"stocks_requested": len(audits_by_code), "events_checked": len(records),
              "request_errors": len(errors), "agreement": agreement_counts,
              "promoted": promoted, "elapsed_seconds": round(time.time()-started, 1), "verified_at": now}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-new", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--codes", default="", help="comma-separated stock codes")
    args = parser.parse_args()
    codes = [value.strip().zfill(6) for value in args.codes.split(",") if value.strip()]
    conn = connect_stock_db(timeout=60)
    try:
        print(json.dumps(run(
            conn,
            workers=max(1, min(args.workers, 8)),
            only_new=args.only_new,
            codes=codes or None,
        ), ensure_ascii=False, indent=2))
    finally:
        conn.close()
