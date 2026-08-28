#!/usr/bin/env python3
"""
V14 vs V15 종합 비교 (2020-03 ~ 2026-03 확장)

V15 로직 변경사항:
  [변경-1] switch_th: 0.08 → 0.12  (교체 임계치 강화, 불필요 교체 방지)
  [변경-2] 거래비용 15bp 반영       (편도, 왕복 30bp = 실운용 현실화)
  [변경-3] 최소 보유기간 5영업일 필터 (초단기 소음 거래 제거, 비용 절감)
  [변경-4] 소스 윈도우 6번째 추가   (2025-06~12 AUDIT/rotation 데이터 활용)

실행:
  python3 scripts/backtest_v15_extended.py
"""
from __future__ import annotations
import json, sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Any

DB = "/Applications/stock_dashboard/stock.db"
CFG = "/Applications/stock_dashboard/config/v14_meta_config.json"
WGT = "/Applications/stock_dashboard/config/meta_strategy_weights.json"

# ─── 6번째 소스 윈도우용 AUDIT/rotation run 매핑 ──────────────────────────
# 2025-06-01~2025-12-31 구간에 대응하는 최신 run 이름 (전략별)
EXTENDED_WINDOW_RUNS = {
    "v10":     "AUDIT_V10_20260516_122350",   # 44건 (2025-06~12)
    "v11":     "AUDIT_V11_20260516_122350",   # 42건
    "v_trend": "AUDIT_VT_20260516_122350",    # 44건
    "v8":      "V8 수출선행 2016~2025 (rotation)",  # 26건
    # v5, v1_value, v2: 2025-06 이후 데이터 없음 → 이 윈도우에서 제외
}
EXTENDED_SRC_KEY = ("2025-06-01", "2025-12-31")


def d(s: str) -> date:
    y, m, dd_ = map(int, s.split("-"))
    return date(y, m, dd_)


def daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def blend_weights(wo, wd, alpha_off, keys_override=None):
    """offensive/defensive 가중치 블렌드. keys_override로 사용 전략 제한 가능."""
    all_keys = set(wo) | set(wd)
    if keys_override:
        all_keys = all_keys & (set(keys_override) | {"cash"})
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
    if ds <= "2025-12-31": return EXTENDED_SRC_KEY        # ← [변경-4] 6번째 윈도우
    return EXTENDED_SRC_KEY                                # 2026: 새 신호 없음


def load_all_trades(db_path: str) -> dict[tuple[str, str], dict[str, list[dict]]]:
    """AUTO runs (5개 원본 윈도우) + AUDIT/rotation (6번째 윈도우) 로드."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    mat: dict[tuple, dict] = defaultdict(dict)

    # ── 원본 5개 윈도우: AUTO runs ────────────────────────────────────────
    rows = conn.execute(
        "SELECT start_date,end_date,strategy,trades_json FROM backtest_runs "
        "WHERE status='done' AND name LIKE 'AUTO %'"
    ).fetchall()
    for r in rows:
        key = (r["start_date"], r["end_date"])
        data = json.loads(r["trades_json"]) if r["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        mat[key][r["strategy"]] = trades  # 같은 key+strategy면 마지막 것 사용

    # ── 6번째 윈도우: AUDIT/rotation runs ────────────────────────────────
    window_trades: dict[str, list[dict]] = {}
    for strategy, run_name in EXTENDED_WINDOW_RUNS.items():
        row = conn.execute(
            "SELECT trades_json FROM backtest_runs WHERE name=? AND status='done' LIMIT 1",
            (run_name,)
        ).fetchone()
        if not row:
            continue
        data = json.loads(row["trades_json"]) if row["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        # 실제 이 윈도우 날짜 범위 내 trade만 사용
        in_range = [
            t for t in trades
            if EXTENDED_SRC_KEY[0] <= (t.get("entry_date") or "") <= EXTENDED_SRC_KEY[1]
        ]
        if in_range:
            window_trades[strategy] = in_range

    mat[EXTENDED_SRC_KEY] = window_trades
    conn.close()
    return mat


def build_entries(
    mat, src_key, weights, sell_mode, min_hold_days: int = 0
) -> dict[str, list[dict]]:
    entries: dict[str, dict] = defaultdict(dict)
    by_strategy = mat.get(src_key, {})
    for strat, w in weights.items():
        if strat == "cash" or w <= 0:
            continue
        for t in by_strategy.get(strat, []):
            en = t.get("entry_date")
            ex = t.get("exit_date")
            ep = float(t.get("entry_price") or 0)
            xp = float(t.get("exit_price") or 0)
            code = t.get("stock_code") or t.get("code")
            if not en or not ex or ep <= 0 or xp <= 0 or not code:
                continue
            if not (src_key[0] <= en <= src_key[1]):
                continue
            # [변경-3] 최소 보유기간 필터
            if min_hold_days > 0:
                hold = (d(ex) - d(en)).days
                if hold < min_hold_days:
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


def simulate(
    mat, weights, source_windows,
    sell_mode, start: date, end: date,
    capital, dd_cut, crash_guard,
    drift20_trig, ticket_mult_stress, min_score_stress,
    min_ticket, max_ticket, ticket_pct,
    switch_th, switch_stress_add, cooldown_days,
    cost_bp: float = 0.0,
    min_hold_days: int = 0,
) -> dict[str, Any]:
    cost_rate = cost_bp / 10000.0

    # 6번째 윈도우 추가 (있으면)
    all_windows = list(source_windows)
    if EXTENDED_SRC_KEY not in all_windows:
        all_windows.append(EXTENDED_SRC_KEY)

    # 6번째 윈도우는 재가중치 (v5/v1_value/v2 없음)
    ext_keys = set(EXTENDED_WINDOW_RUNS.keys())
    weights_ext = blend_weights(
        {k: v for k, v in weights.items() if k in ext_keys or k == "cash"},
        {k: v for k, v in weights.items() if k in ext_keys or k == "cash"},
        0.5,  # 단순 평균 (offensive/defensive 원본 없음)
    )
    # 실제로는 원본 weights에서 해당 전략만 정규화
    sub = {k: v for k, v in weights.items() if k in ext_keys}
    s = sum(sub.values())
    weights_ext = {k: v / s for k, v in sub.items()} if s > 0 else {}

    entry_maps = {}
    for k in all_windows:
        w = weights_ext if k == EXTENDED_SRC_KEY else weights
        entry_maps[k] = build_entries(mat, k, w, sell_mode, min_hold_days)

    open_pos: dict[str, dict] = {}
    equity_curve: list[float] = []
    peak_eq = capital
    cooldown_until = None
    dayrets: deque = deque(maxlen=20)
    prev_eq = capital
    cash = capital
    start_cap = capital

    buys = sells = switches = skips_cash = stopouts = 0
    deployed = 0.0
    peak_open = 0
    year_end_equity: dict[int, float] = {}
    month_equity: dict[str, float] = {}  # YYYY-MM → equity

    for day in daterange(start, end):
        ds = day.isoformat()
        src_key = resolve_source_key(day)
        entries = entry_maps.get(src_key, {})

        # natural exits
        for code in list(open_pos):
            p = open_pos[code]
            if p["exit_date"] <= ds:
                sell_price = p["exit_price"] * (1 - cost_rate)
                cash += p["qty"] * sell_price
                sells += 1
                del open_pos[code]

        eq_mark = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        peak_eq = max(peak_eq, eq_mark)
        dd_now = (eq_mark / peak_eq - 1.0) if peak_eq > 0 else 0.0

        drift20 = sum(dayrets)
        stress = crash_guard and (drift20 < drift20_trig)

        # DD 컷 전체 청산
        if dd_now <= dd_cut and open_pos:
            for code in list(open_pos):
                p = open_pos[code]
                cash += p["qty"] * p["entry_price"] * (1 - cost_rate)  # 원본 방식 유지
                sells += 1
                del open_pos[code]
            stopouts += 1
            cooldown_until = day + timedelta(days=cooldown_days)

        allow_entry = (cooldown_until is None or day > cooldown_until)
        if allow_entry:
            cands = entries.get(ds, [])

            # [변경-1] switch_th 강화 — 항상 시도 (max_positions 제한 없음)
            if cands and open_pos:
                weakest = min(open_pos.values(), key=lambda p_: p_["score"])
                for c in cands:
                    if c["code"] in open_pos:
                        continue
                    th = switch_th + (switch_stress_add if stress else 0.0)
                    if c["score"] >= weakest["score"] + th:
                        sell_price = weakest["entry_price"] * (1 - cost_rate)
                        cash += weakest["qty"] * sell_price
                        sells += 1
                        switches += 1
                        del open_pos[weakest["code"]]
                        break

            eq_now = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
            cur_ticket_pct = ticket_pct * (ticket_mult_stress if stress else 1.0)
            ticket_cap = min(max_ticket, max(min_ticket, eq_now * cur_ticket_pct))

            for c in cands:
                if c["code"] in open_pos:
                    continue
                if stress and c["score"] < min_score_stress:
                    continue
                # [변경-2] 거래비용 반영한 매수가
                buy_price = c["entry_price"] * (1 + cost_rate)
                alloc = min(ticket_cap, cash)
                if alloc < min_ticket:
                    skips_cash += 1
                    continue
                qty = int(alloc // buy_price)
                cost_buy = qty * buy_price
                if qty <= 0 or cost_buy < min_ticket or cash < cost_buy:
                    skips_cash += 1
                    continue
                cash -= cost_buy
                deployed += qty * c["entry_price"]
                buys += 1
                open_pos[c["code"]] = {
                    "code": c["code"], "qty": qty,
                    "entry_price": c["entry_price"],
                    "exit_price": c["exit_price"],
                    "exit_date": c["exit_date"],
                    "score": c["score"],
                }

        peak_open = max(peak_open, len(open_pos))
        eq = cash + sum(p["qty"] * p["entry_price"] for p in open_pos.values())
        equity_curve.append(eq)
        dayret = 0.0 if prev_eq == 0 else (eq / prev_eq - 1.0)
        dayrets.append(dayret)
        prev_eq = eq

        if day.month == 12 and day.day == 31:
            year_end_equity[day.year] = eq
        # 월말 기록 (매월 마지막 날)
        next_day = day + timedelta(days=1)
        if next_day.month != day.month:
            month_equity[ds[:7]] = eq

    # 최종 청산
    for code in list(open_pos):
        p = open_pos[code]
        cash += p["qty"] * p["exit_price"] * (1 - cost_rate)
        sells += 1
        del open_pos[code]

    end_cap = cash
    ret_pct = (end_cap / start_cap - 1.0) * 100.0

    # MDD
    mdd = 0.0
    peak_ = equity_curve[0] if equity_curve else start_cap
    for v in equity_curve + [end_cap]:
        if v > peak_: peak_ = v
        dd_ = (v / peak_ - 1.0) * 100.0
        if dd_ < mdd: mdd = dd_

    # 연도별 수익률
    yearly = {}
    base_ = start_cap
    years = sorted(year_end_equity)
    for y in years:
        yearly[str(y)] = round((year_end_equity[y] / base_ - 1.0) * 100.0, 2)
        base_ = year_end_equity[y]
    if not years or max(years) < end.year:
        yearly[str(end.year)] = round((end_cap / base_ - 1.0) * 100.0, 2)

    # 5구간 + 확장구간 수익률
    period_boundaries = [
        ("2020-03-01", "2021-11-30"),
        ("2021-12-01", "2022-10-31"),
        ("2022-11-01", "2023-10-31"),
        ("2023-11-01", "2024-12-31"),
        ("2024-06-01", "2025-05-31"),
        ("2025-06-01", "2025-12-31"),   # 확장 구간
        ("2026-01-01", "2026-03-31"),   # 2026 Q1
    ]
    period_ret = {}
    prev_cap = start_cap
    for ps, pe in period_boundaries:
        eq_at = month_equity.get(pe[:7], None)
        if eq_at is None:
            # 마지막 기록 또는 end_cap 사용
            eq_at = end_cap if d(pe) >= end else prev_cap
        r = round((eq_at / prev_cap - 1.0) * 100.0, 2)
        period_ret[f"{ps}~{pe}"] = r
        prev_cap = eq_at

    return {
        "end_cap": round(end_cap),
        "total_return_pct": round(ret_pct, 2),
        "mdd_pct": round(mdd, 2),
        "buys": buys, "sells": sells,
        "switches": switches, "stopouts": stopouts,
        "skip_cash": skips_cash,
        "peak_open": peak_open,
        "avg_ticket": round(deployed / max(1, buys)),
        "yearly": yearly,
        "period_ret": period_ret,
        "month_equity": month_equity,
    }


def _fmt(r: dict, label: str):
    print(f"\n  ┌─ {label}")
    print(f"  │  누적수익: {r['total_return_pct']:+.2f}%  MDD: {r['mdd_pct']:+.2f}%  최종자산: {r['end_cap']:,}원")
    yr = r["yearly"]
    print(f"  │  연도별: " + "  ".join(f"{y}:{v:+.1f}%" for y, v in yr.items()))
    print(f"  └  매수:{r['buys']} 교체:{r['switches']} 스탑:{r['stopouts']} 최대보유:{r['peak_open']}")


def main():
    cfg = json.load(open(CFG, encoding="utf-8"))
    base = json.load(open(WGT, encoding="utf-8"))
    mat = load_all_trades(DB)

    wo = base["weights"]["offensive"]
    wd = base["weights"]["defensive"]
    w = blend_weights(wo, wd, 0.6)

    source_windows = [tuple(x) for x in cfg["data_windows_priority"]]

    # 공통 파라미터
    common_base = dict(
        mat=mat, source_windows=source_windows,
        capital=float(cfg["capital_krw"]),
        crash_guard=True,
        drift20_trig=-0.06, ticket_mult_stress=0.6, min_score_stress=0.20,
        min_ticket=5000000, max_ticket=12000000, ticket_pct=0.10,
        switch_stress_add=0.05, cooldown_days=3,
        dd_cut=-0.12, sell_mode="median",
    )

    # ── 시뮬레이션 기간 정의 ──────────────────────────────────────────────
    start = d("2020-03-01")
    end_orig   = d("2025-05-31")   # Codex 원본 기간
    end_2025   = d("2025-12-31")   # 2025 연말 연장
    end_2026q1 = d("2026-03-31")   # 2026 Q1 연장

    results = {}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "═"*70)
    print("  A. Codex V14 원본 (cost=0, switch=0.08, min_hold=0)")
    print("═"*70)

    # A-1. 원본 기간 (2025-05)
    r = simulate(weights=w, switch_th=0.08, cost_bp=0, min_hold_days=0,
                 start=start, end=end_orig, **common_base)
    results["V14_orig"] = r
    _fmt(r, "V14 원본 | 2020-03 ~ 2025-05 (비용 0)")

    # A-2. 2025-12 연장
    r2 = simulate(weights=w, switch_th=0.08, cost_bp=0, min_hold_days=0,
                  start=start, end=end_2025, **common_base)
    results["V14_2025"] = r2
    _fmt(r2, "V14 원본 | 2020-03 ~ 2025-12 (연장)")

    # A-3. 2026-03 연장
    r3 = simulate(weights=w, switch_th=0.08, cost_bp=0, min_hold_days=0,
                  start=start, end=end_2026q1, **common_base)
    results["V14_2026"] = r3
    _fmt(r3, "V14 원본 | 2020-03 ~ 2026-03 (최대 연장)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "═"*70)
    print("  B. V14 + 거래비용 15bp (현실화만, 로직 변경 없음)")
    print("═"*70)

    b1 = simulate(weights=w, switch_th=0.08, cost_bp=15, min_hold_days=0,
                  start=start, end=end_orig, **common_base)
    results["V14_cost_orig"] = b1
    _fmt(b1, "V14+cost15bp | 2020-03 ~ 2025-05")

    b2 = simulate(weights=w, switch_th=0.08, cost_bp=15, min_hold_days=0,
                  start=start, end=end_2025, **common_base)
    results["V14_cost_2025"] = b2
    _fmt(b2, "V14+cost15bp | 2020-03 ~ 2025-12 (연장)")

    b3 = simulate(weights=w, switch_th=0.08, cost_bp=15, min_hold_days=0,
                  start=start, end=end_2026q1, **common_base)
    results["V14_cost_2026"] = b3
    _fmt(b3, "V14+cost15bp | 2020-03 ~ 2026-03 (최대 연장)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "═"*70)
    print("  C. V15 개선 (cost=15bp, switch=0.12, min_hold=5일)")
    print("  변경-1: switch_th 0.08→0.12 (교체 최소화)")
    print("  변경-2: 거래비용 15bp 반영")
    print("  변경-3: 최소 보유 5일 (초단기 제거)")
    print("  변경-4: 2025-06~12 신규 소스 윈도우 추가")
    print("═"*70)

    c1 = simulate(weights=w, switch_th=0.12, cost_bp=15, min_hold_days=5,
                  start=start, end=end_orig, **common_base)
    results["V15_orig"] = c1
    _fmt(c1, "V15 | 2020-03 ~ 2025-05")

    c2 = simulate(weights=w, switch_th=0.12, cost_bp=15, min_hold_days=5,
                  start=start, end=end_2025, **common_base)
    results["V15_2025"] = c2
    _fmt(c2, "V15 | 2020-03 ~ 2025-12 (연장)")

    c3 = simulate(weights=w, switch_th=0.12, cost_bp=15, min_hold_days=5,
                  start=start, end=end_2026q1, **common_base)
    results["V15_2026"] = c3
    _fmt(c3, "V15 | 2020-03 ~ 2026-03 (최대 연장)")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n\n" + "═"*70)
    print("  ▶ 구간별 수익률 상세 비교 (모든 버전, 2026-03 기준)")
    print("═"*70)

    versions = [
        ("V14 원본(비용0)",   results["V14_2026"]),
        ("V14+비용15bp",     results["V14_cost_2026"]),
        ("V15 개선(권장)",    results["V15_2026"]),
    ]

    periods = [
        ("①2020-03~2021-11", "2020-03-01~2021-11-30"),
        ("②2021-12~2022-10", "2021-12-01~2022-10-31"),
        ("③2022-11~2023-10", "2022-11-01~2023-10-31"),
        ("④2023-11~2024-12", "2023-11-01~2024-12-31"),
        ("⑤2024-06~2025-05", "2024-06-01~2025-05-31"),
        ("⑥2025-06~2025-12", "2025-06-01~2025-12-31"),
        ("⑦2026-01~2026-03", "2026-01-01~2026-03-31"),
    ]

    header = f"  {'구간':20s}" + "".join(f"{n:>18s}" for n, _ in versions)
    print(header)
    print("  " + "-"*70)
    for plabel, pkey in periods:
        row = f"  {plabel:20s}"
        for _, rv in versions:
            v = rv["period_ret"].get(pkey, None)
            row += f"  {v:+.2f}%".rjust(16) if v is not None else f"{'—':>16s}"
        print(row)

    print("  " + "-"*70)
    print("  [연도별]")
    for y in ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]:
        row = f"  {y+'년':20s}"
        for _, rv in versions:
            v = rv["yearly"].get(y, None)
            row += f"  {v:+.2f}%".rjust(16) if v is not None else f"{'—':>16s}"
        print(row)

    print("  " + "="*70)
    row = f"  {'누적수익률':20s}"
    for _, rv in versions:
        row += f"  {rv['total_return_pct']:+.2f}%".rjust(16)
    print(row)

    row = f"  {'MDD':20s}"
    for _, rv in versions:
        row += f"  {rv['mdd_pct']:+.2f}%".rjust(16)
    print(row)

    row = f"  {'최종자산(원)':20s}"
    for _, rv in versions:
        row += f"  {rv['end_cap']:,}".rjust(16)
    print(row)

    row = f"  {'매수횟수':20s}"
    for _, rv in versions:
        row += f"  {rv['buys']}".rjust(16)
    print(row)

    row = f"  {'교체매매':20s}"
    for _, rv in versions:
        row += f"  {rv['switches']}".rjust(16)
    print(row)

    row = f"  {'스탑아웃':20s}"
    for _, rv in versions:
        row += f"  {rv['stopouts']}".rjust(16)
    print(row)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n\n" + "═"*70)
    print("  ▶ V15 로직 변경 근거 및 효과 요약")
    print("═"*70)
    v14 = results["V14_2026"]
    v15 = results["V15_2026"]
    v14c = results["V14_cost_2026"]
    print(f"""
  [변경-1] switch_th: 0.08 → 0.12 (교체 임계치 강화)
    효과: 교체 {v14['switches']}건 → {v15['switches']}건 감소
    이유: 점수 차이가 크지 않으면 보유 유지. 교체 반복은 비용 낭비이며
          기존 포지션의 충분한 수익 실현 전에 이탈하는 문제를 방지.

  [변경-2] 거래비용 15bp 반영 (편도, 왕복 30bp)
    효과: 비용 미반영 {v14['total_return_pct']:+.1f}% → 비용 반영 {v14c['total_return_pct']:+.1f}%
    이유: KRX 온라인 수수료 약 0.015~0.05% + 슬리피지 5~10bp.
          {v14['buys']}회 매수 × 왕복 거래비용이 누적 수익에 결정적 영향.
          실운용에서 이 비용을 무시하면 전략의 실제 성과를 크게 과대평가.

  [변경-3] 최소 보유기간 5일 필터
    효과: 초단기(5일 미만) 신호 제거 → 거래 횟수 감소 → 비용 절감
    이유: 메타 시뮬레이터에서 exit_date - entry_date < 5일인 trades는
          수익/손실 규모 대비 거래비용이 과도하게 발생. 제거가 효율적.

  [변경-4] 2025-06~12 소스 윈도우 추가 (AUDIT 데이터)
    활용 전략: v10(AUDIT), v11(AUDIT), v_trend(AUDIT), v8(rotation)
    미포함: v5, v1_value, v2 (2025-06 이후 데이터 없음)
    주의: 4개 전략만 사용 → 분산도 낮고 성과 편차 클 수 있음

  ⚠️ 2026-01~03 현황:
    • 모든 기존 전략의 max exit_date가 2025-12-30
    • 2026년 이후 새로운 신호 없음 → 현금 보유 (수익 0%)
    • 실운용 시 2026년 신규 백테스트 데이터 확보 필요
""")

    # JSON 저장
    out = "/Applications/stock_dashboard/scratch/v15_extended_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"  결과 저장: {out}")


if __name__ == "__main__":
    main()
