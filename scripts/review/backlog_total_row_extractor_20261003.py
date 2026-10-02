#!/usr/bin/env python3
"""수주잔고 보조 추출기 — 원문 발췌의 '합계' 행에서 수주잔고를 엄격하게 뽑는다(2026-10-03 독립 재검토).

규칙(전부 만족할 때만 값 반환):
  1. 발췌에 원화 단위 선언(백만원/천원/억원/원)이 있고 외화 단위 선언이 없다.
  2. '수주잔고' 헤더 이후에 '합 계'/'합계' 행이 있다.
  3. 합계 행의 숫자들 중 마지막 값(수주잔고 열은 표의 마지막 금액 열)을 쓴다. 숫자가 2개 미만이면 포기.
--eval: 현재 값이 '원문에 있음 + 매출비 정상(0.1%~50배)'인 행에서 추출값 일치율 측정.
--apply: dart_backlog_quarterly 중 값이 NULL 이고 발췌에 수주잔고+숫자가 있는 행을 채움(매출비 정상 범위만), order_backlog 투영.
"""
import argparse
import collections
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

FX = re.compile(r"단\s*위\s*[:：]?\s*(?:천\s*)?(USD|US\$|달러|EUR|유로|JPY|엔화|CNY|위안|\$)", re.I)
UNIT = re.compile(r"단\s*위\s*[:：]?[^)]{0,30}?(조원|억원|백만원|천원|원)")
MULT = {"조원": 1e12, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1}
NUM = re.compile(r"-?\d{1,3}(?:,\d{3})+|-?\d+")


def extract(text):
    t = re.sub(r"\s+", " ", text or "")
    if FX.search(t):
        return None
    i = t.find("수주잔고")
    if i < 0:
        i = t.find("수주 잔고")
    if i < 0:
        return None
    units = [m for m in UNIT.finditer(t) if m.start() < i + 400]
    if not units:
        return None
    unit = units[-1].group(1)
    m = re.search(r"합\s*계", t[i:])
    if not m:
        return None
    seg = t[i + m.end(): i + m.end() + 200]
    if seg.lstrip().startswith(("'", "’", "‘")):  # "합계 '20.1~9월 ..." 같은 기간 표기 표 — 열 구조가 달라 포기
        return None
    seg = re.sub(r"\((\d[\d,]*)\)", r"-\1", seg)  # 괄호 음수 (103) → -103
    seg = re.split(r"[가-힣A-Za-z※*$]|\s\d{1,2}\.\s", " " + seg.strip(), maxsplit=1)[0]  # 다음 목차 번호('5. 위험관리') 전에서 끊는다
    nums = [n for n in NUM.findall(seg) if n not in ("-",)]
    if len(nums) < 2:
        return None
    v = float(nums[-1].replace(",", ""))
    if v <= 0:
        return None
    return v, unit, v * MULT[unit]


def load(conn):
    return conn.execute("""
        SELECT b.stock_code, b.fiscal_year, b.fiscal_quarter, b.report_type, b.backlog_amount, b.backlog_amount_krw, b.source_excerpt,
          (SELECT f.revenue FROM financial_data f WHERE f.stock_code=b.stock_code AND f.year=b.fiscal_year-1 AND f.is_annual AND f.revenue>0
             ORDER BY (f.report_type='CFS') DESC LIMIT 1)
        FROM dart_backlog_quarterly b""").fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    rows = load(conn)
    if a.eval:
        st = collections.Counter()
        for code, y, q, fs, amt, krw, exc, rev in rows:
            if not amt or not krw or not rev or not (0.001 <= krw / rev <= 50):
                continue
            if f"{amt:,.0f}" not in (exc or ""):
                continue
            r = extract(exc)
            if r is None:
                st["추출 안 됨"] += 1
            elif abs(r[2] - krw) <= max(1e6, krw * 0.005):
                st["일치"] += 1
            else:
                st["불일치"] += 1
        tot = st["일치"] + st["불일치"]
        print(dict(st), f"추출된 것 중 정확도 {st['일치']/tot*100:.1f}%" if tot else "")
        return
    fills = []
    for code, y, q, fs, amt, krw, exc, rev in rows:
        if amt is not None:
            continue
        r = extract(exc)
        if r is None:
            continue
        if rev and not (0.001 <= r[2] / rev <= 50):
            continue
        fills.append((code, y, q, fs, r))
    print("채울 행", len(fills))
    if not a.apply:
        return
    run_id = f"backlog_total_row_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for code, y, q, fs, (v, unit, krw) in fills:
        conn.execute("""UPDATE dart_backlog_quarterly SET backlog_amount=?, backlog_unit=?, backlog_amount_krw=?, backlog_confidence=0.85,
                        parser_version=COALESCE(parser_version,'')||'+totalrow_20261003', updated_at=?
                        WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=? AND backlog_amount IS NULL""",
                     (v, unit, krw, now, code, y, q, fs))
        conn.execute("""UPDATE order_backlog SET backlog_amount=?, backlog_unit='원', backlog_normalized=?
                        WHERE stock_code=? AND year=? AND quarter=? AND data_source='dart_backlog' AND backlog_amount IS NULL""",
                     (krw, krw / 1e6, code, y, q))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "dart_backlog_quarterly+order_backlog", "원문에 수주잔고 숫자가 있는데 NULL인 행", len(fills),
                  "원화 단위·수주잔고 헤더·합계 행 마지막 값, 매출비 0.1%~50배", "NULL", "합계 행 값(신뢰도 0.85)",
                  "scripts/review/backlog_total_row_extractor_20261003.py", run_id))
    conn.commit()
    print("적용 완료", run_id)


if __name__ == "__main__":
    main()
