"""
segment_revenue_divergence.py -- run_backtest_segment_revenue_divergence()
Split out of backtest.py on 2026-09-03. Pure relocation, no logic changed.
"""
import json
import uuid
import math
import re
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from backtest_common import (
    DB_PATH,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_segment_revenue_divergence(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 12,
    per_stock: float = 8_000_000,
    segment_growth_min: float = 0.35,
    consolidated_growth_min: float = -0.10,
    consolidated_growth_max: float = 0.25,
    min_divergence_gap: float = 0.20,
    min_segment_revenue_억: float = 150.0,
    min_segment_share: float = 0.12,
    stop: float = -0.18,
    trail: float = -0.25,
    trail_activate_pct: float = 0.10,
    max_hold: int = 365,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-SEGDIVERGENCE — 연결 매출은 평평하지만 특정 사업부 매출이 강하게 성장하는
    "숨은 엔진" 이벤트를 사업보고서 공개 직후 매수하는 독립 전략.
    """
    init_backtest_db()
    run_name = run_name or f"V-SEGDIVERGENCE {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "segment_revenue_divergence", "segment_revenue_divergence_v1_20260902",
        {
            "segment_growth_min": segment_growth_min,
            "consolidated_growth_min": consolidated_growth_min,
            "consolidated_growth_max": consolidated_growth_max,
            "min_divergence_gap": min_divergence_gap,
            "min_segment_revenue_억": min_segment_revenue_억,
            "min_segment_share": min_segment_share,
            "stop": stop,
            "trail": trail,
            "trail_activate_pct": trail_activate_pct,
            "max_hold": max_hold,
            "max_positions": max_positions,
            "per_stock": per_stock,
            "total_capital": total_capital,
            "start": start_date,
            "end": end_date,
        },
        signal_timing="close_D",
        execution_timing="next_open",
        market_cap_mode="not_applicable",
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'segment_revenue_divergence',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=420)).strftime("%Y-%m-%d")
        skip_exact = {
            "", "합계", "합 계", "총계", "총 계", "소계", "소 계", "계",
            "내수", "내 수", "국내", "국 내", "수출", "수 출", "기타", "상품",
            "종속회사", "자본",
        }

        def _is_valid_segment_name(name: str) -> bool:
            s = str(name or "").strip()
            # DART labels may contain arbitrary whitespace (for example, "내    수").
            normalized = re.sub(r"\s+", "", s)
            if not s or s in skip_exact or normalized in {re.sub(r"\s+", "", x) for x in skip_exact}:
                return False
            bad_parts = ("수익", "이익", "조정", "제거", "연결", "내부거래", "전사", "공통")
            return not any(part in s for part in bad_parts)

        annual_rev_rows = conn.execute("""
            SELECT stock_code, year, revenue, report_type, quarter
            FROM financial_data
            WHERE is_annual=1 AND revenue IS NOT NULL AND revenue > 0
            ORDER BY stock_code, year,
                     CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END,
                     CASE WHEN quarter=0 THEN 0 ELSE 1 END,
                     quarter DESC
        """).fetchall()
        annual_rev: Dict[str, list] = {}
        seen_annual = set()
        for code, year, revenue, report_type, quarter in annual_rev_rows:
            key = (code, year)
            if key in seen_annual:
                continue
            seen_annual.add(key)
            annual_rev.setdefault(code, []).append({
                "year": int(year),
                "revenue_억": float(revenue) / 100_000_000.0,
                "report_type": report_type,
                "quarter": int(quarter or 0),
            })
        for code in annual_rev:
            annual_rev[code].sort(key=lambda x: x["year"])

        seg_rows = conn.execute("""
            SELECT stock_code, year, quarter, segment_name, revenue, report_type
            FROM segment_revenue
            WHERE revenue IS NOT NULL AND revenue > 0
            ORDER BY stock_code, year, segment_name,
                     CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END,
                     CASE WHEN quarter=0 THEN 0 ELSE 1 END,
                     quarter DESC
        """).fetchall()
        seg_by_code_year: Dict[tuple, list] = {}
        seen_seg = set()
        for code, year, quarter, seg_name, revenue, report_type in seg_rows:
            key = (code, year, seg_name)
            if key in seen_seg:
                continue
            seen_seg.add(key)
            seg_by_code_year.setdefault((code, int(year)), []).append({
                "segment_name": str(seg_name or "").strip(),
                "revenue_억": float(revenue),
                "report_type": report_type,
                "quarter": int(quarter or 0),
            })

        segment_history: Dict[tuple, list] = {}
        for (code, year), items in seg_by_code_year.items():
            for item in items:
                segment_history.setdefault((code, item["segment_name"]), []).append((year, item["revenue_억"]))
        prev_seg_rev: Dict[tuple, float] = {}
        for key, hist in segment_history.items():
            hist.sort(key=lambda x: x[0])
            for idx in range(1, len(hist)):
                prev_seg_rev[(key[0], hist[idx][0], key[1])] = hist[idx - 1][1]

        events: list = []
        for code, rows in annual_rev.items():
            prev_total = None
            for row in rows:
                year = row["year"]
                total_rev = row["revenue_억"]
                if prev_total and prev_total > 0:
                    total_yoy = total_rev / prev_total - 1.0
                    if consolidated_growth_min <= total_yoy <= consolidated_growth_max:
                        seg_candidates = []
                        for seg in seg_by_code_year.get((code, year), []):
                            seg_name = seg["segment_name"]
                            seg_rev = seg["revenue_억"]
                            if not _is_valid_segment_name(seg_name):
                                continue
                            prev_rev = prev_seg_rev.get((code, year, seg_name))
                            if not prev_rev or prev_rev <= 0:
                                continue
                            seg_yoy = seg_rev / prev_rev - 1.0
                            share = seg_rev / total_rev if total_rev > 0 else 0.0
                            if seg_yoy < segment_growth_min:
                                continue
                            if seg_yoy - total_yoy < min_divergence_gap:
                                continue
                            if seg_rev < min_segment_revenue_억 or share < min_segment_share:
                                continue
                            seg_candidates.append({
                                "segment_name": seg_name,
                                "segment_revenue_억": seg_rev,
                                "segment_yoy": seg_yoy,
                                "share": share,
                                "score": (seg_yoy - total_yoy) * min(seg_rev / max(min_segment_revenue_억, 1.0), 8.0),
                            })
                        if seg_candidates:
                            best = sorted(
                                seg_candidates,
                                key=lambda x: (x["score"], x["segment_yoy"], x["segment_revenue_억"]),
                                reverse=True,
                            )[0]
                            events.append({
                                "code": code,
                                "year": year,
                                "signal_day": _release_date(year, 0, True, code),
                                "total_yoy": total_yoy,
                                **best,
                            })
                prev_total = total_rev

        codes = sorted({e["code"] for e in events})
        sd: Dict[str, dict] = {}
        for code in codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(
                c_list[i - 1] > 0 and (c_list[i] / c_list[i - 1] < 0.45 or c_list[i] / c_list[i - 1] > 2.2)
                for i in range(1, len(c_list))
            ):
                continue
            sd[code] = {
                "d": [str(r[0])[:10] for r in rows],
                "c": c_list,
                "o": [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
            }
        didx = {c: {d: i for i, d in enumerate(s["d"])} for c, s in sd.items()}

        buy_pool: Dict[str, list] = {}
        event_meta: Dict[tuple, dict] = {}
        for event in events:
            code = event["code"]
            series = sd.get(code)
            if not series:
                continue
            pos = None
            for idx, day in enumerate(series["d"]):
                if day > event["signal_day"]:
                    pos = idx
                    break
            if pos is None or pos < 60:
                continue
            entry_date = series["d"][pos]
            if entry_date < start_date or entry_date > end_date:
                continue
            event_meta[(entry_date, code)] = event
            buy_pool.setdefault(entry_date, []).append(code)
        for day in buy_pool:
            buy_pool[day] = sorted(
                set(buy_pool[day]),
                key=lambda code: event_meta[(day, code)]["score"],
                reverse=True,
            )

        sim_dates = sorted(set(d for s in sd.values() for d in s["d"] if start_date <= d <= end_date))
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []

        for day in sim_dates:
            still_pending = []
            for code, reason in pending_sells:
                if code not in pos:
                    continue
                i = didx[code].get(day)
                if i is None:
                    still_pending.append((code, reason))
                    continue
                px = sd[code]["o"][i]
                if px <= 0:
                    still_pending.append((code, reason))
                    continue
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p["entry"], px, p["shares"], 300)
                cash += p["shares"] * p["entry"] + pnl
                trades.append({
                    "code": code,
                    "buy_date": p["buy_date"],
                    "sell_date": day,
                    "entry": p["entry"],
                    "exit": px,
                    "pnl_pct": net_pct,
                    "reason": reason,
                    "pnl": round(pnl, 0),
                    "signal_year": p["signal_year"],
                    "segment_name": p["segment_name"],
                    "segment_yoy_pct": round(p["segment_yoy"] * 100, 1),
                    "total_yoy_pct": round(p["total_yoy"] * 100, 1),
                })
            pending_sells = still_pending

            slots = max_positions - len(pos)
            if slots > 0:
                for code in buy_pool.get(day, []):
                    if slots <= 0 or code in pos:
                        break
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]["o"][i]
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    meta = event_meta.get((day, code), {})
                    cash -= shares * px
                    pos[code] = {
                        "entry": px,
                        "shares": shares,
                        "buy_date": day,
                        "hold": 0,
                        "peak": px,
                        "signal_year": meta.get("year"),
                        "segment_name": meta.get("segment_name"),
                        "segment_yoy": meta.get("segment_yoy", 0.0),
                        "total_yoy": meta.get("total_yoy", 0.0),
                    }
                    slots -= 1

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                curr = sd[code]["c"][i]
                if curr <= 0:
                    continue
                p["hold"] += 1
                p["peak"] = max(p.get("peak", p["entry"]), curr)
                ret = curr / p["entry"] - 1
                stop_cond = ret <= stop
                expire_cond = p["hold"] >= max_hold
                trail_cond = ret > trail_activate_pct and (curr - p["peak"]) / p["peak"] <= trail
                if stop_cond or expire_cond or trail_cond:
                    reason = "stop" if stop_cond else "trail" if trail_cond else "expire"
                    if code not in [c for c, _ in pending_sells]:
                        pending_sells.append((code, reason))

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            i = didx[code].get(last_day)
            curr = sd[code]["c"][i] if i is not None else p["entry"]
            if curr <= 0:
                curr = p["entry"]
            pnl, net_pct = _net_profit(p["entry"], curr, p["shares"], 300)
            cash += p["shares"] * p["entry"] + pnl
            trades.append({
                "code": code,
                "buy_date": p["buy_date"],
                "sell_date": last_day,
                "entry": p["entry"],
                "exit": curr,
                "pnl_pct": net_pct,
                "reason": "final",
                "pnl": round(pnl, 0),
                "signal_year": p["signal_year"],
                "segment_name": p["segment_name"],
                "segment_yoy_pct": round(p["segment_yoy"] * 100, 1),
                "total_yoy_pct": round(p["total_yoy"] * 100, 1),
            })

        total_return = (cash - total_capital) / total_capital * 100
        win_rate = (sum(1 for t in trades if t["pnl_pct"] > 0) / len(trades) * 100) if trades else 0.0
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, total_trades=?, win_rate=?, trades_json=?
            WHERE run_id=?
        """, (
            round(total_return, 2),
            len(trades),
            round(win_rate, 1),
            json.dumps({"trades": trades}, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=False)
        return run_id
    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise




