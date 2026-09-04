"""Deterministic multi-strategy order merger using one cash account."""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import statistics
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

from portfolio_engine import CashPortfolio
from run_registry import canonical_hash, derive_status, ensure_schema as ensure_registry_schema, register_artifact, source_snapshot


DB_PATH = Path(__file__).resolve().parent / "stock.db"


def _parse_date(s: str):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _neutral_tiebreak(date: str, stock_code: str, strategy: str) -> str:
    """동점(priority 동일) 주문의 순서를 정하는 중립 키.

    2026-08-08 정상화 — 왜 종목코드를 쓰면 안 되는가:
    기존 정렬 키는 `(-priority, stock_code, strategy)`였다. 한국 종목코드는 낮을수록
    오래되고 큰 기업이라, 동점일 때 코드순으로 자르면 **의도하지 않은 '구형 대형주 선호'
    팩터 틸트**가 주입된다. 슬롯이 넉넉하면 어차피 대부분 체결돼 영향이 작지만, 슬롯이
    줄면(큰 티켓/작은 max_positions) 포트폴리오가 사실상 이 임의 기준으로 결정된다.
    실측: cmb_8d727d5b7a8f의 단일 경로 612.9%는 동점을 무작위화한 10회 평균 524.0%,
    최대 605.0%보다도 높았다(ticket_pct=25%에서는 2681.9% vs 무작위 최대 1371.6%).

    blake2b 해시는 (a) 코드 크기와 무관해 팩터 틸트가 없고 (b) 실행/머신에 무관하게
    항상 같은 값이라 재현 가능하다(파이썬 내장 hash()는 프로세스마다 달라 부적합).
    """
    return hashlib.blake2b(
        f"{date}|{stock_code}|{strategy}".encode("utf-8"), digest_size=8
    ).hexdigest()


@dataclass(frozen=True)
class CandidateOrder:
    date: str
    stock_code: str
    side: str
    price: float
    strategy: str
    priority: float = 0.0
    reason: str = "signal"
    budget: float | None = None
    sector: str = ""


@dataclass
class MergeConfig:
    initial_cash: float = 100_000_000
    ticket_budget: float = 10_000_000
    max_positions: int = 10
    dynamic_tickets: bool = True
    fee_bps: float = 1.5
    slippage_bps: float = 10
    sell_tax_bps: float = 18
    max_sector_positions: int | None = None
    strategy_budget_weights: dict[str, float] = field(default_factory=dict)
    # 2026-08-08: 티켓 크기를 에쿼티에 비례시키는 옵션(0<x<=1). None이면 기존 명목 고정 동작.
    # portfolio_engine.CashPortfolio.ticket_pct 참조.
    ticket_pct: float | None = None
    # 2026-08-08 정상화: 동점 주문의 순서 기준. 기본 "neutral_hash"(_neutral_tiebreak 참조).
    # "stock_code"는 2026-08-08 이전 등록 run을 그대로 재현할 때만 사용한다 — 코드가 낮은
    # (=오래되고 큰) 종목을 체계적으로 선호해 팩터 틸트를 주입하므로 신규 등록에는 쓰지 말 것.
    tiebreak_mode: str = "neutral_hash"
    # 2026-08-11: 피라미딩 공유계좌 나비효과 완화책(ledger
    # 'merged_account/pyramid_shared_account_amplification_instability_20260811') 실험용.
    # 둘 다 None(비활성)이면 기존 동작과 완전히 동일.
    max_pyramid_adds: int | None = None       # 포지션당 최대 추가매수 횟수 상한(merged_simulator 자체 강제)
    pyramid_min_hold_days: int | None = None  # 최초 진입 후 최소 보유일수 경과해야 피라미드 자격


def _normalize_orders(orders: list[CandidateOrder | dict], tiebreak_mode: str = "neutral_hash") -> list[CandidateOrder]:
    def tiebreak_key(o: "CandidateOrder") -> str:
        if tiebreak_mode == "stock_code":
            return o.stock_code
        return _neutral_tiebreak(o.date, o.stock_code, o.strategy)

    normalized = [order if isinstance(order, CandidateOrder) else CandidateOrder(**order) for order in orders]
    for order in normalized:
        if order.side.lower() not in {"buy", "sell", "pyramid"}:
            raise ValueError(f"unsupported side: {order.side}")
        if order.price <= 0 or not order.stock_code or not order.date:
            raise ValueError("order requires date, stock_code and positive price")
    return sorted(normalized, key=lambda x: (x.date, 0 if x.side.lower() == "sell" else 1, -x.priority, tiebreak_key(x), x.strategy))


def _load_daily_price_map(
    codes: set[str], start_date: str, end_date: str, db_path: Path | str = DB_PATH
) -> dict[str, dict[str, float]]:
    """2026-07-25: 일별 mark-to-market 도입 — Codex가 발견한 시뮬레이터 마킹 불일치
    (simulate_merged_account가 주문일에만 marks를 갱신해 장기보유 포지션이 진입가에 고정되던 문제,
    signal_experiment_ledger 'merged_simulator_infrastructure' 참조) 수정용. 종목코드가 실제
    price_history에 없으면(합성 테스트 코드 등) 조용히 빈 dict를 반환 — 기존 order-price 마킹으로
    자연히 폴백되어 하위호환 유지."""
    if not codes:
        return {}
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        placeholders = ",".join("?" for _ in codes)
        rows = conn.execute(
            f"""SELECT stock_code, date(date), close FROM price_history
                WHERE stock_code IN ({placeholders}) AND date(date) BETWEEN ? AND ?
                  AND close > 0""",
            (*sorted(codes), start_date, end_date),
        ).fetchall()
        conn.close()
    except Exception:
        return {}
    out: dict[str, dict[str, float]] = {}
    for code, d, close in rows:
        # 2026-08-11: PostgreSQL 라우팅 하에서는 date(date)가 datetime.date 객체로 반환되어
        # (SQLite는 text로 반환) order_dates(str)와 섞여 정렬 시 TypeError 발생 — 항상 ISO 문자열로
        # 통일한다. str(date(2020,8,7)) == '2020-08-07'이라 포맷은 기존과 동일하게 유지됨.
        out.setdefault(code, {})[str(d)] = float(close)
    return out


def simulate_merged_account(
    orders: list[CandidateOrder | dict],
    config: MergeConfig | None = None,
) -> dict:
    cfg = config or MergeConfig()
    normalized = _normalize_orders(orders, cfg.tiebreak_mode)
    portfolio = CashPortfolio(
        initial_cash=cfg.initial_cash,
        max_positions=cfg.max_positions,
        dynamic_tickets=cfg.dynamic_tickets,
        ticket_budget=cfg.ticket_budget,
        ticket_pct=cfg.ticket_pct,
        fee_bps=cfg.fee_bps,
        slippage_bps=cfg.slippage_bps,
        sell_tax_bps=cfg.sell_tax_bps,
    )
    marks: dict[str, float] = {}
    attribution: dict[str, list[str]] = {}
    capital_owner: dict[str, str] = {}
    strategy_capital_used: dict[str, float] = {}
    position_sector: dict[str, str] = {}
    pyramid_add_counts: dict[str, int] = {}
    events: list[dict] = []
    merged_duplicate_signals = 0
    # 2026-07-29: 병합계좌 안정성(MDD) 지표 신규 — 지금까지 total_return_pct만 보고
    # "안정성"은 개별 컴포넌트 백테스트의 max_drawdown_pct(있는 것만)로 추정할 수밖에 없었음.
    # 실제 병합계좌(현금+포지션 evaluated daily)의 peak-to-trough를 직접 추적해 계좌 단위
    # MDD를 산출 — 조합 후보 비교 시 수익률뿐 아니라 낙폭도 함께 판단 가능하게 함.
    equity_peak = cfg.initial_cash
    max_drawdown_pct = 0.0
    max_drawdown_date = None
    order_dates = {order.date for order in normalized}
    price_map = _load_daily_price_map(
        {order.stock_code for order in normalized}, min(order_dates), max(order_dates)
    ) if order_dates else {}
    dates = sorted(order_dates | {d for by_date in price_map.values() for d in by_date})
    for day in dates:
        # 일별 mark-to-market: 실제 종가로 갱신(보유중이나 오늘 주문 없는 포지션도 최신가 반영).
        # price_history에 없는 종목(합성 테스트 코드 등)은 CashPortfolio.equity()가 average_price로
        # 자동 폴백 — Codex의 검증된 true simulator와 동일하게 주문가로 별도 덮어쓰지 않음.
        for code, by_date in price_map.items():
            px = by_date.get(day)
            if px:
                marks[code] = px
        day_orders = [order for order in normalized if order.date == day]

        sold_codes: set[str] = set()
        sell_groups: dict[str, list[CandidateOrder]] = {}
        for order in day_orders:
            if order.side.lower() == "sell":
                sell_groups.setdefault(order.stock_code, []).append(order)
        for code in sorted(sell_groups):
            group = sell_groups[code]
            contributors = sorted({order.strategy for order in group})
            chosen = sorted(group, key=lambda x: (-x.priority, x.strategy))[0]
            owner = capital_owner.get(code)
            released_cost = portfolio.positions[code].cost_basis if code in portfolio.positions else 0.0
            if portfolio.sell(code, day, chosen.price, chosen.reason):
                sold_codes.add(code)
                events.append({"date": day, "stock_code": code, "side": "sell", "status": "filled", "contributors": contributors})
                attribution.pop(code, None)
                capital_owner.pop(code, None)
                position_sector.pop(code, None)
                pyramid_add_counts.pop(code, None)
                if owner:
                    strategy_capital_used[owner] = max(0.0, strategy_capital_used.get(owner, 0.0) - released_cost)
            else:
                events.append({"date": day, "stock_code": code, "side": "sell", "status": "rejected", "reason": "no_open_position", "contributors": contributors})

        # ── 피라미드 추가매수(2026-08-10, 사용자 제안) ──
        # 신규 슬롯을 전혀 소비하지 않고 "이미 보유 중인" 포지션에만 자본을 더 태운다.
        # backtest.py run_backtest_sector에서 단독 검증된 로직(섹터점수가 진입시점보다
        # +N점 오르면 추가매수)의 이벤트를 CandidateOrder(side="pyramid")로 그대로 재생.
        # ⚠️ 2026-08-11 정정: 슬롯 경쟁은 실제로 무관함(대조실험 CV=0%로 확인)하지만, 공유계좌에서는
        # "그 시점에 우연히 보유 중이었는가"라는 사소한 동점 하나가 수년간 복리로 증폭되는 나비효과가
        # 실측됨(ledger 'merged_account/pyramid_shared_account_amplification_instability_20260811',
        # CV 0%→26.4%). max_pyramid_adds/pyramid_min_hold_days로 증폭 강도를 제한하는 실험용 완화책.
        pyramid_groups: dict[str, list[CandidateOrder]] = {}
        for order in day_orders:
            if order.side.lower() == "pyramid":
                pyramid_groups.setdefault(order.stock_code, []).append(order)
        for code in sorted(pyramid_groups):
            group = pyramid_groups[code]
            contributors = sorted({order.strategy for order in group})
            if code in sold_codes or code not in portfolio.positions:
                events.append({"date": day, "stock_code": code, "side": "pyramid", "status": "rejected", "reason": "no_open_position", "contributors": contributors})
                continue
            pos = portfolio.positions[code]
            if cfg.max_pyramid_adds is not None and pyramid_add_counts.get(code, 0) >= cfg.max_pyramid_adds:
                events.append({"date": day, "stock_code": code, "side": "pyramid", "status": "rejected", "reason": "max_adds_reached", "contributors": contributors})
                continue
            if cfg.pyramid_min_hold_days is not None:
                held_days = (_parse_date(day) - _parse_date(pos.opened_at)).days
                if held_days < cfg.pyramid_min_hold_days:
                    events.append({"date": day, "stock_code": code, "side": "pyramid", "status": "rejected", "reason": "min_hold_not_met", "contributors": contributors})
                    continue
            chosen = sorted(group, key=lambda x: (-x.priority, x.strategy))[0]
            budget = chosen.budget or cfg.ticket_budget
            owner = capital_owner.get(code)
            cost_before = portfolio.positions[code].cost_basis
            if portfolio.add_to_position(code, day, chosen.price, budget, marks):
                events.append({"date": day, "stock_code": code, "side": "pyramid", "status": "filled", "contributors": contributors})
                pyramid_add_counts[code] = pyramid_add_counts.get(code, 0) + 1
                if owner:
                    strategy_capital_used[owner] = strategy_capital_used.get(owner, 0.0) + (portfolio.positions[code].cost_basis - cost_before)
            else:
                events.append({"date": day, "stock_code": code, "side": "pyramid", "status": "rejected", "reason": "cash_or_budget", "contributors": contributors})

        buy_groups: dict[str, list[CandidateOrder]] = {}
        for order in day_orders:
            if order.side.lower() == "buy":
                buy_groups.setdefault(order.stock_code, []).append(order)
        ranked_buys = []
        for code, group in buy_groups.items():
            merged_duplicate_signals += max(0, len(group) - 1)
            chosen = sorted(group, key=lambda x: (-x.priority, x.strategy))[0]
            ranked_buys.append((chosen, sorted({order.strategy for order in group})))
        ranked_buys.sort(key=lambda item: (
            -item[0].priority,
            item[0].stock_code if cfg.tiebreak_mode == "stock_code"
            else _neutral_tiebreak(item[0].date, item[0].stock_code, item[0].strategy),
            ",".join(item[1]),
        ))

        for chosen, contributors in ranked_buys:
            code = chosen.stock_code
            if code in sold_codes:
                events.append({"date": day, "stock_code": code, "side": "buy", "status": "rejected", "reason": "same_day_sell_conflict", "contributors": contributors})
                continue
            if code in portfolio.positions:
                existing = set(attribution.get(code, []))
                attribution[code] = sorted(existing | set(contributors))
                events.append({"date": day, "stock_code": code, "side": "buy", "status": "deduplicated", "reason": "position_already_open", "contributors": contributors})
                continue
            sector = chosen.sector or "미분류"
            if cfg.max_sector_positions is not None:
                sector_count = sum(value == sector for value in position_sector.values())
                if sector_count >= cfg.max_sector_positions:
                    events.append({"date": day, "stock_code": code, "side": "buy", "status": "rejected", "reason": "sector_position_limit", "sector": sector, "contributors": contributors})
                    continue
            owner = chosen.strategy
            requested_budget = chosen.budget or cfg.ticket_budget
            if cfg.strategy_budget_weights:
                weight = float(cfg.strategy_budget_weights.get(owner, 0.0))
                strategy_cap = cfg.initial_cash * max(0.0, weight)
                remaining = strategy_cap - strategy_capital_used.get(owner, 0.0)
                requested_budget = min(requested_budget, remaining)
                if requested_budget <= 0:
                    events.append({"date": day, "stock_code": code, "side": "buy", "status": "rejected", "reason": "strategy_budget_limit", "contributors": contributors})
                    continue
            filled = portfolio.buy(
                code, day, chosen.price,
                budget=requested_budget,
                mark_prices=marks,
            )
            if filled:
                attribution[code] = contributors
                capital_owner[code] = owner
                position_sector[code] = sector
                used = portfolio.positions[code].cost_basis
                strategy_capital_used[owner] = strategy_capital_used.get(owner, 0.0) + used
                events.append({"date": day, "stock_code": code, "side": "buy", "status": "filled", "contributors": contributors, "capital_owner": owner, "sector": sector, "priority": chosen.priority})
            else:
                reason = "position_limit_or_cash"
                events.append({"date": day, "stock_code": code, "side": "buy", "status": "rejected", "reason": reason, "contributors": contributors, "priority": chosen.priority})

        day_equity = portfolio.equity(marks)
        if day_equity > equity_peak:
            equity_peak = day_equity
        elif equity_peak > 0:
            drawdown_pct = (day_equity - equity_peak) / equity_peak * 100.0
            if drawdown_pct < max_drawdown_pct:
                max_drawdown_pct = drawdown_pct
                max_drawdown_date = day

    summary = portfolio.summary(marks)
    buy_fills = [event for event in events if event["side"] == "buy" and event["status"] == "filled"]
    buy_rejections = [event for event in events if event["side"] == "buy" and event["status"] == "rejected"]
    sell_rejections = [event for event in events if event["side"] == "sell" and event["status"] == "rejected"]
    pyramid_fills = [event for event in events if event["side"] == "pyramid" and event["status"] == "filled"]
    pyramid_rejections = [event for event in events if event["side"] == "pyramid" and event["status"] == "rejected"]
    rejection_reasons: dict[str, int] = {}
    for event in buy_rejections + sell_rejections:
        reason = str(event.get("reason") or "unknown")
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    summary.update({
        "buy_fills": len(buy_fills),
        "pyramid_fills": len(pyramid_fills),
        "pyramid_rejections": len(pyramid_rejections),
        "rejections": len(buy_rejections) + len(sell_rejections),
        "buy_rejections": len(buy_rejections),
        "sell_rejections": len(sell_rejections),
        "rejection_reasons": rejection_reasons,
        "deduplicated": sum(event["status"] == "deduplicated" for event in events),
        "merged_duplicate_signals": merged_duplicate_signals,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "max_drawdown_date": max_drawdown_date,
    })
    return {
        "config": asdict(cfg),
        "summary": summary,
        "events": events,
        "ledger": portfolio.ledger,
        "open_attribution": attribution,
        "open_capital_owner": capital_owner,
        "strategy_capital_used": strategy_capital_used,
        "position_sector": position_sector,
        "mark_prices": marks,
    }


def tiebreak_stability(
    orders: list[CandidateOrder | dict],
    config: "MergeConfig | None" = None,
    *,
    trials: int = 8,
    seed: int = 0,
    jitter: float = 1e-3,
    tiebreak_mode: str = "neutral_hash",
) -> dict:
    """동점 타이브레이크 의존도(경로운) 측정.

    왜 필요한가 — 2026-08-08 실측으로 확인된 함정:
    `_normalize_orders`/`ranked_buys`의 정렬 키는 `(-priority, stock_code, strategy)`다.
    컴포넌트 우선순위가 동일한 조합(예: v2·sector_focus 모두 1.0)에서는 **실질 정렬
    기준이 종목코드**가 되어버린다. 슬롯이 넉넉하면(작은 티켓/큰 max_positions) 어차피
    대부분 체결되므로 영향이 작지만, 슬롯이 줄면(큰 ticket_pct/작은 max_positions)
    "그날 코드가 낮은 N종목"만 담기게 되어 **포트폴리오가 신호 품질이 아니라 임의
    기준으로 결정된다.**

    실측: cmb_8d727d5b7a8f를 ticket_pct=25%로 돌리면 단일 경로 수익률이 2681.9%인데,
    동점 구간에만 무작위 jitter를 준 10회의 평균은 962.5%, 최대도 1371.6%에 그쳤다
    (원본이 무작위 최댓값의 약 2배). 기본 설정(고정 1,000만원)조차 원본 612.9% vs
    무작위 평균 524.0%로 낙관 편향이 있었다.

    그래서 단일 경로 수익률만 보고 조합을 채택하면 안 되고, 이 함수가 돌려주는
    `mean`/`cv_pct`/`base_above_max`로 "그 숫자가 실력인지 코드순 행운인지"를 함께
    판단해야 한다. jitter는 동점 구간만 흔들도록 충분히 작게(<0.1) 유지한다 —
    실제 우선순위 차(보통 >=0.1)는 보존된다.
    """
    normalized = _normalize_orders(orders, tiebreak_mode)
    base_ret = simulate_merged_account(normalized, config)["summary"]["total_return_pct"]
    rets: list[float] = []
    for t in range(max(1, trials)):
        rnd = random.Random(seed + t)
        shuffled = [replace(o, priority=float(o.priority) + rnd.random() * jitter) for o in normalized]
        rets.append(simulate_merged_account(shuffled, config)["summary"]["total_return_pct"])
    mean = statistics.mean(rets)
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    return {
        "base_return_pct": round(base_ret, 4),
        "trials": len(rets),
        "mean_return_pct": round(mean, 4),
        "median_return_pct": round(statistics.median(rets), 4),
        "min_return_pct": round(min(rets), 4),
        "max_return_pct": round(max(rets), 4),
        "stdev": round(sd, 4),
        "cv_pct": round(sd / mean * 100, 2) if mean else None,
        # 단일 경로값이 무작위 분포의 최댓값마저 넘으면 그 수치는 사실상 상단 꼬리다.
        "base_above_max": base_ret > max(rets),
        "base_percentile": round(100 * sum(1 for r in rets if r < base_ret) / len(rets), 1),
    }


def persist_merged_run(
    orders: list[CandidateOrder | dict],
    component_run_hashes: list[str],
    config: MergeConfig | None = None,
    db_path: Path | str = DB_PATH,
    *,
    tiebreak_trials: int = 8,
    allow_path_luck: bool = False,
) -> dict:
    """병합계좌 run을 등록한다.

    2026-08-08 추가된 게이트: 등록 전에 `tiebreak_stability()`로 동점 타이브레이크
    의존도를 측정하고, 단일 경로 수익률이 무작위 분포의 최댓값마저 넘으면(=사실상
    상단 꼬리) `allow_path_luck=True`를 명시하기 전까지 등록을 거부한다.
    측정 결과는 통과 여부와 무관하게 spec_payload['tiebreak_stability']에 저장돼
    `/api/backtest/combinations/list`로 노출된다. tiebreak_trials=0이면 측정을 건너뛴다
    (대량 탐색 루프용 — 최종 등록에는 쓰지 말 것).
    """
    cfg = config or MergeConfig()
    result = simulate_merged_account(orders, cfg)
    stability = None
    if tiebreak_trials and tiebreak_trials > 0:
        stability = tiebreak_stability(orders, cfg, trials=tiebreak_trials)
        if stability["base_above_max"] and not allow_path_luck:
            raise ValueError(
                "tie-break path luck detected: 단일 경로 수익률 "
                f"{stability['base_return_pct']:.1f}%가 동점 무작위 {stability['trials']}회의 "
                f"최댓값 {stability['max_return_pct']:.1f}%(평균 {stability['mean_return_pct']:.1f}%, "
                f"CV {stability['cv_pct']}%)를 넘습니다 — 이 수치는 종목코드 순서에 의존한 "
                "상단 꼬리일 가능성이 높습니다. 컴포넌트 우선순위를 명시적으로 차등화하거나, "
                "의도적으로 등록하려면 allow_path_luck=True를 넘기세요."
            )
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_registry_schema(conn)
    known_components = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT run_hash FROM backtest_run_specs WHERE run_hash IN ({})".format(
                ",".join("?" for _ in component_run_hashes) or "''"
            ),
            component_run_hashes,
        )
    }
    missing_components = sorted(set(component_run_hashes) - known_components)
    if missing_components:
        conn.close()
        raise ValueError(f"unknown component run hashes: {missing_components}")
    component_statuses = {run_hash: derive_status(conn, run_hash) for run_hash in component_run_hashes}
    unverified_execution = [
        run_hash for run_hash, status in component_statuses.items()
        if status.get("status_rank", 0) < 1
    ]
    if unverified_execution:
        conn.close()
        raise ValueError(f"component runs lack strict execution artifacts: {unverified_execution}")
    snapshot = source_snapshot(conn)
    normalized = [asdict(order) if isinstance(order, CandidateOrder) else order for order in _normalize_orders(orders)]
    spec_payload = {
        "strategy": "combined",
        "engine_version": "merged_cash_account_v1",
        "component_run_hashes": sorted(component_run_hashes),
        "config": asdict(cfg),
        "orders": normalized,
        "source_snapshot": snapshot,
        "engine_code_hash": canonical_hash({"source": Path(__file__).read_text(encoding="utf-8")}),
        "event_order": "sells_then_ranked_deduplicated_buys",
        "component_verification": {
            run_hash: status["status"] for run_hash, status in component_statuses.items()
        },
        "tiebreak_stability": stability,
    }
    run_hash = canonical_hash(spec_payload)
    run_id = f"cmb_{uuid.uuid4().hex[:12]}"
    now = datetime.now().isoformat(timespec="seconds")
    dates = sorted({order["date"] for order in normalized})
    conn.execute(
        """
        INSERT INTO backtest_runs
        (run_id,name,strategy,start_date,end_date,status,created_at)
        VALUES (?,?,?,?,?,'running',?)
        """,
        (run_id, f"병합계좌 {run_hash}", "combined", dates[0] if dates else "", dates[-1] if dates else "", now),
    )
    conn.execute(
        """
        INSERT INTO backtest_run_specs
        (run_id,strategy,engine_version,git_commit,signal_timing,execution_timing,
         market_cap_mode,universe_version,allocation_rule,fee_model,parameter_json,run_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, "combined", "merged_cash_account_v1", "registry",
            "close_D", "next_open", "component_inherited",
            "component_run_registry", "single_cash_dynamic_ticket",
            f"fee{cfg.fee_bps}bps+tax{cfg.sell_tax_bps}bps+slip{cfg.slippage_bps}bps",
            json.dumps(spec_payload, ensure_ascii=False, sort_keys=True), run_hash,
        ),
    )
    summary = result["summary"]
    conn.execute(
        """
        UPDATE backtest_runs SET status='done',total_return_pct=?,win_rate=?,
          total_trades=?,profit_trades=?,summary_text=?,trades_json=?,max_drawdown_pct=?
        WHERE run_id=?
        """,
        (
            summary["total_return_pct"], summary["win_rate_pct"], summary["completed_trades"],
            sum(row.get("pnl", 0) > 0 for row in result["ledger"] if row["side"] == "sell"),
            json.dumps(summary, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
            summary.get("max_drawdown_pct"), run_id,
        ),
    )
    conn.commit()
    conn.close()
    register_artifact(run_hash, "execution_contract", True, {
        "engine": "merged_cash_account_v1", "integer_shares": True,
        "sells_before_buys": True, "duplicate_stock_policy": "one_position",
        "strategy_budget_policy": bool(cfg.strategy_budget_weights),
        "sector_cap": cfg.max_sector_positions,
    }, db_path)
    expected_cash = cfg.initial_cash
    for row in result["ledger"]:
        gross = float(row["quantity"]) * float(row["price"])
        if row["side"] == "buy":
            expected_cash -= gross + float(row.get("fee") or 0)
        else:
            expected_cash += gross - float(row.get("fee") or 0) - float(row.get("tax") or 0)
    cash_delta = float(summary["cash"]) - expected_cash
    register_artifact(run_hash, "cash_reconciliation", abs(cash_delta) < 0.01, {
        "initial_cash": cfg.initial_cash, "final_cash": summary["cash"],
        "final_equity": summary["equity"], "ledger_rows": len(result["ledger"]),
        "ledger_expected_cash": expected_cash, "delta": cash_delta,
    }, db_path)
    return {"run_id": run_id, "run_hash": run_hash, **result}
