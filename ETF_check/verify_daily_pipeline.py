#!/usr/bin/env python3
"""Fail the daily ETF job unless every cutover prerequisite is present."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from full_pdf_collector import DB_PATH, connect
from issuer_pdf_fallback import validated_domestic_exceptions


AUDIT_ROOT = Path(__file__).with_name("audits")


def _excluded_delisted_tickers(conn: sqlite3.Connection) -> set[str]:
    """상장폐지 추정 자동제외 티커 — etf_delisting_watch.py 참조 (2026-09-29).
    KIS 마스터파일이 KRX 상장폐지를 즉시 반영 안 해 유니버스에 남은 종목이 계속 빈 PDF를
    내면서 이 스크립트의 all-or-nothing postcondition을 영구적으로 막던 문제 수정."""
    try:
        return {r[0] for r in conn.execute("SELECT etf_ticker FROM etf_delisting_exclusion")}
    except sqlite3.OperationalError:
        return set()


def verify(day: str, db_path: Path = DB_PATH) -> dict:
    legacy_validation = os.getenv("ENABLE_ETFCHECK_VALIDATION", "0") == "1"
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        universe = conn.execute(
            "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?", (day,)
        ).fetchone()[0]
        excluded = _excluded_delisted_tickers(conn)
        excl_ph = ",".join("?" for _ in excluded) if excluded else "''"
        excl_params = list(excluded)
        excluded_in_universe = (
            conn.execute(
                f"SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=? AND etf_ticker IN ({excl_ph})",
                [day, *excl_params],
            ).fetchone()[0] if excluded else 0
        )
        # scale/sample은 상장폐지 종목도 여전히 값이 나올 수 있어(마지막 시가총액 캐시 등) 전체
        # universe로 비교하고, PDF 관련 체크만 제외 반영한 pdf_universe로 비교한다.
        pdf_universe = int(universe or 0) - excluded_in_universe
        pdf = conn.execute(
            f"""
            SELECT COUNT(*) snapshots,
                   SUM(status='success') successes,
                   SUM(status='empty') empty_count,
                   SUM(status='error') error_count
            FROM etf_pdf_full_snapshot WHERE base_date=? AND etf_ticker NOT IN ({excl_ph})
            """,
            [day, *excl_params],
        ).fetchone()
        scale = conn.execute(
            "SELECT COUNT(*) FROM etf_scale_daily WHERE base_date=?", (day,)
        ).fetchone()[0]
        sample = conn.execute(
            """
            SELECT attempted,success,error_count,status
            FROM etfcheck_k_sample_run WHERE base_date=?
            ORDER BY run_id DESC LIMIT 1
            """,
            (day,),
        ).fetchone()
        parity = conn.execute(
            """
            SELECT passed,failures_json FROM etf_source_parity_daily
            WHERE base_date=?
            """,
            (day,),
        ).fetchone()
        control = conn.execute(
            """
            SELECT mode,consecutive_pass_days,last_evaluated_date
            FROM etf_source_control WHERE control_id=1
            """
        ).fetchone()
        issuer_exceptions = validated_domestic_exceptions(conn, day)
    finally:
        conn.close()

    failures = []
    if not universe:
        failures.append("universe_missing")
    if not pdf or int(pdf["snapshots"] or 0) != int(pdf_universe):
        failures.append("pdf_snapshot_coverage")
    effective_successes = (int(pdf["successes"] or 0) if pdf else 0) + len(issuer_exceptions)
    if effective_successes != int(pdf_universe):
        failures.append("pdf_success_coverage")
    failed_pdf_count = (
        int(pdf["empty_count"] or 0) + int(pdf["error_count"] or 0) if pdf else 0
    )
    if failed_pdf_count != len(issuer_exceptions):
        failures.append("pdf_empty_or_error")
    if int(scale or 0) != int(universe):
        failures.append("scale_coverage")
    if legacy_validation:
        if not sample or int(sample["attempted"] or 0) < 60 or int(sample["success"] or 0) != int(sample["attempted"] or 0):
            failures.append("etfcheck_sample_coverage")
        if not parity or not int(parity["passed"] or 0):
            failures.append("parity_gate")
    elif not control or control["mode"] != "krx_primary" or int(control["consecutive_pass_days"] or 0) < 5:
        failures.append("direct_source_not_certified")

    return {
        "base_date": day,
        "ok": not failures,
        "universe": int(universe or 0),
        "excluded_delisted_count": len(excluded),
        "excluded_delisted_tickers": sorted(excluded) if excluded else [],
        "pdf_successes": int(pdf["successes"] or 0) if pdf else 0,
        "issuer_exception_count": len(issuer_exceptions),
        "issuer_exception_tickers": [item["etf_ticker"] for item in issuer_exceptions],
        "scale_count": int(scale or 0),
        "sample_success": int(sample["success"] or 0) if sample else 0,
        "sample_attempted": int(sample["attempted"] or 0) if sample else 0,
        "parity_failures": json.loads(parity["failures_json"]) if parity else [],
        "validation_mode": "etfcheck_parallel" if legacy_validation else "direct_internal",
        "source_control_mode": control["mode"] if control else None,
        "source_control_pass_days": int(control["consecutive_pass_days"] or 0) if control else 0,
        "failures": failures,
    }


def write_direct_audit(result: dict) -> Path:
    """Keep direct-pipeline evidence separate from historical ETF Check audits."""
    AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    path = AUDIT_ROOT / f"direct_internal_{result['base_date']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--write-audit", action="store_true",
                        help="write the direct-pipeline receipt under audits/")
    args = parser.parse_args()
    result = verify(args.date, Path(args.db))
    if args.write_audit:
        result["audit_path"] = str(write_direct_audit(result))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
