#!/usr/bin/env python3
"""Static fail-closed audit for recurring backtest methodology hazards."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATHS = [ROOT / "backtest_common.py", *sorted((ROOT / "backtest_strategies").glob("*.py"))]
OUT_JSON = ROOT / "research_outputs" / "backtest_static_contracts_latest.json"
OUT_MD = ROOT / "research_outputs" / "backtest_static_contracts_latest.md"
STRICT_TICKER_GLOB = "GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"


def _defaults(node: ast.FunctionDef) -> dict[str, object]:
    names = [arg.arg for arg in node.args.args]
    values = [None] * (len(names) - len(node.args.defaults)) + list(node.args.defaults)
    result = {}
    for name, value in zip(names, values):
        if isinstance(value, ast.Constant):
            result[name] = value.value
    return result


def audit() -> dict:
    findings = []
    for path in PATHS:
        source = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        lines = source.splitlines()
        for number, line in enumerate(lines, 1):
            if "GLOB '[0-9]*'" in line:
                findings.append({
                    "severity": "P0", "code": "WEAK_NUMERIC_TICKER_FILTER",
                    "file": relative, "line": number,
                    "evidence": "[0-9]* accepts provider aliases such as 00088K",
                })
            if re.search(r"execution_timing\s*=\s*['\"]same_close['\"]", line):
                findings.append({
                    "severity": "P0", "code": "SAME_CLOSE_EXECUTION",
                    "file": relative, "line": number,
                    "evidence": "signal-day close execution is not orderable without look-ahead",
                })
            if "market_cap_mode=\"current\"" in line or "market_cap_mode='current'" in line:
                findings.append({
                    "severity": "P1", "code": "CURRENT_MARKET_CAP_MODE",
                    "file": relative, "line": number,
                    "evidence": line.strip(),
                })
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("run_backtest"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            defaults = _defaults(node)
            if defaults.get("strict_exec") is False:
                findings.append({
                    "severity": "P0", "code": "SAME_CLOSE_DEFAULT",
                    "file": relative, "line": node.lineno, "function": node.name,
                    "evidence": "strict_exec defaults to False",
                })
            uses_delayed_data = any(token in segment for token in (
                "financial_data", "dart_disclosures", "order_contracts",
                "consensus", "insider",
            ))
            params = {arg.arg for arg in node.args.args}
            if uses_delayed_data and "data_asof_ts" not in params:
                findings.append({
                    "severity": "P1", "code": "NO_DATA_ASOF_PARAMETER",
                    "file": relative, "line": node.lineno, "function": node.name,
                    "evidence": "delayed/fundamental data is used without a data_asof_ts contract",
                })
            if "COALESCE(d.avail_date" in segment:
                findings.append({
                    "severity": "P1", "code": "DISCLOSURE_DATE_FALLBACK",
                    "file": relative, "line": node.lineno, "function": node.name,
                    "evidence": "estimated filing dates are mixed with verified availability dates",
                })

    counts = {
        severity: sum(row["severity"] == severity for row in findings)
        for severity in ("P0", "P1")
    }
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "files_checked": len(PATHS),
        "verdict": "BLOCKED" if counts["P0"] else "REVIEW_REQUIRED" if counts["P1"] else "PASS",
        "counts": counts,
        "findings": findings,
        "contracts": {
            "ticker_filter": STRICT_TICKER_GLOB,
            "execution": "close_D_to_next_open",
            "availability": "verified_available_at_or_explicit_approx",
        },
    }


def write(result: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 백테스트 정적 계약 감사", "",
        f"- 점검시각: {result['checked_at']}",
        f"- 결론: **{result['verdict']}**",
        f"- 검사 파일: {result['files_checked']}개",
        f"- P0: {result['counts']['P0']}건 / P1: {result['counts']['P1']}건", "",
        "## 발견사항", "",
    ]
    for item in result["findings"]:
        lines.append(
            f"- **{item['severity']} {item['code']}** "
            f"`{item['file']}:{item['line']}` - {item['evidence']}"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    result = audit()
    write(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["counts"]["P0"] else 0)
