"""
se_momentum.py -- run_backtest_se_momentum()
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
    _chart_bottom_confluence,
    _chart_prep,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_se_momentum(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.08,             # 2026-07-22 채택: 실제 스탁이지 로그인 세션(모멘텀129건/피크 표본)
                                      # 편출내역 실측 결과 손실거래의 60%가 -8.0~-8.14%에 클러스터링(하드손절 확정).
                                      # 6기간 검증 avg6 +22.4%→+27.0%(4/6기간, -0.10/-0.12보다 우수) 확인 후 채택.
    top_sectors: int = 2,            # 주도섹터 상위 N개에서만 편입
    exit_sector_rank: int = 8,       # 편출 히스테리시스: 상위 N위 밖으로 밀려나야 섹터 편출
                                     # 스윕 실측(연속 2020-03~2026-03): 진입=편출 동일(top2) -64.2% →
                                     # rank5 +10.7 → rank8 +45.0. 진입은 좁게/편출은 넓게가 SE 실동작.
                                     # 단 "모멘텀 음전시만 편출"(rank999)은 -45.7% — 승자 반납 참패.
    ma_exit_buffer: float = 0.96,    # MA 역전 청산 버퍼: ma5 < ma20×0.96 (깊은 이탈만) — 0.985는 휩쏘로 -51%
    trail: float = -0.20,            # 고점대비 -20% 추적손절(이익 5%+ 발동) — +45.0→+54.3 개선 확정
    min_sector_ret20: float = 3.0,   # 섹터 평균 ret20 최소값 (%) — 약세장 무리한 편입 방지
    basket_per_sector: int = 5,      # 섹터당 최대 편입 종목 수 (시총 상위 순)
    min_mktcap_억: float = 500.0,
    asof_mktcap: bool = True,
    chart_confluence: bool = False,  # 공통모듈 옵션 (기본 off — SE 로직 자체가 추세 진입)
    require_earnings_accel: bool = True,  # 2026-07-22 채택: 6기간 검증 avg6 +3.6%→+22.4%(4/6기간 대폭개선,
                                          # 최근/AI랠리 2개 기간만 악화) — 스탁이지 실제 정의(실적가속) 반영
    sector_lookback_days: int = 20,  # 2026-07-22 실험: 스탁이지 실제 sector_rs API(로그인불필요) 스냅샷과
                                      # 대조한 결과 우리 ret20 섹터랭킹 TOP10 겹침 1/10뿐 — 252일이 6/10으로 최유사
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-SE 주도섹터 바스켓 (스탁이지 모멘텀 전략의 백테스트 구현, 2026-07-18 신규).

    [배경] 스탁이지(SE) 모멘텀 전략은 stockeasy_logic_validator.py에서 실보유와
    BUY 94~100% 일치까지 재현됐지만(2026-07-11, 전체 이력 기준), 백테스트 전략으로는
    한 번도 구현된 적 없었음 — 사용자 지시("스탁이지 전략 참고, 보유일수 대신
    차트/추세 기반 매도")로 최초 구현.

    진입 (validator v3 로직 충실 포팅):
    1) SE middle 섹터별 "전체 멤버" 평균 ret20 랭킹 → 상위 top_sectors개 주도섹터
       (평균 ret20 >= min_sector_ret20%; 전체멤버 기준 필수 — 통과종목만으로 랭킹하면
        생존자 편향으로 왜곡됨이 실증됨)
    2) 섹터 내 필터: MA5>MA20 + 주가 >= MA20×0.97 + (기관 or 외인 5일 순매수 양수)
       ※ MA20>=MA60 정배열 요구 없음 — SE는 골든크로스 형성 초기에 진입
    3) 통과 종목 중 as-of 시총 상위 basket_per_sector개 편입 (지주/홀딩스 제외)

    매도 (전부 차트/추세 기반 — 보유일수 조건 없음):
    A) MA5 < MA20 하향 (진입 조건 역전 — SE 실증: 달바글로벌 제외 사유와 동일)
    B) 소속 섹터가 주도섹터 랭킹(상위 top_sectors)에서 이탈 (SE의 섹터 일괄 편출 재현)
    C) 안전망 손절 stop (기본 -12%)

    ⚠️ 한계: stockeasy_sector_membership은 현재 시점 분류를 과거에 적용
    (V-SECTOR와 동일 관행 — 섹터 구성 자체는 안정적이나 신규상장 편입 시차 존재).
    """
    init_backtest_db()
    run_name = run_name or f"V-SE주도섹터 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "se_momentum", "se_momentum_v1_20260718",
        {"stop": stop, "top_sectors": top_sectors, "exit_sector_rank": exit_sector_rank,
         "ma_exit_buffer": ma_exit_buffer, "trail": trail, "min_sector_ret20": min_sector_ret20,
         "basket_per_sector": basket_per_sector, "min_mktcap_억": min_mktcap_억,
         "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
         "require_earnings_accel": require_earnings_accel,
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
        VALUES (?,?,'se_momentum',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_days = max(120, int(sector_lookback_days * 1.6) + 30)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=warmup_days)).strftime('%Y-%m-%d')

        # SE 섹터 멤버십 (middle level) — 현재 시점 분류 (한계 docstring 참조)
        se_sector: Dict[str, str] = {}
        for r in conn.execute(
            "SELECT DISTINCT stock_code, sector_name FROM stockeasy_sector_membership "
            "WHERE sector_level='middle'"
        ).fetchall():
            se_sector[r[0]] = r[1]

        # 지주/홀딩스 제외용 이름 맵
        name_map: Dict[str, str] = dict(conn.execute(
            "SELECT stock_code, stock_name FROM stock_universe"
        ).fetchall())

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
        # SE 섹터 분류가 있는 종목만 (전략 유니버스 정의)
        codes = [(c, m) for c, m in codes if c in se_sector]

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

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0),
                       COALESCE(open,close), COALESCE(high,close), COALESCE(low,close),
                       COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 40: continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'frn': [float(r[6]) for r in rows],
                'inst': [float(r[7]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else min_mktcap_억,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 실적가속(이익모멘텀) 게이트 — 2026-07-22: STRATEGY_ANALYSIS_GUIDES의 실제 모멘텀Easy
        # 정의("매출/영업이익 YoY·QoQ 가속, 흑자전환, 이익폭발, 수급전환")를 반영한 실험.
        # 기존 se_momentum은 순수 기술적(MA+섹터+수급) 로직뿐이라 실적 요소가 전무했음.
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
                    (r[6], r[1], r[2], r[3], r[4], r[5]))  # (avail_date, rev, op, ni, year, quarter)

        def _earnings_accel_ok(code: str, day: str) -> bool:
            """실적가속: 최신 공시분기 매출YoY>0 AND 영업이익YoY>0(가속) 이거나, 흑자전환(직전 적자→흑자)."""
            fl = earn_fins.get(code)
            if not fl:
                return False
            avail = [x for x in fl if x[0] <= day]
            if len(avail) < 5:
                return False
            cur, prev_y = avail[-1], avail[-5]
            rev_now, op_now, ni_now = cur[1], cur[2], cur[3]
            rev_1y, op_1y = prev_y[1], prev_y[2]
            rev_yoy_ok = bool(rev_now and rev_1y and rev_1y > 0 and rev_now > rev_1y)
            op_yoy_ok = bool(op_now is not None and op_1y is not None and op_1y > 0 and op_now > op_1y)
            if rev_yoy_ok and op_yoy_ok:
                return True
            # 흑자전환: 최신분기 흑자 + 직전 1~3분기 중 적자 존재
            if ni_now is not None and ni_now > 0:
                prior3 = avail[-4:-1]
                if any(x[3] is not None and x[3] < 0 for x in prior3):
                    return True
            return False

        def _ret20(code: str, day: str) -> Optional[float]:
            i = didx[code].get(day)
            if i is None or i < sector_lookback_days: return None
            c = sd[code]['c']
            lb = sector_lookback_days
            return (c[i] - c[i-lb]) / c[i-lb] * 100 if c[i-lb] > 0 else None

        def _sector_ranking(day: str):
            """당일 기준 섹터 랭킹. 반환: (진입가능 상위섹터 set, 섹터→(순위,평균ret_lookback) dict).
            전체 멤버 평균 ret(sector_lookback_days) 랭킹 — 통과종목만으로 계산 시 생존자 편향(주석 참조)."""
            agg: Dict[str, list] = {}
            for code in sd:
                r = _ret20(code, day)
                if r is not None:
                    agg.setdefault(se_sector[code], []).append(r)
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
        top_secs_cache: Dict[str, set] = {}

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
            top_secs_cache[day] = top_secs

            # ── 매도 체크: 전부 차트/추세 기반 (보유일수 조건 없음) ──
            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None or i < 20: continue
                c = sd[code]['c']
                curr = c[i]
                if curr <= 0: continue
                ret = (curr - p['entry']) / p['entry']
                peak = max(p.get('peak', p['entry']), curr)
                p['peak'] = peak
                ma5 = sum(c[i-4:i+1]) / 5
                ma20 = sum(c[i-19:i+1]) / 20
                sec = se_sector.get(code)
                rk, sec_avg = sec_rank.get(sec, (999, -999.0))
                reason = None
                if ret < stop:
                    reason = 'stop'
                elif trail is not None and ret > 0.05 and (curr - peak) / peak < trail:
                    reason = 'trail'           # 고점대비 추적손절 (이익권에서만)
                elif ma5 < ma20 * ma_exit_buffer:
                    reason = 'ma_reverse'      # 진입조건(MA5>MA20) 역전 — 버퍼로 휩쏘 방지
                elif rk > exit_sector_rank or sec_avg < 0:
                    reason = 'sector_exit'     # 주도섹터 확정 이탈(히스테리시스: 상위권 완전 이탈 or 모멘텀 음전)
                if reason:
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue
            if not top_secs:
                continue

            # ── 진입 후보: 주도섹터 내 SE 필터 통과 종목, 시총 상위 순 ──
            cand = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                if se_sector.get(code) not in top_secs: continue
                nm = name_map.get(code, '')
                if '지주' in nm or '홀딩스' in nm: continue
                i = didx[code].get(day)
                if i is None or i < 25: continue
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
                ma5 = sum(c[i-4:i+1]) / 5
                ma20 = sum(c[i-19:i+1]) / 20
                if ma5 <= ma20: continue
                if curr < ma20 * 0.97: continue
                frn5 = sum(s['frn'][max(0, i-4):i+1])
                inst5 = sum(s['inst'][max(0, i-4):i+1])
                if frn5 <= 0 and inst5 <= 0: continue
                if require_earnings_accel and not _earnings_accel_ok(code, day): continue
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                cand.append((mc, code))

            # 섹터당 상위 basket_per_sector개 (시총 순)
            cand.sort(reverse=True)
            per_sec_count: Dict[str, int] = {}
            available = max(0, position_limit - len(pos) - len(pending_buys))
            for mc, code in cand:
                if available <= 0: break
                sec = se_sector[code]
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




