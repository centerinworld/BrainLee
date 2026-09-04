#!/usr/bin/env python3
"""Audit segment revenue and mezzanine/dilution risk coverage.

This report is meant to be boring and blunt: it separates true segment
breakdowns from consolidated totals, and separates issuance rows with usable
amounts from rows that are only event flags.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def pct(n: float, d: float) -> float:
    return round(n * 100.0 / d, 2) if d else 0.0


def main() -> None:
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row

    universe_sql = """
        SELECT stock_code, stock_name, market, market_cap
        FROM stock_universe
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
          AND COALESCE(secugrp_nm,'') NOT LIKE '%ETF%'
          AND COALESCE(isu_full_name,'') NOT LIKE '%ETN%'
          AND COALESCE(kind_stkcert_nm,'') LIKE '%보통%'
    """
    universe = scalar(conn, f"SELECT COUNT(*) FROM ({universe_sql})")
    segment_total_stocks = scalar(conn, "SELECT COUNT(DISTINCT stock_code) FROM segment_revenue WHERE revenue IS NOT NULL")
    segment_breakdown_stocks = scalar(
        conn,
        """
        SELECT COUNT(DISTINCT stock_code)
        FROM segment_revenue
        WHERE segment_name != '연결전체' AND revenue IS NOT NULL AND revenue > 0
        """,
    )
    segment_pct_rows = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM segment_revenue
        WHERE segment_name != '연결전체' AND revenue_pct IS NOT NULL
        """,
    )

    missing_top_segment = fetchall(
        conn,
        f"""
        WITH u AS ({universe_sql}),
        sr AS (
          SELECT DISTINCT stock_code
          FROM segment_revenue
          WHERE segment_name != '연결전체' AND revenue IS NOT NULL AND revenue > 0
        )
        SELECT u.stock_code, u.stock_name, u.market, u.market_cap
        FROM u
        LEFT JOIN sr ON sr.stock_code = u.stock_code
        WHERE sr.stock_code IS NULL
        ORDER BY u.market_cap DESC NULLS LAST
        LIMIT 80
        """,
    )

    segment_by_year = fetchall(
        conn,
        """
        SELECT year,
               COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks,
               SUM(CASE WHEN revenue_pct IS NOT NULL THEN 1 ELSE 0 END) AS pct_rows
        FROM segment_revenue
        WHERE segment_name != '연결전체'
        GROUP BY year
        ORDER BY year
        """,
    )

    dilution_summary = fetchall(
        conn,
        """
        SELECT event_type,
               COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks,
               SUM(CASE WHEN issue_amount IS NOT NULL AND issue_amount > 0 THEN 1 ELSE 0 END) AS amount_rows,
               SUM(CASE WHEN dilution_pct IS NOT NULL AND dilution_pct > 0 THEN 1 ELSE 0 END) AS dilution_rows,
               SUM(CASE WHEN put_option_date IS NOT NULL AND put_option_date != '' THEN 1 ELSE 0 END) AS put_rows,
               MIN(disclosed_at) AS min_date,
               MAX(disclosed_at) AS max_date
        FROM dilution_events
        GROUP BY event_type
        ORDER BY rows DESC
        """,
    )
    for r in dilution_summary:
        r["amount_pct"] = pct(r["amount_rows"] or 0, r["rows"] or 0)
        r["dilution_pct_coverage"] = pct(r["dilution_rows"] or 0, r["rows"] or 0)
        r["put_option_pct"] = pct(r["put_rows"] or 0, r["rows"] or 0)

    dilution_by_source = fetchall(
        conn,
        """
        SELECT COALESCE(data_source,'') AS data_source,
               COUNT(*) AS rows,
               SUM(CASE WHEN issue_amount IS NOT NULL AND issue_amount > 0 THEN 1 ELSE 0 END) AS amount_rows
        FROM dilution_events
        GROUP BY COALESCE(data_source,'')
        ORDER BY rows DESC
        """,
    )
    for r in dilution_by_source:
        r["amount_pct"] = pct(r["amount_rows"] or 0, r["rows"] or 0)

    dilution_quality = fetchall(
        conn,
        """
        SELECT COALESCE(risk_amount_status, 'unclassified') AS risk_amount_status,
               COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks
        FROM dilution_events
        GROUP BY COALESCE(risk_amount_status, 'unclassified')
        ORDER BY rows DESC
        """,
    )
    dilution_buckets = fetchall(
        conn,
        """
        SELECT COALESCE(risk_event_bucket, 'unclassified') AS risk_event_bucket,
               COUNT(*) AS rows,
               COUNT(DISTINCT stock_code) AS stocks
        FROM dilution_events
        GROUP BY COALESCE(risk_event_bucket, 'unclassified')
        ORDER BY rows DESC
        """,
    )

    missing_amount = fetchall(
        conn,
        """
        SELECT stock_code, stock_name, event_type, disclosed_at, report_nm, data_source, rcept_no
        FROM dilution_events
        WHERE event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS')
          AND (issue_amount IS NULL OR issue_amount <= 0)
        ORDER BY disclosed_at DESC
        LIMIT 120
        """,
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "universe": universe,
            "segment_any_stocks": segment_total_stocks,
            "segment_breakdown_stocks": segment_breakdown_stocks,
            "segment_breakdown_pct": pct(segment_breakdown_stocks, universe),
            "segment_rows_with_revenue_pct": segment_pct_rows,
        },
        "segment_by_year": segment_by_year,
        "missing_top_segment": missing_top_segment,
        "dilution_summary": dilution_summary,
        "dilution_by_source": dilution_by_source,
        "dilution_quality": dilution_quality,
        "dilution_buckets": dilution_buckets,
        "missing_dilution_amount_examples": missing_amount,
    }

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    json_path = OUT_DIR / f"segment_dilution_coverage_{stamp}.json"
    md_path = OUT_DIR / f"segment_dilution_coverage_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Segment + Dilution Coverage Audit — {stamp}",
        "",
        "## Summary",
        f"- Universe: {universe:,}",
        f"- Segment any data stocks: {segment_total_stocks:,}",
        f"- Segment breakdown stocks: {segment_breakdown_stocks:,} ({payload['summary']['segment_breakdown_pct']}%)",
        f"- Segment rows with explicit revenue_pct: {segment_pct_rows:,}",
        "",
        "## Segment Breakdown By Year",
        "|year|rows|stocks|rows with revenue_pct|",
        "|---:|---:|---:|---:|",
    ]
    for r in segment_by_year:
        lines.append(f"|{r['year']}|{r['rows']:,}|{r['stocks']:,}|{r['pct_rows']:,}|")
    lines += [
        "",
        "## Dilution Events",
        "|event_type|rows|stocks|amount rows|amount %|dilution rows|dilution %|put rows|date range|",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in dilution_summary:
        lines.append(
            f"|{r['event_type']}|{r['rows']:,}|{r['stocks']:,}|{r['amount_rows']:,}|{r['amount_pct']}%|"
            f"{r['dilution_rows']:,}|{r['dilution_pct_coverage']}%|{r['put_rows']:,}|{r['min_date']} ~ {r['max_date']}|"
        )
    lines += [
        "",
        "## Dilution By Source",
        "|source|rows|amount rows|amount %|",
        "|---|---:|---:|---:|",
    ]
    for r in dilution_by_source:
        lines.append(f"|{r['data_source']}|{r['rows']:,}|{r['amount_rows']:,}|{r['amount_pct']}%|")
    lines += [
        "",
        "## Dilution Risk Amount Status",
        "|status|rows|stocks|",
        "|---|---:|---:|",
    ]
    for r in dilution_quality:
        lines.append(f"|{r['risk_amount_status']}|{r['rows']:,}|{r['stocks']:,}|")
    lines += [
        "",
        "## Dilution Event Buckets",
        "|bucket|rows|stocks|",
        "|---|---:|---:|",
    ]
    for r in dilution_buckets:
        lines.append(f"|{r['risk_event_bucket']}|{r['rows']:,}|{r['stocks']:,}|")
    lines += [
        "",
        "## Missing Segment Top Market Cap",
        "|stock|market|market_cap|",
        "|---|---|---:|",
    ]
    for r in missing_top_segment[:30]:
        lines.append(f"|{r['stock_name']}({r['stock_code']})|{r['market']}|{r['market_cap'] or 0:,.0f}|")
    lines += [
        "",
        "## Missing Dilution Amount Examples",
        "|date|stock|type|source|report|rcept_no|",
        "|---|---|---|---|---|---|",
    ]
    for r in missing_amount[:30]:
        report = str(r["report_nm"] or "").replace("|", "/")
        lines.append(
            f"|{r['disclosed_at']}|{r['stock_name']}({r['stock_code']})|{r['event_type']}|"
            f"{r['data_source']}|{report}|{r['rcept_no']}|"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "summary": payload["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
