#!/usr/bin/env python3
"""Monthly regeneration of the canonical `strategy_feature_snapshot` (HANDOFF §10 P0-3).

The generator DELETEs its whole target table before writing, so it is pointed at a STAGING table and the canonical table is only
replaced (one transaction) after sanity checks pass. A failed/short build therefore never leaves the canonical table empty.
Options are fixed to the reviewed combination: --adjust-jumps --legit-only --ttm-valuation (point-in-time TTM PER/PBR, real
corporate-action masking only, delisted common stocks included).
Exit code != 0 on any failed check (scheduler `_run_job_safe` records it).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

CANON = "strategy_feature_snapshot"
STAGE = "strategy_feature_snapshot_stage"
MIN_ROW_RATIO = 0.85     # new rows >= 85% of current canonical rows
MIN_PER_FILL = 0.40      # latest snapshot month PER fill rate


def _alert(text: str) -> None:
    try:
        import notifier
        notifier.send(text, key="feature_snapshot_monthly")
    except Exception as exc:  # noqa: BLE001
        print("alert failed:", exc, file=sys.stderr)


def main() -> int:
    from db_compat import connect_primary_db
    from build_strategy_research_dataset import build_strategy_research_dataset

    conn = connect_primary_db()
    conn.execute(f"DROP TABLE IF EXISTS {STAGE}")
    conn.commit()
    build_strategy_research_dataset(snapshot_table=STAGE, adjust_jumps=True, legit_only=True, ttm_valuation=True)

    old_n = conn.execute(f"SELECT count(*) FROM {CANON}").fetchone()[0]
    new_n = conn.execute(f"SELECT count(*) FROM {STAGE}").fetchone()[0]
    fill = conn.execute(
        f"SELECT avg((per IS NOT NULL)::int) FROM {STAGE} WHERE snapshot_date=(SELECT max(snapshot_date) FROM {STAGE})").fetchone()[0]
    print(f"canonical={old_n} stage={new_n} latest_per_fill={fill}")
    if new_n < old_n * MIN_ROW_RATIO or (fill or 0) < MIN_PER_FILL:
        print("점검 실패 — 정본 유지, 스테이징 테이블 보존")
        _alert(f"⚠️ 월간 피처 스냅샷 점검 실패(정본 유지): 신규 {new_n}행/기존 {old_n}행, 최신월 PER 채움 {fill}")
        return 2

    cols = conn.execute(
        f"""SELECT string_agg('"'||a.column_name||'"', ', ' ORDER BY a.ordinal_position)
            FROM information_schema.columns a JOIN information_schema.columns b
              ON b.column_name=a.column_name AND b.table_name='{STAGE}' WHERE a.table_name='{CANON}'""").fetchone()[0]
    try:
        conn.execute(f"DELETE FROM {CANON}")
        conn.execute(f"INSERT INTO {CANON} ({cols}) SELECT {cols} FROM {STAGE}")
        n = conn.execute(f"SELECT count(*) FROM {CANON}").fetchone()[0]
        if n != new_n:
            raise RuntimeError(f"row mismatch {n} != {new_n}")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"교체 롤백: {exc}")
        _alert(f"⚠️ 월간 피처 스냅샷 교체 롤백(정본 유지): {exc}")
        return 3
    conn.execute(f"DROP TABLE IF EXISTS {STAGE}")
    conn.commit()
    print(f"교체 완료 rows={n}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - build failure must alert, canonical table is untouched
        _alert(f"⚠️ 월간 피처 스냅샷 생성 실패(정본 유지): {exc}")
        raise
