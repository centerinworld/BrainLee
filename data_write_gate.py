import sqlite3
from datetime import datetime
from typing import Dict, Tuple, Any

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"


def ensure_canonical_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS write_gate_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      gate_ts TEXT NOT NULL,
      table_name TEXT NOT NULL,
      stock_code TEXT NOT NULL,
      year INTEGER,
      quarter INTEGER,
      is_annual INTEGER,
      report_type TEXT,
      level TEXT NOT NULL,
      reason_code TEXT NOT NULL,
      message TEXT,
      payload_json TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS canonical_financial_data (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      stock_code TEXT NOT NULL,
      year INTEGER NOT NULL,
      quarter INTEGER NOT NULL,
      is_annual INTEGER NOT NULL,
      report_type TEXT NOT NULL DEFAULT 'CFS',
      revenue REAL,
      operating_profit REAL,
      net_income REAL,
      total_assets REAL,
      total_liabilities REAL,
      total_equity REAL,
      capital_stock REAL,
      eps REAL,
      bps REAL,
      dps REAL,
      roe REAL,
      data_source TEXT,
      source_row_id INTEGER,
      rule_version TEXT NOT NULL DEFAULT 'wg_v1',
      decision_reason TEXT,
      quality_score REAL,
      updated_at TEXT NOT NULL,
      UNIQUE(stock_code, year, quarter, is_annual, report_type)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS canonical_cashflow_data (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      stock_code TEXT NOT NULL,
      year INTEGER NOT NULL,
      quarter INTEGER NOT NULL,
      is_annual INTEGER NOT NULL,
      report_type TEXT NOT NULL DEFAULT 'CFS',
      operating_cf REAL,
      investing_cf REAL,
      financing_cf REAL,
      capex REAL,
      cash_end REAL,
      depreciation REAL,
      operating_cf_q REAL,
      investing_cf_q REAL,
      financing_cf_q REAL,
      capex_q REAL,
      value_type TEXT,
      data_source TEXT,
      source_row_id INTEGER,
      rule_version TEXT NOT NULL DEFAULT 'wg_v1',
      decision_reason TEXT,
      quality_score REAL,
      updated_at TEXT NOT NULL,
      UNIQUE(stock_code, year, quarter, is_annual, report_type)
    )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_canon_fin_code_yq ON canonical_financial_data(stock_code, year DESC, quarter DESC, report_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canon_cf_code_yq ON canonical_cashflow_data(stock_code, year DESC, quarter DESC, report_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wg_log_code_ts ON write_gate_log(stock_code, gate_ts DESC)")


def _log(conn: sqlite3.Connection, table_name: str, p: Dict[str, Any], level: str, code: str, msg: str) -> None:
    conn.execute(
        """
        INSERT INTO write_gate_log
        (gate_ts, table_name, stock_code, year, quarter, is_annual, report_type, level, reason_code, message, payload_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now().isoformat(timespec="seconds"),
            table_name,
            str(p.get("stock_code") or ""),
            p.get("year"),
            p.get("quarter"),
            1 if p.get("is_annual") else 0,
            str(p.get("report_type") or "CFS"),
            level,
            code,
            msg,
            str(p),
        ),
    )


def gate_financial_row(conn: sqlite3.Connection, p: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    q = dict(p)
    revenue = q.get("revenue")
    operating_profit = q.get("operating_profit")

    # Revenue is a top-line flow for a whole reporting period. A negative value
    # here is an extraction/sign-mapping error, not a value that downstream
    # valuation calculations can safely consume.
    if revenue is not None:
        try:
            revenue_f = float(revenue)
        except (TypeError, ValueError):
            _log(conn, "financial_data", q, "critical", "REVENUE_CAST_ERROR", "매출 숫자 캐스팅 실패")
            return False, q, "REVENUE_CAST_ERROR"
        if revenue_f < 0:
            _log(conn, "financial_data", q, "critical", "NEGATIVE_REVENUE", f"매출 음수 차단: {revenue_f}")
            return False, q, "NEGATIVE_REVENUE"
        if operating_profit is not None and revenue_f > 0:
            try:
                operating_profit_f = float(operating_profit)
            except (TypeError, ValueError):
                _log(conn, "financial_data", q, "critical", "OPERATING_PROFIT_CAST_ERROR", "영업이익 숫자 캐스팅 실패")
                return False, q, "OPERATING_PROFIT_CAST_ERROR"
            # A small tolerance permits reporting-unit rounding, while catching
            # swapped or incorrectly scaled income-statement fields.
            if operating_profit_f > revenue_f * 1.05:
                _log(conn, "financial_data", q, "critical", "OPERATING_PROFIT_EXCEEDS_REVENUE", f"영업이익/매출 비정상: {operating_profit_f}/{revenue_f}")
                return False, q, "OPERATING_PROFIT_EXCEEDS_REVENUE"

    ta = q.get("total_assets")
    tl = q.get("total_liabilities")
    te = q.get("total_equity")

    # 분기 B/S 핵심 불변식 강제
    if ta is not None and tl is not None:
        try:
            ta_f = float(ta)
            tl_f = float(tl)
            if te is None:
                q["total_equity"] = ta_f - tl_f
                _log(conn, "financial_data", q, "warn", "FILL_EQUITY_FROM_A_L", "total_equity 누락 보정")
            else:
                te_f = float(te)
                diff = ta_f - tl_f - te_f
                tol = max(abs(ta_f) * 0.01, 5e8)
                if abs(diff) > tol:
                    scale = max(abs(ta_f), 1.0)
                    liabilities_match_assets = abs(tl_f - ta_f) / scale < 0.03
                    equity_match_assets = abs(te_f - ta_f) / scale < 0.03
                    if liabilities_match_assets and not equity_match_assets and abs(te_f) > scale * 0.03:
                        q["total_liabilities"] = ta_f - te_f
                        _log(conn, "financial_data", q, "warn", "FIX_LIABILITY_TOTAL_MATCH", f"부채총계가 자산총계로 오매칭 diff={diff}")
                    elif equity_match_assets and not liabilities_match_assets and abs(tl_f) > scale * 0.03:
                        q["total_equity"] = ta_f - tl_f
                        _log(conn, "financial_data", q, "warn", "FIX_EQUITY_TOTAL_MATCH", f"자본총계가 자산총계로 오매칭 diff={diff}")
                    else:
                        _log(conn, "financial_data", q, "critical", "BS_IDENTITY_AMBIGUOUS", f"근거 없는 항등식 보정 차단 diff={diff}")
                        return False, q, "BS_IDENTITY_AMBIGUOUS"
        except Exception:
            _log(conn, "financial_data", q, "critical", "CAST_ERROR", "B/S 숫자 캐스팅 실패")
            return False, q, "CAST_ERROR"

    return True, q, "OK"


def gate_cashflow_row(conn: sqlite3.Connection, p: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    q = dict(p)
    quarter = int(q.get("quarter") or 0)

    # Q1 누적=분기순값 강제 보정
    if quarter == 1:
        m = [
            ("operating_cf", "operating_cf_q"),
            ("investing_cf", "investing_cf_q"),
            ("financing_cf", "financing_cf_q"),
            ("capex", "capex_q"),
        ]
        for src, dst in m:
            if q.get(src) is not None and q.get(dst) is None:
                q[dst] = q.get(src)
                _log(conn, "cash_flow_data", q, "info", "FILL_Q1_Q_FIELD", f"{dst} 보정")

    return True, q, "OK"


def upsert_canonical_financial(conn: sqlite3.Connection, p: Dict[str, Any], source_row_id: int | None = None,
                               decision_reason: str = "write_gate", quality_score: float | None = None) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO canonical_financial_data
        (stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity,
         capital_stock,eps,bps,dps,roe,data_source,source_row_id,rule_version,decision_reason,quality_score,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code,year,quarter,is_annual,report_type) DO UPDATE SET
          revenue=excluded.revenue,
          operating_profit=excluded.operating_profit,
          net_income=excluded.net_income,
          total_assets=excluded.total_assets,
          total_liabilities=excluded.total_liabilities,
          total_equity=excluded.total_equity,
          capital_stock=excluded.capital_stock,
          eps=excluded.eps,
          bps=excluded.bps,
          dps=excluded.dps,
          roe=excluded.roe,
          data_source=excluded.data_source,
          source_row_id=excluded.source_row_id,
          rule_version=excluded.rule_version,
          decision_reason=excluded.decision_reason,
          quality_score=excluded.quality_score,
          updated_at=excluded.updated_at
        """,
        (
            p.get("stock_code"), p.get("year"), p.get("quarter"), 1 if p.get("is_annual") else 0, p.get("report_type") or "CFS",
            p.get("revenue"), p.get("operating_profit"), p.get("net_income"), p.get("total_assets"), p.get("total_liabilities"), p.get("total_equity"),
            p.get("capital_stock"), p.get("eps"), p.get("bps"), p.get("dps"), p.get("roe"), p.get("data_source"), source_row_id,
            "wg_v1", decision_reason, quality_score, ts,
        ),
    )


def upsert_canonical_cashflow(conn: sqlite3.Connection, p: Dict[str, Any], source_row_id: int | None = None,
                              decision_reason: str = "write_gate", quality_score: float | None = None) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO canonical_cashflow_data
        (stock_code,year,quarter,is_annual,report_type,operating_cf,investing_cf,financing_cf,capex,cash_end,depreciation,
         operating_cf_q,investing_cf_q,financing_cf_q,capex_q,value_type,data_source,source_row_id,rule_version,decision_reason,quality_score,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code,year,quarter,is_annual,report_type) DO UPDATE SET
          operating_cf=excluded.operating_cf,
          investing_cf=excluded.investing_cf,
          financing_cf=excluded.financing_cf,
          capex=excluded.capex,
          cash_end=excluded.cash_end,
          depreciation=excluded.depreciation,
          operating_cf_q=excluded.operating_cf_q,
          investing_cf_q=excluded.investing_cf_q,
          financing_cf_q=excluded.financing_cf_q,
          capex_q=excluded.capex_q,
          value_type=excluded.value_type,
          data_source=excluded.data_source,
          source_row_id=excluded.source_row_id,
          rule_version=excluded.rule_version,
          decision_reason=excluded.decision_reason,
          quality_score=excluded.quality_score,
          updated_at=excluded.updated_at
        """,
        (
            p.get("stock_code"), p.get("year"), p.get("quarter"), 1 if p.get("is_annual") else 0, p.get("report_type") or "CFS",
            p.get("operating_cf"), p.get("investing_cf"), p.get("financing_cf"), p.get("capex"), p.get("cash_end"), p.get("depreciation"),
            p.get("operating_cf_q"), p.get("investing_cf_q"), p.get("financing_cf_q"), p.get("capex_q"), p.get("value_type"),
            p.get("data_source"), source_row_id, "wg_v1", decision_reason, quality_score, ts,
        ),
    )
