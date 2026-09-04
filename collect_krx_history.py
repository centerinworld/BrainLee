"""
collect_krx_history.py — KRX API 전종목 일별 OHLCV + 지수 과거 데이터 수집

승인 API 엔드포인트:
  유가증권 일별매매정보  : https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd
  코스닥   일별매매정보  : https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd
  KOSPI 시리즈 일별시세  : https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd
  KOSDAQ 시리즈 일별시세 : https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd
  KRX 시리즈 일별시세    : https://data-dbg.krx.co.kr/svc/apis/idx/krx_dd_trd

저장:
  price_history  — 종목 OHLCV + 지수(^KS11, ^KQ11)
  stock_universe — 시가총액 갱신 (오늘 데이터)

사용법:
  python3 collect_krx_history.py              # 2019년 이전 백필 (2010~2018)
  python3 collect_krx_history.py --mode full   # 2010~오늘 전체
  python3 collect_krx_history.py --mode recent # 최근 60일 갱신
  python3 collect_krx_history.py --start 2022-01-01 --end 2022-12-31
"""

import argparse
import sqlite3
import time
import sys
from datetime import date, datetime, timedelta

import requests
import config
from db_utils import connect_stock_db

DB_PATH   = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
API_KEY   = config.KRX_API_KEY
BASE_URL  = "https://data-dbg.krx.co.kr/svc/apis"
HEADERS   = {"AUTH_KEY": API_KEY}
SLEEP_SEC = 0.3   # 요청 간격 (API rate limit)


# ─────────────────────────────────────────────
# 거래일 목록 (주말 제외)
# ─────────────────────────────────────────────
def _trading_days(start: str, end: str) -> list:
    days  = []
    cur   = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end,   "%Y-%m-%d").date()
    while cur <= end_d:
        if cur.weekday() < 5:
            days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return days


# ─────────────────────────────────────────────
# API 호출
# ─────────────────────────────────────────────
def _fetch(path: str, bas_dd: str) -> list:
    url = f"{BASE_URL}/{path}"
    try:
        r = requests.get(
            url,
            params={"basDd": bas_dd.replace("-", "")},
            headers=HEADERS,
            timeout=20,
        )
        if r.status_code == 200:
            return r.json().get("OutBlock_1", [])
    except Exception as e:
        print(f"    [오류] {path} {bas_dd}: {e}")
    return []


# ─────────────────────────────────────────────
# 종목 데이터 저장 (유가증권/코스닥)
# ─────────────────────────────────────────────
def _save_stocks(conn, rows: list, bas_dd: str, today_str: str) -> tuple:
    inserted = updated = 0

    for r in rows:
        code = str(r.get("ISU_CD", "")).strip()
        if not code or len(code) != 6 or not code.isdigit():
            continue

        def _n(key):
            v = r.get(key, "")
            try:
                return float(str(v).replace(",", "")) if v not in ("", "-", None) else 0.0
            except:
                return 0.0

        close        = _n("TDD_CLSPRC")
        open_        = _n("TDD_OPNPRC")
        high         = _n("TDD_HGPRC")
        low          = _n("TDD_LWPRC")
        volume       = _n("ACC_TRDVOL")
        trade_amount = _n("ACC_TRDVAL")   # 거래대금(원) ★신규
        mktcap       = _n("MKTCAP")
        list_shrs    = _n("LIST_SHRS")    # 상장주식수 ★신규
        sect_tp      = str(r.get("SECT_TP_NM", "")).strip()  # 소속부(대/중/소형) ★신규

        if close <= 0:
            continue

        cur = conn.execute("""
            INSERT OR IGNORE INTO price_history
                (stock_code, date, open, high, low, close, volume, trade_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, bas_dd, open_, high, low, close, volume, trade_amount))

        if cur.rowcount > 0:
            inserted += 1
        else:
            # 기존 행 보완 (volume/trade_amount 없는 경우)
            cursor = conn.execute("""
                UPDATE price_history
                SET open=?, high=?, low=?, close=?, volume=?, trade_amount=?
                WHERE stock_code=? AND date=?
                  AND (volume IS NULL OR volume=0)
            """, (open_, high, low, close, volume, trade_amount, code, bas_dd))
            updated += max(cursor.rowcount, 0)

        # 시가총액 + 상장주식수 + 소속부 갱신 (오늘 데이터만)
        if bas_dd == today_str:
            if mktcap > 0:
                conn.execute(
                    "UPDATE stock_universe SET market_cap=? WHERE stock_code=?",
                    (mktcap, code)
                )
            if list_shrs > 0:
                conn.execute(
                    "UPDATE stock_universe SET shares_issued=? WHERE stock_code=?",
                    (int(list_shrs), code)
                )
            if sect_tp:
                conn.execute(
                    "UPDATE stock_universe SET sector_type=? WHERE stock_code=?",
                    (sect_tp, code)
                )

    return inserted, updated


# ─────────────────────────────────────────────
# 지수 데이터 저장 (KOSPI→^KS11, KOSDAQ→^KQ11)
# ─────────────────────────────────────────────
# 정확한 이름 매칭만 허용 — 서브인덱스(중형주, 기술성장기업부 등)가 덮어쓰는 버그 방지
INDEX_EXACT_MAP = {
    "코스피":     "^KS11",
    "KOSPI":      "^KS11",
    "코스닥":     "^KQ11",
    "KOSDAQ":     "^KQ11",
    "코스피 200": "^KS200",
    "코스닥 150": "^KQ150",
}

def _save_index(conn, rows: list, bas_dd: str) -> int:
    saved = 0
    for r in rows:
        idx_nm = str(r.get("IDX_NM", "")).strip()
        code = INDEX_EXACT_MAP.get(idx_nm)
        if not code:
            continue

        def _n(key):
            v = r.get(key, "")
            try:
                return float(str(v).replace(",", "")) if v not in ("", "-", None) else 0.0
            except:
                return 0.0

        close  = _n("CLSPRC_IDX")
        open_  = _n("OPNPRC_IDX")
        high   = _n("HGPRC_IDX")
        low    = _n("LWPRC_IDX")
        volume = _n("ACC_TRDVOL")

        if close <= 0:
            continue

        cur = conn.execute("""
            INSERT OR IGNORE INTO price_history
                (stock_code, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (code, bas_dd, open_, high, low, close, volume))

        if cur.rowcount > 0:
            saved += 1
        else:
            # 값 갱신 (지수는 항상 덮어쓰기)
            conn.execute("""
                UPDATE price_history
                SET open=?, high=?, low=?, close=?, volume=?
                WHERE stock_code=? AND date=?
            """, (open_, high, low, close, volume, code, bas_dd))

    return saved


# ─────────────────────────────────────────────
# 이미 수집된 날짜
# ─────────────────────────────────────────────
def _collected_dates(conn) -> set:
    """KRX로 충분히 수집된 날짜 (종목 수 500개 이상).
    KIS 장중 수집은 watchlist 소수 종목만 저장하므로 임계값을 높게 설정.
    """
    rows = conn.execute("""
        SELECT date, COUNT(*) AS cnt FROM price_history
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        GROUP BY date HAVING COUNT(*) >= 500
    """).fetchall()
    return {r[0] for r in rows}


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",    default="backfill",
                        choices=["backfill", "full", "recent", "range"])
    parser.add_argument("--start",   default="2010-01-04")
    parser.add_argument("--end",     default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true",
                        help="이미 수집된 날짜도 재수집")
    args = parser.parse_args()

    today_str = date.today().strftime("%Y-%m-%d")

    if args.mode == "backfill":
        # 현재 price_history 최솟값 이전을 채우기
        start, end = "2010-01-04", "2018-12-31"
    elif args.mode == "full":
        start, end = "2010-01-04", today_str
    elif args.mode == "recent":
        start = (date.today() - timedelta(days=60)).strftime("%Y-%m-%d")
        end   = today_str
    else:
        start, end = args.start, args.end

    print(f"KRX 수집 시작: {start} ~ {end}  (mode={args.mode})")

    conn = connect_stock_db(timeout=60, wal=True)

    print("기존 수집 날짜 확인 중...", end=" ", flush=True)
    collected = _collected_dates(conn)
    print(f"{len(collected)}일 보유")

    all_days    = _trading_days(start, end)
    target_days = all_days if args.force else [d for d in all_days if d not in collected]

    print(f"수집 대상: {len(target_days)}일 / 전체 {len(all_days)}일 (스킵: {len(all_days)-len(target_days)}일)")

    if args.dry_run:
        print(f"[dry-run] 첫 5일: {target_days[:5]}")
        conn.close()
        return

    total_ins = total_upd = total_idx = 0

    for idx, day in enumerate(target_days):
        print(f"[{idx+1}/{len(target_days)}] {day}", end="  ", flush=True)

        day_ins = day_upd = day_idx = 0

        # ① 유가증권 종목
        rows = _fetch("sto/stk_bydd_trd", day)
        if rows:
            i, u = _save_stocks(conn, rows, day, today_str)
            day_ins += i; day_upd += u
        time.sleep(SLEEP_SEC)

        # ② 코스닥 종목
        rows = _fetch("sto/ksq_bydd_trd", day)
        if rows:
            i, u = _save_stocks(conn, rows, day, today_str)
            day_ins += i; day_upd += u
        time.sleep(SLEEP_SEC)

        # ③ KOSPI 지수 (^KS11)
        rows = _fetch("idx/kospi_dd_trd", day)
        if rows:
            day_idx += _save_index(conn, rows, day)
        time.sleep(SLEEP_SEC)

        # ④ KOSDAQ 지수 (^KQ11)
        rows = _fetch("idx/kosdaq_dd_trd", day)
        if rows:
            day_idx += _save_index(conn, rows, day)
        time.sleep(SLEEP_SEC)

        conn.commit()
        total_ins += day_ins
        total_upd += day_upd
        total_idx += day_idx

        if day_ins + day_upd + day_idx > 0:
            print(f"종목+{day_ins} 수정{day_upd} 지수+{day_idx}")
        else:
            print("(공휴일/휴장)")

        if (idx + 1) % 50 == 0:
            print(f"\n  ── 누계: 종목삽입 {total_ins:,} / 수정 {total_upd:,} / 지수 {total_idx} ──\n")

    conn.close()
    print(f"\n완료! 종목 삽입 {total_ins:,}건 / 수정 {total_upd:,}건 / 지수 {total_idx}건")


if __name__ == "__main__":
    main()
