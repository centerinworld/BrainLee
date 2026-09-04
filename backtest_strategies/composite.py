"""
composite.py -- run_backtest_composite()
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
    _corp_action_adjusted_entry,
    _load_corp_action_factors,
    _ma,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _score_stock,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_composite(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    score_threshold: int = 60,
    run_name: str = None,
    run_id: str = None,
    use_event_bonus: bool = True,  # 2026-08-30: 6기간 walk-forward 검증 통과(avg6 +9.47%→+10.95%, 4/6기간 개선) — 기본값 채택
    data_asof_ts: str = None,
) -> str:
    """
    복합 스코어링 전략 (V10 선택적 복합 시그널).

    핵심: 100점 스코어에서 threshold(기본 60점) 이상인 종목만 매수.
    60점 달성 = 최소 3가지 독립 조건 동시 충족.

    동적 익절: 점수 60~69 → +20%, 70~79 → +30%, 80+ → +40%
    손절: -10% 고정
    MA60 붕괴 시 즉시 매도

    use_event_bonus=True: _score_stock()에 특허/기술이전(+3)·자사주매입(+2)·소각(+3)·
    희석위험(-3~-6) 이벤트 보정을 추가 반영 — 2026-08-30 세션에서 신규 도입,
    walk-forward(6기간) 검증 통과 후 기본값 True로 채택. avg6 +9.47%→+10.95%,
    4/6기간 개선(20.3~21.11/22.11~23.10/23.11~24.12/21.12~22.10 개선,
    24.6~25.5/25.6~26.3 소폭 악화 — 정직하게 트레이드오프 존재).

    data_asof_ts: 2026-09-04 신규. turnaround/regime_adaptive와 동일하게
    corporate_action_events.adjustment_status(매일 00:10 확정 잡)와 financial_data
    (매일 00:05 재검증 잡)가 실행 중에도 계속 갱신되므로, 같은 과거 구간을 재실행할
    때 결과가 흔들릴 수 있는 잠재 위험이 이 전략에도 동일하게 존재한다(현금원장에
    조정 진입가가 직접 반영됨). 'YYYY-MM-DD HH:MM:SS'를 주면 그 시각 기준 데이터로
    고정해 재실행해도 항상 동일한 결과를 보장한다. None(기본값)이면 기존과 동일.
    """
    init_backtest_db()
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    # 2026-08-24: 확정된 기업행위 조정계수 로드(turnaround/regime_adaptive와 동일 목적).
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
    run_id   = run_id or str(uuid.uuid4())[:8]
    run_name = run_name or f"composite_{start_date[:4]}"
    strategy = "composite"
    # 2026-07-27: 코드 재확인 결과 이 엔진도 유니버스 쿼리에 market_cap 조건이 없고
    # score 계산·positions에도 실제 시총이 쓰이지 않음(mkt_cap_억은 항상 기본값 500) —
    # v8/regime_adaptive와 동일하게 "current" 라벨이 부정확했으므로 정정.
    _record_run_spec(
        run_id, "composite", "composite_v3_eventbonus_20260830",
        {"per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date, "use_event_bonus": use_event_bonus,
         "data_asof_ts": data_asof_ts},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,"
        "per_stock,max_pos,status) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, run_name, strategy, start_date, end_date, per_stock, max_positions, "running"),
    )
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
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

        # KOSPI 레짐 로드
        market_bullish: Dict[str, bool] = {}
        try:
            krows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r["date"]  for r in krows]
            k_prices = [float(r["close"]) for r in krows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                kma = _ma(k_prices[max(0, ki-119):ki+1], 120)
                market_bullish[kd] = (kma is None) or (k_prices[ki] > kma)
        except Exception:
            pass

        # 종목 데이터 로드
        stock_codes = [r[0] for r in conn.execute("""
            SELECT stock_code, COUNT(*) AS cnt FROM price_history
            WHERE date>=? AND date<=? AND close>0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code HAVING COUNT(*) >= 200
        """, (warmup_start, end_date)).fetchall()]

        # ── 이벤트 보정 맵 (use_event_bonus=True일 때만 구축, 2026-08-30 실험) ──
        def _norm_date(raw) -> str:
            """'YYYY-MM-DD'/'YYYY.MM.DD'/'YYYYMMDD' 등을 'YYYY-MM-DD'로 정규화.
            파싱 불가하면 빈 문자열(호출부에서 걸러냄) — treasury_buyback.rcept_dt에
            극소수(13,229건 중 4건) 'YYYY.MM.DD' 표기가 섞여 있어 strptime 크래시 발견(2026-08-30)."""
            s = str(raw).strip()
            if len(s) >= 10 and s[4] == '-' and s[7] == '-':
                return s[:10]
            if len(s) >= 10 and s[4] == '.' and s[7] == '.':
                return s[:10].replace('.', '-')
            if len(s) == 8 and s.isdigit():
                return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
            return ""

        dilution_map: Dict[str, list] = {}
        buyback_map: Dict[str, list] = {}
        patent_map: Dict[str, list] = {}
        if use_event_bonus and stock_codes:
            ph = ",".join("?" * len(stock_codes))
            for r in conn.execute(f"""
                SELECT stock_code, disclosed_at FROM dilution_events
                WHERE event_type IN ('CB','BW','EB','RIGHTS')
                  AND (risk_event_bucket IS NULL OR risk_event_bucket != 'legacy_non_issuance_event')
                  AND stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    dilution_map.setdefault(r[0], []).append(d)
            for r in conn.execute(f"""
                SELECT stock_code, rcept_dt, event_type FROM treasury_buyback
                WHERE stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    buyback_map.setdefault(r[0], []).append((d, r[2]))
            for r in conn.execute(f"""
                SELECT stock_code, rcept_dt, signal_type FROM dart_rd_patent_signals
                WHERE stock_code IN ({ph})
            """, stock_codes).fetchall():
                d = _norm_date(r[1]) if r[1] else ""
                if d:
                    patent_map.setdefault(r[0], []).append((d, r[2]))
            for m in (dilution_map, buyback_map, patent_map):
                for c in m:
                    m[c].sort(key=lambda x: x if isinstance(x, str) else x[0])

        stock_data: Dict[str, dict] = {}
        date_idx:   Dict[str, dict] = {}

        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, volume,
                       COALESCE(frn_net_buy, 0) AS frn,
                       COALESCE(inst_net_buy, 0) AS inst,
                       COALESCE(open, 0) AS open_p
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (sc, warmup_start, end_date)).fetchall()
            if len(rows) < 120:
                continue
            # data_asof_ts 지정 시 그 시각 이후 UPDATE된 행은 제외(재현성 고정용, 2026-09-04).
            fin_rows = conn.execute(f"""
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
                  {"AND f.updated_at <= ?" if data_asof_ts else ""}
                ORDER BY f.year DESC, f.quarter DESC
            """, (sc, sc) + ((data_asof_ts,) if data_asof_ts else ())).fetchall()
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
            ops = [float(r["open_p"]) if r["open_p"] else 0.0 for r in rows]

            # sim_start_i
            sim_i = next((j for j, d in enumerate(dts) if d >= start_date), len(dts))

            stock_data[sc] = {
                'dates': dts, 'prices': prs, 'volumes': vls,
                'frn': fns, 'inst': ins, 'fins': fin_all,
                'sim_start_i': sim_i, 'opens': ops,
            }
            date_idx[sc] = {d: j for j, d in enumerate(dts)}

        conn.row_factory = None

        # 시뮬레이션
        cash = per_stock * max_positions  # 2026-07-16: 실현손익 누산(capital) → 현금원장 전환
        positions: Dict[str, dict] = {}
        trades: List[dict] = []
        daily_pnl: List[Tuple[str, float]] = []
        _pb: Dict[str, dict] = {}   # pending buys  (D+1 집행)
        _ps: Dict[str, dict] = {}   # pending sells (D+1 집행)

        for day in sim_dates:

            # ── Phase A: 전일 매도 신호 → 오늘 시가/종가 집행 ────────
            to_remove_ps = []
            for sc in list(_ps.keys()):
                if sc not in positions:
                    to_remove_ps.append(sc)
                    continue
                pos = positions[sc]
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                ep   = pos['entry_price']
                qty  = pos['qty']
                _cmp_ep_adj = _corp_action_adjusted_entry(
                    _corp_action_factors, sc, pos['entry_date'], day, ep)
                _cmp_amt, _cmp_pct = _net_profit(_cmp_ep_adj, curr, qty, pos.get('mkt_cap_억', 500))
                cash += qty * _cmp_ep_adj + _cmp_amt
                held = pos.get('hold_days', 0)
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': _cmp_pct, 'pnl': _cmp_amt,
                    'reason': _ps[sc].get('reason', '매도'),
                    'score': pos.get('score', 0), 'held_days': held,
                })
                del positions[sc]
                to_remove_ps.append(sc)
            for sc in to_remove_ps:
                _ps.pop(sc, None)

            # ── Phase B: 전일 매수 신호 → 오늘 시가/종가 집행 ────────
            sorted_buys = sorted(_pb.items(), key=lambda x: x[1].get('score', 0), reverse=True)
            for sc, meta in sorted_buys:
                if sc in positions or len(positions) >= max_positions:
                    continue
                sd  = stock_data[sc]
                im  = date_idx.get(sc, {})
                if day not in im:
                    continue
                i   = im[day]
                op  = sd['opens'][i] if i < len(sd['opens']) else 0.0
                curr = op if op > 0 else sd['prices'][i]
                if curr <= 0:
                    continue
                s = meta.get('score', 60)
                take_p = 0.40 if s >= 80 else (0.30 if s >= 70 else 0.20)
                budget = min(per_stock, cash * 0.99)
                qty = int(budget / curr)
                if qty < 1 or qty * curr > cash:
                    continue
                cash -= qty * curr
                positions[sc] = {
                    'entry_date': day, 'entry_price': curr, 'qty': qty,
                    'score': s, 'take_profit': take_p, 'hold_days': 0,
                    'mkt_cap_억': meta.get('mkt_cap_억', 500),
                }
            _pb.clear()

            # ── hold_days 증가 ─────────────────────────────────────
            for pos in positions.values():
                pos['hold_days'] = pos.get('hold_days', 0) + 1

            # ── Phase C: 매도 신호 탐지 → _ps 큐 ─────────────────
            for sc, pos in list(positions.items()):
                if sc in _ps:
                    continue
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
                held = pos.get('hold_days', 0)
                take = pos.get('take_profit', 0.25)
                exit_reason = None
                if ret >= take:
                    exit_reason = f"익절{take*100:.0f}%"
                elif ret <= -0.10:
                    exit_reason = "손절-10%"
                elif held > 5:
                    ma60_e = _ma(sd['prices'][max(0, i-59):i+1], 60)
                    if ma60_e and curr < ma60_e:
                        exit_reason = "MA60붕괴"
                    # 240일 장기횡보 보류: 하락장 -1.3%→-25.3% 악화로 미적용
                if exit_reason:
                    _ps[sc] = {'reason': exit_reason}

            # ── Phase D: 매수 신호 탐지 → _pb 큐 ─────────────────
            if len(positions) < max_positions:
                candidates = []
                for sc, sd in stock_data.items():
                    if sc in positions or sc in _ps:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    s = _score_stock(
                        i, sd['sim_start_i'], sd['dates'], sd['prices'],
                        sd['volumes'], sd['frn'], sd['inst'], sd['fins'],
                        code=sc if use_event_bonus else None,
                        dilution_map=dilution_map if use_event_bonus else None,
                        buyback_map=buyback_map if use_event_bonus else None,
                        patent_map=patent_map if use_event_bonus else None)
                    if s >= score_threshold:
                        candidates.append((s, sc))
                candidates.sort(key=lambda x: -x[0])
                for s, sc in candidates:
                    if len(_pb) + len(positions) - len(_ps) >= max_positions:
                        break
                    _pb[sc] = {'score': s}

            # ── Phase E: 일별 PnL ──────────────────────────────────
            portfolio_val = cash
            for sc, pos in positions.items():
                sd = stock_data[sc]
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                curr = sd['prices'][i]
                portfolio_val += curr * pos['qty']
            daily_pnl.append((day, portfolio_val))

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            if last_day in im:
                i    = im[last_day]
                curr = sd['prices'][i]
                ep   = pos['entry_price']
                qty  = pos['qty']
                _cmpf_ep_adj = _corp_action_adjusted_entry(
                    _corp_action_factors, sc, pos['entry_date'], last_day, ep)
                _cmpf_amt, _cmpf_pct = _net_profit(_cmpf_ep_adj, curr, qty, pos.get('mkt_cap_억', 500))
                cash += qty * _cmpf_ep_adj + _cmpf_amt
                trades.append({
                    'sc': sc, 'entry': pos['entry_date'], 'exit': last_day,
                    'entry_price': ep, 'exit_price': curr,
                    'return_pct': _cmpf_pct, 'pnl': _cmpf_amt,
                    'reason': '기간종료', 'score': pos.get('score', 0),
                    'held_days': pos.get('hold_days', 0),
                })

        # 집계
        total_trades   = len(trades)
        winners        = [t for t in trades if t['return_pct'] > 0]
        losers         = [t for t in trades if t['return_pct'] <= 0]
        win_rate       = len(winners) / total_trades * 100 if total_trades else 0
        total_invested = per_stock * max_positions
        total_ret_pct  = (cash - total_invested) / total_invested * 100 if total_invested else 0

        avg_win  = sum(t['return_pct'] for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t['return_pct'] for t in losers) / len(losers)   if losers  else 0
        pf       = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

        # 스코어 분포
        score_dist = {
            '60-69': len([t for t in trades if 60 <= t.get('score', 0) < 70]),
            '70-79': len([t for t in trades if 70 <= t.get('score', 0) < 80]),
            '80+':   len([t for t in trades if t.get('score', 0) >= 80]),
        }

        days = len(sim_dates)
        yrs  = days / 252
        cagr = (cash / total_invested) ** (1 / yrs) * 100 - 100 if yrs > 0 and total_invested > 0 else 0

        summary = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 복합 스코어링 전략 (threshold={score_threshold}점)\n"
            f"스코어 분포: 60-69점={score_dist['60-69']}건 / 70-79점={score_dist['70-79']}건 / 80+점={score_dist['80+']}건\n"
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
        _register_execution_artifacts(run_id, total_invested, cash)
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
#  Meta-V 2.0: BULL → 복합스코어링, BEAR → V7 흑자전환
# ══════════════════════════════════════════════════════════════


