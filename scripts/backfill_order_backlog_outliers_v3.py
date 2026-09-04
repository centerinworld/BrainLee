#!/usr/bin/env python3
"""Reparse audited backlog outliers and apply only evidence-backed corrections."""

from __future__ import annotations

import argparse
import concurrent.futures
import functools
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from collectors.dart_backlog_collector import (
    PARSER_VERSION,
    _extract_backlog,
    _fetch_document_with_key_rotation,
    _upsert_backlog_trigger,
)
from db_utils import STOCK_DB_PATH, connect_stock_db


_SAFE_CLEAR_PATTERNS = (
    r"파생상품|통화선도|선물환|통화옵션|이자율\s*스왑|위험회피|헤지",
    r"금융기관.{0,120}(?:총계약금액|계약잔액)|통화\s*총계약금액\s*계약잔액",
    r"미완성공사의\s*손실예상액|공사손실충당부채",
    r"미완성공사.{0,350}재고자산|재고자산.{0,350}미완성공사",
    r"매출채권.{0,100}계약잔액|계약자산.{0,150}계약잔액|계약부채.{0,150}계약잔액",
    r"시장조사기관.{0,300}수주\s*잔고|주요\s*(?:제조사|업체)\s*\d*사의?.{0,200}수주\s*잔고",
)


def _is_safe_clear(excerpt: str) -> bool:
    return any(re.search(pattern, excerpt or "", re.IGNORECASE) for pattern in _SAFE_CLEAR_PATTERNS)


def _load_targets(conn, audit_path: Path) -> list[dict]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    keys = set()
    for row in audit["outliers"]:
        keys.add((str(row["stock_code"]), int(row["fiscal_year"]), int(row["fiscal_quarter"]), str(row["report_type"])))
        keys.add((str(row["stock_code"]), int(row["previous_year"]), int(row["previous_quarter"]), str(row["report_type"])))

    rows = conn.execute(
        """
        SELECT stock_code,fiscal_year,fiscal_quarter,report_type,
               backlog_amount,backlog_unit,backlog_amount_krw,backlog_confidence,
               source_excerpt,source_rcept_no,source_report_nm,source_rcept_dt,
               source_text_hash,parser_version
        FROM dart_backlog_quarterly
        WHERE backlog_amount_krw IS NOT NULL AND backlog_amount_krw > 0
        ORDER BY stock_code,fiscal_year,fiscal_quarter
        """
    ).fetchall()
    return [dict(row) for row in rows if (
        str(row[0]), int(row[1]), int(row[2]), str(row[3])
    ) in keys]


def _decision(row: dict, metric) -> tuple[str, str]:
    old = float(row.get("backlog_amount_krw") or 0)
    new = metric.backlog_amount_krw
    if new is None:
        if _is_safe_clear(str(row.get("source_excerpt") or "")):
            return "clear", "known_false_context"
        return "keep", "new_parser_no_metric_but_context_not_proven_false"
    if metric.backlog_confidence < 0.85:
        return "keep", f"new_confidence_too_low:{metric.backlog_confidence}"
    difference = abs(float(new) - old) / max(abs(old), 1.0)
    if difference <= 0.01:
        return "metadata", "same_value_parser_upgrade"
    if metric.backlog_confidence >= 0.95:
        return "update", f"structured_table_reparse_difference:{difference:.4f}"
    if _is_safe_clear(str(row.get("source_excerpt") or "")):
        return "update", f"replaced_known_false_context:{difference:.4f}"
    return "keep", f"manual_review_non_structured_difference:{difference:.4f}"


def _apply_row(conn, row: dict, result: dict) -> None:
    action = result["action"]
    metric = result["new_metric"]
    params_key = (
        row["stock_code"], row["fiscal_year"], row["fiscal_quarter"], row["report_type"]
    )
    if action == "clear":
        conn.execute(
            """
            UPDATE dart_backlog_quarterly SET
                backlog_amount=NULL,backlog_unit=NULL,backlog_amount_krw=NULL,
                backlog_confidence=0,parser_version=?,updated_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
            """,
            (PARSER_VERSION, *params_key),
        )
        conn.execute(
            """
            UPDATE order_backlog SET backlog_amount=NULL,backlog_unit=NULL,
                backlog_normalized=NULL,collected_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND year=? AND quarter=?
            """,
            params_key[:3],
        )
        return
    if action not in {"update", "metadata"}:
        return
    conn.execute(
        """
        UPDATE dart_backlog_quarterly SET
            backlog_amount=?,backlog_unit=?,backlog_amount_krw=?,backlog_confidence=?,
            source_excerpt=?,source_text_hash=?,parser_version=?,updated_at=CURRENT_TIMESTAMP
        WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
        """,
        (
            metric["backlog_amount"], metric["backlog_unit"], metric["backlog_amount_krw"],
            metric["backlog_confidence"], metric["source_excerpt"], result["source_text_hash"],
            PARSER_VERSION, *params_key,
        ),
    )
    conn.execute(
        """
        UPDATE order_backlog SET backlog_amount=?,backlog_unit='원',
            backlog_normalized=?,collected_at=CURRENT_TIMESTAMP
        WHERE stock_code=? AND year=? AND quarter=?
        """,
        (
            metric["backlog_amount_krw"],
            metric["backlog_amount_krw"] / 1_000_000.0,
            *params_key[:3],
        ),
    )


def _rebuild_triggers(conn, stock_codes: set[str]) -> None:
    for stock_code in stock_codes:
        conn.execute(
            "DELETE FROM dart_tenbagger_triggers_quarterly WHERE stock_code=? AND metric_name='backlog'",
            (stock_code,),
        )
        rows = conn.execute(
            """
            SELECT fiscal_year,fiscal_quarter,report_type
            FROM dart_backlog_quarterly
            WHERE stock_code=? AND backlog_amount_krw IS NOT NULL
            ORDER BY fiscal_year,fiscal_quarter
            """,
            (stock_code,),
        ).fetchall()
        for fiscal_year, fiscal_quarter, report_type in rows:
            _upsert_backlog_trigger(
                conn, stock_code, int(fiscal_year), int(fiscal_quarter), str(report_type)
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--audit",
        default=str(BASE_DIR / "research_outputs" / "order_backlog_outlier_audit_latest.json"),
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply-report", default="")
    args = parser.parse_args()

    primary = connect_stock_db(timeout=60)
    primary.row_factory = sqlite3.Row
    try:
        targets = _load_targets(primary, Path(args.audit))
    finally:
        primary.close()
    if args.apply_report:
        prior = json.loads(Path(args.apply_report).read_text(encoding="utf-8"))
        if prior.get("parser_version") != PARSER_VERSION:
            raise RuntimeError("apply report parser version does not match current parser")
        result_by_key = {tuple(result["key"]): result for result in prior["results"]}
        targets = [row for row in targets if (
            row["stock_code"], row["fiscal_year"], row["fiscal_quarter"], row["report_type"]
        ) in result_by_key]
        results = [
            result_by_key[(row["stock_code"], row["fiscal_year"], row["fiscal_quarter"], row["report_type"])]
            for row in targets
        ]
        args.apply = True
    else:
        results = []
    targets = targets[max(0, args.offset):]
    if args.limit:
        targets = targets[:args.limit]

    @functools.lru_cache(maxsize=None)
    def fetch_document(rcept_no: str) -> str:
        text = _fetch_document_with_key_rotation(rcept_no) if rcept_no else ""
        time.sleep(max(0.0, args.sleep))
        return text

    def process_row(row: dict) -> dict:
        started = time.perf_counter()
        rcept_no = str(row.get("source_rcept_no") or "")
        text = fetch_document(rcept_no)
        if not text:
            action, reason = "keep", "fetch_failed"
            metric_dict = None
            text_hash = None
        else:
            metric = _extract_backlog(text)
            action, reason = _decision(row, metric)
            metric_dict = {
                "backlog_amount": metric.backlog_amount,
                "backlog_unit": metric.backlog_unit,
                "backlog_amount_krw": metric.backlog_amount_krw,
                "backlog_confidence": metric.backlog_confidence,
                "source_excerpt": metric.source_excerpt,
            }
            text_hash = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
        return {
            "key": [row["stock_code"], row["fiscal_year"], row["fiscal_quarter"], row["report_type"]],
            "rcept_no": rcept_no,
            "old_metric": {
                "backlog_amount": row.get("backlog_amount"),
                "backlog_unit": row.get("backlog_unit"),
                "backlog_amount_krw": row.get("backlog_amount_krw"),
                "backlog_confidence": row.get("backlog_confidence"),
                "source_excerpt": row.get("source_excerpt"),
                "parser_version": row.get("parser_version"),
            },
            "new_metric": metric_dict,
            "source_text_hash": text_hash,
            "action": action,
            "reason": reason,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    if not args.apply_report:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for index, result in enumerate(pool.map(process_row, targets), start=1):
                results.append(result)
                if index % 50 == 0:
                    print(f"progress={index}/{len(targets)}", flush=True)

    # Reports are immutable evidence, but apply-time invariants must still win
    # when a newer parser guard is added after a dry-run was generated.
    for result in results:
        if result["action"] in {"update", "metadata"}:
            amount = result.get("new_metric", {}).get("backlog_amount_krw")
            if amount is None or float(amount) < 100_000:
                result["action"] = "keep"
                result["reason"] = "apply_guard_nonpositive_or_too_small"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = BASE_DIR / "research_outputs" / f"order_backlog_v3_backfill_{stamp}.json"
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parser_version": PARSER_VERSION,
        "apply": args.apply,
        "targets": len(targets),
        "documents": len(results) if args.apply_report else fetch_document.cache_info().currsize,
        "actions": {action: sum(r["action"] == action for r in results) for action in ("update", "clear", "metadata", "keep")},
        "results": results,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if args.apply:
        primary = connect_stock_db(timeout=60)
        primary.row_factory = sqlite3.Row
        legacy = sqlite3.connect(str(STOCK_DB_PATH), timeout=60)
        try:
            changed_codes = set()
            for row, result in zip(targets, results):
                if result["action"] in {"update", "clear", "metadata"}:
                    _apply_row(primary, row, result)
                    _apply_row(legacy, row, result)
                    changed_codes.add(str(row["stock_code"]))
            _rebuild_triggers(primary, changed_codes)
            _rebuild_triggers(legacy, changed_codes)
            primary.commit()
            legacy.commit()
        except Exception:
            primary.rollback()
            legacy.rollback()
            raise
        finally:
            primary.close()
            legacy.close()

    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
