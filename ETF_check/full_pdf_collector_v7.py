"""Production KRX collector using the complete alphanumeric ETF universe."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import etf_universe_sync_v3 as universe
import full_pdf_collector as collector
import full_pdf_collector_v3 as v3
from full_pdf_collector_v4 import remove_sample_publication

_collect=collector.collect

def collect_complete(day:str,db_path:Path=collector.DB_PATH,raw_root:Path=collector.RAW_ROOT,delay:float=.35,retries:int=3,limit:int|None=None,force:bool=False,headless:bool=True)->dict[str,Any]:
    conn=collector.connect(db_path); universe_result=universe.get_or_sync_universe(conn,day); dated=universe.dated_universe(conn,day); conn.close()
    collector.active_etfs=lambda _conn:[collector.ETF(*row) for row in dated]
    result=_collect(day,db_path,raw_root,delay,retries,limit,force,headless); result["universe_sync"]=universe_result
    if limit is not None:
        remove_sample_publication(db_path,day,result.get("run_id")); result["complete"]=False; result["sample_only"]=True
    return result

collector.KRXSession=v3.CurrentKRXSession
collector.collect=collect_complete
if __name__=="__main__": collector.main()
