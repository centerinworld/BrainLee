#!/usr/bin/env python3
"""
FDR/pykrx 권위원천 국내주가 복구 (validated repair workflow 준수).

권위원천(authority): Naver 일봉 = pykrx get_market_ohlcv = FDR DataReader (3자 100% 일치).
FDR StockListing의 Close는 신뢰불가 → universe(종목코드)용으로만 사용.

두 작업:
  ① 정정(correction): 보유 행 중 정산종가와 >0.1% 불일치하는 오염 행을 권위원천으로 덮어쓰기.
     → price_history_fix_backup 에 old/new 백업 + data_fix_log(source='FDR_verified').
  ② backfill(결측): close<=0/행 없음 종목·날짜를 권위원천으로 삽입.
     → gate_gap_fill_row 로 경계(가격제한폭) 검증 후 삽입 + data_fix_log(source='FDR_backfill').

게이트/안전장치 (Checker 승인 조건):
  (a) 백업/스냅샷: price_history_fix_backup(old/new 전체) — 되돌리기 가능.
  (b) provenance: data_fix_log.source = 'FDR_verified' / 'FDR_backfill', fixed_at 기록.
  (c) 사후 재검증: 보유 행 재확산(재-diff) 0건 확인.
  (d) 루트원인 정합: 9/14~9/18 창 + 정산종가 이탈로 식별 (created_at은 전 행 NULL이라 판별자 아님).

쓰기 가드: 기존 트리거 guard_historical_price_write 를 우회하지 않고, repair 스크립트와
동일하게 백업·검증 후 set_config('app.price_basis_checked','1') 로 통과시킴.

기본 DRY-RUN. --apply 없이는 쓰기 없음.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, invalid_ohlcv, gate_gap_fill_row  # noqa: E402

try:
    from trading_calendar import is_trading_day
except Exception:
    def is_trading_day(d, market="KR"):
        return d.weekday() < 5

CORRECTION_TOL = 0.001  # 0.1%
CHECKPOINT = ROOT / "data" / "price_recover_checkpoint.json"

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


def _alarm(s):
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError(f"timeout {s}s")))
    signal.alarm(s)


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_universe():
    codes = set()
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            df = fdr.StockListing(mkt)
        except Exception:
            continue
        for _, r in df.iterrows():
            code = str(r.get("Code") or r.get("ISU_CD") or "")
            if code.isdigit():
                codes.add(code.zfill(6))
    return sorted(codes)


def fetch_fdr(code, start, end):
    """FDR DataReader(수정) 일별 OHLCV. 실패 시 None."""
    try:
        df = fdr.DataReader(code, start, end)
        if df is None or df.empty:
            return None
        out = {}
        for dt, row in df.iterrows():
            key = str(dt)[:10]
            rec = {
                "open": _f(row.get("Open")), "high": _f(row.get("High")),
                "low": _f(row.get("Low")), "close": _f(row.get("Close")),
                "volume": _f(row.get("Volume")),
            }
            out[key] = rec
        return out
    except Exception:
        return None


def load_checkpoint():
    if CHECKPOINT.exists():
        try:
            return set(json.loads(CHECKPOINT.read_text()))
        except Exception:
            return set()
    return set()


def save_checkpoint(codes):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(sorted(codes)))


def collect_work(conn, target_dates, done_codes, limit=None):
    """반환: (corrections, backfills, skipped_counts) — 모두 read-only 검증."""
    universe = fetch_universe()
    if limit:
        universe = universe[:limit]
    cur = conn.cursor()
    # DB 보유 행 (창 전체) 미리 로드
    cur.execute(
        "SELECT stock_code, date, open, high, low, close, volume FROM price_history "
        "WHERE date>=? AND date<=? AND close>0 AND stock_code ~ '^[0-9]{6}$'",
        (target_dates[0], target_dates[-1]),
    )
    db = {}
    for code, d, o, h, l, c, v in cur.fetchall():
        db[(code, d)] = (o, h, l, c, v)

    corrections, backfills = [], []
    skipped = {"no_fdr": 0, "invalid_ohlcv": 0, "quarantined": 0}
    n = 0
    for code in universe:
        n += 1
        if n % 200 == 0:
            print(f"[progress] {n}/{len(universe)} codes, corrections={len(corrections)} "
                  f"backfills={len(backfills)}", file=sys.stderr)
        if code in done_codes:
            continue
        recs = fetch_fdr(code, target_dates[0], target_dates[-1])
        if not recs:
            skipped["no_fdr"] += 1
            continue
        for d in target_dates:
            rec = recs.get(d)
            if not rec or not rec["close"] or rec["close"] <= 0:
                continue
            new = (rec["open"], rec["high"], rec["low"], rec["close"], rec["volume"])
            if any(x is None for x in new):
                continue
            if invalid_ohlcv(*new):
                skipped["invalid_ohlcv"] += 1
                continue
            if (code, d) in db:
                old = db[(code, d)]
                if old[3] and abs(float(new[3]) / float(old[3]) - 1) > CORRECTION_TOL:
                    corrections.append((code, d, old, new))
            else:
                # 결측: 경계 검증(가격제한폭) 후 backfill
                if gate_gap_fill_row(conn, code, d, new, "fdr_backfill"):
                    backfills.append((code, d, new))
                else:
                    skipped["quarantined"] += 1
    return corrections, backfills, skipped


def apply(conn, run_id, corrections, backfills):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("SELECT set_config('app.price_basis_checked','1',true)")

    backup_rows, update_rows, insert_rows, log_rows = [], [], [], []
    # 정정: live 재확인(변경됐으면 skip)
    for code, d, old, new in corrections:
        cur = conn.execute(
            "SELECT open,high,low,close,volume FROM price_history "
            "WHERE stock_code=? AND date=?", (code, d),
        ).fetchone()
        if not cur:
            continue
        curv = tuple(float(x) for x in cur)
        if all(abs(a - b) <= 1e-3 for a, b in zip(curv, old)):
            backup_rows.append((run_id, code, d, *old, *new,
                                "FDR_verified correction (Naver=pykrx=FDR authority)", now))
            update_rows.append((*new, code, d))
            log_rows.append((now, "price_history", "correction", 1, "FDR_verified",
                             json.dumps({"old": old}), json.dumps({"new": new}),
                             "FDR_verified", run_id))
    for code, d, new in backfills:
        backup_rows.append((run_id, code, d, None, None, None, None, None, *new,
                            "FDR_backfill gap fill", now))
        insert_rows.append((code, d, *new, now))
        log_rows.append((now, "price_history", "backfill", 1, "FDR_backfill",
                         json.dumps({"old": None}), json.dumps({"new": new}),
                         "FDR_backfill", run_id))

    conn.executemany(
        "INSERT INTO price_history_fix_backup "
        "(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,"
        " new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        backup_rows,
    )
    conn.executemany(
        "UPDATE price_history SET open=?,high=?,low=?,close=?,volume=? "
        "WHERE stock_code=? AND date=?",
        update_rows,
    )
    conn.executemany(
        "INSERT INTO price_history "
        "(stock_code,date,open,high,low,close,volume,created_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(stock_code,date) DO NOTHING",
        insert_rows,
    )
    conn.executemany(
        "INSERT INTO data_fix_log "
        "(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,"
        " new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)",
        log_rows,
    )
    conn.commit()
    return {"backup_rows": len(backup_rows), "corrected": len(update_rows),
            "backfilled": len(insert_rows), "logged": len(log_rows)}


def post_verify(conn, target_dates, sample_n=40):
    """재-diff: 정정·backfill 후 보유 행이 권위원천과 일치하는지 확인."""
    universe = fetch_universe()
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code, date, close FROM price_history WHERE date=? AND close>0 "
        "AND stock_code ~ '^[0-9]{6}$'", (target_dates[-1],),
    )
    present = {r[0]: r[2] for r in cur.fetchall()}
    sample = sorted(set(universe) & set(present))[:sample_n]
    mismatch = []
    for code in sample:
        recs = fetch_fdr(code, target_dates[-1], target_dates[-1])
        fdr_c = (recs or {}).get(target_dates[-1], {}).get("close")
        if fdr_c and fdr_c > 0 and code in present:
            diff = abs(fdr_c - present[code]) / fdr_c
            if diff > CORRECTION_TOL:
                mismatch.append((code, present[code], fdr_c, round(diff * 100, 2)))
    return {"coverage": len(present), "sample_checked": len(sample),
            "remaining_mismatch": len(mismatch), "samples": mismatch[:15]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-14")
    ap.add_argument("--end", default="2026-09-18")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--skip-checkpoint", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="universe 상위 N개만 (테스트)")
    args = ap.parse_args()
    _alarm(3600)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    target_dates = [
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
        if is_trading_day(start + timedelta(days=i), "KR")
    ]
    print(f"[recover_price_fdr] 창={target_dates}")

    conn = connect_primary_db(timeout=120)
    native_script(conn, BACKUP_DDL)

    done_codes = set() if args.skip_checkpoint else load_checkpoint()
    corrections, backfills, skipped = collect_work(conn, target_dates, done_codes, args.limit)
    print(f"[collect] corrections={len(corrections)} backfills={len(backfills)} skipped={skipped}")

    if args.apply:
        run_id = f"fdr_authority_recover_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        r = apply(conn, run_id, corrections, backfills)
        print(f"[apply] run_id={run_id} {r}")
        applied_codes = {c for c, _, _ in corrections} | {c for c, _, _ in backfills}
        save_checkpoint(done_codes | applied_codes)
    else:
        print("[DRY-RUN] 쓰기 없음. --apply 로 실제 반영.")

    pv = post_verify(conn, target_dates)
    print("[post_verify]", json.dumps(pv, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
