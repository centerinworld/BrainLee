"""Collection run ledger and dataset freshness contracts.

The run ledger intentionally lives outside stock.db so a lock conflict on the
main database cannot hide the failed or delayed collection attempt.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any

from trading_calendar import is_trading_day


ROOT = Path(__file__).resolve().parent
STOCK_DB = ROOT / "stock.db"
EMPLOYMENT_DB = ROOT / "employment_monitor" / "employment.db"
ETF_DB = ROOT / "ETF_check" / "etf_check.db"
HS_DB = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
LEDGER_DB = ROOT / "data" / "collection_health.db"
_HEALTH_CACHE_TTL_SECONDS = 300
_health_cache_lock = threading.Lock()
_health_cache: dict[str, Any] = {"checked_at": 0.0, "items": [], "refreshing": False}


@dataclass(frozen=True)
class DatasetContract:
    key: str
    label: str
    db_path: Path
    table: str
    source_date_col: str
    cadence: str = "kr_daily"
    ready_hour: int = 20
    ready_minute: int = 0
    allowed_lag: int = 0
    min_latest_coverage: int = 0
    coverage_expr: str = "COUNT(*)"
    collected_at_col: str | None = None
    source_filter: str | None = None
    source: str = ""
    schedule: str = ""


DATASET_CONTRACTS: tuple[DatasetContract, ...] = (
    DatasetContract("kr_price", "국내 주가", STOCK_DB, "price_history", "date", ready_hour=16,
                    min_latest_coverage=2000, coverage_expr="COUNT(DISTINCT stock_code)", source="KIS/KRX", schedule="영업일 장중·장마감"),
    DatasetContract("program_market", "프로그램 매매(시장)", STOCK_DB, "broker_program_market_daily", "dt", ready_hour=19,
                    min_latest_coverage=2, coverage_expr="COUNT(DISTINCT market)", collected_at_col="updated_at", source="KIS/Kiwoom", schedule="영업일 18:20"),
    DatasetContract("program_stock", "프로그램 매매(종목별)", STOCK_DB, "broker_program_stock_daily", "dt", ready_hour=20, ready_minute=30,
                    min_latest_coverage=2000, coverage_expr="COUNT(DISTINCT stock_code)", collected_at_col="updated_at", source="Kiwoom", schedule="영업일 18:50"),
    DatasetContract("investor_flow", "투자자 수급", STOCK_DB, "kiwoom_investor_daily", "dt", ready_hour=20,
                    min_latest_coverage=2000, coverage_expr="COUNT(DISTINCT stock_code)", collected_at_col="updated_at", source="Kiwoom", schedule="영업일 19:00"),
    DatasetContract("foreign_holding", "외국인 지분", STOCK_DB, "kiwoom_foreign_flow", "dt", ready_hour=20,
                    min_latest_coverage=2000, coverage_expr="COUNT(DISTINCT stock_code)", collected_at_col="updated_at", source="Kiwoom", schedule="영업일 19:15"),
    DatasetContract("short_balance", "대차·공매도", STOCK_DB, "short_rank_daily", "bas_dt", ready_hour=21,
                    allowed_lag=1, min_latest_coverage=1000, coverage_expr="COUNT(DISTINCT COALESCE(stock_code, isin_cd))", source="KRX/KIS", schedule="영업일"),
    DatasetContract("sector_index", "섹터 지수", STOCK_DB, "sector_index_daily", "date", ready_hour=19,
                    min_latest_coverage=10, coverage_expr="COUNT(DISTINCT market || '|' || sector)", source="KRX", schedule="영업일"),
    # 이벤트 구동형: 스냅샷 스크립트는 매일 돌지만, 조건을 만족하는 종목이 없으면 0행을 남기는 것이 정상이다.
    # 실측(2026-08-08): 8/1~8/5에는 기아/현대차/현대모비스 3종목이 '원/달러 나쁨(red)'으로 잡혔다가,
    # 원달러가 1,435→1,407로 내리면서 자동차 red 조건이 해제되어 8/6부터 0행 — 원천 series는 8/7까지 정상 적재됨.
    # 즉 '최신일에 행이 없음'을 stale로 보면 신호가 잠잠한 정상 구간마다 오탐한다. 잡 자체의 실패는
    # collection_job_runs(퀀트지표트리거)로 이미 추적되므로, 여기서는 오래 침묵할 때만 경고하도록 30일 여유를 둔다.
    DatasetContract("quant_signals", "퀀트 종목 시그널", STOCK_DB, "quant_stock_trade_signal_snapshots", "signal_date", ready_hour=9,
                    allowed_lag=30,
                    min_latest_coverage=1, coverage_expr="COUNT(DISTINCT stock_code)", source="퀀트 엔진",
                    schedule="매일 07:40 (이벤트 구동 — 조건 미충족 시 0행이 정상)"),
    DatasetContract("global_macro_fast", "글로벌 거시 fast 지표", STOCK_DB, "global_macro_data", "date", cadence="us_daily",
                    ready_hour=7, allowed_lag=2, min_latest_coverage=5,
                    coverage_expr="COUNT(DISTINCT indicator_code)",
                    source_filter=(
                        "indicator_code IN ("
                        "'US_VIX','US_SP500','US_DXY','COMM_COPPER','COMM_GOLD',"
                        "'COMM_OIL_WTI','KR_USD_KRW','US_10Y_BREAKEVEN','US_HY_SPREAD','US_BAA_SPREAD'"
                        ") AND date <= date('now','+1 day')"
                    ),
                    source="Yahoo/FRED/EIA/ECOS", schedule="매일 06:45"),
    DatasetContract("quant_macro_bridge", "퀀트 거시지표 브릿지", STOCK_DB, "quant_major_indicator_series", "period", cadence="us_daily",
                    ready_hour=20, allowed_lag=2, min_latest_coverage=5,
                    coverage_expr="COUNT(DISTINCT indicator_key)",
                    source_filter="indicator_key LIKE 'macro:%' AND period <= date('now','+1 day')",
                    source="global_macro_data → quant_major_indicator_series", schedule="매일 19:35"),
    DatasetContract("us_price", "미국 주가", STOCK_DB, "us_price_history", "date", cadence="us_daily",
                    ready_hour=7, allowed_lag=3, min_latest_coverage=3000,
                    coverage_expr="COUNT(DISTINCT ticker)", source="yfinance", schedule="미국장 마감 후 06:30 KST"),
    DatasetContract("us_factor", "미국 팩터", STOCK_DB, "us_factor_snapshot", "as_of_date", cadence="us_daily",
                    ready_hour=7, allowed_lag=3, min_latest_coverage=3000,
                    coverage_expr="COUNT(DISTINCT ticker)", source="yfinance+US 재무", schedule="미국장 마감 후 06:30 KST"),
    DatasetContract("tenbagger", "텐버거 결과", STOCK_DB, "tenbagger_results", "run_time", cadence="calendar", allowed_lag=2,
                    min_latest_coverage=1, coverage_expr="COUNT(DISTINCT stock_code)", source="텐버거 엔진", schedule="매일"),
    DatasetContract("consensus", "컨센서스", STOCK_DB, "consensus_targets", "report_date", cadence="calendar", allowed_lag=7,
                    min_latest_coverage=1, coverage_expr="COUNT(*)", source="한경 컨센서스", schedule="매일 04:00"),
    DatasetContract("dart_contracts", "수주 공시", STOCK_DB, "dart_contracts", "disclosed_at", cadence="calendar", allowed_lag=3,
                    min_latest_coverage=1, coverage_expr="COUNT(*)", collected_at_col="created_at", source="DART", schedule="매일 3회"),
    DatasetContract("telegram", "텔레그램 채널", STOCK_DB, "telegram_channels", "last_sync", cadence="calendar", allowed_lag=1,
                    min_latest_coverage=1, coverage_expr="COUNT(*)", collected_at_col="last_sync", source="Telegram API", schedule="매일"),
    DatasetContract("etf", "ETF 구성", ETF_DB, "etf_inclusion_daily", "trade_date", ready_hour=19,
                    min_latest_coverage=500,
                    coverage_expr="SUM(CASE WHEN COALESCE(etf_amount, 0) > 0 THEN 1 ELSE 0 END)",
                    source="ETF CHECK", schedule="영업일 20:30"),
    DatasetContract("employment_wlb", "고용보험", EMPLOYMENT_DB, "wlb_monthly", "data_ym", cadence="monthly", allowed_lag=75,
                    min_latest_coverage=1000, coverage_expr="COUNT(*)", collected_at_col="fetched_at", source="근로복지공단", schedule="매일 변화감지"),
    DatasetContract("hs_confirmed", "HS 월간 확정", HS_DB, "customs_monthly_record", "period_ym", cadence="monthly", allowed_lag=75,
                    min_latest_coverage=1000, coverage_expr="COUNT(*)", collected_at_col="updated_at",
                    source_filter="period_ym GLOB '20[0-9][0-9]-[0-1][0-9]'", source="관세청", schedule="월간"),
    # 2026-08-14: public_data_collector.py --company-only(월요일 07:00)가 KRX getItemInfo를
    # 호출하지만 2026-06-24 로그 시작 이후 8주 넘게 단 1건도 저장된 적이 없음(원인 미상,
    # investor_trading_daily처럼 확정 폐지 근거는 없어 수집 자체는 유지) — 설정페이지에서
    # 실패가 보이도록 등록. allowed_lag=10은 주1회 수집 스케줄(최대 2주 간격)을 감안한 여유.
    DatasetContract("listed_company_info", "상장회사 기본정보(공공데이터)", STOCK_DB, "listed_company_info", "bas_dt",
                    cadence="calendar", allowed_lag=10,
                    min_latest_coverage=1, coverage_expr="COUNT(*)", source="공공데이터포털", schedule="매주 월요일 07:00"),
)


CONTRACT_BY_KEY = {contract.key: contract for contract in DATASET_CONTRACTS}
JOB_DATASET_KEYS = {
    "KRX프로그램매매": ("program_market",),
    "종목프로그램매매": ("program_stock",),
    "키움투자자수급": ("investor_flow",),
    "섹터지수보완": ("sector_index",),
    "미국일별시세팩터수집": ("us_price", "us_factor"),
    "글로벌매크로수집": ("global_macro_fast",),
    "퀀트주요지표일일": ("quant_macro_bridge",),
    "퀀트지표트리거": ("quant_signals",),
}


def _connect_ledger() -> sqlite3.Connection:
    LEDGER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LEDGER_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_job_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds REAL,
            error TEXT,
            details_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_collection_runs_job_started ON collection_job_runs(job_name, started_at DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_health_snapshot (
            snapshot_key TEXT PRIMARY KEY,
            checked_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def start_collection_run(job_name: str, attempt: int = 1) -> int:
    conn = _connect_ledger()
    try:
        cur = conn.execute(
            "INSERT INTO collection_job_runs(job_name,attempt,status,started_at) VALUES(?,?,?,?)",
            (job_name, attempt, "running", datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def finish_collection_run(run_id: int, status: str, *, error: str | None = None, details: dict[str, Any] | None = None) -> None:
    conn = _connect_ledger()
    try:
        now = datetime.now()
        row = conn.execute("SELECT started_at FROM collection_job_runs WHERE run_id=?", (run_id,)).fetchone()
        started = datetime.fromisoformat(row["started_at"]) if row else now
        conn.execute(
            """UPDATE collection_job_runs
               SET status=?,finished_at=?,duration_seconds=?,error=?,details_json=?
               WHERE run_id=?""",
            (status, now.isoformat(timespec="seconds"), round((now - started).total_seconds(), 3), error,
             json.dumps(details or {}, ensure_ascii=False, default=str), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def latest_collection_runs(limit: int = 100) -> list[dict[str, Any]]:
    if not LEDGER_DB.exists():
        return []
    conn = _connect_ledger()
    try:
        rows = conn.execute(
            """SELECT r.* FROM collection_job_runs r
               JOIN (SELECT job_name,MAX(run_id) run_id FROM collection_job_runs GROUP BY job_name) x
                 ON x.run_id=r.run_id
               ORDER BY r.started_at DESC LIMIT ?""",
            (max(1, min(limit, 500)),),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json") or "{}")
            except ValueError:
                item["details"] = {}
            result.append(item)
        return result
    finally:
        conn.close()


def interrupt_running_collection_runs(reason: str = "scheduler_restarted") -> int:
    """Close runs left in a running state by a prior process shutdown."""
    if not LEDGER_DB.exists():
        return 0
    conn = _connect_ledger()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            """UPDATE collection_job_runs
               SET status='interrupted',finished_at=?,error=?
               WHERE status='running'""",
            (now, reason),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()[:10]
    for fmt, length in (("%Y-%m-%d", 10), ("%Y%m%d", 8), ("%Y-%m", 7), ("%Y%m", 6)):
        try:
            parsed = datetime.strptime(text[:length], fmt).date()
            return parsed.replace(day=1) if fmt in {"%Y-%m", "%Y%m"} else parsed
        except ValueError:
            continue
    return None


def _expected_trading_date(now: datetime, contract: DatasetContract) -> date:
    candidate = now.date()
    cutoff = dt_time(contract.ready_hour, contract.ready_minute)
    if not is_trading_day(candidate, "KR") or now.time() < cutoff:
        candidate -= timedelta(days=1)
    while not is_trading_day(candidate, "KR"):
        candidate -= timedelta(days=1)
    return candidate


def _expected_us_trading_date(now: datetime, contract: DatasetContract) -> date:
    """Latest US session expected to be complete from a Korea-local clock."""
    cutoff = dt_time(contract.ready_hour, contract.ready_minute)
    candidate = now.date() - timedelta(days=1 if now.time() >= cutoff else 2)
    while not is_trading_day(candidate, "US"):
        candidate -= timedelta(days=1)
    return candidate


def _trading_lag(actual: date, expected: date, market: str = "KR") -> int:
    if actual >= expected:
        return 0
    lag = 0
    cursor = actual + timedelta(days=1)
    while cursor <= expected:
        if is_trading_day(cursor, market):  # type: ignore[arg-type]
            lag += 1
        cursor += timedelta(days=1)
    return lag


def evaluate_contract(contract: DatasetContract, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now()
    result: dict[str, Any] = {
        "key": contract.key, "label": contract.label, "table": contract.table,
        "source": contract.source, "schedule": contract.schedule, "status": "missing",
        "source_as_of": None, "collected_at": None, "expected_as_of": None,
        "lag": None, "latest_coverage": 0, "minimum_coverage": contract.min_latest_coverage,
        "issues": [],
    }
    if not contract.db_path.exists():
        result["issues"].append("database_missing")
        return result
    try:
        conn = sqlite3.connect(f"file:{contract.db_path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (contract.table,)).fetchone()
        if not exists:
            result["issues"].append("table_missing")
            conn.close()
            return result
        source_where = f" WHERE {contract.source_filter}" if contract.source_filter else ""
        latest = conn.execute(
            f"SELECT MAX({contract.source_date_col}) v FROM {contract.table}{source_where}"
        ).fetchone()["v"]
        result["source_as_of"] = latest
        if contract.collected_at_col:
            result["collected_at"] = conn.execute(
                f"SELECT MAX({contract.collected_at_col}) v FROM {contract.table}"
            ).fetchone()["v"]
    except (sqlite3.Error, OSError) as exc:
        result["status"] = "error"
        result["issues"].append(f"query_error:{exc}")
        return result

    actual = _parse_date(result["source_as_of"])
    if actual is None:
        result["issues"].append("source_date_missing")
        return result
    if contract.cadence == "kr_daily":
        expected = _expected_trading_date(now, contract)
        lag = _trading_lag(actual, expected)
        result["expected_as_of"] = expected.isoformat()
    elif contract.cadence == "us_daily":
        expected = _expected_us_trading_date(now, contract)
        lag = _trading_lag(actual, expected, "US")
        result["expected_as_of"] = expected.isoformat()
    else:
        expected = now.date()
        lag = max(0, (expected - actual).days)
        result["expected_as_of"] = expected.isoformat()
    if contract.min_latest_coverage:
        coverage_date = latest
        if contract.cadence in {"kr_daily", "us_daily"} and actual > expected:
            expected_text = expected.isoformat() if "-" in str(latest) else expected.strftime("%Y%m%d")
            where_parts = [f"{contract.source_date_col}<=?"]
            if contract.source_filter:
                where_parts.append(contract.source_filter)
            coverage_date = conn.execute(
                f"SELECT MAX({contract.source_date_col}) v FROM {contract.table} WHERE {' AND '.join(where_parts)}",
                (expected_text,),
            ).fetchone()["v"]
        if coverage_date is not None:
            where_parts = [f"{contract.source_date_col}=?"]
            if contract.source_filter:
                where_parts.append(contract.source_filter)
            row = conn.execute(
                f"SELECT {contract.coverage_expr} v FROM {contract.table} WHERE {' AND '.join(where_parts)}",
                (coverage_date,),
            ).fetchone()
            result["latest_coverage"] = int(row["v"] or 0)
            result["coverage_as_of"] = coverage_date
        if contract.key == "etf" and latest is not None:
            stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    SUM(CASE WHEN COALESCE(etf_amount, 0) > 0 THEN 1 ELSE 0 END) AS positive_rows,
                    SUM(CASE WHEN COALESCE(etf_amount, 0) = 0 AND COALESCE(etf_count, 0) = 0 THEN 1 ELSE 0 END) AS zero_rows,
                    MAX(collected_at) AS latest_collected_at
                FROM etf_inclusion_daily
                WHERE trade_date = ?
                """,
                (latest,),
            ).fetchone()
            result["row_count"] = int(stats["total_rows"] or 0)
            result["positive_rows"] = int(stats["positive_rows"] or 0)
            result["zero_rows"] = int(stats["zero_rows"] or 0)
            if stats["latest_collected_at"]:
                result["collected_at"] = stats["latest_collected_at"]
            latest_run = conn.execute(
                """
                SELECT id, started_at, finished_at, total_stocks, success, failed, status
                FROM collection_log
                WHERE run_date = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (latest,),
            ).fetchone()
            if latest_run:
                result["run_status"] = latest_run["status"]
                result["run_success"] = int(latest_run["success"] or 0)
                result["run_failed"] = int(latest_run["failed"] or 0)
                if latest_run["status"] != "done":
                    result["issues"].append(f"run_status:{latest_run['status']}")
    conn.close()
    result["lag"] = lag
    if lag > contract.allowed_lag:
        result["issues"].append(f"stale:{lag}>{contract.allowed_lag}")
    if contract.min_latest_coverage and result["latest_coverage"] < contract.min_latest_coverage:
        result["issues"].append(
            f"partial:{result['latest_coverage']}<{contract.min_latest_coverage}"
        )
    if any(issue.startswith("stale:") for issue in result["issues"]):
        result["status"] = "stale"
    elif any(issue.startswith("run_status:") for issue in result["issues"]):
        result["status"] = "error"
    elif any(issue.startswith("partial:") for issue in result["issues"]):
        result["status"] = "partial"
    else:
        result["status"] = "healthy"
    return result


def _load_health_snapshot(max_age_seconds: int) -> list[dict[str, Any]]:
    if not LEDGER_DB.exists():
        return []
    conn = _connect_ledger()
    try:
        row = conn.execute(
            "SELECT checked_at,payload_json FROM dataset_health_snapshot WHERE snapshot_key='all'"
        ).fetchone()
        if not row:
            return []
        checked_at = datetime.fromisoformat(row["checked_at"])
        if (datetime.now() - checked_at).total_seconds() > max_age_seconds:
            return []
        return json.loads(row["payload_json"])
    except (ValueError, sqlite3.Error):
        return []
    finally:
        conn.close()


def _save_health_snapshot(items: list[dict[str, Any]]) -> None:
    conn = _connect_ledger()
    try:
        conn.execute(
            """INSERT INTO dataset_health_snapshot(snapshot_key,checked_at,payload_json)
               VALUES('all',?,?)
               ON CONFLICT(snapshot_key) DO UPDATE SET
                 checked_at=excluded.checked_at,payload_json=excluded.payload_json""",
            (datetime.now().isoformat(timespec="seconds"), json.dumps(items, ensure_ascii=False, default=str)),
        )
        conn.commit()
    finally:
        conn.close()


def _refresh_health_cache() -> None:
    try:
        items = [evaluate_contract(contract) for contract in DATASET_CONTRACTS]
        _save_health_snapshot(items)
        with _health_cache_lock:
            _health_cache.update({
                "checked_at": time.monotonic(),
                "items": items,
                "refreshing": False,
            })
    except Exception:
        with _health_cache_lock:
            _health_cache["refreshing"] = False


def evaluate_all_contracts(
    now: datetime | None = None,
    *,
    use_cache: bool = True,
    cache_ttl_seconds: int = _HEALTH_CACHE_TTL_SECONDS,
) -> list[dict[str, Any]]:
    if now is not None or not use_cache:
        return [evaluate_contract(contract, now=now) for contract in DATASET_CONTRACTS]
    current = time.monotonic()
    with _health_cache_lock:
        if _health_cache["items"] and current - _health_cache["checked_at"] <= cache_ttl_seconds:
            return _health_cache["items"]
        persisted = _load_health_snapshot(cache_ttl_seconds)
        if persisted:
            _health_cache.update({"checked_at": current, "items": persisted})
            return persisted
        # A dashboard request must not wait for full scans across every dataset.
        # Serve the last known snapshot and refresh it once in the background.
        stale = _load_health_snapshot(7 * 24 * 60 * 60)
        if stale:
            _health_cache.update({"checked_at": current, "items": stale})
            if not _health_cache["refreshing"]:
                _health_cache["refreshing"] = True
                threading.Thread(
                    target=_refresh_health_cache,
                    daemon=True,
                    name="DatasetHealthRefresh",
                ).start()
            return stale
        items = [evaluate_contract(contract) for contract in DATASET_CONTRACTS]
        _save_health_snapshot(items)
        _health_cache.update({"checked_at": current, "items": items})
        return items


def evaluate_job_outputs(job_name: str, now: datetime | None = None) -> list[dict[str, Any]]:
    return [evaluate_contract(CONTRACT_BY_KEY[key], now=now) for key in JOB_DATASET_KEYS.get(job_name, ())]


def refresh_job_health_snapshot(job_name: str, outputs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Persist fresh health for a completed job without rescanning every dataset.

    Dashboard requests intentionally use a short-lived snapshot to avoid heavy
    scans.  A successful collector must nevertheless publish its own fresh
    result immediately; otherwise a completed repair can remain falsely stale
    until the cache TTL expires.
    """
    fresh = outputs if outputs is not None else evaluate_job_outputs(job_name)
    if not fresh:
        return []
    persisted = _load_health_snapshot(7 * 24 * 60 * 60)
    if persisted:
        by_key = {item.get("key"): item for item in persisted}
        by_key.update({item.get("key"): item for item in fresh})
        merged = list(by_key.values())
    else:
        merged = evaluate_all_contracts(use_cache=False)
    _save_health_snapshot(merged)
    with _health_cache_lock:
        _health_cache.update({
            "checked_at": time.monotonic(),
            "items": merged,
            "refreshing": False,
        })
    return fresh
