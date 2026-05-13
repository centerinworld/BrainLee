"""
fnguide_financial_collector.py — FnGuide 연결/별도 재무제표 일괄 수집

기능:
  1. FnGuide SVD_Finance.asp → 연결(CFS) + 별도(OFS) 각각 수집
  2. financial_source_snapshot에 원본 저장 (AI 감사 추적용)
  3. financial_data + cash_flow_data에 CFS/OFS 각각 UPSERT
  4. 코스피·코스닥 보통주만 처리 (ETF/ETN 제외)
  5. 불일치는 financial_profiles_pending.json에 자동 누적

실행:
    # 소규모 테스트 (50종목)
    python3 collectors/fnguide_financial_collector.py --limit 50

    # 전 종목 수집
    python3 collectors/fnguide_financial_collector.py --limit 9999

    # 연결만 (빠름)
    python3 collectors/fnguide_financial_collector.py --limit 9999 --report-types CFS

    # 오래된 스냅샷 먼저 (30일 이상)
    python3 collectors/fnguide_financial_collector.py --limit 9999 --stale-days 30
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_rate_limiter import api_limiter
from financial_profiles import append_pending_mismatch, get_profile

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

TOLERANCE_PCT = 0.03
TOLERANCE_ABS = 5e8   # 5억원 이하 차이 무시

FNGUIDE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://comp.fnguide.com/",
}

# 연결: ReportGB= (빈값), 별도: ReportGB=B
REPORT_TYPE_PARAMS = {
    "CFS": "",   # 연결재무제표
    "OFS": "B",  # 별도재무제표
}


# ─────────────────────────────────────────────────────────
# DB 유틸
# ─────────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _parse_val(raw) -> Optional[float]:
    s = str(raw).replace(",", "").strip()
    if s in ("", "-", "N/A", "nan", "NaN"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def _flatten(c) -> str:
    if isinstance(c, tuple):
        return " ".join(str(x) for x in c if str(x) != "nan")
    return str(c)


def _close_match(a: Optional[float], b: Optional[float]) -> bool:
    if a is None or b is None:
        return False
    diff = abs(a - b)
    if diff < TOLERANCE_ABS:
        return True
    denom = max(abs(a), abs(b))
    return denom > 0 and diff / denom <= TOLERANCE_PCT


# ─────────────────────────────────────────────────────────
# DART 교차검증 (수집 즉시 FnGuide vs DART 비교)
# ─────────────────────────────────────────────────────────
_DART_FS_DIV    = {"CFS": "CFS", "OFS": "OFS"}
_DART_RPRT_CODE = "11011"  # 사업보고서(연간)
_CROSS_TOL      = 0.05     # 5% 이내 → 검증됨
_CROSS_FIELDS   = ["revenue", "operating_profit", "net_income",
                   "total_assets", "total_equity"]
_DART_ACCOUNT_MAP: dict[str, list[str]] = {
    "revenue":          ["매출액", "영업수익", "수익(매출액)", "매출"],
    "operating_profit": ["영업이익"],
    "net_income":       ["지배기업소유주지분순이익", "지배주주순이익", "당기순이익"],
    "total_assets":     ["자산총계"],
    "total_liabilities":["부채총계"],
    "total_equity":     ["자본총계"],
}
_DART_EXCLUDE_MAP: dict[str, list[str]] = {
    "net_income":   ["비지배", "감소", "취소"],
    "total_equity": ["비지배", "기타포괄", "자본조정"],
}


def _fetch_dart_annual(stock_code: str, report_type: str, year: int) -> Optional[dict]:
    """DART에서 연간 재무 핵심 필드 수집. 교차검증 전용 (쿼터 소비 주의).
    반환: {revenue, operating_profit, net_income, total_assets, total_equity} (원 단위)
    None: DART 실패 또는 쿼터 소진.
    """
    if not api_limiter.wait("DART"):
        return None

    try:
        import OpenDartReader as _ODR  # type: ignore
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from config import settings
        dart = _ODR.OpenDartReader(settings.DART_API_KEY)
    except Exception:
        return None

    fs_div = _DART_FS_DIV.get(report_type, "CFS")
    try:
        df = dart.finstate_all(stock_code, year, _DART_RPRT_CODE, fs_div=fs_div)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    import pandas as _pd
    labels = df.get("account_nm", df.get("account_detail", _pd.Series(dtype=str))).astype(str)
    labels_ns = labels.str.replace(" ", "", regex=False)

    result: dict = {}
    for key, kws in _DART_ACCOUNT_MAP.items():
        excls = _DART_EXCLUDE_MAP.get(key, [])
        for kw in kws:
            mask = labels_ns.str.contains(kw.replace(" ", ""), na=False, regex=False)
            for ex in excls:
                mask = mask & ~labels_ns.str.contains(ex.replace(" ", ""), na=False, regex=False)
            rows = df.loc[mask]
            if rows.empty:
                continue
            col = next((c for c in ["thstrm_amount", "thstrm_add_amount"] if c in rows.columns), None)
            if col is None:
                continue
            # 중복 행: 절댓값 최대 행 선택
            if len(rows) > 1:
                def _pf(r):
                    try: return abs(float(str(r[col]).replace(",", "")))
                    except: return 0
                rows = rows.loc[[rows.apply(_pf, axis=1).idxmax()]]
            raw = str(rows.iloc[0][col]).replace(",", "").strip()
            try:
                result[key] = float(raw)
                break
            except Exception:
                continue

    if not result:
        return None

    # 단위 보정 (동일 로직 인라인)
    rev = result.get("revenue") or result.get("total_assets")
    if rev and abs(rev) < 1e4:
        result = {k: v * 1_000_000 for k, v in result.items()}
    elif rev and abs(rev) < 1e8:
        assets = result.get("total_assets", 0) or 0
        if not (assets > 0 and assets < abs(rev) * 100):
            result = {k: v * 1_000 for k, v in result.items()}

    return result


def cross_validate_annual(
    conn: sqlite3.Connection,
    stock_code: str,
    report_type: str,
    year: int,
    fng_data: dict,
    snapshot_id: int,
) -> str:
    """FnGuide와 DART 연간 데이터 비교. financial_source_snapshot.verification_status 업데이트.
    반환: 'verified' | 'mismatch' | 'unverified'
    """
    dart_data = _fetch_dart_annual(stock_code, report_type, year)
    if dart_data is None:
        return "unverified"

    mismatches: list[str] = []
    for f in _CROSS_FIELDS:
        fv = fng_data.get(f)
        dv = dart_data.get(f)
        if fv is None or dv is None or fv == 0 or dv == 0:
            continue
        ratio = abs(fv - dv) / max(abs(fv), abs(dv))
        if ratio > _CROSS_TOL:
            mismatches.append(f"{f}: FnG={fv/1e8:.1f}억 DART={dv/1e8:.1f}억 ({ratio*100:.1f}%차)")

    if mismatches:
        note = "DART불일치: " + "; ".join(mismatches)
        status = "mismatch"
        logger.warning(f"[교차검증] {stock_code} {report_type} {year} — {note}")
    else:
        note = f"DART교차검증OK ({len([f for f in _CROSS_FIELDS if fng_data.get(f)])}개필드)"
        status = "verified"

    conn.execute(
        "UPDATE financial_source_snapshot SET verification_status=?, verification_note=? WHERE id=?",
        (status, note, snapshot_id)
    )
    conn.commit()
    return status


# ─────────────────────────────────────────────────────────
# Q4 = Annual - Q1 - Q2 - Q3 재계산 (페이지 Q4 신뢰 불가)
# ─────────────────────────────────────────────────────────
_Q4_FIELDS    = ["revenue", "operating_profit", "net_income",
                 "operating_cf", "investing_cf", "financing_cf", "capex"]
_Q4_CF_FIELDS = ["operating_cf", "investing_cf", "financing_cf", "capex"]


def compute_and_upsert_q4(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    report_type: str,
    annual_data: dict,
    quarterly_data: dict,  # {1: {...}, 2: {...}, 3: {...}}
) -> str:
    """Q4 증분 = Annual - Q1 - Q2 - Q3 계산 후 DB UPSERT.
    FnGuide 페이지의 Q4 컬럼은 누적/증분 혼용이 있으므로 직접 계산.
    반환: 'inserted' | 'updated' | 'skipped'
    """
    q4_data: dict = {}
    for f in _Q4_FIELDS:
        ann_v = annual_data.get(f)
        q1_v  = quarterly_data.get(1, {}).get(f)
        q2_v  = quarterly_data.get(2, {}).get(f)
        q3_v  = quarterly_data.get(3, {}).get(f)
        if ann_v is None or q1_v is None or q2_v is None or q3_v is None:
            continue
        q4_data[f] = ann_v - q1_v - q2_v - q3_v

    if not q4_data:
        return "skipped"

    fin_q4  = {k: v for k, v in q4_data.items() if k not in _Q4_CF_FIELDS}
    cf_q4   = {k: v for k, v in q4_data.items() if k in _Q4_CF_FIELDS}

    fin_res = upsert_financial(conn, stock_code, year, 4, 0, report_type, fin_q4, override=True)
    cf_res  = upsert_cashflow(conn, stock_code, year, 4, 0, report_type, cf_q4, override=True)

    if "insert" in fin_res or "insert" in cf_res:
        return "inserted"
    if "overridden" in fin_res or "overridden" in cf_res:
        return "updated"
    return "skipped"


# ─────────────────────────────────────────────────────────
# 자동 학습: 성공한 계정명 → financial_profiles.json 영구 저장
# ─────────────────────────────────────────────────────────
_PROFILE_LOCK = __import__("threading").Lock()
_PROFILE_FILE = Path(__file__).resolve().parent.parent / "config" / "financial_profiles.json"

# field → profile JSON 키 매핑
_FIELD_TO_PROFILE_KEY = {
    "revenue":          "revenue_keywords",
    "operating_profit": "op_profit_keywords",
    "net_income":       "net_income_keywords",
    "total_assets":     "asset_keywords",
    "total_equity":     "equity_keywords",
    "operating_cf":     "op_cf_keywords",
    "investing_cf":     "inv_cf_keywords",
    "financing_cf":     "fin_cf_keywords",
}
# 기본 키워드와 동일한 것은 저장하지 않음 (불필요한 프로파일 오염 방지)
_DEFAULT_KEYWORDS = {
    "revenue_keywords":    {"매출액", "매출", "영업수익"},
    "op_profit_keywords":  {"영업이익"},
    "net_income_keywords": {"지배주주순이익", "당기순이익"},
    "op_cf_keywords":      {"영업활동으로인한현금흐름", "영업활동현금흐름"},
    "inv_cf_keywords":     {"투자활동으로인한현금흐름", "투자활동현금흐름"},
    "fin_cf_keywords":     {"재무활동으로인한현금흐름", "재무활동현금흐름"},
}

def _auto_update_profile(stock_code: str, learned: dict[str, str]) -> None:
    """파싱에서 발견한 실제 계정명을 financial_profiles.json에 기록.

    - 기본 키워드와 동일한 것은 저장 안 함 (잡음 방지)
    - 비표준 계정명만 저장 → 다음 수집부터 키워드 매칭 즉시 성공
    - 스레드 안전 (파일 락)
    """
    with _PROFILE_LOCK:
        try:
            profiles: dict = {}
            if _PROFILE_FILE.exists():
                with _PROFILE_FILE.open("r", encoding="utf-8") as f:
                    profiles = json.load(f)
        except Exception:
            profiles = {}

        prof = profiles.setdefault(stock_code, {})
        changed = False
        for field, acct_name in learned.items():
            pkey = _FIELD_TO_PROFILE_KEY.get(field)
            if not pkey:
                continue
            acct_clean = acct_name.replace(" ", "")
            defaults   = _DEFAULT_KEYWORDS.get(pkey, set())
            if acct_clean in defaults:
                continue  # 기본 키워드와 동일 → 저장 불필요
            # 이미 프로파일에 있으면 스킵
            existing = set(prof.get(pkey, []))
            if acct_clean not in existing and acct_name not in existing:
                prof.setdefault(pkey, []).insert(0, acct_name)  # 앞에 삽입 (우선 적용)
                changed = True
                logger.debug(f"[프로파일] {stock_code} {pkey} += '{acct_name}'")

        if changed:
            try:
                with _PROFILE_FILE.open("w", encoding="utf-8") as f:
                    json.dump(profiles, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"[프로파일] {stock_code} 저장 실패: {e}")


# ─────────────────────────────────────────────────────────
# FnGuide 파싱
# ─────────────────────────────────────────────────────────
def fetch_fnguide_all(stock_code: str, report_type: str) -> dict:
    """
    FnGuide SVD_Finance.asp 1회 요청으로 P&L + BS + CF 모두 추출.
    report_type: 'CFS'(연결) or 'OFS'(별도)
    반환: {
      'annual':   {year: {revenue, operating_profit, net_income, total_assets, total_equity,
                          operating_cf, investing_cf, financing_cf, capex}},
      'quarterly':{year: {quarter: {...}}},
      'source_url': str,
    }
    단위: 억원 → × 1e8 → 원
    """
    gb_param = REPORT_TYPE_PARAMS[report_type]
    url = (
        f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode=A{stock_code}&cID=&MenuYn=Y"
        f"&ReportGB={gb_param}&NewMenuID=103&stkGb=701"
    )

    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    try:
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return {}

    prof = get_profile(stock_code)

    # ── 섹터별 기본 키워드 ────────────────────────────────────────────────────
    # FnGuide는 업종에 따라 최상위 매출 항목명이 다름 (정규화하지 않음).
    # 키워드 순서가 중요: 가장 구체적인 것을 먼저.
    _REV_KW_DEFAULT = [
        # 프로파일 우선
        *prof.get("revenue_keywords", []),
        # 일반 제조·서비스
        "매출액", "매출",
        # 금융·보험 (항상 첫 행이 영업수익 집계)
        "영업수익",
        # 건설
        "도급공사수익", "건설수익",
        # 운수·해운
        "운임수익", "운송수익",
        # 은행
        "이자수익", "순이자이익",
        # 증권
        "수수료수익",
        # 위 모두 실패 시 → 아래에서 위치 기반 폴백 적용
    ]
    _OP_KW_DEFAULT  = [*prof.get("op_profit_keywords", []), "영업이익"]
    _NI_KW_DEFAULT  = [
        *prof.get("net_income_keywords", []),
        "지배기업의소유주에게귀속되는당기순이익",
        "지배주주순이익", "지배주주지분순이익",
        "당기순이익", "반기순이익", "분기순이익",
        "계속영업이익",
    ]
    _NI_EXCL_DEFAULT = [*prof.get("net_income_exclude_keywords", []), "비지배", "귀속되지않는"]
    _OP_CF_KW = [*prof.get("op_cf_keywords",  []), "영업활동으로인한현금흐름", "영업활동현금흐름"]
    _INV_CF_KW= [*prof.get("inv_cf_keywords", []), "투자활동으로인한현금흐름", "투자활동현금흐름"]
    _FIN_CF_KW= [*prof.get("fin_cf_keywords", []), "재무활동으로인한현금흐름", "재무활동현금흐름"]
    _CAPEX_KW = ["설비투자", "유형자산의취득", "유형자산취득", "CAPEX", "자본적지출"]

    annual: dict[int, dict] = {}
    quarterly: dict[int, dict[int, dict]] = {}
    _learned_names: dict[str, str] = {}   # field → 실제 사용된 계정명 (자동 학습용)

    for t in tables:
        if t.empty or t.shape[1] < 2:
            continue
        t = t.copy()
        t.columns = [_flatten(c) for c in t.columns]
        labels_raw = t.iloc[:, 0].astype(str)
        labels     = labels_raw.str.replace(" ", "", regex=False)

        # ── 연간/분기 컬럼 분류 ─────────────────────────────────────────────
        ann_cols: dict[int, str] = {}
        for c in t.columns[1:]:
            m = re.search(r"(20\d{2})/12", str(c))
            if m:
                ann_cols[int(m.group(1))] = c

        q_map = {"03": 1, "06": 2, "09": 3, "12": 4}
        qtr_cols: dict[tuple[int, int], str] = {}
        for c in t.columns[1:]:
            m = re.search(r"(20\d{2})/(03|06|09)", str(c))   # 12월 분기는 연간과 중복 → 제외
            if m:
                yr, mo = int(m.group(1)), m.group(2)
                qtr_cols[(yr, q_map[mo])] = c

        if not ann_cols and not qtr_cols:
            continue

        # ── 키워드 기반 행 탐색 ─────────────────────────────────────────────
        def _find(keywords: list[str], excludes: list[str] = [],
                  field: str = "") -> Optional[pd.Series]:
            mask = pd.Series([False] * len(t))
            for k in keywords:
                mask = mask | labels.str.contains(re.escape(k.replace(" ", "")), na=False)
            for ex in excludes:
                mask = mask & ~labels.str.contains(re.escape(ex.replace(" ", "")), na=False)
            rows = t.loc[mask]
            if not rows.empty:
                row = rows.iloc[0]
                if field:
                    _learned_names[field] = labels_raw.iloc[rows.index[0]]
                return row
            return None

        # ── 테이블 유형 판별 ─────────────────────────────────────────────────
        # FnGuide 구조: [IS연간, IS분기, BS연간, BS분기, CF연간, CF분기]
        has_is  = labels.str.contains("영업이익|영업수익|매출액|이자수익", na=False).any()
        has_bs  = labels.str.contains("자산|부채|자본", na=False).any()
        has_cf  = labels.str.contains("영업활동|투자활동|재무활동", na=False).any()

        rev_row = op_row = ni_row = asset_row = eq_row = None
        op_cf_row = inv_cf_row = fin_cf_row = capex_row = None

        if has_is:
            op_row  = _find(_OP_KW_DEFAULT, field="operating_profit")
            ni_row  = _find(_NI_KW_DEFAULT, _NI_EXCL_DEFAULT, field="net_income")
            rev_row = _find(_REV_KW_DEFAULT, field="revenue")

            # ── 위치 기반 폴백: revenue가 미탐지 → 첫 번째 유효 행 사용 ──────
            # FnGuide 손익계산서는 항상 최상위 매출 지표가 첫 행에 위치.
            # 보험/금융: "영업수익계산에 참여한 계정 펼치기" 등 비표준 명칭도 첫 행.
            if rev_row is None and not has_bs and not has_cf:
                # 첫 행의 주요 연도 컬럼에 숫자가 있으면 매출로 간주
                first_col = list(ann_cols.values() or qtr_cols.values())[0] if (ann_cols or qtr_cols) else None
                if first_col and _parse_val(t.iloc[0][first_col]) is not None:
                    rev_row = t.iloc[0]
                    _learned_names["revenue"] = labels_raw.iloc[0]
                    logger.debug(f"{stock_code}: revenue 위치 기반 폴백 → '{labels_raw.iloc[0]}'")

        if has_bs and not has_cf:
            asset_row = _find(["자산총계", "자산"], field="total_assets")
            eq_row    = _find(["자본총계", "자본"], ["비지배", "기타금융", "기타포괄"], field="total_equity")

        if has_cf:
            op_cf_row  = _find(_OP_CF_KW,  field="operating_cf")
            inv_cf_row = _find(_INV_CF_KW, field="investing_cf")
            fin_cf_row = _find(_FIN_CF_KW, field="financing_cf")
            capex_row  = _find(_CAPEX_KW,  field="capex")

        has_any = any(r is not None for r in [
            rev_row, op_row, ni_row, asset_row, eq_row,
            op_cf_row, inv_cf_row, fin_cf_row
        ])
        if not has_any:
            continue

        # ── 연간 저장 ────────────────────────────────────────────────────────
        for yr, col in ann_cols.items():
            ydata = annual.setdefault(yr, {})
            for key, row in [
                ("revenue", rev_row), ("operating_profit", op_row), ("net_income", ni_row),
                ("total_assets", asset_row), ("total_equity", eq_row),
                ("operating_cf", op_cf_row), ("investing_cf", inv_cf_row),
                ("financing_cf", fin_cf_row), ("capex", capex_row),
            ]:
                if row is None or key in ydata:
                    continue
                v = _parse_val(row[col])
                if v is not None:
                    ydata[key] = v * 1e8  # 억원 → 원

        # ── 분기 저장 ────────────────────────────────────────────────────────
        for (yr, q), col in qtr_cols.items():
            qdata = quarterly.setdefault(yr, {}).setdefault(q, {})
            for key, row in [
                ("revenue", rev_row), ("operating_profit", op_row), ("net_income", ni_row),
                ("operating_cf", op_cf_row), ("investing_cf", inv_cf_row),
                ("financing_cf", fin_cf_row), ("capex", capex_row),
            ]:
                if row is None or key in qdata:
                    continue
                v = _parse_val(row[col])
                if v is not None:
                    qdata[key] = v * 1e8

    # ── 자동 학습: 성공한 계정명을 profiles에 기록 ───────────────────────────
    # 미래 수집 시 키워드 매칭을 바로 성공시켜 재검증 불필요하게 만듦
    if _learned_names and annual:
        _auto_update_profile(stock_code, _learned_names)

    return {"annual": annual, "quarterly": quarterly, "source_url": url,
            "learned_names": _learned_names}


# ─────────────────────────────────────────────────────────
# 스냅샷 저장 (external → financial_source_snapshot)
# ─────────────────────────────────────────────────────────
def save_snapshot(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    quarter: int,
    is_annual: int,
    report_type: str,
    source_url: str,
    data: dict,
    fetched_at: str,
) -> int:
    """스냅샷 저장 후 row ID 반환 (교차검증에 사용)."""
    raw_json = json.dumps(data, ensure_ascii=False)
    cur = conn.execute("""
        INSERT INTO financial_source_snapshot
            (stock_code, year, quarter, is_annual, report_type, data_source, source_url,
             fetched_at, revenue, operating_profit, net_income, eps, bps,
             total_assets, total_equity,
             operating_cf, investing_cf, financing_cf, capex,
             verification_status, raw_data_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unverified',?)
        ON CONFLICT(stock_code, year, quarter, is_annual, report_type, data_source, fetched_at)
        DO UPDATE SET
            revenue=excluded.revenue, operating_profit=excluded.operating_profit,
            net_income=excluded.net_income, total_assets=excluded.total_assets,
            total_equity=excluded.total_equity,
            operating_cf=excluded.operating_cf, investing_cf=excluded.investing_cf,
            financing_cf=excluded.financing_cf, capex=excluded.capex,
            raw_data_json=excluded.raw_data_json
    """, (
        stock_code, year, quarter, is_annual, report_type, "fnguide", source_url,
        fetched_at,
        data.get("revenue"), data.get("operating_profit"), data.get("net_income"),
        data.get("eps"), data.get("bps"),
        data.get("total_assets"), data.get("total_equity"),
        data.get("operating_cf"), data.get("investing_cf"),
        data.get("financing_cf"), data.get("capex"),
        raw_json,
    ))
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM financial_source_snapshot WHERE stock_code=? AND year=? AND quarter=? "
        "AND is_annual=? AND report_type=? AND data_source='fnguide' ORDER BY id DESC LIMIT 1",
        (stock_code, year, quarter, is_annual, report_type)
    ).fetchone()
    return row[0] if row else 0


# ─────────────────────────────────────────────────────────
# financial_data UPSERT
# ─────────────────────────────────────────────────────────
def upsert_financial(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    quarter: int,
    is_annual: int,
    report_type: str,
    data: dict,
    override: bool,
) -> str:
    """
    'fill_only': NULL/0만 채움
    'overridden': 기존값 변경
    'inserted': 신규 삽입
    'skipped': 변경없음
    """
    existing = conn.execute("""
        SELECT id, revenue, operating_profit, net_income, eps, bps,
               total_assets, total_equity
        FROM financial_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?
    """, (stock_code, year, quarter, is_annual, report_type)).fetchone()

    fields = ["revenue", "operating_profit", "net_income", "eps", "bps",
              "total_assets", "total_equity"]

    if existing is None:
        # INSERT
        conn.execute("""
            INSERT INTO financial_data
                (stock_code, year, quarter, is_annual, report_type,
                 revenue, operating_profit, net_income, eps, bps,
                 total_assets, total_equity, data_source, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'fnguide',datetime('now'))
        """, (
            stock_code, year, quarter, is_annual, report_type,
            data.get("revenue"), data.get("operating_profit"), data.get("net_income"),
            data.get("eps"), data.get("bps"),
            data.get("total_assets"), data.get("total_equity"),
        ))
        return "inserted"

    updates: dict[str, float] = {}
    for f in fields:
        ext_val = data.get(f)
        if ext_val is None:
            continue
        db_val = existing[f]
        if db_val is None or db_val == 0:
            updates[f] = ext_val
        elif override and not _close_match(db_val, ext_val):
            updates[f] = ext_val

    if updates:
        updates["data_source"] = "fnguide"
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [existing["id"]]
        conn.execute(f"UPDATE financial_data SET {sets} WHERE id=?", vals)
        return "overridden" if override else "fill_only"

    # 값 변경 없어도 data_source는 fnguide로 마킹
    conn.execute("UPDATE financial_data SET data_source='fnguide' WHERE id=?", (existing["id"],))
    return "skipped"


# ─────────────────────────────────────────────────────────
# cash_flow_data UPSERT
# ─────────────────────────────────────────────────────────
def upsert_cashflow(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    quarter: int,
    is_annual: int,
    report_type: str,
    data: dict,
    override: bool,
) -> str:
    existing = conn.execute("""
        SELECT id, operating_cf, investing_cf, financing_cf, capex
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?
    """, (stock_code, year, quarter, is_annual, report_type)).fetchone()

    cf_fields = ["operating_cf", "investing_cf", "financing_cf", "capex"]

    if existing is None:
        if any(data.get(f) is not None for f in cf_fields):
            conn.execute("""
                INSERT INTO cash_flow_data
                    (stock_code, year, quarter, is_annual, report_type,
                     operating_cf, investing_cf, financing_cf, capex,
                     data_source, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'fnguide',datetime('now'))
            """, (
                stock_code, year, quarter, is_annual, report_type,
                data.get("operating_cf"), data.get("investing_cf"),
                data.get("financing_cf"), data.get("capex"),
            ))
            return "inserted"
        return "skipped"

    updates: dict[str, float] = {}
    for f in cf_fields:
        ext_val = data.get(f)
        if ext_val is None:
            continue
        db_val = existing[f]
        if db_val is None or db_val == 0:
            updates[f] = ext_val
        elif override and not _close_match(db_val, ext_val):
            updates[f] = ext_val

    if updates:
        updates["data_source"] = "fnguide"
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [existing["id"]]
        conn.execute(f"UPDATE cash_flow_data SET {sets} WHERE id=?", vals)
        return "overridden" if override else "fill_only"

    # 값 변경 없어도 data_source는 fnguide로 마킹
    conn.execute("UPDATE cash_flow_data SET data_source='fnguide' WHERE id=?", (existing["id"],))
    return "skipped"


# ─────────────────────────────────────────────────────────
# 메인 수집 루프
# ─────────────────────────────────────────────────────────
def run(
    limit: int,
    report_types: list[str],
    stale_days: int,
    override: bool,
    year_from: int,
    year_to: int,
    cross_validate: bool = True,
) -> dict:
    conn = _conn()

    # 코스피·코스닥 보통주 (ETF/ETN 제외)
    # 우선순위: ① FnGuide 스냅샷이 없는 종목 → ② stale_days 초과된 종목 → ③ 나머지
    stale_cutoff = (
        (datetime.now(timezone.utc).replace(tzinfo=None) -
         __import__("datetime").timedelta(days=stale_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if stale_days > 0 else None
    )
    codes = [
        r[0] for r in conn.execute("""
            SELECT su.stock_code,
                   MAX(fss.fetched_at) AS last_fetched,
                   CASE WHEN MAX(fss.fetched_at) IS NULL THEN 0 ELSE 1 END AS has_snapshot
            FROM stock_universe su
            LEFT JOIN financial_source_snapshot fss
              ON fss.stock_code = su.stock_code AND fss.data_source = 'fnguide'
            WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND COALESCE(su.stock_type,'보통주') = '보통주'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
              AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            GROUP BY su.stock_code
            HAVING has_snapshot = 0
               OR (? IS NULL)
               OR (last_fetched < ?)
            ORDER BY has_snapshot ASC, last_fetched ASC NULLS FIRST, su.stock_code
            LIMIT ?
        """, (stale_cutoff, stale_cutoff, limit)).fetchall()
    ]

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stats = {
        "stocks": len(codes),
        "report_types": report_types,
        "annual_inserted": 0, "annual_updated": 0, "annual_skipped": 0,
        "ofs_inserted": 0,
        "snapshot_saved": 0,
        "cross_verified": 0, "cross_mismatch": 0, "cross_unverified": 0,
        "q4_computed": 0,
        "errors": 0,
    }

    for i, code in enumerate(codes, 1):
        for rt in report_types:
            try:
                result = fetch_fnguide_all(code, rt)
                if not result or not result.get("annual"):
                    continue

                source_url = result["source_url"]
                annual_data = result.get("annual", {})
                qtr_data    = result.get("quarterly", {})

                for yr, ydata in annual_data.items():
                    if not (year_from <= yr <= year_to):
                        continue

                    # 스냅샷 저장 (row ID 반환)
                    snap_id = save_snapshot(conn, code, yr, 0, 1, rt, source_url, ydata, fetched_at)
                    stats["snapshot_saved"] += 1

                    # financial_data UPSERT
                    fs_res = upsert_financial(conn, code, yr, 0, 1, rt, ydata, override)
                    cf_res = upsert_cashflow(conn, code, yr, 0, 1, rt, ydata, override)

                    if fs_res == "inserted":
                        stats["annual_inserted"] += 1
                    elif fs_res in ("fill_only", "overridden"):
                        stats["annual_updated"] += 1
                    else:
                        stats["annual_skipped"] += 1

                    if rt == "OFS" and fs_res == "inserted":
                        stats["ofs_inserted"] += 1

                    # DART 교차검증: 연간 CFS만 (쿼터 절약, OFS는 CFS 검증 결과 참조)
                    if cross_validate and rt == "CFS" and snap_id:
                        cv = cross_validate_annual(conn, code, rt, yr, ydata, snap_id)
                        stats[f"cross_{cv}"] = stats.get(f"cross_{cv}", 0) + 1

                # 분기 데이터: Q4는 페이지 값 대신 Annual - Q1 - Q2 - Q3 계산
                for yr, qmap in qtr_data.items():
                    if not (year_from <= yr <= year_to):
                        continue
                    for q, qdata in qmap.items():
                        if not qdata or q == 4:
                            continue  # Q4는 직접 계산으로 처리
                        save_snapshot(conn, code, yr, q, 0, rt, source_url, qdata, fetched_at)
                        upsert_financial(conn, code, yr, q, 0, rt, qdata, override)
                        upsert_cashflow(conn, code, yr, q, 0, rt, qdata, override)

                    # Q4 재계산: Annual이 있고 Q1~Q3가 모두 있을 때
                    for yr, ydata in annual_data.items():
                        if not (year_from <= yr <= year_to):
                            continue
                        if yr in qtr_data and all(q in qtr_data[yr] for q in [1, 2, 3]):
                            q4_res = compute_and_upsert_q4(conn, code, yr, rt, ydata, qtr_data[yr])
                            if q4_res in ("inserted", "updated"):
                                stats["q4_computed"] += 1

            except Exception as e:
                logger.warning(f"{code} {rt}: {e}")
                stats["errors"] += 1

        if i % 50 == 0:
            conn.commit()
            logger.info(
                f"[{i}/{len(codes)}] snapshot={stats['snapshot_saved']} "
                f"inserted={stats['annual_inserted']} updated={stats['annual_updated']} "
                f"verified={stats['cross_verified']} mismatch={stats['cross_mismatch']} "
                f"q4={stats['q4_computed']}"
            )

    conn.commit()
    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",        type=int,   default=9999)
    ap.add_argument("--report-types", nargs="+",  default=["CFS", "OFS"],
                    help="수집할 보고서 유형 (CFS=연결, OFS=별도)")
    ap.add_argument("--stale-days",   type=int,   default=0,
                    help="N일 이상 된 스냅샷 있는 종목만 재수집 (0=전체)")
    ap.add_argument("--override",            action="store_true",
                    help="불일치 기존값도 FnGuide 기준으로 덮어쓰기")
    ap.add_argument("--year-from",           type=int,   default=2018)
    ap.add_argument("--year-to",             type=int,   default=2025)
    ap.add_argument("--no-cross-validate",   action="store_true",
                    help="DART 교차검증 비활성화 (빠른 수집, 정확도 낮아짐)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    res = run(
        limit=args.limit,
        report_types=args.report_types,
        stale_days=args.stale_days,
        override=args.override,
        year_from=args.year_from,
        year_to=args.year_to,
        cross_validate=not args.no_cross_validate,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
