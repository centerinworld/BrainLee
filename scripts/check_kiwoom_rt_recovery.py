#!/usr/bin/env python3
"""키움 장중 실시간 피드 '복구 확인' 체커 — 읽기 전용.

무엇을 판정하나 (2026-09-24 수정 efb85a5 + 2026-09-25 커밋 b8a6fe8 이후)
--------------------------------------------------------------------------
수정은 두 가지를 바꿨다: ①`_job_kiwoom_realtime` 이 `ok=False`·3사이클 연속 0건을
`RuntimeError` 로 승격 → 원장(`collection_job_runs`)에 `failed` 로 남는다 ②`키움실시간스냅샷`
이 `JOB_DATASET_KEYS` 에 묶여 세 저장경로가 계약으로 검증된다. 그래서 "장중 확인"은
**데이터가 돌아왔는가**와 **실패가 조용히 넘어가지 않는가** 두 축을 함께 봐야 한다.

판정 클래스
-----------
  OK          3테이블 모두 당일 행>0 · 원장 `failed` 0건 · 로그에 `ws_saved>0`
  FAIL_SILENT 원장이 `success` 뿐인데 3테이블 행 0 → **침묵 실패 재발(수정 무효)**
  FAIL_LOUD   원장에 `failed` 존재 → **승격은 작동**, 키움 인증/토큰/IP 문제 재발(데이터는 유실)
  PENDING     당일 원장 실행 기록 없음(장 시작 전·수집 진행 중)
  NON_TRADING 대상일이 KR 비거래일(휴장/주말)

사용
----
    cd /Volumes/Realtek_NVME/stock_dashboard/runtime
    ./venv/bin/python scripts/check_kiwoom_rt_recovery.py                 # 오늘(거래일 09시 이후) 또는 직전 거래일
    ./venv/bin/python scripts/check_kiwoom_rt_recovery.py --date 2026-09-23
    ./venv/bin/python scripts/check_kiwoom_rt_recovery.py --json

종료코드: 0 = OK/NON_TRADING, 2 = FAIL_SILENT/FAIL_LOUD, 3 = PENDING.
이 스크립트는 **아무것도 쓰지 않는다**(읽기전용 DB 연결 + 원장 사본 읽기 + 로그 read).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from scripts.audit_kiwoom_intraday_integrity import FEEDS, check_feed  # noqa: E402
from trading_calendar import is_kr_trading_day  # noqa: E402

JOB = "키움실시간스냅샷"
LEDGER = ROOT / "data" / "collection_health.db"
LOG = ROOT / "logs" / "backend.launchd.log"
WS_MARKER = "[키움실시간스냅샷]"


def target_date(explicit: str | None = None) -> date:
    """대상일: 명시값 > (거래일이고 09시 이후면 오늘) > 직전 거래일."""
    if explicit:
        return date.fromisoformat(explicit)
    now = datetime.now()
    if is_kr_trading_day(now.date()) and now.hour * 60 + now.minute >= 9 * 60:
        return now.date()
    cursor = now.date() - timedelta(days=1)
    for _ in range(10):
        if is_kr_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return now.date()


def session_close(day: date) -> datetime:
    """그날 장 마감 직후 시각 — 계약 신선도를 '그날 기준'으로 평가하기 위한 기준시각."""
    return datetime.combine(day, dtime(15, 31))


def read_ledger(day: date) -> dict:
    """원장(`data/collection_health.db`)은 라이브 서버가 열고 있으므로 사본을 읽는다.

    runtime venv 의 sqlite3 라우터는 canonical `stock.db` 경로만 PG로 돌리므로
    임시 사본 경로는 그대로 SQLite 로 열린다(그 사실을 아래에서 테이블 조회로 확인한다).
    """
    if not LEDGER.exists():
        return {"available": False, "reason": f"ledger missing: {LEDGER}"}
    tmpdir = Path(tempfile.mkdtemp(prefix="kiwoom_rt_check_"))
    copy = tmpdir / "collection_health.db"
    try:
        shutil.copy2(LEDGER, copy)
        con = sqlite3.connect(str(copy))
        try:
            by_status = con.execute(
                "SELECT status, COUNT(*), MIN(started_at), MAX(started_at) "
                "FROM collection_job_runs WHERE job_name=? AND substr(started_at,1,10)=? "
                "GROUP BY status ORDER BY status",
                (JOB, day.isoformat()),
            ).fetchall()
            recent = con.execute(
                "SELECT started_at, status, substr(COALESCE(details_json,''),1,400) "
                "FROM collection_job_runs WHERE job_name=? AND substr(started_at,1,10)=? "
                "ORDER BY started_at DESC LIMIT 3",
                (JOB, day.isoformat()),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:  # 라우팅/PG 직결이면 여기로 온다
        return {"available": False, "reason": f"ledger not readable as sqlite: {exc}"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {
        "available": True,
        "runs_total": sum(r[1] for r in by_status),
        "by_status": [{"status": r[0], "runs": r[1], "first": r[2], "last": r[3]} for r in by_status],
        "failed_runs": sum(r[1] for r in by_status if r[0] == "failed"),
        "recent": [{"started_at": r[0], "status": r[1], "details": r[2]} for r in recent],
    }


def read_log_tail(limit: int = 6) -> dict:
    """로그에서 이 잡의 사이클 라인을 읽는다(로그 라인에는 시각이 없어 누적 기준으로만 본다)."""
    if not LOG.exists():
        return {"available": False, "reason": f"log missing: {LOG}"}
    cycles: list[str] = []
    gates: list[str] = []
    with LOG.open("r", errors="replace") as fh:
        for line in fh:
            if WS_MARKER not in line:
                continue
            if "ws_saved=" in line:
                cycles.append(line.strip())
            elif "장외/휴장" in line or "장중 진입" in line or "비활성" in line:
                gates.append(line.strip())
    return {
        "available": True,
        "cycles_total": len(cycles),
        "last_cycles": cycles[-limit:],
        "gates_total": len(gates),
        "last_gate": gates[-1] if gates else None,
        "log_mtime": datetime.fromtimestamp(LOG.stat().st_mtime).isoformat(timespec="seconds"),
    }


def verdict_for(day: date, rows_by_table: dict[str, int], failed_runs: int, runs_total: int) -> tuple[str, str]:
    if not is_kr_trading_day(day):
        return "NON_TRADING", f"{day.isoformat()} 는 KR 비거래일(휴장/주말) — 판정 대상 아님"
    empty = [t for t, n in rows_by_table.items() if not n]
    if failed_runs:
        return "FAIL_LOUD", (
            f"원장 failed {failed_runs}건 — 실패 승격은 작동, upstream(키움 토큰/IP) 문제. "
            f"빈 테이블: {', '.join(empty) or '없음'}"
        )
    if not empty:
        return "OK", "3테이블 모두 당일 행 존재 + 원장 failed 0건"
    if runs_total:
        return "FAIL_SILENT", (
            f"원장 success {runs_total}건인데 빈 테이블 {', '.join(empty)} → 침묵 실패 재발(수정 무효)"
        )
    return "PENDING", "당일 원장 실행 기록 없음 — 장 시작 전/수집 진행 중"


def main() -> int:
    parser = argparse.ArgumentParser(description="키움 장중 실시간 피드 복구 확인(읽기 전용)")
    parser.add_argument("--date", help="대상일 YYYY-MM-DD (기본: 오늘 또는 직전 거래일)")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args()

    now = datetime.now()
    day = target_date(args.date)
    as_of = session_close(day)
    conn = connect_primary_db(readonly=True, timeout=30)
    try:
        # 계약 신선도는 '그날 장 마감 직후' 기준으로 평가한다 — now 로 평가하면 과거 정상일도
        # 오늘 기준 stale 로 찍혀 판정 근거가 흐려진다.
        feeds = [check_feed(conn, spec, [day], now=as_of) for spec in FEEDS]
    finally:
        conn.close()

    rows_by_table = {f["table"]: next((d["rows"] for d in f["per_day"] if d["date"] == day.isoformat()), 0) for f in feeds}
    ledger = read_ledger(day)
    log = read_log_tail()
    verdict, reason = verdict_for(day, rows_by_table, int(ledger.get("failed_runs") or 0), int(ledger.get("runs_total") or 0))

    last_cycle = log["last_cycles"][-1] if log.get("last_cycles") else None
    ws_saved_last = None
    if last_cycle and "ws_saved=" in last_cycle:
        try:
            ws_saved_last = int(last_cycle.split("ws_saved=")[1].split()[0])
        except (IndexError, ValueError):
            ws_saved_last = None
    today = date.today()
    notes = []
    if args.date is None and not is_kr_trading_day(today):
        notes.append(f"오늘 {today.isoformat()} 는 KR 비거래일(휴장/주말) → 판정 대상은 직전 거래일 {day.isoformat()} 로 자동 선택")

    payload = {
        "checked_at": now.isoformat(timespec="seconds"),
        "target_date": day.isoformat(),
        "today": today.isoformat(),
        "today_is_trading_day": is_kr_trading_day(today),
        "verdict": verdict,
        "reason": reason,
        "notes": notes,
        "rows_by_table": rows_by_table,
        "contracts": {f["table"]: f.get("contract", {}).get("status") for f in feeds},
        "ws_saved_last_cycle": ws_saved_last,
        "ledger": ledger,
        "log": log,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"키움 장중 피드 복구 확인 — {payload['checked_at']} / 대상일 {payload['target_date']}")
        for n in notes:
            print(f"  ※ {n}")
        print(f"판정: {verdict} — {reason}")
        print("  당일 행수:", ", ".join(f"{t}={n:,}" for t, n in rows_by_table.items()))
        print("  계약상태(그날 마감 기준):", ", ".join(f"{t}={s}" for t, s in payload["contracts"].items()))
        print(f"  로그 최근 사이클 ws_saved={ws_saved_last} (로그 전체 기준 — 로그 라인에 날짜가 없다)")
        if ledger.get("available"):
            print(f"  원장: 실행 {ledger['runs_total']}건 " + ", ".join(
                f"{s['status']}={s['runs']}({s['first']}~{s['last']})" for s in ledger["by_status"]))
            for r in ledger["recent"]:
                print(f"    최근 {r['started_at']} {r['status']} {r['details']}")
        else:
            print(f"  원장: 읽기 불가 — {ledger.get('reason')}")
        if log.get("available"):
            print(f"  로그: 사이클 {log['cycles_total']}건 · 마지막 게이트 {log['last_gate']}")
            for line in log["last_cycles"]:
                print(f"    {line}")
        else:
            print(f"  로그: {log.get('reason')}")

    return 0 if verdict in ("OK", "NON_TRADING") else (2 if verdict.startswith("FAIL") else 3)


if __name__ == "__main__":
    raise SystemExit(main())
