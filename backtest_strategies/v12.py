"""
v12.py -- run_backtest_v12()
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
    _calc_metrics,
    _ma,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _rsi,
    _save_result,
    init_backtest_db,
    logger,
    sqlite3,
)

def _run_backtest_v12(conn, warmup_start, start_date, end_date, sim_dates,
                      per_stock, max_positions, stop_loss, stop_loss_pct,
                      take_profit_pct,
                      strict_exec: bool = True,
                      asof_mktcap: bool = True):
    """
    V12는 섹터별 상대강도를 계산해야 해서 별도 함수로 구현.
    섹터 alpha = 해당 섹터 평균 3개월 수익률 - KOSPI 3개월 수익률
    종목은 섹터 alpha > 0 이고 자체 RS도 양수인 경우만 매수.
    """
    # KOSPI 로드
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates  = [r[0] for r in kospi_rows]
    k_prices = {r[0]: float(r[1]) for r in kospi_rows}

    # 시장 상승장 필터
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # 섹터 정보 + 전 종목 로드 (섹터 정의된 종목만; 시총 2000억+ 게이트는 as-of 모드에서 신호평가 시점으로 이동)
    sector_map = {}
    _sector_universe_sql = """
        SELECT stock_code, COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,''), '기타')
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND sector_large NOT IN ('기타','벤처기업부','신성장기업부','우선주','리츠','ETF','ETN','','스팩')
    """ + ("" if asof_mktcap else " AND market_cap >= 2000")
    for sc, sec in conn.execute(_sector_universe_sql).fetchall():
        if sec and sec not in ('기타', '벤처기업부', '신성장기업부'):
            sector_map[sc] = sec

    share_intervals: Dict[str, list] = {}
    if asof_mktcap:
        for code, effective_from, effective_to, shares, quality in conn.execute(
            """SELECT stock_code,effective_from,effective_to,shares_issued,quality
               FROM security_share_history ORDER BY stock_code,effective_from"""
        ):
            share_intervals.setdefault(code, []).append(
                (effective_from, effective_to, float(shares or 0), quality)
            )

    def _v12_shares_asof(code: str, day: str) -> float:
        for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
            if effective_from <= day and (effective_to is None or day < effective_to):
                return shares
        return 0.0

    stock_data = {}
    for sc in sector_map:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0), COALESCE(open, close)
            FROM price_history
            WHERE stock_code=? AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (sc, warmup_start, end_date)).fetchall()
        if len(rows) < 60:
            continue
        dates  = [r[0] for r in rows]
        prices = [float(r[1]) for r in rows]
        vols   = [float(r[2]) for r in rows]
        opens  = [float(r[3]) if r[3] and r[3] > 0 else float(r[1]) for r in rows]
        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {'dates': dates, 'prices': prices, 'volumes': vols, 'opens': opens,
                          'sim_start_i': sim_start_i}

    # 날짜→인덱스 맵
    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    positions = {}
    trades    = []
    equity_curve = []
    total_capital = per_stock * max_positions
    cash = total_capital  # 2026-07-16: 고정슬롯 P&L 누산 → 실제 현금원장 전환

    # 섹터별 3개월 수익률 캐시 (당일 계산, 재계산 최소화)
    _sector_cache = {}
    _sector_cache_date = [None]

    def _get_hot_sectors(day):
        if _sector_cache_date[0] == day:
            return _sector_cache
        _sector_cache.clear()
        _sector_cache_date[0] = day

        # KOSPI 3개월 수익률
        kp_now  = k_prices.get(day)
        if kp_now is None:
            return _sector_cache
        # 약 63 거래일 전 날짜 찾기
        k_date_list = sorted(k_prices.keys())
        idx_now = next((idx for idx, d in enumerate(k_date_list) if d >= day), None)
        if idx_now is None or idx_now < 60:
            return _sector_cache
        kp_63 = k_prices.get(k_date_list[max(0, idx_now - 63)])
        kospi_3m = (kp_now - kp_63) / kp_63 * 100 if kp_63 and kp_63 > 0 else 0

        # KOSPI 1개월(21일) 수익률도 계산 (초기사이클 감지용)
        kp_21  = k_prices.get(k_date_list[max(0, idx_now - 21)])
        kospi_1m = (kp_now - kp_21) / kp_21 * 100 if kp_21 and kp_21 > 0 else 0

        # 섹터별 3개월 + 1개월 수익률
        sec_rets   = {}  # 3M
        sec_rets1m = {}  # 1M
        for sc, sec in sector_map.items():
            if sc not in stock_data:
                continue
            sd = stock_data[sc]
            idx_map = date_idx.get(sc, {})
            i = idx_map.get(day)
            if i is None or i < 63 or i < sd['sim_start_i']:
                continue
            p_now = sd['prices'][i]
            p_63  = sd['prices'][i - 63]
            if p_63 <= 0:
                continue
            ret3m = (p_now - p_63) / p_63 * 100
            sec_rets.setdefault(sec, []).append(ret3m)
            # 1개월 수익률 (21일)
            if i >= 21:
                p_21 = sd['prices'][i - 21]
                if p_21 > 0:
                    sec_rets1m.setdefault(sec, []).append((p_now - p_21) / p_21 * 100)

        for sec, rets in sec_rets.items():
            avg    = sum(rets) / len(rets) if rets else 0
            alpha  = avg - kospi_3m
            rets1m = sec_rets1m.get(sec, [])
            avg1m  = sum(rets1m) / len(rets1m) if rets1m else 0
            alpha1m = avg1m - kospi_1m   # 1개월 alpha (최근 모멘텀)
            _sector_cache[sec] = {
                'alpha':    alpha,
                'alpha1m':  alpha1m,
                'avg_ret':  avg,
                'kospi_3m': kospi_3m,
            }
        return _sector_cache

    v12_pending_sells: list = []
    v12_pending_buys: list = []

    for day in sim_dates:
        # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
        if strict_exec:
            _still = []
            for sc, reason in v12_pending_sells:
                if sc not in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    _still.append((sc, reason)); continue
                px = stock_data[sc]['opens'][im[day]]
                pos = positions.pop(sc)
                _v12_pnl_amt, _v12_pnl_pct = _net_profit(pos['entry_price'], px, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * pos['entry_price'] + _v12_pnl_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  px,
                    'qty':         pos['qty'],
                    'profit_pct':  _v12_pnl_pct,
                    'profit_amt':  _v12_pnl_amt,
                    'exit_reason': reason,
                })
            v12_pending_sells = _still
            for sc in v12_pending_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                px = stock_data[sc]['opens'][im[day]]
                if px <= 0:
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / px)
                if qty < 1 or qty * px > cash:
                    continue
                cash -= qty * px
                positions[sc] = {
                    'entry_date': day, 'entry_price': px,
                    'qty': qty,
                    'peak_price': px, 'hold_days': 0,
                }
            v12_pending_buys = []

        # 매도 체크
        sold_today = []
        # 매도 판단에 섹터 현황 필요 (데이터기반 매도용)
        _hot_sec = _get_hot_sectors(day)
        for sc, pos in list(positions.items()):
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
            sec      = sector_map.get(sc, '기타')
            s_info   = _hot_sec.get(sec, {})
            reason = _check_sell_v12(i, sd['prices'], pos, stop_loss, stop_loss_pct, take_profit_pct,
                                     sec_alpha=s_info.get('alpha'), sec_alpha1m=s_info.get('alpha1m'))
            if reason is None:
                continue
            if strict_exec:
                if not pos.get('pending_exit'):
                    pos['pending_exit'] = reason
                    v12_pending_sells.append((sc, reason))
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _v12b_amt, _v12b_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * pos['entry_price'] + _v12b_amt
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  curr,
                'qty':         pos['qty'],
                'profit_pct':  _v12b_pct,
                'profit_amt':  _v12b_amt,
                'exit_reason': reason,
            })
            sold_today.append(sc)
        for sc in sold_today:
            del positions[sc]

        # 시장 필터
        if market_bullish and not market_bullish.get(day, True):
            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})
            continue

        # 매수 스캔
        if len(positions) < max_positions:
            hot_sectors = _get_hot_sectors(day)

            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                sec = sector_map.get(sc, '기타')
                sec_info = hot_sectors.get(sec)
                if sec_info is None:
                    continue
                # ★ 초기사이클 포착: 3M alpha 4~20% + 1M alpha 양수
                # 기존 'alpha > 15%'는 사이클 정점(이미 오른 뒤) 매수 → 최근 -47.7% 원인
                # 변경: 섹터가 막 상승 시작한 단계(3M alpha 4~20%, 1M 모멘텀 유지)
                sec_alpha   = sec_info['alpha']
                sec_alpha1m = sec_info.get('alpha1m', 0)
                if sec_alpha < 4 or sec_alpha > 20:
                    continue   # 너무 낮거나(미회복) 너무 높으면(정점 위험) 제외
                if sec_alpha1m < 0:
                    continue   # 최근 1달 모멘텀이 꺾이면 진입 금지

                idx_map = date_idx.get(sc, {})
                i = idx_map.get(day)
                if i is None or i < sd['sim_start_i'] or i < 120:
                    continue

                p = sd['prices']
                curr = p[i]

                # 시총 2000억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    _sh = _v12_shares_asof(sc, day)
                    if _sh <= 0 or _sh * curr / 1e8 < 2000:
                        continue

                # 개별 종목 RS: 섹터 평균 아웃퍼폼 (KOSPI보다 엄격)
                p63_back = p[i - 63] if i >= 63 else None
                if p63_back and p63_back > 0:
                    stock_3m = (curr - p63_back) / p63_back * 100
                    # 섹터 평균보다 낮거나 KOSPI보다 낮으면 탈락
                    if stock_3m < sec_info['avg_ret'] * 0.7 or stock_3m < sec_info['kospi_3m']:
                        continue

                # 가격 > MA60 > MA120 (추세 정배열)
                p_slice = p[max(0, i - 120): i + 1]
                if len(p_slice) < 120:
                    continue
                ma60  = sum(p_slice[-60:]) / 60
                ma120 = sum(p_slice) / len(p_slice)
                if curr < ma60 or ma60 < ma120 * 0.98:
                    continue

                # 52주 고점 -20% 이내 (V4 기준 적용)
                high52 = max(p[max(0, i - 251): i + 1])
                if curr < high52 * 0.80:
                    continue

                # RSI 50~75 (모멘텀 확인, 과열 제외)
                rsi_val = _rsi(p[max(0, i - 28): i + 1])
                if rsi_val is None or rsi_val < 50 or rsi_val > 75:
                    continue

                # 거래량 증가 확인 (최소 10일 평균 1.2배)
                vols = sd['volumes']
                vol_win = [v for v in vols[max(0, i - 10): i] if v > 0]
                if not vol_win or vols[i] < sum(vol_win) / len(vol_win) * 1.2:
                    continue

                if strict_exec:
                    if sc not in v12_pending_buys and \
                       len(positions) + len(v12_pending_buys) < max_positions:
                        v12_pending_buys.append(sc)
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date':  day,
                    'entry_price': curr,
                    'qty':         qty,
                    'peak_price':  curr,
                    'hold_days':   0,
                }

        marked = sum(
            stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        equity_curve.append({'date': day, 'equity': round(cash + marked)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        idx_map = date_idx.get(sc, {})
        sd = stock_data[sc]
        curr = sd['prices'][idx_map[last_day]] if last_day and last_day in idx_map \
               else (sd['prices'][-1] if sd['prices'] else pos['entry_price'])
        pct = (curr - pos['entry_price']) / pos['entry_price']
        _v12f_amt, _v12f_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
        cash += pos['qty'] * pos['entry_price'] + _v12f_amt
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  _v12f_pct,
            'profit_amt':  _v12f_amt,
            'exit_reason': '기간종료',
        })

    return trades, equity_curve, cash




def _check_sell_v12(i, prices, pos, stop_loss=-0.08, stop_loss_pct=-0.07,
                    take_profit_pct=0.20, sec_alpha=None, sec_alpha1m=None):
    """V12 매도: 익절 +20%, 손절 -7%, 추적손절 -12%, MA60붕괴.
    ★ 데이터기반 추가: 손실권(-7%+) + 섹터모멘텀 소멸(1M alpha 꺾임) → 조기청산.
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr
    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    if pct >= take_profit_pct:
        return f"익절(+{pct*100:.0f}%)"
    if pct <= stop_loss_pct:
        return f"손절({stop_loss_pct*100:.0f}%)"
    # ★ 데이터기반: 손실권(-7%이하) + 섹터 1M 모멘텀 역전 + MA20 붕괴 → 진입조건 소멸
    if pct < -0.07 and hold_days >= 15 and sec_alpha1m is not None:
        if sec_alpha1m < -2:   # 섹터 1개월 alpha 마이너스 전환 = 사이클 역전
            ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
            if ma20 and curr < ma20:
                return "섹터모멘텀소멸(V12)"
    if hold_days >= 5:
        trail_pct = (curr - peak) / peak if peak > 0 else 0
        if trail_pct <= -0.12 and pct > 0.03:
            return f"추적손절(고점-{abs(trail_pct)*100:.0f}%)"
        ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
        if ma60 is not None and curr < ma60:
            return "MA60 붕괴"
    return None


# ══════════════════════════════════════════════════════════════
#  V10/V11 공통 백테스트 실행 (V4 run_backtest 구조 재활용)
# ══════════════════════════════════════════════════════════════


def run_backtest_v12(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     asof_mktcap: bool = False,  # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: v12/sector_precondition)
                     take_profit_pct: float = 0.25,  # 2026-08-09 파라미터화(텐버거 population 캡처 실험용), 기본값 기존과 동일
                     run_name: str = None, run_id: str = None) -> str:
    """V12는 섹터 계산이 필요하므로 별도 흐름."""
    init_backtest_db()
    run_name = run_name or f"V12 섹터대세 {start_date[:7]}~{end_date[:7]}"
    _v12_params = {"per_stock": per_stock, "max_positions": max_positions,
                   "stop_loss": -0.07, "take_profit_pct": take_profit_pct,
                   "strict_exec": True, "asof_mktcap": asof_mktcap,
                   "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v12',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    _record_run_spec(run_id, "v12", "v12_v2_strict_20260714", _v12_params,
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode=("asof_approx" if asof_mktcap else "current"),
                     allocation_rule="fixed_slot",
                     universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current")
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        trades, equity_curve, final_cash = _run_backtest_v12(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss=-0.07, stop_loss_pct=-0.07, take_profit_pct=take_profit_pct,
            asof_mktcap=asof_mktcap,
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V12 섹터대세: KOSPI MA120 필터 / 섹터 alpha>10% / 개별 RS / 익절+25% / 손절-7%\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        conn.close()
        _register_execution_artifacts(run_id, total_capital, final_cash)
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        conn.close()
        raise




