"""Cash-constrained compounding portfolio simulator shared by strict backtests."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class Position:
    code: str; quantity: int; average_price: float; opened_at: str; cost_basis: float

@dataclass
class CashPortfolio:
    initial_cash: float = 100_000_000
    max_positions: int = 10          # dynamic_tickets=False일 때의 고정 상한
    fee_bps: float = 1.5
    slippage_bps: float = 10
    sell_tax_bps: float = 18
    # C2 (2026-07-14, Codex 필수점검): 동적 티켓 확장 —
    # position_limit = floor(marked_equity / ticket_budget). 1.1억 에쿼티 → 11번째 티켓 허용.
    # 에쿼티 마킹에는 현재가가 필요하므로 buy() 호출 시 mark_prices를 넘겨야 확장이 적용됨
    # (미전달 시 현금+평단 기준 보수적 마킹). 한도 하락 시 강제 매도는 하지 않음(신규 진입만 제한).
    dynamic_tickets: bool = False
    ticket_budget: float = 10_000_000
    # 2026-08-08: dynamic_tickets는 포지션 "개수" 상한만 자본에 비례시키고 티켓 "크기"는
    # ticket_budget 명목값으로 고정된다. 실측(cmb_8d727d5b7a8f)에서 에쿼티가 1억→7.1억으로
    # 불어난 뒤에도 신규 편입은 건당 1,000만원(에쿼티의 1.4%)뿐이라 현금 4.3억(61%)이 유휴로 남았다.
    # ticket_pct(0<x<=1)를 주면 티켓 = max(ticket_budget, equity*ticket_pct)로 자본에 비례시켜
    # 위험 노출을 %로 일정하게 유지한다. None이면 기존 동작과 완전히 동일(기본값).
    ticket_pct: float | None = None
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    ledger: list[dict] = field(default_factory=list)
    def __post_init__(self): self.cash=float(self.initial_cash)
    def _buy_price(self,p): return p*(1+self.slippage_bps/10000)
    def _sell_price(self,p): return p*(1-self.slippage_bps/10000)
    def position_limit(self, mark_prices=None):
        if not self.dynamic_tickets:
            return self.max_positions
        return max(1, int(self.equity(mark_prices or {}) // self.ticket_budget))
    def buy(self,code,date,price,budget=None,mark_prices=None):
        limit = self.position_limit(mark_prices)
        if code in self.positions or len(self.positions)>=limit or price<=0:return False
        if budget is not None and self.ticket_pct:
            budget = max(float(budget), self.equity(mark_prices or {}) * float(self.ticket_pct))
        slots=max(1,limit-len(self.positions)); allocation=min(self.cash,self.cash/slots if budget is None else budget)
        fill=self._buy_price(price); qty=int(allocation/(fill*(1+self.fee_bps/10000)))
        if qty<1:return False
        gross=qty*fill; fee=gross*self.fee_bps/10000; total=gross+fee
        if total>self.cash:return False
        self.cash-=total; self.positions[code]=Position(code,qty,fill,date,total)
        self.ledger.append({"date":date,"code":code,"side":"buy","quantity":qty,"price":fill,"fee":fee,"tax":0.0,"cash_after":self.cash});return True
    def add_to_position(self,code,date,price,budget,mark_prices=None):
        """이미 보유 중인 포지션에만 추가 투입(피라미딩).

        2026-08-09/10: 유휴자본 문제를 ticket_pct(자본비례 티켓)로 풀려던 시도가
        전부 실패했다(동점 슬롯경쟁이 극단적으로 불안정해짐, CV 25~63%, ledger
        'ticket_pct_reverify_after_normalization_20260809'). 이 메서드는 신규 슬롯을
        전혀 건드리지 않고 **이미 보유 중인 종목에만** 자본을 더 태우므로 슬롯 경쟁/동점
        타이브레이크와 구조적으로 무관하다 — backtest.py run_backtest_sector에서
        단독 실행으로 이미 검증됨(결정적 재현, base 270.88%→gain10 289.59%,
        ledger 'score_based_pyramiding_20260809').
        """
        pos = self.positions.get(code)
        if not pos or price<=0 or budget is None or budget<=0: return False
        budget = min(float(budget), self.cash*0.99)
        fill = self._buy_price(price)
        qty = int(budget/(fill*(1+self.fee_bps/10000)))
        if qty<1: return False
        gross=qty*fill; fee=gross*self.fee_bps/10000; total=gross+fee
        if total>self.cash: return False
        self.cash -= total
        new_qty = pos.quantity + qty
        # 가중평균 단가로 원가 재계산 — 이후 매도 시 pnl 계산 기준
        pos.average_price = (pos.average_price*pos.quantity + fill*qty)/new_qty
        pos.quantity = new_qty
        pos.cost_basis += total
        self.ledger.append({"date":date,"code":code,"side":"pyramid_add","quantity":qty,"price":fill,"fee":fee,"tax":0.0,"cash_after":self.cash})
        return True
    def sell(self,code,date,price,reason="signal"):
        pos=self.positions.get(code)
        if not pos or price<=0:return False
        fill=self._sell_price(price);gross=pos.quantity*fill;fee=gross*self.fee_bps/10000;tax=gross*self.sell_tax_bps/10000;net=gross-fee-tax
        self.cash+=net; pnl=net-pos.cost_basis
        self.ledger.append({"date":date,"code":code,"side":"sell","quantity":pos.quantity,"price":fill,"fee":fee,"tax":tax,"pnl":pnl,"reason":reason,"cash_after":self.cash});del self.positions[code];return True
    def equity(self,prices): return self.cash+sum(p.quantity*prices.get(c,p.average_price) for c,p in self.positions.items())
    def summary(self,prices):
        equity=self.equity(prices); sells=[x for x in self.ledger if x['side']=='sell']; wins=[x for x in sells if x['pnl']>0]
        return {"initial_cash":self.initial_cash,"cash":self.cash,"equity":equity,"total_return_pct":(equity/self.initial_cash-1)*100,
                "open_positions":len(self.positions),"completed_trades":len(sells),"win_rate_pct":len(wins)/len(sells)*100 if sells else None,
                "fees_paid":sum(x['fee'] for x in self.ledger),
                "taxes_paid":sum(x.get('tax',0) for x in self.ledger)}
