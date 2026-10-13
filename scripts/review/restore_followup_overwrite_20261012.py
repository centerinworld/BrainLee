#!/usr/bin/env python3
"""00:05 스케줄러 '데이터무결성후속검증'(scripts/data_integrity_followup.py)이 덮어쓴 연간 매출을 직전 확정값으로 복원 (2026-10-12). 기본 dry-run.

원인: 그 작업이 연간 매출 전년 대비 10배+ 칸을 FnGuide 대조 없이 DART 원문값(외화 종목은 원화 환산 전 원통화)으로 교체했고 fix_log를 남기지 않았다.
  같은 날 그 작업은 보고 전용으로 바꿨다(원칙 0).
복원 값: 그 행의 financial_fix_log 마지막 new_value(= 사용자 승인 FnGuide 대체·외화 환산 등 정본 절차로 확정된 값). 출처 '+restore_followup_20261012'.
대상: data_source에 'followup_repair'가 있고 updated_at이 --date 00:00~00:10 인 행 중 현재 값 ≠ 마지막 fix_log 값.
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
TAG = "+restore_followup_20261012"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--date", default="2026-10-12")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=300, readonly=not a.apply)
    rows = conn.execute("""SELECT id, stock_code, year, quarter, is_annual, report_type, revenue FROM financial_data
        WHERE data_source LIKE '%followup_repair%' AND updated_at::text >= ? AND updated_at::text < ?""",
                        (f"{a.date} 00:00", f"{a.date} 00:10")).fetchall()
    plan = []
    for rid, code, y, q, ia, fs, cur in rows:
        last = conn.execute("SELECT new_value, run_id FROM financial_fix_log WHERE row_id=? AND field_name='revenue' ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        if last and last[0] is not None and (cur is None or abs(cur - last[0]) > 0.5):
            plan.append((rid, code, y, q, ia, fs, cur, float(last[0]), last[1]))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "candidates": len(rows), "plan": len(plan),
           "rows": [(p[1], p[2], p[3], p[5], p[6], p[7], p[8]) for p in plan]}
    if a.apply and plan:
        run_id = f"restore_followup_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "financial_data_backup_restore_followup_20261012"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
        ids = [p[0] for p in plan]
        conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
        for rid, code, y, q, ia, fs, cur, new, src in plan:
            conn.execute("UPDATE financial_data SET revenue=?, updated_at=?, data_source=COALESCE(data_source,'')||? WHERE id=?", (new, now, TAG, rid))
        conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
        conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if ia else 0, fs, "revenue", cur, new,
                           f"00:05 데이터무결성후속검증의 무단 덮어쓰기 복원 → 직전 확정값({src})", "financial_fix_log 직전 확정값", run_id)
                          for rid, code, y, q, ia, fs, cur, new, src in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data", "00:05 후속검증 작업이 덮어쓴 연간 매출 복원", len(plan), "직전 확정값(fix_log) 복원, 원칙 0",
                      json.dumps(res["rows"], ensure_ascii=False, default=str)[:4000], "", "scripts/review/restore_followup_overwrite_20261012.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute("SELECT revenue FROM financial_data WHERE id=?", (p[0],)).fetchone()[0]) - p[7]) > 0.5]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, set(ids), set()).items() if "sample" not in k}})
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
