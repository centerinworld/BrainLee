#!/usr/bin/env python3
"""외국기업(보고통화 USD·CNY) 재무를 원화로 환산 — FnGuide 표시 기준(2026-10-03, FINANCIAL_STATEMENTS.md §2-6).

FnGuide 환산 규칙(캡처 역산으로 확인, ECOS 731Y001 매매기준율과 정확히 일치):
  손익(매출·영업이익·순이익)        = 기간 평균 매매기준율(연간=연평균, 분기=분기 평균)
  재무상태표·현금흐름(누적 칸)      = 기말 매매기준율(기간 마지막 영업일)
  분기 3개월 현금흐름(*_q)·D&A      = 기말 매매기준율(분기 CF 환율은 FnGuide로 아직 미검증 — 리포트에 표시)
대상: stock_collection_config fs_quirk:reporting_currency ∈ {USD, CNY} 종목(결산 12월). JPY·'?'(결산월 상이)는 보류.
행·항목마다 DART 원통화 값과 FnGuide 원화 값이 섞여 있어, 값마다 '원통화인지'를 크기로 판별한다:
  기준 크기 = 같은 종목·항목의 DART 원문(원통화) |값| 중앙값. 비율 r=|값|/기준 이 √환율 미만이면 원통화, 이상이면 이미 원화.
  애매(√환율/4~×4)하면 같은 행의 다른 항목 판정 다수결. 그래도 모르면 건드리지 않고 리포트.
환산 후 FnGuide 캡처(연간)와 대조해 일치율을 보고한다. --apply 시 백업·fix_log 기록. eps/bps는 건드리지 않는다.
"""
import argparse
import collections
import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
FX = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/ecos_fx/731Y001_daily.json")
IS_F = ["revenue", "operating_profit", "net_income"]
BS_F = ["total_assets", "total_liabilities", "total_equity", "cash", "capital_stock"]
CF_F = ["operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"]
CFQ_F = ["operating_cf_q", "investing_cf_q", "financing_cf_q", "capex_q", "depreciation_q"]
DART_KEY = {"revenue": "revenue", "operating_profit": "operating_profit", "net_income": "ni_parent", "total_assets": "total_assets",
            "total_liabilities": "total_liabilities", "total_equity": "equity_parent", "operating_cf": "ocf", "investing_cf": "icf",
            "financing_cf": "fcf", "capex": "capex"}
# 결산월·통화가 확인되지 않아 보류(연도마다 3~8% 어긋남): 씨케이에이치, 프레스티지바이오파마
HOLD = {"900120", "950210"}
QEND = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
QSTART = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}


def rates():
    raw = json.load(open(FX))
    out = {}
    for ccy, rows in raw.items():
        out[ccy] = pd.Series({pd.Timestamp(t): v for t, v in rows}).sort_index()
    return out


def avg(s, y, q):
    a, b = (f"{y}-01-01", f"{y}-12-31") if q in (0, None) else (f"{y}-{QSTART[q]}", f"{y}-{QEND[q]}")
    x = s[a:b]
    return float(x.mean()) if len(x) else None


def end(s, y, q):
    b = f"{y}-12-31" if q in (0, None) else f"{y}-{QEND[q]}"
    x = s[:b]
    return float(x.iloc[-1]) if len(x) and x.index[-1].year == y else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    fx = rates()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    tgt = {r[0]: r[1] for r in conn.execute("SELECT stock_code, config_value FROM stock_collection_config "
                                            "WHERE config_key='fs_quirk:reporting_currency' AND config_value IN ('USD','CNY')").fetchall()}
    # 기준 크기: DART 원문(원통화)
    ref = collections.defaultdict(list)
    for fn in ("dart_cf_full.jsonl", "dart_cf_2016_2022.jsonl"):
        p = OUT / fn
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("code") in tgt and d.get("vals"):
                for f, k in DART_KEY.items():
                    v = d["vals"].get(k)
                    if v:
                        ref[(d["code"], f)].append(abs(v))
    refm = {k: statistics.median(v) for k, v in ref.items()}
    for code in tgt:  # 항목 기준이 없으면 같은 종목 유사 항목으로
        for f in IS_F + BS_F + CF_F + CFQ_F + ["depreciation_amortization"]:
            if (code, f) not in refm:
                alt = {"cash": "total_assets", "capital_stock": "total_equity", "cash_end": "total_assets", "depreciation": "capex",
                       "depreciation_amortization": "capex"}.get(f, f.replace("_q", ""))
                if (code, alt) in refm:
                    refm[(code, f)] = refm[(code, alt)] * (0.2 if f in ("cash", "cash_end", "capital_stock") else 1)
    for code in HOLD:
        tgt.pop(code, None)
    ph = ",".join("?" * len(tgt))
    # 연도별 통화 선택: FnGuide 연간 자산총계와 원통화 자산총계 비율로 USD/CNY 중 맞는 쪽(둘 다 아니면 그 연도 제외)
    fg_ta = {(r[0], r[1]): r[2] for r in map(tuple, conn.execute(f"""SELECT DISTINCT ON (stock_code, year) stock_code, year, total_assets
              FROM financial_source_snapshot WHERE stock_code IN ({ph}) AND is_annual=1 AND report_type='CFS' AND data_source='fnguide'
              AND source_url LIKE 'https://wcomp%' AND total_assets IS NOT NULL ORDER BY stock_code, year, fetched_at DESC""", list(tgt)).fetchall())}
    ycc, skip_year = {}, set()
    for (code, y), fgv in fg_ta.items():
        ta = conn.execute("SELECT total_assets FROM financial_data WHERE stock_code=? AND year=? AND is_annual AND report_type='CFS' LIMIT 1",
                          (code, y)).fetchone()
        if not ta or not ta[0]:
            continue
        if abs(ta[0] - fgv) <= abs(fgv) * 0.005:
            continue  # 이미 원화
        hit = [c for c in ("USD", "CNY") if end(fx[c], y, 0) and abs(ta[0] * end(fx[c], y, 0) - fgv) <= abs(fgv) * 0.005]
        if hit:
            ycc[(code, y)] = hit[0]
        else:
            skip_year.add((code, y))
    print("연도별 통화 예외:", {k: v for k, v in ycc.items() if v != tgt[k[0]]}, "제외 연도:", sorted(skip_year))
    plan, st, unknown = [], collections.Counter(), []
    for tbl, fields in (("financial_data", IS_F + BS_F + ["depreciation_amortization"]), ("cash_flow_data", CF_F + CFQ_F)):
        cols = ",".join(fields)
        for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, report_type, {cols} FROM {tbl} WHERE stock_code IN ({ph})",
                              list(tgt)).fetchall():
            r = tuple(r)
            rid, code, y, q, ann, fs = r[:6]
            if (code, y) in skip_year:
                st["제외 연도"] += 1
                continue
            s = fx[ycc.get((code, y), tgt[code])]
            qq = 0 if ann else q
            verdict = {}
            for f, v in zip(fields, r[6:]):
                if v is None or v == 0 or (code, f) not in refm:
                    continue
                rate = end(s, y, qq) or 0
                if not rate:
                    continue
                ratio = abs(v) / refm[(code, f)]
                th = math.sqrt(rate)
                verdict[f] = "native" if ratio < th / 4 else "krw" if ratio > th * 4 else "?"
            known = [x for x in verdict.values() if x != "?"]
            maj = collections.Counter(known).most_common(1)[0][0] if known else "?"
            for f, v in zip(fields, r[6:]):
                if f not in verdict:
                    continue
                kind = verdict[f] if verdict[f] != "?" else maj
                if kind == "krw":
                    st["이미 원화"] += 1
                    continue
                if kind == "?":
                    st["판정 불가"] += 1
                    unknown.append((tbl, code, y, q, ann, fs, f, v))
                    continue
                rate = avg(s, y, qq) if f in IS_F else end(s, y, qq)
                if not rate:
                    st["환율 없음"] += 1
                    continue
                plan.append((tbl, rid, code, y, q, ann, fs, f, v, v * rate, rate))
                st[f"환산 {tbl}"] += 1
    print(dict(st), "대상", len(tgt), "종목")
    # 검증: 환산 후 값으로 FnGuide 캡처(실제 수집 행, 연간) 대조
    newv = {(code, y, fs, f): nv for tbl, rid, code, y, q, ann, fs, f, v, nv, rate in plan if ann}
    cur = {}
    for r in conn.execute(f"SELECT stock_code, year, report_type, {','.join(IS_F[:3] + ['total_assets','total_liabilities','total_equity'])} "
                          f"FROM financial_data WHERE is_annual AND stock_code IN ({ph})", list(tgt)).fetchall():
        r = tuple(r)
        for f, v in zip(IS_F[:3] + ["total_assets", "total_liabilities", "total_equity"], r[3:]):
            cur.setdefault((r[0], r[1], r[2], f), v)
    ok = bad = 0
    ex = []
    for r in conn.execute(f"""SELECT DISTINCT ON (stock_code, year, report_type) stock_code, year, report_type, revenue, operating_profit, net_income,
                                     total_assets, total_liabilities, total_equity FROM financial_source_snapshot
                              WHERE stock_code IN ({ph}) AND is_annual=1 AND data_source='fnguide' AND source_url LIKE 'https://wcomp%'
                              ORDER BY stock_code, year, report_type, fetched_at DESC""", list(tgt)).fetchall():
        r = tuple(r)
        for f, sv in zip(["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"], r[3:]):
            if sv is None:
                continue
            k = (r[0], r[1], r[2], f)
            v = newv.get(k, cur.get(k))
            if v is None:
                continue
            if abs(v - sv) <= max(2e6, abs(sv) * 0.005):
                ok += 1
            else:
                bad += 1
                ex.append((k, v, sv))
    print(f"환산 후 FnGuide 연간 대조: 일치 {ok} / 불일치 {bad}")
    byf = collections.Counter(k[3] for k, _, _ in ex)
    print("  불일치 항목별:", dict(byf), "— 자산·부채가 맞으면 환율은 맞고, 나머지는 정의(지배/전체)·재작성 차이")
    for e in ex[:6]:
        print("  ", e)
    pd.DataFrame(plan, columns=["table", "id", "stock_code", "year", "quarter", "is_annual", "fs", "field", "old", "new", "rate"]).to_csv(
        OUT / "foreign_currency_plan.csv", index=False)
    if not a.apply:
        return
    run_id = f"fx_krw_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        rows = [p for p in plan if p[0] == tbl]
        if not rows:
            continue
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_fx_20261003 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        ids = sorted({p[1] for p in rows})
        for i in range(0, len(ids), 1000):
            ch = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {tbl}_backup_fx_20261003 SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ch))})", [run_id] + ch)
        for _, rid, code, y, q, ann, fs, f, v, nv, rate in rows:
            conn.execute(f"UPDATE {tbl} SET {f}=?, updated_at=? WHERE id=?", (nv, now, rid))
        conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
        conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if ann else 0, fs, f, v, nv, f"보고통화→원화(ECOS 매매기준율 {rate:.2f}, FnGuide 규칙)",
                           "ECOS 731Y001", run_id) for _, rid, code, y, q, ann, fs, f, v, nv, rate in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", f"외국기업 {len(tgt)}종목 보고통화 원화 환산", len(plan),
                  "손익=기간평균, BS·CF=기말 매매기준율(FnGuide 규칙)", json.dumps(dict(st), ensure_ascii=False), f"FnGuide 대조 일치 {ok}/불일치 {bad}",
                  "scripts/review/convert_foreign_currency_20261003.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
