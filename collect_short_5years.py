#!/usr/bin/env python3
"""
collect_short_5years.py — 대차잔고 5년치 역사 데이터 백필
======================================================
API 문서(오픈API 활용자가이드_금융위원회_주식대차정보) 확인 기반:
  - 모든 6개 엔드포인트가 basDt(날짜) 파라미터를 지원 (항목구분: 옵션)
  - 초당 최대 30 TPS / 평균 응답 500ms

수집 대상 테이블:
  short_rank_daily    ← getStLendAndBorrItemRank_V2   (대차종목순위)
  short_sell_daily    ← getStItemLendAndBorrStatu_V2  (종목별대차현황)
  short_sector_daily  ← getStBusiTypePartStatu_V2     (업종별참여현황) [신규]
  short_monthly_stat  ← getMontLendAndBorrStatu_V2    (월별집계, 이미 2008~)
  short_foreign_balance ← getNatiAndForeLendAndBorrBalaCo_V2 (이미 2008~)
  short_foreign_trade   ← getNatiAndForeLendAndBorrTrad_V2   (이미 2008~)

실행:
  python3 /Applications/stock_dashboard/collect_short_5years.py            # 전체(rank+svc+sector)
  python3 /Applications/stock_dashboard/collect_short_5years.py --mode test
  python3 /Applications/stock_dashboard/collect_short_5years.py --mode rank
  python3 /Applications/stock_dashboard/collect_short_5years.py --mode svc
  python3 /Applications/stock_dashboard/collect_short_5years.py --mode sector
  python3 /Applications/stock_dashboard/collect_short_5years.py --start 20210101 --end 20231231

예상 소요: rank+svc 5년치 ~1,250일 × ~2초/일 ≈ 약 40~60분
"""

import argparse
import asyncio
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from env_utils import require_env

# ── 설정 ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = str(BASE_DIR / "stock.db")

Path(BASE_DIR / "logs").mkdir(exist_ok=True)
LOG_PATH = str(BASE_DIR / "logs" / f"short_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

API_KEY  = require_env("PUBLIC_DATA_API_KEY")
BASE_URL = "https://apis.data.go.kr/1160100/GetStocLendBorrInfoService_V2"

DEFAULT_START = (date.today() - timedelta(days=365 * 5 + 10)).strftime("%Y%m%d")
DEFAULT_END   = date.today().strftime("%Y%m%d")

PAGE_SIZE  = 1000
RATE_LIMIT = 0.3   # API당 요청 간격 (초)

# 한국 공휴일 2021~2026
_KR_HOLIDAYS = {
    # 2021
    "20210101","20210212","20210301","20210505","20210519","20210606",
    "20210816","20210920","20210921","20210922","20211003","20211011","20211225",
    # 2022
    "20220101","20220201","20220202","20220203","20220301","20220305",
    "20220505","20220508","20220606","20220815","20220909","20220910","20220911","20220912",
    "20221003","20221010","20221225",
    # 2023
    "20230101","20230122","20230123","20230124","20230301","20230505","20230527","20230529",
    "20230606","20230815","20230928","20230929","20230930","20231002","20231003","20231009","20231225",
    # 2024
    "20240101","20240209","20240210","20240211","20240301","20240410",
    "20240505","20240515","20240606","20240815","20240916","20240917","20240918",
    "20241003","20241009","20241225",
    # 2025
    "20250101","20250128","20250129","20250130","20250301","20250505",
    "20250606","20250815","20251003","20251006","20251007","20251008","20251009","20251225",
    # 2026
    "20260101","20260216","20260217","20260218","20260301","20260505",
    "20260525","20260606","20260817","20260924","20260925","20260928",
    "20261009","20261225",
}

# ── 로깅 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ── 영업일 생성 ────────────────────────────────────────────────────────────────

def trading_days(start_str: str, end_str: str) -> list[str]:
    start = datetime.strptime(start_str, "%Y%m%d").date()
    end   = datetime.strptime(end_str,   "%Y%m%d").date()
    days  = []
    cur   = start
    while cur <= end:
        s = cur.strftime("%Y%m%d")
        if cur.weekday() < 5 and s not in _KR_HOLIDAYS:
            days.append(s)
        cur += timedelta(days=1)
    return days


# ── DB 헬퍼 ───────────────────────────────────────────────────────────────────

def get_existing_dates(table: str, date_col: str = "bas_dt") -> set[str]:
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(f"SELECT DISTINCT {date_col} FROM {table}").fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception as e:
        log.warning(f"[DB] {table} 날짜 조회 오류: {e}")
        return set()


def ensure_tables():
    """필요한 테이블이 없으면 생성."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_rank_daily (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            bas_dt             TEXT NOT NULL,
            isin_cd            TEXT NOT NULL,
            stock_code         TEXT,
            stock_name         TEXT,
            lnb_scrt_dcd       TEXT,
            lnb_ccl_stck_cnt   REAL DEFAULT 0,
            rcal_rdpt_stck_cnt REAL DEFAULT 0,
            rdpt_stck_cnt      REAL DEFAULT 0,
            lnb_rman_stck_cnt  REAL DEFAULT 0,
            lnb_bal            REAL DEFAULT 0,
            created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bas_dt, isin_cd)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_sell_daily (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            bas_dt         TEXT NOT NULL,
            stock_code     TEXT NOT NULL,
            stock_name     TEXT,
            short_qty      REAL DEFAULT 0,
            short_rdpt_qty REAL DEFAULT 0,
            borrow_bal_qty REAL DEFAULT 0,
            short_amt      REAL,
            borrow_bal_amt REAL,
            borrow_bal_pct REAL,
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bas_dt, stock_code)
        )
    """)
    # 기존 테이블에 short_rdpt_qty 컬럼 추가
    try:
        conn.execute("ALTER TABLE short_sell_daily ADD COLUMN short_rdpt_qty REAL DEFAULT 0")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_sector_daily (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            bas_dt        TEXT NOT NULL,
            isin_cd       TEXT NOT NULL,
            stock_code    TEXT,
            stock_name    TEXT,
            sic_cd        TEXT,
            sic_nm        TEXT,
            stck_lndn_bal REAL DEFAULT 0,
            stck_lndn_rto REAL DEFAULT 0,
            stck_brw_bal  REAL DEFAULT 0,
            stck_brw_rto  REAL DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bas_dt, isin_cd)
        )
    """)

    conn.commit()
    conn.close()
    log.info("[DB] 테이블 확인 완료")


# ── API 공통 ──────────────────────────────────────────────────────────────────

def _safe(v, cast=float):
    try:
        s = str(v).replace(",", "").strip()
        return cast(s) if s not in ("", "-", "N/A") else 0.0
    except Exception:
        return 0.0


async def _fetch_pages(client: httpx.AsyncClient, service: str,
                       bas_dt: str | None = None, extra: dict | None = None) -> list[dict]:
    """V2 API 페이지네이션 — basDt 지원 엔드포인트용."""
    url    = f"{BASE_URL}/{service}"
    params = {"serviceKey": API_KEY, "resultType": "json", "numOfRows": str(PAGE_SIZE)}
    if bas_dt:
        params["basDt"] = bas_dt
    if extra:
        params.update(extra)

    all_items: list[dict] = []
    page = 1
    while True:
        try:
            r    = await client.get(url, params={**params, "pageNo": str(page)}, timeout=60)
            body  = r.json().get("response", {}).get("body", {})
            # 결과 코드 확인
            hdr = r.json().get("response", {}).get("header", {})
            if hdr.get("resultCode", "00") not in ("00", ""):
                log.warning(f"[API] {service} 오류: {hdr}")
                break
            items = body.get("items") or {}
            rows  = items.get("item", []) if isinstance(items, dict) else []
            if isinstance(rows, dict):
                rows = [rows]
            if not rows:
                break
            all_items.extend(rows)
            total = int(body.get("totalCount", 0))
            if len(all_items) >= total or len(rows) < PAGE_SIZE:
                break
            page += 1
        except Exception as e:
            log.warning(f"[API] {service} 페이지 {page} 오류: {e}")
            break
    return all_items


# ── 연결 테스트 ────────────────────────────────────────────────────────────────

async def test_connectivity() -> bool:
    log.info("=== APIs.data.go.kr 연결 테스트 ===")
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{BASE_URL}/getStLendAndBorrItemRank_V2",
                params={"serviceKey": API_KEY, "resultType": "json",
                        "numOfRows": "3", "pageNo": "1", "basDt": "20260429"},
            )
            hdr  = r.json().get("response", {}).get("header", {})
            body = r.json().get("response", {}).get("body", {})
            code = hdr.get("resultCode", "")
            msg  = hdr.get("resultMsg", "")
            cnt  = body.get("totalCount", 0)
            log.info(f"  resultCode: {code} / {msg}")
            log.info(f"  20260429 대차종목 수: {cnt}")
            if code == "00":
                log.info("✅ API 연결 성공!")
                return True
            else:
                log.error(f"❌ API 오류 응답: {code}")
                return False
        except Exception as e:
            log.error(f"❌ 연결 실패: {e}")
            log.error("   이 서버에서는 apis.data.go.kr가 DNS 차단됨")
            log.error("   Mac 터미널에서 직접 실행하세요")
            return False


# ══════════════════════════════════════════════════════════════════════════════
# 대차종목순위 (getStLendAndBorrItemRank_V2) → short_rank_daily
# ══════════════════════════════════════════════════════════════════════════════

async def collect_rank(client: httpx.AsyncClient, bas_dt: str) -> list[dict]:
    rows = await _fetch_pages(client, "getStLendAndBorrItemRank_V2", bas_dt)
    result = []
    for r in rows:
        isin = str(r.get("isinCd", "")).strip()
        code: str | None = None
        if isin.startswith("KR") and len(isin) == 12:
            c = isin[3:9]
            code = c if c.isdigit() and c != "000000" else None
        elif len(isin) == 6 and isin.isdigit() and isin != "000000":
            code = isin
        result.append({
            "bas_dt":              bas_dt,
            "isin_cd":             isin,
            "stock_code":          code,
            "stock_name":          r.get("isinCdNm", ""),
            "lnb_scrt_dcd":        r.get("lnbScrtDcd", ""),
            "lnb_ccl_stck_cnt":    _safe(r.get("lnbCclStckCnt",   0)),
            "rcal_rdpt_stck_cnt":  _safe(r.get("rcalRdptStckCnt", 0)),
            "rdpt_stck_cnt":       _safe(r.get("rdptStckCnt",     0)),
            "lnb_rman_stck_cnt":   _safe(r.get("lnbRmanStckCnt",  0)),
            "lnb_bal":             _safe(r.get("lnbBal",          0)),
        })
    return result


def save_rank(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany("""
        INSERT INTO short_rank_daily
          (bas_dt, isin_cd, stock_code, stock_name, lnb_scrt_dcd,
           lnb_ccl_stck_cnt, rcal_rdpt_stck_cnt, rdpt_stck_cnt,
           lnb_rman_stck_cnt, lnb_bal)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(bas_dt, isin_cd) DO UPDATE SET
          stock_code         = excluded.stock_code,
          lnb_ccl_stck_cnt   = excluded.lnb_ccl_stck_cnt,
          rcal_rdpt_stck_cnt = excluded.rcal_rdpt_stck_cnt,
          rdpt_stck_cnt      = excluded.rdpt_stck_cnt,
          lnb_rman_stck_cnt  = excluded.lnb_rman_stck_cnt,
          lnb_bal            = excluded.lnb_bal
    """, [(r["bas_dt"], r["isin_cd"], r["stock_code"], r["stock_name"], r["lnb_scrt_dcd"],
           r["lnb_ccl_stck_cnt"], r["rcal_rdpt_stck_cnt"], r["rdpt_stck_cnt"],
           r["lnb_rman_stck_cnt"], r["lnb_bal"]) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 종목별대차거래현황 (getStItemLendAndBorrStatu_V2) → short_sell_daily
# ══════════════════════════════════════════════════════════════════════════════

async def collect_svc(client: httpx.AsyncClient, bas_dt: str) -> list[dict]:
    rows = await _fetch_pages(client, "getStItemLendAndBorrStatu_V2", bas_dt)
    result = []
    for r in rows:
        isin = str(r.get("isinCd", "")).strip()
        if not isin.startswith("KR") or len(isin) != 12:
            continue
        code = isin[3:9]
        if not code.isdigit() or code == "000000":
            continue
        # basDt 필터 방어
        if r.get("basDt", bas_dt) != bas_dt:
            continue
        result.append({
            "bas_dt":        bas_dt,
            "stock_code":    code,
            "stock_name":    r.get("isinCdNm", ""),
            "short_qty":     _safe(r.get("lnbCclStckCnt",  0)),   # 당일 체결
            "short_rdpt_qty":_safe(r.get("lnbRdptStckCnt", 0)),   # 당일 상환
            "borrow_bal_qty":_safe(r.get("lnbRmanStckCnt", 0)),   # 잔고
        })
    return result


def save_svc(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany("""
        INSERT INTO short_sell_daily
          (bas_dt, stock_code, stock_name, short_qty, short_rdpt_qty, borrow_bal_qty)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(bas_dt, stock_code) DO UPDATE SET
          stock_name     = excluded.stock_name,
          short_qty      = excluded.short_qty,
          short_rdpt_qty = excluded.short_rdpt_qty,
          borrow_bal_qty = excluded.borrow_bal_qty
    """, [(r["bas_dt"], r["stock_code"], r["stock_name"], r["short_qty"],
           r["short_rdpt_qty"], r["borrow_bal_qty"]) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 업종별참여현황 (getStBusiTypePartStatu_V2) → short_sector_daily
# ══════════════════════════════════════════════════════════════════════════════

async def collect_sector(client: httpx.AsyncClient, bas_dt: str) -> list[dict]:
    rows = await _fetch_pages(client, "getStBusiTypePartStatu_V2", bas_dt)
    result = []
    for r in rows:
        isin = str(r.get("isinCd", "")).strip()
        code: str | None = None
        if isin.startswith("KR") and len(isin) == 12:
            c = isin[3:9]
            code = c if c.isdigit() and c != "000000" else None
        elif len(isin) == 6 and isin.isdigit() and isin != "000000":
            code = isin
        bas = r.get("basDt", bas_dt)
        if bas_dt and bas != bas_dt:
            continue
        result.append({
            "bas_dt":       bas,
            "isin_cd":      isin,
            "stock_code":   code,
            "stock_name":   r.get("isinCdNm", ""),
            "sic_cd":       r.get("sicCd", ""),
            "sic_nm":       r.get("sicNm", ""),
            "stck_lndn_bal":_safe(r.get("stckLndnBal", 0)),
            "stck_lndn_rto":_safe(r.get("stckLndnRto", 0)),
            "stck_brw_bal": _safe(r.get("stckBrwBal",  0)),
            "stck_brw_rto": _safe(r.get("stckBrwRto",  0)),
        })
    return result


def save_sector(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany("""
        INSERT INTO short_sector_daily
          (bas_dt, isin_cd, stock_code, stock_name, sic_cd, sic_nm,
           stck_lndn_bal, stck_lndn_rto, stck_brw_bal, stck_brw_rto)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(bas_dt, isin_cd) DO UPDATE SET
          stock_code    = excluded.stock_code,
          stck_lndn_bal = excluded.stck_lndn_bal,
          stck_lndn_rto = excluded.stck_lndn_rto,
          stck_brw_bal  = excluded.stck_brw_bal,
          stck_brw_rto  = excluded.stck_brw_rto
    """, [(r["bas_dt"], r["isin_cd"], r["stock_code"], r["stock_name"],
           r["sic_cd"], r["sic_nm"], r["stck_lndn_bal"], r["stck_lndn_rto"],
           r["stck_brw_bal"], r["stck_brw_rto"]) for r in rows])
    conn.commit()
    conn.close()
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 날짜별 병렬 수집 (rank + svc + sector 동시)
# ══════════════════════════════════════════════════════════════════════════════

async def collect_all_for_date(client: httpx.AsyncClient, bas_dt: str, modes: set[str]) -> dict[str, int]:
    tasks = {}
    if "rank"   in modes: tasks["rank"]   = collect_rank(client, bas_dt)
    if "svc"    in modes: tasks["svc"]    = collect_svc(client, bas_dt)
    if "sector" in modes: tasks["sector"] = collect_sector(client, bas_dt)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    saved = {}
    for name, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            log.warning(f"  [{bas_dt}] {name} 오류: {result}")
            saved[name] = 0
            continue
        if name == "rank":
            saved["short_rank_daily"]   = save_rank(result)
        elif name == "svc":
            saved["short_sell_daily"]   = save_svc(result)
        elif name == "sector":
            saved["short_sector_daily"] = save_sector(result)
    return saved


# ══════════════════════════════════════════════════════════════════════════════
# 메인 백필 루프
# ══════════════════════════════════════════════════════════════════════════════

async def backfill(start_str: str, end_str: str, modes: set[str], force: bool = False):
    log.info("=" * 65)
    log.info(f"  대차잔고 백필 시작 — 모드: {', '.join(modes)}")
    log.info(f"  기간: {start_str} ~ {end_str}")
    log.info("=" * 65)

    ok = await test_connectivity()
    if not ok:
        return

    all_days = trading_days(start_str, end_str)

    # 각 모드별 이미 수집된 날짜
    existing: dict[str, set[str]] = {}
    if not force:
        if "rank"   in modes: existing["rank"]   = get_existing_dates("short_rank_daily")
        if "svc"    in modes: existing["svc"]    = get_existing_dates("short_sell_daily")
        if "sector" in modes: existing["sector"] = get_existing_dates("short_sector_daily")

    # 하나라도 미수집인 날짜를 대상으로
    missing_days = []
    for d in all_days:
        need = False
        for m in modes:
            if d not in existing.get(m, set()):
                need = True
                break
        if need:
            missing_days.append(d)

    log.info(f"  전체 영업일: {len(all_days)}일, 수집 대상: {len(missing_days)}일")
    if not missing_days:
        log.info("✅ 모든 날짜 이미 수집됨. 종료.")
        return

    total   = len(missing_days)
    success = 0
    no_data = 0

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(90.0, connect=15.0),
        limits=httpx.Limits(max_connections=5),
    ) as client:
        for i, bas_dt in enumerate(missing_days, 1):
            # 날짜별 필요한 모드만 추출
            active = {m for m in modes if bas_dt not in existing.get(m, set())}
            if not active:
                continue

            try:
                result = await collect_all_for_date(client, bas_dt, active)
                any_saved = sum(result.values()) > 0
                if any_saved:
                    success += 1
                    parts = " | ".join(f"{k}:{v}건" for k, v in result.items() if v)
                    log.info(f"[{i:4d}/{total}] {bas_dt}: {parts} ✅")
                else:
                    no_data += 1
                    log.info(f"[{i:4d}/{total}] {bas_dt}: 데이터없음 (공휴일?) — skip")

                await asyncio.sleep(RATE_LIMIT)

            except Exception as e:
                log.error(f"[{i:4d}/{total}] {bas_dt}: 오류 — {e}")
                await asyncio.sleep(2)

            if i % 50 == 0:
                log.info(f"  ── 중간 [{i}/{total}] 성공:{success} 데이터없음:{no_data} ──")

    log.info("=" * 65)
    log.info(f"  완료: 성공 {success}일 / 데이터없음 {no_data}일 / 전체 {total}일")
    log.info(f"  로그: {LOG_PATH}")
    log.info("=" * 65)


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="대차잔고 5년치 백필")
    parser.add_argument("--mode", default="all",
                        choices=["all", "rank", "svc", "sector", "test"],
                        help="all=전체(rank+svc+sector), rank/svc/sector=개별, test=연결확인")
    parser.add_argument("--start", default=DEFAULT_START, help="시작일 YYYYMMDD")
    parser.add_argument("--end",   default=DEFAULT_END,   help="종료일 YYYYMMDD")
    parser.add_argument("--force", action="store_true",   help="기존 데이터 무시하고 재수집")
    args = parser.parse_args()

    log.info(f"로그: {LOG_PATH}")
    log.info(f"기간: {args.start} ~ {args.end} | 모드: {args.mode}")

    if args.mode == "test":
        asyncio.run(test_connectivity())
        return

    ensure_tables()

    modes = {"rank", "svc", "sector"} if args.mode == "all" else {args.mode}
    asyncio.run(backfill(args.start, args.end, modes, args.force))


if __name__ == "__main__":
    main()
