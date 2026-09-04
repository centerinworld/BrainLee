#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "research_outputs" / "tenbagger_claude_change_audit_20260810.json"
OUT_MD = ROOT / "research_outputs" / "tenbagger_claude_change_audit_20260810.md"
sys.path.insert(0, str(ROOT))

from database import engine  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from scripts import discover_historical_tenbagger_signals as discovery  # noqa: E402
from scripts import research_historical_tenbagger_scoreboard_v2 as scoreboard  # noqa: E402


GRADE_RULES = {
    "S": lambda f: (f["dist_high_252"] <= -0.80) & f["market_cap_억"].between(0, 1000, inclusive="neither"),
    "A": lambda f: (f["dist_high_252"] <= -0.75) & f["market_cap_억"].between(0, 1500, inclusive="neither"),
    "B": lambda f: (f["dist_high_252"] <= -0.70) & f["market_cap_억"].between(0, 3000, inclusive="neither"),
    "C": lambda f: f["dist_high_252"] <= -0.60,
}

PERIOD_LABELS = ("BULL", "BEAR", "RECOVERY", "AI", "RECENT", "LATEST")


def backtest_suite(prefix: str) -> dict:
    conn = connect_primary_db(timeout=60)
    try:
        rows = []
        for label in PERIOD_LABELS:
            name = f"{prefix}_{label}_20260810"
            row = conn.execute(
                "SELECT run_id,total_return_pct,max_drawdown_pct,total_trades,win_rate "
                "FROM backtest_runs WHERE name=? AND status='done' ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                rows.append({
                    "period": label, "run_id": row[0], "return_pct": row[1],
                    "mdd_pct": row[2], "trades": row[3], "win_rate_pct": row[4],
                })
        return {
            "periods": rows,
            "avg_return_pct": round(sum(r["return_pct"] for r in rows) / len(rows), 2) if rows else None,
            "worst_mdd_pct": min((r["mdd_pct"] for r in rows), default=None),
            "positive_periods": sum(r["return_pct"] > 0 for r in rows),
            "latest3_avg_return_pct": round(sum(r["return_pct"] for r in rows[-3:]) / 3, 2) if len(rows) == 6 else None,
        }
    finally:
        conn.close()


def grade_metrics(frame: pd.DataFrame, target: str) -> dict:
    result = {}
    splits = {
        "train": frame[frame["snapshot_date"] <= pd.Timestamp("2022-12-31")],
        "validation": frame[frame["snapshot_date"].between(pd.Timestamp("2023-01-01"), pd.Timestamp("2024-07-31"))],
    }
    for split, sample in splits.items():
        base_rate = float(sample[target].mean())
        winner_codes = set(sample.loc[sample[target].eq(1), "stock_code"].astype(str))
        rows = {}
        for grade, predicate in GRADE_RULES.items():
            selected = sample[predicate(sample)]
            precision = float(selected[target].mean()) if len(selected) else 0.0
            selected_winners = set(selected.loc[selected[target].eq(1), "stock_code"].astype(str))
            rows[grade] = {
                "rows": len(selected),
                "precision_pct": round(precision * 100, 3),
                "lift": round(precision / base_rate, 3) if base_rate else None,
                "winner_stocks": len(selected_winners),
                "winner_stock_recall_pct": round(len(selected_winners) / len(winner_codes) * 100, 2) if winner_codes else 0.0,
            }
        result[split] = {
            "rows": len(sample),
            "base_rate_pct": round(base_rate * 100, 3),
            "winner_stocks": len(winner_codes),
            "grades": rows,
        }
    return result


def scalar(query: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(query)).scalar() or 0)


def load_clean_label_frame() -> pd.DataFrame:
    quarters = scoreboard._load_point_in_time_earnings()
    annuals = scoreboard._load_annual_business_breakouts()
    snapshots = scoreboard._load_snapshots()
    snapshots = scoreboard._attach_earnings(snapshots, quarters)
    snapshots = scoreboard._attach_dilution(snapshots)
    snapshots = scoreboard._attach_outcome_quality(snapshots, quarters, annuals)
    snapshots = snapshots[snapshots["label_eligible"]].copy()
    target_codes, excluded_codes = discovery._load_target_codes()
    codes = snapshots["stock_code"].astype(str)
    snapshots["durable_tenbagger_24m"] = (
        snapshots["validated_tenbagger_24m"].eq(1) & codes.isin(target_codes)
    ).astype(int)
    excluded = snapshots["validated_tenbagger_24m"].eq(1) & ~codes.isin(target_codes)
    excluded |= snapshots["validated_tenbagger_24m"].eq(1) & codes.isin(excluded_codes)
    return snapshots[~excluded].copy()


def main() -> None:
    raw = scoreboard._load_snapshots()
    raw = raw[raw["label_10x_24m"].notna()].copy()
    raw["raw_target"] = raw["label_10x_24m"].astype(int)
    clean = load_clean_label_frame()

    date_floor = (
        "TO_CHAR(CURRENT_DATE - INTERVAL '1095 days', 'YYYY-MM-DD')"
        if engine.dialect.name == "postgresql"
        else "date('now', '-1095 days')"
    )
    dilution_where = f"""
        disclosed_at >= {date_floor}
        AND event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS')
    """
    gc_2000 = backtest_suite("CODEX_PIT_GC_2000")
    gc_4000 = backtest_suite("CODEX_PIT_GC_4000")
    gc_4000_ma120 = backtest_suite("CODEX_PIT_GC_4000_MA120")
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "overall_assessment": "needs_revision",
        "production_decision": "raw_extreme_drawdown_grades_disabled",
        "raw_label_grades": grade_metrics(raw, "raw_target"),
        "clean_durable_grades": grade_metrics(clean, "durable_tenbagger_24m"),
        "data_quality": {
            "dilution_events_3y_all": scalar(f"SELECT COUNT(*) FROM dilution_events WHERE {dilution_where}"),
            "dilution_events_3y_original_only": scalar(
                f"SELECT COUNT(*) FROM dilution_events WHERE {dilution_where} AND COALESCE(report_nm,'') NOT LIKE '%정정%'"
            ),
            "backlog_rows": scalar("SELECT COUNT(*) FROM dart_backlog_quarterly WHERE backlog_amount_krw > 0"),
            "trusted_backlog_rows": scalar(
                "SELECT COUNT(*) FROM dart_backlog_quarterly WHERE backlog_amount_krw > 0 AND backlog_confidence >= 0.8"
            ),
        },
        "return_validation": {
            "method": "security_share_history point-in-time shares, next-open execution, six fixed periods",
            "golden_cross_mktcap_2000": gc_2000,
            "golden_cross_mktcap_4000": gc_4000,
            "golden_cross_mktcap_4000_ma120": gc_4000_ma120,
            "decision": {
                "mktcap_4000": "adopted",
                "ma120_entry_gate": "rejected_as_return_default_keep_optional_for_risk",
            },
        },
        "fixes": [
            "S/A/B/C raw-label grade priority removed from live ranking",
            "only clean business intersection is retained as non-trading R research tag",
            "dilution guards now exclude correction disclosures and include EB/rights events",
            "backlog features now require dart_backlog_quarterly confidence >= 0.8",
            "tb_hybrid_quality boolean return corrected",
        ],
        "auto_trading_allowed": False,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_v = payload["raw_label_grades"]["validation"]["grades"]
    clean_v = payload["clean_durable_grades"]["validation"]["grades"]
    lines = [
        "# Claude 텐버거 변경 재감사 (2026-08-10)",
        "",
        "- 종합 판정: `needs_revision`",
        "- 운영 조치: 원시 라벨 기반 S/A/B/C 우선정렬 비활성화",
        "- 자동매매 허용: `false`",
        "",
        "## 등급 재계산",
        "",
        "| 등급 | 원시 검증 lift | 지속형 검증 lift | 지속형 승자 종목 |",
        "|---|---:|---:|---:|",
    ]
    for grade in GRADE_RULES:
        lines.append(
            f"| {grade} | {raw_v[grade]['lift']}x | {clean_v[grade]['lift']}x | {clean_v[grade]['winner_stocks']} |"
        )
    dq = payload["data_quality"]
    lines += [
        "",
        "## 데이터 품질",
        "",
        f"- 최근 3년 희석 이벤트: 전체 {dq['dilution_events_3y_all']:,}건 / 정정 제외 {dq['dilution_events_3y_original_only']:,}건",
        f"- 수주잔고: 전체 {dq['backlog_rows']:,}건 / 신뢰도 0.8 이상 {dq['trusted_backlog_rows']:,}건",
        "",
        "## 시점정합 수익률 재검증",
        "",
        f"- 골든크로스 시총 2,000억: 평균 {gc_2000['avg_return_pct']}% / 최악 MDD {gc_2000['worst_mdd_pct']}% / 최신3구간 {gc_2000['latest3_avg_return_pct']}%",
        f"- 골든크로스 시총 4,000억: 평균 {gc_4000['avg_return_pct']}% / 최악 MDD {gc_4000['worst_mdd_pct']}% / 최신3구간 {gc_4000['latest3_avg_return_pct']}% — 채택",
        f"- 4,000억+MA120 진입게이트: 평균 {gc_4000_ma120['avg_return_pct']}% / 최악 MDD {gc_4000_ma120['worst_mdd_pct']}% / 최신3구간 {gc_4000_ma120['latest3_avg_return_pct']}% — 수익률 기본값 기각",
        "",
        "## 결론",
        "",
        "원시 10배 라벨에서 보인 초낙폭 효과는 가격 아티팩트와 비영업 급등을 제거하면 소멸한다. "
        "따라서 해당 등급은 수익률 개선 근거로 사용할 수 없으며, 정제 라벨과 엄격 실행 백테스트를 모두 통과한 신호만 승격한다.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "overall_assessment": payload["overall_assessment"],
        "production_decision": payload["production_decision"],
        "return_validation": payload["return_validation"],
        "output_json": str(OUT_JSON),
        "output_md": str(OUT_MD),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
