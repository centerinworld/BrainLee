#!/usr/bin/env python3
"""Record the 2026-09-24/25 research-tool findings in signal_experiment_ledger (HANDOFF §10 P1-5). Idempotent by experiment_name.
detail always names the evidence file so the ledger row can be traced (research_outputs/*)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

ROWS = [
    ("research_factor", "alphalens_low_vol_60d_20260925",
     "60일 저변동성 팩터가 월간 횡단면에서 전방 60일 수익을 예측한다(폐지 종목 포함 2,708종목, 2020-01~2026-08)",
     "validated_factor_robust",
     "IC 0.158(t비겹침 6.6), 학습(~2024-12) 0.168 / 검증(2025~) 0.165로 부호·크기 유지, 섹터중립 0.157/0.160, Q5-Q1 60일 +3.4%p. 근거: research_outputs/alphalens_factor_validation_20260924.md, alphalens_split_events_20260925.csv"),
    ("research_factor", "alphalens_value_ttm_per_pbr_20260925",
     "시점 정합 TTM PER/PBR(이익수익률·장부수익률)이 전방 60일 수익을 예측한다",
     "validated_factor_moderate",
     "earn_yield IC 0.072(t 3.3) 학습 0.070/검증 0.114, book_yield 0.070(t 2.6) 0.077/0.082 — 부호 유지·효과는 중간. 최초 결과(t 6.5)는 valuation_history.per 결함(분기별 정의 상이·분기말 종가 고정)으로 과대였음, 정정본. 근거: docs hermes.md 'PER 결함 발견·수정'"),
    ("research_factor", "alphalens_momentum_negative_ic_20260925",
     "60~120일 모멘텀이 이 기간 음의 IC(평균회귀)를 보인다",
     "rejected_period_specific",
     "학습 구간 mom_60d -0.052/mom_120d -0.060, 검증 구간 +0.014/+0.018로 부호 반전 → 기간 특이 현상(전략 신규진입 비중 조정 근거 아님). 근거: alphalens_split_events_20260925.csv"),
    ("research_factor", "alphalens_supply_20d_negative_ic_20260925",
     "20일 기관+외국인 순매수(supply_20d_억)가 음의 IC를 보인다",
     "inconclusive_decaying",
     "학습 -0.061 → 검증 -0.015로 약화, 수급 금액 결측 구간(2026-09 등)·백필 영향 가능. 역방향 필터 근거로는 부족. 근거: alphalens_split_events_20260925.csv"),
    ("research_factor", "alphalens_model_score_negative_ic_20260925",
     "기존 model_score_6m/12m(3배 라벨 로지스틱)이 평균 전방수익을 예측한다",
     "rejected_anti_predictive",
     "학습 -0.03~-0.04, 검증 -0.15~-0.16으로 오히려 악화 — 극단적 승자 라벨 목적과 평균수익 예측은 다름. 수정 PER로 재학습해도 동일. 소비처(tenbagger/전략)에서 평균수익 예측용으로 쓰지 말 것"),
    ("research_event", "event_study_buyback_20260925",
     "자사주 취득결정·신탁체결 공시 다음 거래일 진입이 시장 중앙값 대비 초과수익을 낸다(60일)",
     "candidate_positive_needs_execution_backtest",
     "취득결정 평균 +6.0%p(중앙값 +3.0, 학습 t 8.7 / 검증 t 5.3), 신탁체결 +5.6%p(중앙값 +2.1, 검증 t 8.9). 1/99% 윈저라이즈. 실행 백테스트 미실시(거래비용·유동성). 근거: research_outputs/event_study_20260925.csv"),
    ("research_event", "event_study_dilution_cb_20260925",
     "CB 발행 공시 후 60일 초과수익이 음이다",
     "validated_negative_signal",
     "CB -2.6%p(중앙값 -6.9, 학습 t -4.3 / 검증 t -4.5) — 유의·지속. 진입 회피 필터 후보. BW/EB는 표본 적고 검증 구간 양(+)이라 결론 없음"),
    ("research_event", "event_study_contract_patent_20260925",
     "공시된 대형 수주(매출 10%↑)·특허 이벤트가 초과수익을 낸다",
     "weak_mean_driven_by_tail",
     "수주 평균 +5.2%p이나 중앙값 -0.4(우측 꼬리 의존, 검증 +7.6 t 8.5), 특허 평균 +4.6/중앙값 -0.7, 검증 표본 8건. 텍스트 신호로 보기엔 약함. earnings_signals는 이력이 2026-07-10부터라 검증 불가"),
    ("research_backtest", "vectorbt_regime_filter_lowvol_value_20260925",
     "KOSPI MA100/200 국면 필터·손절이 저변동성+저PER 월간 20종목의 성과를 개선한다",
     "rejected_inconclusive_wrong_universe",
     "훈련 MDD -37%→-19%로 개선하나 검증 CAGR 15.3%→5.2%로 하락, 훈련 1위가 검증 하위. 종목선정 자체 CAGR 1.9%(KOSPI 5.7% 미만). 시험 대상이 운영 가드(VT_REGIME_FILTER)의 모멘텀·돌파 계열이 아니고 비용 가정도 운영 엔진과 다름 → 운영 가드 판단 근거로 쓰지 말 것. 근거: vectorbt_rule_sweep_20260924.md"),
    ("research_backtest", "regime_filter_momentum_breakout_20260925",
     "운영 가드 VT_REGIME_FILTER(KOSPI<MA60이면 모멘텀/돌파 신규진입 차단)가 모멘텀·돌파 신호의 성과를 개선한다",
     "validated_direction_proxy_signal_weak",
     "60일 신고가+거래량 돌파 대용 신호, 운영 _tx_cost 동일 비용: 필터 ON이 전 구간에서 CAGR·MDD 개선(전체 -23.2%→-6.6%, 급락 창 MDD -41.6%→-23.7%). 단 대용 신호 자체가 손실(회전율 과다)이라 운영 전략 신호 로그로 최종 확정 필요. VT_REGIME_FILTER=1 유지. 근거: research_outputs/regime_filter_momentum_20260925.md"),
    ("research_portfolio", "pyportfolioopt_hrp_minvol_vs_equal_20260925",
     "HRP/최소분산 비중이 동일비중보다 낫다(월간 20종목)",
     "rejected_equal_weight_better",
     "동일비중 CAGR 7.0%/Sharpe 0.41 vs HRP 3.8%/0.28, MinVol 0.8%/0.14, MDD 개선 없음. 근거: pyportfolioopt_sidebyside_20260924.md"),
    ("data_quality", "snapshot_survivorship_bias_20260925",
     "스냅샷 유니버스에 폐지 종목이 포함돼 있다",
     "defect_found_and_fixed_in_generator",
     "폐지·합병 보통주 549개가 security_type='listed_equity'인데 생성기 필터가 이를 제외해 폐지 종목이 전부 누락(생존편향). 필터 수정 후 v4 테이블(+205종목, 10x_24m 0.82%→0.91%). 운영 소비처 정본 교체는 별도 승인 대기"),
    ("data_quality", "snapshot_label_raw_overcount_20260925",
     "원본(raw) 종가 기준 라벨이 기업행위(역분할·감자)의 가짜 상승을 성공으로 센다",
     "defect_confirmed",
     "raw 10x_24m 1,474(1.23%) → 기업행위 근거 있는 것만 마스크한 조정 라벨 980(0.82%)로 ~34% 과대계상(전체 4분류 마스크 813은 실제 급등까지 지움). 변경 행의 96%가 창 안에 마스크 이벤트 보유"),
]


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=120)
    have = {r[0] for r in conn.execute("SELECT experiment_name FROM signal_experiment_ledger").fetchall()}
    new = [r for r in ROWS if r[1] not in have]
    print({"to_insert": len(new), "already": len(ROWS) - len(new), "apply": apply})
    if not apply or not new:
        return
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany("""INSERT INTO signal_experiment_ledger(strategy_key,experiment_name,hypothesis,verdict,detail,tested_at)
                        VALUES(?,?,?,?,?,?)""", [(a, b, c, d, e, now) for a, b, c, d, e in new])
    conn.commit()
    print("inserted", len(new))


if __name__ == "__main__":
    main("--apply" in sys.argv)
