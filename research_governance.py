"""Standard contract for hypothesis research shown in the strategy UI."""
from __future__ import annotations

REQUIRED_FIELDS = {
    "identity": ("research_id", "title", "hypothesis", "verdict"),
    "sample": ("sample_count", "stock_count", "period_start", "period_end"),
    "performance": ("total_return_pct", "cagr_pct", "mdd_pct", "positive_rate_pct", "profit_factor"),
    "execution": ("initial_capital", "price_basis", "execution_price_type", "transaction_cost_bps", "slippage_bps"),
    "validity": ("is_out_of_sample", "lookahead_violations", "availability_fallback_rows", "survivorship_bias_controlled"),
}
VERDICTS = {"supported", "rejected", "inconclusive"}


def validate_research_record(record: dict) -> dict:
    missing = {group: [key for key in keys if record.get(key) is None]
               for group, keys in REQUIRED_FIELDS.items()}
    missing = {group: keys for group, keys in missing.items() if keys}
    errors = []
    if record.get("verdict") not in VERDICTS:
        errors.append("invalid_verdict")
    if (record.get("lookahead_violations") or 0) > 0:
        errors.append("lookahead_violation")
    publishable = not missing and not errors
    return {"publishable": publishable, "missing": missing, "errors": errors,
            "status": "validated" if publishable else "needs_completion"}
