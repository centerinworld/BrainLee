"""
regime_adaptive.py -- run_backtest_regime_adaptive()
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
    _corp_action_adjusted_entry,
    _is_buy_v1,
    _is_buy_v11,
    _load_corp_action_factors,
    _ma,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _save_result,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_regime_adaptive(start_date: str, end_date: str,
                                  per_stock: float = 10_000_000,
                                  max_positions: int = 10,
                                  strict_exec: bool = True,
                                  run_name: str = None,
                                  run_id: str = None,
                                  data_asof_ts: str = None) -> str:
    """
    레짐 적응형 전략 (Meta-V):
      - BULL (KOSPI > MA120): V1 MA추세 신호 → 추세추종
      - BEAR (KOSPI < MA120): V7 흑자전환 신호 → 구조적 전환주 (하락장에서도 매수)
    전략 전환은 매 거래일 자동으로 이뤄짐.

    data_asof_ts: 2026-09-04 신규. corporate_action_events.adjustment_status(매일
    00:10 "기업행위조정계수후속확정" 잡)와 financial_data(매일 00:05 "데이터무결성
    후속검증" 잡)가 실행 중에도 계속 갱신돼, 이 값 없이 같은 과거 구간을 재실행하면
    거래건수는 같아도 진입가 보정·BEAR 구간 흑자전환 신호가 흔들려 total_return_pct가
    달라진다(회귀검증 비재현성의 실제 원인). 'YYYY-MM-DD HH:MM:SS'를 주면 그 시각
    기준 데이터로 고정해 재실행해도 항상 동일한 결과를 보장한다. None(기본값)이면
    기존과 동일하게 항상 최신 데이터를 사용.
    """
    init_backtest_db()
    _strat_key = 'regime_adaptive'
    _ra_params = {"per_stock": per_stock, "max_positions": max_positions, "strict_exec": strict_exec,
                  "start": start_date, "end": end_date, "data_asof_ts": data_asof_ts}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status,strategy)
            VALUES (?,?,?,?,?,?,'running',?)
        """, (run_id, run_name or f"레짐 적응형 {start_date[:7]}~{end_date[:7]}",
              start_date, end_date, per_stock, max_positions, _strat_key))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running', strategy=? WHERE run_id=?",
                     (_strat_key, run_id))
        conn.commit()

    # 2026-08-23: 확정된 기업행위 조정계수 전량 로드(turnaround와 동일 목적).
    # 2026-09-04: data_asof_ts로 재현성 고정(위 docstring 참조).
    _corp_action_factors = _load_corp_action_factors(
        conn,
        [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM corporate_action_events WHERE adjustment_status='factor_confirmed'"
            + (" AND updated_at <= ?" if data_asof_ts else ""),
            ([data_asof_ts] if data_asof_ts else []),
        ).fetchall()],
        data_asof_ts=data_asof_ts,
    )

    # 2026-07-27: 코드 재확인 결과 이 엔진의 유니버스 쿼리(전 종목, market_cap 조건
    # 없음)와 실제 신호함수 _is_buy_v1/_is_buy_v11 둘 다 시총 파라미터를 받지도,
    # stock_universe.market_cap을 참조하지도 않음 — "V1 스스로 1000억+ 확인" 주석은
    # run_backtest_v1(독립엔진)의 자체 유니버스 사전필터와 혼동된 stale 주석이었음.
    # 즉 애초에 고칠 시총 필터 자체가 없어 "current" 라벨이 부정확했으므로
    # v8과 동일하게 "not_applicable"로 정정(as-of 리트로핏 대상 아님).
    _record_run_spec(
        run_id, "regime_adaptive", "regime_v2_strict_20260716", _ra_params,
        signal_timing="close_D", execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        # 재무 데이터 로드
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
                          ELSE printf('%d-02-15', f.year+1) END) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE ((f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
               OR (f.is_annual=1))
              {"AND f.updated_at <= ?" if data_asof_ts else ""}
            ORDER BY f.stock_code, f.year, f.quarter
        """, ([data_asof_ts] if data_asof_ts else [])).fetchall():
            fin_all.setdefault(r[0], []).append(r[1:])

        # 전체 종목 (6자리 숫자 코드, 충분한 데이터 보유)
        # 2026-07-27 정정: 시총 필터는 여기도 _is_buy_v1/_is_buy_v11 내부에도 없음
        # (과거 주석이 잘못 기술 — 아래 참조).
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) AS cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close, COALESCE(volume,0),
                           COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0),
                           COALESCE(open,0)
                    FROM price_history
                    WHERE stock_code=? AND date>=? AND date<=? AND close>0
                    ORDER BY date ASC
                """, (sc, warmup_start, end_date)).fetchall()
                if len(rows) < 200:
                    continue
                dates_  = [r[0] for r in rows]
                prices_ = [float(r[1]) for r in rows]
                vols_   = [float(r[2]) for r in rows]
                frn_    = [float(r[3]) for r in rows]
                inst_   = [float(r[4]) for r in rows]
                opens_  = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows]
                sim_i   = next((i for i, d in enumerate(dates_) if d >= start_date), len(dates_))
                stock_data[sc] = {
                    'dates': dates_, 'prices': prices_, 'volumes': vols_, 'opens': opens_,
                    'frn': frn_, 'inst': inst_, 'fins': fin_all.get(sc, []),
                    'sim_start_i': sim_i,
                }
            except Exception:
                continue

        # KOSPI 레짐: True=BULL, False=BEAR
        # 히스테리시스 버퍼: BULL→BEAR는 MA120 * 0.97 이하, BEAR→BULL은 MA120 * 1.03 이상
        # → 횡보 구간 잦은 전환 방지
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r[0] for r in krows]
            k_prices = [float(r[1]) for r in krows]
            cur_regime = True  # 초기 상태: BULL
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    # 워밍업 기간: 레짐 초기화
                    kma = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                    if kma is not None:
                        cur_regime = k_prices[ki] > kma
                    continue
                kma = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                if kma is not None:
                    if cur_regime and k_prices[ki] < kma * 0.97:
                        cur_regime = False   # BULL→BEAR: MA120 3% 아래 돌파 시
                    elif not cur_regime and k_prices[ki] > kma * 1.03:
                        cur_regime = True    # BEAR→BULL: MA120 3% 위 돌파 시
                market_bullish[kd] = cur_regime
        except Exception:
            pass

        conn.close()

        def _check_sell_adaptive(i, prices, pos):
            curr = prices[i]
            peak = pos.get('peak_price', pos['entry_price'])
            if curr > peak:
                pos['peak_price'] = curr
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            hold = pos.get('hold_days', 0)
            pos['hold_days'] = hold + 1
            stop = pos.get('stop_loss', -0.10)
            take = pos.get('take_profit',  0.25)
            if pct >= take:
                return f"익절(+{pct*100:.0f}%)"
            if pct <= stop:
                return f"손절({stop*100:.0f}%)"
            if hold >= 5:
                trail = (curr - peak) / peak if peak > 0 else 0
                if trail <= -0.12 and pct > 0.03:
                    return f"추적손절(고점-{abs(trail)*100:.0f}%)"
                ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
                if ma60 is not None and curr < ma60:
                    return "MA60 붕괴"
            return None

        total_capital = per_stock * max_positions
        cash = total_capital  # 2026-07-16: 현금원장 전환
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])} for sc, d in stock_data.items()}
        positions: Dict[str, dict] = {}
        ra_pending_sells: list = []
        ra_pending_buys: list = []
        trades:    list = []
        equity_curve: list = []
        monthly_buys: Dict[str, int] = {}
        regime_switches: list = []   # 레짐 전환 기록
        prev_regime = None

        for day in sim_dates:
            is_bull = market_bullish.get(day, True)
            cur_regime = 'BULL' if is_bull else 'BEAR'
            if cur_regime != prev_regime:
                regime_switches.append({'date': day, 'to': cur_regime})
                prev_regime = cur_regime

            # strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약)
            if strict_exec:
                _still = []
                for sc, reason in ra_pending_sells:
                    if sc not in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        _still.append((sc, reason)); continue
                    px = stock_data[sc]['opens'][im[day]]
                    pos = positions.pop(sc)
                    _ra_entry_adj = _corp_action_adjusted_entry(
                        _corp_action_factors, sc, pos['entry_date'], day, pos['entry_price'])
                    _ra_amt, _ra_pct = _net_profit(_ra_entry_adj, px, pos['qty'], pos.get('mkt_cap_억', 500))
                    cash += pos['qty'] * _ra_entry_adj + _ra_amt
                    trades.append({
                        'stock_code': sc, 'entry_date': pos['entry_date'], 'exit_date': day,
                        'entry_price': pos['entry_price'], 'exit_price': px, 'qty': pos['qty'],
                        'profit_pct': _ra_pct, 'profit_amt': _ra_amt, 'exit_reason': reason,
                        'entry_regime': pos.get('regime', '?'),
                    })
                ra_pending_sells = _still
                for sc, regime_at_signal, stop_val, take_val in ra_pending_buys:
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
                        'entry_date': day, 'entry_price': px, 'qty': qty, 'peak_price': px,
                        'hold_days': 0, 'stop_loss': stop_val, 'take_profit': take_val,
                        'regime': regime_at_signal,
                    }
                ra_pending_buys = []

            # 매도
            sold = []
            for sc, pos in list(positions.items()):
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                reason = _check_sell_adaptive(im[day], stock_data[sc]['prices'], pos)
                if reason is None:
                    continue
                if strict_exec:
                    if not pos.get('pending_exit'):
                        pos['pending_exit'] = reason
                        ra_pending_sells.append((sc, reason))
                    continue
                i_ex  = im[day]
                curr  = stock_data[sc]['prices'][i_ex]
                pct   = (curr - pos['entry_price']) / pos['entry_price']
                _ra2_entry_adj = _corp_action_adjusted_entry(
                    _corp_action_factors, sc, pos['entry_date'], day, pos['entry_price'])
                _ra2_amt, _ra2_pct = _net_profit(_ra2_entry_adj, curr, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * _ra2_entry_adj + _ra2_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  curr,
                    'qty':         pos['qty'],
                    'profit_pct':  _ra2_pct,
                    'profit_amt':  _ra2_amt,
                    'exit_reason': reason,
                    'entry_regime': pos.get('regime', '?'),
                })
                sold.append(sc)
            for sc in sold:
                del positions[sc]

            # 매수 (레짐별 신호 함수 전환)
            month_key = day[:7]
            if len(positions) < max_positions:
                for sc, sd in stock_data.items():
                    if len(positions) >= max_positions:
                        break
                    if sc in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]

                    if is_bull:
                        # BULL: V1 MA추세 신호 (1000억+ 시총 포함 — V1 스스로 확인)
                        signal_ok = _is_buy_v1(
                            i, sd['sim_start_i'], sd['dates'], sd['prices'],
                            sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                        stop_val, take_val = -0.08, 0.20
                    else:
                        # BEAR: V7 흑자전환 신호 (300억+ 포함)
                        signal_ok = _is_buy_v11(
                            i, sd['sim_start_i'], sd['dates'], sd['prices'],
                            sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                        stop_val, take_val = -0.10, 0.30

                    if not signal_ok:
                        continue
                    if strict_exec:
                        if sc not in [x[0] for x in ra_pending_buys] and \
                           len(positions) + len(ra_pending_buys) < max_positions:
                            ra_pending_buys.append((sc, cur_regime, stop_val, take_val))
                            monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1
                        continue
                    curr = sd['prices'][i]
                    budget = min(per_stock, cash * 0.99)
                    qty = int(budget / curr)
                    if qty < 1 or qty * curr > cash:
                        continue
                    cash -= qty * curr
                    positions[sc] = {
                        'entry_date': day, 'entry_price': curr,
                        'qty': qty, 'peak_price': curr, 'hold_days': 0,
                        'stop_loss': stop_val, 'take_profit': take_val,
                        'regime': cur_regime,
                    }
                    monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1

            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})

        # 강제 청산
        last_day = sim_dates[-1] if sim_dates else None
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _raf_entry_adj = _corp_action_adjusted_entry(
                _corp_action_factors, sc, pos['entry_date'], last_day or end_date, pos['entry_price'])
            _raf_amt, _raf_pct = _net_profit(_raf_entry_adj, curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * _raf_entry_adj + _raf_amt
            trades.append({
                'stock_code': sc, 'entry_date': pos['entry_date'],
                'exit_date': last_day or pos['entry_date'],
                'entry_price': pos['entry_price'], 'exit_price': curr,
                'qty': pos['qty'],
                'profit_pct': _raf_pct,
                'profit_amt': _raf_amt,
                'exit_reason': '기간종료',
                'entry_regime': pos.get('regime', '?'),
            })

        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

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

        bull_buys = sum(1 for t in trades if t.get('entry_regime') == 'BULL')
        bear_buys = sum(1 for t in trades if t.get('entry_regime') == 'BEAR')
        bull_days = sum(1 for v in market_bullish.values() if v)
        bear_days = sum(1 for v in market_bullish.values() if not v)

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 레짐 적응형: BULL({bull_days}일→V1 MA추세) / BEAR({bear_days}일→V7 흑자전환)\n"
            f"매수: BULL={bull_buys}건 / BEAR={bear_buys}건  |  레짐 전환: {len(regime_switches)}회\n"
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
            'regime_switches': regime_switches[:20],
        }
        _save_result(run_id, result)
        _register_execution_artifacts(run_id, total_capital, cash)
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
        raise


# ══════════════════════════════════════════════════════════════
#  복합 스코어링 시그널 (100점 기반 선택적 매수)
# ══════════════════════════════════════════════════════════════


