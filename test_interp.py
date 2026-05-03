import sqlite3
import pandas as pd
import numpy as np

def test_interpolation():
    conn = sqlite3.connect('/Applications/stock_dashboard/employment_monitor/employment.db')
    df = pd.read_sql("SELECT stock_code, stock_name, sector_label, ym, worker_count FROM employment_company WHERE ym IN ('2023-12', '2024-12', '2025-12')", conn)
    conn.execute(f"ATTACH DATABASE '/Applications/stock_dashboard/stock.db' AS main_db")
    markets = pd.read_sql("SELECT stock_code, market FROM stock_universe", conn)
    conn.close()
    
    pivot = df.pivot_table(index=['stock_code', 'stock_name', 'sector_label'], columns='ym', values='worker_count').reset_index()
    pivot = pivot.merge(markets, on='stock_code', how='left')
    
    months = pd.date_range(start='2023-01-01', end='2026-02-01', freq='MS').strftime('%Y-%m').tolist()
    
    res = []
    for _, row in pivot.iterrows():
        v23 = row.get('2023-12', np.nan)
        v24 = row.get('2024-12', np.nan)
        v25 = row.get('2025-12', np.nan)
        if pd.isna(v23) and pd.isna(v24) and pd.isna(v25): continue
        
        s = pd.Series(index=months, dtype=float)
        if not pd.isna(v23): s['2023-12'] = v23
        if not pd.isna(v24): s['2024-12'] = v24
        if not pd.isna(v25): s['2025-12'] = v25
        s = s.interpolate(method='linear', limit_direction='both')
        
        trend = 0
        if not pd.isna(v24) and not pd.isna(v25): trend = (v25 - v24) / 12.0
        
        for i, m in enumerate(['2026-01', '2026-02']):
            if pd.isna(s[m]): s[m] = s['2025-12'] + trend * (i + 1)
            
        s = s.round()
        
        latest = s['2026-02']
        diff_1m = latest - s['2026-01']
        diff_3m = latest - s['2025-11']
        diff_6m = latest - s['2025-08']
        diff_1y = latest - s['2025-02']
        
        res.append({
            'stock_code': row['stock_code'],
            'stock_name': row['stock_name'],
            'market': row['market'],
            'sector': row['sector_label'],
            'latest_count': latest,
            'diff_1m': diff_1m,
            'diff_3m': diff_3m,
            'diff_6m': diff_6m,
            'diff_1y': diff_1y,
        })
        
    res.sort(key=lambda x: x['diff_1m'], reverse=True)
    print("Top 3 by 1M increase:")
    for r in res[:3]: print(r)
    
test_interpolation()
