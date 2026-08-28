#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import research_signal_trigger_analysis as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "three_x_capture_filter_logic_2021plus.json"
RISK_DISCLOSURE_RE = re.compile(
    r"상장폐지|정리매매|거래정리|매매거래정지|거래정지|관리종목|투자주의환기|"
    r"투자경고|투자위험|불성실|횡령|배임|감사의견|의견거절|한정|매수제한"
)


def build_panel() -> pd.DataFrame:
    conn = sqlite3.connect(base.STOCK_DB)
    try:
        price = base._load_price_panel(conn)
        panel = base._price_features(price)
        panel = base._enrich_financial(conn, panel)
        panel = base._enrich_contracts(conn, panel)
        panel = base._enrich_backlog_employee_material(conn, panel)
        panel = base._enrich_short(conn, panel)
        panel = add_debt_risk(conn, panel)
        panel = add_disclosure_risk(conn, panel)
    finally:
        conn.close()
    panel = base._enrich_exports(panel)
    panel = base._build_failure_flags(panel)
    return panel[panel["year"] >= 2021].copy()


def add_debt_risk(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    fin = pd.read_sql_query(
        """
        SELECT stock_code, year,
               total_liabilities, total_equity,
               CASE
                   WHEN total_equity > 0 THEN total_liabilities / total_equity * 100.0
                   ELSE NULL
               END AS debt_ratio
        FROM financial_data
        WHERE is_annual = 1
          AND year BETWEEN 2019 AND 2026
          AND total_liabilities IS NOT NULL
          AND total_equity IS NOT NULL
        """,
        conn,
    )
    if fin.empty:
        panel["prev_debt_ratio"] = None
        panel["debt_over_500"] = False
        panel["capital_impaired"] = False
        return panel
    fin["signal_year"] = fin["year"] + 1
    latest = fin.sort_values(["stock_code", "signal_year"]).drop_duplicates(["stock_code", "signal_year"], keep="last")
    out = panel.merge(
        latest[["stock_code", "signal_year", "debt_ratio", "total_equity"]],
        left_on=["stock_code", "year"],
        right_on=["stock_code", "signal_year"],
        how="left",
    )
    out = out.drop(columns=["signal_year"])
    out = out.rename(columns={"debt_ratio": "prev_debt_ratio"})
    out["debt_over_500"] = out["prev_debt_ratio"] > 500
    out["capital_impaired"] = out["total_equity"] <= 0
    return out


def add_disclosure_risk(conn: sqlite3.Connection, panel: pd.DataFrame) -> pd.DataFrame:
    disc = pd.read_sql_query(
        """
        SELECT stock_code, rcept_dt, report_nm
        FROM dart_disclosures
        WHERE rcept_dt >= '20200101'
        """,
        conn,
    )
    panel["risk_disclosure_before_low"] = False
    if disc.empty:
        return panel
    disc["rcept_dt"] = pd.to_datetime(disc["rcept_dt"], format="%Y%m%d", errors="coerce")
    disc = disc[disc["report_nm"].fillna("").str.contains(RISK_DISCLOSURE_RE, regex=True, na=False)]
    if disc.empty:
        return panel
    by_code = {code: g.sort_values("rcept_dt") for code, g in disc.groupby("stock_code")}
    flags = []
    for row in panel[["stock_code", "low_date"]].itertuples(index=False):
        g = by_code.get(row.stock_code)
        if g is None:
            flags.append(False)
            continue
        d = pd.to_datetime(row.low_date)
        since = d - pd.Timedelta(days=365)
        flags.append(bool(((g["rcept_dt"] >= since) & (g["rcept_dt"] <= d)).any()))
    panel["risk_disclosure_before_low"] = flags
    return panel


def eval_rule(panel: pd.DataFrame, name: str, mask: pd.Series, winners_total: int) -> dict:
    s = panel[mask.fillna(False)]
    w = int(s["winner"].sum())
    return {
        "name": name,
        "count": int(len(s)),
        "winner_count": w,
        "winner_rate_pct": round(float(s["winner"].mean() * 100), 2) if len(s) else 0.0,
        "winner_capture_pct": round(w / winners_total * 100, 2) if winners_total else 0.0,
        "median_multiple": base._num(s["multiple"].median(), 2) if len(s) else None,
    }


def main() -> int:
    panel = build_panel()
    winners_total = int(panel["winner"].sum())
    broad = (
        panel["amount_5x"].fillna(False)
        | panel["volume_3x"].fillna(False)
        | panel["ma60_reclaim"].fillna(False)
        | panel["new_60d_high"].fillna(False)
    )
    pulse = (
        (panel["amount_peak_x"].fillna(0) >= 8)
        | (panel["vol_peak_x"].fillna(0) >= 10)
    )
    trend = panel["ma60_reclaim"].fillna(False) | panel["new_60d_high"].fillna(False)
    kosdaq_growth = panel["kosdaq"].fillna(False) | panel["core_sector"].fillna(False)
    follow = panel["follow20_ret"].fillna(0) >= 0.15
    risk = (
        panel["debt_over_500"].fillna(False)
        | panel["capital_impaired"].fillna(False)
        | panel["risk_disclosure_before_low"].fillna(False)
    )

    rules = []
    candidates = {
        "broad_or": broad,
        "pulse_8x10x": pulse,
        "pulse_and_trend": pulse & trend,
        "pulse_trend_kosdaq_or_growth": pulse & trend & kosdaq_growth,
        "pulse_trend_followthrough": pulse & trend & follow,
        "pulse_trend_followthrough_risk_off": pulse & trend & follow & ~risk,
        "broad_or_risk_off": broad & ~risk,
    }
    for name, mask in candidates.items():
        rules.append(eval_rule(panel, name, mask, winners_total))

    risk_rows = []
    for risk_name in ["debt_over_500", "capital_impaired", "risk_disclosure_before_low"]:
        if risk_name in panel:
            risk_rows.append(eval_rule(panel, risk_name, panel[risk_name], winners_total))

    sample_cols = [
        "year", "stock_code", "stock_name", "market", "sector", "multiple",
        "amount_peak_x", "vol_peak_x", "follow20_ret", "prev_debt_ratio",
        "risk_disclosure_before_low",
    ]
    best_mask = candidates["pulse_trend_followthrough_risk_off"]
    sample = panel[best_mask].sort_values(["winner", "multiple"], ascending=[False, False]).head(80)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {
            "years": "2021-2026",
            "unit": "stock-year low-date cohort",
            "winner_definition": "calendar-year low date 이후 같은 연도 고가가 저가 대비 3~50배",
            "sample_count": int(len(panel)),
            "winner_count": winners_total,
            "base_winner_rate_pct": round(float(panel["winner"].mean() * 100), 2),
            "method_note": "저점 이후 관측 신호 기반 연구용 포획률입니다. 실전형은 신호 발생일 기준 월/일별 백테스트로 별도 검증해야 합니다.",
        },
        "rules": rules,
        "risk_filter_stats": risk_rows,
        "recommended_logic": {
            "capture": "(거래대금 피크 >= 직전60일 평균 8배 OR 거래량 피크 >= 직전60일 평균 10배) AND (60일선 회복 OR 60일 신고가 재돌파)",
            "optional_quality": "KOSDAQ 또는 IT/의료/경기소비재/산업재 섹터를 가산점으로 사용",
            "confirm": "저점 이후 20거래일 고가 수익률 +15% 이상이면 실패 종목 제거 효과가 큼",
            "exclude": "전년도 부채비율 500% 초과, 자본잠식, 최근 1년 상장폐지/정리매매/거래정지/관리종목/투자주의환기/감사의견 리스크 공시",
        },
        "sample_candidates": sample[sample_cols].to_dict("records"),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "scope": payload["scope"], "rules": rules}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
