"""
scripts/ops/backfill_nasdaq_sector_20260928.py

2026-09-28 사용자 지시("미결로 두지 말고 100% 완결로") — us_stock_meta에서 index_name='NASDAQ'인데
sector가 비어있는 1,626개 티커를 yfinance .info로 채운다. 대부분 SPAC(합병 전 블랭크체크)·우선주/
채권성 종목이라 진짜 GICS 섹터가 없는 경우가 많은데, 이런 것도 yfinance가 실제로 어떻게 분류하는지
(예: SPAC→Financial Services/Shell Companies) 그대로 반영해 "미확인 공백"을 "확인된 값"으로 바꾼다.
yfinance가 끝내 아무 정보도 못 주는 티커(상장폐지·오류)는 sector='UNRESOLVED'로 표시해 향후 구분되게
하고, 사유를 로그 파일에 남긴다 — 조용히 방치하지 않는다.

병렬(ThreadPoolExecutor)로 처리, N개마다 커밋(중간에 중단돼도 진행분 보존), 429 레이트리밋은
지수백오프 재시도. 진행 로그: scratch/backfill_nasdaq_sector_20260928.log (있으면 이어서 진행).

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/backfill_nasdaq_sector_20260928.py
"""
from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from db_compat import connect_primary_db

LOG_PATH = "scratch/backfill_nasdaq_sector_20260928.log"
BATCH_COMMIT = 25
WORKERS = 8
MAX_RETRY = 3

_lock = threading.Lock()
_done_tickers: set[str] = set()


def _load_progress():
    """error 상태는 재시도 대상으로 남겨두고(다음 실행에서 다시 시도), 그 외 결과만 완료 처리."""
    try:
        with open(LOG_PATH) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                t, status = parts[0].strip(), parts[1].strip()
                if t and status != "error":
                    _done_tickers.add(t)
    except FileNotFoundError:
        pass


def _log(ticker, status, detail=""):
    with _lock, open(LOG_PATH, "a") as f:
        f.write(f"{ticker}\t{status}\t{detail}\n")


def _fetch_one(ticker: str):
    for attempt in range(MAX_RETRY):
        try:
            info = yf.Ticker(ticker).get_info()
            if not info or not isinstance(info, dict):
                return ticker, None
            sector = info.get("sector") or None
            industry = info.get("industry") or None
            mcap = info.get("marketCap") or None
            return ticker, {"sector": sector, "industry": industry, "market_cap": mcap}
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                time.sleep(2 ** attempt * 3)
                continue
            return ticker, {"error": str(e)}
    return ticker, {"error": "rate_limited_giveup"}


def main():
    _load_progress()
    conn = connect_primary_db(timeout=30)
    rows = conn.execute(
        "SELECT ticker FROM us_stock_meta WHERE index_name='NASDAQ' AND (sector IS NULL OR sector='') ORDER BY ticker"
    ).fetchall()
    tickers = [r[0] for r in rows if r[0] not in _done_tickers]
    print(f"대상 {len(rows)}개 중 미처리 {len(tickers)}개 시작 (이미 처리됨 {len(_done_tickers)}개)")

    resolved = unresolved = errors = 0
    pending_updates = []

    def flush():
        nonlocal pending_updates
        if not pending_updates:
            return
        for ticker, d in pending_updates:
            conn.execute(
                "UPDATE us_stock_meta SET sector=?, industry=?, market_cap=COALESCE(?, market_cap) WHERE ticker=?",
                (d["sector"], d["industry"], d["market_cap"], ticker),
            )
        conn.commit()
        pending_updates = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        n = 0
        for fut in as_completed(futures):
            ticker, result = fut.result()
            n += 1
            if result is None:
                # yfinance가 아무 정보도 못 줌 — 상장폐지/무효 티커로 판단, "조회 불가"로 명시 표시
                # (NULL/빈문자와 구분 — "확인 안 됨"이 아니라 "확인했는데 없음").
                pending_updates.append((ticker, {"sector": "조회 불가", "industry": None, "market_cap": None}))
                _log(ticker, "no_info", "no_info")
                unresolved += 1
            elif "error" in result:
                _log(ticker, "error", result["error"][:200])
                errors += 1
            else:
                # Yahoo 자체가 GICS 섹터를 안 주는 경우(SPAC 합병전·우선주/채권성 종목 다수) —
                # "Unclassified"로 명시해 향후 "미확인"과 "확인했는데 분류 없음"을 구분.
                sector = result["sector"] or "Unclassified"
                pending_updates.append((ticker, {"sector": sector, "industry": result["industry"], "market_cap": result["market_cap"]}))
                _log(ticker, "resolved" if result["sector"] else "unclassified", f"sector={result['sector']} industry={result['industry']}")
                resolved += 1

            if len(pending_updates) >= BATCH_COMMIT:
                flush()
            if n % 100 == 0:
                print(f"[{n}/{len(tickers)}] resolved={resolved} unresolved={unresolved} errors={errors}")

    flush()
    conn.close()
    print(f"완료: resolved={resolved} unresolved={unresolved} errors={errors}")


if __name__ == "__main__":
    main()
