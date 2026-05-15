"""
collect_10year_extension.py — 10년치 재무제표·현금흐름표 확장 수집 (Phase 3)

전제 조건:
  Phase 1 완료: fnguide_financial_collector.py (수집)
  Phase 2 완료: validate_5year_pipeline.py --auto-fix (이상 분류 + 자동수정)

⚠️ FnGuide 데이터 가용 범위:
  FnGuide SVD_Finance 페이지는 현재 기준 약 최근 4년(2022~2025)만 표시.
  2021 이전 데이터는 FnGuide에서 수집 불가 → DART API 사용.

소스 전략:
  2022+ → FnGuide (빠르고 안정적, DART 쿼터 절약)
  2021-  → DART finstate_all API (권위 소스, 일일 쿼터 36,000건)
           --dart-daily-limit 로 하루 DART 호출수 제한 (기본 400건)
           여러 날에 나눠서 실행.

이 스크립트의 역할:
  1. financial_anomalies 테이블에서 각 종목 이상 유형을 읽어 수집 전략 결정
  2. 연도별 적합 소스로 수집 (FnGuide 또는 DART)
  3. financial_source_snapshot에 신규 스냅샷 저장 (검증 상태: 'unverified')
  4. financial_data / cash_flow_data에 INSERT ONLY (기존 검증 데이터 덮어쓰지 않음)
  5. 신규 삽입 레코드만 ai_verify_financials.py 재검증 트리거 출력

이상 유형별 수집 전략:
  holding_company   → OFS 수집 우선 (별도 재무제표가 실체)
  cfs_ofs_mislabeled→ CFS+OFS 모두 수집하여 올바른 레이블 재확인
  unit_error        → 수집 후 100배 검사 필수
  large_discrepancy → 2개소 교차 검증 후 기록
  newly_listed      → 상장 전 연도는 건너뜀 (수집 불가)
  persistent_loss / revenue_zero / clean → 표준 CFS+OFS 수집

실행:
  # dry-run (실제 저장 없음)
  python3 scripts/collect_10year_extension.py --dry-run

  # 2022+ 연도 FnGuide 수집 (쿼터 걱정 없음)
  python3 scripts/collect_10year_extension.py --year-from 2019 --year-to 2021

  # 2015-2021 DART 수집 (일일 400종목 제한, 여러 날 실행)
  python3 scripts/collect_10year_extension.py --year-from 2015 --year-to 2021 --dart-daily-limit 400

  # 특정 종목
  python3 scripts/collect_10year_extension.py --codes 005930 000660

  # 수집 후 미검증 레코드 일괄 검증
  python3 scripts/ai_verify_financials.py --limit 9999 --verifier "claude-sonnet-4-6"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from api_rate_limiter import api_limiter

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).resolve().parent.parent
DB_PATH     = BASE_DIR / "stock.db"
PROFILES_PATH = BASE_DIR / "config" / "financial_profiles.json"
REPORT_PATH = BASE_DIR / "scratch" / "collect_10year_report.json"

FNGUIDE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://comp.fnguide.com/",
}
REPORT_TYPE_PARAMS  = {"CFS": "", "OFS": "B"}
DART_FS_DIV         = {"CFS": "CFS", "OFS": "OFS"}
DART_RPRT_CODE      = "11011"  # 사업보고서(연간)
FNGUIDE_MIN_YEAR    = 2022    # FnGuide 페이지에 표시되는 최소 연도 (현재 기준)
DEFAULT_YEAR_FROM   = 2015
DEFAULT_YEAR_TO     = 2021
DEFAULT_DART_DAILY  = 400     # 하루 DART 호출 종목수 한도


# ─── DB 헬퍼 ─────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=60000")
    return c


def _flatten(c):
    if isinstance(c, tuple):
        return " ".join(str(x) for x in c if str(x) != "nan")
    return str(c)


def _parse_val(raw):
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "N/A", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None


# ─── FnGuide 파싱 ────────────────────────────────────────────────────────────

def _parse_fnguide_tables(text: str, year: int) -> dict:
    """FnGuide SVD_Finance HTML에서 특정 연도 값 추출 (CFS/OFS 공통)."""
    try:
        tables = pd.read_html(StringIO(text))
    except Exception:
        return {}

    target_col_pattern = f"{year}/12"
    result: dict = {}

    kw_map = {
        "revenue":          (["매출액", "영업수익", "매출"],                    []),
        "operating_profit": (["영업이익"],                                       ["손실"]),
        "net_income":       (["지배주주순이익", "당기순이익"],                   ["비지배"]),
        "eps":              (["EPS"],                                             []),
        "total_assets":     (["자산총계", "자산"],                               []),
        "total_liabilities":(["부채총계", "부채"],                               []),
        "total_equity":     (["자본총계", "자본"],                               ["비지배", "기타금융"]),
        "operating_cf":     (["영업활동으로인한현금흐름", "영업활동현금흐름"],   []),
        "investing_cf":     (["투자활동으로인한현금흐름", "투자활동현금흐름"],   []),
        "financing_cf":     (["재무활동으로인한현금흐름", "재무활동현금흐름"],   []),
        "capex":            (["자본적지출", "CAPEX", "설비투자"],                []),
    }

    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        labels = t.iloc[:, 0].astype(str).str.replace(" ", "", regex=False)
        target_col = next((c for c in t.columns if target_col_pattern in str(c)), None)
        if target_col is None:
            continue

        for key, (kws, excls) in kw_map.items():
            if key in result:
                continue
            mask = pd.Series([False] * len(t))
            for k in kws:
                mask = mask | labels.str.contains(re.escape(k.replace(" ", "")), na=False)
            for ex in excls:
                mask = mask & ~labels.str.contains(re.escape(ex.replace(" ", "")), na=False)
            rows = t.loc[mask]
            if rows.empty:
                continue
            v = _parse_val(rows.iloc[0][target_col])
            if v is not None:
                result[key] = v * 1e8  # 억원 → 원

    return result


def fetch_fnguide(stock_code: str, report_type: str, year: int) -> Optional[dict]:
    """FnGuide SVD_Finance.asp 요청 → 해당 연도 values dict (단위: 원).
    FnGuide는 현재 기준 약 최근 4년만 표시. 2022 미만 연도는 None 반환.
    """
    if year < FNGUIDE_MIN_YEAR:
        return None  # FnGuide 페이지에 해당 연도 없음 — DART fallback 필요

    gb = REPORT_TYPE_PARAMS.get(report_type, "")
    url = (
        f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y"
        f"&ReportGB={gb}&NewMenuID=103&stkGb=701"
    )
    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        logger.warning(f"FnGuide 요청 실패: {stock_code} {report_type}")
        return None

    data = _parse_fnguide_tables(resp.text, year)
    return data if data else None


def _detect_dart_unit_multiplier(df: "pd.DataFrame", raw_result: dict) -> float:
    """DART 보고서의 금액 단위 배수를 자동 감지.

    DART 공시 중 일부는 '원' 대신 '천원' 또는 '백만원' 기준으로 작성됨.
    규칙:
      - df에 'currency' 컬럼이 있으면 우선 참조 (미래 대비)
      - 매출액/자산총계가 존재하는 기업이고 값이 1억(1e8) 미만이면 천원 단위 의심
      - 1만(1e4) 미만이면 백만원 단위 의심
    반환: 1.0 (원), 1000.0 (천원→원), 1_000_000.0 (백만원→원)
    """
    # DART가 currency_unit 컬럼을 제공하는 경우 (XBRL 원문 파싱 시)
    for col in ("currency_unit", "unit"):
        if col in df.columns:
            unit_val = str(df[col].iloc[0]).replace(",", "").strip().upper()
            if "천원" in unit_val or "KRW000" in unit_val or unit_val == "1000":
                return 1_000.0
            if "백만" in unit_val or "KRW000000" in unit_val or unit_val == "1000000":
                return 1_000_000.0
            if "원" in unit_val or unit_val in ("KRW", "1"):
                return 1.0

    # 금액 크기 휴리스틱: 매출 또는 자산이 존재하는 기업 기준
    rev = raw_result.get("revenue") or raw_result.get("total_assets")
    if rev is None or rev == 0:
        return 1.0  # 판단 불가 → 안전하게 원 단위 유지

    abs_rev = abs(rev)
    if abs_rev < 1e4:
        # 매출이 1만원 미만 → 백만원 단위로 보고한 것이 확실
        logger.warning(f"DART 단위 감지: 백만원 단위 의심 (raw revenue={rev:.0f}), ×1,000,000 보정")
        return 1_000_000.0
    if abs_rev < 1e8:
        # 매출이 1억원 미만 → 천원 단위로 보고한 것이 의심
        # 단, 진짜로 소기업이면 1억 미만일 수도 있으므로 추가 조건 확인
        # 총자산이 매출의 10배 이내면 정상 소기업으로 판단
        assets = raw_result.get("total_assets", 0) or 0
        if assets > 0 and assets < abs_rev * 100:
            return 1.0  # 정상 소기업
        logger.warning(f"DART 단위 감지: 천원 단위 의심 (raw revenue={rev:.0f}), ×1,000 보정")
        return 1_000.0

    return 1.0


def fetch_dart(stock_code: str, report_type: str, year: int) -> Optional[dict]:
    """DART finstate_all API로 해당 연도 재무 데이터 수집 (단위: 원).
    연간 사업보고서 기준. 단위 자동 감지(원/천원/백만원) 포함.
    """
    if not api_limiter.wait("DART"):
        logger.warning("DART 쿼터 소진 — 오늘 수집 중단")
        return None

    try:
        import OpenDartReader as _ODR  # type: ignore
        import config as _cfg
        dart = _ODR.OpenDartReader(_cfg.DART_API_KEY)
    except Exception as e:
        logger.error(f"OpenDartReader 초기화 실패: {e}")
        return None

    fs_div = DART_FS_DIV.get(report_type, "CFS")

    # 우선순위 순서로 매칭 (앞쪽 키워드가 더 정확)
    _ACCOUNT_MAP: dict[str, list[str]] = {
        "revenue":          ["매출액", "영업수익", "수익(매출액)", "매출"],
        "operating_profit": ["영업이익"],
        "net_income":       ["지배기업소유주지분순이익", "지배주주순이익", "당기순이익"],
        "total_assets":     ["자산총계"],
        "total_liabilities":["부채총계"],
        "total_equity":     ["자본총계"],
        "operating_cf":     ["영업활동으로인한현금흐름", "영업활동현금흐름"],
        "investing_cf":     ["투자활동으로인한현금흐름", "투자활동현금흐름"],
        "financing_cf":     ["재무활동으로인한현금흐름", "재무활동현금흐름"],
    }
    # 이 필드는 부분 일치 제외 목록 (오매칭 방지)
    _EXCLUDE_MAP: dict[str, list[str]] = {
        "net_income":    ["비지배", "감소", "취소"],
        "total_equity":  ["비지배", "기타포괄", "자본조정"],
        "total_liabilities": ["기타"],
    }

    try:
        df = dart.finstate_all(stock_code, year, DART_RPRT_CODE, fs_div=fs_div)
    except Exception as e:
        logger.debug(f"DART finstate_all 오류 {stock_code} {year} {report_type}: {e}")
        return None

    if df is None or df.empty:
        return None

    # account_nm 우선, 없으면 account_detail 사용
    labels = df.get("account_nm", df.get("account_detail", pd.Series(dtype=str))).astype(str)
    # 공백 제거한 버전으로 비교
    labels_nospace = labels.str.replace(" ", "", regex=False)

    result: dict = {}
    for key, kws in _ACCOUNT_MAP.items():
        if key in result:
            continue
        excls = _EXCLUDE_MAP.get(key, [])
        for kw in kws:
            kw_ns = kw.replace(" ", "")
            mask = labels_nospace.str.contains(kw_ns, na=False, regex=False)
            # 제외어 적용 (부분 일치가 아닌 정확한 계정명 우선)
            for ex in excls:
                mask = mask & ~labels_nospace.str.contains(ex.replace(" ", ""), na=False, regex=False)
            rows = df.loc[mask]
            if rows.empty:
                continue
            # thstrm_amount(당기) 컬럼 선택
            col = next((c for c in ["thstrm_amount", "thstrm_add_amount"] if c in rows.columns), None)
            if col is None:
                continue
            # 중복 행 있을 경우 절댓값 최대 행 선택 (합계 행이 가장 큼)
            best_row = rows.iloc[0]
            if len(rows) > 1:
                def _parse(r):
                    try: return abs(float(str(r[col]).replace(",", "")))
                    except: return 0
                best_row = rows.loc[rows.apply(_parse, axis=1).idxmax()]
            raw = str(best_row[col]).replace(",", "").strip()
            try:
                val = float(raw)
                result[key] = val
                break
            except Exception:
                continue

    if not result:
        return None

    # 단위 자동 감지 및 보정 (원/천원/백만원)
    multiplier = _detect_dart_unit_multiplier(df, result)
    if multiplier != 1.0:
        result = {k: v * multiplier for k, v in result.items()}

    return result


# ─── 이상 유형 조회 ──────────────────────────────────────────────────────────

def load_anomalies(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """financial_anomalies 테이블 → {stock_code: [anomaly_type, ...]}"""
    try:
        rows = conn.execute(
            "SELECT stock_code, anomaly_type FROM financial_anomalies WHERE is_resolved=0"
        ).fetchall()
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["stock_code"], []).append(r["anomaly_type"])
        return result
    except sqlite3.OperationalError:
        # financial_anomalies 테이블이 아직 없으면 빈 dict
        logger.warning("financial_anomalies 테이블 없음 — validate_5year_pipeline.py 먼저 실행 권장")
        return {}


def load_profiles() -> dict:
    """financial_profiles.json → {stock_code: {...}} — 없으면 빈 dict."""
    if PROFILES_PATH.exists():
        try:
            return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def decide_report_types(stock_code: str, anomalies: list[str]) -> list[str]:
    """이상 유형에 따라 수집할 report_type 목록 결정."""
    if "holding_company" in anomalies:
        # 지주사: OFS(별도) 우선, CFS도 수집
        return ["OFS", "CFS"]
    if "cfs_ofs_mislabeled" in anomalies:
        # 오레이블: 양쪽 모두 수집하여 교차 확인
        return ["CFS", "OFS"]
    # 기본: 연결(CFS) + 별도(OFS)
    return ["CFS", "OFS"]


def should_skip_year(stock_code: str, year: int, anomalies: list[str], profiles: dict) -> bool:
    """수집 불필요한 연도 건너뜀 (신규상장 전 등)."""
    if "newly_listed" in anomalies:
        profile = profiles.get(stock_code, {})
        listing_year = profile.get("listing_year")
        if listing_year and year < listing_year:
            return True
    return False


# ─── 스냅샷 저장 ─────────────────────────────────────────────────────────────

def save_snapshot(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    report_type: str,
    data: dict,
    source_url: str,
    data_source: str = "fnguide",
) -> bool:
    """financial_source_snapshot에 신규 레코드 저장 (기존 row 있으면 스킵)."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    # 이미 같은 (stock, year, report_type, data_source)가 있으면 스킵
    existing = conn.execute("""
        SELECT id FROM financial_source_snapshot
        WHERE stock_code=? AND year=? AND quarter=0 AND is_annual=1
          AND report_type=? AND data_source=?
        LIMIT 1
    """, (stock_code, year, report_type, data_source)).fetchone()
    if existing:
        return False  # 이미 있음

    conn.execute(f"""
        INSERT OR IGNORE INTO financial_source_snapshot
          (stock_code, year, quarter, is_annual, report_type, data_source,
           source_url, fetched_at,
           revenue, operating_profit, net_income, eps, total_assets,
           total_liabilities, total_equity,
           operating_cf, investing_cf, financing_cf, capex,
           verification_status)
        VALUES (?,?,0,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unverified')
    """, (
        stock_code, year, report_type, data_source, source_url, fetched_at,
        data.get("revenue"),
        data.get("operating_profit"),
        data.get("net_income"),
        data.get("eps"),
        data.get("total_assets"),
        data.get("total_liabilities"),
        data.get("total_equity"),
        data.get("operating_cf"),
        data.get("investing_cf"),
        data.get("financing_cf"),
        data.get("capex"),
    ))
    return True


# ─── 재무 데이터 INSERT (기존 건드리지 않음) ──────────────────────────────────

def insert_financial_if_missing(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    report_type: str,
    data: dict,
    dry_run: bool,
) -> bool:
    """financial_data에 해당 (stock_code, year, report_type) 없을 때만 삽입."""
    existing = conn.execute("""
        SELECT id FROM financial_data
        WHERE stock_code=? AND year=? AND quarter=0 AND is_annual=1 AND report_type=?
    """, (stock_code, year, report_type)).fetchone()
    if existing:
        return False

    if not data.get("revenue") and not data.get("operating_profit"):
        return False

    if not dry_run:
        conn.execute("""
            INSERT OR IGNORE INTO financial_data
              (stock_code, year, quarter, revenue, operating_profit, net_income,
               eps, total_assets, total_equity, is_annual, report_type)
            VALUES (?,?,0,?,?,?,?,?,?,1,?)
        """, (
            stock_code, year,
            data.get("revenue"),
            data.get("operating_profit"),
            data.get("net_income"),
            data.get("eps"),
            data.get("total_assets"),
            data.get("total_equity"),
            report_type,
        ))
    return True


def insert_cashflow_if_missing(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    report_type: str,
    data: dict,
    dry_run: bool,
) -> bool:
    """cash_flow_data에 해당 (stock_code, year, report_type) 없을 때만 삽입."""
    existing = conn.execute("""
        SELECT id FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=0 AND is_annual=1 AND report_type=?
    """, (stock_code, year, report_type)).fetchone()
    if existing:
        return False

    if not data.get("operating_cf"):
        return False

    if not dry_run:
        conn.execute("""
            INSERT OR IGNORE INTO cash_flow_data
              (stock_code, year, quarter, operating_cf, investing_cf, financing_cf,
               capex, is_annual, report_type)
            VALUES (?,?,0,?,?,?,?,1,?)
        """, (
            stock_code, year,
            data.get("operating_cf"),
            data.get("investing_cf"),
            data.get("financing_cf"),
            data.get("capex"),
            report_type,
        ))
    return True


# ─── 단위 오류 검사 ──────────────────────────────────────────────────────────

def _check_unit_error(data: dict, anomaly_types: list[str]) -> bool:
    """
    unit_error 이상이 있는 종목은 수집값이 100배 차이인지 체크.
    실제 수정은 validate_5year_pipeline.py에서 처리.
    """
    if "unit_error" not in anomaly_types:
        return False
    rev = data.get("revenue")
    # 매출이 1조(1e12) 이상이면 억원 단위로 잘못 들어온 것 의심
    # 실제론 validate_5year_pipeline이 처리하므로 여기선 경고만
    if rev and rev > 1e13:
        return True
    return False


# ─── 메인 수집 루프 ──────────────────────────────────────────────────────────

def collect_extension(
    codes: Optional[list[str]],
    year_from: int,
    year_to: int,
    dry_run: bool,
    dart_daily_limit: int = DEFAULT_DART_DAILY,
) -> dict:
    conn = _conn()
    anomalies_map = load_anomalies(conn)
    profiles      = load_profiles()

    # 대상 종목 조회 (ETF/ETN 완전 제외)
    if codes:
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(f"""
            SELECT stock_code, stock_name, market
            FROM stock_universe
            WHERE stock_code IN ({placeholders})
              AND market IN ('KOSPI','KOSDAQ')
              AND COALESCE(stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(stock_name,'') NOT LIKE '%ETN%'
              AND COALESCE(stock_name,'') NOT LIKE '%인버스%'
              AND COALESCE(stock_name,'') NOT LIKE '%레버리지%'
            ORDER BY market, stock_code
        """, codes).fetchall()
    else:
        rows = conn.execute("""
            SELECT stock_code, stock_name, market
            FROM stock_universe
            WHERE market IN ('KOSPI','KOSDAQ')
              AND COALESCE(stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(stock_name,'') NOT LIKE '%ETN%'
              AND COALESCE(stock_name,'') NOT LIKE '%인버스%'
              AND COALESCE(stock_name,'') NOT LIKE '%레버리지%'
            ORDER BY market, stock_code
        """).fetchall()

    total_stocks   = len(rows)
    stats = {
        "total_stocks": total_stocks,
        "processed": 0,
        "skipped_etf": 0,
        "snapshots_new": 0,
        "financial_new": 0,
        "cashflow_new": 0,
        "errors": 0,
        "unit_warnings": 0,
        "dart_calls": 0,
        "dart_limit_reached": False,
        "newly_inserted_codes": [],
    }

    logger.info(
        f"대상 종목: {total_stocks}개 | 기간: {year_from}-{year_to} | "
        f"dart_daily_limit={dart_daily_limit} | dry_run={dry_run}"
    )

    COMMIT_EVERY = 30

    for idx, row in enumerate(rows):
        code  = row["stock_code"]
        name  = row["stock_name"]
        anom  = anomalies_map.get(code, [])
        rtypes = decide_report_types(code, anom)

        stock_inserted_any = False

        dart_limit_hit = False
        for year in range(year_from, year_to + 1):
            if should_skip_year(code, year, anom, profiles):
                logger.debug(f"SKIP {code} {year} (newly_listed 이전)")
                continue

            # 소스 결정
            use_dart = year < FNGUIDE_MIN_YEAR
            src_key  = "dart" if use_dart else "fnguide"
            gb = REPORT_TYPE_PARAMS.get("CFS", "")
            fnguide_url = (
                f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
                f"?pGB=1&gicode=A{code}&cID=&MenuYn=Y"
                f"&ReportGB={gb}&NewMenuID=103&stkGb=701"
            )

            for rt in rtypes:
                # 이미 스냅샷 있으면 건너뜀 (fnguide OR dart)
                existing_snap = conn.execute("""
                    SELECT id FROM financial_source_snapshot
                    WHERE stock_code=? AND year=? AND quarter=0
                      AND is_annual=1 AND report_type=? AND data_source=?
                    LIMIT 1
                """, (code, year, rt, src_key)).fetchone()
                if existing_snap:
                    logger.debug(f"SKIP snapshot {code} {year} {rt} {src_key}")
                    continue

                if use_dart:
                    # DART 일일 한도 체크
                    if stats["dart_calls"] >= dart_daily_limit:
                        logger.warning(f"DART 일일 한도({dart_daily_limit}) 도달 — 오늘 수집 중단")
                        stats["dart_limit_reached"] = True
                        dart_limit_hit = True
                        break
                    data = fetch_dart(code, rt, year)
                    src_url = f"dart://finstate_all/{code}/{year}/{rt}"
                    if data is not None:
                        stats["dart_calls"] += 1
                else:
                    gb_rt = REPORT_TYPE_PARAMS.get(rt, "")
                    src_url = (
                        f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
                        f"?pGB=1&gicode=A{code}&cID=&MenuYn=Y"
                        f"&ReportGB={gb_rt}&NewMenuID=103&stkGb=701"
                    )
                    data = fetch_fnguide(code, rt, year)

                if data is None:
                    stats["errors"] += 1
                    continue

                if _check_unit_error(data, anom):
                    logger.warning(f"⚠️ 단위오류 의심: {code} {year} {rt} revenue={data.get('revenue')}")
                    stats["unit_warnings"] += 1

                if dry_run:
                    logger.info(
                        f"[DRY] {code} {name} {year} {rt} [{src_key}]: "
                        f"rev={data.get('revenue','N/A')}"
                    )
                    continue

                snap_new = save_snapshot(conn, code, year, rt, data, src_url, src_key)
                if snap_new:
                    stats["snapshots_new"] += 1

                fin_new = insert_financial_if_missing(conn, code, year, rt, data, dry_run)
                if fin_new:
                    stats["financial_new"] += 1
                    stock_inserted_any = True

                cf_new = insert_cashflow_if_missing(conn, code, year, rt, data, dry_run)
                if cf_new:
                    stats["cashflow_new"] += 1
                    stock_inserted_any = True

            if dart_limit_hit:
                break  # 종목 루프도 중단

        stats["processed"] += 1
        if stock_inserted_any:
            stats["newly_inserted_codes"].append(code)

        if (idx + 1) % COMMIT_EVERY == 0 and not dry_run:
            conn.commit()
            logger.info(
                f"진행 {idx+1}/{total_stocks} | "
                f"snapshots+{stats['snapshots_new']} fin+{stats['financial_new']} "
                f"cf+{stats['cashflow_new']} dart_calls={stats['dart_calls']}"
            )

        if stats["dart_limit_reached"]:
            break  # 전체 종목 루프 중단

    if not dry_run:
        conn.commit()
    conn.close()

    logger.info(
        f"완료 | processed={stats['processed']} "
        f"snapshots+{stats['snapshots_new']} "
        f"fin+{stats['financial_new']} cf+{stats['cashflow_new']} "
        f"errors={stats['errors']}"
    )

    # 신규 삽입 종목 목록 저장 (ai_verify_financials.py에 넘기기 위해)
    REPORT_PATH.parent.mkdir(exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "year_from": year_from,
                "year_to": year_to,
                "dry_run": dry_run,
                **stats,
                "newly_inserted_codes": stats["newly_inserted_codes"][:500],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    logger.info(f"리포트: {REPORT_PATH}")

    # 신규 종목 재검증 안내
    if stats["newly_inserted_codes"] and not dry_run:
        code_arg = " ".join(stats["newly_inserted_codes"][:200])
        logger.info("━━━ 다음 명령으로 신규 데이터 재검증 진행 ━━━")
        logger.info(
            f"python3 scripts/ai_verify_financials.py "
            f"--codes {code_arg} --verifier 'claude-sonnet-4-6'"
        )

    return stats


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="10년치 재무 확장 수집 (2015-2021)")
    ap.add_argument("--year-from",       type=int, default=DEFAULT_YEAR_FROM)
    ap.add_argument("--year-to",         type=int, default=DEFAULT_YEAR_TO)
    ap.add_argument("--dart-daily-limit",type=int, default=DEFAULT_DART_DAILY,
                    help=f"하루 DART 호출 한도 (기본 {DEFAULT_DART_DAILY}종목)")
    ap.add_argument("--codes",     nargs="*", help="특정 종목코드")
    ap.add_argument("--dry-run",   action="store_true", help="실제 저장 없이 시뮬레이션")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / "scratch" / "collect_10year.log", encoding="utf-8"),
        ],
    )

    if args.year_from < 2010 or args.year_to > 2025:
        logger.error("연도 범위: 2010-2025 이내로 설정하세요.")
        sys.exit(1)

    stats = collect_extension(
        codes=args.codes,
        year_from=args.year_from,
        year_to=args.year_to,
        dry_run=args.dry_run,
        dart_daily_limit=args.dart_daily_limit,
    )

    if stats.get("dart_limit_reached"):
        logger.warning(
            f"오늘 DART 한도({args.dart_daily_limit}) 도달. "
            f"내일 동일 명령 재실행하면 중단 지점부터 재개."
        )


if __name__ == "__main__":
    main()
