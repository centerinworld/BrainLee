#!/usr/bin/env python3
"""전 종목: financial_data / cash_flow_data(정정 후) ↔ FnGuide 캡처(financial_source_snapshot) 대조 — 읽기 전용(2026-10-03).

- 캡처는 진짜 FnGuide 수집분만(comp.fnguide.com / wcomp.fnguide.com URL). 'reconstructed_*' 행(DB에서 재구성)은 제외 — 자기 확인 방지.
- 종목·연도·분기·연결/별도마다 가장 최근 캡처 1건.
- 허용오차 max(1억원, 0.5%) — 구 사이트 캡처는 억원 반올림.
- 순이익·자본: DB=지배주주 기준. 캡처가 수집기 버전에 따라 전체/지배를 섞어 읽었으므로, DART 재수집값(전체/지배)으로
  '정의 차이(캡처=전체)'와 '불일치'를 구분한다.
- 같은 종목·같은 항목이 2개 기간 이상 불일치하면 '종목 특징(체계적 차이)'으로 분류 → research_outputs/.../fnguide_systematic.csv
산출: research_outputs/financial_rereview_20261002/fnguide_compare_{summary.json, mismatches.csv, systematic.csv}
"""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
FIN = ["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"]
CF = ["operating_cf", "investing_cf", "financing_cf", "capex"]


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e8, abs(b) * 0.005)


def dart_truth():
    t = {}
    for f in ("dart_cf_full.jsonl", "dart_cf_2016_2022.jsonl", "dart_raw.jsonl"):
        p = OUT / f
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            fs = d.get("fs")
            if fs and d.get("vals"):
                t[(d["code"], d["year"], d["q"], fs)] = d["vals"]
    return t


def main():
    conn = connect_primary_db(timeout=900, readonly=True)
    conn.execute("SET statement_timeout='900s'")
    snap = {}
    for r in conn.execute("""SELECT DISTINCT ON (stock_code, year, quarter, is_annual, report_type)
            stock_code, year, quarter, is_annual, report_type, revenue, operating_profit, net_income, total_assets, total_liabilities,
            total_equity, operating_cf, investing_cf, financing_cf, capex, source_url, fetched_at
          FROM financial_source_snapshot
          WHERE data_source='fnguide' AND (source_url LIKE 'https://comp.fnguide.com%' OR source_url LIKE 'https://wcomp.fnguide.com%')
          ORDER BY stock_code, year, quarter, is_annual, report_type, fetched_at DESC""").fetchall():
        key = (r[0], r[1], 0 if r[3] else r[2], r[4])
        snap[key] = dict(zip(FIN + CF, r[5:15])) | {"site": "wcomp" if "wcomp" in (r[15] or "") else "comp", "fetched": r[16]}
    fd = {}
    for r in conn.execute("SELECT stock_code, year, quarter, is_annual, report_type, " + ",".join(FIN) +
                          " FROM financial_data WHERE year>=2022").fetchall():
        k = (r[0], r[1], 0 if r[3] else r[2], r[4])
        if k[2] == 4:
            continue
        fd.setdefault(k, dict(zip(FIN, r[5:])))
    cf = {}
    for r in conn.execute("SELECT stock_code, year, quarter, is_annual, report_type, operating_cf_q, investing_cf_q, financing_cf_q, capex_q, "
                          "operating_cf, investing_cf, financing_cf, capex FROM cash_flow_data WHERE year>=2022").fetchall():
        k = (r[0], r[1], 0 if r[3] else r[2], r[4])
        if k[2] == 4:
            continue
        # FnGuide 분기 = 3개월 → 분기는 *_q, 연간은 누적 칸
        vals = r[5:9] if k[2] in (1, 2, 3) else r[9:13]
        cf.setdefault(k, dict(zip(CF, vals)))
    conn.close()
    truth = dart_truth()

    st = collections.defaultdict(collections.Counter)
    mism = []
    per_stock = collections.defaultdict(list)
    for k, s in snap.items():
        code, y, q, fs = k
        per = "연간" if q == 0 else "분기"
        dbrow = (fd.get(k) or {}) | (cf.get(k) or {})
        t = truth.get(k, {})
        for f in FIN + CF:
            sv, dv = s.get(f), dbrow.get(f)
            if sv is None:
                continue
            if f == "capex":
                sv, dv = abs(sv), (abs(dv) if dv is not None else None)
            key = (fs, per, f)
            if dv is None:
                st[key]["DB 없음"] += 1
                continue
            if close(dv, sv):
                st[key]["일치"] += 1
                continue
            # 정의 차이: 캡처가 전체 순이익/자본(구 수집기)인데 DB는 지배
            if f == "net_income" and t.get("ni_total") is not None and close(sv, t["ni_total"]) and t.get("ni_parent") is not None and close(dv, t["ni_parent"]):
                st[key]["정의 차이(캡처=전체)"] += 1
                continue
            if f == "total_equity" and t.get("equity_total") is not None and close(sv, t["equity_total"]) and t.get("equity_parent") is not None and close(dv, t["equity_parent"]):
                st[key]["정의 차이(캡처=전체)"] += 1
                continue
            # DART 원문과 DB가 같고 캡처만 다르면 'FnGuide 쪽 차이', DB가 DART와도 다르면 'DB 의심'
            dk = {"net_income": "ni_parent", "total_equity": "equity_parent", "operating_cf": "ocf", "investing_cf": "icf",
                  "financing_cf": "fcf"}.get(f, f)
            tv = t.get(dk)
            if q in (1, 2, 3) and f in ("operating_cf", "investing_cf", "financing_cf", "capex"):
                tv = None  # DART CF는 누적 — 분기 3개월 정답은 별도 계산이라 여기선 판정 보류
            cat = "불일치(DB=DART, 캡처만 다름)" if (tv is not None and close(dv, abs(tv) if f == "capex" else tv)) else (
                "불일치(DB≠DART)" if tv is not None else "불일치(DART 미보유)")
            st[key][cat] += 1
            ratio = dv / sv if sv else None
            mism.append([code, y, q, fs, f, dv, sv, tv, round(ratio, 4) if ratio else None, cat, s["site"], s["fetched"]])
            per_stock[(code, fs, f)].append((y, q, ratio, cat))
    systematic = []
    for (code, fs, f), lst in per_stock.items():
        if len(lst) >= 2:
            rs = [x[2] for x in lst if x[2]]
            same_ratio = len(rs) >= 2 and max(rs) / min(rs) < 1.02 if all(r > 0 for r in rs) and rs else False
            systematic.append([code, fs, f, len(lst), "일정 비율(단위·정의 추정)" if same_ratio else "불규칙",
                               ";".join(f"{y}Q{q}:{r}" for y, q, r, _ in lst[:8]), collections.Counter(c for *_, c in lst).most_common(1)[0][0]])
    OUT.mkdir(exist_ok=True)
    with open(OUT / "fnguide_compare_mismatches.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stock_code", "year", "q", "fs", "field", "db", "fnguide", "dart", "db/fnguide", "category", "site", "fetched"])
        w.writerows(mism)
    with open(OUT / "fnguide_systematic.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stock_code", "fs", "field", "n_periods", "pattern", "periods", "main_category"])
        w.writerows(sorted(systematic, key=lambda x: -x[3]))
    summ = {" | ".join(k): dict(v) for k, v in sorted(st.items())}
    json.dump({"snapshot_keys": len(snap), "stats": summ, "systematic_stock_fields": len(systematic)}, open(OUT / "fnguide_compare_summary.json", "w"),
              ensure_ascii=False, indent=1)
    tot = collections.Counter()
    for k, v in st.items():
        n = sum(v.values()) - v.get("DB 없음", 0)
        ok = v.get("일치", 0) + v.get("정의 차이(캡처=전체)", 0)
        print(f"{' | '.join(k):40s} 비교 {n:7,d}  일치 {ok/n*100 if n else 0:6.2f}%  {dict(v)}")
        tot["n"] += n
        tot["ok"] += ok
    print(f"전체 비교 {tot['n']:,}  일치(정의차 포함) {tot['ok']/tot['n']*100:.3f}%  체계적 차이 종목·항목 {len(systematic):,}")


if __name__ == "__main__":
    main()
