#!/usr/bin/env python3
"""Freeze current strategy-center holdings as prospective forward-test signals."""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db_compat  # noqa: E402
from live_signal_tracker import register_signal  # noqa: E402

ALLOWED_STRATEGIES = {
    "v_gc", "v_contract_momentum",
    # sc_* 접두사 전략센터 운용 전략 (peak_holding strategy 이름 그대로)
    "ai_combo", "sc_v10", "sc_v5", "sc_v8", "sc_v11",
}
# 가상운용 엔진(routes/trend.py)에서 전략별 신호·체결 규칙을 담은 함수·상수 접두사.
# 이 소스가 바뀌면 strategy_version이 바뀌고, forward 표본은 버전별로 따로 집계된다.
_VERSION_SCOPE = {
    "v_gc": ("GC_", "_gc_", "_build_gc_", "execute_gc_", "paper_"),
    "v_contract_momentum": ("CM_", "_cm_", "_build_cm_", "execute_cm_", "paper_"),
    "ai_combo": ("ai_combo", "_ai_combo", "AICOMBO_"),
    "sc_v10": ("V10_", "_v10_", "sc_v10"),
    "sc_v5": ("V5_", "_v5_", "sc_v5"),
    "sc_v8": ("V8_", "_v8_", "sc_v8"),
    "sc_v11": ("V11_", "_v11_", "sc_v11"),
}


def strategy_version(strategy: str) -> str:
    """전략 코드·파라미터 지문(표본 분리용). routes/trend.py + paper_execution.py 해당 구간 해시."""
    prefixes = _VERSION_SCOPE.get(strategy, ())
    parts = []
    for rel in ("routes/trend.py", "paper_execution.py"):
        path = ROOT / rel
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n.startswith(prefixes) or n.lstrip("_").startswith(tuple(p.lstrip("_") for p in prefixes))
                   for n in names):
                parts.append(ast.get_source_segment(source, node) or "")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def capture() -> dict:
    signal_date = datetime.now().date().isoformat()
    available_at = datetime.now().isoformat(timespec="seconds")
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT stock_code,stock_name,strategy,buy_price,current_price,entry_date,
                      entry_reason_json,updated_at
               FROM peak_holding
               WHERE is_active=1 AND stock_code IS NOT NULL AND length(stock_code)=6
               ORDER BY strategy,stock_code"""
        )
        rows = cur.fetchall()
        selected = [row for row in rows if str(row[2] or "") in ALLOWED_STRATEGIES]
        versions = {name: strategy_version(name) for name in ALLOWED_STRATEGIES}
        signal_ids = []
        skipped_existing_episode = 0
        for row in selected:
            cur.execute(
                """SELECT signal_payload_json FROM live_signal_registry
                   WHERE stock_code=%s AND strategy_id=%s AND action='BUY_CANDIDATE'
                   ORDER BY signal_date DESC LIMIT 1""",
                (row[0], row[2]),
            )
            prior = cur.fetchone()
            if prior:
                try:
                    prior_payload = json.loads(prior[0] or "{}")
                except (TypeError, ValueError):
                    prior_payload = {}
                if str(prior_payload.get("source_entry_date") or "") == str(row[5] or ""):
                    skipped_existing_episode += 1
                    continue
            payload = {
                "source": "peak_holding_prospective_snapshot",
                "stock_name": row[1],
                "observed_buy_price": row[3],
                "observed_current_price": row[4],
                "source_entry_date": row[5],
                "entry_reason_json": row[6],
                "source_updated_at": str(row[7]),
                "strategy_version": versions[str(row[2])],
            }
            signal_ids.append(register_signal(
                stock_code=row[0], signal_type="strategy_center_buy",
                strategy_id=row[2], signal_date=signal_date, available_at=available_at,
                action="BUY_CANDIDATE", payload=payload, conn=conn,
            ))
        conn.commit()
        return {
            "signal_date": signal_date,
            "captured": len(signal_ids),
            "skipped_existing_episode": skipped_existing_episode,
            "strategies": sorted({row[2] for row in selected}),
            "strategy_versions": versions,
            "signal_ids": signal_ids,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
