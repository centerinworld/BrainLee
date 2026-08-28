"""
fetch_nps_2years.py — NPS 국민연금 월별 사업장 현황 24개월 백필

전략:
  1) employment.db nps_workplace_monthly의 202602 데이터(2,194개사)에서
     회사명(wkpl_nm) + stock_code 목록을 가져온다.
  2) 회사명(wkplNm) 검색으로 과거 24개월 데이터를 수집한다.
  3) 이미 수집된 (ym, stock_code) 조합은 스킵한다.

실행:
  python3 employment_monitor/fetch_nps_2years.py
  python3 employment_monitor/fetch_nps_2years.py --months 12  # 12개월만
  python3 employment_monitor/fetch_nps_2years.py --start 202401 --end 202601
  python3 employment_monitor/fetch_nps_2years.py --dry-run  # API 연결 테스트만
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import unquote

import requests
from dateutil.relativedelta import relativedelta

sys.path.insert(0, '/Applications/stock_dashboard')
try:
    from config import PUBLIC_DATA_API_KEY
    API_KEY = unquote(PUBLIC_DATA_API_KEY)
except Exception:
    import os
    API_KEY = os.getenv('PUBLIC_DATA_API_KEY', '')

if not API_KEY:
    print("❌ PUBLIC_DATA_API_KEY not found")
    sys.exit(1)

EMP_DB = '/Applications/stock_dashboard/employment_monitor/employment.db'
NPS_URL = 'http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList'


def iter_months(start_ym: str, end_ym: str):
    """start_ym ~ end_ym 사이 YYYYMM 목록 (최신→과거 순)."""
    s = datetime.strptime(start_ym, '%Y%m')
    e = datetime.strptime(end_ym, '%Y%m')
    cur = e
    while cur >= s:
        yield cur.strftime('%Y%m')
        cur -= relativedelta(months=1)


def test_api_connection() -> bool:
    """API 연결 및 키 유효성 확인."""
    try:
        r = requests.get(NPS_URL, params={
            'serviceKey': API_KEY,
            'wkplNm': '삼성전자',
            'data_crt_ym': '202602',
            'pageNo': 1,
            'numOfRows': 1,
            '_type': 'json',
        }, timeout=10)
        print(f"[API 테스트] status={r.status_code} body={r.text[:100]}")
        if r.status_code == 200:
            d = r.json()
            items = d.get('response', {}).get('body', {}).get('items', {}).get('item', [])
            return bool(items)
        return False
    except Exception as e:
        print(f"[API 테스트] 오류: {e}")
        return False


def fetch_by_name(wkpl_nm: str, ym: str) -> dict | None:
    """회사명으로 특정 월 NPS 데이터 조회. 없으면 None."""
    params = {
        'serviceKey': API_KEY,
        'wkplNm': wkpl_nm,
        'data_crt_ym': ym,
        'pageNo': 1,
        'numOfRows': 5,
        '_type': 'json',
    }
    try:
        r = requests.get(NPS_URL, params=params, timeout=15)
        if r.status_code != 200:
            return None
        d = r.json()
        items = d.get('response', {}).get('body', {}).get('items', {}).get('item', [])
        if isinstance(items, dict):
            items = [items]
        return items[0] if items else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--months', type=int, default=24, help='수집할 개월 수 (기본 24)')
    ap.add_argument('--start', default='', help='시작 YYYYMM (기본: 현재-months)')
    ap.add_argument('--end', default='', help='종료 YYYYMM (기본: 현재 전월)')
    ap.add_argument('--dry-run', action='store_true', help='API 연결 테스트만 수행')
    ap.add_argument('--delay', type=float, default=0.1, help='요청 간 딜레이(초, 기본 0.1)')
    ap.add_argument('--limit', type=int, default=0, help='수집 기업 수 제한 (0=전체)')
    args = ap.parse_args()

    # API 연결 테스트
    print("🔌 NPS API 연결 테스트 중...")
    ok = test_api_connection()
    if not ok:
        print("❌ API 접근 불가 — 서버 오류 또는 서비스 키 문제")
        print("   → apis.data.go.kr DNS 차단 또는 서비스 일시 중단 가능성")
        print("   → 나중에 다시 시도하거나 공공데이터포털에서 키 확인 필요")
        if args.dry_run:
            sys.exit(0)
        print("   수집을 중단합니다.")
        sys.exit(1)

    if args.dry_run:
        print("✅ API 정상. --dry-run 모드 종료.")
        sys.exit(0)

    # 월 범위 계산
    now = datetime.now()
    if args.end:
        end_ym = args.end
    else:
        end_ym = (now - relativedelta(months=1)).strftime('%Y%m')
    if args.start:
        start_ym = args.start
    else:
        start_ym = (datetime.strptime(end_ym, '%Y%m') - relativedelta(months=args.months - 1)).strftime('%Y%m')

    months = list(iter_months(start_ym, end_ym))
    print(f"📅 수집 범위: {start_ym} ~ {end_ym} ({len(months)}개월)")

    # 대상 기업 목록 (202602 데이터에 있는 기업들)
    conn = sqlite3.connect(EMP_DB)
    companies = conn.execute("""
        SELECT DISTINCT stock_code, stock_name, wkpl_nm
        FROM nps_workplace_monthly
        WHERE ym = (SELECT MAX(ym) FROM nps_workplace_monthly)
          AND wkpl_nm IS NOT NULL
        ORDER BY stock_code
    """).fetchall()

    if args.limit > 0:
        companies = companies[:args.limit]

    print(f"🏢 대상 기업: {len(companies)}개\n")

    # 이미 수집된 (ym, stock_code) 집합
    existing = set(conn.execute(
        "SELECT ym, stock_code FROM nps_workplace_monthly"
    ).fetchall())

    inserted = 0
    skipped = 0
    errors = 0

    for idx, (code, name, wkpl_nm) in enumerate(companies, 1):
        print(f"[{idx}/{len(companies)}] {name}({code})", flush=True)
        company_inserted = 0

        for ym in months:
            if (ym, code) in existing:
                skipped += 1
                continue

            item = fetch_by_name(wkpl_nm, ym)
            time.sleep(args.delay)

            if item is None:
                errors += 1
                continue

            conn.execute("""
                INSERT OR REPLACE INTO nps_workplace_monthly
                (ym, stock_code, stock_name, seq, wkpl_nm, bzowr_rgst_no,
                 nw_acqzr_cnt, lss_jnngp_cnt, raw_base_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                ym, code, name,
                item.get('seq'), item.get('wkplNm'), item.get('bzowrRgstNo'),
                int(item.get('nwAcqzrCnt') or 0),
                int(item.get('lssJnngpCnt') or 0),
                json.dumps(item, ensure_ascii=False),
            ))
            existing.add((ym, code))
            inserted += 1
            company_inserted += 1

        if company_inserted:
            conn.commit()
            print(f"  → +{company_inserted}개월", flush=True)

        if idx % 100 == 0:
            total_db = conn.execute('SELECT COUNT(*) FROM nps_workplace_monthly').fetchone()[0]
            print(f"\n📈 [{idx}/{len(companies)}] 삽입:{inserted} 스킵:{skipped} 오류:{errors} DB총:{total_db:,}\n")

    conn.commit()
    conn.close()
    print(f"\n✅ 완료 — 삽입:{inserted} 스킵:{skipped} 오류:{errors}")


if __name__ == '__main__':
    main()
