#!/usr/bin/env python3
"""정본 재동기화 (2026-10-13, 외부 검토 P1 두 건). 기본 dry-run.

A) canonical_cashflow_data 분기 3개월 칸(operating_cf_q 등): 공용 canonical_sync가 이 칸을 payload에서 빼 정본 upsert가 NULL로 덮었다
   (원장에는 값이 있고 정본은 NULL인 행 실측 21,509). 고친 canonical_sync(q 칸 포함·null-preserving)로 정본이 가리키는 원장 대표행을 다시 동기화한다.
B) canonical_financial_data 주당·자본금 칸(capital_stock·eps·bps·dps·roe): sparse 원장 행 동기화로 비워진 칸을 백업(canonical_financial_data_backup_ofs_extra_20261009)의
   마지막 비어 있지 않은 값으로 복원 — 원장 대표행도 그 칸이 NULL일 때만. 매출·영업이익·순이익·재무상태 칸은 RAW_CONTRADICTION·NO_SAME_FS_ANNUAL 등 의도한 빈 칸이 섞여 있어 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
QCOLS = ("operating_cf_q", "investing_cf_q", "financing_cf_q", "capex_q")
PER_SHARE = ("capital_stock", "eps", "bps", "dps", "roe")
BK = "canonical_financial_data_backup_ofs_extra_20261009"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    gap = " OR ".join(f"(r.{c} IS NOT NULL AND k.{c} IS NULL)" for c in QCOLS)
    cf_ids = sorted({r[0] for r in conn.execute(
        f"SELECT k.source_row_id FROM canonical_cashflow_data k JOIN cash_flow_data r ON r.id=k.source_row_id WHERE {gap}").fetchall()})
    # B: 복원 대상 (정본 NULL · 원장 대표행 NULL · 백업에 값)
    fin = []
    for c in PER_SHARE:
        for sc, y, q, ia, rt, v in conn.execute(f"""
            SELECT k.stock_code, k.year, k.quarter, k.is_annual, k.report_type,
                   (SELECT b.{c} FROM {BK} b WHERE b.stock_code=k.stock_code AND b.year=k.year AND b.quarter=k.quarter AND b.is_annual=k.is_annual
                      AND b.report_type=k.report_type AND b.{c} IS NOT NULL ORDER BY b.run_id DESC LIMIT 1)
            FROM canonical_financial_data k LEFT JOIN financial_data r ON r.id=k.source_row_id
            WHERE k.{c} IS NULL AND (r.id IS NULL OR r.{c} IS NULL)""").fetchall():
            if v is not None:
                fin.append((sc, y, q, ia, rt, c, float(v)))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "cf_rows_to_resync": len(cf_ids), "fin_cells_to_restore": len(fin)}
    if a.apply:
        run_id = f"canonical_resync_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for i in range(0, len(fin), 1):
            sc, y, q, ia, rt, c, v = fin[i]
            conn.execute("UPDATE canonical_financial_data SET " + c + "=?, updated_at=? WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                         (v, now, sc, y, q, ia, rt))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "canonical_financial_data", "sparse 동기화로 비워진 주당·자본금 칸 복원(백업의 마지막 값)", len(fin),
                      "외부 검토 P1 — null-preserving 정본 동기화", "NULL", "백업 값", "scripts/review/resync_canonical_20261013.py", run_id))
        conn.commit()
        res["canonical_sync_cf"] = {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, set(), set(cf_ids)).items() if k not in ("mismatch_sample", "refused_sample")}
        res["run_id"] = run_id
        res["cf_q_gap_after"] = conn.execute(f"SELECT count(*) FROM canonical_cashflow_data k JOIN cash_flow_data r ON r.id=k.source_row_id WHERE {gap}").fetchone()[0]
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
