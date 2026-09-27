"""
scripts/ops/seed_new_radar_sectors_20260927.py

「섹터 로테이션」(routes/sector_rotation.py SECTOR_GROUPS)에는 이미 있었지만 「섹터 분류」
(routes/market_radar.py, radar_sector_override 기반)에는 없던 3개 세분류를 신규 최상위
섹터로 추가한다: 기판/패키지, 화장품/뷰티, 의료기기/미용.

- 국내 종목: 섹터 로테이션의 SECTOR_GROUPS 코드를 그대로 재사용(신규 리서치 없음, 이미 검증됨).
- 해외 종목: 2026-09-27 신규 조사(글로벌 동종업계 대표주), us_market.db에 yfinance로 3개월치
  가격 적재 완료(별도 커밋 스크립트 없음 — 1회성 fetch, 이 스크립트는 DB row만 추가).

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/seed_new_radar_sectors_20260927.py
재실행해도 안전(멱등) — 먼저 해당 lv0의 기존 행을 지우고 다시 넣는다.
"""
from __future__ import annotations

from db_compat import connect_primary_db

# (company_name, ticker, country_raw, country_flag, lv1)
ROWS: dict[str, list[tuple]] = {
    "기판/패키지": [
        ("심텍", "222800.KQ", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("대덕전자", "353200.KS", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("ISC", "095340.KQ", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("코리아써키트", "007810.KS", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("이수페타시스", "007660.KS", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("해성디에스", "195870.KS", "KR", "🇰🇷", "패키지기판/OSAT"),
        ("TTM Technologies", "TTMI", "US", "🇺🇸", "패키지기판/OSAT"),
        ("Jabil", "JBL", "US", "🇺🇸", "패키지기판/OSAT"),
        ("Amkor Technology", "AMKR", "US", "🇺🇸", "패키지기판/OSAT"),
        ("ASE Technology", "ASX", "TW", "🇹🇼", "패키지기판/OSAT"),
        ("Celestica", "CLS", "CA", "🇨🇦", "패키지기판/OSAT"),
    ],
    "화장품/뷰티": [
        ("LG생활건강", "051900.KS", "KR", "🇰🇷", "화장품 제조/브랜드"),
        ("아모레퍼시픽", "090430.KS", "KR", "🇰🇷", "화장품 제조/브랜드"),
        ("코스메카코리아", "241710.KQ", "KR", "🇰🇷", "화장품 ODM/OEM"),
        ("한국콜마", "161890.KS", "KR", "🇰🇷", "화장품 ODM/OEM"),
        ("에이블씨엔씨", "078520.KQ", "KR", "🇰🇷", "화장품 제조/브랜드"),
        ("코리아나", "027050.KQ", "KR", "🇰🇷", "화장품 제조/브랜드"),
        ("한국화장품제조", "003350.KS", "KR", "🇰🇷", "화장품 ODM/OEM"),
        ("Estee Lauder", "EL", "US", "🇺🇸", "화장품 제조/브랜드"),
        ("Coty", "COTY", "US", "🇺🇸", "화장품 제조/브랜드"),
        ("e.l.f. Beauty", "ELF", "US", "🇺🇸", "화장품 제조/브랜드"),
        ("Inter Parfums", "IPAR", "US", "🇺🇸", "화장품 제조/브랜드"),
        ("Ulta Beauty", "ULTA", "US", "🇺🇸", "화장품 유통"),
    ],
    "의료기기/미용": [
        ("클래시스", "214150.KQ", "KR", "🇰🇷", "미용의료기기"),
        ("파마리서치", "214450.KQ", "KR", "🇰🇷", "미용의료기기"),
        ("에이피알", "278470.KS", "KR", "🇰🇷", "미용의료기기"),
        ("원텍", "336570.KQ", "KR", "🇰🇷", "미용의료기기"),
        ("하이로닉", "149980.KQ", "KR", "🇰🇷", "미용의료기기"),
        ("휴젤", "145020.KQ", "KR", "🇰🇷", "미용의료기기"),
        ("Intuitive Surgical", "ISRG", "US", "🇺🇸", "수술로봇/의료기기"),
        ("Stryker", "SYK", "US", "🇺🇸", "수술로봇/의료기기"),
        ("Medtronic", "MDT", "US", "🇺🇸", "수술로봇/의료기기"),
        ("Edwards Lifesciences", "EW", "US", "🇺🇸", "수술로봇/의료기기"),
        ("Abbott Laboratories", "ABT", "US", "🇺🇸", "수술로봇/의료기기"),
        ("InMode", "INMD", "US", "🇺🇸", "미용의료기기"),
    ],
}


def main() -> None:
    conn = connect_primary_db(timeout=30)
    try:
        # id 컬럼에 시퀀스/DEFAULT가 없다(2026-09-24 PG 이관 시 DEFAULT 유실 이슈와 같은 부류) — 직접 계산.
        next_id = (conn.execute("SELECT COALESCE(MAX(id), 0) FROM radar_sector_override").fetchone()[0] or 0) + 1

        total = 0
        for lv0, rows in ROWS.items():
            conn.execute("DELETE FROM radar_sector_override WHERE lv0=?", (lv0,))
            for i, (company_name, ticker, country_raw, country_flag, lv1) in enumerate(rows):
                conn.execute(
                    """INSERT INTO radar_sector_override
                       (id, lv0, lv1, lv2, company_name, ticker, country_raw, country_flag, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (next_id, lv0, lv1, "", company_name, ticker, country_raw, country_flag, (i + 1) * 10),
                )
                next_id += 1
                total += 1
            print(f"{lv0}: {len(rows)}건 적재")
        conn.commit()
        print(f"총 {total}건 적재 완료")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
