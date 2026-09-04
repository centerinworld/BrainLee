"""고용 정보 API"""
import sqlite3
from fastapi import APIRouter, Query

router = APIRouter()

EMP_DB = "employment_monitor/employment.db"
MAIN_DB = "stock.db"


def _emp_conn():
    conn = sqlite3.connect(EMP_DB)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/company-list")
def get_company_list(
    ym: str = Query(None, description="YYYY-12 형식"),
    limit: int = Query(200),
):
    """연도별 고용인원 목록"""
    conn = _emp_conn()
    try:
        if not ym:
            row = conn.execute("SELECT MAX(ym) FROM employment_company").fetchone()
            ym = row[0] if row else "2025-12"
        rows = conn.execute(
            """SELECT e.ym, e.stock_code, e.stock_name, e.sector_label,
                      e.worker_count, e.yoy_change, e.mom_change
               FROM employment_company e
               WHERE e.ym = ?
               ORDER BY e.worker_count DESC NULLS LAST
               LIMIT ?""",
            (ym, limit),
        ).fetchall()
        years = [r[0] for r in conn.execute(
            "SELECT DISTINCT ym FROM employment_company ORDER BY ym DESC"
        ).fetchall()]
        return {
            "ym": ym,
            "years": years,
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/available-years")
def get_available_years():
    conn = _emp_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT ym FROM employment_company ORDER BY ym DESC"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@router.get("/nps-monthly")
def get_nps_monthly(
    ym: str = Query(None, description="YYYYMM 형식, 미지정 시 최신"),
    limit: int = Query(100),
    order: str = Query("net", description="net=순증가, acq=신규, lss=상실"),
):
    """NPS 사업장 월별 고용 변동 (신규·상실)"""
    conn = _emp_conn()
    try:
        if not ym:
            row = conn.execute("SELECT MAX(ym) FROM nps_workplace_monthly WHERE ym LIKE '2%'").fetchone()
            ym = row[0] if row else "202602"
        order_sql = {
            "acq": "nw_acqzr_cnt DESC",
            "lss": "lss_jnngp_cnt DESC",
            "net": "(nw_acqzr_cnt - lss_jnngp_cnt) DESC",
        }.get(order, "(nw_acqzr_cnt - lss_jnngp_cnt) DESC")
        conn.execute(f"ATTACH DATABASE '/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db' AS main_db")
        rows = conn.execute(
            f"""SELECT n.ym, n.stock_code, n.stock_name,
                       n.nw_acqzr_cnt, n.lss_jnngp_cnt,
                       (n.nw_acqzr_cnt - n.lss_jnngp_cnt) AS net_change,
                       m.market
                FROM nps_workplace_monthly n
                LEFT JOIN main_db.stock_universe m ON n.stock_code = m.stock_code
                WHERE n.ym = ?
                ORDER BY {order_sql}
                LIMIT ?""",
            (ym, limit),
        ).fetchall()
        months = [r[0] for r in conn.execute(
            "SELECT DISTINCT ym FROM nps_workplace_monthly WHERE ym LIKE '2%' ORDER BY ym DESC LIMIT 24"
        ).fetchall()]
        
        updated_at_row = conn.execute("SELECT MAX(fetched_at) FROM nps_workplace_monthly WHERE ym = ?", (ym,)).fetchone()
        updated_at = updated_at_row[0] if updated_at_row and updated_at_row[0] else None
        
        return {
            "ym": ym,
            "months": months,
            "rows": [dict(r) for r in rows],
            "updated_at": updated_at,
        }
    finally:
        conn.close()


@router.get("/nps-trend/{stock_code}")
def get_nps_trend(stock_code: str, months: int = Query(12)):
    """특정 종목의 NPS 월별 추이"""
    conn = _emp_conn()
    try:
        rows = conn.execute(
            """SELECT ym, nw_acqzr_cnt, lss_jnngp_cnt,
                      (nw_acqzr_cnt - lss_jnngp_cnt) AS net_change
               FROM nps_workplace_monthly
               WHERE stock_code = ?
               ORDER BY ym DESC
               LIMIT ?""",
            (stock_code, months),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()
