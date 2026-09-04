#!/usr/bin/env python3
"""Build non-destructive corporate-action and price-basis metadata tables."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"

DDL = """
CREATE TABLE IF NOT EXISTS price_series_registry (
  series_name TEXT PRIMARY KEY,
  price_basis TEXT NOT NULL,
  intended_use TEXT NOT NULL,
  source_detail TEXT NOT NULL,
  mixed_basis_risk INTEGER NOT NULL DEFAULT 0,
  policy_note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corporate_action_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_code TEXT NOT NULL,
  event_date TEXT NOT NULL,
  event_type TEXT NOT NULL,
  old_shares REAL,
  new_shares REAL,
  share_ratio REAL,
  backward_price_factor REAL,
  evidence_report_name TEXT,
  evidence_rcept_no TEXT,
  evidence_url TEXT,
  source TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  adjustment_status TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(stock_code, event_date, event_type)
);
CREATE INDEX IF NOT EXISTS idx_cae_code_date ON corporate_action_events(stock_code, event_date);
CREATE INDEX IF NOT EXISTS idx_cae_status ON corporate_action_events(adjustment_status, event_type);
CREATE VIEW IF NOT EXISTS price_history_quality_v AS
WITH p AS (
  SELECT ph.*,
         LAG(close) OVER (PARTITION BY stock_code ORDER BY date) AS prev_close
  FROM price_history ph
), e AS (
  SELECT stock_code, event_date,
         GROUP_CONCAT(event_type, ',') AS event_type,
         MAX(CASE WHEN adjustment_status='factor_confirmed' THEN 'factor_confirmed' ELSE adjustment_status END) AS adjustment_status,
         MAX(confidence) AS confidence
  FROM corporate_action_events
  GROUP BY stock_code, event_date
)
SELECT p.*,
       CASE WHEN p.prev_close > 0 THEN p.close / p.prev_close END AS daily_price_ratio,
       e.event_type AS corporate_action_type,
       e.adjustment_status,
       e.confidence AS corporate_action_confidence,
       CASE
         WHEN p.prev_close IS NULL OR p.prev_close <= 0 THEN 'insufficient_history'
         WHEN p.close / p.prev_close BETWEEN 0.55 AND 1.80 THEN 'normal'
         WHEN e.adjustment_status='factor_confirmed' THEN 'explained_corporate_action'
         WHEN e.event_type IS NOT NULL THEN 'corporate_action_review'
         ELSE 'unexplained_jump'
       END AS quality_status
FROM p
LEFT JOIN e ON e.stock_code=p.stock_code AND e.event_date=substr(p.date,1,10);
CREATE VIEW IF NOT EXISTS stock_price_daily_adjusted_v AS
WITH base AS (
  SELECT s.*,
         COALESCE((
           SELECT EXP(SUM(LN(e.backward_price_factor)))
           FROM corporate_action_events e
           WHERE e.stock_code=s.stock_code
             AND e.adjustment_status='factor_confirmed'
             AND e.backward_price_factor>0
             AND e.event_date > substr(s.bas_dt,1,4)||'-'||substr(s.bas_dt,5,2)||'-'||substr(s.bas_dt,7,2)
         ), 1.0) AS adjustment_factor
  FROM stock_price_daily s
)
SELECT bas_dt, stock_code, stock_name, market,
       open_price*adjustment_factor AS open_price,
       high_price*adjustment_factor AS high_price,
       low_price*adjustment_factor AS low_price,
       close_price*adjustment_factor AS close_price,
       CASE WHEN adjustment_factor>0 THEN volume/adjustment_factor END AS volume,
       trade_amt, market_cap, shares,
       adjustment_factor,
       CASE WHEN adjustment_factor=1.0 THEN 'raw_no_confirmed_action' ELSE 'confirmed_actions_adjusted' END AS adjustment_status
FROM base;
"""


def _compact(value: str | None) -> str:
    return "".join(str(value or "").split())


def _classify_by_report(name: str) -> str | None:
    n = _compact(name)
    if "주식병합" in n or "액면병합" in n or "자본감소" in n or "감자" in n:
        return "stock_merge_or_reduction"
    if "주식분할" in n or "액면분할" in n:
        return "stock_split"
    if "무상증자" in n:
        return "bonus_issue"
    if "유상증자" in n:
        return "rights_issue"
    return None


def _nearest_disclosure(conn: sqlite3.Connection, code: str, event_date: str) -> sqlite3.Row | None:
    d = datetime.strptime(event_date, "%Y-%m-%d")
    lo = (d - timedelta(days=45)).strftime("%Y%m%d")
    hi = (d + timedelta(days=10)).strftime("%Y%m%d")
    rows = conn.execute(
        """
        SELECT rcept_dt, report_nm, rcept_no, dart_url
        FROM dart_disclosures
        WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?
          AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%'
               OR report_nm LIKE '%무상증자%' OR report_nm LIKE '%유상증자%'
               OR report_nm LIKE '%감자%' OR report_nm LIKE '%자본감소%')
          AND report_nm NOT LIKE '%종속회사%'
        ORDER BY ABS(julianday(substr(rcept_dt,1,4)||'-'||substr(rcept_dt,5,2)||'-'||substr(rcept_dt,7,2))-julianday(?)),
                 CASE WHEN report_nm LIKE '[%' THEN 1 ELSE 0 END,
                 rcept_dt DESC
        LIMIT 1
        """,
        (code, lo, hi, event_date),
    ).fetchone()
    return rows


def build(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    conn.row_factory = sqlite3.Row
    conn.execute("DROP VIEW IF EXISTS price_history_quality_v")
    conn.execute("DROP VIEW IF EXISTS stock_price_daily_adjusted_v")
    conn.executescript(DDL)
    now = datetime.now().isoformat(timespec="seconds")
    registry = [
        ("price_history", "adjusted_intended_mixed_risk", "research/backtest signal calculation",
         "KIS adjusted-price collectors plus legacy historical sources", 1,
         "Do not overwrite from raw sources. Use quality view and exclude unexplained jumps.", now),
        ("stock_price_daily", "unadjusted", "execution-price and listed-share verification",
         "public/KRX-style daily OHLCV with market cap and listed shares", 0,
         "Use as raw execution reference; adjustment factors only for confirmed capital actions.", now),
    ]
    conn.executemany(
        """INSERT INTO price_series_registry VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(series_name) DO UPDATE SET price_basis=excluded.price_basis,
           intended_use=excluded.intended_use,source_detail=excluded.source_detail,
           mixed_basis_risk=excluded.mixed_basis_risk,policy_note=excluded.policy_note,updated_at=excluded.updated_at""",
        registry,
    )
    share_rows = conn.execute(
        """
        WITH s AS (
          SELECT stock_code, bas_dt, shares,
                 LAG(shares) OVER(PARTITION BY stock_code ORDER BY bas_dt) AS prev_shares
          FROM stock_price_daily WHERE shares>0
        )
        SELECT stock_code, bas_dt, prev_shares, shares
        FROM s WHERE prev_shares>0 AND ABS(shares/prev_shares-1)>=0.01
        ORDER BY stock_code, bas_dt
        """
    ).fetchall()
    events = []
    counts: dict[str, int] = {}
    for row in share_rows:
        code = row["stock_code"]
        event_date = f"{row['bas_dt'][:4]}-{row['bas_dt'][4:6]}-{row['bas_dt'][6:8]}"
        old_shares, new_shares = float(row["prev_shares"]), float(row["shares"])
        ratio = new_shares / old_shares
        disclosure = _nearest_disclosure(conn, code, event_date)
        report_type = _classify_by_report(disclosure["report_nm"]) if disclosure else None
        if report_type:
            event_type = report_type
            confidence = 0.9 if not str(disclosure["report_nm"]).startswith("[") else 0.8
            source = "stock_price_daily_shares+DART"
        elif ratio >= 1.5:
            event_type, confidence, source = "share_increase_unclassified", 0.55, "stock_price_daily_shares"
        elif ratio <= 0.67:
            event_type, confidence, source = "share_reduction_unclassified", 0.55, "stock_price_daily_shares"
        elif ratio > 1:
            event_type, confidence, source = "rights_or_other_issue", 0.45, "stock_price_daily_shares"
        else:
            event_type, confidence, source = "reduction_or_cancellation", 0.45, "stock_price_daily_shares"

        ratio_consistent = (
            (event_type == "stock_split" and ratio >= 1.5)
            or (event_type == "stock_merge_or_reduction" and ratio <= 0.67)
            or (event_type == "bonus_issue" and ratio >= 1.05)
        )
        adjustable = ratio_consistent and confidence >= 0.8
        if report_type and not ratio_consistent:
            confidence = min(confidence, 0.65)
        price_factor = old_shares / new_shares if adjustable else None
        minor_change = 0.95 <= ratio <= 1.05
        status = "factor_confirmed" if adjustable else ("not_price_adjusting" if minor_change else "review_required")
        note = (
            "Confirmed event; backward raw-price factor is old_shares/new_shares."
            if adjustable else
            "Share change is within 5%; retained for dilution review but no discontinuity factor is required."
            if minor_change else
            "No automatic price rewrite; event economics or type is not sufficiently confirmed."
        )
        events.append((code, event_date, event_type, old_shares, new_shares, ratio, price_factor,
                       disclosure["report_nm"] if disclosure else None,
                       disclosure["rcept_no"] if disclosure else None,
                       disclosure["dart_url"] if disclosure else None,
                       source, confidence, status, note, now, now))
        counts[event_type] = counts.get(event_type, 0) + 1

    if not dry_run:
        conn.executemany(
            """INSERT INTO corporate_action_events(
                 stock_code,event_date,event_type,old_shares,new_shares,share_ratio,backward_price_factor,
                 evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,adjustment_status,note,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stock_code,event_date,event_type) DO UPDATE SET
                 old_shares=excluded.old_shares,new_shares=excluded.new_shares,share_ratio=excluded.share_ratio,
                 backward_price_factor=excluded.backward_price_factor,evidence_report_name=excluded.evidence_report_name,
                 evidence_rcept_no=excluded.evidence_rcept_no,evidence_url=excluded.evidence_url,source=excluded.source,
                 confidence=excluded.confidence,adjustment_status=excluded.adjustment_status,note=excluded.note,updated_at=excluded.updated_at""",
            events,
        )
        conn.commit()
    explained = conn.execute("SELECT COUNT(*) FROM price_history_quality_v WHERE quality_status='explained_corporate_action'").fetchone()[0]
    unexplained = conn.execute("SELECT COUNT(*) FROM price_history_quality_v WHERE quality_status='unexplained_jump'").fetchone()[0]
    return {"share_change_events": len(events), "event_types": counts, "explained_price_jumps": explained, "unexplained_price_jumps": unexplained}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        print(build(conn, args.dry_run))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
