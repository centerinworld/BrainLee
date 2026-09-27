import unittest

from services import data_integrity_scan as scan


class FakeConn:
    """services.data_integrity_scan은 psycopg 연결의 .execute(sql).fetchone()만
    쓴다 - 실제 Postgres 없이 어떤 SQL 텍스트가 나가는지만 검증한다."""

    def __init__(self, answers):
        self.answers = answers  # list of return values, popped in call order
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        value = self.answers.pop(0)
        return _Row(value)


class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class DataIntegrityScanTests(unittest.TestCase):
    def test_compact_date_column_compares_against_compact_now_expression(self):
        """2026-09-15 발견: bas_dt 같은 "YYYYMMDD"(구분자 없음) 컬럼을 ISO 형식
        기준값과 그냥 문자열 비교하면 모든 과거 행이 거짓 양성으로 '미래'로
        잡힌다(실측: stock_price_daily 853,366행 중 193,805건 오탐). 압축 형식
        컬럼은 압축 형식 기준값(to_char(...,'YYYYMMDD'))과 비교해야 한다."""
        conn = FakeConn([1, "20260101", 0, 0, 0])
        spec = next(s for s in scan.PRICE_TABLES if s["table"] == "stock_price_daily")
        scan.scan_price_table(conn, spec)
        future_check_sql = conn.executed[-1]
        self.assertIn("to_char(CURRENT_DATE,'YYYYMMDD')", future_check_sql)
        self.assertNotIn("CURRENT_DATE::text", future_check_sql)

    def test_iso_date_column_compares_against_iso_now_expression(self):
        """압축 형식 수정이 ISO 형식 컬럼의 정상적인 미래 날짜 비교 기준을 깨면 안 된다."""
        conn = FakeConn([1, "2026-01-01", 0, 0, 0])
        spec = next(s for s in scan.PRICE_TABLES if s["table"] == "price_history")
        scan.scan_price_table(conn, spec)
        future_check_sql = conn.executed[-1]
        self.assertIn("CURRENT_DATE::text", future_check_sql)
        self.assertNotIn("to_char(", future_check_sql)

    def test_invalid_ohlc_count_excludes_suspension_marker_pattern(self):
        """2026-09-19 발견: 시가/고가/저가/거래량이 전부 0인 '거래정지일 마커'는
        stock_dashboard/runtime/price_integrity.invalid_ohlcv()가 이미 정상으로
        인정하는 패턴인데, 이 스캐너는 open<=0이면 무조건 결함으로 셌다(실측:
        price_history 204,241건 중 203,854건이 이 마커였음 - 진짜 결함은 39건).
        SQL이 이 패턴을 제외하도록 바뀌었는지, 그리고 이 값들 중 하나라도 NULL이면
        (거래량 포함) 여전히 결함으로 세는지(us_price_history의 진짜 결함 패턴)
        확인한다."""
        conn = FakeConn([1, "2026-01-01", 0, 0, 0])
        spec = next(s for s in scan.PRICE_TABLES if s["table"] == "price_history")
        scan.scan_price_table(conn, spec)
        invalid_ohlc_sql = conn.executed[2]
        self.assertIn("NOT (volume IS NOT NULL AND volume::float=0", invalid_ohlc_sql)
        self.assertIn("open IS NOT NULL AND open::float=0", invalid_ohlc_sql)
        self.assertIn("close IS NOT NULL AND close::float>0", invalid_ohlc_sql)

    def test_conninfo_strips_sqlalchemy_driver_suffix(self):
        """config.py는 SQLAlchemy 스타일("postgresql+psycopg://...")을 쓰는데
        psycopg는 순수 "postgresql://..."만 받는다 - 접미사를 벗기지 않으면
        연결 자체가 실패한다."""
        result = scan._psycopg_conninfo("postgresql+psycopg://u:p@127.0.0.1:5432/db")
        self.assertEqual(result, "postgresql://u:p@127.0.0.1:5432/db")


if __name__ == "__main__":
    unittest.main()
