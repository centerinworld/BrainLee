#!/usr/bin/env python3
"""DART 재수집 없이 DB 안의 신뢰 값만으로 cash_flow_data 를 정정한다(2026-10-02 독립 재검토 2단계).

근거(150종목 DART 원문 표본, docs/FINANCIAL_REREVIEW_20261002.md):
  - 연간 누적 값은 약 95% 정답, DART 출처 분기 누적 값과 Q1 행은 정답.
  - FnGuide 출처 2·3분기 행(누적/3개월)은 30~60% 오답 → '신뢰 불가'로 보고 재계산의 입력으로 쓰지 않는다(값은 재수집 때 교체).
  - Q4 행 누적 칸은 표본 100% 오답, dart_q2_verified 행은 3개월 칸이 전부 NULL, OFS 행 일부에 CFS 3개월 값이 섞였다.
  - FnGuide 출처 연간(quarter=0) 감가상각은 54건 중 48건 오답(DART의 중앙값 3.6배).

규칙(같은 종목·연도·report_type 안에서만):
  R1 Q4 누적 칸(operating/investing/financing_cf, capex, depreciation) = 그해 연간 행 값(FnGuide 아닌 연간 행 우선).
  R2 3개월 칸: Q1 = 누적(신뢰 행), Q2/Q3 = 누적 − 직전 분기 누적(두 행 모두 신뢰), Q4 = 연간 − Q3 누적(Q3 신뢰).
  R3 FnGuide 연간(quarter=0) 감가상각: 같은 연도 다른 연간 행(FnGuide 아님)에 값이 있으면 그 값, 없으면 NULL.
  신뢰 행 = data_source 가 'fnguide' 로 시작하지 않거나 quarter=1.
--eval: 표본(dart_raw.jsonl)으로 정정 전후 정확도 비교만. --apply: 백업·cashflow_fix_log·data_fix_log 후 적용.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_raw.jsonl"
F = {"operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf", "capex": "capex", "depreciation": "depreciation"}
# 감가상각은 분기 누적 자체가 표본 87% 오답이라 R1/R2(누적 복사·차분)에서 제외 — 표본 평가에서 분기 3개월 정답이 52→21로 악화됐다.
R12 = [c for c in F if c != "depreciation"]
ABS = {"capex", "depreciation"}


def trusted(src, q):
    return q == 1 or not (src or "").startswith("fnguide")


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def plan(rows):
    """rows: dict id -> row dict. 반환: [(id, col, old, new, rule)]"""
    by = collections.defaultdict(dict)    # (code, year, fs) -> {q or 'A0'/'A4': row}
    for r in rows.values():
        key = (r["stock_code"], r["year"], r["report_type"])
        if r["is_annual"]:
            by[key]["A%d" % (r["quarter"] or 0)] = r
        else:
            by[key][r["quarter"]] = r
    out = []
    for key, g in by.items():
        annual = None
        for k in ("A4", "A0"):
            a = g.get(k)
            if a and not (a["data_source"] or "").startswith("fnguide"):
                annual = a
                break
        annual = annual or g.get("A0") or g.get("A4")
        # R3
        a0 = g.get("A0")
        if a0 and (a0["data_source"] or "").startswith("fnguide") and a0["depreciation"] is not None:
            alt = g.get("A4")
            new = alt["depreciation"] if (alt and alt["depreciation"] is not None and not (alt["data_source"] or "").startswith("fnguide")) else None
            if not close(a0["depreciation"], new):
                out.append((a0["id"], "depreciation", a0["depreciation"], new, "R3 FnGuide 연간 감가상각 부풀림 → 다른 연간 행 값/NULL"))
        q4 = g.get(4)
        if q4 and annual:
            for col in R12:
                new = annual[col]
                if new is not None and not close(q4[col], new):
                    out.append((q4["id"], col, q4[col], new, "R1 Q4 누적 = 연간"))
        for q in (1, 2, 3, 4):
            r = g.get(q)
            if not r:
                continue
            for col in R12:
                if q == 1:
                    new = r[col] if trusted(r["data_source"], 1) else None
                elif q in (2, 3):
                    p = g.get(q - 1)
                    new = (r[col] - p[col]) if (p and trusted(r["data_source"], q) and trusted(p["data_source"], q - 1)
                                                and r[col] is not None and p[col] is not None) else None
                else:
                    p = g.get(3)
                    new = (annual[col] - p[col]) if (annual and p and trusted(p["data_source"], 3)
                                                     and annual[col] is not None and p[col] is not None) else None
                if new is None:
                    continue
                if col in ABS:  # CapEx·감가상각은 부호 관례가 섞여 있어 절대값끼리 차분
                    if q == 1:
                        new = abs(r[col])
                    elif q in (2, 3):
                        new = abs(r[col]) - abs(g[q - 1][col])
                    else:
                        new = abs(annual[col]) - abs(g[3][col])
                old = r[col + "_q"]
                if not close(old, new):
                    out.append((r["id"], col + "_q", old, new, "R2 3개월 = 신뢰 누적 차분"))
    return out


def load_rows(conn, codes=None):
    cond = "" if not codes else " AND stock_code IN (%s)" % ",".join("?" * len(codes))
    cols = ["id", "stock_code", "year", "quarter", "is_annual", "report_type", "data_source"] + list(F) + [c + "_q" for c in F]
    rows = conn.execute(f"SELECT {','.join(cols)} FROM cash_flow_data WHERE year>=2016{cond}", codes or []).fetchall()
    return {r[0]: dict(zip(cols, r)) for r in rows}


def evaluate(conn):
    truth = {}
    for line in open(RAW):
        d = json.loads(line)
        truth[(d["code"], d["year"], d["q"], d["fs"])] = d["vals"]
    for (c, y, q, fs), t in list(truth.items()):
        if q in (2, 3):
            p = truth.get((c, y, q - 1, fs), {})
            for f in F.values():
                if f in t and f in p:
                    t[f + "_q"] = t[f] - p[f]
        if q == 1:
            for f in F.values():
                if f in t:
                    t[f + "_q"] = t[f]
    for (c, y, q, fs), t in list(truth.items()):
        if q == 0 and (c, y, 3, fs) in truth:
            t3 = truth[(c, y, 3, fs)]
            truth[(c, y, 4, fs)] = {**{f: t[f] for f in F.values() if f in t},
                                    **{f + "_q": t[f] - t3[f] for f in F.values() if f in t and f in t3}}
    codes = sorted({k[0] for k in truth})
    rows = load_rows(conn, codes)
    changes = plan(rows)
    after = {i: dict(r) for i, r in rows.items()}
    for i, col, old, new, rule in changes:
        after[i][col] = new
    st = collections.Counter()
    for i, r in rows.items():
        pq = 0 if r["is_annual"] else r["quarter"]
        t = truth.get((r["stock_code"], r["year"], pq, r["report_type"]))
        if not t:
            continue
        for col, f in F.items():
            for suffix in ("", "_q"):
                if pq == 0 and suffix:
                    continue
                tv = t.get(f + suffix)
                if tv is None:
                    continue
                tv = abs(tv) if f in ABS else tv
                vb, va = r[col + suffix], after[i][col + suffix]
                vb = abs(vb) if (vb is not None and f in ABS) else vb
                va = abs(va) if (va is not None and f in ABS) else va
                if close(vb, tv) and not close(va, tv):
                    st[("회귀", f + suffix)] += 1
                for tag, rr in (("전", r), ("후", after[i])):
                    v = rr[col + suffix]
                    v = abs(v) if (v is not None and f in ABS) else v
                    st[(f + suffix, "Q4" if pq == 4 else ("연간" if pq == 0 else "분기"), tag, "OK" if close(v, tv) else ("NULL" if v is None else "틀림"))] += 1
    print("회귀(정답→오답):", {k[1]: v for k, v in st.items() if k[0] == "회귀"})
    keys = sorted({k[:3] for k in st if k[0] != "회귀"})
    for k in keys:
        if k[2] != "전":
            continue
        b = {s: st[k + (s,)] for s in ("OK", "틀림", "NULL")}
        a = {s: st[(k[0], k[1], "후", s)] for s in ("OK", "틀림", "NULL")}
        print(f"{k[0]:16s} {k[1]:4s} 전 {b}  →  후 {a}")
    print("표본 변경 필드", len(changes), collections.Counter(c[4][:2] for c in changes))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    if a.eval:
        evaluate(conn)
        return
    rows = load_rows(conn)
    changes = plan(rows)
    print("전체 변경 필드", len(changes), dict(collections.Counter(c[4][:2] for c in changes)))
    if not a.apply:
        return
    run_id = f"cf_internal_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    ids = sorted({c[0] for c in changes})
    conn.execute("CREATE TABLE IF NOT EXISTS cash_flow_data_backup_internal_fix_20261002 AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        conn.execute(f"INSERT INTO cash_flow_data_backup_internal_fix_20261002 SELECT *, ? FROM cash_flow_data WHERE id IN ({','.join('?'*len(chunk))})", [run_id] + chunk)
    for i, col, old, new, rule in changes:
        conn.execute(f"UPDATE cash_flow_data SET {col}=?, updated_at=? WHERE id=?", (new, now, i))
    conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
    meta = {r["id"]: r for r in rows.values()}
    conn.executemany("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(now, i, meta[i]["stock_code"], meta[i]["year"], meta[i]["quarter"], 1 if meta[i]["is_annual"] else 0, meta[i]["report_type"],
                       col, old, new, rule, "DB 내부 신뢰값 재계산", run_id) for i, col, old, new, rule in changes])
    conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "cash_flow_data", "Q4 누적=연간, 3개월=신뢰 누적 차분, FnGuide 연간 감가상각 부풀림", len(changes),
                  "R1/R2/R3 (scripts/review/fix_cashflow_internal_20261002.py)", json.dumps(dict(collections.Counter(c[4][:2] for c in changes)), ensure_ascii=False),
                  "표본 평가 후 적용", "독립 재검토 2단계", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
