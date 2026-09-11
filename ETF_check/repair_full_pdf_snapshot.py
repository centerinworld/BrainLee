"""Rebuild one KRX ETF PDF snapshot date from retained raw responses."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from full_pdf_collector import (
    DB_PATH,
    ETF,
    assess_and_publish,
    connect,
    resolve_isin_codes,
    response_quality_issue,
    save_failure,
    save_snapshot,
)


def load_raw(path: Path) -> tuple[list[dict[str, Any]], str]:
    data = gzip.open(path, "rb").read()
    rows = json.loads(data)
    if not isinstance(rows, list):
        raise ValueError("raw payload is not a list")
    return rows, hashlib.sha256(data).hexdigest()


def repair(day: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    conn = connect(db_path)
    snapshots = conn.execute(
        """
        SELECT etf_ticker,etf_name,isin,raw_path
        FROM etf_pdf_full_snapshot
        WHERE base_date=? ORDER BY etf_ticker
        """,
        (day,),
    ).fetchall()
    universe_count = conn.execute(
        "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?",
        (day,),
    ).fetchone()[0]
    result: dict[str, Any] = {
        "base_date": day,
        "snapshots": len(snapshots),
        "repaired": 0,
        "rejected": 0,
        "unreadable": 0,
        "rejected_tickers": [],
    }
    payloads: dict[str, tuple[list[dict[str, Any]], str, Path]] = {}
    all_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        raw_path = Path(snapshot["raw_path"] or "")
        try:
            rows, digest = load_raw(raw_path)
            payloads[snapshot["etf_ticker"]] = (rows, digest, raw_path)
            all_rows.extend(rows)
        except Exception:
            continue
    isin_code_map = resolve_isin_codes(conn, all_rows)

    for snapshot in snapshots:
        etf = ETF(snapshot["etf_ticker"], snapshot["etf_name"], snapshot["isin"])
        raw_path = Path(snapshot["raw_path"] or "")
        try:
            rows, digest, raw_path = payloads[etf.ticker]
            previous = conn.execute(
                """
                SELECT component_count FROM etf_pdf_full_snapshot
                WHERE etf_ticker=? AND base_date<? AND status='success'
                ORDER BY base_date DESC LIMIT 1
                """,
                (etf.ticker, day),
            ).fetchone()
            previous_count = int(previous[0]) if previous else None
            issue = response_quality_issue(rows, previous_count)
            if issue:
                save_failure(conn, day, etf, "error", issue)
                result["rejected"] += 1
                result["rejected_tickers"].append(etf.ticker)
                continue
            save_snapshot(conn, day, etf, rows, str(raw_path), digest, isin_code_map)
            result["repaired"] += 1
        except Exception as exc:
            save_failure(conn, day, etf, "error", f"raw repair failed: {exc}")
            result["unreadable"] += 1
            result["rejected_tickers"].append(etf.ticker)
    result["assessment"] = assess_and_publish(conn, day, int(universe_count))
    result["repaired_at"] = datetime.now().isoformat(timespec="seconds")
    conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    print(json.dumps(repair(args.date, Path(args.db)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
