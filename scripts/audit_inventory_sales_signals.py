#!/usr/bin/env python3
"""Audit inventory + revenue/order leading signals."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def main() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        summary = dict(conn.execute(
            """
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT stock_code) AS stocks,
                   MIN(fiscal_year || 'Q' || fiscal_quarter) AS min_period,
                   MAX(fiscal_year || 'Q' || fiscal_quarter) AS max_period,
                   SUM(CASE WHEN signal_score > 0 THEN 1 ELSE 0 END) AS good_rows,
                   SUM(CASE WHEN risk_score > 0 THEN 1 ELSE 0 END) AS risk_rows,
                   SUM(CASE WHEN revenue IS NULL THEN 1 ELSE 0 END) AS missing_revenue_rows
            FROM inventory_sales_signals
            """
        ).fetchone())
        by_type = [dict(r) for r in conn.execute(
            """
            SELECT signal_type, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks,
                   AVG(signal_score) AS avg_signal_score, AVG(risk_score) AS avg_risk_score
            FROM inventory_sales_signals
            GROUP BY signal_type
            ORDER BY rows DESC
            """
        ).fetchall()]
        top_good = [dict(r) for r in conn.execute(
            """
            SELECT stock_code, stock_name, fiscal_year, fiscal_quarter, signal_type,
                   signal_score, risk_score, signal_label
            FROM inventory_sales_signals
            WHERE signal_score >= 4
            ORDER BY fiscal_year DESC, fiscal_quarter DESC, signal_score DESC
            LIMIT 20
            """
        ).fetchall()]
        top_risk = [dict(r) for r in conn.execute(
            """
            SELECT stock_code, stock_name, fiscal_year, fiscal_quarter, signal_type,
                   signal_score, risk_score, signal_label
            FROM inventory_sales_signals
            WHERE risk_score >= 4
            ORDER BY fiscal_year DESC, fiscal_quarter DESC, risk_score DESC
            LIMIT 20
            """
        ).fetchall()]
    finally:
        conn.close()

    ok = bool(summary["rows"] and summary["stocks"])
    payload = {
        "ok": ok,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "by_type": by_type,
        "top_good": top_good,
        "top_risk": top_risk,
    }
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    (OUT_DIR / f"inventory_sales_signals_audit_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if ok:
        print(
            "inventory_sales_signals audit OK: "
            f"rows={summary['rows']:,}, stocks={summary['stocks']:,}, "
            f"period={summary['min_period']}~{summary['max_period']}, "
            f"good={summary['good_rows']:,}, risk={summary['risk_rows']:,}"
        )
        return 0
    print("inventory_sales_signals audit FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
