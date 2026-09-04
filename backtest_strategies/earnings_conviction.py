"""
earnings_conviction.py -- run_backtest_earnings_conviction()
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

def run_backtest_earnings_conviction(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,      # 2026-07-22(2차) 사용자 지시로 20→10 축소: "비중조절보다 확실한
                                   # 시그널에 더 집중". 종목 수를 줄이고 대신 각 종목의 배분폭을 키움.
    target_slots: int = 18,       # base_ticket = total_capital/target_slots(555만원). 2026-07-22(2차)
                                   # 1차 시도(target_slots=10, weight_cap=6)는 최고점수 종목 단 2개가
                                   # 1억 전액을 흡수해(60M+40M) 나머지 8슬롯이 전부 굶는 사고 실측
                                   # (당일 최고득점자가 이후 최고수익자와 무관 — 결국 순위 1~2위만
                                   # 반영되는 '이진 베팅'으로 변질, avg -0.37%). target_slots를 넓혀
                                   # base_ticket을 낮추고 weight_cap도 낮춰 5~6개 포지션이 동시에
                                   # 자금을 받을 수 있게 재조정 — "균등화는 아니지만 다수 고득점
                                   # 종목이 함께 집중배분 받는" 중간 지점.
    entry_score_min: float = 0.20,  # 진입(자격) 최소 이익 또는 매출 YoY 가속(+20%) — 성장 자체는
                                     # 퍼센트로 확인(순수 규모만으로는 "가속"인지 알 수 없음).
    weight_cap: float = 3.0,       # 랭킹/가중치는 아래 절대증가액 기준(weight_scale_억) 사용, 이 값은
                                   # 그 결과의 상한 배수.
    weight_scale_억: float = 5000,  # 2026-07-22(3차) 핵심 버그수정: 랭킹·가중치를 %성장이 아니라
                                    # **절대 이익/매출 증가액(억원)**으로 전환. %만 쓰면 20억→80억
                                    # (+300%)이 SK하이닉스 4.55조원 증가(+157%)보다 항상 높게 랭크되는
                                    # 근본 결함이 있었음(실측: 아무리 절대이익 하한을 500억→2000억으로
                                    # 올려도 소형주 %폭발이 계속 최상위권 독점 — 경동도시가스 매출이
                                    # 379,440%로 랭킹1위였던 사례). 절대증가액 랭킹으로 전환한 결과
                                    # SK하이닉스가 54개 후보 중 정확히 1위(4조5,505억원 증가)로 확인.
                                    # weight = 1+min(abs_increase_억/weight_scale_억, weight_cap-1) —
                                    # 5,000억원 증가당 1배씩 가중, 최대 weight_cap배에서 상한.
    min_op_profit_억: float = 500,  # 절대 영업이익 규모 하한(억원) — 초소형 기저효과(20억→80억,+300%)가
                                    # SK하이닉스급 진짜 대형가속을 밀어내는 착시 방지(실측 확인 완료).
    min_revenue_억: float = 500,   # 2026-07-22(2차) 신규: 매출 단독 급증 경로의 절대 매출 하한(억원).
                                    # 사용자 지시 "매출도 급격한 증가는 매수 대상" — 이익이 아직 적자/
                                    # 박한 성장초기 기업(고매출성장 SaaS·바이오임상 등)도 매출YoY 자체가
                                    # 강하면 진입 가능하도록 별도 경로 추가.
    revenue_score_min: float = 0.40,  # 매출단독 경로 진입 최소 매출YoY(+40%, 이익경로 20%보다 높게
                                       # 잡음 — 매출만으로는 신호가 약해 더 강한 확인 필요).
    min_mktcap_억: float = 300,
    stop_loss: float = -0.20,
    trail_pct: float = -0.30,
    max_hold: int = 252,
    deteriorate_exit: bool = True,  # 최신 분기 영업이익 YoY가 역성장으로 전환되면 조기청산
                                     # (비중확대의 대칭 원리 — 실적이 나빠지면 확신도 거둬들임).
    asof_mktcap: bool = True,      # 2026-07-27: min_mktcap_억 유니버스 필터가 stock_universe.market_cap
                                    # (현재시총) 정적 컷오프였음(룩어헤드) — as-of 시총으로 전환.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-EARNINGS — 실적가속 집중배분 전략.

    [배경] 2026-07-22 사용자 지적 2건: ①"삼성전자/SK하이닉스가 역대급 이익을 내고 있는데 왜
    편입이 늦는가? 이익의 질이 좋아지는 기업은 비중을 늘려야 하지 않나?" ②"산술평균이 아니라
    수익 극대화가 목표. 점수가 높고 시그널이 확실한 종목에 더 집중해야 한다. 이익뿐 아니라
    매출 급증도 매수 대상." — V1(균등1/N 배분)에서 V2(소수 고확신 종목 집중배분+매출단독 경로)로
    재설계. V1은 6기간 KOSPI/KOSDAQ avg6와 거의 동률(+18.44% vs +18.90%)이었으나 이는 "평균"
    프레이밍일 뿐 — 실제 목표는 절대수익 극대화이므로 배치 내 정규화를 폐지하고 점수 자체의
    절대 크기를 가중치에 그대로 반영, 포지션 수도 20→10으로 줄여 집중도를 높임.

    매수: 가격조건 없이 ①분기 영업이익 YoY 가속(entry_score_min 이상, 매출YoY도 양수, 절대영업이익
         min_op_profit_억 이상) 또는 ②매출 YoY 단독 급증(revenue_score_min 이상, 절대매출
         min_revenue_억 이상 — 이익이 아직 안 나는 고성장 초기기업 포착) 중 더 강한 신호로 진입.
         가중치 = 1+min(score, weight_cap-1) — 정규화 없이 점수가 클수록 계속 커짐(상한 weight_cap).
    매도: 손절(-20%) / 추적손절(-30%, 이익권 한정) / 실적악화청산(최신분기 YoY 역성장 전환 시) /
         보유만료(252일).
    """
    init_backtest_db()
    run_name = run_name or f"V-EARNINGS {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "earnings_conviction", "earnings_conviction_v2_concentrated_20260722",
        {"entry_score_min": entry_score_min, "weight_cap": weight_cap,
         "stop_loss": stop_loss, "trail_pct": trail_pct, "max_hold": max_hold,
         "max_positions": max_positions, "target_slots": target_slots,
         "min_op_profit_억": min_op_profit_억, "min_revenue_억": min_revenue_억,
         "revenue_score_min": revenue_score_min,
         "min_mktcap_억": min_mktcap_억, "deteriorate_exit": deteriorate_exit,
         "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="conviction_weighted_concentrated",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'earnings_conviction',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / target_slots, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")
        # 2026-07-27: as-of 모드에선 유니버스 자체는 시총 무관하게 넓게 잡고(전 KOSPI/KOSDAQ),
        # min_mktcap_억 컷오프는 아래 매수후보 스캔에서 진입일 as-of 시총으로 매일 재확인.
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

        def _shares_asof_ec(code: str, day: str) -> float:
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

        # 분기 실적 이벤트: (avail_date, op_yoy_growth) 리스트. report_type='CFS' 우선(개별종목
        # override는 여기서는 무시 — 매출·영업이익 대표성은 항상 CFS가 낫다는 2026-07-19 확립 원칙 재사용).
        earn_events: Dict[str, list] = {}
        if sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4 AND f.report_type='CFS'
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, f.year, f.quarter
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_events.setdefault(r[0], []).append(
                    {"avail": r[5], "rev": r[1], "op": r[2]})

        def _earn_score(code: str, day: str):
            """가장 최근 as-of 분기의 진입자격+랭킹기준. ①영업이익 YoY가속(매출도 동반양수, 절대
            영업이익 하한) ②매출 YoY 단독급증(절대매출 하한, 이익 요건 없음 — 성장초기 고매출성장
            기업 포착) 중 조건 충족 시, **절대 증가액(억원)**을 랭킹/가중치 기준으로 반환(2026-07-22
            3차: %기준이었을 때 SK하이닉스급 대형 가속이 초소형 %폭발에 랭킹에서 밀리는 근본결함
            발견 — 절대금액 기준으로 전환). 두 경로 모두 충족 시 절대증가액이 더 큰 쪽 채택.
            반환: None(자격없음) 또는 (rank_abs_억, pct, path)."""
            evs = earn_events.get(code)
            if not evs or len(evs) < 5:
                return None
            avail = [e for e in evs if e["avail"] <= day]
            if len(avail) < 5:
                return None
            cur, prev_y = avail[-1], avail[-5]
            op_now, op_1y = cur["op"], prev_y["op"]
            rev_now, rev_1y = cur["rev"], prev_y["rev"]

            cands = []
            if (op_now is not None and op_1y is not None and op_1y > 0
                    and op_now >= min_op_profit_억 * 1e8
                    and rev_now is not None and rev_1y is not None and rev_1y > 0 and rev_now > rev_1y):
                pct = op_now / op_1y - 1
                if pct >= entry_score_min:
                    cands.append(((op_now - op_1y) / 1e8, pct, "profit"))

            if (rev_now is not None and rev_1y is not None and rev_1y > 0
                    and rev_now >= min_revenue_억 * 1e8):
                pct = rev_now / rev_1y - 1
                if pct >= revenue_score_min:
                    cands.append(((rev_now - rev_1y) / 1e8, pct, "revenue"))

            if not cands:
                return None
            return max(cands, key=lambda x: x[0])

        def _weight_mult(rank_abs_억: float) -> float:
            """연속 가중치 — 절대 증가액(억원) 기준. weight_scale_억마다 1배씩 커지다 weight_cap에서
            상한(2026-07-22 3차: %기준 가중치도 함께 절대금액 기준으로 통일)."""
            return 1.0 + min(max(rank_abs_억, 0.0) / weight_scale_억, weight_cap - 1.0)

        base_ticket = total_capital / target_slots
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
                        'weight_mult': p.get('weight_mult', 1.0),
                    })
                pending_sells = _still
                for code, wmult in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(base_ticket * wmult, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억'], 'weight_mult': wmult}
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
                deter_cond = False
                if deteriorate_exit and ret > 0:
                    sc = _earn_score(code, day)
                    if sc is not None and sc[1] < 0:
                        deter_cond = True
                if stop_cond or expire_cond or trail_cond or deter_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'earnings_deteriorate' if deter_cond else 'expire')
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
                            'weight_mult': p.get('weight_mult', 1.0),
                        })
                        pos.pop(code, None)

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = {c for c, _ in pending_buys} if strict_exec else set()
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
                        _sh = _shares_asof_ec(code, day)
                        if _sh <= 0 or _sh * curr / 1e8 < min_mktcap_억:
                            continue
                    r = _earn_score(code, day)
                    if r is None:
                        continue
                    candidates.append((r[0], code))  # r[0] = 절대증가액(억원) — 랭킹/가중치 기준
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                # 2026-07-22(3차) 사용자 지시 반영: "비중조절보다 확실한 시그널에 더 집중" —
                # 배치 내 평균=1.0 정규화(1차 버전)를 폐지하고, **절대 증가액(억원)** 기준 연속
                # 가중치를 그대로 사용(_weight_mult, 1~weight_cap배) — %기준이면 초소형 %폭발이
                # SK하이닉스급 대형가속을 항상 이겨버리는 근본결함이 있어 절대금액으로 전환.
                if strict_exec:
                    for score, code in picked:
                        pending_buys.append((code, _weight_mult(score)))
                else:
                    for score, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        wmult = _weight_mult(score)
                        budget = min(base_ticket * wmult, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억'], 'weight_mult': wmult}

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
                'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                'reason': 'final', 'pnl': round(pnl, 0), 'weight_mult': p.get('weight_mult', 1.0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-EARNINGS 실적가속확신비중 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{len(trades)}건 | 평균:{avg_ret:.1f}%")

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades),
            len([t for t in trades if t['pnl'] > 0]), summary,
            __import__('json').dumps(trades), run_id,
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




