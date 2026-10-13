#!/usr/bin/env python3
"""연간 재작성값(정정값) 반영 — FINANCIAL_STATEMENTS.md §2-5(표시값 = 최신 재작성값, 최초값 보존), 2026-10-04.

재작성값 원문: Y+1년 사업보고서의 전기(frmtrm) 칸(`vals_prev`, fetch_dart_cashflow_20261002.py).
원칙 0(DART 파싱값 불신): **외부(FnGuide 원문·wcomp 캡처, 네이버)가 재작성값과 일치하는 칸만 반영**한다.
  외부 없음/외부가 현재 DB와 일치/외부가 둘 다와 다름 → 반영하지 않고 후보 파일로만 남긴다.
최초값은 financial_facts_pit(as_reported)와 백업·fix_log(old_value)에 보존된다.
대상 칸: financial_data 연간 revenue·operating_profit·net_income(연결=지배, 별도=전체)·total_assets·total_liabilities·total_equity(연결=지배, 별도=자본총계),
         cash_flow_data 연간 operating_cf·investing_cf·financing_cf·capex. 보고통화 종목·data_lock 잠금은 제외.
허용오차 max(200만원, 0.5%). 기본 dry-run.
"""
import argparse
import collections
import gzip
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from compare_db_vs_fnguide_raw_20261003 import RAW, parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402
from financial_rereview_20261002 import extract  # noqa: E402

RAW_DART = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
FD = {"revenue": "revenue", "operating_profit": "operating_profit", "total_assets": "total_assets", "total_liabilities": "total_liabilities"}
CF = {"operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf", "capex": "capex"}


def close(a, b):
    """네이버(억 단위 반올림) 대조용 — 기존 허용오차."""
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def close_abs(a, b):
    """DB·재작성값·FnGuide(백만원) 대조용 — 200만원 절대 허용(2026-10-09: 0.5%는 '-40억' 같은 억 단위 반올림값을 가렸다)."""
    return a is not None and b is not None and abs(a - b) <= 2e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    prev = {}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("q") == 0 and d.get("fs") and d.get("vals_prev"):
                prev[(d["code"], d["year"] - 1, d["fs"])] = d["vals_prev"]
    # 2026-10-09: 위 두 jsonl에 없는 원문(별도 추가·비12월·key4 수집분 등)은 원문 폴더의 다음연도 사업보고서 전기 칸에서 보충.
    # 놓쳤던 예: 065150 2023 별도(원문 자체 1,000배 — 2024 보고서 전기 칸은 정상, FnGuide 일치), 347700 2024 순이익 재작성.
    for p in RAW_DART.glob("*/20[1-2][0-9]_0_*.json.gz"):
        y1, _, fs = p.name[:-8].split("_")
        k = (p.parent.name, int(y1) - 1, fs)
        v = extract(json.loads(gzip.decompress(p.read_bytes())), "frmtrm_amount")
        d = prev.setdefault(k, {})
        for f, x in v.items():  # 칸 단위 보충: jsonl에 비어 있는 칸(예: 비지배 없는 회사의 ni_parent)만
            if d.get(f) is None:
                d[f] = x
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    # 외부 값: FnGuide 원문 → wcomp 캡처 → 네이버(연결 매출·영업이익)
    ext = {}
    for p in (RAW.iterdir() if RAW.exists() else []):
        if p.is_dir():
            r, _ = parse_code(p.name)
            for (y, q, fs), v in r.items():
                if q == 0:
                    ext[(p.name, y, fs)] = v
    for r in conn.execute("""SELECT DISTINCT ON (stock_code, year, report_type) stock_code, year, report_type, revenue, operating_profit, total_assets,
                                    total_liabilities, operating_cf, investing_cf, financing_cf FROM financial_source_snapshot
                             WHERE data_source='fnguide' AND is_annual=1 AND source_url LIKE 'https://wcomp%' ORDER BY stock_code, year, report_type, fetched_at DESC""").fetchall():
        r = tuple(r)
        k = (r[0], r[1], r[2])
        if k not in ext:
            ext[k] = {f: v for f, v in zip(["revenue", "operating_profit", "total_assets", "total_liabilities", "operating_cf", "investing_cf", "financing_cf"], r[3:])
                      if v is not None}
    # 2026-10-09: 위 두 곳에 없는 칸은 현행 검증표의 FnGuide 값(정본 대조와 같은 값, 순이익·자본은 지배 기준)으로 보충
    for r in conn.execute("""SELECT stock_code, year, report_type, field, fnguide_value FROM financial_field_verification
                             WHERE quarter=0 AND fnguide_value IS NOT NULL""").fetchall():
        d = ext.setdefault((r[0], r[1], r[2]), {})
        d.setdefault(r[3], float(r[4]))
    nv = {(r[0], r[1]): {"revenue": r[2], "operating_profit": r[3]} for r in map(tuple, conn.execute(
        "SELECT stock_code, year, revenue, operating_profit FROM naver_financial WHERE is_annual=1").fetchall())}
    plan, st, cand = [], collections.Counter(), []
    for tbl, cols in (("financial_data", list(FD) + ["net_income", "total_equity"]), ("cash_flow_data", list(CF))):
        for row in conn.execute(f"SELECT id, stock_code, year, report_type, {','.join(cols)} FROM {tbl} WHERE is_annual AND year>=2016").fetchall():
            row = tuple(row)
            rid, code, y, fs = row[:4]
            pv = prev.get((code, y, fs))
            if not pv or code in fx or (code, y, tbl) in locks:
                continue
            for f, dbv in zip(cols, row[4:]):
                if f in FD:
                    k = FD[f]
                elif f in CF:
                    k = CF[f]
                elif f == "net_income":
                    k = "ni_parent" if fs == "CFS" else "ni_total"
                else:
                    k = "equity_parent" if fs == "CFS" else "equity_total"
                rv = pv.get(k)
                if rv is None or dbv is None:
                    continue
                if f == "capex":
                    rv, dbv = abs(rv), abs(dbv)
                if close_abs(dbv, rv):
                    continue  # 재작성 없음(또는 이미 반영)
                st["재작성 차이 칸"] += 1
                e = (ext.get((code, y, fs)) or {}).get(f)
                e = abs(e) if (e is not None and f == "capex") else e
                n = (nv.get((code, y)) or {}).get(f) if fs == "CFS" else None
                if (e is not None and close_abs(e, rv)) or (e is None and n is not None and close(n, rv)):
                    st["외부가 재작성값 확인 → 반영"] += 1
                    plan.append((tbl, rid, code, y, fs, f, row[4 + cols.index(f)], rv if f != "capex" else -abs(rv) if row[4 + cols.index(f)] < 0 else rv,
                                 "FnGuide" if e is not None else "네이버"))
                elif e is not None and close_abs(e, dbv):
                    st["외부가 현재 DB와 일치 → 보류(재작성 추출 의심)"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, e, "외부=DB"))
                elif e is None and n is None:
                    st["외부 값 없음 → 보류"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, None, "외부 없음"))
                else:
                    st["외부가 둘 다와 다름 → 보류"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, e if e is not None else n, "셋 다 다름"))
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    pd.DataFrame(cand, columns=["table", "stock_code", "year", "fs", "field", "db", "restated", "external", "reason"]).to_csv(
        SRC / "restated_annual_hold_candidates.csv", index=False)
    pd.DataFrame(plan, columns=["table", "id", "stock_code", "year", "fs", "field", "old", "new", "confirmed_by"]).to_csv(
        SRC / "restated_annual_apply_plan.csv", index=False)
    if not a.apply or not plan:
        pv = rederive_q4(conn, plan, None, None, write=False)
        pd.DataFrame(pv["changes"], columns=["table", "id", "field", "old", "new", "stock_code", "year", "fs"]).to_csv(SRC / "restated_annual_q4_plan.csv", index=False)
        print("4분기 재계산 미리보기", json.dumps(pv["stats"], ensure_ascii=False))
        return
    run_id = f"restated_annual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        rows = [p for p in plan if p[0] == tbl]
        if not rows:
            continue
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_restated_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        ids = sorted({p[1] for p in rows})
        for i in range(0, len(ids), 1000):
            ch = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {tbl}_backup_restated_20261004 SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ch))})", [run_id] + ch)
        for _, rid, code, y, fs, f, old, new, by in rows:
            conn.execute(f"UPDATE {tbl} SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
        conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, fs, f, old, new, f"재작성값 반영(§2-5) — {by} 확인", "DART 다음연도 사업보고서 전기 칸", run_id)
                          for _, rid, code, y, fs, f, old, new, by in rows])
    q4 = rederive_q4(conn, plan, run_id, now)
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "연간 재작성값(외부 확인분만) + 그 종목-연도 4분기 재계산", len(plan) + len(q4["changes"]),
                  "표시값=최신 재작성값(§2-5), 최초값은 PIT·백업 보존. 4분기 = 연간 − 3분기 보고서 누적(손익, D-6)·연간(재무상태)·누적=연간/3개월=연간−3분기 누적(현금흐름)",
                  json.dumps(dict(st), ensure_ascii=False), json.dumps(q4["stats"], ensure_ascii=False),
                  "scripts/review/apply_restated_annual_20261004.py", run_id))
    conn.commit()
    # readback: 계획한 값이 실제로 들어갔는지(연간 + 4분기)
    bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[5]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - float(p[7])) > 0.5]
    bad += [c for c in q4["changes"] if abs(float(conn.execute(f"SELECT {c[2]} FROM {c[0]} WHERE id=?", (c[1],)).fetchone()[0]) - c[4]) > 0.5]
    sync = _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"} | {c[1] for c in q4["changes"] if c[0] == "financial_data"},
                               {p[1] for p in plan if p[0] == "cash_flow_data"} | {c[1] for c in q4["changes"] if c[0] == "cash_flow_data"})
    res = {"run_id": run_id, "annual_cells": len(plan), "q4": q4["stats"], "q4_cells": len(q4["changes"]),
           "post_readback": {"planned": len(plan) + len(q4["changes"]), "mismatch": len(bad), "pass": not bad}, "canonical_sync": sync}
    (SRC / f"restated_annual_{run_id[-15:]}_apply.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print("적용 완료", json.dumps({k: v for k, v in res.items() if k != "canonical_sync"}, ensure_ascii=False, default=str))


Q4_FIN_FLOW = ("revenue", "operating_profit", "net_income")
Q4_CF = ("operating_cf", "investing_cf", "financing_cf", "capex")


def rederive_q4(conn, plan, run_id, now, write=True):
    """2026-10-09: 연간값을 바꾼 종목-연도-구분·필드의 4분기(비연간 quarter=4) 행을 새 연간값으로 다시 계산.
    예전 4분기 행은 고치기 전 연간값(억 단위 반올림값 등)에서 파생돼 같이 틀려 있었다(311390 2024 4분기 매출 = 403억 − 1~3분기).
    1~3분기·연간 행이 여러 개이고 값이 서로 다르면 그 필드는 건너뛴다(모호)."""
    st, changes = collections.Counter(), []
    overlay = {(p[0], p[1], p[5]): float(p[7]) for p in plan}  # dry-run에서도 새 연간값으로 계산
    touched = collections.defaultdict(set)
    for tbl, rid, code, y, fs, f, *_ in plan:
        touched[(tbl, code, y, fs)].add(f)
    for (tbl, code, y, fs), fields in touched.items():
        cols = sorted(fields) + ([f + "_q" for f in sorted(fields)] if tbl == "cash_flow_data" else [])
        rows = [tuple(r) for r in conn.execute(f"SELECT id, quarter, is_annual, {','.join(cols)} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=?",
                                               (code, y, fs)).fetchall()]
        col = {c: i + 3 for i, c in enumerate(cols)}
        rows = [tuple(overlay.get((tbl, r[0], c), r[i]) if i >= 3 and (c := cols[i - 3]) else r[i] for i in range(len(r))) for r in rows]

        def one(sel, c):
            vals = {r[col[c]] for r in rows if sel(r) and r[col[c]] is not None}
            return vals.pop() if len(vals) == 1 else (None if not vals else "모호")

        q4rows = [r for r in rows if r[1] == 4 and not r[2]]
        if not q4rows:
            st["4분기 행 없음"] += 1
            continue
        for f in sorted(fields):
            A = one(lambda r: r[2], f)
            if A in (None, "모호"):
                st[f"연간 {A or '없음'} — 건너뜀"] += 1
                continue
            targets = []
            if tbl == "financial_data":
                if f in Q4_FIN_FLOW:
                    # D-6(사용자 승인 2026-10-09): 4분기 3개월 = 연간 − 3분기 보고서 누적(FnGuide 방식). 예전 ① 연간 − 1~3분기 합은 폐기.
                    ytd3 = fn.ytd3(code, y, fs, f)  # 공통 정의(fin_norm, 2026-10-10)
                    if ytd3 is None:
                        st["3분기 누적 원문 없음 — 건너뜀(D-6)"] += 1
                        continue
                    targets = [(f, fn.q4_flow(A, ytd3))]
                else:
                    targets = [(f, A)]
            else:
                q3 = one(lambda r: r[1] == 3 and not r[2], f)
                targets = [(f, A)] + ([(f + "_q", A - q3)] if q3 not in (None, "모호") else [])
                if q3 in (None, "모호"):
                    st["3분기 누적 없음/모호 — 3개월 칸 건너뜀"] += 1
            for r in q4rows:
                for c, v in targets:
                    old = r[col[c]]
                    if old is not None and abs(old - v) <= 0.5:
                        st["이미 같음"] += 1
                        continue
                    changes.append((tbl, r[0], c, old, v, code, y, fs))
                    st[f"{tbl} 4분기 재계산"] += 1
    if not write:
        return {"stats": dict(st), "changes": changes}
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        ch = [c for c in changes if c[0] == tbl]
        if not ch:
            continue
        ids = sorted({c[1] for c in ch})
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {tbl}_backup_restated_20261004 SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
        for _, rid, c, old, v, *_ in ch:
            conn.execute(f"UPDATE {tbl} SET {c}=?, updated_at=? WHERE id=?", (v, now, rid))
        conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
        conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,4,0,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, fs, c, old, v, "4분기 재계산 — 같은 실행에서 연간 재작성값 반영(D-6: 연간 − 3분기 누적)", "연간(재작성) − 3분기 보고서 누적", run_id)
                          for _, rid, c, old, v, code, y, fs in ch])
    return {"stats": dict(st), "changes": changes}


if __name__ == "__main__":
    main()
