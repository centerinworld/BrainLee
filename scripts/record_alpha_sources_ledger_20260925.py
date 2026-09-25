#!/usr/bin/env python3
"""Record the §12 R4 alpha-source screening in signal_experiment_ledger. Idempotent by experiment_name.
Evidence: research_outputs/alpha_sources_20260925.{md,csv}, alpha_sources_orth_20260925.csv"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

ROWS = [
    ("research_factor", "alpha_source_sue_op_20260925",
     "영업이익 서프라이즈 대용(전년동기 대비 증감 ÷ 최근 8분기 증감 표준편차, 공시 시차 반영)이 전방 20/60일 수익을 예측한다",
     "candidate_robust_orthogonal",
     "IC 0.025(t 4.3, 20D)/0.023(60D), 학습 0.022/검증 0.033(t 3.4) 부호 유지, 섹터중립 0.024/0.026. 시총·수익률·52주고점·거래량비를 제거한 직교화 후에도 20D 0.026(t 5.2)·60D 0.028(t 2.7), 검증 0.032/0.024 통과. Q5-Q1 20일 +1.0%p. PEAD형 신호. 실행 백테스트·이벤트 스터디 미실시. 근거: research_outputs/alpha_sources_20260925.md"),
    ("research_factor", "alpha_source_sue_ni_20260925",
     "순이익 서프라이즈 대용(SUE)이 전방 수익을 예측한다",
     "candidate_moderate",
     "20D IC 0.019(t 4.0), 학습 0.019/검증 0.020(t 2.3). 직교화 후 20D 통과(0.021, t 4.8), 60D는 검증 t 0.6로 약함. sue_op보다 약함"),
    ("research_factor", "alpha_source_borrow_pct_20260925",
     "대차잔고 비율(borrow_bal_pct)이 높을수록 이후 수익이 낮다(공매도 압력)",
     "candidate_negative_signal",
     "IC -0.023(t -2.1, 20D)/-0.032(t -2.4, 60D), 학습 -0.008/검증 -0.073로 부호 유지·검증에서 강화. 직교화 후 20D -0.020(t -3.0)·60D -0.032(t -3.1) 통과. 회피 필터 후보. 공매도 금지 기간(2023-11~2025-03)에는 IC≈0(ban 0.005) — 금지 해제 후 기간에 의존"),
    ("research_factor", "alpha_source_short_ratio_20260925",
     "20일 공매도 거래 비중이 이후 수익을 예측한다",
     "candidate_uncertain_interpretation",
     "양(+)의 IC 0.037(t 3.8), 검증 0.101(t 5.3). 직교화 후에도 20D 0.022(t 3.8) 통과하나 60D는 미통과. 방향이 직관(공매도↑→하락)과 반대 — 금지 기간에는 시장조성자·헤지 거래가 대부분이라 유동성·옵션 활성도 프록시일 가능성. 원인 규명 전 채택 금지"),
    ("research_factor", "alpha_source_credit_20260925",
     "신용잔고율/신용잔고 증감이 이후 수익을 예측한다",
     "rejected_decaying_or_confounded",
     "credit_chg_20d: IC -0.036(t -5.9)이나 검증 -0.030(20D)·-0.012(60D)로 약화, 직교화 후 검증 -0.019(t -1.9)로 임계 미달(학습 t -5.1). credit_ratio: 직교화 후 IC 0.003(t 0.4)로 소멸 — 시총·최근 수익률 효과였음"),
    ("research_factor", "alpha_source_consensus_targets_20260925",
     "목표주가 리비전·상향 breadth·괴리율(consensus_targets)이 전방 수익을 예측한다",
     "inconclusive_insufficient_history",
     "이력이 2024-05~2026-09(약 27개월, 종목·월 2,700건)뿐이라 학습 구간(~2024-12) 표본이 677건. 모든 지표 |t|<2, 부호 불안정(60D 리비전 raw 학습 0.002/검증 0.033). 과거 백필(hankyung_consensus_collector) 가능성 확인 전까지 판정 불가"),
    ("research_model", "lightgbm_ranking_walkforward_20260925",
     "LightGBM 횡단면 랭킹 모델(사이즈·가치·모멘텀·유동성·수급·R4 신호)이 기존 model_score를 대체하고 벤치마크를 이긴다",
     "rejected_not_beating_benchmark_marginal_over_composite",
     "purged·embargoed walk-forward(2023-01~, 43개월, 6개월 재학습): IC 0.159(t 8.0) > 3팩터 합성 0.127 > 기존 model_score -0.079. 그러나 상위 20% 동일가중 롱온리는 KOSPI 대비 월 -1.6%p(비용 차감 -2.0%p, 2025+ -3.8%p)로 R1 기준 ① 미충족, 유니버스 평균 대비 +0.74%p/월(회전율 0.40, 왕복 1% 가정 시 실익 작음), 합성(+0.64%p, 회전율 0.28)과 차이 작음. 피처 중요도 저변동성 29%. 운영 반영·shadow 미진행. 근거: research_outputs/lightgbm_ranking_20260925.md"),
    ("research_factor", "alpha_source_eps_revision_20260925",
     "추정 EPS 리비전(forward_estimate_snapshots)이 전방 수익을 예측한다",
     "inconclusive_insufficient_history",
     "snapshot_date가 2026-07-03~09-25(201종목)뿐이라 월간 IC 자체가 불가(문서의 '2025-04~'는 estimate_date 기준). 수집 확대(collect_kis_forward_estimates.py limit 450) 후 재검"),
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
