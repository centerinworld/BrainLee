"""
재무데이터 CFS/OFS 혼용 오류 수정 스크립트
- 연간 보고서에서 사용된 FS 타입(CFS or OFS)을 먼저 확인
- 분기 보고서도 동일 FS 타입으로 재수집
- 대상: Q4 영업이익이 -5000억원 이하인 (stock_code, year) 36쌍
"""
import sqlite3, time, sys, traceback
import pandas as pd
from dart_key_manager import RotatingOpenDartReader

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
dart = RotatingOpenDartReader()

# 재수집 대상 (stock_code, year) 목록
TARGETS = [
    ("000660", 2018), ("000720", 2024), ("001430", 2020), ("001800", 2017),
    ("003490", 2016), ("003490", 2018), ("003490", 2022), ("003490", 2023),
    ("003490", 2024), ("003490", 2025), ("003550", 2017), ("003550", 2020),
    ("005930", 2019), ("006400", 2016), ("009410", 2023), ("009540", 2021),
    ("010140", 2017), ("011170", 2019), ("011170", 2021), ("015750", 2024),
    ("018670", 2017), ("034020", 2021), ("034730", 2022), ("034730", 2023),
    ("034730", 2025), ("047040", 2016), ("047040", 2025), ("047050", 2025),
    ("051910", 2024), ("065150", 2023), ("078930", 2017), ("078930", 2021),
    ("078930", 2022), ("096770", 2021), ("096770", 2022), ("096770", 2023),
]

# rcode → quarter
QMAP = {"11011": 0, "11013": 1, "11012": 2, "11014": 3}  # 11011=연간(사업보고서)

ACCOUNTS = [
    ("revenue",           ["매출액", "영업수익"]),
    ("operating_profit",  ["영업이익"]),
    ("net_income",        ["당기순이익", "분기순이익", "반기순이익"]),
    ("total_assets",      ["자산총계"]),
    ("total_liabilities", ["부채총계"]),
    ("total_equity",      ["자본총계"]),
    ("capital_stock",     ["자본금"]),
    ("eps",               ["기본주당순이익", "주당순이익", "기본EPS"]),
    ("bps",               ["주당순자산", "1주당순자산가액"]),
    ("dps",               ["주당배당금", "주당현금배당금"]),
    ("cash",              ["현금및현금성자산", "현금성자산"]),
]

def _parse_row(fn_data):
    """DataFrame → {revenue, operating_profit, ...} 딕셔너리"""
    m = {k: 0.0 for k, _ in ACCOUNTS}
    for _, row in fn_data.iterrows():
        acc = str(row.get("account_nm", "")).replace(" ", "")
        vc = "thstrm_amount"
        if not acc or vc not in row or pd.isna(row[vc]):
            continue
        try:
            val = float(str(row[vc]).replace(",", ""))
        except:
            continue
        for key, keywords in ACCOUNTS:
            if any(k in acc for k in keywords):
                # net_income: 제외 조건
                if key == "net_income" and ("주당" in acc or "지배" in acc):
                    continue
                m[key] = val
                break
    return m


def _fetch_fs(stock_code, year, rcode, fs_type):
    """특정 FS 타입으로 DART 데이터 가져오기. 실패시 None 반환."""
    try:
        df = dart.finstate_all(stock_code, year, rcode, fs_div=fs_type)
        if df is not None and not df.empty:
            return df
    except:
        pass
    return None


def _detect_fs_type(stock_code, year):
    """
    연간 보고서(11011)에서 CFS/OFS 중 어느 타입이 유효한지 판별.
    반환: ("CFS", df) or ("OFS", df) or (None, None)
    """
    for fs in ["CFS", "OFS"]:
        df = _fetch_fs(stock_code, year, "11011", fs)
        if df is not None:
            return fs, df
    return None, None


def _sub(annual, q1, q2, q3):
    if annual is None:
        return None
    return (annual or 0) - (q1 or 0) - (q2 or 0) - (q3 or 0)


def process_one(stock_code, year, conn):
    print(f"\n  [{stock_code}] {year}년 재수집 시작...")

    # 1. 연간 보고서로 FS 타입 판별
    fs_type, annual_df = _detect_fs_type(stock_code, year)
    if fs_type is None:
        print(f"    ⚠️  연간 DART 데이터 없음 - 스킵")
        return False

    print(f"    FS 타입: {fs_type}")
    annual_m = _parse_row(annual_df)

    # 2. 분기 데이터를 동일 FS 타입으로 수집
    qdata = {}  # quarter → parsed dict
    rcode_map = {"11013": 1, "11012": 2, "11014": 3}

    for rcode, qnum in rcode_map.items():
        df = _fetch_fs(stock_code, year, rcode, fs_type)
        if df is None:
            # OFS 실패 시 반대 FS 타입 시도 (데이터 없는 경우만)
            alt_fs = "OFS" if fs_type == "CFS" else "CFS"
            df = _fetch_fs(stock_code, year, rcode, alt_fs)
            if df is not None:
                print(f"    Q{qnum}: {fs_type} 없음 → {alt_fs} 사용")
        if df is None:
            # finstate fallback
            try:
                df = dart.finstate(stock_code, year, rcode)
            except:
                pass
        if df is not None:
            qdata[qnum] = _parse_row(df)
            print(f"    Q{qnum} 수집 OK (revenue={qdata[qnum]['revenue']:.0f})")
        else:
            print(f"    Q{qnum} 수집 실패")
        time.sleep(0.3)

    # 3. 기존 해당 연도 데이터 삭제 (연간 포함 전체)
    conn.execute(
        "DELETE FROM financial_data WHERE stock_code=? AND year=?",
        (stock_code, year)
    )

    # 4. 연간 데이터 저장
    conn.execute("""
        INSERT INTO financial_data
        (stock_code, year, quarter, is_annual,
         revenue, operating_profit, net_income,
         total_assets, total_liabilities, total_equity,
         capital_stock, eps, bps, dps, cash)
        VALUES (?,?,0,1,?,?,?,?,?,?,?,?,?,?,?)
    """, (stock_code, year,
          annual_m["revenue"], annual_m["operating_profit"], annual_m["net_income"],
          annual_m["total_assets"], annual_m["total_liabilities"], annual_m["total_equity"],
          annual_m["capital_stock"], annual_m["eps"], annual_m["bps"],
          annual_m["dps"], annual_m["cash"]))
    print(f"    연간 저장: 영업이익={annual_m['operating_profit']/1e8:.0f}억원")

    # 5. 분기 데이터 저장 (Q1, Q2, Q3)
    for qnum in [1, 2, 3]:
        if qnum not in qdata:
            continue
        qm = qdata[qnum]
        conn.execute("""
            INSERT INTO financial_data
            (stock_code, year, quarter, is_annual,
             revenue, operating_profit, net_income,
             total_assets, total_liabilities, total_equity,
             capital_stock, eps, bps, dps, cash)
            VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)
        """, (stock_code, year, qnum,
              qm["revenue"], qm["operating_profit"], qm["net_income"],
              qm["total_assets"], qm["total_liabilities"], qm["total_equity"],
              qm["capital_stock"], qm["eps"], qm["bps"], qm["dps"], qm["cash"]))

    # 6. Q4 자동 계산 (Q1+Q2+Q3 모두 있을 때)
    if all(q in qdata for q in [1, 2, 3]):
        q1m, q2m, q3m = qdata[1], qdata[2], qdata[3]
        q4_op  = _sub(annual_m["operating_profit"], q1m["operating_profit"], q2m["operating_profit"], q3m["operating_profit"])
        q4_ni  = _sub(annual_m["net_income"],        q1m["net_income"],        q2m["net_income"],        q3m["net_income"])
        q4_rev = _sub(annual_m["revenue"],           q1m["revenue"],           q2m["revenue"],           q3m["revenue"])

        conn.execute("""
            INSERT INTO financial_data
            (stock_code, year, quarter, is_annual,
             revenue, operating_profit, net_income,
             total_assets, total_liabilities, total_equity,
             capital_stock, eps, bps, dps, cash)
            VALUES (?,?,4,0,?,?,?,?,?,?,?,?,?,?,?)
        """, (stock_code, year,
              q4_rev, q4_op, q4_ni,
              annual_m["total_assets"], annual_m["total_liabilities"], annual_m["total_equity"],
              annual_m["capital_stock"], annual_m["eps"], annual_m["bps"],
              annual_m["dps"], annual_m["cash"]))
        print(f"    Q4 계산: 영업이익={q4_op/1e8:.0f}억원")
    else:
        missing = [q for q in [1,2,3] if q not in qdata]
        print(f"    Q4 계산 불가 (Q{missing} 미수집)")

    conn.commit()
    return True


def main():
    conn = sqlite3.connect(DB_PATH)

    ok_count = 0
    fail_count = 0
    fail_list = []

    total = len(TARGETS)
    for idx, (code, year) in enumerate(TARGETS):
        print(f"\n[{idx+1}/{total}] {code} {year}년")
        try:
            ok = process_one(code, year, conn)
            if ok:
                ok_count += 1
            else:
                fail_count += 1
                fail_list.append((code, year, "DART 데이터 없음"))
        except Exception as e:
            fail_count += 1
            fail_list.append((code, year, str(e)))
            print(f"    ❌ 오류: {e}")
            traceback.print_exc()

        # DART API rate limit 방지
        time.sleep(0.5)

    conn.close()

    print("\n\n" + "="*50)
    print(f"재수집 완료: 성공 {ok_count}건 / 실패 {fail_count}건")
    if fail_list:
        print("실패 목록:")
        for code, year, reason in fail_list:
            print(f"  {code} {year}: {reason}")

    # 최종 검증
    conn2 = sqlite3.connect(DB_PATH)
    remaining = conn2.execute("""
        SELECT COUNT(*) FROM financial_data
        WHERE is_annual=0 AND quarter=4 AND operating_profit < -500000000000
    """).fetchone()[0]
    print(f"\n잔존 Q4 < -5000억원 오류: {remaining}건")
    conn2.close()


if __name__ == "__main__":
    main()
