#!/usr/bin/env python3
"""읽기 전용: us_financial_data / us_cashflow_data 를 SEC 공식 XBRL(companyfacts)과 대조한다(2026-10-02 독립 재검토).

정답: 10-K(fp=FY, 기간 330~400일) 연간 값, 10-Q 3개월(기간 80~100일) 분기 손익. 재무상태는 해당 period_end 시점 값.
허용오차 max($1M, 0.5%). 표본: us_financial_data 에 2023년 이후 연간 행이 있는 티커 중 무작위 N개.
사용: venv/bin/python scripts/review/us_financial_vs_sec_20261002.py [--n 40]
"""
import argparse
import collections
import json
import random
import sys
import time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

UA = {"User-Agent": "stock-dashboard research center.in.world@gmail.com"}
TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}
FLOW = {"revenue", "net_income", "operating_income", "operating_cf", "capex"}
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"


def days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def sec_values(facts, field, end, annual):
    for tag in TAGS[field]:
        node = facts.get("us-gaap", {}).get(tag)
        if not node:
            continue
        for unit, arr in node.get("units", {}).items():
            if unit != "USD":
                continue
            vals = set()
            for f in arr:
                if f.get("end") != end:
                    continue
                if field in FLOW:
                    if not f.get("start"):
                        continue
                    d = days(f["start"], f["end"])
                    if annual and not (330 <= d <= 400):
                        continue
                    if not annual and not (80 <= d <= 100):
                        continue
                vals.add(float(f["val"]))
            if vals:
                return vals
    return set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    a = ap.parse_args()
    conn = connect_primary_db(timeout=120, readonly=True)
    tick = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM us_financial_data WHERE period_type='annual' AND period_end>='2023-01-01'").fetchall()]
    cik = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json().values()}
    random.seed(20261002)
    sample = random.sample([t for t in tick if t in cik], a.n)
    fin = conn.execute(f"SELECT ticker,period_end,period_type,revenue,net_income,operating_income,assets,liabilities,equity FROM us_financial_data "
                       f"WHERE ticker IN ({','.join('?'*len(sample))}) AND period_end>='2022-01-01'", sample).fetchall()
    cf = conn.execute(f"SELECT ticker,period_end,period_type,operating_cf,capex FROM us_cashflow_data "
                      f"WHERE ticker IN ({','.join('?'*len(sample))}) AND period_end>='2022-01-01'", sample).fetchall()
    conn.close()
    facts = {}
    for t in sample:
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]}.json", headers=UA, timeout=30)
        facts[t] = r.json().get("facts", {}) if r.ok else {}
        time.sleep(0.15)
    st = collections.Counter()
    bad = []

    def judge(t, end, ptype, field, v):
        annual = ptype == "annual"
        if field in ("operating_cf", "capex") and not annual:
            return  # SEC 분기 현금흐름은 YTD라 3개월 저장값과 직접 비교 불가
        tv = sec_values(facts[t], field, end, annual)
        key = (field, ptype)
        if not tv:
            st[key + ("SEC없음",)] += 1
            return
        if v is None:
            st[key + ("DB_NULL",)] += 1
            return
        vv = abs(v) if field == "capex" else v
        if any(abs(vv - x) <= max(1e6, abs(x) * 0.005) for x in tv):
            st[key + ("OK",)] += 1
        else:
            st[key + ("틀림",)] += 1
            bad.append((t, end, ptype, field, v, sorted(tv)[:3]))

    for t, end, ptype, *vals in fin:
        for f, v in zip(("revenue", "net_income", "operating_income", "assets", "liabilities", "equity"), vals):
            judge(t, end, ptype, f, v)
    for t, end, ptype, ocf, capex in cf:
        judge(t, end, ptype, "operating_cf", ocf)
        judge(t, end, ptype, "capex", capex)
    res = collections.defaultdict(dict)
    for (f, p, s), n in st.items():
        res[f"{f}|{p}"][s] = n
    for k in sorted(res):
        v = res[k]
        tot = v.get("OK", 0) + v.get("틀림", 0)
        print(f"{k:28s} OK {v.get('OK',0):4d} 틀림 {v.get('틀림',0):4d} ({(v.get('틀림',0)/tot*100 if tot else 0):.1f}%)  DB_NULL {v.get('DB_NULL',0)}  SEC없음 {v.get('SEC없음',0)}")
    json.dump({"sample": sample, "stats": res, "bad": bad}, open(OUT / "us_vs_sec.json", "w"), ensure_ascii=False, default=str)
    for b in bad[:12]:
        print(b)


if __name__ == "__main__":
    main()
