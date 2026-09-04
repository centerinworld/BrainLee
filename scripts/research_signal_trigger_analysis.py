#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd


warnings.filterwarnings("ignore", category=RuntimeWarning, message="Mean of empty slice")
warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parents[1]
STOCK_DB = ROOT / "stock.db"
HS_DB = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
OUT_DIR = ROOT / "research_outputs"
OUT_PATH = OUT_DIR / "signal_trigger_analysis_2020plus.json"

START_YEAR = 2020
END_YEAR = 2026


TRIGGER_DEFS = {
    "amount_10x": ("거래대금 10배 이상", "수급/거래대금", "저점 이후 60거래일 내 거래대금이 직전 60일 평균 대비 10배 이상"),
    "amount_5x": ("거래대금 5배 이상", "수급/거래대금", "저점 이후 60거래일 내 거래대금이 직전 60일 평균 대비 5배 이상"),
    "volume_10x": ("거래량 10배 이상", "수급/거래량", "저점 이후 60거래일 내 거래량이 직전 60일 평균 대비 10배 이상"),
    "volume_3x": ("거래량 3배 이상", "수급/거래량", "저점 이후 60거래일 내 거래량이 직전 60일 평균 대비 3배 이상"),
    "ma20_reclaim": ("20일선 회복", "추세선", "저점 이후 60거래일 내 종가가 20일 이동평균 위로 재진입"),
    "ma60_reclaim": ("60일선 회복", "추세선", "저점 이후 90거래일 내 종가가 60일 이동평균 위로 재진입"),
    "new_60d_high": ("60일 신고가 재돌파", "추세/돌파", "저점 이후 90거래일 내 직전 60일 고가 돌파"),
    "inst_20d_buy": ("기관 20일 순매수", "수급/기관", "저점 이후 20거래일 누적 기관 순매수 수량 또는 금액 환산값이 양수"),
    "frn_20d_buy": ("외국인 20일 순매수", "수급/외국인", "저점 이후 20거래일 누적 외국인 순매수 수량 또는 금액 환산값이 양수"),
    "both_inst_frn_buy": ("기관+외국인 동반 순매수", "수급/복합", "저점 이후 20거래일 기관과 외국인 누적 순매수 수량 또는 금액 환산값이 모두 양수"),
    "short_cover": ("공매도/대차 감소", "공매도", "저점 이후 60거래일 내 대차잔고율이 직전 대비 20% 이상 하락"),
    "revenue_yoy_pos": ("매출 YoY 플러스", "실적", "해당 연도까지 확인 가능한 최근 분기 매출 YoY가 0% 이상"),
    "revenue_yoy_15": ("매출 YoY 15% 이상", "실적", "해당 연도까지 확인 가능한 최근 분기 매출 YoY가 15% 이상"),
    "op_turnaround": ("영업이익 흑자전환", "실적", "최근 분기 영업이익이 흑자이고 전년 동기 영업이익은 적자"),
    "op_profit_pos": ("영업이익 흑자", "실적", "최근 분기 영업이익 양수"),
    "export_yoy_30": ("수출 YoY 30% 이상", "수출", "해당 연도 회사-HS 매핑 수출의 3개월 평균 YoY가 30% 이상"),
    "export_yoy_100": ("수출 YoY 100% 이상", "수출", "해당 연도 회사-HS 매핑 수출의 3개월 평균 YoY가 100% 이상"),
    "export_value_rising": ("수출액 증가", "수출", "3개월 평균 수출액이 전년 동기보다 증가"),
    "contract_signal": ("수주/공급계약 공시", "공시/수주", "해당 연도 단일판매ㆍ공급계약 등 수주 공시 확인"),
    "overseas_contract": ("해외 수주 공시", "공시/수주", "해당 연도 해외 거래처 수주 공시 확인"),
    "backlog_growth": ("수주잔고 증가", "수주잔고", "최근 수주잔고가 전년 대비 증가"),
    "employee_growth": ("직원수 증가", "고용", "DART 직원수가 전년 대비 증가"),
    "material_purchase_growth": ("원재료 매입 증가", "원가/생산", "원재료 매입액이 전년 대비 증가"),
    "small_cap_3000": ("시총 3,000억 이하", "규모", "stock_universe 기준 시가총액 3,000억 이하"),
    "kosdaq": ("KOSDAQ", "시장", "KOSDAQ 보통주"),
    "core_sector": ("핵심 성장 섹터", "섹터", "ITㆍ의료ㆍ경기소비재ㆍ산업재 중 하나"),
}

FAIL_DEFS = {
    "amount_under_5x": ("거래대금 후속 유입 부족", "저점 이후 60일 거래대금 피크가 5배 미만"),
    "no_ma60_reclaim": ("60일선 회복 실패", "저점 이후 90일 동안 60일선 회복 실패"),
    "inst_frn_both_sell": ("기관+외국인 동반 매도", "저점 이후 20일 기관/외국인 누적 순매수가 모두 음수"),
    "revenue_yoy_neg": ("매출 역성장", "최근 분기 매출 YoY가 음수"),
    "op_loss": ("영업적자", "최근 분기 영업이익이 적자"),
    "export_yoy_neg": ("수출 역성장", "3개월 평균 수출 YoY가 음수"),
    "short_pressure": ("공매도/대차 부담", "저점 이후 대차잔고율이 상승하거나 3% 이상"),
    "large_cap": ("시총 부담", "시가총액 3,000억 초과"),
    "no_contract": ("수주 공시 부재", "해당 연도 수주/공급계약 공시 없음"),
    "no_20d_followthrough": ("20일 추세 후속 상승 부족", "저점 이후 20거래일 최고수익률이 15% 미만"),
}


def _safe_pct(num: float, den: float, ndigits: int = 1) -> float:
    if not den:
        return 0.0
    return round(num / den * 100, ndigits)


def _num(v, ndigits: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, ndigits)


def _load_price_panel(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT p.stock_code, p.date, p.open, p.high, p.low, p.close, p.volume,
               COALESCE(p.trade_amount, p.close * p.volume) AS trade_amount,
               COALESCE(p.inst_net_buy, 0) AS inst_net_buy,
               COALESCE(p.frn_net_buy, 0) AS frn_net_buy,
               COALESCE(p.inst_net_buy_amt, 0) AS inst_net_buy_amt,
               COALESCE(p.frn_net_buy_amt, 0) AS frn_net_buy_amt,
               su.stock_name, su.market, COALESCE(su.sector_large, '미분류') AS sector,
               COALESCE(su.sector_mid, '') AS theme, su.market_cap
        FROM price_history p
        JOIN (
            SELECT stock_code, MAX(stock_name) AS stock_name, MAX(market) AS market,
                   MAX(sector_large) AS sector_large, MAX(sector_mid) AS sector_mid,
                   MAX(market_cap) AS market_cap
            FROM stock_universe
            WHERE market IN ('KOSPI', 'KOSDAQ')
              AND COALESCE(stock_type, '') = '보통주'
              AND COALESCE(secugrp_nm, '') = '주권'
              AND COALESCE(kind_stkcert_nm, '') = '보통주'
              AND stock_name IS NOT NULL AND stock_name <> ''
            GROUP BY stock_code
        ) su ON su.stock_code = p.stock_code
        WHERE p.date >= ? AND p.date < ?
          AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND p.low >= 100 AND p.high > 0 AND p.close > 0
        ORDER BY p.stock_code, p.date
        """,
        conn,
        params=(f"{START_YEAR - 1}-09-01", f"{END_YEAR + 1}-01-01"),
    )
    df["date"] = pd.to_datetime(df["date"], format="mixed", errors="coerce")
    df = df[df["date"].notna()].copy()
    df["year"] = df["date"].dt.year
    df["dt_key"] = df["date"].dt.strftime("%Y%m%d")
    inv = pd.read_sql_query(
        """
        SELECT stock_code, replace(bas_dt, '-', '') AS dt_key,
               inst_net AS itd_inst_net, frgn_net AS itd_frgn_net
        FROM investor_trading_daily
        WHERE replace(bas_dt, '-', '') >= ? AND replace(bas_dt, '-', '') < ?
        """,
        conn,
        params=(f"{START_YEAR}0101", f"{END_YEAR + 1}0101"),
    )
    if not inv.empty:
        df = df.merge(inv, on=["stock_code", "dt_key"], how="left")
        inst_fallback = df["inst_net_buy_amt"].eq(0)
        frn_fallback = df["frn_net_buy_amt"].eq(0)
        df.loc[inst_fallback, "inst_net_buy_amt"] = (
            df.loc[inst_fallback, "inst_net_buy"].where(df.loc[inst_fallback, "inst_net_buy"].ne(0), df.loc[inst_fallback, "itd_inst_net"].fillna(0))
            * df.loc[inst_fallback, "close"] / 1000000.0
        )
        df.loc[frn_fallback, "frn_net_buy_amt"] = (
            df.loc[frn_fallback, "frn_net_buy"].where(df.loc[frn_fallback, "frn_net_buy"].ne(0), df.loc[frn_fallback, "itd_frgn_net"].fillna(0))
            * df.loc[frn_fallback, "close"] / 1000000.0
        )
        df = df.drop(columns=["itd_inst_net", "itd_frgn_net"])
    return df


def _price_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    core_sectors = {"IT", "의료", "경기소비재", "산업재"}
    for code, g in df.groupby("stock_code", sort=False):
        g = g.sort_values("date").reset_index(drop=True).copy()
        g["avg_vol_prev60"] = g["volume"].shift(1).rolling(60, min_periods=20).mean()
        g["avg_amt_prev60"] = g["trade_amount"].shift(1).rolling(60, min_periods=20).mean()
        g["ma20"] = g["close"].rolling(20, min_periods=10).mean()
        g["ma60"] = g["close"].rolling(60, min_periods=30).mean()
        g["prev60_high"] = g["high"].shift(1).rolling(60, min_periods=20).max()
        for year in range(START_YEAR, END_YEAR + 1):
            y = g[g["year"] == year]
            if len(y) < 60:
                continue
            low_idx = y["low"].idxmin()
            low_row = g.loc[low_idx]
            after = g.loc[low_idx:]
            after_y = after[after["year"] == year]
            if after_y.empty:
                continue
            max_high = float(after_y["high"].max())
            min_low = float(low_row["low"])
            multiple = max_high / min_low if min_low > 0 else None
            win60 = g.loc[low_idx : low_idx + 60]
            win90 = g.loc[low_idx : low_idx + 90]
            win20 = g.loc[low_idx : low_idx + 20]
            base_vol = float(low_row["avg_vol_prev60"]) if pd.notna(low_row["avg_vol_prev60"]) else None
            base_amt = float(low_row["avg_amt_prev60"]) if pd.notna(low_row["avg_amt_prev60"]) else None
            vol_peak_x = float(win60["volume"].max()) / base_vol if base_vol and base_vol > 0 else None
            amount_peak_x = float(win60["trade_amount"].max()) / base_amt if base_amt and base_amt > 0 else None
            ma20_reclaim = bool((win60["close"] > win60["ma20"]).fillna(False).any())
            ma60_reclaim = bool((win90["close"] > win90["ma60"]).fillna(False).any())
            new_60d_high = bool((win90["high"] > win90["prev60_high"]).fillna(False).any())
            inst20 = float(win20["inst_net_buy_amt"].sum())
            frn20 = float(win20["frn_net_buy_amt"].sum())
            follow20 = (float(win20["high"].max()) / min_low - 1) if min_low > 0 else None
            rows.append({
                "stock_code": code,
                "stock_name": low_row["stock_name"],
                "year": year,
                "low_date": low_row["date"].strftime("%Y-%m-%d"),
                "market": low_row["market"],
                "sector": low_row["sector"] or "미분류",
                "theme": low_row["theme"] or "",
                "market_cap": _num(low_row["market_cap"], 1),
                "min_low": _num(min_low),
                "max_high_after_low": _num(max_high),
                "multiple": _num(multiple, 2),
                "winner": bool(multiple and 3 <= multiple <= 50),
                "vol_peak_x": _num(vol_peak_x, 2),
                "amount_peak_x": _num(amount_peak_x, 2),
                "inst20_amt": _num(inst20, 1),
                "frn20_amt": _num(frn20, 1),
                "follow20_ret": _num(follow20, 4),
                "amount_10x": bool(amount_peak_x and amount_peak_x >= 10),
                "amount_5x": bool(amount_peak_x and amount_peak_x >= 5),
                "volume_10x": bool(vol_peak_x and vol_peak_x >= 10),
                "volume_3x": bool(vol_peak_x and vol_peak_x >= 3),
                "ma20_reclaim": ma20_reclaim,
                "ma60_reclaim": ma60_reclaim,
                "new_60d_high": new_60d_high,
                "inst_20d_buy": inst20 > 0,
                "frn_20d_buy": frn20 > 0,
                "both_inst_frn_buy": inst20 > 0 and frn20 > 0,
                "small_cap_3000": bool(pd.notna(low_row["market_cap"]) and float(low_row["market_cap"]) <= 3000),
                "kosdaq": low_row["market"] == "KOSDAQ",
                "core_sector": (low_row["sector"] or "") in core_sectors,
            })
    return pd.DataFrame(rows)


def _enrich_financial(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    fin = pd.read_sql_query(
        """
        SELECT stock_code, year, quarter, revenue, operating_profit
        FROM financial_data
        WHERE is_annual = 0 AND quarter > 0 AND year BETWEEN ? AND ?
        """,
        conn,
        params=(START_YEAR - 1, END_YEAR),
    )
    if fin.empty:
        return panel
    prev = fin.rename(columns={"year": "prev_year", "revenue": "prev_revenue", "operating_profit": "prev_op"})
    cur = fin.merge(
        prev[["stock_code", "prev_year", "quarter", "prev_revenue", "prev_op"]],
        left_on=["stock_code", "year", "quarter"],
        right_on=["stock_code", "prev_year", "quarter"],
        how="left",
    )
    cur = cur[cur["prev_year"] == cur["year"] - 1]
    cur["revenue_yoy"] = (cur["revenue"] / cur["prev_revenue"] - 1) * 100
    cur["op_turnaround"] = (cur["operating_profit"] > 0) & (cur["prev_op"] < 0)
    latest = cur.sort_values(["stock_code", "year", "quarter"]).groupby(["stock_code", "year"]).tail(1)
    latest = latest[["stock_code", "year", "revenue_yoy", "operating_profit", "op_turnaround"]]
    out = panel.merge(latest, on=["stock_code", "year"], how="left")
    out["revenue_yoy_pos"] = out["revenue_yoy"] >= 0
    out["revenue_yoy_15"] = out["revenue_yoy"] >= 15
    out["op_profit_pos"] = out["operating_profit"] > 0
    out["op_turnaround"] = out["op_turnaround"].fillna(False).astype(bool)
    return out


def _enrich_contracts(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    c = pd.read_sql_query(
        """
        SELECT stock_code, CAST(substr(disclosed_at, 1, 4) AS INTEGER) AS year,
               COUNT(*) AS contract_count,
               SUM(CASE WHEN is_overseas = 1 THEN 1 ELSE 0 END) AS overseas_count
        FROM dart_contracts
        WHERE disclosed_at >= ? AND disclosed_at < ?
        GROUP BY stock_code, CAST(substr(disclosed_at, 1, 4) AS INTEGER)
        """,
        conn,
        params=(f"{START_YEAR}-01-01", f"{END_YEAR + 1}-01-01"),
    )
    out = panel.merge(c, on=["stock_code", "year"], how="left")
    out["contract_signal"] = out["contract_count"].fillna(0) > 0
    out["overseas_contract"] = out["overseas_count"].fillna(0) > 0
    return out


def _enrich_backlog_employee_material(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    backlog = pd.read_sql_query(
        """
        SELECT b.stock_code, b.fiscal_year AS year,
               MAX(b.backlog_amount_krw) AS backlog,
               MAX(p.backlog_amount_krw) AS prev_backlog
        FROM dart_backlog_quarterly b
        LEFT JOIN dart_backlog_quarterly p
          ON p.stock_code=b.stock_code AND p.fiscal_year=b.fiscal_year-1 AND p.fiscal_quarter=b.fiscal_quarter
        WHERE b.fiscal_year BETWEEN ? AND ?
          AND b.backlog_confidence >= 0.95
          AND p.backlog_confidence >= 0.95
        GROUP BY b.stock_code, b.fiscal_year
        """,
        conn,
        params=(START_YEAR, END_YEAR),
    )
    emp = pd.read_sql_query(
        """
        SELECT e.stock_code, e.year, MAX(e.total_emp) AS emp, MAX(p.total_emp) AS prev_emp
        FROM dart_employee_count e
        LEFT JOIN dart_employee_count p
          ON p.stock_code=e.stock_code AND p.year=e.year-1 AND p.reprt_code=e.reprt_code
        WHERE e.year BETWEEN ? AND ?
        GROUP BY e.stock_code, e.year
        """,
        conn,
        params=(START_YEAR, END_YEAR),
    )
    mat = pd.read_sql_query(
        """
        SELECT m.stock_code, m.year, MAX(m.material_purchase_krw) AS material, MAX(p.material_purchase_krw) AS prev_material
        FROM dart_material_purchase m
        LEFT JOIN dart_material_purchase p
          ON p.stock_code=m.stock_code AND p.year=m.year-1
        WHERE m.year BETWEEN ? AND ?
        GROUP BY m.stock_code, m.year
        """,
        conn,
        params=(START_YEAR, END_YEAR),
    )
    out = panel.merge(backlog, on=["stock_code", "year"], how="left")
    out = out.merge(emp, on=["stock_code", "year"], how="left")
    out = out.merge(mat, on=["stock_code", "year"], how="left")
    backlog_ratio = out[["backlog", "prev_backlog"]].max(axis=1) / out[["backlog", "prev_backlog"]].min(axis=1).replace(0, np.nan)
    out["backlog_growth"] = (
        (out["backlog"] > out["prev_backlog"])
        & (out["prev_backlog"] > 0)
        & backlog_ratio.le(20.0)
    )
    out["employee_growth"] = (out["emp"] > out["prev_emp"]) & (out["prev_emp"] > 0)
    out["material_purchase_growth"] = (out["material"] > out["prev_material"]) & (out["prev_material"] > 0)
    return out


def _enrich_short(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    short = pd.read_sql_query(
        """
        SELECT stock_code, substr(bas_dt, 1, 4) || '-' || substr(bas_dt, 5, 2) || '-' || substr(bas_dt, 7, 2) AS date,
               borrow_bal_pct
        FROM short_sell_daily
        WHERE bas_dt >= ? AND bas_dt < ?
        """,
        conn,
        params=(f"{START_YEAR}0101", f"{END_YEAR + 1}0101"),
    )
    if short.empty:
        panel["short_cover"] = False
        panel["short_pressure"] = False
        return panel
    short["date"] = pd.to_datetime(short["date"])
    by_code = {c: g.sort_values("date") for c, g in short.groupby("stock_code")}
    covers = []
    pressure = []
    for r in panel[["stock_code", "low_date"]].itertuples(index=False):
        g = by_code.get(r.stock_code)
        if g is None:
            covers.append(False)
            pressure.append(False)
            continue
        d = pd.to_datetime(r.low_date)
        before = g[g["date"] <= d].tail(5)
        after = g[(g["date"] >= d) & (g["date"] <= d + pd.Timedelta(days=90))]
        b = before["borrow_bal_pct"].median() if not before.empty else None
        a_min = after["borrow_bal_pct"].min() if not after.empty else None
        a_max = after["borrow_bal_pct"].max() if not after.empty else None
        covers.append(bool(pd.notna(b) and pd.notna(a_min) and b > 0 and (b - a_min) / b >= 0.2))
        pressure.append(bool(pd.notna(a_max) and (a_max >= 3 or (pd.notna(b) and a_max > b))))
    panel["short_cover"] = covers
    panel["short_pressure"] = pressure
    return panel


def _enrich_exports(panel: pd.DataFrame) -> pd.DataFrame:
    if not HS_DB.exists():
        panel["export_yoy"] = None
        panel["export_yoy_30"] = False
        panel["export_yoy_100"] = False
        panel["export_value_rising"] = False
        return panel
    conn = sqlite3.connect(HS_DB)
    try:
        hs = pd.read_sql_query(
            """
            SELECT stock_code, period_ym, SUM(export_value) AS export_value
            FROM analysis2_company_monthly_cache
            WHERE period_ym >= ? AND period_ym < ?
            GROUP BY stock_code, period_ym
            """,
            conn,
            params=(f"{START_YEAR - 1}-01", f"{END_YEAR + 1}-01"),
        )
    finally:
        conn.close()
    if hs.empty:
        panel["export_yoy"] = None
        panel["export_yoy_30"] = False
        panel["export_yoy_100"] = False
        panel["export_value_rising"] = False
        return panel
    hs["date"] = pd.to_datetime(hs["period_ym"] + "-01")
    hs = hs.sort_values(["stock_code", "date"])
    rows = []
    for code, g in hs.groupby("stock_code"):
        g = g.copy()
        g["ma3"] = g["export_value"].rolling(3, min_periods=2).mean()
        g["prev_ma3"] = g["ma3"].shift(12)
        g["export_yoy"] = (g["ma3"] / g["prev_ma3"] - 1) * 100
        for year in range(START_YEAR, END_YEAR + 1):
            y = g[g["date"].dt.year == year]
            if y.empty:
                continue
            best = y.sort_values("export_yoy").tail(1)
            if best.empty:
                continue
            row = best.iloc[0]
            rows.append({"stock_code": code, "year": year, "export_yoy": _num(row["export_yoy"], 1), "export_ma3": _num(row["ma3"], 1), "export_prev_ma3": _num(row["prev_ma3"], 1)})
    exp = pd.DataFrame(rows)
    out = panel.merge(exp, on=["stock_code", "year"], how="left")
    out["export_yoy_30"] = out["export_yoy"] >= 30
    out["export_yoy_100"] = out["export_yoy"] >= 100
    out["export_value_rising"] = out["export_yoy"] > 0
    return out


def _build_failure_flags(panel: pd.DataFrame) -> pd.DataFrame:
    panel["amount_under_5x"] = panel["amount_peak_x"].fillna(0) < 5
    panel["no_ma60_reclaim"] = ~panel["ma60_reclaim"].fillna(False)
    panel["inst_frn_both_sell"] = (panel["inst20_amt"].fillna(0) < 0) & (panel["frn20_amt"].fillna(0) < 0)
    panel["revenue_yoy_neg"] = panel["revenue_yoy"] < 0
    panel["op_loss"] = panel["operating_profit"] < 0
    panel["export_yoy_neg"] = panel["export_yoy"] < 0
    panel["large_cap"] = panel["market_cap"].fillna(0) > 3000
    panel["no_contract"] = ~panel["contract_signal"].fillna(False)
    panel["no_20d_followthrough"] = panel["follow20_ret"].fillna(0) < 0.15
    return panel


def _signal_stats(panel: pd.DataFrame) -> list[dict]:
    total = len(panel)
    winners = panel["winner"].sum()
    base_rate = winners / total if total else 0
    rows = []
    for key, (name, category, definition) in TRIGGER_DEFS.items():
        if key not in panel:
            continue
        s = panel[panel[key].fillna(False)]
        n = len(s)
        if n == 0:
            continue
        w = int(s["winner"].sum())
        rate = w / n
        rows.append({
            "key": key,
            "name": name,
            "category": category,
            "definition": definition,
            "signal_count": n,
            "winner_count": w,
            "winner_rate": round(rate * 100, 1),
            "winner_coverage": _safe_pct(w, winners),
            "lift_vs_base": round(rate / base_rate, 2) if base_rate else None,
            "median_multiple_when_signal": _num(s["multiple"].median(), 2),
            "failed_count": int(n - w),
        })
    return sorted(rows, key=lambda r: (r["lift_vs_base"] or 0, r["winner_coverage"], r["winner_rate"]), reverse=True)


def _combo_stats(panel: pd.DataFrame, keys: list[str]) -> list[dict]:
    rows = []
    total = len(panel)
    base_rate = panel["winner"].mean() if total else 0
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            if a not in panel or b not in panel:
                continue
            s = panel[panel[a].fillna(False) & panel[b].fillna(False)]
            if len(s) < 20:
                continue
            rate = s["winner"].mean()
            rows.append({
                "signals": [TRIGGER_DEFS[a][0], TRIGGER_DEFS[b][0]],
                "keys": [a, b],
                "count": int(len(s)),
                "winner_count": int(s["winner"].sum()),
                "winner_rate": round(rate * 100, 1),
                "lift_vs_base": round(rate / base_rate, 2) if base_rate else None,
                "median_multiple": _num(s["multiple"].median(), 2),
            })
    return sorted(rows, key=lambda r: (r["lift_vs_base"] or 0, r["winner_rate"], r["count"]), reverse=True)[:20]


def _failure_stats(panel: pd.DataFrame) -> list[dict]:
    rows = []
    for key, (name, category, _) in TRIGGER_DEFS.items():
        if key not in panel:
            continue
        s = panel[panel[key].fillna(False)]
        failed = s[~s["winner"]]
        won = s[s["winner"]]
        if len(s) < 20 or failed.empty:
            continue
        blockers = []
        for fkey, (fname, fdef) in FAIL_DEFS.items():
            if fkey not in panel:
                continue
            fp = failed[fkey].fillna(False).mean()
            wp = won[fkey].fillna(False).mean() if len(won) else 0
            blockers.append({
                "key": fkey,
                "name": fname,
                "definition": fdef,
                "failed_rate": round(fp * 100, 1),
                "winner_rate": round(wp * 100, 1),
                "gap": round((fp - wp) * 100, 1),
            })
        blockers = sorted(blockers, key=lambda r: (r["gap"], r["failed_rate"]), reverse=True)[:5]
        rows.append({
            "trigger_key": key,
            "trigger_name": name,
            "category": category,
            "signal_count": int(len(s)),
            "failed_count": int(len(failed)),
            "winner_rate": round(s["winner"].mean() * 100, 1),
            "dominant_failure_signals": blockers,
        })
    return sorted(rows, key=lambda r: r["failed_count"], reverse=True)[:18]


def _sample_rows(panel: pd.DataFrame, winner: bool, limit: int = 30) -> list[dict]:
    cols = ["year", "stock_code", "stock_name", "market", "sector", "multiple", "amount_peak_x", "vol_peak_x", "revenue_yoy", "export_yoy"]
    df = panel[panel["winner"] == winner].sort_values(["multiple", "amount_peak_x"], ascending=False).head(limit)
    return [{k: (_num(v, 2) if isinstance(v, (float, int)) else v) for k, v in row.items()} for row in df[cols].to_dict("records")]


def build() -> dict:
    conn = sqlite3.connect(STOCK_DB)
    try:
        price = _load_price_panel(conn)
        panel = _price_features(price)
        panel = _enrich_financial(conn, panel)
        panel = _enrich_contracts(conn, panel)
        panel = _enrich_backlog_employee_material(conn, panel)
        panel = _enrich_short(conn, panel)
    finally:
        conn.close()
    panel = _enrich_exports(panel)
    panel = _build_failure_flags(panel)

    for key in list(TRIGGER_DEFS) + list(FAIL_DEFS):
        if key in panel:
            panel[key] = panel[key].fillna(False).astype(bool)

    winners = panel[panel["winner"]]
    signal_keys = [k for k in TRIGGER_DEFS if k in panel]
    best_combo_keys = [
        "amount_10x", "amount_5x", "volume_10x", "ma60_reclaim", "new_60d_high",
        "revenue_yoy_15", "export_yoy_30", "contract_signal", "small_cap_3000", "core_sector",
        "inst_20d_buy", "frn_20d_buy", "short_cover",
    ]
    by_year = []
    for year, g in panel.groupby("year"):
        by_year.append({
            "year": int(year),
            "sample_count": int(len(g)),
            "winner_count": int(g["winner"].sum()),
            "winner_rate": round(g["winner"].mean() * 100, 2),
        })

    sector_counts = Counter(winners["sector"].fillna("미분류"))
    payload = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "stock.db price_history/investor/short/DART + hs_trade_lab analysis2_company_monthly_cache",
        "scope": {
            "start_year": START_YEAR,
            "end_year": END_YEAR,
            "unit": "stock-year low-date cohort",
            "winner_definition": "calendar-year low date 이후 같은 연도 고가가 저가 대비 3~50배",
            "sample_count": int(len(panel)),
            "winner_count": int(panel["winner"].sum()),
            "base_winner_rate": round(panel["winner"].mean() * 100, 2) if len(panel) else 0,
        },
        "by_year": sorted(by_year, key=lambda r: r["year"], reverse=True),
        "sector_distribution": [
            {"sector": k, "winner_count": int(v), "pct": _safe_pct(v, len(winners))}
            for k, v in sector_counts.most_common(12)
        ],
        "trigger_stats": _signal_stats(panel),
        "combo_stats": _combo_stats(panel, best_combo_keys),
        "failure_stats": _failure_stats(panel),
        "top_winners": _sample_rows(panel, True, 40),
        "failed_signal_examples": _sample_rows(panel[(panel["amount_5x"] | panel["volume_3x"] | panel["ma60_reclaim"]) & ~panel["winner"]], False, 40),
        "caveats": [
            "트리거는 저점 이후 20~90거래일 내 관측 가능한 신호 기준입니다. 실제 매수 가능성은 신호 발생일의 종가/거래대금으로 별도 백테스트가 필요합니다.",
            "DART/수출/직원/원재료 데이터는 기업별 커버리지가 달라 해당 신호의 표본 수가 가격·거래량 신호보다 작습니다.",
            "수출 신호는 회사-HS 매핑 캐시의 3개월 평균 YoY를 사용해 당월 미확정 0값 왜곡을 줄였습니다.",
        ],
    }
    return payload


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build()
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT_PATH), "scope": payload["scope"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
