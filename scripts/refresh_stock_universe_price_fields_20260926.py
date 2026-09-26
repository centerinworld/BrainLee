#!/usr/bin/env python3
"""HANDOFF §15 V4: refresh the STALE price fields of stock_universe from price_history (dry-run by default; --apply backs up first).

Root cause: the daily KRX job (scheduler `_job_krx_daily`) UPDATEs market_cap / shares_issued / sector_type of stock_universe only, so close/open/high/low/volume/
change_rate/base_date froze on 2026-09-04 (2,495 of 2,765 rows had a close >0.5% away from the latest price_history close). After a reverse split or a liquidation-trading
crash the fresh market cap no longer matched the frozen close (the 'unit inversion' the contract audit flagged for 15 stocks: ratios 5.0/10.0 = the merge ratio, 0.01-0.03 = crash).
Updated from the latest price_history row (rows only move FORWARD in time): base_date, open, high, low, close, volume, trading_value (when the row has trade_amount), change_rate
(vs the previous row). Backup table stock_universe_backup_v4_20260926. Logged to data_fix_log."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

CTE = """WITH r AS (SELECT stock_code, CAST(date AS TEXT) AS d, open, high, low, close, volume, trade_amount,
       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
     FROM price_history WHERE close > 0 AND CAST(date AS TEXT) >= CAST(CURRENT_DATE - 30 AS TEXT) AND stock_code IN (SELECT stock_code FROM stock_universe))"""


def main(apply: bool) -> int:
    conn = connect_primary_db(timeout=600, readonly=not apply)
    n_new, n_total = conn.execute(CTE + " SELECT count(*), (SELECT count(*) FROM stock_universe) FROM r a JOIN stock_universe u ON u.stock_code=a.stock_code "
                                        "WHERE a.rn=1 AND a.d > u.base_date").fetchone()
    mism = conn.execute(CTE + " SELECT count(*) FILTER (WHERE abs(u.close/a.close-1) > 0.005), count(*) FROM r a JOIN stock_universe u ON u.stock_code=a.stock_code "
                              "WHERE a.rn=1 AND u.close > 0").fetchone()
    print(f"rows to move forward={n_new}/{n_total}; close mismatch >0.5% before: {mism[0]}/{mism[1]}")
    if not apply:
        print("dry-run: 변경 없음 (--apply 로 실행)")
        return 0
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS stock_universe_backup_v4_20260926 AS SELECT * FROM stock_universe")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cur = conn.execute(CTE + """
            UPDATE stock_universe u SET base_date=a.d, open=a.open, high=a.high, low=a.low, close=a.close, volume=a.volume,
              trading_value=COALESCE(NULLIF(a.trade_amount, 0), u.trading_value),
              change_rate=CASE WHEN b.close > 0 THEN (a.close / b.close - 1) * 100 ELSE u.change_rate END,
              updated_at=CAST(now() AS TEXT)
            FROM r a LEFT JOIN r b ON b.stock_code=a.stock_code AND b.rn=2
            WHERE a.rn=1 AND u.stock_code=a.stock_code AND a.d > u.base_date""")
        updated = cur.rowcount
        nid = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM data_fix_log").fetchone()[0]
        conn.execute("INSERT INTO data_fix_log(id, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (nid, "stock_universe", "V4 stale price fields (close/open/high/low/volume/change_rate/base_date frozen at 2026-09-04)", updated,
                      "UPDATE from latest price_history row where its date > base_date", f"close mismatch >0.5%: {mism[0]}/{mism[1]}; base_date 2026-09-04",
                      "price fields = latest price_history row", "refresh_stock_universe_price_fields_20260926.py", f"universe_price_refresh_{stamp}"))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"롤백: {exc}")
        return 3
    print("적용 완료 rows:", updated)
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
