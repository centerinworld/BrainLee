#!/usr/bin/env python3
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
import csv
import math

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
OUT_DIR = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def has_col(conn, table, col):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def src_family(s):
    s = (s or '').lower()
    if 'fnguide' in s:
        return 'fnguide'
    if 'dart' in s:
        return 'dart'
    if 'legacy' in s:
        return 'legacy'
    if 'seibro' in s:
        return 'seibro'
    if 'naver' in s:
        return 'naver'
    if 'null' in s:
        return 'null'
    return 'unknown'


def safe_float(v):
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except Exception:
        return None


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # schema
    if not has_col(conn, 'cash_flow_data', 'depreciation_q'):
        conn.execute('ALTER TABLE cash_flow_data ADD COLUMN depreciation_q REAL')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS depreciation_q_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          stock_code TEXT NOT NULL,
          year INTEGER NOT NULL,
          quarter INTEGER NOT NULL,
          report_type TEXT NOT NULL,
          row_id INTEGER,
          old_depreciation_q REAL,
          new_depreciation_q REAL,
          cause_code TEXT,
          note TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS depr_q_rebuild_cause (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_ts TEXT NOT NULL,
          stock_code TEXT NOT NULL,
          year INTEGER NOT NULL,
          report_type TEXT NOT NULL,
          cause_code TEXT NOT NULL,
          note TEXT
        )
    ''')

    groups = conn.execute('''
        SELECT stock_code, year, COALESCE(report_type,'CFS') AS report_type
        FROM cash_flow_data
        GROUP BY stock_code, year, COALESCE(report_type,'CFS')
    ''').fetchall()

    cause_rows = []
    cause_counter = Counter()
    fix_count = 0
    skip_count = 0

    for g in groups:
        code = g['stock_code']
        year = int(g['year'])
        rt = g['report_type']

        qs = conn.execute('''
            SELECT id, quarter, data_source, depreciation, depreciation_q
            FROM cash_flow_data
            WHERE stock_code=? AND year=? AND is_annual=0 AND COALESCE(report_type,'CFS')=?
            ORDER BY quarter, id
        ''', (code, year, rt)).fetchall()

        ann = conn.execute('''
            SELECT id, quarter, data_source, depreciation
            FROM cash_flow_data
            WHERE stock_code=? AND year=? AND is_annual=1 AND COALESCE(report_type,'CFS')=?
            ORDER BY id DESC
            LIMIT 1
        ''', (code, year, rt)).fetchone()

        qmap = {}
        for r in qs:
            q = int(r['quarter'])
            # latest row wins for each quarter
            qmap[q] = r

        need_q = [1,2,3]
        if not all(q in qmap for q in need_q):
            cause = 'MISSING_Q123'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, 'missing q1~q3 rows'))
            skip_count += 1
            continue

        q1 = safe_float(qmap[1]['depreciation'])
        q2 = safe_float(qmap[2]['depreciation'])
        q3 = safe_float(qmap[3]['depreciation'])
        if None in (q1, q2, q3):
            cause = 'NULL_Q123_DEPR'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, 'null in q1~q3 depreciation'))
            skip_count += 1
            continue

        if ann is None or safe_float(ann['depreciation']) is None:
            cause = 'MISSING_ANNUAL_DEPR'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, 'missing annual depreciation'))
            skip_count += 1
            continue

        ann_dep = safe_float(ann['depreciation'])
        fam_q = src_family(qmap[3]['data_source'])
        fam_ann = src_family(ann['data_source'])

        if fam_q != fam_ann:
            cause = 'MIXED_SOURCE_ANNUAL_Q'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, f'q3_src={qmap[3]["data_source"]}, annual_src={ann["data_source"]}'))

        # cumulative sanity
        if not (abs(q1) <= abs(q2) * 1.25 + 1 and abs(q2) <= abs(q3) * 1.25 + 1):
            cause = 'NON_MONOTONIC_CUMULATIVE'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, f'q1={q1},q2={q2},q3={q3}'))

        # derive quarterly depreciation
        d1 = q1
        d2 = q2 - q1
        d3 = q3 - q2
        d4 = ann_dep - q3

        # outlier flag only (do not block write)
        med = sorted([abs(d1), abs(d2), abs(d3)])[1]
        if med > 0 and abs(d4) > med * 2.5:
            cause = 'Q4_SPIKE_FROM_ANNUAL_DELTA'
            cause_counter[cause] += 1
            cause_rows.append((code, year, rt, cause, f'd1={d1:.0f},d2={d2:.0f},d3={d3:.0f},d4={d4:.0f},annual={ann_dep:.0f},q3cum={q3:.0f}'))

        # 음수 분기 감가상각은 데이터 오염으로 간주하고 저장 금지(NULL)
        derived = {
            1: (d1 if d1 is None or d1 >= 0 else None),
            2: (d2 if d2 is None or d2 >= 0 else None),
            3: (d3 if d3 is None or d3 >= 0 else None),
        }
        if 4 in qmap:
            derived[4] = (d4 if d4 is None or d4 >= 0 else None)

        for q, dv in derived.items():
            if dv is None:
                cause = 'NEGATIVE_DERIVED_DEPR_NULL'
                cause_counter[cause] += 1
                cause_rows.append((code, year, rt, cause, f'q{q} derived<0 null-guard applied'))

        for q, nv in derived.items():
            row = qmap[q]
            old_q = safe_float(row['depreciation_q'])
            if old_q is not None and abs(old_q - nv) < 1:
                continue
            conn.execute('''
                UPDATE cash_flow_data
                SET depreciation_q=?
                WHERE id=?
            ''', (nv, row['id']))
            conn.execute('''
                INSERT INTO depreciation_q_fix_log
                (fixed_at,stock_code,year,quarter,report_type,row_id,old_depreciation_q,new_depreciation_q,cause_code,note)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            ''', (
                datetime.now().isoformat(timespec='seconds'), code, year, q, rt, row['id'], old_q, nv,
                'REBUILD_DEPR_Q',
                f"annual={ann_dep:.0f}, q1={q1:.0f}, q2={q2:.0f}, q3={q3:.0f}"
            ))
            fix_count += 1

    conn.commit()

    # 원인 테이블 적재(실행 이력 보존)
    run_ts = datetime.now().isoformat(timespec='seconds')
    conn.execute("DELETE FROM depr_q_rebuild_cause WHERE run_ts=?", (run_ts,))
    conn.executemany(
        '''
        INSERT INTO depr_q_rebuild_cause(run_ts, stock_code, year, report_type, cause_code, note)
        VALUES(?,?,?,?,?,?)
        ''',
        [(run_ts, r[0], r[1], r[2], r[3], r[4]) for r in cause_rows]
    )
    conn.commit()

    # detail report csv
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    detail_csv = OUT_DIR / f'depr_q_cause_detail_{ts}.csv'
    with detail_csv.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['stock_code','year','report_type','cause_code','note'])
        for r in cause_rows:
            w.writerow(r)

    summary_csv = OUT_DIR / f'depr_q_cause_summary_{ts}.csv'
    with summary_csv.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['cause_code','count'])
        for k,v in cause_counter.most_common():
            w.writerow([k,v])

    # ALT snapshot
    alt_rows = conn.execute('''
        SELECT year, quarter, is_annual, COALESCE(report_type,'CFS') report_type,
               data_source, depreciation, depreciation_q, value_type
        FROM cash_flow_data
        WHERE stock_code='172670'
        ORDER BY year, is_annual, quarter, id
    ''').fetchall()
    alt_csv = OUT_DIR / f'alt_172670_depr_snapshot_{ts}.csv'
    with alt_csv.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['year','quarter','is_annual','report_type','data_source','depreciation','depreciation_q','value_type'])
        for r in alt_rows:
            w.writerow([r['year'],r['quarter'],r['is_annual'],r['report_type'],r['data_source'],r['depreciation'],r['depreciation_q'],r['value_type']])

    conn.close()

    print('DONE')
    print('fix_count=', fix_count)
    print('skip_count=', skip_count)
    print('run_ts=', run_ts)
    print('detail_csv=', detail_csv)
    print('summary_csv=', summary_csv)
    print('alt_csv=', alt_csv)

if __name__ == '__main__':
    main()
