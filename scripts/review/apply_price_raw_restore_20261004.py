#!/usr/bin/env python3
"""price_history 원주가 복원 적용(2026-10-04). 계획: plan_price_raw_restore_20261004.py 의 A·E 구간만.

  A 확정 이벤트 있음(이중 조정 상태) · E PG 단독 단절(기준값은 연속, PG만 배율만큼 끊김 = 가짜 급등락)
행 단위 조건(엄격):
  - 공식(stock_price_daily)과 marcap이 둘 다 있으면 두 종가가 1원 이내로 같을 때만(공식 소스도 틀린 사례 확인: 003090 2020-12-21 65,401원)
  - 한쪽만 있으면 그 종가가 정수이고 >0
  - 새 OHLCV = 기준 소스의 시·고·저·종·거래량(공식 우선, 없으면 marcap). 수급 컬럼은 건드리지 않는다
기록: price_history_fix_backup(old/new OHLCV, reason) + data_fix_log, 같은 run_id. 기본 dry-run, --apply 시 적용.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

D = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
MARCAP = ROOT / "data_cache" / "marcap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    seg = pd.read_csv(D / "restore_plan_segments.csv", dtype={"code": str})
    seg = seg[seg["class"].str[0].isin(["A", "E"])]
    m = pd.read_csv(D / "mismatch_rows.csv", dtype={"code": str})
    keep = []
    for s in seg.itertuples():
        g = m[(m.code == s.code) & (m.date >= s.first) & (m.date <= s.last)]
        g = g[(g.ratio / s.ratio - 1).abs() <= 0.02]
        keep.append(g)
    tgt = pd.concat(keep).drop_duplicates(["code", "date"])
    conn = connect_primary_db(timeout=1800, readonly=not a.apply)
    conn.execute("SET statement_timeout='1800s'")
    codes = sorted(tgt.code.unique())
    ph = ",".join("?" * len(codes))
    off = pd.DataFrame([tuple(r) for r in conn.execute(
        f"SELECT stock_code, bas_dt, open_price, high_price, low_price, close_price, volume FROM stock_price_daily WHERE stock_code IN ({ph})", codes).fetchall()],
        columns=["code", "bas_dt", "o_o", "o_h", "o_l", "o_c", "o_v"])
    off["date"] = pd.to_datetime(off.bas_dt, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    mc = pd.concat([pd.read_parquet(p, columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"]) for p in sorted(MARCAP.glob("marcap-*.parquet"))])
    mc = mc[mc.Code.isin(codes)].rename(columns={"Code": "code", "Open": "m_o", "High": "m_h", "Low": "m_l", "Close": "m_c", "Volume": "m_v"})
    mc["date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d")
    pg = pd.DataFrame([tuple(r) for r in conn.execute(
        f"SELECT stock_code, date, open, high, low, close, volume FROM price_history WHERE stock_code IN ({ph})", codes).fetchall()],
        columns=["code", "date", "p_o", "p_h", "p_l", "p_c", "p_v"])
    x = tgt[["code", "date"]].merge(pg, on=["code", "date"]).merge(off.drop(columns="bas_dt").drop_duplicates(["code", "date"]), on=["code", "date"], how="left") \
        .merge(mc.drop(columns="Date").drop_duplicates(["code", "date"]), on=["code", "date"], how="left")
    has_o = x.o_c.notna() & (x.o_c > 0)
    has_m = x.m_c.notna() & (x.m_c > 0)
    both_agree = has_o & has_m & ((x.o_c - x.m_c).abs() <= 1)
    only_one = (has_o ^ has_m)
    one_val = x.o_c.where(has_o, x.m_c)
    ok = both_agree | (only_one & (one_val % 1 == 0))
    st = {"후보 행": len(x), "적용 가능(조건 통과)": int(ok.sum()), "제외: 공식≠marcap": int((has_o & has_m & ~both_agree).sum()),
          "제외: 기준 없음": int((~has_o & ~has_m).sum())}
    y = x[ok].copy()
    use_o = y.o_c.notna() & (y.o_c > 0)
    for k, oo, mm in (("open", "o_o", "m_o"), ("high", "o_h", "m_h"), ("low", "o_l", "m_l"), ("close", "o_c", "m_c"), ("volume", "o_v", "m_v")):
        y["n_" + k] = y[oo].where(use_o, y[mm])
    y = y[(y.n_close > 0) & y.n_open.notna() & y.n_high.notna() & y.n_low.notna()]
    y = y[(y.n_high >= y[["n_open", "n_close"]].max(axis=1)) & (y.n_low <= y[["n_open", "n_close"]].min(axis=1))]
    st["최종 적용 행"] = len(y)
    st["종목"] = int(y.code.nunique())
    print(json.dumps(st, ensure_ascii=False))
    y.to_csv(D / "restore_apply_rows.csv", index=False)
    if not a.apply:
        return
    run_id = f"price_raw_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = "원주가 복원: PG에 수정주가/배율 오염(PG 단독 단절 또는 확정 이벤트 이중 조정). 기준=stock_price_daily(공식)·marcap 일치 행만. plan_price_raw_restore_20261004"
    rows = list(y.itertuples(index=False))
    conn.executemany("""INSERT INTO price_history_fix_backup(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(run_id, r.code, r.date, r.p_o, r.p_h, r.p_l, r.p_c, r.p_v, r.n_open, r.n_high, r.n_low, r.n_close, r.n_volume, reason, now)
                      for r in rows])
    # 보호 트리거 guard_historical_price_write: 기준 검증을 마친 배치만 이 트랜잭션 안에서 허용(정수·양수 검사는 계속 적용)
    conn.execute("SELECT set_config('app.price_basis_checked','1', true)")
    conn.executemany("UPDATE price_history SET open=?, high=?, low=?, close=?, volume=? WHERE stock_code=? AND date=?",
                     [(r.n_open, r.n_high, r.n_low, r.n_close, r.n_volume, r.code, r.date) for r in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "price_history", f"{st['종목']}종목 원주가 복원(A·E 구간)", len(rows), reason, json.dumps(st, ensure_ascii=False),
                  "공식·marcap 원주가", "scripts/review/apply_price_raw_restore_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
