#!/usr/bin/env python3
"""launchd(com.stock-dashboard.cron.fill_us_ohlc)로 주기 실행되는 시가/고가/저가
결측 채우기. us_price_history에 종가·거래량은 있는데 open이 NULL인 행이 737개
종목, 49,093행 있음(2026-09-29 감사) — Minervini PIT 백테스트를 2021년부터
돌리다 발견. Trend Template 엔진이 OHLC 중 하나라도 없으면 그 날을 통째로
버려서 "결측 345개 티커"로 잡히던 원인.

2026-09-29 수정: Tiingo와 우리 DB의 종가가 일정 비율로 계속 다른 티커가 많았는데
(예: ACN은 거의 모든 날짜에서 정확히 db/tiingo=1.025058) 이건 오류가 아니라
배당 조정 기준일이 달라서 생기는 정상적인 조정주가 배율 차이다. 그래서
"다르면 스킵"이 아니라 그날의 배율(scale=db_close/tiingo_close)로
open/high/low를 재조정해서 넣는다 — 배율이 SCALE_BAND 밖이면(단순 배당조정으로
설명 안 되는 수준) 진짜 다른 문제(CA/SGEN/SIVB류 신원충돌 가능성)로 보고
채우지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import requests

from db_compat import connect_primary_db

STATE_PATH = ROOT / "research_outputs" / "us_ohlc_fill_state.json"
LOG_PATH = ROOT / "logs" / "us_ohlc_fill_cron.log"
TARGETS_PATH = ROOT / "research_outputs" / "us_ohlc_fill_targets.json"
BATCH_SIZE = 40
SCALE_BAND = (0.5, 2.0)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_state() -> dict:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text())
        state.setdefault("done", [])
        state.setdefault("skip_permanent", {})
        return state
    return {"done": [], "skip_permanent": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True))


def load_targets() -> list[str]:
    if TARGETS_PATH.exists():
        return json.loads(TARGETS_PATH.read_text())
    conn = connect_primary_db(readonly=True, timeout=60)
    try:
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM us_price_history WHERE open IS NULL ORDER BY ticker"
        ).fetchall()]
    finally:
        conn.close()
    TARGETS_PATH.write_text(json.dumps(tickers, indent=2))
    return tickers


def fetch_tiingo(ticker: str, token: str) -> list[dict]:
    response = requests.get(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
        params={"startDate": "2000-01-01", "endDate": "2026-09-25", "resampleFreq": "daily", "token": token},
        headers={"Content-Type": "application/json", "User-Agent": "stock-dashboard/1.0"},
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker}: Tiingo HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"{ticker}: Tiingo response is not a row list")
    return payload


def fill_ticker(conn, ticker: str, tiingo_rows: list[dict]) -> dict:
    """Fill NULL open/high/low for existing rows only, rescaled to match our close."""
    existing = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT date, close FROM us_price_history WHERE ticker=? AND open IS NULL", (ticker,)
        ).fetchall()
    }
    if not existing:
        return {"filled": 0, "mismatched": 0}
    filled = 0
    mismatched = 0
    now = datetime.now(timezone.utc).isoformat()
    for item in tiingo_rows:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        if day not in existing:
            continue
        vals = [item.get(k) for k in ("adjOpen", "adjHigh", "adjLow", "adjClose")]
        if any(v is None for v in vals):
            continue
        opn, high, low, close = (float(v) for v in vals)
        if min(opn, high, low, close) <= 0 or high < max(opn, close) or low > min(opn, close):
            continue
        db_close = float(existing[day])
        if db_close <= 0 or close <= 0:
            continue
        scale = db_close / close
        if not (SCALE_BAND[0] <= scale <= SCALE_BAND[1]):
            mismatched += 1
            continue
        opn, high, low = opn * scale, high * scale, low * scale
        conn.execute(
            "UPDATE us_price_history SET open=?, high=?, low=? WHERE ticker=? AND date=? AND open IS NULL",
            (opn, high, low, ticker, day),
        )
        filled += 1
    if filled:
        conn.execute(
            """INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,
                old_value_summary,new_value_summary,source,run_id)
                VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "us_price_history", ticker, filled, "fill_null_open_high_low_from_tiingo_rescaled",
             "open/high/low were NULL", "filled from Tiingo, rescaled to match existing close basis",
             "tiingo_daily_api", f"fill_ohlc_{ticker}_{now}"),
        )
    return {"filled": filled, "mismatched": mismatched}


def main() -> int:
    token = os.getenv("TIINGO_API_KEY", "").strip()
    if not token:
        log("TIINGO_API_KEY 없음 — 중단")
        return 1

    state = load_state()
    targets = load_targets()
    done_set = set(state["done"])
    skip_set = set(state["skip_permanent"])
    candidates = [t for t in targets if t not in done_set and t not in skip_set]

    conn = connect_primary_db(readonly=True, timeout=60)
    try:
        remaining = [
            t for t in candidates
            if conn.execute("SELECT COUNT(*) FROM us_price_history WHERE ticker=? AND open IS NULL", (t,)).fetchone()[0] > 0
        ]
    finally:
        conn.close()

    filled_elsewhere = [t for t in candidates if t not in remaining]
    if filled_elsewhere:
        state["done"].extend(filled_elsewhere)
        log(f"이미 채워짐(다른 경로) — done 처리: {filled_elsewhere}")

    if not remaining:
        log(f"완료 — 목표 {len(targets)}개 중 처리할 결측 없음 (done={len(set(state['done']))}, skip={len(state['skip_permanent'])})")
        save_state(state)
        return 0

    batch = remaining[:BATCH_SIZE]
    log(f"이번 배치 시도: {len(batch)}개 (전체 잔여 {len(remaining)}개)")

    conn = connect_primary_db(timeout=120)
    hit_hourly_cap = False
    total_filled = 0
    total_mismatched = 0
    try:
        for ticker in batch:
            try:
                rows = fetch_tiingo(ticker, token)
            except Exception as exc:
                msg = str(exc)
                if "429" in msg:
                    log(f"{ticker}: 시간당/버스트 한도 도달 — 이번 배치 중단, 다음 실행에서 재시도")
                    hit_hourly_cap = True
                    break
                if isinstance(exc, ValueError) or "404" in msg:
                    state["skip_permanent"][ticker] = msg[:200]
                    log(f"{ticker}: 영구 스킵 처리 — {msg[:200]}")
                else:
                    log(f"{ticker}: 일시적 오류로 이번만 건너뜀 — {msg[:200]}")
                continue
            try:
                result = fill_ticker(conn, ticker, rows)
            except Exception as exc:
                conn.rollback()
                log(f"{ticker}: DB 업데이트 실패, 롤백됨 — {exc}")
                continue
            conn.commit()
            total_filled += result["filled"]
            total_mismatched += result["mismatched"]
            if result["filled"] > 0 or result["mismatched"] == 0:
                state["done"].append(ticker)
            if result["mismatched"] > 0:
                log(f"{ticker}: {result['filled']}행 채움, {result['mismatched']}행 배율이상으로 스킵(수동 확인 필요)")
            else:
                log(f"{ticker}: {result['filled']}행 채움")
    finally:
        conn.close()

    save_state(state)
    remaining_after = len(targets) - len(set(state["done"])) - len(state["skip_permanent"])
    log(
        f"이번 실행 종료 — 채운 행 {total_filled}개, 배율이상으로 보류 {total_mismatched}개, 잔여 목표 {remaining_after}개"
        + (" (한도 도달로 중단됨, 다음 실행 계속)" if hit_hourly_cap else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
