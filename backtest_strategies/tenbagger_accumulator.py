"""
tenbagger_accumulator.py -- run_backtest_tenbagger_accumulator()

V-ACCUM: 텐버거 후보 매집(분할 누적매수) 전략.

[배경] 2026-09-07 사용자 지시: "텐버거 종목을 매집하는 전략은 없어? 만들어봐" — 기존
V-MOONSHOT(moonshot_turnaround.py)은 comprehensive_score(재도전턴어라운드/매출YoY성장/
이익의질, turnaround-watch에서 walk-forward 검증된 0~3점 신호, 2점=lift 1.13~1.23x/
3점=1.38x·검증1.63x)로 후보를 고르지만 진입은 1회 전액매수(one-shot)다. 이 전략은
동일한 검증된 스코어를 그대로 재사용하되, 진입 방식만 바꾼다 — 첫 신호에 전량을 넣지
않고 3트랜치(초기 1/3 + 확신 유지 시 40거래일 간격으로 2회 추가)로 나눠 매집한다.
가설: 텐버거 후보는 변동성이 크고 바닥을 정확히 찍기 어려우므로, 한 시점에 전액
베팅하는 것보다 확신(스코어 유지)이 이어질 때마다 나눠 사면 평단가 리스크를 줄이고
가짜 신호(1회성 스코어 스파이크)에 전액 노출되는 것을 막을 수 있다는 아이디어 —
아직 실측 검증 전이므로 채택/기각 여부는 6기간 백테스트 결과로 판단할 것.

매수: TTM 순이익≤0(적자 모집단, moonshot과 동일) + comprehensive_score≥entry_score_min
     + 희석위험(CB/BW/EB 트레일링365일)≤dilution_max — 최대 max_positions종목,
     각 종목 초기 1/3 트랜치 + tranche_interval_days(기본40일) 간격으로 최대
     max_tranches까지 추가매수(단, 추가 시점에도 score≥entry_score_min 재확인 — 확신이
     식으면 추가매수 중단, 이미 산 것은 보유).
매도: 손절 -35%(가중평균단가 기준) / 추적손절 -35%(이익권) / 만료 500거래일(첫 진입 기준,
     moonshot과 동일 파라미터 — 매도 로직 자체는 바꾸지 않고 진입 방식만 비교하기 위함).
"""
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Dict

from backtest_common import (
    DB_PATH,
    _final_liquidation_quote_for_code,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _release_date,
    init_backtest_db,
    sqlite3,
)


def run_backtest_tenbagger_accumulator(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 30,
    entry_score_min: int = 2,
    dilution_max: int = 3,
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,
    trail_pct: float = -0.35,
    max_hold: int = 500,
    asof_mktcap: bool = True,
    max_tranches: int = 3,          # 초기 1/3 + 최대 2회 추가 = 최대 3트랜치
    tranche_interval_days: int = 40,  # 트랜치 간 최소 간격(거래일)
    include_profitable: bool = False,
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """V-ACCUM: comprehensive_score 확신 유지 시 분할 매집(3트랜치), 그 외 매도/필터는 V-MOONSHOT과 동일."""
    init_backtest_db()
    run_name = run_name or f"V-ACCUM {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "tenbagger_accumulator", "tenbagger_accumulator_v1_20260907",
        {"entry_score_min": entry_score_min, "dilution_max": dilution_max,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "max_tranches": max_tranches, "tranche_interval_days": tranche_interval_days,
         "include_profitable": include_profitable,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket_pyramided",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'tenbagger_accumulator',?,?,?,?,'running')
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
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
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

        def _shares_asof_acc(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o
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
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        if not sd:
            raise RuntimeError("유니버스가 비어있음(가격이력 부족)")

        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, net_income, revenue
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL
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
            r_ni = variants.get(pref) or next(iter(variants.values()))
            r_rev = variants.get("CFS") or r_ni
            panel.setdefault(code, []).append((y, q, r_ni[4], r_rev[5]))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        cf_map: Dict[tuple, dict] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter, report_type, depreciation_q, operating_cf_q
            FROM cash_flow_data WHERE is_annual=0 AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())):
            key = (r[0], r[1], r[2])
            cf_map.setdefault(key, {})[r[3]] = r

        dilution_map: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, disclosed_at FROM dilution_events
            WHERE event_type IN ('CB','BW','EB','RIGHTS')
              AND (risk_event_bucket IS NULL OR risk_event_bucket != 'legacy_non_issuance_event')
              AND stock_code IN ({})
        """.format(",".join("?" * len(sd))), list(sd.keys())):
            if r[1]:
                dilution_map.setdefault(r[0], []).append(str(r[1])[:10])
        for c in dilution_map:
            dilution_map[c].sort()

        def _avail_date(y: int, q: int, code: str = None) -> str:
            return _release_date(y, q, False, code)

        def _dilution_risk(code: str, avail: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(avail, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= avail)

        score_events: Dict[str, list] = {}
        for code, qs in panel.items():
            n = len(qs)
            if n < 8:
                continue
            for i in range(4, n):
                y, q, ni, rev = qs[i]
                avail = _avail_date(y, q, code)
                ttm_now = sum(x[2] or 0 for x in qs[max(0, i-3):i+1])
                if not include_profitable:
                    if ttm_now > 0:
                        continue
                    if ni is not None and ni > 0:
                        continue
                rev_yoy = None
                if i - 4 >= 0 and qs[i-4][3] and qs[i-4][3] >= 1e9 and rev:
                    raw = (rev / qs[i-4][3] - 1) * 100
                    rev_yoy = raw if abs(raw) <= 500 else None
                last_flip = any((qs[j][2] or 0) > 0 for j in range(max(0, i-4), i))
                variants = cf_map.get((code, y, q))
                dep_driven = cash_positive = False
                if variants:
                    r = variants.get("CFS") or next(iter(variants.values()))
                    dep_q, ocf_q = r[4], r[5]
                    dep_driven = bool(dep_q is not None and ni is not None and (ni + dep_q) > 0)
                    cash_positive = bool(ocf_q is not None and ocf_q > 0)
                score = int(last_flip) + int(bool(rev_yoy is not None and rev_yoy > 0)) + int(dep_driven or cash_positive)
                if score >= 1:
                    score_events.setdefault(code, []).append((avail, score))
        for code in score_events:
            score_events[code].sort()

        def _current_score(code: str, day: str):
            evs = score_events.get(code)
            if not evs:
                return None
            avail = [e for e in evs if e[0] <= day]
            if not avail:
                return None
            return avail[-1]

        per_stock = total_capital / max_positions
        tranche_budget = per_stock / max_tranches
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []       # initial-tranche signals (strict_exec queue)
        pending_adds: list = []       # add-on tranche signals (strict_exec queue)

        for day_idx, day in enumerate(sim_dates):
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
                        'tranches_filled': p.get('tranches', 1),
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
                    budget = min(tranche_budget, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억'],
                                 'tranches': 1, 'last_add_idx': day_idx}
                pending_buys = []

                _still_adds = []
                for code in pending_adds:
                    if code not in pos:
                        continue
                    p = pos[code]
                    if p['tranches'] >= max_tranches:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still_adds.append(code); continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(tranche_budget, cash * 0.99)
                    add_shares = int(budget // px)
                    if add_shares <= 0:
                        continue
                    cash -= add_shares * px
                    old_cost = p['entry'] * p['shares']
                    new_shares = p['shares'] + add_shares
                    p['entry'] = (old_cost + add_shares * px) / new_shares
                    p['shares'] = new_shares
                    p['tranches'] += 1
                    p['last_add_idx'] = day_idx
                pending_adds = _still_adds

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
                    if code not in [c for c, _ in pending_sells]:
                        pending_sells.append((code, reason))
                    continue
                # 확신 유지 시 추가 트랜치: 마지막 매수 이후 tranche_interval_days 경과 +
                # 현재도 score>=entry_score_min이면 다음 오픈에 추가매수 신호를 큐에 넣는다.
                if (p['tranches'] < max_tranches
                        and (day_idx - p['last_add_idx']) >= tranche_interval_days
                        and code not in pending_adds):
                    r = _current_score(code, day)
                    if r is not None and r[1] >= entry_score_min:
                        pending_adds.append(code)

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
                        _sh = _shares_asof_acc(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    r = _current_score(code, day)
                    if r is None or r[1] < entry_score_min:
                        continue
                    if _dilution_risk(code, day) > dilution_max:
                        continue
                    candidates.append((r[1], code))
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                for _, code in picked:
                    pending_buys.append(code)

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            curr, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx[code], sd[code]['c'])
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': final_reason, 'pnl': round(pnl, 0), 'tranches_filled': p.get('tranches', 1),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        avg_tranches = (sum(t.get('tranches_filled', 1) for t in trades) / len(trades)) if trades else 0.0
        summary = (f"V-ACCUM 텐버거매집 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}% | 평균트랜치:{avg_tranches:.2f}")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            json.dumps(trades), run_id,
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
