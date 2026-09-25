#!/usr/bin/env python3
"""dart_disclosures → treasury_buyback 증분 동기화 + event_class 정규화 (2026-09-24).

배경:
  * treasury_buyback은 SQLite→PG 동기화(scripts/sync_tenbagger_postgres.py)로만 채워졌고
    PG 전환 후 신규 적재가 끊겨 MAX(rcept_dt)=2026-07-10에서 멈춰 있었다
    (같은 기간 dart_disclosures에는 자기주식 공시 448건).
  * event_type이 두 작성자의 어휘(취득결정/처분결과/… vs acquisition/trust/disposal/…)로
    섞여 있고, 신탁계약 체결(=자사주 매입)은 'trust' 또는 '기타'로 흩어져 있어
    tenbagger_engine의 `event_type IN ('취득결정','취득결과')`가 상당수를 놓쳤다.

동작:
  1) event_class 컬럼(정규 분류)을 추가하고 전 행을 report_nm 기준으로 재분류.
     event_type은 백테스트 재현성을 위해 건드리지 않는다.
  2) dart_disclosures의 자기주식/주식소각 공시 중 없는 rcept_no를 삽입.
     신규 행의 event_type은 기존 한글 어휘(신탁 체결·해지는 기존처럼 '기타').
  3) rcept_dt 'YYYY.MM.DD' 표기를 'YYYY-MM-DD'로 정규화.

event_class: 취득결정 · 취득결과 · 신탁체결 · 신탁해지 · 처분결정 · 처분결과 · 소각 · 기타(철회/취소 포함)
매수 신호로 쓸 것: 취득결정 · 취득결과 · 신탁체결.

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/sync_treasury_buyback_from_dart.py [--dry-run]
refresh_dart_disclosures_recent.py 끝에서 자동 호출된다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

BUY_CLASSES = ("취득결정", "취득결과", "신탁체결")
_LEGACY_TYPE = {"신탁체결": "기타", "신탁해지": "기타"}


def classify(report_nm: str | None) -> str:
    n = "".join(str(report_nm or "").split())
    if "철회" in n or "취소" in n:
        return "기타"
    if "소각" in n:
        return "소각"
    if "신탁" in n and "해지" in n:
        return "신탁해지"
    if "신탁" in n and "체결" in n and "취득" in n:
        return "신탁체결"
    if "취득결과" in n:
        return "취득결과"
    if "취득결정" in n or ("자기주식" in n and "취득" in n and "신탁" not in n):
        return "취득결정"
    if "처분결과" in n:
        return "처분결과"
    if "처분" in n:
        return "처분결정"
    return "기타"


def sync(conn, dry_run: bool = False, backfill_history: bool = False) -> dict:
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='treasury_buyback'"
    ).fetchall()}
    if "event_class" not in cols and not dry_run:
        conn.execute("ALTER TABLE treasury_buyback ADD COLUMN event_class TEXT")

    # 3) 날짜 표기 정규화
    dotted = conn.execute(
        "SELECT COUNT(*) FROM treasury_buyback WHERE rcept_dt LIKE '____.__.__%'"
    ).fetchone()[0]
    if dotted and not dry_run:
        conn.execute("UPDATE treasury_buyback SET rcept_dt = replace(substr(rcept_dt,1,10), '.', '-') "
                     "WHERE rcept_dt LIKE '____.__.__%'")

    # 2) 누락 공시 삽입 — 기본은 기존 적재 최신일 이후(파이프라인 중단 구간)만.
    #    과거 누락분(약 3,300건)은 백테스트 입력을 바꾸므로 --backfill-history로 명시할 때만.
    since = "0000-00-00"
    if not backfill_history:
        since = conn.execute(
            "SELECT COALESCE(MAX(rcept_dt), '0000-00-00') FROM treasury_buyback "
            "WHERE rcept_dt LIKE '____-__-__%' AND rcept_no IN (SELECT rcept_no FROM dart_disclosures)"
        ).fetchone()[0]
    cand = conn.execute("""
        SELECT d.stock_code, d.corp_name, d.rcept_no, d.rcept_dt, d.report_nm
        FROM dart_disclosures d
        WHERE (d.report_nm LIKE '%자기주식%' OR d.report_nm LIKE '%주식소각%')
          AND d.stock_code IS NOT NULL AND d.stock_code <> ''
          AND d.rcept_dt >= ?  -- 같은 날 늦게 수집된 공시 포함(중복은 NOT EXISTS/ON CONFLICT)
          AND NOT EXISTS (SELECT 1 FROM treasury_buyback t
                          WHERE t.stock_code = d.stock_code AND t.rcept_no = d.rcept_no)
    """, (since,)).fetchall()
    inserted = 0
    for code, corp, rno, rdt, nm in cand:
        cls = classify(nm)
        if dry_run:
            inserted += 1
            continue
        conn.execute(
            """INSERT INTO treasury_buyback(stock_code, corp_name, rcept_no, rcept_dt, event_type, report_nm, event_class)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT (stock_code, rcept_no) DO NOTHING""",
            (code, corp, rno, str(rdt)[:10], _LEGACY_TYPE.get(cls, cls), nm, cls),
        )
        inserted += 1

    # 1) event_class 재분류 (전 행, 변경분만)
    reclassified = 0
    if not dry_run:
        rows = conn.execute("SELECT id, report_nm, event_class FROM treasury_buyback").fetchall()
        for rid, nm, cur in rows:
            cls = classify(nm)
            if cls != cur:
                conn.execute("UPDATE treasury_buyback SET event_class=? WHERE id=?", (cls, rid))
                reclassified += 1
        conn.commit()
    dist = dict(conn.execute(
        "SELECT COALESCE(event_class,'(null)'), COUNT(*) FROM treasury_buyback GROUP BY 1"
    ).fetchall()) if "event_class" in cols or not dry_run else {}
    last = conn.execute("SELECT MAX(rcept_dt) FROM treasury_buyback").fetchone()[0]
    return {"inserted": inserted, "inserted_since": since, "reclassified": reclassified, "dotted_dates_fixed": dotted,
            "max_rcept_dt": last, "event_class": dist, "dry_run": dry_run}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill-history", action="store_true",
                    help="기존 최신일 이전의 과거 누락 공시까지 삽입(백테스트 입력 변경)")
    args = ap.parse_args()
    conn = connect_primary_db(timeout=60)
    try:
        print(sync(conn, args.dry_run, args.backfill_history))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
