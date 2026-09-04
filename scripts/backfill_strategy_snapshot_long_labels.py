"""strategy_feature_snapshot에 24/36개월 forward 라벨을 추가·백필한다.

배경(2026-08-08): 기존 라벨은 forward 12개월이 최대라, 실제 10배 종목의 소요기간
(중위 609일 = 1.7년) 대비 관측창이 짧아 "10배"가 구조적으로 집계되지 않았다.
실측: 3년내 10배 종목 251개 중 12개월 안에 완성되는 건 13.1%뿐.

이 스크립트는 기존 컬럼(heuristic_score / model_score_* 등)을 일절 건드리지 않고
신규 라벨 컬럼만 채운다. 전체 재빌드(build_strategy_research_dataset.py)는 기존
점수까지 재계산하므로, 이미 참조 중인 값을 보존하려면 이 백필을 쓴다.

계산 방식은 build_strategy_research_dataset.py와 동일:
  forward_max_ret_Nm = (스냅샷 이후 N개월 거래일 구간의 최고 종가) / 스냅샷 종가 - 1
  (비율 스케일: 1.0 = +100%, 3배 라벨 = 2.0, 10배 라벨 = 9.0)
"""
from __future__ import annotations

import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db_utils import connect_stock_db, stock_db_write_lock  # noqa: E402

TABLE = "strategy_feature_snapshot"

# 거래일 기준 창 (build_strategy_research_dataset.py와 동일)
WINDOWS = {"24m": 504, "36m": 756}
# 라벨 최소 소요기간(달력일). 이 날짜 이후 스냅샷은 창이 안 차므로 NULL 유지.
READY_DAYS = {"24m": 730, "36m": 1095}

NEW_COLUMNS = [
    ("forward_max_ret_24m", "REAL"),
    ("forward_max_ret_36m", "REAL"),
    ("label_3x_24m", "INTEGER"),
    ("label_5x_24m", "INTEGER"),
    ("label_10x_24m", "INTEGER"),
    ("label_5x_36m", "INTEGER"),
    ("label_10x_36m", "INTEGER"),
]

# 라벨 임계값(비율 스케일): 3배=+200%, 5배=+400%, 10배=+900%
LABEL_THRESHOLDS = [
    ("label_3x_24m", "24m", 2.0),
    ("label_5x_24m", "24m", 4.0),
    ("label_10x_24m", "24m", 9.0),
    ("label_5x_36m", "36m", 4.0),
    ("label_10x_36m", "36m", 9.0),
]


def _ensure_columns(conn) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({TABLE})")}
    for name, coltype in NEW_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {coltype}")
            print(f"  + 컬럼 추가: {name} {coltype}")
    conn.commit()


def _load_price_series(conn) -> tuple[dict[str, list[str]], dict[str, list[float]], str]:
    """종목별 (날짜 오름차순, 종가) 시계열. 같은 날짜 중복은 마지막 값을 채택."""
    dates: dict[str, list[str]] = defaultdict(list)
    closes: dict[str, list[float]] = defaultdict(list)
    max_date = ""
    rows = conn.execute(
        """
        SELECT stock_code, substr(date, 1, 10) AS d, close
        FROM price_history
        WHERE close > 0
        ORDER BY stock_code, d, date
        """
    )
    for code, d, close in rows:
        dl = dates[code]
        if dl and dl[-1] == d:      # 같은 날짜 중복 → 마지막 값으로 덮어씀
            closes[code][-1] = float(close)
        else:
            dl.append(d)
            closes[code].append(float(close))
        if d > max_date:
            max_date = d
    return dates, closes, max_date


def backfill(dry_run: bool = False) -> dict:
    conn = connect_stock_db(timeout=120)
    try:
        if not dry_run:
            _ensure_columns(conn)

        print("price_history 로딩 중...")
        dates, closes, max_date = _load_price_series(conn)
        print(f"  {len(dates):,}종목 / 최신 거래일 {max_date}")

        cutoff = {
            k: (datetime.strptime(max_date, "%Y-%m-%d") - timedelta(days=v)).strftime("%Y-%m-%d")
            for k, v in READY_DAYS.items()
        }
        print(f"  라벨 유효 상한: 24m ≤ {cutoff['24m']} / 36m ≤ {cutoff['36m']}")

        snaps = conn.execute(
            f"SELECT snapshot_date, stock_code, close_price FROM {TABLE}"
        ).fetchall()
        print(f"스냅샷 {len(snaps):,}행 처리 중...")

        updates = []
        stats = {"24m": 0, "36m": 0, "no_price": 0}
        for snap_date, code, _stored_close in snaps:
            dl = dates.get(code)
            if not dl:
                stats["no_price"] += 1
                updates.append((None, None, None, None, None, None, None, snap_date, code))
                continue
            pos = bisect_right(dl, snap_date) - 1
            if pos < 0:
                stats["no_price"] += 1
                updates.append((None, None, None, None, None, None, None, snap_date, code))
                continue

            cl = closes[code]
            cur = cl[pos]
            fwd: dict[str, float | None] = {"24m": None, "36m": None}
            if cur > 0 and pos + 1 < len(cl):
                for key, span in WINDOWS.items():
                    if snap_date > cutoff[key]:
                        continue  # 창이 아직 안 참 → NULL 유지
                    window = cl[pos + 1: min(len(cl), pos + 1 + span)]
                    if window:
                        fwd[key] = max(window) / cur - 1.0
                        stats[key] += 1

            labels = [
                None if fwd[key] is None else int(fwd[key] >= th)
                for _, key, th in LABEL_THRESHOLDS
            ]
            updates.append((fwd["24m"], fwd["36m"], *labels, snap_date, code))

        print(f"  forward 산출: 24m {stats['24m']:,}행 / 36m {stats['36m']:,}행 "
              f"(가격이력 없음 {stats['no_price']:,}행)")
        if dry_run:
            print("[dry-run] DB 미반영")
            return {"rows": len(updates), **stats, "dry_run": True}

        with stock_db_write_lock("backfill_strategy_snapshot_long_labels", timeout=600) as got:
            if not got:
                raise RuntimeError("stock.db writer lock 획득 실패")
            conn.executemany(
                f"""UPDATE {TABLE} SET
                        forward_max_ret_24m = ?, forward_max_ret_36m = ?,
                        label_3x_24m = ?, label_5x_24m = ?, label_10x_24m = ?,
                        label_5x_36m = ?, label_10x_36m = ?
                    WHERE snapshot_date = ? AND stock_code = ?""",
                updates,
            )
            conn.commit()
        print(f"✅ {len(updates):,}행 UPDATE 완료")
        return {"rows": len(updates), **stats}
    finally:
        conn.close()


if __name__ == "__main__":
    out = backfill(dry_run="--dry-run" in sys.argv)
    print(out)
