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
        "risk_metrics_complete": bool(rows) and all(
            row.get("mdd") is not None
            and row.get("sharpe") is not None
            and row.get("pl_ratio") is not None
            for row in rows
        ),
    }
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
