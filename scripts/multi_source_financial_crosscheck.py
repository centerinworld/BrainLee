#!/usr/bin/env python3
"""
multi_source_financial_crosscheck.py — DART(anchor) vs FnGuide/Naver/Yahoo 다중소스 교차검증

사용자 지시(2026-08-26): "재무데이터 뿐만 아니라, 현금흐름표, 매입재료비, 감가상각비,
연결기준/별도기준 등의 모든 데이터에 대해서 dart뿐만 아니라 야후, naver, Fnguide등과
중복/다중 점검을 해야해"

범위:
  1. 손익 3종(revenue/operating_profit/net_income): DART vs FnGuide vs Naver vs Yahoo(yfinance)
  2. 현금흐름 6종(operating_cf/investing_cf/financing_cf/capex/depreciation/cash_end):
     DART vs FnGuide vs Yahoo (Naver는 현금흐름표를 별도 제공하지 않아 제외)
  3. 매입재료비(material_purchase): 외부 무료 소스가 존재하지 않아 내부정합성만 확인
     (cost_structure.cogs_pct 대비 과대/과소 여부) — 별도 카테고리로 정직하게 기록
  4. CFS/OFS 거버넌스: stock_collection_config.preferred_report_type 오버라이드가
     실제 financial_data 주 사용 report_type과 일치하는지만 확인(값 비교 아님)

원칙:
  - DART가 유일한 write 경로(anchor), 이 스크립트는 판정만 하고 DB를 임의로 고치지 않는다.
  - 외부 2개 이상과 DART가 불일치하면 review 큐(mismatch)로 분류, 자동수정 금지.
  - Yahoo/Naver/FnGuide 어느 하나라도 DART와 5% 이내 일치하면 confirmed로 간주.
  - 소스가 하나도 없으면 no_external_source로 정직하게 기록(불일치 아님).
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [multisrc] %(message)s")
log = logging.getLogger(__name__)

PG_URL = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
TOL = 0.05  # 5% 이내 일치로 간주

INCOME_FIELDS = ("revenue", "operating_profit", "net_income")
CF_FIELDS = ("operating_cf", "investing_cf", "financing_cf", "capex", "depreciation", "cash_end")

_YF_INCOME_MAP = {
    "revenue": "Total Revenue",
    "operating_profit": "Operating Income",
    "net_income": "Net Income",
}
_YF_CF_MAP = {
    "operating_cf": "Operating Cash Flow",
    "investing_cf": "Investing Cash Flow",
    "financing_cf": "Financing Cash Flow",
    "capex": "Capital Expenditure",
    "depreciation": "Depreciation And Amortization",
    "cash_end": "End Cash Position",
}


def ensure_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS multi_source_financial_mismatch_log (
            id SERIAL PRIMARY KEY,
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            field TEXT NOT NULL,
            category TEXT NOT NULL,
            dart_value DOUBLE PRECISION,
            fnguide_value DOUBLE PRECISION,
            naver_value DOUBLE PRECISION,
            yahoo_value DOUBLE PRECISION,
            status TEXT NOT NULL,
            note TEXT,
            checked_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(stock_code, year, field)
        )
    """)
    conn.commit()


def _ratio_close(a, b, tol=TOL) -> bool:
    if a is None or b is None or a == 0:
        return False
    try:
        return abs(a - b) / max(abs(a), abs(b), 1) <= tol
    except Exception:
        return False


def _yf_ticker(stock_code: str, market: str):
    import yfinance as yf
    suffix = "KQ" if market == "KOSDAQ" else "KS"
    return yf.Ticker(f"{stock_code}.{suffix}")


def _yf_value(t, kind: str, field: str, year: int):
    """연도(회계연도 12월 결산 가정, 다른 결산월은 근접월 허용)에 해당하는 야후 값 조회."""
    try:
        df = t.financials if kind == "income" else t.cashflow
    except Exception:
        return None
    if df is None or df.empty:
        return None
    row_name = (_YF_INCOME_MAP if kind == "income" else _YF_CF_MAP).get(field)
    if row_name is None or row_name not in df.index:
        return None
    row = df.loc[row_name]
    for ts, val in row.items():
        if hasattr(ts, "year") and ts.year == year and val is not None:
            try:
                fv = float(val)
                if fv != fv:  # NaN
                    continue
                return fv
            except Exception:
                continue
    return None


def check_income_statement(conn, limit: int = 40) -> dict:
    """DART 앵커 선정 주의(2026-08-26 세션, 2차 수정): 같은 (종목,연도,CFS) 조합에
    여러 후보 행이 있을 때 단순 '최신 created_at'만 보면, Q4역산/repair 스크립트가 나중에
    덮어쓴 placeholder 행(0.0 sentinel, 또는 operating_profit==net_income인 합성값)이
    더 오래된 정상 fnguide 행을 이겨버림 — 실측(001420/001520/000040) 전부 이 패턴으로
    확인. CFS 후보군 내에서 '오염 의심 행'(op=0 또는 ni=0 또는 op==ni)을 후순위로 돌려
    정상 행을 우선 선택하고, CFS 전체가 오염 의심일 때만 OFS로 폴백한다.
    또한 FnGuide/Naver/Yahoo 외부 소스는 전부 연결(CFS) 기준을 보고하므로, 이 교차검증
    스크립트에서는 stock_collection_config.preferred_report_type=OFS 오버라이드(주로
    지주사의 '실체' 표시용, financial_data 자체의 표시 정책)를 따르지 않고 CFS가 있으면
    항상 CFS를 우선한다 — 2026-07-19 확립된 'revenue는 항상 CFS' 원칙을 op/ni까지 확장."""
    cur = conn.cursor()
    cur.execute("""
        WITH cfs_ranked AS (
            SELECT fd.*, ROW_NUMBER() OVER (
                PARTITION BY fd.stock_code, fd.year
                ORDER BY
                    CASE WHEN fd.operating_profit=0 OR fd.net_income=0
                              OR fd.operating_profit=fd.net_income
                         THEN 1 ELSE 0 END,
                    fd.created_at DESC NULLS LAST
            ) AS rn
            FROM financial_data fd
            WHERE fd.is_annual AND fd.revenue IS NOT NULL AND fd.year >= 2019 AND fd.report_type='CFS'
        ),
        ofs_ranked AS (
            SELECT fd.*, ROW_NUMBER() OVER (
                PARTITION BY fd.stock_code, fd.year
                ORDER BY fd.created_at DESC NULLS LAST
            ) AS rn
            FROM financial_data fd
            WHERE fd.is_annual AND fd.revenue IS NOT NULL AND fd.year >= 2019 AND fd.report_type='OFS'
        ),
        cfs AS (SELECT * FROM cfs_ranked WHERE rn=1),
        ofs AS (SELECT * FROM ofs_ranked WHERE rn=1)
        SELECT COALESCE(cfs.stock_code, ofs.stock_code) AS stock_code,
               COALESCE(cfs.year, ofs.year) AS year,
               su.market,
               CASE WHEN cfs.stock_code IS NULL AND ofs.stock_code IS NOT NULL THEN ofs.revenue
                    -- CFS 최선 후보조차 오염 의심(그 종목/연도의 CFS 전체가 오염)이면 OFS로 폴백
                    WHEN (cfs.operating_profit=0 OR cfs.net_income=0 OR cfs.operating_profit=cfs.net_income)
                         AND cfs.revenue>0 AND ofs.stock_code IS NOT NULL THEN ofs.revenue
                    WHEN cfs.stock_code IS NOT NULL THEN cfs.revenue
                    ELSE ofs.revenue END AS revenue,
               CASE WHEN cfs.stock_code IS NULL AND ofs.stock_code IS NOT NULL THEN ofs.operating_profit
                    WHEN (cfs.operating_profit=0 OR cfs.net_income=0 OR cfs.operating_profit=cfs.net_income)
                         AND cfs.revenue>0 AND ofs.stock_code IS NOT NULL THEN ofs.operating_profit
                    WHEN cfs.stock_code IS NOT NULL THEN cfs.operating_profit
                    ELSE ofs.operating_profit END AS op,
               CASE WHEN cfs.stock_code IS NULL AND ofs.stock_code IS NOT NULL THEN ofs.net_income
                    WHEN (cfs.operating_profit=0 OR cfs.net_income=0 OR cfs.operating_profit=cfs.net_income)
                         AND cfs.revenue>0 AND ofs.stock_code IS NOT NULL THEN ofs.net_income
                    WHEN cfs.stock_code IS NOT NULL THEN cfs.net_income
                    ELSE ofs.net_income END AS ni
        FROM cfs
        FULL OUTER JOIN ofs ON ofs.stock_code=cfs.stock_code AND ofs.year=cfs.year
        JOIN stock_universe su ON su.stock_code = COALESCE(cfs.stock_code, ofs.stock_code)
        LEFT JOIN multi_source_financial_mismatch_log m
               ON m.stock_code = COALESCE(cfs.stock_code, ofs.stock_code)
              AND m.year = COALESCE(cfs.year, ofs.year) AND m.field = 'revenue'
        WHERE m.id IS NULL
        ORDER BY COALESCE(cfs.year, ofs.year) DESC
        LIMIT %s
    """, (limit,))
    targets = cur.fetchall()
    log.info("손익 3종 대상 %d건", len(targets))

    counts = {"confirmed": 0, "mismatch": 0, "no_external_source": 0}
    for stock_code, year, market, revenue, op, ni in targets:
        dart_vals = {"revenue": revenue, "operating_profit": op, "net_income": ni}

        cur.execute("""
            SELECT revenue, operating_profit, net_income FROM naver_financial
            WHERE stock_code=%s AND year=%s AND is_annual=1
            ORDER BY collected_at DESC LIMIT 1
        """, (stock_code, year))
        row = cur.fetchone()
        naver_vals = {"revenue": row[0], "operating_profit": row[1], "net_income": row[2]} if row else {}

        fg_vals = {}
        try:
            from collectors.fnguide_financial_collector import fetch_fnguide_all
            fg_data = fetch_fnguide_all(stock_code, "CFS", annual_only=True)
            fg_vals = fg_data.get("annual", {}).get(year, {})
        except Exception as e:
            log.debug("FnGuide 조회 실패 %s: %s", stock_code, e)

        yf_vals = {}
        try:
            t = _yf_ticker(stock_code, market)
            for f in INCOME_FIELDS:
                yf_vals[f] = _yf_value(t, "income", f, year)
        except Exception as e:
            log.debug("Yahoo 조회 실패 %s: %s", stock_code, e)

        for field in INCOME_FIELDS:
            dv = dart_vals.get(field)
            if dv is None:
                continue
            nv = naver_vals.get(field)
            fv = fg_vals.get(field)
            yv = yf_vals.get(field)
            available = [v for v in (nv, fv, yv) if v is not None]
            if not available:
                status, note = "no_external_source", "naver/fnguide/yahoo 전부 미확보"
                counts["no_external_source"] += 1
            elif any(_ratio_close(dv, v) for v in available):
                status, note = "confirmed", "최소 1개 외부소스와 5%이내 일치"
                counts["confirmed"] += 1
            else:
                status, note = "mismatch", "DART가 확보된 외부소스 전체와 5%초과 불일치"
                counts["mismatch"] += 1

            cur.execute("""
                INSERT INTO multi_source_financial_mismatch_log
                    (stock_code, year, field, category, dart_value, fnguide_value, naver_value, yahoo_value, status, note)
                VALUES (%s,%s,%s,'income_statement',%s,%s,%s,%s,%s,%s)
                ON CONFLICT (stock_code, year, field) DO UPDATE SET
                    dart_value=EXCLUDED.dart_value, fnguide_value=EXCLUDED.fnguide_value,
                    naver_value=EXCLUDED.naver_value, yahoo_value=EXCLUDED.yahoo_value,
                    status=EXCLUDED.status, note=EXCLUDED.note, checked_at=NOW()
            """, (stock_code, year, field, dv, fv, nv, yv, status, note))
        conn.commit()
        time.sleep(0.3)
    return counts


def check_cash_flow(conn, limit: int = 30) -> dict:
    """DART 앵커 선정 주의(2026-08-26 세션, 2차 수정): 000590/000640 등 실측 결과, operating_cf/
    investing_cf/financing_cf/capex/cash_end는 CFS 행이 어떤 data_source(fnguide/NULL_seibro 등)를
    갖든 FnGuide/Yahoo 외부소스와 잘 일치하는 반면, 기존 화이트리스트(dart/dart_api_unified 등)는
    OFS(별도재무제표) 행만 통과시켜 완전히 다른 회계실체(연결 vs 별도)를 비교하게 만드는 문제가
    있었음 — 이 필드들은 "CFS 우선(어떤 소스든), 없으면 OFS 폴백"으로 전환.
    반면 depreciation은 000040/000070 실측에서 'fnguide'와 'NULL_seibro' 두 CFS 후보가 서로 3배
    가까이 차이나는 등 provenance 신뢰가 얕은 소스 간에도 값이 크게 갈렸음(직접법 현금흐름표라
    DART 원문에 감가상각 항목 자체가 없는 회사도 있음) — depreciation만 기존처럼 엄격한 DART
    화이트리스트를 유지(없으면 비교 스킵, 억지로 채우지 않음)."""
    dart_whitelist = "('dart', 'dart_api_unified', 'dart_cfs_requery', 'dart_redownload', 'dart_ofs_backfill', 'dart_q2_verified')"
    cur = conn.cursor()
    cur.execute(f"""
        WITH cfs_wide AS (
            SELECT cf.*, ROW_NUMBER() OVER (
                PARTITION BY cf.stock_code, cf.year
                ORDER BY cf.created_at DESC NULLS LAST
            ) AS rn
            FROM cash_flow_data cf
            WHERE cf.is_annual AND cf.report_type='CFS' AND cf.year >= 2019
                  AND cf.operating_cf IS NOT NULL
        ),
        cfs_strict AS (
            SELECT cf.*, ROW_NUMBER() OVER (
                PARTITION BY cf.stock_code, cf.year
                ORDER BY cf.created_at DESC NULLS LAST
            ) AS rn
            FROM cash_flow_data cf
            WHERE cf.is_annual AND cf.report_type='CFS' AND cf.year >= 2019
                  AND cf.depreciation IS NOT NULL
                  AND cf.data_source IN {dart_whitelist}
        ),
        ofs AS (
            SELECT cf.*, ROW_NUMBER() OVER (
                PARTITION BY cf.stock_code, cf.year
                ORDER BY cf.created_at DESC NULLS LAST
            ) AS rn
            FROM cash_flow_data cf
            WHERE cf.is_annual AND cf.report_type='OFS' AND cf.year >= 2019
                  AND cf.operating_cf IS NOT NULL
                  AND cf.data_source IN {dart_whitelist}
        ),
        cw AS (SELECT * FROM cfs_wide WHERE rn=1),
        cs AS (SELECT * FROM cfs_strict WHERE rn=1),
        ofsr AS (SELECT * FROM ofs WHERE rn=1)
        SELECT COALESCE(cw.stock_code, ofsr.stock_code) AS stock_code,
               COALESCE(cw.year, ofsr.year) AS year,
               su.market,
               COALESCE(cw.operating_cf, ofsr.operating_cf) AS operating_cf,
               COALESCE(cw.investing_cf, ofsr.investing_cf) AS investing_cf,
               COALESCE(cw.financing_cf, ofsr.financing_cf) AS financing_cf,
               COALESCE(cw.capex, ofsr.capex) AS capex,
               cs.depreciation AS depreciation,
               COALESCE(cw.cash_end, ofsr.cash_end) AS cash_end
        FROM cw
        FULL OUTER JOIN ofsr ON ofsr.stock_code=cw.stock_code AND ofsr.year=cw.year
        LEFT JOIN cs ON cs.stock_code=COALESCE(cw.stock_code, ofsr.stock_code)
                    AND cs.year=COALESCE(cw.year, ofsr.year)
        JOIN stock_universe su ON su.stock_code = COALESCE(cw.stock_code, ofsr.stock_code)
        LEFT JOIN multi_source_financial_mismatch_log m
               ON m.stock_code = COALESCE(cw.stock_code, ofsr.stock_code)
              AND m.year = COALESCE(cw.year, ofsr.year) AND m.field = 'operating_cf'
        WHERE m.id IS NULL AND COALESCE(cw.operating_cf, ofsr.operating_cf) IS NOT NULL
        ORDER BY COALESCE(cw.year, ofsr.year) DESC
        LIMIT %s
    """, (limit,))
    targets = cur.fetchall()
    log.info("현금흐름 6종 대상 %d건", len(targets))

    counts = {"confirmed": 0, "mismatch": 0, "no_external_source": 0}
    for stock_code, year, market, ocf, icf, fcf, capex, dep, cend in targets:
        dart_vals = {"operating_cf": ocf, "investing_cf": icf, "financing_cf": fcf,
                     "capex": capex, "depreciation": dep, "cash_end": cend}

        fg_vals = {}
        try:
            from collectors.fnguide_financial_collector import fetch_fnguide_all
            fg_data = fetch_fnguide_all(stock_code, "CFS", annual_only=True)
            fg_vals = fg_data.get("annual", {}).get(year, {})
        except Exception as e:
            log.debug("FnGuide CF 조회 실패 %s: %s", stock_code, e)

        yf_vals = {}
        try:
            t = _yf_ticker(stock_code, market)
            for f in CF_FIELDS:
                yf_vals[f] = _yf_value(t, "cf", f, year)
        except Exception as e:
            log.debug("Yahoo CF 조회 실패 %s: %s", stock_code, e)

        for field in CF_FIELDS:
            dv = dart_vals.get(field)
            if dv is None:
                continue
            fv = fg_vals.get(field)
            yv = yf_vals.get(field)
            available = [v for v in (fv, yv) if v is not None]
            if not available:
                status, note = "no_external_source", "fnguide/yahoo 전부 미확보"
                counts["no_external_source"] += 1
            # capex는 소스마다 부호관행이 다름(DART=취득액 양수, Yahoo/FnGuide=현금유출 음수)
            # — 크기(절대값) 기준으로만 비교, 그 외 필드는 부호 자체가 의미(순유입/유출)이므로 그대로 비교
            elif field == "capex" and any(_ratio_close(abs(dv), abs(v)) for v in available):
                status, note = "confirmed", "최소 1개 외부소스와 5%이내 일치(절대값 기준, 부호관행차이 보정)"
                counts["confirmed"] += 1
            elif field != "capex" and any(_ratio_close(dv, v) for v in available):
                status, note = "confirmed", "최소 1개 외부소스와 5%이내 일치"
                counts["confirmed"] += 1
            else:
                status, note = "mismatch", "DART가 확보된 외부소스 전체와 5%초과 불일치"
                counts["mismatch"] += 1

            cur.execute("""
                INSERT INTO multi_source_financial_mismatch_log
                    (stock_code, year, field, category, dart_value, fnguide_value, naver_value, yahoo_value, status, note)
                VALUES (%s,%s,%s,'cash_flow',%s,%s,NULL,%s,%s,%s)
                ON CONFLICT (stock_code, year, field) DO UPDATE SET
                    dart_value=EXCLUDED.dart_value, fnguide_value=EXCLUDED.fnguide_value,
                    yahoo_value=EXCLUDED.yahoo_value, status=EXCLUDED.status, note=EXCLUDED.note, checked_at=NOW()
            """, (stock_code, year, field, dv, fv, yv, status, note))
        conn.commit()
        time.sleep(0.3)
    return counts


def check_material_purchase_internal(conn, limit: int = 100) -> dict:
    """외부 무료 소스가 없으므로 원가구조(cost_structure) 대비 내부정합성만 확인.
    dart_material_purchase.material_purchase_krw는 이미 원 단위로 정규화되어 저장됨.

    ⚠️ 2026-08-26 세션에서 발견·수정: cost_structure는 quarter=1/2/3에는 그 분기 단독 값을,
    quarter=4에는 연간 누적값(Annual, data_source에 'dart_material_annual' 등이 붙음)을 저장하는
    비일관 스키마다(034020/047810 실측 및 10종목 샘플로 재확인: q4/[q1+q2+q3] 비율이 대부분
    1.3~1.5배 — Q4가 4분기 단독이 아니라 연간누적임을 시사). 기존 SUM(total_cogs)은 이 두 성격이
    다른 값을 그대로 더해 (a) 4분기까지 다 모이면 연간COGS를 거의 2배로 부풀리고 (b) 아직 1분기만
    수집된 해(연말 배치가 아직 안 돈 최신연도)는 1분기치만 남아 연간COGS를 4~5배 과소평가한다.
    → quarter=4 행의 total_cogs(연간누적)을 최우선으로 쓰고, 없을 때만 cogs_ratio_avg×연매출로
    대체(분기별 원가율은 대체로 안정적이라 결측분기에 강건함)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT mp.stock_code, mp.year, mp.material_purchase_krw,
               fd.revenue,
               (SELECT cs.total_cogs FROM cost_structure cs
                WHERE cs.stock_code=mp.stock_code AND cs.year=mp.year AND cs.quarter=4
                      AND cs.total_cogs IS NOT NULL
                ORDER BY cs.collected_at DESC NULLS LAST LIMIT 1) AS cogs_annual,
               (SELECT AVG(cs.cogs_ratio) FROM cost_structure cs
                WHERE cs.stock_code=mp.stock_code AND cs.year=mp.year AND cs.cogs_ratio IS NOT NULL) AS cogs_ratio_avg
        FROM dart_material_purchase mp
        JOIN financial_data fd ON fd.stock_code=mp.stock_code AND fd.year=mp.year
                                  AND fd.is_annual AND fd.report_type='CFS'
        LEFT JOIN multi_source_financial_mismatch_log m
               ON m.stock_code=mp.stock_code AND m.year=mp.year AND m.field='material_purchase'
        WHERE m.id IS NULL AND fd.revenue > 0 AND mp.material_purchase_krw IS NOT NULL
              AND mp.period_type = 'annual'
        ORDER BY mp.year DESC
        LIMIT %s
    """, (limit,))
    targets = cur.fetchall()
    log.info("매입재료비 내부정합성 대상 %d건", len(targets))

    counts = {"confirmed": 0, "mismatch": 0, "no_external_source": 0}
    for stock_code, year, amount_원, revenue, cogs_annual, cogs_ratio_avg in targets:
        if amount_원 is None:
            continue
        cogs_amount = float(cogs_annual) if cogs_annual else (revenue * (float(cogs_ratio_avg) / 100.0) if cogs_ratio_avg else None)
        if cogs_amount is None or cogs_amount <= 0:
            status, note = "no_external_source", "cost_structure 미확보로 내부대조 불가"
            counts["no_external_source"] += 1
        elif 0 <= amount_원 <= cogs_amount * 1.5:
            status, note = "confirmed", f"매입재료비가 매출원가({cogs_amount:,.0f}원)의 1.5배 이내"
            counts["confirmed"] += 1
        else:
            status, note = "mismatch", f"매입재료비({amount_원:,.0f}원)가 매출원가({cogs_amount:,.0f}원) 대비 비정상적으로 큼(외부소스 없음, 재검토 필요)"
            counts["mismatch"] += 1

        cur.execute("""
            INSERT INTO multi_source_financial_mismatch_log
                (stock_code, year, field, category, dart_value, status, note)
            VALUES (%s,%s,'material_purchase','material_purchase_internal',%s,%s,%s)
            ON CONFLICT (stock_code, year, field) DO UPDATE SET
                dart_value=EXCLUDED.dart_value, status=EXCLUDED.status, note=EXCLUDED.note, checked_at=NOW()
        """, (stock_code, year, amount_원, status, note))
    conn.commit()
    return counts


def check_cfs_ofs_governance(conn) -> dict:
    """preferred_report_type 오버라이드가 지정하는 재무제표 기준이 실제로 한 번도
    수집된 적이 없는(진짜 데이터 공백) 종목만 찾는다. 같은 종목에 CFS/OFS 두 유형이
    함께 존재하는 것 자체는 정상(fnguide 보강행 등)이므로 다수결 비교는 오탐만 낸다."""
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, config_value FROM stock_collection_config
        WHERE config_key='preferred_report_type'
    """)
    overrides = dict(cur.fetchall())
    missing = []
    for code, preferred in overrides.items():
        cur.execute("""
            SELECT COUNT(*) FROM financial_data
            WHERE stock_code=%s AND is_annual AND report_type=%s
        """, (code, preferred))
        (cnt,) = cur.fetchone()
        if cnt == 0:
            missing.append((code, preferred))
    return {"overrides_total": len(overrides), "preferred_type_never_collected": len(missing), "examples": missing[:10]}


def main():
    conn = psycopg.connect(PG_URL)
    ensure_tables(conn)

    report = {"date": datetime.now().isoformat(timespec="seconds"), "sections": {}}
    report["sections"]["income_statement"] = check_income_statement(conn, limit=40)
    report["sections"]["cash_flow"] = check_cash_flow(conn, limit=30)
    report["sections"]["material_purchase_internal"] = check_material_purchase_internal(conn, limit=100)
    report["sections"]["cfs_ofs_governance"] = check_cfs_ofs_governance(conn)

    out_dir = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/multi_source_crosscheck")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"crosscheck_{date.today().strftime('%Y%m%d')}.json"
    import json
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    log.info("완료: %s", json.dumps(report, ensure_ascii=False, default=str))
    log.info("리포트 저장: %s", out_path)


if __name__ == "__main__":
    main()
