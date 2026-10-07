#!/usr/bin/env python3
"""§26-2 ⑤: 2026년 종료 일반주식(ETF/ETN 제외) 폐지 사유를 DB에 있는 DART 공시(dart_disclosures)·이름 재사용으로 분류한다. 읽기 전용.
외부 사이트(KRX 상장폐지 현황)는 쓰지 않는다 — 공시 제목 근거만. 결과: research_outputs/closed_2026_classification_20261007.json"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db

RULES = [  # (분류, 공시 제목 키워드) — 위에서부터 먼저 맞는 것
    ("실제 폐지(상장폐지 공시)", ("상장폐지결정", "상장폐지에 따른 정리매매", "상장폐지 사유발생", "정리매매", "상장폐지 확정", "상장폐지(", "상장폐지사유", "자진상장폐지")),
    ("합병·주식교환 소멸", ("합병등종료보고서", "합병종료보고서", "주식교환", "주식이전", "소멸합병", "피합병")),
    ("스팩 합병·청산", ("스팩합병", "기업인수목적")),
]
def main():
    c = connect_primary_db(timeout=60)
    rows = c.execute("""
    WITH m AS (SELECT stock_code, MAX(COALESCE(effective_to,'9999-12-31')) mx, BOOL_OR(effective_to IS NULL) op,
      MAX(stock_name) nm, MAX(is_etf_etn) etf FROM security_master_history GROUP BY stock_code)
    SELECT stock_code, mx, nm FROM m WHERE NOT op AND mx>='2026-01-01' AND mx<'9999' AND etf=0 ORDER BY mx""").fetchall()
    out = []
    for code, mx, nm in rows:
        ds = [(d[0], d[1]) for d in c.execute("select rcept_dt, report_nm from dart_disclosures where stock_code=? and rcept_dt>=? order by rcept_dt", (code, "20250701")).fetchall()]
        cls, ev = None, []
        for name, kws in RULES:
            hit = [d for d in ds if any(k in d[1] for k in kws)]
            if hit:
                cls, ev = name, hit[-3:]
                break
        same = [tuple(s) for s in c.execute("select stock_code, MIN(effective_from), MAX(COALESCE(effective_to,'9999')) from security_master_history where stock_name=? and stock_code<>? group by stock_code", (nm, code)).fetchall()]
        if cls is None and not nm:
            cls = "신형 영숫자 코드·이름 없음(일반주식 아님 가능)"
        if cls is None and "스팩" in nm:
            cls = "스팩(공시 근거 약함)"
        if cls is None and same:
            cls = "코드 변경 의심(같은 이름 다른 코드)"; ev = same
        if cls is None:
            cls = "미분류(공시 제목에서 근거 못 찾음)"; ev = ds[-3:]
        after = [d for d in ds if d[0] > mx]
        if cls.startswith("미분류") and after:
            cls = "마스터 종료 뒤에도 공시 지속(비상장 전환·합병 후 존속 가능 — 외부 확인 필요)"; ev = after[-2:]
        out.append({"code": code, "name": nm, "master_end": mx, "class": cls, "evidence": ev, "disclosures_since_2025_07": len(ds)})
    cnt = {}
    for o in out: cnt[o["class"]] = cnt.get(o["class"], 0) + 1
    (ROOT / "research_outputs" / "closed_2026_classification_20261007.json").write_text(json.dumps({"counts": cnt, "rows": out}, ensure_ascii=False, indent=1))
    print(cnt)
    for o in out:
        if o["class"].startswith(("미분류", "코드 변경", "스팩(", "마스터 종료", "신형")):
            print(o["code"], o["name"], o["master_end"], o["class"], o["evidence"][-2:])
main()
