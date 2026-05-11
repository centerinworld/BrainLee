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
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

BASE_DIR = Path("/Applications/stock_dashboard")
DB_PATH = str(BASE_DIR / "stock.db")
EMP_DB_PATH = str(BASE_DIR / "employment_monitor" / "employment.db")
TRADE_DB_PATH = str(BASE_DIR / "hs_trade_lab" / "data" / "hs_trade_lab.db")
SECTOR_AI_CACHE_PATH = BASE_DIR / "scratch" / "sector_ai_daily.json"

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
            lines.append(f"📌 {summary}")
        if market_view:
            lines.append(f"🔭 {market_view}")
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
            if s.get("ai_reason"):
                lines.append(f"   💡 {s['ai_reason']}")
            if s.get("ai_risk"):
                lines.append(f"   ⚠️ 리스크: {s['ai_risk']}")

        # 주목 종목 상위 5개
        stocks = data.get("stocks", [])[:5]
        if stocks:
            lines.append("─" * 32)
            lines.append("🔍 주목 종목 (상위 5)")
            for st in stocks:
                short_warn = ""
                borrow = st.get("borrow_bal_pct", 0) or 0
                ret20 = st.get("ret_20d", 0) or 0
                if borrow > 1.5:
                    short_warn += f" ⚠️대차{borrow:.1f}%"
                if ret20 > 40:
                    short_warn += f" ⚠️20일+{ret20:.0f}%"
                lines.append(
                    f"   • {st['stock_name']} ({st['stock_code']}) "
                    f"점수:{st.get('score', 0):.0f} "
                    f"20일:{ret20:+.0f}%{short_warn}"
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
    emp_months: int = Query(3, ge=3, le=6),
    opm_mode: str = "score",
    depr_mode: str = "score",
    emp_mode: str = "score",
    export_mode: str = "score",
    foreign_mode: str = "score",
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

            market_cap_won = _num(stock.get("market_cap")) or 0
            frn_amt_million = foreign.get(code)
            foreign_pct = None
            if market_cap_won > 0 and frn_amt_million is not None:
                foreign_pct = (frn_amt_million * 1_000_000) / market_cap_won * 100

            values = {
                "opm": opm,
                "depr": depr_ratio,
                "emp": emp_pct,
                "export": export_growth.get(code),
                "foreign": foreign_pct,
            }
            passed = {
                "opm": values["opm"] is not None and values["opm"] <= opm_threshold,
                "depr": values["depr"] is not None and values["depr"] >= depr_threshold,
                "emp": values["emp"] is not None and values["emp"] >= emp_threshold,
                "export": values["export"] is not None and values["export"] >= export_threshold,
                "foreign": values["foreign"] is not None and values["foreign"] >= foreign_threshold,
            }
            state = {"score": 0, "matched": [], "failed_required": [], "has_or": False, "or_passed": False}
            metric_labels = {
                "opm": "영업이익률",
                "depr": "감가상각비율",
                "emp": "고용증가",
                "export": "수출증가",
                "foreign": "외국인수급",
            }
            for key in ("opm", "depr", "emp", "export", "foreign"):
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
                "market_cap_억": _num(market_cap_won / 100_000_000, 0),
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
