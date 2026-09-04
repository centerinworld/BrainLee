"""
deep_recovery.py -- run_backtest_deep_recovery()
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

def run_backtest_deep_recovery(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.13,
    trail: float = -0.22,
    trail_big: float = -0.30,
    tp: float = 1.00,               # 100%+ 익절 (2배 노림)
    max_hold: int = 300,
    ma60_depth_min: float = -0.25,  # MA60 -25% 이상 낙폭 (데이터 최강구간 시작)
    ma60_depth_max: float = -0.60,  # -60% 이하는 상폐 위험
    pct_from_low_min: float = 10.0, # 저점에서 최소 +10% 반등 확인
    pct_from_low_max: float = 100.0,# 저점 대비 +100% 이내
    vol_ratio: float = 1.5,         # 거래량 1.5x+ (완화)
    asof_mktcap: bool = False,      # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: deep_recovery/no_new_signal)
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DEEP: 깊은낙폭 반등 집중 전략.

    [데이터 기반 설계 — 2026-07-02 실증]
    370만 거래일 분석:
      MA60 -25~-35%: 평균 120d +56.3%
      MA60 -35~-45%: 평균 120d +73.9%
      MA60 -45%↓:    평균 120d +103.5%
    → V-RECOVERY(-20~-65%)보다 최강구간(-25~-60%)에 집중

    진입 조건:
    A) MA60 대비 -25% ~ -60% 낙폭 (최강구간 집중)
    B) 52주 저점 대비 +10~100% (저점 탈출 확인 후 포착)
    C) 거래량 1.5x+ (진입 확인)
    D) 최근 5일 중 3일 이상 상승 (반등 지속 확인)
    E) 시총 300억+ (안전 마진)
    F) KOSPI MA120 × 0.80 이상 (패닉장 제외)

    매도: Trail -22%(이익 후) / Trail -30%(50%+ 이익) / 손절 -13% / 만료 300일 / 익절 100%
    """
    init_backtest_db()
    run_name = run_name or f"V-DEEP깊은낙폭 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "deep_recovery", "deep_v2_strict_20260715",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
         "pct_from_low_min": pct_from_low_min, "pct_from_low_max": pct_from_low_max,
         "vol_ratio": vol_ratio, "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'deep_recovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

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
                  AND su.market_cap >= 300
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()

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
                       COALESCE(high,close) AS h, COALESCE(low,close) AS lo,
                       COALESCE(open,close) AS o
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
                'o': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

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
            # 전일 종가로 확정된 주문만 다음 거래일 시가에 체결한다.
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', 300))
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
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', 300)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            # 매도 체크
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
                tpct = trail_big if ret >= 0.50 else trail
                trail_cond = (curr - peak) / peak < tpct
                stop_cond  = ret < stop
                tp_cond    = ret >= tp
                expire_cond = p['hold'] >= max_hold
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or tp_cond or expire_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'tp' if tp_cond else 'chart_top' if chart_top_cond else 'expire')
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue

            # KOSPI 필터
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
                if i is None or i < 80: continue
                c = s['c']
                v = s['v']
                lo = s['lo']
                curr = c[i]
                if curr < 500: continue

                # [E] 시총 300억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    if sh <= 0 or sh * curr / 1e8 < 300:
                        continue

                # [A] MA60 대비 낙폭 범위
                ma60 = sum(c[max(0,i-59):i+1]) / min(60, i+1)
                if ma60 <= 0: continue
                depth = (curr - ma60) / ma60
                if depth > ma60_depth_min or depth < ma60_depth_max:
                    continue

                # [B] 52주 저점 대비 위치
                p252 = lo[max(0,i-251):i+1]
                low52 = min(p252) if p252 else curr
                if low52 <= 0: continue
                pct_from_low = (curr - low52) / low52 * 100
                if pct_from_low < pct_from_low_min or pct_from_low > pct_from_low_max:
                    continue

                # [C] 거래량 확인
                v_now  = v[i]
                v_avg20 = sum(v[max(0,i-20):i]) / max(1, min(20,i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [D] 최근 5일 중 3일 이상 상승
                if i >= 5:
                    up_days = sum(1 for j in range(i-4, i+1) if j > 0 and c[j] > c[j-1])
                    if up_days < 3:
                        continue

                # 복합 점수: 낙폭 깊이 + 저점반등 최적구간 보너스
                depth_score = min(-depth * 100, 55)
                # 최적 구간 30~80% 반등에 보너스
                low_bonus = 12.0 if 30 <= pct_from_low <= 80 else (
                            6.0 if pct_from_low <= 30 else 2.0)
                score = depth_score + low_bonus
                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            available = max(0, position_limit - len(pos) - len(pending_buys))
            pending_buys.extend(code for _, code, _, _ in candidates[:min(3, available)])

        # 최종 청산
        final_val = cash
        for code, p in pos.items():
            last_c = None
            for d in reversed(sim_dates):
                i = didx[code].get(d)
                if i is not None and sd[code]['c'][i] > 0:
                    last_c = sd[code]['c'][i]; break
            if last_c:
                pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', 300))
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




