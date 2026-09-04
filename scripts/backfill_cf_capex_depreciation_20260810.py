"""
cash_flow_data capex/depreciation 전체 백필 (2026-08-10)

근본원인 4건 수정 완료(collectors/dart_collector.py _parse_cf_df):
1. capex: _CAPEX_PPE_IDS에 주력자산+기타자산 계정이 함께 등록돼 있는데 단순 덮어쓰기라
   서로 다른 PP&E 취득 항목 중 하나가 누락됨 — 합산으로 수정.
2. depreciation: "대손상각비"(수취채권 대손, 감가상각과 무관)가 account_id 미매핑이라
   bare "상각비" 키워드에 오매칭 — 000020 실측: 진짜 감가상각비 163.3억을 0으로 덮어씀.
3. depreciation: "사용권자산손상차손"이 DART 원문 자체에서 account_id
   ifrs-full_DepreciationRightofuseAssets로 잘못 태깅돼(필자측 XBRL 오류 추정) resolve_field가
   depreciation으로 오매핑 — account_nm 텍스트("손상"/"대손")로 추가 배제.
4. depreciation: PP&E/무형자산/사용권자산/투자부동산 감가상각을 별도 행으로 공시하는 회사에서
   기존 "첫값 보호"만으로는 나머지 자산군이 누락돼 총액이 최대 1/3 수준으로 과소집계 — 합산.
   +부수: _CF_MAP 키워드 폴백에 재무제표유형 필터가 없어 손익계산서의 SG&A 감가상각
   세부내역까지 이중계상되던 버그도 함께 수정(현금흐름표 행만 허용).

이 스크립트는 기존 cash_flow_data의 capex/depreciation 125,003개 (종목,연도,분기,보고서유형)
조합을 고쳐진 _parse_cf_df로 재수집해 값이 바뀌는 것만 UPDATE. 최신 연도부터 우선 처리
(텐버거 이익의질 신호 등 최근 데이터 활용도가 높은 순).

실행: python3 scripts/backfill_cf_capex_depreciation_20260810.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_compat import install_sqlite_primary_router

install_sqlite_primary_router()

from dart_key_manager import RotatingOpenDartReader
from collectors.dart_collector import _parse_cf_df, _RPRT_CODE

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
    targets = conn.execute("""
        SELECT DISTINCT stock_code, year, quarter, is_annual, report_type
        FROM cash_flow_data
        WHERE depreciation IS NOT NULL OR capex IS NOT NULL
        ORDER BY year DESC, quarter DESC, stock_code
    """).fetchall()
    if args.limit:
        targets = targets[: args.limit]
    log.info(f"대상 {len(targets)}건(종목·연도·분기·보고서유형 조합, 최신순)")

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

        parsed = _parse_cf_df(df, code)
        new_capex = parsed.get("capex")
        new_dep = parsed.get("depreciation")

        old_row = conn.execute(
            "SELECT capex, depreciation FROM cash_flow_data WHERE stock_code=? AND year=? "
            "AND quarter=? AND is_annual=? AND report_type=?",
            (code, year, quarter, is_annual, report_type),
        ).fetchone()
        old_capex, old_dep = old_row if old_row else (None, None)

        capex_changed = (new_capex is not None and (old_capex is None or abs(new_capex - old_capex) > 0.5))
        dep_changed = (new_dep is not None and (old_dep is None or abs(new_dep - old_dep) > 0.5))

        if capex_changed or dep_changed:
            log.info(
                f"[{code} {year}Q{quarter} {report_type}] "
                f"capex {old_capex}->{new_capex if capex_changed else '(무변경)'} / "
                f"depreciation {old_dep}->{new_dep if dep_changed else '(무변경)'}"
            )
            if not args.dry_run:
                conn.execute(
                    "UPDATE cash_flow_data SET capex=COALESCE(?, capex), "
                    "depreciation=COALESCE(?, depreciation) "
                    "WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                    (new_capex if capex_changed else None, new_dep if dep_changed else None,
                     code, year, quarter, is_annual, report_type),
                )
            changed += 1
        else:
            unchanged += 1

        if not args.dry_run and (i + 1) % 25 == 0:
            conn.commit()
        if (i + 1) % 200 == 0:
            log.info(f"진행 {i+1}/{len(targets)} — 변경 {changed}건, 무변경 {unchanged}건, 오류 {errors}건")
        time.sleep(0.15)

    if not args.dry_run:
        conn.commit()
    conn.close()
    log.info(f"완료: 총 {len(targets)}건 — 변경 {changed} / 무변경 {unchanged} / 오류 {errors}")


if __name__ == "__main__":
    main()
