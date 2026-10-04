#!/usr/bin/env python3
"""price_history 수정주가 혼입 구간의 원주가 복원 계획(dry-run, 2026-10-04). 입력: price_raw_basis_audit_20261004/mismatch_rows.csv

종목별로 불일치 행을 '같은 배율(PG/기준)이 이어지는 구간'으로 묶고, 구간 직후 경계일(=기업행위 발효일 추정)과 배율로 분류한다.
  A  확정 이벤트 있음(corporate_action_events factor_confirmed, 경계 ±10일, backward_price_factor≈배율) → 지금 이중 조정 상태. 원주가 복원만.
  B  이벤트 없음, DART 공시(병합·분할·감자·무상·유상증자·액면) 경계 -60~+10일 → 원주가 복원 + 이벤트 등록(근거 rcept_no)
  C  배율이 구간 안에서 흔들림(최대/최소>1.02) 또는 근거 없음 → 보류(사람 검토)
  D  배율≈1(0.98~1.02, 소액 차이) → 값 오류 후보, 별도 검토(복원 대상 아님)
  E  기준값은 경계에서 이어지는데(전후 변화 <30%) PG만 배율만큼 끊김 → 기업행위가 아니라 PG 단독 오류(가짜 급등락). 원주가 복원만(이벤트 불필요)
산출: research_outputs/price_raw_basis_audit_20261004/restore_plan_segments.csv, restore_plan_summary.json
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

D = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
KW = re.compile(r"주식병합|주식분할|감자|무상증자|유상증자|액면|병합|분할")


def main():
    m = pd.read_csv(D / "mismatch_rows.csv", dtype={"code": str}).sort_values(["code", "date"])
    m["r"] = m.ratio.round(2)
    segs = []
    for code, g in m.groupby("code"):
        g = g.reset_index(drop=True)
        start = 0
        for i in range(1, len(g) + 1):
            if i == len(g) or abs(g.ratio[i] / g.ratio[i - 1] - 1) > 0.02:
                s = g.iloc[start:i]
                segs.append({"code": code, "first": s.date.min(), "last": s.date.max(), "n": len(s), "ratio": float(s.ratio.median()),
                             "ratio_spread": float(s.ratio.max() / s.ratio.min()), "ref_src": s.ref_src.mode()[0]})
                start = i
    seg = pd.DataFrame(segs)
    conn = connect_primary_db(timeout=600, readonly=True)
    # 경계 직후 첫 거래일의 PG 종가(=기준값과 일치하는 행) — 기준값 연속성 판정용
    nxt = {}
    for s in seg.itertuples():
        r = conn.execute("SELECT date, close FROM price_history WHERE stock_code=? AND date>? ORDER BY date LIMIT 1", (s.code, s.last)).fetchone()
        nxt[(s.code, s.last)] = (r[0][:10], float(r[1])) if r and r[1] else None
    last_ref = m.groupby(["code", "date"]).ref.first().to_dict()
    # 2026-10-04 보정: 다음 날도 오류 구간(배율만 다른 연속 구간)일 수 있으므로, 다음 날 값은 PG가 아니라 기준값(불일치 행이면 그 ref)을 쓴다
    for k, v in list(nxt.items()):
        if v:
            nxt[k] = last_ref.get((k[0], v[0]), v[1])
    ev = pd.DataFrame([tuple(r) for r in conn.execute("""SELECT stock_code, event_date::text, backward_price_factor, event_type, adjustment_status
                       FROM corporate_action_events WHERE adjustment_status='factor_confirmed'""").fetchall()],
                      columns=["code", "ev_date", "factor", "etype", "status"])
    dis = pd.DataFrame([tuple(r) for r in conn.execute("SELECT stock_code, rcept_dt::text, rcept_no, report_nm FROM dart_disclosures").fetchall()],
                       columns=["code", "dt", "rcept_no", "report_nm"])
    conn.close()
    dis = dis[dis.report_nm.str.contains(KW, na=False)]
    evg = {k: v for k, v in ev.groupby("code")}
    dsg = {k: v for k, v in dis.groupby("code")}
    cls, evid = [], []
    for s in seg.itertuples():
        last = pd.Timestamp(s.last)
        r = s.ratio
        if 0.98 <= r <= 1.02:
            cls.append("D 소액 차이")
            evid.append("")
            continue
        nx = nxt.get((s.code, s.last))
        lr = last_ref.get((s.code, s.last))
        if nx and lr and abs(nx / lr - 1) < 0.30:
            cls.append("E PG 단독 단절(기준값 연속)")
            evid.append(f"기준 {lr:g}→다음날 {nx:g}, PG 배율 {r:.3f}")
            continue
        if s.ratio_spread > 1.02:
            cls.append("C 배율 흔들림")
            evid.append("")
            continue
        e = evg.get(s.code)
        hit = None
        if e is not None:
            for x in e.itertuples():
                dd = (pd.Timestamp(x.ev_date) - last).days
                f = x.factor
                if -2 <= dd <= 10 and f and (abs(f / r - 1) < 0.03 or abs((1 / f) / r - 1) < 0.03):
                    hit = f"{x.ev_date} {x.etype} factor={f:.4f}"
                    break
        if hit:
            cls.append("A 확정 이벤트 있음(이중 조정)")
            evid.append(hit)
            continue
        d = dsg.get(s.code)
        hitd = None
        if d is not None:
            for x in d.itertuples():
                dd = (last - pd.Timestamp(x.dt)).days
                if -10 <= dd <= 60:
                    hitd = f"{x.dt} {x.rcept_no} {x.report_nm}"
                    break
        if hitd:
            cls.append("B 공시 근거 있음·이벤트 미등록")
            evid.append(hitd)
        else:
            cls.append("C 근거 없음")
            evid.append("")
    seg["class"], seg["evidence"] = cls, evid
    seg.to_csv(D / "restore_plan_segments.csv", index=False)
    summ = seg.groupby("class").agg(segments=("n", "size"), rows=("n", "sum"), stocks=("code", "nunique"))
    print(summ.to_string())
    json.dump({"by_class": summ.reset_index().to_dict("records")}, open(D / "restore_plan_summary.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
