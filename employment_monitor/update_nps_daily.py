import logging
import sqlite3
import pandas as pd
import requests
from urllib.parse import unquote
import json
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
import os

logger = logging.getLogger(__name__)

load_dotenv('/Volumes/Realtek_NVME/stock_dashboard/runtime/.env')
API_KEY = os.getenv('PUBLIC_DATA_API_KEY')

if not API_KEY:
    print("❌ Error: PUBLIC_DATA_API_KEY not found in /Volumes/Realtek_NVME/stock_dashboard/runtime/.env")
    exit(1)

EMP_DB = '/Volumes/Realtek_NVME/stock_dashboard/runtime/employment_monitor/employment.db'
STOCK_DB = '/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'

def get_latest_db_month(conn):
    row = conn.execute("SELECT MAX(ym) FROM nps_workplace_monthly").fetchone()
    return row[0] if row and row[0] else '202401'

def check_if_month_exists_on_api(ym, test_biz_no='124810'): # 삼성전자(124810)로 샘플 테스트
    url = "http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList"
    params = {
        'serviceKey': unquote(API_KEY),
        'bzowr_rgst_no': test_biz_no,
        'data_crt_ym': ym,
        'pageNo': 1,
        'numOfRows': 1,
        '_type': 'json'
    }
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
            return len(items) > 0
    except Exception as _e:
        logger.warning("NPS API 확인 실패: %s", _e)
    return False

def update_nps_data():
    conn = sqlite3.connect(EMP_DB)
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
    
    latest_ym = get_latest_db_month(conn)
    print(f"📌 현재 DB 최신 수집월: {latest_ym}")
    
    # DB 최신월 다음 달 계산
    dt = datetime.strptime(latest_ym, '%Y%m')
    next_month_dt = dt + relativedelta(months=1)
    target_ym = next_month_dt.strftime('%Y%m')
    
    print(f"🔍 공공데이터포털 {target_ym} 월 데이터 업데이트 여부 확인 중...")
    
    if not check_if_month_exists_on_api(target_ym):
        print(f"⏳ 아직 {target_ym} 월 데이터가 공공데이터포털에 올라오지 않았습니다. 수집을 건너뜁니다.")
        conn.close()
        return
        
    print(f"🚀 {target_ym} 월 데이터 업데이트 확인됨! 전체 상장사 대상 수집을 시작합니다.")
    
    df = pd.read_sql("""
        SELECT u.stock_code, u.stock_name, m.biz_no_6 as biz_no 
        FROM main_db.stock_universe u
        JOIN stock_bizno_map m ON u.stock_code = m.stock_code
        WHERE u.market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')
    """, conn)
    
    url = "http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList"
    inserted = 0
    
    for idx, row in df.iterrows():
        code = row['stock_code']
        name = row['stock_name']
        biz_no = row['biz_no'][:6] if row['biz_no'] else None
        
        if not biz_no: continue
            
        print(f"[{idx+1}/{len(df)}] 수집 중: {name}({code}) ({target_ym})")
        
        params = {
            'serviceKey': unquote(API_KEY),
            'bzowr_rgst_no': biz_no,
            'data_crt_ym': target_ym,
            'pageNo': 1,
            'numOfRows': 10,
            '_type': 'json'
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json()
                items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
                if isinstance(items, dict): items = [items]
                
                if items:
                    item = items[0]
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT OR REPLACE INTO nps_workplace_monthly 
                        (ym, stock_code, stock_name, seq, wkpl_nm, bzowr_rgst_no, 
                         nw_acqzr_cnt, lss_jnngp_cnt, raw_base_json, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        target_ym, code, name, item.get('seq'), item.get('wkplNm'), biz_no,
                        int(item.get('nwAcqzrCnt', 0) or 0), 
                        int(item.get('lssJnngpCnt', 0) or 0),
                        json.dumps(item, ensure_ascii=False)
                    ))
                    conn.commit()
                    inserted += 1
        except Exception as e:
            pass
        time.sleep(0.05)
        
    conn.close()
    print(f"\n✅ 수집 완료! {target_ym} 월 데이터 총 {inserted}건 추가됨.")

if __name__ == '__main__':
    update_nps_data()
