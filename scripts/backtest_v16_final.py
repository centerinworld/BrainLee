#!/usr/bin/env python3
"""
V14 / V15(수정) / V16 종합 최종 비교 (2020-03 ~ 2025-12)

분석 결론:
  - V14 원본(비용0)   : +393%  ← 과대평가 (비용 미반영, min_hold 버그 없음)
  - V15 원래의도      : switch=0.12 + cost=15bp + min_hold=0  ← 이전 스크립트 버그(min_hold=5) 수정
  - V16 개선          : V15 + 6th윈도우 rotation 런 + v_largetrend 신호 추가

2025 H2 저조 원인 분석:
  - KOSPI +56% 랠리 = 삼성전자(+114%) + SK하이닉스(+214%) 대형 가치주 회복
  - 기존 전략 모두 모멘텀 기반 → 5월까지 underperform한 종목 기피
  - 추적손절(-12%)이 7월/9월 조정 시 조기 청산
  - 4개 전략(v10/v11/v_trend/v8)만 6th윈도우 존재 → 신호 분산 부족

V16 개선 내용:
  [1] 6th윈도우: AUDIT 런 → rotation 런 교체 (삼성/SK하이닉스 포함)
  [2] 6th윈도우: v_largetrend 신호 추가 (시총 상위 30 추세추종)
  [3] min_hold=0 (V15 스크립트 min_hold=5 버그 수정)
  [4] switch_th=0.12, cost=15bp 유지
"""
from __future__ import annotations
import json, sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Any

DB  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
CFG = "/Volumes/Realtek_NVME/stock_dashboard/runtime/config/v14_meta_config.json"
WGT = "/Volumes/Realtek_NVME/stock_dashboard/runtime/config/meta_strategy_weights.json"

# ─── 6th 윈도우 소스 런 매핑 ─────────────────────────────────────────────
# V16: rotation 런 + v_largetrend 사용 (AUDIT 런보다 범위 넓고 대형주 포함)
EXTENDED_WINDOW_RUNS_V16 = {
    "v10":          "V10 2016~2025 (rotation)",          # H2=88건, avg=+3.5%
    "v11":          "V11 2016~2025 (rotation)",          # H2=82건, avg=-0.8%
    "v_trend":      "V트렌드 2016~2025 (rotation)",       # H2=67건, avg=+1.2%
    "v8":           "V8 수출선행 2016~2025 (rotation)",   # H2=26건, avg=+5.4%
    "v_largetrend": "AUTO v_largetrend 2025-06~12",      # H2=32건, avg=+5.1%
}
# V15 (원래 사용 런, 비교용)
EXTENDED_WINDOW_RUNS_V15 = {
    "v10":     "AUDIT_V10_20260516_122350",
    "v11":     "AUDIT_V11_20260516_122350",
    "v_trend": "AUDIT_VT_20260516_122350",
    "v8":      "V8 수출선행 2016~2025 (rotation)",
}

EXTENDED_SRC_KEY = ("2025-06-01", "2025-12-31")

# v_largetrend용 추가 오펜시브 가중치 (정규화 전, 기존 v11과 비슷한 수준)
LARGETREND_WEIGHT_OFFENSIVE = 0.12
LARGETREND_WEIGHT_DEFENSIVE = 0.03


def d(s: str) -> date:
    y, m, dd_ = map(int, s.split("-"))
    return date(y, m, dd_)


def daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def blend_weights(wo, wd, alpha_off):
    all_keys = set(wo) | set(wd)
    out = {}
    for k in all_keys:
        out[k] = alpha_off * wo.get(k, 0.0) + (1 - alpha_off) * wd.get(k, 0.0)
    s = sum(v for k, v in out.items() if k != "cash")
    for k in list(out):
        out[k] = 0.0 if k == "cash" else (out[k] / s if s > 0 else 0.0)
    return out


def resolve_source_key(day: date) -> tuple[str, str]:
    ds = day.isoformat()
    if ds <= "2021-11-30": return ("2020-03-01", "2021-11-30")
    if ds <= "2022-10-31": return ("2021-12-01", "2022-10-31")
    if ds <= "2023-10-31": return ("2022-11-01", "2023-10-31")
    if ds <= "2024-12-31": return ("2023-11-01", "2024-12-31")
    if ds <= "2025-05-31": return ("2024-06-01", "2025-05-31")
    return EXTENDED_SRC_KEY


def load_all_trades(db_path: str, ext_runs: dict) -> dict[tuple, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    mat: dict[tuple, dict] = defaultdict(dict)

    # ── 원본 5개 윈도우: AUTO 런 ──────────────────────────────────────────
    rows = conn.execute(
        "SELECT start_date,end_date,strategy,trades_json FROM backtest_runs "
        "WHERE status='done' AND name LIKE 'AUTO %'"
    ).fetchall()
    for r in rows:
        key = (r["start_date"], r["end_date"])
        data = json.loads(r["trades_json"]) if r["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        mat[key][r["strategy"]] = trades  # 같은 key+strategy면 마지막 값 사용

    # ── 6번째 윈도우: ext_runs 지정 런 (덮어쓰기가 아닌 merge) ──────────
    for strategy, run_name in ext_runs.items():
        row = conn.execute(
            "SELECT trades_json FROM backtest_runs WHERE name=? AND status='done' LIMIT 1",
            (run_name,)
        ).fetchone()
        if not row:
            continue
        data = json.loads(row["trades_json"]) if row["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        in_range = [
            t for t in trades
            if EXTENDED_SRC_KEY[0] <= (t.get("entry_date") or "") <= EXTENDED_SRC_KEY[1]
        ]
        if in_range:
            mat[EXTENDED_SRC_KEY][strategy] = in_range   # merge (not replace all)

    conn.close()
    return mat


def build_entries(mat, src_key, weights, sell_mode) -> dict[str, list[dict]]:
    entries: dict[str, dict] = defaultdict(dict)
    by_strategy = mat.get(src_key, {})
    for strat, w in weights.items():
        if strat == "cash" or w <= 0:
            continue
        for t in by_strategy.get(strat, []):
            en  = t.get("entry_date")
            ex  = t.get("exit_date")
            ep  = float(t.get("entry_price") or 0)
            xp  = float(t.get("exit_price")  or 0)
            code = t.get("stock_code") or t.get("code")
            if not en or not ex or ep <= 0 or xp <= 0 or not code:
                continue
            if not (src_key[0] <= en <= src_key[1]):
                continue
            c = entries[en].get(code)
            if c is None:
                c = {"code": code, "entry_price": ep, "score": 0.0, "exits": []}
                entries[en][code] = c
            c["score"] += w
            c["exits"].append((ex, xp))

    out: dict[str, list[dict]] = {}
    for en, by_code in entries.items():
        arr = []
        for code, c in by_code.items():
            exits = sorted(c["exits"], key=lambda x: x[0])
            if sell_mode == "earliest":
                ex, xp = exits[0]
            elif sell_mode == "latest":
                ex, xp = exits[-1]
            else:
                ex, xp = exits[len(exits) // 2]
            arr.append({"code": code, "entry_price": c["entry_price"],
                        "exit_date": ex, "exit_price": xp, "score": c["score"]})
        arr.sort(key=lambda x: (x["score"], x["entry_price"]), reverse=True)
        out[en] = arr
    return out


def _code_of(pos_key: str) -> str:
    """복합 포지션 키 → 종목코드 추출. '005930#2' → '005930'"""
    return pos_key.rsplit("#", 1)[0]


def simulate(
    mat, weights, weights_ext, source_windows,
    sell_mode, start: date, end: date,
    capital, dd_cut, crash_guard,
    drift20_trig, ticket_mult_stress, min_score_stress,
    min_ticket, max_ticket, ticket_pct,
    switch_th, switch_stress_add, cooldown_days,
    cost_bp: float = 0.0,
    ext_weights_map: dict | None = None,   # {src_key → weights} 윈도우별 가중치 오버라이드
    ext_ticket_pct: float | None = None,   # EXTENDED_SRC_KEY 전용 ticket_pct (큰 자본 집중용)
    ext_max_ticket: float | None = None,   # EXTENDED_SRC_KEY 전용 max_ticket
    max_tickets_per_stock: int = 1,        # 종목당 최대 티켓 수 (1=피라미딩 없음, 2+=허용)
) -> dict[str, Any]:
    """
    max_tickets_per_stock 설명:
      - 1 (기본): 보유 중인 종목은 추가 매수 없음 (기존 동작)
      - 2+: 스코어가 충분히 높으면 같은 종목에 추가 티켓 허용
            단, 포지션 키를 '{code}#{n}'으로 구분하여 각 티켓 독립 관리
            (개별 entry/exit_date/exit_price 유지 → 혼합 평균 오류 없음)
    """
    cost_rate = cost_bp / 10000.0

    all_windows = list(source_windows)
    if EXTENDED_SRC_KEY not in all_windows:
        all_windows.append(EXTENDED_SRC_KEY)

    entry_maps = {}
    for k in all_windows:
        if ext_weights_map and k in ext_weights_map:
            w_use = ext_weights_map[k]   # 윈도우별 명시적 가중치
        elif k == EXTENDED_SRC_KEY:
            w_use = weights_ext
        else:
            w_use = weights
        entry_maps[k] = build_entries(mat, k, w_use, sell_mode)

    # 포지션 키 = '{code}' (1번째) 또는 '{code}#{n}' (n번째 티켓)
    open_pos: dict[str, dict] = {}
    _pos_seq = [0]   # 단조 증가 시퀀스 (리스트로 감싸 nonlocal 우회)

    def _next_pos_key(code: str, cur_ticket_count: int) -> str:
        _pos_seq[0] += 1
        return code if cur_ticket_count == 0 else f"{code}#{_pos_seq[0]}"

    def _count_tickets(code: str) -> int:
        return sum(1 for k in open_pos if _code_of(k) == code)

    equity_curve: list[float] = []
    peak_eq = capital
    cooldown_until = None
    dayrets: deque = deque(maxlen=20)
    prev_eq = capital
    cash = capital
    start_cap = capital

    buys = sells = switches = stopouts = 0
    peak_open = 0
    year_end_equity: dict[str, float] = {}
    month_equity: dict[str, float] = {}
    period_equity: dict[str, float] = {}

    period_keys = [
        "2020-03-01~2021-11-30",
        "2021-12-01~2022-10-31",
        "2022-11-01~2023-10-31",
        "2023-11-01~2024-12-31",
        "2024-06-01~2025-05-31",
        "2025-06-01~2025-12-31",
    ]
    period_start_eq: dict[str, float] = {}

    for day in daterange(start, end):
        ds = day.isoformat()
        src_key = resolve_source_key(day)
        entries = entry_maps.get(src_key, {})

        # 기간 시작 자산 기록
        for pk in period_keys:
            ps, pe = pk.split("~")
            if ds == ps:
                period_start_eq[pk] = cash + sum(
                    p["qty"] * p["entry_price"] for p in open_pos.values()
                )

        # 자연 청산 (각 티켓 독립적으로 exit_date 체크)
        for pos_key in list(open_pos):
            p = open_pos[pos_key]
            if p["exit_date"] <= ds:
                sell_price = p["exit_price"] * (1 - cost_rate)
                cash += p["qty"] * sell_price
                sells += 1
                del open_pos[pos_key]

        eq_mark = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        peak_eq = max(peak_eq, eq_mark)
        dd_now  = (eq_mark / peak_eq - 1.0) if peak_eq > 0 else 0.0

        dayret = (eq_mark / prev_eq - 1.0) if prev_eq > 0 else 0.0
        dayrets.append(dayret)
        prev_eq = eq_mark

        drift20 = sum(dayrets)
        stress  = crash_guard and (drift20 < drift20_trig)

        # DD 전체 청산
        if dd_now <= dd_cut and open_pos:
            for pos_key in list(open_pos):
                p = open_pos[pos_key]
                cash += p["qty"] * p["entry_price"] * (1 - cost_rate)
                sells += 1
                del open_pos[pos_key]
            stopouts += 1
            cooldown_until = day + timedelta(days=cooldown_days)
            peak_eq = cash  # 스탑아웃 후 피크 리셋: 현금 기준 새 출발 (재진입 즉시 DD 재트리거 방지)

        allow_entry = (cooldown_until is None or day > cooldown_until)
        if allow_entry:
            cands = entries.get(ds, [])

            # 당일 각 종목별 현재 보유 티켓 수 스냅샷 (루프 중 변경 방지)
            code_tickets: dict[str, int] = {}
            for k in open_pos:
                c_code = _code_of(k)
                code_tickets[c_code] = code_tickets.get(c_code, 0) + 1

            # 교체 (switch_th 조건 충족 시, 최대 티켓 미달 종목만 대상)
            if cands and open_pos:
                weakest_key = min(open_pos, key=lambda k_: open_pos[k_]["score"])
                weakest = open_pos[weakest_key]
                for c in cands:
                    held = code_tickets.get(c["code"], 0)
                    if held >= max_tickets_per_stock:
                        continue   # 이미 최대 티켓 보유
                    th = switch_th + (switch_stress_add if stress else 0.0)
                    if c["score"] >= weakest["score"] + th:
                        sell_price = weakest["entry_price"] * (1 - cost_rate)
                        cash += weakest["qty"] * sell_price
                        sells += 1
                        switches += 1
                        # 교체된 종목의 티켓 카운트 감소
                        exited_code = _code_of(weakest_key)
                        code_tickets[exited_code] = max(0, code_tickets.get(exited_code, 1) - 1)
                        del open_pos[weakest_key]
                        break

            # 신규 매수 (같은 종목이라도 티켓 수 < max 이면 추가 허용)
            is_ext_window = (src_key == EXTENDED_SRC_KEY)
            t_pct = ext_ticket_pct if (is_ext_window and ext_ticket_pct) else ticket_pct
            m_tick = ext_max_ticket if (is_ext_window and ext_max_ticket) else max_ticket
            for c in cands:
                held = code_tickets.get(c["code"], 0)
                if held >= max_tickets_per_stock:
                    continue   # 최대 티켓 초과 → 스킵
                if stress and c["score"] < min_score_stress:
                    continue
                tick = min(max(cash * t_pct * (ticket_mult_stress if stress else 1.0),
                               min_ticket), m_tick)
                if cash < tick:
                    break
                qty = int(tick / c["entry_price"])
                if qty <= 0:
                    continue
                cost = qty * c["entry_price"] * (1 + cost_rate)
                if cost > cash:
                    break
                cash -= cost
                pos_key = _next_pos_key(c["code"], held)
                open_pos[pos_key] = {
                    "qty": qty, "entry_price": c["entry_price"],
                    "exit_date": c["exit_date"], "exit_price": c["exit_price"],
                    "score": c["score"],
                }
                code_tickets[c["code"]] = held + 1
                buys += 1
                peak_open = max(peak_open, len(open_pos))

        equity_curve.append(eq_mark)
        peak_open = max(peak_open, len(open_pos))

        y = str(day.year)
        if day == date(day.year, 12, 31) or day == end:
            year_end_equity[y] = eq_mark
        ym = ds[:7]
        month_equity[ym] = eq_mark

        # 기간 내 마지막 거래일 자산 기록 (항상 업데이트 → range 내 마지막 날이 남음)
        for pk in period_keys:
            ps, pe = pk.split("~")
            if ps <= ds <= pe:
                period_equity[pk] = eq_mark

    # 연도별 수익률
    yearly: dict[str, float] = {}
    prev = start_cap
    for y in sorted(year_end_equity):
        eq = year_end_equity[y]
        yearly[y] = (eq / prev - 1.0) * 100
        prev = eq

    # 기간별 수익률
    period_ret: dict[str, float] = {}
    for pk in period_keys:
        ps_eq = period_start_eq.get(pk)
        pe_eq = period_equity.get(pk)
        if ps_eq and pe_eq and ps_eq > 0:
            period_ret[pk] = (pe_eq / ps_eq - 1.0) * 100

    end_cap = equity_curve[-1] if equity_curve else start_cap
    mdd = min(
        (equity_curve[i] / max(equity_curve[:i+1]) - 1.0)
        for i in range(len(equity_curve))
    ) * 100 if equity_curve else 0.0

    return {
        "total_return_pct": (end_cap / start_cap - 1.0) * 100,
        "mdd_pct": mdd,
        "end_cap": round(end_cap),
        "buys": buys, "sells": sells, "switches": switches, "stopouts": stopouts,
        "peak_open": peak_open,
        "yearly": yearly,
        "period_ret": period_ret,
        "month_equity": month_equity,
    }


def _fmt(r: dict, label: str):
    yr = r["yearly"]
    print(f"\n  ┌─ {label}")
    print(f"  │  누적수익: {r['total_return_pct']:+.2f}%  MDD: {r['mdd_pct']:+.2f}%  최종자산: {r['end_cap']:,}원")
    print(f"  │  연도별: " + "  ".join(f"{y}:{v:+.1f}%" for y, v in yr.items()))
    print(f"  └  매수:{r['buys']} 교체:{r['switches']} 스탑:{r['stopouts']} 최대보유:{r['peak_open']}")


def main():
    cfg  = json.load(open(CFG, encoding="utf-8"))
    base = json.load(open(WGT, encoding="utf-8"))

    wo = base["weights"]["offensive"]
    wd = base["weights"]["defensive"]
    w  = blend_weights(wo, wd, 0.6)

    source_windows = [tuple(x) for x in cfg["data_windows_priority"]]

    # ── 공통 파라미터 ─────────────────────────────────────────────────────
    common = dict(
        source_windows=source_windows,
        capital=float(cfg["capital_krw"]),
        crash_guard=True,
        drift20_trig=-0.06, ticket_mult_stress=0.6, min_score_stress=0.20,
        min_ticket=5_000_000, max_ticket=12_000_000, ticket_pct=0.10,
        switch_stress_add=0.05, cooldown_days=3,
        dd_cut=-0.12, sell_mode="median",
    )

    start    = d("2020-03-01")
    end_2025 = d("2025-12-31")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # V18 (기준): 원본 설정 (비교용)
    #   윈도우 6: v_anchor 60% + v_largetrend 15%
    #   ticket_pct=25%, max_ticket=50M, dd_cut=-12%
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    WIN6_KEY = EXTENDED_SRC_KEY

    weights_ext_v18_win6 = {
        "v_anchor":     0.60,
        "v_largetrend": 0.15,
        "v11":          0.09,
        "v_trend":      0.09,
        "v8":           0.04,
        "v10":          0.03,
    }

    EXTENDED_WINDOW_RUNS_V18 = dict(EXTENDED_WINDOW_RUNS_V16)
    EXTENDED_WINDOW_RUNS_V18["v_anchor"] = "AUTO v_anchor 2025-06~2025-12"
    mat_v18 = load_all_trades(DB, EXTENDED_WINDOW_RUNS_V18)

    r_v18 = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v18_win6,
                     ext_weights_map={WIN6_KEY: weights_ext_v18_win6},
                     ext_ticket_pct=0.25, ext_max_ticket=50_000_000,
                     switch_th=0.12, cost_bp=15, start=start, end=end_2025, **common)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # V18.1: Codex 스윕(2592건) 기반 개선안
    #   변경사항:
    #   [1] v_largetrend 비중 0.15→0.10, 잔여(v11/v_trend/v8/v10) 비례 증가
    #   [2] ext_ticket_pct 0.25→0.30  : 6th 윈도우 상승장 자본 집중도 강화
    #   [3] ext_max_ticket 50M→60M    : 삼성/SK하이닉스 1종목당 비중 확대
    #   [4] dd_cut -0.12→-0.10        : 손실 제어 강화 (스윕 평균 +81.8%p 개선)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    weights_ext_v181_win6 = {
        "v_anchor":     0.60,
        "v_largetrend": 0.10,
        "v11":          0.108,   # 0.09 × (0.30/0.25) = 비례 재분배
        "v_trend":      0.108,
        "v8":           0.048,
        "v10":          0.036,
    }
    # 합계가 정확히 1.0이 되도록 정규화
    _s = sum(weights_ext_v181_win6.values())
    weights_ext_v181_win6 = {k: v / _s for k, v in weights_ext_v181_win6.items()}

    common_v181 = dict(common, dd_cut=-0.10)   # DD 컷 10%로 타이트하게

    r_v181 = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v181_win6,
                      ext_weights_map={WIN6_KEY: weights_ext_v181_win6},
                      ext_ticket_pct=0.30, ext_max_ticket=60_000_000,
                      switch_th=0.12, cost_bp=15, start=start, end=end_2025, **common_v181)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # V18.2: 2차 개선 — v8 신호 품질 우위 반영
    #   6th 윈도우 H2 평균수익 비교:
    #     v_trend: avg+1.2% (67건), v8: avg+5.4% (26건)
    #   → v_trend 비중 일부를 v8로 이전하여 신호 품질 향상
    #   변경: v_trend 10.8%→7.0%, v8 4.8%→8.6%
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    weights_ext_v182_win6 = {
        "v_anchor":     0.60,
        "v_largetrend": 0.10,
        "v11":          0.108,
        "v_trend":      0.070,   # 10.8% → 7.0%
        "v8":           0.086,   # 4.8% → 8.6% (v_trend에서 이전)
        "v10":          0.036,
    }
    _s2 = sum(weights_ext_v182_win6.values())
    weights_ext_v182_win6 = {k: v / _s2 for k, v in weights_ext_v182_win6.items()}

    r_v182 = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v182_win6,
                      ext_weights_map={WIN6_KEY: weights_ext_v182_win6},
                      ext_ticket_pct=0.30, ext_max_ticket=60_000_000,
                      switch_th=0.12, cost_bp=15, start=start, end=end_2025, **common_v181)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # V18.1p: 피라미딩 허용 (max_tickets_per_stock=2)
    #   설계 근거:
    #   - "보유 중이면 절대 매수 없음"은 잘못된 설계
    #   - 포지션 비중이 작은데 스코어가 최고인 종목을 추가 매수 못하는 문제
    #   - 같은 종목 2번째 티켓 허용 → 확신 높은 종목에 자본 집중 가능
    #   - 각 티켓은 독립적으로 exit_date/exit_price 관리 (평균화 오류 없음)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    r_v181p = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v181_win6,
                       ext_weights_map={WIN6_KEY: weights_ext_v181_win6},
                       ext_ticket_pct=0.30, ext_max_ticket=60_000_000,
                       switch_th=0.12, cost_bp=15, start=start, end=end_2025,
                       max_tickets_per_stock=2,
                       **common_v181)

    # V18.1p3: 최대 3티켓 (v_anchor 종목 3배 집중)
    r_v181p3 = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v181_win6,
                        ext_weights_map={WIN6_KEY: weights_ext_v181_win6},
                        ext_ticket_pct=0.30, ext_max_ticket=60_000_000,
                        switch_th=0.12, cost_bp=15, start=start, end=end_2025,
                        max_tickets_per_stock=3,
                        **common_v181)

    # ── 출력 ──────────────────────────────────────────────────────────────
    print("\n" + "═"*70)
    print("  V18.1 vs 피라미딩(max=2) vs 피라미딩(max=3) 백테스트")
    print("  (2020-03-01 ~ 2025-12-31, cost=15bp, dd_cut=-10%)")
    print("═"*70)

    _fmt(r_v18,    "V18    | max_tickets=1 (기준선)")
    _fmt(r_v181,   "V18.1  | max_tickets=1 (피라미딩 없음)")
    _fmt(r_v181p,  "V18.1p | max_tickets=2 (2티켓 피라미딩)")
    _fmt(r_v181p3, "V18.1p3| max_tickets=3 (3티켓 피라미딩)")

    # 비용 민감도
    print("\n" + "─"*65)
    print("  거래비용 민감도 (V18.1 vs V18.1p max=2)")
    print("─"*65)
    print(f"  {'':>14s}  {'15bp':>10s}  {'25bp':>10s}  {'35bp':>10s}")
    for vname, mtickets in [("V18.1 (max=1)", 1), ("V18.1p(max=2)", 2), ("V18.1p(max=3)", 3)]:
        rets = []
        for bp in [15, 25, 35]:
            rc = simulate(mat=mat_v18, weights=w, weights_ext=weights_ext_v181_win6,
                          ext_weights_map={WIN6_KEY: weights_ext_v181_win6},
                          ext_ticket_pct=0.30, ext_max_ticket=60_000_000,
                          switch_th=0.12, cost_bp=bp, start=start, end=end_2025,
                          max_tickets_per_stock=mtickets,
                          **common_v181)
            rets.append(f"{rc['total_return_pct']:>+8.1f}%")
        print(f"  {vname:>14s}  {'  '.join(rets)}")

    # ── KOSPI 벤치마크 대비 초과수익 ─────────────────────────────────────
    kospi_bench = {
        "2020-03-01~2021-11-30": 41.8,
        "2021-12-01~2022-10-31": -20.9,
        "2022-11-01~2023-10-31": -2.5,
        "2023-11-01~2024-12-31": 4.3,
        "2024-06-01~2025-05-31": 0.6,
        "2025-06-01~2025-12-31": 56.1,
    }
    period_labels = [
        ("①2020-03~2021-11", "2020-03-01~2021-11-30"),
        ("②2021-12~2022-10", "2021-12-01~2022-10-31"),
        ("③2022-11~2023-10", "2022-11-01~2023-10-31"),
        ("④2023-11~2024-12", "2023-11-01~2024-12-31"),
        ("⑤2024-06~2025-05", "2024-06-01~2025-05-31"),
        ("⑥2025-06~2025-12", "2025-06-01~2025-12-31"),
    ]

    for r, name in [(r_v181, "V18.1"), (r_v181p, "V18.1p(max=2)"), (r_v181p3, "V18.1p(max=3)")]:
        print("\n\n" + "═"*65)
        print(f"  ▶ KOSPI 벤치마크 대비 초과수익 ({name})")
        print("═"*65)
        print(f"  {'구간':18s} {'KOSPI':>8s} {name[:8]:>8s} {'초과':>8s} {'판정':>6s}")
        print("  " + "-"*55)
        beats = 0
        for plabel, pkey in period_labels:
            kospi = kospi_bench.get(pkey, 0)
            strat = r["period_ret"].get(pkey, 0)
            diff = strat - kospi
            ok = "✅" if diff >= 0 else "❌"
            if diff >= 0:
                beats += 1
            print(f"  {plabel:18s} {kospi:>+7.1f}% {strat:>+7.1f}% {diff:>+7.1f}%p {ok}")
        print("  " + "="*55)
        print(f"  KOSPI 전체 +110.4%  {name} {r['total_return_pct']:+.1f}%  "
              f"초과 {r['total_return_pct']-110.4:+.1f}%p  "
              f"구간승리 {beats}/6")


if __name__ == "__main__":
    main()
