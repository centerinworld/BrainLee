#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "research_outputs" / "historical_tenbagger_causes.json"
OUT_CSV = ROOT / "research_outputs" / "historical_tenbagger_causes.csv"
OUT_MD = ROOT / "research_outputs" / "historical_tenbagger_causes.md"
OVERRIDES_PATH = ROOT / "research_inputs" / "historical_tenbagger_cause_overrides.json"

sys.path.insert(0, str(ROOT))
from database import engine  # noqa: E402
import scripts.research_historical_tenbagger_scoreboard_v2 as scoreboard  # noqa: E402


CAUSE_LABELS = {
    "earnings_led": "실적 성장 주도",
    "contract_led": "대형 수주 주도",
    "structural_change": "사업구조 변화 주도",
    "shareholder_return": "자사주·소각 주도",
    "financing_or_issue": "자금조달·이슈 주도",
    "mixed_business": "실적·사업 촉매 복합",
    "theme_event": "검증되지 않은 테마·이슈",
    "unresolved": "원인 미확정",
}


def _load_cause_overrides() -> dict[str, dict]:
    if not OVERRIDES_PATH.exists():
        return {}
    payload = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    return {str(code).zfill(6): value for code, value in payload.items()}


def _read_for_codes(sql: str, codes: list[str]) -> pd.DataFrame:
    if not codes:
        return pd.DataFrame()
    statement = text(sql).bindparams(bindparam("codes", expanding=True))
    return pd.read_sql_query(statement, engine, params={"codes": codes})


def _build_validated_episodes() -> pd.DataFrame:
    quarters = scoreboard._load_point_in_time_earnings()
    annuals = scoreboard._load_annual_business_breakouts()
    snapshots = scoreboard._load_snapshots()
    snapshots = scoreboard._attach_earnings(snapshots, quarters)
    snapshots = scoreboard._attach_dilution(snapshots)
    snapshots = scoreboard._attach_outcome_quality(snapshots, quarters, annuals)
    winners = snapshots[snapshots["validated_tenbagger_24m"] == 1].copy()
    winners = winners.sort_values(["stock_code", "snapshot_date"])
    return winners.drop_duplicates("stock_code", keep="first")[
        ["stock_code", "stock_name", "market", "sector_large", "snapshot_date", "close_price"]
    ].reset_index(drop=True)


def _attach_rally_dates(episodes: pd.DataFrame) -> pd.DataFrame:
    prices = _read_for_codes(
        """
        SELECT stock_code, date, close, volume, inst_net_buy_amt, frn_net_buy_amt
        FROM price_history
        WHERE stock_code IN :codes AND close > 0
        ORDER BY stock_code, date
        """,
        episodes["stock_code"].astype(str).tolist(),
    )
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    by_code = {str(code): group for code, group in prices.groupby("stock_code")}
    records = []
    for row in episodes.to_dict("records"):
        code = str(row["stock_code"])
        base_date = pd.Timestamp(row["snapshot_date"])
        end_date = base_date + pd.Timedelta(days=730)
        base_price = float(row["close_price"] or 0)
        future = by_code.get(code, pd.DataFrame())
        future = future[(future["date"] > base_date) & (future["date"] <= end_date)].copy()
        early_hits = future[future["close"] >= base_price * 1.5]
        row["first_150pct_date"] = (
            early_hits.iloc[0]["date"].date().isoformat() if not early_hits.empty else None
        )
        for multiple in (3, 10):
            hits = future[future["close"] >= base_price * multiple]
            row[f"first_{multiple}x_date"] = (
                hits.iloc[0]["date"].date().isoformat() if not hits.empty else None
            )
        if not future.empty:
            peak_idx = future["close"].idxmax()
            row["peak_date"] = future.loc[peak_idx, "date"].date().isoformat()
            row["peak_multiple"] = round(float(future.loc[peak_idx, "close"]) / base_price, 2)
            pre3_end = pd.Timestamp(row["first_3x_date"] or row["peak_date"])
            pre3 = future[future["date"] <= pre3_end].tail(20)
            row["foreign_flow_20d"] = round(float(pre3["frn_net_buy_amt"].fillna(0).sum()), 0)
            row["institution_flow_20d"] = round(float(pre3["inst_net_buy_amt"].fillna(0).sum()), 0)
        records.append(row)
    return pd.DataFrame(records)


def _load_financial_evidence(codes: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    annual = _read_for_codes(
        """
        WITH ranked AS (
            SELECT stock_code, year, revenue, operating_profit, report_type, data_source, id,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, year
                       ORDER BY CASE WHEN report_type='CFS' THEN 0 ELSE 1 END,
                                CASE WHEN LOWER(data_source) >= 'fnguide' AND LOWER(data_source) < 'fnguidf' THEN 0
                                     WHEN LOWER(data_source) >= 'dart' AND LOWER(data_source) < 'daru' THEN 1 ELSE 2 END,
                                id DESC
                   ) rn
            FROM financial_data
            WHERE is_annual IS TRUE AND stock_code IN :codes
        )
        SELECT stock_code, year, revenue, operating_profit, data_source
        FROM ranked WHERE rn=1
        """,
        codes,
    )
    quarter = _read_for_codes(
        """
        WITH ranked AS (
            SELECT stock_code, year, quarter, revenue, operating_profit, report_type, data_source, id,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, year, quarter
                       ORDER BY CASE WHEN report_type='CFS' THEN 0 ELSE 1 END,
                                CASE WHEN LOWER(data_source) >= 'fnguide' AND LOWER(data_source) < 'fnguidf' THEN 0
                                     WHEN LOWER(data_source) >= 'dart' AND LOWER(data_source) < 'daru' THEN 1 ELSE 2 END,
                                id DESC
                   ) rn
            FROM financial_data
            WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND stock_code IN :codes
        )
        SELECT stock_code, year, quarter, revenue, operating_profit, data_source
        FROM ranked WHERE rn=1
        """,
        codes,
    )

    def enrich(frame: pd.DataFrame, keys: list[str], annual_mode: bool) -> pd.DataFrame:
        if frame.empty:
            return frame
        prior = frame[keys + ["revenue", "operating_profit"]].copy()
        prior["year"] += 1
        prior = prior.rename(columns={"revenue": "revenue_prev", "operating_profit": "op_prev"})
        frame = frame.merge(prior, on=keys, how="left")
        frame["rev_yoy"] = np.where(
            frame["revenue_prev"] > 0, frame["revenue"] / frame["revenue_prev"] - 1, np.nan
        )
        frame["op_growth"] = np.where(
            frame["op_prev"] > 0, frame["operating_profit"] / frame["op_prev"] - 1, np.nan
        )
        frame["turnaround"] = (
            (frame["op_prev"].fillna(0) <= 0) & (frame["operating_profit"].fillna(0) > 0)
        )
        if annual_mode:
            frame["available_date"] = pd.to_datetime(
                {"year": frame["year"] + 1, "month": 3, "day": 31}, errors="coerce"
            )
        else:
            month = frame["quarter"].map({1: 5, 2: 8, 3: 11, 4: 3})
            release_year = frame["year"] + (frame["quarter"] == 4).astype(int)
            frame["available_date"] = pd.to_datetime(
                {"year": release_year, "month": month, "day": 15}, errors="coerce"
            )
        return frame

    return enrich(annual, ["stock_code", "year"], True), enrich(
        quarter, ["stock_code", "year", "quarter"], False
    )


def _load_event_evidence(codes: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    contracts = _read_for_codes(
        """
        SELECT stock_code, disclosed_at, report_nm, contract_ratio_pct, contract_amount_krw,
               counterparty, rcept_no
        FROM dart_contracts WHERE stock_code IN :codes
        """,
        codes,
    )
    disclosures = _read_for_codes(
        """
        SELECT stock_code, rcept_dt, report_nm, rcept_no, dart_url
        FROM dart_disclosures WHERE stock_code IN :codes
        """,
        codes,
    )
    dilution = _read_for_codes(
        """
        SELECT stock_code, disclosed_at, event_type, dilution_pct, issue_amount, rcept_no, report_nm
        FROM dilution_events WHERE stock_code IN :codes
        """,
        codes,
    )
    if not contracts.empty:
        contracts["event_date"] = pd.to_datetime(contracts["disclosed_at"], format="mixed", errors="coerce")
    if not disclosures.empty:
        disclosures["event_date"] = pd.to_datetime(disclosures["rcept_dt"], format="mixed", errors="coerce")
    if not dilution.empty:
        dilution["event_date"] = pd.to_datetime(dilution["disclosed_at"], format="mixed", errors="coerce")
    return contracts, disclosures, dilution


def _dart_url(rcept_no: object) -> str | None:
    value = str(rcept_no or "").strip()
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={value}" if value else None


def _classify_episode(
    episode: dict,
    annual: pd.DataFrame,
    quarter: pd.DataFrame,
    contracts: pd.DataFrame,
    disclosures: pd.DataFrame,
    dilution: pd.DataFrame,
) -> dict:
    code = str(episode["stock_code"])
    rally_start = pd.Timestamp(
        episode.get("first_150pct_date") or episode["first_3x_date"] or episode["peak_date"]
    )
    start = rally_start - pd.Timedelta(days=365)
    expansion_end = pd.Timestamp(episode.get("first_3x_date") or rally_start)
    end = expansion_end + pd.Timedelta(days=30)
    evidence: list[dict] = []
    scores = Counter()

    ann = annual[(annual["stock_code"].astype(str) == code)]
    ann = ann[(ann["available_date"] >= start) & (ann["available_date"] <= end)]
    ann = ann[
        (ann["rev_yoy"].fillna(-99) >= 0.15)
        & (ann["rev_yoy"].fillna(99) <= 5.0)
        & ((ann["revenue"].fillna(0) - ann["revenue_prev"].fillna(0)) >= 5_000_000_000)
        & (ann["operating_profit"].fillna(0) > 0)
        & ((ann["op_growth"].fillna(-99) >= 0.20) | ann["turnaround"])
    ]
    for _, row in ann.iterrows():
        scores["earnings"] += 4
        op_detail = "흑자전환" if row["turnaround"] else f"{row['op_growth'] * 100:+.1f}%"
        evidence.append({
            "type": "annual_earnings",
            "date": row["available_date"].date().isoformat(),
            "detail": f"연매출 {row['rev_yoy'] * 100:+.1f}%, 영업이익 {op_detail}",
            "source": str(row.get("data_source") or "financial_data"),
        })

    qtr = quarter[(quarter["stock_code"].astype(str) == code)]
    qtr = qtr[(qtr["available_date"] >= start) & (qtr["available_date"] <= end)]
    qtr = qtr[
        (qtr["rev_yoy"].fillna(-99) >= 0.20)
        & (qtr["rev_yoy"].fillna(99) <= 3.0)
        & ((qtr["revenue"].fillna(0) - qtr["revenue_prev"].fillna(0)) >= 1_000_000_000)
        & (qtr["operating_profit"].fillna(0) > 0)
        & ((qtr["op_growth"].fillna(-99) >= 0.30) | qtr["turnaround"])
    ]
    if len(qtr) >= 2:
        scores["earnings"] += 4
    elif len(qtr) == 1:
        scores["earnings"] += 2
    for _, row in qtr.iterrows():
        op_detail = "흑자전환" if row["turnaround"] else f"{row['op_growth'] * 100:+.1f}%"
        evidence.append({
            "type": "quarterly_earnings",
            "date": row["available_date"].date().isoformat(),
            "detail": f"{int(row['year'])}Q{int(row['quarter'])} 매출 {row['rev_yoy'] * 100:+.1f}%, "
                      f"영업이익 {op_detail}",
            "source": str(row.get("data_source") or "financial_data"),
        })

    ctr = contracts[(contracts["stock_code"].astype(str) == code)]
    ctr = ctr[(ctr["event_date"] >= start) & (ctr["event_date"] <= end)]
    ctr = ctr.drop_duplicates(subset=["rcept_no", "report_nm", "contract_ratio_pct"])
    ctr = ctr.sort_values("contract_ratio_pct", ascending=False, na_position="last")
    for _, row in ctr.head(3).iterrows():
        ratio = float(row["contract_ratio_pct"] or 0)
        if ratio >= 20:
            scores["contract"] += 4
        elif ratio >= 10:
            scores["contract"] += 2
        else:
            continue
        evidence.append({
            "type": "major_contract",
            "date": row["event_date"].date().isoformat(),
            "detail": f"매출 대비 {ratio:.1f}% 수주·공급계약, 상대방 {row.get('counterparty') or '미기재'}",
            "source": _dart_url(row.get("rcept_no")),
        })

    disc = disclosures[(disclosures["stock_code"].astype(str) == code)]
    disc = disc[(disc["event_date"] >= start) & (disc["event_date"] <= end)]
    disc = disc.copy()
    disc["normalized_report"] = (
        disc["report_nm"].fillna("")
        .map(lambda value: re.sub(r"\[(?:기재정정|첨부정정|정정)\]|\s+", "", str(value)))
    )
    disc = disc.sort_values("event_date").drop_duplicates("normalized_report", keep="last")
    patterns = [
        ("structure", r"합병|분할|주식교환|영업양수|영업양도|타법인주식및출자증권취득|신규시설투자|사업목적|신규사업"),
        ("shareholder", r"자기주식.*취득|자기주식.*소각|주식소각"),
    ]
    for key, pattern in patterns:
        matched = disc[disc["report_nm"].fillna("").str.contains(pattern, regex=True)]
        for _, row in matched.head(3).iterrows():
            scores[key] = max(scores[key], 3)
            evidence.append({
                "type": "structural_disclosure" if key == "structure" else "shareholder_return",
                "date": row["event_date"].date().isoformat(),
                "detail": str(row["report_nm"]),
                "source": row.get("dart_url") or _dart_url(row.get("rcept_no")),
            })

    dil = dilution[(dilution["stock_code"].astype(str) == code)]
    dil = dil[(dil["event_date"] >= start) & (dil["event_date"] <= end)]
    for _, row in dil.head(3).iterrows():
        scores["financing"] += 2
        dilution_pct = row.get("dilution_pct")
        dilution_text = f"{float(dilution_pct):.1f}%" if pd.notna(dilution_pct) else "미확인"
        evidence.append({
            "type": "financing_event",
            "date": row["event_date"].date().isoformat(),
            "detail": f"{row.get('event_type') or '자금조달'} 희석률 {dilution_text}",
            "source": _dart_url(row.get("rcept_no")),
        })

    business_keys = [key for key in ("earnings", "contract", "structure") if scores[key] >= 4]
    if len(business_keys) >= 2:
        cause = "mixed_business"
    elif scores["earnings"] >= 4:
        cause = "earnings_led"
    elif scores["contract"] >= 4:
        cause = "contract_led"
    elif scores["structure"] >= 3:
        cause = "structural_change"
    elif scores["shareholder"] >= 3:
        cause = "shareholder_return"
    elif scores["financing"] >= 2:
        cause = "financing_or_issue"
    else:
        cause = "unresolved"

    if cause == "unresolved":
        confidence = "unresolved"
    elif max(scores.values() or [0]) >= 8 or len(business_keys) >= 2:
        confidence = "high"
    else:
        confidence = "medium"
    amplifiers = []
    if float(episode.get("foreign_flow_20d") or 0) > 0:
        amplifiers.append("외국인 순매수")
    if float(episode.get("institution_flow_20d") or 0) > 0:
        amplifiers.append("기관 순매수")
    evidence.sort(key=lambda item: (item["date"], item["type"]))
    for item in evidence:
        item["phase"] = (
            "ignition"
            if pd.Timestamp(item["date"]) <= rally_start + pd.Timedelta(days=30)
            else "scaling"
        )
    repeatable_business = cause in {
        "earnings_led", "contract_led", "structural_change", "mixed_business"
    }
    if repeatable_business:
        sample_decision = "business_training_eligible"
    elif cause in {"financing_or_issue", "shareholder_return", "theme_event"}:
        sample_decision = "non_operating_excluded"
    else:
        sample_decision = "manual_review_required"
    return {
        **episode,
        "primary_cause": cause,
        "primary_cause_label": CAUSE_LABELS[cause],
        "confidence": confidence,
        "cause_scores": dict(scores),
        "sample_decision": sample_decision,
        "repeatable_business_cause": repeatable_business,
        "amplifiers": amplifiers,
        "evidence": evidence,
    }


def _apply_override(item: dict, overrides: dict[str, dict]) -> dict:
    override = overrides.get(str(item["stock_code"]).zfill(6))
    if not override:
        return item
    cause = str(override["primary_cause"])
    item["primary_cause"] = cause
    item["primary_cause_label"] = CAUSE_LABELS[cause]
    item["confidence"] = str(override.get("confidence") or "medium")
    item["sample_decision"] = str(override["sample_decision"])
    item["repeatable_business_cause"] = item["sample_decision"] == "business_training_eligible"
    item["cause_override"] = "source_verified_manual_audit"
    item["evidence"].append(dict(override["evidence"]))
    item["evidence"].sort(key=lambda evidence: (evidence["date"], evidence["type"]))
    return item


def _write_outputs(results: list[dict]) -> dict:
    counts = Counter(item["primary_cause"] for item in results)
    confidence = Counter(item["confidence"] for item in results)
    source_confirmed = len(results) - counts["unresolved"]
    business_confirmed = sum(bool(item["repeatable_business_cause"]) for item in results)
    non_operating = sum(item["sample_decision"] == "non_operating_excluded" for item in results)
    summary = {
        "stocks": len(results),
        "source_confirmed_stocks": source_confirmed,
        "source_confirmed_pct": round(source_confirmed / len(results) * 100, 1) if results else 0,
        "business_training_eligible_stocks": business_confirmed,
        "business_training_eligible_pct": round(business_confirmed / len(results) * 100, 1) if results else 0,
        "non_operating_excluded_stocks": non_operating,
        "manual_review_required_stocks": counts["unresolved"],
        "cause_counts": dict(counts),
        "confidence_counts": dict(confidence),
        "method": "점화: 최초 50% 상승일 이전, 확장: 최초 3배 도달 후 30일까지 공개된 시점정합 근거",
        "causality_note": "관찰자료 기반 원인 귀속이며 공식 공시·실적 근거가 없는 종목은 미확정 처리",
    }
    payload = {"summary": summary, "cause_labels": CAUSE_LABELS, "results": results}
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(OUT_JSON)

    flat = []
    for item in results:
        flat.append({
            "stock_code": item["stock_code"],
            "stock_name": item["stock_name"],
            "base_date": str(item["snapshot_date"])[:10],
            "first_3x_date": item.get("first_3x_date"),
            "first_10x_date": item.get("first_10x_date"),
            "peak_multiple": item.get("peak_multiple"),
            "primary_cause": item["primary_cause"],
            "primary_cause_label": item["primary_cause_label"],
            "confidence": item["confidence"],
            "evidence_count": len(item["evidence"]),
            "evidence_summary": " | ".join(e["detail"] for e in item["evidence"]),
        })
    pd.DataFrame(flat).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    lines = [
        "# 과거 지속형 텐버거 상승 원인 판정",
        "",
        f"- 분석 종목: {summary['stocks']}개",
        f"- 공개 근거 기반 원인 분류: {summary['source_confirmed_stocks']}개 ({summary['source_confirmed_pct']}%)",
        f"- 재현 가능한 사업 원인 학습 표본: {summary['business_training_eligible_stocks']}개 ({summary['business_training_eligible_pct']}%)",
        f"- 비영업 원인 제외: {summary['non_operating_excluded_stocks']}개",
        f"- 원인 미확정·수동 검토: {summary['manual_review_required_stocks']}개",
        "- 수급·거래량은 원인이 아니라 증폭 요인으로만 기록",
        "",
        "## 원인 분포",
        "",
        "|원인|종목 수|",
        "|---|---:|",
    ]
    for key, count in counts.most_common():
        lines.append(f"|{CAUSE_LABELS[key]}|{count}|")
    lines += ["", "## 종목별 결론", ""]
    for item in results:
        evidence = "; ".join(e["detail"] for e in item["evidence"][:4]) or "확정 근거 없음"
        lines.append(
            f"- {item['stock_name']}({item['stock_code']}): {item['primary_cause_label']} "
            f"[{item['confidence']}·{item['sample_decision']}] / {evidence}"
        )
    lines += [
        "",
        "## 판정 원칙",
        "",
        "- 미래 실적을 상승 전 신호로 소급하지 않고 실제 공개 가능일 이후만 인정했다.",
        "- 연매출 15% 이상과 영업이익 개선, 분기 2회 이상의 강한 가속을 실적 주도의 핵심 근거로 사용했다.",
        "- 매출 대비 20% 이상 수주계약만 수주 주도의 강한 근거로 인정했다.",
        "- 최초 50% 상승까지를 점화, 이후 최초 3배 도달까지를 확장 구간으로 구분했다.",
        "- 실적·수주·사업구조 원인만 학습 가능 표본으로 인정하고 자금조달·주주환원은 제외했다.",
        "- 공시·재무 근거가 없는 경우 테마를 임의 추정하지 않고 원인 미확정으로 남겼다.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> None:
    episodes = _attach_rally_dates(_build_validated_episodes())
    codes = episodes["stock_code"].astype(str).tolist()
    annual, quarter = _load_financial_evidence(codes)
    contracts, disclosures, dilution = _load_event_evidence(codes)
    overrides = _load_cause_overrides()
    results = [
        _apply_override(
            _classify_episode(row, annual, quarter, contracts, disclosures, dilution), overrides
        )
        for row in episodes.to_dict("records")
    ]
    summary = _write_outputs(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
