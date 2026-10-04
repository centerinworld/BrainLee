#!/usr/bin/env python3
"""Read-only third-party cross-checks for Korean stock identifiers, prices, and 2026H1 earnings.

This script does not write to operational tables. It exports PostgreSQL snapshots,
compares them with public third-party files, and writes review queues under
runtime/research_outputs/third_party_crosscheck_20261003 by default.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlretrieve

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / "runtime" / "research_outputs" / "third_party_crosscheck_20261003"

URLS = {
    "kr_corp_ids.csv": "https://raw.githubusercontent.com/pon00050/kr-company-registry/main/data/dist/kr_corp_ids.csv",
    "stock_master.csv.gz": "https://github.com/FinanceData/stock_master/raw/master/stock_master.csv.gz",
    "aik_index.json": "https://aikstockdata.com/data/public/index.json",
    "aik_quotes_min.json": "https://aikstockdata.com/data/public/quotes_min.json",
    "aik_earnings.json": "https://aikstockdata.com/data/public/earnings.json",
    "aik_005930.json": "https://aikstockdata.com/data/public/s/005930.json",
    "aik_172670.json": "https://aikstockdata.com/data/public/s/172670.json",
}


def read_env_url() -> str:
    env_path = ROOT / "runtime" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTGRES_DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
                return url.replace("postgresql+psycopg://", "postgresql://")
    url = os.environ.get("POSTGRES_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("POSTGRES_DATABASE_URL is not set and runtime/.env was not found")
    return url.replace("postgresql+psycopg://", "postgresql://")


def psql_copy(db_url: str, sql: str, dst: Path) -> None:
    cmd = ["psql", db_url, "-q", "-c", f"\\copy ({sql}) to '{dst}' with csv header"]
    subprocess.run(cmd, check=True)


def ensure_inputs(input_dir: Path, fetch: bool) -> dict[str, Path]:
    input_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: input_dir / name for name in URLS}
    if fetch:
        for name, url in URLS.items():
            urlretrieve(url, paths[name])
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        raise SystemExit(f"Missing input files: {missing}. Re-run with --fetch.")
    return paths


def norm_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def compare_identifiers(paths: dict[str, Path], pg_uni: pd.DataFrame, out: Path) -> dict:
    pg_uni["code"] = norm_code(pg_uni["stock_code"])
    pg_common = pg_uni[pg_uni["code"].str.match(r"^[0-9]{6}$", na=False)].copy()
    left = pg_common[["code", "stock_name", "market", "stock_type", "isin_code"]].drop_duplicates("code")

    kr = pd.read_csv(paths["kr_corp_ids.csv"], dtype=str, encoding="utf-8-sig")
    kr["ticker"] = norm_code(kr["ticker"].fillna(""))
    kr_listed = kr[(kr["is_listed"].astype(str).str.lower() == "true") & kr["ticker"].str.match(r"^[0-9]{6}$", na=False)].copy()
    right = kr_listed[["ticker", "corp_name", "market", "corp_code", "bizr_no", "jurir_no", "corp_cls", "extracted_at"]].drop_duplicates("ticker")
    reg = left.merge(right, left_on="code", right_on="ticker", how="outer", indicator=True)
    market_norm = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KONEX": "KONEX", "유가증권": "KOSPI", "코스닥": "KOSDAQ", "코넥스": "KONEX"}
    matched = reg[reg["_merge"] == "both"].copy()
    matched["pg_market_norm"] = matched["market_x"].map(market_norm).fillna(matched["market_x"])
    matched["kr_market_norm"] = matched["market_y"].map(market_norm).fillna(matched["market_y"])
    market_mismatch = matched[matched["pg_market_norm"] != matched["kr_market_norm"]]
    name_mismatch = matched[matched["stock_name"].fillna("") != matched["corp_name"].fillna("")]
    matched.to_csv(out / "kr_company_registry_matched.csv", index=False)
    reg[reg["_merge"] == "left_only"].to_csv(out / "kr_company_registry_pg_only.csv", index=False)
    reg[reg["_merge"] == "right_only"].to_csv(out / "kr_company_registry_registry_only.csv", index=False)
    market_mismatch.to_csv(out / "kr_company_registry_market_mismatch.csv", index=False)
    name_mismatch.to_csv(out / "kr_company_registry_name_mismatch.csv", index=False)

    sm = pd.read_csv(paths["stock_master.csv.gz"], dtype=str, compression="gzip")
    cols = {c.lower(): c for c in sm.columns}
    code_col = cols.get("code") or cols.get("symbol") or cols.get("ticker") or sm.columns[0]
    name_col = cols.get("name") or cols.get("name_kr") or cols.get("stock_name") or sm.columns[1]
    sm["code"] = norm_code(sm[code_col])
    sm_listed = sm[sm["code"].str.match(r"^[0-9]{6}$", na=False)].drop_duplicates("code")
    sm_right = sm_listed[["code", name_col]].copy()
    sm_right.columns = ["code", "sm_name"]
    smj = left.merge(sm_right, on="code", how="outer", indicator=True)
    sm_name_mismatch = smj[(smj["_merge"] == "both") & (smj["stock_name"].fillna("") != smj["sm_name"].fillna(""))]
    smj[smj["_merge"] == "left_only"].to_csv(out / "stock_master_pg_only.csv", index=False)
    smj[smj["_merge"] == "right_only"].to_csv(out / "stock_master_stock_master_only.csv", index=False)
    sm_name_mismatch.to_csv(out / "stock_master_name_mismatch.csv", index=False)

    return {
        "pg_stock_universe_rows": int(len(pg_uni)),
        "pg_stock_universe_common_numeric": int(len(pg_common)),
        "kr_company_registry": {
            "rows": int(len(kr)),
            "listed_numeric_rows": int(len(kr_listed)),
            "extracted_at": sorted(kr["extracted_at"].dropna().unique().tolist())[:3],
            "matched": int((reg["_merge"] == "both").sum()),
            "pg_only": int((reg["_merge"] == "left_only").sum()),
            "registry_only": int((reg["_merge"] == "right_only").sum()),
            "market_mismatch": int(len(market_mismatch)),
            "name_mismatch": int(len(name_mismatch)),
        },
        "finance_data_stock_master": {
            "rows": int(len(sm)),
            "numeric_codes": int(len(sm_listed)),
            "matched": int((smj["_merge"] == "both").sum()),
            "pg_only": int((smj["_merge"] == "left_only").sum()),
            "stock_master_only": int((smj["_merge"] == "right_only").sum()),
            "name_mismatch": int(len(sm_name_mismatch)),
        },
    }


def compare_prices(paths: dict[str, Path], pg_price_20261001: pd.DataFrame, pg_latest: pd.DataFrame, meta: pd.DataFrame, out: Path) -> dict:
    q = json.loads(paths["aik_quotes_min.json"].read_text(encoding="utf-8"))
    aik = pd.DataFrame(q["rows"], columns=q["columns"])
    aik["code"] = norm_code(aik["c"])
    aik["clpr"] = pd.to_numeric(aik["clpr"], errors="coerce")
    pg_price_20261001["code"] = norm_code(pg_price_20261001["stock_code"])
    price = aik[["code", "clpr", "mrktTotAmt"]].merge(
        pg_price_20261001[["code", "date", "close", "volume"]], on="code", how="outer", indicator=True
    )
    price["abs_diff"] = (price["close"] - price["clpr"]).abs()
    price["pct_diff"] = price["abs_diff"] / price["clpr"].abs().replace(0, pd.NA)
    mism = price[(price["_merge"] == "both") & (price["abs_diff"].fillna(0) > 0.5)].copy()
    price[price["_merge"] == "left_only"].to_csv(out / "aik_quotes_aik_only_20261001.csv", index=False)
    price[price["_merge"] == "right_only"].to_csv(out / "aik_quotes_pg_only_20261001.csv", index=False)
    mism.to_csv(out / "aik_quotes_close_mismatch_20261001.csv", index=False)

    pg_latest["code"] = norm_code(pg_latest["stock_code"])
    fresh = aik[["code", "clpr"]].merge(pg_latest[["code", "date", "close", "volume"]], on="code", how="outer", indicator=True)
    stale = fresh[
        (fresh["_merge"] == "both")
        & (~fresh["date"].astype(str).isin(["20261001", "2026-10-01", "20261002", "2026-10-02"]))
    ].copy()
    meta["stock_code"] = norm_code(meta["stock_code"])
    stale["stock_code"] = norm_code(stale["code"])
    stale = stale.merge(meta, on="stock_code", how="left")
    stale["classification"] = stale.apply(
        lambda r: "very_stale_possible_delisted_or_ticker_identity"
        if str(r.get("date", "")).startswith("2018")
        else ("recent_no_volume_or_suspended_review" if float(r.get("volume", 0) or 0) == 0 else "recent_stale_price_review"),
        axis=1,
    )
    stale.to_csv(out / "aik_quotes_pg_latest_stale_classified.csv", index=False)
    stale_summary = stale.groupby("classification").agg(rows=("code", "count"), codes=("code", "nunique")).reset_index()
    stale_summary.to_csv(out / "aik_quotes_pg_latest_stale_summary.csv", index=False)

    return {
        "generated_kst": q.get("generated_kst"),
        "as_of_iso": q.get("as_of_iso"),
        "rows": int(len(aik)),
        "matched_on_20261001": int((price["_merge"] == "both").sum()),
        "aik_only": int((price["_merge"] == "left_only").sum()),
        "pg_only_20261001": int((price["_merge"] == "right_only").sum()),
        "close_mismatch_count": int(len(mism)),
        "latest_stale_count_excluding_20261001_20261002": int(len(stale)),
        "stale_summary": stale_summary.to_dict(orient="records"),
    }


def compare_earnings(paths: dict[str, Path], pg_fin: pd.DataFrame, meta: pd.DataFrame, quirks: pd.DataFrame, out: Path) -> dict:
    items = json.loads(paths["aik_earnings.json"].read_text(encoding="utf-8"))
    rows = []
    for item in items.get("items", []):
        fin = item.get("fin") or {}
        if item.get("parse_status") != "ok" or item.get("period") != "2026.06" or not fin.get("누적", False):
            continue
        basis = fin.get("기준") or item.get("basis")
        if basis not in ("연결", "별도") or item.get("is_subsidiary_filing"):
            continue
        def current(key: str):
            value = fin.get(key) or {}
            return value.get("current") if isinstance(value, dict) else None
        rows.append({
            "code": str(item.get("code")).zfill(6),
            "name": item.get("name"),
            "basis": basis,
            "report_type": "CFS" if basis == "연결" else "OFS",
            "rcept_dt": item.get("rcept_dt"),
            "rcept_no": item.get("rcept_no"),
            "aik_revenue": current("revenue"),
            "aik_operating_profit": current("operating_income"),
            "aik_net_income": current("net_income"),
        })
    aik = pd.DataFrame(rows).sort_values(["code", "report_type", "rcept_dt", "rcept_no"]).drop_duplicates(["code", "report_type"], keep="last")
    agg = pg_fin[pg_fin["report_type"].isin(["CFS", "OFS"])].groupby(["stock_code", "report_type"], as_index=False).agg({
        "revenue": "sum",
        "operating_profit": "sum",
        "net_income": "sum",
        "data_source": lambda x: ";".join(map(str, x)),
    })
    comp = aik.merge(agg, left_on=["code", "report_type"], right_on=["stock_code", "report_type"], how="left")
    long = []
    for _, row in comp.iterrows():
        for pg_field, aik_field in [("revenue", "aik_revenue"), ("operating_profit", "aik_operating_profit"), ("net_income", "aik_net_income")]:
            av, pv = row.get(aik_field), row.get(pg_field)
            if pd.isna(av) or pd.isna(pv):
                status, diff, pct = "missing", None, None
            else:
                diff = float(pv) - float(av)
                pct = abs(diff) / max(abs(float(av)), 1.0)
                status = "ok" if abs(diff) <= max(1_000_000.0, abs(float(av)) * 0.005) else "mismatch"
            long.append({
                "code": row["code"], "name": row["name"], "report_type": row["report_type"], "basis": row["basis"],
                "field": pg_field, "aik_value": av, "pg_value": pv, "diff": diff, "pct_diff": pct, "status": status,
                "rcept_dt": row["rcept_dt"], "rcept_no": row["rcept_no"], "pg_sources": row.get("data_source"),
            })
    long = pd.DataFrame(long)
    long.to_csv(out / "aik_earnings_2026h1_vs_pg_q1q2_long.csv", index=False)
    mismatch = long[long["status"] == "mismatch"].copy()
    missing = long[long["status"] == "missing"].copy()

    meta["stock_code"] = norm_code(meta["stock_code"])
    qmap = {}
    if not quirks.empty:
        quirks = quirks.copy()
        quirks["quirk_pair"] = quirks["config_key"].astype(str) + "=" + quirks["config_value"].astype(str)
        qmap = quirks.groupby("stock_code")["quirk_pair"].apply(lambda s: ";".join(sorted(set(s)))).to_dict()
    for frame in (mismatch, missing):
        frame["stock_code"] = norm_code(frame["code"])
        frame["fs_quirks"] = frame["stock_code"].map(qmap).fillna("")
    def classify(row) -> str:
        code = str(row["stock_code"])
        pct = float(row["pct_diff"]) if pd.notna(row["pct_diff"]) else 0.0
        av = abs(float(row["aik_value"])) if pd.notna(row["aik_value"]) else 0.0
        pv = abs(float(row["pg_value"])) if pd.notna(row["pg_value"]) else 0.0
        ratio = max(av, pv) / max(min(av, pv), 1.0) if max(av, pv) else 0.0
        if "fs_quirk:reporting_currency" in str(row.get("fs_quirks", "")) or code.startswith(("900", "950")) or ratio > 50:
            return "currency_or_foreign_issuer_priority"
        if row["field"] == "net_income" and pct < 0.05:
            return "net_income_definition_or_rounding"
        if pct < 0.01:
            return "rounding_or_unit_tolerance_review"
        if row["report_type"] == "OFS":
            return "ofs_mapping_or_source_basis_review"
        return "financial_mapping_review"
    mismatch["classification"] = mismatch.apply(classify, axis=1)
    missing["classification"] = missing.apply(
        lambda r: "foreign_or_universe_gap" if str(r["stock_code"]).startswith(("900", "950"))
        else ("known_quirk_gap" if r["fs_quirks"] else "pg_financial_missing_or_basis_gap"),
        axis=1,
    )
    mismatch.to_csv(out / "aik_earnings_2026h1_mismatches_classified.csv", index=False)
    missing.to_csv(out / "aik_earnings_2026h1_missing_classified.csv", index=False)
    class_summary = mismatch.groupby("classification").agg(fields=("field", "count"), codes=("stock_code", "nunique")).reset_index()
    missing_summary = missing.groupby("classification").agg(fields=("field", "count"), codes=("stock_code", "nunique")).reset_index()
    class_summary.to_csv(out / "aik_earnings_2026h1_classification_summary.csv", index=False)
    missing_summary.to_csv(out / "aik_earnings_2026h1_missing_summary.csv", index=False)

    return {
        "source_items": int(items.get("count", 0)),
        "filtered_latest_code_basis_rows": int(len(aik)),
        "compared_fields_total": int(len(long)),
        "ok_fields": int((long["status"] == "ok").sum()),
        "mismatch_fields": int((long["status"] == "mismatch").sum()),
        "missing_fields": int((long["status"] == "missing").sum()),
        "mismatch_class_summary": class_summary.to_dict(orient="records"),
        "missing_class_summary": missing_summary.to_dict(orient="records"),
    }


def compare_stock_json_all(stock_json_dir: Path, pg_uni: pd.DataFrame, pg_fin: pd.DataFrame, out: Path) -> dict:
    """Compare per-stock aikstockdata JSON files against PostgreSQL Q1+Q2 sums."""
    pg_codes = pg_uni[["stock_code", "stock_name", "market", "stock_type"]].copy()
    pg_codes["stock_code"] = norm_code(pg_codes["stock_code"])
    pg_base = pg_fin[pg_fin["report_type"].isin(["CFS", "OFS"])].copy()
    h1_agg = pg_base.groupby(["stock_code", "report_type"], as_index=False).agg({
        "revenue": "sum",
        "operating_profit": "sum",
        "net_income": "sum",
        "data_source": lambda x: ";".join(map(str, x)),
    })
    q1_agg = pg_base[pg_base["quarter"].astype(str).isin(["1", "1.0"])].groupby(["stock_code", "report_type"], as_index=False).agg({
        "revenue": "sum",
        "operating_profit": "sum",
        "net_income": "sum",
        "data_source": lambda x: ";".join(map(str, x)),
    })
    q2_agg = pg_base[pg_base["quarter"].astype(str).isin(["2", "2.0"])].groupby(["stock_code", "report_type"], as_index=False).agg({
        "revenue": "sum",
        "operating_profit": "sum",
        "net_income": "sum",
        "data_source": lambda x: ";".join(map(str, x)),
    })
    rows = []
    price_rows = []
    parse_errors = []
    for _, row in pg_codes.iterrows():
        code = row["stock_code"]
        path = stock_json_dir / f"{code}.json"
        base = {"code": code, "stock_name_pg": row.get("stock_name"), "market_pg": row.get("market")}
        if not path.exists() or path.stat().st_size == 0:
            rows.append({**base, "financial_status": "json_missing"})
            price_rows.append({**base, "price_status": "json_missing"})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_errors.append({"code": code, "error": str(exc)[:200]})
            rows.append({**base, "financial_status": "json_parse_error"})
            continue
        quote = payload.get("quote") or {}
        price_rows.append({
            **base,
            "name_json": payload.get("name_ko"),
            "market_json": payload.get("market"),
            "price_status": "ok",
            "aik_close": quote.get("close"),
            "aik_volume": quote.get("volume"),
            "aik_as_of": quote.get("as_of"),
            "quote_absent": payload.get("quote_absent"),
        })
        fs = payload.get("financials") or {}
        if not fs:
            rows.append({
                **base,
                "name_json": payload.get("name_ko"),
                "market_json": payload.get("market"),
                "basis": None,
                "period": None,
                "report_type": None,
                "financial_status": "financials_missing",
            })
            continue
        basis = fs.get("basis")
        period = fs.get("period")
        report_type = "CFS" if basis == "연결" else ("OFS" if basis == "별도" else None)
        if period == "2026Q1":
            period_basis = "pg_q1"
            compare_frame = q1_agg
        elif period == "2026Q2":
            period_basis = "pg_q2"
            compare_frame = q2_agg
        else:
            period_basis = "pg_q1_plus_q2"
            compare_frame = h1_agg
        pg_match = compare_frame[(compare_frame["stock_code"] == code) & (compare_frame["report_type"] == report_type)] if report_type else pd.DataFrame()
        pg_row = pg_match.iloc[0].to_dict() if not pg_match.empty else {}

        def current(section: str):
            value = fs.get(section) or {}
            return value.get("current") if isinstance(value, dict) else None

        for field, aik_value in {
            "revenue": current("revenue"),
            "operating_profit": current("operating_income"),
            "net_income": current("net_income"),
        }.items():
            pg_value = pg_row.get(field)
            if aik_value is None:
                compare_status, diff, pct = "aik_field_missing", None, None
            elif pg_value is None or pd.isna(pg_value):
                compare_status, diff, pct = "pg_missing", None, None
            else:
                diff = float(pg_value) - float(aik_value)
                pct = abs(diff) / max(abs(float(aik_value)), 1.0)
                compare_status = "ok" if abs(diff) <= max(1_000_000.0, abs(float(aik_value)) * 0.005) else "mismatch"
            rows.append({
                **base,
                "name_json": payload.get("name_ko"),
                "market_json": payload.get("market"),
                "basis": basis,
                "period": period,
                "period_compare_basis": period_basis,
                "report_type": report_type,
                "financial_status": "ok",
                "field": field,
                "aik_value": aik_value,
                "pg_value": pg_value,
                "diff": diff,
                "pct_diff": pct,
                "compare_status": compare_status,
                "pg_sources": pg_row.get("data_source"),
            })

    fin_long = pd.DataFrame(rows)
    price_all = pd.DataFrame(price_rows)
    fin_long.to_csv(out / "aik_stock_json_all_financial_vs_pg_q1q2_long.csv", index=False)
    price_all.to_csv(out / "aik_stock_json_all_price_fields.csv", index=False)
    pd.DataFrame(parse_errors).to_csv(out / "aik_stock_json_all_parse_errors.csv", index=False)

    compare_rows = fin_long[fin_long.get("field").notna()] if "field" in fin_long else pd.DataFrame()
    mismatches = compare_rows[compare_rows["compare_status"] == "mismatch"].copy() if not compare_rows.empty else pd.DataFrame()
    if not mismatches.empty:
        def classify(row) -> str:
            code = str(row["code"])
            pct = float(row["pct_diff"]) if pd.notna(row["pct_diff"]) else 0.0
            av = abs(float(row["aik_value"])) if pd.notna(row["aik_value"]) else 0.0
            pv = abs(float(row["pg_value"])) if pd.notna(row["pg_value"]) else 0.0
            ratio = max(av, pv) / max(min(av, pv), 1.0) if max(av, pv) else 0.0
            if code.startswith(("900", "950")) or ratio > 50:
                return "currency_or_foreign_issuer_priority"
            if row["field"] == "net_income" and pct < 0.05:
                return "net_income_definition_or_rounding"
            if row["report_type"] == "OFS":
                return "ofs_mapping_or_source_basis_review"
            if pct < 0.01:
                return "rounding_or_unit_tolerance_review"
            return "financial_mapping_review"
        mismatches["classification"] = mismatches.apply(classify, axis=1)
    mismatches.to_csv(out / "aik_stock_json_all_financial_mismatches_classified.csv", index=False)
    class_summary = (
        mismatches.groupby("classification").agg(fields=("field", "count"), codes=("code", "nunique")).reset_index()
        if not mismatches.empty
        else pd.DataFrame(columns=["classification", "fields", "codes"])
    )
    class_summary.to_csv(out / "aik_stock_json_all_financial_classification_summary.csv", index=False)
    status_summary = pd.DataFrame({
        "status": ["json_present", "json_missing"],
        "codes": [int(sum((stock_json_dir / f"{code}.json").exists() for code in pg_codes["stock_code"])), int(sum(not (stock_json_dir / f"{code}.json").exists() for code in pg_codes["stock_code"]))],
    })
    status_summary.to_csv(out / "aik_stock_json_all_status_summary.csv", index=False)
    return {
        "pg_stock_universe_rows": int(len(pg_codes)),
        "json_present": int(status_summary.loc[status_summary["status"] == "json_present", "codes"].iloc[0]),
        "json_missing": int(status_summary.loc[status_summary["status"] == "json_missing", "codes"].iloc[0]),
        "stocks_with_financials": int(fin_long[fin_long["financial_status"].eq("ok")]["code"].nunique()) if "financial_status" in fin_long else 0,
        "stocks_financials_missing": int(fin_long[fin_long["financial_status"].eq("financials_missing")]["code"].nunique()) if "financial_status" in fin_long else 0,
        "compared_fields": int(len(compare_rows)),
        "ok_fields": int((compare_rows["compare_status"] == "ok").sum()) if not compare_rows.empty else 0,
        "mismatch_fields": int((compare_rows["compare_status"] == "mismatch").sum()) if not compare_rows.empty else 0,
        "pg_missing_fields": int((compare_rows["compare_status"] == "pg_missing").sum()) if not compare_rows.empty else 0,
        "aik_field_missing": int((compare_rows["compare_status"] == "aik_field_missing").sum()) if not compare_rows.empty else 0,
        "mismatch_class_summary": class_summary.to_dict(orient="records"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("/tmp/third_party_crosscheck_inputs"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stock-json-dir", type=Path, default=Path("/tmp/aik_stock_json_all"))
    parser.add_argument("--fetch", action="store_true", help="download public input files")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    paths = ensure_inputs(args.input_dir, args.fetch)
    db_url = read_env_url()
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        psql_copy(db_url, "select stock_code, stock_name, market, stock_type, isin_code, kind_stkcert_nm, base_date, close, market_cap, shares_issued from stock_universe where stock_code ~ '^[0-9A-Z]{6}$'", tmp / "pg_stock_universe.csv")
        psql_copy(db_url, "select stock_code, stock_name, market, stock_type, sector_large, sector_mid from stock_universe", tmp / "pg_stock_meta.csv")
        psql_copy(db_url, "select stock_code, config_key, config_value from stock_collection_config where config_key like 'fs_quirk:%'", tmp / "pg_fs_quirks.csv")
        psql_copy(db_url, "select p.stock_code, p.date, p.close, p.volume from price_history p join (select stock_code, max(date) as date from price_history where close > 0 group by stock_code) m on p.stock_code=m.stock_code and p.date=m.date where p.stock_code ~ '^[0-9A-Z]{6}$'", tmp / "pg_price_latest.csv")
        psql_copy(db_url, "select stock_code, date, close, volume from price_history where date in ('20261001','2026-10-01') and stock_code ~ '^[0-9A-Z]{6}$'", tmp / "pg_price_20261001.csv")
        psql_copy(db_url, "select stock_code, year, quarter, report_type, is_annual, revenue, operating_profit, net_income, data_source from financial_data where year=2026 and quarter in (1,2) and is_annual=false", tmp / "pg_financial_2026_q1q2.csv")

        pg_uni = pd.read_csv(tmp / "pg_stock_universe.csv", dtype=str)
        meta = pd.read_csv(tmp / "pg_stock_meta.csv", dtype=str)
        quirks = pd.read_csv(tmp / "pg_fs_quirks.csv", dtype=str)
        summary = {
            "workspace": str(ROOT),
            "database": "PostgreSQL stock_dashboard",
        }
        summary.update(compare_identifiers(paths, pg_uni, args.out_dir))
        summary["aikstockdata_quotes_min"] = compare_prices(
            paths,
            pd.read_csv(tmp / "pg_price_20261001.csv", dtype={"stock_code": str}),
            pd.read_csv(tmp / "pg_price_latest.csv", dtype={"stock_code": str}),
            meta.copy(),
            args.out_dir,
        )
        summary["aikstockdata_earnings_2026h1_q1q2_sum"] = compare_earnings(
            paths,
            pd.read_csv(tmp / "pg_financial_2026_q1q2.csv", dtype={"stock_code": str}),
            meta.copy(),
            quirks,
            args.out_dir,
        )
        if args.stock_json_dir.exists():
            summary["aikstockdata_stock_json_all"] = compare_stock_json_all(
                args.stock_json_dir,
                pg_uni.copy(),
                pd.read_csv(tmp / "pg_financial_2026_q1q2.csv", dtype={"stock_code": str}),
                args.out_dir,
            )
        summary["artifacts"] = [p.name for p in sorted(args.out_dir.iterdir())]
        (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
