"""
체리형부 채널 분석방식 학습 → 전체종목 적용 스크리너 (2026-08-09 신규)

배경: 사용자 지시("해당 채널에서 중소형주를 분석하는 방식을 학습해서 체계화해줘 /
그렇게 해서 당신이 같은 방법으로 전체 종목을 분석하도록 하는 시스템을 만들어봐")에
따라, 체리형부 채널에서 수집한 텍스트 3,657건 + 첨부파일 491건(telegram_messages,
report_files) 중 실제 자체 분석 리포트(피델릭스_종합분석_260805.pdf, 뉴엔AI_분석글.pdf
등)를 원문 그대로 읽고 반복되는 구조를 역설계했다.

═══════════════════════════════════════════════════════════════
학습한 프레임워크 — "3대 스크리닝" (뉴엔AI 리포트에서 명시적으로 이 이름으로 등장)
═══════════════════════════════════════════════════════════════
① 적자 후 흑자전환 (turnaround): TTM 순이익이 최근 적자 구간을 지나 흑자로 전환했거나,
   단일분기 흑자로 재도전 신호가 보이는 경우.
② 매출 시계열 최대 (revenue all-time-high): 현재 TTM 매출이 과거 전체 분기 이력 중
   최고치를 경신했는가 — "실적이 진짜인가"를 매출 규모 자체로 확인.
③ 주가 미반영 (undervaluation gap): 시가총액에서 순현금을 뺀 "사업가치"가 매출 대비
   낮은 배수(ex-cash PSR)로 거래되고 있는가 — 시장이 아직 스토리를 반영하지 않았는가.

이 3개 스크리닝 위에 체리형부가 반복적으로 쓰는 보조 분석 축을 추가로 이식했다
(전부 이 프로젝트에 이미 검증되어 있던 재료를 재사용 — 새로 지어낸 신호가 아님):
- 선행지표 상관: 계약부채(선수금) YoY vs 차분기 매출YoY 상관계수 [contract_advance_signals]
  (피델릭스 리포트의 "수주잔고 YoY와 차분기 매출YoY 상관 0.914" 방법론과 동일)
- 기관 매집: 최근 90일 5%룰 대량보유 증가 신고 [dart_major_holders]
  (뉴엔AI 리포트의 "KB자산운용 신규 447,949주 보고" 섹션과 동일)
- 희석위험 / 재도전이력 / 이익의질(감가상각) / 꿈촉매(특허·기술이전) — 기존
  turnaround-watch(routes/tenbagger.py)에서 이미 walk-forward 검증된 신호를 그대로
  차용(사실등급 태깅 없이 boolean/숫자 필드로 병기).

⚠️ 정직한 한계 (체리형부와 이 시스템의 차이):
- 체리형부는 종목당 DART 원문 20개+ 보고서를 전수 파싱해 사이클 포지셔닝(대만 D램
  가격 vs 국내기업 시차 등 업스트림 지표 연동), P×Q 분해, 재고 세부항목별 상관,
  비용 성격별 분류→손익환원식, 오버행/락업 스케줄까지 사람이 직접 해석해 붙인다.
  이 시스템은 그 중 "정형화 가능한(전종목에 기계적으로 반복 계산 가능한)" 부분만
  자동화했다 — 즉 "후보를 걸러내는 스크리너"이지 체리형부 리포트 자체의 완전한
  자동 생성이 아니다. 나머지(업스트림 사이클 서사, 비대칭 리스크 판단, 오버행
  스케줄)는 사람의 해석이 필요하며, 이 스크리너가 뽑은 후보를 사용자가 직접
  turnaround-watch 상세보기 + 종목분석 페이지로 넘어가 확인하는 흐름을 전제로 한다.
- 사이클 포지셔닝(예: DDR4 현물가 vs 국내기업 시차)은 종목별로 참조할 업스트림 지표가
  다르므로 전종목 공통 로직화가 불가능 — 반도체 종목은 quant_major_indicator의
  DRAM_SPOT 계열과 수동 대조가 필요함을 안내 문구로만 남긴다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ⚠️ 2026-08-09: connect_primary_db(db_compat)는 Postgres 마이그레이션 진행 중(config.py
# POSTGRES_DATABASE_URL, 미커밋 워크트리 작업으로 추정)이라 dart_major_holders 등 일부 신규
# 테이블이 아직 이관되지 않은 상태 — 이 모듈은 항상 완전한 데이터를 가진 stock.db를 직접
# 사용한다(db_utils.connect_stock_db, CLAUDE.md 표준 sqlite3 패턴). 마이그레이션이 완료되면
# connect_primary_db로 교체 가능.
from db_utils import connect_stock_db
from env_utils import BASE_DIR
from fastapi import APIRouter, Query

router = APIRouter()

CACHE_PATH = BASE_DIR / "scratch" / "cherry_screener_cache.json"


def _avail_date(year: int, quarter: int) -> str:
    if quarter == 1:
        return f"{year}-05-15"
    if quarter == 2:
        return f"{year}-08-15"
    if quarter == 3:
        return f"{year}-11-15"
    return f"{year + 1}-02-15"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / (sxx ** 0.5 * syy ** 0.5), 3)


def _safe_eok(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if abs(number) >= 1_000_000:
        return round(number / 100_000_000, 1)
    return round(number, 1)


def _fmt_eok(value: Any) -> str:
    eok = _safe_eok(value)
    if eok is None:
        return "-"
    return f"{eok:,.1f}억"


def _screen_bool_text(value: bool) -> str:
    return "충족" if value else "미충족"


def _build_quarterly_panel(conn: sqlite3.Connection) -> tuple[dict[str, list[tuple]], dict[str, str]]:
    """(stock_code, year, quarter) -> (net_income, revenue, cash) 패널.
    CFS/OFS 혼재 방지: stock_collection_config.preferred_report_type 존중,
    매출/현금은 항상 CFS 우선(지주사 별도재무 왜곡 회피 — turnaround-watch와 동일 원칙)."""
    overrides = {r[0]: r[1] for r in conn.execute(
        "SELECT stock_code, config_value FROM stock_collection_config "
        "WHERE config_key='preferred_report_type'")}
    raw_rows = conn.execute("""
        SELECT stock_code, year, quarter, report_type, net_income, revenue, cash
        FROM financial_data
        WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
        ORDER BY stock_code, year, quarter
    """).fetchall()
    by_quarter: dict[tuple, dict] = {}
    for r in raw_rows:
        key = (r[0], r[1], r[2])
        by_quarter.setdefault(key, {})[r[3]] = r
    panel: dict[str, list] = {}
    for (code, y, q), variants in by_quarter.items():
        pref = overrides.get(code, "CFS")
        r_ni = variants.get(pref) or next(iter(variants.values()))
        r_rev = variants.get("CFS") or r_ni
        ni = r_ni[4]
        rev = r_rev[5]
        # ⚠️ financial_data.cash에 음수값 다수 확인(2,484종목/16,730행, 2026-08-09) — 일부
        # DART XBRL 계정이 "현금성자산 잔액" 대신 "현금 증감액"으로 잘못 매핑된 것으로 추정.
        # 음수는 절대 실제 현금잔액일 수 없으므로 CFS/OFS 두 변형 중 0 이상인 값만 채택,
        # 둘 다 없거나 음수면 None(가치평가 계산에서 신뢰불가로 별도 처리).
        cash_candidates = [v for v in (r_rev[6], r_ni[6]) if v is not None and v >= 0]
        cash = cash_candidates[0] if cash_candidates else None
        panel.setdefault(code, []).append((y, q, ni, rev, cash))
    for code in panel:
        panel[code].sort(key=lambda x: (x[0], x[1]))
    return panel, overrides


def _find_stock_name(conn: sqlite3.Connection, stock_code: str) -> str:
    row = conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_code=? LIMIT 1",
        (stock_code,),
    ).fetchone()
    return str(row[0]) if row and row[0] else stock_code


def _load_recent_financials(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT year, quarter, revenue, operating_profit, net_income
        FROM financial_data
        WHERE stock_code=? AND is_annual=0 AND report_type='CFS'
        ORDER BY year DESC, quarter DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    return [
        {
            "year": int(r[0]),
            "quarter": int(r[1]),
            "revenue_억": _safe_eok(r[2]),
            "operating_profit_억": _safe_eok(r[3]),
            "net_income_억": _safe_eok(r[4]),
        }
        for r in rows
    ]


def _load_recent_disclosures(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT rcept_dt, report_nm
        FROM dart_disclosures
        WHERE stock_code=?
        ORDER BY rcept_dt DESC
        LIMIT 8
        """,
        (stock_code,),
    ).fetchall()
    return [{"date": r[0], "title": r[1]} for r in rows]


def _load_recent_contracts(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT disclosed_at, report_nm, contract_amount_krw, contract_ratio_pct, is_overseas,
               contract_start, contract_end, signal_strength
        FROM dart_contracts
        WHERE stock_code=?
        ORDER BY disclosed_at DESC, id DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    return [
        {
            "date": r[0],
            "title": r[1],
            "amount_억": _safe_eok(r[2]),
            "ratio_pct": round(float(r[3]), 1) if r[3] is not None else None,
            "is_overseas": bool(r[4]),
            "contract_start": r[5],
            "contract_end": r[6],
            "signal_strength": int(r[7] or 0),
        }
        for r in rows
    ]


def _load_recent_rd_signals(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT rcept_dt, report_nm, signal_type, amount_krw, notes
        FROM dart_rd_patent_signals
        WHERE stock_code=?
        ORDER BY rcept_dt DESC, id DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    return [
        {
            "date": r[0],
            "title": r[1],
            "signal_type": r[2],
            "amount_억": _safe_eok(r[3]),
            "notes": (str(r[4] or "")[:160]).replace("\n", " "),
        }
        for r in rows
    ]


def _load_recent_dilution_events(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT disclosed_at, event_type, dilution_pct, issue_amount, report_nm
        FROM dilution_events
        WHERE stock_code=?
        ORDER BY disclosed_at DESC, id DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    return [
        {
            "date": r[0],
            "event_type": r[1],
            "dilution_pct": round(float(r[2]), 2) if r[2] is not None else None,
            "issue_amount_억": _safe_eok(r[3]),
            "title": r[4],
        }
        for r in rows
    ]


def _load_recent_major_holders(conn: sqlite3.Connection, stock_code: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT rcept_dt, repror, stk_diff, rt_diff, stkrt, report_tp
        FROM dart_major_holders
        WHERE stock_code=?
        ORDER BY rcept_dt DESC, id DESC
        LIMIT 6
        """,
        (stock_code,),
    ).fetchall()
    return [
        {
            "date": r[0],
            "holder": r[1],
            "share_diff": int(r[2] or 0),
            "ratio_diff_pct": round(float(r[3]), 3) if r[3] is not None else None,
            "holding_ratio_pct": round(float(r[4]), 3) if r[4] is not None else None,
            "report_type": r[5],
        }
        for r in rows
    ]


def _load_telegram_buzz(conn: sqlite3.Connection, stock_name: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT mention_date, mention_count
        FROM tg_daily_mentions
        WHERE stock_name=?
        ORDER BY mention_date DESC
        LIMIT 14
        """,
        (stock_name,),
    ).fetchall()
    counts = [int(r[1] or 0) for r in rows]
    last_7d = sum(counts[:7])
    prev_7d = sum(counts[7:14])
    trend = "flat"
    if last_7d > prev_7d * 1.5 and last_7d >= 5:
        trend = "surging"
    elif prev_7d > 0 and last_7d < prev_7d * 0.7:
        trend = "cooling"
    return {
        "last_7d_mentions": last_7d,
        "prev_7d_mentions": prev_7d,
        "trend": trend,
        "daily": [{"date": r[0], "count": int(r[1] or 0)} for r in rows],
    }


def _load_related_reports(conn: sqlite3.Connection, stock_code: str, stock_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, report_date, file_name, caption, channel_id
        FROM report_files
        WHERE (
            (stock_code IS NOT NULL AND stock_code != '' AND stock_code = ?)
            OR (stock_name IS NOT NULL AND stock_name != '' AND stock_name = ?)
            OR (file_name IS NOT NULL AND file_name LIKE '%' || ? || '%')
            OR (caption IS NOT NULL AND caption LIKE '%' || ? || '%')
        )
        ORDER BY COALESCE(report_date, created_at) DESC, id DESC
        LIMIT 12
        """,
        (stock_code, stock_name, stock_name, stock_name),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        file_name = str(r[2] or "")
        caption = str(r[3] or "").replace("\n", " ").strip()
        items.append(
            {
                "id": int(r[0]),
                "report_date": r[1],
                "file_name": file_name,
                "caption": caption[:180] if caption else "",
                "channel_id": r[4],
                "download_url": f"/api/detailed-analysis/report-files/{int(r[0])}/download",
            }
        )
    return items


def _load_related_messages(conn: sqlite3.Connection, stock_code: str, stock_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT channel, message_id, date, text, stocks
        FROM telegram_messages
        WHERE stocks LIKE '%' || ? || '%'
        ORDER BY date DESC
        LIMIT 20
        """,
        (stock_name,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    name_upper = stock_name.upper()
    for r in rows:
        stocks_text = str(r[4] or "").upper()
        if stock_code not in stocks_text and name_upper not in stocks_text:
            continue
        text = str(r[3] or "").replace("\n", " ").strip()
        items.append(
            {
                "channel": r[0],
                "message_id": r[1],
                "date": r[2],
                "text": text[:220],
            }
        )
    return items[:10]


def _build_cherry_narrative(detail: dict[str, Any], financials: list[dict[str, Any]], disclosures: list[dict[str, Any]]) -> dict[str, Any]:
    strengths: list[str] = []
    risks: list[str] = []
    checkpoints: list[str] = []

    if detail.get("screen1_turnaround"):
        strengths.append("TTM 또는 단일분기 기준 흑자전환 조건이 확인돼 턴어라운드 축이 살아 있다.")
    else:
        risks.append("핵심 1번 조건인 흑자전환이 아직 확정되지 않아 체리형부식 관찰 단계에 가깝다.")

    if detail.get("screen2_revenue_ath"):
        strengths.append("최신 분기 매출이 과거 분기 최고치에 도달해 실적 체력 확인에 유리하다.")
    else:
        ath_pct = detail.get("revenue_ath_pct_of_record")
        if ath_pct is not None:
            risks.append(f"최신 분기 매출이 역대 최고의 {ath_pct:.1f}% 수준으로, 외형 가속이 아직 완성형은 아니다.")

    if detail.get("screen3_undervalued_vs_sector"):
        strengths.append("사업가치 기준 ex-cash PSR이 섹터 대비 낮아 주가 미반영 구간 해석이 가능하다.")
    else:
        risks.append("섹터 대비 저평가 신호가 뚜렷하지 않아 밸류 리레이팅 여지는 추가 검증이 필요하다.")

    if detail.get("institutional_accumulation"):
        strengths.append("최근 90일 내 5%룰 지분 증가 신고가 있어 기관 매집 정황이 보인다.")

    backlog_corr = detail.get("backlog_correlation")
    if backlog_corr is not None:
        if backlog_corr >= 0.5:
            strengths.append(f"계약부채/선수금과 차분기 매출의 상관({backlog_corr})이 높아 선행지표 신뢰도가 양호하다.")
        elif backlog_corr <= 0:
            risks.append(f"계약부채와 차분기 매출의 상관({backlog_corr})이 낮아 선행지표 해석을 보수적으로 봐야 한다.")

    dilution_risk = int(detail.get("dilution_risk") or 0)
    if dilution_risk >= 2:
        risks.append(f"최근 1년 희석 이벤트가 {dilution_risk}건으로 자금조달 오버행을 점검해야 한다.")

    if detail.get("dream_catalyst"):
        checkpoints.append("특허·기술이전·R&D 계약성 공시가 실적 매출화로 이어지는지 추적")
    checkpoints.append("다음 분기 매출이 다시 최고치 경신 또는 최소 전년동기 고성장을 유지하는지 확인")
    checkpoints.append("영업이익과 순이익이 일회성이 아니라 2개 분기 이상 이어지는지 확인")
    if detail.get("screen3_undervalued_vs_sector"):
        checkpoints.append("섹터 대비 ex-cash PSR 할인폭이 축소되는지 확인")
    if disclosures:
        checkpoints.append("최근 공시의 계약·수주·자금조달 이슈가 다음 분기 숫자에 반영되는지 대조")

    headline = "3대 스크리닝 통과 후보" if int(detail.get("score") or 0) >= 3 else (
        "체리형부식 관찰 후보" if int(detail.get("score") or 0) >= 2 else "아직 관찰 초기 단계"
    )

    latest_fin = financials[0] if financials else None
    if latest_fin and latest_fin.get("revenue_억") is not None:
        headline += f" · 최신분기 매출 {latest_fin['revenue_억']:,.1f}억"

    return {
        "headline": headline,
        "strengths": strengths[:5],
        "risks": risks[:5],
        "checkpoints": checkpoints[:5],
    }


def _build_humanized_thesis(
    detail: dict[str, Any],
    financials: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    rd_signals: list[dict[str, Any]],
    dilution_events: list[dict[str, Any]],
    major_holders: list[dict[str, Any]],
    buzz: dict[str, Any],
) -> dict[str, Any]:
    why_now: list[str] = []
    drivers: list[dict[str, str]] = []
    invalidation_points: list[str] = []
    bear_case: list[str] = []
    evidence: list[dict[str, str]] = []

    latest_fin = financials[0] if financials else None
    if detail.get("screen2_revenue_ath") and latest_fin and latest_fin.get("revenue_억") is not None:
        why_now.append(
            f"최신 분기 매출이 {latest_fin['revenue_억']:,.1f}억으로 분기 최고권이라 숫자가 먼저 움직이고 있다."
        )
        drivers.append({"title": "실적 체력", "detail": "분기 매출 최고치 또는 그 근처에 올라와 스토리보다 숫자가 앞서는 구간이다."})
        evidence.append({"type": "financial", "detail": "최신 분기 매출이 과거 최고치 기준을 충족 또는 근접"})
    elif detail.get("revenue_ath_pct_of_record") is not None:
        why_now.append(
            f"최신 분기 매출이 역대 최고의 {detail['revenue_ath_pct_of_record']:.1f}% 수준까지 올라와 다음 분기 숫자 확인 가치가 크다."
        )

    if detail.get("screen1_turnaround"):
        drivers.append({"title": "턴어라운드", "detail": "적자 구간을 지나 이익 체력이 돌아오는 초기 구간으로 해석할 수 있다."})
        evidence.append({"type": "turnaround", "detail": "TTM 또는 단일분기 흑자전환 조건 충족"})
    else:
        invalidation_points.append("다음 분기에도 순이익 회복이 확인되지 않으면 턴어라운드 논지는 약해진다.")

    if detail.get("screen3_undervalued_vs_sector"):
        drivers.append({"title": "밸류 리레이팅 여지", "detail": "섹터 대비 ex-cash PSR이 낮아 숫자 개선이 이어지면 재평가 여지가 있다."})
        evidence.append({"type": "valuation", "detail": "섹터 대비 낮은 ex-cash PSR 또는 시총<순현금 구간"})
    else:
        bear_case.append("이미 저평가 구간이라고 단정하기 어려워 주가 재평가 폭은 제한될 수 있다.")

    if contracts:
        top_contract = contracts[0]
        contract_label = f"{top_contract['ratio_pct']:.1f}%" if top_contract.get("ratio_pct") is not None else "비율미상"
        why_now.append(f"최근 수주/계약 공시가 이어졌고 대표 건의 매출 대비 비중은 {contract_label} 수준이다.")
        drivers.append({"title": "공시 촉매", "detail": "계약 공시가 실제 매출 인식으로 연결되면 체리형부식 선행지표 논리가 강화된다."})
        evidence.append({"type": "contract", "detail": f"{top_contract.get('date') or '-'} {top_contract.get('title') or '계약 공시'}"})

    backlog_corr = detail.get("backlog_correlation")
    if backlog_corr is not None and backlog_corr >= 0.5:
        why_now.append(f"계약부채와 차분기 매출의 상관이 {backlog_corr}로 높아 숫자 추적의 선명도가 좋다.")
        evidence.append({"type": "backlog", "detail": f"계약부채-차분기매출 상관 {backlog_corr}"})

    if rd_signals:
        rd = rd_signals[0]
        drivers.append({"title": "꿈 촉매", "detail": "특허·기술이전·R&D 계약 류 공시가 붙어 있어 실적 외 업사이드 옵션이 있다."})
        evidence.append({"type": "rd", "detail": f"{rd.get('date') or '-'} {rd.get('signal_type') or 'rd'}"})
        invalidation_points.append("기술/특허 공시가 실제 매출화나 이익 개선으로 이어지지 않으면 기대만 남을 수 있다.")

    if major_holders:
        latest_holder = major_holders[0]
        if (latest_holder.get("share_diff") or 0) > 0:
            why_now.append("최근 대량보유 보고에서 지분 증가가 확인돼 수급 측면의 확인 신호가 있다.")
            evidence.append({"type": "holder", "detail": f"{latest_holder.get('date') or '-'} {latest_holder.get('holder') or '보고자'} 지분 증가"})

    if buzz.get("trend") == "surging":
        why_now.append(f"텔레그램 언급이 최근 7일 {buzz.get('last_7d_mentions', 0)}회로 직전 주 대비 급증해 관심이 실제로 붙고 있다.")
        evidence.append({"type": "telegram", "detail": "최근 7일 언급 급증"})
    elif buzz.get("trend") == "cooling":
        bear_case.append("텔레그램 관심도는 최근 둔화돼 모멘텀 지속성은 확인이 더 필요하다.")

    dilution_risk = int(detail.get("dilution_risk") or 0)
    if dilution_risk >= 2 or dilution_events:
        bear_case.append("희석 이벤트 이력이 있어 좋은 숫자가 나와도 주주가치 훼손 가능성을 같이 봐야 한다.")
        invalidation_points.append("추가 CB/BW/유상증자성 이벤트가 나오면 논지를 즉시 재검토해야 한다.")

    if not why_now:
        why_now.append("정량 신호는 일부 보이지만 아직 사람처럼 강하게 밀 수 있는 촉매는 제한적이라 관찰형 접근이 맞다.")

    if not invalidation_points:
        invalidation_points.append("매출 최고치 흐름이 꺾이거나 이익 회복이 재차 무너지면 현재 논지는 약해진다.")

    score = int(detail.get("score") or 0)
    stance = "strong_watch" if score >= 3 else ("watch" if score >= 2 else "observe")
    summary = why_now[0]

    return {
        "stance": stance,
        "summary": summary,
        "why_now": why_now[:5],
        "core_drivers": drivers[:5],
        "bear_case": bear_case[:5],
        "invalidation_points": invalidation_points[:5],
        "evidence": evidence[:8],
    }


def _render_cherry_analysis_markdown(
    stock_name: str,
    stock_code: str,
    detail: dict[str, Any],
    financials: list[dict[str, Any]],
    disclosures: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    rd_signals: list[dict[str, Any]],
    dilution_events: list[dict[str, Any]],
    major_holders: list[dict[str, Any]],
    buzz: dict[str, Any],
    reports: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    narrative: dict[str, Any],
    thesis: dict[str, Any],
) -> str:
    lines = [
        f"# {stock_name}({stock_code}) 체리형부식 자동분석 초안",
        "",
        f"- 판정: {narrative['headline']}",
        f"- 기준 시점: {detail.get('as_of') or '-'}",
        f"- 3대 스크리닝 점수: {detail.get('score', 0)}/3",
        "",
        "## 0) 왜 지금 봐야 하나",
    ]
    lines.extend([f"- {item}" for item in thesis.get("why_now", [])] or ["- 아직 강한 why now 논지는 제한적이다."])

    lines.extend([
        "",
        "## 1) 3대 스크리닝",
        f"- 흑자전환: {_screen_bool_text(bool(detail.get('screen1_turnaround')))}",
        f"- 매출 역대최대: {_screen_bool_text(bool(detail.get('screen2_revenue_ath')))}",
        f"- 주가 미반영: {_screen_bool_text(bool(detail.get('screen3_undervalued_vs_sector')))}",
        "",
        "## 2) 핵심 수치",
        f"- TTM 매출: {detail.get('ttm_revenue_억', '-') if detail.get('ttm_revenue_억') is not None else '-'}억",
        f"- TTM 순이익: {_fmt_eok(detail.get('ttm_net_income'))}",
        f"- 순현금 추정: {detail.get('cash_억', '-') if detail.get('cash_억') is not None else '-'}억",
        f"- 사업가치: {detail.get('biz_value_억', '-') if detail.get('biz_value_억') is not None else '-'}억",
        f"- ex-cash PSR: {detail.get('ex_cash_psr') if detail.get('ex_cash_psr') is not None else '-'}",
        "",
        "## 3) 강점",
    ])
    if narrative["strengths"]:
        lines.extend([f"- {item}" for item in narrative["strengths"]])
    else:
        lines.append("- 아직 강한 정량 신호는 제한적이다.")

    lines.extend(["", "## 4) 리스크"])
    if narrative["risks"]:
        lines.extend([f"- {item}" for item in narrative["risks"]])
    else:
        lines.append("- 현재 수집 데이터 기준으로 구조적 리스크는 제한적으로 보인다.")

    lines.extend(["", "## 5) 다음 확인지표"])
    lines.extend([f"- {item}" for item in narrative["checkpoints"]])

    if thesis.get("bear_case"):
        lines.extend(["", "## 6) 반대 논거"])
        lines.extend([f"- {item}" for item in thesis["bear_case"]])

    if thesis.get("invalidation_points"):
        lines.extend(["", "## 7) 논지가 깨지는 조건"])
        lines.extend([f"- {item}" for item in thesis["invalidation_points"]])

    if financials:
        lines.extend(["", "## 8) 최근 분기 재무"])
        for row in financials[:4]:
            lines.append(
                f"- {row['year']}Q{row['quarter']}: 매출 {_fmt_eok(row['revenue_억'])}, "
                f"영업이익 {_fmt_eok(row['operating_profit_억'])}, 순이익 {_fmt_eok(row['net_income_억'])}"
            )

    if disclosures:
        lines.extend(["", "## 9) 최근 공시"])
        for row in disclosures[:5]:
            lines.append(f"- {row['date']} {row['title']}")

    if contracts:
        lines.extend(["", "## 10) 최근 계약/수주"])
        for row in contracts[:4]:
            ratio = f"{row['ratio_pct']:.1f}%" if row.get("ratio_pct") is not None else "-"
            overseas = "해외" if row.get("is_overseas") else "국내"
            lines.append(f"- {row['date']} | {row['title']} | 비중 {ratio} | {overseas}")

    if rd_signals:
        lines.extend(["", "## 11) 기술·특허 촉매"])
        for row in rd_signals[:4]:
            lines.append(f"- {row['date']} | {row['signal_type']} | {row['title']}")

    if major_holders:
        lines.extend(["", "## 12) 대량보유/기관수급"])
        for row in major_holders[:4]:
            diff = row.get("ratio_diff_pct")
            diff_text = f"{diff:+.3f}%p" if diff is not None else "-"
            lines.append(f"- {row['date']} | {row['holder']} | 지분변화 {diff_text}")

    if buzz:
        lines.extend(["", "## 13) 텔레그램 관심도"])
        lines.append(
            f"- 최근 7일 {buzz.get('last_7d_mentions', 0)}회 / 직전 7일 {buzz.get('prev_7d_mentions', 0)}회 / 추세 {buzz.get('trend', 'flat')}"
        )

    if dilution_events:
        lines.extend(["", "## 14) 희석 이벤트"])
        for row in dilution_events[:4]:
            dpct = f"{row['dilution_pct']:.2f}%" if row.get("dilution_pct") is not None else "-"
            lines.append(f"- {row['date']} | {row['event_type']} | 희석 {dpct} | {row.get('title') or '-'}")

    if reports:
        lines.extend(["", "## 15) 텔레그램 첨부리포트"])
        for row in reports[:5]:
            suffix = f" | {row['caption']}" if row.get("caption") else ""
            lines.append(f"- {row.get('report_date') or '-'} | {row['file_name']}{suffix}")

    if messages:
        lines.extend(["", "## 16) 텔레그램 언급"])
        for row in messages[:5]:
            lines.append(f"- {row.get('date') or '-'} | {row['text']}")

    return "\n".join(lines)


def _compute_cherry_screener(min_mktcap: float = 300.0, max_mktcap: float = 30000.0) -> dict:
    """중소형주(min_mktcap~max_mktcap 억원) 대상 3대 스크리닝 + 보조신호 전종목 스캔."""
    conn = connect_stock_db(timeout=15, row_factory=sqlite3.Row, readonly=True)
    try:
        panel, _ = _build_quarterly_panel(conn)

        name_map = dict(conn.execute("SELECT stock_code, stock_name FROM stock_universe").fetchall())
        mktcap_map = dict(conn.execute("SELECT stock_code, market_cap FROM stock_universe").fetchall())
        sector_map = dict(conn.execute(
            "SELECT stock_code, sector_large FROM stock_universe WHERE sector_large IS NOT NULL"))

        # 희석위험 (turnaround-watch와 동일 쿼리 재사용)
        dilution_map: dict[str, list[str]] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
        """):
            if r[1]:
                dilution_map.setdefault(r[0], []).append(r[1][:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _dilution_risk(code: str, avail_date: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(avail_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= avail_date)

        # 꿈촉매(특허/기술이전/R&D계약/라이선스, 트레일링365일) — turnaround-watch와 동일
        patent_events: dict[str, list[str]] = {}
        for r in conn.execute("SELECT stock_code, rcept_dt FROM dart_rd_patent_signals"):
            patent_events.setdefault(r[0], []).append(r[1])
        for c in patent_events:
            patent_events[c].sort()

        def _dream_catalyst(code: str, avail_date: str) -> bool:
            evs = patent_events.get(code)
            if not evs:
                return False
            cutoff = (datetime.strptime(avail_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return any(cutoff <= d <= avail_date for d in evs)

        # 기관 매집(5%룰 대량보유 증가 신고, 최근 90일) — 뉴엔AI 리포트 "6. 수급" 섹션 방법론
        inst_map: dict[str, dict] = {}
        cutoff_inst = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        for r in conn.execute("""
            SELECT stock_code, MAX(rcept_dt) d, COUNT(*) n
            FROM dart_major_holders
            WHERE COALESCE(stk_diff, 0) > 0 AND rcept_dt >= ?
            GROUP BY stock_code
        """, (cutoff_inst,)):
            inst_map[r[0]] = {"latest_report": r[1], "count_90d": r[2]}

        # 선행지표(계약부채/선수금) — contract_advance_signals, 분기쌍 상관계수
        advance_by_code: dict[str, list[tuple]] = {}
        for r in conn.execute("""
            SELECT stock_code, fiscal_year, fiscal_quarter, net_yoy_pct, gross_to_revenue_pct
            FROM contract_advance_signals
            WHERE quality_flag='ok' AND net_yoy_pct IS NOT NULL
            ORDER BY stock_code, fiscal_year, fiscal_quarter
        """):
            advance_by_code.setdefault(r[0], []).append((r[1], r[2], r[3]))

        today = datetime.now().strftime("%Y-%m-%d")
        rows: list[dict[str, Any]] = []

        for code, qs in panel.items():
            mc = mktcap_map.get(code) or 0
            if mc < min_mktcap or mc > max_mktcap:
                continue
            n = len(qs)
            if n < 8:
                continue

            # TTM 시계열(순이익/매출) 롤링 구축
            ttm_ni_series, ttm_rev_series = [], []
            for i in range(3, n):
                window = qs[i - 3:i + 1]
                ttm_ni_series.append(sum((x[2] or 0) for x in window))
                ttm_rev_series.append(sum((x[3] or 0) for x in window))
            if not ttm_ni_series:
                continue

            i_last = n - 1
            y, q, ni, rev, cash = qs[i_last]
            if cash is None:
                # 최신분기 현금값 결측(흔히 Q4/연간 결산 처리 지연) — 최근 4개 분기 내
                # 가장 최신의 유효값으로 대체(뒤로 갈수록 stale하지만 None보다는 근사치가 낫다).
                for j in range(i_last - 1, max(-1, i_last - 4), -1):
                    if qs[j][4] is not None:
                        cash = qs[j][4]
                        break
            avail = _avail_date(y, q)
            if avail > today:
                continue

            ttm_now = ttm_ni_series[-1]
            rev_ttm_now = ttm_rev_series[-1]

            # 재도전 이력(최근 4분기 내 단일분기 흑자 존재, 현재분기 제외) — screen1 판정에 먼저 필요.
            last_flip_q = None
            for j in range(i_last - 1, max(-1, i_last - 5), -1):
                if (qs[j][2] or 0) > 0:
                    last_flip_q = f"{qs[j][0]}Q{qs[j][1]}"
                    break

            # ① 적자 후 흑자전환 — 아래 중 하나면 충족:
            #  (a) TTM이 흑자이고 최근 TTM포인트 중 적자 구간이 있었음(확정 흑자전환)
            #  (b) 단일분기가 흑자이나 TTM은 아직 적자(단일분기 흑자전환, 강한 리딩시그널)
            # ⚠️ 2026-08-09: "재도전형"(직전분기는 흑자였으나 현재분기 다시 적자)은 screen1
            # 충족으로 넣지 않는다 — 뉴엔AI_분석글.pdf 원문의 명시적 판정("주력 Enterprise의
            # 1개 분기 회복만으로 반전이라 단정할 수 없음... 흑자전환이 여전히 미충족이므로
            # 관찰 유지")과 대조 검증한 결과, 체리형부 스스로도 재도전 1회만으로는 스크리닝
            # 통과로 인정하지 않음이 확인됨(기존 turnaround-watch의 D섹션이 재도전을 A/C와
            # 별도의 약한 신호로 분리해둔 설계와도 일치). last_flip_quarter는 참고 필드로만 유지.
            had_recent_ttm_loss = any(v <= 0 for v in ttm_ni_series[max(0, len(ttm_ni_series) - 5):-1])
            screen1 = bool((ttm_now > 0 and had_recent_ttm_loss) or (ttm_now <= 0 and (ni or 0) > 0))

            # ② 매출 시계열 최대 — 체리형부 원문(피델릭스 리포트: "26.1Q 214.9억은 21.4Q
            # 243.0억에 9.8% 못 미침 — 미충족")은 TTM이 아니라 "당분기 매출 vs 역대 분기매출"
            # 기준이므로 동일하게 단일분기 기준으로 판정(2026-08-09 수정, TTM매출은 밸류에이션
            # 계산(rev_ttm_now)에는 계속 사용).
            hist_max_rev_q = max((x[3] or 0) for x in qs)
            screen2 = bool(rev and hist_max_rev_q > 0 and rev >= hist_max_rev_q)
            rev_ath_pct_of_max = round(rev / hist_max_rev_q * 100, 1) if (rev and hist_max_rev_q > 0) else None

            # 매출 YoY (전년동기 대비, 이상치 방어)
            rev_yoy = None
            if i_last - 4 >= 0 and qs[i_last - 4][3] and qs[i_last - 4][3] >= 1e9 and rev:
                raw = (rev / qs[i_last - 4][3] - 1) * 100
                rev_yoy = round(raw, 1) if abs(raw) <= 500 else None

            # ③ 주가 미반영 — 시총(억) - 순현금(억) = 사업가치(억), ex-cash PSR
            cash_억 = round((cash or 0) / 1e8, 1)
            rev_ttm_억 = round(rev_ttm_now / 1e8, 1) if rev_ttm_now else 0
            biz_value_억 = round(mc - cash_억, 1)
            ex_cash_psr = round(biz_value_억 / rev_ttm_억, 2) if rev_ttm_억 > 0 else None
            below_cash = bool(mc <= cash_억 and cash_억 > 0)  # 시총이 순현금 이하(뉴엔AI 저점 사례)

            # 희석/촉매/기관매집
            dilution_risk = _dilution_risk(code, avail)
            dream = _dream_catalyst(code, avail)
            inst = inst_map.get(code)

            # 선행지표 상관(계약부채YoY[t] vs 매출YoY[t+1], 커버리지 있는 종목만)
            backlog_corr = None
            backlog_pairs_n = 0
            adv = advance_by_code.get(code)
            if adv and len(adv) >= 5:
                # (year,quarter)->revenue_yoy 매핑 재구성
                yoy_by_yq: dict[tuple, float] = {}
                for k in range(4, n):
                    yb, qb = qs[k][0], qs[k][1]
                    prev = qs[k - 4][3]
                    if prev and prev >= 1e9 and qs[k][3]:
                        v = (qs[k][3] / prev - 1) * 100
                        if abs(v) <= 500:
                            yoy_by_yq[(yb, qb)] = v
                xs, ys = [], []
                for idx in range(len(adv) - 1):
                    yb, qb, net_yoy = adv[idx]
                    nxt_q = qb + 1
                    nxt_y = yb
                    if nxt_q > 4:
                        nxt_q, nxt_y = 1, yb + 1
                    target = yoy_by_yq.get((nxt_y, nxt_q))
                    if target is not None and net_yoy is not None:
                        xs.append(net_yoy)
                        ys.append(target)
                if len(xs) >= 4:
                    backlog_corr = _pearson(xs, ys)
                    backlog_pairs_n = len(xs)

            score = int(screen1) + int(screen2) + int(bool(ex_cash_psr is not None and ex_cash_psr > 0))

            rows.append({
                "stock_code": code, "stock_name": name_map.get(code, code),
                "sector_large": sector_map.get(code), "mktcap_억": round(mc),
                "as_of": avail, "year": y, "quarter": q,
                "screen1_turnaround": screen1,
                "screen1_last_flip_quarter": last_flip_q,
                "screen2_revenue_ath": screen2,
                "revenue_ath_pct_of_record": rev_ath_pct_of_max,
                "revenue_yoy_pct": rev_yoy,
                "ttm_net_income": round(ttm_now),
                "ttm_revenue_억": rev_ttm_억,
                "cash_억": cash_억,
                "biz_value_억": biz_value_억,
                "ex_cash_psr": ex_cash_psr,
                "below_cash_value": below_cash,
                "score": score,
                "dilution_risk": dilution_risk,
                "dream_catalyst": dream,
                "institutional_accumulation": inst,
                "backlog_correlation": backlog_corr,
                "backlog_pairs_n": backlog_pairs_n,
            })

        # 섹터별 ex_cash_psr 중위값(같은 섹터 내 상대 저평가 판정용, 최소 5종목 이상 섹터만)
        sector_psr: dict[str, list[float]] = {}
        for r in rows:
            if r["ex_cash_psr"] is not None and r["ex_cash_psr"] > 0 and r["sector_large"]:
                sector_psr.setdefault(r["sector_large"], []).append(r["ex_cash_psr"])
        sector_median = {}
        for sec, vals in sector_psr.items():
            if len(vals) >= 5:
                vals_sorted = sorted(vals)
                mid = len(vals_sorted) // 2
                sector_median[sec] = vals_sorted[mid] if len(vals_sorted) % 2 else (vals_sorted[mid - 1] + vals_sorted[mid]) / 2

        for r in rows:
            med = sector_median.get(r["sector_large"])
            r["sector_median_ex_cash_psr"] = round(med, 2) if med else None
            r["screen3_undervalued_vs_sector"] = bool(
                med is not None and r["ex_cash_psr"] is not None and r["ex_cash_psr"] > 0 and r["ex_cash_psr"] < med
            ) or r["below_cash_value"]
            if r["screen3_undervalued_vs_sector"]:
                r["score"] = int(r["screen1_turnaround"]) + int(r["screen2_revenue_ath"]) + 1
            else:
                r["score"] = int(r["screen1_turnaround"]) + int(r["screen2_revenue_ath"])

        rows.sort(key=lambda r: (-r["score"], r["dilution_risk"], -(r["backlog_correlation"] or -1)))

        three_screen = [r for r in rows if r["score"] == 3]
        two_screen = [r for r in rows if r["score"] == 2]

        return {
            "computed_at": datetime.now().isoformat(),
            "min_mktcap": min_mktcap, "max_mktcap": max_mktcap,
            "universe_scanned": len(rows),
            "three_screen_pass": three_screen[:100],
            "two_screen_pass": two_screen[:150],
            "all_candidates": rows[:400],
            "research_note": (
                "체리형부 채널(telegram_messages 3,657건 + report_files 491건) 원문 리포트 "
                "'피델릭스_종합분석_260805.pdf', '뉴엔AI_분석글.pdf'를 직접 읽고 역설계한 "
                "'3대 스크리닝'(①적자후흑자전환 ②매출 역대최대 ③주가 미반영/사업가치 저평가) "
                "프레임워크를 전종목(중소형주 시총 구간)에 기계적으로 적용한 결과. "
                "선행지표상관/기관매집/희석위험/꿈촉매는 이미 이 프로젝트에서 walk-forward 검증된 "
                "신호를 재사용. 업스트림 사이클 포지셔닝(예: D램 현물가 vs 개별기업 판가시차), "
                "P×Q 분해, 오버행/락업 스케줄 등 사람의 정성적 해석이 필요한 부분은 자동화하지 "
                "않았으며, 이 스크리너는 '심층분석할 후보를 걸러내는 1차 필터'로 설계됨."
            ),
        }
    finally:
        conn.close()


def _cache_read() -> dict | None:
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text())
    except Exception:
        pass
    return None


def _cache_write(data: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(CACHE_PATH)
    except Exception:
        pass


def refresh_cherry_screener_cache(min_mktcap: float = 300.0, max_mktcap: float = 30000.0) -> dict:
    data = _compute_cherry_screener(min_mktcap, max_mktcap)
    _cache_write(data)
    return data


@router.get("")
def get_cherry_screener(force: bool = Query(False)):
    if not force:
        cached = _cache_read()
        if cached:
            return cached
    return refresh_cherry_screener_cache()


@router.post("/precompute")
def precompute_cherry_screener():
    data = refresh_cherry_screener_cache()
    return {"ok": True, "universe_scanned": data["universe_scanned"],
            "three_screen_pass": len(data["three_screen_pass"])}


@router.get("/detail/{stock_code}")
def get_cherry_screener_detail(stock_code: str):
    cached = _cache_read() or refresh_cherry_screener_cache()
    for bucket in ("three_screen_pass", "two_screen_pass", "all_candidates"):
        for r in cached.get(bucket, []):
            if r["stock_code"] == stock_code:
                return r
    # 캐시(중소형 시총구간 상위 400)에 없으면 즉시 단일종목 재계산(전 시총 허용)
    full = _compute_cherry_screener(min_mktcap=0, max_mktcap=1e9)
    for bucket in ("three_screen_pass", "two_screen_pass", "all_candidates"):
        for r in full.get(bucket, []):
            if r["stock_code"] == stock_code:
                return r
    return {"stock_code": stock_code, "found": False}


@router.get("/analysis/{stock_code}")
def get_cherry_screener_analysis(stock_code: str):
    detail = get_cherry_screener_detail(stock_code)
    if not detail or detail.get("found") is False:
        return {"stock_code": stock_code, "found": False}

    conn = connect_stock_db(timeout=15, row_factory=sqlite3.Row, readonly=True)
    try:
        stock_name = _find_stock_name(conn, stock_code)
        financials = _load_recent_financials(conn, stock_code)
        disclosures = _load_recent_disclosures(conn, stock_code)
        contracts = _load_recent_contracts(conn, stock_code)
        rd_signals = _load_recent_rd_signals(conn, stock_code)
        dilution_events = _load_recent_dilution_events(conn, stock_code)
        major_holders = _load_recent_major_holders(conn, stock_code)
        buzz = _load_telegram_buzz(conn, stock_name)
        reports = _load_related_reports(conn, stock_code, stock_name)
        messages = _load_related_messages(conn, stock_code, stock_name)
        narrative = _build_cherry_narrative(detail, financials, disclosures)
        thesis = _build_humanized_thesis(
            detail=detail,
            financials=financials,
            disclosures=disclosures,
            contracts=contracts,
            rd_signals=rd_signals,
            dilution_events=dilution_events,
            major_holders=major_holders,
            buzz=buzz,
        )
        markdown = _render_cherry_analysis_markdown(
            stock_name=stock_name,
            stock_code=stock_code,
            detail=detail,
            financials=financials,
            disclosures=disclosures,
            contracts=contracts,
            rd_signals=rd_signals,
            dilution_events=dilution_events,
            major_holders=major_holders,
            buzz=buzz,
            reports=reports,
            messages=messages,
            narrative=narrative,
            thesis=thesis,
        )
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "as_of": detail.get("as_of"),
            "framework": "체리형부식 3대 스크리닝 자동분석 초안",
            "screening": detail,
            "thesis": thesis,
            "financials": financials,
            "disclosures": disclosures,
            "contracts": contracts,
            "rd_signals": rd_signals,
            "dilution_events": dilution_events,
            "major_holders": major_holders,
            "telegram_buzz": buzz,
            "related_reports": reports,
            "telegram_messages": messages,
            "narrative": narrative,
            "markdown": markdown,
        }
    finally:
        conn.close()
