#!/usr/bin/env python3
"""DART 사업보고서 본문의 별도(OFS) 연간 재무표 후보 추출 dry-run.

목적: P1 고정분모 잔여 `DB_MISSING_OFS` 중 DART 재무 API가 별도 값을 주지 않는
칸을, 이미 받은 `data_raw/dart_doc/<code>/<year>_<rcept>.zip` 본문에서 얼마나
복구할 수 있는지 산정한다.

주의:
- 이 스크립트는 DB를 읽거나 쓰지 않는다. residual cause CSV와 본문 zip만 읽고
  후보 파일만 만든다.
- 후보는 DART 원문 후보일 뿐이며, 적용은 FnGuide 일치 확인과 readback/정본 동기화
  절차가 붙은 별도 스크립트에서만 한다.
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import zipfile
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "collectors"))
from dart_product_mix_collector import parse_html_table_grid  # noqa: E402

RAW_DOC = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
DEFAULT_RESIDUAL = OUT / "residual_cause_groups_field_verif_20261010_101708.csv"

FIELD_PAT = {
    "revenue": [r"^매출액$", r"^수익\(매출액\)$", r"^영업수익$", r"^수익$"],
    "operating_profit": [r"^영업이익", r"^영업손실"],
    "net_income": [r"^당기순이익", r"^당기순손실"],
    "total_assets": [r"^자산총계$"],
    "total_liabilities": [r"^부채총계$"],
    "total_equity": [r"^자본총계$", r"^자본 합계$"],
    "operating_cf": [r"^영업활동현금흐름", r"^영업활동.*순현금"],
    "investing_cf": [r"^투자활동현금흐름", r"^투자활동.*순현금"],
    "financing_cf": [r"^재무활동현금흐름", r"^재무활동.*순현금"],
}


def _num(s: str | None) -> float | None:
    s = html.unescape(s or "").replace(",", "").replace(" ", "").replace("\xa0", "")
    if s in ("", "-", "−", "–"):
        return None
    neg = s.startswith(("△", "▲", "-", "("))
    m = re.search(r"\d+(?:\.\d+)?", s)
    return (-1 if neg else 1) * float(m.group()) if m else None


def _load_doc(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        name = max(zf.namelist(), key=lambda n: zf.getinfo(n).file_size)
        return zf.read(name).decode("utf-8", "ignore")


def _section_4(x: str) -> str:
    m = re.search(r"<TITLE[^>]*>\s*4\.\s*재무제표\s*</TITLE>", x)
    if not m:
        return ""
    rest = x[m.end():]
    n = re.search(r"<TITLE[^>]*>\s*5\.\s*재무제표 주석\s*</TITLE>", rest)
    return rest[: n.start()] if n else rest[:400000]


def _period_map(tables: list[list[list[str]]]) -> dict[str, int]:
    """`제 15 기 2022.01.01 ...` 또는 `제 15 기 2022.12.31 현재`에서 기수→연도."""
    out: dict[str, int] = {}
    for grid in tables[:8]:
        for row in grid[:12]:
            line = " ".join(row)
            m = re.search(r"제\s*(\d+)\s*기.*?(20\d{2})", line)
            if m:
                out[m.group(1)] = int(m.group(2))
    return out


def _unit_multiplier(sec: str) -> float:
    text = html.unescape(re.sub(r"<[^>]+>", " ", sec[:6000]))
    for k, v in (("십억원", 1e9), ("억원", 1e8), ("백만원", 1e6), ("천원", 1e3), ("원", 1.0)):
        if k in text:
            return v
    return 1.0


def _header_years(grid: list[list[str]], pmap: dict[str, int]) -> dict[int, int]:
    col_year: dict[int, int] = {}
    for j in range(max(len(r) for r in grid)):
        header = " ".join((grid[i][j] if j < len(grid[i]) else "") for i in range(min(3, len(grid))))
        m = re.search(r"제\s*(\d+)\s*기", header)
        if m and m.group(1) in pmap:
            col_year[j] = pmap[m.group(1)]
        else:
            y = re.search(r"20\d{2}", header)
            if y:
                col_year[j] = int(y.group())
    return col_year


def _field_from_label(label: str) -> str | None:
    label = re.sub(r"\s+", "", label)
    for field, patterns in FIELD_PAT.items():
        if any(re.search(p, label) for p in patterns):
            return field
    return None


def extract_doc(path: Path, wanted_year: int, wanted_field: str) -> tuple[float | None, str | None]:
    x = _load_doc(path)
    sec = _section_4(x)
    if not sec:
        return None, "no_section_4"
    mult = _unit_multiplier(sec)
    tables = [parse_html_table_grid(m.group()) for m in re.finditer(r"<TABLE.*?</TABLE>", sec, re.S)]
    pmap = _period_map(tables)
    if not pmap:
        return None, "no_period_map"
    for grid in tables:
        if not grid or max(len(r) for r in grid) < 2:
            continue
        col_year = _header_years(grid, pmap)
        if wanted_year not in col_year.values():
            continue
        for row in grid:
            label = row[0] if row else ""
            field = _field_from_label(label)
            if field != wanted_field:
                continue
            for j, y in col_year.items():
                if y == wanted_year and j < len(row):
                    v = _num(row[j])
                    if v is not None:
                        return v * mult, "section4_table"
    return None, "not_found"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare-db", action="store_true", help="financial_field_verification을 읽기 전용으로 조회해 FnGuide 일치 여부를 붙인다.")
    args = ap.parse_args()

    residual = DEFAULT_RESIDUAL
    rows = []
    counts = Counter()
    for r in csv.DictReader(residual.open()):
        if r["group"] != "DB_MISSING_OFS" or r["quarter"] != "0":
            continue
        code, year, field = r["stock_code"], int(r["year"]), r["field"]
        docs = sorted((RAW_DOC / code).glob(f"{year}_*.zip")) if (RAW_DOC / code).exists() else []
        if not docs:
            counts["no_doc"] += 1
            continue
        val, reason = extract_doc(docs[-1], year, field)
        if val is None:
            counts[f"miss:{reason}"] += 1
            continue
        counts["candidate"] += 1
        rows.append({**r, "candidate": val, "doc": docs[-1].name, "parse_reason": reason})

    if args.compare_db and rows:
        sys.path.insert(0, str(ROOT))
        from db_compat import connect_primary_db  # noqa: WPS433

        conn = connect_primary_db(timeout=300, readonly=True)
        fgv = {}
        for r in rows:
            key = (r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"])
            fgv[key] = None
        for key in list(fgv):
            row = conn.execute("""SELECT fnguide_value, status FROM financial_field_verification
                                  WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=?""", key).fetchone()
            fgv[key] = tuple(row) if row else (None, None)
        conn.close()
        match = 0
        for r in rows:
            key = (r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"])
            fg, status = fgv[key]
            r["fnguide_value"] = fg
            r["verification_status"] = status
            r["fg_abs_diff"] = abs(float(r["candidate"]) - float(fg)) if fg is not None else None
            r["fg_match_2m"] = bool(r["fg_abs_diff"] is not None and r["fg_abs_diff"] <= 2_000_000)
            match += int(r["fg_match_2m"])
        counts["fnguide_match_2m"] = match
        counts["fnguide_no_value"] = sum(1 for r in rows if r.get("fnguide_value") is None)

    run_id = datetime.now().strftime("ofs_doc_candidates_%Y%m%d_%H%M%S")
    out_json = OUT / f"{run_id}.json"
    out_csv = OUT / f"{run_id}.csv"
    out_json.write_text(json.dumps({"run_id": run_id, "counts": counts, "rows": rows[:500]}, ensure_ascii=False, indent=1))
    with out_csv.open("w", newline="") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(json.dumps({"run_id": run_id, "counts": counts, "csv": str(out_csv)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
