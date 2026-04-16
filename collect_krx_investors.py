
import sqlite3
import time
import requests
import config
from datetime import date, datetime, timedelta

# 설정
DB_PATH = "/Applications/stock_dashboard/stock.db"
API_KEY = config.KRX_API_KEY
BASE_URL = "https://openapi.krx.co.kr/svc/apis"
HEADERS = {"AUTH_KEY": API_KEY}

def fetch_investor_data(bas_dd: str, market: str = "stk"):
    """
    bas_dd: 'YYYYMMDD'
    market: 'stk' (KOSPI) or 'ksq' (KOSDAQ)
    """
    path = f"sto/{market}_bydd_invst"
    url = f"{BASE_URL}/{path}"
    print(f"Fetching {market.upper()} investor data for {bas_dd}...")
    
    try:
        r = requests.get(url, params={"basDd": bas_dd}, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json().get("OutBlock_1", [])
        else:
            print(f"Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Exception: {e}")
    return []

def save_to_db(data: list, bas_dd_iso: str):
    """
    data: list of dicts from KRX API
    bas_dd_iso: 'YYYY-MM-DD'
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # KRX Open API 개별종목 투자자별 데이터는 한 종목당 여러 투자자 주체별로 행이 나뉨
    # 데이터 구조: { ISU_CD, ISU_NM, INVST_TP_NM, NET_BUY_AMT, NET_BUY_VOL, ... }
    
    # 1. 데이터를 종목코드별로 집계
    stats = {} # { stock_code: { inst_amt, frn_amt, ind_amt, ... } }
    
    for row in data:
        code = row.get("ISU_SRT_CD", row.get("ISU_CD", ""))[-6:]
        if not code or not code.isdigit(): continue
        
        inv_nm = str(row.get("INVST_TP_NM", ""))
        amt = float(str(row.get("NET_BUY_AMT", "0")).replace(",", ""))
        qty = float(str(row.get("NET_BUY_VOL", "0")).replace(",", ""))
        
        if code not in stats:
            stats[code] = {"inst_amt": 0, "frn_amt": 0, "ind_amt": 0, "inst_qty": 0, "frn_qty": 0, "ind_qty": 0}
        
        # 주체별 매핑
        if "기관" in inv_nm and "합계" in inv_nm:
            stats[code]["inst_amt"] = amt
            stats[code]["inst_qty"] = qty
        elif "외국인" in inv_nm and "합계" not in inv_nm: # '외국인' (외국인 합계 아님)
            stats[code]["frn_amt"] = amt
            stats[code]["frn_qty"] = qty
        elif "개인" in inv_nm:
            stats[code]["ind_amt"] = amt
            stats[code]["ind_qty"] = qty
            
    # 2. DB 업데이트
    updated_count = 0
    for code, s in stats.items():
        # price_history 에 해당 일자 레코드가 있는지 확인
        cursor.execute("SELECT 1 FROM price_history WHERE stock_code=? AND date=?", (code, bas_dd_iso))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE price_history 
                SET inst_net_buy_amt = ?, frn_net_buy_amt = ?, ind_net_buy_amt = ?,
                    inst_net_buy = ?, frn_net_buy = ?, ind_net_buy = ?
                WHERE stock_code = ? AND date = ?
            """, (s["inst_amt"], s["frn_amt"], s["ind_amt"], s["inst_qty"], s["frn_qty"], s["ind_qty"], code, bas_dd_iso))
        else:
            # 레코드가 없으면 새로 생성 (가격 정보 등은 0으로 채움)
            cursor.execute("""
                INSERT INTO price_history 
                (stock_code, date, open, high, low, close, volume, 
                 inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt,
                 inst_net_buy, frn_net_buy, ind_net_buy)
                VALUES (?, ?, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?)
            """, (code, bas_dd_iso, s["inst_amt"], s["frn_amt"], s["ind_amt"], s["inst_qty"], s["frn_qty"], s["ind_qty"]))
        
        updated_count += 1
        
    conn.commit()
    conn.close()
    return updated_count

def main():
    # 오늘 또는 최근 영업일
    today = datetime.now()
    if today.weekday() >= 5: # 주말이면 금요일로
        today = today - timedelta(days=today.weekday() - 4)
    
    bas_dd = today.strftime("%Y%m%d")
    bas_dd_iso = today.strftime("%Y-%m-%d")
    
    # 1. KOSPI 수집 및 저장
    kospi_data = fetch_investor_data(bas_dd, "stk")
    if kospi_data:
        cnt = save_to_db(kospi_data, bas_dd_iso)
        print(f"KOSPI investor data saved: {cnt} stocks.")
    
    # 2. KOSDAQ 수집 및 저장
    kosdaq_data = fetch_investor_data(bas_dd, "ksq")
    if kosdaq_data:
        cnt = save_to_db(kosdaq_data, bas_dd_iso)
        print(f"KOSDAQ investor data saved: {cnt} stocks.")

if __name__ == "__main__":
    main()
