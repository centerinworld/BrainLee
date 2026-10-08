#!/usr/bin/env python3
"""가상매매 같은 날 반복 매도 정리 (REVIEW_PLAN §33-7·§34-4 ②, 2026-10-08). 기본 dry-run, --apply 때만 쓴다.

원인: 공통 손절 후 스탁이지 재편입 요청이 매도된 보유를 되살려 같은 날 다시 매도(a69ce12에서 차단).
기준(§34-2): 같은 strategy + 종목(stock_name) + entry_date + 매도일(tx_at 날짜) + tx_type='sell' 묶음에서
             가장 이른 매도 1건만 남기고 나머지를 지운다. entry_date가 비어 있는 행은 '' 로 묶는다.
안전장치:
  - 대상 전략(--strategies, 기본 value·peak·momentum)만 정리. gpt_v18 5건은 수량이 서로 다른 묶음 3개(같은 날 재매수·재매도로 보이는
    별개 거래)가 섞여 있어 보류(§35). 매도 시각(tx_at)이 없는 행은 묶지 않는다.
  - 같은 묶음 안 매도 수량이 모두 같을 때만 '반복 매도'로 본다(수량이 다르면 별개 거래 가능 → 남김).
  - 후보 수가 --expect(기본 value=25,peak=85,momentum=1,gpt_v18=5)와 하나라도 다르면 --apply여도 쓰지 않는다.
  - 지우기 전 후보 전체 행을 peak_trade_duplicate_sell_backup_20261008(run_id, backed_up_at, row_json)에 백업.
  - 결과(후보 id, 보존 id, 전략별 실현손익 전후, 샘플 20건)를 research_outputs/virtual_sell_cleanup_20261008_<dry|apply>.json에.
사용: python3 scripts/ops/cleanup_duplicate_virtual_sells_20261008.py [--apply]
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

EXPECT = {"value": 25, "peak": 85, "momentum": 1}
BACKUP = "peak_trade_duplicate_sell_backup_20261008"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expect", default=",".join(f"{k}={v}" for k, v in EXPECT.items()))
    ap.add_argument("--strategies", default="value,peak,momentum")
    a = ap.parse_args()
    expect = {k: int(v) for k, v in (x.split("=") for x in a.expect.split(",") if x)}
    conn = connect_primary_db(timeout=120, readonly=not a.apply)   # dry-run은 읽기 전용(§39)
    cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='peak_trade' ORDER BY ordinal_position").fetchall()]
    rows = [dict(zip(cols, r)) for r in conn.execute(f"SELECT {','.join(cols)} FROM peak_trade WHERE tx_type='sell' ORDER BY tx_at, id").fetchall()]
    targets = {x for x in a.strategies.split(",") if x}
    groups = defaultdict(list)
    for r in rows:
        if r["strategy"] not in targets or not r.get("tx_at"):
            continue
        groups[(r["strategy"], r["stock_name"], str(r.get("entry_date") or ""), str(r["tx_at"])[:10])].append(r)
    keep, drop = [], []
    for k, g in groups.items():
        if len(g) > 1 and len({r.get("quantity") for r in g}) == 1:
            keep.append(g[0]); drop.extend(g[1:])
    by = defaultdict(int)
    for r in drop:
        by[r["strategy"]] += 1
    pnl_before = defaultdict(float); pnl_after = defaultdict(float)
    drop_ids = {r["id"] for r in drop}
    for r in rows:
        pnl_before[r["strategy"]] += float(r.get("profit") or 0)
        if r["id"] not in drop_ids:
            pnl_after[r["strategy"]] += float(r.get("profit") or 0)
    match = dict(by) == expect
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "candidates_by_strategy": dict(by), "expected": expect, "matches_expected": match,
           "drop_ids": sorted(drop_ids), "keep_ids": sorted(r["id"] for r in keep),
           "realized_pnl_before": {k: round(v) for k, v in pnl_before.items() if k in by},
           "realized_pnl_after": {k: round(v) for k, v in pnl_after.items() if k in by},
           "sample": [{k: str(r.get(k)) for k in ("id", "strategy", "stock_name", "entry_date", "tx_at", "price", "quantity", "profit")}
                      for r in drop[:20]]}
    if a.apply and match:
        run_id = f"dup_sell_cleanup_{datetime.now():%Y%m%d_%H%M%S}"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, backed_up_at TEXT, row_json TEXT)")
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany(f"INSERT INTO {BACKUP} (run_id, backed_up_at, row_json) VALUES (?,?,?)",
                         [(run_id, now, json.dumps(r, ensure_ascii=False, default=str)) for r in drop])
        ids = sorted(drop_ids)
        for i in range(0, len(ids), 200):
            ch = ids[i:i + 200]
            conn.execute(f"DELETE FROM peak_trade WHERE id IN ({','.join('?' * len(ch))})", tuple(ch))
        conn.commit()
        res.update({"applied": True, "run_id": run_id, "backup_table": BACKUP, "deleted": len(ids)})
    else:
        res["applied"] = False
        if a.apply:
            res["refused"] = "후보 수가 예상과 다름 — 쓰지 않음(§34-4 ②)"
    out = ROOT / "research_outputs" / f"virtual_sell_cleanup_20261008_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: res[k] for k in ("mode", "candidates_by_strategy", "expected", "matches_expected", "realized_pnl_before",
                                          "realized_pnl_after", "applied")}, ensure_ascii=False, indent=1))
    print("샘플:", json.dumps(res["sample"][:5], ensure_ascii=False))
    return 0 if (match or not a.apply) else 2


if __name__ == "__main__":
    sys.exit(main())
