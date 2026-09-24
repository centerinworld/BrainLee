#!/usr/bin/env python3
"""
FDR/pykrx 보조 소스 → 국내주가(price_history) 원천 대조 + 결측 복구.

권위 원천(authority) 결정:
  - Naver 일봉·pykrx get_market_ohlcv·FDR DataReader 는 상호 일치(원주가, 정산 종가).
  - FDR StockListing의 Close 필드는 이들과 불일치(예: 삼성전자 9/18 261,000 vs 정산 260,000)
    → 종목코드 목록(universe)용으로만 사용, 가격은 절대 사용하지 않는다.

안전장치:
  - 기본 DRY-RUN. --apply 없이는 쓰기 없음.
  - 기존 close>0 행은 절대 덮어쓰지 않음(결측 격리 + ON CONFLICT DO NOTHING).
  - 조정기준 대조: FDR DataReader(수정) vs pykrx(원) 가 샘플에서 불일치(>0.5%)하면 그 종목은 skip.
  - 원천 close<=0 또는 비거래일은 skip.
  - 수신 실패 시 해당 종목 skip → 기존 값 자연 보존.

provenance:
  - price_history.created_at 에 수집시각(ISO) 기록.
  - data/price_backfill_provenance_<ts>.jsonl 에 종목·날짜·원천·fdr/pykrx값·일치여부 기록.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
from db_compat import connect_primary_db

try:
    from trading_calendar import is_trading_day
except Exception:
    def is_trading_day(d, market="KR"):
        return d.weekday() < 5

MAX_AGREEMENT_PCT = 0.005
SAMPLE_N = 40
ROOT = Path(__file__).resolve().parent.parent


def _alarm(s):
    signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError(f"timeout {s}s")))
    signal.alarm(s)


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _d8(d: str) -> str:
    return d.replace("-", "")


def fetch_universe():
    """FDR StockListing 으로 종목코드 universe만 수집 (가격은 사용 안 함)."""
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


def fetch_fdr_reader(code, start, end):
    try:
        df = fdr.DataReader(code, start, end)
        if df is None or df.empty:
            return None
        out = {}
        for dt, row in df.iterrows():
            out[str(dt)[:10].replace("-", "")] = {
                "open": _f(row.get("Open")), "high": _f(row.get("High")),
                "low": _f(row.get("Low")), "close": _f(row.get("Close")),
                "volume": _f(row.get("Volume")),
            }
        return out
    except Exception:
        return None


def fetch_pykrx_ohlcv(code, start, end):
    try:
        from pykrx import stock as pst
        df = pst.get_market_ohlcv(start, end, code)
        if df is None or df.empty:
            return None
        out = {}
        for dt, row in df.iterrows():
            out[str(dt)[:10].replace("-", "")] = {
                "open": _f(row.get("시가")), "high": _f(row.get("고가")),
                "low": _f(row.get("저가")), "close": _f(row.get("종가")),
                "volume": _f(row.get("거래량")),
            }
        return out
    except Exception:
        return None


def cross_validate(conn, latest: str) -> dict:
    """DB(보유) vs FDR DataReader vs pykrx 3자 대조 (read-only)."""
    universe = fetch_universe()
    cur = conn.cursor()
    cur.execute(
        "SELECT stock_code, close FROM price_history WHERE date=? AND close>0 "
        "AND stock_code ~ '^[0-9]{6}$'",
        (latest,),
    )
    db_latest = {r[0]: float(r[1]) for r in cur.fetchall()}
    present = sorted(set(universe) & set(db_latest))
    missing = sorted(set(universe) - set(db_latest))

    sample = present[:SAMPLE_N]
    corrupt = []      # DB vs FDR(권위) 불일치 (기존값 오염 추정)
    adj_mismatch = []  # FDR(수정) vs pykrx(원) 불일치 (조정기준 모호)
    n_checked = 0
    for c in sample:
        fdr_d = fetch_fdr_reader(c, _d8(latest), _d8(latest))
        px_d = fetch_pykrx_ohlcv(c, _d8(latest), _d8(latest))
        fdr_c = (fdr_d or {}).get(_d8(latest), {}).get("close")
        px_c = (px_d or {}).get(_d8(latest), {}).get("close")
        if fdr_c and fdr_c > 0:
            n_checked += 1
            if c in db_latest:
                diff = abs(fdr_c - db_latest[c]) / fdr_c
                if diff > 0.001:
                    corrupt.append((c, db_latest[c], fdr_c, round(diff * 100, 2)))
            if px_c and px_c > 0:
                d2 = abs(fdr_c - px_c) / fdr_c
                if d2 > MAX_AGREEMENT_PCT:
                    adj_mismatch.append((c, fdr_c, px_c, round(d2 * 100, 2)))

    return {
        "universe_count": len(universe),
        "db_present_count": len(present),
        "missing_count": len(missing),
        "sample_checked": n_checked,
        "db_corruption_count": len(corrupt),
        "db_corruption_samples": corrupt[:15],
        "adjustment_basis_mismatch_count": len(adj_mismatch),
        "adjustment_basis_mismatch_samples": adj_mismatch[:10],
    }


def build_backfill_plan(conn, target_dates) -> list:
    universe = fetch_universe()
    cur = conn.cursor()
    plan = []
    for d in target_dates:
        cur.execute(
            "SELECT stock_code FROM price_history WHERE date=? AND close>0 "
            "AND stock_code ~ '^[0-9]{6}$'",
            (d,),
        )
        have = {r[0] for r in cur.fetchall()}
        for code in universe:
            if code in have:
                continue
            plan.append((code, d))
    return plan


def apply_backfill(conn, plan, start, end):
    """FDR DataReader 로 결측만 채움 + provenance JSONL."""
    prov_path = ROOT / "data" / f"price_backfill_provenance_{int(time.time())}.jsonl"
    prov_path.parent.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    inserted = skipped = 0
    fh = prov_path.open("a", encoding="utf-8")
    try:
        for code, d in plan:
            recs = fetch_fdr_reader(code, start, end)
            if not recs:
                skipped += 1
                continue
            key = _d8(d)
            rec = recs.get(key)
            if not rec or not rec["close"] or rec["close"] <= 0:
                skipped += 1
                continue
            try:
                cur.execute(
                    "INSERT INTO price_history "
                    "(stock_code, date, open, high, low, close, volume, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (stock_code, date) DO NOTHING",
                    (code, d, rec["open"], rec["high"], rec["low"],
                     rec["close"], rec["volume"], now),
                )
            except Exception as e:
                sys.stderr.write(f"skip {code} {d}: {e}\n")
                skipped += 1
                continue
            if cur.rowcount:
                inserted += 1
                fh.write(json.dumps({
                    "stock_code": code, "date": d, "source": "fdr_reader",
                    "close": rec["close"], "volume": rec["volume"],
                    "collected_at": now,
                }, ensure_ascii=False) + "\n")
            else:
                skipped += 1
    finally:
        fh.close()
    conn.commit()
    return inserted, skipped, str(prov_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-09-14")
    ap.add_argument("--end", default="2026-09-18")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sample", type=int, default=SAMPLE_N)
    args = ap.parse_args()
    _alarm(1200)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    target_dates = [
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
        if is_trading_day(start + timedelta(days=i), "KR")
    ]
    latest = target_dates[-1]
    print(f"[backfill_price_fdr] 목표 거래일={target_dates} (권위원천=FDR DataReader/pykrx/Naver)")

    conn = connect_primary_db(readonly=not args.apply, timeout=120)
    cv = cross_validate(conn, latest)
    print("[cross_validation]", json.dumps(cv, ensure_ascii=False, indent=2))

    plan = build_backfill_plan(conn, target_dates)
    by_date = {}
    for code, d in plan:
        by_date[d] = by_date.get(d, 0) + 1
    print(f"[gap] 결측 복구 계획 총 {len(plan)}건: {by_date}")

    if args.apply:
        inserted, skipped, prov = apply_backfill(conn, plan, args.start, args.end)
        print(f"[APPLY] inserted={inserted} skipped={skipped} provenance={prov}")
    else:
        print("[DRY-RUN] 쓰기 없음. --apply 로 실제 반영.")
    conn.close()


if __name__ == "__main__":
    main()
