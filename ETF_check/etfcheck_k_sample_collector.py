"""Collect a deterministic K-ETF-only legacy sample for direct-source parity."""
from __future__ import annotations
import argparse,json,re,time,sqlite3
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright
import collector as legacy
from direct_etf_pipeline import trading_date
from full_pdf_collector import DB_PATH,connect

SAMPLE_SIZE=80

def initialize(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS etfcheck_k_sample_universe(stock_code TEXT PRIMARY KEY,selection_reason TEXT NOT NULL,selected_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS etfcheck_k_sample_daily(base_date TEXT NOT NULL,stock_code TEXT NOT NULL,stock_name TEXT,etf_count INTEGER NOT NULL,etf_amount REAL NOT NULL,market_cap REAL,mktcap_ratio REAL,status TEXT NOT NULL,collected_at TEXT NOT NULL,PRIMARY KEY(base_date,stock_code));
    CREATE TABLE IF NOT EXISTS etfcheck_k_sample_run(run_id INTEGER PRIMARY KEY AUTOINCREMENT,base_date TEXT NOT NULL,attempted INTEGER NOT NULL,success INTEGER NOT NULL,error_count INTEGER NOT NULL,status TEXT NOT NULL,details_json TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT NOT NULL);
    """)

def select_sample(conn,day):
    initialize(conn); existing=[r[0] for r in conn.execute("SELECT stock_code FROM etfcheck_k_sample_universe ORDER BY stock_code")]
    if len(existing)>=SAMPLE_SIZE:return existing[:SAMPLE_SIZE]
    candidates=[]
    def add(rows,reason):
        for row in rows:candidates.append((row[0],reason))
    add(conn.execute("""SELECT component_code FROM etf_pdf_full_component c JOIN etf_scale_daily d ON d.base_date=c.base_date AND d.etf_ticker=c.etf_ticker WHERE c.base_date=? AND c.is_domestic_stock=1 GROUP BY component_code ORDER BY SUM(c.valuation_amount*d.scale_factor) DESC LIMIT 35""",(day,)),"direct_amount_top")
    add(conn.execute("""SELECT component_code FROM etf_pdf_full_component WHERE base_date=? AND is_domestic_stock=1 GROUP BY component_code ORDER BY COUNT(DISTINCT etf_ticker) DESC LIMIT 25""",(day,)),"direct_count_top")
    latest=conn.execute("SELECT MAX(trade_date) FROM etf_inclusion_daily WHERE scope_label='K-ETF'").fetchone()[0]
    if latest:add(conn.execute("SELECT stock_code FROM etf_inclusion_daily WHERE trade_date=? AND scope_label='K-ETF' ORDER BY etf_amount DESC LIMIT 30",(latest,)),"legacy_amount_top")
    add(conn.execute("SELECT stock_code FROM etf_stock_meta WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' ORDER BY stock_code"),"code_fill")
    chosen=[];seen=set()
    with conn:
        for code,reason in candidates:
            if code in seen:continue
            seen.add(code);chosen.append(code)
            conn.execute("INSERT OR IGNORE INTO etfcheck_k_sample_universe VALUES(?,?,?)",(code,reason,datetime.now().isoformat(timespec='seconds')))
            if len(chosen)>=SAMPLE_SIZE:break
    return chosen

def ensure_k_only(page):
    k=page.get_by_text("K-ETF",exact=True).first; u=page.get_by_text("US-ETF",exact=True).first
    if "inactive" in str(k.get_attribute("class") or ""):k.click();time.sleep(.8)
    if "inactive" not in str(u.get_attribute("class") or ""):u.click();time.sleep(.8)
    if "inactive" in str(k.get_attribute("class") or "") or "inactive" not in str(u.get_attribute("class") or ""):
        raise RuntimeError("K-ETF-only scope was not established")

def num(text):
    m=re.search(r"[\d,]+",str(text or ""));return float(m.group().replace(",","")) if m else None

def collect_one(page,code):
    page.goto(f"{legacy.BASE_URL}/mobile/searchPdf/{code}",wait_until="domcontentloaded",timeout=25000);time.sleep(2.2)
    if legacy._is_session_expired(page,code):raise PermissionError("ETF Check session expired")
    ensure_k_only(page); raw=legacy._extract_summary_fields(page,code)
    count=num(raw.get("count"));amount=num(raw.get("etf_amount"));cap=num(raw.get("mktcap"));ratio=num(raw.get("ratio"))
    if count is None or amount is None:raise RuntimeError("required K-ETF values missing")
    return {"stock_code":code,"stock_name":raw.get("name"),"etf_count":int(count),"etf_amount":amount,"market_cap":cap,"mktcap_ratio":ratio}

def collect(day,db_path=DB_PATH,limit=None):
    conn=connect(Path(db_path));codes=select_sample(conn,day);codes=codes[:limit] if limit else codes;started=datetime.now().isoformat(timespec="seconds");records=[];errors=[]
    with sync_playwright() as p:
        browser,ctx=legacy.create_session_context(p);page=ctx.new_page()
        try:
            for code in codes:
                try:records.append(collect_one(page,code))
                except Exception as exc:
                    if len(errors)<30:errors.append({"stock_code":code,"error":str(exc)})
                    if isinstance(exc,PermissionError):break
        finally:browser.close()
    now=datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.executemany("""INSERT INTO etfcheck_k_sample_daily VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(base_date,stock_code) DO UPDATE SET stock_name=excluded.stock_name,etf_count=excluded.etf_count,etf_amount=excluded.etf_amount,market_cap=excluded.market_cap,mktcap_ratio=excluded.mktcap_ratio,status='success',collected_at=excluded.collected_at""",[(day,r["stock_code"],r["stock_name"],r["etf_count"],r["etf_amount"],r["market_cap"],r["mktcap_ratio"],"success",now) for r in records])
        result={"base_date":day,"attempted":len(codes),"success":len(records),"errors":len(codes)-len(records),"error_samples":errors}
        conn.execute("INSERT INTO etfcheck_k_sample_run VALUES(NULL,?,?,?,?,?,?,?,?)",(day,len(codes),len(records),len(codes)-len(records),"complete" if len(records)==len(codes) else "partial",json.dumps(result,ensure_ascii=False),started,now))
    conn.close();return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--date");p.add_argument("--db",default=str(DB_PATH));p.add_argument("--limit",type=int);a=p.parse_args();print(json.dumps(collect(trading_date(a.date),a.db,a.limit),ensure_ascii=False,indent=2))
if __name__=="__main__":main()
