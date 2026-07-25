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

# 수주(단일판매·공급계약) 공시 키워드 — kind='I'(거래소공시) 중 필터
_ORDER_CONTRACT_KEYWORDS = ["단일판매", "공급계약체결", "공급계약해지"]

# 계정과목 → 필드명 매핑
_ACCOUNT_MAP = [
    (["매출액", "영업수익"],                                      "revenue"),
    (["영업이익"],                                                "operating_profit"),
    (["당기순이익"],                                              "net_income"),      # "주당" 제외
    (["자산총계"],                                                "total_assets"),
    (["부채총계"],                                                "total_liabilities"),
    (["자본총계"],                                                "total_equity"),
    (["자본금"],                                                  "capital_stock"),
    (["기본주당순이익", "주당순이익", "기본EPS"],                  "eps"),
    (["주당순자산", "1주당순자산가액"],                            "bps"),
    (["주당배당금", "주당현금배당금"],                             "dps"),
    (["현금및현금성자산", "현금성자산"],                           "cash"),
    (["발행주식수", "유통주식수"],                                 "total_shares"),
    (["감가상각비"],                                              "depreciation_amortization"),
]

_BLANK_FIN = {
    "revenue": 0.0, "operating_profit": 0.0, "net_income": 0.0,
    "total_assets": 0.0, "total_liabilities": 0.0, "total_equity": 0.0,
    "capital_stock": 0.0, "eps": 0.0, "bps": 0.0, "dps": 0.0,
    "cash": 0.0, "total_shares": 0.0, "depreciation_amortization": 0.0,
}

_BLANK_CF = {
    "operating_cf": 0.0, "investing_cf": 0.0, "financing_cf": 0.0,
    "capex": 0.0, "cash_end": 0.0,
}

# CF 계정과목 매핑
_CF_MAP = [
    (["영업활동으로인한현금흐름", "영업활동현금흐름"],          "operating_cf"),
    (["투자활동으로인한현금흐름", "투자활동현금흐름"],          "investing_cf"),
    (["재무활동으로인한현금흐름", "재무활동현금흐름"],          "financing_cf"),
    (["유형자산의취득", "유형자산취득"],                        "capex"),
    (["현금및현금성자산의순증감", "기말현금및현금성자산"],       "cash_end"),
]


def _parse_fin_df(df) -> dict:
    """DART finstate DataFrame → 재무 dict."""
    import pandas as _pd
    m = dict(_BLANK_FIN)
    if df is None or df.empty:
        return m
    for _, row in df.iterrows():
        acc = str(row.get("account_nm", "")).replace(" ", "")
        val_col = "thstrm_amount"
        if not acc or val_col not in row or _pd.isna(row[val_col]):
            continue
        try:
            val = float(str(row[val_col]).replace(",", ""))
        except ValueError:
            continue
        for keywords, field in _ACCOUNT_MAP:
            if field == "net_income" and "주당" in acc:
                continue
            if any(kw in acc for kw in keywords):
                m[field] = val
                break
    return m


def _parse_cf_df(df) -> dict:
    """DART cash flow DataFrame → CF dict."""
    import pandas as _pd
    m = dict(_BLANK_CF)
    if df is None or df.empty:
        return m
    for _, row in df.iterrows():
        acc = str(row.get("account_nm", "")).replace(" ", "")
        val_col = "thstrm_amount"
        if not acc or val_col not in row or _pd.isna(row[val_col]):
            continue
        try:
            val = float(str(row[val_col]).replace(",", ""))
        except ValueError:
            continue
        for keywords, field in _CF_MAP:
            if any(kw in acc for kw in keywords):
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

                fn_data = None
                for fs_div in ["CFS", "OFS"]:
                    try:
                        df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
                        if df is not None and not df.empty:
                            fn_data = df
                            break
                    except Exception:
                        pass

                if fn_data is None or fn_data.empty:
                    try:
                        fn_data = dart.finstate(stock_code, year, rprt_code)
                    except Exception:
                        pass

                if fn_data is None or fn_data.empty:
                    continue

                m = _parse_fin_df(fn_data)
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
                cf_data = None
                for fs_div in ["CFS", "OFS"]:
                    try:
                        df = dart.finstate_all(stock_code, year, rprt_code, fs_div=fs_div)
                        if df is not None and not df.empty:
                            # 현금흐름 계정명 포함 여부 확인
                            accs = df["account_nm"].astype(str).str.replace(" ", "")
                            if accs.str.contains("영업활동").any():
                                cf_data = df
                                break
                    except Exception:
                        pass

                if cf_data is None:
                    continue

                m = _parse_cf_df(cf_data)
                results.append({
                    "stock_code": stock_code,
                    "year":       year,
                    "quarter":    quarter,
                    "is_annual":  (quarter == 4),
                    **m,
                })

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

    # ══════════════════════════════════════════════════════════
    # 4) 수주공시 (단일판매·공급계약체결/해지) — 수주잔고 급증 탐지용
    # ══════════════════════════════════════════════════════════

    async def get_contract_disclosures(self, stock_code: str, start: str, end: str) -> list[dict]:
        """종목의 기간(YYYYMMDD~YYYYMMDD) 내 단일판매·공급계약 공시 목록 (금액 파싱 전)."""
        return await self._run_sync(self._fetch_contract_list_sync, stock_code, start, end)

    def _fetch_contract_list_sync(self, stock_code: str, start: str, end: str) -> list[dict]:
        dart = self._get_dart()
        if dart is None:
            return []
        try:
            df = dart.list(stock_code, start=start, end=end, kind="I", final=False)
        except Exception as e:
            logger.debug(f"[DART수주] {stock_code} 목록 조회 오류: {e}")
            return []
        if df is None or df.empty or "report_nm" not in df.columns:
            return []
        out = []
        for _, row in df.iterrows():
            nm = str(row.get("report_nm", ""))
            if not any(kw in nm for kw in _ORDER_CONTRACT_KEYWORDS):
                continue
            out.append({
                "rcept_no":       str(row.get("rcept_no", "")),
                "rcept_dt":       str(row.get("rcept_dt", "")),
                "report_nm":      nm,
                "corp_name":      str(row.get("corp_name", "")),
                "is_termination": "해지" in nm,
            })
        return out

    async def get_todays_contract_disclosures(self) -> list[dict]:
        """오늘자 전체 종목 수주(단일판매·공급계약) 공시 스캔 (kind='I'). 스케줄러 일일 잡용."""
        return await self._run_sync(self._fetch_todays_contract_sync)

    def _fetch_todays_contract_sync(self) -> list[dict]:
        dart = self._get_dart()
        if dart is None:
            return []
        today_str = date.today().strftime("%Y%m%d")
        try:
            df = dart.list(today_str, today_str, kind="I")
        except Exception as e:
            logger.warning(f"[DART수주] 오늘자 목록 조회 오류: {e}")
            return []
        if df is None or df.empty or "report_nm" not in df.columns:
            return []
        out = []
        for _, row in df.iterrows():
            nm = str(row.get("report_nm", ""))
            if not any(kw in nm for kw in _ORDER_CONTRACT_KEYWORDS):
                continue
            code = str(row.get("stock_code", "") or "").zfill(6)
            if not code.isdigit() or len(code) != 6:
                continue
            out.append({
                "stock_code":     code,
                "rcept_no":       str(row.get("rcept_no", "")),
                "rcept_dt":       str(row.get("rcept_dt", "")),
                "report_nm":      nm,
                "corp_name":      str(row.get("corp_name", "")),
                "is_termination": "해지" in nm,
            })
        return out

    async def parse_contract_document(self, rcept_no: str) -> dict:
        """공시 원문(document.xml)에서 계약금액/매출액대비/계약상대/계약기간 추출.
        DART는 회사마다 서식이 조금씩 달라 정규식 매칭 실패 시 parse_ok=False로
        표시하고 raw_snippet만 남긴다 — 사람 검증(verified) 후 확정하는 워크플로 전제.
        """
        return await self._run_sync(self._parse_contract_document_sync, rcept_no)

    def _parse_contract_document_sync(self, rcept_no: str) -> dict:
        result = {
            "contract_amount": None, "revenue_ratio_pct": None,
            "recent_revenue": None, "counterpart": None,
            "contract_date": None, "contract_start": None, "contract_end": None,
            "parse_ok": False, "raw_snippet": "",
        }
        dart = self._get_dart()
        if dart is None:
            return result
        try:
            text = dart.document(rcept_no)
        except Exception as e:
            logger.debug(f"[DART수주] {rcept_no} 원문 조회 오류: {e}")
            return result
        if not text:
            return result

        import re
        plain = re.sub(r"<[^>]+>", " ", str(text))
        plain = re.sub(r"&nbsp;?", " ", plain)
        plain = re.sub(r"\s+", " ", plain).strip()
        result["raw_snippet"] = plain[:800]

        def _num(pattern: str):
            m = re.search(pattern, plain)
            if not m:
                return None
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None

        result["contract_amount"]   = _num(r"계약금액\s*\(?원?\)?\s*([\d,]{4,})")
        result["revenue_ratio_pct"] = _num(r"매출액\s*대비\s*\(?%?\)?\s*([\d]+(?:\.[\d]+)?)")
        result["recent_revenue"]    = _num(r"최근\s*매출액\s*\(?원?\)?\s*([\d,]{4,})")

        m = re.search(r"계약상대\s*[:：]?\s*([^\d]{2,60}?)\s*(?=\d|판매[·ㆍ]?공급지역|계약기간|시작일|주요\s*계약조건)", plain)
        if m:
            cand = m.group(1).strip(" -:：")
            if cand:
                result["counterpart"] = cand[:100]

        m = re.search(r"계약\s*[\(（]?수주[\)）]?\s*일자\s*[:：]?\s*(\d{4}[-./]\d{2}[-./]\d{2})", plain)
        if m:
            result["contract_date"] = m.group(1)

        dates = re.findall(r"(\d{4}[-./]\d{2}[-./]\d{2})", plain)
        if len(dates) >= 2:
            result["contract_start"], result["contract_end"] = dates[0], dates[1]
        elif len(dates) == 1 and not result["contract_date"]:
            result["contract_date"] = dates[0]

        result["parse_ok"] = result["contract_amount"] is not None
        return result
