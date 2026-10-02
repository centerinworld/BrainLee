#!/usr/bin/env python3
"""
scripts/build_risk_parity_sizing.py

price_history 20일 변동성 기반 Risk Parity 포지션 사이즈 산출 → kr_risk_parity_sizing 테이블.

포지션 금액 = (자본금 × TARGET_RISK_PCT) / 일일변동성
→ [MIN_TICKET_KRW, 자본금 × MAX_POSITION_PCT] 클램핑

실행:
  python3 scripts/build_risk_parity_sizing.py           # 전 활성 종목
  python3 scripts/build_risk_parity_sizing.py --full    # stock_universe 전체
"""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

BASE_CAPITAL     = 100_000_000   # 기준 자본금 1억
TARGET_RISK_PCT  = 0.005         # 종목당 목표 리스크 0.5%
MAX_POSITION_PCT = 0.15          # 최대 포지션 15%
MIN_TICKET_KRW   = 3_000_000    # 최소 300만원
MIN_VOL          = 0.005         # 최소 변동성 0.5% (극히 낮은 경우 방어)
LOOKBACK         = 22            # 20영업일 수익률 (22행 조회)


def build(full: bool = False) -> dict:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kr_risk_parity_sizing (
            stock_code          TEXT PRIMARY KEY,
            daily_vol           DOUBLE PRECISION,
            annual_vol          DOUBLE PRECISION,
            recommended_ticket  BIGINT,
            max_ticket          BIGINT,
            min_ticket          BIGINT,
            data_points         INTEGER,
            as_of_date          TEXT,
            updated_at          TEXT
        )
    """)
    conn.commit()

    if full:
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM stock_universe WHERE stock_code ~ '^[0-9]{6}$' ORDER BY stock_code"
        ).fetchall()]
        mode = "전체"
    else:
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM peak_holding WHERE is_active=1 AND stock_code ~ '^[0-9]{6}$'"
        ).fetchall()]
        mode = "활성 포지션"

    print(f"[{mode}] 대상 종목: {len(codes)}개")
    now = datetime.now().isoformat(timespec="seconds")
    today = datetime.now().date().isoformat()

    processed = ok = no_data = 0
    for code in codes:
        rows = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",
            (code, LOOKBACK),
        ).fetchall()
        n = len(rows)
        if n < 5:
            no_data += 1
            continue

        prices = [float(r[0]) for r in rows]
        returns = [(prices[i] - prices[i+1]) / prices[i+1] for i in range(n-1)]
        daily_vol = max((sum(x**2 for x in returns) / len(returns)) ** 0.5, MIN_VOL)
        annual_vol = daily_vol * (252 ** 0.5)

        target_risk = BASE_CAPITAL * TARGET_RISK_PCT
        ticket = target_risk / daily_vol
        ticket = min(ticket, BASE_CAPITAL * MAX_POSITION_PCT)
        ticket = max(ticket, MIN_TICKET_KRW)

        conn.execute("""
            INSERT INTO kr_risk_parity_sizing
              (stock_code, daily_vol, annual_vol, recommended_ticket, max_ticket, min_ticket, data_points, as_of_date, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (stock_code) DO UPDATE SET
              daily_vol=EXCLUDED.daily_vol, annual_vol=EXCLUDED.annual_vol,
              recommended_ticket=EXCLUDED.recommended_ticket,
              data_points=EXCLUDED.data_points, as_of_date=EXCLUDED.as_of_date,
              updated_at=EXCLUDED.updated_at
        """, (
            code, round(daily_vol, 6), round(annual_vol, 4),
            int(ticket),
            int(BASE_CAPITAL * MAX_POSITION_PCT),
            int(MIN_TICKET_KRW),
            n - 1, today, now,
        ))
        ok += 1
        processed += 1
        if processed % 500 == 0:
            conn.commit()
            print(f"  {processed}/{len(codes)} 처리 중...")

    conn.commit()
    conn.close()
    result = {"mode": mode, "ok": ok, "no_data": no_data}
    print(f"완료: {result}")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()
    build(full=args.full)
