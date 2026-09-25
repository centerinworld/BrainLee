#!/usr/bin/env python3
"""Record the §12 R1 strategy adoption review in signal_experiment_ledger. Idempotent by experiment_name.
Evidence: research_outputs/strategy_adoption_review_20260925.md / *_virtual_20260925.csv"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

ROWS = [
    ("adoption_review", "adoption_review_backtest_strategies_20260925",
     "strategy_center의 26개 전략이 표본 외(2025-01~) 비용 차감 KOSPI 초과수익·DSR>0.95·PBO<0.5·최근 12개월 기대값>0을 모두 충족한다",
     "rejected_none_pass",
     "4개 기준 통과 0개. ① 표본 외 초과수익 통과 5개(golden_cross·earnings_conviction·turnaround·contract_momentum·se_momentum)는 전부 근사 곡선(engine_share 0, 고정 크기·자본 제약 없음)이라 유보 — 엔진 재실행 필요. 엔진 곡선 전략(v2·v11·v_trend 등)은 표본 외 초과 -22~-57%p(KOSPI +78~87%, 베타 0.4~0.8). DSR(N=38) 최고 v11 0.60·전부 0.95 미만. 표본 외 1.3년으로 통계력 약함. 근거: research_outputs/strategy_adoption_review_20260925.md"),
    ("adoption_review", "adoption_review_pbo_cscv_20260925",
     "26개 전략 집합에서 학습 구간 최고 전략이 검증 구간에서도 상위권이다(과최적화 아님)",
     "rejected_overfit_risk",
     "CSCV(S=16, 12,870분할, 25개 전략, 2020-11~2026-03) PBO = 0.576 ≥ 0.5, 중앙 로짓 -0.15, IS 최고 전략의 OOS 일별 Sharpe 평균 0.036. 전략 선택 절차 자체가 과최적화 위험 — 전략 추가보다 제거를 우선(§12-3 원칙 3)"),
    ("adoption_review", "adoption_review_virtual_strategies_20260925",
     "운영 가상매매 전략의 실제 진입 기록이 비용 차감 후 KOSPI를 이긴다(peak_holding 청산 309건, 2026-05~09)",
     "rejected_all_negative_excess",
     "비용 차감(profit_pct는 총수익 확인) 순기대값: momentum n133 -1.08%(승률 26%, KOSPI 대비 -5.4%p, t -4.4), peak n76 +0.38%(대비 -4.1%p, t -2.3). 나머지 9개 전략은 n<30 판정 불가이나 전부 순기대값 음수. 급락 창(07-01~09-23) momentum -4.6%. shadow 전환 제안은 사용자 승인 대기(보유분 청산 없음). 근거: strategy_adoption_review_virtual_20260925.csv"),
]


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=120)
    have = {r[0] for r in conn.execute("SELECT experiment_name FROM signal_experiment_ledger").fetchall()}
    new = [r for r in ROWS if r[1] not in have]
    print({"to_insert": len(new), "already": len(ROWS) - len(new), "apply": apply})
    if not apply or not new:
        return
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany("INSERT INTO signal_experiment_ledger(strategy_key,experiment_name,hypothesis,verdict,detail,tested_at) VALUES(?,?,?,?,?,?)",
                     [(a, b, c, d, e, now) for a, b, c, d, e in new])
    conn.commit()
    print("inserted", len(new))


if __name__ == "__main__":
    main("--apply" in sys.argv)
