#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
HS_DB_PATH = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
EPIC_SCRIPT = ROOT / "scripts" / "sync_epic_forward_strategy.py"
EPIC_DIR = ROOT / "scratch" / "epic"
SEED_DIR = EPIC_DIR
UI_CRAWL_PATH = EPIC_DIR / "ui_crawl_latest.json"
OUT_DIR = ROOT / "scratch" / "epic"


def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def json_load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_cmd(args: list[str]) -> dict:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return {
        "cmd": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def fetch_scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return [tuple(r) for r in conn.execute(sql, params).fetchall()]


def collect_db_snapshot() -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        return {
            "forward_strategy_industry_categories": fetch_scalar(conn, "select count(*) from forward_strategy_industry_categories"),
            "forward_strategy_indicators": fetch_scalar(conn, "select count(*) from forward_strategy_indicators"),
            "forward_strategy_indicator_series": fetch_scalar(conn, "select count(*) from forward_strategy_indicator_series"),
            "forward_strategy_related_companies": fetch_scalar(conn, "select count(*) from forward_strategy_related_companies"),
            "forward_strategy_raw_responses": fetch_scalar(conn, "select count(*) from forward_strategy_raw_responses"),
            "indicators_by_tab": fetch_rows(conn, "select tab, count(*) from forward_strategy_indicators group by tab order by count(*) desc"),
            "indicators_by_source": fetch_rows(conn, "select coalesce(source,'') as source, count(*) from forward_strategy_indicators group by coalesce(source,'') order by count(*) desc"),
            "sample_indicators": fetch_rows(
                conn,
                """
                select category_code, sub_code, indicator_name, data_code, latest_date, since_date, unit, source
                from forward_strategy_indicators
                order by updated_at desc, id desc
                limit 10
                """,
            ),
            "sample_companies": fetch_rows(
                conn,
                """
                select category_code, sub_code, stock_code, stock_name
                from forward_strategy_related_companies
                order by updated_at desc, id desc
                limit 10
                """,
            ),
        }
    finally:
        conn.close()


def collect_hs_trade_snapshot() -> dict:
    if not HS_DB_PATH.exists():
        return {"exists": False}
    conn = sqlite3.connect(HS_DB_PATH)
    try:
        return {
            "exists": True,
            "analysis2_company_hs_monthly_cache": fetch_scalar(conn, "select count(*) from analysis2_company_hs_monthly_cache"),
            "analysis2_sector_hs_monthly_cache": fetch_scalar(conn, "select count(*) from analysis2_sector_hs_monthly_cache"),
            "hs_code_company_map": fetch_scalar(conn, "select count(*) from hs_code_company_map"),
            "hs_sector_map": fetch_scalar(conn, "select count(*) from hs_sector_map"),
            "telegram_company_hs_flow_map": fetch_scalar(conn, "select count(*) from telegram_company_hs_flow_map"),
            "telegram_post_cache": fetch_scalar(conn, "select count(*) from telegram_post_cache"),
            "telegram_trade_card": fetch_scalar(conn, "select count(*) from telegram_trade_card"),
            "trade_series_cache": fetch_scalar(conn, "select count(*) from trade_series_cache"),
            "sigungu_trade_record": fetch_scalar(conn, "select count(*) from sigungu_trade_record"),
        }
    finally:
        conn.close()


def inspect_local_assets() -> dict:
    seed_ok = (SEED_DIR / "t3e.json").exists() and (SEED_DIR / "r3e.json").exists()
    ui_ok = UI_CRAWL_PATH.exists()
    out = {
        "seed_catalog_present": seed_ok,
        "ui_crawl_present": ui_ok,
        "epic_access_token_present": bool(os.environ.get("EPIC_ACCESS_TOKEN", "").strip()),
    }
    if seed_ok:
        t3e = json_load(SEED_DIR / "t3e.json")
        r3e = json_load(SEED_DIR / "r3e.json")
        out["seed_catalog_summary"] = {
            "t3e_count": len(t3e) if isinstance(t3e, list) else None,
            "recent_categories": len((r3e or {}).get("recentIndustryCategories") or []),
            "industry_categories": len((r3e or {}).get("industryCategories") or []),
        }
    if ui_ok:
        ui = json_load(UI_CRAWL_PATH)
        items = ui.get("items") or []
        out["ui_crawl_summary"] = {
            "captured_at": ui.get("captured_at"),
            "count": ui.get("count"),
            "items_len": len(items),
            "ok_items": sum(1 for x in items if x.get("ok")),
            "with_related_companies": sum(1 for x in items if x.get("related_companies")),
        }
    return out


def apply_local_assets() -> list[dict]:
    results = []
    if (SEED_DIR / "t3e.json").exists() and (SEED_DIR / "r3e.json").exists():
        results.append(
            run_cmd([str(ROOT / "venv" / "bin" / "python"), str(EPIC_SCRIPT), "--seed-catalog", str(SEED_DIR)])
        )
    if UI_CRAWL_PATH.exists():
        results.append(
            run_cmd([str(ROOT / "venv" / "bin" / "python"), str(EPIC_SCRIPT), "--import-ui-crawl", str(UI_CRAWL_PATH)])
        )
    return results


def smoke_network() -> dict:
    if not os.environ.get("EPIC_ACCESS_TOKEN", "").strip():
        return {
            "skipped": True,
            "reason": "EPIC_ACCESS_TOKEN missing",
        }
    return run_cmd(
        [
            str(ROOT / "venv" / "bin" / "python"),
            str(EPIC_SCRIPT),
            "--max-indicators",
            "3",
            "--dry-run",
            "--delay",
            "0.2",
        ]
    )


def evaluate_readiness(report: dict) -> dict:
    assets = report.get("assets") or {}
    after = report.get("after") or {}
    hs = report.get("hs_trade") or {}
    series_points = int(after.get("forward_strategy_indicator_series") or 0)
    indicators = int(after.get("forward_strategy_indicators") or 0)
    companies = int(after.get("forward_strategy_related_companies") or 0)

    epic_level = "not_ready"
    if indicators > 0 and companies > 0:
        epic_level = "metadata_ready"
    if series_points > 0:
        epic_level = "timeseries_ready"

    return {
        "epic_pipeline_level": epic_level,
        "epic_metadata_ready": indicators > 0 and companies > 0,
        "epic_timeseries_ready": series_points > 0,
        "hs_trade_ready": bool(hs.get("exists")) and int(hs.get("trade_series_cache") or 0) > 0,
        "epic_access_token_present": bool(assets.get("epic_access_token_present")),
        "gaps": [
            gap
            for gap, ok in [
                ("missing_epic_seed_catalog", bool(assets.get("seed_catalog_present"))),
                ("missing_epic_ui_crawl", bool(assets.get("ui_crawl_present"))),
                ("missing_epic_token", bool(assets.get("epic_access_token_present"))),
                ("missing_epic_series_points", series_points > 0),
                ("missing_hs_trade_cache", bool(hs.get("exists")) and int(hs.get("trade_series_cache") or 0) > 0),
            ]
            if not ok
        ],
        "recommended_next_steps": build_recommendations(report),
    }


def build_recommendations(report: dict) -> list[str]:
    assets = report.get("assets") or {}
    after = report.get("after") or {}
    hs = report.get("hs_trade") or {}
    recommendations: list[str] = []

    if not assets.get("seed_catalog_present"):
        recommendations.append("scratch/epic/t3e.json, r3e.json 시드 파일 확보")
    if not assets.get("ui_crawl_present"):
        recommendations.append("로그인된 브라우저에서 EPIC UI crawl JSON 최신본 재수집")
    if int(after.get("forward_strategy_indicator_series") or 0) == 0:
        if assets.get("epic_access_token_present"):
            recommendations.append("EPIC 인증 토큰으로 sync_epic_forward_strategy.py 네트워크 본수집 실행")
        else:
            recommendations.append("EPIC_ACCESS_TOKEN 주입 후 시계열 본수집 실행")
    if int(hs.get("trade_series_cache") or 0) == 0:
        recommendations.append("hs_trade_lab/scripts/daily_refresh.py 실행으로 무역 캐시 초기화")
    if int(hs.get("telegram_trade_card") or 0) == 0:
        recommendations.append("텔레그램 기반 trade card/build cache 재생성")
    if not recommendations:
        recommendations.append("EPIC+HS-Trade 기초 적재 완료, 지표 페이지/백테스트 연동 단계로 진행")
    return recommendations


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and prepare EPIC quant-indicator pipeline.")
    parser.add_argument("--apply-local", action="store_true", help="Import local seed catalog and UI crawl into Forward_Strategy tables.")
    parser.add_argument("--smoke-network", action="store_true", help="Run authenticated dry-run smoke test for 3 indicators if EPIC_ACCESS_TOKEN is present.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    before = collect_db_snapshot()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "assets": inspect_local_assets(),
        "before": before,
        "actions": {},
    }

    if args.apply_local:
        report["actions"]["apply_local"] = apply_local_assets()

    if args.smoke_network:
        report["actions"]["smoke_network"] = smoke_network()

    report["after"] = collect_db_snapshot()
    report["hs_trade"] = collect_hs_trade_snapshot()
    report["readiness"] = evaluate_readiness(report)
    out_path = OUT_DIR / f"epic_quant_pipeline_report_{now_ts()}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(out_path),
                "assets": report["assets"],
                "after": report["after"],
                "hs_trade": report["hs_trade"],
                "readiness": report["readiness"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
