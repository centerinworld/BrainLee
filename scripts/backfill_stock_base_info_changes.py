#!/usr/bin/env python3
"""Seed stock_base_info_changes from normalized listed-share history."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"


def ensure_column(conn: sqlite3.Connection, name: str, ddl: str) -> None:
    columns = {r[1] for r in conn.execute("PRAGMA table_info(stock_base_info_changes)")}
    if name not in columns:
        conn.execute(f"ALTER TABLE stock_base_info_changes ADD COLUMN {name} {ddl}")


def main() -> None:
    conn = sqlite3.connect(DB, timeout=60)
    ensure_column(conn, "source", "TEXT")
    ensure_column(conn, "confidence", "REAL")
    ensure_column(conn, "evidence_report_name", "TEXT")
    before = conn.total_changes
    conn.execute(
        """
        INSERT INTO stock_base_info_changes(
          stock_code,change_date,change_type,old_value,new_value,description,
          source,confidence,evidence_report_name
        )
        SELECT e.stock_code,e.event_date,'shares_issued',CAST(e.old_shares AS TEXT),CAST(e.new_shares AS TEXT),
               '상장주식수 변경: '||printf('%,d',CAST(e.old_shares AS INTEGER))||'주 → '
                 ||printf('%,d',CAST(e.new_shares AS INTEGER))||'주 ('||printf('%.4f',e.share_ratio)||'배)'
                 ||CASE WHEN e.adjustment_status='factor_confirmed' THEN ' · 보정계수 확정' ELSE ' · 유형 검토 필요' END,
               e.source,e.confidence,e.evidence_report_name
        FROM corporate_action_events e
        WHERE NOT EXISTS (
          SELECT 1 FROM stock_base_info_changes c
          WHERE c.stock_code=e.stock_code AND c.change_date=e.event_date
            AND c.change_type='shares_issued'
            AND COALESCE(c.old_value,'')=CAST(e.old_shares AS TEXT)
            AND COALESCE(c.new_value,'')=CAST(e.new_shares AS TEXT)
        )
        """
    )
    inserted = conn.total_changes-before
    conn.execute("CREATE INDEX IF NOT EXISTS idx_base_changes_type_date ON stock_base_info_changes(change_type,change_date,stock_code)")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM stock_base_info_changes").fetchone()[0]
    print({"inserted":inserted,"total":total})
    conn.close()


if __name__ == "__main__":
    main()
