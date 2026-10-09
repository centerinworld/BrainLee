import importlib.util
import sqlite3
from pathlib import Path

from data_write_gate import ensure_canonical_schema

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_canonical_schema(conn)
    conn.execute("""CREATE TABLE financial_data (id INTEGER PRIMARY KEY, stock_code TEXT, year INT, quarter INT, is_annual INT,
                    report_type TEXT, data_source TEXT, revenue REAL, operating_profit REAL, net_income REAL, total_assets REAL,
                    total_liabilities REAL, total_equity REAL, capital_stock REAL, eps REAL, bps REAL, dps REAL, roe REAL)""")
    conn.execute("""CREATE TABLE cash_flow_data (id INTEGER PRIMARY KEY, stock_code TEXT, year INT, quarter INT, is_annual INT,
                    report_type TEXT, data_source TEXT, operating_cf REAL, investing_cf REAL, financing_cf REAL, capex REAL,
                    cash_end REAL, depreciation REAL)""")
    return conn


def test_canonical_sync_mirrors_raw_rows_and_normalizes_annual_quarter():
    conn = _conn()
    conn.execute("INSERT INTO financial_data VALUES (1,'009150',2026,2,0,'OFS','t',100e8,10e8,8e8,1000e8,400e8,600e8,NULL,NULL,NULL,NULL,NULL)")
    conn.execute("INSERT INTO financial_data VALUES (2,'009150',2025,4,1,'OFS','t',400e8,40e8,30e8,900e8,300e8,600e8,NULL,NULL,NULL,NULL,NULL)")
    conn.execute("INSERT INTO cash_flow_data VALUES (1,'009150',2026,2,0,'OFS','t',50e8,-20e8,-5e8,10e8,NULL,NULL)")
    out = mod.canonical_sync(conn, "t_run", "2026-10-09T00:00:00", {1, 2}, {1})
    assert out["pass"] and out["fd_synced"] == 2 and out["cf_synced"] == 1 and out["fd_refused"] == 0
    # 연간 행은 정본에서 quarter=0 키
    r = conn.execute("SELECT quarter, revenue FROM canonical_financial_data WHERE year=2025 AND is_annual=1").fetchone()
    assert r[0] == 0 and r[1] == 400e8
    assert conn.execute("SELECT operating_cf FROM canonical_cashflow_data WHERE year=2026 AND quarter=2").fetchone()[0] == 50e8


def test_canonical_sync_backs_up_existing_and_skips_gate_refused():
    conn = _conn()
    conn.execute("""INSERT INTO canonical_financial_data (stock_code,year,quarter,is_annual,report_type,revenue,updated_at)
                    VALUES ('000660',2026,1,0,'OFS',10e8,'2026-10-01')""")
    # 영업이익 > 매출(게이트 거부) — 정본은 그대로, 거부로 집계
    conn.execute("INSERT INTO financial_data VALUES (3,'000660',2026,1,0,'OFS','t',10e8,30e8,5e8,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
    out = mod.canonical_sync(conn, "t_run", "2026-10-09T00:00:00", {3}, set())
    assert out["fd_refused"] == 1 and out["fd_synced"] == 0
    assert conn.execute("SELECT COUNT(*) FROM canonical_financial_data_backup_ofs_extra_20261009 WHERE run_id='t_run'").fetchone()[0] == 1
    assert conn.execute("SELECT revenue FROM canonical_financial_data WHERE stock_code='000660'").fetchone()[0] == 10e8


def test_q0_preferred_over_q4_when_both_annual_rows_exist():
    conn = _conn()
    # q=4 연간 행과 q=0 연간 행이 둘 다 존재하는 경우
    conn.execute("INSERT INTO financial_data VALUES (10,'009150',2025,4,1,'OFS','t',400e8,40e8,30e8,900e8,300e8,600e8,NULL,NULL,NULL,NULL,NULL)")
    conn.execute("INSERT INTO financial_data VALUES (11,'009150',2025,0,1,'OFS','t',450e8,45e8,35e8,950e8,350e8,600e8,NULL,NULL,NULL,NULL,NULL)")
    # ORDER BY quarter ASC로 쿼리할 때 id=11 (q=0)이 setdefault로 먼저 잡히는지 확인
    rows = conn.execute("SELECT id,stock_code,year,quarter,is_annual FROM financial_data WHERE report_type='OFS' ORDER BY quarter ASC").fetchall()
    fd_rows = {}
    for r in rows:
        r = tuple(r)
        k = "A" if r[4] else f"Q{r[3]}"
        fd_rows.setdefault((r[1], r[2], k), r)
    selected = fd_rows[('009150', 2025, 'A')]
    assert selected[0] == 11, f"q=0 행(id=11)이 선택되어야 함, 실제={selected[0]}"
    assert selected[3] == 0, "quarter는 0이어야 함"

