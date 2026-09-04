"""Human-approved investment decision tasks; never connected to order execution."""
from __future__ import annotations
import hashlib, json, os, sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
from fastapi import APIRouter, BackgroundTasks, HTTPException
from db_utils import connect_stock_db
from routes.company_intelligence import _compute_company_intelligence
from services.decision_llm import deepseek_flash, deepseek_review, gpt_review, is_deepseek_offpeak

router = APIRouter(); KST = ZoneInfo("Asia/Seoul")

def _tables(c):
    c.execute("CREATE TABLE IF NOT EXISTS investment_decision_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, status TEXT NOT NULL, packet_json TEXT NOT NULL, rag_json TEXT, created_at TEXT NOT NULL, completed_at TEXT, error_text TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS investment_decision_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, provider TEXT NOT NULL, model_name TEXT, status TEXT NOT NULL, result_json TEXT, error_text TEXT, created_at TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS investment_decision_rag_cache (packet_hash TEXT PRIMARY KEY, model_name TEXT NOT NULL, rag_json TEXT NOT NULL, created_at TEXT NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_decision_task_stock ON investment_decision_tasks(stock_code, created_at DESC)"); c.commit()

def _offpeak():
    return is_deepseek_offpeak()

def _packet(intel: dict[str, Any]):
    ev=intel.get("evidence") or {}; docs=[]
    for i,x in enumerate((ev.get("reports") or [])[:10],1): docs.append({"id":f"R{i}","type":"report","date":x.get("date"),"source":x.get("file_name"),"text":str(x.get("caption") or "")[:1200]})
    for i,x in enumerate((ev.get("messages") or [])[:14],1): docs.append({"id":f"M{i}","type":"telegram","date":x.get("date"),"source":x.get("channel"),"text":str(x.get("text") or "")[:700]})
    return {"stock":{k:intel.get(k) for k in ("stock_code","stock_name","market_cap_억","current_price","price_date","sector","industry","latest_financial")},"profile":{k:intel.get(k) for k in ("business_model","cycle_type","main_products","differentiations","bull_points","bear_points","analyst_view")},"documents":docs}

def _run(task_id:int):
    c=connect_stock_db(timeout=180,row_factory=sqlite3.Row)
    try:
        row=c.execute("SELECT * FROM investment_decision_tasks WHERE id=?",(task_id,)).fetchone(); packet=json.loads(row["packet_json"])
        if not _offpeak():
            c.execute("UPDATE investment_decision_tasks SET status=?,error_text=? WHERE id=?",("waiting_offpeak","DeepSeek 전처리는 평일 10-13시·15-19시 KST 외에 실행됩니다.",task_id)); c.commit(); return
        packet_hash=hashlib.sha256(json.dumps(packet,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        cached=c.execute("SELECT rag_json FROM investment_decision_rag_cache WHERE packet_hash=?",(packet_hash,)).fetchone()
        if cached:
            rag=json.loads(cached["rag_json"])
        else:
            rag_prompt="문서에서 사실·수치·주장만 추출하라. JSON {claims:[{category,statement,document_ids,confidence}], missing_evidence:[]}\n"+json.dumps(packet,ensure_ascii=False)
            model,rag=deepseek_flash(rag_prompt)
            c.execute("INSERT INTO investment_decision_rag_cache(packet_hash,model_name,rag_json,created_at) VALUES(?,?,?,?)",(packet_hash,model,json.dumps(rag,ensure_ascii=False),datetime.now().isoformat())); c.commit()
        c.execute("UPDATE investment_decision_tasks SET status=?,rag_json=? WHERE id=?",("reviewing",json.dumps(rag,ensure_ascii=False),task_id)); c.commit()
        schema={"verdict":"candidate|watch|hold|reject","filters":{"growth":{"status":"pass|watch|fail","reason":"","evidence_ids":[]},"moat":{"status":"pass|watch|fail","reason":"","evidence_ids":[]},"catalyst":{"status":"pass|watch|fail","reason":"","evidence_ids":[]},"valuation":{"status":"pass|watch|fail","reason":"","discount_source":"market|industry|company|unknown","evidence_ids":[]}},"countercase":[{"statement":"","evidence_ids":[]}],"invalidations":[""],"next_checks":[""],"confidence":"high|medium|low","auto_trade":False}
        prompt="투자원칙: 성장 기울기, 경제적 해자, 촉매, 그리고 가장 중요한 소외된 싼 주가의 교집합만 후보로 본다. 단순 딥밸류는 제외한다. 근거 ID 없는 주장은 금지한다. JSON 스키마="+json.dumps(schema,ensure_ascii=False)+"\n자료="+json.dumps({"stock":packet["stock"],"profile":packet["profile"],"rag":rag},ensure_ascii=False)
        deepseek_result=None
        try:
            model,deepseek_result=deepseek_review(prompt)
            values=(task_id,"deepseek",model,"completed",json.dumps(deepseek_result,ensure_ascii=False),None,datetime.now().isoformat())
        except Exception as e:
            values=(task_id,"deepseek",None,"failed",None,str(e)[:800],datetime.now().isoformat())
        c.execute("INSERT INTO investment_decision_reviews(task_id,provider,model_name,status,result_json,error_text,created_at) VALUES(?,?,?,?,?,?,?)",values); c.commit()
        mode=os.getenv("DECISION_GPT_REVIEW_MODE","never").lower()
        needs_gpt=mode=="always"
        if needs_gpt:
            try: model,result=gpt_review(prompt); values=(task_id,"openai",model,"completed",json.dumps(result,ensure_ascii=False),None,datetime.now().isoformat())
            except Exception as e: values=(task_id,"openai",None,"failed",None,str(e)[:800],datetime.now().isoformat())
            c.execute("INSERT INTO investment_decision_reviews(task_id,provider,model_name,status,result_json,error_text,created_at) VALUES(?,?,?,?,?,?,?)",values); c.commit()
        c.execute("UPDATE investment_decision_tasks SET status=?,completed_at=? WHERE id=?",("completed",datetime.now().isoformat(),task_id)); c.commit()
    except Exception as e: c.execute("UPDATE investment_decision_tasks SET status=?,error_text=? WHERE id=?",("failed",str(e)[:800],task_id)); c.commit()
    finally: c.close()

def resume_waiting_tasks(limit:int=10) -> int:
    """Run tasks held during DeepSeek peak pricing once the off-peak window opens."""
    if not _offpeak(): return 0
    c=connect_stock_db(timeout=30,row_factory=sqlite3.Row)
    try:
        _tables(c)
        rows=c.execute("SELECT id FROM investment_decision_tasks WHERE status=? ORDER BY id LIMIT ?",("waiting_offpeak",limit)).fetchall()
        for row in rows: _run(int(row["id"]))
        return len(rows)
    finally: c.close()

@router.post("/tasks/{stock_code}")
def create_task(stock_code:str, background_tasks:BackgroundTasks):
    c=connect_stock_db(timeout=30,row_factory=sqlite3.Row)
    try:
        _tables(c); intel=_compute_company_intelligence(c,stock_code)
        if not intel.get("found"): raise HTTPException(404,"종목을 찾을 수 없습니다.")
        packet=_packet(intel); cur=c.execute("INSERT INTO investment_decision_tasks(stock_code,stock_name,status,packet_json,created_at) VALUES(?,?,?,?,?)",(stock_code,intel["stock_name"],"queued",json.dumps(packet,ensure_ascii=False),datetime.now().isoformat())); c.commit(); task_id=cur.lastrowid
    finally: c.close()
    background_tasks.add_task(_run,task_id); return {"task_id":task_id,"status":"queued","offpeak":_offpeak(),"auto_trade":False}

@router.get("/tasks/{stock_code}/latest")
def latest_task(stock_code:str):
    c=connect_stock_db(timeout=30,row_factory=sqlite3.Row)
    try:
        _tables(c); task=c.execute("SELECT * FROM investment_decision_tasks WHERE stock_code=? ORDER BY id DESC LIMIT 1",(stock_code,)).fetchone()
        if not task:return {"task":None,"reviews":[]}
        reviews=c.execute("SELECT provider,model_name,status,result_json,error_text,created_at FROM investment_decision_reviews WHERE task_id=? ORDER BY id",(task["id"],)).fetchall()
        return {"task":dict(task),"reviews":[{**dict(x),"result":json.loads(x["result_json"]) if x["result_json"] else None} for x in reviews]}
    finally:c.close()
