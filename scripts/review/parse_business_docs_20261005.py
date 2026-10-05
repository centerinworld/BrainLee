#!/usr/bin/env python3
"""사업보고서 본문 항목 파싱(2021~, 2026-10-05). 원천: data_raw/dart_doc/<code>/<FY>_<rcept>.zip (fetch_dart_business_docs_20261005.py).

추출(회계연도별 최신 접수본, 당기 열만 저장 — 전기 열은 전년 보고서 당기 값과 대조용):
  biz_rd_expense            연구개발비 합계·매출액 대비 비율   검증: 연구개발비 ÷ 매출(financial_data 연간) ≈ 공시 비율(±0.3%p 또는 10%)
  biz_capacity              생산능력·생산실적·가동률(품목별)   검증: 생산실적 ÷ 생산능력 ≈ 공시 가동률(같은 품목이 양쪽 표에 있을 때)
  biz_raw_material_price    주요 원재료 가격(품목별, 단위)    검증: 이 보고서의 전기 값 = 전년 보고서 당기 값
  biz_sales_domestic_export 내수/수출 매출(매출실적 표 또는 '지역별 매출' 표)  검증: 내수+수출 = 합계(0.5%), 합계 ≈ 연간 매출(연결 또는 별도, 1%)
  company_product_mix       2021~2022 제품별 매출(기존 2023+ 수집기 extract_product_mix 재사용, 기존 연도 행은 건드리지 않음)
검증 실패 행도 저장하되 check_status 로 구분 — 화면은 ok 만 쓴다(fail-closed, 원칙 0).
"""
import collections
import glob
import html
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "collectors"))
from db_compat import connect_primary_db  # noqa: E402
from dart_product_mix_collector import extract_product_mix, parse_html_table_grid  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")
UNIT = [("십억원", 1e9), ("억원", 1e8), ("백만원", 1e6), ("천원", 1e3), ("원", 1.0)]
DDL = [
    """CREATE TABLE IF NOT EXISTS biz_rd_expense (stock_code TEXT, fiscal_year INTEGER, rd_total_krw DOUBLE PRECISION, rd_ratio_pct DOUBLE PRECISION,
       computed_ratio_pct DOUBLE PRECISION, check_status TEXT, rcept_no TEXT, run_id TEXT, PRIMARY KEY (stock_code, fiscal_year))""",
    """CREATE TABLE IF NOT EXISTS biz_capacity (stock_code TEXT, fiscal_year INTEGER, item TEXT, capacity DOUBLE PRECISION, production DOUBLE PRECISION,
       utilization_pct DOUBLE PRECISION, unit TEXT, check_status TEXT, rcept_no TEXT, run_id TEXT, PRIMARY KEY (stock_code, fiscal_year, item))""",
    """CREATE TABLE IF NOT EXISTS biz_raw_material_price (stock_code TEXT, fiscal_year INTEGER, item TEXT, price DOUBLE PRECISION, prior_price DOUBLE PRECISION,
       unit TEXT, check_status TEXT, rcept_no TEXT, run_id TEXT, PRIMARY KEY (stock_code, fiscal_year, item))""",
    """CREATE TABLE IF NOT EXISTS biz_cost_nature (stock_code TEXT, fiscal_year INTEGER, report_type TEXT, category TEXT, amount_krw DOUBLE PRECISION,
       items_json TEXT, total_krw DOUBLE PRECISION, is_cost_krw DOUBLE PRECISION, check_status TEXT, rcept_no TEXT, run_id TEXT,
       PRIMARY KEY (stock_code, fiscal_year, report_type, category))""",
    """CREATE TABLE IF NOT EXISTS biz_sales_domestic_export (stock_code TEXT, fiscal_year INTEGER, source TEXT, domestic_krw DOUBLE PRECISION,
       export_krw DOUBLE PRECISION, total_krw DOUBLE PRECISION, export_pct DOUBLE PRECISION, matched_basis TEXT, check_status TEXT, rcept_no TEXT, run_id TEXT,
       PRIMARY KEY (stock_code, fiscal_year, source))""",
]


def num(s):
    s = (s or "").replace(",", "").replace(" ", "").replace("　", "")
    if s in ("", "-", "−", "–"):
        return None
    neg = s.startswith(("△", "(", "-", "▲")) and not s.startswith("(정")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return (-1 if neg else 1) * float(m.group()) if m else None


def unit_of(text):
    m = re.findall(r"단위\s*[:：]?\s*([^)\],]*)", text)
    for seg in reversed(m):
        for k, v in UNIT:
            if k in seg:
                return v, k
    return None, None


def section(x, title_kw, next_kw=None):
    m = re.search(r"<TITLE[^>]*>\s*[\dIVX.\s]*" + title_kw, x)
    if not m:
        return ""
    rest = x[m.end():]
    n = re.search(r"<TITLE[^>]*>\s*(?:\d+\.|[IVX]+\.)", rest)
    return rest[: n.start()] if n else rest[:400000]


def tables_with_unit(sec):
    out, pos = [], 0
    for m in re.finditer(r"<TABLE.*?</TABLE>", sec, re.S):
        grid = parse_html_table_grid(m.group())
        before = sec[max(0, m.start() - 1500): m.start()]
        u = unit_of(re.sub(r"<[^>]+>", " ", before + " ".join(" ".join(r) for r in grid[:1])))
        out.append((grid, u, re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", before[-400:])))))
    return out


def cur_col(grid):
    """헤더에서 당기 열 위치: '제NN기'/연도 중 첫 번째."""
    for r in grid[:3]:
        for j, c in enumerate(r):
            if re.search(r"제\s*\d+\s*기|20\d\d", c or ""):
                return j
    return None


def first_num(row, start):
    """당기 열(start) 칸만 읽는다. 2026-10-05: 예전엔 당기 칸이 '-'이면 다음(전기) 칸 값을 집어 와 전년 값이 당기로 저장됐다(172670 2023 원재료 가격)."""
    if start is None or start >= len(row):
        return None, None
    c = row[start]
    return num(c), c


def load(path):
    z = zipfile.ZipFile(path)
    main = max(z.namelist(), key=lambda n: z.getinfo(n).file_size)
    return z.read(main).decode("utf-8", "ignore")


def parse_rd(x):
    sec = section(x, "주요계약 및 연구개발활동") or section(x, "연구개발활동")
    for grid, (u, _), before in tables_with_unit(sec):
        flat = [" ".join(r) for r in grid]
        if u is None or not (any("연구개발" in f for f in flat) or "연구개발비" in before[-200:]):
            continue
        total = ratio = None
        cc = cur_col(grid) or 1
        for r in grid:
            lab = re.sub(r"\s+", "", " ".join(r[:max(1, cc)]))
            if total is None and (re.search(r"연구개발비용?(총계|합계|계)", lab) or re.match(r"^(합계|총계)", lab)):
                v, raw = first_num(r, cc)
                if v is not None:
                    total = v * u
                    m = re.search(r"\(([\d.]+)\s*%\)", raw or "")
                    if m:
                        ratio = float(m.group(1))
            if ratio is None and "매출액" in lab and ("비율" in lab or "%" in lab):
                v, _ = first_num(r, cc)
                if v is not None and abs(v) < 1000:
                    ratio = v
        if total:
            return total, ratio
    return None


def parse_capacity(x):
    """생산능력·생산실적·가동률. 표 머리에 '가동률' 열이 있으면 그 행의 능력·실적·가동률을 한 번에, 아니면 표 앞 문맥의 마지막 키워드로 표 종류 판정."""
    sec = section(x, "원재료 및 생산설비")
    out = {}
    for grid, (u, uname), before in tables_with_unit(sec):
        if len(grid) < 2:
            continue
        hdr = [" ".join(grid[k][j] for k in range(min(2, len(grid))) if j < len(grid[k])) for j in range(max(len(r) for r in grid))]
        ucol = next((j for j, h in enumerate(hdr) if "가동률" in h), None)
        if ucol is not None:
            ccol = next((j for j, h in enumerate(hdr) if re.search(r"생산능력|가능", h)), None)
            pcol = next((j for j, h in enumerate(hdr) if re.search(r"실제|생산실적", h)), None)
            first = min(c for c in (ucol, ccol, pcol) if c is not None)
            for r in grid[2:] if any("가동률" in c for c in grid[1]) else grid[1:]:
                if len(r) <= ucol:
                    continue
                item = re.sub(r"\s+", " ", " / ".join(dict.fromkeys(c for c in r[:first] if c))).strip()
                ut = num(r[ucol])
                if not item or ut is None or not (0 <= ut <= 200):
                    continue
                d = out.setdefault(item, {"unit": uname or ""})
                d["util"] = ut
                if ccol is not None and ccol < len(r):
                    d["cap_u"] = num(r[ccol])
                if pcol is not None and pcol < len(r):
                    d["prod_u"] = num(r[pcol])
            continue
        tail = before[-300:]
        pos = {k: tail.rfind(k) for k in ("생산능력", "생산실적")}
        kind = max(pos, key=pos.get) if max(pos.values()) >= 0 else None
        cc = cur_col(grid)
        if not kind or cc is None:
            continue
        for r in grid[1:]:
            item = re.sub(r"\s+", " ", " / ".join(dict.fromkeys(c for c in r[:cc] if c))).strip()
            v, _ = first_num(r, cc)
            if v is None or not item:
                continue
            d = out.setdefault(item, {"unit": uname or ""})
            d["cap" if kind == "생산능력" else "prod"] = v
    for d in out.values():
        d["cap"] = d.get("cap_u") if d.get("cap_u") is not None else d.get("cap")
        d["prod"] = d.get("prod_u") if d.get("prod_u") is not None else d.get("prod")
    return out


def parse_raw_material_price(x):
    sec = section(x, "원재료 및 생산설비")
    out = {}
    for grid, (u, uname), before in tables_with_unit(sec):
        if "가격" not in before[-250:] and not any("가격" in " ".join(r) for r in grid[:1]):
            continue
        cc = cur_col(grid)
        if cc is None:
            continue
        for r in grid[1:]:
            item = re.sub(r"\s+", " ", " / ".join(dict.fromkeys(c for c in r[:cc] if c))).strip()
            v, _ = first_num(r, cc)
            # 전기 열은 병합 셀이 복제돼 당기와 같은 값이 읽히는 표가 있어 쓰지 않는다 — 전년 비교는 전년 보고서 당기 값으로(화면 API)
            if item and v is not None:
                out[item] = (v, None, uname or "")
    return out


def parse_dom_exp(x):
    """매출실적 표의 '합계' 블록(합계 | 내수 / 수출 / 합계), 없으면 '지역별 매출' 표(내수/국내 vs 나머지)."""
    res = []
    sec = section(x, "매출 및 수주상황")
    for grid, (u, _), before in tables_with_unit(sec):
        if u is None:
            continue
        cc = cur_col(grid)
        if cc is None:
            continue
        dom = exp = tot = None
        in_total = False
        for r in grid[1:]:
            head = re.sub(r"\s+", "", "".join(dict.fromkeys(r[:cc])))  # 병합 셀 복제 제거('계계'→'계')
            if re.match(r"^(합계|총계)", head):
                in_total = True
            if not in_total:
                continue
            v, _ = first_num(r, cc)
            if v is None:
                continue
            if "내수" in head:
                dom = v * u
            elif "수출" in head:
                exp = v * u
            elif re.search(r"(합계|총계|소계)$", head) or head in ("합계", "총계"):
                tot = v * u
        if dom is not None and exp is not None:
            res.append(("매출실적", dom, exp, tot))
            break
        # 지역별 매출 표: 내수/국내 행 + 나머지 + 계
        if any("내수" in " ".join(r) or "국내" in " ".join(r) for r in grid) and any(re.sub(r"\s", "", r[0]) in ("계", "합계", "총계") for r in grid):
            d = t = None
            others = 0.0
            for r in grid[1:]:
                head = re.sub(r"\s+", "", "".join(dict.fromkeys(r[:cc])))  # 병합 셀 복제 제거('계계'→'계')
                v, _ = first_num(r, cc)
                if v is None:
                    continue
                if re.match(r"^(계|합계|총계)$", head):
                    t = v * u
                elif "내수" in head or "국내" in head:
                    d = (d or 0) + v * u
                else:
                    others += v * u
            if d is not None and t:
                res.append(("지역별매출표", d, t - d, t))
                break
    return res



COST_CAT = [("재고·원재료", r"원재료|상품.*매입|매입액|재공품|재고자산의?\s*변동|제품.*변동"), ("인건비", r"급여|인건비|퇴직|복리후생|종업원"),
            ("감가상각", r"감가상각|상각비"), ("외주·용역", r"외주|용역|지급수수료|수수료"), ("연구개발", r"연구|개발비"),
            ("판매·물류", r"광고|판매촉진|운반|물류|판매수수료"), ("기타", r".")]


def parse_cost_nature(x):
    """비용의 성격별 분류 주석(연결 주석 우선). 반환 (구분, {범주: 금액}, {항목: 금액}, 합계)."""
    for title, fs in (("연결재무제표 주석", "CFS"), ("재무제표 주석", "OFS")):
        sec = section(x, title)
        i = sec.find("성격별")
        if i < 0:
            continue
        sub = sec[i:i + 40000]
        for grid, (u, _), _ in tables_with_unit(sub)[:3]:
            if u is None or len(grid) < 3:
                continue
            cc = cur_col(grid) or next((j for j, c in enumerate(grid[1] if len(grid) > 1 else []) if "당기" in c), None)
            if cc is None:
                for r in grid[:3]:
                    for j, c in enumerate(r):
                        if "당기" in c or "당 기" in c:
                            cc = j
                            break
                    if cc is not None:
                        break
            if cc is None:
                continue
            items, total = {}, None
            for r in grid:
                lab = re.sub(r"\s+", "", " ".join(dict.fromkeys(r[:cc])))
                v, _ = first_num(r, cc)
                if v is None or not lab or "당기" in lab:
                    continue
                if re.match(r"^(합계|총계|계)", lab):
                    total = v * u
                    break
                items[lab] = v * u
            if items and total:
                cats = collections.Counter()
                for k, v in items.items():
                    cat = next(c for c, pat in COST_CAT if re.search(pat, k))
                    cats[cat] += v
                return fs, dict(cats), items, total
    return None


def main():
    conn = connect_primary_db(timeout=900)
    for d in DDL:
        conn.execute(d)
    rev = collections.defaultdict(dict)
    for c, y, fs, v in map(tuple, conn.execute("SELECT stock_code, year, report_type, revenue FROM financial_data WHERE is_annual AND revenue>0 AND year>=2021").fetchall()):
        rev[(c, y)][fs] = v
    import gzip as _gz
    def is_cost(code, fy, fs):
        f = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt") / code / f"{fy}_0_{fs}.json.gz"
        if not f.exists():
            return None
        try:
            rows = json.loads(_gz.decompress(f.read_bytes()))
        except Exception:
            return None
        rows = rows.get("list", []) if isinstance(rows, dict) else rows
        tot = 0.0
        got = False
        for r in rows:
            if r.get("sj_div") in ("IS", "CIS") and (r.get("account_id") in ("ifrs-full_CostOfSales", "dart_TotalSellingGeneralAdministrativeExpenses",
                                                                             "ifrs-full_SellingGeneralAndAdministrativeExpense")):
                v = str(r.get("thstrm_amount") or "").replace(",", "")
                if v.lstrip("-").replace(".", "").isdigit():
                    tot += abs(float(v)); got = True
        return tot if got else None
    have_mix = {(r[0], r[1]) for r in map(tuple, conn.execute("SELECT DISTINCT stock_code, year FROM company_product_mix").fetchall())}
    run_id = f"biz_docs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    st = collections.Counter()
    latest = {}
    for f in glob.glob(str(RAW / "*" / "*.zip")):
        p = Path(f)
        m = re.match(r"(\d{4})_(\d+)\.zip$", p.name)
        if m:
            k = (p.parent.name, int(m.group(1)))
            if k not in latest or m.group(2) > latest[k][1]:
                latest[k] = (f, m.group(2))
    rd_rows, cap_rows, raw_rows, de_rows, mix_rows, cost_rows = [], [], [], [], [], []
    for (code, fy), (f, rc) in sorted(latest.items()):
        try:
            x = load(f)
        except Exception:
            st["원문 읽기 실패"] += 1
            continue
        rv = rev.get((code, fy), {})
        r = parse_rd(x)
        if r:
            tot, ratio = r
            comp = {fs: tot / v * 100 for fs, v in rv.items()}
            ok = ratio is not None and any(abs(c - ratio) <= max(0.3, ratio * 0.1) for c in comp.values())
            status = "ok" if ok else ("no_ratio" if ratio is None else "ratio_mismatch")
            st[f"연구개발비 {status}"] += 1
            rd_rows.append((code, fy, tot, ratio, min(comp.values(), key=lambda c: abs(c - (ratio or c))) if comp else None, status, rc, run_id))
        for item, d in parse_capacity(x).items():
            cp, pr, ut = d.get("cap"), d.get("prod"), d.get("util")
            status = "ok" if (cp and pr and ut is not None and abs(pr / cp * 100 - ut) <= 2) else ("unverified" if ut is not None or cp or pr else None)
            if status:
                st[f"가동률 {status}"] += 1
                cap_rows.append((code, fy, item[:200], cp, pr, ut, d.get("unit"), status, rc, run_id))
        for item, (v, p, un) in parse_raw_material_price(x).items():
            raw_rows.append((code, fy, item[:200], v, p, un, "unverified", rc, run_id))
            st["원재료 가격"] += 1
        for src, dom, exp, tot in parse_dom_exp(x):
            total = tot or (dom + exp)
            ident = tot is None or abs(dom + exp - tot) <= abs(tot) * 0.005
            basis = next((fs for fs, v in rv.items() if abs(total - v) <= abs(v) * 0.01), None)
            status = "ok" if ident and basis else ("identity_fail" if not ident else "revenue_mismatch")
            st[f"내수/수출 {status}"] += 1
            de_rows.append((code, fy, src, dom, exp, total, exp / total * 100 if total else None, basis, status, rc, run_id))
        cn = parse_cost_nature(x)
        if cn:
            fs, cats, items, total = cn
            ic = is_cost(code, fy, fs)
            stt = "ok" if ic and abs(total - ic) <= abs(ic) * 0.01 else ("no_is" if ic is None else "total_mismatch")
            st[f"비용성격 {stt}"] += 1
            for cat, v in cats.items():
                cost_rows.append((code, fy, fs, cat, v, json.dumps(items, ensure_ascii=False)[:4000], total, ic, stt, rc, run_id))
        if fy <= 2022 and (code, fy) not in have_mix:
            try:
                recs, _, mul = extract_product_mix(x)
            except Exception:
                recs = []
            tot = sum((q["revenue"] or 0) * mul for q in recs)
            if recs and tot > 0:
                for q in recs:
                    mix_rows.append((code, fy, q["category"], q["product_name"], q["revenue"] * mul, round(q["revenue"] * mul / tot * 100, 2), rc, "dart_doc_cache"))
                st["제품별 매출(2021~22)"] += 1
    for t in ("biz_rd_expense", "biz_capacity", "biz_raw_material_price", "biz_sales_domestic_export", "biz_cost_nature"):
        conn.execute(f"DELETE FROM {t}")
    def ins(t, cols, rows):
        rows = list({tuple(r[:k]): r for r in rows for k in [cols]}.values())
        if rows:
            ph = ",".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {t} VALUES ({ph})", rows)
    ins("biz_rd_expense", 2, rd_rows)
    ins("biz_capacity", 3, cap_rows)
    ins("biz_raw_material_price", 3, raw_rows)
    ins("biz_sales_domestic_export", 3, de_rows)
    ins("biz_cost_nature", 4, cost_rows)
    if mix_rows:
        conn.executemany("""INSERT INTO company_product_mix(stock_code, year, category, product_name, revenue_krw, revenue_pct, rcept_no, source)
                            VALUES (?,?,?,?,?,?,?,?)""", mix_rows)
    conn.commit()
    print(run_id, f"원문 {len(latest):,}건(종목·연도)", json.dumps(dict(st), ensure_ascii=False))


if __name__ == "__main__":
    main()
