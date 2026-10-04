#!/usr/bin/env python3
"""수주잔고 원문 항등식 검증·정정(2026-10-04, FINANCIAL_STATEMENTS.md §2-4 수주잔고). 외부 비교 소스가 없어 원문 산술 항등식으로 판정한다.

대상: dart_backlog_quarterly 의 원문 발췌(source_excerpt). 발췌 안의 합계 행에서
  (A) 수주상황 표: 수주총액 − 기납품액 = 수주잔고   (행 안의 연속하지 않을 수도 있는 금액 세 개 a,b,c 로 a−b=c)
  (B) 계약잔액 증감표: 기초 + 변경 + 신규 − 당기수익(괄호=음수) = 기말   (연속 4~5개 숫자에서 앞의 합 = 마지막)
선언 단위((단위 : 원/천원/백만원/억원))를 곱해 원화로 환산.
판정:
  확인       : 저장값(backlog_amount_krw)이 항등식 결과와 일치(±1 단위)
  정정 후보  : 항등식은 성립하는데 저장값이 다름 → 항등식 결과(가장 큰 금액 해)가 정답 후보
  검증 불가  : 항등식을 찾지 못함
수량 열(343−162=181처럼 수량도 항등식을 만족)을 피하려고, 같은 행에서 항등식 해가 여럿이면 금액이 가장 큰 해를 쓴다.
기본 dry-run(측정·후보 파일). --apply 시 정정 후보만 반영(백업·data_fix_log).
"""
import argparse
import collections
import itertools
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
UNIT = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9}
NUM = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+\)?|\(?-?\d+\)?|(?<![\d\w])-(?![\d\w])")  # 단독 '-' = 0(공시 표기)
TOTAL = re.compile(r"(합\s*계|총\s*계|소\s*계|(?<![가-힣])계(?![가-힣]))")


def nums(s):
    out = []
    for t in NUM.findall(s):
        if t == "-":
            out.append(0.0)
            continue
        neg = t.startswith("(") or t.startswith("-")
        v = float(t.strip("()").replace(",", "").replace("-", "") or 0)
        out.append(-v if neg else v)
    return out


def unit_before(text, pos):
    best = None
    for m in re.finditer(r"단위\s*[:：]?\s*(십억원|억원|백만원|천원|원)", text[:pos]):
        best = m.group(1)
    return UNIT.get(best) if best else None


def solutions(text):
    """발췌에서 항등식 해(원화) 목록."""
    sols = []
    for m in TOTAL.finditer(text):
        seg = text[m.end(): m.end() + 220]
        seg = re.split(r"[가-힣]{2,}", seg)[0]  # 다음 글자 행 전까지
        ns = [v for v in nums(seg)]
        if len(ns) < 3:
            continue
        u0 = unit_before(text, m.start())
        u = u0 or 1
        # (A) a − b = c (순서 유지, 큰 금액만)
        for i, j, k in itertools.combinations(range(len(ns)), 3):
            a, b, c = ns[i], ns[j], ns[k]
            if a > 0 and b >= 0 and c >= 0 and abs(a - b - c) <= 1 and (c >= 1000 or (c == 0 and a >= 1000)):
                sols.append(("수주총액-기납품=잔고", c * u, c, u0 is not None))
        # (B) 연속 4~5개: 앞의 합 = 마지막
        for w in (4, 5):
            for s in range(0, len(ns) - w + 1):
                win = ns[s:s + w]
                if win[-1] > 0 and abs(sum(win[:-1]) - win[-1]) <= 1 and win[-1] >= 1000 and any(x < 0 for x in win[:-1]):
                    sols.append(("기초+증감=기말", win[-1] * u, win[-1], u0 is not None))
    return sols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    rows = [tuple(r) for r in conn.execute("""SELECT stock_code, fiscal_year, fiscal_quarter, report_type, backlog_amount_krw, source_excerpt
                                              FROM dart_backlog_quarterly""").fetchall()]
    st, fix, out = collections.Counter(), [], []
    for code, y, q, fs, v, ex in rows:
        if not ex:
            st["원문 발췌 없음"] += 1
            continue
        sols = solutions(ex)
        if not sols:
            st["검증 불가(항등식 없음)"] += 1
            out.append((code, y, q, v, None, "검증 불가"))
            continue
        # 확인: 저장값 = 항등식 해 × 표준 단위(단위 표기를 못 찾은 경우 포함)
        if v is not None and any(abs(s[2] * uu - v) <= max(1e3, abs(v) * 1e-6) for s in sols for uu in (1, 1e3, 1e6, 1e8)):
            st["확인(항등식 일치)"] += 1
            continue
        declared = [s for s in sols if s[3]]
        if not declared:
            st["검증 불가(단위 미표기)"] += 1
            out.append((code, y, q, v, None, "단위 미표기"))
            continue
        # 2026-10-05 표본 검증: '수주총액−기납품=잔고' 후보 8/8 정답, '기초+증감=기말' 후보는 기초값을 고르는 오류(013120) → 반영은 A 유형만
        a_type = [s for s in declared if s[0].startswith("수주총액")]
        if not a_type:
            st["정정 보류(기초+증감 유형)"] += 1
            out.append((code, y, q, v, max(declared, key=lambda s: s[1])[1], "기초+증감=기말(보류)"))
            continue
        best = max(a_type, key=lambda s: s[1])
        st["정정 후보(항등식≠저장값)"] += 1
        fix.append((code, y, q, fs, v, best[1], best[0]))
        out.append((code, y, q, v, best[1], best[0]))
    n_ver = st["확인(항등식 일치)"] + st["정정 후보(항등식≠저장값)"]
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    if n_ver:
        print(f"항등식으로 판정 가능한 행 중 저장값 일치율: {st['확인(항등식 일치)'] / n_ver * 100:.2f}% ({n_ver}행)")
    import csv
    with open(OUT / "backlog_identity_check_20261004.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stock_code", "fiscal_year", "fiscal_quarter", "stored_krw", "identity_krw", "identity_type"])
        w.writerows(out)
    if not a.apply or not fix:
        return
    run_id = f"backlog_identity_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("CREATE TABLE IF NOT EXISTS dart_backlog_quarterly_backup_identity_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM dart_backlog_quarterly WHERE false")
    for code, y, q, fs, old, new, kind in fix:
        conn.execute("INSERT INTO dart_backlog_quarterly_backup_identity_20261004 SELECT *, ? FROM dart_backlog_quarterly WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?",
                     (run_id, code, y, q, fs))
        conn.execute("UPDATE dart_backlog_quarterly SET backlog_amount_krw=?, backlog_confidence=0.95, parser_version='identity_v1', updated_at=? "
                     "WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?", (new, now, code, y, q, fs))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "dart_backlog_quarterly", "수주잔고 원문 항등식 정정", len(fix), "합계 행 수주총액−기납품=잔고 또는 기초+증감=기말, 선언 단위 환산",
                  json.dumps(dict(st), ensure_ascii=False), "원문 발췌 항등식", "scripts/review/verify_backlog_identity_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(fix))


if __name__ == "__main__":
    main()
