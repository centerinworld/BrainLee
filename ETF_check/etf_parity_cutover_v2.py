"""Five-day cutover evaluator using an exact K-ETF-only stratified sample."""
from __future__ import annotations
import argparse,json,math,statistics
from datetime import datetime
from pathlib import Path
from etf_parity_cutover import REQUIRED_PASS_DAYS,THRESHOLDS,_correlation,initialize
from full_pdf_collector import DB_PATH,connect

MIN_SAMPLE=60
MIN_SAMPLE_COVERAGE=.90

def evaluate(conn,day=None):
    initialize(conn);selected=day or conn.execute("SELECT MAX(base_date) FROM etfcheck_k_sample_daily").fetchone()[0]
    if not selected:raise RuntimeError("No K-ETF sample snapshot")
    universe=conn.execute("SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?",(selected,)).fetchone()[0]
    successful=conn.execute("SELECT COUNT(*) FROM etf_pdf_full_snapshot WHERE base_date=? AND status='success'",(selected,)).fetchone()[0]
    scale=conn.execute("SELECT COUNT(*) FROM etf_scale_daily WHERE base_date=?",(selected,)).fetchone()[0]
    sample_expected=conn.execute("SELECT COUNT(*) FROM etfcheck_k_sample_universe").fetchone()[0]
    legacy={r[0]:{"count":int(r[1]),"amount":float(r[2])} for r in conn.execute("SELECT stock_code,etf_count,etf_amount FROM etfcheck_k_sample_daily WHERE base_date=? AND status='success'",(selected,))}
    direct={r[0]:{"count":int(r[1]),"amount":float(r[2] or 0)} for r in conn.execute("""SELECT c.component_code,COUNT(DISTINCT c.etf_ticker),SUM(c.valuation_amount*d.scale_factor)/100000000.0 FROM etf_pdf_full_component c JOIN etf_pdf_full_snapshot s ON s.base_date=c.base_date AND s.etf_ticker=c.etf_ticker JOIN etf_scale_daily d ON d.base_date=c.base_date AND d.etf_ticker=c.etf_ticker WHERE c.base_date=? AND s.status='success' AND c.component_code IN (SELECT stock_code FROM etfcheck_k_sample_daily WHERE base_date=?) GROUP BY c.component_code""",(selected,selected))}
    codes=set(legacy);oldpos={c for c in codes if legacy[c]["count"]>0};newpos={c for c in codes if direct.get(c,{"count":0})["count"]>0};union=oldpos|newpos;overlap=oldpos&newpos
    pairs=[(direct.get(c,{"count":0})["count"],legacy[c]["count"]) for c in codes];exact=sum(a==b for a,b in pairs)/max(len(pairs),1);within=sum(abs(a-b)<=1 for a,b in pairs)/max(len(pairs),1);jaccard=len(overlap)/max(len(union),1)
    ap=[(direct.get(c,{"amount":0})["amount"],legacy[c]["amount"]) for c in codes if direct.get(c,{"amount":0})["amount"]>=1 and legacy[c]["amount"]>=1]
    corr=_correlation([math.log1p(a) for a,_ in ap],[math.log1p(b) for _,b in ap]);ratio=sum(a for a,_ in ap)/sum(b for _,b in ap) if ap and sum(b for _,b in ap) else None;smape=statistics.median([abs(a-b)/((a+b)/2) for a,b in ap]) if ap else None
    coverage=min(successful,scale)/max(universe,1);sample_coverage=len(legacy)/max(sample_expected,1)
    published=conn.execute("SELECT 1 FROM etf_pdf_full_publication WHERE base_date=? AND universe_count=?",(selected,universe)).fetchone() is not None
    metrics={"comparison_scope":"stratified_k_etf_sample","sample_size":len(legacy),"sample_coverage":sample_coverage,"complete_publication":published,"new_coverage_ratio":coverage,"membership_jaccard":jaccard,"count_exact_ratio":exact,"count_within_one_ratio":within,"amount_correlation":corr,"amount_total_ratio":ratio,"amount_median_smape":smape}
    failures=[]
    if not published:failures.append("complete_publication")
    if sample_expected<MIN_SAMPLE or sample_coverage<MIN_SAMPLE_COVERAGE:failures.append("sample_coverage")
    for key in ("new_coverage_ratio","membership_jaccard","count_within_one_ratio","amount_correlation"):
        if metrics[key] is None or metrics[key]<THRESHOLDS[key]:failures.append(key)
    if ratio is None or not THRESHOLDS["amount_total_ratio_min"]<=ratio<=THRESHOLDS["amount_total_ratio_max"]:failures.append("amount_total_ratio")
    if smape is None or smape>THRESHOLDS["amount_median_smape_max"]:failures.append("amount_median_smape")
    passed=not failures;now=datetime.now().isoformat(timespec="seconds")
    values=(selected,selected,universe,successful,scale,coverage,len(oldpos),len(newpos),len(overlap),jaccard,len(pairs),exact,within,len(ap),corr,ratio,smape,int(passed),json.dumps(failures,ensure_ascii=False),json.dumps(metrics,ensure_ascii=False),now)
    with conn:
        conn.execute("INSERT OR REPLACE INTO etf_source_parity_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
        rows=conn.execute("SELECT passed FROM etf_source_parity_daily ORDER BY base_date DESC LIMIT ?",(REQUIRED_PASS_DAYS,)).fetchall();consecutive=0
        for r in rows:
            if not r[0]:break
            consecutive+=1
        mode=conn.execute("SELECT mode FROM etf_source_control WHERE control_id=1").fetchone()[0];new_mode=mode;cutover=None
        if mode in ('legacy_validation','legacy_fallback') and consecutive>=REQUIRED_PASS_DAYS:new_mode='krx_primary';cutover=now
        elif mode=='krx_primary' and len(rows)>=2 and not rows[0][0] and not rows[1][0]:new_mode='legacy_fallback'
        conn.execute("UPDATE etf_source_control SET mode=?,consecutive_pass_days=?,cutover_at=COALESCE(?,cutover_at),last_evaluated_date=?,last_failure=?,updated_at=? WHERE control_id=1",(new_mode,consecutive,cutover,selected,','.join(failures) or None,now))
    return {"base_date":selected,"passed":passed,"failures":failures,"metrics":metrics,"consecutive_pass_days":consecutive,"mode":new_mode}

def main():
    p=argparse.ArgumentParser();p.add_argument('--date');p.add_argument('--db',default=str(DB_PATH));a=p.parse_args();c=connect(Path(a.db));print(json.dumps(evaluate(c,a.date),ensure_ascii=False,indent=2));c.close()
if __name__=='__main__':main()
