"""backtest.py의 run_backtest_* 함수들이 시총(market_cap) 룩어헤드를
안전하게 처리하는지 정적으로 점검하는 감사 스크립트.

2026-08-12: 이번 세션까지 최소 6차례(v4/megatrend/earnings_conviction/moonshot_
turnaround/golden_cross/contract_momentum 등) "stock_universe.market_cap(현재
시총)을 과거 백테스트 구간에 그대로 적용"하는 룩어헤드 버그가 반복 발견됐다.
매번 사후에 발견하는 대신, 새 전략을 추가하거나 기존 전략을 수정할 때마다 이
스크립트로 사전 점검하는 것이 재발 방지의 현실적인 방법이다(공용 시뮬레이터로
전면 통합하는 것은 위험도가 커서 계속 보류돼 왔음 — CLAUDE.md 여러 항목 참조).

점검 방식(휴리스틱, 완벽하지 않음 — 최종 판단은 사람이 해야 함):
1. 각 run_backtest_* 함수를 AST로 분리
2. 함수 본문에서 "stock_universe" 참조와 "market_cap" 참조를 함께 갖는 SQL
   문자열을 찾는다 (su.market_cap, market_cap FROM stock_universe 등)
3. 그 함수가 asof_mktcap 파라미터를 갖고 있는지, 있다면 해당 SQL이
   `if asof_mktcap` / `else` 분기의 어느 쪽에 있는지 라인 번호 근접도로 추정
4. asof_mktcap 파라미터 자체가 없는데 market_cap 조건을 쓰는 함수는
   "무조건 현재시총 사용" 후보로 강조 표시(단, security_share_history 등을
   전혀 참조하지 않는 함수는 애초에 시총 필터가 없는 것일 수 있으니 별도 확인)

사용법:
    venv/bin/python3 scripts/audit_market_cap_lookahead.py
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

BACKTEST_PY = Path(__file__).resolve().parents[1] / "backtest.py"

MKTCAP_SQL_RE = re.compile(
    r"(su\.market_cap|market_cap\s+FROM\s+stock_universe|stock_universe\.market_cap)",
    re.IGNORECASE,
)
ASOF_SAFE_RE = re.compile(
    r"(security_master_history|security_share_history|_shares_asof|shares_asof)",
    re.IGNORECASE,
)
# 2026-08-12 발견: run_backtest_recovery는 asof_mktcap=True인데도 발행주식수를
# security_share_history가 아니라 stock_universe.shares_issued(현재값 고정)로
# 조회 — "as-of"라 라벨링됐지만 실제로는 과거 증자/감자/분할이 전혀 반영되지
# 않는 조잡한 근사. 이 패턴도 별도로 검출한다.
CURRENT_SHARES_RE = re.compile(
    r"shares_issued\s+FROM\s+stock_universe(?!.*security_share_history)",
    re.IGNORECASE,
)


def _function_source_lines(node: ast.FunctionDef, src_lines: list[str]) -> list[str]:
    return src_lines[node.lineno - 1: (node.end_lineno or node.lineno)]


def audit(path: Path = BACKTEST_PY) -> list[dict]:
    src = path.read_text()
    src_lines = src.splitlines()
    tree = ast.parse(src)
    findings = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("run_backtest"):
            continue
        func_lines = _function_source_lines(node, src_lines)
        # 주석/docstring 문장에 "market_cap"이 언급되는 경우 오탐을 방지하기 위해
        # 코드 라인만 남기고 순수 주석(#으로 시작)은 검사 대상에서 제외한다.
        # (완벽하지 않음 — 줄 끝 인라인 주석은 이 필터로 걸러지지 않으므로 여전히
        #  사람이 최종 확인해야 함)
        code_only_lines = [
            line for line in func_lines if not line.strip().startswith("#")
        ]
        func_src = "\n".join(code_only_lines)

        has_asof_param = any(
            isinstance(a, ast.arg) and a.arg == "asof_mktcap" for a in node.args.args
        )
        asof_default = None
        if has_asof_param:
            arg_names = [a.arg for a in node.args.args]
            defaults = node.args.defaults
            offset = len(arg_names) - len(defaults)
            for arg_name, default in zip(arg_names[offset:], defaults):
                if arg_name == "asof_mktcap" and isinstance(default, ast.Constant):
                    asof_default = default.value

        mktcap_hits = [
            (i, line) for i, line in enumerate(code_only_lines)
            if MKTCAP_SQL_RE.search(line)
        ]
        has_asof_safe_ref = bool(ASOF_SAFE_RE.search(func_src))

        if not mktcap_hits:
            continue  # 이 함수는 시총 필터 자체가 없음(또는 다른 방식) — 대상 아님

        severity = "info"
        reasons = []
        if not has_asof_param:
            severity = "warn"
            reasons.append("asof_mktcap 파라미터가 없는데 stock_universe.market_cap(현재시총)을 참조함")
        elif asof_default is False:
            severity = "info"
            reasons.append("asof_mktcap 기본값이 False로 명시적 설정됨(과거 as-of 재검증에서 기각된 경우가 많음 — 주석 확인 필요)")
        elif not has_asof_safe_ref:
            severity = "warn"
            reasons.append("asof_mktcap 파라미터는 있으나 security_master_history/security_share_history 참조가 함수 내에 없음 — as-of 분기가 실제로 구현됐는지 확인 필요")

        if CURRENT_SHARES_RE.search(func_src):
            severity = "warn"
            reasons.append("발행주식수를 stock_universe.shares_issued(현재값 고정)로 조회 — security_share_history 기반 as-of 발행주식수가 아님(조잡한 근사)")

        findings.append({
            "function": node.name,
            "lineno": node.lineno,
            "has_asof_param": has_asof_param,
            "asof_default": asof_default,
            "has_asof_safe_ref": has_asof_safe_ref,
            "mktcap_reference_count": len(mktcap_hits),
            "severity": severity,
            "reasons": reasons,
        })
    return findings


def main() -> None:
    findings = audit()
    warns = [f for f in findings if f["severity"] == "warn"]
    infos = [f for f in findings if f["severity"] == "info"]
    print(f"=== market_cap 룩어헤드 감사 결과 ({BACKTEST_PY.name}) ===")
    print(f"시총 필터를 쓰는 run_backtest_* 함수: {len(findings)}개  (경고 {len(warns)}건 / 참고 {len(infos)}건)\n")
    for group_name, group in (("⚠️  경고(직접 확인 필요)", warns), ("ℹ️  참고(기존에 검증/기각된 것으로 추정)", infos)):
        if not group:
            continue
        print(f"--- {group_name} ---")
        for f in group:
            print(f"  {f['function']} (L{f['lineno']}): "
                  f"asof_param={f['has_asof_param']} default={f['asof_default']} "
                  f"asof_ref={f['has_asof_safe_ref']} mktcap참조={f['mktcap_reference_count']}건")
            for r in f["reasons"]:
                print(f"    -> {r}")
        print()
    if warns:
        print(f"⚠️  {len(warns)}개 함수는 사람이 직접 코드를 열어 확인할 것을 권장합니다.")
    else:
        print("✅ 경고 없음 — 시총 필터를 쓰는 함수는 전부 asof_mktcap 분기 처리가 확인됩니다.")


if __name__ == "__main__":
    main()
