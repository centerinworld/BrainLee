#!/usr/bin/env python3
"""P1 fail-closed canonical read gate for the live signal/screener path.

Planner P1 (2026-09-20): replace raw ``price_history``/``financial_data`` reads
in the live strategy engines with fail-closed reads through the canonical/
quarantine layer, so that the price-jump / mixed-basis / invalid-OHLCV /
financial-anomaly quarantines documented in ``hermes.md`` actually block the
live signal path instead of being advisory.

Contract (``docs/codex_handoff_p1_read_gate_spec_20260920.md``):

* ``read_prices_failclosed`` — reads ONLY ``return_usable=1`` rows from
  ``canonical_price_history_v``.  A quarantined/invalid row is never returned,
  and its reason + count are returned in ``PriceGateResult.excluded`` so the
  caller records the exclusion (never a silent drop).
* ``read_financials_failclosed`` — reads ONLY ``canonical_financial_data`` (the
  write-gate-approved store).  A raw ``financial_data`` row that failed the gate
  (e.g. BS identity A != L + E) is structurally absent.

This module is READ-ONLY: it issues no INSERT / UPDATE / DELETE / DDL, and it
never opens a raw SQLite connection of its own — the caller supplies ``conn``
(production passes ``connect_primary_db()``; tests pass ``:memory:``).
"""

from __future__ import annotations

from dataclasses import dataclass

# Column allowlists (prevents SQL injection; keep these in sync with the
# canonical views/tables in price_integrity.py / data_write_gate.py).
_PRICE_COLUMNS = frozenset({
    "date", "open", "high", "low", "close", "volume",
    "inst_net_buy", "frn_net_buy", "ind_net_buy",
    "inst_net_buy_amt", "frn_net_buy_amt", "ind_net_buy_amt",
})
_FIN_COLUMNS = frozenset({
    "revenue", "operating_profit", "net_income",
    "total_assets", "total_liabilities", "total_equity",
    "capital_stock", "eps", "bps", "dps", "roe",
    "data_source", "source_row_id", "rule_version", "decision_reason",
    "quality_score", "updated_at",
})


@dataclass
class PriceGateResult:
    rows: list                 # usable rows only (return_usable=1), date-ordered
    total_in_window: int       # raw rows in the window (usable + excluded)
    usable: int                # len(rows)
    excluded: dict             # canonical_quality reason -> count
    excluded_rows: list        # [(date, reason), ...] audit trail (bounded)


def _validate_fields(fields, allowed: frozenset, what: str) -> list:
    bad = [f for f in fields if f not in allowed]
    if bad:
        raise ValueError(f"unsafe/unknown {what} field(s): {bad}")
    return list(fields)


def read_prices_failclosed(conn, stock_code: str, start: str, end: str, *,
                           fields=("date", "open", "high", "low", "close", "volume"),
                           allow_suspended: bool = False) -> PriceGateResult:
    """Read usable daily prices for one stock, fail-closed on the canonical view.

    Quarantined/invalid candles (``return_usable=0``) are never returned; their
    ``canonical_quality`` reason + count are returned for the caller to log.
    """
    cols = _validate_fields(fields, _PRICE_COLUMNS, "price")
    col_sql = ", ".join(cols)
    window = ("stock_code = ? AND substr(date,1,10) >= ? AND substr(date,1,10) <= ?")
    base = (stock_code, start, end)

    rows = conn.execute(
        f"SELECT {col_sql} FROM canonical_price_history_v "
        f"WHERE {window} AND return_usable = 1 ORDER BY date",
        base,
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) FROM canonical_price_history_v WHERE {window}",
        base,
    ).fetchone()[0]

    excluded: dict = {}
    for quality, count in conn.execute(
        f"SELECT canonical_quality, COUNT(*) FROM canonical_price_history_v "
        f"WHERE {window} AND return_usable = 0 GROUP BY canonical_quality",
        base,
    ).fetchall():
        quality = quality or "unknown"
        if allow_suspended and quality == "suspended":
            continue
        excluded[quality] = count

    excluded_rows: list = []
    if excluded:
        for day, quality in conn.execute(
            f"SELECT substr(date,1,10), canonical_quality FROM canonical_price_history_v "
            f"WHERE {window} AND return_usable = 0 ORDER BY date LIMIT 50",
            base,
        ).fetchall():
            excluded_rows.append((day, quality or "unknown"))

    return PriceGateResult(rows=rows, total_in_window=total, usable=len(rows),
                           excluded=excluded, excluded_rows=excluded_rows)


def read_financials_failclosed(conn, stock_code: str, *, year=None, quarter=None,
                               is_annual: bool = False, report_type: str = "CFS",
                               fields=("revenue", "operating_profit", "net_income",
                                       "total_assets", "total_equity"),
                               as_of: str | None = None) -> list:
    """Read canonical (write-gate-approved) financial facts only.

    ``canonical_financial_data`` is populated by ``gate_financial_row``; a raw
    ``financial_data`` row that failed the gate is structurally absent here.
    ``as_of`` applies a point-in-time availability filter (``updated_at <= as_of``).
    """
    cols = _validate_fields(fields, _FIN_COLUMNS, "financial")
    col_sql = ", ".join(cols)
    sql = (f"SELECT {col_sql} FROM canonical_financial_data "
           f"WHERE stock_code = ? AND is_annual = ? AND report_type = ?")
    params: list = [stock_code, 1 if is_annual else 0, report_type]
    if year is not None:
        sql += " AND year = ?"
        params.append(year)
    if quarter is not None:
        sql += " AND quarter = ?"
        params.append(quarter)
    if as_of is not None:
        sql += " AND updated_at <= ?"
        params.append(as_of)
    sql += " ORDER BY year DESC, quarter DESC"
    return conn.execute(sql, params).fetchall()
