#!/usr/bin/env python3
"""
텐버거 위클리 리포트 자동 생성 + 텔레그램 발송
매주 월요일 07:30 스케줄러 실행
"""
import sqlite3, sys, os, json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from telegram_stock_dedup import filter_new as _filter_new_alerts, mark_sent as _mark_alert_sent

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
REPORT_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/reports")
REPORT_DIR.mkdir(exist_ok=True)
ALERT_NAMESPACE = "tenbagger_hunter_candidate"


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def _send_telegram(msg: str):
    try:
        from notifier import send
        return send(msg, key=f"tenbagger_weekly_{datetime.now().date().isoformat()}")
    except Exception as e:
        print(f"텔레그램 발송 실패: {e}")
        return False


def build_weekly_report() -> tuple[str, list[dict]]:
    conn = _db()
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = [f"<b>📊 텐버거 위클리 리포트 ({today})</b>\n"]

    # ─── 1. 이번 주 TOP 10 후보 ─────────────────────────────────
    rows_all = conn.execute("""
        SELECT r.stock_code, u.stock_name, r.total_score, r.reasons,
               u.market_cap, u.per, u.pbr
        FROM tenbagger_results r
        JOIN stock_universe u USING(stock_code)
        WHERE date(r.run_time) >= date(?)
        ORDER BY r.total_score DESC LIMIT 10
    """, (week_ago,)).fetchall()
    rows = _filter_new_alerts([dict(r) for r in rows_all], ALERT_NAMESPACE)

    if rows:
        lines.append("<b>🏆 이번 주 신규 텐버거 후보</b>")
        for i, r in enumerate(rows, 1):
            name = r["stock_name"]
            score = r["total_score"]
            mc = r["market_cap"] or 0
            mc_str = f"{mc:,.0f}억" if mc else "N/A"
            lines.append(f"{i}. <b>{name}</b>({r['stock_code']}) 점수:{score} 시총:{mc_str} PBR:{r['pbr'] or 'N/A'}")
    else:
        lines.append("이번 주 신규 텐버거 후보 없음 — 텔레그램 후보 반복 전송 생략")

    lines.append("")

    # ─── 2. 이번 주 신규 임원 매수 알림 ──────────────────────────
    insider_rows = conn.execute("""
        SELECT h.stock_code, u.stock_name,
               SUM(CASE WHEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) > 0
                        THEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) ELSE 0 END) AS buy_qty,
               ABS(SUM(CASE WHEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) < 0
                        THEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) ELSE 0 END)) AS sell_qty,
               MAX(h.is_ceo) AS has_ceo
        FROM dart_insider_holdings h
        JOIN stock_universe u ON u.stock_code = h.stock_code
        WHERE h.rcept_dt >= ?
        GROUP BY h.stock_code, u.stock_name
        HAVING SUM(CASE WHEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) > 0
                        THEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) ELSE 0 END)
               > ABS(SUM(CASE WHEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) < 0
                        THEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) ELSE 0 END))
           AND SUM(CASE WHEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) > 0
                        THEN COALESCE(h.change_amount, h.sp_stock_lmp_irds_cnt, 0) ELSE 0 END) > 5000
        ORDER BY buy_qty DESC LIMIT 10
    """, (week_ago,)).fetchall()

    if insider_rows:
        lines.append("<b>👔 이번 주 임원 순매수 상위</b>")
        for r in insider_rows:
            ceo = " ⭐CEO" if r["has_ceo"] else ""
            lines.append(f"  • {r['stock_name']}{ceo}: 매수 {r['buy_qty']:,}주")
    lines.append("")

    # ─── 3. 이번 주 외국인 지분 급증 ──────────────────────────────
    fh_rows = conn.execute("""
        SELECT f1.stock_code, u.stock_name,
               f1.weight - f2.weight AS delta_pct,
               f1.weight AS curr_weight
        FROM kiwoom_foreign_flow f1
        JOIN kiwoom_foreign_flow f2 ON f1.stock_code = f2.stock_code
        JOIN stock_universe u ON u.stock_code = f1.stock_code
        WHERE f1.dt = (SELECT MAX(dt) FROM kiwoom_foreign_flow WHERE stock_code=f1.stock_code)
          AND f2.dt = (SELECT MIN(dt) FROM kiwoom_foreign_flow
                          WHERE stock_code=f1.stock_code AND dt >= REPLACE(?, '-', ''))
          AND f1.weight - f2.weight >= 2.0
        ORDER BY delta_pct DESC LIMIT 8
    """, (week_ago,)).fetchall()

    if fh_rows:
        lines.append("<b>🌍 이번 주 외국인 지분율 급증 (+2%p↑)</b>")
        for r in fh_rows:
            lines.append(f"  • {r['stock_name']}: +{r['delta_pct']:.1f}%p → {r['curr_weight']:.1f}%")
    lines.append("")

    # ─── 4. 이번 주 신용잔고 급감 (숏스퀴즈 예고) ─────────────────
    cr_rows = conn.execute("""
        SELECT c1.stock_code, u.stock_name,
               c1.credit_ratio AS curr_ratio,
               c2.credit_ratio AS prev_ratio,
               c2.credit_ratio - c1.credit_ratio AS drop_pct
        FROM kiwoom_credit_balance c1
        JOIN kiwoom_credit_balance c2 ON c1.stock_code = c2.stock_code
        JOIN stock_universe u ON u.stock_code = c1.stock_code
        WHERE c1.dt = (SELECT MAX(dt) FROM kiwoom_credit_balance WHERE stock_code=c1.stock_code)
          AND c2.dt = (SELECT MIN(dt) FROM kiwoom_credit_balance
                                WHERE stock_code=c1.stock_code AND dt >= REPLACE(?, '-', ''))
          AND c2.credit_ratio - c1.credit_ratio >= 1.0
          AND c1.credit_ratio < c2.credit_ratio
        ORDER BY drop_pct DESC LIMIT 8
    """, (week_ago,)).fetchall()

    if cr_rows:
        lines.append("<b>📉 이번 주 신용잔고 급감 (숏스퀴즈 가능성)</b>")
        for r in cr_rows:
            lines.append(f"  • {r['stock_name']}: {r['prev_ratio']:.1f}% → {r['curr_ratio']:.1f}% ({r['drop_pct']:.1f}%p↓)")
    lines.append("")

    # ─── 5. BigQuery 3배주 주간 Top 5 ─────────────────────────────
    bq_rows = conn.execute("""
        SELECT td.stock_code,
               COALESCE(td.stock_name, u.stock_name) AS stock_name,
               td.triple_pattern_score AS triple_score,
               td.tenbagger_score
        FROM triple_pattern_daily td
        LEFT JOIN stock_universe u ON u.stock_code = td.stock_code
        WHERE td.run_date = (SELECT MAX(run_date) FROM triple_pattern_daily)
        ORDER BY td.triple_pattern_score DESC LIMIT 5
    """).fetchall()

    if bq_rows:
        lines.append("<b>🔮 BQ 3배주 점수 Top 5</b>")
        for r in bq_rows:
            lines.append(f"  • {r['stock_name']}: 복합{r['triple_score']} (텐버거:{r['tenbagger_score']})")
    lines.append("")

    conn.close()
    lines.append(f"<i>생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
    return "\n".join(lines), rows


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 위클리 리포트 생성 시작")
    report, new_top_rows = build_weekly_report()

    # 파일 저장
    fname = REPORT_DIR / f"tenbagger_weekly_{datetime.now():%Y%m%d}.md"
    fname.write_text(report.replace("<b>","**").replace("</b>","**")
                          .replace("<i>","*").replace("</i>","*")
                          .replace("<br>","\n"), encoding="utf-8")
    print(f"리포트 저장: {fname}")

    # 텔레그램 발송: 텐버거 후보가 실제 신규 진입했을 때만 전송한다.
    if new_top_rows:
        _send_telegram(report)
        _mark_alert_sent(ALERT_NAMESPACE, new_top_rows)
        print(f"텔레그램 발송 완료: 신규 {len(new_top_rows)}종목")
    else:
        print("신규 텐버거 후보 없음 — 텔레그램 발송 스킵")


if __name__ == "__main__":
    main()
