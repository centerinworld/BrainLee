#!/usr/bin/env python3
from __future__ import annotations

import calendar
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_health import evaluate_all_contracts

DB = ROOT / "stock.db"
HS_DB = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
# us_market_dashboard는 Realtek_NVME 외장 SSD로 이전됨(2026-07-11)
US_MARKET_DB = Path("/Volumes/Realtek_NVME/us_market_dashboard/us_market.db")
OUT_DIR = ROOT / "research_outputs"


@dataclass
class TableCheck:
    page: str
    dataset: str
    table: str
    grain: str
    date_col: str | None
    key_expr: str | None
    required_cols: tuple[str, ...] = ()
    min_rows: int = 1
    freshness_days: int | None = None
    collector: str = ""
    priority: str = "P2"
    notes: str = ""


CHECKS: list[TableCheck] = [
    TableCheck("메인 대시보드/차트", "국내 OHLCV", "price_history", "stock_code,date", "date", "stock_code || '|' || date", ("close", "volume"), 2_000_000, 10, "KIS collect_kis_ohlcv.py", "P0"),
    TableCheck("메인 대시보드/종목검색", "상장종목", "stock_universe", "stock_code,base_date", "COALESCE(base_info_updated_at, updated_at, base_date)", "stock_code || '|' || base_date", ("stock_name", "market"), 2_000, 30, "KRX/Kiwoom stock info", "P0"),
    TableCheck("시장지표", "공공 일별 시세", "stock_price_daily", "bas_dt,stock_code", "bas_dt", "bas_dt || '|' || stock_code", ("close_price", "volume", "market_cap"), 1_000_000, 10, "public_data_collector.py / KIS fallback", "P1", "페이지/전략의 주 시세 소스는 price_history이며 market_cap은 stock_universe fallback 가능"),
    TableCheck("시장지표", "투자자별 매매", "investor_trading_daily", "bas_dt,stock_code", "bas_dt", "bas_dt || '|' || stock_code", ("inst_net", "frgn_net"), 1_000_000, 10, "public_data_collector.py / KIS", "P0"),
    TableCheck("시장지표", "Kiwoom 투자자 수급", "kiwoom_investor_daily", "stock_code,dt", "dt", "stock_code || '|' || dt", ("frgnr_invsr", "orgn", "ind_invsr"), 100_000, 10, "Kiwoom ka10059", "P0"),
    TableCheck("시장지표", "외국인 보유", "foreign_holding_daily", "bas_dt,stock_code", "bas_dt", "bas_dt || '|' || stock_code", ("frgn_hold_pct",), 1_000_000, 10, "public_data_collector.py / Kiwoom", "P0"),
    TableCheck("시장지표", "Kiwoom 외국인 지분", "kiwoom_foreign_flow", "stock_code,dt", "dt", "stock_code || '|' || dt", ("weight", "limit_exhaust_rate"), 100_000, 10, "Kiwoom ka10008", "P0"),
    TableCheck("시장지표", "대차잔고 종목", "short_sell_daily", "bas_dt,stock_code", "bas_dt", "bas_dt || '|' || stock_code", ("borrow_bal_qty", "borrow_bal_amt"), 1_000_000, 10, "collect_short_5years.py / public_data_collector.py", "P0"),
    TableCheck("시장지표", "대차순위", "short_rank_daily", "bas_dt,isin_cd", "bas_dt", "bas_dt || '|' || COALESCE(isin_cd,stock_code)", ("lnb_rman_stck_cnt", "lnb_bal"), 500_000, 10, "collect_short_5years.py", "P1"),
    TableCheck("시장지표", "외국인 대차잔고", "short_foreign_balance", "bas_dt", "bas_dt", "bas_dt", ("forg_brw_bal",), 100, 45, "collect_short_5years.py", "P1", "월/시장 단위 보조지표라 종목 일별 테이블과 같은 행수 기준을 적용하지 않음"),
    TableCheck("시장지표", "월별 대차", "short_monthly_stat", "bas_dt", "bas_dt", "bas_dt", ("lnb_bal",), 100, 90, "collect_short_5years.py", "P1"),
    TableCheck("시장지표/텐버거", "프로그램 시장", "broker_program_market_daily", "source,dt,market", "dt", "source || '|' || dt || '|' || market", ("prog_net_buy_amt",), 500, 10, "KIS/Kiwoom program collector", "P0"),
    TableCheck("시장지표/텐버거", "프로그램 종목", "broker_program_stock_daily", "source,stock_code,dt,market_channel", "dt", "source || '|' || stock_code || '|' || dt || '|' || market_channel", ("net_buy_amt_krw",), 50_000, 10, "Kiwoom program collector", "P1"),
    TableCheck("공시/이벤트/텐버거", "희석 이벤트", "dilution_events", "stock_code,rcept_no", "disclosed_at", "stock_code || '|' || rcept_no", ("event_type",), 10_000, None, "DART dilution collector", "P0", "CB/BW/EB/유무상증자 이벤트 건수와 금액 필드 커버리지를 분리해서 해석"),
    TableCheck("재무/텐버거/DART Excel", "연결 재무", "financial_data", "stock_code,year,quarter,is_annual,report_type", "year", "stock_code || '|' || year || '|' || quarter || '|' || is_annual || '|' || report_type", ("revenue", "operating_profit"), 40_000, None, "DART/FnGuide batch", "P0"),
    TableCheck("재무/텐버거/DART Excel", "표준 재무", "canonical_financial_data", "stock_code,year,quarter,is_annual,report_type", "year", "stock_code || '|' || year || '|' || quarter || '|' || is_annual || '|' || report_type", ("revenue", "operating_profit"), 20_000, None, "canonical rebuild", "P1"),
    TableCheck("재무/텐버거/DART Excel", "현금흐름", "cash_flow_data", "stock_code,year,quarter,is_annual,report_type", "year", "stock_code || '|' || year || '|' || quarter || '|' || is_annual || '|' || report_type", ("operating_cf", "capex"), 20_000, None, "DART cashflow batch", "P0"),
    TableCheck("재무/텐버거/DART Excel", "표준 현금흐름", "canonical_cashflow_data", "stock_code,year,quarter,is_annual,report_type", "year", "stock_code || '|' || year || '|' || quarter || '|' || is_annual || '|' || report_type", ("operating_cf", "capex"), 20_000, None, "canonical cashflow rebuild", "P1"),
    TableCheck("재무/텐버거", "매입재료비", "dart_material_purchase", "stock_code,year,report_type", "year", "stock_code || '|' || year || '|' || report_type", ("material_purchase_krw",), 3_000, None, "DART material collector", "P0"),
    TableCheck("재무/텐버거", "수주잔고", "order_backlog", "stock_code,year,quarter", "year", "stock_code || '|' || year || '|' || quarter", ("backlog_normalized",), 1_000, None, "DART backlog collector", "P0"),
    TableCheck("재무/텐버거", "세그먼트 매출", "segment_revenue", "stock_code,year,quarter,segment_name", "year", "stock_code || '|' || year || '|' || quarter || '|' || segment_name", ("revenue",), 1_000, None, "DART segment collector", "P0"),
    TableCheck("고용 페이지", "NPS 월별", "nps_workplace_monthly", "ym,stock_code", "ym", "ym || '|' || stock_code", ("nw_acqzr_cnt", "lss_jnngp_cnt"), 10_000, 75, "employment_monitor.collect_nps_workplace", "P0"),
    # 2026-07-17 수정: min_rows 5,000 → 1,200. DART empSttus는 전 상장사가 아니라 일부만 공시하는
    # 선택적 항목 — financial_data 커버 종목 2,751개 중 실측 커버리지는 1,011개(37%), 종목당 평균 1.3행.
    # 완전 커버 상한이 아니라 collector 재발 회귀를 잡을 수 있는 현재 달성치(1,332행) 근접 하한으로 설정.
    TableCheck("고용/재무", "DART 임직원", "dart_employee_count", "stock_code,year,reprt_code,acmtn_dscd", "year", "stock_code || '|' || year || '|' || reprt_code || '|' || COALESCE(acmtn_dscd, '')", ("total_emp",), 1_200, None, "DART employee collector", "P1"),
    TableCheck("컨센서스/종목", "컨센서스", "consensus_targets", "report_idx 또는 자연키", "report_date", "COALESCE(CAST(report_idx AS TEXT), stock_code || '|' || report_date || '|' || securities_firm || '|' || analyst || '|' || report_title || '|' || target_price)", ("target_price",), 1_000, 45, "collect_consensus", "P2", "report_idx가 없는 한경 리포트는 자연키로 중복 판정"),
    TableCheck("텐버거", "텐버거 결과", "tenbagger_results", "run_time,stock_code", "run_time", "run_time || '|' || stock_code", ("total_score", "current_price"), 10, 7, "routes/tenbagger run", "P0"),
    TableCheck("텐버거", "실적 시그널", "earnings_signals", "stock_code,year,quarter,signal_type", "year", "stock_code || '|' || year || '|' || quarter || '|' || signal_type", ("signal_type",), 1, None, "earnings signal scan", "P1"),
    TableCheck("퀀트 주요지표", "주요지표 시계열", "quant_major_indicator_series", "indicator_key,period,series_name,source_name", "period", "indicator_key || '|' || period || '|' || series_name || '|' || source_name", ("value",), 100, 75, "scripts/ops/quant_indicators_cron.py", "P1"),
    TableCheck("마켓 레이더", "섹터 가격 캐시", "radar_price_cache", "ticker,rn", "trade_date", "ticker || '|' || rn", ("close",), 100, 30, "market_radar refresh-cache", "P2"),
]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type IN ('table','view')", (table,)).fetchone() is not None


def date_age_days(max_value: str | int | float | None) -> int | None:
    if max_value is None:
        return None
    s = str(max_value)[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y%m", "%Y"):
        try:
            d = datetime.strptime(s[: len(datetime.now().strftime(fmt))], fmt).date()
            if fmt == "%Y":
                d = date(int(s[:4]), 12, 31)
            elif fmt == "%Y%m":
                # 2026-07-17 수정: 월별 원천(예: YYYYMM=202605)은 그 달 전체를 대표하는 값이므로
                # 월초(day=1)가 아니라 월말 기준으로 age를 계산해야 함.
                # 월초 기준이면 실제로는 정상 공개월인데도 최대 ~30일 과대 staleness가 발생
                # (예: 202605 → 월초 기준 5/1 대비 77일, 월말 기준 5/31 대비 47일).
                y, m = int(s[:4]), int(s[4:6])
                last_day = calendar.monthrange(y, m)[1]
                d = date(y, m, last_day)
            return (date.today() - d).days
        except Exception:
            continue
    return None


def q_scalar(conn: sqlite3.Connection, sql: str):
    return conn.execute(sql).fetchone()[0]


def audit_one(conn: sqlite3.Connection, check: TableCheck) -> dict:
    result = asdict(check)
    result.update({
        "exists": table_exists(conn, check.table),
        "rows": 0,
        "distinct_keys": None,
        "duplicate_keys": None,
        "min_date": None,
        "max_date": None,
        "age_days": None,
        "nulls": {},
        "status": "missing",
        "severity": "critical" if check.priority == "P0" else "high",
        "issues": [],
    })
    if not result["exists"]:
        result["issues"].append("table_missing")
        return result

    try:
        rows = q_scalar(conn, f"SELECT COUNT(*) FROM {check.table}")
        result["rows"] = rows
    except Exception as exc:
        result["issues"].append(f"count_error:{exc}")
        return result

    if rows < check.min_rows:
        result["issues"].append(f"low_volume:{rows}<{check.min_rows}")

    if check.key_expr:
        try:
            distinct_keys = q_scalar(conn, f"SELECT COUNT(DISTINCT {check.key_expr}) FROM {check.table}")
            result["distinct_keys"] = distinct_keys
            dup = rows - distinct_keys
            result["duplicate_keys"] = dup
            if dup > 0:
                result["issues"].append(f"duplicate_grain:{dup}")
        except Exception as exc:
            result["issues"].append(f"key_check_error:{exc}")

    if check.date_col:
        try:
            min_dt, max_dt = conn.execute(f"SELECT MIN({check.date_col}), MAX({check.date_col}) FROM {check.table}").fetchone()
            result["min_date"] = min_dt
            result["max_date"] = max_dt
            age = date_age_days(max_dt)
            result["age_days"] = age
            if check.freshness_days is not None and (age is None or age > check.freshness_days):
                result["issues"].append(f"stale:{age}d>{check.freshness_days}d")
        except Exception as exc:
            result["issues"].append(f"date_check_error:{exc}")

    for col in check.required_cols:
        try:
            nulls = q_scalar(conn, f"SELECT SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) FROM {check.table}")
            rate = (nulls or 0) / rows if rows else 1
            result["nulls"][col] = {"count": nulls or 0, "rate": round(rate, 4)}
            if rows and rate > 0.5:
                result["issues"].append(f"high_null:{col}:{rate:.1%}")
        except Exception as exc:
            result["issues"].append(f"null_check_error:{col}:{exc}")

    if check.table == "broker_program_stock_daily" and rows:
        try:
            latest = result.get("max_date")
            latest_codes = q_scalar(
                conn,
                f"SELECT COUNT(DISTINCT stock_code) FROM {check.table} WHERE dt = '{latest}'",
            )
            result["latest_distinct_stocks"] = latest_codes
            if latest_codes < 2000:
                result["issues"].append(f"latest_day_low_coverage:{latest_codes}<2000")
        except Exception as exc:
            result["issues"].append(f"latest_coverage_error:{exc}")

    if not result["issues"]:
        result["status"] = "ok"
        result["severity"] = "none"
    elif any(x.startswith(("table_missing", "low_volume", "stale")) for x in result["issues"]):
        result["status"] = "needs_collection"
    else:
        result["status"] = "unstable_or_needs_review"
    return result


def _by_table(results: list[dict], table: str) -> dict | None:
    return next((r for r in results if r.get("table") == table), None)


def _covered_by(result: dict, source: str, reason: str) -> None:
    result["status"] = "ok_with_fallback"
    result["severity"] = "none"
    result["issues"] = [f"covered_by:{source}", reason]


def _append_note(result: dict, note: str) -> None:
    existing = result.get("notes") or ""
    result["notes"] = f"{existing} / {note}" if existing else note


def apply_dynamic_coverage_notes(conn: sqlite3.Connection, results: list[dict]) -> None:
    """Add DB-derived coverage figures for fields where row count alone is misleading."""
    segment = _by_table(results, "segment_revenue")
    if segment and segment.get("exists"):
        try:
            active_total = q_scalar(
                conn,
                """
                SELECT COUNT(DISTINCT stock_code)
                FROM stock_universe
                WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND market IN ('KOSPI','KOSDAQ')
                """,
            )
            covered = q_scalar(
                conn,
                """
                SELECT COUNT(DISTINCT s.stock_code)
                FROM segment_revenue s
                JOIN stock_universe u ON u.stock_code = s.stock_code
                WHERE u.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND u.market IN ('KOSPI','KOSDAQ')
                """,
            )
            pct = (covered / active_total * 100) if active_total else 0
            segment["active_stock_total"] = active_total
            segment["active_stock_covered"] = covered
            segment["active_stock_coverage_pct"] = round(pct, 2)
            _append_note(segment, f"활성 KOSPI/KOSDAQ 종목 커버리지 {covered:,}/{active_total:,} ({pct:.1f}%)")
        except Exception as exc:
            segment.setdefault("issues", []).append(f"coverage_note_error:segment_revenue:{exc}")

    dilution = _by_table(results, "dilution_events")
    if dilution and dilution.get("exists"):
        try:
            total, with_amount, stocks_with_amount = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_rows,
                  SUM(CASE WHEN issue_amount IS NOT NULL AND issue_amount > 0 THEN 1 ELSE 0 END) AS with_issue_amount,
                  COUNT(DISTINCT CASE WHEN issue_amount IS NOT NULL AND issue_amount > 0 THEN stock_code END) AS stocks_with_issue_amount
                FROM dilution_events
                """
            ).fetchone()
            pct = (with_amount / total * 100) if total else 0
            dilution["issue_amount_rows"] = with_amount
            dilution["issue_amount_stocks"] = stocks_with_amount
            dilution["issue_amount_coverage_pct"] = round(pct, 2)
            dilution["issue_amount_status"] = "partial" if pct < 80 else "ok"
            _append_note(
                dilution,
                f"issue_amount 실채움 {with_amount:,}/{total:,} ({pct:.2f}%), {stocks_with_amount:,}종목. "
                f"80% 미만이면 금액 기반 희석 리스크는 부분완료로 간주하고, 금액 미추출 행은 건수 기반 리스크로만 사용",
            )
            if pct < 80:
                dilution.setdefault("issues", []).append(f"partial_field:issue_amount:{pct:.2f}%<80%")
                if dilution.get("status") == "ok":
                    dilution["status"] = "unstable_or_needs_review"
                    dilution["severity"] = "high"
        except Exception as exc:
            dilution.setdefault("issues", []).append(f"coverage_note_error:dilution_events:{exc}")


def apply_operational_fallbacks(results: list[dict]) -> None:
    """운영 코드가 이미 쓰는 대체/표준 테이블을 감사 결과에 반영한다."""
    price = _by_table(results, "price_history")
    universe = _by_table(results, "stock_universe")
    public_price = _by_table(results, "stock_price_daily")
    if public_price and public_price["status"] != "ok":
        if price and price["status"] == "ok" and universe and universe["rows"] >= 2000:
            _covered_by(public_price, "price_history+stock_universe", "stock_price_daily의 market_cap 결측은 stock_universe로 보완 가능")

    investor = _by_table(results, "investor_trading_daily")
    kiwoom_inv = _by_table(results, "kiwoom_investor_daily")
    if investor and investor["status"] != "ok" and kiwoom_inv and kiwoom_inv["status"] == "ok":
        _covered_by(investor, "kiwoom_investor_daily", "공공 투자자별 매매 API 미신청/저용량 구간은 Kiwoom ka10059로 보완")

    foreign = _by_table(results, "foreign_holding_daily")
    kiwoom_foreign = _by_table(results, "kiwoom_foreign_flow")
    if foreign and foreign["status"] != "ok" and kiwoom_foreign and kiwoom_foreign["status"] == "ok":
        _covered_by(foreign, "kiwoom_foreign_flow", "공공 외국인 보유 API 미신청/지연 구간은 Kiwoom ka10008로 보완")

    short_sell = _by_table(results, "short_sell_daily")
    short_rank = _by_table(results, "short_rank_daily")
    if short_sell and short_sell["status"] != "ok" and short_rank and short_rank["status"] == "ok":
        high_null_amt = any(str(i).startswith("high_null:borrow_bal_amt") for i in short_sell.get("issues", []))
        if high_null_amt:
            _covered_by(short_sell, "short_rank_daily.lnb_bal", "종목별 API가 잔고금액을 제공하지 않는 구간은 대차순위 금액 필드로 보완")

    financial = _by_table(results, "financial_data")
    canonical_fin = _by_table(results, "canonical_financial_data")
    if financial and financial["status"] != "ok" and canonical_fin and canonical_fin["status"] == "ok":
        _covered_by(financial, "canonical_financial_data", "raw financial_data 중복 grain은 표준 테이블에서 해소")

    cashflow = _by_table(results, "cash_flow_data")
    canonical_cash = _by_table(results, "canonical_cashflow_data")
    if cashflow and cashflow["status"] != "ok" and canonical_cash and canonical_cash["status"] == "ok":
        _covered_by(cashflow, "canonical_cashflow_data", "raw cash_flow_data 중복 grain은 표준 테이블에서 해소")

    radar = _by_table(results, "radar_price_cache")
    if radar and radar["status"] != "ok" and US_MARKET_DB.exists():
        try:
            us_conn = sqlite3.connect(US_MARKET_DB)
            row = us_conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(date), MAX(date) FROM us_price_history"
            ).fetchone()
            us_conn.close()
            rows, tickers, min_dt, max_dt = row
            age = date_age_days(max_dt)
            if rows and rows >= 1000 and tickers >= 100 and age is not None and age <= 30:
                radar["min_date"] = min_dt
                radar["max_date"] = max_dt
                radar["age_days"] = age
                _covered_by(radar, "us_market.db.us_price_history", "마켓 레이더 해외 가격은 현재 radar_price_cache가 아니라 us_price_history를 사용")
        except Exception as exc:
            radar.setdefault("issues", []).append(f"us_price_history_check_error:{exc}")


def apply_dataset_contracts(results: list[dict]) -> None:
    """Override broad calendar-day checks with page-facing cadence contracts."""
    health_by_table = {item["table"]: item for item in evaluate_all_contracts(use_cache=False)}
    for result in results:
        health = health_by_table.get(result["table"])
        if not health or health["status"] == "healthy":
            continue
        result["status"] = "needs_collection"
        result["severity"] = "critical" if result.get("priority") == "P0" else "high"
        result["issues"] = [
            issue for issue in result.get("issues", [])
            if not str(issue).startswith("covered_by:")
        ]
        result["issues"].extend(
            f"contract:{issue}" for issue in health.get("issues", [])
            if f"contract:{issue}" not in result["issues"]
        )
        result["contract_expected_as_of"] = health.get("expected_as_of")
        result["contract_latest_coverage"] = health.get("latest_coverage")


def audit_hs_trade_lab() -> list[dict]:
    checks = [
        TableCheck("HS/시그널 영향성", "HS 월간 확정 수출입", "customs_monthly_record", "endpoint,period_ym,hs_code", "CASE WHEN period_ym GLOB '20[0-9][0-9]-[0-1][0-9]' THEN period_ym END", "endpoint || '|' || period_ym || '|' || hs_code || '|' || country_code || '|' || sido_code || '|' || imex_type_code || '|' || nature_code || '|' || unified_nature_code", ("export_value", "import_value"), 1_000_000, 75, "hs_trade_lab/scripts/daily_refresh.py", "P0"),
        TableCheck("HS/시그널 영향성", "분석2 섹터-HS 캐시", "analysis2_sector_hs_monthly_cache", "sector_key,period_ym,hs_code", "period_ym", "sector_key || '|' || period_ym || '|' || hs_code", ("export_value", "import_value"), 10_000, 75, "rebuild_analysis2_cache.py", "P0"),
        TableCheck("HS/시그널 영향성", "분석2 기업-HS 캐시", "analysis2_company_hs_monthly_cache", "sector_key,stock_code,period_ym,hs_code,flow_type", "period_ym", "sector_key || '|' || stock_code || '|' || period_ym || '|' || hs_code || '|' || flow_type", ("export_value", "import_value"), 50_000, 75, "rebuild_analysis2_cache.py", "P0"),
        TableCheck("HS/시그널 영향성", "10일 잠정 수출입", "customs_provisional_10day_record", "dataset_key,category_code,period_ym,period_day", "period_ym", "dataset_key || '|' || category_code || '|' || period_ym || '|' || period_day", ("amount_thousand_usd",), 100, 45, "collect_provisional_10day.py", "P1"),
    ]
    conn = sqlite3.connect(HS_DB)
    conn.row_factory = sqlite3.Row
    try:
        results = [audit_one(conn, c) for c in checks]
        for r in results:
            r["db"] = str(HS_DB)
        return results
    finally:
        conn.close()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        results = [audit_one(conn, c) for c in CHECKS]
        apply_dynamic_coverage_notes(conn, results)
        apply_operational_fallbacks(results)
        apply_dataset_contracts(results)
    finally:
        conn.close()
    for r in results:
        r["db"] = str(DB)
    if HS_DB.exists():
        results.extend(audit_hs_trade_lab())

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(DB),
        "additional_dbs": [str(HS_DB)] if HS_DB.exists() else [],
        "results": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for r in results if r["status"] in ("ok", "ok_with_fallback")),
            "needs_collection": sum(1 for r in results if r["status"] == "needs_collection"),
            "unstable_or_needs_review": sum(1 for r in results if r["status"] == "unstable_or_needs_review"),
            "missing": sum(1 for r in results if not r["exists"]),
        },
    }

    stamp = date.today().strftime("%Y%m%d")
    out_json = OUT_DIR / f"all_page_data_quality_{stamp}.json"
    out_md = OUT_DIR / f"all_page_data_quality_{stamp}.md"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 전체 페이지 데이터 품질 감사 — {date.today().isoformat()}",
        "",
        f"- DB: `{DB}`",
        f"- HS DB: `{HS_DB}`",
        f"- 생성: `{payload['generated_at']}`",
        f"- 요약: OK {payload['summary']['ok']} / 수집필요 {payload['summary']['needs_collection']} / 검토필요 {payload['summary']['unstable_or_needs_review']} / 누락 {payload['summary']['missing']}",
        "",
        "|페이지|데이터셋|테이블|상태|행수|기간|이슈|수집/보강|메모|",
        "|---|---|---:|---|---:|---|---|---|---|",
    ]
    for r in results:
        period = f"{r.get('min_date')} ~ {r.get('max_date')}"
        issues = "<br>".join(r["issues"]) if r["issues"] else "-"
        lines.append(
            f"|{r['page']}|{r['dataset']}|`{r['table']}`|{r['status']}|{r['rows']}|{period}|{issues}|{r['collector']}|{r.get('notes') or '-'}|"
        )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
