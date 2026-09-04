"""KIS-backed actual short-sale data, kept separate from stock lending data."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import requests
from sqlalchemy import text

import config
from database import engine
from kis_client import KISClient

logger = logging.getLogger(__name__)
_schema_ready = False
_schema_lock = threading.Lock()

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS short_sale_daily (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    short_qty DOUBLE PRECISION,
    short_amt DOUBLE PRECISION,
    short_volume_ratio DOUBLE PRECISION,
    short_amount_ratio DOUBLE PRECISION,
    trade_volume DOUBLE PRECISION,
    trade_amount DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'KIS_FHPST04830000',
    collected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, stock_code)
)
"""


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_short_sale_table() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with engine.begin() as conn:
            conn.exec_driver_sql(_TABLE_SQL)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_short_sale_code_date "
                "ON short_sale_daily(stock_code, trade_date DESC)"
            )
        _schema_ready = True


def _fetch_kis_rows(stock_code: str, days: int = 45) -> list[dict]:
    kis = KISClient()
    token = kis.get_token()
    if not token:
        return []

    end = datetime.now()
    start = end - timedelta(days=days)
    response = requests.get(
        f"{config.KIS_URL}/uapi/domestic-stock/v1/quotations/daily-short-sale",
        headers={
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config.KIS_APP_KEY,
            "appsecret": config.KIS_APP_SECRET,
            "tr_id": "FHPST04830000",
        },
        params={
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("rt_cd") != "0":
        raise RuntimeError(payload.get("msg1") or "KIS short-sale query failed")

    result = []
    for row in payload.get("output2") or []:
        trade_date = str(row.get("stck_bsop_date") or "").strip()
        if len(trade_date) != 8:
            continue
        result.append({
            "trade_date": trade_date,
            "stock_code": stock_code,
            "short_qty": _num(row.get("ssts_cntg_qty")),
            "short_amt": _num(row.get("ssts_tr_pbmn")),
            "short_volume_ratio": _num(row.get("ssts_vol_rlim")),
            "short_amount_ratio": _num(row.get("ssts_tr_pbmn_rlim")),
            "trade_volume": _num(row.get("acml_vol")),
            "trade_amount": _num(row.get("acml_tr_pbmn")),
        })
    return result


def _upsert_rows(rows: list[dict]) -> None:
    if not rows:
        return
    sql = text("""
        INSERT INTO short_sale_daily
          (trade_date, stock_code, short_qty, short_amt, short_volume_ratio,
           short_amount_ratio, trade_volume, trade_amount, source, collected_at)
        VALUES
          (:trade_date, :stock_code, :short_qty, :short_amt, :short_volume_ratio,
           :short_amount_ratio, :trade_volume, :trade_amount,
           'KIS_FHPST04830000', CURRENT_TIMESTAMP)
        ON CONFLICT (trade_date, stock_code) DO UPDATE SET
          short_qty = excluded.short_qty,
          short_amt = excluded.short_amt,
          short_volume_ratio = excluded.short_volume_ratio,
          short_amount_ratio = excluded.short_amount_ratio,
          trade_volume = excluded.trade_volume,
          trade_amount = excluded.trade_amount,
          source = excluded.source,
          collected_at = CURRENT_TIMESTAMP
    """)
    with engine.begin() as conn:
        conn.execute(sql, rows)


def get_actual_short_sale_rows(stock_code: str, limit: int = 60) -> list[dict]:
    """Return verified short-sale rows; refresh from KIS at most every 6 hours."""
    ensure_short_sale_table()
    with engine.connect() as conn:
        cached = conn.execute(
            text("""
                SELECT trade_date, short_qty, short_amt, short_volume_ratio,
                       short_amount_ratio, trade_volume, trade_amount, collected_at
                FROM short_sale_daily
                WHERE stock_code = :stock_code
                ORDER BY trade_date DESC
                LIMIT :limit
            """),
            {"stock_code": stock_code, "limit": limit},
        ).mappings().all()

    refresh = not cached
    if cached and cached[0].get("collected_at"):
        collected_at = cached[0]["collected_at"]
        if isinstance(collected_at, str):
            collected_at = datetime.fromisoformat(collected_at.replace("Z", "+00:00")).replace(tzinfo=None)
        refresh = datetime.now() - collected_at > timedelta(hours=6)

    if refresh:
        try:
            _upsert_rows(_fetch_kis_rows(stock_code))
            with engine.connect() as conn:
                cached = conn.execute(
                    text("""
                        SELECT trade_date, short_qty, short_amt, short_volume_ratio,
                               short_amount_ratio, trade_volume, trade_amount, collected_at
                        FROM short_sale_daily
                        WHERE stock_code = :stock_code
                        ORDER BY trade_date DESC
                        LIMIT :limit
                    """),
                    {"stock_code": stock_code, "limit": limit},
                ).mappings().all()
        except Exception as exc:
            logger.warning("KIS actual short-sale refresh failed [%s]: %s", stock_code, exc)

    return [dict(row) for row in cached]
