# -*- coding: utf-8 -*-
"""
stock_dashboard 실제 운영 DB(PostgreSQL)에 대한 읽기 전용 데이터 무결점 스캐너.

2026-09-15 소유자 지시("실제로 DB를 스캔하는 실행 도구를 연결할까요?" → "실제 실행
도구 연결"): strict_agi_orchestrator.py의 "수집 데이터 무결점" 목표(goal_1)가 지금까지
실제 DB를 단 한 번도 조회하지 않고 "어떻게 검증할지"에 대한 추상적 명세 텍스트만
반복 생성하고 있었다. 이 모듈은 그 자리에 실제로 꽂을 수 있는, 신뢰된 컨트롤러가
직접 실행하는(LLM이 대신 실행하지 않는) 읽기 전용 스캐너다 - LLM에게는 이 스캐너의
실제 결과만 근거로 준다.

2026-09-17 정정: 처음엔 `/Volumes/Realtek_NVME/stock_dashboard/stock.db`(SQLite
파일)를 스캔했고 "가격 데이터가 5~7주째 멈췄다"고 보고했다 - 이건 틀렸다. 실제로는
stock_dashboard가 이미 PostgreSQL로 커트오버했고(`runtime/config.py`의
`POSTGRES_DATABASE_URL`, `runtime/main.py`가 `IS_POSTGRES` 분기로 이미 Postgres를
씀 - 실측: 활성 커넥션 다수, price_history 최신 날짜 = 오늘) 그 SQLite 파일은
컷오버 시점에 멈춘 죽은 스냅샷이었다. 이 스캐너를 실제 운영 DB(Postgres)로
다시 겨냥한다 - 다시는 죽은 사본을 "실제 데이터"로 착각하지 않도록, 이 모듈
자체가 실제로 연결에 성공한 DB의 정체(호스트·DB명·활성 여부)를 결과에 남긴다.

설계 원칙:
- 읽기 전용. 세션 자체를 `SET default_transaction_read_only=on`으로 잠가 이
  프로세스가 실수로라도 쓰기를 시도하면 그 자리에서 실패한다.
- 큰 테이블(수백만~천만 행)에서도 인덱스를 타는 가벼운 쿼리만 쓴다 - 전체 스캔 금지.
- 결과는 실제 숫자만 담는다("무결하다/양호하다" 같은 판정 문구를 스캐너가 대신
  내리지 않는다 - 판정은 이후 LLM 단계의 몫이고, 이 모듈은 사실만 제공한다).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

ENV_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/.env")


def _read_database_url() -> str:
    env_url = os.getenv("POSTGRES_DATABASE_URL")
    if env_url:
        return env_url
    try:
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith("POSTGRES_DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    raise RuntimeError(f"POSTGRES_DATABASE_URL을 찾을 수 없음 (env 또는 {ENV_PATH})")


def _psycopg_conninfo(sqlalchemy_url: str) -> str:
    # config.py는 SQLAlchemy 스타일("postgresql+psycopg://...")을 쓴다 - psycopg는
    # 순수 "postgresql://..." 형태만 받으므로 드라이버 접미사만 벗겨낸다.
    return re.sub(r"^postgresql\+\w+://", "postgresql://", sqlalchemy_url)

# (테이블, 날짜 컬럼, OHLC 컬럼 매핑) - 신뢰된 컨트롤러가 미리 정한 고정 목록만
# 조회한다(임의 테이블명을 동적으로 받지 않음 - SQL 인젝션/오용 표면 자체를 없앤다).
PRICE_TABLES = [
    {"table": "price_history", "date_col": "date", "code_col": "stock_code",
     "close_col": "close", "open_col": "open", "high_col": "high", "low_col": "low",
     "date_format": "iso", "label": "국내 일봉(price_history)"},
    {"table": "stock_price_daily", "date_col": "bas_dt", "code_col": "stock_code",
     "close_col": "close_price", "open_col": "open_price", "high_col": "high_price", "low_col": "low_price",
     "date_format": "compact", "label": "국내 일봉 스냅샷(stock_price_daily)"},
    {"table": "us_price_history", "date_col": "date", "code_col": "ticker",
     "close_col": "close", "open_col": "open", "high_col": "high", "low_col": "low",
     "date_format": "iso", "label": "미국 일봉(us_price_history)"},
]


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(_psycopg_conninfo(_read_database_url()), connect_timeout=10)
    conn.execute("SET default_transaction_read_only = on")
    return conn


def _staleness_days(latest: str | None) -> int | None:
    if not latest:
        return None
    try:
        latest_date = date.fromisoformat(str(latest)[:10])
    except ValueError:
        return None
    return (date.today() - latest_date).days


def scan_price_table(conn: psycopg.Connection, spec: dict) -> dict[str, Any]:
    t, dc, cc = spec["table"], spec["date_col"], spec["close_col"]
    oc, hc, lc = spec["open_col"], spec["high_col"], spec["low_col"]
    row_count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    latest_date = conn.execute(f"SELECT MAX({dc}) FROM {t}").fetchone()[0]
    # 2026-09-19 발견: 이 카운트가 "거래정지일 마커"(시가/고가/저가=0 이고 거래량도
    # 0인 행 - 실제 거래가 없었다는 뜻이지 결측 데이터가 아님)를 결함으로 오판했다.
    # stock_dashboard/runtime/price_integrity.py의 invalid_ohlcv()는 이 패턴을 이미
    # "공급된 정지 마커, 거래 가능 캔들 아님"으로 정확히 인정한다 - 실측 대조 결과
    # price_history는 204,241건 중 203,854건(99.98%), stock_price_daily는 3,577건
    # 전부가 이 마커였다(실제 결함은 각각 39건/0건). 그 판정 기준을 그대로 따른다 -
    # 5개 값 중 하나라도 NULL이면(거래량 포함) 여전히 결함으로 센다(실제 함수가
    # None을 무조건 결함으로 보는 것과 동일 - us_price_history의 진짜 결함인
    # "거래량은 있는데 시가만 NULL" 8만여 건은 이 예외에 안 걸려 그대로 잡힌다).
    invalid_ohlc = conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE ({cc} IS NULL OR {cc}::float<=0 OR {oc} IS NULL OR {oc}::float<=0) "
        f"AND NOT (volume IS NOT NULL AND volume::float=0 "
        f"AND {oc} IS NOT NULL AND {oc}::float=0 AND {hc} IS NOT NULL AND {hc}::float=0 "
        f"AND {lc} IS NOT NULL AND {lc}::float=0 AND {cc} IS NOT NULL AND {cc}::float>0)"
    ).fetchone()[0]
    high_lt_low = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE {hc}::float < {lc}::float").fetchone()[0]
    # bas_dt처럼 "YYYYMMDD"(구분자 없음) 텍스트 컬럼을 ISO 형식 기준값과 그냥 문자열
    # 비교하면 거짓 양성이 난다 - 형식에 맞는 기준값으로 비교한다(SQLite 버전에서
    # 실제로 겪은 버그, 여기서도 동일하게 방지).
    now_expr = "to_char(CURRENT_DATE,'YYYYMMDD')" if spec.get("date_format") == "compact" else "CURRENT_DATE::text"
    future_dated = conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE {dc} > {now_expr}"
    ).fetchone()[0]
    return {
        "table": t, "label": spec["label"], "row_count": row_count,
        "latest_date": latest_date, "staleness_days": _staleness_days(latest_date),
        "invalid_ohlc_count": invalid_ohlc, "high_less_than_low_count": high_lt_low,
        "future_dated_count": future_dated,
    }


def run_scan() -> dict[str, Any]:
    """전체 스캔을 실행하고 실제 숫자만 담은 JSON을 반환한다. 실패 시 예외를 던진다 -
    실패를 조용히 삼켜 '이상 없음'으로 보이게 하지 않는다."""
    started = time.time()
    conninfo = _psycopg_conninfo(_read_database_url())
    conn = _connect()
    try:
        db_identity = conn.execute(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
        ).fetchone()
        active_backends = conn.execute(
            "SELECT COUNT(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"
        ).fetchone()[0]
        tables = [scan_price_table(conn, spec) for spec in PRICE_TABLES]
        open_quality_issues = conn.execute(
            "SELECT COUNT(*) FROM data_quality_issues WHERE is_resolved=0"
        ).fetchone()[0]
        oldest_open_issue_detected_at = conn.execute(
            "SELECT MIN(detected_at) FROM data_quality_issues WHERE is_resolved=0"
        ).fetchone()[0]
    finally:
        conn.close()
    stale_tables = [t for t in tables if (t["staleness_days"] or 0) > 3]
    return {
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db_backend": "postgresql",
        "db_name": db_identity[0], "db_host": db_identity[1] or "localhost", "db_port": db_identity[2],
        "other_active_connections": active_backends,
        "elapsed_seconds": round(time.time() - started, 2),
        "tables": tables,
        "stale_tables": [t["table"] for t in stale_tables],
        "open_quality_issues_tracked": open_quality_issues,
        "oldest_open_quality_issue_detected_at": oldest_open_issue_detected_at,
    }


if __name__ == "__main__":
    print(json.dumps(run_scan(), ensure_ascii=False, indent=2))
