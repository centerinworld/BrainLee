#!/usr/bin/env python3
"""After-close check: sample today's price_history bars against KRX-official values (pykrx, sequential) - HANDOFF §10 P2-14.

Why: 2026-09-14~23 daily bars were stored from a PRE-closing-auction snapshot (close off by ~0.6% median on 67% of rows on 9/21) and
nothing noticed for days. This job compares a sample (top-volume + random names) with pykrx get_market_ohlcv for the trade date
and records the result in price_close_verify_log; a mismatch share above the threshold raises a Telegram alert and exits 2 so the
scheduler ledger marks the run failed. pykrx calls are SEQUENTIAL on purpose (inside a thread pool they stall).

Usage: verify_daily_close_vs_official.py [--date YYYY-MM-DD] [--sample 80] [--threshold 0.05] [--no-alert]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

DDL = """CREATE TABLE IF NOT EXISTS price_close_verify_log (
  id BIGSERIAL PRIMARY KEY, checked_at TEXT NOT NULL DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
  trade_date TEXT NOT NULL, sampled INTEGER NOT NULL, compared INTEGER NOT NULL, close_mismatch INTEGER NOT NULL,
  volume_mismatch INTEGER NOT NULL, mismatch_pct DOUBLE PRECISION NOT NULL, fetch_failed INTEGER NOT NULL,
  status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--sample", type=int, default=80)
    ap.add_argument("--threshold", type=float, default=0.05)
    ap.add_argument("--no-alert", action="store_true")
    a = ap.parse_args()

    from trading_calendar import is_kr_trading_day
    day = date.fromisoformat(a.date) if a.date else date.today()
    if not is_kr_trading_day(day):
        print(json.dumps({"skipped": "not a KR trading day", "date": day.isoformat()}))
        return 0
    iso = day.isoformat()
    conn = connect_primary_db(timeout=120)
    conn.execute(DDL)
    rows = conn.execute("SELECT stock_code,close,volume FROM price_history WHERE date=? AND close>0 AND stock_code ~ '^[0-9]{6}$' "
                        "ORDER BY volume DESC", (iso,)).fetchall()
    if len(rows) < 500:
        msg = f"only {len(rows)} price rows for {iso} - collection not finished or failed"
        conn.execute("INSERT INTO price_close_verify_log(trade_date,sampled,compared,close_mismatch,volume_mismatch,mismatch_pct,fetch_failed,status,detail) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (iso, len(rows), 0, 0, 0, 0.0, 0, "no_data", msg))
        conn.commit()
        print(json.dumps({"status": "no_data", "detail": msg}))
        return 2
    rng = random.Random(iso)
    top = rows[: a.sample // 2]
    rest = rng.sample(rows[a.sample // 2:], min(a.sample - len(top), len(rows) - len(top)))
    sample = list(top) + list(rest)

    # 2026-10-02: pykrx는 KRX 차단 이후 응답 없이 멈춰 매일 900초 타임아웃(09-25 이후 검증 0회) → KIS 일봉(KRX 공식 종가)으로 대조.
    conn.commit()
    from collect_kis_ohlcv import fetch_ohlcv, get_token
    token = get_token()
    ymd = iso.replace("-", "")
    compared = close_bad = vol_bad = failed = 0
    bad = []
    for code, close, vol in sample:
        try:
            k = [r for r in fetch_ohlcv(code, ymd, ymd, token) if r[0] == iso]
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        if not k:
            failed += 1
            continue
        compared += 1
        kc, kv = float(k[-1][4]), float(k[-1][5])
        cb = abs(kc - float(close)) > 0.5
        vb = kv > 0 and abs(kv - float(vol or 0)) / kv > 0.02
        close_bad += cb
        vol_bad += vb
        if cb and len(bad) < 10:
            bad.append({"code": code, "db_close": float(close), "official_close": kc})
    pct = close_bad / compared if compared else 1.0
    fail_share = failed / max(len(sample), 1)
    status = "ok" if (compared and pct <= a.threshold and fail_share <= 0.3) else ("fetch_failed" if fail_share > 0.3 else "mismatch")
    detail = json.dumps({"examples": bad}, ensure_ascii=False)
    conn.execute("INSERT INTO price_close_verify_log(trade_date,sampled,compared,close_mismatch,volume_mismatch,mismatch_pct,fetch_failed,status,detail) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", (iso, len(sample), compared, close_bad, vol_bad, pct, failed, status, detail))
    conn.commit()
    out = {"date": iso, "sampled": len(sample), "compared": compared, "close_mismatch": close_bad, "volume_mismatch": vol_bad,
           "mismatch_pct": round(pct, 4), "fetch_failed": failed, "status": status}
    print(json.dumps(out, ensure_ascii=False))
    if status != "ok":
        if not a.no_alert:
            try:
                import notifier
                notifier.send(f"⚠️ 종가 공식 검증 {status}: {iso} 표본 {compared}종목 중 종가 불일치 {close_bad}({pct:.1%}), 조회 실패 {failed}. 예: {bad[:3]}",
                              key=f"close_verify_{iso}")
            except Exception as exc:  # noqa: BLE001
                print("alert failed:", exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
