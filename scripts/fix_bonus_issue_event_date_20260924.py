#!/usr/bin/env python3
"""무상증자(bonus_issue) factor_confirmed 이벤트의 event_date 를 배정기준일 → 권리락일(실제 가격 단절일)로 정정.

근거(2026-09-24, docs/handoff_corp_action_event_date_offset_20260924.md): 218건 중 153건에서 실제 가격 단절이
event_date 보다 1거래일 앞선다. KRX 권리락일 = 신주배정기준일의 직전 영업일(T+2 결제)이므로 event_date 가 배정기준일이다.

안전장치: 기본 dry-run. --apply 시 백업 테이블 생성 → 트랜잭션 UPDATE → data_fix_log(run_id) 기록.
정정 대상 = (a) 가격 단절이 event_date 보다 정확히 1행 앞서고(비율이 factor ±25% 이내) (b) event_date 당일에는 단절이 없으며
(c) 정정 날짜가 해당 종목 price_history 의 직전 거래일이고 (d) (stock_code, 새 event_date, event_type) 유일성 충돌이 없는 건만.
--verify-dart N: 근거 공시(evidence_rcept_no)의 '신주배정기준일'이 event_date 와 일치하는지 대조(N>0 무작위 샘플, N<0 전 후보). 불일치 건은 후보에서 제외.

실행: cd runtime && set -a; . ./.env; set +a; venv/bin/python3 scripts/fix_bonus_issue_event_date_20260924.py [--verify-dart 12] [--apply]
"""
import argparse, io, os, re, sys, uuid, zipfile
from collections import Counter
from datetime import datetime

import psycopg
import requests

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--verify-dart", type=int, default=0)
args = ap.parse_args()

c = psycopg.connect(re.sub(r"^postgresql\+psycopg", "postgresql", os.environ["POSTGRES_DATABASE_URL"]))
RUN_ID = f"bonus_event_date_fix_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"

ev = c.execute("""SELECT id, stock_code, event_date::text, backward_price_factor, evidence_rcept_no, note
                  FROM corporate_action_events
                  WHERE event_type='bonus_issue' AND adjustment_status='factor_confirmed'
                    AND backward_price_factor IS NOT NULL AND backward_price_factor<0.9 AND event_date>='2019-01-01'
                  ORDER BY event_date""").fetchall()
cands, skipped = [], Counter()
for eid, code, d, f, rcept, note in ev:
    f = float(f)
    px = c.execute("""SELECT left(date::text,10), close FROM price_history WHERE stock_code=%s AND close>0
                      AND left(date::text,10) BETWEEN to_char(%s::date-8,'YYYY-MM-DD') AND to_char(%s::date+5,'YYYY-MM-DD') ORDER BY date""", (code, d, d)).fetchall()
    ds, cl = [p[0] for p in px], [float(p[1]) for p in px]
    if d not in ds or len(ds) < 4:
        skipped["가격부족"] += 1; continue
    i = ds.index(d)
    def hit(k): return 1 <= k < len(cl) and abs(cl[k] / cl[k - 1] / f - 1) < 0.25
    if hit(i):
        skipped["당일 단절(정합)"] += 1; continue
    if not hit(i - 1) or i - 1 < 1:
        skipped["-1일 단절 아님"] += 1; continue
    new_d = ds[i - 1]
    if c.execute("SELECT 1 FROM corporate_action_events WHERE stock_code=%s AND event_date::text=%s AND event_type='bonus_issue'", (code, new_d)).fetchone():
        skipped["유일성 충돌"] += 1; continue
    cands.append((eid, code, d, new_d, f, rcept, note))
print(f"[{RUN_ID}] 검사 {len(ev)}건 → 정정 후보 {len(cands)}건, 제외 {dict(skipped)}")

# ── DART 원문 대조(샘플) ──────────────────────────────────────────────
def dart_record_date(rcept):
    keys = [os.environ.get(k) for k in ("DART_API_KEY", "DART_API_KEY2", "DART_API_KEY3", "DART_API_KEY4") if os.environ.get(k)]
    for key in keys:
        r = requests.get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key": key, "rcept_no": rcept}, timeout=30)
        if r.content[:2] != b"PK":
            if b"020" in r.content[:400]:
                continue  # 키 한도 초과 → 다음 키
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        txt = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist())
        txt = re.sub(r"<[^>]+>", " ", txt)
        m = re.search(r"신주배정기준일[^0-9]{0,40}(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", txt)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return None
    return None

if args.verify_dart:
    # 전 후보(또는 무작위 N건)를 DART 원문과 대조. 불일치 건은 정정 후보에서 제외하고 보고(수동 검토 대상).
    import random
    random.seed(20260924)
    pool = [x for x in cands if x[5]]
    targets = pool if args.verify_dart < 0 else random.sample(pool, min(args.verify_dart, len(pool)))
    tset = {x[0] for x in targets}
    ok = unk = 0; excluded = []
    for eid, code, d, new_d, f, rcept, note in targets:
        rd = dart_record_date(rcept)
        if rd is None: unk += 1
        elif rd == d: ok += 1
        else:
            excluded.append((eid, code, d, rd, rcept))
    print(f"DART 대조 {len(targets)}건: 일치 {ok} / 불일치 {len(excluded)} / 확인불가 {unk}(가격 단절 증거만으로 유지)")
    for e in excluded: print(f"  제외(불일치) {e[1]} event_date={e[2]} DART 기준일={e[3]} {e[4]}")
    bad_ids = {e[0] for e in excluded}
    cands = [x for x in cands if x[0] not in bad_ids]
    print(f"최종 정정 후보 {len(cands)}건")
    if args.verify_dart > 0 and ok < 10:
        print("샘플 일치가 10건 미만이라 중단합니다."); sys.exit(2)

if not args.apply:
    print("dry-run 종료 (--apply 로 적용). 예시:")
    for x in cands[:8]: print("  ", x[1], x[2], "→", x[3], f"factor={x[4]:.4f}")
    sys.exit(0)

# ── 적용: 백업 → UPDATE → data_fix_log ───────────────────────────────
ids = [x[0] for x in cands]
c.commit()  # 위 조회로 열린 암묵 트랜잭션을 종료(안 하면 아래 transaction()이 세이브포인트가 되어 커밋되지 않음)
with c.transaction():
    c.execute("CREATE TABLE IF NOT EXISTS corporate_action_events_backup_20260924 AS SELECT * FROM corporate_action_events")
    n = 0
    for eid, code, d, new_d, f, rcept, note in cands:
        tag = f"[{RUN_ID}] event_date {d}(배정기준일)→{new_d}(권리락일)"
        c.execute("UPDATE corporate_action_events SET event_date=%s::date, note=CASE WHEN COALESCE(note,'')='' THEN %s ELSE note||' | '||%s END, updated_at=%s WHERE id=%s",
                  (new_d, tag, tag, datetime.now().isoformat(timespec="seconds"), eid))
        n += 1
    c.execute("""INSERT INTO data_fix_log (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
                 VALUES ('corporate_action_events','bonus_issue factor_confirmed event_date',%s,
                         'event_date: 신주배정기준일 -> 권리락일(실제 가격 단절일, 직전 거래일)',
                         %s,%s,'fix_bonus_issue_event_date_20260924.py',%s)""",
              (n, f"{n} rows event_date=배정기준일", f"{n} rows event_date=권리락일 (backup corporate_action_events_backup_20260924)", RUN_ID))
c.commit()
chk = c.execute("SELECT COUNT(*) FROM corporate_action_events WHERE note LIKE %s", (f"%{RUN_ID}%",)).fetchone()[0]
assert chk == n, f"커밋 검증 실패: 기록 {n} != 반영 {chk}"
print(f"적용 완료(커밋 검증 {chk}건): {n}건, run_id={RUN_ID}")
