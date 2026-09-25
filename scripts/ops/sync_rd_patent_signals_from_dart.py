#!/usr/bin/env python3
"""dart_disclosures → dart_rd_patent_signals 증분 동기화 + 부정/무관 공시 제외 표시 (2026-09-24).

배경:
  * dart_rd_patent_signals도 SQLite→PG 동기화(sync_tenbagger_postgres.py, 스케줄러 미등록)로만
    채워져 PG 전환 후 2026-07-09에서 멈췄다. tenbagger_engine은 1년 내 기술이전+3/특허+2/R&D+1점을 준다.
  * 공시명 키워드 매칭이라 '특허 침해 소송', '기술도입 계약 해지' 같은 부정/무관 공시 44건이
    patent/license 등 긍정 신호로 들어가 있었다.

동작:
  1) exclude_reason 컬럼 추가 — 소송·판결·해지·철회 등이면 사유 기록(행은 보존, signal_type 불변).
  2) 기존 적재 최신일 이후 dart_disclosures 공시를 분류해 삽입(한 공시가 여러 유형 가능).
라이브 소비처는 `exclude_reason IS NULL` 조건으로 읽는다. 백테스트 소비처는 기존 그대로.
refresh_dart_disclosures_recent.py 끝에서 자동 호출된다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

KEYWORDS = r"특허|기술이전|기술수출|기술도입|라이선스|라이센스|실시권|공동연구|공동개발|연구개발|국책과제|기술협력"
_EXCLUDE = [
    ("lawsuit", re.compile(r"소송|판결|가처분|심판|항소|분쟁|침해")),
    ("termination", re.compile(r"해지|취소|철회|종료|반환|무효|중단")),
]


def exclude_reason(report_nm: str | None) -> str | None:
    n = "".join(str(report_nm or "").split())
    for reason, pat in _EXCLUDE:
        if pat.search(n):
            return reason
    return None


def classify(report_nm: str | None) -> list[str]:
    """기존 어휘(patent/tech_transfer/license/rd_contract)로 분류. 부정 공시도 유형은 매긴다."""
    n = "".join(str(report_nm or "").split())
    types: list[str] = []
    if "특허" in n and ("취득" in n or "등록" in n):
        types.append("patent")
    if re.search(r"기술이전|기술수출|라이선스아웃|마일스톤", n) or "기술도입ㆍ이전" in n:
        types.append("tech_transfer")
    if re.search(r"기술도입|라이선스인|라이센스인|실시권", n) or (
        re.search(r"라이선스|라이센스", n) and "tech_transfer" not in types
    ):
        types.append("license")
    if re.search(r"공동연구|공동개발|연구개발|국책과제|기술협력", n):
        types.append("rd_contract")
    return types


def sync(conn, dry_run: bool = False) -> dict:
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='dart_rd_patent_signals'"
    ).fetchall()}
    if "exclude_reason" not in cols and not dry_run:
        conn.execute("ALTER TABLE dart_rd_patent_signals ADD COLUMN exclude_reason TEXT")

    since = conn.execute(
        "SELECT COALESCE(MAX(rcept_dt), '0000-00-00') FROM dart_rd_patent_signals WHERE rcept_dt LIKE '____-__-__%'"
    ).fetchone()[0]
    cand = conn.execute(f"""
        SELECT d.stock_code, d.rcept_no, d.rcept_dt, d.report_nm
        FROM dart_disclosures d
        -- rcept_dt >= : 같은 날 늦게 수집된 공시도 포함(중복은 ON CONFLICT)
        WHERE d.rcept_dt >= ? AND d.stock_code IS NOT NULL AND d.stock_code <> ''
          AND d.report_nm ~ '{KEYWORDS}'
    """, (since,)).fetchall()
    inserted = 0
    for code, rno, rdt, nm in cand:
        for st in classify(nm):
            inserted += 1
            if dry_run:
                continue
            conn.execute(
                """INSERT INTO dart_rd_patent_signals(stock_code, rcept_no, rcept_dt, report_nm, signal_type, notes, exclude_reason)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT (rcept_no, signal_type) DO NOTHING""",
                (code, rno, str(rdt)[:10], nm, st, "dart_disclosures sync 2026-09-24", exclude_reason(nm)),
            )

    marked = 0
    if not dry_run:
        for rid, nm, cur in conn.execute(
            "SELECT id, report_nm, exclude_reason FROM dart_rd_patent_signals"
        ).fetchall():
            r = exclude_reason(nm)
            if r != cur:
                conn.execute("UPDATE dart_rd_patent_signals SET exclude_reason=? WHERE id=?", (r, rid))
                marked += 1
        conn.commit()
    last = conn.execute("SELECT MAX(rcept_dt) FROM dart_rd_patent_signals").fetchone()[0]
    return {"inserted": inserted, "inserted_since": since, "exclude_marked": marked,
            "max_rcept_dt": last, "dry_run": dry_run}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = connect_primary_db(timeout=60)
    try:
        print(sync(conn, args.dry_run))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
