#!/usr/bin/env python3
"""
corporate_action_confirmation_followup.py — 가격점프 원인규명(review_required) 잔여분을
매일 조금씩 안전하게 확정해나간다.

2026-08-23 세션 배경:
  - price_jump_audit 미해결(2,321건, 21.12~22.10 구간)이 turnaround/regime_adaptive
    전략의 검증을 막고 있었음. 원인을 추적한 결과 corporate_action_events 테이블의
    review_required(당시 7,058건) — 특히 유상증자(rights_issue) 5,588건(79%)가
    핵심 병목이었음.
  - 유상증자는 단순 주식수 비율이 아니라 발행가(구주주배정가)가 있어야 권리락
    이론가(TERP) 계산이 가능한데, 이 값은 dilution_events.conversion_price에 있음.
  - 최초 배치(2026-08-23)로 2,405건 매칭 중 1,285건을 TERP 계수로 확정(1,081건은
    old_shares/new_shares 자체가 오염돼 있어 보수적으로 스킵).

이 스크립트가 하는 일(안전한 "데이터 확정" 작업만 — 계수를 실제 수익률 계산에
적용하는 로직 변경은 하지 않는다. 그건 사람이 코드 리뷰하며 진행할 몫):
  1. corporate_action_events(review_required, rights_issue/rights_or_other_issue) ×
     dilution_events(conversion_price 보유)를 재매칭 — 다른 스케줄 작업이 매일
     dilution_events/order_backlog 등을 갱신하므로, 어제는 매칭 안 됐던 것이
     오늘은 매칭될 수 있음.
  2. 주식수 sanity check(플레이스홀더/연도값 혼입 의심, 5배 초과 등)로 걸러
     신뢰 가능한 것만 confirmed 처리.
  3. 매일 진행 상황(review_required 잔여 건수, 신규 확정 건수)을 리포트로 남긴다.

DART API를 쓰지 않으므로 한도 소진과 무관하게 매일 실행 가능하다.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ca_followup] %(message)s")
log = logging.getLogger(__name__)

PG_URL = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def confirm_rights_issue_terp(conn) -> dict:
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM corporate_action_events WHERE adjustment_status='review_required'")
    before_total = cur.fetchone()[0]

    cur.execute("""
        SELECT c.id, c.stock_code, c.event_date, c.old_shares, c.new_shares,
               d.conversion_price, d.disclosed_at,
               ABS(EXTRACT(EPOCH FROM (d.disclosed_at::timestamp - c.event_date::timestamp))) as diff_secs
        FROM corporate_action_events c
        JOIN dilution_events d ON d.stock_code=c.stock_code
          AND ABS(EXTRACT(EPOCH FROM (d.disclosed_at::timestamp - c.event_date::timestamp))) < 86400*60
        WHERE c.adjustment_status='review_required' AND c.event_type IN ('rights_issue','rights_or_other_issue')
          AND d.conversion_price IS NOT NULL AND d.conversion_price > 0
        ORDER BY c.id, diff_secs ASC
    """)
    rows = cur.fetchall()

    best: dict[int, tuple] = {}
    for id_, code, edate, old_sh, new_sh, conv_price, disc_at, diff in rows:
        if id_ not in best or diff < best[id_][-1]:
            best[id_] = (code, edate, old_sh, new_sh, conv_price, disc_at, diff)

    fixed = skipped_bad_shares = skipped_no_price = 0
    for id_, (code, edate, old_sh, new_sh, conv_price, disc_at, diff) in best.items():
        if old_sh is None or new_sh is None or old_sh < 100000 or new_sh <= old_sh:
            skipped_bad_shares += 1
            continue
        shares_issued = new_sh - old_sh
        if shares_issued <= 0 or shares_issued / old_sh > 5:
            skipped_bad_shares += 1
            continue
        cur.execute("""
            SELECT close FROM price_history
            WHERE stock_code=%s AND date::date < %s::date AND close > 0
            ORDER BY date DESC LIMIT 1
        """, (code, edate))
        pre = cur.fetchone()
        if not pre:
            skipped_no_price += 1
            continue
        pre_price = float(pre[0])
        terp = (pre_price * old_sh + float(conv_price) * shares_issued) / (old_sh + shares_issued)
        if terp <= 0 or pre_price <= 0:
            skipped_no_price += 1
            continue
        factor = terp / pre_price
        if not (0.1 <= factor <= 1.0):
            skipped_bad_shares += 1
            continue
        cur.execute("""
            UPDATE corporate_action_events SET
              backward_price_factor=%s, adjustment_status='factor_confirmed',
              confidence=0.75, source=source || '+terp_dilution_match_followup',
              note=%s, updated_at=now()::text
            WHERE id=%s
        """, (factor, f"TERP followup {date.today().isoformat()} conv={conv_price} pre={pre_price}", id_))
        fixed += 1
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM corporate_action_events WHERE adjustment_status='review_required'")
    after_total = cur.fetchone()[0]

    return {
        "candidates_seen": len(best),
        "newly_confirmed": fixed,
        "skipped_bad_shares": skipped_bad_shares,
        "skipped_no_price": skipped_no_price,
        "review_required_before": before_total,
        "review_required_after": after_total,
    }


def main():
    conn = psycopg.connect(PG_URL)
    today = date.today().strftime("%Y%m%d")
    report = {"date": datetime.now().isoformat(timespec="seconds")}
    try:
        report["rights_issue_terp"] = confirm_rights_issue_terp(conn)
        log.info("결과: %s", json.dumps(report, ensure_ascii=False))
    finally:
        conn.close()

    out_dir = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/corporate_action_followup")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"followup_{today}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
