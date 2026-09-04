#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "research_outputs" / "tenbagger_survivorship_bias_20260811.json"
OUT_MD = ROOT / "research_outputs" / "tenbagger_survivorship_bias_20260811.md"
sys.path.insert(0, str(ROOT))

from database import engine  # noqa: E402


def _load_missing_historical_equities() -> pd.DataFrame:
    return pd.read_sql_query(
        text(
            """
            WITH snapped AS (
                SELECT DISTINCT stock_code FROM strategy_feature_snapshot
            )
            SELECT m.stock_code, m.stock_name, m.market,
                   m.effective_from, m.effective_to, m.interval_quality, m.source
            FROM security_master_history m
            LEFT JOIN snapped s ON s.stock_code=m.stock_code
            WHERE m.is_tradable=1
              AND m.is_etf_etn=0
              AND m.market IN ('KOSPI','KOSDAQ')
              AND s.stock_code IS NULL
            ORDER BY m.stock_code
            """
        ),
        engine,
    )


def _load_prices(codes: list[str]) -> pd.DataFrame:
    query = text(
        """
        SELECT stock_code, date, close, high, low, volume
        FROM price_history
        WHERE stock_code IN :codes
          AND date >= '2018-01-01'
          AND close > 0
        ORDER BY stock_code, date, id
        """
    ).bindparams(bindparam("codes", expanding=True))
    return pd.read_sql_query(query, engine, params={"codes": codes})


def _audit_rows(metadata: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    metadata = metadata.copy()
    metadata["effective_from"] = pd.to_datetime(metadata["effective_from"], errors="coerce")
    metadata["effective_to"] = pd.to_datetime(metadata["effective_to"], errors="coerce")
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices = prices.dropna(subset=["stock_code", "date", "close"])
    meta_map = metadata.set_index("stock_code").to_dict("index")
    records: list[dict] = []
    horizon = pd.Timedelta(days=730)
    label_cutoff = pd.Timestamp("2024-07-31")

    for code, group in prices.groupby("stock_code", sort=False):
        meta = meta_map.get(code)
        if not meta:
            continue
        group = group.sort_values("date").drop_duplicates("date", keep="last").copy()
        start = meta.get("effective_from")
        end = meta.get("effective_to")
        if pd.notna(start):
            group = group[group["date"] >= start]
        if pd.notna(end):
            group = group[group["date"] <= end]
        if len(group) < 60:
            continue
        group["daily_ratio"] = group["close"] / group["close"].shift(1)
        month_rows = group.groupby(group["date"].dt.to_period("M"), sort=True).tail(1)
        dates = group["date"].to_numpy(dtype="datetime64[ns]")
        closes = group["close"].to_numpy(dtype=float)
        anomalies = group["daily_ratio"].gt(1.45) | group["daily_ratio"].lt(0.69)
        anomaly_dates = group.loc[anomalies, "date"].to_numpy(dtype="datetime64[ns]")

        for row in month_rows.itertuples(index=False):
            snapshot = pd.Timestamp(row.date)
            if snapshot < pd.Timestamp("2020-01-01") or snapshot > label_cutoff:
                continue
            horizon_end = snapshot + horizon
            left = int(np.searchsorted(dates, np.datetime64(snapshot), side="right"))
            right = int(np.searchsorted(dates, np.datetime64(horizon_end), side="right"))
            if right <= left:
                continue
            future = closes[left:right]
            peak_return = float(np.max(future) / float(row.close) - 1.0)
            anomaly_left = int(
                np.searchsorted(anomaly_dates, np.datetime64(snapshot - pd.Timedelta(days=35)), side="left")
            )
            anomaly_right = int(
                np.searchsorted(anomaly_dates, np.datetime64(horizon_end), side="right")
            )
            terminal_before_horizon = bool(pd.notna(end) and end < horizon_end)
            records.append(
                {
                    "stock_code": code,
                    "stock_name": meta.get("stock_name"),
                    "snapshot_date": snapshot,
                    "peak_return_24m": peak_return,
                    "label_3x_24m": int(peak_return >= 2.0),
                    "label_5x_24m": int(peak_return >= 4.0),
                    "label_10x_24m": int(peak_return >= 9.0),
                    "price_artifact": int(anomaly_right > anomaly_left),
                    "terminal_before_horizon": int(terminal_before_horizon),
                }
            )
    return pd.DataFrame.from_records(records)


def main() -> None:
    metadata = _load_missing_historical_equities()
    prices = _load_prices(metadata["stock_code"].astype(str).tolist())
    rows = _audit_rows(metadata, prices)
    clean = rows[rows["price_artifact"].eq(0)].copy() if not rows.empty else rows
    existing = pd.read_sql_query(
        text(
            """
            SELECT COUNT(*) AS rows,
                   SUM(CASE WHEN label_3x_24m=1 THEN 1 ELSE 0 END) AS hit_3x,
                   SUM(CASE WHEN label_5x_24m=1 THEN 1 ELSE 0 END) AS hit_5x,
                   SUM(CASE WHEN label_10x_24m=1 THEN 1 ELSE 0 END) AS hit_10x
            FROM strategy_feature_snapshot
            WHERE snapshot_date BETWEEN '2020-01-01' AND '2024-07-31'
              AND label_10x_24m IS NOT NULL
            """
        ),
        engine,
    ).iloc[0]

    missing_stats = {
        "security_master_rows": int(len(metadata)),
        "with_price_history": int(prices["stock_code"].nunique()),
        "snapshot_rows": int(len(rows)),
        "clean_snapshot_rows": int(len(clean)),
        "price_artifact_rows": int(rows["price_artifact"].sum()) if not rows.empty else 0,
        "terminal_rows": int(rows["terminal_before_horizon"].sum()) if not rows.empty else 0,
        "stocks": int(rows["stock_code"].nunique()) if not rows.empty else 0,
        "stocks_with_clean_3x": int(clean.loc[clean["label_3x_24m"].eq(1), "stock_code"].nunique()) if not clean.empty else 0,
        "stocks_with_clean_5x": int(clean.loc[clean["label_5x_24m"].eq(1), "stock_code"].nunique()) if not clean.empty else 0,
        "stocks_with_clean_10x": int(clean.loc[clean["label_10x_24m"].eq(1), "stock_code"].nunique()) if not clean.empty else 0,
        "clean_3x_row_rate_pct": round(float(clean["label_3x_24m"].mean()) * 100, 3) if not clean.empty else 0.0,
        "clean_5x_row_rate_pct": round(float(clean["label_5x_24m"].mean()) * 100, 3) if not clean.empty else 0.0,
        "clean_10x_row_rate_pct": round(float(clean["label_10x_24m"].mean()) * 100, 3) if not clean.empty else 0.0,
    }
    existing_rows = int(existing["rows"] or 0)
    combined_rows = existing_rows + len(clean)
    combined = {
        "rows": combined_rows,
        "raw_3x_rate_pct": round((int(existing["hit_3x"] or 0) + int(clean["label_3x_24m"].sum())) / combined_rows * 100, 3),
        "raw_5x_rate_pct": round((int(existing["hit_5x"] or 0) + int(clean["label_5x_24m"].sum())) / combined_rows * 100, 3),
        "raw_10x_rate_pct": round((int(existing["hit_10x"] or 0) + int(clean["label_10x_24m"].sum())) / combined_rows * 100, 3),
    } if combined_rows else {}
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "assessment": "rebuild_required" if missing_stats["stocks"] else "no_material_gap",
        "existing_snapshot": {
            "rows": existing_rows,
            "raw_3x_rate_pct": round(int(existing["hit_3x"] or 0) / existing_rows * 100, 3) if existing_rows else 0.0,
            "raw_5x_rate_pct": round(int(existing["hit_5x"] or 0) / existing_rows * 100, 3) if existing_rows else 0.0,
            "raw_10x_rate_pct": round(int(existing["hit_10x"] or 0) / existing_rows * 100, 3) if existing_rows else 0.0,
        },
        "missing_historical_equities": missing_stats,
        "combined_raw_estimate": combined,
        "decision": {
            "production_model_change_allowed": False,
            "required_action": "build a point-in-time universe snapshot including delisted equities before further threshold tuning",
        },
        "limitations": [
            "missing-equity audit measures raw price outcomes, not business-cause validated tenbaggers",
            "delisting terminal value is not modeled; only observed pre-delisting prices are used",
            "rows containing impossible daily jumps are excluded from clean rates",
        ],
    }
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_JSON)
    OUT_MD.write_text(
        "\n".join(
            [
                "# 텐버거 생존편향 감사 (2026-08-11)",
                "",
                f"- 판정: `{payload['assessment']}`",
                f"- 기존 스냅샷 누락 과거 주식: {missing_stats['security_master_rows']:,}종목",
                f"- 가격 보유: {missing_stats['with_price_history']:,}종목",
                f"- 생성 가능한 정상 월말 표본: {missing_stats['clean_snapshot_rows']:,}행",
                f"- 가격 이상 포함 표본: {missing_stats['price_artifact_rows']:,}행",
                f"- 정상 10배 이력 종목: {missing_stats['stocks_with_clean_10x']:,}종목",
                "",
                "## 결론",
                "",
                "현재 상장 종목만 사용한 기존 스냅샷은 생존편향이 있으므로 추가 임계값 튜닝을 중단한다. "
                "상장기간 기준 과거 종목을 포함한 point-in-time 데이터셋을 별도 구축·검증한 뒤에만 로직을 승격한다.",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
