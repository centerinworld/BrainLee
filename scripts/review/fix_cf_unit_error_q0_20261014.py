#!/usr/bin/env python3
"""CF annual(Q0) CFS 단위오류 수정: operating_cf 값이 OFS 대비 1e6 배 작음.
원인: data_quality_null 소스로 채워진 CFS annual operating_cf가 백만원 단위로 저장됨.
대상: 006380/036670/084650 2019-2020 CFS annual (is_annual=true, report_type='CFS').
수정: raw DART CFS 값(없으면 OFS raw 값)으로 교체.
2026-10-14
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

import gzip
from db_compat import connect_primary_db
import fin_norm as fn
import apply_ofs_extra_20261009 as _ofs

OCF_LABELS = {"영업활동순현금흐름", "영업활동으로인한현금흐름", "영업활동으로인한현금흐름합계",
              "영업활동현금흐름"}


def _raw_ocf(code: str, year: int, fs: str) -> float | None:
    """DART raw 파일에서 영업현금흐름 thstrm_amount 반환."""
    by, bq = fn.dart_key_for_fiscal(code, year, 0)
    p = fn.RAW_DART / code / f"{by}_{bq}_{fs}.json.gz"
    if not p.exists():
        return None
    data = json.loads(gzip.decompress(p.read_bytes()))
    for r in data:
        nm = str(r.get("account_nm", "")).replace(" ", "")
        if nm in OCF_LABELS and r.get("sj_div") == "CF":
            v = r.get("thstrm_amount")
            if v not in (None, "", "-"):
                try:
                    return float(str(v).replace(",", ""))
                except (ValueError, TypeError):
                    pass
    return None

DRY_RUN = "--apply" not in sys.argv
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"

TARGETS = [
    ("006380", 2019), ("006380", 2020),
    ("036670", 2019), ("036670", 2020),
    ("084650", 2019), ("084650", 2020),
]
T_RATIO = 500_000  # ratio 50만 초과면 단위오류 확정


def main():
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"fix_cf_unit_error_q0_{datetime.now():%Y%m%d_%H%M%S}"

    plan = []
    for code, year in TARGETS:
        row = conn.execute(
            "SELECT id, operating_cf, data_source FROM cash_flow_data"
            " WHERE stock_code=%s AND year=%s AND is_annual=true AND report_type='CFS'",
            (code, year)
        ).fetchone()
        if not row:
            print(f"{code} {year}: DB CFS 행 없음 (skip)")
            continue

        db_id, db_ocf, src = row[0], row[1], row[2]
        if db_ocf is None:
            print(f"{code} {year}: db_ocf=None (skip)")
            continue

        # raw 값 우선순위: DART CFS raw → DART OFS raw → DB OFS → db_ocf × 1e6
        rc = _raw_ocf(code, year, "CFS")
        ro = _raw_ocf(code, year, "OFS")
        if rc is not None and abs(rc) > abs(db_ocf) * T_RATIO / 2:
            raw_ocf, raw_src = float(rc), "DART_CFS"
        elif ro is not None and abs(ro) > abs(db_ocf) * T_RATIO / 2:
            raw_ocf, raw_src = float(ro), "DART_OFS"
        else:
            ofs_row = conn.execute(
                "SELECT operating_cf FROM cash_flow_data"
                " WHERE stock_code=%s AND year=%s AND is_annual=true AND report_type='OFS'"
                " AND operating_cf IS NOT NULL ORDER BY id LIMIT 1",
                (code, year)
            ).fetchone()
            if ofs_row and ofs_row[0] is not None and abs(ofs_row[0]) > abs(db_ocf) * T_RATIO / 2:
                raw_ocf, raw_src = float(ofs_row[0]), "DB_OFS"
            else:
                raw_ocf, raw_src = db_ocf * 1_000_000, "x1e6"

        if abs(db_ocf) < 1 or abs(raw_ocf / db_ocf) < T_RATIO:
            print(f"{code} {year}: ratio={raw_ocf/db_ocf:.0f} < {T_RATIO} (skip — 단위오류 아님)")
            continue

        plan.append({
            "id": db_id, "code": code, "year": year,
            "old_ocf": db_ocf, "new_ocf": raw_ocf, "raw_src": raw_src,
        })
        print(f"{code} {year}: {db_ocf:.0f}원 → {raw_ocf/1e8:.2f}억원  (raw_src={raw_src})")

    print(f"\n수정 예정: {len(plan)}행")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음. 적용하려면: python3 fix_cf_unit_error_q0_20261014.py --apply")
        return

    # 백업 테이블
    bk = "cash_flow_data_backup_cf_unit_error_q0_20261014"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")

    cf_ids = set()
    for p in plan:
        conn.execute(f"INSERT INTO {bk} SELECT *, %s FROM cash_flow_data WHERE id=%s", (run_id, p["id"]))
        conn.execute(
            "UPDATE cash_flow_data SET operating_cf=%s, updated_at=%s,"
            " data_source=data_source||'+fix_cf_unit_q0_20261014' WHERE id=%s",
            (p["new_ocf"], now, p["id"])
        )
        cf_ids.add(p["id"])

    # cashflow_fix_log
    for p in plan:
        conn.execute(
            "INSERT INTO cashflow_fix_log"
            "(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, p["id"], p["code"], p["year"], 4, 1, "CFS",
             "operating_cf", p["old_ocf"], p["new_ocf"],
             f"CFS annual operating_cf 단위오류 수정 (×1e6 원 단위 오류, data_quality_null → raw {p['raw_src']})",
             "scripts/review/fix_cf_unit_error_q0_20261014.py", run_id)
        )

    # data_fix_log
    conn.execute(
        "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (now, "cash_flow_data", "CFS annual operating_cf 단위오류",
         len(plan),
         "data_quality_null 소스 CFS annual ocf × 1e6 오류 → raw DART/OFS 값으로 교체",
         str({f"{p['code']} {p['year']}": p["old_ocf"] for p in plan}),
         str({f"{p['code']} {p['year']}": p["new_ocf"] for p in plan}),
         "scripts/review/fix_cf_unit_error_q0_20261014.py", run_id)
    )

    # canonical sync
    sync = _ofs.canonical_sync(conn, run_id, now, set(), cf_ids)
    conn.commit()

    # readback
    fail = 0
    for p in plan:
        r = conn.execute("SELECT operating_cf FROM cash_flow_data WHERE id=%s", (p["id"],)).fetchone()
        if not r or abs(r[0] - p["new_ocf"]) > 1:
            print(f"  READBACK FAIL {p['code']} {p['year']}: {r}")
            fail += 1
    if fail == 0:
        print(f"\nreadback pass: {len(plan)}건 모두 통과")

    res = {"run_id": run_id, "fixed": len(plan), "sync_pass": sync.get("pass"),
           "sync_cf_synced": sync.get("cf_synced"), "sync_cf_refused": sync.get("cf_refused")}
    (OUT / f"{run_id}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"결과: {res}")


if __name__ == "__main__":
    main()
