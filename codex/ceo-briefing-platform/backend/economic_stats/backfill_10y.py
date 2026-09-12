#!/usr/bin/env python3
"""
📅 10년 치 경제 지표 데이터 백필(Backfill) 스크립트 (2016-01-01 ~ 현재)
==============================================================
"""

import os
import json
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# Paths
BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "data" / "economic_indicators.db"

BOK_BASE_URL = "https://ecos.bok.or.kr/api"

def load_env():
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        env_file = BACKEND_DIR / ".env"
    
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

def connect_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def _request_json(url):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "EconomicBackfiller/1.0",
            "Accept": "application/json"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [ERROR] API 호출 실패: {url[:80]}... - {e}")
        return None

def _bok_stat_search(api_key, stat_code, start_date, end_date, item_code="", cycle="D"):
    sd = start_date.replace("-", "")
    ed = end_date.replace("-", "")

    # 주기에 맞춰 ECOS 날짜 형식 변환
    if cycle == "Q":
        def _to_quarter(d_str):
            if len(d_str) >= 6:
                year = d_str[:4]
                m = int(d_str[4:6])
                q = (m - 1) // 3 + 1
                return f"{year}Q{q}"
            return d_str[:4] + "Q1"
        sd = _to_quarter(sd)
        ed = _to_quarter(ed)
    elif cycle == "A" or cycle == "Y":
        cycle = "A"
        sd = sd[:4]
        ed = ed[:4]
    elif cycle == "M":
        sd = sd[:6]
        ed = ed[:6]
    elif cycle == "D":
        sd = sd[:8]
        ed = ed[:8]

    # 10년 치 일별 지표를 한 번에 긁어오기 위해 최대 행 수(5000건)로 호출 범위 설정
    url = f"{BOK_BASE_URL}/StatisticSearch/{api_key}/json/kr/1/5000/{stat_code}/{cycle}/{sd}/{ed}/{item_code}"
    data = _request_json(url)
    if not data:
        return []
    
    return data.get("StatisticSearch", {}).get("row", [])

def _calc_change_rate(current, previous):
    if previous is not None and previous != 0:
        return ((current - previous) / previous) * 100
    return None

def save_indicator_data(conn, indicator_code, date, value, previous_value=None, change_rate=None, source_ref=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO indicator_data(
            indicator_code, date, value, previous_value, change_rate,
            source_ref, raw_json, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, '', ?)
        ON CONFLICT(indicator_code, date) DO UPDATE SET
            value = excluded.value,
            previous_value = COALESCE(excluded.previous_value, indicator_data.previous_value),
            change_rate = COALESCE(excluded.change_rate, indicator_data.change_rate),
            source_ref = excluded.source_ref,
            fetched_at = excluded.fetched_at
        """,
        (indicator_code, date, value, previous_value, change_rate, source_ref, now),
    )
    conn.commit()

def backfill_all():
    load_env()
    api_key = os.environ.get("BOK_API_KEY")
    if not api_key:
        print("❌ ECOS API 키가 설정되지 않았습니다.")
        return

    conn = connect_db()
    
    # 10년 전부터 오늘까지 범위
    start_date = "2016-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    print(f"🚀 [백필 시작] {start_date} ~ {end_date}")

    # ==========================================
    # 1. 환율 백필 (731Y001, D)
    # ==========================================
    exchange_indicators = [
        ("EXCHANGE_USD", "731Y001", "0000001"),
        ("EXCHANGE_JPY", "731Y001", "0000002"),
        ("EXCHANGE_CNY", "731Y001", "0000053"),
        ("EXCHANGE_EUR", "731Y001", "0000003"),
    ]
    for code, stat, item in exchange_indicators:
        print(f"  환율 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, start_date, end_date, item, "D")
        if not rows:
            print("    → 데이터 없음")
            continue
        
        # 시간 역순으로 올 수 있으므로 날짜 정렬(오름차순)하여 순차 저장
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, code, date, val, prev_val, change_rate, f"BOK_{stat}_{item}")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    # ==========================================
    # 2. 금리 백필 (722Y001-M, 817Y002-D)
    # ==========================================
    interest_indicators = [
        ("BASE_RATE", "722Y001", "0101000", "M"),
        ("BOND_YIELD_3Y", "817Y002", "010200000", "D"),
        ("BOND_YIELD_10Y", "817Y002", "010210000", "D"),
        ("CALL_RATE", "817Y002", "010101000", "D"),
    ]
    for code, stat, item, cycle in interest_indicators:
        print(f"  금리 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, start_date, end_date, item, cycle)
        if not rows:
            print("    → 데이터 없음")
            continue
        
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            if cycle == "D":
                date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            else:
                date = f"{date_str[:4]}-{date_str[4:6]}-01"
                
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, code, date, val, prev_val, change_rate, f"BOK_{stat}_{item}")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    # ==========================================
    # 3. 물가 백필 (소비자물가, 근원물가 - M)
    # ==========================================
    # YoY 계산을 위해 2014년부터 가져옴
    cpi_indicators = [
        ("CPI_CHANGE", "901Y009", "0"),
        ("CORE_CPI", "901Y010", "QB"),
    ]
    for code, stat, item in cpi_indicators:
        print(f"  물가 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, "2014-01-01", end_date, item, "M")
        if not rows:
            print("    → 데이터 없음")
            continue
        
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        # 날짜 정렬 후 순차 저장하면서 1년 전의 값을 뒤져서 전년동월대비(YoY) 상승률 계산
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            date = f"{date_str[:4]}-{date_str[4:6]}-01"
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            # 1년 전 날짜 계산
            dt = datetime.strptime(date, "%Y-%m-%d")
            one_year_ago_date = (dt - timedelta(days=365)).strftime("%Y-%m-%d")
            one_year_ago_row = conn.execute(
                "SELECT value FROM indicator_data WHERE indicator_code=? AND date<=? ORDER BY date DESC LIMIT 1",
                (code, one_year_ago_date)
            ).fetchone()
            
            one_year_ago_val = one_year_ago_row[0] if one_year_ago_row else None
            change_rate = _calc_change_rate(val, one_year_ago_val) if one_year_ago_val else None
            
            save_indicator_data(conn, code, date, val, one_year_ago_val, change_rate, f"BOK_{stat}_{item}")
            count += 1
        print(f"    → {count}건 저장 완료")

    # ==========================================
    # 4. 무역 백필 (301Y013, M)
    # ==========================================
    trade_indicators = [
        ("EXPORT_AMOUNT", "301Y013", "110000"),
        ("IMPORT_AMOUNT", "301Y013", "120000"),
    ]
    for code, stat, item in trade_indicators:
        print(f"  무역 수하량 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, start_date, end_date, item, "M")
        if not rows:
            print("    → 데이터 없음")
            continue
        
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            date = f"{date_str[:4]}-{date_str[4:6]}-01"
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, code, date, val, prev_val, change_rate, f"BOK_{stat}_{item}")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    # 무역수지 재계산 및 10년 백필
    print("  무역수지 10년 치 재계산 중...")
    export_data = conn.execute("SELECT date, value FROM indicator_data WHERE indicator_code='EXPORT_AMOUNT' ORDER BY date ASC").fetchall()
    import_data = conn.execute("SELECT date, value FROM indicator_data WHERE indicator_code='IMPORT_AMOUNT' ORDER BY date ASC").fetchall()
    export_map = {row["date"]: row["value"] for row in export_data}
    import_map = {row["date"]: row["value"] for row in import_data}
    
    prev_bal = None
    count = 0
    for date in sorted(export_map.keys()):
        if date in import_map:
            balance = export_map[date] - import_map[date]
            change_rate = _calc_change_rate(balance, prev_bal)
            save_indicator_data(conn, "TRADE_BALANCE", date, balance, prev_bal, change_rate, "BOK_CALCULATED")
            prev_bal = balance
            count += 1
    print(f"    → 무역수지 {count}건 갱신 완료")

    # ==========================================
    # 5. GDP & GNI 백필 (200Y102-Q, 200Y101-A)
    # ==========================================
    gdp_gni_indicators = [
        ("GDP_GROWTH", "200Y102", "10111", "Q"),
        ("GNI_PER_CAPITA", "200Y101", "1010601", "A"),
    ]
    for code, stat, item, cycle in gdp_gni_indicators:
        print(f"  GDP/GNI 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, start_date, end_date, item, cycle)
        if not rows:
            print("    → 데이터 없음")
            continue
        
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            if cycle == "Q":
                if len(date_str) == 6 and "Q" in date_str:
                    date = f"{date_str[:4]}-{date_str[4:]}"
                else:
                    quarter_map = {"01": "Q1", "04": "Q2", "07": "Q3", "10": "Q4"}
                    q = quarter_map.get(date_str[4:6], date_str[4:6])
                    date = f"{date_str[:4]}-{q}"
            else:
                date = f"{date_str[:4]}-12-31"
                
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, code, date, val, prev_val, change_rate, f"BOK_{stat}_{item}")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    # ==========================================
    # 6. 고용 백필 (901Y027, M)
    # ==========================================
    employment_indicators = [
        ("UNEMPLOYMENT_RATE", "901Y027", "I61BC"),
        ("EMPLOYMENT_RATE", "901Y027", "I61E"),
    ]
    for code, stat, item in employment_indicators:
        print(f"  고용 백필 중: {code}...")
        rows = _bok_stat_search(api_key, stat, start_date, end_date, item, "M")
        if not rows:
            print("    → 데이터 없음")
            continue
        
        rows.sort(key=lambda r: r.get("TIME", ""))
        
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            date = f"{date_str[:4]}-{date_str[4:6]}-01"
            try:
                val = float(val_str)
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, code, date, val, prev_val, change_rate, f"BOK_{stat}_{item}")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    # ==========================================
    # 7. 외환보유액 백필 (732Y001, M)
    # ==========================================
    print("  외환보유액 백필 중...")
    rows = _bok_stat_search(api_key, "732Y001", start_date, end_date, "99", "M")
    if rows:
        rows.sort(key=lambda r: r.get("TIME", ""))
        prev_val = None
        count = 0
        for row in rows:
            date_str = row.get("TIME", "")
            val_str = row.get("DATA_VALUE", "")
            if not date_str or not val_str:
                continue
            
            date = f"{date_str[:4]}-{date_str[4:6]}-01"
            try:
                val = float(val_str) / 1000.0  # 천달러 -> 백만달러 단위환산
            except ValueError:
                continue
            
            change_rate = _calc_change_rate(val, prev_val)
            save_indicator_data(conn, "FOREIGN_RESERVE", date, val, prev_val, change_rate, "BOK_732Y001_99")
            prev_val = val
            count += 1
        print(f"    → {count}건 저장 완료")

    conn.close()
    print("\n🎉 [백필 완료] 10년 치 경제 지표 적재 완료!")

if __name__ == "__main__":
    backfill_all()
