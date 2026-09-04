"""
extreme_dd_volume.py -- run_backtest_extreme_dd_volume()
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
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_extreme_dd_volume(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.15,
    trail: float = -0.35,
    trail_big: float = -0.40,        # 이익 100%+ 달성 후 Trail 확장 (V13과 동일 철학)
    tp: float = 999.0,               # 익절 없음 — 실제 3배 종목은 훨씬 더 갈 수 있어 Trail로만 청산
    max_hold: int = 365,
    trail_activate_pct: float = 0.10,
    from_high_max: float = -70.0,    # 52주 고점 대비 낙폭 -70% 이하 (S등급 핵심조건)
    vol_ratio_min: float = 1.5,      # 당일거래량/20일평균(당일포함) 1.5배+
    min_tvol5_억: float = 5.0,       # 5일 평균거래대금 5억원+ (유동성)
    min_mktcap_억: float = 300.0,
    max_mktcap_억: float = 0.0,      # 0=상한없음. 2026-08-08 신 실증등급용(S<1000/A<1500/B<3000)
    asof_mktcap: bool = True,
    require_turn_confirm: bool = True,  # 2026-07-18: 반등확인 없이 진입 시 참패(-82.8%) → 기본 활성화
    require_top_exit: bool = True,   # 2026-08-08 신설: 아래 참조. 진입확인과 분리된 별도 플래그.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-EXTREME: 초낙폭+거래량 확인 전략 (2026-07-18 신규).

    [데이터 근거] tenbagger_engine.py 실증등급 체계(2026-07-12, strategy_feature_snapshot
    15.5만행 시기분리 walk-forward 검증, 학습 ≤2022/검증 2023+ 완전 분리):
      S등급(낙폭-70%↓ + 당일거래량 20일평균 1.5배+ + 5일평균거래대금 5억+)
      → 12개월 내 3배 달성률: 학습 24.9% / 검증 22.6% (기준율 6.3~8.2% 대비 3.0~3.6배 lift)
    이 조합은 tenbagger 디스커버리(관심종목 스코어링)에만 쓰이고 실제 매매 백테스트로
    옮겨진 적이 없었음 — 이번이 최초 실전 백테스트 구현.

    진입 조건 (tenbagger_engine._fetch_price_data와 동일 정의로 fidelity 유지):
    A) 52주 고점(최근 250거래일, 당일포함) 대비 -70% 이하 낙폭
    B) 당일거래량 / 20일평균거래량(당일포함) >= 1.5배
    C) 5일평균거래대금(당일포함, 억원) >= 5억
    D) 시총 300억+ (as-of)
    E) KOSPI MA120×0.80 이상 (패닉장 신규진입 제외, V-DEEP과 동일)

    ⚠️ 1차 버전(require_turn_confirm 없음) 실측 결과 avg=-82.8%로 참패(사용자 지적으로 원인규명).
    거래량 급증 조건이 "패닉투매 절정"과 "진짜 바닥 확인"을 구분하지 못해, 계속 하락 중인
    종목을 그대로 매수하는 사례 다수 발견(매수직전 5일 상승일수 0/5인 사례 등). 이에 따라
    F) 터닝포인트 확인(require_turn_confirm=True 기본) 추가:
       F-1) 최근 5일 중 3일 이상 상승(반등 모멘텀 확인, deep_recovery와 동일 관행)
       F-2) 당일이 최근 10일 최저가가 아님(저점이 이미 과거에 찍혔는지 확인, 신규 저점 배제)
    → 거래량 급증 즉시 매수가 아니라 "하락이 멈추고 반등이 확인된 이후"에만 진입.

    매도: Trail-35%(이익10%+ 발동) / Trail-40%(이익100%+) / 손절-15% / 만료365일 / 익절없음
    (V13 고수익집중과 동일 "3배~25배 종목은 Trail로만 청산" 철학 — S등급의 목적 자체가
    희귀한 3배 이벤트 포착이므로 익절 상한을 두면 그 이벤트를 스스로 차단하게 됨)
    """
    init_backtest_db()
    run_name = run_name or f"V-EXTREME초낙폭거래량 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "extreme_dd_volume", "extreme_dd_v1_20260718",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "trail_activate_pct": trail_activate_pct, "from_high_max": from_high_max,
         "vol_ratio_min": vol_ratio_min, "min_tvol5_억": min_tvol5_억,
         "min_mktcap_억": min_mktcap_억, "max_mktcap_억": max_mktcap_억,
         "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap, "require_turn_confirm": require_turn_confirm,
         "require_top_exit": require_top_exit,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'extreme_dd_volume',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=380)).strftime('%Y-%m-%d')

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

        # ── 차트 컨플루언스: module-level 공통 모듈(_chart_*) 위임 (2026-07-18 공통화) ──
        def _bottom_confluence_score(s: dict, i: int) -> int:
            return _chart_bottom_confluence(
                s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i)

        def _top_confluence_score(s: dict, i: int) -> int:
            return _chart_top_confluence(
                s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i)

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
            """, (start_date, end_date, min_mktcap_억)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v,
                       COALESCE(open,close) AS o,
                       COALESCE(high,close) AS h, COALESCE(low,close) AS lo
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터 (전일대비 급격한 불연속 배제)
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            # price_history.date에 타임스탬프가 섞인 오염행이 있어 str[:10] 정규화 필수.
            # (미정규화 시 _chart_prep의 strptime('%Y-%m-%d')이 크래시 — 2026-07-20(6)에
            #  run_backtest_megatrend에서 같은 버그를 고쳤으나 이 엔진에는 남아있었음, 2026-08-08 수정)
            d_list = [str(r[0])[:10] for r in rows]
            sd[code] = {
                'd': d_list,
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }
            # 주봉/월봉 집계 — module-level 공통 헬퍼로 위임 (2026-07-18 공통화)
            sd[code]['chart'] = _chart_prep(d_list, sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                               'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                               'reason': reason, 'pnl': round(pnl, 0)})
                del pending_sells[code]

            marked_equity = cash + sum(
                p['shares'] * sd[code]['c'][didx[code][day]]
                for code, p in pos.items() if day in didx[code]
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill)
                    if shares > 0:
                        cash -= shares * fill
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap_억)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            # 매도 체크 (V13과 동일: 이익권 확장 Trail, 익절 상한 없음 + 고점 컨플루언스 청산)
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue
                entry = p['entry']
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                peak_ret = (peak - entry) / entry
                stop_cond  = ret < stop
                trail_cond = False
                if peak_ret >= trail_activate_pct:
                    tpct = trail_big if peak_ret >= 1.00 else trail
                    trail_cond = (curr - peak) / peak < tpct
                tp_cond    = ret >= tp
                expire_cond = p['hold'] >= max_hold
                # 고점 컨플루언스 청산 (2026-07-18 도입, 2026-08-08 진입확인과 분리).
                # ⚠️ 종전엔 require_turn_confirm 하나가 진입 바닥확인과 이 청산조건을 동시에
                # 켰음 — 진입확인은 검증됐지만(끄면 -82.8%) 이 청산조건은 이 전략에서 독립
                # 검증된 적이 없었다. 실측(2026-08-08): trail_activate_pct=10%만 넘으면
                # 차트신호 2개 합의로 즉시 발동해 승자 206/463건(신A/730일 표본)을 평균
                # 38일·중앙값+16%에서 잘라냄 — 이 신호의 예측지평(24개월)과 청산지평(38일)이
                # 불일치. require_top_exit=False(trail만으로 청산) 대비수치는 아래 __main__
                # 홀드아웃으로 별도 검증(주석에 미검증 수치 기재 금지 원칙).
                top_cond = False
                if require_top_exit and ret >= trail_activate_pct and not trail_cond:
                    top_cond = _top_confluence_score(sd[code], i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or tp_cond or expire_cond or top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'tp' if tp_cond else 'chart_top' if top_cond else 'expire')
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue

            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.80:
                    continue

            candidates = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                i = didx[code].get(day)
                if i is None or i < 60: continue
                c = s['c']
                v = s['v']
                curr = c[i]
                if curr < 500: continue

                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    if sh <= 0 or sh * curr / 1e8 < min_mktcap_억:
                        continue
                    # 2026-08-08: 신 실증등급은 시총 '상한'이 핵심 판별축(작을수록 lift↑).
                    # label_10x_24m 검증 lift — 시총<1000억 10.3x / <1500억 7.6x / <3000억 4.4x.
                    if max_mktcap_억 and sh * curr / 1e8 > max_mktcap_억:
                        continue

                # [A] 52주 고점(최근 250일, 당일포함) 대비 낙폭
                p250 = c[max(0, i-249):i+1]
                high_52w = max(p250) if p250 else curr
                if high_52w <= 0: continue
                from_high_pct = (curr / high_52w - 1) * 100
                if from_high_pct > from_high_max:
                    continue

                # [B] 거래량 20일평균(당일포함) 대비
                v_window20 = v[max(0, i-19):i+1]
                avg_vol20 = sum(v_window20) / len(v_window20) if v_window20 else 0
                v_now = v[i]
                if avg_vol20 <= 0 or v_now < avg_vol20 * vol_ratio_min:
                    continue

                # [C] 5일평균거래대금(당일포함, 억원)
                v_window5 = v[max(0, i-4):i+1]
                avg_vol5 = sum(v_window5) / len(v_window5) if v_window5 else 0
                avg_tvol5_억 = avg_vol5 * curr / 1e8
                if avg_tvol5_억 < min_tvol5_억:
                    continue

                # [D] 바닥 컨플루언스 확인 (2026-07-18 — 사용자 지시: 일봉추세+주봉구조+캔들패턴을
                # 복합 판단. 거래량 급증만으로는 "패닉투매 중"과 "진짜 바닥"을 구분 못함이 실측으로
                # 확인됨(매수직전 5일이 단 하루도 안 쉬고 계속 하락하는 케이스 다수). 아래 3요소
                # (일봉 MA5>MA10 / 주봉 higher-low / 캔들 반전패턴) 중 최소 2개 이상 합의 요구.
                conf_score = 0
                if require_turn_confirm:
                    conf_score = _bottom_confluence_score(s, i)
                    if conf_score < _CHART_BOTTOM_MIN:
                        continue

                # 낙폭이 깊을수록, 거래량 확인이 강할수록, 컨플루언스가 강할수록 우선 순위
                score = min(-from_high_pct, 95) + min(v_now / max(avg_vol20, 1), 5) * 2 + conf_score * 5
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)
            available = max(0, position_limit - len(pos) - len(pending_buys))
            pending_buys.extend(code for _, code, _, _ in candidates[:min(3, available)])

        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', min_mktcap_억))
                final_val += p['shares'] * p['entry'] + pnl
                trades.append({
                    'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                    'entry': p['entry'], 'exit': last_c,
                    'pnl_pct': net_pct, 'reason': 'final',
                    'pnl': round(pnl, 0),
                })

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?,
                trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2),
              round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식·복리 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
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




