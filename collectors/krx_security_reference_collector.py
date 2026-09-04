"""Collect point-in-time KRX equity references and product exclusions.

KRX stock base-info is the authoritative source for equity listing dates and
shares.  FinanceDataReader's KRX delisting list and Naver's ETF/ETN lists fill
product classification gaps that are not enabled for the configured KRX key.
Those secondary rows remain source-labelled and cannot make a run PIT-verified.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import requests

import config


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
    conn = sqlite3.connect(db_path, timeout=60)
    ensure_schema(conn)

    # Refresh only reference rows. Historical share snapshots are retained.
    conn.execute("DELETE FROM krx_security_reference WHERE source<>'YAHOO_CHART_META'")
    for row in current_rows:
        code = str(row.get("ISU_SRT_CD") or "").strip()
        listed = _iso(row.get("LIST_DD"))
        if len(code) != 6 or not code.isdigit() or not listed:
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
    for item in delisted.to_dict("records"):
        code = str(item.get("Symbol") or "").strip()
        start, end = _iso(item.get("ListingDate")), _iso(item.get("DelistingDate"))
        if len(code) != 6 or not code.isdigit() or not start or not end:
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
    for item in etfs.to_dict("records"):
        code = str(item.get("Symbol") or "").strip()
        if len(code) == 6 and code.isdigit():
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
    for item in response.json().get("result", {}).get("etnItemList", []):
        code = str(item.get("itemcode") or "").strip()
        if len(code) == 6 and code.isdigit():
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_reference VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, "1900-01-01", "", str(item.get("itemname") or ""), "KRX", "ETN",
                 1, 0, "current_product_classification", "NAVER_ETN",
                "상장일은 미확정이며 상품 제외 판정에만 사용", now),
            )

    # Yahoo chart metadata is a secondary safety net for recently delisted ETFs
    # missing from the current ETF list. It is never treated as official PIT data.
    covered = {row[0] for row in conn.execute("SELECT DISTINCT stock_code FROM krx_security_reference")}
    unresolved = conn.execute(
        """SELECT stock_code,MIN(substr(date,1,10)),MAX(substr(date,1,10))
           FROM price_history
           WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
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
    conn = sqlite3.connect(db_path, timeout=60)
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
                if len(code) != 6 or not code.isdigit() or shares <= 0:
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
                          resume_from: str | None = None) -> dict:
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
    conn = sqlite3.connect(db_path, timeout=120)
    ensure_schema(conn)
    now = datetime.now().isoformat(timespec="seconds")
    start_date = f"{start_year}-01-01"
    end_date = f"{end_year}-12-31"
    trading_days = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(date,1,10) FROM price_history WHERE date>=? AND date<=? ORDER BY 1",
        (start_date, end_date),
    ).fetchall()]
    if resume_from:
        trading_days = [d for d in trading_days if d >= resume_from]
    already = {r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM krx_security_share_snapshot WHERE quality='official_daily_snapshot'"
    ).fetchall()}
    days_done = rows_written = errors = skipped = 0
    total = len(trading_days)
    for day in trading_days:
        if day in already:
            skipped += 1
            continue
        try:
            rows = _krx_rows(day)
        except Exception as exc:  # noqa: BLE001 — 개별 일자 실패는 스킵하고 계속 진행
            errors += 1
            print(f"[오류] {day}: {exc}", flush=True)
            continue
        if not rows:
            continue
        for row in rows:
            code = str(row.get("ISU_SRT_CD") or "").strip()
            try:
                shares = float(str(row.get("LIST_SHRS") or "0").replace(",", ""))
            except ValueError:
                shares = 0
            if len(code) != 6 or not code.isdigit() or shares <= 0:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO krx_security_share_snapshot
                   VALUES (?,?,?,?,?,?,?,?)""",
                (code, day, shares, str(row.get("ISU_ABBRV") or ""),
                 row["_market"], "official_daily_snapshot", "KRX_OPEN_API", now),
            )
            rows_written += 1
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


if __name__ == "__main__":
    print(collect_reference())
