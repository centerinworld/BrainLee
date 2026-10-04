#!/usr/bin/env python3
"""Read-only price-data accuracy recheck for the 99.99% data-quality target.

This audit intentionally does not call DART and does not modify operational
tables.  It measures whether PostgreSQL price_history is good enough to be
treated as canonical raw KR OHLCV data, and writes review queues.
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


OUT = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"


def read_db_url() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTGRES_DATABASE_URL="):
                return line.split("=", 1)[1].strip().replace("postgresql+psycopg://", "postgresql://")
    url = os.environ.get("POSTGRES_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("POSTGRES_DATABASE_URL is not set and runtime/.env was not found")
    return url.replace("postgresql+psycopg://", "postgresql://")


def rows(db_url: str, sql: str) -> list[dict[str, Any]]:
    wrapped = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
    proc = subprocess.run(
        ["psql", db_url, "-q", "-t", "-A", "-c", wrapped],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not proc.stdout.strip():
        return []
    return list(csv.DictReader(io.StringIO(proc.stdout)))


def one(db_url: str, sql: str) -> dict[str, Any]:
    result = rows(db_url, sql)
    return result[0] if result else {}


def table_exists(db_url: str, name: str) -> bool:
    r = one(
        db_url,
        f"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='{name}') AS ok",
    )
    return str(r.get("ok")).lower() in {"t", "true", "1"}


def write_csv(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in data:
        for key in row:
            if key not in cols:
                cols.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(data)


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def latest_universe_cte() -> str:
    return """
    WITH latest_universe AS (
      SELECT DISTINCT ON (stock_code)
             stock_code, stock_name, market, stock_type, kind_stkcert_nm, base_date
      FROM stock_universe
      WHERE stock_code ~ '^[0-9A-Z]{6}$'
      ORDER BY stock_code, base_date DESC NULLS LAST
    )
    """


def pct(ok: int, total: int) -> float | None:
    return round(ok / total * 100, 6) if total else None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    db_url = read_db_url()
    generated_at = datetime.now().isoformat(timespec="seconds")

    summary: dict[str, Any] = {
        "generated_at": generated_at,
        "workspace": str(ROOT.parent),
        "database": "PostgreSQL stock_dashboard",
        "mode": "read_only",
        "target_accuracy_pct": 99.99,
    }

    universe = one(
        db_url,
        latest_universe_cte()
        + """
        SELECT
          COUNT(*) AS universe_codes,
          COUNT(*) FILTER (WHERE stock_code ~ '^[0-9]{6}$') AS numeric_codes,
          COUNT(*) FILTER (WHERE market IN ('KOSPI','KOSDAQ','KONEX','유가증권','코스피','코스닥','코넥스')) AS kr_market_codes,
          COUNT(*) FILTER (WHERE COALESCE(stock_name,'') ILIKE '%ETF%' OR COALESCE(stock_name,'') ILIKE '%ETN%'
                           OR COALESCE(kind_stkcert_nm,'') ILIKE '%ETF%' OR COALESCE(kind_stkcert_nm,'') ILIKE '%ETN%') AS etf_etn_like_codes
        FROM latest_universe
        """,
    )
    summary["universe"] = universe

    scope_filter = """
      ph.stock_code ~ '^[0-9A-Z]{6}$'
      AND EXISTS (
        SELECT 1 FROM stock_universe su
        WHERE su.stock_code=ph.stock_code
          AND su.market IN ('KOSPI','KOSDAQ','KONEX','유가증권','코스피','코스닥','코넥스')
      )
    """
    price_rows = one(
        db_url,
        f"""
        SELECT COUNT(*) AS price_rows,
               COUNT(DISTINCT stock_code) AS price_codes,
               MIN(date::text) AS min_date,
               MAX(date::text) AS max_date
        FROM price_history ph
        WHERE {scope_filter}
        """,
    )
    summary["price_history_scope"] = price_rows

    duplicate_groups = rows(
        db_url,
        f"""
        SELECT stock_code, date::text AS date, COUNT(*) AS row_count
        FROM price_history ph
        WHERE {scope_filter}
        GROUP BY stock_code, date
        HAVING COUNT(*) > 1
        ORDER BY row_count DESC, stock_code, date
        LIMIT 1000
        """,
    )
    write_csv(OUT / "price_duplicate_keys.csv", duplicate_groups)
    summary["duplicate_keys"] = {
        "groups": len(duplicate_groups),
        "extra_rows": sum(int(r["row_count"]) - 1 for r in duplicate_groups),
        "artifact": "price_duplicate_keys.csv",
    }

    invalid_ohlcv = rows(
        db_url,
        f"""
        SELECT stock_code, date::text AS date, open, high, low, close, volume,
               CASE
                 WHEN close IS NULL OR close <= 0 THEN 'close_missing_or_nonpositive'
                 WHEN open IS NULL OR high IS NULL OR low IS NULL THEN 'ohl_missing'
                 WHEN volume IS NULL OR volume < 0 THEN 'volume_missing_or_negative'
                 WHEN open < 0 OR high < 0 OR low < 0 THEN 'negative_ohl'
                 WHEN NOT (volume=0 AND open=0 AND high=0 AND low=0)
                      AND (open <= 0 OR high <= 0 OR low <= 0) THEN 'nonpositive_tradable_ohl'
                 WHEN high + 1 < GREATEST(open, low, close) THEN 'high_below_ohlc'
                 WHEN low - 1 > LEAST(open, high, close) THEN 'low_above_ohlc'
                 ELSE 'unknown_invalid'
               END AS reason
        FROM price_history ph
        WHERE {scope_filter}
          AND (
            close IS NULL OR close <= 0
            OR open IS NULL OR high IS NULL OR low IS NULL
            OR volume IS NULL OR volume < 0
            OR open < 0 OR high < 0 OR low < 0
            OR (NOT (volume=0 AND open=0 AND high=0 AND low=0)
                AND (open <= 0 OR high <= 0 OR low <= 0))
            OR high + 1 < GREATEST(open, low, close)
            OR low - 1 > LEAST(open, high, close)
          )
        ORDER BY date DESC, stock_code
        LIMIT 20000
        """,
    )
    write_csv(OUT / "price_invalid_ohlcv.csv", invalid_ohlcv)
    summary["invalid_ohlcv"] = {
        "rows": len(invalid_ohlcv),
        "artifact": "price_invalid_ohlcv.csv",
    }

    fractional = rows(
        db_url,
        f"""
        SELECT stock_code, date::text AS date, open, high, low, close, volume
        FROM price_history ph
        WHERE {scope_filter}
          AND ph.stock_code ~ '^[0-9]{{6}}$'
          AND close > 0
          AND (open <> ROUND(open) OR high <> ROUND(high) OR low <> ROUND(low) OR close <> ROUND(close))
        ORDER BY date DESC, stock_code
        LIMIT 20000
        """,
    )
    write_csv(OUT / "price_fractional_krw_candidates.csv", fractional)
    summary["fractional_krw_candidates"] = {
        "rows_limited": len(fractional),
        "limit": 20000,
        "artifact": "price_fractional_krw_candidates.csv",
        "interpretation": "KR raw OHLCV should be integer KRW; any remaining fractional numeric-stock price is a basis/repair candidate.",
    }

    latest_path = OUT / "price_latest_freshness_by_stock.csv"
    latest_rows = read_csv(latest_path)
    if latest_rows:
        freshness_counts: dict[str, int] = {}
        for r in latest_rows:
            freshness_counts[str(r["freshness_status"])] = freshness_counts.get(str(r["freshness_status"]), 0) + 1
        current = freshness_counts.get("current", 0)
        summary["latest_freshness"] = {
            "stocks": len(latest_rows),
            "counts": freshness_counts,
            "current_pct": pct(current, len(latest_rows)),
            "artifact": "price_latest_freshness_by_stock.csv",
            "note": "Reused completed artifact from the same audit run because live full latest-date aggregation is too heavy for an interactive recheck.",
        }
    else:
        summary["latest_freshness"] = {
            "status": "skipped",
            "reason": "Full latest-date aggregation exceeded interactive audit budget and no completed artifact was present.",
        }

    summary["price_history_quality_v"] = {
        "status": "skipped_full_scan",
        "reason": "Full view aggregation exceeded interactive audit budget. Direct deterministic checks and existing price audit artifacts are used instead.",
    }

    summary["canonical_price_history_v"] = {
        "status": "skipped_full_scan",
        "reason": "Full canonical view aggregation exceeded interactive audit budget; price_history_quality_v, deterministic OHLC gates, freshness, quarantine, and third-party quote checks are used for this recheck.",
    }

    if table_exists(db_url, "price_ingestion_quarantine"):
        quarantine_counts = rows(
            db_url,
            """
            SELECT reason, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks,
                   MIN(created_at::text) AS first_seen, MAX(created_at::text) AS last_seen
            FROM price_ingestion_quarantine
            WHERE stock_code ~ '^[0-9A-Z]{6}$'
            GROUP BY reason
            ORDER BY rows DESC
            """,
        )
        write_csv(OUT / "price_ingestion_quarantine_counts.csv", quarantine_counts)
        recent_quarantine = rows(
            db_url,
            """
            SELECT stock_code, source, reason, COUNT(*) AS rows,
                   MIN(created_at::text) AS first_seen, MAX(created_at::text) AS last_seen
            FROM price_ingestion_quarantine
            WHERE stock_code ~ '^[0-9A-Z]{6}$'
              AND created_at::date >= CURRENT_DATE - INTERVAL '14 day'
              AND reason <> 'duplicate_input_dates'
            GROUP BY stock_code, source, reason
            ORDER BY rows DESC, stock_code
            LIMIT 2000
            """,
        )
        write_csv(OUT / "price_ingestion_quarantine_recent_14d.csv", recent_quarantine)
        summary["price_ingestion_quarantine"] = {
            "counts": quarantine_counts,
            "recent_14d_stock_source_reason_rows": len(recent_quarantine),
            "artifacts": ["price_ingestion_quarantine_counts.csv", "price_ingestion_quarantine_recent_14d.csv"],
        }

    if table_exists(db_url, "price_close_verify_log"):
        close_verify = rows(
            db_url,
            """
            SELECT trade_date::text AS trade_date, status, COUNT(*) AS runs,
                   MAX(checked_at::text) AS last_checked_at
            FROM price_close_verify_log
            GROUP BY trade_date, status
            ORDER BY trade_date DESC, status
            LIMIT 20
            """,
        )
        write_csv(OUT / "price_close_verify_recent.csv", close_verify)
        summary["price_close_verify_log"] = {
            "recent": close_verify,
            "artifact": "price_close_verify_recent.csv",
        }

    # One-day third-party comparison generated by third_party_crosscheck_20261003.py.
    third = ROOT / "research_outputs" / "third_party_crosscheck_20261003"
    close_mismatch = third / "aik_quotes_close_mismatch_20261001.csv"
    stale = third / "aik_quotes_pg_latest_stale_classified.csv"
    if close_mismatch.exists() or stale.exists():
        third_party: dict[str, Any] = {}
        if close_mismatch.exists():
            with close_mismatch.open(encoding="utf-8-sig") as f:
                third_party["aik_20261001_close_mismatches"] = max(sum(1 for _ in f) - 1, 0)
            third_party["close_mismatch_artifact"] = str(close_mismatch.relative_to(ROOT))
        if stale.exists():
            with stale.open(encoding="utf-8-sig") as f:
                third_party["aik_latest_stale_rows"] = max(sum(1 for _ in f) - 1, 0)
            third_party["stale_artifact"] = str(stale.relative_to(ROOT))
        summary["third_party_aikstockdata_quote_check"] = third_party

    # A conservative pass-rate view. It is not a claim of true accuracy; it is a
    # count of rows that pass deterministic internal gates.
    total_rows = int(price_rows.get("price_rows") or 0)
    known_bad_rows = int(summary["duplicate_keys"]["extra_rows"]) + len(invalid_ohlcv) + len(fractional)
    summary["deterministic_internal_gate"] = {
        "known_bad_or_review_rows_counted": known_bad_rows,
        "rows_checked": total_rows,
        "pass_pct_upper_bound": pct(max(total_rows - known_bad_rows, 0), total_rows),
        "note": "Upper bound because freshness, unresolved jumps, quarantines, and third-party mismatches are not one-to-one row defects.",
    }

    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
