#!/usr/bin/env python3
"""event_type='company_split' 으로 잘못 분류된 '주식분할결정'(단순 액면분할) 이벤트를 stock_split 으로 확정한다.

배경(2026-09-24): register_corporate_events_from_dart 가 만든 company_split review_required 153건 중 86건은 근거 공시가
'주식분할결정'(액면분할)이다. 확정 규칙은 build_corporate_action_adjustment_engine.py 와 동일(분할비율 ≥1.5, 계수 = old_shares/new_shares).
진짜 회사분할(분할/분할합병) 67건은 가치 이전이 있어 자동 확정하지 않는다(review_required 유지).

확정 조건(모두 충족): (a) 근거 공시명에 '주식분할' 포함·'철회/취소' 미포함 (b) share_ratio ≥ 1.5 이고 old/new_shares 존재
(c) event_date 당일 종가 단절 비율 × share_ratio 가 1±0.25 (가격 단절이 이벤트 당일) (d) 같은 종목 ±3일 안에 다른 factor_confirmed
이벤트 없음(이중 적용 방지) (e) (stock_code, event_date, 'stock_split') 유일성 충돌 없음.
기본 dry-run, --apply 시 백업 테이블 + data_fix_log(run_id).
실행: cd runtime && set -a; . ./.env; set +a; venv/bin/python3 scripts/confirm_stock_split_from_company_split_20260924.py [--apply]
"""
import argparse, os, re, uuid
from collections import Counter
from datetime import datetime

import psycopg

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
args = ap.parse_args()
c = psycopg.connect(re.sub(r"^postgresql\+psycopg", "postgresql", os.environ["POSTGRES_DATABASE_URL"]))
RUN_ID = f"split_confirm_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"

rows = c.execute("""SELECT id, stock_code, event_date::text, old_shares, new_shares, share_ratio, evidence_report_name, evidence_rcept_no, evidence_url
                    FROM corporate_action_events
                    WHERE event_type='company_split' AND adjustment_status<>'factor_confirmed'
                      AND evidence_report_name LIKE '%%주식분할%%' ORDER BY event_date""").fetchall()
ok, skip = [], Counter()
for eid, code, d, old, new, ratio, rname, rno, url in rows:
    if "철회" in rname or "취소" in rname: skip["철회/취소"] += 1; continue
    if not (old and new and ratio and float(ratio) >= 1.5): skip["비율<1.5/결측"] += 1; continue
    ratio = float(ratio)
    px = c.execute("""SELECT left(date::text,10), close FROM price_history WHERE stock_code=%s AND close>0
                      AND left(date::text,10) BETWEEN to_char(%s::date-8,'YYYY-MM-DD') AND to_char(%s::date+5,'YYYY-MM-DD') ORDER BY date""", (code, d, d)).fetchall()
    ds, cl = [p[0] for p in px], [float(p[1]) for p in px]
    if d not in ds or ds.index(d) < 1: skip["가격부족"] += 1; continue
    i = ds.index(d)
    if abs(cl[i] / cl[i - 1] * ratio - 1) >= 0.25: skip["당일 가격단절 불일치"] += 1; continue
    near = c.execute("""SELECT 1 FROM corporate_action_events WHERE stock_code=%s AND adjustment_status='factor_confirmed'
                        AND backward_price_factor IS NOT NULL AND id<>%s
                        AND event_date::date BETWEEN %s::date-3 AND %s::date+3 LIMIT 1""", (code, eid, d, d)).fetchone()
    if near: skip["±3일 내 기확정 이벤트(이중적용 방지)"] += 1; continue
    if c.execute("SELECT 1 FROM corporate_action_events WHERE stock_code=%s AND event_date::text=%s AND event_type='stock_split'", (code, d)).fetchone():
        skip["유일성 충돌"] += 1; continue
    ok.append((eid, code, d, float(old) / float(new), ratio, rname, rno, url))
print(f"[{RUN_ID}] 주식분할 후보 {len(rows)}건 → 확정 {len(ok)}건, 제외 {dict(skip)}")
for x in ok[:6]: print("  ", x[1], x[2], f"factor={x[3]:.4f} (ratio {x[4]:g})", x[5][:24])
if not args.apply:
    print("dry-run 종료 (--apply 로 적용)"); raise SystemExit(0)

c.commit()
with c.transaction():
    c.execute("CREATE TABLE IF NOT EXISTS corporate_action_events_backup_20260924_split AS SELECT * FROM corporate_action_events WHERE false")
    ids = [x[0] for x in ok]
    c.execute("INSERT INTO corporate_action_events_backup_20260924_split SELECT * FROM corporate_action_events WHERE id = ANY(%s)", (ids,))
    now = datetime.now().isoformat(timespec="seconds")
    for eid, code, d, factor, ratio, rname, rno, url in ok:
        c.execute("""UPDATE corporate_action_events SET event_type='stock_split', backward_price_factor=%s, adjustment_status='factor_confirmed',
                     confidence=0.85, source='dart_disclosure+marcap_jump+price_break',
                     note=%s, updated_at=%s WHERE id=%s""",
                  (factor, f"[{RUN_ID}] company_split→stock_split 재분류 확정: 근거 '{rname[:40]}', 계수=old/new, 이벤트 당일 가격 단절과 비율 정합", now, eid))
    c.execute("""INSERT INTO data_fix_log (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
                 VALUES ('corporate_action_events','company_split(주식분할결정) -> stock_split factor_confirmed',%s,
                         'event_type=stock_split, backward_price_factor=old_shares/new_shares (ratio>=1.5, 당일 가격단절 정합, ±3일 기확정 없음)',
                         'company_split review_required',%s,'confirm_stock_split_from_company_split_20260924.py',%s)""",
              (len(ok), f"stock_split factor_confirmed (backup corporate_action_events_backup_20260924_split)", RUN_ID))
c.commit()
chk = c.execute("SELECT COUNT(*) FROM corporate_action_events WHERE note LIKE %s", (f"%{RUN_ID}%",)).fetchone()[0]
assert chk == len(ok), f"커밋 검증 실패 {chk}!={len(ok)}"
print(f"적용 완료(커밋 검증 {chk}건), run_id={RUN_ID}")
