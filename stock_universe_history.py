"""stock_universe 를 종목당 1행으로 유지하고 과거 스냅샷은 stock_universe_history 로 이관한다.

배경(2026-09-24): stock_universe 는 base_date 별 스냅샷을 쌓는 구조라 종목당 2~3행이 존재했다.
그런데 228개 파일이 base_date 없이 `WHERE stock_code=?` / JOIN 으로 조회해 중복 행·임의 행 선택이 생겼다.
→ 본 테이블은 종목별 최신 base_date 1행만 두고, 이전 스냅샷은 history 테이블에 보존한다.

호출: stock_universe.update_from_krx() 가 새 스냅샷을 만든 직후(정적 필드 상속이 끝난 뒤) 자동 실행.
수동: `python3 stock_universe_history.py` (멱등 — 이관할 행이 없으면 아무것도 안 함).
PostgreSQL 전용(운영 DB). SQLite 폴백에서는 아무것도 하지 않는다.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

_SUPERSEDED = """
    SELECT s.id FROM stock_universe s
    WHERE s.base_date < (SELECT MAX(x.base_date) FROM stock_universe x WHERE x.stock_code = s.stock_code)
"""


def archive_superseded_snapshots(run_id: str | None = None) -> dict:
    """종목별 최신 스냅샷을 제외한 행을 stock_universe_history 로 옮기고 본 테이블에서 삭제한다."""
    from config import IS_POSTGRES
    from database import engine

    if not IS_POSTGRES:
        return {"ok": True, "skipped": "not_postgres", "moved": 0}
    run_id = run_id or f"su_history_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    with engine.begin() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM ({_SUPERSEDED}) q")).scalar()
        if not n:
            return {"ok": True, "moved": 0, "run_id": run_id}
        # id 를 그대로 보존하는 이력 테이블(시퀀스/기본값 미복제) + (종목, 기준일) 유일 인덱스
        conn.execute(text("CREATE TABLE IF NOT EXISTS stock_universe_history (LIKE stock_universe)"))
        conn.execute(text("ALTER TABLE stock_universe_history ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP DEFAULT now()"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_stock_universe_history_code_date "
                          "ON stock_universe_history (stock_code, base_date)"))
        cols = [r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='stock_universe' ORDER BY ordinal_position"))]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        conn.execute(text(
            f"INSERT INTO stock_universe_history ({col_sql}) "
            f"SELECT {col_sql} FROM stock_universe WHERE id IN ({_SUPERSEDED}) "
            f"ON CONFLICT (stock_code, base_date) DO NOTHING"))
        # 이력 테이블에 실제로 들어간 행만 삭제(검증 후 삭제)
        deleted = conn.execute(text(
            f"DELETE FROM stock_universe WHERE id IN ({_SUPERSEDED}) "
            f"AND EXISTS (SELECT 1 FROM stock_universe_history h "
            f"WHERE h.stock_code=stock_universe.stock_code AND h.base_date=stock_universe.base_date)")).rowcount
        remaining = conn.execute(text("SELECT COUNT(*) FROM stock_universe")).scalar()
        dup = conn.execute(text("SELECT COUNT(*) FROM (SELECT stock_code FROM stock_universe GROUP BY 1 HAVING COUNT(*)>1) q")).scalar()
        if deleted != n:
            raise RuntimeError(f"이관 검증 실패: 대상 {n} != 삭제 {deleted} (롤백)")
        try:
            conn.execute(text(
                "INSERT INTO data_fix_log (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id) "
                "VALUES ('stock_universe', 'superseded snapshots -> stock_universe_history', :n, "
                "'keep latest base_date per stock_code; move older snapshots to history', :old, :new, 'stock_universe_history.py', :rid)"),
                {"n": int(deleted), "old": f"{remaining + deleted} rows", "new": f"{remaining} rows (dup_codes={dup})", "rid": run_id})
        except Exception as e:  # 로그 실패가 이관을 막지 않도록
            logger.warning(f"[stock_universe_history] data_fix_log 기록 실패: {e}")
    logger.info(f"[stock_universe_history] {deleted}행 이관, 본 테이블 {remaining}행 (중복 종목 {dup})")
    return {"ok": True, "moved": int(deleted), "remaining": int(remaining), "dup_codes": int(dup), "run_id": run_id}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(archive_superseded_snapshots())
