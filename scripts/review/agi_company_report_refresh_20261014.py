#!/usr/bin/env python3
"""AGI-06 기업 보고서 — 매일 재생성(자동 갱신 트리거, 2026-10-14).

설계: docs/AGI_06_COMPANY_REPORT_DESIGN_20261013.md §3(갱신 트리거). 실적 발표·공시·
애널리스트 추정·사업 포트폴리오·비교군 재구성 "이벤트"를 개별로 감지하지 않는다 —
`agi.company_report.generate_and_store_report()` 가 섹션마다 **콘텐츠 해시**로 바뀐
것만 새 revision을 만들므로, 그냥 매일 다시 조립하는 것 자체가 트리거다(바뀐 데이터가
없으면 그대로 `unchanged`로 끝나 쓰기 0건). 이 스크립트는 "언제 다시 조립할지"만
결정한다.

대상 종목: `company_reports`에 이미 있는 종목(지금까지 생성된 전부) ∪ 현재 `portfolio`
보유 종목(새로 산 종목은 다음날 자동 편입) ∪ `buy_candidates`(관심 후보도 같이 추적).

실행: venv/bin/python scripts/review/agi_company_report_refresh_20261014.py
launchd: com.stock-dashboard.agi-company-report-refresh(매일, 숫자 데이터 수집 이후).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agi.company_report import generate_and_store_report  # noqa: E402
from agi.db import connect_primary  # noqa: E402


def _target_codes(conn) -> list[str]:
    codes: set[str] = set()
    for row in conn.execute("SELECT DISTINCT stock_code FROM company_reports").fetchall():
        codes.add(str(row["stock_code"]))
    for row in conn.execute("SELECT DISTINCT stock_code FROM portfolio").fetchall():
        codes.add(str(row["stock_code"]))
    for row in conn.execute("SELECT DISTINCT stock_code FROM buy_candidates").fetchall():
        codes.add(str(row["stock_code"]))
    return sorted(codes)


def main() -> int:
    conn = connect_primary(timeout=120)
    run_tag = f"scheduled_refresh_{datetime.now():%Y%m%d_%H%M%S}"
    codes = _target_codes(conn)
    print(f"대상 {len(codes)}종목, trigger_reason={run_tag}")

    changed_sections = 0
    unchanged_sections = 0
    failed: list[tuple[str, str]] = []
    new_codes = 0
    for code in codes:
        try:
            result = generate_and_store_report(conn, code, trigger_reason=run_tag)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 한 종목 실패가 나머지를 막지 않는다
            conn.rollback()
            failed.append((code, str(exc)[:200]))
            continue
        kinds = [s["change_kind"] for s in result["sections"].values()]
        if "new" in kinds and len(set(kinds)) == 1:
            new_codes += 1
        changed_sections += sum(1 for k in kinds if k != "unchanged")
        unchanged_sections += sum(1 for k in kinds if k == "unchanged")

    print(
        f"완료: 신규 {new_codes}종목, 변경 섹션 {changed_sections}, 변경 없음 {unchanged_sections}, "
        f"실패 {len(failed)}"
    )
    for code, err in failed[:10]:
        print(f"  실패 {code}: {err}")
    conn.close()
    return 1 if failed and len(failed) == len(codes) else 0


if __name__ == "__main__":
    sys.exit(main())
