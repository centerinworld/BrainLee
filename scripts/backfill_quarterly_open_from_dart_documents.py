#!/usr/bin/env python3
"""Safely fill NULL Q4 CFS facts from DART business-report XML.

Only existing quarterly CFS balance-sheet rows with a NULL requested field are
updated.  Income-statement values in a business report are annual cumulative
amounts and must not be written into the Q4 standalone column.  The validation
flag remains OPEN because this is one authoritative source, not a cross-source
confirmation.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import config
from collectors.dart_document_financials import download_core_facts
from db_compat import connect_primary_db


RUN_ID = "dart_document_q4_backfill_20260920"
FIELDS = {"total_assets", "total_equity"}


def targets(conn, limit: int):
    return conn.execute(
        """WITH open_flags AS (
               SELECT stock_code, year, field
               FROM fin_quarterly_validation_flags
               WHERE check_type='QUARTERLY_4WAY' AND status='OPEN'
                 AND source_count=0 AND quarter=4 AND field = ANY(?)
             ), fillable_flags AS (
               SELECT f.stock_code, f.year, f.field
               FROM open_flags f
               JOIN financial_data d
                 ON d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=4
                AND d.is_annual IS FALSE AND d.report_type='CFS'
               WHERE (f.field='total_assets' AND d.total_assets IS NULL)
                  OR (f.field='total_equity' AND d.total_equity IS NULL)
             ), grouped AS (
               SELECT stock_code, year, array_agg(DISTINCT field) fields
               FROM fillable_flags GROUP BY stock_code, year
             )
             SELECT g.stock_code, g.year, g.fields, d.rcept_no
             FROM grouped g
             JOIN LATERAL (
               SELECT rcept_no FROM dart_disclosures
               WHERE stock_code=g.stock_code AND report_nm LIKE '사업보고서%'
                 AND report_nm LIKE ('%' || '(' || g.year::text || '.%')
               ORDER BY rcept_dt DESC, rcept_no DESC LIMIT 1
             ) d ON TRUE
             ORDER BY g.year DESC, g.stock_code
             LIMIT ?""",
        (list(FIELDS), limit),
    ).fetchall()


def main(limit: int, apply: bool) -> None:
    conn = connect_primary_db(timeout=120, readonly=not apply)
    rows = targets(conn, limit)
    stats = {"targets": len(rows), "documents": 0, "filled": 0, "no_facts": 0}
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        try:
            facts = download_core_facts(row["rcept_no"], config.DART_API_KEY3)
        except Exception:
            stats["no_facts"] += 1
            continue
        stats["documents"] += 1
        for field in row["fields"]:
            value = facts.get(field)
            if value is None:
                continue
            existing = conn.execute(
                f"""SELECT id, {field} AS value FROM financial_data
                    WHERE stock_code=? AND year=? AND quarter=4
                      AND is_annual IS FALSE AND report_type='CFS'
                      AND {field} IS NULL""",
                (row["stock_code"], row["year"]),
            ).fetchall()
            for current in existing:
                if apply:
                    conn.execute(
                        f"""UPDATE financial_data SET {field}=?,
                                data_source=CASE
                                    WHEN COALESCE(data_source, '') LIKE '%dart_document_fallback%'
                                      THEN COALESCE(data_source, '')
                                    ELSE COALESCE(data_source, '') || '+dart_document_fallback'
                                END
                            WHERE id=? AND {field} IS NULL""",
                        (value, current["id"]),
                    )
                    conn.execute(
                        """INSERT INTO financial_fix_log
                           (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,
                            field_name,old_value,new_value,fix_rule,source,run_id)
                           VALUES (?,?,?,?,4,0,'CFS',?,NULL,?,
                                   'DART_DOCUMENT_XML_FALLBACK',?,?)""",
                        (now, current["id"], row["stock_code"], row["year"], field, value,
                         f"OpenDART rcept_no={row['rcept_no']}", RUN_ID),
                    )
                    conn.execute(
                        """UPDATE fin_quarterly_validation_flags
                           SET dart_value=?, source_count=1,
                               notes=?, updated_at=?
                           WHERE stock_code=? AND year=? AND quarter=4 AND field=?
                             AND check_type='QUARTERLY_4WAY' AND status='OPEN'
                             AND source_count=0""",
                        (value, f"DART business-report XML fallback: rcept_no={row['rcept_no']}", now,
                         row["stock_code"], row["year"], field),
                    )
                stats["filled"] += 1
    if apply:
        conn.commit()
    conn.close()
    print(stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    main(args.limit, args.apply)
