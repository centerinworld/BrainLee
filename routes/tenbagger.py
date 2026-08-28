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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from env_utils import BASE_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(BASE_DIR / "stock.db")
EMP_DB_PATH = str(BASE_DIR / "employment_monitor" / "employment.db")
TRADE_DB_PATH = str(BASE_DIR / "hs_trade_lab" / "data" / "hs_trade_lab.db")
SECTOR_AI_CACHE_PATH = BASE_DIR / "scratch" / "sector_ai_daily.json"
TRIGGER_ANALYSIS_PATH = BASE_DIR / "research_outputs" / "signal_trigger_analysis_2020plus.json"
TRIGGER_ANALYSIS_SCRIPT = BASE_DIR / "scripts" / "research_signal_trigger_analysis.py"

_run_lock   = threading.Lock()
_run_status = {"running": False, "last_run": None, "last_count": 0, "error": None}


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ro_conn(path: str):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


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
                    AVG(NULLIF(p.trade_amount, 0)) AS avg_trade_amount,
                    MAX(p.trade_amount) AS max_trade_amount,
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
                    ROUND(y.max_high / y.min_low, 2) AS multiple,
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
                ROUND(r.max_volume / NULLIF(r.avg_volume, 0), 1) AS vol_peak_x,
                ROUND(r.max_trade_amount / NULLIF(r.avg_trade_amount, 0), 1) AS amount_peak_x
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
                    AVG(NULLIF(p.trade_amount, 0)) AS avg_trade_amount,
                    MAX(p.trade_amount) AS max_trade_amount,
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
                ROUND(y.max_high / y.min_low, 2) AS multiple,
                ROUND(y.max_volume / NULLIF(y.avg_volume, 0), 1) AS vol_peak_x,
                ROUND(y.max_trade_amount / NULLIF(y.avg_trade_amount, 0), 1) AS amount_peak_x
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
        """, (latest_run, limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["reasons"]      = json.loads(d["reasons"] or "[]")
            d["score_detail"] = json.loads(d["score_detail"] or "{}")
            results.append(d)

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
            api_key = getattr(config, "OPENAI_API_KEY", "")
            if api_key and sector_payload:
                import openai
                client = openai.OpenAI(api_key=api_key, timeout=18.0, max_retries=0)
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
                ROUND((e.p_end - s.p_start) / s.p_start * 100.0, 2) AS pct_change
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
        fin_rows = conn.execute(f"""
            SELECT f.stock_code,
                   MAX(CASE WHEN f.year = (SELECT MAX(year) FROM financial_data fd WHERE fd.stock_code=f.stock_code AND fd.is_annual=1) THEN f.revenue END) AS rev_cur,
                   MAX(CASE WHEN f.year = (SELECT MAX(year) FROM financial_data fd WHERE fd.stock_code=f.stock_code AND fd.is_annual=1) - 1 THEN f.revenue END) AS rev_prev,
                   MAX(CASE WHEN f.year = (SELECT MAX(year) FROM financial_data fd WHERE fd.stock_code=f.stock_code AND fd.is_annual=1) THEN f.operating_profit END) AS op_cur,
                   MAX(CASE WHEN f.year = (SELECT MAX(year) FROM financial_data fd WHERE fd.stock_code=f.stock_code AND fd.is_annual=1) - 1 THEN f.operating_profit END) AS op_prev,
                   MAX(CASE WHEN f.year = (SELECT MAX(year) FROM financial_data fd WHERE fd.stock_code=f.stock_code AND fd.is_annual=1) THEN f.roe END) AS roe
            FROM financial_data f
            WHERE f.stock_code IN ({ph}) AND f.is_annual = 1
            GROUP BY f.stock_code
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

        results.sort(key=lambda x: x["score"], reverse=True)
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


# ── DeepSeek 텐버거 심층 분석 ─────────────────────────────────────────

DB_PATH = "/Applications/stock_dashboard/stock.db"
_AI_CACHE_HOURS = 24  # 같은 날 재호출 시 캐시 반환


def _build_tenbagger_context(stock_code: str) -> dict:
    """DB에서 종목의 재무/수급/공시/스코어 컨텍스트를 수집"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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


def _call_deepseek_tenbagger(ctx: dict) -> str:
    import os, requests as _rq
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")

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

    if not api_key:
        return f"## {ctx['stock_name']} 텐버거 분석\n\nDeepSeek API 키가 설정되지 않았습니다."

    try:
        res = _rq.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "당신은 10배 수익 후보 종목을 발굴하는 한국 주식 투자 전문 애널리스트입니다. 데이터 기반으로 냉정하게 분석하되, 텐버거 가능성이 있는 핵심 근거를 구체적 수치와 함께 제시하세요."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=90,
        )
        if res.ok:
            return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error("[Tenbagger AI] DeepSeek 호출 실패: %s", e)
    return f"## {ctx['stock_name']} 분석 실패\n\nAPI 응답 오류가 발생했습니다."


@router.get("/ai-analysis/{stock_code}")
def get_tenbagger_ai_analysis(stock_code: str, force: bool = False):
    """텐버거 후보 종목 DeepSeek 심층 분석. 24시간 캐시."""
    import sqlite3 as _sl, os
    from datetime import datetime as _dt, timedelta
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row

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
    analysis = _call_deepseek_tenbagger(ctx)

    # 저장
    conn2 = _sl.connect(DB_PATH)
    now = _dt.now().isoformat()
    conn2.execute(
        "INSERT INTO tenbagger_ai_analysis(stock_code, generated_at, score, reasons, ai_analysis, model)"
        " VALUES(?,?,?,?,?,?)",
        (stock_code, now, ctx["tb_score"], ctx["tb_reasons"], analysis,
         os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    )
    conn2.commit()
    conn2.close()

    return {"stock_code": stock_code, "stock_name": ctx["stock_name"],
            "analysis": analysis, "generated_at": now, "score": ctx["tb_score"], "cached": False}


@router.get("/ai-analysis-list")
def get_ai_analysis_list(limit: int = Query(20, ge=1, le=100)):
    """최근 생성된 AI 분석 목록"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    텐버거 상위 후보 N종목 DeepSeek 심층 분석 배치 실행.
    - 24시간 이내 캐시된 종목은 건너뜀 (force=True 시 강제 재분석)
    - 백그라운드 실행: 즉시 job_id 반환, 진행 상황은 /ai-analysis-list 에서 확인
    """
    import threading, time as _t
    from datetime import datetime as _dt, timedelta

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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
                analysis = _call_deepseek_tenbagger(ctx)
                conn2 = sqlite3.connect(DB_PATH, timeout=60)
                conn2.execute(
                    "INSERT INTO tenbagger_ai_analysis"
                    "(stock_code, generated_at, score, reasons, ai_analysis, model)"
                    " VALUES(?,?,?,?,?,?)",
                    (sc, _dt.now().isoformat(), score, ctx.get("tb_reasons", ""),
                     analysis, "deepseek-batch")
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
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row

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
        params + [page_size, offset]
    ).fetchall()

    conn.close()

    def _parse_reasons(r):
        try: return __import__("json").loads(r) if r else []
        except: return [r] if r else []

    return {
        "results": [{**dict(r), "reasons": _parse_reasons(r["reasons"])} for r in rows],
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
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row

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
        """, (code, sel_date)).fetchall()
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
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row

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
            """, (code, sel_date)).fetchall()
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

        v3_score = (r["total_score"] or 0) + industry_adj + fp_penalty + backlog_bonus

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

        r["v3_score"] = round(v3_score, 2)
        r["industry_adj"] = industry_adj
        r["industry_label"] = ind_label
        r["industry_yoy_pct"] = round(yoy_pct, 1) if yoy_pct is not None else None
        r["fp_penalty"] = fp_penalty
        r["fp_fp_count"] = fp_cnt
        r["backlog_bonus"] = backlog_bonus
        r["backlog_ratio"] = round(bl_ratio, 2) if bl_ratio else None
        r["reasons_v3"] = reasons_v3
        results.append(r)

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
                   (SELECT ROUND(AVG(vh2.pbr),2) FROM valuation_history vh2 WHERE vh2.stock_code=vh.stock_code AND vh2.pbr IS NOT NULL) as pbr_hist_avg
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
        
        return {
            "price_history":        {"rows": cnt("price_history")[0], "stocks": cnt("price_history")[1]},
            "financial_data":       {"rows": cnt("financial_data")[0], "stocks": cnt("financial_data")[1]},
            "cash_flow_data":       {"rows": cnt("cash_flow_data")[0], "stocks": cnt("cash_flow_data")[1]},
            "order_backlog":        {"rows": cnt("order_backlog")[0], "stocks": cnt("order_backlog")[1]},
            "cost_structure":       {"rows": cnt("cost_structure")[0], "stocks": cnt("cost_structure")[1]},
            "dilution_events":      {"rows": cnt("dilution_events")[0], "stocks": cnt("dilution_events")[1]},
            "kiwoom_credit_balance":{"rows": cnt("kiwoom_credit_balance")[0], "stocks": cnt("kiwoom_credit_balance")[1]},
            "dart_insider_holdings":{"rows": cnt("dart_insider_holdings")[0], "stocks": cnt("dart_insider_holdings")[1]},
            "valuation_history":    {"rows": cnt("valuation_history")[0], "stocks": cnt("valuation_history")[1]},
            "segment_revenue":      {"rows": cnt("segment_revenue")[0], "stocks": cnt("segment_revenue")[1]},
            "kiwoom_investor_daily":{"rows": cnt("kiwoom_investor_daily")[0], "stocks": cnt("kiwoom_investor_daily")[1]},
            "earnings_signals":     {"rows": cnt("earnings_signals")[0], "stocks": cnt("earnings_signals")[1]},
            "tenbagger_results":    {"rows": cnt("tenbagger_results")[0], "stocks": cnt("tenbagger_results")[1]},
            "triple_pattern_daily": {"rows": cnt("triple_pattern_daily")[0], "stocks": cnt("triple_pattern_daily")[1]},
            "treasury_buyback":     {"rows": cnt("treasury_buyback")[0], "stocks": cnt("treasury_buyback")[1]},
            "investor_flow_quarterly": {"rows": cnt("investor_flow_quarterly")[0], "stocks": cnt("investor_flow_quarterly")[1]},
            "foreign_flow_quarterly":  {"rows": cnt("foreign_flow_quarterly")[0], "stocks": cnt("foreign_flow_quarterly")[1]},
            "broker_program_market_daily": {
                "rows": program_market.get("rows", 0) or 0,
                "markets": program_market.get("markets", 0) or 0,
                "min_dt": program_market.get("min_dt"),
                "max_dt": program_market.get("max_dt"),
            },
            "broker_program_stock_daily": {
                "rows": program_stock.get("rows", 0) or 0,
                "stocks": program_stock.get("stocks", 0) or 0,
                "min_dt": program_stock.get("min_dt"),
                "max_dt": program_stock.get("max_dt"),
            },
        }
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
            SELECT tb.stock_code, su.stock_name, su.market,
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
            """, (code, sel_date, days_after)).fetchall()

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

        # 4) CB/BW 희석 이벤트 (최근 3년)
        dilution_rows = conn.execute("""
            SELECT event_type, issue_amount, dilution_pct, conversion_price, disclosed_at, report_nm
            FROM dilution_events
            WHERE stock_code=? AND disclosed_at >= date('now','-1095 days')
            ORDER BY disclosed_at DESC
            LIMIT 10
        """, (stock_code,)).fetchall()
        dilution_data = []
        for r in dilution_rows:
            amt_억 = round(r[1]/1e8,1) if r[1] else None
            dilution_data.append({"type": r[0], "issue_amount_억": amt_억, "dilution_pct": r[2], "conversion_price": r[3], "date": r[4], "report": r[5]})

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
            "buyback": buyback_data,
            "credit_balance": credit_data,
            "foreign_ownership": foreign_data,
            "has_valuation": len(valuation_data) > 0,
            "has_investor_flow": len(investor_flow) > 0,
            "has_insider": len(insider_data) > 0,
            "has_dilution": len(dilution_data) > 0,
            "has_buyback": len(buyback_data) > 0,
            "has_credit": len(credit_data) > 0,
            "has_foreign": len(foreign_data) > 0,
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        return {
            "buy_signals":   [s for s in signals if s.get("buy_signal")],
            "watch_signals": [s for s in signals if not s.get("buy_signal")],
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
            _conn = _sl.connect(DB_PATH)
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
