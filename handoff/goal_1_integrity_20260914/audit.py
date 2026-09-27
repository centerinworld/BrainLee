import sys,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
OUT=Path(__file__).resolve().parent
sys.path.insert(0,'/Volumes/Realtek_NVME/stock_dashboard/runtime')
from config import IS_POSTGRES
from db_compat import connect_primary_db
assert IS_POSTGRES, 'No SQLite fallback'
c=connect_primary_db(readonly=True)
queries={
'schema':"SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='price_history' ORDER BY ordinal_position",
'inventory':"SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name",
'profile':"""SELECT count(*) n,count(DISTINCT stock_code) stocks,min(date) min_date,max(date) max_date,
count(*) FILTER(WHERE stock_code IS NULL OR btrim(stock_code)='' OR date IS NULL OR btrim(date)='') missing_key,
count(*) FILTER(WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL) missing_ohlcv,
count(*) FILTER(WHERE open::text IN ('NaN','Infinity','-Infinity') OR high::text IN ('NaN','Infinity','-Infinity') OR low::text IN ('NaN','Infinity','-Infinity') OR close::text IN ('NaN','Infinity','-Infinity') OR volume::text IN ('NaN','Infinity','-Infinity')) nonfinite,
count(*) FILTER(WHERE open<0 OR high<0 OR low<0 OR close<0 OR volume<0) negative,
count(*) FILTER(WHERE high<low OR high<open OR high<close OR low>open OR low>close) ohlc_order,
count(*) FILTER(WHERE open=0 OR high=0 OR low=0 OR close=0) zero_price_review,
count(*) FILTER(WHERE date !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') noncanonical_date,
count(*) FILTER(WHERE substr(date,1,10)>to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD')) future_date_candidate,
count(*) FILTER(WHERE created_at IS NULL OR btrim(created_at)='') missing_created_at
FROM price_history""",
'duplicates':"SELECT count(*) groups,coalesce(sum(n-1),0) excess_rows FROM (SELECT stock_code,substr(date,1,10),count(*) n FROM price_history GROUP BY 1,2 HAVING count(*)>1) x",
'anomaly_samples':"SELECT id,stock_code,date,open,high,low,close,volume FROM price_history WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL OR high<low OR high<open OR high<close OR low>open OR low>close OR open<0 OR close<0 OR volume<0 ORDER BY stock_code,date,id LIMIT 30",
'zero_samples':"SELECT id,stock_code,date,open,high,low,close,volume FROM price_history WHERE open=0 OR high=0 OR low=0 OR close=0 ORDER BY stock_code,date,id LIMIT 10",
'indexes':"SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='price_history' ORDER BY indexname"
}
report={'scope':'All stored price_history rows; missing expected trading rows, units and PIT NOT certified','started_utc':datetime.now(timezone.utc).isoformat(),'queries':queries,'results':{}}
try:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 c.execute("SET LOCAL statement_timeout = '90s'")
 c.execute("SET LOCAL lock_timeout = '3s'")
 report['transaction']=[dict(r) for r in c.execute("SELECT version(),current_setting('transaction_read_only') read_only,current_setting('transaction_isolation') isolation,txid_current_snapshot()::text snapshot,CURRENT_TIMESTAMP as audit_time").fetchall()]
 for name,sql in queries.items():
  report['results'][name]=[dict(r) for r in c.execute(sql).fetchall()]
  print(name,json.dumps(report['results'][name] if name in ('profile','duplicates') else {'rows':len(report['results'][name])},default=str),flush=True)
finally:
 c.rollback();c.close()
 report['ended_utc']=datetime.now(timezone.utc).isoformat()
 report['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
 (OUT/'db_evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
