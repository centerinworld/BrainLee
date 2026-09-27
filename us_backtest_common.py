"""Point-in-time backtesting infrastructure for US equities.

This module is deliberately independent from :mod:`backtest_common`, whose
universe, price-integrity rules, fees and disclosures are specific to Korea.

Contract
--------
* Signals are evaluated after the close of session D.
* Orders are filled at the open of the next *market* session.  A missing open
  never falls back to the signal-day close.
* Price and fundamental data come only from the ``us_*`` PostgreSQL tables.
* Fundamentals are visible only when ``avail_date <= signal_date``.
* Current-index universes are labelled as survivorship-biased.  The engine
  does not silently promote such a run to research-grade evidence.

The operational ``us_price_history`` feed contains split-adjusted OHLC values
but has no separate adjusted-close column.  That source contract is recorded
in every result.  Runs fail closed on malformed OHLC rows and report large
overnight discontinuities for review instead of attempting to infer a split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Sequence

from db_compat import connect_primary_db
from us_price_integrity import LOWER_DAILY_RATIO, UPPER_DAILY_RATIO


ENGINE_VERSION = "us-event-v2"
PRICE_SOURCE = "us_price_history.adjusted_ohlc_contract_v1"


@dataclass(frozen=True)
class USBar:
    ticker: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class USTarget:
    """Desired portfolio weight generated after a session close."""

    ticker: str
    weight: float


@dataclass(frozen=True)
class USBacktestConfig:
    start_date: str
    end_date: str
    initial_cash: float = 100_000.0
    max_positions: int = 10
    rebalance: str = "month_start"
    slippage_bps: float = 5.0
    commission_per_share: float = 0.0
    minimum_commission: float = 0.0
    sell_notional_fee_bps: float = 0.0
    allow_fractional_shares: bool = False
    benchmark: str = "SPY"
    universe_name: str = "explicit"
    universe_mode: str = "point_in_time"
    price_basis: str = "adjusted_ohlc"
    large_jump_upper_ratio: float = UPPER_DAILY_RATIO
    large_jump_lower_ratio: float = LOWER_DAILY_RATIO

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.initial_cash <= 0 or self.max_positions <= 0:
            raise ValueError("initial_cash and max_positions must be positive")
        if self.rebalance not in {"daily", "week_start", "month_start"}:
            raise ValueError("rebalance must be daily, week_start or month_start")
        if self.universe_mode not in {"point_in_time", "current_membership", "explicit"}:
            raise ValueError("unsupported universe_mode")
        if self.price_basis != "adjusted_ohlc":
            raise ValueError("US engine requires the adjusted_ohlc source contract")
        if self.large_jump_upper_ratio <= 1 or not 0 < self.large_jump_lower_ratio < 1:
            raise ValueError("large-jump bounds must straddle 1.0")
        for value in (self.slippage_bps, self.commission_per_share,
                      self.minimum_commission, self.sell_notional_fee_bps):
            if value < 0:
                raise ValueError("cost parameters must be non-negative")


@dataclass
class USPosition:
    ticker: str
    shares: float
    entry_date: str
    entry_price: float
    cost_basis: float


@dataclass(frozen=True)
class USSecurityOutcome:
    ticker: str
    effective_date: str
    outcome_type: str
    cash_per_share: float = 0.0
    successor_ticker: str | None = None
    successor_shares_per_share: float = 0.0
    contingent_value_unmodeled: bool = False


@dataclass
class USTrade:
    ticker: str
    side: str
    signal_date: str
    execution_date: str
    shares: float
    price: float
    gross_notional: float
    fees: float
    reason: str


@dataclass
class USBacktestResult:
    run_id: str
    config: dict
    metrics: dict
    trades: list[dict]
    equity_curve: list[dict]
    quality: dict
    data_fingerprint: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)


SignalFunction = Callable[[str, Mapping[str, Sequence[USBar]]], Sequence[USTarget]]
EligibilityFunction = Callable[[str], set[str]]


def _valid_bar(row: Sequence[object]) -> USBar | None:
    ticker, day, opn, high, low, close, volume = row
    values = (opn, high, low, close)
    if any(v is None for v in values):
        return None
    opn, high, low, close = (float(v) for v in values)
    if not all(math.isfinite(v) and v > 0 for v in (opn, high, low, close)):
        return None
    tolerance = max(opn, high, low, close) * 1e-9
    if (high + tolerance < max(opn, close)
            or low - tolerance > min(opn, close)
            or high + tolerance < low):
        return None
    # yfinance's dividend adjustment can create machine-epsilon inversions.
    # Normalize those only after the strict material-error test above.
    high = max(high, opn, close)
    low = min(low, opn, close)
    return USBar(str(ticker), str(day)[:10], opn, high, low, close, float(volume or 0))


def load_us_bars(
    tickers: Sequence[str], start_date: str, end_date: str, *, warmup_sessions: int = 260,
    conn=None,
) -> tuple[dict[str, list[USBar]], dict]:
    """Load valid US OHLCV rows, including a bounded pre-start warm-up.

    Invalid/missing OHLC rows are dropped and counted.  The caller owns a
    supplied connection; an internally-created connection is closed here.
    """
    symbols = sorted({str(t).strip().upper() for t in tickers if str(t).strip()})
    if not symbols:
        return {}, {"raw_rows": 0, "valid_rows": 0, "invalid_rows": 0}
    own = conn is None
    conn = conn or connect_primary_db(readonly=True, timeout=120)
    try:
        # PostgreSQL ROW_NUMBER keeps warm-up bounded per symbol and avoids
        # loading the entire 4M-row US table.
        rows = conn.execute(
            """
            WITH selected AS (
              SELECT ticker,date,open,high,low,close,volume
              FROM us_price_history
              WHERE ticker = ANY(?) AND date BETWEEN ? AND ?
            ), warm AS (
              SELECT ticker,date,open,high,low,close,volume FROM (
                SELECT ticker,date,open,high,low,close,volume,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM us_price_history
                WHERE ticker = ANY(?) AND date < ?
              ) x WHERE rn <= ?
            )
            SELECT ticker,date,open,high,low,close,volume FROM selected
            UNION ALL
            SELECT ticker,date,open,high,low,close,volume FROM warm
            ORDER BY ticker,date
            """,
            (symbols, start_date, end_date, symbols, start_date, int(warmup_sessions)),
        ).fetchall()
        out: dict[str, list[USBar]] = {t: [] for t in symbols}
        invalid = 0
        invalid_in_period = 0
        for row in rows:
            bar = _valid_bar(tuple(row))
            if bar is None:
                invalid += 1
                if start_date <= str(row[1])[:10] <= end_date:
                    invalid_in_period += 1
            else:
                out.setdefault(bar.ticker, []).append(bar)
        out = {k: v for k, v in out.items() if v}
        valid = sum(map(len, out.values()))
        return out, {"raw_rows": len(rows), "valid_rows": valid, "invalid_rows": invalid,
                     "invalid_rows_in_test_period": invalid_in_period}
    finally:
        if own:
            conn.close()


def load_current_us_universe(index_name: str = "S&P500", *, conn=None) -> list[str]:
    """Return the current membership list; callers must label survivorship bias."""
    own = conn is None
    conn = conn or connect_primary_db(readonly=True, timeout=120)
    try:
        return [str(r[0]).upper() for r in conn.execute(
            "SELECT ticker FROM us_stock_meta WHERE index_name=? ORDER BY ticker", (index_name,)
        ).fetchall()]
    finally:
        if own:
            conn.close()


def load_us_membership_intervals(
    start_date: str, end_date: str, index_name: str = "S&P500", *, conn=None,
) -> tuple[list[tuple[str, str, str | None]], dict]:
    """Load public reconstructed index intervals overlapping the test period."""
    own = conn is None
    conn = conn or connect_primary_db(readonly=True, timeout=120)
    try:
        rows = [tuple(r) for r in conn.execute(
            """SELECT COALESCE(a.price_ticker,i.ticker),i.effective_from,i.effective_to
                 FROM us_index_membership_intervals i
                 LEFT JOIN us_ticker_aliases a
                   ON a.old_ticker=i.ticker
                  AND a.identity_continuity=1 AND a.status='verified'
                WHERE index_name=? AND effective_from<=?
                  AND (effective_to IS NULL OR effective_to>?)
                ORDER BY COALESCE(a.price_ticker,i.ticker),i.effective_from""",
            (index_name, end_date, start_date),
        ).fetchall()]
        meta = conn.execute(
            """SELECT source,source_hash,first_effective_date,last_effective_date,collected_at
                 FROM us_reference_source_runs
                WHERE source='github_fja05680_sp500' AND status='success'
                ORDER BY collected_at DESC LIMIT 1""").fetchone()
        if not rows or not meta:
            raise RuntimeError("US point-in-time membership reference is not loaded")
        verified_events: list[tuple[str, str, str]] = []
        official_as_of = None
        event_columns = conn.execute(
            "PRAGMA table_info(us_index_membership_verified_events)"
        ).fetchall()
        run_columns = conn.execute(
            "PRAGMA table_info(us_index_membership_event_runs)"
        ).fetchall()
        if event_columns and run_columns:
            verified_events = [tuple(r) for r in conn.execute(
                """SELECT ticker,effective_date,action
                     FROM us_index_membership_verified_events
                    WHERE index_name=? AND status='verified' AND effective_date<=?
                    ORDER BY effective_date,action,ticker""",
                (index_name, end_date),
            ).fetchall()]
            official_as_of_row = conn.execute(
                """SELECT MAX(as_of_date) FROM us_index_membership_event_runs
                    WHERE index_name=? AND status='success'""", (index_name,)
            ).fetchone()
            official_as_of = official_as_of_row[0] if official_as_of_row else None
        rows = apply_verified_membership_events(rows, verified_events)
        alias_count = conn.execute(
            """SELECT COUNT(DISTINCT a.old_ticker)
                 FROM us_index_membership_intervals i
                 JOIN us_ticker_aliases a ON a.old_ticker=i.ticker
                  AND a.identity_continuity=1 AND a.status='verified'
                WHERE i.index_name=? AND i.effective_from<=?
                  AND (i.effective_to IS NULL OR i.effective_to>?)""",
            (index_name, end_date, start_date),
        ).fetchone()[0]
        return rows, {
            "source": meta[0], "source_hash": meta[1], "first_date": meta[2],
            "last_date": max(str(meta[3]), str(official_as_of or "")),
            "collected_at": meta[4],
            "covers_end": max(str(meta[3]), str(official_as_of or "")) >= end_date,
            "verified_aliases_applied": alias_count,
            "verified_official_events_applied": len(verified_events),
            "official_events_as_of": official_as_of,
        }
    finally:
        if own:
            conn.close()


def membership_eligibility(intervals: Sequence[tuple[str, str, str | None]]) -> EligibilityFunction:
    normalized = [(str(t), str(start)[:10], str(end)[:10] if end else None)
                  for t, start, end in intervals]

    def eligible(day: str) -> set[str]:
        return {ticker for ticker, start, end in normalized if start <= day and (end is None or day < end)}

    return eligible


def apply_verified_membership_events(
    intervals: Sequence[tuple[str, str, str | None]],
    events: Sequence[tuple[str, str, str]],
) -> list[tuple[str, str, str | None]]:
    """Overlay official add/remove events onto a lagging public reconstruction."""
    out = [(str(t), str(start)[:10], str(end)[:10] if end else None)
           for t, start, end in intervals]
    for ticker, day, action in sorted(events, key=lambda x: (str(x[1]), str(x[2]), str(x[0]))):
        ticker, day, action = str(ticker), str(day)[:10], str(action).lower()
        if action == "remove":
            out = [(t, start, day if t == ticker and start <= day and (end is None or day < end) else end)
                   for t, start, end in out]
        elif action == "add":
            if not any(t == ticker and start <= day and (end is None or day < end)
                       for t, start, end in out):
                out.append((ticker, day, None))
        else:
            raise ValueError(f"unsupported verified membership action: {action}")
    return sorted(out, key=lambda x: (x[0], x[1], x[2] or "9999-12-31"))


def load_us_security_outcomes(tickers: Sequence[str], *, conn=None) -> list[USSecurityOutcome]:
    if not tickers:
        return []
    own = conn is None
    conn = conn or connect_primary_db(readonly=True, timeout=120)
    try:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(f"""SELECT ticker,effective_date,outcome_type,cash_per_share,
                                       successor_ticker,successor_shares_per_share,
                                       contingent_value_unmodeled
                                  FROM us_security_outcomes
                                 WHERE status='verified' AND ticker IN ({placeholders})
                                 ORDER BY effective_date,ticker""", tuple(tickers)).fetchall()
        return [USSecurityOutcome(
            ticker=str(r[0]), effective_date=str(r[1])[:10], outcome_type=str(r[2]),
            cash_per_share=float(r[3] or 0), successor_ticker=str(r[4]) if r[4] else None,
            successor_shares_per_share=float(r[5] or 0),
            contingent_value_unmodeled=bool(r[6]),
        ) for r in rows]
    finally:
        if own:
            conn.close()


def load_available_us_financials(ticker: str, as_of_date: str, *, conn=None) -> list[dict]:
    """Load only statements that were public by ``as_of_date``.

    Rows without an SEC-derived ``avail_date`` fail closed and are excluded.
    """
    own = conn is None
    conn = conn or connect_primary_db(readonly=True, timeout=120)
    try:
        cur = conn.execute(
            """SELECT ticker,period_end,period_type,revenue,operating_income,net_income,
                      eps,bps,roe,roa,per,pbr,opm,avail_date
                 FROM us_financial_data
                WHERE ticker=? AND avail_date IS NOT NULL AND avail_date<=?
                ORDER BY avail_date,period_end""",
            (ticker.upper(), as_of_date),
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, tuple(r))) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def _is_rebalance_session(i: int, sessions: Sequence[str], mode: str) -> bool:
    if mode == "daily" or i == 0:
        return True
    prev, now = sessions[i - 1], sessions[i]
    if mode == "month_start":
        return prev[:7] != now[:7]
    from datetime import date
    return date.fromisoformat(prev).isocalendar()[:2] != date.fromisoformat(now).isocalendar()[:2]


def _cost(config: USBacktestConfig, shares: float, price: float, side: str) -> float:
    commission = max(config.minimum_commission, abs(shares) * config.commission_per_share)
    sell_fee = abs(shares * price) * config.sell_notional_fee_bps / 10_000 if side == "sell" else 0.0
    return commission + sell_fee


def _metrics(curve: Sequence[dict], trades: Sequence[USTrade], initial_cash: float) -> dict:
    if not curve:
        return {}
    values = [float(x["equity"]) for x in curve]
    total = values[-1] / initial_cash - 1
    years = max((len(values) - 1) / 252.0, 1 / 252.0)
    cagr = (values[-1] / initial_cash) ** (1 / years) - 1 if values[-1] > 0 else -1.0
    peak, mdd = values[0], 0.0
    daily: list[float] = []
    for a, b in zip(values, values[1:]):
        if a > 0:
            daily.append(b / a - 1)
        peak = max(peak, b)
        if peak > 0:
            mdd = min(mdd, b / peak - 1)
    if len(daily) > 1:
        mean = sum(daily) / len(daily)
        variance = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
        sharpe = mean / math.sqrt(variance) * math.sqrt(252) if variance > 0 else None
    else:
        sharpe = None
    sells = [t for t in trades if t.side == "sell"]
    return {
        "total_return_pct": round(total * 100, 4),
        "cagr_pct": round(cagr * 100, 4),
        "max_drawdown_pct": round(mdd * 100, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "sessions": len(values),
        "orders_filled": len(trades),
        "sell_orders": len(sells),
        "ending_equity": round(values[-1], 2),
    }


def run_us_backtest(
    bars_by_ticker: Mapping[str, Sequence[USBar]], config: USBacktestConfig,
    signal_fn: SignalFunction, eligibility_fn: EligibilityFunction | None = None,
    *, eligibility_reference_complete: bool = True,
    security_outcomes: Sequence[USSecurityOutcome] = (),
) -> USBacktestResult:
    """Run a long-only, target-weight event simulation.

    The callback receives history ending on ``signal_date``.  Its targets are
    queued and can only execute on the next market session open.
    """
    by_day: dict[str, dict[str, USBar]] = {}
    histories: dict[str, list[USBar]] = {t: [] for t in bars_by_ticker}
    all_rows: list[USBar] = []
    for ticker, bars in bars_by_ticker.items():
        for b in sorted(bars, key=lambda x: x.date):
            all_rows.append(b)
            if config.start_date <= b.date <= config.end_date:
                by_day.setdefault(b.date, {})[ticker] = b
    sessions = sorted(by_day)
    if len(sessions) < 2:
        raise ValueError("at least two market sessions are required")

    # Warm-up history is available before session processing, while future
    # rows are appended exactly once at their close below.
    for ticker, bars in bars_by_ticker.items():
        histories[ticker] = [b for b in bars if b.date < config.start_date]

    cash = float(config.initial_cash)
    positions: dict[str, USPosition] = {}
    pending: tuple[str, list[USTarget]] | None = None
    trades: list[USTrade] = []
    curve: list[dict] = []
    rejected_missing_open = 0
    large_jumps: list[dict] = []
    pit_coverage: list[float] = []
    pit_missing_tickers: set[str] = set()
    last_close: dict[str, float] = {}
    applied_outcomes: list[dict] = []
    processed_outcomes: set[tuple[str, str]] = set()
    unmodeled_contingent_value = False

    for i, day in enumerate(sessions):
        today = by_day[day]

        # Apply verified merger/delisting consideration before new orders.
        # No fee or slippage is charged because this is a mandatory security
        # conversion rather than an exchange order.
        for outcome in (
            x for x in security_outcomes
            if x.effective_date <= day and (x.ticker, x.effective_date) not in processed_outcomes
        ):
            processed_outcomes.add((outcome.ticker, outcome.effective_date))
            position = positions.pop(outcome.ticker, None)
            if position is None:
                continue
            cash_value = position.shares * outcome.cash_per_share
            cash += cash_value
            successor_shares = position.shares * outcome.successor_shares_per_share
            if outcome.successor_ticker and successor_shares > 0:
                existing = positions.get(outcome.successor_ticker)
                if existing:
                    total = existing.shares + successor_shares
                    positions[outcome.successor_ticker] = USPosition(
                        outcome.successor_ticker, total, existing.entry_date,
                        existing.entry_price, existing.cost_basis + position.cost_basis,
                    )
                else:
                    positions[outcome.successor_ticker] = USPosition(
                        outcome.successor_ticker, successor_shares, position.entry_date,
                        position.entry_price, position.cost_basis,
                    )
            trades.append(USTrade(
                outcome.ticker, "corporate_action", outcome.effective_date, day,
                position.shares, outcome.cash_per_share, cash_value, 0.0,
                outcome.outcome_type,
            ))
            unmodeled_contingent_value |= outcome.contingent_value_unmodeled
            applied_outcomes.append(asdict(outcome))

        # Execute yesterday's close signal at today's open.
        if pending is not None:
            signal_date, targets = pending
            weights = {x.ticker: float(x.weight) for x in targets if x.weight > 0}
            if len(weights) > config.max_positions or sum(weights.values()) > 1.000001:
                raise ValueError("targets exceed max_positions or 100% weight")
            # At the execution event only today's open is known.  Using the
            # same day's close here would leak future information into sizing.
            mark = sum(p.shares * (today[t].open if t in today else histories[t][-1].close)
                       for t, p in positions.items() if histories.get(t))
            portfolio_value = cash + mark

            # Compute whole-share targets from opening prices, then sell down
            # before buying so the rebalance can reuse proceeds.
            desired: dict[str, float] = {}
            for ticker, weight in weights.items():
                bar = today.get(ticker)
                if bar is None:
                    rejected_missing_open += 1
                    continue
                px = bar.open * (1 + config.slippage_bps / 10_000)
                qty = portfolio_value * weight / px
                desired[ticker] = qty if config.allow_fractional_shares else math.floor(qty)

            for ticker in sorted(set(positions) | set(desired)):
                current = positions[ticker].shares if ticker in positions else 0.0
                wanted = desired.get(ticker, 0.0)
                if wanted >= current:
                    continue
                bar = today.get(ticker)
                if bar is None:
                    rejected_missing_open += 1
                    continue
                p = positions[ticker]
                shares = current - wanted
                px = bar.open * (1 - config.slippage_bps / 10_000)
                fee = _cost(config, shares, px, "sell")
                cash += shares * px - fee
                trades.append(USTrade(ticker, "sell", signal_date, day, shares, px,
                                      shares * px, fee, "rebalance"))
                if wanted <= 0:
                    positions.pop(ticker)
                else:
                    positions[ticker] = USPosition(ticker, wanted, p.entry_date,
                                                   p.entry_price, p.cost_basis * wanted / current)

            for ticker, wanted in sorted(desired.items()):
                current = positions[ticker].shares if ticker in positions else 0.0
                shares = wanted - current
                if shares <= 0:
                    continue
                bar = today.get(ticker)
                if bar is None:
                    continue
                px = bar.open * (1 + config.slippage_bps / 10_000)
                fee = _cost(config, shares, px, "buy")
                while shares > 0 and shares * px + fee > cash:
                    shares = shares - 1 if not config.allow_fractional_shares else max(0, shares - 0.001)
                    fee = _cost(config, shares, px, "buy")
                if shares <= 0:
                    continue
                cash -= shares * px + fee
                if current > 0:
                    old = positions[ticker]
                    new_total = current + shares
                    new_basis = old.cost_basis + shares * px + fee
                    positions[ticker] = USPosition(ticker, new_total, old.entry_date,
                                                   new_basis / new_total, new_basis)
                else:
                    positions[ticker] = USPosition(ticker, shares, day, px, shares * px + fee)
                trades.append(USTrade(ticker, "buy", signal_date, day, shares, px,
                                      shares * px, fee, "rebalance"))
            pending = None

        # Today's close becomes visible only after the open executions.
        for ticker, bar in today.items():
            prev = last_close.get(ticker)
            ratio = bar.close / prev if prev else None
            if ratio is not None and (
                ratio > config.large_jump_upper_ratio
                or ratio < config.large_jump_lower_ratio
            ):
                large_jumps.append({"ticker": ticker, "date": day,
                                    "ratio": round(ratio, 6)})
            last_close[ticker] = bar.close
            histories.setdefault(ticker, []).append(bar)

        equity = cash
        stale_positions: list[str] = []
        for ticker, p in positions.items():
            bar = today.get(ticker)
            if bar is None:
                stale_positions.append(ticker)
                close = histories[ticker][-1].close
            else:
                close = bar.close
            equity += p.shares * close
        curve.append({"date": day, "equity": round(equity, 6)})

        if i < len(sessions) - 1 and _is_rebalance_session(i, sessions, config.rebalance):
            eligible = eligibility_fn(day) if eligibility_fn else set(histories)
            signal_history = {
                k: tuple(v) for k, v in histories.items()
                if k in eligible and (not config.benchmark or k != config.benchmark)
                and v and v[-1].date == day
            }
            if eligibility_fn:
                expected = len(eligible - ({config.benchmark} if config.benchmark else set()))
                pit_coverage.append(len(signal_history) / expected if expected else 0.0)
                pit_missing_tickers.update(eligible - set(signal_history))
            targets = list(signal_fn(day, signal_history))
            pending = (day, targets)

    # Terminal marking is explicit.  We do not synthesize a next-day fill.
    # Any still-open position makes execution completeness false.
    open_positions = sorted(positions)
    fingerprint_payload = [
        (b.ticker, b.date, round(b.open, 8), round(b.close, 8), round(b.volume, 3))
        for b in sorted(all_rows, key=lambda x: (x.ticker, x.date))
        if b.date <= config.end_date
    ]
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, separators=(",", ":")).encode()).hexdigest()
    residual_survivorship_risk = (
        config.universe_mode != "point_in_time"
        or eligibility_fn is None
        or not eligibility_reference_complete
        or (min(pit_coverage) if pit_coverage else 0.0) < 0.98
    )
    research_grade = (
        config.universe_mode == "point_in_time"
        and eligibility_fn is not None
        and eligibility_reference_complete
        and (min(pit_coverage) if pit_coverage else 0.0) >= 0.98
        and not large_jumps
        and not unmodeled_contingent_value
        and rejected_missing_open == 0
        and not open_positions
    )
    metrics = _metrics(curve, trades, config.initial_cash)
    benchmark_bars = [
        b for b in bars_by_ticker.get(config.benchmark, ())
        if config.start_date <= b.date <= config.end_date
    ] if config.benchmark else []
    if len(benchmark_bars) >= 2 and benchmark_bars[0].close > 0:
        benchmark_return = benchmark_bars[-1].close / benchmark_bars[0].close - 1
        metrics["benchmark"] = config.benchmark
        metrics["benchmark_return_pct"] = round(benchmark_return * 100, 4)
        metrics["excess_return_pct"] = round(metrics["total_return_pct"] - benchmark_return * 100, 4)

    quality = {
        "engine_version": ENGINE_VERSION,
        "price_source": PRICE_SOURCE,
        "signal_timing": "close_D",
        "execution_timing": "next_market_session_open",
        "fundamental_timing": "SEC avail_date; null fails closed",
        "benchmark": config.benchmark or None,
        "benchmark_available": len(benchmark_bars) >= 2,
        "universe_mode": config.universe_mode,
        "pit_universe_applied": eligibility_fn is not None,
        "pit_reference_complete": eligibility_reference_complete if eligibility_fn else None,
        "pit_price_coverage_min": round(min(pit_coverage), 6) if pit_coverage else None,
        "pit_price_coverage_mean": round(sum(pit_coverage) / len(pit_coverage), 6) if pit_coverage else None,
        "pit_missing_ticker_count": len(pit_missing_tickers),
        "pit_missing_tickers": sorted(pit_missing_tickers)[:500],
        "survivorship_bias": residual_survivorship_risk,
        "invalid_ohlc_policy": "excluded",
        "large_jump_events": large_jumps[:100],
        "large_jump_count": len(large_jumps),
        "large_jump_bounds": {
            "lower_ratio": config.large_jump_lower_ratio,
            "upper_ratio": config.large_jump_upper_ratio,
        },
        "missing_open_rejections": rejected_missing_open,
        "security_outcomes_loaded": len(security_outcomes),
        "security_outcomes_applied": len(applied_outcomes),
        "applied_security_outcomes": applied_outcomes,
        "unmodeled_contingent_value": unmodeled_contingent_value,
        "open_positions_at_end": open_positions,
        "execution_complete": not open_positions,
        "research_grade": research_grade,
    }
    return USBacktestResult(
        run_id=f"usbt_{uuid.uuid4().hex}",
        config=asdict(config),
        metrics=metrics,
        trades=[asdict(x) for x in trades],
        equity_curve=curve,
        quality=quality,
        data_fingerprint=fingerprint,
    )


def momentum_signal(top_n: int = 5, lookback: int = 126, min_history: int = 200) -> SignalFunction:
    """Create an equal-weight close-to-close momentum strategy callback."""
    if top_n <= 0 or lookback <= 0:
        raise ValueError("top_n and lookback must be positive")

    def signal(_day: str, histories: Mapping[str, Sequence[USBar]]) -> Sequence[USTarget]:
        ranked: list[tuple[float, str]] = []
        for ticker, bars in histories.items():
            if len(bars) < max(min_history, lookback + 1):
                continue
            now, before = bars[-1].close, bars[-lookback - 1].close
            ma200 = sum(x.close for x in bars[-200:]) / 200
            if before > 0 and now > ma200:
                ranked.append((now / before - 1, ticker))
        chosen = [t for _, t in sorted(ranked, reverse=True)[:top_n]]
        return [USTarget(t, 1 / top_n) for t in chosen]

    return signal


def persist_us_backtest(result: USBacktestResult, *, conn=None) -> None:
    """Persist a completed run in US-only tables, atomically."""
    own = conn is None
    conn = conn or connect_primary_db(timeout=120)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS us_backtest_runs (
            run_id TEXT PRIMARY KEY, engine_version TEXT NOT NULL, strategy TEXT NOT NULL,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL, status TEXT NOT NULL,
            config_json TEXT NOT NULL, metrics_json TEXT NOT NULL, quality_json TEXT NOT NULL,
            data_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS us_backtest_trades (
            run_id TEXT NOT NULL, seq INTEGER NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL,
            signal_date TEXT NOT NULL, execution_date TEXT NOT NULL, shares DOUBLE PRECISION NOT NULL,
            price DOUBLE PRECISION NOT NULL, gross_notional DOUBLE PRECISION NOT NULL,
            fees DOUBLE PRECISION NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(run_id,seq))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS us_backtest_equity (
            run_id TEXT NOT NULL, date TEXT NOT NULL, equity DOUBLE PRECISION NOT NULL,
            PRIMARY KEY(run_id,date))""")
        strategy = str(result.config.get("strategy") or "callback")
        conn.execute("""INSERT INTO us_backtest_runs
            (run_id,engine_version,strategy,start_date,end_date,status,config_json,metrics_json,
             quality_json,data_fingerprint,created_at) VALUES(?,?,?,?,?,'done',?,?,?,?,?)""",
            (result.run_id, ENGINE_VERSION, strategy, result.config["start_date"], result.config["end_date"],
             json.dumps(result.config, sort_keys=True), json.dumps(result.metrics, sort_keys=True),
             json.dumps(result.quality, sort_keys=True), result.data_fingerprint, result.created_at))
        conn.executemany("""INSERT INTO us_backtest_trades
            (run_id,seq,ticker,side,signal_date,execution_date,shares,price,gross_notional,fees,reason)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", [
                (result.run_id, i, t["ticker"], t["side"], t["signal_date"], t["execution_date"],
                 t["shares"], t["price"], t["gross_notional"], t["fees"], t["reason"])
                for i, t in enumerate(result.trades)
            ])
        conn.executemany("INSERT INTO us_backtest_equity(run_id,date,equity) VALUES(?,?,?)", [
            (result.run_id, x["date"], x["equity"]) for x in result.equity_curve
        ])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the US next-open momentum baseline")
    parser.add_argument("--start", default="2022-01-03")
    parser.add_argument("--end", default="2026-09-25")
    parser.add_argument("--index", default="S&P500")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--universe-mode", choices=("pit", "current"), default="pit")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    conn = connect_primary_db(readonly=not args.persist, timeout=120)
    try:
        interval_meta = None
        intervals = None
        if args.universe_mode == "pit":
            intervals, interval_meta = load_us_membership_intervals(args.start, args.end, args.index, conn=conn)
            tickers = sorted({x[0] for x in intervals})
        else:
            tickers = load_current_us_universe(args.index, conn=conn)
        outcomes = load_us_security_outcomes(tickers, conn=conn)
        tickers.extend(x.successor_ticker for x in outcomes if x.successor_ticker)
        tickers = sorted(set(tickers))
        if args.limit:
            tickers = tickers[:args.limit]
        if "SPY" not in tickers:
            tickers.append("SPY")
        bars, load_quality = load_us_bars(tickers, args.start, args.end, conn=conn)
    finally:
        conn.close()
    config = USBacktestConfig(
        start_date=args.start, end_date=args.end, max_positions=args.top,
        universe_name=args.index,
        universe_mode="point_in_time" if args.universe_mode == "pit" else "current_membership",
    )
    eligible_fn = membership_eligibility(intervals) if intervals is not None else None
    reference_complete = bool(
        interval_meta
        and str(interval_meta["first_date"]) <= args.start
        and interval_meta["covers_end"]
    ) if eligible_fn else True
    result = run_us_backtest(
        bars, config, momentum_signal(args.top), eligible_fn,
        eligibility_reference_complete=reference_complete,
        security_outcomes=outcomes,
    )
    result.config["strategy"] = f"momentum_{126}d_top{args.top}_ma200"
    result.quality["load"] = load_quality
    if interval_meta:
        result.quality["membership_reference"] = interval_meta
    if args.persist:
        persist_us_backtest(result)
    print(json.dumps({
        "run_id": result.run_id, "metrics": result.metrics, "quality": result.quality,
        "data_fingerprint": result.data_fingerprint,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
