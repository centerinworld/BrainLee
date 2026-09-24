"""
routes/peer_compare.py — 동종기업(유사기업) 비교 API

  GET /api/peer-compare/compare?codes=290550,094970,054040
      → 종목별 기본정보/밸류에이션/재무추이/매출구성비 + 상대 저평가 판정

데이터 소스:
  - stock_universe        : 시가총액/PER/PBR/ROE/ROA
  - financial_data(연간)   : 매출/영업이익/순이익/자산/자본 추이(최근 5개년)
  - company_product_mix    : DART 사업보고서 '매출 및 수주상황' 기반 품목별 매출구성비
                              (collectors/dart_product_mix_collector.py로 수집)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from db_compat import connect_primary_db

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_PEERS = 6


def _num(v):
    return None if v is None else round(float(v), 4)


@router.get("/compare")
def compare_peers(codes: str = Query(..., description="쉼표구분 종목코드 (2~6개)")):
    stock_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if len(stock_codes) < 2:
        raise HTTPException(status_code=400, detail="비교할 종목을 2개 이상 입력해주세요.")
    if len(stock_codes) > MAX_PEERS:
        raise HTTPException(status_code=400, detail=f"최대 {MAX_PEERS}개까지 비교할 수 있습니다.")

    conn = connect_primary_db(readonly=True)
    conn.row_factory = None
    import sqlite3
    conn.row_factory = sqlite3.Row

    companies = []
    for code in stock_codes:
        row = conn.execute(
            """SELECT stock_code, stock_name, market, sector_large, sector_mid,
                      market_cap, per, pbr, roe, roa, shares_issued
               FROM stock_universe WHERE stock_code=?""",
            (code,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"종목코드 {code}를 찾을 수 없습니다.")

        price_row = conn.execute(
            "SELECT close, date FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code,),
        ).fetchone()

        # 최근 5개년 연간 재무 추이 (연결(CFS) 우선, 동일 연도 중복은 최신 id 채택)
        fin_rows = conn.execute(
            """SELECT year, revenue, operating_profit, net_income, total_assets, total_equity, eps, bps, report_type, id
               FROM financial_data
               WHERE stock_code=? AND is_annual=1 AND revenue IS NOT NULL
               ORDER BY year DESC, id ASC""",
            (code,),
        ).fetchall()
        by_year: dict[int, dict] = {}
        for r in fin_rows:
            y = r["year"]
            prev = by_year.get(y)
            # CFS 우선, 동일 report_type이면 더 최근에 적재된(id 큰) 값으로 갱신
            if prev is None or (r["report_type"] == "CFS" and prev["report_type"] != "CFS") or (
                r["report_type"] == prev["report_type"] and r["id"] > prev["id"]
            ):
                by_year[y] = r
        fin_rows = [by_year[y] for y in sorted(by_year.keys(), reverse=True)[:5]]
        trend = [
            {
                "year": r["year"],
                "revenue_억": _num((r["revenue"] or 0) / 1e8),
                "operating_profit_억": _num((r["operating_profit"] or 0) / 1e8),
                "net_income_억": _num((r["net_income"] or 0) / 1e8),
                "total_assets_억": _num((r["total_assets"] or 0) / 1e8),
                "total_equity_억": _num((r["total_equity"] or 0) / 1e8),
            }
            for r in reversed(fin_rows)
        ]
        latest = trend[-1] if trend else None

        # 매출 구성비 (최신 연도)
        mix_year_row = conn.execute(
            "SELECT MAX(year) AS y FROM company_product_mix WHERE stock_code=?", (code,)
        ).fetchone()
        mix_year = mix_year_row["y"] if mix_year_row else None
        product_mix = []
        if mix_year:
            mix_rows = conn.execute(
                """SELECT category, product_name, revenue_krw, revenue_pct
                   FROM company_product_mix
                   WHERE stock_code=? AND year=?
                   ORDER BY revenue_pct DESC""",
                (code, mix_year),
            ).fetchall()
            product_mix = [
                {
                    "category": r["category"],
                    "product_name": r["product_name"],
                    "revenue_억": _num((r["revenue_krw"] or 0) / 1e8),
                    "revenue_pct": _num(r["revenue_pct"]),
                }
                for r in mix_rows
            ]

        companies.append({
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "market": row["market"],
            "sector_large": row["sector_large"],
            "sector_mid": row["sector_mid"],
            "market_cap_억": _num(row["market_cap"]),
            "per": _num(row["per"]),
            "pbr": _num(row["pbr"]),
            "roe": _num(row["roe"]),
            "roa": _num(row["roa"]),
            "current_price": price_row["close"] if price_row else None,
            "price_date": price_row["date"] if price_row else None,
            "financial_trend": trend,
            "latest_annual": latest,
            "product_mix_year": mix_year,
            "product_mix": product_mix,
        })

    conn.close()

    _attach_valuation_verdict(companies)

    return {"companies": companies}


def _attach_valuation_verdict(companies: list[dict]) -> None:
    """PER/PBR/ROE 상대비교로 간단한 저평가/고평가 판정 부여 (참고용, 투자권유 아님)."""
    valid = [c for c in companies if c["per"] is not None and c["pbr"] is not None and c["per"] > 0 and c["pbr"] > 0]
    if len(valid) < 2:
        for c in companies:
            c["valuation_verdict"] = None
            c["valuation_score"] = None
        return

    pers = sorted(c["per"] for c in valid)
    pbrs = sorted(c["pbr"] for c in valid)
    roes = [c["roe"] for c in valid if c["roe"] is not None]

    def _rank_pct(sorted_vals, v):
        # 값이 작을수록(저PER/저PBR) 좋은 순위 → 0(가장 저평가)~1(가장 고평가)
        n = len(sorted_vals)
        if n <= 1:
            return 0.5
        idx = sorted_vals.index(v)
        return idx / (n - 1)

    for c in companies:
        if c not in valid:
            c["valuation_verdict"] = "밸류에이션 데이터 부족"
            c["valuation_score"] = None
            continue
        per_rank = _rank_pct(pers, c["per"])       # 낮을수록 저평가
        pbr_rank = _rank_pct(pbrs, c["pbr"])        # 낮을수록 저평가
        roe_avg = sum(roes) / len(roes) if roes else 0
        roe_bonus = 0.0
        if c["roe"] is not None and roes:
            # ROE가 평균보다 높으면 저평가 판정에 가산점(수익성 대비 저평가 가능성)
            roe_bonus = max(-0.15, min(0.15, (c["roe"] - roe_avg) / max(abs(roe_avg), 1) * 0.15))
        score = (1 - ((per_rank + pbr_rank) / 2)) + roe_bonus  # 높을수록 상대적 저평가
        score = round(max(0.0, min(1.0, score)), 3)
        c["valuation_score"] = score
        if score >= 0.66:
            c["valuation_verdict"] = "peer 대비 상대적 저평가"
        elif score <= 0.34:
            c["valuation_verdict"] = "peer 대비 상대적 고평가"
        else:
            c["valuation_verdict"] = "peer 대비 중간 수준"
