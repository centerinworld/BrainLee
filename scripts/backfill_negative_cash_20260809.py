"""
financial_data.cash 음수값 백필 (2026-08-09)

근본원인(collectors/dart_collector.py 수정 완료, 이 스크립트는 기존 오염 데이터 재수집):
DART 현금흐름표의 "현금및현금성자산의순증가(감소)"(계정id
ifrs-full_IncreaseDecreaseInCashAndCashEquivalents, 매핑테이블 미등록)가 account_nm에
"현금및현금성자산"을 포함해 재무상태표 잔액과 혼동되어 저장된 사례. 이 값은 기간 중
증감액이라 음수가 정상이나, 재무상태표 현금잔액(항상 0 이상)과 혼동되면 안 됨.
_parse_fin_df에 "cash"도 재무상태표 행만 허용하도록 방어 추가 완료 — 이 스크립트는
이미 오염된 기존 2,484종목·16,730행을 고쳐진 파서로 재수집한다.

실행: python3 scripts/backfill_negative_cash_20260809.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import logging
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
        return "11011"  # 사업보고서(연간) — Q4/연간 모두 연말 시점 잔액이라 동일 소스 사용
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
        FROM financial_data WHERE cash < 0
        ORDER BY stock_code, year, quarter
    """).fetchall()
    if args.limit:
        targets = targets[:args.limit]
    log.info(f"대상 {len(targets)}건 (종목·연도·분기·보고서유형 조합 단위)")

    rdr = RotatingOpenDartReader()
    fixed = fixed_to_null = skipped = errors = 0

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
            # status!=000(데이터없음 등) — 되살릴 수 없으므로 NULL 처리(음수보다 안전)
            new_cash = None
        else:
            parsed = _parse_fin_df(df, code)
            new_cash = parsed.get("cash")
            if new_cash is not None and new_cash < 0:
                # 그래도 음수면 여전히 신뢰불가 — NULL 처리(재발방지 코드 이후에도 발생하면
                # 별도 조사 필요하므로 경고 로그)
                log.warning(f"[{code} {year}Q{quarter} {report_type}] 재파싱 후에도 음수: {new_cash}")
                new_cash = None

        if args.dry_run:
            log.info(f"[dry-run] {code} {year}Q{quarter} {report_type} -> cash={new_cash}")
        else:
            conn.execute(
                "UPDATE financial_data SET cash=? WHERE stock_code=? AND year=? AND quarter=? "
                "AND is_annual=? AND report_type=?",
                (new_cash, code, year, quarter, is_annual, report_type),
            )
            # 2026-08-09: 매건 commit()이 운영서버(1분마다 KIS시세 등 빈번한 짧은 쓰기)와
            # 락 경합해 건당 최대 27초까지 대기하는 것을 실측 확인(CLAUDE.md에 이미 기록된
            # "장시간 락 보유 기아" 패턴과 동일 클래스) — 25건 단위 배치커밋으로 락 획득
            # 횟수 자체를 줄여 총 대기 노출을 최소화.
            if (i + 1) % 25 == 0:
                conn.commit()

        if new_cash is None:
            fixed_to_null += 1
        else:
            fixed += 1

        if (i + 1) % 100 == 0:
            log.info(f"진행 {i+1}/{len(targets)} — 수정 {fixed}건, NULL처리 {fixed_to_null}건, 오류 {errors}건")
        time.sleep(0.15)

    if not args.dry_run:
        conn.commit()
    conn.close()
    log.info(f"완료: 총 {len(targets)}건 — 수정 {fixed} / NULL처리 {fixed_to_null} / 오류 {errors}")


if __name__ == "__main__":
    main()
