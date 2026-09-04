#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
DOC_PATH = ROOT / "docs" / f"macro_quant_expansion_research_{datetime.now():%Y%m%d}.md"


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def md_table(headers: list[str], data: list[sqlite3.Row | tuple]) -> str:
    if not data:
        return "_없음_\n"
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in data:
        values = [row[h] if isinstance(row, sqlite3.Row) else row[i] for i, h in enumerate(headers)]
        out.append("| " + " | ".join("" if v is None else str(v).replace("|", "/") for v in values) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    summary = rows(
        conn,
        """
        SELECT
          (SELECT COUNT(*) FROM quant_major_indicator_catalog) AS catalog_total,
          (SELECT COUNT(*) FROM quant_major_indicator_catalog WHERE indicator_key LIKE 'macro:%') AS macro_catalog,
          (SELECT COUNT(DISTINCT indicator_key) FROM cafe_quant_indicator_mappings WHERE indicator_key LIKE 'macro:%') AS macro_sector_mapped,
          (SELECT COUNT(DISTINCT indicator_key) FROM indicator_sector_direction_rules WHERE indicator_key LIKE 'macro:%') AS macro_direction_ruled,
          (SELECT COUNT(DISTINCT indicator_key) FROM cafe_stock_indicator_mappings WHERE indicator_key LIKE 'macro:%') AS macro_stock_mapped,
          (SELECT COUNT(*) FROM cafe_stock_indicator_mappings WHERE indicator_key LIKE 'macro:%' AND mapping_status='candidate_macro_context') AS macro_stock_candidates,
          (SELECT COUNT(*) FROM quant_indicator_signal_events WHERE indicator_key LIKE 'macro:%') AS macro_signal_events
        """,
    )
    sector_coverage = rows(
        conn,
        """
        SELECT q.sector_name, COUNT(*) AS mapped, SUM(r.indicator_key IS NOT NULL) AS with_rule
        FROM cafe_quant_indicator_mappings q
        LEFT JOIN indicator_sector_direction_rules r
          ON r.indicator_key=q.indicator_key AND r.sector_name=q.sector_name
        WHERE q.indicator_key LIKE 'macro:%'
        GROUP BY q.sector_name
        ORDER BY mapped DESC, q.sector_name
        """,
    )
    unmapped_macro = rows(
        conn,
        """
        SELECT c.indicator_key, c.epic_indicator_name, c.source_system
        FROM quant_major_indicator_catalog c
        WHERE c.indicator_key LIKE 'macro:%'
          AND NOT EXISTS (SELECT 1 FROM cafe_quant_indicator_mappings m WHERE m.indicator_key=c.indicator_key)
        ORDER BY c.indicator_key
        """,
    )
    missing_rules = rows(
        conn,
        """
        SELECT q.sector_name, q.indicator_key, q.indicator_name
        FROM cafe_quant_indicator_mappings q
        LEFT JOIN indicator_sector_direction_rules r
          ON r.indicator_key=q.indicator_key AND r.sector_name=q.sector_name
        WHERE q.indicator_key LIKE 'macro:%' AND r.indicator_key IS NULL
        ORDER BY q.sector_name, q.indicator_key
        """,
    )
    stock_status = rows(
        conn,
        """
        SELECT mapping_status, importance_level, COUNT(*) AS count
        FROM cafe_stock_indicator_mappings
        GROUP BY mapping_status, importance_level
        ORDER BY count DESC
        """,
    )

    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(
        "\n".join(
            [
                "# Macro/Quant Expansion Research",
                "",
                f"- 작성: {datetime.now():%Y-%m-%d %H:%M:%S}",
                f"- DB: `{DB_PATH}`",
                "",
                "## Coverage Summary",
                "",
                md_table(
                    [
                        "catalog_total",
                        "macro_catalog",
                        "macro_sector_mapped",
                        "macro_direction_ruled",
                        "macro_stock_mapped",
                        "macro_stock_candidates",
                        "macro_signal_events",
                    ],
                    summary,
                ),
                "## Sector Direction Coverage",
                "",
                md_table(["sector_name", "mapped", "with_rule"], sector_coverage),
                "## Remaining Gaps",
                "",
                "### Unmapped Macro Indicators",
                "",
                md_table(["indicator_key", "epic_indicator_name", "source_system"], unmapped_macro),
                "### Mapped But Missing Direction Rule",
                "",
                md_table(["sector_name", "indicator_key", "indicator_name"], missing_rules),
                "## Stock Mapping Status",
                "",
                md_table(["mapping_status", "importance_level", "count"], stock_status),
                "## Research Notes",
                "",
                "- `candidate_macro_context`는 종목 페이지 맥락/설명에는 표시할 수 있지만, 자동 매수 신호 점수에는 바로 포함하지 않는다.",
                "- 매수/매도 신호로 승격하려면 지표별 발표일 기준 +20/+60/+120거래일 수익률, 섹터 대비 초과수익, 하락위험을 검증해야 한다.",
                "- 비용성 지표는 매출 노출보다 원가/이익 노출이 중요하므로 `segment_revenue`, `cost_structure`, `dart_material_purchase`로 노출 비중을 먼저 보강한다.",
                "- 다음 개선 과제: macro 후보 936건을 지표-섹터 단위로 백테스트하여 profit factor, hit rate, max drawdown 기준을 통과한 것만 `confirmed_macro_signal`로 승격한다.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(DOC_PATH)


if __name__ == "__main__":
    main()
