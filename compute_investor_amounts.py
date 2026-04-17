"""
compute_investor_amounts.py — 수급 수량 × 종가로 금액(백만원) 역산

price_history에서 inst_net_buy(수량) 또는 frn_net_buy(수량)이 있지만
inst_net_buy_amt / frn_net_buy_amt 가 0인 행에 대해
  amt = qty × close / 1,000,000  (백만원 단위)
으로 역산하여 채운다.

개인(ind_net_buy_amt) = -(기관+외국인) 으로 역산 (합계 0 가정)

사용법:
  python3 compute_investor_amounts.py           # 전체 역산 (약 190만 행)
  python3 compute_investor_amounts.py --days 30 # 최근 30일만
"""

import argparse
import sqlite3
import time
from datetime import datetime, timedelta

DB_PATH = "/Applications/stock_dashboard/stock.db"
BATCH   = 10_000   # 한 번에 처리할 행 수


def run(days: int | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    since_clause = ""
    params: list = []
    if days:
        since = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        since_clause = f"AND substr(date,1,10) >= '{since}'"

    # 처리 대상 행 수 확인
    total = conn.execute(f"""
        SELECT COUNT(*) FROM price_history
        WHERE close > 0
          AND (inst_net_buy != 0 OR frn_net_buy != 0)
          AND (inst_net_buy_amt = 0 AND frn_net_buy_amt = 0)
          AND stock_code NOT LIKE '%^%'
          AND stock_code NOT LIKE '%-F'
          AND stock_code NOT LIKE '%=%'
          {since_clause}
    """).fetchone()[0]

    print(f"역산 대상: {total:,}행 (batch={BATCH})")

    updated = 0
    offset  = 0
    t0      = time.time()

    while True:
        rows = conn.execute(f"""
            SELECT rowid, inst_net_buy, frn_net_buy, close
            FROM price_history
            WHERE close > 0
              AND (inst_net_buy != 0 OR frn_net_buy != 0)
              AND (inst_net_buy_amt = 0 AND frn_net_buy_amt = 0)
              AND stock_code NOT LIKE '%^%'
              AND stock_code NOT LIKE '%-F'
              AND stock_code NOT LIKE '%=%'
              {since_clause}
            LIMIT {BATCH}
        """).fetchall()

        if not rows:
            break

        upd_data = []
        for rowid, inst_qty, frn_qty, close in rows:
            inst_amt = round(inst_qty * close / 1_000_000)
            frn_amt  = round(frn_qty  * close / 1_000_000)
            ind_amt  = -round((inst_amt + frn_amt))   # 개인 = -(기관+외국인)
            upd_data.append((inst_amt, frn_amt, ind_amt, rowid))

        conn.executemany(
            "UPDATE price_history SET inst_net_buy_amt=?, frn_net_buy_amt=?, ind_net_buy_amt=? WHERE rowid=?",
            upd_data,
        )
        conn.commit()
        updated += len(rows)

        elapsed = time.time() - t0
        pct     = updated / total * 100 if total else 100
        eta_s   = (elapsed / updated * (total - updated)) if updated else 0
        print(f"  {updated:,}/{total:,} ({pct:.1f}%) | {elapsed:.0f}s elapsed | ETA {eta_s:.0f}s")

    conn.close()
    print(f"\n완료: {updated:,}행 역산 ({time.time()-t0:.1f}초)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="최근 N일만 처리 (기본=전체)")
    args = ap.parse_args()
    run(days=args.days)
