#!/usr/bin/env python3
"""Classify extreme price jumps and build a non-destructive canonical quality layer."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import IS_POSTGRES  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "price_basis_audit_20260712.json"
OUT_LATEST = ROOT / "research_outputs" / "price_basis_audit_latest.json"

DDL = """
CREATE TABLE IF NOT EXISTS price_jump_audit (
  stock_code TEXT NOT NULL,
  event_date TEXT NOT NULL,
  previous_date TEXT,
  previous_close REAL,
  event_close REAL,
  price_ratio REAL,
  public_previous_close REAL,
  public_event_close REAL,
  public_price_ratio REAL,
  classification TEXT NOT NULL,
  return_usable INTEGER NOT NULL DEFAULT 0,
  matched_event_type TEXT,
  matched_report_name TEXT,
  evidence TEXT NOT NULL DEFAULT '',
  audited_at TEXT NOT NULL,
  PRIMARY KEY(stock_code, event_date)
);
CREATE INDEX IF NOT EXISTS idx_pja_class ON price_jump_audit(classification, return_usable);
CREATE VIEW IF NOT EXISTS canonical_price_history_v AS
SELECT p.*,
       COALESCE(a.classification,
         CASE WHEN q.quality_status='normal' THEN 'normal' ELSE q.quality_status END
       ) AS canonical_quality,
       CASE
         WHEN a.return_usable IS NOT NULL THEN a.return_usable
         WHEN q.quality_status IN ('normal','insufficient_history') THEN 1
         ELSE 0
       END AS return_usable,
       'price_history' AS selected_series,
       r.price_basis
FROM price_history p
LEFT JOIN price_history_quality_v q ON q.id=p.id
LEFT JOIN price_jump_audit a ON a.stock_code=p.stock_code AND a.event_date=substr(p.date,1,10)
LEFT JOIN price_series_registry r ON r.series_name='price_history';
CREATE VIEW IF NOT EXISTS canonical_price_returns_v AS
WITH x AS (
  SELECT c.*,
         LAG(close) OVER(PARTITION BY stock_code ORDER BY date) AS canonical_prev_close,
         LAG(return_usable) OVER(PARTITION BY stock_code ORDER BY date) AS previous_return_usable
  FROM canonical_price_history_v c
)
SELECT x.*,
       CASE WHEN return_usable=1 AND previous_return_usable=1 AND canonical_prev_close>0
            THEN close/canonical_prev_close-1 END AS safe_daily_return
FROM x;
"""


def _iso(raw: str) -> str:
    value = str(raw or "")[:10]
    return value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def run(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    if not IS_POSTGRES:
        conn.execute("DROP VIEW IF EXISTS canonical_price_returns_v")
        conn.execute("DROP VIEW IF EXISTS canonical_price_history_v")
        conn.executescript(DDL)
    current_common = set(r[0] for r in conn.execute(
        """WITH x AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY base_date DESC,id DESC) rn FROM stock_universe)
           SELECT stock_code FROM x WHERE rn=1 AND market IN ('KOSPI','KOSDAQ') AND COALESCE(stock_type,'보통주')='보통주'"""
    ))
    jumps = conn.execute(
        """
        WITH d AS (
          SELECT stock_code, substr(date,1,10) event_date, close,
                 LAG(substr(date,1,10)) OVER(PARTITION BY stock_code ORDER BY date) previous_date,
                 LAG(close) OVER(PARTITION BY stock_code ORDER BY date) previous_close
          FROM price_history WHERE close>0
        )
        SELECT * FROM d WHERE previous_close>0 AND (close/previous_close>1.8 OR close/previous_close<0.55)
        """
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    counts = Counter()
    for row in jumps:
        code, event_date = row["stock_code"], row["event_date"]
        ratio = float(row["close"]) / float(row["previous_close"])
        prev_raw = conn.execute(
            "SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt<=? ORDER BY bas_dt DESC LIMIT 1",
            (code, row["previous_date"].replace("-", "")),
        ).fetchone()
        cur_raw = conn.execute(
            "SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt=?",
            (code, event_date.replace("-", "")),
        ).fetchone()
        raw_prev = float(prev_raw[0]) if prev_raw and prev_raw[0] else None
        raw_cur = float(cur_raw[0]) if cur_raw and cur_raw[0] else None
        raw_ratio = raw_cur / raw_prev if raw_prev and raw_cur else None

        d = datetime.strptime(event_date, "%Y-%m-%d")
        lo, hi = (d-timedelta(days=10)).strftime("%Y%m%d"), (d+timedelta(days=3)).strftime("%Y%m%d")
        disclosure = conn.execute(
            """SELECT report_nm FROM dart_disclosures WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?
               AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%' OR report_nm LIKE '%무상증자%'
                    OR report_nm LIKE '%유상증자%' OR report_nm LIKE '%감자%' OR report_nm LIKE '%상장폐지%')
               ORDER BY rcept_dt DESC LIMIT 1""", (code, lo, hi)
        ).fetchone()
        action = conn.execute(
            """SELECT event_type,adjustment_status,evidence_report_name FROM corporate_action_events
               WHERE stock_code=? AND event_date BETWEEN ? AND ? ORDER BY confidence DESC LIMIT 1""",
            (code, (d-timedelta(days=3)).date().isoformat(), (d+timedelta(days=3)).date().isoformat()),
        ).fetchone()

        if not (code.isdigit() and len(code) == 6):
            classification, usable = "non_equity_symbol", 0
            evidence = "Index/macro symbol mixed into price_history"
        elif action and action["adjustment_status"] == "factor_confirmed":
            classification, usable = "confirmed_corporate_action", 0
            evidence = f"Confirmed normalized event: {action['event_type']}"
        elif disclosure:
            classification, usable = "corporate_action_or_delisting_nearby", 0
            evidence = f"Nearby disclosure: {disclosure['report_nm']}"
        elif raw_ratio is not None and abs(raw_ratio-ratio)/max(abs(ratio), 0.01) <= 0.15:
            classification, usable = "raw_source_confirmed_jump", 1
            evidence = f"Public raw series confirms ratio {raw_ratio:.4f}"
        elif raw_ratio is not None and 0.55 <= raw_ratio <= 1.8:
            classification, usable = "mixed_basis_or_price_corruption", 0
            evidence = f"price_history ratio {ratio:.4f}, raw ratio {raw_ratio:.4f}"
        elif code not in current_common:
            classification, usable = "inactive_or_noncommon_review", 0
            evidence = "Not in latest active KOSPI/KOSDAQ common-stock universe"
        else:
            classification, usable = "unresolved_active_common", 0
            evidence = "No same-day raw confirmation or nearby capital-action evidence"
        counts[classification] += 1
        records.append((code,event_date,row["previous_date"],row["previous_close"],row["close"],ratio,
                        raw_prev,raw_cur,raw_ratio,classification,usable,
                        action["event_type"] if action else None,
                        (action["evidence_report_name"] if action else None) or (disclosure["report_nm"] if disclosure else None),
                        evidence,now))
    # The audit is a snapshot of current jumps. Backfills can remove old jumps,
    # so stale audit rows must not survive a rebuild.
    conn.execute("DELETE FROM price_jump_audit")
    conn.executemany(
        """INSERT INTO price_jump_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(stock_code,event_date) DO UPDATE SET previous_date=excluded.previous_date,
           previous_close=excluded.previous_close,event_close=excluded.event_close,price_ratio=excluded.price_ratio,
           public_previous_close=excluded.public_previous_close,public_event_close=excluded.public_event_close,
           public_price_ratio=excluded.public_price_ratio,classification=excluded.classification,
           return_usable=excluded.return_usable,matched_event_type=excluded.matched_event_type,
           matched_report_name=excluded.matched_report_name,evidence=excluded.evidence,audited_at=excluded.audited_at""",
        records,
    )
    conn.commit()
    result = {"audited_jumps": len(records), "classifications": dict(counts),
              "return_usable_jumps": sum(r[10] for r in records), "audited_at": now,
              "database_backend": "postgresql" if IS_POSTGRES else "sqlite"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_LATEST.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    if "--require-postgres" in sys.argv and not IS_POSTGRES:
        raise RuntimeError("price jump audit requires PostgreSQL, but SQLite routing is active")
    conn = connect_stock_db(timeout=60)
    try:
        print(json.dumps(run(conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()
