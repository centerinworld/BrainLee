"""
collect_kis_supply_history.py — KIS API 전종목 수급 누락분 보완

KIS FHKST01010900: 1회 호출 시 최근 30거래일치 기관/외국인/개인 순매수 반환.
  ※ 날짜 범위 지정 불가 — 항상 오늘 기준 최근 30일만 제공.
  ※ 5년치 과거 데이터는 KIS API로 제공되지 않음.

동작:
  - price_history에 이미 OHLCV가 있는 날짜 중
    inst_net_buy / frn_net_buy 가 NULL 또는 0인 행만 업데이트 (기존 데이터 불변).
  - 전종목(stock_universe 기준, 시총 내림차순) 순차 처리.
  - 매일 장 마감 후 실행하면 최근 30일치를 지속 유지.

사용법:
  python3 collect_kis_supply_history.py             # 전체 universe
  python3 collect_kis_supply_history.py --limit 100 # 앞 100개만 (테스트)
  python3 collect_kis_supply_history.py --codes 005930,000660
"""

import argparse
import sqlite3
import time
import os
import sys

DB_PATH = "/Applications/stock_dashboard/stock.db"


def get_universe_codes(conn, limit=None):
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND (stock_type IS NULL OR stock_type = '보통주')
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()
    codes = [r[0] for r in rows]
    return codes[:limit] if limit else codes


def collect_one(kis_client, conn, stock_code: str) -> int:
    """
    KIS 최근 30일 수급을 가져와서 price_history의 누락분만 채움.
    - 이미 inst_net_buy != 0 이거나 frn_net_buy != 0 인 날짜는 건드리지 않음.
    반환: 실제 업데이트된 행 수
    """
    trends = kis_client.get_investor_trends_bulk(stock_code)
    if not trends:
        return 0

    saved = 0
    for t in trends:
        d = t.get("date", "")
        if not d:
            continue

        iq = t.get("inst_net_buy", 0) or 0
        fq = t.get("frn_net_buy",  0) or 0
        dq = t.get("ind_net_buy",  0) or 0
        ia = t.get("inst_net_buy_amt", 0) or 0
        fa = t.get("frn_net_buy_amt",  0) or 0
        da = t.get("ind_net_buy_amt",  0) or 0

        if iq == 0 and fq == 0 and ia == 0 and fa == 0:
            continue  # 수급 없는 행(휴장 등) 스킵

        # 기존에 수급 데이터가 없는 날짜만 업데이트 (기존 데이터 불변)
        conn.execute("""
            UPDATE price_history
            SET inst_net_buy     = ?,
                frn_net_buy      = ?,
                ind_net_buy      = ?,
                inst_net_buy_amt = ?,
                frn_net_buy_amt  = ?,
                ind_net_buy_amt  = ?
            WHERE stock_code = ? AND date = ?
              AND (inst_net_buy IS NULL OR inst_net_buy = 0)
              AND (frn_net_buy  IS NULL OR frn_net_buy  = 0)
        """, (iq, fq, dq, ia, fa, da, stock_code, d))
        saved += conn.execute("SELECT changes()").fetchone()[0]

    return saved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="최대 종목 수 (0=전체)")
    parser.add_argument("--codes", default="", help="특정 종목: 005930,000660")
    parser.add_argument("--sleep", type=float, default=1.05, help="요청 간격(초)")
    args = parser.parse_args()

    sys.path.insert(0, "/Applications/stock_dashboard")
    os.chdir("/Applications/stock_dashboard")

    from kis_client import kis_client

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_universe_codes(conn, limit=args.limit or None)

    total = len(codes)
    print(f"수급 누락분 보완 시작: {total}종목 (약 {total * args.sleep / 60:.0f}분)")
    print("※ 기존 수급 데이터가 있는 날짜는 변경하지 않습니다.\n")

    total_saved = total_skip = total_err = 0

    for i, code in enumerate(codes):
        try:
            n = collect_one(kis_client, conn, code)
            if n > 0:
                total_saved += n
                print(f"  [{i+1}/{total}] {code} +{n}일 보완")
            else:
                total_skip += 1
        except Exception as e:
            total_err += 1
            print(f"  [{i+1}/{total}] {code} 오류: {e}")

        if (i + 1) % 50 == 0:
            conn.commit()
            if total_saved > 0 or total_err > 0:
                print(f"  ── 진행: {i+1}/{total} | 저장 {total_saved:,}건 | 오류 {total_err} ──")

        time.sleep(args.sleep)

    conn.commit()
    conn.close()
    print(f"\n완료! 저장 {total_saved:,}건 / 스킵(이미있음/없음) {total_skip} / 오류 {total_err}")


if __name__ == "__main__":
    main()
