"""
data_fix.py — 주가 데이터 품질 수정 및 보완 스크립트

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 data_fix.py

수행 작업:
  1. Q4 중복 수정: is_annual=1 인 Q4 레코드를 quarter=0 으로 이동
  2. 음수 equity 수정: 자산-부채 불일치 레코드 보정
  3. EPS/BPS 계산: financial_data에서 역산하여 stock_universe 업데이트
  4. Sector 업데이트: FinanceDataReader KRX-DESC로 섹터 정보 채움
  5. PER/PBR 계산: 현재가 + EPS/BPS 기반 업데이트
  6. financial_data EPS/BPS 보정: shares_issued 기반 역산
"""

import sqlite3
import math
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "stock.db")


def fix_q4_duplicates(conn):
    """
    is_annual=1 이면서 quarter=4 인 레코드 → quarter=0 으로 변경.
    이렇게 하면 연간 합산 데이터(is_annual=1, quarter=0)와
    실제 4분기 데이터(is_annual=0, quarter=4)가 명확히 구분됨.
    """
    print("\n[1] Q4 중복 수정 (연간 데이터 → quarter=0)")

    # 현재 상태
    before = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE is_annual=1 AND quarter=4"
    ).fetchone()[0]
    print(f"  수정 대상: {before}개 레코드 (is_annual=1, quarter=4)")

    if before == 0:
        print("  → 이미 정상 상태")
        return

    conn.execute("""
        UPDATE financial_data
        SET quarter = 0
        WHERE is_annual = 1 AND quarter = 4
    """)
    conn.commit()

    after = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE is_annual=1 AND quarter=4"
    ).fetchone()[0]
    moved = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE is_annual=1 AND quarter=0"
    ).fetchone()[0]
    print(f"  → 완료: {before - after}개 quarter=0 으로 이동, 남은 Q4 연간: {after}개")
    print(f"  → 총 연간(is_annual=1, quarter=0) 레코드: {moved}개")


def fix_negative_equity(conn):
    """
    음수 equity 레코드 검사 및 보정.
    - total_assets - total_liabilities 와 total_equity 가 크게 불일치하면 → 재계산
    - 자산/부채 데이터도 없으면 → NULL 로 처리 (신뢰할 수 없는 데이터)
    """
    print("\n[2] 음수 equity 수정")

    rows = conn.execute("""
        SELECT id, stock_code, year, quarter,
               total_assets, total_liabilities, total_equity
        FROM financial_data
        WHERE total_equity < 0
    """).fetchall()

    print(f"  음수 equity 레코드: {len(rows)}개")

    fixed = 0
    nulled = 0
    legitimate = 0

    for row in rows:
        rid, code, year, qtr, assets, liab, equity = row

        if assets is not None and liab is not None:
            computed = assets - liab
            # 계산값과 저장값의 차이가 10% 초과하면 → 계산값으로 덮어씀
            if equity != 0 and abs(computed - equity) / max(abs(equity), 1) > 0.10:
                conn.execute(
                    "UPDATE financial_data SET total_equity=? WHERE id=?",
                    (round(computed, 0), rid)
                )
                fixed += 1
            else:
                # 불일치 없음 → 실제 완전자본잠식 (합법적 음수)
                legitimate += 1
        else:
            # 자산/부채 데이터도 없는데 equity만 음수 → NULL 처리
            conn.execute(
                "UPDATE financial_data SET total_equity=NULL WHERE id=?",
                (rid,)
            )
            nulled += 1

    conn.commit()
    print(f"  → 계산값으로 보정: {fixed}개")
    print(f"  → 합법적 완전자본잠식 (변경 없음): {legitimate}개")
    print(f"  → 데이터 없어 NULL 처리: {nulled}개")


def update_sector_from_fdr(conn):
    """
    FinanceDataReader KRX-DESC 로 stock_universe 섹터 정보 업데이트.
    """
    print("\n[3] 섹터 정보 업데이트 (FDR KRX-DESC)")

    try:
        import FinanceDataReader as fdr
        krx = fdr.StockListing('KRX-DESC')
    except Exception as e:
        print(f"  FDR 로드 실패: {e}")
        return

    # 컬럼 확인
    if 'Code' not in krx.columns:
        print("  KRX-DESC Code 컬럼 없음")
        return

    # sector 매핑
    sector_map = {}
    industry_map = {}
    for _, r in krx.iterrows():
        code = str(r.get('Code', '')).zfill(6)
        sec = r.get('Sector') or r.get('Industry') or None
        ind = r.get('Industry') or None
        if sec and str(sec) != 'nan':
            sector_map[code] = str(sec)
        if ind and str(ind) != 'nan':
            industry_map[code] = str(ind)

    print(f"  FDR 섹터 데이터: {len(sector_map)}개 종목")

    # stock_universe 업데이트
    updated = 0
    for code, sector in sector_map.items():
        industry = industry_map.get(code)
        conn.execute("""
            UPDATE stock_universe
            SET sector_large = COALESCE(sector_large, ?),
                sector_mid   = COALESCE(sector_mid, ?)
            WHERE stock_code = ? AND (sector_large IS NULL OR sector_large = '')
        """, (sector, industry, code))
        if conn.total_changes > 0:
            updated += 1

    conn.commit()

    # 결과 확인
    still_null = conn.execute(
        "SELECT COUNT(*) FROM stock_universe WHERE sector_large IS NULL"
    ).fetchone()[0]
    print(f"  → 업데이트: {updated}개")
    print(f"  → 여전히 섹터 없음: {still_null}개")


def calc_eps_bps_from_financial(conn):
    """
    financial_data의 EPS/BPS가 NULL인 경우 역산:
      - shares = stock_universe.shares_issued
      - EPS = net_income / shares
      - BPS = total_equity / shares
    그리고 stock_universe의 eps, bps, pbr, per 도 업데이트.
    """
    print("\n[4] EPS/BPS 역산 및 stock_universe 업데이트")

    # 주식수 맵 (stock_universe)
    shares_rows = conn.execute("""
        SELECT stock_code, shares_issued, close, per
        FROM stock_universe
        WHERE shares_issued IS NOT NULL AND shares_issued > 0
    """).fetchall()
    shares_map = {r[0]: (r[1], r[2], r[3]) for r in shares_rows}

    print(f"  주식수 데이터: {len(shares_map)}개 종목")

    # financial_data EPS/BPS NULL인 레코드에 역산 채우기
    fd_rows = conn.execute("""
        SELECT id, stock_code, net_income, total_equity, eps, bps
        FROM financial_data
        WHERE (eps IS NULL OR bps IS NULL)
          AND (net_income IS NOT NULL OR total_equity IS NOT NULL)
    """).fetchall()

    fd_updated = 0
    for row in fd_rows:
        rid, code, ni, eq, eps, bps = row
        if code not in shares_map:
            continue
        shares, close, per_from_su = shares_map[code]
        if shares <= 0:
            continue

        new_eps = round(ni / shares, 2) if (ni is not None and eps is None) else eps
        new_bps = round(eq / shares, 2) if (eq is not None and eq > 0 and bps is None) else bps

        if new_eps != eps or new_bps != bps:
            conn.execute(
                "UPDATE financial_data SET eps=?, bps=? WHERE id=?",
                (new_eps, new_bps, rid)
            )
            fd_updated += 1

    conn.commit()
    print(f"  → financial_data EPS/BPS 역산 업데이트: {fd_updated}개")

    # stock_universe 의 EPS/BPS/PBR/PER 업데이트
    # 가장 최근 분기 데이터(is_annual=0) 기준
    su_rows = conn.execute("""
        SELECT stock_code, close, per
        FROM stock_universe
        WHERE close IS NOT NULL AND close > 0
    """).fetchall()

    su_updated = 0
    for code, close, per_su in su_rows:
        # 가장 최근 분기 EPS/BPS
        rec = conn.execute("""
            SELECT eps, bps
            FROM financial_data
            WHERE stock_code=? AND is_annual=0
              AND eps IS NOT NULL AND eps > 0
            ORDER BY year DESC, quarter DESC
            LIMIT 1
        """, (code,)).fetchone()

        if not rec:
            # 연간 데이터라도 사용
            rec = conn.execute("""
                SELECT eps, bps
                FROM financial_data
                WHERE stock_code=? AND eps IS NOT NULL AND eps > 0
                ORDER BY year DESC, quarter DESC
                LIMIT 1
            """, (code,)).fetchone()

        if not rec:
            continue

        eps, bps = rec
        per = round(close / eps, 2) if eps and eps > 0 else per_su
        pbr = round(close / bps, 2) if bps and bps > 0 else None

        conn.execute("""
            UPDATE stock_universe
            SET eps=?, bps=?, per=?, pbr=?
            WHERE stock_code=?
        """, (eps, bps, per, pbr, code))
        su_updated += 1

    conn.commit()
    print(f"  → stock_universe EPS/BPS/PER/PBR 업데이트: {su_updated}개")


def update_from_fdr_fundamental(conn):
    """
    FinanceDataReader로 최근 주요 펀더멘털 정보 업데이트 시도.
    FDR이 PBR/PER 제공 가능한 경우 활용.
    """
    print("\n[5] FDR 펀더멘털 데이터 보완 시도")
    try:
        import FinanceDataReader as fdr

        # KRX 전종목 현재 데이터 (시가총액, 가격 포함)
        krx_all = fdr.StockListing('KRX')
        print(f"  KRX 종목 수: {len(krx_all)}")
        print(f"  컬럼: {krx_all.columns.tolist()}")

        # market_cap, Shares 업데이트
        updated = 0
        for _, r in krx_all.iterrows():
            code = str(r.get('Code', '')).zfill(6)
            marcap = r.get('Marcap')
            stocks = r.get('Stocks')
            close = r.get('Close')

            if not code or len(code) != 6:
                continue

            sets = []
            vals = []
            if marcap and marcap > 0:
                sets.append("market_cap=?")
                vals.append(float(marcap))
            if stocks and stocks > 0:
                sets.append("shares_issued=?")
                vals.append(float(stocks))
            if close and close > 0:
                sets.append("close=?")
                vals.append(float(close))

            if sets:
                vals.append(code)
                conn.execute(
                    f"UPDATE stock_universe SET {', '.join(sets)} WHERE stock_code=?",
                    vals
                )
                updated += 1

        conn.commit()
        print(f"  → market_cap/shares/close 업데이트: {updated}개")

    except Exception as e:
        print(f"  FDR 펀더멘털 로드 실패: {e}")


def fix_financial_data_screener_logic(conn):
    """
    signal_engine/screener 가 사용하는 재무 데이터 쿼리를 위해
    financial_data 데이터 품질 체크 및 요약 출력.
    """
    print("\n[6] 데이터 품질 최종 점검")

    # Q4 중복 잔여
    dup = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT stock_code, year FROM financial_data
            WHERE quarter=4 GROUP BY stock_code, year HAVING COUNT(*)>1
        )
    """).fetchone()[0]
    print(f"  Q4 잔여 중복: {dup}개")

    # is_annual 분포
    for ia, cnt in conn.execute("SELECT is_annual, COUNT(*) FROM financial_data GROUP BY is_annual"):
        label = "연간(annual)" if ia else "분기(quarterly)"
        print(f"  {label} (is_annual={ia}): {cnt}개")

    # EPS 있는 종목 수
    eps_cnt = conn.execute("""
        SELECT COUNT(DISTINCT stock_code) FROM financial_data
        WHERE eps IS NOT NULL AND eps > 0
    """).fetchone()[0]
    print(f"  EPS 있는 종목 수 (financial_data): {eps_cnt}개")

    # stock_universe EPS 현황
    su_eps = conn.execute(
        "SELECT COUNT(*) FROM stock_universe WHERE eps IS NOT NULL AND eps != 0"
    ).fetchone()[0]
    su_bps = conn.execute(
        "SELECT COUNT(*) FROM stock_universe WHERE bps IS NOT NULL AND bps != 0"
    ).fetchone()[0]
    su_pbr = conn.execute(
        "SELECT COUNT(*) FROM stock_universe WHERE pbr IS NOT NULL"
    ).fetchone()[0]
    su_sec = conn.execute(
        "SELECT COUNT(*) FROM stock_universe WHERE sector_large IS NOT NULL"
    ).fetchone()[0]
    total_su = conn.execute("SELECT COUNT(*) FROM stock_universe").fetchone()[0]
    print(f"  stock_universe ({total_su}개):")
    print(f"    EPS: {su_eps}개 ({su_eps/total_su*100:.1f}%)")
    print(f"    BPS: {su_bps}개 ({su_bps/total_su*100:.1f}%)")
    print(f"    PBR: {su_pbr}개 ({su_pbr/total_su*100:.1f}%)")
    print(f"    sector: {su_sec}개 ({su_sec/total_su*100:.1f}%)")

    # 음수 equity 잔여
    neg_eq = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE total_equity < 0"
    ).fetchone()[0]
    print(f"  음수 equity 잔여: {neg_eq}개 (완전자본잠식 포함)")


def update_pykrx_fundamental(conn):
    """
    pykrx로 EPS/BPS/PBR/PER 일괄 수집 시도.
    pykrx API가 정상이면 가장 빠름.
    """
    print("\n[+] pykrx 펀더멘털 수집 시도")
    try:
        from pykrx import stock as pkrx

        # 최근 영업일 후보
        dates_to_try = ['20260404', '20260403', '20260402', '20260401',
                        '20260331', '20260328', '20251231', '20251128']

        df = None
        used_date = None
        for d in dates_to_try:
            try:
                df_k = pkrx.get_market_fundamental_by_ticker(d, market='ALL')
                if df_k is not None and not df_k.empty and 'EPS' in df_k.columns:
                    df = df_k
                    used_date = d
                    print(f"  pykrx OK: {d}, {len(df)}개 종목")
                    break
            except Exception:
                continue

        if df is None:
            print("  pykrx 수집 실패 (API 응답 없음)")
            return

        updated = 0
        for ticker, row in df.iterrows():
            code = str(ticker).zfill(6)
            eps = float(row.get('EPS', 0) or 0)
            bps = float(row.get('BPS', 0) or 0)
            per = float(row.get('PER', 0) or 0)
            pbr = float(row.get('PBR', 0) or 0)

            if eps == 0 and bps == 0:
                continue

            conn.execute("""
                UPDATE stock_universe
                SET eps=COALESCE(NULLIF(?,0), eps),
                    bps=COALESCE(NULLIF(?,0), bps),
                    per=COALESCE(NULLIF(?,0), per),
                    pbr=COALESCE(NULLIF(?,0), pbr)
                WHERE stock_code=?
            """, (eps or None, bps or None, per or None, pbr or None, code))
            updated += 1

        conn.commit()
        print(f"  → pykrx EPS/BPS/PER/PBR 업데이트: {updated}개")

    except Exception as e:
        print(f"  pykrx 오류: {e}")


def main():
    print("=" * 60)
    print("데이터 품질 수정 및 보완 스크립트")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # 1. Q4 중복 수정 (가장 중요 - 스크리너 오작동 방지)
    fix_q4_duplicates(conn)

    # 2. 음수 equity 수정
    fix_negative_equity(conn)

    # 3. 섹터 정보 업데이트 (FDR)
    update_sector_from_fdr(conn)

    # 4. FDR으로 시가총액/주식수 최신화
    update_from_fdr_fundamental(conn)

    # 5. EPS/BPS 역산 (financial_data → stock_universe)
    calc_eps_bps_from_financial(conn)

    # 6. pykrx로 추가 보완 시도
    update_pykrx_fundamental(conn)

    # 최종 품질 점검
    fix_financial_data_screener_logic(conn)

    conn.close()
    print("\n" + "=" * 60)
    print("[완료] 데이터 수정 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
