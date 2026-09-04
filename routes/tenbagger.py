"""
routes/tenbagger.py — 텐버거 종목 발굴 API

GET  /api/tenbagger/results           최신 발굴 회차 결과
GET  /api/tenbagger/history           발굴 이력 (회차 목록)
GET  /api/tenbagger/run-history       회차별 상세 결과
POST /api/tenbagger/run               수동 발굴 실행 (백그라운드)
GET  /api/tenbagger/status            마지막 실행 상태
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date as _date_cls, datetime, timedelta
from pathlib import Path
from typing import Any

from config import IS_POSTGRES
from db_compat import connect_primary_db
from db_utils import STOCK_DB_PATH, connect_stock_db
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from env_utils import BASE_DIR
from services.gemini import generate_text, is_configured, model_name
from services.gemini_openai_compat import OpenAI

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(STOCK_DB_PATH)
EMP_DB_PATH = str(BASE_DIR / "employment_monitor" / "employment.db")
TRADE_DB_PATH = str(BASE_DIR / "hs_trade_lab" / "data" / "hs_trade_lab.db")
SECTOR_AI_CACHE_PATH = BASE_DIR / "scratch" / "sector_ai_daily.json"
TURNAROUND_WATCH_CACHE_PATH = BASE_DIR / "scratch" / "turnaround_watch_cache.json"
HISTORICAL_SCOREBOARD_V2_PATH = (
    BASE_DIR / "research_outputs" / "historical_tenbagger_scoreboard_v2.json"
)
HISTORICAL_CAUSES_PATH = BASE_DIR / "research_outputs" / "historical_tenbagger_causes.json"
HISTORICAL_SIGNAL_DISCOVERY_PATH = (
    BASE_DIR / "research_outputs" / "historical_tenbagger_signal_discovery.json"
)


def _to_date(v: Any) -> str | None:
    """price_history.date 비교에 바인딩할 값을 항상 'YYYY-MM-DD' 문자열로 정규화.

    2026-08-24 정정: price_history.date는 PostgreSQL에서도 실제로는 TEXT 컬럼이다
    (information_schema로 실측 확인). 애초 버그는 반대 방향이었음 — 일부 호출부의
    sel_date가 `DATE(run_time) AS sel_date`처럼 SQL DATE() 함수를 거쳐 들어오는데,
    PostgreSQL의 DATE()는 진짜 datetime.date 객체를 반환한다. 이 객체를 바인딩하면
    psycopg가 date OID로 보내 TEXT 컬럼과 비교 시 "operator does not exist:
    text > date"로 실패한다(SQLite는 전부 TEXT 비교라 무해했음). str이든 date/datetime
    객체든 전부 문자열로 맞춰 바인딩하는 것이 올바른 방향."""
    if v is None:
        return None
    if isinstance(v, (datetime, _date_cls)):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]

# 턴어라운드 확률 예측 로지스틱 모델 (2026-07-24, 발굴/리스트업 전용 — 매매전략(moonshot 등)에는
# 연결하지 않음). 기존 comprehensive_score(재도전/매출YoY성장/이익의질 3개 이진신호를 동일가중
# 1점씩 합산)를 그대로 학습기(<=2022)/검증기(2023+) 데이터로 로지스틱회귀에 넣어 "실제 예측력에
# 비례한" 가중치를 학습 — 3개 신호 모두 이진(binary)만 사용(연속값인 매출YoY% 자체를 직접 넣으면
# 소형주 기저효과 노이즈에 취약함을 같은 날 moonshot_turnaround 동점처리 실험에서 확인했기 때문).
# 검증 결과: 상위10% lift=1.96x/상위20% lift=2.24x로 기존 3점버킷(lift=1.60x)보다 개선
# (scratch/claude_turnaround_logistic_model_20260724.py, research_outputs/claude_turnaround_
# logistic_weights_20260724.json). 학습된 가중치가 재도전(0.83) > 이익의질(0.53) > 매출성장(0.22)
# 순으로 불균등함을 확인 — 기존 동일가중 가정보다 실제에 가까움.
_TA_LOGISTIC_W = {"intercept": -1.1938, "reattempt": 0.8291, "rev_growth": 0.2236, "quality": 0.5329}


def _turnaround_probability(has_reattempt: bool, has_rev_growth: bool, has_quality: bool) -> float:
    z = (_TA_LOGISTIC_W["intercept"]
         + _TA_LOGISTIC_W["reattempt"] * int(bool(has_reattempt))
         + _TA_LOGISTIC_W["rev_growth"] * int(bool(has_rev_growth))
         + _TA_LOGISTIC_W["quality"] * int(bool(has_quality)))
    if z < -30:
        return 0.0
    if z > 30:
        return 100.0
    return round(100.0 / (1.0 + math.exp(-z)), 1)
TRIGGER_ANALYSIS_PATH = BASE_DIR / "research_outputs" / "signal_trigger_analysis_2020plus.json"
TRIGGER_ANALYSIS_SCRIPT = BASE_DIR / "scripts" / "research_signal_trigger_analysis.py"

_run_lock   = threading.Lock()
_run_status = {"running": False, "last_run": None, "last_count": 0, "error": None}
_DATA_STATUS_CACHE: dict[str, Any] = {"data": None, "at": 0.0}
_DATA_STATUS_CACHE_TTL_SEC = 300


def _get_conn():
    return connect_primary_db(timeout=30, row_factory=sqlite3.Row)


def _ro_conn(path: str):
    resolved = str(Path(path).resolve())
    if resolved == str(STOCK_DB_PATH):
        return connect_primary_db(timeout=30, row_factory=sqlite3.Row, readonly=True)
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_full_price_date(conn: sqlite3.Connection, min_coverage: int = 2000) -> str | None:
    row = conn.execute(
        """
        SELECT date
        FROM price_history
        WHERE close > 0
        GROUP BY date
        HAVING COUNT(DISTINCT stock_code) >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (min_coverage,),
    ).fetchone()
    return row["date"] if row else None


def _price_return_pct(conn: sqlite3.Connection, code: str, as_of: str, days: int) -> float | None:
    start = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT close
        FROM price_history
        WHERE stock_code=? AND date>=? AND date<=? AND close>0
        ORDER BY date
        """,
        (code, start, as_of),
    ).fetchall()
    if len(rows) < 3:
        return None
    first = float(rows[0]["close"] or 0)
    last = float(rows[-1]["close"] or 0)
    return (last / first - 1.0) * 100.0 if first > 0 else None


def _price_risk(ret_1m: float | None, ret_3m: float | None) -> dict[str, Any]:
    flags: list[str] = []
    penalty = 0
    level = "OK"
    label = "가격 정상"

    if ret_3m is not None and ret_3m <= -50:
        flags.append(f"3개월 {ret_3m:.1f}% 급락")
        penalty = max(penalty, 18)
        level = "AVOID"
        label = "회피: 가격붕괴"
    elif ret_3m is not None and ret_3m <= -35:
        flags.append(f"3개월 {ret_3m:.1f}% 하락")
        penalty = max(penalty, 10)
        level = "WATCH_PRICE"
        label = "가격확인 필요"

    if ret_1m is not None and ret_1m <= -20:
        flags.append(f"1개월 {ret_1m:.1f}% 급락")
        penalty = max(penalty, 8)
        if level == "OK":
            level = "WATCH_PRICE"
            label = "단기급락 확인"

    return {
        "price_risk": level,
        "price_risk_label": label,
        "price_risk_penalty": penalty,
        "price_risk_flags": flags,
    }


def _enrich_tenbagger_price_risk(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    as_of = _latest_full_price_date(conn)
    if not as_of:
        return rows
    for row in rows:
        code = str(row.get("stock_code") or "").zfill(6)
        if not code or code == "000000":
            continue
        ret_1m = _price_return_pct(conn, code, as_of, 30)
        ret_3m = _price_return_pct(conn, code, as_of, 90)
        risk = _price_risk(ret_1m, ret_3m)
        base_score = float(
            row.get("total_score")
            or row.get("score")
            or row.get("combined_score")
            or row.get("signal_score")
            or 0
        )
        row["price_as_of"] = as_of
        row["price_return_1m"] = round(ret_1m, 1) if ret_1m is not None else None
        row["price_return_3m"] = round(ret_3m, 1) if ret_3m is not None else None
        row.update(risk)
        row["risk_adjusted_score"] = round(max(0.0, base_score - risk["price_risk_penalty"]), 1)
    return rows


def _triple_winner_reason(row: dict) -> str:
    sector = row.get("sector") or "미분류"
    theme = row.get("theme") or ""
    multiple = row.get("multiple")
    vol_peak = row.get("vol_peak_x")
    amount_peak = row.get("amount_peak_x")

    sector_reason = {
        "IT": "IT/반도체/소프트웨어 계열은 성장 기대와 테마 순환매가 붙을 때 재평가 속도가 빠릅니다.",
        "의료": "바이오·의료기기 계열은 임상, 허가, 진단, 신제품 기대가 가격에 빠르게 반영되는 경우가 많습니다.",
        "산업재": "산업재는 로봇, 방산, 건설, 운송, 설비투자 기대가 생길 때 수주/정책 모멘텀으로 확산됩니다.",
        "소재": "소재주는 2차전지, 화학, 원자재, 구조조정 기대가 붙으면 업황 민감도가 크게 반영됩니다.",
        "경기소비재": "소비재·미디어·엔터 계열은 흥행, 브랜드, 정책/테마 수급이 결합될 때 탄력이 큽니다.",
        "필수소비재": "음식료·생활용품 계열은 원가, 가격 인상, 사료/곡물 등 방어주와 테마 성격이 함께 작동합니다.",
        "금융": "금융주는 금리, 증시 거래대금, 지분/경영권 이슈가 생길 때 저평가 해소 흐름이 나타납니다.",
        "에너지": "에너지주는 원자재 가격, 정책, 공급망 이슈가 맞물릴 때 짧은 기간에 수급이 집중됩니다.",
        "유틸리티": "유틸리티는 요금, 정책, 에너지 전환 기대가 붙을 때 방어주 성격에서 모멘텀주로 바뀔 수 있습니다.",
        "통신서비스": "통신서비스는 구조개편, 신사업, 저평가 해소 기대가 붙을 때 거래대금이 급증하는 경향이 있습니다.",
    }.get(sector, "섹터 분류가 제한적이어서 가격·거래량 기반 모멘텀을 우선 확인해야 합니다.")

    flow_bits = []
    if vol_peak is not None:
        flow_bits.append(f"거래량 피크 {vol_peak:.1f}배")
    if amount_peak is not None:
        flow_bits.append(f"거래대금 피크 {amount_peak:.1f}배")
    flow = " · ".join(flow_bits)
    if flow:
        flow = f" 정량적으로는 {flow}가 확인되어 수급 집중이 상승을 뒷받침했습니다."
    mult = f" 연중 저가 대비 고가 기준 {multiple:.2f}배 상승했습니다." if multiple else ""
    theme_txt = f" 세부 테마는 {theme}입니다." if theme else ""
    return f"{sector_reason}{theme_txt}{flow}{mult}"


def _dist(rows: list[dict], key: str, limit: int = 10) -> list[dict]:
    total = len(rows) or 1
    counts = Counter((r.get(key) or "미분류") for r in rows)
    return [
        {"name": name, "count": count, "pct": round(count / total * 100, 1)}
        for name, count in counts.most_common(limit)
    ]


def _metric_stats(rows: list[dict], key: str) -> dict:
    vals = sorted(float(r[key]) for r in rows if r.get(key) is not None)
    if not vals:
        return {"count": 0, "avg": None, "median": None, "p75": None, "p90": None}

    def pct(p: float) -> float:
        idx = min(len(vals) - 1, max(0, int(len(vals) * p)))
        return round(vals[idx], 2)

    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 2),
        "median": pct(0.5),
        "p75": pct(0.75),
        "p90": pct(0.9),
    }


def _monthly_period_plus(period: str, months: int) -> str | None:
    try:
        y = int(period[:4])
        m = int(period[5:7])
    except Exception:
        return None
    total = y * 12 + (m - 1) + months
    ny, nm = divmod(total, 12)
    return f"{ny:04d}-{nm + 1:02d}"


def _quarter_available_date(year: int, quarter: int) -> str:
    month_map = {1: 5, 2: 8, 3: 11, 4: 3}
    rel_year = year + 1 if quarter == 4 else year
    month = month_map.get(quarter, 5)
    return f"{rel_year:04d}-{month:02d}-15"


def _load_quarterly_rows_for_codes(conn: sqlite3.Connection, codes: list[str]) -> dict[str, list[dict]]:
    if not codes:
        return {}
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT stock_code, year, quarter, revenue, operating_profit, net_income,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, year, quarter
                       ORDER BY CASE WHEN report_type = 'CFS' THEN 0 ELSE 1 END,
                                CASE WHEN LOWER(data_source) >= 'fnguide' AND LOWER(data_source) < 'fnguidf' THEN 0
                                     WHEN LOWER(data_source) >= 'dart' AND LOWER(data_source) < 'daru' THEN 1 ELSE 2 END,
                                id DESC
                   ) AS rn
            FROM financial_data
            WHERE is_annual = 0
              AND quarter BETWEEN 1 AND 4
              AND stock_code IN ({ph})
        )
        SELECT stock_code, year, quarter, revenue, operating_profit, net_income
        FROM ranked
        WHERE rn = 1
        ORDER BY stock_code, year DESC, quarter DESC
        """,
        codes,
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(str(row["stock_code"]), []).append(dict(row))
    return out


def _earnings_backed_profile(snapshot_date: str, code: str, qrows_by_code: dict[str, list[dict]]) -> dict[str, Any]:
    as_of = snapshot_date[:10]
    rows = [
        r for r in (qrows_by_code.get(code) or [])
        if _quarter_available_date(int(r["year"]), int(r["quarter"])) <= as_of
    ]
    rows = rows[:8]
    lookup = {(int(r["year"]), int(r["quarter"])): r for r in rows}
    tags: list[str] = []
    rev_yoy = None
    op_yoy = None
    latest_op = None
    latest_rev = None

    if rows:
        latest = rows[0]
        latest_rev = _num(latest.get("revenue"))
        latest_op = _num(latest.get("operating_profit"))
        prev_same_q = lookup.get((int(latest["year"]) - 1, int(latest["quarter"])))
        if prev_same_q:
            rev_yoy = _pct_change(latest_rev, _num(prev_same_q.get("revenue")))
            op_yoy = _pct_change(latest_op, _num(prev_same_q.get("operating_profit")))

        if rev_yoy is not None and rev_yoy >= 10:
            tags.append("매출YoY상승")
        if latest_op is not None and latest_op > 0:
            tags.append("영업흑자")
        if op_yoy is not None and op_yoy >= 20:
            tags.append("영업이익YoY상승")
        if prev_same_q and _num(prev_same_q.get("operating_profit")) is not None and _num(prev_same_q.get("operating_profit")) <= 0 and (latest_op or 0) > 0:
            tags.append("흑자전환")

    yoy_rev_streak = _consecutive_yoy(rows, "revenue") if rows else 0
    yoy_op_streak = _consecutive_yoy(rows, "operating_profit") if rows else 0
    if yoy_rev_streak >= 2:
        tags.append("매출연속성장")
    if yoy_op_streak >= 2:
        tags.append("이익연속성장")

    earnings_backed = any(t in tags for t in (
        "매출YoY상승", "영업이익YoY상승", "흑자전환", "매출연속성장", "이익연속성장"
    )) and ("영업흑자" in tags or "흑자전환" in tags or (op_yoy is not None and op_yoy > 0))

    return {
        "earnings_backed": bool(earnings_backed),
        "issue_driven_proxy": not bool(earnings_backed),
        "earnings_tags": tags,
        "rev_yoy_pct": round(rev_yoy, 1) if rev_yoy is not None else None,
        "op_yoy_pct": round(op_yoy, 1) if op_yoy is not None else None,
        "yoy_rev_streak": yoy_rev_streak,
        "yoy_op_streak": yoy_op_streak,
        "latest_op_positive": bool(latest_op is not None and latest_op > 0),
    }


def _share(rows: list[dict], predicate) -> dict:
    total = len(rows) or 1
    count = sum(1 for r in rows if predicate(r))
    return {"count": count, "pct": round(count / total * 100, 1)}


def _build_triple_pattern_stats(rows: list[dict]) -> dict:
    total = len(rows)
    code_counts = Counter(r["code"] for r in rows)
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_code[r["code"]].append(r)

    repeaters = []
    for code, count in code_counts.most_common(20):
        if count < 2:
            continue
        items = sorted(by_code[code], key=lambda x: x["year"])
        repeaters.append({
            "code": code,
            "name": items[-1].get("name"),
            "count": count,
            "years": [str(x["year"]) for x in items],
            "sector": items[-1].get("sector"),
            "theme": items[-1].get("theme"),
        })

    by_year = {}
    for year in sorted({str(r["year"]) for r in rows}, reverse=True):
        items = [r for r in rows if str(r["year"]) == year]
        by_year[year] = {
            "count": len(items),
            "market": _dist(items, "market", 3),
            "sector": _dist(items, "sector", 5),
            "theme": _dist(items, "theme", 5),
        }

    volume10 = _share(rows, lambda r: (r.get("vol_peak_x") or 0) >= 10)
    amount10 = _share(rows, lambda r: (r.get("amount_peak_x") or 0) >= 10)
    kosdaq = _share(rows, lambda r: r.get("market") == "KOSDAQ")
    core_sectors = {"IT", "의료", "경기소비재", "산업재"}
    core_sector_share = _share(rows, lambda r: r.get("sector") in core_sectors)
    small_cap = _share(rows, lambda r: r.get("market_cap") is not None and r.get("market_cap") <= 3000)

    triggers = [
        {
            "rank": 1,
            "name": "거래대금 급증",
            "signal": "연중 거래대금 피크가 평소 대비 10배 이상",
            "hit_rate": amount10["pct"],
            "why": "가격 3배 구간은 거의 항상 시장 관심의 급격한 유입이 먼저 확인됩니다.",
        },
        {
            "rank": 2,
            "name": "거래량 급증",
            "signal": "연중 거래량 피크가 평소 대비 10배 이상",
            "hit_rate": volume10["pct"],
            "why": "유동성 공급이 약한 중소형주에서 거래량 확장은 가격 재평가의 직접 트리거입니다.",
        },
        {
            "rank": 3,
            "name": "KOSDAQ 중소형주",
            "signal": "KOSDAQ + 시가총액 3,000억 이하",
            "hit_rate": _share(rows, lambda r: r.get("market") == "KOSDAQ" and r.get("market_cap") is not None and r.get("market_cap") <= 3000)["pct"],
            "why": "작은 시가총액일수록 같은 자금 유입에도 가격 탄력성이 큽니다.",
        },
        {
            "rank": 4,
            "name": "성장/테마 섹터",
            "signal": "IT·의료·경기소비재·산업재 중 하나",
            "hit_rate": core_sector_share["pct"],
            "why": "7개년 내내 주도 테마는 바뀌지만 이 네 섹터 안에서 대부분 순환했습니다.",
        },
        {
            "rank": 5,
            "name": "고변동 반복 종목군",
            "signal": "2개 연도 이상 3배 구간 재진입",
            "hit_rate": _share(rows, lambda r: code_counts[r["code"]] >= 2)["pct"],
            "why": "일부 종목은 사업 안정성보다 테마·수급 민감도가 높아 여러 해 반복 출현합니다.",
        },
    ]

    return {
        "total": total,
        "market": _dist(rows, "market", 5),
        "sector": _dist(rows, "sector", 10),
        "theme": _dist(rows, "theme", 12),
        "metrics": {
            "multiple": _metric_stats(rows, "multiple"),
            "vol_peak_x": _metric_stats(rows, "vol_peak_x"),
            "amount_peak_x": _metric_stats(rows, "amount_peak_x"),
            "market_cap": _metric_stats(rows, "market_cap"),
        },
        "shares": {
            "kosdaq": kosdaq,
            "core_sectors": core_sector_share,
            "volume_peak_10x": volume10,
            "amount_peak_10x": amount10,
            "small_cap_3000억": small_cap,
        },
        "triggers": triggers,
        "repeaters": repeaters[:12],
        "by_year": by_year,
    }


@router.get("/triple-winners-by-year")
def triple_winners_by_year(
    start_year: int = Query(2020, ge=2010, le=2030),
    end_year: int = Query(2026, ge=2010, le=2030),
    limit_per_year: int = Query(80, ge=1, le=300),
    min_multiple: float = Query(3.0, ge=1.0, le=1000.0),
    max_multiple: float = Query(50.0, ge=3.0, le=1000.0),
    min_price: float = Query(100.0, ge=0.0, le=100000.0),
):
    """연도별 저가 대비 고가 3배 이상 상승 종목.

    보정 왜곡 가능성이 큰 항목은 API 단계에서 제외한다.
    """
    if end_year < start_year:
        start_year, end_year = end_year, start_year
    if max_multiple < min_multiple:
        max_multiple = min_multiple

    with _ro_conn(DB_PATH) as conn:
        summary_rows = conn.execute(
            """
            WITH su AS (
                SELECT
                    stock_code,
                    MAX(stock_name) AS stock_name
                FROM stock_universe
                WHERE market IN ('KOSPI', 'KOSDAQ')
                  AND COALESCE(stock_type, '') = '보통주'
                  AND COALESCE(secugrp_nm, '') = '주권'
                  AND COALESCE(kind_stkcert_nm, '') = '보통주'
                  AND stock_name IS NOT NULL
                  AND stock_name != ''
                GROUP BY stock_code
            ),
            yearly AS (
                SELECT
                    p.stock_code,
                    CAST(substr(p.date, 1, 4) AS INTEGER) AS year,
                    MIN(p.low) AS min_low,
                    MAX(p.high) AS max_high,
                    COUNT(*) AS trading_days
                FROM price_history p
                JOIN su ON su.stock_code = p.stock_code
                WHERE p.date >= ?
                  AND p.date < ?
                  AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND p.low >= ?
                  AND p.high > 0
                GROUP BY p.stock_code, CAST(substr(p.date, 1, 4) AS INTEGER)
            )
            SELECT year, COUNT(*) AS count, MAX(max_high / min_low) AS top_multiple
            FROM yearly
            WHERE trading_days >= 60
              AND max_high / min_low BETWEEN ? AND ?
            GROUP BY year
            ORDER BY year DESC
            """,
            (f"{start_year}-01-01", f"{end_year + 1}-01-01", min_price, min_multiple, max_multiple),
        ).fetchall()

        rows = conn.execute(
            """
            WITH su AS (
                SELECT
                    stock_code,
                    MAX(stock_name) AS stock_name,
                    MAX(market) AS market,
                    MAX(sector_large) AS sector,
                    MAX(sector_mid) AS theme
                FROM stock_universe
                WHERE market IN ('KOSPI', 'KOSDAQ')
                  AND COALESCE(stock_type, '') = '보통주'
                  AND COALESCE(secugrp_nm, '') = '주권'
                  AND COALESCE(kind_stkcert_nm, '') = '보통주'
                  AND stock_name IS NOT NULL
                  AND stock_name != ''
                GROUP BY stock_code
            ),
            yearly AS (
                SELECT
                    p.stock_code,
                    CAST(substr(p.date, 1, 4) AS INTEGER) AS year,
                    MIN(p.low) AS min_low,
                    MAX(p.high) AS max_high,
                    AVG(NULLIF(p.volume, 0)) AS avg_volume,
                    MAX(p.volume) AS max_volume,
                    AVG(COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume)) AS avg_trade_amount,
                    MAX(COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume)) AS max_trade_amount,
                    COUNT(*) AS trading_days
                FROM price_history p
                JOIN su ON su.stock_code = p.stock_code
                WHERE p.date >= ?
                  AND p.date < ?
                  AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND p.low >= ?
                  AND p.high > 0
                GROUP BY p.stock_code, CAST(substr(p.date, 1, 4) AS INTEGER)
            ),
            ranked AS (
                SELECT
                    y.*,
                    ROUND(CAST(y.max_high / y.min_low AS NUMERIC), 2) AS multiple,
                    ROW_NUMBER() OVER (
                        PARTITION BY y.year
                        ORDER BY y.max_high / y.min_low DESC
                    ) AS rn
                FROM yearly y
                WHERE y.trading_days >= 60
                  AND y.max_high / y.min_low BETWEEN ? AND ?
            )
            SELECT
                r.year,
                r.stock_code AS code,
                COALESCE(su.stock_name, r.stock_code) AS name,
                COALESCE(su.market, '') AS market,
                COALESCE(su.sector, '미분류') AS sector,
                COALESCE(su.theme, '') AS theme,
                r.multiple,
                ROUND(CAST(r.max_volume / NULLIF(r.avg_volume, 0) AS NUMERIC), 1) AS vol_peak_x,
                ROUND(CAST(r.max_trade_amount / NULLIF(r.avg_trade_amount, 0) AS NUMERIC), 1) AS amount_peak_x
            FROM ranked r
            JOIN su ON su.stock_code = r.stock_code
            WHERE r.rn <= ?
            ORDER BY r.year DESC, r.multiple DESC
            """,
            (f"{start_year}-01-01", f"{end_year + 1}-01-01", min_price, min_multiple, max_multiple, limit_per_year),
        ).fetchall()

        stats_rows = conn.execute(
            """
            WITH su AS (
                SELECT
                    stock_code,
                    MAX(stock_name) AS stock_name,
                    MAX(market) AS market,
                    MAX(sector_large) AS sector,
                    MAX(sector_mid) AS theme,
                    MAX(market_cap) AS market_cap
                FROM stock_universe
                WHERE market IN ('KOSPI', 'KOSDAQ')
                  AND COALESCE(stock_type, '') = '보통주'
                  AND COALESCE(secugrp_nm, '') = '주권'
                  AND COALESCE(kind_stkcert_nm, '') = '보통주'
                  AND stock_name IS NOT NULL
                  AND stock_name != ''
                GROUP BY stock_code
            ),
            yearly AS (
                SELECT
                    p.stock_code,
                    CAST(substr(p.date, 1, 4) AS INTEGER) AS year,
                    MIN(p.low) AS min_low,
                    MAX(p.high) AS max_high,
                    AVG(NULLIF(p.volume, 0)) AS avg_volume,
                    MAX(p.volume) AS max_volume,
                    AVG(COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume)) AS avg_trade_amount,
                    MAX(COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume)) AS max_trade_amount,
                    COUNT(*) AS trading_days
                FROM price_history p
                JOIN su ON su.stock_code = p.stock_code
                WHERE p.date >= ?
                  AND p.date < ?
                  AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND p.low >= ?
                  AND p.high > 0
                GROUP BY p.stock_code, CAST(substr(p.date, 1, 4) AS INTEGER)
            )
            SELECT
                y.year,
                y.stock_code AS code,
                su.stock_name AS name,
                su.market,
                COALESCE(su.sector, '미분류') AS sector,
                COALESCE(su.theme, '') AS theme,
                su.market_cap,
                ROUND(CAST(y.max_high / y.min_low AS NUMERIC), 2) AS multiple,
                ROUND(CAST(y.max_volume / NULLIF(y.avg_volume, 0) AS NUMERIC), 1) AS vol_peak_x,
                ROUND(CAST(y.max_trade_amount / NULLIF(y.avg_trade_amount, 0) AS NUMERIC), 1) AS amount_peak_x
            FROM yearly y
            JOIN su ON su.stock_code = y.stock_code
            WHERE y.trading_days >= 60
              AND y.max_high / y.min_low BETWEEN ? AND ?
            ORDER BY y.year DESC, multiple DESC
            """,
            (f"{start_year}-01-01", f"{end_year + 1}-01-01", min_price, min_multiple, max_multiple),
        ).fetchall()

    years: dict[str, list[dict]] = {}
    for r in rows:
        item = dict(r)
        item["multiple"] = _num(item.get("multiple"), 2)
        item["vol_peak_x"] = _num(item.get("vol_peak_x"), 1)
        item["amount_peak_x"] = _num(item.get("amount_peak_x"), 1)
        item["reason"] = _triple_winner_reason(item)
        years.setdefault(str(item["year"]), []).append(item)

    summary = []
    for r in summary_rows:
        y = str(r["year"])
        visible = years.get(y, [])
        sectors = []
        for item in visible:
            s = item.get("sector") or "미분류"
            if s not in sectors:
                sectors.append(s)
        summary.append({
            "year": y,
            "count": int(r["count"] or 0),
            "shown": len(visible),
            "top_multiple": _num(r["top_multiple"], 2),
            "sectors": sectors[:5],
        })

    stats_items = []
    for r in stats_rows:
        item = dict(r)
        item["multiple"] = _num(item.get("multiple"), 2)
        item["vol_peak_x"] = _num(item.get("vol_peak_x"), 1)
        item["amount_peak_x"] = _num(item.get("amount_peak_x"), 1)
        item["market_cap"] = _num(item.get("market_cap"), 1)
        stats_items.append(item)

    return {
        "ok": True,
        "source": "stock.db price_history",
        "method": "calendar-year max(high) / min(low)",
        "start_year": start_year,
        "end_year": end_year,
        "min_multiple": min_multiple,
        "max_multiple": max_multiple,
        "min_price": min_price,
        "limit_per_year": limit_per_year,
        "summary": summary,
        "pattern_stats": _build_triple_pattern_stats(stats_items),
        "years": years,
        "notice": "KOSPI/KOSDAQ 보통주 중 연중 저가 대비 고가 3~50배 종목만 표시합니다. 종목명 없는 코드, 보통주 외 증권, 100원 미만 저가/권리락 의심 데이터, 과도한 배율 이상치는 제외했습니다.",
    }


@router.get("/historical-tenbaggers")
def historical_tenbaggers(
    horizon: str = Query("24m", description="24m|36m"),
    multiple: int = Query(10, ge=3, le=10, description="3|5|10"),
    start_date: str = Query("2020-01-01"),
    end_date: str = Query("2026-08-08"),
    analysis_focus: str = Query("earnings", description="earnings|all"),
    min_score: float = Query(55.0, ge=0, le=100),
    min_turnover_억: float = Query(3.0, ge=0, le=1000),
    max_mktcap_억: float = Query(3000.0, ge=0, le=100000),
    limit: int = Query(200, ge=10, le=1000),
):
    """
    과거 월말 스냅샷에서 실제 3배/5배/10배 달성 종목을 '첫 포착 시점' 기준으로 재구성한다.
    미래 후보 추천이 아니라, 과거 대승주를 당시 어떤 상태에서 먼저 포착할 수 있었는지 보기 위한 API.
    """
    if horizon not in {"24m", "36m"}:
        raise HTTPException(status_code=400, detail="horizon must be 24m or 36m")
    if multiple not in {3, 5, 10}:
        raise HTTPException(status_code=400, detail="multiple must be 3, 5, or 10")

    label_col = f"label_{multiple}x_{horizon}"
    peak_col = f"forward_max_ret_{horizon}"

    with _ro_conn(DB_PATH) as conn:
        sql = f"""
            WITH base AS (
                SELECT
                    snapshot_date,
                    stock_code,
                    stock_name,
                    market,
                    sector_large,
                    close_price,
                    market_cap_억,
                    per,
                    pbr,
                    ret_20d,
                    ret_60d,
                    ret_120d,
                    dist_high_252,
                    dist_low_252,
                    vol_ratio_20d,
                    avg_turnover_20d_억,
                    supply_20d_억,
                    heuristic_score,
                    model_score_12m,
                    {peak_col} AS forward_peak,
                    {label_col} AS winner_label,
                    ROW_NUMBER() OVER (
                        PARTITION BY stock_code
                        ORDER BY snapshot_date ASC
                    ) AS rn
                FROM strategy_feature_snapshot
                WHERE snapshot_date >= ?
                  AND snapshot_date <= ?
                  AND heuristic_score >= ?
                  AND avg_turnover_20d_억 >= ?
                  AND market_cap_억 <= ?
                  AND COALESCE({label_col}, 0) = 1
            )
            SELECT *
            FROM base
            WHERE rn = 1
            ORDER BY snapshot_date ASC, heuristic_score DESC, model_score_12m DESC
            LIMIT ?
        """
        rows = [dict(r) for r in conn.execute(
            sql,
            (start_date, end_date, min_score, min_turnover_억, max_mktcap_억, limit),
        ).fetchall()]

        if not rows:
            return {
                "meta": {
                    "horizon": horizon,
                    "multiple": multiple,
                    "label_col": label_col,
                    "peak_col": peak_col,
                    "total": 0,
                },
                "summary": {},
                "results": [],
            }

        code_set = sorted({str(r["stock_code"]) for r in rows if r.get("stock_code")})
        qrows_by_code = _load_quarterly_rows_for_codes(conn, code_set)
        ph = ",".join("?" * len(code_set))
        month_close_rows = conn.execute(
            f"""
            SELECT stock_code, substr(snapshot_date, 1, 7) AS period, MAX(close_price) AS close_price
            FROM strategy_feature_snapshot
            WHERE stock_code IN ({ph})
            GROUP BY stock_code, substr(snapshot_date, 1, 7)
            """,
            code_set,
        ).fetchall()
        close_map = {
            (str(r["stock_code"]), str(r["period"])): float(r["close_price"] or 0)
            for r in month_close_rows
            if r["close_price"]
        }

        results = []
        for row in rows:
            code = str(row["stock_code"])
            period = str(row["snapshot_date"])[:7]
            p0 = float(row.get("close_price") or 0)
            ret12 = None
            target_12m = _monthly_period_plus(period, 12)
            if target_12m:
                p12 = close_map.get((code, target_12m))
                if p12 and p0 > 0:
                    ret12 = (float(p12) / p0 - 1.0) * 100.0
            earnings = _earnings_backed_profile(str(row["snapshot_date"]), code, qrows_by_code)

            item = {
                "snapshot_date": row["snapshot_date"],
                "stock_code": code,
                "stock_name": row.get("stock_name") or code,
                "market": row.get("market"),
                "sector": row.get("sector_large") or "미분류",
                "heuristic_score": round(float(row.get("heuristic_score") or 0), 1),
                "model_score_12m": round(float(row.get("model_score_12m") or 0), 4),
                "market_cap_억": round(float(row.get("market_cap_억") or 0), 1),
                "pbr": round(float(row["pbr"]), 2) if row.get("pbr") is not None else None,
                "per": round(float(row["per"]), 2) if row.get("per") is not None else None,
                "ret_20d_pct": round(float(row["ret_20d"]) * 100.0, 1) if row.get("ret_20d") is not None else None,
                "ret_60d_pct": round(float(row["ret_60d"]) * 100.0, 1) if row.get("ret_60d") is not None else None,
                "ret_120d_pct": round(float(row["ret_120d"]) * 100.0, 1) if row.get("ret_120d") is not None else None,
                "dist_high_252_pct": round(float(row["dist_high_252"]) * 100.0, 1) if row.get("dist_high_252") is not None else None,
                "dist_low_252_pct": round(float(row["dist_low_252"]) * 100.0, 1) if row.get("dist_low_252") is not None else None,
                "vol_ratio_20d": round(float(row["vol_ratio_20d"] or 0), 2),
                "avg_turnover_20d_억": round(float(row["avg_turnover_20d_억"] or 0), 1),
                "supply_20d_억": round(float(row["supply_20d_억"] or 0), 1),
                "forward_peak_pct": round(float(row["forward_peak"] or 0) * 100.0, 1) if row.get("forward_peak") is not None else None,
                "return_12m_pct": round(ret12, 1) if ret12 is not None else None,
                **earnings,
            }
            if analysis_focus == "all" or item["earnings_backed"]:
                results.append(item)

        summary = {
            "total": len(results),
            "by_market": _dist(results, "market", 4),
            "by_sector": _dist(results, "sector", 8),
            "heuristic_score": _metric_stats(results, "heuristic_score"),
            "model_score_12m": _metric_stats(results, "model_score_12m"),
            "market_cap_억": _metric_stats(results, "market_cap_억"),
            "pbr": _metric_stats(results, "pbr"),
            "avg_turnover_20d_억": _metric_stats(results, "avg_turnover_20d_억"),
            "return_12m_pct": _metric_stats(results, "return_12m_pct"),
            "forward_peak_pct": _metric_stats(results, "forward_peak_pct"),
            "deep_drawdown_share": _share(results, lambda r: (r.get("dist_high_252_pct") or 0) <= -70),
            "near_bottom_share": _share(results, lambda r: (r.get("dist_low_252_pct") or 999) <= 15),
            "volume_2x_share": _share(results, lambda r: (r.get("vol_ratio_20d") or 0) >= 2.0),
            "smallcap_1500_share": _share(results, lambda r: 0 < (r.get("market_cap_억") or 0) <= 1500),
            "earnings_backed_share": _share(results, lambda r: r.get("earnings_backed")),
        }

        return {
            "meta": {
                "horizon": horizon,
                "multiple": multiple,
                "analysis_focus": analysis_focus,
                "label_col": label_col,
                "peak_col": peak_col,
                "start_date": start_date,
                "end_date": end_date,
                "min_score": min_score,
                "min_turnover_억": min_turnover_억,
                "max_mktcap_억": max_mktcap_억,
                "total": len(results),
                "dedupe_rule": "stock_code당 earliest snapshot_date 1건",
            },
            "summary": summary,
            "results": results,
        }


@router.get("/historical-tenbagger-audit")
def historical_tenbagger_audit(
    horizon: str = Query("24m", description="24m|36m"),
    multiple: int = Query(10, ge=3, le=10, description="3|5|10"),
    start_date: str = Query("2020-01-01"),
    end_date: str = Query("2026-08-08"),
    analysis_focus: str = Query("earnings", description="earnings|all"),
    min_score: float = Query(55.0, ge=0, le=100),
    min_turnover_억: float = Query(3.0, ge=0, le=1000),
    max_mktcap_억: float = Query(3000.0, ge=0, le=100000),
    sample_limit: int = Query(30, ge=10, le=200),
):
    """
    과거 실제 3배/5배/10배 승자 중 현재 필터로 잡힌 것(captured)과 놓친 것(missed)을 비교한다.
    """
    if horizon not in {"24m", "36m"}:
        raise HTTPException(status_code=400, detail="horizon must be 24m or 36m")
    if multiple not in {3, 5, 10}:
        raise HTTPException(status_code=400, detail="multiple must be 3, 5, or 10")

    label_col = f"label_{multiple}x_{horizon}"
    peak_col = f"forward_max_ret_{horizon}"

    with _ro_conn(DB_PATH) as conn:
        sql = f"""
            WITH winners AS (
                SELECT
                    snapshot_date,
                    stock_code,
                    stock_name,
                    market,
                    sector_large,
                    close_price,
                    market_cap_억,
                    per,
                    pbr,
                    ret_20d,
                    ret_60d,
                    ret_120d,
                    dist_high_252,
                    dist_low_252,
                    vol_ratio_20d,
                    avg_turnover_20d_억,
                    supply_20d_억,
                    heuristic_score,
                    model_score_12m,
                    {peak_col} AS forward_peak,
                    ROW_NUMBER() OVER (
                        PARTITION BY stock_code
                        ORDER BY snapshot_date ASC
                    ) AS rn
                FROM strategy_feature_snapshot
                WHERE snapshot_date >= ?
                  AND snapshot_date <= ?
                  AND COALESCE({label_col}, 0) = 1
            )
            SELECT *
            FROM winners
            WHERE rn = 1
        """
        all_rows = [dict(r) for r in conn.execute(sql, (start_date, end_date)).fetchall()]

        qrows_by_code = _load_quarterly_rows_for_codes(
            conn, sorted({str(r["stock_code"]) for r in all_rows if r.get("stock_code")})
        )

        def _normalize(row: dict) -> dict:
            base = {
                "snapshot_date": row["snapshot_date"],
                "stock_code": str(row["stock_code"]),
                "stock_name": row.get("stock_name") or str(row["stock_code"]),
                "market": row.get("market"),
                "sector": row.get("sector_large") or "미분류",
                "heuristic_score": float(row.get("heuristic_score") or 0),
                "model_score_12m": float(row.get("model_score_12m") or 0),
                "market_cap_억": float(row.get("market_cap_억") or 0),
                "pbr": float(row["pbr"]) if row.get("pbr") is not None else None,
                "per": float(row["per"]) if row.get("per") is not None else None,
                "ret_20d_pct": float(row["ret_20d"]) * 100.0 if row.get("ret_20d") is not None else None,
                "ret_60d_pct": float(row["ret_60d"]) * 100.0 if row.get("ret_60d") is not None else None,
                "ret_120d_pct": float(row["ret_120d"]) * 100.0 if row.get("ret_120d") is not None else None,
                "dist_high_252_pct": float(row["dist_high_252"]) * 100.0 if row.get("dist_high_252") is not None else None,
                "dist_low_252_pct": float(row["dist_low_252"]) * 100.0 if row.get("dist_low_252") is not None else None,
                "vol_ratio_20d": float(row["vol_ratio_20d"] or 0),
                "avg_turnover_20d_억": float(row["avg_turnover_20d_억"] or 0),
                "supply_20d_억": float(row["supply_20d_억"] or 0),
                "forward_peak_pct": float(row["forward_peak"] or 0) * 100.0 if row.get("forward_peak") is not None else None,
            }
            base.update(_earnings_backed_profile(str(row["snapshot_date"]), str(row["stock_code"]), qrows_by_code))
            return base

        winners = [_normalize(r) for r in all_rows]
        if analysis_focus == "earnings":
            winners = [r for r in winners if r.get("earnings_backed")]
        captured = [
            r for r in winners
            if r["heuristic_score"] >= min_score
            and (r["avg_turnover_20d_억"] or 0) >= min_turnover_억
            and (r["market_cap_억"] or 0) <= max_mktcap_억
        ]
        captured_codes = {r["stock_code"] for r in captured}
        missed = [r for r in winners if r["stock_code"] not in captured_codes]

        def _summary(rows: list[dict]) -> dict:
            if not rows:
                return {"total": 0}
            return {
                "total": len(rows),
                "by_market": _dist(rows, "market", 4),
                "by_sector": _dist(rows, "sector", 8),
                "heuristic_score": _metric_stats(rows, "heuristic_score"),
                "model_score_12m": _metric_stats(rows, "model_score_12m"),
                "market_cap_억": _metric_stats(rows, "market_cap_억"),
                "pbr": _metric_stats(rows, "pbr"),
                "avg_turnover_20d_억": _metric_stats(rows, "avg_turnover_20d_억"),
                "forward_peak_pct": _metric_stats(rows, "forward_peak_pct"),
                "deep_drawdown_share": _share(rows, lambda r: (r.get("dist_high_252_pct") or 0) <= -70),
                "near_bottom_share": _share(rows, lambda r: (r.get("dist_low_252_pct") or 999) <= 15),
                "volume_2x_share": _share(rows, lambda r: (r.get("vol_ratio_20d") or 0) >= 2.0),
                "smallcap_1500_share": _share(rows, lambda r: 0 < (r.get("market_cap_억") or 0) <= 1500),
                "earnings_backed_share": _share(rows, lambda r: r.get("earnings_backed")),
            }

        def _miss_reasons(row: dict) -> list[str]:
            reasons = []
            if row["heuristic_score"] < min_score:
                reasons.append(f"점수 {row['heuristic_score']:.1f} < {min_score:.1f}")
            if (row["avg_turnover_20d_억"] or 0) < min_turnover_억:
                reasons.append(f"거래대금 {row['avg_turnover_20d_억']:.1f}억 < {min_turnover_억:.1f}억")
            if (row["market_cap_억"] or 0) > max_mktcap_억:
                reasons.append(f"시총 {row['market_cap_억']:.0f}억 > {max_mktcap_억:.0f}억")
            if not reasons:
                reasons.append("기타 조건 미세차")
            return reasons

        miss_reason_counter: Counter[str] = Counter()
        constraint_counter = {
            "score_cut": 0,
            "turnover_cut": 0,
            "mktcap_cut": 0,
            "score_only": 0,
            "turnover_only": 0,
            "mktcap_only": 0,
            "score_and_turnover": 0,
            "score_and_mktcap": 0,
            "turnover_and_mktcap": 0,
            "all_three": 0,
        }
        for row in missed:
            flags = {
                "score_cut": row["heuristic_score"] < min_score,
                "turnover_cut": (row["avg_turnover_20d_억"] or 0) < min_turnover_억,
                "mktcap_cut": (row["market_cap_억"] or 0) > max_mktcap_억,
            }
            if flags["score_cut"]:
                miss_reason_counter["점수컷"] += 1
            if flags["turnover_cut"]:
                miss_reason_counter["거래대금컷"] += 1
            if flags["mktcap_cut"]:
                miss_reason_counter["시총컷"] += 1
            if flags["score_cut"] and not flags["turnover_cut"] and not flags["mktcap_cut"]:
                constraint_counter["score_only"] += 1
            if flags["turnover_cut"] and not flags["score_cut"] and not flags["mktcap_cut"]:
                constraint_counter["turnover_only"] += 1
            if flags["mktcap_cut"] and not flags["score_cut"] and not flags["turnover_cut"]:
                constraint_counter["mktcap_only"] += 1
            if flags["score_cut"] and flags["turnover_cut"] and not flags["mktcap_cut"]:
                constraint_counter["score_and_turnover"] += 1
            if flags["score_cut"] and flags["mktcap_cut"] and not flags["turnover_cut"]:
                constraint_counter["score_and_mktcap"] += 1
            if flags["turnover_cut"] and flags["mktcap_cut"] and not flags["score_cut"]:
                constraint_counter["turnover_and_mktcap"] += 1
            if flags["score_cut"] and flags["turnover_cut"] and flags["mktcap_cut"]:
                constraint_counter["all_three"] += 1
        for key in ("score_cut", "turnover_cut", "mktcap_cut"):
            constraint_counter[key] = int(sum(
                1 for row in missed
                if (
                    (key == "score_cut" and row["heuristic_score"] < min_score) or
                    (key == "turnover_cut" and (row["avg_turnover_20d_억"] or 0) < min_turnover_억) or
                    (key == "mktcap_cut" and (row["market_cap_억"] or 0) > max_mktcap_억)
                )
            ))

        score_cut_rows = [r for r in missed if r["heuristic_score"] < min_score]
        score_only_rows = [
            r for r in missed
            if r["heuristic_score"] < min_score
            and (r["avg_turnover_20d_억"] or 0) >= min_turnover_억
            and (r["market_cap_억"] or 0) <= max_mktcap_억
        ]
        mktcap_cut_rows = [r for r in missed if (r["market_cap_억"] or 0) > max_mktcap_억]
        score_cut_archetypes: Counter[str] = Counter()
        for row in score_cut_rows:
            tags: list[str] = []
            if row["model_score_12m"] >= 0.8:
                tags.append("모델고득점")
            elif row["model_score_12m"] >= 0.6:
                tags.append("모델중상위")
            if (row.get("dist_high_252_pct") or 0) <= -70:
                tags.append("초낙폭")
            elif (row.get("dist_high_252_pct") or 0) <= -50:
                tags.append("중낙폭")
            if (row.get("dist_low_252_pct") or 999) <= 15:
                tags.append("저점근접")
            if (row.get("vol_ratio_20d") or 0) >= 2.0:
                tags.append("거래량급증")
            if 0 < (row.get("market_cap_억") or 0) <= 1500:
                tags.append("소형주")
            elif (row.get("market_cap_억") or 0) > 3000:
                tags.append("중대형주")
            if (row.get("pbr") or 999) <= 1.0:
                tags.append("저PBR")
            if (row.get("avg_turnover_20d_억") or 0) < min_turnover_억:
                tags.append("저유동성")
            score_cut_archetypes["+".join(tags[:3]) if tags else "기타"] += 1

        score_gap_bands = {
            "0~5점 부족": 0,
            "5~10점 부족": 0,
            "10~20점 부족": 0,
            "20점+ 부족": 0,
        }
        for row in score_cut_rows:
            gap = max(0.0, min_score - row["heuristic_score"])
            if gap < 5:
                score_gap_bands["0~5점 부족"] += 1
            elif gap < 10:
                score_gap_bands["5~10점 부족"] += 1
            elif gap < 20:
                score_gap_bands["10~20점 부족"] += 1
            else:
                score_gap_bands["20점+ 부족"] += 1

        def _cap_band(value: float) -> str:
            if value <= 1500:
                return "1500억 이하"
            if value <= 3000:
                return "1500~3000억"
            if value <= 5000:
                return "3000~5000억"
            if value <= 10000:
                return "5000억~1조"
            return "1조 초과"

        mktcap_cut_breakdown: Counter[str] = Counter()
        for row in mktcap_cut_rows:
            band = _cap_band(float(row.get("market_cap_억") or 0))
            tag = band
            if row.get("earnings_backed"):
                if row.get("model_score_12m", 0) >= 0.8:
                    tag += "+모델고득점"
                elif row.get("model_score_12m", 0) >= 0.6:
                    tag += "+모델중상위"
                if (row.get("ret_60d_pct") or 0) >= 20:
                    tag += "+상승추세"
                elif (row.get("dist_high_252_pct") or 0) <= -50:
                    tag += "+낙폭회복형"
            mktcap_cut_breakdown[tag] += 1

        earnings_pattern_counter: Counter[str] = Counter()
        for row in score_cut_rows:
            tags = row.get("earnings_tags") or []
            if not tags:
                earnings_pattern_counter["실적태그빈약"] += 1
                continue
            key = "+".join(tags[:3])
            earnings_pattern_counter[key] += 1

        def _avg_metric(rows: list[dict], key: str) -> float | None:
            vals = [float(r[key]) for r in rows if r.get(key) is not None]
            if not vals:
                return None
            return round(sum(vals) / len(vals), 2)

        score_cut_feature_gap = []
        for key, label, unit in [
            ("heuristic_score", "휴리스틱 점수", "pt"),
            ("model_score_12m", "모델 점수", ""),
            ("market_cap_억", "시가총액", "억"),
            ("avg_turnover_20d_억", "20일 평균 거래대금", "억"),
            ("ret_60d_pct", "60일 수익률", "%"),
            ("dist_high_252_pct", "52주 고점 대비 거리", "%"),
            ("dist_low_252_pct", "52주 저점 대비 거리", "%"),
            ("vol_ratio_20d", "20일 거래량 배수", "x"),
            ("rev_yoy_pct", "매출 YoY", "%"),
            ("op_yoy_pct", "영업이익 YoY", "%"),
            ("yoy_rev_streak", "매출 성장 연속분기", "q"),
            ("yoy_op_streak", "이익 성장 연속분기", "q"),
        ]:
            captured_avg = _avg_metric(captured, key)
            score_cut_avg = _avg_metric(score_cut_rows, key)
            if captured_avg is None and score_cut_avg is None:
                continue
            gap = None
            if captured_avg is not None and score_cut_avg is not None:
                gap = round(score_cut_avg - captured_avg, 2)
            score_cut_feature_gap.append({
                "metric": key,
                "label": label,
                "unit": unit,
                "captured_avg": captured_avg,
                "score_cut_avg": score_cut_avg,
                "gap": gap,
            })

        score_only_feature_gap = []
        for key, label, unit in [
            ("heuristic_score", "휴리스틱 점수", "pt"),
            ("model_score_12m", "모델 점수", ""),
            ("market_cap_억", "시가총액", "억"),
            ("avg_turnover_20d_억", "20일 평균 거래대금", "억"),
            ("ret_60d_pct", "60일 수익률", "%"),
            ("dist_high_252_pct", "52주 고점 대비 거리", "%"),
            ("dist_low_252_pct", "52주 저점 대비 거리", "%"),
            ("vol_ratio_20d", "20일 거래량 배수", "x"),
            ("rev_yoy_pct", "매출 YoY", "%"),
            ("op_yoy_pct", "영업이익 YoY", "%"),
            ("yoy_rev_streak", "매출 성장 연속분기", "q"),
            ("yoy_op_streak", "이익 성장 연속분기", "q"),
        ]:
            captured_avg = _avg_metric(captured, key)
            score_only_avg = _avg_metric(score_only_rows, key)
            if captured_avg is None and score_only_avg is None:
                continue
            gap = None
            if captured_avg is not None and score_only_avg is not None:
                gap = round(score_only_avg - captured_avg, 2)
            score_only_feature_gap.append({
                "metric": key,
                "label": label,
                "unit": unit,
                "captured_avg": captured_avg,
                "score_only_avg": score_only_avg,
                "gap": gap,
            })

        sensitivity_configs = [
            ("기준", min_score, min_turnover_억, max_mktcap_억),
            ("점수 50", 50.0, min_turnover_억, max_mktcap_억),
            ("점수 45", 45.0, min_turnover_억, max_mktcap_억),
            ("거래대금 2억", min_score, 2.0, max_mktcap_억),
            ("시총 5000억", min_score, min_turnover_억, 5000.0),
            ("점수50+시총5000", 50.0, min_turnover_억, 5000.0),
            ("점수50+거래2억", 50.0, 2.0, max_mktcap_억),
            ("점수50+거래2억+시총5000", 50.0, 2.0, 5000.0),
        ]
        capture_sensitivity = []
        base_captured = len(captured)
        for name, score_cut, turnover_cut, mktcap_cut in sensitivity_configs:
            scenario_rows = [
                r for r in winners
                if r["heuristic_score"] >= score_cut
                and (r["avg_turnover_20d_억"] or 0) >= turnover_cut
                and (r["market_cap_억"] or 0) <= mktcap_cut
            ]
            scenario_earnings_backed = sum(1 for r in scenario_rows if r.get("earnings_backed"))
            capture_sensitivity.append({
                "name": name,
                "min_score": score_cut,
                "min_turnover_억": turnover_cut,
                "max_mktcap_억": mktcap_cut,
                "captured": len(scenario_rows),
                "capture_rate_pct": round(len(scenario_rows) / len(winners) * 100.0, 1) if winners else 0.0,
                "incremental_vs_base": len(scenario_rows) - base_captured,
                "earnings_backed_share": round(scenario_earnings_backed / len(scenario_rows) * 100.0, 1) if scenario_rows else 0.0,
            })

        missed_sorted = sorted(
            missed,
            key=lambda r: (
                r.get("forward_peak_pct") or 0,
                r.get("heuristic_score") or 0,
            ),
            reverse=True,
        )
        missed_examples = [{
            **r,
            "miss_reasons": _miss_reasons(r),
        } for r in missed_sorted[:sample_limit]]

        return {
            "meta": {
                "horizon": horizon,
                "multiple": multiple,
                "analysis_focus": analysis_focus,
                "label_col": label_col,
                "peak_col": peak_col,
                "start_date": start_date,
                "end_date": end_date,
                "min_score": min_score,
                "min_turnover_억": min_turnover_억,
                "max_mktcap_억": max_mktcap_억,
            },
            "summary": {
                "winners_total": len(winners),
                "captured": len(captured),
                "missed": len(missed),
                "capture_rate_pct": round(len(captured) / len(winners) * 100.0, 1) if winners else 0.0,
            },
            "captured_profile": _summary(captured),
            "missed_profile": _summary(missed),
            "score_cut_profile": _summary(score_cut_rows),
            "score_only_profile": _summary(score_only_rows),
            "capture_sensitivity": capture_sensitivity,
            "score_cut_feature_gap": score_cut_feature_gap,
            "score_only_feature_gap": score_only_feature_gap,
            "miss_reason_rank": [
                {
                    "name": name,
                    "count": count,
                    "pct_of_missed": round(count / len(missed) * 100.0, 1) if missed else 0.0,
                }
                for name, count in miss_reason_counter.most_common()
            ],
            "score_cut_archetypes": [
                {
                    "name": name,
                    "count": count,
                    "pct_of_score_cut": round(count / len(score_cut_rows) * 100.0, 1) if score_cut_rows else 0.0,
                }
                for name, count in score_cut_archetypes.most_common(12)
            ],
            "score_cut_earnings_patterns": [
                {
                    "name": name,
                    "count": count,
                    "pct_of_score_cut": round(count / len(score_cut_rows) * 100.0, 1) if score_cut_rows else 0.0,
                }
                for name, count in earnings_pattern_counter.most_common(12)
            ],
            "score_gap_bands": {
                key: {
                    "count": value,
                    "pct_of_score_cut": round(value / len(score_cut_rows) * 100.0, 1) if score_cut_rows else 0.0,
                }
                for key, value in score_gap_bands.items()
            },
            "mktcap_cut_breakdown": [
                {
                    "name": name,
                    "count": count,
                    "pct_of_mktcap_cut": round(count / len(mktcap_cut_rows) * 100.0, 1) if mktcap_cut_rows else 0.0,
                }
                for name, count in mktcap_cut_breakdown.most_common(12)
            ],
            "constraint_overlap": {
                key: {
                    "count": value,
                    "pct_of_missed": round(value / len(missed) * 100.0, 1) if missed else 0.0,
                }
                for key, value in constraint_counter.items()
            },
            "top_missed_examples": missed_examples,
        }


@router.get("/triple-trigger-analysis")
def triple_trigger_analysis(force: bool = Query(False)):
    """2020년 이후 3배주/비3배주 트리거 빈도와 실패 신호 통계."""
    if force or not TRIGGER_ANALYSIS_PATH.exists():
        if not TRIGGER_ANALYSIS_SCRIPT.exists():
            raise HTTPException(status_code=404, detail="trigger analysis script not found")
        proc = subprocess.run(
            [sys.executable, str(TRIGGER_ANALYSIS_SCRIPT)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            logger.error("[tenbagger] trigger analysis failed: %s", proc.stderr[-4000:])
            raise HTTPException(status_code=500, detail="trigger analysis generation failed")
    try:
        return json.loads(TRIGGER_ANALYSIS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"trigger analysis read failed: {e}")


def _num(v: Any, ndigits: int | None = None):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, ndigits) if ndigits is not None else f


def _mode(v: str) -> str:
    v = (v or "score").lower()
    return v if v in {"and", "or", "score"} else "score"


def _pass_metric(mode: str, passed: bool, state: dict, metric: str):
    if mode == "and" and not passed:
        state["failed_required"].append(metric)
    elif mode == "or":
        state["has_or"] = True
        if passed:
            state["or_passed"] = True
    elif mode == "score" and passed:
        state["score"] += 1
    if passed:
        state["matched"].append(metric)


def _final_selected(state: dict, min_score: int) -> bool:
    if state["failed_required"]:
        return False
    if state["has_or"] and not state["or_passed"]:
        return False
    return state["score"] >= min_score


def _quarterly_rows(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    rows = conn.execute("""
        SELECT stock_code, year, quarter, revenue, operating_profit,
               depreciation_amortization
        FROM financial_data
        WHERE is_annual = 0
          AND quarter > 0
          AND revenue IS NOT NULL
        ORDER BY stock_code, year DESC, quarter DESC
    """).fetchall()
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        by_code.setdefault(row["stock_code"], []).append(dict(row))
    return by_code


def _consecutive_qoq(rows: list[dict], field: str = "revenue") -> int:
    streak = 0
    for i in range(min(4, len(rows) - 1)):
        cur = _num(rows[i].get(field))
        prev = _num(rows[i + 1].get(field))
        if cur is None or prev is None or prev <= 0 or cur <= prev:
            break
        streak += 1
    return streak


def _consecutive_yoy(rows: list[dict], field: str = "revenue") -> int:
    lookup = {(r["year"], r["quarter"]): r for r in rows}
    streak = 0
    for row in rows[:4]:
        cur = _num(row.get(field))
        prev_row = lookup.get((row["year"] - 1, row["quarter"]))
        prev = _num(prev_row.get(field)) if prev_row else None
        if cur is None or prev is None or prev <= 0 or cur <= prev:
            break
        streak += 1
    return streak


def _pct_change(cur: float | None, prev: float | None):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100


def _parse_ai_json(text: str):
    if not text:
        return None
    # markdown 코드블록 제거 (```json ... ``` 형식)
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _send_sector_ai_telegram(run_type: str = "morning") -> bool:
    """AI 주도 섹터 리포트를 텔레그램으로 전송."""
    try:
        from notifier import send as _notify
        data = _safe_cache_read()
        if not data:
            return False

        label = {"morning": "🌅 오전 8:30", "lunch": "☀️ 점심 12:30"}.get(run_type, "📊")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        sectors = data.get("sectors", [])[:3]
        summary = data.get("ai_summary", "")
        market_view = data.get("market_view", "")

        lines = [
            f"📡 AI 주도 섹터 리포트 [{label}] — {now_str}",
            "─" * 32,
        ]
        if summary:
            lines.append(f"📌 핵심 시황: {summary}")
        if market_view:
            lines.append(f"🔭 관찰 포인트: {market_view}")
        if summary or market_view:
            lines.append("─" * 32)

        rank_icons = ["🥇", "🥈", "🥉"]
        for i, s in enumerate(sectors):
            icon = rank_icons[i] if i < 3 else "🔹"
            ret1 = s.get("ret_1d", 0) or 0
            ret5 = s.get("ret_5d", 0) or 0
            ret20 = s.get("ret_20d", 0) or 0
            lines.append(
                f"{icon} {s.get('kr', s.get('ticker', ''))} ({s.get('ticker', '')})"
            )
            lines.append(
                f"   📈 1일 {ret1:+.1f}% / 5일 {ret5:+.1f}% / 20일 {ret20:+.1f}%"
            )
            lines.append(
                "   ✅ 진입 시그널: 5일·20일 동반 우상향 + 단기 조정 후 거래대금 회복 여부 체크"
            )
            if s.get("ai_reason"):
                lines.append(f"   💡 상승 이유: {s['ai_reason']}")
            if s.get("ai_risk"):
                lines.append(f"   ⚠️ 리스크: {s['ai_risk']}")
            lines.append("   ⛔ 이탈 기준: 5일 수익률 급반전 음수 전환 또는 섹터 주도력 약화")

        # 주목 종목 상위 5개
        stocks = data.get("stocks", [])[:5]
        if stocks:
            lines.append("─" * 32)
            lines.append("🔍 주목 종목 (상위 5)")
            for st in stocks:
                short_warn = ""
                borrow = st.get("borrow_bal_pct", 0) or 0
                ret20 = st.get("ret_20d", 0) or 0
                ret_label = "추세초기" if ret20 < 10 else "추세진행" if ret20 < 30 else "가속구간"
                if borrow > 1.5:
                    short_warn += f" ⚠️대차{borrow:.1f}%"
                if ret20 > 40:
                    short_warn += f" ⚠️20일+{ret20:.0f}%"
                lines.append(
                    f"   • {st['stock_name']} ({st['stock_code']}) "
                    f"점수:{st.get('score', 0):.0f} "
                    f"20일:{ret20:+.0f}% ({ret_label}){short_warn}"
                )
                lines.append(
                    f"     └ 인사이트: {st.get('source_sector','')}"
                    f" 주도 수혜. 점수 상위/수급 과열 여부({('주의' if short_warn else '양호')}) 기준으로 분할 접근."
                )

        msg = "\n".join(lines)
        key = f"sector_ai_{run_type}_{now_str[:10]}"
        return _notify(msg, key=key)
    except Exception as e:
        logger.warning(f"[섹터AI텔레그램] 전송 오류: {e}")
        return False


def _fetch_rss_titles(url: str, limit: int = 8, timeout: float = 2.5) -> list[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)
        root = ET.fromstring(raw)
        titles = []
        for item in root.findall(".//item"):
            title = item.findtext("title") or ""
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                titles.append(title)
            if len(titles) >= limit:
                break
        return titles
    except Exception:
        return []


US_SECTOR_ETFS = [
    {"ticker": "XLK", "name": "Technology", "kr": "기술", "keywords": ["반도체", "소프트웨어", "IT", "AI"]},
    {"ticker": "SOXX", "name": "Semiconductors", "kr": "반도체", "keywords": ["반도체", "장비", "소부장"]},
    {"ticker": "SMH", "name": "Semiconductor Leaders", "kr": "반도체 대형주", "keywords": ["반도체", "장비", "소부장"]},
    {"ticker": "XLC", "name": "Communication Services", "kr": "커뮤니케이션", "keywords": ["인터넷", "미디어", "플랫폼"]},
    {"ticker": "XLY", "name": "Consumer Discretionary", "kr": "임의소비재", "keywords": ["자동차", "소비재", "유통"]},
    {"ticker": "XLI", "name": "Industrials", "kr": "산업재", "keywords": ["기계", "전력", "방산", "조선"]},
    {"ticker": "XLF", "name": "Financials", "kr": "금융", "keywords": ["금융", "증권", "보험"]},
    {"ticker": "XLE", "name": "Energy", "kr": "에너지", "keywords": ["에너지", "정유", "가스"]},
    {"ticker": "XLV", "name": "Health Care", "kr": "헬스케어", "keywords": ["바이오", "제약", "의료"]},
    {"ticker": "XBI", "name": "Biotech", "kr": "바이오", "keywords": ["바이오", "제약"]},
    {"ticker": "XLB", "name": "Materials", "kr": "소재", "keywords": ["화학", "소재", "철강"]},
    {"ticker": "XLU", "name": "Utilities", "kr": "유틸리티", "keywords": ["전력", "가스", "인프라"]},
    {"ticker": "XLP", "name": "Consumer Staples", "kr": "필수소비재", "keywords": ["음식료", "생활소비재"]},
    {"ticker": "XLRE", "name": "Real Estate", "kr": "부동산", "keywords": ["건설", "리츠"]},
    {"ticker": "ITA", "name": "Aerospace & Defense", "kr": "방산", "keywords": ["방산", "항공", "우주"]},
    {"ticker": "PAVE", "name": "Infrastructure", "kr": "인프라", "keywords": ["전력", "건설", "인프라"]},
    {"ticker": "URA", "name": "Uranium", "kr": "원전", "keywords": ["원전", "전력"]},
    {"ticker": "TAN", "name": "Solar", "kr": "태양광", "keywords": ["태양광", "신재생"]},
    {"ticker": "COPX", "name": "Copper Miners", "kr": "구리", "keywords": ["구리", "비철", "소재"]},
]

US_MARKET_SYMBOLS = ["^IXIC", "^GSPC", "^DJI", "^RUT", "QQQ", "SPY"]

MARKET_NEWS_FEEDS = [
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("CNBC Markets", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Google News", "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": "NASDAQ S&P 500 leading sectors stocks today",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })),
]


def _safe_cache_read() -> dict | None:
    try:
        if not SECTOR_AI_CACHE_PATH.exists():
            return None
        return json.loads(SECTOR_AI_CACHE_PATH.read_text())
    except Exception:
        return None


def _safe_cache_write(data: dict) -> None:
    try:
        SECTOR_AI_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_AI_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"[sector-ai-leaders] cache write failed: {e}")


def _download_us_market_rows() -> list[dict]:
    symbols = US_MARKET_SYMBOLS + [x["ticker"] for x in US_SECTOR_ETFS]
    try:
        import yfinance as yf
        df = yf.download(
            tickers=" ".join(symbols),
            period="3mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        logger.warning(f"[sector-ai-leaders] yfinance sector download failed: {e}")
        return []
    try:
        closes = df["Close"] if hasattr(df, "__getitem__") and "Close" in df else df
    except Exception:
        return []

    rows = []
    meta = {x["ticker"]: x for x in US_SECTOR_ETFS}
    for symbol in symbols:
        try:
            series = closes[symbol].dropna() if symbol in closes else closes.dropna()
        except Exception:
            continue
        if len(series) < 6:
            continue
        latest = float(series.iloc[-1])
        prev1 = float(series.iloc[-2])
        prev5 = float(series.iloc[-6])
        prev20 = float(series.iloc[-21]) if len(series) >= 21 else float(series.iloc[0])
        rows.append({
            "ticker": symbol,
            "name": meta.get(symbol, {}).get("name", symbol),
            "kr": meta.get(symbol, {}).get("kr", symbol),
            "keywords": meta.get(symbol, {}).get("keywords", []),
            "type": "sector" if symbol in meta else "index",
            "close": round(latest, 2),
            "ret_1d": _num(_pct_change(latest, prev1), 2),
            "ret_5d": _num(_pct_change(latest, prev5), 2),
            "ret_20d": _num(_pct_change(latest, prev20), 2),
        })
    return rows


def _score_us_sectors(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    indices = [r for r in rows if r["type"] == "index"]
    sectors = [r for r in rows if r["type"] == "sector"]
    spy_5d = next((r.get("ret_5d") for r in indices if r["ticker"] == "SPY"), 0) or 0
    qqq_5d = next((r.get("ret_5d") for r in indices if r["ticker"] == "QQQ"), 0) or 0
    bench = max(spy_5d, qqq_5d)
    for s in sectors:
        r1 = s.get("ret_1d") or 0
        r5 = s.get("ret_5d") or 0
        r20 = s.get("ret_20d") or 0
        s["lead_score"] = round((r1 * 0.25) + (r5 * 0.45) + (r20 * 0.20) + ((r5 - bench) * 0.35), 2)
        s["relative_5d"] = round(r5 - bench, 2)
    sectors.sort(key=lambda x: x["lead_score"], reverse=True)
    return sectors, indices


def _fetch_market_news(limit_per_feed: int = 5) -> list[dict]:
    items = []
    for source, url in MARKET_NEWS_FEEDS:
        for title in _fetch_rss_titles(url, limit=limit_per_feed, timeout=2.2):
            items.append({"source": source, "title": title})
    return items


def _sector_where_clause(keywords: list[str]) -> tuple[str, list[str]]:
    patterns = [f"%{k}%" for k in keywords if k]
    if not patterns:
        return "1=1", []
    parts = []
    params = []
    for pat in patterns:
        parts.append("(COALESCE(u.sector_mid,'') LIKE ? OR COALESCE(u.sector_large,'') LIKE ? OR COALESCE(u.stock_name,'') LIKE ?)")
        params.extend([pat, pat, pat])
    return "(" + " OR ".join(parts) + ")", params


def _local_leaders_for_keywords(conn: sqlite3.Connection, keywords: list[str], limit: int = 8) -> list[dict]:
    where, params = _sector_where_clause(keywords)
    rows = conn.execute(f"""
        WITH latest AS (
            SELECT stock_code, MAX(date) AS max_date
            FROM price_history
            WHERE close > 0
            GROUP BY stock_code
        ),
        p0 AS (
            SELECT p.stock_code, p.close AS latest_close
            FROM price_history p
            JOIN latest l ON l.stock_code=p.stock_code AND l.max_date=p.date
        ),
        p20 AS (
            SELECT stock_code, close AS old_close
            FROM (
                SELECT stock_code, close, date,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                FROM price_history
                WHERE close > 0
            )
            WHERE rn=20
        )
        SELECT u.stock_code, u.stock_name, u.market, u.sector_large, u.sector_mid,
               u.market_cap, u.per, u.pbr, u.roe, p0.latest_close,
               CASE WHEN p20.old_close > 0 THEN (p0.latest_close - p20.old_close) / p20.old_close * 100 END AS ret_20d
        FROM stock_universe u
        JOIN p0 ON p0.stock_code = u.stock_code
        LEFT JOIN p20 ON p20.stock_code = u.stock_code
        WHERE length(u.stock_code)=6
          AND (u.stock_type IS NULL OR u.stock_type != 'ETF')
          AND {where}
          AND u.market_cap IS NOT NULL
        ORDER BY u.market_cap DESC
        LIMIT 150
    """, params).fetchall()
    # 대차잔고 조회 (최근 1건)
    borrow_map: dict = {}
    try:
        codes = [dict(r)["stock_code"] for r in rows]
        if codes:
            placeholders = ",".join("?" * len(codes))
            b_rows = conn.execute(
                f"SELECT stock_code, borrow_bal_pct FROM short_sell_daily "
                f"WHERE stock_code IN ({placeholders}) "
                f"ORDER BY bas_dt DESC",
                codes,
            ).fetchall()
            seen_b: set = set()
            for br in b_rows:
                code = br[0]
                if code not in seen_b:
                    borrow_map[code] = float(br[1] or 0)
                    seen_b.add(code)
    except Exception:
        pass

    leaders = []
    for r in rows:
        d = dict(r)
        ret20 = _num(d.get("ret_20d")) or 0
        market_cap = _num(d.get("market_cap")) or 0
        roe = _num(d.get("roe")) or 0
        d["ret_20d"] = _num(ret20, 2)
        d["market_cap_억"] = _num(market_cap / 100_000_000, 0)
        borrow = borrow_map.get(d["stock_code"], 0)
        d["borrow_bal_pct"] = round(borrow, 2)

        base_score = round(
            min(35, max(0, ret20))
            + min(35, math.log10(max(market_cap / 100_000_000, 1)) * 6)
            + min(20, max(0, roe)),
            1,
        )
        # 페널티: 대차잔고 2% 초과 시 -5점, 5% 초과 시 -15점
        if borrow > 5:
            base_score -= 15
        elif borrow > 2:
            base_score -= 5
        # 페널티: 20일 수익률 50% 초과 시 단기 과열 -10점
        if ret20 > 50:
            base_score -= 10

        d["score"] = max(0, base_score)
        d["warn_borrow"] = borrow > 2        # 대차잔고 경고
        d["warn_overheated"] = ret20 > 40    # 단기 급등 경고
        leaders.append(d)
    leaders.sort(key=lambda x: x["score"], reverse=True)
    return leaders[:limit]


def _ensure_table():
    try:
        from tenbagger_engine import init_tenbagger_tables
        init_tenbagger_tables()
    except Exception as e:
        logger.warning(f"[tenbagger] 테이블 초기화 오류: {e}")


# ──────────────────────────────────────────────
# 최신 결과 조회
# ──────────────────────────────────────────────

@router.get("/results")
def get_latest_results(limit: int = 20):
    """가장 최근 발굴 회차의 결과 목록."""
    _ensure_table()
    conn = _get_conn()
    try:
        # 최신 run_time 조회
        row = conn.execute(
            "SELECT run_time FROM tenbagger_results ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"run_time": None, "results": [], "count": 0}

        latest_run = row["run_time"]
        fetch_limit = max(limit * 3, limit, 60)
        rows = conn.execute("""
            SELECT id, run_time, run_type, stock_code, stock_name,
                   total_score, score_detail, reasons, ai_analysis,
                   current_price, market_cap, per, pbr, roe,
                   revenue_growth, op_growth, op_margin,
                   inst_net_10d, frn_net_10d, telegram_sent, created_at
            FROM   tenbagger_results
            WHERE  run_time = ?
            ORDER  BY total_score DESC
            LIMIT  ?
        """, (latest_run, fetch_limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["reasons"]      = json.loads(d["reasons"] or "[]")
            d["score_detail"] = json.loads(d["score_detail"] or "{}")
            results.append(d)
        results = _enrich_tenbagger_price_risk(conn, results)
        results.sort(key=lambda x: (x.get("risk_adjusted_score", x.get("total_score") or 0), x.get("total_score") or 0), reverse=True)
        results = results[:limit]

        return {"run_time": latest_run, "results": results, "count": len(results)}
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 발굴 이력 (회차 목록)
# ──────────────────────────────────────────────

@router.get("/history")
def get_history(limit: int = 30):
    """발굴 회차별 요약 목록 (최신 N회)."""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT run_time, run_type,
                   COUNT(*)         AS count,
                   MAX(total_score) AS max_score,
                   AVG(total_score) AS avg_score
            FROM   tenbagger_results
            GROUP  BY run_time, run_type
            ORDER  BY run_time DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 특정 회차 상세 조회
# ──────────────────────────────────────────────

@router.get("/run-history")
def get_run_detail(run_time: str, limit: int = 20):
    """특정 run_time의 발굴 결과 전체."""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, run_time, run_type, stock_code, stock_name,
                   total_score, score_detail, reasons, ai_analysis,
                   current_price, market_cap, per, pbr, roe,
                   revenue_growth, op_growth, op_margin,
                   inst_net_10d, frn_net_10d, telegram_sent, created_at
            FROM   tenbagger_results
            WHERE  run_time = ?
            ORDER  BY total_score DESC
            LIMIT  ?
        """, (run_time, limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["reasons"]      = json.loads(d["reasons"] or "[]")
            d["score_detail"] = json.loads(d["score_detail"] or "{}")
            results.append(d)
        results = _enrich_tenbagger_price_risk(conn, results)
        results.sort(key=lambda x: (x.get("risk_adjusted_score", x.get("total_score") or 0), x.get("total_score") or 0), reverse=True)

        return {"run_time": run_time, "results": results, "count": len(results)}
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 수동 실행 트리거
# ──────────────────────────────────────────────

@router.post("/run")
def trigger_run():
    """수동으로 텐버거 발굴 즉시 실행 (백그라운드 스레드)."""
    global _run_status

    if _run_status["running"]:
        return {"status": "already_running", "message": "현재 발굴이 진행 중입니다."}

    def _bg():
        global _run_status
        _run_status["running"] = True
        _run_status["error"]   = None
        try:
            from tenbagger_engine import run_discovery
            results = run_discovery("manual")
            _run_status["last_run"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _run_status["last_count"] = len(results)
        except Exception as e:
            logger.error(f"[텐버거 수동실행] {e}", exc_info=True)
            _run_status["error"] = str(e)
        finally:
            _run_status["running"] = False

    t = threading.Thread(target=_bg, daemon=True, name="tenbagger-manual")
    t.start()

    return {"status": "started", "message": "발굴을 시작했습니다. 잠시 후 결과를 확인하세요."}


# ──────────────────────────────────────────────
# 실행 상태
# ──────────────────────────────────────────────

@router.get("/status")
def get_status():
    return {
        "running":    _run_status["running"],
        "last_run":   _run_status["last_run"],
        "last_count": _run_status["last_count"],
        "error":      _run_status["error"],
    }


@router.get("/custom-filter")
def get_custom_filter(
    opm_threshold: float = Query(3.0, description="영업이익률 상한(%)"),
    depr_threshold: float = Query(40.0, description="감가상각비율 하한(%)"),
    emp_threshold: float = Query(0.0, description="고용 증가율 하한(%)"),
    export_threshold: float = Query(0.0, description="수출 증가율 하한(%)"),
    foreign_threshold: float = Query(0.0, description="외국인 수급/시총 하한(%)"),
    revenue_qoq_streak_min: int = Query(0, ge=0, le=4, description="매출 QoQ 연속상승 최소 분기수(0=미사용)"),
    op_qoq_streak_min: int = Query(0, ge=0, le=4, description="영업이익 QoQ 연속상승 최소 분기수(0=미사용)"),
    emp_months: int = Query(3, ge=3, le=6),
    opm_mode: str = "score",
    depr_mode: str = "score",
    emp_mode: str = "score",
    export_mode: str = "score",
    foreign_mode: str = "score",
    revenue_qoq_mode: str = "score",
    op_qoq_mode: str = "score",
    min_score: int = Query(2, ge=0, le=5),
    limit: int = Query(100, ge=1, le=500),
):
    """DB 읽기 전용 커스텀 텐버거 필터.

    각 지표는 AND(필수), OR(선택 묶음), score(+점수) 중 하나로 평가한다.
    """
    emp_months = 6 if int(emp_months) == 6 else 3
    modes = {
        "opm": _mode(opm_mode),
        "depr": _mode(depr_mode),
        "emp": _mode(emp_mode),
        "export": _mode(export_mode),
        "foreign": _mode(foreign_mode),
        "revenue_qoq": _mode(revenue_qoq_mode),
        "op_qoq": _mode(op_qoq_mode),
    }

    stock_conn = _ro_conn(DB_PATH)
    emp_conn = _ro_conn(EMP_DB_PATH)
    trade_conn = _ro_conn(TRADE_DB_PATH)
    try:
        universe = {
            r["stock_code"]: dict(r)
            for r in stock_conn.execute("""
                SELECT stock_code, stock_name, market, sector_large, sector_mid,
                       close, market_cap, per, pbr, roe
                FROM stock_universe
                WHERE length(stock_code)=6
                  AND (stock_type IS NULL OR stock_type != 'ETF')
                  AND market_cap IS NOT NULL
                  AND market_cap > 0
            """).fetchall()
        }
        if not universe:
            return {"stocks": [], "count": 0, "meta": {"reason": "no_universe"}}

        fin = {
            r["stock_code"]: dict(r)
            for r in stock_conn.execute("""
                SELECT f.stock_code, f.year, f.quarter, f.revenue,
                       f.operating_profit, f.depreciation_amortization
                FROM financial_data f
                JOIN (
                    SELECT stock_code, MAX(year * 10 + quarter) AS yq
                    FROM financial_data
                    WHERE is_annual = 0 AND quarter > 0
                    GROUP BY stock_code
                ) x ON x.stock_code = f.stock_code AND x.yq = f.year * 10 + f.quarter
            """).fetchall()
        }
        cash = {
            r["stock_code"]: dict(r)
            for r in stock_conn.execute("""
                SELECT c.stock_code, c.year, c.quarter, c.depreciation
                FROM cash_flow_data c
                JOIN (
                    SELECT stock_code, MAX(year * 10 + quarter) AS yq
                    FROM cash_flow_data
                    WHERE is_annual = 0 AND quarter > 0
                    GROUP BY stock_code
                ) x ON x.stock_code = c.stock_code AND x.yq = c.year * 10 + c.quarter
            """).fetchall()
        }
        qrows = _quarterly_rows(stock_conn)
        foreign = {
            r["stock_code"]: _num(r["frn_amt"], 4)
            for r in stock_conn.execute("""
                SELECT stock_code, SUM(frn_net_buy_amt) AS frn_amt
                FROM price_history
                WHERE date >= DATE('now', '-40 days')
                  AND close > 0
                GROUP BY stock_code
            """).fetchall()
        }

        latest_workers = {
            r["stock_code"]: r["worker_count"]
            for r in emp_conn.execute("""
                SELECT e.stock_code, e.worker_count
                FROM employment_company e
                JOIN (
                    SELECT stock_code, MAX(ym) AS ym
                    FROM employment_company
                    GROUP BY stock_code
                ) x ON x.stock_code = e.stock_code AND x.ym = e.ym
            """).fetchall()
        }
        emp_rows = emp_conn.execute("""
            SELECT stock_code, data_ym, net_change
            FROM nps_monthly
            ORDER BY stock_code, data_ym DESC
        """).fetchall()
        counters: dict[str, int] = {}
        emp_changes = {}
        for row in emp_rows:
            code = row["stock_code"]
            cnt = counters.get(code, 0)
            if cnt >= emp_months:
                continue
            emp_changes[code] = emp_changes.get(code, 0.0) + (row["net_change"] or 0)
            counters[code] = cnt + 1

        export_months: dict[str, list[tuple[str, float]]] = {}
        for row in trade_conn.execute("""
            SELECT stock_code, period_ym, SUM(export_value) AS export_value
            FROM analysis2_company_monthly_cache
            WHERE export_value > 0
            GROUP BY stock_code, period_ym
            ORDER BY stock_code, period_ym DESC
        """).fetchall():
            export_months.setdefault(row["stock_code"], []).append(
                (row["period_ym"], float(row["export_value"] or 0))
            )

        export_growth: dict[str, float] = {}
        for code, months in export_months.items():
            if len(months) < 2:
                continue
            latest_ym, latest_val = months[0]
            prev_same_month = next((v for ym, v in months[1:] if ym[5:7] == latest_ym[5:7]), None)
            base_val = prev_same_month if prev_same_month is not None else months[1][1]
            if base_val and base_val > 0:
                export_growth[code] = (latest_val - base_val) / base_val * 100

        results = []
        for code, stock in universe.items():
            f = fin.get(code) or {}
            revenue = _num(f.get("revenue")) or 0
            op = _num(f.get("operating_profit")) or 0
            depr = _num(f.get("depreciation_amortization")) or 0
            if not depr and code in cash:
                depr = _num(cash[code].get("depreciation")) or 0

            opm = (op / revenue * 100) if revenue > 0 else None
            depr_ratio = (depr / revenue * 100) if revenue > 0 and depr else None

            worker_base = latest_workers.get(code) or 0
            emp_inc = emp_changes.get(code)
            emp_pct = (emp_inc / worker_base * 100) if worker_base and emp_inc is not None else None

            market_cap_억 = _num(stock.get("market_cap")) or 0  # 억원 단위
            frn_amt_million = foreign.get(code)  # 백만원 단위
            foreign_pct = None
            if market_cap_억 > 0 and frn_amt_million is not None:
                # 백만원 → 억원: /100,  (순매수억원 / 시총억원) * 100 = %
                foreign_pct = (frn_amt_million / 100) / market_cap_억 * 100

            values = {
                "opm": opm,
                "depr": depr_ratio,
                "emp": emp_pct,
                "export": export_growth.get(code),
                "foreign": foreign_pct,
            }
            rev_qoq_streak = _consecutive_qoq(qrows.get(code) or [], "revenue")
            op_qoq_streak = _consecutive_qoq(qrows.get(code) or [], "operating_profit")
            passed = {
                "opm": values["opm"] is not None and values["opm"] >= opm_threshold,
                "depr": values["depr"] is not None and values["depr"] >= depr_threshold,
                "emp": values["emp"] is not None and values["emp"] >= emp_threshold,
                "export": values["export"] is not None and values["export"] >= export_threshold,
                "foreign": values["foreign"] is not None and values["foreign"] >= foreign_threshold,
                "revenue_qoq": (revenue_qoq_streak_min == 0) or (rev_qoq_streak >= revenue_qoq_streak_min),
                "op_qoq": (op_qoq_streak_min == 0) or (op_qoq_streak >= op_qoq_streak_min),
            }
            state = {"score": 0, "matched": [], "failed_required": [], "has_or": False, "or_passed": False}
            metric_labels = {
                "opm": "영업이익률",
                "depr": "감가상각비율",
                "emp": "고용증가",
                "export": "수출증가",
                "foreign": "외국인수급",
                "revenue_qoq": "매출QoQ연속",
                "op_qoq": "영업이익QoQ연속",
            }
            for key in ("opm", "depr", "emp", "export", "foreign", "revenue_qoq", "op_qoq"):
                _pass_metric(modes[key], passed[key], state, metric_labels[key])

            selected = not state["failed_required"]
            if selected and state["has_or"] and not state["or_passed"]:
                selected = False
            if selected and state["score"] < min_score:
                selected = False
            if not selected:
                continue

            results.append({
                "stock_code": code,
                "stock_name": stock["stock_name"],
                "market": stock["market"],
                "sector": stock["sector_mid"] or stock["sector_large"],
                "current_price": _num(stock["close"], 2),
                "market_cap_억": _num(market_cap_억, 0),
                "per": _num(stock["per"], 2),
                "pbr": _num(stock["pbr"], 2),
                "roe": _num(stock["roe"], 2),
                "score": state["score"],
                "matched_indicators": state["matched"],
                "opm": _num(opm, 2),
                "depr_ratio": _num(depr_ratio, 2),
                "emp_change_pct": _num(emp_pct, 2),
                "emp_change_count": int(emp_inc) if emp_inc is not None else None,
                "export_growth_pct": _num(values["export"], 2),
                "foreign_supply_pct": _num(foreign_pct, 4),
                "revenue_qoq_streak": rev_qoq_streak,
                "op_qoq_streak": op_qoq_streak,
                "passes": passed,
            })

        results.sort(
            key=lambda r: (
                r["score"],
                r["export_growth_pct"] if r["export_growth_pct"] is not None else -999999,
                r["foreign_supply_pct"] if r["foreign_supply_pct"] is not None else -999999,
            ),
            reverse=True,
        )
        return {
            "stocks": results[:limit],
            "count": len(results),
            "meta": {
                "modes": modes,
                "thresholds": {
                    "opm": opm_threshold,
                    "depr": depr_threshold,
                    "emp": emp_threshold,
                    "export": export_threshold,
                    "foreign": foreign_threshold,
                    "revenue_qoq_streak_min": revenue_qoq_streak_min,
                    "op_qoq_streak_min": op_qoq_streak_min,
                    "emp_months": emp_months,
                    "min_score": min_score,
                },
            },
        }
    finally:
        stock_conn.close()
        emp_conn.close()
        trade_conn.close()


@router.get("/undervalued-filter")
def get_undervalued_filter(
    pbr_threshold: float = Query(1.0, description="PBR 상한"),
    per_threshold: float = Query(12.0, description="PER 상한"),
    qoq_streak: int = Query(2, ge=1, le=4, description="매출 QoQ 연속 상승 분기 수"),
    yoy_streak: int = Query(2, ge=1, le=4, description="매출 YoY 연속 상승 분기 수"),
    pbr_mode: str = "and",
    per_mode: str = "and",
    qoq_mode: str = "score",
    yoy_mode: str = "score",
    min_score: int = Query(1, ge=0, le=4),
    limit: int = Query(100, ge=1, le=500),
):
    """저평가 + 매출 연속 성장 종목 발굴."""
    modes = {
        "pbr": _mode(pbr_mode),
        "per": _mode(per_mode),
        "qoq": _mode(qoq_mode),
        "yoy": _mode(yoy_mode),
    }
    conn = _ro_conn(DB_PATH)
    try:
        fins = _quarterly_rows(conn)
        stocks = conn.execute("""
            SELECT stock_code, stock_name, market, sector_large, sector_mid,
                   close, market_cap, per, pbr, roe
            FROM stock_universe
            WHERE length(stock_code)=6
              AND (stock_type IS NULL OR stock_type != 'ETF')
              AND market_cap IS NOT NULL
              AND market_cap > 0
        """).fetchall()

        results = []
        for s in stocks:
            code = s["stock_code"]
            rows = fins.get(code) or []
            if len(rows) < 2:
                continue
            pbr = _num(s["pbr"])
            per = _num(s["per"])
            qoq = _consecutive_qoq(rows, "revenue")
            yoy = _consecutive_yoy(rows, "revenue")
            latest_rev = _num(rows[0].get("revenue"))
            prev_rev = _num(rows[1].get("revenue")) if len(rows) > 1 else None
            yoy_prev = next((r for r in rows[1:] if r["quarter"] == rows[0]["quarter"] and r["year"] == rows[0]["year"] - 1), None)
            yoy_prev_rev = _num(yoy_prev.get("revenue")) if yoy_prev else None

            passed = {
                "pbr": pbr is not None and pbr <= pbr_threshold,
                "per": per is not None and 0 < per <= per_threshold,
                "qoq": qoq >= qoq_streak,
                "yoy": yoy >= yoy_streak,
            }
            state = {"score": 0, "matched": [], "failed_required": [], "has_or": False, "or_passed": False}
            labels = {"pbr": "PBR", "per": "PER", "qoq": "매출QoQ", "yoy": "매출YoY"}
            for key in ("pbr", "per", "qoq", "yoy"):
                _pass_metric(modes[key], passed[key], state, labels[key])
            if not _final_selected(state, min_score):
                continue

            results.append({
                "stock_code": code,
                "stock_name": s["stock_name"],
                "market": s["market"],
                "sector": s["sector_mid"] or s["sector_large"],
                "current_price": _num(s["close"], 2),
                "market_cap_억": _num((_num(s["market_cap"]) or 0) / 100_000_000, 0),
                "per": _num(per, 2),
                "pbr": _num(pbr, 2),
                "roe": _num(s["roe"], 2),
                "qoq_streak": qoq,
                "yoy_streak": yoy,
                "revenue_qoq_pct": _num(_pct_change(latest_rev, prev_rev), 2),
                "revenue_yoy_pct": _num(_pct_change(latest_rev, yoy_prev_rev), 2),
                "score": state["score"],
                "matched_indicators": state["matched"],
                "passes": passed,
            })

        results.sort(
            key=lambda r: (
                r["score"],
                r["qoq_streak"],
                r["yoy_streak"],
                -(r["pbr"] if r["pbr"] is not None else 999),
            ),
            reverse=True,
        )
        return {"stocks": results[:limit], "count": len(results), "meta": {"modes": modes}}
    finally:
        conn.close()


@router.get("/turnaround-filter")
def get_turnaround_filter(
    depr_threshold: float = Query(20.0, description="감가상각비율 하한(%)"),
    revenue_qoq_streak: int = Query(1, ge=1, le=4),
    revenue_yoy_streak: int = Query(1, ge=1, le=4),
    loss_improve_threshold: float = Query(20.0, description="영업손실 축소율 하한(%)"),
    min_score: int = Query(3, ge=0, le=8),
    limit: int = Query(100, ge=1, le=500),
):
    """현재 적자지만 흑자전환 가능성이 높은 후보 발굴."""
    conn = _ro_conn(DB_PATH)
    trade_conn = _ro_conn(TRADE_DB_PATH)
    emp_conn = _ro_conn(EMP_DB_PATH)
    try:
        fins = _quarterly_rows(conn)
        cash = {
            r["stock_code"]: dict(r)
            for r in conn.execute("""
                SELECT c.stock_code, c.year, c.quarter, c.depreciation
                FROM cash_flow_data c
                JOIN (
                    SELECT stock_code, MAX(year * 10 + quarter) AS yq
                    FROM cash_flow_data
                    WHERE is_annual = 0 AND quarter > 0
                    GROUP BY stock_code
                ) x ON x.stock_code = c.stock_code AND x.yq = c.year * 10 + c.quarter
            """).fetchall()
        }
        latest_contracts = {
            r["stock_code"]: dict(r)
            for r in conn.execute("""
                SELECT stock_code, MAX(signal_strength) AS strength,
                       MAX(contract_ratio_pct) AS ratio
                FROM dart_contracts
                WHERE disclosed_at >= strftime('%Y%m%d', DATE('now','-120 days'))
                  AND stock_code IS NOT NULL
                GROUP BY stock_code
            """).fetchall()
        }
        export_growth = {}
        for r in trade_conn.execute("""
            SELECT stock_code, period_ym, SUM(export_value) AS export_value
            FROM analysis2_company_monthly_cache
            WHERE export_value > 0
            GROUP BY stock_code, period_ym
            ORDER BY stock_code, period_ym DESC
        """).fetchall():
            export_growth.setdefault(r["stock_code"], []).append((r["period_ym"], float(r["export_value"] or 0)))
        export_pct = {}
        for code, months in export_growth.items():
            if len(months) >= 2 and months[1][1] > 0:
                export_pct[code] = (months[0][1] - months[1][1]) / months[1][1] * 100

        emp_rows = emp_conn.execute("""
            SELECT stock_code, SUM(net_change) AS net_change
            FROM (
                SELECT stock_code, net_change,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY data_ym DESC) AS rn
                FROM nps_monthly
            )
            WHERE rn <= 3
            GROUP BY stock_code
        """).fetchall()
        emp_change = {r["stock_code"]: r["net_change"] or 0 for r in emp_rows}

        stocks = conn.execute("""
            SELECT stock_code, stock_name, market, sector_large, sector_mid,
                   close, market_cap, per, pbr, roe
            FROM stock_universe
            WHERE length(stock_code)=6
              AND (stock_type IS NULL OR stock_type != 'ETF')
              AND market_cap IS NOT NULL
              AND market_cap > 0
        """).fetchall()
        results = []
        for s in stocks:
            code = s["stock_code"]
            rows = fins.get(code) or []
            if len(rows) < 2:
                continue
            latest = rows[0]
            prev = rows[1]
            revenue = _num(latest.get("revenue")) or 0
            op = _num(latest.get("operating_profit"))
            prev_op = _num(prev.get("operating_profit"))
            if op is None or op >= 0:
                continue

            depr = _num(latest.get("depreciation_amortization")) or 0
            if not depr and code in cash:
                depr = _num(cash[code].get("depreciation")) or 0
            depr_ratio = (depr / revenue * 100) if revenue > 0 and depr else None
            qoq = _consecutive_qoq(rows, "revenue")
            yoy = _consecutive_yoy(rows, "revenue")
            loss_improve = None
            if prev_op is not None and prev_op < 0:
                loss_improve = (abs(prev_op) - abs(op)) / abs(prev_op) * 100 if prev_op else None

            checks = {
                "감가상각 leverage": depr_ratio is not None and depr_ratio >= depr_threshold,
                "매출 QoQ 연속상승": qoq >= revenue_qoq_streak,
                "매출 YoY 연속상승": yoy >= revenue_yoy_streak,
                "손실 축소": loss_improve is not None and loss_improve >= loss_improve_threshold,
                "수출 증가": (export_pct.get(code) or -999999) > 0,
                "고용 증가": (emp_change.get(code) or 0) > 0,
                "수주공시": (latest_contracts.get(code, {}).get("strength") or 0) >= 2,
            }
            score = sum(1 for v in checks.values() if v)
            if score < min_score:
                continue
            reasons = [k for k, v in checks.items() if v]
            results.append({
                "stock_code": code,
                "stock_name": s["stock_name"],
                "market": s["market"],
                "sector": s["sector_mid"] or s["sector_large"],
                "current_price": _num(s["close"], 2),
                "market_cap_억": _num((_num(s["market_cap"]) or 0) / 100_000_000, 0),
                "op_loss_억": _num(op / 100_000_000, 1),
                "prev_op_억": _num((prev_op or 0) / 100_000_000, 1),
                "loss_improve_pct": _num(loss_improve, 2),
                "depr_ratio": _num(depr_ratio, 2),
                "qoq_streak": qoq,
                "yoy_streak": yoy,
                "export_growth_pct": _num(export_pct.get(code), 2),
                "emp_change_count": int(emp_change.get(code) or 0),
                "contract_strength": latest_contracts.get(code, {}).get("strength"),
                "score": score,
                "reasons": reasons,
                "passes": checks,
            })

        results.sort(key=lambda r: (r["score"], r["loss_improve_pct"] or -999999, r["export_growth_pct"] or -999999), reverse=True)
        return {"stocks": results[:limit], "count": len(results)}
    finally:
        conn.close()
        trade_conn.close()
        emp_conn.close()


def refresh_sector_ai_report(limit: int = 30, force: bool = True) -> dict:
    """미국 지수/섹터 ETF와 시장 뉴스 기반으로 오늘의 주도 섹터와 국내 선도주를 갱신."""
    today = datetime.now().strftime("%Y-%m-%d")
    if not force:
        cached = _safe_cache_read()
        if cached and cached.get("as_of") == today:
            return cached

    conn = _ro_conn(DB_PATH)
    try:
        market_rows = _download_us_market_rows()
        sectors, indices = _score_us_sectors(market_rows)
        news_items = _fetch_market_news()
        trend_titles = _fetch_rss_titles(
            "https://trends.google.com/trends/trendingsearches/daily/rss?"
            + urllib.parse.urlencode({"geo": "US"})
        )
        selected_sectors = sectors[:3]
        sector_payload = sectors[:10]

        ai_summary = ""
        market_view = ""
        ai_used = False
        ai_error = ""
        try:
            import config
            api_key = is_configured()
            if api_key and sector_payload:
                client = OpenAI()
                prompt = (
                    "너는 미국 증시 흐름을 한국 주식 아이디어로 번역하는 섹터 전략가다. "
                    "아래 JSON을 보고 오늘 가장 강한 주도 섹터를 딱 1~3개만 선별한다. "
                    "선별 기준: 5일 상대강도 상위, 20일 추세 양호, 뉴스 촉매 존재. "
                    "이미 단기 급등(20일+40% 이상)하거나 대차잔고·공매도가 많은 섹터는 제외한다. "
                    "반드시 아래 JSON 형식으로만 답하라 (마크다운 금지, JSON만): "
                    "{\"summary\":\"한 문장 시황\", \"market_view\":\"전망 한 문장\", "
                    "\"leading_sectors\":[{\"ticker\":\"SOXX\",\"kr\":\"반도체\","
                    "\"reason\":\"선정이유 1~2문장\",\"risk\":\"리스크 1문장\"}]}\n"
                    + json.dumps({
                        "market_indices": indices,
                        "sector_etfs": sector_payload[:7],
                        "us_market_news_titles": news_items[:10],
                        "google_trends_daily_titles": trend_titles[:4],
                    }, ensure_ascii=False)
                )
                res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=320,
                )
                content = res.choices[0].message.content or ""
                parsed = _parse_ai_json(content)
                if isinstance(parsed, dict):
                    ai_summary = parsed.get("summary", "")
                    market_view = parsed.get("market_view", "")
                    leader_map = {str(x.get("ticker", "")).upper(): x for x in parsed.get("leading_sectors", []) if isinstance(x, dict)}
                    reordered = []
                    for s in sectors:
                        hit = leader_map.get(s["ticker"])
                        if hit:
                            s["ai_reason"] = hit.get("reason")
                            s["ai_risk"] = hit.get("risk")
                            s["ai_ranked"] = True
                            reordered.append(s)
                    selected_sectors = reordered[:3] or selected_sectors[:3]
                    ai_used = True
                else:
                    ai_summary = content[:1000]
                    ai_used = True
        except Exception as e:
            ai_error = str(e)[:300]
            logger.warning(f"[sector-ai-leaders] OpenAI 분석 실패: {e}")

        stocks = []
        seen = set()
        for sector_row in selected_sectors:
            leaders = _local_leaders_for_keywords(conn, sector_row.get("keywords", []), limit=6)
            for stock in leaders:
                code = stock["stock_code"]
                if code in seen:
                    continue
                seen.add(code)
                stock["source_sector"] = sector_row.get("kr")
                stock["source_sector_ticker"] = sector_row.get("ticker")
                stock["source_sector_reason"] = sector_row.get("ai_reason") or f"{sector_row.get('kr')} ETF 상대강도 상위"
                stocks.append(stock)
                if len(stocks) >= limit:
                    break
            if len(stocks) >= limit:
                break

        data = {
            "as_of": today,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sectors": selected_sectors,
            "all_sectors": sectors[:12],
            "market_indices": indices,
            "stocks": stocks,
            "count": len(stocks),
            "ai_used": ai_used,
            "ai_required": True,
            "ai_summary": ai_summary,
            "market_view": market_view,
            "ai_error": ai_error,
            "news_items": news_items,
            "news_titles": [x["title"] for x in news_items],
            "trend_titles": trend_titles[:8],
            "data_sources": ["yfinance_us_indices", "yfinance_sector_etfs", "yahoo_finance_rss", "cnbc_rss", "marketwatch_rss", "google_news_rss", "google_trends_rss", "stock_universe", "price_history", "OpenAI" if ai_used else "rule_fallback"],
        }
        _safe_cache_write(data)
        return data
    finally:
        conn.close()


@router.get("/sector-ai-leaders")
def get_sector_ai_leaders(
    limit: int = Query(30, ge=5, le=100),
    refresh: bool = False,
):
    """미국 증시/뉴스 기반 오늘의 주도 섹터와 국내 관련 선도주 추천."""
    cached = _safe_cache_read()
    today = datetime.now().strftime("%Y-%m-%d")
    if not refresh and cached and cached.get("as_of") == today:
        return cached
    return refresh_sector_ai_report(limit=limit, force=True)


# ──────────────────────────────────────────────
# 낙폭과대 회복탄력주 발굴
# ──────────────────────────────────────────────

@router.get("/recovery-candidates")
def get_recovery_candidates(
    days: int = Query(10, ge=5, le=30, description="하락 기준 거래일 수"),
    drop_min: float = Query(8.0, ge=3.0, le=40.0, description="최소 하락률 (%)"),
    limit: int = Query(40, ge=10, le=100),
):
    """
    실적 우량 + 과도 하락 + 회복탄력 종목 발굴.

    스코어링 (100점 만점):
      [하락 강도]   10pt — 더 많이 빠질수록 반등 기대↑
      [실적 우량]   30pt — 매출·영업이익 성장, OP마진, ROE
      [저평가]      20pt — PBR·PER 저평가 정도
      [수급 반전]   20pt — 하락기 기관/외국인 순매수 전환 신호
      [기술적 지지] 20pt — 52주 저점 근접, 과거 반등 이력
    """
    conn = _get_conn()
    try:
        # 1. 기준일 확정 (최근 영업일 목록)
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM price_history "
            "WHERE stock_code='005930' AND close>0 "
            "ORDER BY date DESC LIMIT 40"
        ).fetchall()]
        if len(dates) < days + 1:
            return {"error": "가격 데이터 부족"}

        end_date   = dates[0]      # 최신일
        start_date = dates[days]   # N거래일 전

        # 2. N거래일 전·후 종가 조회 → 하락률 계산
        drop_rows = conn.execute("""
            WITH end_p AS (
                SELECT stock_code, close AS p_end
                FROM price_history WHERE date = ? AND close > 0
            ),
            start_p AS (
                SELECT stock_code, close AS p_start
                FROM price_history WHERE date = ? AND close > 0
            )
            SELECT
                e.stock_code,
                s.p_start,
                e.p_end,
                ROUND(CAST((e.p_end - s.p_start) / s.p_start * 100.0 AS NUMERIC), 2) AS pct_change
            FROM end_p e
            JOIN start_p s ON s.stock_code = e.stock_code
            WHERE (e.p_end - s.p_start) / s.p_start * 100.0 <= -?
        """, (end_date, start_date, drop_min)).fetchall()

        if not drop_rows:
            return {"results": [], "meta": {"end_date": end_date, "start_date": start_date, "drop_min": drop_min}}

        codes = [r[0] for r in drop_rows]
        drop_map = {r[0]: {"p_start": r[1], "p_end": r[2], "pct_change": r[3]} for r in drop_rows}

        # 3. 종목 유니버스 필터 (ETF/지수 제외, 코스피·코스닥 주식만)
        ph = ",".join("?" * len(codes))
        universe = conn.execute(f"""
            SELECT stock_code, stock_name, market, sector_large, market_cap, per, pbr, roe
            FROM stock_universe
            WHERE stock_code IN ({ph})
              AND market IN ('유가증권', '코스닥', 'KOSPI', 'KOSDAQ')
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """, codes).fetchall()
        universe = {r["stock_code"]: dict(r) for r in universe}

        # 4. 재무 데이터 (최근 연간)
        # 2026-08-28: financial_data 연간행 중복(quarter=0/4/NULL 3중 관례 혼재 — CLAUDE.md
        # 참조)이 있으면 기존 단순 MAX(CASE...)가 stock_code당 여러 (year) 중복행을 그대로
        # 집계에 흘려보내 수치가 왜곡될 수 있었음 — ROW_NUMBER()로 (stock_code,year)당
        # 하나의 대표행만 먼저 뽑은 뒤 집계하도록 변경.
        fin_rows = conn.execute(f"""
            WITH ranked AS (
                SELECT f.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY f.stock_code, f.year
                           ORDER BY (CASE WHEN f.report_type='CFS' THEN 0 ELSE 1 END),
                                    (CASE WHEN f.quarter=4 THEN 0 WHEN f.quarter=0 THEN 1 ELSE 2 END),
                                    (CASE WHEN f.data_source='dart' THEN 0 ELSE 1 END),
                                    f.id DESC
                       ) AS rn
                FROM financial_data f
                WHERE f.stock_code IN ({ph}) AND f.is_annual = 1
            ),
            dedup AS (
                SELECT *, MAX(year) OVER (PARTITION BY stock_code) AS latest_year
                FROM ranked WHERE rn = 1
            )
            SELECT stock_code,
                   MAX(CASE WHEN year = latest_year THEN revenue END) AS rev_cur,
                   MAX(CASE WHEN year = latest_year - 1 THEN revenue END) AS rev_prev,
                   MAX(CASE WHEN year = latest_year THEN operating_profit END) AS op_cur,
                   MAX(CASE WHEN year = latest_year - 1 THEN operating_profit END) AS op_prev,
                   MAX(CASE WHEN year = latest_year THEN roe END) AS roe
            FROM dedup
            GROUP BY stock_code
        """, codes).fetchall()
        fin_map = {r["stock_code"]: dict(r) for r in fin_rows}

        # 5. 52주 저점·고점
        ma_rows = conn.execute(f"""
            SELECT stock_code,
                   MIN(CASE WHEN close > 0 THEN close END) AS low52,
                   MAX(close) AS high52
            FROM price_history
            WHERE stock_code IN ({ph})
              AND date >= date(?, '-365 days')
              AND close > 0
            GROUP BY stock_code
        """, codes + [end_date]).fetchall()
        ma_map = {r["stock_code"]: dict(r) for r in ma_rows}

        # 6. 하락기 수급 (최근 N일 기관+외국인 순매수 합계)
        supply_rows = conn.execute(f"""
            SELECT stock_code,
                   SUM(inst_net_buy_amt)  / 100.0 AS inst_億,
                   SUM(frn_net_buy_amt)   / 100.0 AS frn_億,
                   SUM(inst_net_buy)  AS inst_qty,
                   SUM(frn_net_buy)   AS frn_qty
            FROM price_history
            WHERE stock_code IN ({ph})
              AND date BETWEEN ? AND ?
              AND close > 0
            GROUP BY stock_code
        """, codes + [start_date, end_date]).fetchall()
        supply_map = {r["stock_code"]: dict(r) for r in supply_rows}

        # 7. 과거 반등 이력 — 직전 1년 내 10일 급락 후 20일 반등 평균
        bounce_rows = conn.execute(f"""
            WITH daily AS (
                SELECT stock_code, date, close,
                       LAG(close, 10) OVER(PARTITION BY stock_code ORDER BY date) AS close_10d_ago,
                       LEAD(close, 20) OVER(PARTITION BY stock_code ORDER BY date) AS close_20d_later
                FROM price_history
                WHERE stock_code IN ({ph})
                  AND date >= date(?, '-365 days')
                  AND close > 0
            )
            SELECT stock_code,
                   AVG((close_20d_later - close) / close * 100.0) AS avg_bounce_pct,
                   COUNT(*) AS bounce_count
            FROM daily
            WHERE close_10d_ago > 0
              AND (close - close_10d_ago) / close_10d_ago * 100.0 <= -8.0
              AND close_20d_later > 0
            GROUP BY stock_code
        """, codes + [end_date]).fetchall()
        bounce_map = {r["stock_code"]: dict(r) for r in bounce_rows}

        # 8. 스코어링
        results = []
        for code, u in universe.items():
            if code not in drop_map:
                continue
            d  = drop_map[code]
            f  = fin_map.get(code, {})
            ma = ma_map.get(code, {})
            sp = supply_map.get(code, {})
            bv = bounce_map.get(code, {})

            score = 0
            reasons = []

            # [A] 하락 강도 (10pt)
            pct = abs(d["pct_change"])
            if pct >= 25:
                score += 10; reasons.append(f"급락 {pct:.1f}%↓ (강한반등기대)")
            elif pct >= 18:
                score += 8;  reasons.append(f"급락 {pct:.1f}%↓")
            elif pct >= 12:
                score += 6;  reasons.append(f"조정 {pct:.1f}%↓")
            else:
                score += 4;  reasons.append(f"하락 {pct:.1f}%↓")

            # [B] 실적 우량 (30pt)
            rev_cur  = _num(f.get("rev_cur"))
            rev_prev = _num(f.get("rev_prev"))
            op_cur   = _num(f.get("op_cur"))
            op_prev  = _num(f.get("op_prev"))
            roe_val  = _num(u.get("roe") or f.get("roe"))

            if rev_cur and rev_prev and rev_prev > 0:
                rev_growth = (rev_cur - rev_prev) / rev_prev * 100
                if rev_growth >= 20:
                    score += 10; reasons.append(f"매출 +{rev_growth:.0f}%YoY")
                elif rev_growth >= 10:
                    score += 7;  reasons.append(f"매출 +{rev_growth:.0f}%YoY")
                elif rev_growth >= 0:
                    score += 3
            else:
                rev_growth = None

            if op_cur and op_prev and op_prev > 0 and op_cur > 0:
                op_growth = (op_cur - op_prev) / op_prev * 100
                if op_growth >= 30:
                    score += 10; reasons.append(f"영업이익 +{op_growth:.0f}%YoY")
                elif op_growth >= 15:
                    score += 7;  reasons.append(f"영업이익 +{op_growth:.0f}%YoY")
                elif op_growth >= 0:
                    score += 3
            else:
                op_growth = None

            if rev_cur and op_cur and rev_cur > 0 and op_cur > 0:
                opm = op_cur / rev_cur * 100
                if opm >= 15:
                    score += 10; reasons.append(f"OP마진 {opm:.1f}%")
                elif opm >= 8:
                    score += 7
                elif opm >= 3:
                    score += 4
            else:
                opm = None

            if roe_val and roe_val >= 10:
                score += 5; reasons.append(f"ROE {roe_val:.1f}%")
            elif roe_val and roe_val >= 5:
                score += 2

            # [C] 저평가 (20pt)
            pbr = _num(u.get("pbr"))
            per = _num(u.get("per"))
            if pbr:
                if pbr <= 0.7:
                    score += 10; reasons.append(f"PBR {pbr:.2f} (극저평가)")
                elif pbr <= 1.2:
                    score += 7;  reasons.append(f"PBR {pbr:.2f}")
                elif pbr <= 2.0:
                    score += 4
            if per and 0 < per <= 10:
                score += 10; reasons.append(f"PER {per:.1f}")
            elif per and per <= 15:
                score += 7
            elif per and per <= 20:
                score += 3

            # [D] 수급 반전 (20pt) — 하락기에 기관/외국인이 사줬으면 반등 가능성↑
            inst = _num(sp.get("inst_億"))
            frn  = _num(sp.get("frn_億"))
            both = (inst or 0) + (frn or 0)
            if inst and inst > 5:
                score += 10; reasons.append(f"기관 낙폭매수 {inst:.0f}억")
            elif inst and inst > 0:
                score += 6
            if frn and frn > 5:
                score += 10; reasons.append(f"외국인 낙폭매수 {frn:.0f}억")
            elif frn and frn > 0:
                score += 5

            # [E] 기술적 지지 (20pt)
            low52  = _num(ma.get("low52"))
            high52 = _num(ma.get("high52"))
            p_end  = d["p_end"]

            if low52 and p_end:
                pct_above_low = (p_end - low52) / low52 * 100 if low52 > 0 else None
                if pct_above_low is not None:
                    if pct_above_low <= 5:
                        score += 10; reasons.append("52주 저점 근접 (강한지지)")
                    elif pct_above_low <= 15:
                        score += 7;  reasons.append("52주 저점 근접")
                    elif pct_above_low <= 30:
                        score += 3
            else:
                pct_above_low = None

            avg_bounce = _num(bv.get("avg_bounce_pct"))
            if avg_bounce and avg_bounce >= 15:
                score += 10; reasons.append(f"과거 급락후 평균반등 +{avg_bounce:.0f}%")
            elif avg_bounce and avg_bounce >= 8:
                score += 5

            if score < 30:
                continue  # 최소 30점 미만 제외

            results.append({
                "stock_code":    code,
                "stock_name":    u["stock_name"],
                "sector":        u.get("sector_large"),
                "market":        u.get("market"),
                "market_cap":    _num(u.get("market_cap"), 0),
                "score":         round(score, 1),
                "pct_change":    d["pct_change"],
                "p_start":       d["p_start"],
                "p_end":         d["p_end"],
                "rev_growth":    _num(rev_growth, 1),
                "op_growth":     _num(op_growth, 1),
                "opm":           _num(opm, 1),
                "roe":           _num(roe_val, 1),
                "per":           _num(per, 1),
                "pbr":           _num(pbr, 2),
                "inst_億":       _num(inst, 1),
                "frn_億":        _num(frn, 1),
                "pct_above_low": _num(pct_above_low, 1),
                "avg_bounce":    _num(avg_bounce, 1),
                "reasons":       reasons[:4],
            })

        results = _enrich_tenbagger_price_risk(conn, results)
        results.sort(
            key=lambda x: (x.get("risk_adjusted_score", x.get("score") or 0), x.get("score") or 0),
            reverse=True,
        )
        results = results[:limit]

        return {
            "results": results,
            "meta": {
                "end_date":   end_date,
                "start_date": start_date,
                "days":       days,
                "drop_min":   drop_min,
                "total":      len(results),
                "avg_score":  round(sum(r["score"] for r in results) / len(results), 1) if results else 0,
            },
        }
    finally:
        conn.close()


# ── BigQuery Week2 복합 신호 API ─────────────────────────────────────────────

_BQ_CACHE: dict = {}
_BQ_CACHE_TTL = 3600


def _bq_query_cached(key: str, sql: str, ttl: int = _BQ_CACHE_TTL):
    import time
    if key in _BQ_CACHE:
        ts, data = _BQ_CACHE[key]
        if time.time() - ts < ttl:
            return data
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project="project-d8a62269-8156-4f96-870")
        rows = list(client.query(sql).result())
        data = [dict(r.items()) for r in rows]
        _BQ_CACHE[key] = (time.time(), data)
        return data
    except Exception as e:
        logger.warning("BQ query failed: %s", e)
        return None


@router.get("/bq-composite")
def get_bq_composite(limit: int = Query(50, ge=1, le=200)):
    """BigQuery Week2 composite signal."""
    P = "project-d8a62269-8156-4f96-870"
    D = "stock_dashboard"
    sql = (
        "SELECT stock_code, stock_name, sector_large, market, market_cap,"
        " tenbagger_score, triple_score, supply_net_10d, supply_label,"
        " composite_score, latest_close, reasons, run_time"
        " FROM `" + P + "." + D + ".v_tenbagger_composite_week2`"
        " ORDER BY composite_score DESC LIMIT " + str(limit)
    )
    data = _bq_query_cached("bq_composite", sql)
    if data is None:
        return {"error": "BQ unavailable", "results": []}

    results = []
    for r in data:
        reasons = r.get("reasons") or ""
        if isinstance(reasons, str):
            try:
                import json as _j
                reasons = _j.loads(reasons)
            except Exception:
                reasons = [reasons]
        results.append({
            "stock_code":      r.get("stock_code"),
            "stock_name":      r.get("stock_name"),
            "sector":          r.get("sector_large"),
            "market":          r.get("market"),
            "market_cap":      r.get("market_cap"),
            "tenbagger_score": r.get("tenbagger_score"),
            "triple_score":    r.get("triple_score"),
            "supply_net_10d":  r.get("supply_net_10d"),
            "supply_label":    r.get("supply_label"),
            "composite_score": r.get("composite_score"),
            "price":           r.get("latest_close"),
            "reasons":         reasons[:4] if isinstance(reasons, list) else [str(reasons)],
            "run_time":        str(r.get("run_time") or ""),
        })
    return {"results": results, "total": len(results), "source": "bigquery_week2"}


@router.get("/bq-sector")
def get_bq_sector():
    """BigQuery Week2 sector aggregation."""
    P = "project-d8a62269-8156-4f96-870"
    D = "stock_dashboard"
    sql = (
        "SELECT sector_large, stock_count, avg_score, max_score,"
        " both_buy_count, high_score_count, top_stocks"
        " FROM `" + P + "." + D + ".v_sector_tenbagger_week2`"
        " ORDER BY avg_score DESC LIMIT 30"
    )
    data = _bq_query_cached("bq_sector", sql)
    if data is None:
        return {"error": "BQ unavailable", "sectors": []}
    return {"sectors": [dict(r) for r in data]}


# ── OpenAI mini 텐버거 심층 분석 ───────────────────────────────────────

DB_PATH = str(STOCK_DB_PATH)
_AI_CACHE_HOURS = 24  # 같은 날 재호출 시 캐시 반환


def _build_tenbagger_context(stock_code: str) -> dict:
    """DB에서 종목의 재무/수급/공시/스코어 컨텍스트를 수집"""
    conn = connect_primary_db(timeout=30, row_factory=sqlite3.Row)

    # 기본 정보
    meta = conn.execute(
        "SELECT stock_name, sector_large, market, market_cap, per, pbr, roe FROM stock_universe WHERE stock_code=?",
        (stock_code,)
    ).fetchone()
    stock_name = meta["stock_name"] if meta else stock_code

    # 최신 텐버거 스코어 + 이유
    tb = conn.execute(
        "SELECT total_score AS score, reasons, run_time FROM tenbagger_results WHERE stock_code=? ORDER BY run_time DESC LIMIT 1",
        (stock_code,)
    ).fetchone()

    # 최근 5개 분기 재무
    fin_rows = conn.execute("""
        SELECT year, quarter, revenue, operating_profit, net_income, total_assets, total_equity
        FROM financial_data
        WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC, quarter DESC LIMIT 5
    """, (stock_code,)).fetchall()

    # 연간 재무 2년
    ann_rows = conn.execute("""
        SELECT year, revenue, operating_profit, net_income, total_assets, total_equity
        FROM financial_data
        WHERE stock_code=? AND is_annual=1
        ORDER BY year DESC LIMIT 2
    """, (stock_code,)).fetchall()

    # 최근 현금흐름
    cf_rows = conn.execute("""
        SELECT year, quarter, operating_cf, investing_cf, capex,
               (COALESCE(operating_cf_q,operating_cf,0) - ABS(COALESCE(capex_q,capex,0))) AS free_cf
        FROM cash_flow_data
        WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC, quarter DESC LIMIT 4
    """, (stock_code,)).fetchall()

    # 수급 (최근 60일)
    supply = conn.execute("""
        SELECT date, close, inst_net_buy, frn_net_buy, inst_net_buy_amt, frn_net_buy_amt
        FROM price_history
        WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 60
    """, (stock_code,)).fetchall()

    # 최근 공시 5건
    disc = conn.execute("""
        SELECT rcept_dt, report_nm FROM dart_disclosures
        WHERE stock_code=? ORDER BY rcept_dt DESC LIMIT 5
    """, (stock_code,)).fetchall()

    # 수주잔고
    backlog = conn.execute("""
        SELECT year, quarter, backlog_amount, data_source AS source_text
        FROM order_backlog WHERE stock_code=? ORDER BY year DESC, quarter DESC LIMIT 2
    """, (stock_code,)).fetchall()

    # 원가율
    cost = conn.execute("""
        SELECT year, quarter, cogs_ratio FROM cost_structure
        WHERE stock_code=? ORDER BY year DESC, quarter DESC LIMIT 4
    """, (stock_code,)).fetchall()

    # 신용잔고 추이
    margin = conn.execute("""
        SELECT dt, credit_balance FROM margin_balance_daily
        WHERE stock_code=? ORDER BY dt DESC LIMIT 10
    """, (stock_code,)).fetchall()

    conn.close()

    def _b(v): return f"{v/1e8:.0f}억" if v and abs(v) >= 1e8 else (f"{v:,.0f}원" if v else "-")
    def _pct(a, b): return f"{(a-b)/abs(b)*100:+.1f}%" if b and b != 0 else ""

    # 재무 컨텍스트 구성
    fin_lines = []
    for r in fin_rows:
        rev = r["revenue"]; op = r["operating_profit"]; ni = r["net_income"]
        opm = f"{op/rev*100:.1f}%" if rev and op else "-"
        fin_lines.append(f"  {r['year']}Q{r['quarter']}: 매출 {_b(rev)}, 영업익 {_b(op)}(OPM {opm}), 순익 {_b(ni)}")

    for r in ann_rows:
        rev = r["revenue"]; op = r["operating_profit"]; ni = r["net_income"]
        opm = f"{op/rev*100:.1f}%" if rev and op else "-"
        fin_lines.append(f"  {r['year']}연간: 매출 {_b(rev)}, 영업익 {_b(op)}(OPM {opm}), 순익 {_b(ni)}")

    cf_lines = [f"  {r['year']}Q{r['quarter']}: OCF {_b(r['operating_cf'])}, CAPEX {_b(r['capex'])}, FCF {_b(r['free_cf'])}" for r in cf_rows]

    # 수급 집계
    inst_5d = sum(r["inst_net_buy_amt"] or 0 for r in supply[:5]) / 100  # 억
    frn_5d  = sum(r["frn_net_buy_amt"]  or 0 for r in supply[:5]) / 100
    inst_20d = sum(r["inst_net_buy_amt"] or 0 for r in supply[:20]) / 100
    frn_20d  = sum(r["frn_net_buy_amt"]  or 0 for r in supply[:20]) / 100
    supply_ctx = (f"  기관: 5일 {inst_5d:+.0f}억 / 20일 {inst_20d:+.0f}억\n"
                  f"  외국인: 5일 {frn_5d:+.0f}억 / 20일 {frn_20d:+.0f}억")

    backlog_ctx = "\n".join([f"  {r['year']}Q{r['quarter']}: {_b(r['backlog_amount'])}" for r in backlog]) or "  데이터 없음"
    cost_ctx = "\n".join([f"  {r['year']}Q{r['quarter']}: 원가율 {r['cogs_ratio']:.1f}%" for r in cost if r['cogs_ratio']]) or "  데이터 없음"
    margin_ctx = ""
    if margin:
        recent = margin[0]["credit_balance"]; old = margin[-1]["credit_balance"]
        chg = f"{(recent-old)/old*100:+.1f}%" if old else "-"
        margin_ctx = f"  최근 {margin[0]['dt']}: {recent:,}주 (10일전 대비 {chg})"

    disc_ctx = "\n".join([f"  [{r['rcept_dt']}] {r['report_nm']}" for r in disc]) or "  없음"

    return {
        "stock_name": stock_name,
        "meta": dict(meta) if meta else {},
        "tb_score": tb["score"] if tb else None,
        "tb_reasons": tb["reasons"] if tb else "",
        "fin_ctx": "\n".join(fin_lines) or "  재무 데이터 없음",
        "cf_ctx": "\n".join(cf_lines) or "  현금흐름 데이터 없음",
        "supply_ctx": supply_ctx,
        "backlog_ctx": backlog_ctx,
        "cost_ctx": cost_ctx,
        "margin_ctx": margin_ctx or "  데이터 없음",
        "disc_ctx": disc_ctx,
    }


def _call_openai_mini_tenbagger(ctx: dict) -> str:

    meta = ctx["meta"]
    mktcap = meta.get("market_cap", 0)
    per = meta.get("per") or "-"
    pbr = meta.get("pbr") or "-"
    roe = meta.get("roe") or "-"
    sector = meta.get("sector_large", "-")

    prompt = f"""
당신은 한국 주식 투자 전문 애널리스트입니다.
아래 데이터를 바탕으로 **{ctx['stock_name']}**이 텐버거(10배 수익) 후보인 이유를 심층 분석하세요.

## 기본 정보
- 섹터: {sector} | 시총: {mktcap:,}억원 | PER: {per} | PBR: {pbr} | ROE: {roe}%
- 텐버거 엔진 점수: {ctx['tb_score']}점
- 엔진 선정 이유: {ctx['tb_reasons']}

## 분기 실적 (최근 5분기 + 연간)
{ctx['fin_ctx']}

## 현금흐름 (최근 4분기)
{ctx['cf_ctx']}

## 투자자 수급
{ctx['supply_ctx']}

## 수주잔고
{ctx['backlog_ctx']}

## 원가율 추이
{ctx['cost_ctx']}

## 신용잔고
{ctx['margin_ctx']}

## 최근 공시
{ctx['disc_ctx']}

---
분석 형식을 반드시 아래와 같이 작성하세요:

### 한 줄 결론
(이 종목이 텐버거 후보인 핵심 한 문장)

### 성장 모멘텀 분석
- 매출/이익 성장세의 구체적 수치와 가속화 여부
- 분기별 OPM 변화 추이가 의미하는 것

### 수급 관점
- 기관/외국인 최근 움직임의 투자 해석
- 신용잔고 변화가 시사하는 것

### 밸류에이션 매력
- 현재 PER/PBR이 섹터/업황 대비 어느 수준인지
- 저평가/적정/고평가 판단 근거

### 사업 구조 & 경쟁 우위
- 수주잔고/원가율/공시 기반 사업 동력
- 이 회사만의 해자(moat)가 있다면?

### 텐버거 달성 시나리오
- 구체적으로 어떤 조건이 충족되면 주가가 10배 갈 수 있는지
- 1~3년 내 실현 가능한 트리거는?

### 핵심 리스크
- 이 시나리오가 실패하는 조건 2~3가지

### 다음 분기 체크포인트 3가지
(숫자 기반, 구체적)
"""

    if not is_configured():
        return f"## {ctx['stock_name']} 텐버거 분석\n\nDeepSeek API 키가 설정되지 않았습니다."

    try:
        return generate_text(
            prompt,
            system_instruction="당신은 10배 수익 후보 종목을 발굴하는 한국 주식 투자 전문 애널리스트입니다. 데이터 기반으로 냉정하게 분석하되, 텐버거 가능성이 있는 핵심 근거를 구체적 수치와 함께 제시하세요.",
            temperature=0.3,
            max_output_tokens=2000,
            timeout=90,
        )
    except Exception as e:
        logger.error("[Tenbagger AI] DeepSeek Flash 호출 실패: %s", e)
    return f"## {ctx['stock_name']} 분석 실패\n\nAPI 응답 오류가 발생했습니다."


@router.get("/ai-analysis/{stock_code}")
def get_tenbagger_ai_analysis(stock_code: str, force: bool = False):
    """텐버거 후보 종목 OpenAI mini 심층 분석. 24시간 캐시."""
    import sqlite3 as _sl, os
    from datetime import datetime as _dt, timedelta
    conn = connect_primary_db(timeout=30, row_factory=_sl.Row)

    if not force:
        cached = conn.execute(
            "SELECT ai_analysis, generated_at, score FROM tenbagger_ai_analysis"
            " WHERE stock_code=? ORDER BY generated_at DESC LIMIT 1",
            (stock_code,)
        ).fetchone()
        if cached:
            gen_dt = _dt.fromisoformat(cached["generated_at"])
            if _dt.now() - gen_dt < timedelta(hours=_AI_CACHE_HOURS):
                conn.close()
                return {"stock_code": stock_code, "analysis": cached["ai_analysis"],
                        "generated_at": cached["generated_at"], "score": cached["score"], "cached": True}

    conn.close()

    ctx = _build_tenbagger_context(stock_code)
    analysis = _call_openai_mini_tenbagger(ctx)

    # 저장
    conn2 = connect_primary_db(timeout=30, row_factory=_sl.Row)
    now = _dt.now().isoformat()
    conn2.execute(
        "INSERT INTO tenbagger_ai_analysis(stock_code, generated_at, score, reasons, ai_analysis, model)"
        " VALUES(?,?,?,?,?,?)",
        (stock_code, now, ctx["tb_score"], ctx["tb_reasons"], analysis,
         model_name())
    )
    conn2.commit()
    conn2.close()

    return {"stock_code": stock_code, "stock_name": ctx["stock_name"],
            "analysis": analysis, "generated_at": now, "score": ctx["tb_score"], "cached": False}


@router.get("/ai-analysis-list")
def get_ai_analysis_list(limit: int = Query(20, ge=1, le=100)):
    """최근 생성된 AI 분석 목록"""
    conn = connect_primary_db(timeout=30, row_factory=sqlite3.Row)
    rows = conn.execute(
        "SELECT stock_code, generated_at, score, model FROM tenbagger_ai_analysis"
        " ORDER BY generated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows]}


@router.post("/ai-analysis-batch")
def run_ai_analysis_batch(
    top_n: int = Query(20, ge=1, le=50, description="분석할 상위 종목 수"),
    min_score: int = Query(55, ge=0, le=100, description="최소 텐버거 점수"),
    force: bool = Query(False, description="캐시 무시 강제 재분석"),
    background_tasks: BackgroundTasks = None,
):
    """
    텐버거 상위 후보 N종목 OpenAI mini 심층 분석 배치 실행.
    - 24시간 이내 캐시된 종목은 건너뜀 (force=True 시 강제 재분석)
    - 백그라운드 실행: 즉시 job_id 반환, 진행 상황은 /ai-analysis-list 에서 확인
    """
    import threading, time as _t
    from datetime import datetime as _dt, timedelta

    conn = connect_primary_db(timeout=30, row_factory=sqlite3.Row)

    # 상위 후보 조회
    candidates = conn.execute("""
        SELECT r.stock_code, u.stock_name, r.total_score
        FROM tenbagger_results r
        JOIN stock_universe u ON u.stock_code = r.stock_code
        WHERE r.total_score >= ?
        ORDER BY r.total_score DESC, r.run_time DESC
        LIMIT ?
    """, (min_score, top_n)).fetchall()

    if not candidates:
        conn.close()
        return {"status": "no_candidates", "message": f"점수 {min_score}점 이상 종목 없음"}

    # 캐시 있는 종목 제외 (force=False 시)
    already_cached = set()
    if not force:
        cutoff = (_dt.now() - timedelta(hours=_AI_CACHE_HOURS)).isoformat()
        cached_rows = conn.execute(
            "SELECT DISTINCT stock_code FROM tenbagger_ai_analysis WHERE generated_at > ?",
            (cutoff,)
        ).fetchall()
        already_cached = {r[0] for r in cached_rows}

    conn.close()

    to_analyze = [(r["stock_code"], r["stock_name"], r["total_score"])
                  for r in candidates if r["stock_code"] not in already_cached]

    job_id = f"batch_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
    logger.info("[AI배치] job=%s 대상=%d종목 (캐시건너뜀=%d)", job_id, len(to_analyze), len(already_cached))

    def _batch_worker():
        ok = skip = fail = 0
        for sc, sname, score in to_analyze:
            try:
                ctx = _build_tenbagger_context(sc)
                analysis = _call_openai_mini_tenbagger(ctx)
                conn2 = connect_primary_db(timeout=60, row_factory=sqlite3.Row)
                conn2.execute(
                    "INSERT INTO tenbagger_ai_analysis"
                    "(stock_code, generated_at, score, reasons, ai_analysis, model)"
                    " VALUES(?,?,?,?,?,?)",
                    (sc, _dt.now().isoformat(), score, ctx.get("tb_reasons", ""),
                     analysis, model_name())
                )
                conn2.commit()
                conn2.close()
                ok += 1
                logger.info("[AI배치] ✅ %s(%s) score=%d [%d/%d]", sname, sc, score, ok, len(to_analyze))
                _t.sleep(1.5)  # rate limit 회피
            except Exception as e:
                fail += 1
                logger.error("[AI배치] ❌ %s(%s): %s", sname, sc, e)
        logger.info("[AI배치] 완료 — ok=%d skip=%d fail=%d", ok, len(already_cached), fail)

    t = threading.Thread(target=_batch_worker, daemon=True, name=f"ai_batch_{job_id}")
    t.start()

    return {
        "status": "started",
        "job_id": job_id,
        "total_candidates": len(candidates),
        "to_analyze": len(to_analyze),
        "already_cached": len(already_cached),
        "message": f"{len(to_analyze)}종목 백그라운드 분석 시작. /api/tenbagger/ai-analysis-list 에서 진행 확인.",
    }


# ── Week 4: 스크리너 v2 (필터/정렬 지원) ──────────────────────────────────
@router.get("/screener-v2")
def screener_v2(
    min_score: int = Query(50, ge=0, le=100),
    max_score: int = Query(100, ge=0, le=100),
    market: str = Query("ALL", description="ALL|유가증권|코스닥"),
    sector: str = Query("ALL"),
    min_mktcap: float = Query(0, description="최소 시총(억원)"),
    max_mktcap: float = Query(0, description="최대 시총(억원), 0=무제한"),
    max_per: float = Query(0, description="최대 PER, 0=무제한"),
    max_pbr: float = Query(0, description="최대 PBR, 0=무제한"),
    sort: str = Query("total_score", description="total_score|market_cap|per|pbr|revenue_growth"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", description="종목명/코드 검색"),
):
    """스크리너 v2 — 텐버거 결과에 필터/정렬/페이지네이션 지원"""
    conn = connect_primary_db(timeout=20, row_factory=sqlite3.Row)

    # 최신 run_time 기준 결과
    latest = conn.execute(
        "SELECT MAX(run_time) FROM tenbagger_results"
    ).fetchone()[0]

    if not latest:
        conn.close()
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "run_time": None, "sectors": []}

    # 섹터 목록
    sectors = [r[0] for r in conn.execute(
        "SELECT DISTINCT u.sector_large FROM tenbagger_results t "
        "JOIN stock_universe u ON u.stock_code = t.stock_code "
        "WHERE t.run_time = ? AND u.sector_large IS NOT NULL "
        "ORDER BY u.sector_large", (latest,)
    ).fetchall()]

    # 기본 쿼리
    where = ["t.run_time = ?"]
    params: list = [latest]

    where.append("t.total_score >= ? AND t.total_score <= ?")
    params += [min_score, max_score]

    if market != "ALL":
        where.append("u.market = ?"); params.append(market)
    if sector != "ALL":
        where.append("u.sector_large = ?"); params.append(sector)
    if min_mktcap > 0:
        where.append("u.market_cap >= ?"); params.append(min_mktcap)
    if max_mktcap > 0:
        where.append("u.market_cap <= ?"); params.append(max_mktcap)
    if max_per > 0:
        where.append("(u.per IS NULL OR u.per BETWEEN 0.01 AND ?)"); params.append(max_per)
    if max_pbr > 0:
        where.append("(u.pbr IS NULL OR u.pbr BETWEEN 0.01 AND ?)"); params.append(max_pbr)
    if q:
        where.append("(u.stock_name LIKE ? OR t.stock_code LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    where_sql = " AND ".join(where)
    sort_col = sort if sort in ("total_score","market_cap","per","pbr","revenue_growth") else "total_score"
    order_sql = "DESC" if order.lower() == "desc" else "ASC"

    total = conn.execute(
        f"SELECT COUNT(*) FROM tenbagger_results t "
        f"JOIN stock_universe u ON u.stock_code=t.stock_code "
        f"WHERE {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * page_size
    fetch_size = page_size * 3 if sort_col == "total_score" else page_size
    rows = conn.execute(
        f"SELECT t.stock_code, t.stock_name, t.total_score, t.score_detail, t.reasons, "
        f"t.current_price, t.market_cap, t.per, t.pbr, t.roe, "
        f"t.revenue_growth, t.op_growth, t.op_margin, "
        f"t.inst_net_10d, t.frn_net_10d, t.run_type, "
        f"u.sector_large, u.market "
        f"FROM tenbagger_results t "
        f"JOIN stock_universe u ON u.stock_code=t.stock_code "
        f"WHERE {where_sql} "
        f"ORDER BY t.{sort_col} {order_sql} "
        f"LIMIT ? OFFSET ?",
        params + [fetch_size, offset]
    ).fetchall()

    def _parse_reasons(r):
        try: return __import__("json").loads(r) if r else []
        except: return [r] if r else []

    result_rows = [{**dict(r), "reasons": _parse_reasons(r["reasons"])} for r in rows]
    result_rows = _enrich_tenbagger_price_risk(conn, result_rows)
    if sort_col == "total_score":
        result_rows.sort(
            key=lambda x: (x.get("risk_adjusted_score", x.get("total_score") or 0), x.get("total_score") or 0),
            reverse=(order.lower() == "desc"),
        )
    result_rows = result_rows[:page_size]

    conn.close()

    return {
        "results": result_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "run_time": latest,
        "sectors": sectors,
    }


# ── Week 4: FP/FN 오판 역분석 ────────────────────────────────────────────
@router.get("/fp-fn-analysis")
def fp_fn_analysis(
    days_after: int = Query(7, description="선정 후 N일 후 수익률 기준"),
    threshold_tp: float = Query(5.0, description="TP 기준 상승률(%)"),
    threshold_fp: float = Query(-5.0, description="FP 기준 하락률(%)"),
    limit: int = Query(200, ge=10, le=500),
):
    """
    텐버거 후보 선정 후 실제 수익률 기반 FP/FN 오판 분석
    TP: 선정 → 상승 (성공)
    FP: 선정 → 하락 (오류: 왜 틀렸나?)
    FN: 미선정 → 상승 (놓침)
    TN: 미선정 → 하락/횡보 (정상)
    """
    import json as _json
    conn = connect_primary_db(timeout=20, row_factory=sqlite3.Row)

    # 1. 전체 과거 선정 결과 (모든 실행 포함, 오래된 것 우선 — 가격 추적 가능성↑)
    base_rows = conn.execute("""
        SELECT stock_code, stock_name, total_score,
               score_detail, reasons, run_type, current_price AS price_at_select,
               run_time AS selected_at, DATE(run_time) AS select_date
        FROM tenbagger_results
        WHERE total_score >= 55
        ORDER BY run_time ASC
        LIMIT ?
    """, (limit,)).fetchall()

    # 2. N일 후 가격 조회 (종목별 선정일 이후 N번째 거래일)
    rows = []
    for r in base_rows:
        code = r["stock_code"]
        sel_date = r["select_date"]
        ph_rows = conn.execute("""
            SELECT close, date FROM price_history
            WHERE stock_code=? AND date > ? AND close > 0
            ORDER BY date
        """, (code, _to_date(sel_date))).fetchall()
        price_after = None
        after_date = None
        if ph_rows:
            idx = min(days_after - 1, len(ph_rows) - 1)
            price_after = ph_rows[idx][0]
            after_date = ph_rows[idx][1]
        rows.append({**dict(r), "price_after": price_after, "after_date": after_date})

    results = []
    score_factors = {}  # FP 종목의 주요 요인 집계

    for r in rows:
        p0 = (r["price_at_select"] or 0) if isinstance(r, dict) else (r["price_at_select"] or 0)
        p1 = (r.get("price_after") or 0) if isinstance(r, dict) else 0

        if p0 > 0 and p1 > 0:
            ret_pct = (p1 - p0) / p0 * 100
        else:
            ret_pct = None

        # 분류
        if ret_pct is None:
            label = "PENDING"
        elif ret_pct >= threshold_tp:
            label = "TP"
        elif ret_pct <= threshold_fp:
            label = "FP"
        else:
            label = "NEUTRAL"

        reasons = []
        try:
            rv = r["reasons"] if isinstance(r, dict) else r["reasons"]
            reasons = _json.loads(rv) if rv else []
        except:
            pass

        # FP 요인 집계
        if label == "FP":
            for reason in reasons:
                # 이모지 기반 카테고리 추출
                key = reason[:6] if reason else "기타"
                score_factors[key] = score_factors.get(key, 0) + 1

        def _get(key):
            return r[key] if isinstance(r, dict) else r[key]

        results.append({
            "stock_code": _get("stock_code"),
            "stock_name": _get("stock_name"),
            "total_score": _get("total_score"),
            "selected_at": _get("selected_at"),
            "price_at_select": p0,
            "price_after": p1 if p1 > 0 else None,
            "after_date": r.get("after_date") if isinstance(r, dict) else None,
            "return_pct": round(ret_pct, 2) if ret_pct is not None else None,
            "label": label,
            "reasons": reasons[:3],
        })

    # 통계 요약
    tp = [r for r in results if r["label"] == "TP"]
    fp = [r for r in results if r["label"] == "FP"]
    neutral = [r for r in results if r["label"] == "NEUTRAL"]
    pending = [r for r in results if r["label"] == "PENDING"]

    evaluated = [r for r in results if r["return_pct"] is not None]
    avg_return = sum(r["return_pct"] for r in evaluated) / len(evaluated) if evaluated else 0

    # FP 요인 top 5
    fp_factors = sorted(score_factors.items(), key=lambda x: -x[1])[:10]

    conn.close()
    return {
        "summary": {
            "total_candidates": len(results),
            "tp": len(tp),
            "fp": len(fp),
            "neutral": len(neutral),
            "pending": len(pending),
            "precision": round(len(tp) / (len(tp)+len(fp)) * 100, 1) if (tp or fp) else None,
            "avg_return_pct": round(avg_return, 2),
            "days_after": days_after,
        },
        "fp_factors": [{"reason": k, "count": v} for k, v in fp_factors],
        "results": results,
    }


# ── Screener v3: 업황지표 연동 + FP 패널티 + 수주보너스 ────────────────────

SECTOR_INDICATOR_MAP = {
    "IT":         ("epic:3:semi", "한국반도체수출"),    # 반도체/IT → 반도체 수출(HS 8542)
    "소재":       ("epic:0:2",   "한국자동차판매"),    # 철강/화학 원자재 → 자동차판매 간접
    "경기소비재": ("epic:2:98",  "온라인쇼핑거래액"),  # 유통/의류
    "필수소비재": ("epic:2:98",  "온라인쇼핑거래액"),  # 식품/생활용품
    "에너지":     ("epic:6:18",  "SMP전력가격"),       # 에너지 → SMP
    "유틸리티":   ("epic:6:18",  "SMP전력가격"),       # 유틸리티 → SMP
}

# 종목명 기반 개별 지표 매핑 (섹터 매핑보다 우선 적용)
STOCK_NAME_INDICATOR_MAP = {
    # 화장품/뷰티 종목 → 화장품 온라인쇼핑 지표
    "화장품":  ("epic:8:14", "화장품쇼핑"),
    "뷰티":    ("epic:8:14", "화장품쇼핑"),
    # 카지노 종목 → 마카오 GGR
    "카지노":  ("epic:9:13", "마카오GGR"),
    "파라다이스": ("epic:9:13", "마카오GGR"),
    "GKL":     ("epic:9:13", "마카오GGR"),
    "드림타워": ("epic:9:13", "마카오GGR"),
}


def _get_stock_name_indicator(stock_name: str) -> tuple | None:
    """종목명에서 STOCK_NAME_INDICATOR_MAP 매핑 탐색."""
    for keyword, mapping in STOCK_NAME_INDICATOR_MAP.items():
        if keyword in (stock_name or ""):
            return mapping
    return None


def _get_industry_yoy(conn, indicator_key: str, series_name: str | None = None) -> float | None:
    """quant_major_indicator_series에서 최신 월 vs 1년전 YoY 계산."""
    if series_name:
        rows = conn.execute("""
            SELECT period, value FROM quant_major_indicator_series
            WHERE indicator_key = ? AND series_name = ? AND value IS NOT NULL
            ORDER BY period DESC LIMIT 15
        """, (indicator_key, series_name)).fetchall()
    else:
        rows = conn.execute("""
            SELECT period, value FROM quant_major_indicator_series
            WHERE indicator_key = ? AND value IS NOT NULL
            ORDER BY period DESC LIMIT 15
        """, (indicator_key,)).fetchall()
    if not rows:
        return None
    latest_period, latest_val = rows[0]
    # 1년 전 기준 period
    try:
        y, m = int(latest_period[:4]), int(latest_period[5:7])
        prev_y = y - 1
        prev_period = f"{prev_y}-{m:02d}"
    except Exception:
        return None
    for period, val in rows:
        if period == prev_period and val:
            return (latest_val - val) / abs(val) * 100 if val != 0 else None
    return None


def _industry_adj_score(yoy: float | None) -> int:
    if yoy is None:
        return 0
    if yoy >= 20:
        return 3
    if yoy >= 5:
        return 1
    if yoy <= -20:
        return -4
    if yoy <= -10:
        return -2
    return 0


@router.get("/screener-v3")
def screener_v3(
    min_v3_score: int = Query(50, ge=0, le=120),
    min_score: int = Query(40, ge=0, le=100),
    market: str = Query("ALL", description="ALL|유가증권|코스닥"),
    sector: str = Query("ALL"),
    min_mktcap: float = Query(0, description="최소 시총(억원)"),
    max_mktcap: float = Query(0, description="최대 시총(억원), 0=무제한"),
    max_per: float = Query(0, description="최대 PER, 0=무제한"),
    max_pbr: float = Query(0, description="최대 PBR, 0=무제한"),
    sort: str = Query("v3_score", description="v3_score|total_score|market_cap"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    q: str = Query("", description="종목명/코드 검색"),
):
    """스크리너 v3 — 업황지표 YoY 조정 + FP 패널티 + 수주잔고 보너스."""
    import json as _json
    conn = connect_primary_db(timeout=20, row_factory=sqlite3.Row)

    # 최신 run_time
    latest = conn.execute("SELECT MAX(run_time) FROM tenbagger_results").fetchone()[0]
    if not latest:
        conn.close()
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "run_time": None, "sectors": [], "indicator_map": {}}

    # 섹터 목록
    sectors = [r[0] for r in conn.execute(
        "SELECT DISTINCT u.sector_large FROM tenbagger_results t "
        "JOIN stock_universe u ON u.stock_code=t.stock_code "
        "WHERE t.run_time=? AND u.sector_large IS NOT NULL ORDER BY u.sector_large",
        (latest,)
    ).fetchall()]

    # 기본 필터
    where = ["t.run_time = ?", "t.total_score >= ?"]
    params: list = [latest, min_score]

    if market != "ALL":
        where.append("u.market = ?"); params.append(market)
    if sector != "ALL":
        where.append("u.sector_large = ?"); params.append(sector)
    if min_mktcap > 0:
        where.append("u.market_cap >= ?"); params.append(min_mktcap)
    if max_mktcap > 0:
        where.append("u.market_cap <= ?"); params.append(max_mktcap)
    if max_per > 0:
        where.append("(u.per IS NULL OR u.per BETWEEN 0.01 AND ?)"); params.append(max_per)
    if max_pbr > 0:
        where.append("(u.pbr IS NULL OR u.pbr BETWEEN 0.01 AND ?)"); params.append(max_pbr)
    if q:
        where.append("(u.stock_name LIKE ? OR t.stock_code LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]

    where_sql = " AND ".join(where)
    rows = conn.execute(
        f"SELECT t.stock_code, t.stock_name, t.total_score, t.reasons, "
        f"t.current_price, t.market_cap, t.per, t.pbr, t.roe, "
        f"t.revenue_growth, t.op_growth, t.op_margin, "
        f"u.sector_large, u.market "
        f"FROM tenbagger_results t "
        f"JOIN stock_universe u ON u.stock_code=t.stock_code "
        f"WHERE {where_sql}",
        params
    ).fetchall()

    # 업황 YoY 캐시 (섹터별 1회만 조회)
    industry_cache: dict = {}
    for sector_key, (ind_key, _) in SECTOR_INDICATOR_MAP.items():
        # SMP 지표는 integrated 시계열로 필터링
        sn = "integrated_smp_krw_per_kwh" if ind_key == "epic:6:18" else None
        yoy = _get_industry_yoy(conn, ind_key, series_name=sn)
        industry_cache[sector_key] = yoy

    # FP 이력 집계: 종목별 선정 후 7일 이내 -5% 이하 횟수
    fp_history: dict = {}
    try:
        fp_base = conn.execute("""
            SELECT stock_code, DATE(run_time) AS sel_date, current_price
            FROM tenbagger_results
            WHERE total_score >= 50
            ORDER BY run_time
        """).fetchall()
        for fb in fp_base:
            code = fb["stock_code"]
            sel_date = fb["sel_date"]
            sel_price = fb["current_price"] or 0
            if sel_price <= 0:
                continue
            ph = conn.execute("""
                SELECT close FROM price_history
                WHERE stock_code=? AND date > ? AND close > 0
                ORDER BY date LIMIT 7
            """, (code, _to_date(sel_date))).fetchall()
            if ph:
                low_close = min(r[0] for r in ph)
                ret = (low_close - sel_price) / sel_price * 100
                if ret <= -5:
                    fp_history[code] = fp_history.get(code, 0) + 1
    except Exception:
        pass

    # 수주잔고 보너스 데이터 (최신 연도)
    backlog_map: dict = {}
    try:
        bl_rows = conn.execute("""
            SELECT ob.stock_code, ob.backlog_amount, fd.revenue
            FROM order_backlog ob
            JOIN (
                SELECT stock_code, MAX(year) AS yr FROM order_backlog GROUP BY stock_code
            ) latest ON latest.stock_code=ob.stock_code AND ob.year=latest.yr
            LEFT JOIN financial_data fd ON fd.stock_code=ob.stock_code
                AND fd.year=ob.year AND fd.is_annual=1 AND fd.revenue IS NOT NULL
            ORDER BY ob.stock_code, ob.quarter DESC
        """).fetchall()
        for r in bl_rows:
            code = r["stock_code"]
            if code in backlog_map:
                continue
            bl_amt = r["backlog_amount"] or r.get("backlog_normalized") or 0
            rev = r["revenue"] or 0
            if rev and rev > 0 and bl_amt:
                backlog_map[code] = bl_amt / rev
    except Exception:
        pass

    # 수주공시 급증 proxy 보너스 (최근 3개월 vs 직전 3개월)
    order_surge_map: dict = {}
    try:
        from signal_engine import _load_order_contract_surge_bonus_map
        order_surge_map = _load_order_contract_surge_bonus_map(window_months=3)
    except Exception:
        order_surge_map = {}

    # v3 점수 계산
    results = []
    for row in rows:
        r = dict(row)
        sec = r.get("sector_large") or ""
        # 업황 조정 (종목명 개별 매핑 우선, 없으면 섹터 매핑)
        ind_key, ind_label, yoy_pct = None, "업황데이터없음", None
        industry_adj = 0
        stock_name_val = r.get("stock_name") or ""
        name_mapping = _get_stock_name_indicator(stock_name_val)
        if name_mapping:
            ind_key_val, ind_label = name_mapping
            sn = "integrated_smp_krw_per_kwh" if ind_key_val == "epic:6:18" else None
            yoy_pct = _get_industry_yoy(conn, ind_key_val, series_name=sn)
            industry_adj = _industry_adj_score(yoy_pct)
        elif sec in SECTOR_INDICATOR_MAP:
            ind_key_val, ind_label = SECTOR_INDICATOR_MAP[sec]
            yoy_pct = industry_cache.get(sec)
            industry_adj = _industry_adj_score(yoy_pct)

        # FP 패널티
        fp_cnt = fp_history.get(r["stock_code"], 0)
        fp_penalty = -3 if fp_cnt >= 2 else 0

        # 수주보너스
        bl_ratio = backlog_map.get(r["stock_code"], 0)
        backlog_bonus = 2 if bl_ratio >= 1.5 else 0
        order_surge = order_surge_map.get(r["stock_code"])
        order_surge_bonus = min(order_surge.get("bonus", 0), 4) if order_surge else 0

        v3_score = (r["total_score"] or 0) + industry_adj + fp_penalty + backlog_bonus + order_surge_bonus

        reasons = []
        try:
            reasons = _json.loads(r["reasons"]) if r["reasons"] else []
        except Exception:
            reasons = [r["reasons"]] if r["reasons"] else []

        reasons_v3 = list(reasons)
        if industry_adj > 0:
            reasons_v3.append(f"업황호조 {ind_label}(YoY {yoy_pct:+.1f}%)" if yoy_pct is not None else f"업황호조 {ind_label}")
        elif industry_adj < 0:
            reasons_v3.append(f"업황악화 {ind_label}(YoY {yoy_pct:+.1f}%)" if yoy_pct is not None else f"업황악화 {ind_label}")
        if fp_penalty < 0:
            reasons_v3.append(f"FP이력패널티({fp_cnt}회)")
        if backlog_bonus > 0:
            reasons_v3.append(f"수주잔고보너스(backlog/rev {bl_ratio:.1f}x)")
        if order_surge_bonus > 0:
            reasons_v3.append(f"수주급증보너스({order_surge.get('label')})")

        r["v3_score"] = round(v3_score, 2)
        r["industry_adj"] = industry_adj
        r["industry_label"] = ind_label
        r["industry_yoy_pct"] = round(yoy_pct, 1) if yoy_pct is not None else None
        r["fp_penalty"] = fp_penalty
        r["fp_fp_count"] = fp_cnt
        r["backlog_bonus"] = backlog_bonus
        r["backlog_ratio"] = round(bl_ratio, 2) if bl_ratio else None
        r["order_surge_bonus"] = order_surge_bonus
        r["order_surge"] = order_surge
        r["reasons_v3"] = reasons_v3
        results.append(r)

    results = _enrich_tenbagger_price_risk(conn, results)
    for r in results:
        penalty = float(r.get("price_risk_penalty") or 0)
        r["v3_score"] = round(max(0.0, float(r.get("v3_score") or 0) - penalty), 2)
        if penalty > 0:
            flags = r.get("price_risk_flags") or []
            r["reasons_v3"] = list(r.get("reasons_v3") or []) + [f"가격위험감점 -{penalty:.0f}점" + (f" ({' / '.join(flags)})" if flags else "")]

    # v3_score 필터
    results = [r for r in results if r["v3_score"] >= min_v3_score]

    # 정렬
    sort_key = "v3_score" if sort == "v3_score" else ("total_score" if sort == "total_score" else "market_cap")
    results.sort(key=lambda x: (x.get(sort_key) or 0), reverse=(order.lower() == "desc"))

    # 페이지네이션
    total = len(results)
    offset = (page - 1) * page_size
    results_page = results[offset: offset + page_size]

    conn.close()

    # 업황지표 연동 현황 요약
    indicator_summary = {}
    for sec_key, (ik, label) in SECTOR_INDICATOR_MAP.items():
        yoy = industry_cache.get(sec_key)
        indicator_summary[sec_key] = {
            "indicator_key": ik,
            "label": label,
            "yoy_pct": round(yoy, 1) if yoy is not None else None,
            "adj_score": _industry_adj_score(yoy),
        }

    return {
        "results": results_page,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "run_time": latest,
        "sectors": sectors,
        "indicator_map": indicator_summary,
    }


# ── PBR/PER 히스토리 API ─────────────────────────────────────────────
@router.get("/valuation-history/{stock_code}")
def get_valuation_history(stock_code: str, quarters: int = Query(20, ge=4, le=40)):
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT year, quarter, period_end, close_price, eps, bps, per, pbr, market_cap_억
            FROM valuation_history
            WHERE stock_code=?
            ORDER BY year DESC, quarter DESC
            LIMIT ?
        """, (stock_code, quarters)).fetchall()
        items = [dict(r) for r in rows]
        if not items:
            return {"stock_code": stock_code, "items": [], "note": "데이터 없음"}
        # 최신 vs 5분위 비교
        pers = [r["per"] for r in items if r["per"]]
        pbrs = [r["pbr"] for r in items if r["pbr"]]
        stats = {}
        for nm, vals in [("per", pers), ("pbr", pbrs)]:
            if vals:
                svals = sorted(vals)
                n = len(svals)
                stats[nm] = {
                    "min": svals[0], "max": svals[-1],
                    "avg": round(sum(svals)/n, 2),
                    "median": svals[n//2],
                    "pct20": svals[int(n*0.2)],
                    "pct80": svals[int(n*0.8)],
                    "current": svals[-1] if svals else None,
                }
        return {"stock_code": stock_code, "items": items, "stats": stats}
    finally:
        conn.close()


@router.get("/valuation-summary")
def get_valuation_summary(
    sort: str = Query("pbr", description="per/pbr/market_cap"),
    limit: int = Query(50, le=200),
    year: int = Query(2025),
    quarter: int = Query(4),
):
    """최신 분기 PBR/PER 전종목 요약 — 역사적 분위수 포함"""
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT vh.stock_code, su.stock_name, su.sector_large, su.market,
                   vh.year, vh.quarter, vh.close_price, vh.per, vh.pbr, vh.market_cap_억,
                   -- 역사적 PBR 분위
                   (SELECT COUNT(*) FROM valuation_history vh2 WHERE vh2.stock_code=vh.stock_code AND vh2.pbr IS NOT NULL) as pbr_hist_count,
                   (SELECT ROUND(CAST(AVG(vh2.pbr) AS NUMERIC),2) FROM valuation_history vh2 WHERE vh2.stock_code=vh.stock_code AND vh2.pbr IS NOT NULL) as pbr_hist_avg
            FROM valuation_history vh
            JOIN stock_universe su ON su.stock_code=vh.stock_code
            WHERE vh.year=? AND vh.quarter=?
            AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            ORDER BY vh.{sort_col} ASC
            LIMIT ?
        """.replace("{sort_col}", "pbr" if sort=="pbr" else "per" if sort=="per" else "market_cap_억 DESC --"),
        (year, quarter, limit)).fetchall()
        return {"items": [dict(r) for r in rows], "year": year, "quarter": quarter}
    finally:
        conn.close()


# ── 세그먼트별 매출 API ───────────────────────────────────────────────
@router.get("/segments/{stock_code}")
def get_segments(stock_code: str):
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT year, quarter, segment_name, revenue, operating_profit, revenue_pct
            FROM segment_revenue
            WHERE stock_code=?
            ORDER BY year DESC, quarter DESC, revenue DESC
        """, (stock_code,)).fetchall()
        items = [dict(r) for r in rows]
        # 연도별 그룹
        by_year = {}
        for r in items:
            key = f"{r['year']}Q{r['quarter']}" if r['quarter'] else str(r['year'])
            by_year.setdefault(key, []).append(r)
        return {"stock_code": stock_code, "periods": list(by_year.keys()), "by_period": by_year}
    finally:
        conn.close()


@router.get("/data-status")
def get_data_status():
    """데이터 현황 실시간 통계"""
    cached = _DATA_STATUS_CACHE.get("data")
    if cached and time.time() - float(_DATA_STATUS_CACHE.get("at") or 0) < _DATA_STATUS_CACHE_TTL_SEC:
        return cached
    conn = _get_conn()
    try:
        def cnt(table, cond="1=1"):
            try:
                return conn.execute(f"SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM {table} WHERE {cond}").fetchone()
            except: return (0, 0)
        def one(sql, params=()):
            try:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else {}
            except Exception:
                return {}

        if IS_POSTGRES:
            program_market = {}
            program_stock = {}
        else:
            program_market = one("""
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT source || '|' || market) AS markets,
                       MIN(dt) AS min_dt,
                       MAX(dt) AS max_dt
                FROM broker_program_market_daily
            """)
            program_stock = one("""
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT stock_code) AS stocks,
                       MIN(dt) AS min_dt,
                       MAX(dt) AS max_dt
                FROM broker_program_stock_daily
            """)
        
        tables = (
            "price_history", "financial_data", "cash_flow_data", "order_backlog",
            "cost_structure", "dilution_events", "kiwoom_credit_balance",
            "dart_insider_holdings", "valuation_history", "segment_revenue",
            "kiwoom_investor_daily", "earnings_signals", "tenbagger_results",
            "triple_pattern_daily", "treasury_buyback", "investor_flow_quarterly",
            "foreign_flow_quarterly", "broker_program_market_daily",
            "broker_program_stock_daily",
        )
        table_counts = {}
        if IS_POSTGRES:
            # 화면용 현황에 대형 테이블 전체 COUNT(DISTINCT)를 수행하지 않는다.
            # ANALYZE 통계를 사용하므로 수치는 근사치지만 원본 데이터에는 영향이 없다.
            quoted_tables = ",".join(f"'{table}'" for table in tables)
            stats_rows = conn.execute(
                f"""
                SELECT st.relname,
                       GREATEST(st.n_live_tup, 0) AS row_estimate,
                       CASE
                           WHEN ps.n_distinct < 0
                               THEN ROUND(-ps.n_distinct * GREATEST(st.n_live_tup, 0))
                           WHEN ps.n_distinct IS NOT NULL THEN ps.n_distinct
                           ELSE 0
                       END AS stock_estimate
                FROM pg_stat_user_tables st
                LEFT JOIN pg_stats ps
                  ON ps.schemaname=st.schemaname
                 AND ps.tablename=st.relname
                 AND ps.attname='stock_code'
                WHERE st.relname IN ({quoted_tables})
                """
            ).fetchall()
            estimates = {
                str(row[0]): {
                    "rows": int(row[1] or 0),
                    "stocks": int(float(row[2] or 0)),
                    "approximate": True,
                }
                for row in stats_rows
            }
            table_counts = {
                table: estimates.get(
                    table, {"rows": 0, "stocks": 0, "approximate": True}
                )
                for table in tables
            }
        else:
            for table in tables:
                row = cnt(table)
                table_counts[table] = {"rows": row[0] or 0, "stocks": row[1] or 0}

        result = {
            **table_counts,
            "broker_program_market_daily": {
                "rows": program_market.get("rows", table_counts["broker_program_market_daily"]["rows"]) or 0,
                "markets": program_market.get("markets", 0) or 0,
                "min_dt": program_market.get("min_dt"),
                "max_dt": program_market.get("max_dt"),
                "approximate": IS_POSTGRES,
            },
            "broker_program_stock_daily": {
                "rows": program_stock.get("rows", table_counts["broker_program_stock_daily"]["rows"]) or 0,
                "stocks": program_stock.get("stocks", table_counts["broker_program_stock_daily"]["stocks"]) or 0,
                "min_dt": program_stock.get("min_dt"),
                "max_dt": program_stock.get("max_dt"),
                "approximate": IS_POSTGRES,
            },
        }
        _DATA_STATUS_CACHE["data"] = result
        _DATA_STATUS_CACHE["at"] = time.time()
        return result
    finally:
        conn.close()


# ── 자사주 취득/처분 이력 API ────────────────────────────────────────────────
@router.get("/treasury-buyback/{stock_code}")
def get_treasury_buyback(stock_code: str):
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT rcept_dt, event_type, report_nm, rcept_no
            FROM treasury_buyback
            WHERE stock_code=?
            ORDER BY rcept_dt DESC
            LIMIT 50
        """, (stock_code,)).fetchall()
        items = [dict(r) for r in rows]
        # Summary counts
        from collections import Counter
        type_cnt = Counter(r['event_type'] for r in items)
        # Recent 12 months buyback count
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        recent = [r for r in items if r['rcept_dt'] and r['rcept_dt'] >= cutoff]
        return {
            "stock_code": stock_code,
            "total": len(items),
            "recent_12m": len(recent),
            "type_counts": dict(type_cnt),
            "acquisitions": type_cnt.get('취득결정', 0) + type_cnt.get('취득결과', 0),
            "cancellations": type_cnt.get('소각', 0),
            "items": items
        }
    finally:
        conn.close()


# ── 자사주 취득 상위 종목 API ────────────────────────────────────────────────
@router.get("/treasury-buyback-top")
def get_treasury_buyback_top(days: int = Query(365, ge=30, le=3650), limit: int = Query(30)):
    conn = _get_conn()
    try:
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        rows = conn.execute("""
            SELECT tb.stock_code, MAX(su.stock_name) AS stock_name, MAX(su.market) AS market,
                   COUNT(*) as total_events,
                   SUM(CASE WHEN tb.event_type IN ('취득결정','취득결과') THEN 1 ELSE 0 END) as acquisitions,
                   SUM(CASE WHEN tb.event_type='소각' THEN 1 ELSE 0 END) as cancellations,
                   MAX(tb.rcept_dt) as last_event
            FROM treasury_buyback tb
            JOIN stock_universe su ON su.stock_code=tb.stock_code
            WHERE tb.rcept_dt >= ? AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            GROUP BY tb.stock_code
            ORDER BY acquisitions DESC, total_events DESC
            LIMIT ?
        """, (cutoff, limit)).fetchall()
        return {"items": [dict(r) for r in rows], "period_days": days, "cutoff": cutoff}
    finally:
        conn.close()


# ── 스코어링 가중치 최적화 / 백테스트 성능 분석 ─────────────────────────────
@router.get("/score-performance")
def get_score_performance(
    days_after: int = Query(7, ge=1, le=30),
    min_score: float = Query(55.0),
    limit: int = Query(500),
):
    """
    각 스코어 구간별 평균 수익률 분석 — 가중치 최적화 근거
    새 데이터 축(임원매매/신용잔고/외인지분율/자사주/PBR백분위) 효과 검증
    """
    import json as _json
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT stock_code, stock_name, total_score, score_detail, reasons, 
                   current_price AS price_at_select, run_time, DATE(run_time) AS select_date
            FROM tenbagger_results
            WHERE total_score >= ?
            ORDER BY run_time ASC
            LIMIT ?
        """, (min_score, limit)).fetchall()

        results = []
        score_return_buckets = {
            "55-65": [], "65-70": [], "70-75": [], "75-80": [], "80+": []
        }
        signal_returns = {
            "임원매수": [], "외국인지분": [], "자사주": [], "PBR저점": [],
            "신용잔고급감": [], "수주공시": [], "원가개선": []
        }

        for r in rows:
            code = r["stock_code"]
            sel_date = r["select_date"]
            p0 = r["price_at_select"] or 0

            ph = conn.execute("""
                SELECT close FROM price_history
                WHERE stock_code=? AND date > ? AND close > 0
                ORDER BY date LIMIT ?
            """, (code, _to_date(sel_date), days_after)).fetchall()

            p1 = ph[days_after - 1][0] if len(ph) >= days_after else None
            ret = round((p1 - p0) / p0 * 100, 2) if p0 > 0 and p1 else None

            score = float(r["total_score"] or 0)
            # 구간 분류
            if score >= 80:    bucket = "80+"
            elif score >= 75:  bucket = "75-80"
            elif score >= 70:  bucket = "70-75"
            elif score >= 65:  bucket = "65-70"
            else:              bucket = "55-65"

            if ret is not None:
                score_return_buckets[bucket].append(ret)

            # 신호별 수익률 집계
            reasons_str = r["reasons"] or "[]"
            try:
                reason_list = _json.loads(reasons_str)
            except:
                reason_list = []

            if ret is not None:
                for reason in reason_list:
                    if "임원" in reason or "CEO" in reason:
                        signal_returns["임원매수"].append(ret)
                    if "외국인 지분" in reason:
                        signal_returns["외국인지분"].append(ret)
                    if "자사주" in reason:
                        signal_returns["자사주"].append(ret)
                    if "PBR" in reason and "저점" in reason or "하위" in reason:
                        signal_returns["PBR저점"].append(ret)
                    if "신용잔고" in reason or "숏커버" in reason:
                        signal_returns["신용잔고급감"].append(ret)
                    if "수주" in reason:
                        signal_returns["수주공시"].append(ret)
                    if "원가" in reason:
                        signal_returns["원가개선"].append(ret)

            results.append({
                "stock_code": code,
                "stock_name": r["stock_name"],
                "total_score": score,
                "select_date": sel_date,
                "return_pct": ret,
                "bucket": bucket,
            })

        # 구간별 통계
        bucket_stats = {}
        for bname, rets in score_return_buckets.items():
            if rets:
                bucket_stats[bname] = {
                    "count": len(rets),
                    "avg_return": round(sum(rets) / len(rets), 2),
                    "win_rate": round(len([r for r in rets if r > 0]) / len(rets) * 100, 1),
                    "best": round(max(rets), 2),
                    "worst": round(min(rets), 2),
                }
            else:
                bucket_stats[bname] = {"count": 0}

        # 신호별 통계
        signal_stats = {}
        for sig, rets in signal_returns.items():
            if rets:
                signal_stats[sig] = {
                    "count": len(rets),
                    "avg_return": round(sum(rets) / len(rets), 2),
                    "win_rate": round(len([r for r in rets if r > 0]) / len(rets) * 100, 1),
                }

        evaluated = [r for r in results if r["return_pct"] is not None]
        return {
            "days_after": days_after,
            "total": len(results),
            "evaluated": len(evaluated),
            "overall_avg": round(sum(r["return_pct"] for r in evaluated) / len(evaluated), 2) if evaluated else 0,
            "bucket_stats": bucket_stats,
            "signal_stats": signal_stats,
            "results": results[:100],
        }
    finally:
        conn.close()


@router.get("/historical-scoreboard-v2")
def get_historical_scoreboard_v2():
    """Return the leakage-controlled, business-validated historical scoreboard."""
    if not HISTORICAL_SCOREBOARD_V2_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="historical scoreboard is not built; run scripts/research_historical_tenbagger_scoreboard_v2.py",
        )
    try:
        report = json.loads(HISTORICAL_SCOREBOARD_V2_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to read historical tenbagger scoreboard v2")
        raise HTTPException(status_code=500, detail="historical scoreboard artifact is invalid") from exc

    precision_finalists = [
        {**item, "tier": "precision_core"}
        for item in report.get("train_selected_finalists", [])
        if item.get("stable")
    ]
    coverage_finalists = [
        {**item, "tier": "coverage_watchlist"}
        for item in report.get("train_selected_coverage_finalists", [])
        if item.get("stable")
    ]
    report["stable_finalists"] = precision_finalists[:5] + coverage_finalists[:5]
    report["status"] = (
        "research_validated" if precision_finalists and coverage_finalists else "partially_validated"
    )
    report.setdefault("decision", {})["production_ready"] = False
    report["decision"]["auto_trading_allowed"] = False
    report["usage"] = (
        "과거 지속형 텐버거 로직 검증 전용입니다. 현재 종목 추천이나 매수·매도 신호로 사용하지 않습니다."
    )
    return report


@router.get("/historical-causes")
def get_historical_tenbagger_causes():
    """Return source-timed cause attribution for cleaned historical winners."""
    if not HISTORICAL_CAUSES_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="historical cause audit is not built; run scripts/research_historical_tenbagger_causes.py",
        )
    try:
        report = json.loads(HISTORICAL_CAUSES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to read historical tenbagger cause audit")
        raise HTTPException(status_code=500, detail="historical cause artifact is invalid") from exc
    report["status"] = (
        "review_required"
        if report.get("summary", {}).get("manual_review_required_stocks", 0)
        else "validated"
    )
    report["usage"] = "사업 원인이 확인된 과거 표본 검증용이며 현재 종목 추천 신호가 아닙니다."
    return report


@router.get("/historical-signal-discovery")
def get_historical_tenbagger_signal_discovery():
    """Return train/validation/holdout-tested historical signal families."""
    if not HISTORICAL_SIGNAL_DISCOVERY_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="historical signal discovery is not built",
        )
    try:
        report = json.loads(HISTORICAL_SIGNAL_DISCOVERY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Failed to read historical tenbagger signal discovery")
        raise HTTPException(status_code=500, detail="historical signal artifact is invalid") from exc
    report["status"] = report.get("conclusion", "unknown")
    report["production_ready"] = False
    report["auto_trading_allowed"] = False
    return report


@router.get("/empirical-scoreboard")
def get_empirical_scoreboard(
    min_score: float = Query(55.0),
    limit: int = Query(250000, ge=1000, le=400000),
):
    """
    과거 전체 월말 스냅샷(strategy_feature_snapshot)을 기준으로
    "어떤 필터 조합이 실제 3배/10배 종목을 잘 포착했는지" 비교하는 실증 스코어보드.

    핵심 지표:
      - 12개월 월말 종가 기준 평균수익률 / 승률 / 손익비 / profit factor
      - 24개월 최고수익 기준 2x/3x/5x/10x 포착률
    """
    import statistics as _stats

    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT snapshot_date, stock_code, stock_name, market, sector_large,
                   close_price, market_cap_억, per, pbr,
                   ret_20d, ret_60d, ret_120d,
                   dist_high_252, dist_low_252,
                   vol_ratio_20d, avg_turnover_20d_억, supply_20d_억,
                   heuristic_score, model_score_12m,
                   forward_max_ret_12m, forward_max_ret_24m, forward_max_ret_36m,
                   label_2x_12m, label_3x_12m, label_3x_24m, label_5x_24m, label_10x_24m
            FROM strategy_feature_snapshot
            WHERE heuristic_score >= ?
            ORDER BY snapshot_date ASC
            LIMIT ?
        """, (min_score, limit)).fetchall()
        if not rows:
            return {"summary": {}, "scoreboard": [], "row_count": 0}

        model_scores = sorted(float(r["model_score_12m"] or 0) for r in rows if r["model_score_12m"] is not None)
        def _quantile(values: list[float], q: float) -> float:
            if not values:
                return 0.0
            idx = min(len(values) - 1, max(0, int((len(values) - 1) * q)))
            return float(values[idx])
        model_q80 = _quantile(model_scores, 0.80)
        model_q90 = _quantile(model_scores, 0.90)

        # 12개월 종가수익률은 같은 스냅샷 테이블의 12개월 뒤 월말 종가로 계산한다.
        month_close_map: dict[tuple[str, str], float] = {}
        for r in rows:
            code = str(r["stock_code"] or "")
            period = str(r["snapshot_date"] or "")[:7]
            close = float(r["close_price"] or 0)
            if code and period and close > 0:
                month_close_map[(code, period)] = close

        def _month_plus(period: str, months: int) -> str | None:
            try:
                y = int(period[:4]); m = int(period[5:7])
            except Exception:
                return None
            total = y * 12 + (m - 1) + months
            ny, nm = divmod(total, 12)
            return f"{ny:04d}-{nm + 1:02d}"

        analyzed_rows: list[dict] = []
        for r in rows:
            code = str(r["stock_code"] or "")
            period = str(r["snapshot_date"] or "")[:7]
            close_0 = float(r["close_price"] or 0)
            period_12m = _month_plus(period, 12)
            close_12m = month_close_map.get((code, period_12m)) if period_12m else None
            ret_12m = ((float(close_12m) / close_0 - 1.0) * 100.0) if close_0 > 0 and close_12m else None
            analyzed_rows.append({
                "stock_code": code,
                "stock_name": r["stock_name"],
                "snapshot_date": r["snapshot_date"],
                "heuristic_score": float(r["heuristic_score"] or 0),
                "model_score_12m": float(r["model_score_12m"] or 0),
                "market_cap": float(r["market_cap_억"] or 0),
                "per": float(r["per"] or 0) if r["per"] is not None else None,
                "pbr": float(r["pbr"] or 0) if r["pbr"] is not None else None,
                "ret_20d": float(r["ret_20d"] or 0) if r["ret_20d"] is not None else None,
                "ret_60d": float(r["ret_60d"] or 0) if r["ret_60d"] is not None else None,
                "ret_120d": float(r["ret_120d"] or 0) if r["ret_120d"] is not None else None,
                "dist_high_252": float(r["dist_high_252"] or 0) if r["dist_high_252"] is not None else None,
                "dist_low_252": float(r["dist_low_252"] or 0) if r["dist_low_252"] is not None else None,
                "vol_ratio_20d": float(r["vol_ratio_20d"] or 0) if r["vol_ratio_20d"] is not None else None,
                "avg_turnover_20d": float(r["avg_turnover_20d_억"] or 0) if r["avg_turnover_20d_억"] is not None else None,
                "supply_20d": float(r["supply_20d_억"] or 0) if r["supply_20d_억"] is not None else None,
                "ret_12m": ret_12m,
                "peak_12m": float(r["forward_max_ret_12m"] or 0) if r["forward_max_ret_12m"] is not None else None,
                "peak_24m": float(r["forward_max_ret_24m"] or 0) if r["forward_max_ret_24m"] is not None else None,
                "peak_36m": float(r["forward_max_ret_36m"] or 0) if r["forward_max_ret_36m"] is not None else None,
                "label_2x_12m": int(r["label_2x_12m"] or 0),
                "label_3x_12m": int(r["label_3x_12m"] or 0),
                "label_3x_24m": int(r["label_3x_24m"] or 0),
                "label_5x_24m": int(r["label_5x_24m"] or 0),
                "label_10x_24m": int(r["label_10x_24m"] or 0),
            })

        filter_specs = [
            {
                "key": "heuristic_55",
                "label": "휴리스틱 55+",
                "thesis": "현재 기본 후보군 전체",
                "predicate": lambda x: x["heuristic_score"] >= 55,
            },
            {
                "key": "heuristic_65",
                "label": "휴리스틱 65+",
                "thesis": "상위 점수만 압축",
                "predicate": lambda x: x["heuristic_score"] >= 65,
            },
            {
                "key": "model_top20",
                "label": "모델 상위 20%",
                "thesis": "model_score_12m 기준 상위 20%",
                "predicate": lambda x: x["model_score_12m"] >= model_q80,
            },
            {
                "key": "model_top10",
                "label": "모델 상위 10%",
                "thesis": "model_score_12m 기준 상위 10%",
                "predicate": lambda x: x["model_score_12m"] >= model_q90,
            },
            {
                "key": "smallcap_value",
                "label": "소형 저PBR",
                "thesis": "시총 1500억 이하 + PBR 1배 이하",
                "predicate": lambda x: 0 < x["market_cap"] <= 1500 and (x["pbr"] is not None and x["pbr"] <= 1.0),
            },
            {
                "key": "deep_drawdown_small",
                "label": "초낙폭 소형주",
                "thesis": "52주 고점 대비 -70% 이하 + 시총 3000억 이하",
                "predicate": lambda x: (x["dist_high_252"] is not None and x["dist_high_252"] <= -0.70)
                and (0 < x["market_cap"] <= 3000),
            },
            {
                "key": "deep_drawdown_volume",
                "label": "초낙폭+거래량",
                "thesis": "고점 -70% 이하 + 거래량 2배+ + 거래대금 3억+",
                "predicate": lambda x: (x["dist_high_252"] is not None and x["dist_high_252"] <= -0.70)
                and (x["vol_ratio_20d"] is not None and x["vol_ratio_20d"] >= 2.0)
                and (x["avg_turnover_20d"] is not None and x["avg_turnover_20d"] >= 3.0),
            },
            {
                "key": "low_near_bottom",
                "label": "저점 근접형",
                "thesis": "52주 저점 15% 이내 + 고점대비 -50% 이하",
                "predicate": lambda x: (x["dist_low_252"] is not None and x["dist_low_252"] <= 0.15)
                and (x["dist_high_252"] is not None and x["dist_high_252"] <= -0.50),
            },
            {
                "key": "liquid_supply_reversal",
                "label": "유동성+수급반전",
                "thesis": "거래대금 5억+ + 20일 수급 양수 + 최근60일 과매도",
                "predicate": lambda x: (x["avg_turnover_20d"] is not None and x["avg_turnover_20d"] >= 5.0)
                and (x["supply_20d"] is not None and x["supply_20d"] > 0)
                and (x["ret_60d"] is not None and x["ret_60d"] <= -0.20),
            },
            {
                "key": "model_overlay_value",
                "label": "모델+가치",
                "thesis": "모델 상위20% + PBR 1.2배 이하 + 시총 1500억 이하",
                "predicate": lambda x: (x["model_score_12m"] >= model_q80)
                and (x["pbr"] is not None and x["pbr"] <= 1.2)
                and (0 < x["market_cap"] <= 1500),
            },
            {
                "key": "balanced_core",
                "label": "균형형 코어",
                "thesis": "휴리스틱65+ + 모델상위20% + 거래대금 3억+",
                "predicate": lambda x: x["heuristic_score"] >= 65
                and (x["model_score_12m"] >= model_q80)
                and (x["avg_turnover_20d"] is not None and x["avg_turnover_20d"] >= 3.0),
            },
        ]

        scoreboard: list[dict] = []
        for spec in filter_specs:
            matched = [r for r in analyzed_rows if spec["predicate"](r)]
            eval_12m = [r for r in matched if r.get("ret_12m") is not None]
            eval_24m = [r for r in matched if r.get("peak_24m") is not None and r.get("label_3x_24m") is not None]
            if len(eval_12m) < 20 or len(eval_24m) < 20:
                continue

            rets_12m = [float(r["ret_12m"]) for r in eval_12m]
            winners = [x for x in rets_12m if x > 0]
            losers = [x for x in rets_12m if x < 0]
            gross_win = sum(winners)
            gross_loss = abs(sum(losers))
            avg_win = (sum(winners) / len(winners)) if winners else 0.0
            avg_loss = (abs(sum(losers) / len(losers))) if losers else 0.0
            peak24 = [float(r["peak_24m"]) for r in eval_24m]
            x2_hits = sum(int(r["label_2x_12m"] or 0) for r in eval_12m)
            triple_hits = sum(int(r["label_3x_24m"] or 0) for r in eval_24m)
            x5_hits = sum(int(r["label_5x_24m"] or 0) for r in eval_24m)
            x10_hits = sum(int(r["label_10x_24m"] or 0) for r in eval_24m)

            win_rate = len(winners) / len(rets_12m) * 100.0
            avg_ret = sum(rets_12m) / len(rets_12m)
            median_ret = _stats.median(rets_12m) if rets_12m else 0.0
            payoff = (avg_win / avg_loss) if avg_win > 0 and avg_loss > 0 else None
            profit_factor = (gross_win / gross_loss) if gross_win > 0 and gross_loss > 0 else None
            triple_rate = triple_hits / len(eval_24m) * 100.0 if eval_24m else 0.0
            tenbagger_rate = x10_hits / len(eval_24m) * 100.0 if eval_24m else 0.0
            score = (
                triple_rate * 0.35
                + win_rate * 0.20
                + max(min(avg_ret, 120.0), -30.0) * 0.20
                + min((profit_factor or 0.0) * 12.0, 30.0) * 0.15
                + tenbagger_rate * 0.10
            )
            scoreboard.append({
                "key": spec["key"],
                "label": spec["label"],
                "thesis": spec["thesis"],
                "sample_count": len(matched),
                "evaluated_12m": len(eval_12m),
                "evaluated_24m": len(eval_24m),
                "avg_return_12m": round(avg_ret, 2),
                "median_return_12m": round(median_ret, 2),
                "win_rate_12m": round(win_rate, 1),
                "profit_factor_12m": round(profit_factor, 2) if profit_factor is not None else None,
                "payoff_ratio_12m": round(payoff, 2) if payoff is not None else None,
                "hit_rate_2x_24m": round(x2_hits / len(eval_12m) * 100.0, 1) if eval_12m else 0.0,
                "hit_rate_3x_24m": round(triple_rate, 1),
                "hit_rate_5x_24m": round(x5_hits / len(eval_24m) * 100.0, 1) if eval_24m else 0.0,
                "hit_rate_10x_24m": round(tenbagger_rate, 1),
                "empirical_score": round(score, 1),
            })

        scoreboard.sort(
            key=lambda x: (
                x.get("empirical_score") or 0,
                x.get("hit_rate_3x_24m") or 0,
                x.get("profit_factor_12m") or 0,
                x.get("avg_return_12m") or 0,
            ),
            reverse=True,
        )

        overall_eval = [r for r in analyzed_rows if r.get("ret_12m") is not None]
        overall_peak = [r for r in analyzed_rows if r.get("peak_24m") is not None]
        return {
            "summary": {
                "row_count": len(analyzed_rows),
                "evaluated_12m": len(overall_eval),
                "evaluated_24m": len(overall_peak),
                "baseline_avg_return_12m": round(
                    sum(float(r["ret_12m"]) for r in overall_eval) / len(overall_eval), 2
                ) if overall_eval else None,
                "baseline_hit_rate_3x_24m": round(
                    sum(int(r["label_3x_24m"] or 0) for r in overall_peak) / len(overall_peak) * 100.0, 1
                ) if overall_peak else None,
            },
            "metric_guide": {
                "avg_return_12m": "월말 스냅샷 기준 12개월 뒤 종가 수익률 평균",
                "win_rate_12m": "12개월 뒤 종가 수익률이 0% 초과인 비율",
                "profit_factor_12m": "12개월 이익합 / 손실합 절대값",
                "payoff_ratio_12m": "평균 이익폭 / 평균 손실폭",
                "hit_rate_3x_24m": "24개월 내 최고가 기준 3배주(label_3x_24m) 포착률",
                "hit_rate_10x_24m": "24개월 내 최고가 기준 10배주(label_10x_24m) 포착률",
            },
            "scoreboard": scoreboard,
        }
    finally:
        conn.close()


# ── 일별 알림 저장 API ────────────────────────────────────────────────

@router.get("/daily-alerts")
def get_daily_alerts(date: str = "", limit: int = 30, new_only: bool = False):
    """tenbagger_daily_alerts 조회 — 날짜별 알림 이력"""
    conn = _get_conn()
    try:
        if date:
            where = "WHERE alert_date = ?"
            params = [date]
        else:
            # 최신 날짜
            latest = conn.execute("SELECT MAX(alert_date) FROM tenbagger_daily_alerts").fetchone()
            latest_date = latest[0] if latest and latest[0] else ""
            where = "WHERE alert_date = ?"
            params = [latest_date]

        if new_only:
            where += " AND is_new=1"

        rows = conn.execute(f"""
            SELECT da.alert_date, da.stock_code, da.stock_name, da.total_score,
                   da.reasons, da.is_new, da.best_reason, da.created_at,
                   su.sector_large, su.market, su.market_cap, su.per, su.pbr, su.roe
            FROM tenbagger_daily_alerts da
            LEFT JOIN stock_universe su ON su.stock_code=da.stock_code
            {where}
            ORDER BY da.total_score DESC
            LIMIT ?
        """, params + [limit]).fetchall()

        alerts = []
        for r in rows:
            try:
                reasons = json.loads(r["reasons"]) if r["reasons"] else []
            except Exception:
                reasons = []
            alerts.append({
                "alert_date": r["alert_date"],
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "total_score": r["total_score"],
                "reasons": reasons,
                "is_new": bool(r["is_new"]),
                "best_reason": r["best_reason"] or "",
                "created_at": r["created_at"],
                "sector_large": r["sector_large"],
                "market": r["market"],
                "market_cap": r["market_cap"],
                "per": r["per"],
                "pbr": r["pbr"],
                "roe": r["roe"],
            })

        # 날짜 목록
        dates = conn.execute(
            "SELECT DISTINCT alert_date FROM tenbagger_daily_alerts ORDER BY alert_date DESC LIMIT 30"
        ).fetchall()

        return {
            "date": params[0] if params else "",
            "total": len(alerts),
            "new_count": sum(1 for a in alerts if a["is_new"]),
            "alerts": alerts,
            "available_dates": [r[0] for r in dates],
        }
    finally:
        conn.close()


@router.get("/stock-extra/{stock_code}")
def get_stock_extra_data(stock_code: str):
    """개별종목 매입재료비/재고자산/수주잔고 조회"""
    conn = _get_conn()
    try:
        # 재고자산 + 매입재료비 (dart_cost_quarterly)
        cost_rows = conn.execute("""
            SELECT fiscal_year, fiscal_quarter, material_cost_krw, inventory_assets_krw,
                   report_type
            FROM dart_cost_quarterly
            WHERE stock_code=?
            ORDER BY fiscal_year DESC, fiscal_quarter DESC
            LIMIT 20
        """, (stock_code,)).fetchall()

        # 수주잔고 (order_backlog)
        backlog_rows = conn.execute("""
            SELECT year, quarter, backlog_amount, backlog_normalized, data_source
            FROM order_backlog
            WHERE stock_code=?
            ORDER BY year DESC, quarter DESC
            LIMIT 12
        """, (stock_code,)).fetchall()

        # 연간 원재료 매입액 (dart_material_purchase)
        mat_annual = conn.execute("""
            SELECT year, material_purchase_krw, unit_label, rcept_no
            FROM dart_material_purchase
            WHERE stock_code=?
            ORDER BY year DESC
            LIMIT 6
        """, (stock_code,)).fetchall() if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dart_material_purchase'"
        ).fetchone() else []

        # 세그먼트별 매출 (segment_revenue)
        seg_rows = conn.execute("""
            SELECT year, quarter, segment_name, revenue, operating_profit, assets
            FROM segment_revenue
            WHERE stock_code=?
            ORDER BY year DESC, quarter DESC
            LIMIT 12
        """, (stock_code,)).fetchall()

        def _fmt_krw(v):
            if v is None: return None
            return round(v / 1e8, 2)  # 원 → 억원

        cost_data = []
        for r in cost_rows:
            cost_data.append({
                "year": r["fiscal_year"],
                "quarter": r["fiscal_quarter"],
                "material_cost": _fmt_krw(r["material_cost_krw"]),  # 억원
                "inventory": _fmt_krw(r["inventory_assets_krw"]),    # 억원
                "report_type": r["report_type"],
            })

        backlog_data = []
        for r in backlog_rows:
            # backlog_normalized이 NULL인 경우 backlog_amount(원)에서 억원 변환
            if r["backlog_normalized"] is not None:
                amt_억 = round(r["backlog_normalized"], 1)
            elif r["backlog_amount"] is not None:
                amt_억 = round(r["backlog_amount"] / 1e8, 1)
            else:
                amt_억 = None
            backlog_data.append({
                "year": r["year"],
                "quarter": r["quarter"],
                "backlog_amount": amt_억,   # 억원
                "data_source": r["data_source"],
            })

        seg_data = []
        for r in seg_rows:
            seg_data.append({
                "year": r["year"],
                "quarter": r["quarter"],
                "segment_name": r["segment_name"],
                "revenue": r["revenue"],         # 억원
                "operating_profit": r["operating_profit"],
                "assets": r["assets"],
            })

        mat_annual_data = [
            {
                "year": r["year"],
                "material_purchase": round(r["material_purchase_krw"] / 1e8, 1) if r["material_purchase_krw"] else None,
                "unit_label": r["unit_label"],
            }
            for r in mat_annual
        ]

        return {
            "stock_code": stock_code,
            "cost_quarterly": cost_data,
            "order_backlog": backlog_data,
            "segment_revenue": seg_data,
            "material_annual": mat_annual_data,
            "has_material_cost": any(r["material_cost"] is not None for r in cost_data) or len(mat_annual_data) > 0,
            "has_inventory": any(r["inventory"] is not None for r in cost_data),
            "has_backlog": len(backlog_data) > 0,
        }
    finally:
        conn.close()


@router.get("/stock-quality-signals/{stock_code}")
def get_stock_quality_signals(stock_code: str):
    """개별종목 수주·선수금·재고·현금전환 신호.

    These signals are explanatory evidence only. The 2026-07-26 execution
    backtest rejected using them as a direct buy-ranking overlay.
    """
    conn = _get_conn()
    try:
        def one(sql: str, params: tuple = ()):
            r = conn.execute(sql, params).fetchone()
            return dict(r) if r else None

        advance = one("""
            SELECT fiscal_year, fiscal_quarter,
                   gross_customer_funding AS advance_krw,
                   revenue AS revenue_krw,
                   gross_to_revenue_pct AS advance_to_revenue_pct,
                   gross_yoy_pct AS advance_yoy_pct,
                   signal_score,
                   signal_label, quality_flag, source_accounts_json
            FROM contract_advance_signals
            WHERE stock_code=?
            ORDER BY fiscal_year DESC, fiscal_quarter DESC
            LIMIT 1
        """, (stock_code,))

        inventory = one("""
            SELECT fiscal_year, fiscal_quarter, inventory_krw, revenue AS revenue_krw,
                   order_contracts_krw, inventory_to_revenue_pct,
                   inventory_yoy_pct, revenue_yoy_pct, signal_type,
                   signal_score, risk_score, signal_label, quality_flag
            FROM inventory_sales_signals
            WHERE stock_code=?
            ORDER BY fiscal_year DESC, fiscal_quarter DESC
            LIMIT 1
        """, (stock_code,))

        cash = one("""
            SELECT fiscal_year, fiscal_quarter,
                   operating_cf AS operating_cf_krw,
                   capex AS capex_krw,
                   free_cf AS free_cf_krw,
                   net_income AS net_income_krw,
                   trade_receivable AS receivable_krw,
                   revenue AS revenue_krw,
                   rolling4_operating_cf AS ocf_4q_krw,
                   rolling4_free_cf AS fcf_4q_krw,
                   receivable_to_revenue_pct,
                   ocf_to_net_income_pct AS ocf_to_ni_pct,
                   signal_type, signal_score, risk_score,
                   signal_label, quality_flag
            FROM cash_conversion_signals
            WHERE stock_code=?
            ORDER BY fiscal_year DESC, fiscal_quarter DESC
            LIMIT 1
        """, (stock_code,))

        orders = [
            dict(r) for r in conn.execute("""
                SELECT rcept_dt, report_nm, report_nm AS contract_title, contract_amount,
                       revenue_ratio_pct, contract_start, contract_end,
                       counterpart AS customer_name, is_termination, verified
                FROM order_contracts
                WHERE stock_code=?
                  AND is_termination=0
                ORDER BY rcept_dt DESC
                LIMIT 5
            """, (stock_code,)).fetchall()
        ]

        def _krw_억(v):
            return round(v / 1e8, 1) if v is not None else None

        for d in (advance, inventory, cash):
            if not d:
                continue
            for k in list(d.keys()):
                if k.endswith("_krw"):
                    d[k.replace("_krw", "_억")] = _krw_억(d[k])

        for o in orders:
            o["contract_amount_억"] = _krw_억(o.get("contract_amount"))

        positive = []
        caution = []
        if advance and advance.get("signal_score", 0) >= 4 and advance.get("quality_flag") == "ok":
            positive.append("선수금/계약부채 증가")
        if orders:
            latest_order = orders[0]
            if (latest_order.get("revenue_ratio_pct") or 0) >= 10:
                positive.append("최근 대형 수주공시")
        if cash and cash.get("signal_score", 0) >= 4 and cash.get("signal_type") == "cash_quality":
            positive.append("현금전환 양호")
        if inventory and inventory.get("signal_score", 0) >= 4:
            caution.append("재고 신호는 전체 검증에서 매수 알파가 약해 참고만")
        if inventory and inventory.get("risk_score", 0) >= 4:
            caution.append("재고/매출 리스크")
        if cash and cash.get("risk_score", 0) >= 4:
            caution.append("현금전환 리스크")

        return {
            "stock_code": stock_code,
            "verdict": "explanatory_only",
            "verdict_label": "후보 설명용 · 매수랭킹 미채택",
            "backtest_note": "2026-07-26 월별 실행 백테스트에서 보조랭킹은 기본 ML 랭킹보다 수익률이 낮아 매수점수로 쓰지 않습니다.",
            "positive_flags": positive,
            "caution_flags": caution,
            "contract_advance": advance,
            "inventory_sales": inventory,
            "cash_conversion": cash,
            "recent_order_contracts": orders,
        }
    finally:
        conn.close()


@router.get("/collection-coverage")
def get_collection_coverage():
    """매입재료비/재고자산/수주잔고 수집 현황 분석"""
    conn = _get_conn()
    try:
        # 재고자산 — 연도별/시장별 커버리지 (distinct stocks)
        inv_by_year = conn.execute("""
            SELECT dc.fiscal_year, su.market,
                   COUNT(DISTINCT dc.stock_code) total_stocks,
                   COUNT(DISTINCT CASE WHEN dc.inventory_assets_krw IS NOT NULL AND dc.inventory_assets_krw>0 THEN dc.stock_code END) has_inv
            FROM dart_cost_quarterly dc
            JOIN stock_universe su ON su.stock_code=dc.stock_code
            WHERE dc.fiscal_year >= 2022 AND dc.fiscal_quarter IN (1,2,3)
            GROUP BY dc.fiscal_year, su.market
            ORDER BY dc.fiscal_year DESC, su.market
        """).fetchall()

        # 수주잔고 — 연도별/시장별
        backlog_by_year = conn.execute("""
            SELECT ob.year, su.market,
                   COUNT(DISTINCT ob.stock_code) cnt
            FROM order_backlog ob
            JOIN stock_universe su ON su.stock_code=ob.stock_code
            GROUP BY ob.year, su.market
            ORDER BY ob.year DESC, su.market
        """).fetchall()

        # 매입재료비 — 연도별 (distinct stocks)
        mat_by_year = conn.execute("""
            SELECT dc.fiscal_year, su.market,
                   COUNT(DISTINCT dc.stock_code) total,
                   COUNT(DISTINCT CASE WHEN dc.material_cost_krw IS NOT NULL AND dc.material_cost_krw>0 THEN dc.stock_code END) has_mat
            FROM dart_cost_quarterly dc
            JOIN stock_universe su ON su.stock_code=dc.stock_code
            WHERE dc.fiscal_year >= 2022
            GROUP BY dc.fiscal_year, su.market
            ORDER BY dc.fiscal_year DESC, su.market
        """).fetchall()

        # 총 종목수 (기준)
        total_kospi = conn.execute(
            "SELECT COUNT(*) FROM stock_universe WHERE market IN ('유가증권','KOSPI') AND market_cap>=500"
        ).fetchone()[0]
        total_kosdaq = conn.execute(
            "SELECT COUNT(*) FROM stock_universe WHERE market IN ('코스닥','KOSDAQ') AND market_cap>=500"
        ).fetchone()[0]

        return {
            "reference_counts": {"KOSPI": total_kospi, "KOSDAQ": total_kosdaq},
            "inventory_by_year": [
                {"year": r[0], "market": r[1], "total_stocks": r[2], "has_inventory": r[3],
                 "coverage_pct": round(r[3]/r[2]*100, 1) if r[2] else 0}
                for r in inv_by_year
            ],
            "backlog_by_year": [
                {"year": r[0], "market": r[1], "count": r[2]}
                for r in backlog_by_year
            ],
            "material_cost_by_year": [
                {"year": r[0], "market": r[1], "total": r[2], "has_material": r[3],
                 "coverage_pct": round(r[3]/r[2]*100, 1) if r[2] else 0}
                for r in mat_by_year
            ],
        }
    finally:
        conn.close()


@router.get("/stock-insight/{stock_code}")
def get_stock_insight(stock_code: str):
    """개별종목 심층 인사이트: 역사적밸류/투자자수급/임원매매/CB-BW/신용잔고"""
    conn = _get_conn()
    try:
        # 1) 역사적 PBR/PER (최근 16분기)
        valuation_rows = conn.execute("""
            SELECT year, quarter, close_price, per, pbr, market_cap_억
            FROM valuation_history
            WHERE stock_code=? AND per IS NOT NULL AND pbr IS NOT NULL
            ORDER BY year DESC, quarter DESC
            LIMIT 16
        """, (stock_code,)).fetchall()

        all_pbr = conn.execute("""
            SELECT pbr FROM valuation_history WHERE stock_code=? AND pbr>0 AND pbr<100
            ORDER BY year DESC, quarter DESC LIMIT 40
        """, (stock_code,)).fetchall()
        pbr_vals = [r[0] for r in all_pbr]
        cur_pbr = pbr_vals[0] if pbr_vals else None
        pbr_percentile = None
        if cur_pbr and len(pbr_vals) >= 4:
            below = sum(1 for v in pbr_vals if v <= cur_pbr)
            pbr_percentile = round(below / len(pbr_vals) * 100, 1)

        valuation_data = []
        for r in reversed(valuation_rows):
            valuation_data.append({"year": r[0], "quarter": r[1], "close_price": r[2], "per": r[3], "pbr": r[4], "market_cap_억": r[5]})

        # 2) 분기별 투자자 수급 (최근 8분기, 백만원→억원)
        inv_flow_rows = conn.execute("""
            SELECT year, quarter, ind_net_sum, frgnr_net_sum, orgn_net_sum, trading_days
            FROM investor_flow_quarterly
            WHERE stock_code=?
            ORDER BY year DESC, quarter DESC
            LIMIT 8
        """, (stock_code,)).fetchall()
        investor_flow = []
        for r in reversed(inv_flow_rows):
            investor_flow.append({"year": r[0], "quarter": r[1], "individual": round((r[2] or 0)/100,1), "foreign": round((r[3] or 0)/100,1), "institution": round((r[4] or 0)/100,1), "trading_days": r[5]})

        # 3) 임원 매매 (최근 1년)
        insider_rows = conn.execute("""
            SELECT repror, isu_exctv_ofcps, sp_stock_lmp_cnt,
                   sp_stock_lmp_irds_cnt, change_amount, rcept_dt, is_ceo
            FROM dart_insider_holdings
            WHERE stock_code=? AND rcept_dt >= date('now','-365 days')
            ORDER BY rcept_dt DESC
            LIMIT 20
        """, (stock_code,)).fetchall()
        insider_data = []
        for r in insider_rows:
            try: chg = float(str(r[4]).replace(',','')) if r[4] else 0
            except: chg = 0
            insider_data.append({"name": r[0], "title": r[1], "current_qty": r[2], "change_qty": r[3], "change_amount": chg, "date": r[5], "is_ceo": bool(r[6]), "direction": "매수" if chg > 0 else ("매도" if chg < 0 else "기타")})

        # 4) CB/BW 희석 이벤트 (최근 3년) — 2026-07-19 사용자 지적: LIMIT 10이 너무 낮아 잦은
        # 희석 종목(예: 젬백스 082270, 3년 24건)의 절반 이상이 안 보였음. LIMIT을 30으로 올리고,
        # 표시 개수와 무관하게 실제 총 건수(1년/3년)를 별도로 반환해 "몇 건이 더 있는지" 항상 알 수 있게 함.
        dilution_count_1y = conn.execute(
            "SELECT COUNT(*) FROM dilution_events WHERE stock_code=? AND disclosed_at >= date('now','-365 days')",
            (stock_code,),
        ).fetchone()[0]
        dilution_count_3y = conn.execute(
            "SELECT COUNT(*) FROM dilution_events WHERE stock_code=? AND disclosed_at >= date('now','-1095 days')",
            (stock_code,),
        ).fetchone()[0]
        dilution_risk_count_1y = conn.execute(
            """
            SELECT COUNT(*) FROM dilution_events
            WHERE stock_code=? AND disclosed_at >= date('now','-365 days')
              AND event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
            """,
            (stock_code,),
        ).fetchone()[0]
        dilution_risk_count_3y = conn.execute(
            """
            SELECT COUNT(*) FROM dilution_events
            WHERE stock_code=? AND disclosed_at >= date('now','-1095 days')
              AND event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
            """,
            (stock_code,),
        ).fetchone()[0]
        dilution_rows = conn.execute("""
            SELECT event_type, issue_amount, dilution_pct, conversion_price, shares_to_issue,
                   current_shares, disclosed_at, report_nm, put_option_date,
                   risk_amount_status, risk_event_bucket, risk_use_note
            FROM dilution_events
            WHERE stock_code=? AND disclosed_at >= date('now','-1095 days')
            ORDER BY disclosed_at DESC
            LIMIT 30
        """, (stock_code,)).fetchall()
        outstanding_row = conn.execute("""
            SELECT COALESCE(sm.shares_outstanding, su.shares_issued)
            FROM stock_universe su
            LEFT JOIN stock_meta sm ON sm.stock_code=su.stock_code
            WHERE su.stock_code=?
            ORDER BY su.base_date DESC
            LIMIT 1
        """, (stock_code,)).fetchone()
        current_outstanding_shares = outstanding_row[0] if outstanding_row else None

        impact_labels = {
            "CB": "전환 시 신주가 발행되어 주당가치·지분율이 희석될 수 있습니다.",
            "BW": "신주인수권 행사 시 신주가 발행되어 주당가치·지분율이 희석될 수 있습니다.",
            "RIGHTS": "유상증자 신주가 상장되면 기존 주주의 지분율과 주당지표가 희석될 수 있습니다.",
            "RIGHTS_RESULT": "유상증자 발행 결과 공시입니다. 실제 상장·납입 여부를 원문으로 확인해야 합니다.",
            "BONUS": "무상증자는 주식 수만 늘고 회사 가치가 같은 비율로 조정될 수 있어 경제적 희석과 구분해야 합니다.",
            "RIGHTS_BONUS": "유무상증자는 유상증자 부분의 경제적 희석과 무상증자 부분을 구분해 해석해야 합니다.",
            "EB": "교환사채는 통상 기존 주식 교환 구조여서 신주 발행 여부를 공시 원문으로 별도 확인해야 합니다.",
        }
        dilution_data = []
        for r in dilution_rows:
            amt_억 = round(r[1]/1e8,1) if r[1] else None
            potential_shares = r[4]
            baseline_shares = r[5] or current_outstanding_shares
            estimated_pct = r[2]
            if estimated_pct is None and potential_shares and baseline_shares and r[0] != "EB":
                estimated_pct = round(float(potential_shares) / float(baseline_shares) * 100, 2)
            dilution_data.append({
                "type": r[0],
                "issue_amount_억": amt_억,
                "dilution_pct": estimated_pct,
                "conversion_price": r[3],
                "potential_shares": potential_shares,
                "baseline_shares": baseline_shares,
                "impact_note": impact_labels.get(r[0], "주식 수 및 전환·행사 조건을 공시 원문으로 확인해야 합니다."),
                "date": r[6],
                "report": r[7],
                "put_option_date": r[8],
                "risk_amount_status": r[9],
                "risk_event_bucket": r[10],
                "risk_use_note": r[11],
            })

        # 풋옵션(조기상환청구권) 유동성 리스크 — 2026-07-19 신규(사용자 사례: 에이엘티 172670
        # CB 200억, 풋옵션 개시 2026.12.13 vs 보유현금 77.5억). 다가오는 풋옵션 대비 회사가
        # 실제로 상환할 현금이 있는지 계산. put_option_date는 자동파서가 아직 대부분 미채움 —
        # 채워진 종목에 한해서만 계산되고, 없으면 has_liquidity_risk=False로 조용히 생략.
        cash_row = conn.execute("""
            SELECT cash_end FROM cash_flow_data
            WHERE stock_code=? AND cash_end IS NOT NULL
            ORDER BY year DESC, quarter DESC, (report_type='CFS') DESC LIMIT 1
        """, (stock_code,)).fetchone()
        latest_cash = cash_row[0] if cash_row else None
        liquidity_risk = None
        upcoming_puts = [r for r in dilution_rows if r[8] and r[1]]
        if upcoming_puts:
            upcoming_puts.sort(key=lambda r: r[8])
            nearest = upcoming_puts[0]
            shortfall = (nearest[1] - latest_cash) if latest_cash is not None else None
            liquidity_risk = {
                "put_option_date": nearest[8],
                "amount_억": round(nearest[1]/1e8, 1),
                "current_cash_억": round(latest_cash/1e8, 1) if latest_cash is not None else None,
                "shortfall_억": round(shortfall/1e8, 1) if shortfall is not None else None,
                "at_risk": bool(shortfall is not None and shortfall > 0),
            }

        # 자사주 (최근 1년)
        buyback_rows = conn.execute("""
            SELECT event_type, report_nm, rcept_dt FROM treasury_buyback
            WHERE stock_code=? AND rcept_dt >= date('now','-365 days')
            ORDER BY rcept_dt DESC LIMIT 10
        """, (stock_code,)).fetchall()
        buyback_data = [{"type": r[0], "report": r[1], "date": r[2]} for r in buyback_rows]

        # 5) 신용잔고 추이 (최근 60일)
        credit_rows = conn.execute("""
            SELECT dt, credit_balance_qty, credit_ratio FROM kiwoom_credit_balance
            WHERE stock_code=? ORDER BY dt DESC LIMIT 60
        """, (stock_code,)).fetchall()
        credit_data = [{"date": r[0], "qty": r[1], "ratio": r[2]} for r in reversed(credit_rows)]

        # 외국인 지분율 추이 (최근 60개)
        foreign_rows = conn.execute("""
            SELECT dt, weight, poss_stock_cnt FROM kiwoom_foreign_flow
            WHERE stock_code=? ORDER BY dt DESC LIMIT 60
        """, (stock_code,)).fetchall()
        foreign_data = [{"date": r[0], "weight": r[1], "qty": r[2]} for r in reversed(foreign_rows)]

        return {
            "stock_code": stock_code,
            "valuation": valuation_data,
            "pbr_percentile": pbr_percentile,
            "current_pbr": cur_pbr,
            "investor_flow": investor_flow,
            "insider_trading": insider_data,
            "dilution_events": dilution_data,
            "share_change_coverage": {
                "status": "event_detected" if dilution_data else "unverified",
                "current_outstanding_shares": current_outstanding_shares,
                "note": (
                    "최근 3년 DART 희석·증자 공시를 확인했습니다. 전환·행사·상환·실제 상장 여부는 각 공시별로 별도 확인이 필요합니다."
                    if dilution_data else
                    "이 종목은 현재 희석·증자 이벤트 DB에서 확인된 공시가 없습니다. '없음'이 아니라 과거 공시 전수 검증 전의 미확인 상태입니다."
                ),
            },
            "dilution_count_1y": dilution_count_1y,
            "dilution_count_3y": dilution_count_3y,
            "dilution_risk_count_1y": dilution_risk_count_1y,
            "dilution_risk_count_3y": dilution_risk_count_3y,
            "liquidity_risk": liquidity_risk,
            "buyback": buyback_data,
            "credit_balance": credit_data,
            "foreign_ownership": foreign_data,
            "has_valuation": len(valuation_data) > 0,
            "has_investor_flow": len(investor_flow) > 0,
            "has_insider": len(insider_data) > 0,
            "has_dilution": len(dilution_data) > 0,
            "has_liquidity_risk": liquidity_risk is not None,
            "has_buyback": len(buyback_data) > 0,
            "has_credit": len(credit_data) > 0,
            "has_foreign": len(foreign_data) > 0,
        }
    finally:
        conn.close()


@router.get("/liquidity-risk-scan")
def get_liquidity_risk_scan(months_ahead: int = Query(default=18)):
    """전종목 CB/BW 풋옵션(조기상환청구권) vs 보유현금 유동성 리스크 스캔 — 2026-07-19 신규.
    사용자 지시("에이엘티 CB 200억 풋옵션 vs 현금 77.5억 사실을 모든 종목에 반영") 대응.
    put_option_date는 자동파서(dilution_v2)가 아직 대부분 미채움 — 채워진 건만 표시되며,
    DART 재수집이 진행될수록 결과가 늘어나는 것이 정상(로직 결함 아님).
    """
    conn = connect_primary_db(timeout=15, row_factory=sqlite3.Row)
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        horizon = (datetime.now() + timedelta(days=months_ahead * 30)).strftime("%Y-%m-%d")
        rows = conn.execute("""
            SELECT stock_code, stock_name, event_type, issue_amount, conversion_price,
                   put_option_date, disclosed_at, report_nm
            FROM dilution_events
            WHERE put_option_date IS NOT NULL AND put_option_date BETWEEN ? AND ?
              AND issue_amount IS NOT NULL
            ORDER BY put_option_date ASC
        """, (today, horizon)).fetchall()

        name_map = dict(conn.execute("SELECT stock_code, stock_name FROM stock_universe").fetchall())
        mktcap_map = dict(conn.execute("SELECT stock_code, market_cap FROM stock_universe").fetchall())

        results = []
        for r in rows:
            code = r["stock_code"]
            cash_row = conn.execute("""
                SELECT cash_end FROM cash_flow_data
                WHERE stock_code=? AND cash_end IS NOT NULL
                ORDER BY year DESC, quarter DESC, (report_type='CFS') DESC LIMIT 1
            """, (code,)).fetchone()
            cash = cash_row[0] if cash_row else None
            shortfall = (r["issue_amount"] - cash) if cash is not None else None
            results.append({
                "stock_code": code, "stock_name": name_map.get(code, r["stock_name"] or code),
                "mktcap_억": round(mktcap_map.get(code) or 0),
                "event_type": r["event_type"],
                "put_option_date": r["put_option_date"],
                "amount_억": round(r["issue_amount"] / 1e8, 1),
                "conversion_price": r["conversion_price"],
                "current_cash_억": round(cash / 1e8, 1) if cash is not None else None,
                "shortfall_억": round(shortfall / 1e8, 1) if shortfall is not None else None,
                "at_risk": bool(shortfall is not None and shortfall > 0),
                "disclosed_at": r["disclosed_at"],
            })
        results.sort(key=lambda x: (-int(x["at_risk"]), x["put_option_date"]))

        return {
            "months_ahead": months_ahead,
            "results": results,
            "research_note": {
                "status": f"put_option_date 파싱 완료 건수: {len(rows)}건 (전체 dilution_events 대비 극히 일부 — "
                          "자동파서가 조기상환청구권 날짜를 아직 대부분 추출하지 못함)",
                "caveat": "이 스캔은 DART CB/BW 원문에서 '조기상환청구권/풋옵션' 날짜가 실제로 추출된 건만 "
                          "표시합니다. 전종목 커버리지를 위해서는 dart_dilution_collector.py 재수집이 필요하며 "
                          "DART API 일일한도 회복 후 순차 진행 예정입니다. 표시되지 않는다고 해당 종목에 "
                          "풋옵션 리스크가 없다는 뜻은 아닙니다 — 데이터 미수집 상태일 수 있습니다.",
            },
        }
    finally:
        conn.close()


# ─── 섹터별 퀀트 지표 매핑 ──────────────────────────────────────────
_SECTOR_QUANT_MAP = {
    "운수장비": {
        "keys": ["public:23:1", "public:23:2", "epic:0:2", "epic:0:4", "epic:0:14", "epic:0:55", "epic:0:57", "epic:20:usdkrw"],
        "labels": {"public:23:1": "완성차 수출", "public:23:2": "자동차부품 수출", "epic:0:2": "국내차 판매(회사별)", "epic:0:4": "시장점유율", "epic:0:14": "현대차 내수", "epic:0:55": "현대차 미국", "epic:0:57": "기아 미국", "epic:20:usdkrw": "원달러환율"},
    },
    "전기전자": {
        "keys": ["public:23:4", "public:23:5", "public:23:6", "epic:semi:dram_proxy", "epic:3:semi", "epic:20:usdkrw"],
        "labels": {"public:23:4": "메모리반도체 수출", "public:23:5": "시스템반도체 수출", "public:23:6": "반도체장비 수출", "epic:semi:dram_proxy": "DRAM 수출단가", "epic:3:semi": "반도체 수출액", "epic:20:usdkrw": "원달러환율"},
    },
    "화학": {
        "keys": ["epic:20:ppi", "epic:20:exports", "epic:20:usdkrw", "public:23:3"],
        "labels": {"epic:20:ppi": "생산자물가지수", "epic:20:exports": "수출액", "epic:20:usdkrw": "원달러환율", "public:23:3": "이차전지 수출"},
    },
    "철강금속": {
        "keys": ["epic:1:25", "epic:1:27", "epic:1:29", "epic:20:ppi_steel", "epic:17:17", "public:23:8"],
        "labels": {"epic:1:25": "중국 HRC철강가", "epic:1:27": "중국 후판가", "epic:1:29": "중국 철근가", "epic:20:ppi_steel": "국내 철강 PPI", "epic:17:17": "한국 후판가", "public:23:8": "철강 수출"},
    },
    "조선": {
        "keys": ["epic:7:14", "epic:7:15", "epic:7:16", "epic:7:17", "public:23:7", "epic:20:usdkrw"],
        "labels": {"epic:7:14": "BDI 건화물지수", "epic:7:15": "BCI 케이프사이즈", "epic:7:16": "BPI 파나막스", "epic:7:17": "BSI 수프라막스", "public:23:7": "조선 수출", "epic:20:usdkrw": "원달러환율"},
    },
    "유통업": {
        "keys": ["epic:2:93", "epic:2:94", "epic:2:95", "epic:2:97", "epic:2:98", "epic:20:101"],
        "labels": {"epic:2:93": "백화점 거래액", "epic:2:94": "대형마트 거래액", "epic:2:95": "편의점 거래액", "epic:2:97": "온라인 거래액", "epic:2:98": "온라인쇼핑 총액", "epic:20:101": "소비자심리지수"},
    },
    "섬유의복": {
        "keys": ["epic:12:5", "epic:12:6", "epic:2:93", "epic:20:101"],
        "labels": {"epic:12:5": "인터넷 의류쇼핑", "epic:12:6": "모바일 의류쇼핑", "epic:2:93": "백화점 거래액", "epic:20:101": "소비자심리지수"},
    },
    "카지노": {
        "keys": ["epic:9:13", "epic:9:18", "epic:9:20", "epic:9:22", "epic:3:70"],
        "labels": {"epic:9:13": "마카오 GGR", "epic:9:18": "파라다이스 매출", "epic:9:20": "GKL 매출", "epic:9:22": "GKL 홀드율", "epic:3:70": "방한 외래관광객"},
    },
    "음식료품": {
        "keys": ["epic:11:155", "epic:11:156", "epic:20:cpi_food", "epic:11:69"],
        "labels": {"epic:11:155": "커피/패스트푸드 소비", "epic:11:156": "외식 소비", "epic:20:cpi_food": "식료품 물가", "epic:11:69": "원재료(가다랑어) 가격"},
    },
    "의약품": {
        "keys": ["public:23:10", "epic:16:110", "epic:16:112", "epic:20:cpi"],
        "labels": {"public:23:10": "의약품 수출", "epic:16:110": "피부과 진료", "epic:16:112": "치과 진료", "epic:20:cpi": "소비자물가"},
    },
    "서비스업": {
        "keys": ["epic:3:70", "epic:3:71", "epic:22:9", "epic:20:101"],
        "labels": {"epic:3:70": "방한 관광객", "epic:3:71": "지역별 관광객", "epic:22:9": "대중교통 이용", "epic:20:101": "소비자심리지수"},
    },
    "전기가스업": {
        "keys": ["epic:6:18", "epic:4:96", "epic:20:ppi"],
        "labels": {"epic:6:18": "SMP 전력도매가", "epic:4:96": "유연탄 가격", "epic:20:ppi": "생산자물가"},
    },
}
# 기본(공통) 지표 — 모든 종목에 추가
_DEFAULT_KEYS = ["epic:20:1", "epic:20:usdkrw", "epic:20:101", "epic:20:103", "public:21:1", "epic:20:exports"]
_DEFAULT_LABELS = {
    "epic:20:1": "기준금리", "epic:20:usdkrw": "원달러환율",
    "epic:20:101": "소비자심리지수", "epic:20:103": "제조업BSI",
    "public:21:1": "KOSPI시장폭", "epic:20:exports": "수출액"
}


@router.get("/quant-context/{stock_code}")
def get_quant_context(stock_code: str, months: int = Query(12, ge=3, le=36)):
    """종목 섹터 기반 퀀트 주요지표 업황 맥락 반환 (최근 N개월 추세)"""
    conn = _get_conn()
    try:
        # 종목 정보
        info = conn.execute(
            "SELECT stock_name, sector_large FROM stock_universe WHERE stock_code=?",
            (stock_code,)
        ).fetchone()
        if not info:
            return {"error": "종목 없음"}
        stock_name, sector = info[0], info[1] or ""

        # 섹터 매핑
        sector_map = _SECTOR_QUANT_MAP.get(sector, {})
        sector_keys = sector_map.get("keys", [])
        sector_labels = sector_map.get("labels", {})

        # 전체 지표 목록 (섹터 + 기본)
        all_keys = list(dict.fromkeys(sector_keys + _DEFAULT_KEYS))  # 중복제거 순서보존
        all_labels = {**_DEFAULT_LABELS, **sector_labels}

        results = []
        for key in all_keys:
            label = all_labels.get(key, key)
            rows = conn.execute("""
                SELECT period, value, unit, source_name FROM quant_major_indicator_series
                WHERE indicator_key=?
                ORDER BY period DESC LIMIT ?
            """, (key, months)).fetchall()
            if not rows:
                continue
            rows = list(reversed(rows))  # 오래된 것→최근 순
            values = [r[1] for r in rows]
            periods = [r[0] for r in rows]
            unit = rows[-1][2] if rows else ""

            # 추세 분석
            recent = values[-1] if values else None
            prev3 = values[-4] if len(values) >= 4 else (values[0] if values else None)
            prev12 = values[0] if len(values) >= 12 else (values[0] if values else None)
            yoy_pct = round((recent - prev12) / abs(prev12) * 100, 1) if recent and prev12 and prev12 != 0 else None
            qoq_pct = round((recent - prev3) / abs(prev3) * 100, 1) if recent and prev3 and prev3 != 0 else None

            # 추세 방향
            if yoy_pct is not None:
                if yoy_pct >= 15:   trend = "급상승"
                elif yoy_pct >= 5:  trend = "상승"
                elif yoy_pct <= -15: trend = "급하락"
                elif yoy_pct <= -5:  trend = "하락"
                else:               trend = "횡보"
            else:
                trend = "데이터부족"

            results.append({
                "key": key,
                "label": label,
                "unit": unit,
                "recent_value": recent,
                "recent_period": periods[-1] if periods else None,
                "yoy_pct": yoy_pct,
                "qoq_pct": qoq_pct,
                "trend": trend,
                "series": [{"period": p, "value": v} for p, v in zip(periods, values)],
            })

        # 업황 신호 요약
        rising = [r["label"] for r in results if r["trend"] in ("급상승", "상승") and r["key"] not in _DEFAULT_KEYS[:4]]
        falling = [r["label"] for r in results if r["trend"] in ("급하락", "하락") and r["key"] not in _DEFAULT_KEYS[:4]]

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "sector": sector,
            "indicators": results,
            "summary": {
                "rising_indicators": rising,
                "falling_indicators": falling,
                "sector_tailwind": len(rising) > len(falling),
                "indicator_count": len(results),
            }
        }
    finally:
        conn.close()


@router.get("/rd-patent/{stock_code}")
def get_rd_patent_signals(stock_code: str):
    """종목별 특허/기술이전/R&D 공시 이력"""
    conn = connect_primary_db(timeout=30, row_factory=sqlite3.Row)
    try:
        rows = conn.execute("""
            SELECT signal_type, rcept_dt, report_nm, amount_krw, notes, rcept_no
            FROM dart_rd_patent_signals
            WHERE stock_code = ?
            ORDER BY rcept_dt DESC LIMIT 100
        """, (stock_code,)).fetchall()
        signals = [dict(r) for r in rows]

        # 1년 내 요약
        summary_rows = conn.execute("""
            SELECT signal_type, COUNT(*) as cnt, MAX(rcept_dt) as latest
            FROM dart_rd_patent_signals
            WHERE stock_code = ? AND rcept_dt >= date('now', '-365 days')
            GROUP BY signal_type
        """, (stock_code,)).fetchall()
        summary = {r["signal_type"]: {"cnt": r["cnt"], "latest": r["latest"]} for r in summary_rows}

        return {"stock_code": stock_code, "signals": signals, "summary_1y": summary, "total": len(signals)}
    finally:
        conn.close()


@router.get("/action-signals")
def get_action_signals_api(limit: int = 30):
    """백테스트 검증 매수/매도 신호 (2019-2024, +105%, KOSPI 5.43x)

    매수 조건:
      - 엔진 총점 ≥ 50
      - 52주 고가 대비 -30~-85% 낙폭과대 구간
      - 거래량 20일 평균 대비 ≥ 1.5x

    매도 기준 (보유종목용):
      - 손절: -20%
      - 익절: +80%
      - 기간: 240일
    """
    try:
        from tenbagger_engine import get_action_signals, BUY_PARAMS, SELL_PARAMS
        signals = get_action_signals(limit=limit)
        conn = _get_conn()
        try:
            signals = _enrich_tenbagger_price_risk(conn, [dict(s) for s in signals])
        finally:
            conn.close()
        for s in signals:
            adjusted = float(s.get("risk_adjusted_score") or s.get("total_score") or s.get("score") or 0)
            if s.get("buy_signal") and (s.get("price_risk") == "AVOID" or adjusted < 50):
                reason = f"가격위험 보정 후 기준 미달({adjusted:.0f}점)"
                s["buy_signal"] = False
                s["buy_strength"] = "관망"
                failed = list(s.get("buy_failed") or [])
                if reason not in failed:
                    failed.append(reason)
                s["buy_failed"] = failed
                reasons = list(s.get("buy_reasons") or [])
                reasons.append(f"❌ {reason}")
                s["buy_reasons"] = reasons
        signals.sort(
            key=lambda x: (
                bool(x.get("buy_signal")),
                x.get("risk_adjusted_score", x.get("score") or 0),
                x.get("score") or 0,
            ),
            reverse=True,
        )
        buy_codes = {str(s.get("stock_code") or "") for s in signals if s.get("buy_signal")}
        rebound_raw = get_recovery_candidates(days=10, drop_min=8.0, limit=max(12, min(limit, 24)))
        rebound_candidates = []
        for item in (rebound_raw or {}).get("results", []):
            code = str(item.get("stock_code") or "")
            if not code or code in buy_codes:
                continue
            rebound_candidates.append({
                **item,
                "strategy_label": "단기 반등",
                "holding_hint": "5~20거래일 스윙",
                "thesis": "낙폭 과대 후 기술적/수급 반등을 노리는 별도 전략",
            })
        return {
            "buy_signals":   [s for s in signals if s.get("buy_signal")],
            "watch_signals": [s for s in signals if not s.get("buy_signal")],
            "rebound_candidates": rebound_candidates,
            "params": {
                "buy":  BUY_PARAMS,
                "sell": SELL_PARAMS,
            },
            "backtest": {
                "period":      "2019-2024 (6년)",
                "total_ret":   "+105.3%",
                "kospi_ratio": "5.43x",
                "mdd":         "-33.7%",
            },
            "strategy_note": {
                "tenbagger": "실적/현금흐름/희석 방어를 통과한 장기 다년 보유 후보",
                "rebound": "급락 뒤 반등 탄력에 초점을 둔 단기 스윙 후보. 텐버거와 분리해서 봐야 함",
            },
            "total": len(signals),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell-check")
def check_sell_signal_api(body: dict):
    """보유 종목 매도 조건 체크

    body: {stock_code, entry_price, current_price, entry_date}
    """
    try:
        from tenbagger_engine import check_sell_signal

        code         = body.get("stock_code", "")
        entry_price  = body.get("entry_price")
        entry_date   = body.get("entry_date")

        # 현재가 조회 (body에 없으면 DB)
        current_price = body.get("current_price")
        if not current_price:
            import sqlite3 as _sl
            _conn = connect_primary_db(timeout=30, row_factory=_sl.Row)
            row = _conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            _conn.close()
            if row:
                current_price = row[0]

        if not entry_price or not current_price:
            raise HTTPException(status_code=400, detail="entry_price / current_price 필요")

        result = check_sell_signal(entry_price, current_price, entry_date)
        return {"stock_code": code, "entry_price": entry_price,
                "current_price": current_price, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════
# 턴어라운드 후보 추적 (2026-07-18 신규) — "실험 로드맵" 탭 전용
#
# 배경: scratch/turnaround_leading_signal_research.py의 walk-forward 검증
# (학습≤2022/검증2023+, 적자모집단 43,036건) 결과 요약:
#   - 강한 사전예측 리딩시그널은 아직 미발견
#   - 매출YoY>0만 학습·검증 양쪽 방향 일치(lift ~1.07~1.08x, 약하지만 유일하게 안정)
#   - 매출총이익률 개선은 부호 불안정(노이즈) / 임원매수·적자축소는 일관된 음의 예측력(반직관적)
#   - 기존 확정(래깅) 흑자전환 신호에 매출YoY필터 추가 시 오히려 악화(+79.9%→+71.5%, 연속운용)
# 이 탭은 "정답을 아는 척"하지 않고, 위 실증에 기반해 두 그룹을 정직하게 분리 표시한다:
#   A) 최근 확정된 흑자전환(래깅, 신뢰도 높음) — 추적 관찰용
#   B) 현재 적자이나 약한 리딩시그널(매출YoY>0)이 있는 후보 — 참고용, 예측력 약함을 명시
# ══════════════════════════════════════════════════════════════════

def _ta_avail_date(year: int, quarter: int) -> str:
    if quarter == 1: return f"{year}-05-15"
    if quarter == 2: return f"{year}-08-15"
    if quarter == 3: return f"{year}-11-15"
    return f"{year+1}-02-15"


def _compute_turnaround_watch(min_mktcap: float = 300.0) -> dict:
    """턴어라운드 추적 — 확정된 흑자전환(A) + 단일분기 흑자전환/TTM적자지속(C, 강한 리딩시그널)
    + 약한 리딩시그널 적자후보(B) + 재도전 턴어라운드(D, 2026-07-19 신규, 최강 리딩시그널).
    순수 로직(AI 미사용).

    ⚠️ 전종목 financial_data/cash_flow_data/dart_contracts 등을 매번 풀스캔하는 무거운 연산
    (수 분 소요, 서버 CPU 점유) — 매 요청 재계산하지 않고 새벽 유휴시간(스케줄러 04:40)에
    사전계산해 TURNAROUND_WATCH_CACHE_PATH에 저장한 결과만 API가 서빙한다
    (2026-07-26, 사용자 지시: "많이 무겁다면 매번 하면 안될거 같은데,, 새벽에 cpu가 한가할때만
    돌도록 하세요"). 아래는 실제 계산 본체 — 캐시 갱신 함수(refresh_turnaround_watch_cache)와
    최초기동 폴백(get_turnaround_watch)에서만 호출한다.
    """
    conn = connect_primary_db(timeout=15, row_factory=sqlite3.Row)
    try:
        # CFS/OFS 혼재 방지 (CLAUDE.md 알려진 함정): 종목별 stock_collection_config.
        # preferred_report_type 존중(사용자 지시 2026-07-18 — 지주사 등 197종목은 이미
        # OFS가 "교정된" 선택으로 기록돼 있었음), 없으면 CFS 기본값(K-IFRS 연결 우선 원칙).
        overrides = {r["stock_code"]: r["config_value"] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, net_income, revenue
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL
            ORDER BY stock_code, year, quarter
        """).fetchall()
        # 종목별 선호 report_type 우선, 해당 분기에 없으면 다른 타입으로 폴백(공백 방지).
        # ⚠️ 순이익은 override 존중(지주사 등 net_income 교정 목적)하되, 매출은 항상 CFS 우선.
        # 이유(실측 재발견): 순수 지주사(DL 등)는 OFS 매출이 본사관리수익 수준(300억대)뿐이라
        # 실제 사업 매출이 아니며 분기별로 크게 요동쳐(YoY -98%) 리딩시그널로 무의미함 —
        # 반면 CFS 매출(1.2~1.4조원, 안정적)이 실제 그룹 영업활동을 반영함.
        by_quarter: dict[tuple, dict] = {}
        for r in raw_rows:
            key = (r["stock_code"], r["year"], r["quarter"])
            by_quarter.setdefault(key, {})[r["report_type"]] = r
        panel: dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            r_ni = variants.get(pref) or next(iter(variants.values()))
            r_rev = variants.get("CFS") or r_ni
            panel.setdefault(code, []).append((y, q, r_ni["net_income"], r_rev["revenue"]))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        name_map = dict(conn.execute(
            "SELECT stock_code, stock_name FROM stock_universe").fetchall())
        mktcap_map = dict(conn.execute(
            "SELECT stock_code, market_cap FROM stock_universe").fetchall())

        # 임원매수 (참고정보로만 표시 — 리딩시그널 연구에서 음의 예측력으로 판정됨)
        insider_recent: dict[str, str] = {}
        cutoff180 = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        for r in conn.execute("""
            SELECT stock_code, MAX(rcept_dt) d FROM dart_insider_holdings
            WHERE COALESCE(sp_stock_lmp_irds_cnt,0) > 0 AND rcept_dt >= ?
            GROUP BY stock_code
        """, (cutoff180,)).fetchall():
            insider_recent[r["stock_code"]] = r["d"]

        # "꿈(내러티브)" 촉매 — 특허/기술이전/R&D계약/라이선스 공시(2026-07-19 walk-forward
        # 검증됨, scratch/turnaround_dream_catalyst_test.py). 중앙값은 개선 없음(오히려 낮거나
        # 비슷)이나 12개월 forward 50%+ 급등 달성률이 확정흑자전환 기준 검증기간 14.3%→25.0%로
        # 유의하게 높아짐(학습기간도 동일 방향, 재현됨) — "대부분은 그대로지만 터지면 크게 터지는"
        # 전형적 꿈/테마주 분포. 수주잔고서지(order_backlog YoY)는 train/test 부호가 뒤집혀
        # (학습 -13.7%/검증 +17.7%) 기각 — 채택하지 않음.
        patent_events: dict[str, list[str]] = {}
        for r in conn.execute("SELECT stock_code, rcept_dt FROM dart_rd_patent_signals"):
            patent_events.setdefault(r["stock_code"], []).append(r["rcept_dt"])
        for c in patent_events:
            patent_events[c].sort()

        def _dream_catalyst(code: str, avail_date: str) -> bool:
            evs = patent_events.get(code)
            if not evs:
                return False
            cutoff = (datetime.strptime(avail_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return any(cutoff <= d <= avail_date for d in evs)

        # "감가상각 주도 적자" (회계상 적자 vs 실질 현금흐름) — 사용자 사례 에이엘티(172670)
        # 재검증(2026-07-19, scratch/depreciation_driven_loss_test.py). 매출원가 문제로 인한
        # 실질 적자와, 감가상각(비현금성 비용)이 커서 회계상으로만 적자로 보이는 경우를 구분.
        # 실측: 에이엘티 2025 Q1~Q3는 순이익 -40.3/-45.0/-38.0억이지만 분기 감가상각비가
        # 82.2/59.7/53.8억으로 더 커서 NI+감가상각(EBITDA근사)이 매분기 플러스, 영업현금흐름도
        # 전분기 플러스(20~123억) — "회계상 적자, 실질 현금흐름은 건전"인 전형적 자본집약 사례.
        # walk-forward 검증: 감가상각주도(NI+분기Dep>0) 단독 lift 1.31x(학습)/1.43x(검증),
        # 영업현금흐름>0 단독 1.25x/1.38x, **둘다 충족 시 1.40x(학습)/1.67x(검증, 세션 최강)**.
        cf_map: dict[tuple, dict] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter, report_type, depreciation_q, operating_cf_q,
                   financing_cf_q, capex_q
            FROM cash_flow_data WHERE is_annual=0
        """):
            key = (r["stock_code"], r["year"], r["quarter"])
            cf_map.setdefault(key, {})[r["report_type"]] = r

        def _quality_of_loss(code: str, y: int, q: int, ni):
            variants = cf_map.get((code, y, q))
            if not variants:
                return None, None, False, False
            r = variants.get("CFS") or next(iter(variants.values()))
            dep_q, ocf_q = r["depreciation_q"], r["operating_cf_q"]
            dep_driven = bool(dep_q is not None and ni is not None and (ni + dep_q) > 0)
            cash_positive = bool(ocf_q is not None and ocf_q > 0)
            return dep_q, ocf_q, dep_driven, cash_positive

        # "좋은 부채 vs 나쁜 부채" — 2026-07-28 신규(사용자 지시: "매출과 이익을 예측하고, 좋은
        # 부채, 이익의 질을 보여주는게 중요해" + 기존 why_score는 "크게 의미는 없어 보여" 지적).
        # walk-forward 검증(scratch/debt_quality_v2_test_20260728.py, TTM<=0 population, 학습<=2023/
        # 검증2024~): financing 조달(financing_cf_q>0)이 있었던 분기에서 capex_q(성장투자, 절대값)가
        # 영업현금소진액(-operating_cf_q, 양수화)보다 크면 "성장투자형"(좋은부채), 작으면 "생존형"
        # (나쁜부채, 운영손실을 메우기 위한 조달)으로 분류. 검증 결과 학습/검증 양쪽에서 방향 일치:
        # 성장투자형 avg12=3.58%/17.34%(loss30 26.5%/32.0%) vs 생존형 -1.01%/6.66%(loss30 30.2%/40.2%)
        # — 생존형은 avg수익률도 낮고 -30%이하 손실확률도 8~10%p 더 높음(재현성 확인, 채택).
        # 참고: 1차 정의(조달액 대비 capex 비중)는 학습/검증 방향이 뒤집혀 기각됨 — 반드시 이
        # v2 정의(영업현금소진 대비 capex)만 사용할 것, 1차 정의로 되돌리지 말 것.
        def _debt_quality(code: str, qs: list, i: int):
            raised_any = False
            good_hits = 0
            bad_hits = 0
            for j in range(max(0, i - 3), i + 1):
                yj, qj = qs[j][0], qs[j][1]
                variants = cf_map.get((code, yj, qj))
                if not variants:
                    continue
                r = variants.get("CFS") or next(iter(variants.values()))
                fin_cf, capex, ocf = r["financing_cf_q"], r["capex_q"], r["operating_cf_q"]
                if fin_cf is None or fin_cf <= 0:
                    continue
                raised_any = True
                if capex is None or ocf is None:
                    continue
                ocf_burn = max(0.0, -ocf)
                if abs(capex) >= ocf_burn:
                    good_hits += 1
                else:
                    bad_hits += 1
            if not raised_any:
                return "조달없음"
            if good_hits == 0 and bad_hits == 0:
                return "조달있음(데이터부족)"
            return "성장투자형" if good_hits >= bad_hits else "생존형"

        # 희석위험(CB/BW/EB/RIGHTS) — 2026-07-19 사용자 지적 계기(젬백스 082270가 재도전+매출
        # 성장+이익의질 3개 신호를 모두 충족해 종합스코어 상위에 뜨지만, 실제로는 2025-08~2026-05
        # 사이 거의 매달 CB/BW/유상증자 공시가 이어진 전형적 희석 스파이럴 종목(고점대비 -84%)임을
        # 지적받음. walk-forward 재검증(scratch/dilution_risk_test.py, 적자모집단 n_train=10372/
        # n_test=5432): 트레일링365일 희석공시 건수가 늘어날수록 TTM흑자전환율·12개월 forward
        # 주가수익률이 모두 단조 악화 — 0건 lift 1.03x/1.07x(중앙값-12.2%/-15.5%, -30%이하 29%/31%)
        # → 4건+(젬백스 수준) lift 0.90x/0.64x(중앙값-25.9%/-29.1%, **-30%이하 42.6%/49.3%** —
        # 거의 절반이 1년 내 -30% 이상 하락). 재무 신호가 좋아도 희석이 잦으면 실제 성과는 반대로
        # 나쁨 — 발굴 랭킹에서 반드시 별도 경고로 노출해야 함(점수에 섞지 않고 분리 표시).
        dilution_map: dict[str, list[str]] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
        """):
            if r["disclosed_at"]:
                dilution_map.setdefault(r["stock_code"], []).append(r["disclosed_at"][:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _dilution_risk(code: str, avail_date: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(avail_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= avail_date)

        confirmed, candidates, quarterly_flips, reattempts, quality_loss, comprehensive = [], [], [], [], [], []
        today = datetime.now().strftime("%Y-%m-%d")
        cutoff_recent = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")

        for code, qs in panel.items():
            mc = mktcap_map.get(code) or 0
            if mc < min_mktcap:
                continue
            n = len(qs)
            if n < 8:
                continue
            i = n - 1
            y, q, ni, rev = qs[i]
            avail = _ta_avail_date(y, q)
            if avail > today:
                # 최신 공시가 미래 avail_date로 추정되는 경우 그 이전 분기로 조정
                continue
            ttm_now = sum(x[2] or 0 for x in qs[max(0, i-3):i+1])
            had_loss_recent = any((x[2] or 0) < 0 for x in qs[max(0, i-3):i])

            def _rev_yoy():
                if i-4 >= 0 and qs[i-4][3] and qs[i-4][3] >= 1e9 and rev:
                    raw = (rev / qs[i-4][3] - 1) * 100
                    return round(raw, 1) if abs(raw) <= 500 else None
                return None

            # A) 확정 흑자전환: 최신분기 TTM>0 이지만 그 직전 1~3분기 중 적자가 있었음, 확정일 최근 200일 이내
            if ttm_now > 0 and had_loss_recent and avail >= cutoff_recent:
                rev_yoy = _rev_yoy()
                confirmed.append({
                    "stock_code": code, "stock_name": name_map.get(code, code),
                    "mktcap_억": round(mc), "confirmed_date": avail,
                    "year": y, "quarter": q, "ttm_net_income": round(ttm_now),
                    "revenue_yoy_pct": rev_yoy,
                    "revenue_growth_at_confirm": bool(rev_yoy is not None and rev_yoy > 0),
                    "dream_catalyst": _dream_catalyst(code, avail),
                })
            elif ttm_now <= 0:
                # C) 단일분기 흑자전환, TTM은 아직 적자 (2026-07-18 신규 — walk-forward 검증된
                # 강한 리딩시그널: lift 1.33x(학습)/1.70x(검증), 사용자 사례 에이엘티(172670)
                # 2025Q4가 정확히 이 패턴 — 기존 A)TTM기준 확정 로직은 이런 경우를 놓쳤음.
                # ⚠️ 바로 다음분기도 흑자 유지되는 비율은 47.6~54.6%뿐(에이엘티도 즉시 재적자
                # 전환) — "단일분기 반전"은 노이즈가 크지만, 그럼에도 1년 내 진짜 흑자전환
                # 확률을 유의하게 높이는 것으로 검증됨.
                if ni is not None and ni > 0:
                    rev_yoy = _rev_yoy()
                    quarterly_flips.append({
                        "stock_code": code, "stock_name": name_map.get(code, code),
                        "mktcap_억": round(mc), "as_of": avail,
                        "year": y, "quarter": q,
                        "quarter_net_income": round(ni),
                        "ttm_net_income": round(ttm_now),
                        "revenue_yoy_pct": rev_yoy,
                        "insider_buy_recent": insider_recent.get(code),
                        "dream_catalyst": _dream_catalyst(code, avail),
                    })
                # B) 적자 지속 중이나 매출YoY 성장(약한 리딩시그널, lift 1.07~1.08x)
                else:
                    rev_yoy = _rev_yoy()
                    dilution_risk = _dilution_risk(code, avail)

                    # D) 재도전 턴어라운드 계산을 B)보다 먼저 끌어올림(2026-07-27) — "왜 이
                    # 종목이 흑자전환 가능한가"를 설명하는 근거(재도전 이력/이익의 질)를 B)
                    # 후보에도 그대로 붙이기 위해 순서 변경. 로직 자체는 변경 없음.
                    last_flip_q = None
                    for j in range(i - 1, max(-1, i - 5), -1):
                        if (qs[j][2] or 0) > 0:
                            last_flip_q = f"{qs[j][0]}Q{qs[j][1]}"
                            break
                    # E) 감가상각 주도 적자(이익의 질)도 B) 전에 먼저 계산 — 아래 참조.
                    dep_q, ocf_q, dep_driven, cash_positive = _quality_of_loss(code, y, q, ni)
                    dream = _dream_catalyst(code, avail)

                    if rev_yoy is not None and rev_yoy > 0:
                        # 2026-07-26 신규: 사용자 관심(에이엘티/에이팩트류 "숨은 진주") 대응 —
                        # strategy_feature_snapshot 15.5만행 walk-forward 재검증 결과, 이 population을
                        # 시총<1000억 + 매출YoY+20%↑로 좁히면 forward 12개월 3배(3x)율이 학습11.15%/
                        # 검증12.10%로 전체평균(6.75%/7.71%)의 약 1.5~1.7배·검증기간에 더 강화(재현성
                        # 확인) — 반대로 "시총작음+이미흑자+성장"은 오히려 8.13%/5.28%로 더 약해서,
                        # "아직 적자인 소형 고성장주"가 핵심임(scratch/smallcap_earnings_accel_test_
                        # 20260726.py). 희석위험으로 이 그룹을 추가 필터링해봤으나(scratch/smallcap_
                        # dilution_risk_test_20260726.py) forward_max_ret 라벨 특성상(12개월 중 최고점
                        # 기준이라 대부분 한번은 반등해 loss30 자체가 낮아 방향이 불안정) 유의미한
                        # 리스크감소 효과를 확인 못함 — 배제하지 않고 참고정보(dilution_risk)로만 유지.
                        small_cap_high_growth = bool(mc < 1000 and rev_yoy >= 20)
                        # 2026-07-27 신규 — 사용자 지적: "에이엘티는 정밀분석으로 믿을만하다고
                        # 판단했는데, 나머지 종목은 왜 흑자전환이 가능한지 알아낼 방법이 없나?"
                        # → 에이엘티를 신뢰하게 만든 근거(재도전 이력 + 매출확인 + 회계상적자 vs
                        # 실질현금흐름)가 이미 D/E 섹션에 계산돼 있었으나 B) 후보에는 붙어있지
                        # 않았던 게 원인 — 동일 근거 필드를 여기에도 부여해 "왜"를 설명.
                        # ⚠️ 2026-07-28: 여기서 붙였던 why_score(재도전+이익의질+꿈촉매 단순합산)는
                        # 사용자 지적대로 폐기 — dream_catalyst는 "확률"이 아닌 "터지면 크게 터지는
                        # 폭"(fat-tail) 신호라 확률성 점수에 합산하면 안 된다는 원칙(F섹션에서 이미
                        # 확립된 원칙)을 B)에서는 어겼던 것이 근본 원인. 대신 walk-forward로 새로
                        # 검증된 debt_quality(좋은부채/나쁜부채)를 정렬 기준으로 사용.
                        debt_quality = _debt_quality(code, qs, i)
                        candidates.append({
                            "stock_code": code, "stock_name": name_map.get(code, code),
                            "mktcap_억": round(mc), "as_of": avail,
                            "year": y, "quarter": q, "ttm_net_income": round(ttm_now),
                            "revenue_yoy_pct": rev_yoy,
                            "insider_buy_recent": insider_recent.get(code),
                            "dilution_risk": dilution_risk,
                            "small_cap_high_growth": small_cap_high_growth,
                            "has_reattempt": bool(last_flip_q),
                            "last_flip_quarter": last_flip_q,
                            "depreciation_q": round(dep_q) if dep_q is not None else None,
                            "ni_plus_depreciation": round(ni + dep_q) if (ni is not None and dep_q is not None) else None,
                            "operating_cf_q": round(ocf_q) if ocf_q is not None else None,
                            "dep_driven": dep_driven,
                            "cash_positive": cash_positive,
                            "dream_catalyst": dream,
                            "debt_quality": debt_quality,
                        })

                    # D) 재도전 턴어라운드 (2026-07-19 신규 — 사용자 사례 에이엘티(172670)가 정확히
                    # 이 패턴: 25Q4 단일분기 흑자 후 26Q1 다시 적자, 그러나 매출은 계속 성장 중).
                    # walk-forward 검증: "최근 4분기 내 단일분기 흑자(재도전) 이력"만으로도
                    # lift 1.13x(학습)/1.22x(검증), 매출YoY성장까지 결합 시 1.18x(학습)/1.31x(검증)
                    # — 이번 세션에서 검증한 신호 중 가장 강함(검증기간에 더 강해짐, 재현성 확인).
                    # TTM 레벨 "적자폭 축소"는 음의 예측력이었지만, 이건 "이미 한번 흑자를 낸 적
                    # 있는 회사가 다시 흑자권에 접근 중"이라는 질적으로 다른(더 구체적인) 신호.
                    if last_flip_q:
                        reattempts.append({
                            "stock_code": code, "stock_name": name_map.get(code, code),
                            "mktcap_억": round(mc), "as_of": avail,
                            "year": y, "quarter": q,
                            "quarter_net_income": round(ni) if ni is not None else None,
                            "ttm_net_income": round(ttm_now),
                            "last_flip_quarter": last_flip_q,
                            "revenue_yoy_pct": rev_yoy,
                            "revenue_confirmed": bool(rev_yoy is not None and rev_yoy > 0),
                            "dream_catalyst": dream,
                            "dilution_risk": dilution_risk,
                        })

                    # E) 감가상각 주도 적자(이익의 질) — 2026-07-19 신규, 사용자 지시(에이엘티
                    # 사례: 매출원가 문제가 아니라 감가상각비가 커서 회계상으로만 적자로 보임).
                    # walk-forward 검증: 감가상각주도(NI+분기Dep>0) 단독 1.31x/1.43x, 영업현금흐름
                    # >0 단독 1.25x/1.38x, 둘다 충족 시 1.40x(학습)/1.67x(검증) — 세션 전체 최강.
                    # (dep_q/ocf_q/dep_driven/cash_positive는 B) 처리 전에 이미 계산됨 — 재사용)
                    if dep_driven or cash_positive:
                        quality_loss.append({
                            "stock_code": code, "stock_name": name_map.get(code, code),
                            "mktcap_억": round(mc), "as_of": avail,
                            "year": y, "quarter": q,
                            "quarter_net_income": round(ni) if ni is not None else None,
                            "depreciation_q": round(dep_q) if dep_q is not None else None,
                            "ni_plus_depreciation": round(ni + dep_q) if (ni is not None and dep_q is not None) else None,
                            "operating_cf_q": round(ocf_q) if ocf_q is not None else None,
                            "dep_driven": dep_driven,
                            "cash_positive": cash_positive,
                            "both_confirmed": bool(dep_driven and cash_positive),
                            "revenue_yoy_pct": rev_yoy,
                            "dream_catalyst": dream,
                            "dilution_risk": dilution_risk,
                        })

                    # F) 종합 턴어라운드 스코어 (2026-07-19 신규 — 사용자 지시: "재도전이 아니라
                    # 감가상각비 등을 고려한 전체적인 분위기가 턴어라운드 가능한 종목을 발굴하는
                    # 탭"). B/D/E 3개 독립신호를 하나로 합쳐 재랭킹 — walk-forward 검증 결과
                    # 신호 개수와 흑자전환율이 깔끔한 단조증가(monotonic) 관계: 0개=0.52~0.59x
                    # (기준율보다 낮음)/1개=0.79~0.91x/2개=1.13~1.23x/**3개 전부=1.38x(학습)/
                    # 1.63x(검증, 검증기간에 더 강화)** — 신호들이 서로 겹치지 않고 독립적으로
                    # 누적됨을 실증. dream_catalyst는 흑자전환 확률이 아니라 상승폭(fat-tail)
                    # 신호라 스코어에는 포함하지 않고 별도 배지로만 표시.
                    ta_score = int(bool(last_flip_q)) + int(bool(rev_yoy is not None and rev_yoy > 0)) + int(dep_driven or cash_positive)
                    if ta_score >= 1:
                        _has_rev_growth = bool(rev_yoy is not None and rev_yoy > 0)
                        _has_quality = bool(dep_driven or cash_positive)
                        comprehensive.append({
                            "stock_code": code, "stock_name": name_map.get(code, code),
                            "mktcap_억": round(mc), "as_of": avail,
                            "year": y, "quarter": q,
                            "quarter_net_income": round(ni) if ni is not None else None,
                            "ttm_net_income": round(ttm_now),
                            "score": ta_score,
                            # 2026-07-24 신규: 로지스틱 예측확률(발굴 전용, 매매엔진 미연결) —
                            # 동일 3개 이진신호를 검증된 가중치(재도전0.83>이익의질0.53>매출0.22)로
                            # 결합, 검증기 상위20% lift=2.24x(기존 3점버킷 lift=1.60x보다 개선).
                            "predicted_probability_pct": _turnaround_probability(last_flip_q, _has_rev_growth, _has_quality),
                            "has_reattempt": bool(last_flip_q),
                            "last_flip_quarter": last_flip_q,
                            "has_revenue_growth": bool(rev_yoy is not None and rev_yoy > 0),
                            "revenue_yoy_pct": rev_yoy,
                            "has_quality_loss": bool(dep_driven or cash_positive),
                            "depreciation_q": round(dep_q) if dep_q is not None else None,
                            "ni_plus_depreciation": round(ni + dep_q) if (ni is not None and dep_q is not None) else None,
                            "operating_cf_q": round(ocf_q) if ocf_q is not None else None,
                            "dream_catalyst": dream,
                            "dilution_risk": dilution_risk,
                        })

        # G) 수주모멘텀 — 계약기간 정규화 + 계약부채(선수금) 교차확인 (2026-07-25 신규,
        # 사용자 지시: 아이크래프트(052460) 사례 계기). dart_contracts의 원시 contract_ratio_pct는
        # 6개월 계약과 5년 계약을 동일 취급 — 계약기간(분기수)으로 나눠 근시일 매출임팩트를 정밀화.
        # ⚠️ 국내(is_overseas=0) 계약 단독은 홀드아웃 검증 결과 해외계약보다 신호품질이 뚜렷이 낮음
        # (scratch/claude_domestic_contract_signal_20260725.py: 검증기 25.4%/MDD-42.7% vs
        # 해외전용 154.3%/MDD-27.9%, is_overseas=1 필터는 실측 근거 있는 설계였음을 확인) —
        # 이 탭은 매매전략이 아니라 발굴 도구이므로 국내계약을 배제하지 않되 is_overseas로 구분 표시.
        # Codex 신규 contract_advance_signals(계약부채/선수금, IFRS15 실제 고객선수금 근거)와
        # 교차확인되면 "공시(예고)+계약부채(실제 현금 확인)" 이중검증이라 신뢰도가 훨씬 높아짐 —
        # 단 현재 collect_dart_report_items.py가 시가총액 상위 종목 위주로만 수집돼 있어(63종목
        # 한정, 2026-07-25 기준) 커버리지가 좁음을 정직하게 명시.
        advance_map: dict[str, dict] = {}
        for r in conn.execute("""
            SELECT stock_code, fiscal_year, fiscal_quarter, fs_div, gross_to_revenue_pct,
                   gross_qoq_pct, gross_yoy_pct, signal_score, signal_label
            FROM contract_advance_signals WHERE quality_flag='ok'
        """).fetchall():
            rank = (r["fiscal_year"], r["fiscal_quarter"], 1 if r["fs_div"] == "CFS" else 0)
            cur = advance_map.get(r["stock_code"])
            if cur is None or rank > cur["_rank"]:
                advance_map[r["stock_code"]] = {**dict(r), "_rank": rank}

        def _quarters_between(start_s, end_s):
            try:
                sd = datetime.strptime(start_s, "%Y-%m-%d")
                ed = datetime.strptime(end_s, "%Y-%m-%d")
            except Exception:
                return None
            days = (ed - sd).days
            if days <= 0:
                return None
            return max(days / 91.25, 0.25)  # 최소 0.25분기 하한 — 초단기계약 과대추정 방지

        cutoff_contract_ymd = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
        contract_by_code: dict[str, list[dict]] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at, contract_amount_krw, contract_ratio_pct,
                   contract_start, contract_end, is_overseas, report_nm
            FROM dart_contracts
            WHERE disclosed_at >= ? AND contract_ratio_pct >= 10
            ORDER BY disclosed_at DESC
        """, (cutoff_contract_ymd,)).fetchall():
            code = r["stock_code"]
            if (mktcap_map.get(code) or 0) < min_mktcap:
                continue
            dur_q = _quarters_between(r["contract_start"], r["contract_end"]) if r["contract_start"] and r["contract_end"] else None
            q_impact = round(r["contract_ratio_pct"] / dur_q, 2) if dur_q else None
            contract_by_code.setdefault(code, []).append({
                "disclosed_at": r["disclosed_at"], "amount_억": round((r["contract_amount_krw"] or 0) / 1e8),
                "ratio_pct": r["contract_ratio_pct"],
                "contract_start": r["contract_start"], "contract_end": r["contract_end"],
                "duration_quarters": round(dur_q, 1) if dur_q else None,
                "quarterly_impact_pct": q_impact,
                "is_overseas": bool(r["is_overseas"]),
                "report_nm": (r["report_nm"] or "").strip(),
            })

        contract_momentum = []
        for code, events in contract_by_code.items():
            total_q_impact = round(sum(e["quarterly_impact_pct"] or 0 for e in events), 2)
            if total_q_impact < 5:
                continue
            adv = advance_map.get(code)
            contract_momentum.append({
                "stock_code": code, "stock_name": name_map.get(code, code),
                "mktcap_억": round(mktcap_map.get(code) or 0),
                "events_180d": len(events),
                "total_quarterly_impact_pct": total_q_impact,
                "any_overseas": any(e["is_overseas"] for e in events),
                "all_domestic": all(not e["is_overseas"] for e in events),
                "contracts": sorted(events, key=lambda x: x["disclosed_at"], reverse=True)[:8],
                "advance_signal_score": adv["signal_score"] if adv else None,
                "advance_gross_to_revenue_pct": adv["gross_to_revenue_pct"] if adv else None,
                "advance_label": adv["signal_label"] if adv else None,
                "cross_confirmed": bool(adv and (adv["signal_score"] or 0) >= 4),
                "dilution_risk": _dilution_risk(code, today),
            })
        contract_momentum.sort(key=lambda x: x["dilution_risk"])
        contract_momentum.sort(key=lambda x: x["cross_confirmed"], reverse=True)
        contract_momentum.sort(key=lambda x: x["total_quarterly_impact_pct"], reverse=True)

        # 정렬 우선순위: 각 리스트의 핵심 기준을 최우선으로 하되, 희석위험(dilution_risk, 오름차순
        # =위험 낮은 순)을 그 다음 우선순위로 끼워넣어 "재무신호는 좋지만 희석이 잦은" 종목이
        # 같은 등급 내에서 상위로 올라오지 못하게 함(2026-07-19, 젬백스 사례 계기).
        confirmed.sort(key=lambda x: x["confirmed_date"], reverse=True)
        confirmed.sort(key=lambda x: x["dream_catalyst"], reverse=True)
        candidates.sort(key=lambda x: -(x["revenue_yoy_pct"] or 0))
        candidates.sort(key=lambda x: x["dilution_risk"])
        # 2026-07-28: why_score(재도전+이익의질+꿈촉매 단순합산) 폐기 — debt_quality로 대체.
        # walk-forward 검증된 순서: 조달없음 > 성장투자형 > 조달있음(데이터부족) > 생존형
        _DEBT_Q_RANK = {"조달없음": 0, "성장투자형": 1, "조달있음(데이터부족)": 2, "생존형": 3}
        candidates.sort(key=lambda x: _DEBT_Q_RANK.get(x.get("debt_quality"), 2))
        candidates.sort(key=lambda x: not x["small_cap_high_growth"])  # 검증된 최강신호 최우선
        quarterly_flips.sort(key=lambda x: -(x["quarter_net_income"] or 0))
        quarterly_flips.sort(key=lambda x: x["dream_catalyst"], reverse=True)
        reattempts.sort(key=lambda x: x["last_flip_quarter"], reverse=True)
        reattempts.sort(key=lambda x: x["dilution_risk"])
        reattempts.sort(key=lambda x: x["revenue_confirmed"], reverse=True)
        quality_loss.sort(key=lambda x: -(x["ni_plus_depreciation"] or -1e18))
        quality_loss.sort(key=lambda x: x["dilution_risk"])
        quality_loss.sort(key=lambda x: x["both_confirmed"], reverse=True)
        comprehensive.sort(key=lambda x: -(x["revenue_yoy_pct"] or -1e9))
        comprehensive.sort(key=lambda x: x["dream_catalyst"], reverse=True)
        comprehensive.sort(key=lambda x: x["dilution_risk"])
        comprehensive.sort(key=lambda x: x["score"], reverse=True)
        # 2026-07-24: 로지스틱 예측확률을 최우선 정렬기준으로 승격(위 score/dilution/dream_catalyst
        # 정렬은 그 다음 tie-break로 유지) — 검증된 가중치 기반이라 동일가중 score보다 세밀한 랭킹.
        comprehensive.sort(key=lambda x: x["predicted_probability_pct"], reverse=True)

        return {
            "confirmed_turnarounds": confirmed[:60],
            "quarterly_flip_ttm_still_negative": quarterly_flips[:60],
            "candidates_loss_but_revenue_growth": candidates[:60],
            "reattempt_turnaround": reattempts[:60],
            "quality_of_loss": quality_loss[:60],
            "comprehensive_score": comprehensive[:80],
            "contract_momentum": contract_momentum[:60],
            "research_note": {
                "status": "리딩시그널 5건 + 리스크신호 1건 검증됨(단일분기 흑자전환 + 재도전 턴어라운드 + 감가상각주도적자 + 종합스코어 + 꿈/테마 촉매 + 희석위험) — 2026-07-19 walk-forward 7차 검증",
                "base_rate": "적자 기업의 44.8%(학습)/38.7%(검증)가 4분기 내 자연스럽게 흑자 전환 — 기준율 자체가 높음",
                "findings": [
                    "🔎소형고성장(small_cap_high_growth, 2026-07-26 신규 — 사용자 관심: 에이엘티/에이팩트 "
                    "같은 '숨은 진주'는 시총 낮은 종목에서 텐버거 확률이 높다는 가설 검증): "
                    "strategy_feature_snapshot 15.5만행 walk-forward(학습<=2023/검증2024~) 결과 시총 자체는 "
                    "~300억 구간이 가장 높은 3배율(학습14.03%/검증13.99%, 매우 안정적)을 보이나 중간~대형 "
                    "구간은 2024~26년 메가캡 슈퍼사이클(SK하이닉스 등)로 역전됨 — 시총만으로는 불충분. "
                    "이 population(B섹션)을 시총<1000억 + 매출YoY+20%↑로 좁히면 3배율이 학습11.15%/검증"
                    "12.10%(전체평균 6.75%/7.71%의 약 1.5~1.7배, 검증기간에 더 강화)로 강건 — 단, **이미 "
                    "흑자인 소형 성장주는 오히려 더 약함**(8.13%/5.28%, 시총작음 단독 9.21%/8.04%보다도 열위) "
                    "— '아직 적자인데 매출은 빠르게 크는 소형주'가 핵심(에이엘티류와 정확히 일치, 에이팩트류 "
                    "-이미 흑자 성장가속-는 이 신호로는 안 잡힘, 별도 수급/거래량 신호 영역). 희석위험으로 "
                    "추가 필터링을 시도했으나(forward_max_ret은 12개월 중 최고점 기준이라 손실위험 측정에 "
                    "부적합 - 대부분 한번은 반등해 loss30 자체가 낮음) 방향이 불안정해 배제 미채택, 참고정보로만 "
                    "유지. 소형주 리스크 관리는 필터가 아니라 **분산(집중배팅 금지, 최소 10종목 이상)**과 "
                    "**엄격한 손절 규율**로 대응할 것 — 이 프로젝트가 검증한 V-MOONSHOT/V-MEGATREND와 동일 "
                    "원칙.",
                    "💰부채의 질(debt_quality, 2026-07-28 신규 — 사용자 지적: '3가지 점수(why_score)는 크게 "
                    "의미 없어 보인다' + '좋은 부채, 이익의 질을 보여주는게 중요해'): 기존 why_score(재도전+"
                    "이익의질+꿈촉매 단순합산)를 폐기했다 — dream_catalyst는 확률이 아니라 상승폭(fat-tail) "
                    "신호라 확률성 점수에 합산하면 안 된다는 원칙(F섹션 종합스코어에서 이미 확립)을 B)에서는 "
                    "어겼던 것이 원인. 대신 '좋은 부채/나쁜 부채'를 신규 검증(TTM<=0 population, 학습<=2023/"
                    "검증2024~): financing 조달이 있었던 분기에서 capex(성장투자)가 영업현금소진액보다 크면 "
                    "**성장투자형**(좋은부채), 작으면 **생존형**(나쁜부채, 운영손실을 메우기 위한 조달)으로 분류 "
                    "— forward 12개월 평균수익률 학습3.58%/검증17.34%(성장투자형) vs 학습-1.01%/검증6.66%"
                    "(생존형), -30%이하 손실확률도 성장투자형이 학습/검증 양쪽에서 8~10%p 낮음(26.5%/32.0% vs "
                    "30.2%/40.2%) — 학습·검증 방향이 일치하고 재현됨(채택). ⚠️ 1차 시도(조달액 대비 capex "
                    "비중으로 정의)는 학습/검증 방향이 뒤집혀 기각됐음 — 반드시 이 v2 정의(영업현금소진 대비 "
                    "capex)만 사용할 것. 부채비율(총부채/자본) 하락추세, 영업이익률 개선추세도 같이 검증했으나 "
                    "둘 다 학습/검증 방향이 불안정(부채비율은 완전히 뒤집힘, 영업이익률은 평균수익률 방향은 "
                    "뒤집히고 3배율만 일치)해 채택하지 않음(scratch/debt_quality_earnings_quality_test_"
                    "20260728.py, scratch/debt_quality_v2_test_20260728.py). '매출과 이익을 예측'은 정직하게 "
                    "한계 명시 — analyst_pdf_extracts(애널리스트 컨센서스)는 이 숨은진주 58종목 중 단 1종목만 "
                    "커버(소형주라 증권사 커버리지 자체가 없음), 검증된 예측모델도 없어 '예측'을 만들어 보여주는 "
                    "대신 상세보기 패널의 분기 매출/이익 추세(실측치)로만 제공.",
                    "🚨희석위험(트레일링365일 CB/BW/EB/RIGHTS 공시 건수, 리스크 신호 — 사용자 지적 계기: "
                    "젬백스 082270이 재도전+매출성장+이익의질 3개 신호를 모두 충족해 종합스코어 상위에 뜨지만 "
                    "실제로는 2025-08~2026-05 거의 매달 CB/BW/유상증자가 이어진 희석 스파이럴 종목, 고점대비 "
                    "-84%): 희석공시 건수가 늘어날수록 TTM흑자전환율·12개월 forward 주가수익률이 모두 단조 "
                    "악화 — 0건 lift 1.03x/1.07x(중앙값-12.2%/-15.5%, -30%이하비율 29%/31%) → **4건+(젬백스 "
                    "수준) lift 0.90x/0.64x(중앙값-25.9%/-29.1%, -30%이하비율 42.6%/49.3% — 거의 절반이 1년 "
                    "내 -30%이상 하락)**. 재무 신호가 좋아도 희석이 잦으면 실제 성과는 정반대로 나쁨 — 점수에 "
                    "섞지 않고 모든 발굴 섹션(B/D/E/F)에 별도 경고 컬럼으로 노출, 동일 등급 내에서는 희석위험 "
                    "낮은 순으로 재정렬.",
                    "🎯종합 턴어라운드 스코어(재도전이력+매출YoY성장+이익의질 3개 신호를 0~3점으로 합산): "
                    "신호 개수와 흑자전환율이 깔끔한 단조증가 — 0점 0.52~0.59x(기준율보다 낮음)/1점 0.79~0.91x/"
                    "2점 1.13~1.23x/**3점 전부 1.38x(학습)/1.63x(검증, 검증기간에 더 강화)**. 세 신호가 서로 겹치지 "
                    "않고 독립적으로 누적됨을 실증 — 이 조합 랭킹이 개별 탭보다 실전 발굴에 더 유용함.",
                    "★★★감가상각주도적자(회계상 적자이나 NI+분기감가상각>0 이고 영업현금흐름도 양호): "
                    "lift 1.40x(학습)/1.67x(검증) — 세션 전체 최강, 검증기간에 크게 강화(재현성 확인). "
                    "감가상각주도(NI+분기Dep>0)만으로도 1.31x/1.43x, 영업현금흐름>0만으로도 1.25x/1.38x. "
                    "사용자 사례 에이엘티(172670)의 2025년 분기 손실은 실제로 이 패턴 — 분기 감가상각비"
                    "(53~82억)가 순손실(38~45억)보다 커서 NI+감가상각이 매분기 플러스였고, 영업현금흐름도 "
                    "전분기 플러스(20~123억). 매출원가 문제(원가율>100%)가 아니라 자본집약적 사업(설비투자→"
                    "감가상각)이 회계상 적자를 만드는 사례임을 실증.",
                    "★★재도전 턴어라운드(최근4분기 내 단일분기 흑자 이력 + 현재 다시 적자 + 매출YoY성장): "
                    "lift 1.18x(학습)/1.31x(검증) — 검증기간에 더 강해짐(재현성 확인). "
                    "매출성장 조건 없이 '재도전 이력'만으로도 1.13x/1.22x. 에이엘티(25Q4 흑자→26Q1 재적자, "
                    "매출은 계속 성장)가 정확히 이 패턴이며 이 신호로도 포착됨 — TTM만 보면 놓치는 종목을 "
                    "잡아내는 게 핵심 취지.",
                    "★단일분기 흑자전환(TTM 적자 지속): lift 1.33x(학습)/1.70x(검증) — 검증기간에 오히려 강화, 매우 강한 신호",
                    "🌙꿈(테마) 촉매(특허/기술이전/R&D계약/라이선스 공시, 트레일링365일): 확정흑자전환 기준 12개월 forward "
                    "50%+급등 달성률이 학습 17.2%→검증 25.0%로 상승(무촉매 14.9%/14.3%, 방향 일치·재현됨) — 단, "
                    "중앙값은 개선 없음(오히려 낮거나 비슷)해 '대부분은 그대로지만 터지면 크게 터지는' 전형적 "
                    "꿈/테마주 분포. tech_transfer·license만 좁혀서 보면 학습/검증 부호가 뒤집혀 노이즈 — 반드시 "
                    "4종(특허/기술이전/R&D계약/라이선스) 전체를 합쳐서 사용해야 방향이 안정적임.",
                    "매출YoY>0(단독): 학습·검증 양쪽 방향 일치 (lift 1.07~1.08x, 약함)",
                    "매출 3분기 연속 QoQ 증가(계절 무시 순수추세, 단독): lift 1.05~1.09x — 매출YoY와 비슷한 수준, "
                    "계절적 저점분기(예: Q1)에서는 직전분기 대비 감소로 보여 놓치는 경우 있음 — 반드시 YoY 비교와 "
                    "병행할 것.",
                    "당분기 순이익 YoY 개선(적자축소, 당분기 단위·TTM 아님, 단독): lift 0.95~0.96x — 여전히 무효과/약한 음의 "
                    "예측력. '적자를 얼마나 덜 냈는가'보다 '매출이 늘고 있는가/과거 흑자 경험이 있는가/실질현금흐름이 "
                    "건전한가'가 실제로 유효한 신호였음.",
                    "매출총이익률 개선: 부호 불안정(학습 0.7x/검증 1.2x) — 노이즈로 판정",
                    "임원매수(직전180일): 일관된 음의 예측력(0.88x/0.95x) — 스마트머니 가설 기각",
                    "적자폭 축소 중(TTM 기준): 일관된 음의 예측력(0.86x/0.89x) — 딥밸류가 완만개선보다 더 잘 전환",
                    "수주잔고 YoY서지(+30%↑): 학습/검증 부호가 뒤집힘(학습 -13.7%/검증 +17.7%, n도 20~77로 협소) — 기각",
                    "🆕수주모멘텀(계약기간 정규화, 2026-07-25 신규): 단순 계약금액/매출 비율은 6개월 계약과 5년 "
                    "계약을 동일 취급해 오도 가능(예: 아이크래프트 052460은 단순합산 128.74%지만 5.5년 장기계약 "
                    "2건이 대부분 희석, 실제 2027-03-31 만기 단기계약 3건만 합치면 분기당 +31%로 훨씬 집중된 "
                    "신호) — quarterly_impact_pct = ratio/계약기간(분기수)로 근시일 임팩트를 정밀화. ⚠️국내(is_overseas="
                    "0) 계약은 홀드아웃 검증 결과 해외계약보다 신호품질이 뚜렷이 낮음(scratch/claude_domestic_"
                    "contract_signal_20260725.py: 국내전용 검증 25.4%/MDD-42.7% vs 해외전용 154.3%/MDD-27.9%) — "
                    "발굴 도구이므로 배제하지 않되 is_overseas/all_domestic 플래그로 구분 표시, 매매전략에는 미반영. "
                    "Codex 신규 contract_advance_signals(계약부채/선수금)와 signal_score>=4로 교차확인되면 "
                    "cross_confirmed=true — 공시(예고)+계약부채(실제 고객 현금수취 확인) 이중검증이라 신뢰도 높음. "
                    "단 계약부채 수집이 아직 시가총액 상위 63종목 한정이라 커버리지 낮음(전종목 확장 시 개선 예정).",
                ],
                "caveat": "⚠️ 재무 신호(재도전/매출성장/이익의질)가 전부 좋아도 최근 1년 내 CB/BW/유상증자 등 "
                          "희석 공시가 여러 건이면 실제 주가 성과는 반대로 나쁠 수 있습니다(위 희석위험 항목 참고) "
                          "— 이 발굴 탭들은 재무제표만 보는 스크리닝이라 희석·주가 추세는 별도로 꼭 확인하세요. "
                          "단일분기 흑자전환도 바로 다음분기 흑자 유지율은 47.6~54.6%뿐(거의 동전던지기) — "
                          "개별 케이스로는 되돌아갈 위험이 큽니다. 재도전 턴어라운드·감가상각주도적자도 '다음엔 "
                          "반드시 흑자'라는 보장이 아니라 확률이 유의하게 높아지는 것뿐입니다. 감가상각주도 판정은 "
                          "cash_flow_data 분기 감가상각비 데이터가 있는 종목(전체 이력 기준 약 34%)에만 적용 가능 — "
                          "데이터 없는 종목은 이 탭에 나타나지 않을 뿐 '해당 없음'을 의미하지 않습니다. ⚠️ 특히 최신 "
                          "분기(2026 Q1)는 DART 현금흐름표 수집이 아직 진행 중이라(전종목 중 영업현금흐름 확보 "
                          "153종목·감가상각 0종목, 2026-07-19 기준) 이 탭 결과 수가 일시적으로 매우 적습니다 — "
                          "수집기가 따라잡으면 자동으로 늘어날 정상적인 데이터 지연이며 로직 결함이 아닙니다. "
                          "꿈(테마) 촉매도 "
                          "'이 종목이 오른다'는 보장이 아니라 '오를 때 크게 오를 확률이 상대적으로 높다'는 fat-tail "
                          "신호일 뿐 — 표본이 작아(검증기간 확정흑자전환 촉매有 n=44) 과신 금지. '매출YoY성장'(B)은 "
                          "기존 확정 보너스에 결합 백테스트 시 오히려 악화(+79.9%→+71.5%)됨이 실증되어 매매 필터가 "
                          "아닌 참고 관찰용입니다.",
                "next_steps": "단일분기 흑자전환(C)·재도전턴어라운드(D)·감가상각주도적자(E)·종합스코어(F)·꿈촉매"
                              "(dream_catalyst)를 실제 매매전략(V-RECOVERY turnaround_bonus)에 결합 백테스트 예정. "
                              "세그먼트별 매출 회복(segment_revenue), 컨센서스 추정치 상향(analyst_pdf_extracts) 등 "
                              "미검증 후보도 남아 있음.",
            },
        }
    finally:
        conn.close()


_turnaround_watch_lock = threading.Lock()


def _load_turnaround_watch_cache() -> dict | None:
    try:
        if not TURNAROUND_WATCH_CACHE_PATH.exists():
            return None
        return json.loads(TURNAROUND_WATCH_CACHE_PATH.read_text())
    except Exception:
        return None


def _save_turnaround_watch_cache(min_mktcap: float, data: dict) -> None:
    try:
        TURNAROUND_WATCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = TURNAROUND_WATCH_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(
            {"computed_at": datetime.now().isoformat(), "min_mktcap": min_mktcap, "data": data},
            ensure_ascii=False,
        ))
        tmp.replace(TURNAROUND_WATCH_CACHE_PATH)
    except Exception as e:
        logger.warning(f"[turnaround-watch] cache write failed: {e}")


def refresh_turnaround_watch_cache(min_mktcap: float = 300.0) -> dict:
    """무거운 연산 실제 실행 지점 — 새벽 스케줄러(04:40) 전용, 수동 강제갱신(precompute)에서도 사용."""
    with _turnaround_watch_lock:
        data = _compute_turnaround_watch(min_mktcap)
        _save_turnaround_watch_cache(min_mktcap, data)
        return data


@router.get("/turnaround-watch/detail/{stock_code}")
def get_turnaround_watch_detail(stock_code: str):
    """개별종목 "왜 흑자전환 가능한가" 상세 — 2026-07-27 신규(사용자: "페이지에 상세 표시해야지").
    엑셀로 만들어 드린 것과 동일한 관점(분기재무추이/이익의질/희석이력/특허공시)을 페이지 내에서
    바로 펼쳐볼 수 있게 함(scratch/build_hidden_gem_excel_20260727.py와 동일 로직 재사용)."""
    conn = connect_primary_db(timeout=15, row_factory=sqlite3.Row)
    try:
        overrides = {r["stock_code"]: r["config_value"] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        pref = overrides.get(stock_code, "CFS")

        fin_rows = conn.execute("""
            SELECT year, quarter, report_type, revenue, operating_profit, net_income
            FROM financial_data WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND stock_code=?
            ORDER BY year, quarter
        """, (stock_code,)).fetchall()
        by_q: dict[tuple, dict] = {}
        for r in fin_rows:
            by_q.setdefault((r["year"], r["quarter"]), {})[r["report_type"]] = r
        quarters = sorted(by_q.keys())[-8:]
        quarterly = []
        for (y, q) in quarters:
            variants = by_q[(y, q)]
            r_ni = variants.get(pref) or next(iter(variants.values()))
            r_rev = variants.get("CFS") or r_ni
            prev = by_q.get((y - 1, q))
            rev_1y = None
            if prev:
                r_rev_prev = prev.get("CFS") or next(iter(prev.values()))
                rev_1y = r_rev_prev["revenue"]
            rev_yoy = round((r_rev["revenue"] / rev_1y - 1) * 100, 1) if (rev_1y and rev_1y > 0 and r_rev["revenue"]) else None
            quarterly.append({
                "year": y, "quarter": q,
                "revenue_억": round((r_rev["revenue"] or 0) / 1e8, 1),
                "revenue_yoy_pct": rev_yoy,
                "operating_profit_억": round((r_ni["operating_profit"] or 0) / 1e8, 1) if r_ni["operating_profit"] is not None else None,
                "net_income_억": round((r_ni["net_income"] or 0) / 1e8, 1) if r_ni["net_income"] is not None else None,
            })

        cf_rows = conn.execute("""
            SELECT year, quarter, report_type, depreciation_q, operating_cf_q, financing_cf_q, capex_q
            FROM cash_flow_data WHERE quarter BETWEEN 1 AND 4 AND stock_code=?
            ORDER BY year, quarter
        """, (stock_code,)).fetchall()
        cf_by_q: dict[tuple, dict] = {}
        for r in cf_rows:
            cf_by_q.setdefault((r["year"], r["quarter"]), {})[r["report_type"]] = r
        quality_of_loss = []
        for (y, q) in quarters:
            cf = cf_by_q.get((y, q))
            if not cf:
                continue
            r_cf = cf.get(pref) or next(iter(cf.values()))
            ni_row = by_q.get((y, q), {})
            r_ni = ni_row.get(pref) or (next(iter(ni_row.values())) if ni_row else None)
            ni = r_ni["net_income"] if r_ni else None
            dep = r_cf["depreciation_q"]
            ocf = r_cf["operating_cf_q"]
            if dep is None and ocf is None:
                continue
            ni_dep = (ni + dep) if (ni is not None and dep is not None) else None
            dep_driven = bool(ni_dep is not None and ni_dep > 0)
            cash_positive = bool(ocf is not None and ocf > 0)
            quality_of_loss.append({
                "year": y, "quarter": q,
                "net_income_억": round((ni or 0) / 1e8, 1) if ni is not None else None,
                "depreciation_억": round((dep or 0) / 1e8, 1) if dep is not None else None,
                "ni_plus_depreciation_억": round(ni_dep / 1e8, 1) if ni_dep is not None else None,
                "operating_cf_억": round((ocf or 0) / 1e8, 1) if ocf is not None else None,
                "dep_driven": dep_driven, "cash_positive": cash_positive,
                "verdict": "회계상적자·실질건전" if (dep_driven and cash_positive and (ni or 0) < 0)
                           else ("이익의질 양호" if (dep_driven or cash_positive) else "-"),
            })

        # 부채의 질(성장투자형/생존형) — 2026-07-28 신규, walk-forward 검증됨(quality_of_loss와
        # 동일 원리로 분기별 근거를 그대로 노출): financing_cf_q>0(자금조달)인 분기에서 capex_q
        # (성장투자, 절대값)가 영업현금소진액(-operating_cf_q)보다 크면 성장투자형, 작으면 생존형.
        debt_financing = []
        for (y, q) in quarters:
            cf = cf_by_q.get((y, q))
            if not cf:
                continue
            r_cf = cf.get(pref) or next(iter(cf.values()))
            fin_cf, capex, ocf = r_cf["financing_cf_q"], r_cf["capex_q"], r_cf["operating_cf_q"]
            if fin_cf is None or fin_cf <= 0:
                continue
            ocf_burn = max(0.0, -(ocf or 0))
            capex_abs = abs(capex) if capex is not None else None
            verdict = "-"
            if capex_abs is not None:
                verdict = "성장투자형(좋은부채)" if capex_abs >= ocf_burn else "생존형(주의)"
            debt_financing.append({
                "year": y, "quarter": q,
                "financing_cf_억": round(fin_cf / 1e8, 1),
                "capex_억": round(capex_abs / 1e8, 1) if capex_abs is not None else None,
                "operating_cf_burn_억": round(ocf_burn / 1e8, 1),
                "verdict": verdict,
            })

        cutoff3y = (datetime.now() - timedelta(days=1095)).strftime("%Y-%m-%d")
        dilution_rows = conn.execute("""
            SELECT event_type, issue_amount, dilution_pct, conversion_price, disclosed_at, report_nm, put_option_date
            FROM dilution_events
            WHERE stock_code=? AND event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS') AND disclosed_at >= ?
            ORDER BY disclosed_at DESC
        """, (stock_code, cutoff3y)).fetchall()
        dilution_history = [{
            "type": r["event_type"], "issue_amount_억": round(r["issue_amount"] / 1e8, 1) if r["issue_amount"] else None,
            "dilution_pct": r["dilution_pct"], "conversion_price": r["conversion_price"],
            "date": r["disclosed_at"], "report": r["report_nm"], "put_option_date": r["put_option_date"],
        } for r in dilution_rows]

        patent_rows = conn.execute("""
            SELECT rcept_dt, report_nm, signal_type, amount_krw FROM dart_rd_patent_signals
            WHERE stock_code=? AND rcept_dt >= ? ORDER BY rcept_dt DESC
        """, (stock_code, cutoff3y.replace("-", ""))).fetchall()
        patent_history = [{
            "date": r["rcept_dt"], "report": r["report_nm"], "type": r["signal_type"],
            "amount_백만": round(r["amount_krw"] / 1e6) if r["amount_krw"] else None,
        } for r in patent_rows]

        name_row = conn.execute("SELECT stock_name, sector_large, sector_small, market FROM stock_universe WHERE stock_code=?", (stock_code,)).fetchone()

        return {
            "stock_code": stock_code,
            "stock_name": name_row["stock_name"] if name_row else stock_code,
            "sector_large": name_row["sector_large"] if name_row else None,
            "sector_small": name_row["sector_small"] if name_row else None,
            "market": name_row["market"] if name_row else None,
            "quarterly": quarterly,
            "quality_of_loss": quality_of_loss,
            "debt_financing": debt_financing,
            "dilution_history": dilution_history,
            "patent_history": patent_history,
        }
    finally:
        conn.close()


@router.get("/turnaround-watch")
def get_turnaround_watch(min_mktcap: float = Query(default=300.0)):
    """턴어라운드 추적 화면용 — 새벽 사전계산 캐시를 그대로 반환(요청마다 재계산하지 않음).
    캐시가 아예 없는 경우(최초 기동 등)에 한해 이번 요청에서 동기 계산 후 캐싱한다."""
    cached = _load_turnaround_watch_cache()
    if cached and abs(float(cached.get("min_mktcap", 300.0)) - min_mktcap) < 1e-6:
        return cached["data"]
    return refresh_turnaround_watch_cache(min_mktcap)


@router.post("/turnaround-watch/precompute")
def trigger_turnaround_watch_precompute(min_mktcap: float = Query(default=300.0)):
    """수동 강제 재계산 (평소엔 새벽 04:40 스케줄러가 자동 수행)."""
    data = refresh_turnaround_watch_cache(min_mktcap)
    return {
        "ok": True,
        "computed_at": datetime.now().isoformat(),
        "sections": {k: (len(v) if isinstance(v, list) else None) for k, v in data.items()},
    }
