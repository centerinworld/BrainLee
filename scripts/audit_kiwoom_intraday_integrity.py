#!/usr/bin/env python3
"""키움 장중 실시간 피드 3종 무결성 감사 — 읽기 전용.

왜 필요한가 (2026-09-24 실측)
------------------------------
`KiwoomCollector._save_realtime_snapshot()` 한 번이 **세 테이블을 같은 트랜잭션으로** 쓴다:
  ① `kiwoom_realtime_quote`   — 종목당 1행 최신 스냅샷 (PK stock_code, upsert)
  ② `kiwoom_tick_history`     — 틱 원본 append (PK id, event_ts는 초 단위로 절단)
  ③ `kiwoom_minute_snapshot`  — 1분 집계 OHLC/체결강도 (PK stock_code+minute_ts, upsert)
그런데 09-22·09-23(KR 거래일)에는 8050 토큰 실패로 세 테이블이 통째로 비었고, 잡은 매 사이클
`status=success`로 기록돼 아무 경보도 나지 않았다. `collection_health`의 신선도 계약은
"마지막 데이터가 최근 거래일인가"만 판정하므로, **행 단위 이상(자연키 중복/NaN/비장시간)**은
이 감사가 담당한다. 계약과 이 감사를 함께 돌려야 세 저장 경로 중 하나만 죽는 경우도 잡힌다.

사용
----
    cd /Volumes/Realtek_NVME/stock_dashboard/runtime
    ./venv/bin/python scripts/audit_kiwoom_intraday_integrity.py           # 사람이 읽는 리포트
    ./venv/bin/python scripts/audit_kiwoom_intraday_integrity.py --json    # 기계 판독

종료코드: 0 = 이상 없음, 2 = 문제 발견(계약 stale / NaN / 비장시간 / 자연키 중복 급증).
이 스크립트는 **아무것도 쓰지 않는다**(readonly 연결 + SELECT만).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collection_health import CONTRACT_BY_KEY, evaluate_contract  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from trading_calendar import is_kr_trading_day  # noqa: E402

SESSION_START = "09:00"
SESSION_END = "15:30"
# 틱 원본에서 '같은 초·같은 가격' 반복은 정상이다: REG 등록 시 refresh=1 초기 스냅샷을 다시 받기
# 때문. 실측(09-16~09-21) 하루 21~23만행 중 자연키 중복행 비율이 31.2~33.0%로 안정적이므로,
# 절대 임계가 아니라 '기준선 대비 급증'만 이상 신호로 본다(임계 45%).
DUP_RATIO_TOLERANCE = 0.45


@dataclass(frozen=True)
class FeedSpec:
    table: str
    ts_col: str
    key_expr: str
    price_cols: tuple[str, ...]
    contract_key: str | None = None
    allow_intra_second_repeats: bool = False


FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        "kiwoom_minute_snapshot", "minute_ts", "stock_code || '|' || minute_ts",
        ("open_price", "high_price", "low_price", "close_price"), contract_key="kiwoom_intraday",
    ),
    FeedSpec(
        "kiwoom_realtime_quote", "updated_at", "stock_code",
        ("last_price", "change_price", "change_rate"), contract_key="kiwoom_intraday_quote",
    ),
    FeedSpec(
        "kiwoom_tick_history", "event_ts", "stock_code || '|' || event_ts || '|' || COALESCE(CAST(last_price AS TEXT), '')",
        ("last_price", "change_price", "change_rate"), contract_key="kiwoom_intraday_tick",
        allow_intra_second_repeats=True,
    ),
)


def recent_trading_days(now: datetime | None = None, count: int = 6) -> list[date]:
    now = now or datetime.now()
    cursor = now.date()
    if not is_kr_trading_day(cursor) or now.hour * 60 + now.minute < 9 * 60:
        cursor -= timedelta(days=1)
    days: list[date] = []
    while len(days) < count:
        if is_kr_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
    return days


def scalar(conn, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def check_feed(conn, spec: FeedSpec, days: list[date], now: datetime | None = None) -> dict:
    """한 피드 테이블의 워터마크/행수/중복/NaN/비장시간을 읽기 전용으로 점검한다."""
    now = now or datetime.now()
    out: dict = {"table": spec.table, "issues": [], "per_day": []}
    oldest = days[-1].isoformat()

    out["rows"] = int(scalar(conn, f"SELECT COUNT(*) FROM {spec.table}") or 0)
    out["distinct_keys"] = int(scalar(conn, f"SELECT COUNT(DISTINCT {spec.key_expr}) FROM {spec.table}") or 0)
    out["duplicate_rows"] = max(0, out["rows"] - out["distinct_keys"])
    out["watermark"] = scalar(conn, f"SELECT MAX({spec.ts_col}) FROM {spec.table}")
    out["distinct_codes"] = int(scalar(conn, f"SELECT COUNT(DISTINCT stock_code) FROM {spec.table}") or 0)

    # NaN: PG에서 close>0 류 비교는 NaN을 걸러내지 못한다(NaN이 모든 실수보다 크다).
    # 그래서 ::text 비교 대신 이식 가능한 CAST(col AS TEXT)='NaN' 으로 잡는다.
    nan_parts = [f"CAST({col} AS TEXT) = 'NaN'" for col in spec.price_cols]
    out["nan_rows"] = int(scalar(conn, f"SELECT COUNT(*) FROM {spec.table} WHERE {' OR '.join(nan_parts)}") or 0)
    if out["nan_rows"]:
        out["issues"].append(f"nan_rows:{out['nan_rows']}")

    # 일자별 행수/커버 종목/비장시간 행 (최근 거래일만 스캔해 비용을 묶는다)
    for d in days:
        ds = d.isoformat()
        ts = spec.ts_col
        rows = int(scalar(conn, f"SELECT COUNT(*) FROM {spec.table} WHERE substr({ts},1,10)=?", (ds,)) or 0)
        codes = int(scalar(conn, f"SELECT COUNT(DISTINCT stock_code) FROM {spec.table} WHERE substr({ts},1,10)=?", (ds,)) or 0)
        off = int(scalar(
            conn,
            f"SELECT COUNT(*) FROM {spec.table} WHERE substr({ts},1,10)=? "
            f"AND (substr({ts},12,5) < ? OR substr({ts},12,5) > ?)",
            (ds, SESSION_START, SESSION_END),
        ) or 0)
        out["per_day"].append({"date": ds, "rows": rows, "codes": codes, "off_session_rows": off})
        if off:
            out["issues"].append(f"off_session:{ds}:{off}")

    # 자연키 중복 (allow_intra_second_repeats=True 인 틱은 급증만 본다)
    # 휴장/공백일은 rows=0이라 중복률이 무의미하므로 '행이 있는 최신 거래일'을 기준으로 본다.
    latest = next((d for d in out["per_day"] if d["rows"]), None)
    if latest and latest["rows"]:
        distinct_today = int(scalar(
            conn,
            f"SELECT COUNT(DISTINCT {spec.key_expr}) FROM {spec.table} WHERE substr({spec.ts_col},1,10)=?",
            (latest["date"],),
        ) or 0)
        dup_rows = latest["rows"] - distinct_today
        out["latest_dup_rows"] = dup_rows
        out["latest_dup_ratio"] = round(dup_rows / latest["rows"], 4)
        if not spec.allow_intra_second_repeats and dup_rows:
            out["issues"].append(f"duplicate_grain:{latest['date']}:{dup_rows}")
        if spec.allow_intra_second_repeats and out["latest_dup_ratio"] > DUP_RATIO_TOLERANCE:
            out["issues"].append(f"duplicate_ratio_high:{latest['date']}:{out['latest_dup_ratio']}")

    if spec.contract_key:
        health = evaluate_contract(CONTRACT_BY_KEY[spec.contract_key], now=now)
        out["contract"] = {k: health.get(k) for k in ("key", "status", "source_as_of", "expected_as_of", "lag", "issues")}
        if health.get("status") != "healthy":
            out["issues"].append(f"contract:{health.get('status')}")

    if out["watermark"] and str(out["watermark"])[:10] < oldest:
        out["issues"].append(f"watermark_older_than:{oldest}")
    out["ok"] = not out["issues"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="키움 장중 실시간 피드 3종 무결성 감사(읽기 전용)")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--days", type=int, default=6, help="조회할 최근 거래일 수 (기본 6)")
    args = parser.parse_args()

    now = datetime.now()
    days = recent_trading_days(now, count=max(1, args.days))
    conn = connect_primary_db(readonly=True, timeout=30)
    try:
        results = [check_feed(conn, spec, days, now=now) for spec in FEEDS]
    finally:
        conn.close()

    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "trading_days": [d.isoformat() for d in days],
        "feeds": results,
        "ok": all(r["ok"] for r in results),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"키움 장중 피드 무결성 감사 — {payload['generated_at']} (최근 거래일: {', '.join(payload['trading_days'][:3])} …)")
        for r in results:
            mark = "OK  " if r["ok"] else "이상"
            print(f"\n[{mark}] {r['table']}  rows={r['rows']:,} codes={r['distinct_codes']:,} "
                  f"watermark={r['watermark']} nan={r['nan_rows']}")
            if r.get("latest_dup_ratio") is not None:
                print(f"        자연키 중복행(최신 데이터일): {r['latest_dup_rows']:,} ({r['latest_dup_ratio']:.1%})")
            if r.get("contract"):
                c = r["contract"]
                print(f"        계약 {c['key']}: {c['status']} (source_as_of={c['source_as_of']}, "
                      f"expected={c['expected_as_of']}, lag={c['lag']})")
            for d in r["per_day"]:
                print(f"        {d['date']}  rows={d['rows']:,} codes={d['codes']:,} 비장시간={d['off_session_rows']}")
            for issue in r["issues"]:
                print(f"        ⚠ {issue}")
        print("\n판정:", "정상" if payload["ok"] else "문제 발견 — 위 ⚠ 항목 확인")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
