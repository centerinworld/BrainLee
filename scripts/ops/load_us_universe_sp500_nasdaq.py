#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import requests

DB = Path('/Applications/stock_dashboard/stock.db')
UA = 'StockDashboard AdminContact admin@example.com'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/plain,application/json,*/*',
    'Accept-Language': 'en-US,en;q=0.9',
}

SP500_URL = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
NASDAQ_LISTED_URL = 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'


def norm_ticker(t: str) -> str:
    return (t or '').strip().upper().replace('.', '-')


def load_sp500() -> dict[str, dict]:
    r = requests.get(SP500_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    out: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        t = norm_ticker(row.get('Symbol', ''))
        if not t:
            continue
        out[t] = {
            'company_name': (row.get('Security') or t).strip(),
            'sector': (row.get('GICS Sector') or '').strip(),
            'industry': (row.get('GICS Sub-Industry') or '').strip(),
            'index_name': 'S&P500',
            'exchange': '',
        }
    return out


def load_nasdaq_listed() -> dict[str, dict]:
    r = requests.get(NASDAQ_LISTED_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    txt = r.text
    lines = [ln for ln in txt.splitlines() if ln.strip() and not ln.startswith('File Creation Time')]
    reader = csv.DictReader(io.StringIO('\n'.join(lines)), delimiter='|')

    out: dict[str, dict] = {}
    for row in reader:
        t = norm_ticker(row.get('Symbol', ''))
        if not t:
            continue
        if (row.get('Test Issue') or '').strip().upper() == 'Y':
            continue
        if (row.get('ETF') or '').strip().upper() == 'Y':
            continue
        if (row.get('NextShares') or '').strip().upper() == 'Y':
            continue
        # 권리주/워런트/유닛 과도 유입 방지 (보통 Security Name에 포함)
        sec_name = (row.get('Security Name') or '').strip()
        sn = sec_name.upper()
        bad_tokens = [' WARRANT', ' RIGHTS', ' UNIT', ' PREFERRED', ' DEPOSITARY']
        if any(tok in sn for tok in bad_tokens):
            continue
        out[t] = {
            'company_name': sec_name or t,
            'sector': '',
            'industry': '',
            'index_name': 'NASDAQ',
            'exchange': 'NASDAQ',
        }
    return out


def ensure_table(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS us_stock_meta (
            ticker TEXT PRIMARY KEY,
            company_name TEXT,
            exchange TEXT,
            index_name TEXT,
            sector TEXT,
            industry TEXT,
            market_cap REAL,
            country TEXT DEFAULT 'US',
            currency TEXT DEFAULT 'USD',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def main() -> int:
    sp = load_sp500()
    nd = load_nasdaq_listed()

    merged: dict[str, dict] = {}
    # NASDAQ 전체 먼저
    for t, v in nd.items():
        merged[t] = dict(v)
    # S&P500 덮어쓰기(우선 표시) + 섹터/산업 보강
    for t, v in sp.items():
        cur = merged.get(t, {'company_name': v['company_name'], 'sector': '', 'industry': '', 'index_name': 'NASDAQ', 'exchange': ''})
        cur['company_name'] = v['company_name'] or cur.get('company_name')
        cur['sector'] = v['sector'] or cur.get('sector')
        cur['industry'] = v['industry'] or cur.get('industry')
        cur['index_name'] = 'S&P500'
        merged[t] = cur

    conn = sqlite3.connect(str(DB), timeout=120)
    conn.execute('PRAGMA busy_timeout=120000')
    try:
        ensure_table(conn)
        for t, v in merged.items():
            conn.execute(
                """
                INSERT INTO us_stock_meta
                (ticker, company_name, exchange, index_name, sector, industry, market_cap, country, currency, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT market_cap FROM us_stock_meta WHERE ticker=?), NULL), 'US', 'USD', CURRENT_TIMESTAMP)
                ON CONFLICT(ticker) DO UPDATE SET
                  company_name=COALESCE(excluded.company_name, us_stock_meta.company_name),
                  exchange=COALESCE(excluded.exchange, us_stock_meta.exchange),
                  index_name=CASE WHEN excluded.index_name='S&P500' THEN 'S&P500' ELSE us_stock_meta.index_name END,
                  sector=COALESCE(NULLIF(excluded.sector,''), us_stock_meta.sector),
                  industry=COALESCE(NULLIF(excluded.industry,''), us_stock_meta.industry),
                  country='US',
                  currency='USD',
                  updated_at=CURRENT_TIMESTAMP
                """,
                (t, v.get('company_name'), v.get('exchange'), v.get('index_name'), v.get('sector'), v.get('industry'), t),
            )
        conn.commit()

        counts = conn.execute(
            """
            SELECT index_name, COUNT(DISTINCT ticker) AS cnt
            FROM us_stock_meta
            WHERE country='US'
            GROUP BY index_name
            ORDER BY index_name
            """
        ).fetchall()
        total = conn.execute("SELECT COUNT(DISTINCT ticker) FROM us_stock_meta WHERE country='US'").fetchone()[0]

        payload = {
            'run_at': datetime.now().isoformat(timespec='seconds'),
            'sp500_source': len(sp),
            'nasdaq_source': len(nd),
            'merged_upserted': len(merged),
            'db_total': total,
            'db_by_index_name': {r[0]: r[1] for r in counts},
        }
        out = Path('/Applications/stock_dashboard/scratch') / f"us_universe_load_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        print(str(out))
        print(json.dumps(payload, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
