"""
v8.py -- run_backtest_v8()
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
    EMP_DB_PATH,
    _calc_metrics,
    _date_to_ym,
    _get_export_yoy,
    _get_financial_as_of,
    _load_trade_signals,
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

def _load_employment_signals() -> Dict[str, Dict[str, int]]:
    """
    고용보험 DB에서 종목별 연도별 고용인원 로드.
    반환: {stock_code: {ym: worker_count}} (예: {'000660': {'2024-12': 21493, ...}})
    ym 형식: 'YYYY-MM' (대부분 12월 = 연말 기준)
    """
    try:
        conn = sqlite3.connect(EMP_DB_PATH, timeout=10)
        rows = conn.execute("""
            SELECT stock_code, ym, worker_count
            FROM employment_company
            WHERE worker_count IS NOT NULL AND worker_count > 0
            ORDER BY stock_code, ym
        """).fetchall()
        conn.close()
        result: Dict[str, Dict[str, int]] = {}
        for sc, ym, cnt in rows:
            result.setdefault(sc, {})[ym] = int(cnt)
        return result
    except Exception:
        return {}




def _run_backtest_v8(conn, warmup_start, start_date, end_date, sim_dates,
                     per_stock, max_positions,
                     stop_loss_pct, take_profit_pct, max_hold_days,
                     strict_exec: bool = True):
    """
    V8 선행지표 멀티팩터 포트폴리오 시뮬레이터.

    매수 조건 (선행지표 기반 — '싸게 사서 비싸게 판다'):
      [A] 수출 YoY 가속: 최근 2개월 수출 YoY 모두 > 10%
                         + 최근 3개월 YoY 평균 > 직전 3개월 YoY 평균 (가속 확인)
      [B] 가격 선진입:   현재가 < MA60 × 1.15 (강한 상승 추세 진입 전, 바닥권~초기)
                         OR 52주 고점 대비 -30% 이상 하락
      [C] 재무 건전:     최근 공시 영업이익 > 0 (적자 기업 제외)
      [D] 고평가 제외:   BPS > 0 이면 PBR < 4.0
      [E] 시장 필터:     KOSPI > MA120 (하락장 매수 금지)
      [F] 고용 보조:     연간 고용 YoY > 3% (데이터 있을 때만 추가 점수)

    매도 조건:
      ① 익절: +take_profit_pct
      ② 손절: -stop_loss_pct
      ③ 추적 손절: 고점 대비 -15% (수익 구간에서만)
      ④ MA60 붕괴 (최소 5일 보유 후)
      ⑤ 최대 보유 max_hold_days 초과 시 강제 청산 (선행지표 미실현 대비)
    """
    # ── 수출 + 고용 데이터 로드 ────────────────────────────────
    trade_all = _load_trade_signals()    # {sc: {ym: export_val}}
    emp_all   = _load_employment_signals()  # {sc: {ym: worker_cnt}}

    # ── KOSPI 시장 필터 ────────────────────────────────────────
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates      = [r[0] for r in kospi_rows]
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish: Dict[str, bool] = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # ── 재무 데이터 로드 ────────────────────────────────────────
    fin_all: Dict[str, list] = {}
    for r in conn.execute("""
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
        WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 4)
           OR (f.is_annual=1)
        ORDER BY f.stock_code, f.year, f.quarter
    """).fetchall():
        sc = r[0]
        fin_all.setdefault(sc, []).append(r[1:])

    # ── 종목 목록: 수출 데이터 있는 종목만 (trade_all 키셋)
    #    + price_history에서 충분한 데이터 보유 확인 ─────────────
    export_stocks = set(trade_all.keys())
    stock_codes = [r[0] for r in conn.execute("""
        SELECT ph.stock_code, COUNT(*) AS cnt
        FROM price_history ph
        INNER JOIN (
            SELECT stock_code FROM stock_universe
            WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        ) su ON ph.stock_code = su.stock_code
        WHERE ph.date>=? AND ph.date<=? AND ph.close>0
        GROUP BY ph.stock_code HAVING COUNT(*) >= 200
    """, (warmup_start, end_date)).fetchall()
    if r[0] in export_stocks]

    # ── 종목별 가격 데이터 로드 ────────────────────────────────
    stock_data: Dict[str, dict] = {}
    for sc in stock_codes:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0),
                   COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0),
                   COALESCE(open, close)
            FROM price_history
            WHERE stock_code=? AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (sc, warmup_start, end_date)).fetchall()
        if len(rows) < 200:
            continue
        dates   = [r[0] for r in rows]
        prices  = [float(r[1]) for r in rows]
        volumes = [float(r[2]) for r in rows]
        frn     = [float(r[3]) for r in rows]
        inst    = [float(r[4]) for r in rows]
        opens   = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) for r in rows]

        # ── 데이터 품질 필터: 단일일 50% 이상 급등락 종목 제외
        # (두 시리즈 혼재, 권리락 미반영 등 데이터 오염 방지)
        bad_data = False
        for pi in range(1, len(prices)):
            if prices[pi - 1] > 0:
                ratio = prices[pi] / prices[pi - 1]
                if ratio > 3.0 or ratio < 0.20:   # 하루 3배 이상 또는 80% 이상 하락
                    bad_data = True
                    break
        if bad_data:
            continue

        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {
            'dates': dates, 'prices': prices, 'volumes': volumes, 'opens': opens,
            'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
            'sim_start_i': sim_start_i,
            'trade': trade_all.get(sc, {}),   # 이 종목의 월별 수출액
            'emp':   emp_all.get(sc, {}),     # 이 종목의 연별 고용인원
        }

    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    total_capital = per_stock * max_positions
    # 2026-07-16 개선: 고정슬롯 P&L 누산 → 실제 현금원장. 매수 시 가용현금 검사+차감,
    # 부족 시 주문 거부(cash 음수 금지). equity_curve도 cash+마킹포지션 기준으로 정정.
    cash = total_capital
    positions: Dict[str, dict] = {}
    trades:    list = []
    equity_curve: list = []

    def _check_sell_v8(i, prices, pos, trade_sc=None, d=None):
        """
        V8 매도: 선행지표 기반 보유 전략
        - 수출 YoY 이 여전히 양호하면 MA60 붕괴에도 보유 (선행지표 우선)
        - 수출이 꺾이거나 손절선 도달 시 청산
        """
        curr = prices[i]
        if curr > pos.get('peak_price', pos['entry_price']):
            pos['peak_price'] = curr
        pct       = (curr - pos['entry_price']) / pos['entry_price']
        peak      = pos.get('peak_price', pos['entry_price'])
        hold_days = pos.get('hold_days', 0)
        pos['hold_days'] = hold_days + 1

        # 익절 (항상 우선)
        if pct >= take_profit_pct:
            return f"익절(+{pct*100:.0f}%)"

        # 하드 손절 (항상 우선)
        if pct <= -stop_loss_pct:
            return f"손절(-{stop_loss_pct*100:.0f}%)"

        # 추적 손절: 고점 대비 -15% (수익 구간, 5일 이상 보유)
        if hold_days >= 5 and pct > 0.05:
            trail = (curr - peak) / peak if peak > 0 else 0
            if trail <= -0.15:
                return f"추적손절(고점-{abs(trail)*100:.0f}%)"

        # 수출 선행지표 소멸 체크 — 진입조건 역전 시 청산
        # 진입조건: yoy1 > 2%(양전환). 소멸: 최근 달 음전환 OR 이전 달도 부진
        if hold_days >= 25 and trade_sc is not None and d is not None:
            ref_ym1 = _date_to_ym(d, lag_months=2)
            ref_ym2 = _date_to_ym(d, lag_months=3)
            yoy1 = _get_export_yoy(trade_sc, ref_ym1)
            yoy2 = _get_export_yoy(trade_sc, ref_ym2)
            if yoy1 is not None:
                # 케이스 A: 최근 달 명확히 음전환 → 변곡점 소멸
                if yoy1 < -3:
                    return "수출역전청산(V9)"
                # 케이스 B: 최근 달 보합이고 이전 달도 음전환 → 전환 실패
                if yoy1 < 2 and yoy2 is not None and yoy2 < 0:
                    return "수출전환실패청산(V9)"

        # 장기횡보 안전망 (시간기반 대신 — 수익 없이 오래 끌면 기회비용)
        if hold_days >= 240 and -0.05 < pct < 0.15:
            ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
            if ma20 and curr < ma20:
                return f"장기횡보청산(V9,{hold_days}일)"

        # MA200 붕괴 (장기 하락 구조 편입, 30일 이상 보유)
        if hold_days >= 30:
            ma200 = _ma(prices[max(0, i - 199): i + 1], 200)
            if ma200 is not None and curr < ma200 * 0.92:
                return "MA200 붕괴"

        return None

    def _is_buy_v8_signal(sc, sd, i, d):
        """
        V8 매수 시그널 v2 — '수출 선행 + 일시 눌림목 매수'

        철학: 수출이 증가하는 기업이 단기 조정 구간에 있을 때 매수.
              → 장기 추세(MA200)는 살아 있으나 단기적으로 MA60 이하 또는 근접
              → 수출 데이터(선행 2개월)가 YoY 회복/성장 구간
              → RSI 35~60 (과매도 후 회복 초기)
        """
        if i < sd['sim_start_i']:
            return False
        curr   = sd['prices'][i]
        trade  = sd['trade']
        emp    = sd['emp']
        fins   = sd['fins']

        # ══ [A] 수출 YoY 진짜 변곡점 포착 — 음수→양수 전환 ══════
        # 철학: "수출 선행"의 진짜 선행 = 아직 주가에 반영 안 된 전환 순간
        #   기존 문제: yoy1>8% AND yoy2>8% = "확인된 성장" → 주가 이미 반영 후 진입
        #   수정: 최근 수출 양성 전환 + 이전엔 부진(진짜 변곡) + 주가 미반영(MA60 근처)
        ref_ym1 = _date_to_ym(d, lag_months=2)   # 가장 최근 공표된 달
        ref_ym2 = _date_to_ym(d, lag_months=3)   # 그 전달
        ref_ym3 = _date_to_ym(d, lag_months=4)   # 3개월 전
        ref_ym4 = _date_to_ym(d, lag_months=5)   # 4개월 전
        ref_ym5 = _date_to_ym(d, lag_months=6)   # 5개월 전

        yoy1 = _get_export_yoy(trade, ref_ym1)
        if yoy1 is None:
            return False

        # [A-1] 최근 수출 YoY 양성(최소 +2%) — 음수면 진입 불가
        if yoy1 < 2:
            return False

        # [A-2] 진짜 변곡점 확인: 이전 달들 중 부진(≤2%) 또는 음수가 있어야 함
        #   케이스 A(역전): 이전 3~4개월 중 0% 이하 있고 최근 전환 → 진짜 선행 매수
        #   케이스 B(급가속): 이전 대비 15%p 이상 급개선 → 예상 못 한 서프라이즈
        #   케이스 C(지속 성장) 제거 — 이미 주가에 반영된 상태 → 사후 추종
        yoy2 = _get_export_yoy(trade, ref_ym2)
        yoy3 = _get_export_yoy(trade, ref_ym3)
        yoy4 = _get_export_yoy(trade, ref_ym4)
        yoy5 = _get_export_yoy(trade, ref_ym5)
        older = [y for y in [yoy2, yoy3, yoy4] if y is not None]
        if not older:
            return False
        avg_older   = sum(older) / len(older)
        had_negative = any(y <= 2 for y in older)   # 이전에 부진/음수 있었는가
        accelerated  = yoy1 >= avg_older + 15        # 갑작스러운 급개선
        if not had_negative and not accelerated:
            return False   # 케이스 C(지속 성장) 진입 차단 ← 기존 부진 원인

        # ══ [B] 가격 구조: 장기 추세 안에서 단기 눌림 ══════════
        # 철학: 수출이 이미 성장 중이고, 주가가 이제 막 MA60을 돌파하는 순간 매수
        #       → 너무 이른 진입(MA60 아래) X, 너무 늦은 진입(Minervini 완성) X
        p_all = sd['prices'][max(0, i - 250): i + 1]
        if len(p_all) < 120:
            return False
        ma60  = sum(p_all[-60:]) / 60
        ma120 = sum(p_all[-120:]) / 120
        ma200 = sum(p_all) / len(p_all) if len(p_all) >= 200 else None

        # [B-1] 현재가 MA60 위에 있어야 함 (실증: MA60아래=0.87x 불리)
        # 이전: MA60 근처 또는 아래 허용 → 실증에서 MA아래는 음의 신호
        # 수정: MA60 위 + 너무 과열 아닌 수준 (MA60의 130% 이하)
        if curr < ma60:
            return False  # MA60 아래 → 실증적으로 불리
        if curr > ma60 * 1.20:
            return False  # 이미 주가 반영 구간(MA60+20% 초과) — 선행성 없음

        # [B-2] MA60이 10일 전보다 높거나 같다 (MA60 방향 전환 또는 상승 중)
        if i >= 10:
            ma60_10ago = sum(sd['prices'][max(0, i - 69): i - 9]) / 60 \
                         if (i - 9) >= 60 else None
            if ma60_10ago is not None and ma60 < ma60_10ago * 0.99:
                return False  # MA60 여전히 하락 중

        # [B-3] 장기 하락 구조 제외: MA120 > MA200 * 0.92 (장기 상승 구조)
        if ma200 is not None and ma120 < ma200 * 0.92:
            return False

        # [B-4] 52주 고점 대비 -35% 이상 하락이면 너무 큰 낙폭 → 제외
        high52 = max(sd['prices'][max(0, i - 251): i + 1])
        if high52 > 0 and curr < high52 * 0.65:
            return False

        # ══ [C] RSI 42~80 (실증: RSI>70=1.25x 양호, 상한 완화) ═════
        rsi_val = _rsi(p_all[-29:] if len(p_all) >= 29 else p_all)
        if rsi_val is None or rsi_val < 42 or rsi_val > 80:
            return False

        # ══ [D] 재무 건전: 영업이익 > 0 ═════════════════════
        fin = _get_financial_as_of(fins, d)
        if fin is not None:
            _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin
            if op is not None and op <= 0:
                return False  # 영업적자 제외
            if bps and bps > 0 and curr / bps > 6.0:
                return False  # 극단적 고평가(PBR>6) 제외

        # ══ [E] 고용 보조: 연간 고용 감소(-5% 이상) 기업 제외 ══
        if emp:
            emp_sorted = sorted(
                [(ym, cnt) for ym, cnt in emp.items() if ym <= d[:7]],
                reverse=True
            )
            if len(emp_sorted) >= 2:
                (y1, cnt1), (_, cnt2) = emp_sorted[0], emp_sorted[1]
                if cnt2 > 0 and (cnt1 - cnt2) / cnt2 * 100 < -5.0:
                    return False

        return True

    v8_pending_sells: list = []  # (sc, reason)
    v8_pending_buys: list = []   # sc

    for day in sim_dates:
        # ── strict_exec: 전일 신호 → 오늘 시가 체결 (Codex 계약) ──
        if strict_exec:
            _still = []
            for sc, reason in v8_pending_sells:
                if sc not in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    _still.append((sc, reason)); continue
                i = im[day]
                px = stock_data[sc]['opens'][i]
                pos = positions.pop(sc)
                _pnl_amt, _net_pct = _net_profit(pos['entry_price'], px, pos['qty'], pos.get('mkt_cap_억', 500))
                cash += pos['qty'] * pos['entry_price'] + _pnl_amt
                trades.append({
                    'stock_code':  sc,
                    'entry_date':  pos['entry_date'],
                    'exit_date':   day,
                    'entry_price': pos['entry_price'],
                    'exit_price':  px,
                    'qty':         pos['qty'],
                    'profit_pct':  _net_pct,
                    'profit_amt':  _pnl_amt,
                    'exit_reason': reason,
                })
            v8_pending_sells = _still
            for sc in v8_pending_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                px = stock_data[sc]['opens'][i]
                if px <= 0:
                    continue
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / px)
                if qty < 1 or qty * px > cash:
                    continue  # 현금 부족 → 주문 거부
                cash -= qty * px
                positions[sc] = {
                    'entry_date': day, 'entry_price': px,
                    'qty': qty,
                    'peak_price': px, 'hold_days': 0,
                }
            v8_pending_buys = []

        # 매도 체크
        sold = []
        for sc, pos in list(positions.items()):
            im = date_idx.get(sc, {})
            if day not in im:
                continue
            i      = im[day]
            sd     = stock_data[sc]
            reason = _check_sell_v8(i, sd['prices'], pos, trade_sc=sd['trade'], d=day)
            if reason is None:
                continue
            if strict_exec:
                if not pos.get('pending_exit'):
                    pos['pending_exit'] = reason
                    v8_pending_sells.append((sc, reason))
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            _pnl_amt, _net_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
            cash += pos['qty'] * pos['entry_price'] + _pnl_amt
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  curr,
                'qty':         pos['qty'],
                'profit_pct':  _net_pct,
                'profit_amt':  _pnl_amt,
                'exit_reason': reason,
            })
            sold.append(sc)
        for sc in sold:
            del positions[sc]

        # 시장 필터 (하락장 매수 금지)
        if market_bullish and not market_bullish.get(day, True):
            marked = sum(
                stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            equity_curve.append({'date': day, 'equity': round(cash + marked)})
            continue

        # 매수 스캔
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
                if not _is_buy_v8_signal(sc, sd, i, day):
                    continue
                if strict_exec:
                    if sc not in v8_pending_buys and \
                       len(positions) + len(v8_pending_buys) < max_positions:
                        v8_pending_buys.append(sc)
                    continue
                curr = sd['prices'][i]
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

        # 에쿼티 커브 (현금원장 기준)
        marked = sum(
            stock_data[sc]['prices'][date_idx[sc][day]] * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        equity_curve.append({'date': day, 'equity': round(cash + marked)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        sd  = stock_data[sc]
        im  = date_idx.get(sc, {})
        curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
        pct  = (curr - pos['entry_price']) / pos['entry_price']
        _pnl_amt, _net_pct = _net_profit(pos['entry_price'], curr, pos['qty'], pos.get('mkt_cap_억', 500))
        cash += pos['qty'] * pos['entry_price'] + _pnl_amt
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  _net_pct,
            'profit_amt':  _pnl_amt,
            'exit_reason': '기간종료',
        })

    return trades, equity_curve, len(stock_data), market_bullish, cash




def run_backtest_v8(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    run_name: str = None, run_id: str = None) -> str:
    """
    V8 수출 선행지표 멀티팩터 백테스트.
    HS 무역통계(월별 수출 YoY) + 고용 데이터를 선행 신호로 활용.
    """
    init_backtest_db()
    run_name = run_name or f"V8 수출선행 {start_date[:7]}~{end_date[:7]}"
    _v8_params = {"per_stock": per_stock, "max_positions": max_positions,
                  "stop_loss_pct": 0.10, "take_profit_pct": 0.30, "max_hold_days": 252,
                  "strict_exec": True, "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v8',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    # 2026-07-27: 코드 재확인 결과 v8은 애초에 시총 기반 유니버스 필터를 전혀
    # 쓰지 않음(종목선정은 수출데이터 보유여부(trade_all 키셋)만 기준, mkt_cap_억은
    # _net_profit 슬리피지 계산에만 쓰이고 항상 기본값 500 — positions에 실제 시총이
    # 저장된 적이 없음). "current"라는 라벨은 실제로 존재하지 않는 현재시총 룩어헤드를
    # 있는 것처럼 오기술한 것이므로 megatrend/earnings_conviction과 동일하게
    # "not_applicable"로 정정(as-of 리트로핏 대상 아님 — 고칠 시총필터 자체가 없음).
    _record_run_spec(run_id, "v8", "v8_v2_strict_20260714", _v8_params,
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode="not_applicable", allocation_rule="fixed_slot")
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
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

        trades, equity_curve, n_stocks, market_bullish, final_cash = _run_backtest_v8(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss_pct=0.10,      # 선행 매수 → 넓은 손절 허용
            take_profit_pct=0.30,    # 선행 매수 → 충분한 상승 기다림
            max_hold_days=252,       # 최대 1년 보유 (선행지표 실현 대기)
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  수출데이터 종목수: {n_stocks}\n"
            f"★ V8 수출선행: HS무역통계 월별수출 YoY>10%(2개월연속) + 가격선진입(MA60근처) + 영업이익>0\n"
            f"  매개변수: 익절+30% / 손절-10% / 추적손절-15% / 최대보유252일 / KOSPI MA120 필터\n"
            f"하락장 차단: {bear_days}/{total_days}일({filter_pct}%)\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"손익비: {metrics['pl_ratio']}배  총손익: {metrics.get('total_profit_amt',0):,}원\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'strategy': 'v8',
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        _register_execution_artifacts(run_id, total_capital, final_cash)
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


# ══════════════════════════════════════════════════════════════
#  레짐 적응형 전략: BULL→V1 MA추세, BEAR→V7 흑자전환 자동 전환
# ══════════════════════════════════════════════════════════════


