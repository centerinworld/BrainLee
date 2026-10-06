#!/usr/bin/env python3
"""스탁이지 3전략(peak·momentum·value) 가상매매 일치도 매일 측정 — 읽기 전용(2026-10-07, docs/Stock_Strategy.md S29·§11).

측정:
  site        : 스탁이지 앱 API 현재 보유 종목(stockeasy_analyzer.fetch_strategy_api)
  ours        : peak_holding 활성 보유(같은 전략 키)
  match       : 둘 다 보유 / 사이트에만(미편입) / 우리만(미청산)
  entry_gap   : 둘 다 보유한 종목의 매수가 차이(우리 ÷ 스탁이지 − 1, %)
  blocks_1d   : 최근 1일 virtual_guard_log 차단·shadow 기록(전략·가드별)
  dup_sells   : 같은 전략·종목·날짜 매도 2건 이상의 초과 행 수(실현 손익 왜곡)
결과: research_outputs/stockeasy_mirror/<날짜>.json + history.jsonl(한 줄 요약 누적)
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from stockeasy_analyzer import fetch_strategy_api  # noqa: E402

OUT = ROOT / "research_outputs" / "stockeasy_mirror"
STRATS = ("peak", "momentum", "value")


def main():
    conn = connect_primary_db(readonly=True)
    since = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    res = {"at": datetime.now().isoformat(timespec="seconds"), "strategies": {}}
    for s in STRATS:
        d = fetch_strategy_api(s)
        ours = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT stock_code, stock_name, buy_price FROM peak_holding WHERE strategy=? AND is_active=1", (s,)).fetchall()}
        if d is None:
            res["strategies"][s] = {"site_ok": False, "ours": len(ours)}
            continue
        site = {(h.get("stock_code") or ""): (h.get("name"), float(h.get("buy_price") or 0)) for h in d.get("holdings") or []}
        both = sorted(set(site) & set(ours))
        gaps = [round((float(ours[c][1] or 0) / site[c][1] - 1) * 100, 2) for c in both if site[c][1] and ours[c][1]]
        blocks = [tuple(r) for r in conn.execute(
            "SELECT guard, decision, COUNT(*) FROM virtual_guard_log WHERE strategy=? AND logged_at>=? GROUP BY 1,2", (s, since)).fetchall()]
        res["strategies"][s] = {
            "site_ok": True, "site": len(site), "ours": len(ours), "both": len(both),
            "site_only": [f"{c} {site[c][0]}" for c in sorted(set(site) - set(ours))],
            "ours_only": [f"{c} {ours[c][0]}" for c in sorted(set(ours) - set(site))],
            "match_pct": round(len(both) / len(set(site) | set(ours)) * 100, 1) if (site or ours) else 100.0,
            "entry_gap_pct": gaps,
            "blocks_1d": [list(b) for b in blocks],
        }
    res["dup_sells"] = {r[0]: int(r[1]) for r in conn.execute(
        """SELECT strategy, SUM(n-1) FROM (SELECT strategy, stock_name, substr(tx_at::text,1,10) d, COUNT(*) n FROM peak_trade
           WHERE tx_type='sell' GROUP BY 1,2,3 HAVING COUNT(*)>1) x GROUP BY 1""").fetchall()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{datetime.now():%Y-%m-%d}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    line = {"at": res["at"], **{s: {k: v.get(k) for k in ("site", "ours", "both", "match_pct")} for s, v in res["strategies"].items()},
            "dup_sells": res["dup_sells"]}
    with open(OUT / "history.jsonl", "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps(line, ensure_ascii=False))


if __name__ == "__main__":
    main()
