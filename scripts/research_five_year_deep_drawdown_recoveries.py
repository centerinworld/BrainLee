#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs" / "deep_drawdown_recovery_5y"
START = "2020-01-01"  # 2021 사건의 52주 롤링 계산용 버퍼
EVENT_START = pd.Timestamp("2021-01-01")
EVENT_END = pd.Timestamp("2025-12-31")  # 2026년은 후속수익 관찰 기간으로 사용


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0) or pd.isna(a) or pd.isna(b):
        return None
    return round((float(a) / float(b) - 1.0) * 100.0, 2)


def load_data(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict[str, dict], set[str]]:
    prices = pd.read_sql_query(
        """
        SELECT stock_code, date, close, volume,
               inst_net_buy, frn_net_buy
        FROM price_history
        WHERE date>=? AND close>0
        ORDER BY stock_code, date
        """,
        conn,
        params=(START,),
        parse_dates=["date"],
    )
    prices = prices.dropna(subset=["date", "close"])
    latest = pd.read_sql_query(
        """
        WITH ranked AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY base_date DESC, id DESC) rn
          FROM stock_universe
        )
        SELECT stock_code, stock_name, market, stock_type, sector_large, market_cap
        FROM ranked WHERE rn=1
        """,
        conn,
    )
    meta = latest.set_index("stock_code").to_dict("index")
    active = set(latest.loc[
        latest["market"].isin(["KOSPI", "KOSDAQ"])
        & latest["stock_type"].fillna("보통주").eq("보통주"),
        "stock_code",
    ])
    return prices, meta, active


def add_features(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("date").copy()
    g["high52"] = g["close"].rolling(252, min_periods=120).max()
    g["low52"] = g["close"].rolling(252, min_periods=120).min()
    g["ma20"] = g["close"].rolling(20, min_periods=15).mean()
    g["ma60"] = g["close"].rolling(60, min_periods=40).mean()
    g["vol20"] = g["volume"].rolling(20, min_periods=15).mean()
    ratio = g["close"] / g["close"].shift(1)
    g["corporate_action_jump"] = (ratio > 1.8) | (ratio < 0.55)
    g["drawdown_pct"] = (g["close"] / g["high52"] - 1.0) * 100.0
    g["from_low_pct"] = (g["close"] / g["low52"] - 1.0) * 100.0
    return g


def detect_episodes(code: str, g: pd.DataFrame) -> list[dict]:
    rows = g.reset_index(drop=True)
    episodes: list[dict] = []
    i = 0
    n = len(rows)
    while i < n:
        row = rows.iloc[i]
        if row["date"] < EVENT_START or row["date"] > EVENT_END or pd.isna(row["high52"]):
            i += 1
            continue
        deep = row["drawdown_pct"] <= -60.0
        low_touch = row["from_low_pct"] <= 2.0
        if not (deep or low_touch):
            i += 1
            continue

        trigger_i = i
        trough_i = i
        deadline_i = min(n - 1, trigger_i + 252)
        recovery_i: int | None = None
        j = i + 1
        while j <= deadline_i:
            if rows.iloc[j]["close"] < rows.iloc[trough_i]["close"]:
                trough_i = j
            days_from_trough = j - trough_i
            rebound = rows.iloc[j]["close"] / rows.iloc[trough_i]["close"] - 1.0
            ma20_rising = j >= 5 and rows.iloc[j]["ma20"] > rows.iloc[j - 5]["ma20"]
            if (
                days_from_trough >= 10
                and rebound >= 0.30
                and rows.iloc[j]["close"] > rows.iloc[j]["ma20"]
                and ma20_rising
            ):
                recovery_i = j
                break
            j += 1

        trigger = rows.iloc[trigger_i]
        trough = rows.iloc[trough_i]
        recovery = rows.iloc[recovery_i] if recovery_i is not None else None
        audit_end = min(n - 1, max(deadline_i, (recovery_i or trigger_i) + 252))
        audit_start = max(0, trigger_i - 252)
        if rows.iloc[audit_start:audit_end + 1]["corporate_action_jump"].any():
            i = deadline_i + 1
            continue
        base = {
            "stock_code": code,
            "trigger_date": trigger["date"].date().isoformat(),
            "trigger_type": "deep_drawdown" if deep else "new_52w_low",
            "trigger_close": float(trigger["close"]),
            "trigger_drawdown_pct": round(float(trigger["drawdown_pct"]), 2),
            "trough_date": trough["date"].date().isoformat(),
            "trough_close": float(trough["close"]),
            "trough_drawdown_pct": round(float(trough["close"] / trigger["high52"] - 1) * 100, 2),
            "additional_loss_after_trigger_pct": _pct(float(trough["close"]), float(trigger["close"])),
            "recovered_within_1y": recovery_i is not None,
            "recovery_date": recovery["date"].date().isoformat() if recovery is not None else None,
            "days_trigger_to_trough": int(trough_i - trigger_i),
            "days_trough_to_recovery": int(recovery_i - trough_i) if recovery_i is not None else None,
            "days_trigger_to_recovery": int(recovery_i - trigger_i) if recovery_i is not None else None,
            "rebound_at_confirmation_pct": _pct(float(recovery["close"]), float(trough["close"])) if recovery is not None else None,
            "recovery_close": float(recovery["close"]) if recovery is not None else None,
        }
        for horizon in (60, 120, 252):
            k = trigger_i + horizon
            base[f"trigger_return_{horizon}d_pct"] = _pct(float(rows.iloc[k]["close"]), float(trigger["close"])) if k < n else None
        if recovery_i is not None:
            for horizon in (60, 120, 252):
                k = recovery_i + horizon
                base[f"return_{horizon}d_pct"] = _pct(float(rows.iloc[k]["close"]), float(recovery["close"])) if k < n else None
            future = rows.iloc[recovery_i:min(n, recovery_i + 253)]
            base["post_recovery_max_gain_1y_pct"] = _pct(float(future["close"].max()), float(recovery["close"]))
            base["post_recovery_max_loss_1y_pct"] = _pct(float(future["close"].min()), float(recovery["close"]))
            recent20 = rows.iloc[max(0, recovery_i - 19):recovery_i + 1]
            base["volume_ratio_20d"] = round(float(recovery["volume"] / recovery["vol20"]), 2) if recovery["vol20"] else None
            base["inst_net_20d"] = round(float(recent20["inst_net_buy"].fillna(0).sum()), 2)
            base["frn_net_20d"] = round(float(recent20["frn_net_buy"].fillna(0).sum()), 2)
        episodes.append(base)
        i = (recovery_i + 126) if recovery_i is not None else (deadline_i + 1)
    return episodes


def financial_catalyst(conn: sqlite3.Connection, event: dict) -> tuple[list[str], list[str]]:
    code = event["stock_code"]
    asof = event.get("recovery_date") or event["trough_date"]
    year = int(asof[:4])
    month = int(asof[5:7])
    rows = conn.execute(
        """
        SELECT year, quarter, revenue, operating_profit, net_income
        FROM canonical_financial_data
        WHERE stock_code=? AND report_type='CFS' AND is_annual=0 AND year BETWEEN ? AND ?
        ORDER BY year, quarter
        """,
        (code, year - 2, year),
    ).fetchall()
    reasons: list[str] = []
    evidence: list[str] = []
    # 분기보고서 통상 공시 시점을 보수적으로 반영해 사건 당시 공개 가능 분기만 사용한다.
    if month <= 3:
        cutoff = (year - 1, 3)
    elif month <= 5:
        cutoff = (year - 1, 4)
    elif month <= 8:
        cutoff = (year, 1)
    elif month <= 11:
        cutoff = (year, 2)
    else:
        cutoff = (year, 3)
    vals = [
        dict(r) for r in rows
        if r["quarter"] in (1, 2, 3, 4) and (r["year"], r["quarter"]) <= cutoff
    ]
    if len(vals) >= 2:
        last, prev = vals[-1], vals[-2]
        if (last["operating_profit"] or 0) > 0 >= (prev["operating_profit"] or 0):
            reasons.append("operating_turnaround")
            evidence.append(f"영업이익 흑자전환 {prev['operating_profit']}→{last['operating_profit']}")
        elif prev["operating_profit"] not in (None, 0) and last["operating_profit"] is not None:
            growth = (last["operating_profit"] / abs(prev["operating_profit"]) - 1) * 100
            if growth >= 30:
                reasons.append("earnings_acceleration")
                evidence.append(f"영업이익 개선 {growth:+.1f}%")
        if prev["revenue"] not in (None, 0) and last["revenue"] is not None:
            growth = (last["revenue"] / abs(prev["revenue"]) - 1) * 100
            if growth >= 20:
                reasons.append("revenue_growth")
                evidence.append(f"매출 증가 {growth:+.1f}%")

    if event.get("volume_ratio_20d") and event["volume_ratio_20d"] >= 1.8:
        reasons.append("volume_breakout")
        evidence.append(f"거래량 {event['volume_ratio_20d']:.1f}배")
    if (event.get("inst_net_20d") or 0) > 0 and (event.get("frn_net_20d") or 0) > 0:
        reasons.append("foreign_institution_buying")
        evidence.append("외국인·기관 20일 동반 순매수")
    elif (event.get("inst_net_20d") or 0) > 0:
        reasons.append("institution_buying")
        evidence.append("기관 20일 순매수")
    elif (event.get("frn_net_20d") or 0) > 0:
        reasons.append("foreign_buying")
        evidence.append("외국인 20일 순매수")

    disclosure_rows = conn.execute(
        """
        SELECT report_nm FROM dart_disclosures
        WHERE stock_code=? AND rcept_dt BETWEEN replace(date(?, '-120 day'),'-','') AND replace(?,'-','')
        ORDER BY rcept_dt DESC LIMIT 30
        """,
        (code, asof, asof),
    ).fetchall()
    names = " ".join(str(r[0] or "") for r in disclosure_rows)
    disclosure_rules = [
        ("수주|공급계약", "order_contract", "수주·공급계약 공시"),
        ("자기주식|소각", "buyback", "자사주·소각 공시"),
        ("합병|영업양수|분할", "restructuring", "사업재편 공시"),
        ("유상증자|전환사채|신주인수권", "financing_event", "자금조달 공시"),
    ]
    import re
    for pattern, key, label in disclosure_rules:
        if re.search(pattern, names):
            reasons.append(key)
            evidence.append(label)
    if not reasons and event["recovered_within_1y"]:
        reasons.append("technical_or_market_rebound")
        evidence.append("DB에서 확인되는 개별 펀더멘털 촉매 없음")
    return list(dict.fromkeys(reasons)), list(dict.fromkeys(evidence))


def summarize(df: pd.DataFrame, active: set[str]) -> dict:
    recovered = df[df["recovered_within_1y"]]
    failed = df[~df["recovered_within_1y"]]
    active_df = df[df["stock_code"].isin(active)]
    bins = [-101, -80, -70, -60, -50, -40, -30, 1]
    labels = ["<=-80", "-80~-70", "-70~-60", "-60~-50", "-50~-40", "-40~-30", ">-30"]
    tmp = df.copy()
    tmp["trough_bin"] = pd.cut(tmp["trough_drawdown_pct"], bins=bins, labels=labels)
    by_bin = []
    for label, group in tmp.groupby("trough_bin", observed=True):
        wins = group[group["recovered_within_1y"]]
        by_bin.append({
            "bin": str(label), "events": len(group),
            "recovery_rate_pct": round(group["recovered_within_1y"].mean() * 100, 1),
            "median_additional_loss_pct": round(group["additional_loss_after_trigger_pct"].median(), 1),
            "median_252d_return_after_confirmation_pct": round(wins["return_252d_pct"].median(), 1) if len(wins) else None,
            "median_252d_return_from_trigger_pct": round(group["trigger_return_252d_pct"].median(), 1),
            "positive_252d_rate_pct": round(group["trigger_return_252d_pct"].gt(0).mean() * 100, 1),
        })
    observed_252 = df.dropna(subset=["trigger_return_252d_pct"])
    first_events = (
        df.sort_values(["stock_code", "trigger_date"])
        .drop_duplicates("stock_code")
        .dropna(subset=["trigger_return_252d_pct"])
    )
    cause_labels = {
        "earnings_acceleration": "영업이익 개선",
        "operating_turnaround": "영업이익 흑자전환",
        "financing_event": "자금조달 공시",
        "order_contract": "수주·공급계약 공시",
        "foreign_institution_buying": "외국인·기관 동반 순매수",
        "volume_breakout": "거래량 급증",
    }
    cause_outcomes = []
    for key, label in cause_labels.items():
        group = recovered[
            recovered["cause_keys"].map(lambda values: key in values)
        ].dropna(subset=["return_252d_pct"])
        cause_outcomes.append({
            "cause": key,
            "label": label,
            "events": len(group),
            "median_252d_return_after_confirmation_pct": round(group["return_252d_pct"].median(), 2),
            "positive_252d_rate_pct": round(group["return_252d_pct"].gt(0).mean() * 100, 2),
        })
    return {
        "period": "2021-01-01~2025-12-31 (outcomes through 2026-07-11)",
        "all_events": len(df),
        "all_stocks": int(df["stock_code"].nunique()),
        "recovered_events": len(recovered),
        "failed_events": len(failed),
        "recovery_rate_pct": round(df["recovered_within_1y"].mean() * 100, 2),
        "active_only_events": len(active_df),
        "active_only_recovery_rate_pct": round(active_df["recovered_within_1y"].mean() * 100, 2) if len(active_df) else None,
        "inactive_or_delisted_events": int((~df["stock_code"].isin(active)).sum()),
        "median_additional_loss_after_trigger_pct": round(df["additional_loss_after_trigger_pct"].median(), 2),
        "median_days_to_recovery": round(recovered["days_trigger_to_recovery"].median(), 1) if len(recovered) else None,
        "median_returns_after_confirmation_pct": {
            str(h): round(recovered[f"return_{h}d_pct"].median(), 2) for h in (60, 120, 252)
        },
        "median_returns_from_trigger_pct": {
            str(h): round(df[f"trigger_return_{h}d_pct"].median(), 2) for h in (60, 120, 252)
        },
        "observed_252d_events": len(observed_252),
        "positive_252d_rate_pct": round(observed_252["trigger_return_252d_pct"].gt(0).mean() * 100, 2),
        "gain_over_20pct_252d_rate_pct": round(observed_252["trigger_return_252d_pct"].gt(20).mean() * 100, 2),
        "first_event_only": {
            "events": len(first_events),
            "median_252d_return_pct": round(first_events["trigger_return_252d_pct"].median(), 2),
            "positive_252d_rate_pct": round(first_events["trigger_return_252d_pct"].gt(0).mean() * 100, 2),
            "gain_over_20pct_252d_rate_pct": round(first_events["trigger_return_252d_pct"].gt(20).mean() * 100, 2),
        },
        "cause_counts": Counter(reason for reasons in recovered["cause_keys"] for reason in reasons),
        "cause_outcomes_after_confirmation": cause_outcomes,
        "by_trough_drawdown": by_bin,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    prices, meta, active = load_data(conn)
    events: list[dict] = []
    for code, group in prices.groupby("stock_code", sort=False):
        if len(group) < 140:
            continue
        featured = add_features(group)
        for event in detect_episodes(code, featured):
            event.update(meta.get(code, {}))
            event["is_active_common_stock"] = code in active
            causes, evidence = financial_catalyst(conn, event)
            event["cause_keys"] = causes
            event["cause_evidence"] = evidence
            events.append(event)
    conn.close()

    df = pd.DataFrame(events)
    if df.empty:
        raise SystemExit("No drawdown events detected")
    summary = summarize(df, active)
    serial = df.copy()
    serial["cause_keys"] = serial["cause_keys"].map(lambda x: json.dumps(x, ensure_ascii=False))
    serial["cause_evidence"] = serial["cause_evidence"].map(lambda x: json.dumps(x, ensure_ascii=False))
    serial.to_csv(OUT_DIR / "events.csv", index=False, encoding="utf-8-sig")
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=dict), encoding="utf-8")

    named_common = df[
        df["stock_name"].notna()
        & df["market"].isin(["KOSPI", "KOSDAQ"])
        & df["stock_type"].fillna("보통주").eq("보통주")
    ]
    recovered = named_common[
        named_common["recovered_within_1y"] & named_common["trigger_return_252d_pct"].notna()
    ].sort_values("trigger_return_252d_pct", ascending=False)
    failed = named_common[
        ~named_common["recovered_within_1y"]
    ].sort_values("additional_loss_after_trigger_pct")
    lines = [
        "# 최근 5년 낙폭과대·52주 신저가 회복 연구",
        "",
        f"- 사건 {summary['all_events']:,}건 / 종목 {summary['all_stocks']:,}개",
        f"- 1년 내 30% 반등 확인률: {summary['recovery_rate_pct']}%",
        "- 위 반등률은 저점 대비 기술적 반등 빈도이며 투자 승률이 아님",
        f"- 진입 후 252거래일 플러스 비율: {summary['positive_252d_rate_pct']}% ({summary['observed_252d_events']:,}건 관찰)",
        f"- 진입 후 252거래일 +20% 초과 비율: {summary['gain_over_20pct_252d_rate_pct']}%",
        f"- 종목당 첫 사건만 사용: 252일 중앙값 {summary['first_event_only']['median_252d_return_pct']}%, 플러스 {summary['first_event_only']['positive_252d_rate_pct']}%",
        f"- 현재 상장 보통주만 볼 때: {summary['active_only_recovery_rate_pct']}%",
        f"- 비활성·상장폐지 가능 종목 사건: {summary['inactive_or_delisted_events']:,}건",
        f"- 조건 진입 후 추가 하락 중앙값: {summary['median_additional_loss_after_trigger_pct']}%",
        f"- 반등 확인까지 중앙값: {summary['median_days_to_recovery']} 거래일",
        f"- 반등 확인 후 수익률 중앙값(60/120/252일): "
        f"{summary['median_returns_after_confirmation_pct']['60']}% / {summary['median_returns_after_confirmation_pct']['120']}% / {summary['median_returns_after_confirmation_pct']['252']}%",
        f"- 낙폭 조건 즉시 매수 수익률 중앙값(60/120/252일): "
        f"{summary['median_returns_from_trigger_pct']['60']}% / {summary['median_returns_from_trigger_pct']['120']}% / {summary['median_returns_from_trigger_pct']['252']}%",
        "",
        "## 낙폭 구간별",
        "",
        "|저점 낙폭|사건|30% 반등률|추가하락 중앙값|진입 252일 수익 중앙값|진입 252일 플러스|확인 후 252일 수익|",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["by_trough_drawdown"]:
        lines.append(
            f"|{row['bin']}|{row['events']}|{row['recovery_rate_pct']}%|{row['median_additional_loss_pct']}%|"
            f"{row['median_252d_return_from_trigger_pct']}%|{row['positive_252d_rate_pct']}%|"
            f"{row['median_252d_return_after_confirmation_pct']}%|"
        )
    lines += [
        "", "## 반등 확인 시 동반 신호별 252일 성과", "",
        "|동반 신호|관찰 건수|252일 중앙수익|플러스 비율|",
        "|---|---:|---:|---:|",
    ]
    for row in summary["cause_outcomes_after_confirmation"]:
        lines.append(
            f"|{row['label']}|{row['events']}|{row['median_252d_return_after_confirmation_pct']}%|"
            f"{row['positive_252d_rate_pct']}%|"
        )
    lines += [
        "", "## 결론", "",
        "- 낙폭만으로 매수하는 전략은 기각한다. 전체 252일 중앙수익은 -4.23%, 종목당 첫 사건만 보면 -8.67%다.",
        "- 낙폭이 깊을수록 결과가 나빠졌다. 저점 낙폭 -60% 이하는 플러스 비율 28.1% 이하, -80% 이하는 9.8%였다.",
        "- 저점 대비 30% 반등도 추세 전환을 보장하지 않았다. 반등 확인 후 252일 중앙수익은 -15.27%였다.",
        "- 단순 실적개선·수급·거래량 신호만으로는 손실 집단을 충분히 제거하지 못했다.",
        "- 실전 후보는 사업구조 변화와 이익 추정치 상향이 공시·실적으로 확인되고, 증자·CB·감사의견·거래정지 위험이 없는 종목으로 제한해야 한다.",
        "", "## 공식 자료로 재확인한 회복 사례", "",
        "- 위메이드맥스: 2021년 글로벌 블록체인 게임 개발사 전환과 미르4 개발사 위메이드넥스트와의 주식교환이 사업 재평가의 핵심 촉매였다. 단순 저가 반등 사례가 아니다.",
        "- 이수페타시스: 회사 IR은 AI 인프라 확대와 데이터센터용 고속 AI 스위치 수요 증가를 명시한다. 이후 실적에서도 2021년 적자 EPS가 2024~2025년 흑자로 개선됐다.",
        "- 에코프로: 고니켈 양극재·배터리 재활용 사업의 성장과 생산 확대가 확인된다. 역시 낙폭 자체보다 산업 성장과 실적 변화가 설명력이 높다.",
        "", "공식 자료:",
        "- https://www.wemademax.com/ir/files/download?file=1&idx=71",
        "- https://www.petasys.com/upload/new_board/B1747271168399.pdf",
        "- https://www.petasys.com/kor/ir/financial.jsp",
        "- https://kind.krx.co.kr/external/2024/04/17/000676/20240417001619/11011.htm",
    ]
    lines += ["", "## 대표 회복 사례", ""]
    for _, row in recovered.head(20).iterrows():
        lines.append(
            f"- {row.get('stock_name') or row.stock_code}({row.stock_code}) "
            f"저점 {row.trough_drawdown_pct:.1f}% → {row.recovery_date} 반등확인 → 진입 252일 {row.trigger_return_252d_pct:.1f}% "
            f"/ {', '.join(row.cause_evidence)}"
        )
    lines += ["", "## 대표 실패·추가하락 사례", ""]
    for _, row in failed.head(20).iterrows():
        lines.append(
            f"- {row.get('stock_name') or row.stock_code}({row.stock_code}) "
            f"진입 {row.trigger_drawdown_pct:.1f}% 이후 추가 {row.additional_loss_after_trigger_pct:.1f}% / 1년 내 반등 미확인"
        )
    lines += [
        "", "## 해석 주의", "",
        "- 낙폭은 하방 한계가 아니라 악재의 크기를 반영할 수 있다.",
        "- 같은 종목의 여러 사건을 허용하면 결과가 좋아 보이는 반복 사건 편향이 생긴다.",
        "- 재무·수급·공시는 반등 시점에 함께 관찰된 연관 신호이며 단독 인과관계로 해석하지 않는다.",
        "- 저점 대비 30% + MA20 회복을 확인한 뒤에도 1년 최대 손실이 발생할 수 있다.",
        "- 현재 상장 종목만 분석하면 상장폐지·거래정지 실패 사례가 빠져 회복률이 과대평가된다.",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
