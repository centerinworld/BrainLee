import sqlite3
import requests
from urllib.parse import unquote
import json
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv('/Applications/stock_dashboard/.env')
API_KEY = os.getenv('PUBLIC_DATA_API_KEY')

if not API_KEY:
    print("❌ Error: PUBLIC_DATA_API_KEY not found in /Applications/stock_dashboard/.env")
    exit(1)

EMP_DB  = '/Applications/stock_dashboard/employment_monitor/employment.db'
STOCK_DB = '/Applications/stock_dashboard/stock.db'

def get_target_months():
    """과거 24개월 YYYYMM 목록 생성 (최신 → 과거 순)"""
    end_date = datetime.now()
    return [(end_date - relativedelta(months=i)).strftime('%Y%m') for i in range(1, 25)]

def update_nps_data():
    # ── stock.db는 읽기 전용 별도 커넥션으로만 사용 ─────────────
    # ATTACH 사용 시 stock.db(644)가 read-only라 employment.db 쓰기도 차단됨
    stock_conn = sqlite3.connect(f'file:{STOCK_DB}?mode=ro', uri=True)
    rows = stock_conn.execute("""
        SELECT u.stock_code, u.stock_name, m.biz_no_6
        FROM stock_universe u
        JOIN stock_bizno_map m ON u.stock_code = m.stock_code
        WHERE u.market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')
          AND m.biz_no_6 IS NOT NULL
    """).fetchall()
    stock_conn.close()

    # ── employment.db는 쓰기 전용 별도 커넥션 ───────────────────
    emp_conn = sqlite3.connect(EMP_DB)

    months = get_target_months()
    total_companies = len(rows)
    print(f"🎯 2년치 데이터 수집 시작: {months[-1]} ~ {months[0]} ({len(months)}개월)")
    print(f"📊 대상 기업 수: {total_companies}개\n")

    url = "http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList"

    inserted = 0
    api_errors = 0

    for idx, (code, name, biz_no_6) in enumerate(rows, start=1):
        if not biz_no_6:
            continue

        biz_no = biz_no_6[:6]
        print(f"[{idx}/{total_companies}] {name}({code}) biz={biz_no}", flush=True)

        for ym in months:
            params = {
                'serviceKey': unquote(API_KEY),
                'bzowr_rgst_no': biz_no,
                'data_crt_ym':   ym,
                'pageNo':        1,
                'numOfRows':     10,
                '_type':         'json'
            }
            try:
                res = requests.get(url, params=params, timeout=15)
                if res.status_code != 200:
                    api_errors += 1
                    continue

                data  = res.json()
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if isinstance(items, dict):
                    items = [items]

                if items:
                    item = items[0]
                    emp_conn.execute("""
                        INSERT OR REPLACE INTO nps_workplace_monthly
                        (ym, stock_code, stock_name, seq, wkpl_nm, bzowr_rgst_no,
                         nw_acqzr_cnt, lss_jnngp_cnt, raw_base_json, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        ym, code, name,
                        item.get('seq'), item.get('wkplNm'), biz_no,
                        int(item.get('nwAcqzrCnt', 0) or 0),
                        int(item.get('lssJnngpCnt', 0) or 0),
                        json.dumps(item, ensure_ascii=False)
                    ))
                    emp_conn.commit()
                    inserted += 1

            except requests.exceptions.Timeout:
                api_errors += 1
                print(f"  ⚠️ 타임아웃: {name} {ym}", flush=True)
            except Exception as e:
                api_errors += 1
                print(f"  ⚠️ 오류: {name} {ym} - {e}", flush=True)

            time.sleep(0.05)  # 공공데이터포털 트래픽 제한 방지

        # 50개 기업마다 중간 현황 출력
        if idx % 50 == 0:
            total_db = emp_conn.execute('SELECT COUNT(*) FROM nps_workplace_monthly').fetchone()[0]
            print(f"\n📈 중간현황 [{idx}/{total_companies}] 누적 삽입: {inserted}건 / DB 총: {total_db:,}건 / API오류: {api_errors}건\n", flush=True)

    emp_conn.close()
    print(f"\n✅ 수집 완료! 삽입: {inserted}건 / API오류: {api_errors}건")

if __name__ == '__main__':
    update_nps_data()
