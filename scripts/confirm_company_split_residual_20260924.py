#!/usr/bin/env python3
"""company_split(주식분할결정 근거) review_required 잔여 30건 중 확정 가능한 건을 확정한다.

confirm_stock_split_from_company_split_20260924.py 는 이벤트 당일 가격단절 허용치를 ±25%로 잡아, 재개 첫날 상한가(+30%)로 시작한
정상 액면분할(분할비율 5배 → 가격비 0.26 = 0.2×1.3)을 놓쳤다. KRX 가격제한폭(±30%)에 맞춰 허용치를 ±32%로 재적용하고,
주식수가 줄어든 건(사실상 주식병합)은 stock_merge_or_reduction 으로 확정한다.

확정 조건(모두): (a) 공시명에 '주식분할' 포함·철회/취소 아님 (b) old/new_shares·share_ratio 존재
(c) 이벤트 당일 종가 비율 × share_ratio 가 1±0.32 (당일 단절 + 가격제한폭 내) (d) ratio≥1.5 → stock_split(계수 old/new),
ratio≤0.67 → stock_merge_or_reduction(계수 old/new>1) (e) 같은 종목 ±3일 내 다른 factor_confirmed 이벤트 없음
(f) (stock_code, event_date, event_type) 유일성 충돌 없음.
주식수 불변(ratio≈1)·날짜 어긋난 건은 확정하지 않는다(DART 원문상 실제 분할 상장일이 다른 날짜인 가짜 이벤트).
기본 dry-run, --apply 시 백업(corporate_action_events_backup_20260924_split) + data_fix_log + 커밋 검증.
"""
import argparse, os, re, uuid
from collections import Counter
from datetime import datetime

import psycopg

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
args = ap.parse_args()
c = psycopg.connect(re.sub(r"^postgresql\+psycopg", "postgresql", os.environ["POSTGRES_DATABASE_URL"]))
RUN_ID = f"split_residual_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
rows = c.execute("""SELECT id, stock_code, event_date::text, old_shares, new_shares, share_ratio, evidence_report_name, evidence_rcept_no, evidence_url
                    FROM corporate_action_events WHERE event_type='company_split' AND adjustment_status<>'factor_confirmed'
                      AND evidence_report_name LIKE '%%주식분할%%' ORDER BY event_date""").fetchall()
ok, skip = [], Counter()
for eid, code, d, old, new, ratio, rname, rno, url in rows:
    if "철회" in rname or "취소" in rname: skip["철회/취소"] += 1; continue
    if not (old and new and ratio): skip["비율 결측"] += 1; continue
    ratio = float(ratio)
    if 0.67 < ratio < 1.5: skip["주식수 불변(가짜/날짜어긋남)"] += 1; continue
    px = c.execute("""SELECT left(date::text,10), close FROM price_history WHERE stock_code=%s AND close>0
                      AND left(date::text,10) BETWEEN to_char(%s::date-8,'YYYY-MM-DD') AND to_char(%s::date+5,'YYYY-MM-DD') ORDER BY date""", (code, d, d)).fetchall()
    ds, cl = [p[0] for p in px], [float(p[1]) for p in px]
    if d not in ds or ds.index(d) < 1: skip["가격부족"] += 1; continue
    i = ds.index(d)
    if abs(cl[i] / cl[i - 1] * ratio - 1) >= 0.32: skip["당일 가격단절 불일치(±32%)"] += 1; continue
    if c.execute("""SELECT 1 FROM corporate_action_events WHERE stock_code=%s AND id<>%s AND adjustment_status='factor_confirmed'
                    AND backward_price_factor IS NOT NULL AND event_date::date BETWEEN %s::date-3 AND %s::date+3 LIMIT 1""", (code, eid, d, d)).fetchone():
        skip["±3일 내 기확정"] += 1; continue
    etype = "stock_split" if ratio >= 1.5 else "stock_merge_or_reduction"
    if c.execute("SELECT 1 FROM corporate_action_events WHERE stock_code=%s AND event_date::text=%s AND event_type=%s AND id<>%s", (code, d, etype, eid)).fetchone():
        skip["유일성 충돌"] += 1; continue
    ok.append((eid, code, d, etype, float(old) / float(new), ratio, rname, rno, url))
print(f"[{RUN_ID}] 잔여 {len(rows)}건 → 확정 {len(ok)}건, 제외 {dict(skip)}")
for x in ok: print("  ", x[1], x[2], x[3], f"factor={x[4]:.4f} (ratio {x[5]:.3g})")
if not args.apply:
    print("dry-run 종료 (--apply 로 적용)"); raise SystemExit(0)
c.commit()
with c.transaction():
    c.execute("CREATE TABLE IF NOT EXISTS corporate_action_events_backup_20260924_split AS SELECT * FROM corporate_action_events WHERE false")
    c.execute("INSERT INTO corporate_action_events_backup_20260924_split SELECT * FROM corporate_action_events WHERE id = ANY(%s)", ([x[0] for x in ok],))
    now = datetime.now().isoformat(timespec="seconds")
    for eid, code, d, etype, factor, ratio, rname, rno, url in ok:
        c.execute("""UPDATE corporate_action_events SET event_type=%s, backward_price_factor=%s, adjustment_status='factor_confirmed', confidence=0.8,
                     source='dart_disclosure+marcap_jump+price_break', note=%s, updated_at=%s WHERE id=%s""",
                  (etype, factor, f"[{RUN_ID}] company_split→{etype} 재분류 확정: 근거 '{rname[:36]}', 계수=old/new, 이벤트 당일 가격단절이 분할비율과 가격제한폭(±30%) 내 정합", now, eid))
    c.execute("""INSERT INTO data_fix_log (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
                 VALUES ('corporate_action_events','company_split(주식분할결정) 잔여 -> stock_split/stock_merge_or_reduction factor_confirmed',%s,
                         'ratio>=1.5 stock_split / ratio<=0.67 stock_merge_or_reduction, factor=old/new, 당일 가격단절 ±32%%, ±3일 기확정 없음',
                         'company_split review_required',%s,'confirm_company_split_residual_20260924.py',%s)""",
              (len(ok), "factor_confirmed (backup corporate_action_events_backup_20260924_split)", RUN_ID))
c.commit()
chk = c.execute("SELECT COUNT(*) FROM corporate_action_events WHERE note LIKE %s", (f"%{RUN_ID}%",)).fetchone()[0]
assert chk == len(ok), f"커밋 검증 실패 {chk}!={len(ok)}"
print(f"적용 완료(커밋 검증 {chk}건), run_id={RUN_ID}")
