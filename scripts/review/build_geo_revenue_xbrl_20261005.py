#!/usr/bin/env python3
"""국내/해외(지역별) 매출 — DART XBRL 주석(IFRS 8 '지역에 대한 정보')에서 추출(2026-10-05, 사용자 요청).

원천: data_raw/dart_xbrl/<code>_<연도|연도Qn>_<rcept_no>.zip (감가상각 XBRL 수집 때 이미 저장된 원문, 매일 밤 추가).
추출: 당기(CFY…dFY / 분기 CFY…) 기간 맥락 중 GeographicalAreasAxis 차원이 붙은 ifrs-full:Revenue(·RevenueFromContractsWithCustomers) 사실.
      연결(ConsolidatedMember)·별도(SeparateMember) 구분, 영업부문 축 등 다른 차원이 섞인 사실은 제외(부문×지역 교차표 이중 집계 방지).
국내 = ifrs-full:CountryOfDomicileMember 또는 한글 라벨에 '국내·대한민국·한국·내수'.
검증(원칙 0 — 원문 값만 믿지 않는다): 같은 맥락의 지역 미지정 매출 합계 T(또는 financial_data 연간 매출)와
  (a) 지역 값들의 부분합 중 국내+나머지가 T와 0.5% 이내(하위·상위 지역이 섞여 있어도 '국내 + 해외 소계' 또는 '국내 + 해외 하위 합' 중 하나가 맞으면 일치)
  → identity_ok. 국내만 있고 해외는 T−국내로 계산되면 domestic_only(해외 = 차감), 맞지 않으면 mismatch(화면 미표시).
결과 테이블 revenue_geography(매 실행 전체 교체, 연간 사업보고서만).
"""
import collections
import glob
import itertools
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_xbrl")
DDL = """CREATE TABLE IF NOT EXISTS revenue_geography (
    stock_code TEXT NOT NULL, fiscal_year INTEGER NOT NULL, report_type TEXT NOT NULL,
    total_krw DOUBLE PRECISION, domestic_krw DOUBLE PRECISION, overseas_krw DOUBLE PRECISION, overseas_pct DOUBLE PRECISION,
    regions_json TEXT, identity_status TEXT NOT NULL, source_file TEXT, rcept_no TEXT, built_at TEXT, run_id TEXT,
    PRIMARY KEY (stock_code, fiscal_year, report_type))"""
REV = ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers")
DOM_KO = ("국내", "대한민국", "한국", "내수", "본사 소재지")


def labels(z):
    out = {}
    for n in z.namelist():
        if n.endswith("lab-ko.xml"):
            t = z.read(n).decode("utf-8", "ignore")
            for m in re.finditer(r'xlink:label="Label_label_([^"]+?)_ko"[^>]*>([^<]*)</link:label>', t):
                out.setdefault(m.group(1).replace("_", ":", 1), m.group(2).strip())
    return out


def parse(path):
    z = zipfile.ZipFile(path)
    x = z.read([n for n in z.namelist() if n.endswith(".xbrl")][0]).decode("utf-8", "ignore")
    lab = labels(z)
    ctx = {}
    for cid, body in re.findall(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', x, re.S):
        mem = dict(re.findall(r'<xbrldi:explicitMember dimension="([^"]+)">([^<]+)</xbrldi:explicitMember>', body))
        ctx[cid] = mem
    res = collections.defaultdict(lambda: {"total": None, "regions": {}})
    for tag, cid, val in re.findall(r'<(ifrs-full:Revenue(?:FromContractsWithCustomers)?)\b[^>]*contextRef="([^"]+)"[^>]*>(-?[\d.]+)<', x):
        if not cid.startswith("CFY") or "dFY" not in cid:
            continue  # 당기 기간(duration)만
        mem = ctx.get(cid, {})
        fsm = mem.get("ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis", "ifrs-full:ConsolidatedMember")
        fs = "OFS" if "Separate" in fsm else "CFS"
        other = {k for k in mem if k not in ("ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis", "ifrs-full:GeographicalAreasAxis")}
        if other:
            continue
        v = float(val)
        geo = mem.get("ifrs-full:GeographicalAreasAxis")
        if geo is None:
            if res[fs]["total"] is None or tag == "ifrs-full:Revenue":
                res[fs]["total"] = v
        else:
            res[fs]["regions"].setdefault(geo, v)
    return res, lab


def classify(total, regions, lab):
    def name(m):
        return lab.get(m) or re.sub(r"Member.*$", "", m.split(":")[-1])
    dom = [m for m in regions if m.endswith("CountryOfDomicileMember") or any(k in name(m) for k in DOM_KO)]
    if not dom:
        return None
    d = regions[dom[0]]
    others = {m: v for m, v in regions.items() if m != dom[0]}
    tol = lambda a, b: b and abs(a - b) <= abs(b) * 0.005
    if total:
        # 해외 쪽 부분집합 중 국내와 더해 합계가 되는 조합(소계·하위 혼재 대비, 최대 8개까지 탐색)
        items = list(others.items())[:12]
        for r in range(len(items), 0, -1):
            for comb in itertools.combinations(items, r):
                if tol(d + sum(v for _, v in comb), total):
                    return d, total - d, "identity_ok", {name(m): v for m, v in [(dom[0], d)] + list(comb)}
        if 0 <= d <= total:
            return d, total - d, ("domestic_only" if not others else "mismatch"), {name(m): v for m, v in regions.items()}
        return d, None, "mismatch", {name(m): v for m, v in regions.items()}
    return d, None, "no_total", {name(m): v for m, v in regions.items()}


def main():
    files = sorted(glob.glob(str(RAW / "*.zip")))
    latest = {}
    for f in files:
        m = re.match(r"(\d{6})_(\d{4})_(\d+)\.zip$", Path(f).name)  # 연간(사업보고서)만
        if m:
            k = (m.group(1), int(m.group(2)))
            if k not in latest or m.group(3) > latest[k][1]:
                latest[k] = (f, m.group(3))
    conn = connect_primary_db(timeout=900)
    fd = {(r[0], r[1], r[2]): r[3] for r in map(tuple, conn.execute(
        "SELECT stock_code, year, report_type, revenue FROM financial_data WHERE is_annual AND revenue IS NOT NULL").fetchall())}
    st, rows = collections.Counter(), []
    run_id = f"geo_rev_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for (code, y), (f, rc) in sorted(latest.items()):
        try:
            res, lab = parse(f)
        except Exception:
            st["파싱 실패"] += 1
            continue
        found = False
        for fs, r in res.items():
            if not r["regions"]:
                continue
            total = r["total"] or fd.get((code, y, fs))
            c = classify(total, r["regions"], lab)
            if c is None:
                st["지역 주석 있으나 국내 구분 없음"] += 1
                continue
            d, o, status, reg = c
            found = True
            st[status] += 1
            rows.append((code, y, fs, total, d, o, (o / total * 100) if (o is not None and total) else None,
                         json.dumps(reg, ensure_ascii=False), status, Path(f).name, rc, now, run_id))
        if not found:
            st["지역 매출 주석 없음"] += 1
    conn.execute(DDL)
    conn.execute("DELETE FROM revenue_geography")
    conn.executemany("""INSERT INTO revenue_geography(stock_code,fiscal_year,report_type,total_krw,domestic_krw,overseas_krw,overseas_pct,
                        regions_json,identity_status,source_file,rcept_no,built_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    n_codes = len({r[0] for r in rows if r[8] in ("identity_ok", "domestic_only")})
    print(run_id, f"연간 XBRL {len(latest):,}건", json.dumps(dict(st), ensure_ascii=False), f"표시 가능 종목 {n_codes}")


if __name__ == "__main__":
    main()
