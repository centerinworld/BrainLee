#!/usr/bin/env python3
"""Repair fixable non-financial market-data integrity findings.

Backs up every affected row before mutation. Scope excludes financial
statements and cash-flow data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402


RUN_ID = f"market_data_integrity_repair_20261001_{datetime.now():%H%M%S}"


def main() -> int:
    conn = connect_primary_db(timeout=300)
    cur = conn.cursor()
    summary: dict[str, int | str] = {"run_id": RUN_ID}

    def exec_count(sql: str, params: tuple = ()) -> int:
        cur.execute(sql, params)
        return int(cur.rowcount or 0)

    try:
        # Required for the price_history guard on historical price writes.
        cur.execute("SELECT set_config('app.price_basis_checked','1',true)")

        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS repair_backup_price_history_20261001 AS
            SELECT *, %s::text AS repair_run_id, now() AS backed_up_at
            FROM price_history
            WHERE false
            """,
            (RUN_ID,),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS repair_backup_kiwoom_foreign_flow_20261001 AS
            SELECT *, %s::text AS repair_run_id, now() AS backed_up_at
            FROM kiwoom_foreign_flow
            WHERE false
            """,
            (RUN_ID,),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS repair_backup_kiwoom_credit_balance_20261001 AS
            SELECT *, %s::text AS repair_run_id, now() AS backed_up_at
            FROM kiwoom_credit_balance
            WHERE false
            """,
            (RUN_ID,),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS repair_backup_short_rank_daily_20261001 AS
            SELECT *, %s::text AS repair_run_id, now() AS backed_up_at
            FROM short_rank_daily
            WHERE false
            """,
            (RUN_ID,),
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS repair_backup_short_sector_daily_20261001 AS
            SELECT *, %s::text AS repair_run_id, now() AS backed_up_at
            FROM short_sector_daily
            WHERE false
            """,
            (RUN_ID,),
        )

        # 1) price_history OHLCV:
        # - No-volume close-only placeholder rows: set OHLC to close and amount to 0.
        # - Positive-envelope rows: widen high/low to envelope bounds.
        cur.execute(
            """
            INSERT INTO repair_backup_price_history_20261001
            SELECT ph.*, %s, now()
            FROM price_history ph
            WHERE ph.stock_code ~ '^[0-9]{6}$'
              AND ph.close > 0
              AND (
                (COALESCE(ph.volume,0) = 0 AND (
                  COALESCE(ph.open,0) <= 0 OR COALESCE(ph.high,0) <= 0 OR COALESCE(ph.low,0) <= 0
                  OR ph.trade_amount IS NULL OR ph.trade_amount < 0
                ))
                OR (
                  ph.open > 0 AND ph.high > 0 AND ph.low > 0 AND ph.close > 0
                  AND (ph.high < GREATEST(ph.open, ph.close, ph.low)
                       OR ph.low > LEAST(ph.open, ph.close, ph.high))
                )
              )
            """,
            (RUN_ID,),
        )
        summary["price_history_backup_rows"] = int(cur.rowcount or 0)
        summary["price_history_close_only_repaired"] = exec_count(
            """
            UPDATE price_history
            SET open = close,
                high = close,
                low = close,
                volume = COALESCE(volume, 0),
                trade_amount = 0
            WHERE stock_code ~ '^[0-9]{6}$'
              AND close > 0
              AND COALESCE(volume,0) = 0
              AND (
                COALESCE(open,0) <= 0 OR COALESCE(high,0) <= 0 OR COALESCE(low,0) <= 0
                OR trade_amount IS NULL OR trade_amount < 0
              )
            """
        )
        summary["price_history_envelope_repaired"] = exec_count(
            """
            UPDATE price_history
            SET high = GREATEST(open, high, low, close),
                low = LEAST(open, high, low, close)
            WHERE stock_code ~ '^[0-9]{6}$'
              AND open > 0 AND high > 0 AND low > 0 AND close > 0
              AND (high < GREATEST(open, close, low) OR low > LEAST(open, close, high))
            """
        )

        # 2) Kiwoom foreign flow stores signed current prices; use absolute price.
        cur.execute(
            """
            INSERT INTO repair_backup_kiwoom_foreign_flow_20261001
            SELECT k.*, %s, now()
            FROM kiwoom_foreign_flow k
            WHERE close_price < 0
            """,
            (RUN_ID,),
        )
        summary["kiwoom_foreign_flow_backup_rows"] = int(cur.rowcount or 0)
        summary["kiwoom_foreign_flow_close_abs_repaired"] = exec_count(
            "UPDATE kiwoom_foreign_flow SET close_price = ABS(close_price) WHERE close_price < 0"
        )

        # 3) Kiwoom credit ratio: raw shr_rt is daily change rate; remn_rt is balance ratio.
        # Existing raw_json lets us repair legacy rows without a network call.
        cur.execute(
            """
            INSERT INTO repair_backup_kiwoom_credit_balance_20261001
            SELECT k.*, %s, now()
            FROM kiwoom_credit_balance k
            WHERE COALESCE(credit_ratio,0) < 0 OR COALESCE(credit_ratio,0) > 100
               OR COALESCE(new_credit_qty,0) < 0 OR COALESCE(repay_credit_qty,0) < 0
            """,
            (RUN_ID,),
        )
        summary["kiwoom_credit_balance_backup_rows"] = int(cur.rowcount or 0)
        summary["kiwoom_credit_balance_raw_json_ratio_repaired"] = exec_count(
            """
            UPDATE kiwoom_credit_balance
            SET credit_ratio = NULLIF(raw_json::jsonb ->> 'remn_rt', '')::numeric
            WHERE raw_json IS NOT NULL
              AND raw_json::jsonb ->> 'remn_rt' IS NOT NULL
              AND (COALESCE(credit_ratio,0) < 0 OR COALESCE(credit_ratio,0) > 100)
            """
        )
        summary["kiwoom_credit_balance_qty_abs_repaired"] = exec_count(
            """
            UPDATE kiwoom_credit_balance
            SET new_credit_qty = ABS(new_credit_qty),
                repay_credit_qty = ABS(repay_credit_qty)
            WHERE COALESCE(new_credit_qty,0) < 0 OR COALESCE(repay_credit_qty,0) < 0
            """
        )

        # 4) Securities lending duplicates: keep newest row per intended grain.
        cur.execute(
            """
            WITH ranked AS (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY stock_code, bas_dt
                ORDER BY created_at DESC NULLS LAST, id DESC
              ) AS rn
              FROM short_rank_daily
            )
            INSERT INTO repair_backup_short_rank_daily_20261001
            SELECT s.*, %s, now()
            FROM short_rank_daily s
            JOIN ranked r USING (id)
            WHERE r.rn > 1
            """,
            (RUN_ID,),
        )
        summary["short_rank_daily_backup_rows"] = int(cur.rowcount or 0)
        summary["short_rank_daily_duplicates_deleted"] = exec_count(
            """
            DELETE FROM short_rank_daily s
            USING (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY stock_code, bas_dt
                ORDER BY created_at DESC NULLS LAST, id DESC
              ) AS rn
              FROM short_rank_daily
            ) r
            WHERE s.id = r.id AND r.rn > 1
            """
        )
        cur.execute(
            """
            WITH ranked AS (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY stock_code, bas_dt, sic_cd
                ORDER BY created_at DESC NULLS LAST, id DESC
              ) AS rn
              FROM short_sector_daily
            )
            INSERT INTO repair_backup_short_sector_daily_20261001
            SELECT s.*, %s, now()
            FROM short_sector_daily s
            JOIN ranked r USING (id)
            WHERE r.rn > 1
            """,
            (RUN_ID,),
        )
        summary["short_sector_daily_backup_rows"] = int(cur.rowcount or 0)
        summary["short_sector_daily_duplicates_deleted"] = exec_count(
            """
            DELETE FROM short_sector_daily s
            USING (
              SELECT id, ROW_NUMBER() OVER (
                PARTITION BY stock_code, bas_dt, sic_cd
                ORDER BY created_at DESC NULLS LAST, id DESC
              ) AS rn
              FROM short_sector_daily
            ) r
            WHERE s.id = r.id AND r.rn > 1
            """
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    out = ROOT / "run" / f"{RUN_ID}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
