"""AGI-06 기업 보고서 조립·저장 — 설계 문서를 코드로 옮긴 첫 구현.

정본: `docs/AGI_06_COMPANY_REPORT_DESIGN_20261013.md`,
`docs/AGI_06_SECTOR_REPORT_SAMPLES_20261013.md`(업종별 폼 + 손으로 만든 샘플 3건).

이 모듈은 **읽기만** 한다 — 기존 운영 테이블(`financial_data`, `company_product_mix`,
`segment_revenue`, `dart_disclosures`, `analyst_pdf_extracts`, `stock_universe`,
`sector_taxonomy_nodes`/`stock_sector_membership_v2`, `order_backlog`)을 그대로 가져와
섹션으로 조립하고, `company_report_sections`(migration 0003)에만 쓴다. 다른 테이블에는
쓰지 않는다.

섹션 단위 revision — 바뀐 섹션만 새 행
--------------------------------------
섹션마다 콘텐츠를 캐노니컬 JSON으로 만들어 해시한다. 같은 해시가 이미 있으면(콘텐츠가
그대로) 새 행을 만들지 않는다(`change_kind` 없음, 호출 결과에 `unchanged`로만 보고).
바뀌었으면 `revision_no + 1`로 새 행을 추가한다 — 이전 행은 지우지 않는다(과거 보고서
내용이 그대로 남는다, AGI-01의 revision 철학과 같다).

업종 템플릿
-----------
`classify_template()`이 종목코드 하나당 템플릿 하나를 고른다(현재 5종 — 설계 문서 §1).
템플릿은 "어떤 업종 고유 섹션을 추가로 만드는가"만 바꾸고, 공통 섹션(헤더·실적·사업구성·
최근공시)은 모든 템플릿에서 같다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from agi.db import qualify

TEMPLATE_SEMICONDUCTOR = "semiconductor"
TEMPLATE_FINANCIAL_HOLDING = "financial_holding"
TEMPLATE_MEDTECH = "medtech"
TEMPLATE_ORDER_BACKLOG = "order_backlog_industry"
# (2026-10-13 2차 — 사용자 지시로 업종 표본을 넓혀 추가 리포트 3건을 더 읽고 반영)
TEMPLATE_DIVIDEND_UTILITY = "dividend_utility"       # SK텔레콤 리포트: 배당 발표 시점·배당수익률이 투자논리의 핵심
TEMPLATE_HOLDING_SOTP = "holding_sotp"               # LG화학 리포트: 사업부별 EV/EBITDA + 자회사 지분가치 합산(SOTP)
TEMPLATE_MATERIALS_VALUE_CHAIN = "materials_value_chain"  # LS에코에너지 리포트: 정책 드라이버·capex·밸류체인 포지션
TEMPLATE_GENERIC = "generic"
TEMPLATE_KINDS = (
    TEMPLATE_SEMICONDUCTOR,
    TEMPLATE_FINANCIAL_HOLDING,
    TEMPLATE_MEDTECH,
    TEMPLATE_ORDER_BACKLOG,
    TEMPLATE_DIVIDEND_UTILITY,
    TEMPLATE_HOLDING_SOTP,
    TEMPLATE_MATERIALS_VALUE_CHAIN,
    TEMPLATE_GENERIC,
)

COMMON_SECTIONS = ("header", "financials", "business_mix", "recent_disclosures", "margin_gap")
SECTOR_SECTION_KEY = "sector_specific"
MULTI_SEGMENT_SHARE_THRESHOLD = 0.15  # 부문 2개 이상이 각 15%+ 면 "복합 사업"으로 본다(샘플 ③).


# --------------------------------------------------------------------- 분류
def classify_template(conn, stock_code: str) -> str:
    """종목 하나에 업종 템플릿 하나를 고른다. 모르면 `generic`(fail-soft — 공통 섹션만)."""
    row = conn.execute(
        "SELECT 1 FROM stock_sector_membership_v2 m JOIN sector_taxonomy_nodes n ON n.node_key = m.node_key "
        "WHERE m.stock_code = ? AND n.dimension = 'peer_group' AND n.name LIKE ?",
        (stock_code, "%OSAT%"),
    ).fetchone()
    if row is not None:
        return TEMPLATE_SEMICONDUCTOR

    universe = _row(
        conn, "SELECT stock_name, sector_large, sector_mid FROM stock_universe WHERE stock_code = ?", (stock_code,)
    )
    stock_name = str((universe or {}).get("stock_name") or "")
    sector_large = str((universe or {}).get("sector_large") or "")
    sector_mid = str((universe or {}).get("sector_mid") or "")
    if "금융" in sector_large:
        return TEMPLATE_FINANCIAL_HOLDING
    if "의료" in sector_large:
        return TEMPLATE_MEDTECH
    if "반도체" in sector_mid:
        return TEMPLATE_SEMICONDUCTOR

    # (2026-10-13 2차) 통신/유틸리티 — SK텔레콤 리포트: 투자논리의 중심이 배당(DPS 발표
    # 시점·배당수익률)이다. 업종명 매칭을 order_backlog 존재 여부보다 먼저 본다 —
    # order_backlog 는 조선·건설뿐 아니라 통신 장비·인프라 계약도 섞여 있다(실측: SK텔레콤도
    # 행이 있다). 업종이 분명하면 그 업종 템플릿이 우선한다.
    if any(key in sector_mid or key in sector_large for key in ("통신", "유틸리티", "전력", "가스")):
        return TEMPLATE_DIVIDEND_UTILITY
    # 지주회사·복합기업 SOTP — LG화학 리포트: 사업부+자회사 지분가치를 따로 더해 밸류에이션.
    if stock_name.endswith(("홀딩스", "지주")) or "지주" in sector_mid:
        return TEMPLATE_HOLDING_SOTP
    # 소재/밸류체인 — LS에코에너지 리포트: 정책 드라이버·capex·밸류체인 포지션이 핵심.
    if any(key in sector_mid for key in ("소재", "금속", "화학", "에너지")):
        return TEMPLATE_MATERIALS_VALUE_CHAIN

    # 2026-10-14 실측: order_backlog 에는 전부 NULL인 과거 placeholder 행도 섞여 있다
    # (예: AJ네트웍스 2020년 행). 행이 있다는 사실만으로는 부족하고, 실제 숫자가 있는
    # 행이 있어야 이 업종 템플릿을 쓴다(값을 보지 않고 모양만 보고 판정하지 않는다).
    backlog = conn.execute(
        "SELECT 1 FROM order_backlog WHERE stock_code = ? AND "
        "(backlog_normalized IS NOT NULL OR new_orders IS NOT NULL OR backlog_to_rev IS NOT NULL) LIMIT 1",
        (stock_code,),
    ).fetchone()
    if backlog is not None:
        return TEMPLATE_ORDER_BACKLOG
    return TEMPLATE_GENERIC


# --------------------------------------------------------------------- 공통 섹션
def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _row(conn, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    """`fetchone()` 결과를 평범한 dict로(없으면 None) — CompatRow 는 `.get()` 을 지원하지 않는다."""
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def section_header(conn, stock_code: str) -> dict[str, Any]:
    u = _row(
        conn,
        "SELECT stock_name, sector_large, sector_mid, market_cap, per, pbr, roe, shares_issued "
        "FROM stock_universe WHERE stock_code = ?",
        (stock_code,),
    )
    analyst = _row(
        conn,
        "SELECT target_price, opinion, fwd_eps_1y, extracted_at FROM analyst_pdf_extracts "
        "WHERE stock_code = ? ORDER BY extracted_at DESC LIMIT 1",
        (stock_code,),
    )
    return {
        "stock_name": (u or {}).get("stock_name"),
        "sector_large": (u or {}).get("sector_large"),
        "sector_mid": (u or {}).get("sector_mid"),
        "market_cap_억": (u or {}).get("market_cap"),
        "per": (u or {}).get("per"),
        "pbr": (u or {}).get("pbr"),
        "roe": (u or {}).get("roe"),
        "shares_issued": (u or {}).get("shares_issued"),
        "target_price": (analyst or {}).get("target_price"),
        "opinion": (analyst or {}).get("opinion"),
        "fwd_eps_1y": (analyst or {}).get("fwd_eps_1y"),
        "analyst_extracted_at": str((analyst or {}).get("extracted_at")) if analyst else None,
    }


def section_financials(conn, stock_code: str, *, quarters: int = 4) -> dict[str, Any]:
    """최근 N개 분기(연결 CFS 우선, 없으면 별도 OFS)를 오래된→최신 순으로 돌려준다."""
    rows = _rows(
        conn,
        "SELECT year, quarter, report_type, revenue, operating_profit, net_income FROM financial_data "
        "WHERE stock_code = ? AND NOT is_annual AND report_type = 'CFS' ORDER BY year DESC, quarter DESC LIMIT ?",
        (stock_code, quarters),
    )
    basis = "CFS"
    if not rows:
        rows = _rows(
            conn,
            "SELECT year, quarter, report_type, revenue, operating_profit, net_income FROM financial_data "
            "WHERE stock_code = ? AND NOT is_annual AND report_type = 'OFS' ORDER BY year DESC, quarter DESC LIMIT ?",
            (stock_code, quarters),
        )
        basis = "OFS"
    rows = list(reversed(rows))  # 오래된 분기가 먼저 오게
    quarters_out = []
    prev = None
    for r in rows:
        entry = {
            "year": r["year"], "quarter": r["quarter"],
            "revenue_억": _to_억(r["revenue"]), "operating_profit_억": _to_억(r["operating_profit"]),
            "net_income_억": _to_억(r["net_income"]),
        }
        if prev is not None and prev["revenue_억"] and entry["revenue_억"] is not None:
            entry["revenue_qoq_pct"] = round(100 * (entry["revenue_억"] - prev["revenue_억"]) / abs(prev["revenue_억"]), 1)
        quarters_out.append(entry)
        prev = entry
    return {"basis": basis, "quarters": quarters_out}


def section_business_mix(conn, stock_code: str) -> dict[str, Any]:
    row = _row(conn, "SELECT MAX(year) AS y FROM company_product_mix WHERE stock_code = ?", (stock_code,))
    year = (row or {}).get("y")
    items = []
    if year is not None:
        items = _rows(
            conn,
            "SELECT category, product_name, revenue_pct FROM company_product_mix "
            "WHERE stock_code = ? AND year = ? ORDER BY revenue_pct DESC LIMIT 8",
            (stock_code, year),
        )
    # 샘플 ③(AJ네트웍스)에서 확인한 규칙: 서로 다른 category 가 둘 이상 각 15%+ 면 복합 사업.
    by_category: dict[str, float] = {}
    for item in items:
        by_category[item["category"]] = by_category.get(item["category"], 0.0) + float(item.get("revenue_pct") or 0)
    major_segments = {k: v for k, v in by_category.items() if v >= MULTI_SEGMENT_SHARE_THRESHOLD * 100}
    return {
        "year": year,
        "items": items,
        "is_multi_segment": len(major_segments) >= 2,
        "major_segments_pct": major_segments,
    }


def section_recent_disclosures(conn, stock_code: str, *, limit: int = 5) -> dict[str, Any]:
    rows = _rows(
        conn,
        "SELECT rcept_dt, report_nm FROM dart_disclosures WHERE stock_code = ? ORDER BY rcept_dt DESC LIMIT ?",
        (stock_code, limit),
    )
    return {"items": [{"rcept_dt": r["rcept_dt"], "report_nm": (r["report_nm"] or "").strip()} for r in rows]}


def section_margin_gap(financials: dict[str, Any]) -> dict[str, Any]:
    """영업이익은 평평한데 순이익이 크게 튀면 일회성 항목을 의심한다(샘플 ③에서 발견한 규칙).

    공시 본문을 읽지 않으므로 **원인을 추정하지 않는다** — "괴리가 있다"는 사실과 크기만
    보고하고, 원인 설명은 `recent_disclosures`를 사람이 대조해야 한다고 명시한다.
    """
    quarters = financials.get("quarters") or []
    if len(quarters) < 2:
        return {"flag": False, "reason": "분기 2개 미만"}
    prev, cur = quarters[-2], quarters[-1]
    op_prev, op_cur = prev.get("operating_profit_억"), cur.get("operating_profit_억")
    ni_prev, ni_cur = prev.get("net_income_억"), cur.get("net_income_억")
    if None in (op_prev, op_cur, ni_prev, ni_cur) or op_prev == 0 or ni_prev == 0:
        return {"flag": False, "reason": "값 없음"}
    op_chg = (op_cur - op_prev) / abs(op_prev)
    ni_chg = (ni_cur - ni_prev) / abs(ni_prev)
    flagged = abs(ni_chg - op_chg) > 0.5 and abs(op_chg) < 0.15
    return {
        "flag": flagged,
        "operating_profit_qoq_pct": round(op_chg * 100, 1),
        "net_income_qoq_pct": round(ni_chg * 100, 1),
        "note": "영업이익은 거의 안 변했는데 순이익 변화가 훨씬 크다 — 영업외 일회성 항목 가능성, "
                "recent_disclosures 공시와 수동 대조 필요(본문 파싱 전까지 자동 원인 추정은 하지 않는다)."
                if flagged else None,
    }


def _to_억(value) -> float | None:
    return round(float(value) / 1e8, 1) if value is not None else None


# --------------------------------------------------------------------- 업종 고유 섹션
def section_sector_specific(conn, stock_code: str, template_kind: str) -> dict[str, Any]:
    if template_kind == TEMPLATE_SEMICONDUCTOR:
        return _sector_semiconductor(conn, stock_code)
    if template_kind == TEMPLATE_FINANCIAL_HOLDING:
        return _sector_financial_holding()
    if template_kind == TEMPLATE_MEDTECH:
        return _sector_medtech()
    if template_kind == TEMPLATE_ORDER_BACKLOG:
        return _sector_order_backlog(conn, stock_code)
    if template_kind == TEMPLATE_DIVIDEND_UTILITY:
        return _sector_dividend_utility(conn, stock_code)
    if template_kind == TEMPLATE_HOLDING_SOTP:
        return _sector_holding_sotp(conn, stock_code)
    if template_kind == TEMPLATE_MATERIALS_VALUE_CHAIN:
        return _sector_materials_value_chain()
    return {"note": "업종 고유 폼 없음(generic) — 공통 섹션만 제공"}


def _sector_semiconductor(conn, stock_code: str) -> dict[str, Any]:
    peers = _rows(
        conn,
        "SELECT m.stock_code, u.stock_name, m.status, m.confidence FROM stock_sector_membership_v2 m "
        "JOIN sector_taxonomy_nodes n ON n.node_key = m.node_key JOIN stock_universe u ON u.stock_code = m.stock_code "
        "WHERE n.dimension = 'peer_group' AND n.name LIKE ? AND m.is_primary = 1 "
        "ORDER BY m.status, u.stock_name",
        ("%OSAT%",),
    )
    is_member = any(p["stock_code"] == stock_code for p in peers)
    return {
        "peer_group": "OSAT(반도체 후공정 패키징·테스트) 비교군",
        "is_member": is_member,
        "peer_members": peers,
        "note": None if is_member else "이 종목은 OSAT 비교군 멤버가 아니다 — 반도체 템플릿은 "
                                        "sector_mid='반도체' 로도 적용되므로 비교군 밖일 수 있다.",
    }


def _sector_financial_holding() -> dict[str, Any]:
    return {
        "note": "자본비율(CET1 등)은 DB에 1급 컬럼이 없다 — 공시 본문 파싱 전까지 이 섹션은 "
                "데이터 공백으로 남긴다(설계 문서 §7에서 이미 확인한 한계).",
        "data_available": False,
    }


def _sector_medtech() -> dict[str, Any]:
    return {
        "note": "해외 규제 승인(FDA 등) 진행 상황은 국내 DART 공시만으로 추적되지 않는다 — "
                "뉴스/애널리스트 리포트 수집(AGI-03)에 의존도가 높은 섹션이다(설계 문서 §5 확인).",
        "regulatory_tracking_available": False,
    }


def _sector_order_backlog(conn, stock_code: str) -> dict[str, Any]:
    rows = _rows(
        conn,
        "SELECT year, quarter, backlog_normalized, new_orders, backlog_to_rev FROM order_backlog "
        "WHERE stock_code = ? ORDER BY year DESC, quarter DESC LIMIT 4",
        (stock_code,),
    )
    has_values = any(
        r.get("backlog_normalized") is not None or r.get("new_orders") is not None or r.get("backlog_to_rev") is not None
        for r in rows
    )
    return {"recent_backlog": rows, "data_available": has_values}


def _sector_dividend_utility(conn, stock_code: str) -> dict[str, Any]:
    """SK텔레콤 리포트에서 확인: 배당 발표 시점·배당수익률·배당성향 추이가 투자논리의 중심.

    `dart_dividends`에 실제 이력이 있으므로(이번에 확인) 추정 없이 그대로 쓴다.
    """
    rows = _rows(
        conn,
        "SELECT fiscal_year, dps_krw, div_yield_pct, div_payout_pct FROM dart_dividends "
        "WHERE stock_code = ? ORDER BY fiscal_year DESC LIMIT 4",
        (stock_code,),
    )
    trend = None
    if len(rows) >= 2 and rows[0].get("dps_krw") is not None and rows[1].get("dps_krw"):
        trend = "증가" if rows[0]["dps_krw"] > rows[1]["dps_krw"] else (
            "감소" if rows[0]["dps_krw"] < rows[1]["dps_krw"] else "동일")
    return {"recent_dividends": rows, "dps_trend_latest_vs_prior_year": trend, "data_available": bool(rows)}


def _sector_holding_sotp(conn, stock_code: str) -> dict[str, Any]:
    """LG화학 리포트에서 확인: 사업부별 EV/EBITDA + 자회사 지분가치를 따로 합산(SOTP)한다.

    사업부별 매출 비중까지는 `company_product_mix`로 보이지만, **EV/EBITDA 멀티플과
    자회사 지분가치(상장 자회사 시가총액×지분율×할인율)는 이 DB에 없다** — 추정하지
    않고 공백으로 명시한다(§7의 데이터 공백 원칙과 동일).
    """
    segment_hint = _rows(
        conn,
        "SELECT category, SUM(revenue_pct) AS pct FROM company_product_mix "
        "WHERE stock_code = ? AND year = (SELECT MAX(year) FROM company_product_mix WHERE stock_code = ?) "
        "GROUP BY category ORDER BY pct DESC",
        (stock_code, stock_code),
    )
    return {
        "segment_revenue_share": segment_hint,
        "sotp_available": False,
        "note": "사업부별 EV/EBITDA 멀티플·상장 자회사 지분가치는 DB에 없다 — SOTP 밸류에이션은 "
                "지금 매출 비중까지만 보여주고, 멀티플·지분가치는 공시 본문/외부 리포트 대조가 "
                "필요하다(AGI-04 범위).",
    }


def _sector_materials_value_chain() -> dict[str, Any]:
    """LS에코에너지 리포트에서 확인: 정책 드라이버·capex 발표·밸류체인 포지션이 핵심.

    capex 발표는 공시(`recent_disclosures`)에 섞여 들어오지만 유형 분류가 안 돼 있고,
    밸류체인상 파트너사(원광→금속→완제품) 연결은 이 DB에 없다 — 공백으로 명시한다.
    """
    return {
        "value_chain_tracking_available": False,
        "note": "정책 드라이버·capex 공시는 recent_disclosures에 섞여 있으나 유형 분류가 안 돼 "
                "있고, 밸류체인상 파트너사 연결(원재료→가공→완제품)은 DB에 없다 — 공시 유형 "
                "분류기(dilution_events·dart_rd_patent_signals 같은 기존 분류 확장)가 필요하다.",
    }


# --------------------------------------------------------------------- 조립 + 저장
def _canonical_hash(content: dict) -> str:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_sections(conn, stock_code: str, template_kind: str) -> dict[str, dict]:
    financials = section_financials(conn, stock_code)
    return {
        "header": section_header(conn, stock_code),
        "financials": financials,
        "business_mix": section_business_mix(conn, stock_code),
        "recent_disclosures": section_recent_disclosures(conn, stock_code),
        "margin_gap": section_margin_gap(financials),
        SECTOR_SECTION_KEY: section_sector_specific(conn, stock_code, template_kind),
    }


def generate_and_store_report(
    conn, stock_code: str, *, trigger_reason: str = "manual", schema: str | None = None
) -> dict:
    """보고서를 조립하고, **바뀐 섹션만** `company_report_sections`에 새 revision 으로 쓴다.

    `schema` 는 신설 2테이블(`company_reports`/`company_report_sections`)에만 적용한다
    (검증용 임시 스키마 격리) — 읽어 오는 기존 운영 테이블(financial_data 등)은 항상
    `public` 비한정 이름이다(그 테이블들은 임시 스키마에 복제되지 않는다).

    `company_reports` 행이 없으면 만든다(템플릿 재분류 포함 — 분류가 바뀌면 `template_kind`
    컬럼도 갱신한다). 반환값은 섹션별 `{revision_no, change_kind}` 요약이다.
    """
    reports_t = qualify(schema, "company_reports")
    sections_t = qualify(schema, "company_report_sections")
    template_kind = classify_template(conn, stock_code)
    existing = conn.execute(f"SELECT 1 FROM {reports_t} WHERE stock_code = ?", (stock_code,)).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if existing is None:
        conn.execute(
            f"INSERT INTO {reports_t} (stock_code, template_kind, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (stock_code, template_kind, now, now),
        )
    else:
        conn.execute(
            f"UPDATE {reports_t} SET template_kind = ?, updated_at = ? WHERE stock_code = ?",
            (template_kind, now, stock_code),
        )

    sections = build_sections(conn, stock_code, template_kind)
    result: dict[str, dict] = {}
    for section_key, content in sections.items():
        content_hash = _canonical_hash(content)
        latest = conn.execute(
            f"SELECT revision_no, content_hash FROM {sections_t} "
            f"WHERE stock_code = ? AND section_key = ? ORDER BY revision_no DESC LIMIT 1",
            (stock_code, section_key),
        ).fetchone()
        if latest is not None and latest["content_hash"] == content_hash:
            result[section_key] = {"revision_no": latest["revision_no"], "change_kind": "unchanged"}
            continue
        revision_no = (latest["revision_no"] + 1) if latest is not None else 1
        change_kind = "updated" if latest is not None else "new"
        conn.execute(
            f"INSERT INTO {sections_t} "
            f"(stock_code, template_kind, section_key, revision_no, content_hash, content_json, "
            f" change_kind, trigger_reason, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (stock_code, template_kind, section_key, revision_no, content_hash,
             json.dumps(content, ensure_ascii=False, default=str), change_kind, trigger_reason, now),
        )
        result[section_key] = {"revision_no": revision_no, "change_kind": change_kind}
    return {"stock_code": stock_code, "template_kind": template_kind, "sections": result}


def get_current_report(conn, stock_code: str, *, schema: str | None = None) -> dict | None:
    """공개 조회(무부작용) — 각 섹션의 최신 revision 만 모아서 돌려준다."""
    reports_t = qualify(schema, "company_reports")
    sections_t = qualify(schema, "company_report_sections")
    meta = conn.execute(
        f"SELECT template_kind, updated_at FROM {reports_t} WHERE stock_code = ?", (stock_code,)
    ).fetchone()
    if meta is None:
        return None
    rows = _rows(
        conn,
        f"SELECT DISTINCT ON (section_key) section_key, revision_no, content_json, change_kind, generated_at "
        f"FROM {sections_t} WHERE stock_code = ? ORDER BY section_key, revision_no DESC",
        (stock_code,),
    )
    sections = {
        r["section_key"]: {
            "revision_no": r["revision_no"],
            "content": json.loads(r["content_json"]),
            "change_kind": r["change_kind"],
            "generated_at": str(r["generated_at"]),
        }
        for r in rows
    }
    return {
        "stock_code": stock_code,
        "template_kind": meta["template_kind"],
        "updated_at": str(meta["updated_at"]),
        "sections": sections,
    }
