#!/usr/bin/env python3
"""
월별 종목 후보 함수: strategy_backlog_rs_lowvol_market

입력: signal_date (기준일, 없으면 오늘)
출력: stock_code, score, rank, position_weight, entry_rule, exit_rule, risk_flags

실전 적용 전 주의:
- 2026-06-23 재검토 결과, 기존 strong_trend_material_stop20은 폐기
- 새 후보는 수익률 1등보다 시간 정합성/낮은 MDD/장세 선택성을 우선
- 수주잔고는 DART 접수일/confidence 기준만 사용하고, 섹터 강도는 지수보다 우선 확인
- 매월 마지막 영업일 신호 계산 → 다음 영업일 시가 매수 → 월말 종가 청산
- 손절은 기본 자동청산 조건에서 제외하고 월말 리밸런싱을 원칙으로 함
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"

# ── 확정 전략 파라미터 ──────────────────────────────────────
STRATEGY_ID = "backlog_rs_lowvol_market"
TOP_N = 8
MAX_POSITION_WEIGHT = 0.25
STOP_LOSS_PCT = None
TCOST_ROUNDTRIP = 0.0035  # 왕복 수수료+슬리피지 0.35% (타이트한 가정)
TCOST_REALISTIC = 0.0070  # 현실적 거래비용 0.70%
SECTOR_RET3_MIN = 0.10    # 시장 전체가 약해도 섹터 3M 중앙값 +10% 이상이면 진입 허용
SECTOR_RANK_MIN = 0.60    # 섹터 강도 상위 40% 이내

SCORE_WEIGHTS: list[tuple[str, float]] = [
    ("r_ret_12_1",      0.28),
    ("r_ret_6m",        0.22),
    ("r_near_high52",   0.18),
    ("r_low_vol60",     0.20),
    ("r_avg_turnover20", 0.12),
]


EXIT_RULES = {
    "월말청산":  "해당 월 마지막 거래일 종가 매도 (기본 청산)",
    "위험점검":  "보유 중 -20% 이상 급락 시 자동청산이 아니라 수동 위험 점검 대상으로 표시",
}


# ── DB 헬퍼 ──────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _theme_sector_map() -> dict[str, str]:
    """전략용 테마 섹터 맵.

    stock_universe 대분류만 쓰면 방산/전력기기/기판처럼 한국 시장에서
    따로 움직이는 섹터를 놓치므로, sector_rotation 그룹을 우선 사용한다.
    """
    try:
        from routes.sector_rotation import SECTOR_GROUPS

        out: dict[str, str] = {}
        for key, info in SECTOR_GROUPS.items():
            for code in info.get("codes", []):
                if isinstance(code, str) and len(code) == 6:
                    out[code] = key
        return out
    except Exception:
        return {}


# ── 피처 계산 ────────────────────────────────────────────────

def _load_price_features(signal_date: date, lookback_days: int = 520) -> pd.DataFrame:
    """signal_date 기준으로 각 종목의 가격/거래량/수급 피처 계산."""
    start = (signal_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end   = signal_date.strftime("%Y-%m-%d")

    c = _conn()
    px = pd.read_sql_query(
        """
        SELECT ph.stock_code, ph.date, ph.open, ph.high, ph.low, ph.close,
               ph.volume, ph.trade_amount AS turnover,
               CASE
                 WHEN COALESCE(ph.inst_net_buy_amt, 0) != 0 THEN ph.inst_net_buy_amt
                 ELSE COALESCE(ph.inst_net_buy, 0) * ph.close / 1000000.0
               END AS inst_net_buy_amt,
               CASE
                 WHEN COALESCE(ph.frn_net_buy_amt, 0) != 0 THEN ph.frn_net_buy_amt
                 ELSE COALESCE(ph.frn_net_buy, 0) * ph.close / 1000000.0
               END AS frn_net_buy_amt
        FROM price_history ph
        WHERE ph.date BETWEEN ? AND ?
          AND ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND ph.close > 0 AND ph.open > 0
        ORDER BY ph.stock_code, ph.date
        """,
        c, params=(start, end), parse_dates=["date"],
    )
    su = pd.read_sql_query(
        """
        SELECT stock_code, stock_name, market, market_cap,
               sector_large, sector_mid, sector_small
        FROM stock_universe
        """,
        c,
    )
    c.close()

    # 보조 사업 피처: 재료비는 참고용, 주력 필터는 수주잔고/수급.
    cost_df = _load_material_cost_yoy(signal_date)
    backlog_df = _load_backlog_signal(signal_date)

    features = []
    for code, g in px.groupby("stock_code"):
        g = g.sort_values("date")
        if len(g) < 60:
            continue
        last = g.iloc[-1]
        closes = g["close"].values
        n = len(closes)

        # ── MA (단기·중기·장기 전체) ────────────────────────────
        def _ma(k):
            return float(closes[-k:].mean()) if n >= k else np.nan

        ma5   = _ma(5)
        ma10  = _ma(10)
        ma20  = _ma(20)
        ma60  = _ma(60)
        ma120 = _ma(100)   # 데이터 100일 이상이면 MA120 근사 가능
        ma200 = _ma(180)   # 데이터 180일 이상이면 MA200 근사 가능
        ma240 = _ma(240) if n >= 230 else np.nan   # 약 1년 (240거래일)

        # ── 모멘텀 ──────────────────────────────────────────────
        def _ret(k):
            return float(closes[-1] / closes[-k] - 1) if n >= k and closes[-k] > 0 else np.nan

        ret_5d  = _ret(5)    # 1주
        ret_1m  = _ret(22)   # 1개월
        ret_3m  = _ret(63)   # 3개월
        ret_6m  = _ret(126)  # 6개월
        ret_12m = _ret(252)  # 12개월
        ret_12_1 = (
            float(closes[-22] / closes[-252] - 1)
            if n >= 252 and closes[-252] > 0 else np.nan
        )

        # ── 52주 고/저 ─────────────────────────────────────────
        win52   = closes[-252:] if n >= 252 else closes
        high52  = float(win52.max())
        low52   = float(win52.min())
        near_h  = closes[-1] / high52  if high52 > 0 else np.nan
        near_l  = closes[-1] / low52   if low52  > 0 else np.nan

        # ── 변동성 (5일, 20일, 60일) ───────────────────────────
        def _vol(k):
            if n < k + 1:
                return np.nan
            lr = np.diff(np.log(closes[-(k+1):]))
            return float(np.std(lr) * np.sqrt(252)) if len(lr) >= 5 else np.nan

        vol5  = _vol(5)
        vol20 = _vol(20)
        vol60 = _vol(60)

        # ── 거래대금 (5일·20일 평균) ───────────────────────────
        turnover = g["turnover"].values
        if turnover.sum() == 0:
            turnover = g["close"].values * g["volume"].values
        avg_turnover5  = float(turnover[-5:].mean())  if len(turnover) >= 5  else np.nan
        avg_turnover20 = float(turnover[-20:].mean()) if len(turnover) >= 20 else np.nan

        # ── 수급 (기관+외국인 순매수금액, 5·20일) ──────────────
        inst  = g["inst_net_buy_amt"].values
        frn   = g["frn_net_buy_amt"].values
        supply5  = float((inst[-5:] + frn[-5:]).sum())  if len(inst) >= 5  else 0.0
        supply20 = float((inst[-20:] + frn[-20:]).sum()) if len(inst) >= 20 else 0.0
        supply20_to_turnover = (
            supply20 / (avg_turnover20 * 20)
            if avg_turnover20 and avg_turnover20 > 0 else 0.0
        )

        features.append({
            "stock_code":  code,
            "close":       float(closes[-1]),
            # MA 전체
            "ma5":   ma5,   "ma10":  ma10,  "ma20":  ma20,
            "ma60":  ma60,  "ma120": ma120, "ma200": ma200, "ma240": ma240,
            # 모멘텀
            "ret_5d": ret_5d,  "ret_1m": ret_1m,  "ret_3m": ret_3m,
            "ret_6m": ret_6m,  "ret_12m": ret_12m, "ret_12_1": ret_12_1,
            # 고/저
            "high52": high52, "low52": low52,
            "near_high52": near_h, "near_low52": near_l,
            # 변동성
            "vol5": vol5, "vol20": vol20, "vol60": vol60,
            # 거래대금
            "avg_turnover5":  avg_turnover5,
            "avg_turnover20": avg_turnover20,
            # 수급
            "supply5":  supply5,
            "supply20": supply20,
            "supply20_to_turnover": supply20_to_turnover,
        })

    df = pd.DataFrame(features)
    df = df.merge(
        su[["stock_code", "stock_name", "market", "market_cap", "sector_large", "sector_mid", "sector_small"]],
        on="stock_code",
        how="left",
    )
    df = df.merge(cost_df, on="stock_code", how="left")
    df = df.merge(backlog_df, on="stock_code", how="left")
    df["market_ret3_pos"] = _market_ret3_positive(signal_date)
    theme_map = _theme_sector_map()
    df["theme_sector"] = df["stock_code"].astype(str).map(theme_map)
    df["sector_key"] = (
        df["theme_sector"]
        .fillna(df["sector_mid"])
        .fillna(df["sector_large"])
        .fillna("미분류")
        .replace("", "미분류")
    )
    sector_stats = (
        df.groupby("sector_key", dropna=False)
        .agg(sector_ret3_median=("ret_3m", "median"), sector_count=("stock_code", "count"))
        .reset_index()
    )
    sector_stats["sector_ret3_rank"] = sector_stats["sector_ret3_median"].rank(pct=True)
    df = df.merge(sector_stats, on="sector_key", how="left")
    df["sector_market_ok"] = (
        (df["sector_count"].fillna(0) >= 3)
        & (df["sector_ret3_median"].fillna(-1) >= SECTOR_RET3_MIN)
        & (df["sector_ret3_rank"].fillna(0) >= SECTOR_RANK_MIN)
    )
    for col in ["backlog_present", "backlog_yoy", "new_order_yoy"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["backlog_pos"] = (
        (df["backlog_present"] > 0)
        | (df["backlog_yoy"] > 0)
        | (df["new_order_yoy"] > 0)
    )
    return df


def _market_ret3_positive(signal_date: date) -> bool:
    """KOSPI 3개월 수익률이 양수인지 확인.

    KOSPI 8000~9000대는 현재 실제 지수 레벨이므로 절대값 상한으로
    매수 중단하지 않는다. 지수 데이터가 부족한 경우에만 보통주
    유니버스의 3개월 중앙값 수익률로 대체한다.
    """
    start = (signal_date - timedelta(days=180)).strftime("%Y-%m-%d")
    end = signal_date.strftime("%Y-%m-%d")
    c = _conn()
    try:
        ks = pd.read_sql_query(
            """
            SELECT date, close
            FROM price_history
            WHERE stock_code='^KS11'
              AND date BETWEEN ? AND ?
              AND close > 0
            ORDER BY date
            """,
            c, params=(start, end), parse_dates=["date"],
        )
    finally:
        c.close()
    if len(ks) < 64:
        return _common_stock_market_ret3_positive(signal_date)
    closes = pd.to_numeric(ks["close"], errors="coerce").dropna().to_numpy()
    if len(closes) < 64:
        return _common_stock_market_ret3_positive(signal_date)
    last = float(closes[-1])
    return bool(last / float(closes[-64]) - 1 > 0)


def _common_stock_market_ret3_positive(signal_date: date) -> bool:
    """Index fallback: 보통주 3개월 중앙값 수익률이 양수인지 판단."""
    start = (signal_date - timedelta(days=180)).strftime("%Y-%m-%d")
    end = signal_date.strftime("%Y-%m-%d")
    c = _conn()
    try:
        px = pd.read_sql_query(
            """
            SELECT ph.stock_code, ph.date, ph.close
            FROM price_history ph
            JOIN stock_universe su ON su.stock_code = ph.stock_code
            WHERE ph.date BETWEEN ? AND ?
              AND ph.close > 0
              AND ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND su.market IN ('KOSPI', 'KOSDAQ')
              AND COALESCE(su.stock_type, '') = '보통주'
              AND COALESCE(su.secugrp_nm, '') = '주권'
              AND COALESCE(su.kind_stkcert_nm, '') = '보통주'
            ORDER BY ph.stock_code, ph.date
            """,
            c,
            params=(start, end),
            parse_dates=["date"],
        )
    finally:
        c.close()
    if px.empty:
        return False
    returns = []
    for _, g in px.groupby("stock_code"):
        g = g.sort_values("date")
        if len(g) < 64:
            continue
        prev = float(g.iloc[-64]["close"])
        cur = float(g.iloc[-1]["close"])
        if prev > 0:
            returns.append(cur / prev - 1)
    if len(returns) < 300:
        return False
    return bool(float(np.nanmedian(returns)) > 0)


def _load_material_cost_yoy(signal_date: date) -> pd.DataFrame:
    """매입재료비 YoY 로드 (2개월 지연 반영 — lag_months=2)."""
    lag_date = signal_date - timedelta(days=60)   # 2개월 지연
    year  = lag_date.year
    qtr   = (lag_date.month - 1) // 3

    c = _conn()
    try:
        # 현재 분기 대비 전년 동기 비교
        df = pd.read_sql_query(
            """
            SELECT cs.stock_code,
                   cs.raw_material_cost / NULLIF(py.raw_material_cost, 0) - 1 AS raw_material_cost_yoy
            FROM cost_structure cs
            JOIN cost_structure py
              ON cs.stock_code = py.stock_code
              AND cs.year = py.year + 1
              AND cs.quarter = py.quarter
            WHERE cs.year = ? AND cs.quarter = ?
              AND cs.raw_material_cost IS NOT NULL
              AND py.raw_material_cost IS NOT NULL
            """,
            c, params=(year, qtr),
        )
    except Exception:
        df = pd.DataFrame(columns=["stock_code", "raw_material_cost_yoy"])
    c.close()
    return df


def _load_backlog_signal(signal_date: date) -> pd.DataFrame:
    """수주잔고 신호 로드.

    DART 파싱 테이블의 실제 접수일(source_rcept_dt)만 사용한다.
    레거시 order_backlog는 공시일/단위/업종 오염 이슈가 확인되어
    실전 후보 필터에서는 사용하지 않는다.
    """
    sig = signal_date.strftime("%Y-%m-%d")
    c = _conn()
    pieces = []
    try:
        dart = pd.read_sql_query(
            """
            SELECT stock_code, fiscal_year AS year, fiscal_quarter AS quarter,
                   backlog_amount_krw AS backlog_amount,
                   source_rcept_dt
            FROM dart_backlog_quarterly
            WHERE source_rcept_dt IS NOT NULL
              AND REPLACE(source_rcept_dt, '.', '-') <= ?
              AND backlog_amount_krw IS NOT NULL
              AND backlog_confidence >= 0.95
            """,
            c, params=(sig,),
        )
        if not dart.empty:
            dart["backlog_amount"] = pd.to_numeric(dart["backlog_amount"], errors="coerce")
            dart = dart.sort_values(["stock_code", "year", "quarter"])
            g = dart.groupby("stock_code", group_keys=False)
            previous = g["backlog_amount"].shift(1)
            adjacent_ratio = pd.concat(
                [dart["backlog_amount"], previous], axis=1
            ).max(axis=1) / pd.concat(
                [dart["backlog_amount"], previous], axis=1
            ).min(axis=1).replace(0, np.nan)
            bad_pair = adjacent_ratio.gt(20.0)
            bad_neighbor = bad_pair.groupby(dart["stock_code"]).shift(-1, fill_value=False)
            dart = dart[~(bad_pair | bad_neighbor)].copy()
            g = dart.groupby("stock_code", group_keys=False)
            dart["backlog_yoy"] = dart["backlog_amount"] / g["backlog_amount"].shift(4) - 1
            dart["new_order_yoy"] = np.nan
            dart["backlog_present"] = (dart["backlog_amount"] > 0).astype(float)
            pieces.append(dart[["stock_code", "year", "quarter", "backlog_present", "backlog_yoy", "new_order_yoy"]])
    except Exception:
        pass

    c.close()

    if not pieces:
        return pd.DataFrame(columns=["stock_code", "backlog_present", "backlog_yoy", "new_order_yoy"])
    out = pd.concat(pieces, ignore_index=True)
    out["_key"] = pd.to_numeric(out["year"], errors="coerce").fillna(0) * 10 + pd.to_numeric(out["quarter"], errors="coerce").fillna(0)
    out = out.sort_values(["stock_code", "_key"]).drop_duplicates("stock_code", keep="last")
    return out[["stock_code", "backlog_present", "backlog_yoy", "new_order_yoy"]]


def _add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """횡단면 퍼센타일 랭크 추가."""
    rank_cols = [
        "ret_12_1", "ret_12m", "ret_6m", "ret_3m", "near_high52",
        "avg_turnover20", "supply20_to_turnover", "vol60",
        "backlog_yoy", "new_order_yoy", "raw_material_cost_yoy",
    ]
    for col in rank_cols:
        if col not in df.columns:
            df[f"r_{col}"] = np.nan
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        df[f"r_{col}"] = s.rank(pct=True)

    # 낮은 변동성일수록 좋음
    if "r_vol60" in df.columns:
        df["r_low_vol60"] = 1 - df["r_vol60"]

    # 재료비 관련 rank는 데이터 없으면 0
    for col in ["r_dart_material_yoy", "r_annual_material_yoy"]:
        if col not in df.columns:
            df[col] = 0.0

    return df


FILTER_RULES = {
    "liquid_1b":      "20일 평균거래대금 ≥ 10억원",
    "listed_common":  "stock_universe에서 KOSPI/KOSDAQ 보통주성 종목 확인",
    "price_ok":       "종가 ≥ 1,000원",
    "trend_base":     "종가 > MA120, MA20 > MA60",
    "mom_3m_pos":     "3개월 수익률 > +8%",
    "backlog_pos":    "수주잔고 존재/증가 또는 신규수주 증가 신호 확인",
    "market_or_sector": "KOSPI 3개월 수익률 > 0 또는 해당 섹터 3개월 중앙값 +10% 이상",
}

def _apply_filters(df: pd.DataFrame) -> pd.Series:
    """필터 마스크 반환."""
    n = lambda col: pd.to_numeric(df.get(col), errors="coerce")
    mask = (
        (n("avg_turnover20").fillna(0) >= 1e9)          # 거래대금 10억+
        & (df.get("market").isin(["KOSPI", "KOSDAQ"]))  # 상장주식 마스터 확인
        & (df.get("stock_name").notna())
        & (n("close").fillna(0) >= 1000)                 # 종가 1,000원+
        & (n("close") > n("ma120"))                      # 종가 > MA120
        & (n("ma20") > n("ma60"))                        # MA20 > MA60
        & (n("ret_3m").fillna(-1) > 0.08)                # 3M 모멘텀 +8%+
        & (df.get("backlog_pos", False) == True)         # 수주/잔고 트리거 확인
        & (
            (df.get("market_ret3_pos", False) == True)   # 시장 3M 양수
            | (df.get("sector_market_ok", False) == True) # 또는 강한 섹터 장세
        )
    )
    return mask


def _compute_score(df: pd.DataFrame) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    for col, w in SCORE_WEIGHTS:
        s = pd.to_numeric(df.get(col), errors="coerce").fillna(0)
        out += w * s
    return out


# ── 공개 API ──────────────────────────────────────────────────

def generate_monthly_picks(
    signal_date: date | None = None,
    top_n: int = TOP_N,
    position_sizing: str = "vol_inv",   # "equal" | "score" | "vol_inv"
) -> list[dict[str, Any]]:
    """
    월말 기준 최적 종목 Top-N 반환.

    Args:
        signal_date: 신호 계산 기준일 (기본: 오늘)
        top_n:       선택 종목 수 (기본 10)
        position_sizing: "equal" / "score" / "vol_inv"

    Returns:
        [
          {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "rank": 1,
            "score": 0.812,
            "position_weight": 0.20,
            "close": 85000,
            "ret_6m": 0.23,
            "vol60": 0.18,
            "supply20_to_turnover": 0.031,
            "entry_rule": "다음 거래일 시가 매수",
            "exit_rule": "월말 종가 청산, -20% 이상 급락 시 위험 점검",
            "risk_flags": [],
            "filter_passed": {...},
          },
          ...
        ]
    """
    if signal_date is None:
        signal_date = date.today()

    df = _load_price_features(signal_date)
    df = _add_ranks(df)
    flt = _apply_filters(df)
    universe = df[flt].copy()

    if universe.empty:
        return []

    universe["score"] = _compute_score(universe)
    universe = universe.sort_values("score", ascending=False).head(top_n * 3)  # buffer
    universe = universe.head(top_n).copy()
    universe["rank"] = range(1, len(universe) + 1)

    # 포지션 사이징
    if position_sizing == "score":
        total_score = universe["score"].sum()
        universe["position_weight"] = universe["score"] / total_score if total_score > 0 else 1 / len(universe)
    elif position_sizing == "vol_inv":
        vol = universe["vol60"].fillna(0.03).clip(lower=0.005)
        inv = 1.0 / vol
        inv = inv.clip(upper=inv.mean() * 2.5)  # 극단값 제한
        total_inv = inv.sum()
        universe["position_weight"] = inv / total_inv
        # 최대 25% 제한
        raw_w = universe["position_weight"].clip(upper=MAX_POSITION_WEIGHT)
        if len(universe) >= int(np.ceil(1 / MAX_POSITION_WEIGHT)):
            universe["position_weight"] = raw_w / raw_w.sum()
        else:
            # 후보가 너무 적은 달에는 억지로 전액 투자하지 않고 현금을 남긴다.
            universe["position_weight"] = raw_w
    else:
        universe["position_weight"] = 1.0 / len(universe)

    result = []
    for _, row in universe.iterrows():
        risk_flags = []

        # 변동성 경고
        if (row.get("vol60") or 0) > 0.45:
            risk_flags.append("고변동성(연환산45%+)")

        # 단기 약세
        if (row.get("ret_1m") or 0) < -0.05:
            risk_flags.append("최근1개월약세(-5%+)")

        # 거래대금 50억 미만 (슬리피지 주의)
        avg_t = row.get("avg_turnover20") or 0
        if 0 < avg_t < 5e9:
            risk_flags.append("거래대금50억미만(슬리피지주의)")

        # 소형주 (시총 500억 미만 — 억원 단위)
        mktcap = row.get("market_cap") or 0
        if 0 < mktcap < 500:
            risk_flags.append(f"소형주(시총{int(mktcap)}억)")

        # 신규상장 / 마스터 미등록
        sname = str(row.get("stock_name", ""))
        if not sname or sname in ("nan", "None", ""):
            sname = "(신규상장/마스터DB미등록)"
            risk_flags.append("신규상장종목(정보미확인)")
        mkt = str(row.get("market", ""))
        if not mkt or mkt in ("nan", "None"):
            mkt = "—"

        def _f(col, mult=1, dec=1):
            v = row.get(col)
            return round(float(v or 0) * mult, dec) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None

        def _ma_str(col):
            v = row.get(col)
            return int(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None

        result.append({
            "stock_code":      str(row["stock_code"]),
            "stock_name":      sname,
            "market":          mkt,
            "sector_key":      str(row.get("sector_key") or "미분류"),
            "sector_ret3_pct": _f("sector_ret3_median", 100, 1),
            "sector_ret3_rank": _f("sector_ret3_rank", 1, 3),
            "market_ret3_pos": bool(row.get("market_ret3_pos")),
            "sector_market_ok": bool(row.get("sector_market_ok")),
            "market_cap_억":   int(mktcap) if (mktcap and not np.isnan(mktcap)) else None,
            "rank":            int(row["rank"]),
            "score":           round(float(row["score"]), 4),
            "position_weight": round(float(row["position_weight"]), 4),
            "close":           _ma_str("close"),
            # MA 전체 (단기→장기)
            "ma5":             _ma_str("ma5"),
            "ma10":            _ma_str("ma10"),
            "ma20":            _ma_str("ma20"),
            "ma60":            _ma_str("ma60"),
            "ma120":           _ma_str("ma120"),
            "ma200":           _ma_str("ma200"),
            "ma240":           _ma_str("ma240"),
            # 모멘텀
            "ret_5d_pct":      _f("ret_5d", 100, 1),
            "ret_1m_pct":      _f("ret_1m", 100, 1),
            "ret_3m_pct":      _f("ret_3m", 100, 1),
            "ret_6m_pct":      _f("ret_6m", 100, 1),
            "ret_12m_pct":     _f("ret_12m", 100, 1),
            "ret_12_1_pct":     _f("ret_12_1", 100, 1),
            # 고/저 위치
            "near_high52":     _f("near_high52", 100, 1),   # % (100=52주 최고)
            "near_low52":      _f("near_low52", 100, 1),    # % (100=52주 최저와 같은 수준)
            # 변동성 (연환산 %)
            "vol5_ann_pct":    _f("vol5",  100, 1),
            "vol20_ann_pct":   _f("vol20", 100, 1),
            "vol60_ann_pct":   _f("vol60", 100, 1),
            # 거래대금
            "avg_turnover5_억":  round(avg_t / 1e8, 1) if avg_t else None,
            "avg_turnover20_억": round((row.get("avg_turnover20") or 0) / 1e8, 1),
            # 수급
            "supply5_억":   round((row.get("supply5")  or 0) / 1e8, 1),
            "supply20_억":  round((row.get("supply20") or 0) / 1e8, 1),
            "supply20_to_turnover": _f("supply20_to_turnover", 1, 4),
            # 사업 피처
            "backlog_present": bool((row.get("backlog_present") or 0) > 0),
            "backlog_yoy_pct": _f("backlog_yoy", 100, 1),
            "new_order_yoy_pct": _f("new_order_yoy", 100, 1),
            # 진입/청산 규칙
            "entry_rule":  "다음 영업일 시가 매수",
            "exit_rule":   "당월 말 종가 청산 | -20% 이상 급락 시 위험 점검",
            "risk_flags":  risk_flags,
        })

    return result


def strategy_description() -> dict[str, Any]:
    """전략 상세 설명 (사이트 표시용)."""
    return {
        "id":          STRATEGY_ID,
        "name":        "수주잔고+저변동성 RS+섹터필터",
        "type":        "월별 리밸런싱 후보 전략",
        "status":      "candidate_requires_forward_validation",
        "live_trading_allowed": False,
        "top_n":       TOP_N,
        "max_position_weight": MAX_POSITION_WEIGHT,
        "stop_loss":   "자동 손절 없음; -20% 이상 급락 시 위험 점검",
        "rebalance":   "매월 마지막 영업일 신호 → 다음 영업일 시가 매수 → 월말 종가 매도",
        "filters":     FILTER_RULES,
        "score_weights": {col: w for col, w in SCORE_WEIGHTS},
        "exit_rules":  EXIT_RULES,
        "backtest": {
            "strict_2021_2025": {
                "period": "2021-01~2025-03 signals / exits through 2025-04",
                "return_pct": 127.63,
                "cagr_pct": 21.35,
                "mdd_pct": -21.13,
                "train_return_pct": 30.94,
                "test_return_pct": 73.84,
                "active_months": "26/51",
                "trade_count": 208,
                "active_month_hit_rate_pct": 53.8,
                "trade_hit_rate_pct": 47.6,
                "verdict": "재생성 수급 데이터 기준 균형 1위. 수주잔고/수주 신호를 사업 트리거로 추가",
            },
            "rejected_strict_backlog": {
                "period": "2021-01~2025-03 signals / exits through 2025-04",
                "return_pct": -5.8,
                "mdd_pct": -6.94,
                "active_months": "7/51",
                "trade_count": 8,
                "note": "DART 접수일 확인 수주잔고만 쓰면 너무 희소해 실전 로직으로 부적합",
            },
            "rejected_oss": {
                "strategy": "strong_trend_material_stop20",
                "period": "2021-01~2026-05",
                "return_pct": -10.6,
                "mdd_pct": -51.2,
                "note": "짧은 강세장 OOS 착시와 추격매수 손실 때문에 폐기",
            },
        },
        "warnings": [
            "실매매 허용 전 2025-05 이후 및 2026년 데이터로 전진검증 필요",
            "KOSPI 8000~9000대 지수 레벨은 정상 범위로 처리; 전체 시장이 약해도 섹터 3개월 강도가 충분하면 진입 허용",
            "수주잔고/수주 피처는 DART 접수일·confidence 기준으로만 사용; 데이터 희소 구간은 현금 비중 유지",
            "과최적화 위험 완화를 위해 수익률 1등이 아니라 MDD와 train/test 양수 조건을 우선 적용",
            "거래대금 10억원 미만 종목 자동 제외 — 유동성 확보",
        ],
    }


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys

    sig_date = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    print(f"=== 월별 추천 종목 (기준일: {sig_date}) ===\n")

    picks = generate_monthly_picks(signal_date=sig_date, top_n=TOP_N, position_sizing="vol_inv")

    if not picks:
        print("  ⚠️  이 달 필터를 통과한 종목 없음 (강한 조건)")
    else:
        for p in picks:
            flags = f"  ⚠️ {', '.join(p['risk_flags'])}" if p['risk_flags'] else ""
            print(
                f"  #{p['rank']} {p['stock_name']:10s}({p['stock_code']}) "
                f"점수:{p['score']:.3f}  비중:{p['position_weight']*100:.0f}%  "
                f"6m:{p['ret_6m_pct']:+.1f}%  52w고:{p['near_high52']:.0f}%  "
                f"MA5:{p.get('ma5','?'):,}  시총:{p.get('market_cap_억','?')}억"
                f"{flags}"
            )

    print(f"\n=== 전략 설명 ===")
    desc = strategy_description()
    for k, v in desc["backtest"].items():
        b = desc["backtest"][k]
        ret = b.get("return_pct", "")
        mdd = b.get("mdd_pct", "")
        hr  = b.get("hit_rate_pct", "")
        print(f"  [{b['period']}] 수익:{ret:+.1f}%  MDD:{mdd}%  승률:{hr}%")
    print(json.dumps(picks, ensure_ascii=False, indent=2))
