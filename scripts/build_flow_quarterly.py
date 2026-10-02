#!/usr/bin/env python3
"""investor_flow_quarterly / foreign_flow_quarterly 를 price_history 에서 다시 집계한다.

2026-10-02: 두 테이블은 레거시 SQLite→PG 동기화(scripts/sync_tenbagger_postgres.py, 보관)로만 채워져
2026-06-11 이후 멈춰 있었다(routes/tenbagger.py 가 읽음). PG 생성 경로를 새로 둔다.
정의(기존 행과 대조해 일치 확인 — 005930 2026Q1):
  investor_flow_quarterly: ind/frgnr/orgn_net_sum = 분기 내 price_history ind/frn/inst_net_buy_amt 합(백만원), trading_days = 행 수
  foreign_flow_quarterly : frn_net_buy_amt_sum, frn_net_buy_qty_sum(=frn_net_buy 합), weight_end = 분기말 이전 마지막 kiwoom_foreign_flow.weight
사용: venv/bin/python scripts/build_flow_quarterly.py [--since 2018] (기본: 직전·현재 분기만)
"""
import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=None, help="이 연도부터 전부 재집계(기본: 직전·현재 분기)")
    a = ap.parse_args()
    today = date.today()
    if a.since:
        start = f"{a.since}-01-01"
    else:
        q = (today.month - 1) // 3
        y, pq = (today.year, q - 1) if q > 0 else (today.year - 1, 3)
        start = f"{y}-{pq*3+1:02d}-01"
    conn = connect_primary_db(timeout=900)
    conn.execute("SET statement_timeout='900s'")
    n1 = conn.execute(f"""
        INSERT INTO investor_flow_quarterly(stock_code, year, quarter, ind_net_sum, frgnr_net_sum, orgn_net_sum, trading_days, source, updated_at)
        SELECT stock_code, CAST(substr(date,1,4) AS INT), (CAST(substr(date,6,2) AS INT)-1)/3+1,
               SUM(ind_net_buy_amt), SUM(frn_net_buy_amt), SUM(inst_net_buy_amt), COUNT(*), 'price_history', to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        FROM price_history WHERE date >= '{start}' AND stock_code ~ '^[0-9]{{5}}[0-9A-Z]$'
        GROUP BY 1,2,3
        ON CONFLICT (stock_code, year, quarter) DO UPDATE SET ind_net_sum=excluded.ind_net_sum, frgnr_net_sum=excluded.frgnr_net_sum,
          orgn_net_sum=excluded.orgn_net_sum, trading_days=excluded.trading_days, source=excluded.source, updated_at=excluded.updated_at""").rowcount
    n2 = conn.execute(f"""
        INSERT INTO foreign_flow_quarterly(stock_code, year, quarter, frn_net_buy_amt_sum, frn_net_buy_qty_sum, trading_days, weight_end, source, updated_at)
        SELECT p.stock_code, p.y, p.q, p.amt, p.qty, p.n,
               (SELECT w.weight FROM kiwoom_foreign_flow w WHERE w.stock_code=p.stock_code
                  AND w.dt <= to_char(make_date(p.y, p.q*3, 1) + interval '1 month' - interval '1 day', 'YYYYMMDD') ORDER BY w.dt DESC LIMIT 1),
               'price_history', to_char(now(),'YYYY-MM-DD HH24:MI:SS')
        FROM (SELECT stock_code, CAST(substr(date,1,4) AS INT) y, (CAST(substr(date,6,2) AS INT)-1)/3+1 q,
                     SUM(frn_net_buy_amt) amt, SUM(frn_net_buy) qty, COUNT(*) n
              FROM price_history WHERE date >= '{start}' AND stock_code ~ '^[0-9]{{5}}[0-9A-Z]$' GROUP BY 1,2,3) p
        ON CONFLICT (stock_code, year, quarter) DO UPDATE SET frn_net_buy_amt_sum=excluded.frn_net_buy_amt_sum, frn_net_buy_qty_sum=excluded.frn_net_buy_qty_sum,
          trading_days=excluded.trading_days, weight_end=excluded.weight_end, source=excluded.source, updated_at=excluded.updated_at""").rowcount
    conn.commit()
    print(f"investor_flow_quarterly {n1}행, foreign_flow_quarterly {n2}행 갱신 (since {start})")


if __name__ == "__main__":
    main()
