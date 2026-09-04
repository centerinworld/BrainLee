import unittest

from hs_trade_lab.scripts.rebuild_telegram_flow_mappings import (
    HS_ALIASES,
    _hs_allowed_for_context,
)


class TelegramHsMappingTest(unittest.TestCase):
    def test_semiconductor_implant_does_not_use_dental_hs(self):
        self.assertFalse(
            _hs_allowed_for_context(
                "9021290000",
                "임플란트",
                "반도체 재료 도핑용 이온주입(임플란트 장비)",
                "수입(글로벌)",
            )
        )
        self.assertTrue(
            _hs_allowed_for_context(
                "8486203000",
                "이온주입",
                "반도체 재료 도핑용 이온주입(임플란트 장비)",
                "수입(글로벌)",
            )
        )

    def test_tank_and_ship_engine_aliases_are_separate(self):
        tank_codes = {code for code, _ in HS_ALIASES["전차와 그 밖의 장갑차량"]}
        self.assertEqual(tank_codes, {"8710001000", "8710002000"})
        self.assertFalse(any(code.startswith("8409998") for code in tank_codes))

    def test_repeated_missing_products_have_explicit_aliases(self):
        for label in (
            "임플란트",
            "피스톤식 엔진 시동용 연산 축전지",
            "ESS",
            "TC BONDER",
            "산업용원자현미경",
            "시스템반도체",
            "플래시 메모리",
            "블랭크마스크",
        ):
            self.assertIn(label, HS_ALIASES)


if __name__ == "__main__":
    unittest.main()
