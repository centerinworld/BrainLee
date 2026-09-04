"""
fill_supply_amt_from_qty.py — inst_net_buy_amt / frn_net_buy_amt 공백 채우기

수량(qty) × 종가(close)로 금액(amt) 근사치를 계산해
inst_net_buy_amt / frn_net_buy_amt 가 0이거나 NULL인 행을 채운다.

단위: 백만원 (÷1,000,000)
  amt = qty × close / 1_000_000

실행:
    python3 scripts/fill_supply_amt_from_qty.py             # 전체
    python3 scripts/fill_supply_amt_from_qty.py --year 2021 # 특정 연도만
    python3 scripts/fill_supply_amt_from_qty.py --dry-run   # 몇 건인지만 확인
"""

import argparse
import sqlite3
import time
from datetime import datetime

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"

def run(year_filter=None, dry_run=False):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")

    year_cond = f"AND substr(date,1,4)='{year_filter}'" if year_filter else ""

    # 대상: close>0, qty 있음, amt가 0 또는 NULL
    count = conn.execute(f"""
        SELECT COUNT(*) FROM price_history
        WHERE close > 0
          AND (inst_net_buy != 0 OR frn_net_buy != 0)
          AND (inst_net_buy_amt IS NULL OR inst_net_buy_amt = 0)
          AND (frn_net_buy_amt  IS NULL OR frn_net_buy_amt  = 0)
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          {year_cond}
    """).fetchone()[0]

    print(f"대상: {count:,}행")
    if dry_run:
        # 연도별 분포 확인
        rows = conn.execute(f"""
            SELECT substr(date,1,4) yr, COUNT(*) cnt
            FROM price_history
            WHERE close > 0
              AND (inst_net_buy != 0 OR frn_net_buy != 0)
              AND (inst_net_buy_amt IS NULL OR inst_net_buy_amt = 0)
              AND (frn_net_buy_amt  IS NULL OR frn_net_buy_amt  = 0)
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              {year_cond}
            GROUP BY yr ORDER BY yr
        """).fetchall()
        for r in rows:
            print(f"  {r[0]}: {r[1]:,}행")
        conn.close()
        return

    if count == 0:
        print("채울 대상 없음")
        conn.close()
        return

    start = time.time()
    updated = 0
    batch = 5000

    # 배치 UPDATE
    while True:
        cur = conn.execute(f"""
            SELECT rowid, inst_net_buy, frn_net_buy, ind_net_buy, close
            FROM price_history
            WHERE close > 0
              AND (inst_net_buy != 0 OR frn_net_buy != 0)
              AND (inst_net_buy_amt IS NULL OR inst_net_buy_amt = 0)
              AND (frn_net_buy_amt  IS NULL OR frn_net_buy_amt  = 0)
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              {year_cond}
            LIMIT {batch}
        """).fetchall()

        if not cur:
            break

        for rowid, inst_qty, frn_qty, ind_qty, close in cur:
            inst_amt = round((inst_qty or 0) * close / 1_000_000, 4)
            frn_amt  = round((frn_qty  or 0) * close / 1_000_000, 4)
            ind_amt  = round((ind_qty  or 0) * close / 1_000_000, 4)
            conn.execute("""
                UPDATE price_history
                SET inst_net_buy_amt = ?,
                    frn_net_buy_amt  = ?,
                    ind_net_buy_amt  = ?
                WHERE rowid = ?
            """, (inst_amt, frn_amt, ind_amt, rowid))

        conn.commit()
        updated += len(cur)
        elapsed = time.time() - start
        rate = updated / elapsed if elapsed > 0 else 0
        remaining = (count - updated) / rate if rate > 0 else 0
        print(f"\r  {updated:,}/{count:,}행 ({updated/count*100:.1f}%) "
              f"| {rate:.0f}행/초 | 남은시간 {remaining/60:.1f}분   ", end="", flush=True)

    print(f"\n완료: {updated:,}행 갱신 ({time.time()-start:.1f}초)")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year",    type=str, help="특정 연도만 (예: 2021)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(year_filter=args.year, dry_run=args.dry_run)
