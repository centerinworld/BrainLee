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

import backtest_common as _bc
from price_integrity import research_price_issues
from backtest_common import (
    DELISTING_CONFIRM_DAYS,
    QUALITY_DAY_CLASSES,
    is_excluded_day,
    last_tradable_day_before_break,
    last_tradable_index_before,
    load_adjusted_prices,
    _load_jump_aligned_corp_factors,
    _rebase_positions_for_corp_actions,
    SignalEvidenceLedger,
    evidence_item,
    DB_PATH,
    _calc_metrics,
    _final_liquidation_quote_for_code,
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
    data_asof_ts: str = None,
    adjusted_prices: bool = None,   # W5b(REVIEW_PLAN §32-3): None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
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
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    # 조정 모드(W5b 공통 규칙): 신호·손익 = 조정 시계열(보유 재기준 대신). 유니버스는 원래부터 공시 이벤트 기반(폐지 종목 포함).
    # '분할/합병 필터'·'260행 이상'(기간 전체 = 미래 정보) 미사용 — 신호일 이력(pos ≥ 260)은 그대로. 거래대금은 금액이라 조정과 무관.
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'stocks_with_breaks': 0, 'share_unknown_breaks': 0,
                 'delisted_exits': 0, 'unevaluable_delistings': 0, 'delisted_entries': 0, 'pit_mktcap_missing': 0}
    init_backtest_db()
    run_name = run_name or f"V-CONTRACT-MOMENTUM {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    effective_data_asof_ts = data_asof_ts or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _record_run_spec(
        run_id, "contract_momentum", "contract_momentum_v2_snapshot_20260906",
        {"min_ratio": min_ratio, "overseas_only": overseas_only, "min_ai": min_ai,
         "pos52_max": pos52_max, "min_ma20": min_ma20, "min_quarterly_impact": min_quarterly_impact,
         "max_mom60": max_mom60,
         "stop": stop, "trail": trail,
         "max_hold": max_hold, "max_positions": max_positions, "per_stock": per_stock,
         "total_capital": total_capital, "start": start_date, "end": end_date,
         "data_asof_ts": effective_data_asof_ts,
         "adjusted_prices": True if adjusted_prices else None},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="not_applicable", allocation_rule="fixed_slot",
        **({"universe_version": "event_driven_dart_contracts_adjusted"} if adjusted_prices else {}),
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
                   COALESCE(contract_amount_krw,0), contract_start, contract_end,
                   id, created_at, updated_at, corrects_rcept_no, corrected_by_rcept_no
            FROM dart_contracts
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' AND contract_ratio_pct IS NOT NULL
              AND COALESCE(is_correction, 0) = 0
              AND COALESCE(updated_at, created_at) <= ?
        """, (effective_data_asof_ts,)).fetchall()
        # 2026-09-05: "정정" 공시("[기재정정]...") 매칭 기능 도입(collectors/dart_contract_collector.py)
        # 이후 dart_contracts에 원 공시를 감사기록용으로 복제한 is_correction=1 행이 추가로 쌓임 —
        # 이 필터 없이는 같은 계약이 원 공시 행(정정후 값으로 갱신됨)과 정정 감사행 양쪽에서
        # 중복 매수신호로 잡혀 신호 건수가 부풀려짐.
        # 2026-09-06: created_at만 보면 안 되는 이유 — 정정은 원 공시 행을 in-place로
        # 덮어쓰고 created_at은 최초 수집시각 그대로 두므로, data_asof_ts를 "정정 전" 시점으로
        # 지정해도 이미 정정후 값이 보이는 look-ahead가 있었다. 정정 적용 시 채워지는
        # updated_at(없으면 created_at로 폴백)을 기준으로 판단해 정정 시점 이후에만 새 값을
        # "알 수 있었던 것"으로 취급한다.

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
        event_meta: Dict[tuple, list] = {}
        for (rcept_no, code, dt, report_name, ratio, overseas, ai_score, amount_krw, c_start, c_end,
             row_id, created_at, updated_at, corrects_no, corrected_by_no) in raw:
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
            event_meta.setdefault((code, iso), []).append({
                "row_id": row_id, "rcept_no": rcept_no, "disclosed_at": iso, "report_nm": report_name,
                "contract_ratio_pct": float(ratio or 0), "is_overseas": int(overseas or 0),
                "ai_score": float(ai_score or 0), "contract_amount_krw": float(amount_krw or 0),
                "contract_start": c_start, "contract_end": c_end, "quarterly_impact": q_impact,
                "corrects_rcept_no": corrects_no, "corrected_by_rcept_no": corrected_by_no,
                "created_at": created_at, "updated_at": updated_at,
            })

        codes = sorted({e[0] for e in events_raw})
        sd: Dict[str, dict] = {}
        if adjusted_prices:
            _codes = sorted({str(c_) for c_ in codes})
            _ap = load_adjusted_prices(conn, _codes, warmup_start, end_date)
            _qdays: Dict[str, set] = {}
            for _qc, _qd, _qcls in research_price_issues(conn, _codes, warmup_start, end_date, allow_confirmed_corporate_actions=True):
                if _qcls in QUALITY_DAY_CLASSES:
                    _qdays.setdefault(str(_qc), set()).add(str(_qd)[:10])
            for code in _codes:
                e = _ap.get(code)
                if not e or not e['dates'] or not any(start_date <= d <= end_date for d in e['dates']):
                    continue
                d_list = [str(d)[:10] for d in e['dates']]
                sd[code] = {
                    'd': d_list, 'dates': d_list,
                    'c': list(e['close']), 'o': list(e['open']), 'h': list(e['high']), 'lo': list(e['low']),
                    'v': list(e['volume']), 'volumes': list(e['volume']),
                    'raw_c': list(e['raw_close']), 'f': list(e['adj_factor']),
                    'breaks': list(e['breaks']), 'excluded_ranges': list(e['excluded_ranges']),
                    'disc': e.get('break_disclosed', {}) or {}, 'qdays': _qdays.get(code, set()),
                    'master_closed_to': e.get('master_closed_to'),
                    'mkt_cap_억': 300,
                }
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)
            for code, s_ in sd.items():
                _amt = {str(d)[:10]: (float(a_) if a_ and a_ > 0 else float(c_) * float(v_ or 0))
                        for d, a_, c_, v_ in conn.execute(
                            "SELECT date, COALESCE(trade_amount,0), close, COALESCE(volume,0) FROM price_history "
                            "WHERE stock_code=? AND date>=? AND date<=? AND close>0", (code, warmup_start, end_date)).fetchall()}
                s_['amt'] = [_amt.get(d, 0.0) for d in s_['d']]
        for code in ([] if adjusted_prices else codes):
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
        pool_events: Dict[tuple, list] = {}
        evidence = SignalEvidenceLedger("contract_momentum", {
            "min_ratio": min_ratio, "overseas_only": overseas_only, "min_ai": min_ai,
            "pos52_max": pos52_max, "min_ma20": min_ma20,
            "min_quarterly_impact": min_quarterly_impact, "max_mom60": max_mom60,
            "data_asof_ts": effective_data_asof_ts,
        })

        def _note_evidence(code: str, day: str) -> None:
            items = []
            for m in pool_events.get((day, code), ()):
                # 정정 반영 시각: 정정은 원 공시 행의 계약금액·계약종료일만 덮어쓴다
                # (collectors/dart_contract_collector.py _apply_correction). 계약비율·해외여부·
                # 공시일은 원 공시 그대로다. 정정 공시일은 정정 rcept_no 앞 8자리(DART 접수일).
                # min_quarterly_impact(계약종료일 사용)가 켜진 run만 정정값을 신호에 쓰므로 그때만
                # 정정 공시일을 available_at으로 삼는다 — 신호일 이후 정정이면 감사에서 걸린다.
                corr_no = str(m["corrected_by_rcept_no"] or "")
                corr_date = (f"{corr_no[:4]}-{corr_no[4:6]}-{corr_no[6:8]}"
                             if len(corr_no) >= 8 and corr_no[:8].isdigit() else None)
                uses_corrected = min_quarterly_impact is not None
                avail = (max(m["disclosed_at"], corr_date) if (corr_date and uses_corrected)
                         else m["disclosed_at"])
                value = {**m, "correction_disclosed_at": corr_date,
                         "corrected_fields_used_by_signal": bool(corr_date and uses_corrected)}
                items.append(evidence_item(
                    "dart_contracts", m["rcept_no"], avail, basis="actual_disclosure",
                    source_key=f"row:{m['row_id']}", value=value,
                    collected_at=m["created_at"], modified_at=m["updated_at"],
                ))
            evidence.note(code, day, items or [evidence_item("dart_contracts", None, None)])
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
            if adjusted_prices:
                if is_excluded_day(s, s['d'][i0]) or is_excluded_day(s, s['d'][pos]):
                    adj_stats['candidate_skips_excluded'] += 1; continue
                if s['d'][i0] in s['qdays']:
                    adj_stats['candidate_skips_quality_day'] += 1; continue
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
            pool_events.setdefault((entry_date, code), []).extend(
                m for m in event_meta.get((code, sig_date), ())
                if m["contract_ratio_pct"] == ratio and m["ai_score"] == ai_score
            )
        for d in buy_pool:
            buy_pool[d].sort(reverse=True)

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))

        cash = total_capital
        trades = []
        pending_sells: list = []
        pos: Dict[str, dict] = {}
        if adjusted_prices and sim_dates:
            from datetime import date as _d0
            for code, s_ in sd.items():
                s_['last_tradable_i'] = max((k for k, v_ in enumerate(s_['v']) if v_ > 0), default=None)
                s_['ends_before_period_end'] = s_['d'][-1] < sim_dates[-1]
                _mc = s_.get('master_closed_to')
                try:
                    _gap = abs((_d0.fromisoformat(_mc[:10]) - _d0.fromisoformat(s_['d'][-1][:10])).days) if _mc else None
                except Exception:
                    _gap = None
                s_['delisting_confirmed'] = bool(_gap is not None and _gap <= DELISTING_CONFIRM_DAYS)

        def _pit_mc(code, day_, raw_px):
            """신호일 시총(억원) = 상장주식 수 × 원주가. 주식 수를 모르면 가장 보수적(최대 슬리피지)."""
            sh_ = (lambda _c, _d: 0.0)(code, day_)
            if sh_ > 0:
                return sh_ * raw_px / 1e8
            adj_stats['pit_mktcap_missing'] += 1
            return 1.0

        def _close_now(code, p, i, price, reason, day_):
            """조정 단위 `price`로 즉시 청산(단절·폐지 처리)."""
            nonlocal cash, pending_sells
            pnl_, net_pct_ = _net_profit(p['entry'], price, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl_
            _f = sd[code]['f'][i]
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day_,
                           'entry': p['entry'], 'exit': price, 'pnl_pct': net_pct_,
                           'reason': reason, 'pnl': round(pnl_, 0),
                           'exit_raw': round(price / _f, 4) if _f else price,
                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
            del pos[code]
            pending_sells = [(c_, r_) for c_, r_ in pending_sells if c_ != code]

        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []
        equity_curve: list = []  # 2026-08-13: MDD/Sharpe 계산용 일별 자산평가(현금+보유포지션 시가평가)

        # 2026-09-28: 보유 중 확정 기업행위(권리락·분할 등) 날 포지션을 새 주식 기준으로 재기준
        _ca_factors = {} if adjusted_prices else _load_jump_aligned_corp_factors(conn, list(sd.keys()))
        _ca_prev_day = None
        for day in sim_dates:
            _rebase_positions_for_corp_actions(_ca_factors, pos, _ca_prev_day, day, ('entry', 'peak'), 'shares')
            _ca_prev_day = day
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
                if adjusted_prices and (sd[code]['v'][i] or 0) <= 0:
                    adj_stats['zero_volume_deferred_sells'] += 1   # 거래 불가능한 날 — 다음 거래일로 이월
                    _still.append((code, reason)); continue
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], px, p['shares'], 300)
                cash += p['shares'] * p['entry'] + pnl
                trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                                'entry': p['entry'], 'exit': px, 'pnl_pct': net_pct,
                                'reason': reason, 'pnl': round(pnl, 0)})
                if adjusted_prices:
                    _ff = sd[code]['f'][i]
                    trades[-1].update({'exit_raw': round(px / _ff, 4) if _ff else px,
                                       'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
            pending_sells = _still
            if adjusted_prices:
                for code, p in list(pos.items()):
                    i = didx[code].get(day)
                    if i is not None and i > 0 and day in sd[code]['breaks']:
                        _k = last_tradable_index_before(sd[code], i)
                        _k = i - 1 if _k is None else _k
                        _close_now(code, p, i, sd[code]['c'][_k], '단절 당일 청산(공시 근거 없음·평가 불가)', day)
                        trades[-1]['evaluation'] = 'unevaluable_break'; trades[-1]['basis_date'] = sd[code]['d'][_k]
                        adj_stats['break_day_liquidations'] += 1

            for code in pending_buys:
                if code in pos or len(pos) >= max_positions:
                    continue
                i = didx[code].get(day)
                if i is None:
                    continue
                px = sd[code]['o'][i]
                if adjusted_prices:
                    if is_excluded_day(sd[code], day):   # 계수 미확정 단절 제외 구간(단절 당일 포함)에는 체결도 하지 않는다(D12 ②)
                        adj_stats['excluded_fill_cancels'] = adj_stats.get('excluded_fill_cancels', 0) + 1
                        continue
                    if px <= 0 or (sd[code]['v'][i] or 0) <= 0:
                        adj_stats['zero_volume_skipped_buys'] += 1
                        continue
                    _ff = sd[code]['f'][i] or 1.0
                    _rpx = px / _ff
                    if cash < _rpx * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares_raw = int(budget // _rpx)
                    if shares_raw <= 0:
                        continue
                    cash -= shares_raw * _rpx
                    pos[code] = {'entry': px, 'shares': shares_raw / _ff, 'buy_date': day, 'hold': 0, 'peak': px,
                                 'entry_raw': round(_rpx, 4), 'shares_raw': shares_raw}
                    if sd[code].get('ends_before_period_end'):
                        adj_stats['delisted_entries'] += 1
                    continue
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
                if adjusted_prices:
                    s_ = sd[code]
                    # 상장폐지·거래종료(N1 ②·§26-2 ②): 자료가 기간 끝 전에 끝나고 오늘이 마지막 거래 가능일이면 그 종가로 청산
                    if s_.get('ends_before_period_end') and s_.get('last_tradable_i') == i:
                        _close_now(code, p, i, s_['c'][i], '상장폐지 청산(delisted)', day)
                        trades[-1]['delisted'] = True
                        if s_.get('delisting_confirmed'):
                            adj_stats['delisted_exits'] += 1
                        else:
                            trades[-1]['evaluation'] = 'unevaluable_delisting'
                            adj_stats['unevaluable_delistings'] += 1
                        continue
                    # 계수 미확정 단절이 공시돼 있고 오늘이 그 전 '거래 가능한 마지막 날'이면 오늘 종가로 사전 청산(D12 ②)
                    if s_['breaks']:
                        _bi = bisect.bisect_right(s_['breaks'], day)
                        if _bi < len(s_['breaks']):
                            _nb = s_['breaks'][_bi]
                            if last_tradable_day_before_break(s_, s_['disc'].get(_nb), _nb) == day:
                                _close_now(code, p, i, s_['c'][i], '단절 전 청산(D12, 공시 후)', day)
                                adj_stats['break_liquidations'] += 1
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
                    _note_evidence(code, day)
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
            curr, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx[code], sd[code]['c'])
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], 300)
            cash += p['shares'] * p['entry'] + pnl
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                            'entry': p['entry'], 'exit': curr, 'pnl_pct': net_pct,
                            'reason': final_reason, 'pnl': round(pnl, 0)})

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
                          "max_drawdown_pct": round(max_dd, 2),
                          **({"adjusted_stats_w5b": adj_stats} if adjusted_prices else {})}), run_id))
        conn.commit()
        try:  # daily mark-to-market curve -> backtest_equity_curve (2026-09-26); never break the run itself
            import backtest_equity
            backtest_equity.save_run_curve(conn, run_id, {'equity_curve': equity_curve})
            conn.commit()
        except Exception:  # noqa: BLE001
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=False)
        evidence.persist(run_id, trades)
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


