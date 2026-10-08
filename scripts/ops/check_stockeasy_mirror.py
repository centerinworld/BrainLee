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
2026-10-07(REVIEW_PLAN §23-3): 운영 계좌(peak·momentum·value)와 미러 계좌(<전략>_mirror)를 두 줄로 측정.
  미러는 source_gap_pct(우리 source_price ÷ 스탁이지 매수가 − 1)가 0이어야 하고 일치율 100%가 목표.
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
    try:
        conn.execute("SELECT source_price FROM peak_holding LIMIT 1").fetchone()
        src_col = "source_price"
    except Exception:
        conn.rollback()
        src_col = "NULL"
    for base in STRATS:
      d = fetch_strategy_api(base)
      for s in (base, base + "_mirror"):
        ours = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            f"SELECT stock_code, stock_name, buy_price, {src_col} FROM peak_holding WHERE strategy=? AND is_active=1", (s,)).fetchall()}
        if d is None:
            res["strategies"][s] = {"site_ok": False, "ours": len(ours)}
            continue
        site = {(h.get("stock_code") or ""): (h.get("name"), float(h.get("buy_price") or 0)) for h in d.get("holdings") or []}
        both = sorted(set(site) & set(ours))
        gaps = [round((float(ours[c][1] or 0) / site[c][1] - 1) * 100, 2) for c in both if site[c][1] and ours[c][1]]
        src_gaps = [round((float(ours[c][2]) / site[c][1] - 1) * 100, 2) for c in both if site[c][1] and ours[c][2]]
        blocks = [tuple(r) for r in conn.execute(
            "SELECT guard, decision, COUNT(*) FROM virtual_guard_log WHERE strategy=? AND logged_at>=? GROUP BY 1,2", (s, since)).fetchall()]
        res["strategies"][s] = {
            "site_ok": True, "site": len(site), "ours": len(ours), "both": len(both),
            "site_only": [f"{c} {site[c][0]}" for c in sorted(set(site) - set(ours))],
            "ours_only": [f"{c} {ours[c][0]}" for c in sorted(set(ours) - set(site))],
            "match_pct": round(len(both) / len(set(site) | set(ours)) * 100, 1) if (site or ours) else 100.0,
            "entry_gap_pct": gaps,
            "source_gap_pct": src_gaps,
            "blocks_1d": [list(b) for b in blocks],
        }
    res["dup_sells"] = {r[0]: int(r[1]) for r in conn.execute(
        """SELECT strategy, SUM(n-1) FROM (SELECT strategy, stock_name, substr(tx_at::text,1,10) d, COUNT(*) n FROM peak_trade
           WHERE tx_type='sell' GROUP BY 1,2,3 HAVING COUNT(*)>1) x GROUP BY 1""").fetchall()}
    # 2026-10-08(REVIEW_PLAN §34-4 ③): 매도된 보유 재활성화 거부(already_sold_same_entry) 일 집계 — 반복 매도 재발 감시
    try:
        _log = ROOT / "logs" / "peak_monitor.launchd.log"
        _today = datetime.now().strftime("%Y-%m-%d")
        res["already_sold_rejects_today"] = sum(
            1 for _l in _log.read_text(encoding="utf-8", errors="ignore").splitlines()
            if _l.startswith(_today) and "already_sold_same_entry" in _l and "오늘은 재시도 안 함" in _l) if _log.exists() else None
    except Exception:
        res["already_sold_rejects_today"] = None
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{datetime.now():%Y-%m-%d}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    line = {"at": res["at"], **{s: {k: v.get(k) for k in ("site", "ours", "both", "match_pct")} for s, v in res["strategies"].items()},
            "dup_sells": res["dup_sells"], "already_sold_rejects_today": res.get("already_sold_rejects_today")}
    with open(OUT / "history.jsonl", "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    print(json.dumps(line, ensure_ascii=False))


if __name__ == "__main__":
    main()
