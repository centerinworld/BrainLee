#!/usr/bin/env python3
"""
Backfill missing dilution_events.issue_amount from DART document text.

This is intentionally narrower than the legacy dart_disclosure_parse importer:
it repairs issuance/rights/result disclosures where an amount is meaningful,
and skips retirement, buyback-before-maturity, self-CB sale, subsidiary-only,
and bonus issue rows where issue_amount would be misleading.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db
from collectors.dart_backlog_collector import _fetch_document_with_key_rotation
from collectors.dart_dilution_collector import (
    _extract_conversion_or_exercise_price,
    _extract_metric,
)
from collectors.dart_equity_issue_collector import (
    _extract_issue_amount as _extract_equity_issue_amount,
    _extract_labeled_num,
    _extract_share_pair,
)

log = logging.getLogger("dilution_issue_amount_backfill")

ISSUANCE_NAME_RE = re.compile(
    r"전환사채권발행결정|신주인수권부사채권발행결정|교환사채권발행결정|"
    r"주요사항보고서\(유상증자결정\)|주요사항보고서\(유무상증자결정\)|"
    r"유상증자결정|유무상증자결정|"
    r"증권발행결과.*유상증자|유상증자또는주식관련사채등의발행결과",
)

SKIP_NAME_RE = re.compile(
    r"만기전사채취득|자기전환사채|자기사채|사채취득|매수선택권행사자지정|"
    r"무상증자결정|종속회사|자회사|청약결과|철회",
)


def _execute_with_retry(conn: sqlite3.Connection, sql: str, params: tuple | list = (), attempts: int = 8):
    delay = 0.5
    for attempt in range(attempts):
        try:
            return conn.execute(sql, params)
        except Exception as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.7, 6.0)


def _compact(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "")


def _is_repairable(report_nm: str | None, event_type: str | None) -> bool:
    name = _compact(report_nm)
    if not name or SKIP_NAME_RE.search(name):
        return False
    if event_type == "BONUS":
        return False
    return bool(ISSUANCE_NAME_RE.search(name))


def _instrument_type(report_nm: str | None, event_type: str | None) -> Optional[str]:
    name = report_nm or ""
    et = event_type or ""
    if "신주인수권부사채" in name or et == "BW":
        return "BW"
    if "교환사채" in name or et == "EB":
        return "EB"
    if "전환사채" in name or et == "CB":
        return "CB"
    return None


def _extract_amount(text: str, report_nm: str | None, event_type: str | None) -> tuple[Optional[float], str]:
    t = re.sub(r"\s+", " ", text or "")
    ins = _instrument_type(report_nm, event_type)
    if ins:
        amount, excerpt = _extract_metric(
            t,
            r"발행금액|권면\s*\(?전자등록\)?\s*총액|권면총액|"
            r"사채(?:의\s*)?권면\s*\(?전자등록\)?\s*총액|사채총액|"
            r"전자등록총액|교환사채\s*총액",
            prefer="max",
        )
        if amount:
            return amount, excerpt
        price, price_ex = _extract_conversion_or_exercise_price(t, ins)
        shares, shares_ex = _extract_metric(
            t,
            r"전환에\s*따라\s*발행할\s*주식(?:\s*-\s*주식\s*수|\s*수)?|"
            r"교환대상\s*주식수|행사주식수|발행예정주식수|발행할\s*주식\s*수",
            prefer="last",
        )
        if price and shares:
            return price * shares, f"{price_ex} | {shares_ex} | 전환/행사가액 x 잠재주식수 추정"[:800]
        return None, ""

    amount, excerpt = _extract_equity_issue_amount(t)
    if amount:
        return amount, excerpt

    new_shares, shares_ex = _extract_share_pair(t, r"신주의\s*종류와\s*수|신주\s*발행주식수|발행할\s*주식")
    issue_price, price_ex = _extract_labeled_num(
        t,
        r"신주\s*발행가액\s*보통주식\s*\(원\)|발행가액\s*보통주식\s*\(원\)|발행가액",
        window=120,
    )
    if new_shares and issue_price:
        return new_shares * issue_price, f"{shares_ex} | {price_ex} | 신주수 x 발행가액 추정"[:800]

    return None, ""


def backfill(since: str, until: Optional[str], limit: Optional[int], source: str, dry_run: bool = False) -> dict:
    since_compact = re.sub(r"\D", "", since)[:8] or "20200101"
    since_date = f"{since_compact[:4]}-{since_compact[4:6]}-{since_compact[6:8]}"
    until_date = None
    if until:
        until_compact = re.sub(r"\D", "", until)[:8]
        if len(until_compact) == 8:
            until_date = f"{until_compact[:4]}-{until_compact[4:6]}-{until_compact[6:8]}"
    conn = connect_stock_db(timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    try:
        source_clause = "" if source == "all" else "AND COALESCE(de.data_source,'') = ?"
        params: list = [since_date]
        until_clause = ""
        if until_date:
            until_clause = "AND de.disclosed_at <= ?"
            params.append(until_date)
        if source != "all":
            params.append(source)
        if limit:
            params.append(int(limit))
        rows = conn.execute(
            f"""
            SELECT de.id, de.rcept_no, de.stock_code, de.stock_name, de.event_type,
                   de.disclosed_at, de.report_nm, de.data_source
            FROM dilution_events de
            WHERE de.disclosed_at >= ?
              {until_clause}
              AND (de.issue_amount IS NULL OR de.issue_amount <= 0)
              AND de.rcept_no IS NOT NULL
              {source_clause}
            ORDER BY de.disclosed_at DESC, de.id DESC
            {"LIMIT ?" if limit else ""}
            """,
            params,
        ).fetchall()

        scanned = 0
        repairable = 0
        updated = 0
        no_amount = 0
        skipped = 0
        errors = 0
        examples = []

        for row in rows:
            scanned += 1
            if scanned % 250 == 0:
                log.info(
                    "progress scanned=%s repairable=%s updated=%s skipped=%s no_amount=%s errors=%s",
                    scanned, repairable, updated, skipped, no_amount, errors,
                )
            if not _is_repairable(row["report_nm"], row["event_type"]):
                skipped += 1
                continue
            repairable += 1
            try:
                text = _fetch_document_with_key_rotation(row["rcept_no"]) or ""
                if not text or text.startswith("020"):
                    no_amount += 1
                    continue
                amount, excerpt = _extract_amount(text, row["report_nm"], row["event_type"])
                if not amount or amount <= 0:
                    no_amount += 1
                    continue
                if not dry_run:
                    _execute_with_retry(
                        conn,
                        """
                        UPDATE dilution_events
                        SET issue_amount = ?,
                            data_source = CASE
                                WHEN data_source = 'dart_disclosure_parse' THEN 'dart_issue_amount_repair'
                                ELSE data_source
                            END,
                            collected_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (amount, row["id"]),
                    )
                updated += 1
                if len(examples) < 10:
                    examples.append({
                        "rcept_no": row["rcept_no"],
                        "stock_code": row["stock_code"],
                        "event_type": row["event_type"],
                        "date": row["disclosed_at"],
                        "amount_억": round(amount / 100_000_000, 2),
                        "source": row["data_source"],
                        "excerpt": excerpt[:160],
                    })
                if updated % 100 == 0 and not dry_run:
                    conn.commit()
            except Exception as exc:
                errors += 1
                log.exception("repair failed rcept_no=%s: %s", row["rcept_no"], exc)

        if not dry_run:
            conn.commit()
        return {
            "since": since,
            "until": until,
            "source": source,
            "limit": limit,
            "dry_run": dry_run,
            "scanned": scanned,
            "repairable": repairable,
            "updated": updated,
            "skipped_non_issuance": skipped,
            "no_amount": no_amount,
            "errors": errors,
            "examples": examples,
        }
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill missing dilution_events.issue_amount")
    ap.add_argument("--since", default="2020-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--source", default="dart_disclosure_parse", choices=["dart_disclosure_parse", "all"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(backfill(args.since, args.until, args.limit, args.source, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
