#!/usr/bin/env python3
"""DART 재수집 없이 financial_data 를 DB 내부 값으로 정정한다(2026-10-02 독립 재검토 2단계).

  F1 Q4(quarter=4, is_annual=false) 손익 = 연간 − (Q1+Q2+Q3).  같은 report_type, 네 값 모두 있을 때만.
     연간 행은 is_annual 행 중 FnGuide 아닌 것 우선. 순이익은 연간·분기가 같은 기준(대부분 전체)이라는 전제 —
     표본 평가로 정답률이 오르는 필드만 적용한다.
  F2 Q4 재무상태(자산·부채·자본) = 연간 행 값.
  F3 부채총계 NULL = 자산 − 자본 (부채≈자산 오입력 정정과 같은 항등식, 표본 72건 중 71건 DART 일치).
--eval: 표본(dart_raw.jsonl) 전후 비교·회귀 집계. --apply [--fields a,b]: 백업·financial_fix_log·data_fix_log 후 적용.
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
PL = ["revenue", "operating_profit", "net_income"]
BS = ["total_assets", "total_liabilities", "total_equity"]
COLS = ["id", "stock_code", "year", "quarter", "is_annual", "report_type", "data_source"] + PL + BS


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def plan(rows, fields):
    by = collections.defaultdict(dict)
    for r in rows.values():
        k = (r["stock_code"], r["year"], r["report_type"])
        by[k][("A", r["quarter"]) if r["is_annual"] else r["quarter"]] = r
    out = []
    for g in by.values():
        ann = None
        for k in (("A", 4), ("A", 0)):
            a = g.get(k)
            if a and not (a["data_source"] or "").startswith("fnguide"):
                ann = a
                break
        ann = ann or g.get(("A", 0)) or g.get(("A", 4))
        q4 = g.get(4)
        if q4 and ann:
            for f in PL:
                if f not in fields:
                    continue
                qs = [g.get(i) for i in (1, 2, 3)]
                if all(qs) and ann[f] is not None and all(x[f] is not None for x in qs):
                    new = ann[f] - sum(x[f] for x in qs)
                    if not close(q4[f], new):
                        out.append((q4["id"], f, q4[f], new, "F1 Q4 = 연간 − (Q1+Q2+Q3)"))
            for f in BS:
                if f in fields and ann[f] is not None and not close(q4[f], ann[f]):
                    out.append((q4["id"], f, q4[f], ann[f], "F2 Q4 재무상태 = 연간"))
        if "liab_fill" in fields:
            for r in g.values():
                if r["total_liabilities"] is None and r["total_assets"] and r["total_equity"] is not None:
                    if not any(o[0] == r["id"] and o[1] == "total_liabilities" for o in out):
                        out.append((r["id"], "total_liabilities", None, r["total_assets"] - r["total_equity"], "F3 부채 NULL = 자산 − 자본"))
    return out


def load(conn, codes=None):
    cond = "" if not codes else " AND stock_code IN (%s)" % ",".join("?" * len(codes))
    return {r[0]: dict(zip(COLS, r)) for r in conn.execute(f"SELECT {','.join(COLS)} FROM financial_data WHERE year>=2016{cond}", codes or []).fetchall()}


def evaluate(conn, fields):
    truth = {}
    for line in open(RAW):
        d = json.loads(line)
        truth[(d["code"], d["year"], d["q"], d["fs"])] = d["vals"]
    for (c, y, q, fs), t in list(truth.items()):
        if q == 0 and all((c, y, i, fs) in truth for i in (1, 2, 3)):
            qs = [truth[(c, y, i, fs)] for i in (1, 2, 3)]
            q4 = {}
            for f in ("revenue", "operating_profit", "ni_total", "ni_parent"):
                if f in t and all(f in x for x in qs):
                    q4[f] = t[f] - sum(x[f] for x in qs)
            for f in ("total_assets", "total_liabilities", "equity_total", "equity_parent"):
                if f in t:
                    q4[f] = t[f]
            truth[(c, y, 4, fs)] = q4
    codes = sorted({k[0] for k in truth})
    rows = load(conn, codes)
    ch = plan(rows, fields)
    after = {i: dict(r) for i, r in rows.items()}
    for i, f, o, n, _ in ch:
        after[i][f] = n
    st = collections.Counter()
    for i, r in rows.items():
        pq = 0 if r["is_annual"] else r["quarter"]
        t = truth.get((r["stock_code"], r["year"], pq, r["report_type"]))
        if not t:
            continue
        cand = {"revenue": [t.get("revenue")], "operating_profit": [t.get("operating_profit")], "net_income": [t.get("ni_total"), t.get("ni_parent")],
                "total_assets": [t.get("total_assets")], "total_liabilities": [t.get("total_liabilities")], "total_equity": [t.get("equity_total"), t.get("equity_parent")]}
        per = "Q4" if pq == 4 else ("연간" if pq == 0 else "분기")
        for f, cs in cand.items():
            cs = [c for c in cs if c is not None]
            if not cs:
                continue
            b = any(close(r[f], c) for c in cs)
            a = any(close(after[i][f], c) for c in cs)
            st[(f, per, "전OK")] += b
            st[(f, per, "후OK")] += a
            st[(f, per, "n")] += 1
            if b and not a:
                st[(f, per, "회귀")] += 1
    for f in PL + BS:
        for per in ("Q4", "분기", "연간"):
            n = st[(f, per, "n")]
            if n:
                print(f"{f:18s} {per:3s} n={n:4d} 정답 {st[(f, per, '전OK')]:4d} → {st[(f, per, '후OK')]:4d}  회귀 {st[(f, per, '회귀')]}")
    print("표본 변경", len(ch), collections.Counter(c[4][:2] for c in ch))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fields", default="revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity,liab_fill")
    a = ap.parse_args()
    fields = set(a.fields.split(","))
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    if a.eval:
        evaluate(conn, fields)
        return
    rows = load(conn)
    ch = plan(rows, fields)
    print("전체 변경", len(ch), dict(collections.Counter(c[4][:2] for c in ch)))
    if not a.apply:
        return
    run_id = f"fin_internal_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='financial_data'").fetchall()}
    ch = [c for c in ch if (rows[c[0]]["stock_code"], rows[c[0]]["year"]) not in locked]
    print("잠금 제외 후", len(ch))
    ids = sorted({c[0] for c in ch})
    conn.execute("CREATE TABLE IF NOT EXISTS financial_data_backup_internal_fix_20261002 AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
    for i in range(0, len(ids), 1000):
        chunk = ids[i:i + 1000]
        conn.execute(f"INSERT INTO financial_data_backup_internal_fix_20261002 SELECT *, ? FROM financial_data WHERE id IN ({','.join('?'*len(chunk))})", [run_id] + chunk)
    for i, f, o, n, rule in ch:
        conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (n, now, i))
    conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
    conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(now, i, rows[i]["stock_code"], rows[i]["year"], rows[i]["quarter"], 1 if rows[i]["is_annual"] else 0, rows[i]["report_type"],
                       f, o, n, rule, "DB 내부 재계산", run_id) for i, f, o, n, rule in ch])
    conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "financial_data", "Q4 손익 재계산·Q4 재무상태=연간·부채 NULL 항등식", len(ch), "F1/F2/F3 " + a.fields,
                  json.dumps(dict(collections.Counter(c[4][:2] for c in ch))), "표본 평가 후 적용", "scripts/review/fix_financial_internal_20261002.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
