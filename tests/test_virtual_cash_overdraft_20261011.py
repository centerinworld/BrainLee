"""2026-10-11 P0 재현·원인 확정 — 가상매매 "음수 현금" (읽기 전용, 라이브 쓰기 0).

라이브 현상 (peak_monitor.launchd.log / backend.launchd.log 실측)
-----------------------------------------------------------------
* `POST /api/trend/buy` → `routes/trend.py::trend_buy` → `virtual_trading_ledger.record_trade` 가
  `ValueError: virtual cash would become negative: -802609.25`(및 -787699.56) → **HTTP 500**.
* peak_monitor 는 30분 폴링마다 `[미러] momentum_mirror {'bought': 0, 'sold': 0, 'fail': 2}` 를
  반복 기록(09:58·10:28·10:58·11:23·11:53) — **같은 2건이 영원히 재시도**된다.
  `[API POST] /api/trend/buy HTTP 500` 누적 402건.

원인 (아래 테스트가 수치로 고정)
--------------------------------
`peak_monitor.sync_mirror` 는 새 편입을 **항상 `MAX_BUDGET`=1,000만원** 으로 주문한다
(`calc_quantity(price) = 10_000_000 // price`). 그런데 momentum_mirror 잔액은 2026-10-09 마지막
거래 후 **9,175,022.00원** 이라 1,000만원 포지션을 살 수 없다. 그 두 주문이 정확히 로그의 두
결손값과 일치한다:

    217주 × 45,950원 = 9,971,150 → +비용 = 9,977,631.25 → 9,175,022.00 − 9,977,631.25 = −802,609.25
    225주 × 44,250원 = 9,956,250 → +비용 = 9,962,721.56 → 9,175,022.00 − 9,962,721.56 = −787,699.56

즉 **"정상 잔액 거부"** 다 — 중복·수량·가격 계산 오류가 아니다(원장 중복 방지와 대조로 함께 고정한다).
거부를 막는 것은 옳지만, 엔드포인트에 **현금 사전 점검이 없어** 500 + 무한 재시도가 됐고, 그 결과
호출자는 "DB 저장 실패" 로만 보았다. 그래서 거절을 409 로 돌리고, 판정식을 원장과 공유한다.

실행:
    ./venv/bin/python -m pytest tests/test_virtual_cash_overdraft_20261011.py -v
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

import virtual_trading_ledger as ledger

STRATEGY = "momentum_mirror"
LIVE_BALANCE = 9_175_022.00
INITIAL_CASH = 100_000_000.0

# ── 라이브 `virtual_cash_ledger` 실측값(2026-10-11 11:5x, read-only 조회) ──────────────────
# (event_type, stock_name, quantity, price, gross_amount, balance_after, occurred_at, ref_key, holding_id)
LIVE_MOMENTUM_MIRROR_LEDGER = (
    ("buy", "한화시스템", 128, 77_600, 9_932_800, 90_060_744.00, "2026-10-08 09:24:51", "peak_trade:1059", 1),
    ("buy", "삼성전기", 6, 1_605_000, 9_630_000, 80_424_490.00, "2026-10-08 09:24:51", "peak_trade:1063", 2),
    ("buy", "로보티즈", 32, 305_500, 9_776_000, 70_642_136.00, "2026-10-08 09:24:51", "peak_trade:1065", 3),
    ("buy", "한온시스템", 2594, 3_855, 9_999_870, 60_635_768.00, "2026-10-08 09:24:51", "peak_trade:1067", 4),
    ("buy", "대한광통신", 557, 17_930, 9_987_010, 50_642_268.00, "2026-10-08 09:24:51", "peak_trade:1069", 5),
    ("buy", "고영", 276, 36_200, 9_991_200, 40_644_572.00, "2026-10-08 09:24:51", "peak_trade:1071", 6),
    ("buy", "서진시스템", 242, 41_300, 9_994_600, 30_643_476.00, "2026-10-08 09:24:51", "peak_trade:1073", 7),
    ("buy", "산일전기", 43, 231_000, 9_933_000, 20_704_020.00, "2026-10-08 09:24:51", "peak_trade:1075", 8),
    ("buy", "LS ELECTRIC", 48, 206_000, 9_888_000, 10_809_593.00, "2026-10-08 09:24:51", "peak_trade:1077", 9),
    ("buy", "한화솔루션", 276, 36_150, 9_977_400, 825_707.70, "2026-10-08 09:24:51", "peak_trade:1079", 10),
    ("sell", "LS ELECTRIC", 48, 196_700, 9_441_600, 10_242_287.00, "2026-10-09 14:11", "peak_trade:1194", 9),
    ("sell", "대한광통신", 557, 16_970, 9_452_290, 19_669_528.00, "2026-10-09 14:11", "peak_trade:1195", 5),
    ("sell", "로보티즈", 32, 297_500, 9_520_000, 29_164_300.00, "2026-10-09 14:11", "peak_trade:1196", 3),
    ("sell", "한화시스템", 128, 76_500, 9_792_000, 38_930_352.00, "2026-10-09 14:11", "peak_trade:1197", 1),
    ("buy", "엘앤씨바이오", 177, 56_300, 9_965_100, 28_958_774.00, "2026-10-09 14:14:25", "peak_trade:1198", 11),
    ("buy", "씨젠", 264, 37_850, 9_992_400, 18_959_878.00, "2026-10-09 14:14:25", "peak_trade:1199", 12),
    ("buy", "삼천당제약", 41, 238_500, 9_778_500, 9_175_022.00, "2026-10-09 14:14:25", "peak_trade:1200", 13),
)

# 로그가 보고한 두 거절 주문(잔액 9,175,022 에서 1,000만원 예산 포지션)
REJECTED_ORDERS = ((217, 45_950, -802_609.25), (225, 44_250, -787_699.56))


class OverdraftReproTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.previous_ready = ledger._SCHEMA_READY
        ledger._SCHEMA_READY = False
        self.conn.executescript(ledger.DDL)
        ledger._SCHEMA_READY = True

    def tearDown(self) -> None:
        self.conn.close()
        ledger._SCHEMA_READY = self.previous_ready

    # ---------------------------------------------------------------- 재생
    def _replay_live_ledger(self) -> list[float]:
        balances = []
        for side, name, qty, price, _gross, _after, occurred_at, ref_key, holding_id in LIVE_MOMENTUM_MIRROR_LEDGER:
            result = ledger.record_trade(
                self.conn,
                strategy=STRATEGY,
                initial_cash=INITIAL_CASH,
                side=side,
                stock_code=f"{holding_id:06d}",
                stock_name=name,
                holding_id=holding_id,
                quantity=qty,
                price=price,
                ref_key=ref_key,
                occurred_at=occurred_at,
            )
            self.assertTrue(result["inserted"], f"{ref_key} 재생 실패: {result}")
            balances.append(result["balance_after"])
        return balances

    def test_live_ledger_replays_and_reaches_the_live_balance(self) -> None:
        """라이브 원장 17행을 그대로 재생하면 저장된 잔액 연쇄가 **원 단위 이내**로 재현된다.

        수량·가격·차감액이 손상됐다면(중복/수량 오류) 이 연쇄가 깨진다 — 그래서 이 테스트가
        "중복·수량·가격 계산 오류" 가설을 배제하는 근거다. 저장 컬럼은 원 단위로 절사돼 있고,
        10-08 행들은 동일 `occurred_at`(09:24:51) 의 **일괄 백필** 산출이라 한 행에서 ~5.8원
        (0.00007%) 의 반올림 차이가 보인다 — 임계 12원으로 고정하고 실측 최대값을 함께 단언한다.
        """
        balances = self._replay_live_ledger()
        deviations = []
        for (_, _, _, _, _, stored_after, _, ref_key, _), computed in zip(LIVE_MOMENTUM_MIRROR_LEDGER, balances):
            deviation = abs(computed - stored_after)
            deviations.append((deviation, ref_key))
            self.assertLess(deviation, 12.0, f"{ref_key} 잔액 불일치 {deviation:.2f}원")
        worst = max(deviations)
        self.assertLess(worst[0], 12.0, f"최대 편차 {worst[0]:.2f}원 ({worst[1]})")
        self.assertLess(abs(balances[-1] - LIVE_BALANCE), 12.0, "최종 잔액이 라이브 값과 다르다")
        self.assertEqual(ledger.account_summary(self.conn, STRATEGY)["balance_krw"], balances[-1])

    def test_live_rows_charged_costs_within_the_configured_band(self) -> None:
        """각 매수 행의 실효 비용률(수수료+슬리피지)이 설정 범위 안이다(이중차감·미차감 배제)."""
        previous = INITIAL_CASH
        for side, _name, _qty, _price, gross, stored_after, _occurred, ref_key, _holding in LIVE_MOMENTUM_MIRROR_LEDGER:
            if side == "buy":
                implied = (previous - stored_after) / float(gross) - 1.0
                self.assertGreater(implied, 0.0001, f"{ref_key} 비용이 차감되지 않았다")
                self.assertLess(implied, 0.0030, f"{ref_key} 비용이 과다하다 ({implied:.6f})")
            previous = stored_after

    def _anchor_live_balance(self) -> float:
        """라이브 계좌 상태(잔액 9,175,022.00)로 고정 — 서버가 실제로 본 값에서 재현한다."""
        return ledger.initialize_account(self.conn, STRATEGY, LIVE_BALANCE)

    def test_the_two_live_rejections_reproduce_the_exact_logged_deficits(self) -> None:
        """로그의 두 결손값이 **그 잔액에서** 정확히 재현된다(217×45,950 / 225×44,250)."""
        self.assertEqual(self._anchor_live_balance(), LIVE_BALANCE)
        rows_before = self.conn.execute("SELECT COUNT(*) FROM virtual_cash_ledger").fetchone()[0]

        for index, (qty, price, expected) in enumerate(REJECTED_ORDERS):
            with self.assertRaises(ledger.InsufficientVirtualCash) as ctx:
                ledger.record_trade(
                    self.conn,
                    strategy=STRATEGY,
                    initial_cash=INITIAL_CASH,
                    side="buy",
                    stock_code="999999",
                    stock_name=f"거절후보{index}",
                    holding_id=900 + index,
                    quantity=qty,
                    price=price,
                    ref_key=f"peak_trade:repro:{index}",
                    occurred_at="2026-10-11 11:53:24",
                )
            exc = ctx.exception
            self.assertAlmostEqual(exc.projected, expected, delta=0.01, msg=f"{qty}×{price} 결손 불일치")
            self.assertIn(f"{expected:.2f}", str(exc), "기존 로그 접두사 호환 메시지가 깨졌다")
            self.assertEqual(exc.balance, LIVE_BALANCE)
            self.assertEqual(
                exc.max_quantity,
                ledger.max_affordable_quantity(LIVE_BALANCE, price),
                "몇 주까지 가능한지가 원장 판정과 다르게 보고된다",
            )
            self.assertLess(exc.max_quantity, qty, "거절된 주문을 '가능'하다고 보고했다")
            self.assertEqual(exc.detail()["reason"], "insufficient_cash")

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM virtual_cash_ledger").fetchone()[0],
            rows_before,
            "거절된 주문이 원장에 기록됐다",
        )

    def test_both_deficits_come_from_the_same_exhausted_account(self) -> None:
        """두 결손은 **같은 잔액**에서 나온 1,000만원 예산 포지션 2건이다(계좌 1개가 소진됨)."""
        for qty, price, expected in REJECTED_ORDERS:
            affordable = ledger.max_affordable_quantity(LIVE_BALANCE, price)
            self.assertLess(affordable, qty, "잔액으로 감당 가능한 수량이라면 거부될 이유가 없다")
            self.assertAlmostEqual(LIVE_BALANCE - ledger.required_cash(qty, price), expected, delta=0.01)
            self.assertGreater(ledger.required_cash(qty, price), LIVE_BALANCE, "1,000만원 포지션이 잔액보다 크다")
        # 두 주문 모두 '1,000만원 예산' 이라 잔액(917만원)보다 크다 — 즉 계좌 하나가 예산 1건조차 못 산다.
        self.assertLess(LIVE_BALANCE, ledger.required_cash(217, 45_950))
        self.assertLess(LIVE_BALANCE, ledger.required_cash(225, 44_250))

    def test_a_healthy_account_accepts_both_orders(self) -> None:
        """양성 대조 — 계산이 깨진 게 아니라 **현금이 모자란** 것이다."""
        ledger.initialize_account(self.conn, "fresh", INITIAL_CASH)
        for index, (qty, price, _expected) in enumerate(REJECTED_ORDERS):
            result = ledger.record_trade(
                self.conn,
                strategy="fresh",
                initial_cash=INITIAL_CASH,
                side="buy",
                stock_code="005930",
                stock_name="대조군",
                holding_id=500 + index,
                quantity=qty,
                price=price,
                ref_key=f"fresh:{index}",
                occurred_at="2026-10-11 11:53:24",
            )
            self.assertTrue(result["inserted"])

    def test_duplicate_resend_is_not_the_cause(self) -> None:
        """중복 전송이었다면 예외가 아니라 `inserted: False` 로 끝난다 — 진입 전에 걸러진다.

        즉 ValueError 는 **처음 보는 주문**에서만 나므로, 로그의 거절은 '중복 재전송' 때문이 아니다.
        """
        self._replay_live_ledger()
        again = ledger.record_trade(
            self.conn,
            strategy=STRATEGY,
            initial_cash=INITIAL_CASH,
            side="buy",
            stock_code="222222",
            stock_name="한화시스템",
            holding_id=1,
            quantity=128,
            price=77_600,
            ref_key="peak_trade:1059",
            occurred_at="2026-10-11 11:53:24",
        )
        self.assertFalse(again["inserted"])
        self.assertNotIn("skipped", again)


class CallerBudgetTests(unittest.TestCase):
    def test_the_caller_sizes_every_mirror_position_at_ten_million_won(self) -> None:
        """호출자(`peak_monitor.sync_mirror`)의 고정 예산이 두 거절 수량을 **정확히** 만든다."""
        import peak_monitor

        self.assertEqual(peak_monitor.MAX_BUDGET, 10_000_000)
        for qty, price, _expected in REJECTED_ORDERS:
            self.assertEqual(peak_monitor.calc_quantity(price), qty)
            self.assertLessEqual(qty * price, peak_monitor.MAX_BUDGET)


class EndpointGuardTests(unittest.TestCase):
    """거절을 500 이 아니라 4xx 로 돌리기 위한 판정식(`projected_balance`/`max_affordable_quantity`)."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.previous_ready = ledger._SCHEMA_READY
        ledger._SCHEMA_READY = False
        self.conn.executescript(ledger.DDL)
        ledger._SCHEMA_READY = True

    def tearDown(self) -> None:
        self.conn.close()
        ledger._SCHEMA_READY = self.previous_ready

    def test_projection_matches_the_ledger_exactly_on_the_live_orders(self) -> None:
        """사전 판정식이 원장과 **같은 결정**을 내린다(갈라지면 500 이 다시 샌다)."""
        for qty, price, _expected in REJECTED_ORDERS:
            self.assertLess(
                ledger.projected_balance(LIVE_BALANCE, qty, price),
                ledger.OVERDRAFT_TOLERANCE_KRW,
                "사전 점검이 이 주문을 통과시킨다 — 원장이 500 을 낸다",
            )
            affordable = ledger.max_affordable_quantity(LIVE_BALANCE, price)
            self.assertGreaterEqual(
                ledger.projected_balance(LIVE_BALANCE, affordable, price),
                ledger.OVERDRAFT_TOLERANCE_KRW,
                "가능하다고 한 수량을 원장이 거부한다",
            )
            self.assertLess(
                ledger.projected_balance(LIVE_BALANCE, affordable + 1, price),
                ledger.OVERDRAFT_TOLERANCE_KRW,
                "최대 수량을 1주 낮게 보고했다",
            )

    def test_projection_equals_the_ledger_on_a_grid_and_never_accepts_an_overdraft(self) -> None:
        for balance in (0.0, 100.0, 500_000.0, LIVE_BALANCE, INITIAL_CASH):
            for price in (1_000, 3_855, 45_950, 206_000, 1_605_000):
                for qty in (1, 5, 100, 217, 1000):
                    projected = ledger.projected_balance(balance, qty, price)
                    guard_rejects = projected < ledger.OVERDRAFT_TOLERANCE_KRW
                    try:
                        ledger.record_trade(
                            self.conn, strategy=f"grid-{balance}-{price}-{qty}", initial_cash=balance,
                            side="buy", stock_code="005930", stock_name="그리드",
                            holding_id=qty, quantity=qty, price=price,
                            ref_key=f"grid:{balance}:{price}:{qty}", occurred_at="2026-10-11 00:00:00",
                        )
                        ledger_raised = False
                    except ledger.InsufficientVirtualCash:
                        ledger_raised = True
                    self.assertEqual(guard_rejects, ledger_raised, f"판정 불일치 {balance=} {price=} {qty=}")

    def test_max_affordable_quantity_is_always_executable_and_maximal(self) -> None:
        for balance in (825_707.70, LIVE_BALANCE, 5_000_000.0, 30_643_476.00):
            for price in (3_855, 37_850, 45_950, 1_605_000):
                qty = ledger.max_affordable_quantity(balance, price)
                if qty > 0:
                    result = ledger.record_trade(
                        self.conn, strategy=f"max-{balance}-{price}", initial_cash=balance, side="buy",
                        stock_code="005930", stock_name="최대수량", holding_id=qty, quantity=qty, price=price,
                        ref_key=f"max:{balance}:{price}", occurred_at="2026-10-11 00:00:00",
                    )
                    self.assertTrue(result["inserted"], f"{balance}×{price} → {qty}주가 실행되지 않는다")
                    self.assertGreaterEqual(result["balance_after"], ledger.OVERDRAFT_TOLERANCE_KRW)
                self.assertLess(
                    ledger.projected_balance(balance, qty + 1, price),
                    ledger.OVERDRAFT_TOLERANCE_KRW,
                    f"{balance}×{price}: {qty+1}주도 가능하다고 잘못 판정했다",
                )

    def test_cost_breakdown_matches_the_stored_ledger_row(self) -> None:
        """비용식도 원장과 한 곳에서 온다 — 저장된 gross/fee/slippage 와 대조한다."""
        result = ledger.record_trade(
            self.conn, strategy="cost", initial_cash=100_000_000, side="buy",
            stock_code="005930", stock_name="삼성전자", holding_id=1, quantity=217, price=45_950,
            ref_key="cost:1", occurred_at="2026-10-11 00:00:00",
        )
        costs = ledger.buy_cost_breakdown(217, 45_950)
        self.assertAlmostEqual(costs["total"], ledger.required_cash(217, 45_950))
        self.assertAlmostEqual(
            result["fee"] + result["slippage_cost"] + costs["gross"],
            costs["total"],
            places=6,
        )
        self.assertAlmostEqual(result["balance_after"], INITIAL_CASH - costs["total"], places=6)

    def test_rejection_is_a_valueerror_subclass_for_compatibility(self) -> None:
        self.assertTrue(issubclass(ledger.InsufficientVirtualCash, ValueError))


class EndpointWiringTests(unittest.TestCase):
    """엔드포인트가 원장과 **같은** 판정식을 쓰는지 소스로 고정(둘이 갈라지면 500 재발)."""

    def test_trend_buy_uses_the_shared_predicate_and_returns_409(self) -> None:
        source = Path(__file__).resolve().parents[1] / "routes" / "trend.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("OVERDRAFT_TOLERANCE_KRW", text)
        self.assertIn("projected_balance(", text)
        self.assertIn("max_affordable_quantity(", text)
        self.assertIn("LIMIT_INSUFFICIENT_CASH", text)
        self.assertEqual(ledger.LIMIT_INSUFFICIENT_CASH, "insufficient_cash")
        self.assertIn('"BLOCKED_CASH"', text)
        self.assertIn("except InsufficientVirtualCash", text, "원장 예외 2차 방어선이 없다")


class SizingPlanTests(unittest.TestCase):
    """수량 정책 ① — 목표수량을 잔액 기반 최대가능수량으로 제한한다(계획 단계의 순수 함수)."""

    def test_full_size_plan_keeps_the_target(self) -> None:
        plan = ledger.plan_buy_quantity(217, 45_950, 100_000_000.0)
        self.assertEqual(plan["quantity"], 217)
        self.assertEqual(plan["target_quantity"], 217)
        self.assertIsNone(plan["limit_reason"], "다 살 수 있는데 제한 사유가 붙었다")
        note = ledger.sizing_note(target_quantity=217, filled_quantity=plan["quantity"], limit_reason=None)
        self.assertFalse(note["partial"])

    def test_partial_plan_reports_reason_and_affordable_quantity(self) -> None:
        plan = ledger.plan_buy_quantity(217, 45_950, 5_000_000.0)
        affordable = ledger.max_affordable_quantity(5_000_000.0, 45_950)
        self.assertEqual(plan["quantity"], affordable)
        self.assertEqual(plan["affordable_quantity"], affordable)
        self.assertEqual(plan["limit_reason"], ledger.LIMIT_INSUFFICIENT_CASH)
        self.assertLess(plan["quantity"], plan["target_quantity"])

    def test_zero_affordable_plan_is_zero_not_a_zero_share_order(self) -> None:
        plan = ledger.plan_buy_quantity(217, 45_950, 10_000.0)
        self.assertEqual(plan["quantity"], 0)
        self.assertEqual(plan["limit_reason"], ledger.LIMIT_INSUFFICIENT_CASH)
        self.assertEqual(plan["affordable_quantity"], 0)

    def test_unknown_balance_defers_the_decision_to_the_server(self) -> None:
        plan = ledger.plan_buy_quantity(217, 45_950, None)
        self.assertEqual(plan["quantity"], 217)
        self.assertIsNone(plan["limit_reason"])
        self.assertIsNone(plan["affordable_quantity"])

    def test_plan_never_exceeds_the_target(self) -> None:
        self.assertEqual(ledger.plan_buy_quantity(5, 45_950, 100_000_000.0)["quantity"], 5)
        self.assertEqual(ledger.plan_buy_quantity(0, 45_950, 100_000_000.0)["quantity"], 0)


class SizingNoteTests(unittest.TestCase):
    """부분 체결 감사 기록 — 목표·체결·사유·판정 근거를 남긴다."""

    def _note(self, **overrides):
        base = dict(
            target_quantity=217, filled_quantity=199, limit_reason=ledger.LIMIT_INSUFFICIENT_CASH,
            price=45_950, balance=9_175_022.0, affordable_quantity=199,
            decision_source="peak_monitor.sync_mirror",
        )
        base.update(overrides)
        return ledger.sizing_note(**base)

    def test_partial_note_records_intent_and_reason(self) -> None:
        note = self._note()
        self.assertEqual(note["target_quantity"], 217)
        self.assertEqual(note["filled_quantity"], 199)
        self.assertEqual(note["limit_reason"], ledger.LIMIT_INSUFFICIENT_CASH)
        self.assertTrue(note["partial"])
        self.assertEqual(note["balance_at_decision"], 9_175_022.0)
        self.assertEqual(note["decision_source"], "peak_monitor.sync_mirror")

    def test_full_fill_is_not_marked_partial(self) -> None:
        note = self._note(filled_quantity=217, limit_reason=None, affordable_quantity=300)
        self.assertFalse(note["partial"], "목표대로 다 샀는데 부분체결로 기록됐다")
        self.assertIsNone(note["limit_reason"])

    def test_full_quantity_with_a_reason_is_still_not_partial(self) -> None:
        note = self._note(filled_quantity=217, limit_reason=ledger.LIMIT_INSUFFICIENT_CASH)
        self.assertFalse(note["partial"])


class MergeReasonJsonTests(unittest.TestCase):
    """감사 기록은 기존 편입 사유를 **덮어쓰지 않고** 병합돼야 한다."""

    def test_empty_existing_json_gets_the_note(self) -> None:
        merged = json.loads(ledger.merge_reason_json("", {"target_quantity": 217}))
        self.assertEqual(merged[ledger.SIZING_NOTE_KEY]["target_quantity"], 217)

    def test_existing_object_keys_are_preserved(self) -> None:
        existing = json.dumps({"why": "신규 편입", "sector": "AI"}, ensure_ascii=False)
        merged = json.loads(ledger.merge_reason_json(existing, {"filled_quantity": 199}))
        self.assertEqual(merged["why"], "신규 편입")
        self.assertEqual(merged["sector"], "AI")
        self.assertEqual(merged["mirror_sizing"]["filled_quantity"], 199)

    def test_non_json_existing_value_is_preserved_as_raw(self) -> None:
        merged = json.loads(ledger.merge_reason_json("사유 텍스트", {"filled_quantity": 1}))
        self.assertEqual(merged["previous_raw"], "사유 텍스트")
        self.assertIn("mirror_sizing", merged)

    def test_json_array_existing_value_is_also_preserved(self) -> None:
        merged = json.loads(ledger.merge_reason_json("[1, 2]", {"filled_quantity": 1}))
        self.assertEqual(merged["previous_raw"], "[1, 2]")

    def test_merge_is_valid_json_for_a_note_roundtrip(self) -> None:
        note = ledger.sizing_note(target_quantity=217, filled_quantity=199, limit_reason="insufficient_cash")
        self.assertEqual(json.loads(ledger.merge_reason_json(None, note))["mirror_sizing"], note)


class SizingAuditWiringTests(unittest.TestCase):
    """엔드포인트가 호출자가 보낸 목표수량·사유를 **원장(보유행 사유 JSON)과 응답에** 남기는지 소스로 고정."""

    def test_trend_buy_persists_and_echoes_the_sizing_audit(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "routes" / "trend.py").read_text(encoding="utf-8")
        for token in (
            'payload.get("target_quantity")',
            'payload.get("limit_reason")',
            "sizing_note(",
            "merge_reason_json(entry_reason_json, sizing_audit)",
            '"filled_quantity": int(quantity)',
            '"sizing_partial"',
        ):
            self.assertIn(token, text, f"{token} 누락 — 감사 기록이 보존되지 않는다")

    def test_monitor_sends_both_audit_fields(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "peak_monitor.py").read_text(encoding="utf-8")
        for token in ('"target_quantity": target_qty', '"limit_reason": plan["limit_reason"]',
                      "plan_buy_quantity(", "mirror_buy_is_held("):
            self.assertIn(token, text, f"{token} 누락 — 모니터가 감사 필드를 보내지 않는다")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
