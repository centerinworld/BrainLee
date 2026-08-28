#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P("/Applications/stock_dashboard")))
from scripts.validate_financial_multi_source import fetch_fnguide_annual_fs, fetch_fnguide_eps_bps

DB_PATH = Path('/Applications/stock_dashboard/stock.db')
OUT_DIR = Path('/Applications/stock_dashboard/scratch')
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://finance.naver.com/',
}

@dataclass
class Tol:
    pct: float
    abs: float

TOL = {
    'revenue': Tol(0.03, 5e8),
    'operating_profit': Tol(0.03, 3e8),
    'net_income': Tol(0.04, 3e8),
    'eps': Tol(0.05, 30),
    'bps': Tol(0.05, 80),
    'per': Tol(0.06, 0.5),
    'pbr': Tol(0.06, 0.08),
    'eps_naver': Tol(0.06, 40),
}


def close_match(a: Optional[float], b: Optional[float], tol: Tol) -> Optional[bool]:
    if a is None or b is None:
        return None
    diff = abs(a - b)
    if diff <= tol.abs:
        return True
    denom = max(abs(a), abs(b), 1e-12)
    return (diff / denom) <= tol.pct


def parse_num(s: str) -> Optional[float]:
    s = (s or '').strip().replace(',', '').replace('N/A', '').replace('nan', '')
    if not s or s == '-':
        return None
    try:
        return float(s)
    except Exception:
        return None


def scrape_naver(code: str) -> dict:
    try:
        r = requests.get(f'https://finance.naver.com/item/main.naver?code={code}', headers=HEADERS, timeout=12)
        r.encoding = 'euc-kr'
        soup = BeautifulSoup(r.text, 'html.parser')
        def sel(css):
            n = soup.select_one(css)
            return parse_num(n.text if n else '')
        return {
            'per': sel('em#_per'),
            'pbr': sel('em#_pbr'),
            'eps': sel('em#_eps'),
        }
    except Exception:
        return {'per': None, 'pbr': None, 'eps': None}


def get_major_600(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    q = """
    WITH latest AS (
      SELECT stock_code, MAX(base_date) AS max_dt
      FROM stock_universe
      WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      GROUP BY stock_code
    ), u AS (
      SELECT su.stock_code, su.stock_name, su.market, su.market_cap,
             su.per AS db_per, su.pbr AS db_pbr, su.eps AS db_eps
      FROM stock_universe su
      JOIN latest l ON su.stock_code=l.stock_code AND su.base_date=l.max_dt
      WHERE COALESCE(su.stock_type, '보통주')='보통주'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
    ), k AS (
      SELECT * FROM u
      WHERE market IN ('유가증권','코스피','KOSPI')
      ORDER BY market_cap DESC
      LIMIT 300
    ), qd AS (
      SELECT * FROM u
      WHERE market IN ('코스닥','KOSDAQ')
      ORDER BY market_cap DESC
      LIMIT 300
    )
    SELECT 'KOSPI' AS mkt_grp, * FROM k
    UNION ALL
    SELECT 'KOSDAQ' AS mkt_grp, * FROM qd
    """
    return conn.execute(q).fetchall()


def latest_annual_fin(conn: sqlite3.Connection, code: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT year, revenue, operating_profit, net_income, eps, bps
        FROM financial_data
        WHERE stock_code=? AND is_annual=1
        ORDER BY year DESC, CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END, id DESC
        LIMIT 1
        """,
        (code,),
    ).fetchone()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = get_major_600(conn)

    out_rows = []
    for i, r in enumerate(rows, 1):
        code = r['stock_code']
        name = r['stock_name']
        mkt = r['mkt_grp']
        db_fin = latest_annual_fin(conn, code)
        fy = int(db_fin['year']) if db_fin else None

        fg_fs = fetch_fnguide_annual_fs(code)
        fg_eps = fetch_fnguide_eps_bps(code)
        fg_y = fg_fs.get(fy, {}) if fy else {}
        fg_ey = fg_eps.get(fy, {}) if fy else {}

        nv = scrape_naver(code)

        checks = {}
        checks['revenue'] = close_match(db_fin['revenue'] if db_fin else None, fg_y.get('revenue'), TOL['revenue'])
        checks['operating_profit'] = close_match(db_fin['operating_profit'] if db_fin else None, fg_y.get('operating_profit'), TOL['operating_profit'])
        checks['net_income'] = close_match(db_fin['net_income'] if db_fin else None, fg_y.get('net_income'), TOL['net_income'])
        checks['eps'] = close_match(db_fin['eps'] if db_fin else None, fg_ey.get('eps'), TOL['eps'])
        checks['bps'] = close_match(db_fin['bps'] if db_fin else None, fg_ey.get('bps'), TOL['bps'])
        checks['per'] = close_match(r['db_per'], nv.get('per'), TOL['per'])
        checks['pbr'] = close_match(r['db_pbr'], nv.get('pbr'), TOL['pbr'])
        checks['eps_naver'] = close_match(r['db_eps'], nv.get('eps'), TOL['eps_naver'])

        out_rows.append({
            'market': mkt,
            'stock_code': code,
            'stock_name': name,
            'year': fy,
            'db_revenue': db_fin['revenue'] if db_fin else None,
            'fg_revenue': fg_y.get('revenue'),
            'ok_revenue': checks['revenue'],
            'db_operating_profit': db_fin['operating_profit'] if db_fin else None,
            'fg_operating_profit': fg_y.get('operating_profit'),
            'ok_operating_profit': checks['operating_profit'],
            'db_net_income': db_fin['net_income'] if db_fin else None,
            'fg_net_income': fg_y.get('net_income'),
            'ok_net_income': checks['net_income'],
            'db_eps': db_fin['eps'] if db_fin else None,
            'fg_eps': fg_ey.get('eps'),
            'ok_eps': checks['eps'],
            'db_bps': db_fin['bps'] if db_fin else None,
            'fg_bps': fg_ey.get('bps'),
            'ok_bps': checks['bps'],
            'db_per': r['db_per'],
            'naver_per': nv.get('per'),
            'ok_per': checks['per'],
            'db_pbr': r['db_pbr'],
            'naver_pbr': nv.get('pbr'),
            'ok_pbr': checks['pbr'],
            'db_eps_univ': r['db_eps'],
            'naver_eps': nv.get('eps'),
            'ok_eps_naver': checks['eps_naver'],
        })

        if i % 30 == 0:
            print(f'progress {i}/{len(rows)}')
        time.sleep(0.12)

    # summarize
    fields = [
        'revenue', 'operating_profit', 'net_income', 'eps', 'bps', 'per', 'pbr', 'eps_naver'
    ]
    summary = {'total_stocks': len(out_rows), 'by_field': {}, 'by_market': {}}

    for f in fields:
        key = 'ok_' + f
        vals = [x[key] for x in out_rows]
        t = sum(v is not None for v in vals)
        p = sum(v is True for v in vals)
        fail = sum(v is False for v in vals)
        summary['by_field'][f] = {
            'coverage': t,
            'pass': p,
            'fail': fail,
            'pass_rate_pct': round((p / t * 100), 2) if t else None,
        }

    for mkt in ('KOSPI', 'KOSDAQ'):
        mr = [x for x in out_rows if x['market'] == mkt]
        msum = {}
        for f in fields:
            vals = [x['ok_' + f] for x in mr]
            t = sum(v is not None for v in vals)
            p = sum(v is True for v in vals)
            msum[f] = round((p / t * 100), 2) if t else None
        summary['by_market'][mkt] = {'count': len(mr), 'pass_rate_pct': msum}

    ts = time.strftime('%Y%m%d_%H%M%S')
    csv_path = OUT_DIR / f'major600_verify_{ts}.csv'
    json_path = OUT_DIR / f'major600_verify_{ts}.json'

    with csv_path.open('w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    json_path.write_text(json.dumps({'summary': summary, 'rows': out_rows[:200]}, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps({'summary': summary, 'csv': str(csv_path), 'json': str(json_path)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
