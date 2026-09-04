#!/usr/bin/env python3
"""Audit contract advance signal data health."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from argparse import ArgumentParser
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db_utils import connect_stock_db

from routes.contract_advance_signals import router as contract_advance_router
from signal_engine import _load_contract_advance_bonus_map


DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"


def connect() -> sqlite3.Connection:
    return connect_stock_db(timeout=60, row_factory=sqlite3.Row)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
    )


def parse_as_of_date(raw: str | None) -> date:
    if not raw:
        return date.today()
    return datetime.strptime(raw, "%Y-%m-%d").date()


def rebuild_if_missing(conn: sqlite3.Connection) -> dict:
    if table_exists(conn, "contract_advance_signals"):
        count = conn.execute("SELECT COUNT(*) FROM contract_advance_signals").fetchone()[0]
        if count:
            return {"rebuilt": False, "returncode": 0, "stdout": "", "stderr": ""}

    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_contract_advance_signals.py"), "--since-year", "2020"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "rebuilt": True,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }


def metrics(conn: sqlite3.Connection) -> dict:
    if not table_exists(conn, "contract_advance_signals"):
        return {"exists": False}
    row = conn.execute(
        """
        SELECT COUNT(*) rows,
               COUNT(DISTINCT stock_code) stocks,
               MIN(fiscal_year) min_year,
               MAX(fiscal_year) max_year,
               SUM(CASE WHEN signal_score>0 THEN 1 ELSE 0 END) scored_rows,
               SUM(CASE WHEN quality_flag='financial_like_excluded' THEN 1 ELSE 0 END) financial_excluded,
               SUM(CASE WHEN quality_flag='ok' THEN 1 ELSE 0 END) ok_rows
        FROM contract_advance_signals
        """
    ).fetchone()
    latest_rows = conn.execute(
        """
        SELECT fiscal_year, fiscal_quarter, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks
        FROM contract_advance_signals
        GROUP BY fiscal_year, fiscal_quarter
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        LIMIT 1
        """
    ).fetchone()
    top = conn.execute(
        """
        SELECT stock_code, stock_name, fiscal_year, fiscal_quarter, fs_div,
               signal_score, gross_to_revenue_pct, gross_qoq_pct, signal_label
        FROM contract_advance_signals
        WHERE signal_score >= 6 AND quality_flag='ok'
        ORDER BY fiscal_year DESC, fiscal_quarter DESC, signal_score DESC
        LIMIT 10
        """
    ).fetchall()
    latest_stock = conn.execute(
        """
        SELECT stock_code
        FROM contract_advance_signals
        WHERE quality_flag='ok'
        ORDER BY fiscal_year DESC, fiscal_quarter DESC, signal_score DESC, stock_code ASC
        LIMIT 1
        """
    ).fetchone()
    bad_financial = conn.execute(
        """
        SELECT COUNT(*) AS bad_rows
        FROM contract_advance_signals
        WHERE quality_flag='financial_like_excluded'
          AND COALESCE(signal_score, 0) > 0
        """
    ).fetchone()
    return {
        "exists": True,
        **dict(row),
        "latest_period": dict(latest_rows) if latest_rows else None,
        "top_signals": [dict(r) for r in top],
        "sample_stock_code": latest_stock["stock_code"] if latest_stock else None,
        "financial_exclusion_violations": int(bad_financial["bad_rows"] or 0),
    }


def check_api(sample_stock_code: str | None) -> dict:
    app = FastAPI()
    app.include_router(contract_advance_router, prefix="/api/contract-advance-signals")
    client = TestClient(app)

    top_resp = client.get("/api/contract-advance-signals/top", params={"min_score": 4, "limit": 5, "fs_div": "CFS"})
    stock_resp = None
    if sample_stock_code:
        stock_resp = client.get(f"/api/contract-advance-signals/stock/{sample_stock_code}", params={"fs_div": "ALL"})

    top_payload = top_resp.json() if top_resp.headers.get("content-type", "").startswith("application/json") else {}
    stock_payload = (
        stock_resp.json()
        if stock_resp is not None and stock_resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    return {
        "top_ok": top_resp.status_code == 200,
        "top_status": top_resp.status_code,
        "top_count": top_payload.get("count"),
        "top_latest_period": top_payload.get("latest_period"),
        "stock_code": sample_stock_code,
        "stock_ok": bool(stock_resp is not None and stock_resp.status_code == 200),
        "stock_status": stock_resp.status_code if stock_resp is not None else None,
        "stock_count": stock_payload.get("count"),
        "stock_latest_period": (
            f"{stock_payload['latest']['fiscal_year']}Q{stock_payload['latest']['fiscal_quarter']}"
            if stock_payload.get("latest")
            else None
        ),
    }


def check_bonus_map() -> dict:
    bonus_map = _load_contract_advance_bonus_map(min_score=4)
    sample_code = next(iter(sorted(bonus_map)), None)
    sample = bonus_map.get(sample_code) if sample_code else None
    return {
        "ok": bool(bonus_map),
        "count": len(bonus_map),
        "sample_stock_code": sample_code,
        "sample": sample,
    }


def issues(m: dict, rebuild: dict, api: dict, bonus: dict, as_of: date) -> list[dict]:
    out: list[dict] = []
    if rebuild.get("rebuilt") and rebuild.get("returncode") != 0:
        out.append({"severity": "critical", "code": "REBUILD_FAILED", "message": rebuild.get("stderr") or "rebuild failed"})
    if not m.get("exists"):
        out.append({"severity": "critical", "code": "TABLE_MISSING", "message": "contract_advance_signals table missing"})
        return out
    if (m.get("rows") or 0) < 500:
        out.append({"severity": "warning", "code": "LOW_ROW_COUNT", "message": f"rows={m.get('rows')}"})
    if (m.get("stocks") or 0) < 30:
        out.append({"severity": "warning", "code": "LOW_STOCK_COVERAGE", "message": f"stocks={m.get('stocks')}"})
    if (m.get("scored_rows") or 0) == 0:
        out.append({"severity": "warning", "code": "NO_SCORED_ROWS", "message": "no positive signal rows"})
    if (m.get("financial_exclusion_violations") or 0) > 0:
        out.append({
            "severity": "critical",
            "code": "FINANCIAL_EXCLUSION_BROKEN",
            "message": f"violations={m.get('financial_exclusion_violations')}",
        })
    latest = m.get("latest_period") or {}
    if latest and latest.get("fiscal_year", 0) < as_of.year - 1:
        out.append({"severity": "warning", "code": "STALE_PERIOD", "message": f"latest={latest}"})
    if not api.get("top_ok"):
        out.append({"severity": "critical", "code": "TOP_API_FAILED", "message": f"status={api.get('top_status')}"})
    if not api.get("stock_ok"):
        out.append({
            "severity": "critical",
            "code": "STOCK_API_FAILED",
            "message": f"stock={api.get('stock_code')} status={api.get('stock_status')}",
        })
    if not bonus.get("ok"):
        out.append({"severity": "critical", "code": "BONUS_MAP_EMPTY", "message": "contract advance bonus map is empty"})
    return out


def write_report(as_of: date, m: dict, rebuild: dict, api: dict, bonus: dict, issue_list: list[dict]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    stamp = as_of.strftime("%Y%m%d")
    payload = {
        "as_of_date": as_of.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": m,
        "rebuild": rebuild,
        "api_checks": api,
        "bonus_map": bonus,
        "issues": issue_list,
    }
    (OUT_DIR / f"contract_advance_signal_audit_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# contract_advance_signals audit — {stamp}",
        "",
        "## Metrics",
        f"- rows: {m.get('rows', 0):,}",
        f"- stocks: {m.get('stocks', 0):,}",
        f"- years: {m.get('min_year')} ~ {m.get('max_year')}",
        f"- scored_rows: {m.get('scored_rows', 0):,}",
        f"- financial_excluded: {m.get('financial_excluded', 0):,}",
        f"- financial_exclusion_violations: {m.get('financial_exclusion_violations', 0):,}",
        f"- latest_period: {m.get('latest_period')}",
        "",
        "## Required Checks",
        f"- row/stock coverage: rows={m.get('rows', 0):,}, stocks={m.get('stocks', 0):,}",
        f"- latest fiscal period: {m.get('latest_period')}",
        f"- signal_score>0 rows exist: {'yes' if (m.get('scored_rows') or 0) > 0 else 'no'} ({m.get('scored_rows', 0):,})",
        (
            "- financial/insurance exclusion kept: "
            f"excluded_rows={m.get('financial_excluded', 0):,}, "
            f"violations={m.get('financial_exclusion_violations', 0):,}"
        ),
        (
            "- /api/contract-advance-signals/top: "
            f"status={api.get('top_status')} count={api.get('top_count')} latest={api.get('top_latest_period')}"
        ),
        (
            f"- /api/contract-advance-signals/stock/{api.get('stock_code')}: "
            f"status={api.get('stock_status')} count={api.get('stock_count')} latest={api.get('stock_latest_period')}"
        ),
        (
            "- _load_contract_advance_bonus_map(min_score=4): "
            f"count={bonus.get('count')} sample={bonus.get('sample_stock_code')} {bonus.get('sample')}"
        ),
        "",
        "## Issues",
    ]
    if issue_list:
        lines.extend(f"- [{i['severity']}] {i['code']}: {i['message']}" for i in issue_list)
    else:
        lines.append("- none")
    lines.extend(["", "## Top Signals"])
    for r in m.get("top_signals", [])[:10]:
        lines.append(
            f"- {r['stock_name']}({r['stock_code']}) {r['fiscal_year']}Q{r['fiscal_quarter']} "
            f"{r['fs_div']} score={r['signal_score']} gross/rev={r['gross_to_revenue_pct']} "
            f"QoQ={r['gross_qoq_pct']} — {r['signal_label']}"
        )
    (OUT_DIR / f"contract_advance_signal_audit_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument("--as-of-date", help="Override audit/report date (YYYY-MM-DD)")
    args = parser.parse_args()
    as_of = parse_as_of_date(args.as_of_date)

    conn = connect()
    try:
        rebuild = rebuild_if_missing(conn)
        m = metrics(conn)
    finally:
        conn.close()
    api = check_api(m.get("sample_stock_code"))
    bonus = check_bonus_map()
    issue_list = issues(m, rebuild, api, bonus, as_of)
    write_report(as_of, m, rebuild, api, bonus, issue_list)
    if issue_list:
        for i in issue_list:
            print(f"[{i['severity']}] {i['code']}: {i['message']}")
    else:
        print("contract_advance_signals audit OK")
    return 1 if any(i["severity"] == "critical" for i in issue_list) else 0


if __name__ == "__main__":
    raise SystemExit(main())
