"""
patent_catalyst.py -- run_backtest_patent_catalyst()
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

def run_backtest_patent_catalyst(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,
    per_stock: float = 10_000_000,
    dilution_max: int = 3,          # 희석위험(CB/BW/EB/RIGHTS 트레일링365일) 배제 상한 — V-MOONSHOT과 동일 검증된 임계값 재사용
    min_mktcap_억: float = 100,
    stop: float = -0.25,
    trail: float = -0.30,
    trail_activate_pct: float = 0.10,
    max_hold: int = 365,
    asof_mktcap: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-PATENT-CATALYST — 적자기업 특허/기술이전/R&D계약/라이선스 공시 촉매 전략.

    [배경] 2026-08-09 사용자 지시로 251개 10배 종목 중 특허기술이전 카테고리(27개,
    10.8%)를 조사한 결과 이미 81.5% 커버돼 있어 우선순위를 낮췄으나, "완전히 검증되고
    수익률 향상 값을 찾을 때까지 계속하라"는 지시에 따라 라벨 레벨 검증(2026-07-19
    dream_catalyst, TTM 적자모집단 12개월 forward 50%+급등 달성률 학습17.2%/검증25.0%
    vs 무촉매14.9%/14.3%)이 실전 백테스트로 옮겨진 적이 없음을 확인해 이번에 최초 구현.
    독립 재검증(다른 방법론, 반기간격 무작위대조군): 적자모집단 50%+달성률 학습42.3%
    vs 대조35.9%(lift1.18x), 검증41.9% vs 34.8%(lift1.20x) — 학습·검증 방향 일치·재현.
    ⚠️ V-CONTRACT-MOMENTUM(lift 훨씬 강함)보다 약한 신호 — 실전 수익성은 미검증, 이하
    walk-forward로 확인 필요.

    매수: dart_rd_patent_signals(signal_type 4종 patent/tech_transfer/rd_contract/license
         반드시 합산 사용 — 2026-07-19 검증: 유형별 분리는 학습·검증 부호가 뒤집힘)
         공시 + TTM 순이익≤0(적자모집단, as-of) + 희석위험≤dilution_max + 시총≥min_mktcap_억(as-of).
         공시 다음거래일 시가매수, 동일일 복수신호는 시총 내림차순(대형 적자 소형 아님 우선순위 없음 — 균등).
    매도: 손절-25%/추적손절-30%(이익10%+발동)/만기365일.
    """
    init_backtest_db()
    run_name = run_name or f"V-PATENT-CATALYST {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "patent_catalyst", "patent_catalyst_v1_20260809",
        {"dilution_max": dilution_max, "min_mktcap_억": min_mktcap_억, "stop": stop,
         "trail": trail, "max_hold": max_hold, "max_positions": max_positions,
         "per_stock": per_stock, "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'patent_catalyst',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=500)).strftime('%Y-%m-%d')

        events_raw = conn.execute("""
            SELECT stock_code, rcept_dt FROM dart_rd_patent_signals
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """).fetchall()

        codes = sorted({e[0] for e in events_raw})
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

        # as-of TTM 순이익(적자모집단 필터) — financial_data 분기 net_income 누적
        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='preferred_report_type'")}
        raw_fin = conn.execute("""
            SELECT stock_code, year, quarter, report_type, net_income FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL
              AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall() if sd else []
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_fin:
            by_quarter.setdefault((r[0], r[1], r[2]), {})[r[3]] = r[4]
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            ni = variants.get(pref) if pref in variants else next(iter(variants.values()))
            panel.setdefault(code, []).append((y, q, ni))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        def _avail_date(y: int, q: int, code: str = None) -> str:
            # 공용 _release_date() 재사용 — 실제 DART 공시일(fin_disclosure_dates) 우선,
            # 없으면 법정기한(분기+45일 근사) fallback. 과거 하드코딩 공식만 쓰던 버그 수정(2026-08-30).
            return _release_date(y, q, False, code)

        def _ttm_negative(code: str, asof: str) -> bool:
            qs = panel.get(code)
            if not qs or len(qs) < 4:
                return False
            idx = None
            for i, (y, q, ni) in enumerate(qs):
                if _avail_date(y, q, code) <= asof:
                    idx = i
            if idx is None or idx < 3:
                return False
            ttm = sum((qs[j][2] or 0) for j in range(idx - 3, idx + 1))
            return ttm <= 0

        dilution_map: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS')
              AND (risk_event_bucket IS NULL OR risk_event_bucket != 'legacy_non_issuance_event')
              AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())) if sd else []:
            if r[1]:
                dilution_map.setdefault(r[0], []).append(str(r[1])[:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _dilution_risk(code: str, asof: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= asof)

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, ef, et, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(sd))), list(sd.keys())
            ) if sd else []:
                share_intervals.setdefault(code, []).append((ef, et, float(shares or 0), quality))

        def _shares_asof_pc(code: str, day: str) -> float:
            for ef, et, shares, _q in reversed(share_intervals.get(code, [])):
                if ef <= day and (et is None or day < et):
                    return shares
            return 0.0

        buy_pool: Dict[str, list] = {}
        for code, rd in events_raw:
            s = sd.get(code)
            if not s or code not in didx:
                continue
            sig_date = str(rd)[:10]
            pos = None
            for i, d in enumerate(s['d']):
                if d > sig_date:
                    pos = i; break
            if pos is None or pos < 60:
                continue
            avail = sig_date
            if not _ttm_negative(code, avail):
                continue
            if _dilution_risk(code, avail) > dilution_max:
                continue
            entry_date = s['d'][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            if asof_mktcap:
                mc = _shares_asof_pc(code, entry_date)
                if mc <= 0:
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




