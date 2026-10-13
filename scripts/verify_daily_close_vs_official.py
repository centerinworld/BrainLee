#!/usr/bin/env python3
"""After-close check: compare today's price_history closes with an official reference and log the result in price_close_verify_log.

Why: 2026-09-14~23 daily bars were stored from a PRE-closing-auction snapshot (close off by ~0.6% median on 67% of rows on 9/21) and
nothing noticed for days.

Reference, in order of authority (policy: scripts/close_verify_policy.py):
  official_krx  the KRX Open API sibling table stock_price_daily (whole universe, final by construction) - used when it holds >= OFFICIAL_MIN_ROWS rows for the date
  fallback_kis  the KIS daily bar for a sample (top-volume + random names) - provisional until the settle deadline, because that endpoint revises its bar
Each row records which source judged it (`reference_kind`), so the contract can tell a final disagreement from a provisional one.

Schema: this script performs NO schema change. The table and the `reference_kind` column come from scripts/migrations/close_verify_0001_reference_kind.py.
Without the table it stops with `schema_not_migrated` (exit 3); without the column it still logs the run, just without the source.

Usage: verify_daily_close_vs_official.py [--date YYYY-MM-DD] [--sample 80] [--threshold 0.05] [--no-alert]
Exit: 0 ok / still provisional, 2 mismatch / fetch failed / no data, 3 schema not migrated.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from db_compat import connect_primary_db  # noqa: E402
import close_verify_policy as policy  # noqa: E402

LOG_TABLE = "price_close_verify_log"
LOG_COLUMNS = ("trade_date", "sampled", "compared", "close_mismatch", "volume_mismatch", "mismatch_pct", "fetch_failed", "status", "detail")


def schema_state(conn) -> dict:
    """Read-only catalog probe: does the log table exist, and does it already have the reference_kind column?"""
    table = conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name=?", (LOG_TABLE,)).fetchone()
    if not table:
        return {"table": False, "reference_kind": False}
    col = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name='reference_kind'", (LOG_TABLE,)).fetchone()
    return {"table": True, "reference_kind": bool(col)}


def insert_log_row(conn, state: dict, *, trade_date, sampled, compared, close_mismatch, volume_mismatch, mismatch_pct,
                   fetch_failed, status, detail, reference_kind) -> None:
    """Append one run to the log. Writes reference_kind only when the migrated column exists; never alters the table."""
    values = [trade_date, sampled, compared, close_mismatch, volume_mismatch, mismatch_pct, fetch_failed, status, detail]
    cols = list(LOG_COLUMNS)
    if state.get("reference_kind"):
        cols.append("reference_kind")
        values.append(reference_kind)
    conn.execute(f"INSERT INTO {LOG_TABLE}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(values))
    conn.commit()


def compare_with_official(rows, official: dict):
    """rows: (code, close, volume) from price_history; official: code -> (close, volume). Returns (compared, close_bad, vol_bad, examples)."""
    compared = close_bad = vol_bad = 0
    bad = []
    for code, close, vol in rows:
        ref = official.get(code)
        if not ref:
            continue
        compared += 1
        kc, kv = float(ref[0]), float(ref[1] or 0)
        cb = abs(kc - float(close)) > policy.CLOSE_TOLERANCE_KRW
        vb = kv > 0 and abs(kv - float(vol or 0)) / kv > 0.02
        close_bad += cb
        vol_bad += vb
        if cb and len(bad) < 10:
            bad.append({"code": code, "db_close": float(close), "official_close": kc})
    return compared, close_bad, vol_bad, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--sample", type=int, default=80)
    ap.add_argument("--threshold", type=float, default=policy.CLOSE_MISMATCH_SHARE_THRESHOLD)
    ap.add_argument("--no-alert", action="store_true")
    a = ap.parse_args()
    from trading_calendar import is_kr_trading_day
    day = date.fromisoformat(a.date) if a.date else date.today()
    if not is_kr_trading_day(day):
        print(json.dumps({"skipped": "not a KR trading day", "date": day.isoformat()}))
        return 0
    iso = day.isoformat()
    conn = connect_primary_db(timeout=120)
    state = schema_state(conn)
    if not state["table"]:
        print(json.dumps({"status": "schema_not_migrated", "detail": f"{LOG_TABLE} is missing - apply scripts/migrations/close_verify_0001_reference_kind.py first"}))
        return 3
    rows = conn.execute("SELECT stock_code,close,volume FROM price_history WHERE date=? AND close>0 AND stock_code ~ '^[0-9]{6}$' "
                        "ORDER BY volume DESC", (iso,)).fetchall()
    if len(rows) < 500:
        msg = f"only {len(rows)} price rows for {iso} - collection not finished or failed"
        insert_log_row(conn, state, trade_date=iso, sampled=len(rows), compared=0, close_mismatch=0, volume_mismatch=0, mismatch_pct=0.0,
                       fetch_failed=0, status=policy.STATUS_NO_DATA, detail=msg, reference_kind=policy.FALLBACK_KIS)
        print(json.dumps({"status": policy.STATUS_NO_DATA, "detail": msg}))
        return 2

    now = datetime.now()
    official = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT stock_code, close_price, volume FROM stock_price_daily WHERE bas_dt=? AND close_price>0", (iso.replace("-", ""),)).fetchall()}
    failed = 0
    if len(official) >= policy.OFFICIAL_MIN_ROWS:
        kind = policy.OFFICIAL_KRX
        sampled = len(rows)
        compared, close_bad, vol_bad, bad = compare_with_official(rows, official)
        fail_share = 0.0
    else:
        # No official sibling for this date yet: KIS daily bar for a sample (provisional until settle_deadline).
        kind = policy.FALLBACK_KIS
        rng = random.Random(iso)
        top = rows[: a.sample // 2]
        rest = rng.sample(rows[a.sample // 2:], min(a.sample - len(top), len(rows) - len(top)))
        sample = list(top) + list(rest)
        sampled = len(sample)
        conn.commit()
        from collect_kis_ohlcv import fetch_ohlcv, get_token
        token = get_token()
        ymd = iso.replace("-", "")
        ref = {}
        for code, close, vol in sample:
            try:
                k = [r for r in fetch_ohlcv(code, ymd, ymd, token) if r[0] == iso]
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if not k:
                failed += 1
                continue
            ref[code] = (float(k[-1][4]), float(k[-1][5]))
        compared, close_bad, vol_bad, bad = compare_with_official(sample, ref)
        fail_share = failed / max(sampled, 1)
    pct = close_bad / compared if compared else 1.0
    status = policy.classify_run_status(close_mismatch=close_bad, compared=compared, fetch_failed=failed, reference_kind=kind,
                                        trade_date_iso=iso, now=now, threshold=a.threshold)
    if fail_share > 0.3:
        status = policy.STATUS_FETCH_FAILED
    detail = json.dumps({"examples": bad, "reference_kind": kind}, ensure_ascii=False)
    insert_log_row(conn, state, trade_date=iso, sampled=sampled, compared=compared, close_mismatch=close_bad, volume_mismatch=vol_bad,
                   mismatch_pct=pct, fetch_failed=failed, status=status, detail=detail, reference_kind=kind)
    out = {"date": iso, "reference_kind": kind, "sampled": sampled, "compared": compared, "close_mismatch": close_bad, "volume_mismatch": vol_bad,
           "mismatch_pct": round(pct, 4), "fetch_failed": failed, "status": status}
    print(json.dumps(out, ensure_ascii=False))
    if status in (policy.STATUS_OK, policy.STATUS_REFERENCE_PENDING):
        return 0  # a provisional disagreement is re-checked after the settle deadline, not alerted
    if not a.no_alert:
        try:
            import notifier
            notifier.send(f"⚠️ 종가 공식 검증 {status}({kind}): {iso} 비교 {compared}종목 중 종가 불일치 {close_bad}({pct:.1%}), 조회 실패 {failed}. 예: {bad[:3]}",
                          key=f"close_verify_{iso}")
        except Exception as exc:  # noqa: BLE001
            print("alert failed:", exc, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
