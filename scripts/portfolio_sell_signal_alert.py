#!/usr/bin/env python3
"""Portfolio sell-signal Telegram monitor.

This does not place sell orders. It scans real portfolio holdings and sends a
Telegram alert when several validated sell-risk conditions line up.
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(str(v).replace(",", ""))
    except Exception:
        return default


def _pct(a: float, b: float) -> float | None:
    if not b:
        return None
    return (a - b) / b * 100.0


def _ma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%"


def _fmt_krw(v: float | None) -> str:
    if v is None:
        return "-"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e8:
        return f"{sign}{a / 1e8:,.1f}억"
    if a >= 1e6:
        return f"{sign}{a / 1e6:,.1f}백만"
    return f"{sign}{a:,.0f}원"


def _ensure_tables(c: sqlite3.Connection) -> None:
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_sell_signal_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            severity TEXT NOT NULL,
            score INTEGER NOT NULL,
            current_price REAL,
            avg_price REAL,
            return_pct REAL,
            peak_drawdown_pct REAL,
            reasons_json TEXT,
            sent_telegram INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_portfolio_sell_signal_alerts_ts
        ON portfolio_sell_signal_alerts(scan_ts DESC, stock_code)
        """
    )
    c.commit()


def _price_rows(c: sqlite3.Connection, code: str, limit: int = 270) -> list[sqlite3.Row]:
    rows = c.execute(
        """
        SELECT stock_code, date, close, high, low, volume, trade_amount,
               frn_net_buy_amt, inst_net_buy_amt
        FROM price_history
        WHERE stock_code=? AND close IS NOT NULL AND close>0
        ORDER BY date DESC
        LIMIT ?
        """,
        (code, limit),
    ).fetchall()
    return list(reversed(rows))


def _sum_recent_price_flow(rows: list[sqlite3.Row], col: str, n: int) -> float:
    return sum(_f(r[col]) for r in rows[-n:])


def _program_flow(c: sqlite3.Connection, code: str, n: int = 5) -> float | None:
    try:
        rows = c.execute(
            """
            SELECT net_buy_amt_krw
            FROM broker_program_stock_daily
            WHERE stock_code=? AND net_buy_amt_krw IS NOT NULL
            ORDER BY dt DESC
            LIMIT ?
            """,
            (code, n),
        ).fetchall()
        if not rows:
            return None
        return sum(_f(r[0]) for r in rows)
    except Exception:
        return None


def _short_context(c: sqlite3.Connection, code: str) -> dict[str, Any]:
    rows = c.execute(
        """
        SELECT bas_dt, borrow_bal_qty, borrow_bal_pct
        FROM short_sell_daily
        WHERE stock_code=? AND borrow_bal_qty IS NOT NULL
        ORDER BY bas_dt DESC
        LIMIT 6
        """,
        (code,),
    ).fetchall()
    if not rows:
        return {}
    latest = rows[0]
    old = rows[-1]
    qty = _f(latest["borrow_bal_qty"])
    old_qty = _f(old["borrow_bal_qty"])
    chg = _pct(qty, old_qty)
    return {
        "date": latest["bas_dt"],
        "borrow_bal_qty": qty,
        "borrow_bal_pct": _f(latest["borrow_bal_pct"], None),
        "change_5row_pct": chg,
    }


def _stock_name(c: sqlite3.Connection, code: str, fallback: str | None = None) -> str:
    name = (fallback or "").strip()
    if name and name != code:
        return name
    for table, col in (
        ("stock_meta", "stock_name"),
        ("stock_universe", "stock_name"),
        ("listed_company_info", "corp_name"),
    ):
        try:
            row = c.execute(f"SELECT {col} FROM {table} WHERE stock_code=? LIMIT 1", (code,)).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception:
            continue
    return name or code


def evaluate_holding(c: sqlite3.Connection, h: sqlite3.Row) -> dict[str, Any] | None:
    code = str(h["stock_code"] or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    qty = _f(h["quantity"])
    avg = _f(h["avg_price"])
    if qty <= 0 or avg <= 0:
        return None

    rows = _price_rows(c, code)
    if len(rows) < 20:
        return None

    latest = rows[-1]
    cur = _f(latest["close"])
    closes = [_f(r["close"]) for r in rows if _f(r["close"]) > 0]
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)

    entry_date = str(h["bought_at"] or h["created_at"] or "")[:10]
    entry_rows = [r for r in rows if not entry_date or str(r["date"])[:10] >= entry_date]
    peak_rows = entry_rows if entry_rows else rows[-252:]
    peak = max((_f(r["high"]) or _f(r["close"])) for r in peak_rows)
    ret_pct = _pct(cur, avg)
    peak_dd = _pct(cur, peak)

    frn5 = _sum_recent_price_flow(rows, "frn_net_buy_amt", 5)
    inst5 = _sum_recent_price_flow(rows, "inst_net_buy_amt", 5)
    frn20 = _sum_recent_price_flow(rows, "frn_net_buy_amt", 20)
    inst20 = _sum_recent_price_flow(rows, "inst_net_buy_amt", 20)
    program5 = _program_flow(c, code, 5)
    short = _short_context(c, code)

    reasons: list[dict[str, Any]] = []
    score = 0

    def add(level: str, label: str, detail: str, points: int) -> None:
        nonlocal score
        score += points
        reasons.append({"level": level, "label": label, "detail": detail, "points": points})

    if ret_pct is not None and ret_pct <= -15:
        add("critical", "하드손절", f"매입가 대비 {_fmt_pct(ret_pct)}", 3)
    elif ret_pct is not None and ret_pct <= -10:
        add("warning", "손절주의", f"매입가 대비 {_fmt_pct(ret_pct)}", 2)

    if peak_dd is not None and peak_dd <= -30:
        add("critical", "고점대비 -30% 이탈", f"보유 이후/최근 고점 {peak:,.0f}원 대비 {_fmt_pct(peak_dd)}", 3)
    elif peak_dd is not None and peak_dd <= -20 and ret_pct is not None and ret_pct > 3:
        add("warning", "이익 반납 확대", f"고점 대비 {_fmt_pct(peak_dd)}", 2)

    if ma60 and cur < ma60 * 0.97 and (not ma20 or ma20 < ma60):
        add("critical", "중기 추세 붕괴", f"현재가 {cur:,.0f}원 < MA60 {ma60:,.0f}원", 3)
    elif ma20 and ma60 and cur < ma20 and cur < ma60:
        add("warning", "MA20/MA60 동시 하회", f"MA20 {ma20:,.0f}원 / MA60 {ma60:,.0f}원", 2)
    elif ma20 and cur < ma20:
        add("info", "MA20 하회", f"MA20 {ma20:,.0f}원", 1)

    if frn5 < 0 and inst5 < 0:
        add("warning", "외국인·기관 5일 동반매도", f"외국인 {_fmt_krw(frn5 * 1_000_000)} / 기관 {_fmt_krw(inst5 * 1_000_000)}", 2)
    if frn20 < 0 and inst20 < 0 and ret_pct is not None and ret_pct < 0:
        add("warning", "20일 수급 이탈", f"외국인 {_fmt_krw(frn20 * 1_000_000)} / 기관 {_fmt_krw(inst20 * 1_000_000)}", 2)

    if program5 is not None and program5 < 0:
        add("info", "프로그램 5일 순매도", _fmt_krw(program5), 1)

    short_chg = short.get("change_5row_pct")
    short_pct = short.get("borrow_bal_pct")
    if short_chg is not None and short_chg >= 10:
        add("warning", "대차잔고 증가", f"최근 5개 관측치 {_fmt_pct(short_chg)}", 2)
    if short_pct is not None and short_pct >= 5:
        add("info", "대차잔고비율 높음", f"{short_pct:.2f}%", 1)

    has_critical = any(r["level"] == "critical" for r in reasons)
    should_alert = has_critical or score >= 4
    severity = "긴급 매도검토" if has_critical or score >= 6 else "매도주의"

    return {
        "stock_code": code,
        "stock_name": _stock_name(c, code, h["stock_name"]),
        "quantity": qty,
        "avg_price": avg,
        "current_price": cur,
        "price_date": str(latest["date"])[:10],
        "return_pct": ret_pct,
        "peak_price": peak,
        "peak_drawdown_pct": peak_dd,
        "ma20": ma20,
        "ma60": ma60,
        "score": score,
        "severity": severity,
        "should_alert": should_alert,
        "reasons": reasons,
    }


def _message(s: dict[str, Any]) -> str:
    reasons = "\n".join(
        f"• {html.escape(r['label'])}: {html.escape(r['detail'])}"
        for r in s["reasons"][:6]
    )
    return (
        f"🔴 <b>[보유종목 {html.escape(s['severity'])}]</b>\n"
        f"{html.escape(s['stock_name'])}({s['stock_code']})\n"
        f"현재가 {s['current_price']:,.0f}원 / 매입가 {s['avg_price']:,.0f}원 "
        f"({_fmt_pct(s['return_pct'])})\n"
        f"고점대비 {_fmt_pct(s['peak_drawdown_pct'])} · 점수 {s['score']}점 · 기준 {s['price_date']}\n"
        f"{reasons}\n\n"
        f"※ 자동매도 아님. 매도 검토 알림입니다. -30% 고점이탈은 단독 절대규칙이 아니라 "
        f"손절·추세·수급과 함께 판단합니다."
    )


def _summary_message(signals: list[dict[str, Any]], scanned: int, candidate_count: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if not signals:
        return (
            f"🟢 <b>[보유종목 매도점검]</b> {today}\n"
            f"스캔 {scanned}종목 / 매도검토 후보 0종목\n"
            f"오늘은 점수 기준을 넘는 매도검토 종목이 없습니다."
        )

    lines = []
    for i, s in enumerate(signals, 1):
        top_reasons = ", ".join(r["label"] for r in s["reasons"][:3])
        lines.append(
            f"{i}. <b>{html.escape(s['stock_name'])}</b>({s['stock_code']}) "
            f"{html.escape(s['severity'])} {s['score']}점\n"
            f"   수익률 {_fmt_pct(s['return_pct'])} / 고점대비 {_fmt_pct(s['peak_drawdown_pct'])} / "
            f"현재가 {s['current_price']:,.0f}원\n"
            f"   사유: {html.escape(top_reasons)}"
        )

    return (
        f"🔴 <b>[보유종목 매도점검]</b> {today} 15시 기준\n"
        f"스캔 {scanned}종목 / 후보 {candidate_count}종목 / 상위 {len(signals)}종목\n"
        f"※ 자동매도 아님. 종목별 매도검토 요약입니다.\n\n"
        + "\n\n".join(lines)
        + "\n\n-30% 고점이탈은 단독 절대규칙이 아니라 손절·추세·수급과 함께 판단합니다."
    )


def _record(c: sqlite3.Connection, s: dict[str, Any], sent: bool) -> None:
    c.execute(
        """
        INSERT INTO portfolio_sell_signal_alerts
        (scan_ts, stock_code, stock_name, severity, score, current_price, avg_price,
         return_pct, peak_drawdown_pct, reasons_json, sent_telegram)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            s["stock_code"],
            s["stock_name"],
            s["severity"],
            int(s["score"]),
            s["current_price"],
            s["avg_price"],
            s["return_pct"],
            s["peak_drawdown_pct"],
            json.dumps(s["reasons"], ensure_ascii=False),
            1 if sent else 0,
        ),
    )
    c.commit()


def scan(send_telegram: bool = False, min_score: int = 6, limit: int = 10, summary: bool = False) -> dict[str, Any]:
    c = _conn()
    _ensure_tables(c)
    try:
        holdings = c.execute(
            """
            SELECT stock_code, stock_name, quantity, avg_price, created_at, bought_at, broker, owner
            FROM portfolio
            WHERE COALESCE(quantity,0)>0
              AND stock_code IS NOT NULL AND TRIM(stock_code)!=''
            ORDER BY stock_name
            """
        ).fetchall()
        candidates = []
        sent_count = 0
        for h in holdings:
            s = evaluate_holding(c, h)
            if not s:
                continue
            if s["should_alert"] and s["score"] >= min_score:
                candidates.append(s)

        candidates.sort(
            key=lambda s: (
                s["score"],
                1 if s["severity"] == "긴급 매도검토" else 0,
                abs(float(s.get("return_pct") or 0)),
            ),
            reverse=True,
        )
        signals = candidates[:limit] if limit > 0 else candidates

        if send_telegram and summary:
            from notifier import send

            today = datetime.now().strftime("%Y-%m-%d")
            sent = send(_summary_message(signals, len(holdings), len(candidates)), key=f"portfolio_sell_summary_{today}")
            sent_count = 1 if sent else 0
            for s in signals:
                _record(c, s, sent)
        else:
            for s in signals:
                sent = False
                if send_telegram:
                    from notifier import send

                    today = datetime.now().strftime("%Y-%m-%d")
                    top_label = s["reasons"][0]["label"] if s["reasons"] else "signal"
                    key = f"portfolio_sell_{s['stock_code']}_{top_label}_{today}"
                    sent = send(_message(s), key=key)
                    sent_count += 1 if sent else 0
                _record(c, s, sent)
        return {
            "ok": True,
            "scanned": len(holdings),
            "candidate_count": len(candidates),
            "signals": signals,
            "signal_count": len(signals),
            "sent_count": sent_count,
        }
    finally:
        c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send-telegram", action="store_true")
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--summary", action="store_true", help="텔레그램을 종목별이 아니라 1개 요약 메시지로 발송")
    args = ap.parse_args()
    result = scan(send_telegram=args.send_telegram, min_score=args.min_score, limit=args.limit, summary=args.summary)
    printable = {
        **result,
        "signals": [
            {
                "stock_code": s["stock_code"],
                "stock_name": s["stock_name"],
                "severity": s["severity"],
                "score": s["score"],
                "return_pct": round(s["return_pct"], 2) if s["return_pct"] is not None else None,
                "peak_drawdown_pct": round(s["peak_drawdown_pct"], 2) if s["peak_drawdown_pct"] is not None else None,
                "reasons": [r["label"] for r in s["reasons"]],
            }
            for s in result["signals"]
        ],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
