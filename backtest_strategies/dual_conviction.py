"""
dual_conviction.py -- run_backtest_dual_conviction()
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
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_dual_conviction(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 15,
    per_stock: float = 8_000_000,
    window_days: int = 90,
    stop: float = -0.20,
    trail: float = -0.25,
    trail_activate_pct: float = 0.10,
    max_hold: int = 365,
    dedup_months: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DUALCONVICTION — 임원 자사주 매수(dart_insider_holdings) + 회사 자사주
    취득(treasury_buyback)이 ±window_days 이내에 동시 발생하는 "경영진 이중확신"
    독립 신규 전략 (2026-09-01, 사용자 지시: 기존 전략에 얹지 말고 독립적으로 검증).

    [배경] 라벨레벨 사전검증(insider-only 대조군 대비, train<2023/test>=2023):
    median 기준 DUAL이 CONTROL을 4개 비교(6m/12m × train/test) 전부 상회
    (6m train 4.52%>-0.64%, 6m test 0.46%>-0.14%, 12m train 7.52%>-3.73%,
    12m test 2.96%>0.97%) — 방향 일치. mean은 6m test만 역전(9.39%<11.87%,
    control의 두꺼운 우측꼬리 영향 추정), 12m은 mean도 일치(24.88%>21.29%).
    median 기준 재현성은 확인됐으나 mean 기준으로는 약한 신호 — 실행 백테스트로
    최초 검증.

    매수: 임원 자사주 매수(change_amount>0) 공시와 회사 자사주 취득
         (event_type IN 취득/acquisition/trust) 공시가 동일종목에서
         ±window_days 이내 발생 시, 둘 중 늦은 날짜(정보 확정 시점) 익일 시가매수.
         동일 (종목,월) 중복 이벤트는 1건으로 축약(dedup_months).
    매도: 손절-20%/추적손절-25%(이익10%+발동)/만기365일.
    """
    init_backtest_db()
    run_name = run_name or f"V-DUALCONVICTION {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "dual_conviction", "dual_conviction_v1_20260901",
        {"window_days": window_days, "stop": stop, "trail": trail,
         "max_hold": max_hold, "max_positions": max_positions,
         "per_stock": per_stock, "total_capital": total_capital,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable",
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'dual_conviction',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=400)).strftime('%Y-%m-%d')

        def _norm_date(raw) -> str:
            if not raw:
                return ""
            s = str(raw).strip()
            if len(s) == 8 and s.isdigit():
                return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
            s = s.replace(".", "-")
            return s[:10] if len(s) >= 10 else ""

        insider_buy: Dict[str, list] = {}
        for code, rd, ca in conn.execute("""
            SELECT stock_code, rcept_dt, change_amount FROM dart_insider_holdings
            WHERE rcept_dt IS NOT NULL AND change_amount IS NOT NULL AND change_amount > 0
        """).fetchall():
            d = _norm_date(rd)
            if d:
                insider_buy.setdefault(code, []).append(d)

        buyback: Dict[str, list] = {}
        for code, rd, et in conn.execute("""
            SELECT stock_code, rcept_dt, event_type FROM treasury_buyback
            WHERE event_type LIKE '%취득%' OR event_type LIKE '%acquisition%' OR event_type LIKE '%trust%'
        """).fetchall():
            d = _norm_date(rd)
            if d:
                buyback.setdefault(code, []).append(d)

        for m in (insider_buy, buyback):
            for c in m:
                m[c] = sorted(set(m[c]))

        # 이중확신 이벤트 = 종목별 (임원매수일, 자사주취득일)이 window_days 이내인 쌍
        raw_events: list = []
        for code, bdates in buyback.items():
            idates = insider_buy.get(code)
            if not idates:
                continue
            for bd in bdates:
                bd_dt = datetime.strptime(bd, "%Y-%m-%d")
                for idt in idates:
                    idt_dt = datetime.strptime(idt, "%Y-%m-%d")
                    if abs((idt_dt - bd_dt).days) <= window_days:
                        anchor = max(bd_dt, idt_dt)
                        raw_events.append((code, anchor.strftime("%Y-%m-%d")))
                        break

        if dedup_months:
            seen = set()
            events = []
            for code, d in raw_events:
                key = (code, d[:7])
                if key in seen:
                    continue
                seen.add(key)
                events.append((code, d))
        else:
            events = sorted(set(raw_events))

        codes = sorted({c for c, _ in events})
        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open,close) AS o
                FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            sd[code] = {'d': [str(r[0])[:10] for r in rows], 'c': c_list,
                        'o': [float(r[2]) for r in rows]}
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        buy_pool: Dict[str, list] = {}
        for code, sig_date in events:
            s = sd.get(code)
            if not s or code not in didx:
                continue
            pos = None
            for i, d in enumerate(s['d']):
                if d > sig_date:
                    pos = i; break
            if pos is None or pos < 60:
                continue
            entry_date = s['d'][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            buy_pool.setdefault(entry_date, []).append(code)
        for d in buy_pool:
            buy_pool[d] = sorted(set(buy_pool[d]))

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        for day in sim_dates:
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
                pnl, net_pct = _net_profit(p['entry'], px, p['shares'], 300)
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                                'entry': p['entry'], 'exit': px, 'pnl_pct': net_pct,
                                'reason': reason, 'pnl': round(pnl, 0)})
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
                pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0, 'peak': px}
            pending_buys = []

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
                stop_cond = ret <= stop
                expire_cond = p['hold'] >= max_hold
                trail_cond = ret > trail_activate_pct and (curr - p['peak']) / p['peak'] <= trail
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if code not in [c for c, _ in pending_sells]:
                        pending_sells.append((code, reason))

            pending_codes = set(pending_buys)
            slots = max_positions - len(pos) - len(pending_codes)
            if slots > 0:
                for code in buy_pool.get(day, []):
                    if slots <= 0:
                        break
                    if code in pos or code in pending_codes:
                        continue
                    pending_buys.append(code)
                    pending_codes.add(code)
                    slots -= 1

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]['c'][i] if i is not None else p['entry']
            if curr <= 0:
                curr = p['entry']
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], 300)
            cash += p['shares'] * p['entry'] + pnl
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                            'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                            'reason': 'final', 'pnl': round(pnl, 0)})

        total_return = (cash - total_capital) / total_capital * 100
        completed = [t for t in trades if 'pnl_pct' in t]
        win_rate = (sum(1 for t in completed if t['pnl_pct'] > 0) / len(completed) * 100) if completed else 0
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, total_trades=?, win_rate=?, trades_json=?
            WHERE run_id=?
        """, (round(total_return, 2), len(completed), round(win_rate, 1),
              json.dumps({"trades": trades}), run_id))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=False)
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




