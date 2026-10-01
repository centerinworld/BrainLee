"""
scripts/build_backlog_validation_flags.py

order_backlog_validation_flags 전체 재빌드(UPSERT).

플래그 기준:
  rev_ratio_flag:
    NO_BACKLOG   → backlog_amount IS NULL
    NO_REV_DATA  → backlog_to_rev IS NULL (revenue 없음)
    EXTREME_RATIO→ backlog_to_rev > 20  (FAIL)
    HIGH_RATIO   → backlog_to_rev > 5   (WARN)
    OK           → 그 외

  yoy_flag:
    EXTREME_INCREASE → yoy_pct > 500%
    EXTREME_DECREASE → yoy_pct < -90%
    OK               → 그 외

  overall_flag:
    FAIL → rev_ratio_flag = EXTREME_RATIO
    WARN → rev_ratio_flag IN (HIGH_RATIO, NO_REV_DATA)
           OR yoy_flag IN (EXTREME_INCREASE, EXTREME_DECREASE)
    PASS → 그 외 (NO_BACKLOG 포함)

실행:
  python3 scripts/build_backlog_validation_flags.py
  python3 scripts/build_backlog_validation_flags.py --dry-run
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "build_backlog_vflags_auto"
BATCH = 5000


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_backlog_validation_flags (
            stock_code  TEXT NOT NULL,
            year        INTEGER NOT NULL,
            quarter     INTEGER NOT NULL,
            rev_ratio_flag TEXT,
            yoy_pct     REAL,
            yoy_flag    TEXT,
            overall_flag TEXT,
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (stock_code, year, quarter)
        )
    """)
    conn.commit()

    # 전년 동기 backlog_to_rev 맵
    prev_map: dict = {}
    for r in conn.execute("""
        SELECT stock_code, year, quarter, backlog_to_rev
        FROM order_backlog
        WHERE backlog_to_rev IS NOT NULL
    """).fetchall():
        prev_map[(r[0], r[1], r[2])] = float(r[3])

    total = conn.execute("SELECT COUNT(*) FROM order_backlog").fetchone()[0]
    print(f"처리 대상: {total:,}건")

    offset = 0
    processed = 0
    fail_cnt = warn_cnt = pass_cnt = 0

    while True:
        rows = conn.execute("""
            SELECT stock_code, year, quarter, backlog_amount, backlog_to_rev
            FROM order_backlog
            ORDER BY stock_code, year, quarter
            LIMIT %s OFFSET %s
        """, (BATCH, offset)).fetchall()
        if not rows:
            break

        batch_data = []
        for sc, yr, qt, bamt, brev in rows:
            # rev_ratio_flag
            if bamt is None:
                rf = "NO_BACKLOG"
            elif brev is None:
                rf = "NO_REV_DATA"
            elif float(brev) > 20:
                rf = "EXTREME_RATIO"
            elif float(brev) > 5:
                rf = "HIGH_RATIO"
            else:
                rf = "OK"

            # yoy (전년 동기 비교)
            prev_yr_brev = prev_map.get((sc, yr - 1, qt))
            yoy_pct = None
            yf = "OK"
            if brev is not None and prev_yr_brev is not None and prev_yr_brev != 0:
                yoy_pct = (float(brev) - prev_yr_brev) / abs(prev_yr_brev) * 100
                if yoy_pct > 500:
                    yf = "EXTREME_INCREASE"
                elif yoy_pct < -90:
                    yf = "EXTREME_DECREASE"

            # overall
            if rf == "EXTREME_RATIO":
                overall = "FAIL"
            elif rf in ("HIGH_RATIO", "NO_REV_DATA") or yf in ("EXTREME_INCREASE", "EXTREME_DECREASE"):
                overall = "WARN"
            else:
                overall = "PASS"

            if overall == "FAIL":
                fail_cnt += 1
            elif overall == "WARN":
                warn_cnt += 1
            else:
                pass_cnt += 1

            batch_data.append((sc, yr, qt, rf, yoy_pct, yf, overall))

        if not dry_run:
            for row in batch_data:
                conn.execute("""
                    INSERT INTO order_backlog_validation_flags
                        (stock_code, year, quarter, rev_ratio_flag, yoy_pct, yoy_flag, overall_flag)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (stock_code, year, quarter) DO UPDATE SET
                        rev_ratio_flag = EXCLUDED.rev_ratio_flag,
                        yoy_pct        = EXCLUDED.yoy_pct,
                        yoy_flag       = EXCLUDED.yoy_flag,
                        overall_flag   = EXCLUDED.overall_flag,
                        updated_at     = NOW()
                """, row)
            conn.commit()

        processed += len(rows)
        offset += BATCH

    print(f"완료: {processed:,}건 | FAIL={fail_cnt:,} WARN={warn_cnt:,} PASS={pass_cnt:,}")

    if not dry_run:
        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "order_backlog_validation_flags", "전체 재빌드", processed,
            "backlog_to_rev>20→FAIL, >5→WARN, yoy±극단→WARN",
            "재빌드 전",
            f"FAIL={fail_cnt}, WARN={warn_cnt}, PASS={pass_cnt}",
            "build_script", RUN_ID,
        ))
        conn.commit()

    conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN]")
    main(dry_run=args.dry_run)
