"""
DART 사업보고서 '원재료 및 생산설비' 섹션에서 연간 원재료 매입액 수집
- 섹션 위치: '가. 주요 원재료 등의 현황' 테이블의 합계 행
- 단위: 섹션 헤더에 명시된 단위(백만원/억원/원 등) 자동 감지
- 저장: dart_cost_quarterly (is_annual=1, quarter=4) 및 material_cost_krw 컬럼
"""
import sqlite3, zipfile, io, re, requests, time, os, logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
KEYS = get_dart_api_keys()
_exhausted: set = set()
_ki = 0

def _next_key():
    global _ki
    for _ in range(len(KEYS)):
        k = KEYS[_ki % len(KEYS)]; _ki += 1
        if k not in _exhausted:
            return k
    return None

def _get_conn():
    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn

def _clean_html(txt: str) -> str:
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'&[a-zA-Z#\d]+;', ' ', txt)
    return re.sub(r'\s+', ' ', txt).strip()

def _detect_unit_to_won(text_around: str) -> float:
    """단위 텍스트에서 1원당 승수 반환"""
    t = text_around.lower()
    if "억원" in t or "hundred million" in t:
        return 1e8
    if "백만원" in t or "million" in t:
        return 1e6
    if "천원" in t or "thousand" in t:
        return 1e3
    return 1.0  # 원 단위 기본

def extract_material_purchase(txt: str) -> tuple[float | None, str]:
    """
    DART 문서 XML에서 원재료 매입액 합계 추출.
    반환: (원화 금액, 출처 설명) 또는 (None, "")
    """
    # 섹션 헤더 패턴들
    section_patterns = [
        "원재료 및 생산설비",
        "주요 원재료 등의 현황",
        "원재료 현황",
        "원재료 매입",
    ]

    section_start = -1
    for pat in section_patterns:
        idx = txt.find(pat)
        if idx >= 0:
            section_start = idx
            break

    if section_start < 0:
        return None, ""

    # 섹션 종료 기준: 다음 섹션 시작 또는 최대 4,000자
    # 원재료 현황 테이블은 보통 1,000~2,000자 이내
    NEXT_SECTION_PATTERNS = [
        "나. ", "나.", "2. 생산", "2. 판매", "제조", "생산설비 현황",
        "다. ", "라. ", "마. ", "3. ", "4. ", "5. ",
        "<표>", "이하 생략", "해당없음"
    ]
    section_end = section_start + 4000
    search_from = section_start + 50  # 헤더 자체는 건너뜀
    for end_pat in NEXT_SECTION_PATTERNS:
        idx = txt.find(end_pat, search_from)
        if 0 < idx < section_end:
            section_end = idx
            break

    section_raw = txt[section_start:section_end]
    section = _clean_html(section_raw)

    # 섹션이 너무 짧으면 (300자 미만) 원재료 테이블이 없을 가능성
    if len(section) < 100:
        return None, ""

    # 단위 감지 (섹션 처음 300자 내에서)
    header_chunk = section[:300]
    unit_multiplier = _detect_unit_to_won(header_chunk)
    if unit_multiplier == 1e8:
        unit_label = "억원"
    elif unit_multiplier == 1e6:
        unit_label = "백만원"
    elif unit_multiplier == 1e3:
        unit_label = "천원"
    else:
        unit_label = "원"

    # 합계/총계 행 찾기 (다양한 패턴)
    # 패턴1: "총 계 877,247,319 100.0"
    m = re.search(r'총\s*계\s+([\d,]+)\s+100', section)
    if m:
        val = float(m.group(1).replace(",", ""))
        return val * unit_multiplier, f"총계({unit_label})"

    # 패턴2: "합계 44,844 100%"
    m = re.search(r'합\s*계\s+([\d,]+)\s+100', section)
    if m:
        val = float(m.group(1).replace(",", ""))
        return val * unit_multiplier, f"섹션합계({unit_label})"

    # 패턴3: "총 계 877,247,319" — 맥락 검증: 주변 200자에 매입/구매/원료 언급 필요
    for m in re.finditer(r'총\s*계\s+([\d,]{4,})', section):
        ctx_start = max(0, m.start() - 200)
        ctx = section[ctx_start:m.end()]
        if any(kw in ctx for kw in ["매입", "구매", "원료", "재료", "Material", "material"]):
            val = float(m.group(1).replace(",", ""))
            return val * unit_multiplier, f"총계({unit_label})"

    # 패턴4: "합 계 44,844" — 동일 맥락 검증
    for m in re.finditer(r'합\s*계\s+([\d,]{4,})', section):
        ctx_start = max(0, m.start() - 200)
        ctx = section[ctx_start:m.end()]
        if any(kw in ctx for kw in ["매입", "구매", "원료", "재료", "Material", "material"]):
            val = float(m.group(1).replace(",", ""))
            return val * unit_multiplier, f"섹션합계({unit_label})"

    # 패턴5: "원재료 매입액은 XXX백만원"  (서술형)
    m = re.search(r'원재료\s*매입액[은이]?\s+([\d,]+)\s*(백만원|억원|천원|원)', section)
    if m:
        val = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        mult = {"백만원": 1e6, "억원": 1e8, "천원": 1e3, "원": 1.0}.get(unit, 1e6)
        return val * mult, f"서술형({unit})"

    return None, ""

def fetch_annual_report_rcept(corp_code: str, year: int, key: str) -> str | None:
    """해당 연도의 사업보고서 rcept_no 반환"""
    resp = requests.get(f"{DART_BASE}/list.json", params={
        "crtfc_key": key,
        "corp_code": corp_code,
        "bgn_de": f"{year}0101",
        "end_de": f"{year+1}0401",
        "pblntf_ty": "A",
        "page_count": 10,
    }, timeout=15)
    time.sleep(0.2)

    if resp.status_code != 200:
        return None

    for item in resp.json().get("list", []):
        nm = item.get("report_nm", "")
        if "사업보고서" in nm and f"{year}" in nm:
            return item["rcept_no"]
    return None

def download_and_extract(rcept_no: str, key: str) -> float | None:
    """rcept_no로 문서 다운로드 후 원재료 매입액 추출 (원 단위 반환)"""
    resp = requests.get(f"{DART_BASE}/document.xml", params={
        "crtfc_key": key, "rcept_no": rcept_no
    }, timeout=20)
    time.sleep(0.3)

    if resp.status_code != 200:
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception:
        return None

    # 모든 XML 파일에서 시도 (압축 전 크기 기준 정렬 — read() 호출 없이)
    files = sorted(zf.namelist(), key=lambda x: zf.getinfo(x).file_size, reverse=True)
    for fname in files:
        if not fname.endswith(".xml"):
            continue
        try:
            txt = zf.read(fname).decode("utf-8", errors="ignore")
        except Exception:
            continue

        val, src = extract_material_purchase(txt)
        if val is not None and val > 0:
            log.debug("  추출 성공 [%s] → %.0f원 (%s)", fname, val, src)
            return val

    return None

def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_material_purchase (
            stock_code TEXT,
            year INTEGER,
            report_type TEXT DEFAULT 'CFS',
            material_purchase_krw REAL,
            unit_label TEXT,
            rcept_no TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, year, report_type)
        )
    """)
    conn.commit()

def load_corp_map() -> dict:
    xml_path = "/tmp/CORPCODE.xml"
    import xml.etree.ElementTree as ET
    corp_map = {}
    if not os.path.exists(xml_path):
        key = _next_key()
        if not key:
            return corp_map
        try:
            resp = requests.get(
                f"{DART_BASE}/corpCode.xml",
                params={"crtfc_key": key},
                timeout=30,
            )
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extract("CORPCODE.xml", "/tmp/")
        except Exception as exc:
            log.warning("corpCode.xml 다운로드 실패: %s", exc)
            return corp_map
    if os.path.exists(xml_path):
        tree = ET.parse(xml_path)
        for item in tree.getroot().findall(".//list"):
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc and cc:
                corp_map[sc] = cc
    return corp_map

# 반복 오염 종목 영구 블랙리스트 (파서 오매칭으로 비현실적 값 반복 재발)
PARSE_BLACKLIST = {
    "032680",  # 소프트센 — 여러 테이블 1e15~1e17원대 극단값 반복
    "065150",  # 대산F&B — 19,508억원 오파싱 반복
}

def get_targets(conn) -> list:
    """DART corpCode가 있는 국내 보통주 전체를 우선순위대로 수집한다.

    기존에는 제조/소재 일부 섹터와 시가총액 구간으로 대상을 좁혀 2020~현재
    장기 보강 시 누락이 컸다. 원재료 매입액은 제조업에서 주로 나오지만,
    섹터 분류가 비어 있거나 잘못 매핑된 종목도 있어 전체 보통주를 대상으로
    하고 제조 관련 섹터를 먼저 처리한다.
    """
    return conn.execute("""
        SELECT su.stock_code, su.stock_name, su.sector_large, su.market_cap
        FROM stock_universe su
        WHERE su.market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND su.stock_name NOT LIKE '%스팩%'
          AND su.stock_name NOT LIKE '%SPAC%'
          AND su.stock_name NOT LIKE '%리츠%'
          AND su.stock_name NOT LIKE '%ETN%'
          AND su.stock_name NOT LIKE '%ETF%'
          AND su.stock_name NOT LIKE '%우선주%'
        ORDER BY
          CASE
            WHEN su.sector_large IN ('소재', '산업재', '필수소비재', '경기소비재', 'IT', '에너지') THEN 0
            WHEN su.sector_large IS NULL OR su.sector_large = '' THEN 1
            ELSE 2
          END,
          su.market_cap DESC
    """).fetchall()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, default=[2022, 2023, 2024])
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--stock", type=str, help="특정 종목코드만 처리")
    args = parser.parse_args()

    conn = _get_conn()
    ensure_table(conn)
    corp_map = load_corp_map()

    if args.stock:
        targets = conn.execute(
            "SELECT stock_code, stock_name, sector_large, market_cap FROM stock_universe WHERE stock_code=?",
            (args.stock,)
        ).fetchall()
    else:
        targets = get_targets(conn)[:args.limit]

    log.info("대상 종목: %d개, 연도: %s", len(targets), args.years)

    ok = skip = err = no_corp = 0

    for i, row in enumerate(targets):
        sc = row["stock_code"]
        sname = row["stock_name"]
        if sc in PARSE_BLACKLIST:
            log.debug("블랙리스트 스킵: %s(%s)", sname, sc)
            skip += 1
            continue
        corp_code = corp_map.get(sc)
        if not corp_code:
            no_corp += 1
            continue

        key = _next_key()
        if not key:
            log.error("API 키 전부 소진 — 중단")
            break

        for year in args.years:
            # 이미 수집된 경우 스킵
            exists = conn.execute(
                "SELECT 1 FROM dart_material_purchase WHERE stock_code=? AND year=?",
                (sc, year)
            ).fetchone()
            if exists:
                skip += 1
                continue

            # 사업보고서 rcept_no 찾기
            rcept_no = fetch_annual_report_rcept(corp_code, year, key)
            if not rcept_no:
                err += 1
                continue

            # 문서 다운로드 및 추출
            val = download_and_extract(rcept_no, key)
            # 최소 10억원 이상, 최대 50조원 이하만 저장 (노이즈/오파싱 제거 — 단위오류로 비현실적 값 방지)
            # 추가: 시가총액의 20배를 초과하면 파싱오류로 간주(소형주 매출 규모로는 불가능한 수치)
            mkt_cap = row["market_cap"] if "market_cap" in row.keys() else None
            mkt_cap_won = (mkt_cap * 1e8) if mkt_cap else None
            sane = val is not None and 1e9 <= val <= 5e13
            if sane and mkt_cap_won and val > mkt_cap_won * 20:
                sane = False
            if sane:
                for _retry in range(5):
                    try:
                        conn.execute("""
                            INSERT INTO dart_material_purchase
                                (stock_code, year, material_purchase_krw, unit_label, rcept_no)
                            VALUES (?, ?, ?, '원', ?)
                            ON CONFLICT(stock_code, year, report_type) DO UPDATE SET
                                material_purchase_krw=excluded.material_purchase_krw,
                                rcept_no=excluded.rcept_no,
                                collected_at=CURRENT_TIMESTAMP
                        """, (sc, year, val, rcept_no))
                        conn.commit()
                        ok += 1
                        log.info("[%d/%d] %s(%s) %d년 → %.0f억원",
                                 i+1, len(targets), sname, sc, year, val/1e8)
                        break
                    except sqlite3.OperationalError as e:
                        if "locked" in str(e) and _retry < 4:
                            time.sleep(8 * (_retry + 1))
                        else:
                            log.warning("저장 실패 %s/%s: %s", sc, year, e)
                            err += 1
                            break
            else:
                err += 1

        if i % 30 == 0:
            conn.commit()

    conn.commit()
    conn.close()
    log.info("완료 — ok=%d skip=%d err=%d no_corp=%d", ok, skip, err, no_corp)

if __name__ == "__main__":
    main()
