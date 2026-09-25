#!/usr/bin/env python3
"""HANDOFF §12 R6 (research venv): account-level drawdown rule on the momentum/breakout proxy book (turnover_rules_20260925 simulator).

The virtual ledger (83 business days, max drawdown -3.4%) never reaches the -10%/-15% thresholds, so the rule cannot be validated there; the 2020-2026
proxy book (incl. the 2026-07~09 sell-off) is used instead. Rule: account equity vs its 60-day high: dd <= -10% -> only every 2nd new entry ('half'),
dd <= -15% -> no new entries ('stop'); a state is left only when KOSPI > MA60 and dd >= -5% (hysteresis). Existing positions are never closed.
Compared for the R3-accepted setting (cooldown 20d, mcap floor 1000억) and for the raw proxy. Output research_outputs/account_dd_rule_20260925.md
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs"
spec = importlib.util.spec_from_file_location("tr", Path(__file__).with_name("turnover_rules_20260925.py"))
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)
HALF, STOP, RECOVER, LOOK = -10.0, -15.0, -5.0, 60


def simulate_rule(cands, prices, n_days, ks_ok, min_hold, q, cooldown, floor, rule: bool):
    pnl = np.zeros(n_days)
    live_until, last_exit, trades, skipped = [], {}, 0, 0
    state, alt = "normal", 0
    w = 1.0 / tr.MAXPOS
    sel = cands[(cands.rank_ord <= np.ceil(q * cands.n_day)) & (cands.mcap >= floor)]
    for t0, j, mc in zip(sel.t0.to_numpy(), sel.j.to_numpy(), sel.mcap.to_numpy()):
        live_until = [e for e in live_until if e > t0]
        if len(live_until) >= tr.MAXPOS or last_exit.get(j, -10**9) + cooldown > t0:
            continue
        if rule and t0 > 5:
            eq = 1 + np.cumsum(pnl[:t0])
            dd = (eq[-1] / eq[-LOOK:].max() - 1) * 100
            if dd <= STOP:
                new = "stop"
            elif dd <= HALF:
                new = "half" if state != "stop" else "stop"
            elif state != "normal" and not (bool(ks_ok[t0 - 1]) and dd >= RECOVER):
                new = state
            else:
                new = "normal"
            state = new
            if state == "stop":
                skipped += 1
                continue
            if state == "half":
                alt += 1
                if alt % 2 == 0:
                    skipped += 1
                    continue
        p = prices[t0:t0 + tr.HOLD_MAX + 1, j]
        if len(p) < 3 or not np.isfinite(p[0]) or p[0] <= 0:
            continue
        peak, exit_i = p[0], len(p) - 1
        for i in range(1, len(p)):
            if not np.isfinite(p[i]):
                exit_i = i - 1
                break
            peak = max(peak, p[i])
            if i >= min_hold and p[i] <= peak * (1 - tr.STOP):
                exit_i = i
                break
        path = np.where(np.isfinite(p[: exit_i + 1]), p[: exit_i + 1], p[0])
        pnl[t0 + 1: t0 + 1 + len(path) - 1] += np.diff(path) / path[0] * w
        s = tr.slip(mc)
        pnl[t0] -= w * (tr.FEE + s)
        pnl[min(t0 + exit_i, n_days - 1)] -= w * (tr.FEE + s + tr.TAX)
        live_until.append(t0 + exit_i + 1)
        last_exit[j] = t0 + exit_i
        trades += 1
    return pnl, trades, skipped


def main() -> None:
    px, first, strength, snap = tr.build_signals()
    cands, prices = tr.candidates(px, first, strength, snap)
    bm = pd.read_parquet(tr.IN / "benchmark.parquet").close.reindex(px.index).ffill()
    ks_ok = (bm > bm.rolling(60).mean()).to_numpy()
    idx = px.index
    rows = []
    for label, cd, fl in (("raw proxy", 0, 0), ("R3 accepted (cooldown 20d + mcap>=1000억)", 20, 1000)):
        for rule in (False, True):
            pnl, trades, skipped = simulate_rule(cands, prices, len(px), ks_ok, 0, 1.0, cd, fl, rule)
            r = {"book": label, "account_dd_rule": "on" if rule else "off", "trades": trades, "entries_skipped": skipped}
            for name, (a, b) in (("full", ("2020-06-01", "2026-09-23")), ("train", tr.TRAIN), ("valid", tr.VALID), ("crash", tr.CRASH)):
                for k, v in tr.stats(pnl, idx, a, b).items():
                    r[f"{name}_{k}"] = round(v, 2) if v == v else np.nan
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "account_dd_rule_20260925.csv", index=False)
    print(df.to_string(index=False))
    md = ["# R6 계좌 단위 낙폭 규칙 (대용 포트폴리오 온/오프) — 2026-09-25", "",
          "시스템 검증 결과이며 투자 권유가 아니다. 가상 원장(83영업일, 최대 낙폭 -3.4%)은 임계값에 도달한 적이 없어 검증 불가 → 모멘텀·돌파 대용 포트폴리오(2020-06~2026-09, 급락 창 포함)로 대체. "
          "규칙: 계좌 평가액이 60일 고점 대비 -10% 이하면 신규 진입 격번(50% 축소), -15% 이하면 신규 진입 중단, KOSPI>MA60 이고 낙폭 -5% 이내일 때만 해제. 기존 포지션은 청산하지 않는다.", "",
          df.to_markdown(index=False), ""]
    (OUT / "account_dd_rule_20260925.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
