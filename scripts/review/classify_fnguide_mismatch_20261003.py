#!/usr/bin/env python3
"""DB ↔ FnGuide 캡처 불일치(compare_db_vs_fnguide_snapshot_20261003.py 산출)를 원인별로 분류하고,
같은 종목에서 반복되는 원인을 종목 특성(stock_collection_config, config_key='fs_quirk:*')으로 기록한다(2026-10-03).

원인 판정 순서(먼저 맞는 것):
  외국기업(통화)        : 코드 9xxxxx, DB/FnGuide 비율이 통화 환율과 일정 → DB가 보고통화 원본으로 저장
  캡처 기간 오배치       : 캡처 분기 칸 = DB 같은 해 연간 값(구 comp 수집기 파싱 오류)
  캡처=누적(YTD)        : 캡처 분기 = DB 1~q분기 합(손익) 또는 DB 누적 칸(현금흐름)
  기간 한 칸 밀림        : 캡처 = DB의 앞뒤 분기·앞뒤 연도 값
  연결/별도 뒤바뀜       : 캡처 = DB 반대 구분 값
  단위 1000배 / 부호 반대
  정의 차이(캡처=자본총계): 캡처 자본 = DB 자산−부채(비지배 포함) — DB는 지배주주지분이라 정상
  재작성(정정)          : 네이버 = FnGuide ≠ DB (매출·영업이익·순이익(전체)) → DB가 최초 공시값, 외부는 재작성값
  FnGuide 단독 차이      : 네이버 = DB ≠ FnGuide
  미확인                : 그 밖(3번째 소스 없음 포함)
사용: (dry-run) → --apply 시 stock_collection_config upsert.
산출: research_outputs/financial_rereview_20261002/fnguide_mismatch_classified.csv, fnguide_stock_quirks.csv
"""
import argparse
import collections
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
FLOW = ["revenue", "operating_profit", "net_income"]
CFF = ["operating_cf", "investing_cf", "financing_cf"]
CCY = [("USD", 1 / 1600, 1 / 1100), ("CNY", 1 / 230, 1 / 160), ("JPY", 1 / 11, 1 / 8), ("HKD", 1 / 200, 1 / 150)]


def close(a, b):
    return a is not None and b is not None and a == a and b == b and abs(a - b) <= max(1e8, abs(b) * 0.005)


def load_db(conn):
    db = collections.defaultdict(dict)
    for r in conn.execute("""SELECT f.stock_code,f.year,f.quarter,f.is_annual,f.report_type,f.revenue,f.operating_profit,f.net_income,
                                    f.total_assets,f.total_liabilities,f.total_equity,
                                    cf.operating_cf_q,cf.investing_cf_q,cf.financing_cf_q,cf.operating_cf,cf.investing_cf,cf.financing_cf
                             FROM financial_data f LEFT JOIN cash_flow_data cf USING(stock_code,year,quarter,is_annual,report_type)
                             WHERE f.year>=2020""").fetchall():
        r = tuple(r)
        code, y, q, ann, fs = r[:5]
        if not ann and q == 4:
            continue
        per = (y, 0) if ann else (y, q)
        v = dict(zip(FLOW + ["total_assets", "total_liabilities", "total_equity"], r[5:11]))
        v.update(dict(zip(CFF, r[14:17] if ann else r[11:14])))
        v["ytd"] = dict(zip(CFF, r[14:17]))
        db[(code, fs)].setdefault(per, v)
    return db


def classify(t, db, nv):
    code, y, q, fs, f, sv, dv = t.stock_code, t.year, t.q, t.fs, t.field, t.fnguide, t.db
    d = db.get((code, fs), {})
    o = db.get((code, "OFS" if fs == "CFS" else "CFS"), {})
    r = dv / sv if dv and sv else None
    if code.startswith("9") and r:
        for ccy, lo, hi in CCY:
            if lo <= abs(r) <= hi:
                return "외국기업(통화)", ccy
        return "외국기업(통화)", "?"
    cur = d.get((y, q), {})
    if f == "total_equity" and cur.get("total_assets") is not None and cur.get("total_liabilities") is not None \
            and close(cur["total_assets"] - cur["total_liabilities"], sv):
        return "정의 차이(캡처=자본총계)", "DB=지배주주지분"
    if q and close(d.get((y, 0), {}).get(f), sv):
        return "캡처 기간 오배치", "분기칸=연간"
    if q and f in CFF and close(d.get((y, q), {}).get("ytd", {}).get(f), sv):
        return "캡처=누적(YTD)", ""
    if q and f in FLOW and all(d.get((y, k), {}).get(f) is not None for k in range(1, q + 1)) \
            and close(sum(d[(y, k)][f] for k in range(1, q + 1)), sv):
        return "캡처=누적(YTD)", ""
    near = [(y, q - 1), (y, q + 1), (y - 1, q), (y + 1, q)] if q else [(y - 1, 0), (y + 1, 0)]
    if any(close(d.get(p, {}).get(f), sv) for p in near if p[1] in (0, 1, 2, 3)):
        return "기간 한 칸 밀림", ""
    if close(o.get((y, q), {}).get(f), sv):
        return "연결/별도 뒤바뀜", ""
    if r and (0.0009 < abs(r) < 0.0011 or 900 < abs(r) < 1100):
        return "단위 1000배", ""
    if r and -1.01 < r < -0.99:
        return "부호 반대", ""
    n = nv.get((code, y, q))
    if fs == "CFS" and n and f in FLOW:
        v = n[FLOW.index(f)]
        if v is not None:
            a, b = close(v, sv), close(v, dv)
            if a and not b:
                return "재작성(정정)", "네이버=FnGuide"
            if b and not a:
                return "FnGuide 단독 차이", "네이버=DB"
    return "미확인", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    db = load_db(conn)
    nv = {(r[0], r[1], 0 if r[3] else r[2]): (r[4], r[5], r[6]) for r in map(tuple, conn.execute(
        "SELECT stock_code,year,quarter,is_annual,revenue,operating_profit,net_income FROM naver_financial").fetchall())}
    m = pd.read_csv(OUT / "fnguide_compare_mismatches.csv", dtype={"stock_code": str})
    res = [classify(t, db, nv) for t in m.itertuples()]
    m["cause"] = [x[0] for x in res]
    m["detail"] = [x[1] for x in res]
    m.to_csv(OUT / "fnguide_mismatch_classified.csv", index=False)
    print(m.groupby("cause").size().sort_values(ascending=False).to_string())

    # 종목 특성: 원인별로 종목 단위 집계(재작성은 1건도 사실 기록, 나머지는 2개 기간 이상 반복일 때)
    quirks = []
    key = {"외국기업(통화)": "fs_quirk:reporting_currency", "캡처 기간 오배치": "fs_quirk:fnguide_capture_issue",
           "캡처=누적(YTD)": "fs_quirk:fnguide_capture_issue", "기간 한 칸 밀림": "fs_quirk:period_shift",
           "연결/별도 뒤바뀜": "fs_quirk:cfs_ofs_mismatch", "단위 1000배": "fs_quirk:unit_mismatch", "부호 반대": "fs_quirk:sign_mismatch",
           "재작성(정정)": "fs_quirk:restated_periods", "정의 차이(캡처=자본총계)": None, "FnGuide 단독 차이": "fs_quirk:fnguide_definition_diff", "미확인": "fs_quirk:unexplained"}
    for (code, cause), g in m.groupby(["stock_code", "cause"]):
        periods = sorted({(int(y), int(q)) for y, q in zip(g.year, g.q)})
        if key[cause] is None or (cause != "재작성(정정)" and len(periods) < 2):
            continue
        per = ",".join(f"{y}{'' if q == 0 else 'Q' + str(q)}" for y, q in periods)
        fields = ",".join(sorted(set(g.field)))
        if cause == "외국기업(통화)":
            med = g["db/fnguide"].abs().median()  # 종목 단위 중앙값으로 통화 판정(개별 칸은 부호·0 근처로 튐)
            val = next((c for c, lo, hi in CCY if lo <= med <= hi), "?")
            why = f"DB 재무값이 보고통화({val}) 원본 — FnGuide는 원화 환산. 대상 기간 {per}"
        else:
            val = f"{cause}|{fields}|{per}"
            why = f"FnGuide 캡처 대조 {len(g)}칸: {cause} ({fields}; {per})"
        quirks.append((code, key[cause], val[:500], why[:1000]))
    # 같은 키가 여러 원인(캡처 오배치+누적)이면 합친다
    merged = {}
    for code, k, v, why in quirks:
        if (code, k) in merged:
            pv, pw = merged[(code, k)]
            merged[(code, k)] = (f"{pv} / {v}"[:500], f"{pw} / {why}"[:1000])
        else:
            merged[(code, k)] = (v, why)
    q = pd.DataFrame([(c, k, v, w) for (c, k), (v, w) in merged.items()], columns=["stock_code", "config_key", "config_value", "reason"])
    q.to_csv(OUT / "fnguide_stock_quirks.csv", index=False)
    print("\n종목 특성", len(q), "건 /", q.stock_code.nunique(), "종목")
    print(q.config_key.value_counts().to_string())
    if not a.apply:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM stock_collection_config WHERE config_key LIKE 'fs_quirk:%' AND source='fnguide_compare_20261003'")
    conn.executemany("""INSERT INTO stock_collection_config(stock_code,config_key,config_value,reason,source,created_at,updated_at)
                        VALUES (?,?,?,?,'fnguide_compare_20261003',?,?)
                        ON CONFLICT (stock_code,config_key) DO UPDATE SET config_value=EXCLUDED.config_value, reason=EXCLUDED.reason,
                        source=EXCLUDED.source, updated_at=EXCLUDED.updated_at""",
                     [(r.stock_code, r.config_key, r.config_value, r.reason, now, now) for r in q.itertuples()])
    conn.commit()
    print("기록 완료")


if __name__ == "__main__":
    main()
