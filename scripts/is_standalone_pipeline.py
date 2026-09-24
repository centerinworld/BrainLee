#!/usr/bin/env python3
"""Income-statement 당분기(standalone) derivation pipeline.

PostgreSQL-only / connection-agnostic implementation of the contract in
`docs/codex_handoff_is_standalone_impl_spec_20260920.md`.

* ``derive_standalone`` is a **pure function** (no DB, no I/O): it extracts a
  single quarter's standalone (3-month) income-statement value from a DART
  report XML, carrying full provenance (which ACODE/ADECIMAL/period/suffix was
  chosen).  It never invents a value when the source evidence is absent —
  it returns ``SKIP`` with a reason instead.
* ``record_standalone`` enforces the raw/computed/evidence 3-way split and is
  **idempotent**: the computed value is written only into a NULL slot (never
  overwrites an existing value), and the provenance row is de-duplicated.

This module never opens a raw SQLite connection of its own.  The caller
supplies the connection — production passes ``connect_primary_db()``, which
routes to PostgreSQL; the focused test passes an in-memory connection.  All
SQL is written in the SQLite-compatible dialect (``?`` placeholders,
``is_annual IS FALSE``) that ``db_compat.translate_sqlite_sql`` already maps
to PostgreSQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# --- design constants --------------------------------------------------------
# (from docs/codex_handoff_is_quarterly_standalone_design_20260920.md §2)

# ACODE priority per field.  operating_profit uses the DART extension code
# first — the legacy ifrs-full_OperatingIncomeLoss matches 0 rows for issuers
# such as 삼성전자 (documented mapping bug).
_FIELD_CODES = {
    "revenue": ("ifrs-full_Revenue", "ifrs-full_RevenueFromContractsWithCustomers"),
    "operating_profit": ("dart_OperatingIncomeLoss", "ifrs-full_OperatingIncomeLoss"),
    "net_income": ("ifrs-full_ProfitLoss",),
}

# Quarter -> DART period token (design §1-1).
# Q1=FQ, Q2=HY, Q3=TQ are 실측; Q4="FY" was UNMEASURED until 2026-09-20, when a
# live 사업보고서 (삼성전자 005930, rcept_no=20260310002820) confirmed the token is
# "FY".  However, the Q4 사업보고서 income facts carry NO Q/A suffix — the
# ACONTEXT is `CFY2025dFY_...` (annual cumulative), and prior-year comparison
# uses `BPFY{year}` (not `PFY`).  Because `derive_standalone` requires a Q/A
# suffix, a Q4 call finds no matching fact and correctly returns SKIP(no_fact)
# rather than mis-deriving a standalone quarter.  Q4 standalone would require a
# separate "FY annual − Q3 cumulative" derivation (deferred, out of P0 scope).
_PERIOD_TOKEN = {1: "FQ", 2: "HY", 3: "TQ", 4: "FY"}

# Fields that may be interpolated into a financial_data write (SQL identifier
# allowlist — prevents injection, keeps writes to the income-statement facts).
_WRITABLE_FIELDS = frozenset(_FIELD_CODES)

_TE_RE = re.compile(r"<TE(?P<attrs>[^>]*)>(?P<value>.*?)</TE>", re.DOTALL)


@dataclass
class Derivation:
    mode: str                       # 'DIRECT' | 'SUBTRACT' | 'SKIP'
    raw_value: float | None         # 원문: DIRECT=당분기 fact / SUBTRACT=현재 누적 fact
    prior_raw_value: float | None   # 원문: SUBTRACT의 직전 누적 fact (그 외 None)
    computed_value: float | None    # 계산값: 최종 당분기 (financial_data에 기입될 유일한 값)
    acode: str | None               # 근거: 채택된 ACODE
    adecimal: int | None            # 근거: 스케일 (ADECIMAL)
    consolidated: bool              # 근거: 연결 여부
    period_token: str | None        # 근거: FQ/HY/TQ/FY
    suffix: str | None              # 근거: Q(당분기)/A(누적)
    skip_reason: str | None         # SKIP 사유


def _number(value: str) -> float | None:
    """Parse a DART numeric literal: commas and (parenthesis)-negation."""
    s = value.replace(",", "").strip()
    if not s:
        return None
    try:
        if s.startswith("(") and s.endswith(")"):
            return -float(s.strip("()"))
        return float(s)
    except ValueError:
        return None


def _decode_context(acontext: str) -> dict:
    """Parse ACONTEXT -> {fy, year, period, suffix, member, has_extra_axis}.

    Total-amount facts end exactly at ``...ConsolidatedMember`` /
    ``...SeparateMember``; any trailing axis (e.g. SegmentsAxis) marks a
    breakdown fact that must be excluded.
    """
    m = re.match(r"^(CFY|PFY)(\d{4})d(FQ|HY|TQ|FY)(Q|A)_(.+)$", acontext)
    out = {"fy": None, "year": None, "period": None, "suffix": None,
           "member": None, "has_extra_axis": False}
    if not m:
        return out
    out["fy"] = m.group(1)
    out["year"] = int(m.group(2))
    out["period"] = m.group(3)
    out["suffix"] = m.group(4)
    rest = m.group(5)
    if "_ifrs-full_ConsolidatedMember" in rest:
        out["member"] = "consolidated"
    elif "_ifrs-full_SeparateMember" in rest:
        out["member"] = "separate"
    if out["member"] is not None:
        axis_marker = ("_ifrs-full_ConsolidatedMember"
                       if out["member"] == "consolidated"
                       else "_ifrs-full_SeparateMember")
        tail = rest.split(axis_marker, 1)[1]
        if tail:  # e.g. "_ifrs-full_SegmentsAxis_..."
            out["has_extra_axis"] = True
    return out


def _collect_facts(xml: str, field: str):
    """Yield (fy, year, period, suffix, member, extra, dec, value, acode)."""
    codes = _FIELD_CODES[field]
    for m in _TE_RE.finditer(xml):
        attrs = m.group("attrs")
        acode = re.search(r'ACODE="([^"]*)"', attrs)
        if not acode or acode.group(1) not in codes:
            continue
        acontext = re.search(r'ACONTEXT="([^"]*)"', attrs)
        adecimal = re.search(r'ADECIMAL="([^"]*)"', attrs)
        raw_text = re.sub(r"<[^>]+>", "", m.group("value"))
        value = _number(raw_text)
        if value is None or not acontext:
            continue
        dec = int(adecimal.group(1)) if adecimal else 0
        ctx = _decode_context(acontext.group(1))
        yield (ctx["fy"], ctx["year"], ctx["period"], ctx["suffix"],
               ctx["member"], ctx["has_extra_axis"], dec, value, acode.group(1))


def derive_standalone(xml_text: str, field: str, year: int, quarter: int,
                      prior_cumulative: float | None = None) -> Derivation:
    """XML 원문 → 당분기 파생값 (pure function, DB 무접근).

    Priority (spec §3):
      1. DIRECT   — 당분기 fact(``...d{PERIOD}Q``, consolidated, total).
      2. Q1 특례  — 누적==당분기, 차감 없이 DIRECT 기입.
      3. SUBTRACT — 당분기 = 누적(Q_n) − 누적(Q_{n-1})  (prior_cumulative 필요).
      4. SKIP     — 증거 부족 시 산출 거부 (no-guess).
    """
    if field not in _FIELD_CODES:
        raise ValueError(f"unknown field: {field!r}")
    if quarter not in _PERIOD_TOKEN:
        raise ValueError(f"unsupported quarter: {quarter!r}")
    token = _PERIOD_TOKEN[quarter]

    direct = None        # (acode, dec, scaled)
    cumulative = None    # (acode, dec, scaled)

    for fy, fy_year, period, suffix, member, extra, dec, value, acode in _collect_facts(xml_text, field):
        if fy != "CFY" or fy_year != year:
            continue
        if period != token:
            continue
        if member != "consolidated" or extra:
            continue
        scaled = value * (10 ** (-dec))
        if suffix == "Q" and direct is None:
            direct = (acode, dec, scaled)
        elif suffix == "A" and cumulative is None:
            cumulative = (acode, dec, scaled)

    # 1순위: 직접 당분기 fact.
    if direct is not None:
        acode, dec, scaled = direct
        if field == "revenue" and scaled < 0:
            return Derivation("SKIP", scaled, None, None, acode, dec, True,
                              token, "Q", "negative_revenue")
        return Derivation("DIRECT", scaled, None, scaled, acode, dec, True,
                          token, "Q", None)

    # Q1(최초분기): 누적==당분기 → 차감 없이 직접 기입.
    if cumulative is not None and quarter == 1:
        acode, dec, scaled = cumulative
        if field == "revenue" and scaled < 0:
            return Derivation("SKIP", scaled, None, None, acode, dec, True,
                              token, "A", "negative_revenue")
        return Derivation("DIRECT", scaled, None, scaled, acode, dec, True,
                          token, "A", None)

    # 2순위: 누적 차감 폴백.
    if cumulative is not None:
        acode, dec, scaled = cumulative
        if prior_cumulative is None:
            return Derivation("SKIP", scaled, None, None, acode, dec, True,
                              token, "A", "missing_prior_cumulative")
        return Derivation("SUBTRACT", scaled, prior_cumulative,
                          scaled - prior_cumulative, acode, dec, True,
                          token, "A", None)

    # 증거 없음 → 산출 거부.
    return Derivation("SKIP", None, None, None, None, None, True,
                      token, None, "no_fact")


def _record_evidence(conn, stock_code, year, quarter, field, deriv,
                     report_type, rcept_no, run_id, created_at) -> None:
    """Insert one provenance row, de-duplicated (idempotent)."""
    guard = (
        "SELECT 1 FROM is_standalone_derivation "
        "WHERE stock_code = ? AND year = ? AND quarter = ? "
        "AND field = ? AND run_id = ? AND mode = ? LIMIT 1"
    )
    existing = conn.execute(
        guard, (stock_code, year, quarter, field, run_id, deriv.mode)
    ).fetchone()
    if existing is not None:
        return

    conn.execute(
        "INSERT INTO is_standalone_derivation "
        "(stock_code, year, quarter, field, mode, raw_value, prior_raw_value, "
        "computed_value, acode, adecimal, consolidated, period_token, suffix, "
        "skip_reason, rcept_no, run_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (stock_code, year, quarter, field, deriv.mode, deriv.raw_value,
         deriv.prior_raw_value, deriv.computed_value, deriv.acode,
         deriv.adecimal, 1 if deriv.consolidated else 0, deriv.period_token,
         deriv.suffix, deriv.skip_reason, rcept_no, run_id, created_at),
    )


def _record_fix_log(conn, row_id, stock_code, year, quarter, field, deriv,
                    report_type, rcept_no, run_id, created_at) -> None:
    """Append one ``financial_fix_log`` entry for a value actually written.

    ``old_value`` is always NULL because the write target is guarded by
    ``{field} IS NULL`` (the slot was empty before the fill). The ``source``
    string carries the full DART provenance (rcept_no / ACODE / ADECIMAL /
    derivation mode / unit) so a reviewer can re-derive the value from the
    original report XML.
    """
    source = (
        f"OpenDART document.xml rcept_no={rcept_no} "
        f"acode={deriv.acode} adecimal={deriv.adecimal} mode={deriv.mode} "
        f"unit=KRW"
    )
    conn.execute(
        "INSERT INTO financial_fix_log "
        "(fixed_at, row_id, stock_code, year, quarter, is_annual, report_type, "
        "field_name, old_value, new_value, fix_rule, source, run_id) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, "
        "'DART_STANDALONE_IS_DERIVATION', ?, ?)",
        (created_at, row_id, stock_code, year, quarter, report_type, field,
         deriv.computed_value, source, run_id),
    )


def _promote_flag(conn, stock_code, year, quarter, field, deriv,
                  rcept_no, created_at) -> None:
    """Promote the OPEN ``QUARTERLY_4WAY`` flag from 0→1 source (idempotent).

    Guarded by ``source_count = 0`` so a re-run (or a flag already promoted by
    another source) is a structural no-op. ``dart_value`` is recorded so the
    flag stays OPEN (still awaiting an independent source) but with evidence.
    """
    source = (
        f"OpenDART document.xml rcept_no={rcept_no} "
        f"acode={deriv.acode} adecimal={deriv.adecimal} mode={deriv.mode} "
        f"unit=KRW"
    )
    conn.execute(
        "UPDATE fin_quarterly_validation_flags "
        "SET dart_value = ?, source_count = 1, notes = ?, updated_at = ? "
        "WHERE stock_code = ? AND year = ? AND quarter = ? AND field = ? "
        "AND check_type = 'QUARTERLY_4WAY' AND status = 'OPEN' "
        "AND source_count = 0",
        (deriv.computed_value,
         f"DART standalone IS derivation: {source}",
         created_at, stock_code, year, quarter, field),
    )


def record_standalone(conn, stock_code, year, quarter, field,
                      deriv: Derivation, rcept_no: str, run_id: str) -> bool:
    """계산값만 financial_data에, 원문/계산값/근거를 별도 테이블에 분리 저장.

    Returns True only when a computed value was actually written into
    ``financial_data`` (False for ``SKIP``, or when the slot was already filled
    by a prior/confirmed value — an idempotent no-op).

    Idempotency & safety:
      * financial_data UPDATE is guarded by ``{field} IS NULL`` → never
        overwrites an existing (already reconciled) value.
      * financial_fix_log row is written only for an actual change (old=NULL).
      * flag promotion is guarded by ``source_count = 0`` → a re-run is a no-op.
      * provenance INSERT is de-duplicated on (stock_code, year, quarter,
        field, run_id, mode).
      * No commit is issued — the caller owns the transaction (rollback-safe).
    """
    if field not in _WRITABLE_FIELDS:
        raise ValueError(f"unsafe field for financial_data write: {field!r}")
    if deriv is None:
        raise ValueError("deriv must not be None")

    report_type = "CFS" if deriv.consolidated else "OFS"
    created_at = datetime.now().isoformat(timespec="seconds")

    wrote = False
    if deriv.mode in ("DIRECT", "SUBTRACT") and deriv.computed_value is not None:
        target = conn.execute(
            f"SELECT id FROM financial_data "
            f"WHERE stock_code = ? AND year = ? AND quarter = ? "
            f"AND report_type = ? AND is_annual IS FALSE AND {field} IS NULL "
            f"LIMIT 1",
            (stock_code, year, quarter, report_type),
        ).fetchone()
        if target is not None:
            row_id = target[0]
            cur = conn.execute(
                f"UPDATE financial_data SET {field} = ? "
                f"WHERE id = ? AND {field} IS NULL",
                (deriv.computed_value, row_id),
            )
            if cur.rowcount and cur.rowcount > 0:
                wrote = True
                _record_fix_log(conn, row_id, stock_code, year, quarter,
                                field, deriv, report_type, rcept_no, run_id,
                                created_at)
                _promote_flag(conn, stock_code, year, quarter, field, deriv,
                              rcept_no, created_at)

    _record_evidence(conn, stock_code, year, quarter, field, deriv,
                     report_type, rcept_no, run_id, created_at)
    return wrote
