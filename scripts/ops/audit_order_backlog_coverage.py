#!/usr/bin/env python3
"""Audit order backlog coverage and separate direct data from usable proxies.

수주잔고는 모든 종목에 존재하는 재무항목이 아니므로, 전체 커버리지 하나만 보면
자동매매/백테스트에서 결측을 0으로 오해하기 쉽다. 이 스크립트는 직접 수주잔고,
수주공시, proxy, 유효 업종 커버리지를 분리해 JSON/Markdown으로 남긴다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


ELIGIBLE_SQL = """
(
  COALESCE(su.sector_large,'') IN ('산업재','에너지','IT','소재')
  OR COALESCE(su.sector_mid,'') IN ('자본재','반도체','하드웨어','디스플레이','에너지','소재')
  OR COALESCE(su.sector_small,'') LIKE '%건설%'
  OR COALESCE(su.sector_small,'') LIKE '%조선%'
  OR COALESCE(su.sector_small,'') LIKE '%장비%'
  OR COALESCE(su.sector_small,'') LIKE '%플랜트%'
  OR COALESCE(su.sector_small,'') LIKE '%방산%'
)
"""


def pct(n: int | float, d: int | float) -> float:
    return round((float(n) / float(d) * 100.0), 2) if d else 0.0


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_report() -> dict:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        summary = one(
            conn,
            f"""
            WITH u AS (
              SELECT stock_code, {ELIGIBLE_SQL} AS is_eligible
              FROM stock_universe su
              WHERE stock_code IS NOT NULL AND length(stock_code)=6
            ),
            ob AS (
              SELECT DISTINCT stock_code
              FROM order_backlog
              WHERE COALESCE(backlog_amount, backlog_normalized, 0) > 0
            ),
            oc AS (SELECT DISTINCT stock_code FROM order_contracts),
            inv AS (
              SELECT DISTINCT stock_code
              FROM inventory_sales_signals
              WHERE COALESCE(order_contracts_krw,0) > 0 OR COALESCE(order_backlog_krw,0) > 0
            ),
            adv AS (SELECT DISTINCT stock_code FROM contract_advance_signals)
            SELECT
              COUNT(*) AS universe,
              SUM(is_eligible) AS eligible_universe,
              COUNT(DISTINCT ob.stock_code) AS direct_backlog,
              COUNT(DISTINCT CASE WHEN ob.stock_code IS NOT NULL OR oc.stock_code IS NOT NULL THEN u.stock_code END) AS direct_or_contract,
              COUNT(DISTINCT CASE WHEN ob.stock_code IS NOT NULL OR oc.stock_code IS NOT NULL OR inv.stock_code IS NOT NULL OR adv.stock_code IS NOT NULL THEN u.stock_code END) AS any_order_signal,
              COUNT(DISTINCT CASE WHEN is_eligible AND ob.stock_code IS NOT NULL THEN u.stock_code END) AS eligible_direct_backlog,
              COUNT(DISTINCT CASE WHEN is_eligible AND (ob.stock_code IS NOT NULL OR oc.stock_code IS NOT NULL) THEN u.stock_code END) AS eligible_direct_or_contract,
              COUNT(DISTINCT CASE WHEN is_eligible AND (ob.stock_code IS NOT NULL OR oc.stock_code IS NOT NULL OR inv.stock_code IS NOT NULL OR adv.stock_code IS NOT NULL) THEN u.stock_code END) AS eligible_any_order_signal
            FROM u
            LEFT JOIN ob ON ob.stock_code=u.stock_code
            LEFT JOIN oc ON oc.stock_code=u.stock_code
            LEFT JOIN inv ON inv.stock_code=u.stock_code
            LEFT JOIN adv ON adv.stock_code=u.stock_code
            """,
        )
        for k, denom_key in [
            ("direct_backlog", "universe"),
            ("direct_or_contract", "universe"),
            ("any_order_signal", "universe"),
            ("eligible_direct_backlog", "eligible_universe"),
            ("eligible_direct_or_contract", "eligible_universe"),
            ("eligible_any_order_signal", "eligible_universe"),
        ]:
            summary[f"{k}_pct"] = pct(summary.get(k, 0), summary.get(denom_key, 0))

        by_sector = rows(
            conn,
            """
            WITH ob AS (
              SELECT DISTINCT stock_code
              FROM order_backlog
              WHERE COALESCE(backlog_amount, backlog_normalized, 0) > 0
            ),
            oc AS (SELECT DISTINCT stock_code FROM order_contracts)
            SELECT
              COALESCE(su.sector_large,'-') AS sector_large,
              COALESCE(su.sector_mid,'-') AS sector_mid,
              COUNT(*) AS total,
              COUNT(DISTINCT ob.stock_code) AS direct_backlog,
              COUNT(DISTINCT CASE WHEN ob.stock_code IS NOT NULL OR oc.stock_code IS NOT NULL THEN su.stock_code END) AS direct_or_contract
            FROM stock_universe su
            LEFT JOIN ob ON ob.stock_code=su.stock_code
            LEFT JOIN oc ON oc.stock_code=su.stock_code
            WHERE su.stock_code IS NOT NULL AND length(su.stock_code)=6
            GROUP BY sector_large, sector_mid
            HAVING total >= 10
            ORDER BY direct_or_contract * 1.0 / total DESC, total DESC
            """,
        )
        for r in by_sector:
            r["direct_backlog_pct"] = pct(r["direct_backlog"], r["total"])
            r["direct_or_contract_pct"] = pct(r["direct_or_contract"], r["total"])

        low_eligible_missing = rows(
            conn,
            f"""
            WITH ob AS (
              SELECT DISTINCT stock_code
              FROM order_backlog
              WHERE COALESCE(backlog_amount, backlog_normalized, 0) > 0
            ),
            oc AS (SELECT DISTINCT stock_code FROM order_contracts)
            SELECT su.stock_code, su.stock_name, su.market, su.sector_large, su.sector_mid, su.sector_small, su.market_cap
            FROM stock_universe su
            LEFT JOIN ob ON ob.stock_code=su.stock_code
            LEFT JOIN oc ON oc.stock_code=su.stock_code
            WHERE su.stock_code IS NOT NULL AND length(su.stock_code)=6
              AND {ELIGIBLE_SQL}
              AND ob.stock_code IS NULL
              AND oc.stock_code IS NULL
            ORDER BY COALESCE(su.market_cap,0) DESC
            LIMIT 100
            """,
        )

        latest_rows = rows(
            conn,
            """
            SELECT year, quarter, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks
            FROM order_backlog
            WHERE COALESCE(backlog_amount, backlog_normalized, 0) > 0
              AND year BETWEEN 2020 AND 2026
            GROUP BY year, quarter
            ORDER BY year DESC, quarter DESC
            LIMIT 16
            """,
        )

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "by_sector": by_sector,
            "latest_periods": latest_rows,
            "eligible_missing_top100": low_eligible_missing,
            "interpretation": [
                "수주잔고 직접값은 전체 종목 공통 항목이 아니므로 결측을 0으로 처리하면 안 된다.",
                "전체 직접 커버리지보다 핵심 수주 가능 업종 커버리지가 전략 판단에 더 적합하다.",
                "매수/매도 로직에서는 direct_backlog, order_contract, proxy, not_applicable을 분리해야 한다.",
            ],
        }
    finally:
        conn.close()


def write_md(report: dict, path: Path) -> None:
    s = report["summary"]
    lines = [
        "# Order Backlog Coverage Audit",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- universe: `{s['universe']:,}`",
        f"- direct backlog: `{s['direct_backlog']:,}` (`{s['direct_backlog_pct']}%`)",
        f"- direct backlog or order contract: `{s['direct_or_contract']:,}` (`{s['direct_or_contract_pct']}%`)",
        f"- any order signal/proxy: `{s['any_order_signal']:,}` (`{s['any_order_signal_pct']}%`)",
        f"- eligible universe: `{s['eligible_universe']:,}`",
        f"- eligible direct backlog: `{s['eligible_direct_backlog']:,}` (`{s['eligible_direct_backlog_pct']}%`)",
        f"- eligible direct or contract: `{s['eligible_direct_or_contract']:,}` (`{s['eligible_direct_or_contract_pct']}%`)",
        f"- eligible any order signal/proxy: `{s['eligible_any_order_signal']:,}` (`{s['eligible_any_order_signal_pct']}%`)",
        "",
        "## Interpretation",
        "",
    ]
    lines += [f"- {x}" for x in report["interpretation"]]
    lines += ["", "## Sector Coverage", "", "| sector_large | sector_mid | total | direct | direct+contract |", "| --- | --- | ---: | ---: | ---: |"]
    for r in report["by_sector"][:40]:
        lines.append(
            f"| {r['sector_large']} | {r['sector_mid']} | {r['total']:,} | "
            f"{r['direct_backlog']:,} ({r['direct_backlog_pct']}%) | "
            f"{r['direct_or_contract']:,} ({r['direct_or_contract_pct']}%) |"
        )
    lines += ["", "## Eligible Missing Top 100", "", "| code | name | market | sector | market_cap |", "| --- | --- | --- | --- | ---: |"]
    for r in report["eligible_missing_top100"]:
        lines.append(
            f"| {r['stock_code']} | {r['stock_name']} | {r['market']} | "
            f"{r['sector_large']} / {r['sector_mid']} / {r['sector_small']} | {r['market_cap'] or 0:,.0f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"order_backlog_coverage_audit_{stamp}.json"
    md_path = OUT_DIR / f"order_backlog_coverage_audit_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(report, md_path)
    print(json.dumps({"json": str(json_path), "md": str(md_path), "summary": report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
