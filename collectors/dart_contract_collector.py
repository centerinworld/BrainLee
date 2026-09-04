"""
collectors/dart_contract_collector.py — DART 수주·공급계약 공시 수집 + AI 분석

수집 대상 (DART 주요사항보고서 B):
  - 단일판매·공급계약체결의건
  - 수주 (방산·조선·플랜트)
  - 기술이전·라이선스 계약
  - MOU / LOI (단계별 추적)

AI 분석 → 매수 시그널 (★1~★5) → 텔레그램 알림
"""

import json
import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from services import gemini_openai_compat as openai
from services.gemini import is_configured

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
DART_API_KEY = None  # 기본 계약공시 키
DART_API_KEYS: list[str] | None = None
DART_CONTRACT_AI_ENABLED = os.getenv("DART_CONTRACT_AI_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
DART_CONTRACT_TELEGRAM_ENABLED = os.getenv("DART_CONTRACT_TELEGRAM_ENABLED", "0").lower() in {"1", "true", "yes", "on"}

# ───────── 수집 대상 공시 유형 ─────────
CONTRACT_KEYWORDS = [
    "단일판매", "공급계약", "수주", "기술이전", "기술수출",
    "라이선스", "license", "License",
    "공급기본계약", "물품공급", "서비스공급",
    "LOI", "MOU",
]

# 제외 키워드 (재무공시 오분류 방지)
# 2026-09 수정: "정정"은 더 이상 일괄 제외하지 않는다. "[기재정정]단일판매·공급계약체결"처럼
# 원 계약공시가 금액/기간을 정정하는 경우, CONTRACT_KEYWORDS 매칭 + _detect_correction()으로
# 정정후 값을 추출해 원본 행을 갱신하는 것이 목적 — 무조건 버리면 계약금액 축소/기간연장 등
# 실제 변경사항이 화면에 반영되지 않는 문제가 있었다(단, CONTRACT_KEYWORDS 자체가 없는
# 정정공시는 여전히 아래 for문에서 걸러진다).
EXCLUDE_KEYWORDS = [
    "합병", "분할", "전환사채", "유상증자",
    "자기주식", "감자", "청산", "거래정지",
]

# ───────── 단위 정규화 ─────────
UNIT_MAP = {
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "억원":   100_000_000,
    "원":     1,
    "천원":   1_000,
    "million": 1_000_000,   # USD
    "billion": 1_000_000_000,
}

# 계약 유형 분류
CONTRACT_TYPES = {
    "기술이전": ["기술이전", "기술수출", "라이선스", "license", "License", "기술료"],
    "수출계약": ["수출", "overseas", "export", "해외", "글로벌"],
    "공급계약": ["공급계약", "단일판매", "납품", "공급기본계약"],
    "방산수주": ["방산", "군납", "방위", "무기", "탄약", "K2", "K9", "천궁"],
    "MOU/LOI": ["MOU", "LOI", "양해각서", "의향서"],
}

# 시그널 강도 기준 (★ 개수)
SIGNAL_RULES = {
    5: lambda r: r["is_overseas"] and r["ratio"] >= 30 and r["ai_score"] >= 80,
    4: lambda r: r["ratio"] >= 20 or (r["is_overseas"] and r["ratio"] >= 10),
    3: lambda r: r["ratio"] >= 10 or r["ai_score"] >= 70,
    2: lambda r: r["ratio"] >= 5 or r["is_overseas"],
    1: lambda r: True,
}


def _krw_amount_from_text(text: str) -> Optional[float]:
    """텍스트에서 원화 환산/표기 금액을 보조 추출한다."""
    unit_re = r"(백\s*만원|천\s*만원|억\s*원|천\s*원|원)"
    patterns = [
        rf"약\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*{unit_re}",
        rf"\(\s*약?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*{unit_re}\s*\)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        raw = float(m.group(1).replace(",", ""))
        unit = m.group(2).replace(" ", "")
        if unit == "억원":
            unit = "억원"
        return raw * UNIT_MAP.get(unit, 1)
    return None


# ══════════════════════════════════════════════
#  DART API 호출 유틸
# ══════════════════════════════════════════════

def _get_api_keys() -> list[str]:
    """계약공시용 DART 키 목록을 우선순위대로 반환한다."""
    global DART_API_KEY, DART_API_KEYS
    if DART_API_KEYS:
        return DART_API_KEYS
    import os, sys
    # 프로젝트 루트를 sys.path에 추가 (collectors/ 하위에서 실행 시 대비)
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    try:
        from config import DART_API_KEY, DART_API_KEY2, DART_API_KEY3
        keys = [DART_API_KEY2, DART_API_KEY, DART_API_KEY3]
    except Exception:
        keys: list[str] = []
        # .env 파일 직접 읽기 (fallback)
        env_path = os.path.join(_root, ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                line = line.strip()
                if line.startswith("DART_API_KEY2="):
                    keys.append(line.split("=", 1)[1].strip().strip('"\''))
                elif line.startswith("DART_API_KEY="):
                    keys.append(line.split("=", 1)[1].strip().strip('"\''))
                elif line.startswith("DART_API_KEY3="):
                    keys.append(line.split("=", 1)[1].strip().strip('"\''))
        if not keys:
            keys = [
                os.getenv("DART_API_KEY2", ""),
                os.getenv("DART_API_KEY", ""),
                os.getenv("DART_API_KEY3", ""),
            ]

    deduped = [key for key in dict.fromkeys(key.strip() for key in keys if key and key.strip())]
    DART_API_KEYS = deduped
    DART_API_KEY = deduped[0] if deduped else ""
    return deduped


def _fetch_dart_list(bgn_de: str, end_de: str) -> list[dict]:
    """
    DART 전체 공시 목록 조회 (페이지 순회).

    "단일판매·공급계약"은 pblntf_ty가 없는 전체 공시에 포함됨.
    단기간(1~3일) 조회 시 총 1,000~3,000건 내외 — 전체 순회.
    """
    keys = _get_api_keys()
    if not keys:
        logger.warning("[DART수주] API 키 없음")
        return []
    url = "https://opendart.fss.or.kr/api/list.json"
    last_message = None

    for key_idx, key in enumerate(keys, start=1):
        all_items: list[dict] = []
        page_no = 1
        page_count = 100
        try:
            while True:
                resp = requests.get(url, params={
                    "crtfc_key": key,
                    "bgn_de": bgn_de,
                    "end_de": end_de,
                    "page_no": page_no,
                    "page_count": page_count,
                }, timeout=20)
                data = resp.json()

                status = str(data.get("status", ""))
                if status not in ("000", "013"):
                    last_message = data.get("message")
                    logger.warning(f"[DART수주] API 오류: {last_message}")
                    # 키별 한도/일시 오류면 다음 키로 재시도
                    if key_idx < len(keys):
                        logger.info("[DART수주] 보조 API 키로 재시도합니다 (%s/%s)", key_idx + 1, len(keys))
                    all_items = []
                    break

                items = data.get("list", [])
                all_items.extend(items)

                total = int(data.get("total_count", 0))
                fetched = page_no * page_count
                logger.debug(f"[DART수주] 페이지 {page_no}: {len(items)}건 (누적 {len(all_items)}/{total})")

                if fetched >= total or len(items) < page_count:
                    logger.info(f"[DART수주] 총 {len(all_items)}건 공시 수집")
                    return all_items
                page_no += 1
                time.sleep(0.1)

                # 안전 장치: 일별 조회는 최대 5000건 (50페이지)
                if page_no > 50:
                    logger.warning("[DART수주] 페이지 한도 초과 — 종료")
                    logger.info(f"[DART수주] 총 {len(all_items)}건 공시 수집")
                    return all_items

        except Exception as e:
            last_message = str(e)
            logger.error(f"[DART수주] 목록 조회 실패: {e}")

        if all_items:
            logger.info(f"[DART수주] 총 {len(all_items)}건 공시 수집")
            return all_items

    if last_message:
        logger.warning(f"[DART수주] 모든 API 키 시도 실패: {last_message}")
    logger.info("[DART수주] 총 0건 공시 수집")
    all_items = []
    return all_items


def _fetch_dart_document(rcept_no: str) -> str:
    """
    DART 공시 원문 → 텍스트 추출.

    DART는 ZIP 형식으로 응답. 압축 해제 후 HTML/XML 파싱.
    """
    import io, zipfile
    url = "https://opendart.fss.or.kr/api/document.xml"
    keys = _get_api_keys()
    if not keys:
        return ""
    for key_idx, key in enumerate(keys, start=1):
        try:
            resp = requests.get(url, params={
                "crtfc_key": key,
                "rcept_no": rcept_no,
            }, timeout=20)

            raw = resp.content

            # ZIP 형식 처리 (대부분)
            if raw[:2] == b"PK":
                try:
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    texts = []
                    for name in zf.namelist():
                        with zf.open(name) as f:
                            try:
                                content = f.read().decode("utf-8", errors="ignore")
                            except Exception:
                                content = f.read().decode("euc-kr", errors="ignore")
                            content = re.sub(r"<[^>]+>", " ", content)
                            content = re.sub(r"&[a-z]+;", " ", content)
                            content = re.sub(r"\s+", " ", content).strip()
                            texts.append(content)
                    return " ".join(texts)[:8000]
                except Exception as e:
                    logger.warning(f"[DART수주] ZIP 압축 해제 실패 {rcept_no}: {e}")

            text = resp.text
            if "status" in text and "message" in text and "사용한도를 초과" in text and key_idx < len(keys):
                logger.warning(f"[DART수주] 문서 API 오류: 사용한도를 초과하였습니다.")
                logger.info("[DART수주] 문서 조회 보조 API 키로 재시도합니다 (%s/%s)", key_idx + 1, len(keys))
                continue

            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&[a-z]+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:8000]

        except Exception as e:
            logger.error(f"[DART수주] 문서 조회 실패 {rcept_no}: {e}")
    return ""


# ══════════════════════════════════════════════
#  공시 필터링
# ══════════════════════════════════════════════

def _is_contract_report(report_nm: str) -> bool:
    nm = report_nm.lower()
    # 제외 키워드 먼저
    for kw in EXCLUDE_KEYWORDS:
        if kw in report_nm:
            return False
    # 포함 키워드
    for kw in CONTRACT_KEYWORDS:
        if kw.lower() in nm:
            return True
    return False


def _classify_contract_type(text: str, report_nm: str) -> str:
    combined = report_nm + " " + text[:1000]
    for ctype, keywords in CONTRACT_TYPES.items():
        for kw in keywords:
            if kw in combined:
                return ctype
    return "공급계약"


# ══════════════════════════════════════════════
#  금액/비율 파싱
# ══════════════════════════════════════════════

_CORRECTION_DATE_RE = re.compile(
    r"정정관련\s*공시서류제출일\s*([0-9]{4}[-.][0-9]{1,2}[-.][0-9]{1,2})"
)
_CORRECTION_TABLE_AMOUNT_RE = re.compile(
    r"계약금액\s*\(?원?\)?[:\s]*([0-9][0-9,]*(?:\.[0-9]+)?)\s+([0-9][0-9,]*(?:\.[0-9]+)?)"
)
_CORRECTION_TABLE_END_RE = re.compile(
    r"종료일\s*[:\s]*([0-9]{4}[-.][0-9]{1,2}[-.][0-9]{1,2})\s+([0-9]{4}[-.][0-9]{1,2}[-.][0-9]{1,2})"
)
_CORRECTION_TABLE_RATIO_RE = re.compile(
    r"매출액\s*대비\s*\(?%?\)?[:\s]*([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)"
)


def _detect_correction(text: str) -> dict:
    """정정신고(보고) 공시에서 원 공시 제출일과 정정전/정정후 값을 추출한다.

    DART "[기재정정]" 공시는 본문 상단에 "정정전/정정후" 비교표를 먼저 제시하고
    그 뒤에 정정 반영된 계약 전체 내역을 재수록하는 구조다(실제 사례 확인:
    아시아나IDT 20260903800538). 문제는 이 비교표가 "계약금액(원) 13,344,100,000
    13,501,400,000" 처럼 정정전 값을 먼저 적기 때문에, 일반 _extract_amounts()의
    첫 매칭(re.search) 패턴이 이 정정전(구) 값을 잘못 채택할 위험이 있다 —
    여기서 정정후 값을 먼저 확정해두면 이후 파싱 단계가 그 값을 우선하도록 만들 수 있다.
    """
    result = {
        "is_correction": False,
        "corrects_disclosed_at": None,  # YYYY-MM-DD, 원 공시 제출일
        "correction_amount_before": None,
        "correction_amount_after": None,
        "correction_end_before": None,
        "correction_end_after": None,
        "correction_ratio_before": None,
        "correction_ratio_after": None,
    }
    if "정정관련" not in text and "정정신고" not in text:
        return result
    m = _CORRECTION_DATE_RE.search(text)
    if not m:
        return result
    result["is_correction"] = True
    result["corrects_disclosed_at"] = m.group(1).replace(".", "-")
    amt = _CORRECTION_TABLE_AMOUNT_RE.search(text)
    if amt:
        result["correction_amount_before"] = float(amt.group(1).replace(",", ""))
        result["correction_amount_after"] = float(amt.group(2).replace(",", ""))
    end = _CORRECTION_TABLE_END_RE.search(text)
    if end:
        result["correction_end_before"] = end.group(1).replace(".", "-")
        result["correction_end_after"] = end.group(2).replace(".", "-")
    ratio = _CORRECTION_TABLE_RATIO_RE.search(text)
    if ratio:
        result["correction_ratio_before"] = float(ratio.group(1))
        result["correction_ratio_after"] = float(ratio.group(2))
    return result


def _extract_amounts(text: str) -> dict:
    """공시 원문에서 계약금액, 매출액, 비율 추출."""
    result = {
        "contract_amount": None,
        "contract_unit": None,
        "contract_amount_krw": None,
        "revenue_base": None,
        "contract_ratio_pct": None,
        "counterparty": None,
        "counterparty_country": None,
        "is_overseas": False,
        "contract_start": None,
        "contract_end": None,
    }
    result.update(_detect_correction(text))

    # 계약금액 추출
    # 2026-07-21 수정: 실제 DART 표준양식은 "계약금액(원) 32,352,000,000"처럼 단위가
    # 라벨 직후 괄호 안에 오고 숫자 자체엔 단위가 안 붙는 형태가 대부분이었음(기존 규칙은
    # 숫자 뒤에 단위가 붙는 형태만 가정해 대부분 매칭 실패). "확정 계약금액 -"처럼 값이
    # "-"(미공시/조건부)인 라벨은 자연히 건너뛰고 다음 라벨(계약금액 총액 등)에서 재시도한다.
    # 2026-09 수정: 정정공시는 위에서 이미 "정정후" 금액을 확정했으므로, 그 값이 있으면
    # 아래 일반 패턴 루프를 건너뛰고 바로 채택한다(정정전/정정후 비교표의 첫 숫자가
    # 구 값이라 일반 re.search 패턴이 오채택할 위험을 원천 차단).
    NUM_RE = r"(?P<num>[0-9][0-9,]*(?:\.[0-9]+)?)"
    UNIT_RE = r"(?P<unit>백만원|천만원|억원|천원|원|USD|EUR)"
    POST_UNIT_RE = r"(?P<post_unit>백만원|천만원|억원|천원|원)"
    if result.get("correction_amount_after") is not None:
        # 정정공시는 위에서 이미 "정정후" 금액을 확정했으므로 일반 패턴 루프(아래)를
        # 건너뛰고 바로 채택 — 정정전/정정후 비교표의 첫 숫자(구 값)를 일반
        # re.search 패턴이 오채택할 위험을 원천 차단. 계약상대방/계약기간 등
        # 나머지 필드는 아래 로직이 계속 이어서 정상 채운다.
        result["contract_amount"] = result["correction_amount_after"]
        result["contract_unit"] = "원"
        result["contract_amount_krw"] = result["correction_amount_after"]
        if result.get("correction_end_after"):
            result["contract_end"] = result["correction_end_after"]
        patterns = []
    else:
        patterns = [
            rf"해지금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"확정\s*계약금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"계약금액\s*총액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"총\s*계약금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"계약금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"계약\s*금액[\s\S]{{0,80}}?(?P<unit>USD|EUR)\s*{NUM_RE}",
            rf"계약\s*금액[\s\S]{{0,40}}?{NUM_RE}\s*{POST_UNIT_RE}",
            rf"공급금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"수주금액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
            rf"계약\s*총액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
        ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        unit = (m.groupdict().get("unit") or m.groupdict().get("post_unit") or "원").strip()
        raw_val = float(m.group("num").replace(",", ""))
        # 2026-09 수정: "계약금액 1) 총계약금액 : USD 19,500,000..."처럼 단위·괄호가
        # 전부 optional인 패턴("계약금액\s*(unit)?[:\s]*숫자")이 항목 나열번호("1)", "2)"
        # 등)를 계약금액으로 오인식하는 사례가 다수 발견됨(기술이전/바이오 자유서식
        # 공시에서 흔함). 단위가 명시 안 됐는데(기본값 '원') 값이 만원 미만이거나,
        # 숫자 바로 뒤가 ')' 또는 '.'로 끝나는(목록번호로 보이는) 경우는 실제 계약금액일
        # 수 없으므로 이 패턴을 버리고 다음(더 구체적인) 패턴으로 넘어간다.
        raw_digits = m.group("num").replace(",", "")
        looks_like_list_marker = (
            len(raw_digits) <= 2
            and text[m.end():m.end() + 1] in (")", ".")
        )
        implausibly_small = unit == "원" and raw_val < 10000
        if looks_like_list_marker or implausibly_small:
            continue
        multiplier = UNIT_MAP.get(unit, 1)
        result["contract_amount"] = raw_val
        result["contract_unit"] = unit
        if unit in {"USD", "EUR"}:
            nearby = text[max(0, m.start() - 40):min(len(text), m.end() + 240)]
            result["contract_amount_krw"] = _krw_amount_from_text(nearby) or _krw_amount_from_text(text)
        else:
            result["contract_amount_krw"] = raw_val * multiplier
        break

    # 매출액 대비 비율 추출 (2026-07-21: "매출액대비(%) 48.7"처럼 공백/괄호 없는 변형 추가)
    # ⚠️ 기존 3번째 폴백 패턴("N%...매출|수주" 근처 아무 숫자나 매칭)은 "선급금 30%" 같은
    # 무관한 문구를 오탐하는 사례를 발견해 제거 — 값이 없으면 None을 유지하고, 아래 739행
    # 근처의 DB 기반 재계산(contract_amount_krw/매출액)이 대신 처리하도록 함(정확도 우선).
    if result.get("correction_ratio_after") is not None:
        # 계약금액과 동일한 이유(정정전 값이 먼저 나오는 비교표 구조)로 정정후
        # 매출액대비 비율을 우선 채택.
        result["contract_ratio_pct"] = result["correction_ratio_after"]
    else:
        ratio_patterns = [
            r"최근\s*사업연도\s*매출액\s*대비\s*\(?%?\)?[:\s]*([0-9]+(?:\.[0-9]+)?)\s*%?",
            r"매출액\s*대비\s*\(?%?\)?[:\s]*([0-9]+(?:\.[0-9]+)?)\s*%?",
        ]
        for pat in ratio_patterns:
            m = re.search(pat, text)
            if m:
                result["contract_ratio_pct"] = float(m.group(1))
                break

    # 매출 기준값 (2026-07-21 수정: "최근 매출액(원)"처럼 "사업연도"가 없는 표기와
    # "최근매출액"(공백없음) 변형 추가, 단위도 라벨 직후 괄호 형태로 재작성)
    rev_patterns = [
        rf"최근\s*사업연도\s*매출액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
        rf"최근\s*매출액\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
        rf"최근\s*사업년도말[\s\S]{{0,80}}?매출액(?:은)?\s*\(?{UNIT_RE}?\)?[:\s]*{NUM_RE}",
    ]
    for pat in rev_patterns:
        m = re.search(pat, text)
        if m:
            # The optional unit appears before the value, so positional capture
            # groups can turn `원` into the numeric input. Use stable named groups.
            unit = (m.groupdict().get("unit") or "원").strip()
            raw_text = m.groupdict().get("num") or ""
            try:
                raw = float(raw_text.replace(",", ""))
            except (TypeError, ValueError):
                logger.warning("[DART수주] 매출 기준값 숫자 파싱 생략: %r", raw_text)
                continue
            result["revenue_base"] = raw * UNIT_MAP.get(unit, 1)
            break

    # 계약 상대방 추출 (다양한 표현 방식 처리)
    party_patterns = [
        r"계약\s*상대\s*(?:방)?[:\s]([가-힣A-Za-z\s\(\)]+?)(?:\s*[-\-]\s*회사와의|주식회사|Corp|Inc|Ltd|\s{3,}|\d)",
        r"3\.\s*계약상대\s+([가-힣A-Za-z\s\(\)\.]+?)(?:\s*\(|주식회사|SK\s|\s{2,})",
        r"납품\s*처[:\s]*([가-힣A-Za-z\s]+?)(?:\n|\r|,|\s{2,})",
        r"구매\s*자[:\s]*([가-힣A-Za-z\s]+?)(?:\n|\r|,|\s{2,})",
    ]
    for pat in party_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            party = m.group(1).strip().rstrip('-').strip()
            if 2 <= len(party) <= 40 and "계약" not in party:
                result["counterparty"] = party
                break

    # 해외 여부 판단
    overseas_signals = [
        "해외", "수출", "overseas", "export", "export",
        "USA", "US ", "미국", "EU", "유럽", "독일", "중국",
        "일본", "영국", "프랑스", "인도", "UAE", "사우디",
        "엔비디아", "구글", "마이크로소프트", "애플", "AMD",
        "삼성SDI", "SK하이닉스",  # 국내이지만 글로벌 매출
        "Inc", "Corp", "GmbH", "Ltd", "Co.,",
    ]
    text_lower = text[:3000].lower()
    for sig in overseas_signals:
        if sig.lower() in text_lower:
            result["is_overseas"] = True
            # 국가 추정
            countries = {
                "미국": ["usa", "us ", "미국", "america"],
                "유럽": ["eu", "유럽", "독일", "gmbh", "프랑스"],
                "중국": ["중국", "china"],
                "일본": ["일본", "japan"],
                "중동": ["uae", "사우디", "이라크", "중동"],
                "인도": ["인도", "india"],
                "글로벌": ["글로벌", "global"],
            }
            for country, signals in countries.items():
                if any(s in text_lower for s in signals):
                    result["counterparty_country"] = country
                    break
            if not result["counterparty_country"]:
                result["counterparty_country"] = "해외"
            break

    # 계약 기간 추출
    # 2026-07-21 수정: 실제 DART 표준양식 다수가 "계약기간 시작일 2026-07-20 종료일
    # 2031-12-31" 형태(시작일/종료일 라벨 분리)인데 기존 규칙은 "DATE~DATE" 인접 표기만
    # 가정해 거의 매칭되지 않았음 — 시작일/종료일 라벨 패턴을 우선 시도.
    date_patterns = [
        r"계약기간\s*시작일\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*종료일\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})",
        r"시작일\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*종료일\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})",
        r"계약기간[:\s]*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})\s*[~\-]\s*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})",
        r"납품기한[:\s]*(\d{4}[.\-]\d{1,2}[.\-]\d{1,2})",
    ]
    for pat in date_patterns:
        m = re.search(pat, text)
        if m:
            result["contract_start"] = m.group(1)
            if m.lastindex >= 2:
                result["contract_end"] = m.group(2)
            break

    return result


# ══════════════════════════════════════════════
#  AI 분석
# ══════════════════════════════════════════════

def _ai_analyze_contract(stock_name: str, report_nm: str, parsed: dict,
                          raw_text: str) -> dict:
    """OpenAI로 공시 내용 분석 → 매수 시그널 평가."""
    try:
        import os
        if not is_configured():
            return _rule_based_signal(parsed)

        ratio_str = (f"{parsed['contract_ratio_pct']:.1f}%"
                     if parsed.get("contract_ratio_pct") else "미확인")
        overseas_str = "해외" if parsed.get("is_overseas") else "국내"

        prompt = f"""당신은 한국 주식시장 전문 애널리스트입니다.
아래 DART 공시를 분석하여 주가 영향과 매수 시그널을 평가해주세요.

【공시 기본 정보】
- 기업명: {stock_name}
- 공시 제목: {report_nm}
- 계약금액: {parsed.get('contract_amount', '미확인')} {parsed.get('contract_unit', '')}
- 매출액 대비: {ratio_str}
- 계약 상대방: {parsed.get('counterparty', '미확인')}
- 해외/국내: {overseas_str}
- 계약 유형: {parsed.get('contract_type', '미확인')}

【공시 원문 (요약)】
{raw_text[:2000]}

다음 형식으로 JSON만 응답하세요:
{{
  "score": 85,
  "signal": "강한매수",
  "summary": "2줄 이내 핵심 요약",
  "reason": "매수 근거 2~3가지",
  "risk": "주요 리스크 1가지",
  "strategy_match": ["V8", "V11"],
  "holding_period": "3~6개월"
}}

signal 값: 강한매수/매수/관망/주의
score: 0~100 (80+ = 강한매수, 60~79 = 매수, 40~59 = 관망, 40미만 = 주의)
strategy_match: 해당되는 전략만 포함 (V8=수출선행, V11=흑자전환, V10=이익폭발, V4=AI콤보)"""

        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400,
        )
        content = resp.choices[0].message.content.strip()
        # JSON 추출
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            result = json.loads(m.group())
            return result
    except Exception as e:
        logger.warning(f"[DART수주] AI 분석 실패: {e}")

    return _rule_based_signal(parsed)


def _rule_based_signal(parsed: dict) -> dict:
    """AI 없을 때 규칙 기반 시그널."""
    ratio = parsed.get("contract_ratio_pct") or 0
    is_overseas = parsed.get("is_overseas", False)
    ctype = parsed.get("contract_type", "")

    score = 30  # 기본
    if ratio >= 30:
        score += 50
    elif ratio >= 20:
        score += 40
    elif ratio >= 10:
        score += 25
    elif ratio >= 5:
        score += 15

    if is_overseas:
        score += 15
    if "기술이전" in ctype or "라이선스" in ctype:
        score += 20
    if "방산" in ctype:
        score += 10

    score = min(100, score)

    strategies = []
    if is_overseas:
        strategies.append("V8")
    if ratio >= 10:
        strategies.append("V10")
    if "기술이전" in ctype:
        strategies.append("V11")
    strategies.append("V4")

    if score >= 80:
        signal = "강한매수"
    elif score >= 60:
        signal = "매수"
    elif score >= 40:
        signal = "관망"
    else:
        signal = "주의"

    reason_parts = []
    if ratio >= 10:
        reason_parts.append(f"매출액 대비 {ratio:.0f}% 대형 계약")
    if is_overseas:
        reason_parts.append("해외 계약 → 수출 증가 선행 신호")
    if "기술이전" in ctype:
        reason_parts.append("기술수출 → 고마진 로열티 수익")

    return {
        "score": score,
        "signal": signal,
        "summary": f"{ctype} 공시. 매출비중 {ratio:.0f}%." if ratio else f"{ctype} 공시.",
        "reason": " / ".join(reason_parts) or "계약 공시 발생",
        "risk": "계약 이행 지연 또는 취소 가능성",
        "strategy_match": strategies,
        "holding_period": "3~6개월",
    }


def _calc_signal_strength(parsed: dict, ai_score: int) -> int:
    """★1~★5 시그널 강도 계산."""
    r = {
        "ratio": parsed.get("contract_ratio_pct") or 0,
        "is_overseas": parsed.get("is_overseas", False),
        "ai_score": ai_score,
    }
    for stars in [5, 4, 3, 2, 1]:
        if SIGNAL_RULES[stars](r):
            return stars
    return 1


# ══════════════════════════════════════════════
#  DB 저장
# ══════════════════════════════════════════════

def _stock_code_from_dart(corp_code: str, corp_name: str) -> Optional[str]:
    """DART corp_code → 종목코드 매핑 (stock_universe에서 조회)."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        # 기업명으로 먼저 검색
        row = conn.execute(
            "SELECT stock_code FROM stock_universe WHERE stock_name=? LIMIT 1",
            (corp_name,)
        ).fetchone()
        if row:
            return row[0]
        # 유사 검색 (앞 4자)
        if len(corp_name) >= 4:
            row = conn.execute(
                "SELECT stock_code FROM stock_universe WHERE stock_name LIKE ? LIMIT 1",
                (corp_name[:4] + "%",)
            ).fetchone()
            if row:
                return row[0]
    finally:
        conn.close()
    return None


def _ensure_correction_columns(conn) -> None:
    """dart_contracts에 정정공시 매칭용 컬럼이 없으면 추가한다."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(dart_contracts)").fetchall()}
    for col, typ in {
        "is_correction": "INTEGER DEFAULT 0",
        "corrects_rcept_no": "TEXT",
        "corrected_by_rcept_no": "TEXT",
    }.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE dart_contracts ADD COLUMN {col} {typ}")
    conn.commit()


def _find_original_contract(conn, stock_code: str, corrects_disclosed_at: str) -> Optional[str]:
    """정정공시가 가리키는 원 공시(rcept_no)를 stock_code+공시일로 찾는다.
    disclosed_at은 DART list.json 원본 그대로(YYYYMMDD)라 대시 포맷과 둘 다 비교한다."""
    if not stock_code or not corrects_disclosed_at:
        return None
    compact = corrects_disclosed_at.replace("-", "")
    row = conn.execute(
        """
        SELECT rcept_no FROM dart_contracts
        WHERE stock_code=? AND COALESCE(is_correction,0)=0
          AND (disclosed_at=? OR disclosed_at=? OR substr(disclosed_at,1,10)=?)
        ORDER BY rcept_no DESC LIMIT 1
        """,
        (stock_code, compact, corrects_disclosed_at, corrects_disclosed_at),
    ).fetchone()
    return row[0] if row else None


def _apply_correction_to_original(conn, original_rcept_no: str, correction_rcept_no: str, parsed: dict) -> None:
    """정정후 값을 원 공시 행에 반영(supersede)하고 상호참조를 남긴다."""
    conn.execute(
        """
        UPDATE dart_contracts SET
            contract_amount=?, contract_unit=?, contract_amount_krw=?,
            contract_end=?, corrected_by_rcept_no=?
        WHERE rcept_no=?
        """,
        (
            parsed.get("contract_amount"), parsed.get("contract_unit"), parsed.get("contract_amount_krw"),
            parsed.get("contract_end"), correction_rcept_no, original_rcept_no,
        ),
    )
    conn.commit()


def _save_contract(data: dict, max_retries: int = 6):
    """dart_contracts DB 저장. DB 잠금 시 지수 백오프 재시도."""
    columns = (
        "rcept_no", "stock_code", "stock_name", "disclosed_at", "report_nm",
        "contract_amount", "contract_unit", "contract_amount_krw",
        "revenue_base", "contract_ratio_pct", "counterparty",
        "counterparty_country", "is_overseas", "contract_start",
        "contract_end", "contract_type", "ai_score", "ai_summary",
        "ai_signal", "signal_strength", "raw_text", "created_at",
        "is_correction", "corrects_rcept_no",
    )
    values = tuple(data.get(column) for column in columns)
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.execute("PRAGMA busy_timeout = 60000")  # 60초
            try:
                _ensure_correction_columns(conn)
                conn.execute("""
                    INSERT OR IGNORE INTO dart_contracts
                    (rcept_no, stock_code, stock_name, disclosed_at, report_nm,
                     contract_amount, contract_unit, contract_amount_krw,
                     revenue_base, contract_ratio_pct,
                     counterparty, counterparty_country, is_overseas,
                     contract_start, contract_end, contract_type,
                     ai_score, ai_summary, ai_signal, signal_strength,
                     raw_text, created_at, is_correction, corrects_rcept_no)
                    VALUES
                    (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, values)
                conn.commit()
                # 정정공시면 원 공시 행을 정정후 값으로 갱신(supersede)한다.
                # 2026-09 신규: 이전에는 "정정" 자체를 EXCLUDE_KEYWORDS로 걸러 정정된
                # 값(계약금액 축소/기간연장 등)이 화면에 전혀 반영되지 않았음.
                if data.get("is_correction") and data.get("corrects_rcept_no"):
                    _apply_correction_to_original(
                        conn, data["corrects_rcept_no"], data["rcept_no"], data
                    )
                return  # 성공
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1, 2, 4, 8, 16, 32초
                logger.warning(f"[DART수주] DB 잠금, {wait}초 후 재시도 ({attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                logger.error(f"[DART수주] DB 저장 실패: {e}")
                raise


# ══════════════════════════════════════════════
#  텔레그램 알림
# ══════════════════════════════════════════════

def _send_telegram_alert(contract: dict):
    """매수 시그널 텔레그램 발송."""
    if not DART_CONTRACT_TELEGRAM_ENABLED:
        logger.info("[DART수주] 텔레그램 발송 비활성화 — 스킵")
        return False

    strength = contract.get("signal_strength", 0)
    stars = "★" * strength + "☆" * (5 - strength)

    signal = contract.get("ai_signal", "관망")
    signal_emoji = {
        "강한매수": "🚀",
        "매수": "📈",
        "관망": "👀",
        "주의": "⚠️",
    }.get(signal, "📌")

    def _safe_text(v, default="미확인"):
        if v is None:
            return default
        s = str(v).strip()
        if not s or s.lower() == "none":
            return default
        return s

    def _safe_num(v, default="-"):
        try:
            if v is None:
                return default
            return f"{float(v):,.0f}".rstrip("0").rstrip(".")
        except Exception:
            return default

    overseas_tag = "🌏 해외계약" if contract.get("is_overseas") else "🏠 국내계약"
    ratio_val = contract.get("contract_ratio_pct")
    ratio_str = (f"{float(ratio_val):.1f}%" if ratio_val is not None else "미확인")

    strategies = contract.get("strategy_match_json", "")
    try:
        strats = json.loads(strategies) if strategies else []
    except Exception:
        strats = []
    strat_str = " ".join(f"[{s}]" for s in strats) if strats else ""

    msg = f"""{signal_emoji} <b>DART 수주공시 알림</b> {stars}

📌 <b>{_safe_text(contract.get('stock_name'), '종목미확인')}</b> ({_safe_text(contract.get('stock_code'), '코드미확인')})
📄 {_safe_text(contract.get('report_nm'), '공시제목 미확인')}

💰 계약금액: {_safe_num(contract.get('contract_amount'))} {_safe_text(contract.get('contract_unit'), '').strip()}
📊 매출액 대비: {ratio_str}
🤝 계약상대방: {_safe_text(contract.get('counterparty'), '미확인')}
{overseas_tag}

🧠 AI 판단: <b>{signal}</b> (스코어 {contract.get('ai_score', 0)})
📝 {_safe_text(contract.get('ai_summary'), '-')}
💡 근거: {_safe_text(contract.get('ai_reason'), '-')}
⚠️ 리스크: {_safe_text(contract.get('ai_risk'), '-')}

{strat_str}
🕐 공시일: {_safe_text(contract.get('disclosed_at'), '')}"""

    try:
        from notifier import send as _notify
        dedup_key = f"dart_contract_{contract.get('rcept_no','')}"
        sent = _notify(msg, key=dedup_key)
        if sent:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.execute(
                "UPDATE dart_contracts SET telegram_sent=1 WHERE rcept_no=?",
                (contract["rcept_no"],)
            )
            conn.commit()
            conn.close()
            return True
        logger.info(f"[DART수주] 중복/실패로 전송 스킵: {dedup_key}")
    except Exception as e:
        logger.error(f"[DART수주] 텔레그램 발송 실패: {e}")
    return False


# ══════════════════════════════════════════════
#  메인 수집 함수
# ══════════════════════════════════════════════

def collect_dart_contracts(days: int = 1, min_signal: int = 2,
                           skip_ai: bool = False,
                           bgn_str: str = "", end_str: str = "") -> list[dict]:
    """
    DART 수주·공급계약 공시 수집 + AI 분석.

    Args:
        days: 최근 N일 공시 수집 (기본 1일) — bgn_str/end_str 지정 시 무시
        min_signal: 텔레그램 발송 최소 시그널 강도 (기본 ★★)
        skip_ai: True면 AI 분석 생략 → 규칙기반만 (백필 고속 모드)
        bgn_str/end_str: 직접 날짜 지정 (YYYYMMDD) — 청크 백필 시 사용

    Returns:
        처리된 계약 목록
    """
    end_dt = date.today()
    bgn_dt = end_dt - timedelta(days=days)
    if not bgn_str:
        bgn_str = bgn_dt.strftime("%Y%m%d")
    if not end_str:
        end_str = end_dt.strftime("%Y%m%d")

    if not DART_CONTRACT_AI_ENABLED:
        skip_ai = True
    logger.info(
        f"[DART수주] {bgn_str}~{end_str} 공시 수집 시작 "
        f"(AI={'ON' if not skip_ai else 'OFF'}, 텔레그램={'ON' if DART_CONTRACT_TELEGRAM_ENABLED else 'OFF'})"
    )
    items = _fetch_dart_list(bgn_str, end_str)
    logger.info(f"[DART수주] 전체 주요사항보고서 {len(items)}건")

    # 이미 처리된 rcept_no 확인
    conn = sqlite3.connect(DB_PATH, timeout=30)
    existing = set(row[0] for row in conn.execute(
        "SELECT rcept_no FROM dart_contracts"
    ).fetchall())
    conn.close()

    results = []
    for item in items:
        report_nm = item.get("report_nm", "")
        if not _is_contract_report(report_nm):
            continue

        rcept_no = item.get("rcept_no", "")
        if rcept_no in existing:
            continue

        corp_name = item.get("corp_name", "")
        stock_code = item.get("stock_code") or _stock_code_from_dart(
            item.get("corp_code", ""), corp_name
        )
        disclosed_at = item.get("rcept_dt", "")

        logger.info(f"[DART수주] 처리: {corp_name} / {report_nm}")

        # 공시 원문 수집
        raw_text = _fetch_dart_document(rcept_no)
        time.sleep(0.3)  # API rate limit

        # 금액/비율 파싱
        parsed = _extract_amounts(raw_text)
        parsed["contract_type"] = _classify_contract_type(raw_text, report_nm)

        # 매출액 대비 비율 계산 (DB에서 보완)
        # 2026-09 수정: 기존엔 CFS/OFS·quarter·data_source tiebreak가 전혀 없어
        # 동일 종목이라도 요청마다 다른 report_type 매출이 임의로 선택될 수
        # 있었음 — routes/order_contracts.py의 검증된 tiebreak 순서(CFS 우선 >
        # quarter=4/0 우선 > DART 소스 우선 > id DESC) 재사용.
        if not parsed["contract_ratio_pct"] and parsed.get("contract_amount_krw") and stock_code:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            row = conn.execute("""
                SELECT revenue FROM financial_data
                WHERE stock_code=? AND is_annual=1 AND revenue IS NOT NULL AND revenue > 0
                ORDER BY year DESC,
                         (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),
                         (CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END),
                         (CASE WHEN data_source='dart' THEN 0 ELSE 1 END),
                         id DESC
                LIMIT 1
            """, (stock_code,)).fetchone()
            conn.close()
            if row and row[0] and row[0] > 0:
                parsed["contract_ratio_pct"] = (
                    parsed["contract_amount_krw"] / row[0] * 100
                )
                parsed["revenue_base"] = row[0]

        # AI 분석 (skip_ai=True면 규칙기반만 사용)
        if skip_ai:
            ai_result = _rule_based_signal(parsed)
        else:
            ai_result = _ai_analyze_contract(corp_name, report_nm, parsed, raw_text)
        ai_score = ai_result.get("score", 30)
        signal_strength = _calc_signal_strength(parsed, ai_score)

        # 정정공시("[기재정정]...")면 원 공시 rcept_no를 찾아 상호참조를 남긴다.
        # 못 찾으면(원 공시가 아직 미수집 등) 신규 행으로 저장(감사기록 목적).
        corrects_rcept_no = None
        if parsed.get("is_correction") and stock_code:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                _ensure_correction_columns(conn)
                corrects_rcept_no = _find_original_contract(
                    conn, stock_code, parsed.get("corrects_disclosed_at") or ""
                )
            finally:
                conn.close()

        # 저장
        record = {
            "rcept_no": rcept_no,
            "stock_code": stock_code,
            "stock_name": corp_name,
            "disclosed_at": disclosed_at,
            "report_nm": report_nm,
            "contract_amount": parsed.get("contract_amount"),
            "contract_unit": parsed.get("contract_unit"),
            "contract_amount_krw": parsed.get("contract_amount_krw"),
            "revenue_base": parsed.get("revenue_base"),
            "contract_ratio_pct": parsed.get("contract_ratio_pct"),
            "counterparty": parsed.get("counterparty"),
            "counterparty_country": parsed.get("counterparty_country"),
            "is_overseas": 1 if parsed.get("is_overseas") else 0,
            "contract_start": parsed.get("contract_start"),
            "contract_end": parsed.get("contract_end"),
            "contract_type": parsed.get("contract_type"),
            "ai_score": ai_score,
            "ai_summary": ai_result.get("summary", ""),
            "ai_signal": ai_result.get("signal", "관망"),
            "signal_strength": signal_strength,
            "raw_text": raw_text[:4000],
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_correction": 1 if parsed.get("is_correction") else 0,
            "corrects_rcept_no": corrects_rcept_no,
        }
        _save_contract(record)

        # 텔레그램 발송 (시그널 강도 기준 이상)
        if signal_strength >= min_signal:
            full_record = dict(record)
            full_record["strategy_match_json"] = json.dumps(
                ai_result.get("strategy_match", []), ensure_ascii=False
            )
            full_record["ai_reason"] = ai_result.get("reason", "")
            full_record["ai_risk"] = ai_result.get("risk", "")
            _send_telegram_alert(full_record)

        record["ai_result"] = ai_result
        results.append(record)
        logger.info(
            f"[DART수주] 완료: {corp_name} / 시그널 ★{signal_strength} / "
            f"AI {ai_result.get('signal')} ({ai_score}점)"
        )

    logger.info(f"[DART수주] 총 {len(results)}건 처리 완료")
    return results


def collect_dart_contracts_catchup(
    *,
    max_backfill_days: int = 14,
    min_signal: int = 2,
    skip_ai: bool = False,
) -> dict:
    """Backfill recent gaps from the latest saved row up to today."""
    end_dt = date.today()
    fallback_start = end_dt - timedelta(days=max_backfill_days)

    latest_raw = None
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        row = conn.execute("SELECT MAX(disclosed_at) FROM dart_contracts").fetchone()
        latest_raw = row[0] if row else None
    finally:
        conn.close()

    start_dt = fallback_start
    if latest_raw:
        try:
            latest_dt = datetime.strptime(str(latest_raw), "%Y%m%d").date()
            start_dt = max(fallback_start, latest_dt + timedelta(days=1))
        except ValueError:
            logger.warning(f"[DART수주] latest disclosed_at 파싱 실패: {latest_raw}")

    if start_dt > end_dt:
        start_dt = end_dt

    bgn_str = start_dt.strftime("%Y%m%d")
    end_str = end_dt.strftime("%Y%m%d")
    results: list[dict] = []
    cursor = start_dt
    while cursor <= end_dt:
        day_str = cursor.strftime("%Y%m%d")
        results.extend(
            collect_dart_contracts(
                min_signal=min_signal,
                skip_ai=skip_ai,
                bgn_str=day_str,
                end_str=day_str,
            )
        )
        cursor += timedelta(days=1)
    return {
        "start_date": bgn_str,
        "end_date": end_str,
        "saved_count": len(results),
    }


def backfill_dart_contracts(days: int = 30, skip_ai: bool = True,
                            chunk_days: int = 30) -> int:
    """
    과거 N일치 수주공시 청크 단위 백필.

    Args:
        days: 총 백필 기간 (일)
        skip_ai: True(기본) = 규칙기반만, 빠르게 수집
        chunk_days: 한 번에 조회할 기간 (30일 단위 권장)

    Returns:
        저장된 총 건수
    """
    logger.info(f"[DART수주 백필] 최근 {days}일치 수집 시작 (AI={'ON' if not skip_ai else 'OFF'}, {chunk_days}일 청크)")
    total = 0
    end_dt = date.today()

    # 과거 → 현재 방향으로 청크 순회
    chunk_starts = []
    cur = end_dt - timedelta(days=days)
    while cur < end_dt:
        chunk_starts.append(cur)
        cur += timedelta(days=chunk_days)

    for i, chunk_bgn in enumerate(chunk_starts):
        chunk_end = min(chunk_bgn + timedelta(days=chunk_days - 1), end_dt)
        bgn_str = chunk_bgn.strftime("%Y%m%d")
        end_str = chunk_end.strftime("%Y%m%d")
        logger.info(f"[DART수주 백필] 청크 {i+1}/{len(chunk_starts)}: {bgn_str}~{end_str}")
        # DB 잠금 시 최대 3회 재시도
        for retry in range(3):
            try:
                results = collect_dart_contracts(
                    min_signal=99,       # 텔레그램 발송 없이
                    skip_ai=skip_ai,
                    bgn_str=bgn_str,
                    end_str=end_str,
                )
                total += len(results)
                logger.info(f"[DART수주 백필] 청크 {i+1} 완료: {len(results)}건 (누적 {total}건)")
                break  # 성공
            except Exception as e:
                if retry < 2:
                    wait = 30 * (retry + 1)  # 30초, 60초
                    logger.warning(f"[DART수주 백필] 청크 {i+1} 오류({retry+1}/3): {e} — {wait}초 후 재시도")
                    time.sleep(wait)
                else:
                    logger.error(f"[DART수주 백필] 청크 {i+1} 최종 실패: {e}")
        time.sleep(2)  # 청크 간 대기

    logger.info(f"[DART수주 백필] 전체 완료: {total}건 저장")
    return total


# ══════════════════════════════════════════════
#  V8/V11 전략 연계 함수
# ══════════════════════════════════════════════

def get_recent_contract_signals(days: int = 30) -> dict[str, dict]:
    """
    최근 N일 수주공시 매수 시그널 반환.
    backtest.py / signal_engine.py에서 호출.

    Returns:
        {stock_code: {"signal_strength": 4, "is_overseas": True, "ratio": 25.0, ...}}
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    rows = conn.execute("""
        SELECT stock_code, MAX(signal_strength) as max_sig,
               MAX(is_overseas) as overseas,
               MAX(contract_ratio_pct) as max_ratio,
               MAX(ai_score) as max_ai,
               GROUP_CONCAT(ai_signal, ',') as signals
        FROM dart_contracts
        WHERE stock_code IS NOT NULL
          AND stock_code != ''
          AND disclosed_at >= ?
          AND signal_strength >= 2
        GROUP BY stock_code
    """, (cutoff,)).fetchall()
    conn.close()

    result = {}
    for row in rows:
        sc, sig, overseas, ratio, ai_sc, sigs = row
        result[sc] = {
            "signal_strength": sig or 1,
            "is_overseas": bool(overseas),
            "ratio": ratio or 0,
            "ai_score": ai_sc or 0,
            "has_contract_signal": True,
        }
    return result


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--backfill", type=int, default=0,
                        help="N일치 백필 (0=최근 1일만)")
    parser.add_argument("--min-signal", type=int, default=2)
    args = parser.parse_args()

    if args.backfill > 0:
        n = backfill_dart_contracts(days=args.backfill)
        print(f"백필 완료: {n}건")
    else:
        results = collect_dart_contracts(days=args.days, min_signal=args.min_signal)
        print(f"수집 완료: {len(results)}건")
        for r in results:
            ai = r.get("ai_result", {})
            print(f"  [{r['stock_name']}] ★{r['signal_strength']} "
                  f"{ai.get('signal','?')} ({ai.get('score',0)}점) "
                  f"— {ai.get('summary','')}")
