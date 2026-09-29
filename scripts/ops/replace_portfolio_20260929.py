"""
scripts/ops/replace_portfolio_20260929.py

2026-09-29 사용자 지시("이미지의 10종목이 계좌현황 전체 보유종목, 나머지는 삭제") — 사용자가 캡처한
HTS 화면(10종목, 2개 소계 그룹 572,194,343/102,306,727 + 6,907,500/-745,350로 검산 완료)으로
portfolio 테이블을 완전히 교체한다.

안전장치: 삭제 전 전체를 backups/portfolio_backup_20260929.csv로 백업(되돌릴 수 있게).

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/replace_portfolio_20260929.py
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone, timedelta

from db_compat import connect_primary_db

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

# (stock_code, stock_name, quantity, avg_price) — 이미지 매입총액/수량으로 검산된 정확값
HOLDINGS = [
    ("172670", "에이엘티",     47591, round(552554843 / 47591, 4)),
    ("451220", "아이엠티",     500,   12930.0),
    ("039440", "에스티아이",   300,   22650.0),
    ("425420", "티에프이",     100,   42550.0),
    ("101160", "월덱스",       70,    30350.0),
    ("398120", "에스지헬스케어", 2694, 3508.0),
    ("494120", "큐리오시스",   223,   11625.0),
    ("491000", "리브스메드",   40,    58445.0),
    ("229640", "LS에코에너지", 30,    76800.0),
    ("095570", "AJ네트웍스",   990,   4650.0),
]


def main() -> None:
    conn = connect_primary_db(timeout=30)
    try:
        # 1) 백업
        rows = conn.execute("SELECT * FROM portfolio ORDER BY id").fetchall()
        backup_path = "backups/portfolio_backup_20260929.csv"
        if rows:
            cols = list(dict(rows[0]).keys())
            with open(backup_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for r in rows:
                    w.writerow([dict(r)[c] for c in cols])
        print(f"백업 완료: {len(rows)}행 → {backup_path}")

        # 2) 전체 삭제
        conn.execute("DELETE FROM portfolio")

        # 3) 신규 10종목 삽입
        for i, (code, name, qty, price) in enumerate(HOLDINGS, start=1):
            conn.execute(
                """INSERT INTO portfolio (id, stock_code, stock_name, quantity, avg_price, created_at, updated_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'manual_image_20260929')""",
                (i, code, name, qty, price, NOW, NOW),
            )
        conn.commit()

        check = conn.execute("SELECT stock_code, stock_name, quantity, avg_price FROM portfolio ORDER BY id").fetchall()
        print(f"교체 완료: {len(check)}종목")
        for r in check:
            print(dict(r))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
