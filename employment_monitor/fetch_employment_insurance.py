"""
fetch_employment_insurance.py — 고용보험 상시인원 수집

근로복지공단 고용산재보험현황정보 API에서 기업별 상시인원(sangsiInwonCnt)을 수집.

전략:
  1) employment.db의 nps_workplace_monthly에서 상장 기업 목록 + stock_code 추출
  2) DART company API로 bizr_no(10자리 사업자번호) 조회
  3) 고용보험 API로 상시인원 수집 → employment_company 테이블 저장

실행:
  python3 employment_monitor/fetch_employment_insurance.py
  python3 employment_monitor/fetch_employment_insurance.py --limit 50  # 상위 N종목만
  python3 employment_monitor/fetch_employment_insurance.py --code 005930  # 단일 종목
  python3 employment_monitor/fetch_employment_insurance.py --dry-run  # API 연결 테스트
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import unquote

import requests

sys.path.insert(0, '/Applications/stock_dashboard')
try:
    from config import PUBLIC_DATA_API_KEY
    from dart_key_manager import DartKeyRotator, RotatingOpenDartReader, get_dart_api_keys
    EMP_API_KEY = unquote(PUBLIC_DATA_API_KEY)
except Exception:
    import os
    EMP_API_KEY = os.getenv('PUBLIC_DATA_API_KEY', '')
    from dart_key_manager import DartKeyRotator, RotatingOpenDartReader, get_dart_api_keys

if not EMP_API_KEY:
    print("❌ PUBLIC_DATA_API_KEY not found")
    sys.exit(1)
if not get_dart_api_keys():
    print("❌ DART_API_KEY not found")
    sys.exit(1)

EMP_DB  = '/Applications/stock_dashboard/employment_monitor/employment.db'
STOCK_DB = '/Applications/stock_dashboard/stock.db'
EMP_INS_URL = 'http://apis.data.go.kr/B490001/gySjbPstateInfoService/getGySjBoheomBsshItem'
DART_COMPANY_URL = 'https://opendart.fss.or.kr/api/company.json'


# ─── DB 초기화 ──────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection):
    """컬럼 보정 (bizr_no, source)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(employment_company)")}
    if 'bizr_no' not in cols:
        conn.execute("ALTER TABLE employment_company ADD COLUMN bizr_no TEXT")
    if 'source' not in cols:
        conn.execute("ALTER TABLE employment_company ADD COLUMN source TEXT")
        conn.commit()
    # bizr_no 캐시 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_bizr_no_map (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            bizr_no    TEXT,
            corp_code  TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


# ─── DART corp_code 조회 ─────────────────────────────────────────────────────

def _get_corp_list():
    """OpenDartReader를 통해 전체 종목 corp_code 맵 반환: {stock_code: corp_code}."""
    dart = RotatingOpenDartReader()
    clist = dart.corp_codes  # DataFrame
    if clist is None or clist.empty:
        return {}
    mapping = {}
    for _, row in clist.iterrows():
        sc = str(row.get('stock_code', '')).strip()
        cc = str(row.get('corp_code', '')).strip()
        if sc and len(sc) == 6 and sc.isdigit():
            mapping[sc] = cc
    return mapping


def get_bizr_no_from_dart(corp_code: str) -> str | None:
    """DART company API로 사업자등록번호(10자리) 조회."""
    rotator = DartKeyRotator()
    while True:
        key = rotator.next_key()
        if not key:
            return None
        try:
            r = requests.get(DART_COMPANY_URL, params={
                'crtfc_key': key,
                'corp_code': corp_code,
            }, timeout=10)
            if r.status_code != 200:
                return None
            d = r.json()
            if d.get('status') == '020':
                rotator.mark_exhausted(key)
                continue
            if d.get('status') != '000':
                return None
            bizr = str(d.get('bizr_no', '')).replace('-', '').strip()
            return bizr if len(bizr) == 10 else None
        except Exception:
            return None


# ─── 고용보험 API ─────────────────────────────────────────────────────────────

def fetch_sangsi_inwon(bizr_no: str) -> dict | None:
    """
    고용보험(opaBoheomFg=1) 상시인원 합산 조회.

    ※ opaBoheomFg 필터 이유:
      opaBoheomFg=1 → 고용보험 가입 사업장
      opaBoheomFg=2 → 산재보험 가입 사업장
      동일 사업장이 두 보험에 모두 등록되면 응답에 2번 포함 → 이중 집계 발생.
      고용보험(1)만 필터링하면 정확한 실제 고용인원 산출 가능.
      (예: KAI 전체조회=10,635명 → 고용보험만=5,353명 ≈ 실제 5,300명)

    반환: {'sangsiInwonCnt': 총합계, 'saeopjaNm': 대표명칭} or None
    """
    all_items = []
    page = 1
    while True:
        try:
            r = requests.get(EMP_INS_URL, params={
                'serviceKey': EMP_API_KEY,
                'v_saeopjaDrno': bizr_no,
                'opaBoheomFg': '1',    # 고용보험만 (산재보험 이중집계 방지)
                'pageNo': page,
                'numOfRows': 100,
                '_type': 'json',
            }, timeout=15)
            if r.status_code != 200:
                break
            d = r.json()
            body = d.get('response', {}).get('body', {})
            total_count = int(body.get('totalCount') or 0)
            items = body.get('items', {}).get('item', [])
            if isinstance(items, dict):
                items = [items]
            all_items.extend(items)
            if len(all_items) >= total_count or not items:
                break
            page += 1
        except Exception:
            break

    if not all_items:
        return None

    # 고용보험 사업장 상시인원 합산
    total_sangsi = sum(int(it.get('sangsiInwonCnt') or 0) for it in all_items)
    # 대표 사업장명 (가장 상시인원 많은 곳)
    main_item = max(all_items, key=lambda x: int(x.get('sangsiInwonCnt') or 0))
    return {
        'sangsiInwonCnt': total_sangsi,
        'saeopjaNm': main_item.get('saeopjangNm', ''),
        'items_count': len(all_items),
        'raw': all_items,
    }


def test_api() -> bool:
    """삼성전자 사업자번호로 API 연결 테스트."""
    result = fetch_sangsi_inwon('1248100998')  # 삼성전자
    print(f"[API 테스트] 결과: sangsi={result.get('sangsiInwonCnt') if result else None}")
    return result is not None


# ─── 메인 수집 루프 ───────────────────────────────────────────────────────────

def get_target_companies(conn: sqlite3.Connection, stock_db_conn: sqlite3.Connection):
    """수집 대상 기업 목록: 코스피/코스닥 보통주만 (ETF/ETN/리츠/스팩 제외).

    kind_stkcert_nm='보통주' 필터로 ETF·ETN·리츠·스팩·우선주 등을 원천 제외.
    (stock_type 컬럼은 ETF들이 NULL로 저장돼 신뢰도 낮음 — kind_stkcert_nm 사용)
    """
    rows = stock_db_conn.execute("""
        SELECT DISTINCT stock_code, stock_name
        FROM stock_universe
        WHERE market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
          AND kind_stkcert_nm = '보통주'
          AND COALESCE(secugrp_nm, '주권') NOT IN ('외국주권', '주식예탁증권')
          AND LENGTH(stock_code) = 6
        ORDER BY stock_code
    """).fetchall()
    return [(r[0], r[1]) for r in rows]


def run(limit: int = 0, single_code: str = '', dry_run: bool = False, delay: float = 0.3, force: bool = False):
    if dry_run:
        print("🔌 API 연결 테스트 중...")
        ok = test_api()
        print("✅ 정상" if ok else "❌ 실패")
        sys.exit(0 if ok else 1)

    emp_conn   = sqlite3.connect(EMP_DB, timeout=60)
    stock_conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)

    init_db(emp_conn)

    # bizr_no 캐시 로드
    bizr_cache = {r[0]: r[1] for r in emp_conn.execute(
        "SELECT stock_code, bizr_no FROM stock_bizr_no_map WHERE bizr_no IS NOT NULL"
    ).fetchall()}

    # corp_code 맵 로드
    print("📥 DART 종목 코드 목록 로드 중...")
    corp_map = _get_corp_list()
    print(f"   → {len(corp_map)}개 종목")

    if single_code:
        companies = [(single_code, '')]
    else:
        companies = get_target_companies(emp_conn, stock_conn)
        if limit:
            companies = companies[:limit]

    total = len(companies)
    ym = datetime.now().strftime('%Y-%m')
    print(f"🏢 대상 기업: {total}개 | 수집 기준월: {ym}\n")

    ok = errors = skipped = bizr_miss = 0

    for idx, (code, name) in enumerate(companies, 1):
        # bizr_no 조회 (캐시 우선)
        bizr = bizr_cache.get(code)
        if not bizr:
            corp_code = corp_map.get(code)
            if not corp_code:
                bizr_miss += 1
                continue
            bizr = get_bizr_no_from_dart(corp_code)
            time.sleep(0.2)
            if bizr:
                emp_conn.execute("""
                    INSERT OR REPLACE INTO stock_bizr_no_map
                    (stock_code, stock_name, bizr_no, corp_code, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (code, name, bizr, corp_code))
                emp_conn.commit()
                bizr_cache[code] = bizr
            else:
                bizr_miss += 1
                continue

        # 이미 이번 달 수집했으면 스킵 (--force 또는 단일종목 재수집 시 덮어쓰기)
        existing = emp_conn.execute(
            "SELECT id FROM employment_company WHERE stock_code=? AND ym=?",
            (code, ym)
        ).fetchone()
        if existing and not force and not single_code:
            skipped += 1
            continue

        item = fetch_sangsi_inwon(bizr)
        time.sleep(delay)

        if item is None:
            errors += 1
            continue

        sangsi = int(item.get('sangsiInwonCnt') or 0)
        saeopja_nm = str(item.get('saeopjaNm') or name)

        # 전월 데이터 조회 (yoy/mom 계산용)
        prev = emp_conn.execute("""
            SELECT worker_count FROM employment_company
            WHERE stock_code=? AND ym < ? ORDER BY ym DESC LIMIT 1
        """, (code, ym)).fetchone()
        mom = round(sangsi - prev[0], 2) if prev and prev[0] else None

        prev_yr = emp_conn.execute("""
            SELECT worker_count FROM employment_company
            WHERE stock_code=? AND ym=?
        """, (code, f"{int(ym[:4])-1}{ym[4:]}" )).fetchone()
        yoy = round(sangsi - prev_yr[0], 2) if prev_yr and prev_yr[0] else None

        emp_conn.execute("""
            INSERT OR REPLACE INTO employment_company
            (ym, stock_code, stock_name, worker_count, yoy_change, mom_change, bizr_no, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (ym, code, name or saeopja_nm, sangsi, yoy, mom, bizr, 'insurance_api'))
        emp_conn.commit()
        ok += 1

        if ok % 50 == 0 or idx % 100 == 0:
            print(f"[{idx}/{total}] 저장:{ok} 스킵:{skipped} bizr_miss:{bizr_miss} 오류:{errors}")

    emp_conn.close()
    stock_conn.close()
    print(f"\n✅ 완료 — 저장:{ok} 스킵:{skipped} bizr_miss:{bizr_miss} 오류:{errors}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit',    type=int,  default=0,     help='수집 기업 수 제한')
    ap.add_argument('--code',     default='',               help='단일 종목 코드 (자동 강제 재수집)')
    ap.add_argument('--dry-run',  action='store_true',      help='API 연결 테스트만')
    ap.add_argument('--delay',    type=float, default=0.3,  help='요청 간 딜레이(초)')
    ap.add_argument('--force',    action='store_true',      help='이미 수집된 데이터도 덮어쓰기')
    args = ap.parse_args()
    run(limit=args.limit, single_code=args.code, dry_run=args.dry_run, delay=args.delay, force=args.force)
