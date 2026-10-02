#!/usr/bin/env python3
"""DART 재수집값(dart_cf_full.jsonl)으로 2023년 이후 financial_data / cash_flow_data 를 맞춘다(2026-10-02 독립 재검토).

원칙(CLAUDE.md 재무 무결성 규칙): DART가 기준축. 값이 허용오차(max(100만원, 0.5%)) 안이면 손대지 않는다.
다르거나 비어 있을 때만 DART 값으로 바꾸고, 바꾼 필드는 한 건씩 financial_fix_log / cashflow_fix_log 에 남긴다(run_id 필수).

대상과 규칙(종목의 재수집 재무제표 구분 fs 와 같은 report_type 행만):
  financial_data
    - 분기(Q1~Q3, is_annual=false)·연간(is_annual=true, quarter 0/4 모두): 매출·영업이익·자산·부채 = DART.
      순이익·자본은 DB가 DART 전체 또는 지배 중 하나와 맞으면 유지(기준 혼재 방지), 둘 다 아니면 DART 전체값.
    - Q4(quarter=4, is_annual=false): 손익 = 연간 − (Q1+Q2+Q3 3개월), 재무상태 = 연간. 네 보고서가 모두 있을 때만.
  cash_flow_data (영업·투자·재무·CapEx, 감가상각은 DART에 있을 때만)
    - Q1~Q3: 누적 칸 = DART 누적(YTD), 3개월 칸 = YTD − 직전 분기 YTD (Q1은 YTD).
    - Q4(is_annual=false): 누적 칸 = 연간, 3개월 칸 = 연간 − Q3 YTD.
    - 연간(is_annual=true): 누적 칸 = 연간.
    - CapEx·감가상각은 절대값으로 저장(기존 양수 관례).
    - FnGuide 출처 연간 감가상각(표본 54건 중 48건 오류, DART의 중앙값 3.6배)은 DART 값이 없으면 NULL.
백업: financial_data_backup_dart_refetch_20261002, cash_flow_data_backup_dart_refetch_20261002 (바뀐 행 전체).
사용: venv/bin/python scripts/review/apply_dart_refetch_20261002.py [--apply] [--codes 005930,000660]
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

SRC = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_full.jsonl"
CF_FIELDS = {"ocf": "operating_cf", "icf": "investing_cf", "fcf": "financing_cf", "capex": "capex", "depreciation": "depreciation"}
ABS = {"capex", "depreciation"}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def load():
    t = {}
    for line in open(SRC):
        d = json.loads(line)
        if d.get("ok") and d.get("fs") and d.get("vals"):
            t[(d["code"], d["year"], d["q"])] = (d["fs"], d["vals"])
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--codes", default="")
    a = ap.parse_args()
    truth = load()
    if a.codes:
        keep = set(a.codes.split(","))
        truth = {k: v for k, v in truth.items() if k[0] in keep}
    codes = sorted({k[0] for k in truth})
    print(f"DART 재수집 {len(truth)}건, {len(codes)}종목")
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    run_id = f"dart_refetch_apply_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    fin_changes, cf_changes = [], []   # (row_id, code, year, q, is_annual, fs, field, old, new, rule)
    stats = collections.Counter()

    def ytd(code, year, q, f):
        x = truth.get((code, year, q))
        return None if not x else x[1].get(f)

    for i in range(0, len(codes), 300):
        chunk = codes[i:i + 300]
        ph = ",".join("?" * len(chunk))
        fd = conn.execute(f"""SELECT id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,
                               total_liabilities,total_equity FROM financial_data WHERE year>=2023 AND stock_code IN ({ph})""", chunk).fetchall()
        for r in fd:
            rid, code, year, quarter, is_ann, fs = r[0], r[1], r[2], r[3], r[4], r[5]
            pq = 0 if is_ann else quarter
            if pq in (0, 1, 2, 3):
                x = truth.get((code, year, pq))
                if not x or x[0] != fs:
                    continue
                v = x[1]
                want = {"revenue": v.get("revenue"), "operating_profit": v.get("operating_profit"),
                        "total_assets": v.get("total_assets"), "total_liabilities": v.get("total_liabilities")}
                if r[8] is None or not (close(r[8], v.get("ni_total")) or close(r[8], v.get("ni_parent"))):
                    want["net_income"] = v.get("ni_total")
                if r[11] is None or not (close(r[11], v.get("equity_total")) or close(r[11], v.get("equity_parent"))):
                    want["total_equity"] = v.get("equity_total")
                rule = "DART 원문값(분기 3개월/연간)"
            else:  # Q4 파생
                xs = [truth.get((code, year, k)) for k in (0, 1, 2, 3)]
                if not all(xs) or any(x[0] != fs for x in xs):
                    continue
                ann, q1, q2, q3 = (x[1] for x in xs)
                want = {}
                for f in ("revenue", "operating_profit"):
                    if all(f in z for z in (ann, q1, q2, q3)):
                        want[f] = ann[f] - q1[f] - q2[f] - q3[f]
                if all("ni_total" in z for z in (ann, q1, q2, q3)):
                    tot = ann["ni_total"] - q1["ni_total"] - q2["ni_total"] - q3["ni_total"]
                    par = (ann["ni_parent"] - q1["ni_parent"] - q2["ni_parent"] - q3["ni_parent"]
                           if all("ni_parent" in z for z in (ann, q1, q2, q3)) else None)
                    if r[8] is None or not (close(r[8], tot) or (par is not None and close(r[8], par))):
                        want["net_income"] = tot
                want["total_assets"] = ann.get("total_assets")
                want["total_liabilities"] = ann.get("total_liabilities")
                if r[11] is None or not (close(r[11], ann.get("equity_total")) or close(r[11], ann.get("equity_parent"))):
                    want["total_equity"] = ann.get("equity_total")
                rule = "Q4 = DART 연간 − (Q1+Q2+Q3), 재무상태 = 연간"
            cols = {"revenue": 6, "operating_profit": 7, "net_income": 8, "total_assets": 9, "total_liabilities": 10, "total_equity": 11}
            for f, new in want.items():
                if new is None:
                    continue
                old = r[cols[f]]
                if close(old, new):
                    stats[("financial_data", f, "OK")] += 1
                    continue
                stats[("financial_data", f, "NULL채움" if old is None else "정정")] += 1
                fin_changes.append((rid, code, year, quarter, is_ann, fs, f, old, new, rule))

        cf = conn.execute(f"""SELECT id,stock_code,year,quarter,is_annual,report_type,operating_cf,investing_cf,financing_cf,capex,depreciation,
                               operating_cf_q,investing_cf_q,financing_cf_q,capex_q,depreciation_q,data_source FROM cash_flow_data
                               WHERE year>=2023 AND stock_code IN ({ph})""", chunk).fetchall()
        for r in cf:
            rid, code, year, quarter, is_ann, fs, src = r[0], r[1], r[2], r[3], r[4], r[5], r[16]
            if is_ann or quarter == 0:
                x = truth.get((code, year, 0))
                if not x or x[0] != fs:
                    continue
                cum = {k: x[1].get(k) for k in CF_FIELDS}
                qv = {}
            elif quarter in (1, 2, 3):
                x = truth.get((code, year, quarter))
                if not x or x[0] != fs:
                    continue
                cum = {k: x[1].get(k) for k in CF_FIELDS}
                prev = truth.get((code, year, quarter - 1)) if quarter > 1 else None
                qv = {}
                for k in CF_FIELDS:
                    if quarter == 1:
                        qv[k] = cum[k]
                    elif prev and prev[0] == fs and cum[k] is not None and prev[1].get(k) is not None:
                        qv[k] = cum[k] - prev[1][k]
            elif quarter == 4:
                x, x3 = truth.get((code, year, 0)), truth.get((code, year, 3))
                if not x or x[0] != fs:
                    continue
                cum = {k: x[1].get(k) for k in CF_FIELDS}
                qv = {k: (cum[k] - x3[1][k]) if (x3 and x3[0] == fs and cum[k] is not None and x3[1].get(k) is not None) else None
                      for k in CF_FIELDS}
            else:
                continue
            for k, col in CF_FIELDS.items():
                idx_c = 6 + list(CF_FIELDS).index(k)
                idx_q = 11 + list(CF_FIELDS).index(k)
                new_c = abs(cum[k]) if (cum.get(k) is not None and k in ABS) else cum.get(k)
                old_c = r[idx_c]
                old_c_cmp = abs(old_c) if (old_c is not None and k in ABS) else old_c
                if new_c is not None:
                    if close(old_c_cmp, new_c) and not (k in ABS and old_c is not None and old_c < 0):
                        stats[("cash_flow_data", col, "OK")] += 1
                    else:
                        stats[("cash_flow_data", col, "NULL채움" if old_c is None else "정정")] += 1
                        cf_changes.append((rid, code, year, quarter, is_ann, fs, col, old_c, new_c, "DART 누적(YTD)/연간"))
                elif k == "depreciation" and (is_ann or quarter == 0) and (src or "").startswith("fnguide") and old_c is not None:
                    stats[("cash_flow_data", col, "FnGuide부풀림→NULL")] += 1
                    cf_changes.append((rid, code, year, quarter, is_ann, fs, col, old_c, None, "FnGuide 연간 감가상각 부풀림(표본 DART 중앙값 3.6배), DART값 없음 → NULL"))
                if not (is_ann or quarter == 0):
                    nq = qv.get(k)
                    if nq is None:
                        continue
                    nq = abs(nq) if k in ABS else nq
                    oq = r[idx_q]
                    oq_cmp = abs(oq) if (oq is not None and k in ABS) else oq
                    if close(oq_cmp, nq):
                        stats[("cash_flow_data", col + "_q", "OK")] += 1
                    else:
                        stats[("cash_flow_data", col + "_q", "NULL채움" if oq is None else "정정")] += 1
                        cf_changes.append((rid, code, year, quarter, is_ann, fs, col + "_q", oq, nq, "3개월 = DART YTD 차분"))

    for k in sorted(stats):
        print(k, stats[k])
    print(f"financial_data 변경 {len(fin_changes)}필드 / cash_flow_data 변경 {len(cf_changes)}필드")
    if not a.apply:
        json.dump({"fin": fin_changes[:200], "cf": cf_changes[:200]}, open(SRC.parent / "apply_preview.json", "w"), default=str, ensure_ascii=False)
        print("dry-run — 미리보기 apply_preview.json (적용은 --apply)")
        return

    fin_ids = sorted({c[0] for c in fin_changes})
    cf_ids = sorted({c[0] for c in cf_changes})
    conn.execute("CREATE TABLE IF NOT EXISTS financial_data_backup_dart_refetch_20261002 AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
    conn.execute("CREATE TABLE IF NOT EXISTS cash_flow_data_backup_dart_refetch_20261002 AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
    for i in range(0, len(fin_ids), 1000):
        ids = fin_ids[i:i + 1000]
        conn.execute(f"INSERT INTO financial_data_backup_dart_refetch_20261002 SELECT *, ? FROM financial_data WHERE id IN ({','.join('?'*len(ids))})", [run_id] + ids)
    for i in range(0, len(cf_ids), 1000):
        ids = cf_ids[i:i + 1000]
        conn.execute(f"INSERT INTO cash_flow_data_backup_dart_refetch_20261002 SELECT *, ? FROM cash_flow_data WHERE id IN ({','.join('?'*len(ids))})", [run_id] + ids)
    for rid, code, year, q, is_ann, fs, f, old, new, rule in fin_changes:
        conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
    for rid, code, year, q, is_ann, fs, f, old, new, rule in cf_changes:
        conn.execute(f"UPDATE cash_flow_data SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
    conn.execute("UPDATE financial_data SET data_source=COALESCE(data_source,'')||'+dart_refetch_20261002' WHERE id IN (SELECT id FROM financial_data_backup_dart_refetch_20261002 WHERE run_id=?)", (run_id,))
    conn.execute("UPDATE cash_flow_data SET data_source=COALESCE(data_source,'')||'+dart_refetch_20261002' WHERE id IN (SELECT id FROM cash_flow_data_backup_dart_refetch_20261002 WHERE run_id=?)", (run_id,))
    for tbl, rows in (("financial_fix_log", fin_changes), ("cashflow_fix_log", cf_changes)):
        conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
        conn.executemany(f"""INSERT INTO {tbl}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, year, q, (1 if is_ann else 0) if tbl == "financial_fix_log" else bool(is_ann), fs, f, old, new, rule,
                           "DART fnlttSinglAcntAll 재수집", run_id) for rid, code, year, q, is_ann, fs, f, old, new, rule in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "financial_data+cash_flow_data", "2023년 이후 분기·연간·Q4 손익/재무상태/현금흐름", len(fin_changes) + len(cf_changes),
                  "DART와 max(100만원,0.5%) 넘게 다르거나 NULL인 필드만 DART값으로", json.dumps(dict(collections.Counter((c[6]) for c in fin_changes + cf_changes)), ensure_ascii=False)[:900],
                  "DART fnlttSinglAcntAll", "scripts/review/apply_dart_refetch_20261002.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
