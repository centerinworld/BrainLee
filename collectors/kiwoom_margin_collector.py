"""
collectors/kiwoom_margin_collector.py

키움 REST를 이용해 종목별 신용잔고/대주잔고 계열 데이터를 수집한다.
주의: 계정별 REST 엔드포인트 차이가 있을 수 있어 endpoint/api-id를 fallback 순서로 시도.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from typing import Any

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from db_utils import connect_stock_db
from collectors.kiwoom_collector import KiwoomCollector

logger = logging.getLogger(__name__)

# 키움 환경 차이에 대비한 후보군 (실패 시 다음 후보 시도)
API_CANDIDATES = [
    ("ka10013", "/api/dostk/stkinfo"),
    ("ka10014", "/api/dostk/stkinfo"),
    ("ka10017", "/api/dostk/stkinfo"),
]


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS margin_balance_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                dt TEXT NOT NULL,
                credit_balance REAL,
                credit_amount REAL,
                credit_ratio REAL,
                short_balance REAL,
                data_source TEXT DEFAULT 'kiwoom_ka10013',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, dt)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mbd_code ON margin_balance_daily(stock_code, dt)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kiwoom_margin_daily (
                stock_code TEXT NOT NULL,
                base_date TEXT NOT NULL,
                credit_balance REAL,
                credit_buy_balance REAL,
                credit_sell_balance REAL,
                loan_balance REAL,
                short_balance REAL,
                source_api_id TEXT,
                raw_json TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(stock_code, base_date)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kiwoom_margin_date ON kiwoom_margin_daily(base_date DESC)")
        # Existing deployments may predate this column; only migrate when absent.
        margin_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(kiwoom_margin_daily)").fetchall()
        }
        if "credit_ratio" not in margin_columns:
            conn.execute("ALTER TABLE kiwoom_margin_daily ADD COLUMN credit_ratio REAL")
        # 2026-08-23: 레거시 kiwoom_credit_balance 브리지 대상 — collectors/kiwoom_collector.py의
        # 원본 스키마와 동일(그쪽 컬렉터는 더 이상 스케줄러에서 호출되지 않아 죽은 코드가 됐지만
        # 테이블 스키마 정의 자체는 이쪽에도 복사해둬 이 파일만으로도 자기완결적이게 함).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kiwoom_credit_balance (
                stock_code TEXT NOT NULL,
                dt TEXT NOT NULL,
                credit_balance_qty REAL,
                credit_balance_amt REAL,
                credit_ratio REAL,
                new_credit_qty REAL,
                repay_credit_qty REAL,
                raw_json TEXT,
                updated_at TEXT,
                PRIMARY KEY (stock_code, dt)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _to_num(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        s = str(v).replace(",", "").strip()
        if not s:
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _extract_rows(rows: Any) -> list[dict]:
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    if isinstance(rows, dict):
        return [rows]
    return []


def _parse_margin_row(data: dict) -> dict:
    row = {}
    row["dt"] = str(data.get("dt") or data.get("base_dt") or "").strip()
    # 응답 포맷 가변: 가장 흔한 key 후보들을 순차 매핑
    row["credit_balance"] = _to_num(data.get("crdt_bal") or data.get("credit_bal") or data.get("siny_bal") or data.get("jango"))
    row["credit_buy_balance"] = _to_num(data.get("crdt_buy_bal") or data.get("buy_bal") or data.get("siny_buy") or data.get("new"))
    row["credit_sell_balance"] = _to_num(data.get("crdt_sell_bal") or data.get("sell_bal") or data.get("siny_sell") or data.get("rpya"))
    if abs(row["credit_balance"]) < 1e-12:
        row["credit_balance"] = _to_num(data.get("remn"))
    row["loan_balance"] = _to_num(data.get("loan_bal") or data.get("yungja_bal") or data.get("loan"))
    row["short_balance"] = _to_num(data.get("short_bal") or data.get("daeju_bal") or data.get("short"))
    # 2026-08-23 버그 수정: ka10013(crd_trde_trend) 응답에 신용잔고비율(shr_rt)이 매 행마다
    # 이미 포함돼 있는데도 지금까지 추출하지 않아 margin_balance_daily.credit_ratio가 생성
    # 이래(2026-06-01~) 단 한 건도 채워진 적이 없었음(항상 하드코딩 None 저장) — 이 프로젝트의
    # 여러 다운스트림(tenbagger_engine·리스크게이트·트리거알림 등)이 실제로는 구버전
    # kiwoom_credit_balance(2026-07-07 이후 수집 자체가 중단된 레거시 테이블)만 보고 있었으므로,
    # "최신 컬렉터인데 값이 비어있는" 조용한 회귀가 7주 넘게 발견되지 않고 있었다.
    row["credit_ratio"] = _to_num(data.get("shr_rt") or data.get("crdt_rt"))
    row["credit_amount"] = _to_num(data.get("amt"))
    return row


def _fetch_history(collector: KiwoomCollector, stock_code: str) -> tuple[list[dict], str | None, str | None]:
    """종목의 신용거래동향 이력 전체(응답에 담긴 만큼 — 통상 최근 수개월 영업일)를 반환.

    2026-08-23 버그 수정: 기존엔 응답 배열의 첫 번째(최신) 행만 취하고 나머지를 버렸다.
    매일 종목당 1회 API를 호출하면서도 정작 응답에 이미 들어있는 과거 이력은 활용하지
    않고 있었던 것 — 배열 전체를 파싱하면 API 호출 횟수를 늘리지 않고도 결측 구간
    (2026-07-07~08-22, kiwoom_credit_balance 갱신중단 기간)을 상당 부분 메울 수 있다.
    """
    if not collector.ensure_token():
        return [], None, "token_fail"

    base_url = collector.base_url.rstrip("/")
    for api_id, endpoint in API_CANDIDATES:
        try:
            url = f"{base_url}{endpoint}"
            headers = collector._auth_headers(api_id=api_id)
            # 일부 TR은 stkinfo 공용 URI를 사용하므로 공통 파라미터를 넉넉히 전달
            body = {
                "stk_cd": stock_code,
                "dt": datetime.now().strftime("%Y%m%d"),
                "qry_tp": "0",
                "mrkt_tp": "000",
            }
            r = requests.post(url, headers=headers, json=body, timeout=10)
            raw = r.json() if r.content else {}
            if r.status_code >= 400:
                continue

            # 가변 출력키 대응
            block = (
                raw.get("crdt")
                or raw.get("crdt_bal")
                or raw.get("crd_trde_trend")
                or raw.get("stk_crdt")
                or raw.get("output")
                or raw.get("data")
                or raw
            )
            entries = _extract_rows(block)
            if not entries:
                # 데이터 없음이면 다음 후보
                continue

            parsed_rows = [_parse_margin_row(e) for e in entries]
            parsed_rows = [
                p for p in parsed_rows
                if p["dt"] and not all(abs(p[k]) < 1e-12 for k in p if k != "dt")
            ]
            if not parsed_rows:
                continue
            return parsed_rows, api_id, json.dumps(raw, ensure_ascii=False)[:4000]
        except Exception:
            continue

    return [], None, "no_valid_endpoint_or_zero"


def collect_kiwoom_margin_daily(limit: int = 300) -> dict:
    _ensure_table()
    kc = KiwoomCollector()
    if not kc.is_configured():
        return {"ok": False, "reason": "kiwoom_not_configured"}

    conn = connect_stock_db(timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        rows = conn.execute(
            """
            SELECT stock_code
            FROM stock_universe
            WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            ORDER BY market_cap DESC
            LIMIT ?
            """,
            (max(10, int(limit)),),
        ).fetchall()
        codes = [r[0] for r in rows]
    finally:
        conn.close()

    today = datetime.now().strftime("%Y%m%d")
    conn = connect_stock_db(timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        saved = 0
        rows_written = 0
        fail = 0
        reason_count: dict[str, int] = {}
        for i, code in enumerate(codes, start=1):
            parsed_rows, api_id, raw = _fetch_history(kc, code)
            if not parsed_rows:
                fail += 1
                reason = (raw or "unknown")[:80]
                reason_count[reason] = reason_count.get(reason, 0) + 1
                continue
            latest_dt = parsed_rows[0]["dt"]
            for p in parsed_rows:
                dt_val = p["dt"]
                is_latest = dt_val == latest_dt
                conn.execute(
                    """
                    INSERT INTO margin_balance_daily(
                        stock_code, dt, credit_balance, credit_amount, credit_ratio, short_balance, data_source, collected_at
                    ) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code, dt) DO UPDATE SET
                        credit_balance=excluded.credit_balance,
                        credit_amount=excluded.credit_amount,
                        credit_ratio=excluded.credit_ratio,
                        short_balance=excluded.short_balance,
                        data_source='kiwoom_ka10013',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        code, dt_val, p["credit_balance"], p["credit_amount"], p["credit_ratio"],
                        p["short_balance"], "kiwoom_ka10013",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO kiwoom_margin_daily(
                        stock_code, base_date,
                        credit_balance, credit_buy_balance, credit_sell_balance,
                        loan_balance, short_balance, credit_ratio,
                        source_api_id, raw_json, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code, base_date) DO UPDATE SET
                        credit_balance=excluded.credit_balance,
                        credit_buy_balance=excluded.credit_buy_balance,
                        credit_sell_balance=excluded.credit_sell_balance,
                        loan_balance=excluded.loan_balance,
                        short_balance=excluded.short_balance,
                        credit_ratio=excluded.credit_ratio,
                        source_api_id=excluded.source_api_id,
                        raw_json=COALESCE(excluded.raw_json, kiwoom_margin_daily.raw_json),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        code, dt_val,
                        p["credit_balance"], p["credit_buy_balance"], p["credit_sell_balance"],
                        p["loan_balance"], p["short_balance"], p["credit_ratio"],
                        # raw_json은 응답 전체(최대 100여 영업일)가 매 행에 중복 저장되는 걸
                        # 막기 위해 최신 행에만 보관(나머지는 숫자만 재구성한 것이므로 불필요)
                        api_id, (raw if is_latest else None),
                    ),
                )
                # 2026-08-23: 레거시 kiwoom_credit_balance 테이블로도 동일 값을 브리지 —
                # 이 테이블을 읽는 기존 소비처(tenbagger_engine/routes/kiwoom.py/
                # routes/kis_trading.py 리스크게이트/트리거알림 등 10개 파일)를 한 곳도
                # 건드리지 않고 신선한 데이터를 다시 받아보게 하기 위함. 스키마가
                # (stock_code, dt) 자연키로 호환되고 컬럼 의미도 그대로 대응된다.
                conn.execute(
                    """
                    INSERT OR REPLACE INTO kiwoom_credit_balance
                    (stock_code, dt, credit_balance_qty, credit_balance_amt, credit_ratio,
                     new_credit_qty, repay_credit_qty, raw_json, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    """,
                    (
                        code, dt_val, p["credit_balance"], p["credit_amount"], p["credit_ratio"],
                        p["credit_buy_balance"], p["credit_sell_balance"], None,
                    ),
                )
                rows_written += 1
            saved += 1
            if i % 20 == 0:
                conn.commit()

        conn.commit()
        top_reasons = sorted(reason_count.items(), key=lambda x: x[1], reverse=True)[:5]
        return {
            "ok": True,
            "target": len(codes),
            "saved": saved,
            "rows_written": rows_written,
            "failed": fail,
            "base_date": today,
            "fail_reasons_top": top_reasons,
        }
    finally:
        conn.close()

def collect_margin_balance_daily(limit: int = 300) -> dict:
    """지시서 호환 별칭."""
    return collect_kiwoom_margin_daily(limit=limit)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="키움 신용/대주 잔고 수집")
    ap.add_argument("--limit", type=int, default=300)
    args = ap.parse_args()
    print(json.dumps(collect_kiwoom_margin_daily(limit=args.limit), ensure_ascii=False, indent=2))
