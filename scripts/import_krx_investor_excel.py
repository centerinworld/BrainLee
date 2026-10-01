"""
KRX Data Marketplace 엑셀 다운로드 → price_history 수급 컬럼 임포트

KRX OpenAPI가 차단됨 → Data Marketplace에서 수동 다운로드한 엑셀 파일로 대체.

다운로드 경로: KRX Data Marketplace → 주식 → 투자자별 순매수 (일별)
파일 형식: 엑셀(.xlsx, .xls) 또는 CSV

사용법:
    python3 scripts/import_krx_investor_excel.py --file <다운로드한파일> [--date YYYY-MM-DD] [--dry-run]

엑셀 컬럼 구조 (KRX Data Marketplace 기준):
    종목코드 | 종목명 | 기관합계_순매수수량 | 기관합계_순매수금액 |
    외국인_순매수수량 | 외국인_순매수금액 | 개인_순매수수량 | 개인_순매수금액

컬럼명이 다를 경우 --col-map 또는 소스 수정.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_compat


# KRX Data Marketplace 엑셀의 컬럼명 매핑 후보
# key: 내부 필드명, value: 엑셀에서 찾을 컬럼명 목록 (순서대로 시도)
COLUMN_CANDIDATES = {
    "stock_code":  ["종목코드", "ISU_SRT_CD", "code"],
    "inst_qty":    ["기관합계_순매수수량", "기관합계순매수수량", "기관 순매수수량"],
    "inst_amt":    ["기관합계_순매수금액", "기관합계순매수금액", "기관 순매수금액"],
    "frn_qty":     ["외국인_순매수수량",  "외국인순매수수량",  "외국인 순매수수량"],
    "frn_amt":     ["외국인_순매수금액",  "외국인순매수금액",  "외국인 순매수금액"],
    "ind_qty":     ["개인_순매수수량",    "개인순매수수량",    "개인 순매수수량"],
    "ind_amt":     ["개인_순매수금액",    "개인순매수금액",    "개인 순매수금액"],
}


def _find_col(df_columns, candidates):
    for c in candidates:
        if c in df_columns:
            return c
    return None


def load_file(path: str):
    try:
        import pandas as pd
    except ImportError:
        sys.exit("pandas 패키지가 필요합니다: pip install pandas openpyxl")

    ext = os.path.splitext(path)[1].lower()
    if ext in (".xls", ".xlsx"):
        df = pd.read_excel(path, dtype=str)
    elif ext == ".csv":
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    else:
        sys.exit(f"지원하지 않는 파일 형식: {ext} (xlsx, xls, csv만 가능)")

    # 공백 컬럼명 정리
    df.columns = [str(c).strip() for c in df.columns]
    return df


def parse_number(val) -> float:
    if val is None or str(val).strip() in ("", "-", "nan"):
        return 0.0
    return float(str(val).replace(",", "").replace(" ", ""))


def infer_date_from_filename(path: str):
    """파일명에서 YYYYMMDD 또는 YYYY-MM-DD 추출 시도."""
    name = os.path.basename(path)
    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def main():
    parser = argparse.ArgumentParser(description="KRX 투자자 수급 엑셀 → DB 임포트")
    parser.add_argument("--file", required=True, help="KRX Data Marketplace 다운로드 파일 경로")
    parser.add_argument("--date", help="기준일 YYYY-MM-DD (미지정 시 파일명에서 추출)")
    parser.add_argument("--dry-run", action="store_true", help="실제 DB 반영 없이 확인만")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        sys.exit(f"파일 없음: {args.file}")

    bas_dd_iso = args.date
    if not bas_dd_iso:
        bas_dd_iso = infer_date_from_filename(args.file)
    if not bas_dd_iso:
        sys.exit("--date YYYY-MM-DD 를 지정하거나 파일명에 날짜(YYYYMMDD)를 포함하세요.")

    print(f"기준일: {bas_dd_iso}")
    print(f"파일: {args.file}")

    df = load_file(args.file)
    print(f"로드 완료: {len(df):,}행, 컬럼: {list(df.columns)}")

    # 컬럼 매핑
    col_map = {}
    for field, candidates in COLUMN_CANDIDATES.items():
        found = _find_col(df.columns, candidates)
        if found is None and field == "stock_code":
            sys.exit(f"종목코드 컬럼을 찾을 수 없습니다. 컬럼 목록: {list(df.columns)}")
        col_map[field] = found

    print(f"컬럼 매핑: {col_map}")

    # 파싱
    stats = {}
    for _, row in df.iterrows():
        raw_code = str(row[col_map["stock_code"]]).strip().zfill(6)
        if not raw_code or not raw_code.isdigit():
            continue
        code = raw_code[-6:]

        def get(field):
            c = col_map.get(field)
            return parse_number(row[c]) if c else 0.0

        stats[code] = {
            "inst_qty": get("inst_qty"),
            "inst_amt": get("inst_amt"),
            "frn_qty":  get("frn_qty"),
            "frn_amt":  get("frn_amt"),
            "ind_qty":  get("ind_qty"),
            "ind_amt":  get("ind_amt"),
        }

    print(f"유효 종목: {len(stats):,}개")

    if args.dry_run:
        for code, s in list(stats.items())[:5]:
            print(f"  {code}: inst_amt={s['inst_amt']:,.0f} frn_amt={s['frn_amt']:,.0f} ind_amt={s['ind_amt']:,.0f}")
        print("[dry-run] DB 반영 없음 — --dry-run 없이 실행하면 적용됩니다.")
        return

    conn = db_compat.connect_primary_db()
    cursor = conn.cursor()
    updated = inserted = 0

    for code, s in stats.items():
        cursor.execute(
            "SELECT 1 FROM price_history WHERE stock_code=%s AND date=%s",
            (code, bas_dd_iso),
        )
        exists = cursor.fetchone()

        if exists:
            cursor.execute(
                """
                UPDATE price_history
                SET inst_net_buy_amt=%s, frn_net_buy_amt=%s, ind_net_buy_amt=%s,
                    inst_net_buy=%s, frn_net_buy=%s, ind_net_buy=%s
                WHERE stock_code=%s AND date=%s
                """,
                (s["inst_amt"], s["frn_amt"], s["ind_amt"],
                 s["inst_qty"], s["frn_qty"], s["ind_qty"],
                 code, bas_dd_iso),
            )
            updated += 1
        else:
            cursor.execute("SELECT set_config('app.price_basis_checked','1',true)")
            cursor.execute(
                """
                INSERT INTO price_history
                (stock_code, date, open, high, low, close, volume,
                 inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt,
                 inst_net_buy, frn_net_buy, ind_net_buy)
                VALUES (%s, %s, 0, 0, 0, 0, 0, %s, %s, %s, %s, %s, %s)
                """,
                (code, bas_dd_iso,
                 s["inst_amt"], s["frn_amt"], s["ind_amt"],
                 s["inst_qty"], s["frn_qty"], s["ind_qty"]),
            )
            inserted += 1

    conn.commit()
    conn.close()
    print(f"✅ 완료: UPDATE {updated:,}건, INSERT {inserted:,}건 (기준일: {bas_dd_iso})")


if __name__ == "__main__":
    main()
