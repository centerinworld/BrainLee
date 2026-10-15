"""2026-10-11 미러 매수 보류/재시도 + 잔액기반 수량 정책 회귀 (라이브 매매·DB 쓰기 0).

planner 확정 사항
-----------------
* 현금 부족 409 는 **종목·전략·편입일 기준 보류**하고, **잔액 또는 후보 가격이 바뀔 때만** 재시도한다
  (예전엔 매 폴링마다 같은 2건을 재전송해 `[미러] … fail:2` 가 30분마다 무한 반복).
* 수량 정책 ①: 목표 = 1,000만원 예산이되 **잔액 기반 최대가능수량**으로 제한.
  `max_affordable_quantity == 0` 이면 **0주 주문을 만들지 않고 보류**.
* 부분 체결은 원래 목표수량·제한사유를 주문에 실어 보존한다(성과 왜곡 감사 가능).

방법: `peak_monitor.api_get/api_post` 를 **가짜 서버**로 바꾼다. 그 서버는 실제 `virtual_cash_math`
산술로 409/200 을 판정하므로 계약(잔액·최대가능수량·사유)까지 함께 검증된다. DB 접속 없음.

실행:
    ./venv/bin/python -m pytest tests/test_mirror_insufficient_cash_hold_20261011.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import peak_monitor as peak  # noqa: E402
from virtual_cash_math import (  # noqa: E402
    LIMIT_INSUFFICIENT_CASH,
    OVERDRAFT_TOLERANCE_KRW,
    max_affordable_quantity,
    projected_balance,
    required_cash,
)

STRATEGY = "momentum"          # → 계좌 키 momentum_mirror
KEY = STRATEGY + peak.MIRROR_SUFFIX
LIVE_CASH = 9_175_022.0        # 2026-10-11 실측 잔액
PRICE = 45_950.0               # 로그 결손 -802,609.25 를 만든 가격
TARGET_QTY = 217               # calc_quantity(45,950) == 10,000,000 // 45,950
NOW = "2026-10-11 11:53"


class _FakeServer:
    """`trend_buy` 계약만 흉내내는 가짜 서버 — 판정은 실제 산술 모듈을 쓴다."""

    def __init__(self, cash: float, *, active=None, site=None, forced_reason: str | None = None, hard_error: bool = False):
        self.cash = float(cash)
        self.active = list(active or [])
        self.site = site or []
        self.forced_reason = forced_reason
        self.hard_error = hard_error
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str):
        if path.startswith("/api/trend/holdings"):
            return list(self.active)
        if path == "/api/trend/performance":
            return {"strategies": {KEY: {"cash": round(self.cash), "initial_cash": 100_000_000}}}
        return None

    def post(self, path: str, data: dict):
        self.calls.append((path, dict(data)))
        if path != "/api/trend/buy":
            peak._LAST_POST_STATUS[path] = 200
            peak._LAST_POST_BODY[path] = "{}"
            return {"status": "ok"}

        if self.forced_reason:
            return self._reject(path, reason=self.forced_reason)
        if self.hard_error:
            peak._LAST_POST_STATUS[path] = 500
            peak._LAST_POST_BODY[path] = "Internal Server Error"
            return None

        qty, price = int(data.get("quantity") or 0), float(data.get("buy_price") or 0)
        if projected_balance(self.cash, qty, price) < OVERDRAFT_TOLERANCE_KRW:
            return self._reject(path, reason=LIMIT_INSUFFICIENT_CASH, qty=qty, price=price)
        self.cash = self.cash - required_cash(qty, price)
        self.active.append({"stock_name": data["stock_name"], "is_active": 1})
        peak._LAST_POST_STATUS[path] = 200
        peak._LAST_POST_BODY[path] = "{}"
        return {
            "status": "ok", "stock_name": data["stock_name"], "filled_quantity": qty,
            "target_quantity": data.get("target_quantity"), "limit_reason": data.get("limit_reason") or None,
        }

    def _reject(self, path: str, *, reason: str, qty: int = 0, price: float = 0.0):
        body = {
            "detail": {
                "message": "가상매수 현금 부족" if reason == LIMIT_INSUFFICIENT_CASH else "가상매수 위험게이트 차단",
                "decision": "BLOCKED_CASH" if reason == LIMIT_INSUFFICIENT_CASH else "BLOCKED_RISK",
                "reasons": [reason],
                "balance": round(self.cash, 2),
                "quantity": qty,
                "max_affordable_quantity": max_affordable_quantity(self.cash, price) if price else 0,
            }
        }
        peak._LAST_POST_STATUS[path] = 409
        peak._LAST_POST_BODY[path] = json.dumps(body, ensure_ascii=False)
        return None

    # --- 조회 편의
    def buy_payloads(self) -> list[dict]:
        return [data for path, data in self.calls if path == "/api/trend/buy"]

    def buy_calls(self) -> int:
        return len(self.buy_payloads())


def _site(name: str = "신규종목", *, price: float = PRICE, entry_date: str = "2026-10-08") -> dict:
    return {
        "name": name, "stock_code": "123456", "sector": "", "sector_score": 0,
        "current_price": price, "buy_price": price, "entry_date": entry_date, "hold_days": 0,
    }


class MirrorHoldTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # 모듈 전역 상태(보류/재시도)는 테스트 간에 새로 시작한다.
        peak._MIRROR_BUY_HOLDS.clear()
        peak._MIRROR_HOLD_LOGGED.clear()
        peak._EXIT_MISS_COUNTS.clear()
        peak._LAST_POST_STATUS.clear()
        peak._LAST_POST_BODY.clear()
        self._real_get, self._real_post = peak.api_get, peak.api_post
        peak.api_post_background = lambda path, data: None  # 배경 갱신은 이 테스트의 관심사가 아니다

    def tearDown(self) -> None:
        peak.api_get, peak.api_post = self._real_get, self._real_post

    def _install(self, server: _FakeServer) -> _FakeServer:
        peak.api_get = server.get
        peak.api_post = server.post
        return server


class HoldAndRetryTests(MirrorHoldTestCase):
    def test_insufficient_cash_is_held_and_not_reposted_until_state_changes(self) -> None:
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))

        first = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(first["held"], 1)
        self.assertEqual(first["fail"], 0, "보류를 실패로 세면 매 회차 재시도하게 된다")
        self.assertEqual(server.buy_calls(), 1)
        hold_key = peak.mirror_hold_key(KEY, "신규종목", "2026-10-08")
        self.assertIn(hold_key, peak._MIRROR_BUY_HOLDS)
        self.assertEqual(peak._MIRROR_BUY_HOLDS[hold_key]["reason"], LIMIT_INSUFFICIENT_CASH)

        # 2·3회차: 잔액·가격 그대로 → **주문을 아예 보내지 않는다**
        for _ in range(2):
            again = peak.sync_mirror(STRATEGY, server.site, [], NOW)
            self.assertEqual(again["held"], 1)
        self.assertEqual(server.buy_calls(), 1, "잔액·가격이 그대로인데 재시도했다")

    def test_retry_happens_when_the_balance_changes(self) -> None:
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))
        peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(server.buy_calls(), 1)

        server.forced_reason = None
        server.cash = LIVE_CASH + 5_000_000  # 잔액 증가 → 재시도 허용
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(result["bought"], 1, "잔액이 바뀌었는데 재시도하지 않았다")
        self.assertEqual(server.buy_calls(), 2)
        self.assertNotIn(peak.mirror_hold_key(KEY, "신규종목", "2026-10-08"), peak._MIRROR_BUY_HOLDS, "성공 후 보류가 남았다")

    def test_retry_happens_when_the_candidate_price_changes(self) -> None:
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))
        peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(server.buy_calls(), 1)

        server.forced_reason = None
        server.site = [_site(price=44_250.0)]  # 후보 가격 변경 → 재시도 허용
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(result["bought"], 1)
        self.assertEqual(server.buy_calls(), 2)

    def test_hold_is_keyed_by_entry_date(self) -> None:
        """같은 종목이라도 편입일이 바뀌면 새 편입이므로 재시도한다."""
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))
        peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(server.buy_calls(), 1)

        server.forced_reason = None
        server.site = [_site(entry_date="2026-10-10")]
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(result["bought"], 1)
        self.assertEqual(server.buy_calls(), 2)

    def test_other_409_reasons_keep_the_old_fail_and_retry_behaviour(self) -> None:
        """shadow_strategy 같은 위험게이트 거부는 보류가 아니라 '실패' — 다음 회차에 다시 시도한다."""
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason="shadow_strategy"))
        first = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(first["fail"], 1)
        self.assertEqual(first["held"], 0)
        self.assertEqual(peak._MIRROR_BUY_HOLDS, {})
        second = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(second["fail"], 1)
        self.assertEqual(server.buy_calls(), 2, "위험게이트 거부까지 보류하면 안 된다")

    def test_a_hard_error_is_not_treated_as_a_hold(self) -> None:
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], hard_error=True))
        first = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(first["fail"], 1)
        self.assertEqual(peak._MIRROR_BUY_HOLDS, {})
        peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(server.buy_calls(), 2)

    def test_hold_is_purged_when_the_stock_leaves_the_site(self) -> None:
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))
        peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(len(peak._MIRROR_BUY_HOLDS), 1)
        peak.sync_mirror(STRATEGY, [], [], NOW)
        self.assertEqual(peak._MIRROR_BUY_HOLDS, {}, "편입이 사라진 종목의 보류가 남았다")


class BalanceBasedSizingTests(MirrorHoldTestCase):
    def test_clamped_order_carries_target_quantity_and_limit_reason(self) -> None:
        server = self._install(_FakeServer(5_000_000.0, site=[_site()]))
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)

        self.assertEqual(result["bought"], 1)
        payload = server.buy_payloads()[0]
        affordable = max_affordable_quantity(5_000_000.0, PRICE)
        self.assertEqual(payload["target_quantity"], TARGET_QTY, "원래 목표수량이 사라졌다")
        self.assertEqual(payload["quantity"], affordable)
        self.assertEqual(payload["limit_reason"], LIMIT_INSUFFICIENT_CASH)
        self.assertLess(payload["quantity"], payload["target_quantity"])

    def test_full_size_order_has_no_limit_reason(self) -> None:
        server = self._install(_FakeServer(100_000_000.0, site=[_site()]))
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(result["bought"], 1)
        payload = server.buy_payloads()[0]
        self.assertEqual(payload["quantity"], TARGET_QTY)
        self.assertEqual(payload["target_quantity"], TARGET_QTY)
        self.assertEqual(payload["limit_reason"], "")

    def test_zero_affordable_quantity_holds_without_creating_a_zero_share_order(self) -> None:
        server = self._install(_FakeServer(10_000.0, site=[_site()]))  # 1주(45,950)도 못 산다
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)

        self.assertEqual(server.buy_calls(), 0, "0주 주문을 서버로 보냈다")
        self.assertEqual(result["held"], 1)
        self.assertEqual(result["fail"], 0)
        hold = peak._MIRROR_BUY_HOLDS[peak.mirror_hold_key(KEY, "신규종목", "2026-10-08")]
        self.assertEqual(hold["target_quantity"], TARGET_QTY)
        self.assertEqual(hold["affordable_quantity"], 0)
        self.assertEqual(hold["reason"], LIMIT_INSUFFICIENT_CASH)

        # 잔액이 늘면 다시 시도해 정상 체결된다.
        server.cash = 100_000_000.0
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)
        self.assertEqual(result["bought"], 1)
        self.assertEqual(server.buy_payloads()[-1]["quantity"], TARGET_QTY)

    def test_two_new_holdings_in_one_cycle_share_the_remaining_cash(self) -> None:
        site = [_site("A"), _site("B")]
        server = self._install(_FakeServer(15_000_000.0, site=site))
        result = peak.sync_mirror(STRATEGY, site, [], NOW)

        self.assertEqual(result["bought"], 2)
        first, second = server.buy_payloads()
        self.assertEqual(first["quantity"], TARGET_QTY, "첫 주문은 예산을 다 쓸 수 있어야 한다")
        self.assertEqual(first["limit_reason"], "")
        remaining = 15_000_000.0 - required_cash(TARGET_QTY, PRICE)
        self.assertEqual(second["quantity"], max_affordable_quantity(remaining, PRICE))
        self.assertEqual(second["target_quantity"], TARGET_QTY)
        self.assertEqual(second["limit_reason"], LIMIT_INSUFFICIENT_CASH, "두 번째 주문이 남은 현금을 넘겼다")

    def test_unknown_balance_falls_back_to_the_target_and_lets_the_server_decide(self) -> None:
        """잔액 조회가 실패하면 목표수량으로 보내고, 판정은 서버(원장)가 fail-closed 로 내린다."""
        server = self._install(_FakeServer(LIVE_CASH, site=[_site()], forced_reason=LIMIT_INSUFFICIENT_CASH))

        def get_without_performance(path):
            return None if path == "/api/trend/performance" else server.get(path)

        peak.api_get = get_without_performance
        result = peak.sync_mirror(STRATEGY, server.site, [], NOW)

        self.assertEqual(server.buy_calls(), 1)
        self.assertEqual(server.buy_payloads()[0]["quantity"], TARGET_QTY)
        self.assertEqual(result["held"], 1)
        hold = peak._MIRROR_BUY_HOLDS[peak.mirror_hold_key(KEY, "신규종목", "2026-10-08")]
        self.assertEqual(hold["balance"], LIVE_CASH, "서버가 준 잔액을 기록해야 한다")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
