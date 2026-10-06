#!/usr/bin/env python3
"""원주가 종가 = KRX 공식 종가 매일 확인 — 보고·알림만, 수정 금지(2026-10-07, REVIEW_PLAN §16-1 4번).

사고(FINANCIAL_STATEMENTS.md §5 실패 27): 2026-10-02 22:25 `collect_kis_ohlcv.py --provisional-days 14`가 KIS 일봉의
당일 잠정 값(장후 대체거래소 거래가 섞인 값, KIS가 이후 KRX 종가로 확정)을 price_history에 썼다. 평소엔 다음 거래일 실행이
최근 14일을 다시 받아 덮지만 10-03~05 연휴로 남아 2,115종목 종가가 KRX 공식 종가와 달랐다(평소 0건).
검사: 공식 주가(stock_price_daily — 공공데이터·KRX Open API, 같은 KRX 원천)가 있는 최근 10거래일마다 PG 종가 ≠ 공식 종가 건수.
결과: data_anomaly_daily(check_name='pg_close_vs_krx') + 0건이 아니면 텔레그램 알림(notifier.send).
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def main():
    conn = connect_primary_db(timeout=600)
    days = [r[0] for r in conn.execute("SELECT DISTINCT bas_dt FROM stock_price_daily ORDER BY bas_dt DESC LIMIT 10").fetchall()]
    res = {}
    rows = []
    for d in days:
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        n, bad = conn.execute("""SELECT COUNT(*), SUM(CASE WHEN abs(p.close - s.close_price) > 1 THEN 1 ELSE 0 END)
                                 FROM price_history p JOIN stock_price_daily s ON s.stock_code=p.stock_code AND s.bas_dt=?
                                 WHERE p.date LIKE ? AND s.close_price > 0""", (d, iso + "%")).fetchone()
        res[iso] = {"compared": int(n or 0), "mismatch": int(bad or 0)}
        if bad:
            rows.append((date.today().isoformat(), "pg_close_vs_krx", "*", int(d[:4]), 0, "-", f"{iso} PG 종가≠KRX 공식 {bad}/{n}", 0))
    conn.execute("""CREATE TABLE IF NOT EXISTS data_anomaly_daily (check_date TEXT, check_name TEXT, stock_code TEXT, year INTEGER, quarter INTEGER,
        report_type TEXT, detail TEXT, known INTEGER, PRIMARY KEY (check_date, check_name, stock_code, year, quarter, report_type))""")
    conn.execute("DELETE FROM data_anomaly_daily WHERE check_date=? AND check_name='pg_close_vs_krx'", (date.today().isoformat(),))
    # 날짜마다 1행(키 충돌 방지: stock_code 자리에 날짜)
    rows = [(r[0], r[1], r[6][:10], r[3], r[4], r[5], r[6], r[7]) for r in rows]
    if rows:
        conn.executemany("INSERT INTO data_anomaly_daily VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    bad_days = {k: v for k, v in res.items() if v["mismatch"]}
    print(datetime.now().isoformat(timespec="seconds"), "PG 종가 vs KRX 공식:", json.dumps(res, ensure_ascii=False))
    if bad_days:
        try:
            import notifier
            notifier.send("⚠ 원주가 종가가 KRX 공식 종가와 다름(평소 0건): " + ", ".join(f"{k} {v['mismatch']}/{v['compared']}" for k, v in bad_days.items())
                          + " — collect_kis_ohlcv 잠정 값 의심, FINANCIAL §5 실패 27", key=f"pg_vs_krx_{date.today().isoformat()}")
        except Exception as e:
            print("알림 실패:", e)


if __name__ == "__main__":
    main()
