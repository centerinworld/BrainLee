#!/usr/bin/env python3
"""Audit US financial-statement / cash-flow coverage against the US ticker universe.

The Korean side has a multi-layer DART/FnGuide verification stack (CLAUDE.md
§9-1); the US side only had on-demand SEC EDGAR backfill triggered per ticker
view with no standing coverage report. This is the missing report: how many
US tickers actually have usable annual/quarterly financials and cash flow,
which key fields are NULL even when a row exists, and how stale the newest
row is. Boring and blunt on purpose — no scoring, just counts.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db_compat import connect_primary_db

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "research_outputs"
DOC_PATH = ROOT / "docs" / "us_financial_coverage_audit_latest.md"

FIN_KEY_FIELDS = ["revenue", "operating_income", "net_income", "eps", "bps"]
CF_KEY_FIELDS = ["operating_cf", "investing_cf", "financing_cf", "capex", "free_cf"]


def fetchall(conn, sql: str, params: tuple = ()) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(conn, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def pct(n: float, d: float) -> float:
    return round(n * 100.0 / d, 2) if d else 0.0


def null_rate(conn, table: str, period_type: str, field: str) -> dict:
    total = scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE period_type=?", (period_type,))
    non_null = scalar(
        conn, f"SELECT COUNT(*) FROM {table} WHERE period_type=? AND {field} IS NOT NULL", (period_type,)
    )
    return {"field": field, "rows": total, "non_null": non_null, "null_pct": pct((total or 0) - (non_null or 0), total)}


def main() -> None:
    conn = connect_primary_db(readonly=True, timeout=60)

    universe = scalar(conn, "SELECT COUNT(*) FROM us_stock_meta WHERE UPPER(COALESCE(country,'US'))='US'")

    coverage = {}
    for table, key_fields in (("us_financial_data", FIN_KEY_FIELDS), ("us_cashflow_data", CF_KEY_FIELDS)):
        section = {}
        for period_type in ("annual", "quarter"):
            tickers_with_rows = scalar(
                conn, f"SELECT COUNT(DISTINCT ticker) FROM {table} WHERE period_type=?", (period_type,)
            )
            tickers_with_4plus = scalar(
                conn,
                f"""SELECT COUNT(*) FROM (
                        SELECT ticker FROM {table} WHERE period_type=?
                        GROUP BY ticker HAVING COUNT(*) >= 4
                    ) t""",
                (period_type,),
            )
            newest_period = scalar(conn, f"SELECT MAX(period_end) FROM {table} WHERE period_type=?", (period_type,))
            section[period_type] = {
                "tickers_with_any_row": tickers_with_rows,
                "tickers_with_any_row_pct_of_universe": pct(tickers_with_rows, universe),
                "tickers_with_4plus_periods": tickers_with_4plus,
                "newest_period_end": newest_period,
                "field_null_rates": [null_rate(conn, table, period_type, f) for f in key_fields],
            }
        coverage[table] = section

    # 상세페이지가 실제로 요구하는 최소 기준(main.py get_us_stock_detail: annual>=4, quarter 존재)
    # 미달 티커 표본 20개 — 어떤 종목이 온디맨드 수집에 계속 의존하는지 바로 확인용.
    thin_coverage_sample = fetchall(
        conn,
        """
        SELECT m.ticker, m.company_name,
               (SELECT COUNT(*) FROM us_financial_data f WHERE f.ticker=m.ticker AND f.period_type='annual') AS annual_rows,
               (SELECT COUNT(*) FROM us_cashflow_data c WHERE c.ticker=m.ticker AND c.period_type='annual') AS cf_annual_rows
        FROM us_stock_meta m
        WHERE UPPER(COALESCE(m.country,'US'))='US'
          AND (
            (SELECT COUNT(*) FROM us_financial_data f WHERE f.ticker=m.ticker AND f.period_type='annual') < 4
            OR (SELECT COUNT(*) FROM us_cashflow_data c WHERE c.ticker=m.ticker AND c.period_type='annual') < 4
          )
        ORDER BY m.ticker
        LIMIT 20
        """,
    )

    conn.close()

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_us_tickers": universe,
        "coverage": coverage,
        "thin_coverage_sample_limit20": thin_coverage_sample,
        "note": (
            "온디맨드 수집 방식(main.py get_us_stock_detail: annual<4 또는 quarter<4 또는 "
            "가격<120행이면 그 자리에서 _refresh_us_stock_data 실행)이라 이 표는 특정 시점 스냅샷일 "
            "뿐이며, 종목 상세를 한 번이라도 열면 그 티커의 커버리지가 바뀔 수 있다."
        ),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "us_financial_coverage_audit_latest.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    lines = [
        "# 미국 재무제표/현금흐름 커버리지 감사 (자동 생성)",
        "",
        f"- 생성 시각: {result['generated_at']}",
        f"- 미국 종목 유니버스: {universe}개",
        "",
    ]
    for table, section in coverage.items():
        lines.append(f"## `{table}`")
        for period_type, s in section.items():
            lines.append(
                f"- **{period_type}**: {s['tickers_with_any_row']}개 종목에 행 존재"
                f"({s['tickers_with_any_row_pct_of_universe']}%), "
                f"4개 이상 기간 확보 {s['tickers_with_4plus_periods']}개, "
                f"최신 period_end={s['newest_period_end']}"
            )
            for fr in s["field_null_rates"]:
                lines.append(f"  - `{fr['field']}` NULL 비율 {fr['null_pct']}% (행 {fr['rows']}개 중)")
        lines.append("")
    lines.append("## 상세페이지 최소 기준(annual 4건) 미달 표본 (최대 20개)")
    lines.append("")
    lines.append("| ticker | company_name | annual_rows | cf_annual_rows |")
    lines.append("|---|---|---:|---:|")
    for r in thin_coverage_sample:
        lines.append(f"| {r['ticker']} | {r['company_name'] or ''} | {r['annual_rows']} | {r['cf_annual_rows']} |")
    lines.append("")
    lines.append(f"> {result['note']}")
    DOC_PATH.write_text("\n".join(lines))

    print(json.dumps({"universe": universe, "out": str(out_path), "doc": str(DOC_PATH)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
