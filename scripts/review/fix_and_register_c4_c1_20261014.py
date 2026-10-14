#!/usr/bin/env python3
"""잔여 C4/C1 처리:
① 032080 2018 Q3 OFS revenue 단위오류 수정 (1.3596e16 → 13596354708)
② 025530 2020 Q2 OFS revenue 예외 등록 (OFS Q2 원문 없음)
③ 069330 2019 Q1 CFS revenue/op/ni 예외 등록 (CFS Q1 원문 없음)
④ 088910 2020 Q0 감가상각 확인 후 예외/수정

2026-10-14
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

from db_compat import connect_primary_db
import apply_ofs_extra_20261009 as _ofs

DRY_RUN = "--apply" not in sys.argv
APPROVED_BASIS = "10-14 잔여 C4/C1 처리 — 원문 확인 후 수정 또는 예외 등록"


def main():
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"fix_and_register_c4c1_{datetime.now():%Y%m%d_%H%M%S}"

    # ── ① 032080 2018 Q3 OFS revenue 단위오류 수정 ──────────────────────────
    r032 = conn.execute(
        "SELECT id, revenue FROM financial_data WHERE stock_code='032080' AND year=2018 AND quarter=3 AND report_type='OFS'"
    ).fetchone()
    if r032:
        old_rev, new_rev = r032[1], 13_596_354_708.0
        ratio = abs(r032[1]) / abs(new_rev) if new_rev else 0
        print(f"032080 2018 Q3 OFS revenue: {r032[1]:.0f} → {new_rev:.0f}  ratio={ratio:.0f}")
    else:
        print("032080 2018 Q3 OFS: DB 행 없음")
        r032 = None

    # ── 예외 등록 대상 ────────────────────────────────────────────────────────
    exceptions = [
        # 025530 2020 Q2 OFS revenue (OFS Q2 원문 없음)
        ("025530", 2020, 2, "OFS", "revenue",
         "NO_OFS_QUARTERLY_RAW", "OFS Q2 원문 미제출; CFS Q2만 존재 → OFS Q2 revenue 검증 불가"),
        # 069330 2019 Q1 CFS (CFS Q1 원문 없음)
        ("069330", 2019, 1, "CFS", "revenue",
         "NO_CFS_QUARTERLY_RAW", "CFS Q1 분기보고서 미제출; OFS Q1만 존재, effective_fs=CFS → Q1 CFS raw 검증 불가"),
        ("069330", 2019, 1, "CFS", "operating_profit",
         "NO_CFS_QUARTERLY_RAW", "CFS Q1 분기보고서 미제출; OFS Q1만 존재"),
        ("069330", 2019, 1, "CFS", "net_income",
         "NO_CFS_QUARTERLY_RAW", "CFS Q1 분기보고서 미제출; OFS Q1만 존재"),
    ]

    # 기존 예외 중복 확인
    existing = set(
        (r[0], r[1], r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT stock_code, year, quarter, report_type, field FROM financial_field_exception"
        ).fetchall()
    )

    new_exc = [(e) for e in exceptions if (e[0], e[1], e[2], e[3], e[4]) not in existing]
    print(f"\n예외 등록 예정: {len(new_exc)}/{len(exceptions)}건")
    for e in new_exc:
        print(f"  {e[0]} {e[1]} Q{e[2]} {e[3]} {e[4]} → {e[5]}")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음.")
        return

    # ── ① 수정 ───────────────────────────────────────────────────────────────
    fd_ids: set[int] = set()
    if r032 and abs(r032[1]) > abs(new_rev) * 100:
        # 백업
        conn.execute(
            "CREATE TABLE IF NOT EXISTS financial_data_backup_c4_fix_20261014 AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false"
        )
        conn.execute(
            "INSERT INTO financial_data_backup_c4_fix_20261014 SELECT *, %s FROM financial_data WHERE id=%s",
            (run_id, r032[0])
        )
        conn.execute(
            "UPDATE financial_data SET revenue=%s, updated_at=%s WHERE id=%s",
            (new_rev, now, r032[0])
        )
        conn.execute(
            "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, "financial_data", "032080 2018 Q3 OFS revenue 단위오류",
             1, "DB OFS revenue 1.36e16 → OFS raw 13596354708 (×1e6 단위오류 수정)",
             f"{r032[1]}", f"{new_rev}",
             "scripts/review/fix_and_register_c4c1_20261014.py", run_id)
        )
        fd_ids.add(r032[0])
        print(f"\n[수정] 032080 2018 Q3 OFS revenue: {r032[1]:.0f} → {new_rev:.0f}")

    # canonical sync
    if fd_ids:
        sync = _ofs.canonical_sync(conn, run_id, now, fd_ids, set())
        print(f"canonical_sync: {sync.get('fd_synced')}건 동기화, {sync.get('fd_refused')}건 거부")

    # ── ② 예외 등록 ───────────────────────────────────────────────────────────
    inserted = 0
    for code, year, q, fs, field, rc, excl in new_exc:
        conn.execute(
            """INSERT INTO financial_field_exception
               (stock_code, year, quarter, report_type, field, reason_code, exclusion_reason, approved_basis, run_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (code, year, q, fs, field, rc, excl, APPROVED_BASIS, run_id, now)
        )
        inserted += 1

    conn.execute(
        "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (now, "financial_field_exception", "잔여 C4/C1 예외 등록",
         inserted, "NO_OFS_QUARTERLY_RAW/NO_CFS_QUARTERLY_RAW",
         f"기존 {len(existing)}건", f"등록 {inserted}건",
         "scripts/review/fix_and_register_c4c1_20261014.py", run_id)
    )
    conn.commit()
    print(f"\n[APPLIED] 수정 {len(fd_ids)}건, 예외 등록 {inserted}건, run_id={run_id}")


if __name__ == "__main__":
    main()
