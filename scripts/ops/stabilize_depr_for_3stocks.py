#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from datetime import datetime

DB = "/Applications/stock_dashboard/stock.db"
TARGETS = ["096770", "178320", "268280"]
YEARS = [2022, 2023, 2024, 2025]


def ensure_log(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS depreciation_q_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          stock_code TEXT NOT NULL,
          year INTEGER NOT NULL,
          quarter INTEGER NOT NULL,
          report_type TEXT NOT NULL,
          row_id INTEGER,
          old_depreciation_q REAL,
          new_depreciation_q REAL,
          cause_code TEXT,
          note TEXT
        )
        """
    )


def log(conn: sqlite3.Connection, code: str, year: int, quarter: int, row_id: int | None, old_v, new_v, cause: str, note: str):
    conn.execute(
        """
        INSERT INTO depreciation_q_fix_log
        (fixed_at,stock_code,year,quarter,report_type,row_id,old_depreciation_q,new_depreciation_q,cause_code,note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            code,
            year,
            quarter,
            "CFS",
            row_id,
            old_v,
            new_v,
            cause,
            note,
        ),
    )


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    ensure_log(conn)

    fixed_annual = 0
    nulled_quarter = 0

    for code in TARGETS:
        for y in YEARS:
            # 1) 연간 기준값은 quarter=4, is_annual=1의 기존 검증행(dart/null_seibro/fnguide_verified) 우선
            trusted = conn.execute(
                """
                SELECT id, depreciation, data_source
                FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=1 AND quarter=4 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY CASE
                    WHEN LOWER(COALESCE(data_source,'')) LIKE 'dart%' THEN 0
                    WHEN LOWER(COALESCE(data_source,'')) LIKE 'null_seibro%' THEN 1
                    WHEN LOWER(COALESCE(data_source,'')) LIKE 'fnguide_verified%' THEN 2
                    ELSE 9 END, id DESC
                LIMIT 1
                """,
                (code, y),
            ).fetchone()
            annual0 = conn.execute(
                """
                SELECT id, depreciation, data_source
                FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=1 AND quarter=0 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY id DESC LIMIT 1
                """,
                (code, y),
            ).fetchone()

            if trusted and annual0 and trusted["depreciation"] is not None:
                t = float(trusted["depreciation"])
                a = float(annual0["depreciation"]) if annual0["depreciation"] is not None else None
                if a is None or abs(a - t) > max(abs(t) * 0.1, 1e9):
                    conn.execute(
                        "UPDATE cash_flow_data SET depreciation=?, data_source='stabilized_from_q4_annual' WHERE id=?",
                        (t, annual0["id"]),
                    )
                    fixed_annual += 1

            # 2) 분기 누적 vs 연간 단위 괴리 시 분기 D&A 사용 차단(NULL)
            q1 = conn.execute(
                """
                SELECT id, depreciation, depreciation_q FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=0 AND quarter=1 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY id DESC LIMIT 1
                """,
                (code, y),
            ).fetchone()
            q2 = conn.execute(
                """
                SELECT id, depreciation, depreciation_q FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=0 AND quarter=2 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY id DESC LIMIT 1
                """,
                (code, y),
            ).fetchone()
            q3 = conn.execute(
                """
                SELECT id, depreciation, depreciation_q FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=0 AND quarter=3 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY id DESC LIMIT 1
                """,
                (code, y),
            ).fetchone()
            annual = conn.execute(
                """
                SELECT depreciation FROM cash_flow_data
                WHERE stock_code=? AND year=? AND is_annual=1 AND quarter=0 AND COALESCE(report_type,'CFS')='CFS'
                ORDER BY id DESC LIMIT 1
                """,
                (code, y),
            ).fetchone()
            if not (q1 and q2 and q3 and annual and annual["depreciation"] is not None):
                continue
            if q1["depreciation"] is None or q2["depreciation"] is None or q3["depreciation"] is None:
                continue
            try:
                c1, c2, c3 = float(q1["depreciation"]), float(q2["depreciation"]), float(q3["depreciation"])
                ann = float(annual["depreciation"])
            except Exception:
                continue

            # 단위 괴리 판단: q3 누적이 annual 대비 너무 작거나(1% 미만) 너무 큼(200% 초과)
            ratio = abs(c3) / max(abs(ann), 1.0)
            if ratio < 0.01 or ratio > 2.0:
                for row, qn in [(q1, 1), (q2, 2), (q3, 3)]:
                    oldq = row["depreciation_q"]
                    conn.execute(
                        "UPDATE cash_flow_data SET depreciation_q=NULL, data_source='mixed_unit_blocked' WHERE id=?",
                        (row["id"],),
                    )
                    log(
                        conn,
                        code,
                        y,
                        qn,
                        row["id"],
                        oldq,
                        None,
                        "MIXED_UNIT_BLOCK",
                        f"ratio_q3_to_annual={ratio:.6f}, q3={c3}, annual={ann}",
                    )
                    nulled_quarter += 1

                q4 = conn.execute(
                    """
                    SELECT id, depreciation_q FROM cash_flow_data
                    WHERE stock_code=? AND year=? AND is_annual=0 AND quarter=4 AND COALESCE(report_type,'CFS')='CFS'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (code, y),
                ).fetchone()
                if q4:
                    oldq = q4["depreciation_q"]
                    conn.execute(
                        "UPDATE cash_flow_data SET depreciation_q=NULL, data_source='mixed_unit_blocked' WHERE id=?",
                        (q4["id"],),
                    )
                    log(
                        conn,
                        code,
                        y,
                        4,
                        q4["id"],
                        oldq,
                        None,
                        "MIXED_UNIT_BLOCK",
                        f"q4 nulled due to q1~q3/annual unit mismatch (ratio={ratio:.6f})",
                    )
                    nulled_quarter += 1

    conn.commit()
    conn.close()
    print({"fixed_annual_rows": fixed_annual, "nulled_quarter_depq_rows": nulled_quarter})


if __name__ == "__main__":
    main()

