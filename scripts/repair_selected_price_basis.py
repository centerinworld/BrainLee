#!/usr/bin/env python3
"""Repair selected-strategy price series from one reproducible Naver snapshot.

The legacy price_history table mixed adjusted and raw sources. This tool stages
complete per-security histories, rejects internally discontinuous snapshots,
backs up every changed OHLCV row, and supports exact restoration.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from config import IS_POSTGRES  # noqa: E402
from database import engine  # noqa: E402
from scripts.backfill_naver_ohlcv_2015_2018 import fetch  # noqa: E402

AUDIT = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"
OUT = ROOT / "research_outputs" / "selected_price_basis_repair_latest.json"
START = "20150101"
END = datetime.now().strftime("%Y%m%d")

DDL = """
CREATE TABLE IF NOT EXISTS selected_price_repair_stage(
 batch_id TEXT NOT NULL,stock_code TEXT NOT NULL,date TEXT NOT NULL,
 open REAL,high REAL,low REAL,close REAL,volume REAL,
 source_url TEXT NOT NULL,fetched_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,stock_code,date)
);
CREATE TABLE IF NOT EXISTS selected_price_repair_backup(
 batch_id TEXT NOT NULL,stock_code TEXT NOT NULL,date TEXT NOT NULL,
 open REAL,high REAL,low REAL,close REAL,volume REAL,
 backed_up_at TEXT NOT NULL,
 PRIMARY KEY(batch_id,stock_code,date)
);
CREATE TABLE IF NOT EXISTS selected_price_repair_inserted(
 batch_id TEXT NOT NULL,stock_code TEXT NOT NULL,date TEXT NOT NULL,
 PRIMARY KEY(batch_id,stock_code,date)
);
CREATE TABLE IF NOT EXISTS selected_price_repair_batches(
 batch_id TEXT PRIMARY KEY,status TEXT NOT NULL,source TEXT NOT NULL,
 target_codes_json TEXT NOT NULL,eligible_codes_json TEXT NOT NULL,
 created_at TEXT NOT NULL,applied_at TEXT,restored_at TEXT
);
"""


def _ensure_schema(conn) -> None:
    if IS_POSTGRES:
        with engine.begin() as db:
            for statement in (part.strip() for part in DDL.split(";")):
                if statement:
                    db.exec_driver_sql(statement)
        return
    conn.executescript(DDL)


def _targets() -> tuple[list[str], list[tuple[str, str]], dict[str, str]]:
    payload = json.loads(AUDIT.read_text(encoding="utf-8"))
    events = {
        (event["stock_code"], event["event_date"])
        for strategy in payload.get("strategies", [])
        for event in strategy.get("contaminated_events", [])
    }
    relevant_ends: dict[str, str] = {}
    for strategy in payload.get("strategies", []):
        for event in strategy.get("contaminated_events", []):
            code = event["stock_code"]
            relevant_ends[code] = max(relevant_ends.get(code, ""), str(event["holding_end"])[:10])
    return sorted({code for code, _ in events}), sorted(events), relevant_ends


def _invalid_ohlcv_codes() -> set[str]:
    """Return symbols whose adjusted close is inconsistent with stored OHLC."""
    conn = connect_stock_db(timeout=120)
    try:
        rows = conn.execute(
            """SELECT DISTINCT stock_code FROM price_history
               WHERE close IS NULL OR close<=0 OR volume IS NULL OR volume<0
                  OR (volume>0 AND (open IS NULL OR open<=0 OR high IS NULL OR high<=0 OR low IS NULL OR low<=0))
                  OR (open>0 AND (high<low OR high<open OR high<close OR low>open OR low>close))"""
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}
    finally:
        conn.close()


def _has_discontinuity(rows: list[tuple]) -> list[dict]:
    ordered = sorted(rows, key=lambda item: item[1])
    bad = []
    for previous, current in zip(ordered, ordered[1:]):
        before, after = float(previous[5]), float(current[5])
        if before <= 0:
            continue
        ratio = after / before
        if ratio > 1.8 or ratio < 0.55:
            bad.append({"previous_date": previous[1], "event_date": current[1], "ratio": round(ratio, 6)})
    return bad


def _stage(conn, batch_id: str, rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT INTO selected_price_repair_stage
             (batch_id,stock_code,date,open,high,low,close,volume,source_url,fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(batch_id,stock_code,date) DO UPDATE SET
             open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
             volume=excluded.volume,source_url=excluded.source_url,fetched_at=excluded.fetched_at""",
        [(batch_id, *row) for row in rows],
    )


def run(
    apply: bool = False,
    workers: int = 6,
    only_codes: set[str] | None = None,
    manual_cutoff: str = "",
) -> dict:
    codes, selected_events, relevant_ends = _targets()
    if only_codes:
        codes = sorted(only_codes)
        if manual_cutoff:
            relevant_ends.update({code: manual_cutoff for code in codes})
    batch_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    fetched: dict[str, list[tuple]] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {pool.submit(fetch, code, START, END): code for code in codes}
        for future in as_completed(futures):
            code, rows, error = future.result()
            fetched[code] = rows
            if error:
                errors[code] = error

    diagnostics = {}
    eligible = []
    cutoffs: dict[str, str] = {}
    for code in codes:
        rows = fetched.get(code, [])
        jumps = _has_discontinuity(rows)
        event_dates = {row[1] for row in rows}
        required = [event_date for event_code, event_date in selected_events if event_code == code]
        missing_events = [event_date for event_date in required if event_date not in event_dates]
        relevant_end = relevant_ends.get(code, END)
        blocking_jumps = [jump for jump in jumps if jump["event_date"] <= relevant_end]
        # A caller-provided cutoff is a hard upper bound, not merely a fallback
        # for discontinuous histories. This prevents a historical repair from
        # staging and overwriting years of unrelated recent prices.
        cutoff = manual_cutoff or (relevant_end if jumps else END)
        diagnostics[code] = {
            "rows": len(rows), "first_date": rows[0][1] if rows else None,
            "last_date": rows[-1][1] if rows else None, "discontinuities": jumps,
            "required_events": len(required), "missing_event_dates": missing_events,
            "relevant_holding_end": relevant_end, "blocking_discontinuities": blocking_jumps,
            "repair_cutoff": cutoff, "error": errors.get(code),
        }
        if len(rows) >= 200 and not blocking_jumps and not missing_events and code not in errors:
            eligible.append(code)
            cutoffs[code] = cutoff

    conn = connect_stock_db(timeout=120)
    try:
        _ensure_schema(conn)
        for code in eligible:
            _stage(conn, batch_id, [row for row in fetched[code] if row[1] <= cutoffs[code]])
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO selected_price_repair_batches
                 (batch_id,status,source,target_codes_json,eligible_codes_json,created_at)
               VALUES(?,?,?,?,?,?)""",
            (batch_id, "staged", "naver_finance_fchart_full_snapshot",
             json.dumps(codes), json.dumps(eligible), now),
        )
        if apply and eligible:
            conn.execute(
                f"""INSERT INTO selected_price_repair_backup
                       (batch_id,stock_code,date,open,high,low,close,volume,backed_up_at)
                     SELECT ?,p.stock_code,substr(p.date,1,10),p.open,p.high,p.low,p.close,p.volume,?
                     FROM price_history p
                     JOIN selected_price_repair_stage s
                       ON s.batch_id=? AND s.stock_code=p.stock_code
                      AND s.date=substr(p.date,1,10)
                     ON CONFLICT(batch_id,stock_code,date) DO NOTHING""",
                (batch_id, now, batch_id),
            )
            conn.execute(
                """INSERT INTO selected_price_repair_inserted(batch_id,stock_code,date)
                   SELECT s.batch_id,s.stock_code,s.date
                   FROM selected_price_repair_stage s
                   LEFT JOIN selected_price_repair_backup b
                     ON b.batch_id=s.batch_id AND b.stock_code=s.stock_code AND b.date=s.date
                   WHERE s.batch_id=? AND b.batch_id IS NULL
                   ON CONFLICT(batch_id,stock_code,date) DO NOTHING""",
                (batch_id,),
            )
            conn.execute(
                """UPDATE price_history p SET
                     open=s.open,high=s.high,low=s.low,close=s.close,volume=s.volume
                   FROM selected_price_repair_stage s
                   WHERE s.batch_id=? AND s.stock_code=p.stock_code
                     AND s.date=substr(p.date,1,10)""",
                (batch_id,),
            )
            conn.execute(
                """INSERT INTO price_history(stock_code,date,open,high,low,close,volume)
                   SELECT s.stock_code,s.date,s.open,s.high,s.low,s.close,s.volume
                   FROM selected_price_repair_stage s
                   WHERE s.batch_id=? AND NOT EXISTS(
                     SELECT 1 FROM price_history p
                     WHERE p.stock_code=s.stock_code AND substr(p.date,1,10)=s.date)""",
                (batch_id,),
            )
            conn.execute(
                """UPDATE selected_price_repair_batches
                   SET status='applied',applied_at=? WHERE batch_id=?""",
                (now, batch_id),
            )
        conn.commit()
    finally:
        conn.close()

    result = {
        "batch_id": batch_id, "mode": "apply" if apply else "stage_only",
        "target_codes": len(codes), "eligible_codes": len(eligible),
        "ineligible_codes": sorted(set(codes) - set(eligible)),
        "request_errors": errors, "diagnostics": diagnostics,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def restore(batch_id: str) -> dict:
    conn = connect_stock_db(timeout=120)
    try:
        _ensure_schema(conn)
        status = conn.execute(
            "SELECT status FROM selected_price_repair_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if not status or status[0] != "applied":
            raise ValueError("batch is not in applied state")
        conn.execute(
            """UPDATE price_history p SET
                 open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume
               FROM selected_price_repair_backup b
               WHERE b.batch_id=? AND b.stock_code=p.stock_code
                 AND b.date=substr(p.date,1,10)""",
            (batch_id,),
        )
        # Older repair batches predate selected_price_repair_inserted. Derive
        # their inserted rows from the immutable stage and backup snapshots.
        conn.execute(
            """INSERT INTO selected_price_repair_inserted(batch_id,stock_code,date)
               SELECT s.batch_id,s.stock_code,s.date FROM selected_price_repair_stage s
               WHERE s.batch_id=? AND NOT EXISTS(
                 SELECT 1 FROM selected_price_repair_backup b
                 WHERE b.batch_id=s.batch_id AND b.stock_code=s.stock_code AND b.date=s.date)
               ON CONFLICT(batch_id,stock_code,date) DO NOTHING""",
            (batch_id,),
        )
        conn.execute(
            """DELETE FROM price_history p USING selected_price_repair_inserted i
               WHERE i.batch_id=? AND i.stock_code=p.stock_code
                 AND i.date=substr(p.date,1,10)""",
            (batch_id,),
        )
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """UPDATE selected_price_repair_batches
               SET status='restored',restored_at=? WHERE batch_id=?""",
            (now, batch_id),
        )
        conn.commit()
        return {"batch_id": batch_id, "status": "restored"}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore", default="")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--codes", default="")
    parser.add_argument(
        "--invalid-ohlcv",
        action="store_true",
        help="Target every symbol currently violating OHLC invariants.",
    )
    parser.add_argument("--manual-cutoff", default="", help="YYYY-MM-DD; requires --codes")
    args = parser.parse_args()
    only_codes = {value.strip().zfill(6) for value in args.codes.split(",") if value.strip()}
    if args.invalid_ohlcv:
        only_codes.update(_invalid_ohlcv_codes())
    payload = restore(args.restore) if args.restore else run(
        args.apply, args.workers, only_codes or None, args.manual_cutoff
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
