"""
전략 센터 연구 데이터셋 생성기

목표:
1. 월별 feature snapshot 생성
2. 6개월/12개월 2배·3배 라벨 생성
3. 휴리스틱 점수와 간단한 ML 확률 점수 생성
4. 비교 리포트 JSON 저장
"""
from __future__ import annotations

import json
import math
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DB_PATH = "/Applications/stock_dashboard/stock.db"
OUTPUT_JSON = "/Applications/stock_dashboard/research_outputs/strategy_research_summary.json"
SNAPSHOT_TABLE = "strategy_feature_snapshot"

FEATURE_COLUMNS = [
    "market_cap_log",
    "pbr",
    "per",
    "ret_20d",
    "ret_60d",
    "ret_120d",
    "dist_high_252",
    "dist_low_252",
    "vol_ratio_20d",
    "supply_20d_억",
]


@dataclass
class LogisticModel:
    weights: np.ndarray
    mean: np.ndarray
    std: np.ndarray

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        if frame.empty:
            return np.array([])
        x = frame[FEATURE_COLUMNS].to_numpy(dtype=float)
        x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
        xs = (x - self.mean) / self.std
        xb = np.c_[np.ones(len(xs)), xs]
        z = np.clip(xb @ self.weights, -30, 30)
        return 1.0 / (1.0 + np.exp(-z))


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=120)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {SNAPSHOT_TABLE} (
            snapshot_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            close_price REAL,
            market_cap_억 REAL,
            market_cap_log REAL,
            per REAL,
            pbr REAL,
            ret_20d REAL,
            ret_60d REAL,
            ret_120d REAL,
            dist_high_252 REAL,
            dist_low_252 REAL,
            vol_ratio_20d REAL,
            avg_turnover_20d_억 REAL,
            supply_20d_억 REAL,
            heuristic_score REAL,
            forward_max_ret_6m REAL,
            forward_max_ret_12m REAL,
            label_2x_6m INTEGER,
            label_3x_6m INTEGER,
            label_2x_12m INTEGER,
            label_3x_12m INTEGER,
            model_score_6m REAL,
            model_score_12m REAL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (snapshot_date, stock_code)
        )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{SNAPSHOT_TABLE}_date ON {SNAPSHOT_TABLE}(snapshot_date)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{SNAPSHOT_TABLE}_score12 ON {SNAPSHOT_TABLE}(model_score_12m DESC)")
    conn.commit()


def _month_end_dates(price: pd.DataFrame, start_date: str) -> list[pd.Timestamp]:
    dates = pd.to_datetime(price["date"], format="mixed", errors="coerce")
    base = pd.DataFrame({"date": dates}).drop_duplicates().sort_values("date")
    base["ym"] = base["date"].dt.to_period("M")
    month_end = base.groupby("ym")["date"].max().tolist()
    month_end = [pd.Timestamp(d) for d in month_end if str(d.date()) >= start_date]
    latest_date = dates.max()
    if latest_date not in month_end:
        month_end.append(pd.Timestamp(latest_date))
    return sorted(set(month_end))


def _load_source_frames(start_price_date: str = "2018-01-01") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    conn = _connect()
    price = pd.read_sql_query("""
        SELECT p.stock_code, p.date, p.close, p.high, p.low, COALESCE(p.volume, 0) AS volume,
               COALESCE(p.inst_net_buy_amt, 0) AS inst_net_buy_amt,
               COALESCE(p.frn_net_buy_amt, 0) AS frn_net_buy_amt,
               su.stock_name, su.market, su.sector_large, su.market_cap
        FROM price_history p
        JOIN stock_universe su ON su.stock_code = p.stock_code
        WHERE p.date >= ?
          AND p.close > 0
          AND LENGTH(p.stock_code) = 6
          AND p.stock_code GLOB '[0-9]*'
          AND su.market IN ('KOSPI', 'KOSDAQ')
        ORDER BY p.stock_code, p.date
    """, conn, params=(start_price_date,))
    valuation = pd.read_sql_query("""
        SELECT stock_code, period_end, per, pbr, market_cap_억
        FROM valuation_history
        WHERE period_end IS NOT NULL
        ORDER BY stock_code, period_end
    """, conn)
    conn.close()
    return price, valuation, pd.DataFrame()


def _heuristic_score(row: pd.Series) -> float:
    score = 0.0
    dd = row["dist_high_252"]
    if pd.notna(dd):
        if -0.70 <= dd <= -0.30:
            score += 35.0
        elif -0.85 <= dd < -0.70:
            score += 28.0
        elif -0.30 < dd <= -0.15:
            score += 18.0
    pbr = row["pbr"]
    if pd.notna(pbr) and pbr > 0:
        if pbr <= 0.8:
            score += 20.0
        elif pbr <= 1.2:
            score += 12.0
        elif pbr <= 1.8:
            score += 6.0
    ret60 = row["ret_60d"]
    ret20 = row["ret_20d"]
    if pd.notna(ret60):
        if -0.15 <= ret60 <= 0.35:
            score += 10.0
        elif 0.35 < ret60 <= 0.8:
            score += 5.0
    if pd.notna(ret20) and ret20 > 0:
        score += 6.0
    vol_ratio = row["vol_ratio_20d"]
    if pd.notna(vol_ratio):
        if vol_ratio >= 1.8:
            score += 12.0
        elif vol_ratio >= 1.3:
            score += 7.0
    supply = row["supply_20d_억"]
    if pd.notna(supply):
        if supply >= 80:
            score += 12.0
        elif supply >= 20:
            score += 8.0
        elif supply > 0:
            score += 4.0
    mcap = row["market_cap_억"]
    if pd.notna(mcap) and 200 <= mcap <= 3000:
        score += 8.0
    elif pd.notna(mcap) and 3000 < mcap <= 10000:
        score += 4.0
    low = row["dist_low_252"]
    if pd.notna(low):
        if low <= 0.15:
            score += 8.0
        elif low <= 0.35:
            score += 4.0
    return float(min(100.0, max(0.0, score)))


def _fit_logistic(frame: pd.DataFrame, label_col: str, lr: float = 0.08, epochs: int = 500) -> LogisticModel:
    train = frame.dropna(subset=[label_col]).copy()
    x = train[FEATURE_COLUMNS].to_numpy(dtype=float)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    y = train[label_col].astype(float).to_numpy()
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std == 0] = 1.0
    xs = (x - mean) / std
    xb = np.c_[np.ones(len(xs)), xs]
    w = np.zeros(xb.shape[1], dtype=float)
    pos = max(float(y.sum()), 1.0)
    neg = max(float(len(y) - y.sum()), 1.0)
    pos_weight = neg / pos
    sample_weight = np.where(y == 1.0, pos_weight, 1.0)
    l2 = 0.0008
    for _ in range(epochs):
        z = np.clip(xb @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = (xb.T @ ((p - y) * sample_weight)) / len(y)
        grad[1:] += l2 * w[1:]
        w -= lr * grad
    return LogisticModel(weights=w, mean=mean, std=std)


def _evaluate_monthly_topk(frame: pd.DataFrame, score_col: str, label_col: str, k: int) -> dict:
    picks = []
    for snapshot_date, grp in frame.groupby("snapshot_date"):
        top = grp.sort_values(score_col, ascending=False).head(k)
        if top.empty:
            continue
        picks.append({
            "snapshot_date": snapshot_date,
            "precision": float(top[label_col].mean()),
            "avg_forward_ret_12m": float(top["forward_max_ret_12m"].mean()),
            "positive_count": int(top[label_col].sum()),
            "count": int(len(top)),
        })
    if not picks:
        return {"months": 0, "precision": 0.0, "avg_forward_ret_12m": 0.0}
    return {
        "months": len(picks),
        "precision": round(float(np.mean([p["precision"] for p in picks])), 4),
        "avg_forward_ret_12m": round(float(np.mean([p["avg_forward_ret_12m"] for p in picks])), 4),
    }


def _coerce_record(value) -> float | None:
    if pd.isna(value):
        return None
    return float(value)


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def build_strategy_research_dataset(
    start_snapshot_date: str = "2020-01-31",
    train_end_date: str = "2024-06-30",
) -> dict:
    price, valuation, _ = _load_source_frames()
    if price.empty:
        raise RuntimeError("price_history source is empty")

    price["date"] = pd.to_datetime(price["date"], format="mixed", errors="coerce")
    valuation["period_end"] = pd.to_datetime(valuation["period_end"], format="mixed", errors="coerce")
    price = price.dropna(subset=["date"]).copy()
    valuation = valuation.dropna(subset=["period_end"]).copy()
    month_end_dates = _month_end_dates(price, start_snapshot_date)

    records: list[dict] = []
    valuation_groups = {
        code: grp.sort_values("period_end").reset_index(drop=True)
        for code, grp in valuation.groupby("stock_code", sort=False)
    }

    for stock_code, grp in price.groupby("stock_code", sort=False):
        grp = grp.sort_values("date").reset_index(drop=True)
        grp = grp.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
        close = grp["close"]
        volume = grp["volume"]
        high = grp["high"]
        low = grp["low"]
        supply = (grp["inst_net_buy_amt"] + grp["frn_net_buy_amt"]) / 100.0

        grp["ret_20d"] = close / close.shift(20) - 1.0
        grp["ret_60d"] = close / close.shift(60) - 1.0
        grp["ret_120d"] = close / close.shift(120) - 1.0
        grp["high_252"] = high.rolling(252, min_periods=60).max()
        grp["low_252"] = low.rolling(252, min_periods=60).min()
        grp["dist_high_252"] = close / grp["high_252"] - 1.0
        grp["dist_low_252"] = close / grp["low_252"] - 1.0
        grp["vol_ratio_20d"] = volume / volume.rolling(20, min_periods=10).mean()
        grp["avg_turnover_20d_억"] = (
            (close * volume).rolling(20, min_periods=10).mean() / 100_000_000.0
        )
        grp["supply_20d_억"] = supply.rolling(20, min_periods=10).sum()

        grp = grp.set_index("date")
        sampled = grp.reindex(month_end_dates, method="ffill")
        sampled = sampled.dropna(subset=["close"]).reset_index().rename(
            columns={"index": "snapshot_date", "date": "snapshot_date"}
        )
        if sampled.empty:
            continue

        price_dates = grp.index.to_list()
        price_values = grp["close"].to_numpy(dtype=float)
        val_grp = valuation_groups.get(stock_code)
        val_dates: list[pd.Timestamp] = []
        val_pbr: list[float] = []
        val_per: list[float] = []
        val_mcap: list[float] = []
        if val_grp is not None and not val_grp.empty:
            val_dates = val_grp["period_end"].to_list()
            val_pbr = val_grp["pbr"].fillna(np.nan).to_list()
            val_per = val_grp["per"].fillna(np.nan).to_list()
            val_mcap = val_grp["market_cap_억"].fillna(np.nan).to_list()

        for _, row in sampled.iterrows():
            snapshot_date = pd.Timestamp(row["snapshot_date"])
            pos = bisect_right(price_dates, snapshot_date) - 1
            if pos < 0:
                continue

            fwd_6m = None
            fwd_12m = None
            if pos + 1 < len(price_values):
                max_6m = float(np.max(price_values[pos + 1:min(len(price_values), pos + 1 + 126)]))
                max_12m = float(np.max(price_values[pos + 1:min(len(price_values), pos + 1 + 252)]))
                cur_close = float(row["close"])
                if cur_close > 0:
                    fwd_6m = max_6m / cur_close - 1.0
                    fwd_12m = max_12m / cur_close - 1.0

            pbr = per = mcap_hist = np.nan
            if val_dates:
                vpos = bisect_right(val_dates, snapshot_date) - 1
                if vpos >= 0:
                    pbr = val_pbr[vpos]
                    per = val_per[vpos]
                    mcap_hist = val_mcap[vpos]

            market_cap = mcap_hist if pd.notna(mcap_hist) else row.get("market_cap", np.nan)
            record = {
                "snapshot_date": snapshot_date.strftime("%Y-%m-%d"),
                "stock_code": stock_code,
                "stock_name": row.get("stock_name"),
                "market": row.get("market"),
                "sector_large": row.get("sector_large"),
                "close_price": float(row["close"]),
                "market_cap_억": _coerce_record(market_cap),
                "market_cap_log": math.log1p(max(float(market_cap), 0.0)) if pd.notna(market_cap) else None,
                "per": _coerce_record(per),
                "pbr": _coerce_record(pbr),
                "ret_20d": _coerce_record(row.get("ret_20d")),
                "ret_60d": _coerce_record(row.get("ret_60d")),
                "ret_120d": _coerce_record(row.get("ret_120d")),
                "dist_high_252": _coerce_record(row.get("dist_high_252")),
                "dist_low_252": _coerce_record(row.get("dist_low_252")),
                "vol_ratio_20d": _coerce_record(row.get("vol_ratio_20d")),
                "avg_turnover_20d_억": _coerce_record(row.get("avg_turnover_20d_억")),
                "supply_20d_억": _coerce_record(row.get("supply_20d_억")),
                "forward_max_ret_6m": _coerce_record(fwd_6m),
                "forward_max_ret_12m": _coerce_record(fwd_12m),
            }
            record["label_2x_6m"] = int(fwd_6m is not None and fwd_6m >= 1.0)
            record["label_3x_6m"] = int(fwd_6m is not None and fwd_6m >= 2.0)
            record["label_2x_12m"] = int(fwd_12m is not None and fwd_12m >= 1.0)
            record["label_3x_12m"] = int(fwd_12m is not None and fwd_12m >= 2.0)
            record["heuristic_score"] = _heuristic_score(pd.Series(record))
            records.append(record)

    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise RuntimeError("snapshot frame is empty")

    for col in FEATURE_COLUMNS:
        if col not in frame.columns:
            frame[col] = np.nan
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"])

    label_12m_ready = frame["snapshot_date"] <= (price["date"].max() - pd.Timedelta(days=365))
    label_6m_ready = frame["snapshot_date"] <= (price["date"].max() - pd.Timedelta(days=183))
    frame.loc[~label_12m_ready, ["forward_max_ret_12m", "label_2x_12m", "label_3x_12m"]] = np.nan
    frame.loc[~label_6m_ready, ["forward_max_ret_6m", "label_2x_6m", "label_3x_6m"]] = np.nan

    train_mask = frame["snapshot_date"] <= pd.Timestamp(train_end_date)
    test_mask = label_12m_ready & (frame["snapshot_date"] > pd.Timestamp(train_end_date))
    train_frame = frame.loc[train_mask & frame["label_3x_12m"].notna()].copy()

    model_12m = _fit_logistic(train_frame, "label_3x_12m")
    frame["model_score_12m"] = model_12m.predict_proba(frame)

    train_frame_6m = frame.loc[train_mask & frame["label_3x_6m"].notna()].copy()
    model_6m = _fit_logistic(train_frame_6m, "label_3x_6m")
    frame["model_score_6m"] = model_6m.predict_proba(frame)
    test_frame = frame.loc[test_mask & frame["label_3x_12m"].notna()].copy()

    latest_snapshot_date = frame["snapshot_date"].max()
    latest_snapshot = frame.loc[frame["snapshot_date"] == latest_snapshot_date].copy()
    latest_snapshot = latest_snapshot.sort_values("model_score_12m", ascending=False)

    # 유동성/품질 필터: 거래정지·관리종목·극소형주·PBR누락·메가캡·극단PBR·이미급등 제거
    liquid_mask = (
        (latest_snapshot["close_price"].fillna(0) > 0) &
        (latest_snapshot["vol_ratio_20d"].fillna(0) > 0.01) &    # 거래량 거의 없는 종목 제거
        (latest_snapshot["market_cap_억"].fillna(0) >= 100) &     # 시총 100억+ (억원 단위)
        (latest_snapshot["market_cap_억"].fillna(0) <= 100_000) & # 10조 이하 (메가캡 3배 불가)
        (latest_snapshot["pbr"].fillna(0) > 0) &                 # PBR NULL 제거 (NaN→0 왜곡 방지)
        (latest_snapshot["pbr"].fillna(999) < 30) &              # PBR 30 초과 = 음의자본/생물공학 왜곡 제거
        (latest_snapshot["ret_60d"].fillna(999) <= 2.0)          # 60일 200% 이상 급등은 이미 선반영
    )
    latest_snap_liquid = latest_snapshot.loc[liquid_mask]

    report = {
        "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_snapshot_date": latest_snapshot_date.strftime("%Y-%m-%d"),
        "dataset": {
            "snapshot_rows": int(len(frame)),
            "snapshot_months": int(frame["snapshot_date"].nunique()),
            "stocks_covered": int(frame["stock_code"].nunique()),
            "label_3x_6m_rows": int(frame["label_3x_6m"].notna().sum()),
            "label_3x_12m_rows": int(frame["label_3x_12m"].notna().sum()),
            "train_rows_12m": int(len(train_frame)),
            "test_rows_12m": int(len(test_frame)),
        },
        "evaluation": {
            "target": "12개월 내 3배(최대수익률 +200% 이상)",
            "train_end_date": train_end_date,
            "heuristic_top10": _evaluate_monthly_topk(test_frame.assign(score= test_frame["heuristic_score"]), "score", "label_3x_12m", 10),
            "heuristic_top20": _evaluate_monthly_topk(test_frame.assign(score= test_frame["heuristic_score"]), "score", "label_3x_12m", 20),
            "ml_top10": _evaluate_monthly_topk(test_frame, "model_score_12m", "label_3x_12m", 10),
            "ml_top20": _evaluate_monthly_topk(test_frame, "model_score_12m", "label_3x_12m", 20),
        },
        "current_rankings": {
            "ml_top20": latest_snap_liquid.head(20)[[
                "stock_code", "stock_name", "sector_large", "close_price", "market_cap_억",
                "model_score_12m", "heuristic_score", "pbr", "per",
                "dist_high_252", "ret_60d", "vol_ratio_20d", "supply_20d_억",
            ]].to_dict("records"),
            "heuristic_top20": latest_snap_liquid.sort_values("heuristic_score", ascending=False).head(20)[[
                "stock_code", "stock_name", "sector_large", "close_price", "market_cap_억",
                "model_score_12m", "heuristic_score", "pbr", "per",
                "dist_high_252", "ret_60d", "vol_ratio_20d", "supply_20d_억",
            ]].to_dict("records"),
            "liquidity_filtered": int(len(latest_snapshot) - len(latest_snap_liquid)),
        },
    }

    persist = frame.copy()
    persist["snapshot_date"] = persist["snapshot_date"].dt.strftime("%Y-%m-%d")
    conn = _connect()
    _ensure_table(conn)
    conn.execute(f"DELETE FROM {SNAPSHOT_TABLE}")
    conn.commit()
    persist.to_sql(SNAPSHOT_TABLE, conn, if_exists="append", index=False)
    conn.commit()
    conn.close()

    safe_report = _json_safe(report)
    Path(OUTPUT_JSON).write_text(json.dumps(safe_report, ensure_ascii=False, indent=2, allow_nan=False))
    return safe_report


if __name__ == "__main__":
    out = build_strategy_research_dataset()
    print(json.dumps(out["dataset"], ensure_ascii=False, indent=2))
