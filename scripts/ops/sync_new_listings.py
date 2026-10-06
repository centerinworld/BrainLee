#!/usr/bin/env python3
"""신규 상장 종목 매일 편입 + 상장일부터 가격 채우기(2026-10-05, 사용자 지적 "추가 상장 종목이 누락될 것 같다").

발견: 종목 마스터(stock_universe) 신규 편입은 매월 1일 03:00 `update_from_krx`뿐이라 상장 후 최대 한 달 누락
(468670 브릴스 10/01 상장 → 11/01 편입 예정). 편입이 늦으면 price_history 도 편입 이후부터만 쌓여 상장 초기 구간이 빈다
(282620 기도산업 8/21 상장·가격 9/7부터, 487400 케이앤에스아이앤씨 8/13 상장·가격 9/7부터). 공식 시세 테이블도 8/21 이전 공백.

1) 편입: 공식 시세(stock_price_daily) 최신일의 코스피·코스닥 종목 중 마스터에 없는 것 → stock_universe 삽입
   (코넥스 제외 — 2026-10-05 사용자 결정). 상장일 = 공식 시세 첫 날(공백이 있으면 KIS로 보정 가능, 아래 2에서 확인).
2) 가격: 상장일이 최근 180일 이내인 종목에서 price_history 첫 날 이전 구간을 KIS 원주가(FID_ORG_ADJ_PRC=1)로 받아 삽입.
   공식 시세가 있는 날은 KIS 종가와 1원 이내로 맞을 때만, OHLC 정합 확인. 기존 행은 건드리지 않는다(삽입만).
기록: price_history_fix_backup(old NULL = 삽입) + data_fix_log(run_id). 기본 dry-run, --apply.
"""
import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def _d(s):
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]


async def _kis_rows(code, d0, d1):
    from collectors.kis_collector import KISCollector
    from kis_client import KISClient
    col = KISCollector(kis_client=KISClient())
    rows = await col.fetch_period_ohlcv(code, d0.replace("-", ""), d1.replace("-", ""), adj_price="1")
    return {str(r["date"])[:10]: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=180)
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    latest = conn.execute("SELECT MAX(bas_dt) FROM stock_price_daily").fetchone()[0]
    now = datetime.now().isoformat(timespec="seconds")
    # 1) 편입
    new = [tuple(r) for r in conn.execute(
        """SELECT s.stock_code, s.stock_name, s.market, s.open_price, s.high_price, s.low_price, s.close_price, s.volume, s.trade_amt, s.shares,
                  (SELECT MIN(bas_dt) FROM stock_price_daily x WHERE x.stock_code=s.stock_code)
           FROM stock_price_daily s WHERE s.bas_dt=? AND s.market IN ('KOSPI','KOSDAQ')
             AND s.stock_code NOT IN (SELECT stock_code FROM stock_universe)""", (latest,)).fetchall()]
    print(f"공식 최신 {latest}: 마스터 미편입 {len(new)}종목", [(r[0], r[1]) for r in new])
    # 2) 가격 공백(상장일 < price_history 첫 날)
    since = (date.today() - timedelta(days=a.days)).isoformat()
    cand = [tuple(r) for r in conn.execute(
        """SELECT u.stock_code, u.listed_date, (SELECT MIN(date) FROM price_history p WHERE p.stock_code=u.stock_code)
           FROM stock_universe u WHERE u.listed_date >= ? AND u.market IN ('KOSPI','KOSDAQ')""", (since,)).fetchall()]
    cand += [(r[0], _d(r[10]), None) for r in new]
    gaps = [(c, str(l)[:10], (str(f)[:10] if f else None)) for c, l, f in cand if l and (f is None or str(l)[:10] < str(f)[:10])]
    print(f"최근 {a.days}일 상장 중 가격 공백 {len(gaps)}종목", gaps)
    off = {}
    plan = []
    for code, ld, first in gaps:
        # 2026-10-07(REVIEW_PLAN §19-2 3번): 당일 봉은 잠정(게이트를 안 거쳐 잠정 표시도 없음) → 어제까지만. 당일은 KIS 일일 수집이 게이트로 씀
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        end = min((datetime.strptime(first, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d") if first else yesterday, yesterday)
        if end < ld:
            continue
        kis = asyncio.run(_kis_rows(code, ld, end))
        for d, r in sorted(kis.items()):
            if not (ld <= d <= end):
                continue
            o, h, l, c, v = (float(r[k]) for k in ("open", "high", "low", "close", "volume"))
            if not (c > 0 and l <= min(o, c) and h >= max(o, c)):
                continue
            od = conn.execute("SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt=?", (code, d.replace("-", ""))).fetchone()
            if od and abs(float(od[0]) - c) > 1:
                off[(code, d)] = (float(od[0]), c)
                continue
            plan.append((code, d, o, h, l, c, v))
    print(f"삽입 예정 가격 {len(plan)}행, 공식≠KIS 보류 {len(off)}행")
    if not a.apply or (not new and not plan):
        return
    run_id = f"new_listing_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    for code, name, mkt, o, h, l, c, v, ta, sh, first in new:
        conn.execute("""INSERT INTO stock_universe(stock_code, stock_name, market, base_date, close, open, high, low, volume, trading_value,
                        market_cap, shares_issued, listed_date, source, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (code, name, mkt, _d(latest), c, o, h, l, v, ta, (c * sh / 1e8) if c and sh else None, sh, _d(first),
                      "krx_official_new_listing", now, now))
    if plan:
        conn.execute("SELECT set_config('app.price_basis_checked','1', true)")
        conn.executemany("INSERT INTO price_history(stock_code, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)", plan)
        conn.executemany("""INSERT INTO price_history_fix_backup(run_id,stock_code,date,new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         [(run_id, code, d, o, h, l, c, v, "신규 상장 초기 구간 KIS 원주가 삽입(sync_new_listings)", now) for code, d, o, h, l, c, v in plan])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "stock_universe+price_history", "신규 상장 편입·상장 초기 가격", len(new) + len(plan),
                  "공식 시세 최신일 미편입 코스피·코스닥 편입, 상장일~가격 첫 날 KIS 원주가(공식과 1원 이내)", json.dumps({"편입": len(new), "가격": len(plan), "보류": len(off)}, ensure_ascii=False),
                  "KRX 공식 시세 + KIS 원주가", "scripts/ops/sync_new_listings.py", run_id))
    conn.commit()
    print("적용 완료", run_id, "편입", len(new), "가격", len(plan))


if __name__ == "__main__":
    main()
