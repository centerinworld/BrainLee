#!/usr/bin/env python3
"""독립 재검토(읽기 전용): financial_data / cash_flow_data 를 DART 원문(fnlttSinglAcntAll)과 직접 대조한다.

왜 새로 만드나(2026-10-02): 기존 감사(scripts/audit_db_vs_dart_multi_20260926.py)는
  - DB 값이 당기·전기·전전기 값 중 '아무거나'와 맞으면 OK로 셌고,
  - Q4(파생)와 현금흐름·감가상각은 대조하지 않았으며,
  - 그 DART 값으로 DB를 덮어쓴 뒤 같은 소스로 재감사해 자기 확인이 됐다.
이 스크립트는 층화 무작위 표본에 대해 그 회계기간 '당기' 값만 정답으로 쓰고, 모든 행(연간 중복 행 포함)을 판정한다.

정답 정의(DART, 원 단위):
  IS  : 연간=thstrm_amount, 분기/반기=thstrm_amount(3개월). 순이익은 전체(ProfitLoss)와 지배(…OwnersOfParent) 둘 다 기록.
  BS  : thstrm_amount. 자본은 전체(Equity)와 지배(…OwnersOfParent) 둘 다 기록.
  CF  : thstrm_amount = 연초부터 누적(YTD). 3개월 값은 직전 분기 누적과의 차.
  Q4  : IS 3개월 = 연간 − (Q1+Q2+Q3 3개월), CF 3개월 = 연간 − Q3 누적.
판정: |DB−DART| ≤ max(100만원, 0.5%) 이면 OK. 금융업(은행·보험·증권·지주)은 매출·영업이익 정의가 달라 별도 그룹.

사용: venv/bin/python scripts/review/financial_rereview_20261002.py [--n 120] [--seed 20261002]
산출: research_outputs/financial_rereview_20261002/{dart_raw.jsonl, results.json, mismatches.csv}
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import json
import random
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
REPRT = {0: "11011", 1: "11013", 2: "11012", 3: "11014"}
ANNUAL_YEARS = (2019, 2021, 2023, 2024, 2025)
QUARTERLY = ((2025, 1), (2025, 2), (2025, 3), (2026, 1), (2026, 2))

IDS = {
    "revenue": ["ifrs-full_Revenue"],
    "operating_profit": ["dart_OperatingIncomeLoss"],
    "ni_total": ["ifrs-full_ProfitLoss"],
    "ni_parent": ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
    "total_assets": ["ifrs-full_Assets"],
    "total_liabilities": ["ifrs-full_Liabilities"],
    "equity_total": ["ifrs-full_Equity"],
    "equity_parent": ["ifrs-full_EquityAttributableToOwnersOfParent"],
    "ocf": ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
    "icf": ["ifrs-full_CashFlowsFromUsedInInvestingActivities"],
    "fcf": ["ifrs-full_CashFlowsFromUsedInFinancingActivities"],
    "capex": ["ifrs-full_PurchaseOfPropertyPlantAndEquipment", "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "dart_PurchaseOfPropertyPlantAndEquipment"],
    "depreciation": ["ifrs-full_AdjustmentsForDepreciationExpense", "dart_AdjustmentsForDepreciationExpense",
                     "ifrs-full_DepreciationExpense"],
}
NAMES = {
    "revenue": {"매출액", "수익(매출액)", "영업수익", "매출"},
    "operating_profit": {"영업이익", "영업이익(손실)", "영업손실"},
    "ni_total": {"당기순이익", "당기순이익(손실)", "분기순이익", "분기순이익(손실)", "반기순이익", "반기순이익(손실)"},
    "depreciation": {"감가상각비", "유형자산감가상각비"},
    # 2026-10-03: 2016~2018 공시는 재무상태표 총계에도 표준 ID가 없는 경우가 많다(정확 일치만 — '자본과부채총계' 등 배제).
    "total_assets": {"자산총계"},
    "total_liabilities": {"부채총계"},
    "equity_total": {"자본총계"},
    # 2026-10-02: 국내 공시는 CapEx에 표준 IFRS ID를 거의 안 써서(표본 0건 추출) 계정명으로도 찾는다.
    "capex": {"유형자산의취득", "유형자산취득", "유형자산의증가", "유형자산증가"},
}
SJ = {"revenue": ("IS", "CIS"), "operating_profit": ("IS", "CIS"), "ni_total": ("IS", "CIS"), "ni_parent": ("IS", "CIS"),
      "total_assets": ("BS",), "total_liabilities": ("BS",), "equity_total": ("BS",), "equity_parent": ("BS",),
      "ocf": ("CF",), "icf": ("CF",), "fcf": ("CF",), "capex": ("CF",), "depreciation": ("CF",)}


def num(s):
    s = (s or "").replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


class Dart:
    def __init__(self):
        self.keys = list(config.DART_API_KEYS)
        self.i = 0
        self.calls = 0

    def get(self, url, params):
        for _ in range(len(self.keys) * 2):
            p = dict(params, crtfc_key=self.keys[self.i % len(self.keys)])
            try:
                r = requests.get(url, params=p, timeout=20)
            except Exception:
                time.sleep(1)
                continue
            self.calls += 1
            time.sleep(0.08)
            if url.endswith(".json"):
                d = r.json()
                if d.get("status") == "020":  # 키 한도 초과 → 다음 키
                    self.i += 1
                    continue
                return d
            return r.content
        raise RuntimeError("DART 전 키 한도 초과")

    def corp_codes(self):
        z = zipfile.ZipFile(io.BytesIO(self.get("https://opendart.fss.or.kr/api/corpCode.xml", {})))
        root = ET.fromstring(z.read(z.namelist()[0]))
        return {e.findtext("stock_code").strip(): e.findtext("corp_code") for e in root.iter("list")
                if (e.findtext("stock_code") or "").strip()}

    def statements(self, corp, year, q, fs):
        d = self.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                     {"corp_code": corp, "bsns_year": str(year), "reprt_code": REPRT[q], "fs_div": fs})
        return d.get("list") or [] if d.get("status") == "000" else []


def _parent_by_name(rows, kind, total):
    """지배주주 귀속 값을 계정명·순서로 찾고 '지배 + 비지배 = 전체'로 검산한다(2026-10-03).

    오래된 공시는 표준 ID 없이 이름만 있고 오타도 있다('지배지주 지분순이익'). 손익계산서에서는 순이익 귀속 행이
    총포괄이익 귀속 행보다 먼저 나오므로 '지배'(비지배 제외)가 든 **첫 행**을 쓰고, 바로 뒤 '비지배' 행과 합쳐
    전체 순이익(또는 자본총계)과 맞을 때만 채택한다. 비지배 행이 없으면 지배 값이 전체와 같을 때만 채택."""
    sjs = ("IS", "CIS") if kind == "ni" else ("BS",)
    for sj in sjs:
        seq = [r for r in rows if r.get("sj_div") == sj]
        for idx, r in enumerate(seq):
            nm = (r.get("account_nm") or "").replace(" ", "")
            if "지배" in nm and "비지배" not in nm and "포괄" not in nm:
                p = num(r.get("thstrm_amount"))
                if p is None:
                    continue
                nci = None
                for r2 in seq[idx + 1: idx + 4]:
                    if "비지배" in (r2.get("account_nm") or ""):
                        nci = num(r2.get("thstrm_amount"))
                        break
                if total is None:
                    return None
                if nci is not None and abs(p + nci - total) <= max(1e6, abs(total) * 0.005):
                    return p
                if nci is None and abs(p - total) <= max(1e6, abs(total) * 0.005):
                    return p
                return None
    return None


def extract(rows):
    out = {}
    for f, ids in IDS.items():
        cand = [r for r in rows if r.get("sj_div") in SJ[f] and r.get("account_id") in ids]
        if not cand and f in NAMES:
            cand = [r for r in rows if r.get("sj_div") in SJ[f] and (r.get("account_nm") or "").replace(" ", "") in NAMES[f]]
        if cand:
            v = num(cand[0].get("thstrm_amount"))
            if v is not None:
                out[f] = abs(v) if f in ("capex", "depreciation") else v
    if "ni_parent" not in out:
        v = _parent_by_name(rows, "ni", out.get("ni_total"))
        if v is not None:
            out["ni_parent"] = v
    if "equity_parent" not in out:
        v = _parent_by_name(rows, "eq", out.get("equity_total"))
        if v is not None:
            out["equity_parent"] = v
    return out


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--from-raw", action="store_true", help="DART 재호출 없이 dart_raw.jsonl로 비교만 다시 수행")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    conn = connect_primary_db(timeout=300, readonly=True)
    fin = {r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' "
        "OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    universe = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM financial_data WHERE year=2025 AND stock_code ~ '^[0-9]{5}0$'").fetchall()]
    conn.close()  # DART 수집이 길어 유휴 트랜잭션 타임아웃으로 끊기므로 비교 단계에서 다시 연결한다
    rng = random.Random(a.seed)
    sample = sorted(rng.sample(universe, min(a.n, len(universe))))
    dart = Dart()
    truth = {}  # (code, year, q, fs) -> {field: value}
    if a.from_raw:
        for line in open(OUT / "dart_raw.jsonl"):
            d = json.loads(line)
            truth[(d["code"], d["year"], d["q"], d["fs"])] = d["vals"]
        sample = sorted({k[0] for k in truth})
    corp = {} if a.from_raw else dart.corp_codes()
    raw = open(OUT / ("dart_raw_unused.jsonl" if a.from_raw else "dart_raw.jsonl"), "w")
    for k, code in enumerate([] if a.from_raw else sample, 1):
        cc = corp.get(code)
        if not cc:
            continue
        for year, q in [(y, 0) for y in ANNUAL_YEARS] + list(QUARTERLY):
            for fs in ("CFS", "OFS"):
                rows = dart.statements(cc, year, q, fs)
                if rows:
                    t = extract(rows)
                    truth[(code, year, q, fs)] = t
                    raw.write(json.dumps({"code": code, "year": year, "q": q, "fs": fs, "vals": t}, ensure_ascii=False) + "\n")
        if k % 10 == 0:
            print(f"  {k}/{len(sample)} 종목, DART 호출 {dart.calls}", flush=True)
    raw.close()

    # 파생 정답: 분기 3개월 CF, Q4 IS/CF
    for (code, year, q, fs), t in list(truth.items()):
        if q in (2, 3):
            prev = truth.get((code, year, q - 1, fs), {})
            for f in ("ocf", "icf", "fcf", "capex", "depreciation"):
                if f in t and f in prev:
                    t[f + "_q"] = t[f] - prev[f]
        if q == 1:
            for f in ("ocf", "icf", "fcf", "capex", "depreciation"):
                if f in t:
                    t[f + "_q"] = t[f]
    for (code, year, q, fs), t in list(truth.items()):
        if q != 0:
            continue
        qs = [truth.get((code, year, i, fs)) for i in (1, 2, 3)]
        if all(qs):
            q4 = {}
            for f in ("revenue", "operating_profit", "ni_total", "ni_parent"):
                if f in t and all(f in x for x in qs):
                    q4[f] = t[f] - sum(x[f] for x in qs)
            for f in ("total_assets", "total_liabilities", "equity_total", "equity_parent"):
                if f in t:
                    q4[f] = t[f]
            for f in ("ocf", "icf", "fcf", "capex", "depreciation"):
                if f in t:
                    q4[f] = t[f]
                    if f in qs[2]:
                        q4[f + "_q"] = t[f] - qs[2][f]
            truth[(code, year, 4, fs)] = q4

    conn = connect_primary_db(timeout=300, readonly=True)
    ph = ",".join("?" * len(sample))
    fd = conn.execute(f"SELECT id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,"
                      f"total_liabilities,total_equity,data_source FROM financial_data WHERE stock_code IN ({ph})", sample).fetchall()
    cf = conn.execute(f"SELECT id,stock_code,year,quarter,is_annual,report_type,operating_cf,investing_cf,financing_cf,capex,depreciation,"
                      f"operating_cf_q,investing_cf_q,financing_cf_q,capex_q,depreciation_q,data_source FROM cash_flow_data "
                      f"WHERE stock_code IN ({ph})", sample).fetchall()
    conn.close()

    stats = collections.defaultdict(collections.Counter)
    mism = []

    def judge(table, r, period_q, fs, field, dbv, cands, grp, src):
        key = (table, field, "annual" if period_q == 0 else ("Q4" if period_q == 4 else "quarter"), grp)
        cands = {n: v for n, v in cands.items() if v is not None}
        if not cands:
            stats[key]["DART없음"] += 1
            return
        if dbv is None:
            stats[key]["DB_NULL"] += 1
            return
        hit = [n for n, v in cands.items() if close(dbv, v)]
        if hit:
            stats[key]["OK:" + "/".join(hit)] += 1
        else:
            stats[key]["MISMATCH"] += 1
            ref = next(iter(cands.values()))
            mism.append([table, r[0], r[1], r[2], period_q, fs, field, dbv, json.dumps(cands, ensure_ascii=False),
                         round((dbv / ref - 1) * 100, 2) if ref else None, src])

    for r in fd:
        code, year, quarter, is_ann, fs = r[1], r[2], r[3], r[4], r[5]
        pq = 0 if is_ann else quarter
        t = truth.get((code, year, pq, fs))
        if t is None:
            continue
        grp = "금융" if code in fin else "비금융"
        src = r[12]
        judge("financial_data", r, pq, fs, "revenue", r[6], {"dart": t.get("revenue")}, grp, src)
        judge("financial_data", r, pq, fs, "operating_profit", r[7], {"dart": t.get("operating_profit")}, grp, src)
        judge("financial_data", r, pq, fs, "net_income", r[8], {"전체": t.get("ni_total"), "지배": t.get("ni_parent")}, grp, src)
        judge("financial_data", r, pq, fs, "total_assets", r[9], {"dart": t.get("total_assets")}, grp, src)
        judge("financial_data", r, pq, fs, "total_liabilities", r[10], {"dart": t.get("total_liabilities")}, grp, src)
        judge("financial_data", r, pq, fs, "total_equity", r[11], {"전체": t.get("equity_total"), "지배": t.get("equity_parent")}, grp, src)
    for r in cf:
        code, year, quarter, is_ann, fs = r[1], r[2], r[3], r[4], r[5]
        pq = 0 if is_ann else quarter
        t = truth.get((code, year, pq, fs))
        if t is None:
            continue
        grp = "금융" if code in fin else "비금융"
        src = r[16]
        for i, f in enumerate(("ocf", "icf", "fcf", "capex", "depreciation")):
            judge("cash_flow_data", r, pq, fs, f + "(누적)", None if r[6 + i] is None else (abs(r[6 + i]) if f in ("capex", "depreciation") else r[6 + i]),
                  {"dart": t.get(f)}, grp, src)
            if pq != 0:
                v = r[11 + i]
                judge("cash_flow_data", r, pq, fs, f + "(3개월)", None if v is None else (abs(v) if f in ("capex", "depreciation") else v),
                      {"dart": t.get(f + "_q")}, grp, src)

    out = {" | ".join(k): dict(v) for k, v in sorted(stats.items())}
    json.dump({"sample": sample, "dart_calls": dart.calls, "stats": out}, open(OUT / "results.json", "w"), ensure_ascii=False, indent=1)
    with open(OUT / "mismatches.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "row_id", "stock_code", "year", "period_q", "report_type", "field", "db_value", "dart_candidates", "diff_pct_vs_first", "data_source"])
        w.writerows(mism)
    for k, v in out.items():
        tot = sum(v.values())
        bad = v.get("MISMATCH", 0) + v.get("DB_NULL", 0)
        print(f"{k:60s} n={tot:5d} 불일치={v.get('MISMATCH',0):4d} DB_NULL={v.get('DB_NULL',0):4d}  {dict(v)}")
    print("DART 호출", dart.calls, "불일치 행", len(mism))


if __name__ == "__main__":
    main()
