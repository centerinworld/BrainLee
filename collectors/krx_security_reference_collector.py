"""Collect point-in-time KRX equity references and product exclusions.

KRX stock base-info is the authoritative source for equity listing dates and
shares.  FinanceDataReader's KRX delisting list and Naver's ETF/ETN lists fill
product classification gaps that are not enabled for the configured KRX key.
Those secondary rows remain source-labelled and cannot make a run PIT-verified.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import requests

import config
from db_compat import connect_primary_db
from security_master import is_kr_equity_code


DB_PATH = Path(__file__).resolve().parents[1] / "stock.db"
KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"
NAVER_ETN_URL = "https://finance.naver.com/api/sise/etnItemList.nhn"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS krx_security_reference (
            stock_code TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT NOT NULL DEFAULT '',
            stock_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            security_type TEXT NOT NULL,
            is_etf_etn INTEGER NOT NULL DEFAULT 0,
            is_equity INTEGER NOT NULL DEFAULT 1,
            quality TEXT NOT NULL,
            source TEXT NOT NULL,
            source_note TEXT NOT NULL DEFAULT '',
            collected_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, effective_from, effective_to, source)
        );
        CREATE INDEX IF NOT EXISTS ix_krx_security_reference_asof
          ON krx_security_reference(stock_code, effective_from, effective_to);

        CREATE TABLE IF NOT EXISTS krx_security_share_snapshot (
            stock_code TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            shares_issued REAL NOT NULL,
            stock_name TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL DEFAULT '',
            quality TEXT NOT NULL,
            source TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY (stock_code, snapshot_date)
        );
        CREATE INDEX IF NOT EXISTS ix_krx_security_share_asof
          ON krx_security_share_snapshot(stock_code, snapshot_date);
        """
    )


def _iso(value: object) -> str:
    text = str(value or "")[:10].replace("-", "")
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) >= 8 and text[:8].isdigit() else ""


def _krx_rows(day: str) -> list[dict]:
    rows: list[dict] = []
    for path, market in (("sto/stk_isu_base_info", "KOSPI"), ("sto/ksq_isu_base_info", "KOSDAQ")):
        response = requests.get(
            f"{KRX_BASE}/{path}",
            params={"basDd": day.replace("-", "")},
            headers={"AUTH_KEY": config.KRX_API_KEY},
            timeout=30,
        )
        response.raise_for_status()
        for row in response.json().get("OutBlock_1", []):
            row["_market"] = market
            rows.append(row)
    return rows


def _last_krx_day(day: date) -> tuple[str, list[dict]]:
    for offset in range(10):
        candidate = day - timedelta(days=offset)
        rows = _krx_rows(candidate.isoformat())
        if rows:
            return candidate.isoformat(), rows
    raise RuntimeError(f"KRX base info unavailable near {day.isoformat()}")


def collect_reference(db_path: Path | str = DB_PATH, as_of: str | None = None) -> dict:
    target = date.fromisoformat(as_of) if as_of else date.today()
    snapshot_day, current_rows = _last_krx_day(target)
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_primary_db(timeout=60) if db_path == DB_PATH else sqlite3.connect(db_path, timeout=60)
    ensure_schema(conn)

    # Refresh sources independently. Never delete a previously good historical
    # source merely because its upstream provider returned an empty frame.
    # Historical daily-snapshot intervals are maintained by
    # sync_daily_snapshot_references() and must survive a current refresh.
    conn.execute("DELETE FROM krx_security_reference WHERE source='KRX_OPEN_API'")
    for row in current_rows:
        code = str(row.get("ISU_SRT_CD") or "").strip()
        listed = _iso(row.get("LIST_DD"))
        if not is_kr_equity_code(code) or not listed:
            continue
        shares = float(str(row.get("LIST_SHRS") or "0").replace(",", "") or 0)
        name = str(row.get("ISU_ABBRV") or row.get("ISU_NM") or "").strip()
        secugrp = str(row.get("SECUGRP_NM") or "주권").strip()
        sec_type = "preferred" if str(row.get("KIND_STKCERT_TP_NM") or "").find("우선") >= 0 else secugrp
        conn.execute(
            """INSERT INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, listed, "", name, row["_market"], sec_type, 0, 1,
             "official_krx_daily", "KRX_OPEN_API", "현행 주권 종목기본정보", now),
        )
        if shares > 0:
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_share_snapshot
                   VALUES (?,?,?,?,?,?,?,?)""",
                (code, snapshot_day, shares, name, row["_market"],
                 "official_daily_snapshot", "KRX_OPEN_API", now),
            )

    # The KRX delisting reference includes exact listing/delisting dates and
    # security groups. Product-like rights/funds are retained but ineligible.
    delisted = fdr.StockListing("KRX-DELISTING")
    excluded_groups = {"수익증권", "신주인수권증서", "신주인수권증권"}
    if not delisted.empty:
        conn.execute(
            "DELETE FROM krx_security_reference WHERE source='FinanceDataReader:KRX-DELISTING'"
        )
    for item in delisted.to_dict("records"):
        code = str(item.get("Symbol") or "").strip()
        start, end = _iso(item.get("ListingDate")), _iso(item.get("DelistingDate"))
        if not is_kr_equity_code(code) or not start or not end:
            continue
        group = str(item.get("SecuGroup") or "unknown")
        is_equity = int(group not in excluded_groups)
        conn.execute(
            """INSERT OR REPLACE INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, start, (date.fromisoformat(end) + timedelta(days=1)).isoformat(),
             str(item.get("Name") or ""), str(item.get("Market") or ""), group,
             int(group == "수익증권"), is_equity, "krx_delisting_reference",
             "FinanceDataReader:KRX-DELISTING", str(item.get("Reason") or ""), now),
        )

    # Explicit current ETF and ETN exclusions prevent product codes from being
    # inferred as ordinary shares merely because OHLCV exists.
    etfs = fdr.StockListing("ETF/KR")
    if not etfs.empty:
        conn.execute("DELETE FROM krx_security_reference WHERE source='FinanceDataReader:ETF/KR'")
    for item in etfs.to_dict("records"):
        code = str(item.get("Symbol") or "").strip()
        if is_kr_equity_code(code):
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, "1900-01-01", "", str(item.get("Name") or ""), "KRX", "ETF",
                 1, 0, "current_product_classification", "FinanceDataReader:ETF/KR",
                 "상장일은 미확정이며 상품 제외 판정에만 사용", now),
            )
    response = requests.get(
        NAVER_ETN_URL,
        params={"targetColumn": "acc_quant", "sortOrder": "desc"},
        timeout=30,
    )
    response.raise_for_status()
    conn.execute("DELETE FROM krx_security_reference WHERE source='NAVER_ETN'")
    for item in response.json().get("result", {}).get("etnItemList", []):
        code = str(item.get("itemcode") or "").strip()
        if is_kr_equity_code(code):
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, "1900-01-01", "", str(item.get("itemname") or ""), "KRX", "ETN",
                 1, 0, "current_product_classification", "NAVER_ETN",
                "상장일은 미확정이며 상품 제외 판정에만 사용", now),
            )
    # Keep the authoritative/current refresh durable even if a secondary
    # Yahoo metadata lookup below is slow or unavailable.
    conn.commit()

    # Yahoo chart metadata is a secondary safety net for recently delisted ETFs
    # missing from the current ETF list. It is never treated as official PIT data.
    covered = {row[0] for row in conn.execute("SELECT DISTINCT stock_code FROM krx_security_reference")}
    # price_history is tens of millions of rows in production. The previous
    # GROUP BY rescanned that entire table and regularly hit statement_timeout.
    # security_master_history already stores the same observed bounds at one
    # row per interval and is sufficient for this secondary ETF safety net.
    unresolved = conn.execute(
        """SELECT stock_code,MIN(effective_from),
                  MAX(COALESCE(effective_to,effective_from))
           FROM security_master_history
           WHERE is_etf_etn=1
              OR UPPER(stock_name) LIKE '%ETF%'
              OR UPPER(stock_name) LIKE '%ETN%'
              OR UPPER(stock_name) LIKE '%KODEX%'
              OR UPPER(stock_name) LIKE '%TIGER%'
              OR UPPER(stock_name) LIKE '%ARIRANG%'
              OR UPPER(stock_name) LIKE '%KOSEF%'
              OR UPPER(stock_name) LIKE '%KBSTAR%'
           GROUP BY stock_code"""
    ).fetchall()
    for code, first_seen, last_seen in unresolved:
        if code in covered:
            continue
        meta = None
        for suffix in ("KS", "KQ"):
            try:
                chart = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suffix}",
                    params={"range": "1d", "interval": "1d"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
                ).json()
                meta = (chart.get("chart", {}).get("result") or [{}])[0].get("meta")
                if meta:
                    break
            except (requests.RequestException, ValueError, TypeError, AttributeError):
                continue
        if meta and str(meta.get("instrumentType") or "").upper() == "ETF":
            end = (date.fromisoformat(last_seen) + timedelta(days=1)).isoformat()
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, first_seen, end, str(meta.get("longName") or ""), "KRX", "ETF",
                 1, 0, "secondary_product_classification", "YAHOO_CHART_META",
                 "최근 상품목록 누락 ETF의 보조 분류; PIT 검증 근거로 사용 금지", now),
            )
    conn.commit()
    result = {
        "snapshot_date": snapshot_day,
        "reference_rows": conn.execute("SELECT COUNT(*) FROM krx_security_reference").fetchone()[0],
        "equity_rows": conn.execute("SELECT COUNT(*) FROM krx_security_reference WHERE is_equity=1").fetchone()[0],
        "excluded_products": conn.execute("SELECT COUNT(*) FROM krx_security_reference WHERE is_etf_etn=1").fetchone()[0],
    }
    conn.close()
    return result


def collect_monthly_shares(start_year: int = 2015, end_year: int = 2019,
                           db_path: Path | str = DB_PATH) -> dict:
    """Backfill official month-end snapshots; timing remains monthly-approximate."""
    conn = connect_primary_db(timeout=60) if db_path == DB_PATH else sqlite3.connect(db_path, timeout=60)
    ensure_schema(conn)
    now = datetime.now().isoformat(timespec="seconds")
    months = rows_written = 0
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
            snapshot_day, rows = _last_krx_day(next_month - timedelta(days=1))
            months += 1
            for row in rows:
                code = str(row.get("ISU_SRT_CD") or "").strip()
                try:
                    shares = float(str(row.get("LIST_SHRS") or "0").replace(",", ""))
                except ValueError:
                    shares = 0
                if not is_kr_equity_code(code) or shares <= 0:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO krx_security_share_snapshot
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (code, snapshot_day, shares, str(row.get("ISU_ABBRV") or ""),
                     row["_market"], "official_month_end_snapshot_approx",
                     "KRX_OPEN_API", now),
                )
                rows_written += 1
            conn.commit()
    conn.close()
    return {"months": months, "rows_written": rows_written}


def collect_daily_shares(start_year: int = 2015, end_year: int = 2019,
                          db_path: Path | str = DB_PATH,
                          resume_from: str | None = None,
                          refresh_existing: bool = False,
                          workers: int = 4) -> dict:
    """2015~2019년 실제 거래일 전량에 대해 KRX 일별 발행주식수를 백필한다.

    2026-08-12: point_in_time_coverage 아티팩트가 approx_count==0을 요구하는데
    이 구간이 전부 collect_monthly_shares()의 월말 근사값(official_month_end_
    snapshot_approx)뿐이라 어떤 전략도 point_in_time_verified(rank3)에 도달할
    수 없었음. price_history 기준 실제 거래일마다 KRX Open API를 호출해
    "official_daily_snapshot"(정확값, approx 아님) 품질로 채워 넣는다.
    security_master.rebuild_security_master()의 우선순위(priority=3, 월말/일별
    구분 없이 quality 문자열 그대로 사용)에 따라 이 데이터가 반영되면 approx가
    exact로 승격된다. resume_from을 주면 그 날짜 이후만 이어서 처리(중단 재개용).
    """
    conn = connect_primary_db(timeout=120) if db_path == DB_PATH else sqlite3.connect(db_path, timeout=120)
    ensure_schema(conn)
    now = datetime.now().isoformat(timespec="seconds")
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"
    trading_days = [r[0] for r in conn.execute(
        "SELECT date FROM price_trading_calendar WHERE date>=? AND date<=? ORDER BY date",
        (start_date, end_date),
    ).fetchall()]
    if resume_from:
        trading_days = [d for d in trading_days if d >= resume_from]
    already = {r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM krx_security_share_snapshot WHERE quality='official_daily_snapshot'"
    ).fetchall()}
    days_done = rows_written = errors = skipped = 0
    total = len(trading_days)
    pending = [day for day in trading_days if refresh_existing or day not in already]

    def fetch(day: str):
        try:
            return day, _krx_rows(day), None
        except Exception as exc:  # noqa: BLE001
            return day, [], exc

    skipped = len(trading_days) - len(pending)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
      for day, rows, error in pool.map(fetch, pending):
        if error is not None:
            errors += 1
            print(f"[오류] {day}: {error}", flush=True)
            continue
        if not rows:
            continue
        inserts = []
        for row in rows:
            code = str(row.get("ISU_SRT_CD") or "").strip()
            try:
                shares = float(str(row.get("LIST_SHRS") or "0").replace(",", ""))
            except ValueError:
                shares = 0
            if not is_kr_equity_code(code) or shares <= 0:
                continue
            inserts.append((
                code, day, shares, str(row.get("ISU_ABBRV") or ""),
                row["_market"], "official_daily_snapshot", "KRX_OPEN_API", now,
            ))
        conn.executemany(
            """INSERT INTO krx_security_share_snapshot
               (stock_code,snapshot_date,shares_issued,stock_name,market,quality,source,collected_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(stock_code,snapshot_date) DO UPDATE SET
                 shares_issued=excluded.shares_issued,
                 stock_name=excluded.stock_name,market=excluded.market,
                 quality=excluded.quality,source=excluded.source,
                 collected_at=excluded.collected_at""",
            inserts,
        )
        rows_written += len(inserts)
        days_done += 1
        if days_done % 20 == 0:
            conn.commit()
            print(f"[진행] {days_done+skipped}/{total}일 처리(신규{days_done}/기존skip{skipped}), "
                  f"{rows_written}행 저장, 오류{errors}건, 최근일={day}", flush=True)
    conn.commit()
    conn.close()
    return {
        "total_trading_days": total, "days_done": days_done, "already_skipped": skipped,
        "rows_written": rows_written, "errors": errors,
    }


def sync_daily_snapshot_references(db_path: Path | str = DB_PATH) -> dict:
    """Turn exact KRX daily observations into contiguous listing intervals.

    This removes the old price-first/last-seen approximation, including for
    alphanumeric preferred-share codes and market-transfer histories.
    """
    conn = connect_primary_db(timeout=180) if db_path == DB_PATH else sqlite3.connect(db_path, timeout=180)
    ensure_schema(conn)
    now = datetime.now().isoformat(timespec="seconds")
    source = "KRX_OPEN_API_DAILY_HISTORY"
    conn.execute("DELETE FROM krx_security_reference WHERE source=?", (source,))
    intervals = conn.execute("""
        WITH days AS (
          SELECT market,snapshot_date,
                 DENSE_RANK() OVER (PARTITION BY market ORDER BY snapshot_date) day_seq
          FROM (SELECT DISTINCT market,snapshot_date
                FROM krx_security_share_snapshot
                WHERE quality='official_daily_snapshot') d
        ), obs AS (
          SELECT s.stock_code,s.stock_name,s.market,s.snapshot_date,d.day_seq,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.stock_code,s.market ORDER BY s.snapshot_date
                 ) stock_seq
          FROM krx_security_share_snapshot s
          JOIN days d ON d.market=s.market AND d.snapshot_date=s.snapshot_date
          WHERE s.quality='official_daily_snapshot'
        ), islands AS (
          SELECT stock_code,market,MAX(stock_name) stock_name,
                 MIN(snapshot_date) effective_from,MAX(snapshot_date) last_seen
          FROM obs GROUP BY stock_code,market,day_seq-stock_seq
        )
        SELECT i.stock_code,i.stock_name,i.market,i.effective_from,
               COALESCE((SELECT MIN(d.snapshot_date) FROM days d
                         WHERE d.market=i.market AND d.snapshot_date>i.last_seen),'') effective_to
        FROM islands i
        ORDER BY i.stock_code,i.effective_from
    """).fetchall()
    written = 0
    for code, name, market, start, end in intervals:
        if not is_kr_equity_code(code):
            continue
        conn.execute("""INSERT INTO krx_security_reference
            (stock_code,effective_from,effective_to,stock_name,market,security_type,
             is_etf_etn,is_equity,quality,source,source_note,collected_at)
            VALUES (?,?,?,?,?,'listed_equity',0,1,'official_daily_snapshot',?,?,?)
            ON CONFLICT(stock_code,effective_from,effective_to,source) DO UPDATE SET
              stock_name=excluded.stock_name,market=excluded.market,
              quality=excluded.quality,collected_at=excluded.collected_at
        """, (code, start, end, name or "", market or "", source,
               "KRX 종목기본정보 일별 관측으로 재구성한 정확 상장구간", now))
        written += 1
    # A current/delisted authoritative reference may already cover the whole
    # snapshot island (the normal case for a continuously listed stock). Keep
    # daily-derived intervals only where they fill a real historical gap, such
    # as the pre-transfer KOSDAQ life of a stock now listed on KOSPI.
    conn.execute("""DELETE FROM krx_security_reference
        WHERE source=? AND EXISTS (
          SELECT 1 FROM krx_security_reference r
          WHERE r.stock_code=krx_security_reference.stock_code AND r.source<>?
            AND r.is_equity=1 AND r.is_etf_etn=0
            AND r.effective_from<=krx_security_reference.effective_from
            AND (r.effective_to='' OR
                 (krx_security_reference.effective_to<>'' AND
                  r.effective_to>=krx_security_reference.effective_to))
        )""", (source, source))
    conn.commit()
    retained = conn.execute(
        "SELECT COUNT(*) FROM krx_security_reference WHERE source=?", (source,)
    ).fetchone()[0]
    conn.close()
    return {"intervals_written": written, "intervals_retained": retained, "source": source}


if __name__ == "__main__":
    print(collect_reference())
