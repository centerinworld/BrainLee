"""
high_profit_compound.py -- run_backtest_high_profit_compound()
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
    SignalEvidenceLedger,
    evidence_aggregate,
    DB_PATH,
    _final_liquidation_quote_for_code,
    _record_run_spec,
    _register_execution_artifacts,
    logger,
    sqlite3,
)

def run_backtest_high_profit_compound(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 8,
    stop: float = -0.15,              # 데이터: 손절15%가 avg+22.1% 최고
    trail: float = -0.35,             # 데이터: Trail35%가 3배실현 342건, 5배110건 최고
    trail_big: float = -0.40,         # 이익100%+ 달성 시 Trail40%로 확장
    tp: float = 999.0,                # 익절 없음 — Trail로만 청산 (25배 종목 조기청산 방지)
    max_hold: int = 400,              # 데이터: 고점까지 평균 280~424일 소요
    insider_days: int = 180,          # 임원매수 유효기간
    trail_activate_pct: float = 0.10, # Trail 발동 최소 이익 (데이터: 10%+ 이후 추세 형성)
    min_turnover_m: float = 20.0,     # 최소 일거래대금 (억원)
    sectors: tuple = ('IT', '의료', '경기소비재', '산업재'),
    run_name: str = None,
    run_id: str = None,
) -> str:
    """V13 고수익 집중 — 임원매수+성장섹터+계약/수주 복합 신호. 데이터 기반 최적화.
    진입: ①임원매수(180일) ②IT·의료·경기소비재·산업재 ③MA20 위(반등 확인) ④거래대금20억+ ⑤계약or수주잔고
    매도(데이터 기반): Trail-35%(이익10%+후)/이익100%+시Trail-40% / 손절-15% / 만료400일
    ※ 익절라인 없음 — 실제 3배 종목은 25배까지 가므로 Trail로만 청산
    """
    import uuid as _uuid
    rid = run_id or str(_uuid.uuid4())[:8]
    run_name = run_name or f"[V13] {start_date[:7]}~{end_date[:7]}"

    # 2026-07-18: 이 엔진도 V4와 동일하게 run_spec 기록이 전혀 없어(_record_run_spec 미호출)
    # trg_backtest_done_requires_spec 트리거에 항상 막혀 완료되지 못하던 버그 — 연속운영
    # 실측 시도 중 발견. 실제로는 정수주식이 아닌 금액모델(per_stock 고정배분)이라
    # "레거시" 등급으로 정직하게 기록.
    _record_run_spec(
        rid, "high_profit_compound", "v13_legacy_money_model",
        {"per_stock": per_stock, "max_positions": max_positions, "stop": stop,
         "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode="current", allocation_rule="fixed_slot",
        universe_version="stock_universe_current",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # trg_backtest_insert_done_requires_spec가 status='done' 직접 INSERT를 항상 막으므로
    # (스펙 존재 여부 무관, running으로 먼저 넣고 나중에 done으로 갱신하는 생명주기를 강제)
    # 다른 엔진들과 동일하게 running으로 먼저 등록해야 한다(2026-07-18, V4와 같은 계열 버그).
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'high_profit_compound',?,?,?,?,'running')
    """, (rid, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=280)).strftime('%Y-%m-%d')

    # 전체 거래일 목록
    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(date,1,10) FROM price_history WHERE substr(date,1,10) BETWEEN ? AND ? ORDER BY 1",
        (warmup_start, end_date)
    ).fetchall()]
    sim_dates = [d for d in all_dates if d >= start_date]

    sector_ph = ",".join("?" * len(sectors))

    # KOSPI MA60 사전 계산 (하락장 진입 금지 필터)
    _kospi_rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date"
    ).fetchall()
    _k_dates_v13  = [r[0] for r in _kospi_rows]
    _k_prices_v13 = [float(r[1]) for r in _kospi_rows]
    _k_idx_v13    = {d: i for i, d in enumerate(_k_dates_v13)}

    def _k_ma60_v13(date: str) -> Optional[float]:
        idx = _k_idx_v13.get(date)
        if idx is None:
            for d in reversed(_k_dates_v13):
                if d <= date: idx = _k_idx_v13[d]; break
        if idx is None or idx < 60: return None
        return sum(_k_prices_v13[idx-59:idx+1]) / 60

    def _k_price_v13(date: str) -> float:
        idx = _k_idx_v13.get(date)
        if idx is None:
            for d in reversed(_k_dates_v13):
                if d <= date: idx = _k_idx_v13[d]; break
        return _k_prices_v13[idx] if idx is not None else 0.0

    # 임원매수 공시 캐시: {date → set of stock_codes with recent insider buy}
    def _insider_buy_codes(as_of: str) -> set:
        cutoff = (datetime.strptime(as_of, '%Y-%m-%d') - timedelta(days=insider_days)).strftime('%Y-%m-%d')
        rows = conn.execute(
            """SELECT DISTINCT stock_code FROM dart_insider_holdings
               WHERE rcept_dt BETWEEN ? AND ?
                 AND COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) > 0""",
            (cutoff, as_of)
        ).fetchall()
        return {r[0] for r in rows}

    # 2026-09-22 수정(룩어헤드 편향 발견·수정): 이전엔 "연도 변동이 적으므로"라는
    # 검증 안 된 가정으로 dart_contracts/order_backlog를 날짜 필터 없이 전체 이력을
    # 한 번에 로드했다 — 실측 확인 결과 그 가정은 틀렸다: dart_contracts는
    # 2021-05-31~2026-09-22까지 846개 종목이 분산돼 있고(as_of=2021-06-30이면 실제로는
    # 0개만 공시됐어야 하는데 846개 전부를 "촉매"로 인식), order_backlog의 collected_at은
    # 2026-06~09처럼 최근 3개월뿐이라 그보다 이전 어떤 백테스트 날짜에도 사실상 전부
    # "미래" 데이터였다. `catalyst_codes`가 buy_universe의 하드 AND 조건이라 이건
    # golden_cross(진입 우선순위 보너스 하나)보다 영향 범위가 크다 — 매수 유니버스
    # 자체가 미래정보로 결정되고 있었다.
    #
    # dart_contracts는 실제 공시일(disclosed_at, 'YYYYMMDD')이 있어 그대로 필터링.
    # order_backlog는 별도 공시일 컬럼이 없어(collected_at은 우리 수집시각일 뿐 실제
    # 공시일이 아님) 다른 전략들과 동일한 법정기한 근사(분기+45일/연간 익년 3월31일,
    # quarter=4는 사업보고서=연간으로 취급)로 avail_date를 계산.
    _contract_rows = conn.execute(
        "SELECT stock_code, disclosed_at, rcept_no, id, contract_ratio_pct, signal_strength, updated_at"
        " FROM dart_contracts WHERE signal_strength >= 2"
    ).fetchall()
    _backlog_rows = conn.execute(
        "SELECT stock_code, year, quarter, id, rcept_no, backlog_amount, collected_at FROM order_backlog"
        " WHERE COALESCE(backlog_amount,0)>0"
    ).fetchall()

    def _backlog_avail_date(year: int, quarter: int) -> str:
        if quarter == 4:
            return f"{year + 1}-03-31"
        return {1: f"{year}-05-15", 2: f"{year}-08-15", 3: f"{year}-11-15"}.get(
            quarter, f"{year + 1}-03-31"
        )

    _contract_events = [
        (r[0], f"{str(r[1])[:4]}-{str(r[1])[4:6]}-{str(r[1])[6:8]}")
        for r in _contract_rows if r[1]
    ]
    _backlog_events = [
        (r[0], _backlog_avail_date(int(r[1]), int(r[2])))
        for r in _backlog_rows if r[1] and r[2]
    ]
    # 증거 원장용 원본 행(종목별). order_backlog는 공시일 컬럼이 없어 법정기한 근사 →
    # availability_basis='statutory_estimate'로 남아 PIT는 approx로만 인정된다.
    _catalyst_rows_by_code: dict = {}
    for r in _contract_rows:
        if r[1]:
            _catalyst_rows_by_code.setdefault(r[0], []).append({
                "row_id": f"dart_contracts:{r[3]}",
                "available_at": f"{str(r[1])[:4]}-{str(r[1])[4:6]}-{str(r[1])[6:8]}",
                "value": {"rcept_no": r[2], "contract_ratio_pct": r[4], "signal_strength": r[5],
                          "updated_at": str(r[6]) if r[6] else None, "basis": "actual_disclosure"}})
    for r in _backlog_rows:
        if r[1] and r[2]:
            _catalyst_rows_by_code.setdefault(r[0], []).append({
                "row_id": f"order_backlog:{r[3]}",
                "available_at": _backlog_avail_date(int(r[1]), int(r[2])),
                "value": {"rcept_no": r[4], "year": r[1], "quarter": r[2], "backlog_amount": r[5],
                          "collected_at": str(r[6]) if r[6] else None, "basis": "statutory_estimate"}})
    evidence = SignalEvidenceLedger("high_profit_compound", {
        "insider_days": insider_days, "insider_refresh_every_sim_days": 7,
        "catalyst": "dart_contracts(signal_strength>=2) + order_backlog(backlog_amount>0)",
        "sectors": list(sectors), "min_turnover_m": min_turnover_m,
    })
    _cache_as_of = {"insider": None, "catalyst": None}

    def _note_evidence(code: str, day: str) -> None:
        ins_day = _cache_as_of["insider"] or day
        cutoff = (datetime.strptime(ins_day, '%Y-%m-%d') - timedelta(days=insider_days)).strftime('%Y-%m-%d')
        ins_rows = [
            {"row_id": f"dart_insider_holdings:{r[0]}", "available_at": str(r[2])[:10],
             "value": {"rcept_no": r[1], "change_amount": r[3], "sp_stock_lmp_irds_cnt": r[4]}}
            for r in conn.execute(
                """SELECT id, rcept_no, rcept_dt, change_amount, sp_stock_lmp_irds_cnt
                   FROM dart_insider_holdings
                   WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?
                     AND COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) > 0""",
                (code, cutoff, ins_day)).fetchall()
        ]
        cat_day = _cache_as_of["catalyst"] or day
        cat_rows = [r for r in _catalyst_rows_by_code.get(code, ()) if r["available_at"] <= cat_day]
        cat_item = evidence_aggregate("catalyst_disclosure", cat_rows,
                                      source_key=f"catalyst_as_of:{cat_day}")
        if cat_rows:
            cat_item["availability_basis"] = (
                "actual_disclosure" if any(r["value"]["basis"] == "actual_disclosure" for r in cat_rows)
                else "statutory_estimate")
        evidence.note(code, day, [
            evidence_aggregate("dart_insider_holdings", ins_rows,
                               source_key=f"window:{cutoff}~{ins_day}"),
            cat_item,
        ])
    _catalyst_events = _contract_events + _backlog_events

    def _catalyst_codes_as_of(as_of: str) -> set:
        return {code for code, avail in _catalyst_events if avail <= as_of}

    # 섹터 필터
    sector_codes = {r[0] for r in conn.execute(
        f"SELECT stock_code FROM stock_universe WHERE sector_large IN ({sector_ph})"
        " AND market IN ('KOSPI','KOSDAQ','유가증권','코스닥')"
        " AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'",
        sectors
    ).fetchall()}

    # 포지션 관리
    holdings: dict = {}   # code → {entry, peak, entry_date}
    trades: list = []
    capital = per_stock * max_positions
    cash = capital

    def _close_as_of(code: str, date: str) -> float:
        r = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, date)
        ).fetchone()
        return float(r[0]) if r else 0.0

    def _ma_as_of(code: str, date: str, n: int) -> float:
        rows = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT ?",
            (code, date, n)
        ).fetchall()
        if len(rows) < n:
            return 0.0
        return sum(r[0] for r in rows) / n

    _insider_cache: dict = {}
    _catalyst_cache: set = set()

    def _next_trade_date(idx: int) -> Optional[str]:
        return sim_dates[idx + 1] if idx + 1 < len(sim_dates) else None

    def _open_on(code: str, date: str) -> float:
        r = conn.execute(
            "SELECT open FROM price_history WHERE stock_code=? AND date=? AND open>0",
            (code, date),
        ).fetchone()
        return float(r[0]) if r else 0.0

    for date_idx, date in enumerate(sim_dates):
        # 임원매수 코드 (7일마다 갱신)
        if date_idx % 7 == 0 or not _insider_cache:
            _insider_cache.clear()
            _insider_cache.update({c: True for c in _insider_buy_codes(date)})
            _cache_as_of["insider"] = date
        if date_idx % 7 == 0 or not _catalyst_cache:
            _catalyst_cache = _catalyst_codes_as_of(date)
            _cache_as_of["catalyst"] = date

        # ── 매도 체크 ──
        for code in list(holdings.keys()):
            curr = _close_as_of(code, date)
            if curr <= 0:
                continue
            h = holdings[code]
            pnl = (curr - h["entry"]) / h["entry"]
            h["peak"] = max(h["peak"], curr)
            peak_pnl = (h["peak"] - h["entry"]) / h["entry"]
            # 데이터 기반: 이익100%+ 달성 후 Trail-40%로 확장 (대박 홀드)
            trail_pct = trail_big if peak_pnl >= 1.00 else trail
            from_peak = (curr - h["peak"]) / h["peak"]
            hold_days = (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(h["entry_date"], "%Y-%m-%d")).days
            reason = None
            if pnl <= stop:
                reason = "stop"
            # Trail은 이익 trail_activate_pct+ 달성 후에만 발동 (데이터: 10%)
            elif from_peak <= trail_pct and peak_pnl >= trail_activate_pct:
                reason = "trail"
            elif pnl >= tp:
                reason = "tp"
            elif hold_days >= max_hold:
                reason = "end"
            if reason:
                sell_date = _next_trade_date(date_idx)
                if not sell_date:
                    continue
                fill = _open_on(code, sell_date)
                if fill <= 0:
                    continue
                realized_pnl = (fill - h["entry"]) / h["entry"]
                pnl_abs = round(per_stock * realized_pnl)
                trades.append({"code": code, "buy_date": h["entry_date"], "sell_date": sell_date,
                                "signal_date": date, "entry": h["entry"], "exit": fill,
                                "pnl_pct": round(realized_pnl * 100, 2),
                                "reason": reason, "pnl": pnl_abs})
                cash += per_stock + pnl_abs
                del holdings[code]

        # ── 매수 후보 탐색 ──
        if len(holdings) >= max_positions:
            continue

        # 임원매수 × 섹터 × 촉매 교집합
        buy_universe = (set(_insider_cache.keys()) & sector_codes & _catalyst_cache) - set(holdings.keys())
        if not buy_universe:
            continue

        # 가격 조건 평가
        ph = ",".join("?" * len(buy_universe))
        # 데이터 기반 진입 조건:
        # - MA20 위(반등 확인): 3배 달성 종목 avg vs_ma20=102.7% vs 손실 avg=98.4%
        # - 52주 저점 대비 30%+ 반등: 저점에서 어느정도 올라온 종목(너무 초기 X)
        # - 거래대금 20억+: 유동성 확보
        # - 52주 고점 80% 조건 제거: 데이터에서 고점근처 vs 낙폭과대 3배달성률 차이없음
        cands = conn.execute(
            f"""SELECT p.stock_code,
                       MAX(CASE WHEN rn=1 THEN p.close END) AS close,
                       AVG(CASE WHEN rn<=20 THEN p.close END) AS ma20,
                       MIN(CASE WHEN rn<=252 THEN p.close END) AS low52,
                       AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END) AS avg_ta20
                FROM (
                    SELECT stock_code, close, volume, trade_amount,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE stock_code IN ({ph}) AND date<=? AND close>0
                ) p
                WHERE rn<=252
                GROUP BY p.stock_code
                HAVING COUNT(*)>=60
                   AND MAX(CASE WHEN rn=1 THEN p.close END) >= AVG(CASE WHEN rn<=20 THEN p.close END)
                   AND MAX(CASE WHEN rn=1 THEN p.close END) >= MIN(CASE WHEN rn<=252 THEN p.close END) * 1.20
                   AND MAX(CASE WHEN rn=1 THEN p.volume END) >= AVG(CASE WHEN rn<=20 THEN p.volume END) * 1.3
                   AND AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END)
                       >= ?
                ORDER BY AVG(CASE WHEN rn<=20 THEN COALESCE(NULLIF(p.trade_amount,0), p.close*p.volume) END) DESC""",
            list(buy_universe) + [date, min_turnover_m * 1e8]
        ).fetchall()

        # KOSPI 필터: MA60 미만이면 신규진입 금지 (지속 약세장 차단)
        # 실험결과: MA60 avg6=17.3% >> MA120×0.82 baseline avg6=10.2%
        #           MA120×0.90=5.1%, MA20>=MA60(종목)=-69.5%하락 → MA60 KOSPI가 최적
        kospi_k = _k_price_v13(date)
        ki_v13 = _k_idx_v13.get(date)
        if ki_v13 is None:
            for d in reversed(_k_dates_v13):
                if d <= date: ki_v13 = _k_idx_v13[d]; break
        kospi_ma60_v13 = (sum(_k_prices_v13[max(0, ki_v13-59):ki_v13+1]) / 60
                          if ki_v13 is not None and ki_v13 >= 60 else None)
        if kospi_ma60_v13 is not None and kospi_k < kospi_ma60_v13:
            continue  # KOSPI < MA60: 약세장 신규진입 금지

        slots = max_positions - len(holdings)
        entry_date = _next_trade_date(date_idx)
        if not entry_date:
            continue
        for row in cands[:slots]:
            code = row[0]
            entry_price = _open_on(code, entry_date)
            if entry_price <= 0:
                continue
            if cash < per_stock * 0.95:
                break
            holdings[code] = {"entry": entry_price, "peak": entry_price, "entry_date": entry_date,
                              "signal_date": date}
            _note_evidence(code, date)
            cash -= per_stock

    # 기간 종료 처리
    # 2026-09-23 수정: 이전엔 _close_as_of(date<=end_date로 가장 최근 종가를 그냥
    # 끌어옴)를 그대로 써서, 상장폐지된 종목도 몇 달 전 마지막 종가가 "정상 청산가"인
    # 것처럼 조용히 재사용됐다(다른 26개 전략이 전부 공유하는 _final_liquidation_
    # quote_for_code()로 교체될 때 이 파일만 자체 헬퍼를 써서 누락됨). 게다가 그
    # 가짜값마저 없으면(curr<=0) 포지션 자체가 trades에서 통째로 빠져 손실 계상도
    # 안 됐다. 종목별 실제 시세이력으로 idx_map을 만들어 같은 안전헬퍼를 쓰도록 정정
    # — 실제 최종거래일 종가, 확인된 합병/교환가치(delisting_outcomes), 둘 다 없으면
    # 명시적 전액손실 순으로 정확히 처리되고 포지션이 조용히 사라지지 않는다.
    #
    # 첫 시도 버그(즉시 발견·수정): last_day로 sim_dates[-1](실제 마지막 거래일)이
    # 아니라 end_date(달력상 종료일, 예 2025-05-31 토요일)를 그대로 넘겨서, 042660/
    # 329180/207940 같은 초대형 우량주까지 "시세부재 전액손실"로 오판했다(정상 시세는
    # 그 직전 거래일 2025-05-30까지 있었음). golden_cross/v8 등 이미 검증된 전략들과
    # 동일하게 sim_dates[-1]을 실제 청산 기준일로 사용하도록 정정.
    _last_trading_day = sim_dates[-1] if sim_dates else end_date
    for code, h in holdings.items():
        _rows = conn.execute(
            "SELECT date, close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date",
            (code, _last_trading_day),
        ).fetchall()
        _dates = [r[0] for r in _rows]
        _prices = [float(r[1]) for r in _rows]
        _idx_map = {d: i for i, d in enumerate(_dates)}
        curr, _reason = _final_liquidation_quote_for_code(conn, code, _last_trading_day, _idx_map, _prices)
        pnl = (curr - h["entry"]) / h["entry"]
        pnl_abs = round(per_stock * pnl)
        trades.append({"code": code, "buy_date": h["entry_date"], "sell_date": end_date,
                       "entry": h["entry"], "exit": curr, "pnl_pct": round(pnl * 100, 2),
                       "reason": "end" if curr > 0 else _reason, "pnl": pnl_abs})

    # 수익률 계산
    total_pnl = sum(t["pnl"] for t in trades)
    total_ret = round(total_pnl / capital * 100, 2) if capital > 0 else 0.0
    profit_cnt = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = round(profit_cnt / len(trades) * 100, 1) if trades else 0.0
    avg_ret = round(sum(t["pnl_pct"] for t in trades) / len(trades), 2) if trades else 0.0

    summary = (f"[V13고수익집중] {start_date[:7]}~{end_date[:7]} "
               f"수익률={total_ret:+.1f}% 승률={win_rate:.0f}% 거래={len(trades)}건 avg={avg_ret:+.1f}%")
    logging.info(summary)

    conn.execute(
        """UPDATE backtest_runs
           SET status='done', name=?, start_date=?, end_date=?, total_return_pct=?,
               profit_trades=?, total_trades=?, trades_json=?, summary_text=?
           WHERE run_id=?""",
        (run_name, start_date, end_date, total_ret, profit_cnt, len(trades),
         json.dumps(trades, ensure_ascii=False), summary, rid)
    )
    conn.commit()
    # 2026-09-23 신규: execution_contract/cash_reconciliation 아티팩트가 이 전략만
    # 한 번도 등록된 적이 없어 derive_status()가 영원히 legacy로 고정돼 있었다
    # (golden_cross 등 6개 엔진에서 2026-07-14에 이미 고친 것과 동일 부류의 누락
    # — 그때 이 파일은 이관 대상에서 빠졌다). final_cash는 `cash`가 기간종료
    # 강제청산분을 반영 안 하므로 capital+total_pnl(모든 trades의 실현손익 합)로
    # 직접 계산 — 다른 26개 전략과 동일하게 "최종현금=초기자본+총손익" 원칙.
    _register_execution_artifacts(rid, capital, capital + total_pnl)
    evidence.persist(rid, trades)
    conn.close()
    return rid


# ─── 섹터 연동 유틸: 특정 날짜/종목의 섹터 BUY 여부 ───────────────────────


