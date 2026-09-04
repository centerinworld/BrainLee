"""
financial_source_snapshot.verification_status='unverified' 백로그 정리 (2026-08-28 신설).

cross_validate_annual()이 DART 쿼터 체크(api_limiter.wait) 실패 시 재시도 없이 'unverified'로
끝내버려 매일 신규 수집분의 상당수가 검증 없이 쌓이던 문제(실측 91%, 136,441건)의 근본원인
자체(쿼터체크 실패시 무재시도)는 그대로 두되, 이미 저장된 FnGuide 원본값(financial_source_
snapshot 컬럼에 이미 있음)을 재사용해 DART 쪽만 다시 시도하는 저비용 백필로 기존 백로그를
점진적으로 소진한다. FnGuide 재수집(별도 일일한도) 불필요 — DART 쿼터만 소비.

스케줄러(scheduler.py `_loop_unverified_snapshot_backfill`)가 매일 호출.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.fnguide_financial_collector import _conn, cross_validate_annual, _CROSS_FIELDS
import dart_data_quality as dq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run_backfill(limit: int = 1000, conn=None) -> dict:
    """unverified 스냅샷 중 오래된 것부터 limit개를 재검증. 반환: 처리 통계."""
    own_conn = conn is None
    if own_conn:
        conn = _conn()
    dq.ensure_schema(conn)

    rows = conn.execute(f"""
        SELECT id, stock_code, year, report_type, {', '.join(_CROSS_FIELDS)}
        FROM financial_source_snapshot
        WHERE verification_status='unverified' AND is_annual=1
        ORDER BY fetched_at ASC
        LIMIT ?
    """, (limit,)).fetchall()

    stats = {"verified": 0, "mismatch": 0, "structural_diff": 0, "unverified": 0, "errors": 0}

    for i, row in enumerate(rows, 1):
        snap_id, stock_code, year, report_type = row[0], row[1], row[2], row[3]
        fng_data = dict(zip(_CROSS_FIELDS, row[4:]))
        try:
            status = cross_validate_annual(conn, stock_code, report_type, int(year), fng_data, snap_id)
            stats[status] = stats.get(status, 0) + 1
            # cross_validate_annual()은 DART 원문 자체를 못 가져오면 status='unverified'를
            # 반환한다(재시도 없이) — 이걸 종목별 DART 조회가능여부 추적에 누적한다.
            dq.record_dart_result(conn, stock_code, int(year), ok=(status != "unverified"),
                                   source="backfill_unverified_snapshot")
        except Exception as e:
            stats["errors"] += 1
            log.warning(f"{stock_code} {year} {report_type}: {e}")

        if i % 100 == 0:
            conn.commit()
            log.info(f"[{i}/{len(rows)}] {stats}")

    conn.commit()
    if own_conn:
        conn.close()

    log.info(f"완료: 대상 {len(rows)}건, {stats}")
    return {"target_count": len(rows), "stats": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()
    run_backfill(limit=args.limit)


if __name__ == "__main__":
    main()
