"""Shared point-in-time controls for strict historical simulations."""
from __future__ import annotations

from datetime import date, datetime, timedelta


def iso_day(value) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)[:10].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def next_weekday(value) -> str | None:
    day = iso_day(value)
    if not day:
        return None
    current = date.fromisoformat(day) + timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.isoformat()


def validate_strict_trade_contract(
    *, signal_date, execution_date, feature_available_at_max,
    decision_time: str, execution_price_type: str,
) -> dict:
    signal = iso_day(signal_date)
    execution = iso_day(execution_date)
    available = iso_day(feature_available_at_max)
    violations: list[str] = []
    if not signal or not execution:
        violations.append("invalid_signal_or_execution_date")
    if available and signal and available > signal:
        violations.append("feature_available_after_signal")
    if signal and execution and execution <= signal and decision_time == "after_close":
        violations.append("same_day_execution_after_close_signal")
    if execution_price_type not in {"next_open", "next_close", "intraday_stop"}:
        violations.append("non_strict_execution_price_type")
    return {
        "signal_date": signal, "execution_date": execution,
        "feature_available_at_max": available,
        "decision_time": decision_time, "execution_price_type": execution_price_type,
        "has_lookahead_violation": bool(violations), "violations": violations,
    }


def assert_strict_trade_contract(**kwargs) -> dict:
    result = validate_strict_trade_contract(**kwargs)
    if result["has_lookahead_violation"]:
        raise ValueError("Strict backtest contract violation: " + ", ".join(result["violations"]))
    return result

