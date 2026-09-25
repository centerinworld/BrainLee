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
from marcap_client import share_count_evidence  # noqa: E402
from price_integrity import native_script, ensure_schema, refresh_calendar, rebuild_views, outside_band, install_write_guard  # noqa: E402
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
"""
# canonical_price_history_v / canonical_price_returns_v are owned by
# price_integrity.rebuild_views(), not by this script. That module applies
# the actual ±15%/±30% price-limit band by date, honors
# price_integrity_quarantine, and — critically — runs its DDL through
# native_script() so it actually executes on PostgreSQL (this script's own
# CREATE VIEW here used to silently no-op there; see IS_POSTGRES guard
# below). Defining the same view names in two places would make whichever
# script runs last win unpredictably, so this script only owns price_jump_audit.


def _iso(raw: str) -> str:
    value = str(raw or "")[:10]
    return value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def run(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    native_script(conn, DDL)
    ensure_schema(conn)
    install_write_guard(conn)
    refresh_calendar(conn)
    rebuild_views(conn)
    current_common = set(r[0] for r in conn.execute(
        """WITH x AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY base_date DESC,id DESC) rn FROM stock_universe)
           SELECT stock_code FROM x WHERE rn=1 AND market IN ('KOSPI','KOSDAQ') AND COALESCE(stock_type,'보통주')='보통주'"""
    ))
    jumps = conn.execute(
        """
        SELECT stock_code,substr(date,1,10) event_date,close,prev_close previous_close,
               substr(previous_date,1,10) previous_date,quality_status
        FROM price_history_quality_v
        WHERE quality_status NOT IN ('normal','insufficient_history','suspended')
        """
    ).fetchall()
    now = datetime.now().isoformat(timespec="seconds")
    records = []
    counts = Counter()
    for row in jumps:
        code, event_date = row["stock_code"], row["event_date"]
        ratio = (float(row["close"]) / float(row["previous_close"])
                 if row["close"] is not None and row["previous_close"] and row["previous_close"] > 0 else None)
        prev_raw = conn.execute(
            "SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt=? ORDER BY bas_dt DESC LIMIT 1",
            (code, (row["previous_date"] or "").replace("-", "")),
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
               WHERE stock_code=? AND event_date BETWEEN ? AND ? ORDER BY CASE WHEN adjustment_status='factor_confirmed' THEN 0 ELSE 1 END, confidence DESC LIMIT 1""",
            (code, (d-timedelta(days=3)).date().isoformat(), (d+timedelta(days=3)).date().isoformat()),
        ).fetchone()
        if action is None and ratio is not None:
            # capital-reduction/reverse-split events are frequently logged in
            # corporate_action_events under their DART registration-completion
            # date (신주상장일), which can trail the actual trading-halt/
            # resumption date the price jump lands on by 1-3 weeks. The ±3-day
            # window above misses these, and they then fall through to
            # unresolved_active_common and can get wrongly promoted to
            # externally_confirmed_internal_corruption by
            # verify_price_history_with_naver.py (Naver backward-adjusts its
            # own series across a split, so it never shows the raw jump and
            # looks like it "disagrees" with a perfectly legitimate one).
            # Widen the search to ±25 days but require the row's own
            # backward_price_factor (confirmed from DART share-count data) to
            # actually reproduce the observed ratio within 5% - a coincidental
            # date-proximity match on an unrelated action is not evidence.
            wide = conn.execute(
                """SELECT event_type,adjustment_status,evidence_report_name,backward_price_factor,event_date
                   FROM corporate_action_events
                   WHERE stock_code=? AND adjustment_status='factor_confirmed' AND backward_price_factor IS NOT NULL
                   AND event_date BETWEEN ? AND ?""",
                (code, (d-timedelta(days=25)).date().isoformat(), (d+timedelta(days=25)).date().isoformat()),
            ).fetchall()
            best = None
            for w in wide:
                bpf = w["backward_price_factor"]
                if not bpf:
                    continue
                for candidate in (bpf, 1.0 / bpf):
                    if abs(candidate - ratio) / max(abs(ratio), 0.01) <= 0.05:
                        gap = abs((datetime.strptime(str(w["event_date"])[:10], "%Y-%m-%d") - d).days)
                        if best is None or gap < best[0]:
                            best = (gap, w)
                        break
            if best is not None:
                action = best[1]
                action_date_gap = best[0]
            else:
                action_date_gap = None
        else:
            action_date_gap = 0

        if not (code.isdigit() and len(code) == 6):
            classification, usable = "non_equity_symbol", 0
            evidence = "Index/macro symbol mixed into price_history"
        elif row["quality_status"] == 'coverage_gap' and (gap_review := conn.execute(
                "SELECT reason,evidence FROM price_coverage_gap_reviewed WHERE stock_code=? AND event_date=? AND previous_date=?",
                (code, event_date, row["previous_date"])).fetchone()):
            # 2026-09-24: a gap already investigated (suspension/delisting/no source anywhere) is recorded in
            # price_coverage_gap_reviewed so it is not re-queued for backfill on every audit. Still not return-usable.
            classification, usable = "coverage_gap_reviewed", 0
            evidence = f"{gap_review[0]}: {gap_review[1]}"
        elif row["quality_status"] in ('coverage_gap','invalid_ohlcv','invalid_previous_price','quarantined_basis'):
            classification, usable = row["quality_status"], 0
            evidence = "Structural price safety check; quote agreement cannot override"
        elif action and action["adjustment_status"] == "factor_confirmed":
            classification, usable = "confirmed_corporate_action", 0
            evidence = f"Confirmed normalized event: {action['event_type']}"
            if action_date_gap:
                evidence += f" (matched via ratio, {action_date_gap}d from recorded event_date)"
        elif action:
            # A matched corporate_action_events row that isn't factor_confirmed yet
            # (e.g. adjustment_status='review_required') is still evidence of a
            # capital action, not proof there wasn't one. Falling through to a raw
            # source agreement here previously let an unconfirmed rights issue etc.
            # get marked return_usable=1 just because two providers reproduced the
            # same pre-confirmation jump (see 011080 2026-05-07 case).
            classification, usable = "corporate_action_pending_confirmation", 0
            evidence = f"Unconfirmed matched event ({action['adjustment_status']}): {action['event_type']}"
        elif disclosure:
            classification, usable = "corporate_action_or_delisting_nearby", 0
            evidence = f"Nearby disclosure: {disclosure['report_nm']}"
        elif ratio is not None and (share_ev := share_count_evidence(code, event_date, ratio)):
            # 2026-09-24: KRX-sourced (marcap) shares outstanding moved by ~1/price_ratio around
            # the jump, so market cap is continuous - a split/merge/capital change without a
            # factor_confirmed row. Still return_usable=0 and NOT counted as confirmed_corporate_action
            # (allow_confirmed_corporate_actions gates require the factor-confirmed evidence).
            classification, usable = "corporate_action_share_count_evidence", 0
            evidence = (f"marcap shares {share_ev['shares_before']:.0f}({share_ev['before_date']}) -> "
                        f"{share_ev['shares_after']:.0f}({share_ev['after_date']}), x{share_ev['share_ratio']:.4f} "
                        f"vs price ratio {ratio:.4f}")
        elif raw_ratio is not None and ratio is not None and abs(raw_ratio-ratio)/max(abs(ratio), 0.01) <= 0.005:
            classification, usable = "raw_source_confirmed_jump_review", 0
            evidence = f"Public raw series confirms ratio {raw_ratio:.4f}"
        elif raw_ratio is not None and not outside_band(raw_prev, raw_cur, event_date):
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
        """INSERT INTO price_jump_audit
           (stock_code,event_date,previous_date,previous_close,event_close,price_ratio,
            public_previous_close,public_event_close,public_price_ratio,classification,return_usable,
            matched_event_type,matched_report_name,evidence,audited_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    conn = connect_stock_db(timeout=1800)
    try:
        print(json.dumps(run(conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()
