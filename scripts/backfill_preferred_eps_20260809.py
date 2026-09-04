"""
EPS 우선주/보통주 오매칭 백필 (2026-08-09(2차))

근본원인(collectors/dart_collector.py 수정 완료): "기본주당이익" 키워드가 "우선주 기본주당이익"과
"보통주기본주당이익" 둘 다에 매칭되는데, 회사에 따라 우선주 행이 보통주 행보다 DataFrame에서
먼저 등장하면(현대건설 실측 확인) 잘못된(우선주) EPS가 고정 저장되고 그 뒤 진짜 보통주 행은
"이미 세팅된 값 보호" 로직에 막혀 덮어쓰지 못함. _parse_fin_df에 "우선주" exclude 추가 완료 —
이 스크립트는 우선주를 발행한 것으로 확인된 75개 모회사(보통주)의 기존 financial_data.eps를
고쳐진 파서로 재수집한다.

실행: python3 scripts/backfill_preferred_eps_20260809.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dart_key_manager import RotatingOpenDartReader
from collectors.dart_collector import _parse_fin_df, _RPRT_CODE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).resolve().parents[1] / "stock.db")


def _rprt_code(is_annual: int, quarter: int) -> str:
    if is_annual or quarter == 4:
        return "11011"
    return _RPRT_CODE.get(quarter, "11011")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")

    pref = conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_name GLOB '*우' OR stock_name GLOB '*우[A-Z]'"
    ).fetchall()
    parent_names = {re.sub(r"\d?우[A-Z]?$", "", r[0]) for r in pref}
    placeholders = ",".join("?" for _ in parent_names)
    common_codes = [r[0] for r in conn.execute(
        f"""SELECT stock_code FROM stock_universe
            WHERE stock_name IN ({placeholders})
              AND market IN ('유가증권','코스닥','KOSPI','KOSDAQ')""",
        list(parent_names),
    ).fetchall()]
    log.info(f"우선주 발행 모회사(보통주) {len(common_codes)}개")

    targets = conn.execute(f"""
        SELECT DISTINCT stock_code, year, quarter, is_annual, report_type
        FROM financial_data
        WHERE stock_code IN ({",".join("?" for _ in common_codes)})
          AND eps IS NOT NULL
        ORDER BY stock_code, year, quarter
    """, common_codes).fetchall()
    if args.limit:
        targets = targets[: args.limit]
    log.info(f"대상 {len(targets)}건(종목·연도·분기·보고서유형 조합)")

    rdr = RotatingOpenDartReader()
    changed = unchanged = errors = 0

    for i, (code, year, quarter, is_annual, report_type) in enumerate(targets):
        rprt = _rprt_code(is_annual, quarter)
        try:
            df = rdr.finstate_all(code, int(year), rprt, fs_div=report_type)
        except Exception as e:
            errors += 1
            log.warning(f"[{code} {year}Q{quarter} {report_type}] API 오류: {e}")
            time.sleep(1.0)
            continue

        if df is None or (hasattr(df, "empty") and df.empty) or isinstance(df, dict):
            continue

        parsed = _parse_fin_df(df, code)
        new_eps = parsed.get("eps")

        old_row = conn.execute(
            "SELECT eps FROM financial_data WHERE stock_code=? AND year=? AND quarter=? "
            "AND is_annual=? AND report_type=?",
            (code, year, quarter, is_annual, report_type),
        ).fetchone()
        old_eps = old_row[0] if old_row else None

        if new_eps is not None and old_eps is not None and abs(new_eps - old_eps) > 0.5:
            log.info(f"[{code} {year}Q{quarter} {report_type}] eps {old_eps} -> {new_eps}")
            if not args.dry_run:
                conn.execute(
                    "UPDATE financial_data SET eps=? WHERE stock_code=? AND year=? AND quarter=? "
                    "AND is_annual=? AND report_type=?",
                    (new_eps, code, year, quarter, is_annual, report_type),
                )
            changed += 1
        else:
            unchanged += 1

        if not args.dry_run and (i + 1) % 25 == 0:
            conn.commit()
        if (i + 1) % 100 == 0:
            log.info(f"진행 {i+1}/{len(targets)} — 변경 {changed}건, 무변경 {unchanged}건, 오류 {errors}건")
        time.sleep(0.15)

    if not args.dry_run:
        conn.commit()
    conn.close()
    log.info(f"완료: 총 {len(targets)}건 — 변경 {changed} / 무변경 {unchanged} / 오류 {errors}")


if __name__ == "__main__":
    main()
