from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from scripts.ops.quant_indicator_signal_engine import (
    classify_traffic_light,
    is_consecutive_period,
    load_indicator_rows,
    period_yoy_key,
    related_stocks,
)


router = APIRouter()
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
PIPELINE = ROOT / "scripts" / "ops" / "naver_cafe_signal_pipeline.py"
PYTHON = ROOT / "venv" / "bin" / "python"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cafe_signal_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_id TEXT NOT NULL,
            board_key TEXT NOT NULL,
            board_name TEXT,
            article_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            author TEXT,
            published_at TEXT,
            excerpt TEXT,
            content_hash TEXT,
            collected_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(cafe_id, article_id)
        );
        CREATE TABLE IF NOT EXISTS cafe_signal_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_post_id INTEGER NOT NULL,
            mention_type TEXT NOT NULL,
            mention_key TEXT NOT NULL,
            mention_name TEXT NOT NULL,
            stock_code TEXT,
            stock_name TEXT,
            sector_name TEXT,
            indicator_name TEXT,
            signal_direction TEXT,
            confidence REAL DEFAULT 0.5,
            evidence TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(cafe_post_id, mention_type, mention_key)
        );
        CREATE TABLE IF NOT EXISTS cafe_signal_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            source_board_keys TEXT,
            posts_count INTEGER DEFAULT 0,
            stocks_count INTEGER DEFAULT 0,
            sectors_count INTEGER DEFAULT 0,
            indicators_count INTEGER DEFAULT 0,
            summary_json TEXT,
            generated_at TEXT NOT NULL,
            UNIQUE(run_type, period_key)
        );
        CREATE TABLE IF NOT EXISTS cafe_monthly_sector_leadership (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            export_value_musd REAL,
            export_yoy_pct REAL,
            export_mom_pct REAL,
            unit_price_yoy_pct REAL,
            trade_balance_musd REAL,
            momentum_score REAL,
            rank_no INTEGER,
            source_detail TEXT,
            generated_at TEXT NOT NULL,
            UNIQUE(period, indicator_key)
        );
        CREATE TABLE IF NOT EXISTS cafe_monthly_hs_leadership (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            hs_code TEXT NOT NULL,
            hs_name TEXT,
            sector_name TEXT,
            export_value_usd REAL,
            export_yoy_pct REAL,
            export_mom_pct REAL,
            export_weight_kg REAL,
            export_unit_price REAL,
            unit_price_yoy_pct REAL,
            related_companies TEXT,
            matched_indicator_key TEXT,
            momentum_score REAL,
            rank_no INTEGER,
            generated_at TEXT NOT NULL,
            UNIQUE(period, hs_code)
        );
        CREATE TABLE IF NOT EXISTS cafe_monthly_generated_reports (
            period TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            sector_count INTEGER DEFAULT 0,
            hs_count INTEGER DEFAULT 0,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cafe_quant_indicator_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_name TEXT NOT NULL,
            mention_count INTEGER DEFAULT 0,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            status TEXT,
            source_system TEXT,
            confidence REAL DEFAULT 0.7,
            mapping_note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(sector_name, indicator_key)
        );
        CREATE TABLE IF NOT EXISTS cafe_stock_indicator_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            sector_name TEXT,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            mention_count INTEGER DEFAULT 0,
            evidence_terms TEXT,
            example_posts TEXT,
            latest_collected_at TEXT,
            revenue_exposure_pct REAL,
            profit_exposure_pct REAL,
            cost_exposure_pct REAL,
            exposure_basis TEXT,
            importance_level TEXT,
            confidence REAL DEFAULT 0.6,
            mapping_status TEXT DEFAULT 'candidate_context',
            mapping_note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(stock_code, indicator_key)
        );
        CREATE INDEX IF NOT EXISTS idx_cafe_stock_indicator_stock
            ON cafe_stock_indicator_mappings(stock_code, mention_count DESC);
        CREATE TABLE IF NOT EXISTS quant_indicator_signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            series_name TEXT NOT NULL,
            period TEXT NOT NULL,
            value REAL,
            prev_value REAL,
            mom_pct REAL,
            yoy_pct REAL,
            z_score REAL,
            signal_type TEXT NOT NULL,
            signal_strength REAL DEFAULT 0,
            related_stocks TEXT,
            message TEXT,
            generated_at TEXT NOT NULL,
            telegram_sent INTEGER DEFAULT 0,
            UNIQUE(indicator_key, series_name, period, signal_type)
        );
        CREATE INDEX IF NOT EXISTS idx_qise_generated
            ON quant_indicator_signal_events(generated_at DESC);
        CREATE TABLE IF NOT EXISTS indicator_sector_direction_rules (
            indicator_key TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            direction_mode TEXT NOT NULL,
            note TEXT,
            confidence REAL DEFAULT 0.7,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(indicator_key, sector_name)
        );
        """
    )
    existing = {r[1] for r in conn.execute("PRAGMA table_info(cafe_stock_indicator_mappings)").fetchall()}
    for name, ddl in {
        "revenue_exposure_pct": "REAL",
        "profit_exposure_pct": "REAL",
        "cost_exposure_pct": "REAL",
        "exposure_basis": "TEXT",
        "importance_level": "TEXT",
        "mapping_status": "TEXT DEFAULT 'candidate_context'",
    }.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE cafe_stock_indicator_mappings ADD COLUMN {name} {ddl}")
    conn.commit()


def _decode_summary(row: sqlite3.Row | None) -> dict:
    if not row:
        return {}
    data = dict(row)
    try:
        data["summary"] = json.loads(data.get("summary_json") or "{}")
    except Exception:
        data["summary"] = {}
    data.pop("summary_json", None)
    return data


def _period_freshness(period: str) -> dict:
    now = datetime.now()
    try:
        if len(period) == 10:
            lag = (now - datetime.strptime(period, "%Y-%m-%d")).days
            return {"is_fresh": lag <= 14, "freshness_lag": lag, "freshness_unit": "days"}
        if len(period) == 7:
            year, month = map(int, period.split("-"))
            lag = now.year * 12 + now.month - (year * 12 + month)
            return {"is_fresh": lag <= 3, "freshness_lag": lag, "freshness_unit": "months"}
        if len(period) == 4 and period.isdigit():
            lag = now.year - int(period)
            return {"is_fresh": lag <= 1, "freshness_lag": lag, "freshness_unit": "years"}
    except ValueError:
        pass
    return {"is_fresh": False, "freshness_lag": None, "freshness_unit": "unknown"}


def _latest_indicator_traffic(conn: sqlite3.Connection, indicator_key: str) -> dict | None:
    series = conn.execute(
        """
        SELECT DISTINCT series_name
        FROM quant_major_indicator_series
        WHERE indicator_key=? AND value IS NOT NULL
        ORDER BY series_name
        """,
        (indicator_key,),
    ).fetchall()
    signals = []
    for series_row in series:
        rows = load_indicator_rows(conn, indicator_key, series_row["series_name"])
        if len(rows) < 3:
            continue
        latest = rows[-1]
        prev_candidate = rows[-2]
        prev = prev_candidate if is_consecutive_period(prev_candidate["period"], latest["period"]) else None
        yoy_key = period_yoy_key(latest["period"])
        yoy_row = next((r for r in reversed(rows[:-1]) if r["period"] == yoy_key), None) if yoy_key else None
        history = [float(r["value"]) for r in rows[:-1] if r["value"] is not None]
        status = classify_traffic_light(
            float(latest["value"]),
            float(prev["value"]) if prev and prev["value"] is not None else None,
            float(yoy_row["value"]) if yoy_row and yoy_row["value"] is not None else None,
            history,
            series_row["series_name"],
        )
        signals.append(
            {
                "series_name": series_row["series_name"],
                "period": latest["period"],
                "value": float(latest["value"]),
                "unit": latest["unit"],
                "quality": latest["quality"],
                **_period_freshness(latest["period"]),
                **status,
            }
        )
    if not signals:
        return None
    greens = [s for s in signals if s["traffic_light"] == "green"]
    reds = [s for s in signals if s["traffic_light"] == "red"]
    if greens and reds:
        strongest = max(signals, key=lambda s: float(s.get("signal_strength") or 0))
        return {
            **strongest,
            "traffic_light": "yellow",
            "signal_label": "혼조/주의",
            "reason": f"상승 우호 {len(greens)}개와 하락 위험 {len(reds)}개 구성 신호가 함께 감지됐습니다.",
        }
    priority = {"red": 0, "green": 1, "yellow": 2, "gray": 3}
    return sorted(signals, key=lambda s: (priority.get(s["traffic_light"], 9), -float(s.get("signal_strength") or 0)))[0]


def _sector_direction_rule(conn: sqlite3.Connection, indicator_key: str, sector_name: str) -> dict | None:
    row = conn.execute(
        """
        SELECT indicator_key, sector_name, direction_mode, note, confidence
        FROM indicator_sector_direction_rules
        WHERE indicator_key=? AND sector_name=?
        """,
        (indicator_key, sector_name),
    ).fetchone()
    return dict(row) if row else None


def _apply_sector_direction(signal: dict, rule: dict | None) -> dict:
    if not rule:
        return signal
    signal_type = signal.get("signal_type")
    strength = float(signal.get("signal_strength") or 0)
    direction_mode = rule.get("direction_mode") or ""
    if signal_type not in {"spike_up", "spike_down"}:
        return {**signal, "sector_direction_rule": rule}
    if direction_mode == "higher_is_good":
        signed = strength if signal_type == "spike_up" else -strength
    elif direction_mode == "higher_is_bad":
        signed = -strength if signal_type == "spike_up" else strength
    elif direction_mode == "ambiguous":
        return {
            **signal,
            "traffic_light": "yellow",
            "signal_label": "주의",
            "sector_adjusted_score": 0.0,
            "direction_mode": direction_mode,
            "direction_note": rule.get("note") or signal.get("direction_note"),
            "reason": rule.get("note") or signal.get("reason"),
            "sector_direction_rule": rule,
        }
    else:
        return {**signal, "sector_direction_rule": rule}
    return {
        **signal,
        "traffic_light": "green" if signed > 0 else "red",
        "signal_label": "좋음" if signed > 0 else "나쁨",
        "sector_adjusted_score": round(signed, 3),
        "direction_mode": direction_mode,
        "direction_note": rule.get("note") or signal.get("direction_note"),
        "reason": rule.get("note") or signal.get("reason"),
        "sector_direction_rule": rule,
    }


def _signal_score(signal: dict) -> float:
    if "sector_adjusted_score" in signal:
        return float(signal.get("sector_adjusted_score") or 0)
    if signal.get("traffic_light") == "green":
        return float(signal.get("signal_strength") or 0)
    if signal.get("traffic_light") == "red":
        return -float(signal.get("signal_strength") or 0)
    return 0.0


@router.get("/summary")
def get_cafe_signal_summary(run_type: str = Query("weekly", pattern="^(weekly|monthly)$")):
    conn = _conn()
    try:
        _ensure_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM cafe_signal_runs
            WHERE run_type=?
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """,
            (run_type,),
        ).fetchone()
        counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM cafe_signal_posts) AS posts,
                (SELECT COUNT(*) FROM cafe_signal_mentions) AS mentions,
                (SELECT COUNT(*) FROM cafe_signal_runs) AS runs,
                (SELECT MAX(collected_at) FROM cafe_signal_posts) AS latest_collected_at
            """
        ).fetchone()
        return {"latest": _decode_summary(row), "counts": dict(counts) if counts else {}}
    finally:
        conn.close()


@router.get("/runs")
def get_cafe_signal_runs(run_type: Optional[str] = None, limit: int = Query(20, ge=1, le=100)):
    conn = _conn()
    try:
        _ensure_tables(conn)
        params = []
        where = ""
        if run_type:
            where = "WHERE run_type=?"
            params.append(run_type)
        rows = conn.execute(
            f"""
            SELECT id, run_type, period_key, posts_count, stocks_count, sectors_count,
                   indicators_count, generated_at, summary_json
            FROM cafe_signal_runs
            {where}
            ORDER BY generated_at DESC, id DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        return {"items": [_decode_summary(r) for r in rows]}
    finally:
        conn.close()


@router.get("/posts")
def get_cafe_signal_posts(
    q: str = "",
    mention_type: str = "",
    limit: int = Query(50, ge=1, le=200),
):
    conn = _conn()
    try:
        _ensure_tables(conn)
        params = []
        where = "WHERE 1=1"
        if q:
            where += " AND (p.title LIKE ? OR p.excerpt LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        if mention_type:
            where += " AND EXISTS (SELECT 1 FROM cafe_signal_mentions m WHERE m.cafe_post_id=p.id AND m.mention_type=?)"
            params.append(mention_type)
        rows = conn.execute(
            f"""
            SELECT p.id, MAX(p.board_name) AS board_name, MAX(p.article_id) AS article_id,
                   MAX(p.title) AS title, MAX(p.url) AS url, MAX(p.author) AS author,
                   MAX(p.published_at) AS published_at, MAX(p.excerpt) AS excerpt,
                   MAX(p.collected_at) AS collected_at, MAX(p.updated_at) AS updated_at,
                   GROUP_CONCAT(m.mention_type || ':' || m.mention_name, ', ') AS mentions
            FROM cafe_signal_posts p
            LEFT JOIN cafe_signal_mentions m ON m.cafe_post_id=p.id
            {where}
            GROUP BY p.id
            ORDER BY MAX(p.collected_at) DESC, p.id DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/mentions")
def get_cafe_signal_mentions(
    mention_type: str = Query("stock", pattern="^(stock|sector|indicator)$"),
    days: int = Query(35, ge=1, le=3700),
    limit: int = Query(50, ge=1, le=200),
):
    conn = _conn()
    try:
        _ensure_tables(conn)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            """
            SELECT m.mention_type, m.mention_key,
                   MAX(m.mention_name) AS mention_name, MAX(m.stock_code) AS stock_code,
                   MAX(m.stock_name) AS stock_name, MAX(m.sector_name) AS sector_name,
                   MAX(m.indicator_name) AS indicator_name,
                   COUNT(*) AS mention_count,
                   ROUND(AVG(m.confidence), 3) AS avg_confidence,
                   SUM(CASE WHEN m.signal_direction='positive' THEN 1 ELSE 0 END) AS positive_count,
                   SUM(CASE WHEN m.signal_direction='negative' THEN 1 ELSE 0 END) AS negative_count,
                   MAX(p.collected_at) AS latest_collected_at
            FROM cafe_signal_mentions m
            JOIN cafe_signal_posts p ON p.id=m.cafe_post_id
            WHERE m.mention_type=?
              AND p.collected_at >= ?
            GROUP BY m.mention_type, m.mention_key
            ORDER BY mention_count DESC, avg_confidence DESC
            LIMIT ?
            """,
            (mention_type, cutoff, limit),
        ).fetchall()
        return {"items": [dict(r) for r in rows], "mention_type": mention_type, "days": days}
    finally:
        conn.close()


@router.get("/leadership")
def get_cafe_monthly_leadership(period: str = "", sector_limit: int = Query(12, ge=1, le=50), hs_limit: int = Query(30, ge=1, le=100)):
    conn = _conn()
    try:
        _ensure_tables(conn)
        if not period:
            row = conn.execute("SELECT MAX(period) AS period FROM cafe_monthly_generated_reports").fetchone()
            period = row["period"] if row and row["period"] else ""
        if not period:
            return {"period": "", "report": None, "sectors": [], "hs_codes": []}
        report = conn.execute(
            "SELECT * FROM cafe_monthly_generated_reports WHERE period=?",
            (period,),
        ).fetchone()
        sectors = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM cafe_monthly_sector_leadership
                WHERE period=?
                ORDER BY rank_no ASC
                LIMIT ?
                """,
                (period, sector_limit),
            ).fetchall()
        ]
        hs_rows = []
        for r in conn.execute(
            """
            SELECT * FROM cafe_monthly_hs_leadership
            WHERE period=?
            ORDER BY rank_no ASC
            LIMIT ?
            """,
            (period, hs_limit),
        ).fetchall():
            d = dict(r)
            try:
                d["related_companies"] = json.loads(d.get("related_companies") or "[]")
            except Exception:
                d["related_companies"] = []
            hs_rows.append(d)
        return {
            "period": period,
            "report": dict(report) if report else None,
            "sectors": sectors,
            "hs_codes": hs_rows,
        }
    finally:
        conn.close()


@router.get("/quant-mappings")
def get_cafe_quant_indicator_mappings(limit: int = Query(200, ge=1, le=500)):
    conn = _conn()
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT sector_name, mention_count, indicator_key, indicator_name, status,
                   source_system, confidence, mapping_note, updated_at
            FROM cafe_quant_indicator_mappings
            ORDER BY mention_count DESC, sector_name, confidence DESC, indicator_key
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/stock-indicator-mappings")
def get_cafe_stock_indicator_mappings(
    stock_code: str = "",
    q: str = "",
    limit: int = Query(50, ge=1, le=1000),
):
    conn = _conn()
    try:
        _ensure_tables(conn)
        params = []
        where = "WHERE 1=1"
        if stock_code:
            where += " AND m.stock_code=?"
            params.append(stock_code)
        if q:
            where += " AND (m.stock_name LIKE ? OR m.indicator_name LIKE ? OR m.evidence_terms LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like, like])
        rows = conn.execute(
            f"""
            SELECT m.stock_code, m.stock_name, m.sector_name, m.indicator_key, m.indicator_name,
                   m.mention_count, m.evidence_terms, m.example_posts, m.latest_collected_at,
                   m.revenue_exposure_pct, m.profit_exposure_pct, m.cost_exposure_pct,
                   m.exposure_basis, m.importance_level, m.confidence, m.mapping_status,
                   m.mapping_note, m.updated_at, q.sector_name AS signal_sector
            FROM cafe_stock_indicator_mappings m
            LEFT JOIN cafe_quant_indicator_mappings q ON q.indicator_key=m.indicator_key
            {where}
            ORDER BY CASE m.mapping_status
                       WHEN 'confirmed_exposure' THEN 0
                       WHEN 'confirmed_relationship' THEN 1
                       ELSE 2 END,
                     m.mention_count DESC, m.confidence DESC, m.latest_collected_at DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["evidence_terms"] = json.loads(d.get("evidence_terms") or "[]")
            except Exception:
                d["evidence_terms"] = []
            try:
                d["example_posts"] = json.loads(d.get("example_posts") or "[]")
            except Exception:
                d["example_posts"] = []
            d["traffic"] = _latest_indicator_traffic(conn, d["indicator_key"])
            items.append(d)
        return {"items": items, "stock_code": stock_code, "q": q}
    finally:
        conn.close()


@router.get("/quant-indicator-signals")
def get_quant_indicator_signals(
    signal_type: str = "",
    stock_code: str = "",
    limit: int = Query(50, ge=1, le=200),
):
    conn = _conn()
    try:
        _ensure_tables(conn)
        params = []
        where = "WHERE 1=1"
        if signal_type:
            where += " AND signal_type=?"
            params.append(signal_type)
        if stock_code:
            where += " AND related_stocks LIKE ?"
            params.append(f"%\"stock_code\": \"{stock_code}\"%")
        rows = conn.execute(
            f"""
            SELECT id, indicator_key, indicator_name, series_name, period, value, prev_value,
                   mom_pct, yoy_pct, z_score, signal_type, signal_strength,
                   related_stocks, message, generated_at, telegram_sent
            FROM quant_indicator_signal_events
            {where}
            ORDER BY generated_at DESC, signal_strength DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["related_stocks"] = json.loads(d.get("related_stocks") or "[]")
            except Exception:
                d["related_stocks"] = []
            items.append(d)
        return {"items": items}
    finally:
        conn.close()


@router.get("/indicator-traffic-lights")
def get_indicator_traffic_lights(
    limit: int = Query(120, ge=1, le=300),
    only_mapped: bool = False,
):
    conn = _conn()
    try:
        _ensure_tables(conn)
        stock_join = "JOIN cafe_stock_indicator_mappings m ON m.indicator_key=s.indicator_key" if only_mapped else ""
        rows = conn.execute(
            f"""
            SELECT DISTINCT s.indicator_key, s.series_name, c.epic_indicator_name
            FROM quant_major_indicator_series s
            {stock_join}
            LEFT JOIN quant_major_indicator_catalog c ON c.indicator_key=s.indicator_key
            WHERE s.value IS NOT NULL
              AND (
                    s.indicator_key LIKE 'macro:%'
                 OR EXISTS (
                        SELECT 1
                        FROM cafe_quant_indicator_mappings q
                        WHERE q.indicator_key=s.indicator_key
                          AND q.status IN ('ready_existing', 'partial_existing')
                    )
              )
            ORDER BY s.indicator_key, s.series_name
            """
        ).fetchall()
        items = []
        for p in rows:
            series_rows = load_indicator_rows(conn, p["indicator_key"], p["series_name"])
            if len(series_rows) < 3:
                continue
            latest = series_rows[-1]
            prev_candidate = series_rows[-2] if len(series_rows) >= 2 else None
            prev = prev_candidate if prev_candidate and is_consecutive_period(prev_candidate["period"], latest["period"]) else None
            yoy_key = period_yoy_key(latest["period"])
            yoy_row = next((r for r in reversed(series_rows[:-1]) if r["period"] == yoy_key), None) if yoy_key else None
            history = [float(r["value"]) for r in series_rows[:-1] if r["value"] is not None]
            status = classify_traffic_light(
                float(latest["value"]),
                float(prev["value"]) if prev and prev["value"] is not None else None,
                float(yoy_row["value"]) if yoy_row and yoy_row["value"] is not None else None,
                history,
                p["series_name"],
            )
            stocks = related_stocks(conn, p["indicator_key"], limit=6)
            if only_mapped and not stocks:
                continue
            items.append(
                {
                    "indicator_key": p["indicator_key"],
                    "indicator_name": p["epic_indicator_name"] or p["indicator_key"],
                    "series_name": p["series_name"],
                    "period": latest["period"],
                    "value": float(latest["value"]),
                    "unit": latest["unit"],
                    "source_name": latest["source_name"],
                    "source_detail": latest["source_detail"],
                    "quality": latest["quality"],
                    **_period_freshness(latest["period"]),
                    "prev_value": float(prev["value"]) if prev and prev["value"] is not None else None,
                    "related_stocks": stocks,
                    **status,
                }
            )

        priority = {"red": 0, "green": 1, "yellow": 2, "gray": 3}
        items.sort(key=lambda x: (priority.get(x["traffic_light"], 9), -float(x.get("signal_strength") or 0), x["indicator_key"]))
        return {"items": items[:limit], "only_mapped": only_mapped}
    finally:
        conn.close()


@router.get("/sector-traffic-lights")
def get_sector_traffic_lights(limit: int = Query(30, ge=1, le=50)):
    indicator_payload = get_indicator_traffic_lights(limit=300, only_mapped=False)
    indicator_rows = indicator_payload.get("items") or []
    by_indicator: dict[str, list[dict]] = {}
    for item in indicator_rows:
        if not item.get("is_fresh"):
            continue
        by_indicator.setdefault(item["indicator_key"], []).append(item)

    indicator_summary: dict[str, dict] = {}
    for key, rows in by_indicator.items():
        directional = [r for r in rows if r.get("traffic_light") in {"green", "red"}]
        signed = [
            float(r.get("signal_strength") or 0) * (1 if r["traffic_light"] == "green" else -1)
            for r in directional
        ]
        score = sum(signed) / len(signed) if signed else 0.0
        greens = sum(r.get("traffic_light") == "green" for r in rows)
        reds = sum(r.get("traffic_light") == "red" for r in rows)
        strongest = sorted(
            rows,
            key=lambda r: (
                0 if r.get("traffic_light") in {"red", "green"} else 1,
                -float(r.get("signal_strength") or 0),
            ),
        )[0]
        indicator_summary[key] = {
            "indicator_key": key,
            "indicator_name": strongest.get("indicator_name") or key,
            "score": round(score, 3),
            "green_series": greens,
            "red_series": reds,
            "strongest": strongest,
        }

    conn = _conn()
    try:
        _ensure_tables(conn)
        mappings = conn.execute(
            """
            SELECT sector_name, indicator_key, indicator_name, confidence
            FROM cafe_quant_indicator_mappings
            WHERE status IN ('ready_existing', 'partial_existing')
            ORDER BY sector_name, confidence DESC, indicator_key
            """
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in mappings:
            grouped.setdefault(row["sector_name"], []).append(row)

        sectors = []
        for sector_name, sector_mappings in grouped.items():
            summaries = []
            weighted_total = 0.0
            weight_sum = 0.0
            for mapping in sector_mappings:
                summary = indicator_summary.get(mapping["indicator_key"])
                if not summary:
                    continue
                rule = _sector_direction_rule(conn, mapping["indicator_key"], sector_name)
                strongest = _apply_sector_direction(summary["strongest"], rule)
                adjusted_score = _signal_score(strongest)
                adjusted_summary = {
                    **summary,
                    "score": round(adjusted_score, 3),
                    "strongest": strongest,
                    "sector_direction_rule": rule,
                }
                summaries.append(adjusted_summary)
                weight = max(0.3, float(mapping["confidence"] or 0.7))
                weighted_total += adjusted_score * weight
                weight_sum += weight
            score = weighted_total / weight_sum if weight_sum else 0.0
            positive = sum(s["score"] > 0.2 for s in summaries)
            negative = sum(s["score"] < -0.2 for s in summaries)
            caution = sum(s["strongest"].get("traffic_light") == "yellow" for s in summaries)
            mixed = positive > 0 and negative > 0
            if score >= 0.35 and not (mixed and score < 0.75):
                light, label = "green", "좋음"
            elif score <= -0.35 and not (mixed and score > -0.75):
                light, label = "red", "나쁨"
            elif mixed or caution:
                light, label = "yellow", "혼조/주의"
            else:
                light, label = "gray", "중립"

            keys = [r["indicator_key"] for r in sector_mappings]
            placeholders = ",".join("?" for _ in keys)
            stocks = []
            if keys:
                stock_rows = conn.execute(
                    f"""
                    SELECT stock_code, stock_name, sector_name, indicator_key, indicator_name,
                           revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
                           importance_level, mapping_status, confidence, mention_count
                    FROM cafe_stock_indicator_mappings
                    WHERE indicator_key IN ({placeholders})
                    ORDER BY CASE mapping_status
                               WHEN 'confirmed_exposure' THEN 0
                               WHEN 'confirmed_relationship' THEN 1
                               ELSE 2 END,
                             COALESCE(revenue_exposure_pct, profit_exposure_pct, 0) DESC,
                             mention_count DESC, confidence DESC
                    LIMIT 24
                    """,
                    keys,
                ).fetchall()
                seen_codes = set()
                for stock_row in stock_rows:
                    stock = dict(stock_row)
                    if stock["stock_code"] in seen_codes:
                        continue
                    seen_codes.add(stock["stock_code"])
                    stocks.append(stock)
                    if len(stocks) >= 8:
                        break

            top_signals = []
            for summary in sorted(summaries, key=lambda s: -abs(float(s["score"])))[:5]:
                strongest = summary["strongest"]
                top_signals.append(
                    {
                        "indicator_key": summary["indicator_key"],
                        "indicator_name": summary["indicator_name"],
                        "score": summary["score"],
                        "traffic_light": strongest.get("traffic_light"),
                        "series_name": strongest.get("series_name"),
                        "period": strongest.get("period"),
                        "direction_note": strongest.get("direction_note"),
                        "sector_direction_rule": summary.get("sector_direction_rule"),
                    }
                )
            sectors.append(
                {
                    "sector_name": sector_name,
                    "traffic_light": light,
                    "signal_label": label,
                    "sector_score": round(score, 3),
                    "indicator_count": len(summaries),
                    "positive_indicators": positive,
                    "negative_indicators": negative,
                    "caution_indicators": caution,
                    "top_signals": top_signals,
                    "related_stocks": stocks,
                }
            )
        priority = {"red": 0, "green": 1, "yellow": 2, "gray": 3}
        sectors.sort(key=lambda s: (priority.get(s["traffic_light"], 9), -abs(float(s["sector_score"])), s["sector_name"]))
        return {"items": sectors[:limit], "sector_count": len(sectors)}
    finally:
        conn.close()


@router.get("/stock-trade-signals")
def get_stock_trade_signals(limit: int = Query(50, ge=1, le=100)):
    sector_payload = get_sector_traffic_lights(limit=50)
    sector_by_name = {item["sector_name"]: item for item in sector_payload.get("items") or []}
    conn = _conn()
    try:
        from routes.tenbagger import _latest_full_price_date, _price_return_pct, _price_risk

        _ensure_tables(conn)
        price_as_of = _latest_full_price_date(conn)
        rows = conn.execute(
            """
            SELECT m.stock_code, m.stock_name, m.sector_name AS stock_sector,
                   m.indicator_key, m.indicator_name, m.revenue_exposure_pct,
                   m.profit_exposure_pct, m.cost_exposure_pct, m.importance_level,
                   m.mapping_status, m.confidence, q.sector_name AS signal_sector
            FROM cafe_stock_indicator_mappings m
            JOIN cafe_quant_indicator_mappings q
              ON q.indicator_key=m.indicator_key
             AND q.sector_name=m.sector_name
            WHERE m.mapping_status IN ('confirmed_exposure', 'confirmed_relationship', 'confirmed_macro_signal')
              AND q.status IN ('ready_existing', 'partial_existing')
            ORDER BY m.stock_code, m.indicator_key
            """
        ).fetchall()
        traffic_cache: dict[str, dict | None] = {}
        stocks: dict[str, dict] = {}
        for row in rows:
            key = row["indicator_key"]
            if key not in traffic_cache:
                traffic_cache[key] = _latest_indicator_traffic(conn, key)
            traffic = traffic_cache[key]
            if not traffic or not traffic.get("is_fresh") or traffic.get("traffic_light") not in {"green", "red"}:
                continue
            traffic = _apply_sector_direction(
                traffic,
                _sector_direction_rule(conn, key, row["signal_sector"] or row["stock_sector"] or ""),
            )
            if traffic.get("traffic_light") not in {"green", "red"}:
                continue
            direction = 1.0 if traffic["traffic_light"] == "green" else -1.0
            relation_weight = (
                1.0 if row["mapping_status"] == "confirmed_exposure"
                else 0.68 if row["mapping_status"] == "confirmed_macro_signal"
                else 0.75
            )
            exposure = row["revenue_exposure_pct"] if row["revenue_exposure_pct"] is not None else row["profit_exposure_pct"]
            exposure_weight = min(1.5, max(0.35, float(exposure) / 50.0)) if exposure is not None else 0.75
            quality = str(traffic.get("quality") or "")
            quality_weight = 0.6 if "proxy" in quality else 0.72 if "partial" in quality else 1.0
            strength = max(0.5, float(traffic.get("signal_strength") or 0))
            contribution = direction * strength * relation_weight * exposure_weight * quality_weight

            sector = sector_by_name.get(row["signal_sector"]) or {}
            sector_light = sector.get("traffic_light")
            if sector_light == traffic["traffic_light"]:
                contribution *= 1.15
            elif sector_light in {"green", "red"} and sector_light != traffic["traffic_light"]:
                contribution *= 0.7

            stock = stocks.setdefault(
                row["stock_code"],
                {
                    "stock_code": row["stock_code"],
                    "stock_name": row["stock_name"],
                    "stock_sector": row["stock_sector"],
                    "score": 0.0,
                    "positive_drivers": 0,
                    "negative_drivers": 0,
                    "drivers": [],
                },
            )
            stock["score"] += contribution
            stock["positive_drivers" if direction > 0 else "negative_drivers"] += 1
            stock["drivers"].append(
                {
                    "indicator_key": key,
                    "indicator_name": row["indicator_name"],
                    "sector_name": row["signal_sector"],
                    "traffic_light": traffic["traffic_light"],
                    "signal_label": traffic["signal_label"],
                    "series_name": traffic["series_name"],
                    "period": traffic["period"],
                    "quality": quality,
                    "mapping_status": row["mapping_status"],
                    "revenue_exposure_pct": row["revenue_exposure_pct"],
                    "profit_exposure_pct": row["profit_exposure_pct"],
                    "contribution": round(contribution, 3),
                }
            )

        items = []
        for stock in stocks.values():
            sector_seen: dict[str, int] = {}
            adjusted_score = 0.0
            for driver in sorted(stock["drivers"], key=lambda d: -abs(float(d["contribution"]))):
                sector_name = driver["sector_name"]
                occurrence = sector_seen.get(sector_name, 0)
                decay = 1.0 if occurrence == 0 else 0.45 if occurrence == 1 else 0.25
                driver["raw_contribution"] = driver["contribution"]
                driver["contribution"] = round(float(driver["contribution"]) * decay, 3)
                adjusted_score += driver["contribution"]
                sector_seen[sector_name] = occurrence + 1
            score = adjusted_score
            conflict = stock["positive_drivers"] > 0 and stock["negative_drivers"] > 0
            if score >= 2.0 and not conflict:
                action, light = "매수 후보", "green"
            elif score <= -2.0 and not conflict:
                action, light = "매도/위험", "red"
            else:
                action, light = "관찰", "yellow" if conflict or abs(score) >= 0.8 else "gray"
            stock["score"] = round(score, 3)
            stock["action"] = action
            stock["traffic_light"] = light
            stock["drivers"].sort(key=lambda d: -abs(float(d["contribution"])))
            market_rows = conn.execute(
                """
                SELECT date, close, volume, trade_amount, frn_net_buy_amt, inst_net_buy_amt
                FROM price_history
                WHERE stock_code=? AND close IS NOT NULL AND close>0
                ORDER BY date DESC LIMIT 61
                """,
                (stock["stock_code"],),
            ).fetchall()
            if market_rows:
                latest = market_rows[0]
                closes = [float(r["close"]) for r in market_rows]
                volumes = [float(r["volume"] or 0) for r in market_rows]
                ma20 = sum(closes[:20]) / min(20, len(closes))
                ma60 = sum(closes[:60]) / min(60, len(closes))
                avg20_volume = sum(volumes[1:21]) / len(volumes[1:21]) if len(volumes) > 1 else 0
                volume_ratio = volumes[0] / avg20_volume if avg20_volume > 0 else None
                flow_rows = market_rows[:5]
                frn_5d = sum(float(r["frn_net_buy_amt"] or 0) for r in flow_rows) / 100.0
                inst_5d = sum(float(r["inst_net_buy_amt"] or 0) for r in flow_rows) / 100.0
                checks = {
                    "price_above_ma20": closes[0] >= ma20,
                    "ma20_above_ma60": ma20 >= ma60,
                    "volume_expansion": volume_ratio is not None and volume_ratio >= 1.2,
                    "positive_flow_5d": frn_5d + inst_5d > 0,
                }
                confirmation_score = sum(bool(value) for value in checks.values())
                stock["market_confirmation"] = {
                    "as_of": latest["date"],
                    "close": closes[0],
                    "ma20": round(ma20, 2),
                    "ma60": round(ma60, 2),
                    "volume_ratio_20d": round(volume_ratio, 2) if volume_ratio is not None else None,
                    "foreign_5d_억": round(frn_5d, 2),
                    "institution_5d_억": round(inst_5d, 2),
                    "score": confirmation_score,
                    "label": "시장 확인" if confirmation_score >= 3 else "부분 확인" if confirmation_score >= 2 else "미확인",
                    "checks": checks,
                }
            else:
                stock["market_confirmation"] = None
            if price_as_of:
                ret_1m = _price_return_pct(conn, stock["stock_code"], price_as_of, 30)
                ret_3m = _price_return_pct(conn, stock["stock_code"], price_as_of, 90)
                price_risk = _price_risk(ret_1m, ret_3m)
                stock["price_as_of"] = price_as_of
                stock["price_return_1m"] = round(ret_1m, 1) if ret_1m is not None else None
                stock["price_return_3m"] = round(ret_3m, 1) if ret_3m is not None else None
                stock.update(price_risk)
                base_score = float(stock["score"])
                risk_penalty = float(price_risk["price_risk_penalty"]) / 10.0
                stock["risk_adjusted_score"] = round(
                    max(0.0, base_score - risk_penalty) if base_score >= 0 else base_score - risk_penalty,
                    3,
                )
                if stock["action"] == "매수 후보" and price_risk["price_risk"] in {"WATCH_PRICE", "AVOID"}:
                    stock["action"] = "관찰"
                    stock["traffic_light"] = "yellow"
                    stock["price_risk_note"] = "지표는 우호적이나 최근 가격 급락으로 매수 후보에서 관찰로 조정"
            items.append(stock)
        priority = {"매도/위험": 0, "매수 후보": 1, "관찰": 2}
        items.sort(key=lambda item: (priority[item["action"]], -abs(float(item["score"])), item["stock_name"]))
        return {
            "items": items[:limit],
            "counts": {
                "buy": sum(item["action"] == "매수 후보" for item in items),
                "sell_risk": sum(item["action"] == "매도/위험" for item in items),
                "watch": sum(item["action"] == "관찰" for item in items),
            },
            "policy": "확인된 비중/직접 관계 + 지표 신호 + 섹터 정합성 + 원천 품질",
        }
    finally:
        conn.close()


@router.get("/stock-trade-signal-performance")
def get_stock_trade_signal_performance(limit: int = Query(100, ge=1, le=500)):
    conn = _conn()
    try:
        _ensure_tables(conn)
        snapshots = conn.execute(
            """
            SELECT signal_date, stock_code, stock_name, action, score, generated_at
            FROM quant_stock_trade_signal_snapshots
            ORDER BY signal_date DESC, ABS(score) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        horizons = (5, 20, 60, 120)
        items = []
        for snapshot in snapshots:
            base = conn.execute(
                """
                SELECT date, close FROM price_history
                WHERE stock_code=? AND date<=? AND close IS NOT NULL AND close>0
                ORDER BY date DESC LIMIT 1
                """,
                (snapshot["stock_code"], snapshot["signal_date"]),
            ).fetchone()
            if not base:
                continue
            prices = conn.execute(
                """
                SELECT date, close, high, low FROM price_history
                WHERE stock_code=? AND date>=? AND close IS NOT NULL AND close>0
                ORDER BY date ASC
                """,
                (snapshot["stock_code"], base["date"]),
            ).fetchall()
            base_price = float(base["close"])
            metrics = {}
            for horizon in horizons:
                target = prices[horizon] if len(prices) > horizon else None
                window = prices[1 : min(len(prices), horizon + 1)]
                metrics[f"return_{horizon}d_pct"] = round((float(target["close"]) / base_price - 1) * 100, 2) if target else None
                metrics[f"mfe_{horizon}d_pct"] = round((max(float(p["high"] or p["close"]) for p in window) / base_price - 1) * 100, 2) if window else None
                metrics[f"mae_{horizon}d_pct"] = round((min(float(p["low"] or p["close"]) for p in window) / base_price - 1) * 100, 2) if window else None
            direction = -1 if snapshot["action"] == "매도/위험" else 1
            completed = sum(metrics[f"return_{h}d_pct"] is not None for h in horizons)
            items.append({
                **dict(snapshot),
                "base_date": base["date"],
                "base_price": base_price,
                "trading_days_elapsed": max(0, len(prices) - 1),
                "completed_horizons": completed,
                "evaluation_status": "평가중" if completed < len(horizons) else "평가완료",
                "direction": direction,
                **metrics,
            })
        summary = {}
        for horizon in horizons:
            evaluated = [item for item in items if item[f"return_{horizon}d_pct"] is not None]
            directional = [float(item[f"return_{horizon}d_pct"]) * item["direction"] for item in evaluated]
            summary[f"{horizon}d"] = {
                "samples": len(evaluated),
                "avg_directional_return_pct": round(sum(directional) / len(directional), 2) if directional else None,
                "hit_rate_pct": round(sum(value > 0 for value in directional) / len(directional) * 100, 2) if directional else None,
            }
        return {"items": items, "summary": summary, "horizons": list(horizons)}
    finally:
        conn.close()


@router.get("/macro-signal-backtests")
def get_macro_signal_backtests(limit: int = Query(50, ge=1, le=200), passed_only: bool = Query(False)):
    conn = _conn()
    try:
        _ensure_tables(conn)
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='macro_signal_backtest_results'"
        ).fetchone()
        if not table_exists:
            return {"items": [], "summary": {}, "run_id": None}
        latest = conn.execute(
            "SELECT run_id, MAX(created_at) AS created_at FROM macro_signal_backtest_results GROUP BY run_id ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return {"items": [], "summary": {}, "run_id": None}
        where = "WHERE r.run_id=?"
        params: list = [latest["run_id"]]
        if passed_only:
            where += " AND r.pass_flag=1"
        rows = conn.execute(
            f"""
            SELECT r.run_id, r.indicator_key, COALESCE(c.epic_indicator_name, r.indicator_name, r.indicator_key) AS indicator_name,
                   r.sector_name, r.direction_mode,
                   event_count, observation_count, stock_count,
                   avg_ret_20d, median_ret_20d, hit_rate_20d,
                   avg_ret_60d, median_ret_60d, hit_rate_60d,
                   avg_ret_120d, median_ret_120d, hit_rate_120d,
                   avg_mdd_60d, profit_factor_60d, pass_flag, promotion_status,
                   r.created_at
            FROM macro_signal_backtest_results r
            LEFT JOIN quant_major_indicator_catalog c ON c.indicator_key=r.indicator_key
            {where}
            ORDER BY pass_flag DESC, avg_ret_60d DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        summary_row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(pass_flag=1) AS passed,
                   SUM(promotion_status='promoted') AS promoted,
                   SUM(observation_count) AS observations
            FROM macro_signal_backtest_results
            WHERE run_id=?
            """,
            (latest["run_id"],),
        ).fetchone()
        return {
            "run_id": latest["run_id"],
            "created_at": latest["created_at"],
            "summary": dict(summary_row) if summary_row else {},
            "items": [dict(row) for row in rows],
        }
    finally:
        conn.close()


@router.post("/collect")
def trigger_cafe_signal_collect(payload: dict = {}):
    run_type = payload.get("run_type", "weekly")
    max_pages = int(payload.get("max_pages") or 3)

    def _run() -> None:
        cmd = [
            str(PYTHON if PYTHON.exists() else sys.executable),
            str(PIPELINE),
            "--collect",
            "--max-pages",
            str(max_pages),
            "--run-type",
            run_type if run_type in {"weekly", "monthly"} else "weekly",
        ]
        subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        subprocess.run(
            [str(PYTHON if PYTHON.exists() else sys.executable), str(ROOT / "scripts" / "ops" / "generate_cafe_monthly_leadership.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        subprocess.run(
            [str(PYTHON if PYTHON.exists() else sys.executable), str(ROOT / "scripts" / "ops" / "sync_cafe_quant_mappings.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        subprocess.run(
            [str(PYTHON if PYTHON.exists() else sys.executable), str(ROOT / "scripts" / "ops" / "sync_cafe_stock_indicator_mappings.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            [str(PYTHON if PYTHON.exists() else sys.executable), str(ROOT / "scripts" / "ops" / "quant_indicator_signal_engine.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            [
                str(PYTHON if PYTHON.exists() else sys.executable),
                str(ROOT / "scripts" / "ops" / "backtest_macro_indicator_candidates.py"),
                "--min-obs",
                "30",
                "--promote",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "collecting", "run_type": run_type, "max_pages": max_pages}
