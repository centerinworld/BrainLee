#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


DB = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
OUT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/remaining_data_error_fix_20260626")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=120000")
    return c


def scalar(c: sqlite3.Connection, sql: str) -> int:
    return int(c.execute(sql).fetchone()[0] or 0)


def log(c: sqlite3.Connection, run_id: str, table: str, repair: str, affected: int, backup: str | None) -> None:
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS data_quality_repair_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            repair_name TEXT NOT NULL,
            affected_rows INTEGER NOT NULL,
            backup_table TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    c.execute(
        """
        INSERT INTO data_quality_repair_log(run_id, table_name, repair_name, affected_rows, backup_table)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, table, repair, affected, backup),
    )


def backup_table(c: sqlite3.Connection, name: str, sql: str) -> int:
    c.execute(f"DROP TABLE IF EXISTS {name}")
    c.execute(f"CREATE TABLE {name} AS {sql}")
    return scalar(c, f"SELECT COUNT(*) FROM {name}")


def count_core(c: sqlite3.Connection) -> dict[str, int]:
    return {
        "financial_q_rev_eq_annual": scalar(
            c,
            """
            SELECT COUNT(*) FROM (
              WITH a AS (
                SELECT stock_code, year, COALESCE(report_type,'CFS') rt, revenue a_rev
                FROM financial_data
                WHERE is_annual=1 AND revenue IS NOT NULL
              ), q AS (
                SELECT id, stock_code, year, quarter, COALESCE(report_type,'CFS') rt, revenue q_rev
                FROM financial_data
                WHERE is_annual=0 AND quarter IN (1,2,3) AND revenue IS NOT NULL
              )
              SELECT q.id
              FROM q JOIN a ON a.stock_code=q.stock_code AND a.year=q.year AND a.rt=q.rt
              WHERE q.q_rev=a.a_rev AND q.q_rev>0
            )
            """,
        ),
        "cashflow_q_eq_annual": scalar(
            c,
            """
            SELECT COUNT(*) FROM (
              WITH a AS (
                SELECT stock_code, year, COALESCE(report_type,'CFS') rt,
                       operating_cf a_op, investing_cf a_inv, financing_cf a_fin, capex a_cap
                FROM cash_flow_data
                WHERE is_annual=1
              ), q AS (
                SELECT id, stock_code, year, quarter, COALESCE(report_type,'CFS') rt,
                       operating_cf q_op, investing_cf q_inv, financing_cf q_fin, capex q_cap,
                       operating_cf_q q_op_q, investing_cf_q q_inv_q, financing_cf_q q_fin_q, capex_q q_cap_q
                FROM cash_flow_data
                WHERE is_annual=0 AND quarter IN (1,2,3)
              )
              SELECT q.id
              FROM q JOIN a ON a.stock_code=q.stock_code AND a.year=q.year AND a.rt=q.rt
              WHERE (q.q_op=a.a_op AND ABS(q.q_op)>0)
                 OR (q.q_inv=a.a_inv AND ABS(q.q_inv)>0)
                 OR (q.q_fin=a.a_fin AND ABS(q.q_fin)>0)
                 OR (q.q_cap=a.a_cap AND ABS(q.q_cap)>0)
                 OR (q.q_op_q=a.a_op AND ABS(q.q_op_q)>0)
                 OR (q.q_inv_q=a.a_inv AND ABS(q.q_inv_q)>0)
                 OR (q.q_fin_q=a.a_fin AND ABS(q.q_fin_q)>0)
                 OR (q.q_cap_q=a.a_cap AND ABS(q.q_cap_q)>0)
            )
            """,
        ),
        "kiwoom_bad_market_fields": scalar(
            c,
            """
            SELECT COUNT(*)
            FROM kiwoom_investor_daily
            WHERE close_pric<0 OR acc_trde_qty<0 OR acc_trde_prica<0
               OR close_pric IS NULL OR acc_trde_qty IS NULL OR acc_trde_prica IS NULL
            """,
        ),
        "valuation_internal_mismatch": scalar(
            c,
            """
            WITH latest AS (
              SELECT stock_code, MAX(base_date) md
              FROM stock_universe
              WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              GROUP BY stock_code
            ), p AS (
              SELECT stock_code, close,
                     ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
              FROM price_history
              WHERE close>0
            ), f AS (
              SELECT stock_code, year, eps, bps,
                     ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END, id DESC) rn
              FROM financial_data
              WHERE is_annual=1
            )
            SELECT COUNT(*)
            FROM stock_universe t
            JOIN latest l ON l.stock_code=t.stock_code AND l.md=t.base_date
            LEFT JOIN p ON p.stock_code=t.stock_code AND p.rn=1
            LEFT JOIN f ON f.stock_code=t.stock_code AND f.rn=1
            WHERE p.close IS NOT NULL
              AND (
                (t.per IS NOT NULL AND f.eps>0 AND ABS(t.per-(p.close/f.eps))/ABS(p.close/f.eps) > 0.25)
                OR
                (t.pbr IS NOT NULL AND f.bps>0 AND ABS(t.pbr-(p.close/f.bps))/ABS(p.close/f.bps) > 0.25)
              )
            """,
        ),
        "fnguide_fin_snapshot_gap": scalar(
            c,
            """
            SELECT COUNT(*)
            FROM financial_data fd
            LEFT JOIN financial_source_snapshot fss
              ON fss.stock_code=fd.stock_code AND fss.year=fd.year AND fss.quarter=fd.quarter
             AND fss.is_annual=fd.is_annual AND fss.report_type=fd.report_type AND fss.data_source='fnguide'
            WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
              AND fd.data_source='fnguide' AND fss.stock_code IS NULL
            """,
        ),
        "fnguide_cf_snapshot_gap": scalar(
            c,
            """
            SELECT COUNT(*)
            FROM cash_flow_data cf
            LEFT JOIN financial_source_snapshot fss
              ON fss.stock_code=cf.stock_code AND fss.year=cf.year AND fss.quarter=cf.quarter
             AND fss.is_annual=cf.is_annual AND fss.report_type=cf.report_type AND fss.data_source='fnguide'
            WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
              AND cf.data_source='fnguide' AND fss.stock_code IS NULL
            """,
        ),
    }


def fix_financial_quarter_annual(c: sqlite3.Connection, run_id: str) -> dict:
    backup = f"data_quality_backup_fin_q_eq_annual_{run_id}"
    affected = backup_table(
        c,
        backup,
        """
        WITH a AS (
          SELECT stock_code, year, COALESCE(report_type,'CFS') rt, revenue a_rev
          FROM financial_data
          WHERE is_annual=1 AND revenue IS NOT NULL
        )
        SELECT q.*
        FROM financial_data q
        JOIN a ON a.stock_code=q.stock_code AND a.year=q.year AND a.rt=COALESCE(q.report_type,'CFS')
        WHERE q.is_annual=0 AND q.quarter IN (1,2,3)
          AND q.revenue IS NOT NULL AND q.revenue=a.a_rev AND q.revenue>0
        """,
    )
    if affected:
        c.execute(
            f"""
            UPDATE financial_data
            SET revenue=NULL,
                data_source=COALESCE(data_source,'') || '_qrev_null_eq_annual'
            WHERE id IN (SELECT id FROM {backup})
            """
        )
    log(c, run_id, "financial_data", "null_quarter_revenue_equal_to_annual", affected, backup)
    return {"affected": affected, "backup": backup}


def fix_cashflow_quarter_annual(c: sqlite3.Connection, run_id: str) -> dict:
    backup = f"data_quality_backup_cf_q_eq_annual_{run_id}"
    affected = backup_table(
        c,
        backup,
        """
        WITH a AS (
          SELECT stock_code, year, COALESCE(report_type,'CFS') rt,
                 operating_cf a_op, investing_cf a_inv, financing_cf a_fin, capex a_cap
          FROM cash_flow_data
          WHERE is_annual=1
        )
        SELECT q.*, a.a_op, a.a_inv, a.a_fin, a.a_cap
        FROM cash_flow_data q
        JOIN a ON a.stock_code=q.stock_code AND a.year=q.year AND a.rt=COALESCE(q.report_type,'CFS')
        WHERE q.is_annual=0 AND q.quarter IN (1,2,3)
          AND (
            (q.operating_cf=a.a_op AND ABS(q.operating_cf)>0)
            OR (q.investing_cf=a.a_inv AND ABS(q.investing_cf)>0)
            OR (q.financing_cf=a.a_fin AND ABS(q.financing_cf)>0)
            OR (q.capex=a.a_cap AND ABS(q.capex)>0)
            OR (q.operating_cf_q=a.a_op AND ABS(q.operating_cf_q)>0)
            OR (q.investing_cf_q=a.a_inv AND ABS(q.investing_cf_q)>0)
            OR (q.financing_cf_q=a.a_fin AND ABS(q.financing_cf_q)>0)
            OR (q.capex_q=a.a_cap AND ABS(q.capex_q)>0)
          )
        """,
    )
    if affected:
        c.execute(
            f"""
            UPDATE cash_flow_data
            SET operating_cf=CASE WHEN operating_cf=(SELECT a_op FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE operating_cf END,
                investing_cf=CASE WHEN investing_cf=(SELECT a_inv FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE investing_cf END,
                financing_cf=CASE WHEN financing_cf=(SELECT a_fin FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE financing_cf END,
                capex=CASE WHEN capex=(SELECT a_cap FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE capex END,
                operating_cf_q=CASE WHEN operating_cf_q=(SELECT a_op FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE operating_cf_q END,
                investing_cf_q=CASE WHEN investing_cf_q=(SELECT a_inv FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE investing_cf_q END,
                financing_cf_q=CASE WHEN financing_cf_q=(SELECT a_fin FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE financing_cf_q END,
                capex_q=CASE WHEN capex_q=(SELECT a_cap FROM {backup} b WHERE b.id=cash_flow_data.id) THEN NULL ELSE capex_q END,
                data_source=COALESCE(data_source,'') || '_qcf_null_eq_annual'
            WHERE id IN (SELECT id FROM {backup})
            """
        )
    log(c, run_id, "cash_flow_data", "null_quarter_cashflow_fields_equal_to_annual", affected, backup)
    return {"affected": affected, "backup": backup}


def fix_kiwoom_market_fields(c: sqlite3.Connection, run_id: str) -> dict:
    c.execute("CREATE INDEX IF NOT EXISTS idx_price_history_code_date_fix ON price_history(stock_code, date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_inv_code_dt_fix ON kiwoom_investor_daily(stock_code, dt)")
    c.execute("DROP TABLE IF EXISTS temp.tmp_kiwoom_bad")
    c.execute(
        """
        CREATE TEMP TABLE tmp_kiwoom_bad AS
        SELECT stock_code, dt, close_pric, acc_trde_qty, acc_trde_prica
        FROM kiwoom_investor_daily
        WHERE close_pric<0 OR acc_trde_qty<0 OR acc_trde_prica<0
           OR close_pric IS NULL OR acc_trde_qty IS NULL OR acc_trde_prica IS NULL
        """
    )
    c.execute("CREATE INDEX idx_tmp_kiwoom_bad_key ON tmp_kiwoom_bad(stock_code, dt)")
    c.execute("DROP TABLE IF EXISTS temp.tmp_price_key")
    c.execute(
        """
        CREATE TEMP TABLE tmp_price_key AS
        SELECT ph.stock_code,
               replace(substr(ph.date,1,10),'-','') dt,
               MAX(ph.close) close,
               MAX(ph.volume) volume,
               MAX(ph.trade_amount) trade_amount
        FROM price_history ph
        WHERE ph.stock_code IN (SELECT DISTINCT stock_code FROM tmp_kiwoom_bad)
        GROUP BY ph.stock_code, replace(substr(ph.date,1,10),'-','')
        """
    )
    c.execute("CREATE INDEX idx_tmp_price_key ON tmp_price_key(stock_code, dt)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_stock_price_daily_code_dt_fix ON stock_price_daily(stock_code, bas_dt)")
    backup = f"data_quality_backup_kiwoom_market_fields_{run_id}"
    affected = backup_table(
        c,
        backup,
        """
        SELECT k.stock_code, k.dt,
               k.close_pric old_close_pric, k.acc_trde_qty old_acc_trde_qty, k.acc_trde_prica old_acc_trde_prica,
               ph.close ph_close, ph.volume ph_volume, ph.trade_amount ph_trade_amount,
               sp.close_price sp_close, sp.volume sp_volume, sp.trade_amt sp_trade_amt,
               COALESCE(NULLIF(ph.close,0), NULLIF(sp.close_price,0), 0) new_close_pric,
               COALESCE(ph.volume, sp.volume, 0) new_acc_trde_qty,
               COALESCE(NULLIF(ph.trade_amount,0), NULLIF(sp.trade_amt,0), ph.close * ph.volume, sp.close_price * sp.volume, 0) new_acc_trde_prica
        FROM tmp_kiwoom_bad k
        LEFT JOIN tmp_price_key ph
          ON ph.stock_code=k.stock_code AND ph.dt=k.dt
        LEFT JOIN stock_price_daily sp
          ON sp.stock_code=k.stock_code AND sp.bas_dt=k.dt
        """,
    )
    if affected:
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_key ON {backup}(stock_code, dt)")
        c.execute(
            f"""
            UPDATE kiwoom_investor_daily
            SET close_pric=b.new_close_pric,
                acc_trde_qty=b.new_acc_trde_qty,
                acc_trde_prica=b.new_acc_trde_prica,
                updated_at=CURRENT_TIMESTAMP
            FROM {backup} b
            WHERE kiwoom_investor_daily.stock_code=b.stock_code
              AND kiwoom_investor_daily.dt=b.dt
            """
        )
    log(c, run_id, "kiwoom_investor_daily", "fill_market_fields_from_price_tables_or_zero", affected, backup)
    return {"affected": affected, "backup": backup}


def fix_valuation_internal(c: sqlite3.Connection, run_id: str) -> dict:
    backup = f"data_quality_backup_stock_universe_valuation_{run_id}"
    affected = backup_table(
        c,
        backup,
        """
        WITH latest AS (
          SELECT stock_code, MAX(base_date) md
          FROM stock_universe
          WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          GROUP BY stock_code
        ), p AS (
          SELECT stock_code, close,
                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
          FROM price_history
          WHERE close>0
        ), f AS (
          SELECT stock_code, year, eps, bps,
                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END, id DESC) rn
          FROM financial_data
          WHERE is_annual=1
        )
        SELECT t.id, t.stock_code, t.base_date,
               t.per old_per, t.pbr old_pbr, t.eps old_eps, t.bps old_bps,
               p.close latest_close, f.eps fin_eps, f.bps fin_bps,
               CASE WHEN f.eps>0 THEN p.close/f.eps END new_per,
               CASE WHEN f.bps>0 THEN p.close/f.bps END new_pbr
        FROM stock_universe t
        JOIN latest l ON l.stock_code=t.stock_code AND l.md=t.base_date
        LEFT JOIN p ON p.stock_code=t.stock_code AND p.rn=1
        LEFT JOIN f ON f.stock_code=t.stock_code AND f.rn=1
        WHERE p.close IS NOT NULL
          AND (
            (t.per IS NOT NULL AND f.eps>0 AND ABS(t.per-(p.close/f.eps))/ABS(p.close/f.eps) > 0.25)
            OR
            (t.pbr IS NOT NULL AND f.bps>0 AND ABS(t.pbr-(p.close/f.bps))/ABS(p.close/f.bps) > 0.25)
          )
        """,
    )
    if affected:
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_id ON {backup}(id)")
        c.execute(
            f"""
            UPDATE stock_universe
            SET per=(SELECT new_per FROM {backup} b WHERE b.id=stock_universe.id),
                pbr=(SELECT new_pbr FROM {backup} b WHERE b.id=stock_universe.id),
                eps=(SELECT fin_eps FROM {backup} b WHERE b.id=stock_universe.id),
                bps=(SELECT fin_bps FROM {backup} b WHERE b.id=stock_universe.id),
                source=COALESCE(source,'') || '_valuation_recalc',
                updated_at=CURRENT_TIMESTAMP
            WHERE id IN (SELECT id FROM {backup})
            """
        )
    log(c, run_id, "stock_universe", "recalculate_latest_per_pbr_from_price_eps_bps", affected, backup)
    return {"affected": affected, "backup": backup}


def fix_fnguide_snapshots(c: sqlite3.Connection, run_id: str) -> dict:
    fin_backup = f"data_quality_backup_missing_fnguide_fin_snapshot_{run_id}"
    fin = backup_table(
        c,
        fin_backup,
        """
        SELECT fd.*
        FROM financial_data fd
        LEFT JOIN financial_source_snapshot fss
          ON fss.stock_code=fd.stock_code AND fss.year=fd.year AND fss.quarter=fd.quarter
         AND fss.is_annual=fd.is_annual AND fss.report_type=fd.report_type AND fss.data_source='fnguide'
        WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
          AND fd.data_source='fnguide' AND fss.stock_code IS NULL
        """,
    )
    if fin:
        c.execute(
            f"""
            INSERT INTO financial_source_snapshot
            (stock_code,year,quarter,is_annual,report_type,data_source,source_url,fetched_at,
             revenue,operating_profit,net_income,eps,bps,dps,total_assets,total_liabilities,total_equity,
             verification_status,verification_note,raw_data_json)
            SELECT stock_code,year,quarter,is_annual,COALESCE(report_type,'CFS'),'fnguide',
                   'reconstructed_from_financial_data_fnguide_label',
                   datetime('now'),
                   revenue,operating_profit,net_income,eps,bps,dps,total_assets,total_liabilities,total_equity,
                   'reconstructed','missing snapshot reconstructed from existing fnguide-labeled financial_data row',
                   json_object('source_table','financial_data','source_row_id',id,'run_id','{run_id}')
            FROM {fin_backup}
            """
        )

    cf_backup = f"data_quality_backup_missing_fnguide_cf_snapshot_{run_id}"
    cf = backup_table(
        c,
        cf_backup,
        """
        SELECT cf.*
        FROM cash_flow_data cf
        LEFT JOIN financial_source_snapshot fss
          ON fss.stock_code=cf.stock_code AND fss.year=cf.year AND fss.quarter=cf.quarter
         AND fss.is_annual=cf.is_annual AND fss.report_type=cf.report_type AND fss.data_source='fnguide'
        WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
          AND cf.data_source='fnguide' AND fss.stock_code IS NULL
        """,
    )
    if cf:
        c.execute(
            f"""
            INSERT INTO financial_source_snapshot
            (stock_code,year,quarter,is_annual,report_type,data_source,source_url,fetched_at,
             operating_cf,investing_cf,financing_cf,capex,
             verification_status,verification_note,raw_data_json)
            SELECT stock_code,year,quarter,is_annual,COALESCE(report_type,'CFS'),'fnguide',
                   'reconstructed_from_cash_flow_data_fnguide_label',
                   datetime('now'),
                   COALESCE(operating_cf_q, operating_cf),
                   COALESCE(investing_cf_q, investing_cf),
                   COALESCE(financing_cf_q, financing_cf),
                   COALESCE(capex_q, capex),
                   'reconstructed','missing snapshot reconstructed from existing fnguide-labeled cash_flow_data row',
                   json_object('source_table','cash_flow_data','source_row_id',id,'run_id','{run_id}')
            FROM {cf_backup}
            """
        )
    log(c, run_id, "financial_source_snapshot", "reconstruct_missing_fnguide_financial_snapshots", fin, fin_backup)
    log(c, run_id, "financial_source_snapshot", "reconstruct_missing_fnguide_cashflow_snapshots", cf, cf_backup)
    return {"financial": fin, "cashflow": cf, "financial_backup": fin_backup, "cashflow_backup": cf_backup}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    c = conn()
    try:
        before = count_core(c)
        repairs = {
            "financial": fix_financial_quarter_annual(c, run_id),
            "cashflow": fix_cashflow_quarter_annual(c, run_id),
            "kiwoom": fix_kiwoom_market_fields(c, run_id),
            "valuation": fix_valuation_internal(c, run_id),
            "snapshots": fix_fnguide_snapshots(c, run_id),
        }
        c.commit()
        after = count_core(c)
        payload = {"run_id": run_id, "before": before, "repairs": repairs, "after": after}
        out = OUT / f"summary_{run_id}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
