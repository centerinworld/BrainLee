#!/usr/bin/env python3
"""감가상각·CapEx 구성요소 테이블 `financial_dep_capex_components` 재구축(2026-10-03 사용자 승인, FINANCIAL_STATEMENTS.md §2-1).

한 숫자로 정의를 고르지 않고 구성요소를 따로 보존한다. 화면·신호는 목적에 맞게 조합해 쓴다.
  dep_cf_total      현금흐름표 조정 '감가상각비'(보통 유형+사용권) = FnGuide '유형자산감가상각비' = cash_flow_data.depreciation
  dep_ppe           주석 유형자산 감가상각(XBRL DepreciationPropertyPlantAndEquipment)
  dep_rou           주석 사용권자산 감가상각(리스, 현금 지출 아님)
  amort_intangible  무형자산상각비(현금흐름표 조정 우선, 없으면 주석) = FnGuide 기타무형자산상각비+개발비상각
  capex_ppe         유형자산 취득(현금흐름표) = FnGuide '유형자산의증가' = cash_flow_data.capex(_q)
  capex_intangible  무형자산 취득(현금흐름표) = FnGuide '무형자산의증가' — DART 수집기에 2026-10-03 추가, 수집분부터 채워짐
  ppe_rou_check     (dep_ppe+dep_rou)/dep_cf_total - 1
  cf_line_basis     현금흐름표 '감가상각비' 행 구성: 유형만(53%) / 유형+사용권(21%) / 기타 포함 / 주석과 구성 다름 — 회사마다 달라 FnGuide 값의 의미도 다르다
연간 감가상각 구성요소는 사업보고서 XBRL(xbrl_depreciation.jsonl), CapEx는 cash_flow_data(연간=누적, 분기=3개월).
보고통화 종목은 XBRL이 원통화라 감가상각 구성요소를 비운다(cash_flow_data 값은 원화 환산됨).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
DDL = """CREATE TABLE IF NOT EXISTS financial_dep_capex_components (
    stock_code TEXT NOT NULL, year INTEGER NOT NULL, quarter INTEGER NOT NULL, report_type TEXT NOT NULL,
    dep_cf_total DOUBLE PRECISION, dep_ppe DOUBLE PRECISION, dep_rou DOUBLE PRECISION, amort_intangible DOUBLE PRECISION,
    capex_ppe DOUBLE PRECISION, capex_intangible DOUBLE PRECISION, ppe_rou_check DOUBLE PRECISION, cf_line_basis TEXT,
    dep_source TEXT, capex_source TEXT, rcept_no TEXT, run_id TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (stock_code, year, quarter, report_type))"""


def main():
    conn = connect_primary_db(timeout=900)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    rows = {}
    # CapEx·현금흐름표 감가상각(운영 테이블, quarter 0 = 연간)
    for r in conn.execute("""SELECT stock_code, year, quarter, is_annual, report_type, capex, capex_q, depreciation, depreciation_q
                             FROM cash_flow_data WHERE year>=2016""").fetchall():
        code, y, q, ann, fs, capex, capex_q, dep, dep_q = tuple(r)
        k = (code, y, 0 if ann else q, fs)
        if k in rows:
            continue
        rows[k] = {"capex_ppe": abs(capex) if ann and capex is not None else (abs(capex_q) if capex_q is not None else None),
                   "dep_cf_total": abs(dep) if ann and dep is not None else None,
                   "capex_source": "cash_flow_data", "dep_source": "cash_flow_data(정의 미확인 — XBRL 대조 전)" if ann and dep is not None else None}
    dart_dep = {}
    # 무형자산 취득(DART 재수집 원문, 연간 누적만 — 분기 3개월은 누적 차분이 필요해 연간만)
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("q") == 0 and d.get("fs") and (d.get("vals") or {}).get("depreciation") is not None:
                dart_dep[(d["code"], d["year"], 0, d["fs"])] = abs(d["vals"]["depreciation"])
            v = (d.get("vals") or {}).get("capex_intangible")
            if d.get("q") == 0 and v is not None and d.get("fs"):
                rows.setdefault((d["code"], d["year"], 0, d["fs"]), {})["capex_intangible"] = abs(v)
    # 분기 3개월 감가상각(현금흐름표 본문 행, 누적 차분) — 본문에 행이 있는 회사만(약 20~35%), 2026-10-03
    ytd = {}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if p.exists():
            for line in open(p):
                d = json.loads(line)
                v = (d.get("vals") or {}).get("depreciation")
                if d.get("fs") and v is not None:
                    ytd[(d["code"], d["year"], d["q"], d["fs"])] = abs(v)
    for (code, y, q, fs), v in ytd.items():
        if code in fx or q == 0:
            continue
        prev = 0.0 if q == 1 else ytd.get((code, y, q - 1, fs))
        if prev is None or v - prev < 0:
            continue
        row = rows.setdefault((code, y, q, fs), {})
        row["dep_cf_total"], row["dep_source"] = v - prev, "dart_cf_statement(누적 차분)"
    for (code, y, q, fs), v in ytd.items():
        if q == 3 and code not in fx and ytd.get((code, y, 0, fs)) is not None and ytd[(code, y, 0, fs)] - v >= 0:
            row = rows.setdefault((code, y, 4, fs), {})
            row["dep_cf_total"], row["dep_source"] = ytd[(code, y, 0, fs)] - v, "dart_cf_statement(연간−3분기 누적)"
    # 감가상각 구성요소(사업보고서 XBRL 주석·조정)
    for line in open(SRC / "xbrl_depreciation.jsonl"):
        d = json.loads(line)
        if d["code"] in fx:
            continue
        for fs, v in (d.get("vals") or {}).items():
            k = (d["code"], d["year"], 0, fs)
            row = rows.setdefault(k, {})
            row["dep_ppe"] = v.get("dep_ppe")
            row["dep_rou"] = v.get("dep_rou")
            row["amort_intangible"] = v.get("adj_amort") if v.get("adj_amort") is not None else v.get("amort")
            # 현금흐름표 합계는 진짜 조정값만(유형자산 값으로 대신 채운 기존 운영값은 쓰지 않는다)
            adj = v.get("adj_dep") if v.get("adj_dep") is not None else v.get("adj_dep_custom")
            if adj is not None:
                row["dep_cf_total"] = adj
                row["dep_source"] = "xbrl_adj" if v.get("adj_dep") is not None else "xbrl_adj_custom"
            elif dart_dep.get(k) is not None:
                row["dep_cf_total"] = dart_dep[k]
                row["dep_source"] = "dart_cf_statement"
            else:
                row["dep_cf_total"] = None
                row["dep_source"] = "미수집(조정 감가상각 없음 — 재수집 대기)"
            row["rcept_no"] = d.get("rcept_no")
            t, pp, ro = row.get("dep_cf_total"), row.get("dep_ppe"), row.get("dep_rou") or 0
            if t and pp is not None:
                row["ppe_rou_check"] = round((pp + ro) / t - 1, 4)
                # 현금흐름표 '감가상각비' 행이 무엇을 담는지(회사마다 다름 — FnGuide는 이 행을 그대로 표시)
                row["cf_line_basis"] = ("유형만" if abs(pp / t - 1) <= 0.02 else
                                        "유형+사용권" if ro and abs((pp + ro) / t - 1) <= 0.02 else
                                        "기타 포함(투자부동산 등)" if (pp + ro) < t else "주석과 구성 다름")
    run_id = f"dep_capex_comp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    cols = ["dep_cf_total", "dep_ppe", "dep_rou", "amort_intangible", "capex_ppe", "capex_intangible", "ppe_rou_check", "cf_line_basis",
            "dep_source", "capex_source", "rcept_no"]
    data = [k + tuple(v.get(c) for c in cols) + (run_id, now) for k, v in rows.items() if any(v.get(c) is not None for c in cols[:6])]
    conn.execute("DROP TABLE IF EXISTS financial_dep_capex_components")  # 매 실행 전체 재생성(스키마 변경 반영)
    conn.execute(DDL)
    conn.execute("DELETE FROM financial_dep_capex_components")
    for i in range(0, len(data), 10000):
        conn.executemany(f"""INSERT INTO financial_dep_capex_components(stock_code,year,quarter,report_type,{','.join(cols)},run_id,updated_at)
                             VALUES ({','.join('?' * (4 + len(cols) + 2))})""", data[i:i + 10000])
    conn.commit()
    import collections
    print(run_id, f"{len(data):,}행", "현금흐름표 감가상각 행 구성:", dict(collections.Counter(v.get("cf_line_basis") for v in rows.values() if v.get("cf_line_basis"))))


if __name__ == "__main__":
    main()
