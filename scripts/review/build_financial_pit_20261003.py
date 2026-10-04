#!/usr/bin/env python3
"""시점(point-in-time) 재무 사실 테이블 `financial_facts_pit` 재구축(2026-10-03, FINANCIAL_STATEMENTS.md §2-5, Codex 의견 §9-1-4).

표시값(운영 테이블)은 최신 재작성값으로 가지만, 백테스트는 '그 시점에 알 수 있던 값'을 써야 한다(미래 참조 방지).
이 테이블은 운영 테이블을 덮어쓰기 전에 값의 이력을 보존한다.
  as_reported : 해당 기간 보고서의 당기(thstrm) 값 — DART API는 기재정정까지 반영한 그 보고서의 최종본을 준다
  restated    : 다음 연도 사업보고서의 전기(frmtrm) 값(vals_prev) — 재작성 값
available_at(공개 시점 추정): 실제 접수일(rcept_dt)이 없으면 법정 제출기한으로 보수적 추정
  분기·반기 = 기간 말 + 45일, 사업보고서 = 회계연도 말 + 90일, 재작성(다음 해 사업보고서) = 다음 회계연도 말 + 90일
원천: research_outputs/financial_rereview_20261002/dart_cf_full.jsonl, dart_cf_2016_2022.jsonl(같은 키는 마지막 줄이 우선).
값 단위·정의는 DART 원문 그대로(손익: 분기=3개월, 현금흐름: 누적 YTD, 순이익·자본은 total/parent 둘 다). 보고통화 종목은 원통화.
매 실행 전체 교체.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
QEND = {1: (3, 31), 2: (6, 30), 3: (9, 30), 0: (12, 31)}
DDL = """CREATE TABLE IF NOT EXISTS financial_facts_pit (
    stock_code TEXT NOT NULL, year INTEGER NOT NULL, quarter INTEGER NOT NULL, report_type TEXT NOT NULL, field TEXT NOT NULL,
    value_kind TEXT NOT NULL, value DOUBLE PRECISION, source_report TEXT NOT NULL, available_at DATE NOT NULL,
    available_basis TEXT NOT NULL, recorded_at TEXT NOT NULL, run_id TEXT NOT NULL,
    PRIMARY KEY (stock_code, year, quarter, report_type, field, value_kind))"""


def avail(y, q, kind, code=None):
    """기간 종료일 + 법정기한(분·반기 45일, 사업보고서 90일). 비12월 결산은 fiscal_period.period_end 기준(2026-10-04)."""
    import calendar
    import fiscal_period as fp
    if kind == "restated":  # 다음 회계연도 사업보고서 제출 기한
        pe = fp.period_end(code, y + 1, 0) if code else f"{y + 1}-12"
        yy, mm = int(pe[:4]), int(pe[5:7])
        return date(yy, mm, calendar.monthrange(yy, mm)[1]) + timedelta(days=90)
    pe = fp.period_end(code, y, q) if code else f"{y}-{QEND[q][0]:02d}"
    yy, mm = int(pe[:4]), int(pe[5:7])
    return date(yy, mm, calendar.monthrange(yy, mm)[1]) + timedelta(days=90 if q == 0 else 45)


def main():
    last = {}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("ok") and d.get("fs"):
                last[(d["code"], d["year"], d["q"])] = d
    run_id = f"pit_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    rows = {}
    import fiscal_period as fp
    fp.fiscal_month_map()
    for (code, y0, q), d in last.items():
        fs = d["fs"]
        y, _ = fp.to_fiscal(code, y0, q, q == 0)  # 2026-10-04: 회계 기준 키(비12월 결산)
        rep = {0: "사업보고서", 1: "1분기보고서", 2: "반기보고서", 3: "3분기보고서"}[q]
        for f, v in (d.get("vals") or {}).items():
            rows[(code, y, q, fs, f, "as_reported")] = (v, f"{y} {rep} 당기", avail(y, q, "as_reported", code))
        if q == 0:
            for f, v in (d.get("vals_prev") or {}).items():
                # y년 사업보고서의 전기 칸 = (y-1)년 재작성 값
                rows[(code, y - 1, 0, fs, f, "restated")] = (v, f"{y} 사업보고서 전기", avail(y - 1, 0, "restated", code))
    conn = connect_primary_db(timeout=900)
    conn.execute(DDL)
    conn.execute("DELETE FROM financial_facts_pit")
    data = [(k[0], k[1], k[2], k[3], k[4], k[5], v[0], v[1], v[2].isoformat(), "법정기한 추정", now, run_id) for k, v in rows.items()]
    for i in range(0, len(data), 10000):
        conn.executemany("""INSERT INTO financial_facts_pit(stock_code,year,quarter,report_type,field,value_kind,value,source_report,
                            available_at,available_basis,recorded_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", data[i:i + 10000])
    conn.commit()
    n_rest = sum(1 for k in rows if k[5] == "restated")
    print(run_id, f"as_reported {len(rows) - n_rest:,} / restated {n_rest:,}")


if __name__ == "__main__":
    main()
