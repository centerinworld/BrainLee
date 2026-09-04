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
import re
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

# 수주(단일판매·공급계약) 공시 키워드 — kind='I'(거래소공시) 중 필터
_ORDER_CONTRACT_KEYWORDS = ["단일판매", "공급계약체결", "공급계약해지"]

# 계정과목 → 필드명 매핑
_ACCOUNT_MAP = [
    (["매출액", "영업수익", "매출"],                              "revenue"),
    (["영업이익"],                                                "operating_profit"),
    (["당기순이익", "당기순손익", "분기순이익", "반기순이익",
       "당기순손실", "분기순손실", "반기순손실",
       "당기순이익(손실)", "당기순손익(이익)", "연간순이익"],      "net_income"),      # "주당"·"귀속"·"비지배" 제외
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
      "유형자산감가상각비", "무형자산상각비", "사용권자산감가상각비",
      "감가상각및무형자산상각", "상각비", "감가및상각비",
      "유형자산및무형자산감가상각비"], "depreciation"),

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
    ni_components: dict[str, float] = {}
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

        if "손익계산서" in sj and acc_id in {
            "ifrs-full_ProfitLossFromContinuingOperations",
            "ifrs-full_ProfitLossFromDiscontinuedOperations",
        }:
            ni_components[acc_id] = val

        # Some issuers use GrossProfit's standard id for a top-line
        # "영업수익" row. It is revenue, not an operating-profit fallback.
        if acc_id == "ifrs-full_GrossProfit" and acc in {"영업수익", "수익"}:
            if m.get("revenue") in (None, 0, 0.0):
                m["revenue"] = val
            continue

        mapped = resolve_field(stock_code or "", acc_id, acc)
        if mapped in m:
            if mapped in ("revenue", "operating_profit", "net_income", "eps") and "손익계산서" not in sj:
                # 같은 표준 account_id가 자본변동표/현금흐름표에도 반복된다.
                # fallback 키워드로 다시 매칭되지 않도록 이 행 자체를 버린다.
                continue
            if mapped in ("total_assets", "total_liabilities", "total_equity", "capital_stock", "bps", "cash") and "재무상태표" not in sj:
                # ifrs-full_Equity가 자본변동표에 여러 번 등장하며 마지막
                # 세부항목으로 자본총계를 덮어쓰는 오염을 원천 차단한다.
                continue
        if mapped:
            # LOW_PRIORITY_OP(ifrs-full_GrossProfit 등)는 HIGH_PRIORITY_OP가 이미 세팅된 경우 덮어쓰기 금지
            # (SK하이닉스 사례: GrossProfit=58.69조가 OperatingIncomeLoss=47.21조를 덮어쓰는 버그)
            from collectors.dart_mapping_engine import HIGH_PRIORITY_OP_IDS, LOW_PRIORITY_OP_IDS
            if mapped == "operating_profit" and acc_id in LOW_PRIORITY_OP_IDS:
                if m.get("_op_high_priority_set"):
                    continue  # HIGH_PRIORITY 값이 이미 있으면 GrossProfit으로 덮어쓰지 않음
                m["_op_low_priority_pending"] = val  # HIGH_PRIORITY가 없으면 나중에 적용
                continue
            if val != 0 or m.get(mapped) in (None, 0, 0.0):
                m[mapped] = val
                if mapped == "operating_profit" and acc_id in HIGH_PRIORITY_OP_IDS:
                    m["_op_high_priority_set"] = True
            continue

        for keywords, field in account_map:
            if field == "net_income" and "주당" in acc:
                continue
            if field == "net_income" and any(x in acc for x in ("귀속", "비지배", "지배기업")):
                continue
            # "계속"/"중단": "계속영업당기순이익"(중단영업 제외 부분치)·"중단영업당기순이익"
            # (중단영업만의 부분치) 둘 다 진짜 총계 "당기순이익"의 서브스트링 매칭 위험
            # (2026-08-09(2차) 8개사 재조사로 실제 라벨 확인) — dart_collector.py는 총계를
            # 원하므로 두 부분치 모두 배제.
            if field == "net_income" and any(x in acc for x in ("계속", "중단")):
                continue
            if field == "net_income" and ni_excludes and any(x in acc for x in ni_excludes):
                continue
            # operating_profit: "중단영업이익"(중단영업 부문, HIGH_PRIORITY_OP_IDS account_id
            # 매핑이 없는 회사에서 낙하 위험)·"신용손실충당금 반영전 영업이익"(금융업 조정치)가
            # "영업이익" 키워드의 서브스트링 — 2026-08-09(2차) 삼성전자 등 8개사 원문 폭넓게
            # 재조사 중 발견(기존엔 "계속영업이익"만 제외돼 있었음, 대칭적으로 "중단"도 위험
            # 동일). account_id 매핑이 우선 적용되는 회사는 미발현이나 선제 방어.
            if field == "operating_profit" and any(x in acc for x in ("중단", "신용손실충당금", "반영전")):
                continue
            # revenue: "매출원가", "매출총이익", "매출차감" 등 하위항목은 제외
            # (ifrs-full_Revenue로 account_id 매핑이 먼저 수행되므로, 키워드 스캔에서 하위항목이 덮어쓰는 버그 방지)
            # "기타" 추가(2026-08-09): fnguide_financial_collector.py의 동일 파서에서 LG화학
            # 실측으로 발견된 것과 같은 클래스 버그 — "영업수익" 키워드가 "기타영업수익"(소액
            # 부수항목)에 먼저 매칭될 위험. resolve_field(account_id 매핑)가 우선 시도되므로
            # 이 회사는 실제로는 그 경로로 정상 값(45.9조)이 들어가 있어 라이브 데이터엔
            # 미발현이었으나, account_id 매핑이 없는 회사에서는 동일하게 재현될 수 있어
            # 선제적으로 방어.
            if field == "revenue" and any(x in acc for x in ("원가", "총이익", "차감", "비용", "손실", "기타")):
                continue
            # 손익 항목은 포괄손익계산서 행만 허용 (자본변동표 0 덮어쓰기 방지)
            if field in ("revenue", "operating_profit", "net_income", "eps"):
                if "손익계산서" not in sj:
                    continue
            # 재무상태 항목은 재무상태표 행만 허용. cash도 여기 포함 — 2026-08-09 수정:
            # 현금흐름표의 "현금및현금성자산의순증가(감소)"(계정id
            # ifrs-full_IncreaseDecreaseInCashAndCashEquivalents, 매핑테이블 미등록이라
            # 키워드 폴백으로 낙하)가 account_nm에 "현금및현금성자산"을 포함해 cash 키워드와
            # 오매칭되던 버그. 이 값은 기간 중 증감액이라 음수가 정상이며, 재무상태표의
            # 현금잔액과 혼동하면 절대 음수일 수 없는 잔액이 음수로 저장됨(실측: 2,484종목·
            # 16,730행 오염 확인, 에이엘티 172670 2026Q1 cash=-311,804,881 등).
            if field in ("total_assets", "total_liabilities", "total_equity", "capital_stock", "bps", "cash"):
                if "재무상태표" not in sj:
                    continue
            # total_liabilities: "부채총계" 키워드가 "자본과부채총계"(=자산총계와 동일값,
            # 자본+부채 합계 확인용 행)의 서브스트링이라 오매칭 위험 — 2026-08-09 현대건설
            # (000720) 원문 실측으로 확인: 이 행이 진짜 "부채총계" 행보다 DataFrame에서
            # 먼저 등장(row#35 vs row#49)해, "이미 세팅된 값은 덮어쓰지 않음" 보호 로직이
            # 오히려 잘못된 첫 매칭을 고정시켜버리는 조합. 이 회사는 현재 resolve_field
            # (account_id 매핑)가 먼저 정상 값을 채워 라이브 데이터엔 미발현이었으나,
            # account_id 매핑이 없는 회사에서 재현될 수 있어 선제 방어.
            if field == "total_liabilities" and "자본" in acc:
                continue
            # eps: 우선주와 보통주 EPS를 별도 행으로 공시하는 회사(예: 우선주 기본주당이익/
            # 보통주기본주당이익)에서 "기본주당이익" 키워드가 둘 다에 매칭 — 2026-08-09
            # 현대건설(000720) 원문 실측으로 확인: 우선주 행(3369원)이 보통주 행(3319원,
            # DART 원문상 진짜 valuation 기준)보다 먼저 등장해 잘못된 값이 고정 저장되고
            # 있었음(라이브 financial_data.eps=3369 확인, 재무 무결성 규칙상 이 배치수정은
            # 별도 스크립트로 사용자 확인 후 수행 — 여기서는 향후 수집분만 방지).
            if field == "eps" and "우선주" in acc:
                continue
            if any(kw in acc for kw in keywords):
                # 이미 비영값이 세팅된 경우 키워드 스캔으로 덮어쓰기 방지
                # (resolve_field로 올바른 값이 이미 세팅된 경우 보호)
                if m.get(field) not in (None, 0, 0.0):
                    break  # 이미 올바른 값 존재 → 키워드 매칭으로 덮어쓰지 않음
                if val != 0 or m.get(field) in (None, 0, 0.0):
                    m[field] = val
                break

    # LOW_PRIORITY fallback: HIGH_PRIORITY operating_profit이 없으면 GrossProfit 사용
    if m.get("operating_profit") in (None, 0, 0.0) and m.get("_op_low_priority_pending") is not None:
        m["operating_profit"] = m["_op_low_priority_pending"]

    if m.get("net_income") is None and ni_components:
        m["net_income"] = sum(ni_components.values())

    # 내부 추적 키 제거
    m.pop("_op_high_priority_set", None)
    m.pop("_op_low_priority_pending", None)

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
        # depreciation: 2026-08-10 발견 — ①"대손상각비"(수취채권 대손충당금, 감가상각과 무관)가
        # account_id 미매핑(resolve_field=None)이라 아래 키워드폴백의 bare "상각비"에 오매칭.
        # ②"사용권자산손상차손"(사용권자산 "손상"이지 "감가상각"이 아님)이 DART 원문 자체에서
        # account_id='ifrs-full_DepreciationRightofuseAssets'(표준태그명이 Depreciation이라
        # 필자측 XBRL 태깅 오류로 추정)로 잘못 태깅돼 resolve_field가 depreciation으로 매핑 —
        # 000020 실측: 값0인 이 행이 무형자산상각비(33.4억)+감가상각비(163.3억) 이후 마지막에
        # 처리되며 단순 덮어쓰기 로직 때문에 진짜 감가상각비 총액을 0으로 지워버림. account_id
        # 태그 신뢰만으로는 방어 불가 → account_nm 텍스트로 "손상"/"대손" 계열은 항상 배제.
        if "손상" in acc or "대손" in acc:
            mapped = None if mapped == "depreciation" else mapped
        if mapped in m:
            # 현금흐름표 계열만 허용
            if mapped in ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"):
                # OCF/ICF/FCF 순합계 필드에서 "현금유입액"/"현금유출액" 같은 단방향 소계는 skip
                if mapped in ("operating_cf", "investing_cf", "financing_cf") and (
                    "유입액" in acc or "유출액" in acc or "현금유입" in acc or "현금유출" in acc
                ):
                    pass  # account_id 매핑 스킵 → keyword scan으로 낙하
                elif mapped == "depreciation":
                    # 2026-08-10 수정: PP&E/무형자산/사용권자산/투자부동산 감가상각을 각각
                    # 별도 행으로 공시하는 회사(000080/000100/000140 실측)에서 기존 단순
                    # 덮어쓰기가 마지막 매칭 행 하나만 남기고 나머지를 지워 총액을 과소집계
                    # 하고 있었음 — 전부 서로 다른 자산군의 감가상각으로 합산이 맞다.
                    m["depreciation"] = (m["depreciation"] or 0.0) + val
                    continue
                elif mapped == "capex":
                    is_ppe = acc_id in _CAPEX_PPE_IDS
                    is_int = acc_id in _CAPEX_INTANGIBLE_IDS
                    if is_ppe:
                        # PP&E 항상 우선. 2026-08-10 수정: _CAPEX_PPE_IDS에 주력자산
                        # (ifrs-full_PurchaseOfPropertyPlantAndEquipment)과 기타자산
                        # (dart_PurchaseOfOtherPropertyPlantAndEquipment)이 함께 등록돼
                        # 있는데, 기존엔 둘 다 단순 덮어쓰기라 DataFrame 행 순서에 따라
                        # "유형자산의 취득"(주력)과 "기타유형자산의 취득"(잔여) 중 나중에
                        # 나온 쪽만 남고 먼저 나온 쪽이 사라지는 문제였음 — 둘은 서로 다른
                        # 계정이라 대체가 아니라 합산이 맞음(068270 실측: 기타=0이라 우연히
                        # 미발현이었으나 기타값이 있는 회사에서는 과소/과대 왜곡 가능).
                        m["capex"] = (m["capex"] or 0.0) + abs(val)
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

        sj = str(row.get("sj_nm", "")).replace(" ", "")
        for keywords, field in _CF_MAP:
            # 2026-08-10: finstate_all()은 BS/IS/CF/SCE 전체를 하나의 DataFrame으로 반환하는데
            # _CF_MAP 키워드 폴백엔 지금까지 재무제표유형 필터가 전혀 없었음 — 000050 실측으로
            # 발견: 손익계산서의 "감가상각비"(판매비와관리비 세부내역, 113.5억, account_id가
            # dart_DepreciationExpenseSellingGeneralAdministrativeExpenses라 resolve_field는
            # None 반환)가 bare "감가상각비" 키워드에 걸려 현금흐름표의 진짜 감가상각비
            # 가산행(374.5억)과 합산되며 이중계상되고 있었음 — 개념 자체가 다른 두 공시(SG&A
            # 세부내역 vs 비현금 조정 가산액)라 절대 합산하면 안 됨. 현금흐름표 행만 허용.
            if field in ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"):
                if "현금흐름표" not in sj:
                    continue
            if field == "depreciation" and ("손상" in acc or "대손" in acc):
                # 2026-08-10: "대손상각비"(수취채권 대손, account_id 미매핑이라 여기로 낙하)가
                # bare "상각비" 키워드에 오매칭되던 실측 버그(000020) 방지.
                continue
            if any(kw in acc for kw in keywords):
                if field == "capex":
                    # PP&E 키워드: 유형자산의취득, 유형자산및투자부동산의취득
                    is_ppe_kw = any(kw in acc for kw in
                                    ["유형자산의취득", "유형자산취득", "유형자산및투자부동산의취득"])
                    if is_ppe_kw:
                        # 2026-08-10: account_id 분기와 동일하게 합산(위 주석 참조) — 이 행이
                        # "기타유형자산의 취득"이면 "유형자산의취득" 서브스트링을 포함해
                        # 같은 is_ppe_kw로 잡히므로, 덮어쓰기가 아니라 누적해야 함.
                        m["capex"] = (m["capex"] or 0.0) + abs(val)
                        _capex_from_ppe = True
                    elif m["capex"] is not None and _capex_from_ppe:
                        break  # PP&E 값 이미 확정 → 무형자산 키워드는 스킵(보호)
                    elif m["capex"] is None:
                        m["capex"] = abs(val)  # 무형자산만 있는 경우 fallback
                    break
                elif field == "depreciation":
                    # 2026-08-10: account_id 분기와 동일하게 합산 — 여러 자산군의 감가상각을
                    # 별도 행으로 공시하는 회사에서 첫값보호(overwrite 금지)만으로는 나머지
                    # 자산군이 누락되어 총액 과소집계됨.
                    m["depreciation"] = (m["depreciation"] or 0.0) + val
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
                    except Exception: pass
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
                df = dart.list(start=today_str, end=today_str, kind=kind)
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
                df = dart.list(start=week_ago, end=today_str, kind=kind)
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
        """종목의 기간(YYYYMMDD~YYYYMMDD) 내 단일판매·공급계약 공시 목록."""
        return await self._run_sync(self._fetch_contract_list_sync, stock_code, start, end)

    async def get_contract_disclosures_range(self, start: str, end: str) -> list[dict]:
        """기간(YYYYMMDD~YYYYMMDD) 내 전체 종목 단일판매·공급계약 공시 목록."""
        return await self._run_sync(self._fetch_contract_range_sync, start, end)

    def _fetch_contract_list_sync(self, stock_code: str, start: str, end: str) -> list[dict]:
        try:
            from collectors.dart_contract_collector import _fetch_dart_list, _is_contract_report
            items = _fetch_dart_list(start, end)
        except Exception as e:
            logger.debug(f"[DART수주] {stock_code} 목록 조회 오류: {e}")
            return []

        rows = []
        for row in items:
            code = str(row.get("stock_code", "") or "").zfill(6)
            if code != stock_code:
                continue
            report_nm = str(row.get("report_nm", "") or "")
            if not _is_contract_report(report_nm):
                continue
            rows.append({
                "stock_code": stock_code,
                "corp_name": row.get("corp_name"),
                "rcept_no": str(row.get("rcept_no", "")),
                "rcept_dt": str(row.get("rcept_dt", "")),
                "report_nm": report_nm,
                "is_termination": int(("해지" in report_nm) or ("취소" in report_nm)),
            })
        return rows

    def _fetch_contract_range_sync(self, start: str, end: str) -> list[dict]:
        try:
            from collectors.dart_contract_collector import _fetch_dart_list, _is_contract_report
            items = _fetch_dart_list(start, end)
        except Exception as e:
            logger.warning(f"[DART수주] 기간 목록 조회 오류 {start}~{end}: {e}")
            return []

        rows = []
        for row in items:
            report_nm = str(row.get("report_nm", "") or "")
            if not _is_contract_report(report_nm):
                continue
            code = str(row.get("stock_code", "") or "").zfill(6)
            if not code or code == "000000":
                continue
            rows.append({
                "stock_code": code,
                "corp_name": row.get("corp_name"),
                "rcept_no": str(row.get("rcept_no", "")),
                "rcept_dt": str(row.get("rcept_dt", "")),
                "report_nm": report_nm,
                "is_termination": int(("해지" in report_nm) or ("취소" in report_nm)),
            })
        return rows

    async def get_todays_contract_disclosures(self) -> list[dict]:
        """오늘자 전체 종목 수주(단일판매·공급계약) 공시 스캔."""
        return await self._run_sync(self._fetch_todays_contract_sync)

    def _fetch_todays_contract_sync(self) -> list[dict]:
        today_str = date.today().strftime("%Y%m%d")
        try:
            from collectors.dart_contract_collector import _fetch_dart_list, _is_contract_report
            items = _fetch_dart_list(today_str, today_str)
        except Exception as e:
            logger.warning(f"[DART수주] 오늘자 목록 조회 오류: {e}")
            return []

        rows = []
        for row in items:
            report_nm = str(row.get("report_nm", "") or "")
            if not _is_contract_report(report_nm):
                continue
            code = str(row.get("stock_code", "") or "").zfill(6)
            if not code or code == "000000":
                continue
            rows.append({
                "stock_code": code,
                "corp_name": row.get("corp_name"),
                "rcept_no": str(row.get("rcept_no", "")),
                "rcept_dt": str(row.get("rcept_dt", "")),
                "report_nm": report_nm,
                "is_termination": int(("해지" in report_nm) or ("취소" in report_nm)),
            })
        return rows

    async def parse_contract_document(self, rcept_no: str) -> dict:
        """공시 원문(document.xml)에서 계약금액/매출액대비/계약상대/계약기간 추출."""
        return await self._run_sync(self._parse_contract_document_sync, rcept_no)

    def _parse_contract_document_sync(self, rcept_no: str) -> dict:
        result = {
            "contract_amount": None,
            "revenue_ratio_pct": None,
            "recent_revenue": None,
            "counterpart": None,
            "contract_date": None,
            "contract_start": None,
            "contract_end": None,
            "parse_ok": False,
            "raw_snippet": "",
            "is_correction": False,
            "corrects_disclosed_at": None,
        }
        try:
            from collectors.dart_contract_collector import _extract_amounts, _fetch_dart_document
            text = _fetch_dart_document(rcept_no)
        except Exception as e:
            logger.debug(f"[DART수주] {rcept_no} 원문 조회 오류: {e}")
            return result

        plain = re.sub(r"\s+", " ", text.replace("\xa0", " "))
        result["raw_snippet"] = plain[:2000]
        parsed = _extract_amounts(plain)

        result["contract_amount"] = parsed.get("contract_amount_krw")
        result["recent_revenue"] = parsed.get("revenue_base")
        result["revenue_ratio_pct"] = parsed.get("contract_ratio_pct")
        result["counterpart"] = parsed.get("counterparty")
        result["contract_start"] = parsed.get("contract_start")
        result["contract_end"] = parsed.get("contract_end")
        result["parse_ok"] = parsed.get("contract_amount_krw") is not None
        result["is_correction"] = bool(parsed.get("is_correction"))
        result["corrects_disclosed_at"] = parsed.get("corrects_disclosed_at")
        return result
