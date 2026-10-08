"""Machine-derived strategy promotion tiers for the strategy center."""
from __future__ import annotations

from statistics import mean


STATUS_RANK = {
    "legacy": 0,
    "execution_strict": 1,
    "point_in_time_approx": 2,
    "point_in_time_verified": 3,
    "forward_validated": 4,
}


def classify_strategy(periods: dict) -> dict:
    rows = [row for row in periods.values() if row.get("total_return_pct") is not None]
    returns = [float(row["total_return_pct"]) for row in rows]
    statuses = [str(row.get("verification_status") or "legacy") for row in rows]
    rank = min((STATUS_RANK.get(status, 0) for status in statuses), default=0)
    verification_status = next(
        (status for status, value in STATUS_RANK.items() if value == rank), "legacy"
    )
    execution_ready = verification_status in {
        "execution_strict", "point_in_time_verified", "forward_validated"
    }
    metrics = {
        "period_count": len(returns),
        "average_return_pct": round(mean(returns), 2) if returns else None,
        "positive_periods": sum(value > 0 for value in returns),
        "non_loss_periods": sum(value >= 0 for value in returns),
        "worst_period_return_pct": round(min(returns), 2) if returns else None,
        # 2026-10-07 결정 D3(REVIEW_PLAN §13): 평균 유지 + 최악 구간·최대 낙폭·평균÷낙폭 열 추가(정렬 기준 변경은 W4·W5 뒤).
        # 최대 낙폭 = 구간별 MDD 중 가장 나쁜 값. 한 구간이라도 MDD가 없으면 None('위험 지표 없음').
        "max_drawdown_pct": (round(min(float(r["mdd"]) for r in rows), 2)
                             if rows and all(r.get("mdd") is not None for r in rows) else None),
        "risk_metrics_complete": bool(rows) and all(
            row.get("mdd") is not None
            and row.get("sharpe") is not None
            and row.get("pl_ratio") is not None
            for row in rows
        ),
    }
    mdd = metrics["max_drawdown_pct"]
    metrics["return_to_mdd"] = (round(metrics["average_return_pct"] / abs(mdd), 3)
                                if mdd not in (None, 0) and metrics["average_return_pct"] is not None else None)
    enough = len(returns) == 6
    avg_return = metrics["average_return_pct"] if returns else float("-inf")
    worst_return = metrics["worst_period_return_pct"] if returns else float("-inf")
    positive = metrics["positive_periods"]
    non_loss = metrics["non_loss_periods"]

    live_ready = bool(
        enough
        and rank >= STATUS_RANK["forward_validated"]
        and metrics["risk_metrics_complete"]
        and avg_return >= 15
        and non_loss >= 5
        and worst_return >= -20
    )
    if live_ready:
        tier, reason = "live_eligible", "전방검증·위험지표·6구간 안정성 기준을 모두 통과"
    elif enough and execution_ready and avg_return >= 20 and non_loss >= 5 and worst_return >= -15:
        tier = "paper_core"
        reason = "성과 안정성은 통과했지만 PIT/전방 검증 전이라 종이운용만 허용"
    elif enough and rank >= 1 and avg_return >= 30 and positive >= 4 and worst_return >= -35:
        tier = "offensive_satellite"
        reason = "상승 수익은 높지만 손실 구간 편중이 있어 핵심 비중 사용 금지"
    elif enough and rank >= 1 and avg_return >= 15 and positive >= 4 and worst_return >= -35:
        tier = "validation_queue"
        reason = "성과 후보이나 안정성 또는 방법론 검증이 부족"
    else:
        tier, reason = "retired", "6구간 성과 또는 실행 검증 기준 미달"

    return {
        "tier": tier,
        "reason": reason,
        "verification_status": verification_status,
        "metrics": metrics,
        "auto_trading_allowed": False,
        "live_ready": live_ready,
    }


# 2026-10-08 W6(REVIEW_PLAN §34-3, 사용자 위임 검토자 결정): 기계 산정 등급 위에 얹는 명시적 결정.
# 입력은 W5 분포(무작위 12회 중앙값, D14). 기계 산정과 다르면 machine_tier를 함께 남겨 차이를 숨기지 않는다.
GOVERNANCE_DECISIONS = {
    "v12": {"tier": "paper_core", "reason": "W6 결정: 유지 — 무작위 순서 하위25% 평균 +10.8, 6구간 중 5구간 중앙값 플러스",
            "source": "REVIEW_PLAN §34-3"},
    "v8": {"tier": "paper_core", "flag": "forward_validation_pending_60d",
           "reason": "W6 결정: 60거래일 전진 검증 조건부 승격 — 하위25% 평균 +12.7(21개 중 최고), 21.12~22.10 0%는 시장 필터로 거래 없음",
           "source": "REVIEW_PLAN §34-3"},
    "minervini": {"tier": "retired", "reason": "W6 결정: 퇴역 — 이전 +19.2는 종목코드 순 선착순의 운, W5 중앙값 평균 +5.5",
                  "source": "REVIEW_PLAN §34-3"},
    "earnings_conviction": {"tier": "validation_queue", "reason": "W6 결정: 대기 유지 — 최악 구간 −29.5, 선택 운 분포 미측정",
                            "source": "REVIEW_PLAN §34-3"},
    "contract_momentum": {"tier": "validation_queue", "reason": "W6 결정: 대기 유지 — 최악 구간 −19.4, 선택 운 분포 미측정",
                          "source": "REVIEW_PLAN §34-3"},
    "golden_cross": {"tier": "offensive_satellite", "reason": "W6 결정: 현 등급 유지(순위 고정 전략, 플러스 구간 3/6)",
                     "source": "REVIEW_PLAN §34-3"},
    "sector_focus": {"tier": "offensive_satellite", "note": "수동 후보군 생존 편향 미해결 — 핵심 승격 금지",
                     "reason": "W6 결정: 현 등급 유지", "source": "REVIEW_PLAN §34-3"},
    "megatrend": {"note": "섹터 필터 후보군 생존 편향 미해결(폐지 종목 섹터 미상)", "source": "REVIEW_PLAN §33-1"},
}


def apply_governance_decision(strategy_key: str, governance: dict) -> dict:
    """기계 산정 결과에 W6 결정을 덮어쓴다. tier를 바꾸면 machine_tier·machine_reason을 남긴다."""
    d = GOVERNANCE_DECISIONS.get(strategy_key)
    if not d:
        return governance
    out = dict(governance)
    if d.get("tier") and d["tier"] != governance.get("tier"):
        out["machine_tier"], out["machine_reason"] = governance.get("tier"), governance.get("reason")
    if d.get("tier"):
        out["tier"], out["reason"] = d["tier"], d.get("reason", out.get("reason"))
    for k in ("flag", "note", "source"):
        if d.get(k):
            out[f"decision_{k}"] = d[k]
    return out


def summarize_governance(strategies: list[dict]) -> dict:
    counts = {tier: 0 for tier in (
        "live_eligible", "paper_core", "offensive_satellite", "validation_queue", "retired"
    )}
    for strategy in strategies:
        tier = strategy.get("governance", {}).get("tier", "retired")
        counts[tier] = counts.get(tier, 0) + 1
    return {
        "counts": counts,
        "auto_trading_allowed": False,
        "policy": "실전 자동매매 비활성화. PIT 검증과 전방 검증 전에는 종이운용 연구만 허용.",
    }
