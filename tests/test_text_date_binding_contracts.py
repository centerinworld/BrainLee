"""portfolio_tx.tx_date(TEXT) vs datetime 바인드 — 결함 D 회귀 방지.

결함 D (2026-09-25 확정, planner 우선순위 P0):
  `routes/portfolio.sync_kis_executions()` 의 중복체결 방지 쿼리가
  `models.PortfolioTx.tx_date >= datetime.strptime(today_str, "%Y-%m-%d")` 로
  비교한다. `models.py:137` 은 `Column(DateTime)` 이지만 라이브 PostgreSQL 컬럼은
  **text** 다(information_schema 실측). SQLAlchemy 는 datetime 을 timestamp 로
  렌더하고, PG 는 INSERT 시 assignment cast(timestamp→text)로 통과시키지만
  **비교식에는 implicit cast 가 없어**

      operator does not exist: text >= timestamp without time zone

  (psycopg.errors.UndefinedFunction) 을 낸다. 그 예외는 같은 함수의
  `except Exception: db.rollback(); return 0` 이 삼키고 호출자(`장마감`)는
  "KIS 체결 0건 동기화" 로 정상 기록한다 → KIS 실체결이 있는 날에는
  portfolio / portfolio_tx 가 갱신되지 않는다.

수정 방향: 저장값이 모두 ISO 접두(`YYYY-MM-DD…`)이므로 **문자열 비교 = 시간순 비교**.
연산자 우변은 datetime 이 아니라 str 이어야 한다.
"""
from __future__ import annotations

import unittest

import models
from routes.portfolio import kis_tx_dup_date_floor


class TextDateBindingContractTests(unittest.TestCase):
    def test_predicate_binds_a_string_not_a_datetime(self) -> None:
        expr = kis_tx_dup_date_floor("2026-09-25")
        return_type = type(expr.right.value).__name__
        self.assertIsInstance(
            expr.right.value,
            str,
            f"tx_date(text) 비교의 우변은 str 이어야 한다 — 현재 {return_type}. "
            "datetime 이면 PG 가 UndefinedFunction 을 낸다.",
        )
        self.assertEqual(expr.right.value, "2026-09-25")

    def test_live_column_type_is_text(self) -> None:
        """모델은 `Column(DateTime)` 이지만 라이브 컬럼은 text — 이 불일치가 결함 D 의 뿌리다."""
        from db_compat import connect_primary_db

        try:
            conn = connect_primary_db(timeout=10)
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"라이브 PostgreSQL 접속 불가: {exc}")
        try:
            data_type = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='portfolio_tx' "
                "AND column_name='tx_date'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(data_type, "text")

    def test_source_does_not_compare_with_strptime(self) -> None:
        """소스 레벨 가드 — 같은 패턴이 다시 들어오지 못하게 한다."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "routes" / "portfolio.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "PortfolioTx.tx_date   >=",
            source,
            "tx_date 비교가 다시 datetime 좌변/우변 패턴으로 돌아갔다",
        )


class LiveTextDateQueryTests(unittest.TestCase):
    """라이브 PG 읽기전용 실행 — 수정 전에는 UndefinedFunction 으로 RED."""

    def test_query_against_live_postgres_does_not_raise(self) -> None:
        try:
            from database import SessionLocal
        except Exception as exc:  # pragma: no cover - 환경 의존
            self.skipTest(f"database 세션을 열 수 없음: {exc}")

        db = SessionLocal()
        try:
            count = (
                db.query(models.PortfolioTx)
                .filter(kis_tx_dup_date_floor("2026-09-25"))
                .count()
            )
            # 중복체결 방지 쿼리 전체 형태 (조회만, 쓰기 없음)
            db.query(models.PortfolioTx).filter(
                models.PortfolioTx.stock_code == "005930",
                kis_tx_dup_date_floor("2026-09-25"),
                models.PortfolioTx.quantity == 1.0,
                models.PortfolioTx.price == 1.0,
                models.PortfolioTx.tx_type == "buy",
                models.PortfolioTx.memo == "KIS_000000",
            ).first()
        except Exception as exc:  # pragma: no cover - 연결 실패는 skip
            if "could not connect" in str(exc).lower():
                self.skipTest(f"라이브 PostgreSQL 접속 불가: {exc}")
            raise
        finally:
            db.rollback()
            db.close()
        self.assertGreaterEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
