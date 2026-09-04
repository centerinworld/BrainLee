#!/usr/bin/env python3
"""
텐버거 실시간 복합 트리거 알림
- 기관 N일 연속 순매수
- 임원 신규 매수
- 신용잔고 급감
- 외국인 지분율 급증
스케줄러에서 평일 18:00 실행
"""
import sqlite3, sys, json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from telegram_stock_dedup import filter_new as _filter_new_alerts, mark_sent as _mark_alert_sent

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
ALERT_STATE_FILE = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/trigger_alert_state.json")


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def _load_state() -> dict:
    if ALERT_STATE_FILE.exists():
        return json.loads(ALERT_STATE_FILE.read_text())
    return {}


def _save_state(state: dict):
    ALERT_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def _send_telegram(msg: str) -> bool:
    try:
        from notifier import send
        return send(msg)
    except Exception as e:
        print(f"텔레그램 실패: {e}")
        return False


def check_inst_consecutive(conn, min_days=3, min_amt_억=5) -> list:
    """기관 N일 연속 순매수 종목 감지"""
    results = []
    ref_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    rows = conn.execute("""
        SELECT stock_code, COUNT(*) AS cnt,
               SUM(inst_net_buy_amt/100.0) AS total_억
        FROM (
            SELECT stock_code, date,
                   inst_net_buy_amt,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
            FROM price_history
            WHERE date <= ?
              AND stock_code NOT LIKE '%^%'
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ) t
        WHERE rn <= ? AND inst_net_buy_amt > 0
        GROUP BY stock_code
        HAVING COUNT(*) >= ? AND SUM(inst_net_buy_amt/100.0) >= ?
        ORDER BY total_억 DESC LIMIT 30
    """, (ref_date, min_days+2, min_days, min_amt_억 * min_days)).fetchall()

    for r in rows:
        results.append({
            "stock_code": r["stock_code"],
            "signal_type": "inst_consecutive",
            "days": r["cnt"],
            "total_억": round(r["total_억"] or 0, 1),
        })
    return results


def check_insider_new(conn, days_back=2) -> list:
    """신규 임원 매수 감지"""
    since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT h.stock_code, u.stock_name,
               SUM(COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0)) AS buy_qty,
               MAX(h.is_ceo) AS is_ceo,
               MAX(h.rcept_dt) AS latest_date
        FROM dart_insider_holdings h
        JOIN stock_universe u ON u.stock_code = h.stock_code
        WHERE COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) > 0
          AND h.rcept_dt >= ?
        GROUP BY h.stock_code, u.stock_name
        HAVING SUM(COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0)) > 1000
        ORDER BY is_ceo DESC, buy_qty DESC LIMIT 20
    """, (since,)).fetchall()

    return [{"stock_code": r["stock_code"], "stock_name": r["stock_name"],
             "signal_type": "insider_buy", "qty": r["buy_qty"],
             "is_ceo": bool(r["is_ceo"]), "date": r["latest_date"]}
            for r in rows]


def check_credit_drop(conn, days_window=5, min_drop_pct=1.5) -> list:
    """신용잔고 급감 감지"""
    since = (datetime.now() - timedelta(days=days_window)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT c1.stock_code, u.stock_name,
               c1.credit_ratio AS curr, c2.credit_ratio AS prev,
               c2.credit_ratio - c1.credit_ratio AS drop_pct
        FROM kiwoom_credit_balance c1
        JOIN kiwoom_credit_balance c2 ON c1.stock_code = c2.stock_code
        JOIN stock_universe u ON u.stock_code = c1.stock_code
        WHERE c1.dt = (SELECT MAX(dt) FROM kiwoom_credit_balance WHERE stock_code=c1.stock_code)
          AND c2.dt = (SELECT MIN(dt) FROM kiwoom_credit_balance
                                WHERE stock_code=c1.stock_code AND dt >= REPLACE(?, '-', ''))
          AND c2.credit_ratio - c1.credit_ratio >= ?
        ORDER BY drop_pct DESC LIMIT 20
    """, (since, min_drop_pct)).fetchall()

    return [{"stock_code": r["stock_code"], "stock_name": r["stock_name"],
             "signal_type": "credit_drop",
             "curr_ratio": r["curr"], "prev_ratio": r["prev"],
             "drop_pct": round(r["drop_pct"] or 0, 2)}
            for r in rows]


def check_foreign_surge(conn, days_window=5, min_delta=2.0) -> list:
    """외국인 지분율 급증 감지"""
    since = (datetime.now() - timedelta(days=days_window)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT f1.stock_code, u.stock_name,
               f1.weight - f2.weight AS delta_pct,
               f1.weight AS curr_weight
        FROM kiwoom_foreign_flow f1
        JOIN kiwoom_foreign_flow f2 ON f1.stock_code = f2.stock_code
        JOIN stock_universe u ON u.stock_code = f1.stock_code
        WHERE f1.dt = (SELECT MAX(dt) FROM kiwoom_foreign_flow WHERE stock_code=f1.stock_code)
          AND f2.dt = (SELECT MIN(dt) FROM kiwoom_foreign_flow
                          WHERE stock_code=f1.stock_code AND dt >= REPLACE(?, '-', ''))
          AND f1.weight - f2.weight >= ?
        ORDER BY delta_pct DESC LIMIT 20
    """, (since, min_delta)).fetchall()

    return [{"stock_code": r["stock_code"], "stock_name": r["stock_name"],
             "signal_type": "foreign_surge",
             "delta_pct": round(r["delta_pct"] or 0, 2),
             "curr_weight": round(r["curr_weight"] or 0, 2)}
            for r in rows]


def detect_composite_signals(conn) -> list:
    """복합 신호 (2개 이상 겹친 종목) 탐지"""
    inst = {r["stock_code"]: r for r in check_inst_consecutive(conn)}
    insider = {r["stock_code"]: r for r in check_insider_new(conn)}
    credit = {r["stock_code"]: r for r in check_credit_drop(conn)}
    foreign = {r["stock_code"]: r for r in check_foreign_surge(conn)}

    all_codes = set(inst) | set(insider) | set(credit) | set(foreign)
    composite = []

    for code in all_codes:
        signals = []
        score = 0
        if code in inst:
            signals.append(f"기관{inst[code]['days']}일연속(+{inst[code]['total_억']:.0f}억)")
            score += 2
        if code in insider:
            prefix = "CEO" if insider[code]["is_ceo"] else "임원"
            signals.append(f"{prefix}매수({insider[code]['qty']:,}주)")
            score += 3 if insider[code]["is_ceo"] else 2
        if code in credit:
            signals.append(f"신용↓{credit[code]['drop_pct']:.1f}%p")
            score += 2
        if code in foreign:
            signals.append(f"외국인↑{foreign[code]['delta_pct']:.1f}%p")
            score += 2

        if score >= 4:  # 복합 기준: 점수 4점 이상
            name_row = conn.execute(
                "SELECT stock_name, market_cap, close FROM stock_universe su "
                "LEFT JOIN (SELECT stock_code, close FROM price_history p "
                "WHERE date=(SELECT MAX(date) FROM price_history p2 WHERE p2.stock_code=p.stock_code)) ph "
                "ON ph.stock_code=su.stock_code WHERE su.stock_code=?", (code,)
            ).fetchone()
            composite.append({
                "stock_code": code,
                "stock_name": name_row["stock_name"] if name_row else code,
                "composite_score": score,
                "signals": signals,
                "signal_count": len(signals),
            })

    return sorted(composite, key=lambda x: -x["composite_score"])


def build_alert_message(composite: list, inst: list, insider: list) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"<b>🎯 텐버거 트리거 알림 ({today})</b>\n"]

    if composite:
        lines.append(f"<b>⚡ 복합 신호 ({len(composite)}종목)</b>")
        for c in composite[:10]:
            sig_str = " + ".join(c["signals"])
            lines.append(f"  🔥 <b>{c['stock_name']}</b>({c['stock_code']}) [{c['composite_score']}점]\n    {sig_str}")
        lines.append("")

    # 단독 신호 요약
    if inst:
        lines.append(f"<b>📊 기관 연속 순매수</b> ({len(inst)}종목)")
        for r in inst[:5]:
            lines.append(f"  • {r['stock_code']} {r['days']}일 +{r['total_억']:.0f}억")
        lines.append("")

    if insider:
        lines.append(f"<b>👔 임원 신규 매수</b> ({len(insider)}종목)")
        for r in insider[:5]:
            ceo = "⭐CEO " if r.get("is_ceo") else ""
            lines.append(f"  • {r['stock_name']} {ceo}{r['qty']:,}주")
        lines.append("")

    return "\n".join(lines)


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 트리거 알림 체크 시작")
    conn = _db()
    state = _load_state()
    today_str = datetime.now().strftime("%Y-%m-%d")

    composite = detect_composite_signals(conn)
    inst = check_inst_consecutive(conn)
    insider = check_insider_new(conn, days_back=2)

    # 중복 알림 방지:
    # - 예전 로직은 today_str 기준이라 다음날 같은 종목이 다시 발송됐다.
    # - 신규 종목만 보내기 위해 signal namespace별 영구 DB 이력을 우선 적용한다.
    sent_today = set(state.get(today_str, []))
    new_composite = [
        c for c in _filter_new_alerts(composite, "tenbagger_trigger_composite")
        if c["stock_code"] not in sent_today
    ]
    new_inst = _filter_new_alerts(inst, "tenbagger_trigger_inst_consecutive")
    new_insider = _filter_new_alerts(insider, "tenbagger_trigger_insider_buy")

    print(f"  복합신호: {len(composite)}종목 (신규: {len(new_composite)})")
    print(f"  기관연속: {len(inst)}종목 (신규: {len(new_inst)})")
    print(f"  임원매수: {len(insider)}종목 (신규: {len(new_insider)})")

    if new_composite or new_inst or new_insider:
        msg = build_alert_message(new_composite, new_inst, new_insider)
        if _send_telegram(msg):
            print("  텔레그램 발송 완료")
            _mark_alert_sent("tenbagger_trigger_composite", new_composite)
            _mark_alert_sent("tenbagger_trigger_inst_consecutive", new_inst)
            _mark_alert_sent("tenbagger_trigger_insider_buy", new_insider)
        else:
            print("  텔레그램 발송 실패 — 신규 이력 저장 안 함")

        # 상태 저장
        state[today_str] = list(sent_today | {c["stock_code"] for c in composite})
        _save_state(state)
    else:
        print("  신규 종목 없음 — 텔레그램 발송 스킵")

    conn.close()


if __name__ == "__main__":
    main()
