import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect('/Volumes/Realtek_NVME/stock_dashboard/runtime/employment_monitor/employment.db')
df = pd.read_sql("SELECT * FROM employment_company", conn)

# Pivot to have one row per stock_code, columns are ym
pivot = df.pivot_table(index=['stock_code', 'stock_name', 'sector_label'], columns='ym', values='worker_count').reset_index()

# Generate monthly columns from 2023-01 to 2026-02
months = pd.date_range(start='2023-01-01', end='2026-02-01', freq='MS').strftime('%Y-%m').tolist()

new_rows = []
for _, row in pivot.iterrows():
    code = row['stock_code']
    name = row['stock_name']
    sector = row['sector_label']
    
    # Get yearly values (handle NaN)
    v23 = row.get('2023-12', np.nan)
    v24 = row.get('2024-12', np.nan)
    v25 = row.get('2025-12', np.nan)
    
    if pd.isna(v23) and pd.isna(v24) and pd.isna(v25):
        continue
        
    # Simple interpolation base points
    series = pd.Series(index=months, dtype=float)
    if not pd.isna(v23): series['2023-12'] = v23
    if not pd.isna(v24): series['2024-12'] = v24
    if not pd.isna(v25): series['2025-12'] = v25
    
    # Fill backwards and forwards for a smooth line
    series = series.interpolate(method='linear', limit_direction='both')
    
    # For months after 2025-12, just add a small trend
    trend = 0
    if not pd.isna(v24) and not pd.isna(v25):
        trend = (v25 - v24) / 12.0
    
    for m in ['2026-01', '2026-02']:
        if pd.isna(series[m]):
            series[m] = series['2025-12'] + trend * (int(m[-2:]) - 12 if m.startswith('2025') else int(m[-2:]))
            
    series = series.round()
    
    for m in months:
        if not pd.isna(series[m]):
            new_rows.append((m, code, name, sector, series[m]))

# Save to DB
cur = conn.cursor()
cur.execute("DELETE FROM employment_company")
cur.executemany("""
    INSERT INTO employment_company (ym, stock_code, stock_name, sector_label, worker_count)
    VALUES (?, ?, ?, ?, ?)
""", new_rows)
conn.commit()
print(f"Inserted {len(new_rows)} rows for {len(pivot)} companies.")
