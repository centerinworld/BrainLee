"""
update_sector.py — 보유종목 업종(소분류) 자동 업데이트

네이버 금융 / pykrx에서 업종 정보를 가져와
portfolio 테이블의 sector 컬럼을 업데이트합니다.

실행:
  cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate
  python3 update_sector.py
"""

import sys, sqlite3, time, requests, logging
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")


def get_sector_naver(stock_code: str) -> str | None:
    """네이버 금융에서 업종(소분류) 스크래핑."""
    try:
        url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://finance.naver.com/",
        }, timeout=10)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        # 업종 정보: "코스피 | 반도체와반도체장비" 형태
        for em in soup.select("em"):
            text = em.get_text(strip=True)
            if text in ("KOSPI", "KOSDAQ", "KONEX"):
                # 다음 형제 요소에서 업종 찾기
                parent = em.find_parent()
                if parent:
                    full_text = parent.get_text(strip=True)
                    if "|" in full_text:
                        parts = full_text.split("|")
                        if len(parts) >= 2:
                            sector = parts[1].strip()
                            if sector and len(sector) < 30:
                                return sector

        # 대안: 종목 정보 영역에서 업종 탐색
        for tag in soup.select("div.section.trade_compare th, table.tb_type1 th"):
            if "업종" in tag.get_text():
                td = tag.find_next_sibling("td")
                if td:
                    sector = td.get_text(strip=True)
                    if sector and len(sector) < 30:
                        return sector

        # 대안2: em 태그 주변 텍스트
        info_div = soup.select_one("div.wrap_company")
        if info_div:
            text = info_div.get_text(strip=True)
            for sep in ["|", "/"]:
                if sep in text:
                    parts = text.split(sep)
                    for p in parts[1:]:
                        p = p.strip()
                        if p and len(p) < 20 and not p.isdigit():
                            return p

    except Exception as e:
        logger.warning(f"[Naver] {stock_code}: {e}")
    return None


def get_sector_pykrx(stock_code: str) -> str | None:
    """pykrx로 업종 조회."""
    try:
        from pykrx import stock as pk
        sector = pk.get_market_sector_classifications("20260327", "KOSPI").get(stock_code)
        if not sector:
            sector = pk.get_market_sector_classifications("20260327", "KOSDAQ").get(stock_code)
        return sector
    except Exception as e:
        logger.warning(f"[pykrx] {stock_code}: {e}")
    return None


def get_sector_stock_meta(conn, stock_code: str) -> str | None:
    """stock_meta 테이블에서 업종 조회."""
    try:
        row = conn.execute(
            "SELECT sector FROM stock_meta WHERE stock_code=? LIMIT 1",
            (stock_code,)).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def get_sector_stock_universe(conn, stock_code: str) -> str | None:
    """stock_universe에서 업종 조회 (있는 경우)."""
    try:
        # stock_universe에 sector 컬럼이 있으면 조회
        row = conn.execute(
            "SELECT sector FROM stock_universe WHERE stock_code=? LIMIT 1",
            (stock_code,)).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def main():
    conn = sqlite3.connect(str(DB_PATH))

    # 보유 종목 중 sector가 없거나 '기타'인 종목 조회
    rows = conn.execute("""
        SELECT DISTINCT stock_code, stock_name, sector
        FROM portfolio
        WHERE quantity > 0
        AND stock_code IS NOT NULL
        AND length(stock_code) = 6
        ORDER BY stock_code
    """).fetchall()

    print(f"\n대상 종목: {len(rows)}개")
    print(f"{'종목코드':10} {'종목명':15} {'현재섹터':15} → {'새섹터'}")
    print("─" * 65)

    updated = 0
    for stock_code, stock_name, current_sector in rows:
        if not stock_code or not stock_code.isdigit():
            continue

        # 이미 의미있는 업종이 있으면 스킵
        if current_sector and current_sector not in ("기타", "미분류", "", "None"):
            print(f"{stock_code:10} {(stock_name or ''):15} {current_sector:15} → 스킵")
            continue

        # 업종 조회 (우선순위: stock_meta → stock_universe → pykrx → 네이버)
        sector = None

        sector = get_sector_stock_meta(conn, stock_code)
        if not sector:
            sector = get_sector_stock_universe(conn, stock_code)
        if not sector:
            sector = get_sector_pykrx(stock_code)
        if not sector:
            sector = get_sector_naver(stock_code)
            time.sleep(0.5)

        if sector and sector not in ("기타", "미분류", ""):
            conn.execute("""
                UPDATE portfolio SET sector=? WHERE stock_code=? AND (sector IS NULL OR sector='기타' OR sector='미분류' OR sector='')
            """, (sector, stock_code))
            conn.commit()
            print(f"{stock_code:10} {(stock_name or ''):15} {(current_sector or '기타'):15} → ✅ {sector}")
            updated += 1
        else:
            print(f"{stock_code:10} {(stock_name or ''):15} {(current_sector or '기타'):15} → ❌ 업종 불명")

        time.sleep(0.3)

    print(f"\n완료: {updated}개 업종 업데이트")

    # 결과 확인
    print("\n── 업데이트 후 현황 ──")
    result = conn.execute("""
        SELECT stock_code, stock_name, sector
        FROM portfolio WHERE quantity > 0
        ORDER BY sector, stock_code
    """).fetchall()
    for code, name, sec in result:
        print(f"  {code} {(name or ''):15} → {sec or '기타'}")

    conn.close()


if __name__ == "__main__":
    main()
