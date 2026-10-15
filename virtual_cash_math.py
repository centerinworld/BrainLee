"""가상매매 현금 산술 — **순수 함수만**(DB·엔진 import 없음).

왜 별도 모듈인가
----------------
같은 판정을 두 프로세스가 쓴다: 서버(`routes/trend.py` → `virtual_trading_ledger`)와
별도 프로세스인 `peak_monitor`(30분 폴링으로 주문을 보낸다). 두 곳이 각자 식을 들고 있으면
한쪽만 바뀌어 **미러 계좌가 다시 초과인출**되거나, 반대로 살 수 있는 주문이 거부된다.
`peak_monitor` 가 DB 레이어(`database.py` 엔진)를 끌어오지 않도록 여기에는 부작용이 없다.

2026-10-11 P0(음수 현금)의 교훈
-------------------------------
* 판정식은 `OVERDRAFT_TOLERANCE_KRW` 하나로 고정하고, 원장(`record_trade`)과 엔드포인트 사전점검이
  **같은 함수**를 쓴다.
* 부분 체결(목표수량 > 잔액가능수량)은 조용히 줄이지 않는다 — 목표수량·제한사유를
  `sizing_note()` 로 만들어 응답과 보유행(`entry_reason_json`)에 남겨 성과 왜곡을 감사 가능하게 한다.
"""

from __future__ import annotations

import json
import os

# 비용 비율(환경변수로 조정 가능 — 서버·모니터가 같은 값을 읽는다)
BUY_COMMISSION_RATE = float(os.getenv("VIRTUAL_BUY_COMMISSION_RATE", "0.00015"))
SELL_COMMISSION_RATE = float(os.getenv("VIRTUAL_SELL_COMMISSION_RATE", "0.00015"))
SELL_TAX_RATE = float(os.getenv("VIRTUAL_SELL_TAX_RATE", "0.0020"))
SLIPPAGE_RATE = float(os.getenv("VIRTUAL_SLIPPAGE_RATE", "0.0005"))

# 원장이 초과인출로 판정하는 임계값(이 값 미만이면 거절). 단일 출처.
OVERDRAFT_TOLERANCE_KRW = -0.5

# 부분 체결 감사 기록이 들어가는 키(`entry_reason_json` 안)
SIZING_NOTE_KEY = "mirror_sizing"

LIMIT_INSUFFICIENT_CASH = "insufficient_cash"


def buy_cost_breakdown(quantity, price, *, cost_rates: dict | None = None) -> dict:
    """매수 1건의 비용 내역. 원장과 사전 판정이 **같은 식**을 쓴다."""
    gross = float(quantity) * float(price)
    rates = cost_rates or {}
    fee = gross * float(rates.get("fee", BUY_COMMISSION_RATE))
    slippage = gross * float(rates.get("slippage", SLIPPAGE_RATE))
    return {"gross": gross, "fee": fee, "slippage": slippage, "total": gross + fee + slippage}


def required_cash(quantity, price, *, cost_rates: dict | None = None) -> float:
    """이 매수가 계좌에서 빼갈 총액(수수료·슬리피지 포함)."""
    return buy_cost_breakdown(quantity, price, cost_rates=cost_rates)["total"]


def projected_balance(balance: float, quantity, price, *, cost_rates: dict | None = None) -> float:
    return float(balance) - required_cash(quantity, price, cost_rates=cost_rates)


def max_affordable_quantity(
    balance: float, price, *, cost_rates: dict | None = None, reserve_krw: float = 0.0
) -> int:
    """잔액(-예약금)으로 살 수 있는 최대 수량(비용 포함, 내림). 살 수 없으면 **0**.

    0 을 돌려주는 것은 "0주 주문" 이 아니라 **매수 불가**라는 뜻이다 — 호출자는 주문을 보내지
    않고 보류해야 한다(2026-10-11 정책).
    """
    if price is None or float(price) <= 0:
        return 0
    spendable = float(balance) - float(reserve_krw)
    if spendable <= 0:
        return 0
    rates = cost_rates or {}
    factor = 1.0 + float(rates.get("fee", BUY_COMMISSION_RATE)) + float(rates.get("slippage", SLIPPAGE_RATE))
    qty = max(0, int(spendable // (float(price) * factor)))
    while qty > 0 and required_cash(qty, price, cost_rates=cost_rates) > spendable:
        qty -= 1
    while required_cash(qty + 1, price, cost_rates=cost_rates) <= spendable:
        qty += 1
    return qty


def plan_buy_quantity(
    target_quantity: int, price: float, balance: float | None, *, cost_rates: dict | None = None
) -> dict:
    """목표수량을 **잔액 기반 최대가능수량**으로 제한해 계획을 돌려준다(정책 ①).

    반환: `{"quantity", "target_quantity", "limit_reason", "affordable_quantity", "balance"}`.
    `limit_reason` 이 비어 있으면 목표수량 그대로, 아니면 **부분 체결**이며 그 사유가 남는다.
    잔액을 모르면(None) 목표수량을 그대로 계획한다 — 판정은 서버(원장)가 fail-closed 로 내린다.
    """
    target = max(0, int(target_quantity or 0))
    if balance is None:
        return {
            "quantity": target,
            "target_quantity": target,
            "limit_reason": None,
            "affordable_quantity": None,
            "balance": None,
        }
    affordable = max_affordable_quantity(balance, price, cost_rates=cost_rates)
    quantity = min(target, affordable)
    return {
        "quantity": max(0, quantity),
        "target_quantity": target,
        "limit_reason": None if quantity >= target else LIMIT_INSUFFICIENT_CASH,
        "affordable_quantity": affordable,
        "balance": float(balance),
    }


def sizing_note(
    *,
    target_quantity: int,
    filled_quantity: int,
    limit_reason: str | None,
    price: float | None = None,
    balance: float | None = None,
    affordable_quantity: int | None = None,
    decision_source: str | None = None,
) -> dict:
    """부분 체결 감사 기록 — "원래 몇 주를 사려 했고, 왜 줄었는가"를 남긴다."""
    note = {
        "target_quantity": int(target_quantity or 0),
        "filled_quantity": int(filled_quantity or 0),
        "limit_reason": limit_reason or None,
    }
    if price is not None:
        note["price"] = float(price)
    if balance is not None:
        note["balance_at_decision"] = round(float(balance), 2)
    if affordable_quantity is not None:
        note["affordable_quantity"] = int(affordable_quantity)
    if decision_source:
        note["decision_source"] = decision_source
    note["partial"] = bool(limit_reason) and int(filled_quantity or 0) < int(target_quantity or 0)
    return note


def merge_reason_json(existing: str | None, note: dict, *, key: str = SIZING_NOTE_KEY) -> str:
    """기존 `entry_reason_json`(다른 경로가 쓴 사유)을 **보존**하며 감사 키만 덧붙인다.

    덮어쓰면 다른 전략이 남긴 편입 사유가 사라진다. 기존 값이 JSON 객체가 아니면
    (`''`/NULL/임의 문자열/배열) 원문을 `previous_raw` 로 보관하고 새 객체를 만든다.
    """
    payload: dict = {}
    raw = (existing or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
        else:
            payload = {"previous_raw": raw}
    payload[key] = note
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class InsufficientVirtualCash(ValueError):
    """잔액으로 감당할 수 없는 주문 — **값이 아니라 거절**이다(호출자는 4xx 로 돌려야 한다).

    기존 메시지 접두사(`virtual cash would become negative: <잔액>`)는 로그 grep 호환을 위해
    그대로 두고, 진단에 필요한 전략·수량·가격·최대가능수량을 뒤에 붙인다(이 정보가 없어서
    2026-10-11 진단은 로그만으로 주문을 복원할 수 없었다).
    """

    def __init__(
        self, *, strategy, balance, quantity, price, projected, max_quantity, cost_rates: dict | None = None
    ):
        self.strategy = strategy
        self.balance = float(balance)
        self.quantity = int(quantity)
        self.price = float(price)
        self.projected = float(projected)
        self.max_quantity = int(max_quantity)
        self.required = required_cash(quantity, price, cost_rates=cost_rates)
        super().__init__(
            f"virtual cash would become negative: {self.projected:.2f} "
            f"(strategy={strategy} qty={self.quantity} price={self.price:g} "
            f"balance={self.balance:,.2f} required={self.required:,.2f} "
            f"max_affordable={self.max_quantity})"
        )

    def detail(self) -> dict:
        """API 응답용(호출자가 409 본문에 그대로 실을 수 있게)."""
        return {
            "reason": LIMIT_INSUFFICIENT_CASH,
            "strategy": self.strategy,
            "balance": round(self.balance, 2),
            "quantity": self.quantity,
            "price": self.price,
            "required": round(self.required, 2),
            "projected_balance": round(self.projected, 2),
            "max_affordable_quantity": self.max_quantity,
        }
