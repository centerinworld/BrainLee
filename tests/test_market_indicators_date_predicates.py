"""market_indicators 날짜 술어 회귀 테스트.

배경(2026-09-23 실측): `substr(<t>.date,1,10) = ?` / `strftime('%w', date)` 형태의
술어는 PostgreSQL에서 sargable 하지 않다 — ix_price_history_6755ae263c(date) /
ix_price_history_0b57a1f65a(stock_code,date)를 사용할 수 없어 1,024만행(2.9GB)
price_history 전체를 seq scan하고, 30초 statement_timeout을 넘겨 HTTP 500을 냈다
(/api/market-indicators/investor-top·turnover-top·available-dates 재현, 로그에
`psycopg.errors.QueryCanceled: canceling statement due to statement timeout`).
프론트엔드는 `r.ok ? r.json() : null`로 500을 삼키므로 화면은 직전 값/빈 표로 남고
기준일만 새 날짜를 가리켜 "수치 불일치"처럼 보인다.

이 테스트는 (1) 비-sargable 술어가 소스로 되돌아오지 않았는지, (2) 범위 술어가
substr 술어와 정확히 같은 행을 고르는지를 라이브 PG 읽기전용 연결로 검증한다.
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import re
import sys
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "routes" / "market_indicators.py"
ROOT = MODULE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# substr(date,1,10) 뒤에 비교 연산자가 붙은 "술어"만 잡는다 (SELECT ... AS d 투영은 제외)
_NON_SARGABLE_PREDICATE = re.compile(
    r"substr\(\s*[\w]*\.?date\s*,\s*1\s*,\s*10\s*\)\s*(?:<=|>=|<>|!=|=|<|>)"
)


class StaticDatePredicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = MODULE.read_text(encoding="utf-8")

    def test_no_non_sargable_date_predicate(self) -> None:
        hits = sorted({m.group(0) for m in _NON_SARGABLE_PREDICATE.finditer(self.src)})
        self.assertEqual(hits, [], f"비-sargable 날짜 술어 재도입: {hits}")

    def test_available_dates_scan_is_index_bounded(self) -> None:
        body = self.src.split("def get_available_dates", 1)[1].split("\n@router", 1)[0]
        # 주석은 술어가 아니므로 제외하고 실제 SQL/코드만 본다.
        code = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("#"))
        self.assertIn("date >= ?", code, "available-dates에 인덱스 범위 하한이 없다")
        self.assertNotIn(
            "strftime('%w'", code, "available-dates SQL에 비-sargable 요일 필터가 남아 있다"
        )
        self.assertIn("[:limit]", code, "파이썬 요일 필터 이후 limit 절단이 없다")

    def test_day_bounds_helper(self) -> None:
        from routes.market_indicators import _day_bounds

        self.assertEqual(_day_bounds("2026-09-22"), ("2026-09-22", "2026-09-23"))
        self.assertEqual(_day_bounds("2026-09-30"), ("2026-09-30", "2026-10-01"))
        self.assertEqual(_day_bounds("2026-12-31T09:00:00"[:10]), ("2026-12-31", "2027-01-01"))
        # 파싱 불가 입력은 예외 대신 넓은 상한으로 후퇴
        self.assertEqual(_day_bounds(""), ("", " 99"))


class RangePredicateEquivalenceTests(unittest.TestCase):
    """라이브 PG(읽기전용): 범위 술어 ≡ substr 술어 — 같은 행을 고른다."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from db_compat import connect_primary_db

            cls.conn = connect_primary_db(timeout=60, readonly=True)
            cls.conn.execute("SELECT 1").fetchone()
        except Exception as exc:  # DB 미기동/미설정 시 skip
            raise unittest.SkipTest(f"live PG unavailable: {exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.conn.close()
        except Exception:
            pass

    def test_no_row_needs_the_substr_truncation(self) -> None:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE substr(date,1,10) <> date"
        ).fetchone()
        self.assertEqual(
            row[0], 0,
            "date 컬럼에 10자리가 아닌(타임스탬프) 행이 있다 — 범위 술어만으로는 축소 불가",
        )

    def test_daily_counts_match_between_predicates(self) -> None:
        # 오늘 날짜는 수집 잡이 계속 쓰는 중이므로 "완료된 거래일"만 대상으로 한다.
        today = _dt.date.today().strftime("%Y-%m-%d")
        since = (_dt.date.today() - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
        days = [
            r[0]
            for r in self.conn.execute(
                "SELECT DISTINCT substr(date,1,10) AS d FROM price_history "
                "WHERE date >= ? AND date < ? ORDER BY d DESC LIMIT 12",
                (since, today),
            ).fetchall()
        ]
        self.assertTrue(days, "최근 30일 내 완료된 price_history 거래일이 없다")
        for day in days:
            day = str(day)[:10]
            nxt = (_dt.datetime.strptime(day, "%Y-%m-%d") + _dt.timedelta(days=1)).strftime("%Y-%m-%d")
            # 한 문장 = 한 스냅샷으로 두 술어를 동시에 세어 동시쓰기 영향을 제거한다.
            row = self.conn.execute(
                "SELECT (SELECT COUNT(*) FROM price_history WHERE substr(date,1,10) = ?),"
                "       (SELECT COUNT(*) FROM price_history WHERE date >= ? AND date < ?)",
                (day, day, nxt),
            ).fetchone()
            substr_n, range_n = row[0], row[1]
            self.assertEqual(substr_n, range_n, f"{day}: substr={substr_n} range={range_n}")
            self.assertGreater(substr_n, 0, f"{day}: 행이 없다")


if __name__ == "__main__":
    unittest.main()
