"""
cf_triple_validator.py — DART·FnGuide·Seibro 3중 현금흐름 검증 모듈

신규 수집된 현금흐름(cash_flow_data) 행에 대해 자동으로 3중 검증 수행:
  DART    (cash_flow_data 최우선 소스 — CFS 연결 기준)
  FnGuide (cash_flow_data 보조 소스 — CFS 연결 기준)
  Seibro  (실시간 API 독립 소스 — 항상 연결 기준)

비교 결과 → cf_validation_flags 에 CONFIRMED / CLOSE_MATCH / AMBIGUOUS 기록

임계값 근거 (실데이터 95%+ 분석 기반):
  CONFIRMED  : ±3% 이내 — 실데이터 95% 이상이 0.1% 이내 정확 일치. 단위 반올림 허용.
  CLOSE_MATCH: 3~15% — 연결/별도 경계 영역. 자회사 비율 낮은 기업에서 발생 가능.
               DART report_type=CFS 확인 후에도 차이 → 추가 검토 필요.
  AMBIGUOUS  : 15% 초과 — 실질적 불일치. 주로 OFS/CFS 혼용 또는 실제 데이터 오류.

판정 규칙:
  ① DART ≈ Seibro (±3%)              → CONFIRMED  (DART 유지)
  ② DART ≈ FG (±3%), Seibro 없음     → CONFIRMED  (DART+FG 합의)
  ③ DART 3~15% ≈ Seibro 또는 FG     → CLOSE_MATCH (수동 확인 권고)
  ④ FG ≈ Seibro (±3%) ≠ DART        → AMBIGUOUS   (DART 오류 의심, FG값 보정)
  ⑤ DART None, FG ≈ Seibro (±3%)    → CONFIRMED  (FG 채택)
  ⑥ 15% 초과 불일치                   → AMBIGUOUS

스케줄: scheduler.py에서 매일 05:30 호출 (DART 03:30 + FnGuide 배치 이후)
독립 실행: python3 collectors/cf_triple_validator.py [--days 7] [--codes 005930] [--dry-run]
"""

import sys
import re
import time
import sqlite3
import logging
import argparse
from typing import Optional

import requests

sys.path.insert(0, "/Applications/stock_dashboard")

logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

# ── Seibro 설정 (validate_cf_seibro.py 와 동일) ────────────────────────
SEIBRO_BASE = "https://seibro.or.kr"
SERVLET_URL = f"{SEIBRO_BASE}/websquare/engine/proworks/callServletService.jsp"
CD_OCF      = "4001390000"   # 영업활동현금흐름
CD_ICF      = "4001390160"   # 투자활동현금흐름
CD_CASH_END = "4001340050"   # 기말현금및현금성자산
YEAR_COL    = {0: "A1", 1: "A6", 2: "A11", 3: "A16"}

_FIELD_MAP = {
    "operating_cf": CD_OCF,
    "investing_cf": CD_ICF,
    "cash_end":     CD_CASH_END,
}
CONFIRMED_RATIO   = 0.03   # ±3%  → CONFIRMED (실데이터 95%+ 0.1% 이내 → 반올림 여유 포함)
CLOSE_MATCH_RATIO = 0.15   # ±15% → CLOSE_MATCH (연결/별도 경계 회색지대)
# 15% 초과 → AMBIGUOUS

# Seibro ISSUCO_CUSTNO 캐시 (세션 내 재사용)
_custno_cache: dict[str, Optional[str]] = {}


# ══════════════════════════════════════════════════════════
# DB 유틸
# ══════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def get_recent_dart_rows(conn: sqlite3.Connection, days: int, codes: list[str]) -> list[dict]:
    """최근 N일 내 수집된 연간 DART CF 행 반환."""
    code_filter = ""
    params: list = [days]
    if codes:
        placeholders = ",".join("?" * len(codes))
        code_filter = f"AND stock_code IN ({placeholders})"
        params += codes

    rows = conn.execute(f"""
        SELECT DISTINCT stock_code, year
        FROM cash_flow_data
        WHERE is_annual = 1
          AND data_source LIKE 'dart%'
          AND created_at >= datetime('now', '-' || ? || ' days')
          {code_filter}
        ORDER BY stock_code, year
    """, params).fetchall()
    return [dict(r) for r in rows]


def get_cf_values(conn: sqlite3.Connection, stock_code: str, year: int) -> dict:
    """
    cash_flow_data에서 DART·FnGuide 연간 CF 값 반환.
    반환: {
      'dart':    {'operating_cf': val_원, ..., 'report_type': 'CFS'},
      'fnguide': {'operating_cf': val_원, ...}
    }
    값이 없으면 None. DB 저장 단위는 원(₩).
    """
    rows = conn.execute("""
        SELECT data_source, operating_cf, investing_cf, cash_end, report_type
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND is_annual=1
        ORDER BY id
    """, (stock_code, year)).fetchall()

    result = {"dart": {}, "fnguide": {}}
    for r in rows:
        ds = (r["data_source"] or "").lower()
        bucket = None
        if ds.startswith("dart") or ds in ("", "null"):
            bucket = "dart"
        elif ds.startswith("fnguide"):
            bucket = "fnguide"
        if bucket is None:
            continue
        for field in ("operating_cf", "investing_cf", "cash_end"):
            existing = result[bucket].get(field)
            new_val = r[field]
            if existing is None and new_val is not None:
                result[bucket][field] = new_val
        # report_type: CFS 우선 (CFS가 있으면 그것을 사용)
        if bucket == "dart":
            rt = r["report_type"] or "CFS"
            if result["dart"].get("report_type") != "CFS":
                result["dart"]["report_type"] = rt

    return result


# ══════════════════════════════════════════════════════════
# Seibro API
# ══════════════════════════════════════════════════════════

def _seibro_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/xml; charset=UTF-8",
        "Referer": "https://seibro.or.kr/",
    })
    try:
        s.get(SEIBRO_BASE, timeout=10)
    except Exception:
        pass
    return s


def _post(session: requests.Session, payload: str, retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            resp = session.post(SERVLET_URL, data=payload.encode("utf-8"), timeout=20)
            return resp.text
        except requests.RequestException:
            if attempt == retries:
                return ""
            time.sleep(2)
    return ""


def _get_val(tag: str, text: str) -> str:
    m = re.search(rf'<{tag}\s+value="([^"]*)"', text)
    return m.group(1) if m else ""


def lookup_custno(session: requests.Session, stock_code: str) -> Optional[str]:
    if stock_code in _custno_cache:
        return _custno_cache[stock_code]
    payload = (
        f'<reqParam action="searchCompanyContentList" '
        f'task="ksd.safe.bip.cmuc.User.process.SearchPTask">'
        f'<search_string value="{stock_code}"/></reqParam>'
    )
    content = _post(session, payload)
    results = re.findall(r"<result>(.*?)</result>", content, re.DOTALL)
    custno = None
    for r in results:
        c = _get_val("ISSUCO_CUSTNO", r)
        if c:
            custno = c
            break
    _custno_cache[stock_code] = custno
    return custno


def fetch_seibro_cf(session: requests.Session, stock_code: str, year: int) -> Optional[dict]:
    """
    Seibro에서 특정 종목/연도 CF 조회.
    반환: {'operating_cf': 백만원, 'investing_cf': 백만원, 'cash_end': 백만원} 또는 None
    """
    custno = lookup_custno(session, stock_code)
    if not custno:
        return None

    def _query(fs_type: str) -> Optional[dict]:
        payload = (
            f'<reqParam action="cashFlowTableList" '
            f'task="ksd.safe.bip.cnts.Company.process.EntrFnafInfoPTask">'
            f'<ISSUCO_CUSTNO value="{custno}"/>'
            f'<UNIT value="1000000"/>'
            f'<TO_YEAR value="{year}"/>'
            f'<TYPE value="{fs_type}"/>'
            f'<FROM_YEAR value="{year - 1}"/>'
            f"</reqParam>"
        )
        content = _post(session, payload)
        results = re.findall(r"<result>(.*?)</result>", content, re.DOTALL)
        if not results:
            return None

        col_year = {v: year - k for k, v in YEAR_COL.items()}
        data: dict[str, dict[int, Optional[float]]] = {}
        for r in results:
            cd = _get_val("FNAF_ACCSUBJ_CD", r)
            if not cd:
                continue
            yr_vals: dict[int, Optional[float]] = {}
            for col, yr in col_year.items():
                raw = _get_val(col, r)
                try:
                    yr_vals[yr] = float(raw) if raw else None
                except ValueError:
                    yr_vals[yr] = None
            data[cd] = yr_vals

        if not data:
            return None

        return {
            "operating_cf": data.get(CD_OCF, {}).get(year),
            "investing_cf": data.get(CD_ICF, {}).get(year),
            "cash_end":     data.get(CD_CASH_END, {}).get(year),
        }

    result = _query("연결")
    # 연결 값이 전부 None이면 별도 시도
    if result and all(v is None for v in result.values()):
        result = _query("별도")
    return result


# ══════════════════════════════════════════════════════════
# 3중 검증 판정 로직
# ══════════════════════════════════════════════════════════

def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """두 값의 절대 차이 비율. None이면 None 반환."""
    if a is None or b is None:
        return None
    denom = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denom


def _close(a: Optional[float], b: Optional[float], ratio: float = CONFIRMED_RATIO) -> bool:
    r = _ratio(a, b)
    return r is not None and r <= ratio


def adjudicate(
    dart_원: Optional[float],
    fg_원:   Optional[float],
    seibro_백만: Optional[float],
    dart_report_type: str = "CFS",
) -> tuple[str, str, Optional[float]]:
    """
    3-way 판정.
    반환: (status, verdict_code, chosen_value_백만원)

    임계값:
      CONFIRMED   : ±3%  (실데이터 95%+ 0.1% 이내. 단위 반올림 허용)
      CLOSE_MATCH : 3~15% (연결/별도 경계 회색지대 — 수동 확인 권고)
      AMBIGUOUS   : 15% 초과 (실질적 불일치)

    Seibro는 항상 연결(CFS) 기준.
    DART report_type=OFS면 별도 기준이므로 Seibro 비교 의미 없음.
    """
    dart_mn = dart_원 / 1_000_000 if dart_원 is not None else None
    fg_mn   = fg_원   / 1_000_000 if fg_원   is not None else None
    seibro  = seibro_백만

    # DART가 OFS(별도)인 경우: Seibro(연결)와 비교는 의미 없음 → FG하고만 비교
    dart_cfs = (dart_report_type or "CFS").upper() == "CFS"

    # ── 규칙 ①: DART(CFS) ≈ Seibro ±3%
    if dart_cfs and _close(dart_mn, seibro, CONFIRMED_RATIO):
        return "CONFIRMED", "dart_seibro_agree", dart_mn

    # ── 규칙 ②: DART ≈ FG ±3% (Seibro 없거나 OFS)
    if _close(dart_mn, fg_mn, CONFIRMED_RATIO):
        return "CONFIRMED", "dart_fg_agree", dart_mn

    # ── 규칙 ⑤: DART None, FG ≈ Seibro ±3%
    if dart_mn is None and _close(fg_mn, seibro, CONFIRMED_RATIO):
        return "CONFIRMED", "fg_seibro_agree", fg_mn

    # ── DART OFS: Seibro는 연결 기준이므로 비교 불가. FG와만 비교.
    if not dart_cfs:
        if dart_mn is None:
            return "CONFIRMED", "dart_ofs_none", fg_mn
        if fg_mn is None:
            return "CONFIRMED", "dart_ofs_no_fg", dart_mn
        r_fg = _ratio(dart_mn, fg_mn)
        if r_fg is not None and r_fg <= CONFIRMED_RATIO:
            return "CONFIRMED", "dart_ofs_fg_agree", dart_mn
        if r_fg is not None and r_fg <= CLOSE_MATCH_RATIO:
            return "CLOSE_MATCH", "dart_ofs_fg_close", dart_mn
        return "AMBIGUOUS", "dart_ofs_fg_mismatch", None

    # ── DART 단독 소스 (CFS, FG·Seibro 없음)
    if seibro is None and fg_mn is None and dart_mn is not None:
        return "CONFIRMED", "dart_only", dart_mn

    # ── 규칙 ③: CLOSE_MATCH 구간 (3~15%)
    r_dart_seibro = _ratio(dart_mn, seibro) if dart_cfs else None
    r_dart_fg     = _ratio(dart_mn, fg_mn)
    r_fg_seibro   = _ratio(fg_mn, seibro)

    if (r_dart_seibro is not None and r_dart_seibro <= CLOSE_MATCH_RATIO) or \
       (r_dart_fg is not None and r_dart_fg <= CLOSE_MATCH_RATIO):
        return "CLOSE_MATCH", "close_3_15pct", dart_mn

    # ── 규칙 ④: FG ≈ Seibro ±3% ≠ DART (DART 오류 의심)
    if _close(fg_mn, seibro, CONFIRMED_RATIO) and dart_mn is not None:
        return "AMBIGUOUS", "dart_vs_fg_seibro", fg_mn

    # ── 규칙 ⑥: 15% 초과 전방위 불일치
    return "AMBIGUOUS", "three_way_mismatch", None


def upsert_flag(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    field: str,
    dart_mn: Optional[float],
    fg_mn: Optional[float],
    seibro_mn: Optional[float],
    status: str,
    verdict: str,
    dry_run: bool,
) -> str:
    """cf_validation_flags INSERT OR REPLACE."""
    if dry_run:
        return f"DRY {status}"

    # 기존 CONFIRMED는 덮어쓰지 않음 (이미 검증 완료)
    # CLOSE_MATCH는 CONFIRMED보다 낮은 신뢰도이므로 기존 CONFIRMED 유지
    existing = conn.execute("""
        SELECT id, status FROM cf_validation_flags
        WHERE stock_code=? AND year=? AND field=?
    """, (stock_code, year, field)).fetchone()

    if existing and existing["status"] == "CONFIRMED" and status in ("AMBIGUOUS", "CLOSE_MATCH"):
        return "skip_confirmed"

    if existing:
        conn.execute("""
            UPDATE cf_validation_flags
            SET dart_value=?, fnguide_value=?, seibro_value=?,
                status=?, ai_verdict=?, resolved_at=datetime('now')
            WHERE id=?
        """, (dart_mn, fg_mn, seibro_mn, status, verdict, existing["id"]))
        return "updated"
    else:
        conn.execute("""
            INSERT INTO cf_validation_flags
                (stock_code, year, field, dart_value, fnguide_value, seibro_value,
                 status, ai_verdict, created_at)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'))
        """, (stock_code, year, field, dart_mn, fg_mn, seibro_mn, status, verdict))
        return "inserted"


# ══════════════════════════════════════════════════════════
# 메인 실행 함수 (scheduler에서 호출)
# ══════════════════════════════════════════════════════════

def validate_recent(
    days: int = 7,
    codes: Optional[list[str]] = None,
    dry_run: bool = False,
) -> dict:
    """
    최근 days일 내 신규 DART CF 데이터를 3중 검증.
    scheduler.py의 _job_cf_triple_validate에서 호출.
    """
    conn = _conn()
    session = _seibro_session()

    targets = get_recent_dart_rows(conn, days, codes or [])
    logger.info(f"[3중검증] 대상 {len(targets)}건 (최근 {days}일)")

    stats = {"confirmed": 0, "ambiguous": 0, "skipped": 0, "total": len(targets)}

    for i, target in enumerate(targets):
        stock_code = target["stock_code"]
        year = target["year"]

        try:
            db_vals = get_cf_values(conn, stock_code, year)
            dart_vals = db_vals["dart"]
            fg_vals   = db_vals["fnguide"]
            dart_report_type = dart_vals.get("report_type", "CFS")

            # Seibro 조회 (연결 기준 항상)
            seibro_cf = fetch_seibro_cf(session, stock_code, year)
            time.sleep(0.4)

            any_change = False
            for field in ("operating_cf", "investing_cf", "cash_end"):
                dart_원 = dart_vals.get(field)
                fg_원   = fg_vals.get(field)
                seibro_mn = seibro_cf.get(field) if seibro_cf else None

                status, verdict, chosen = adjudicate(dart_원, fg_원, seibro_mn, dart_report_type)

                op = upsert_flag(
                    conn, stock_code, year, field,
                    dart_원 / 1_000_000 if dart_원 is not None else None,
                    fg_원   / 1_000_000 if fg_원   is not None else None,
                    seibro_mn,
                    status, verdict, dry_run,
                )

                if op not in ("skip_confirmed",):
                    any_change = True
                    if status == "CONFIRMED":
                        stats["confirmed"] += 1
                    else:
                        stats["ambiguous"] += 1

                        # AMBIGUOUS 시 FG ≈ Seibro (±3%)이고 DART CFS인데 다른 경우만 보정
                        # DART가 OFS면 Seibro(CFS)와 달라도 정상일 수 있으므로 보정 안 함
                        if (verdict == "dart_vs_fg_seibro" and fg_원 is not None
                                and dart_report_type == "CFS" and not dry_run):
                            _apply_fg_correction(conn, stock_code, year, field, fg_원)

                    logger.debug(
                        f"  {stock_code} {year} {field}: {verdict} → {status}"
                        f" (dart={dart_원/1e6:.0f}백만" if dart_원 else "" +
                        f" fg={fg_원/1e6:.0f}백만" if fg_원 else "" +
                        f" seibro={seibro_mn:.0f}백만)" if seibro_mn else ")"
                    )

            if not dry_run and any_change:
                conn.commit()

            if i % 50 == 0:
                logger.info(f"[3중검증] {i+1}/{len(targets)} 처리 중…")

        except Exception as e:
            logger.warning(f"[3중검증] {stock_code} {year} 오류: {e}")
            stats["skipped"] += 1

    conn.close()
    logger.info(
        f"[3중검증] 완료 — CONFIRMED:{stats['confirmed']} "
        f"AMBIGUOUS:{stats['ambiguous']} SKIP:{stats['skipped']}"
    )
    return stats


def _apply_fg_correction(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    field: str,
    fg_원: float,
) -> None:
    """FG ≈ Seibro ≠ DART 케이스: cash_flow_data DART 행의 해당 필드를 FG 값으로 교체."""
    existing = conn.execute("""
        SELECT id FROM cash_flow_data
        WHERE stock_code=? AND year=? AND is_annual=1 AND data_source LIKE 'dart%'
        ORDER BY id LIMIT 1
    """, (stock_code, year)).fetchone()
    if existing:
        conn.execute(
            f"UPDATE cash_flow_data SET {field}=?, data_source='dart_fg_corrected' WHERE id=?",
            (fg_원, existing["id"])
        )
        logger.info(f"  → {stock_code} {year} {field} FG 값으로 보정 (dart_fg_corrected)")


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="CF 3중 검증 실행")
    parser.add_argument("--days", type=int, default=7, help="최근 N일 내 수집 행 대상 (기본 7)")
    parser.add_argument("--codes", nargs="*", help="특정 종목코드 지정")
    parser.add_argument("--dry-run", action="store_true", help="DB 수정 없이 결과만 출력")
    args = parser.parse_args()

    result = validate_recent(days=args.days, codes=args.codes, dry_run=args.dry_run)
    print(f"\n결과: {result}")
