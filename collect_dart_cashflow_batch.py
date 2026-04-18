"""
collect_dart_cashflow_batch.py — 전종목 DART 현금흐름표 배치 수집

financial_data에 있는 종목(DART 공시 종목)에 대해
cash_flow_data 테이블을 채운다. 이미 있는 행은 건너뜀.

사용법:
  python3 collect_dart_cashflow_batch.py             # 전종목 (약 2500종목)
  python3 collect_dart_cashflow_batch.py --years 3   # 최근 3년만
  python3 collect_dart_cashflow_batch.py --missing   # cash_flow_data 전혀 없는 종목만
  python3 collect_dart_cashflow_batch.py --limit 100 # 상위 N종목만
"""
import argparse
import logging
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


def collect_one(dart, conn: sqlite3.Connection, code: str, years: int) -> int:
    """단일 종목 현금흐름표 수집. 반환: 저장 건수."""
    latest_y, latest_q = _latest_quarter()
    qmap = {"11011": 4, "11013": 1, "11012": 2, "11014": 3}
    saved = 0

    for year in range(latest_y, latest_y - years, -1):
        for rcode, qnum in qmap.items():
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue
            is_annual = 1 if rcode == "11011" else 0

            # 이미 있으면 스킵
            exists = conn.execute(
                "SELECT 1 FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
                (code, year, qnum, is_annual)
            ).fetchone()
            if exists:
                continue

            fn_data = None
            for fs in ["CFS", "OFS"]:
                try:
                    df = dart.finstate_all(code, year, rcode, fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df
                        break
                except Exception:
                    pass
                time.sleep(0.2)

            if fn_data is None or fn_data.empty:
                continue

            m = {k: None for k in ["operating_cf","investing_cf","financing_cf",
                                    "capex","cash_end","depreciation"]}
            for _, row in fn_data.iterrows():
                acc = str(row.get("account_nm", "")).replace(" ", "")
                vc  = "thstrm_amount"
                if not acc or vc not in row or pd.isna(row[vc]):
                    continue
                try:
                    val = float(str(row[vc]).replace(",", ""))
                except Exception:
                    continue

                if acc in ("영업활동현금흐름", "영업활동으로인한현금흐름"):
                    m["operating_cf"] = val
                elif acc in ("투자활동현금흐름", "투자활동으로인한현금흐름"):
                    m["investing_cf"] = val
                elif acc in ("재무활동현금흐름", "재무활동으로인한현금흐름"):
                    m["financing_cf"] = val
                elif any(k in acc for k in ["유형자산의취득", "유형자산취득"]):
                    m["capex"] = abs(val)
                elif any(k in acc for k in ["현금및현금성자산의기말잔액", "기말의현금및현금성자산"]):
                    m["cash_end"] = val
                elif any(k in acc for k in ["감가상각비", "유형자산상각비"]):
                    m["depreciation"] = val

            if all(v is None for v in m.values()):
                continue

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
            except Exception as e:
                logger.warning(f"[{code}] {year}Q{qnum} 저장 실패: {e}")

    if saved:
        conn.commit()
    return saved


def run(years: int = 5, missing_only: bool = False, limit: int | None = None):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # 대상 종목: financial_data에 있는 6자리 국내 종목
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM financial_data
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        ORDER BY stock_code
    """).fetchall()
    codes = [r[0] for r in rows]

    if missing_only:
        have = set(r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM cash_flow_data"
        ).fetchall())
        codes = [c for c in codes if c not in have]

    if limit:
        codes = codes[:limit]

    total = len(codes)
    logger.info(f"대상: {total}종목 | 최근 {years}년 | missing_only={missing_only}")

    dart   = _get_dart()
    ok     = 0
    errors = 0

    for i, code in enumerate(codes, 1):
        try:
            n = collect_one(dart, conn, code, years)
            ok += n
            if n:
                logger.info(f"[{i}/{total}] {code}: {n}건 저장")
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
    ap.add_argument("--years",   type=int,  default=5,     help="수집 연수 (기본 5년)")
    ap.add_argument("--missing", action="store_true",       help="cash_flow_data 없는 종목만")
    ap.add_argument("--limit",   type=int,  default=None,   help="최대 종목수")
    args = ap.parse_args()
    run(years=args.years, missing_only=args.missing, limit=args.limit)
