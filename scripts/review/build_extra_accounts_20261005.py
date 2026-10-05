#!/usr/bin/env python3
"""재무 추가 계정(2021~ 연간): 매출채권·단기차입금·장기차입금·사채·금융원가·이자비용·이자 지급(2026-10-05 사용자 요청).

원천: data_raw/dart_fnltt/<code>/<Y>_0_<CFS|OFS>.json.gz (DART fnlttSinglAcntAll 전체 계정 원문 행, 매일 밤 보충).
검증(원칙 0): FnGuide wcomp 연간 원문(같은 구분, 결산월 열)과 max(1억, 0.5%) 이내면 confirmed, 다르면 mismatch, FnGuide에 없으면 dart_only.
  FnGuide 연간 표시는 최근 3개 연도뿐이라 2021~2022년은 대부분 dart_only(미확정)로 남는다.
결과 financial_extra_accounts(매 실행 전체 교체). 화면은 confirmed + dart_only(미확정 표시)만.
"""
import collections
import glob
import gzip
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

DART = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
FG = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/fnguide_wcomp")
# field: (재무제표 구분, IFRS/DART 계정 id 후보, 계정명 정규식, FnGuide 계정명)
FIELDS = {
    "trade_receivables": ("BS", ("ifrs-full_CurrentTradeReceivables", "dart_ShortTermTradeReceivable"),
                          r"^매출채권$", None),  # 좁은 정의(매출채권만) — FnGuide 대조는 trade_other_receivables
    "short_borrowings": ("BS", ("ifrs-full_ShortTermBorrowings",), r"^단기차입금$", "단기차입금"),
    "long_borrowings": ("BS", ("ifrs-full_LongtermBorrowings", "dart_LongTermBorrowingsGross"), r"^장기차입금$", "장기차입금"),
    "bonds": ("BS", ("dart_BondsIssued", "ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued", "ifrs-full_BondsIssued"), r"^사채$", "사채"),
    "finance_costs": ("IS", ("ifrs-full_FinanceCosts",), r"^금융(원가|비용)$", "금융원가"),
    "interest_expense": ("IS", ("ifrs-full_InterestExpense", "dart_InterestExpenseFinanceExpense"), r"^이자비용$", "이자비용"),
    "interest_paid": ("CF", ("ifrs-full_InterestPaidClassifiedAsOperatingActivities", "ifrs-full_InterestPaidClassifiedAsFinancingActivities"),
                      r"^이자(의)?지급", None),
}
RECV = re.compile(r"(매출채권|미수금|미수수익|수취채권|유동채권|계약자산|받을어음)")
DDL = """CREATE TABLE IF NOT EXISTS financial_extra_accounts (stock_code TEXT, fiscal_year INTEGER, report_type TEXT, field TEXT,
    value_krw DOUBLE PRECISION, fnguide_krw DOUBLE PRECISION, status TEXT, account_id TEXT, account_nm TEXT, rcept_no TEXT, run_id TEXT,
    PRIMARY KEY (stock_code, fiscal_year, report_type, field))"""


def amt(s):
    s = str(s or "").replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(rows, sj, ids, pat):
    sjs = {"BS": ("BS",), "IS": ("IS", "CIS"), "CF": ("CF",)}[sj]
    cand = [r for r in rows if r.get("sj_div") in sjs]
    for i in ids:
        for r in cand:
            if r.get("account_id") == i and amt(r.get("thstrm_amount")) is not None:
                return r
    for r in cand:
        if re.search(pat, re.sub(r"\s+", "", r.get("account_nm", ""))) and amt(r.get("thstrm_amount")) is not None:
            return r
    return None


def fnguide(code):
    """{(fy, fs): {이름: 원}} — 연간 표, 결산월 열 = 회계연도."""
    d = FG / code
    files = sorted(d.glob("*.json.gz")) if d.exists() else []
    if not files:
        return {}
    day = files[-1].name[:8]
    out = collections.defaultdict(dict)
    for consol, fs in (("C", "CFS"), ("P", "OFS")):
        for ep in ("getFinBalance", "getFinIncome"):
            f = d / f"{day}_{consol}_Y_{ep}.json.gz"
            if not f.exists():
                continue
            try:
                ds = json.loads(gzip.decompress(f.read_bytes())).get("dataset") or {}
            except Exception:
                continue
            cols = {h["CD"]: int(h["YYMM"][:4]) for h in ds.get("header") or [] if re.fullmatch(r"20\d\d/\d\d", str(h.get("YYMM", "")).strip())}
            for r in ds.get("data") or []:
                n = str(r.get("NAME") or "").replace(" ", "")
                for cd, fy in cols.items():
                    v = r.get(cd)
                    if v not in (None, "") and n not in out[(fy, fs)]:
                        try:
                            out[(fy, fs)][n] = float(v) * 1e8
                        except ValueError:
                            pass
    return out


def main():
    run_id = f"extra_acc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    rows, st = [], collections.Counter()
    fgc = {}
    recv_def = {}
    files = sorted(glob.glob(str(DART / "*" / "*_0_*.json.gz")), key=lambda f: (Path(f).parent.name, -int(Path(f).name[:4])))
    for f in files:  # 종목별 최신 연도부터 → FnGuide 있는 연도에서 정의를 배운 뒤 과거 연도에 적용
        p = Path(f)
        m = re.match(r"(\d{4})_0_(CFS|OFS)\.json\.gz$", p.name)
        if not m or int(m.group(1)) < 2021:
            continue
        code, fy, fs = p.parent.name, int(m.group(1)), m.group(2)
        try:
            data = json.loads(gzip.decompress(p.read_bytes()))
        except Exception:
            continue
        if isinstance(data, dict):
            data = data.get("list") or []
        if code not in fgc:
            fgc[code] = fnguide(code)
        fgv = fgc[code].get((fy, fs), {})
        # FnGuide '매출채권및기타채권'(유동) 정의는 회사마다 DART 계정 구성이 다르다(매출채권+미수금+미수수익+계약자산…).
        # → 유동자산 구간의 채권류 행 중 FnGuide 값과 합이 맞는 조합을 찾고(최대 7행), 그 계정명 조합을 회사 정의로 기억해
        #   FnGuide가 없는 연도(2021~22)에 같은 조합을 적용한다(recv_def). 조합을 못 찾으면 mismatch.
        cur_rows, seen_nc = [], False
        for r in sorted((r for r in data if r.get("sj_div") == "BS"), key=lambda r: int(r.get("ord") or 0)):
            nm = re.sub(r"\s+", "", r.get("account_nm", ""))
            if nm.startswith("비유동자산"):
                seen_nc = True
            if not seen_nc and RECV.search(nm) and not nm.startswith("장기") and amt(r.get("thstrm_amount")) is not None:
                cur_rows.append((nm, amt(r["thstrm_amount"]), r))
        uniq = list({nm: (nm, v, r) for nm, v, r in cur_rows}.values())[:7]
        e0 = fgv.get("매출채권및기타채권")
        tao = None
        if uniq and e0 and code not in recv_def:
            import itertools
            for k in range(1, len(uniq) + 1):
                hit = next((cmb for cmb in itertools.combinations(uniq, k) if abs(sum(x[1] for x in cmb) - e0) <= max(1e8, abs(e0) * 0.005)), None)
                if hit:
                    recv_def[code] = (tuple(sorted(x[0] for x in hit)), fy)
                    break
        names, learn_fy = recv_def.get(code, (None, None))
        if names:
            got = [x for x in uniq if x[0] in names]
            if len(got) == len(names):
                tao = {"thstrm_amount": str(sum(x[1] for x in got)), "account_id": "회사정의" + ("(학습연도)" if fy == learn_fy else ""),
                       "account_nm": "+".join(names)[:200], "rcept_no": got[0][2].get("rcept_no")}
        if tao is None and not names:  # FnGuide 원문이 아직 없는 종목: 기본 규칙(한 줄 계정 또는 매출채권+미수금+미수수익) — 미확정
            tao = pick(data, "BS", ("ifrs-full_TradeAndOtherCurrentReceivables",), r"^매출채권및기타(유동)?채권$")
            if tao is None:
                base = [x for x in uniq if re.fullmatch(r"(단기)?(매출채권|미수금|미수수익)", x[0])]
                if base:
                    tao = {"thstrm_amount": str(sum(x[1] for x in base)), "account_id": "기본규칙", "account_nm": "+".join(x[0] for x in base),
                           "rcept_no": base[0][2].get("rcept_no")}
        extra = {"trade_other_receivables": tao}
        for field, (sj, ids, pat, fgname) in list(FIELDS.items()) + [("trade_other_receivables", ("BS", (), r"$^", "매출채권및기타채권"))]:
            r = extra[field] if field in extra else pick(data, sj, ids, pat)
            if r is None:
                continue
            v = amt(r["thstrm_amount"])
            e = fgv.get(fgname) if fgname else None
            if field == "trade_other_receivables" and str(r.get("account_id", "")).endswith("(학습연도)"):
                status = "definition_fit"  # 이 연도 FnGuide 값으로 계정 조합을 정했으므로 독립 확인이 아님
            elif e is None:
                status = "dart_only"
            elif abs(v - e) <= max(1e8, abs(e) * 0.005):
                status = "confirmed"
            else:
                status = "mismatch"
            st[f"{field}:{status}"] += 1
            rows.append((code, fy, fs, field, v, e, status, r.get("account_id"), r.get("account_nm"), r.get("rcept_no"), run_id))
    conn = connect_primary_db(timeout=900)
    conn.execute(DDL)
    conn.execute("DELETE FROM financial_extra_accounts")
    rows = list({r[:4]: r for r in rows}.values())
    for i in range(0, len(rows), 5000):
        conn.executemany("INSERT INTO financial_extra_accounts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows[i:i + 5000])
    conn.commit()
    agg = collections.defaultdict(collections.Counter)
    for k, n in st.items():
        f, s = k.split(":")
        agg[f][s] += n
    for f, c in agg.items():
        comp = c["confirmed"] + c["mismatch"]
        print(f"{f:18s} {dict(c)}  대조 일치율 {c['confirmed'] / comp * 100:.2f}%" if comp else f"{f:18s} {dict(c)}")
    print(run_id, len(rows))


if __name__ == "__main__":
    main()
