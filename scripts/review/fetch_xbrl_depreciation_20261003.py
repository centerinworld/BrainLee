#!/usr/bin/env python3
"""DART 사업보고서 XBRL 원본(주석 포함)에서 감가상각·상각비를 모은다 — 읽기 전용, jsonl 출력, 재개 가능(2026-10-03).

배경: DART fnlttSinglAcntAll 현금흐름표에는 감가상각 행이 있는 회사가 ~20%뿐(나머지는 주석)이라 감가상각을 검증할 수 없었다.
XBRL 인스턴스에는 주석 표준 항목이 있다(삼성전자 FY2024 연결: DepreciationPropertyPlantAndEquipment 39.65조 —
DB quarter=4 연간 값과 정확히 일치, FnGuide quarter=0 값 51.85조는 근거 없음).
수집 항목(당기 1년, 연결=ConsolidatedMember 또는 차원 없음, 별도=SeparateMember):
  dep_ppe  = DepreciationPropertyPlantAndEquipment (없으면 DepreciationExpense)
  amort    = AmortisationIntangibleAssetsOtherThanGoodwill (없으면 AmortisationExpense)
  dep_rou  = DepreciationRightofuseAssets
  adj_dep / adj_amort = 현금흐름 조정 AdjustmentsForDepreciationExpense / AdjustmentsForAmortisationExpense
출력: research_outputs/financial_rereview_20261002/xbrl_depreciation.jsonl  (종목·회계연도당 1줄)
키: DART_API_KEY2(운영 공시 전용) 제외, 한도 소진 시 다음 날 00:20까지 대기 후 재개.
"""
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "xbrl_depreciation.jsonl"
YEARS = range(2021, 2026)
NS = {"xbrli": "http://www.xbrl.org/2003/instance", "xbrldi": "http://xbrl.org/2006/xbrldi"}
WANT = {
    "dep_ppe": ["DepreciationPropertyPlantAndEquipment", "DepreciationExpense"],
    "amort": ["AmortisationIntangibleAssetsOtherThanGoodwill", "AmortisationExpense"],
    "dep_rou": ["DepreciationRightofuseAssets"],
    "adj_dep": ["AdjustmentsForDepreciationExpense"],
    "adj_amort": ["AdjustmentsForAmortisationExpense"],
}
KEYS = [k for k in config.DART_API_KEYS if k != getattr(config, "DART_API_KEY2", None)]


class Quota(Exception):
    pass


def get(url, params, key, binary=False):
    for attempt in range(4):
        try:
            r = requests.get(url, params=dict(params, crtfc_key=key), timeout=60)
        except Exception:
            time.sleep(3 + attempt * 3)
            continue
        time.sleep(0.3)
        if binary:
            if r.content[:2] == b"PK":
                return r.content
            try:
                d = r.json()
            except Exception:
                return None
            if d.get("status") == "020":
                raise Quota()
            return None
        d = r.json()
        if d.get("status") == "020":
            raise Quota()
        return d
    return None


def parse(xbrl_bytes, year):
    root = ET.fromstring(xbrl_bytes)
    ctx = {}
    for c in root.findall("xbrli:context", NS):
        p = c.find("xbrli:period", NS)
        mems = [m.text or "" for m in c.findall(".//xbrldi:explicitMember", NS)]
        ctx[c.get("id")] = (p.findtext("xbrli:startDate", namespaces=NS), p.findtext("xbrli:endDate", namespaces=NS), mems)
    vals = {"CFS": {}, "OFS": {}}
    for el in root:
        tag = el.tag.split("}")[-1]
        if not el.text:
            continue
        s, e, mems = ctx.get(el.get("contextRef"), (None, None, []))
        if not s or not e or not s.startswith(str(year)) or not e.startswith(str(year)):
            continue
        if (datetime.fromisoformat(e) - datetime.fromisoformat(s)).days < 330:
            continue
        if len(mems) > 1:
            continue
        fs = "OFS" if (mems and "Separate" in mems[0]) else ("CFS" if (not mems or "Consolidated" in mems[0]) else None)
        if not fs:
            continue
        for k, tags in WANT.items():
            if tag in tags:
                prio = tags.index(tag)
                cur = vals[fs].get(k)
                if cur is None or prio < cur[1]:
                    try:
                        vals[fs][k] = (abs(float(el.text)), prio)
                    except ValueError:
                        pass
    return {fs: {k: v[0] for k, v in d.items()} for fs, d in vals.items() if d}


def main():
    conn = connect_primary_db(timeout=120, readonly=True)
    codes = [r[0] for r in conn.execute("""SELECT DISTINCT stock_code FROM cash_flow_data WHERE year>=2021
                                           AND stock_code ~ '^[0-9]{5}[0-9A-Z]$' ORDER BY 1""").fetchall()]
    conn.close()
    done = set()
    if OUT.exists():
        for line in open(OUT):
            d = json.loads(line)
            done.add((d["code"], d["year"]))
    ki = 0
    key = KEYS[ki]
    zb = None
    while zb is None:
        for k in KEYS:
            c = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": k}, timeout=60).content
            if c[:2] == b"PK":
                zb = c
                break
        if zb is None:
            nxt = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=20, second=0)
            print(f"회사코드: 모든 키 한도 소진 → {nxt} 까지 대기", flush=True)
            time.sleep(max(60, (nxt - datetime.now()).total_seconds()))
    z = zipfile.ZipFile(io.BytesIO(zb))
    root = ET.fromstring(z.read(z.namelist()[0]))
    corp = {e.findtext("stock_code").strip(): e.findtext("corp_code") for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}
    todo = [c for c in codes if c in corp and any((c, y) not in done for y in YEARS)]
    print(f"대상 {len(todo)}종목 (완료 {len(done)}건)", flush=True)
    with open(OUT, "a") as fh:
        i = 0
        while i < len(todo):
            code = todo[i]
            try:
                lst = get("https://opendart.fss.or.kr/api/list.json",
                          {"corp_code": corp[code], "bgn_de": "20220101", "end_de": "20261231", "pblntf_ty": "A", "page_count": "100"}, key)
                reports = {}
                for it in (lst or {}).get("list", []):
                    nm = it.get("report_nm", "")
                    if "사업보고서" in nm and "반기" not in nm and "분기" not in nm:
                        for y in YEARS:
                            if f"({y}." in nm and y not in reports:
                                reports[y] = it["rcept_no"]   # 최신 접수(정정 포함)가 먼저 온다
                for y in YEARS:
                    if (code, y) in done:
                        continue
                    rec = {"code": code, "year": y, "rcept_no": reports.get(y), "vals": {}}
                    if reports.get(y):
                        zb = get("https://opendart.fss.or.kr/api/fnlttXbrl.xml", {"rcept_no": reports[y], "reprt_code": "11011"}, key, binary=True)
                        if zb:
                            zz = zipfile.ZipFile(io.BytesIO(zb))
                            inst = [n for n in zz.namelist() if n.endswith(".xbrl")]
                            if inst:
                                try:
                                    rec["vals"] = parse(zz.read(inst[0]), y)
                                except ET.ParseError:
                                    rec["error"] = "parse"
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fh.flush()
                    done.add((code, y))
                i += 1
                if i % 100 == 0:
                    print(f"  {i}/{len(todo)}", flush=True)
            except Quota:
                ki += 1
                if ki < len(KEYS):
                    key = KEYS[ki]
                    print("키 한도 → 다음 키", flush=True)
                    continue
                nxt = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=20, second=0)
                print(f"모든 키 한도 소진 → {nxt} 까지 대기", flush=True)
                time.sleep(max(60, (nxt - datetime.now()).total_seconds()))
                ki = 0
                key = KEYS[ki]
    print("완료", flush=True)


if __name__ == "__main__":
    main()
