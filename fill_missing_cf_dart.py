"""
fill_missing_cf_dart.py — DART 재수집으로 CF NULL 채우기

cash_flow_data 에서 depreciation이 NULL인 종목을 대상으로
DART finstate_all()을 다시 호출하여 감가상각비를 찾아 채운다.

- 넓은 키워드 매핑 사용 (상각비 계열 전체 합산)
- force=False: NULL 컬럼만 업데이트 (기존 값 보존)
- 연간(사업보고서) + 분기 모두 시도

사용법:
    python fill_missing_cf_dart.py              # 전체 NULL 종목
    python fill_missing_cf_dart.py --limit 50   # 상위 50종목
    python fill_missing_cf_dart.py --code 078150 # 단일 종목
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/fill_dart_cf.log"),
    ],
)
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

# DART 분기 코드
_RPRT_CODE = {4: "11011", 3: "11014", 2: "11012", 1: "11013"}

# 넓은 상각비 키워드 집합 (단순 부분 매칭 후 제외 키워드 필터)
_DEPR_KEYWORDS = [
    "감가상각비및무형자산상각비",
    "감가상각비와무형자산상각비",
    "유무형자산상각비",
    "유형및무형자산상각비",
    "감가상각비(유무형자산)",
    "감가상각비및상각비",
    "유형자산의감가상각비",
    "유형자산에대한감가상각비",
    "유형자산감가상각비",
    "유형자산상각비",
    "사용권자산에대한감가상각비",
    "사용권자산상각비",
    "사용권자산에대한상각비",
    "무형자산상각비",
    "감가상각비",
    "상각비",          # 최후 catch-all
]
_DEPR_EXCLUDE = ["대손", "처분", "손상", "취득원가", "장부금액", "평가", "기타상각"]


def _is_depr_account(acc: str) -> bool:
    """계정명이 상각비 항목인지 판별 (오탐 제외 포함)."""
    a = acc.replace(" ", "").replace("\xa0", "").replace("　", "")
    if any(ex in a for ex in _DEPR_EXCLUDE):
        return False
    # 긴 키워드 우선
    for kw in _DEPR_KEYWORDS:
        if kw in a:
            return True
    return False


def _parse_amount(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


class _RateLimitError(Exception):
    """DART API 일일 한도 초과."""
    pass


def _collect_dart_cf_for_code(dart, stock_code: str) -> list[dict]:
    """
    단일 종목의 DART 현금흐름 데이터를 수집해 dict 리스트로 반환.
    depreciation은 여러 상각비 항목 합산.
    """
    from datetime import date
    today = date.today()
    m, y = today.month, today.year
    if m >= 11:   latest_y, latest_q = y, 3
    elif m >= 8:  latest_y, latest_q = y, 2
    elif m >= 5:  latest_y, latest_q = y, 1
    elif m >= 4:  latest_y, latest_q = y - 1, 4
    else:         latest_y, latest_q = y - 1, 3

    results = []

    for year in range(latest_y, latest_y - 10, -1):
        for quarter, rprt_code in _RPRT_CODE.items():
            if year > latest_y or (year == latest_y and quarter > latest_q):
                continue

            cf_data = None
            for fs_div in ["CFS", "OFS"]:
                try:
                    df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
                    # DART 한도 초과 감지
                    if isinstance(df, dict) and df.get("status") == "020":
                        raise _RateLimitError("DART API 일일 한도 초과")
                    if df is None or df.empty:
                        continue
                    accs = df["account_nm"].astype(str).str.replace(" ", "")
                    if accs.str.contains("영업활동").any():
                        cf_data = df
                        break
                except _RateLimitError:
                    raise  # 상위로 전파
                except Exception:
                    pass

            if cf_data is None:
                continue

            rec = {
                "stock_code": stock_code,
                "year": year,
                "quarter": quarter,
                "is_annual": (quarter == 4),
                "operating_cf": None, "investing_cf": None, "financing_cf": None,
                "capex": None, "cash_end": None, "depreciation": None,
            }

            _CF_TOP = {
                "operating_cf":  ["영업활동으로인한현금흐름", "영업활동현금흐름", "영업활동으로인한순현금흐름"],
                "investing_cf":  ["투자활동으로인한현금흐름", "투자활동현금흐름", "투자활동으로인한순현금흐름"],
                "financing_cf":  ["재무활동으로인한현금흐름", "재무활동현금흐름", "재무활동으로인한순현금흐름"],
                "capex":         ["유형자산의취득", "유형자산취득", "유형자산의증가", "유형자산의구입"],
                "cash_end":      ["기말현금및현금성자산", "기말의현금및현금성자산",
                                  "현금및현금성자산의기말잔액", "현금및현금성자산기말잔액",
                                  "현금및현금성자산의순증감"],
            }

            for _, row in cf_data.iterrows():
                acc_raw = str(row.get("account_nm", ""))
                acc = acc_raw.replace(" ", "").replace("　", "")
                val_str = row.get("thstrm_amount")
                if not acc or val_str is None:
                    continue
                try:
                    import pandas as _pd
                    if _pd.isna(val_str):
                        continue
                except Exception:
                    pass
                val = _parse_amount(val_str)
                if val is None:
                    continue

                # 상단 CF 항목 매핑
                for field, kws in _CF_TOP.items():
                    if any(kw in acc for kw in kws):
                        if field == "capex":
                            rec[field] = abs(val)
                        elif rec[field] is None:
                            rec[field] = val
                        break

                # 상각비 합산 (여러 행 합산)
                if _is_depr_account(acc):
                    v = abs(val)
                    if v > 0:
                        rec["depreciation"] = (rec["depreciation"] or 0) + v
                        logger.debug(f"  [{stock_code}] {year}Q{quarter} 상각비 항목: '{acc_raw}' = {v:,.0f} → 누계={rec['depreciation']:,.0f}")

            # depreciation이 수집됐으면 결과에 추가
            if any(rec[k] is not None for k in ["operating_cf", "capex", "depreciation"]):
                results.append(rec)

    return results


def _upsert_cf(conn: sqlite3.Connection, rec: dict, force: bool = False) -> bool:
    """NULL 컬럼만 채우는 upsert (force=False)."""
    code, year, quarter, is_annual = (
        rec["stock_code"], rec["year"], rec["quarter"], rec["is_annual"]
    )
    row = conn.execute(
        "SELECT operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation "
        "FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        (code, year, quarter, is_annual)
    ).fetchone()

    fields = ["operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"]

    if row is None:
        vals = [code, year, quarter, is_annual] + [rec.get(f) for f in fields]
        ph = ",".join("?" * len(vals))
        conn.execute(
            f"INSERT INTO cash_flow_data (stock_code,year,quarter,is_annual,"
            f"operating_cf,investing_cf,financing_cf,capex,cash_end,depreciation) "
            f"VALUES ({ph})",
            vals
        )
        conn.commit()
        return True

    updates, params = [], []
    for i, col in enumerate(fields):
        new_val = rec.get(col)
        if new_val is None:
            continue
        if force or row[i] is None:
            updates.append(f"{col}=?")
            params.append(new_val)

    if not updates:
        return False
    params += [code, year, quarter, is_annual]
    conn.execute(
        f"UPDATE cash_flow_data SET {', '.join(updates)} "
        f"WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        params
    )
    conn.commit()
    return True


def fill_missing(db_path: str = DB_PATH, limit: int = 0,
                 codes: list[str] | None = None, force: bool = False) -> None:
    """depreciation NULL 종목 전체 DART 재수집."""
    import OpenDartReader as _ODR
    dart = _ODR(config.DART_API_KEY)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    if codes:
        target = codes
    else:
        rows = conn.execute("""
            SELECT DISTINCT stock_code FROM cash_flow_data
            WHERE depreciation IS NULL
              AND LENGTH(stock_code) = 6
              AND stock_code GLOB '[0-9]*'
            ORDER BY stock_code
        """).fetchall()
        target = [r[0] for r in rows]
        if limit:
            target = target[:limit]

    logger.info(f"대상 종목: {len(target)}개")

    total_saved = 0
    for i, code in enumerate(target, 1):
        logger.info(f"[{i}/{len(target)}] {code} DART 재수집...")
        try:
            recs = _collect_dart_cf_for_code(dart, code)
            saved = 0
            for rec in recs:
                ok = _upsert_cf(conn, rec, force=force)
                if ok:
                    saved += 1
            total_saved += saved
            logger.info(f"  → {len(recs)}기간 수집, {saved}건 업데이트")
        except _RateLimitError:
            logger.warning("DART 일일 한도 초과 — 내일 다시 실행하세요. 진행률: {i}/{len(target)}")
            break
        except Exception as e:
            logger.error(f"  → 실패: {e}")
        time.sleep(1.0)   # DART API rate limit (1초로 늘림)

    conn.close()
    logger.info(f"\n완료: 총 {total_saved}건 업데이트")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART CF 재수집 (depreciation NULL 채우기)")
    parser.add_argument("--code",  help="단일 종목코드")
    parser.add_argument("--limit", type=int, default=0, help="최대 종목 수 (0=전체)")
    parser.add_argument("--force", action="store_true", help="기존 값 덮어쓰기")
    parser.add_argument("--db",    default=DB_PATH, help="DB 경로")
    args = parser.parse_args()

    if args.code:
        fill_missing(db_path=args.db, codes=[args.code], force=args.force)
    else:
        fill_missing(db_path=args.db, limit=args.limit, force=args.force)
