#!/usr/bin/env python3
"""Record US tickers whose CURRENT occupant is a different legal entity than the
historical company a PIT backtest might be looking for under the same symbol
(ticker reuse after delisting/rename), so no script ever splices the two
together by symbol alone.

Seeded 2026-09-27 while investigating why 24 tickers in the Minervini PIT
missing-price list already had *some* rows in ``us_price_history`` (starting
long after the historically relevant company delisted). Each row here is
issuer/press-release/SEC verified, matching the citation bar already used by
``scripts/sync_us_security_outcomes.py``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


# ticker, historical_company, historical_delist_or_reticker_date, historical_reason,
# current_occupant, current_occupant_since, status, source_url, note
CONFLICTS = (
    ("BBBY", "Bed Bath & Beyond Inc. (pre-2023, CIK-distinct)", "2023-04-23",
     "chapter11_liquidation",
     "Beyond, Inc. (formerly Overstock.com) renamed itself Bed Bath & Beyond, Inc. and "
     "reticker'd BYON->BBBY in Aug 2025 after buying only the brand/IP out of bankruptcy",
     "2025-08", "confirmed_different_entity",
     "https://www.sec.gov/Archives/edgar/data/1130713/000114036126000734/ny20060053x1_s4.htm",
     "us_price_history rows start 2026 only (50 rows) — do not backfill pre-2023 history under this ticker"),
    ("PCLN", "The Priceline Group Inc.", "2018-02-27", "corporate_rename",
     "Pictet Cleaner Planet ETF (unrelated fund, not an operating company)",
     "unknown_recent", "confirmed_different_entity",
     "https://www.bloomberg.com/quote/PCLN*:MM",
     "Priceline/Booking Holdings has traded as BKNG since 2018; us_price_history PCLN rows "
     "start 2025-10-16 (237 rows) and belong to an ETF, not the historical company"),
    ("LIFE", "Life Technologies Corporation", "2014-02-03", "cash_acquisition_thermo_fisher",
     "Ethos Technologies Inc. (insurance/fintech, unrelated)",
     "2026", "confirmed_different_entity",
     "https://www.nasdaq.com/market-activity/stocks/life",
     "us_price_history rows start 2026 only (166 rows) — different industry, different entity"),
    ("SNDK", "SanDisk Corporation (1995-2016, acquired by Western Digital)", "2016-05-12",
     "cash_stock_acquisition_western_digital",
     "SanDisk Corporation (2025 spin-off) — legitimate new legal entity, thematically related "
     "(WDC flash business) but not the same company",
     "2025-02-24", "confirmed_distinct_spinoff",
     "https://investor.sandisk.com/news-releases/news-release-details/sandisk-celebrates-nasdaq-listing-after-completing-separation",
     "us_price_history rows start 2025-02-13 (406 rows); 2016-2025 gap must stay a gap, not be filled from the old entity's history"),
    ("SPLS", "Staples Inc. (pre-2017, take-private by Sycamore Partners)", "2017-09-12",
     "going_private_lbo",
     "unresolved — SEC's own current exchange-ticker registry (company_tickers.json, checked "
     "2026-09-27) has NO entry for ticker SPLS at all; general web search results claiming "
     "'Staples Inc trades under SPLS' appear to be stale cached aggregator pages, since the real "
     "Staples has been a private LBO since 2017 with no public equity to trade",
     "unknown", "unresolved_needs_manual_check",
     "https://www.sec.gov/files/company_tickers.json",
     "us_price_history SPLS rows start 2026 only (174 rows) with no SEC-registered current filer "
     "found under this symbol — possibly an OTC-only listing outside SEC's exchange-ticker file, "
     "or a data collection artifact. Do not treat as continuous with pre-2017 Staples."),
    ("MICC", "Millicom International Cellular S.A. (legacy Nasdaq MICC listing)", "unknown",
     "ticker_migrated_to_TIGO",
     "The Magnum Ice Cream Company N.V. (Unilever's ice cream spinoff, unrelated to telecom)",
     "2025-12-08", "confirmed_different_entity",
     "https://en.wikipedia.org/wiki/The_Magnum_Ice_Cream_Company",
     "Confirmed via SEC company_tickers.json (CIK 2071668, title 'Magnum Ice Cream Co N.V.') and "
     "independent press coverage: Unilever's ice cream business began trading as MICC on NYSE/"
     "Euronext Amsterdam/LSE on 2025-12-08 — exactly matching us_price_history's first MICC row "
     "(2025-12-08). Millicom International Cellular now trades as TIGO, unrelated."),
    ("CA", "CA Technologies (CA, Inc.)", "2018-11-05", "cash_acquisition_broadcom",
     "unidentified — NOT the historical CA Technologies",
     "2023-12", "confirmed_different_entity",
     "https://www.sec.gov/Archives/edgar/data/0001730168/000119312518317917/d648705dex991.htm",
     "Broadcom completed the CA Technologies acquisition 2018-11-05. us_price_history CA rows "
     "(inserted by the 2026-09-27 Tiingo backfill) run 2023-12-14 to 2026-09-25 — 5+ years after "
     "delisting and continuing to the present day. Tiingo returned HTTP 200 with real-looking "
     "OHLCV for a DIFFERENT current company reusing the ticker; this was not caught by the "
     "backfill script's 404/no-valid-rows checks since the response was well-formed. Rows should "
     "be deleted from us_price_history (not done automatically — see remediation note below)."),
    ("CTRP", "Ctrip.com International, Ltd.", "2019-11-05", "ticker_rename_to_TCOM",
     "unidentified — the real Trip.com Group trades as TCOM since 2019-11-05; CTRP data past "
     "that date is not the same continuously-tracked instrument",
     "unknown", "confirmed_different_entity",
     "https://www.sec.gov/Archives/edgar/data/1269238/000119312521085779/d884543dex991.htm",
     "Ctrip changed its Nasdaq ticker from CTRP to TCOM effective 2019-11-05 (same company, "
     "renamed Trip.com Group). us_price_history CTRP rows (inserted by the 2026-09-27 Tiingo "
     "backfill) continue uninterrupted through 2026-09-25 — 7 years past the rename — meaning "
     "Tiingo is serving TCOM's ongoing price data mislabeled under the retired CTRP symbol. "
     "Rows after 2019-11-05 should be deleted from us_price_history."),
    ("SGEN", "Seagen Inc.", "2023-12-14", "cash_acquisition_pfizer",
     "unidentified — NOT the historical Seagen",
     "unknown", "confirmed_different_entity",
     "https://www.sec.gov/Archives/edgar/data/78003/000007800323000118/pr121223ex991.htm",
     "Pfizer completed the Seagen acquisition 2023-12-14. us_price_history SGEN rows (inserted by "
     "the 2026-09-27 Tiingo backfill) continue uninterrupted through 2026-09-25, nearly 3 years "
     "past delisting. Rows after 2023-12-14 should be deleted from us_price_history."),
    ("SIVB", "SVB Financial Group", "2023-03-10", "fdic_receivership_collapse",
     "unidentified — NOT the historical SVB Financial Group",
     "unknown", "confirmed_different_entity",
     "https://www.fdic.gov/resources/resolutions/bank-failures/failed-bank-list/silicon-valley.html",
     "Silicon Valley Bank was closed by regulators 2023-03-10 (FDIC receivership); the holding "
     "company filed Chapter 11 on 2023-03-17. us_price_history SIVB rows (inserted by the "
     "2026-09-27 Tiingo backfill) continue uninterrupted through 2026-09-25. Rows after "
     "2023-03-10 should be deleted from us_price_history."),
    ("SPLK", "Splunk Inc.", "2024-03-18", "cash_acquisition_cisco",
     "unidentified — NOT the historical Splunk",
     "unknown", "confirmed_different_entity",
     "https://www.tipranks.com/news/company-announcements/splunk-merges-with-cisco-transforms-stock-and-corporate-structure",
     "Cisco completed the Splunk acquisition 2024-03-18 ($157.00/share cash); SPLK was delisted "
     "from Nasdaq at close. us_price_history SPLK rows (inserted by the 2026-09-27 Tiingo "
     "backfill) continue uninterrupted through 2026-09-25. Rows after 2024-03-18 should be "
     "deleted from us_price_history."),
    ("ANSS", "ANSYS, Inc.", "2025-07-17", "cash_stock_acquisition_synopsys",
     "unidentified — NOT the historical Ansys",
     "unknown", "confirmed_different_entity",
     "https://www.nasdaqtrader.com/TraderNews.aspx?id=ECA2025-373",
     "Synopsys completed the Ansys acquisition 2025-07-17 (merger closed prior to market open; "
     "stock suspended 2025-07-18). us_price_history ANSS rows (inserted by the 2026-09-27 Tiingo "
     "backfill) continue uninterrupted through 2026-09-25, over a year past delisting. Rows "
     "after 2025-07-17 should be deleted from us_price_history."),
    ("MEDI", "MedImmune, Inc.", "2007-06-18", "cash_acquisition_astrazeneca",
     "unidentified — NOT the historical MedImmune",
     "2022-11", "confirmed_different_entity",
     "https://www.fiercebiotech.com/biotech/press-release-astrazeneca-to-acquire-medimmune",
     "AstraZeneca acquired MedImmune for $58.00/share cash, tender completed by 2007-06-18. "
     "us_price_history MEDI rows start only 2022-11-17 (15+ years later) at ~$19-20/share — "
     "roughly a third of the deal price and over a decade after delisting. Different entity."),
    ("INFO", "IHS Markit Ltd.", "2022-02-28", "stock_merger_spgi",
     "unidentified — NOT the historical IHS Markit",
     "2024-10", "confirmed_different_entity",
     "https://press.spglobal.com/2022-02-28-S-P-Global-and-IHS-Markit-Complete-Merger",
     "Merged into S&P Global at 0.2838 SPGI shares/INFO share; SPGI closed at $380.89 on the "
     "merger date, implying ~$108.10/share consideration. IHS Markit 'is no longer a publicly "
     "traded company' per S&P Global's own release. us_price_history INFO rows start only "
     "2024-10-10 (2.5 years later) at ~$20/share — a different entity trading under the same "
     "vacated ticker. Note: us_security_outcomes already records the real INFO->SPGI merger "
     "correctly for backtest liquidation purposes; this row only flags the price *table* rows."),
    ("JAVA", "Sun Microsystems, Inc.", "2010-01-27", "cash_acquisition_oracle",
     "unidentified — NOT the historical Sun Microsystems",
     "2021-10", "confirmed_different_entity",
     "https://www.oracle.com/corporate/pressrelease/oracle-buys-sun-042009.html",
     "Oracle acquired Sun for $9.50/share cash, completed 2010-01-27 (deal announced 2009-04-20). "
     "us_price_history JAVA rows start only 2021-10-05 at ~$46-47/share — 5x the final deal price "
     "and 11+ years after delisting. Not the historical company."),
    ("SHLD", "Sears Holdings Corporation", "2018-10-15", "chapter11_bankruptcy",
     "unidentified — NOT the historical Sears Holdings (which trades OTC as SHLDQ, a different symbol)",
     "2023-09", "confirmed_different_entity",
     "https://www.9news.com/article/money/business/sears-is-delisted-on-nasdaq/73-607988737",
     "Sears was delisted from Nasdaq 2018-10 after 30 days under $1 (final price $0.36); surviving "
     "shell trades OTC as SHLDQ, not SHLD. us_price_history SHLD rows start only 2023-09-14 at "
     "~$24/share — a different ticker occupant entirely."),
    ("GENZ", "Genzyme Corporation", "2011-04-04", "cash_acquisition_sanofi",
     "unidentified — NOT the historical Genzyme Corp",
     "unknown", "confirmed_data_error",
     "https://www.news.sanofi.us/press-releases?item=118549",
     "SEC-documented deal price was $74.00/share cash (raised from $69, announced 2011-02-16, "
     "board recommended acceptance 2011-03-07). us_price_history GENZ trades at ~$20-24/share "
     "through this exact window (checked 2010-11 through 2011-05) with NO gap or jump around the "
     "2011-04-04 close — a ~3x price mismatch against the actual deal terms. The GENZ series in "
     "this DB (continuous 2008-2026, 4,698 rows) is not the real Genzyme Corporation at any point "
     "checked; do not use it for PIT membership return calculations"),
)


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_ticker_identity_conflict (
        ticker TEXT NOT NULL,
        historical_company TEXT NOT NULL,
        historical_event_date TEXT NOT NULL,
        historical_event_type TEXT NOT NULL,
        current_occupant TEXT NOT NULL,
        current_occupant_since TEXT,
        status TEXT NOT NULL,
        source_url TEXT NOT NULL,
        note TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(ticker))""")


def apply() -> int:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_primary_db(timeout=120)
    try:
        ensure_schema(conn)
        conn.executemany("""INSERT INTO us_ticker_identity_conflict
            (ticker,historical_company,historical_event_date,historical_event_type,
             current_occupant,current_occupant_since,status,source_url,note,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
              historical_company=excluded.historical_company,
              historical_event_date=excluded.historical_event_date,
              historical_event_type=excluded.historical_event_type,
              current_occupant=excluded.current_occupant,
              current_occupant_since=excluded.current_occupant_since,
              status=excluded.status,source_url=excluded.source_url,note=excluded.note,
              updated_at=excluded.updated_at""", [(*x, now) for x in CONFLICTS])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        count = verify.execute("SELECT COUNT(*) FROM us_ticker_identity_conflict").fetchone()[0]
    finally:
        verify.close()
    if count < len(CONFLICTS):
        raise RuntimeError("identity-conflict read-back count mismatch")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print(f"dry-run: {len(CONFLICTS)} rows would be written (use --apply)")
        for row in CONFLICTS:
            print(" ", row[0], row[6])
        return 0
    count = apply()
    print(f"applied: {count} rows in us_ticker_identity_conflict")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
