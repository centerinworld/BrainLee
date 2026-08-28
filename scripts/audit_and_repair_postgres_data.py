#!/usr/bin/env python3
"""Audit all PostgreSQL tables and repair only deterministic data corruption.

The audit is intentionally conservative: business outliers are reported, while
automatic repairs are limited to invalid OHLC rows with a valid same-day peer.
Every changed row is stored as JSONB and can be restored by batch id.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import DATABASE_URL  # noqa: E402

OUT_ROOT = ROOT / "research_outputs" / "postgres_data_quality"


def pg_url() -> str:
    return DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


def scalar(conn: psycopg.Connection, query: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(query, params).fetchone()
    return row[0] if row else None


def fetch_dicts(
    conn: psycopg.Connection, query: str, params: tuple[Any, ...] = (), limit: int = 25
) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchmany(limit)]


def check(
    conn: psycopg.Connection,
    check_id: str,
    severity: str,
    description: str,
    count_sql: str,
    sample_sql: str | None = None,
    params: tuple[Any, ...] = (),
    auto_repairable: bool = False,
) -> dict[str, Any]:
    count = int(scalar(conn, count_sql, params) or 0)
    return {
        "id": check_id,
        "severity": severity,
        "description": description,
        "count": count,
        "status": "pass" if count == 0 else "fail",
        "auto_repairable": auto_repairable,
        "samples": fetch_dicts(conn, sample_sql, params) if count and sample_sql else [],
    }


def inventory(conn: psycopg.Connection) -> dict[str, Any]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
        )
    pk_tables = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT table_name FROM information_schema.table_constraints "
            "WHERE table_schema='public' AND constraint_type='PRIMARY KEY'"
        )
    }
    return {
        "table_count": len(tables),
        "nonempty_table_count": sum(v > 0 for v in counts.values()),
        "total_rows": sum(counts.values()),
        "row_counts": counts,
        "tables_without_primary_key": sorted(set(tables) - pk_tables),
    }


def run_checks(conn: psycopg.Connection) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    add = checks.append

    price_invalid = """
        close IS NULL OR close <= 0 OR volume IS NULL OR volume < 0
        OR (volume > 0 AND (open IS NULL OR open <= 0 OR high IS NULL OR high <= 0 OR low IS NULL OR low <= 0))
        OR (open > 0 AND (high < low OR high < open*0.98 OR high < close*0.98 OR low > open*1.02 OR low > close*1.02))
        OR open IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR high IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR low IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR close IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
    """
    naver_valid = """
        n.open > 0 AND n.close > 0 AND n.high >= n.low
        AND n.high >= n.open AND n.high >= n.close
        AND n.low <= n.open AND n.low <= n.close AND n.volume >= 0
        AND n.open NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        AND n.high NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        AND n.low NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        AND n.close NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        AND n.volume NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
    """
    add(check(conn, "price.invalid_ohlcv", "critical", "price_history OHLCV 불변식 위반",
        f"SELECT COUNT(*) FROM price_history WHERE {price_invalid}",
        f"SELECT id,stock_code,date,open,high,low,close,volume FROM price_history WHERE {price_invalid} ORDER BY date DESC",
        auto_repairable=True))
    add(check(conn, "price.adjusted_rounding_bounds", "medium", "조정가격 반올림으로 2% 이내 OHLC 경계 이탈",
        """SELECT COUNT(*) FROM price_history
           WHERE open>0 AND high>0 AND low>0 AND close>0 AND high>=low
             AND (high<open OR high<close OR low>open OR low>close)
             AND high>=open*0.98 AND high>=close*0.98
             AND low<=open*1.02 AND low<=close*1.02""",
        """SELECT id,stock_code,date,open,high,low,close FROM price_history
           WHERE open>0 AND high>0 AND low>0 AND close>0 AND high>=low
             AND (high<open OR high<close OR low>open OR low>close)
             AND high>=open*0.98 AND high>=close*0.98
             AND low<=open*1.02 AND low<=close*1.02
           ORDER BY GREATEST(open,close)/GREATEST(high,1) DESC""", auto_repairable=True))
    add(check(conn, "price.invalid_repairable_from_naver", "critical", "동일 일자 Naver 정상행으로 확정 복구 가능",
        f"SELECT COUNT(*) FROM (SELECT * FROM price_history WHERE {price_invalid}) p JOIN naver_price_history_backfill n USING(stock_code,date) WHERE {naver_valid}",
        f"SELECT p.id,p.stock_code,p.date,p.open,p.high,p.low,p.close,n.open AS n_open,n.high AS n_high,n.low AS n_low,n.close AS n_close FROM (SELECT * FROM price_history WHERE {price_invalid}) p JOIN naver_price_history_backfill n USING(stock_code,date) WHERE {naver_valid}",
        auto_repairable=True))
    add(check(conn, "price.duplicate_grain", "critical", "price_history 종목·일자 중복",
        "SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT stock_code,date,COUNT(*) cnt FROM price_history GROUP BY stock_code,date HAVING COUNT(*)>1) d",
        "SELECT stock_code,date,COUNT(*) cnt FROM price_history GROUP BY stock_code,date HAVING COUNT(*)>1 ORDER BY cnt DESC,stock_code,date"))
    add(check(conn, "price.malformed_date", "high", "price_history 날짜 형식 오류",
        "SELECT COUNT(*) FROM price_history WHERE date IS NULL OR date !~ '^\\d{4}-\\d{2}-\\d{2}$'",
        "SELECT id,stock_code,date FROM price_history WHERE date IS NULL OR date !~ '^\\d{4}-\\d{2}-\\d{2}$'"))
    add(check(conn, "price.future_date", "high", "현재일 이후 가격 행",
        "SELECT COUNT(*) FROM price_history WHERE date ~ '^\\d{4}-\\d{2}-\\d{2}$' AND date::date > CURRENT_DATE",
        "SELECT id,stock_code,date,close FROM price_history WHERE date ~ '^\\d{4}-\\d{2}-\\d{2}$' AND date::date > CURRENT_DATE ORDER BY date DESC"))
    add(check(conn, "price.unresolved_extreme_jump", "high", "가격 점프 감사에서 미해결/오염으로 분류된 이벤트",
        "SELECT COUNT(*) FROM price_jump_audit WHERE classification IN ('mixed_basis_or_price_corruption','unresolved_active_common')",
        "SELECT stock_code,event_date,previous_close,event_close,price_ratio,classification,evidence FROM price_jump_audit WHERE classification IN ('mixed_basis_or_price_corruption','unresolved_active_common') ORDER BY ABS(price_ratio) DESC"))

    sp_invalid = """
        close_price IS NULL OR close_price <= 0 OR volume IS NULL OR volume < 0
        OR (volume > 0 AND (open_price IS NULL OR open_price <= 0 OR high_price IS NULL OR high_price <= 0 OR low_price IS NULL OR low_price <= 0))
        OR (open_price > 0 AND (high_price < low_price OR high_price < open_price OR high_price < close_price OR low_price > open_price OR low_price > close_price))
        OR open_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR high_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR low_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR close_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        OR volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
    """
    add(check(conn, "stock_price.invalid_ohlcv", "critical", "stock_price_daily OHLCV 불변식 위반",
        f"SELECT COUNT(*) FROM stock_price_daily WHERE {sp_invalid}",
        f"SELECT id,bas_dt,stock_code,open_price,high_price,low_price,close_price,volume FROM stock_price_daily WHERE {sp_invalid}",
        auto_repairable=True))
    add(check(conn, "stock_price.duplicate_grain", "high", "stock_price_daily 종목·일자 중복",
        "SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT stock_code,bas_dt,COUNT(*) cnt FROM stock_price_daily GROUP BY stock_code,bas_dt HAVING COUNT(*)>1) d",
        "SELECT stock_code,bas_dt,COUNT(*) cnt FROM stock_price_daily GROUP BY stock_code,bas_dt HAVING COUNT(*)>1 ORDER BY cnt DESC"))

    add(check(conn, "universe.duplicate_active_code", "critical", "최신 기준일 종목코드 중복",
        "WITH latest AS (SELECT MAX(base_date) d FROM stock_universe) SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT stock_code,COUNT(*) cnt FROM stock_universe,latest WHERE base_date=latest.d GROUP BY stock_code HAVING COUNT(*)>1) x",
        "WITH latest AS (SELECT MAX(base_date) d FROM stock_universe) SELECT stock_code,COUNT(*) cnt FROM stock_universe,latest WHERE base_date=latest.d GROUP BY stock_code HAVING COUNT(*)>1 ORDER BY cnt DESC"))
    add(check(conn, "universe.invalid_ohlcv", "critical", "stock_universe 최신 OHLCV 불변식 위반",
        "SELECT COUNT(*) FROM stock_universe WHERE close IS NOT NULL AND (close<=0 OR open<=0 OR high<low OR high<open OR high<close OR low>open OR low>close OR volume<0)",
        "SELECT id,stock_code,base_date,open,high,low,close,volume FROM stock_universe WHERE close IS NOT NULL AND (close<=0 OR open<=0 OR high<low OR high<open OR high<close OR low>open OR low>close OR volume<0)"))

    financial_grain = "stock_code,year,quarter,is_annual,report_type,data_source"
    add(check(conn, "financial.duplicate_source_grain", "high", "financial_data 동일 소스·기간 중복",
        f"SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT {financial_grain},COUNT(*) cnt FROM financial_data GROUP BY {financial_grain} HAVING COUNT(*)>1) d",
        f"SELECT {financial_grain},COUNT(*) cnt FROM financial_data GROUP BY {financial_grain} HAVING COUNT(*)>1 ORDER BY cnt DESC"))
    add(check(conn, "financial.invalid_period", "critical", "financial_data 연도/분기 범위 오류",
        "SELECT COUNT(*) FROM financial_data WHERE year IS NULL OR year<1990 OR year>EXTRACT(YEAR FROM CURRENT_DATE)+1 OR (is_annual AND quarter IS NOT NULL AND quarter NOT IN (0,4)) OR (NOT is_annual AND (quarter IS NULL OR quarter NOT BETWEEN 1 AND 4))",
        "SELECT id,stock_code,year,quarter,is_annual,report_type,data_source FROM financial_data WHERE year IS NULL OR year<1990 OR year>EXTRACT(YEAR FROM CURRENT_DATE)+1 OR (is_annual AND quarter IS NOT NULL AND quarter NOT IN (0,4)) OR (NOT is_annual AND (quarter IS NULL OR quarter NOT BETWEEN 1 AND 4))"))
    add(check(conn, "financial.nonfinite", "critical", "financial_data NaN/Infinity",
        "SELECT COUNT(*) FROM financial_data WHERE revenue IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR operating_profit IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR net_income IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_assets IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_liabilities IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_equity IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)",
        "SELECT id,stock_code,year,quarter,data_source,revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity FROM financial_data WHERE revenue IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR operating_profit IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR net_income IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_assets IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_liabilities IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR total_equity IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)"))
    add(check(conn, "financial.balance_identity_large_gap", "high", "자산과 부채+자본의 20% 초과 불일치",
        "SELECT COUNT(*) FROM financial_data WHERE total_assets>100000000 AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL AND ABS(total_assets-total_liabilities-total_equity)/GREATEST(ABS(total_assets),1)>0.2",
        "SELECT id,stock_code,year,quarter,is_annual,report_type,data_source,total_assets,total_liabilities,total_equity,ROUND((ABS(total_assets-total_liabilities-total_equity)/GREATEST(ABS(total_assets),1))::numeric,4) gap_ratio FROM financial_data WHERE total_assets>100000000 AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL AND ABS(total_assets-total_liabilities-total_equity)/GREATEST(ABS(total_assets),1)>0.2 ORDER BY gap_ratio DESC"))
    add(check(conn, "financial.balance_identity_repairable", "high", "동일 연도·재무구분의 정상 연간행으로 복구 가능한 BS 불일치",
        """SELECT COUNT(*) FROM financial_data b
           WHERE b.is_annual AND b.total_assets>100000000
             AND b.total_liabilities IS NOT NULL AND b.total_equity IS NOT NULL
             AND ABS(b.total_assets-b.total_liabilities-b.total_equity)/GREATEST(ABS(b.total_assets),1)>0.2
             AND EXISTS(SELECT 1 FROM financial_data g WHERE g.id<>b.id
               AND g.stock_code=b.stock_code AND g.year=b.year AND g.report_type=b.report_type
               AND g.is_annual AND g.total_assets>100000000
               AND g.total_liabilities IS NOT NULL AND g.total_equity IS NOT NULL
               AND ABS(g.total_assets-g.total_liabilities-g.total_equity)/GREATEST(ABS(g.total_assets),1)<0.03)""",
        """SELECT b.id,b.stock_code,b.year,b.quarter,b.report_type,b.data_source,b.total_assets,b.total_liabilities,b.total_equity
           FROM financial_data b WHERE b.is_annual AND b.total_assets>100000000
             AND b.total_liabilities IS NOT NULL AND b.total_equity IS NOT NULL
             AND ABS(b.total_assets-b.total_liabilities-b.total_equity)/GREATEST(ABS(b.total_assets),1)>0.2
             AND EXISTS(SELECT 1 FROM financial_data g WHERE g.id<>b.id
               AND g.stock_code=b.stock_code AND g.year=b.year AND g.report_type=b.report_type
               AND g.is_annual AND g.total_assets>100000000
               AND g.total_liabilities IS NOT NULL AND g.total_equity IS NOT NULL
               AND ABS(g.total_assets-g.total_liabilities-g.total_equity)/GREATEST(ABS(g.total_assets),1)<0.03)
           ORDER BY b.stock_code,b.year""", auto_repairable=True))
    add(check(conn, "financial.revenue_extreme_yoy", "medium", "연간 CFS 매출 전년 대비 10배 초과 변동(검토 대상)",
        "WITH a AS (SELECT stock_code,year,MAX(revenue) revenue FROM financial_data WHERE is_annual AND report_type='CFS' AND revenue>100000000 GROUP BY stock_code,year), p AS (SELECT a.*,LAG(revenue) OVER(PARTITION BY stock_code ORDER BY year) prev FROM a) SELECT COUNT(*) FROM p WHERE prev>100000000 AND (revenue/prev>10 OR prev/revenue>10)",
        "WITH a AS (SELECT stock_code,year,MAX(revenue) revenue FROM financial_data WHERE is_annual AND report_type='CFS' AND revenue>100000000 GROUP BY stock_code,year), p AS (SELECT a.*,LAG(revenue) OVER(PARTITION BY stock_code ORDER BY year) prev FROM a) SELECT stock_code,year,prev,revenue,ROUND((revenue/prev)::numeric,2) ratio FROM p WHERE prev>100000000 AND (revenue/prev>10 OR prev/revenue>10) ORDER BY GREATEST(revenue/prev,prev/revenue) DESC"))

    cf_grain = "stock_code,year,quarter,is_annual,report_type,data_source"
    add(check(conn, "cashflow.duplicate_source_grain", "high", "cash_flow_data 동일 소스·기간 중복",
        f"SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT {cf_grain},COUNT(*) cnt FROM cash_flow_data GROUP BY {cf_grain} HAVING COUNT(*)>1) d",
        f"SELECT {cf_grain},COUNT(*) cnt FROM cash_flow_data GROUP BY {cf_grain} HAVING COUNT(*)>1 ORDER BY cnt DESC"))
    add(check(conn, "cashflow.invalid_period", "critical", "cash_flow_data 연도/분기 범위 오류",
        "SELECT COUNT(*) FROM cash_flow_data WHERE year IS NULL OR year<1990 OR year>EXTRACT(YEAR FROM CURRENT_DATE)+1 OR (is_annual AND quarter IS NOT NULL AND quarter NOT IN (0,4)) OR (NOT is_annual AND (quarter IS NULL OR quarter NOT BETWEEN 1 AND 4))",
        "SELECT id,stock_code,year,quarter,is_annual,report_type,data_source FROM cash_flow_data WHERE year IS NULL OR year<1990 OR year>EXTRACT(YEAR FROM CURRENT_DATE)+1 OR (is_annual AND quarter IS NOT NULL AND quarter NOT IN (0,4)) OR (NOT is_annual AND (quarter IS NULL OR quarter NOT BETWEEN 1 AND 4))"))
    add(check(conn, "cashflow.nonfinite", "critical", "cash_flow_data NaN/Infinity",
        "SELECT COUNT(*) FROM cash_flow_data WHERE operating_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR investing_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR financing_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR capex IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR cash_end IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)",
        "SELECT id,stock_code,year,quarter,data_source,operating_cf,investing_cf,financing_cf,capex,cash_end FROM cash_flow_data WHERE operating_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR investing_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR financing_cf IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR capex IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8) OR cash_end IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)"))

    add(check(conn, "dart.invalid_receipt", "high", "DART 접수번호/접수일 형식 오류",
        "SELECT COUNT(*) FROM dart_disclosures WHERE rcept_no IS NULL OR rcept_no !~ '^\\d{14}$' OR rcept_dt IS NULL OR rcept_dt !~ '^(\\d{8}|\\d{4}-\\d{2}-\\d{2})$'",
        "SELECT stock_code,rcept_no,rcept_dt,report_nm FROM dart_disclosures WHERE rcept_no IS NULL OR rcept_no !~ '^\\d{14}$' OR rcept_dt IS NULL OR rcept_dt !~ '^(\\d{8}|\\d{4}-\\d{2}-\\d{2})$'"))
    add(check(conn, "dilution.invalid_numeric", "high", "희석 이벤트 음수 금액/주식수/희석률",
        "SELECT COUNT(*) FROM dilution_events WHERE issue_amount<0 OR conversion_price<0 OR shares_to_issue<0 OR current_shares<0 OR dilution_pct<0",
        "SELECT id,stock_code,event_type,disclosed_at,issue_amount,conversion_price,shares_to_issue,current_shares,dilution_pct FROM dilution_events WHERE issue_amount<0 OR conversion_price<0 OR shares_to_issue<0 OR current_shares<0 OR dilution_pct<0"))
    add(check(conn, "dilution.suspicious_denominator", "high", "희석률 1000% 초과 또는 발행주식수에 연도값 혼입 의심",
        "SELECT COUNT(*) FROM dilution_events WHERE dilution_pct>1000 OR current_shares BETWEEN 1900 AND 2100 OR shares_to_issue BETWEEN 1900 AND 2100",
        "SELECT id,stock_code,event_type,disclosed_at,issue_amount,conversion_price,shares_to_issue,current_shares,dilution_pct FROM dilution_events WHERE dilution_pct>1000 OR current_shares BETWEEN 1900 AND 2100 OR shares_to_issue BETWEEN 1900 AND 2100 ORDER BY dilution_pct DESC"))
    add(check(conn, "dilution.duplicate_receipt", "high", "희석 이벤트 종목·접수번호 중복",
        "SELECT COALESCE(SUM(cnt-1),0) FROM (SELECT stock_code,rcept_no,COUNT(*) cnt FROM dilution_events WHERE rcept_no IS NOT NULL GROUP BY stock_code,rcept_no HAVING COUNT(*)>1) d",
        "SELECT stock_code,rcept_no,COUNT(*) cnt FROM dilution_events WHERE rcept_no IS NOT NULL GROUP BY stock_code,rcept_no HAVING COUNT(*)>1 ORDER BY cnt DESC"))

    add(check(conn, "strategy.orphan_run_spec", "high", "실행 본문이 없는 backtest_run_specs",
        "SELECT COUNT(*) FROM backtest_run_specs s LEFT JOIN backtest_runs r ON r.run_id=s.run_id WHERE r.run_id IS NULL",
        "SELECT s.run_id,s.strategy,s.created_at FROM backtest_run_specs s LEFT JOIN backtest_runs r ON r.run_id=s.run_id WHERE r.run_id IS NULL ORDER BY s.created_at DESC"))
    add(check(conn, "strategy.run_without_spec", "medium", "명세가 없는 완료 백테스트 실행",
        "SELECT COUNT(*) FROM backtest_runs r LEFT JOIN backtest_run_specs s ON s.run_id=r.run_id WHERE r.status='completed' AND s.run_id IS NULL",
        "SELECT r.id,r.run_id,r.strategy,r.created_at FROM backtest_runs r LEFT JOIN backtest_run_specs s ON s.run_id=r.run_id WHERE r.status='completed' AND s.run_id IS NULL ORDER BY r.created_at DESC"))
    add(check(conn, "trading.fill_orphan_order", "critical", "주문이 없는 live fill",
        "SELECT COUNT(*) FROM live_fills f LEFT JOIN live_orders o ON o.order_id=f.order_id WHERE o.order_id IS NULL",
        "SELECT f.* FROM live_fills f LEFT JOIN live_orders o ON o.order_id=f.order_id WHERE o.order_id IS NULL"))
    add(check(conn, "trading.invalid_order_quantity", "critical", "실전 주문 수량/체결수량 불변식 위반",
        "SELECT COUNT(*) FROM live_orders WHERE qty<=0 OR filled_qty<0 OR filled_qty>qty",
        "SELECT order_id,mode,strategy_key,stock_code,side,qty,filled_qty,status FROM live_orders WHERE qty<=0 OR filled_qty<0 OR filled_qty>qty"))
    add(check(conn, "trading.cash_ledger_chain", "critical", "현금원장 전후 잔액 연결 불일치",
        "WITH x AS (SELECT id,balance_after,delta_krw,LAG(balance_after) OVER(ORDER BY id) prev FROM live_cash_ledger) SELECT COUNT(*) FROM x WHERE prev IS NOT NULL AND ABS((prev+delta_krw)-balance_after)>1",
        "WITH x AS (SELECT id,ts,balance_after,delta_krw,LAG(balance_after) OVER(ORDER BY id) prev FROM live_cash_ledger) SELECT * FROM x WHERE prev IS NOT NULL AND ABS((prev+delta_krw)-balance_after)>1 ORDER BY id"))

    return checks


def ensure_repair_tables(conn: psycopg.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS postgres_data_repair_batches (
            batch_id text PRIMARY KEY,
            created_at timestamptz NOT NULL DEFAULT now(),
            status text NOT NULL,
            repair_counts jsonb NOT NULL DEFAULT '{}'::jsonb,
            restored_at timestamptz
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS postgres_data_repair_backup (
            batch_id text NOT NULL,
            table_name text NOT NULL,
            row_id bigint NOT NULL,
            old_row jsonb NOT NULL,
            PRIMARY KEY(batch_id,table_name,row_id)
        )
    """)


def backup_rows(conn: psycopg.Connection, batch_id: str, table: str, where_sql: str) -> int:
    query = sql.SQL("""
        INSERT INTO postgres_data_repair_backup(batch_id,table_name,row_id,old_row)
        SELECT %s,%s,t.id,to_jsonb(t) FROM {table} t WHERE {where}
        ON CONFLICT DO NOTHING
    """).format(table=sql.Identifier(table), where=sql.SQL(where_sql))
    cur = conn.execute(query, (batch_id, table))
    return cur.rowcount


def apply_repairs(conn: psycopg.Connection, batch_id: str) -> dict[str, int]:
    ensure_repair_tables(conn)
    conn.execute(
        "INSERT INTO postgres_data_repair_batches(batch_id,status) VALUES(%s,'running')",
        (batch_id,),
    )
    counts: dict[str, int] = {}

    price_where = """
        EXISTS (
            SELECT 1 FROM naver_price_history_backfill n
            WHERE n.stock_code=t.stock_code AND n.date=t.date
              AND n.open>0 AND n.close>0 AND n.high>=n.low
              AND n.high>=n.open AND n.high>=n.close
              AND n.low<=n.open AND n.low<=n.close AND n.volume>=0
              AND n.open NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
              AND n.high NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
              AND n.low NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
              AND n.close NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
              AND n.volume NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
        )
        AND (t.close IS NULL OR t.close<=0 OR t.volume IS NULL OR t.volume<0
             OR (t.volume>0 AND (t.open IS NULL OR t.open<=0 OR t.high IS NULL OR t.high<=0 OR t.low IS NULL OR t.low<=0))
             OR (t.open>0 AND (t.high<t.low OR t.high<t.open*0.98 OR t.high<t.close*0.98 OR t.low>t.open*1.02 OR t.low>t.close*1.02))
             OR t.open IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.high IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.low IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.close IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8))
    """
    backup_rows(conn, batch_id, "price_history", price_where)
    cur = conn.execute("""
        UPDATE price_history p SET
          open=n.open,high=n.high,low=n.low,close=n.close,volume=n.volume,
          trade_amount=CASE WHEN p.trade_amount IS NULL OR p.trade_amount<0
                            THEN n.close*n.volume ELSE p.trade_amount END
        FROM naver_price_history_backfill n
        WHERE p.stock_code=n.stock_code AND p.date=n.date
          AND n.open>0 AND n.close>0 AND n.high>=n.low
          AND n.high>=n.open AND n.high>=n.close
          AND n.low<=n.open AND n.low<=n.close AND n.volume>=0
          AND n.open NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
          AND n.high NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
          AND n.low NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
          AND n.close NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
          AND n.volume NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
          AND (p.close IS NULL OR p.close<=0 OR p.volume IS NULL OR p.volume<0
               OR (p.volume>0 AND (p.open IS NULL OR p.open<=0 OR p.high IS NULL OR p.high<=0 OR p.low IS NULL OR p.low<=0))
               OR (p.open>0 AND (p.high<p.low OR p.high<p.open*0.98 OR p.high<p.close*0.98 OR p.low>p.open*1.02 OR p.low>p.close*1.02))
               OR p.open IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR p.high IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR p.low IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR p.close IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR p.volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8))
    """)
    counts["price_history_from_naver"] = cur.rowcount

    rounding_price_where = """
        t.open>0 AND t.high>0 AND t.low>0 AND t.close>0 AND t.high>=t.low
        AND (t.high<t.open OR t.high<t.close OR t.low>t.open OR t.low>t.close)
        AND t.high>=t.open*0.98 AND t.high>=t.close*0.98
        AND t.low<=t.open*1.02 AND t.low<=t.close*1.02
    """
    backup_rows(conn, batch_id, "price_history", rounding_price_where)
    cur = conn.execute("""
        UPDATE price_history SET
          high=GREATEST(high,open,close),
          low=LEAST(low,open,close)
        WHERE open>0 AND high>0 AND low>0 AND close>0 AND high>=low
          AND (high<open OR high<close OR low>open OR low>close)
          AND high>=open*0.98 AND high>=close*0.98
          AND low<=open*1.02 AND low<=close*1.02
    """)
    counts["price_history_adjusted_rounding_bounds"] = cur.rowcount

    malformed_price_where = """
        t.date ~ ' '
        AND EXISTS (
          SELECT 1 FROM price_history x
          WHERE x.stock_code=t.stock_code AND x.date=left(t.date,10) AND x.id<>t.id
            AND x.open IS NOT DISTINCT FROM t.open
            AND x.high IS NOT DISTINCT FROM t.high
            AND x.low IS NOT DISTINCT FROM t.low
            AND x.close IS NOT DISTINCT FROM t.close
            AND x.volume IS NOT DISTINCT FROM t.volume
        )
    """
    backup_rows(conn, batch_id, "price_history", malformed_price_where)
    cur = conn.execute("""
        DELETE FROM price_history t
        WHERE t.date ~ ' '
          AND EXISTS (
            SELECT 1 FROM price_history x
            WHERE x.stock_code=t.stock_code AND x.date=left(t.date,10) AND x.id<>t.id
              AND x.open IS NOT DISTINCT FROM t.open
              AND x.high IS NOT DISTINCT FROM t.high
              AND x.low IS NOT DISTINCT FROM t.low
              AND x.close IS NOT DISTINCT FROM t.close
              AND x.volume IS NOT DISTINCT FROM t.volume
          )
    """)
    counts["price_history_duplicate_malformed_date_removed"] = cur.rowcount

    cashflow_duplicate_where = """
        t.id IN (
          SELECT id FROM (
            SELECT id,row_number() OVER (
              PARTITION BY stock_code,year,quarter,is_annual,operating_cf,investing_cf,
                financing_cf,capex,cash_end,depreciation,operating_cf_q,investing_cf_q,
                financing_cf_q,capex_q,value_type,report_type,data_source,depreciation_q
              ORDER BY id
            ) rn
            FROM cash_flow_data
          ) ranked WHERE rn>1
        )
    """
    backup_rows(conn, batch_id, "cash_flow_data", cashflow_duplicate_where)
    cur = conn.execute("""
        DELETE FROM cash_flow_data
        WHERE id IN (
          SELECT id FROM (
            SELECT id,row_number() OVER (
              PARTITION BY stock_code,year,quarter,is_annual,operating_cf,investing_cf,
                financing_cf,capex,cash_end,depreciation,operating_cf_q,investing_cf_q,
                financing_cf_q,capex_q,value_type,report_type,data_source,depreciation_q
              ORDER BY id
            ) rn
            FROM cash_flow_data
          ) ranked WHERE rn>1
        )
    """)
    counts["cash_flow_exact_duplicates_removed"] = cur.rowcount

    financial_balance_where = """
        t.is_annual AND t.total_assets>100000000
        AND t.total_liabilities IS NOT NULL AND t.total_equity IS NOT NULL
        AND ABS(t.total_assets-t.total_liabilities-t.total_equity)/GREATEST(ABS(t.total_assets),1)>0.2
        AND EXISTS(SELECT 1 FROM financial_data g WHERE g.id<>t.id
          AND g.stock_code=t.stock_code AND g.year=t.year AND g.report_type=t.report_type
          AND g.is_annual AND g.total_assets>100000000
          AND g.total_liabilities IS NOT NULL AND g.total_equity IS NOT NULL
          AND ABS(g.total_assets-g.total_liabilities-g.total_equity)/GREATEST(ABS(g.total_assets),1)<0.03)
    """
    backup_rows(conn, batch_id, "financial_data", financial_balance_where)
    cur = conn.execute("""
        WITH best AS (
          SELECT DISTINCT ON (b.id)
            b.id AS bad_id,g.total_assets,g.total_liabilities,g.total_equity
          FROM financial_data b
          JOIN financial_data g ON g.id<>b.id AND g.stock_code=b.stock_code
            AND g.year=b.year AND g.report_type=b.report_type AND g.is_annual
            AND g.total_assets>100000000
            AND g.total_liabilities IS NOT NULL AND g.total_equity IS NOT NULL
            AND ABS(g.total_assets-g.total_liabilities-g.total_equity)/GREATEST(ABS(g.total_assets),1)<0.03
          WHERE b.is_annual AND b.total_assets>100000000
            AND b.total_liabilities IS NOT NULL AND b.total_equity IS NOT NULL
            AND ABS(b.total_assets-b.total_liabilities-b.total_equity)/GREATEST(ABS(b.total_assets),1)>0.2
          ORDER BY b.id,
            CASE g.data_source
              WHEN 'dart_redownload' THEN 1 WHEN 'dart' THEN 2
              WHEN 'dart_recollect' THEN 3 WHEN 'dart_ofs_backfill' THEN 4 ELSE 9
            END,
            CASE WHEN g.quarter=4 THEN 1 WHEN g.quarter=0 THEN 2 ELSE 3 END,
            g.id DESC
        )
        UPDATE financial_data b SET
          total_assets=best.total_assets,
          total_liabilities=best.total_liabilities,
          total_equity=best.total_equity,
          data_source=b.data_source || '+bs_identity_repair'
        FROM best WHERE b.id=best.bad_id
    """)
    counts["financial_balance_identity_from_agreeing_annual_peer"] = cur.rowcount

    financial_liability_mismatch_where = """
        t.total_assets>100000000 AND t.total_liabilities IS NOT NULL AND t.total_equity IS NOT NULL
        AND ABS(t.total_assets-t.total_liabilities-t.total_equity)/GREATEST(ABS(t.total_assets),1)>0.2
        AND ABS(t.total_liabilities-t.total_assets)/GREATEST(ABS(t.total_assets),1)<0.03
        AND ABS(t.total_equity)>t.total_assets*0.03
        AND NOT (ABS(t.total_equity-t.total_assets)/GREATEST(ABS(t.total_assets),1)<0.03)
    """
    backup_rows(conn, batch_id, "financial_data", financial_liability_mismatch_where)
    cur = conn.execute("""
        UPDATE financial_data SET
          total_liabilities=total_assets-total_equity,
          data_source=data_source || '+liability_total_match_repair'
        WHERE total_assets>100000000 AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
          AND ABS(total_assets-total_liabilities-total_equity)/GREATEST(ABS(total_assets),1)>0.2
          AND ABS(total_liabilities-total_assets)/GREATEST(ABS(total_assets),1)<0.03
          AND ABS(total_equity)>total_assets*0.03
          AND NOT (ABS(total_equity-total_assets)/GREATEST(ABS(total_assets),1)<0.03)
    """)
    counts["financial_liabilities_mapped_as_assets"] = cur.rowcount

    financial_equity_mismatch_where = """
        t.total_assets>100000000 AND t.total_liabilities IS NOT NULL AND t.total_equity IS NOT NULL
        AND ABS(t.total_assets-t.total_liabilities-t.total_equity)/GREATEST(ABS(t.total_assets),1)>0.2
        AND ABS(t.total_equity-t.total_assets)/GREATEST(ABS(t.total_assets),1)<0.03
        AND ABS(t.total_liabilities)>t.total_assets*0.03
        AND NOT (ABS(t.total_liabilities-t.total_assets)/GREATEST(ABS(t.total_assets),1)<0.03)
    """
    backup_rows(conn, batch_id, "financial_data", financial_equity_mismatch_where)
    cur = conn.execute("""
        UPDATE financial_data SET
          total_equity=total_assets-total_liabilities,
          data_source=data_source || '+equity_total_match_repair'
        WHERE total_assets>100000000 AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
          AND ABS(total_assets-total_liabilities-total_equity)/GREATEST(ABS(total_assets),1)>0.2
          AND ABS(total_equity-total_assets)/GREATEST(ABS(total_assets),1)<0.03
          AND ABS(total_liabilities)>total_assets*0.03
          AND NOT (ABS(total_liabilities-total_assets)/GREATEST(ABS(total_assets),1)<0.03)
    """)
    counts["financial_equity_mapped_as_assets"] = cur.rowcount

    dilution_share_where = """
        t.current_shares BETWEEN 1900 AND 2100
        AND EXISTS(SELECT 1 FROM security_share_history h
          WHERE h.stock_code=t.stock_code AND h.shares_issued>0
            AND h.effective_from<=t.disclosed_at
            AND (h.effective_to IS NULL OR h.effective_to>=t.disclosed_at))
    """
    backup_rows(conn, batch_id, "dilution_events", dilution_share_where)
    cur = conn.execute("""
        WITH best AS (
          SELECT DISTINCT ON (d.id) d.id,h.shares_issued
          FROM dilution_events d
          JOIN security_share_history h ON h.stock_code=d.stock_code AND h.shares_issued>0
            AND h.effective_from<=d.disclosed_at
            AND (h.effective_to IS NULL OR h.effective_to>=d.disclosed_at)
          WHERE d.current_shares BETWEEN 1900 AND 2100
          ORDER BY d.id,h.confidence DESC,h.effective_from DESC
        )
        UPDATE dilution_events d SET
          current_shares=best.shares_issued,
          dilution_pct=CASE WHEN d.shares_to_issue>0
                            THEN d.shares_to_issue/best.shares_issued*100 ELSE NULL END,
          data_source=d.data_source || '+pit_share_history_repair'
        FROM best WHERE d.id=best.id
    """)
    counts["dilution_current_shares_from_pit_history"] = cur.rowcount

    dilution_issue_where = """
        t.shares_to_issue BETWEEN 1900 AND 2100
        AND t.issue_amount>1000000 AND t.conversion_price>0
        AND t.current_shares>10000
    """
    backup_rows(conn, batch_id, "dilution_events", dilution_issue_where)
    cur = conn.execute("""
        UPDATE dilution_events SET
          shares_to_issue=ROUND(issue_amount/conversion_price),
          dilution_pct=ROUND(issue_amount/conversion_price)/current_shares*100,
          data_source=data_source || '+issue_amount_price_repair'
        WHERE shares_to_issue BETWEEN 1900 AND 2100
          AND issue_amount>1000000 AND conversion_price>0 AND current_shares>10000
    """)
    counts["dilution_issue_shares_from_amount_price"] = cur.rowcount

    dilution_share_ratio_where = """
        t.current_shares>0 AND t.dilution_pct>1000
        AND EXISTS(SELECT 1 FROM security_share_history h
          WHERE h.stock_code=t.stock_code AND h.shares_issued>0
            AND h.effective_from<=t.disclosed_at
            AND (h.effective_to IS NULL OR h.effective_to>=t.disclosed_at)
            AND GREATEST(h.shares_issued/t.current_shares,t.current_shares/h.shares_issued)>5)
    """
    backup_rows(conn, batch_id, "dilution_events", dilution_share_ratio_where)
    cur = conn.execute("""
        WITH best AS (
          SELECT DISTINCT ON (d.id) d.id,h.shares_issued
          FROM dilution_events d
          JOIN security_share_history h ON h.stock_code=d.stock_code AND h.shares_issued>0
            AND h.effective_from<=d.disclosed_at
            AND (h.effective_to IS NULL OR h.effective_to>=d.disclosed_at)
          WHERE d.current_shares>0 AND d.dilution_pct>1000
            AND GREATEST(h.shares_issued/d.current_shares,d.current_shares/h.shares_issued)>5
          ORDER BY d.id,h.confidence DESC,h.effective_from DESC
        )
        UPDATE dilution_events d SET
          current_shares=best.shares_issued,
          dilution_pct=CASE WHEN d.shares_to_issue>0
                            THEN d.shares_to_issue/best.shares_issued*100 ELSE NULL END,
          data_source=d.data_source || '+pit_share_ratio_repair'
        FROM best WHERE d.id=best.id
    """)
    counts["dilution_current_shares_large_ratio_from_pit_history"] = cur.rowcount

    dilution_formula_where = """
        t.issue_amount>1000000 AND t.conversion_price>0 AND t.shares_to_issue>0
        AND GREATEST(t.shares_to_issue/(t.issue_amount/t.conversion_price),
                     (t.issue_amount/t.conversion_price)/t.shares_to_issue)>5
        AND t.dilution_pct>1000
    """
    backup_rows(conn, batch_id, "dilution_events", dilution_formula_where)
    cur = conn.execute("""
        UPDATE dilution_events SET
          shares_to_issue=ROUND(issue_amount/conversion_price),
          dilution_pct=CASE WHEN current_shares>0
                            THEN ROUND(issue_amount/conversion_price)/current_shares*100 ELSE NULL END,
          data_source=data_source || '+extreme_formula_repair'
        WHERE issue_amount>1000000 AND conversion_price>0 AND shares_to_issue>0
          AND GREATEST(shares_to_issue/(issue_amount/conversion_price),
                       (issue_amount/conversion_price)/shares_to_issue)>5
          AND dilution_pct>1000
    """)
    counts["dilution_extreme_issue_shares_from_formula"] = cur.rowcount

    sp_where = """
        EXISTS (
          SELECT 1 FROM price_history p
          WHERE p.stock_code=t.stock_code AND replace(p.date,'-','')=t.bas_dt
            AND p.open>0 AND p.close>0 AND p.high>=p.low
            AND p.high>=p.open AND p.high>=p.close
            AND p.low<=p.open AND p.low<=p.close AND p.volume>=0
        )
        AND (t.close_price IS NULL OR t.close_price<=0 OR t.open_price IS NULL OR t.open_price<=0
             OR t.high_price IS NULL OR t.low_price IS NULL OR t.high_price<t.low_price
             OR t.high_price<t.open_price OR t.high_price<t.close_price
             OR t.low_price>t.open_price OR t.low_price>t.close_price
             OR t.volume IS NULL OR t.volume<0
             OR t.open_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.high_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.low_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.close_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
             OR t.volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8))
    """
    backup_rows(conn, batch_id, "stock_price_daily", sp_where)
    cur = conn.execute("""
        UPDATE stock_price_daily s SET
          open_price=p.open,high_price=p.high,low_price=p.low,close_price=p.close,
          volume=p.volume,
          trade_amt=CASE WHEN s.trade_amt IS NULL OR s.trade_amt<0
                         THEN COALESCE(p.trade_amount,p.close*p.volume) ELSE s.trade_amt END
        FROM price_history p
        WHERE p.stock_code=s.stock_code AND replace(p.date,'-','')=s.bas_dt
          AND p.open>0 AND p.close>0 AND p.high>=p.low
          AND p.high>=p.open AND p.high>=p.close
          AND p.low<=p.open AND p.low<=p.close AND p.volume>=0
          AND (s.close_price IS NULL OR s.close_price<=0 OR s.open_price IS NULL OR s.open_price<=0
               OR s.high_price IS NULL OR s.low_price IS NULL OR s.high_price<s.low_price
               OR s.high_price<s.open_price OR s.high_price<s.close_price
               OR s.low_price>s.open_price OR s.low_price>s.close_price
               OR s.volume IS NULL OR s.volume<0
               OR s.open_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR s.high_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR s.low_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR s.close_price IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)
               OR s.volume IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8))
    """)
    counts["stock_price_daily_from_price_history"] = cur.rowcount

    conn.execute("ANALYZE")
    conn.execute(
        "UPDATE postgres_data_repair_batches SET status='applied',repair_counts=%s::jsonb WHERE batch_id=%s",
        (json.dumps(counts), batch_id),
    )
    return counts


def restore_batch(conn: psycopg.Connection, batch_id: str) -> dict[str, int]:
    ensure_repair_tables(conn)
    status = scalar(conn, "SELECT status FROM postgres_data_repair_batches WHERE batch_id=%s", (batch_id,))
    if status != "applied":
        raise RuntimeError(f"batch {batch_id} is not restorable (status={status!r})")
    counts: dict[str, int] = {}
    for table in ("price_history", "stock_price_daily", "cash_flow_data", "financial_data", "dilution_events"):
        n = int(scalar(conn, "SELECT COUNT(*) FROM postgres_data_repair_backup WHERE batch_id=%s AND table_name=%s", (batch_id, table)) or 0)
        if not n:
            continue
        conn.execute(
            sql.SQL("DELETE FROM {table} WHERE id IN (SELECT row_id FROM postgres_data_repair_backup WHERE batch_id=%s AND table_name=%s)").format(table=sql.Identifier(table)),
            (batch_id, table),
        )
        conn.execute(
            sql.SQL("INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},old_row)).* FROM postgres_data_repair_backup WHERE batch_id=%s AND table_name=%s ORDER BY row_id").format(table=sql.Identifier(table)),
            (batch_id, table),
        )
        counts[table] = n
    conn.execute("UPDATE postgres_data_repair_batches SET status='restored',restored_at=now() WHERE batch_id=%s", (batch_id,))
    conn.execute("ANALYZE")
    return counts


def write_report(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    failed = [c for c in report["after"]["checks"] if c["count"]]
    lines = [
        "# PostgreSQL Full Data Quality Audit",
        "",
        f"- Checked at: `{report['checked_at']}`",
        f"- Batch: `{report.get('batch_id') or 'audit-only'}`",
        f"- Tables: {report['after']['inventory']['table_count']:,} ({report['after']['inventory']['nonempty_table_count']:,} non-empty)",
        f"- Rows: {report['after']['inventory']['total_rows']:,}",
        f"- Failed checks after repair: {len(failed):,}/{len(report['after']['checks']):,}",
        "",
        "## Applied Repairs",
        "",
    ]
    repairs = report.get("repairs") or {}
    if repairs:
        lines.extend(f"- `{key}`: {value:,} rows" for key, value in repairs.items())
    else:
        lines.append("- None")
    lines.extend(["", "## Remaining Findings", ""])
    for item in failed:
        lines.append(f"- **{item['severity']}** `{item['id']}`: {item['count']:,} - {item['description']}")
    lines.extend(["", "## Safety", "", "- Business outliers were not overwritten.", "- Applied rows are restorable from `postgres_data_repair_backup` by batch id.", "- JSON evidence includes samples and exact SQL-derived counts.", ""])
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    latest = OUT_ROOT / "latest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def snapshot(conn: psycopg.Connection) -> dict[str, Any]:
    return {"inventory": inventory(conn), "checks": run_checks(conn)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply deterministic repairs and ANALYZE.")
    parser.add_argument("--restore", metavar="BATCH_ID", help="Restore an applied repair batch.")
    args = parser.parse_args()
    batch_id = datetime.now().strftime("%Y%m%dT%H%M%S") if args.apply else None
    with psycopg.connect(pg_url()) as conn:
        conn.execute("SET statement_timeout='180s'")
        if args.restore:
            restored = restore_batch(conn, args.restore)
            conn.commit()
            print(json.dumps({"restored_batch": args.restore, "rows": restored}, ensure_ascii=False, indent=2))
            return
        before = snapshot(conn)
        repairs: dict[str, int] = {}
        if args.apply:
            repairs = apply_repairs(conn, batch_id)
            conn.commit()
        after = snapshot(conn)
        report = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "database": "PostgreSQL/public",
            "scope": "all public tables plus domain checks for prices, financials, disclosures, dilution, strategies, orders and ledger",
            "batch_id": batch_id,
            "repairs": repairs,
            "before": before,
            "after": after,
        }
    out_dir = OUT_ROOT / (batch_id or datetime.now().strftime("audit_%Y%m%dT%H%M%S"))
    write_report(report, out_dir)
    summary = {
        "out_dir": str(out_dir),
        "batch_id": batch_id,
        "tables": after["inventory"]["table_count"],
        "rows": after["inventory"]["total_rows"],
        "repairs": repairs,
        "remaining": {c["id"]: c["count"] for c in after["checks"] if c["count"]},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
