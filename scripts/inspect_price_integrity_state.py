#!/usr/bin/env python3
"""Read-only operational receipt for the price-integrity controls."""
from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


def rows(conn, sql, params=()):
    return [tuple(row) for row in conn.execute(sql, params).fetchall()]


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--deep',action='store_true',help='include the expensive 1.85M-row Naver join')
    parser.add_argument('--indexes-only',action='store_true')
    args=parser.parse_args()
    conn = connect_primary_db(timeout=120)
    try:
        if args.indexes_only:
            print(json.dumps(rows(conn,"""SELECT indexname,indexdef FROM pg_indexes
                WHERE schemaname='public' AND tablename='price_history' ORDER BY indexname"""),indent=2))
            return
        report = {
            "write_guard": rows(conn, """SELECT action_timing,event_manipulation
                FROM information_schema.triggers
                WHERE trigger_schema='public' AND trigger_name='price_history_basis_write_guard'"""),
            "price_history_indexes": rows(conn, """SELECT indexname,indexdef FROM pg_indexes
                WHERE schemaname='public' AND tablename='price_history' ORDER BY indexname"""),
            "quarantine_reasons": rows(conn, """SELECT reason,COUNT(*),MIN(created_at),MAX(created_at)
                FROM price_integrity_quarantine GROUP BY reason ORDER BY COUNT(*) DESC"""),
            "unverified_write_hours": rows(conn, """SELECT substr(created_at,1,13),COUNT(*),COUNT(DISTINCT stock_code)
                FROM price_integrity_quarantine WHERE reason='unverified_historical_write'
                GROUP BY 1 ORDER BY 1"""),
            "unverified_write_hour_ranges": rows(conn, """SELECT substr(created_at,1,13),MIN(event_date),MAX(event_date),
                       COUNT(DISTINCT substr(event_date,1,4))
                FROM price_integrity_quarantine WHERE reason='unverified_historical_write'
                GROUP BY 1 ORDER BY 1"""),
            "audit_classes": rows(conn, """SELECT classification,COUNT(*),SUM(return_usable)
                FROM price_jump_audit GROUP BY classification ORDER BY COUNT(*) DESC"""),
            "fix_log": rows(conn, """SELECT run_id,row_count,fixed_at,source
                FROM data_fix_log WHERE table_name='price_history' ORDER BY fixed_at DESC LIMIT 30"""),
            "backup_runs": rows(conn, """SELECT run_id,COUNT(*),COUNT(DISTINCT stock_code),MIN(date),MAX(date)
                FROM price_history_fix_backup GROUP BY run_id ORDER BY run_id"""),
            "snapshot_repair_runs": rows(conn, """SELECT batch_id,status,COUNT(*),SUM((details::jsonb->>'changed_rows')::int)
                FROM price_snapshot_repair_runs GROUP BY batch_id,status ORDER BY batch_id,status"""),
            "top_jump_dates": rows(conn, """SELECT substr(date,1,10),COUNT(*)
                FROM price_history_quality_v WHERE quality_status='unexplained_jump'
                GROUP BY 1 HAVING COUNT(*)>=5 ORDER BY COUNT(*) DESC,1 LIMIT 30"""),
        }
        if args.deep:
            report["unverified_naver_agreement"] = rows(conn, """SELECT
                COUNT(*) total,COUNT(n.stock_code) staged_overlap,
                COUNT(*) FILTER (WHERE n.stock_code IS NOT NULL
                  AND abs(p.open-n.open)<0.000001 AND abs(p.high-n.high)<0.000001
                  AND abs(p.low-n.low)<0.000001 AND abs(p.close-n.close)<0.000001
                  AND abs(p.volume-n.volume)<0.000001) exact_ohlcv
                FROM price_integrity_quarantine q
                JOIN price_history p ON p.stock_code=q.stock_code AND substr(p.date,1,10)=q.event_date
                LEFT JOIN naver_price_history_backfill n ON n.stock_code=q.stock_code AND n.date=q.event_date
                WHERE q.reason='unverified_historical_write'""")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    main()
