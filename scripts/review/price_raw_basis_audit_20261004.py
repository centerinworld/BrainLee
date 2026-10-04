#!/usr/bin/env python3
"""price_history(원주가 정본) 전수 기준 감사 — 읽기 전용(2026-10-04, Claude가 Codex 결론 재검토).

배경: Codex는 marcap과 다른 행을 FDR·pykrx(수정주가 계열) 표본이 PG와 같다는 이유로 '정상'으로 봤다. 그러나 공공데이터
원주가(stock_price_daily)가 있는 표본 79/79에서 공식값=marcap≠PG였다 → PG에 수정주가가 원주가 자리에 섞인 것.
이 스크립트는 PG 모든 행을 기준값과 대조한다.
  기준 1(우선): stock_price_daily(공공데이터 원주가, 2020~)
  기준 2: marcap parquet(KRX 일별, 2010~). 기준 1이 있는 날은 marcap=공식 여부도 함께 집계(marcap 신뢰도 측정)
판정(종가 기준, 허용 1원): 일치 / 불일치(배율 r=PG/기준이 정수배·역수 정수배면 '기업행위 배율', 아니면 '값 다름') / 기준 없음
산출: research_outputs/price_raw_basis_audit_20261004/{summary.json, mismatch_rows.csv}
"""
import collections
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "price_raw_basis_audit_20261004"
MARCAP = ROOT / "data_cache" / "marcap"


def ratio_kind(r):
    if r <= 0:
        return "값 다름"
    for x in (r, 1 / r):
        if x >= 1.5 and abs(x - round(x)) / x < 0.01:
            return "기업행위 배율(정수배)"
    return "값 다름"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conn = connect_primary_db(timeout=1800, readonly=True)
    conn.execute("SET statement_timeout='1800s'")
    pg = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code, date::text, open, high, low, close, volume FROM price_history WHERE stock_code ~ '^[0-9A-Z]{6}$'").fetchall()],
        columns=["code", "date", "open", "high", "low", "close", "volume"])
    off = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code, bas_dt, close_price, volume FROM stock_price_daily").fetchall()], columns=["code", "bas_dt", "off_close", "off_vol"])
    conn.close()
    off["date"] = pd.to_datetime(off.bas_dt, format="%Y%m%d").dt.strftime("%Y-%m-%d")
    off = off.drop(columns="bas_dt").drop_duplicates(["code", "date"])
    mc = pd.concat([pd.read_parquet(p, columns=["Code", "Date", "Close", "Volume"]) for p in sorted(MARCAP.glob("marcap-*.parquet"))])
    mc = mc.rename(columns={"Code": "code", "Close": "mc_close", "Volume": "mc_vol"})
    mc["date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d")
    mc = mc.drop(columns="Date").drop_duplicates(["code", "date"])
    pg["date"] = pg.date.str[:10]
    m = pg.merge(off, on=["code", "date"], how="left").merge(mc, on=["code", "date"], how="left")
    st = collections.Counter()
    # marcap 신뢰도: 공식값이 있는 날 marcap=공식?
    both = m.dropna(subset=["off_close", "mc_close"])
    both = both[both.off_close > 0]
    st["marcap 신뢰도 표본(공식·marcap 둘 다)"] = len(both)
    st["  그중 marcap=공식"] = int(((both.mc_close - both.off_close).abs() <= 1).sum())
    ref = m.off_close.where(m.off_close.notna() & (m.off_close > 0), m.mc_close)
    src = pd.Series(["공식"] * len(m), index=m.index).where(m.off_close.notna() & (m.off_close > 0), "marcap")
    src = src.where(ref.notna(), "없음")
    m["ref"], m["ref_src"] = ref, src
    has = m.ref.notna() & (m.ref > 0) & m.close.notna() & (m.close > 0)
    st["PG 행"] = len(m)
    st["기준 있음"] = int(has.sum())
    st["기준 없음(또는 0)"] = int((~has).sum())
    ok = has & ((m.close - m.ref).abs() <= 1)
    st["일치"] = int(ok.sum())
    bad = m[has & ~ok].copy()
    bad["ratio"] = bad.close / bad.ref
    bad["kind"] = bad.ratio.map(ratio_kind)
    for (s, k), n in bad.groupby(["ref_src", "kind"]).size().items():
        st[f"불일치[{s}] {k}"] = int(n)
    st["불일치 종목 수"] = int(bad.code.nunique())
    st["기준 대비 일치율(%)"] = round(ok.sum() / has.sum() * 100, 4)
    bad.sort_values(["code", "date"]).to_csv(OUT / "mismatch_rows.csv", index=False)
    by_year = bad.groupby(bad.date.str[:4]).size().to_dict()
    json.dump({"stats": dict(st), "mismatch_by_year": by_year}, open(OUT / "summary.json", "w"), ensure_ascii=False, indent=1)
    for k, v in st.items():
        print(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}")
    print("연도별 불일치:", by_year)


if __name__ == "__main__":
    main()
