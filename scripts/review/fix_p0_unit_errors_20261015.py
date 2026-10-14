#!/usr/bin/env python3
"""P0 단위오류 6행 수정 + canonical 동기화 + 102260 OFS 예외 등록.

수정 대상:
① 032080 2018 Q3 OFS (id=167328): op 1.684e15→1,683,830,850 / ni 1.717e15→1,716,915,608  (raw IS 확인)
② 032080 2018 Q4 OFS (id=669032): op/ni 파생재계산 (Annual_raw - Q1 - Q2 - Q3_corrected)
③ 032680 2022 Q0 OFS (id=132557): rev/op/ni ÷1e6  (DART CIS 원문 자체 백만원 단위 오기재)
④ 032680 2022 Q4 OFS (id=669402): rev/op/ni 파생재계산 (Annual_corrected - Q1 - Q2 - Q3)
⑤ 102260 2016 Q2 CFS (id=65987): rev/op/ni ÷1e6  (raw CIS 확인: 213,371,655,426 / 21,557,470,653 / 18,047,546,402)
⑥ 102260 2016 Q2 OFS (id=188978): 예외 등록 (NO_OFS_QUARTERLY_RAW — raw 파일 없음)

canonical_financial_data 동기화:
- 032080 Q3/Q4 OFS: canonical 행 없음 → INSERT (P1a 이슈 해결)
- 032680 Q0/Q4 OFS, 102260 Q2 CFS: canonical 행 존재 → UPDATE

2026-10-15
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

from db_compat import connect_primary_db

DRY_RUN = "--apply" not in sys.argv
APPROVED_BASIS = "10-15 P0 단위오류 수정 — 외부검토 후 DART raw IS/CIS 원문 직접 확인"
RUN_ID = f"fix_p0_unit_errors_{datetime.now():%Y%m%d_%H%M%S}"


# ── 수정값 (raw 확인 완료) ─────────────────────────────────────────────────────

# ① 032080 2018 Q3 OFS
FD_032080_Q3 = {
    "id": 167328,
    "operating_profit": 1_683_830_850.0,
    "net_income": 1_716_915_608.0,
}

# ② 032080 2018 Q4 OFS — Annual_raw - Q1 - Q2 - Q3_corrected
_A_op = 2_046_477_455.0   # annual raw IS
_A_ni = 3_925_313_316.0
_Q1_op = 640_447_802.0;  _Q1_ni = 1_478_220_375.0
_Q2_op = -507_373_928.0; _Q2_ni = -121_584_334.0
_Q3_op = 1_683_830_850.0; _Q3_ni = 1_716_915_608.0   # corrected
FD_032080_Q4 = {
    "id": 669032,
    "operating_profit": _A_op - _Q1_op - _Q2_op - _Q3_op,   # 229,572,731
    "net_income":       _A_ni - _Q1_ni - _Q2_ni - _Q3_ni,   # 851,761,667
}

# ③ 032680 2022 Q0 OFS (÷1e6)
FD_032680_Q0 = {
    "id": 132557,
    "revenue":           32_431_035_679.0,
    "operating_profit":  12_630_548_130.0,
    "net_income":        10_395_270_779.0,
}

# ④ 032680 2022 Q4 OFS — Annual_corrected - Q1 - Q2 - Q3
_B_rev = 32_431_035_679.0; _B_op = 12_630_548_130.0; _B_ni = 10_395_270_779.0
_BQ1_rev = 1_459_636_869.0;  _BQ1_op = -712_756_410.0;  _BQ1_ni = -1_135_610_237.0
_BQ2_rev = 6_909_568_122.0;  _BQ2_op =  4_226_249_530.0; _BQ2_ni =  5_530_376_868.0
_BQ3_rev = 8_688_425_732.0;  _BQ3_op = -1_197_612_837.0; _BQ3_ni = -2_146_601_793.0
FD_032680_Q4 = {
    "id": 669402,
    "revenue":           _B_rev - _BQ1_rev - _BQ2_rev - _BQ3_rev,  # 15,373,404,956
    "operating_profit":  _B_op  - _BQ1_op  - _BQ2_op  - _BQ3_op,  # 10,314,667,847
    "net_income":        _B_ni  - _BQ1_ni  - _BQ2_ni  - _BQ3_ni,  # 8,147,105,941
}

# ⑤ 102260 2016 Q2 CFS (raw CIS 값)
FD_102260_Q2 = {
    "id": 65987,
    "revenue":           213_371_655_426.0,
    "operating_profit":   21_557_470_653.0,
    "net_income":         18_047_546_402.0,
}

FD_FIXES = [FD_032080_Q3, FD_032080_Q4, FD_032680_Q0, FD_032680_Q4, FD_102260_Q2]

# ── canonical 직접 업데이트 매핑 (id: canonical_id or None=INSERT) ────────────
CANONICAL_MAP = {
    167328: None,    # 032080 Q3 OFS → INSERT
    669032: None,    # 032080 Q4 OFS → INSERT
    132557: 442213,  # 032680 Q0 OFS → UPDATE
    669402: 407978,  # 032680 Q4 OFS → UPDATE
    65987:  580358,  # 102260 Q2 CFS → UPDATE
}

# ⑥ 102260 2016 Q2 OFS 예외 등록
OFS_EXCEPTIONS = [
    ("102260", 2016, 2, "OFS", "revenue",          "NO_OFS_QUARTERLY_RAW", "OFS Q2 raw 파일 미존재; CFS만 제출됨"),
    ("102260", 2016, 2, "OFS", "operating_profit", "NO_OFS_QUARTERLY_RAW", "OFS Q2 raw 파일 미존재"),
    ("102260", 2016, 2, "OFS", "net_income",        "NO_OFS_QUARTERLY_RAW", "OFS Q2 raw 파일 미존재"),
]


def main() -> None:
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)
    now = datetime.now().isoformat(timespec="seconds")

    # 현재 DB 값 읽기 (dryrun에서도)
    print(f"=== 수정 예정 ({RUN_ID}) ===")
    for fix in FD_FIXES:
        fd_id = fix["id"]
        row = conn.execute(
            "SELECT stock_code, year, quarter, is_annual, report_type, "
            "revenue, operating_profit, net_income FROM financial_data WHERE id=%s",
            (fd_id,)
        ).fetchone()
        if not row:
            print(f"  id={fd_id}: DB 행 없음 (skip)")
            continue
        code, yr, q, ia, rt = row[0], row[1], row[2], row[3], row[4]
        print(f"\n  id={fd_id} {code} {yr} Q{q} {'연간' if ia else ''} {rt}")
        for col in ("revenue", "operating_profit", "net_income"):
            if col not in fix:
                continue
            old = row[5 + ("revenue", "operating_profit", "net_income").index(col)]
            new = fix[col]
            ratio = abs(old / new) if new and old else 0
            old_s = f"{old:.0f}" if old is not None else "None"
            print(f"    {col}: {old_s}  →  {new:.0f}  (ratio={ratio:.0f}x)")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음. 실행: python3 fix_p0_unit_errors_20261015.py --apply")
        return

    # 백업 테이블
    bk_fd = "financial_data_backup_p0_fix_20261015"
    bk_canon = "canonical_financial_data_backup_p0_fix_20261015"
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {bk_fd} AS "
        "SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {bk_canon} AS "
        "SELECT *, CAST(NULL AS TEXT) run_id FROM canonical_financial_data WHERE false"
    )

    fixed_ids: list[int] = []

    for fix in FD_FIXES:
        fd_id = fix["id"]
        row = conn.execute(
            "SELECT id, stock_code, year, quarter, is_annual, report_type, "
            "revenue, operating_profit, net_income FROM financial_data WHERE id=%s",
            (fd_id,)
        ).fetchone()
        if not row:
            print(f"  id={fd_id}: skip (없음)")
            continue

        # 백업
        conn.execute(
            f"INSERT INTO {bk_fd} SELECT *, %s FROM financial_data WHERE id=%s",
            (RUN_ID, fd_id)
        )

        # UPDATE
        sets = ", ".join(f"{c}=%s" for c in fix if c != "id")
        vals = [fix[c] for c in fix if c != "id"] + [now, fd_id]
        conn.execute(f"UPDATE financial_data SET {sets}, updated_at=%s WHERE id=%s", vals)

        # data_fix_log
        old_rev, old_op, old_ni = row[6], row[7], row[8]
        new_str = str({c: fix[c] for c in fix if c != "id"})
        conn.execute(
            "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,"
            "old_value_summary,new_value_summary,source,run_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, "financial_data",
             f"{row[1]} {row[2]} Q{row[3]} {'연간' if row[4] else ''} {row[5]}",
             1, "P0 단위오류 수정: DART raw IS/CIS 직접 확인",
             str({"revenue": old_rev, "operating_profit": old_op, "net_income": old_ni}),
             new_str, "scripts/review/fix_p0_unit_errors_20261015.py", RUN_ID)
        )
        fixed_ids.append(fd_id)
        print(f"  [수정] id={fd_id} {row[1]} {row[2]} Q{row[3]} {row[5]}")

    # ── canonical 동기화 ──────────────────────────────────────────────────────
    print(f"\n=== canonical 동기화 ({len(fixed_ids)}행) ===")
    for fd_id in fixed_ids:
        canon_id = CANONICAL_MAP.get(fd_id)
        fd_row = conn.execute(
            "SELECT stock_code, year, quarter, is_annual, report_type, "
            "revenue, operating_profit, net_income, data_source FROM financial_data WHERE id=%s",
            (fd_id,)
        ).fetchone()
        if not fd_row:
            continue
        code, yr, q, ia, rt, rev, op, ni, src = (
            fd_row[0], fd_row[1], fd_row[2], fd_row[3], fd_row[4],
            fd_row[5], fd_row[6], fd_row[7], fd_row[8]
        )
        canon_q = 0 if ia else q

        if canon_id is not None:
            # 백업 + UPDATE
            conn.execute(
                f"INSERT INTO {bk_canon} SELECT *, %s FROM canonical_financial_data WHERE id=%s",
                (RUN_ID, canon_id)
            )
            conn.execute(
                "UPDATE canonical_financial_data SET revenue=%s, operating_profit=%s, net_income=%s,"
                " source_row_id=%s, decision_reason=%s, updated_at=%s WHERE id=%s",
                (rev, op, ni, fd_id, RUN_ID, now, canon_id)
            )
            print(f"  [canon UPDATE] id={canon_id} {code} {yr} Q{canon_q} {rt}")
        else:
            # INSERT (P1a: canonical 행 없음)
            conn.execute(
                """INSERT INTO canonical_financial_data
                   (stock_code, year, quarter, is_annual, report_type,
                    revenue, operating_profit, net_income,
                    data_source, source_row_id, rule_version, decision_reason, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (stock_code,year,quarter,is_annual,report_type) DO UPDATE
                   SET revenue=EXCLUDED.revenue, operating_profit=EXCLUDED.operating_profit,
                       net_income=EXCLUDED.net_income, source_row_id=EXCLUDED.source_row_id,
                       decision_reason=EXCLUDED.decision_reason, updated_at=EXCLUDED.updated_at""",
                (code, yr, canon_q, 1 if ia else 0, rt,
                 rev, op, ni, src, fd_id, "p0_fix_20261015", RUN_ID, now)
            )
            print(f"  [canon INSERT] {code} {yr} Q{canon_q} {rt}")

    # ── ⑥ OFS 예외 등록 ──────────────────────────────────────────────────────
    existing = {(r[0], r[1], r[2], r[3], r[4]) for r in conn.execute(
        "SELECT stock_code, year, quarter, report_type, field FROM financial_field_exception"
    ).fetchall()}
    exc_inserted = 0
    for code, yr, q, rt, fld, rc, excl in OFS_EXCEPTIONS:
        key = (code, yr, q, rt, fld)
        if key in existing:
            print(f"  [예외 중복] {key}")
            continue
        conn.execute(
            """INSERT INTO financial_field_exception
               (stock_code, year, quarter, report_type, field, reason_code,
                exclusion_reason, approved_basis, run_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (code, yr, q, rt, fld, rc, excl, APPROVED_BASIS, RUN_ID, now)
        )
        exc_inserted += 1

    conn.commit()

    # readback
    print("\n=== readback ===")
    fail = 0
    for fix in FD_FIXES:
        fd_id = fix["id"]
        r = conn.execute(
            "SELECT revenue, operating_profit, net_income FROM financial_data WHERE id=%s",
            (fd_id,)
        ).fetchone()
        ok = True
        for col in ("revenue", "operating_profit", "net_income"):
            if col not in fix:
                continue
            idx = ("revenue", "operating_profit", "net_income").index(col)
            if r is None or abs(float(r[idx]) - fix[col]) > 1:
                print(f"  FAIL id={fd_id} {col}: got={r[idx] if r else None}, want={fix[col]}")
                fail += 1
                ok = False
        if ok:
            print(f"  PASS id={fd_id}")

    print(f"\n[완료] 수정 {len(fixed_ids)}행, 예외 등록 {exc_inserted}건, readback_fail={fail}, run_id={RUN_ID}")


if __name__ == "__main__":
    main()
