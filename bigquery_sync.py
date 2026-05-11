"""
BigQuery 동기화 스크립트
stock.db → Google BigQuery (project-d8a62269-8156-4f96-870.stock_dashboard)

실행:
  python3 bigquery_sync.py --mode full      # 전체 테이블 최초 업로드
  python3 bigquery_sync.py --mode daily     # 일별 증분 업로드 (price_history 최근 7일)
  python3 bigquery_sync.py --mode table --table tenbagger_results  # 특정 테이블만
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
PROJECT_ID  = "project-d8a62269-8156-4f96-870"
DATASET_ID  = "stock_dashboard"
DB_PATH     = "/Applications/stock_dashboard/stock.db"
CHUNK_SIZE  = 50_000   # BigQuery 업로드 단위

# ── 제외 테이블 (보고서, 백업, 내부, 빈 테이블) ─────────────────────
EXCLUDE_TABLES = {
    "report_files",
    "report_files_backup_before_cleanup_20260430_234638",
    "financial_data_backup_20260412",
    "sqlite_sequence",
    "dart_disclosure_cache",       # 캐시
    "nps_workplace_monthly",       # API 차단으로 빈 테이블
    "foreign_holding_daily",       # 수집 불가
    "investor_trading_daily",      # 수집 불가
    "listed_company_info",         # 빈 테이블
    "sector_info",                 # 빈 테이블
    "stock_bizno_map",             # 내부 매핑
    "futures_contract_daily",      # 선물 데이터 (423K행, 주식 분석 무관)
}

# ── price_history는 증분 처리 (전체는 너무 큼) ───────────────────────
INCREMENTAL_TABLES = {"price_history"}  # daily 모드에서 최근 N일만

# ── BigQuery 테이블별 파티셔닝/클러스터링 설정 ───────────────────────
TABLE_OPTIONS = {
    "price_history": {
        "partition_field": "date",
        "clustering_fields": ["stock_code"],
    },
    "financial_data": {
        "clustering_fields": ["stock_code"],
    },
    "tenbagger_results": {
        "clustering_fields": ["stock_code"],
    },
    "dart_contracts": {
        "clustering_fields": ["stock_code", "ai_signal"],
    },
    # bas_dt는 YYYYMMDD 문자열 → BigQuery time-partitioning은 DATE/TIMESTAMP만 지원
    # sanitize_df에서 YYYY-MM-DD로 변환하지만 autodetect는 STRING으로 인식 → 파티션 제거
    "short_rank_daily": {
        "clustering_fields": ["stock_code"],
    },
    "short_sell_daily": {
        "clustering_fields": ["stock_code"],
    },
    "short_sector_daily": {
        "clustering_fields": ["stock_code"],
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


def get_sqlite_tables():
    """stock.db 테이블 목록 (제외 목록 필터링)"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    tables = [r[0] for r in rows if r[0] not in EXCLUDE_TABLES]
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
        if col.lower() in ("date", "bas_dt", "post_date", "disclosed_at", "created_at",
                           "contract_start", "contract_end", "analyzed_at", "snapshot_date",
                           "tx_date", "bought_at", "entry_date"):
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass

    return df


def upload_table(client, table_name: str, df: pd.DataFrame, write_mode: str = "WRITE_TRUNCATE"):
    """DataFrame → BigQuery 업로드"""
    from google.cloud import bigquery

    if df.empty:
        logger.info(f"  [{table_name}] 0행 — 스킵")
        return

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{table_name}"
    opts = TABLE_OPTIONS.get(table_name, {})

    job_config = bigquery.LoadJobConfig(
        write_disposition=write_mode,
        autodetect=True,
    )

    # 파티셔닝 설정
    if "partition_field" in opts:
        pf = opts["partition_field"]
        if pf in df.columns:
            job_config.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field=pf,
            )

    # 클러스터링 설정
    if "clustering_fields" in opts:
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

    # ── 소형 테이블 FULL REFRESH ──────────────────────────────────────
    full_refresh_daily = [
        "tenbagger_results", "dart_contracts", "stock_universe",
        "signal_result", "buy_candidates", "watchlist",
        "peak_holding", "peak_trade", "stockeasy_analysis",
        "sector_posts", "sector_stocks",
        "short_rank_daily", "short_sell_daily",
        "short_foreign_balance", "short_foreign_trade", "short_monthly_stat",
        "portfolio", "portfolio_snapshot", "portfolio_tx",
    ]

    for tname in full_refresh_daily:
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{tname}"', conn)
            df = sanitize_df(df)
            upload_table(client, tname, df, write_mode="WRITE_TRUNCATE")
        except Exception as e:
            logger.error(f"  [{tname}] ❌ {e}")

    conn.close()
    logger.info("=== 일별 동기화 완료 ===")


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
              u.market_cap / 1e8 AS mktcap_100m,
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
                CASE WHEN u.market_cap BETWEEN 30000000000 AND 300000000000 THEN 20 ELSE 0 END
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
              AND u.market_cap > 5000000000       -- 50
              AND f.revenue > 0
              AND f.op_profit > 0           --
              AND ma.current_close > 0

            ORDER BY triple_pattern_score DESC, u.market_cap ASC
        """,
    }

    for view_name, query in views.items():
        try:
            table_ref = f"{PROJECT_ID}.{DATASET_ID}.{view_name}"
            view = bigquery.Table(table_ref)
            view.view_query = query.strip()
            client.create_table(view, exists_ok=True)
            logger.info(f"  뷰 생성: {view_name}")
        except Exception as e:
            logger.error(f"  뷰 [{view_name}] 오류: {e}")


def main():
    parser = argparse.ArgumentParser(description="stock.db → BigQuery 동기화")
    parser.add_argument("--mode", choices=["full", "daily", "table", "views"],
                        default="daily", help="동기화 모드")
    parser.add_argument("--table", help="특정 테이블만 업로드 (--mode table 시)")
    parser.add_argument("--days", type=int, default=7,
                        help="daily 모드에서 price_history 최근 N일 (기본: 7)")
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

    elif args.mode == "table":
        if not args.table:
            logger.error("--table 옵션이 필요합니다")
            sys.exit(1)
        sync_full(tables=[args.table])

    elif args.mode == "views":
        create_analysis_views(client)

    logger.info("=== 완료 ===")


if __name__ == "__main__":
    main()
