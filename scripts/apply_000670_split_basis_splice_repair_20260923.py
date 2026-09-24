#!/usr/bin/env python3
"""Repair 000670(SK하이닉스)의 2021-02-17~2025-04-24 구간 가격기준 오류.

배경(hermes.md "unresolved_active_common pykrx 검증" 섹션 전체 조사 과정 참고):
이 구간의 price_history OHLCV가 실제 원가(raw, 2025-04-25 실제 10:1 액면분할 전 기준)
대신 그 미래 분할이 적용된 것처럼 ~10.61배 낮은(그리고 거래량은 그만큼 높은) 값으로
저장돼 있었다 - 진짜 분할은 DART 확인 결과 2025-04-25 신주상장이 맞는데, 우리 데이터는
4년 넘게 앞서 2021-02-17부터 이미 그 분할후 기준으로 전환돼 있었다(원인 미상 - 아마도
언젠가 이 구간을 pykrx 같은 "현재 기준 자동조정" 소스로 재백필한 배치가 원본을
덮어썼을 것으로 추정, 다음 세션이 원인 자체를 더 파도 됨. 이 스크립트는 결과만 고친다).

증거(2단계 독립 교차검증):
  1. marcap(비조정 원가) - 2010년 값(569,000원, 발행주식 1,842,040주)이 우리 DB의
     2010년 값과 정확히 일치 = 2010~2021-02-16 구간은 우리 데이터가 옳았다는 확인.
     그런데 marcap은 2021-02-17 이후에도 발행주식수 1,842,040(비분할)을 유지하며
     578,000원대 정상 연속 - 우리 DB만 2021-02-17부터 54,465원으로 떨어짐.
  2. DART(000670, 2025-04-10 [기재정정]주식분할결정) - 액면가 5,000→500원(10:1),
     발행주식 1,842,040→18,420,400주, 신주상장예정일 2025-04-25. 실제 분할일이
     2021-02-17이 아니라 2025-04-25라는 것을 공식 확인.

범위: 2021-02-17~2025-04-24(정지기간 포함, marcap도 이 기간 자체적으로 정지마커
O=H=L=0/close유지/volume=0으로 기록 - 별도 이상현상 아님, 정상 관례). 2025-04-25 이후는
실제 분할이 반영된 시점이라 건드리지 않음(우리 DB도 이미 marcap과 일치).

marcap 데이터는 /tmp/skhynix_marcap_fix_range.parquet에 미리 받아둔 1,029행
(2026-09-23 확인, ensure_year(2021~2025) 캐시 기반)을 그대로 사용.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, invalid_ohlcv  # noqa: E402

BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS price_history_fix_backup (
  run_id TEXT NOT NULL, stock_code TEXT NOT NULL, date TEXT NOT NULL,
  old_open DOUBLE PRECISION, old_high DOUBLE PRECISION, old_low DOUBLE PRECISION,
  old_close DOUBLE PRECISION, old_volume DOUBLE PRECISION,
  new_open DOUBLE PRECISION, new_high DOUBLE PRECISION, new_low DOUBLE PRECISION,
  new_close DOUBLE PRECISION, new_volume DOUBLE PRECISION,
  reason TEXT NOT NULL, fixed_at TEXT NOT NULL,
  PRIMARY KEY(run_id, stock_code, date)
)
"""

STOCK_CODE = "000670"
MARCAP_PARQUET = "/tmp/skhynix_marcap_fix_range.parquet"

REASON = (
    "split-basis splice repair: 2021-02-17~2025-04-24 구간이 2025-04-25 실제 10:1 "
    "액면분할 전인데도 분할후 기준(~10.61배 낮은 가격, 그만큼 높은 거래량)으로 저장돼 "
    "있었음 - marcap(비조정 원가, 2010년 값+발행주식수 일치로 검증)과 DART(2025-04-10 "
    "주식분할결정, 신주상장 2025-04-25)로 이중 확인. marcap 원가로 OHLCV 전체 교체."
)


def build_plan(conn):
    marcap = pd.read_parquet(MARCAP_PARQUET)
    marcap["Date"] = marcap["Date"].astype(str)
    plan = []
    for _, row in marcap.iterrows():
        day = row["Date"]
        new_o, new_h, new_l = float(row["Open"]), float(row["High"]), float(row["Low"])
        new_c, new_v = float(row["Close"]), float(row["Volume"])
        cur = conn.execute(
            "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date=?",
            (STOCK_CODE, day),
        ).fetchone()
        if not cur or any(x is None for x in cur):
            continue
        cur = tuple(float(x) for x in cur)
        new = (new_o, new_h, new_l, new_c, new_v)
        if invalid_ohlcv(new_o, new_h, new_l, new_c, new_v):
            # marcap's own suspension-marker rows (O=H=L=0, volume=0, real close) are
            # explicitly allowed by invalid_ohlcv's carve-out; anything else failing
            # here would be a real problem worth stopping on, so don't silently skip.
            if not (new_v == 0 and new_o == 0 and new_h == 0 and new_l == 0):
                raise RuntimeError(f"unexpected invalid replacement OHLCV at {day}: {new}")
        # Skip if already matching (idempotent re-run)
        if all(abs(a - b) <= 1.0 for a, b in zip(cur, new)):
            continue
        plan.append((day, cur, new))
    return plan


def run(dry_run: bool = False) -> dict:
    conn = connect_primary_db(timeout=120)
    try:
        native_script(conn, BACKUP_DDL)
        plan = build_plan(conn)

        run_id = f"000670_split_basis_splice_repair_20260923_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows = [], []

        for day, cur, new in plan:
            backup_rows.append((run_id, STOCK_CODE, day, *cur, *new, REASON,
                                 datetime.now().isoformat(timespec="seconds")))
            update_rows.append((*new, STOCK_CODE, day))

        result = {"run_id": run_id, "candidates": len(plan), "ready_rows": len(update_rows),
                   "dry_run": dry_run}
        if dry_run:
            return result

        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.executemany(
            """INSERT INTO price_history_fix_backup
               (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            backup_rows,
        )
        conn.executemany(
            "UPDATE price_history SET open=?,high=?,low=?,close=?,volume=? WHERE stock_code=? AND date=?",
            update_rows,
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                "000670(SK하이닉스) 2021-02-17~2025-04-24 split-basis splice repair",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = marcap raw values",
                "OHLCV on incorrect post-2025-split-equivalent basis (~10.61x too low "
                "price, proportionally high volume) 4+ years before the real split",
                "marcap raw OHLCV for the exact affected window",
                "marcap (2010 value + share count cross-check) + DART 2025-04-10 "
                "주식분할결정(10:1, 신주상장 2025-04-25) - 2026-09-23", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
