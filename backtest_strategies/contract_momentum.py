"""
contract_momentum.py -- run_backtest_contract_momentum()
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
    _calc_metrics,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_contract_momentum(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 10,
    per_stock: float = 10_000_000,
    min_ratio: float = 10.0,        # 계약금액/매출 비율(%) 하한
    overseas_only: bool = True,     # 해외수주만(국내계약은 2026-07-24 홀드아웃에서 신호품질 열위 확인)
    min_ai: float = 0.0,
    pos52_max: float = 1.0,         # 52주 내 상대위치 상한(과열 회피, 1.0=제한없음)
    min_ma20: float = 0.0,          # 종가/MA20-1 하한(추세 확인)
    min_quarterly_impact: float = None,  # 2026-08-10: 계약비율(%)÷계약기간(분기수) 하한.
                                     # None=비활성(기존 동작 동일). 사용자 지시("10버거 종목을 확대하되
                                     # 아닌 종목을 걸러낼수 있는 지표")로 실증: 실제거래(102건 중 68건
                                     # 매칭) success(trail/expire)그룹 평균13.74 vs stop그룹 평균5.79로
                                     # 2.4배 차이 — 단순 ratio는 오히려 역방향(success15.8%<stop26.0%,
                                     # 대형 단발계약이 후속 모멘텀 없이 소진되는 경향 추정).
                                     # ⚠️ 학습기(2020-03~2023-12) 최적값(qi=8)을 검증기(2024-01+)에
                                     # 얼려적용 시 대폭악화(방향뒤집힘, 과최적화 확정) — 기본값 None 유지.
    max_mom60: float = None,        # 2026-08-10: 진입시점 60일 모멘텀(%) 상한. None=비활성.
    stop: float = -0.08,
    trail: float = -0.25,
    trail_activate_pct: float = 0.10,
    max_hold: int = 400,  # 2026-08-10: 240→400 변경. 텐버거(10배+) 583종목 저점~고점 실제 페이스
                           # (3년이내 달성군 중위 483일=1.3년, p90 886일) 대비 원안(240일)이 짧다는
                           # 문제의식으로 스윕(240/400/500/700/999) — 연속운용(2020-03~2026-03)
                           # 204.24%→222.10%(+17.9%p), 350~450 전구간 견고(knife-edge 아님).
                           # 홀드아웃(학습<2024-01-01/검증>=2024-01-01) 재검증: baseline 학습45.78%/
                           # 검증162.8% vs 400 학습39.09%/검증187.92%(+25.1%p) — 검증기(미래데이터)
                           # 에서 개선, 방향 일치. signal_experiment_ledger: contract_momentum/
                           # max_hold_240_to_400_holdout_20260810.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-CONTRACT-MOMENTUM — 해외 대형수주 공시 모멘텀 전략.

    [배경] 2026-08-09 사용자 지시로 3년내 10배 종목 251개를 상승계기별 카테고리화한
    결과, "대형수주"(36개, 14.3%) 카테고리의 최초 공시일 중 42%(15/36)가 저점 이전에
    발생 — 즉 선행지표로 쓸 수 있는 비중이 상당함을 확인. 이 신호는 이미 2026-07-23~24
    Codex가 독립 스크립트(scratch/codex_research_contract_momentum_20260723.py)로
    발굴·검증했으나(학습기 최적파라미터를 검증기에 얼려서 적용, +154.3%/145건/
    승률24.8%/PF2.51/MDD-27.9% — 붕괴 없음 확인) 정식 backtest.py 함수로 이식되지
    않아 실전(가상매매/콤보)에 연결된 적이 없었음. 이번에 원본 로직을 최대한 그대로
    유지하되(파라미터·필터·매도규칙 동일), 다른 전략과 같은 프레임워크(as-of 없음—
    원본에 시총필터 자체가 없었음, D+1 시가체결, run_spec 기록)로 이식.

    매수: dart_contracts 중 "단일판매/공급계약"류 공시(해지·거래정지·유동성공급·
         [첨부추가] 제외) + contract_ratio_pct>=min_ratio + (해외한정 옵션) +
         52주 내 상대위치<=pos52_max + 종가>=MA20×(1+min_ma20) + 20일평균거래대금>=20억.
         공시 다음 거래일 시가 매수, 동일일 복수신호는 ratio·ai_score 내림차순 우선.
    매도: 손절-8% / 추적손절-25%(이익10%+ 발동) / 만기400거래일(2026-08-10 240→400, 홀드아웃 검증 채택).
    """
    init_backtest_db()
    run_name = run_name or f"V-CONTRACT-MOMENTUM {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "contract_momentum", "contract_momentum_v1_20260809",
        {"min_ratio": min_ratio, "overseas_only": overseas_only, "min_ai": min_ai,
         "pos52_max": pos52_max, "min_ma20": min_ma20, "min_quarterly_impact": min_quarterly_impact,
         "max_mom60": max_mom60,
         "stop": stop, "trail": trail,
         "max_hold": max_hold, "max_positions": max_positions, "per_stock": per_stock,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'contract_momentum',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        def _is_clean_contract_report(report_name: str) -> bool:
            name = (report_name or "").replace(" ", "")
            if not name:
                return False
            if "단일판매" not in name and "공급계약" not in name:
                return False
            blocked = ("계약해지", "주권매매거래정지", "유동성공급", "[첨부추가]")
            return not any(token in name for token in blocked)

        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=500)).strftime('%Y-%m-%d')
        raw = conn.execute("""
            SELECT rcept_no, stock_code, disclosed_at, COALESCE(report_nm,''),
                   COALESCE(contract_ratio_pct,0), COALESCE(is_overseas,0), COALESCE(ai_score,0),
                   COALESCE(contract_amount_krw,0), contract_start, contract_end
            FROM dart_contracts
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' AND contract_ratio_pct IS NOT NULL
        """).fetchall()

        def _duration_months(s, e):
            # 2026-08-10: 계약기간 정규화 지표(2026-07-25 이론적 제안 → 오늘 실증) —
            # 단순 contract_ratio_pct는 성공/손절 분포에서 오히려 역방향(성공15.8%<손절26.0%,
            # 대형 단발성 계약이 후속 모멘텀 없이 소진되는 경향으로 추정), 계약기간으로 나눈
            # quarterly_impact(=ratio/duration_quarters)는 성공13.74 vs 손절5.79로 2.4배 판별력.
            if not s or not e:
                return None
            try:
                sy, sm = int(str(s)[:4]), int(str(s)[5:7])
                ey, em = int(str(e)[:4]), int(str(e)[5:7])
                return max(0.25, (ey - sy) * 12 + (em - sm))
            except Exception:
                return None

        seen = set()
        events_raw = []
        for rcept_no, code, dt, report_name, ratio, overseas, ai_score, amount_krw, c_start, c_end in raw:
            digits = "".join(ch for ch in str(dt or "") if ch.isdigit())
            if len(digits) < 8:
                continue
            iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            if not (warmup_start <= iso <= end_date):
                continue
            if not _is_clean_contract_report(report_name):
                continue
            # dedup key는 원본 스크립트(codex_research_contract_momentum_20260723.py)와
            # 동일하게 금액도 포함 — 같은 종목·날짜·비율이라도 계약금액이 다르면 별개 이벤트.
            key = (code, iso, round(float(amount_krw or 0) / 1_000_000), round(float(ratio or 0), 2))
            if key in seen:
                continue
            seen.add(key)
            dur_m = _duration_months(c_start, c_end)
            q_impact = (float(ratio or 0) / (dur_m / 3)) if (dur_m and ratio) else None
            events_raw.append((code, iso, float(ratio or 0), int(overseas or 0), float(ai_score or 0), q_impact))

        codes = sorted({e[0] for e in events_raw})
        sd: Dict[str, dict] = {}
        for code in codes:
            # 2026-08-09: trade_amount(KRX ACC_TRDVAL)가 2026-07~08 전종목 0으로 채워지던
            # 인프라버그를 발견·수정(scheduler.py _job_krx_daily) + 결측분 백필했으나,
            # signal_engine.py/screener.py처럼 close×volume 폴백도 방어적으로 추가해
            # 향후 유사 회귀에도 이 전략만 취약해지지 않도록 함.
            rows = conn.execute("""
                SELECT date, close, COALESCE(open,close) AS o, COALESCE(high,close) AS h,
                       COALESCE(low,close) AS lo, COALESCE(trade_amount,0) AS amt, COALESCE(volume,0) AS vol
                FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 260:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            amt_list = [float(r[5]) if r[5] and r[5] > 0 else float(r[1]) * float(r[6] or 0) for r in rows]
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows], 'c': c_list,
                'o': [float(r[2]) for r in rows], 'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows], 'amt': amt_list,
            }
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}

        # 이벤트별 진입일(entry_date=신호일 다음 거래일) 및 필터 지표(52주위치/MA20/20일평균거래대금) 계산
        buy_pool: Dict[str, list] = {}
        for code, sig_date, ratio, overseas, ai_score, q_impact in events_raw:
            s = sd.get(code)
            if not s or code not in didx:
                continue
            pos = None
            for i, d in enumerate(s['d']):
                if d > sig_date:
                    pos = i; break
            if pos is None or pos < 260:
                continue
            i0 = pos - 1  # 신호 확인 시점(공시일 당일 종가 기준)
            ma20 = sum(s['c'][i0-19:i0+1]) / 20
            avg20_amt = sum(s['amt'][i0-19:i0+1]) / 20
            if ma20 <= 0 or avg20_amt < 2_000_000_000:
                continue
            hi252 = max(s['h'][i0-251:i0+1]); lo252 = min(s['lo'][i0-251:i0+1])
            if hi252 <= lo252:
                continue
            pos52 = (s['c'][i0] - lo252) / (hi252 - lo252)
            close_ma20 = s['c'][i0] / ma20 - 1
            # 2026-08-10: 진입시점 60일 모멘텀 — 교차전략(golden_cross/earnings_conviction/
            # contract_momentum/moonshot_turnaround) 384건 실측: success그룹 평균17.56% <
            # stop그룹 평균21.69% (반직관적, 과열회피 원칙과 일치 — 너무 급하게 오른 뒤
            # 진입하면 오히려 실패 확률 높음). max_mom60=None이면 비활성(기존동작 동일).
            mom60 = (s['c'][i0] / s['c'][i0 - 60] - 1) * 100 if i0 >= 60 and s['c'][i0 - 60] > 0 else None
            if ratio < min_ratio: continue
            if overseas_only and not overseas: continue
            if ai_score < min_ai: continue
            if pos52 > pos52_max: continue
            if close_ma20 < min_ma20: continue
            if min_quarterly_impact is not None and (q_impact is None or q_impact < min_quarterly_impact):
                continue
            if max_mom60 is not None and (mom60 is None or mom60 > max_mom60):
                continue
            entry_date = s['d'][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            buy_pool.setdefault(entry_date, []).append((ratio, ai_score, code))
        for d in buy_pool:
            buy_pool[d].sort(reverse=True)

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []
        equity_curve: list = []  # 2026-08-13: MDD/Sharpe 계산용 일별 자산평가(현금+보유포지션 시가평가)

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
                for ratio, ai_score, code in buy_pool.get(day, []):
                    if slots <= 0:
                        break
                    if code in pos or code in pending_codes:
                        continue
                    pending_buys.append(code)
                    pending_codes.add(code)
                    slots -= 1

            _mkval = cash
            for _c, _p in pos.items():
                _i = didx[_c].get(day)
                if _i is not None and sd[_c]['c'][_i] > 0:
                    _mkval += _p['shares'] * sd[_c]['c'][_i]
                else:
                    _mkval += _p['shares'] * _p['entry']
            equity_curve.append({'date': day, 'equity': _mkval})

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

        # 2026-08-13(사용자 지시): MDD/샤프/손익비 계산 파이프라인 신규 추가.
        # _calc_metrics()(L1163)와 동일한 산식(에쿼티커브 peak대비 낙폭, 일별수익률
        # 표준편차 기반 샤프, 승/패 평균금액비 손익비)을 이 함수 구조에 맞춰 인라인 적용.
        peak = total_capital
        max_dd = 0.0
        for e in equity_curve:
            eq = e['equity']
            if eq > peak:
                peak = eq
            if peak > 0:
                dd = (eq - peak) / peak * 100
                max_dd = min(max_dd, dd)
        sharpe = 0.0
        if len(equity_curve) > 5:
            eq_vals = [e['equity'] for e in equity_curve]
            daily_r = [(eq_vals[i] - eq_vals[i - 1]) / eq_vals[i - 1]
                       for i in range(1, len(eq_vals)) if eq_vals[i - 1] > 0]
            if len(daily_r) > 5:
                rf = 0.03 / 252
                mean_r = sum(daily_r) / len(daily_r)
                std_r = (sum((r - mean_r) ** 2 for r in daily_r) / len(daily_r)) ** 0.5
                sharpe = round((mean_r - rf) / std_r * (252 ** 0.5), 2) if std_r > 0 else 0.0
        win_t = [t for t in completed if t['pnl'] > 0]
        loss_t = [t for t in completed if t['pnl'] <= 0]
        avg_win = sum(t['pnl'] for t in win_t) / len(win_t) if win_t else 0
        avg_loss = abs(sum(t['pnl'] for t in loss_t)) / len(loss_t) if loss_t else 1
        pl_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, total_trades=?, win_rate=?,
                max_drawdown_pct=?, trades_json=?
            WHERE run_id=?
        """, (round(total_return, 2), len(completed), round(win_rate, 1),
              round(max_dd, 2),
              json.dumps({"trades": trades, "sharpe": sharpe, "pl_ratio": pl_ratio,
                          "max_drawdown_pct": round(max_dd, 2)}), run_id))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=False)
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




