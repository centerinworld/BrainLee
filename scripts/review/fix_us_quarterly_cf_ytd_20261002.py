#!/usr/bin/env python3
"""[현금흐름판] 미국 분기 현금흐름의 '연초 누적(YTD) 값이 분기 칸에 저장된' 오류를 SEC 공식 XBRL로 3개월 값으로 바로잡는다(2026-10-02).

발견: SEC 대조(40티커)에서 분기 매출·영업이익·순이익 39~46% 불일치, 그중 97%(445/458)가 6·9개월 누적 값과 정확히 일치.
규칙: 분기 행(period_type='quarter')의 각 필드에 대해
  - SEC 3개월 값(기간 80~100일)이 있으면 그것, 없으면 같은 회계연도 YTD(end) − YTD(직전 분기 end)로 3개월 값 계산.
  - DB 값이 그 기간의 SEC YTD(>100일) 값과 일치할 때만 교체(확인된 오류 유형만, 다른 차이는 손대지 않음).
  - opm(영업이익률) = operating_income / revenue * 100 재계산(교체가 있었던 행).
캐시: research_outputs/financial_rereview_20261002/sec_facts/{CIK}.json
사용: --eval(표본 40티커 전후 정확도) / --apply(전 티커, 백업 us_cashflow_data_backup_ytd_fix_20261002, data_fix_log)
"""
import argparse
import collections
import json
import random
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

UA = {"User-Agent": "stock-dashboard research center.in.world@gmail.com"}
CACHE = ROOT / "research_outputs" / "financial_rereview_20261002" / "sec_facts_cf"
TAGS = {
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "investing_cf": ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "financing_cf": ["NetCashProvidedByUsedInFinancingActivities", "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def facts_for(cik):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{cik}.json"
    if p.exists():
        return json.loads(p.read_text())
    for _ in range(3):
        r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=UA, timeout=30)
        time.sleep(0.12)
        if r.status_code == 200:
            f = r.json().get("facts", {}).get("us-gaap", {})
            keep = {t: f[t] for ts in TAGS.values() for t in ts if t in f}
            p.write_text(json.dumps(keep))
            return keep
        if r.status_code == 404:
            p.write_text("{}")
            return {}
        time.sleep(2)
    return {}


def series(facts, field):
    """end -> {'q3m': val or None, 'ytd': {days: val}, 'start': ...} (첫 번째 태그 우선)"""
    out = {}
    for tag in TAGS[field]:
        arr = facts.get(tag, {}).get("units", {}).get("USD", [])
        if not arr:
            continue
        for x in arr:
            if not x.get("start"):
                continue
            d = (date.fromisoformat(x["end"]) - date.fromisoformat(x["start"])).days
            e = out.setdefault(x["end"], {"q3m": None, "ytd": {}})
            if 80 <= d <= 100:
                e["q3m"] = float(x["val"])
                e["q3m_start"] = x["start"]
            elif 100 < d < 330:
                e["ytd"][(x["start"], d)] = float(x["val"])
        if out:
            return out
    return out


def three_month(s, end):
    e = s.get(end)
    if not e:
        return None
    if e["q3m"] is not None:
        return e["q3m"]
    # YTD(end) − YTD(직전 분기 end, 같은 start)
    for (start, d), v in e["ytd"].items():
        for end2, e2 in s.items():
            if end2 < end:
                for (s2, d2), v2 in e2["ytd"].items():
                    if s2 == start and 0 < d - d2 <= 100 and d - d2 >= 80:
                        return v - v2
                # 직전 분기가 회계연도 첫 분기(3개월 값만 존재)인 경우: Q2 = YTD(6개월) − Q1(3개월), 시작일이 같아야 한다
                if e2["q3m"] is not None and e2.get("q3m_start") == start and 80 <= d - 91 <= 100 and 80 <= (date.fromisoformat(end) - date.fromisoformat(end2)).days <= 100:
                    return v - e2["q3m"]
    return None


def plan(conn, tickers, cik):
    rows = conn.execute(f"SELECT ticker,period_end,operating_cf,investing_cf,financing_cf,capex FROM us_cashflow_data "
                        f"WHERE period_type='quarter' AND ticker IN ({','.join('?'*len(tickers))})", tickers).fetchall()
    by = collections.defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    out = []
    fields = ["operating_cf", "investing_cf", "financing_cf", "capex"]
    for t, rs in by.items():
        if t not in cik:
            continue
        facts = facts_for(cik[t])
        if not facts:
            continue
        for f_i, f in enumerate(fields):
            s = series(facts, f)
            if not s:
                continue
            for r in rs:
                end, v = r[1], r[2 + f_i]
                e = s.get(end)
                if v is None or not e or not e["ytd"]:
                    continue
                sign = -1 if (f == "capex" and v < 0) else 1
                vv = abs(v) if f == "capex" else v
                if any(close(vv, y) for y in e["ytd"].values()) and not (e["q3m"] is not None and close(vv, e["q3m"])):
                    new = three_month(s, end)
                    if new is not None and not close(vv, new):
                        out.append((t, end, f, v, sign * new if f == "capex" else new))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    cik = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json().values()}
    if a.eval:
        sample = json.load(open(ROOT / "research_outputs" / "financial_rereview_20261002" / "us_vs_sec.json"))["sample"]
        ch = plan(conn, sample, cik)
        print("표본 변경", len(ch), collections.Counter(c[2] for c in ch))
        # 교체값이 SEC 3개월 값과 맞는지
        ok = sum(1 for t, end, f, o, n in ch if (series(facts_for(cik[t]), f).get(end, {}).get("q3m") in (None,) or close(n, series(facts_for(cik[t]), f)[end]["q3m"])))
        print("교체값 검증(SEC 3개월 있으면 일치):", ok, "/", len(ch))
        for c in ch[:8]:
            print(c)
        return
    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM us_cashflow_data WHERE period_type='quarter'").fetchall()]
    ch = []
    for i in range(0, len(tickers), 200):
        ch += plan(conn, tickers[i:i + 200], cik)
        print(f"  {min(i+200,len(tickers))}/{len(tickers)} 티커, 누적 변경 {len(ch)}", flush=True)
    print("전체 변경", len(ch), collections.Counter(c[2] for c in ch))
    if not a.apply:
        return
    run_id = f"us_cf_q_ytd_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    keys = sorted({(t, e) for t, e, *_ in ch})
    conn.execute("CREATE TABLE IF NOT EXISTS us_cashflow_data_backup_ytd_fix_20261002 AS SELECT *, CAST(NULL AS TEXT) run_id FROM us_cashflow_data WHERE false")
    for t, e in keys:
        conn.execute("INSERT INTO us_cashflow_data_backup_ytd_fix_20261002 SELECT *, ? FROM us_cashflow_data WHERE ticker=? AND period_end=? AND period_type='quarter'", (run_id, t, e))
    for t, e, f, o, n in ch:
        conn.execute(f"UPDATE us_cashflow_data SET {f}=?, updated_at=? WHERE ticker=? AND period_end=? AND period_type='quarter'", (n, now, t, e))
    for t, e in keys:
        conn.execute("UPDATE us_cashflow_data SET free_cf=operating_cf-ABS(capex) WHERE ticker=? AND period_end=? AND period_type='quarter' AND operating_cf IS NOT NULL AND capex IS NOT NULL", (t, e))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "us_cashflow_data", "분기 현금흐름 YTD 누적값 저장 오류", len(ch), "DB=SEC YTD 일치 행만 → SEC 3개월(또는 YTD 차분)",
                  json.dumps(dict(collections.Counter(c[2] for c in ch))), "SEC 3개월 값", "SEC companyfacts", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
