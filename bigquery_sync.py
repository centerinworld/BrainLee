"""
BigQuery 동기화 스크립트
stock.db → Google BigQuery (project-d8a62269-8156-4f96-870.stock_dashboard)

실행:
  python3 bigquery_sync.py --mode full      # 전체 테이블 최초 업로드
  python3 bigquery_sync.py --mode daily     # 일별 증분 업로드 (price_history 최근 7일)
  python3 bigquery_sync.py --mode table --table tenbagger_results  # 특정 테이블만
  python3 bigquery_sync.py --mode external  # hs_trade_lab.db, employment.db 추가 업로드
"""

import sqlite3
import pandas as pd
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/bigquery_sync.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────
PROJECT_ID  = os.getenv("BQ_PROJECT_ID", "project-d8a62269-8156-4f96-870")
DATASET_ID  = os.getenv("BQ_DATASET_ID", "stock_dashboard")
DB_PATH     = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
LOW_COST_MAX_BYTES_BILLED = int(os.getenv("BQ_MAX_BYTES_BILLED", str(20 * 1024**3)))  # 20GiB/query guard
EXTERNAL_DB_SOURCES = {
    "hs_trade": {
        "path": "/Volumes/Realtek_NVME/stock_dashboard/runtime/hs_trade_lab/data/hs_trade_lab.db",
        "prefix": "hs_trade",
        "tables": [
            "customs_monthly_record",
            "customs_provisional_10day_record",
            "analysis2_company_monthly_cache",
            "analysis2_company_hs_monthly_cache",
            "analysis2_sector_monthly_cache",
            "analysis2_sector_hs_monthly_cache",
            "trade_series_cache",
            "hs_code_company_map",
            "hs_company_market_share",
            "hs_sector_map",
            "companies",
            "hs_codes",
            "telegram_company_hs_flow_map",
            "telegram_trade_card",
        ],
    },
    "employment": {
        "path": "/Volumes/Realtek_NVME/stock_dashboard/runtime/employment_monitor/employment.db",
        "prefix": "employment",
        "tables": [
            "employment_company",
            "employment_monthly",
            "nps_monthly",
            "nps_seq_map",
            "stock_bizno_map",
            "stock_bizr_no_map",
            "wlb_monthly",
            "wlb_meta",
        ],
    },
}
CHUNK_SIZE  = 200_000  # BigQuery 업로드 단위

# ── 제외 테이블 (백업, 내부, 캐시, 운영 분석과 무관한 대용량 임시 테이블) ─────
EXCLUDE_TABLES = {
    "sqlite_sequence", "sqlite_stat1", "sqlite_stat4",
    "dart_disclosure_cache",          # 캐시
    "nps_workplace_monthly",          # API 차단, 빈 테이블
    "listed_company_info",            # 빈 테이블
    "sector_info",                    # 빈 테이블
    "stock_bizno_map",                # 내부 매핑
    "futures_contract_daily",         # 선물 (423K행, 주식 무관)
    "kiwoom_tick_history",            # 틱 데이터 (152K행, 단기 스냅샷)
    "kiwoom_minute_snapshot",         # 분 단위 스냅샷 (22K행)
    # ── 백업/수정 중 테이블 ──────────────────────────
    "financial_data_backup_20260412",
    "financial_data_backup_20260522",
    "financial_data_backup_annual_q4_dup_20260515",
    "financial_data_backup_cfs_dup_20260515",
    "financial_data_backup_comprehensive_sync_20260516",
    "financial_data_backup_fnguide_sync_20260516",
    "financial_data_backup_fnguide_sync_20260517",
    "financial_data_backup_fnguide_sync_20260518",
    "financial_data_backup_fnguide_sync_20260523",
    "financial_data_backup_fnguide_sync_20260526",
    "financial_data_backup_fnguide_sync_20260527",
    "financial_data_backup_fnguide_sync_20260528",
    "financial_data_backup_fnguide_sync_20260529",
    "financial_data_backup_ofs_20260515",
    "financial_data_backup_unit_error_20260515",
    "cash_flow_data_backup_ofs_20260515",
    # ── 0행 or 내부 운영 테이블 ──────────────────────
    "autotrade_guard_state",
    "cashflow_fix_log",
    "dart_major_holders",
    "forward_strategy_indicator_series",
    "kis_paper_orders", "kis_paper_positions", "kis_paper_realized",
    "seibro_financial_snapshot",
    "stock_base_info_changes",
    "turnover_breakout_live_log",
    "_codex_lock_test",
}

EXCLUDE_TABLE_PREFIXES = (
    "backup_",
    "data_quality_backup_",
    "financial_data_backup_",
    "cash_flow_data_backup_",
    "price_history_index_backup_",
    "report_files_backup_",
)

EXCLUDE_TABLE_CONTAINS = (
    "_backup_",
    "_fix_log",
)

# ── price_history는 증분 처리 (전체는 너무 큼) ───────────────────────
INCREMENTAL_TABLES = {"price_history", "us_price_history"}  # daily 모드에서 최근 N일만

# ── 저비용 BigQuery 실험 모드: 핵심 테이블만 적재 ─────────────────────
LOW_COST_DAILY_TABLES = [
    "stock_universe",
    "strategy_feature_snapshot",
    "quant_major_indicator_catalog",
    "quant_major_indicator_series",
    "cafe_quant_indicator_mappings",
    "cafe_stock_indicator_mappings",
    "indicator_sector_direction_rules",
    "order_contracts",
    "dart_backlog_quarterly",
    "dilution_events",
    "broker_program_stock_daily",
    "program_trading_daily",
    "short_foreign_balance",
    "stockeasy_sector_rs_daily",
    "sector_rotation_cache",
    "trigger_discovery_events",
    "trigger_discovery_stock_links",
    "trigger_discovery_forward_returns",
]

# ── BigQuery 테이블별 파티셔닝/클러스터링 설정 ───────────────────────
TABLE_OPTIONS = {
    "price_history": {
        "partition_field": "date",
        "clustering_fields": ["stock_code"],
    },
    "us_price_history": {
        "partition_field": "date",
        "clustering_fields": ["ticker"],
    },
    "financial_data": {
        "clustering_fields": ["stock_code"],
    },
    "canonical_financial_data": {
        "clustering_fields": ["stock_code"],
    },
    "canonical_cashflow_data": {
        "clustering_fields": ["stock_code"],
    },
    "cash_flow_data": {
        "clustering_fields": ["stock_code"],
    },
    "us_financial_data": {
        "clustering_fields": ["ticker"],
    },
    "us_cashflow_data": {
        "clustering_fields": ["ticker"],
    },
    "tenbagger_results": {
        "clustering_fields": ["stock_code"],
    },
    "dart_contracts": {
        "clustering_fields": ["stock_code", "ai_signal"],
    },
    "dart_disclosures": {
        "clustering_fields": ["stock_code"],
    },
    "kiwoom_investor_daily": {
        "clustering_fields": ["stock_code"],
    },
    "fin_quarterly_validation_flags": {
        "clustering_fields": ["stock_code"],
    },
    "financial_source_snapshot": {
        "clustering_fields": ["stock_code"],
    },
    "cf_validation_flags": {
        "clustering_fields": ["stock_code"],
    },
    "short_rank_daily": {
        "clustering_fields": ["stock_code"],
    },
    "short_sell_daily": {
        "clustering_fields": ["stock_code"],
    },
    "short_sector_daily": {
        "clustering_fields": ["stock_code"],
    },
    "foreign_holding_daily": {
        "partition_field": "bas_dt",
        "clustering_fields": ["stock_code"],
    },
    "investor_trading_daily": {
        "partition_field": "bas_dt",
        "clustering_fields": ["stock_code"],
    },
    "kiwoom_credit_balance": {
        "partition_field": "dt",
        "clustering_fields": ["stock_code"],
    },
    "kiwoom_foreign_flow": {
        "partition_field": "dt",
        "clustering_fields": ["stock_code"],
    },
    "margin_balance_daily": {
        "partition_field": "dt",
        "clustering_fields": ["stock_code"],
    },
    "quant_major_indicator_series": {
        "clustering_fields": ["indicator_key", "series_name"],
    },
    "quant_major_indicator_catalog": {
        "clustering_fields": ["indicator_key", "priority"],
    },
    "hs_trade_customs_monthly_record": {
        "clustering_fields": ["hs_code", "period_ym"],
    },
    "hs_trade_analysis2_company_monthly_cache": {
        "clustering_fields": ["stock_code", "sector_key"],
    },
    "hs_trade_analysis2_company_hs_monthly_cache": {
        "clustering_fields": ["stock_code", "hs_code"],
    },
    "hs_trade_analysis2_sector_monthly_cache": {
        "clustering_fields": ["sector_key"],
    },
    "hs_trade_analysis2_sector_hs_monthly_cache": {
        "clustering_fields": ["sector_key", "hs_code"],
    },
    "employment_nps_monthly": {
        "clustering_fields": ["stock_code"],
    },
    "employment_wlb_monthly": {
        "clustering_fields": ["stock_code"],
    },
    "trigger_discovery_events": {
        "clustering_fields": ["trigger_key", "available_date", "stock_code"],
    },
    "trigger_discovery_stock_links": {
        "clustering_fields": ["stock_code", "event_id"],
    },
    "trigger_discovery_forward_returns": {
        "clustering_fields": ["stock_code", "horizon_days", "event_id"],
    },
}


def get_bq_client():
    """BigQuery 클라이언트 반환 (Application Default Credentials 사용)"""
    try:
        from google.cloud import bigquery
        return bigquery.Client(project=PROJECT_ID)
    except ImportError:
        logger.error("google-cloud-bigquery 패키지가 없습니다. 설치: pip install google-cloud-bigquery pandas-gbq pyarrow")
        sys.exit(1)


def ensure_dataset(client):
    """데이터셋이 없으면 생성"""
    from google.cloud import bigquery
    dataset_ref = f"{PROJECT_ID}.{DATASET_ID}"
    try:
        client.get_dataset(dataset_ref)
        logger.info(f"데이터셋 확인: {dataset_ref}")
    except Exception:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"
        client.create_dataset(dataset, exists_ok=True)
        logger.info(f"데이터셋 생성: {dataset_ref}")


def is_excluded_table(table_name: str) -> bool:
    if table_name in EXCLUDE_TABLES:
        return True
    if any(table_name.startswith(prefix) for prefix in EXCLUDE_TABLE_PREFIXES):
        return True
    if any(part in table_name for part in EXCLUDE_TABLE_CONTAINS):
        return True
    return False


def get_sqlite_tables():
    """stock.db 테이블 목록 (제외 목록 필터링)"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    tables = [r[0] for r in rows if not is_excluded_table(r[0])]
    return tables


def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """BigQuery 호환 타입으로 변환"""
    for col in df.columns:
        # 컬럼명 특수문자 제거
        pass
    df.columns = [c.replace(" ", "_").replace("-", "_").replace(".", "_") for c in df.columns]

    for col in df.columns:
        if df[col].dtype == object:
            # 문자열로 변환
            df[col] = df[col].astype(str).where(df[col].notna(), None)
            # 'None' 문자열 → None
            df[col] = df[col].replace("None", None)
        elif str(df[col].dtype).startswith("float"):
            # inf 값 제거
            df[col] = df[col].replace([float("inf"), float("-inf")], None)

    # date 컬럼 → pandas datetime64[ns] (BigQuery가 TIMESTAMP로 자동 감지)
    # .dt.date → object dtype로 저장되어 BigQuery autodetect가 STRING으로 처리 → 비권장
    # 대신 pd.to_datetime → datetime64[ns]로 유지해야 BQ가 TIMESTAMP/DATE 감지
    for col in df.columns:
        if col.lower() in ("date", "dt", "bas_dt", "post_date", "disclosed_at", "created_at",
                           "contract_start", "contract_end", "analyzed_at", "snapshot_date",
                           "tx_date", "bought_at", "entry_date"):
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass

    return df


def _external_table_name(source_cfg: dict, table_name: str) -> str:
    return f"{source_cfg['prefix']}_{table_name}"


def drop_bq_table(client, table_name: str):
    """BQ 테이블 삭제 (스키마 불일치 수정 시 사용)"""
    from google.cloud.exceptions import NotFound
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    try:
        client.delete_table(table_ref)
        logger.info(f"  [{table_name}] BQ 테이블 삭제 완료 (스키마 재생성)")
    except NotFound:
        pass


# 스키마 불일치로 삭제 후 재생성이 필요한 테이블
RECREATE_TABLES = {"price_history", "triple_pattern_daily"}

# DATE 타입으로 변환이 필요한 컬럼 (테이블명 → 컬럼명 목록)
DATE_COLUMNS = {
    "triple_pattern_daily": ["run_date"],
    "tenbagger_daily_alerts": ["alert_date"],
}


def upload_table(client, table_name: str, df: pd.DataFrame, write_mode: str = "WRITE_TRUNCATE"):
    """DataFrame → BigQuery 업로드"""
    from google.cloud import bigquery

    if df.empty:
        logger.info(f"  [{table_name}] 0행 — 스킵")
        return

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    opts = TABLE_OPTIONS.get(table_name, {})

    # DATE 컬럼 변환
    for col in DATE_COLUMNS.get(table_name, []):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    # 스키마/파티셔닝/클러스터링 충돌 방지: 옵션이 있는 full-refresh 테이블은 재생성
    if write_mode == "WRITE_TRUNCATE" and (table_name in RECREATE_TABLES or bool(opts)):
        drop_bq_table(client, table_name)

    job_config = bigquery.LoadJobConfig(
        write_disposition=write_mode,
        autodetect=True,
    )

    # 파티셔닝은 daily full-refresh 대용량 테이블에서 partition modification quota를
    # 쉽게 초과하므로 사용하지 않는다. 날짜 컬럼은 그대로 보존하고 클러스터링만 적용한다.

    # 클러스터링 설정
    if write_mode == "WRITE_TRUNCATE" and "clustering_fields" in opts:
        valid = [f for f in opts["clustering_fields"] if f in df.columns]
        if valid:
            job_config.clustering_fields = valid[:4]

    total = len(df)
    uploaded = 0
    for i in range(0, total, CHUNK_SIZE):
        chunk = df.iloc[i : i + CHUNK_SIZE]
        job = client.load_table_from_dataframe(chunk, table_ref, job_config=job_config)
        job.result()
        uploaded += len(chunk)
        # 첫 청크만 WRITE_TRUNCATE, 이후는 WRITE_APPEND
        if write_mode == "WRITE_TRUNCATE" and i == 0:
            job_config.write_disposition = "WRITE_APPEND"
        logger.info(f"  [{table_name}] {uploaded:,}/{total:,}행 업로드 완료")

    logger.info(f"  [{table_name}] ✅ 총 {total:,}행 → BigQuery")


def sync_full(tables=None):
    """전체 동기화 — WRITE_TRUNCATE"""
    client = get_bq_client()
    ensure_dataset(client)

    all_tables = tables or get_sqlite_tables()
    conn = sqlite3.connect(DB_PATH)

    for tname in all_tables:
        try:
            logger.info(f"[{tname}] 로드 시작...")
            if tname == "price_history":
                # 최초 전체 업로드: 5년치 (2021~)
                cutoff = (datetime.now() - timedelta(days=365*5+1)).strftime("%Y-%m-%d")
                df = pd.read_sql_query(
                    f"SELECT * FROM price_history WHERE date >= ? ORDER BY date",
                    conn, params=(cutoff,)
                )
            else:
                df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)

            df = sanitize_df(df)
            upload_table(client, tname, df, write_mode="WRITE_TRUNCATE")

        except Exception as e:
            logger.error(f"  [{tname}] ❌ 오류: {e}")

    conn.close()
    logger.info("=== 전체 동기화 완료 ===")


def sync_daily(days_back: int = 7):
    """일별 증분 동기화"""
    client = get_bq_client()
    ensure_dataset(client)

    cutoff = (datetime.now() - timedelta(days=max(days_back, 7))).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)

    # ── price_history: 최근 N일 증분 ─────────────────────────────────
    logger.info(f"[price_history] 증분 로드 ({cutoff} 이후)...")
    try:
        df = pd.read_sql_query(
            "SELECT * FROM price_history WHERE date >= ? AND stock_code NOT LIKE '%^%' "
            "AND stock_code NOT LIKE 'GC%' AND stock_code NOT LIKE 'CL%' "
            "AND stock_code NOT LIKE '%-F' AND stock_code NOT LIKE '%=%' "
            "AND stock_code NOT LIKE 'NQ%' AND stock_code NOT LIKE 'ES%'",
            conn, params=(cutoff,)
        )
        df = sanitize_df(df)

        # 해당 날짜 범위 먼저 삭제 후 재삽입
        from google.cloud import bigquery
        table_ref = f"{PROJECT_ID}.{DATASET_ID}.price_history"
        dml = f"""
            DELETE FROM `{table_ref}`
            WHERE date >= '{cutoff}'
        """
        try:
            client.query(dml).result()
        except Exception:
            pass  # 테이블 없으면 스킵

        upload_table(client, "price_history", df, write_mode="WRITE_APPEND")
    except Exception as e:
        logger.error(f"  [price_history] ❌ {e}")

    # ── 운영 테이블 FULL REFRESH ──────────────────────────────────────
    # price_history/us_price_history는 위에서 증분 처리하고, 백업/내부/캐시를 제외한
    # 나머지 운영 테이블은 매일 1회 BigQuery로 재적재한다. 새 테이블이 추가되어도
    # 별도 목록 관리 없이 텐버거/전략 분석에서 사용할 수 있게 하기 위함이다.
    full_refresh_daily = [
        t for t in get_sqlite_tables()
        if t not in INCREMENTAL_TABLES and t != "dart_disclosures"
    ]

    for tname in full_refresh_daily:
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
            df = sanitize_df(df)
            upload_table(client, tname, df, write_mode="WRITE_TRUNCATE")
        except Exception as e:
            logger.error(f"  [{tname}] ❌ {e}")

    # ── dart_disclosures: 최근 N일 증분 ─────────────────────────────────
    logger.info(f"[dart_disclosures] 증분 로드 ({cutoff} 이후)...")
    try:
        df = pd.read_sql_query(
            "SELECT * FROM dart_disclosures WHERE rcept_dt >= ?",
            conn, params=(cutoff,)
        )
        df = sanitize_df(df)
        if not df.empty:
            from google.cloud import bigquery as _bq
            table_ref = f"{PROJECT_ID}.{DATASET_ID}.dart_disclosures"
            try:
                client.query(f"DELETE FROM `{table_ref}` WHERE rcept_dt >= '{cutoff}'").result()
            except Exception:
                pass
            upload_table(client, "dart_disclosures", df, write_mode="WRITE_APPEND")
    except Exception as e:
        logger.error(f"  [dart_disclosures] ❌ {e}")

    conn.close()
    logger.info("=== 일별 동기화 완료 ===")


def sync_daily_lite(days_back: int = 7, rebuild_trigger_lab: bool = True):
    """저비용 일별 동기화.

    목적:
    - price_history는 최근 N일만 증분 적재
    - Trigger Discovery/전략 실험 핵심 테이블만 full refresh
    - 기존 daily처럼 모든 운영 테이블을 훑지 않아 BigQuery 저장/조회 대상을 작게 유지
    """
    if rebuild_trigger_lab:
        import subprocess
        logger.info("[trigger_discovery_lab] 로컬 Lab 테이블 재생성...")
        proc = subprocess.run(
            [sys.executable, "/Volumes/Realtek_NVME/stock_dashboard/runtime/scripts/build_trigger_discovery_lab.py", "--start", "2020-01-01"],
            capture_output=True,
            text=True,
            timeout=900,
            cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime",
        )
        if proc.returncode != 0:
            logger.error("[trigger_discovery_lab] ❌ %s", (proc.stderr or "")[-2000:])
        else:
            logger.info("[trigger_discovery_lab] ✅ %s", (proc.stdout or "").strip()[-1000:])

    client = get_bq_client()
    ensure_dataset(client)
    cutoff = (datetime.now() - timedelta(days=max(days_back, 7))).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)

    logger.info(f"[price_history] lite 증분 로드 ({cutoff} 이후)...")
    try:
        df = pd.read_sql_query(
            "SELECT * FROM price_history WHERE date >= ? AND stock_code NOT LIKE '%^%' "
            "AND stock_code NOT LIKE 'GC%' AND stock_code NOT LIKE 'CL%' "
            "AND stock_code NOT LIKE '%-F' AND stock_code NOT LIKE '%=%' "
            "AND stock_code NOT LIKE 'NQ%' AND stock_code NOT LIKE 'ES%'",
            conn,
            params=(cutoff,),
        )
        df = sanitize_df(df)
        table_ref = f"{PROJECT_ID}.{DATASET_ID}.price_history"
        try:
            client.query(
                f"DELETE FROM `{table_ref}` WHERE date >= '{cutoff}'",
                job_config=_query_job_config(),
            ).result()
        except Exception:
            pass
        upload_table(client, "price_history", df, write_mode="WRITE_APPEND")
    except Exception as e:
        logger.error("  [price_history] ❌ %s", e)

    existing = set(get_sqlite_tables())
    for tname in LOW_COST_DAILY_TABLES:
        if tname not in existing:
            logger.warning("[daily-lite] 테이블 없음 — 스킵: %s", tname)
            continue
        try:
            logger.info("[daily-lite:%s] 로드 시작...", tname)
            df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
            df = sanitize_df(df)
            upload_table(client, tname, df, write_mode="WRITE_TRUNCATE")
        except Exception as e:
            logger.error("  [daily-lite:%s] ❌ %s", tname, e)
    conn.close()
    logger.info("=== 저비용 일별 동기화 완료 ===")


def _query_job_config():
    """BigQuery query cost guard."""
    from google.cloud import bigquery
    return bigquery.QueryJobConfig(maximum_bytes_billed=LOW_COST_MAX_BYTES_BILLED)


def sync_external_sources(source: str | None = None, tables: list[str] | None = None):
    """stock.db 밖의 보조 SQLite DB를 BigQuery에 업로드."""
    client = get_bq_client()
    ensure_dataset(client)

    selected_sources = (
        {source: EXTERNAL_DB_SOURCES[source]}
        if source
        else EXTERNAL_DB_SOURCES
    )

    for source_name, cfg in selected_sources.items():
        db_path = cfg["path"]
        if not os.path.exists(db_path):
            logger.error("[%s] DB 없음: %s", source_name, db_path)
            continue

        requested = tables or cfg["tables"]
        conn = sqlite3.connect(db_path)
        try:
            existing = {
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for src_table in requested:
                if src_table not in existing:
                    logger.warning("[%s.%s] 테이블 없음 — 스킵", source_name, src_table)
                    continue
                bq_table = _external_table_name(cfg, src_table)
                try:
                    logger.info("[%s.%s → %s] 로드 시작...", source_name, src_table, bq_table)
                    df = pd.read_sql_query(f'SELECT * FROM "{src_table}"', conn)
                    df = sanitize_df(df)
                    upload_table(client, bq_table, df, write_mode="WRITE_TRUNCATE")
                except Exception as e:
                    logger.error("[%s.%s] ❌ 오류: %s", source_name, src_table, e)
        finally:
            conn.close()


def create_analysis_views(client):
    """텐버거 분석용 BigQuery 뷰 생성"""
    from google.cloud import bigquery

    views = {
        # 1. 텐버거 발굴 종목 재무 프로파일
        "v_tenbagger_profile": f"""
            SELECT
              t.run_time,
              t.run_type,
              t.stock_code,
              t.stock_name,
              t.total_score,
              t.current_price,
              t.market_cap / 1e8 AS market_cap_100m,
              t.per,
              t.pbr,
              t.roe,
              t.revenue_growth * 100 AS revenue_growth_pct,
              t.op_growth * 100    AS op_growth_pct,
              t.op_margin * 100    AS op_margin_pct,
              t.inst_net_10d / 100  AS inst_net_10d_100m,
              t.frn_net_10d / 100   AS frn_net_10d_100m,
              u.sector_large,
              u.market,
              u.shares_issued,
              t.ai_analysis,
              t.created_at
            FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results` t
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON t.stock_code = u.stock_code
        """,

        # 2. 텐버거 종목의 발굴 이후 주가 성과
        "v_tenbagger_price_perf": f"""
            WITH discovery AS (
              SELECT
                stock_code, stock_name,
                MIN(created_at) AS first_discovered,
                MIN(current_price) AS price_at_discovery
              FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results`
              WHERE stock_code IS NOT NULL
              GROUP BY stock_code, stock_name
            ),
            latest_price AS (
              SELECT stock_code,
                MAX(date) AS latest_date,
                MAX_BY(close, date) AS latest_close
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
              GROUP BY stock_code
            )
            SELECT
              d.stock_code,
              d.stock_name,
              DATE(d.first_discovered) AS discovery_date,
              d.price_at_discovery,
              lp.latest_close AS current_price,
              ROUND((lp.latest_close - d.price_at_discovery) / d.price_at_discovery * 100, 2) AS return_pct,
              lp.latest_date,
              DATE_DIFF(lp.latest_date, DATE(d.first_discovered), DAY) AS holding_days
            FROM discovery d
            JOIN latest_price lp ON d.stock_code = lp.stock_code
            WHERE d.price_at_discovery > 0
            ORDER BY return_pct DESC
        """,

        # 3. 발굴 전후 30일 수급 흐름
        "v_tenbagger_investor_flow": f"""
            WITH discovery AS (
              SELECT stock_code, stock_name,
                MIN(DATE(created_at)) AS disc_date
              FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results`
              GROUP BY stock_code, stock_name
            )
            SELECT
              d.stock_code,
              d.stock_name,
              d.disc_date,
              p.date,
              DATE_DIFF(DATE(p.date), d.disc_date, DAY) AS days_from_discovery,
              p.close,
              ROUND(p.inst_net_buy_amt / 100, 0)  AS inst_100m,
              ROUND(p.frn_net_buy_amt / 100, 0)   AS frn_100m,
              ROUND(p.ind_net_buy_amt / 100, 0)   AS ind_100m,
              p.volume
            FROM discovery d
            JOIN `{PROJECT_ID}.{DATASET_ID}.price_history` p
              ON d.stock_code = p.stock_code
             AND DATE(p.date) BETWEEN DATE_SUB(d.disc_date, INTERVAL 30 DAY)
                                  AND DATE_ADD(d.disc_date, INTERVAL 60 DAY)
            WHERE p.close > 0
            ORDER BY d.stock_code, p.date
        """,

        # 4. 섹터별 텐버거 분포
        "v_tenbagger_sector": f"""
            SELECT
              COALESCE(u.sector_large, 'etc') AS sector,
              u.market,
              COUNT(DISTINCT t.stock_code)  AS tenbagger_cnt,
              ROUND(AVG(t.total_score), 2)  AS avg_score,
              ROUND(AVG(t.per), 1)          AS avg_per,
              ROUND(AVG(t.pbr), 2)          AS avg_pbr,
              ROUND(AVG(t.roe), 1)          AS avg_roe,
              ROUND(AVG(t.revenue_growth * 100), 1) AS avg_rev_growth_pct,
              ROUND(AVG(t.op_margin * 100), 1)      AS avg_op_margin_pct
            FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results` t
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON t.stock_code = u.stock_code
            GROUP BY sector, u.market
            ORDER BY tenbagger_cnt DESC
        """,

        # 5. 재무 패턴 — 텐버거 vs 전체 시장 비교
        "v_tenbagger_vs_market": f"""
            WITH tenbagger_codes AS (
              SELECT DISTINCT stock_code
              FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results`
            ),
            fin_latest AS (
              SELECT f.stock_code,
                MAX_BY(f.revenue, f.year * 10 + f.quarter)         AS revenue,
                MAX_BY(f.operating_profit, f.year * 10 + f.quarter) AS op_profit,
                MAX_BY(f.net_income, f.year * 10 + f.quarter)      AS net_income,
                MAX_BY(f.total_equity, f.year * 10 + f.quarter)    AS equity
              FROM `{PROJECT_ID}.{DATASET_ID}.financial_data` f
              WHERE f.is_annual = 0 AND f.quarter > 0
              GROUP BY f.stock_code
            )
            SELECT
              CASE WHEN t.stock_code IS NOT NULL THEN 'tenbagger_candidate' ELSE 'general' END AS grp,
              COUNT(*)                                         AS cnt,
              ROUND(AVG(u.per), 1)                            AS avg_per,
              ROUND(AVG(u.pbr), 2)                            AS avg_pbr,
              ROUND(AVG(u.roe), 1)                            AS avg_roe,
              ROUND(AVG(u.market_cap) / 1e8, 0)              AS avg_mktcap_100m,
              ROUND(AVG(SAFE_DIVIDE(f.op_profit, f.revenue)) * 100, 1) AS avg_op_margin_pct
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            LEFT JOIN tenbagger_codes t ON u.stock_code = t.stock_code
            LEFT JOIN fin_latest f ON u.stock_code = f.stock_code
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
            GROUP BY grp
        """,

        # 6. DART 수주공시 있는 텐버거 종목
        "v_tenbagger_dart": f"""
            SELECT
              t.stock_code,
              t.stock_name,
              t.total_score,
              t.created_at AS tenbagger_date,
              d.disclosed_at,
              d.report_nm,
              d.contract_amount_krw / 1e8 AS contract_100m,
              d.contract_ratio_pct,
              d.is_overseas,
              d.ai_signal,
              d.signal_strength,
              d.counterparty_country
            FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results` t
            JOIN `{PROJECT_ID}.{DATASET_ID}.dart_contracts` d
              ON t.stock_code = d.stock_code
             AND DATE(d.disclosed_at) >= DATE_SUB(DATE(t.created_at), INTERVAL 90 DAY)
            ORDER BY t.created_at DESC, d.signal_strength DESC
        """,

        # 7. 스탁이지 전략 편입 + 텐버거 교집합
        "v_tenbagger_stockeasy": f"""
            SELECT
              t.stock_code,
              t.stock_name,
              t.total_score,
              DATE(t.created_at) AS tenbagger_date,
              s.strategy,
              s.analyzed_at AS stockeasy_date,
              s.analysis_text
            FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results` t
            JOIN `{PROJECT_ID}.{DATASET_ID}.stockeasy_analysis` s
              ON JSON_VALUE(s.holdings_json, CONCAT('$[*].name')) LIKE CONCAT('%', t.stock_name, '%')
               OR JSON_VALUE(s.exits_json, CONCAT('$[*].name')) LIKE CONCAT('%', t.stock_name, '%')
            ORDER BY t.created_at DESC
        """,

        # ── 3배 상승 종목 분석 뷰 ─────────────────────────────────────────────

        # 8. 3배 상승 종목 탐지 (5년 내 24개월 슬라이딩 윈도우)
        #    • 기준: 최저가 대비 최고가가 3배 이상인 구간이 1년 이내 존재
        #    • 결과: stock_code별 첫 번째 3x 달성일, 시작가, 고점가, 소요일수
        "v_3x_stocks": f"""
            WITH monthly_close AS (
              --    (ETF )
              SELECT
                stock_code,
                DATE_TRUNC(date, MONTH) AS ym,
                MAX_BY(close, date)      AS month_close,
                MAX(close)               AS month_high,
                MIN(CASE WHEN close > 0 THEN close END) AS month_low
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND stock_code NOT LIKE 'GC%'
                AND stock_code NOT LIKE 'CL%'
                AND stock_code NOT LIKE '%-F'
                AND stock_code NOT LIKE '%=%'
                AND stock_code NOT LIKE 'NQ%'
                AND stock_code NOT LIKE 'ES%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 5 YEAR)
              GROUP BY stock_code, ym
            ),
            low_window AS (
              -- :   12
              SELECT
                b.stock_code,
                b.ym AS base_ym,
                b.month_low AS base_price,
                h.ym AS peak_ym,
                h.month_high AS peak_price,
                DATE_DIFF(h.ym, b.ym, MONTH) AS months_to_peak,
                SAFE_DIVIDE(h.month_high, b.month_low) AS price_ratio
              FROM monthly_close b
              JOIN monthly_close h
                ON b.stock_code = h.stock_code
               AND h.ym > b.ym
               AND h.ym <= DATE_ADD(b.ym, INTERVAL 12 MONTH)
              WHERE b.month_low IS NOT NULL
                AND b.month_low > 0
                AND h.month_high > 0
            ),
            triple_events AS (
              SELECT
                stock_code,
                base_ym,
                base_price,
                peak_ym,
                peak_price,
                months_to_peak,
                ROUND(price_ratio, 2) AS price_ratio,
                ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY base_ym) AS rn
              FROM low_window
              WHERE price_ratio >= 3.0
            )
            SELECT
              te.stock_code,
              u.stock_name,
              u.sector_large AS sector,
              u.market,
              u.market_cap / 1e8   AS market_cap_100m_present,
              te.base_ym           AS surge_start_month,
              te.peak_ym           AS surge_peak_month,
              te.base_price        AS price_at_start,
              te.peak_price        AS price_at_peak,
              te.price_ratio,
              te.months_to_peak
            FROM triple_events te
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON te.stock_code = u.stock_code
            WHERE te.rn = 1   --    3
            ORDER BY te.base_ym DESC, te.price_ratio DESC
        """,

        # 9. 3배 상승 전 6개월 재무·수급 특징 (서지 직전 재무 스냅샷)
        "v_3x_pre_surge_stats": f"""
            WITH
            mc9 AS (
              SELECT stock_code,
                DATE_TRUNC(date, MONTH) AS ym,
                MAX(close) AS month_high,
                MIN(CASE WHEN close > 0 THEN close END) AS month_low
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 5 YEAR)
              GROUP BY stock_code, ym
            ),
            lw9 AS (
              SELECT b.stock_code,
                b.ym AS base_ym,
                b.month_low AS base_price,
                SAFE_DIVIDE(h.month_high, b.month_low) AS price_ratio,
                ROW_NUMBER() OVER (PARTITION BY b.stock_code ORDER BY b.ym) AS rn
              FROM mc9 b
              JOIN mc9 h ON b.stock_code = h.stock_code
                AND h.ym > b.ym AND h.ym <= DATE_ADD(b.ym, INTERVAL 12 MONTH)
              WHERE b.month_low > 0 AND h.month_high > 0
                AND SAFE_DIVIDE(h.month_high, b.month_low) >= 3.0
            ),
            triple AS (
              SELECT stock_code, base_ym, base_price, price_ratio
              FROM lw9 WHERE rn = 1
            ),
            pre_surge_fin AS (
              --    6
              SELECT
                t.stock_code,
                t.base_ym,
                t.base_price,
                t.price_ratio,
                f.year AS fin_year,
                f.revenue          / 1e8  AS revenue_100m,
                f.operating_profit / 1e8  AS op_profit_100m,
                f.net_income       / 1e8  AS net_income_100m,
                f.total_assets     / 1e8  AS total_assets_100m,
                f.total_equity     / 1e8  AS total_equity_100m,
                f.eps,
                f.bps,
                f.roe,
                f.depreciation_amortization / 1e8 AS da_100m,
                --
                ROUND(SAFE_DIVIDE(f.operating_profit, f.revenue) * 100, 2) AS op_margin_pct,
                --
                ROUND(SAFE_DIVIDE(f.net_income, f.revenue) * 100, 2) AS net_margin_pct,
                --
                ROUND(SAFE_DIVIDE(f.total_liabilities, f.total_equity) * 100, 2) AS debt_ratio_pct,
                ROW_NUMBER() OVER (
                  PARTITION BY t.stock_code
                  ORDER BY f.year DESC
                ) AS fin_rn
              FROM triple t
              JOIN `{PROJECT_ID}.{DATASET_ID}.financial_data` f
                ON t.stock_code = f.stock_code
               AND f.is_annual = 1
               AND f.year >= EXTRACT(YEAR FROM t.base_ym) - 1
               AND f.year <= EXTRACT(YEAR FROM t.base_ym)
            ),
            pre_surge_supply AS (
              --   3
              SELECT
                t.stock_code,
                ROUND(AVG(p.inst_net_buy_amt) / 100, 0)  AS avg_inst_3m_100m,
                ROUND(AVG(p.frn_net_buy_amt) / 100, 0)   AS avg_frn_3m_100m,
                ROUND(AVG(p.volume), 0)                   AS avg_vol_3m
              FROM triple t
              JOIN `{PROJECT_ID}.{DATASET_ID}.price_history` p
                ON t.stock_code = p.stock_code
               AND DATE(p.date) BETWEEN DATE_SUB(t.base_ym, INTERVAL 90 DAY)
                                    AND t.base_ym
              WHERE p.close > 0
              GROUP BY t.stock_code
            )
            SELECT
              pf.stock_code,
              u.stock_name,
              u.sector_large AS sector,
              u.market,
              pf.base_ym     AS surge_start_month,
              pf.price_ratio AS actual_peak_ratio,
              pf.fin_year,
              pf.revenue_100m,
              pf.op_profit_100m,
              pf.net_income_100m,
              pf.total_assets_100m,
              pf.total_equity_100m,
              pf.op_margin_pct,
              pf.net_margin_pct,
              pf.debt_ratio_pct,
              pf.roe,
              pf.eps,
              pf.bps,
              pf.da_100m,
              u.per          AS per_at_present,
              u.pbr          AS pbr_at_present,
              ps.avg_inst_3m_100m,
              ps.avg_frn_3m_100m,
              ps.avg_vol_3m,
              u.shares_issued / 1e4 AS shares_10k_shares
            FROM pre_surge_fin pf
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON pf.stock_code = u.stock_code
            LEFT JOIN pre_surge_supply ps
              ON pf.stock_code = ps.stock_code
            WHERE pf.fin_rn = 1
            ORDER BY pf.base_ym DESC
        """,

        # 10. 3배 상승 종목 공통 패턴 프로파일 (섹터·규모·재무 통계)
        "v_3x_pattern_profile": f"""
            WITH
            mc10 AS (
              SELECT stock_code,
                DATE_TRUNC(date, MONTH) AS ym,
                MAX(close) AS month_high,
                MIN(CASE WHEN close > 0 THEN close END) AS month_low
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0 AND stock_code NOT LIKE '%^%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 5 YEAR)
              GROUP BY stock_code, ym
            ),
            lw10 AS (
              SELECT b.stock_code,
                b.ym AS base_ym,
                SAFE_DIVIDE(h.month_high, b.month_low) AS price_ratio,
                DATE_DIFF(h.ym, b.ym, MONTH) AS months_to_peak,
                ROW_NUMBER() OVER (PARTITION BY b.stock_code ORDER BY b.ym) AS rn
              FROM mc10 b
              JOIN mc10 h ON b.stock_code = h.stock_code
                AND h.ym > b.ym AND h.ym <= DATE_ADD(b.ym, INTERVAL 12 MONTH)
              WHERE b.month_low > 0 AND SAFE_DIVIDE(h.month_high, b.month_low) >= 3.0
            ),
            triple AS (
              SELECT stock_code, base_ym, price_ratio, months_to_peak
              FROM lw10 WHERE rn = 1
            )
            SELECT
              COALESCE(u.sector_large, 'etc') AS sector,
              u.market,

              --
              COUNT(DISTINCT t.stock_code) AS cnt_stocks,
              ROUND(AVG(t.price_ratio), 2)  AS avg_price_ratio,
              ROUND(MIN(t.price_ratio), 2)  AS min_price_ratio,
              ROUND(MAX(t.price_ratio), 2)  AS max_price_ratio,
              ROUND(AVG(t.months_to_peak), 1) AS avg_months_to_peak,

              --   (  :  )
              ROUND(AVG(u.market_cap) / 1e8, 0) AS avg_mktcap_100m,
              ROUND(MIN(u.market_cap) / 1e8, 0) AS min_mktcap_100m,
              ROUND(MAX(u.market_cap) / 1e8, 0) AS max_mktcap_100m,

              --
              ROUND(AVG(u.per), 1)  AS avg_per,
              ROUND(AVG(u.pbr), 2)  AS avg_pbr,
              ROUND(AVG(u.roe), 1)  AS avg_roe,

              --
              COUNTIF(u.market_cap < 10000000000)               AS cnt_under_100100m,
              COUNTIF(u.market_cap BETWEEN 10000000000 AND 50000000000)  AS cnt_100_500100m,
              COUNTIF(u.market_cap BETWEEN 50000000000 AND 100000000000) AS cnt_500_1000100m,
              COUNTIF(u.market_cap BETWEEN 100000000000 AND 500000000000) AS cnt_1000_5000100m,
              COUNTIF(u.market_cap > 500000000000)              AS cnt_over_5000100m

            FROM triple t
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON t.stock_code = u.stock_code
            GROUP BY sector, u.market
            ORDER BY cnt_stocks DESC
        """,

        # 11. 현재 종목 중 3배 상승 전 패턴 유사 종목 (스크리닝)
        #     → 과거 3x 종목의 "서지 전 평균" 지표를 기준으로 현재 종목 필터링
        "v_3x_candidate_screen": f"""
            WITH fin_latest AS (
              --
              SELECT
                f.stock_code,
                MAX_BY(f.operating_profit, f.year) AS op_profit,
                MAX_BY(f.revenue, f.year)           AS revenue,
                MAX_BY(f.net_income, f.year)        AS net_income,
                MAX_BY(f.total_equity, f.year)      AS total_equity,
                MAX_BY(f.total_liabilities, f.year) AS total_liabilities,
                MAX_BY(f.roe, f.year)               AS roe,
                MAX_BY(f.eps, f.year)               AS eps
              FROM `{PROJECT_ID}.{DATASET_ID}.financial_data` f
              WHERE f.is_annual = 1 AND f.year >= EXTRACT(YEAR FROM CURRENT_DATE()) - 2
              GROUP BY f.stock_code
            ),
            supply_recent AS (
              --  60
              SELECT
                stock_code,
                ROUND(AVG(inst_net_buy_amt) / 100, 0) AS avg_inst_60d_100m,
                ROUND(AVG(frn_net_buy_amt) / 100, 0)  AS avg_frn_60d_100m,
                ROUND(AVG(volume), 0)                  AS avg_vol_60d,
                ROUND(MAX(close), 0)                   AS recent_close
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
              GROUP BY stock_code
            ),
            ma_calc AS (
              -- 52     (   )
              SELECT
                stock_code,
                MIN(CASE WHEN close > 0 THEN close END) AS low_52w,
                MAX(close)                               AS high_52w,
                MAX_BY(close, date)                      AS current_close
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large AS sector,
              u.market,
              u.market_cap / 100 AS mktcap_100m,
              u.per,
              u.pbr,
              u.roe,
              f.op_profit  / 1e8 AS op_profit_100m,
              f.revenue    / 1e8 AS revenue_100m,
              f.net_income / 1e8 AS net_income_100m,
              ROUND(SAFE_DIVIDE(f.op_profit, f.revenue) * 100, 2) AS op_margin_pct,
              ROUND(SAFE_DIVIDE(f.net_income, f.revenue) * 100, 2) AS net_margin_pct,
              ROUND(SAFE_DIVIDE(f.total_liabilities, f.total_equity) * 100, 2) AS debt_ratio_pct,
              s.avg_inst_60d_100m,
              s.avg_frn_60d_100m,
              s.avg_vol_60d,
              ma.low_52w,
              ma.high_52w,
              ma.current_close,
              ROUND(SAFE_DIVIDE(ma.current_close - ma.low_52w, ma.low_52w) * 100, 1) AS pct_above_52w_low,
              ROUND(SAFE_DIVIDE(ma.high_52w - ma.current_close, ma.high_52w) * 100, 1) AS pct_below_52w_high,

              -- 3x   (0~100):
              LEAST(100, GREATEST(0,
                --  300~3000 ( ) +20
                CASE WHEN u.market_cap BETWEEN 300 AND 3000 THEN 20 ELSE 0 END
                -- ROE 10%  +15
                + CASE WHEN u.roe >= 10 THEN 15 WHEN u.roe >= 5 THEN 8 ELSE 0 END
                --  10%  +15
                + CASE WHEN SAFE_DIVIDE(f.op_profit, f.revenue) >= 0.10 THEN 15
                        WHEN SAFE_DIVIDE(f.op_profit, f.revenue) >= 0.05 THEN 8 ELSE 0 END
                --  100%  +10
                + CASE WHEN SAFE_DIVIDE(f.total_liabilities, f.total_equity) <= 1.0 THEN 10 ELSE 0 END
                --    +15
                + CASE WHEN s.avg_inst_60d_100m > 5 THEN 15
                        WHEN s.avg_inst_60d_100m > 0 THEN 8 ELSE 0 END
                --   +10
                + CASE WHEN s.avg_frn_60d_100m > 5 THEN 10
                        WHEN s.avg_frn_60d_100m > 0 THEN 5 ELSE 0 END
                -- 52   20~80%   ( ) +15
                + CASE WHEN SAFE_DIVIDE(ma.current_close - ma.low_52w, ma.low_52w) BETWEEN 0.15 AND 0.80 THEN 15
                        WHEN SAFE_DIVIDE(ma.current_close - ma.low_52w, ma.low_52w) < 0.15 THEN 8 ELSE 0 END
                -- PBR 3  +0 (PBR  )
                + CASE WHEN u.pbr <= 1 THEN 0 WHEN u.pbr <= 2 THEN 0 WHEN u.pbr <= 3 THEN 0
                        WHEN u.pbr > 5 THEN -5 ELSE 0 END
              )) AS triple_pattern_score

            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN fin_latest f   ON u.stock_code = f.stock_code
            JOIN supply_recent s ON u.stock_code = s.stock_code
            JOIN ma_calc ma     ON u.stock_code = ma.stock_code

            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND u.market_cap IS NOT NULL
              AND u.market_cap > 50       -- 50억원 이상
              AND f.revenue > 0
              AND f.op_profit > 0           --
              AND ma.current_close > 0

            ORDER BY triple_pattern_score DESC, u.market_cap ASC
        """,
    }

    views.update({
        # 12. Week2 수급 추세 — [BUG FIX 2026-07-07]
        #     kiwoom_investor_daily는 매수(buy-only) 데이터라 순매수로 사용 불가했음
        #     (2026-07-21 trde_tp='0' 수정으로 근본 해결, 재수집 완료 — 이 뷰는
        #     price_history가 이미 KIS 5분 수집 기반 신뢰 소스라 유지).
        #     price_history.frn_net_buy_amt / inst_net_buy_amt (백만원, 순매수)로 교체.
        "v_supply_trend_week2": f"""
            WITH supply AS (
              SELECT
                stock_code,
                date AS dt_date,
                frn_net_buy_amt / 100.0 AS frg_net,
                inst_net_buy_amt / 100.0 AS inst_net
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND stock_code NOT LIKE '%=%'
                AND stock_code NOT LIKE '%-F'
                AND date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 30 DAY)
            ),
            agg AS (
              SELECT
                stock_code,
                ROUND(SUM(frg_net), 1) AS frg_net_30d,
                ROUND(SUM(inst_net), 1) AS inst_net_30d,
                ROUND(SUM(CASE WHEN dt_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 10 DAY)
                         THEN frg_net ELSE 0 END), 1) AS frg_net_10d,
                ROUND(SUM(CASE WHEN dt_date >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 10 DAY)
                         THEN inst_net ELSE 0 END), 1) AS inst_net_10d,
                COUNT(DISTINCT dt_date) AS data_days
              FROM supply
              GROUP BY stock_code
            )
            SELECT
              a.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap,
              a.frg_net_10d,
              a.inst_net_10d,
              a.frg_net_30d,
              a.inst_net_30d,
              ROUND(a.frg_net_10d + a.inst_net_10d, 1) AS combined_net_10d,
              CASE
                WHEN a.frg_net_10d > 0 AND a.inst_net_10d > 0 THEN 'BOTH_BUY'
                WHEN a.frg_net_10d > 0 OR  a.inst_net_10d > 0 THEN 'ONE_BUY'
                WHEN a.frg_net_10d < 0 AND a.inst_net_10d < 0 THEN 'BOTH_SELL'
                ELSE 'MIXED'
              END AS supply_label
            FROM agg a
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u USING (stock_code)
            WHERE a.data_days >= 5
            ORDER BY combined_net_10d DESC
        """,

        # 13. 텐버거 + 3배 패턴 + 수급 복합 신호
        "v_tenbagger_composite_week2": f"""
            WITH base AS (
              SELECT
                t.stock_code,
                t.stock_name,
                t.total_score,
                t.score_detail,
                t.reasons,
                t.run_time,
                ROW_NUMBER() OVER (
                  PARTITION BY t.stock_code
                  ORDER BY CAST(t.run_time AS STRING) DESC, t.created_at DESC
                ) AS rn
              FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results` t
            ),
            triple AS (
              SELECT
                stock_code,
                MAX(triple_pattern_score) AS triple_score
              FROM `{PROJECT_ID}.{DATASET_ID}.triple_pattern_daily`
              GROUP BY stock_code
            ),
            supply AS (
              SELECT stock_code, combined_net_10d, supply_label
              FROM `{PROJECT_ID}.{DATASET_ID}.v_supply_trend_week2`
            ),
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS latest_close,
                ARRAY_AGG(date ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS price_date
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
              GROUP BY stock_code
            )
            SELECT
              b.stock_code,
              b.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap,
              b.total_score AS tenbagger_score,
              COALESCE(tr.triple_score, 0) AS triple_score,
              COALESCE(s.combined_net_10d, 0) AS supply_net_10d,
              COALESCE(s.supply_label, 'NO_DATA') AS supply_label,
              ROUND(
                b.total_score
                + COALESCE(tr.triple_score, 0) * 0.3
                + CASE
                    WHEN s.supply_label = 'BOTH_BUY' THEN 5
                    WHEN s.supply_label = 'ONE_BUY' THEN 2
                    ELSE 0
                  END,
                1
              ) AS composite_score,
              px.latest_close,
              b.reasons,
              b.run_time
            FROM base b
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u USING (stock_code)
            LEFT JOIN triple tr USING (stock_code)
            LEFT JOIN supply s USING (stock_code)
            LEFT JOIN px USING (stock_code)
            WHERE b.rn = 1
            ORDER BY composite_score DESC
        """,

        # 14. 텐버거 복합 신호 섹터 집계
        "v_sector_tenbagger_week2": f"""
            SELECT
              COALESCE(u.sector_large, 'etc') AS sector_large,
              COUNT(DISTINCT t.stock_code) AS stock_count,
              ROUND(AVG(t.composite_score), 1) AS avg_score,
              MAX(t.composite_score) AS max_score,
              COUNTIF(t.supply_label = 'BOTH_BUY') AS both_buy_count,
              COUNTIF(t.composite_score >= 60) AS high_score_count,
              STRING_AGG(
                CASE WHEN t.composite_score >= 60 THEN t.stock_name END,
                ', ' ORDER BY t.composite_score DESC LIMIT 3
              ) AS top_stocks
            FROM `{PROJECT_ID}.{DATASET_ID}.v_tenbagger_composite_week2` t
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u USING (stock_code)
            GROUP BY sector_large
            ORDER BY avg_score DESC
        """,

        # 15. 임원매수 + 자사주취득/소각 + R&D/기술이전 카탈리스트 신호
        "v_tenbagger_insider_catalyst": f"""
            WITH
            insider_buy AS (
              SELECT
                stock_code,
                COUNT(*) AS insider_buy_cnt,
                MAX(IF(is_ceo = 1, 1, 0)) AS has_ceo_buy,
                SUM(SAFE_CAST(sp_stock_lmp_irds_cnt AS FLOAT64)) AS total_shares_acquired
              FROM `{PROJECT_ID}.{DATASET_ID}.dart_insider_holdings`
              WHERE SAFE_CAST(rcept_dt AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
                AND SAFE_CAST(sp_stock_lmp_irds_cnt AS FLOAT64) > 0
              GROUP BY stock_code
            ),
            buyback AS (
              SELECT
                stock_code,
                COUNTIF(event_type LIKE '%취득%') AS acquire_cnt,
                COUNTIF(event_type LIKE '%소각%') AS cancel_cnt
              FROM `{PROJECT_ID}.{DATASET_ID}.treasury_buyback`
              WHERE COALESCE(
                      SAFE.PARSE_DATE('%Y.%m.%d', rcept_dt),
                      SAFE.PARSE_DATE('%Y-%m-%d', rcept_dt)
                    ) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            ),
            rd AS (
              SELECT
                stock_code,
                COUNTIF(signal_type = 'tech_transfer') AS tech_cnt,
                COUNTIF(signal_type = 'license') AS license_cnt,
                COUNTIF(signal_type = 'patent') AS patent_cnt,
                COUNTIF(signal_type = 'rd_contract') AS rd_cnt
              FROM `{PROJECT_ID}.{DATASET_ID}.dart_rd_patent_signals`
              WHERE SAFE_CAST(rcept_dt AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            ),
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close,
                MIN(CASE WHEN close > 0 THEN close END) AS low_52w,
                MAX(close) AS high_52w
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
                AND stock_code NOT LIKE '%^%'
              GROUP BY stock_code
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr,
              u.per,
              u.roe,
              COALESCE(ib.insider_buy_cnt, 0) AS insider_buy_cnt,
              COALESCE(ib.has_ceo_buy, 0) AS has_ceo_buy,
              COALESCE(bb.acquire_cnt, 0) AS buyback_acquire_cnt,
              COALESCE(bb.cancel_cnt, 0) AS buyback_cancel_cnt,
              COALESCE(rd.tech_cnt, 0) AS tech_transfer_cnt,
              COALESCE(rd.license_cnt, 0) AS license_cnt,
              COALESCE(rd.patent_cnt, 0) AS patent_cnt,
              COALESCE(rd.rd_cnt, 0) AS rd_contract_cnt,
              px.cur_close,
              ROUND(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) * 100, 1) AS pct_above_52w_low,
              ROUND(SAFE_DIVIDE(px.high_52w - px.cur_close, px.high_52w) * 100, 1) AS pct_below_52w_high,
              LEAST(100, GREATEST(0,
                COALESCE(ib.insider_buy_cnt, 0) * 5
                + IF(COALESCE(ib.has_ceo_buy, 0) = 1, 12, 0)
                + COALESCE(bb.acquire_cnt, 0) * 3
                + COALESCE(bb.cancel_cnt, 0) * 6
                + COALESCE(rd.tech_cnt, 0) * 10
                + COALESCE(rd.license_cnt, 0) * 7
                + COALESCE(rd.patent_cnt, 0) * 3
                + COALESCE(rd.rd_cnt, 0) * 4
              )) AS catalyst_score,
              CONCAT(
                IF(COALESCE(ib.has_ceo_buy, 0)=1, 'CEO매수 ', ''),
                IF(COALESCE(ib.insider_buy_cnt, 0)>0, CONCAT('임원매수', CAST(COALESCE(ib.insider_buy_cnt,0) AS STRING), '건 '), ''),
                IF(COALESCE(bb.cancel_cnt, 0)>0, '자사주소각 ', ''),
                IF(COALESCE(bb.acquire_cnt, 0)>0, '자사주취득 ', ''),
                IF(COALESCE(rd.tech_cnt, 0)>0, '기술이전 ', ''),
                IF(COALESCE(rd.license_cnt, 0)>0, '라이선스 ', ''),
                IF(COALESCE(rd.patent_cnt, 0)>0, '특허 ', '')
              ) AS signal_summary
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN px USING (stock_code)
            LEFT JOIN insider_buy ib USING (stock_code)
            LEFT JOIN buyback bb USING (stock_code)
            LEFT JOIN rd USING (stock_code)
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND (
                COALESCE(ib.insider_buy_cnt, 0) > 0
                OR COALESCE(bb.acquire_cnt, 0) > 0
                OR COALESCE(bb.cancel_cnt, 0) > 0
                OR COALESCE(rd.tech_cnt, 0) > 0
                OR COALESCE(rd.patent_cnt, 0) > 0
              )
            ORDER BY catalyst_score DESC
        """,

        # 16. 신용잔고 급감 + 낙폭과대 반등 복합 신호
        "v_tenbagger_credit_oversold": f"""
            WITH
            credit AS (
              SELECT
                stock_code,
                AVG(CASE
                  WHEN DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 DAY)
                  THEN SAFE_CAST(credit_ratio AS FLOAT64) END) AS ratio_10d,
                AVG(CASE
                  WHEN DATE(dt) BETWEEN
                       DATE_SUB(CURRENT_DATE(), INTERVAL 45 DAY) AND DATE_SUB(CURRENT_DATE(), INTERVAL 20 DAY)
                  THEN SAFE_CAST(credit_ratio AS FLOAT64) END) AS ratio_45d_ago,
                AVG(CASE
                  WHEN DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 DAY)
                  THEN SAFE_CAST(credit_balance_qty AS FLOAT64) END) AS qty_10d,
                AVG(CASE
                  WHEN DATE(dt) BETWEEN
                       DATE_SUB(CURRENT_DATE(), INTERVAL 45 DAY) AND DATE_SUB(CURRENT_DATE(), INTERVAL 20 DAY)
                  THEN SAFE_CAST(credit_balance_qty AS FLOAT64) END) AS qty_45d_ago,
                COUNT(DISTINCT dt) AS data_days
              FROM `{PROJECT_ID}.{DATASET_ID}.kiwoom_credit_balance`
              WHERE credit_balance_qty > 0
                AND DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
              GROUP BY stock_code
              HAVING COUNT(DISTINCT dt) >= 5
            ),
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close,
                MIN(CASE WHEN close > 0 THEN close END) AS low_52w,
                MAX(close) AS high_52w
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
                AND stock_code NOT LIKE '%^%'
              GROUP BY stock_code
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr, u.per,
              ROUND(cr.ratio_10d, 2) AS credit_ratio_now_pct,
              ROUND(cr.ratio_45d_ago, 2) AS credit_ratio_45d_ago_pct,
              ROUND(cr.ratio_10d - cr.ratio_45d_ago, 2) AS credit_ratio_change,
              ROUND(SAFE_DIVIDE(cr.qty_10d - cr.qty_45d_ago, cr.qty_45d_ago) * 100, 1) AS credit_qty_chg_pct,
              px.cur_close,
              ROUND(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) * 100, 1) AS pct_above_52w_low,
              ROUND(SAFE_DIVIDE(px.high_52w - px.cur_close, px.high_52w) * 100, 1) AS pct_below_52w_high,
              -- 낙폭과대 판정 (52주 저점 10~70% 위)
              CASE
                WHEN SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.10 AND 0.70 THEN TRUE
                ELSE FALSE
              END AS is_oversold_bounce,
              -- 신용잔고 감소폭
              CASE
                WHEN cr.ratio_10d - cr.ratio_45d_ago < -0.5 THEN 'SHARP_DECLINE'
                WHEN cr.ratio_10d - cr.ratio_45d_ago < -0.2 THEN 'MODERATE_DECLINE'
                WHEN cr.ratio_10d - cr.ratio_45d_ago < 0 THEN 'SLIGHT_DECLINE'
                ELSE 'STABLE_OR_RISING'
              END AS credit_trend,
              GREATEST(0, LEAST(100,
                -- 신용 감소 신호
                CASE WHEN cr.ratio_10d - cr.ratio_45d_ago < -0.5 THEN 30
                     WHEN cr.ratio_10d - cr.ratio_45d_ago < -0.2 THEN 20
                     WHEN cr.ratio_10d - cr.ratio_45d_ago < 0 THEN 10
                     ELSE 0 END
                -- 낙폭과대 신호 (3배주의 70.5% 출발점)
                + CASE
                    WHEN SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.10 AND 0.70 THEN 40
                    WHEN SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) < 0.10 THEN 20
                    ELSE 5
                  END
                -- 저평가 보너스
                + IF(u.pbr > 0 AND u.pbr < 1.0, 15, 0)
                + IF(u.roe > 5, 10, 0)
              )) AS oversold_score
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN credit cr USING (stock_code)
            JOIN px USING (stock_code)
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND cr.ratio_10d IS NOT NULL
              AND cr.ratio_10d - cr.ratio_45d_ago < 0
            ORDER BY oversold_score DESC
        """,

        # 17. 외국인 지분율 증가 추세 신호
        "v_tenbagger_foreign_trend": f"""
            WITH
            frg AS (
              SELECT
                stock_code,
                ARRAY_AGG(SAFE_CAST(weight AS FLOAT64) ORDER BY dt DESC LIMIT 1)[OFFSET(0)] AS weight_latest,
                AVG(CASE
                  WHEN DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
                  THEN SAFE_CAST(weight AS FLOAT64) END) AS weight_30d,
                AVG(CASE
                  WHEN DATE(dt) BETWEEN
                       DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
                  THEN SAFE_CAST(weight AS FLOAT64) END) AS weight_90d_ago,
                SUM(SAFE_CAST(change_qty AS FLOAT64)) AS total_qty_change_90d,
                COUNT(DISTINCT dt) AS data_days
              FROM `{PROJECT_ID}.{DATASET_ID}.kiwoom_foreign_flow`
              WHERE SAFE_CAST(poss_stock_cnt AS FLOAT64) > 0
                AND DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
              GROUP BY stock_code
              HAVING COUNT(DISTINCT dt) >= 10
            ),
            sup AS (
              SELECT stock_code, sum_frn_60d_100m AS frg_net_60d_100m
              FROM (
                SELECT stock_code,
                       ROUND(SUM(frn_net_buy_amt) / 100.0, 0) AS sum_frn_60d_100m
                FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
                WHERE close > 0 AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
                  AND stock_code NOT LIKE '%^%'
                GROUP BY stock_code
              )
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr, u.per, u.roe,
              ROUND(frg.weight_latest, 2) AS foreign_ownership_pct,
              ROUND(frg.weight_30d, 2) AS weight_30d_avg,
              ROUND(frg.weight_90d_ago, 2) AS weight_90d_ago,
              ROUND(frg.weight_30d - frg.weight_90d_ago, 2) AS weight_change_2m,
              ROUND(COALESCE(sup.frg_net_60d_100m, 0), 0) AS frg_net_60d_100m,
              CASE
                WHEN frg.weight_30d - frg.weight_90d_ago >= 3.0 THEN 'STRONG_INCREASE'
                WHEN frg.weight_30d - frg.weight_90d_ago >= 1.0 THEN 'MODERATE_INCREASE'
                WHEN frg.weight_30d - frg.weight_90d_ago >= 0.3 THEN 'SLIGHT_INCREASE'
                WHEN frg.weight_30d - frg.weight_90d_ago < -0.5 THEN 'DECREASING'
                ELSE 'STABLE'
              END AS foreign_trend_label,
              GREATEST(0, LEAST(100,
                CASE
                  WHEN frg.weight_30d - frg.weight_90d_ago >= 3.0 THEN 50
                  WHEN frg.weight_30d - frg.weight_90d_ago >= 1.0 THEN 35
                  WHEN frg.weight_30d - frg.weight_90d_ago >= 0.3 THEN 20
                  ELSE 0
                END
                + IF(COALESCE(sup.frg_net_60d_100m, 0) > 50, 30, IF(COALESCE(sup.frg_net_60d_100m, 0) > 10, 15, 0))
                + IF(u.pbr > 0 AND u.pbr < 1.5, 10, 0)
                + IF(u.roe > 8, 10, 0)
              )) AS foreign_signal_score
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN frg USING (stock_code)
            LEFT JOIN sup USING (stock_code)
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND frg.weight_30d > frg.weight_90d_ago
            ORDER BY weight_change_2m DESC
        """,

        # 18. 흑자전환/이익급증/이익가속 실적 신호
        "v_tenbagger_earnings_pivot": f"""
            WITH
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close,
                ARRAY_AGG(date ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS latest_date
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0 AND stock_code NOT LIKE '%^%'
              GROUP BY stock_code
            )
            SELECT
              es.stock_code,
              es.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr, u.per, u.roe,
              es.signal_type,
              es.year,
              es.quarter,
              ROUND(es.ttm_op_cur / 1e8, 1) AS ttm_op_100m,
              ROUND(es.ttm_rev_cur / 1e8, 1) AS ttm_rev_100m,
              ROUND(es.ttm_rev_yoy_pct, 1) AS rev_yoy_pct,
              ROUND(es.ttm_op_accel_pct, 1) AS op_accel_pct,
              es.price_at_signal,
              px.cur_close,
              ROUND(SAFE_DIVIDE(px.cur_close - es.price_at_signal, es.price_at_signal) * 100, 1) AS return_since_signal,
              DATE(es.detected_at) AS signal_date,
              -- 신호 가중치 (흑자전환 > 이익폭발 > 이익가속)
              CASE es.signal_type
                WHEN 'TTM_OP_INFLECT' THEN 4
                WHEN 'TTM_BOTH' THEN 4
                WHEN 'TTM_REV_30' THEN 3
                WHEN 'TTM_OP_ACCEL' THEN 2
                WHEN 'QOQ_REV_20_2CON' THEN 2
                ELSE 1
              END AS signal_weight
            FROM `{PROJECT_ID}.{DATASET_ID}.earnings_signals` es
            JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u USING (stock_code)
            LEFT JOIN px USING (stock_code)
            WHERE es.is_active = 1
            ORDER BY signal_weight DESC, es.detected_at DESC
        """,

        # 19. 수주잔고 급증 + 트리거 신호 (건설/조선/방산 중심)
        "v_tenbagger_backlog_catalyst": f"""
            WITH
            bl AS (
              SELECT
                b1.stock_code,
                b1.year AS latest_year,
                b1.quarter AS latest_quarter,
                ROUND(b1.backlog_normalized / 100.0, 1) AS backlog_latest_100m,
                ROUND(b2.backlog_normalized / 100.0, 1) AS backlog_1y_ago_100m,
                ROUND(SAFE_DIVIDE(b1.backlog_normalized - b2.backlog_normalized,
                                  b2.backlog_normalized) * 100, 1) AS backlog_yoy_pct,
                b1.backlog_to_rev
              FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                FROM `{PROJECT_ID}.{DATASET_ID}.order_backlog` WHERE backlog_normalized > 0
              ) b1
              LEFT JOIN (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                FROM `{PROJECT_ID}.{DATASET_ID}.order_backlog` WHERE backlog_normalized > 0
              ) b2 ON b1.stock_code = b2.stock_code AND b2.rn = 5
              WHERE b1.rn = 1
            ),
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close,
                MIN(CASE WHEN close > 0 THEN close END) AS low_52w
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
                AND stock_code NOT LIKE '%^%'
              GROUP BY stock_code
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr, u.per,
              bl.latest_year,
              bl.latest_quarter,
              bl.backlog_latest_100m,
              bl.backlog_1y_ago_100m,
              bl.backlog_yoy_pct,
              bl.backlog_to_rev,
              px.cur_close,
              ROUND(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) * 100, 1) AS pct_above_52w_low,
              CASE
                WHEN bl.backlog_yoy_pct >= 100 THEN 'SURGE_OVER_100PCT'
                WHEN bl.backlog_yoy_pct >= 50 THEN 'SURGE_50_100PCT'
                WHEN bl.backlog_yoy_pct >= 20 THEN 'GROWING_20_50PCT'
                WHEN bl.backlog_yoy_pct >= 0 THEN 'STABLE'
                ELSE 'DECLINING'
              END AS backlog_trend,
              GREATEST(0, LEAST(100,
                CASE
                  WHEN bl.backlog_yoy_pct >= 100 THEN 40
                  WHEN bl.backlog_yoy_pct >= 50 THEN 30
                  WHEN bl.backlog_yoy_pct >= 20 THEN 20
                  WHEN bl.backlog_yoy_pct >= 0 THEN 5
                  ELSE 0
                END
                + IF(bl.backlog_to_rev >= 2.0, 20, IF(bl.backlog_to_rev >= 1.0, 10, 0))
                + IF(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.10 AND 0.70, 20, 0)
                + IF(u.pbr > 0 AND u.pbr < 1.5, 10, 0)
                + IF(u.per > 0 AND u.per < 15, 10, 0)
              )) AS backlog_score
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN bl USING (stock_code)
            JOIN px USING (stock_code)
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND bl.backlog_latest_100m > 0
            ORDER BY backlog_score DESC
        """,

        # 20. 역사적 저PBR 분위 — valuation_history 기반 (하위 25% = 역사적 저평가)
        "v_tenbagger_historical_cheap": f"""
            WITH
            pbr_stats AS (
              SELECT
                stock_code,
                COUNT(*) AS q_count,
                AVG(pbr) AS avg_pbr,
                MIN(pbr) AS min_pbr,
                MAX(pbr) AS max_pbr,
                STDDEV(pbr) AS std_pbr,
                ARRAY_AGG(pbr ORDER BY period_end DESC LIMIT 1)[OFFSET(0)] AS cur_pbr,
                ARRAY_AGG(close_price ORDER BY period_end DESC LIMIT 1)[OFFSET(0)] AS cur_price_hist,
                ARRAY_AGG(`market_cap_억` ORDER BY period_end DESC LIMIT 1)[OFFSET(0)] AS mktcap_hist
              FROM `{PROJECT_ID}.{DATASET_ID}.valuation_history`
              WHERE pbr > 0 AND pbr < 50
              GROUP BY stock_code
              HAVING COUNT(*) >= 8
            ),
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0 AND stock_code NOT LIKE '%^%'
              GROUP BY stock_code
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.per,
              u.roe,
              ROUND(ps.cur_pbr, 2) AS pbr_current,
              ROUND(ps.avg_pbr, 2) AS pbr_2y_avg,
              ROUND(ps.min_pbr, 2) AS pbr_hist_min,
              ROUND(ps.max_pbr, 2) AS pbr_hist_max,
              ps.q_count AS pbr_quarters,
              -- PBR percentile in historical range (0=lowest, 100=highest)
              ROUND(SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) * 100, 1) AS pbr_pct_in_range,
              -- PBR vs historical average ratio
              ROUND(SAFE_DIVIDE(ps.cur_pbr, ps.avg_pbr) * 100, 1) AS pbr_vs_avg_pct,
              -- PBR Z-score (lower = more historically undervalued)
              ROUND(SAFE_DIVIDE(ps.cur_pbr - ps.avg_pbr, NULLIF(ps.std_pbr, 0)), 2) AS pbr_zscore,
              px.cur_close,
              CASE
                WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.10 THEN 'HIST_LOW_10PCT'
                WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.25 THEN 'HIST_LOW_25PCT'
                WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.50 THEN 'BELOW_MEDIAN'
                ELSE 'ABOVE_MEDIAN'
              END AS pbr_hist_label,
              GREATEST(0, LEAST(100,
                -- historical low PBR score
                CASE
                  WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.10 THEN 50
                  WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.25 THEN 35
                  WHEN SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.50 THEN 20
                  ELSE 0
                END
                + IF(u.roe > 10, 20, IF(u.roe > 5, 10, 0))
                + IF(u.per > 0 AND u.per < 10, 20, IF(u.per > 0 AND u.per < 20, 10, 0))
                + IF(u.market_cap BETWEEN 100 AND 3000, 10, 0)
              )) AS hist_value_score
            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN pbr_stats ps USING (stock_code)
            JOIN px USING (stock_code)
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND ps.cur_pbr > 0
              AND SAFE_DIVIDE(ps.cur_pbr - ps.min_pbr, ps.max_pbr - ps.min_pbr) <= 0.30
            ORDER BY pbr_pct_in_range ASC
        """,

        # 21. 2026 텐버거 종합 마스터 발굴 뷰 — 10대 시그널 통합 점수
        #     데이터 기반 설계: 3배 달성 1,991종목 역산 분석 반영
        #     낙폭과대(25)+펀더멘털변화(25)+저평가소형(20)+카탈리스트(15)+수급반전(10)+섹터보정(5)
        "v_tenbagger_final_2026": f"""
            WITH
            -- 가격/52주 데이터
            px AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS cur_close,
                MIN(CASE WHEN close > 0 THEN close END) AS low_52w,
                MAX(close) AS high_52w,
                ARRAY_AGG(volume ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS recent_vol,
                AVG(volume) AS avg_vol_60d
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND stock_code NOT LIKE '%=%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            ),
            -- 60일 순매수 수급 (price_history 기반, 단위: 억원)
            sup AS (
              SELECT
                stock_code,
                ROUND(SUM(inst_net_buy_amt) / 100.0, 0) AS inst_net_60d,
                ROUND(SUM(frn_net_buy_amt) / 100.0, 0) AS frn_net_60d
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
                AND stock_code NOT LIKE '%^%'
                AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
              GROUP BY stock_code
            ),
            -- 텐버거 엔진 최신 점수
            tb AS (
              SELECT stock_code, stock_name, total_score,
                     ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY run_time DESC) AS rn
              FROM `{PROJECT_ID}.{DATASET_ID}.tenbagger_results`
            ),
            -- 임원 매수 (1년)
            ins AS (
              SELECT stock_code,
                     COUNT(*) AS buy_cnt,
                     MAX(IF(is_ceo=1,1,0)) AS ceo_buy
              FROM `{PROJECT_ID}.{DATASET_ID}.dart_insider_holdings`
              WHERE SAFE_CAST(rcept_dt AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
                AND SAFE_CAST(sp_stock_lmp_irds_cnt AS FLOAT64) > 0
              GROUP BY stock_code
            ),
            -- 자사주 취득/소각 (1년, dot and dash format both supported)
            bb AS (
              SELECT stock_code,
                     COUNTIF(event_type LIKE '%취득%') AS acq,
                     COUNTIF(event_type LIKE '%소각%') AS cnl
              FROM `{PROJECT_ID}.{DATASET_ID}.treasury_buyback`
              WHERE COALESCE(
                      SAFE.PARSE_DATE('%Y.%m.%d', rcept_dt),
                      SAFE.PARSE_DATE('%Y-%m-%d', rcept_dt)
                    ) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            ),
            -- R&D/기술이전 (1년)
            rd AS (
              SELECT stock_code,
                     COUNTIF(signal_type='tech_transfer') AS tech,
                     COUNTIF(signal_type='patent') AS pat,
                     COUNTIF(signal_type IN ('rd_contract','license')) AS rd
              FROM `{PROJECT_ID}.{DATASET_ID}.dart_rd_patent_signals`
              WHERE SAFE_CAST(rcept_dt AS DATE) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
              GROUP BY stock_code
            ),
            -- 신용잔고 변화 (60일)
            cr AS (
              SELECT stock_code,
                     AVG(CASE
                       WHEN DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 DAY)
                       THEN SAFE_CAST(credit_ratio AS FLOAT64) END) AS ratio_now,
                     AVG(CASE
                       WHEN DATE(dt) BETWEEN
                            DATE_SUB(CURRENT_DATE(), INTERVAL 50 DAY) AND DATE_SUB(CURRENT_DATE(), INTERVAL 20 DAY)
                       THEN SAFE_CAST(credit_ratio AS FLOAT64) END) AS ratio_old
              FROM `{PROJECT_ID}.{DATASET_ID}.kiwoom_credit_balance`
              WHERE credit_balance_qty > 0
                AND DATE(dt) >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
              GROUP BY stock_code
            ),
            -- 흑자전환/이익급증 신호
            ea AS (
              SELECT stock_code,
                     COUNTIF(signal_type IN ('TTM_OP_INFLECT','TTM_BOTH')) AS inflect_cnt,
                     COUNTIF(signal_type IN ('TTM_REV_30','TTM_OP_ACCEL')) AS surge_cnt
              FROM `{PROJECT_ID}.{DATASET_ID}.earnings_signals`
              WHERE is_active = 1
              GROUP BY stock_code
            ),
            -- 역사적 PBR 분위
            vhp AS (
              SELECT stock_code,
                     AVG(pbr) AS avg_pbr,
                     ARRAY_AGG(pbr ORDER BY period_end DESC LIMIT 1)[OFFSET(0)] AS cur_pbr,
                     MIN(pbr) AS min_pbr,
                     MAX(pbr) AS max_pbr
              FROM `{PROJECT_ID}.{DATASET_ID}.valuation_history`
              WHERE pbr > 0 AND pbr < 50
              GROUP BY stock_code
              HAVING COUNT(*) >= 4
            ),
            -- 수주잔고 YoY 증가율 (capped at 5x to prevent outlier noise)
            bl AS (
              SELECT b1.stock_code,
                     LEAST(5.0, ROUND(SAFE_DIVIDE(b1.backlog_normalized - b2.backlog_normalized,
                                       b2.backlog_normalized), 2)) AS yoy_ratio
              FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                    FROM `{PROJECT_ID}.{DATASET_ID}.order_backlog` WHERE backlog_normalized > 100) b1
              LEFT JOIN (SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                          FROM `{PROJECT_ID}.{DATASET_ID}.order_backlog` WHERE backlog_normalized > 100) b2
                ON b1.stock_code = b2.stock_code AND b2.rn = 5
              WHERE b1.rn = 1 AND b2.stock_code IS NOT NULL
            )
            SELECT
              u.stock_code,
              u.stock_name,
              u.sector_large,
              u.market,
              ROUND(u.market_cap, 0) AS market_cap_100m,
              u.pbr,
              u.per,
              u.roe,
              COALESCE(tb.total_score, 0) AS engine_score,
              px.cur_close,
              ROUND(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) * 100, 1) AS pct_above_low,
              ROUND(SAFE_DIVIDE(px.high_52w - px.cur_close, px.high_52w) * 100, 1) AS pct_below_high,
              ROUND(COALESCE(sup.inst_net_60d, 0), 0) AS inst_net_60d_100m,
              ROUND(COALESCE(sup.frn_net_60d, 0), 0) AS frn_net_60d_100m,
              COALESCE(ins.buy_cnt, 0) AS insider_buy_cnt,
              COALESCE(ins.ceo_buy, 0) AS ceo_buy,
              COALESCE(bb.acq, 0) AS buyback_acq,
              COALESCE(bb.cnl, 0) AS buyback_cnl,
              COALESCE(rd.tech, 0) AS tech_transfer_cnt,
              COALESCE(rd.pat, 0) AS patent_cnt,
              ROUND(COALESCE(cr.ratio_now, 0), 2) AS credit_ratio_pct,
              ROUND(COALESCE(cr.ratio_now, 0) - COALESCE(cr.ratio_old, 0), 2) AS credit_change,
              COALESCE(ea.inflect_cnt, 0) AS turnaround_cnt,
              COALESCE(ea.surge_cnt, 0) AS surge_cnt,
              ROUND(COALESCE(vhp.cur_pbr, u.pbr), 2) AS pbr_hist_cur,
              ROUND(COALESCE(vhp.avg_pbr, 0), 2) AS pbr_hist_avg,
              ROUND(SAFE_DIVIDE(
                COALESCE(vhp.cur_pbr, u.pbr) - COALESCE(vhp.min_pbr, 0),
                NULLIF(COALESCE(vhp.max_pbr, 1) - COALESCE(vhp.min_pbr, 0), 0)) * 100, 1) AS pbr_pct_in_range,
              ROUND(COALESCE(bl.yoy_ratio, 0) * 100, 1) AS backlog_yoy_pct,

              -- ★ Tenbagger composite score (100 pts max)
              LEAST(100, GREATEST(0, ROUND(
                -- [A] Oversold drawdown signal (25pts) - 70.5% of 3x stocks started -30~70% below 52w high
                CASE
                  WHEN SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.05 AND 0.70 THEN 25
                  WHEN SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.70 AND 1.20 THEN 12
                  ELSE 5
                END
                -- [B] Fundamental change signal (25pts) - turnaround, earnings surge, backlog surge
                + IF(COALESCE(ea.inflect_cnt, 0) > 0, 20, 0)
                + IF(COALESCE(ea.surge_cnt, 0) > 0 AND COALESCE(ea.inflect_cnt, 0) = 0, 12, 0)
                + CASE
                    WHEN COALESCE(bl.yoy_ratio, 0) >= 0.5 THEN 8
                    WHEN COALESCE(bl.yoy_ratio, 0) >= 0.2 THEN 4
                    ELSE 0
                  END
                -- [C] Undervalued small-cap signal (20pts)
                + CASE
                    WHEN u.pbr > 0 AND u.pbr < 0.7 THEN 15
                    WHEN u.pbr > 0 AND u.pbr < 1.0 THEN 10
                    WHEN u.pbr > 0 AND u.pbr < 1.5 THEN 5
                    ELSE 0
                  END
                + IF(u.market_cap BETWEEN 100 AND 2000, 5, 0)
                -- [D] Catalyst signal (15pts) - CEO buy, tech transfer, buyback cancellation
                + IF(COALESCE(ins.ceo_buy, 0) = 1, 8, 0)
                + IF(COALESCE(ins.buy_cnt, 0) > 0, LEAST(4, COALESCE(ins.buy_cnt, 0) * 2), 0)
                + IF(COALESCE(rd.tech, 0) > 0, 6, 0)
                + IF(COALESCE(bb.cnl, 0) > 0, 4, 0)
                + IF(COALESCE(bb.acq, 0) > 0, 2, 0)
                -- [E] Supply reversal signal (10pts) - institutional + foreign net buy
                + CASE
                    WHEN COALESCE(sup.inst_net_60d, 0) > 30 AND COALESCE(sup.frn_net_60d, 0) > 30 THEN 10
                    WHEN COALESCE(sup.inst_net_60d, 0) > 10 OR COALESCE(sup.frn_net_60d, 0) > 10 THEN 5
                    WHEN COALESCE(cr.ratio_now, 0) - COALESCE(cr.ratio_old, 0) < -0.3 THEN 4
                    ELSE 0
                  END
                -- [F] Historical low PBR bonus (5pts)
                + CASE
                    WHEN SAFE_DIVIDE(
                      COALESCE(vhp.cur_pbr, u.pbr) - COALESCE(vhp.min_pbr, 0),
                      NULLIF(COALESCE(vhp.max_pbr, 1) - COALESCE(vhp.min_pbr, 0), 0)) <= 0.20 THEN 5
                    WHEN SAFE_DIVIDE(
                      COALESCE(vhp.cur_pbr, u.pbr) - COALESCE(vhp.min_pbr, 0),
                      NULLIF(COALESCE(vhp.max_pbr, 1) - COALESCE(vhp.min_pbr, 0), 0)) <= 0.35 THEN 2
                    ELSE 0
                  END
              , 1))) AS tenbagger_2026_score,

              -- 신호 요약 태그
              CONCAT(
                IF(SAFE_DIVIDE(px.cur_close - px.low_52w, px.low_52w) BETWEEN 0.05 AND 0.70, '📉낙폭 ', ''),
                IF(COALESCE(ea.inflect_cnt, 0) > 0, '📈흑자전환 ', ''),
                IF(COALESCE(ea.surge_cnt, 0) > 0, '💹이익급증 ', ''),
                IF(COALESCE(ins.ceo_buy, 0) = 1, '👔CEO매수 ', ''),
                IF(COALESCE(ins.buy_cnt, 0) > 0, '🧑‍💼임원매수 ', ''),
                IF(COALESCE(rd.tech, 0) > 0, '🔬기술이전 ', ''),
                IF(COALESCE(bb.cnl, 0) > 0, '🔄자사주소각 ', ''),
                IF(COALESCE(sup.inst_net_60d, 0) > 10, '🏦기관매수 ', ''),
                IF(COALESCE(sup.frn_net_60d, 0) > 10, '🌏외인매수 ', ''),
                IF(COALESCE(bl.yoy_ratio, 0) >= 0.2, '📋수주급증 ', ''),
                IF(COALESCE(cr.ratio_now, 0) - COALESCE(cr.ratio_old, 0) < -0.3, '⬇신용감소 ', '')
              ) AS signal_flags

            FROM `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
            JOIN px ON u.stock_code = px.stock_code
            LEFT JOIN tb ON u.stock_code = tb.stock_code AND tb.rn = 1
            LEFT JOIN sup ON u.stock_code = sup.stock_code
            LEFT JOIN ins ON u.stock_code = ins.stock_code
            LEFT JOIN bb ON u.stock_code = bb.stock_code
            LEFT JOIN rd ON u.stock_code = rd.stock_code
            LEFT JOIN cr ON u.stock_code = cr.stock_code
            LEFT JOIN ea ON u.stock_code = ea.stock_code
            LEFT JOIN vhp ON u.stock_code = vhp.stock_code
            LEFT JOIN bl ON u.stock_code = bl.stock_code
            WHERE u.market IN ('KOSPI', 'KOSDAQ')
              AND u.market_cap BETWEEN 50 AND 15000
              AND px.cur_close > 0
            ORDER BY tenbagger_2026_score DESC
        """,
    })

    for view_name, query in views.items():
        try:
            table_ref = f"{PROJECT_ID}.{DATASET_ID}.{view_name}"
            view = bigquery.Table(table_ref)
            view.view_query = query.strip()
            client.delete_table(table_ref, not_found_ok=True)
            client.create_table(view)
            logger.info(f"  뷰 재생성: {view_name}")
        except Exception as e:
            logger.error(f"  뷰 [{view_name}] 오류: {e}")


def create_trigger_discovery_views(client):
    """Trigger Discovery Lab용 저비용 분석 뷰 생성."""
    from google.cloud import bigquery

    views = {
        "v_trigger_discovery_scorecard": f"""
            SELECT
              e.trigger_key,
              ANY_VALUE(e.trigger_name) AS trigger_name,
              e.source,
              r.horizon_days,
              COUNT(*) AS sample_count,
              COUNT(DISTINCT r.stock_code) AS stock_count,
              ROUND(AVG(r.return_pct), 2) AS avg_return_pct,
              ROUND(APPROX_QUANTILES(r.return_pct, 101)[OFFSET(50)], 2) AS median_return_pct,
              ROUND(AVG(IF(r.return_pct > 0, 1, 0)) * 100, 2) AS positive_rate_pct,
              ROUND(AVG(IF(r.return_pct >= 50, 1, 0)) * 100, 2) AS gain50_rate_pct,
              ROUND(AVG(IF(r.return_pct >= 100, 1, 0)) * 100, 2) AS double_rate_pct,
              ROUND(AVG(IF(r.return_pct <= -30, 1, 0)) * 100, 2) AS loss30_rate_pct,
              ROUND(AVG(r.max_drawdown_pct), 2) AS avg_mdd_pct,
              ROUND(SAFE_DIVIDE(
                SUM(IF(r.return_pct > 0, r.return_pct, 0)),
                ABS(SUM(IF(r.return_pct < 0, r.return_pct, 0)))
              ), 2) AS profit_factor
            FROM `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_events` e
            JOIN `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_forward_returns` r
              USING(event_id)
            GROUP BY e.trigger_key, e.source, r.horizon_days
            HAVING sample_count >= 30
            ORDER BY horizon_days, profit_factor DESC, avg_return_pct DESC
        """,
        "v_trigger_sector_scorecard": f"""
            SELECT
              COALESCE(l.sector_name, e.sector_name, '미분류') AS sector_name,
              e.trigger_key,
              ANY_VALUE(e.trigger_name) AS trigger_name,
              r.horizon_days,
              COUNT(*) AS sample_count,
              COUNT(DISTINCT r.stock_code) AS stock_count,
              ROUND(AVG(r.return_pct), 2) AS avg_return_pct,
              ROUND(APPROX_QUANTILES(r.return_pct, 101)[OFFSET(50)], 2) AS median_return_pct,
              ROUND(AVG(IF(r.return_pct > 0, 1, 0)) * 100, 2) AS positive_rate_pct,
              ROUND(AVG(IF(r.return_pct >= 50, 1, 0)) * 100, 2) AS gain50_rate_pct,
              ROUND(AVG(IF(r.return_pct <= -30, 1, 0)) * 100, 2) AS loss30_rate_pct,
              ROUND(SAFE_DIVIDE(
                SUM(IF(r.return_pct > 0, r.return_pct, 0)),
                ABS(SUM(IF(r.return_pct < 0, r.return_pct, 0)))
              ), 2) AS profit_factor
            FROM `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_events` e
            JOIN `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_forward_returns` r USING(event_id)
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_stock_links` l
              ON l.event_id=e.event_id AND l.stock_code=r.stock_code
            GROUP BY sector_name, e.trigger_key, r.horizon_days
            HAVING sample_count >= 20
            ORDER BY horizon_days, profit_factor DESC, avg_return_pct DESC
        """,
        "v_trigger_recent_candidates": f"""
            WITH trigger_stats AS (
              SELECT
                trigger_key,
                horizon_days,
                sample_count,
                avg_return_pct,
                positive_rate_pct,
                loss30_rate_pct,
                profit_factor
              FROM `{PROJECT_ID}.{DATASET_ID}.v_trigger_discovery_scorecard`
              WHERE horizon_days = 60
                AND sample_count >= 30
                AND positive_rate_pct >= 52
                AND loss30_rate_pct <= 35
            ),
            latest_price AS (
              SELECT
                stock_code,
                ARRAY_AGG(close ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS latest_close,
                ARRAY_AGG(date ORDER BY date DESC LIMIT 1)[OFFSET(0)] AS latest_date
              FROM `{PROJECT_ID}.{DATASET_ID}.price_history`
              WHERE close > 0
              GROUP BY stock_code
            )
            SELECT
              e.available_date,
              e.trigger_key,
              e.trigger_name,
              COALESCE(l.stock_code, e.stock_code) AS stock_code,
              COALESCE(l.stock_name, u.stock_name) AS stock_name,
              COALESCE(l.sector_name, u.sector_large, e.sector_name) AS sector_name,
              e.direction,
              ROUND(e.strength, 2) AS event_strength,
              ROUND(e.value, 2) AS event_value,
              ts.sample_count,
              ts.avg_return_pct AS historical_avg_60d,
              ts.positive_rate_pct AS historical_positive_60d,
              ts.loss30_rate_pct AS historical_loss30_60d,
              ts.profit_factor AS historical_profit_factor,
              lp.latest_close,
              lp.latest_date,
              ROUND(
                COALESCE(e.strength, 0)
                + COALESCE(ts.profit_factor, 0)
                + COALESCE(ts.positive_rate_pct, 0) / 20
                - COALESCE(ts.loss30_rate_pct, 0) / 25
                + COALESCE(l.confidence, 0),
                2
              ) AS discovery_score
            FROM `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_events` e
            JOIN trigger_stats ts ON ts.trigger_key=e.trigger_key
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.trigger_discovery_stock_links` l USING(event_id)
            LEFT JOIN `{PROJECT_ID}.{DATASET_ID}.stock_universe` u
              ON u.stock_code = COALESCE(l.stock_code, e.stock_code)
            LEFT JOIN latest_price lp
              ON lp.stock_code = COALESCE(l.stock_code, e.stock_code)
            WHERE DATE(e.available_date) >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 45 DAY)
              AND COALESCE(l.stock_code, e.stock_code) IS NOT NULL
            ORDER BY discovery_score DESC, e.available_date DESC
        """,
    }

    for view_name, query in views.items():
        try:
            table_ref = f"{PROJECT_ID}.{DATASET_ID}.{view_name}"
            view = bigquery.Table(table_ref)
            view.view_query = query.strip()
            client.delete_table(table_ref, not_found_ok=True)
            client.create_table(view)
            logger.info("  Trigger Lab 뷰 재생성: %s", view_name)
        except Exception as e:
            logger.error("  Trigger Lab 뷰 [%s] 오류: %s", view_name, e)


def main():
    parser = argparse.ArgumentParser(description="stock.db → BigQuery 동기화")
    parser.add_argument("--mode", choices=["full", "daily", "daily-lite", "table", "views", "trigger-views", "external"],
                        default="daily", help="동기화 모드")
    parser.add_argument("--table", help="특정 테이블만 업로드 (--mode table 시)")
    parser.add_argument("--source", choices=sorted(EXTERNAL_DB_SOURCES.keys()),
                        help="external 모드에서 특정 외부 DB만 업로드")
    parser.add_argument("--days", type=int, default=7,
                        help="daily 모드에서 price_history 최근 N일 (기본: 7)")
    parser.add_argument("--skip-trigger-lab-build", action="store_true",
                        help="daily-lite에서 로컬 trigger_discovery 테이블 재생성을 건너뜀")
    args = parser.parse_args()

    logger.info(f"=== BigQuery 동기화 시작 [mode={args.mode}] ===")
    client = get_bq_client()
    ensure_dataset(client)

    if args.mode == "full":
        sync_full()
        logger.info("분석 뷰 생성 중...")
        create_analysis_views(client)

    elif args.mode == "daily":
        sync_daily(days_back=args.days)
        logger.info("분석 뷰 갱신 중...")
        create_analysis_views(client)

    elif args.mode == "daily-lite":
        sync_daily_lite(days_back=args.days, rebuild_trigger_lab=not args.skip_trigger_lab_build)
        logger.info("Trigger Discovery Lab 뷰 갱신 중...")
        create_trigger_discovery_views(client)

    elif args.mode == "table":
        if not args.table:
            logger.error("--table 옵션이 필요합니다")
            sys.exit(1)
        sync_full(tables=[args.table])

    elif args.mode == "views":
        create_analysis_views(client)

    elif args.mode == "trigger-views":
        create_trigger_discovery_views(client)

    elif args.mode == "external":
        tables = [args.table] if args.table else None
        sync_external_sources(source=args.source, tables=tables)

    logger.info("=== 완료 ===")


if __name__ == "__main__":
    main()
