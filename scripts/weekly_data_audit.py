#!/usr/bin/env python3
"""
weekly_data_audit.py — 주간 데이터 자동점검

1) 국민연금(nps_workplace_monthly) 최신 월 존재 여부 점검 및 최근 N개월 누락 월 백필
2) 주식 종목 수급(price_history inst/frn_net_buy) 최근 3년치 커버리지 점검
   - 3년 구간에 수급이 "전혀" 없는 종목만 선별 백필 (네이버 수급)
3) 금액 환산(inst/frn/ind_net_buy_amt) 갱신 (최근 3년)

실행 예:
  python3 scripts/weekly_data_audit.py
  python3 scripts/weekly_data_audit.py --nps-months 12 --investor-years 3
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta


DB_PATH = "/Applications/stock_dashboard/stock.db"
EMP_DB_PATH = "/Applications/stock_dashboard/employment_monitor/employment.db"
PROJECT_ROOT = "/Applications/stock_dashboard"


if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _run(cmd: list[str], timeout_s: int | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout_s)
    return p.returncode, p.stdout


def _since_date(years: int) -> str:
    return (datetime.today() - timedelta(days=365 * years)).strftime("%Y-%m-%d")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _connect_emp() -> sqlite3.Connection:
    conn = sqlite3.connect(EMP_DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@dataclass
class NpsReport:
    api_latest_ym: str
    existing_latest_ym: str
    existing_yms: set[str]
    missing_yms: list[str]
    collected: dict[str, dict]
    failures: list[str]


def audit_nps(nps_months: int, limit: int = 0) -> NpsReport:
    failures: list[str] = []
    collected: dict[str, dict] = {}

    # NPS 사업장 월별은 employment_monitor/employment.db 의 nps_monthly(data_ym) 에 저장
    conn_emp = _connect_emp()
    try:
        existing_latest = (
            conn_emp.execute("SELECT MAX(data_ym) AS ym FROM nps_monthly").fetchone()["ym"] or ""
        )
        existing_yms = {
            str(r["ym"])
            for r in conn_emp.execute("SELECT DISTINCT data_ym AS ym FROM nps_monthly").fetchall()
            if str(r["ym"] or "").isdigit() and len(str(r["ym"])) == 6
        }
    finally:
        conn_emp.close()

    def _load_public_api_key() -> str:
        # Prefer env var; fallback to reading /Applications/stock_dashboard/.env
        import os

        key = os.getenv("PUBLIC_DATA_API_KEY", "").strip()
        if key:
            return key
        try:
            with open("/Applications/stock_dashboard/.env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == "PUBLIC_DATA_API_KEY":
                        return v.strip().strip("'").strip('"')
        except Exception:
            pass
        return ""

    def _month_add(ym: str, delta_months: int) -> str:
        base = datetime.strptime(ym, "%Y%m")
        y = base.year
        m = base.month + delta_months
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        return f"{y:04d}{m:02d}"

    def _iter_months_backwards(ym: str, n: int) -> list[str]:
        out: list[str] = []
        cur = ym
        for _ in range(max(0, n)):
            out.append(cur)
            cur = _month_add(cur, -1)
        return out

    def _api_month_exists(ym: str, api_key: str, test_biz_no_6: str = "124810") -> tuple[bool, str | None]:
        # 삼성전자(124810) 사업자번호 앞 6자리 샘플로 최신 월 여부 확인
        from urllib.parse import unquote

        import requests

        url = "http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList"
        params = {
            "serviceKey": unquote(api_key),
            "bzowr_rgst_no": test_biz_no_6,
            "data_crt_ym": ym,
            "pageNo": 1,
            "numOfRows": 1,
            "_type": "json",
        }
        r = requests.get(url, params=params, timeout=12)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        return bool(items), None

    def _detect_api_latest_ym(existing_latest_ym: str) -> str:
        api_key = _load_public_api_key()
        if not api_key:
            failures.append("NPS API 키(PUBLIC_DATA_API_KEY) 로드 실패")
            return ""
        # existing_latest 기준으로 최대 6개월 앞으로 탐색 (동일월 재갱신 가능성을 고려해 existing_latest도 포함)
        start = existing_latest_ym or datetime.today().strftime("%Y%m")
        candidates = [start] + [_month_add(start, i) for i in range(1, 7)]
        latest = ""
        for ym in candidates:
            try:
                exists, err = _api_month_exists(ym, api_key=api_key)
                if err:
                    failures.append(f"NPS API 최신월 조회 실패({ym}): {err}")
                    break
                if exists:
                    latest = ym
                else:
                    # 연속성 가정: 최초로 끊기면 중단
                    if latest:
                        break
            except Exception as e:
                failures.append(f"NPS API 최신월 조회 실패({ym}): {e}")
                break
        return latest

    api_latest = _detect_api_latest_ym(existing_latest)

    missing: list[str] = []
    if api_latest:
        target_window = set(_iter_months_backwards(api_latest, nps_months))
        missing = sorted(target_window - existing_yms)

    def _collect_month_into_db(ym: str, limit_codes: int) -> dict:
        api_key = _load_public_api_key()
        if not api_key:
            raise RuntimeError("PUBLIC_DATA_API_KEY not found")

        # universe 목록은 stock.db(stock_universe), bizno는 employment.db(stock_bizno_map) 사용
        conn_stock = _connect()
        conn_emp2 = _connect_emp()
        try:
            rows = conn_emp2.execute(
                """
                SELECT m.stock_code, m.biz_no_6 AS biz_no_6
                FROM stock_bizno_map m
                WHERE LENGTH(m.stock_code)=6 AND m.stock_code GLOB '[0-9]*'
                ORDER BY m.stock_code
                """
            ).fetchall()
            # stock_name은 stock.db에서 조회 (표준 표기)
            name_map = {
                r["stock_code"]: r["stock_name"]
                for r in conn_stock.execute(
                    """
                    SELECT stock_code, stock_name
                    FROM stock_universe
                    WHERE market IN ('유가증권', 'KOSPI', '코스닥', 'KOSDAQ')
                      AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                      AND (stock_type IS NULL OR stock_type='보통주')
                    """
                ).fetchall()
            }
            if limit_codes and limit_codes > 0:
                rows = rows[:limit_codes]

            from urllib.parse import unquote

            import json
            import time

            import requests

            url = "http://apis.data.go.kr/B552015/NpsBsnmWorkplaceListInfoService/getNpsBsnmBssInfoList"
            inserted = 0
            attempted = 0
            for r in rows:
                biz_no_6 = (r["biz_no_6"] or "").strip()[:6]
                if not biz_no_6:
                    continue
                attempted += 1
                params = {
                    "serviceKey": unquote(api_key),
                    "bzowr_rgst_no": biz_no_6,
                    "data_crt_ym": ym,
                    "pageNo": 1,
                    "numOfRows": 10,
                    "_type": "json",
                }
                resp = requests.get(url, params=params, timeout=12)
                if resp.status_code != 200:
                    time.sleep(0.05)
                    continue
                data = resp.json()
                items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
                if isinstance(items, dict):
                    items = [items]
                if not items:
                    time.sleep(0.05)
                    continue
                item = items[0]
                # nps_monthly: 종목별 월 스냅샷(사업장수/입사/퇴사/증감)
                stock_code = r["stock_code"]
                conn_emp2.execute(
                    """
                    INSERT OR REPLACE INTO nps_monthly
                    (stock_code, data_ym, new_hires, terminations, net_change, wkpl_count, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        stock_code,
                        ym,
                        int(item.get("nwAcqzrCnt", 0) or 0),
                        int(item.get("lssJnngpCnt", 0) or 0),
                        int(item.get("jngpCnt", 0) or 0),
                        int(item.get("wkplCnt", 0) or 0),
                    ),
                )
                inserted += 1
                if inserted % 200 == 0:
                    conn_emp2.commit()
                time.sleep(0.05)
            conn_emp2.commit()
            return {"ym": ym, "attempted": attempted, "inserted": inserted}
        finally:
            conn_stock.close()
            conn_emp2.close()

    for ym in missing:
        try:
            collected[ym] = _collect_month_into_db(ym, limit_codes=limit)
        except Exception as e:
            failures.append(f"{ym}: {e}")

    return NpsReport(
        api_latest_ym=api_latest,
        existing_latest_ym=existing_latest,
        existing_yms=existing_yms,
        missing_yms=missing,
        collected=collected,
        failures=failures,
    )


@dataclass
class InvestorReport:
    universe_kospi: int
    universe_kosdaq: int
    since: str
    latest_price_date: str
    codes_missing: list[str]
    latest_investor_date: str
    backfill_rc: int | None
    backfill_output_tail: str
    compute_rc: int | None
    compute_output_tail: str
    failures: list[str]


def audit_investor(years: int, chunk: int, limit_missing: int = 300) -> InvestorReport:
    since = _since_date(years)
    conn = _connect()
    failures: list[str] = []
    try:
        universe = conn.execute(
            """
            SELECT market, COUNT(DISTINCT stock_code) AS cnt
            FROM stock_universe
            WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
              AND (stock_type IS NULL OR stock_type='보통주')
            GROUP BY market
            """
        ).fetchall()
        counts = {r["market"]: int(r["cnt"] or 0) for r in universe}
        kospi = counts.get("KOSPI", 0) + counts.get("유가증권", 0)
        kosdaq = counts.get("KOSDAQ", 0) + counts.get("코스닥", 0)

        latest_price_date = (
            conn.execute(
                """
                SELECT MAX(substr(date,1,10)) AS d
                FROM price_history
                WHERE stock_code IN (
                    SELECT stock_code FROM stock_universe
                    WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
                      AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                      AND (stock_type IS NULL OR stock_type='보통주')
                )
                """
            ).fetchone()["d"]
            or ""
        )

        latest_investor_date = (
            conn.execute(
                f"""
                SELECT MAX(substr(date,1,10)) AS d
                FROM price_history
                WHERE substr(date,1,10) >= ?
                  AND (inst_net_buy != 0 OR frn_net_buy != 0)
                  AND stock_code IN (
                    SELECT stock_code FROM stock_universe
                    WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
                      AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                      AND (stock_type IS NULL OR stock_type='보통주')
                  )
                """,
                (since,),
            ).fetchone()["d"]
            or ""
        )

        # "3년 구간에 수급이 전혀 없는" 종목 선별
        # (0 값은 정상일 수도 있지만, 3년 동안 단 1건도 없는 경우는 거의 누락으로 간주)
        missing_rows = conn.execute(
            f"""
            WITH universe AS (
              SELECT stock_code
              FROM stock_universe
              WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
                AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                AND (stock_type IS NULL OR stock_type='보통주')
            ),
            inv AS (
              SELECT stock_code, COUNT(*) AS n
              FROM price_history
              WHERE substr(date,1,10) >= ?
                AND (inst_net_buy != 0 OR frn_net_buy != 0)
              GROUP BY stock_code
            )
            SELECT u.stock_code
            FROM universe u
            LEFT JOIN inv i ON i.stock_code = u.stock_code
            WHERE COALESCE(i.n, 0) = 0
            ORDER BY u.stock_code
            LIMIT ?
            """,
            (since, limit_missing),
        ).fetchall()
        codes_missing = [r["stock_code"] for r in missing_rows]
    finally:
        conn.close()

    backfill_rc: int | None = None
    backfill_tail = ""
    if codes_missing:
        # chunked backfill to keep command line size reasonable
        for i in range(0, len(codes_missing), chunk):
            batch = codes_missing[i : i + chunk]
            cmd = [
                sys.executable,
                "/Applications/stock_dashboard/collect_naver_investor.py",
                "--years",
                str(float(years)),
                "--codes",
                ",".join(batch),
            ]
            try:
                rc, out = _run(cmd, timeout_s=None)
                backfill_rc = rc
                backfill_tail = "\n".join(out.strip().splitlines()[-30:])
                if rc != 0:
                    failures.append(f"naver_investor backfill failed batch {i//chunk+1}: rc={rc}")
                    break
            except Exception as e:
                failures.append(f"naver_investor backfill error batch {i//chunk+1}: {e}")
                break

    compute_rc: int | None = None
    compute_tail = ""
    cmd = [sys.executable, "/Applications/stock_dashboard/compute_investor_amounts.py", "--days", str(365 * years + 10)]
    try:
        compute_rc, out = _run(cmd, timeout_s=None)
        compute_tail = "\n".join(out.strip().splitlines()[-30:])
        if compute_rc != 0:
            failures.append(f"compute_investor_amounts failed: rc={compute_rc}")
    except Exception as e:
        failures.append(f"compute_investor_amounts error: {e}")

    return InvestorReport(
        universe_kospi=kospi,
        universe_kosdaq=kosdaq,
        since=since,
        latest_price_date=latest_price_date,
        codes_missing=codes_missing,
        latest_investor_date=latest_investor_date,
        backfill_rc=backfill_rc,
        backfill_output_tail=backfill_tail,
        compute_rc=compute_rc,
        compute_output_tail=compute_tail,
        failures=failures,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nps-months", type=int, default=12, help="NPS 누락 점검 대상 월 수 (기본 12)")
    ap.add_argument("--nps-limit", type=int, default=0, help="NPS 수집 종목 제한 (0=전체)")
    ap.add_argument("--investor-years", type=int, default=3, help="수급 점검 기간(년, 기본 3)")
    ap.add_argument("--investor-chunk", type=int, default=50, help="네이버 수급 백필 배치 크기")
    ap.add_argument("--investor-missing-limit", type=int, default=300, help="백필 후보 종목 최대 개수")
    args = ap.parse_args()

    print("== Weekly Data Audit ==")

    nps_r = audit_nps(nps_months=args.nps_months, limit=args.nps_limit)
    print(f"[NPS] api_latest_ym={nps_r.api_latest_ym} existing_latest_ym={nps_r.existing_latest_ym}")
    print(f"[NPS] missing_yms({len(nps_r.missing_yms)}): {', '.join(nps_r.missing_yms) if nps_r.missing_yms else '(none)'}")
    if nps_r.failures:
        print("[NPS] failures:")
        for f in nps_r.failures:
            print(f"  - {f}")

    inv_r = audit_investor(years=args.investor_years, chunk=args.investor_chunk, limit_missing=args.investor_missing_limit)
    print(f"[INV] universe KOSPI={inv_r.universe_kospi} KOSDAQ={inv_r.universe_kosdaq}")
    print(f"[INV] since={inv_r.since} latest_price_date={inv_r.latest_price_date} latest_investor_date={inv_r.latest_investor_date}")
    print(f"[INV] missing_codes({len(inv_r.codes_missing)}): {', '.join(inv_r.codes_missing[:20])}{' ...' if len(inv_r.codes_missing) > 20 else ''}")
    if inv_r.failures:
        print("[INV] failures:")
        for f in inv_r.failures:
            print(f"  - {f}")

    # exit code: any failure -> 2
    return 2 if (nps_r.failures or inv_r.failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
