"""
collect_dart_cashflow_batch.py — 전종목 DART 현금흐름표 배치 수집

financial_data에 있는 종목(DART 공시 종목)에 대해
cash_flow_data 테이블을 채운다. 이미 있는 행은 건너뜀.

사용법:
  python3 collect_dart_cashflow_batch.py             # 전종목 (약 2500종목)
  python3 collect_dart_cashflow_batch.py --years 3   # 최근 3년만
  python3 collect_dart_cashflow_batch.py --missing   # cash_flow_data 전혀 없는 종목만
  python3 collect_dart_cashflow_batch.py --fill-missing  # 최근 N년 중 비어 있는 기간이 있는 종목
  python3 collect_dart_cashflow_batch.py --refill-depr   # depreciation=NULL 행만 재수집
  python3 collect_dart_cashflow_batch.py --limit 100 # 상위 N종목만
"""
import argparse
import contextlib
import logging
import os
import sqlite3
import time
from datetime import date

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH  = "/Applications/stock_dashboard/stock.db"
RATE_SEC = 0.8   # DART API rate limit


def _get_dart():
    import config as _cfg
    import OpenDartReader
    return OpenDartReader(_cfg.DART_API_KEY)


def _latest_quarter() -> tuple[int, int]:
    """현재 공시된 최신 분기 (year, quarter)."""
    today = date.today()
    y, m = today.year, today.month
    if m < 5:   return y - 1, 4
    elif m < 8: return y, 1
    elif m < 11: return y, 2
    else:        return y, 3


_DEPR_KEYWORDS = [
    "감가상각비",
    "유형자산상각비",
    "유형자산의감가상각비",
    "유형자산감가상각비",
    "사용권자산감가상각비",
    "사용권자산의감가상각비",
    "사용권자산상각비",
    "투자부동산의감가상각비",
    "투자부동산감가상각비",
    "무형자산상각비",
    "무형자산의상각비",
]

_PPE_KEYWORDS = ["유형자산", "유형자산합계", "유형자산순액"]


def _is_sparse_cashflow_row(row: sqlite3.Row | tuple | None) -> bool:
    if row is None:
        return True
    values = row[0:6]
    return all(v is None for v in values)


def _has_null_depreciation(row: sqlite3.Row | tuple | None) -> bool:
    if row is None:
        return True
    # row index 5 = depreciation
    return row[5] is None


def _parse_val(row, col: str) -> float | None:
    """DataFrame row에서 숫자 추출. 실패 시 None."""
    v = row.get(col)
    if v is None or (hasattr(v, '__class__') and pd.isna(v)):
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def collect_one(
    dart,
    conn: sqlite3.Connection,
    code: str,
    years: int,
    refill_sparse: bool = False,
    refill_depr: bool = False,
    refill_cash: bool = False,
) -> int:
    """단일 종목 현금흐름표 수집. 반환: 저장 건수."""
    latest_y, latest_q = _latest_quarter()
    qmap = {"11011": 4, "11013": 1, "11012": 2, "11014": 3}
    saved = 0

    for year in range(latest_y, latest_y - years, -1):
        for rcode, qnum in qmap.items():
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue
            is_annual = 1 if rcode == "11011" else 0

            # 스킵 조건
            exists = conn.execute(
                """
                SELECT operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation
                FROM cash_flow_data
                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                """,
                (code, year, qnum, is_annual)
            ).fetchone()

            skip = False
            if exists:
                if refill_depr and _has_null_depreciation(exists):
                    skip = False   # depreciation 재시도
                elif refill_sparse and _is_sparse_cashflow_row(exists):
                    skip = False
                else:
                    skip = True
            if skip:
                continue

            fn_data = None
            for fs in ["CFS", "OFS"]:
                try:
                    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
                        df = dart.finstate_all(code, year, rcode, fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df
                        break
                except Exception:
                    pass
                time.sleep(0.2)

            if fn_data is None or fn_data.empty:
                continue

            m = {k: None for k in ["operating_cf", "investing_cf", "financing_cf",
                                    "capex", "cash_end", "depreciation"]}
            depr_sum = 0.0
            depr_found = False

            # PP&E(유형자산) 당기/전기 — BS fallback용
            ppe_curr: float | None = None
            ppe_prev: float | None = None

            for _, row in fn_data.iterrows():
                acc = str(row.get("account_nm", "")).replace(" ", "")
                sj  = str(row.get("sj_div", "")).strip()  # CF / BS / IS 등
                if not acc:
                    continue

                # ── CF 항목 ────────────────────────────────────────────────
                val = _parse_val(row, "thstrm_amount")
                if val is not None:
                    if acc in ("영업활동현금흐름", "영업활동으로인한현금흐름"):
                        m["operating_cf"] = val
                    elif acc in ("투자활동현금흐름", "투자활동으로인한현금흐름"):
                        m["investing_cf"] = val
                    elif acc in ("재무활동현금흐름", "재무활동으로인한현금흐름"):
                        m["financing_cf"] = val
                    elif any(k in acc for k in ["유형자산의취득", "유형자산취득"]):
                        if m["capex"] is None:
                            m["capex"] = abs(val)
                    elif any(k in acc for k in [
                        "현금및현금성자산의기말잔액", "기말의현금및현금성자산",
                        "현금및현금성자산기말잔액", "기말현금및현금성자산",
                        "현금및현금성자산의기말", "기말의현금",
                    ]):
                        m["cash_end"] = val
                    # 감가상각: 여러 항목 합산 (주요 항목만 — 중간계 항목 제외)
                    elif any(kw == acc for kw in _DEPR_KEYWORDS):
                        depr_sum += abs(val)
                        depr_found = True
                    # 부분문자열 매칭 (키워드 포함하지만 정확히 일치하지 않는 경우)
                    elif any(kw in acc for kw in _DEPR_KEYWORDS) and sj == "CF":
                        # "감가상각비및상각비" 같은 집계 계정 → 아직 depr_found 안됐을 때만
                        if not depr_found:
                            depr_sum = max(depr_sum, abs(val))
                            depr_found = True

                # ── BS 항목 (PP&E fallback용) ──────────────────────────────
                if sj == "BS" and any(kw in acc for kw in _PPE_KEYWORDS):
                    # 정확히 "유형자산" 또는 "유형자산합계"인 행을 선호
                    is_exact = acc in ("유형자산", "유형자산합계", "유형자산순액")
                    curr_val = _parse_val(row, "thstrm_amount")
                    prev_val = _parse_val(row, "frmtrm_amount")
                    if curr_val is not None and (ppe_curr is None or is_exact):
                        ppe_curr = curr_val
                    if prev_val is not None and (ppe_prev is None or is_exact):
                        ppe_prev = prev_val

            if depr_found:
                m["depreciation"] = depr_sum if depr_sum > 0 else None
            elif ppe_prev is not None and ppe_curr is not None:
                # PP&E 변화로 감가상각 추정: D ≈ PPE_prev + CAPEX - PPE_curr
                capex_val = m["capex"] or 0.0
                estimated = ppe_prev + capex_val - ppe_curr
                if estimated > 0:
                    m["depreciation"] = estimated
                    logger.debug(f"[{code}] {year}Q{qnum} 감가상각 추정: {estimated:.0f} (PPE법)")

            # refill_depr 모드: depreciation만 업데이트
            if refill_depr and exists and not _is_sparse_cashflow_row(exists):
                if m["depreciation"] is not None:
                    for _attempt in range(5):
                        try:
                            conn.execute("""
                                UPDATE cash_flow_data SET depreciation=?
                                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                            """, (m["depreciation"], code, year, qnum, is_annual))
                            saved += 1
                            break
                        except Exception as e:
                            if "locked" in str(e).lower() and _attempt < 4:
                                time.sleep(2 ** _attempt)
                                continue
                            logger.warning(f"[{code}] {year}Q{qnum} depr 업데이트 실패: {e}")
                            break
                continue

            # refill_cash 모드: cash_end만 업데이트
            if refill_cash and exists and not _is_sparse_cashflow_row(exists):
                if m["cash_end"] is not None:
                    for _attempt in range(5):
                        try:
                            conn.execute("""
                                UPDATE cash_flow_data SET cash_end=?
                                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                            """, (m["cash_end"], code, year, qnum, is_annual))
                            saved += 1
                            break
                        except Exception as e:
                            if "locked" in str(e).lower() and _attempt < 4:
                                time.sleep(2 ** _attempt)
                                continue
                            logger.warning(f"[{code}] {year}Q{qnum} cash_end 업데이트 실패: {e}")
                            break
                continue

            if all(v is None for v in m.values()):
                continue

            for _attempt in range(5):
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO cash_flow_data
                          (stock_code, year, quarter, is_annual,
                           operating_cf, investing_cf, financing_cf,
                           capex, cash_end, depreciation)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                    """, (
                        code, year, qnum, is_annual,
                        m["operating_cf"], m["investing_cf"], m["financing_cf"],
                        m["capex"], m["cash_end"], m["depreciation"],
                    ))
                    saved += 1
                    break
                except Exception as e:
                    if "locked" in str(e).lower() and _attempt < 4:
                        time.sleep(2 ** _attempt)
                        continue
                    logger.warning(f"[{code}] {year}Q{qnum} 저장 실패: {e}")
                    break

    if saved:
        conn.commit()
    return saved


def _expected_periods(years: int) -> list[tuple[int, int, int]]:
    latest_y, latest_q = _latest_quarter()
    qnums = [4, 1, 2, 3]
    periods: list[tuple[int, int, int]] = []
    for year in range(latest_y, latest_y - years, -1):
        for qnum in qnums:
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue
            periods.append((year, qnum, 1 if qnum == 4 else 0))
    return periods


def _codes_with_missing_periods(conn: sqlite3.Connection, codes: list[str], years: int) -> list[str]:
    periods = _expected_periods(years)
    sparse_codes: list[str] = []
    for code in codes:
        existing = set(
            conn.execute(
                """
                SELECT year, quarter, is_annual
                FROM cash_flow_data
                WHERE stock_code=?
                """,
                (code,),
            ).fetchall()
        )
        if any(period not in existing for period in periods):
            sparse_codes.append(code)
    return sparse_codes


def _eligible_stock_codes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        WITH market_codes AS (
            SELECT stock_code
            FROM stock_meta
            WHERE UPPER(COALESCE(market, '')) IN ('KOSPI', 'KOSDAQ')
            UNION
            SELECT stock_code
            FROM stock_universe
            WHERE market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
              AND COALESCE(stock_type, '보통주') = '보통주'
        )
        SELECT DISTINCT f.stock_code
        FROM financial_data f
        JOIN market_codes m ON m.stock_code = f.stock_code
        WHERE LENGTH(f.stock_code)=6 AND f.stock_code GLOB '[0-9]*'
        ORDER BY f.stock_code
    """).fetchall()
    return [r[0] for r in rows]


def _codes_with_null_depreciation(conn: sqlite3.Connection, codes: list[str], years: int) -> list[str]:
    """depreciation=NULL인 행이 존재하는 종목 목록."""
    periods = _expected_periods(years)
    result = []
    for code in codes:
        rows = conn.execute(
            """
            SELECT year, quarter, is_annual, depreciation
            FROM cash_flow_data
            WHERE stock_code=?
            """,
            (code,)
        ).fetchall()
        existing_map = {(r[0], r[1], r[2]): r[3] for r in rows}
        for (y, q, ia) in periods:
            if (y, q, ia) in existing_map and existing_map[(y, q, ia)] is None:
                result.append(code)
                break
    return result


def _codes_with_null_cash_end(conn: sqlite3.Connection, codes: list[str], years: int) -> list[str]:
    """cash_end=NULL인 행이 존재하는 종목 목록."""
    periods = [(y, 4, 1) for y in range(2022, 2026)]  # 연간 연도별만
    result = []
    for code in codes:
        rows = conn.execute(
            "SELECT year, quarter, is_annual, cash_end FROM cash_flow_data WHERE stock_code=? AND is_annual=1",
            (code,)
        ).fetchall()
        existing_map = {(r[0], r[1], r[2]): r[3] for r in rows}
        for (y, q, ia) in periods:
            if (y, q, ia) in existing_map and existing_map[(y, q, ia)] is None:
                result.append(code)
                break
    return result


def run(
    years: int = 5,
    missing_only: bool = False,
    fill_missing: bool = False,
    refill_depr: bool = False,
    refill_cash: bool = False,
    limit: int | None = None,
):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # 대상 종목: financial_data가 있고 시장구분이 코스피/코스닥인 6자리 국내 보통주
    codes = _eligible_stock_codes(conn)

    if missing_only:
        have = set(r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM cash_flow_data"
        ).fetchall())
        codes = [c for c in codes if c not in have]
    elif fill_missing:
        codes = _codes_with_missing_periods(conn, codes, years)
    elif refill_depr:
        codes = _codes_with_null_depreciation(conn, codes, years)
    elif refill_cash:
        codes = _codes_with_null_cash_end(conn, codes, years)

    if limit:
        codes = codes[:limit]

    total = len(codes)
    logger.info(
        f"대상: {total}종목 | 최근 {years}년 | "
        f"missing_only={missing_only} fill_missing={fill_missing} refill_depr={refill_depr}"
    )

    dart   = _get_dart()
    ok     = 0
    errors = 0

    for i, code in enumerate(codes, 1):
        try:
            n = collect_one(
                dart, conn, code, years,
                refill_sparse=fill_missing,
                refill_depr=refill_depr,
                refill_cash=refill_cash,
            )
            ok += n
            if n:
                logger.info(f"[{i}/{total}] {code}: {n}건 저장/수정")
        except Exception as e:
            errors += 1
            logger.warning(f"[{i}/{total}] {code} 오류: {e}")

        if i % 50 == 0:
            logger.info(f"진행: {i}/{total} | 저장 {ok}건 | 오류 {errors}건")

        time.sleep(RATE_SEC)

    conn.close()
    logger.info(f"완료: {total}종목 처리 | {ok}건 저장 | {errors}건 오류")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years",        type=int,  default=5,    help="수집 연수 (기본 5년)")
    ap.add_argument("--missing",      action="store_true",      help="cash_flow_data 없는 종목만")
    ap.add_argument("--fill-missing", action="store_true",      help="최근 N년 중 누락 기간이 있는 종목 보강")
    ap.add_argument("--refill-depr",  action="store_true",      help="depreciation=NULL인 행만 재수집/추정")
    ap.add_argument("--refill-cash",  action="store_true",      help="cash_end=NULL인 연간 행만 재수집")
    ap.add_argument("--limit",        type=int,  default=None,  help="최대 종목수")
    args = ap.parse_args()
    run(
        years=args.years,
        missing_only=args.missing,
        fill_missing=args.fill_missing,
        refill_depr=args.refill_depr,
        refill_cash=args.refill_cash,
        limit=args.limit,
    )
