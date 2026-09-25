#!/usr/bin/env python3
"""결함 A 후속 — `^KS11`/`^KQ11` 지수 수급 4컬럼의 **가짜 0.0 → NULL 복원** (승인 게이트 도구).

배경 (2026-09-25 확정, 결함 A)
  원장은 `야간배치`·`장중수급` 모두 success 인데 `^KS11`/`^KQ11` 의
  `inst_net_buy`/`frn_net_buy`/`inst_net_buy_amt`/`frn_net_buy_amt` 가
  2026-09-11~09-23 **9거래일 연속 0.0**(코드 2개 × 9 = 18행)으로 저장됐다.
  원인은 `data_collector.collect_macro_data()` 가 KIS 수급 `None` 을 `0.0` 으로 바꿔 POST 한 것
  → **"수급 없음"과 "실제 0"이 구분 불가**해졌다. writer 코드는 수정됨(재발 방지).
  이 스크립트는 **이미 굳은 18행을 결측(NULL)으로 되돌리는** 일회성 복원 도구다.

안전장치
  * 기본은 **DRY-RUN**(`--dry-run`, 기본값). `--apply` 없이는 어떤 쓰기도 하지 않는다.
  * 대상 선별에 `inst/frn/amt 4컬럼 = 0` 조건을 걸어 **재실행 멱등** + 실제 0 오염 방지.
  * 대상 행수·행별 값을 먼저 출력하고, `--expect N`(기본 18)과 불일치하면 중단.
  * `--apply` 는 (1) 백업 테이블 생성 → (2) UPDATE → (3) `data_fix_log` 감사행 → (4) 재선별 0행 확인.
  * `--rollback` 은 백업 테이블에서 1문장으로 되돌린다.
  * `price_history` 의 유일한 트리거 `price_history_basis_write_guard` 는
    `BEFORE INSERT OR UPDATE **OF open, high, low, close, volume**` 이므로
    수급 4컬럼 UPDATE 는 가드 대상이 아니다(`app.price_basis_checked` 불필요) — 2026-09-25 실측.

사용
    venv/bin/python3 scripts/fix_index_supply_null_restore_20260925.py                 # dry-run
    venv/bin/python3 scripts/fix_index_supply_null_restore_20260925.py --apply         # 승인 후
    venv/bin/python3 scripts/fix_index_supply_null_restore_20260925.py --rollback
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

RUN_ID = "index_supply_null_restore_20260925"
BACKUP_TABLE = "price_history_index_supply_backup_20260925"
CODES = ("^KS11", "^KQ11")
DATE_FROM, DATE_TO = "2026-09-11", "2026-09-23"
SUPPLY_COLS = ("inst_net_buy", "frn_net_buy", "inst_net_buy_amt", "frn_net_buy_amt")

TARGET_WHERE = (
    "stock_code IN ('^KS11','^KQ11') AND date >= '2026-09-11' AND date <= '2026-09-23' "
    "AND inst_net_buy = 0 AND frn_net_buy = 0 "
    "AND inst_net_buy_amt = 0 AND frn_net_buy_amt = 0"
)


def select_targets(conn):
    return conn.execute(
        f"""SELECT stock_code, date, inst_net_buy, frn_net_buy, inst_net_buy_amt, frn_net_buy_amt
            FROM price_history WHERE {TARGET_WHERE}
            ORDER BY stock_code, date"""
    ).fetchall()


def table_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
            (name,),
        ).fetchone()
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 백업+UPDATE+감사로그를 수행")
    ap.add_argument("--rollback", action="store_true", help="백업 테이블에서 되돌린다")
    ap.add_argument("--dry-run", action="store_true", help="(기본) 아무것도 쓰지 않는다")
    ap.add_argument("--expect", type=int, default=18)
    args = ap.parse_args()

    conn = connect_primary_db(timeout=60)
    try:
        if args.rollback:
            return _rollback(conn)

        rows = select_targets(conn)
        print(f"[대상] {len(rows)}행  ({', '.join(SUPPLY_COLS)} → NULL)")
        for r in rows:
            print("   ", dict(r) if hasattr(r, "keys") else tuple(r))
        if not rows:
            print("[결과] 대상 0행 — 이미 복원됐거나 조건 불일치. 아무것도 하지 않음.")
            return 0
        if len(rows) != args.expect:
            print(f"[중단] 대상 {len(rows)}행 != 기대 {args.expect}행 — 사람 확인 필요")
            return 3
        if any(any(r[i] not in (0, 0.0) for i in (2, 3, 4, 5)) for r in rows):
            print("[중단] 0 이 아닌 값이 섞여 있다 — 실제 관측값을 건드릴 수 있음")
            return 3

        nulls = ", ".join(f"{c} = NULL" for c in SUPPLY_COLS)
        if args.dry_run or not args.apply:
            print("[DRY-RUN] 실행될 문장:")
            print(f"   CREATE TABLE {BACKUP_TABLE} AS SELECT stock_code, date, "
                  f"{', '.join(SUPPLY_COLS)}, now() AS captured_at FROM price_history WHERE {TARGET_WHERE};")
            print(f"   UPDATE price_history SET {nulls} WHERE {TARGET_WHERE};")
            print(f"   INSERT INTO data_fix_log (run_id, table_name, scope, row_count, fix_rule, "
                  f"old_value_summary, new_value_summary, source) VALUES ('{RUN_ID}', 'price_history', "
                  f"'index supply 4 cols 2026-09-11..23 (^KS11/^KQ11)', {len(rows)}, "
                  f"'missing KIS supply was stored as 0.0 -> NULL (defect A)', 'all 4 cols = 0.0', "
                  f"'all 4 cols = NULL', 'scripts/fix_index_supply_null_restore_20260925.py');")
            print("[DRY-RUN] 쓰기 없음. 승인 후 --apply.")
            return 0

        if table_exists(conn, BACKUP_TABLE):
            print(f"[중단] 백업 테이블 {BACKUP_TABLE} 이 이미 있다 — 중복 적용 방지")
            return 3

        conn.execute(
            f"CREATE TABLE {BACKUP_TABLE} AS SELECT stock_code, date, "
            f"{', '.join(SUPPLY_COLS)}, now() AS captured_at FROM price_history WHERE {TARGET_WHERE}"
        )
        backed_up = conn.execute(f"SELECT count(*) FROM {BACKUP_TABLE}").fetchone()[0]
        conn.execute(f"UPDATE price_history SET {nulls} WHERE {TARGET_WHERE}")
        conn.execute(
            "INSERT INTO data_fix_log (run_id, table_name, scope, row_count, fix_rule, "
            "old_value_summary, new_value_summary, source) "
            "VALUES (?, 'price_history', ?, ?, ?, ?, ?, ?)",
            (RUN_ID, "index supply 4 cols 2026-09-11..23 (^KS11/^KQ11)", backed_up,
             "missing KIS supply was stored as 0.0 -> NULL (defect A)",
             "all 4 cols = 0.0", "all 4 cols = NULL",
             "scripts/fix_index_supply_null_restore_20260925.py"),
        )
        conn.commit()

        remaining = len(select_targets(conn))
        print(f"[APPLY] 백업 {backed_up}행 → UPDATE → 재선별 잔여 {remaining}행(0이어야 함)")
        return 0 if remaining == 0 else 3
    finally:
        conn.close()


def _rollback(conn) -> int:
    if not table_exists(conn, BACKUP_TABLE):
        print(f"[중단] 백업 테이블 {BACKUP_TABLE} 없음 — 되돌릴 근거가 없다")
        return 3
    sets = ", ".join(f"{c} = b.{c}" for c in SUPPLY_COLS)
    conn.execute(
        f"UPDATE price_history p SET {sets} FROM {BACKUP_TABLE} b "
        f"WHERE p.stock_code = b.stock_code AND p.date = b.date"
    )
    conn.execute(
        "INSERT INTO data_fix_log (run_id, table_name, scope, row_count, fix_rule, "
        "old_value_summary, new_value_summary, source) VALUES (?, 'price_history', ?, "
        "(SELECT count(*) FROM " + BACKUP_TABLE + "), ?, ?, ?, ?)",
        (RUN_ID + "_rollback", "index supply 4 cols 2026-09-11..23 restore from backup",
         "ROLLBACK: NULL -> 0.0 (backup table)", "all 4 cols = NULL", "all 4 cols = 0.0",
         "scripts/fix_index_supply_null_restore_20260925.py --rollback"),
    )
    conn.commit()
    print(f"[ROLLBACK] {BACKUP_TABLE} 에서 복원 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
