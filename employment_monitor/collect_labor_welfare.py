"""
근로복지공단 고용·산재보험현황정보 API 수집기
B490001/gySjbPstateInfoService/getGySjBoheomBsshItem
- 고용보험 가입 사업장별 상시인원수(sangsiInwonCnt) 수집
- 우리 stock_universe 상장사와 이름 매칭 → 사업장별 인원 합산
- employment.db wlb_monthly 테이블에 월별 저장

실행: python3 employment_monitor/collect_labor_welfare.py [--test]
"""

import os, re, sys, time, sqlite3, logging, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
STOCK_DB      = os.path.join(BASE_DIR, '..', 'stock.db')
EMP_DB        = os.path.join(BASE_DIR, 'employment.db')
API_KEY       = '93b5be4d33f6d76af92ead610f161975e4dca7cd021b60e97d40348ab0d824da'
BASE_URL      = 'https://apis.data.go.kr/B490001/gySjbPstateInfoService'
PAGE_SIZE     = 1000
THREADS       = 8         # 병렬 스레드 수
RETRY         = 3         # 재시도 횟수
INS_TYPE      = '1'       # 고용보험만 (산재='2', 전체=없음)

# ─── DB 초기화 ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(EMP_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wlb_monthly (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            data_ym     TEXT NOT NULL,           -- 'YYYYMM' 형식 (수집월)
            stock_code  TEXT NOT NULL,
            stock_name  TEXT NOT NULL,
            total_workers INTEGER DEFAULT 0,     -- sangsiInwonCnt 합산
            workplace_cnt INTEGER DEFAULT 0,     -- 매칭된 사업장 수
            fetched_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(data_ym, stock_code)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_wlb_ym ON wlb_monthly(data_ym)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_wlb_sc ON wlb_monthly(stock_code, data_ym)")
    conn.commit()
    conn.close()
    logger.info("wlb_monthly table ready")

# ─── 회사명 정규화 ─────────────────────────────────────────
_RM_SUFFIX = re.compile(
    r'주식회사|\(주\)|㈜|(\(주\))|株式会社|코리아|korea|KOREA'
    r'|제\d+[공장사업소법인]|[가-힣]+공장|[가-힣]+사업소|[가-힣]+연구소|[가-힣]+지점'
    r'|headquarters|HQ|\s+', flags=re.IGNORECASE
)

def _clean(s: str) -> str:
    s = _RM_SUFFIX.sub('', s or '')
    return s.strip().upper()

# ─── stock_universe 로드 ───────────────────────────────────
def load_stock_universe():
    conn = sqlite3.connect(STOCK_DB)
    rows = conn.execute(
        "SELECT stock_code, stock_name FROM stock_universe WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ') AND (secugrp_nm = '주권' OR secugrp_nm IS NULL)"
    ).fetchall()
    conn.close()
    result = {}  # clean_name → (code, name)
    for code, name in rows:
        c = _clean(name)
        if len(c) >= 2:
            result[c] = (code, name)
    # 이름 길이 내림차순 정렬 → 최장 일치 우선 (삼성전자 > 삼성 순서 보장)
    result = dict(sorted(result.items(), key=lambda x: -len(x[0])))
    logger.info(f"Stock universe loaded: {len(result)} companies")
    return result, {v[0]: v[1] for v in result.values()}  # clean→(code,name), code→name


# ─── 사업자번호 맵 로드 ────────────────────────────────────
def load_bizno_map() -> dict:
    """
    사업자등록번호(10자리) → stock_code 매핑.
    우선순위:
      1) employment_company.bizr_no (fetch_employment_insurance.py가 수집한 10자리)
      2) stock_bizno_map.biz_no_6 (6자리 앞자리 → 10자리 확인용 보조)

    ※ 사용자 제안: 사업자번호 10자리가 곧 고용/산재보험 사업장의 사업자등록번호.
       전체 스캔 아이템의 saeopjaDrno(10자리)와 직접 비교하면 이름 매칭보다 정확.
    """
    emp_conn = sqlite3.connect(EMP_DB)

    # 1) employment_company 테이블의 bizr_no (10자리, 가장 정확)
    bizno_map: dict = {}  # bizr_no(10자리) → (stock_code, stock_name)
    try:
        rows = emp_conn.execute(
            "SELECT DISTINCT bizr_no, stock_code, stock_name "
            "FROM employment_company WHERE bizr_no IS NOT NULL AND LENGTH(bizr_no)=10"
        ).fetchall()
        for bizr, code, name in rows:
            bizno_map[bizr] = (code, name)
        logger.info(f"bizno_map from employment_company: {len(bizno_map)}개")
    except Exception as e:
        logger.warning(f"employment_company bizr_no 로드 실패: {e}")

    # 2) stock_bizno_map 테이블의 biz_no_6 (6자리 앞자리, 보조)
    # → 정확도가 낮아 bizno_map에 없는 종목만 추가 (이름 매칭 병행 확인용)
    prefix_map: dict = {}  # biz_no_6(6자리) → (stock_code, stock_name)
    try:
        rows = emp_conn.execute(
            "SELECT biz_no_6, stock_code, stock_name FROM stock_bizno_map "
            "WHERE biz_no_6 IS NOT NULL AND LENGTH(biz_no_6)=6"
        ).fetchall()
        for prefix, code, name in rows:
            prefix_map[prefix] = (code, name)
        logger.info(f"prefix_map from stock_bizno_map: {len(prefix_map)}개")
    except Exception as e:
        logger.warning(f"stock_bizno_map 로드 실패: {e}")

    emp_conn.close()
    return bizno_map, prefix_map

# ─── 단일 페이지 요청 ──────────────────────────────────────
def _fetch_page(page: int) -> list[dict]:
    for attempt in range(RETRY):
        try:
            r = requests.get(f'{BASE_URL}/getGySjBoheomBsshItem', params={
                'serviceKey': API_KEY,
                'pageNo': page,
                'numOfRows': PAGE_SIZE,
                'opaBoheomFg': INS_TYPE,
            }, timeout=30)
            root = ET.fromstring(r.text)
            return [
                {c.tag: c.text for c in item}
                for item in root.findall('.//item')
            ]
        except Exception as e:
            if attempt == RETRY - 1:
                logger.warning(f"Page {page} failed after {RETRY} retries: {e}")
                return []
            time.sleep(1 * (attempt + 1))
    return []

# ─── 전체 수집 ─────────────────────────────────────────────
def collect_all(data_ym: str, test_pages: int = 0) -> dict:
    """
    모든 페이지 수집 후 stock_universe와 매칭.
    test_pages>0이면 해당 페이지 수만 수집 (테스트용).
    returns {stock_code: {'name': str, 'total': int, 'workplaces': int}}
    """
    # Total page count
    r = requests.get(f'{BASE_URL}/getGySjBoheomBsshItem', params={
        'serviceKey': API_KEY, 'pageNo': 1, 'numOfRows': 1, 'opaBoheomFg': INS_TYPE,
    }, timeout=20)
    root = ET.fromstring(r.text)
    total_cnt = int(root.findtext('.//totalCount', '0') or '0')
    total_pages = (total_cnt + PAGE_SIZE - 1) // PAGE_SIZE
    scan_pages = min(total_pages, test_pages) if test_pages > 0 else total_pages
    logger.info(f"Total workplaces: {total_cnt:,}  Pages to scan: {scan_pages:,}")

    # Load stock universe (이름 매칭 fallback용)
    universe, code_to_name = load_stock_universe()

    # Load bizno maps (사업자번호 기반 정확 매칭 우선)
    bizno_map, prefix_map = load_bizno_map()

    # bizr_no가 알려진 종목 코드 세트 — 이름 fallback 매칭에서 제외 대상
    # 이름매칭으로 자회사 사업장까지 집계되는 이중집계 버그 방지 (2026-05-15 수정)
    _known_bizno_codes = {code for code, _name in bizno_map.values()}
    logger.info(f'bizr_no 알려진 종목: {len(_known_bizno_codes)}개 → 이름매칭 fallback 제외')

    # Aggregate: stock_code → {total, count}
    aggregated = {}  # code → {'name': str, 'total': int, 'workplaces': int}

    def process_page(page: int):
        items = _fetch_page(page)
        local = {}
        for item in items:
            wk_name = item.get('saeopjangNm', '') or ''
            cnt      = int(item.get('sangsiInwonCnt', '0') or '0')
            saeopja_drno = (item.get('saeopjaDrno') or '').strip()
            if cnt == 0 or not wk_name:
                continue

            matched_code = None
            match_method = ''

            # ── 매칭 우선순위 1: 사업자번호 10자리 직접 매칭 (가장 정확) ──
            if saeopja_drno and len(saeopja_drno) == 10:
                if saeopja_drno in bizno_map:
                    matched_code = bizno_map[saeopja_drno]
                    match_method = 'bizno10'

            # ── 매칭 우선순위 2: 이름 + 사업자번호 앞 6자리 교차 확인 ──
            # 10자리 맵에 없지만 6자리 prefix가 알려진 회사 → 이름 매칭 후 prefix로 검증
            if matched_code is None and saeopja_drno and len(saeopja_drno) == 10:
                prefix6 = saeopja_drno[:6]
                if prefix6 in prefix_map:
                    # prefix가 알려진 회사 → 이름도 같으면 확정, 다르면 스킵
                    prefix_code, prefix_name = prefix_map[prefix6]
                    wk_clean = _clean(wk_name)
                    clean_prefix_nm = _clean(prefix_name)
                    if len(clean_prefix_nm) >= 4 and wk_clean.startswith(clean_prefix_nm):
                        matched_code = (prefix_code, prefix_name)
                        match_method = 'bizno6+name'

            # ── 매칭 우선순위 3: 이름 기반 매칭 (fallback) ──
            # ⛔ 주의: bizr_no가 알려진 회사는 이름매칭 제외 (자회사 이중집계 방지)
            # 예: 삼성물산(028260)의 bizr_no로 이미 일부 사업장 집계 중인데,
            #      "삼성물산건설부문" 등 다른 bizr_no를 가진 자회사까지 이름매칭으로 추가 집계되면
            #      실제 인원의 수십배가 집계되는 버그 발생
            if matched_code is None and saeopja_drno not in bizno_map:
                wk_clean = _clean(wk_name)
                matched_len = 0
                for clean_nm, (code, stock_name) in universe.items():
                    if not clean_nm:
                        continue
                    # bizr_no가 알려진 회사는 이름 fallback에서 제외
                    if code in _known_bizno_codes:
                        continue
                    nm_len = len(clean_nm)
                    if nm_len >= 4:
                        if wk_clean.startswith(clean_nm) and nm_len > matched_len:
                            matched_code = (code, stock_name)
                            matched_len = nm_len
                    else:
                        if wk_clean == clean_nm and nm_len > matched_len:
                            matched_code = (code, stock_name)
                            matched_len = nm_len
                if matched_code:
                    match_method = 'name'

            if matched_code:
                code, stock_name = matched_code
                if code not in local:
                    local[code] = {'name': stock_name, 'total': 0, 'workplaces': 0, 'method': match_method}
                local[code]['total'] += cnt
                local[code]['workplaces'] += 1
        return local

    # Parallel fetch
    t0 = time.time()
    pages = list(range(1, scan_pages + 1))
    completed = 0
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = {pool.submit(process_page, p): p for p in pages}
        for fut in as_completed(futures):
            completed += 1
            local = fut.result()
            for code, info in local.items():
                if code not in aggregated:
                    aggregated[code] = {'name': info['name'], 'total': 0, 'workplaces': 0}
                aggregated[code]['total'] += info['total']
                aggregated[code]['workplaces'] += info['workplaces']
            if completed % 100 == 0:
                logger.info(f"  {completed}/{scan_pages} pages done, matched {len(aggregated)} companies")

    elapsed = time.time() - t0
    logger.info(f"Scan complete in {elapsed:.0f}s. Matched {len(aggregated)} companies.")
    return aggregated

# ─── DB 저장 ───────────────────────────────────────────────
def save_to_db(data_ym: str, aggregated: dict):
    conn = sqlite3.connect(EMP_DB, timeout=30)
    rows = [
        (data_ym, code, info['name'], info['total'], info['workplaces'])
        for code, info in aggregated.items()
    ]
    conn.executemany("""
        INSERT INTO wlb_monthly (data_ym, stock_code, stock_name, total_workers, workplace_cnt)
        VALUES (?,?,?,?,?)
        ON CONFLICT(data_ym, stock_code) DO UPDATE SET
            total_workers = excluded.total_workers,
            workplace_cnt = excluded.workplace_cnt,
            fetched_at = CURRENT_TIMESTAMP
    """, rows)
    conn.commit()
    conn.close()
    logger.info(f"Saved {len(rows)} rows to wlb_monthly (ym={data_ym})")

# ─── 메인 ─────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='근로복지공단 고용보험 월별 인원 수집')
    parser.add_argument('--test', action='store_true', help='테스트: 50페이지만 수집')
    parser.add_argument('--month', type=str, default=None, help='[주의] 라벨 수동 지정 YYYYMM (기본 비권장)')
    parser.add_argument('--allow-override-month', action='store_true', help='--month 강제 허용 (기본: 현재월과 다르면 종료)')
    parser.add_argument('--test-pages', type=int, default=50, help='테스트 시 스캔 페이지 수')
    args = parser.parse_args()

    now_ym = datetime.now().strftime('%Y%m')
    data_ym = args.month or now_ym
    if data_ym != now_ym and not args.allow_override_month:
        raise SystemExit(
            f"[ABORT] WLB API는 과거월 조회 파라미터를 지원하지 않습니다. "
            f"현재월({now_ym})과 다른 data_ym({data_ym}) 저장은 라벨 오염 위험이 큽니다. "
            f"정말 필요하면 --allow-override-month를 명시하세요."
        )
    init_db()

    test_pages = args.test_pages if args.test else 0
    logger.info(f"Starting collection for {data_ym} ({'TEST ' + str(test_pages) + ' pages' if test_pages else 'FULL'})")

    aggregated = collect_all(data_ym, test_pages=test_pages)

    if aggregated:
        logger.info("Top 20 companies by worker count:")
        for code, info in sorted(aggregated.items(), key=lambda x: -x[1]['total'])[:20]:
            logger.info(f"  {info['name']:25s} ({code}): {info['total']:,}명 ({info['workplaces']}개 사업장)")
        save_to_db(data_ym, aggregated)
        print(f"\n✓ {len(aggregated)}개 기업 저장 완료 (data_ym={data_ym})")
    else:
        print("⚠️ 매칭된 기업 없음 - 이름 매칭 로직 확인 필요")
