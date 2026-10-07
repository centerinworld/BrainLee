#!/usr/bin/env python3
"""N1 ③(Stock_Strategy §24-4): 2015~2019 상장폐지 종목 140개의 `price_history` 누락(2014-12-30 이후 행 없음)을 공식 표 `stock_price_daily`로 채운다.
근거(측정 2026-10-07): 140종목 모두 공식 표에 2015+ 행이 있고 마지막 행이 마스터 종료일과 1~6일 차이, 겹치는 33,093일의 종가는 100% 일치(같은 원주가 기준).
규칙: 빈 (종목, 날짜)에만 삽입, 기존 행 불변, 대상 = 마스터에 '종료만 있는' 종목 중 price_history 마지막 행이 마스터 종료일보다 10일 넘게 앞서고 그 뒤 공식 행이 있는 종목.
OHLC 정합(저가≤시·종가≤고가, 저가>0)·거래량≥0 불량 행은 삽입 안 함. 기본 dry-run, --apply 시 반영(백업 price_history_fix_backup old=NULL + data_fix_log)."""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900)
    codes = [r[0] for r in conn.execute("""
        WITH m AS (SELECT stock_code, MAX(COALESCE(effective_to,'9999-12-31')) mx, BOOL_OR(effective_to IS NULL) has_open FROM security_master_history GROUP BY 1),
             p AS (SELECT stock_code, MAX(date) lp FROM price_history WHERE close>0 GROUP BY 1)
        SELECT m.stock_code FROM m JOIN p USING (stock_code)
        WHERE NOT m.has_open AND m.mx BETWEEN '2015-01-01' AND '2019-12-31'
          AND (m.mx::date - p.lp::date) > 10
          AND EXISTS (SELECT 1 FROM stock_price_daily s WHERE s.stock_code=m.stock_code AND s.bas_dt > REPLACE(p.lp,'-',''))""").fetchall()]
    print("대상 종목", len(codes))
    rows, bad = [], 0
    for i in range(0, len(codes), 40):
        ch = codes[i:i + 40]; ph = ",".join("?" * len(ch))
        for code, d, o, h, l, c, v, ta in conn.execute(f"""
            SELECT s.stock_code, s.bas_dt, s.open_price, s.high_price, s.low_price, s.close_price, s.volume, s.trade_amt
            FROM stock_price_daily s WHERE s.stock_code IN ({ph}) AND s.bas_dt >= '20150102'
              AND NOT EXISTS (SELECT 1 FROM price_history p WHERE p.stock_code=s.stock_code AND p.date = SUBSTR(s.bas_dt,1,4)||'-'||SUBSTR(s.bas_dt,5,2)||'-'||SUBSTR(s.bas_dt,7,2))
            ORDER BY s.stock_code, s.bas_dt""", ch).fetchall():
            if not c or c <= 0:
                bad += 1; continue
            o = o or c; h = h or max(o, c); l = l or min(o, c)
            if not (l > 0 and l <= min(o, c) + 1e-9 and h >= max(o, c) - 1e-9) or (v is not None and v < 0):
                bad += 1; continue
            rows.append((code, f"{d[:4]}-{d[4:6]}-{d[6:]}", float(o), float(h), float(l), float(c), float(v or 0), float(ta) if ta is not None else None))
    print(f"삽입 후보 {len(rows):,}행, 정합 불량으로 제외 {bad}행")
    if not a.apply or not rows:
        return
    run_id = f"delisted_price_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = "상장폐지 종목 price_history 누락(2014-12-30 이후) — 공식 표 stock_price_daily로 채움(겹치는 33,093일 종가 100% 일치). fill_missing_delisted_prices_20261007"
    conn.executemany("""INSERT INTO price_history_fix_backup(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?)""",
                     [(run_id, c, d, o, h, l, cl, v, reason, now) for c, d, o, h, l, cl, v, _ in rows])
    conn.execute("SELECT set_config('app.price_basis_checked','1', true)")
    conn.executemany("""INSERT INTO price_history(stock_code,date,open,high,low,close,volume,trade_amount,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                     [(c, d, o, h, l, cl, v, ta, now) for c, d, o, h, l, cl, v, ta in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "price_history", "상장폐지 140종목 가격 누락 채움", len(rows), reason, "", f"stock_price_daily {len(rows)}행 삽입", "scripts/review/fill_missing_delisted_prices_20261007.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(rows))


if __name__ == "__main__":
    main()
