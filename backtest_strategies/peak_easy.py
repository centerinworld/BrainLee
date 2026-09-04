"""
peak_easy.py -- run_backtest_peak_easy()
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

def run_backtest_peak_easy(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.08,             # 2026-07-22 실측 교정: 실제 피크Easy 편출내역 345건 중 손실의 80.8%가
                                      # -8.0%대에 클러스터링(모멘텀Easy와 동일 프레임워크) — 기존 -0.12→-0.08
    trail: float = -0.25,
    top_sectors: int = 2,
    exit_sector_rank: int = 8,
    min_sector_ret20: float = 3.0,
    basket_per_sector: int = 5,
    pct_of_52w_high: float = 0.995,  # 2026-07-22 실측 교정: 사용자 로그인세션으로 확보한 실제 피크Easy
                                      # 편출내역 10건 표본 전수 검증 결과 매수일=정확히 52주 신고가(100.0%)
                                      # — 기존 0.90(근사) 대신 실측치(사실상 그날의 신고가)로 교정
    vol_ratio_min: float = 1.3,      # 거래량 재증가: 5일평균 > 20일평균 x 1.3
    require_rs: bool = True,        # 상대강도: 종목 60일수익률 > KOSPI 60일수익률
    require_earnings_accel: bool = False,  # 실적가속 게이트 (se_momentum과 동일 헬퍼 재사용)
    require_market_uptrend: bool = False,  # 2026-07-22 실험: KOSPI>MA120일 때만 신규진입(약세장 가짜돌파 회피)
    min_mktcap_억: float = 500.0,
    asof_mktcap: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-PEAK -- 스탁이지 "Peak Easy" 전략의 백테스트 재현 (2026-07-22 신규).

    [배경] stockeasy_analyzer.py STRATEGY_ANALYSIS_GUIDES의 실제 정의:
    "52주 신고가/저항 돌파형 전략이다. 신고가권, MA 정배열, 거래량 재증가,
     상대강도, 주도 섹터, 실적 가속을 중심으로 분석" -- 이 6개 요소를 그대로 구현.
    기존 vbr(V8 52W돌파)은 이 중 신고가권·MA정배열·거래량재증가만 있고
    상대강도/주도섹터/실적가속이 빠져 있어 별도 전략으로 신규 구현.

    진입 (전부 충족):
    1) 종목 소속 sector_large가 주도섹터 랭킹(전체멤버 평균ret20) 상위 top_sectors개
    2) 현재가 >= 최근 252일 고점 x pct_of_52w_high (신고가권)
    3) MA20 > MA60 (MA 정배열) & 현재가 > MA20 (추세 위에 위치)
    4) 거래량 재증가: 5일평균 거래량 > 20일평균 x vol_ratio_min
    5) 상대강도: 종목 60일수익률 > KOSPI 60일수익률 (require_rs=True 시)
    6) require_earnings_accel=True 시: 매출/영업이익 YoY 가속 또는 흑자전환 추가 요구

    매도: 손절(stop) / 고점대비 추적손절(trail, 이익5%+ 발동) /
          MA역전(MA20<MA60) / 섹터이탈(히스테리시스 exit_sector_rank)
    -- se_momentum과 동일한 현금원장·D+1 시가체결·as-of 시총 엔진 재사용.
    """
    init_backtest_db()
    run_name = run_name or f"V-PEAK {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "peak_easy", "peak_easy_v1_20260722",
        {"stop": stop, "trail": trail, "top_sectors": top_sectors,
         "exit_sector_rank": exit_sector_rank, "min_sector_ret20": min_sector_ret20,
         "basket_per_sector": basket_per_sector, "pct_of_52w_high": pct_of_52w_high,
         "vol_ratio_min": vol_ratio_min, "require_rs": require_rs,
         "require_earnings_accel": require_earnings_accel,
         "min_mktcap_억": min_mktcap_억, "asof_mktcap": asof_mktcap,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'peak_easy',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')

        sec_map: Dict[str, str] = {}
        name_map: Dict[str, str] = {}
        for r in conn.execute(
            "SELECT stock_code, sector_large, stock_name FROM stock_universe "
            "WHERE sector_large IS NOT NULL AND sector_large!=''"
        ).fetchall():
            sec_map[r[0]] = r[1]
            name_map[r[0]] = r[2]

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
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= ? AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6 AND p.stock_code GLOB '[0-9]*'
            """, (start_date, end_date, min_mktcap_억)).fetchall()
        codes = [(c, m) for c, m in codes if c in sec_map]

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality))

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        kospi_rows = conn.execute(
            "SELECT date, close FROM price_history WHERE stock_code='^KS11' "
            "AND date>=? AND date<=? AND close>0 ORDER BY date",
            (warmup_start, end_date)
        ).fetchall()
        kospi_c: Dict[str, float] = {r[0]: float(r[1]) for r in kospi_rows}
        kospi_dates = sorted(kospi_c.keys())

        def _kospi_ret60(day: str) -> Optional[float]:
            idx = [i for i, d in enumerate(kospi_dates) if d <= day]
            if not idx or idx[-1] < 60:
                return None
            i = idx[-1]
            c0, c60 = kospi_c[kospi_dates[i]], kospi_c[kospi_dates[i-60]]
            return (c0 - c60) / c60 * 100 if c60 > 0 else None

        kospi_ma120: Dict[str, float] = {}
        if require_market_uptrend:
            kv = [kospi_c[d] for d in kospi_dates]
            for i in range(119, len(kospi_dates)):
                kospi_ma120[kospi_dates[i]] = sum(kv[i-119:i+1]) / 120

        def _kospi_uptrend(day: str) -> bool:
            idx = [d for d in kospi_dates if d <= day]
            if not idx:
                return True
            last = idx[-1]
            ma = kospi_ma120.get(last)
            return ma is None or kospi_c[last] > ma

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(open,close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows], 'c': c_list,
                'v': [float(r[2]) for r in rows], 'o': [float(r[3]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        earn_fins: Dict[str, list] = {}
        if require_earnings_accel and sd:
            for r in conn.execute("""
                SELECT f.stock_code, f.revenue, f.operating_profit, f.net_income, f.year, f.quarter,
                       COALESCE(d.avail_date,
                         CASE WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=f.stock_code AND d.year=f.year AND d.quarter=f.quarter AND d.is_annual<1
                WHERE f.is_annual=0 AND f.quarter BETWEEN 1 AND 4
                  AND f.stock_code IN ({})
                ORDER BY f.stock_code, avail_date
            """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall():
                earn_fins.setdefault(r[0], []).append(
                    (r[6], r[1], r[2], r[3], r[4], r[5]))

        def _earnings_accel_ok(code: str, day: str) -> bool:
            fl = earn_fins.get(code)
            if not fl: return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5: return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            if ni_now is not None and ni_now > 0:
                if any(x[3] is not None and x[3] < 0 for x in avail[-4:-1]):
                    return True
            return False

        def _ret20(code: str, day: str) -> Optional[float]:
            i = didx[code].get(day)
            if i is None or i < 20: return None
            c = sd[code]['c']
            return (c[i] - c[i-20]) / c[i-20] * 100 if c[i-20] > 0 else None

        def _sector_ranking(day: str):
            agg: Dict[str, list] = {}
            for code in sd:
                r = _ret20(code, day)
                if r is not None:
                    agg.setdefault(sec_map[code], []).append(r)
            ranked = sorted(
                ((sec, sum(v)/len(v)) for sec, v in agg.items() if len(v) >= 5),
                key=lambda x: -x[1])
            rank_map = {sec: (idx + 1, avg) for idx, (sec, avg) in enumerate(ranked)}
            entry_set = {sec for sec, avg in ranked[:top_sectors] if avg >= min_sector_ret20}
            return entry_set, rank_map

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
                                     'mkt_cap_억': sd[code].get('mkt_cap_억', min_mktcap_억)}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                pending_buys.remove(code)

            top_secs, sec_rank = _sector_ranking(day)

            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None or i < 60: continue
                c = sd[code]['c']
                curr = c[i]
                if curr <= 0: continue
                ret = (curr - p['entry']) / p['entry']
                peak = max(p.get('peak', p['entry']), curr)
                p['peak'] = peak
                ma20 = sum(c[i-19:i+1]) / 20
                ma60 = sum(c[i-59:i+1]) / 60
                sec = sec_map.get(code)
                rk, sec_avg = sec_rank.get(sec, (999, -999.0))
                reason = None
                if ret < stop:
                    reason = 'stop'
                elif trail is not None and ret > 0.05 and (curr - peak) / peak < trail:
                    reason = 'trail'
                elif ma20 < ma60:
                    reason = 'ma_reverse'
                elif rk > exit_sector_rank or sec_avg < 0:
                    reason = 'sector_exit'
                if reason:
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue
            if not top_secs:
                continue
            if require_market_uptrend and not _kospi_uptrend(day):
                continue

            cand = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                if sec_map.get(code) not in top_secs: continue
                nm = name_map.get(code, '')
                if '지주' in nm or '홀딩스' in nm: continue
                i = didx[code].get(day)
                if i is None or i < 252: continue
                c = s['c']
                curr = c[i]
                if curr < 500: continue
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    mc = sh * curr / 1e8
                    if sh <= 0 or mc < min_mktcap_억:
                        continue
                else:
                    mc = s.get('mkt_cap_억', 0)
                ma20 = sum(c[i-19:i+1]) / 20
                ma60 = sum(c[i-59:i+1]) / 60
                if not (ma20 > ma60 and curr > ma20): continue
                high_252 = max(c[i-251:i+1])
                if high_252 <= 0 or curr < high_252 * pct_of_52w_high: continue
                v = s['v']
                v20 = sum(v[i-19:i+1]) / 20
                v5 = sum(v[i-4:i+1]) / 5
                if not (v20 > 0 and v5 > v20 * vol_ratio_min): continue
                if require_rs:
                    ret60 = (curr - c[i-60]) / c[i-60] * 100 if c[i-60] > 0 else None
                    k60 = _kospi_ret60(day)
                    if ret60 is None or k60 is None or ret60 <= k60: continue
                if require_earnings_accel and not _earnings_accel_ok(code, day): continue
                cand.append((mc, code))

            cand.sort(reverse=True)
            per_sec_count: Dict[str, int] = {}
            available = max(0, position_limit - len(pos) - len(pending_buys))
            for mc, code in cand:
                if available <= 0: break
                sec = sec_map[code]
                if per_sec_count.get(sec, 0) >= basket_per_sector: continue
                pending_buys.append(code)
                per_sec_count[sec] = per_sec_count.get(sec, 0) + 1
                available -= 1

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
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': sim_dates[-1],
                               'entry': p['entry'], 'exit': last_c,
                               'pnl_pct': net_pct, 'reason': 'final', 'pnl': round(pnl, 0)})

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
                win_rate=?, total_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2), round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래",
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




