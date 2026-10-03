#!/usr/bin/env python3
"""FnGuide wcomp 원문(fetch_fnguide_raw_20261003.py 저장분) ↔ DB 대조 — FINANCIAL_STATEMENTS.md §2-1 정의 그대로(읽기 전용, 2026-10-03).

- 종목별 가장 최근 수집일의 원문 12개 파일(연결 C/별도 P × 연간 Y/분기 Q × 손익/재무상태/현금흐름)을 읽는다.
- 항목(FnGuide 표시명 → DB):
    매출액(수익)→revenue, 영업이익(발표기준)→operating_profit,
    (지배주주지분)당기순이익(연결)/당기순이익(별도)→net_income,
    자산총계→total_assets, 부채총계→total_liabilities, 지배주주지분(연결)/자본총계(별도)→total_equity,
    영업·투자·재무활동으로인한현금흐름→operating/investing/financing_cf(연간=누적 칸, 분기=*_q 3개월),
    유형자산의증가→capex(_q), 유형자산감가상각비→depreciation(_q),
    유형자산감가상각비+기타무형자산상각비+개발비상각→depreciation_amortization(연간)
- 단위: 억원(소수 2자리) × 1e8. 허용오차 max(200만원, 0.5%) — 원문 정밀도가 백만원이라 정의가 같으면 사실상 같아야 한다.
- 결산월이 12월이 아닌 열(YYYY/03 등)은 회계연도 매핑이 확정되지 않아 '결산월≠12'로만 집계(판정 보류).
- '(최근분기)' 등 접미사 열·전년동기·증감률 열은 쓰지 않는다.
산출: research_outputs/financial_rereview_20261002/fnguide_raw_compare_{summary.json,mismatches.csv}
"""
import collections
import csv
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/fnguide_wcomp")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
QMAP = {"03": 1, "06": 2, "09": 3, "12": 4}


def rows_by_name(data):
    out = {}
    for r in data:
        n = str(r.get("NAME") or "").replace(" ", "")
        out.setdefault(n, r)  # 같은 이름이 여러 번이면 첫 행(상위 수준)
    return out


def num(r, col):
    if r is None:
        return None
    v = r.get(col)
    if v in (None, ""):
        return None
    try:
        return float(v) * 1e8
    except ValueError:
        return None


def parse_code(code):
    d = RAW / code
    files = sorted(d.glob("*.json.gz"))
    if not files:
        return {}, None
    day = files[-1].name[:8]
    res = {}  # (year, q, fs) -> {field: value}  q=0 연간
    for consol, fs in (("C", "CFS"), ("P", "OFS")):
        for freq in ("Y", "Q"):
            parts = {}
            hdr = None
            for ep in ("getFinIncome", "getFinBalance", "getFinCashFlow"):
                f = d / f"{day}_{consol}_{freq}_{ep}.json.gz"
                if not f.exists():
                    continue
                try:
                    ds = json.loads(gzip.decompress(f.read_bytes())).get("dataset") or {}
                except Exception:
                    continue
                parts[ep] = (ds.get("header") or [], rows_by_name(ds.get("data") or []))
            if not parts:
                continue
            inc = parts.get("getFinIncome", ([], {}))[1]
            bal = parts.get("getFinBalance", ([], {}))[1]
            cf = parts.get("getFinCashFlow", ([], {}))[1]
            cols = {}
            for ep, (h, _) in parts.items():
                for x in h:
                    raw = str(x.get("YYMM", "")).strip()
                    if re.fullmatch(r"20\d{2}/\d{2}", raw):
                        cols.setdefault((ep, raw), x["CD"])
            periods = sorted({raw for (_, raw) in cols})
            for raw in periods:
                yy, mm = int(raw[:4]), raw[5:]
                if freq == "Y":
                    key = (yy, 0, fs) if mm == "12" else (yy, "FY" + mm, fs)
                else:
                    key = (yy, QMAP.get(mm, "M" + mm), fs)
                ci, cb, cc = (cols.get(("getFinIncome", raw)), cols.get(("getFinBalance", raw)), cols.get(("getFinCashFlow", raw)))
                v = {}
                if ci:
                    v["revenue"] = num(inc.get("매출액(수익)"), ci)
                    v["operating_profit"] = num(inc.get("영업이익(발표기준)") or inc.get("영업이익"), ci)
                    v["net_income"] = num(inc.get("(지배주주지분)당기순이익"), ci) if fs == "CFS" else num(inc.get("당기순이익"), ci)
                    v["ni_total"] = num(inc.get("당기순이익"), ci)
                if cb:
                    v["total_assets"] = num(bal.get("자산총계"), cb)
                    v["total_liabilities"] = num(bal.get("부채총계"), cb)
                    v["total_equity"] = num(bal.get("지배주주지분"), cb) if fs == "CFS" else num(bal.get("자본총계"), cb)
                    v["equity_total"] = num(bal.get("자본총계"), cb)
                    v["inventory"] = num(bal.get("재고자산"), cb)
                if cc:
                    v["operating_cf"] = num(cf.get("영업활동으로인한현금흐름"), cc)
                    v["investing_cf"] = num(cf.get("투자활동으로인한현금흐름"), cc)
                    v["financing_cf"] = num(cf.get("재무활동으로인한현금흐름"), cc)
                    v["capex"] = num(cf.get("유형자산의증가"), cc)
                    dep = num(cf.get("유형자산감가상각비"), cc)
                    v["depreciation"] = dep
                    am = [num(cf.get("기타무형자산상각비"), cc), num(cf.get("개발비상각"), cc)]
                    v["depreciation_amortization"] = (dep or 0) + sum(x or 0 for x in am) if dep is not None else None
                v = {k: x for k, x in v.items() if x is not None}
                if v:
                    res.setdefault(key, {}).update(v)
    return res, day


def close(a, b):
    return abs(a - b) <= max(2e6, abs(b) * 0.005)


def main():
    codes = sorted(p.name for p in RAW.iterdir() if p.is_dir()) if RAW.exists() else []
    fg = {}
    days = {}
    for c in codes:
        r, day = parse_code(c)
        for k, v in r.items():
            fg[(c,) + k] = v
        days[c] = day
    if not fg:
        print("원문 없음")
        return
    conn = connect_primary_db(timeout=600, readonly=True)
    ph = ",".join("?" * len(codes))
    db = {}
    for r in conn.execute(f"""SELECT f.stock_code,f.year,f.quarter,f.is_annual,f.report_type,f.revenue,f.operating_profit,f.net_income,
                                     f.total_assets,f.total_liabilities,f.total_equity,f.depreciation_amortization,
                                     cf.operating_cf,cf.investing_cf,cf.financing_cf,cf.capex,cf.depreciation,
                                     cf.operating_cf_q,cf.investing_cf_q,cf.financing_cf_q,cf.capex_q,cf.depreciation_q
                              FROM financial_data f LEFT JOIN cash_flow_data cf USING(stock_code,year,quarter,is_annual,report_type)
                              WHERE f.stock_code IN ({ph}) AND f.year>=2022""", codes).fetchall():
        r = tuple(r)
        code, y, q, ann, fs = r[:5]
        k = (code, y, 0 if ann else q, fs)
        base = dict(zip(["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
                         "depreciation_amortization"], r[5:12]))
        if ann:
            base.update(zip(["operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"], r[12:17]))
        else:
            base.update(zip(["operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"], r[17:22]))
            base.pop("depreciation_amortization")
        db.setdefault(k, base)
    conn.close()
    st = collections.defaultdict(collections.Counter)
    mism = []
    for (code, y, q, fs), v in fg.items():
        if isinstance(q, str):
            st[(fs, "결산월≠12", "-")]["판정 보류"] += 1
            continue
        per = "연간" if q == 0 else "분기"
        d = db.get((code, y, q, fs))
        for f in ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
                  "operating_cf", "investing_cf", "financing_cf", "capex", "depreciation", "depreciation_amortization"):
            if f not in v:
                continue
            sv = abs(v[f]) if f in ("capex", "depreciation", "depreciation_amortization") else v[f]
            if d is None or d.get(f) is None:
                st[(fs, per, f)]["DB 없음"] += 1
                continue
            dv = abs(d[f]) if f in ("capex", "depreciation", "depreciation_amortization") else d[f]
            if close(dv, sv):
                st[(fs, per, f)]["일치"] += 1
            else:
                st[(fs, per, f)]["불일치"] += 1
                mism.append([code, y, q, fs, f, dv, sv, round(dv / sv, 4) if sv else None, days.get(code)])
    with open(OUT / "fnguide_raw_compare_mismatches.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stock_code", "year", "q", "fs", "field", "db", "fnguide", "db/fnguide", "fetched_day"])
        w.writerows(mism)
    summ = {" | ".join(k): dict(c) for k, c in sorted(st.items())}
    json.dump({"stocks": len(codes), "stats": summ}, open(OUT / "fnguide_raw_compare_summary.json", "w"), ensure_ascii=False, indent=1)
    n = ok = 0
    for k, c in sorted(st.items()):
        m = c.get("일치", 0) + c.get("불일치", 0)
        n += m
        ok += c.get("일치", 0)
        print(f"{' | '.join(k):38s} 비교 {m:6,d}  일치 {c.get('일치', 0) / m * 100 if m else 0:6.2f}%  {dict(c)}")
    print(f"종목 {len(codes)}  비교 {n:,}  일치 {ok / n * 100 if n else 0:.3f}%")


if __name__ == "__main__":
    main()
