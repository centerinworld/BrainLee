#!/usr/bin/env python3
"""corporate_action_events.event_date 와 실제 가격 단절일의 오프셋 점검 (읽기 전용, DB 미변경).

2026-09-24 발견: 무상증자(bonus_issue) factor_confirmed 218건 중 153건에서 실제 가격 단절이 event_date 보다
정확히 하루 앞선다(정합 4건). 권리락일이 아니라 배정기준일이 event_date 로 들어간 것으로 보임(액면분할은 9/10 정합).
백테스트 조정계수가 하루 뒤 적용되어 그 하루가 인위적 왜곡이 되고, 재등록 게이트(price_integrity)가
`confirmed_corporate_action`으로 거부한다(신진에스엠 138070 2022-08-08, 솔루스첨단소재 336370 2024-01-08).

실행: cd runtime && set -a; . ./.env; set +a; venv/bin/python3 scripts/audit_corp_action_event_date_offset_20260924.py [--csv out.csv]
"""
import argparse, csv, os, re
from collections import Counter
import psycopg

ap = argparse.ArgumentParser(); ap.add_argument("--csv", default="")
args = ap.parse_args()
c = psycopg.connect(re.sub(r"^postgresql\+psycopg", "postgresql", os.environ["POSTGRES_DATABASE_URL"]))
ev = c.execute("""SELECT id, stock_code, event_date::text, event_type, backward_price_factor FROM corporate_action_events
                  WHERE adjustment_status='factor_confirmed' AND backward_price_factor IS NOT NULL
                    AND backward_price_factor<0.9 AND event_date>='2019-01-01' ORDER BY event_type, event_date""").fetchall()
summary, by_type, rows_out = Counter(), {}, []
for eid, code, d, typ, f in ev:
    f = float(f)
    px = c.execute("""SELECT left(date::text,10), close FROM price_history WHERE stock_code=%s AND close>0
                      AND left(date::text,10) BETWEEN to_char(%s::date-8,'YYYY-MM-DD') AND to_char(%s::date+5,'YYYY-MM-DD') ORDER BY date""", (code, d, d)).fetchall()
    ds, cl = [p[0] for p in px], [float(p[1]) for p in px]
    if d not in ds or len(ds) < 4:
        summary["가격부족"] += 1; continue
    i, best = ds.index(d), None
    for off in (-2, -1, 0, 1, 2):
        k = i + off
        if 1 <= k < len(cl):
            r = cl[k] / cl[k - 1]
            if abs(r / f - 1) < 0.25 and (best is None or abs(r / f - 1) < best[1]):
                best = (off, abs(r / f - 1), ds[k])
    label = {0: "정합(0)", -1: "하루 이른 단절(-1)", -2: "이틀 이른 단절(-2)"}.get(best[0] if best else None,
            "늦은 단절(+)" if best and best[0] > 0 else "단절 미확인")
    summary[label] += 1; by_type.setdefault(typ, Counter())[label] += 1
    rows_out.append((eid, code, d, typ, f, label, best[2] if best else ""))
print("검사 대상:", len(ev), dict(summary))
for t, cn in by_type.items():
    print(" ", t, dict(cn))
if args.csv:
    with open(args.csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["event_id", "stock_code", "event_date", "event_type", "factor", "판정", "실제단절일"]); w.writerows(rows_out)
    print("CSV:", args.csv)
