#!/usr/bin/env python3
"""fix_c2_ofs_q4_20261014로 수정된 86개 financial_data 행을 canonical_financial_data에 동기화.
절차: 백업 테이블 생성 → canonical_sync → readback → 결과 JSON 저장.
2026-10-14 (외부 검토 P1 대응)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

from db_compat import connect_primary_db

# canonical_sync 및 게이트 함수 임포트
import apply_ofs_extra_20261009 as _ofs

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
DRY_RUN = "--apply" not in sys.argv


def main():
    conn = connect_primary_db(timeout=600)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"sync_c2_ofs_q4_{datetime.now():%Y%m%d_%H%M%S}"

    # fix_c2_ofs_q4_20261014로 수정된 86개 키 → financial_data id 조회
    fix_scopes = conn.execute(
        "SELECT scope FROM data_fix_log WHERE source='fix_c2_ofs_q4_20261014' ORDER BY fixed_at"
    ).fetchall()
    if not fix_scopes:
        print("fix_c2_ofs_q4_20261014 로그 없음. 중단.")
        return

    fd_ids = set()
    for (scope,) in fix_scopes:
        parts = scope.split("/")
        if len(parts) != 4:
            continue
        code, y = parts[0], int(parts[1])
        row = conn.execute(
            "SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=false AND report_type='CFS'",
            (code, y)
        ).fetchone()
        if row:
            fd_ids.add(row[0])

    print(f"대상 financial_data 행: {len(fd_ids)}개  (fix 로그: {len(fix_scopes)}개)", flush=True)

    if DRY_RUN:
        # dry-run: canonical 현황만 확인
        missing, mismatched, ok_count = 0, 0, 0
        for fid in sorted(fd_ids):
            fd = conn.execute(
                "SELECT stock_code, year, quarter, is_annual, report_type, total_assets, total_liabilities, total_equity FROM financial_data WHERE id=?",
                (fid,)
            ).fetchone()
            if not fd:
                continue
            sc, y, q, ia, rt = fd[:5]
            cq = 0 if ia else q
            can = conn.execute(
                "SELECT total_assets, total_liabilities, total_equity FROM canonical_financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                (sc, y, cq, 1 if ia else 0, rt)
            ).fetchone()
            if can is None:
                missing += 1
            elif any((c or 0) != (d or 0) for c, d in zip(can, fd[5:])):
                mismatched += 1
            else:
                ok_count += 1
        print(f"[DRY-RUN] canonical 없음: {missing}, 값 다름: {mismatched}, 동일: {ok_count}")
        print("--apply 없이 실행 중. 적용하려면: python3 sync_canonical_c2_ofs_q4_20261014.py --apply")
        return

    # 백업 테이블 생성 (canonical_financial_data)
    bk = "financial_data_backup_fix_c2_ofs_q4_20261014"
    conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT * FROM financial_data WHERE false")
    # 수정된 86개 행 백업
    backed = 0
    for fid in sorted(fd_ids):
        exists = conn.execute(f"SELECT 1 FROM {bk} WHERE id=?", (fid,)).fetchone()
        if not exists:
            conn.execute(f"INSERT INTO {bk} SELECT * FROM financial_data WHERE id=?", (fid,))
            backed += 1
    conn.execute("COMMIT")
    print(f"백업 완료: {backed}행 → {bk}", flush=True)

    # canonical_sync 실행
    print("canonical_sync 실행 중...", flush=True)
    result = _ofs.canonical_sync(conn, run_id, now, fd_ids, set())
    print(f"결과: {result}", flush=True)

    # 결과 저장
    out_file = OUT / f"sync_c2_ofs_q4_{datetime.now():%Y%m%d_%H%M%S}.json"
    json.dump({"run_id": run_id, "fd_ids_count": len(fd_ids), "backup_table": bk,
               "backed_rows": backed, "result": result, "at": now},
              open(out_file, "w"), ensure_ascii=False, indent=2)
    print(f"결과 저장: {out_file}", flush=True)

    if result.get("readback_mismatch", 0) > 0:
        print(f"⚠ readback mismatch {result['readback_mismatch']}건: {result.get('mismatch_sample', [])[:3]}")
    else:
        print("readback pass")

    print(f"\n동기화: {result.get('fd_synced', 0)}건 완료, {result.get('fd_refused', 0)}건 게이트 거부")
    if result.get("refused_sample"):
        print("거부 샘플:")
        for s in result["refused_sample"][:5]:
            print(f"  {s}")


if __name__ == "__main__":
    main()
