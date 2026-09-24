import json
import unittest

from collectors.hk_southbound_flow_collector import parse_hkex_southbound_js


def _market(name, buy, sell):
    return {
        "date": "2026-09-08",
        "market": name,
        "tradingDay": 1,
        "content": [{
            "style": 1,
            "table": {
                "schema": [["Total Turnover", "Buy Turnover", "Sell Turnover"]],
                "tr": [
                    {"td": [["0"]]},
                    {"td": [[buy]]},
                    {"td": [[sell]]},
                ],
            },
        }],
    }


class HkSouthboundFlowCollectorTests(unittest.TestCase):
    def test_sums_shanghai_and_shenzhen_net_buy(self):
        payload = "tabData = " + json.dumps([
            _market("SSE Southbound", "31,027.95", "27,571.05"),
            _market("SZSE Southbound", "17,431.27", "14,760.35"),
        ]) + ";"
        date, value = parse_hkex_southbound_js(payload)
        self.assertEqual(date, "2026-09-08")
        self.assertAlmostEqual(value, 6127.82, places=2)

    def test_rejects_partial_or_non_js_payload(self):
        partial = "tabData = " + json.dumps([_market("SSE Southbound", "2", "1")]) + ";"
        self.assertEqual(parse_hkex_southbound_js(partial), (None, None))
        self.assertEqual(parse_hkex_southbound_js("<html>blocked</html>"), (None, None))


if __name__ == "__main__":
    unittest.main()
