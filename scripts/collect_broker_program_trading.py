"""
Collect program trading data from broker APIs.

KIS and Kiwoom both expose program trading endpoints. This collector keeps the
existing market-level table populated in 100M KRW units, and stores stock-level
daily rows in raw KRW units for later signal research.

Examples:
    python scripts/collect_broker_program_trading.py --date 20260619 --stocks 005930
    python scripts/collect_broker_program_trading.py --date 20260619 --source kis --market-only
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from collectors.kiwoom_collector import KiwoomCollector  # noqa: E402
from db_utils import stock_db_write_lock  # noqa: E402
from kis_client import kis_client  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in {"-", "--"}:
        return None
    # Kiwoom sometimes returns values like "--1026845" for negative numbers.
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    if not s:
        return None
    try:
        return sign * float(s)
    except ValueError:
        return None


def _date_fmt(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=90)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=90000")
    return con


def ensure_tables(con: sqlite3.Connection) -> None:
    last_err: Exception | None = None
    for _ in range(6):
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS program_trading_daily (
                    dt TEXT NOT NULL,
                    market TEXT NOT NULL,
                    prog_net_buy_amt REAL,
                    arb_net_buy_amt REAL,
                    non_arb_net_buy_amt REAL,
                    source TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (dt, market)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_program_stock_daily (
                    source TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    dt TEXT NOT NULL,
                    close_price REAL,
                    change_rate REAL,
                    trade_volume REAL,
                    sell_qty REAL,
                    buy_qty REAL,
                    net_buy_qty REAL,
                    sell_amt_krw REAL,
                    buy_amt_krw REAL,
                    net_buy_amt_krw REAL,
                    market_channel TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, stock_code, dt, market_channel)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_program_market_daily (
                    source TEXT NOT NULL,
                    dt TEXT NOT NULL,
                    market TEXT NOT NULL,
                    prog_net_buy_amt REAL,
                    arb_net_buy_amt REAL,
                    non_arb_net_buy_amt REAL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, dt, market)
                )
                """
            )
            con.commit()
            return
        except sqlite3.OperationalError as exc:
            last_err = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(2)
    raise sqlite3.OperationalError(f"database is locked while ensuring broker program tables: {last_err}")


def load_stock_codes(con: sqlite3.Connection, limit: int | None = None) -> list[str]:
    sql = """
        SELECT stock_code
        FROM stock_universe
        WHERE stock_code IS NOT NULL
          AND LENGTH(stock_code) = 6
          AND (
            market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
            OR market IS NULL
          )
          AND (secugrp_nm = '주권' OR secugrp_nm IS NULL)
          AND COALESCE(stock_type, '') NOT IN ('ETF', 'ETN')
          AND COALESCE(secugrp_nm, '') NOT IN ('ETF', 'ETN')
          AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
          AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
        GROUP BY stock_code
        ORDER BY stock_code
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [str(row[0]).zfill(6) for row in con.execute(sql).fetchall()]


def iter_weekdays(start: str, end: str) -> list[str]:
    cur = _parse_day(start)
    last = _parse_day(end)
    dates: list[str] = []
    while cur <= last:
        if cur.weekday() < 5:
            dates.append(_yyyymmdd(cur))
        cur += timedelta(days=1)
    return dates


def existing_market_dates(con: sqlite3.Connection, source: str | None = None) -> set[tuple[str, str]]:
    source_filter = "" if not source else " AND source = ?"
    params = () if not source else (source,)
    rows = con.execute(
        f"""
        SELECT dt, market
        FROM broker_program_market_daily
        WHERE prog_net_buy_amt IS NOT NULL{source_filter}
        """,
        params,
    ).fetchall()
    return {(str(r["dt"]).replace("-", ""), r["market"]) for r in rows}


def existing_stock_dates(con: sqlite3.Connection, source: str) -> set[tuple[str, str]]:
    rows = con.execute(
        """
        SELECT stock_code, dt
        FROM broker_program_stock_daily
        WHERE source = ? AND net_buy_amt_krw IS NOT NULL
        """,
        (source,),
    ).fetchall()
    return {(r["stock_code"], str(r["dt"]).replace("-", "")) for r in rows}


def upsert_market(con: sqlite3.Connection, dt: str, market: str, row: dict[str, Any], source: str) -> None:
    # KIS and Kiwoom market program fields are in million KRW. Existing table is 100M KRW.
    prog_eok = (_num(row.get("whol_smtn_ntby_tr_pbmn")) or _num(row.get("all_netprps")))
    arb_eok = (_num(row.get("arbt_smtn_ntby_tr_pbmn")) or _num(row.get("dfrt_trde_netprps")))
    non_arb_eok = (_num(row.get("nabt_smtn_ntby_tr_pbmn")) or _num(row.get("ndiffpro_trde_netprps")))
    vals = [None if v is None else v / 100.0 for v in (prog_eok, arb_eok, non_arb_eok)]
    con.execute(
        """
        INSERT INTO broker_program_market_daily
        (source, dt, market, prog_net_buy_amt, arb_net_buy_amt, non_arb_net_buy_amt, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source, dt, market) DO UPDATE SET
            prog_net_buy_amt=excluded.prog_net_buy_amt,
            arb_net_buy_amt=excluded.arb_net_buy_amt,
            non_arb_net_buy_amt=excluded.non_arb_net_buy_amt,
            raw_json=excluded.raw_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (source, _date_fmt(dt), market, vals[0], vals[1], vals[2], json.dumps(row, ensure_ascii=False)),
    )
    con.execute(
        """
        INSERT INTO program_trading_daily
        (dt, market, prog_net_buy_amt, arb_net_buy_amt, non_arb_net_buy_amt, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(dt, market) DO UPDATE SET
            prog_net_buy_amt=excluded.prog_net_buy_amt,
            arb_net_buy_amt=excluded.arb_net_buy_amt,
            non_arb_net_buy_amt=excluded.non_arb_net_buy_amt,
            source=excluded.source,
            updated_at=CURRENT_TIMESTAMP
        """,
        (_date_fmt(dt), market, vals[0], vals[1], vals[2], source),
    )


def upsert_stock(con: sqlite3.Connection, source: str, stock_code: str, dt: str, row: dict[str, Any]) -> None:
    if source == "kis":
        close = _num(row.get("stck_clpr"))
        change_rate = _num(row.get("prdy_ctrt"))
        volume = _num(row.get("acml_vol"))
        sell_qty = _num(row.get("whol_smtn_seln_vol"))
        buy_qty = _num(row.get("whol_smtn_shnu_vol"))
        net_qty = _num(row.get("whol_smtn_ntby_qty"))
        sell_amt = _num(row.get("whol_smtn_seln_tr_pbmn"))
        buy_amt = _num(row.get("whol_smtn_shnu_tr_pbmn"))
        net_amt = _num(row.get("whol_smtn_ntby_tr_pbmn"))
        channel = "KRX"
    else:
        close = abs(_num(row.get("cur_prc")) or 0)
        change_rate = _num(row.get("flu_rt"))
        volume = _num(row.get("trde_qty"))
        sell_qty = _num(row.get("prm_sell_qty"))
        buy_qty = _num(row.get("prm_buy_qty"))
        net_qty = _num(row.get("prm_netprps_qty"))
        sell_amt = None if _num(row.get("prm_sell_amt")) is None else _num(row.get("prm_sell_amt")) * 1_000_000
        buy_amt = None if _num(row.get("prm_buy_amt")) is None else _num(row.get("prm_buy_amt")) * 1_000_000
        net_amt = None if _num(row.get("prm_netprps_amt")) is None else _num(row.get("prm_netprps_amt")) * 1_000_000
        channel = row.get("stex_tp") or "KRX"
    con.execute(
        """
        INSERT INTO broker_program_stock_daily
        (source, stock_code, dt, close_price, change_rate, trade_volume,
         sell_qty, buy_qty, net_buy_qty, sell_amt_krw, buy_amt_krw, net_buy_amt_krw,
         market_channel, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source, stock_code, dt, market_channel) DO UPDATE SET
            close_price=excluded.close_price,
            change_rate=excluded.change_rate,
            trade_volume=excluded.trade_volume,
            sell_qty=excluded.sell_qty,
            buy_qty=excluded.buy_qty,
            net_buy_qty=excluded.net_buy_qty,
            sell_amt_krw=excluded.sell_amt_krw,
            buy_amt_krw=excluded.buy_amt_krw,
            net_buy_amt_krw=excluded.net_buy_amt_krw,
            raw_json=excluded.raw_json,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            source,
            stock_code,
            _date_fmt(dt),
            close,
            change_rate,
            volume,
            sell_qty,
            buy_qty,
            net_qty,
            sell_amt,
            buy_amt,
            net_amt,
            channel,
            json.dumps(row, ensure_ascii=False),
        ),
    )


def kis_headers(tr_id: str) -> dict[str, str]:
    token = kis_client.get_token()
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "custtype": "P",
        "tr_id": tr_id,
    }


def collect_kis(
    con: sqlite3.Connection,
    dt: str,
    stocks: list[str],
    market_only: bool,
    save_all_returned: bool = False,
    request_sleep: float = 0.25,
) -> dict[str, int]:
    base = config.KIS_URL.rstrip()
    stats = {"market": 0, "stock": 0}
    for market, code in [("KOSPI", "K"), ("KOSDAQ", "Q")]:
        r = requests.get(
            f"{base}/uapi/domestic-stock/v1/quotations/comp-program-trade-daily",
            headers=kis_headers("FHPPG04600001"),
            params={
                "FID_COND_MRKT_DIV_CODE": "UN",
                "FID_MRKT_CLS_CODE": code,
                "FID_INPUT_DATE_1": dt,
                "FID_INPUT_DATE_2": dt,
            },
            timeout=15,
        )
        data = r.json()
        if data.get("rt_cd") == "0" and data.get("output"):
            upsert_market(con, dt, market, data["output"][0], "kis")
            stats["market"] += 1
        if request_sleep > 0:
            time.sleep(request_sleep)
    if market_only:
        return stats
    for stock_code in stocks:
        r = requests.get(
            f"{base}/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
            headers=kis_headers("FHPPG04650201"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code, "FID_INPUT_DATE_1": dt},
            timeout=15,
        )
        data = r.json()
        if data.get("rt_cd") == "0" and data.get("output"):
            rows = data["output"] if save_all_returned else [next((x for x in data["output"] if x.get("stck_bsop_date") == dt), data["output"][0])]
            for row in rows:
                row_dt = row.get("stck_bsop_date") or dt
                upsert_stock(con, "kis", stock_code, row_dt, row)
                stats["stock"] += 1
        if stats["stock"] and stats["stock"] % 1000 == 0:
            con.commit()
            print(f"    KIS stock rows saved={stats['stock']} latest={stock_code}", flush=True)
        if request_sleep > 0:
            time.sleep(request_sleep)
    return stats


def collect_kiwoom(
    con: sqlite3.Connection,
    dt: str,
    stocks: list[str],
    market_only: bool,
    save_all_returned: bool = False,
    request_sleep: float = 0.25,
) -> dict[str, int]:
    kw = KiwoomCollector()
    if not kw.ensure_token():
        raise RuntimeError("Kiwoom token issue failed")
    stats = {"market": 0, "stock": 0}
    for market, mrkt_tp in [("KOSPI", "P001_AL01"), ("KOSDAQ", "P101_AL02")]:
        r = requests.post(
            f"{kw.base_url}/api/dostk/mrkcond",
            headers=kw._auth_headers("ka90010"),
            json={"date": dt, "amt_qty_tp": "1", "mrkt_tp": mrkt_tp, "min_tic_tp": "1", "stex_tp": "3"},
            timeout=15,
        )
        data = r.json()
        if data.get("return_code") == 0 and data.get("prm_trde_trnsn"):
            row = data["prm_trde_trnsn"][0]
            upsert_market(con, dt, market, row, "kiwoom")
            stats["market"] += 1
        if request_sleep > 0:
            time.sleep(request_sleep)
    if market_only:
        return stats
    for stock_code in stocks:
        r = requests.post(
            f"{kw.base_url}/api/dostk/mrkcond",
            headers=kw._auth_headers("ka90013"),
            json={"amt_qty_tp": "1", "stk_cd": stock_code, "date": dt},
            timeout=15,
        )
        data = r.json()
        if data.get("return_code") == 0 and data.get("stk_daly_prm_trde_trnsn"):
            rows = (
                data["stk_daly_prm_trde_trnsn"]
                if save_all_returned
                else [next((x for x in data["stk_daly_prm_trde_trnsn"] if x.get("dt") == dt), data["stk_daly_prm_trde_trnsn"][0])]
            )
            for row in rows:
                row_dt = row.get("dt") or dt
                upsert_stock(con, "kiwoom", stock_code, row_dt, row)
                stats["stock"] += 1
        if stats["stock"] and stats["stock"] % 1000 == 0:
            con.commit()
            print(f"    KIWOOM stock rows saved={stats['stock']} latest={stock_code}", flush=True)
        if request_sleep > 0:
            time.sleep(request_sleep)
    return stats


def collect_range(
    con: sqlite3.Connection,
    dates: list[str],
    stocks: list[str],
    source: str,
    market_only: bool,
    skip_existing: bool,
    sleep_sec: float,
    commit_every: int,
    save_all_returned: bool,
) -> dict[str, int]:
    total = {"market": 0, "stock": 0, "errors": 0, "skipped": 0}
    market_done_by_source = {
        src: existing_market_dates(con, src)
        for src in ("kis", "kiwoom")
        if source in {src, "both"} and skip_existing
    }
    stock_done_by_source = {
        src: existing_stock_dates(con, src)
        for src in ("kis", "kiwoom")
        if source in {src, "both"} and skip_existing and not market_only
    }

    for idx, dt in enumerate(dates, 1):
        day_stocks = stocks
        if skip_existing and not market_only:
            # Source-specific filtering happens inside each source branch below.
            day_stocks = stocks
        print(f"[{idx}/{len(dates)}] {dt} start stocks={0 if market_only else len(day_stocks)} source={source}", flush=True)
        try:
            if source in {"kis", "both"}:
                kis_stocks = day_stocks
                if skip_existing and not market_only:
                    done = stock_done_by_source.get("kis", set())
                    kis_stocks = [code for code in day_stocks if (code, dt) not in done]
                    total["skipped"] += len(day_stocks) - len(kis_stocks)
                market_done = market_done_by_source.get("kis", set())
                market_missing = (dt, "KOSPI") not in market_done or (dt, "KOSDAQ") not in market_done
                if not skip_existing or market_missing or kis_stocks:
                    stats = collect_kis(con, dt, kis_stocks, market_only, save_all_returned, sleep_sec)
                    print(f"  KIS {stats}", flush=True)
                    total["market"] += stats["market"]
                    total["stock"] += stats["stock"]
                    con.commit()

            if source in {"kiwoom", "both"}:
                kiwoom_stocks = day_stocks
                if skip_existing and not market_only:
                    done = stock_done_by_source.get("kiwoom", set())
                    kiwoom_stocks = [code for code in day_stocks if (code, dt) not in done]
                    total["skipped"] += len(day_stocks) - len(kiwoom_stocks)
                market_done = market_done_by_source.get("kiwoom", set())
                market_missing = (dt, "KOSPI") not in market_done or (dt, "KOSDAQ") not in market_done
                if not skip_existing or market_missing or kiwoom_stocks:
                    stats = collect_kiwoom(con, dt, kiwoom_stocks, market_only, save_all_returned, sleep_sec)
                    print(f"  KIWOOM {stats}", flush=True)
                    total["market"] += stats["market"]
                    total["stock"] += stats["stock"]
                    con.commit()
        except Exception as exc:
            total["errors"] += 1
            con.rollback()
            print(f"  ERROR {dt}: {type(exc).__name__}: {exc}", flush=True)

        if commit_every and idx % commit_every == 0:
            con.commit()
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return total


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYYMMDD")
    p.add_argument("--start", help="YYYYMMDD")
    p.add_argument("--end", help="YYYYMMDD")
    p.add_argument("--stocks", default="", help="Comma-separated stock codes")
    p.add_argument("--all-stocks", action="store_true", help="Load all listed stocks from stock_universe")
    p.add_argument("--limit-stocks", type=int, default=0, help="Limit stock universe for staged backfill")
    p.add_argument("--source", choices=["kis", "kiwoom", "both"], default="both")
    p.add_argument("--market-only", action="store_true")
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--sleep", type=float, default=0.0)
    p.add_argument("--commit-every", type=int, default=20)
    p.add_argument("--save-all-returned", action="store_true", help="For stock endpoints, store every daily row returned by the broker.")
    args = p.parse_args()

    with stock_db_write_lock("collect_broker_program_trading", timeout=600) as acquired:
        if not acquired:
            raise SystemExit("stock.db writer lock timeout")
        con = _conn()
        ensure_tables(con)
        try:
            stocks = [x.strip().zfill(6) for x in args.stocks.split(",") if x.strip()]
            if args.all_stocks:
                stocks = load_stock_codes(con, args.limit_stocks or None)
            if args.start or args.end:
                if not args.start:
                    raise SystemExit("--start is required when using --end/range mode")
                end = args.end or date.today().strftime("%Y%m%d")
                dates = iter_weekdays(args.start, end)
                total = collect_range(
                    con,
                    dates,
                    stocks,
                    args.source,
                    args.market_only,
                    args.skip_existing,
                    args.sleep,
                    args.commit_every,
                    args.save_all_returned,
                )
                print("TOTAL", total)
            else:
                if not args.date:
                    raise SystemExit("--date or --start is required")
                if args.source in {"kis", "both"}:
                    print("KIS", collect_kis(con, args.date, stocks, args.market_only, args.save_all_returned, args.sleep))
                    con.commit()
                if args.source in {"kiwoom", "both"}:
                    print("KIWOOM", collect_kiwoom(con, args.date, stocks, args.market_only, args.save_all_returned, args.sleep))
                    con.commit()
        finally:
            con.close()


if __name__ == "__main__":
    main()
