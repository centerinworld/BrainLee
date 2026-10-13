#!/usr/bin/env python3
"""현행 기준(FINANCIAL_STATEMENTS.md §2-2) 필드 단위 확정 상태 테이블 `financial_field_verification` 재구축(2026-10-03).

Codex 보강 의견 §9-1-3('필드 단위 확정 상태 스키마화')·§9-1-2('품질 라벨 fail-closed')의 근거 테이블.
외부 값은 '실제로 FnGuide에서 받은 값'만 쓴다:
  1) FnGuide wcomp 원문(data_raw/fnguide_wcomp, fetch_fnguide_raw_20261003.py) — 연결·별도, 연간·분기, 백만원 정밀도
  2) 원문이 없는 종목·기간은 financial_source_snapshot 중 wcomp URL 행(연간, 2026-08 이후) — 구 comp 캡처는 파싱 오류가 많아 제외
  3) 네이버(naver_financial, 연결 매출·영업이익)는 보조 확인
상태: 확정(3소스) / 확정(2소스) / 원인 조사 / 미확인(DB 없음). 외부 값이 없는 필드는 행을 만들지 않는다(= 미확인).
허용오차: DB vs FnGuide 확정 = **절대 200만원**(`close_fg`; 외화 보고통화 종목만 max(200만원, 0.1%) `close_fx` — D-9 사용자 승인 2026-10-11; D-7 사용자 승인 2026-10-09 — 이전 max(200만원, 0.5%)는 억 단위 반올림값을 통과시켜 폐기), 네이버(억 단위) 보조 비교만 max(200만원, 0.5%)(`close`). 순이익·자본은 연결=지배주주, 별도=전체(§2-1). 기준 버전 basis_version='FS-2026-10-03'.
매 실행마다 전체 교체(run_id 기록). 읽기는 main.py /api/dashboard/data-quality.
"""
import collections
import json
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
    """네이버(억 단위 반올림) 보조 비교용 — 기존 허용오차."""
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def close_fx(a, b):
    """D-9(사용자 승인 2026-10-11): 외화 보고통화 종목(fs_quirk:reporting_currency)은 환율 소수·원천 기준일 차이로 200만원 절대 기준을
    원리상 맞추기 어렵다 → max(200만원, |FnGuide| × 0.1%)(환산 스크립트 fix_fx_cells·P10-3과 같은 기준)."""
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.001)


def close_fg(a, b):
    """DB vs FnGuide(wcomp 백만원) 확정 판정 — 절대 200만원(D-7 사용자 승인 2026-10-09). 이전 max(200만원, 0.5%)는 억 단위 반올림값을 '확정'으로 통과시켰다."""
    return a is not None and b is not None and abs(a - b) <= 2e6


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
    # 네이버(2026-10-10): 모바일 API 재수집(naver_finance_api, 실적만·회계 키) 우선. 옛 naver_financial(05-27 단일 수집, 달력 분기 키·추정치 미판별)은
    # 새 표에 그 종목이 없을 때만. 원칙(사용자 10-10): 네이버는 FnGuide 값을 쓰므로 둘은 100% 일치해야 하고, 다르면 우리 파싱 오류로 가정(목록 기록).
    nv = {(r[0], r[1], 0 if r[3] else r[2]): (r[4], r[5]) for r in map(tuple, conn.execute(
        "SELECT stock_code, year, quarter, is_annual, revenue, operating_profit FROM naver_financial").fetchall())}
    try:
        new_nv = {(r[0], r[1], r[2]): (r[3], r[4]) for r in map(tuple, conn.execute(
            "SELECT stock_code, fiscal_year, quarter, revenue, operating_profit FROM naver_finance_api WHERE NOT is_estimate").fetchall())}
    except Exception:
        conn.rollback(); new_nv = {}
    new_codes = {k[0] for k in new_nv}
    nv = {k: v for k, v in nv.items() if k[0] not in new_codes}
    nv.update(new_nv)
    naver_mismatch = []
    fx_codes = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    db = {}
    for r in conn.execute("""SELECT f.stock_code,f.year,f.quarter,f.is_annual,f.report_type,f.revenue,f.operating_profit,f.net_income,
                                    f.total_assets,f.total_liabilities,f.total_equity,f.depreciation_amortization,
                                    cf.operating_cf,cf.investing_cf,cf.financing_cf,cf.capex,cf.depreciation,
                                    cf.operating_cf_q,cf.investing_cf_q,cf.financing_cf_q,cf.capex_q,cf.depreciation_q
                             FROM financial_data f LEFT JOIN cash_flow_data cf USING(stock_code,year,quarter,is_annual,report_type)
                             LEFT JOIN canonical_financial_data cn ON cn.stock_code=f.stock_code AND cn.year=f.year
                                  AND cn.is_annual=(CASE WHEN f.is_annual THEN 1 ELSE 0 END) AND cn.report_type=f.report_type
                                  AND cn.quarter=(CASE WHEN f.is_annual THEN 0 ELSE f.quarter END)
                             WHERE f.year>=2022
                             -- 2026-10-09: 원장 연간 행이 quarter 0·4 두 벌(약 2.9만 종목-연도, 절반은 값 다름)이라 정렬 없이 setdefault 하면
                             -- 어느 행으로 판정할지 정해지지 않았다 → 정본(canonical)이 고른 원장 행 우선, 다음 quarter 0, 다음 id 순
                             ORDER BY f.stock_code, f.year, f.report_type, f.is_annual, CASE WHEN f.is_annual THEN 0 ELSE f.quarter END,
                                      CASE WHEN cn.source_row_id = f.id THEN 0 ELSE 1 END, f.quarter, f.id""").fetchall():
        r = tuple(r)
        code, y, q, ann, fs = r[:5]
        k = (code, y, 0 if ann else q, fs)
        base = dict(zip(FIELDS[:6] + ["depreciation_amortization"], r[5:12]))
        cfv = r[12:17] if ann else r[17:22]
        base.update(zip(["operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"], cfv))
        if not ann:
            base.pop("depreciation_amortization")
        db.setdefault(k, base)
    # 2026-10-11: 현금흐름은 재무 행에 묶어 읽으면 ① 재무 행이 없을 때 ② 연간 두 벌(quarter 0·4)의 표기가 재무·현금흐름에서 다를 때
    # 원장 값이 있어도 'DB 없음'으로 나왔다 → 현금흐름 원장에서 직접 대표 행(정본이 고른 행 → quarter 0 → id)을 골라 덮어쓴다.
    cfdb = {}
    for r in conn.execute("""SELECT c.stock_code,c.year,c.quarter,c.is_annual,c.report_type,c.operating_cf,c.investing_cf,c.financing_cf,c.capex,c.depreciation,
                                    c.operating_cf_q,c.investing_cf_q,c.financing_cf_q,c.capex_q,c.depreciation_q
                             FROM cash_flow_data c
                             LEFT JOIN canonical_cashflow_data cn ON cn.stock_code=c.stock_code AND cn.year=c.year
                                  AND cn.is_annual=(CASE WHEN c.is_annual THEN 1 ELSE 0 END) AND cn.report_type=c.report_type
                                  AND cn.quarter=(CASE WHEN c.is_annual THEN 0 ELSE c.quarter END)
                             WHERE c.year>=2022 AND c.quarter IS NOT NULL
                             ORDER BY c.stock_code, c.year, c.report_type, c.is_annual, CASE WHEN c.is_annual THEN 0 ELSE c.quarter END,
                                      CASE WHEN cn.source_row_id = c.id THEN 0 ELSE 1 END, c.quarter, c.id""").fetchall():
        r = tuple(r)
        code, y, q, ann, fs = r[:5]
        k = (code, y, 0 if ann else q, fs)
        if k in cfdb:
            continue
        cfdb[k] = dict(zip(["operating_cf", "investing_cf", "financing_cf", "capex", "depreciation"], r[5:10] if ann else r[10:15]))
    for k, v in cfdb.items():
        db.setdefault(k, {}).update(v)
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
            if nval is not None and abs(nval - sv) > 5.1e7:  # 네이버 억원 표시 반올림(±0.5억) 밖 = 파싱 의심
                naver_mismatch.append((code, y, q, fs, f, sv, nval, "naver_finance_api" if code in new_codes else "naver_financial(옛)"))
            if dv is None:
                status, cause = "미확인(DB 없음)", None
            elif (close_fx(dv, sv) if code in fx_codes else close_fg(dv, sv)):
                status = "확정(3소스)" if nval is not None and close(nval, sv) else "확정(2소스)"
                cause = None
            else:
                status = "원인 조사"
                if nval is not None and close(nval, sv):
                    cause = "외부 2곳 일치·DB 다름(재작성 미반영 또는 DB 오류)"
                elif nval is not None and close(nval, dv):
                    cause = "네이버=DB, FnGuide 단독 차이"
                elif f in ("depreciation", "depreciation_amortization"):
                    cause = "감가상각: 현금흐름표 조정값 미수집(유형자산 값으로 채워진 행) — XBRL 재수집 대기"
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
    # 2026-10-09 PLAN P8: 세 지표(대조분 일치율·전체 확정률·원 단위 정밀도)를 영역별로 매일 기록 — 진척 추적(FINANCIAL §7 '지표 이름 고정')
    areas = {"손익·재무상태": {"revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"},
             "현금흐름": {"operating_cf", "investing_cf", "financing_cf"}, "CapEx": {"capex"},
             "감가상각": {"depreciation", "depreciation_amortization"}}
    areas["합계"] = set().union(*areas.values())
    conn.execute("""CREATE TABLE IF NOT EXISTS data_quality_metrics_daily (run_id TEXT, measured_at TEXT, area TEXT, total_cells INT,
                    confirmed INT, investigating INT, db_missing INT, confirmed_eok_rounded INT, match_rate_compared DOUBLE PRECISION,
                    confirm_rate_all DOUBLE PRECISION, precision_rate DOUBLE PRECISION)""")
    # 2026-10-09 D-1(조건부 승인): '설명된 예외'는 분모에서 빼지 않는다 — 외부 확정률(confirm_rate_all)과 별도로 해결률을 기록.
    # 해결 = 확정 + 원문 근거가 붙은 설명된 예외(financial_field_exception, 미확정 칸만) + 대상 아님(같은 표 reason_code='NOT_APPLICABLE')
    conn.execute("""CREATE TABLE IF NOT EXISTS financial_field_exception (stock_code TEXT, year INT, quarter INT, report_type TEXT, field TEXT,
                    reason_code TEXT, dart_evidence TEXT, db_value DOUBLE PRECISION, fnguide_value DOUBLE PRECISION, naver_value DOUBLE PRECISION,
                    exclusion_reason TEXT, approved_basis TEXT, run_id TEXT, created_at TEXT,
                    PRIMARY KEY (stock_code, year, quarter, report_type, field))""")
    for col in ("explained_exception INT", "not_applicable INT", "resolution_rate DOUBLE PRECISION", "exception_share DOUBLE PRECISION"):
        try:
            conn.execute(f"ALTER TABLE data_quality_metrics_daily ADD COLUMN IF NOT EXISTS {col}")
        except Exception:
            conn.rollback()
    exc = {tuple(r[:5]): r[5] for r in conn.execute(
        "SELECT stock_code, year, quarter, report_type, field, reason_code FROM financial_field_exception").fetchall()}
    # 2026-10-10(사용자·외부 검토): 확정률을 하나로 말하지 않는다. kpi_scope로 분리 기록 —
    #   '전체(리츠 포함)'(이전과 같은 정의) · '일반·전체'(리츠 분리) · '일반·고정분모'(기준선 칸만 = 같은 칸끼리 개선률) ·
    #   '일반·신규 대조'(기준선 뒤 새로 들어온 칸 — 기존 품질 악화로 읽지 말 것) · '리츠(6개월 결산)'(D-8: 일반 KPI에서 분리, 별도 추적)
    for col in ("kpi_scope TEXT", "baseline_id TEXT"):
        try:
            conn.execute(f"ALTER TABLE data_quality_metrics_daily ADD COLUMN IF NOT EXISTS {col}")
        except Exception:
            conn.rollback()
    reit = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE (config_key='fs_quirk:fiscal_period' AND config_value LIKE 'REIT%%')
        OR (config_key='entity_category' AND config_value='reit') UNION SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'""").fetchall()}
    try:
        bl = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()
        baseline_id = bl[0] if bl else None
        base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?",
                                               (baseline_id,)).fetchall()} if baseline_id else set()
    except Exception:
        conn.rollback(); baseline_id, base = None, set()
    scopes = [("전체(리츠 포함)", rows), ("일반·전체", [r for r in rows if r[0] not in reit]), ("리츠(6개월 결산)", [r for r in rows if r[0] in reit])]
    if base:
        scopes += [("일반·고정분모", [r for r in rows if r[0] not in reit and tuple(r[:5]) in base]),
                   ("일반·신규 대조", [r for r in rows if r[0] not in reit and tuple(r[:5]) not in base])]
    for scope, srows in scopes:
      for area, fs_ in areas.items():
        sub = [r for r in srows if r[4] in fs_]
        cf = [r for r in sub if r[8].startswith("확정")]
        inv = sum(1 for r in sub if r[8].startswith("원인"))
        miss = sum(1 for r in sub if r[8].startswith("미확인"))
        eok = sum(1 for r in cf if r[5] not in (None, 0) and abs(r[5]) % 1e8 < 0.5 and r[6] is not None and abs(r[6]) % 1e8 >= 0.5)
        un = [r for r in sub if not r[8].startswith("확정")]
        ex_n = sum(1 for r in un if exc.get(tuple(r[:5])) not in (None, "NOT_APPLICABLE"))
        na_n = sum(1 for r in un if exc.get(tuple(r[:5])) == "NOT_APPLICABLE")
        conn.execute("""INSERT INTO data_quality_metrics_daily (run_id, measured_at, area, total_cells, confirmed, investigating, db_missing,
                        confirmed_eok_rounded, match_rate_compared, confirm_rate_all, precision_rate, explained_exception, not_applicable,
                        resolution_rate, exception_share, kpi_scope, baseline_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (run_id, now, area, len(sub), len(cf), inv, miss, eok,
                      round(100 * len(cf) / (len(cf) + inv), 4) if cf or inv else None,
                      round(100 * len(cf) / len(sub), 4) if sub else None,
                      round(100 * (len(cf) - eok) / len(cf), 4) if cf else None,
                      ex_n, na_n,
                      round(100 * (len(cf) + ex_n + na_n) / len(sub), 4) if sub else None,
                      round(100 * ex_n / len(sub), 4) if sub else None, scope, baseline_id if "고정" in scope or "신규" in scope else None))
    conn.commit()
    # 네이버 ≠ FnGuide 목록(파싱 의심, 2026-10-10 원칙) — 매 실행 덮어씀
    _mm = json.dumps({"run_id": run_id, "count": len(naver_mismatch), "by_source": dict(collections.Counter(x[7] for x in naver_mismatch)),
                      "rows": naver_mismatch}, ensure_ascii=False, indent=1, default=str)
    _od = ROOT / "research_outputs" / "financial_rereview_20261002"
    (_od / "naver_fnguide_mismatch_latest.json").write_text(_mm)
    (_od / "naver_fnguide_mismatch_history").mkdir(exist_ok=True)
    (_od / "naver_fnguide_mismatch_history" / f"{run_id}.json").write_text(_mm)  # 10-10: latest 덮어쓰기로 직전 목록이 사라지지 않게
    print(run_id, len(rows), "필드", dict(st), "네이버≠FnGuide", len(naver_mismatch))


if __name__ == "__main__":
    main()
