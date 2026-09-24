#!/usr/bin/env python3
"""Fill Q1-Q3 CFS balance-sheet NULLs from consolidated DART report XML only."""

from __future__ import annotations

import argparse
from datetime import datetime

import config
from collectors.dart_document_financials import download_core_fact_details
from db_compat import connect_primary_db


RUN_ID = "dart_document_quarterly_bs_backfill_20260920"
FIELDS = ("total_assets", "total_equity")


def targets(conn, limit: int):
    return conn.execute(
        """WITH open_flags AS (
               SELECT f.stock_code, f.year, f.quarter, f.field
               FROM fin_quarterly_validation_flags f
               JOIN financial_data d
                 ON d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter
                AND d.is_annual IS FALSE AND d.report_type='CFS'
               WHERE f.check_type='QUARTERLY_4WAY' AND f.status='OPEN'
                 AND f.source_count=0 AND f.quarter IN (1,2,3) AND f.field = ANY(?)
                 AND ((f.field='total_assets' AND d.total_assets IS NULL)
                   OR (f.field='total_equity' AND d.total_equity IS NULL))
             ), grouped AS (
               SELECT stock_code, year, quarter, array_agg(DISTINCT field) fields
               FROM open_flags GROUP BY stock_code, year, quarter
             )
             SELECT g.stock_code, g.year, g.quarter, g.fields, d.rcept_no
             FROM grouped g
             JOIN LATERAL (
               SELECT rcept_no FROM dart_disclosures
               WHERE stock_code=g.stock_code
                 AND ((g.quarter=1 AND report_nm LIKE '%분기보고서%' AND report_nm LIKE ('%' || '(' || g.year::text || '.03)'))
                   OR (g.quarter=2 AND report_nm LIKE '%반기보고서%' AND report_nm LIKE ('%' || '(' || g.year::text || '.06)'))
                   OR (g.quarter=3 AND report_nm LIKE '%분기보고서%' AND report_nm LIKE ('%' || '(' || g.year::text || '.09)')))
               ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 1
             ) d ON TRUE
             ORDER BY g.year DESC, g.quarter, g.stock_code
             LIMIT ?""",
        (list(FIELDS), limit),
    ).fetchall()


def main(limit: int, apply: bool) -> None:
    conn = connect_primary_db(timeout=180, readonly=not apply)
    stats = {"targets": 0, "documents": 0, "filled": 0, "not_consolidated": 0, "no_fact": 0}
    now = datetime.now().isoformat(timespec="seconds")
    try:
        for row in targets(conn, limit):
            stats["targets"] += 1
            try:
                facts = download_core_fact_details(row["rcept_no"], config.DART_API_KEY3)
            except Exception:
                stats["no_fact"] += 1
                continue
            stats["documents"] += 1
            for field in row["fields"]:
                detail = facts.get(field)
                if detail is None:
                    stats["no_fact"] += 1
                    continue
                value, consolidated = detail
                if not consolidated:
                    stats["not_consolidated"] += 1
                    continue
                current = conn.execute(
                    f"""SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=?
                        AND is_annual IS FALSE AND report_type='CFS' AND {field} IS NULL""",
                    (row["stock_code"], row["year"], row["quarter"]),
                ).fetchone()
                if not current:
                    continue
                if apply:
                    conn.execute(
                        f"""UPDATE financial_data SET {field}=?, data_source=CASE
                              WHEN COALESCE(data_source, '') LIKE '%dart_document_fallback%'
                                THEN COALESCE(data_source, '')
                              ELSE COALESCE(data_source, '') || '+dart_document_fallback'
                            END WHERE id=? AND {field} IS NULL""",
                        (value, current["id"]),
                    )
                    conn.execute(
                        """INSERT INTO financial_fix_log
                           (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,
                            field_name,old_value,new_value,fix_rule,source,run_id)
                           VALUES(?,?,?,?,?,0,'CFS',?,NULL,?,'DART_DOCUMENT_XML_CONSOLIDATED',?,?)""",
                        (now, current["id"], row["stock_code"], row["year"], row["quarter"], field,
                         value, f"OpenDART rcept_no={row['rcept_no']}", RUN_ID),
                    )
                    conn.execute(
                        """UPDATE fin_quarterly_validation_flags
                           SET dart_value=?, source_count=1, notes=?, updated_at=?
                           WHERE stock_code=? AND year=? AND quarter=? AND field=?
                             AND check_type='QUARTERLY_4WAY' AND status='OPEN' AND source_count=0""",
                        (value, f"DART consolidated report XML: rcept_no={row['rcept_no']}", now,
                         row["stock_code"], row["year"], row["quarter"], field),
                    )
                stats["filled"] += 1
        if apply:
            conn.commit()
    finally:
        conn.close()
    print(stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    main(args.limit, args.apply)
