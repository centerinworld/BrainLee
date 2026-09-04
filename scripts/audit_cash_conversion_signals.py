#!/usr/bin/env python3
"""Audit cash-conversion quality signals."""

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
        summary = dict(conn.execute("""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT stock_code) AS stocks,
                   MIN(fiscal_year || 'Q' || fiscal_quarter) AS min_period,
                   MAX(fiscal_year || 'Q' || fiscal_quarter) AS max_period,
                   SUM(CASE WHEN signal_score > 0 THEN 1 ELSE 0 END) AS good_rows,
                   SUM(CASE WHEN risk_score > 0 THEN 1 ELSE 0 END) AS risk_rows,
                   SUM(CASE WHEN quality_flag='missing_receivable' THEN 1 ELSE 0 END) AS missing_receivable_rows
            FROM cash_conversion_signals
        """).fetchone())
        by_type = [dict(r) for r in conn.execute("""
            SELECT signal_type, quality_flag, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks,
                   AVG(signal_score) AS avg_signal_score, AVG(risk_score) AS avg_risk_score
            FROM cash_conversion_signals
            GROUP BY signal_type, quality_flag
            ORDER BY rows DESC
        """).fetchall()]
        latest = [dict(r) for r in conn.execute("""
            SELECT fiscal_year || 'Q' || fiscal_quarter AS period,
                   COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks,
                   SUM(signal_score>=4) AS good_4p,
                   SUM(risk_score>=4) AS risk_4p
            FROM cash_conversion_signals
            GROUP BY fiscal_year, fiscal_quarter
            ORDER BY fiscal_year DESC, fiscal_quarter DESC
            LIMIT 8
        """).fetchall()]
    finally:
        conn.close()

    ok = bool(summary["rows"] and summary["stocks"] and summary["good_rows"] is not None)
    payload = {
        "ok": ok,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "by_type": by_type,
        "latest_periods": latest,
    }
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    (OUT_DIR / f"cash_conversion_signals_audit_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if ok:
        print(
            "cash_conversion_signals audit OK: "
            f"rows={summary['rows']:,}, stocks={summary['stocks']:,}, "
            f"period={summary['min_period']}~{summary['max_period']}, "
            f"good={summary['good_rows']:,}, risk={summary['risk_rows']:,}, "
            f"missing_ar={summary['missing_receivable_rows']:,}"
        )
        return 0
    print("cash_conversion_signals audit FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
