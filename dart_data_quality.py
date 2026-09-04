"""
종목별 DART 원문 조회 가능여부 추적 (2026-08-29 신설).

사용자 지시: "Dart 원문 조회자체가 안되는 종목들에는 국내종목 페이지 내에 꼭 표시를 해서
데이터에 문제가 있다고 표시 할 것" — DART finstate_all이 특정 연도에 대해 아무 데이터도
반환하지 않는 종목(상폐/비표준 공시/신규상장 등)을 stock_dart_data_quality 테이블에
누적 기록하고, 프론트엔드가 이를 조회해 경고 배지를 표시할 수 있게 한다.

여러 스크립트(연간행 dedup, snapshot 백필 등)가 DART 재조회를 시도할 때마다 이 모듈의
record_dart_result()를 호출해 결과를 누적한다 — 한 번의 실패로 "확인불가" 확정하지 않고,
연도별 성공/실패 이력을 모아 종합 판단한다(어느 한 해라도 성공하면 최소 partial).
"""
import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_dart_data_quality (
          stock_code TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          note TEXT,
          ok_years TEXT,
          fail_years TEXT,
          checked_at TEXT NOT NULL,
          source TEXT
        )
    """)


def record_dart_result(conn, stock_code: str, year: int, ok: bool, source: str) -> None:
    """DART 원문 재조회 결과 1건을 누적 기록. ok=False면 해당 연도를 fail_years에,
    ok=True면 ok_years에 추가(중복 제거) 후 status를 재계산해 upsert."""
    row = conn.execute(
        "SELECT ok_years, fail_years FROM stock_dart_data_quality WHERE stock_code=%s",
        (stock_code,),
    ).fetchone()

    ok_years = set(json.loads(row[0])) if row and row[0] else set()
    fail_years = set(json.loads(row[1])) if row and row[1] else set()

    if ok:
        ok_years.add(int(year))
        fail_years.discard(int(year))
    else:
        if int(year) not in ok_years:
            fail_years.add(int(year))

    if fail_years and not ok_years:
        status = "no_dart_data"
        note = f"DART 원문 조회 시도한 {len(fail_years)}개 연도 전부 실패 — 데이터 신뢰도 확인 필요"
    elif fail_years:
        status = "partial"
        note = f"{len(fail_years)}개 연도 DART 원문 조회 실패, {len(ok_years)}개 연도는 정상"
    else:
        status = "ok"
        note = None

    conn.execute("""
        INSERT INTO stock_dart_data_quality (stock_code, status, note, ok_years, fail_years, checked_at, source)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (stock_code) DO UPDATE SET
            status=EXCLUDED.status, note=EXCLUDED.note,
            ok_years=EXCLUDED.ok_years, fail_years=EXCLUDED.fail_years,
            checked_at=EXCLUDED.checked_at, source=EXCLUDED.source
    """, (stock_code, status, note, json.dumps(sorted(ok_years)), json.dumps(sorted(fail_years)),
          _now_iso(), source))
