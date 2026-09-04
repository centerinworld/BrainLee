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

# 2026-08-09: FnGuide가 comp.fnguide.com(SVO2/ASP 구 ASP 페이지) → wcomp.fnguide.com
# (신규 JSON API 기반 사이트)으로 전면 개편(마지막 정상 수집 2026-07-28 직후로 추정) —
# 구 URL은 1년짜리 캐시(max-age=31536000)가 걸린 정적 에러페이지만 반환해 전종목 수집이
# 조용히 실패하고 있었음. 신규 사이트 기준으로 재작성.
# 연결: consol_typ=C, 별도: consol_typ=P (구 ReportGB=B와 다름 — 실측 확인)
REPORT_TYPE_PARAMS = {
    "CFS": "C",  # 연결재무제표
    "OFS": "P",  # 별도재무제표
}
WCOMP_BASE = "https://wcomp.fnguide.com"


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
# 2026-08-09(3차) 근본해결: 자본변동표(SCE) account_detail 구성요소 breakdown으로 지배주주분을
# 직접 추출하는 폴백을 추가해 net_income 정책차이 자체를 해소했으므로(456040/453450/452450
# 실측 전부 FnGuide와 정확 일치 확인), 완화 허용오차는 더 이상 필요 없어 원복(5% 유지) —
# 남는 net_income mismatch는 이제 진짜 조사가 필요한 케이스로 취급한다.
_CROSS_TOL_NET_INCOME = _CROSS_TOL
_CROSS_FIELDS   = ["revenue", "operating_profit", "net_income",
                   "total_assets", "total_equity", "cash"]
_DART_ACCOUNT_MAP: dict[str, list[str]] = {
    "revenue":          ["매출액", "영업수익", "수익(매출액)", "매출"],
    "operating_profit": ["영업이익"],
    # 2026-08-09 수정: DART 실제 계정명("지배기업의 소유주에게 귀속되는 당기순이익")을
    # 최우선 키워드로 추가. 기존 키워드("지배기업소유주지분순이익" 등)는 이 실제 phrasing과
    # 달라 매칭에 실패, 매번 일반 "당기순이익" 키워드로 낙하했음 — 그 경우 동일 키워드에
    # 매칭되는 3개 행(총계/지배/비지배)을 "절댓값 최대"로 골라야 했는데, 비지배지분이
    # 양수인 회사(삼성전자 등)는 총계>지배분이라 총계(틀림)가, 음수인 회사(에이엘티 등)는
    # 지배분>총계라 지배분(맞음)이 선택되는 회사마다 뒤바뀌는 구조적 결함이었음
    # (실측: 삼성전자 2023 총계15.49조 선택→FnGuide14.47조와 6.5%차, 에이엘티는 우연히 일치).
    "net_income":       ["지배기업의소유주에게귀속되는당기순이익", "지배기업소유주지분순이익",
                          "지배주주순이익", "당기순이익"],
    "total_assets":     ["자산총계"],
    "total_liabilities":["부채총계"],
    "total_equity":     ["자본총계"],
    "cash":             ["현금및현금성자산"],
}
_DART_EXCLUDE_MAP: dict[str, list[str]] = {
    # "기타" 추가 이유(2026-08-09, LG화학 051910 실측): "영업수익" 키워드가 "기타영업수익"
    # (부수적 소액 수익 항목, 676.9억)에 오매칭되어 실제 매출("매출" 계정, 55.2조)을
    # 찾기 전에 루프가 멈춰버림 — LG화학처럼 최상위 매출 계정이 단순 "매출"(금융·보험사의
    # "영업수익"과 달리)인 회사에서 "영업수익" 키워드가 부수항목을 먼저 잡는 문제.
    "revenue":          ["원가", "총이익", "차감", "기타"],
    # 2026-08-09 추가: 삼성전자(005930) 2023년 실측으로 발견 — "계속영업이익(손실)"
    # (ifrs-full_ProfitLossFromContinuingOperations, 15.49조)이 "영업이익" 키워드와
    # 오매칭되어 실제 영업이익(dart_OperatingIncomeLoss, 6.57조)을 덮어씀. 둘 다
    # sj_nm=손익계산서라 BS/IS 필터로는 구분 안 되고, "절댓값 최대" 타이브레이커가
    # 항상 더 큰 계속영업이익 쪽을 선택하는 구조적 문제였음.
    # "중단"/"신용손실충당금"/"반영전" 추가(2026-08-09(2차)): 8개사 원문 폭넓게 재조사 중
    # "중단영업이익"(계속의 대칭 케이스, 미발견이었으나 동일 위험)과 "신용손실충당금
    # 반영전 영업이익"(금융업 조정치)도 "영업이익" 서브스트링으로 확인, 선제 방어.
    "operating_profit": ["계속", "중단", "신용손실충당금", "반영전"],
    # "계속"(계속영업연결당기순이익 등, 중단영업 손익 제외한 부분치) 추가 —
    # 2026-08-09 현대차(005380) 실측: "지배" 표기가 아예 없는 회사는 net_income 선행패스가
    # 스킵되고 일반 "당기순이익" 키워드로 낙하하는데, 이때 "계속영업연결당기순이익"
    # (중단영업 제외, 129,920억)이 진짜 총계 "연결당기순이익"(122,723억)보다 커서
    # 절댓값최대 타이브레이커가 잘못된 쪽을 선택했음.
    # "귀속"/"중단" 추가(2026-08-09(2차)): 8개사 폭넓은 재조사 중 발견 — 현대건설(000720)
    # 포괄손익계산서에 "당기순이익의 귀속"(구성요소 breakdown 섹션 헤더 성격의 행,
    # -7,662.21억이라는 이례적으로 큰 값)이 존재. "지배" 없이 "귀속"만 있어 지배주주분
    # 선행패스는 정확히 건너뛰지만, 두 선행패스(IS 직접표기/SCE breakdown) 모두 실패하는
    # 회사에서 최후수단 일반 "당기순이익" 키워드로 낙하할 때 이 헤더행이 오매칭될 위험이
    # 있어 선제 방어(이 폴백은 실제로는 거의 발동 안 하지만 방어 비용이 낮음). "중단"은
    # 기존 "계속"(중단영업 제외 부분치)의 대칭 케이스(중단영업 부문만의 순이익)로 동일 위험.
    "net_income":   ["비지배", "감소", "취소", "계속", "귀속", "중단"],
    # 2026-08-09 추가: 삼성전자 2024/2025년 실측으로 발견 — "부채와자본총계"
    # (ifrs-full_EquityAndLiabilities, 자산총계와 동일값)가 "자본총계" 키워드와
    # 오매칭되어 실제 자본총계(ifrs-full_Equity)를 덮어씀. 둘 다 sj_nm=재무상태표라
    # BS필터로는 구분 안 되고, "절댓값 최대" 타이브레이커가 항상 더 큰(자산총계와
    # 같은 크기인) 부채와자본총계 쪽을 선택 — total_equity가 사실상 매번 total_assets와
    # 뒤바뀌어 저장되고 있었음.
    "total_equity": ["비지배", "기타포괄", "자본조정", "부채"],
    # "자본과부채총계"/"부채와자본총계"는 자산총계와 같은 검산 행이다.
    # account-id 매핑이 없는 OFS에서 단순 "부채총계" 부분문자열로 잡히면
    # liabilities=assets가 되어 BS 항등식이 깨지므로 키워드 폴백에서 제외한다.
    "total_liabilities": ["자본"],
}
# 재무상태표 행만 허용해야 하는 필드 — 2026-08-09: "cash"(현금및현금성자산)가 현금흐름표의
# "현금및현금성자산의순증가(감소)"(ifrs-full_IncreaseDecreaseInCashAndCashEquivalents,
# 기간증감액이라 음수가 정상)와 계정명이 겹쳐 오매칭되던 버그를 collectors/dart_collector.py
# _parse_fin_df에서 수정한 것과 동일한 문제가 이 독립 파서에도 있어 함께 수정.
_DART_BS_ONLY_FIELDS = frozenset({"total_assets", "total_liabilities", "total_equity", "cash"})
# 2026-08-09 추가: net_income도 같은 클래스의 버그로 확인 — 현금흐름표의
# "당기순이익조정을 위한 가감"(ifrs-full_AdjustmentsForReconcileProfitLoss, 순이익과
# 무관한 조정항목)이 "당기순이익" 키워드와 오매칭되어 실제 손익계산서 순이익
# (지배기업의 소유주에게 귀속되는 당기순이익)을 덮어쓰던 것을 실제 에이엘티(172670)
# 2025년 사례로 발견(FnGuide -89.7억 vs 이 버그로 인한 DART 248.4억, 흑자/적자 반전).
_DART_IS_ONLY_FIELDS = frozenset({"revenue", "operating_profit", "net_income"})

_dart_client_cache = None


def _get_dart_client():
    """DART 클라이언트를 프로세스당 1회만 생성해 재사용한다.
    2026-08-29 발견: 기존에는 _fetch_dart_annual() 호출마다 매번 새로 OpenDartReader(key)를
    생성했음 — 이 라이브러리 생성자가 회사 고유번호 목록(corp_codes)을 날짜별 pickle
    캐시(opendartreader_corp_codes_YYYYMMDD.pkl)로 관리하는데, 자정이 지나 새 날짜의 캐시
    파일이 아직 없는 시점에 대량 호출(예: financial_source_snapshot unverified 백필,
    수만 건)을 돌리면 호출마다 DART 원격 corp_codes 재다운로드를 시도하게 되어 DART
    서버로부터 "status=800 시스템 점검으로 인한 서비스가 중지 중입니다"(사실상 과호출
    차단으로 추정)를 100% 실패율로 받는 것을 실측 확인. 클라이언트를 프로세스 생애주기
    동안 한 번만 만들어 재사용한다.
    2026-08-30 수정: 이 함수가 `config.DART_API_KEY`(KEY1) 단일 키만 써서, KEY2/KEY3가
    설정돼 있어도 전혀 활용되지 못하고 있었음(사용자 지적: "id가 3개나 있는데 모두
    사용한거야?" — 실측 결과 아니오, 이 경로는 KEY1만 씀). `dart_key_manager.
    RotatingOpenDartReader`(이미 dedup/verify_all 스크립트들이 쓰는 3키 로테이션 프록시)로
    교체 — 이제 이 경로도 KEY1 소진 시 KEY2/KEY3로 자동 전환되어 실질 가용량이 3배가 됨."""
    global _dart_client_cache
    if _dart_client_cache is not None:
        return _dart_client_cache
    try:
        from dart_key_manager import RotatingOpenDartReader
        _dart_client_cache = RotatingOpenDartReader()
    except Exception as e:
        logger.warning(f"[교차검증] DART 클라이언트 초기화 실패: {e}")
        return None
    return _dart_client_cache


def _fetch_dart_annual(stock_code: str, report_type: str, year: int) -> Optional[dict]:
    """DART에서 연간 재무 핵심 필드 수집. 교차검증 전용 (쿼터 소비 주의).
    반환: {revenue, operating_profit, net_income, total_assets, total_equity} (원 단위)
    None: DART 실패 또는 쿼터 소진.
    """
    if not api_limiter.wait("DART"):
        return None

    dart = _get_dart_client()
    if dart is None:
        return None

    fs_div = _DART_FS_DIV.get(report_type, "CFS")
    try:
        df = dart.finstate_all(stock_code, year, _DART_RPRT_CODE, fs_div=fs_div)
    except Exception:
        df = None

    # 2026-08-09(2차) 추가: 전종목 검증 스윕 실측 결과 226종목 중 91건(40%)이 CFS 조회
    # status=013("조회된 데이타가 없습니다")로 단순 실패 처리되고 있었음 — 종속회사가
    # 없어 연결재무제표를 아예 작성하지 않는(별도재무제표만 존재) 회사들. FnGuide 쪽은
    # 이런 회사에서도 consol_typ='C' 요청에 사실상 별도 수치를 그대로 반환하는 것으로
    # 보여(연결=별도이므로), DART 쪽만 OFS로 폴백하면 정상 비교 가능. 이미 이 프로젝트의
    # stock_collection_config.preferred_report_type이 확립한 CFS/OFS 오버라이드 관행과
    # 같은 원리 — 여기서는 "요청한 fs_div가 없으면 자동으로 다른 쪽 시도"로 일반화.
    if (df is None or df.empty) and fs_div == "CFS":
        try:
            df = dart.finstate_all(stock_code, year, _DART_RPRT_CODE, fs_div="OFS")
        except Exception:
            df = None

    if df is None or df.empty:
        return None

    import pandas as _pd
    labels = df.get("account_nm", df.get("account_detail", _pd.Series(dtype=str))).astype(str)
    labels_ns = labels.str.replace(" ", "", regex=False)
    sj_ns = df.get("sj_nm", _pd.Series([""] * len(df))).astype(str).str.replace(" ", "", regex=False)

    result: dict = {}

    # ── net_income 전용 선행 패스 (2026-08-09) ──────────────────────────────
    # 지배주주 귀속 순이익의 DART 계정명은 회사마다 phrasing이 제각각임을 실측 확인:
    # "지배기업의 소유주에게 귀속되는 당기순이익"(삼성전자/에이엘티), "지배주주에 귀속되는
    # 순이익"(LG화학, "당기" 없음) 등. 정확한 문구를 계속 나열하는 방식은 새 회사마다
    # 재발하므로, "지배"+"순이익"을 모두 포함하고 "비지배"는 제외하는 패턴 매칭으로 전환
    # (손익계산서 행 한정). 이런 분리표기가 아예 없는 회사(비지배지분 없음)는 일반
    # "당기순이익" 폴백으로 자연스럽게 넘어감.
    ni_mask = (
        labels_ns.str.contains("지배", na=False, regex=False)
        & labels_ns.str.contains("순이익", na=False, regex=False)
        & ~labels_ns.str.contains("비지배", na=False, regex=False)
        & sj_ns.str.contains("손익계산서", na=False)
    )
    ni_rows = df.loc[ni_mask]
    if not ni_rows.empty:
        col = next((c for c in ["thstrm_amount", "thstrm_add_amount"] if c in ni_rows.columns), None)
        if col is not None:
            try:
                result["net_income"] = float(str(ni_rows.iloc[0][col]).replace(",", "").strip())
            except Exception:
                pass

    # ── net_income 2차 폴백: 자본변동표(SCE) 구성요소 breakdown (2026-08-09(3차), 근본해결) ──
    # 위 선행패스는 손익계산서에 "지배기업의 소유주에게 귀속되는 당기순이익" 같은 명시적
    # 분리행이 있는 회사(삼성전자 등)에서만 성공한다. 그런 분리행이 없는 회사는 회사가
    # 비지배지분이 없어서가 아니라, DART finstate_all()이 자본변동표를 계정명(account_nm)만
    # 평탄화해 반환하면서 지배주주/비지배지분 구분 자체가 account_detail 컬럼(구성요소
    # 경로 문자열, 예: "자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]")으로만
    # 남기 때문 — 456040 실측으로 발견: 손익계산서엔 총계(89.67억)만 있고, 자본변동표의
    # "당기순이익" 행 중 하나가 이 구성요소 경로로 지배주주분(96.03억=FnGuide와 정확히 일치)을
    # 담고 있었음. account_id='ifrs-full_ProfitLoss' + sj_nm='자본변동표' 행에서
    # account_detail로 지배주주 breakdown을 직접 조회 — 회사마다 문구가 "지배기업의
    # 소유주에게 귀속되는 지분"/"지배기업 소유주지분" 등으로 다르므로 "지배"만 포함 기준으로
    # 매칭하되, "|이익잉여금"/"|자본금"/"|기타자본"/"|주식발행초과금" 같은 하위 구성요소
    # drill-down 행(같은 값이 중복 등장)은 제외해 최상위 집계행 하나만 남긴다.
    if "net_income" not in result:
        detail = df.get("account_detail", _pd.Series(dtype=str)).astype(str)
        sce_mask = (
            sj_ns.str.contains("자본변동표", na=False)
            & (df.get("account_id", _pd.Series(dtype=str)).astype(str) == "ifrs-full_ProfitLoss")
            & detail.str.contains("지배", na=False, regex=False)
            & ~detail.str.contains("비지배", na=False, regex=False)
            & ~detail.str.contains("|이익잉여금", na=False, regex=False)
            & ~detail.str.contains("|자본금", na=False, regex=False)
            & ~detail.str.contains("|기타자본", na=False, regex=False)
            & ~detail.str.contains("|주식발행초과금", na=False, regex=False)
        )
        sce_rows = df.loc[sce_mask]
        if not sce_rows.empty:
            col = next((c for c in ["thstrm_amount", "thstrm_add_amount"] if c in sce_rows.columns), None)
            if col is not None:
                try:
                    result["net_income"] = float(str(sce_rows.iloc[0][col]).replace(",", "").strip())
                except Exception:
                    pass

    for key, kws in _DART_ACCOUNT_MAP.items():
        if key == "net_income" and "net_income" in result:
            continue  # 위 선행 패스에서 이미 확정됨
        excls = _DART_EXCLUDE_MAP.get(key, [])
        for kw in kws:
            mask = labels_ns.str.contains(kw.replace(" ", ""), na=False, regex=False)
            for ex in excls:
                mask = mask & ~labels_ns.str.contains(ex.replace(" ", ""), na=False, regex=False)
            if key in _DART_BS_ONLY_FIELDS:
                # 재무상태표 행만 허용 — 현금흐름표의 "현금및현금성자산의순증가(감소)" 등
                # 계정명이 겹치는 행을 배제(2026-08-09, cash 필드 오매칭 재발방지)
                mask = mask & sj_ns.str.contains("재무상태표", na=False)
            elif key in _DART_IS_ONLY_FIELDS:
                # 손익계산서(또는 포괄손익계산서) 행만 허용 — 현금흐름표의 "당기순이익조정을
                # 위한 가감" 등 계정명이 겹치는 행을 배제(2026-08-09, net_income 오매칭 재발방지)
                mask = mask & sj_ns.str.contains("손익계산서", na=False)
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


def _entity_category(conn: sqlite3.Connection, stock_code: str) -> Optional[str]:
    """2026-08-09(2차) 신규 — REIT/외국상장기업처럼 재무제표 양식 자체가 이질적인 카테고리를
    stock_collection_config('entity_category')에서 조회. 마스턴프리미어리츠(357430)·
    JTC(950170) 실측으로 발견: 이 카테고리는 DART/FnGuide 수치가 일관되게(단순 배수 아닌
    구조적 이유로) 어긋나므로, 일반 계정매칭 버그와 구분해 별도 상태로 기록한다."""
    row = conn.execute(
        "SELECT config_value FROM stock_collection_config WHERE stock_code=? AND config_key='entity_category'",
        (stock_code,),
    ).fetchone()
    return row[0] if row else None


def cross_validate_annual(
    conn: sqlite3.Connection,
    stock_code: str,
    report_type: str,
    year: int,
    fng_data: dict,
    snapshot_id: int,
) -> str:
    """FnGuide와 DART 연간 데이터 비교. financial_source_snapshot.verification_status 업데이트.
    반환: 'verified' | 'mismatch' | 'structural_diff' | 'unverified'
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
        tol = _CROSS_TOL_NET_INCOME if f == "net_income" else _CROSS_TOL
        if ratio > tol:
            mismatches.append(f"{f}: FnG={fv/1e8:.1f}억 DART={dv/1e8:.1f}억 ({ratio*100:.1f}%차)")

    category = _entity_category(conn, stock_code) if mismatches else None
    if mismatches and category:
        # REIT/외국상장기업 — 계정매칭 버그가 아니라 재무제표 양식 자체의 구조적 차이로
        # 추정되는 카테고리. mismatch 로그(fnguide_dart_mismatch_log)를 오염시키지 않도록
        # 별도 상태로 분리 — 진짜 파싱버그 조사 시 이 카테고리는 자동으로 제외됨.
        note = f"구조적차이({category}): " + "; ".join(mismatches)
        status = "structural_diff"
        logger.info(f"[교차검증] {stock_code} {report_type} {year} — {note}")
    elif mismatches:
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
def _fnguide_json(endpoint: str, cmp_cd: str, freq_typ: str, consol_typ: str) -> tuple[list[dict], list[dict]]:
    """wcomp.fnguide.com/CompanyInfo/{endpoint} 공통 호출. (header, data) 반환.
    2026-08-09 신규 사이트(JSON API) 대응 공용 헬퍼."""
    url = f"{WCOMP_BASE}/CompanyInfo/{endpoint}?cmp_cd={cmp_cd}&freq_typ={freq_typ}&consol_typ={consol_typ}"
    headers = {
        **FNGUIDE_HEADERS,
        "Referer": f"{WCOMP_BASE}/CompanyInfo/Finance?cmp_cd={cmp_cd}",
        "X-Requested-With": "XMLHttpRequest",
    }
    resp = api_limiter.get("FNGUIDE", url, headers=headers, timeout=20)
    if resp is None or resp.status_code != 200:
        return [], []
    try:
        j = resp.json()
    except Exception:
        return [], []
    ds = j.get("dataset") or {}
    return ds.get("header") or [], ds.get("data") or []


def _fnguide_find_row(data: list[dict], keywords: list[str], excludes: list[str] = ()) -> Optional[dict]:
    """keywords를 우선순위 순으로 시도 — 각 키워드에 처음 매칭되는 데이터행을 반환
    (키워드1이 어느 행에도 없을 때만 키워드2로 넘어감 — 구체적 라벨을 일반 라벨보다 우선)."""
    ex_ns = [e.replace(" ", "") for e in excludes]
    for kw in keywords:
        kw_ns = kw.replace(" ", "")
        for row in data:
            name_ns = str(row.get("NAME") or "").replace(" ", "")
            if kw_ns in name_ns and not any(e in name_ns for e in ex_ns):
                return row
    return None


def _fnguide_annual_cols(header: list[dict]) -> dict[int, str]:
    """freq_typ=Y 응답 header에서 확정 연간 컬럼만 채택.
    2026-08-09 수정: 기존엔 'YYYY/12'만 인정해 **12월 결산이 아닌 회사(예: 현대약품
    004310=11월 결산)가 통째로 annual={} 빈 결과가 되는 버그**였음(실측 확인 —
    revenue/net_income 등 계정 매칭 자체는 성공했으나 연도 컬럼 인식 실패로 전부 유실).
    실제로는 '접미사 없는 순수 YYYY/MM'이면 결산월과 무관하게 항상 확정 연간치이고
    (최신 미완결 분기만 "(최근분기)" 접미사가 붙음 — 12월 결산사도 이 접미사 컬럼은
    항상 존재), '(E)' 등 추정치 표기는 애초에 이 신규 API에 없음(구 SVD 페이지 전용
    표기였음) — 따라서 결산월 제약을 완전히 제거하고 접미사 유무만으로 판정."""
    out: dict[int, str] = {}
    for h in header:
        raw = str(h.get("YYMM", "")).strip()
        if re.fullmatch(r"20\d{2}/\d{2}", raw):
            out[int(raw[:4])] = h["CD"]
    return out


_Q_MONTH_MAP = {"03": 1, "06": 2, "09": 3, "12": 4}


def _fnguide_qtr_cols(header: list[dict]) -> dict[tuple[int, int], str]:
    """freq_typ=Q 응답 header에서 (year, quarter) → 컬럼코드.
    2026-08-09 수정: getFinIncome/getFinCashFlow의 최신분기 컬럼은
    "2026/03 (최근분기)"처럼 접미사가 붙어 fullmatch로는 누락됨(반면 getFinBalance는
    접미사 없이 순수 "2026/03") — 접두부만 매칭하도록 완화(re.match). "전년동기"/
    "전년동기대비(%)" 같은 비교용 컬럼은 YYMM이 연도/월 패턴이 아니라 애초에 매칭
    안 되므로 안전."""
    out: dict[tuple[int, int], str] = {}
    for h in header:
        raw = str(h.get("YYMM", "")).strip()
        m = re.match(r"(20\d{2})/(03|06|09|12)(?!\d)", raw)
        if m:
            out[(int(m.group(1)), _Q_MONTH_MAP[m.group(2)])] = h["CD"]
    return out


def _fnguide_val(row: Optional[dict], col: str) -> Optional[float]:
    if row is None:
        return None
    return _parse_val(row.get(col))


def fetch_fnguide_eps_bps(stock_code: str) -> dict[int, dict[str, float]]:
    """
    wcomp.fnguide.com/CompanyInfo/Snapshot 임베디드 snpFinancial JSON에서 연도별 EPS/BPS 추출.
    (2026-08-09: 구 SVD_Main.asp 폐지로 신규 사이트 대응 재작성)
    반환: {year: {"eps": float, "bps": float}}
    단위: 원/주
    """
    url = f"{WCOMP_BASE}/CompanyInfo/Snapshot?cmp_cd={stock_code}"
    resp = api_limiter.get("FNGUIDE", url, headers=FNGUIDE_HEADERS, timeout=20)
    if resp is None or resp.status_code != 200:
        return {}

    m = re.search(r"snpFinancial\s*:\s*(\{.*?\}\]\})\s*,\s*\n", resp.text, re.S)
    if not m:
        return {}
    try:
        snp = json.loads(m.group(1))
    except Exception:
        return {}

    header = snp.get("header") or []
    data = snp.get("data") or []
    ann_cols = {}
    for h in header:
        raw = str(h.get("YYMM", "")).strip()
        # 2026-08-09 수정: "/12"만 인정하면 12월 결산이 아닌 회사가 전부 누락됨(위
        # _fnguide_annual_cols와 동일 버그) — EP_CHK="E"(추정치)만으로 걸러내면 충분.
        if re.fullmatch(r"20\d{2}/\d{2}", raw) and h.get("EP_CHK") != "E":
            ann_cols[int(raw[:4])] = h["CD"]

    eps_row = _fnguide_find_row(data, ["EPS"])
    bps_row = _fnguide_find_row(data, ["BPS"])

    result: dict[int, dict[str, float]] = {}
    for y, col in ann_cols.items():
        yvals: dict[str, float] = {}
        v = _fnguide_val(eps_row, col)
        if v is not None:
            yvals["eps"] = v
        v = _fnguide_val(bps_row, col)
        if v is not None:
            yvals["bps"] = v
        if yvals:
            result[y] = yvals
    return result


def fetch_fnguide_all(stock_code: str, report_type: str, annual_only: bool = False) -> dict:
    """
    wcomp.fnguide.com JSON API(getFinIncome/getFinBalance/getFinCashFlow, 연간+분기
    각 1회씩 총 6회 호출)로 P&L + BS + CF 전체 추출.
    (2026-08-09: 구 comp.fnguide.com/SVO2/ASP/SVD_Finance.asp가 1년 캐시된 정적
    에러페이지만 반환하는 사이트 전면개편에 대응해 재작성 — 마지막 정상 수집
    2026-07-28 직후 FnGuide가 wcomp.fnguide.com JSON API 사이트로 이전한 것으로 추정)
    report_type: 'CFS'(연결) or 'OFS'(별도)
    반환 형태는 구버전과 동일하게 유지(save_snapshot/upsert_financial 등 하위 호환):
    {
      'annual':   {year: {revenue, operating_profit, net_income, total_assets, total_equity,
                          cash, operating_cf, investing_cf, financing_cf, capex, cash_end,
                          depreciation}},
      'quarterly':{year: {quarter: {...}}},
      'source_url': str,
    }
    단위: 억원 → × 1e8 → 원
    """
    consol_typ = REPORT_TYPE_PARAMS[report_type]
    source_url = f"{WCOMP_BASE}/CompanyInfo/Finance?cmp_cd={stock_code}"
    prof = get_profile(stock_code)

    _REV_KW = [*prof.get("revenue_keywords", []),
               "매출액(수익)", "매출액", "영업수익", "매출",
               "도급공사수익", "건설수익", "운임수익", "운송수익",
               "이자수익", "순이자이익", "수수료수익"]
    _OP_KW  = [*prof.get("op_profit_keywords", []), "영업이익(발표기준)", "영업이익"]
    # 지배주주지분 순이익을 최우선(연결 전사 합계 "당기순이익"보다 먼저 매칭되도록
    # 키워드 우선순위로 강제 — 데이터 행 순서에 의존하지 않음, 2026-08-09 설계).
    _NI_KW  = [*prof.get("net_income_keywords", []),
               "지배주주지분)당기순이익", "지배기업소유주지분", "지배주주순이익", "지배주주지분순이익",
               "당기순이익(지배)", "당기순이익", "반기순이익", "분기순이익"]
    _NI_EXCL = [*prof.get("net_income_exclude_keywords", []), "비지배"]
    _ASSET_KW = ["자산총계"]
    _EQ_KW    = ["자본총계(지배)", "자본총계"]
    _EQ_EXCL  = ["비지배"]
    _CASH_KW  = ["현금및현금성자산"]
    _OP_CF_KW  = [*prof.get("op_cf_keywords", []), "영업활동으로인한현금흐름", "영업활동현금흐름"]
    _INV_CF_KW = [*prof.get("inv_cf_keywords", []), "투자활동으로인한현금흐름", "투자활동현금흐름"]
    _FIN_CF_KW = [*prof.get("fin_cf_keywords", []), "재무활동으로인한현금흐름", "재무활동현금흐름"]
    _CAPEX_KW  = ["유형자산의취득", "유형자산취득", "설비투자", "CAPEX", "자본적지출"]
    _CASH_END_KW = ["기말현금및현금성자산", "현금및현금성자산의기말잔액", "기말의현금및현금성자산"]
    _DEPR_KW = ["유형자산감가상각비", "감가상각비및무형자산상각비", "감가상각비"]

    annual: dict[int, dict] = {}
    quarterly: dict[int, dict[int, dict]] = {}

    try:
        inc_h_y, inc_d_y = _fnguide_json("getFinIncome", stock_code, "Y", consol_typ)
        bal_h_y, bal_d_y = _fnguide_json("getFinBalance", stock_code, "Y", consol_typ)
        cf_h_y,  cf_d_y  = _fnguide_json("getFinCashFlow", stock_code, "Y", consol_typ)
        if annual_only:
            # 2026-08-09: 전종목 DART교차검증 스윕 전용 경량모드 — cross_validate_annual은
            # 연간 데이터만 사용하므로 분기 3회 호출을 생략해 종목당 요청을 6회→3회로
            # 절반 이하로 줄임(FNGUIDE daily_limit=1500 제약 하에서 커버리지 극대화).
            inc_h_q = inc_d_q = bal_h_q = bal_d_q = cf_h_q = cf_d_q = []
        else:
            inc_h_q, inc_d_q = _fnguide_json("getFinIncome", stock_code, "Q", consol_typ)
            bal_h_q, bal_d_q = _fnguide_json("getFinBalance", stock_code, "Q", consol_typ)
            cf_h_q,  cf_d_q  = _fnguide_json("getFinCashFlow", stock_code, "Q", consol_typ)
    except Exception as e:
        logger.warning(f"[FnGuide] {stock_code} {report_type} JSON 조회 실패: {e}")
        return {}

    if not inc_d_y and not bal_d_y and not cf_d_y:
        return {}

    rev_row = _fnguide_find_row(inc_d_y, _REV_KW)
    op_row  = _fnguide_find_row(inc_d_y, _OP_KW)
    # 2026-08-09: DART측과 동일하게 "지배"+"순이익" AND매칭(비지배 제외)을 우선 시도 —
    # 순수 우선순위 키워드 목록만으로는 회사마다 다른 phrasing(예: LG화학 "지배주주에
    # 귀속되는 순이익"에는 "당기"가 없음)을 전부 나열하기 어려워, 놓치면 총계 "당기순이익"
    # 폴백이 비지배지분 부호에 따라 맞거나 틀리는 불안정한 결과를 냄(005930 실측 재현).
    ni_row = next(
        (r for r in inc_d_y
         if "지배" in str(r.get("NAME") or "").replace(" ", "")
         and "순이익" in str(r.get("NAME") or "").replace(" ", "")
         and "비지배" not in str(r.get("NAME") or "").replace(" ", "")),
        None,
    ) or _fnguide_find_row(inc_d_y, _NI_KW, _NI_EXCL)
    asset_row = _fnguide_find_row(bal_d_y, _ASSET_KW)
    eq_row    = _fnguide_find_row(bal_d_y, _EQ_KW, _EQ_EXCL)
    cash_row  = _fnguide_find_row(bal_d_y, _CASH_KW)
    op_cf_row  = _fnguide_find_row(cf_d_y, _OP_CF_KW)
    inv_cf_row = _fnguide_find_row(cf_d_y, _INV_CF_KW)
    fin_cf_row = _fnguide_find_row(cf_d_y, _FIN_CF_KW)
    capex_row  = _fnguide_find_row(cf_d_y, _CAPEX_KW)
    cash_end_row = _fnguide_find_row(cf_d_y, _CASH_END_KW)
    depr_row     = _fnguide_find_row(cf_d_y, _DEPR_KW)

    for yr, col in _fnguide_annual_cols(inc_h_y).items():
        ydata = annual.setdefault(yr, {})
        for key, v in [
            ("revenue", _fnguide_val(rev_row, col)),
            ("operating_profit", _fnguide_val(op_row, col)),
            ("net_income", _fnguide_val(ni_row, col)),
        ]:
            if v is not None:
                ydata[key] = v * 1e8
    for yr, col in _fnguide_annual_cols(bal_h_y).items():
        ydata = annual.setdefault(yr, {})
        for key, v in [
            ("total_assets", _fnguide_val(asset_row, col)),
            ("total_equity", _fnguide_val(eq_row, col)),
            ("cash", _fnguide_val(cash_row, col)),
        ]:
            if v is not None:
                ydata[key] = v * 1e8
    for yr, col in _fnguide_annual_cols(cf_h_y).items():
        ydata = annual.setdefault(yr, {})
        for key, v in [
            ("operating_cf", _fnguide_val(op_cf_row, col)),
            ("investing_cf", _fnguide_val(inv_cf_row, col)),
            ("financing_cf", _fnguide_val(fin_cf_row, col)),
            ("capex", _fnguide_val(capex_row, col)),
            ("cash_end", _fnguide_val(cash_end_row, col)),
            ("depreciation", _fnguide_val(depr_row, col)),
        ]:
            if v is not None:
                ydata[key] = v * 1e8

    # ── 분기 (동일 필드, freq_typ=Q 응답 재매칭 — 분기 테이블 행 구성이 연간과
    # 다를 수 있어 별도로 찾음) ────────────────────────────────────────────────
    rev_row_q = _fnguide_find_row(inc_d_q, _REV_KW)
    op_row_q  = _fnguide_find_row(inc_d_q, _OP_KW)
    ni_row_q  = _fnguide_find_row(inc_d_q, _NI_KW, _NI_EXCL)
    op_cf_row_q  = _fnguide_find_row(cf_d_q, _OP_CF_KW)
    inv_cf_row_q = _fnguide_find_row(cf_d_q, _INV_CF_KW)
    fin_cf_row_q = _fnguide_find_row(cf_d_q, _FIN_CF_KW)
    capex_row_q  = _fnguide_find_row(cf_d_q, _CAPEX_KW)
    cash_end_row_q = _fnguide_find_row(cf_d_q, _CASH_END_KW)
    depr_row_q     = _fnguide_find_row(cf_d_q, _DEPR_KW)

    for (yr, q), col in _fnguide_qtr_cols(inc_h_q).items():
        if q == 4:
            continue  # Q4는 Annual - Q1 - Q2 - Q3 직접계산으로 처리(run()에서)
        qdata = quarterly.setdefault(yr, {}).setdefault(q, {})
        for key, v in [
            ("revenue", _fnguide_val(rev_row_q, col)),
            ("operating_profit", _fnguide_val(op_row_q, col)),
            ("net_income", _fnguide_val(ni_row_q, col)),
        ]:
            if v is not None:
                qdata[key] = v * 1e8
    for (yr, q), col in _fnguide_qtr_cols(cf_h_q).items():
        if q == 4:
            continue
        qdata = quarterly.setdefault(yr, {}).setdefault(q, {})
        for key, v in [
            ("operating_cf", _fnguide_val(op_cf_row_q, col)),
            ("investing_cf", _fnguide_val(inv_cf_row_q, col)),
            ("financing_cf", _fnguide_val(fin_cf_row_q, col)),
            ("capex", _fnguide_val(capex_row_q, col)),
            ("cash_end", _fnguide_val(cash_end_row_q, col)),
            ("depreciation", _fnguide_val(depr_row_q, col)),
        ]:
            if v is not None:
                qdata[key] = v * 1e8

    _learned_names = {
        k: v["NAME"].strip() for k, v in [
            ("revenue", rev_row), ("operating_profit", op_row), ("net_income", ni_row),
        ] if v
    }
    if _learned_names and annual:
        _auto_update_profile(stock_code, _learned_names)

    return {"annual": annual, "quarterly": quarterly, "source_url": source_url,
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
             total_assets, total_equity, cash,
             operating_cf, investing_cf, financing_cf, capex,
             verification_status, raw_data_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unverified',?)
        ON CONFLICT(stock_code, year, quarter, is_annual, report_type, data_source, fetched_at)
        DO UPDATE SET
            revenue=excluded.revenue, operating_profit=excluded.operating_profit,
            net_income=excluded.net_income, total_assets=excluded.total_assets,
            total_equity=excluded.total_equity, cash=excluded.cash,
            operating_cf=excluded.operating_cf, investing_cf=excluded.investing_cf,
            financing_cf=excluded.financing_cf, capex=excluded.capex,
            raw_data_json=excluded.raw_data_json
    """, (
        stock_code, year, quarter, is_annual, report_type, "fnguide", source_url,
        fetched_at,
        data.get("revenue"), data.get("operating_profit"), data.get("net_income"),
        data.get("eps"), data.get("bps"),
        data.get("total_assets"), data.get("total_equity"), data.get("cash"),
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
    # FnGuide 표 행 오매칭 시 자산총계 대신 소액 구성항목이 들어오는 사례를 차단한다.
    # 원본 snapshot은 보존하되 운영 financial_data에는 명백히 모순된 BS 값을 쓰지 않는다.
    data = dict(data)
    assets = data.get("total_assets")
    equity = data.get("total_equity")
    if isinstance(assets, (int, float)) and isinstance(equity, (int, float)):
        if assets <= 0 or (equity > 0 and assets < equity * 0.5):
            data["total_assets"] = None
        if assets > 0 and abs(equity) > assets * 2:
            data["total_equity"] = None

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
        SELECT id, operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?
    """, (stock_code, year, quarter, is_annual, report_type)).fetchone()

    cf_fields = ["operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"]

    if existing is None:
        if any(data.get(f) is not None for f in cf_fields):
            conn.execute("""
                INSERT INTO cash_flow_data
                    (stock_code, year, quarter, is_annual, report_type,
                     operating_cf, investing_cf, financing_cf, capex,
                     cash_end, depreciation,
                     data_source, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'fnguide',datetime('now'))
            """, (
                stock_code, year, quarter, is_annual, report_type,
                data.get("operating_cf"), data.get("investing_cf"),
                data.get("financing_cf"), data.get("capex"),
                data.get("cash_end"), data.get("depreciation"),
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
    # 2026-08-14: 기존 `(? IS NULL)` 단독 조건은 PostgreSQL이 파라미터 타입을 추론하지
    # 못해 "could not determine data type of parameter $1" 오류를 냄(HAVING 별칭참조
    # 버그와 별개의 문제) — stale_cutoff가 None인 경우(stale_days<=0, 전량 대상)를
    # Python에서 미리 분기해 불필요한 파라미터 자체를 없앰.
    having_sql = "HAVING MAX(fss.fetched_at) IS NULL OR MAX(fss.fetched_at) < ?" if stale_cutoff else "HAVING 1=1"
    having_params = (stale_cutoff,) if stale_cutoff else ()
    codes = [
        r[0] for r in conn.execute(f"""
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
            {having_sql}
            ORDER BY has_snapshot DESC, last_fetched ASC NULLS LAST, su.stock_code
            LIMIT ?
        """, having_params + (limit,)).fetchall()
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
        "eps_bps_updated": 0,
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

        # EPS/BPS: SVD_Main.asp에서 종목당 1회 수집 (CFS 연간 레코드에만 적용)
        try:
            eps_bps = fetch_fnguide_eps_bps(code)
            for yr, eb in eps_bps.items():
                if not (year_from <= yr <= year_to):
                    continue
                if not eb:
                    continue
                res = upsert_financial(conn, code, yr, 0, 1, "CFS", eb, override)
                if res in ("fill_only", "overridden", "inserted"):
                    stats["eps_bps_updated"] += 1
        except Exception as e:
            logger.warning(f"{code} EPS/BPS: {e}")

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
