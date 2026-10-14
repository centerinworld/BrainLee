#!/usr/bin/env python3
"""canonical_cashflow_data orphan 행 정리 (source_row_id → cash_flow_data 없음).
- 23건 재연결 가능 (same-key ledger 존재)
- 5건 ledger 없음 → 백업 후 삭제
2026-10-14 (외부 검토 P2-2 대응)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
DRY_RUN = "--apply" not in sys.argv
BK = "canonical_cashflow_data_backup_orphan_fix_20261014"


def main():
    conn = connect_primary_db(timeout=600, readonly=DRY_RUN)

    # 1. orphan 전체 조회
    orphans = conn.execute("""
        SELECT c.id, c.stock_code, c.year, c.report_type, c.quarter,
               c.is_annual::int AS ia_int, c.source_row_id
        FROM canonical_cashflow_data c
        LEFT JOIN cash_flow_data d ON c.source_row_id = d.id
        WHERE c.source_row_id IS NOT NULL AND d.id IS NULL
        ORDER BY c.stock_code, c.year
    """).fetchall()
    print(f"orphan 총: {len(orphans)}개", flush=True)

    reconnect = []  # (canon_id, code, year, rt, q, ia, old_src, new_src)
    no_ledger = []  # (canon_id, code, year, rt, q, ia, old_src)

    for r in orphans:
        cid, code, year, rt, q, ia_int, src = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        # same-key ledger: is_annual bool 비교 — postgresql에서 boolean != int
        is_annual_bool = (ia_int == 1)
        led = conn.execute(
            "SELECT id FROM cash_flow_data WHERE stock_code=%s AND year=%s"
            " AND report_type=%s AND is_annual=%s ORDER BY id LIMIT 1",
            (code, year, rt, is_annual_bool)
        ).fetchone()
        if led:
            reconnect.append((cid, code, year, rt, q, ia_int, src, led[0]))
        else:
            no_ledger.append((cid, code, year, rt, q, ia_int, src))

    print(f"  재연결 가능: {len(reconnect)}개")
    for r in reconnect:
        print(f"    [{r[0]}] {r[1]} {r[2]} {r[3]} q={r[4]} ia={r[5]}  old_src={r[6]} -> new_src={r[7]}")
    print(f"  ledger 없음: {len(no_ledger)}개")
    for r in no_ledger:
        print(f"    [{r[0]}] {r[1]} {r[2]} {r[3]} q={r[4]} ia={r[5]}  old_src={r[6]}")

    if DRY_RUN:
        print("\n[DRY-RUN] --apply 없음. 적용하려면: python3 fix_canonical_cf_orphans_20261014.py --apply")
        return

    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"fix_canonical_cf_orphans_{datetime.now():%Y%m%d_%H%M%S}"

    # 백업 테이블
    conn.execute(f"CREATE TABLE IF NOT EXISTS {BK} AS SELECT *, CAST(NULL AS TEXT) run_id FROM canonical_cashflow_data WHERE false")

    # 2. 재연결 가능한 23건: source_row_id만 새 ledger id로 업데이트
    reconnected = 0
    for cid, code, year, rt, q, ia_int, old_src, new_src in reconnect:
        conn.execute(f"INSERT INTO {BK} SELECT *, %s FROM canonical_cashflow_data WHERE id=%s", (run_id, cid))
        conn.execute(
            "UPDATE canonical_cashflow_data SET source_row_id=%s, updated_at=%s WHERE id=%s",
            (new_src, now, cid)
        )
        reconnected += 1

    # 3. ledger 없는 5건: 백업 후 삭제
    deleted = 0
    del_ids = []
    for cid, code, year, rt, q, ia_int, src in no_ledger:
        conn.execute(f"INSERT INTO {BK} SELECT *, %s FROM canonical_cashflow_data WHERE id=%s", (run_id, cid))
        del_ids.append(cid)

    if del_ids:
        conn.execute(
            f"DELETE FROM canonical_cashflow_data WHERE id = ANY(%s)",
            (del_ids,)
        )
        deleted = len(del_ids)

    # 4. 로그
    conn.execute(
        "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (now, "canonical_cashflow_data", "orphan 행 정리(source_row_id 단절)",
         reconnected + deleted,
         "orphan 28건: 재연결 가능 23건(source_row_id 갱신), ledger 없는 5건(백업 후 삭제)",
         f"orphan {len(orphans)}건",
         f"재연결 {reconnected}건, 삭제 {deleted}건",
         "scripts/review/fix_canonical_cf_orphans_20261014.py", run_id)
    )

    conn.commit()

    # 5. readback: orphan 수 재확인
    after = conn.execute("""
        SELECT count(*) FROM canonical_cashflow_data c
        LEFT JOIN cash_flow_data d ON c.source_row_id = d.id
        WHERE c.source_row_id IS NOT NULL AND d.id IS NULL
    """).fetchone()[0]

    res = {
        "run_id": run_id, "at": now,
        "orphan_before": len(orphans), "reconnected": reconnected, "deleted": deleted,
        "orphan_after": after, "backup_table": BK
    }
    out_file = OUT / f"{run_id}.json"
    out_file.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"\n[APPLIED] 재연결 {reconnected}건, 삭제 {deleted}건")
    print(f"orphan after: {after}건")
    if after > 0:
        print(f"  ⚠ 잔여 orphan {after}건 — 추가 확인 필요")
    else:
        print("  readback pass: orphan 0건")
    print(f"결과: {out_file}")


if __name__ == "__main__":
    main()
