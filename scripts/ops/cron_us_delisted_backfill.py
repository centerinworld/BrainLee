#!/usr/bin/env python3
"""launchd(com.stock-dashboard.cron.us_delisted_backfill)로 주기 실행되는
미국 상장폐지/구티커 가격 백필. Tiingo 무료 티어 시간당 호출 한도 안에서
한 번에 일부만 처리하고, 남은 대상은 DB의 실제 결측 여부로 다시 판단한다.

FastAPI 프로세스와 완전히 분리된 독립 실행(다른 crontab류 잡과 동일 패턴).
BACKFILL_STATE는 실행마다 갱신되는 유일한 상태 저장소 — 완료된 티커, 재시도
불가로 판정된 티커를 기억해 매 실행마다 API 호출을 낭비하지 않는다.
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

from db_compat import connect_primary_db
from scripts.backfill_us_delisted_prices import apply_rows, fetch_ticker

STATE_PATH = ROOT / "research_outputs" / "us_delisted_backfill_state.json"
LOG_PATH = ROOT / "logs" / "us_delisted_backfill_cron.log"
BATCH_SIZE = 40
START_DATE = "2000-01-01"
END_DATE = "2026-09-25"

# 2026-09-27 결측 감사 기준 — S&P500 PIT(39) + Nasdaq-100 PIT(89) 합집합 중
# us_price_history에 해당 티커 행이 0건인 93개. 최근 데이터가 이미 있는데
# PIT에서 missing 처리된 24개(KHC/KLAC/MNST/PEP 등, 일부는 티커 재사용 의심)는
# 별도 검토 대상이라 이 목록에서 의도적으로 제외했다.
TARGET_TICKERS = """ABMD AEOS ALTR ALXN AMLN ANSS APCC APOL ATVI BEAS BMC BMET BRCM CA
CDWC CELG CEPH CERN CKFR CMA CTLT CTRA CTRP CTRX CTXS DAY DFS DISCA DISCK DISH DRE DTV
ESRX FBHS FLIR FMCN FRC FWLT GMCR GPS HANS HES HOLX IACI IPG JNPR JOYG K KFT KRFT LEAP
LLTC LMCA LMCK LVLT MRO MXIM MYL NDOI NIHD NLSN NLTI NUAN PBCT PDCO PEAK PETM PPDI PXD
QRTEA RIMM SEE SEPR SGEN SHPG SIAL SIVB SPLK SRCL STRZA TLAB TWTR UAUA VIAB VMED VMRK
WBA WCRX WFMI WRK XLNX XMSR YHOO""".split()

# 2026-09-27 dry-run에서 429가 아닌 확정 실패(404 또는 유효 조정가 행 없음)로
# 이미 확인된 티커 — 재시도해도 결과가 바뀌지 않으므로 매 실행 API 낭비 방지.
SEED_SKIP = {
    "AEOS": "tiingo_404_20260927", "FRC": "tiingo_404_20260927",
    "HANS": "tiingo_404_20260927", "JOYG": "tiingo_404_20260927",
    "AMLN": "no_valid_rows_20260927", "APCC": "no_valid_rows_20260927",
    "BEAS": "no_valid_rows_20260927", "BMC": "no_valid_rows_20260927",
    "BMET": "no_valid_rows_20260927", "CDWC": "no_valid_rows_20260927",
    "CEPH": "no_valid_rows_20260927", "CKFR": "no_valid_rows_20260927",
    "FBHS": "no_valid_rows_20260927", "FMCN": "no_valid_rows_20260927",
    "GPS": "no_valid_rows_20260927", "KFT": "no_valid_rows_20260927",
}


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
        for k, v in SEED_SKIP.items():
            state["skip_permanent"].setdefault(k, v)
        return state
    return {"done": [], "skip_permanent": dict(SEED_SKIP)}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True))


def still_missing_in_db(conn, tickers: list[str]) -> list[str]:
    missing = []
    for t in tickers:
        row = conn.execute(
            "SELECT COUNT(*) FROM us_price_history WHERE ticker=?", (t,)
        ).fetchone()
        if row[0] == 0:
            missing.append(t)
    return missing


def main() -> int:
    token = os.getenv("TIINGO_API_KEY", "").strip()
    if not token:
        log("TIINGO_API_KEY 없음 — 중단")
        return 1

    state = load_state()
    done_set = set(state["done"])
    skip_set = set(state["skip_permanent"])
    candidates = [t for t in TARGET_TICKERS if t not in done_set and t not in skip_set]

    conn = connect_primary_db(readonly=True, timeout=60)
    try:
        remaining = still_missing_in_db(conn, candidates)
    finally:
        conn.close()

    # DB에 이미 채워진(다른 경로로) 티커는 done으로 승격해 상태를 정리한다.
    filled_elsewhere = [t for t in candidates if t not in remaining]
    if filled_elsewhere:
        state["done"].extend(filled_elsewhere)
        log(f"DB 확인 결과 이미 채워짐(다른 경로) — done 처리: {filled_elsewhere}")

    if not remaining:
        total_done = len(set(state["done"]))
        total_skip = len(state["skip_permanent"])
        log(
            f"완료 — 목표 {len(TARGET_TICKERS)}개 중 처리할 결측 없음 "
            f"(done={total_done}, skip={total_skip}). "
            f"launchd 잡 com.stock-dashboard.cron.us_delisted_backfill 은 "
            f"제거해도 됨(scripts/ops/unload_us_delisted_backfill.sh 참고 없으면 수동 launchctl bootout)."
        )
        save_state(state)
        return 0

    batch = remaining[:BATCH_SIZE]
    log(f"이번 배치 시도: {len(batch)}개 (전체 잔여 {len(remaining)}개) — {batch}")

    rows_by_ticker: dict[str, list[tuple]] = {}
    hit_hourly_cap = False
    for ticker in batch:
        try:
            rows_by_ticker[ticker] = fetch_ticker(ticker, START_DATE, END_DATE, token)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg:
                log(f"{ticker}: 시간당/버스트 한도 도달 — 이번 배치 중단, 다음 실행에서 재시도")
                hit_hourly_cap = True
                break
            # 영구 스킵은 "이 티커는 절대 못 받는다"가 확정된 경우만: Tiingo가 명시적으로
            # 404(심볼 없음)를 준 경우, 또는 normalize_rows가 유효한 행이 하나도 없다고
            # 확정한 ValueError뿐이다. 그 외(커넥션 에러/타임아웃/5xx 등 일시적 네트워크
            # 문제)는 절대 영구 스킵하지 않는다 — 2026-09-27 12:05 실행에서 우연히 이
            # 순간 네트워크가 끊겨 "Max retries exceeded"가 뜬 39개 정상 티커를 전부
            # 영구 스킵으로 잘못 처리한 사고가 실제로 있었다(재현: 이 조건 없이 40분에
            # 걸쳐 6번 재실행해도 skip_permanent에서 done으로 전혀 옮겨가지 못했음).
            is_confirmed_404 = isinstance(exc, RuntimeError) and "404" in msg
            is_confirmed_no_data = isinstance(exc, ValueError)
            if is_confirmed_404 or is_confirmed_no_data:
                state["skip_permanent"][ticker] = msg[:200]
                log(f"{ticker}: 영구 스킵 처리 — {msg[:200]}")
            else:
                log(f"{ticker}: 일시적 오류로 이번만 건너뜀(영구 스킵 아님, 다음 실행에서 재시도) — {msg[:200]}")

    if rows_by_ticker:
        try:
            result = apply_rows(rows_by_ticker)
            state["done"].extend(rows_by_ticker.keys())
            log(
                f"적용 완료: {sorted(rows_by_ticker.keys())} — "
                f"run_id={result['run_id']}, inserted={result['inserted']}"
            )
        except Exception as exc:
            # apply_rows 실패(예: 2026-09-27 data_fix_log_id_seq 어긋남 사고)로 전체
            # 스크립트가 죽더라도, 이미 확정된 skip_permanent 판정은 반드시 저장한다 —
            # fetch에 성공한 티커는 done에 못 들어가고 다음 실행에서 다시 받아오면 된다.
            save_state(state)
            log(f"적용 실패 — 이번 배치의 done 반영은 안 됐고 skip_permanent만 저장됨: {exc}")
            raise

    save_state(state)
    remaining_after = len(TARGET_TICKERS) - len(set(state["done"])) - len(state["skip_permanent"])
    log(
        f"이번 실행 종료 — 잔여 목표 {remaining_after}개"
        + (" (한도 도달로 중단됨, 다음 실행 계속)" if hit_hourly_cap else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
