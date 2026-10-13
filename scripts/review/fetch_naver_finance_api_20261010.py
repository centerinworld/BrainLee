#!/usr/bin/env python3
"""네이버 재무 실적·추정치 재수집 — 모바일 API (2026-10-10).

배경: naver_financial(2026-05-27 단일 수집, finance.naver.com/item/main.naver 표 파싱 — 보관 폴더 scratch/collect_naver_financial.py)은
  ① 달력 분기로 키를 매겨 비12월 결산이 어긋나고 ② 추정치(E) 열 판별이 없고 ③ 원문을 저장하지 않았다. 그 화면은 사이트 개편으로
  stock.naver.com으로 302 이동해 더 이상 받을 수 없다. FnGuide와 0.75%(106/14,152칸) 불일치.
원칙(사용자 2026-10-10): 네이버는 FnGuide 값을 쓰므로 **네이버 = FnGuide 100% 일치**가 정상, 다르면 우리 파싱 오류로 가정한다.
원천: https://m.stock.naver.com/api/stock/{code}/finance/{quarter|annual}  (단위 억원, 열마다 isConsensus Y = 추정치)
저장: 원문 data_raw/naver_finance_api/<YYYYMMDD>/<code>_<quarter|annual>.json.gz,
      표 naver_finance_api(stock_code, fiscal_year, quarter[0=연간], period_key, is_estimate, revenue, operating_profit, net_income, ni_parent,
      revenue_label, fetched_at, raw_file) — 실적·추정치 모두(추정치는 사이트 예측치 표시용, 검증에는 실적만).
기간: 열 키 YYYYMM = 기간 종료 월 → fiscal_period.period_end 역산으로 회계 키(비12월 결산 포함).
사용: fetch_naver_finance_api_20261010.py [--codes a,b] [--limit N]   (당일 원문 있으면 재요청 안 함, 0.25초 간격)
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402 — 정규화 정의 한 곳(2026-10-10)
import fiscal_period  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/naver_finance_api")
URL = "https://m.stock.naver.com/api/stock/{code}/finance/{per}"
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
     "Referer": "https://m.stock.naver.com/"}
REV = ("매출액", "영업수익", "순영업수익", "보험수익", "이자수익")
DDL = """CREATE TABLE IF NOT EXISTS naver_finance_api (
    stock_code TEXT NOT NULL, fiscal_year INT NOT NULL, quarter INT NOT NULL, period_key TEXT NOT NULL, is_estimate BOOLEAN NOT NULL,
    revenue DOUBLE PRECISION, operating_profit DOUBLE PRECISION, net_income DOUBLE PRECISION, ni_parent DOUBLE PRECISION,
    revenue_label TEXT, fetched_at TEXT NOT NULL, raw_file TEXT,
    PRIMARY KEY (stock_code, fiscal_year, quarter, is_estimate))"""


def num(s):
    s = (s or "").replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return float(s) * 1e8  # 억원 → 원
    except ValueError:
        return None


def fetch(code, per, day):
    p = RAW / day / f"{code}_{per}.json.gz"
    if p.exists():
        return json.loads(gzip.decompress(p.read_bytes())), p, False
    for attempt in range(3):
        try:
            r = requests.get(URL.format(code=code, per=per), headers=H, timeout=15)
            if r.status_code == 200:
                d = r.json()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(gzip.compress(json.dumps(d, ensure_ascii=False).encode()))
                return d, p, True
            if r.status_code in (404, 400):
                return None, None, True
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    return None, None, True


def parse(code, d, per):
    fi = (d or {}).get("financeInfo") or {}
    titles = {t["key"]: t.get("isConsensus") == "Y" for t in fi.get("trTitleList") or []}
    rows = {r.get("title", "").strip(): r.get("columns") or {} for r in fi.get("rowList") or []}
    rev_label = next((t for t in REV if t in rows), None)
    out = []
    for key, est in titles.items():
        fk = fn.fiscal_key_from_period_end(code, key, per == "annual")
        if not fk:
            continue
        g = lambda t: num((rows.get(t, {}).get(key) or {}).get("value"))  # noqa: E731
        vals = {"revenue": g(rev_label) if rev_label else None, "operating_profit": g("영업이익"),
                "net_income": g("당기순이익"), "ni_parent": g("지배주주순이익")}
        if all(v is None for v in vals.values()):
            continue
        out.append((code, fk[0], fk[1], key, est, vals["revenue"], vals["operating_profit"], vals["net_income"], vals["ni_parent"], rev_label))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--codes", default=""); ap.add_argument("--limit", type=int, default=0); a = ap.parse_args()
    conn = connect_primary_db(timeout=600)
    conn.execute(DDL); conn.commit()
    if a.codes:
        codes = a.codes.split(",")
    else:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM financial_field_verification ORDER BY 1").fetchall()]
    if a.limit:
        codes = codes[:a.limit]
    day = datetime.now().strftime("%Y%m%d")
    st = {"codes": len(codes), "ok": 0, "empty": 0, "rows": 0, "estimate_rows": 0}
    for i, code in enumerate(codes, 1):
        got = []
        for per in ("quarter", "annual"):
            d, p, net = fetch(code, per, day)
            if net:
                time.sleep(0.25)
            if d:
                got += [r + (datetime.now().isoformat(timespec="seconds"), str(p)) for r in parse(code, d, per)]
        if got:
            st["ok"] += 1; st["rows"] += len(got); st["estimate_rows"] += sum(1 for r in got if r[4])
            conn.executemany("""INSERT INTO naver_finance_api VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (stock_code, fiscal_year, quarter, is_estimate) DO UPDATE SET period_key=EXCLUDED.period_key, revenue=EXCLUDED.revenue,
                operating_profit=EXCLUDED.operating_profit, net_income=EXCLUDED.net_income, ni_parent=EXCLUDED.ni_parent,
                revenue_label=EXCLUDED.revenue_label, fetched_at=EXCLUDED.fetched_at, raw_file=EXCLUDED.raw_file""", got)
            # 실적이 나온 기간의 옛 추정치 행은 지운다(같은 기간 실적·추정치 동시 보관 방지)
            for r in got:
                if not r[4]:
                    conn.execute("DELETE FROM naver_finance_api WHERE stock_code=? AND fiscal_year=? AND quarter=? AND is_estimate", (r[0], r[1], r[2]))
            conn.commit()
        else:
            st["empty"] += 1
        if i % 200 == 0:
            print(f"{i}/{len(codes)} {st}", flush=True)
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
