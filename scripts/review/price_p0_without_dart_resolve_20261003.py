#!/usr/bin/env python3
"""Classify P0 price accuracy candidates without using DART.

Inputs are produced by price_accuracy_recheck_20261003.py.  This script is
read-only against PostgreSQL and compares the P0 candidate rows with existing
local/raw-ish sources: stock_price_daily, naver_price_history_backfill,
price_history_fix_backup, and local FinanceData/marcap parquet cache.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
MARCAP_DIR = ROOT / "data_cache" / "marcap"


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


def psql_csv(db_url: str, sql: str, dst: Path) -> None:
    cmd = ["psql", db_url, "-q", "-c", f"\\copy ({sql}) TO '{dst}' WITH CSV HEADER"]
    subprocess.run(cmd, check=True)


def sql_list(values: list[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in sorted(set(values)))


def norm_date(s: object) -> str:
    text = str(s)[:10]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def read_candidates() -> pd.DataFrame:
    frac = pd.read_csv(OUT / "price_fractional_krw_candidates.csv", dtype={"stock_code": str})
    frac["candidate_type"] = "fractional_krw"
    invalid = pd.read_csv(OUT / "price_invalid_ohlcv.csv", dtype={"stock_code": str})
    invalid["candidate_type"] = "invalid_ohlcv"
    df = pd.concat([frac, invalid], ignore_index=True, sort=False)
    df["date"] = df["date"].map(norm_date)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_pg_sources(db_url: str, candidates: pd.DataFrame) -> dict[str, pd.DataFrame]:
    codes = candidates["stock_code"].dropna().astype(str).unique().tolist()
    dates = candidates["date"].dropna().astype(str)
    min_date, max_date = dates.min(), dates.max()
    code_sql = sql_list(codes)
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        psql_csv(
            db_url,
            f"""
            SELECT stock_code, bas_dt, open_price, high_price, low_price, close_price, volume, trade_amt, market_cap, shares
            FROM stock_price_daily
            WHERE stock_code IN ({code_sql})
              AND bas_dt BETWEEN replace('{min_date}','-','') AND replace('{max_date}','-','')
            """,
            tmp / "stock_price_daily.csv",
        )
        psql_csv(
            db_url,
            f"""
            SELECT stock_code, date, open, high, low, close, volume, source_url, fetched_at
            FROM naver_price_history_backfill
            WHERE stock_code IN ({code_sql})
              AND date BETWEEN '{min_date}' AND '{max_date}'
            """,
            tmp / "naver_price_history_backfill.csv",
        )
        psql_csv(
            db_url,
            f"""
            SELECT run_id, stock_code, date,
                   old_open, old_high, old_low, old_close, old_volume,
                   new_open, new_high, new_low, new_close, new_volume,
                   reason, fixed_at
            FROM price_history_fix_backup
            WHERE stock_code IN ({code_sql})
              AND date BETWEEN '{min_date}' AND '{max_date}'
            """,
            tmp / "price_history_fix_backup.csv",
        )
        psql_csv(
            db_url,
            f"""
            SELECT stock_code, event_date, reason, evidence, created_at
            FROM price_integrity_quarantine
            WHERE stock_code IN ({code_sql})
              AND event_date BETWEEN '{min_date}' AND '{max_date}'
            """,
            tmp / "price_integrity_quarantine.csv",
        )
        return {
            name: pd.read_csv(tmp / f"{name}.csv", dtype={"stock_code": str})
            for name in [
                "stock_price_daily",
                "naver_price_history_backfill",
                "price_history_fix_backup",
                "price_integrity_quarantine",
            ]
        }


def load_marcap(candidates: pd.DataFrame) -> pd.DataFrame:
    years = sorted({int(str(d)[:4]) for d in candidates["date"].dropna().astype(str)})
    codes = set(candidates["stock_code"].astype(str))
    parts: list[pd.DataFrame] = []
    for year in years:
        path = MARCAP_DIR / f"marcap-{year}.parquet"
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df.columns = [str(c) for c in df.columns]
        code_col = "Code" if "Code" in df.columns else "code"
        date_col = "Date" if "Date" in df.columns else "date"
        cols = [c for c in [date_col, code_col, "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"] if c in df.columns]
        sub = df.loc[df[code_col].astype(str).isin(codes), cols].copy()
        sub = sub.rename(columns={date_col: "date", code_col: "stock_code"})
        sub["stock_code"] = sub["stock_code"].astype(str).str.zfill(6)
        sub["date"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d")
        parts.append(sub)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def integer_ohlcv(row: pd.Series, prefix: str = "") -> bool:
    for col in ["open", "high", "low", "close"]:
        val = row.get(prefix + col)
        if pd.isna(val) or float(val) != round(float(val)):
            return False
    return True


def ohlc_equal(row: pd.Series, prefix: str, tolerance: float = 0.0) -> bool:
    for col in ["open", "high", "low", "close"]:
        left = row.get(col)
        right = row.get(prefix + col)
        if pd.isna(left) or pd.isna(right):
            return False
        if abs(float(left) - float(right)) > tolerance:
            return False
    return True


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = read_candidates()
    db_url = read_db_url()
    sources = load_pg_sources(db_url, candidates)
    marcap = load_marcap(candidates)

    spd = sources["stock_price_daily"].copy()
    if not spd.empty:
        spd["date"] = spd["bas_dt"].map(norm_date)
        spd = spd.rename(columns={
            "open_price": "spd_open",
            "high_price": "spd_high",
            "low_price": "spd_low",
            "close_price": "spd_close",
            "volume": "spd_volume",
        })
    naver = sources["naver_price_history_backfill"].rename(columns={
        "open": "naver_open",
        "high": "naver_high",
        "low": "naver_low",
        "close": "naver_close",
        "volume": "naver_volume",
    })
    backup = sources["price_history_fix_backup"].copy()
    if not backup.empty:
        backup["backup_rows"] = 1
        backup_latest = (
            backup.sort_values(["stock_code", "date", "fixed_at", "run_id"])
            .groupby(["stock_code", "date"], as_index=False)
            .tail(1)
        )
    else:
        backup_latest = backup
    if not marcap.empty:
        marcap = marcap.rename(columns={
            "Open": "marcap_open",
            "High": "marcap_high",
            "Low": "marcap_low",
            "Close": "marcap_close",
            "Volume": "marcap_volume",
        })

    m = candidates.merge(
        spd[["stock_code", "date", "spd_open", "spd_high", "spd_low", "spd_close", "spd_volume"]] if not spd.empty else pd.DataFrame(columns=["stock_code", "date"]),
        on=["stock_code", "date"],
        how="left",
    )
    m = m.merge(
        naver[["stock_code", "date", "naver_open", "naver_high", "naver_low", "naver_close", "naver_volume"]] if not naver.empty else pd.DataFrame(columns=["stock_code", "date"]),
        on=["stock_code", "date"],
        how="left",
    )
    m = m.merge(
        backup_latest[["stock_code", "date", "old_open", "old_high", "old_low", "old_close", "new_open", "new_high", "new_low", "new_close", "reason", "run_id", "fixed_at"]] if not backup_latest.empty else pd.DataFrame(columns=["stock_code", "date"]),
        on=["stock_code", "date"],
        how="left",
    )
    m = m.merge(
        marcap[["stock_code", "date", "marcap_open", "marcap_high", "marcap_low", "marcap_close", "marcap_volume"]] if not marcap.empty else pd.DataFrame(columns=["stock_code", "date"]),
        on=["stock_code", "date"],
        how="left",
    )

    for col in m.columns:
        if col.endswith(("_open", "_high", "_low", "_close", "_volume")) or col in ["open", "high", "low", "close", "volume"]:
            m[col] = pd.to_numeric(m[col], errors="coerce")

    decisions = []
    for _, r in m.iterrows():
        source = "none"
        status = "needs_manual_or_external_raw"
        note = ""
        # Prefer stock_price_daily and marcap: both represent exchange/public raw-ish OHLCV.
        if not pd.isna(r.get("spd_close")) and all(not pd.isna(r.get(f"spd_{c}")) for c in ["open", "high", "low", "close"]):
            if all(float(r[f"spd_{c}"]) == round(float(r[f"spd_{c}"])) for c in ["open", "high", "low", "close"]) and not (
                float(r["spd_open"]) == 0 and float(r["spd_high"]) == 0 and float(r["spd_low"]) == 0 and float(r["spd_close"]) > 0
            ):
                source = "stock_price_daily"
                status = "repairable_from_stock_price_daily"
        if status == "needs_manual_or_external_raw" and not pd.isna(r.get("marcap_close")) and all(not pd.isna(r.get(f"marcap_{c}")) for c in ["open", "high", "low", "close"]):
            if all(float(r[f"marcap_{c}"]) == round(float(r[f"marcap_{c}"])) for c in ["open", "high", "low", "close"]) and not (
                float(r["marcap_open"]) == 0 and float(r["marcap_high"]) == 0 and float(r["marcap_low"]) == 0 and float(r["marcap_close"]) > 0
            ):
                source = "marcap"
                status = "repairable_from_marcap"
        if status == "needs_manual_or_external_raw" and not pd.isna(r.get("naver_close")):
            if all(not pd.isna(r.get(f"naver_{c}")) and float(r[f"naver_{c}"]) == round(float(r[f"naver_{c}"])) for c in ["open", "high", "low", "close"]):
                source = "naver_backfill"
                status = "candidate_repairable_from_naver_but_needs_raw_basis_check"
        if r["candidate_type"] == "invalid_ohlcv" and float(r["volume"] or 0) == 0 and float(r["open"] or 0) == 0 and float(r["high"] or 0) == 0 and float(r["low"] or 0) == 0:
            if status == "needs_manual_or_external_raw":
                status = "source_agrees_zero_ohlc_positive_close_policy_decision"
                note = "zero OHLC with positive close and zero volume"
        decisions.append({**r.to_dict(), "without_dart_resolution": status, "preferred_source": source, "resolution_note": note})

    out = pd.DataFrame(decisions)
    out.to_csv(OUT / "price_p0_without_dart_resolution.csv", index=False, encoding="utf-8-sig")
    summary = (
        out.groupby(["candidate_type", "without_dart_resolution"], dropna=False)
        .agg(rows=("stock_code", "size"), stocks=("stock_code", "nunique"))
        .reset_index()
        .sort_values(["candidate_type", "rows"], ascending=[True, False])
    )
    summary.to_csv(OUT / "price_p0_without_dart_resolution_summary.csv", index=False, encoding="utf-8-sig")
    stock_summary = (
        out.groupby(["candidate_type", "stock_code", "without_dart_resolution"], dropna=False)
        .agg(rows=("date", "size"), first_date=("date", "min"), last_date=("date", "max"))
        .reset_index()
        .sort_values(["candidate_type", "rows"], ascending=[True, False])
    )
    stock_summary.to_csv(OUT / "price_p0_without_dart_resolution_by_stock.csv", index=False, encoding="utf-8-sig")

    payload = {
        "rows": int(len(out)),
        "stocks": int(out["stock_code"].nunique()),
        "summary": summary.to_dict(orient="records"),
        "artifacts": [
            "price_p0_without_dart_resolution.csv",
            "price_p0_without_dart_resolution_summary.csv",
            "price_p0_without_dart_resolution_by_stock.csv",
        ],
    }
    (OUT / "price_p0_without_dart_resolution_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
