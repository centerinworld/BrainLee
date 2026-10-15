#!/usr/bin/env python3
"""정본 재동기화 (2026-10-13, 외부 검토 P1 두 건). 기본 dry-run.

A) canonical_cashflow_data: 공용 canonical_sync가 분기 3개월 칸(operating_cf_q 등)을 payload에서 빼 정본 upsert가 NULL로 덮었다
   (원장에는 값이 있고 정본은 NULL인 행 실측 21,509). 또한 'rebuild_2022_2025'로 만든 정본이 억 단위로 반올림된 값을 가진 채 원장과 다른 행이 많다
   (10-13 외부 검토 P1). 대상 = 원장 값(누적 6칸·분기 3개월 4칸)이 있는데 정본이 NULL이거나 0.5 넘게 다른 같은 키 행. 고친 canonical_sync
   (q 칸 포함·null-preserving·전 칸 readback)로 정본이 가리키는 원장 대표행을 다시 동기화한다.
   정본 키(연도·분기·연간 여부)가 원장 행과 다른 행은 건드리지 않고 보류 CSV로 분리하며 reason_class로 원인을 구분한다:
   ANNUAL_Q4_CANONICAL(연간 정본이 quarter 4로 남음 → normalize_canonical_cashflow_annual_20261013.py가 정리), FISCAL_REKEY_YEAR_MISMATCH, SOURCE_ROW_KEY_MISMATCH.
B) canonical_financial_data 주당·자본금 칸(capital_stock·eps·bps·dps·roe): sparse 원장 행 동기화로 비워진 칸을 백업(canonical_financial_data_backup_ofs_extra_20261009)의
   마지막 비어 있지 않은 값으로 복원 — 원장 대표행도 그 칸이 NULL일 때만. 매출·영업이익·순이익·재무상태 칸은 RAW_CONTRADICTION·NO_SAME_FS_ANNUAL 등 의도한 빈 칸이 섞여 있어 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import csv
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
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
QCOLS = ("operating_cf_q", "investing_cf_q", "financing_cf_q", "capex_q")
ALL_CF = ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation") + QCOLS
PER_SHARE = ("capital_stock", "eps", "bps", "dps", "roe")
BK = "canonical_financial_data_backup_ofs_extra_20261009"
HOLD_FILE = OUT / "canonical_cf_q_gap_hold_20261013.csv"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    gap = " OR ".join(f"(r.{c} IS NOT NULL AND (k.{c} IS NULL OR abs(r.{c} - k.{c}) > 0.5))" for c in ALL_CF)
    same_key = "k.stock_code=r.stock_code AND k.year=r.year AND k.quarter=(CASE WHEN r.is_annual THEN 0 ELSE r.quarter END) AND k.report_type=r.report_type"
    base = f"FROM canonical_cashflow_data k JOIN cash_flow_data r ON r.id=k.source_row_id WHERE ({gap})"

    def _count():
        return (conn.execute(f"SELECT count(*) {base} AND {same_key}").fetchone()[0], conn.execute(f"SELECT count(*) {base} AND NOT ({same_key})").fetchone()[0])

    before_same, before_other = _count()
    cf_ids = sorted({r[0] for r in conn.execute(f"SELECT k.source_row_id {base} AND {same_key}").fetchall()})
    hold = conn.execute(f"SELECT k.stock_code,k.year,k.quarter,k.is_annual,k.report_type,k.source_row_id,r.year,r.quarter,r.is_annual,r.report_type {base} AND NOT ({same_key}) ORDER BY 1,2,3").fetchall()
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
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "cf_rows_to_resync": len(cf_ids),
           "cf_field_mismatch_same_key_before": before_same, "cf_field_mismatch_other_key_hold": before_other, "fin_cells_to_restore": len(fin)}
    if a.apply:
        run_id = f"canonical_resync_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for sc, y, q, ia, rt, c, v in fin:
            conn.execute("UPDATE canonical_financial_data SET " + c + "=?, updated_at=? WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                         (v, now, sc, y, q, ia, rt))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        if fin:
            conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                         (now, "canonical_financial_data", "sparse 동기화로 비워진 주당·자본금 칸 복원(백업의 마지막 값)", len(fin),
                          "외부 검토 P1 — null-preserving 정본 동기화", "NULL", "백업 값", "scripts/review/resync_canonical_20261013.py", run_id))
        conn.commit()
        sync = _ofs.canonical_sync(conn, run_id, now, set(), set(cf_ids))
        res["canonical_sync_cf"] = sync
        after_same, after_other = _count()
        res["cf_field_mismatch_same_key_after"], res["cf_field_mismatch_other_key_after"] = after_same, after_other
        with open(HOLD_FILE, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow("canon_code canon_year canon_q canon_is_annual canon_fs source_row_id ledger_year ledger_q ledger_is_annual ledger_fs reason_class".split())
            for x in hold:
                x = tuple(x)
                cls = "ANNUAL_Q4_CANONICAL" if x[3] and x[2] == 4 else "FISCAL_REKEY_YEAR_MISMATCH" if x[1] != x[6] else "SOURCE_ROW_KEY_MISMATCH"
                w.writerow(x + (cls,))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "canonical_cashflow_data", f"정본 현금흐름 재동기화(원장 대표행 기준) — 보류 {before_other}건: {HOLD_FILE.name}", len(cf_ids),
                      "외부 검토 P1 — 현금흐름 칸 NULL·불일치 재동기화", f"칸 불일치(같은 키) {before_same}, 다른 키 {before_other}",
                      f"칸 불일치(같은 키) {after_same}, 다른 키 {after_other}, readback 불일치 {sync['readback_mismatch']}, 게이트 거부 {sync['cf_refused']}",
                      "scripts/review/resync_canonical_20261013.py", run_id))
        conn.commit()
        res["run_id"] = run_id
        (OUT / f"{run_id}.json").write_text(json.dumps(res, ensure_ascii=False, default=str, indent=1))
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
