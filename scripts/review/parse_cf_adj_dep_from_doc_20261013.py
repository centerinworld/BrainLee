#!/usr/bin/env python3
"""사업보고서 본문 주석의 '현금흐름 조정 항목' 표에서 감가상각비(연간)를 읽는다 (2026-10-13, C14 ①). 기본 dry-run(측정·CSV).

배경: fnlttSinglAcntAll 현금흐름표에 '감가상각비' 행이 없는 회사가 많아(2021~22 연간 칸의 약 3분의 2) 정의(= 현금흐름표 조정 '감가상각비' 행) 값을 원문으로 검증할 수 없었다.
      조정 항목은 주석의 '영업에서 창출된 현금흐름' 표(조정 항목 나열)에 있다. 원천: data_raw/dart_doc/<code>/<FY>_<rcept>.zip (2021~2025).
표 식별: '감가상각비' 행이 있고 법인세비용·이자/금융 행이 함께 있는 조정 항목 표(현금흐름표 본체 또는 주석), 손익 표·판관비 명세는 배제.
  (10-13 대조 시험: 조정 특징 행만으로 식별했더니 판관비 명세가 있는 손익 표를 잡아 확정 값의 39%가 fnltt 원문과 달랐다 → 배제 조건 추가. 정확도는 fnltt 현금흐름표 행이 있는 칸으로 대조한다.)
감가상각 값 = 표 안에서 이름에 '감가상각'이 들어가는 행들의 합(무형·대손·상각후원가 제외) — FnGuide 2023~24 대조 78.2%(한 행은 후보 있는 칸 기준 57.7%)로 정한 정의. 본문 표 값은 76.5%(861칸) — verify_depreciation_definition_20261013.py 참조.
연결/별도: 한 보고서에 연결 주석과 별도 주석이 순서대로 있다 — 표가 둘 이상이면 첫 번째=연결, 마지막=별도, 하나뿐이면 연결 회사는 연결, 별도 전용 회사는 별도.
확정 조건: 같은 회사의 다음 해 보고서 전기 열 값과 일치(두 보고서 일치) — 일치하지 않으면 후보로만 둔다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import html
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
sys.path.insert(0, str(ROOT / "collectors"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402
from dart_product_mix_collector import parse_html_table_grid  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
UNIT = [("십억원", 1e9), ("억원", 1e8), ("백만원", 1e6), ("천원", 1e3), ("원", 1.0)]
MARK = re.compile(r"이자수익|이자비용|금융수익|금융원가|법인세비용|퇴직급여|대손상각비|외화환산|처분손실|처분이익|지분법|충당부채전입|재고자산평가")
LABEL = {"감가상각비", "유형자산감가상각비"}


def num(s):
    s = (s or "").replace(",", "").replace(" ", "").replace("　", "")
    if s in ("", "-", "−", "–"):
        return None
    neg = s.startswith(("△", "(", "-", "▲"))
    m = re.search(r"\d+(?:\.\d+)?", s)
    return (-1 if neg else 1) * float(m.group()) if m else None


def unit_of(text):
    for seg in reversed(re.findall(r"단위\s*[:：]?\s*([^)\],]*)", text)):
        for k, v in UNIT:
            if k in seg.replace(" ", ""):
                return v
    return None


def load_main(path):
    z = zipfile.ZipFile(path)
    return z.read(max(z.namelist(), key=lambda n: z.getinfo(n).file_size)).decode("utf-8", "ignore")


def dep_rows(text):
    """조정 항목 표에서 (당기, 전기) 감가상각비 후보 리스트(문서 순서). 단위는 원으로 환산."""
    out = []
    for m in re.finditer(r"<TABLE.*?</TABLE>", text, re.S | re.I):
        tb = m.group()
        if "감가상각비" not in tb:
            continue
        grid = [r for r in parse_html_table_grid(tb) if r]
        labs = [re.sub(r"\s", "", r[0]) for r in grid if r]
        # 조정 항목 표(현금흐름표 본체 또는 주석 '영업에서 창출된 현금흐름'): 법인세비용 + 이자(수익·비용·금융) 행이 함께 있고,
        # 손익 표(매출액·매출원가)와 판관비 명세(급여·복리후생비)는 배제 — 판관비 명세에도 퇴직급여·감가상각비가 있어 10-13 시험에서 오탐
        has_tax = any(l.startswith("법인세비용") or l.startswith("법인세수익") for l in labs)
        has_int = any(re.search(r"이자(수익|비용)|금융(수익|원가|비용)", l) for l in labs)
        income_like = any(l in ("매출액", "수익(매출액)", "영업수익", "매출원가", "급여", "복리후생비", "판매비와관리비") for l in labs)
        if not (has_tax and has_int) or income_like:
            continue
        before = text[max(0, m.start() - 1500): m.start()]
        u = unit_of(re.sub(r"<[^>]+>", " ", before) + " " + " ".join(" ".join(r) for r in grid[:1]))
        if u is None:
            continue
        cur = prior = None
        for r in grid:
            lab = re.sub(r"\s", "", r[0])
            if "감가상각" in lab and not re.search(r"무형|대손|상각후원가", lab):
                vals = [v for v in (num(c) for c in r[1:]) if v is not None]
                if vals:
                    cur = (cur or 0) + abs(vals[0]) * u
                    if len(vals) > 1:
                        prior = (prior or 0) + abs(vals[1]) * u
        if cur is not None:
            out.append((cur, prior))
    return out


def doc_for(code, fy):
    zs = sorted(glob.glob(f"{RAW}/{code}/{fy}_*.zip"))
    return zs[-1] if zs else None


def pick(rows, fs, report_type):
    """연결 = 첫 표, 별도 = 마지막 표. 연결 회사의 별도(OFS) 행인데 표가 하나뿐이면 그 표는 연결 주석일 수 있어 건너뛴다."""
    if not rows:
        return None
    if report_type == "OFS" and fs == "OFS" and len(rows) < 2:
        return None
    return rows[0] if fs == "CFS" else rows[-1]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--years", default="2021-2022")
    ap.add_argument("--emit-reviewed", action="store_true", help="두 보고서 일치 + DB와 다른 칸을 apply_reviewed_cells 입력 CSV로")
    a = ap.parse_args()
    y0, y1 = map(int, a.years.split("-"))
    conn = connect_primary_db(timeout=300, readonly=True)
    cells = conn.execute("SELECT stock_code, year, report_type, depreciation FROM cash_flow_data WHERE is_annual AND quarter IN (0,4) AND depreciation IS NOT NULL AND year BETWEEN ? AND ? GROUP BY 1,2,3,4", (y0, y1)).fetchall()
    cache, st, out = {}, collections.Counter(), []
    n = 0
    for code, y, fs, v in cells:
        rfs = fn.effective_fs(code, y, fs)
        if fn._extract(code, y, 0, rfs, "thstrm_amount").get("depreciation") is not None or fn._extract(code, y + 1, 0, rfs, "frmtrm_amount").get("depreciation") is not None:
            continue
        n += 1
        if a.limit and n > a.limit:
            break
        def rows_of(fy):
            if (code, fy) not in cache:
                p = doc_for(code, fy)
                cache[(code, fy)] = dep_rows(load_main(p)) if p else None
            return cache[(code, fy)]
        cur = rows_of(y)
        if cur is None:
            st["본문 없음"] += 1; continue
        c = pick(cur, rfs, fs)
        if not c:
            st["조정 표에서 감가상각비 행 없음"] += 1; continue
        nxt = rows_of(y + 1)
        nc = pick(nxt, rfs, fs) if nxt else None
        confirm = nc is not None and nc[1] is not None and abs(nc[1] - c[0]) <= max(2e6, abs(c[0]) * 1e-3)
        agree_db = abs(c[0] - float(v)) <= 2e6
        st[("두 보고서 일치" if confirm else "다음 해 보고서 없음·불일치") + (" / DB와 일치" if agree_db else " / DB와 다름")] += 1
        out.append([code, y, fs, float(v), c[0], c[1], nc[1] if nc else None, confirm, agree_db])
    if a.emit_reviewed:
        with open(OUT / "reviewed_cells_dep_from_doc_20261013.csv", "w", newline="") as fh:
            w = csv.writer(fh); w.writerow("table stock_code year quarter report_type field new_value rule reason_code evidence".split())
            for code, y, fs, dbv, cur, pri, nprior, conf, agree in out:
                if conf and not agree:
                    w.writerow(["cash_flow_data", code, y, 0, fs, "depreciation", f"{nprior:.0f}",
                                "연간 감가상각 = 사업보고서 주석 '현금흐름 조정' 표의 감가상각비(두 보고서 일치, FINANCIAL §2 정의, OpenDART 단일 경로)", "", f"DB {dbv:,.0f} → 본문 {cur:,.0f}, 다음 해 보고서 전기 {nprior:,.0f}"])
    with open(OUT / "cf_adj_dep_from_doc_20261013.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow("stock_code year report_type db_value doc_current doc_prior next_report_prior confirmed agrees_db".split()); w.writerows(out)
    print(f"대상 {n}칸")
    for k, v in sorted(st.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
