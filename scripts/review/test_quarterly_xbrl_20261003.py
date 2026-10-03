#!/usr/bin/env python3
"""분기 주석 XBRL 시험(2026-10-03, FINANCIAL_STATEMENTS.md §9-5) — 자산 2조↑ 대형사 30곳의 2025년 1분기 보고서 XBRL에
감가상각 주석(유형·사용권·조정)이 들어 있는지 확인한다. 한 번만 실행(결과 파일이 있으면 건너뜀). 원문 zip 저장.
결과: research_outputs/financial_rereview_20261002/quarterly_xbrl_test.json
"""
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import requests  # noqa: E402
from xml.etree import ElementTree as ET  # noqa: E402
import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from financial_rereview_20261002 import Dart  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "quarterly_xbrl_test.json"
RAWX = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_xbrl")
KEY = next(k for k in config.DART_API_KEYS if k != getattr(config, "DART_API_KEY2", None))


def main():
    if OUT.exists():
        print("이미 시험함", OUT)
        return
    conn = connect_primary_db(readonly=True)
    codes = [r[0] for r in conn.execute("""SELECT DISTINCT ON (stock_code) stock_code FROM financial_data WHERE is_annual AND report_type='OFS'
              AND year=2024 AND total_assets>=2e12 AND stock_code ~ '^[0-9]{6}$' ORDER BY stock_code""").fetchall()][:30]
    conn.close()
    corp = Dart().corp_codes()
    res = {}
    for code in codes:
        lst = requests.get("https://opendart.fss.or.kr/api/list.json", params={"crtfc_key": KEY, "corp_code": corp.get(code),
                           "bgn_de": "20250401", "end_de": "20250630", "pblntf_ty": "A"}, timeout=30).json()
        if lst.get("status") == "020":
            print("한도 소진 — 내일 재시도")
            return
        rc = next((x["rcept_no"] for x in lst.get("list", []) if "분기보고서" in x.get("report_nm", "")), None)
        if not rc:
            res[code] = {"status": "1분기보고서 없음"}
            continue
        zb = requests.get("https://opendart.fss.or.kr/api/fnlttXbrl.xml", params={"crtfc_key": KEY, "rcept_no": rc, "reprt_code": "11013"}, timeout=60).content
        if zb[:2] != b"PK":
            res[code] = {"status": "XBRL 없음", "rcept_no": rc}
            continue
        RAWX.mkdir(parents=True, exist_ok=True)
        (RAWX / f"{code}_2025Q1_{rc}.zip").write_bytes(zb)
        z = zipfile.ZipFile(io.BytesIO(zb))
        inst = [n for n in z.namelist() if n.endswith(".xbrl")]
        tags = set()
        if inst:
            for el in ET.fromstring(z.read(inst[0])):
                t = el.tag.split("}")[-1]
                if "depreciation" in t.lower() or "amorti" in t.lower():
                    tags.add(t)
        res[code] = {"status": "ok", "rcept_no": rc, "files": len(z.namelist()), "dep_tags": sorted(tags)}
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    ok = [v for v in res.values() if v.get("dep_tags")]
    print(f"시험 {len(res)}곳 — 감가상각 태그 있음 {len(ok)}곳")


if __name__ == "__main__":
    main()
