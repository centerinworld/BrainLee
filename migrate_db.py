"""
migrate_db.py — 기존 DB를 안전하게 마이그레이션합니다.

★ 서버 실행 전에 반드시 1회 실행하세요 ★

실행 방법:
    cd /Applications/stock_dashboard
    source venv/bin/activate
    python3 migrate_db.py

수행 작업:
    1. financial_data 테이블에 roe 컬럼 추가 (없을 때만)
    2. price_history 테이블에 UNIQUE 인덱스 추가 (없을 때만)
    3. 오염 데이터 정리 (year < 2000 또는 quarter NOT IN (0,1,2,3,4))
    4. 기존 정상 데이터는 절대 삭제하지 않음
"""

import sqlite3
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "stock.db"


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def index_exists(cursor, index_name: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,),
    )
    return cursor.fetchone() is not None


def table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def migrate():
    print("=" * 55)
    print("DB 마이그레이션 시작")
    print(f"DB 경로: {DB_PATH}")
    print("=" * 55)

    if not DB_PATH.exists():
        print("\n[오류] stock.db 파일이 없습니다.")
        print("  → 서버를 한 번 실행하여 DB를 먼저 생성하세요.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── 1. financial_data 컬럼 추가 ──────────────────────────
    if table_exists(cur, "financial_data"):
        if not column_exists(cur, "financial_data", "roe"):
            cur.execute("ALTER TABLE financial_data ADD COLUMN roe FLOAT")
            conn.commit()
            print("[완료] financial_data.roe 컬럼 추가")
        else:
            print("[스킵] financial_data.roe 이미 존재")
        if not column_exists(cur, "financial_data", "data_source"):
            cur.execute("ALTER TABLE financial_data ADD COLUMN data_source TEXT")
            conn.commit()
            print("[완료] financial_data.data_source 컬럼 추가")
        else:
            print("[스킵] financial_data.data_source 이미 존재")

        # ── 2. 오염 데이터 정리 ───────────────────────────────
        # year=1, quarter=0 같은 비정상 데이터가 e3q8 에러를 유발함
        # year < 2000: DART 공시는 최소 2000년 이후만 존재
        # quarter NOT IN (0,1,2,3,4): 유효하지 않은 분기값
        print("\n  오염 데이터 확인 중...")

        cur.execute("""
            SELECT COUNT(*) FROM financial_data
            WHERE year < 2000 OR year > 2030
               OR quarter NOT IN (0, 1, 2, 3, 4)
        """)
        bad_count = cur.fetchone()[0]

        if bad_count > 0:
            print(f"  오염 레코드 {bad_count}건 발견 → 삭제 진행")
            cur.execute("""
                DELETE FROM financial_data
                WHERE year < 2000 OR year > 2030
                   OR quarter NOT IN (0, 1, 2, 3, 4)
            """)
            conn.commit()
            print(f"[완료] 오염 데이터 {bad_count}건 삭제 완료")
        else:
            print("[스킵] 오염 데이터 없음")

        # ── 3. is_annual=True인데 quarter=0인 레코드 → quarter=4로 정규화
        cur.execute("""
            SELECT COUNT(*) FROM financial_data
            WHERE is_annual = 1 AND quarter = 0
        """)
        q0_annual = cur.fetchone()[0]
        if q0_annual > 0:
            cur.execute("""
                UPDATE financial_data SET quarter = 4
                WHERE is_annual = 1 AND quarter = 0
            """)
            conn.commit()
            print(f"[완료] 연간 데이터 quarter=0 → quarter=4 정규화 ({q0_annual}건)")
        else:
            print("[스킵] quarter=0 연간 데이터 없음")
    else:
        print("[경고] financial_data 테이블이 없습니다.")

    # ── 4. price_history UNIQUE 인덱스 추가 ───────────────────
    if table_exists(cur, "price_history"):
        idx_name = "uq_price_history_code_date"
        if not index_exists(cur, idx_name):
            print("\n  price_history 중복 데이터 정리 중...")
            cur.execute("""
                DELETE FROM price_history
                WHERE id NOT IN (
                    SELECT MIN(id) FROM price_history
                    GROUP BY stock_code, date
                )
            """)
            removed = cur.rowcount
            if removed > 0:
                print(f"  중복 레코드 {removed}건 제거")
            conn.commit()

            try:
                cur.execute(
                    f"CREATE UNIQUE INDEX {idx_name} ON price_history (stock_code, date)"
                )
                conn.commit()
                print("[완료] price_history UNIQUE 인덱스 추가")
            except sqlite3.Error as e:
                print(f"[경고] UNIQUE 인덱스 추가 실패: {e}")
        else:
            print("[스킵] price_history UNIQUE 인덱스 이미 존재")
    else:
        print("[경고] price_history 테이블이 없습니다.")

    conn.close()
    print("\n" + "=" * 55)
    print("마이그레이션 완료! 이제 서버를 실행하세요:")
    print("  python3 run_server.py")
    print("=" * 55)


if __name__ == "__main__":
    migrate()
