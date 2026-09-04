#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
OUT_DIR = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch')


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          stock_code TEXT,
          year INTEGER,
          quarter INTEGER,
          is_annual INTEGER,
          report_type TEXT,
          field_name TEXT NOT NULL,
          old_value REAL,
          new_value REAL,
          fix_rule TEXT NOT NULL,
          source TEXT NOT NULL,
          run_id TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cashflow_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          row_id INTEGER NOT NULL,
          stock_code TEXT,
          year INTEGER,
          quarter INTEGER,
          is_annual INTEGER,
          report_type TEXT,
          field_name TEXT NOT NULL,
          old_value REAL,
          new_value REAL,
          fix_rule TEXT NOT NULL,
          source TEXT NOT NULL,
          run_id TEXT NOT NULL
        )
        """
    )


def log_fix(conn: sqlite3.Connection, table: str, payload: dict) -> None:
    conn.execute(
        f"""
        INSERT INTO {table} (
          fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,
          field_name,old_value,new_value,fix_rule,source,run_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload['fixed_at'],
            payload['row_id'],
            payload.get('stock_code'),
            payload.get('year'),
            payload.get('quarter'),
            payload.get('is_annual'),
            payload.get('report_type'),
            payload['field_name'],
            payload.get('old_value'),
            payload.get('new_value'),
            payload['fix_rule'],
            payload['source'],
            payload['run_id'],
        ),
    )


def safe_num(v):
    return None if v is None else float(v)


def approx_eq(a, b, tol=1.0):
    return a is not None and b is not None and abs(a - b) <= tol


def repair_financial(conn: sqlite3.Connection, run_id: str, dry_run: bool) -> dict:
    rows = conn.execute(
        """
        SELECT id, stock_code, year, quarter, is_annual, report_type,
               total_assets, total_liabilities, total_equity
        FROM financial_data
        WHERE report_type='CFS'
        """
    ).fetchall()
    fixed = 0
    touched_rows = 0
    for r in rows:
        rid, code, y, q, ia, rt, a, l, e = r
        a = safe_num(a)
        l = safe_num(l)
        e = safe_num(e)
        changes = []

        if a is not None and l is not None and e is None:
            cand = a - l
            if cand >= 0:
                changes.append(('total_equity', e, cand, 'identity_fill_equity'))
                e = cand
        if a is not None and l is None and e is not None:
            cand = a - e
            if cand >= 0:
                changes.append(('total_liabilities', l, cand, 'identity_fill_liabilities'))
                l = cand
        if a is None and l is not None and e is not None:
            cand = l + e
            if cand >= 0:
                changes.append(('total_assets', a, cand, 'identity_fill_assets'))
                a = cand

        if a is not None and l is not None and e is not None:
            if approx_eq(a, l) and e > 0:
                cand = a - e
                if cand >= 0 and not approx_eq(cand, l):
                    changes.append(('total_liabilities', l, cand, 'equal_assets_liab_fix_liabilities'))
                    l = cand
            elif approx_eq(a, e) and l > 0:
                cand = l + e
                if cand >= 0 and not approx_eq(cand, a):
                    changes.append(('total_assets', a, cand, 'equal_assets_equity_fix_assets'))
                    a = cand

        if not changes:
            continue

        touched_rows += 1
        if not dry_run:
            conn.execute(
                """
                UPDATE financial_data
                SET total_assets=?, total_liabilities=?, total_equity=?
                WHERE id=?
                """,
                (a, l, e, rid),
            )
            now = datetime.now().isoformat(timespec='seconds')
            for field_name, old_v, new_v, rule in changes:
                log_fix(
                    conn,
                    'financial_fix_log',
                    {
                        'fixed_at': now,
                        'row_id': rid,
                        'stock_code': code,
                        'year': y,
                        'quarter': q,
                        'is_annual': ia,
                        'report_type': rt,
                        'field_name': field_name,
                        'old_value': old_v,
                        'new_value': new_v,
                        'fix_rule': rule,
                        'source': 'scripts/ops/repair_financial_cashflow_with_logs.py',
                        'run_id': run_id,
                    },
                )
                fixed += 1
        else:
            fixed += len(changes)

    return {'rows_touched': touched_rows, 'fields_fixed': fixed}


def repair_cashflow(conn: sqlite3.Connection, run_id: str, dry_run: bool) -> dict:
    # 현금흐름은 핵심 3축 + capex/depreciation NULL을 0으로 채우지 않음.
    # 대신 숫자 타입 오염/비정상 값만 로그 기반 정리(현재는 점검 전용).
    rows = conn.execute(
        """
        SELECT id, stock_code, year, quarter, is_annual, report_type,
               operating_cf, investing_cf, financing_cf, capex, depreciation
        FROM cash_flow_data
        WHERE report_type='CFS'
        """
    ).fetchall()
    anomalies = 0
    for r in rows:
        rid, code, y, q, ia, rt, ocf, icf, fcf, capex, dep = r
        for field_name, val in [('operating_cf', ocf), ('investing_cf', icf), ('financing_cf', fcf), ('capex', capex), ('depreciation', dep)]:
            if val is None:
                continue
            try:
                float(val)
            except Exception:
                anomalies += 1
                if not dry_run:
                    now = datetime.now().isoformat(timespec='seconds')
                    log_fix(
                        conn,
                        'cashflow_fix_log',
                        {
                            'fixed_at': now,
                            'row_id': rid,
                            'stock_code': code,
                            'year': y,
                            'quarter': q,
                            'is_annual': ia,
                            'report_type': rt,
                            'field_name': field_name,
                            'old_value': None,
                            'new_value': None,
                            'fix_rule': 'non_numeric_detected',
                            'source': 'scripts/ops/repair_financial_cashflow_with_logs.py',
                            'run_id': run_id,
                        },
                    )
    return {'anomalies_logged': anomalies}


def summary(conn: sqlite3.Connection) -> dict:
    s = {}
    for ia in (0, 1):
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS n,
              SUM(CASE WHEN total_assets IS NULL THEN 1 ELSE 0 END) AS assets_null,
              SUM(CASE WHEN total_liabilities IS NULL THEN 1 ELSE 0 END) AS liab_null,
              SUM(CASE WHEN total_equity IS NULL THEN 1 ELSE 0 END) AS eq_null,
              SUM(CASE WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL
                        AND ABS(total_assets-total_liabilities) < 1 THEN 1 ELSE 0 END) AS assets_eq_liab,
              SUM(CASE WHEN total_assets IS NOT NULL AND total_equity IS NOT NULL
                        AND ABS(total_assets-total_equity) < 1 THEN 1 ELSE 0 END) AS assets_eq_eq,
              SUM(CASE WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
                        AND ABS((total_liabilities + total_equity) - total_assets) > 1000 THEN 1 ELSE 0 END) AS identity_mismatch
            FROM financial_data
            WHERE report_type='CFS' AND is_annual=?
            """,
            (ia,),
        ).fetchone()
        s[f'is_annual_{ia}'] = dict(zip([c[0] for c in conn.execute("PRAGMA table_info(financial_data)").fetchall()[:0]], []))
        s[f'is_annual_{ia}'] = {
            'n': row[0], 'assets_null': row[1], 'liab_null': row[2], 'eq_null': row[3],
            'assets_eq_liab': row[4], 'assets_eq_eq': row[5], 'identity_mismatch': row[6],
        }
    return s


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(DB_PATH))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    conn = sqlite3.connect(args.db)
    try:
        before = summary(conn)
        create_tables(conn)
        fin = repair_financial(conn, run_id, args.dry_run)
        cf = repair_cashflow(conn, run_id, args.dry_run)
        after = summary(conn)
        if not args.dry_run:
            conn.commit()

        payload = {
            'run_id': run_id,
            'db': args.db,
            'dry_run': args.dry_run,
            'before': before,
            'after': after,
            'financial_repair': fin,
            'cashflow_repair': cf,
        }
        out = OUT_DIR / f'financial_cashflow_repair_{run_id}.json'
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(out)
    finally:
        conn.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
