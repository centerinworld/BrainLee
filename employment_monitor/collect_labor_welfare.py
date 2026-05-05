"""
근로복지공단 고용·산재보험현황정보 API 수집기
B490001/gySjbPstateInfoService/getGySjBoheomBsshItem
- 고용보험 가입 사업장별 상시인원수(sangsiInwonCnt) 수집
- 우리 stock_universe 상장사와 이름 매칭 → 사업장별 인원 합산
- employment.db wlb_monthly 테이블에 월별 저장

실행: python3 employment_monitor/collect_labor_welfare.py [--test] [--month YYYYMM]
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
        "SELECT stock_code, stock_name FROM stock_universe WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')"
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

    # Load stock universe
    universe, code_to_name = load_stock_universe()

    # Aggregate: stock_code → {total, count}
    aggregated = {}  # code → {'name': str, 'total': int, 'workplaces': int}

    def process_page(page: int):
        items = _fetch_page(page)
        local = {}
        for item in items:
            wk_name = item.get('saeopjangNm', '') or ''
            cnt = int(item.get('sangsiInwonCnt', '0') or '0')
            if cnt == 0 or not wk_name:
                continue
            wk_clean = _clean(wk_name)
            # Longest-prefix match against stock names
            # - 4자 이상: prefix 매칭 허용 (삼성전자 → 삼성전자구미사업장 OK)
            # - 3자 이하: 완전 일치만 (GS, LG, 동양 등 오매칭 방지)
            matched_code = None
            matched_len = 0
            for clean_nm, (code, stock_name) in universe.items():
                if not clean_nm:
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
                code, stock_name = matched_code
                if code not in local:
                    local[code] = {'name': stock_name, 'total': 0, 'workplaces': 0}
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
    parser.add_argument('--month', type=str, default=None, help='수집월 YYYYMM (기본: 현재월)')
    parser.add_argument('--test-pages', type=int, default=50, help='테스트 시 스캔 페이지 수')
    args = parser.parse_args()

    data_ym = args.month or datetime.now().strftime('%Y%m')
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
