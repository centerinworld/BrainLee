"""Coverage-aware ETF membership audit and query helpers.

An absent row is not proof that a stock is absent from every ETF when the
component source returns only a partial list.  This module persists snapshot
coverage separately from component rows and exposes an explicit verdict.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from direct_etf_pipeline import DB_PATH, DatabaseManager, ETFCollector, KISETFSource, trading_date


def initialize_coverage(db: DatabaseManager) -> None:
    with db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS etf_pdf_snapshot_status (
                base_date TEXT NOT NULL,
                etf_ticker TEXT NOT NULL,
                expected_component_count INTEGER,
                returned_component_count INTEGER NOT NULL,
                domestic_stock_count INTEGER NOT NULL,
                coverage_ratio REAL,
                coverage_status TEXT NOT NULL,
                source TEXT NOT NULL,
                error TEXT,
                collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (base_date, etf_ticker),
                FOREIGN KEY (etf_ticker) REFERENCES etf_meta(etf_ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_etf_pdf_status_date
                ON etf_pdf_snapshot_status(base_date, coverage_status);
            """
        )


def classify(expected: int | None, returned: int, domestic: int) -> tuple[str, float | None]:
    ratio = min(returned / expected, 1.0) if expected and expected > 0 else None
    if expected is not None and expected > 0 and returned >= expected:
        return "complete", ratio
    if returned > 0:
        return "partial", ratio
    if expected == 0:
        return "complete_empty", 1.0
    return "unknown_empty", ratio


def save_status(
    db: DatabaseManager,
    day: str,
    ticker: str,
    expected: int | None,
    returned: int,
    domestic: int,
    source: str,
    error: str | None = None,
) -> None:
    status, ratio = ("error", None) if error else classify(expected, returned, domestic)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO etf_pdf_snapshot_status(
                base_date, etf_ticker, expected_component_count,
                returned_component_count, domestic_stock_count, coverage_ratio,
                coverage_status, source, error, collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date, etf_ticker) DO UPDATE SET
                expected_component_count=excluded.expected_component_count,
                returned_component_count=excluded.returned_component_count,
                domestic_stock_count=excluded.domestic_stock_count,
                coverage_ratio=excluded.coverage_ratio,
                coverage_status=excluded.coverage_status,
                source=excluded.source,
                error=excluded.error,
                collected_at=excluded.collected_at
            """,
            (day, ticker, expected, returned, domestic, ratio, status, source, error,
             datetime.now().isoformat(timespec="seconds")),
        )


def collect_with_coverage(
    db: DatabaseManager, source: KISETFSource, day: str, limit: int | None = None
) -> dict[str, Any]:
    initialize_coverage(db)
    collector = ETFCollector(db, source)
    metas = collector.metas(limit)
    result: dict[str, Any] = {
        "base_date": day,
        "attempted": len(metas),
        "succeeded": 0,
        "failed": 0,
        "complete": 0,
        "partial": 0,
        "empty_or_unknown": 0,
        "rows": 0,
        "errors": [],
    }
    for index, meta in enumerate(metas, 1):
        try:
            snap = source.composition(meta, day)
            result["rows"] += db.replace_snapshot(snap)
            returned = len(snap.rows)
            status, _ = classify(snap.expected, returned, returned)
            save_status(db, day, meta.ticker, snap.expected, returned, returned, snap.source)
            result["succeeded"] += 1
            if status == "complete":
                result["complete"] += 1
            elif status == "partial":
                result["partial"] += 1
            else:
                result["empty_or_unknown"] += 1
        except Exception as exc:
            message = str(exc)
            save_status(db, day, meta.ticker, None, 0, 0, "KIS_OFFICIAL", message)
            result["failed"] += 1
            if len(result["errors"]) < 30:
                result["errors"].append({"ticker": meta.ticker, "error": message})
        if index % 100 == 0:
            print(json.dumps({"progress": index, "total": len(metas)}, ensure_ascii=False), flush=True)
    return result


def membership_verdict(db: DatabaseManager, stock_ticker: str, day: str | None = None) -> dict[str, Any]:
    initialize_coverage(db)
    with db.connect() as conn:
        selected = day or conn.execute(
            "SELECT MAX(base_date) FROM etf_pdf_snapshot_status"
        ).fetchone()[0]
        if not selected:
            return {
                "stock_ticker": stock_ticker,
                "base_date": None,
                "verdict": "source_unavailable",
                "is_confirmed": False,
                "reason": "ETF별 구성종목 수집상태가 없습니다.",
            }
        universe = conn.execute("SELECT COUNT(*) FROM etf_meta WHERE is_active=1").fetchone()[0]
        status = conn.execute(
            """
            SELECT COUNT(*) total,
                   SUM(coverage_status IN ('complete','complete_empty')) complete,
                   SUM(coverage_status='partial') partial,
                   SUM(coverage_status IN ('error','unknown_empty')) unresolved
            FROM etf_pdf_snapshot_status WHERE base_date=?
            """,
            (selected,),
        ).fetchone()
        holdings = [dict(row) for row in conn.execute(
            """
            SELECT p.etf_ticker,m.etf_name,p.weight,p.estimated_shares,
                   p.estimated_amount,p.quality_status,p.coverage_ratio
            FROM etf_pdf_daily p JOIN etf_meta m USING(etf_ticker)
            WHERE p.base_date=? AND p.stock_ticker=?
            ORDER BY p.estimated_amount DESC NULLS LAST,p.weight DESC
            """,
            (selected, stock_ticker),
        )]
        legacy = conn.execute(
            """
            SELECT trade_date,etf_count,etf_amount,scope_label
            FROM etf_inclusion_daily WHERE stock_code=?
            ORDER BY trade_date DESC LIMIT 1
            """,
            (stock_ticker,),
        ).fetchone()

    total = int(status["total"] or 0)
    complete = int(status["complete"] or 0)
    partial = int(status["partial"] or 0)
    unresolved = int(status["unresolved"] or 0) + max(int(universe) - total, 0)
    all_complete = total == int(universe) and complete == total
    if holdings:
        verdict, confirmed = "included", True
        reason = f"직접수집 구성종목에서 {len(holdings)}개 ETF 편입을 확인했습니다."
    elif all_complete:
        verdict, confirmed = "confirmed_not_included", True
        reason = f"활성 ETF {universe}개 전체의 완전한 구성목록에서 발견되지 않았습니다."
    else:
        verdict, confirmed = "not_observed_unconfirmed", False
        reason = (
            "수집된 구성종목에서는 발견되지 않았지만 일부 ETF 목록이 불완전하여 "
            "미편입을 확정할 수 없습니다."
        )
    return {
        "stock_ticker": stock_ticker,
        "base_date": selected,
        "verdict": verdict,
        "is_confirmed": confirmed,
        "reason": reason,
        "observed_etf_count": len(holdings),
        "holdings": holdings,
        "coverage": {
            "active_etfs": int(universe),
            "status_rows": total,
            "complete_etfs": complete,
            "partial_etfs": partial,
            "unresolved_etfs": unresolved,
            "all_etfs_complete": all_complete,
        },
        "legacy_etfcheck": dict(legacy) if legacy else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--date")
    parser.add_argument("--stock", default="172670")
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=0.3)
    args = parser.parse_args()
    db = DatabaseManager(Path(args.db))
    day = trading_date(args.date)
    if args.collect:
        result = collect_with_coverage(db, KISETFSource(args.delay), day, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(membership_verdict(db, args.stock, day), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
