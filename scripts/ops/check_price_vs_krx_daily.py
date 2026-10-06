#!/usr/bin/env python3
"""원주가 종가 = KRX 공식 종가 매일 확인 — 보고·알림만, 수정 금지(2026-10-07, REVIEW_PLAN §16-1 4번).

사고(FINANCIAL_STATEMENTS.md §5 실패 27): 2026-10-02 22:25 `collect_kis_ohlcv.py --provisional-days 14`가 KIS 일봉의
당일 잠정 값(장후 대체거래소 거래가 섞인 값, KIS가 이후 KRX 종가로 확정)을 price_history에 썼다. 평소엔 다음 거래일 실행이
최근 14일을 다시 받아 덮지만 10-03~05 연휴로 남아 2,115종목 종가가 KRX 공식 종가와 달랐다(평소 0건).
검사: 공식 주가(stock_price_daily — 공공데이터·KRX Open API, 같은 KRX 원천)가 있는 최근 10거래일마다 PG 종가 ≠ 공식 종가 건수.
결과: data_anomaly_daily(check_name='pg_close_vs_krx') + 0건이 아니면 텔레그램 알림(notifier.send).

잠정 행 교체(2026-10-07, REVIEW_PLAN §17-2): 당일에 쓴 행은 price_integrity.gate_price_batch 가 price_provisional_rows 에
잠정으로 표시한다(KIS 당일 봉은 장후 대체거래소 거래로 계속 바뀜). KRX 공식값이 들어온 날짜의 표시 행은
값이 다르면 KRX 값으로 교체(price_history_fix_backup·data_fix_log, 거래량 0 행은 종가만 — 거래정지일 시가·고가·저가 관례)하고
표시를 지운다. 표시 없는 행의 불일치는 교체하지 않고 알림만(원인 확인 대상). --no-repair 로 보고만.
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def repair_provisional(conn, days):
    """KRX 공식값이 있는 날짜의 잠정 표시 행 → 공식값으로 교체 후 표시 해제. 반환: {날짜: (교체, 표시 해제)}"""
    out = {}
    run_id = f"provisional_to_krx_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    total = 0
    for d in days:
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        rows = [tuple(r) for r in conn.execute("""
            SELECT p.stock_code, p.open, p.high, p.low, p.close, p.volume, s.open_price, s.high_price, s.low_price, s.close_price, s.volume
            FROM price_provisional_rows m JOIN price_history p ON p.stock_code=m.stock_code AND p.date=m.date
            JOIN stock_price_daily s ON s.stock_code=m.stock_code AND s.bas_dt=?
            WHERE m.date=? AND s.close_price>0""", (d, iso)).fetchall()]
        fixed = 0
        for code, po, ph, pl, pc, pv, ko, kh, kl, kc, kv in rows:
            if kv and kv > 0:
                new = (ko or po, kh or ph, kl or pl, kc, kv)
            else:
                new = (po, ph, pl, kc, pv)
            if all(abs((a or 0) - (b or 0)) <= 0.5 for a, b in zip((po, ph, pl, pc, pv), new)):
                continue
            conn.execute("SELECT set_config('app.price_basis_checked','1', true)")
            conn.execute("""INSERT INTO price_history_fix_backup(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                            new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (run_id, code, iso, po, ph, pl, pc, pv, *new, "당일 잠정 값 → KRX 공식값(REVIEW_PLAN §17-2)", now))
            conn.execute("UPDATE price_history SET open=?, high=?, low=?, close=?, volume=? WHERE stock_code=? AND date=?", (*new, code, iso))
            fixed += 1
        cleared = conn.execute("""DELETE FROM price_provisional_rows m USING stock_price_daily s
                                  WHERE m.date=? AND s.stock_code=m.stock_code AND s.bas_dt=? AND s.close_price>0""", (iso, d)).rowcount
        out[iso] = (fixed, cleared)
        total += fixed
    if total:
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (now, "price_history", "당일 잠정 표시 행", total, "KRX 공식값 수신 → 다른 칸 교체(거래량 0 행은 종가만)",
                      json.dumps(out, ensure_ascii=False), "KRX 공식", "scripts/ops/check_price_vs_krx_daily.py", run_id))
    conn.commit()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-repair", action="store_true", help="잠정 행 교체 없이 보고만")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600)
    days = [r[0] for r in conn.execute("SELECT DISTINCT bas_dt FROM stock_price_daily ORDER BY bas_dt DESC LIMIT 10").fetchall()]
    import price_integrity
    price_integrity.ensure_schema(conn)
    conn.commit()
    if not a.no_repair:
        rep = repair_provisional(conn, days)
        print("잠정 행 → KRX 공식값 (교체, 표시 해제):", json.dumps(rep, ensure_ascii=False))
    pending = [tuple(r) for r in conn.execute("SELECT date, COUNT(*) FROM price_provisional_rows GROUP BY 1 ORDER BY 1").fetchall()]
    print("KRX 확정 대기 잠정 행:", pending)
    # 2026-10-07(REVIEW_PLAN §19-2 2번): KRX 수신이 멈추면 표시 행이 쌓이고 감사 기준에서 조용히 빠진다 → 2거래일 넘게 남으면 알림
    recent = [r[0] for r in conn.execute("SELECT DISTINCT date FROM price_history WHERE stock_code='005930' ORDER BY date DESC LIMIT 3").fetchall()]
    stale = [] if len(recent) < 3 else [(d, n) for d, n in pending if d < str(recent[-1])[:10]]
    if stale:
        print("⚠ 2거래일 넘게 남은 잠정 행:", stale)
        try:
            import notifier
            notifier.send("⚠ KRX 공식값으로 확정되지 않은 잠정 가격 행이 2거래일 넘게 남음: " + ", ".join(f"{d} {n}행" for d, n in stale)
                          + " — KRX Open API 수신(fetch_krx_openapi) 확인, REVIEW_PLAN §19-2", key=f"prov_stale_{date.today().isoformat()}")
        except Exception as e:
            print("알림 실패:", e)
    res = {}
    rows = []
    for d, n in stale:
        rows.append((date.today().isoformat(), "pg_close_vs_krx", "*", int(d[:4]), 0, "-", f"{d} 잠정 표시 {n}행 2거래일 초과(KRX 미수신)", 0))
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
