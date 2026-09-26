"""Canonical as-of security eligibility and historical share-count resolver."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from db_compat import connect_primary_db

DB_PATH = Path(__file__).resolve().parent / "stock.db"

_KR_CODE_RE = __import__("re").compile(r"^[0-9A-Z]{6}$")

# KRX KOSPI/KOSDAQ daily reference snapshots do not contain KONEX history.
# A blanket "pre-reference = ineligible" rule is therefore correct for SPAC
# predecessor identities, but wrong for verified KONEX-to-KOSDAQ transfers.
# Keep this list evidence-based and deliberately small; a price series alone
# is never enough to admit an interval.
_VERIFIED_KONEX_INTERVALS = (
    {
        "stock_code": "126340",
        "effective_from": "2013-07-01",
        "effective_to": "2020-09-23",
        "stock_name": "비나텍",
        "source_note": "KRX KIND 공시: 2013-07-01 KONEX 공개, 2020-09-23 KOSDAQ 이전상장",
    },
    {
        "stock_code": "107640",
        "effective_from": "2013-12-10",
        "effective_to": "2024-06-24",
        "stock_name": "한중엔시에스",
        "source_note": "KRX KIND 공시: 2013-12-10 KONEX 상장, 2024-06-24 KOSDAQ 이전상장",
    },
)


def is_kr_equity_code(code: object) -> bool:
    """True only for the exact six-char KR security-code shape.

    Accepts preferred shares such as ``00088K`` (letters allowed in the 6-char
    slot) and rejects every macro/overseas symbol that also lives in
    price_history: indices (``^KS11``, ``^GSPC``), futures (``GC=F``),
    currency pairs (``USDKRW=X``), yields (``2YY=F``), and blank/malformed
    values. This is the single shared predicate for keeping index/futures/
    currency codes out of stock-only screens and buy-candidate queries.
    """
    if not code:
        return False
    return bool(_KR_CODE_RE.match(str(code)))


def _kr_code_predicate(conn, column: str = "stock_code") -> str:
    """Return an exact six-character KR security-code predicate per backend.

    Preferred shares such as ``00088K`` require letters, while the local-file
    rebuild path still uses SQLite and cannot execute PostgreSQL's ``~`` regex
    operator.
    """
    if isinstance(conn, sqlite3.Connection):
        atom = "[0-9A-Z]"
        return f"{column} GLOB '{atom * 6}'"
    return f"{column} ~ '^[0-9A-Z]{{6}}$'"


def _iso(value: object) -> str | None:
    text = str(value or "")[:10].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS security_master_history (
            stock_code TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            stock_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            security_type TEXT NOT NULL DEFAULT 'unknown',
            is_etf_etn INTEGER NOT NULL DEFAULT 0,
            is_tradable INTEGER NOT NULL DEFAULT 1,
            interval_quality TEXT NOT NULL,
            source TEXT NOT NULL,
            source_note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, effective_from)
        );
        CREATE INDEX IF NOT EXISTS ix_security_master_asof
          ON security_master_history(stock_code, effective_from, effective_to);

        CREATE TABLE IF NOT EXISTS security_share_history (
            stock_code TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            shares_issued REAL NOT NULL,
            quality TEXT NOT NULL,
            source TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, effective_from)
        );
        CREATE INDEX IF NOT EXISTS ix_security_shares_asof
          ON security_share_history(stock_code, effective_from, effective_to);
        """
    )


def apply_verified_security_history_overrides(conn: sqlite3.Connection) -> int:
    """Apply explicit exchange-history evidence missing from the daily API.

    The operation is idempotent.  It replaces only the synthetic
    pre-official interval for the exact code/transfer boundary and leaves SPAC
    predecessor intervals fail-closed.
    """
    applied = 0
    for row in _VERIFIED_KONEX_INTERVALS:
        conn.execute(
            """DELETE FROM security_master_history
               WHERE stock_code=? AND effective_to=?
                 AND interval_quality='pre_official_equity_reference_ineligible'""",
            (row["stock_code"], row["effective_to"]),
        )
        conn.execute(
            """INSERT INTO security_master_history
               (stock_code,effective_from,effective_to,stock_name,market,security_type,
                is_etf_etn,is_tradable,interval_quality,source,source_note)
               VALUES (?,?,?,?,'KONEX','주권',0,1,
                       'official_disclosure_verified','KRX_KIND_DISCLOSURE',?)
               ON CONFLICT(stock_code,effective_from) DO UPDATE SET
                 effective_to=excluded.effective_to,
                 stock_name=excluded.stock_name,
                 market=excluded.market,
                 security_type=excluded.security_type,
                 is_etf_etn=excluded.is_etf_etn,
                 is_tradable=excluded.is_tradable,
                 interval_quality=excluded.interval_quality,
                 source=excluded.source,
                 source_note=excluded.source_note,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                row["stock_code"], row["effective_from"], row["effective_to"],
                row["stock_name"], row["source_note"],
            ),
        )
        applied += 1
    return applied


def _name_map(conn: sqlite3.Connection) -> dict[str, str]:
    names = {r[0]: r[1] or "" for r in conn.execute(
        "SELECT stock_code, stock_name FROM stock_universe"
    )}
    code_filter = _kr_code_predicate(conn)
    for code, name in conn.execute(
        f"""
        SELECT stock_code, MAX(COALESCE(stock_name,''))
        FROM stock_price_daily
        WHERE {code_filter}
        GROUP BY stock_code
        """
    ):
        if name:
            names.setdefault(code, name)
    for code, name in conn.execute(
        f"""
        SELECT stock_code, MAX(COALESCE(corp_name,''))
        FROM dart_disclosures
        WHERE {code_filter}
        GROUP BY stock_code
        """
    ):
        if name:
            names.setdefault(code, name)
    return names


def _security_type(name: str, current_type: str = "") -> tuple[str, int]:
    value = f"{name} {current_type}".upper()
    if "ETN" in value:
        return "ETN", 1
    if "ETF" in value:
        return "ETF", 1
    if "스팩" in value or "SPAC" in value:
        return "SPAC", 0
    if "리츠" in value or "REIT" in value:
        return "REIT", 0
    if "우" in name[-3:]:
        return "preferred", 0
    return current_type or "common_or_unknown", 0


def rebuild_security_master(db_path: Path | str = DB_PATH) -> dict:
    conn = connect_primary_db(timeout=60) if db_path == DB_PATH else sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    names = _name_map(conn)
    code_filter = _kr_code_predicate(conn)
    current = {r["stock_code"]: r for r in conn.execute(
        f"""
        SELECT stock_code, stock_name, market, stock_type, secugrp_nm,
               listed_date, shares_issued
        FROM stock_universe
        WHERE {code_filter}
        """
    )}
    observed = conn.execute(
        f"""
        SELECT stock_code, MIN(substr(date,1,10)) first_seen,
               MAX(substr(date,1,10)) last_seen, COUNT(*) observations
        FROM price_history
        WHERE {code_filter} AND close > 0
        GROUP BY stock_code
        """
    ).fetchall()

    has_reference = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='krx_security_reference'"
    ).fetchone())
    references: dict[str, list[sqlite3.Row]] = {}
    etn_prefixes: set[str] = set()
    if has_reference:
        for row in conn.execute(
            """SELECT * FROM krx_security_reference
               ORDER BY stock_code,effective_from,effective_to"""
        ):
            references.setdefault(row["stock_code"], []).append(row)
            if row["security_type"] == "ETN":
                etn_prefixes.add(row["stock_code"][:3])

    conn.execute("DELETE FROM security_master_history")
    for row in observed:
        code = row["stock_code"]
        code_refs = references.get(code, [])
        if code_refs:
            cur = current.get(code)
            # Current KRX reference rows can start at a market-transfer or
            # reclassification date. Preserve an observed pre-reference
            # interval instead of silently erasing the stock's earlier life.
            first_ref_start = min(ref["effective_from"] for ref in code_refs)
            first_seen = _iso(row["first_seen"])
            if first_seen and first_seen < first_ref_start:
                historical_market_row = conn.execute(
                    """SELECT market FROM stock_price_daily
                       WHERE stock_code=? AND bas_dt<? AND market IN ('KOSPI','KOSDAQ')
                       ORDER BY bas_dt DESC LIMIT 1""",
                    (code, first_ref_start.replace("-", "")),
                ).fetchone()
                name = (cur["stock_name"] if cur else "") or names.get(code, "")
                raw_type = ((cur["secugrp_nm"] or cur["stock_type"] or "") if cur else "")
                sec_type, is_etf = _security_type(name, raw_type)
                # With complete KRX KOSPI/KOSDAQ daily reference coverage, an
                # observed price interval absent from those endpoints before
                # the first official reference is not an eligible exchange
                # listing. These are overwhelmingly KONEX-to-KOSDAQ transfers
                # (e.g. 058970, 199800), which the old current-market fallback
                # mislabeled as historical KOSDAQ and admitted into backtests.
                historical_market = historical_market_row[0] if historical_market_row else "OTHER"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO security_master_history
                    (stock_code,effective_from,effective_to,stock_name,market,security_type,
                     is_etf_etn,is_tradable,interval_quality,source,source_note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        code, first_seen, first_ref_start, name, historical_market,
                        sec_type, is_etf, 0,
                        "pre_official_equity_reference_ineligible",
                        "price_history+KRX_OPEN_API_DAILY_HISTORY",
                        "가격은 관측됐지만 KOSPI/KOSDAQ 일별 종목기본정보에 없던 구간; "
                        "KONEX 등 비대상 시장으로 진입 제외",
                    ),
                )
            # Exact/labelled KRX reference intervals supersede price-observation
            # inference. A code may have multiple intervals after relisting.
            for ref in code_refs:
                start = ref["effective_from"]
                end = ref["effective_to"] or None
                if end and end <= row["first_seen"]:
                    # Keep exact delisted intervals even when the local OHLCV
                    # sample begins later only if the two ranges overlap.
                    continue
                if start > row["last_seen"] and not end:
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO security_master_history
                    (stock_code,effective_from,effective_to,stock_name,market,security_type,
                     is_etf_etn,is_tradable,interval_quality,source,source_note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        code, start, end, ref["stock_name"] or names.get(code, ""),
                        ref["market"] or "", ref["security_type"], ref["is_etf_etn"],
                        int(bool(ref["is_equity"]) and not bool(ref["is_etf_etn"])),
                        ref["quality"], ref["source"], ref["source_note"],
                    ),
                )
            continue
        cur = current.get(code)
        listed = _iso(cur["listed_date"]) if cur else None
        start = listed or _iso(row["first_seen"])
        if not start:
            continue
        end = None if cur else _next_day(_iso(row["last_seen"]))
        name = (cur["stock_name"] if cur else names.get(code, "")) or ""
        raw_type = ((cur["secugrp_nm"] or cur["stock_type"] or "") if cur else "")
        sec_type, is_etf = _security_type(name, raw_type)
        # KRX reserves issuer-prefix ranges for ETNs. This fallback only applies
        # where the exact product list has no historical row and remains approx.
        if not is_etf and code[:3] in etn_prefixes:
            sec_type, is_etf = "ETN", 1
        quality = "official_current" if cur and listed else (
            "product_code_pattern_approx" if is_etf else "observed_price_interval_approx"
        )
        conn.execute(
            """
            INSERT INTO security_master_history
            (stock_code,effective_from,effective_to,stock_name,market,security_type,
             is_etf_etn,is_tradable,interval_quality,source,source_note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                code, start, end, name, (cur["market"] if cur else "") or "",
                sec_type, is_etf, 0 if is_etf else 1, quality,
                "stock_universe+price_history",
                "현재 종목은 공식 상장일, 비현재 종목은 가격 관측 첫날~마지막날+1 근사",
            ),
        )

    apply_verified_security_history_overrides(conn)

    conn.execute("DELETE FROM security_share_history")
    masters = conn.execute(
        """SELECT stock_code,effective_from,effective_to
           FROM security_master_history WHERE is_tradable=1
           ORDER BY stock_code,effective_from"""
    ).fetchall()
    for master in masters:
        code = master["stock_code"]
        changes = conn.execute(
            """
            SELECT change_date, old_value, new_value, source, confidence
            FROM stock_base_info_changes
            WHERE stock_code=? AND change_type='shares_issued'
            ORDER BY change_date
            """,
            (code,),
        ).fetchall()
        points: list[tuple[str, float, str, float, str, int]] = []
        # Daily public-data snapshots contain the exact shares observed on each
        # date from 2020 onward. Keep only change points.
        daily = conn.execute(
            """SELECT bas_dt,shares FROM stock_price_daily
               WHERE stock_code=? AND shares>0 ORDER BY bas_dt""",
            (code,),
        ).fetchall()
        previous_shares = None
        for snap in daily:
            shares = float(snap["shares"] or 0)
            effective = _iso(snap["bas_dt"])
            if effective and shares > 0 and shares != previous_shares:
                points.append((effective, shares, "stock_price_daily", 0.95,
                               "official_daily_observed", 4))
                previous_shares = shares
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='krx_security_share_snapshot'"
        ).fetchone():
            for snap in conn.execute(
                """SELECT snapshot_date,shares_issued,quality,source
                   FROM krx_security_share_snapshot
                   WHERE stock_code=? AND shares_issued>0 ORDER BY snapshot_date""",
                (code,),
            ):
                points.append((snap["snapshot_date"], float(snap["shares_issued"]),
                               snap["source"], 0.7, snap["quality"], 3))
        if changes:
            try:
                old = float(changes[0]["old_value"] or 0)
            except (TypeError, ValueError):
                old = 0
            if old > 0:
                points.append((master["effective_from"], old, changes[0]["source"] or "change_old",
                               float(changes[0]["confidence"] or 0), "asof_change_observed", 2))
            for change in changes:
                try:
                    new = float(change["new_value"] or 0)
                except (TypeError, ValueError):
                    continue
                changed_at = _iso(change["change_date"])
                if new > 0 and changed_at:
                    points.append((max(changed_at, master["effective_from"]), new,
                                   change["source"] or "change_new", float(change["confidence"] or 0),
                                   "asof_change_observed", 2))
        elif (not master["effective_to"] and code in current
              and float(current[code]["shares_issued"] or 0) > 0):
            points.append((master["effective_from"], float(current[code]["shares_issued"]),
                           "current_stock_universe_fallback", 0.25,
                           "current_fallback_approx", 1))

        dedup: dict[str, tuple[float, str, float, str, int]] = {}
        for effective, shares, source, confidence, quality, priority in points:
            if effective < master["effective_from"]:
                continue
            if master["effective_to"] and effective >= master["effective_to"]:
                continue
            if effective not in dedup or priority >= dedup[effective][4]:
                dedup[effective] = (shares, source, confidence, quality, priority)
        ordered_raw = sorted(dedup.items())
        ordered: list[tuple[str, tuple[float, str, float, str, int]]] = []
        previous_signature: tuple[float, str] | None = None
        for item in ordered_raw:
            payload = item[1]
            quality_family = "approx" if "approx" in payload[3] else "observed"
            signature = (payload[0], quality_family)
            if signature == previous_signature:
                continue
            ordered.append(item)
            previous_signature = signature
        for idx, (effective, payload) in enumerate(ordered):
            next_effective = ordered[idx + 1][0] if idx + 1 < len(ordered) else master["effective_to"]
            shares, source, confidence, quality, _priority = payload
            conn.execute(
                """
                INSERT INTO security_share_history
                (stock_code,effective_from,effective_to,shares_issued,quality,source,confidence)
                VALUES (?,?,?,?,?,?,?)
                """,
                (code, effective, next_effective, shares, quality, source, confidence),
            )
    conn.commit()
    result = {
        "securities": conn.execute("SELECT COUNT(*) FROM security_master_history").fetchone()[0],
        "historical_only": conn.execute("SELECT COUNT(*) FROM security_master_history WHERE effective_to IS NOT NULL").fetchone()[0],
        "share_intervals": conn.execute("SELECT COUNT(*) FROM security_share_history").fetchone()[0],
        "share_stocks": conn.execute("SELECT COUNT(DISTINCT stock_code) FROM security_share_history").fetchone()[0],
        "approx_intervals": conn.execute("SELECT COUNT(*) FROM security_master_history WHERE interval_quality LIKE '%approx%'").fetchone()[0],
        "excluded_products": conn.execute("SELECT COUNT(*) FROM security_master_history WHERE is_etf_etn=1").fetchone()[0],
        "official_reference_intervals": conn.execute("SELECT COUNT(*) FROM security_master_history WHERE source='KRX_OPEN_API'").fetchone()[0],
    }
    conn.close()
    return result


@dataclass(frozen=True)
class AsOfSecurity:
    stock_code: str
    as_of: str
    eligible: bool
    shares_issued: float | None
    interval_quality: str | None
    shares_quality: str | None
    reason: str


def resolve_security(conn: sqlite3.Connection, stock_code: str, as_of: str) -> AsOfSecurity:
    day = _iso(as_of)
    if not day:
        return AsOfSecurity(stock_code, str(as_of), False, None, None, None, "invalid_date")
    master = conn.execute(
        """
        SELECT * FROM security_master_history
        WHERE stock_code=? AND effective_from<=?
          AND (effective_to IS NULL OR ?<effective_to)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (stock_code, day, day),
    ).fetchone()
    if not master:
        return AsOfSecurity(stock_code, day, False, None, None, None, "outside_listing_interval")
    if master["is_etf_etn"] or not master["is_tradable"]:
        return AsOfSecurity(stock_code, day, False, None, master["interval_quality"], None, "excluded_security_type")
    shares = conn.execute(
        """
        SELECT shares_issued,quality FROM security_share_history
        WHERE stock_code=? AND effective_from<=?
          AND (effective_to IS NULL OR ?<effective_to)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (stock_code, day, day),
    ).fetchone()
    return AsOfSecurity(
        stock_code, day, True,
        float(shares["shares_issued"]) if shares else None,
        master["interval_quality"], shares["quality"] if shares else None,
        "ok" if shares else "eligible_but_shares_unknown",
    )


if __name__ == "__main__":
    print(rebuild_security_master())
