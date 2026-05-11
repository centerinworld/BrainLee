"""
dedup_financial_data.py — financial_data 중복 연간 행 제거

문제:
  - (stock_code, year) 쌍에 여러 행 존재 (587쌍, 587개 여분 행)
  - 원인1: 완전 동일 중복 (무림SP 등) → 낮은 ID 보존
  - 원인2: 연결(consolidated) vs 별도(standalone) 혼재 (한화, 삼성전자 등)
           → 높은 revenue 행 = 연결 데이터로 판단, 보존

처리:
  1. 백업 테이블 생성
  2. 중복 그룹별: revenue 최대 행 보존, 나머지 삭제
     (revenue NULL인 경우 operating_profit, 그것도 NULL이면 id 최소 보존)
  3. 결과 검증

실행:
    python3 scripts/dedup_financial_data.py --dry-run    # 미실행 시뮬
    python3 scripts/dedup_financial_data.py              # 실제 적용
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def step_1_backup(conn) -> str:
    today = date.today().strftime("%Y%m%d")
    name = f"financial_data_dedup_backup_{today}"
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {name}")
    cur.execute(f"CREATE TABLE {name} AS SELECT * FROM financial_data WHERE is_annual=1")
    conn.commit()
    n = cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    logger.info(f"[1단계] 연간 데이터 백업: {name} ({n:,}행)")
    return name


def step_2_dedup(conn, dry_run: bool) -> dict:
    cur = conn.cursor()

    # 중복 그룹 조회: (stock_code, year)별 여러 행
    dups = cur.execute("""
        SELECT stock_code, year, COUNT(*) cnt
        FROM financial_data
        WHERE is_annual = 1
        GROUP BY stock_code, year
        HAVING COUNT(*) > 1
    """).fetchall()

    total_deleted = 0
    exact_dup_deleted = 0
    consol_selected = 0
    detail: list[dict] = []

    for code, year, cnt in dups:
        rows = cur.execute("""
            SELECT id, revenue, operating_profit, net_income, eps
            FROM financial_data
            WHERE stock_code=? AND year=? AND is_annual=1
            ORDER BY COALESCE(ABS(revenue), 0) DESC, id ASC
        """, (code, year)).fetchall()

        if not rows:
            continue

        # 보존할 행: revenue 최대 (= 연결 우선). revenue 동일이면 id 최소
        keep_id = rows[0][0]  # revenue DESC 정렬 → 첫 행 = 최대 revenue
        delete_ids = [r[0] for r in rows[1:]]

        # 완전 동일 여부 확인
        values_set = set((r[1], r[2], r[3]) for r in rows)
        is_exact = len(values_set) == 1

        if is_exact:
            exact_dup_deleted += len(delete_ids)
        else:
            consol_selected += 1
            if len(detail) < 30:
                detail.append({
                    "code": code, "year": year,
                    "kept": {"id": rows[0][0], "rev": rows[0][1]},
                    "deleted": [{"id": r[0], "rev": r[1]} for r in rows[1:]],
                })

        total_deleted += len(delete_ids)

        if not dry_run:
            cur.executemany(
                "DELETE FROM financial_data WHERE id=?",
                [(did,) for did in delete_ids]
            )

    if not dry_run:
        conn.commit()

    return {
        "total_dup_groups": len(dups),
        "total_deleted": total_deleted,
        "exact_dup_deleted": exact_dup_deleted,
        "consol_vs_standalone_fixed": consol_selected,
        "dry_run": dry_run,
        "samples": detail[:10],
    }


def step_3_verify(conn) -> dict:
    cur = conn.cursor()
    remaining_dups = cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT stock_code, year FROM financial_data WHERE is_annual=1
            GROUP BY stock_code, year HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM financial_data WHERE is_annual=1").fetchone()[0]
    return {"remaining_duplicates": remaining_dups, "total_annual_rows": total}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    conn = _conn()

    if not args.dry_run:
        step_1_backup(conn)

    logger.info(f"[2단계] 중복 제거 {'(DRY-RUN)' if args.dry_run else ''}")
    res = step_2_dedup(conn, args.dry_run)
    logger.info(f"  중복 그룹: {res['total_dup_groups']:,}")
    logger.info(f"  삭제 행: {res['total_deleted']:,} (완전중복={res['exact_dup_deleted']}, 연결선택={res['consol_vs_standalone_fixed']})")

    for s in res["samples"]:
        kept_rev = s["kept"]["rev"]
        del_revs = [d["rev"] for d in s["deleted"]]
        del_summary = [(d["id"], round(r/1e8) if r else None) for d, r in zip(s["deleted"], del_revs)]
        kept_억 = round(kept_rev/1e8) if kept_rev else None
        logger.info(f"  {s['code']} {s['year']}: keep={s['kept']['id']}({kept_억}억) del={del_summary}")

    if not args.dry_run:
        v = step_3_verify(conn)
        logger.info(f"[3단계] 검증: 잔여중복={v['remaining_duplicates']}, 연간총행={v['total_annual_rows']:,}")

    conn.close()


if __name__ == "__main__":
    main()
