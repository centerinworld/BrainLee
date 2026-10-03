#!/usr/bin/env python3
"""현행 기준(FINANCIAL_STATEMENTS.md §2-2) 필드 단위 확정 상태 테이블 `financial_field_verification` 재구축(2026-10-03).

Codex 보강 의견 §9-1-3('필드 단위 확정 상태 스키마화')·§9-1-2('품질 라벨 fail-closed')의 근거 테이블.
외부 값은 '실제로 FnGuide에서 받은 값'만 쓴다:
  1) FnGuide wcomp 원문(data_raw/fnguide_wcomp, fetch_fnguide_raw_20261003.py) — 연결·별도, 연간·분기, 백만원 정밀도
  2) 원문이 없는 종목·기간은 financial_source_snapshot 중 wcomp URL 행(연간, 2026-08 이후) — 구 comp 캡처는 파싱 오류가 많아 제외
  3) 네이버(naver_financial, 연결 매출·영업이익)는 보조 확인
상태: 확정(3소스) / 확정(2소스) / 원인 조사 / 미확인(DB 없음). 외부 값이 없는 필드는 행을 만들지 않는다(= 미확인).
허용오차 max(200만원, 0.5%). 순이익·자본은 연결=지배주주, 별도=전체(§2-1). 기준 버전 basis_version='FS-2026-10-03'.
매 실행마다 전체 교체(run_id 기록). 읽기는 main.py /api/dashboard/data-quality.
"""
import collections
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from compare_db_vs_fnguide_raw_20261003 import RAW, parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

BASIS = "FS-2026-10-03"
FIELDS = ["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
          "operating_cf", "investing_cf", "financing_cf", "capex", "depreciation", "depreciation_amortization"]
ABS = {"capex", "depreciation", "depreciation_amortization"}
DDL = """CREATE TABLE IF NOT EXISTS financial_field_verification (
    stock_code TEXT NOT NULL, year INTEGER NOT NULL, quarter INTEGER NOT NULL, report_type TEXT NOT NULL, field TEXT NOT NULL,
    db_value DOUBLE PRECISION, fnguide_value DOUBLE PRECISION, naver_value DOUBLE PRECISION,
    status TEXT NOT NULL, cause TEXT, fnguide_source TEXT, fnguide_fetched TEXT,
    basis_version TEXT NOT NULL, verified_at TEXT NOT NULL, run_id TEXT NOT NULL,
    PRIMARY KEY (stock_code, year, quarter, report_type, field))"""


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def main():
    conn = connect_primary_db(timeout=900)
    conn.execute("SET statement_timeout='900s'")
    # 외부 값 수집: (code, y, q, fs) -> {field: (value, source, fetched)}
    ext = {}
    for p in (RAW.iterdir() if RAW.exists() else []):
        if not p.is_dir():
            continue
        res, day = parse_code(p.name)
        for (y, q, fs), v in res.items():
            if isinstance(q, str):
                continue
            ext[(p.name, y, q, fs)] = {f: (v[f], "fnguide_wcomp_raw", day) for f in FIELDS if f in v}
    snap_cols = ["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
                 "operating_cf", "investing_cf", "financing_cf"]
    for r in conn.execute(f"""SELECT DISTINCT ON (stock_code, year, report_type) stock_code, year, report_type, fetched_at, {','.join(snap_cols)}
                              FROM financial_source_snapshot WHERE data_source='fnguide' AND is_annual=1 AND source_url LIKE 'https://wcomp.fnguide.com%'
                              ORDER BY stock_code, year, report_type, fetched_at DESC""").fetchall():
        r = tuple(r)
        k = (r[0], r[1], 0, r[2])
        if k in ext:
            continue  # 원문이 우선
        # 스냅샷 수집기는 순이익·자본을 지배 우선으로 읽지만 일부 전체 기준이 섞임 → 순이익·자본은 원문에서만 확정
        ext[k] = {f: (v, "fnguide_wcomp_snapshot", str(r[3])) for f, v in zip(snap_cols, r[4:])
                  if v is not None and f not in ("net_income", "total_equity")}
    nv = {(r[0], r[1], 0 if r[3] else r[2]): (r[4], r[5]) for r in map(tuple, conn.execute(
        "SELECT stock_code, year, quarter, is_annual, revenue, operating_profit FROM naver_financial").fetchall())}
    db = {}
    for r in conn.execute("""SELECT f.stock_code,f.year,f.quarter,f.is_annual,f.report_type,f.revenue,f.operating_profit,f.net_income,
                                    f.total_assets,f.total_liabilities,f.total_equity,f.depreciation_amortization,
                                    cf.operating_cf,cf.investing_cf,cf.financing_cf,cf.capex,cf.depreciation,
                                    cf.operating_cf_q,cf.investing_cf_q,cf.financing_cf_q,cf.capex_q,cf.depreciation_q
                             FROM financial_data f LEFT JOIN cash_flow_data cf USING(stock_code,year,quarter,is_annual,report_type)
                             WHERE f.year>=2022""").fetchall():
        r = tuple(r)
        code, y, q, ann, fs = r[:5]
        k = (code, y, 0 if ann else q, fs)
        base = dict(zip(FIELDS[:6] + ["depreciation_amortization"], r[5:12]))
        cfv = r[12:17] if ann else r[17:22]
        base.update(zip(["operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"], cfv))
        if not ann:
            base.pop("depreciation_amortization")
        db.setdefault(k, base)
    run_id = f"field_verif_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    rows, st = [], collections.Counter()
    for (code, y, q, fs), fv in ext.items():
        d = db.get((code, y, q, fs)) or {}
        n = nv.get((code, y, q)) if fs == "CFS" else None
        for f, (sv, src, fetched) in fv.items():
            if f in ABS:
                sv = abs(sv)
            dv = d.get(f)
            if dv is not None and f in ABS:
                dv = abs(dv)
            nval = (n[0] if f == "revenue" else n[1] if f == "operating_profit" else None) if n else None
            if dv is None:
                status, cause = "미확인(DB 없음)", None
            elif close(dv, sv):
                status = "확정(3소스)" if nval is not None and close(nval, sv) else "확정(2소스)"
                cause = None
            else:
                status = "원인 조사"
                if nval is not None and close(nval, sv):
                    cause = "외부 2곳 일치·DB 다름(재작성 미반영 또는 DB 오류)"
                elif nval is not None and close(nval, dv):
                    cause = "네이버=DB, FnGuide 단독 차이"
                elif f in ("depreciation", "depreciation_amortization"):
                    cause = "감가상각 정의 미결(FnGuide=현금흐름표 조정 감가상각)"
                else:
                    cause = "미분류"
            st[status] += 1
            rows.append((code, y, q, fs, f, dv, sv, nval, status, cause, src, fetched, BASIS, now, run_id))
    conn.execute(DDL)
    conn.execute("DELETE FROM financial_field_verification")
    for i in range(0, len(rows), 5000):
        conn.executemany("""INSERT INTO financial_field_verification(stock_code,year,quarter,report_type,field,db_value,fnguide_value,naver_value,
                            status,cause,fnguide_source,fnguide_fetched,basis_version,verified_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         rows[i:i + 5000])
    conn.commit()
    print(run_id, len(rows), "필드", dict(st))


if __name__ == "__main__":
    main()
