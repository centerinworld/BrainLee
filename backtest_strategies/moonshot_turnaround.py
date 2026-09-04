"""
moonshot_turnaround.py -- run_backtest_moonshot_turnaround()
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
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_moonshot_turnaround(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 30,       # 2026-07-23 20→30 상향: 사용자 지시("1000% 상승 종목을 찾는게
                                    # 중요, 꼭 다 안먹어도 어깨에 나와도 상관없다")에 따라 "총수익 극대화"
                                    # 보다 "발굴 개수 극대화" 우선 — 실측 20→30 비교: 6기간 avg6
                                    # 18.94%→22.39%(3/6→4/6양수, 개선), 연속운용(2020-03~2026-03)
                                    # 100%+ 대박종목 21건→28건으로 증가(예스24+336.7%·인화정공+254.6%·
                                    # 코스맥스+214.8% 등 추가 포착), 단 연속운용 총수익은 190.31%→
                                    # 160.97%로 하락(자본이 더 얇게 분산돼 개별 대박의 기여도 희석) —
                                    # "총수익"과 "발굴개수"의 트레이드오프이며 사용자 지시대로 후자 우선.
                                    # 분산 바스켓(집중 아님) — "어느 종목이 1000%될지" 사전에 알 수
                                    # 없으므로 넓게 분산해 소수 대박이 다수 소손실을 상쇄하는
                                    # fat-tail 구조를 노림(megatrend/turnaround와 동일 철학).
    entry_score_min: int = 2,      # /api/tenbagger/turnaround-watch comprehensive_score 채택기준
                                    # (walk-forward 검증: 0점 0.52~0.59x/1점 0.79~0.91x/2점 1.13~1.23x/
                                    # 3점 1.38x·검증1.63x). 기본 2점(재도전/매출성장/이익의질 중 2개+).
    dilution_max: int = 3,         # 희석위험(CB/BW/EB/RIGHTS 트레일링365일 공시건수) 배제 상한.
                                    # 검증: 4건+부터 lift 0.90x/0.64x·1년내-30%↓비율 42.6%/49.3%로
                                    # 급격 악화(젬백스 사례) — 3건 이하만 허용.
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,      # 심한 낙폭과대/적자 모집단 특성상 변동성이 커 표준(-20%)보다
                                    # 넓게 설정 — 조기손절로 진짜 턴어라운드를 놓치는 것을 방지.
    trail_pct: float = -0.35,      # 추적손절도 넓게 — 1000%대 승자가 중간조정에 흔들리지 않고
                                    # 최대한 오래 보유되도록(megatrend와 동일 설계).
    max_hold: int = 500,           # 최대 보유 500거래일(~2년) — 턴어라운드가 무르익어 대박으로
                                    # 발전하기까지 megatrend(252일)보다 긴 호흡이 필요하다는 전제.
    asof_mktcap: bool = True,      # 2026-07-27: min_mktcap_억 유니버스 필터가 stock_universe.market_cap
                                    # (현재시총) 정적 컷오프였음(룩어헤드 — 부실기업이 턴어라운드로 몸집이
                                    # 커진 뒤에야 "300억+"로 소급 편입되는 등) — as-of 시총으로 전환.
    panic_stop_loss: float = None,  # 2026-08-09: KOSPI 패닉장(MA120×0.85 미만) 중 -20% 터치 이벤트
                                     # 176건 사후분석 — 패닉장 중 낙폭은 반등확률 30.0%(평균최종-9.8%)
                                     # vs 정상/강세장 중 낙폭은 7.7~20.0%(평균최종-19.8~-28.6%). 시장전체
                                     # 패닉이 원인인 낙폭은 시스템리스크(재평가) 회복 여지가 크고, 강세장
                                     # 중 개별종목만 하락하면 펀더멘털 악재일 가능성이 커 계속 하락하는
                                     # 것으로 추정. None이면 stop_loss 단일값(기존 동작) 유지 — 국면별
                                     # 손절폭을 분리하려면 panic_stop_loss(완화, 예: -0.45)를 지정.
    panic_ma_ratio: float = 0.85,   # 패닉장 판정 임계(KOSPI/MA120)
    include_profitable: bool = False,  # 2026-08-10: 기본 False(기존 동작=TTM적자 모집단만).
                                     # True면 TTM 흑자/분기흑자 종목도 포함 — comprehensive_score
                                     # (재도전+매출YoY성장+이익의질)조건은 그대로 유지하되 population을
                                     # 흑자기업까지 확장. 사용자 지시(2026-08-10): "매수/매도가 워낙 많은
                                     # 범용전략보다 텐버거 전용 로직(익절없음+넓은손절+긴만기)을 흑자
                                     # 모집단에도 넓혀보자" — 실증(capture_rate_analysis) 결과 이 매도
                                     # 설계(손절-35%/trail-35%/만기500일/익절없음)가 v4/v2류(익절있음)
                                     # 대비 텐버거 거래 평균실현수익 3.6~17.5%→75.0%로 압도적 우위였음.
    strict_exec: bool = True,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-MOONSHOT — 턴어라운드 종합스코어 기반 대박발굴 전략.

    [배경] 2026-07-22 사용자 지시 "기존 전략을 그대로 두더라도 핵심종목 1000%씩 오르는 종목의
    발굴에 집중하는것도 괜찮다고 생각합니다" — V-EARNINGS(메가캡 이익가속 집중배분)와는 반대 극단:
    현재는 작고 실적이 안 좋은(TTM 적자) 종목 중에서, 이미 walk-forward로 검증된 3개 독립 리딩
    시그널(재도전 턴어라운드/매출YoY성장/감가상각주도 이익의질, 2026-07-19 turnaround-watch 구축)의
    조합점수(0~3)가 높은 종목을 광범위하게 분산 매수 — 어느 한 종목이 1000%가 될지는 사전에 알 수
    없으므로 집중이 아니라 분산+장기보유로 fat-tail을 노림. 희석위험(CB/BW/EB) 높은 종목은 검증된
    리스크 신호로 배제.

    매수: TTM 순이익≤0(적자 모집단) + comprehensive_score(재도전+매출YoY+이익의질) ≥ entry_score_min
         + 희석위험(트레일링365일 CB/BW/EB건수) ≤ dilution_max — 최대 20종목 균등분산.
    매도: 손절 -35%(하드, 변동성 큰 모집단 감안 확대) / 추적손절 -35%(이익권) / 만료 500거래일(~2년).
    """
    init_backtest_db()
    run_name = run_name or f"V-MOONSHOT {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "moonshot_turnaround", "moonshot_turnaround_v1_20260723",
        {"entry_score_min": entry_score_min, "dilution_max": dilution_max,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "panic_stop_loss": panic_stop_loss, "panic_ma_ratio": panic_ma_ratio,
         "include_profitable": include_profitable,
         "total_capital": total_capital, "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'moonshot_turnaround',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")

        # KOSPI 국면(패닉장 판정용, panic_stop_loss 사용 시에만 의미 — 2026-08-09
        # 사후분석: 원안 완료거래 253건 중 -20%터치 176건 재구성 결과, KOSPI<MA120×0.85
        # 패닉장 중 -20%낙폭 반등확률 30.0%(평균최종-9.8%) vs 정상/강세장 중 낙폭 7.7~20.0%
        # (평균최종-19.8~-28.6%) — 시장전체 패닉발 낙폭은 시스템리스크 회복 여지가 크고,
        # 강세장 중 개별종목만 하락하면 펀더멘털 악재일 가능성이 커 계속 하락하는 것으로 추정)
        _k_rows_ms = conn.execute("SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date").fetchall()
        _k_dates_ms = [r[0] for r in _k_rows_ms]
        _k_prices_ms = [float(r[1]) for r in _k_rows_ms]
        _k_idx_ms = {d: i for i, d in enumerate(_k_dates_ms)}

        def _is_panic_ms(date: str) -> bool:
            if panic_stop_loss is None:
                return False
            idx = _k_idx_ms.get(date)
            if idx is None:
                for d in reversed(_k_dates_ms):
                    if d <= date:
                        idx = _k_idx_ms[d]
                        break
            if idx is None or idx < 120:
                return False
            ma = sum(_k_prices_ms[idx - 119:idx + 1]) / 120
            if ma <= 0:
                return False
            return (_k_prices_ms[idx] / ma) < panic_ma_ratio

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

        def _shares_asof_ms(code: str, day: str) -> float:
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
            # 공용 _release_date() 재사용 — 실제 DART 공시일(fin_disclosure_dates) 우선,
            # 없으면 법정기한(분기+45일 근사) fallback. 과거 하드코딩 공식만 쓰던 버그 수정(2026-08-30).
            return _release_date(y, q, False, code)

        def _dilution_risk(code: str, avail: str) -> int:
            evs = dilution_map.get(code)
            if not evs:
                return 0
            cutoff = (datetime.strptime(avail, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            return sum(1 for d in evs if cutoff <= d <= avail)

        # 종목별 (avail_date, score) 이벤트 리스트를 사전 계산 — turnaround-watch의
        # comprehensive_score(B/D/E 3신호 합산) 로직을 그대로 이식, as-of 판단용으로 재구성.
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
                        continue  # TTM 흑자면 모집단 밖(V-EARNINGS 등 다른 전략의 영역)
                    if ni is not None and ni > 0:
                        continue  # 이번 분기 자체는 흑자(단일분기 흑자전환) — comprehensive_score 모집단은
                                  # "이번 분기도 적자"인 경우만(turnaround-watch B/D/E와 동일 조건)
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
                    # 2026-07-24 조사(에이엘티 사례): 동점(같은 comprehensive_score) 시
                    # candidates.sort(reverse=True)가 (score,code) 튜플을 그대로 내림차순 정렬해
                    # 종목코드 문자열이 큰 코드를 기계적으로 우선시하는 편향을 발견 — rev_yoy를
                    # 2차 정렬기준으로 바꿔봤으나 실측 결과 연속운용 수익률이 오히려 대폭 악화
                    # (2020-03~2026-07-24 기준 137.46%→81.83%, -55.6%p, run_id 비교로 확인).
                    # 추정 원인: 소형주 기저효과로 인한 노이즈성 매출YoY 극단치가 "성숙한" 우선순위
                    # 신호가 되어버려, 기존의 (사실상 무작위에 가까운) 코드순 동점처리보다 못한
                    # 선택을 반복 유발. 검증된 대안이 없어 원래 동작으로 되돌림 — 동점 처리는
                    # 여전히 종목코드 기준이며 투자근거상 의미는 없으나, 이 신호(comprehensive_score)
                    # 자체는 turnaround-watch 발굴용으로만 쓰고 실전 편입 여부는 자본경쟁(30슬롯)
                    # 결과라는 점을 감안할 것 — 에이엘티가 미편입된 것은 편향 때문이 아니라 정상적인
                    # 슬롯 경쟁 결과로 판단됨(CLAUDE.md 참조).
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
            return avail[-1]  # (avail_date, score) — 가장 최근 것

        per_stock = total_capital / max_positions
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
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
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
                _eff_stop = panic_stop_loss if (panic_stop_loss is not None and _is_panic_ms(day)) else stop_loss
                stop_cond = ret <= _eff_stop
                expire_cond = p['hold'] >= max_hold
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
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
                        })
                        pos.pop(code, None)

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
                        _sh = _shares_asof_ms(code, day)
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
                if strict_exec:
                    for _, code in picked:
                        pending_buys.append(code)
                else:
                    for _, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}

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
                'reason': 'final', 'pnl': round(pnl, 0),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100) if trades else 0.0
        avg_ret = sum(t['pnl_pct'] for t in trades) / len(trades) if trades else 0.0
        summary = (f"V-MOONSHOT 턴어라운드종합스코어 대박발굴 | {start_date}~{end_date} | "
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




