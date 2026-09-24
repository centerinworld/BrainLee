"""price_history 데이터 무결성 가드 회귀 테스트.

P0 티켓의 세 가지 보증을 검증한다:
  1. close<=0 행은 DB 레벨(트리거)에서 신규 INSERT/UPDATE가 fail-closed로 거부된다.
  2. 종목코드·날짜 upsert 시 기존 수급값(inst/frn/ind + 3개 금액)을 0·NULL로 덮어쓰지 않는다.
  3. 지수·선물·통화 등 비(非)KR 일반종목 코드는 매수후보/스크리너에 섞이지 않도록
     공통 판별자(is_kr_equity_code)가 거른다.
"""
import unittest

from security_master import is_kr_equity_code
from crud import merge_supply_fields
from config import IS_POSTGRES


class EquityCodeFilterTest(unittest.TestCase):
    def test_accepts_six_char_numeric_and_preferred(self):
        self.assertTrue(is_kr_equity_code("005930"))
        self.assertTrue(is_kr_equity_code("00088K"))  # 우선주(문자 포함)
        self.assertTrue(is_kr_equity_code("123456"))

    def test_rejects_index_futures_currency_and_blank(self):
        self.assertFalse(is_kr_equity_code("^KS11"))
        self.assertFalse(is_kr_equity_code("^KQ11"))
        self.assertFalse(is_kr_equity_code("GC=F"))
        self.assertFalse(is_kr_equity_code("CL=F"))
        self.assertFalse(is_kr_equity_code("USDKRW=X"))
        self.assertFalse(is_kr_equity_code("JPYKRW=X"))
        self.assertFalse(is_kr_equity_code("^GSPC"))
        self.assertFalse(is_kr_equity_code("2YY=F"))
        self.assertFalse(is_kr_equity_code(""))
        self.assertFalse(is_kr_equity_code("00593"))   # 5자리
        self.assertFalse(is_kr_equity_code("0059300"))  # 7자리


class SupplyMergeTest(unittest.TestCase):
    def test_backfills_blank_supply_from_existing(self):
        # 1분 단위 장중 가격 갱신이 수급=0으로 들어와도 기존 6개 필드를 보존한다.
        new = {"inst_net_buy": 0, "frn_net_buy": 0, "ind_net_buy": 0,
               "inst_net_buy_amt": 0, "frn_net_buy_amt": 0, "ind_net_buy_amt": 0}
        existing = (100, 200, 300, 10, 20, 30)
        out = merge_supply_fields(new, existing)
        self.assertEqual(out["inst_net_buy"], 100)
        self.assertEqual(out["frn_net_buy"], 200)
        self.assertEqual(out["ind_net_buy"], 300)
        self.assertEqual(out["inst_net_buy_amt"], 10)
        self.assertEqual(out["frn_net_buy_amt"], 20)
        self.assertEqual(out["ind_net_buy_amt"], 30)

    def test_keeps_nonzero_incoming_value(self):
        new = {"inst_net_buy": 50, "frn_net_buy": 0, "ind_net_buy": None,
               "inst_net_buy_amt": 0, "frn_net_buy_amt": 0, "ind_net_buy_amt": 0}
        existing = (100, 200, 300, 10, 20, 30)
        out = merge_supply_fields(new, existing)
        self.assertEqual(out["inst_net_buy"], 50)   # 새 값 우선
        self.assertEqual(out["frn_net_buy"], 200)   # 빈 값만 보존
        self.assertEqual(out["ind_net_buy"], 300)   # None도 보존

    def test_does_not_fabricate_when_existing_is_zero(self):
        new = {"inst_net_buy": 0, "frn_net_buy": 0, "ind_net_buy": 0,
               "inst_net_buy_amt": 0, "frn_net_buy_amt": 0, "ind_net_buy_amt": 0}
        existing = (0, 0, 0, 0, 0, 0)
        out = merge_supply_fields(new, existing)
        self.assertEqual(out["inst_net_buy"], 0)
        self.assertEqual(out["ind_net_buy_amt"], 0)


@unittest.skipUnless(IS_POSTGRES, "PostgreSQL 전용 트리거 검증")
class CloseGuardTriggerTest(unittest.TestCase):
    """close<=0 거부가 신규 트리거 함수에서 fail-closed로 동작하는지 검증.

    프로덕션 price_history에 락을 잡지 않도록, 동일 컬럼 형태의 임시 테이블에
    같은 함수/트리거를 걸고 검증한 뒤 전체를 ROLLBACK한다(아무것도 영속되지 않음).
    """

    def test_close_zero_rejected_fail_closed(self):
        from db_compat import connect_primary_db
        from price_integrity import WRITE_GUARD_FUNCTION_SQL

        conn = connect_primary_db(timeout=30)
        try:
            cur = conn._connection.cursor()
            cur.execute(
                "CREATE TEMP TABLE _guard_close_test ("
                "stock_code text, date text, open double precision, high double precision, "
                "low double precision, close double precision, volume double precision)"
            )
            cur.execute(WRITE_GUARD_FUNCTION_SQL)  # 실제 price_history를 지키는 것과 동일한 함수
            cur.execute(
                "CREATE TRIGGER _guard_close_test_trg BEFORE INSERT OR UPDATE OF open,high,low,close,volume "
                "ON _guard_close_test FOR EACH ROW EXECUTE FUNCTION guard_historical_price_write()"
            )

            # 비KR 코드(^TESTX)는 기존 히스토리 가드를 우회하므로 close<=0 가드가 유일한 방어선
            cur.execute("SAVEPOINT _g1")
            try:
                cur.execute(
                    "INSERT INTO _guard_close_test (stock_code,date,open,high,low,close,volume) "
                    "VALUES (%s,%s,0,0,0,0,0)",
                    ("^TESTX", "2001-01-02"),
                )
                self.fail("close<=0 INSERT(^TESTX)가 거부돼야 한다")
            except Exception as exc:
                self.assertIn("non-positive close", str(exc))
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT _g1")

            # 검증 플래그(app.price_basis_checked=1)가 있어도 close<=0는 거부
            # (가드가 플래그 우회보다 먼저 실행).
            cur.execute("SAVEPOINT _g2")
            try:
                cur.execute("SELECT set_config('app.price_basis_checked','1',true)")
                cur.execute(
                    "INSERT INTO _guard_close_test (stock_code,date,open,high,low,close,volume) "
                    "VALUES (%s,%s,0,0,0,0,0)",
                    ("005930", "2001-01-02"),
                )
                self.fail("검증 플래그 경로에서도 close<=0 INSERT가 거부돼야 한다")
            except Exception as exc:
                self.assertIn("non-positive close", str(exc))
            finally:
                cur.execute("ROLLBACK TO SAVEPOINT _g2")
            cur.close()
        finally:
            conn.rollback()  # 함수/임시 테이블/데이터 전부 폐기
            conn.close()


if __name__ == "__main__":
    unittest.main()
