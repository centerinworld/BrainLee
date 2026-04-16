"""
supplement_financials.py — FinanceDataReader로 재무데이터 보완 수집

DART API 한도 소진 시 FDR(FinanceDataReader)로 주요 종목 재무데이터 보완.
FDR은 KRX/네이버 기반으로 연간 재무 요약 제공 (DART보다 빠름, 분기 제한적).

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 supplement_financials.py              # watchlist 전체
  python3 supplement_financials.py --codes 005930 000660  # 특정 종목
"""

import sys, sqlite3, time, logging
from pathlib import Path
from datetime import date

sys.path.insert(0, "/Applications/stock_dashboard")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

DB_PATH = Path("/Applications/stock_dashboard/stock.db")


def get_watchlist_codes(conn) -> list:
    codes = []
    try:
        for row in conn.execute("SELECT stock_code FROM watchlist"):
            c = str(row[0]).strip().zfill(6)
            if c.isdigit() and len(c) == 6:
                codes.append(c)
    except Exception as e:
        logger.warning(f"watchlist 조회 실패: {e}")
    return codes


def has_annual(conn, code) -> bool:
    """연간 데이터가 3건 이상 있는지 확인."""
    cnt = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE stock_code=? AND is_annual=1",
        (code,)).fetchone()[0]
    return cnt >= 3


def collect_fdr(conn, code) -> int:
    """FDR로 연간 재무데이터 수집. 저장 건수 반환."""
    try:
        import FinanceDataReader as fdr
        import pandas as pd

        # FDR 재무제표: stock_code 기준 연간 요약
        df = fdr.DataReader(code, 'krx-desc')
        if df is None or df.empty:
            return 0

        saved = 0
        for year in range(date.today().year, 2014, -1):
            col = str(year)
            if col not in df.columns:
                continue

            def _val(key):
                try:
                    v = df.loc[key, col] if key in df.index else None
                    if v is None or pd.isna(v): return None
                    return float(str(v).replace(",", ""))
                except: return None

            # 매핑 (FDR krx-desc 인덱스 기준)
            revenue  = _val("매출액") or _val("영업수익")
            op       = _val("영업이익")
            ni       = _val("당기순이익")
            assets   = _val("자산총계")
            liab     = _val("부채총계")
            equity   = _val("자본총계")
            eps      = _val("EPS")
            bps      = _val("BPS")

            if not any([revenue, op, ni]):
                continue

            # 이미 있으면 스킵
            exists = conn.execute(
                "SELECT 1 FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
                (code, year)).fetchone()
            if exists:
                continue

            conn.execute("""
                INSERT OR REPLACE INTO financial_data (
                    stock_code, year, quarter, is_annual,
                    revenue, operating_profit, net_income,
                    total_assets, total_liabilities, total_equity,
                    eps, bps, created_at
                ) VALUES (?,?,4,1,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """, (code, year, revenue, op, ni, assets, liab, equity, eps, bps))
            conn.commit()
            saved += 1

        return saved

    except ImportError:
        logger.error("FinanceDataReader 미설치: pip install finance-datareader")
        return 0
    except Exception as e:
        logger.warning(f"[FDR] {code}: {e}")
        return 0


def collect_naver(conn, code) -> int:
    """네이버 금융 재무제표 스크래핑으로 보완."""
    try:
        import requests
        from bs4 import BeautifulSoup
        import re

        url = f"https://finance.naver.com/item/finstate_all.naver?code={code}&finGubun=DEPR"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        # 연간 탭 테이블
        table = soup.select_one("table.gHead01")
        if not table:
            return 0

        # 헤더에서 연도 추출
        headers = [th.get_text(strip=True) for th in table.select("thead tr th")]
        years = []
        for h in headers:
            m = re.search(r"(\d{4})", h)
            if m: years.append(int(m.group(1)))

        if not years:
            return 0

        rows = {}
        for tr in table.select("tbody tr"):
            tds = tr.select("td")
            if not tds: continue
            label = tds[0].get_text(strip=True)
            vals  = []
            for td in tds[1:]:
                t = td.get_text(strip=True).replace(",","").replace("-","")
                try: vals.append(float(t) * 1e8)  # 네이버는 억원 단위
                except: vals.append(None)
            rows[label] = vals

        def _get(label_list, idx):
            for label in label_list:
                for k, v in rows.items():
                    if label in k and idx < len(v):
                        return v[idx]
            return None

        saved = 0
        for i, year in enumerate(years):
            revenue = _get(["매출액","영업수익"], i)
            op      = _get(["영업이익"], i)
            ni      = _get(["당기순이익"], i)
            assets  = _get(["자산총계"], i)
            liab    = _get(["부채총계"], i)
            equity  = _get(["자본총계"], i)

            if not any([revenue, op, ni]):
                continue

            exists = conn.execute(
                "SELECT 1 FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1 LIMIT 1",
                (code, year)).fetchone()
            if exists:
                continue

            conn.execute("""
                INSERT OR REPLACE INTO financial_data (
                    stock_code, year, quarter, is_annual,
                    revenue, operating_profit, net_income,
                    total_assets, total_liabilities, total_equity,
                    created_at
                ) VALUES (?,?,4,1,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            """, (code, year, revenue, op, ni, assets, liab, equity))
            conn.commit()
            saved += 1

        return saved

    except Exception as e:
        logger.warning(f"[Naver] {code}: {e}")
        return 0


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", nargs="+")
    parser.add_argument("--naver-only", action="store_true", help="네이버 스크래핑만 사용")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    codes = [c.zfill(6) for c in args.codes] if args.codes else get_watchlist_codes(conn)

    print(f"\n대상 종목: {len(codes)}개")
    print(f"수집 방법: {'네이버' if args.naver_only else 'FDR → 네이버 fallback'}\n")

    total_new = 0
    for i, code in enumerate(codes, 1):
        annual_cnt = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE stock_code=? AND is_annual=1",
            (code,)).fetchone()[0]

        print(f"[{i:3d}/{len(codes)}] {code}  연간:{annual_cnt}건", end="  ")

        if annual_cnt >= 5 and not args.naver_only:
            print("스킵 (충분)")
            continue

        # FDR 시도
        saved = 0
        if not args.naver_only:
            saved = collect_fdr(conn, code)

        # 네이버 fallback
        if saved == 0:
            saved = collect_naver(conn, code)
            if saved > 0:
                print(f"네이버 {saved}건 저장 ✅")
            else:
                print("데이터 없음")
        else:
            print(f"FDR {saved}건 저장 ✅")

        total_new += saved
        time.sleep(0.5)

    print(f"\n완료: 총 {total_new}건 신규 저장")

    # 결과 요약
    print("\n── 수집 후 현황 ──")
    for code in codes:
        annual = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE stock_code=? AND is_annual=1",
            (code,)).fetchone()[0]
        quarter = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE stock_code=? AND is_annual=0",
            (code,)).fetchone()[0]
        status = "✅" if annual >= 3 else "⚠️ " if annual > 0 else "❌"
        print(f"  {status} {code}: 연간 {annual}건 / 분기 {quarter}건")

    conn.close()


if __name__ == "__main__":
    main()
