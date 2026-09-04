"""
golden_cross.py -- run_backtest_golden_cross()
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
    _CHART_TOP_MIN,
    _SECTOR_GROUPS,
    _chart_bottom_confluence,
    _chart_prep,
    _chart_top_confluence,
    _load_trade_signals,
    _ma,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _sector_score_memo,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_golden_cross(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    run_name: str = None, run_id: str = None,
    cross_days: int = 15,
    vol_ratio: float = 1.2,
    rs6m_min: float = -20.0,
    trail_pct: float = -0.30,  # 2026-07-21 -0.25→-0.30: 연속운용(2020-03~2026-03) 212.08%→215.65%, 승률27.6→28.8%, trail_big는 그대로 유지해야 개선(둘 다 -0.30로 맞추면 209.27%로 오히려 악화 — 메가수익주 확장구간을 건드리면 안 됨)
    trail_big: float = -0.35,
    stop: float = -0.12,
    max_hold: int = 300,
    max_new_per_month: int = 8,
    min_mktcap: float = 4000,  # 2026-08-10: 2000→4000 변경. 사용자 지시("10버거 종목을 확대하되
                                # 아닌 종목을 걸러낼수 있는 지표")로 384건 교차전략(golden_cross 등
                                # 4개 전략) 텐버거 매칭 데이터에서 golden_cross 단독(238건) 성공/손절
                                # 비교 시 시총이 단조판별력 보유(baseline succ33.8%→mktcap1조+ succ42.2%,
                                # 학습/검증 양쪽 방향일치+강화 확인). 실제 전략 min_mktcap을
                                # 2000/3000/4000/5000/6000/7000/8000/10000 전수 스윕한 결과 5000만
                                # fat-tail 이상치(특정 대형주 3종목이 우연히 그 임계값에서만 편입, 지속
                                # 검증 불가로 폐기)이고 3000~8000은 대체로 견고. 6기간 매트릭스 공정비교:
                                # baseline avg6=25.28%(4/6양수) → 3000=33.33% → **4000=35.48%**(4/6양수,
                                # 거의 전구간 개선 — 하락장 방어도 개선, 최근/최신 구간 대폭개선).
    hot_sector_boost: bool = False,  # 2026-07-13 철회: as-of 유니버스에서 무효(+15.7 vs 무부스트 +17.4) — 채택 근거가 유니버스 룩어헤드였음
    big_gate: float = 0.50,
    hot_boost_1: float = 15.0,
    hot_boost_2: float = 5.0,
    hot_top_n: int = 2,
    prog_bonus_pt: float = None,    # 프로그램매매 5일 누적 순매수 양수 종목 랭킹 보너스 (예: 10.0)
    export_bonus_pt: float = None,  # 수출 YoY 양수(2개월 시차 as-of) 종목 랭킹 보너스 (예: 10.0)
    sector_export_bonus_pt: float = None,  # 섹터 실물수출지표(관세청 44종→sector_mid 매핑) YoY>+5% 랭킹 보너스 — 2026-07-12 기각(시차 지표가 모멘텀 랭킹 오염)
    gc_flow_bonus_pt: float = None,        # 기관+외인 5일 순매수 양수 랭킹 보너스 — 2026-07-12 기각(추세초기 수급 부재)
    pyramid_gain: float = None,            # 보유 수익 +N 도달 시 0.5티켓 추가매수 1회 (예: 0.15) — 이기는 포지션 증액
    compounding: bool = False,             # 복리 모드: 티켓 = 현재 에쿼티/max_positions (기본 고정 1,000만)
    asof_mktcap: bool = True,              # 2026-07-13 기본화: 시총 필터를 as-of(진입일 주가×상장주식수)로 적용 — 현재시총 룩어헤드 제거. as-of 실측 avg6 +17.4%(부스트 없음, 2/6 양수)
    avoid_overheat: float = 1.0,           # 2026-07-13 기본 채택: 진입일 40일 수익률 +100% 초과 급등주 진입 제외.
                                            # 근거: 스냅샷 라벨 실증(급등직후 6개월 -30%하락률 37~41%, 기준율 3.6배) →
                                            # as-of 6기간 백테스트 baseline -0.6% → oh0.7 +20.5% / oh1.0 +17.8%(3/6양수, 채택) / oh1.5 +14.9% (전 범위 강건)
    chart_confluence: bool = False,        # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    market_ma_gate: int = None,            # KOSPI가 N일선 아래면 신규 진입만 중단(보유/청산 불변)
) -> str:
    """
    골든크로스 모멘텀 전략 (V-GC).
    hot_sector_boost=True(선택): 진입 랭킹에 주도섹터 보너스 추가 — 당일 기준(as-of)
    sector_large별 유니버스 평균 ret20 랭킹 1위 섹터 +15pt, 2위 +5pt.
    (2026-07-11 StockEasy 주도섹터 바스켓 실증에서 착안, 룩어헤드 없음)
    trail_big -0.35: +50% 이상 수익 구간 추적손절 완화 — 메가수익주(금양 +442% 등) 보유 지속.
    2026-07-13 편향 정정 결과 (6기간, 다음날 시가·as-of 근사 시총):
      기본선 avg6 -0.6%(2/6 양수), 40거래일 +100% 초과 과열 제외 시 +17.8%(3/6 양수).
      과거 +71.1% 결과는 현재 유니버스/시총 룩어헤드가 포함되어 폐기한다.
    ⚠️ sector_large는 현재 시점 분류를 과거에 적용하므로 PIT 섹터 이력 구축 전까지 연구용이다.
    RS6M 상대강도로 후보 종목 정렬 후 진입, Trail-25%/30% 추적손절 매도.
    시총 2000억+(중대형주) 종목 대상, 분할/합병 미조정 데이터 자동 제거.
    - 2000억 이상으로 제한: 소형주 골든크로스는 약세/회복장에서 성능 급락(avg5 -11%→+28%로 개선).
    """
    init_backtest_db()
    run_name = run_name or f"V-GC골든크로스Trail25 {start_date[:7]}~{end_date[:7]}"
    # 방법론 메타 기록 (Codex P0-2)
    _gc_params = {
        "cross_days": cross_days, "vol_ratio": vol_ratio, "rs6m_min": rs6m_min,
        "trail_pct": trail_pct, "trail_big": trail_big, "stop": stop, "max_hold": max_hold,
        "min_mktcap": min_mktcap, "hot_sector_boost": hot_sector_boost,
        "avoid_overheat": avoid_overheat, "asof_mktcap": asof_mktcap,
        "chart_confluence": chart_confluence, "market_ma_gate": market_ma_gate,
        "per_stock": per_stock, "max_positions": max_positions,
        "start": start_date, "end": end_date,
    }
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,'golden_cross',?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running',strategy='golden_cross' WHERE run_id=?",
                     (run_id,))
        conn.commit()

    _record_run_spec(
        run_id, "golden_cross", "gc_v3_overheat_20260713", _gc_params,
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        allocation_rule=("compounding" if compounding else "fixed_slot"),
    )

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        # KOSPI 데이터 (RS6M 계산용)
        kospi_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0
            ORDER BY date
        """).fetchall()
        k_dates = [r[0] for r in kospi_rows]
        k_prices = [float(r[1]) for r in kospi_rows]
        k_idx = {d: i for i, d in enumerate(k_dates)}

        def _get_k6m(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date:
                        idx = k_idx[d]; break
            if idx is None or idx < 126: return None
            p0, p126 = k_prices[idx], k_prices[idx - 126]
            return (p0 / p126 - 1) * 100 if p126 > 0 else None

        def _market_gate_open(day: str) -> bool:
            if not market_ma_gate:
                return True
            idx = k_idx.get(day)
            if idx is None:
                return False
            window = int(market_ma_gate)
            if window <= 1 or idx + 1 < window:
                return False
            return k_prices[idx] >= sum(k_prices[idx - window + 1:idx + 1]) / window

        # 종목 로드 (시총 min_mktcap+, KOSPI/KOSDAQ, 6자리 코드)
        # stock_universe.market_cap은 억원 단위 → 500억 = 500
        codes = conn.execute("""
            SELECT DISTINCT p.stock_code, su.market_cap
            FROM price_history p
            INNER JOIN stock_universe su ON p.stock_code = su.stock_code
            WHERE p.date >= ? AND p.date <= ? AND p.close >= 1000
            AND su.market_cap >= ?
            AND su.market IN ('KOSPI','KOSDAQ')
            AND LENGTH(p.stock_code) = 6
            AND p.stock_code NOT LIKE '%^%'
            AND p.stock_code NOT LIKE 'GC%'
            AND p.stock_code NOT LIKE 'CL%'
            AND p.stock_code NOT LIKE '%-F'
            AND p.stock_code NOT LIKE '%=%'
            AND p.stock_code NOT LIKE 'NQ%'
            ORDER BY su.market_cap DESC
        """, (start_date, end_date, 0 if asof_mktcap else min_mktcap)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _gc_shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        for code, mktcap in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume, 0), COALESCE(open, close),
                       COALESCE(high, close), COALESCE(low, close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>=500
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 145: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 미조정 필터: 하루 ±50% 이상 변동 제거
            has_artifact = any(
                c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.5 or c_list[i]/c_list[i-1] > 2.0)
                for i in range(1, len(c_list))
            )
            if has_artifact: continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'o': [float(r[3]) for r in rows],
                'h': [float(r[4]) for r in rows],
                'lo': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 500,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        # conn은 _is_sector_buy 에서 계속 사용되므로 루프 후에 닫는다

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx: Dict[str, Dict[str, int]] = {
            c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()
        }

        # 주도섹터 부스트: sector_large 매핑 로드 (as-of ret20으로 일별 랭킹 계산)
        sec_of: Dict[str, str] = {}
        if hot_sector_boost:
            for r in conn.execute(
                "SELECT stock_code, sector_large FROM stock_universe "
                "WHERE stock_code IN ({})".format(",".join("?" * len(sd))),
                list(sd.keys()),
            ).fetchall():
                if r[1] and r[1] != '기타':
                    sec_of[r[0]] = r[1]

        def _hot_sectors_of(day: str) -> dict:
            """당일 기준 섹터별 유니버스 평균 ret20 → {섹터: 랭크(1|2)}. 룩어헤드 없음."""
            agg: Dict[str, list] = {}
            for code2, s2 in sd.items():
                sec2 = sec_of.get(code2)
                if not sec2:
                    continue
                i2 = didx[code2].get(day)
                if i2 is None or i2 < 20:
                    continue
                c0, c20 = s2['c'][i2], s2['c'][i2 - 20]
                if c20 > 0:
                    agg.setdefault(sec2, []).append(c0 / c20 - 1)
            avg2 = {k: sum(v) / len(v) for k, v in agg.items() if len(v) >= 5}
            top = sorted(avg2.items(), key=lambda x: -x[1])[:hot_top_n]
            return {s3: (i3 + 1) for i3, (s3, a3) in enumerate(top) if a3 >= 0.03}

        # 프로그램매매 보너스: 종목별 일자→순매수량 로드 (as-of 5일 누적, 데이터 2020-12~)
        prog_map: Dict[str, Dict[str, float]] = {}
        if prog_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, dt, AVG(net_buy_qty) FROM broker_program_stock_daily "
                "WHERE dt>=? AND dt<=? AND net_buy_qty IS NOT NULL AND stock_code IN ({}) "
                "GROUP BY stock_code, dt".format(",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                prog_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _prog_positive(code: str, day: str) -> bool:
            m = prog_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        # 수출 YoY 보너스: hs_trade_lab 월별 수출 (공표시차 2개월 as-of)
        export_map: Dict[str, Dict[str, float]] = {}
        if export_bonus_pt is not None:
            export_map = _load_trade_signals()

        # 섹터 실물수출지표 보너스: 관세청 44종(quant_major_indicator_series) → sector_mid 매핑
        SEC_EXPORT_MAP = {
            'public:23:1':  ['자동차 및 부품', '자동차 신품 부품 제조업', '자동차 차체나 트레일러 제조업'],
            'public:23:2':  ['자동차 및 부품', '자동차 신품 부품 제조업'],
            'public:23:27': ['자동차 및 부품'], 'public:23:33': ['자동차 및 부품'],
            'public:23:4':  ['반도체', '반도체 제조업'], 'public:23:5': ['반도체', '반도체 제조업'],
            'public:23:6':  ['반도체', '반도체 제조업'], 'public:23:32': ['반도체', '반도체 제조업'],
            'public:23:40': ['반도체', '전자부품 제조업'],
            'public:23:11': ['디스플레이', '전자부품 제조업'],
            'public:23:12': ['전자부품 제조업'], 'public:23:13': ['전자부품 제조업'],
            'public:23:7':  ['선박 및 보트 건조업'], 'public:23:21': ['선박 및 보트 건조업'],
            'public:23:8':  ['소재'], 'public:23:20': ['소재'], 'public:23:34': ['소재'], 'public:23:39': ['소재'],
            'public:23:18': ['1차 비철금속 제조업'], 'public:23:19': ['1차 비철금속 제조업'],
            'public:23:9':  ['생활용품'],
            'public:23:10': ['제약 및 바이오', '의약품 제조업', '기초 의약물질 제조업'],
            'public:23:25': ['제약 및 바이오', '의료장비 및 서비스'],
            'public:23:26': ['제약 및 바이오', '의약품 제조업'],
            'public:23:24': ['의료장비 및 서비스'],
            'public:23:14': ['에너지'], 'public:23:35': ['에너지'],
            'public:23:17': ['기초 화학물질 제조업', '기타 화학제품 제조업', '합성고무 및 플라스틱 물질 제조업'],
            'public:23:41': ['기초 화학물질 제조업'], 'public:23:42': ['기초 화학물질 제조업', '기타 화학제품 제조업'],
            'public:23:22': ['특수 목적용 기계 제조업', '일반 목적용 기계 제조업'],
            'public:23:23': ['특수 목적용 기계 제조업', '자본재'],
            'public:23:38': ['자본재', '특수 목적용 기계 제조업'],
            'public:23:36': ['자본재'], 'public:23:37': ['자본재'], 'public:23:43': ['자본재'],
            'public:23:29': ['내구 소비재 및 의류', '봉제의복 제조업'],
            'public:23:30': ['음식료 및 담배'], 'public:23:31': ['음식료 및 담배'],
            'public:23:44': ['펄프, 종이 및 판지 제조업'],
        }
        sec_export_series: Dict[str, Dict[str, float]] = {}   # indicator_key → {ym: 수출액}
        smid_of: Dict[str, str] = {}                           # stock_code → sector_mid
        smid_indicators: Dict[str, list] = {}                  # sector_mid → [indicator_key]
        if sector_export_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT indicator_key, period, value FROM quant_major_indicator_series "
                "WHERE indicator_key LIKE 'public:23:%' AND series_name LIKE '%수출액' AND value>0"
            ).fetchall():
                sec_export_series.setdefault(r[0], {})[r[1]] = float(r[2])
            for r in conn.execute(
                "SELECT stock_code, sector_mid FROM stock_universe WHERE stock_code IN ({})".format(
                    ",".join("?" * len(sd))), list(sd.keys())).fetchall():
                if r[1]:
                    smid_of[r[0]] = r[1]
            for ik, mids in SEC_EXPORT_MAP.items():
                for mid in mids:
                    smid_indicators.setdefault(mid, []).append(ik)

        # 기관+외인 수급 보너스 (V-RECOVERY 채택 신호 교차이식)
        gc_flow_map: Dict[str, Dict[str, float]] = {}
        if gc_flow_bonus_pt is not None and sd:
            for r in conn.execute(
                "SELECT stock_code, date, COALESCE(inst_net_buy,0)+COALESCE(frn_net_buy,0) "
                "FROM price_history WHERE date>=? AND date<=? AND close>0 AND stock_code IN ({})".format(
                    ",".join("?" * len(sd))),
                [warmup_start, end_date] + list(sd.keys())).fetchall():
                gc_flow_map.setdefault(r[0], {})[r[1]] = float(r[2] or 0)

        def _gc_flow_positive(code: str, day: str) -> bool:
            m = gc_flow_map.get(code)
            if not m:
                return False
            i0 = didx[code].get(day)
            if i0 is None:
                return False
            days5 = sd[code]['d'][max(0, i0 - 4):i0 + 1]
            vals = [m[d5] for d5 in days5 if d5 in m]
            return bool(vals) and sum(vals) > 0

        def _sector_export_hot(code: str, day: str) -> bool:
            """종목 sector_mid에 매핑된 관세청 수출지표(2개월 시차 as-of) YoY > +5% 여부."""
            iks = smid_indicators.get(smid_of.get(code, ""))
            if not iks:
                return False
            y, mo = int(day[:4]), int(day[5:7])
            mo -= 2
            if mo <= 0:
                mo += 12; y -= 1
            ym_ref, ym_prev = f"{y:04d}-{mo:02d}", f"{y-1:04d}-{mo:02d}"
            for ik in iks:
                m = sec_export_series.get(ik)
                if not m:
                    continue
                cur, prev = m.get(ym_ref), m.get(ym_prev)
                if cur and prev and prev > 0 and cur / prev - 1 > 0.05:
                    return True
            return False

        def _export_yoy_positive(code: str, day: str) -> bool:
            m = export_map.get(code)
            if not m:
                return False
            y, mo = int(day[:4]), int(day[5:7])
            mo -= 2  # 공표 시차 2개월
            if mo <= 0:
                mo += 12; y -= 1
            ym_ref = f"{y:04d}-{mo:02d}"
            ym_prev = f"{y-1:04d}-{mo:02d}"
            cur, prev = m.get(ym_ref), m.get(ym_prev)
            if not cur or not prev or prev <= 0:
                return False
            return cur / prev - 1 > 0

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        nm: Dict[str, int] = {}
        eq_peak = cash          # MDD 추적 (2026-07-12)
        max_dd = 0.0
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        def _gc_equity(day: str) -> float:
            return cash + sum(
                p['qty'] * (sd[code]['c'][didx[code][day]] if day in didx[code] else p['entry'])
                for code, p in pos.items()
            )

        def _gc_limit(day: str) -> int:
            return max(max_positions, int(_gc_equity(day) // per_stock))

        for day in sim_dates:
            ym = day[:7]

            # 전일 종가 이후 생성된 주문을 다음 거래일 시가에 체결한다.
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos: continue
                p = pos.pop(code)
                fill = sd[code]['o'][i]
                net_amt, net_pct = _net_profit(
                    p['entry'], fill, p['qty'], p.get('mkt_cap_억', sd[code].get('mkt_cap_억', 500)))
                cash += p['entry'] * p['qty'] + net_amt
                trades.append({'stock_code': code, 'entry_date': p['entry_date'],
                               'exit_date': day, 'entry_price': p['entry'],
                               'exit_price': fill, 'qty': p['qty'],
                               'profit_pct': net_pct, 'profit_amt': net_amt,
                               'hold_days': p.get('hold', 0), 'exit_reason': reason})
                del pending_sells[code]

            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None: continue
                if code not in pos and len(pos) < _gc_limit(day):
                    fill = sd[code]['o'][i]
                    qty = int(min(per_stock, cash) // fill)
                    if qty > 0:
                        cash -= qty * fill
                        entry_mktcap = sd[code].get('mkt_cap_억', 500)
                        if asof_mktcap:
                            entry_shares = _gc_shares_asof(code, day)
                            if entry_shares > 0:
                                entry_mktcap = entry_shares * fill / 1e8
                        pos[code] = {'entry': fill, 'entry_date': day, 'qty': qty,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': entry_mktcap}
                        nm[ym] = nm.get(ym, 0) + 1
                pending_buys.remove(code)

            for code, p in list(pos.items()):
                if code in pending_sells: continue
                i = didx[code].get(day)
                if i is None: continue
                c = sd[code]['c'][i]
                entry = p['entry']
                pct = (c - entry) / entry
                hold = p.get('hold', 0)
                peak = p.get('peak', entry)
                if c > peak: p['peak'] = c; peak = c
                trail = (c - peak) / peak if peak > 0 else 0
                reason = None
                if pct <= stop:
                    reason = '손절'
                elif hold >= 10:
                    cur_trail = trail_big if (pct > big_gate) else trail_pct
                    if pct > 0.05 and trail <= cur_trail:
                        reason = 'Trail'
                # V-GC 데스크로스 테스트: avg5 +50%→-9.6% 급락으로 제거 (trail stop이 더 효과적)
                # 고점 컨플루언스 청산 (2026-07-18 공통모듈)
                if reason is None and chart_confluence and pct >= 0.10:
                    s_ = sd[code]
                    if _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN:
                        reason = 'chart_top'
                if reason is None and hold >= max_hold:
                    reason = '만료'
                p['hold'] = hold + 1
                if reason:
                    pending_sells[code] = reason
                elif (pyramid_gain is not None and not p.get('pyr')
                      and pct >= pyramid_gain and hold >= 10):
                    # 피라미딩: 이기는 포지션에 0.5티켓 추가 (1회, 평균단가 블렌딩)
                    add_qty = int(per_stock * 0.5 / c)
                    if add_qty > 0 and add_qty * c <= cash:
                        cash -= add_qty * c
                        p['entry'] = (entry * p['qty'] + c * add_qty) / (p['qty'] + add_qty)
                        p['qty'] += add_qty
                        p['pyr'] = True
            # 진입 (매월 max_new_per_month건 제한)
            if (
                _market_gate_open(day)
                and len(pos) + len(pending_buys) < _gc_limit(day)
                and nm.get(ym, 0) < max_new_per_month
            ):
                hot_map = _hot_sectors_of(day) if hot_sector_boost else {}
                cands = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys or code in pending_sells: continue
                    i = didx[code].get(day)
                    if i is None or i < 145: continue
                    c = s['c'][i]
                    if c < 1000: continue
                    if asof_mktcap:
                        sh = _gc_shares_asof(code, day)
                        if sh <= 0 or sh * c / 1e8 < min_mktcap:
                            continue
                    ma20 = _ma(s['c'][:i+1], 20)
                    ma60 = _ma(s['c'][:i+1], 60)
                    if not (ma20 and ma60 and ma20 > ma60): continue
                    # 최근 cross_days 내 골든크로스 발생 체크
                    crossed = False
                    for back in range(2, cross_days + 1):
                        if i < back: break
                        m20b = _ma(s['c'][:i-back+1], 20)
                        m60b = _ma(s['c'][:i-back+1], 60)
                        if m20b and m60b and m20b <= m60b:
                            crossed = True; break
                    if not crossed: continue
                    # 거래량 확인
                    v5 = sum(s['v'][max(0, i-4):i+1]) / 5
                    v20 = sum(s['v'][max(0, i-19):i+1]) / 20
                    if v20 <= 0 or v5 < v20 * vol_ratio: continue
                    # RS6M
                    if i < 126: continue
                    prev126 = s['c'][i - 126]
                    if prev126 <= 0: continue
                    k6m = _get_k6m(day)
                    if k6m is None: continue
                    rs6m = (c / prev126 - 1) * 100 - k6m
                    if rs6m < rs6m_min: continue
                    # 과열 회피 (2026-07-13 실증: 60일 +100% 급등 종목의 6개월 -30%하락률 37~41%, 기준율 3.6배)
                    if avoid_overheat is not None and i >= 40:
                        _c40 = s['c'][i - 40]
                        if _c40 > 0 and (c / _c40 - 1) > avoid_overheat:
                            continue
                    # 섹터 보너스: BUY 섹터 종목 우선순위 상승
                    sector_bonus = 10.0 if _get_stock_sector_key(code) else 0.0
                    if sector_bonus > 0 and _is_sector_buy(conn, code, day, threshold=55.0):
                        sector_bonus = 25.0  # BUY 섹터 종목에게 RS6M +25pt 보너스
                    if hot_sector_boost:
                        hr = hot_map.get(sec_of.get(code, ""))
                        if hr == 1:
                            sector_bonus += hot_boost_1
                        elif hr is not None:
                            sector_bonus += hot_boost_2
                    if prog_bonus_pt is not None and _prog_positive(code, day):
                        sector_bonus += prog_bonus_pt
                    if export_bonus_pt is not None and _export_yoy_positive(code, day):
                        sector_bonus += export_bonus_pt
                    if sector_export_bonus_pt is not None and _sector_export_hot(code, day):
                        sector_bonus += sector_export_bonus_pt
                    if gc_flow_bonus_pt is not None and _gc_flow_positive(code, day):
                        sector_bonus += gc_flow_bonus_pt
                    # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                    if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                        continue
                    cands.append((code, c, rs6m + sector_bonus))

                cands.sort(key=lambda x: -x[2])  # RS6M+섹터보너스 내림차순
                available = max(0, _gc_limit(day) - len(pos) - len(pending_buys))
                queued_now = 0
                for code, price, _ in cands:
                    if queued_now >= available: break
                    if nm.get(ym, 0) + queued_now >= max_new_per_month: break
                    pending_buys.append(code)
                    queued_now += 1

            # 일별 에쿼티 마킹 → MDD
            mark = _gc_equity(day)
            if mark > eq_peak:
                eq_peak = mark
            elif eq_peak > 0:
                dd = (mark - eq_peak) / eq_peak * 100
                if dd < max_dd:
                    max_dd = dd

        # 잔존 포지션 청산
        for code, p in pos.items():
            c = sd[code]['c'][-1]
            last_date = sd[code]['d'][-1]
            net_amt, net_pct = _net_profit(p['entry'], c, p['qty'],
                                           p.get('mkt_cap_억', sd[code].get('mkt_cap_억', 500)))
            cash += p['entry'] * p['qty'] + net_amt
            trades.append({
                'stock_code': code,
                'entry_date': p['entry_date'],
                'exit_date': last_date,
                'entry_price': p['entry'],
                'exit_price': c,
                'qty': p['qty'],
                'profit_pct': net_pct,
                'profit_amt': net_amt,
                'hold_days': p.get('hold', 0),
                'exit_reason': '잔존',
            })

        # 성과 집계
        total_cap = per_stock * max_positions
        total_ret_pct = round((cash - total_cap) / total_cap * 100, 2)
        n_trades = len(trades)
        win_trades = sum(1 for t in trades if t['profit_pct'] > 0)
        win_rate = round(win_trades / max(n_trades, 1) * 100, 1)
        avg_pct = round(sum(t['profit_pct'] for t in trades) / max(n_trades, 1), 2)
        exit_reasons = {}
        for t in trades:
            exit_reasons[t['exit_reason']] = exit_reasons.get(t['exit_reason'], 0) + 1
        days = max((datetime.strptime(end_date, '%Y-%m-%d')
                    - datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
        cagr = round(((cash / total_cap) ** (365.0 / days) - 1) * 100, 2)

        summary = (
            f"전략: V-GC 골든크로스 모멘텀  |  기간: {start_date}~{end_date}\n"
            f"진입: MA20골든크로스MA60({cross_days}일내) + 거래량{vol_ratio}배 + RS6M>{rs6m_min:.0f}\n"
            f"매도: Trail{trail_pct*100:.0f}%/Trail{trail_big*100:.0f}%(50%이익이상) + 손절{stop*100:.0f}%\n"
            f"대상: 시총{min_mktcap:.0f}억+ KOSPI/KOSDAQ / 엄격 다음날시가·정수주식·실제현금·동적슬롯\n"
            f"총 거래: {n_trades}건  승률: {win_rate}%  평균: {avg_pct:+.1f}%\n"
            f"총수익률: {total_ret_pct:+.2f}%  CAGR: {cagr:+.2f}%  MDD: {max_dd:.1f}%\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )

        conn.close()
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        # 종목명 매핑
        all_codes = list({t['stock_code'] for t in trades})
        name_map: Dict[str, str] = {}
        for i in range(0, len(all_codes), 100):
            batch = all_codes[i:i+100]
            ph = ','.join('?' * len(batch))
            for sc, sn in conn2.execute(
                f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_code IN ({ph})",
                batch
            ).fetchall():
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        conn2.execute("""
            UPDATE backtest_runs SET
                status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?, strategy=?,
                max_drawdown_pct=?
            WHERE run_id=?
        """, (total_ret_pct, cagr, win_rate, n_trades, win_trades, summary,
              json.dumps(trades, ensure_ascii=False), 'golden_cross',
              round(max_dd, 2), run_id))
        conn2.commit()
        conn2.close()
        _register_execution_artifacts(run_id, total_cap, cash)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=120)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                       (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise



# ─────────────────────────────────────────────────────────────────────
# V-SECTOR: 섹터 로테이션 집중 투자 전략
# 섹터 BUY 신호(점수 >= 65) 발생 시 해당 섹터 급등 후보 TOP3 집중 투자
# 급등점수 = 영업이익YoY(40) + 기관집중도%(30) + 52주저점(20) + 소형주(10)
# ─────────────────────────────────────────────────────────────────────

# 섹터 그룹 정의 (routes/sector_rotation.py 와 동기화)


def _sector_score_as_of(conn: sqlite3.Connection, sector_key: str, as_of: str) -> float:
    """백테스트용 — 특정 날짜 기준 섹터 점수 계산 (간소화 버전).
    외인3M + 기관3M 수급 + 영업이익YoY + 섹터 3M 가격 모멘텀을 실제 DB 데이터로 계산.
    """
    info = _SECTOR_GROUPS.get(sector_key, {})
    codes = info.get("codes", [])
    if not codes:
        return 0.0

    d_3m   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")
    d_1y   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=400)).strftime("%Y-%m-%d")
    d_2y   = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=765)).strftime("%Y-%m-%d")
    ph_sql = "({})".format(",".join("?" * len(codes)))

    frn_3m = (conn.execute(
        f"SELECT SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0 "
        f"THEN frn_net_buy_amt/100.0 ELSE COALESCE(frn_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
        f"WHERE stock_code IN {ph_sql} AND date>=? AND date<=? "
        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
        codes + [d_3m, as_of]
    ).fetchone() or (0,))[0] or 0.0

    inst_3m = (conn.execute(
        f"SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
        f"THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
        f"WHERE stock_code IN {ph_sql} AND date>=? AND date<=? "
        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
        codes + [d_3m, as_of]
    ).fetchone() or (0,))[0] or 0.0

    # 영업이익 YoY (섹터 합산)
    cur_year  = str(int(as_of[:4]))
    prev_year = str(int(as_of[:4]) - 1)
    op_cur = (conn.execute(
        f"SELECT SUM(operating_profit) FROM financial_data "
        f"WHERE stock_code IN {ph_sql} AND is_annual=1 AND year=?",
        codes + [cur_year]
    ).fetchone() or (None,))[0]
    op_prev = (conn.execute(
        f"SELECT SUM(operating_profit) FROM financial_data "
        f"WHERE stock_code IN {ph_sql} AND is_annual=1 AND year=?",
        codes + [prev_year]
    ).fetchone() or (None,))[0]

    op_yoy = 0.0
    if op_cur and op_prev and op_prev != 0:
        op_yoy = (op_cur - op_prev) / abs(op_prev) * 100

    ret3_values = []
    for code in codes:
        p_now = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, as_of),
        ).fetchone()
        p_3m = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, d_3m),
        ).fetchone()
        if p_now and p_3m and p_3m[0]:
            ret3_values.append((float(p_now[0]) / float(p_3m[0]) - 1) * 100)
    sector_ret3 = sorted(ret3_values)[len(ret3_values) // 2] if ret3_values else 0.0

    score = 0.0
    # 외인 (max 40)
    if   frn_3m >= 30000: score += 40
    elif frn_3m >= 10000: score += 35
    elif frn_3m >=  5000: score += 30
    elif frn_3m >=  1500: score += 22
    elif frn_3m >=   300: score += 12
    elif frn_3m < -5000:  score -= 10
    # 기관 (max 35)
    if   inst_3m >= 20000: score += 35
    elif inst_3m >= 10000: score += 28
    elif inst_3m >=  3000: score += 20
    elif inst_3m >=  1000: score += 12
    elif inst_3m >=   200: score += 6
    elif inst_3m < -3000:  score -= 8
    # 영업이익YoY (max 25)
    if   op_yoy >= 100: score += 25
    elif op_yoy >= 50:  score += 18
    elif op_yoy >= 20:  score += 10
    elif op_yoy >= 0:   score += 4
    elif op_yoy < -30:  score -= 8
    # 섹터 장세 자체가 강하면 지수 레벨과 무관하게 들어갈 수 있도록 반영
    if   sector_ret3 >= 30: score += 25
    elif sector_ret3 >= 20: score += 18
    elif sector_ret3 >= 10: score += 10
    elif sector_ret3 >= 5:  score += 5
    elif sector_ret3 < -10: score -= 8

    return round(score, 1)




def _get_stock_sector_key(code: str) -> Optional[str]:
    """종목 코드 → 섹터 그룹 키"""
    for sk, info in _SECTOR_GROUPS.items():
        if code in info["codes"]:
            return sk
    return None




def _is_sector_buy(conn: sqlite3.Connection, code: str, date: str,
                   threshold: float = 50.0) -> bool:
    """V-GC/V11 섹터 필터용 — BUY 섹터 여부 실시간 계산.
    성능 최적화: 월 단위 캐싱.
    """
    sk = _get_stock_sector_key(code)
    if sk is None:
        return True  # 섹터 미등록 종목은 필터 통과 (기존 전략 유지)

    ym = date[:7]
    cache_key = (sk, ym)
    if cache_key not in _sector_score_memo:
        _sector_score_memo[cache_key] = _sector_score_as_of(conn, sk, date)
    return _sector_score_memo[cache_key] >= threshold


# ─── V-DEEP: 깊은낙폭 반등 집중 전략 ─────────────────────────────────────────



