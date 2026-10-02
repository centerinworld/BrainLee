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

# 전략 → strategy_regime_policy.strategy_family 매핑
_STRATEGY_FAMILY = {
    "v_gc": "breakout",
    "sc_v10": "breakout",
    "sc_v5": "breakout",
    "sc_v11": "breakout",
    "v_contract_momentum": "momentum",
    "ai_combo": "momentum",
    "sc_v8": "momentum",
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


def _get_regime_policy(cur) -> dict:
    """현재 시장 레짐 및 전략별 action 조회. {strategy_family: {action, suitability_score}}"""
    cur.execute(
        """SELECT market_regime FROM market_regime_daily
           WHERE index_code='^KS11' ORDER BY trade_date DESC LIMIT 1"""
    )
    row = cur.fetchone()
    current_regime = row[0] if row else "bull"

    cur.execute(
        """SELECT strategy_family, action, suitability_score
           FROM strategy_regime_policy
           WHERE market_regime=%s""",
        (current_regime,),
    )
    policy = {r[0]: {"action": r[1], "suitability": float(r[2] or 0)} for r in cur.fetchall()}
    return {"regime": current_regime, "policy": policy}


def capture() -> dict:
    signal_date = datetime.now().date().isoformat()
    available_at = datetime.now().isoformat(timespec="seconds")
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()

        regime_info = _get_regime_policy(cur)
        current_regime = regime_info["regime"]
        policy = regime_info["policy"]

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
        skipped_regime_off = 0
        for row in selected:
            strategy = str(row[2] or "")
            family = _STRATEGY_FAMILY.get(strategy, "momentum")
            regime_action = policy.get(family, {}).get("action", "active")
            regime_suitability = policy.get(family, {}).get("suitability", 0.0)

            # action='off'인 레짐에서는 신호 생성 건너뜀
            if regime_action == "off":
                skipped_regime_off += 1
                continue

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
            # Quality 팩터 조회 (최신 분기 기준)
            cur.execute("""
                SELECT quality_score, quality_grade
                FROM kr_quality_factor
                WHERE stock_code=%s
                ORDER BY year DESC, quarter DESC LIMIT 1
            """, (row[0],))
            qf = cur.fetchone()
            quality_score = float(qf[0]) if qf else None
            quality_grade = qf[1] if qf else None

            payload = {
                "source": "peak_holding_prospective_snapshot",
                "stock_name": row[1],
                "observed_buy_price": row[3],
                "observed_current_price": row[4],
                "source_entry_date": row[5],
                "entry_reason_json": row[6],
                "source_updated_at": str(row[7]),
                "strategy_version": versions[str(row[2])],
                "market_regime": current_regime,
                "regime_action": regime_action,
                "regime_suitability": regime_suitability,
                "quality_score": quality_score,
                "quality_grade": quality_grade,
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
            "skipped_regime_off": skipped_regime_off,
            "market_regime": current_regime,
            "strategies": sorted({row[2] for row in selected}),
            "strategy_versions": versions,
            "signal_ids": signal_ids,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
