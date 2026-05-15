from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"
CACHE_PATH = "/Applications/stock_dashboard/scratch/stock_analysis_rs_cache.json"
KST = timezone(timedelta(hours=9))

_cache_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _safe_num(v, d=0.0) -> float:
    try:
        if v is None:
            return d
        return float(v)
    except Exception:
        return d


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100.0


def _is_kr_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return False
    hhmm = now.hour * 100 + now.minute
    return 900 <= hhmm <= 1535


def _load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(payload: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = f"{CACHE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


def _fetch_sector_maps(conn: sqlite3.Connection) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    major = defaultdict(list)
    middle = defaultdict(list)
    rows = conn.execute(
        """
        SELECT stock_code, sector, strategy
        FROM stock_sector_tags
        WHERE source IN ('stockeasy_stock_analysis', 'manual_multi')
        """
    ).fetchall()
    for r in rows:
        code = str(r["stock_code"] or "")
        sector = str(r["sector"] or "").strip()
        strategy = str(r["strategy"] or "").strip()
        if not code or not sector:
            continue
        if strategy == "major":
            if sector not in major[code]:
                major[code].append(sector)
        elif strategy == "middle":
            if sector not in middle[code]:
                middle[code].append(sector)
        else:
            if sector not in middle[code]:
                middle[code].append(sector)
    return dict(major), dict(middle)


def _latest_universe(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        WITH latest AS (
          SELECT stock_code, MAX(base_date) max_dt
          FROM stock_universe
          WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          GROUP BY stock_code
        )
        SELECT u.stock_code, u.stock_name, UPPER(COALESCE(u.market,'')) market,
               COALESCE(u.market_cap,0) market_cap
        FROM stock_universe u
        JOIN latest l ON u.stock_code=l.stock_code AND u.base_date=l.max_dt
        WHERE u.stock_name IS NOT NULL
        """
    ).fetchall()
    return {
        str(r["stock_code"]): {
            "stock_name": str(r["stock_name"] or ""),
            "market": "KOSDAQ" if "KOSDAQ" in str(r["market"]) or "코스닥" in str(r["market"]) else "KOSPI",
            "market_cap": _safe_num(r["market_cap"]),
        }
        for r in rows
    }


def _fetch_recent_prices(conn: sqlite3.Connection, code_meta: dict[str, dict], n: int = 260) -> dict[str, list[sqlite3.Row]]:
    if not code_meta:
        return {}
    placeholders = ",".join("?" * len(code_meta))
    rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT p.stock_code, p.date, p.close, p.volume, COALESCE(p.trade_amount,0) trade_amount,
                 ROW_NUMBER() OVER (PARTITION BY p.stock_code ORDER BY p.date DESC) rn
          FROM price_history p
          WHERE p.stock_code IN ({placeholders})
            AND p.close > 0
        )
        SELECT stock_code, date, close, volume, trade_amount
        FROM ranked
        WHERE rn <= ?
        ORDER BY stock_code, date DESC
        """,
        list(code_meta.keys()) + [n],
    ).fetchall()
    by_code: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_code[str(r["stock_code"])].append(r)
    return by_code


def _fetch_bench_series(conn: sqlite3.Connection) -> dict[str, list[float]]:
    rows = conn.execute(
        """
        WITH ranked AS (
          SELECT stock_code, date, close,
                 ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
          FROM price_history
          WHERE stock_code IN ('KOSPI','KOSDAQ','^KS11','^KQ11')
            AND close > 0
        )
        SELECT stock_code, close FROM ranked WHERE rn <= 260 ORDER BY stock_code, rn
        """
    ).fetchall()
    m: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        m[str(r["stock_code"])].append(_safe_num(r["close"]))
    return {
        "KOSPI": m.get("KOSPI") or m.get("^KS11") or [],
        "KOSDAQ": m.get("KOSDAQ") or m.get("^KQ11") or [],
    }


def _compute_rs_dashboard() -> dict:
    conn = _conn()
    try:
        code_meta = _latest_universe(conn)
        major_map, middle_map = _fetch_sector_maps(conn)
        by_code = _fetch_recent_prices(conn, code_meta, 260)
        bench = _fetch_bench_series(conn)

        def bench_ret(market: str, n: int) -> float:
            arr = bench["KOSPI"] if market == "KOSPI" else bench["KOSDAQ"]
            if len(arr) <= n or arr[n] == 0:
                return 0.0
            return (arr[0] - arr[n]) / arr[n] * 100.0

        rs_list = []
        major_bucket = defaultdict(list)
        middle_bucket = defaultdict(list)

        for code, rows in by_code.items():
            if len(rows) < 130:
                continue
            meta = code_meta.get(code)
            if not meta:
                continue

            closes = [_safe_num(r["close"]) for r in rows]
            vols = [_safe_num(r["volume"]) for r in rows]
            tvals = [_safe_num(r["trade_amount"]) for r in rows]
            for i in range(min(len(closes), len(tvals))):
                if tvals[i] <= 0:
                    tvals[i] = vols[i] * closes[i]

            r1 = _pct(closes[0], closes[20]) if len(closes) > 20 else None
            r3 = _pct(closes[0], closes[60]) if len(closes) > 60 else None
            r6 = _pct(closes[0], closes[120]) if len(closes) > 120 else None
            if r3 is None:
                continue

            b1 = bench_ret(meta["market"], 20)
            b3 = bench_ret(meta["market"], 60)
            b6 = bench_ret(meta["market"], 120)

            rs_1m = (r1 or 0.0) - b1
            rs_3m = r3 - b3
            rs_6m = (r6 or 0.0) - b6
            rs = round(50 + rs_3m, 2)
            mmt = round(rs_1m * 0.25 + rs_3m * 0.5 + rs_6m * 0.25, 2)

            current = closes[0]
            prev = closes[1] if len(closes) > 1 else closes[0]
            change_rate = _pct(current, prev) or 0.0
            major_list = major_map.get(code) or ["미분류"]
            middle_list = middle_map.get(code) or []

            row = {
                "stock_code": code,
                "stock_name": meta["stock_name"],
                "major_name": major_list[0],
                "major_names": major_list,
                "mid_name": middle_list[0] if middle_list else "",
                "mid_names": middle_list,
                "market": meta["market"],
                "rs": rs,
                "rs_1m": round(rs_1m, 2),
                "rs_3m": round(rs_3m, 2),
                "rs_6m": round(rs_6m, 2),
                "mmt": mmt,
                "market_cap": round(meta["market_cap"] / 100_000_000.0, 2) if meta["market_cap"] else 0.0,
                "current_price": round(current, 2),
                "change_rate": round(change_rate, 2),
            }
            rs_list.append(row)
            for s in major_list:
                major_bucket[s].append(row["rs"])
            for s in middle_list:
                middle_bucket[s].append(row["rs"])

        sector_rs = [
            {"sector": k, "avg_rs": round(sum(v) / len(v), 2), "count": len(v)}
            for k, v in major_bucket.items() if v
        ]
        sector_rs.sort(key=lambda x: (x["avg_rs"], x["count"]), reverse=True)

        sector_rs_mid = [
            {"sector": k, "avg_rs": round(sum(v) / len(v), 2), "count": len(v)}
            for k, v in middle_bucket.items() if v
        ]
        sector_rs_mid.sort(key=lambda x: (x["avg_rs"], x["count"]), reverse=True)

        rs_list.sort(key=lambda x: (x["rs"], x["mmt"]), reverse=True)

        kospi_rs = round(50 + bench_ret("KOSPI", 60), 2)
        kosdaq_rs = round(50 + bench_ret("KOSDAQ", 60), 2)

        latest_date = conn.execute("SELECT MAX(date) FROM price_history WHERE close > 0").fetchone()
        return {
            "success": True,
            "data": {
                "rs_list": rs_list,
                "sector_rs": sector_rs,
                "sector_rs_mid": sector_rs_mid,
                "benchmarks": {
                    "kospi": {
                        "label": "코스피",
                        "rs": kospi_rs,
                        "rs_1m": round(bench_ret("KOSPI", 20), 2),
                        "rs_3m": round(bench_ret("KOSPI", 60), 2),
                        "rs_6m": round(bench_ret("KOSPI", 120), 2),
                    },
                    "kosdaq": {
                        "label": "코스닥",
                        "rs": kosdaq_rs,
                        "rs_1m": round(bench_ret("KOSDAQ", 20), 2),
                        "rs_3m": round(bench_ret("KOSDAQ", 60), 2),
                        "rs_6m": round(bench_ret("KOSDAQ", 120), 2),
                    },
                },
                "metadata": {
                    "target_date": latest_date[0] if latest_date else None,
                    "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "count": len(rs_list),
                },
            },
        }
    finally:
        conn.close()


def _compute_52w_high_dashboard() -> dict:
    conn = _conn()
    try:
        code_meta = _latest_universe(conn)
        by_code = _fetch_recent_prices(conn, code_meta, 260)
        major_map, middle_map = _fetch_sector_maps(conn)

        out = []
        for code, rows in by_code.items():
            if len(rows) < 30:
                continue
            meta = code_meta.get(code)
            closes = [_safe_num(r["close"]) for r in rows]
            vols = [_safe_num(r["volume"]) for r in rows]
            if not meta or not closes:
                continue
            current = closes[0]
            prev = closes[1] if len(closes) > 1 else closes[0]
            high52 = max(closes)
            low52 = min(closes)
            if high52 <= 0:
                continue
            high_gap_pct = _pct(current, high52) or 0.0
            is_new_high = abs(current - high52) < 1e-9
            near_high = current >= high52 * 0.98
            avg20 = sum(vols[1:21]) / 20.0 if len(vols) > 20 else (sum(vols[1:]) / max(len(vols[1:]), 1))
            vol_ratio = (vols[0] / avg20) if avg20 > 0 else 0.0
            change_rate = _pct(current, prev) or 0.0
            breakout_score = (2.5 if is_new_high else (1.2 if near_high else 0.0)) + (0.7 if vol_ratio >= 1.5 else 0.0) + (0.4 if change_rate > 0 else 0.0)

            out.append({
                "stock_code": code,
                "stock_name": meta["stock_name"],
                "market": meta["market"],
                "major_names": major_map.get(code) or ["미분류"],
                "mid_names": middle_map.get(code) or [],
                "current_price": round(current, 2),
                "high52_price": round(high52, 2),
                "low52_price": round(low52, 2),
                "high_gap_pct": round(high_gap_pct, 2),
                "change_rate": round(change_rate, 2),
                "vol_ratio": round(vol_ratio, 2),
                "is_new_high": is_new_high,
                "is_near_high": near_high,
                "breakout_score": round(breakout_score, 2),
                "market_cap": round(meta["market_cap"] / 100_000_000.0, 2) if meta["market_cap"] else 0.0,
            })

        out.sort(key=lambda x: (x["is_new_high"], x["is_near_high"], x["breakout_score"], x["high_gap_pct"]), reverse=True)
        latest_date = conn.execute("SELECT MAX(date) FROM price_history WHERE close > 0").fetchone()

        return {
            "success": True,
            "data": {
                "high52_list": out,
                "metadata": {
                    "target_date": latest_date[0] if latest_date else None,
                    "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "count": len(out),
                    "new_high_count": sum(1 for x in out if x["is_new_high"]),
                    "near_high_count": sum(1 for x in out if x["is_near_high"]),
                },
            },
        }
    finally:
        conn.close()


def _cached_or_compute(key: str, compute_fn, open_ttl_sec: int = 600, closed_ttl_sec: int = 86400) -> dict:
    now = datetime.now(KST)
    is_open = _is_kr_market_open(now)
    ttl = open_ttl_sec if is_open else closed_ttl_sec
    with _cache_lock:
        cache = _load_cache()
        entry = cache.get(key)
        if entry:
            try:
                ts = datetime.fromisoformat(entry.get("generated_at"))
                if (now - ts).total_seconds() <= ttl and entry.get("payload"):
                    return entry["payload"]
            except Exception:
                pass

        payload = compute_fn()
        cache[key] = {
            "generated_at": now.isoformat(timespec="seconds"),
            "ttl_sec": ttl,
            "mode": "open_10m" if is_open else "closed_cached",
            "payload": payload,
        }
        _save_cache(cache)
        return payload


@router.get("/dashboard-data")
def get_rs_dashboard_data():
    try:
        return _cached_or_compute("dashboard_data", _compute_rs_dashboard, 600, 86400)
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"rs_list": [], "sector_rs": [], "sector_rs_mid": [], "benchmarks": {}}}


@router.get("/high52-data")
def get_52w_high_data():
    try:
        return _cached_or_compute("high52_data", _compute_52w_high_dashboard, 600, 86400)
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"high52_list": [], "metadata": {}}}
