#!/usr/bin/env python3
"""중복 매도 정리 후 peak_holding 보정 (REVIEW_PLAN §36-3 ① 검토자 지적, 2026-10-08). 기본 dry-run, --apply 때만 쓴다.

배경: 반복 매도 경로(`/api/trend/sell`)는 peak_holding의 sell_price·sold_at·current_price·profit_pct를 먼저 갱신한 뒤
peak_trade를 넣는다. 그래서 두 번째 이후 peak_trade만 지우면(cleanup_duplicate_virtual_sells_20261008.py, 111행)
보유 행에는 '마지막 반복 매도' 값이 남는다. 정리 때 남긴 첫 매도(keep_ids)를 기준으로 보유 행을 되돌린다.
대응: peak_trade.holding_id가 있으면 그것, 없으면 strategy + stock_name + 매도일(sold_at 날짜 = tx_at 날짜) + quantity가 같은
      비활성 보유. 후보가 2개 이상이면 자동 갱신하지 않고 '수동 검토'로 남긴다(0개도 목록에 남김).
쓰기 전 대상 보유 행 전체를 peak_holding_dup_sell_sync_backup_20261008(run_id, backed_up_at, row_json)에 백업.
적용 후: 남은 첫 매도와 보유의 sold_at/tx_at·sell_price/price·profit_pct 불일치 0인지 다시 센다.
사용: sync_holdings_after_dup_sell_cleanup_20261008.py [--apply]
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

BACKUP = "peak_holding_dup_sell_sync_backup_20261008"
CLEANUP = ROOT / "research_outputs" / "virtual_sell_cleanup_20261008_apply.json"


def _ts(v):
    return str(v)[:19] if v is not None else None


def _cols(conn, table):
    try:
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name=? "
                                           "ORDER BY ordinal_position", (table,)).fetchall()]
        if cols:
            return cols
    except Exception:
        pass
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]   # SQLite(테스트)


def plan(conn, keep_ids=None):
    keep_ids = keep_ids if keep_ids is not None else json.loads(CLEANUP.read_text())["keep_ids"]
    hcols = _cols(conn, "peak_holding")
    items, ambiguous, missing = [], [], []
    for tid in keep_ids:
        t = conn.execute("SELECT id, holding_id, strategy, stock_name, price, quantity, profit_pct, tx_at FROM peak_trade WHERE id=?", (tid,)).fetchone()
        if not t:
            missing.append({"trade_id": tid, "why": "trade 없음"}); continue
        tid, hid, strat, name, price, qty, ppct, tx_at = t
        if hid:
            hs = conn.execute(f"SELECT {','.join(hcols)} FROM peak_holding WHERE id=?", (hid,)).fetchall()
        else:
            hs = conn.execute(f"SELECT {','.join(hcols)} FROM peak_holding WHERE strategy=? AND stock_name=? AND is_active=0 "
                              "AND substr(CAST(sold_at AS TEXT),1,10)=? AND quantity=?", (strat, name, str(tx_at)[:10], qty)).fetchall()
        hs = [dict(zip(hcols, h)) for h in hs]
        if len(hs) > 1:
            ambiguous.append({"trade_id": tid, "strategy": strat, "stock_name": name, "tx_at": _ts(tx_at), "holding_ids": [h["id"] for h in hs]}); continue
        if not hs:
            missing.append({"trade_id": tid, "strategy": strat, "stock_name": name, "tx_at": _ts(tx_at), "why": "대응 보유 없음"}); continue
        h = hs[0]
        diff = (_ts(h["sold_at"]) != _ts(tx_at) or abs(float(h["sell_price"] or 0) - float(price or 0)) > 1e-6
                or abs(float(h["profit_pct"] or 0) - float(ppct or 0)) > 0.005)
        items.append({"holding": h, "trade": {"id": tid, "price": price, "profit_pct": ppct, "tx_at": tx_at}, "needs_update": diff})
    return items, ambiguous, missing


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=120, readonly=not a.apply)   # dry-run은 읽기 전용(§39)
    items, ambiguous, missing = plan(conn)
    upd = [x for x in items if x["needs_update"]]
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "matched": len(items), "needs_update": len(upd), "ambiguous": ambiguous, "missing": missing,
           "updates": [{"holding_id": x["holding"]["id"], "strategy": x["holding"]["strategy"], "stock_name": x["holding"]["stock_name"],
                        "now": {"sold_at": _ts(x["holding"]["sold_at"]), "sell_price": x["holding"]["sell_price"], "profit_pct": x["holding"]["profit_pct"]},
                        "first_sell": {"trade_id": x["trade"]["id"], "tx_at": _ts(x["trade"]["tx_at"]), "price": x["trade"]["price"],
                                       "profit_pct": x["trade"]["profit_pct"]}} for x in upd]}
    if a.apply and upd:
        run_id = f"dup_sell_holding_sync_{datetime.now():%Y%m%d_%H%M%S}"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, backed_up_at TEXT, row_json TEXT)")
        now = datetime.now().isoformat(timespec="seconds")
        conn.executemany(f"INSERT INTO {BACKUP} (run_id, backed_up_at, row_json) VALUES (?,?,?)",
                         [(run_id, now, json.dumps(x["holding"], ensure_ascii=False, default=str)) for x in upd])
        for x in upd:
            t = x["trade"]
            conn.execute("UPDATE peak_holding SET sell_price=?, sold_at=?, current_price=?, profit_pct=?, "
                         "sold_price=CASE WHEN sold_price IS NULL THEN NULL ELSE ? END, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (t["price"], _ts(t["tx_at"]), t["price"], t["profit_pct"], t["price"], x["holding"]["id"]))
        conn.commit()
        res.update({"applied": True, "run_id": run_id, "backup_table": BACKUP})
        items2, _, _ = plan(conn)
        res["after_mismatch"] = sum(1 for x in items2 if x["needs_update"])
    else:
        res["applied"] = False
    out = ROOT / "research_outputs" / f"dup_sell_holding_sync_20261008_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "matched", "needs_update", "applied")}, ensure_ascii=False),
          "| 애매", len(ambiguous), "| 대응 없음", len(missing), "| 적용 후 불일치", res.get("after_mismatch"))
    for u in res["updates"][:8]:
        print(" ", u["strategy"], u["stock_name"], u["now"], "→", u["first_sell"])
    for m in (ambiguous + missing)[:10]:
        print("  검토:", m)


if __name__ == "__main__":
    main()
