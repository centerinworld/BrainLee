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

DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
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

def _mark_exhausted(key: str, reason: str = ""):
    if key not in _exhausted:
        _exhausted.add(key)
        log.warning("키 소진 처리: ...%s (%s) — 남은 키 %d개", key[-4:], reason, len(KEYS) - len(_exhausted))

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

    # 목차(TOC)에 동일 제목이 먼저 나오는 경우가 많아 첫 등장(find) 대신
    # 마지막 등장(rfind, 본문)을 사용한다 — TOC 항목은 항상 문서 앞부분에 위치.
    section_start = -1
    for pat in section_patterns:
        idx = txt.rfind(pat)
        if idx >= 0:
            section_start = idx
            break

    if section_start < 0:
        return None, ""

    # 섹션 종료 기준: 다음 섹션 시작 또는 최대 4,000자
    # 원재료 현황 테이블은 보통 1,000~2,000자 이내
    # "제조"는 "자동차 부품 제조"처럼 표 안의 설명 문구에도 흔히 등장해
    # 실제 매입액/합계 행이 나오기 전에 섹션을 잘라버리는 오탐이 많아 제외한다.
    NEXT_SECTION_PATTERNS = [
        "나. ", "나.", "2. 생산", "2. 판매", "생산설비 현황",
        "다. ", "라. ", "마. ", "3. ", "4. ", "5. ",
        "<표>", "이하 생략", "해당없음"
    ]
    section_end = section_start + 4000
    search_from = section_start + 50  # 헤더 자체는 건너뜀
    # 여러 종료 패턴 중 텍스트상 가장 먼저 등장하는(=가장 안전한) 위치를 사용한다.
    for end_pat in NEXT_SECTION_PATTERNS:
        idx = txt.find(end_pat, search_from)
        if 0 < idx < section_end:
            section_end = idx

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
    # 빈 칸 컬럼이 "-"로 채워진 표 대응: "합계 - - 7,802,154 100.0" 형태를 위해
    # 총계/합계 키워드와 숫자 사이에 "-" 플레이스홀더가 여러 개 낄 수 있음을 허용한다.
    DASH_GAP = r'(?:-\s+)*'

    # 패턴1: "총 계 877,247,319 100.0" / "총계 - - 877,247,319 100.0"
    m = re.search(rf'총\s*계\s+{DASH_GAP}([\d,]+)\s+100', section)
    if m:
        val = float(m.group(1).replace(",", ""))
        return val * unit_multiplier, f"총계({unit_label})"

    # 패턴2: "합계 44,844 100%" / "합계 - - 44,844 100%"
    m = re.search(rf'합\s*계\s+{DASH_GAP}([\d,]+)\s+100', section)
    if m:
        val = float(m.group(1).replace(",", ""))
        return val * unit_multiplier, f"섹션합계({unit_label})"

    # 패턴3: "총 계 877,247,319" — 맥락 검증: 주변 200자에 매입/구매/원료 언급 필요
    for m in re.finditer(rf'총\s*계\s+{DASH_GAP}([\d,]{{4,}})', section):
        ctx_start = max(0, m.start() - 200)
        ctx = section[ctx_start:m.end()]
        if any(kw in ctx for kw in ["매입", "구매", "원료", "재료", "Material", "material"]):
            val = float(m.group(1).replace(",", ""))
            return val * unit_multiplier, f"총계({unit_label})"

    # 패턴4: "합 계 44,844" — 동일 맥락 검증
    for m in re.finditer(rf'합\s*계\s+{DASH_GAP}([\d,]{{4,}})', section):
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

    # 패턴6: "품목 ... 매입액 비율(%)" 행별 테이블 — 합계 행이 없을 때
    # 각 행의 (금액, 비율%) 쌍을 모아 비율 합이 100%에 근접하면 금액을 합산한다.
    if "매입액" in section and ("비율" in section or "%" in section):
        pairs = re.findall(r'([\d,]{4,})\s+(-?\d{1,3}(?:\.\d+)?)\s*%?\s', section)
        if pairs:
            amounts, pcts = [], []
            for amt_s, pct_s in pairs:
                pct = float(pct_s)
                if 0 < pct <= 100:
                    amounts.append(float(amt_s.replace(",", "")))
                    pcts.append(pct)
            if amounts and 90 <= sum(pcts) <= 110:
                val = sum(amounts)
                return val * unit_multiplier, f"행별합산({unit_label})"

    # 패턴7: "원재료명 매입액 주요매입처" 형태의 단순 2~3열 표(비율/총계 없음).
    # 헤더 직후 짧은 구간(600자)에서 4~6자리 콤마숫자를 모두 합산한다(품목이 보통 5개 이하).
    hdr = re.search(r'원재료명?\s*매입액|매입액\s*주요\s*매입처', section)
    if hdr and "비율" not in section[:hdr.end() + 50]:
        window = section[hdr.end():hdr.end() + 600]
        nums = [float(n.replace(",", "")) for n in re.findall(r'(?<!\d)([\d]{1,3}(?:,\d{3})+)(?!\d)', window)]
        if 1 <= len(nums) <= 6:
            val = sum(nums)
            return val * unit_multiplier, f"품목합산({unit_label})"

    return None, ""

_EXPENSE_NOTE_HEADERS = ["비용의 성격별 분류", "성격별 분류", "비용의 성격별"]
# 회사마다 표현이 다르지만 전부 "원재료/상품 매입·사용" 성격의 항목이다.
# 구체적일수록 우선순위를 높게 둔다(상품매입만 있는 유통/상사업체와 혼동 방지).
_EXPENSE_NOTE_KEYWORDS = [
    "원재료 매입액",
    "원재료 등의 사용액 및 상품 순매입액",
    "원재료와 저장품의 사용액",
    "원재료 및 저장품의 사용액",
    "원재료와 소모품의 사용액",
    "원재료의 사용액",
    "원재료비",
    "재료비",
    "상품의 매입 등",
    "상품의 매입",
    "재고자산의 매입액",
]

def extract_from_expense_note(txt: str) -> tuple[float | None, str]:
    """K-IFRS 필수 주석 '비용의 성격별 분류'에서 원재료/상품 매입 관련 항목 추출.
    이 주석은 거의 모든 상장사가 재무제표 주석에 의무 공시하므로
    사업의 내용(자유서술) 섹션보다 커버리지가 훨씬 높다.
    """
    hdr_idx = -1
    for hdr in _EXPENSE_NOTE_HEADERS:
        idx = txt.rfind(hdr)
        if idx >= 0:
            hdr_idx = idx
            break
    if hdr_idx < 0:
        return None, ""

    window_raw = txt[hdr_idx:hdr_idx + 6000]
    window = _clean_html(window_raw)
    unit_multiplier = _detect_unit_to_won(window[:200])
    unit_label = {1e8: "억원", 1e6: "백만원", 1e3: "천원"}.get(unit_multiplier, "원")

    for kw in _EXPENSE_NOTE_KEYWORDS:
        m = re.search(rf'{re.escape(kw)}\s+\(?(-?[\d,]{{6,}})\)?', window)
        if m:
            val = float(m.group(1).replace(",", ""))
            if val <= 0:
                continue
            return val * unit_multiplier, f"성격별분류({kw})"
    return None, ""

# DART document.xml에는 인라인 XBRL 태그(ACODE)가 이미 내장돼 있는 경우가 많다.
# 회사마다 한글 라벨(원재료 매입액/원재료의 사용액/...)은 제각각이지만, IFRS 표준
# 택소노미 코드는 회사와 무관하게 동일해서 훨씬 안정적으로 값을 찾을 수 있다.
# 다만 XBRL 태깅 자체가 없는 문서도 많아(구형 서식 등) 키워드 매칭의 완전한 대체는 아니고
# "우선 시도, 없으면 기존 방식으로 폴백"하는 보강재로 사용한다.
_XBRL_CODES = [
    "ifrs-full_RawMaterialsAndConsumablesUsed",   # 원재료와 소모품의 사용액 (제조업)
    "ifrs-full_CostOfMerchandiseSold",            # 상품의 매입 등 (유통/상사)
]

def extract_from_xbrl_tag(txt: str, year: int, codes: list[str] | None = None) -> tuple[float | None, str]:
    """인라인 XBRL ACODE 태그에서 표준 IFRS 계정값 추출.
    ACONTEXT가 "CFY{year}dFY_...ConsolidatedMember"(또는 SeparateMember)로 끝나는,
    즉 매출원가/판관비 등으로 더 쪼개지지 않은 '합계' 셀만 채택한다.
    연결(Consolidated) 재무제표를 별도(Separate)보다 우선한다.
    원재료(RawMaterialsAndConsumablesUsed)와 상품매입(CostOfMerchandiseSold)이
    둘 다 태깅된 회사(제조+유통 겸업 등)는 두 계정을 합산한다.
    codes를 지정하면 해당 계정만 탐색한다(여러 첨부파일에 계정이 나뉜 경우
    "원재료" 태그만 우선 탐색하기 위함).
    """
    codes = codes or _XBRL_CODES
    def _find(code: str, member: str) -> float | None:
        pat = (
            rf'<TE ACODE="{re.escape(code)}" '
            rf'ACONTEXT="CFY{year}dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_'
            rf'ifrs-full_{member}"[^>]*>\(?(-?[\d,]+)\)?</TE>'
        )
        m = re.search(pat, txt)
        if not m:
            return None
        raw = m.group(1).replace(",", "")
        val = abs(float(raw))
        if val <= 0:
            return None
        # ADECIMAL 속성은 DART 인라인 태깅에서 신뢰할 수 없어(관찰상 총계 셀에 0으로
        # 잘못 찍히는 경우가 있음) 기존 방식과 동일하게 근처 "(단위 : 천원)" 문구로
        # 배율을 판단한다.
        window_before = _clean_html(txt[max(0, m.start() - 5000):m.start()])
        unit_idx = window_before.rfind("단위")
        unit_ctx = window_before[unit_idx:unit_idx + 20] if unit_idx >= 0 else ""
        unit_multiplier = _detect_unit_to_won(unit_ctx) if unit_ctx else 1e3
        return val * unit_multiplier

    for member in ("ConsolidatedMember", "SeparateMember"):
        found = {code: _find(code, member) for code in codes}
        found = {k: v for k, v in found.items() if v}
        if found:
            total = sum(found.values())
            return total, f"XBRL({'+'.join(found)}/{member})"
    return None, ""

def fetch_annual_report_rcept(corp_code: str, year: int, key: str) -> str | None:
    """해당 연도의 사업보고서 rcept_no 반환 (하위호환용, fetch_periodic_reports 사용 권장)"""
    reports = fetch_periodic_reports(corp_code, year, key)
    return reports.get("annual")

# report_nm에 등장하는 월 → 기간 타입. 사업보고서(연간)를 최우선으로, 그다음은
# 누적 데이터가 가장 많은 3분기보고서 → 반기보고서 → 1분기보고서 순으로 폴백한다.
_PERIOD_PRIORITY = ["annual", "Q3", "H1", "Q1"]
_PERIOD_BY_MONTH = {"12": "annual", "09": "Q3", "06": "H1", "03": "Q1"}

def fetch_periodic_reports(corp_code: str, year: int, key: str) -> dict:
    """해당 연도의 사업보고서/반기보고서/분기보고서 rcept_no를 기간타입별로 반환.
    반환: {"annual": rcept_no, "Q3": rcept_no, "H1": rcept_no, "Q1": rcept_no} (있는 것만)
    """
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
        return {}

    data = resp.json()
    if data.get("status") == "020":
        _mark_exhausted(key, data.get("message", "일일 한도 초과"))
        return {}

    out = {}
    for item in data.get("list", []):
        nm = item.get("report_nm", "")
        m = re.search(rf'({year})\.(\d{{2}})', nm)
        if not m:
            continue
        month = m.group(2)
        period = _PERIOD_BY_MONTH.get(month)
        if not period:
            continue
        if ("사업보고서" in nm and period == "annual") or \
           ("반기보고서" in nm and period == "H1") or \
           ("분기보고서" in nm and period in ("Q1", "Q3")):
            out.setdefault(period, item["rcept_no"])
    return out

def download_and_extract(rcept_no: str, key: str, year: int | None = None) -> float | None:
    """rcept_no로 문서 다운로드 후 원재료 매입액 추출 (원 단위 반환)"""
    resp = requests.get(f"{DART_BASE}/document.xml", params={
        "crtfc_key": key, "rcept_no": rcept_no
    }, timeout=20)
    time.sleep(0.3)

    if resp.status_code != 200:
        return None

    if resp.content[:1] != b"P":  # ZIP은 'PK'로 시작 — 아니면 DART 오류 JSON일 가능성
        try:
            data = resp.json()
            if data.get("status") == "020":
                _mark_exhausted(key, data.get("message", "일일 한도 초과"))
        except Exception:
            pass
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except Exception:
        return None

    # 모든 XML 파일에서 시도 (압축 전 크기 기준 정렬 — read() 호출 없이)
    files = sorted(zf.namelist(), key=lambda x: zf.getinfo(x).file_size, reverse=True)
    xml_texts = []
    for fname in files:
        if not fname.endswith(".xml"):
            continue
        try:
            xml_texts.append((fname, zf.read(fname).decode("utf-8", errors="ignore")))
        except Exception:
            continue

    # 1차: 여러 첨부 XML에 계정이 나뉘어 태깅된 경우(제조+유통 겸업 등) 대비,
    # "원재료" XBRL 태그를 zip 전체에서 우선 탐색한다(가장 신뢰도 높은 신호).
    if year is not None:
        for fname, txt in xml_texts:
            if "ifrs-full_RawMaterialsAndConsumablesUsed" not in txt:
                continue
            val, src = extract_from_xbrl_tag(txt, year, codes=["ifrs-full_RawMaterialsAndConsumablesUsed"])
            if val is not None and val > 0:
                log.debug("  추출 성공 [%s] → %.0f원 (%s)", fname, val, src)
                return val

    # 2차: 키워드 기반(사업내용 서술/비용의 성격별 분류) — "상품매입" 단독 XBRL보다 우선.
    # "상품의 매입" 태그만 있고 "원재료" 태그가 없는 경우, 유통 자회사 등 일부 사업만
    # 반영된 값일 수 있어 실제 "원재료 매입액" 텍스트가 있으면 그쪽을 신뢰한다.
    for fname, txt in xml_texts:
        val, src = extract_material_purchase(txt)
        if val is None:
            val, src = extract_from_expense_note(txt)
        if val is not None and val > 0:
            log.debug("  추출 성공 [%s] → %.0f원 (%s)", fname, val, src)
            return val

    # 3차(최후): "상품의 매입" 등 XBRL 단독 태그 — 원재료 신호도 키워드 매치도 없을 때만.
    if year is not None:
        for fname, txt in xml_texts:
            val, src = extract_from_xbrl_tag(txt, year)
            if val is not None and val > 0:
                log.debug("  추출 성공 [%s] → %.0f원 (%s)", fname, val, src)
                return val

    return None

def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_material_purchase (
            stock_code TEXT,
            year INTEGER,
            period_type TEXT DEFAULT 'annual',
            report_type TEXT DEFAULT 'CFS',
            material_purchase_krw REAL,
            unit_label TEXT,
            rcept_no TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, year, period_type, report_type)
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
            # 이미 수집된 경우(연간이든 분기 폴백이든) 스킵
            exists = conn.execute(
                "SELECT 1 FROM dart_material_purchase WHERE stock_code=? AND year=?",
                (sc, year)
            ).fetchone()
            if exists:
                skip += 1
                continue

            # 사업보고서/반기보고서/분기보고서 rcept_no 조회
            reports = fetch_periodic_reports(corp_code, year, key)
            if not reports:
                err += 1
                continue

            # 연간(annual) → 3분기 → 반기 → 1분기 순으로 시도, 첫 성공만 채택
            mkt_cap = row["market_cap"] if "market_cap" in row.keys() else None
            mkt_cap_won = (mkt_cap * 1e8) if mkt_cap else None
            val = None
            used_period = None
            used_rcept = None
            for period in _PERIOD_PRIORITY:
                rcept_no = reports.get(period)
                if not rcept_no:
                    continue
                val = download_and_extract(rcept_no, key, year)
                sane = val is not None and 1e9 <= val <= 5e13
                if sane and mkt_cap_won and val > mkt_cap_won * 20:
                    sane = False
                if sane:
                    used_period, used_rcept = period, rcept_no
                    break
                val = None

            if val is None:
                err += 1
                continue

            for _retry in range(5):
                try:
                    conn.execute("""
                        INSERT INTO dart_material_purchase
                            (stock_code, year, period_type, material_purchase_krw, unit_label, rcept_no)
                        VALUES (?, ?, ?, ?, '원', ?)
                        ON CONFLICT(stock_code, year, period_type, report_type) DO UPDATE SET
                            material_purchase_krw=excluded.material_purchase_krw,
                            rcept_no=excluded.rcept_no,
                            collected_at=CURRENT_TIMESTAMP
                    """, (sc, year, used_period, val, used_rcept))
                    conn.commit()
                    ok += 1
                    tag = "" if used_period == "annual" else f"[{used_period} 폴백]"
                    log.info("[%d/%d] %s(%s) %d년 → %.0f억원 %s",
                             i+1, len(targets), sname, sc, year, val/1e8, tag)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" in str(e) and _retry < 4:
                        time.sleep(8 * (_retry + 1))
                    else:
                        log.warning("저장 실패 %s/%s: %s", sc, year, e)
                        err += 1
                        break

        if i % 30 == 0:
            conn.commit()
        if (i + 1) % 100 == 0:
            log.info("진행 [%d/%d] ok=%d skip=%d err=%d no_corp=%d 남은키=%d",
                      i + 1, len(targets), ok, skip, err, no_corp, len(KEYS) - len(_exhausted))
        if len(_exhausted) >= len(KEYS):
            log.error("API 키 전부 소진 — 중단 [%d/%d]", i + 1, len(targets))
            break

    conn.commit()
    conn.close()
    log.info("완료 — ok=%d skip=%d err=%d no_corp=%d", ok, skip, err, no_corp)

if __name__ == "__main__":
    main()
