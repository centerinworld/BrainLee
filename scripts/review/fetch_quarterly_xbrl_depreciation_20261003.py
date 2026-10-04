#!/usr/bin/env python3
"""분기·반기 XBRL 주석 감가상각 구성요소 수집(읽기 전용, 재개 가능).

FINANCIAL_STATEMENTS.md §9-5 후속:
대형사 2025Q1 표본 30곳 모두 분기 XBRL에 감가상각 관련 태그가 있음을 확인했다.
이 스크립트는 자산 2조 이상 종목의 1Q·반기·3Q XBRL을 매일 이어 받아
research_outputs/financial_rereview_20261002/quarterly_xbrl_depreciation.jsonl 에
YTD 구성요소를 저장한다. 운영 `cash_flow_data.depreciation_q`는 변경하지 않는다.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

import config  # noqa: E402
import dart_keys  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from financial_rereview_20261002 import Dart  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "quarterly_xbrl_depreciation.jsonl"
RAWX = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_xbrl")
NS = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi"}
REPRT = {1: "11013", 2: "11012", 3: "11014"}
REPORT_NAME = {1: "분기보고서", 2: "반기보고서", 3: "분기보고서"}
Q_END = {1: ("03", "31"), 2: ("06", "30"), 3: ("09", "30")}
Q_DAYS = {1: 90, 2: 181, 3: 273}
PERIOD_MARK = {1: "03", 2: "06", 3: "09"}
WANT = {
    "dep_ppe": ["DepreciationPropertyPlantAndEquipment", "DepreciationExpense"],
    "amort": ["AmortisationIntangibleAssetsOtherThanGoodwill", "AmortisationExpense"],
    "dep_rou": ["DepreciationRightofuseAssets"],
    "adj_dep": ["AdjustmentsForDepreciationExpense"],
    "adj_amort": ["AdjustmentsForAmortisationExpense"],
}


class Quota(Exception):
    pass


def get_json(url: str, params: dict, key: str) -> dict | None:
    if not dart_keys.allow(key):
        raise Quota()
    for attempt in range(4):
        try:
            d = requests.get(url, params=dict(params, crtfc_key=key), timeout=40).json()
        except Exception:
            time.sleep(2 + attempt * 2)
            continue
        time.sleep(0.3)
        if d.get("status") == "020":
            raise Quota()
        return d
    return None


def get_xbrl(params: dict, key: str) -> bytes | None:
    if not dart_keys.allow(key):
        raise Quota()
    for attempt in range(4):
        try:
            content = requests.get("https://opendart.fss.or.kr/api/fnlttXbrl.xml",
                                   params=dict(params, crtfc_key=key), timeout=80).content
        except Exception:
            time.sleep(3 + attempt * 3)
            continue
        time.sleep(0.3)
        if content[:2] == b"PK":
            return content
        if b"<status>020<" in content[:200]:
            raise Quota()
        return None
    return None


def parse_ytd(xbrl_bytes: bytes, year: int, q: int) -> dict:
    root = ET.fromstring(xbrl_bytes)
    ctx = {}
    end_mm, end_dd = Q_END[q]
    for c in root.findall("xbrli:context", NS):
        p = c.find("xbrli:period", NS)
        s = p.findtext("xbrli:startDate", namespaces=NS) if p is not None else None
        e = p.findtext("xbrli:endDate", namespaces=NS) if p is not None else None
        mems = [m.text or "" for m in c.findall(".//xbrldi:explicitMember", NS)]
        if not s or not e:
            continue
        if not s.startswith(str(year)) or not e.startswith(f"{year}-{end_mm}-{end_dd}"):
            continue
        days = (datetime.fromisoformat(e) - datetime.fromisoformat(s)).days + 1
        if abs(days - Q_DAYS[q]) > 15:
            continue
        ctx[c.get("id")] = (days, mems)

    vals = {"CFS": {}, "OFS": {}}
    for el in root:
        tag = el.tag.split("}")[-1]
        if not el.text:
            continue
        meta = ctx.get(el.get("contextRef"))
        if not meta:
            continue
        _, mems = meta
        if len(mems) > 1:
            continue
        fs = "OFS" if (mems and "Separate" in mems[0]) else ("CFS" if (not mems or "Consolidated" in mems[0]) else None)
        if not fs:
            continue
        low = tag.lower()
        if "depreciation" in low or "amorti" in low:
            try:
                vals[fs].setdefault("_all", {})
                vals[fs]["_all"][tag] = abs(float(el.text))
            except ValueError:
                pass
            if "adjustment" in low and "depreciation" in low and "amorti" not in low and tag not in WANT["adj_dep"]:
                try:
                    vals[fs].setdefault("adj_dep_custom", abs(float(el.text)))
                except ValueError:
                    pass
        for k, tags in WANT.items():
            if tag in tags:
                cur = vals[fs].get(k)
                prio = tags.index(tag)
                if cur is None or prio < cur[1]:
                    try:
                        vals[fs][k] = (abs(float(el.text)), prio)
                    except ValueError:
                        pass

    out = {}
    for fs, d in vals.items():
        if not d:
            continue
        o = {k: (v[0] if isinstance(v, tuple) else v) for k, v in d.items() if k != "_all"}
        if "_all" in d:
            o["_all"] = d["_all"]
        out[fs] = o
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2023-2026")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-assets", type=float, default=2e12)
    ap.add_argument("--exit-on-quota", action="store_true")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    years = list(range(y0, y1 + 1))

    done = set()
    if OUT.exists():
        for line in OUT.open(errors="ignore"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            done.add((d.get("code"), int(d.get("year")), int(d.get("q"))))

    conn = connect_primary_db(timeout=120, readonly=True)
    codes = [r[0] for r in conn.execute("""SELECT DISTINCT ON (stock_code) stock_code
              FROM financial_data
              WHERE is_annual AND report_type='OFS' AND year=2024 AND total_assets>=?
                AND stock_code ~ '^[0-9]{6}$'
              ORDER BY stock_code""", (args.min_assets,)).fetchall()]
    conn.close()
    if args.limit:
        codes = codes[:args.limit]

    corp = Dart().corp_codes()
    keys = dart_keys.ordered_keys()
    ki = 0
    targets = [(c, y, q) for c in codes for y in years for q in (1, 2, 3) if (c, y, q) not in done and c in corp]
    print(f"대상 {len(targets)}보고서 ({len(codes)}종목, 완료 {len(done)}건)", flush=True)
    RAWX.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as fh:
        for idx, (code, year, q) in enumerate(targets, 1):
            while True:
                if ki >= len(keys):
                    print("모든 키 한도 소진", flush=True)
                    return 0 if args.exit_on_quota else 1
                key = keys[ki]
                try:
                    lst = get_json("https://opendart.fss.or.kr/api/list.json",
                                   {"corp_code": corp[code], "bgn_de": f"{year}0101", "end_de": f"{year}1231", "pblntf_ty": "A", "page_count": "100"},
                                   key)
                    break
                except Quota:
                    ki += 1
                    print("키 한도 → 다음 키", flush=True)
            wanted = REPORT_NAME[q]
            rc = None
            for it in (lst or {}).get("list", []):
                nm = it.get("report_nm", "")
                if wanted in nm and f"({year}.{PERIOD_MARK[q]}" in nm:
                    if q in (1, 3) and "반기" in nm:
                        continue
                    rc = it.get("rcept_no")
                    break
            rec = {"code": code, "year": year, "q": q, "rcept_no": rc, "vals_ytd": {}, "ok": True}
            if rc:
                while True:
                    if ki >= len(keys):
                        print("모든 키 한도 소진", flush=True)
                        return 0 if args.exit_on_quota else 1
                    try:
                        zb = get_xbrl({"rcept_no": rc, "reprt_code": REPRT[q]}, keys[ki])
                        break
                    except Quota:
                        ki += 1
                        print("키 한도 → 다음 키", flush=True)
                if zb:
                    (RAWX / f"{code}_{year}Q{q}_{rc}.zip").write_bytes(zb)
                    try:
                        z = zipfile.ZipFile(io.BytesIO(zb))
                        inst = [n for n in z.namelist() if n.endswith(".xbrl")]
                        if inst:
                            rec["vals_ytd"] = parse_ytd(z.read(inst[0]), year, q)
                    except (zipfile.BadZipFile, ET.ParseError):
                        rec["ok"] = False
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            done.add((code, year, q))
            if idx % 50 == 0:
                print(f"  {idx}/{len(targets)}", flush=True)
    print("완료", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
