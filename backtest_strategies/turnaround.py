"""
turnaround.py -- run_backtest_turnaround()
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
    _CHART_BOTTOM_MIN,
    _CHART_TOP_MIN,
    _chart_bottom_confluence,
    _chart_prep,
    _chart_top_confluence,
    _corp_action_adjusted_entry,
    _load_corp_action_factors,
    _load_disc_dates,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_turnaround(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.13,            # v3: -0.13 최적화 (avg5=20.6%, ALL기간 양수)
    trail: float = -0.25,           # 기본 추적손절
    trail_big: float = -0.30,       # 50%+ 이익 시 더 넓게
    tp: float = 999.0,              # 익절 없음 — 흑자전환 후 폭발적 상승 기대
    max_hold: int = 300,
    hi52_drop_min: float = -0.30,   # 52주 고가 대비 최소 낙폭 (BQ: 70.5% 출발점)
    hi52_drop_max: float = -0.65,   # 너무 깊으면 상장폐지 위험
    max_pbr: float = 1.5,           # PBR 저평가 필터
    min_mktcap: int = 200,          # 시총 200억+ (억원 단위)
    vol_ratio: float = 1.3,         # 거래량 확인 (관심 시작 신호)
    strict_exec: bool = True,       # 2026-07-13 (Codex 계약): D종가 신호 → D+1 시가 체결
    asof_mktcap: bool = True,       # 2026-07-17 기본화: 시총 필터를 as-of(security_master_history)로 적용
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    run_name: str = None,
    run_id: str = None,
    data_asof_ts: str = None,  # 2026-09-04: 기업행위조정계수/재무데이터 재현성 고정 — 아래 참조
) -> str:
    """
    흑자전환 특화 전략 (V-TURNAROUND).

    data_asof_ts: corporate_action_events.adjustment_status는 매일 00:10 "기업행위
    조정계수후속확정" 잡이 계속 review_required→factor_confirmed로 승격시키고,
    financial_data도 매일 00:05 "데이터무결성후속검증" 잡이 DART 원문대조로 값을
    UPDATE한다. 이 전략은 진입가 보정(_corp_action_adjusted_entry)이 현금원장에
    직접 반영되고, 흑자전환 판정(_get_turnaround)도 분기 순이익 문턱값에 좌우돼
    두 값 중 하나만 실행 시점 사이에 바뀌어도 거래건수·손익이 흔들린다(회귀검증
    비재현성의 실제 원인). 'YYYY-MM-DD HH:MM:SS'를 주면 그 시각 기준 데이터로
    고정해 재실행해도 항상 동일한 결과를 보장한다. None(기본값)이면 기존과 동일.

    [데이터 기반 핵심 인사이트] BigQuery 1,324종목 전수 분석:
      흑자전환 종목 평균 수익률: 6.14x (우량성장주 3.48x의 1.77배!)
      40.5%가 매수 시점에 적자 상태 → 흑자전환 시 대형 모멘텀 발생

    [진입 조건] v2 (2026-07-04 TTM 필터)
    A) 흑자전환: 최근 공시 분기 NI > 0 + 직전 3분기 중 1분기 이상 NI < 0
       + TTM(최근 4분기 NI 합계) > 0 (임시 반등 아닌 실질 흑자전환)
    B) 낙폭과대: 52주 고가 대비 -30~-65% (BQ 실증: 이 구간에서 70.5% 출발)
    C) PBR < 1.5 (저평가 — 이미 가격에 부정적 기대 반영)
    D) 거래량 > 20일 평균 × 1.3 (관심 증가 확인)
    E) 시총 200억+ (불량기업 제외)
    F) KOSPI MA120 × 0.85 이상 (완전 패닉장 제외 — 흑자전환은 패닉장 제외 허용)

    [매도 조건]
    - Trail -25% (이익 발생 후)  /  Trail -30% (50%+ 이익 시 더 넓게)
    - 손절 -12% (v2: -15%→-12%, stop-out 빈도 감소)
    - 최대 보유 300일
    """
    init_backtest_db()
    run_name = run_name or f"V-TURNAROUND흑자전환 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "turnaround", "ta_v4_strict_20260714",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "max_hold": max_hold,
         "hi52_drop_min": hi52_drop_min, "hi52_drop_max": hi52_drop_max,
         "max_pbr": max_pbr, "min_mktcap": min_mktcap, "vol_ratio": vol_ratio,
         "strict_exec": strict_exec, "asof_mktcap": asof_mktcap,
         "chart_confluence": chart_confluence,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date, "data_asof_ts": data_asof_ts},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    # 2026-08-23: 확정된 기업행위 조정계수 전량 로드(테이블 전체 ~2천행, 저비용).
    # turnaround 보유기간이 유상증자/감자 등을 가로지를 때 진입가 보정에 사용.
    # 2026-09-04: data_asof_ts로 재현성 고정(파일 상단 docstring 참조).
    _corp_action_factors = _load_corp_action_factors(
        conn,
        [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM corporate_action_events WHERE adjustment_status='factor_confirmed'"
            + (" AND updated_at <= ?" if data_asof_ts else ""),
            ([data_asof_ts] if data_asof_ts else []),
        ).fetchall()],
        data_asof_ts=data_asof_ts,
    )
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'turnaround',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        _load_disc_dates(conn)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=400)).strftime('%Y-%m-%d')

        # KOSPI 시장 필터
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

        def _k_ma60(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 60: return None
            return sum(k_prices[idx-59:idx+1]) / 60

        # 재무 데이터 로드 (분기 + 연간, 공시일 포함)
        # 컬럼: year,quarter,rev,op,eps,bps,equity,net_inc,roe,is_annual,avail_date
        # data_asof_ts 지정 시 그 시각 이후 UPDATE된 행은 제외(재현성 고정용, 2026-09-04).
        fin_all: Dict[str, list] = {}
        for r in conn.execute(f"""
            SELECT f.stock_code, f.year, f.quarter,
                   f.revenue, f.operating_profit, f.eps, f.bps,
                   f.total_equity, f.net_income, f.roe,
                   CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END,
                   COALESCE(d.avail_date,
                     CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                          WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                          WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                          WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                          ELSE printf('%d-02-15', f.year+1) END
                   ) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE ((f.is_annual=0 AND f.quarter BETWEEN 1 AND 3)
               OR (f.is_annual=1))
              {"AND f.updated_at <= ?" if data_asof_ts else ""}
            ORDER BY f.stock_code, f.year, f.quarter
        """, ([data_asof_ts] if data_asof_ts else [])).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])

        # 분기 NI 이력 사전 구성 (종목별, 시간순 정렬)
        # ni_hist[code] = [(avail_date, year, quarter, net_income), ...]  ASC by (year, quarter)
        ni_hist: Dict[str, list] = {}
        for sc, rows in fin_all.items():
            q_rows = []
            for row in rows:
                is_ann = row[9]
                if is_ann:  # 연간 제외 (분기만)
                    continue
                y, q, ni = row[0], row[1], row[7]
                if ni is None:
                    continue
                avail = row[10] if (len(row) > 10 and row[10]) else _release_date(y, q, False)
                q_rows.append((avail, y, q, ni))
            q_rows.sort(key=lambda x: (x[1], x[2]))  # year, quarter 오름차순
            if q_rows:
                ni_hist[sc] = q_rows

        def _get_turnaround(code: str, target_date: str):
            """
            Returns (latest_ni, neg_count) if turnaround detected, else None.
            - latest_ni: 최근 공시 분기 NI (양수)
            - neg_count: 직전 3분기 중 음수 분기 수
            TTM NI 합계 > 0: 실질적 흑자전환 (임시 반등 제외), 시장국면 무관 균일 적용
            """
            rows = ni_hist.get(code)
            if not rows:
                return None
            available = [(y, q, ni) for avail, y, q, ni in rows if avail <= target_date]
            if len(available) < 2:
                return None
            available.sort(key=lambda x: (x[0], x[1]), reverse=True)
            latest_ni = available[0][2]
            if latest_ni is None or latest_ni <= 0:
                return None
            prev_quarters = available[1:4]  # 직전 3분기
            neg_count = sum(1 for _, _, ni in prev_quarters if ni is not None and ni < 0)
            if neg_count == 0:
                return None  # 적자 이력 없음 → 흑자전환 아님
            # TTM NI 합계가 양수여야 함 (실질적 흑자전환, 임시 반등 제외)
            ttm_nis = [ni for _, _, ni in available[:4] if ni is not None]
            if not ttm_nis or sum(ttm_nis) <= 0:
                return None
            return (latest_ni, neg_count)

        # 종목 로드 (시총 200억+(as-of 가능 시 security_master_history), 분할 필터)
        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ?
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _ta_shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(high,close), COALESCE(low,close),
                       COALESCE(open, close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 100: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd':  [r[0]  for r in rows],
                'c':  c_list,
                'v':  [float(r[2]) for r in rows],
                'h':  [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o':  [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # valuation_history 기반 시점별 PBR 로드
        pbr_hist: Dict[str, list[tuple[str, float]]] = {}
        if sd:
            ph = ','.join('?' * len(sd))
            for r in conn.execute(
                f"""
                SELECT stock_code, period_end, pbr
                FROM valuation_history
                WHERE stock_code IN ({ph})
                  AND pbr IS NOT NULL
                  AND pbr > 0
                ORDER BY stock_code, period_end
                """,
                list(sd.keys())
            ).fetchall():
                pbr_hist.setdefault(r[0], []).append((r[1], float(r[2])))

        fallback_pbr_map: Dict[str, float] = {}
        if sd:
            ph = ','.join('?' * len(sd))
            for r in conn.execute(
                f"SELECT stock_code, COALESCE(pbr, 9.9) FROM stock_universe WHERE stock_code IN ({ph})",
                list(sd.keys())
            ).fetchall():
                fallback_pbr_map[r[0]] = float(r[1]) if r[1] is not None else 9.9

        def _pbr_as_of(code: str, target_date: str) -> float:
            hist = pbr_hist.get(code)
            if hist:
                dates = [d for d, _ in hist]
                idx = bisect_right(dates, target_date) - 1
                if idx >= 0:
                    return hist[idx][1]
            return fallback_pbr_map.get(code, 9.9)

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        # 흑자전환 감지 캐시 (동일 종목을 매일 재검사 비용 절감)
        # 캐시 키: (code, month) — 같은 달은 동일 결과로 가정
        ta_cache: Dict[tuple, object] = {}

        ta_pending_sells: list = []
        ta_pending_buys: list = []

        for day in sim_dates:
            # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
            if strict_exec:
                _still = []
                for code, reason in ta_pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    entry_adj = _corp_action_adjusted_entry(
                        _corp_action_factors, code, p['buy_date'], day, p['entry'])
                    pnl, net_pct = _net_profit(entry_adj, px, p['shares'], p.get('mkt_cap_억', min_mktcap))
                    cash += p['shares'] * entry_adj + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                ta_pending_sells = _still
                for code in ta_pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
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
                        'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap),
                    }
                    trades.append({'code': code, 'buy_date': day, 'entry': px,
                                   'shares': shares, 'action': 'buy'})
                ta_pending_buys = []

            # ── 매도 체크 ───────────────────────────────────────────
            to_sell = []
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue

                entry = p['entry']
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1

                ret = (curr - entry) / entry
                tpct = trail_big if ret >= 0.50 else trail
                trail_cond  = (curr - peak) / peak < tpct
                stop_cond   = ret < stop
                tp_cond     = ret >= tp
                expire_cond = p['hold'] >= max_hold
                # 손실 타임아웃: 120일 후에도 -8% 이하 손실 지속 → 스토리 미발현
                momentum_timeout = p['hold'] >= 120 and ret < -0.08
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈)
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN

                if stop_cond or trail_cond or tp_cond or expire_cond or momentum_timeout or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond
                              else 'tp' if tp_cond else
                              'momentum_timeout' if momentum_timeout else
                              'chart_top' if chart_top_cond else 'expire')
                    to_sell.append((code, curr, ret, reason))

            if strict_exec:
                for code, curr, ret, reason in to_sell:
                    if not pos[code].get('pending_exit'):
                        pos[code]['pending_exit'] = reason
                        ta_pending_sells.append((code, reason))
            else:
                for code, curr, ret, reason in to_sell:
                    p = pos.pop(code)
                    entry_adj = _corp_action_adjusted_entry(
                        _corp_action_factors, code, p['buy_date'], day, p['entry'])
                    pnl, net_pct = _net_profit(entry_adj, curr, p['shares'], p.get('mkt_cap_억', min_mktcap))
                    cash += p['shares'] * entry_adj + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': curr,
                        'pnl_pct': net_pct, 'reason': reason,
                        'pnl': round(pnl, 0),
                    })

            if len(pos) >= max_positions:
                continue

            # KOSPI 필터 (패닉장 제외 — MA120×0.85, 흑자전환은 패닉장 제외 나머지는 허용)
            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.85:
                    continue

            # ── 매수 후보 탐색 ──────────────────────────────────────
            candidates = []
            day_month = day[:7]  # YYYY-MM

            for code, s in sd.items():
                if code in pos: continue
                i = didx[code].get(day)
                if i is None or i < 90: continue

                c   = s['c']
                v   = s['v']
                h   = s['h']
                curr = c[i]
                if curr < 500: continue

                # [E] 시총 200억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    _sh = _ta_shares_asof(code, day)
                    if _sh <= 0 or _sh * curr / 1e8 < min_mktcap:
                        continue

                # [B] 52주 고가 대비 낙폭 체크
                hi_252 = s['h'][max(0, i-251):i+1]
                high52 = max(hi_252) if hi_252 else curr
                if high52 <= 0: continue
                hi_drop = (curr - high52) / high52  # 음수
                if hi_drop > hi52_drop_min or hi_drop < hi52_drop_max:
                    continue

                # [D] PBR 필터 (과거 시점 valuation_history 우선)
                pbr = _pbr_as_of(code, day)
                if pbr > max_pbr or pbr <= 0:
                    continue

                # [E] 거래량 체크
                v_now  = v[i]
                v_avg20 = sum(v[max(0, i-20):i]) / max(1, min(20, i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [A] 흑자전환 체크 (캐시 활용)
                cache_key = (code, day_month)
                ta_result = ta_cache.get(cache_key, 'MISS')
                if ta_result == 'MISS':
                    ta_result = _get_turnaround(code, day)
                    ta_cache[cache_key] = ta_result
                if ta_result is None:
                    continue

                latest_ni, neg_count = ta_result

                # 복합 점수 산출
                # 낙폭 깊이 (BQ 실증: 깊을수록 좋음)
                depth_score = min(-hi_drop * 100, 55.0)  # 최대 55점
                # 적자 분기 수 보너스 (더 오래 적자일수록 반전 모멘텀 ↑)
                neg_bonus = min(neg_count * 10.0, 30.0)  # 최대 30점 (3분기×10)
                # PBR 저평가 보너스
                pbr_bonus = max(0.0, (max_pbr - pbr) / max_pbr * 15.0)  # 최대 15점
                score = depth_score + neg_bonus + pbr_bonus

                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            for score, code, curr, i in candidates[:3]:
                if strict_exec:
                    if code not in pos and code not in ta_pending_buys and \
                       len(pos) + len(ta_pending_buys) < max_positions:
                        ta_pending_buys.append(code)
                    continue
                if len(pos) >= max_positions: break
                if cash < curr * 100: continue
                budget = min(per_stock, cash * 0.99)
                shares = int(budget // curr)
                if shares < 1: continue
                cost   = shares * curr
                cash  -= cost
                pos[code] = {
                    'entry': curr, 'shares': shares, 'buy_date': day,
                    'hold': 0, 'peak': curr,
                    'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap),
                }
                trades.append({
                    'code': code, 'buy_date': day, 'entry': curr,
                    'shares': shares, 'action': 'buy',
                })

        # ── 최종 청산 ────────────────────────────────────────────────
        sell_trades = [t for t in trades if 'sell_date' in t]
        for code, p in pos.items():
            last_price = sd[code]['c'][-1] if sd.get(code, {}).get('c') else p['entry']
            entry_adj = _corp_action_adjusted_entry(
                _corp_action_factors, code, p['buy_date'], end_date, p['entry'])
            pnl, net_pct = _net_profit(entry_adj, last_price, p['shares'], p.get('mkt_cap_억', min_mktcap))
            sell_trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': end_date,
                'entry': p['entry'], 'exit': last_price,
                'pnl_pct': net_pct, 'reason': 'end',
                'pnl': round(pnl, 0),
            })
            cash += p['shares'] * p['entry'] + pnl

        init_cap = per_stock * max_positions
        portfolio_return = (cash - init_cap) / init_cap * 100
        win_rate = (sum(1 for t in sell_trades if t.get('pnl_pct', 0) > 0) /
                    max(1, len(sell_trades)) * 100)
        avg_trade = (sum(t.get('pnl_pct', 0) for t in sell_trades) /
                     max(1, len(sell_trades)))

        summary = (f"[V-TURNAROUND] {start_date[:7]}~{end_date[:7]} "
                   f"수익률={portfolio_return:+.1f}% 승률={win_rate:.0f}% "
                   f"거래={len(sell_trades)}건 avg={avg_trade:+.1f}%")
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


# ─── V-MEGATREND: 구조적 테마 추종(분산 바스켓 + 손절규율) ────────────────────



