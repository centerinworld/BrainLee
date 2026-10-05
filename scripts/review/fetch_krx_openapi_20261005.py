#!/usr/bin/env python3
"""KRX Open API 일별 데이터 수집·백필(2026-10-05).

발견: 공식 주가 테이블 stock_price_daily(공공데이터포털)가 2021-02-18~2026-08-20 1,280거래일·2010~2019년 전체가 비어 있어
      그 기간 가격 대조가 marcap·KIS 두 소스뿐이었다. KRX Open API 승인 서비스(키 1개, 서비스별 승인) 중 다음을 쓴다.
  stock : sto/stk_bydd_trd(유가)·sto/ksq_bydd_trd(코스닥) 일별매매 → stock_price_daily 빈 (날짜, 종목)에만 삽입(기존 행 불변)
  deriv : idx/drvprod_dd_trd(파생상품지수: 코스피200 변동성지수·선물지수 등) → krx_derivative_index_daily
          drv/fut_bydd_trd(선물 일별매매: 코스피200·코스닥150·변동성지수·통화·금리 선물, 미결제약정) → krx_futures_daily
원문: /Volumes/Realtek_NVME/stock_dashboard/data_raw/krx_openapi/<서비스>/<YYYYMMDD>.json.gz (받은 날은 다시 받지 않음 — 이어 받기)
거래일 목록 = price_history 005930 날짜(2010~). 호출 상한 --max-calls(KRX 일일 한도 안), 한도·인증 오류 시 중단.
"""
import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/krx_openapi")
API = "https://data-dbg.krx.co.kr/svc/apis/"
DDL = [
    """CREATE TABLE IF NOT EXISTS krx_derivative_index_daily (bas_dd TEXT, idx_clss TEXT, idx_nm TEXT, close DOUBLE PRECISION, open DOUBLE PRECISION,
       high DOUBLE PRECISION, low DOUBLE PRECISION, chg DOUBLE PRECISION, fluc_rt DOUBLE PRECISION, PRIMARY KEY (bas_dd, idx_clss, idx_nm))""",
    """CREATE TABLE IF NOT EXISTS krx_futures_daily (bas_dd TEXT, prod_nm TEXT, mkt_nm TEXT, isu_cd TEXT, isu_nm TEXT, close DOUBLE PRECISION,
       open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, spot DOUBLE PRECISION, settle DOUBLE PRECISION, volume DOUBLE PRECISION,
       value DOUBLE PRECISION, open_interest DOUBLE PRECISION, PRIMARY KEY (bas_dd, isu_cd, mkt_nm))""",
]


class Stop(Exception):
    pass


def f(v):
    v = str(v or "").replace(",", "").strip()
    if v in ("", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


class Krx:
    def __init__(self, max_calls):
        self.key = os.getenv("KRX_OPENAPI_KEY") or os.getenv("KRX_API_KEY")
        self.left = max_calls

    def get(self, ep, d):
        p = RAW / ep.replace("/", "_") / f"{d}.json.gz"
        if p.exists():
            return json.loads(gzip.decompress(p.read_bytes()))
        if self.left <= 0:
            raise Stop("호출 상한")
        self.left -= 1
        time.sleep(0.15)
        r = requests.get(API + ep, params={"basDd": d}, headers={"AUTH_KEY": self.key}, timeout=30)
        if r.status_code != 200:
            raise Stop(f"{ep} {d} HTTP {r.status_code} {r.text[:80]}")
        rows = r.json().get("OutBlock_1")
        if rows is None:
            raise Stop(f"{ep} {d} 응답 형식 이상 {r.text[:80]}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode()))
        return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="all", choices=["stock", "deriv", "all"])
    ap.add_argument("--since", default="2010-01-01")
    ap.add_argument("--max-calls", type=int, default=9000)
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900)
    for d_ in DDL:
        conn.execute(d_)
    conn.commit()
    days = [str(r[0])[:10].replace("-", "") for r in conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='005930' AND date>=? ORDER BY 1", (a.since,)).fetchall()]
    have = {r[0]: r[1] for r in conn.execute("SELECT bas_dt, COUNT(*) FROM stock_price_daily GROUP BY 1").fetchall()}
    K = Krx(a.max_calls)
    run_id = f"krx_openapi_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    n_stock = n_idx = n_fut = 0
    try:
        if a.kind in ("stock", "all"):
            todo = [d for d in days if have.get(d, 0) < 2000]  # 유가+코스닥 약 2,500~2,700종목 — 비었거나 덜 찬 날만
            print(f"공식 주가 빈 날 {len(todo)}일", flush=True)
            for i, d in enumerate(todo, 1):
                rows = []
                for ep, mkt in (("sto/stk_bydd_trd", "KOSPI"), ("sto/ksq_bydd_trd", "KOSDAQ")):
                    for x in K.get(ep, d):
                        c = f(x.get("TDD_CLSPRC"))
                        if not c or not str(x.get("ISU_CD", "")).strip():
                            continue
                        rows.append((d, x["ISU_CD"].strip(), x.get("ISU_NM"), mkt, f(x.get("TDD_OPNPRC")), f(x.get("TDD_HGPRC")), f(x.get("TDD_LWPRC")), c,
                                     f(x.get("CMPPREVDD_PRC")), f(x.get("FLUC_RT")), f(x.get("ACC_TRDVOL")), f(x.get("ACC_TRDVAL")), f(x.get("MKTCAP")),
                                     f(x.get("LIST_SHRS")), now))
                if rows:
                    conn.executemany("""INSERT INTO stock_price_daily(bas_dt, stock_code, stock_name, market, open_price, high_price, low_price, close_price,
                                        vs, change_pct, volume, trade_amt, market_cap, shares, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                        ON CONFLICT (bas_dt, stock_code) DO NOTHING""", rows)
                    n_stock += len(rows)
                if i % 50 == 0:
                    conn.commit()
                    print(f"  주가 {i}/{len(todo)}일 ({d}), 남은 호출 {K.left}", flush=True)
            conn.commit()
        if a.kind in ("deriv", "all"):
            print(f"파생 {len(days)}일 확인", flush=True)
            for i, d in enumerate(days, 1):
                ix = [(d, x.get("IDX_CLSS"), x.get("IDX_NM"), f(x.get("CLSPRC_IDX")), f(x.get("OPNPRC_IDX")), f(x.get("HGPRC_IDX")), f(x.get("LWPRC_IDX")),
                       f(x.get("CMPPREVDD_IDX")), f(x.get("FLUC_RT"))) for x in K.get("idx/drvprod_dd_trd", d) if x.get("IDX_NM")]
                fu = [(d, x.get("PROD_NM"), x.get("MKT_NM"), x.get("ISU_CD"), x.get("ISU_NM"), f(x.get("TDD_CLSPRC")), f(x.get("TDD_OPNPRC")), f(x.get("TDD_HGPRC")),
                       f(x.get("TDD_LWPRC")), f(x.get("SPOT_PRC")), f(x.get("SETL_PRC")), f(x.get("ACC_TRDVOL")), f(x.get("ACC_TRDVAL")), f(x.get("ACC_OPNINT_QTY")))
                      for x in K.get("drv/fut_bydd_trd", d) if x.get("ISU_CD")]
                if ix:
                    conn.executemany("INSERT INTO krx_derivative_index_daily VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", ix)
                    n_idx += len(ix)
                if fu:
                    conn.executemany("INSERT INTO krx_futures_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING", fu)
                    n_fut += len(fu)
                if i % 100 == 0:
                    conn.commit()
                    print(f"  파생 {i}/{len(days)}일 ({d}), 남은 호출 {K.left}", flush=True)
            conn.commit()
    except Stop as e:
        conn.commit()
        print("중단:", e, flush=True)
    if n_stock:
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                     (now, "stock_price_daily", "공식 주가 빈 날짜 KRX Open API 채움", n_stock, "빈 (날짜, 종목)에만 삽입, 기존 행 불변",
                      "", f"KRX 유가·코스닥 일별매매 {n_stock}행", "scripts/review/fetch_krx_openapi_20261005.py", run_id))
        conn.commit()
    print(run_id, f"주가 {n_stock:,}행 · 파생지수 {n_idx:,} · 선물 {n_fut:,}, 남은 호출 {K.left}", flush=True)


if __name__ == "__main__":
    main()
