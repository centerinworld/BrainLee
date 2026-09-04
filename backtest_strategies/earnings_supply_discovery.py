"""
earnings_supply_discovery.py -- run_backtest_earnings_supply_discovery()
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
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_earnings_supply_discovery(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 25,
    op_growth_min: float = 1.0,       # 분기 영업이익 YoY 성장 하한(1.0=100%). Codex 2026-08-11 PIT
                                       # walk-forward 발견(생존편향 제거 데이터셋, 학습/검증 양쪽 lift>1):
                                       # supply_20d_억>=10 & op_growth>=100% -> 10x 2.13%/3x 16.31%/
                                       # 5x 8.51%(3배 기준 목표15% 이미 초과). 개별예측 정밀도가 아니라
                                       # V-MOONSHOT과 동일한 분산+익절없음+넓은손절 포트폴리오로 실전
                                       # 백테스트해 실제 운용수익률을 확인하기 위해 이식.
    supply_min_억: float = 10.0,      # 20일 기관+외국인 순매수 합계 하한(억원)
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,         # V-MOONSHOT과 동일 설계(변동성 큰 모집단, 조기손절 방지)
    trail_pct: float = -0.35,
    max_hold: int = 500,              # ~2년, 텐버거 중위 도달기간(1.3~1.7년) 고려
    asof_mktcap: bool = True,
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DISCOVERY — Codex PIT(생존편향 제거) walk-forward 발굴 신호 실전 백테스트.

    [배경] 2026-08-11 Codex가 상장폐지 종목 214개(과거가격 200,586건) 포함한 point-in-time
    데이터셋(strategy_feature_snapshot_pit_v2, 187,543행/2,691종목)으로 재검증한 결과,
    기존 heuristic_score>=55 로직이 모든 검증구간에서 역신호(lift<1.0)로 확인되어 폐기됨.
    대신 발굴된 5개 신호 중 최강(`earnings_demand`: 20일 순매수 10억+ & 영업이익 100%+ 성장)이
    학습/검증 양쪽 lift>1을 유지했으나, "10배 단독 예측 정밀도"(2.13%)는 목표(15%) 미달로
    Codex는 실전 승격을 보류함(research_candidate_only). 단 "3배 기준"으로는 이미 목표 초과
    (16.31%>15%) — 개별 예측기가 아니라 V-MOONSHOT과 같은 분산 포트폴리오(익절없음+넓은손절+
    긴만기)로 운용하면 실제 수익이 날 수 있는지 별도 검증 필요.

    매수: 분기 영업이익 YoY 성장(as-of 공시일 기준) >= op_growth_min
         + 20일 기관+외국인 순매수 합계 >= supply_min_억 — 최대 max_positions종목 분산.
    매도: 손절 stop_loss(하드) / 추적손절 trail_pct(이익권) / 만료 max_hold거래일.
    """
    init_backtest_db()
    run_name = run_name or f"V-DISCOVERY {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "earnings_supply_discovery", "earnings_supply_discovery_v1_20260811",
        {"op_growth_min": op_growth_min, "supply_min_억": supply_min_억,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'earnings_supply_discovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")

        _mktcap_gate = "" if asof_mktcap else "AND COALESCE(market_cap, 0) >= ?"
        _mktcap_param = [] if asof_mktcap else [min_mktcap_억]
        all_rows = conn.execute(f"""
            SELECT stock_code, stock_name, market_cap FROM stock_universe
            WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              {_mktcap_gate}
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        """, _mktcap_param).fetchall()
        codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
        mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_ed(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o,
                       (COALESCE(inst_net_buy_amt,0) + COALESCE(frn_net_buy_amt,0)) / 100.0 AS supply_억
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            supply_list = [float(r[3] or 0) for r in rows]
            # 20일 롤링 합계(prefix-sum, O(1) 조회용)
            prefix = [0.0]
            for v in supply_list:
                prefix.append(prefix[-1] + v)
            supply_20d = [
                prefix[i + 1] - prefix[max(0, i - 19)]
                for i in range(len(supply_list))
            ]
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'supply_20d': supply_20d,
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        if not sd:
            raise RuntimeError("유니버스가 비어있음(가격이력 부족)")

        # 분기 영업이익 YoY 성장(as-of 공시일 기준) — Codex PIT 연구와 동일 정의
        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, operating_profit
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND operating_profit IS NOT NULL
              AND stock_code IN ({})
            ORDER BY stock_code, year, quarter
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall()
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_rows:
            key = (r[0], r[1], r[2])
            by_quarter.setdefault(key, {})[r[3]] = r
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            r_op = variants.get(pref) or next(iter(variants.values()))
            panel.setdefault(code, []).append((y, q, r_op[4]))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        def _avail_date(y: int, q: int, code: str = None) -> str:
            # 공용 _release_date() 재사용 — 실제 DART 공시일(fin_disclosure_dates) 우선,
            # 없으면 법정기한(분기+45일 근사) fallback. 과거 하드코딩 공식만 쓰던 버그 수정(2026-08-30).
            return _release_date(y, q, False, code)

        # 종목별 (avail_date, op_growth) 이벤트 리스트
        growth_events: Dict[str, list] = {}
        for code, qs in panel.items():
            n = len(qs)
            for i in range(4, n):
                y, q, op = qs[i]
                op_prev = qs[i - 4][2]
                if op is None or op_prev is None or op_prev <= 0:
                    continue
                growth = op / op_prev - 1.0
                if not (-5 <= growth <= 10):  # PIT 연구와 동일 이상치 제외
                    continue
                avail = _avail_date(y, q, code)
                growth_events.setdefault(code, []).append((avail, growth))
        for code in growth_events:
            growth_events[code].sort()

        def _current_growth(code: str, day: str):
            evs = growth_events.get(code)
            if not evs:
                return None
            avail = [e for e in evs if e[0] <= day]
            if not avail:
                return None
            return avail[-1][1]

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
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
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

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = set(pending_buys) if strict_exec else set()
                for code in sd:
                    if code in pos or code in pending_codes:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    curr = sd[code]['c'][i]
                    if curr <= 0:
                        continue
                    if asof_mktcap:
                        _sh = _shares_asof_ed(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    growth = _current_growth(code, day)
                    if growth is None or growth < op_growth_min:
                        continue
                    supply_now = sd[code]['supply_20d'][i]
                    if supply_now < supply_min_억:
                        continue
                    candidates.append((growth, code))
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                if strict_exec:
                    for _, code in picked:
                        pending_buys.append(code)
                else:
                    for _, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}

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
                'entry': p['entry'], 'exit': curr,
                'pnl_pct': net_pct, 'reason': 'final', 'pnl': round(pnl, 0),
            })

        name_map = {}
        all_codes = list({t['code'] for t in trades})
        for i in range(0, len(all_codes), 400):
            batch = all_codes[i:i + 400]
            ph = ",".join("?" * len(batch))
            for sc, sn in conn.execute(
                f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_code IN ({ph})", batch
            ):
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['code'], t['code'])

        total_return = (cash - total_capital) / total_capital * 100
        win_trades = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = (win_trades / len(trades) * 100) if trades else 0.0
        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V-DISCOVERY: op_growth>={op_growth_min*100:.0f}% + supply20d>={supply_min_억}억 / "
            f"손절{stop_loss*100:.0f}% / trail{trail_pct*100:.0f}% / 만기{max_hold}거래일\n"
            f"총 거래: {len(trades)}건  승률: {win_rate:.1f}%  총수익률: {total_return:.2f}%"
        )
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?, total_trades=?,
                profit_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades), win_trades,
            json.dumps({"trades": trades}, ensure_ascii=False), summary_text, run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
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




