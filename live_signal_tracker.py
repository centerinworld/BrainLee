"""Register immutable strategy signals and update leakage-safe forward outcomes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import db_compat


DDL = """
CREATE TABLE IF NOT EXISTS live_signal_registry(
 signal_id TEXT PRIMARY KEY,stock_code TEXT NOT NULL,signal_type TEXT NOT NULL,strategy_id TEXT,
 signal_date TEXT NOT NULL,available_at TEXT NOT NULL,entry_date TEXT,entry_price REAL,price_basis TEXT NOT NULL,
 quality_score REAL,confidence_score REAL,action TEXT NOT NULL,signal_payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_signal_outcomes(
 signal_id TEXT NOT NULL,horizon_days INTEGER NOT NULL,outcome_date TEXT,outcome_price REAL,return_pct REAL,
 max_gain_pct REAL,max_loss_pct REAL,status TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(signal_id,horizon_days),FOREIGN KEY(signal_id) REFERENCES live_signal_registry(signal_id));
"""
HORIZONS = (1, 5, 20, 60, 120, 252)


def ensure(conn) -> None:
    cur = conn.cursor()
    for stmt in DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()


def _signal_id(signal_type: str, stock_code: str, signal_date: str, strategy_id: str | None) -> str:
    identity = f"{signal_type}|{stock_code}|{signal_date}|{strategy_id or ''}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{signal_type}:{stock_code}:{signal_date}:{digest}"


def _usable_prices(conn, stock_code: str, start_date: str, *, strictly_after: bool) -> list:
    operator = ">" if strictly_after else ">="
    cur = conn.cursor()
    cur.execute(
        f"""SELECT p.date,p.close,p.open
            FROM price_history p
            LEFT JOIN price_jump_audit a
              ON a.stock_code=p.stock_code AND a.event_date=substr(p.date,1,10)
            WHERE p.stock_code=%s AND p.date{operator}%s AND p.close>0
              AND COALESCE(a.return_usable,1)=1
            ORDER BY p.date""",
        (stock_code, start_date),
    )
    return cur.fetchall()


def register_signal(
    *,
    stock_code: str,
    signal_type: str,
    signal_date: str,
    available_at: str,
    action: str,
    payload: dict,
    strategy_id: str | None = None,
    quality_score: float | None = None,
    confidence_score: float | None = None,
    conn=None,
) -> str:
    owned = conn is None
    conn = conn or db_compat.connect_primary_db()
    ensure(conn)
    signal_id = _signal_id(signal_type, stock_code, signal_date, strategy_id)
    now = datetime.now().isoformat(timespec="seconds")
    entry = _usable_prices(conn, stock_code, signal_date, strictly_after=True)
    first_entry = entry[0] if entry else None
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO live_signal_registry VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(signal_id) DO NOTHING""",
        (
            signal_id, stock_code, signal_type, strategy_id, signal_date, available_at,
            first_entry[0] if first_entry else None, first_entry[2] if first_entry else None,
            "price_history+price_jump_audit", quality_score, confidence_score, action,
            json.dumps(payload, ensure_ascii=False), now,
        ),
    )
    inserted = cur.rowcount > 0
    if inserted:
        cur.executemany(
            """INSERT INTO live_signal_outcomes(signal_id,horizon_days,status,updated_at)
               VALUES(%s,%s,%s,%s) ON CONFLICT(signal_id,horizon_days) DO NOTHING""",
            [(signal_id, horizon, "pending", now) for horizon in HORIZONS],
        )
    if owned:
        conn.commit()
        conn.close()
    return signal_id


def update_outcomes(conn=None) -> int:
    owned = conn is None
    conn = conn or db_compat.connect_primary_db()
    ensure(conn)
    now = datetime.now().isoformat(timespec="seconds")
    updated = 0
    cur = conn.cursor()
    cur.execute("SELECT signal_id, stock_code, signal_type, strategy_id, signal_date, "
                "available_at, entry_date, entry_price, price_basis, quality_score, "
                "confidence_score, action, signal_payload_json, created_at "
                "FROM live_signal_registry")
    cols = [d[0] for d in cur.description]
    signals = [dict(zip(cols, r)) for r in cur.fetchall()]

    for signal in signals:
        entry_date = signal["entry_date"]
        entry_price = signal["entry_price"]
        if not entry_date or not entry_price:
            entries = _usable_prices(conn, signal["stock_code"], signal["signal_date"], strictly_after=True)
            if not entries:
                continue
            entry_date, entry_price = entries[0][0], float(entries[0][2])
            cur.execute(
                "UPDATE live_signal_registry SET entry_date=%s,entry_price=%s WHERE signal_id=%s",
                (entry_date, entry_price, signal["signal_id"]),
            )
        future = _usable_prices(conn, signal["stock_code"], entry_date, strictly_after=False)
        # 2026-09-28: 사용불가 가격 사건(분할·감자·미확인 급변 등, price_jump_audit.return_usable=0)은
        # 그 날만 빼면 앞뒤 가격이 다른 기준으로 이어 붙어 수익률이 망가진다(417310 코람코더원리츠
        # 2026-08-28 x0.2136 → -78% 오판). 창 안에 그런 사건이 있으면 완결로 치지 않고 제외한다.
        cur.execute(
            """SELECT event_date FROM price_jump_audit
               WHERE stock_code=%s AND event_date>%s AND return_usable=0 ORDER BY event_date""",
            (signal["stock_code"], str(entry_date)[:10]),
        )
        blocked = [str(r[0])[:10] for r in cur.fetchall()]
        for horizon in HORIZONS:
            if len(future) <= horizon:
                continue
            window = future[:horizon + 1]
            end = window[-1]
            if any(str(entry_date)[:10] < d <= str(end[0])[:10] for d in blocked):
                cur.execute(
                    """UPDATE live_signal_outcomes
                       SET outcome_date=NULL,outcome_price=NULL,return_pct=NULL,max_gain_pct=NULL,
                           max_loss_pct=NULL,status='price_event_excluded',updated_at=%s
                       WHERE signal_id=%s AND horizon_days=%s AND status<>'price_event_excluded'""",
                    (now, signal["signal_id"], horizon),
                )
                updated += max(cur.rowcount, 0)
                continue
            entry_price = float(entry_price)
            closes = [float(row[1]) for row in window]
            cur.execute(
                """UPDATE live_signal_outcomes
                   SET outcome_date=%s,outcome_price=%s,return_pct=%s,max_gain_pct=%s,max_loss_pct=%s,
                       status='complete',updated_at=%s
                   WHERE signal_id=%s AND horizon_days=%s AND status<>'complete'""",
                (
                    end[0], end[1], (float(end[1]) / entry_price - 1) * 100,
                    (max(closes) / entry_price - 1) * 100,
                    (min(closes) / entry_price - 1) * 100,
                    now, signal["signal_id"], horizon,
                ),
            )
            updated += max(cur.rowcount, 0)
    if owned:
        conn.commit()
        conn.close()
    return updated
