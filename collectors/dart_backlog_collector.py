"""
collectors/dart_backlog_collector.py

정기보고서(사업/반기/분기) 원문에서 수주잔고(Backlog) 관련 값을 추출해
분기 단위 DB로 적재한다.

설계 원칙
- 별도 DB를 만들지 않고 stock.db 내 전용 테이블로 분리 저장
  (운영/백업/트랜잭션 관리 단일화 + 조인 용이)
- 원문 근거(rcept_no/report_nm/rcept_dt)와 parser_version 보존
- 동일 종목/연도/분기 중 최신 공시만 유효(UPSERT)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_utils import connect_stock_db
import config
from dart_key_manager import get_dart_api_keys

logger = logging.getLogger(__name__)

PARSER_VERSION = "backlog_v3"
MIN_OPERATIONAL_CONFIDENCE = 0.95

# dart_contract_collector의 검증된 문서 fetch 로직 재사용
import collectors.dart_contract_collector as _dcc
import io, zipfile, requests as _requests

# 수주잔고는 문서 깊숙한 곳에 위치 → 8000자 제한 해제, 최대 300000자 사용
_DOC_MAX_CHARS = 300_000


def _decode_dart_bytes(raw: bytes) -> str:
    """DART ZIP 내부 문서는 과거 공시일수록 cp949/euc-kr인 경우가 많다.
    utf-8(errors=ignore)는 예외 없이 깨진 한글을 반환할 수 있으므로,
    한국어 키워드 점수가 가장 높은 디코딩을 선택한다."""
    best = ""
    best_score = -1
    for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            text = raw.decode(enc, errors="ignore")
        score = sum(text.count(k) for k in ("주요사항보고서", "전환사채", "유상증자", "발행금액", "권면", "수주", "계약"))
        score += sum(1 for ch in text[:4000] if "가" <= ch <= "힣") // 20
        if score > best_score:
            best = text
            best_score = score
    return best


def _fetch_document_full(rcept_no: str, api_key: str) -> str:
    """DART 원문 전체를 최대 _DOC_MAX_CHARS 자까지 가져온다 (ZIP 처리 포함)."""
    import re as _re
    url = "https://opendart.fss.or.kr/api/document.xml"
    try:
        resp = _requests.get(url, params={"crtfc_key": api_key, "rcept_no": rcept_no},
                             timeout=30)
        if resp.status_code != 200:
            return ""
        raw = resp.content
        # 한도 초과 체크 (JSON 오류 응답)
        if raw[:1] == b"{":
            err_text = raw.decode("utf-8", errors="ignore")
            if "020" in err_text or "사용한도" in err_text or "한도" in err_text:
                return "020"
            return ""
        # ZIP 형식 (PK 헤더)
        if raw[:2] == b"PK":
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                texts = []
                for name in sorted(zf.namelist()):
                    with zf.open(name) as f:
                        content = _decode_dart_bytes(f.read())
                        content = _re.sub(r"<[^>]+>", " ", content)
                        content = _re.sub(r"&[a-zA-Z#0-9]+;", " ", content)
                        content = _re.sub(r"\s+", " ", content).strip()
                        texts.append(content)
                return " ".join(texts)[:_DOC_MAX_CHARS]
            except Exception as e:
                logger.warning("[Backlog] ZIP 해제 실패 %s: %s", rcept_no, e)
                return ""
        # XML/HTML 직접 처리
        text = _decode_dart_bytes(raw)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        if text.startswith("020") or "사용한도" in text[:80]:
            return "020"
        return text[:_DOC_MAX_CHARS]
    except Exception as e:
        logger.warning("[Backlog] 문서 fetch 실패 %s: %s", rcept_no, e)
        return ""


def _fetch_document_with_key_rotation(rcept_no: str) -> str:
    """3-key 라운드로빈으로 전체 문서(최대 300000자) 취득."""
    keys = get_dart_api_keys()
    tried = set()
    for k in keys:
        if not k or k in tried:
            continue
        tried.add(k)
        txt = _fetch_document_full(rcept_no, k)
        if txt == "020" or txt.startswith("020") or "사용한도" in txt[:80]:
            continue  # 한도 초과 → 다음 키
        if txt:
            return txt
    return ""


@dataclass
class BacklogMetric:
    backlog_amount: Optional[float] = None
    backlog_unit: Optional[str] = None
    backlog_amount_krw: Optional[float] = None
    backlog_confidence: float = 0.0
    source_excerpt: str = ""


def _ensure_table() -> None:
    conn = connect_stock_db(timeout=60)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_backlog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                year INTEGER NOT NULL,
                quarter INTEGER NOT NULL,
                report_type TEXT,
                rcept_no TEXT,
                backlog_amount REAL,
                backlog_unit TEXT DEFAULT '원',
                backlog_normalized REAL,
                new_orders REAL,
                revenue_base REAL,
                backlog_to_rev REAL,
                data_source TEXT DEFAULT 'dart_backlog',
                collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, year, quarter)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_backlog_code ON order_backlog(stock_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_backlog_year ON order_backlog(year, quarter)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_backlog_quarterly (
                stock_code TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'CFS',
                backlog_amount REAL,
                backlog_unit TEXT,
                backlog_amount_krw REAL,
                backlog_confidence REAL DEFAULT 0,
                source_excerpt TEXT,
                source_rcept_no TEXT,
                source_report_nm TEXT,
                source_rcept_dt TEXT,
                source_text_hash TEXT,
                parser_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, report_type)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dart_backlog_src ON dart_backlog_quarterly(source_rcept_no)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dart_tenbagger_triggers_quarterly (
                stock_code TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                fiscal_quarter INTEGER NOT NULL,
                report_type TEXT NOT NULL DEFAULT 'CFS',
                metric_name TEXT NOT NULL,
                metric_value REAL,
                yoy_pct REAL,
                qoq_pct REAL,
                trigger_level TEXT,
                source_table TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, report_type, metric_name)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _korean_to_krw(value: float, unit: str) -> float:
    u = (unit or "원").replace(" ", "").strip()
    if u == "조":
        u = "조원"
    elif u == "억":
        u = "억원"
    if u == "조원":
        return value * 1_0000_0000_0000
    if u == "억원":
        return value * 100_000_000
    if u in {"백만원", "백만"}:
        return value * 1_000_000
    if u == "천만원":
        return value * 10_000_000
    if u == "천원":  # 2026-07-19 수정: "천원" 단위가 통째로 누락돼 있어 (단위:천원) 표는 1000배
        return value * 1_000       # 축소된 잘못된 값으로 저장되고 있었음(유진테크 084370 등 다수 발견).
    if u == "만원":
        return value * 10_000
    return value


def _parse_num(s: str) -> Optional[float]:
    if s is None:
        return None
    t = str(s).replace(",", "").replace(" ", "").strip()
    if not t:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


_UNIT_TAG_PAT = r"\(\s*단위\s*[:：][^)]{0,40}?(조원|억원|백만원|백만|천만원|천원|만원|원)[^)]{0,20}\)"


def _canonical_unit(unit: str) -> str:
    return "백만원" if unit == "백만" else unit


def _has_explicit_unit_nearby(t: str, pos: int) -> bool:
    """Return whether a real currency unit declaration exists near the table."""
    start = max(0, pos - 1000)
    return bool(
        re.search(_UNIT_TAG_PAT, t[start:pos])
        or re.search(_UNIT_TAG_PAT, t[pos:pos + 250])
    )


def _find_unit_nearby(t: str, pos: int) -> str:
    """숫자/키워드 주변에서 '(단위 : XXX)' 선언을 찾는다.
    2026-07-19 신규: HD한국조선해양(009540)/삼성중공업(010140) 등 실측 중 발견 — 표 헤더에
    단위가 "(단위 : 백만원)"으로 한 번만 선언되고 그 아래 데이터 행 숫자들에는 개별 단위가
    안 붙는 표 형식에서, 기존 코드가 숫자 바로 뒤 단위표기만 찾고 없으면 무조건 "원"으로
    묵인 처리해 89조원이 0.76억원으로(100만분의 1) 축소 저장되는 심각한 과소평가 버그가 있었음.
    표 헤더는 데이터보다 앞서 나오므로 역방향(backward) 탐색을 우선하고, 없으면 순방향도 확인."""
    start = max(0, pos - 1000)
    candidates = []
    for match in re.finditer(_UNIT_TAG_PAT, t[start:pos]):
        absolute_end = start + match.end()
        candidates.append((pos - absolute_end, _canonical_unit(match.group(1))))
    for match in re.finditer(_UNIT_TAG_PAT, t[pos:pos + 250]):
        candidates.append((match.start(), _canonical_unit(match.group(1))))
    if candidates:
        return min(candidates, key=lambda item: item[0])[1]
    return "원"


_CUR_PERIOD_PAT = r"당\s*분기|당\s*반기|당\s*기말|당기말|당분기|당반기|당해"
_PRIOR_PERIOD_PAT = r"전\s*분기|전\s*반기|전\s*기말|전기말|전분기|전반기|직전"


def _period_tier(t: str, pos: int) -> int:
    """가까운 과거/현재 구분 마커를 확인해 전분기(과거) 표를 낮은 우선순위로 둔다.
    2026-07-19 신규: 삼성중공업(010140) 실측 중 발견 — "당분기"/"전분기" 표가 나란히(비교표
    형식) 공시되는데, 기존 로직이 이를 구분하지 않아 전분기(과거) 세그먼트 값을 그대로 채택하는
    버그 확인(전분기 조선해양 세그먼트 기초값 30.84조원을 채택 — 실제로는 당분기 부문합계
    29.52조원이 정답). 후보 위치 바로 앞 400자 내 가장 가까운 마커로 판정."""
    window = t[max(0, pos - 400):pos]
    cur_matches = list(re.finditer(_CUR_PERIOD_PAT, window))
    prior_matches = list(re.finditer(_PRIOR_PERIOD_PAT, window))
    cur_last = cur_matches[-1].end() if cur_matches else -1
    prior_last = prior_matches[-1].end() if prior_matches else -1
    if cur_last < 0 and prior_last < 0:
        return 1  # 마커 없음(중립)
    return 2 if cur_last > prior_last else 0


def _has_total_marker(t: str, pos: int) -> bool:
    """후보 위치 바로 앞 40자 내 '합계' 라벨이 있으면 세그먼트가 아닌 총계 행으로 간주."""
    return "합계" in t[max(0, pos - 40):pos]


def _bounded_table_window(t: str, start: int, limit: int = 2500) -> str:
    """Stop a flattened DART table before the next section or business table."""
    window = t[start:start + limit]
    boundaries = [
        r"\s\[[^\]]{1,60}(?:부문|내용)[^\]]*\]",
        r"\s\d{1,2}\.\s*(?:\([^)]{1,40}\)|[가-힣]{2,})",
        r"\s[가-힣]\s*\.\s*[가-힣]{2,}",
        r"\s\(\s*단위\s*[:：]",
    ]
    hits = [m.start() for pattern in boundaries for m in re.finditer(pattern, window)]
    return window[:min(hits)] if hits else window


def _is_derivative_context(t: str, pos: int) -> bool:
    """"파생상품 계약잔액"(헤지 거래 명목금액, 수주잔고와 무관)이 키워드 "계약잔액"과
    글자 그대로 겹쳐 오탐되는 것을 방지. 2026-07-19 신규: SK오션플랜트(100090) 실측 중
    발견 — 수주잔고와 전혀 무관한 파생상품 계약잔액(통화선도 등)이 후보로 섞여 들어감."""
    context = t[max(0, pos - 500):pos + 220]
    return bool(re.search(
        r"파생상품|통화선도|선물환|통화옵션|이자율\s*스왑|위험회피|헤지|"
        r"(?:약정환율|계약환율).{0,100}(?:매도|매입|포지션)",
        context,
        re.IGNORECASE,
    ))


def _is_false_contract_balance_context(t: str, pos: int, matched_text: str) -> bool:
    """Reject generic contract balances that are not order backlog."""
    if not re.search(r"계약\s*잔액", matched_text, re.IGNORECASE):
        return False
    context = t[max(0, pos - 260):pos + 320]
    if re.search(r"수주|공사|도급|용역|건설|신규계약|수익인식|기초.*기말", context):
        return False
    return bool(re.search(
        r"매출채권|계약자산|계약부채|선수금|금융상품|차입금|사채|리스|보증|담보",
        context,
    ))


def _is_footnote_marker(t: str, start: int, end: int) -> bool:
    """캡처된 숫자가 각주/목록 참조번호인지 판별(진짜 수치가 아님) — 2026-08-12
    003030(세아제강) 20260515001160 실측으로 발견: "수주잔고 금액 금액 금액 (주)세아제강
    주1) 카타르..." 구조에서 키워드 직후 60자 forward window 안의 첫 숫자가 "주1)"의
    "1"이라 1×백만원=100만원이라는 터무니없이 작은 값이 진짜 합계(516,454백만원, 표
    맨 아래 "합 계" 행)보다 먼저 채택되던 버그. 전종목 스캔 결과 66개 종목 284건이
    정확히 1,000,000원(=1×백만원)으로 저장되어 있어 동일 패턴이 광범위했음을 확인.
    2026-08-12(2차) 010420 실측으로 "(*1)"(괄호+별표) 변형도 추가 확인해 패턴 확장.
    작은 정수(1~2자리, 콤마/소수점 없음)만 대상으로 제한해 "12,345)"처럼 괄호 안에
    진짜 큰 숫자가 오는 정상적인 음수 표기(-1,234) 등과 혼동하지 않도록 방어."""
    raw = t[start:end]
    if "," in raw or "." in raw or len(raw.lstrip("-")) > 2:
        return False  # 콤마 포함 큰 수·소수는 각주번호일 수 없음(진짜 금액으로 간주)
    before = t[max(0, start - 3):start]
    after = t[end:end + 2]
    if not after.startswith(")"):
        return False
    return before.endswith("주") or before.endswith("(*") or before.endswith("*") or before.endswith("(") or before.endswith("[")


def _extract_backlog(text: str) -> BacklogMetric:
    t = _normalize_ws(text)
    if not t:
        return BacklogMetric()

    # 1) 수주잔고 키워드 인접 값 (다양한 표현 포함)
    # ⚠️ 2026-07-19 수정(사용자 지적: 유진테크 084370이 2022Q4 이후 전혀 수집 안 됨) — 원인 특정:
    # 이 회사(및 반도체 장비 업종 다수)는 "장비수주 계약잔액"(계약잔고 아님, '고' vs '액' 한 글자
    # 차이로 기존 키워드 그룹이 매칭 실패)을 IFRS15 계약자산 증감표(기초→신규계약→수익인식→기말)
    # 형태로 공시함 — "계약잔액" 키워드를 추가.
    kw_group = (
        r"(?:수주\s*잔고|수주잔고|수주\s*잔액|수주잔액|계약\s*잔고|계약잔고|계약\s*잔액|계약잔액"
        r"|미착공\s*수주잔고|미완성\s*공사\s*(?:잔고|계약잔액)|잔여\s*공사잔고|잔여공사잔고"
        r"|공사\s*잔고|용역\s*잔고|수주\s*금액\s*잔액"
        r"|order\s*backlog|backlog)"
    )
    patterns = [
        kw_group + r"[^\d-]{0,60}(-?[\d,]+(?:\.\d+)?)\s*(조\s*원|억\s*원|백만원|천만원|천원|만원|조|억|원)",
    ]

    cands: list[tuple[float, str, float, str]] = []

    # ⚠️ 2026-07-19: "합계 NUMBER" 명시적 총계 우선탐색을 시도했으나 철회함 — "합계"는
    # 파생상품/이자율스왑 등 수주잔고와 무관한 표에도 매우 흔히 등장해(SK오션플랜트 100090
    # 실측 중 이자율스왑 헤지테이블의 "합 계 5,754,804"를 오채택, derivative-context 30자
    # 예외로도 다 못 걸러짐 — 파생상품 섹션이 여러 하위표에 걸쳐 넓게 퍼져있음) 오히려 다른
    # 정상 케이스(삼성중공업)까지 깨뜨림. 순효과가 마이너스라 제거, 아래 1-b/1-c/base패턴만 사용.

    for p in patterns:
        for m in re.finditer(p, t, re.IGNORECASE):
            if _is_derivative_context(t, m.start()):
                continue
            if _is_false_contract_balance_context(t, m.start(), m.group(0)):
                continue
            if _is_footnote_marker(t, m.start(1), m.end(1)):
                continue
            raw_v = _parse_num(m.group(1))
            if raw_v is None:
                continue
            unit = m.group(2).replace(" ", "")
            unit = {"조": "조원", "억": "억원"}.get(unit, unit)
            conf = 0.85
            krw = _korean_to_krw(raw_v, unit)
            excerpt = t[max(0, m.start()-40):min(len(t), m.end()+40)]
            cands.append((krw, unit, conf, excerpt, _period_tier(t, m.start()), _has_total_marker(t, m.start())))

    # 1-c) "기말"+키워드가 붙어 행 라벨을 이루는 표 형식(예: "기말계약잔액 76,325,858 2,214,216
    # 10,552,953 89,093,027" — 조선/해양플랜트/기타부문/합계 4개 열) 대응. 1-b는 "기초"가 같은
    # 키워드 매칭 앞쪽(이미 소비된 위치)에 있어 forward window에서 못 찾으므로 못 잡는 케이스.
    # 실측(HD한국조선해양 009540, 20260515001799): 수정 전 76,325,858원(0.76억) → 수정 후
    # 89,093,027백만원(89.09조원, 합계열)으로 정상화 확인.
    end_kw_pat = r"기말[^\d가-힣]{0,10}(?:" + kw_group + r")|(?:" + kw_group + r")[^\d가-힣]{0,10}기말"
    for em in re.finditer(end_kw_pat, t, re.IGNORECASE):
        if _is_derivative_context(t, em.start()):
            continue
        row_window = t[em.end():em.end() + 150]
        stop_m = re.search(r"[가-힣]{2,}", row_window)
        row = row_window[:stop_m.start()] if stop_m else row_window
        # 2026-08-12: 090470 실측으로 발견 — "주1)" 각주번호가 이 행 안에 섞여있으면
        # re.findall 기반 nums[-1] 선택이 각주번호를 진짜 값으로 오인할 수 있어, 위치 정보를
        # 보존하는 finditer로 전환하고 각주 마커 위치는 후보에서 제외.
        num_matches = [
            nm for nm in re.finditer(r"-?[\d,]+(?:\.\d+)?", row)
            if _parse_num(nm.group(0)) is not None
            and not _is_footnote_marker(t, em.end() + nm.start(), em.end() + nm.end())
        ]
        if num_matches:
            raw_v = _parse_num(num_matches[-1].group(0))
            if raw_v is not None:
                unit = _find_unit_nearby(t, em.start())
                krw = _korean_to_krw(raw_v, unit)
                excerpt = t[max(0, em.start()-40):em.end()+150]
                cands.append((krw, unit, 0.92, excerpt, _period_tier(t, em.start()), _has_total_marker(t, em.start())))

    # 1-b) 증감표(기초→신규계약→수익인식→기말) 구조 — 위 일반 패턴은 키워드 직후 첫 숫자(기초,
    # 즉 전기말 잔액이자 과거값)를 잡아버림. "기말" 열 값이 현재 시점의 실제 잔액이므로 별도로
    # 우선 탐색해 최우선 후보로 추가. 실측(유진테크 084370, 20260319000837): "(1) 당기말 장비
    # 수주 계약잔액 (단위 : 천원) 구 분 기초 신규계약 수익인식 기말 반도체 전공정장비 84,406,992
    # 309,317,594 298,079,051 95,645,534 (2) 전기말 장비수주 계약잔액 ..." — (1)당기말 표와
    # (2)전기말 표가 연속으로 나오므로, 다음 "(N)" 마커 전까지로 검색 범위를 제한해야 두 표가
    # 섞이지 않음. 단위는 키워드 바로 뒤 "(단위 : 천원)"에서 추출(기존 코드는 키워드 앞쪽만
    # 봐서 항상 실패했음).
    for km in re.finditer(kw_group, t, re.IGNORECASE):
        if _is_derivative_context(t, km.start()):
            continue
        window_full = t[km.end():km.end() + 1200]
        boundary_m = re.search(
            r"\(\s*\d+\s*\)|\(\s*전(?:기|분기|반기|\d)|5\.\s*위험관리|6\.\s",
            window_full[10:],
        )
        boundary = (boundary_m.start() + 10) if boundary_m else len(window_full)
        window = window_full[:boundary]
        # 2026-07-19 수정: 기존엔 forward window[:60]만 봐서, "(단위:...)" 가 키워드 "앞"에
        # 선언되는 표(삼성중공업 010140 "당분기 (단위 : 천원) 기초공사계약잔액..." 등)에서
        # 항상 "원"으로 묵인 처리되어 29.52조원이 295.2억원으로(10만분의 1) 축소되는 버그 확인
        # — _find_unit_nearby()로 교체(역방향 우선 탐색).
        unit = _find_unit_nearby(t, km.start())
        if "기초" in window and "기말" in window:
            total_row = re.search(r"합\s*계\s+", window)
            if total_row:
                total_tail = window[total_row.end():]
                total_numbers = list(re.finditer(r"-?[\d,]+(?:\.\d+)?", total_tail))
                if len(total_numbers) >= 4:
                    raw_v = _parse_num(total_numbers[3].group(0))
                    if raw_v is not None:
                        krw = _korean_to_krw(raw_v, unit)
                        excerpt = t[km.start():km.end() + boundary]
                        cands.append((krw, unit, 0.96, excerpt, _period_tier(t, km.start()), True))
                        continue
            end_idx = window.find("기말")
            after_end = window[end_idx:]
            after_end_abs = km.end() + end_idx
            # 2026-08-12: 위 1-c 블록과 동일 이유로 각주번호 오채택 방지(finditer로 위치 보존).
            num_matches = [
                nm for nm in re.finditer(r"-?[\d,]+(?:\.\d+)?", after_end)
                if _parse_num(nm.group(0)) is not None
                and not _is_footnote_marker(t, after_end_abs + nm.start(), after_end_abs + nm.end())
            ]
            if num_matches:
                raw_v = _parse_num(num_matches[-1].group(0))
                if raw_v is not None:
                    krw = _korean_to_krw(raw_v, unit)
                    excerpt = t[km.start():km.end() + boundary]
                    cands.append((krw, unit, 0.9, excerpt, _period_tier(t, km.start()), _has_total_marker(t, km.start())))

    # 1-d) "구분 수주총액 매출인식액 수주잔고" 3열 표(IFRS15 잔여이행의무 공시 표준양식) —
    # 2026-08-12 신규: 010420 실측으로 발견 — 위 base패턴(kw_group 직후 첫 숫자)이 "계약잔액"
    # 이라는 동일 키워드가 붙은 전혀 다른 표("구분 당반기말 전기말 매출채권 49,478,847 ...",
    # IFRS15 계약자산/계약부채 잔액 세부내역이지 수주잔고가 아님)를 오채택하는 문제 발견.
    # "수주총액"+"매출인식액"+"수주잔고" 세 헤더가 이 순서로 함께 등장하는 표는 이 프로젝트가
    # 확인한 범위에서 예외 없이 진짜 수주잔고 표였음(010420 두 분기 모두 실측 일치: 반기말
    # 다른 시점 2개 표에서 각각 23,803,708천원/20,993,171천원 — 값이 자연스럽게 변화하는
    # 정상 시계열, 유진테크 084370과 달리 "구분" 자체가 헤더행이라 데이터행에서 값 3개를
    # 순서대로 추출). 헤더 뒤 첫 데이터행에서 마지막(3번째 유효) 숫자가 수주잔고 열.
    header_pat = r"수주총액[^\d가-힣]{0,15}매출인식액[^\d가-힣]{0,15}수주잔고"
    for hm in re.finditer(header_pat, t, re.IGNORECASE):
        row_window = t[hm.end():hm.end() + 200]
        stop_m = re.search(r"\d+\.\s*[가-힣]{2,}", row_window)  # 다음 번호항목(예: "27. 비용의") 전까지
        row = row_window[:stop_m.start()] if stop_m else row_window
        num_matches = [
            nm for nm in re.finditer(r"-?[\d,]+(?:\.\d+)?", row)
            if _parse_num(nm.group(0)) is not None
            and not _is_footnote_marker(t, hm.end() + nm.start(), hm.end() + nm.end())
        ]
        if len(num_matches) >= 3:
            # 첫 데이터행의 3번째 숫자 = 수주잔고 열(수주총액/매출인식액/수주잔고 순서 고정)
            picked = num_matches[2]
            raw_v = _parse_num(picked.group(0))
            if raw_v is not None:
                unit = _find_unit_nearby(t, hm.start())
                krw = _korean_to_krw(raw_v, unit)
                excerpt = t[max(0, hm.start()-20):hm.end() + 150]
                conf = 0.95 if _has_explicit_unit_nearby(t, hm.start()) else 0.55
                cands.append((krw, unit, conf, excerpt, _period_tier(t, hm.start()), True))

    # 1-e) 수량/금액이 반복되는 수주현황 표. 일반 패턴은 수주잔고 헤더 뒤 첫 데이터행의
    # 수량을 금액으로 오인하므로, 엄격한 헤더 안에서만 합계 행의 마지막 값(잔고 금액)을 쓴다.
    quantity_header_pat = (
        r"(?:당\s*(?:분기|반기|기)말\s*)?수주\s*잔고"
        r".{0,100}?수량\s*금액(?:\s*수량\s*금액){2,4}"
    )
    for hm in re.finditer(quantity_header_pat, t, re.IGNORECASE):
        table_window = _bounded_table_window(t, hm.end())
        total_m = re.search(
            r"합\s*계\s+(.{1,300}?)(?=\s*(?:주석|주\s*[)1-9]|※|\(\*|\*\s*수주|\[)|$)",
            table_window,
        )
        if not total_m:
            continue
        total_row = total_m.group(1)
        numbers = [
            nm for nm in re.finditer(r"-?[\d,]+(?:\.\d+)?", total_row)
            if _parse_num(nm.group(0)) is not None
            and not _is_footnote_marker(
                t,
                hm.end() + total_m.start(1) + nm.start(),
                hm.end() + total_m.start(1) + nm.end(),
            )
        ]
        if len(numbers) < 2:
            continue
        raw_v = _parse_num(numbers[-1].group(0))
        if raw_v is None:
            continue
        unit = _find_unit_nearby(t, hm.start())
        krw = _korean_to_krw(raw_v, unit)
        excerpt = t[max(0, hm.start() - 120):hm.end() + total_m.end()]
        # Some filings literally contain "(단위 :)". Treating those tables as won
        # created exact 1,000,000x discontinuities, so keep them review-only.
        conf = 0.96 if _has_explicit_unit_nearby(t, hm.start()) else 0.55
        cands.append((krw, unit, conf, excerpt, _period_tier(t, hm.end()), True))

    # 연도값·소액 필터 적용
    valid_cands = []
    for krw, unit, conf, excerpt, tier, is_total in cands:
        unit_factor = _korean_to_krw(1, unit) or 1
        raw_amount = krw / unit_factor
        if 1_990 <= abs(raw_amount) < 2_031:
            # Dates such as 2021 in a flattened row were frequently selected as
            # 2021백만원. The old check ran after currency conversion and missed it.
            continue
        if krw < 100_000:  # 수주잔고 음수 및 10만원 미만 파싱 오류 제거
            continue
        valid_cands.append((krw, unit, conf, excerpt, tier, is_total))

    if valid_cands:
        # 2026-07-19: 우선순위 = ①당분기(전분기 아님) > ②추출방식 신뢰도(1-b/1-c의 "기말=행의
        # 마지막 숫자" 방식이 conf 0.9~0.92로 가장 정확 > 단위명시 베이스패턴 0.85 > 단위추정
        # 베이스패턴 0.5~0.6) > ③절대값 큰 순.
        # ⚠️ is_total(주변 40자 내 "합계" 존재) 신호는 폐기 — 실측(HD한국조선해양 009540) 결과
        # "부문 합계" 헤더가 있다고 해서 매칭된 숫자 자체가 합계열이라는 보장이 없음(세그먼트별
        # 다중 컬럼 행에서 첫 번째 세그먼트 값에 false positive 태깅되는 사례 발견) — conf가
        # 더 신뢰할 수 있는 우선순위 신호.
        krw, unit, conf, excerpt, tier, is_total = sorted(
            valid_cands, key=lambda x: (x[4], x[2], abs(x[0])), reverse=True
        )[0]
        return BacklogMetric(
            backlog_amount=(krw / (_korean_to_krw(1, unit) or 1)),
            backlog_unit=unit,
            backlog_amount_krw=krw,
            backlog_confidence=conf,
            source_excerpt=excerpt,
        )

    return BacklogMetric()


def _infer_period(report_nm: str, rcept_dt: str) -> tuple[Optional[int], Optional[int]]:
    nm = report_nm or ""
    # 보고서명 내 기준기간 우선 사용: 2026.03 / 2026.06 / 2026.09 / 2025.12
    m = re.search(r"(20\d{2})[./-](0[1-9]|1[0-2])", nm)
    if m:
        y = int(m.group(1))
        mm = int(m.group(2))
        q = {3: 1, 6: 2, 9: 3, 12: 4}.get(mm)
        if q:
            return y, q

    # fallback: 접수일 + 보고서 유형
    try:
        y = int((rcept_dt or "")[:4])
        mm = int((rcept_dt or "")[4:6])
    except Exception:
        return None, None

    if "사업보고서" in nm:
        return y - 1, 4
    if "반기보고서" in nm:
        return y, 2
    if "분기보고서" in nm:
        return (y, 1) if mm <= 6 else (y, 3)
    return None, None


def _candidate_disclosures(
    year_from: int,
    year_to: int,
    limit: int | None,
    target_codes: set[str] | None = None,
    annual_only: bool = False,
) -> list[dict]:
    conn = connect_stock_db(timeout=60)
    conn.row_factory = __import__("sqlite3").Row
    try:
        rows = conn.execute(
            """
            SELECT stock_code, rcept_no, rcept_dt, report_nm, corp_name
            FROM dart_disclosures
            WHERE stock_code IS NOT NULL AND stock_code<>''
              AND (
                report_nm LIKE '%사업보고서%'
                OR report_nm LIKE '%반기보고서%'
                OR report_nm LIKE '%분기보고서%'
              )
              AND substr(rcept_dt,1,4) BETWEEN ? AND ?
            ORDER BY rcept_dt DESC, rcept_no DESC
            """,
            (str(year_from), str(year_to)),
        ).fetchall()
    finally:
        conn.close()

    # 동일 종목/연도/분기 최신 1건만 남김
    dedup: dict[tuple[str, int, int], dict] = {}
    for r in rows:
        fy, fq = _infer_period(r["report_nm"], r["rcept_dt"])
        if not fy or not fq:
            continue
        key = (r["stock_code"], fy, fq)
        if key not in dedup:
            dedup[key] = dict(r)
            dedup[key]["fiscal_year"] = fy
            dedup[key]["fiscal_quarter"] = fq

    out = list(dedup.values())
    if target_codes is not None:
        out = [r for r in out if r.get("stock_code") in target_codes]
    if annual_only:
        out = [r for r in out if "사업보고서" in (r.get("report_nm") or "")]
    out.sort(
        key=lambda x: (
            1 if "사업보고서" in (x.get("report_nm") or "") else 0,
            x["fiscal_year"],
            x["fiscal_quarter"],
            x["stock_code"],
        ),
        reverse=True,
    )
    if limit:
        out = out[: max(1, int(limit))]
    return out


def _missing_backlog_codes(year_from: int, year_to: int, eligible_only: bool = False) -> set[str]:
    """수주잔고가 한 번도 저장되지 않은 종목만 추린다.

    수주잔고는 모든 업종이 공시하는 항목이 아니므로, 이 옵션은 커버리지 보강용 재시도에
    사용한다. 이미 값이 있는 종목을 반복 파싱하지 않아 DART 쿼터를 아낀다.
    """
    conn = connect_stock_db(timeout=60)
    try:
        sector_filter = ""
        params: list[object] = []
        if eligible_only:
            sector_filter = """
                  AND (
                    COALESCE(sector_large,'') IN ('산업재','에너지','IT','소재')
                    OR COALESCE(sector_mid,'') IN ('자본재','반도체','하드웨어','디스플레이','에너지','소재')
                    OR COALESCE(sector_small,'') LIKE '%건설%'
                    OR COALESCE(sector_small,'') LIKE '%조선%'
                    OR COALESCE(sector_small,'') LIKE '%장비%'
                    OR COALESCE(sector_small,'') LIKE '%플랜트%'
                    OR COALESCE(sector_small,'') LIKE '%방산%'
                  )
            """
        all_codes = {
            str(r[0])
            for r in conn.execute(
                f"""
                SELECT stock_code
                FROM stock_universe
                WHERE stock_code IS NOT NULL AND length(stock_code)=6
                {sector_filter}
                """
                ,
                params,
            )
        }
        covered = {
            str(r[0])
            for r in conn.execute(
                """
                SELECT DISTINCT stock_code
                FROM order_backlog
                WHERE year BETWEEN ? AND ?
                  AND COALESCE(backlog_amount, backlog_normalized, 0) > 0
                """,
                (year_from, year_to),
            )
        }
        return all_codes - covered
    finally:
        conn.close()


def collect_backlog_quarterly(
    year_from: int = 2021,
    year_to: int = 2026,
    limit: int | None = None,
    report_type: str = "CFS",
    missing_only: bool = False,
    eligible_only: bool = False,
    annual_only: bool = False,
) -> dict:
    _ensure_table()
    target_codes = _missing_backlog_codes(year_from, year_to, eligible_only=eligible_only) if missing_only else None
    cands = _candidate_disclosures(year_from, year_to, limit, target_codes=target_codes, annual_only=annual_only)

    conn = connect_stock_db(timeout=60)
    try:
        ok = 0
        no_text = 0
        no_metric = 0
        errs = 0

        for i, row in enumerate(cands, start=1):
            stock_code = row["stock_code"]
            rcept_no = row["rcept_no"]
            report_nm = row["report_nm"]
            rcept_dt = row["rcept_dt"]
            corp_name = row.get("corp_name")
            fy = int(row["fiscal_year"])
            fq = int(row["fiscal_quarter"])

            try:
                raw = _fetch_document_with_key_rotation(rcept_no)
                if not raw:
                    no_text += 1
                    continue
                metric = _extract_backlog(raw)
                if metric.backlog_amount_krw is None:
                    no_metric += 1
                    continue

                text_hash = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
                conn.execute(
                    """
                    INSERT INTO order_backlog(
                        stock_code, stock_name, year, quarter, report_type, rcept_no,
                        backlog_amount, backlog_unit, backlog_normalized, data_source, collected_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                        stock_name=excluded.stock_name,
                        report_type=excluded.report_type,
                        rcept_no=excluded.rcept_no,
                        backlog_amount=excluded.backlog_amount,
                        backlog_unit=excluded.backlog_unit,
                        backlog_normalized=excluded.backlog_normalized,
                        data_source='dart_backlog',
                        collected_at=CURRENT_TIMESTAMP
                    """,
                    (
                        stock_code, corp_name, fy, fq, report_nm, rcept_no,
                        metric.backlog_amount_krw, "원", (metric.backlog_amount_krw / 1_000_000.0) if metric.backlog_amount_krw is not None else None, "dart_backlog",
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO dart_backlog_quarterly(
                        stock_code,fiscal_year,fiscal_quarter,report_type,
                        backlog_amount,backlog_unit,backlog_amount_krw,backlog_confidence,
                        source_excerpt,source_rcept_no,source_report_nm,source_rcept_dt,
                        source_text_hash,parser_version,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type) DO UPDATE SET
                        backlog_amount=excluded.backlog_amount,
                        backlog_unit=excluded.backlog_unit,
                        backlog_amount_krw=excluded.backlog_amount_krw,
                        backlog_confidence=excluded.backlog_confidence,
                        source_excerpt=excluded.source_excerpt,
                        source_rcept_no=excluded.source_rcept_no,
                        source_report_nm=excluded.source_report_nm,
                        source_rcept_dt=excluded.source_rcept_dt,
                        source_text_hash=excluded.source_text_hash,
                        parser_version=excluded.parser_version,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        stock_code, fy, fq, report_type,
                        metric.backlog_amount, metric.backlog_unit, metric.backlog_amount_krw, metric.backlog_confidence,
                        metric.source_excerpt, rcept_no, report_nm, rcept_dt,
                        text_hash, PARSER_VERSION,
                    ),
                )
                _refresh_order_backlog_projection(conn, stock_code, report_type)
                # backlog 변화율 트리거 적재
                _upsert_backlog_trigger(conn, stock_code, fy, fq, report_type)
                ok += 1
            except Exception:
                errs += 1
                logger.exception("[Backlog] parse failed %s %s", stock_code, rcept_no)

            if i % 20 == 0:
                conn.commit()
                logger.info("[Backlog] progress %s/%s ok=%s no_text=%s no_metric=%s err=%s", i, len(cands), ok, no_text, no_metric, errs)

        conn.commit()
        return {
            "ok": ok,
            "candidates": len(cands),
            "no_text": no_text,
            "no_metric": no_metric,
            "errors": errs,
            "year_from": year_from,
            "year_to": year_to,
            "report_type": report_type,
            "parser_version": PARSER_VERSION,
            "missing_only": missing_only,
            "eligible_only": eligible_only,
            "annual_only": annual_only,
            "target_codes": len(target_codes) if target_codes is not None else None,
        }
    finally:
        conn.close()


def collect_order_backlog(
    year_from: int = 2021,
    year_to: int = 2026,
    limit: int | None = None,
    report_type: str = "CFS",
    missing_only: bool = False,
    eligible_only: bool = False,
    annual_only: bool = False,
) -> dict:
    """지시서 호환 별칭."""
    return collect_backlog_quarterly(
        year_from=year_from,
        year_to=year_to,
        limit=limit,
        report_type=report_type,
        missing_only=missing_only,
        eligible_only=eligible_only,
        annual_only=annual_only,
    )


def _refresh_order_backlog_projection(conn, stock_code: str, report_type: str = "CFS") -> dict[str, int]:
    """Keep legacy order_backlog as a safe projection while preserving DART evidence."""
    rows = conn.execute(
        """
        SELECT fiscal_year,fiscal_quarter,backlog_amount_krw,backlog_confidence
        FROM dart_backlog_quarterly
        WHERE stock_code=? AND report_type=? AND backlog_amount_krw IS NOT NULL
        ORDER BY fiscal_year,fiscal_quarter
        """,
        (stock_code, report_type),
    ).fetchall()
    values = {
        (int(row[0]), int(row[1])): (float(row[2]), float(row[3] or 0))
        for row in rows
    }
    rejected = {
        period for period, (amount, confidence) in values.items()
        if confidence < MIN_OPERATIONAL_CONFIDENCE or amount <= 0
    }
    periods = sorted(values)
    for previous, current in zip(periods, periods[1:]):
        if current != ((previous[0], previous[1] + 1) if previous[1] < 4 else (previous[0] + 1, 1)):
            continue
        left, left_confidence = values[previous]
        right, right_confidence = values[current]
        if left_confidence < MIN_OPERATIONAL_CONFIDENCE or right_confidence < MIN_OPERATIONAL_CONFIDENCE:
            continue
        if left <= 0 or right <= 0 or max(left, right) / min(left, right) > 20.0:
            rejected.update((previous, current))

    for (year, quarter), (amount, _) in values.items():
        if (year, quarter) in rejected:
            conn.execute(
                """
                UPDATE order_backlog SET backlog_amount=NULL,backlog_unit=NULL,
                    backlog_normalized=NULL,backlog_to_rev=NULL,collected_at=CURRENT_TIMESTAMP
                WHERE stock_code=? AND year=? AND quarter=?
                """,
                (stock_code, year, quarter),
            )
        else:
            conn.execute(
                """
                UPDATE order_backlog SET backlog_amount=?,backlog_unit='원',
                    backlog_normalized=?,collected_at=CURRENT_TIMESTAMP
                WHERE stock_code=? AND year=? AND quarter=?
                """,
                (amount, amount / 1_000_000.0, stock_code, year, quarter),
            )
    return {"accepted": len(values) - len(rejected), "rejected": len(rejected)}


def _upsert_backlog_trigger(conn, stock_code: str, fy: int, fq: int, report_type: str) -> None:
    rows = conn.execute(
        """
        SELECT fiscal_year, fiscal_quarter, backlog_amount_krw, backlog_confidence
        FROM dart_backlog_quarterly
        WHERE stock_code=? AND report_type=? AND backlog_amount_krw IS NOT NULL
        ORDER BY fiscal_year, fiscal_quarter
        """,
        (stock_code, report_type),
    ).fetchall()
    mp = {(int(r[0]), int(r[1])): (float(r[2]), float(r[3] or 0)) for r in rows}
    current = mp.get((fy, fq))
    trigger_key = (stock_code, fy, fq, report_type, "backlog")
    if current is None or current[1] < MIN_OPERATIONAL_CONFIDENCE:
        conn.execute(
            """DELETE FROM dart_tenbagger_triggers_quarterly
               WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?
                 AND report_type=? AND metric_name=?""",
            trigger_key,
        )
        return
    cur = current[0]
    prev_q = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
    next_q = (fy, fq + 1) if fq < 4 else (fy + 1, 1)
    prev_y = (fy - 1, fq)
    qrow = mp.get(prev_q)
    next_row = mp.get(next_q)
    yrow = mp.get(prev_y)
    qv = qrow[0] if qrow and qrow[1] >= MIN_OPERATIONAL_CONFIDENCE else None
    nv = next_row[0] if next_row and next_row[1] >= MIN_OPERATIONAL_CONFIDENCE else None
    yv = yrow[0] if yrow and yrow[1] >= MIN_OPERATIONAL_CONFIDENCE else None

    def comparable(value: Optional[float]) -> bool:
        if value in (None, 0) or cur <= 0 or value <= 0:
            return value is None
        return max(cur, value) / min(cur, value) <= 20.0

    # A 20x discontinuity is overwhelmingly a unit/segment mismatch in the audit.
    # Keep the source row for review, but never turn it into a trading trigger.
    if not comparable(qv) or not comparable(nv) or not comparable(yv):
        conn.execute(
            """DELETE FROM dart_tenbagger_triggers_quarterly
               WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?
                 AND report_type=? AND metric_name=?""",
            trigger_key,
        )
        return
    qoq = ((cur - qv) / abs(qv) * 100.0) if (qv not in (None, 0)) else None
    yoy = ((cur - yv) / abs(yv) * 100.0) if (yv not in (None, 0)) else None
    lvl = "BACKLOG_SURGE" if (yoy is not None and yoy >= 25.0) else None
    conn.execute(
        """
        INSERT INTO dart_tenbagger_triggers_quarterly(
            stock_code,fiscal_year,fiscal_quarter,report_type,
            metric_name,metric_value,yoy_pct,qoq_pct,trigger_level,source_table,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type,metric_name) DO UPDATE SET
            metric_value=excluded.metric_value,
            yoy_pct=excluded.yoy_pct,
            qoq_pct=excluded.qoq_pct,
            trigger_level=excluded.trigger_level,
            source_table=excluded.source_table,
            updated_at=CURRENT_TIMESTAMP
        """,
        (stock_code, fy, fq, report_type, "backlog", cur, yoy, qoq, lvl, "dart_backlog_quarterly"),
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="DART 수주잔고 분기 수집기")
    ap.add_argument("--year-from", type=int, default=2021)
    ap.add_argument("--year-to", type=int, default=datetime.now().year)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report-type", default="CFS")
    ap.add_argument("--missing-only", action="store_true", help="수주잔고 미포착 종목만 재파싱")
    ap.add_argument("--eligible-only", action="store_true", help="수주잔고 공시 가능성이 높은 업종만 재파싱")
    ap.add_argument("--annual-only", action="store_true", help="사업보고서만 우선 재파싱")
    args = ap.parse_args()

    stats = collect_backlog_quarterly(
        year_from=args.year_from,
        year_to=args.year_to,
        limit=(args.limit or None),
        report_type=args.report_type,
        missing_only=args.missing_only,
        eligible_only=args.eligible_only,
        annual_only=args.annual_only,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
