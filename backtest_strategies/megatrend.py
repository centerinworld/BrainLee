"""
megatrend.py -- run_backtest_megatrend()
Split out of backtest.py on 2026-09-03. Pure relocation, no logic changed.
"""
import json
import uuid
import math
import re
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from backtest_common import (
    DB_PATH,
    _CHART_TOP_MIN,
    _chart_prep,
    _chart_top_confluence,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_megatrend(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 30,
    ret6m_min: float = 1.0,       # 6개월 수익률 +100%↑ (2026-07-20 검증: scratch/megatrend_sector_confirm_test.py)
    dist_high_max: float = -0.15,  # 52주 고점 대비 -15% 이내
    stop_loss: float = -0.20,      # 손절 -20% (손실 상한 고정 — 승자 무제한 보유의 전제조건)
    max_hold: int = 252,           # 최대 보유 12개월(거래일 기준) — 검증 시 사용한 forward 윈도우와 동일
    trend_break_ma: int = None,    # 2026-07-20 1차 채택 후 트레일링스탑에 밀려 철회(아래 trail_pct 참조).
                                    # MA120 하향이탈 시 조기청산(이익권 한정) — 시간청산(+77.2%)보다는
                                    # 나았으나(+96.7%) 트레일링스탑(+123.2%)에 못 미쳐 기본값에서 제외.
                                    # None이면 미사용, 값 지정 시 trail_pct와 동시 적용(더 먼저 걸리는 쪽이 발동).
    trail_pct: float = -0.30,      # ★2026-07-20 종합 매도로직 최적화 채택: 고점대비 추적손절(이익권 한정).
                                    # 시간청산/MA이탈/차트컨플루언스/복합조건 12종 전수비교(연속운용
                                    # 2020-03~2026-07-20 기준 총수익률) — trail30 +123.2%(최고) > trail25
                                    # +120.4% > trail35 +117.6% > ma120+trail30 +114.2% > MA200 +99.8% >
                                    # MA120 +96.7% > MA60 +96.2% > 시간청산(원래 기본값) +76.5% > 차트컨플루언스
                                    # 계열 4종 전부 최하위(+59~63%, 승률은 59%로 최고이나 거래빈도 644건으로
                                    # 3배 급증해 승자를 조기 매도 — 추세추종 전략엔 역효과, 신규 확인). 6기간
                                    # 매트릭스도 trail25 avg6=+8.22%(3/6, 트레일 계열 중 최고 일관성)로 개선 확인.
    chart_confluence: bool = False,  # 2026-07-20 실험 후 기각(차트컨플루언스는 추세추종에 역효과, 위 주석 참조).
                                    # 기본 비활성 유지 — 재실험 전 반드시 signal_experiment_ledger 확인.
    universe: str = "all_market",  # ★2026-07-21 채택: 사용자 지시(반도체 외 전력기기/조선/화장품ODM 등
                                    # 2024년 메가랠리도 포착 필요)로 "semiconductor"(151종목 한정)에서
                                    # 확장. 순수 전체시장(2,165종목)은 노이즈 급등 유입으로 오히려 악화
                                    # (연속운용 -6.1%) — sector_filter+sector_confirm_min 조합으로 해결.
    min_mktcap_억: float = 300,     # all_market 유니버스 전용 시총 하한(억원) — 유동성 필터
    sector_confirm_min: int = 3,    # ★2026-07-21 채택: 같은 sector_large 내 동시 조건충족 종목이 3개
                                    # 이상일 때만 매수 — "개별 급등 노이즈"와 "진짜 섹터 재평가"를 구분.
                                    # sector2 +63.9%, sector3 +125.1%(최종채택), sector4 -35.5%(불안정)
                                    # — 3이 안정적 최적점(연속운용 2020-03~2026-07-20, sector_filter 적용시).
    sector_filter: tuple = ("IT", "산업재", "필수소비재"),  # ★2026-07-21 채택: 반도체(IT)+전력기기/
                                    # 조선(산업재)+화장품ODM(필수소비재) — 실제 구조적 메가테마가 확인된
                                    # 3개 섹터로 한정(1,040종목). 순수 전체시장(2,165종목, 필터없음)보다
                                    # 안정적(연속운용 +125.1% vs -6.1%, 6기간 avg +6.68%(3/6) vs 반도체
                                    # 단독 +7.8%(2/6)와 대등하거나 더 일관적). None=전체시장(비권장).
    min_price: float = 50000,      # 2026-07-22 실측 진단+채택: 24.6~25.5구간 99건 중 71건 손절(승률22%)
                                    # 원인 분석 결과 진입가 10만원+ 승률45%/+15.2% vs 10만원미만 승률12~27%/
                                    # -8~-16% — sector_large가 방산·조선(대형주)과 저가 투기성 급등주를
                                    # 구분 못해 동일 섹터로 대량 혼입시킨 것이 확인됨. 6기간 검증: avg6는
                                    # 거의 그대로(6.68%→6.32%)지만 하락장 리스크가 극적으로 감소
                                    # (-35.2%→-7.1%) — 완전한 해결책은 아니고 "다운사이드 방어" 트레이드오프.
    require_earnings_accel: bool = False,  # 2026-07-22 실험: 사용자 제안 — 가격 대신/추가로 매출·영업이익
                                    # YoY 가속 또는 흑자전환(as-of) 요구. se_momentum/V-PEAK와 동일 헬퍼 재사용.
    smart_money_min_score: int = 0,  # 2026-07-22 실험: case-control walk-forward(반도체제외 300%+ 종목 vs
                                    # 대조군, 학습~2022/검증2023~)에서 유일하게 학습·검증 방향이 일치하고
                                    # 검증기에서 더 강화된 신호. 점수=[신용잔고비율<3%]+[기관+외인20일수급 강한매수(10억+)].
                                    # 0(미적용)/1/2로 게이트. 라벨판별력(2점: 학습1.40x/검증2.39x)이 실전
                                    # 백테스트 수익률 개선으로 이어지는지 검증 중 — 기본값 0(미적용).
    exclude_quality_risk: bool = False,  # 2026-07-26 실험: Codex가 신규 구축한 inventory_sales_signals/
                                    # cash_conversion_signals(재고build_up·현금전환불량 risk_score>=4)를
                                    # PIT 제외필터로 적용. Codex 자체 event-study에서 exclude_quality_risk는
                                    # 소폭 개선(avg12 +73.68 vs 기준 +73.33, PF 339.85 vs 288.20)이었으나,
                                    # 이 신호를 "랭킹 가점"으로 쓴 monthly_top20 실행백테스트는 대폭 악화
                                    # (Overlay Top10 +30.38% vs Model Top10 +173.85%, 라벨스윕이 실현가능
                                    # 수익률을 과대평가) — 랭킹가점이 아닌 "이미 선정된 진입신호를 거부만
                                    # 하는" 순수 제외필터로 V-MEGATREND에서 별도 검증 필요(기본값 False,
                                    # scratch/megatrend_quality_risk_exclude_test_20260726.py 참조).
    asof_mktcap: bool = True,      # 2026-07-27: universe="all_market"일 때 min_mktcap_억 유니버스 필터가
                                    # stock_universe.market_cap(현재시총) 정적 컷오프였음(진짜 룩어헤드) —
                                    # security_master_history/security_share_history 기반 as-of 시총으로
                                    # 전환. universe="semiconductor"(hand-curated 테이블)에는 영향 없음.
    market_regime_gate_min: float = None,  # 2026-08-23 실험: signal_experiment_ledger id=72~73과
                                    # 동일한 신호(신용잔고비율<3%+기관외인20일동반매수 종목비중)를
                                    # 종목별 필터(id=73, V-MEGATREND 자체게이트로 실패 — 오버샘플링
                                    # 고갈, 5/6구간 거래0건)가 아니라 "시장 전체" 일별 레짐 점수로
                                    # 재구성(quant_market_regime_signal.regime_score, rolling
                                    # z-score 합산, scratch/build_credit_flow_regime.py). 종목 풀을
                                    # 줄이지 않고 레짐이 나쁜 날에만 신규매수 전체를 하루 건너뛰는
                                    # V10 bear_gate와 동일 구조. None=미적용(기본값, 라벨검증은
                                    # scratch/validate_regime_score_forward_return.py — 학습기
                                    # 상관계수0.40/검증기0.19, 3분위 전부 학습·검증 방향 일치).
                                    # ⚠️ 데이터 소스(kiwoom_credit_balance)가 2026-07-07 이후 갱신
                                    # 중단(레거시 테이블, margin_balance_daily로 대체됨) — 이 파라미터는
                                    # 연구/백테스트 전용이며 라이브 게이트로 쓰려면 소스 교체 필요.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-MEGATREND — 구조적 테마(반도체/전력기기/조선/화장품ODM 등) 추종 전략.

    [배경] 2026-07-20 사용자 지시: V-GC/V-SECTOR가 전공정장비·전력기기 메가랠리를
    트레일링손절(-20~25%)·섹터점수하락 즉시청산 때문에 대부분 놓쳤음이 실거래 검증으로
    확인됨(원익IPS/피에스케이/테스/유진테크 대부분 미보유, 심텍은 -12%손절 직후 +569%
    폭등). "몇 년짜리 구조적 테마를 끝까지 타는" 전략이 없다는 공백을 메우기 위해 신규 설계.
    2026-07-21 사용자 지시로 반도체(151종목) 한정에서 확장: 전력기기/조선/화장품ODM 등
    타업종 메가랠리(2024년 HD현대일렉트릭+861%, 효성중공업+1538%, 2025년 한국콜마/코스메카코리아
    ODM랠리)도 포착 필요. 순수 전체시장(2,165종목) 확장은 노이즈 급등 유입으로 오히려 악화
    (연속운용 -6.1%) — sector_filter(IT+산업재+필수소비재)+sector_confirm_min(3)으로 해결,
    반도체 단독과 대등하거나 더 나은 성과(+125.1%) 확인.

    [데이터 기반 설계] scratch/megatrend_sector_confirm_test.py,
    scratch/megatrend_fundamental_confirm_test.py (walk-forward, n=232건,
    2020-06~2025-06 월별체크포인트, 반도체밸류스트림 151종목 기준 최초 검증):
    - 개별 종목 승률은 19~31%로 낮음(섹터동반강세·매출/영업이익YoY가속 추가해도 무개선,
      기존 avoid_overheat 근거와 일치 — "이미 급등"만으로 다음 승자를 못 고름).
    - 단, 손실을 -20%로 고정하고 승자를 트레일링스탑(-30%)으로 최대한 오래 보유하면
      건당 기대값이 플러스로 재현 — "몇 개 대박이 다수의 소손실을 상쇄" 전형적 fat-tail 구조.
      개별종목 확신이 아니라 분산 바스켓+엄격한 손절 규율이 핵심 전제.

    매수: sector_filter 섹터(기본 IT/산업재/필수소비재) 중 6개월 수익률≥ret6m_min &
         52주고점대비≥dist_high_max & 같은 섹터 내 동시충족 종목≥sector_confirm_min
    매도: 손실≤stop_loss(하드손절) 또는 고점대비 추적손절 trail_pct(기본 -30%, 이익권 한정)
         또는 보유기간≥max_hold(안전망) — 승자를 중간 조정에 흔들리지 않고 최대한 오래 보유.
    """
    init_backtest_db()
    run_name = run_name or f"V-MEGATREND {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _asof_active = bool(asof_mktcap and universe == "all_market")
    _record_run_spec(
        run_id, "megatrend", "megatrend_v4_multisector_20260721",
        {"ret6m_min": ret6m_min, "dist_high_max": dist_high_max, "stop_loss": stop_loss,
         "max_hold": max_hold, "trend_break_ma": trend_break_ma, "trail_pct": trail_pct,
         "chart_confluence": chart_confluence, "max_positions": max_positions,
         "universe": universe, "min_mktcap_억": min_mktcap_억, "asof_mktcap": asof_mktcap,
         "exclude_quality_risk": exclude_quality_risk, "market_regime_gate_min": market_regime_gate_min,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if _asof_active else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'megatrend',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')

        share_intervals: Dict[str, list] = {}
        def _shares_asof_mt(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        if universe == "all_market":
            # 2026-07-21: 전체 KOSPI/KOSDAQ 보통주(우선주 제외 — collect_dart_cashflow_batch.py에서
            # 발견한 것과 동일한 우선주 명명패턴 필터 재사용), 시총 min_mktcap_억 이상.
            # 2026-07-27: 시총 컷오프는 진짜 룩어헤드였음(현재 market_cap으로 소급 편입) — 종목
            # 유니버스 자체는 시총 무관하게 넓게 잡고(섹터만 필터), 실제 min_mktcap_억 임계값은
            # 아래 매수후보 스캔 루프에서 _shares_asof_mt()로 진입일 as-of 시총으로 매일 재확인.
            _pref_pat = re.compile(r"\d?우[A-Z]?$")
            _sector_clause = ""
            _sector_params: list = []
            if sector_filter:
                _sector_clause = " AND sector_large IN ({})".format(",".join("?" * len(sector_filter)))
                _sector_params = list(sector_filter)
            _mktcap_gate = "" if _asof_active else "AND COALESCE(market_cap, 0) >= ?"
            _mktcap_param = [] if _asof_active else [min_mktcap_억]
            all_rows = conn.execute(f"""
                SELECT stock_code, stock_name, market_cap FROM stock_universe
                WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
                  {_mktcap_gate}
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  {_sector_clause}
            """, _mktcap_param + _sector_params).fetchall()
            codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
            mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
            sector_map: Dict[str, str] = {}
            if sector_confirm_min is not None:
                sector_map = {r[0]: r[1] for r in conn.execute(
                    "SELECT stock_code, sector_large FROM stock_universe WHERE stock_code IN ({})".format(
                        ",".join("?" * len(codes))), codes).fetchall()}
            if _asof_active:
                for code, effective_from, effective_to, shares, quality in conn.execute(
                    """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                       FROM security_share_history WHERE stock_code IN ({})
                       ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
                ):
                    share_intervals.setdefault(code, []).append(
                        (effective_from, effective_to, float(shares or 0), quality)
                    )
        else:
            sector_map = {}
            codes = [r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM semiconductor_valuestream WHERE stock_code IS NOT NULL"
            ).fetchall()]
            mktcap_map = {r[0]: (r[1] or 300) for r in conn.execute(
                "SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({})".format(
                    ",".join("?" * len(codes))), codes).fetchall()} if codes else {}

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o,
                       COALESCE(high, close) AS h, COALESCE(low, close) AS lo
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            d_list = [str(r[0])[:10] for r in rows]  # 드물게 섞인 타임스탬프 오염 방어(_chart_prep strptime 대비)
            lo_list = [float(r[4]) if r[4] and r[4] > 0 else c_list[idx] for idx, r in enumerate(rows)]
            sd[code] = {
                'd': d_list,
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'h': [float(r[3]) if r[3] and r[3] > 0 else c_list[idx] for idx, r in enumerate(rows)],
                'lo': lo_list,
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(d_list, lo_list, c_list)

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        regime_score_dates: list = []
        regime_score_vals: list = []
        if market_regime_gate_min is not None:
            _rg_rows = conn.execute(
                "SELECT trade_date, regime_score FROM quant_market_regime_signal "
                "WHERE regime_score IS NOT NULL ORDER BY trade_date"
            ).fetchall()
            regime_score_dates = [r[0] for r in _rg_rows]
            regime_score_vals = [r[1] for r in _rg_rows]

        def _regime_gate_ok(day: str) -> bool:
            """market_regime_gate_min 미만이면 그날 신규매수 전체를 건너뜀(종목풀 축소 아님).
            데이터가 없는 날(과거 구간 밖·소스 미갱신)은 판단불가로 통과시킨다."""
            if market_regime_gate_min is None or not regime_score_dates:
                return True
            idx = bisect.bisect_right(regime_score_dates, day) - 1
            if idx < 0:
                return True
            return regime_score_vals[idx] >= market_regime_gate_min
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        earn_fins: Dict[str, list] = {}
        if require_earnings_accel and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.net_income, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_fins.setdefault(r[0], []).append(
                    (r[6], r[1], r[2], r[3], r[4], r[5]))

        def _earnings_accel_ok(code: str, day: str) -> bool:
            fl = earn_fins.get(code)
            if not fl: return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5: return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            if ni_now is not None and ni_now > 0:
                if any(x[3] is not None and x[3] < 0 for x in avail[-4:-1]):
                    return True
            return False

        # 2026-07-22: 신용잔고비율+기관외인수급 결합 스코어 (case-control walk-forward 검증됨)
        credit_hist: Dict[str, list] = {}
        flow_hist: Dict[str, list] = {}
        if smart_money_min_score > 0 and sd:
            for r in conn.execute("""
                SELECT stock_code, dt, credit_ratio FROM kiwoom_credit_balance
                WHERE stock_code IN ({}) AND credit_ratio IS NOT NULL
                ORDER BY stock_code, dt
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                credit_hist.setdefault(r[0], []).append((str(r[1])[:10], r[2]))
            for code in sd:
                flow_hist[code] = conn.execute("""
                    SELECT date, COALESCE(inst_net_buy_amt,0), COALESCE(frn_net_buy_amt,0)
                    FROM price_history WHERE stock_code=? ORDER BY date
                """, (code,)).fetchall()

        def _smart_money_score(code: str, day: str) -> int:
            score = 0
            cl = credit_hist.get(code)
            if cl:
                avail = [x for x in cl if x[0] <= day]
                if avail and avail[-1][1] is not None and avail[-1][1] < 3:
                    score += 1
            fl = flow_hist.get(code)
            if fl:
                idx = [i for i, r in enumerate(fl) if r[0] <= day]
                if idx:
                    i = idx[-1]
                    window = fl[max(0, i-19):i+1]
                    if sum(r[1] + r[2] for r in window) >= 1000:
                        score += 1
            return score

        # 2026-07-26: Codex 신규 재고/현금전환 품질리스크 PIT 제외필터 (분기말+60일 지연,
        # Codex의 research_new_quality_factor_validation.py와 동일한 가용시점 계산식 재사용).
        # quality_risk_count(=inventory_risk + cash_risk)와 동일하게 두 테이블을 독립적으로
        # "as-of 최신 1건"만 조회해 OR — 두 신호를 한 리스트에 섞으면 서로 다른 분기 캘린더가
        # 뒤섞여 "직전 2건"류의 근사가 부정확해지므로 반드시 테이블별로 분리 추적한다.
        inv_risk_hist: Dict[str, list] = {}
        cash_risk_hist: Dict[str, list] = {}
        if exclude_quality_risk and sd:
            def _avail_date(year: int, quarter: int) -> str:
                month = quarter * 3
                day = 31 if month in (3, 12) else 30
                base = datetime(int(year), month, day)
                return (base + timedelta(days=60)).strftime("%Y-%m-%d")
            for tbl, dest in (("inventory_sales_signals", inv_risk_hist),
                              ("cash_conversion_signals", cash_risk_hist)):
                for r in conn.execute(f"""
                    SELECT stock_code, fiscal_year, fiscal_quarter, risk_score
                    FROM {tbl} WHERE stock_code IN ({",".join("?" * len(sd))})
                """, list(sd.keys())).fetchall():
                    if r[3] is None:
                        continue
                    dest.setdefault(r[0], []).append((_avail_date(r[1], r[2]), int(r[3]) >= 4))
            for dest in (inv_risk_hist, cash_risk_hist):
                for code in dest:
                    dest[code].sort(key=lambda x: x[0])

        def _latest_risk(hist: Dict[str, list], code: str, day: str) -> bool:
            points = hist.get(code)
            if not points:
                return False
            avail = [x for x in points if x[0] <= day]
            return bool(avail) and avail[-1][1]

        def _quality_risk(code: str, day: str) -> bool:
            return _latest_risk(inv_risk_hist, code, day) or _latest_risk(cash_risk_hist, code, day)

        per_stock = total_capital / max_positions
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                pending_sells = _still
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
                pending_buys = []

            # 매도 체크: 손절 또는 보유기간 만료
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop_loss
                expire_cond = p['hold'] >= max_hold
                trend_break_cond = False
                if trend_break_ma is not None and ret > 0 and i >= trend_break_ma:
                    ma = sum(sd[code]['c'][i - trend_break_ma + 1:i + 1]) / trend_break_ma
                    trend_break_cond = curr < ma
                trail_cond = False
                if trail_pct is not None and ret > 0:
                    trail_cond = (curr - p['peak']) / p['peak'] < trail_pct
                chart_top_cond = False
                if chart_confluence and ret > 0.05 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or expire_cond or trend_break_cond or trail_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'trend_break' if trend_break_cond else
                              'chart_top' if chart_top_cond else 'expire')
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        })
                        pos.pop(code, None)

            # 매수 후보 스캔
            if len(pos) + len(pending_buys) < max_positions and _regime_gate_ok(day):
                candidates = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys:
                        continue
                    i = didx[code].get(day)
                    if i is None or i < 126:
                        continue
                    c_arr = s['c']
                    curr = c_arr[i]
                    if curr <= 0 or curr < min_price:
                        continue
                    if _asof_active:
                        _sh = _shares_asof_mt(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    ret6m = curr / c_arr[i - 126] - 1 if c_arr[i - 126] > 0 else -1
                    if ret6m < ret6m_min:
                        continue
                    hi252 = max(c_arr[max(0, i - 251):i + 1])
                    if hi252 <= 0:
                        continue
                    dist = curr / hi252 - 1
                    if dist < dist_high_max:
                        continue
                    if require_earnings_accel and not _earnings_accel_ok(code, day):
                        continue
                    if smart_money_min_score > 0 and _smart_money_score(code, day) < smart_money_min_score:
                        continue
                    if exclude_quality_risk and _quality_risk(code, day):
                        continue
                    candidates.append((ret6m, code))
                if sector_confirm_min is not None and candidates:
                    sec_count: Dict[str, int] = {}
                    for _, code in candidates:
                        sec = sector_map.get(code)
                        if sec:
                            sec_count[sec] = sec_count.get(sec, 0) + 1
                    candidates = [(r, c) for r, c in candidates
                                  if sector_map.get(c) and sec_count.get(sector_map[c], 0) >= sector_confirm_min]
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_buys)
                if strict_exec:
                    pending_buys.extend(code for _, code in candidates[:slots])
                else:
                    for _, code in candidates[:slots]:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'mkt_cap_억': sd[code]['mkt_cap_억']}

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': 'final', 'pnl': round(pnl, 0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-MEGATREND 구조테마추종(반도체+전력기기+조선+화장품ODM) | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}%")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            __import__('json').dumps(trades), run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=_asof_active)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise




