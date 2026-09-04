"""
meta_v2.py -- run_backtest_meta_v2()
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
    WARMUP_DAYS,
    _ma,
    _score_stock,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_meta_v2(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    score_threshold: int = 65,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    Meta-V 2.0 전략:
      - BULL (KOSPI > MA120): 복합스코어링(65점+)으로 매수
      - BEAR (KOSPI < MA120): V7 흑자전환 단독으로 매수 (MA추세 무관)

    복합스코어링은 상승장/회복장에서 역대 최고 성과.
    V7은 하락장에서 유일하게 양수 (+80.3%).
    두 전략을 레짐에 맞게 조합하여 전 기간 양수 목표.
    """
    init_backtest_db()
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    run_id   = run_id or str(uuid.uuid4())[:8]
    run_name = run_name or f"meta_v2_{start_date[:4]}"
    strategy = "meta_v2"

    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,"
        "per_stock,max_pos,status) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, run_name, strategy, start_date, end_date, per_stock, max_positions, "running"),
    )
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")

        # 시뮬레이션 날짜 (영업일)
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            raise ValueError("시뮬레이션 날짜가 없습니다.")

        # KOSPI 레짐 (히스테리시스 ±2%)
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r["date"]  for r in krows]
            k_prices = [float(r["close"]) for r in krows]
            cur_regime = True
            for ki, kd in enumerate(k_dates):
                kma = _ma(k_prices[max(0, ki-119):ki+1], 120)
                if kma is not None:
                    if cur_regime and k_prices[ki] < kma * 0.98:
                        cur_regime = False
                    elif not cur_regime and k_prices[ki] > kma * 1.02:
                        cur_regime = True
                if kd >= start_date:
                    market_bullish[kd] = cur_regime
        except Exception:
            pass

        # 종목 데이터 로드
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        date_idx:   Dict[str, dict] = {}

        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, volume,
                       COALESCE(frn_net_buy, 0) frn,
                       COALESCE(inst_net_buy, 0) inst
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 120:
                continue
            fin_rows = conn.execute("""
                SELECT f.year, f.quarter, f.revenue, f.operating_profit, f.eps, f.bps,
                       f.total_equity, f.net_income, f.roe, f.is_annual,
                       COALESCE(d.avail_date,
                         CASE WHEN f.is_annual=1 THEN printf('%d-03-31', f.year+1)
                              WHEN f.quarter=1 THEN printf('%d-05-15', f.year)
                              WHEN f.quarter=2 THEN printf('%d-08-15', f.year)
                              WHEN f.quarter=3 THEN printf('%d-11-15', f.year)
                              ELSE printf('%d-02-15', f.year+1) END) as avail_date
                FROM financial_data f
                LEFT JOIN fin_disclosure_dates d ON
                    d.stock_code=? AND d.year=f.year
                    AND d.quarter=CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                    AND d.is_annual=CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
                WHERE f.stock_code=? AND f.report_type IN ('CFS','') AND f.quarter IN (1,2,3,4)
                ORDER BY f.year DESC, f.quarter DESC
            """, (sc, sc)).fetchall()
            if not fin_rows:
                continue
            fin_all = [(r["year"], r["quarter"], r["revenue"], r["operating_profit"],
                        r["eps"], r["bps"], r["total_equity"], r["net_income"],
                        r["roe"], bool(r["is_annual"]), r["avail_date"]) for r in fin_rows]
            dts = [r["date"] for r in rows]
            prs = [float(r["close"]) for r in rows]
            vls = [float(r["volume"]) if r["volume"] else 0.0 for r in rows]
            fns = [float(r["frn"]) if r["frn"] else 0.0 for r in rows]
            ins = [float(r["inst"]) if r["inst"] else 0.0 for r in rows]
            sim_i = next((j for j, d in enumerate(dts) if d >= start_date), len(dts))
            stock_data[sc] = {
                'dates': dts, 'prices': prs, 'volumes': vls,
                'frn': fns, 'inst': ins, 'fins': fin_all, 'sim_start_i': sim_i,
            }
            date_idx[sc] = {d: j for j, d in enumerate(dts)}

        conn.row_factory = None

        # 시뮬레이션
        capital = 0.0
        positions: Dict[str, dict] = {}
        trades: List[dict] = []
        bull_days = 0
        bear_days = 0
        prev_bull = True   # 초기 상태: BULL

        for si, day in enumerate(sim_dates):
            is_bull = market_bullish.get(day, True)
            if is_bull:
                bull_days += 1
            else:
                bear_days += 1

            # BULL→BEAR 전환 감지: 전 포지션 즉시 강제 청산
            if prev_bull and not is_bull and positions:
                for sc, pos in list(positions.items()):
                    im = date_idx.get(sc, {})
                    if day in im:
                        i    = im[day]
                        curr = stock_data[sc]['prices'][i]
                        ep   = pos['entry_price']
                        ret  = (curr - ep) / ep
                        capital += ret * per_stock
                        trades.append({
                            'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                            'entry_price': ep, 'exit_price': curr,
                            'return_pct': ret * 100, 'pnl': ret * per_stock,
                            'reason': 'BEAR전환강제청산', 'score': pos.get('score', 0),
                            'mode': 'bull',
                        })
                positions.clear()
            prev_bull = is_bull

            # BULL 구간: 일반 매도 체크 (익절/손절/MA60붕괴)
            if is_bull:
                to_sell = []
                for sc, pos in positions.items():
                    sd  = stock_data[sc]
                    im  = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i    = im[day]
                    curr = sd['prices'][i]
                    if curr <= 0:
                        continue
                    ep   = pos['entry_price']
                    ret  = (curr - ep) / ep
                    held = si - next((j for j, d in enumerate(sim_dates) if d == pos['entry_date']), si)
                    take = pos.get('take_profit', 0.25)
                    stop = pos.get('stop_loss', -0.10)

                    exit_reason = None
                    if ret >= take:
                        exit_reason = f"익절{take*100:.0f}%"
                    elif ret <= stop:
                        exit_reason = f"손절{stop*100:.0f}%"
                    elif held > 5:
                        ma60_e = _ma(sd['prices'][max(0, i-59):i+1], 60)
                        if ma60_e and curr < ma60_e:
                            exit_reason = "MA60붕괴"
                        # 240일 장기횡보 보류: 하락장 -1.3%→-25.3% 악화로 미적용

                    if exit_reason:
                        pnl = ret * per_stock
                        capital += pnl
                        trades.append({
                            'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                            'entry_price': ep, 'exit_price': curr,
                            'return_pct': ret * 100, 'pnl': pnl,
                            'reason': exit_reason, 'score': pos.get('score', 0),
                            'mode': pos.get('mode', 'bull'),
                        })
                        to_sell.append(sc)
                for sc in to_sell:
                    del positions[sc]

            # 매수: BULL 구간에서만 복합 스코어링 진입 (BEAR=현금 보유)
            if is_bull and len(positions) < max_positions:
                candidates = []
                for sc, sd in stock_data.items():
                    if sc in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    s = _score_stock(
                        i, sd['sim_start_i'], sd['dates'], sd['prices'],
                        sd['volumes'], sd['frn'], sd['inst'], sd['fins'])
                    if s >= score_threshold:
                        candidates.append((s, sc))
                candidates.sort(key=lambda x: -x[0])
                for s, sc in candidates:
                    if len(positions) >= max_positions:
                        break
                    sd   = stock_data[sc]
                    i    = date_idx[sc][day]
                    curr = sd['prices'][i]
                    if curr <= 0:
                        continue
                    # 점수 기반 익절
                    take_p = 0.40 if s >= 80 else 0.30 if s >= 70 else 0.20
                    positions[sc] = {
                        'entry_date': day, 'entry_price': curr,
                        'qty': max(1, int(per_stock / curr)),
                        'score': s, 'take_profit': take_p, 'stop_loss': -0.10,
                        'mode': 'bull',
                    }

        # 미청산 강제 청산
        last_day = sim_dates[-1]
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            if last_day in im:
                i    = im[last_day]
                curr = sd['prices'][i]
                ep   = pos['entry_price']
                ret  = (curr - ep) / ep
                capital += ret * per_stock
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': last_day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': ret * 100, 'pnl': ret * per_stock,
                    'reason': '기간종료', 'score': pos.get('score', 0),
                    'mode': pos.get('mode', 'bull'),
                })

        # 집계
        total_trades  = len(trades)
        winners       = [t for t in trades if t['return_pct'] > 0]
        losers        = [t for t in trades if t['return_pct'] <= 0]
        win_rate      = len(winners) / total_trades * 100 if total_trades else 0
        total_invested = per_stock * max_positions
        total_ret_pct = capital / total_invested * 100 if total_invested else 0
        avg_win  = sum(t['return_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['return_pct'] for t in losers) / len(losers)   if losers  else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        days = len(sim_dates)
        yrs  = days / 252
        cagr = ((1 + capital / total_invested) ** (1 / yrs) - 1) * 100 if yrs > 0 and total_invested > 0 else 0

        summary = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ Meta-V 2.0: BULL={bull_days}일(복합스코어링+{score_threshold}점) / BEAR={bear_days}일(현금보유)\n"
            f"총 거래: {total_trades}건  승률: {win_rate:.1f}%  Profit Factor: {pf:.2f}\n"
            f"avg 수익: {avg_win:+.1f}%  avg 손실: {avg_loss:+.1f}%\n"
            f"CAGR: {cagr:.2f}%  총수익: {total_ret_pct:+.1f}%\n"
        )

        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, summary_text=?, trades_json=?, strategy=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, total_trades,
              summary, json.dumps(trades, ensure_ascii=False), strategy, run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        return run_id

    except Exception as e:
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        conn2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
        conn2.commit()
        conn2.close()
        conn.close()
        raise


# ══════════════════════════════════════════════════════════════
#  골든크로스 모멘텀 전략 (V-GC)
#
#  실증 근거 (2020~2026, 6기간 검증):
#    5/6 기간 KOSPI 아웃퍼폼, 평균 알파 +37.4%
#    하락장 -1.4% (KOSPI -22.9% 대비 +21.5%α) ← 핵심 강점
#
#  진입 조건:
#    [A] MA20 > MA60 (단기 정배열)
#    [B] 최근 15일 내 MA20이 MA60을 골든크로스
#    [C] 5일 평균거래량 > 20일 평균거래량 × 1.2 (거래량 확인)
#    [D] RS6M(6개월 KOSPI 대비 상대강도) > -20
#    [E] 최근 5일 중 1일이라도 ±50% 이상 등락 없음 (분할/합병 미조정 제거)
#
#  정렬: RS6M 내림차순 (상대강도 높은 종목 우선 진입)
#
#  매도 조건 (피크이지/모멘텀이지):
#    손절: -12% 이하 (갭리스크 감안)
#    Trail25%: 이익 5%+ 달성 후 고점대비 -25% 하락 시 매도
#    Trail30%: 이익 50%+ 달성 시 고점대비 -30% (대박 종목 홀드 연장)
#    만료: 300거래일 초과
# ══════════════════════════════════════════════════════════════


