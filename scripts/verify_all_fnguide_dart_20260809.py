"""
전종목 FnGuide vs DART 교차검증 스윕 (2026-08-09)

사용자 지시: "전종목 verify" — 오늘 발견/수정한 계정 오매칭 버그들(cash/net_income/
operating_profit/total_equity/revenue)이 실제로 전종목에 걸쳐 해소됐는지 확인.

FNGUIDE 레이트리밋(min_interval=3s, daily_limit=1500) 제약으로 fetch_fnguide_all의
annual_only=True 경량모드(종목당 3회 호출: getFinIncome/getFinBalance/getFinCashFlow
연간만, 분기·EPS/BPS 생략)를 사용 — 그래도 daily_limit 하에서 하루 최대 ~500종목.
재실행 시 "오늘 이미 스냅샷을 저장한 종목"은 건너뛰어(스냅샷 fetched_at 날짜 기준)
자연스럽게 다음날 이어서 진행 가능(run()의 has_snapshot 우선순위 로직과 동일 원리).

실행: python3 scripts/verify_all_fnguide_dart_20260809.py [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collectors.fnguide_financial_collector import (
    _conn, cross_validate_annual, fetch_fnguide_all, save_snapshot,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def run_verify_sweep(limit: int = 9999, conn=None) -> dict:
    """전종목 FnGuide vs DART 순차 교차검증 (2026-08-09 신규, 2026-08-09(2) 스케줄러 재사용을
    위해 함수로 분리). limit은 이번 호출에서 처리할 최대 종목수(FNGUIDE 일일한도 준수용,
    annual_only 모드는 종목당 3회 호출: getFinIncome/getFinBalance/getFinCashFlow).
    "오늘 이미 스냅샷을 저장한 종목"은 자동으로 건너뛰므로, 매일 정해진 limit만큼만 호출해도
    날짜가 바뀌면 이어서 다음 종목들을 처리 — 전종목(~2,585개)을 여러 날에 걸쳐 순환한다.
    conn을 주입하지 않으면 자체 커넥션을 열고 닫는다(스크립트 단독 실행용)."""
    own_conn = conn is None
    if own_conn:
        conn = _conn()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    codes = [r[0] for r in conn.execute("""
        SELECT su.stock_code,
               MAX(CASE WHEN date(fss.fetched_at)=? THEN 1 ELSE 0 END) AS done_today,
               MAX(fss.fetched_at) AS last_fetched
        FROM stock_universe su
        LEFT JOIN financial_source_snapshot fss
          ON fss.stock_code = su.stock_code AND fss.data_source='fnguide'
        WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND COALESCE(su.stock_type,'보통주') = '보통주'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          -- 2026-08-09: stock_type이 NULL인 우선주가 COALESCE 기본값으로 보통주 취급되던
          -- 문제(하이트진로2우B 등 실측 확인, collect_dart_cashflow_batch.py에서 같은 날
          -- 발견한 것과 동일 클래스) — 종목명 접미사 패턴으로 우선주 명시 배제.
          -- 2026-08-11: GLOB('*우'/'*우[A-Z]')은 db_compat의 sqlite→Postgres 라우터가
          -- 지원하는 번역 패턴(6자리 숫자 GLOB만 지원)에 없어 Postgres에서
          -- "syntax error at or near NOT"로 스케줄러 잡이 매일 실패하고 있었음 — LIKE로 교체
          -- (우_ 는 '우'+임의의 한 글자를 모두 배제해 [A-Z] 한정보다 살짝 넓지만, 이 필터는
          -- "확실한 우선주만 배제"가 목적이라 안전한 방향으로만 넓어짐).
          AND su.stock_name NOT LIKE '%우' AND su.stock_name NOT LIKE '%우_'
        GROUP BY su.stock_code
        -- 2026-08-11: SQLite는 HAVING에서 SELECT 별칭(done_today) 참조를 허용하지만
        -- Postgres는 HAVING이 SELECT보다 먼저 평가돼 별칭이 안 보임(column "done_today"
        -- does not exist) — 별칭 대신 원본 표현식을 그대로 반복해 두 DB 모두 호환.
        HAVING MAX(CASE WHEN date(fss.fetched_at)=? THEN 1 ELSE 0 END) = 0
            OR MAX(CASE WHEN date(fss.fetched_at)=? THEN 1 ELSE 0 END) IS NULL
        ORDER BY last_fetched ASC NULLS FIRST, su.stock_code
        LIMIT ?
    """, (today, today, today, limit)).fetchall()]
    log.info(f"대상 {len(codes)}종목 (오늘 이미 처리된 종목 제외, limit={limit})")

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = {"verified": 0, "mismatch": 0, "unverified": 0, "no_data": 0, "errors": 0}
    mismatches: list[dict] = []

    for i, code in enumerate(codes, 1):
        try:
            data = fetch_fnguide_all(code, "CFS", annual_only=True)
            if not data or not data.get("annual"):
                stats["no_data"] += 1
                continue
            for yr, ydata in sorted(data["annual"].items()):
                if yr < 2023:
                    continue  # 최근 3개년만 (쿼터 절약, 오래된 연도는 이미 검증됨)
                snap_id = save_snapshot(conn, code, yr, 0, 1, "CFS", data["source_url"], ydata, now_iso)
                status = cross_validate_annual(conn, code, "CFS", yr, ydata, snap_id)
                stats[status] = stats.get(status, 0) + 1
                if status == "mismatch":
                    note = conn.execute(
                        "SELECT verification_note FROM financial_source_snapshot WHERE id=?", (snap_id,)
                    ).fetchone()[0]
                    mismatches.append({"code": code, "year": yr, "note": note})
        except Exception as e:
            stats["errors"] += 1
            log.warning(f"{code}: {e}")

        if i % 50 == 0:
            conn.commit()
            log.info(f"[{i}/{len(codes)}] {stats}")

    conn.commit()
    if own_conn:
        conn.close()

    log.info(f"완료: {stats}")
    log.info(f"불일치 {len(mismatches)}건:")
    for m in mismatches:
        log.info(f"  {m['code']} {m['year']}: {m['note']}")

    return {"target_count": len(codes), "stats": stats, "mismatches": mismatches}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=9999)
    args = ap.parse_args()
    run_verify_sweep(limit=args.limit)


if __name__ == "__main__":
    main()
