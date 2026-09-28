#!/usr/bin/env python3
"""Classify selected runs by whether delayed data has row-level PIT evidence."""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_common import (  # noqa: E402
    SIGNAL_EVIDENCE_REQUIREMENTS,
    _normalize_entries,
    evaluate_signal_evidence,
)
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact  # noqa: E402


OUT_JSON = ROOT / "research_outputs" / "selected_strategy_data_availability_latest.json"
OUT_MD = ROOT / "research_outputs" / "selected_strategy_data_availability_latest.md"
CODE_PATHS = [ROOT / "backtest_common.py", *sorted((ROOT / "backtest_strategies").glob("*.py"))]
DELAYED_TOKENS = (
    "fin_rows", "['fins']", '["fins"]', "financial_data", "dart_",
    "order_contract", "order_backlog", "insider", "consensus", "segment_revenue",
)

# 2026-09-22: 이 스크립트는 파일 전체 텍스트를 스캔해서 "지연데이터를 쓸 수 있는 코드가
# 존재하는가"만 본다 - "이 run이 실제로 그 코드경로를 탔는가"는 구분하지 못한다.
# megatrend가 이 오탐의 실제 사례로 확인됨: require_earnings_accel(기본값 False)이
# 켜져야만 financial_data 쿼리가 실행되는데, 등록된 실제 run의 parameter_json엔 이 키가
# 아예 없어(기본값 사용) 이 run은 재무데이터를 전혀 안 읽었다(backtest_strategies/
# megatrend.py:71,269,498 확인, 2026-09-22).
#
# 일반화된 파라미터 분석기를 만드는 대신, 코드를 직접 읽어 확인한 전략만 여기에
# 수동으로 등록한다 - {strategy: {param_name: "지연데이터 미사용을 의미하는 값"}}.
# 이 run의 parameter_json에 그 파라미터가 없거나(기본값 사용) 정확히 이 값이면
# 텍스트스캔 결과를 무시하고 통과 처리한다. 근거 확인 없이 추가 금지.
_VERIFIED_OPTIONAL_DELAYED_DATA_GATES: dict[str, dict[str, object]] = {
    "megatrend": {"require_earnings_accel": False},
}


def _delayed_data_gate_off(strategy: str, params: dict) -> bool:
    """True면 이 run은 검증된 opt-in 게이트가 전부 꺼져 있어 지연데이터를 쓸 수 없다."""
    gates = _VERIFIED_OPTIONAL_DELAYED_DATA_GATES.get(strategy)
    if not gates:
        return False
    return all(params.get(name, off_value) == off_value for name, off_value in gates.items())


def _function_sources() -> dict[str, str]:
    functions = {}
    for path in CODE_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = ast.get_source_segment(source, node) or ""
    return functions


def _multi_input_verdict(conn, strategy: str, run_hash: str) -> dict:
    """진입(trades_json) x 필수 데이터셋 증거(backtest_signal_input_evidence) 대조."""
    row = conn.execute(
        """SELECT r.trades_json FROM backtest_run_specs s JOIN backtest_runs r ON r.run_id=s.run_id
           WHERE s.run_hash=? AND r.status='done' ORDER BY s.created_at DESC LIMIT 1""",
        (run_hash,),
    ).fetchone()
    payload = json.loads((row[0] if row else None) or "[]")
    trades = payload.get("trades", []) if isinstance(payload, dict) else payload
    entries = _normalize_entries(trades)
    try:
        evidence = [
            {"stock_code": r[0], "entry_date": r[1], "decision_date": r[2], "dataset": r[3],
             "source_row_id": r[4], "available_at": r[5], "availability_basis": r[6]}
            for r in conn.execute(
                """SELECT stock_code,entry_date,decision_date,dataset,source_row_id,available_at,
                          availability_basis
                   FROM backtest_signal_input_evidence WHERE run_hash=?""", (run_hash,)
            ).fetchall()
        ]
    except Exception:
        evidence = []
    verdict = evaluate_signal_evidence(strategy, entries, evidence)
    verdict.pop("samples", None) if verdict.get("passed") else None
    return verdict


def audit() -> dict:
    function_sources = _function_sources()
    file_sources = {
        str(path.relative_to(ROOT)): path.read_text(encoding="utf-8") for path in CODE_PATHS
    }
    conn = connect_stock_db(readonly=True)
    components = []
    try:
        rows = conn.execute(
            """SELECT x.strategy,x.run_hash,m.period_label,m.run_hash,s.parameter_json
               FROM selected_run_registry x
               JOIN backtest_run_set_members m ON m.suite_hash=x.run_hash
               JOIN (
                 SELECT run_hash,MAX(parameter_json) parameter_json
                 FROM backtest_run_specs GROUP BY run_hash
               ) s ON s.run_hash=m.run_hash
               WHERE x.report_type='strategy_center'
               ORDER BY x.strategy,m.period_label"""
        ).fetchall()
        for strategy, suite_hash, period, run_hash, parameter_json in rows:
            params = json.loads(parameter_json or "{}")
            fingerprints = params.get("_code_fingerprint") or {}
            relevant = "\n".join(
                file_sources.get(path, "") for path in fingerprints
                if path.startswith("backtest_strategies/")
            )
            for key in ("signal_fn", "entry_bonus_fn", "sell_signal_fn"):
                name = params.get(key)
                if name:
                    relevant += "\n" + function_sources.get(str(name), "")
            dependencies = sorted({token for token in DELAYED_TOKENS if token in relevant})
            uses_delayed = bool(dependencies)
            gate_off = uses_delayed and _delayed_data_gate_off(str(strategy), params)
            provenance = None
            multi_input = str(strategy) in SIGNAL_EVIDENCE_REQUIREMENTS
            if multi_input:
                # 2026-09-28: 다중 입력 전략은 텍스트 스캔 결과와 무관하게
                # "모든 진입 x 필수 데이터셋" 증거 원장으로만 판정한다.
                provenance = _multi_input_verdict(conn, str(strategy), str(run_hash))
                uses_delayed = True
                gate_off = False
                dependencies = sorted(set(dependencies) | set(SIGNAL_EVIDENCE_REQUIREMENTS[str(strategy)]))
            elif uses_delayed and not gate_off:
                try:
                    p = conn.execute("""SELECT COUNT(*),
                           SUM(CASE WHEN available_at IS NOT NULL AND available_at>decision_date THEN 1 ELSE 0 END)
                        FROM backtest_signal_data_provenance WHERE run_hash=?""", (run_hash,)).fetchone()
                    trade_row = conn.execute("""SELECT COALESCE(MAX(r.total_trades),0)
                        FROM backtest_run_specs s JOIN backtest_runs r ON r.run_id=s.run_id
                        WHERE s.run_hash=? AND r.status='done'""", (run_hash,)).fetchone()
                    provenance = {
                        "records": int((p or [0])[0] or 0),
                        "invalid_available_at": int((p or [0, 0])[1] or 0),
                        "executed_entries": int((trade_row or [0])[0] or 0),
                    }
                except Exception:
                    provenance = None
            if multi_input:
                passed = bool(provenance and provenance.get("passed"))
            else:
                passed = not uses_delayed or gate_off or bool(
                    provenance
                    and provenance["records"] == provenance["executed_entries"]
                    and provenance["invalid_available_at"] == 0
                )
            if not uses_delayed:
                reason = "price/volume-only logic; delayed-data availability is not applicable"
            elif gate_off:
                reason = (
                    "code can use delayed data, but this run's own parameters leave the "
                    "opt-in gate off (verified against source, see "
                    "_VERIFIED_OPTIONAL_DELAYED_DATA_GATES) - not actually consumed"
                )
            elif multi_input and passed:
                reason = ("every executed entry x required dataset has persisted source rows with "
                          f"available_at <= signal_date ({provenance.get('pit_grade')})")
            elif multi_input:
                reason = ("multi-input evidence incomplete: "
                          f"missing={provenance.get('missing_evidence') if provenance else 'n/a'}, "
                          f"late={provenance.get('available_after_signal') if provenance else 'n/a'}")
            elif passed and provenance is not None:
                reason = "every executed entry has persisted financial row/availability provenance"
            else:
                reason = "delayed data is used but row-level values consumed by each signal were not persisted"
            components.append({
                "strategy": str(strategy), "suite_hash": str(suite_hash),
                "period": str(period), "run_hash": str(run_hash),
                "passed": passed, "reason": reason,
                "delayed_dependencies": dependencies,
                "data_asof_ts": params.get("data_asof_ts"),
                "provenance": provenance,
            })
    finally:
        conn.close()

    for item in components:
        register_artifact(item["run_hash"], "data_availability", item["passed"], {
            "strategy": item["strategy"], "period": item["period"],
            "reason": item["reason"],
            "delayed_dependencies": item["delayed_dependencies"],
            "data_asof_ts": item["data_asof_ts"],
            "provenance": item["provenance"],
            "pit_grade": (item["provenance"] or {}).get("pit_grade") if isinstance(item["provenance"], dict) else None,
            "required_for_delayed_data": "persisted signal-input row ids and available_at values",
        })

    strategies = {}
    for item in components:
        row = strategies.setdefault(item["strategy"], {"components": 0, "passed": 0, "failed": 0})
        row["components"] += 1
        row["passed" if item["passed"] else "failed"] += 1
    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "verdict": "BLOCKED" if any(not row["passed"] for row in components) else "PASS",
        "component_count": len(components),
        "passed": sum(row["passed"] for row in components),
        "failed": sum(not row["passed"] for row in components),
        "strategies": strategies,
        "components": components,
    }
    return result


def write(result: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 선택 전략 데이터 가용시점 감사", "",
        f"- 점검시각: {result['checked_at']}",
        f"- 결론: **{result['verdict']}**",
        f"- 컴포넌트: {result['component_count']}개",
        f"- 통과: {result['passed']}개 / 실패: {result['failed']}개", "",
        "지연 데이터 사용 전략은 신호별 입력 행 ID와 available_at을 저장하기 전까지 PIT로 승격하지 않는다.",
        "", "## 전략별 결과", "",
    ]
    for strategy, item in sorted(result["strategies"].items()):
        lines.append(
            f"- `{strategy}`: 통과 {item['passed']}/{item['components']}, 실패 {item['failed']}"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    result = audit()
    write(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["verdict"] == "BLOCKED" else 0)
