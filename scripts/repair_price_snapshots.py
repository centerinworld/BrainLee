#!/usr/bin/env python3
"""Repair research OHLCV using one complete, validated Naver snapshot per code.
No mixed partial backfill. Every overwritten value has an immutable DB backup.
Rejected snapshots are retained on disk for review, not silently discarded.
"""
import argparse
import gzip
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import psycopg
from config import DATABASE_URL
from price_integrity import invalid_ohlcv, outside_band
from scripts.backfill_naver_ohlcv_2015_2018 import fetch

DDL='''
CREATE TABLE IF NOT EXISTS price_snapshot_repair_backup(
 batch_id text,stock_code text,date text,old_open double precision,old_high double precision,
 old_low double precision,old_close double precision,old_volume double precision,
 PRIMARY KEY(batch_id,stock_code,date));
CREATE TABLE IF NOT EXISTS price_snapshot_repair_runs(
 batch_id text,stock_code text,status text,details text,updated_at text,
 PRIMARY KEY(batch_id,stock_code));
CREATE TABLE IF NOT EXISTS price_snapshot_provenance(
 stock_code text PRIMARY KEY,source text,batch_id text,first_date text,last_date text,
 verified_at text);
CREATE TABLE IF NOT EXISTS price_snapshot_repair_insertions(
 batch_id text,stock_code text,date text,PRIMARY KEY(batch_id,stock_code,date));
'''

def validate(rows, existing):
    if len(rows)<2:return 'insufficient_source_history'
    days={r[1] for r in rows}
    if len(days)!=len(rows):return 'duplicate_source_dates'
    if any(invalid_ohlcv(*r[2:7]) for r in rows):return 'invalid_source_ohlcv'
    if any(outside_band(a[5],b[5],b[1]) for a,b in zip(rows,rows[1:])):
        return 'source_jump_requires_event_review'
    if any(r[0] not in days for r in existing):return 'incomplete_source_coverage'
    return None


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true')
    ap.add_argument('--codes',default='');ap.add_argument('--workers',type=int,default=6)
    ap.add_argument('--resume',default='');args=ap.parse_args()
    if args.apply and not args.codes:
        ap.error('--apply requires an explicit comma-separated --codes review set')
    batch=args.resume or datetime.now().strftime('%Y%m%dT%H%M%S')
    out=ROOT/'research_outputs'/'price_snapshot_repair'/batch;out.mkdir(parents=True,exist_ok=True)
    c=psycopg.connect(DATABASE_URL.replace('postgresql+psycopg:','postgresql:'),autocommit=True)
    if args.apply:
        c.execute(DDL)
        c.execute('''CREATE TEMP TABLE incoming_price_snapshot(stock_code text,date text,
            open double precision,high double precision,low double precision,close double precision,volume double precision)''')
    codes=sorted(code.strip() for code in set(args.codes.split(',')) if code.strip()) if args.codes else [r[0] for r in c.execute(
        "SELECT DISTINCT stock_code FROM price_history WHERE stock_code ~ '^[0-9]{6}$' ORDER BY stock_code")]
    starts={r[0]:str(r[1]).replace('-','') for r in c.execute(
        "SELECT stock_code,MIN(date) FROM price_history WHERE stock_code = ANY(%s) GROUP BY stock_code",(codes,))}
    result={'batch_id':batch,'apply':args.apply,'target_codes':len(codes),'processed':0,
            'ready':0,'ready_changed_rows':0,'ready_missing_rows':0,
            'applied':0,'applied_changed_rows':0,'applied_inserted_rows':0,
            'unchanged':0,'rejected_changed_rows':0,'rejected':{},'errors':{}}
    def get(code):
        path=out/(code+'.json.gz')
        if path.exists():return code,json.loads(gzip.decompress(path.read_bytes())),None
        code,rows,error=fetch(code,starts.get(code,'20100101'),datetime.now().strftime('%Y%m%d'))
        rows=sorted(rows,key=lambda r:r[1])
        if not error:path.write_bytes(gzip.compress(json.dumps(rows).encode()))
        return code,rows,error
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        futures={pool.submit(get,code):code for code in codes}
        for i,future in enumerate(as_completed(futures),1):
            code=futures[future]
            try:
                code,rows,error=future.result()
            except Exception as exc:
                result['errors'][code]=f'{type(exc).__name__}: {exc}'
                result['processed']+=1
                continue
            if error:
                result['errors'][code]=error;result['processed']+=1;continue
            # Refuse leaving a source-incompatible tail or head in the same code.
            existing=list(c.execute('SELECT date,open,high,low,close,volume FROM price_history WHERE stock_code=%s ORDER BY date',(code,)))
            reason=validate(rows,existing)
            source={r[1]:r for r in rows}
            changes=[r for r in existing if r[0] in source and tuple(r[1:])!=tuple(source[r[0]][2:7])]
            existing_days={r[0] for r in existing}
            first_day,last_day=(existing[0][0],existing[-1][0]) if existing else ('','')
            missing=[r for r in rows if first_day <= r[1] <= last_day and r[1] not in existing_days]
            details={'rows':len(rows),'changed_rows':len(changes),'missing_rows':len(missing),
                     'reason':reason,'snapshot_file':code+'.json.gz'}
            status='rejected' if reason else ('ready' if changes or missing else 'unchanged')
            if reason:
                result['rejected'][code]=reason;result['rejected_changed_rows']+=len(changes)+len(missing)
            elif not changes and not missing:result['unchanged']+=1
            elif args.apply:
                with c.transaction():
                    c.execute("SELECT set_config('app.price_basis_checked','1',true)")
                    c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('price_repair:'+code,))
                    current=list(c.execute('SELECT date,open,high,low,close,volume FROM price_history WHERE stock_code=%s ORDER BY date FOR UPDATE',(code,)))
                    if current!=existing:
                        reason='concurrent_price_change';result['rejected'][code]=reason;status='rejected';details['reason']=reason
                    else:
                        c.execute('TRUNCATE incoming_price_snapshot')
                        with c.cursor().copy('COPY incoming_price_snapshot FROM STDIN') as cp:
                            for old in changes:cp.write_row(source[old[0]][:7])
                            for row in missing:cp.write_row(row[:7])
                        c.execute('''INSERT INTO price_snapshot_repair_backup
                            SELECT %s,p.stock_code,p.date,p.open,p.high,p.low,p.close,p.volume
                            FROM price_history p JOIN incoming_price_snapshot s USING(stock_code,date)
                            ON CONFLICT DO NOTHING''',(batch,))
                        c.execute('''UPDATE price_history p SET open=s.open,high=s.high,low=s.low,close=s.close,volume=s.volume
                            FROM incoming_price_snapshot s WHERE p.stock_code=s.stock_code AND p.date=s.date''')
                        c.execute('''INSERT INTO price_history(stock_code,date,open,high,low,close,volume)
                            SELECT s.stock_code,s.date,s.open,s.high,s.low,s.close,s.volume
                            FROM incoming_price_snapshot s
                            WHERE NOT EXISTS(SELECT 1 FROM price_history p WHERE p.stock_code=s.stock_code AND p.date=s.date)''')
                        if missing:
                            c.executemany('''INSERT INTO price_snapshot_repair_insertions VALUES(%s,%s,%s)
                                ON CONFLICT DO NOTHING''',[(batch,code,r[1]) for r in missing])
                        c.execute('''INSERT INTO price_snapshot_provenance VALUES(%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(stock_code) DO UPDATE SET source=excluded.source,batch_id=excluded.batch_id,
                            first_date=excluded.first_date,last_date=excluded.last_date,verified_at=excluded.verified_at''',
                            (code,'naver_date_bounded_complete_snapshot',batch,rows[0][1],rows[-1][1],datetime.now().isoformat()))
                        status='applied';result['applied']+=1
                        result['applied_changed_rows']+=len(changes)
                        result['applied_inserted_rows']+=len(missing)
            else:
                result['ready']+=1
                result['ready_changed_rows']+=len(changes)
                result['ready_missing_rows']+=len(missing)
            if args.apply:
                c.execute('''INSERT INTO price_snapshot_repair_runs VALUES(%s,%s,%s,%s,%s)
                    ON CONFLICT(batch_id,stock_code) DO UPDATE SET status=excluded.status,details=excluded.details,updated_at=excluded.updated_at''',
                    (batch,code,status,json.dumps(details),datetime.now().isoformat()))
            result['processed']+=1
            if i%50==0:
                (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
                print(json.dumps({'progress':i,'total':len(codes),'ready':result['ready'],'applied':result['applied'],
                                  'rejected':len(result['rejected']),'errors':len(result['errors'])}),flush=True)
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v if not isinstance(v,dict) else len(v) for k,v in result.items()}),flush=True)
    c.close()
if __name__=='__main__':main()
