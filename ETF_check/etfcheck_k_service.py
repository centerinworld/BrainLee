"""On-demand ETF Check query with K-ETF enabled and US-ETF disabled."""
from __future__ import annotations
import time
from playwright.sync_api import sync_playwright
import collector as legacy
from etfcheck_k_sample_collector import ensure_k_only,num

def fetch_summary(stock_code:str)->dict:
    with sync_playwright() as p:
        browser,ctx=legacy.create_session_context(p);page=ctx.new_page()
        try:
            page.goto(f"{legacy.BASE_URL}/mobile/searchPdf/{stock_code}",wait_until="domcontentloaded",timeout=25000);time.sleep(2.2)
            if legacy._is_session_expired(page,stock_code):raise PermissionError("ETF Check session expired")
            ensure_k_only(page);raw=legacy._extract_summary_fields(page,stock_code)
            parsed=page.evaluate("""() => { const lines=document.body.innerText.split('\\n').map(x=>x.trim()).filter(Boolean); const out={ratio:null,amount:null}; for(let i=0;i<lines.length;i++){ if(lines[i]==='비중 TOP'&&i+1<lines.length){let value=null;for(let j=i+2;j<=i+4&&j<lines.length;j++){if(/^[\\d.]+%$/.test(lines[j])){value=lines[j];break;}}out.ratio={name:lines[i+1],value};} if(lines[i]==='투자금액 TOP'&&i+1<lines.length){const p=lines[i+1].split(' | ');out.amount={name:p[0].trim(),value:p[1]?p[1].trim():null};}} return out;}""")
            count=num(raw.get("count"));amount=num(raw.get("etf_amount"));
            if count is None or amount is None:raise RuntimeError("K-ETF summary missing")
            items=[]
            if count > 0 and parsed.get("ratio") and parsed["ratio"].get("value"):
                items.append({"label":"비중 1위","name":parsed["ratio"]["name"],"value":parsed["ratio"]["value"],"type":"ratio"})
            if count > 0 and parsed.get("amount") and parsed["amount"].get("value"):
                items.append({"label":"편입금액 1위","name":parsed["amount"]["name"],"value":parsed["amount"]["value"],"type":"amount"})
            return {"stock_code":stock_code,"stock_name":raw.get("name"),"etf_count":int(count),"etf_amount_total":amount,"etf_list":items,"note":f"총 {int(count)}개 국내 ETF 편입 (ETF Check K-ETF 범위)","source":"ETFCHECK_K_ONLY"}
        finally:browser.close()
