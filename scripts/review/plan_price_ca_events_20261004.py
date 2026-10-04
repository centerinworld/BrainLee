#!/usr/bin/env python3
"""B·C 구간(기준 원주가도 경계일에 배율만큼 끊김 = 실제 기업행위)의 이벤트 근거 확인(dry-run, 2026-10-04).

근거: marcap 상장주식수(Stocks)가 경계 전 마지막 날 → 경계일(다음 거래일) 사이에 1/배율 만큼 변했는가(±2%).
  예) 1→2 액면분할: PG(수정주가)/원주가 = 0.5, 주식수 ×2 → 확인.
판정:
  확인        : 주식수 변화가 배율과 일치 → 이벤트 등록 + 원주가 복원 대상
  기존 이벤트 : 경계 ±10일에 corporate_action_events가 이미 있음(상태 무관) → 중복 방지로 등록 보류(사람 검토)
  불일치/자료 없음 : 보류
산출: research_outputs/price_raw_basis_audit_20261004/ca_event_plan.csv
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

D = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
MARCAP = ROOT / "data_cache" / "marcap"


def main():
    seg = pd.read_csv(D / "restore_plan_segments.csv", dtype={"code": str})
    seg = seg[seg["class"].str[0].isin(["B", "C"]) & (seg["class"] != "C 배율 흔들림")].copy()
    codes = sorted(seg.code.unique())
    mc = pd.concat([pd.read_parquet(p, columns=["Code", "Date", "Stocks"]) for p in sorted(MARCAP.glob("marcap-*.parquet"))])
    mc = mc[mc.Code.isin(codes)]
    mc["date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d")
    sh = {k: g.set_index("date").Stocks.sort_index() for k, g in mc.groupby("Code")}
    conn = connect_primary_db(timeout=600, readonly=True)
    ev = pd.DataFrame([tuple(r) for r in conn.execute("SELECT stock_code, event_date::text, event_type, adjustment_status FROM corporate_action_events").fetchall()],
                      columns=["code", "ev_date", "etype", "status"])
    nxt = {}
    for s in seg.itertuples():
        r = conn.execute("SELECT date FROM price_history WHERE stock_code=? AND date>? ORDER BY date LIMIT 1", (s.code, s.last)).fetchone()
        nxt[(s.code, s.last)] = r[0][:10] if r else None
    conn.close()
    evg = {k: v for k, v in ev.groupby("code")}
    out = []
    for s in seg.itertuples():
        bd = nxt.get((s.code, s.last))
        st = sh.get(s.code)
        verdict, old, new = "자료 없음", None, None
        if bd and st is not None and s.last in st.index and bd in st.index:
            old, new = float(st[s.last]), float(st[bd])
            if old > 0 and new > 0 and abs((new / old) * s.ratio - 1) <= 0.02:
                verdict = "확인"
            else:
                verdict = "주식수 불일치"
        e = evg.get(s.code)
        if verdict == "확인" and e is not None and bd:
            near = e[(pd.to_datetime(e.ev_date) - pd.Timestamp(bd)).dt.days.abs() <= 10]
            if len(near):
                verdict = "기존 이벤트 있음: " + ";".join(f"{a}/{b}/{c}" for a, b, c in near[["ev_date", "etype", "status"]].values[:3])
        out.append({"code": s.code, "first": s.first, "last": s.last, "boundary": bd, "n": s.n, "ratio": s.ratio, "class": getattr(s, "_8", None) or seg.loc[s.Index, "class"],
                    "old_shares": old, "new_shares": new, "verdict": verdict, "evidence": s.evidence})
    o = pd.DataFrame(out)
    o.to_csv(D / "ca_event_plan.csv", index=False)
    o["v"] = o.verdict.str.split(":").str[0]
    print(o.groupby(["class", "v"]).agg(segments=("n", "size"), rows=("n", "sum"), stocks=("code", "nunique")).to_string())


if __name__ == "__main__":
    main()
