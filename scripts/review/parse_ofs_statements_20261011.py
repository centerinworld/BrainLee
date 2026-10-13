#!/usr/bin/env python3
"""사업보고서 본문의 별도 재무제표 표 추출 (2026-10-11) — 원인군 DB_MISSING_OFS(DART 재무 API가 별도를 주지 않음, 013).

원문: data_raw/dart_doc/<code>/<회계연도>_<rcept_no>.zip(fetch_dart_business_docs). 섹션 'N. 재무제표'(연결 아님) 안의 표에서
재무상태표(자산·부채·자본총계), 손익계산서(매출·영업이익·당기순이익), 현금흐름표(영업·투자·재무활동 현금흐름) 당기 열 값을 읽는다.
저장: 표 ofs_doc_values(stock_code, fiscal_year, field, value, unit, label, rcept_no, parsed_at) — 후보일 뿐, 반영은 fill_missing_from_raw가
FnGuide와 200만원 이내 일치할 때만(원칙 0).
사용: parse_ofs_statements_20261011.py [--codes a,b] [--all-docs]   (기본: 고정 분모의 별도 DB 없음 연간 종목-연도만)
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
from parse_business_docs_20261005 import cur_col, first_num, load, section, tables_with_unit  # noqa: E402

DOC = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
STMT = {"BS": r"재\s*무\s*상\s*태\s*표|대\s*차\s*대\s*조\s*표", "IS": r"손\s*익\s*계\s*산\s*서", "CF": r"현\s*금\s*흐\s*름\s*표"}
LABEL = {  # (표, 정규식) — 공백 제거 후 매칭, 첫 행만
    "total_assets": ("BS", r"^자산총계$"), "total_liabilities": ("BS", r"^부채총계$"), "total_equity": ("BS", r"^자본총계$"),
    "revenue": ("IS", r"^(매출액|수익\(매출액\)|영업수익|매출|수익)$"), "operating_profit": ("IS", r"^영업(이익|손실|이익\(손실\)|손익)$"),
    "net_income": ("IS", r"^(당기|분기|반기)순(이익|손실|이익\(손실\)|손익)$"),
    "operating_cf": ("CF", r"^영업활동(으로인한|으로부터의)?(순)?현금흐름$"), "investing_cf": ("CF", r"^투자활동(으로인한|으로부터의)?(순)?현금흐름$"),
    "financing_cf": ("CF", r"^재무활동(으로인한|으로부터의)?(순)?현금흐름$"),
}
DDL = """CREATE TABLE IF NOT EXISTS ofs_doc_values (stock_code TEXT, fiscal_year INT, field TEXT, value DOUBLE PRECISION, unit TEXT, label TEXT,
         rcept_no TEXT, parsed_at TEXT, PRIMARY KEY (stock_code, fiscal_year, field))"""


def signed(v, label):
    """라벨이 '…손실'(이익(손실)·손익 아님)인데 값이 양수로 적힌 경우 = 손실 → 음수(10-11, 052770 '영업손실' 9.5억 → −9.5억 = FnGuide)."""
    lab = re.sub(r"\s", "", label or "")
    if v is not None and v > 0 and re.search(r"(영업|당기|분기|반기)순?손실$", lab) and "이익" not in lab:
        return -v
    return v


def clean(s):
    return re.sub(r"[\s　]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?|^\d+\.|^[가-하]\.|\(주\d*\)|\(주석[\d,\s]*\)", "", s or "")


def parse_doc(path, title="재무제표"):
    x = load(path)
    sec = section(x, title)
    if not sec:
        return {}, "별도 재무제표 섹션 없음"
    out = {}
    cur = None  # 제목 표('재무상태표' 등)가 숫자 표 앞에 따로 있다(10-11 확인) → 직전 제목의 종류를 기억
    for grid, (mult, ukey), before in tables_with_unit(sec):
        head = " ".join(" ".join(r) for r in grid[:2]) if grid else ""
        t = next((k for k, p in STMT.items() if re.search(p, head)), None)
        if t:
            cur = None if "신탁" in head else t  # 증권사 신탁계정 재무제표는 회사 별도 재무제표가 아님
        kind = t or cur or next((k for k, p in STMT.items() if re.search(p, before[-300:])), None)
        if "신탁" in head or "신탁" in before[-200:]:
            continue
        if not kind or not mult:
            continue
        j = cur_col(grid)
        for row in grid:
            if not row:
                continue
            lab = clean(row[0])
            for f, (k, pat) in LABEL.items():
                if k != kind or f in out or not re.search(pat, lab):
                    continue
                v, raw = first_num(row, j if j is not None else 1)
                if v is None:  # 당기 열 표기가 없는 표: 숫자 첫 칸
                    v = next((n for n in (_n(c) for c in row[1:]) if n is not None), None)
                if v is not None:
                    out[f] = (signed(v, row[0]) * mult, ukey, row[0])
    return out, "ok" if out else "표에서 항목 못 찾음"


def parse_doc_q(path, title="재무제표"):
    """분기·반기보고서 본문: {field: (3개월 값, 누적 값, 단위, 라벨)} — 재무상태·현금흐름은 3개월 칸에도 분기말·누적 값을 그대로 둔다."""
    x = load(path)
    sec = section(x, title)
    if not sec:
        return {}, "별도 재무제표 섹션 없음"
    out, cur = {}, None
    for grid, (mult, ukey), before in tables_with_unit(sec):
        head = " ".join(" ".join(r) for r in grid[:2]) if grid else ""
        t = next((k for k, p in STMT.items() if re.search(p, head)), None)
        if t:
            cur = None if "신탁" in head else t
        kind = t or cur
        if "신탁" in head or not kind or not mult:
            continue
        i3 = ic = None
        for r in grid[:3]:  # 머리줄의 '3개월'·'누적' 위치(당기 = 첫 번째)
            for j, c in enumerate(r):
                cc = re.sub(r"\s", "", c or "")
                if i3 is None and cc == "3개월":
                    i3 = j
                if ic is None and cc == "누적":
                    ic = j
        j0 = cur_col(grid)
        for row in grid:
            if not row:
                continue
            lab = clean(row[0])
            for f, (k, pat) in LABEL.items():
                if k != kind or f in out or not re.search(pat, lab):
                    continue
                first = first_num(row, j0 if j0 is not None else 1)[0]
                v3 = first_num(row, i3)[0] if (kind == "IS" and i3 is not None) else first
                vc = first_num(row, ic)[0] if (kind == "IS" and ic is not None) else first
                if v3 is None and vc is None:
                    continue
                out[f] = (None if v3 is None else signed(v3, row[0]) * mult, None if vc is None else signed(vc, row[0]) * mult, ukey, row[0])
    return out, "ok" if out else "표에서 항목 못 찾음"


def _n(c):
    from parse_business_docs_20261005 import num
    return num(c)


DDL_Q = """CREATE TABLE IF NOT EXISTS ofs_doc_values_q (stock_code TEXT, fiscal_year INT, quarter INT, field TEXT, value_3m DOUBLE PRECISION,
           value_cum DOUBLE PRECISION, unit TEXT, label TEXT, rcept_no TEXT, parsed_at TEXT, PRIMARY KEY (stock_code, fiscal_year, quarter, field))"""
DOC_Q = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc_q")


def main_quarterly(conn):
    conn.execute(DDL_Q); conn.commit()
    st, rows = collections.Counter(), []
    now = datetime.now().isoformat(timespec="seconds")
    latest = {}
    for z in DOC_Q.glob("*/*Q*_*.zip"):
        m = re.match(r"(\d{4})Q(\d)_(\d+)\.zip", z.name)
        if m:
            k = (z.parent.name, int(m.group(1)), int(m.group(2)))
            if m.group(3) > latest.get(k, ("", None))[0]:
                latest[k] = (m.group(3), z)
    for (code, y, q), (rc, z) in sorted(latest.items()):
        try:
            vals, why = parse_doc_q(z)
        except Exception as e:  # noqa: BLE001
            st["파싱 오류"] += 1; print(code, y, q, e); continue
        st[why] += 1
        for fld, (v3, vc, u, lab) in vals.items():
            rows.append((code, y, q, fld, v3, vc, u, lab, rc, now))
    conn.executemany("""INSERT INTO ofs_doc_values_q VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (stock_code, fiscal_year, quarter, field) DO UPDATE SET
        value_3m=EXCLUDED.value_3m, value_cum=EXCLUDED.value_cum, unit=EXCLUDED.unit, label=EXCLUDED.label, rcept_no=EXCLUDED.rcept_no, parsed_at=EXCLUDED.parsed_at""", rows)
    conn.commit()
    print(json.dumps({"docs": len(latest), "stats": dict(st), "values": len(rows)}, ensure_ascii=False))


DDL_ALL = """CREATE TABLE IF NOT EXISTS doc_statement_values (stock_code TEXT, fiscal_year INT, quarter INT, report_type TEXT, field TEXT,
             value_3m DOUBLE PRECISION, value_cum DOUBLE PRECISION, unit TEXT, label TEXT, rcept_no TEXT, parsed_at TEXT,
             PRIMARY KEY (stock_code, fiscal_year, quarter, report_type, field))"""
SECTIONS = (("CFS", "연결재무제표"), ("OFS", "재무제표"))


def main_all(conn):
    """10-11: 사업보고서·분기보고서 본문의 연결·별도 재무제표 전부 → doc_statement_values(quarter 0 = 연간)."""
    conn.execute(DDL_ALL); conn.commit()
    docs = {}
    for z in DOC.glob("*/*.zip"):
        m = re.match(r"(\d{4})_(\d+)\.zip", z.name)
        if m:
            k = (z.parent.name, int(m.group(1)), 0)
            if m.group(2) > docs.get(k, ("", None))[0]:
                docs[k] = (m.group(2), z)
    for z in DOC_Q.glob("*/*Q*_*.zip"):
        m = re.match(r"(\d{4})Q(\d)_(\d+)\.zip", z.name)
        if m:
            k = (z.parent.name, int(m.group(1)), int(m.group(2)))
            if m.group(3) > docs.get(k, ("", None))[0]:
                docs[k] = (m.group(3), z)
    want = None
    f = sorted(OUT.glob("residual_cause_groups_field_verif_*.csv"))[-1]
    want = {(r["stock_code"], int(r["year"])) for r in csv.DictReader(open(f)) if r["group"] in ("OTHER", "DB_MISSING_OFS", "DB_MISSING_CFS", "NONDEC")}
    st, rows = collections.Counter(), []
    now = datetime.now().isoformat(timespec="seconds")
    for (code, y, q), (rc, z) in sorted(docs.items()):
        if (code, y) not in want and (code, y + 1 if q == 0 else y) not in want:
            continue
        for fs, title in SECTIONS:
            try:
                if q == 0:
                    vals, why = parse_doc(z, title)
                    vals = {k: (v, v, u, lab) for k, (v, u, lab) in vals.items()}
                else:
                    vals, why = parse_doc_q(z, title)
            except Exception as e:  # noqa: BLE001
                st["파싱 오류"] += 1; continue
            st[(fs, why)] += 1
            for fld, (v3, vc, u, lab) in vals.items():
                rows.append((code, y, q, fs, fld, v3, vc, u, lab, rc, now))
    conn.executemany("""INSERT INTO doc_statement_values VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (stock_code, fiscal_year, quarter, report_type, field) DO UPDATE SET
        value_3m=EXCLUDED.value_3m, value_cum=EXCLUDED.value_cum, unit=EXCLUDED.unit, label=EXCLUDED.label, rcept_no=EXCLUDED.rcept_no, parsed_at=EXCLUDED.parsed_at""", rows)
    conn.commit()
    print(json.dumps({"docs": len(docs), "stats": {f"{k[0]}|{k[1]}" if isinstance(k, tuple) else k: v for k, v in st.items()}, "values": len(rows)}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--codes", default=""); ap.add_argument("--quarterly", action="store_true")
    ap.add_argument("--all", action="store_true", help="10-11: 연결·별도 × 연간·분기 본문 → doc_statement_values"); a = ap.parse_args()
    conn = connect_primary_db(timeout=300)
    conn.execute(DDL); conn.commit()
    if a.all:
        return main_all(conn)
    if a.quarterly:
        return main_quarterly(conn)
    f = sorted(OUT.glob("residual_cause_groups_field_verif_*.csv"))[-1]
    targets = sorted({(r["stock_code"], int(r["year"])) for r in csv.DictReader(open(f))
                      if r["group"] == "DB_MISSING_OFS" and r["quarter"] in ("0", "4")})
    if a.codes:
        targets = [t for t in targets if t[0] in a.codes.split(",")]
    st, rows = collections.Counter(), []
    now = datetime.now().isoformat(timespec="seconds")
    for code, y in targets:
        z = sorted((DOC / code).glob(f"{y}_*.zip")) if (DOC / code).exists() else []
        if not z:
            st["본문 없음"] += 1; continue
        try:
            vals, why = parse_doc(z[-1])
        except Exception as e:  # noqa: BLE001
            st["파싱 오류"] += 1; print(code, y, e); continue
        st[why] += 1
        rc = z[-1].stem.split("_", 1)[1]
        for fld, (v, u, lab) in vals.items():
            rows.append((code, y, fld, v, u, lab, rc, now))
    conn.executemany("""INSERT INTO ofs_doc_values VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (stock_code, fiscal_year, field) DO UPDATE SET value=EXCLUDED.value,
        unit=EXCLUDED.unit, label=EXCLUDED.label, rcept_no=EXCLUDED.rcept_no, parsed_at=EXCLUDED.parsed_at""", rows)
    conn.commit()
    print(json.dumps({"targets": len(targets), "stats": dict(st), "values": len(rows), "by_field": dict(collections.Counter(r[2] for r in rows))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
