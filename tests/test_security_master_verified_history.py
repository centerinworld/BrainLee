import sqlite3

from security_master import (
    apply_verified_security_history_overrides,
    ensure_schema,
)


def test_verified_konex_intervals_replace_only_matching_synthetic_rows():
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    conn.execute(
        """INSERT INTO security_master_history
           (stock_code,effective_from,effective_to,stock_name,market,security_type,
            is_etf_etn,is_tradable,interval_quality,source,source_note)
           VALUES ('126340','2015-01-02','2020-09-23','비나텍','OTHER','주권',0,0,
                   'pre_official_equity_reference_ineligible','generated','')"""
    )
    conn.execute(
        """INSERT INTO security_master_history
           (stock_code,effective_from,effective_to,stock_name,market,security_type,
            is_etf_etn,is_tradable,interval_quality,source,source_note)
           VALUES ('162300','2020-01-30','2022-12-23','신스틸','OTHER','주권',0,0,
                   'pre_official_equity_reference_ineligible','generated','')"""
    )

    assert apply_verified_security_history_overrides(conn) == 2
    assert apply_verified_security_history_overrides(conn) == 2

    konex = conn.execute(
        """SELECT effective_from,effective_to,market,is_tradable,interval_quality
           FROM security_master_history WHERE stock_code='126340'"""
    ).fetchall()
    assert konex == [
        ("2013-07-01", "2020-09-23", "KONEX", 1, "official_disclosure_verified")
    ]
    spac = conn.execute(
        """SELECT is_tradable,interval_quality FROM security_master_history
           WHERE stock_code='162300'"""
    ).fetchone()
    assert spac == (0, "pre_official_equity_reference_ineligible")
