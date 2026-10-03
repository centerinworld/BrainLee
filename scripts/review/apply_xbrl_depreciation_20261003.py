#!/usr/bin/env python3
"""XBRL 주석 감가상각(xbrl_depreciation.jsonl)으로 연간 감가상각 두 컬럼을 정의대로 맞춘다(2026-10-03).

정의(docs/FINANCIAL_REREVIEW_20261002.md §9):
  cash_flow_data.depreciation (is_annual 행)        = 유형자산 감가상각(DepreciationPropertyPlantAndEquipment),
                                                    없으면 현금흐름 조정 감가상각(AdjustmentsForDepreciationExpense).
                                                    조정값이 유형값의 1.5배를 넘으면 유형값이 일부만 잡혔을 수 있어 건너뜀.
  financial_data.depreciation_amortization (연간)    = 위 값 + 무형자산 상각(Amortisation…, 없으면 조정 상각). 사용권자산 상각 제외(FnGuide 표시 기준).
CFS 행 ↔ XBRL 연결(ConsolidatedMember 또는 차원 없음), OFS 행 ↔ SeparateMember. data_lock 잠금 연도는 건너뜀.
사용: --apply 없으면 dry-run.
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

SRC = ROOT / "research_outputs" / "financial_rereview_20261002" / "xbrl_depreciation.jsonl"


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


BASIS = "adj" if "--basis-adj" in sys.argv else "ppe"  # 2026-10-03: adj = FnGuide 실측 정의(현금흐름표 조정 감가상각) — 사용자 승인 대기


def values():
    out = {}
    for line in open(SRC):
        d = json.loads(line)
        for fs, v in d.get("vals", {}).items():
            p, a = v.get("dep_ppe"), v.get("adj_dep")
            if BASIS == "adj":
                # FnGuide 유형자산감가상각비 = 현금흐름표 조정 '감가상각비'(사용권 포함), 무형 = 조정 '무형자산상각비'
                dep = a or p
                amort = v.get("adj_amort") or v.get("amort") or 0
                if dep:
                    out[(d["code"], d["year"], fs)] = (dep, dep + amort, d.get("rcept_no"))
                continue
            if p and a and a / p > 1.5:
                continue
            dep = p or a
            if not dep:
                continue
            amort = v.get("amort") or v.get("adj_amort") or 0
            # 2026-10-03: FnGuide 표시 기준(유형자산감가상각비 + 기타무형자산상각비 + 개발비상각)에 맞춰 사용권자산 상각은 제외
            out[(d["code"], d["year"], fs)] = (dep, dep + amort, d.get("rcept_no"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--basis-adj", action="store_true", help="FnGuide 실측 정의(조정 감가상각, 사용권 포함) — 사용자 승인 후에만 --apply")
    a = ap.parse_args()
    x = values()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    # 2026-10-03: 보고통화 종목(XBRL 값이 원통화)은 제외 — 원화 환산값 보호(FINANCIAL_STATEMENTS.md §2-6)
    fx_codes = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    x = {k: v for k, v in x.items() if k[0] not in fx_codes}
    cf_ch, fd_ch, st = [], [], collections.Counter()
    for rid, code, y, q, fs, dep in conn.execute("""SELECT id, stock_code, year, quarter, report_type, depreciation FROM cash_flow_data
                                                    WHERE is_annual AND year BETWEEN 2021 AND 2025""").fetchall():
        v = x.get((code, y, fs))
        if not v:
            continue
        if (code, y, "cash_flow_data") in locks:
            st["cf 잠금"] += 1
            continue
        if close(abs(dep) if dep is not None else None, v[0]):
            st["cf 이미 일치"] += 1
        else:
            st["cf NULL채움" if dep is None else "cf 정정"] += 1
            cf_ch.append((rid, code, y, q, fs, dep, v[0]))
    for rid, code, y, q, fs, da in conn.execute("""SELECT id, stock_code, year, quarter, report_type, depreciation_amortization FROM financial_data
                                                   WHERE is_annual AND year BETWEEN 2021 AND 2025""").fetchall():
        v = x.get((code, y, fs))
        if not v:
            continue
        if (code, y, "financial_data") in locks:
            st["fd 잠금"] += 1
            continue
        if close(abs(da) if da is not None else None, v[1]):
            st["fd 이미 일치"] += 1
        else:
            st["fd NULL채움" if da is None else "fd 정정"] += 1
            fd_ch.append((rid, code, y, q, fs, da, v[1]))
    print(dict(st), "XBRL 종목·연도·구분", len(x))
    if not a.apply:
        return
    run_id = f"xbrl_dep_apply_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl, rows, col, log in (("cash_flow_data", cf_ch, "depreciation", "cashflow_fix_log"),
                                ("financial_data", fd_ch, "depreciation_amortization", "financial_fix_log")):
        if not rows:
            continue
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_xbrl_dep_20261003 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        ids = [r[0] for r in rows]
        for i in range(0, len(ids), 1000):
            ch = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {tbl}_backup_xbrl_dep_20261003 SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?'*len(ch))})", [run_id] + ch)
        conn.executemany(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", [(new, now, rid) for rid, *_, new in rows])
        conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
        conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, fs, col, old, new, "XBRL 주석 감가상각(정의 §9)", "DART fnlttXbrl", run_id)
                          for rid, code, y, q, fs, old, new in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "cash_flow_data+financial_data", "연간 감가상각(2021~2025) XBRL 주석 기준", len(cf_ch) + len(fd_ch),
                  "depreciation=유형자산 감가상각(없으면 조정), D&A=+무형(사용권 제외, FnGuide 기준)", json.dumps(dict(st), ensure_ascii=False), "XBRL 값",
                  "scripts/review/apply_xbrl_depreciation_20261003.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
