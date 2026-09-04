"""
collect_dart_financial_batch.py — DART 전종목 재무제표 배치 수집 (손익+재무상태)

설계 원칙:
  · financial_data에 data_source='dart' 행만 write (FnGuide 행 불변)
  · 연간 보고서(11011) → quarter=4, is_annual=1 (연간 총계값 그대로 저장)
  · 분기 보고서(11013/11012/11014) → quarter=1/2/3, is_annual=0 (DART 누적값)
  · Q4 증분 = 연간 - Q3누적 (표시 시 역산, 별도 저장 안함)
  · EPS/BPS: DART 직접 필드 우선 → 없으면 net_income/total_equity ÷ shares 계산
  · 검증: 계산 EPS/BPS vs FnGuide 저장값 비교 → 차이 CSV 출력

사용법:
  python3 collect_dart_financial_batch.py                    # 전종목 최근 4년
  python3 collect_dart_financial_batch.py --years 6          # 최근 6년
  python3 collect_dart_financial_batch.py --annual-only      # 연간(Q4)만
  python3 collect_dart_financial_batch.py --missing          # DART 행 없는 종목만
  python3 collect_dart_financial_batch.py --validate         # 검증만 (수집 없음)
  python3 collect_dart_financial_batch.py --limit 50         # 테스트: 50종목만
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import logging
import os
import sqlite3
import time
from datetime import date

import pandas as pd
from env_utils import BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH  = str(BASE_DIR / "stock.db")
OUT_DIR  = BASE_DIR / "scratch"
RATE_SEC = 0.8  # DART API rate limit

# 분기 코드 매핑
_RPRT: dict[int, str] = {
    4: "11011",  # 사업보고서 (연간, 연간 총계값)
    1: "11013",  # 1분기 (Q1 누적)
    2: "11012",  # 반기 (Q1+Q2 누적)
    3: "11014",  # 3분기 (Q1+Q2+Q3 누적)
}

# 손익계산서 계정 매핑
_IS_MAP = [
    (["매출액", "영업수익"],                                      "revenue"),
    (["영업이익", "영업손익"],                                    "operating_profit"),
    # 당기순이익: 비지배주주귀속 제외, 주당 제외
    (["당기순이익", "당기순손익", "분기순이익", "반기순이익"],     "net_income"),
]

# 재무상태표 계정 매핑
_BS_MAP = [
    (["자산총계"],              "total_assets"),
    (["부채총계"],              "total_liabilities"),
    (["자본총계"],              "total_equity"),
    (["자본금"],                "capital_stock"),
]

# EPS/BPS 계정 (IS/주석)
_EPS_KEYWORDS = ["기본주당순이익", "기본주당이익", "주당순이익", "기본EPS"]
_BPS_KEYWORDS = ["주당순자산", "1주당순자산가액", "주당장부가액"]


def _get_dart():
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from dart_key_manager import RotatingOpenDartReader
    return RotatingOpenDartReader()


def _latest_quarter() -> tuple[int, int]:
    """현재 공시 가능한 최신 분기."""
    today = date.today()
    y, m = today.year, today.month
    if m < 5:    return y - 1, 4
    elif m < 8:  return y, 1
    elif m < 11: return y, 2
    else:        return y, 3


def _parse_val(row: pd.Series, col: str = "thstrm_amount") -> float | None:
    v = row.get(col)
    if v is None or pd.isna(v):
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _parse_finstate(df: pd.DataFrame) -> dict:
    """
    DART finstate_all DataFrame → 재무 dict.

    CFS/OFS 혼합 데이터 처리:
      · IS(손익): CFS 우선 (연결 실적이 진실)
      · BS total_equity: OFS 기준 우선 → BPS 계산 시 비지배주주 제외
        (CFS 자본총계는 비지배주주 포함이라 FnGuide BPS와 크게 차이남)
      · EPS/BPS: DART 직접 필드(`기본주당순이익`, `주당순자산`) 최우선
    """
    m: dict[str, float | None] = {
        "revenue": None, "operating_profit": None, "net_income": None,
        "total_assets": None, "total_liabilities": None,
        "total_equity_cfs": None,   # 미사용(삭제 예정), 구조 유지
        "total_equity_ofs": None,   # 미사용(삭제 예정), 구조 유지
        "total_equity": None,
        "capital_stock": None, "eps": None, "bps": None,
    }

    for _, row in df.iterrows():
        sj  = str(row.get("sj_nm", "")).replace(" ", "")
        acc = str(row.get("account_nm", "")).replace(" ", "")
        fs  = str(row.get("fs_div", "")).strip()   # CFS 또는 OFS
        if not acc:
            continue
        val = _parse_val(row)
        if val is None:
            continue

        # 손익계산서 항목 (CFS 우선)
        if "손익계산서" in sj:
            for keywords, field in _IS_MAP:
                if field == "net_income":
                    if any(x in acc for x in ("비지배", "주당", "총포괄", "지배기업")):
                        continue
                if field == "revenue":
                    # "매출총이익", "매출원가", "영업외수익", "금융수익" 등 오매핑 차단
                    if any(x in acc for x in ("원가", "총이익", "총손실", "비용", "대손", "충당", "차감", "조정", "금융", "외", "투자", "수수료")):
                        continue
                    # 정확한 "수익" 계정명 또는 매출액/영업수익 키워드 포함 검사
                    if not (any(kw in acc for kw in keywords) or acc == "수익"):
                        continue
                elif field == "operating_profit":
                    if any(x in acc for x in ("외", "금융", "기타")):
                        continue
                    if not any(kw in acc for kw in keywords):
                        continue
                else:
                    if not any(kw in acc for kw in keywords):
                        continue

                if m[field] is None:
                    m[field] = val
                break

            # EPS: DART 직접 필드
            if any(kw in acc for kw in _EPS_KEYWORDS):
                if m["eps"] is None:
                    m["eps"] = val

        # 재무상태표 항목
        elif "재무상태표" in sj:
            for keywords, field in _BS_MAP:
                if field == "total_equity":
                    # "부채와자본총계"는 자본총계 키워드에 걸리지만 자산합계이므로 제외
                    if "부채" in acc:
                        continue
                if any(kw in acc for kw in keywords):
                    if m[field] is None:
                        m[field] = val
                    break

            # BPS: DART 직접 필드 (`주당순자산` 등)
            if any(kw in acc for kw in _BPS_KEYWORDS):
                if m["bps"] is None:
                    m["bps"] = val

    del m["total_equity_ofs"]; del m["total_equity_cfs"]
    return m


def _calc_eps_bps(
    m: dict,
    shares: float | None,
) -> dict:
    """
    DART 직접 EPS/BPS 없으면 net_income/total_equity ÷ shares로 계산.
    Q4 역산 여부와 무관하게 연간값 기준.
    """
    if m.get("eps") is None and m.get("net_income") is not None and shares and shares > 0:
        m["eps"] = round(m["net_income"] / shares, 2)
    if m.get("bps") is None and m.get("total_equity") is not None and shares and shares > 0:
        m["bps"] = round(m["total_equity"] / shares, 2)
    return m


def _get_shares(conn: sqlite3.Connection, code: str) -> float | None:
    """최신 shares_issued 조회."""
    row = conn.execute(
        """SELECT shares_issued FROM stock_universe
           WHERE stock_code=? AND shares_issued>0
           ORDER BY base_date DESC LIMIT 1""",
        (code,),
    ).fetchone()
    return float(row[0]) if row else None


def collect_one(
    dart,
    conn: sqlite3.Connection,
    code: str,
    years: int,
    annual_only: bool,
    overwrite: bool,
) -> int:
    """단일 종목 수집. 반환: 저장 건수."""
    latest_y, latest_q = _latest_quarter()
    shares = _get_shares(conn, code)
    saved = 0

    quarters_to_collect = [4] if annual_only else [4, 1, 2, 3]

    for year in range(latest_y, latest_y - years, -1):
        for qnum in quarters_to_collect:
            # 미래 분기 스킵
            if year > latest_y or (year == latest_y and qnum > latest_q):
                continue

            is_annual = 1 if qnum == 4 else 0
            rprt_code = _RPRT[qnum]

            # 기존 DART 계열 행 확인. financial_data의 실제 UNIQUE 키에는
            # report_type이 포함되므로, CFS가 FnGuide 등으로 이미 차 있으면
            # OFS를 시도하고 둘 다 차 있으면 스킵한다.
            exists = conn.execute(
                """SELECT id FROM financial_data
                   WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                     AND data_source LIKE 'dart%'""",
                (code, year, qnum, is_annual),
            ).fetchone()

            if exists and not overwrite:
                continue

            # CFS 우선, OFS fallback
            fn_data = None
            fs_used = "CFS"
            for fs in ["CFS", "OFS"]:
                conflict = conn.execute(
                    """SELECT id, data_source FROM financial_data
                       WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                         AND report_type=?""",
                    (code, year, qnum, is_annual, fs),
                ).fetchone()
                if conflict and not overwrite:
                    continue
                try:
                    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
                        df = dart.finstate_all(code, year, rprt_code, fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df
                        fs_used = fs
                        break
                except Exception:
                    pass
                time.sleep(0.15)

            time.sleep(RATE_SEC)

            if fn_data is None or fn_data.empty:
                continue

            m = _parse_finstate(fn_data)
            m = _calc_eps_bps(m, shares)

            # 아무 값도 없으면 스킵
            if all(v is None for v in m.values()):
                continue

            try:
                if exists and overwrite:
                    conn.execute("""
                        UPDATE financial_data SET
                            revenue=?, operating_profit=?, net_income=?,
                            total_assets=?, total_liabilities=?, total_equity=?,
                            capital_stock=?, eps=?, bps=?,
                            report_type=?, data_source='dart'
                        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?
                          AND data_source='dart'
                    """, (
                        m["revenue"], m["operating_profit"], m["net_income"],
                        m["total_assets"], m["total_liabilities"], m["total_equity"],
                        m["capital_stock"], m["eps"], m["bps"],
                        fs_used, code, year, qnum, is_annual,
                    ))
                else:
                    conn.execute("""
                        INSERT INTO financial_data
                          (stock_code, year, quarter, is_annual,
                           revenue, operating_profit, net_income,
                           total_assets, total_liabilities, total_equity,
                           capital_stock, eps, bps, report_type, data_source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        code, year, qnum, is_annual,
                        m["revenue"], m["operating_profit"], m["net_income"],
                        m["total_assets"], m["total_liabilities"], m["total_equity"],
                        m["capital_stock"], m["eps"], m["bps"],
                        fs_used, "dart",
                    ))
                conn.commit()
                saved += 1
                logger.debug(
                    f"[{code}] {year}Q{qnum} 저장 ({fs_used}) "
                    f"NI={m['net_income']}, EPS={m['eps']}, BPS={m['bps']}"
                )
            except Exception as e:
                logger.warning(f"[{code}] {year}Q{qnum} 저장 실패: {e}")

    return saved


def validate_eps_bps(conn: sqlite3.Connection) -> list[dict]:
    """
    DART 기반 계산 EPS/BPS vs FnGuide 저장값 비교.

    비교 기준:
      · 같은 stock_code + year 에서 DART(is_annual=1) + FnGuide(is_annual=1) 행 매칭
      · EPS 차이: |dart_eps - fg_eps| / max(|dart_eps|, |fg_eps|, 1) > 10%
      · BPS 차이: 동일 기준 > 10%

    Q4 역산 주의:
      · DART 연간 행(quarter=4)의 EPS/BPS는 연간 총계 기준
      · FnGuide 행(quarter=0)의 EPS/BPS는 FnGuide Main SVD_Main 기준
      · 지배주주순이익 vs 당기순이익 기준 차이로 EPS 다를 수 있음 (기록만)
    """
    rows = conn.execute("""
        SELECT
            d.stock_code,
            d.year,
            d.eps   AS dart_eps,
            d.bps   AS dart_bps,
            d.net_income AS dart_ni,
            d.total_equity AS dart_eq,
            f.eps   AS fg_eps,
            f.bps   AS fg_bps,
            f.net_income AS fg_ni,
            su.shares_issued,
            su.stock_name
        FROM financial_data d
        JOIN financial_data f
          ON d.stock_code = f.stock_code
         AND d.year = f.year
         AND f.data_source = 'fnguide'
         AND f.is_annual = 1
        LEFT JOIN (
            SELECT stock_code, stock_name, shares_issued
            FROM stock_universe
            WHERE shares_issued > 0
            GROUP BY stock_code HAVING MAX(base_date)
        ) su ON su.stock_code = d.stock_code
        WHERE d.data_source = 'dart'
          AND d.is_annual = 1
          AND (d.eps IS NOT NULL OR d.bps IS NOT NULL)
        ORDER BY d.stock_code, d.year DESC
    """).fetchall()

    results = []
    for r in rows:
        code = r[0]; year = r[1]
        dart_eps = r[2]; dart_bps = r[3]
        dart_ni = r[4]; dart_eq = r[5]
        fg_eps = r[6]; fg_bps = r[7]
        fg_ni = r[8]
        shares = r[9]; name = r[10]

        # EPS 계산값 (DART net_income 기준)
        calc_eps = None
        if dart_ni is not None and shares and shares > 0:
            calc_eps = round(dart_ni / shares, 2)

        # BPS 계산값 (DART total_equity 기준)
        calc_bps = None
        if dart_eq is not None and shares and shares > 0:
            calc_bps = round(dart_eq / shares, 2)

        def pct_diff(a, b):
            if a is None or b is None:
                return None
            m = max(abs(a), abs(b), 1e-9)
            return abs(a - b) / m

        eps_diff = pct_diff(dart_eps or calc_eps, fg_eps)
        bps_diff = pct_diff(dart_bps or calc_bps, fg_bps)

        if (eps_diff is not None and eps_diff > 0.10) or \
           (bps_diff is not None and bps_diff > 0.10):
            results.append({
                "stock_code": code,
                "stock_name": name,
                "year": year,
                "dart_eps": dart_eps,
                "calc_eps": calc_eps,
                "fg_eps": fg_eps,
                "eps_diff_pct": round(eps_diff * 100, 1) if eps_diff is not None else None,
                "dart_bps": dart_bps,
                "calc_bps": calc_bps,
                "fg_bps": fg_bps,
                "bps_diff_pct": round(bps_diff * 100, 1) if bps_diff is not None else None,
                "dart_ni": dart_ni,
                "fg_ni": fg_ni,
                "shares": shares,
                "note": _diagnose(dart_eps or calc_eps, fg_eps, dart_bps or calc_bps, fg_bps,
                                  dart_ni, fg_ni),
            })

    return results


def _diagnose(dart_eps, fg_eps, dart_bps, fg_bps, dart_ni, fg_ni) -> str:
    """차이 원인 자동 진단."""
    notes = []
    if dart_eps is not None and fg_eps is not None:
        ratio = dart_eps / fg_eps if fg_eps != 0 else None
        if ratio is not None and abs(ratio - 1.0) > 0.5:
            if 0.09 < abs(ratio) < 0.11:
                notes.append("EPS단위오류(×10배차이)")
            elif 90 < abs(ratio) < 110:
                notes.append("EPS단위오류(÷100배차이)")
            else:
                notes.append(f"EPS기준차이(지배주주vs당기, ratio={ratio:.2f})")
    if dart_ni is not None and fg_ni is not None:
        ni_diff = abs(dart_ni - fg_ni) / max(abs(dart_ni), abs(fg_ni), 1)
        if ni_diff > 0.05:
            notes.append(f"NI원본차이({ni_diff*100:.1f}%)")
    return "; ".join(notes) if notes else "기준/시점차이"


def _eligible_codes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT su.stock_code
        FROM stock_universe su
        WHERE su.market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
          AND COALESCE(su.stock_type,'보통주') = '보통주'
          AND LENGTH(su.stock_code) = 6
          AND su.stock_code GLOB '[0-9]*'
        ORDER BY su.stock_code
    """).fetchall()
    return [r[0] for r in rows]


def run(
    years: int = 4,
    annual_only: bool = False,
    missing_only: bool = False,
    overwrite: bool = False,
    validate_only: bool = False,
    limit: int | None = None,
):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row

    if validate_only:
        logger.info("검증 모드: DART vs FnGuide EPS/BPS 비교")
        results = validate_eps_bps(conn)
        conn.close()
        if not results:
            logger.info("차이 없음 (10% 기준 통과)")
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = OUT_DIR / f"dart_eps_bps_validation_{ts}.csv"
        fields = list(results[0].keys())
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(results)
        logger.info(f"검증 결과: {len(results)}건 차이 → {out}")
        return

    codes = _eligible_codes(conn)

    if missing_only:
        have_dart = set(
            r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM financial_data WHERE data_source='dart'"
            ).fetchall()
        )
        codes = [c for c in codes if c not in have_dart]

    if limit:
        codes = codes[:limit]

    total = len(codes)
    logger.info(
        f"대상: {total}종목 | 최근 {years}년 | "
        f"annual_only={annual_only} missing_only={missing_only} overwrite={overwrite}"
    )

    dart = _get_dart()
    ok = 0; errors = 0

    for i, code in enumerate(codes, 1):
        try:
            n = collect_one(dart, conn, code, years, annual_only, overwrite)
            ok += n
            if n:
                logger.info(f"[{i}/{total}] {code}: {n}건 저장")
        except Exception as e:
            errors += 1
            logger.warning(f"[{i}/{total}] {code} 오류: {e}")

        if i % 50 == 0:
            logger.info(f"진행: {i}/{total} | 저장 {ok}건 | 오류 {errors}건")

    conn.close()
    logger.info(f"완료: {total}종목 | 저장 {ok}건 | 오류 {errors}건")

    # 수집 후 자동 검증
    if ok > 0:
        logger.info("수집 완료 — 자동 검증 실행")
        conn2 = sqlite3.connect(DB_PATH, timeout=60)
        conn2.row_factory = sqlite3.Row
        results = validate_eps_bps(conn2)
        conn2.close()
        if results:
            ts = time.strftime("%Y%m%d_%H%M%S")
            out = OUT_DIR / f"dart_eps_bps_validation_{ts}.csv"
            fields = list(results[0].keys())
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader(); w.writerows(results)
            logger.info(f"검증: {len(results)}건 차이 → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years",       type=int,  default=4,    help="수집 연수 (기본 4년)")
    ap.add_argument("--annual-only", action="store_true",      help="연간 보고서만 (Q4)")
    ap.add_argument("--missing",     action="store_true",      help="DART 행 없는 종목만")
    ap.add_argument("--overwrite",   action="store_true",      help="기존 DART 행 덮어쓰기")
    ap.add_argument("--validate",    action="store_true",      help="검증만 실행 (수집 없음)")
    ap.add_argument("--limit",       type=int,  default=None,  help="최대 종목수 (테스트용)")
    args = ap.parse_args()
    run(
        years=args.years,
        annual_only=args.annual_only,
        missing_only=args.missing,
        overwrite=args.overwrite,
        validate_only=args.validate,
        limit=args.limit,
    )
