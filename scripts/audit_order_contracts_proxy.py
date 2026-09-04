#!/usr/bin/env python3
"""Audit DART order-contract proxy data and route health.

Checks:
- order_contracts table exists and is synced from dart_contracts
- recent collection freshness
- parse coverage and unverified rows
- surge screener can calculate candidates

Writes JSON/MD reports under research_outputs and exits non-zero only for
critical issues that should wake the next Codex/Claude pass.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_order_contracts(conn: sqlite3.Connection) -> None:
    from routes.order_contracts import _db

    c = _db()
    c.close()


def sync_from_dart_contracts(conn: sqlite3.Connection) -> dict:
    before = conn.execute("SELECT COUNT(*) FROM order_contracts").fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO order_contracts
            (stock_code, stock_name, rcept_no, rcept_dt, report_nm, is_termination,
             contract_amount, revenue_ratio_pct, recent_revenue, counterpart,
             contract_start, contract_end, parse_ok, raw_snippet, dart_url)
        SELECT
            stock_code,
            stock_name,
            rcept_no,
            CASE
              WHEN length(disclosed_at)=8
              THEN substr(disclosed_at,1,4)||'-'||substr(disclosed_at,5,2)||'-'||substr(disclosed_at,7,2)
              ELSE disclosed_at
            END,
            report_nm,
            CASE WHEN report_nm LIKE '%해지%' THEN 1 ELSE 0 END,
            contract_amount_krw,
            contract_ratio_pct,
            revenue_base,
            counterparty,
            contract_start,
            contract_end,
            CASE WHEN contract_amount_krw IS NOT NULL THEN 1 ELSE 0 END,
            substr(raw_text,1,2000),
            'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='||rcept_no
        FROM dart_contracts
        WHERE rcept_no IS NOT NULL
          AND stock_code IS NOT NULL
          AND length(stock_code)=6
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND report_nm NOT LIKE '%거래정지%'
        """
    )
    mid = conn.execute("SELECT COUNT(*) FROM order_contracts").fetchone()[0]
    conn.execute(
        """
        DELETE FROM order_contracts
        WHERE stock_code IS NULL
           OR length(stock_code) != 6
           OR stock_code NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
           OR report_nm LIKE '%거래정지%'
        """
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM order_contracts").fetchone()[0]
    return {
        "inserted": max(0, mid - before),
        "deleted_invalid": max(0, mid - after),
        "net_change": after - before,
    }


def repair_recent_parse_misses(conn: sqlite3.Connection, lookback_days: int = 14) -> dict:
    from collectors.dart_contract_collector import _extract_amounts

    rows = conn.execute(
        """
        SELECT id, raw_snippet
        FROM order_contracts
        WHERE rcept_dt >= date('now', ?)
          AND contract_amount IS NULL
          AND COALESCE(raw_snippet, '') != ''
        """,
        (f"-{lookback_days} day",),
    ).fetchall()

    repaired = 0
    skipped_undisclosed = 0
    skipped_document_missing = 0
    for row in rows:
        snippet = row["raw_snippet"] or ""
        if "014 파일이 존재하지 않습니다." in snippet:
            skipped_document_missing += 1
            continue
        if re.search(r"(해지금액|계약금액\s*총액|확정\s*계약금액)\(원\)\s*-", snippet):
            skipped_undisclosed += 1
            continue
        if re.search(r"계약\s*금액.*비공개|계약금액.*비공개|비공개\s*조항", snippet):
            skipped_undisclosed += 1
            continue

        parsed = _extract_amounts(snippet)
        amount = parsed.get("contract_amount_krw")
        if amount is None:
            continue

        conn.execute(
            """
            UPDATE order_contracts
               SET contract_amount = COALESCE(contract_amount, ?),
                   revenue_ratio_pct = COALESCE(revenue_ratio_pct, ?),
                   recent_revenue = COALESCE(recent_revenue, ?),
                   counterpart = COALESCE(counterpart, ?),
                   contract_start = COALESCE(contract_start, ?),
                   contract_end = COALESCE(contract_end, ?),
                   parse_ok = 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (
                amount,
                parsed.get("contract_ratio_pct"),
                parsed.get("revenue_base"),
                parsed.get("counterparty"),
                parsed.get("contract_start"),
                parsed.get("contract_end"),
                row["id"],
            ),
        )
        repaired += 1

    conn.commit()
    return {
        "repaired_rows": repaired,
        "skipped_undisclosed": skipped_undisclosed,
        "skipped_document_missing": skipped_document_missing,
        "lookback_days": lookback_days,
    }


def one_value(conn: sqlite3.Connection, sql: str, default=None):
    row = conn.execute(sql).fetchone()
    if not row:
        return default
    return row[0]


def fetch_metrics(conn: sqlite3.Connection) -> dict:
    today = date.today()
    freshness_cutoff = today - timedelta(days=7)
    metrics = {}

    metrics["dart_contracts_count"] = one_value(conn, "SELECT COUNT(*) FROM dart_contracts", 0)
    metrics["order_contracts_count"] = one_value(conn, "SELECT COUNT(*) FROM order_contracts", 0)
    metrics["sync_ratio_pct"] = (
        round(metrics["order_contracts_count"] / metrics["dart_contracts_count"] * 100, 2)
        if metrics["dart_contracts_count"]
        else 0.0
    )
    metrics["order_min_date"] = one_value(conn, "SELECT MIN(rcept_dt) FROM order_contracts")
    metrics["order_max_date"] = one_value(conn, "SELECT MAX(rcept_dt) FROM order_contracts")
    metrics["parse_ok_count"] = one_value(conn, "SELECT SUM(parse_ok) FROM order_contracts", 0) or 0
    metrics["verified_count"] = one_value(conn, "SELECT SUM(verified) FROM order_contracts", 0) or 0
    metrics["missing_amount_count"] = one_value(
        conn,
        "SELECT COUNT(*) FROM order_contracts WHERE contract_amount IS NULL",
        0,
    )
    metrics["recent_rows_7d"] = one_value(
        conn,
        "SELECT COUNT(*) FROM order_contracts WHERE rcept_dt >= date('now','-7 day')",
        0,
    )
    metrics["freshness_cutoff"] = freshness_cutoff.isoformat()
    metrics["parse_ok_ratio"] = (
        round(metrics["parse_ok_count"] / metrics["order_contracts_count"] * 100, 2)
        if metrics["order_contracts_count"]
        else 0.0
    )
    return metrics


def fetch_quality_trend(conn: sqlite3.Connection) -> dict:
    recent = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN parse_ok=1 THEN 1 ELSE 0 END) AS parse_ok_count,
            SUM(CASE WHEN contract_amount IS NULL THEN 1 ELSE 0 END) AS missing_amount_count
        FROM order_contracts
        WHERE rcept_dt >= date('now','-7 day')
        """
    ).fetchone()
    previous = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN parse_ok=1 THEN 1 ELSE 0 END) AS parse_ok_count,
            SUM(CASE WHEN contract_amount IS NULL THEN 1 ELSE 0 END) AS missing_amount_count
        FROM order_contracts
        WHERE rcept_dt >= date('now','-14 day')
          AND rcept_dt < date('now','-7 day')
        """
    ).fetchone()

    def summarize(row: sqlite3.Row) -> dict:
        total = row["total"] or 0
        parse_ok = row["parse_ok_count"] or 0
        missing = row["missing_amount_count"] or 0
        return {
            "total": total,
            "parse_ok_count": parse_ok,
            "missing_amount_count": missing,
            "parse_ok_ratio": round(parse_ok / total * 100, 2) if total else None,
            "missing_amount_ratio": round(missing / total * 100, 2) if total else None,
        }

    recent_summary = summarize(recent)
    previous_summary = summarize(previous)
    return {
        "recent_7d": recent_summary,
        "previous_7d": previous_summary,
        "parse_ok_ratio_delta": (
            round(recent_summary["parse_ok_ratio"] - previous_summary["parse_ok_ratio"], 2)
            if recent_summary["parse_ok_ratio"] is not None and previous_summary["parse_ok_ratio"] is not None
            else None
        ),
        "missing_amount_ratio_delta": (
            round(recent_summary["missing_amount_ratio"] - previous_summary["missing_amount_ratio"], 2)
            if recent_summary["missing_amount_ratio"] is not None and previous_summary["missing_amount_ratio"] is not None
            else None
        ),
    }


def fetch_missing_reason_breakdown(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT
            CASE
                WHEN contract_amount IS NOT NULL THEN 'ok'
                WHEN COALESCE(raw_snippet, '') LIKE '%014 파일이 존재하지 않습니다.%' THEN 'document_014'
                WHEN COALESCE(raw_snippet, '') LIKE '%계약금액 총액(원) -%'
                  OR COALESCE(raw_snippet, '') LIKE '%확정 계약금액 -%'
                  OR COALESCE(raw_snippet, '') LIKE '%해지금액(원) -%'
                  OR COALESCE(raw_snippet, '') LIKE '%비공개 조항%'
                  OR COALESCE(raw_snippet, '') LIKE '%계약금액은 계약서 비공개%'
                  OR COALESCE(raw_snippet, '') LIKE '%계약 금액%비공개%' THEN 'undisclosed'
                ELSE 'other_parse_miss'
            END AS reason,
            COUNT(*) AS cnt
        FROM order_contracts
        WHERE rcept_dt >= date('now','-7 day')
        GROUP BY 1
        """
    ).fetchall()
    return {row["reason"]: row["cnt"] for row in rows}


def detect_collection_method() -> dict:
    collector_path = ROOT / "collectors" / "dart_collector.py"
    text = collector_path.read_text(encoding="utf-8")
    contract_start = text.find("def _fetch_contract_list_sync")
    contract_end = text.find("async def get_todays_contract_disclosures", contract_start)
    today_start = text.find("def _fetch_todays_contract_sync")
    today_end = text.find("async def parse_contract_document", today_start)
    contract_block = text[contract_start:contract_end] if contract_start != -1 and contract_end != -1 else ""
    today_block = text[today_start:today_end] if today_start != -1 and today_end != -1 else ""
    return {
        "uses_list_json_pagination": "_fetch_dart_list(start, end)" in contract_block and "_fetch_dart_list(today_str, today_str)" in today_block,
        "uses_opendart_kind_i_for_contracts": "kind='I'" in contract_block or 'kind="I"' in contract_block or "kind='I'" in today_block or 'kind="I"' in today_block,
        "evidence_file": str(collector_path),
    }


def fetch_surge_sample() -> dict:
    from routes.order_contracts import get_surge_screener

    result = get_surge_screener(window_months=3, min_growth_pct=50, limit=10)
    return {
        "count": result.get("count", 0),
        "sample": result.get("candidates", [])[:5],
    }


def classify_issues(metrics: dict, surge: dict, sync_result: dict, quality_trend: dict, missing_breakdown: dict, collection_method: dict, recent_collect: dict) -> list[dict]:
    issues: list[dict] = []
    order_count = metrics["order_contracts_count"]
    dart_count = metrics["dart_contracts_count"]
    max_dt = metrics.get("order_max_date")
    source_latest = recent_collect.get("source_latest_date")

    if order_count == 0:
        issues.append({"severity": "critical", "code": "ORDER_CONTRACTS_EMPTY", "message": "order_contracts table is empty."})
    if dart_count and order_count < int(dart_count * 0.95):
        issues.append({
            "severity": "critical",
            "code": "ORDER_CONTRACTS_NOT_SYNCED",
            "message": f"order_contracts count {order_count} is less than 95% of dart_contracts {dart_count}.",
        })
    if max_dt:
        latest = datetime.strptime(max_dt[:10], "%Y-%m-%d").date()
        source_latest_dt = None
        if source_latest:
            try:
                source_latest_dt = datetime.strptime(source_latest, "%Y%m%d").date()
            except ValueError:
                source_latest_dt = None
        if source_latest_dt and latest < source_latest_dt:
            issues.append({
                "severity": "warning",
                "code": "STALE_ORDER_CONTRACTS",
                "message": (
                    f"latest order_contracts date is {max_dt}, "
                    f"but DART source has contract disclosures through {source_latest_dt.isoformat()}."
                ),
            })
    if metrics["parse_ok_ratio"] < 50 and order_count >= 100:
        issues.append({
            "severity": "warning",
            "code": "LOW_PARSE_COVERAGE",
            "message": f"parse_ok_ratio is {metrics['parse_ok_ratio']}%.",
        })
    if surge["count"] == 0 and order_count >= 100:
        issues.append({
            "severity": "warning",
            "code": "NO_SURGE_CANDIDATES",
            "message": "surge screener returned zero candidates despite populated order_contracts.",
        })
    recent_delta = quality_trend.get("parse_ok_ratio_delta")
    recent_missing_delta = quality_trend.get("missing_amount_ratio_delta")
    unexplained_recent = missing_breakdown.get("other_parse_miss", 0)
    if recent_delta is not None and recent_delta <= -15 and unexplained_recent >= 2:
        issues.append({
            "severity": "warning",
            "code": "RECENT_PARSE_DROP",
            "message": (
                f"recent 7d parse_ok_ratio dropped by {abs(recent_delta)}pp "
                f"with {unexplained_recent} unexplained recent parse misses."
            ),
        })
    if recent_missing_delta is not None and recent_missing_delta >= 10 and unexplained_recent >= 2:
        issues.append({
            "severity": "warning",
            "code": "RECENT_MISSING_AMOUNT_SPIKE",
            "message": (
                f"recent 7d missing_amount_ratio increased by {recent_missing_delta}pp "
                f"with {unexplained_recent} unexplained recent parse misses."
            ),
        })
    if not collection_method.get("uses_list_json_pagination") or collection_method.get("uses_opendart_kind_i_for_contracts"):
        issues.append({
            "severity": "critical",
            "code": "CONTRACT_COLLECTION_METHOD_REGRESSED",
            "message": "contract collector no longer clearly uses list.json pagination path instead of kind='I' filtering.",
        })
    if sync_result.get("inserted"):
        issues.append({
            "severity": "info",
            "code": "SYNCED_FROM_DART_CONTRACTS",
            "message": f"synced {sync_result['inserted']} new rows from dart_contracts.",
        })
    if sync_result.get("repaired_rows"):
        issues.append({
            "severity": "info",
            "code": "REPAIRED_RECENT_PARSE_MISSES",
            "message": f"repaired {sync_result['repaired_rows']} recent rows from stored raw_snippet.",
        })
    if sync_result.get("deleted_invalid"):
        issues.append({
            "severity": "info",
            "code": "DELETED_INVALID_ORDER_CONTRACT_CODES",
            "message": f"deleted {sync_result['deleted_invalid']} invalid or non-contract proxy rows.",
        })
    if recent_collect.get("saved"):
        issues.append({
            "severity": "info",
            "code": "BACKFILLED_RECENT_ORDER_CONTRACTS",
            "message": (
                f"backfilled {recent_collect['saved']} rows from DART source "
                f"for {recent_collect.get('start_date')}~{recent_collect.get('end_date')}."
            ),
        })
    return issues


def write_reports(metrics: dict, surge: dict, issues: list[dict], sync_result: dict, quality_trend: dict, missing_breakdown: dict, collection_method: dict, recent_collect: dict) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recent_collect": recent_collect,
        "sync_from_dart_contracts": sync_result,
        "metrics": metrics,
        "quality_trend": quality_trend,
        "missing_reason_breakdown_7d": missing_breakdown,
        "collection_method_check": collection_method,
        "surge": surge,
        "issues": issues,
    }
    json_path = OUT_DIR / f"order_contracts_proxy_audit_{stamp}.json"
    md_path = OUT_DIR / f"order_contracts_proxy_audit_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# order_contracts proxy audit — {stamp}",
        "",
        "## Metrics",
        f"- dart_contracts: {metrics['dart_contracts_count']:,}",
        f"- order_contracts: {metrics['order_contracts_count']:,}",
        f"- sync ratio: {metrics['sync_ratio_pct']}%",
        f"- date range: {metrics.get('order_min_date')} ~ {metrics.get('order_max_date')}",
        f"- parse_ok: {metrics['parse_ok_count']:,} ({metrics['parse_ok_ratio']}%)",
        f"- verified: {metrics['verified_count']:,}",
        f"- missing amount: {metrics['missing_amount_count']:,}",
        f"- recent 7d rows: {metrics['recent_rows_7d']:,}",
        f"- surge candidates: {surge['count']:,}",
        "",
        "## Recent Collect",
        f"- catch-up range: {recent_collect.get('start_date')} ~ {recent_collect.get('end_date')}",
        f"- scanned: {recent_collect.get('scanned', 0):,}",
        f"- saved: {recent_collect.get('saved', 0):,}",
        f"- source latest date: {recent_collect.get('source_latest_date')}",
        "",
        "## Recent Trend",
        f"- recent 7d parse_ok: {quality_trend['recent_7d']['parse_ok_ratio']}% ({quality_trend['recent_7d']['parse_ok_count']}/{quality_trend['recent_7d']['total']})",
        f"- previous 7d parse_ok: {quality_trend['previous_7d']['parse_ok_ratio']}% ({quality_trend['previous_7d']['parse_ok_count']}/{quality_trend['previous_7d']['total']})",
        f"- recent 7d missing amount: {quality_trend['recent_7d']['missing_amount_ratio']}% ({quality_trend['recent_7d']['missing_amount_count']}/{quality_trend['recent_7d']['total']})",
        f"- previous 7d missing amount: {quality_trend['previous_7d']['missing_amount_ratio']}% ({quality_trend['previous_7d']['missing_amount_count']}/{quality_trend['previous_7d']['total']})",
        f"- recent missing breakdown: {json.dumps(missing_breakdown, ensure_ascii=False, sort_keys=True)}",
        "",
        "## Collection Method",
        f"- uses list.json pagination path: {collection_method['uses_list_json_pagination']}",
        f"- uses kind='I' direct filter in contract path: {collection_method['uses_opendart_kind_i_for_contracts']}",
        f"- evidence: {collection_method['evidence_file']}",
        "",
        "## Issues",
    ]
    if issues:
        for issue in issues:
            lines.append(f"- [{issue['severity']}] {issue['code']}: {issue['message']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Surge Sample"])
    for item in surge.get("sample", []):
        lines.append(
            f"- {item.get('stock_name')} ({item.get('stock_code')}): "
            f"growth={item.get('growth_pct')}, recent_to_revenue={item.get('recent_to_revenue_pct')}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    from routes.order_contracts import collect_recent_disclosures

    recent_collect = asyncio.run(collect_recent_disclosures(max_backfill_days=14))
    conn = connect()
    try:
        ensure_order_contracts(conn)
        sync_result = sync_from_dart_contracts(conn)
        sync_result.update(repair_recent_parse_misses(conn))
        metrics = fetch_metrics(conn)
        quality_trend = fetch_quality_trend(conn)
        missing_breakdown = fetch_missing_reason_breakdown(conn)
    finally:
        conn.close()

    collection_method = detect_collection_method()
    surge = fetch_surge_sample()
    issues = classify_issues(metrics, surge, sync_result, quality_trend, missing_breakdown, collection_method, recent_collect)
    write_reports(metrics, surge, issues, sync_result, quality_trend, missing_breakdown, collection_method, recent_collect)

    for issue in issues:
        print(f"[{issue['severity']}] {issue['code']}: {issue['message']}")
    if not issues:
        print("order_contracts proxy audit OK")

    return 1 if any(i["severity"] == "critical" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
