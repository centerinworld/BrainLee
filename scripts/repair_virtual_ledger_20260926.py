#!/usr/bin/env python3
"""HANDOFF §15 V6: repair the contaminated virtual cash ledger. Dry-run by default; --apply writes inside ONE transaction after creating backup tables.

A. Legacy buys (written by scripts/backfill_virtual_cash_ledger.py with stock_code='' and a placeholder negative holding_id = -trade_id): restore stock_code and the real
   holding_id through ref_key 'peak_trade:<id>' -> peak_trade -> peak_holding.
B. Mirror-only accounts (momentum, peak): the StockEasy mirror opened positions WITHOUT ledger buys while the API sell path credited the proceeds, inflating the cash
   balance (429.9M / 178.5M on a 100M seed). For every holding of these strategies that has a ledger SELL without a BUY, or is still ACTIVE without a BUY, an idempotent
   repair BUY row (ref_key 'repair_buy:<holding_id>', occurred_at = entry_date) debits gross + fee + slippage with the ledger's own rates; the account balance/fees/slippage
   are updated, realized_pnl_net is reduced by the buy costs that the sell rows never deducted. Nothing is deleted.
Backups: virtual_cash_ledger_backup_v6_20260926, virtual_cash_accounts_backup_v6_20260926 (rows of the affected strategies). Logged to data_fix_log."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from virtual_trading_ledger import BUY_COMMISSION_RATE, SLIPPAGE_RATE  # noqa: E402

MIRROR = ("momentum", "peak")


def main(apply: bool) -> int:
    conn = connect_primary_db(timeout=300, readonly=not apply)
    # ---- A. legacy buys
    a_updates, a_unresolved = [], 0
    for lid, strat, qty, price, ref in conn.execute(
            "SELECT id, strategy, quantity, price, ref_key FROM virtual_cash_ledger WHERE event_type='buy' AND (stock_code IS NULL OR stock_code='' OR holding_id < 0)").fetchall():
        code = hid = None
        try:
            tid = int(str(ref).split(":")[1])
            pt = conn.execute("SELECT holding_id, stock_name, price FROM peak_trade WHERE id=?", (tid,)).fetchone()
        except Exception:
            pt = None
        if pt:
            if pt[0] and int(pt[0]) > 0:
                h = conn.execute("SELECT id, stock_code FROM peak_holding WHERE id=?", (int(pt[0]),)).fetchone()
                if h:
                    hid, code = int(h[0]), h[1]
            if not code:
                h = conn.execute("SELECT id, stock_code FROM peak_holding WHERE stock_name=? AND strategy=? AND quantity=? ORDER BY ABS(buy_price-?) LIMIT 1",
                                 (pt[1], strat, int(qty), float(price))).fetchone()
                if h:
                    hid, code = int(h[0]), h[1]
        if code:
            a_updates.append((code, hid, lid))
        else:
            a_unresolved += 1
    print(f"A. legacy buys: restorable={len(a_updates)} unresolved={a_unresolved}")

    # ---- B. repair buys for mirror-only accounts
    b_rows, b_skipped = [], []
    for strat in MIRROR:
        acct = conn.execute("SELECT balance_krw FROM virtual_cash_accounts WHERE strategy=?", (strat,)).fetchone()
        if not acct:
            continue
        bal = float(acct[0])
        sold = {int(r[0]): int(r[1]) for r in conn.execute(
            "SELECT holding_id, quantity FROM virtual_cash_ledger WHERE strategy=? AND event_type='sell' AND holding_id IS NOT NULL", (strat,)).fetchall()}
        has_buy = {int(r[0]) for r in conn.execute(
            "SELECT holding_id FROM virtual_cash_ledger WHERE strategy=? AND event_type='buy' AND holding_id IS NOT NULL", (strat,)).fetchall()}
        for hid, code, name, qty, bp, entry, active in conn.execute(
                "SELECT id, stock_code, stock_name, quantity, buy_price, entry_date, is_active FROM peak_holding WHERE strategy=? ORDER BY entry_date, id", (strat,)).fetchall():
            hid = int(hid)
            if hid in has_buy or not (hid in sold or int(active or 0) == 1):
                continue
            if hid in sold and int(sold[hid]) != int(qty or 0):
                b_skipped.append((strat, hid, name, f"sell qty {sold[hid]} != holding qty {qty}"))
                continue
            if not qty or not bp or float(bp) <= 0:
                b_skipped.append((strat, hid, name, "no quantity/price"))
                continue
            gross = float(bp) * int(qty)
            fee, slip = gross * BUY_COMMISSION_RATE, gross * SLIPPAGE_RATE
            delta = -(gross + fee + slip)
            bal += delta
            b_rows.append({"strategy": strat, "holding_id": hid, "code": code, "name": name, "qty": int(qty), "price": float(bp), "gross": gross,
                           "fee": fee, "slip": slip, "delta": delta, "balance_after": bal, "entry": str(entry)[:10], "sold": hid in sold})
    for strat in MIRROR:
        rs = [r for r in b_rows if r["strategy"] == strat]
        cur = conn.execute("SELECT balance_krw FROM virtual_cash_accounts WHERE strategy=?", (strat,)).fetchone()
        if cur:
            print(f"B. {strat}: repair buys={len(rs)} total debit={sum(r['delta'] for r in rs)/1e6:,.1f}M  balance {float(cur[0])/1e6:,.1f}M -> "
                  f"{(rs[-1]['balance_after'] if rs else float(cur[0]))/1e6:,.1f}M (seed 100.0M)")
    if b_skipped:
        print("   skipped:", b_skipped[:5])
    if any(r["balance_after"] < 0 for r in b_rows):
        print("ABORT: a repaired balance would be negative")
        return 4
    if not apply:
        print("dry-run: 변경 없음 (--apply 로 실행)")
        return 0
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        conn.execute("CREATE TABLE IF NOT EXISTS virtual_cash_ledger_backup_v6_20260926 AS SELECT * FROM virtual_cash_ledger WHERE false")
        conn.execute("CREATE TABLE IF NOT EXISTS virtual_cash_accounts_backup_v6_20260926 AS SELECT * FROM virtual_cash_accounts WHERE false")
        conn.execute("INSERT INTO virtual_cash_ledger_backup_v6_20260926 SELECT * FROM virtual_cash_ledger WHERE id NOT IN (SELECT id FROM virtual_cash_ledger_backup_v6_20260926)")
        conn.execute("INSERT INTO virtual_cash_accounts_backup_v6_20260926 SELECT * FROM virtual_cash_accounts WHERE strategy NOT IN (SELECT strategy FROM virtual_cash_accounts_backup_v6_20260926)")
        for code, hid, lid in a_updates:
            if hid:
                conn.execute("UPDATE virtual_cash_ledger SET stock_code=?, holding_id=? WHERE id=?", (code, hid, lid))
            else:
                conn.execute("UPDATE virtual_cash_ledger SET stock_code=? WHERE id=?", (code, lid))
        now = datetime.now().isoformat(timespec="seconds")
        for r in b_rows:
            ref = f"repair_buy:{r['holding_id']}"
            if conn.execute("SELECT 1 FROM virtual_cash_ledger WHERE ref_key=?", (ref,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO virtual_cash_ledger(strategy,event_type,stock_code,stock_name,holding_id,quantity,price,gross_amount,fee,tax,slippage_cost,cash_delta,balance_after,"
                "realized_pnl_gross,realized_pnl_net,ref_key,occurred_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["strategy"], "buy", r["code"], r["name"], r["holding_id"], r["qty"], r["price"], r["gross"], r["fee"], 0.0, r["slip"], r["delta"], r["balance_after"],
                 0.0, 0.0, ref, r["entry"] + " 00:00:00", now))
            conn.execute("INSERT INTO virtual_position_costs(holding_id,strategy,stock_code,stock_name,buy_fee,buy_slippage,gross_cost,opened_at) VALUES(?,?,?,?,?,?,?,?) "
                         "ON CONFLICT(holding_id) DO NOTHING", (r["holding_id"], r["strategy"], r["code"], r["name"], r["fee"], r["slip"], r["gross"], r["entry"]))
            extra = (r["fee"] + r["slip"]) if r["sold"] else 0.0     # sell rows never deducted these buy costs from realized_pnl_net
            conn.execute("UPDATE virtual_cash_accounts SET balance_krw=balance_krw+?, total_fees=total_fees+?, total_slippage=total_slippage+?, realized_pnl_net=realized_pnl_net-?, updated_at=? "
                         "WHERE strategy=?", (r["delta"], r["fee"], r["slip"], extra, now, r["strategy"]))
        nid = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM data_fix_log").fetchone()[0]
        conn.execute("INSERT INTO data_fix_log(id, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (nid, "virtual_cash_ledger/virtual_cash_accounts", "V6 ledger contamination repair (momentum/peak repair buys; legacy buy code/holding_id restore)",
                      len(b_rows) + len(a_updates), "A: restore stock_code/holding_id via ref_key->peak_trade->peak_holding; B: idempotent repair buys ref_key repair_buy:<holding_id>",
                      "momentum balance 429.9M / peak 178.5M on 100M seed (sells credited without buys)", f"repair buys={len(b_rows)}, legacy restored={len(a_updates)}",
                      "repair_virtual_ledger_20260926.py", f"v6_ledger_repair_{stamp}"))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"롤백: {exc}")
        return 3
    print("적용 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
