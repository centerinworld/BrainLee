#!/usr/bin/env python3
"""
EPS/BPS가 NULL 또는 0인 financial_data 레코드를 net_income/total_equity 기반으로 일괄 계산.

근거:
- financial_data.eps = 0 또는 NULL 이면서 net_income이 실제값인 경우 → net_income/shares_issued로 계산
- financial_data.bps = 0 또는 NULL 이면서 total_equity가 실제값인 경우 → total_equity/shares_issued로 계산
- 이미 값이 있는 레코드는 수정하지 않음 (fnguide 원본 우선)
"""
import sqlite3
import sys

DB_PATH = "/Applications/stock_dashboard/stock.db"

def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    shares_map = {
        r["stock_code"]: r["shares_issued"]
        for r in conn.execute(
            "SELECT stock_code, shares_issued FROM stock_universe WHERE shares_issued > 0"
        )
    }

    rows = conn.execute("""
        SELECT id, stock_code, year, quarter, is_annual,
               net_income, total_equity, eps, bps
        FROM financial_data
        WHERE (eps IS NULL OR eps = 0 OR bps IS NULL OR bps = 0)
          AND (net_income IS NOT NULL OR total_equity IS NOT NULL)
    """).fetchall()

    eps_updated = 0
    bps_updated = 0

    for r in rows:
        sid = r["id"]
        code = r["stock_code"]
        shares = shares_map.get(code)
        if not shares or shares <= 0:
            continue

        new_eps = None
        new_bps = None

        # EPS 계산 (NULL/0 이고 net_income이 실제값인 경우만)
        if (r["eps"] is None or r["eps"] == 0) and r["net_income"] not in (None, 0):
            new_eps = round(r["net_income"] / shares, 2)
            eps_updated += 1

        # BPS 계산 (NULL/0 이고 total_equity가 실제값인 경우만)
        if (r["bps"] is None or r["bps"] == 0) and r["total_equity"] not in (None, 0):
            new_bps = round(r["total_equity"] / shares, 2)
            bps_updated += 1

        if new_eps is not None and new_bps is not None:
            conn.execute(
                "UPDATE financial_data SET eps=?, bps=? WHERE id=?",
                (new_eps, new_bps, sid)
            )
        elif new_eps is not None:
            conn.execute("UPDATE financial_data SET eps=? WHERE id=?", (new_eps, sid))
        elif new_bps is not None:
            conn.execute("UPDATE financial_data SET bps=? WHERE id=?", (new_bps, sid))

    conn.commit()
    conn.close()

    print(f"완료: EPS 업데이트 {eps_updated}건, BPS 업데이트 {bps_updated}건")

if __name__ == "__main__":
    run()
