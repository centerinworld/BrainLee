"""D-6(2026-10-09): 흐름 항목 4분기 = 연간 − 3분기 보고서 누적. 옛 '연간 − (Q1+Q2+Q3)' 계산 코드가 다시 들어오지 않는지 검사."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAT = re.compile(r"-\s*q1\[\w+\]\s*-\s*q2\[\w+\]\s*-\s*q3\[|-\s*f1\[f\]\s*-\s*f2\[f\]\s*-\s*f3\[f\]|ann\[f\]\s*-\s*sum\(|\ba\s*-\s*sum\(qs\)|-\s*\(Q1\+Q2\+Q3 원문\)\"")
FILES = ["scripts/review/apply_restated_annual_20261004.py", "scripts/review/apply_ofs_extra_20261009.py",
         "scripts/review/apply_dart_refetch_20261002.py", "scripts/review/fix_financial_internal_20261002.py",
         "scripts/review/fix_external_agree_db_diff_20261009.py", "scripts/review/unify_q4_ytd3_20261009.py",
         "scripts/review/classify_fix_unclassified_fin_20261009.py"]


def test_no_legacy_q4_code():
    bad = []
    for f in FILES:
        for i, line in enumerate((ROOT / f).read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if PAT.search(code):
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "옛 4분기 공식 코드:\n" + "\n".join(bad)
