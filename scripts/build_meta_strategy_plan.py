#!/usr/bin/env python3
"""
손실 제한형 복합전략 운영안 생성기

- 시장 국면(5단계)을 signal_engine에서 읽어 전략 비중을 동적으로 배분
- 과거 백테스트 성과(수익/낙폭) 기반으로 전략 점수 계산
- DART 수주공시 강신호 종목을 오버레이 후보로 제시
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/Applications/stock_dashboard")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import signal_engine

DB_PATH = "/Applications/stock_dashboard/stock.db"
OUT_PATH = Path("/Applications/stock_dashboard/scratch/meta_strategy_plan_2026-05-19.json")


@dataclass
class StratStat:
    strategy: str
    avg_ret: float
    avg_mdd: float
    avg_win: float
    samples: int
    score: float


def _fetch_strategy_stats(conn: sqlite3.Connection) -> list[StratStat]:
    """
    최근 검증구간(5개)에서 전략별 평균 성과를 취합.
    score = avg_ret - 0.7*abs(avg_mdd) + 0.15*avg_win
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT strategy,
               AVG(total_return_pct) AS avg_ret,
               AVG(max_drawdown_pct) AS avg_mdd,
               AVG(win_rate) AS avg_win,
               COUNT(*) AS n
        FROM backtest_runs
        WHERE status='done'
          AND strategy IN ('v_trend','v_dart','v8','v10','v11','v12','v1_value','v2','v5','combo')
          AND start_date IN ('2020-03-01','2021-12-01','2022-11-01','2023-11-01','2024-06-01')
        GROUP BY strategy
        """
    ).fetchall()
    out: list[StratStat] = []
    for r in rows:
        avg_ret = float(r["avg_ret"] or 0.0)
        avg_mdd = float(r["avg_mdd"] or 0.0)
        avg_win = float(r["avg_win"] or 0.0)
        score = avg_ret - 0.7 * abs(avg_mdd) + 0.15 * avg_win
        out.append(
            StratStat(
                strategy=r["strategy"],
                avg_ret=round(avg_ret, 2),
                avg_mdd=round(avg_mdd, 2),
                avg_win=round(avg_win, 2),
                samples=int(r["n"] or 0),
                score=round(score, 2),
            )
        )
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def _pick_regime(snapshot: dict) -> int:
    """KOSPI/KOSDAQ 중 더 보수적인(숫자가 큰) stage 채택."""
    stages = []
    for m in snapshot.get("markets", []):
        try:
            stages.append(int(m.get("stage", 3)))
        except Exception:
            continue
    return max(stages) if stages else 3


def _base_weights_for_stage(stage: int) -> dict[str, float]:
    """
    국면별 기본 비중:
    - stage 1~2: 추세/모멘텀 중심
    - stage 3: 균형
    - stage 4~5: 방어 중심 (v11, v1_value, cash)
    """
    if stage <= 2:
        return {
            "v5": 0.26,
            "v_trend": 0.24,
            "v1_value": 0.16,
            "v11": 0.14,
            "v8": 0.10,
            "v_dart": 0.05,
            "v10": 0.05,
            "cash": 0.00,
        }
    if stage == 3:
        return {
            "v1_value": 0.26,
            "v11": 0.24,
            "v5": 0.16,
            "v_trend": 0.14,
            "v8": 0.08,
            "v_dart": 0.06,
            "v10": 0.06,
            "cash": 0.00,
        }
    if stage == 4:
        return {
            "v11": 0.34,
            "v1_value": 0.28,
            "v_dart": 0.10,
            "v8": 0.08,
            "v5": 0.05,
            "v_trend": 0.05,
            "v10": 0.00,
            "cash": 0.10,
        }
    return {
        "v11": 0.38,
        "v1_value": 0.30,
        "v_dart": 0.08,
        "v8": 0.04,
        "v5": 0.00,
        "v_trend": 0.00,
        "v10": 0.00,
        "cash": 0.20,
    }


def _tilt_by_scores(base: dict[str, float], stats: list[StratStat]) -> dict[str, float]:
    """기본 비중 위에 전략 점수 기반 ±20% 틸트."""
    score_map = {s.strategy: s.score for s in stats}
    active = {k: v for k, v in base.items() if k != "cash" and v > 0}
    if not active:
        return base
    vals = [score_map.get(k, 0.0) for k in active]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) if hi != lo else 1.0
    tilted = {}
    for k, w in active.items():
        s = score_map.get(k, 0.0)
        norm = (s - lo) / span  # 0~1
        mult = 0.9 + 0.2 * norm  # 0.9~1.1
        tilted[k] = w * mult
    tot = sum(tilted.values())
    cash = float(base.get("cash", 0.0))
    final = {k: round(v * (1.0 - cash) / tot, 4) for k, v in tilted.items()} if tot > 0 else {}
    if cash > 0:
        final["cash"] = round(cash, 4)
    return final


def _pick_disclosure_overlay(conn: sqlite3.Connection, days: int = 7) -> list[dict]:
    """
    최근 수주공시 중 강신호 후보.
    - ai_score >= 80
    - signal_strength >= 4
    - contract_ratio_pct >= 10 (NULL 허용)
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, disclosed_at, contract_ratio_pct,
               ai_score, signal_strength, ai_signal
        FROM dart_contracts
        WHERE disclosed_at >= ?
          AND ai_score >= 80
          AND signal_strength >= 4
          AND (contract_ratio_pct IS NULL OR contract_ratio_pct >= 10.0)
        ORDER BY disclosed_at DESC, ai_score DESC, signal_strength DESC
        LIMIT 20
        """,
        (cutoff,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "disclosed_at": r["disclosed_at"],
                "contract_ratio_pct": None if r["contract_ratio_pct"] is None else round(float(r["contract_ratio_pct"]), 2),
                "ai_score": int(r["ai_score"] or 0),
                "signal_strength": int(r["signal_strength"] or 0),
                "ai_signal": r["ai_signal"] or "",
            }
        )
    return out


def build_plan() -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        stats = _fetch_strategy_stats(conn)
        snap = signal_engine.get_market_regime_snapshot()
        stage = _pick_regime(snap)
        base = _base_weights_for_stage(stage)
        weights = _tilt_by_scores(base, stats)
        overlay = _pick_disclosure_overlay(conn)

        plan = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "regime_stage_used": stage,
            "regime_principle": "더 보수적인 시장단계(max stage)를 사용해 비중 결정",
            "strategy_stats": [s.__dict__ for s in stats],
            "base_weights": base,
            "tilted_weights": weights,
            "risk_rules": {
                "portfolio_hard_stop": -0.06,
                "daily_drawdown_cut": -0.025,
                "stage4_cash_floor": 0.10,
                "stage5_cash_floor": 0.20,
                "disable_strategies_in_stage5": ["v5", "v_trend", "v10", "v12"],
            },
            "disclosure_overlay": {
                "rule": "최근 7일 강공시(점수80+/강도4+/비율10%+) 종목을 총자산 10% 한도에서 이벤트 바스켓으로 운용",
                "max_overlay_weight": 0.10,
                "candidates": overlay,
            },
            "execution_notes": [
                "기본 비중은 시장국면별, 세부 틸트는 전략 score 기반으로 매일/주간 갱신",
                "v12는 최근 손실·낙폭이 커서 별도 개선 전까지 비중 0 또는 극소량 유지 권고",
                "약세장(stage4~5)에서는 신규매수 강도 축소 + 현금비중 하한 강제",
            ],
        }
        return plan
    finally:
        conn.close()


def main() -> None:
    plan = build_plan()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(OUT_PATH))
    print(json.dumps({"stage": plan["regime_stage_used"], "weights": plan["tilted_weights"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
