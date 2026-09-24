"""
DART 사업보고서 '4. 매출 및 수주상황' > '가. 매출 실적' 표에서
품목(제품)별 매출액을 추출해 company_product_mix 테이블에 저장.

회사마다 표 형식이 다름(수출/내수 구분 있는 곳 vs 없는 곳, 단위 표기 위치 등)이라
표를 그리드로 완전 파싱한 뒤 규칙 기반으로 "품목 합계 행"만 골라낸다.

실행:
    python3 collectors/dart_product_mix_collector.py --codes 290550,094970,054040
    python3 collectors/dart_product_mix_collector.py --codes 290550 --year 2025
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dart_key_manager import get_dart_api_keys
from db_utils import STOCK_DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"
CORP_XML_CACHE = Path("/tmp/CORPCODE.xml")
PROG_PATH = Path("/tmp/dart_product_mix_progress.json")

KEYS = get_dart_api_keys()
_exhausted: set = set()
_ki = 0


def _next_key():
    global _ki
    for _ in range(len(KEYS)):
        k = KEYS[_ki % len(KEYS)]
        _ki += 1
        if k not in _exhausted:
            return k
    return KEYS[0] if KEYS else None


def _get_conn():
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


# ── corp_code 매핑 ──────────────────────────────────────────────
def _ensure_corp_code_xml() -> Path:
    if CORP_XML_CACHE.exists() and CORP_XML_CACHE.stat().st_size > 1_000_000:
        return CORP_XML_CACHE
    for _ in range(len(KEYS)):
        key = _next_key()
        if key is None:
            break
        r = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            body = r.content[:300].decode("utf-8", errors="ignore")
            if "020" in body or "한도" in body:
                _exhausted.add(key)
            continue
        data = zf.read(zf.namelist()[0])
        CORP_XML_CACHE.write_bytes(data)
        return CORP_XML_CACHE
    raise RuntimeError("corpCode.xml 다운로드 실패 (모든 키 소진)")


def load_corp_map(codes: list[str]) -> dict[str, str]:
    import xml.etree.ElementTree as ET
    path = _ensure_corp_code_xml()
    tree = ET.parse(str(path))
    wanted = set(codes)
    full_map: dict[str, str] = {}  # corpCode.xml 전체(보통주 fallback 조회용)
    out = {}
    for child in tree.getroot():
        sc = child.findtext("stock_code", "").strip()
        if not sc:
            continue
        cc = child.findtext("corp_code", "").strip()
        full_map[sc] = cc
        if sc in wanted:
            out[sc] = cc

    missing = [c for c in codes if c not in out]
    if missing:
        out.update(_resolve_preferred_corp_codes(missing, full_map))
    return out


# KRX 표시명 축약으로 인해 접미사(우/1우/2우B 등)를 떼도 보통주 종목명과 매칭되지 않는 예외.
# 우선주 → 보통주 stock_code 수동 매핑.
_PREFERRED_NAME_OVERRIDE = {
    "코리아써우": "코리아써키트",
    "남선알미우": "남선알미늄",
}


def _resolve_preferred_corp_codes(missing_codes: list[str], known_corp_map: dict[str, str]) -> dict[str, str]:
    """
    corpCode.xml에 없는 종목은 대부분 우선주(DART가 corp_code를 보통주 stock_code에만 부여).
    같은 회사의 사업보고서를 그대로 쓰면 되므로, 종목명에서 '우/1우/2우B' 등 접미사를 떼어
    보통주 stock_code를 찾고 그 corp_code를 빌려온다.
    """
    conn = _get_conn()
    rows = conn.execute("SELECT stock_code, stock_name FROM stock_universe").fetchall()
    conn.close()
    name_to_code = {r["stock_name"].strip(): r["stock_code"] for r in rows}
    code_to_name = {r["stock_code"]: r["stock_name"].strip() for r in rows}

    resolved: dict[str, str] = {}
    for code in missing_codes:
        name = code_to_name.get(code, "")
        base = _PREFERRED_NAME_OVERRIDE.get(name) or re.sub(r"\d*우B?$", "", name).strip()
        if not base or base == name:
            continue
        common_code = name_to_code.get(base)
        if not common_code:
            continue
        # 보통주 자신의 corp_code(이미 확보됐거나, 아직이면 별도 조회 필요)
        cc = known_corp_map.get(common_code)
        if not cc:
            continue
        resolved[code] = cc
    return resolved


# ── 사업보고서 원문 다운로드 ───────────────────────────────────
def find_latest_annual_rcept(corp_code: str) -> tuple[str, str] | None:
    for _ in range(len(KEYS)):
        key = _next_key()
        if key is None:
            break
        try:
            r = requests.get(f"{DART_BASE}/list.json", params={
                "crtfc_key": key, "corp_code": corp_code, "pblntf_ty": "A",
                "bgn_de": "20200101", "end_de": "20261231", "page_count": 30,
            }, timeout=30)
            j = r.json()
        except (requests.RequestException, ValueError):
            continue  # 네트워크/파싱 일시 오류 — 키는 그대로 두고 다음 키로 재시도
        if j.get("status") == "020":
            _exhausted.add(key)
            continue
        if j.get("status") != "000":
            log.warning("list.json error corp=%s: %s", corp_code, j.get("message"))
            return None
        for item in j.get("list", []):
            # "[기재정정]" "[첨부추가]" 등 정정본은 제외, 순수 "사업보고서"만
            if item["report_nm"].startswith("사업보고서"):
                return item["rcept_no"], item["report_nm"]
        return None
    return None


def download_document(rcept_no: str) -> str | None:
    # zip이 아닌 응답은 대부분 크지 않은 XML 에러 바디(예: {"status":"020",...})다.
    # 이 값이 진짜 "일일한도 초과"일 때만 해당 키를 소진 처리하고, 그 외 사유(일시적 오류,
    # 문서 없음 등)로 키를 영구 블랙리스트에 넣으면 대량 배치 중 몇 번의 우연한 실패만으로
    # 키 3개가 전부 소진 처리되어 이후 전종목이 연쇄 실패하는 사고로 이어진다.
    last_err = None
    for _ in range(len(KEYS)):
        key = _next_key()
        if key is None:
            break
        try:
            r = requests.get(f"{DART_BASE}/document.xml", params={
                "crtfc_key": key, "rcept_no": rcept_no,
            }, timeout=60)
        except requests.RequestException as e:
            last_err = str(e)
            continue  # 네트워크 일시 오류 — 키는 그대로 두고 다음 키로 재시도
        try:
            zf = zipfile.ZipFile(io.BytesIO(r.content))
        except zipfile.BadZipFile:
            body = r.content[:300].decode("utf-8", errors="ignore")
            if "020" in body or "한도" in body:
                _exhausted.add(key)
            else:
                last_err = body
            continue
        main_name = f"{rcept_no}.xml"
        names = [n for n in zf.namelist() if n == main_name] or [zf.namelist()[0]]
        return zf.read(names[0]).decode("utf-8", errors="ignore")
    if last_err:
        log.debug("document.xml 실패(rcept=%s): %s", rcept_no, last_err)
    return None


# ── HTML(XML) 표 → 그리드 파싱 (ROWSPAN/COLSPAN 처리) ─────────
def parse_html_table_grid(table_html: str) -> list[list[str]]:
    rows_raw = re.findall(r"<TR\b.*?</TR>", table_html, re.S | re.I)
    grid_rows: list[dict[int, str]] = []
    pending: dict[int, list] = {}  # col_idx -> [remaining_rows, text]
    max_cols = 0
    for row_html in rows_raw:
        cells = re.findall(r"<T[DH]\b([^>]*)>(.*?)</T[DH]>", row_html, re.S | re.I)
        row: dict[int, str] = {}
        col = 0

        def _place_pending(c):
            while c in pending:
                span, text = pending[c]
                row[c] = text
                if span - 1 > 0:
                    pending[c] = [span - 1, text]
                else:
                    del pending[c]
                c += 1
            return c

        col = _place_pending(col)
        for attrs, raw_text in cells:
            cs_m = re.search(r'COLSPAN="(\d+)"', attrs, re.I)
            rs_m = re.search(r'ROWSPAN="(\d+)"', attrs, re.I)
            colspan = int(cs_m.group(1)) if cs_m else 1
            rowspan = int(rs_m.group(1)) if rs_m else 1
            text = re.sub(r"<[^>]+>", "", raw_text)
            text = re.sub(r"&[a-zA-Z#\d]+;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            for _ in range(colspan):
                row[col] = text
                if rowspan > 1:
                    pending[col] = [rowspan - 1, text]
                col += 1
                col = _place_pending(col)
        max_cols = max(max_cols, col)
        grid_rows.append(row)
    return [[row.get(c, "") for c in range(max_cols)] for row in grid_rows]


_TOTAL_LABELS = {"합계", "합 계", "소계", "계"}
_UNIT_MULTIPLIER = {"억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}


def _detect_unit(section_text: str) -> float:
    m = re.search(r"단위\s*[:：]\s*([^)<]+)", section_text)
    label = (m.group(1) if m else "").strip()
    for key, mul in _UNIT_MULTIPLIER.items():
        if key in label:
            return mul
    return 1.0  # 기본값: 원 (또는 단위 미기재 시 하위 로직에서 규모로 판단)


def _to_number(text: str) -> float | None:
    t = text.replace(",", "").strip()
    if t in ("", "-", "―", "—"):
        return 0.0
    # "44,293,105(98.20%)"처럼 금액 뒤에 비중이 괄호로 붙는 표기 — 괄호 앞 금액만 사용
    m = re.match(r"^(-?\d+(?:\.\d+)?)\(.*\)$", t)
    if m:
        t = m.group(1)
    try:
        return float(t)
    except ValueError:
        return None


def extract_product_mix(doc_text: str) -> tuple[list[dict], str, float]:
    """
    반환: (품목별 레코드 리스트, rcept 내 매출실적 섹션 발견 여부 문자열, 감지된 단위 배수)
    레코드: {"category": str, "product_name": str, "revenue": float(연도별 가장 최근 컬럼, 단위 반영 전 원문 숫자)}
    """
    idxs = [m.start() for m in re.finditer(r"매출\s*및\s*수주상황", doc_text)]
    if not idxs:
        return [], "section_not_found", 1.0

    # DART 원문은 각 최상위 항목을 <SECTION-2>...</SECTION-2>로 감싸므로 이걸 경계로 쓰면
    # "나." 같은 텍스트 패턴에 의존하지 않고 정확한 절 범위를 잡을 수 있다(번호 표기가
    # "나.)"/"나)"/생략 등으로 회사마다 달라 텍스트 매칭은 신뢰할 수 없음).
    # 실패 시(구조가 다른 문서) 예전 방식(고정폭 35000자)으로 폴백.
    for idx in reversed(idxs):
        sec_start = doc_text.rfind("<SECTION-2", 0, idx)
        sec_end = doc_text.find("<SECTION-2", idx + 10)
        if sec_start >= 0 and sec_end > idx:
            section = doc_text[idx:sec_end]
        else:
            section = doc_text[idx: idx + 35000]
        tables = re.findall(r"<TABLE\b.*?</TABLE>", section, re.S | re.I)
        if not tables:
            continue

        # "매출 실적" 표 선택: 1순위 "품목"/"매출유형"/"사업부문" 헤더, 2순위(없을 때만)
        # "구분"류 일반 라벨 — 단, 사업분야 무관 표(재무위험/통화/충당금 등)가 같은 절에
        # 섞여 있는 경우가 많아 알려진 잡음 헤더는 명시적으로 제외한다(세그먼트 노트 외
        # 표는 대부분 "구분" 하나만 헤더에 쓰고 실제 항목행에 숫자 다수 컬럼을 가짐).
        _NOISE_HEADER_WORDS = (
            "계정과목", "과목", "당기말", "전기말", "통화", "회사명", "매출처", "계약상대방",
            "변동일자", "평가일", "연구과제", "주권상장", "중소기업", "세후이익", "자본에대한영향",
            "기대손실률", "장부금액", "손실충당금", "연체", "위험",
        )
        target_grid = None
        target_idx = None
        fallback_grid = None
        fallback_idx = None
        for ti, t in enumerate(tables):
            grid = parse_html_table_grid(t)
            if len(grid) < 2:
                continue
            header_join = " ".join(grid[0])
            header_join_n = header_join.replace(" ", "")
            if (
                "품목" in header_join_n or "매출유형" in header_join_n
                or "사업부문" in header_join_n or "사업구분" in header_join_n or "사업부분" in header_join_n
                or "보고부문" in header_join_n or "재화및용역" in header_join_n
            ) and len(grid[0]) >= 2:
                target_grid = grid
                target_idx = ti
                break
            if (
                fallback_grid is None
                and grid[0]
                and grid[0][0].replace(" ", "") == "구분"
                and not any(w in header_join_n for w in _NOISE_HEADER_WORDS)
            ):
                fallback_grid = grid
                fallback_idx = ti
        if target_grid is None:
            target_grid, target_idx = fallback_grid, fallback_idx
        if target_grid is None:
            continue

        # 단위 표기는 ①대상 표 바로 앞의 별도 표(가장 흔한 패턴), ②대상 표 헤더 자체,
        # ③표 앞의 캡션 문단(예: "1) 매출 실적 (단위: 억원)" 처럼 표가 아니라 <P> 텍스트로만
        # 표시되는 경우) 순으로 찾는다. section 앞부분(다른 표의 단위)을 잘못 줍지 않도록
        # "대상 표 바로 앞"으로 범위를 한정한다.
        unit_mul = 1.0
        if target_idx is not None and target_idx > 0:
            prev_text = " ".join(cell for row in parse_html_table_grid(tables[target_idx - 1]) for cell in row)
            unit_mul = _detect_unit(prev_text)
        if unit_mul == 1.0:
            unit_mul = _detect_unit(" ".join(target_grid[0]))
        if unit_mul == 1.0:
            table_html = tables[target_idx]
            pos = section.find(table_html)
            if pos > 0:
                unit_mul = _detect_unit(section[max(0, pos - 500): pos])

        data_rows = target_grid[1:] if target_grid else []

        def _norm(s: str) -> str:
            return s.replace(" ", "")

        # "수출"/"내수"는 수출입 구분표에서만 등장하는 토큰이라 형식 판별에 안전하게 쓸 수 있음
        # ("소계"/"합계"는 단순 형식의 총계행에도 나타나므로 판별 기준에서 제외)
        has_type_col = any(
            any(_norm(cell) in ("수출", "내수") for cell in row) for row in data_rows
        )
        _TYPE_TOKENS = ("수출", "내수", "국내", "소계", "합계", "계", "총계")

        # 1차: 행을 (라벨컬럼들, 유형라벨 또는 None) → 값 으로 그룹화.
        # 같은 품목이 수출/내수/소계 3줄로 나뉘어 있으면 "소계"(또는 합계/계) 행을 우선 채택하고,
        # 수출만 있거나 내수만 있는 등 유형행이 단 하나뿐이면(예: 국내영업만 하는 회사) 그 값을 그대로 채택한다.
        groups: dict[tuple, dict] = {}
        group_order: list[tuple] = []
        for row in data_rows:
            vals = []
            ci = len(row) - 1
            while ci >= 0:
                cell = row[ci]
                if cell.rstrip().endswith("%"):
                    # 금액 옆에 나란히 붙는 "비중(%)" 컬럼 — 라벨은 아니지만 값으로도 쓰지 않고 건너뜀
                    ci -= 1
                    continue
                # 빈 문자열은 _to_number가 0으로 처리하므로 여기서 걸러지지 않음 —
                # 맨 끝 "비고" 등 항상 빈 컬럼이 있어도 실제 값 컬럼까지 스캔이 이어진다.
                if _to_number(cell) is None:
                    break
                vals.insert(0, cell)
                ci -= 1
            if not vals or ci < 0:
                continue
            val = _to_number(vals[0])  # 값 컬럼 중 첫번째 = 최신 연도
            if val is None:
                continue

            last_label_norm = _norm(row[ci])
            if has_type_col and last_label_norm in _TYPE_TOKENS:
                type_key = last_label_norm
                label_cols = tuple(c for c in row[:ci] if c)
            else:
                type_key = "_single"
                label_cols = tuple(c for c in row[: ci + 1] if c)
            if not label_cols:
                continue

            if label_cols not in groups:
                groups[label_cols] = {}
                group_order.append(label_cols)
            groups[label_cols][type_key] = val

        item_rows: list[dict] = []
        cat_agg: dict[str, float] = {}
        for label_cols in group_order:
            type_map = groups[label_cols]
            category = label_cols[0]
            if len(label_cols) >= 3:
                # 3단 이상 계층(예: 사업부문>매출유형>품목)은 품목명만 쓰면 서로 다른 매출유형의
                # 품목명이 같아 (category,product) 키가 충돌 → DB UNIQUE 제약에 의해 한쪽이
                # 덮어써져 사라지는 사고가 났음(예: 008350 "제품반제품/AL샷시외" vs
                # "상품/AL샷시외"). 중간 라벨을 품목명에 포함시켜 유일하게 만든다.
                product = " ".join(label_cols[1:])
            elif len(label_cols) == 2:
                product = label_cols[-1]
            else:
                product = label_cols[0]
            cat_n, prod_n = _norm(category), _norm(product)
            if cat_n in ("합계", "총계") or prod_n in ("합계", "총계"):
                continue  # 전체 합계 그룹 제외

            if len(type_map) == 1:
                val = next(iter(type_map.values()))  # 유형행이 하나뿐이면 그대로 채택(예: 내수만 있는 회사)
            elif "소계" in type_map or "합계" in type_map or "계" in type_map:
                val = type_map.get("소계", type_map.get("합계", type_map.get("계")))
            else:
                # 수출/내수(/국내) 여러 유형만 있고 합산행이 없는 표가 흔함 — 직접 합산해서 사용
                val = sum(type_map.values())

            if val < 0:
                continue  # 내부거래 제거 등 조정행(음수)은 개별 품목이 아니므로 제외
            if prod_n in ("계", "소계") or prod_n == cat_n + "계":
                cat_agg[category] = val  # 사업부문(또는 유형) 소계 — 세부품목 있으면 버림
                continue
            item_rows.append({"category": category, "product_name": product, "revenue": val})

        # 세부 품목이 아예 없는 카테고리(예: 임가공)는 카테고리 소계를 품목처럼 사용
        item_categories = {r["category"] for r in item_rows}
        for cat, val in cat_agg.items():
            if cat not in item_categories:
                item_rows.append({"category": cat, "product_name": cat, "revenue": val})

        # 안전장치: 어느 한 행의 값이 "나머지 행 전체 합계"와 (오차 0.5% 이내로) 같으면 그건
        # 세부품목이 아니라 어딘가에 노출된 총계행이다(라벨이 "합계/계"가 아니라 "매출액"/
        # "영업수익"/"연결기준합계" 등 임의 문구일 수 있고, 위치도 맨 앞이 아닐 수 있음 —
        # "구분"표 폴백이나 사업부문별 소계+총계 병기 표에서 흔함). 실제 총계=부분합은 반올림
        # 오차 정도만 나므로 0.5%면 충분히 안전 — 2%는 너무 느슨해서 우연히 비슷한 값을 가진
        # 서로 다른 두 사업부문(총계 아님)을 잘못 총계로 오판해 삭제한 사례가 있었음(002760).
        # 가장 큰 값부터 검사해서 작은 항목들의 합이 다른 작은 항목과 우연히 같아지는 경우를 피한다.
        if len(item_rows) >= 3:
            total_sum = sum(r["revenue"] for r in item_rows)
            for i in sorted(range(len(item_rows)), key=lambda i: -item_rows[i]["revenue"]):
                v = item_rows[i]["revenue"]
                rest_sum = total_sum - v
                if rest_sum > 0 and abs(v - rest_sum) / rest_sum < 0.005:
                    item_rows.pop(i)
                    break

        if item_rows:
            status = "ok_detailed" if has_type_col else "ok_simple"
            return item_rows, status, unit_mul
        # 이 섹션 후보에서 못 찾았으면 다음 후보(다른 idx)로 계속 탐색

    return [], "table_not_matched", 1.0


def extract_year_from_report_nm(report_nm: str) -> int | None:
    m = re.search(r"\((\d{4})\.\d{1,2}\)", report_nm)
    return int(m.group(1)) if m else None


def collect_one(stock_code: str, corp_code: str) -> bool:
    found = find_latest_annual_rcept(corp_code)
    if not found:
        log.warning("%s: 사업보고서 rcept_no 못 찾음", stock_code)
        return False
    rcept_no, report_nm = found
    year = extract_year_from_report_nm(report_nm)
    if not year:
        log.warning("%s: 연도 파싱 실패 (%s)", stock_code, report_nm)
        return False

    doc = download_document(rcept_no)
    if not doc:
        log.warning("%s: 문서 다운로드 실패 (rcept=%s)", stock_code, rcept_no)
        return False

    records, status, unit_mul = extract_product_mix(doc)
    if not records:
        log.warning("%s: 매출구성 표 파싱 실패 (%s)", stock_code, status)
        return False

    for r in records:
        r["revenue_krw"] = r["revenue"] * unit_mul

    total_krw = sum(r["revenue_krw"] for r in records) or 0.0
    if total_krw <= 0:
        log.warning("%s: 합계 0원, 저장 스킵", stock_code)
        return False

    conn = _get_conn()
    with conn:
        conn.execute(
            "DELETE FROM company_product_mix WHERE stock_code=? AND year=?",
            (stock_code, year),
        )
        for r in records:
            pct = round(r["revenue_krw"] / total_krw * 100, 2)
            conn.execute(
                """INSERT OR REPLACE INTO company_product_mix
                   (stock_code, year, category, product_name, revenue_krw, revenue_pct, rcept_no, source)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (stock_code, year, r["category"], r["product_name"], r["revenue_krw"], pct, rcept_no, "dart_report_text"),
            )
    conn.close()
    log.info("%s: %d개 품목 저장 (연도=%d, 합계=%.0f원, %s)", stock_code, len(records), year, total_krw, status)
    return True


def _load_universe_codes() -> list[str]:
    """KOSPI/KOSDAQ 6자리 종목코드 전체 (시가총액 큰 순 — 대형주부터 확인 가능하도록)"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY market_cap DESC
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _load_progress() -> dict:
    if PROG_PATH.exists():
        try:
            return json.loads(PROG_PATH.read_text())
        except Exception:
            pass
    return {"done": [], "failed": {}}


def _save_progress(prog: dict) -> None:
    PROG_PATH.write_text(json.dumps(prog, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", help="쉼표구분 종목코드 (지정 시 --all 무시)")
    ap.add_argument("--all", action="store_true", help="KOSPI/KOSDAQ 전종목 배치 수집")
    ap.add_argument("--resume", action="store_true", help="체크포인트(%s) 기준 이미 완료/이미 DB에 있는 종목 건너뜀" % PROG_PATH)
    ap.add_argument("--force", action="store_true", help="이미 company_product_mix에 있어도 재수집")
    ap.add_argument("--limit", type=int, default=0, help="샘플 검증용 상한 개수")
    ap.add_argument("--sleep", type=float, default=0.4, help="종목 간 딜레이(초), DART 부하 방지")
    args = ap.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all:
        codes = _load_universe_codes()
    else:
        ap.error("--codes 또는 --all 중 하나는 지정해야 합니다")
        return

    if args.limit:
        codes = codes[: args.limit]

    already_in_db: set[str] = set()
    if not args.force:
        conn = _get_conn()
        already_in_db = {r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM company_product_mix").fetchall()}
        conn.close()

    prog = _load_progress() if args.resume else {"done": [], "failed": {}}
    done_set = set(prog.get("done", []))
    failed_map: dict = prog.get("failed", {})

    corp_map = load_corp_map(codes)
    log.info("대상 %d종목, corp_code 매핑 %d건", len(codes), len(corp_map))

    stats = {"ok": 0, "skip": 0, "fail": 0, "no_corp": 0}
    for idx, code in enumerate(codes):
        if code in done_set or (not args.force and code in already_in_db):
            stats["skip"] += 1
            continue
        cc = corp_map.get(code)
        if not cc:
            stats["no_corp"] += 1
            failed_map[code] = "corp_code 매핑 없음"
            continue
        try:
            ok = collect_one(code, cc)
            if ok:
                stats["ok"] += 1
                done_set.add(code)
                failed_map.pop(code, None)
            else:
                stats["fail"] += 1
                failed_map[code] = "파싱/다운로드 실패 (로그 참조)"
        except Exception as e:
            log.exception("%s: 수집 실패 - %s", code, e)
            stats["fail"] += 1
            failed_map[code] = str(e)

        if (idx + 1) % 25 == 0:
            log.info("진행 %d/%d — ok=%d skip=%d fail=%d no_corp=%d",
                      idx + 1, len(codes), stats["ok"], stats["skip"], stats["fail"], stats["no_corp"])
            prog["done"] = sorted(done_set)
            prog["failed"] = failed_map
            _save_progress(prog)

        if len(_exhausted) >= len(KEYS) and KEYS:
            log.error("DART API 키 전체 소진 — 중단 (진행상황 저장됨, 나중에 --resume 으로 재개)")
            break

        time.sleep(args.sleep)

    prog["done"] = sorted(done_set)
    prog["failed"] = failed_map
    _save_progress(prog)
    log.info("완료: ok=%d skip=%d fail=%d no_corp=%d (체크포인트: %s) — 키 소진 %d/%d",
              stats["ok"], stats["skip"], stats["fail"], stats["no_corp"], PROG_PATH,
              len(_exhausted), len(KEYS))


if __name__ == "__main__":
    main()
