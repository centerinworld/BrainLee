from __future__ import annotations

import json
import os
import sqlite3
import threading
import statistics
import base64
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query
import requests

router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"
CACHE_PATH = "/Applications/stock_dashboard/scratch/stock_analysis_rs_cache.json"
KST = timezone(timedelta(hours=9))
STOCKEASY_API_BASE = "https://stockeasy.intellio.kr/stockdata"
OPEN_SLOT_MINUTES = 10
OPEN_TTL_SEC = 600
CLOSED_TTL_SEC = 86400

# Separate locks: one for file I/O only (never held during compute), one for computing status
_cache_file_lock = threading.Lock()
_compute_events: dict[str, threading.Event] = {}
_compute_events_lock = threading.Lock()


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


def _percentile_scores(values: list[float]) -> list[int]:
    """
    값 배열을 0~100 퍼센타일 점수로 변환(동점은 평균 순위).
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50]
    pairs = sorted(enumerate(values), key=lambda x: x[1])
    out = [50] * n
    i = 0
    while i < n:
        j = i
        v = pairs[i][1]
        while j + 1 < n and pairs[j + 1][1] == v:
            j += 1
        # rank is 1-based; use average rank for ties
        avg_rank = (i + 1 + j + 1) / 2.0
        score = int(round((avg_rank - 1) / (n - 1) * 100))
        for k in range(i, j + 1):
            out[pairs[k][0]] = score
        i = j + 1
    return out


def _robust_period_return(closes_desc: list[float], n: int, jump_threshold: float = 0.35, cap: float = 0.30) -> float | None:
    """
    액면분할/병합 등 비연속 가격점프로 왜곡되는 수익률 완화.
    - 평시: raw period return 사용
    - 급격한 일간 점프 감지 시: 일간 수익률 cap 기반 누적수익률 사용
    closes_desc: 최신 -> 과거 순서
    """
    if len(closes_desc) <= n:
        return None
    a = closes_desc[0]
    b = closes_desc[n]
    if b == 0:
        return None
    raw = (a - b) / b
    # detect discontinuity
    jumps = []
    for i in range(n):
        prev = closes_desc[i + 1]
        cur = closes_desc[i]
        if prev and prev > 0:
            jumps.append(cur / prev - 1.0)
    if not jumps:
        return raw * 100.0
    if max(abs(x) for x in jumps) < jump_threshold and abs(raw) < 1.5:
        return raw * 100.0
    # split/역분할 또는 기준일 불연속 가능성이 있으면 구간 median anchor로 완화
    cur_win = [x for x in closes_desc[0:min(5, len(closes_desc))] if x and x > 0]
    past_win = [x for x in closes_desc[n:min(n + 5, len(closes_desc))] if x and x > 0]
    if cur_win and past_win:
        a_ref = statistics.median(cur_win)
        b_ref = statistics.median(past_win)
        if b_ref > 0:
            robust = (a_ref - b_ref) / b_ref
            return robust * 100.0
    # fallback: winsorized daily cumulative
    cum = 1.0
    for r in jumps:
        rr = max(-cap, min(cap, r))
        cum *= (1.0 + rr)
    return (cum - 1.0) * 100.0


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


def _current_refresh_slot(now: datetime, is_open: bool) -> str:
    if is_open:
        slot_min = (now.minute // OPEN_SLOT_MINUTES) * OPEN_SLOT_MINUTES
        return f"open:{now.date().isoformat()}:{now.hour:02d}:{slot_min:02d}"
    return f"closed:{now.date().isoformat()}"


def _check_cache_entry(entry: dict | None, now: datetime, ttl: float, slot_id: str | None = None) -> dict | None:
    if not entry:
        return None
    try:
        # 슬롯이 같은 경우는 TTL보다 우선적으로 캐시 사용 (장중 특정 시각 단위 계산)
        if slot_id and entry.get("slot_id") == slot_id and entry.get("payload"):
            return entry["payload"]
        ts = datetime.fromisoformat(entry.get("generated_at"))
        if (now - ts).total_seconds() <= ttl and entry.get("payload"):
            return entry["payload"]
    except Exception:
        pass
    return None


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


def _base36(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    out = ""
    while n:
        n, rem = divmod(n, 36)
        out = chars[rem] + out
    return out


def _stockeasy_app_token() -> str:
    bucket = int(time.time() // 30)
    sig = ((0x45D9F3B * bucket) ^ 0xDEADBEEF) & 0xFFFFFFFF
    return base64.b64encode(f"{bucket}.{_base36(sig)}".encode("utf-8")).decode("ascii")


def _fetch_stockeasy_sector_rs(sector_level: str = "major", top_n: int = 10) -> list[dict]:
    try:
        url = f"{STOCKEASY_API_BASE}/api/v1/rs/sectors?top_n={top_n}&sector_level={sector_level}"
        r = requests.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
                "X-App-Token": _stockeasy_app_token(),
            },
            timeout=3,
        )
        if not r.ok:
            return []
        j = r.json()
        if j.get("status") != "success":
            return []
        out = []
        for it in j.get("data") or []:
            dist = it.get("distribution") or {}
            cnt = int((dist.get("strong") or 0) + (dist.get("neutral") or 0) + (dist.get("weak") or 0))
            out.append({
                "sector": str(it.get("sector_name") or "").strip(),
                "avg_rs": round(float(it.get("weighted_avg_rs") or 0.0), 2),
                "count": cnt,
            })
        return [x for x in out if x.get("sector")]
    except Exception:
        return []


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
        raw_1m: list[float] = []
        raw_3m: list[float] = []
        raw_6m: list[float] = []
        raw_12m: list[float] = []
        major_bucket = defaultdict(list)
        middle_bucket = defaultdict(list)

        for code, rows in by_code.items():
            if len(rows) < 250:
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

            r1 = _robust_period_return(closes, 20) if len(closes) > 20 else None
            r3 = _robust_period_return(closes, 60) if len(closes) > 60 else None
            r6 = _robust_period_return(closes, 120) if len(closes) > 120 else None
            r12 = _robust_period_return(closes, 240) if len(closes) > 240 else None
            if r3 is None or r12 is None:
                continue

            b1 = bench_ret(meta["market"], 20)
            b3 = bench_ret(meta["market"], 60)
            b6 = bench_ret(meta["market"], 120)
            b12 = bench_ret(meta["market"], 240)

            rs_1m_raw = (r1 or 0.0) - b1
            rs_3m_raw = r3 - b3
            rs_6m_raw = (r6 or 0.0) - b6
            rs_12m_raw = r12 - b12

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
                "rs": 0,
                "rs_1m": 0,
                "rs_3m": 0,
                "rs_6m": 0,
                "rs_12m": 0,
                "mmt": 0,
                "rs_1m_raw": round(rs_1m_raw, 4),
                "rs_3m_raw": round(rs_3m_raw, 4),
                "rs_6m_raw": round(rs_6m_raw, 4),
                "rs_12m_raw": round(rs_12m_raw, 4),
                "market_cap": round(meta["market_cap"] / 100_000_000.0, 2) if meta["market_cap"] else 0.0,
                "current_price": round(current, 2),
                "change_rate": round(change_rate, 2),
            }
            rs_list.append(row)
            raw_1m.append(rs_1m_raw)
            raw_3m.append(rs_3m_raw)
            raw_6m.append(rs_6m_raw)
            raw_12m.append(rs_12m_raw)

        # stockeasy 형태로 RS를 0~100 백분위 점수로 정규화
        p1 = _percentile_scores(raw_1m)
        p3 = _percentile_scores(raw_3m)
        p6 = _percentile_scores(raw_6m)
        p12 = _percentile_scores(raw_12m)
        mmt_raw_list: list[float] = []
        for i, row in enumerate(rs_list):
            row["rs_1m"] = p1[i]
            row["rs_3m"] = p3[i]
            row["rs_6m"] = p6[i]
            row["rs_12m"] = p12[i]
            mmt_raw = p1[i] * 0.25 + p3[i] * 0.5 + p6[i] * 0.25
            row["mmt_raw"] = mmt_raw
            mmt_raw_list.append(mmt_raw)

        pm = _percentile_scores(mmt_raw_list)
        for i, row in enumerate(rs_list):
            row["mmt"] = pm[i]
            row["rs"] = p12[i]  # 12M 탭 기본 RS는 12M 상대강도 점수
            mcap = max(float(row.get("market_cap") or 0.0), 1.0)
            for s in row.get("major_names") or ["미분류"]:
                major_bucket[s].append((row["rs"], mcap))
            for s in row.get("mid_names") or []:
                middle_bucket[s].append((row["rs"], mcap))

        sector_rs = []
        for k, pairs in major_bucket.items():
            if not pairs:
                continue
            wsum = sum((w for _, w in pairs))
            weighted = (sum((rs * w for rs, w in pairs)) / wsum) if wsum > 0 else (sum(rs for rs, _ in pairs) / len(pairs))
            sector_rs.append({"sector": k, "avg_rs": round(weighted, 2), "count": len(pairs)})
        sector_rs.sort(key=lambda x: (x["avg_rs"], x["count"]), reverse=True)

        sector_rs_mid = []
        for k, pairs in middle_bucket.items():
            if not pairs:
                continue
            wsum = sum((w for _, w in pairs))
            weighted = (sum((rs * w for rs, w in pairs)) / wsum) if wsum > 0 else (sum(rs for rs, _ in pairs) / len(pairs))
            sector_rs_mid.append({"sector": k, "avg_rs": round(weighted, 2), "count": len(pairs)})
        sector_rs_mid.sort(key=lambda x: (x["avg_rs"], x["count"]), reverse=True)

        # StockEasy 종합 RS 로직 역추적:
        # 섹터 RS는 StockEasy API의 weighted_avg_rs를 우선 사용해 화면 정합성을 맞춘다.
        sector_rs_source_major = "local"
        sector_rs_source_mid = "local"
        se_major = _fetch_stockeasy_sector_rs("major", top_n=10)
        if se_major:
            sector_rs = se_major
            sector_rs_source_major = "stockeasy"
        se_mid = _fetch_stockeasy_sector_rs("middle", top_n=20)
        if se_mid:
            sector_rs_mid = se_mid
            sector_rs_source_mid = "stockeasy"

        rs_list.sort(key=lambda x: (x["rs"], x["mmt"]), reverse=True)

        # 지수 RS도 동일 기준(종목군 내 상대 퍼센타일)로 표시
        kospi_rs3 = bench_ret("KOSPI", 60)
        kosdaq_rs3 = bench_ret("KOSDAQ", 60)
        kospi_rs = _percentile_scores(raw_3m + [kospi_rs3])[-1] if raw_3m else 50
        kosdaq_rs = _percentile_scores(raw_3m + [kosdaq_rs3])[-1] if raw_3m else 50
        kospi_cur = bench["KOSPI"][0] if bench.get("KOSPI") else None
        kospi_prev = bench["KOSPI"][1] if bench.get("KOSPI") and len(bench["KOSPI"]) > 1 else kospi_cur
        kosdaq_cur = bench["KOSDAQ"][0] if bench.get("KOSDAQ") else None
        kosdaq_prev = bench["KOSDAQ"][1] if bench.get("KOSDAQ") and len(bench["KOSDAQ"]) > 1 else kosdaq_cur

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
                        "current_price": round(_safe_num(kospi_cur), 2) if kospi_cur is not None else None,
                        "change_rate": round(_pct(kospi_cur, kospi_prev) or 0.0, 2) if kospi_cur is not None and kospi_prev is not None else None,
                        "rs": kospi_rs,
                        "rs_1m": round(bench_ret("KOSPI", 20), 2),
                        "rs_3m": round(bench_ret("KOSPI", 60), 2),
                        "rs_6m": round(bench_ret("KOSPI", 120), 2),
                    },
                    "kosdaq": {
                        "label": "코스닥",
                        "current_price": round(_safe_num(kosdaq_cur), 2) if kosdaq_cur is not None else None,
                        "change_rate": round(_pct(kosdaq_cur, kosdaq_prev) or 0.0, 2) if kosdaq_cur is not None and kosdaq_prev is not None else None,
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
                    "sector_rs_source_major": sector_rs_source_major,
                    "sector_rs_source_mid": sector_rs_source_mid,
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


def _cached_or_compute(key: str, compute_fn, open_ttl_sec: int = OPEN_TTL_SEC, closed_ttl_sec: int = CLOSED_TTL_SEC) -> dict:
    """
    Double-check locking: 캐시 미스 시 락 밖에서 compute 수행.
    동시 요청이 와도 락 안에서 직렬화되지 않음.
    두 스레드가 동시에 compute할 수 있으나, 마지막 write가 이기는 방식으로 허용.
    """
    now = datetime.now(KST)
    is_open = _is_kr_market_open(now)
    ttl = open_ttl_sec if is_open else closed_ttl_sec
    slot_id = _current_refresh_slot(now, is_open)

    # 1단계: 락 없이 캐시 조회 (빠른 경로)
    cache = _load_cache()
    hit = _check_cache_entry(cache.get(key), now, ttl, slot_id=slot_id)
    if hit is not None:
        return hit

    # 2단계: 캐시 미스
    # 같은 key에 대해 동시 계산이 발생하면 1개만 계산하고 나머지는 대기 후 캐시 재조회
    leader = False
    event: threading.Event
    with _compute_events_lock:
        event = _compute_events.get(key)
        if event is None:
            event = threading.Event()
            _compute_events[key] = event
            leader = True

    if not leader:
        event.wait(timeout=25)
        cache_after_wait = _load_cache()
        hit_after_wait = _check_cache_entry(cache_after_wait.get(key), now, ttl, slot_id=slot_id)
        if hit_after_wait is not None:
            return hit_after_wait

    payload = compute_fn()

    # 3단계: 파일 쓰기만 락으로 보호 (double-check)
    try:
        with _cache_file_lock:
            cache = _load_cache()
            existing = _check_cache_entry(cache.get(key), now, ttl, slot_id=slot_id)
            if existing is not None:
                # 다른 스레드가 이미 저장했으면 그 결과 반환
                return existing
            cache[key] = {
                "generated_at": now.isoformat(timespec="seconds"),
                "ttl_sec": ttl,
                "mode": f"open_{OPEN_SLOT_MINUTES}m" if is_open else "closed_1d",
                "slot_id": slot_id,
                "payload": payload,
            }
            _save_cache(cache)
        return payload
    finally:
        if leader:
            with _compute_events_lock:
                ev = _compute_events.pop(key, None)
            if ev:
                ev.set()


def _get_cached_list(key: str, data_field: str) -> list:
    """JSON 캐시에서 특정 key의 list를 꺼냄. 없으면 빈 리스트."""
    cache = _load_cache()
    entry = cache.get(key)
    if not entry:
        return []
    payload = entry.get("payload") or {}
    return (payload.get("data") or {}).get(data_field) or []


def _apply_rs_filters(rows: list, q: str, sector: str, cap_min: float, market: str, sector_mode: str) -> list:
    q = q.strip().lower()
    filtered = []
    for r in rows:
        if (r.get("market_cap") or 0) < cap_min:
            continue
        if market != "ALL" and r.get("market") != market:
            continue
        if sector != "전체":
            if sector_mode == "major":
                names = [str(s).strip() for s in (r.get("major_names") or [])]
            else:
                names = [str(s).strip() for s in (r.get("mid_names") or [])]
            if sector not in names:
                continue
        if q:
            if q not in str(r.get("stock_name") or "").lower() and q not in str(r.get("stock_code") or ""):
                continue
        filtered.append(r)
    return filtered


# ── API 엔드포인트 ──────────────────────────────────────────────────────


@router.get("/dashboard-data")
def get_rs_dashboard_data():
    """
    요약 정보만 반환: benchmarks, sector_rs, metadata.
    전체 행은 /dashboard-rows 에서 페이지네이션으로 조회.
    """
    try:
        payload = _cached_or_compute("dashboard_data", _compute_rs_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
        if not payload.get("success"):
            return payload
        data = payload.get("data") or {}
        rs_list = data.get("rs_list") or []
        return {
            "success": True,
            "data": {
                "sector_rs": data.get("sector_rs") or [],
                "sector_rs_mid": data.get("sector_rs_mid") or [],
                "benchmarks": data.get("benchmarks") or {},
                "metadata": {**(data.get("metadata") or {}), "total_count": len(rs_list)},
            },
        }
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"sector_rs": [], "benchmarks": {}, "metadata": {}}}


@router.get("/dashboard-rows")
def get_rs_dashboard_rows(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str = Query("rs"),
    q: str = Query(""),
    sector: str = Query("전체"),
    cap_min: float = Query(0),
    market: str = Query("ALL"),
    sector_mode: str = Query("major"),
):
    """서버 측 필터/정렬/페이지네이션. 캐시된 데이터에서 슬라이스만 반환."""
    try:
        rows = _get_cached_list("dashboard_data", "rs_list")
        if not rows:
            # 캐시 없으면 트리거
            payload = _cached_or_compute("dashboard_data", _compute_rs_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
            rows = (payload.get("data") or {}).get("rs_list") or []

        filtered = _apply_rs_filters(rows, q, sector, cap_min, market, sector_mode)

        valid_sorts = {"rs", "rs_1m", "rs_3m", "rs_6m", "mmt", "market_cap", "change_rate"}
        sort_key = sort if sort in valid_sorts else "rs"
        filtered.sort(key=lambda x: (x.get(sort_key) or 0), reverse=True)

        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "success": True,
            "data": {
                "rows": filtered[start:start + page_size],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        }
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"rows": [], "total": 0, "page": 1, "total_pages": 1}}


@router.get("/high52-data")
def get_52w_high_data():
    """
    요약 정보만 반환: metadata.
    전체 행은 /high52-rows 에서 페이지네이션으로 조회.
    """
    try:
        payload = _cached_or_compute("high52_data", _compute_52w_high_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
        if not payload.get("success"):
            return payload
        data = payload.get("data") or {}
        return {
            "success": True,
            "data": {
                "metadata": data.get("metadata") or {},
            },
        }
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"metadata": {}}}


@router.get("/high52-rows")
def get_high52_rows(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str = Query("score"),
    q: str = Query(""),
    sector: str = Query("전체"),
    cap_min: float = Query(0),
    market: str = Query("ALL"),
    sector_mode: str = Query("major"),
    high_filter: str = Query("all"),  # all | new | near
):
    """서버 측 필터/정렬/페이지네이션. 캐시된 52주 데이터에서 슬라이스만 반환."""
    try:
        rows = _get_cached_list("high52_data", "high52_list")
        if not rows:
            payload = _cached_or_compute("high52_data", _compute_52w_high_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
            rows = (payload.get("data") or {}).get("high52_list") or []

        # 필터
        q_low = q.strip().lower()
        filtered = []
        for r in rows:
            if (r.get("market_cap") or 0) < cap_min:
                continue
            if market != "ALL" and r.get("market") != market:
                continue
            if high_filter == "new" and not r.get("is_new_high"):
                continue
            if high_filter == "near" and not (r.get("is_near_high") or r.get("is_new_high")):
                continue
            if sector != "전체":
                names = [str(s).strip() for s in (
                    (r.get("major_names") or []) if sector_mode == "major" else (r.get("mid_names") or [])
                )]
                if sector not in names:
                    continue
            if q_low:
                if q_low not in str(r.get("stock_name") or "").lower() and q_low not in str(r.get("stock_code") or ""):
                    continue
            filtered.append(r)

        # 정렬
        if sort == "gap":
            filtered.sort(key=lambda x: (x.get("high_gap_pct") or -999), reverse=True)
        elif sort == "vol":
            filtered.sort(key=lambda x: (x.get("vol_ratio") or 0), reverse=True)
        else:
            filtered.sort(
                key=lambda x: (x.get("is_new_high", False), x.get("is_near_high", False), x.get("breakout_score") or 0),
                reverse=True,
            )

        total = len(filtered)
        start = (page - 1) * page_size
        return {
            "success": True,
            "data": {
                "rows": filtered[start:start + page_size],
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": max(1, (total + page_size - 1) // page_size),
            },
        }
    except Exception as e:
        return {"success": False, "reason": str(e), "data": {"rows": [], "total": 0, "page": 1, "total_pages": 1}}


@router.post("/precompute")
def trigger_precompute():
    """스케줄러/수동 트리거용: 캐시를 강제 재계산."""
    try:
        # 기존 캐시 만료 처리: 강제로 재계산하도록 캐시 비움
        with _cache_file_lock:
            _save_cache({})
        rs = _cached_or_compute("dashboard_data", _compute_rs_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
        h52 = _cached_or_compute("high52_data", _compute_52w_high_dashboard, OPEN_TTL_SEC, CLOSED_TTL_SEC)
        rs_count = len((rs.get("data") or {}).get("rs_list") or [])
        h52_count = len((h52.get("data") or {}).get("high52_list") or [])
        return {"success": True, "rs_count": rs_count, "high52_count": h52_count}
    except Exception as e:
        return {"success": False, "reason": str(e)}
