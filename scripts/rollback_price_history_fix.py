#!/usr/bin/env python3
"""Rollback one logged OHLCV fix run after verifying its current new values."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


def same(left, right):
    return all(a is not None and b is not None and abs(float(a)-float(b)) <= 1e-6
               for a,b in zip(left,right))


def run(run_id: str, apply: bool = False) -> dict:
    conn = connect_primary_db(timeout=60)
    try:
        backups = conn.execute("""SELECT stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                new_open,new_high,new_low,new_close,new_volume
            FROM price_history_fix_backup WHERE run_id=? ORDER BY stock_code,date""",(run_id,)).fetchall()
        ready, skipped = [], {}
        for row in backups:
            current = conn.execute("""SELECT open,high,low,close,volume FROM price_history
                WHERE stock_code=? AND substr(date,1,10)=?""",(row[0],row[1])).fetchone()
            status = 'ready' if current and same(tuple(current),tuple(row[7:12])) else 'current_no_longer_matches_fix'
            if status == 'ready': ready.append(tuple(row))
            else: skipped[status]=skipped.get(status,0)+1
        result={'run_id':run_id,'backup_rows':len(backups),'ready_rows':len(ready),
                'skipped_rows':skipped,'applied':apply}
        if not apply:
            return result
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        for row in ready:
            conn.execute("""UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
                WHERE stock_code=? AND substr(date,1,10)=?""",(*row[2:7],row[0],row[1]))
        conn.execute("""INSERT INTO data_fix_log
            (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES(?,?,?,?,?,?,?,?,?)""",(
                datetime.now().isoformat(timespec='seconds'),'price_history',f'rollback of {run_id}',len(ready),
                'restore old OHLCV only when current OHLCV still exactly matches the backed-up replacement',
                'replacement values from price_history_fix_backup','old values from price_history_fix_backup',
                'price_history_fix_backup',f'rollback_{run_id}'))
        conn.commit()
        return result
    finally:
        conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(run(args.run_id,args.apply),ensure_ascii=False,indent=2))
