import sqlite3
import os
import pandas as pd
import numpy as np
from typing import Optional
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/employment-v2", tags=["employment-v2"])

DIR = os.path.dirname(__file__)
EMP_DB = os.path.join(DIR, "employment.db")
STOCK_DB = "/Applications/stock_dashboard/stock.db"

@router.get("/yearly")
def get_yearly_employment(limit: int = 200, sort_by: str = "count"):
    """연도별 고용인원(25, 24, 23년) 가로 배치 및 증감률"""
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
        
        order_clause = "count_26 DESC NULLS LAST"
        if sort_by == "increase":
            order_clause = "count_25 DESC NULLS LAST"
        elif sort_by == "increase_pct":
            order_clause = "count_24 DESC NULLS LAST"
            
        # NPS 데이터의 각 연도별 순증가를 계산
        query = f"""
            SELECT 
                u.stock_code,
                u.stock_name,
                u.market,
                u.sector_small as sector,
                SUM(CASE WHEN n.ym LIKE '2026%' THEN n.nw_acqzr_cnt - n.lss_jnngp_cnt ELSE 0 END) as count_26,
                SUM(CASE WHEN n.ym LIKE '2025%' THEN n.nw_acqzr_cnt - n.lss_jnngp_cnt ELSE 0 END) as count_25,
                SUM(CASE WHEN n.ym LIKE '2024%' THEN n.nw_acqzr_cnt - n.lss_jnngp_cnt ELSE 0 END) as count_24
            FROM main_db.stock_universe u
            LEFT JOIN nps_workplace_monthly n ON u.stock_code = n.stock_code
            WHERE u.market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')
            GROUP BY u.stock_code
            ORDER BY {order_clause}
            LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        
        # 최신 수집일자 가져오기
        updated_at_row = conn.execute("SELECT MAX(fetched_at) FROM nps_workplace_monthly").fetchone()
        updated_at = updated_at_row[0][:10] if updated_at_row and updated_at_row[0] else "2026-04-30"
        
        result = []
        for r in rows:
            d = dict(r)
            # 증감률은 순증가 데이터에서는 의미가 모호할 수 있으나, 전년도 대비 증감으로 계산 (예: 25년 순증가 - 24년 순증가)
            # NPS 기반에서는 % 보다 절대 증감량이 더 의미있으므로 yoy는 None 처리하거나 절대값 증감을 보냅니다.
            # 여기서는 프론트엔드 호환성을 위해 yoy_26, yoy_25를 유지하되, 값은 증감률이 아닌 증감량 차이를 보냄.
            # (프론트엔드 수정 필요)
            d['yoy_26'] = None 
            d['yoy_25'] = None
            d['yoy_24'] = None
            result.append(d)
            
        return {"rows": result, "date": updated_at}
    finally:
        conn.close()

# 전역 캐시
_trend_data_cache = None

def get_trend_data():
    global _trend_data_cache
    if _trend_data_cache is not None:
        return _trend_data_cache
        
    conn = sqlite3.connect(EMP_DB)
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
    
    # 1. KOSPI/KOSDAQ 종목 가져오기
    markets = pd.read_sql("SELECT stock_code, stock_name, market, sector_small as sector FROM main_db.stock_universe WHERE market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')", conn)
    
    # 2. NPS 데이터 가져오기 (전체 데이터)
    nps_df = pd.read_sql("""
        SELECT ym, stock_code, nw_acqzr_cnt as new_cnt, lss_jnngp_cnt as lost_cnt, (nw_acqzr_cnt - lss_jnngp_cnt) as net_change
        FROM nps_workplace_monthly
        ORDER BY ym ASC
    """, conn)
    conn.close()
    
    # NPS Pivot for net_change
    available_months = sorted(nps_df['ym'].unique(), reverse=True)
    nps_pivot = nps_df.pivot_table(index='stock_code', columns='ym', values='net_change', aggfunc='sum').fillna(0) if not nps_df.empty else pd.DataFrame()
    
    # NPS dictionary for chart history
    history_dict = {}
    for _, row in nps_df.iterrows():
        code = row['stock_code']
        if code not in history_dict:
            history_dict[code] = []
        ym = row['ym']
        history_dict[code].append({
            'month': f"{ym[:4]}-{ym[4:]}",
            'new_cnt': int(row['new_cnt']),
            'lost_cnt': int(row['lost_cnt']),
            'net_change': int(row['net_change'])
        })
    
    # Find specific target months
    target_0m = available_months[0] if len(available_months) > 0 else None
    target_1m = available_months[1] if len(available_months) > 1 else None
    target_3m = available_months[3] if len(available_months) > 3 else None
    target_6m = available_months[6] if len(available_months) > 6 else None
    target_1y = available_months[12] if len(available_months) > 12 else None
    
    res = []
    for _, row in markets.iterrows():
        code = row['stock_code']
        
        diff_0m = diff_1m = diff_3m = diff_6m = diff_1y = None
        history = history_dict.get(code, [])
        
        if code in nps_pivot.index:
            stock_nps = nps_pivot.loc[code]
            if target_0m: diff_0m = stock_nps.get(target_0m, None)
            if target_1m: diff_1m = stock_nps.get(target_1m, None)
            if target_3m: diff_3m = stock_nps.get(target_3m, None)
            if target_6m: diff_6m = stock_nps.get(target_6m, None)
            if target_1y: diff_1y = stock_nps.get(target_1y, None)
            
        res.append({
            'stock_code': code,
            'stock_name': row['stock_name'],
            'market': row['market'],
            'sector': row['sector'],
            'latest_count': None, # 사업보고서 미사용
            'diff_0m': int(diff_0m) if not pd.isna(diff_0m) else None,
            'diff_1m': int(diff_1m) if not pd.isna(diff_1m) else None,
            'diff_3m': int(diff_3m) if not pd.isna(diff_3m) else None,
            'diff_6m': int(diff_6m) if not pd.isna(diff_6m) else None,
            'diff_1y': int(diff_1y) if not pd.isna(diff_1y) else None,
            'history': history
        })
        
    _trend_data_cache = res
    return _trend_data_cache

@router.get("/trend")
def get_nps_trend(sort_by: str = "1m", limit: int = 100):
    data = get_trend_data()
    sort_key = f'diff_{sort_by}'
    
    # Filter out NaNs and sort
    valid_data = [d for d in data if not pd.isna(d.get(sort_key))]
    valid_data.sort(key=lambda x: x[sort_key], reverse=True)
    
    # Remove full history from the list endpoint to save bandwidth
    result = []
    for d in valid_data[:limit]:
        item = d.copy()
        item.pop('history', None)
        result.append(item)
        
    # 최신 수집일자 가져오기
    conn = sqlite3.connect(EMP_DB)
    updated_at_row = conn.execute("SELECT MAX(fetched_at) FROM nps_workplace_monthly").fetchone()
    conn.close()
    updated_at = updated_at_row[0][:10] if updated_at_row and updated_at_row[0] else "2026-04-30"
    
    return {"rows": result, "date": updated_at}

@router.get("/chart")
def get_nps_chart(query: str = Query(..., description="Stock code or name")):
    data = get_trend_data()
    # Find matching company
    for d in data:
        if d['stock_code'] == query or d['stock_name'] == query:
            if not d['history']:
                return {"company": d['stock_name'], "history": [], "notFound": True}
            return {"company": d['stock_name'], "history": d['history']}
            
    # Search by partial name if no exact match
    for d in data:
        if query in d['stock_name']:
            if not d['history']:
                return {"company": d['stock_name'], "history": [], "notFound": True}
            return {"company": d['stock_name'], "history": d['history']}
            
    return {"company": None, "history": [], "notFound": True}
