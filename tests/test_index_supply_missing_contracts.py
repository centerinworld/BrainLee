"""지수 수급 결측(None) vs 실제 0 — 결함 A 회귀 방지.

결함 A (2026-09-25 확정, planner 우선순위 P1):
  `^KS11`/`^KQ11` 의 `inst_net_buy`/`frn_net_buy`(+`_amt` 2컬럼)이 2026-09-11~09-23
  **9거래일 연속 0.0** 으로 저장돼 있었다(마지막 정상값 2026-09-10). 원인은
  `data_collector.collect_macro_data()` 가 `supply`(KIS 지수 수급)가 None 일 때
  `inst = supply[...] if supply else 0.0` 으로 **결측을 0 으로 바꿔** ingest 에 POST 한 것.
  같은 기간 원장(`collection_job_runs`)은 `야간배치`/`장중수급` 모두 success 였다.

  표출도 같은 문제를 겹으로 갖고 있었다: `routes/market_indicators.py` 의
  `index-investor` 가 SQL `SUM(COALESCE(x,0))` + `round(amt or qty or 0)` 로
  NULL 을 0 으로 마스킹해, 저장값을 NULL 로 복원해도 화면은 계속 0 이었다.

⚠️ 범위: 이 파일은 **코드 계약**만 검증한다. 이미 저장된 9거래일 0.0 을 NULL 로
되돌리는 적재(라이브 쓰기)는 별도 승인 대상이며 여기서 수행하지 않는다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from data_collector import index_supply_values
from routes.market_indicators import _pick_supply_amount

RUNTIME = Path(__file__).resolve().parent.parent


class IndexSupplyMissingContractTests(unittest.TestCase):
    def test_missing_supply_is_none_not_zero(self) -> None:
        inst, frn, ind, have_supply = index_supply_values(None)
        self.assertIsNone(inst)
        self.assertIsNone(frn)
        self.assertIsNone(ind)
        self.assertFalse(have_supply)

    def test_real_zero_supply_is_preserved_as_zero(self) -> None:
        """실제 0(순매수 0)은 0 으로 남아야 한다 — 결측과 구분."""
        inst, frn, ind, have_supply = index_supply_values(
            {"inst_net_buy": 0.0, "frn_net_buy": 0.0}
        )
        self.assertEqual((inst, frn), (0.0, 0.0))
        self.assertTrue(have_supply)
        self.assertEqual(ind, 0.0)

    def test_supply_values_pass_through_and_ind_is_derived(self) -> None:
        inst, frn, ind, have_supply = index_supply_values(
            {"inst_net_buy": -6655.0, "frn_net_buy": -9620.0}
        )
        self.assertEqual((inst, frn), (-6655.0, -9620.0))
        self.assertEqual(ind, 16275.0)  # -(inst+frn)
        self.assertTrue(have_supply)

    def test_explicit_ind_value_wins_over_derived(self) -> None:
        _, _, ind, _ = index_supply_values(
            {"inst_net_buy": 100.0, "frn_net_buy": 200.0, "ind_net_buy": -999.0}
        )
        self.assertEqual(ind, -999.0)

    def test_source_does_not_coerce_missing_supply_to_zero(self) -> None:
        source = (RUNTIME / "data_collector.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "if supply else 0.0",
            source,
            "결측을 0.0 으로 바꾸는 표현이 다시 들어왔다 (결함 A 재발)",
        )


class IndexInvestorDisplayContractTests(unittest.TestCase):
    def test_both_null_means_missing_not_zero(self) -> None:
        self.assertIsNone(_pick_supply_amount(None, None))

    def test_both_real_zero_is_zero(self) -> None:
        self.assertEqual(_pick_supply_amount(0.0, 0.0), 0)

    def test_amount_preferred_then_quantity(self) -> None:
        self.assertEqual(_pick_supply_amount(12.4, 5.0), 12)
        self.assertEqual(_pick_supply_amount(0.0, 5.0), 5)
        self.assertEqual(_pick_supply_amount(None, 5.0), 5)
        self.assertEqual(_pick_supply_amount(12.4, None), 12)

    def test_live_index_investor_endpoint_shape(self) -> None:
        """라이브 PG 읽기전용 — 응답이 int/None 만 담고 예외가 없어야 한다."""
        from routes.market_indicators import get_index_investor

        try:
            result = get_index_investor(days=20)
        except Exception as exc:  # pragma: no cover - 연결 실패는 skip
            if "could not connect" in str(exc).lower():
                self.skipTest(f"라이브 PostgreSQL 접속 불가: {exc}")
            raise
        self.assertIn("KOSPI", result)
        self.assertIn("KOSDAQ", result)
        for market, rows in result.items():
            for row in rows:
                for key in ("inst_amt", "frn_amt"):
                    self.assertIn(row[key] is None or isinstance(row[key], int), (True,), market)
                    self.assertFalse(isinstance(row[key], float), f"{market} {key} 가 float")


if __name__ == "__main__":
    unittest.main()
