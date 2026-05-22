"""
collectors/dart_collector.py — DART Open API 수집기

담당 데이터:
  · 분기/연간 재무제표 (손익계산서·대차대조표·EPS/BPS)
  · 현금흐름표 (영업/투자/재무 CF, CAPEX)
  · 공시 목록 조회 (당일 재무 관련 공시 필터링)

OpenDartReader는 동기 라이브러리 → ThreadPoolExecutor(max_workers=1)에서 실행
Q4 = 연간 - Q1 - Q2 - Q3 파생 계산 포함
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Optional

import config
from collectors.base import BaseCollector
from financial_profiles import get_profile
from collectors.dart_mapping_engine import resolve_field

try:
    from api_rate_limiter import api_limiter as _rl
    _USE_RL = True
except ImportError:
    _USE_RL = False

logger = logging.getLogger(__name__)

# 분기 코드 매핑 (DART 표준)
_RPRT_CODE: dict[int, str] = {
    1: "11013",   # 1분기
    2: "11012",   # 반기
    3: "11014",   # 3분기
    4: "11011",   # 사업보고서(연간)
}

# 재무 공시 트리거 키워드
_FINANCIAL_KEYWORDS = [
    "사업보고서", "반기보고서", "분기보고서",
    "재무제표", "감사보고서", "회계처리",
    "내부회계관리", "합병", "분할합병", "영업양수도",
    "유상증자", "무상증자", "자본감소", "전환사채",
]

# 계정과목 → 필드명 매핑
_ACCOUNT_MAP = [
    (["매출액", "영업수익", "매출"],                              "revenue"),
    (["영업이익"],                                                "operating_profit"),
    (["당기순이익", "당기순손익", "분기순이익", "반기순이익"],     "net_income"),      # "주당" 제외
    (["자산총계"],                                                "total_assets"),
    (["부채총계"],                                                "total_liabilities"),
    (["자본총계"],                                                "total_equity"),
    (["자본금"],                                                  "capital_stock"),
    # EPS: DART 공시에 따라 계정명 상이 — "기본주당이익(손실)" 등 추가
    (["기본주당순이익", "주당순이익", "기본EPS", "기본주당이익"],  "eps"),
    # BPS: 재무상태표 주석에 주로 표기
    (["주당순자산", "1주당순자산가액", "주당장부가액"],            "bps"),
    (["주당배당금", "주당현금배당금"],                             "dps"),
    (["현금및현금성자산", "현금성자산"],                           "cash"),
    (["발행주식수", "유통주식수"],                                 "total_shares"),
    # 감가상각비: CFS 현금흐름표에 개별 항목으로 표시되는 경우만 수집됨
    # → 대부분 회사는 finstate_all에 미포함 (조정 합계만 표시)
    # → fix_financial_gaps.py (FnGuide CF 스크래핑)으로 보완
    (["감가상각비"],                                              "depreciation_amortization"),
]

_BLANK_FIN = {
    "revenue": None, "operating_profit": None, "net_income": None,
    "total_assets": None, "total_liabilities": None, "total_equity": None,
    "capital_stock": None, "eps": None, "bps": None, "dps": None,
    "cash": None, "total_shares": None, "depreciation_amortization": None,
}

_BLANK_CF = {
    "operating_cf": None, "investing_cf": None, "financing_cf": None,
    "capex": None, "cash_end": None, "depreciation": None,
}

# CF 계정과목 키워드 매핑 (account_id 매핑 실패 시 낙하)
# 출처: config/dart_cf_account_catalog.json (top150 종목 전수조사 2026-05-17)
#
# 주의: account_nm에서 공백 제거 후 매칭 (str.replace(' ',''))
# 순서: 더 구체적인 패턴을 먼저 나열
_CF_MAP = [
    # ── 영업활동 (OCF) ──────────────────────────────────────────────────────
    # 표준 패턴: "영업활동현금흐름", "영업활동으로인한현금흐름"
    # 순합계 변형: "영업활동순현금흐름합계", "영업활동으로인한순현금흐름"
    # 이마트 패턴: "영업활동으로부터의순현금유입" (비표준, account_id=-표준계정코드 미사용-)
    # 직접법 제외: "영업활동으로인한현금수취액/지급액" → 단방향이므로 제외
    ([
        "영업활동으로인한현금흐름",
        "영업활동현금흐름",
        "영업활동순현금흐름합계",
        "영업활동으로인한순현금흐름",
        "영업활동순현금흐름",
        "영업활동으로부터의순현금유입",    # 이마트 등 비표준
        "영업활동으로부터의현금흐름",      # 변형 패턴
    ], "operating_cf"),

    # ── 투자활동 (ICF) ──────────────────────────────────────────────────────
    # 주의: "유입액"/"유출액"은 단방향 소계 → _parse_cf_df 에서 account_id 매핑 시 이미 스킵
    ([
        "투자활동으로인한현금흐름",
        "투자활동현금흐름",
        "투자활동순현금흐름합계",
        "투자활동으로인한순현금흐름",
        "투자활동순현금흐름",
    ], "investing_cf"),

    # ── 재무활동 (FCF) ──────────────────────────────────────────────────────
    ([
        "재무활동으로인한현금흐름",
        "재무활동현금흐름",
        "재무활동순현금흐름합계",
        "재무활동으로인한순현금흐름",
        "재무활동순현금흐름",
    ], "financing_cf"),

    # ── CapEx (PP&E 우선, 무형자산은 fallback) ───────────────────────────────
    # 순서: PP&E 키워드 먼저 → 매칭 시 capex 확정, 무형자산은 PP&E 없을 때만 사용
    (["유형자산의취득", "유형자산취득", "유형자산및투자부동산의취득"], "capex"),
    (["무형자산의취득", "무형자산취득"], "capex"),

    # ── 감가상각 ────────────────────────────────────────────────────────────
    (["감가상각비", "감가상각비및무형자산상각비", "유무형자산상각비",
      "유형자산감가상각비"], "depreciation"),

    # ── 기말현금 ────────────────────────────────────────────────────────────
    # account_id "dart_CashAndCashEquivalentsAtEndOfPeriodCf" 가 PRIMARY
    # 아래는 account_id 미매핑 기업 fallback
    ([
        "기말현금및현금성자산",
        "기말의현금및현금성자산",
        "현금및현금성자산기말잔액",
        "현금및현금성자산의기말잔액",
        "기말의현금",
        "당기말현금및현금성자산",
    ], "cash_end"),
]


def _parse_fin_df(df, stock_code: Optional[str] = None) -> dict:
    """DART finstate DataFrame → 재무 dict."""
    import pandas as _pd
    m = dict(_BLANK_FIN)
    if df is None or df.empty:
        return m
    prof = get_profile(stock_code or "")
    extra_rev = prof.get("revenue_keywords", [])
    extra_ni = prof.get("net_income_keywords", [])
    extra_eps = prof.get("eps_keywords", [])
    ni_excludes = tuple(prof.get("net_income_exclude_keywords", []))

    account_map = list(_ACCOUNT_MAP)
    if extra_rev:
        account_map.append((extra_rev, "revenue"))
    if extra_ni:
        account_map.append((extra_ni, "net_income"))
    if extra_eps:
        account_map.append((extra_eps, "eps"))

    for _, row in df.iterrows():
        sj  = str(row.get("sj_nm", "")).replace(" ", "")
        acc = str(row.get("account_nm", "")).replace(" ", "")
        acc_id = str(row.get("account_id", "")).strip()
        val_col = "thstrm_amount"
        if not acc or val_col not in row or _pd.isna(row[val_col]):
            continue
        try:
            val = float(str(row[val_col]).replace(",", ""))
        except ValueError:
            continue

        mapped = resolve_field(stock_code or "", acc_id, acc)
        if mapped in m:
            if mapped in ("revenue", "operating_profit", "net_income", "eps") and "손익계산서" not in sj:
                mapped = None
            if mapped in ("total_assets", "total_liabilities", "total_equity", "capital_stock", "bps") and "재무상태표" not in sj:
                mapped = None
        if mapped:
            if val != 0 or m.get(mapped) in (None, 0, 0.0):
                m[mapped] = val
            continue

        for keywords, field in account_map:
            if field == "net_income" and "주당" in acc:
                continue
            if field == "net_income" and any(x in acc for x in ("귀속", "비지배", "지배기업")):
                continue
            if field == "net_income" and ni_excludes and any(x in acc for x in ni_excludes):
                continue
            # revenue: "매출원가", "매출총이익", "매출차감" 등 하위항목은 제외
            # (ifrs-full_Revenue로 account_id 매핑이 먼저 수행되므로, 키워드 스캔에서 하위항목이 덮어쓰는 버그 방지)
            if field == "revenue" and any(x in acc for x in ("원가", "총이익", "차감", "비용", "손실")):
                continue
            # 손익 항목은 포괄손익계산서 행만 허용 (자본변동표 0 덮어쓰기 방지)
            if field in ("revenue", "operating_profit", "net_income", "eps"):
                if "손익계산서" not in sj:
                    continue
            # 재무상태 항목은 재무상태표 행만 허용
            if field in ("total_assets", "total_liabilities", "total_equity", "capital_stock", "bps"):
                if "재무상태표" not in sj:
                    continue
            if any(kw in acc for kw in keywords):
                # 이미 비영값이 세팅된 경우 키워드 스캔으로 덮어쓰기 방지
                # (resolve_field로 올바른 값이 이미 세팅된 경우 보호)
                if m.get(field) not in (None, 0, 0.0):
                    break  # 이미 올바른 값 존재 → 키워드 매칭으로 덮어쓰지 않음
                if val != 0 or m.get(field) in (None, 0, 0.0):
                    m[field] = val
                break

    # 매출액 음수 sanity check: 음수 매출은 데이터 오류로 간주
    if m.get("revenue") is not None and m["revenue"] < 0:
        m["revenue"] = None

    return m


# capex를 위한 PP&E 우선 account_id 목록 (무형자산 account_id보다 신뢰도 높음)
_CAPEX_PPE_IDS = frozenset({
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "dart_PurchaseOfOtherPropertyPlantAndEquipment",
})
_CAPEX_INTANGIBLE_IDS = frozenset({
    "ifrs-full_PurchaseOfIntangibleAssets",
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
})


def _parse_cf_df(df, stock_code: Optional[str] = None) -> dict:
    """DART cash flow DataFrame → CF dict."""
    import pandas as _pd
    m = dict(_BLANK_CF)
    _capex_from_ppe = False  # PP&E capex 세팅 여부 (무형자산 override 방지)
    if df is None or df.empty:
        return m
    for _, row in df.iterrows():
        acc = str(row.get("account_nm", "")).replace(" ", "")
        acc_id = str(row.get("account_id", "")).strip()
        val_col = "thstrm_amount"
        if not acc or val_col not in row or _pd.isna(row[val_col]):
            continue
        try:
            val = float(str(row[val_col]).replace(",", ""))
        except ValueError:
            continue

        mapped = resolve_field(stock_code or "", acc_id, acc)
        if mapped in m:
            # 현금흐름표 계열만 허용
            if mapped in ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"):
                # OCF/ICF/FCF 순합계 필드에서 "현금유입액"/"현금유출액" 같은 단방향 소계는 skip
                if mapped in ("operating_cf", "investing_cf", "financing_cf") and (
                    "유입액" in acc or "유출액" in acc or "현금유입" in acc or "현금유출" in acc
                ):
                    pass  # account_id 매핑 스킵 → keyword scan으로 낙하
                elif mapped == "capex":
                    is_ppe = acc_id in _CAPEX_PPE_IDS
                    is_int = acc_id in _CAPEX_INTANGIBLE_IDS
                    if is_ppe:
                        # PP&E 항상 우선
                        m["capex"] = abs(val)
                        _capex_from_ppe = True
                        continue
                    elif is_int and _capex_from_ppe:
                        continue  # PP&E 이미 있으면 무형자산 skip
                    else:
                        m["capex"] = abs(val)
                        continue
                else:
                    m[mapped] = val
                    continue

        for keywords, field in _CF_MAP:
            if any(kw in acc for kw in keywords):
                if field == "capex":
                    if m["capex"] is not None and _capex_from_ppe:
                        break  # PP&E 값 보호
                    # PP&E 키워드: 유형자산의취득, 유형자산및투자부동산의취득
                    is_ppe_kw = any(kw in acc for kw in
                                    ["유형자산의취득", "유형자산취득", "유형자산및투자부동산의취득"])
                    if is_ppe_kw:
                        m["capex"] = abs(val)
                        _capex_from_ppe = True
                    elif m["capex"] is None:
                        m["capex"] = abs(val)  # 무형자산만 있는 경우 fallback
                    break
                else:
                    if m[field] is not None:
                        break  # account_id 매핑으로 이미 세팅된 값 유지
                    m[field] = val
                    break
    return m


class DARTCollector(BaseCollector):
    """DART Open API 수집기."""

    # DART는 동기 라이브러리 — 단일 스레드 풀로 직렬화
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dart")

    def __init__(self, api_key: str = ""):
        super().__init__(rate_limit_secs=0.0, name="DART")
        self._api_key = api_key or config.DART_API_KEY
        self._dart    = None
        self._disclosure_cache: dict[date, set[str]] = {}

    def _get_dart(self):
        """OpenDartReader 지연 초기화 (import 시간 절약)."""
        if self._dart is None:
            import OpenDartReader as _ODR
            import glob, os
            try:
                self._dart = _ODR(self._api_key)
            except Exception:
                # pkl 캐시 손상 시 삭제 후 재시도
                for f in glob.glob("/tmp/**/*.pkl", recursive=True):
                    try: os.remove(f)
                    except: pass
                try:
                    self._dart = _ODR(self._api_key)
                except Exception as e:
                    logger.error(f"[DART] OpenDartReader 초기화 실패: {e}")
        return self._dart

    async def _run_sync(self, fn, *args):
        """동기 함수를 DART 전용 스레드 풀에서 실행."""
        return await asyncio.get_event_loop().run_in_executor(
            self._executor, fn, *args
        )

    # ══════════════════════════════════════════════════════════
    # 유틸: 최근 공시 분기
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def latest_disclosed_quarter() -> tuple[int, int]:
        """DART 실제 공시 기준 최신 (year, quarter)."""
        m, y = date.today().month, date.today().year
        if m >= 11: return (y, 3)
        if m >= 8:  return (y, 2)
        if m >= 5:  return (y, 1)
        if m >= 4:  return (y - 1, 4)
        return (y - 1, 3)

    # ══════════════════════════════════════════════════════════
    # 1) 재무제표 수집
    # ══════════════════════════════════════════════════════════

    async def fetch_financials(
        self,
        stock_code:  str,
        years:       int = 10,
        latest_only: bool = False,
    ) -> list[dict]:
        """
        DART 재무제표 수집.
        latest_only=False: 최근 years년치 전체 (최초 등록 시)
        latest_only=True : 최근 2개 분기만 (공시 업데이트 시)

        반환: [{stock_code, year, quarter, is_annual, revenue, ..., eps, bps, ...}, ...]
        """
        return await self._run_sync(
            self._fetch_financials_sync, stock_code, years, latest_only
        )

    def _fetch_financials_sync(
        self,
        stock_code:  str,
        years:       int,
        latest_only: bool,
    ) -> list[dict]:
        dart = self._get_dart()
        if dart is None:
            return []

        latest_y, latest_q = self.latest_disclosed_quarter()
        target_years = [latest_y, latest_y - 1] if latest_only else list(range(latest_y, latest_y - years, -1))

        results = []
        new_cnt = 0

        for year in target_years:
            for quarter, rprt_code in _RPRT_CODE.items():
                # 미래 분기 스킵
                if year > latest_y or (year == latest_y and quarter > latest_q):
                    continue

                # ── Rate limiter: finstate 호출마다 쿼터 차감 ──
                if _USE_RL and not _rl.wait("DART"):
                    logger.warning(f"[DART재무] 쿼터 소진 — {stock_code} 수집 중단")
                    return results

                fn_data = None
                for fs_div in ["CFS", "OFS"]:
                    try:
                        df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
                        if df is not None and not df.empty:
                            fn_data = df
                            break
                    except Exception:
                        pass
                    if _USE_RL:
                        _rl.wait("DART")

                if fn_data is None or fn_data.empty:
                    if _USE_RL and not _rl.wait("DART"):
                        return results
                    try:
                        fn_data = dart.finstate(stock_code, year, rprt_code)
                    except Exception:
                        pass

                if fn_data is None or fn_data.empty:
                    continue

                m = _parse_fin_df(fn_data, stock_code=stock_code)
                results.append({
                    "stock_code": stock_code,
                    "year":       year,
                    "quarter":    quarter,
                    "is_annual":  (quarter == 4),
                    **m,
                })
                new_cnt += 1

                if latest_only and new_cnt >= 2:
                    return results

        logger.info(f"[DART재무] {stock_code}: {new_cnt}건")
        return results

    # ══════════════════════════════════════════════════════════
    # 2) 현금흐름표 수집
    # ══════════════════════════════════════════════════════════

    async def fetch_cash_flows(
        self,
        stock_code: str,
        years:      int = 10,
    ) -> list[dict]:
        """
        DART 현금흐름표 수집.
        반환: [{stock_code, year, quarter, is_annual, operating_cf, ...}, ...]
        """
        return await self._run_sync(self._fetch_cf_sync, stock_code, years)

    def _fetch_cf_sync(self, stock_code: str, years: int) -> list[dict]:
        dart = self._get_dart()
        if dart is None:
            return []

        latest_y, latest_q = self.latest_disclosed_quarter()
        results = []

        for year in range(latest_y, latest_y - years, -1):
            for quarter, rprt_code in _RPRT_CODE.items():
                if year > latest_y or (year == latest_y and quarter > latest_q):
                    continue
                # ── Rate limiter: CF finstate 호출마다 쿼터 차감 ──
                if _USE_RL and not _rl.wait("DART"):
                    logger.warning(f"[DART-CF] 쿼터 소진 — {stock_code} CF 수집 중단")
                    return results

                # CFS·OFS 각각 독립 수집 — 연결/별도 모두 저장
                for fs_div in ["CFS", "OFS"]:
                    try:
                        df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
                        if df is not None and not df.empty:
                            accs = df["account_nm"].astype(str).str.replace(" ", "")
                            if accs.str.contains("영업활동").any():
                                m = _parse_cf_df(df, stock_code=stock_code)
                                results.append({
                                    "stock_code":  stock_code,
                                    "year":        year,
                                    "quarter":     quarter,
                                    "is_annual":   (quarter == 4),
                                    "report_type": fs_div,
                                    **m,
                                })
                    except Exception:
                        pass
                    if _USE_RL:
                        _rl.wait("DART")

        logger.info(f"[DART현금흐름] {stock_code}: {len(results)}건")
        return results

    # ══════════════════════════════════════════════════════════
    # 3) 공시 목록 (당일/최근 7일)
    # ══════════════════════════════════════════════════════════

    async def get_disclosures_today(self) -> set[str]:
        """
        오늘 공시가 있는 stock_code 집합 반환.
        하루 1회만 DART 조회 (일내 캐시).
        """
        today = date.today()
        if today in self._disclosure_cache:
            return self._disclosure_cache[today]

        codes = await self._run_sync(self._fetch_all_disclosures_sync, today)
        self._disclosure_cache[today] = codes
        return codes

    def _fetch_all_disclosures_sync(self, target_date: date) -> set[str]:
        dart = self._get_dart()
        if dart is None:
            return set()

        today_str = target_date.strftime("%Y%m%d")
        codes: set[str] = set()

        for kind in ["A", "B"]:
            try:
                df = dart.list(today_str, today_str, kind=kind)
                if df is not None and not df.empty and "stock_code" in df.columns:
                    for code in df["stock_code"].dropna().unique():
                        codes.add(str(code).zfill(6))
            except Exception as e:
                logger.debug(f"[DART공시] kind={kind}: {e}")

        logger.info(f"[DART공시] 오늘 공시 종목 {len(codes)}개")
        return codes

    async def has_financial_disclosure(self, stock_code: str) -> bool:
        """
        최근 7일 이내 재무 관련 공시(_FINANCIAL_KEYWORDS) 여부.
        True → 재무 재수집 트리거.
        """
        return await self._run_sync(self._check_financial_disclosure_sync, stock_code)

    def _check_financial_disclosure_sync(self, stock_code: str) -> bool:
        dart = self._get_dart()
        if dart is None:
            return False

        today     = date.today()
        week_ago  = (today - timedelta(days=7)).strftime("%Y%m%d")
        today_str = today.strftime("%Y%m%d")

        for kind in ["A", "B"]:
            try:
                df = dart.list(week_ago, today_str, kind=kind)
                if df is None or df.empty:
                    continue
                if "stock_code" not in df.columns or "report_nm" not in df.columns:
                    continue
                target = df[df["stock_code"] == stock_code]
                for _, row in target.iterrows():
                    nm = str(row.get("report_nm", ""))
                    if any(kw in nm for kw in _FINANCIAL_KEYWORDS):
                        logger.info(f"[DART] {stock_code} 재무공시 발견: {nm}")
                        return True
            except Exception as e:
                logger.debug(f"[DART] {stock_code} 공시 확인 오류: {e}")
        return False
