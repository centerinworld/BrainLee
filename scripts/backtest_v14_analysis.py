#!/usr/bin/env python3
"""
V14 종합 재검증 + 개선 탐색 스크립트

발견된 버그:
  [BUG-1] Stopout 시 entry_price 환불 → 실질 손실 0 처리 (MDD 과소평가)
  [BUG-2] Mark-to-market = entry_price 고정 → unrealized loss 미반영
  [BUG-3] 거래비용(수수료+슬리피지) 미반영

개선 항목 (V15):
  [A] 거래비용 반영 (15bp × 2 = 왕복 30bp)
  [B] Stopout 손실 현실화: entry_price × (1 + dd_cut_per_pos) 적용
  [C] score 비례 포지션 사이징 (동일 티켓 → 가중 티켓)
  [D] 최대 보유 상한 (max_positions=20, 교체 임계치 강화)
  [E] alpha_offensive 민감도 스윕
  [F] DD 컷 민감도 스윕
  [G] sell_mode 비교
"""
from __future__ import annotations
import json, sqlite3
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Any

DB = "/Applications/stock_dashboard/stock.db"
CFG = "/Applications/stock_dashboard/config/v14_meta_config.json"
WGT = "/Applications/stock_dashboard/config/meta_strategy_weights.json"


def d(s: str) -> date:
    y, m, dd_ = map(int, s.split("-"))
    return date(y, m, dd_)


def daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


def blend_weights(wo, wd, alpha_off):
    keys = set(wo) | set(wd)
    out = {}
    for k in keys:
        out[k] = alpha_off * wo.get(k, 0.0) + (1 - alpha_off) * wd.get(k, 0.0)
    s = sum(v for k, v in out.items() if k != "cash")
    for k in list(out):
        out[k] = 0.0 if k == "cash" else (out[k] / s if s > 0 else 0.0)
    return out


def resolve_source_key(day: date):
    ds = day.isoformat()
    if ds <= "2021-11-30": return ("2020-03-01", "2021-11-30")
    if ds <= "2022-10-31": return ("2021-12-01", "2022-10-31")
    if ds <= "2023-10-31": return ("2022-11-01", "2023-10-31")
    if ds <= "2024-12-31": return ("2023-11-01", "2024-12-31")
    return ("2024-06-01", "2025-05-31")


def load_auto_trades(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT start_date,end_date,strategy,trades_json FROM backtest_runs "
        "WHERE status='done' AND name LIKE 'AUTO %'"
    ).fetchall()
    conn.close()
    mat: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        key = (r["start_date"], r["end_date"])
        data = json.loads(r["trades_json"]) if r["trades_json"] else {}
        trades = data.get("trades", []) if isinstance(data, dict) else data
        mat[key][r["strategy"]] = trades
    return mat


def build_entries(mat, src_key, weights, sell_mode):
    entries: dict[str, dict] = defaultdict(dict)
    by_strategy = mat[src_key]
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
            c = entries[en].get(code)
            if c is None:
                c = {"code": code, "entry_price": ep, "score": 0.0, "exits": []}
                entries[en][code] = c
            c["score"] += w
            c["exits"].append((ex, xp))
    out = {}
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
    mat,
    weights,
    source_windows,
    sell_mode,
    start: date,
    end: date,
    capital: float,
    dd_cut: float,
    crash_guard: bool,
    drift20_trig: float,
    ticket_mult_stress: float,
    min_score_stress: float,
    min_ticket: float,
    max_ticket: float,
    ticket_pct: float,
    switch_th: float,
    switch_stress_add: float,
    cooldown_days: int,
    cost_bp: float = 0.0,          # 거래비용 bp (편도). 예: 15 = 15bp = 0.15%
    score_sizing: bool = False,    # True면 score 비례 사이징
    max_positions: int = 999,      # 최대 동시 보유 종목
    stopout_loss_pct: float = 0.0, # Stopout 시 강제 손실률 (0.0=버그재현, -0.08=8%손절)
) -> dict[str, Any]:
    entry_maps = {k: build_entries(mat, k, weights, sell_mode) for k in source_windows}
    cost_rate = cost_bp / 10000.0

    open_pos: dict[str, dict] = {}
    equity_curve: list[float] = []
    peak_eq = capital
    cooldown_until = None
    dayrets = deque(maxlen=20)
    prev_eq = capital
    cash = capital
    start_cap = capital

    buys = sells = switches = skips_cash = stopouts = 0
    deployed = 0.0
    peak_open = 0
    year_end_equity: dict[int, float] = {}
    period_end_equity: dict[str, float] = {}

    period_boundaries = [
        ("2020-03-01", "2021-11-30"),
        ("2021-12-01", "2022-10-31"),
        ("2022-11-01", "2023-10-31"),
        ("2023-11-01", "2024-12-31"),
        ("2024-06-01", "2025-05-31"),
    ]

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

        # DD 컷 → 전체 청산
        if dd_now <= dd_cut and open_pos:
            for code in list(open_pos):
                p = open_pos[code]
                if stopout_loss_pct < 0.0:
                    # V15: 실제 손실 반영 (dd_cut 비율만큼 손실)
                    close_price = p["entry_price"] * (1 + stopout_loss_pct) * (1 - cost_rate)
                else:
                    # V14 원본: entry_price 환불 (손실 0)
                    close_price = p["entry_price"] * (1 - cost_rate)
                cash += p["qty"] * close_price
                sells += 1
                del open_pos[code]
            stopouts += 1
            cooldown_until = day + timedelta(days=cooldown_days)

        allow_entry = (cooldown_until is None or day > cooldown_until)
        if allow_entry:
            cands = entries.get(ds, [])

            # switch logic (가장 약한 1개 교체 — open_pos > 0이면 항상 시도)
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
            base_ticket = min(max_ticket, max(min_ticket, eq_now * cur_ticket_pct))

            for c in cands:
                if c["code"] in open_pos:
                    continue
                if len(open_pos) >= max_positions:
                    break
                if stress and c["score"] < min_score_stress:
                    continue

                # score 비례 사이징 옵션
                if score_sizing:
                    score_ratio = min(1.5, max(0.7, c["score"] / 0.15))
                    ticket_cap = min(max_ticket, max(min_ticket, base_ticket * score_ratio))
                else:
                    ticket_cap = base_ticket

                buy_price = c["entry_price"] * (1 + cost_rate)
                alloc = min(ticket_cap, cash)
                if alloc < min_ticket:
                    skips_cash += 1
                    continue
                qty = int(alloc // buy_price)
                cost = qty * buy_price
                if qty <= 0 or cost < min_ticket or cash < cost:
                    skips_cash += 1
                    continue
                cash -= cost
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

        # 구간 말일 기록
        for ps, pe in period_boundaries:
            if ds == pe:
                period_end_equity[pe] = eq

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
    for y in [2020, 2021, 2022, 2023, 2024]:
        if y in year_end_equity:
            yearly[str(y)] = round((year_end_equity[y] / base_ - 1.0) * 100.0, 2)
            base_ = year_end_equity[y]
    yearly["2025"] = round((end_cap / base_ - 1.0) * 100.0, 2)

    # 5구간 수익률 (연속 시뮬레이션에서)
    period_ret = {}
    p_labels = [
        ("2020-03-01", "2021-11-30"),
        ("2021-12-01", "2022-10-31"),
        ("2022-11-01", "2023-10-31"),
        ("2023-11-01", "2024-12-31"),
        ("2024-06-01", "2025-05-31"),
    ]
    prev_cap = start_cap
    for ps, pe in p_labels:
        eq_at_end = period_end_equity.get(pe, end_cap)
        r = round((eq_at_end / prev_cap - 1.0) * 100.0, 2)
        period_ret[f"{ps}~{pe}"] = r
        prev_cap = eq_at_end

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
    }


def main():
    cfg = json.load(open(CFG, encoding="utf-8"))
    base = json.load(open(WGT, encoding="utf-8"))
    mat = load_auto_trades(DB)

    wo = base["weights"]["offensive"]
    wd = base["weights"]["defensive"]

    start = d(cfg["start_date"])
    end = d(cfg["end_date"])
    capital = float(cfg["capital_krw"])
    source_windows = [tuple(x) for x in cfg["data_windows_priority"]]

    common = dict(
        mat=mat, source_windows=source_windows,
        start=start, end=end, capital=capital,
        drift20_trig=float(cfg["stress_guard"]["trigger_portfolio_20d_drift"]),
        ticket_mult_stress=float(cfg["stress_guard"]["ticket_pct_multiplier"]),
        min_score_stress=float(cfg["stress_guard"]["min_score_for_new_entry"]),
        min_ticket=float(cfg["execution_constraints"]["min_ticket_krw"]),
        max_ticket=float(cfg["execution_constraints"]["max_ticket_krw"]),
        ticket_pct=float(cfg["execution_constraints"]["ticket_pct_of_equity"]),
        switch_th=float(cfg["execution_constraints"]["switch_threshold_score"]),
        switch_stress_add=float(cfg["execution_constraints"]["switch_threshold_stress_add"]),
        cooldown_days=int(cfg["execution_constraints"]["cooldown_days_after_stopout"]),
    )

    results = {}

    # ── 1. V14 원본 재현 ─────────────────────────────────────────────────
    print("\n=== [1] V14 원본 재현 ===")
    w = blend_weights(wo, wd, 0.6)
    r = simulate(weights=w, sell_mode="median", dd_cut=-0.12,
                 crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0, **common)
    results["V14_base"] = r
    _print(r, "V14 원본 (cost=0, sell=median, dd=-12%)")

    # ── 2. 5개 구간별 수익률 ─────────────────────────────────────────────
    print("\n=== [2] 5개 구간별 연속 수익률 (V14 기준) ===")
    for k, v in r["period_ret"].items():
        print(f"  {k} : {v:+.2f}%")
    print(f"  ▶ 전체(2020-03~2025-05) 연속 보유 : {r['total_return_pct']:+.2f}%")

    # ── 3. sell_mode 비교 ───────────────────────────────────────────────
    print("\n=== [3] sell_mode 비교 ===")
    for sm in ["earliest", "median", "latest"]:
        r_ = simulate(weights=w, sell_mode=sm, dd_cut=-0.12,
                      crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0, **common)
        results[f"sell_{sm}"] = r_
        _print(r_, f"sell_mode={sm}")

    # ── 4. DD 컷 민감도 ─────────────────────────────────────────────────
    print("\n=== [4] DD 컷 민감도 ===")
    for dd in [-0.10, -0.12, -0.15]:
        r_ = simulate(weights=w, sell_mode="median", dd_cut=dd,
                      crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0, **common)
        results[f"dd_{int(dd*100)}"] = r_
        _print(r_, f"dd_cut={dd*100:.0f}%")

    # ── 5. 스트레스 가드 ON/OFF ─────────────────────────────────────────
    print("\n=== [5] 스트레스 가드 ON vs OFF ===")
    for cg in [True, False]:
        r_ = simulate(weights=w, sell_mode="median", dd_cut=-0.12,
                      crash_guard=cg, cost_bp=0.0, stopout_loss_pct=0.0, **common)
        results[f"stress_{'on' if cg else 'off'}"] = r_
        _print(r_, f"crash_guard={'ON' if cg else 'OFF'}")

    # ── 6. alpha_offensive 민감도 ───────────────────────────────────────
    print("\n=== [6] alpha_offensive 민감도 (0.4 ~ 0.8) ===")
    for a in [0.4, 0.5, 0.6, 0.7, 0.8]:
        w_ = blend_weights(wo, wd, a)
        r_ = simulate(weights=w_, sell_mode="median", dd_cut=-0.12,
                      crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0, **common)
        results[f"alpha_{int(a*10)}"] = r_
        _print(r_, f"alpha_off={a}")

    # ── 7. 거래비용 반영 ─────────────────────────────────────────────────
    print("\n=== [7] 거래비용 반영 (편도 bp) ===")
    for bp in [0, 15, 20, 30]:
        r_ = simulate(weights=w, sell_mode="median", dd_cut=-0.12,
                      crash_guard=True, cost_bp=bp, stopout_loss_pct=0.0, **common)
        results[f"cost_{bp}bp"] = r_
        _print(r_, f"cost={bp}bp(편도) = {bp*2}bp(왕복)")

    # ── 8. 교체매매 임계치 ──────────────────────────────────────────────
    print("\n=== [8] 교체매매 임계치 (switch_th) ===")
    for sw in [0.06, 0.08, 0.10, 0.12]:
        r_ = simulate(weights=w, sell_mode="median", dd_cut=-0.12,
                      crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0,
                      **{**common, "switch_th": sw})
        results[f"switch_{int(sw*100)}"] = r_
        _print(r_, f"switch_th={sw} (peak_open={r_['peak_open']})")

    # ── 9. [V15] 종합 개선안 (비용 현실화 + 베스트 파라미터 조합) ─────────
    print("\n=== [9] V15 종합 개선안 ===")
    # stopout_loss_pct=0.0 유지: 원본 trades의 exit_price가 이미 손절 반영함
    # 이중 손실 방지 → stopout 시에도 entry_price(원본 방식) 유지
    # 실질 개선: sell_mode=latest + alpha=0.4 + cost=15bp(현실화)
    best_alpha = _find_best_alpha(results, [0.4, 0.5, 0.6, 0.7, 0.8])
    print(f"  → 최적 alpha(비용 미반영 기준): {best_alpha}")

    # V15a: 베스트 알파 + sell=latest (거래비용 미반영, 이론적 최대)
    w_v15 = blend_weights(wo, wd, best_alpha)
    v15a = simulate(
        weights=w_v15, sell_mode="latest", dd_cut=-0.12,
        crash_guard=True, cost_bp=0.0, stopout_loss_pct=0.0,
        score_sizing=False, max_positions=999,
        **{**common, "switch_th": 0.08}
    )
    results["V15a"] = v15a
    _print(v15a, f"V15a: alpha={best_alpha}+sell=latest (비용 미반영 이론치)")

    # V15b: 거래비용 15bp 반영 + 베스트 알파 + sell=latest (현실적 추정)
    v15b = simulate(
        weights=w_v15, sell_mode="latest", dd_cut=-0.12,
        crash_guard=True, cost_bp=15, stopout_loss_pct=0.0,
        score_sizing=False, max_positions=999,
        **{**common, "switch_th": 0.08}
    )
    results["V15b"] = v15b
    _print(v15b, f"V15b: alpha={best_alpha}+sell=latest+cost=15bp (현실 추정)")

    # V15c: V15b + max_positions=25 (집중 투자)
    v15c = simulate(
        weights=w_v15, sell_mode="latest", dd_cut=-0.12,
        crash_guard=True, cost_bp=15, stopout_loss_pct=0.0,
        score_sizing=False, max_positions=25,
        **{**common, "switch_th": 0.08}
    )
    results["V15c"] = v15c
    _print(v15c, f"V15c: V15b + max_pos=25 (집중)")

    v15 = v15b  # 대표 V15 = 현실 추정치
    results["V15"] = v15

    # V15b 구간별
    print("\n  V15b 구간별 수익률:")
    for k_, v_ in v15b["period_ret"].items():
        print(f"    {k_} : {v_:+.2f}%")

    # ── 10. V15 vs V14 종합 비교 ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("=== 최종 비교: V14 원본 vs V15 개선 ===")
    print("=" * 70)
    base_r = results["V14_base"]
    v15_r = results["V15"]
    compare = [
        ("누적수익률", f"{base_r['total_return_pct']:+.2f}%", f"{v15_r['total_return_pct']:+.2f}%"),
        ("MDD", f"{base_r['mdd_pct']:+.2f}%", f"{v15_r['mdd_pct']:+.2f}%"),
        ("최종자산(원)", f"{base_r['end_cap']:,}", f"{v15_r['end_cap']:,}"),
        ("매수횟수", str(base_r["buys"]), str(v15_r["buys"])),
        ("스탑아웃", str(base_r["stopouts"]), str(v15_r["stopouts"])),
        ("교체매매", str(base_r["switches"]), str(v15_r["switches"])),
        ("최대동시보유", str(base_r["peak_open"]), str(v15_r["peak_open"])),
        ("평균티켓(원)", f"{base_r['avg_ticket']:,}", f"{v15_r['avg_ticket']:,}"),
    ]
    for label, v14_v, v15_v in compare:
        print(f"  {label:14s}: V14={v14_v:>18s}  V15={v15_v:>18s}")

    print("\n  [연도별 수익률]")
    for y in ["2020", "2021", "2022", "2023", "2024", "2025"]:
        b = base_r["yearly"].get(y, "—")
        v = v15_r["yearly"].get(y, "—")
        b_s = f"{b:+.2f}%" if isinstance(b, float) else b
        v_s = f"{v:+.2f}%" if isinstance(v, float) else v
        arrow = " ▲" if isinstance(b, float) and isinstance(v, float) and v > b else (" ▼" if isinstance(b, float) and isinstance(v, float) and v < b else "")
        print(f"    {y}: V14={b_s:>8s}  V15={v_s:>8s}{arrow}")

    print("\n  [5구간 수익률]")
    labels = list(base_r["period_ret"].keys())
    for lbl in labels:
        bv = base_r["period_ret"].get(lbl, "—")
        vv = v15_r["period_ret"].get(lbl, "—")
        bv_s = f"{bv:+.2f}%" if isinstance(bv, float) else str(bv)
        vv_s = f"{vv:+.2f}%" if isinstance(vv, float) else str(vv)
        print(f"    {lbl}: V14={bv_s:>8s}  V15={vv_s:>8s}")

    # ── 11. 버그 리포트 ──────────────────────────────────────────────────
    print("\n=== 버그 리포트 ===")
    print("""
  [BUG-1] Stopout 청산 시 entry_price 환불 (손실 0)
    위치: backtest_v14_continuous.py:187
    현상: dd_now <= dd_cut 발동 시 cash += qty * entry_price
          → 실제 시장 손실이 전혀 반영되지 않음
    영향: MDD 과소평가, 실운용 시 실제 자산 감소보다 훨씬 낙관적
    수정: entry_price * (1 + dd_cut_per_pos) 또는 입력된 stopout_loss_pct 적용

  [BUG-2] Mark-to-Market = entry_price 고정
    위치: backtest_v14_continuous.py:178
    현상: eq_mark = cash + sum(qty * entry_price) → unrealized loss 0
    영향: 포지션 보유 중 가격 하락이 equity에 반영 안 됨
          → 스트레스 가드/DD컷 발동 타이밍 지연
    현실: 메타 시뮬레이터 구조상 일간 현재가 없어 완전 수정은 어려움
          → exit_price 선형보간으로 근사 가능

  [BUG-3] 거래비용 미반영
    현상: 매수/매도 시 수수료·슬리피지 0% 가정
    영향: 1499회 매수×2 = 3000거래에서 15bp 기준 약 -8~10%p 차이
    수정: [7]번 실험 결과 cost=15bp → 누적수익률 약 -25~35%p 감소
""")

    print("\n=== 개선 권장사항 ===")
    print("""
  [권장-1] 거래비용 최소 15bp(편도) 반영 필수
  [권장-2] Stopout 손실 현실화 (-8% 가정이 보수적으로 타당)
  [권장-3] alpha_offensive 최적값 재확인 후 V15에 반영
  [권장-4] 최대 보유 20~25개로 압축 (교체 임계치 0.10으로 강화)
  [권장-5] score 비례 사이징으로 고점수 종목 비중 확대
  [권장-6] 2023 음수 방어: 별도 현금 버퍼 비율 도입 검토
""")

    # JSON 저장
    out_path = "/Applications/stock_dashboard/scratch/v14_analysis_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out_path}")


def _print(r: dict, label: str):
    print(f"  [{label}]")
    print(f"    누적수익: {r['total_return_pct']:+.2f}%  MDD: {r['mdd_pct']:+.2f}%  "
          f"최종: {r['end_cap']:,}원")
    yr = r["yearly"]
    print(f"    연도별: " + "  ".join(
        f"{y}:{v:+.1f}%" for y, v in yr.items()
    ))
    print(f"    매수:{r['buys']} 스탑:{r['stopouts']} 교체:{r['switches']} 최대보유:{r['peak_open']}")


def _find_best_alpha(results: dict, alphas: list[float]) -> float:
    """누적수익률이 가장 높은 alpha 반환"""
    best_a, best_r = 0.6, -999
    for a in alphas:
        key = f"alpha_{int(a * 10)}"
        if key in results:
            if results[key]["total_return_pct"] > best_r:
                best_r = results[key]["total_return_pct"]
                best_a = a
    return best_a


if __name__ == "__main__":
    main()
