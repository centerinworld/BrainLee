#!/usr/bin/env python3
"""Generate local, no-BigQuery trigger-discovery insight reports.

The goal is deliberately modest: use the point-in-time trigger_discovery_* tables
already built in SQLite, summarize which triggers have historically worked, and
surface recent candidates without running any BigQuery refresh/query.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def pct(v: float | None, digits: int = 1) -> float | None:
    if v is None:
        return None
    return round(float(v) * 100, digits)


def fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "-"
    return f"{float(v):+.{digits}f}%"


def build_scorecards(conn: sqlite3.Connection, horizon: int = 60, min_n: int = 50) -> tuple[list[dict], list[dict]]:
    trigger_rows = rows(
        conn,
        """
        SELECT e.trigger_key, e.trigger_name, e.source, fr.horizon_days,
               COUNT(*) AS n,
               COUNT(DISTINCT fr.stock_code) AS stocks,
               AVG(fr.return_pct) AS avg_return_pct,
               SUM(CASE WHEN fr.return_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS positive_rate,
               SUM(CASE WHEN fr.return_pct >= 50 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS gain50_rate,
               SUM(CASE WHEN fr.return_pct <= -30 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS loss30_rate,
               AVG(fr.max_drawdown_pct) AS avg_mdd_pct,
               SUM(CASE WHEN fr.return_pct > 0 THEN fr.return_pct ELSE 0 END) AS gross_gain,
               ABS(SUM(CASE WHEN fr.return_pct < 0 THEN fr.return_pct ELSE 0 END)) AS gross_loss
        FROM trigger_discovery_events e
        JOIN trigger_discovery_forward_returns fr ON fr.event_id = e.event_id
        JOIN trigger_discovery_stock_links l
          ON l.event_id = fr.event_id AND l.stock_code = fr.stock_code
        WHERE fr.horizon_days = ?
          AND fr.return_pct IS NOT NULL
          AND COALESCE(l.confidence, 0) >= 0.35
        GROUP BY e.trigger_key, e.trigger_name, e.source, fr.horizon_days
        HAVING COUNT(*) >= ?
        """,
        (horizon, min_n),
    )
    for r in trigger_rows:
        loss = float(r["gross_loss"] or 0)
        r["profit_factor"] = round(float(r["gross_gain"] or 0) / loss, 2) if loss > 0 else None
        r["positive_rate_pct"] = pct(r.pop("positive_rate"))
        r["gain50_rate_pct"] = pct(r.pop("gain50_rate"))
        r["loss30_rate_pct"] = pct(r.pop("loss30_rate"))
        r["avg_return_pct"] = round(float(r["avg_return_pct"] or 0), 2)
        r["avg_mdd_pct"] = round(float(r["avg_mdd_pct"] or 0), 2)
    trigger_rows.sort(
        key=lambda r: (
            r["avg_return_pct"],
            r["profit_factor"] or 0,
            r["positive_rate_pct"] or 0,
            r["n"],
        ),
        reverse=True,
    )

    sector_rows = rows(
        conn,
        """
        SELECT COALESCE(l.sector_name, e.sector_name, '미분류') AS sector_name,
               e.trigger_key, e.trigger_name, fr.horizon_days,
               COUNT(*) AS n,
               COUNT(DISTINCT fr.stock_code) AS stocks,
               AVG(fr.return_pct) AS avg_return_pct,
               SUM(CASE WHEN fr.return_pct > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS positive_rate,
               SUM(CASE WHEN fr.return_pct >= 50 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS gain50_rate,
               SUM(CASE WHEN fr.return_pct <= -30 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS loss30_rate,
               SUM(CASE WHEN fr.return_pct > 0 THEN fr.return_pct ELSE 0 END) AS gross_gain,
               ABS(SUM(CASE WHEN fr.return_pct < 0 THEN fr.return_pct ELSE 0 END)) AS gross_loss
        FROM trigger_discovery_events e
        JOIN trigger_discovery_forward_returns fr ON fr.event_id = e.event_id
        JOIN trigger_discovery_stock_links l
          ON l.event_id = fr.event_id AND l.stock_code = fr.stock_code
        WHERE fr.horizon_days = ?
          AND fr.return_pct IS NOT NULL
          AND COALESCE(l.confidence, 0) >= 0.35
        GROUP BY COALESCE(l.sector_name, e.sector_name, '미분류'), e.trigger_key, e.trigger_name, fr.horizon_days
        HAVING COUNT(*) >= ?
        """,
        (horizon, max(20, min_n // 2)),
    )
    for r in sector_rows:
        loss = float(r["gross_loss"] or 0)
        r["profit_factor"] = round(float(r["gross_gain"] or 0) / loss, 2) if loss > 0 else None
        r["positive_rate_pct"] = pct(r.pop("positive_rate"))
        r["gain50_rate_pct"] = pct(r.pop("gain50_rate"))
        r["loss30_rate_pct"] = pct(r.pop("loss30_rate"))
        r["avg_return_pct"] = round(float(r["avg_return_pct"] or 0), 2)
    sector_rows.sort(
        key=lambda r: (
            r["avg_return_pct"],
            r["profit_factor"] or 0,
            r["positive_rate_pct"] or 0,
            r["n"],
        ),
        reverse=True,
    )
    return trigger_rows, sector_rows


def recent_candidates(
    conn: sqlite3.Connection,
    trigger_scorecard: list[dict],
    sector_scorecard: list[dict],
    lookback_days: int = 45,
    min_exposure_pct: float = 5.0,
    limit: int = 80,
) -> list[dict]:
    trigger_stats = {r["trigger_key"]: r for r in trigger_scorecard}
    sector_stats = {(r["sector_name"], r["trigger_key"]): r for r in sector_scorecard}
    latest = rows(conn, "SELECT MAX(available_date) AS latest_date FROM trigger_discovery_events")[0]["latest_date"]
    raw = rows(
        conn,
        """
        SELECT e.available_date, e.event_date, e.trigger_key, e.trigger_name, e.source,
               e.direction, e.strength, e.yoy_pct, e.mom_pct,
               l.stock_code, l.stock_name, COALESCE(l.sector_name, e.sector_name, '미분류') AS sector_name,
               COALESCE(l.confidence, 0) AS confidence,
               l.revenue_exposure_pct, l.profit_exposure_pct, l.cost_exposure_pct
        FROM trigger_discovery_events e
        JOIN trigger_discovery_stock_links l ON l.event_id = e.event_id
        WHERE DATE(e.available_date) >= DATE(?, ?)
          AND DATE(e.available_date) <= DATE(?)
          AND COALESCE(l.confidence, 0) >= 0.35
        """,
        (latest, f"-{lookback_days} days", latest),
    )
    best_by_stock_trigger: dict[tuple[str, str], dict] = {}
    for r in raw:
        hist = trigger_stats.get(r["trigger_key"])
        sec = sector_stats.get((r["sector_name"], r["trigger_key"]))
        if not hist:
            continue
        exposure = max(
            float(r["revenue_exposure_pct"] or 0),
            float(r["profit_exposure_pct"] or 0),
            float(r["cost_exposure_pct"] or 0),
        )
        if exposure < min_exposure_pct:
            continue
        score = (
            float(hist["avg_return_pct"] or 0) * 0.50
            + float(hist["positive_rate_pct"] or 0) * 0.18
            + float(hist["gain50_rate_pct"] or 0) * 0.25
            - float(hist["loss30_rate_pct"] or 0) * 0.35
            + min(float(hist["profit_factor"] or 0), 8.0) * 1.8
            + float(r["confidence"] or 0) * 8.0
            + min(exposure, 80.0) * 0.08
        )
        if sec:
            score += float(sec["avg_return_pct"] or 0) * 0.20
            score += min(float(sec["profit_factor"] or 0), 8.0) * 0.8
        out = {
            **r,
            "historical_60d_avg_pct": hist["avg_return_pct"],
            "historical_60d_positive_pct": hist["positive_rate_pct"],
            "historical_60d_gain50_pct": hist["gain50_rate_pct"],
            "historical_60d_loss30_pct": hist["loss30_rate_pct"],
            "historical_60d_pf": hist["profit_factor"],
            "sector_60d_avg_pct": sec["avg_return_pct"] if sec else None,
            "insight_score": round(score, 2),
        }
        key = (r["stock_code"], r["trigger_key"])
        if key not in best_by_stock_trigger or out["insight_score"] > best_by_stock_trigger[key]["insight_score"]:
            best_by_stock_trigger[key] = out
    candidates = sorted(best_by_stock_trigger.values(), key=lambda r: r["insight_score"], reverse=True)
    return candidates[:limit]


def write_reports(payload: dict) -> tuple[Path, Path]:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = OUT_DIR / f"trigger_discovery_insights_{stamp}.json"
    md_path = OUT_DIR / f"trigger_discovery_insights_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Trigger Discovery Local Insights — {stamp}",
        "",
        "BigQuery refresh/query를 사용하지 않고 로컬 SQLite `trigger_discovery_*` 테이블만으로 생성한 인사이트입니다.",
        "",
        "## 데이터 상태",
    ]
    s = payload["summary"]
    lines += [
        f"- 이벤트: {s['events']:,}건",
        f"- 종목 연결: {s['links']:,}건",
        f"- forward return: {s['forward_returns']:,}건",
        f"- available_date: {s['available_min']} ~ {s['available_max']}",
        "",
        "## 60일 기준 상위 트리거",
        "|순위|트리거|표본|종목|평균수익|양수비율|+50%비율|-30%비율|PF|",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(payload["top_triggers_60d"][:20], 1):
        lines.append(
            f"|{i}|{r['trigger_name']}|{r['n']:,}|{r['stocks']:,}|"
            f"{fmt_pct(r['avg_return_pct'])}|{r['positive_rate_pct']:.1f}%|"
            f"{r['gain50_rate_pct']:.1f}%|{r['loss30_rate_pct']:.1f}%|{r['profit_factor'] or 0:.2f}|"
        )
    lines += [
        "",
        "## 60일 기준 상위 섹터-트리거",
        "|순위|섹터|트리거|표본|평균수익|양수비율|+50%비율|-30%비율|PF|",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(payload["top_sector_triggers_60d"][:20], 1):
        lines.append(
            f"|{i}|{r['sector_name']}|{r['trigger_name']}|{r['n']:,}|"
            f"{fmt_pct(r['avg_return_pct'])}|{r['positive_rate_pct']:.1f}%|"
            f"{r['gain50_rate_pct']:.1f}%|{r['loss30_rate_pct']:.1f}%|{r['profit_factor'] or 0:.2f}|"
        )
    lines += [
        "",
        "## 최근 45일 종목 후보",
        "",
        "종목 후보는 매출/이익/비용 노출도 중 하나가 5% 이상인 경우만 표시합니다. 노출도 0%인 거시 배경 신호는 매수 후보가 아니라 시장 환경으로만 해석합니다.",
        "",
        "|순위|available|종목|섹터|트리거|점수|과거60일 평균|양수비율|PF|노출도|",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for i, r in enumerate(payload["recent_candidates"][:30], 1):
        exposure = max(
            float(r["revenue_exposure_pct"] or 0),
            float(r["profit_exposure_pct"] or 0),
            float(r["cost_exposure_pct"] or 0),
        )
        lines.append(
            f"|{i}|{r['available_date']}|{r['stock_name']}({r['stock_code']})|{r['sector_name']}|"
            f"{r['trigger_name']}|{r['insight_score']:.1f}|{fmt_pct(r['historical_60d_avg_pct'])}|"
            f"{r['historical_60d_positive_pct']:.1f}%|{r['historical_60d_pf'] or 0:.2f}|{exposure:.1f}%|"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    conn = sqlite3.connect(DB, timeout=60)
    summary = rows(
        conn,
        """
        SELECT
          (SELECT COUNT(*) FROM trigger_discovery_events) AS events,
          (SELECT COUNT(*) FROM trigger_discovery_stock_links) AS links,
          (SELECT COUNT(*) FROM trigger_discovery_forward_returns) AS forward_returns,
          (SELECT MIN(available_date) FROM trigger_discovery_events) AS available_min,
          (SELECT MAX(available_date) FROM trigger_discovery_events) AS available_max
        """,
    )[0]
    triggers60, sectors60 = build_scorecards(conn, horizon=60, min_n=50)
    triggers120, sectors120 = build_scorecards(conn, horizon=120, min_n=50)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "local_sqlite_no_bigquery",
        "summary": summary,
        "top_triggers_60d": triggers60[:100],
        "top_triggers_120d": triggers120[:100],
        "top_sector_triggers_60d": sectors60[:100],
        "top_sector_triggers_120d": sectors120[:100],
        "recent_candidates": recent_candidates(conn, triggers60, sectors60),
    }
    json_path, md_path = write_reports(payload)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
