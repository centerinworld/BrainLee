"""
recovery.py -- run_backtest_recovery()
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

import backtest_common as _bc
from price_integrity import research_price_issues
from backtest_common import (
    DELISTING_CONFIRM_DAYS,
    QUALITY_DAY_CLASSES,
    is_excluded_day,
    last_tradable_day_before_break,
    last_tradable_index_before,
    load_adjusted_prices,
    _load_jump_aligned_corp_factors,
    _rebase_positions_for_corp_actions,
    SignalEvidenceLedger,
    evidence_item,
    financial_row_evidence,
    DB_PATH,
    _CHART_BOTTOM_MIN,
    _CHART_TOP_MIN,
    _chart_bottom_confluence,
    _chart_prep,
    _chart_top_confluence,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _final_liquidation_quote_for_code,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_recovery(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.12,
    trail: float = -0.20,
    trail_big: float = -0.25,       # 50%+ 이익 시 더 넓은 추적손절
    tp: float = 0.80,               # 80%+ 익절 (3배 노림)
    max_hold: int = 240,
    ma60_depth_min: float = -0.20,  # MA60 -20% 이상 낙폭 (80%로 넓히면 하락장 +49.2%→-0.04% 확인)
    ma60_depth_max: float = -0.65,  # 너무 깊은 낙폭 제외 (상장폐지 위험)
    pct_from_low_max: float = 40.0, # 52주 저점 대비 +40% 이내 (하락장 핵심 — 중간반등은 V-DEEP에서 담당)
    vol_ratio: float = 2.0,         # 거래량 반등 확인 (20일 평균 × 2.0배) — 실증 최적값
    hot_sector_boost: bool = False, # 실험용: 주도섹터(as-of ret20 상위) 보너스 — V-GC와 동일 기법
    turnaround_bonus: float = 20.0, # 직전 공시분기 첫 흑자전환 종목 랭킹 보너스 (2026-07-12 채택: avg6 +25.8→+26.9%, 10~30pt 전 구간 개선 강건)
    buyback_bonus: float = None,    # 최근 60일 자사주 취득공시 종목 랭킹 보너스 — 실측 개선 없음(2026-07-12 기각)
    flow_bonus: float = 20.0,       # 기관+외인 5일 순매수 양수 랭킹 보너스 (2026-07-12 채택: avg6 +26.9→+29.5%, 5/10/20pt 전 구간 개선·30pt는 6/6 붕괴)
    asof_mktcap: bool = True,       # 2026-07-13 기본화: as-of 시총 — 룩어헤드 제거. as-of 실측 avg6 +22.4%(5/6), ablation: 무보너스 +15.1/흑자전환만 +18.0/수급만 +19.3 → 두 보너스 모두 진짜 개선
    avoid_overheat: float = None,   # 실험용: 진입일 40일 수익률 +N(1.0=+100%) 초과 급등주 제외 (V-GC 채택 필터 이식)
    strict_exec: bool = True,       # 2026-07-13 기본화 (Codex 계약 §3-2): D종가 신호 → D+1 시가 체결.
                                    # 검증: same_close avg6 +22.4%(5/6) vs next_open +23.0%(5/6) — 전략 유효성 유지.
                                    # 기간분포는 이동(하락 78.5→42.9 / 최근 11.2→56.3 / 최신 15.5→3.6) — 당일종가 편향이 기간 단위론 유의미했음.
    vol_fade_exit: bool = False,    # 실험용(2026-07-17): 437건 실증 — 중앙값 고점76일, 고점후 평균고점수익+93.9%→최종-3.5%(거의 전부반납).
                                    # 진입 근거였던 거래량반등(20일평균×2.0)이 식으면(진입시 거래량의 40% 이하) 조기청산.
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    fin_health: bool = False,       # 2026-07-18 실험(사용자 가설): as-of TTM 순이익 흑자 종목만 — "재무 멀쩡한데 이벤트로 폭락한" 종목 한정
    turnaround_rev_filter: bool = False,  # 2026-07-18 실험: 흑자전환 확정분기에 매출YoY>0 조건 추가(리딩시그널 연구 결과 반영)
    avoid_dilution_risk: int = None,  # 2026-07-20 실험: 진입일 트레일링365일 CB/BW/EB/RIGHTS 공시 N건+ 종목 제외
                                       # (turnaround-watch 실증: 4건+ 구간 TTM흑자전환율 lift 0.90x/0.64x,
                                       # 12개월 forward -30%↓ 비율 29%→49.3% — 젬백스형 희석스파이럴 배제 목적)
    ta_score_bonus: float = None,  # 2026-07-20 실험: 종합턴어라운드스코어(0~3, 재도전+매출성장+이익의질)
                                    # 점당 랭킹 보너스 — routes/tenbagger.py comprehensive_score와 동일 정의.
                                    # walk-forward: 0점 lift 0.52~0.59x/1점 0.79~0.91x/2점 1.13~1.23x/
                                    # 3점 1.38x(학습)/1.63x(검증) 단조증가 확인됨(turnaround-watch 탭).
    max_credit_ratio: float = None,  # 2026-08-27 실험: kiwoom_credit_balance.credit_ratio(신용잔고비율) 제외필터.
                                    # 낙폭과대 population(52주고점대비-30%↓, 시총300+) walk-forward 검증:
                                    # 신용잔고<1% lift 1.21x(학습)/1.11x(검증) > 1~3% 1.15x/1.05x > 3%+ 0.91x/0.94x
                                    # — 단조·학습검증 방향일치(signal_experiment_ledger 확인). None=미적용(기본값),
                                    # 실전 반영 전 실행 백테스트(A/B) 검증 필요.
    adjusted_prices: bool = None,   # W5b(REVIEW_PLAN §32-3): None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    낙폭과대 반등 전략 (V-RECOVERY).

    [데이터 기반 핵심 로직]
    실증 데이터(2020-2025, 370만 거래일): MA60 대비 낙폭이 깊을수록 수익률↑
      -20~-25%: 평균 +43.6%  |  -25~-35%: +56.3%  |  -35~-45%: +73.9%  |  -45%↓: +103.5%
    pct_from_low_max=40 유지 이유: 80으로 넓히면 하락장+49.2%→-0.04% 확인(저점근방이 하락장 방어 핵심)
    중간반등 포착은 V-DEEP 전략에서 담당.

    A) 현재가 < MA60 × (1 + ma60_depth_min)  [MA60 대비 깊은 낙폭, ≥-20%]
    B) 현재가 < MA60 × (1 + ma60_depth_max)  [너무 깊으면 제외, ≤-65%]
    C) 52주 저점 대비 pct_from_low_max% 이내  [저점 근방 집중]
    D) 최근 5일 거래량 > 20일 평균 × vol_ratio [반등 신호]
    E) 최근 3일 가격 상승 (바닥 확인)
    F) 최근 5일 종가 > 10일 전 저점 (회복 시작)
    G) 시총 200억+ (불량기업 제외) — stock_universe 기반
    H) KOSPI MA120 붕괴 심각하지 않을 때만 (MA120 × 0.85 이상)

    매도:
    - Trail -20% (이익 달성 후), Trail -25% (50%+ 이익 시)
    - 손절 -12%
    - 최대 보유 240일
    """
    init_backtest_db()
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    # 조정 모드(deep_recovery W5b-1과 같은 규칙): 신호·손익 = 조정 시계열(보유 재기준 대신), 최저가·시총 = 원주가,
    # 유니버스 = 기간 중 상장(폐지 포함) PIT 마스터, '분할/합병 필터'·'90행 이상'(기간 전체 = 미래 정보) 미사용.
    if adjusted_prices and not strict_exec:
        raise ValueError("adjusted_prices는 strict_exec(다음날 시가 체결)에서만 지원")
    if adjusted_prices:
        asof_mktcap = True
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'stocks_with_breaks': 0, 'share_unknown_breaks': 0,
                 'delisted_exits': 0, 'unevaluable_delistings': 0, 'delisted_entries': 0, 'pit_mktcap_missing': 0}
    run_name = run_name or f"V-RECOVERY낙폭반등 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "recovery", "rec_v3_flow_ta_20260713",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
         "pct_from_low_max": pct_from_low_max, "vol_ratio": vol_ratio,
         "turnaround_bonus": turnaround_bonus, "flow_bonus": flow_bonus,
         "avoid_dilution_risk": avoid_dilution_risk, "ta_score_bonus": ta_score_bonus,
         "max_credit_ratio": max_credit_ratio,
         "asof_mktcap": asof_mktcap, "avoid_overheat": avoid_overheat,
         "strict_exec": strict_exec, "chart_confluence": chart_confluence,
         "fin_health": fin_health,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date,
         "adjusted_prices": True if adjusted_prices else None},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
        **({"universe_version": "security_master_history_v3_pit_delisted"} if adjusted_prices else {}),
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'recovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        # KOSPI (시장 필터용)
        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        # 종목 로드 (시총 200억+, KOSPI/KOSDAQ) — 2026-08-12: SQLite CASE WHEN 정수
        # 트릭이 PostgreSQL boolean 타입과 불일치해 오류 발생하던 것을 Python측
        # 조건분기로 교체(기존 로직과 완전히 동일하게 동작, PostgreSQL 호환성만 수정)
        _rec_mktcap_min = 0 if asof_mktcap else 200
        codes = conn.execute("""
            SELECT DISTINCT p.stock_code, su.market_cap
            FROM price_history p
            JOIN stock_universe su ON p.stock_code=su.stock_code
            WHERE p.date BETWEEN ? AND ? AND p.close>0
              AND su.market_cap >= ?
              AND su.market IN ('KOSPI','KOSDAQ')
              AND LENGTH(p.stock_code)=6
              AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """, (start_date, end_date, _rec_mktcap_min)).fetchall()
        if adjusted_prices:
            codes = [(r[0], None) for r in conn.execute("""
                SELECT DISTINCT sm.stock_code FROM security_master_history sm
                WHERE sm.is_tradable=1 AND sm.is_etf_etn=0 AND sm.market IN ('KOSPI','KOSDAQ')
                  AND (sm.security_type IS NULL OR sm.security_type != 'preferred')
                  AND sm.effective_from <= ? AND (sm.effective_to IS NULL OR sm.effective_to > ?)
                  AND LENGTH(sm.stock_code)=6
                ORDER BY sm.stock_code
            """, (end_date, start_date)).fetchall()]

        # 2026-08-12: 발행주식수를 stock_universe.shares_issued(현재값 고정)로 쓰던
        # 것을 security_share_history 기반 정확한 as-of 값으로 교체(공용 패턴, V-EARNINGS/
        # V-MOONSHOT 등이 이미 쓰던 것과 동일) — 표본검증 결과 300종목 중 173개(57.7%)가
        # 2020-03 시점 실제 발행주식수가 현재값과 2%+ 차이(최대 17배, 분할/대규모증자
        # 미반영) — asof_mktcap=True인데도 실제로는 "근사"라 부르기 민망한 수준이었음.
        rec_share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            _rec_codes = [c for c, _ in codes]
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(_rec_codes))), _rec_codes
            ):
                rec_share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_rec(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(rec_share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        if adjusted_prices:
            _codes = sorted({str(c_) for c_, _m in codes})
            _ap = load_adjusted_prices(conn, _codes, warmup_start, end_date)
            _qdays: Dict[str, set] = {}
            for _qc, _qd, _qcls in research_price_issues(conn, _codes, warmup_start, end_date, allow_confirmed_corporate_actions=True):
                if _qcls in QUALITY_DAY_CLASSES:
                    _qdays.setdefault(str(_qc), set()).add(str(_qd)[:10])
            for code in _codes:
                e = _ap.get(code)
                if not e or not e['dates'] or not any(start_date <= d <= end_date for d in e['dates']):
                    continue
                d_list = [str(d)[:10] for d in e['dates']]
                sd[code] = {
                    'd': d_list, 'dates': d_list,
                    'c': list(e['close']), 'v': list(e['volume']), 'volumes': list(e['volume']),
                    'h': list(e['high']), 'lo': list(e['low']), 'o': list(e['open']),
                    'raw_c': list(e['raw_close']), 'f': list(e['adj_factor']),
                    'breaks': list(e['breaks']), 'excluded_ranges': list(e['excluded_ranges']),
                    'disc': e.get('break_disclosed', {}) or {}, 'qdays': _qdays.get(code, set()),
                    'master_closed_to': e.get('master_closed_to'),
                    'mkt_cap_억': 300,
                }
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)
                if chart_confluence:
                    sd[code]['chart'] = _chart_prep(d_list, sd[code]['lo'], sd[code]['c'])
        for code, mktcap in ([] if adjusted_prices else codes):
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v, COALESCE(high,close) AS h, COALESCE(low,close) AS lo,
                       COALESCE(open, close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o': [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}
        if adjusted_prices and sim_dates:
            from datetime import date as _d0
            for code, s_ in sd.items():
                s_['last_tradable_i'] = max((k for k, v_ in enumerate(s_['v']) if v_ > 0), default=None)
                s_['ends_before_period_end'] = s_['d'][-1] < sim_dates[-1]
                _mc = s_.get('master_closed_to')
                try:
                    _gap = abs((_d0.fromisoformat(_mc[:10]) - _d0.fromisoformat(s_['d'][-1][:10])).days) if _mc else None
                except Exception:
                    _gap = None
                s_['delisting_confirmed'] = bool(_gap is not None and _gap <= DELISTING_CONFIRM_DAYS)

        def _pit_mc(code, day_, raw_px):
            """신호일 시총(억원) = 상장주식 수 × 원주가. 주식 수를 모르면 가장 보수적(최대 슬리피지)."""
            sh_ = _shares_asof_rec(code, day_)
            if sh_ > 0:
                return sh_ * raw_px / 1e8
            adj_stats['pit_mktcap_missing'] += 1
            return 1.0

        def _close_now(code, p, i, price, reason, day_):
            """조정 단위 `price`로 즉시 청산(단절·폐지 처리)."""
            nonlocal cash, pending_sells
            pnl_, net_pct_ = _net_profit(p['entry'], price, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl_
            _f = sd[code]['f'][i]
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day_,
                           'entry': p['entry'], 'exit': price, 'pnl_pct': net_pct_,
                           'reason': reason, 'pnl': round(pnl_, 0),
                           'exit_raw': round(price / _f, 4) if _f else price,
                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
            del pos[code]
            pending_sells = [(c_, r_) for c_, r_ in pending_sells if c_ != code]

        # 주도섹터 부스트 (실험용, V-GC와 동일: as-of sector_large 평균 ret20 상위 2섹터)
        rec_sec_of: Dict[str, str] = {}
        if hot_sector_boost and sd:
            for r in conn.execute(
                "SELECT stock_code, sector_large FROM stock_universe "
                "WHERE stock_code IN ({})".format(",".join("?" * len(sd))),
                list(sd.keys()),
            ).fetchall():
                if r[1] and r[1] != '기타':
                    rec_sec_of[r[0]] = r[1]

        def _rec_hot_sectors(day: str) -> dict:
            agg: Dict[str, list] = {}
            for code2, s2 in sd.items():
                sec2 = rec_sec_of.get(code2)
                if not sec2:
                    continue
                i2 = didx[code2].get(day)
                if i2 is None or i2 < 20:
                    continue
                c0, c20 = s2['c'][i2], s2['c'][i2 - 20]
                if c20 > 0:
                    agg.setdefault(sec2, []).append(c0 / c20 - 1)
            avg2 = {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 5}
            top = sorted(avg2.items(), key=lambda x: -x[1])[:2]
            return {s3: (i3 + 1) for i3, (s3, a3) in enumerate(top) if a3 >= 0.03}

        # 흑자전환 보너스: 분기 순이익+매출 + 공시가능일(as-of) 로드 — V-TURNAROUND 검증 조건
        # 2026-07-18: 매출도 함께 로드 — 리딩시그널 연구(scratch/turnaround_leading_signal_research.py)
        # walk-forward 검증 결과 "매출YoY성장"만이 학습/검증 양쪽에서 안정적 lift(1.05~1.08x)를
        # 보인 유일한 후보였음(임원매수·적자축소는 오히려 음의 lift, 마진개선은 부호 불안정).
        ta_fins: Dict[str, list] = {}
        if (turnaround_bonus is not None or fin_health or ta_score_bonus is not None) and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.net_income, f.revenue, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE COALESCE((SELECT a.avail_date FROM fin_disclosure_dates a WHERE a.stock_code=f.stock_code AND a.year=f.year AND a.quarter=4 AND a.is_annual=1), printf('%d-03-31', f.year+1)) END) as avail_date,
                       f.id, f.report_type, f.created_at, f.updated_at
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE NOT EXISTS (SELECT 1 FROM stock_collection_config q WHERE q.stock_code=f.stock_code AND q.config_key='fs_quirk:dart_unit_error' AND q.config_value LIKE '%' || (CASE WHEN f.is_annual=1 THEN CAST(f.year AS TEXT) || 'Y' ELSE CAST(f.year AS TEXT) || 'Q' || CAST(f.quarter AS TEXT) END) || f.report_type || '%') AND f.is_annual=0 AND f.quarter BETWEEN 1 AND 4 AND f.net_income IS NOT NULL
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                # 튜플: (avail_date, net_income, revenue, year, quarter) — 인덱스[0..2]는 기존 코드 호환
                ta_fins.setdefault(r[0], []).append((r[5], r[1], r[2], r[3], r[4], r[6], r[7], r[8], r[9]))

        evidence = SignalEvidenceLedger("recovery", {
            "turnaround_bonus": turnaround_bonus, "turnaround_rev_filter": turnaround_rev_filter,
            "fin_health": fin_health, "ta_score_bonus": ta_score_bonus, "flow_bonus": flow_bonus,
            "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
            "pct_from_low_max": pct_from_low_max, "vol_ratio": vol_ratio,
        })

        def _note_evidence(code: str, day: str) -> None:
            if turnaround_bonus is None and not fin_health and ta_score_bonus is None:
                evidence.note(code, day, [evidence_item("financial_data", None, None,
                                                        source_key="dataset_not_consulted_by_params")])
                return
            # 흑자전환/TTM/종합스코어 판정이 읽는 창: as-of 최근 5개 공시 분기(avail 정렬 그대로)
            avail = [x for x in ta_fins.get(code, ()) if x[0] <= day][-5:]
            items = [financial_row_evidence(
                x[5], code, x[3], x[4], report_type=x[6], role=f"lag{len(avail) - 1 - k}",
                value={"net_income": x[1], "revenue": x[2]}, available_at=x[0],
                collected_at=x[7], modified_at=x[8]) for k, x in enumerate(avail)]
            evidence.note(code, day, items or [evidence_item("financial_data", None, None)])

        def _fin_healthy(code: str, day: str) -> bool:
            """day 시점 공시된 최근 4개 분기 순이익 합(TTM) > 0 — '재무가 나쁘지 않은' 종목 판정."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 4:
                return False
            vals = [x[1] for x in avail[-4:] if x[1] is not None]
            return len(vals) == 4 and sum(vals) > 0

        def _is_turnaround(code: str, day: str) -> bool:
            """day 시점 공시된 최신 분기 첫 흑자전환 (직전 NI>0, 이전 1~3분기 중 NI<0 존재)."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 2:
                return False
            if avail[-1][1] is None or avail[-1][1] <= 0:
                return False
            return any(x[1] is not None and x[1] < 0 for x in avail[-4:-1])

        def _is_turnaround_rev_growth(code: str, day: str) -> bool:
            """2026-07-18 실험: _is_turnaround 조건 + 흑자전환 확정분기 매출 YoY>0
            (리딩시그널 연구에서 유일하게 안정적이었던 매출성장 조건을 확정 흑자전환에 추가 필터로 결합)."""
            fl = ta_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return False
            if avail[-1][1] is None or avail[-1][1] <= 0:
                return False
            if not any(x[1] is not None and x[1] < 0 for x in avail[-4:-1]):
                return False
            rev_now, rev_1y = avail[-1][2], avail[-5][2]
            return bool(rev_now and rev_1y and rev_1y > 0 and rev_now / rev_1y - 1 > 0)

        # 자사주 취득공시 보너스: 종목별 공시일 리스트 (as-of, 취득결정/신탁체결만)
        bb_events: Dict[str, list] = {}
        if buyback_bonus is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, replace(rcept_dt,'.','-') d FROM treasury_buyback
                WHERE event_type IN ('취득결정','acquisition','trust')
                  AND report_nm LIKE '%취득%' AND report_nm NOT LIKE '%해지%' AND report_nm NOT LIKE '%결과%'
                  AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                bb_events.setdefault(r[0], []).append(r[1])

        def _has_recent_buyback(code: str, day: str) -> bool:
            evs = bb_events.get(code)
            if not evs:
                return False
            cutoff = (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=60)).strftime('%Y-%m-%d')
            return any(cutoff <= e <= day for e in evs)

        # 기관+외인 수급 보너스: 종목별 일자→순매수 합 (수량 기준)
        flow_map: Dict[str, Dict[str, float]] = {}
        if flow_bonus is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, date, COALESCE(inst_net_buy,0)+COALESCE(frn_net_buy,0) "
                "FROM price_history WHERE date>=? AND date<=? AND close>0 AND stock_code IN ({})".format(
                    ",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                flow_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _flow_positive(code: str, day: str) -> bool:
            m = flow_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        # 희석위험 이벤트: 종목별 공시일 리스트 (as-of, CB/BW/EB/유상증자만)
        dilution_map: Dict[str, list] = {}
        if avoid_dilution_risk is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, disclosed_at FROM dilution_events
                WHERE event_type IN ('CB','BW','EB','RIGHTS') AND disclosed_at IS NOT NULL
                  AND (risk_event_bucket IS NULL OR risk_event_bucket != 'legacy_non_issuance_event')
                  AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                dilution_map.setdefault(r[0], []).append(r[1])

        def _dilution_risk_count(code: str, day: str) -> int:
            """day 시점 트레일링 365일 내 CB/BW/EB/RIGHTS 공시 건수."""
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=365)).strftime('%Y-%m-%d')
            return sum(1 for e in evs if cutoff <= e <= day)

        # 신용잔고비율 (2026-08-27 실험): 종목별 (date_str, ratio) 정렬 리스트, day 이하 최근값 asof 조회
        credit_map: Dict[str, list] = {}
        if max_credit_ratio is not None and sd:
            import bisect as _bisect
            for r in conn.execute("""
                SELECT stock_code, dt, credit_ratio FROM kiwoom_credit_balance
                WHERE credit_ratio IS NOT NULL AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                d = str(r[1])
                d_iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
                credit_map.setdefault(r[0], []).append((d_iso, float(r[2] or 0)))
            for k in credit_map:
                credit_map[k].sort()

        def _credit_ratio_asof(code: str, day: str):
            """day 시점(이하) 가장 최근 신용잔고비율. 최대 30일 이내만 유효, 없으면 None(판단불가=통과)."""
            rows = credit_map.get(code)
            if not rows:
                return None
            dates = [r[0] for r in rows]
            idx = _bisect.bisect_right(dates, day) - 1
            if idx < 0:
                return None
            found_date, ratio = rows[idx]
            cutoff = (datetime.strptime(day, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')
            if found_date < cutoff:
                return None
            return ratio

        # 종합 턴어라운드 스코어(0~3): 재도전이력 + 매출YoY성장 + 이익의질(감가상각/현금흐름)
        # — routes/tenbagger.py get_turnaround_watch()의 comprehensive_score와 동일 정의를
        # as-of(일별 시뮬레이션 시점 기준)로 재구현.
        cf_map: Dict[tuple, dict] = {}
        if ta_score_bonus is not None and sd:
            for r in conn.execute("""
                SELECT stock_code, year, quarter, report_type, depreciation_q, operating_cf_q
                FROM cash_flow_data WHERE is_annual=0 AND stock_code IN ({})
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                cf_map.setdefault((r[0], r[1], r[2]), {})[r[3]] = (r[4], r[5])

        def _ta_score(code: str, day: str) -> int:
            fl = ta_fins.get(code)
            if not fl:
                return 0
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return 0
            y, q = avail[-1][3], avail[-1][4]
            ni_now = avail[-1][1]
            score = 0
            if any((x[1] or 0) > 0 for x in avail[-5:-1]):  # 재도전: 최근4분기(당분기제외) 흑자 있었음
                score += 1
            rev_now, rev_1y = avail[-1][2], avail[-5][2]
            if rev_1y and rev_1y >= 1e9 and rev_now and rev_now / rev_1y - 1 > 0:  # 매출YoY성장
                score += 1
            variants = cf_map.get((code, y, q))
            if variants and ni_now is not None:  # 이익의질(감가상각주도 또는 영업현금흐름>0)
                dep_q, ocf_q = variants.get("CFS") or next(iter(variants.values()))
                if (dep_q is not None and (ni_now + dep_q) > 0) or (ocf_q is not None and ocf_q > 0):
                    score += 1
            return score

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []

        pending_sells: list = []   # strict_exec: (code, reason) — 익일 시가 체결 대기
        pending_buys: list = []    # strict_exec: code — 익일 시가 체결 대기

        # 2026-09-28: 보유 중 확정 기업행위(권리락·분할 등) 날 포지션을 새 주식 기준으로 재기준
        _ca_factors = {} if adjusted_prices else _load_jump_aligned_corp_factors(conn, list(sd.keys()))
        _ca_prev_day = None
        for day in sim_dates:
            _rebase_positions_for_corp_actions(_ca_factors, pos, _ca_prev_day, day, ('entry', 'peak'), 'shares')
            _ca_prev_day = day
            # ── strict_exec: 전일 신호 주문을 오늘 시가에 체결 (Codex 계약 §3-2) ──
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
                    if adjusted_prices and (sd[code]['v'][i] or 0) <= 0:
                        adj_stats['zero_volume_deferred_sells'] += 1   # 거래 불가능한 날 — 다음 거래일로 이월
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                    if adjusted_prices:
                        _ff = sd[code]['f'][i]
                        trades[-1].update({'exit_raw': round(px / _ff, 4) if _ff else px,
                                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
                pending_sells = _still
                if adjusted_prices:
                    for code, p in list(pos.items()):
                        i = didx[code].get(day)
                        if i is not None and i > 0 and day in sd[code]['breaks']:
                            _k = last_tradable_index_before(sd[code], i)
                            _k = i - 1 if _k is None else _k
                            _close_now(code, p, i, sd[code]['c'][_k], '단절 당일 청산(공시 근거 없음·평가 불가)', day)
                            trades[-1]['evaluation'] = 'unevaluable_break'; trades[-1]['basis_date'] = sd[code]['d'][_k]
                            adj_stats['break_day_liquidations'] += 1
                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue  # 당일 미거래 → 주문 만료
                    px = sd[code]['o'][i]
                    if adjusted_prices:
                        if is_excluded_day(sd[code], day):   # 계수 미확정 단절 제외 구간(단절 당일 포함)에는 체결도 하지 않는다(D12 ②)
                            adj_stats['excluded_fill_cancels'] = adj_stats.get('excluded_fill_cancels', 0) + 1
                            continue
                        if px <= 0 or (sd[code]['v'][i] or 0) <= 0:
                            adj_stats['zero_volume_skipped_buys'] += 1
                            continue
                        _ff = sd[code]['f'][i] or 1.0
                        _rpx = px / _ff
                        if cash < _rpx:
                            continue
                        budget = min(per_stock, cash * 0.99)
                        shares_raw = int(budget // _rpx)
                        if shares_raw < 1:
                            continue
                        cash -= shares_raw * _rpx
                        pos[code] = {
                            'entry': px, 'shares': shares_raw / _ff, 'buy_date': day,
                            'hold': 0, 'peak': px, 'mkt_cap_억': _pit_mc(code, day, _rpx),
                            'entry_raw': round(_rpx, 4), 'shares_raw': shares_raw,
                        }
                        trades.append({'code': code, 'buy_date': day, 'entry': px,
                                       'shares': shares_raw / _ff, 'action': 'buy',
                                       'entry_raw': round(_rpx, 4), 'shares_raw': shares_raw})
                        if sd[code].get('ends_before_period_end'):
                            adj_stats['delisted_entries'] += 1
                        continue
                    if px <= 0 or cash < px:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares < 1:
                        continue
                    cash -= shares * px
                    pos[code] = {
                        'entry': px, 'shares': shares, 'buy_date': day,
                        'hold': 0, 'peak': px,
                        'mkt_cap_억': sd[code].get('mkt_cap_억', 300),
                    }
                    trades.append({'code': code, 'buy_date': day, 'entry': px,
                                   'shares': shares, 'action': 'buy'})
                pending_buys = []

            # ── 매도 체크 ───────────────────────────────────
            to_sell = []
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None: continue
                if adjusted_prices:
                    s_ = sd[code]
                    # 상장폐지·거래종료(N1 ②·§26-2 ②): 자료가 기간 끝 전에 끝나고 오늘이 마지막 거래 가능일이면 그 종가로 청산
                    if s_.get('ends_before_period_end') and s_.get('last_tradable_i') == i:
                        _close_now(code, p, i, s_['c'][i], '상장폐지 청산(delisted)', day)
                        trades[-1]['delisted'] = True
                        if s_.get('delisting_confirmed'):
                            adj_stats['delisted_exits'] += 1
                        else:
                            trades[-1]['evaluation'] = 'unevaluable_delisting'
                            adj_stats['unevaluable_delistings'] += 1
                        continue
                    # 계수 미확정 단절이 공시돼 있고 오늘이 그 전 '거래 가능한 마지막 날'이면 오늘 종가로 사전 청산(D12 ②)
                    if s_['breaks']:
                        _bi = bisect.bisect_right(s_['breaks'], day)
                        if _bi < len(s_['breaks']):
                            _nb = s_['breaks'][_bi]
                            if last_tradable_day_before_break(s_, s_['disc'].get(_nb), _nb) == day:
                                _close_now(code, p, i, s_['c'][i], '단절 전 청산(D12, 공시 후)', day)
                                adj_stats['break_liquidations'] += 1
                                continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue

                entry = p['entry']
                peak  = p.get('peak', entry)
                peak  = max(peak, curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1

                ret = (curr - entry) / entry
                # 추적손절
                if ret >= 0.50:
                    tpct = trail_big
                else:
                    tpct = trail
                trail_cond = (curr - peak) / peak < tpct
                # 손절
                stop_cond = ret < stop
                # 익절
                tp_cond = ret >= tp
                # 만료
                expire_cond = p['hold'] >= max_hold
                # 실험(2026-07-17): 거래량 위축 조기청산 — 진입 근거(거래량 반등)가 식으면 청산
                vol_fade_cond = False
                if vol_fade_exit and p['hold'] >= 15 and i >= 20:
                    v_arr = sd[code]['v']
                    v_now2 = v_arr[i]
                    v_avg20_2 = sum(v_arr[max(0, i-20):i]) / max(1, min(20, i))
                    vol_fade_cond = v_avg20_2 > 0 and v_now2 < v_avg20_2 * 0.5 and ret > 0.03
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈): 이익권(+10%)에서 2/3 합의 시 선제 정리
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN

                if stop_cond or trail_cond or tp_cond or expire_cond or vol_fade_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else
                              'trail' if trail_cond else
                              'tp' if tp_cond else
                              'expire' if expire_cond else
                              'vol_fade' if vol_fade_cond else 'chart_top')
                    to_sell.append((code, curr, ret, reason))

            if strict_exec:
                for code, curr, ret, reason in to_sell:
                    if not pos[code].get('pending_exit'):
                        pos[code]['pending_exit'] = reason
                        pending_sells.append((code, reason))
            else:
                for code, curr, ret, reason in to_sell:
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': curr,
                        'pnl_pct': net_pct, 'reason': reason,
                        'pnl': round(pnl, 0),
                    })

            # ── 매수 체크 ───────────────────────────────────
            if len(pos) >= max_positions:
                continue

            # KOSPI MA120 필터 (완전 패닉장 제외)
            kma120 = _k_ma120(day)
            kospi_ok = True
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None:
                    k_curr = k_prices[ki]
                    if k_curr < kma120 * 0.85:  # KOSPI가 MA120 -15% 이하면 스킵
                        kospi_ok = False
            if not kospi_ok:
                continue

            rec_hot_map = _rec_hot_sectors(day) if hot_sector_boost else {}
            candidates = []
            for code, s in sd.items():
                if code in pos: continue
                i = didx[code].get(day)
                if i is None or i < 80: continue
                c = s['c']
                v = s['v']
                lo = s['lo']
                curr = c[i]
                _rc = s['raw_c'][i] if adjusted_prices else curr   # 최저가·시총은 원주가 기준
                if _rc < 500: continue  # 최소 주가
                if adjusted_prices:
                    if is_excluded_day(s, day):
                        adj_stats['candidate_skips_excluded'] += 1; continue
                    if day in s['qdays']:
                        adj_stats['candidate_skips_quality_day'] += 1; continue
                if asof_mktcap:
                    sh = _shares_asof_rec(code, day)
                    if sh <= 0 or sh * _rc / 1e8 < 200:
                        continue

                # [F] MA60 계산
                ma60 = sum(c[max(0,i-59):i+1]) / min(60, i+1)
                if ma60 <= 0: continue

                depth = (curr - ma60) / ma60  # 음수 = 하방
                # [A][B] 낙폭 범위 체크
                if depth > ma60_depth_min or depth < ma60_depth_max:
                    continue

                # [C] 52주 저점 대비 위치
                p252 = lo[max(0,i-251):i+1]
                low52 = min(p252) if p252 else curr
                if low52 <= 0: continue
                pct_from_low = (curr - low52) / low52 * 100
                if pct_from_low > pct_from_low_max:
                    continue

                # [D] 거래량 반등 확인
                v_now = v[i]
                v_avg20 = sum(v[max(0,i-20):i]) / max(1, min(20,i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [E] 최근 3일 가격 상승 (바닥 확인: 최근 3일 중 2일 이상 상승)
                if i >= 3:
                    up_days = sum(1 for j in range(i-2, i+1) if j > 0 and c[j] > c[j-1])
                    if up_days < 2:
                        continue

                # [F] 과열 회피 (실험): 40일 +100% 초과 급등(크래시 후 V자 급반등)은 제외
                if avoid_overheat is not None and i >= 40:
                    _c40r = c[i - 40]
                    if _c40r > 0 and (curr / _c40r - 1) > avoid_overheat:
                        continue

                # 복합 점수: 낙폭 깊이(70%) + 저점 반등 위치(30%)
                # 실증: -35~-45% 구간 최강, 저점+30~80% 반등 구간 최강
                depth_score = min(-depth * 100, 50)  # 최대 50점 (depth -0.5 이상 포화)
                # 저점+30~80% 구간에 보너스
                low_bonus = 10.0 if 30 <= pct_from_low <= 80 else (
                    5.0 if pct_from_low < 30 else 0.0)
                score = depth_score * 0.7 + low_bonus
                if turnaround_bonus is not None:
                    _ta_hit = (_is_turnaround_rev_growth(code, day) if turnaround_rev_filter
                              else _is_turnaround(code, day))
                    if _ta_hit:
                        score += turnaround_bonus
                if buyback_bonus is not None and _has_recent_buyback(code, day):
                    score += buyback_bonus
                if flow_bonus is not None and _flow_positive(code, day):
                    score += flow_bonus
                if ta_score_bonus is not None:
                    score += _ta_score(code, day) * ta_score_bonus
                if hot_sector_boost:
                    hr = rec_hot_map.get(rec_sec_of.get(code, ""))
                    if hr == 1:
                        score += 15.0
                    elif hr is not None:
                        score += 5.0
                # 재무건전 필터 (2026-07-18 실험): TTM 흑자 종목만
                if fin_health and not _fin_healthy(code, day):
                    continue
                # 희석위험 제외필터 (2026-07-20 실험): 트레일링365일 CB/BW/EB/RIGHTS N건+ 제외
                if avoid_dilution_risk is not None and _dilution_risk_count(code, day) >= avoid_dilution_risk:
                    continue
                # 신용잔고비율 제외필터 (2026-08-27 실험): 판단불가(None)는 통과시킴(안전)
                if max_credit_ratio is not None:
                    _cr = _credit_ratio_asof(code, day)
                    if _cr is not None and _cr >= max_credit_ratio:
                        continue
                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈): 2/3 합의 미달 시 진입 보류
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            for score, code, curr, i in candidates[:3]:
                if strict_exec:
                    if code not in pos and code not in pending_buys and \
                       len(pos) + len(pending_buys) < max_positions:
                        pending_buys.append(code)
                        _note_evidence(code, day)
                    continue
                if len(pos) >= max_positions: break
                if cash < curr * 100: continue  # 최소 100주 살 돈
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // curr)
                if shares < 1: continue
                cost = shares * curr
                cash -= cost
                pos[code] = {
                    'entry': curr, 'shares': shares, 'buy_date': day,
                    'hold': 0, 'peak': curr,
                    'mkt_cap_억': sd[code].get('mkt_cap_억', 300),
                }
                trades.append({
                    'code': code, 'buy_date': day, 'entry': curr,
                    'shares': shares, 'action': 'buy',
                })

        # ── 최종 청산 ──
        sell_trades = [t for t in trades if 'sell_date' in t]
        last_date = sim_dates[-1] if sim_dates else end_date
        for code, p in pos.items():
            idx_map = {day: idx for idx, day in enumerate(sd.get(code, {}).get('d', []))}
            last_price, final_reason = _final_liquidation_quote_for_code(conn, code, 
                last_date, idx_map, sd.get(code, {}).get('c', []))
            pnl, net_pct = _net_profit(p['entry'], last_price, p['shares'], p.get('mkt_cap_억', 300))
            sell_trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_date,
                'entry': p['entry'], 'exit': last_price,
                'pnl_pct': net_pct, 'reason': final_reason,
                'pnl': round(pnl, 0),
            })
            cash += p['shares'] * p['entry'] + pnl

        init_cap = per_stock * max_positions
        portfolio_return = (cash - init_cap) / init_cap * 100
        win_rate = (sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0) /
                    max(1, len(sell_trades)) * 100)
        avg_trade = (sum(t.get('pnl_pct', 0) for t in sell_trades) /
                     max(1, len(sell_trades)))
        summary = (f"[V-RECOVERY] {start_date[:7]}~{end_date[:7]} "
                   f"수익률={portfolio_return:+.1f}% 승률={win_rate:.0f}% "
                   f"거래={len(sell_trades)}건 avg={avg_trade:+.1f}%"
                   + (f"\n조정가격(W5b): {json.dumps(adj_stats, ensure_ascii=False)}" if adjusted_prices else ""))
        print(summary)

        conn.execute("""
            UPDATE backtest_runs SET status='done', summary_text=?, total_return_pct=?,
              win_rate=?, total_trades=?, profit_trades=?, trades_json=?
            WHERE run_id=?
        """, (
            summary, round(portfolio_return, 2), round(win_rate, 1),
            len(sell_trades), sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0),
            json.dumps({'trades': sell_trades, 'avg_trade_return_pct': round(avg_trade, 2),
                        'portfolio_return_pct': round(portfolio_return, 2)}, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, init_cap, cash)
        evidence.persist(run_id, sell_trades)
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


# ─── V13 고수익 집중 백테스트 ──────────────────────────────────────────────


