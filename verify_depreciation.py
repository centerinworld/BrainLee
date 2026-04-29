"""
verify_depreciation.py — DART 감가상각 vs 네이버금융 교차검증 + 전종목 업데이트

사용법:
  python3 verify_depreciation.py --verify              # 50개 종목 교차검증
  python3 verify_depreciation.py --verify --verify-n 100
  python3 verify_depreciation.py --update-all          # 전종목 업데이트
  python3 verify_depreciation.py --update-all --limit 200
  python3 verify_depreciation.py --update-all --force  # 기존 값도 덮어쓰기

단위 규칙 (중요):
  DART API  → 원(KRW) 단위 반환  (예: 13,500,000,000,000)
  DB 저장   → 원(KRW) 그대로     (예: 13500000000000)
  화면표시  → ÷1e8 → 억원        (예: 135,000 억원)
  Naver     → 억원 표시           (예: 135,000)
  → 검증 시: DART 값 ÷1e8 == Naver 값 이어야 함
"""
import argparse
import logging
import sqlite3
import time
import sys
from typing import Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH    = "/Applications/stock_dashboard/stock.db"
RATE_DART  = 0.8   # DART API rate limit
RATE_NAVER = 0.5   # 네이버 rate limit

HEADERS_NAVER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}

# 감가상각 계정명 키워드 우선순위 (높은 우선순위 먼저)
DEPR_KEYWORDS = [
    "감가상각비",        # 가장 표준적
    "유형자산상각비",    # 두 번째로 흔함
    "감가상각",          # 나머지 변형 포괄: "감가상각비및상각비", "감가상각비및무형자산상각비" 등
]

MISMATCH_THRESHOLD = 0.25  # 25% 이상 차이 시 불일치 판정


# ─────────────────────────────────────────────
# DART
# ─────────────────────────────────────────────

def _get_dart():
    import config as _cfg
    import OpenDartReader
    return OpenDartReader(_cfg.DART_API_KEY)


def fetch_dart_depreciation(dart, code: str, year: int) -> tuple[Optional[float], Optional[str]]:
    """
    DART finstate_all 에서 연간 현금흐름표 감가상각비 추출.
    반환: (원 단위 값, 매칭된 계정명). 없으면 (None, None).

    주의: DART CF row 중 양수(+)인 값만 사용.
    현금흐름 간접법에서 감가상각은 이익에 더하는 항목이므로 양수.
    """
    import pandas as pd

    for fs in ["CFS", "OFS"]:
        try:
            df = dart.finstate_all(code, year, "11011", fs_div=fs)
            if df is None or df.empty:
                time.sleep(0.15)
                continue

            cf_df = df[df["sj_div"] == "CF"] if "sj_div" in df.columns else df

            for _, row in cf_df.iterrows():
                acc = str(row.get("account_nm", "")).replace(" ", "")
                vc = "thstrm_amount"
                if not acc or vc not in row or pd.isna(row[vc]):
                    continue
                for kw in DEPR_KEYWORDS:
                    if kw in acc:
                        try:
                            v = float(str(row[vc]).replace(",", ""))
                            if v > 0:
                                return v, acc
                        except Exception:
                            pass

        except Exception as e:
            logger.debug(f"[DART] {code} {year} {fs}: {e}")
        time.sleep(0.15)

    return None, None


def fetch_dart_all_cf_accounts(dart, code: str, year: int) -> list[tuple[str, float]]:
    """디버깅용: CF 계정 전체 목록 반환."""
    import pandas as pd
    result = []
    for fs in ["CFS", "OFS"]:
        try:
            df = dart.finstate_all(code, year, "11011", fs_div=fs)
            if df is None or df.empty:
                continue
            cf_df = df[df["sj_div"] == "CF"] if "sj_div" in df.columns else df
            for _, row in cf_df.iterrows():
                acc = str(row.get("account_nm", "")).replace(" ", "")
                vc = "thstrm_amount"
                if acc and vc in row and not pd.isna(row[vc]):
                    try:
                        v = float(str(row[vc]).replace(",", ""))
                        result.append((acc, v, fs))
                    except Exception:
                        pass
            if result:
                break
        except Exception:
            pass
        time.sleep(0.15)
    return result


# ─────────────────────────────────────────────
# 네이버 금융 — 현금흐름표 감가상각비 파싱
# ─────────────────────────────────────────────

def fetch_naver_cashflow_table(code: str) -> list[dict]:
    """
    네이버 금융 재무제표(현금흐름표) 전체 테이블 파싱.
    반환: [{"label": str, "values": [v1, v2, ...]}, ...]  단위: 억원
    """
    url = (f"https://finance.naver.com/item/coinfo.naver"
           f"?code={code}&category=finsum&finGubun=CASH")
    try:
        r = requests.get(url, headers=HEADERS_NAVER, timeout=12)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        # 재무제표 데이터는 .tb_type1 클래스 테이블에 있음
        tbl = soup.find("table", class_="tb_type1")
        if not tbl:
            # fallback: 첫 번째 큰 테이블
            tbls = soup.find_all("table")
            for t in tbls:
                if len(t.find_all("tr")) > 5:
                    tbl = t
                    break

        if not tbl:
            return []

        items = []
        for row in tbl.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True)
            if not label:
                continue
            vals = []
            for cell in cells[1:]:
                txt = cell.get_text(strip=True).replace(",", "")
                if txt and txt not in ("-", ""):
                    try:
                        vals.append(float(txt))
                    except ValueError:
                        vals.append(None)
                else:
                    vals.append(None)
            items.append({"label": label, "values": vals})
        return items

    except Exception as e:
        logger.debug(f"[Naver CF] {code}: {e}")
        return []


def fetch_naver_depreciation(code: str) -> Optional[float]:
    """
    네이버 금융 현금흐름표에서 감가상각비(억원) 파싱.
    반환: 억원 단위 최신 연간 값. 없으면 None.
    """
    items = fetch_naver_cashflow_table(code)
    for item in items:
        label = item["label"].replace(" ", "")
        for kw in DEPR_KEYWORDS:
            if kw in label:
                # 첫 번째 유효한 값 반환 (최신 연간)
                for v in item["values"]:
                    if v is not None and v > 0:
                        return v
    return None


# ─────────────────────────────────────────────
# 검증 (샘플 N개)
# ─────────────────────────────────────────────

def get_sample_codes(conn: sqlite3.Connection, n: int = 50) -> list[dict]:
    """시총 상위 종목 N개 (stock_universe 기준)."""
    rows = conn.execute("""
        SELECT su.stock_code, su.stock_name, su.market_cap
        FROM stock_universe su
        WHERE su.market IN ('KOSPI','KOSDAQ')
          AND LENGTH(su.stock_code) = 6
          AND su.stock_code GLOB '[0-9]*'
          AND su.market_cap > 0
        ORDER BY su.market_cap DESC
        LIMIT ?
    """, (n,)).fetchall()
    return [{"code": r[0], "name": r[1], "mktcap": r[2]} for r in rows]


def run_verify(n: int = 50, show_cf_detail: bool = False):
    logger.info(f"=== 감가상각 교차검증 시작 (샘플 {n}개) ===")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    dart = _get_dart()
    samples = get_sample_codes(conn, n)
    year = 2023

    results       = []
    no_dart       = []
    no_naver      = []
    mismatches    = []
    unit_suspects = []  # 단위 의심 케이스

    print(f"\n{'종목코드':8s} {'종목명':14s} {'DART(억)':>10s} {'계정명':30s} {'Naver(억)':>10s} {'DB현재(억)':>10s} {'비고'}")
    print("-" * 105)

    for i, s in enumerate(samples, 1):
        code = s["code"]
        name = s["name"]

        # DART 연간 감가상각
        dart_raw, dart_acc = fetch_dart_depreciation(dart, code, year)
        dart_억 = round(dart_raw / 1e8) if dart_raw else None
        time.sleep(RATE_DART)

        # 네이버 감가상각 (억원)
        naver_억 = fetch_naver_depreciation(code)
        time.sleep(RATE_NAVER)

        # DB 현재 값
        db_row = conn.execute(
            "SELECT depreciation FROM cash_flow_data "
            "WHERE stock_code=? AND year=? AND is_annual=1",
            (code, year)
        ).fetchone()
        db_val = db_row[0] if db_row else None
        db_억 = round(db_val / 1e8) if db_val else None

        # 판정
        note = ""
        ok = True

        if dart_억 and naver_억:
            ratio = abs(dart_억 - naver_억) / max(dart_억, naver_억, 1)
            if ratio > MISMATCH_THRESHOLD:
                ok = False
                note = f"불일치 {ratio:.0%}"
                mismatches.append(s | {"dart_억": dart_억, "naver_억": naver_억, "dart_acc": dart_acc})

            # 단위 의심: DART 값이 네이버의 100배이면 단위 오류
            if abs(dart_억 - naver_억 * 100) / max(dart_억, 1) < 0.05:
                note = "⚠️단위의심(DART=원, Naver=백만)"
                unit_suspects.append(s | {"dart_억": dart_억, "naver_억": naver_억})
        elif dart_억 is None:
            note = "DART없음"
            no_dart.append(code)
        elif naver_억 is None:
            note = "Naver없음"
            no_naver.append(code)

        # DART 값은 있는데 100 이하면 의심 (단위가 억원인지 확인)
        if dart_억 and dart_억 < 1:
            note += " ⚠️값너무작음"
            unit_suspects.append(s | {"dart_억": dart_억, "dart_raw": dart_raw})

        status = "✅" if ok and "없음" not in note else ("⚠️" if "의심" in note or "없음" in note else "❌")
        print(f"{code:8s} {name[:13]:14s} {str(dart_억 or '-'):>10s} {str(dart_acc or '-')[:29]:30s} "
              f"{str(naver_억 or '-'):>10s} {str(db_억 or '-'):>10s} {status} {note}")

        results.append({
            "code": code, "name": name,
            "dart_억": dart_억, "dart_raw": dart_raw, "dart_acc": dart_acc,
            "naver_억": naver_억, "db_억": db_억,
            "ok": ok, "note": note,
        })

        # CF 전체 계정명 출력 (--detail 시)
        if show_cf_detail and dart_억 is None:
            all_cf = fetch_dart_all_cf_accounts(dart, code, year)
            if all_cf:
                print(f"  └ CF 계정 전체: {[(a, round(v/1e8) if abs(v)>1e7 else v) for a,v,_ in all_cf[:8]]}")

    # 요약
    print("\n" + "=" * 105)
    print(f"검증 종목: {n}개  |  DART 값 있음: {sum(1 for r in results if r['dart_억'])}  "
          f"|  Naver 값 있음: {sum(1 for r in results if r['naver_억'])}  "
          f"|  DB 이미 있음: {sum(1 for r in results if r['db_억'])}")
    print(f"불일치(>{MISMATCH_THRESHOLD:.0%}): {len(mismatches)}  "
          f"|  DART없음: {len(no_dart)}  |  Naver없음: {len(no_naver)}  "
          f"|  단위의심: {len(unit_suspects)}")

    if mismatches:
        print("\n--- ❌ 불일치 종목 ---")
        for m in mismatches:
            print(f"  {m['code']} {m['name']}: DART={m['dart_억']}억 | Naver={m['naver_억']}억 | 계정={m['dart_acc']}")

    if unit_suspects:
        print("\n--- ⚠️ 단위 의심 종목 ---")
        for u in unit_suspects:
            print(f"  {u['code']} {u['name']}: {u}")

    conn.close()
    return results


# ─────────────────────────────────────────────
# 전종목 업데이트
# ─────────────────────────────────────────────

def update_all(limit: Optional[int] = None, years: int = 5, force: bool = False):
    """
    전종목 현금흐름표 감가상각비 업데이트.

    로직:
    1. financial_data에 있는 전 국내종목 대상
    2. DART finstate_all → CF 계정 → DEPR_KEYWORDS 매칭
    3. cash_flow_data 행 UPDATE (없으면 INSERT)
    4. 이미 값이 있는 행은 force 없이 스킵
    """
    import pandas as pd
    from datetime import date

    logger.info(f"=== 전종목 감가상각 업데이트 시작 (years={years}, force={force}) ===")
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    dart = _get_dart()

    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM financial_data
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        ORDER BY stock_code
    """).fetchall()
    codes = [r[0] for r in rows]
    if limit:
        codes = codes[:limit]

    today = date.today()
    y, m = today.year, today.month
    latest_y = y - 1 if m < 5 else y

    total_updated = 0
    total_skipped = 0
    total_nohit   = 0
    total_err     = 0

    for i, code in enumerate(codes, 1):
        for year in range(latest_y, latest_y - years, -1):
            # 이미 값 있으면 스킵 (force 모드 아닐 때)
            if not force:
                existing = conn.execute(
                    "SELECT depreciation FROM cash_flow_data "
                    "WHERE stock_code=? AND year=? AND is_annual=1",
                    (code, year)
                ).fetchone()
                if existing and existing[0] is not None:
                    total_skipped += 1
                    continue

            fn_data = None
            for fs in ["CFS", "OFS"]:
                try:
                    df = dart.finstate_all(code, year, "11011", fs_div=fs)
                    if df is not None and not df.empty:
                        fn_data = df
                        break
                except Exception:
                    pass
                time.sleep(0.15)

            if fn_data is None or fn_data.empty:
                total_nohit += 1
                time.sleep(RATE_DART)
                continue

            cf_df = fn_data[fn_data["sj_div"] == "CF"] if "sj_div" in fn_data.columns else fn_data
            depr_val = None
            matched_acc = None

            for _, row in cf_df.iterrows():
                acc = str(row.get("account_nm", "")).replace(" ", "")
                vc = "thstrm_amount"
                if not acc or vc not in row or pd.isna(row[vc]):
                    continue
                for kw in DEPR_KEYWORDS:
                    if kw in acc:
                        try:
                            v = float(str(row[vc]).replace(",", ""))
                            # 양수만 저장 (감가상각은 간접법 이익 조정 항목으로 양수)
                            if v > 0:
                                depr_val = v
                                matched_acc = acc
                                break
                        except Exception:
                            pass
                if depr_val is not None:
                    break

            if depr_val is None:
                total_nohit += 1
                logger.debug(f"[{code}] {year} 감가상각 계정 없음")
                time.sleep(RATE_DART)
                continue

            # 단위 검증: 너무 작은 값은 이상하므로 경고
            depr_억 = depr_val / 1e8
            if depr_억 < 0.1:
                logger.warning(f"[{code}] {year} 감가상각 값 이상 ({depr_억:.2f}억): {matched_acc}={depr_val}")
                total_err += 1
                time.sleep(RATE_DART)
                continue

            # cash_flow_data 업데이트
            exists = conn.execute(
                "SELECT 1 FROM cash_flow_data "
                "WHERE stock_code=? AND year=? AND is_annual=1 AND quarter=4",
                (code, year)
            ).fetchone()

            try:
                if exists:
                    conn.execute(
                        "UPDATE cash_flow_data SET depreciation=? "
                        "WHERE stock_code=? AND year=? AND is_annual=1 AND quarter=4",
                        (depr_val, code, year)
                    )
                else:
                    conn.execute(
                        """INSERT OR IGNORE INTO cash_flow_data
                           (stock_code, year, quarter, is_annual, depreciation)
                           VALUES (?,?,4,1,?)""",
                        (code, year, depr_val)
                    )
                conn.commit()
                total_updated += 1
                logger.info(
                    f"[{i}/{len(codes)}] {code} {year} OK: {matched_acc} = {round(depr_억)}억원"
                )
            except Exception as e:
                logger.warning(f"[{code}] {year} 저장 실패: {e}")
                total_err += 1

            time.sleep(RATE_DART)

        if i % 50 == 0:
            logger.info(
                f"진행: {i}/{len(codes)} | 업데이트={total_updated} "
                f"| 스킵={total_skipped} | 미발견={total_nohit} | 오류={total_err}"
            )

    logger.info(
        f"=== 완료: 업데이트={total_updated} | 스킵={total_skipped} "
        f"| 미발견={total_nohit} | 오류={total_err} ==="
    )
    conn.close()


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="감가상각 검증 및 업데이트")
    ap.add_argument("--verify",      action="store_true", help="N개 교차검증 (기본 50)")
    ap.add_argument("--verify-n",    type=int, default=50, help="검증 샘플 수")
    ap.add_argument("--detail",      action="store_true", help="DART없는 종목의 CF계정 전체 출력")
    ap.add_argument("--update-all",  action="store_true", help="전종목 업데이트")
    ap.add_argument("--limit",       type=int, default=None, help="업데이트 종목 수 제한")
    ap.add_argument("--years",       type=int, default=5,    help="소급 연도 수 (기본 5)")
    ap.add_argument("--force",       action="store_true",    help="기존 값 덮어쓰기")
    args = ap.parse_args()

    if args.verify:
        run_verify(n=args.verify_n, show_cf_detail=args.detail)
    elif args.update_all:
        update_all(limit=args.limit, years=args.years, force=args.force)
    else:
        ap.print_help()
        print("\n▶ 순서: --verify 로 검증 → 이상 없으면 --update-all 실행")
