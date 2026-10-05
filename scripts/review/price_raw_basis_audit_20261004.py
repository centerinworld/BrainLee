#!/usr/bin/env python3
"""price_history(원주가 정본) 전수 기준 감사 — 읽기 전용(2026-10-04, Claude가 Codex 결론 재검토).

배경: Codex는 marcap과 다른 행을 FDR·pykrx(수정주가 계열) 표본이 PG와 같다는 이유로 '정상'으로 봤다. 그러나 공공데이터
원주가(stock_price_daily)가 있는 표본 79/79에서 공식값=marcap≠PG였다 → PG에 수정주가가 원주가 자리에 섞인 것.
이 스크립트는 PG 모든 행을 기준값과 대조한다.
  기준 1(우선): stock_price_daily(공공데이터 원주가, 2020~)
  기준 2: marcap parquet(KRX 일별, 2010~). 기준 1이 있는 날은 marcap=공식 여부도 함께 집계(marcap 신뢰도 측정)
2026-10-05 추가: 독립 소스 수별 일치율(공식·marcap = KRX 계열 1개, KIS = 별개), 시가·고가·저가 공식 대조, 보통주 소수점 가격 행.
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
KRAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/kis_raw_daily")


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
        "SELECT stock_code, bas_dt, close_price, volume, open_price, high_price, low_price FROM stock_price_daily").fetchall()],
        columns=["code", "bas_dt", "off_close", "off_vol", "off_open", "off_high", "off_low"])
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
    # 2026-10-04: 공식(stock_price_daily)도 틀리는 사례(거래정지 기간 기준가 등) 확인 → KIS 원주가(제3 소스, data_raw/kis_raw_daily)로 다수결
    kis_rows = []
    for p in KRAW.glob("*.json"):
        for d, v in json.loads(p.read_text()).items():
            if v.get("close"):
                kis_rows.append((p.stem, d, float(v["close"])))
    kis = pd.DataFrame(kis_rows, columns=["code", "date", "kis_close"]).drop_duplicates(["code", "date"])
    m = m.merge(kis, on=["code", "date"], how="left")
    o_ok = m.off_close.notna() & (m.off_close > 0)
    m_ok = m.mc_close.notna() & (m.mc_close > 0)
    k_ok = m.kis_close.notna() & (m.kis_close > 0)
    om = o_ok & m_ok & ((m.off_close - m.mc_close).abs() <= 1)
    ok_ = o_ok & k_ok & ((m.off_close - m.kis_close).abs() <= 1)
    mk = m_ok & k_ok & ((m.mc_close - m.kis_close).abs() <= 1)
    ref = pd.Series(float("nan"), index=m.index)
    src = pd.Series("없음", index=m.index)
    # 우선순위: 두 소스 이상 합의 → 그 값. 한 소스뿐이면 그 값(공식>marcap>KIS). 공식≠marcap이고 KIS 없으면 '판정 보류'
    for cond, val, name in ((om, m.off_close, "공식=marcap"), (ok_, m.off_close, "공식=KIS"), (mk, m.mc_close, "marcap=KIS")):
        sel = cond & ref.isna()
        ref[sel], src[sel] = val[sel], name
    only_o = o_ok & ~m_ok & ~k_ok & ref.isna()
    only_m = m_ok & ~o_ok & ~k_ok & ref.isna()
    only_k = k_ok & ~o_ok & ~m_ok & ref.isna()
    ref[only_o], src[only_o] = m.off_close[only_o], "공식만"
    ref[only_m], src[only_m] = m.mc_close[only_m], "marcap만"
    ref[only_k], src[only_k] = m.kis_close[only_k], "KIS만"
    amb = ref.isna() & (o_ok | m_ok | k_ok)
    src[amb] = "소스 간 불일치(판정 보류)"
    st["소스 간 불일치(판정 보류)"] = int(amb.sum())
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
    bad.drop(columns=["off_open", "off_high", "off_low"], errors="ignore").sort_values(["code", "date"]).to_csv(OUT / "mismatch_rows.csv", index=False)
    # 2026-10-05(§9-2-8 #6): ① 독립 소스 수별 집계 — 공식(공공데이터·KRX Open API)과 marcap은 모두 KRX 원천이라 독립 1개, KIS만 별개
    krx = (o_ok | m_ok)
    indep = pd.Series("기준 없음", index=m.index)
    indep[has & krx & ~k_ok] = "독립 1(KRX 계열만)"
    indep[has & ~krx & k_ok] = "독립 1(KIS만)"
    indep[has & krx & k_ok] = "독립 2(KRX 계열+KIS)"
    for g, sub in m[has].groupby(indep[has]):
        okg = ((sub.close - sub.ref).abs() <= 1).sum()
        st[f"[독립 소스] {g}: 행"] = int(len(sub))
        st[f"[독립 소스] {g}: 일치율(%)"] = round(okg / len(sub) * 100, 4)
    # ② 시가·고가·저가 대조(공식값이 있는 행, 1원 허용) + 주식의 소수점 가격
    oh = m[o_ok & m.off_open.notna() & (m.off_open > 0)]
    for f in ("open", "high", "low"):
        diff = (oh[f] - oh[f"off_{f}"]).abs() > 1
        st[f"[OHLC] {f} 공식 대조 {len(oh):,}행 중 불일치"] = int(diff.sum())
    frac = m[(m.code.str.match(r"^\d{5}0$")) & ((m.open % 1 != 0) | (m.high % 1 != 0) | (m.low % 1 != 0) | (m.close % 1 != 0))]
    st["[OHLC] 보통주 소수점 가격 행"] = int(len(frac))
    oh_bad = oh[((oh.open - oh.off_open).abs() > 1) | ((oh.high - oh.off_high).abs() > 1) | ((oh.low - oh.off_low).abs() > 1)]
    oh_bad[["code", "date", "open", "high", "low", "close", "off_open", "off_high", "off_low", "off_close"]].to_csv(OUT / "ohlc_mismatch_rows.csv", index=False)
    frac[["code", "date", "open", "high", "low", "close"]].to_csv(OUT / "fractional_stock_rows.csv", index=False)
    by_year = bad.groupby(bad.date.str[:4]).size().to_dict()
    json.dump({"stats": dict(st), "mismatch_by_year": by_year}, open(OUT / "summary.json", "w"), ensure_ascii=False, indent=1)
    for k, v in st.items():
        print(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}")
    print("연도별 불일치:", by_year)


if __name__ == "__main__":
    main()
