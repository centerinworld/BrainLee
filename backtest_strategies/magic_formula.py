"""Point-in-time Greenblatt Magic Formula research strategy."""

import bisect
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional

from backtest_common import (
    DB_PATH,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    sqlite3,
)


def run_backtest_magic_formula(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 20,
    per_stock: float = 5_000_000,
    min_mktcap_억: float = 300.0,
    top_pct: float = 0.20,
    stop: float = -0.20,
    trail: float = -0.25,
    trail_activate_pct: float = 0.15,
    max_hold: int = 252,
    cooldown_days: int = 63,
    run_name: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """Buy high earnings-yield/high capital-return stocks at the next open.

    The Korean statements do not expose a consistently populated historical
    interest-bearing-debt field. Enterprise value is therefore conservatively
    approximated as market cap + total liabilities - cash, and capital employed
    as total assets - cash. Rankings are rebuilt at each month-end using only
    annual statements whose ``avail_date`` is already known on that date.
    """
    init_backtest_db()
    run_name = run_name or f"V-MAGIC-FORMULA {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id,
        "magic_formula",
        "magic_formula_v1_20260904",
        {
            "total_capital": total_capital,
            "max_positions": max_positions,
            "per_stock": per_stock,
            "min_mktcap_억": min_mktcap_억,
            "top_pct": top_pct,
            "stop": stop,
            "trail": trail,
            "trail_activate_pct": trail_activate_pct,
            "max_hold": max_hold,
            "cooldown_days": cooldown_days,
            "start": start_date,
            "end": end_date,
        },
        signal_timing="close_D",
        execution_timing="next_open",
        market_cap_mode="asof_approx",
        allocation_rule="fixed_slot",
        universe_version="security_master_history_v1_mixed_approx",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute(
        """
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'magic_formula',?,?,?,?,'running')
        """,
        (run_id, run_name, start_date, end_date, per_stock, max_positions),
    )
    conn.commit()

    try:
        warmup_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=450)
        ).strftime("%Y-%m-%d")

        annual_rows = conn.execute(
            """
            SELECT f.stock_code, f.year, f.revenue, f.operating_profit,
                   f.total_assets, f.total_liabilities, f.total_equity,
                   COALESCE(f.cash, 0),
                   COALESCE(d.avail_date, printf('%d-03-31', f.year+1)) AS avail_date,
                   f.report_type
            FROM financial_data f
            LEFT JOIN fin_disclosure_dates d
              ON d.stock_code=f.stock_code AND d.year=f.year
             AND d.quarter=4 AND d.is_annual=1
            WHERE f.is_annual=1 AND f.year BETWEEN 2016 AND 2025
            ORDER BY f.stock_code, f.year,
                     CASE f.report_type WHEN 'CFS' THEN 0 ELSE 1 END
            """
        ).fetchall()
        annual_by_code: Dict[str, list] = defaultdict(list)
        seen = set()
        for row in annual_rows:
            key = (row[0], int(row[1]))
            if key in seen:
                continue
            seen.add(key)
            annual_by_code[row[0]].append(
                {
                    "year": int(row[1]),
                    "revenue": float(row[2] or 0),
                    "operating_profit": float(row[3] or 0),
                    "assets": float(row[4] or 0),
                    "liabilities": float(row[5] or 0),
                    "equity": float(row[6] or 0),
                    "cash": float(row[7] or 0),
                    "avail_date": str(row[8])[:10],
                    "report_type": row[9],
                }
            )
        for rows in annual_by_code.values():
            rows.sort(key=lambda x: (x["avail_date"], x["year"]))

        # Current sector labels are used only to avoid incomparable financial
        # company balance sheets; tradability itself is checked point-in-time.
        finance_codes = {
            row[0]
            for row in conn.execute(
                "SELECT stock_code FROM stock_universe WHERE sector_large='금융'"
            ).fetchall()
        }

        master_intervals: Dict[str, list] = defaultdict(list)
        for code, effective_from, effective_to, market, tradable, is_etf_etn in conn.execute(
            """
            SELECT stock_code,effective_from,effective_to,market,is_tradable,is_etf_etn
            FROM security_master_history
            WHERE market IN ('KOSPI','KOSDAQ')
            ORDER BY stock_code,effective_from
            """
        ):
            master_intervals[code].append(
                (effective_from, effective_to, market, int(tradable or 0), int(is_etf_etn or 0))
            )

        share_intervals: Dict[str, list] = defaultdict(list)
        for code, effective_from, effective_to, shares, quality in conn.execute(
            """
            SELECT stock_code,effective_from,effective_to,shares_issued,quality
            FROM security_share_history
            ORDER BY stock_code,effective_from
            """
        ):
            share_intervals[code].append(
                (effective_from, effective_to, float(shares or 0), quality)
            )

        def _tradable_asof(code: str, day: str) -> bool:
            for effective_from, effective_to, _market, tradable, is_etf_etn in reversed(
                master_intervals.get(code, [])
            ):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return bool(tradable and not is_etf_etn)
            return False

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(
                share_intervals.get(code, [])
            ):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        codes = sorted(set(annual_by_code) & set(master_intervals) - finance_codes)
        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute(
                """
                SELECT date,close,COALESCE(open,close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
                """,
                (code, warmup_start, end_date),
            ).fetchall()
            if len(rows) < 80:
                continue
            closes = [float(row[1]) for row in rows]
            if any(
                closes[i - 1] > 0
                and (closes[i] / closes[i - 1] < 0.45 or closes[i] / closes[i - 1] > 2.2)
                for i in range(1, len(closes))
            ):
                continue
            sd[code] = {
                "d": [str(row[0])[:10] for row in rows],
                "c": closes,
                "o": [float(row[2]) for row in rows],
            }
        didx = {code: {day: i for i, day in enumerate(data["d"])} for code, data in sd.items()}

        market_dates = [
            str(row[0])[:10]
            for row in conn.execute(
                """
                SELECT date FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date
                """,
                (start_date, end_date),
            ).fetchall()
        ]
        if not market_dates:
            market_dates = sorted(
                {day for data in sd.values() for day in data["d"] if start_date <= day <= end_date}
            )
        month_ends = []
        for idx, day in enumerate(market_dates):
            if idx + 1 == len(market_dates) or market_dates[idx + 1][:7] != day[:7]:
                month_ends.append(day)
        signal_days = set(month_ends)

        def _latest_annual(code: str, day: str):
            rows = annual_by_code.get(code, [])
            if not rows:
                return None
            dates = [row["avail_date"] for row in rows]
            idx = bisect.bisect_right(dates, day) - 1
            return rows[idx] if idx >= 0 else None

        candidate_pool: Dict[str, list] = {}
        candidate_meta: Dict[tuple, dict] = {}
        for day in month_ends:
            scored = []
            for code, data in sd.items():
                if not _tradable_asof(code, day):
                    continue
                i = didx[code].get(day)
                if i is None:
                    continue
                fin = _latest_annual(code, day)
                if not fin or fin["operating_profit"] <= 0 or fin["assets"] <= 0:
                    continue
                shares = _shares_asof(code, day)
                market_cap = data["c"][i] * shares
                if market_cap < min_mktcap_억 * 100_000_000:
                    continue
                enterprise_value = market_cap + fin["liabilities"] - fin["cash"]
                capital_employed = fin["assets"] - fin["cash"]
                if enterprise_value <= 0 or capital_employed <= 0:
                    continue
                earnings_yield = fin["operating_profit"] / enterprise_value
                capital_return = fin["operating_profit"] / capital_employed
                if not (0 < earnings_yield < 2 and 0 < capital_return < 2):
                    continue
                scored.append(
                    {
                        "code": code,
                        "year": fin["year"],
                        "earnings_yield": earnings_yield,
                        "capital_return": capital_return,
                        "mktcap_억": market_cap / 100_000_000,
                    }
                )
            if not scored:
                continue
            ey_rank = {
                row["code"]: rank
                for rank, row in enumerate(
                    sorted(scored, key=lambda x: (x["earnings_yield"], x["code"]), reverse=True)
                )
            }
            cr_rank = {
                row["code"]: rank
                for rank, row in enumerate(
                    sorted(scored, key=lambda x: (x["capital_return"], x["code"]), reverse=True)
                )
            }
            keep = max(max_positions, int(len(scored) * top_pct))
            selected = sorted(
                scored,
                key=lambda x: (ey_rank[x["code"]] + cr_rank[x["code"]], x["code"]),
            )[:keep]
            candidate_pool[day] = [row["code"] for row in selected]
            for rank, row in enumerate(selected, 1):
                row["combined_rank"] = rank
                candidate_meta[(day, row["code"])] = row

        cash = total_capital
        positions: Dict[str, dict] = {}
        trades = []
        pending_buys = []
        pending_sells: Dict[str, str] = {}
        last_exit_index: Dict[str, int] = {}

        for day_index, day in enumerate(market_dates):
            for code, reason in list(pending_sells.items()):
                if code not in positions:
                    del pending_sells[code]
                    continue
                i = didx[code].get(day)
                if i is None:
                    continue
                fill = sd[code]["o"][i]
                position = positions.pop(code)
                pnl, net_pct = _net_profit(
                    position["entry"], fill, position["shares"], position["mktcap_억"]
                )
                cash += position["shares"] * position["entry"] + pnl
                trades.append(
                    {
                        "code": code,
                        "buy_date": position["buy_date"],
                        "sell_date": day,
                        "entry": position["entry"],
                        "exit": fill,
                        "pnl_pct": net_pct,
                        "pnl": round(pnl),
                        "reason": reason,
                        "signal_year": position["signal_year"],
                        "earnings_yield_pct": round(position["earnings_yield"] * 100, 2),
                        "capital_return_pct": round(position["capital_return"] * 100, 2),
                        "combined_rank": position["combined_rank"],
                    }
                )
                last_exit_index[code] = day_index
                del pending_sells[code]

            slots = max_positions - len(positions)
            if slots > 0:
                deferred = []
                for signal_day, code in pending_buys:
                    if code in positions or code in pending_sells:
                        continue
                    if (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(signal_day, "%Y-%m-%d")).days > 10:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        deferred.append((signal_day, code))
                        continue
                    if day <= signal_day:
                        deferred.append((signal_day, code))
                        continue
                    if day_index - last_exit_index.get(code, -10_000) < cooldown_days:
                        continue
                    fill = sd[code]["o"][i]
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // fill) if fill > 0 else 0
                    if shares <= 0 or slots <= 0:
                        continue
                    meta = candidate_meta[(signal_day, code)]
                    cash -= shares * fill
                    positions[code] = {
                        "entry": fill,
                        "shares": shares,
                        "buy_date": day,
                        "hold": 0,
                        "peak": fill,
                        "mktcap_억": meta["mktcap_억"],
                        "signal_year": meta["year"],
                        "earnings_yield": meta["earnings_yield"],
                        "capital_return": meta["capital_return"],
                        "combined_rank": meta["combined_rank"],
                    }
                    slots -= 1
                    if slots <= 0:
                        break
                pending_buys = deferred

            for code, position in list(positions.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                close = sd[code]["c"][i]
                position["hold"] += 1
                position["peak"] = max(position["peak"], close)
                ret = close / position["entry"] - 1
                stop_hit = ret <= stop
                trail_hit = (
                    ret >= trail_activate_pct
                    and close / position["peak"] - 1 <= trail
                )
                expired = position["hold"] >= max_hold
                if stop_hit or trail_hit or expired:
                    pending_sells.setdefault(
                        code, "stop" if stop_hit else "trail" if trail_hit else "expire"
                    )

            if day in signal_days:
                pending_buys = [
                    (day, code)
                    for code in candidate_pool.get(day, [])
                    if code not in positions and code not in pending_sells
                ]

        last_day = market_dates[-1] if market_dates else end_date
        for code, position in list(positions.items()):
            i = didx[code].get(last_day)
            close = sd[code]["c"][i] if i is not None else position["entry"]
            pnl, net_pct = _net_profit(
                position["entry"], close, position["shares"], position["mktcap_억"]
            )
            cash += position["shares"] * position["entry"] + pnl
            trades.append(
                {
                    "code": code,
                    "buy_date": position["buy_date"],
                    "sell_date": last_day,
                    "entry": position["entry"],
                    "exit": close,
                    "pnl_pct": net_pct,
                    "pnl": round(pnl),
                    "reason": "final",
                    "signal_year": position["signal_year"],
                    "earnings_yield_pct": round(position["earnings_yield"] * 100, 2),
                    "capital_return_pct": round(position["capital_return"] * 100, 2),
                    "combined_rank": position["combined_rank"],
                }
            )

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (
            sum(1 for trade in trades if trade["pnl_pct"] > 0) / len(trades) * 100
            if trades
            else 0.0
        )
        conn.execute(
            """
            UPDATE backtest_runs
            SET status='done',total_return_pct=?,total_trades=?,win_rate=?,trades_json=?
            WHERE run_id=?
            """,
            (
                round(total_return, 2),
                len(trades),
                round(win_rate, 1),
                json.dumps({"trades": trades}, ensure_ascii=False),
                run_id,
            ),
        )
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=True)
        return run_id
    except Exception as exc:
        import traceback

        detail = f"{exc}\n{traceback.format_exc()}"
        try:
            conn.execute(
                "UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                (detail, run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        raise
