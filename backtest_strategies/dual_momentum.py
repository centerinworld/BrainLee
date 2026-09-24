"""Point-in-time Dual Momentum (Antonacci-style) research strategy.

Relative momentum ranks the eligible universe by trailing ret_120d and buys the
top slice; absolute momentum gates the whole book on KOSPI's own trailing
ret_120d — when the market's own momentum turns negative, no new positions are
opened and existing positions are force-exited toward cash, mirroring the
"flee to cash" mechanic of classic dual momentum (Antonacci GEM), adapted here
to single-stock selection since this engine trades individual KOSPI/KOSDAQ
names rather than asset classes.
"""

import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional

from backtest_common import (
    DB_PATH,
    _final_liquidation_quote_for_code,
    _net_profit,
    _max_drawdown_pct,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    sqlite3,
)


def run_backtest_dual_momentum(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 20,
    per_stock: float = 5_000_000,
    min_mktcap_억: float = 300.0,
    top_pct: float = 0.20,
    lookback_days: int = 120,
    stop: float = -0.20,
    trail: float = -0.25,
    trail_activate_pct: float = 0.15,
    max_hold: int = 252,
    cooldown_days: int = 63,
    run_name: Optional[str] = None,
    run_id: Optional[str] = None,
    data_asof_ts: Optional[str] = None,
) -> str:
    """Buy the top relative-momentum quintile at the next open, gated by KOSPI absolute momentum."""
    init_backtest_db()
    effective_data_asof_ts = data_asof_ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_name = run_name or f"V-DUAL-MOMENTUM {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id,
        "dual_momentum",
        "dual_momentum_v1_20260906",
        {
            "total_capital": total_capital,
            "max_positions": max_positions,
            "per_stock": per_stock,
            "min_mktcap_억": min_mktcap_억,
            "top_pct": top_pct,
            "lookback_days": lookback_days,
            "factors": ["ret_120d_relative_momentum", "kospi_ret_120d_absolute_gate"],
            "stop": stop,
            "trail": trail,
            "trail_activate_pct": trail_activate_pct,
            "max_hold": max_hold,
            "cooldown_days": cooldown_days,
            "start": start_date,
            "end": end_date,
            "data_asof_ts": effective_data_asof_ts,
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
        VALUES (?,?,'dual_momentum',?,?,?,?,'running')
        """,
        (run_id, run_name, start_date, end_date, per_stock, max_positions),
    )
    conn.commit()

    try:
        master_intervals: Dict[str, list] = defaultdict(list)
        for row in conn.execute(
            """
            SELECT stock_code,effective_from,effective_to,is_tradable,is_etf_etn
            FROM security_master_history
            WHERE market IN ('KOSPI','KOSDAQ')
            ORDER BY stock_code,effective_from
            """
        ):
            master_intervals[row[0]].append((row[1], row[2], int(row[3] or 0), int(row[4] or 0)))

        def tradable_asof(code: str, day: str) -> bool:
            for effective_from, effective_to, tradable, is_etf_etn in reversed(master_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return bool(tradable and not is_etf_etn)
            return False

        snapshot_rows = conn.execute(
            """
            SELECT snapshot_date,stock_code,market_cap_억,ret_120d
            FROM strategy_feature_snapshot
            WHERE snapshot_date>=? AND snapshot_date<=?
              AND market_cap_억>=? AND ret_120d IS NOT NULL AND ret_120d BETWEEN -0.8 AND 5.0
            ORDER BY snapshot_date,stock_code
            """,
            (start_date, end_date, min_mktcap_억),
        ).fetchall()
        raw_by_day: Dict[str, list] = defaultdict(list)
        for day, code, market_cap, momentum in snapshot_rows:
            day = str(day)[:10]
            momentum = float(momentum)
            if momentum <= 0 or not tradable_asof(code, day):
                continue
            raw_by_day[day].append({
                "code": code,
                "mktcap_억": float(market_cap),
                "momentum": momentum,
            })

        candidate_pool: Dict[str, list] = {}
        candidate_meta: Dict[tuple, dict] = {}
        for day, rows in raw_by_day.items():
            if len(rows) < 30:
                continue
            rows.sort(key=lambda row: (-row["momentum"], row["code"]))
            keep = max(max_positions, int(len(rows) * top_pct))
            selected = rows[:keep]
            candidate_pool[day] = [row["code"] for row in selected]
            for rank, row in enumerate(selected, 1):
                row["candidate_rank"] = rank
                candidate_meta[(day, row["code"])] = row

        codes = sorted({code for candidates in candidate_pool.values() for code in candidates})
        sd = {}
        for code in codes:
            rows = conn.execute(
                """
                SELECT substr(date,1,10),close,COALESCE(open,close)
                FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
                """,
                (code, start_date, end_date),
            ).fetchall()
            if not rows:
                continue
            closes = [float(row[1]) for row in rows]
            if any(closes[i - 1] > 0 and not 0.45 <= closes[i] / closes[i - 1] <= 2.2 for i in range(1, len(closes))):
                continue
            sd[code] = {"d": [row[0] for row in rows], "c": closes, "o": [float(row[2]) for row in rows]}
        didx = {code: {day: i for i, day in enumerate(data["d"])} for code, data in sd.items()}

        lookback_start = (
            datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=int(lookback_days * 1.8) + 30)
        ).strftime("%Y-%m-%d")
        market_rows = conn.execute(
            """SELECT substr(date,1,10),close FROM price_history
               WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0 ORDER BY date""",
            (lookback_start, end_date),
        ).fetchall()
        market_all_dates = [str(row[0])[:10] for row in market_rows]
        market_all_closes = [float(row[1]) for row in market_rows]
        market_abs_mom: Dict[str, float] = {}
        for i in range(len(market_all_dates)):
            if i >= lookback_days and market_all_closes[i - lookback_days] > 0:
                market_abs_mom[market_all_dates[i]] = market_all_closes[i] / market_all_closes[i - lookback_days] - 1.0
        market_dates = [day for day in market_all_dates if start_date <= day <= end_date]
        if not market_dates:
            market_dates = sorted({day for data in sd.values() for day in data["d"]})

        cash = total_capital
        positions: Dict[str, dict] = {}
        trades, pending_buys = [], []
        pending_sells: Dict[str, str] = {}
        last_exit_index: Dict[str, int] = {}
        equity_curve = []
        for day_index, day in enumerate(market_dates):
            gate_negative = market_abs_mom.get(day, 0.0) <= 0

            for code, reason in list(pending_sells.items()):
                i = didx.get(code, {}).get(day)
                if code not in positions or i is None:
                    continue
                fill, position = sd[code]["o"][i], positions.pop(code)
                pnl, net_pct = _net_profit(position["entry"], fill, position["shares"], position["mktcap_억"])
                cash += position["shares"] * position["entry"] + pnl
                trades.append({
                    "code": code, "buy_date": position["buy_date"], "sell_date": day,
                    "entry": position["entry"], "exit": fill, "pnl_pct": net_pct,
                    "pnl": round(pnl), "reason": reason,
                    "candidate_rank": position["candidate_rank"],
                })
                last_exit_index[code] = day_index
                del pending_sells[code]

            slots, deferred = max_positions - len(positions), []
            if not gate_negative:
                for signal_day, code in pending_buys:
                    if code in positions or code in pending_sells:
                        continue
                    if (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(signal_day, "%Y-%m-%d")).days > 10:
                        continue
                    i = didx.get(code, {}).get(day)
                    if i is None or day <= signal_day:
                        deferred.append((signal_day, code))
                        continue
                    if day_index - last_exit_index.get(code, -10_000) < cooldown_days:
                        continue
                    fill = sd[code]["o"][i]
                    shares = int(min(per_stock, cash * 0.99) // fill) if fill > 0 else 0
                    if shares <= 0 or slots <= 0:
                        continue
                    meta = candidate_meta[(signal_day, code)]
                    cash -= shares * fill
                    positions[code] = {
                        "entry": fill, "shares": shares, "buy_date": day, "hold": 0, "peak": fill,
                        "mktcap_억": meta["mktcap_억"], "candidate_rank": meta["candidate_rank"],
                    }
                    slots -= 1
                    if slots <= 0:
                        break
            pending_buys = deferred

            for code, position in list(positions.items()):
                i = didx.get(code, {}).get(day)
                if i is None:
                    continue
                close = sd[code]["c"][i]
                position["hold"] += 1
                position["peak"] = max(position["peak"], close)
                ret = close / position["entry"] - 1
                stop_hit = ret <= stop
                trail_hit = ret >= trail_activate_pct and close / position["peak"] - 1 <= trail
                expired = position["hold"] >= max_hold
                if stop_hit or trail_hit or expired:
                    pending_sells.setdefault(code, "stop" if stop_hit else "trail" if trail_hit else "expire")
                elif gate_negative:
                    pending_sells.setdefault(code, "abs_momentum_gate")

            if not gate_negative and day in candidate_pool:
                pending_buys = [(day, code) for code in candidate_pool[day] if code not in positions]
            equity_curve.append({
                "date": day,
                "equity": round(cash + sum(
                    position["shares"] * (
                        sd[code]["c"][didx[code][day]] if day in didx[code] else position["entry"]
                    )
                    for code, position in positions.items()
                )),
            })

        last_day = market_dates[-1] if market_dates else end_date
        for code, position in positions.items():
            close, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx.get(code, {}), sd[code]["c"])
            pnl, net_pct = _net_profit(position["entry"], close, position["shares"], position["mktcap_억"])
            cash += position["shares"] * position["entry"] + pnl
            trades.append({
                "code": code, "buy_date": position["buy_date"], "sell_date": last_day,
                "entry": position["entry"], "exit": close, "pnl_pct": net_pct,
                "pnl": round(pnl), "reason": final_reason,
                "candidate_rank": position["candidate_rank"],
            })

        total_return = (cash - total_capital) / total_capital * 100
        max_drawdown = _max_drawdown_pct([point["equity"] for point in equity_curve])
        win_rate = sum(trade["pnl_pct"] > 0 for trade in trades) / len(trades) * 100 if trades else 0.0
        conn.execute(
            """UPDATE backtest_runs SET status='done',total_return_pct=?,total_trades=?,win_rate=?,max_drawdown_pct=?,trades_json=?
               WHERE run_id=?""",
            (round(total_return, 2), len(trades), round(win_rate, 1), max_drawdown,
             json.dumps({"trades": trades, "equity_curve": equity_curve}, ensure_ascii=False), run_id),
        )
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=True)
        return run_id
    except Exception as exc:
        import traceback

        try:
            conn.execute(
                "UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                (f"{exc}\n{traceback.format_exc()}", run_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        raise
