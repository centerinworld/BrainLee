"""
base.py -- run_backtest()
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
    _calc_metrics,
    _load_disc_dates,
    _ma,
    _record_run_spec,
    _register_execution_artifacts,
    _run_portfolio,
    _save_result,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest(start_date: str, end_date: str,
                 per_stock: float = 10_000_000,
                 max_positions: int = 10,
                 run_name: str = None,
                 run_id: str = None,
                 asof_mktcap: bool = True,
                 take_profit: float = 0.25,
                 big_gate: float = None,
                 trail_big: float = -0.35) -> str:
    """
    run_id가 주어지면 해당 레코드(이미 DB에 존재)를 직접 업데이트.
    없으면 새 run_id를 생성하고 INSERT.
    """
    init_backtest_db()
    run_name = run_name or f"백테스트 {start_date[:7]}~{end_date[:7]}"

    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'v4',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        # 이미 INSERT된 레코드 → 상태만 running으로 갱신
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    # 2026-07-18: 이 엔진은 run_spec 기록이 없어 trg_backtest_done_requires_spec에
    # 막혀 완료 불가였던 버그 수정. 최초에 "same_close 레거시"로 기록했으나 재검증 결과
    # 실행기 _run_portfolio는 D+1 pending 큐 체결 + 현금원장(budget 검사·cash 차감)을
    # 갖춘 엄격 엔진임을 확인(P0 Codex timing fix, backtest.py:831) — 실제 동작대로 정정.
    # 2026-07-27: as-of 시총(진입일 주가×상장주식수, security_master_history/
    # security_share_history 기반) 게이트를 기본화 — _run_generic_backtest와 동일 패턴.
    _record_run_spec(
        run_id, "v4", "v4_portfolio_nextopen_cashledger",
        {"per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date, "asof_mktcap": asof_mktcap,
         "take_profit": take_profit, "big_gate": big_gate, "trail_big": trail_big},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current",
    )

    try:
        _load_disc_dates(conn)  # 실제 DART 공시일 로드

        # ── 워밍업 시작일: start_date 보다 WARMUP_DAYS 거래일 이전 ──
        # 달력 기준 약 450일 전(영업일 300일 여유분)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')

        # ── 시뮬레이션 거래일 목록 (start_date ~ end_date) ─────────
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        if not sim_dates:
            # KOSPI 없으면 전체 종목에서 추출
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        # ── 재무 데이터 로드 (분기 + 연간 모두, 실제 공시일 포함) ──
        # 컬럼: year, quarter, rev, op, eps, bps, equity, net_inc, roe, is_annual, avail_date
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
                          ELSE printf('%d-02-15', f.year+1) END
                   ) as avail_date
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d ON
                d.stock_code = f.stock_code AND d.year = f.year
                AND d.quarter = CASE WHEN f.is_annual=1 THEN 4 ELSE f.quarter END
                AND d.is_annual = CASE WHEN f.is_annual=1 THEN 1 ELSE 0 END
            WHERE (f.is_annual=0 AND f.quarter BETWEEN 1 AND 3)
               OR (f.is_annual=1)
            ORDER BY f.stock_code, f.year, f.quarter
        """).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])   # (y,q,...,is_ann,avail_date)

        # ── 종목 목록 (시총 1000억 이상 + 워밍업 포함 충분한 데이터 보유) ─
        # stock_universe.market_cap은 억원 단위 → 1000억 = 1000
        # 2026-07-27: as-of 모드는 현재시점 유니버스 대신 신호일 시점 security master 사용
        # (룩어헤드 제거, _run_generic_backtest와 동일 패턴).
        MKTCAP_MIN_V4 = 1000.0
        if asof_mktcap:
            stock_codes = [r[0] for r in conn.execute("""
                SELECT ph.stock_code, COUNT(*) AS cnt
                FROM price_history ph
                JOIN security_master_history sm ON sm.stock_code=ph.stock_code
                  AND substr(ph.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(ph.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                WHERE ph.date>=? AND ph.date<=? AND ph.close>0
                GROUP BY ph.stock_code HAVING COUNT(*) >= 200
            """, (warmup_start, end_date)).fetchall()]
        else:
            stock_codes = [r[0] for r in conn.execute("""
                SELECT ph.stock_code, COUNT(*) AS cnt
                FROM price_history ph
                INNER JOIN (
                    SELECT stock_code FROM stock_universe
                    WHERE market_cap >= 1000
                      AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                    GROUP BY stock_code
                ) su ON ph.stock_code = su.stock_code
                WHERE ph.date>=? AND ph.date<=? AND ph.close>0
                GROUP BY ph.stock_code
                HAVING COUNT(*) >= 200
            """, (warmup_start, end_date)).fetchall()]

        # ── as-of 상장주식수 이력 (as-of 시총 게이트용) ──────────────
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_v4(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        # ── 시총 로드 (슬리피지 계산용) ─────────────────────────────
        mkt_cap_map: Dict[str, float] = {}
        if stock_codes:
            ph = ','.join('?' * len(stock_codes))
            mkt_cap_map = {r[0]: float(r[1] or 500) for r in conn.execute(
                f"SELECT stock_code, COALESCE(market_cap, 500) FROM stock_universe "
                f"WHERE stock_code IN ({ph})", stock_codes
            ).fetchall()}

        # ── 종목별 데이터 로드 ────────────────────────────────────
        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close,
                           COALESCE(volume, 0),
                           COALESCE(frn_net_buy, 0),
                           COALESCE(inst_net_buy, 0),
                           COALESCE(open, 0)
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
                opens   = [float(r[5]) for r in rows]
                fins    = fin_all.get(sc, [])

                # 시뮬레이션 시작 인덱스 (start_date 이후 첫 거래일)
                sim_start_i = next(
                    (i for i, d in enumerate(dates) if d >= start_date),
                    len(dates)
                )

                stock_data[sc] = {
                    'dates':       dates,
                    'prices':      prices,
                    'volumes':     volumes,
                    'frn':         frn,
                    'inst':        inst,
                    'fins':        fins,
                    'sim_start_i': sim_start_i,
                    'mkt_cap_억':  mkt_cap_map.get(sc, 500),
                    'opens':       opens,
                }
            except Exception:
                continue

        # ── ★ KOSPI 시장 추세 필터 + 국면별 익절 생성 ─────────────────
        # KOSPI > MA120: 상승장(매수 허용), 아니면 하락장(매수 금지)
        # KOSPI vs MA60 비율로 익절 동적 조정:
        #   KOSPI > MA60 × 1.15: 강세장 → TP=75% (대세 상승장에서 winner 유지)
        #   KOSPI > MA60 × 1.05: 보통 강세 → TP=40%
        #   KOSPI > MA60:         보통장  → TP=25%
        #   KOSPI < MA60:         약세장  → TP=20%
        market_bullish: Dict[str, bool] = {}
        take_profit_map: Dict[str, float] = {}
        trail_stop_map: Dict[str, float] = {}
        try:
            kospi_rows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r[0] for r in kospi_rows]
            k_prices = [float(r[1]) for r in kospi_rows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                kma60  = _ma(k_prices[max(0, ki - 59): ki + 1], 60)
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                kp = k_prices[ki]
                # 매수 허용 여부 (MA120 기준)
                if kma120 is None:
                    market_bullish[kd] = True
                else:
                    market_bullish[kd] = kp > kma120
                # 국면별 익절+추적손절 (MA60 비율 기준)
                # TP는 기본 0.25 유지(회전속도 최적, 2026-08-09 take_profit 파라미터화 —
                # 텐버거 population 캡처 실험용, 기본값은 기존과 동일), trail만 강세장에서 살짝 완화
                if kma60 is None:
                    take_profit_map[kd] = take_profit;  trail_stop_map[kd] = -0.10
                elif kp > kma60 * 1.10:    # 강세장: trail -12% (조정에 여유)
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.12
                elif kp > kma60:           # 보통장
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.10
                else:                      # 약세장
                    take_profit_map[kd] = take_profit
                    trail_stop_map[kd]  = -0.10
        except Exception:
            pass  # KOSPI 데이터 없으면 필터 비활성화

        conn.close()

        # ── 포트폴리오 시뮬레이션 ───────────────────────────────
        total_capital = per_stock * max_positions
        trades, equity_curve = _run_portfolio(
            sim_dates, stock_data,
            per_stock, max_positions,
            stop_loss=-0.08,
            market_bullish=market_bullish if market_bullish else None,
            take_profit_map=take_profit_map if take_profit_map else None,
            trail_stop_map=trail_stop_map   if trail_stop_map  else None,
            asof_mktcap=asof_mktcap,
            shares_asof_fn=_shares_asof_v4 if asof_mktcap else None,
            mktcap_min=MKTCAP_MIN_V4,
            big_gate=big_gate, trail_big=trail_big,
        )

        # ── 종목명 매핑 ──────────────────────────────────────
        conn2  = sqlite3.connect(DB_PATH, timeout=120)
        name_map: Dict[str, str] = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            ph = ','.join('?' * len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(sm.stock_name, su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_meta sm USING(stock_code)
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        # ── 성과 지표 ────────────────────────────────────────
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        # 월별 손익
        monthly: dict = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        monthly_list = [{'month': k, 'profit': v} for k, v in sorted(monthly.items())]

        # 종목별 손익
        from collections import defaultdict
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        top_winners = sorted(per_name.items(), key=lambda x: -x[1])[:5]
        top_losers  = sorted(per_name.items(), key=lambda x:  x[1])[:5]

        # 매도 사유별 통계
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        # 시장 필터 통계
        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days > 0 else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 전략 v5: 시장추세필터(KOSPI MA120) / 손절-8% / 추적손절-10% / 익절+15% / 최소보유5일 / 시총1000억+\n"
            f"시장필터: 하락장 {bear_days}일/{total_days}일({filter_pct}%) 매수 차단\n"
            f"총 거래: {metrics['total_trades']}건  수익: {metrics['profit_trades']}건  "
            f"손실: {metrics['total_trades'] - metrics['profit_trades']}건\n"
            f"승률: {metrics['win_rate']}%  손익비: {metrics['pl_ratio']}배  "
            f"총손익: {metrics['total_profit_amt']:,}원\n"
            f"총수익률: {metrics['total_return_pct']}%  CAGR: {metrics['cagr']}%  "
            f"연환산: {metrics['ann_return_pct']}%\n"
            f"MDD: {metrics['max_drawdown_pct']}%  샤프지수: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )

        result = {
            **metrics,
            'monthly':      monthly_list,
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in top_winners],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in top_losers],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True),
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        # 2026-07-18: _run_portfolio는 D+1체결+현금원장 엄격 엔진 — 아티팩트 등록으로
        # derive_status legacy 고정 해제 (초기자본→최종에쿼티는 total_return으로 재구성)
        # 2026-07-27: as-of 시총 게이트 기본화에 맞춰 asof_mktcap 실제값 전달
        _init_cap = per_stock * max_positions
        _final_eq = _init_cap * (1 + float(metrics.get('total_return_pct') or 0) / 100.0)
        _register_execution_artifacts(run_id, _init_cap, _final_eq, asof_mktcap=asof_mktcap)
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




