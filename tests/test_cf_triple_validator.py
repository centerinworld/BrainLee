import sqlite3

from collectors.cf_triple_validator import get_cf_values


def test_get_cf_values_prefers_cfs_when_ofs_was_inserted_first():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE cash_flow_data (
            id INTEGER PRIMARY KEY,
            stock_code TEXT,
            year INTEGER,
            is_annual INTEGER,
            data_source TEXT,
            report_type TEXT,
            operating_cf REAL,
            investing_cf REAL,
            cash_end REAL
        )
    """)
    conn.executemany("""
        INSERT INTO cash_flow_data
            (id, stock_code, year, is_annual, data_source, report_type,
             operating_cf, investing_cf, cash_end)
        VALUES (?, '000001', 2024, 1, 'dart_recollect_annual', ?, ?, ?, ?)
    """, [
        (1, 'OFS', 10.0, 20.0, 30.0),
        (2, 'CFS', 100.0, 200.0, 300.0),
    ])

    values = get_cf_values(conn, '000001', 2024)

    assert values['dart'] == {
        'operating_cf': 100.0,
        'investing_cf': 200.0,
        'cash_end': 300.0,
        'report_type': 'CFS',
    }
