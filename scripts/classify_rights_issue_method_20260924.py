#!/usr/bin/env python3
"""유상증자 이벤트를 DART 유상증자결정 원문의 '증자방식'으로 분류하고 신주배정기준일을 추출한다 (읽기 전용 → CSV/분류 테이블).

목적(2026-09-24): rights_issue factor_confirmed 515건 중 제3자배정·일반공모는 신주배정기준일/권리락이 없어 가격 단절로
event_date 를 검증할 수 없다. 권리락이 있는 '주주배정' 부분집합만 분리해 별도 검증하기 위한 분류.

산출: research_outputs/rights_issue_method_20260924.csv, 테이블 rights_issue_method_20260924(event_id, method, record_date, fetched_at).
재개 가능(이미 분류된 event_id 건너뜀). DART 한도(status 020) 시 키 로테이션, 전부 소진되면 중단.
실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/classify_rights_issue_method_20260924.py
"""
import csv, io, os, re, sys, time, zipfile
from datetime import datetime
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parents[1]
c = psycopg.connect(re.sub(r"^postgresql\+psycopg", "postgresql", os.environ["POSTGRES_DATABASE_URL"]), autocommit=True)
c.execute("""CREATE TABLE IF NOT EXISTS rights_issue_method_20260924 (
    event_id BIGINT PRIMARY KEY, stock_code TEXT, event_date TEXT, rcept_no TEXT, method TEXT, record_date TEXT, fetched_at TEXT)""")
done = {r[0] for r in c.execute("SELECT event_id FROM rights_issue_method_20260924").fetchall()}
ev = c.execute("""SELECT id, stock_code, event_date::text, evidence_rcept_no FROM corporate_action_events
                  WHERE event_type='rights_issue' AND adjustment_status='factor_confirmed' AND backward_price_factor<0.95
                    AND event_date>='2019-01-01' AND evidence_rcept_no IS NOT NULL ORDER BY event_date""").fetchall()
keys = [os.environ.get(k) for k in ("DART_API_KEY", "DART_API_KEY2", "DART_API_KEY3") if os.environ.get(k)]
dead = set()


def fetch(rno):
    for k in keys:
        if k in dead:
            continue
        try:
            r = requests.get("https://opendart.fss.or.kr/api/document.xml", params={"crtfc_key": k, "rcept_no": rno}, timeout=30)
        except Exception:
            time.sleep(2); continue
        if r.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(r.content))
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist())))
        if b"<status>020" in r.content[:300]:
            dead.add(k); continue
        return ""  # 014 등 문서 없음
    return None  # 모든 키 소진


def method_of(t):
    m = re.search(r"증자방식\s*[:：]?\s*([가-힣0-9 ()·/]{2,30}?)(?= \d| [0-9]\.|$| 5\.| 6\.| 7\.)", t) or re.search(r"증자방식[^가-힣]{0,6}([가-힣 ]{2,20})", t)
    s = m.group(1).strip() if m else ""
    for k, v in (("제3자", "제3자배정"), ("주주배정", "주주배정"), ("주주우선", "주주우선공모"), ("일반공모", "일반공모"), ("공모", "공모")):
        if k in s.replace(" ", ""):
            return v
    return s[:20] or "미상"


def record_date(t):
    m = re.search(r"신주배정기준일[^0-9]{0,40}(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일", t) or \
        re.search(r"신주배정\s*기준일[^0-9]{0,40}(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", t)
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


n = 0
for eid, code, d, rno in ev:
    if eid in done:
        continue
    t = fetch(rno)
    if t is None:
        print("DART 키 한도 소진 — 중단(재실행 시 이어서)"); break
    meth = method_of(t) if t else "문서없음"
    rd = record_date(t) if t else None
    c.execute("INSERT INTO rights_issue_method_20260924 VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
              (eid, code, d, rno, meth, rd, datetime.now().isoformat(timespec="seconds")))
    n += 1
    if n % 50 == 0:
        print(f"...{n}건 처리", flush=True)
rows = c.execute("SELECT event_id, stock_code, event_date, rcept_no, method, record_date FROM rights_issue_method_20260924 ORDER BY event_date").fetchall()
out = ROOT / "research_outputs" / "rights_issue_method_20260924.csv"
with open(out, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["event_id", "stock_code", "event_date", "rcept_no", "method", "record_date"]); w.writerows(rows)
from collections import Counter
print("총", len(rows), "건 | 방식 분포:", dict(Counter(r[4] for r in rows)))
print("기준일 추출 성공:", sum(1 for r in rows if r[5]), "→ CSV", out)
