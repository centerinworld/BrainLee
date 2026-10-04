#!/usr/bin/env python3
"""남은 가격 불일치·ETF 소수점 행을 KIS 원주가(제3 소스)로 판정·복원(2026-10-04). 기본 dry-run.

입력: price_raw_basis_audit_20261004/mismatch_rows.csv(남은 불일치), fractional_rows_classified.csv(ETF 소수점),
      data_raw/kis_raw_daily/<code>.json(fetch_kis_raw_daily_20261004.py)
판정(종가 1원 허용):
  주식: KIS가 공식 또는 marcap과 일치(독립 2소스 합의) → 그 OHLCV로 복원 / KIS=PG → PG 정상(기준값 쪽 오류)으로 기록 / 그 외 보류
  ETF : PG 종가가 소수점이고 KIS 원주가가 정수 → KIS OHLCV로 교체 / 그 외 보류
기업행위 경계(B·C)도 같은 규칙으로 원주가 복원 — 경계일의 실제 급변은 가격 급변 감사가 unexplained_jump로 표시해
canonical_price_history_v가 return_usable=0으로 수익률에서 제외한다(이벤트 미등록이어도 가짜 수익 미발생).
기록: price_history_fix_backup + data_fix_log(run_id). 보호 트리거는 트랜잭션 한정 app.price_basis_checked=1.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

D = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
KRAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/kis_raw_daily")
MARCAP = ROOT / "data_cache" / "marcap"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--etf-adjusted", action="store_true",
                    help="ETF 정수 행 중 PG가 배당 조정 수정주가(FDR 계열)인 것 → KIS 원주가로. 근거: KIS 수정모드(ADJ=0)≈PG·FDR, 원주가모드(ADJ=1)만 다름(2026-10-04 표본 확인)")
    a = ap.parse_args()
    m = pd.read_csv(D / "mismatch_rows.csv", dtype={"code": str})
    f = pd.read_csv(D / "fractional_rows_classified.csv", dtype={"code": str})
    etf = f[f.cls == "marcap에 종목 없음"][["code", "date"]].assign(kind="etf")
    stk = m[["code", "date"]].assign(kind="stock")
    t = pd.concat([stk, etf]).drop_duplicates(["code", "date"])
    if a.etf_adjusted:
        t = m[m.ref_src == "KIS만"][["code", "date"]].assign(kind="etf_adj")
    kis = {}
    for code in t.code.unique():
        p = KRAW / f"{code}.json"
        if p.exists():
            kis[code] = json.loads(p.read_text())
    conn = connect_primary_db(timeout=1800, readonly=not a.apply)
    codes = sorted(t.code.unique())
    ph = ",".join("?" * len(codes))
    pg = {(r[0], r[1][:10]): r for r in map(tuple, conn.execute(
        f"SELECT stock_code, date, open, high, low, close, volume FROM price_history WHERE stock_code IN ({ph})", codes).fetchall())}
    off = {(r[0], f"{r[1][:4]}-{r[1][4:6]}-{r[1][6:8]}"): float(r[2]) for r in map(tuple, conn.execute(
        f"SELECT stock_code, bas_dt, close_price FROM stock_price_daily WHERE stock_code IN ({ph}) AND close_price>0", codes).fetchall())}
    mc = pd.concat([pd.read_parquet(p, columns=["Code", "Date", "Close"]) for p in sorted(MARCAP.glob("marcap-*.parquet"))])
    mc = mc[mc.Code.isin(codes)]
    mcd = {(c, d.strftime("%Y-%m-%d")): float(v) for c, d, v in zip(mc.Code, mc.Date, mc.Close)}
    st, plan, keep_pg = collections.Counter(), [], []
    for r in t.itertuples(index=False):
        p = pg.get((r.code, r.date))
        k = (kis.get(r.code) or {}).get(r.date)
        if not p:
            st["PG 행 없음(이미 삭제 등)"] += 1
            continue
        if not k or not k.get("close"):
            st[f"{r.kind}: KIS 값 없음 → 보류"] += 1
            continue
        kc, pc = float(k["close"]), float(p[5])
        o, mk = off.get((r.code, r.date)), mcd.get((r.code, r.date))
        if abs(kc - pc) <= 1 and r.kind == "stock":
            st["stock: KIS=PG → PG 정상(기준값 쪽 오류)"] += 1
            keep_pg.append((r.code, r.date, pc, o, mk, kc))
            continue
        if r.kind == "stock":
            agree = (o is not None and abs(kc - o) <= 1) or (mk is not None and abs(kc - mk) <= 1)
            if not agree:
                st["stock: KIS가 공식·marcap과도 다름 → 보류"] += 1
                continue
        elif r.kind == "etf_adj":
            if kc != round(kc) or not (0.75 <= pc / kc < 1.0):
                st["etf_adj: 조정 방향·범위 밖 → 보류"] += 1
                continue
        else:
            if pc == round(pc) or kc != round(kc):
                st["etf: 조건 불충족(PG 정수 또는 KIS 소수) → 보류"] += 1
                continue
        no, nh, nl, nv = (float(k[x]) for x in ("open", "high", "low", "volume"))
        if not (nl <= min(no, kc) and nh >= max(no, kc) and nl > 0):
            st[f"{r.kind}: KIS OHLC 정합 실패 → 보류"] += 1
            continue
        plan.append((r.code, p[1], p[2], p[3], p[4], p[5], p[6], no, nh, nl, kc, nv, r.kind))
        st[f"{r.kind}: 복원"] += 1
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    pd.DataFrame(keep_pg, columns=["code", "date", "pg", "official", "marcap", "kis"]).to_csv(D / "kis_confirms_pg.csv", index=False)
    pd.DataFrame(plan, columns=["code", "date", "o_o", "o_h", "o_l", "o_c", "o_v", "n_o", "n_h", "n_l", "n_c", "n_v", "kind"]).to_csv(D / "kis_tiebreak_plan.csv", index=False)
    if not a.apply or not plan:
        return
    run_id = f"price_kis_tiebreak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    reason = "원주가 복원(KIS FID_ORG_ADJ_PRC=1 원주가가 공식 또는 marcap과 일치 / ETF는 PG 소수점·KIS 정수). apply_price_kis_tiebreak_20261004"
    conn.executemany("""INSERT INTO price_history_fix_backup(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [(run_id, c, d, oo, oh, ol, oc, ov, no, nh, nl, nc, nv, reason, now) for c, d, oo, oh, ol, oc, ov, no, nh, nl, nc, nv, _ in plan])
    conn.execute("SELECT set_config('app.price_basis_checked','1', true)")
    conn.executemany("UPDATE price_history SET open=?, high=?, low=?, close=?, volume=? WHERE stock_code=? AND date=?",
                     [(no, nh, nl, nc, nv, c, d) for c, d, oo, oh, ol, oc, ov, no, nh, nl, nc, nv, _ in plan])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "price_history", "KIS 원주가 제3 소스 판정 복원(주식·ETF)", len(plan), reason, json.dumps(dict(st), ensure_ascii=False),
                  "KIS 원주가", "scripts/review/apply_price_kis_tiebreak_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan))


if __name__ == "__main__":
    main()
