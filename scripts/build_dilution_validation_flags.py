"""
scripts/build_dilution_validation_flags.py

dart_dilution_events_validation_flags 증분 업데이트(신규 rcept_no만).

플래그 기준 (2026-10-01 완화 적용):
  dilution_flag:
    OK      → dilution_ratio_pct IS NULL OR <= 100%
    INVALID → 100% < ratio <= 1000%  → overall=WARN
    INVALID → ratio > 1000%          → overall=FAIL
    EXTREME → (예비, 향후 확장)

  parser_quality:
    HIGH → confidence >= 0.7
    MED  → confidence >= 0.5
    LOW  → confidence < 0.5

  amount_flag:
    OK         → issue_amount_krw IS NOT NULL AND > 0
    MISSING    → issue_amount_krw IS NULL
    ZERO       → issue_amount_krw = 0

  overall_flag:
    FAIL → (dilution=INVALID AND ratio > 1000%) OR (dilution=NO_DATA AND quality=LOW)
    WARN → (dilution=INVALID AND ratio <= 1000%) OR quality != HIGH
    PASS → 그 외

실행:
  python3 scripts/build_dilution_validation_flags.py            # 증분(신규만)
  python3 scripts/build_dilution_validation_flags.py --full     # 전체 재빌드
  python3 scripts/build_dilution_validation_flags.py --dry-run  # dry-run
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "build_dilution_vflags_auto"
BATCH = 2000


def _dilution_flag(ratio_pct) -> str:
    if ratio_pct is None:
        return "NO_DATA"
    r = float(ratio_pct)
    if r > 1000:
        return "INVALID_EXTREME"
    if r > 100:
        return "INVALID"
    return "OK"


def _parser_quality(confidence) -> str:
    c = float(confidence or 0)
    if c >= 0.7:
        return "HIGH"
    if c >= 0.5:
        return "MED"
    return "LOW"


def _amount_flag(amount_krw) -> str:
    if amount_krw is None:
        return "MISSING"
    if float(amount_krw) == 0:
        return "ZERO"
    return "OK"


def _overall(df: str, pq: str) -> str:
    if df == "INVALID_EXTREME":
        return "FAIL"
    if df == "NO_DATA" and pq == "LOW":
        return "FAIL"
    if df in ("INVALID", "NO_DATA") or pq in ("MED", "LOW"):
        return "WARN"
    return "PASS"


def main(full: bool = False, dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_dilution_events_validation_flags (
            rcept_no        TEXT PRIMARY KEY,
            stock_code      TEXT,
            parser_quality  TEXT,
            dilution_flag   TEXT,
            amount_flag     TEXT,
            overall_flag    TEXT,
            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()

    if full:
        # 전체 재빌드: 모든 rcept_no 처리
        sql = """
            SELECT rcept_no, stock_code, dilution_ratio_pct, confidence, issue_amount_krw
            FROM dart_dilution_events
            ORDER BY rcept_dt DESC
        """
        mode = "전체 재빌드"
    else:
        # 증분: validation_flags에 없는 신규 행만
        sql = """
            SELECT de.rcept_no, de.stock_code, de.dilution_ratio_pct,
                   de.confidence, de.issue_amount_krw
            FROM dart_dilution_events de
            LEFT JOIN dart_dilution_events_validation_flags vf ON de.rcept_no = vf.rcept_no
            WHERE vf.rcept_no IS NULL
            ORDER BY de.rcept_dt DESC
        """
        mode = "증분(신규)"

    rows = conn.execute(sql).fetchall()
    total = len(rows)
    print(f"[{mode}] 처리 대상: {total:,}건")
    if total == 0:
        print("신규 없음, 종료")
        conn.close()
        return

    fail_cnt = warn_cnt = pass_cnt = 0
    batch_data = []
    for rcept_no, sc, ratio, conf, amount in rows:
        df = _dilution_flag(ratio)
        pq = _parser_quality(conf)
        af = _amount_flag(amount)
        overall = _overall(df, pq)

        if overall == "FAIL":
            fail_cnt += 1
        elif overall == "WARN":
            warn_cnt += 1
        else:
            pass_cnt += 1

        batch_data.append((rcept_no, sc, pq, df, af, overall))

    if not dry_run:
        for i in range(0, len(batch_data), BATCH):
            chunk = batch_data[i:i + BATCH]
            for row in chunk:
                conn.execute("""
                    INSERT INTO dart_dilution_events_validation_flags
                        (rcept_no, stock_code, parser_quality, dilution_flag, amount_flag, overall_flag)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (rcept_no) DO UPDATE SET
                        parser_quality = EXCLUDED.parser_quality,
                        dilution_flag  = EXCLUDED.dilution_flag,
                        amount_flag    = EXCLUDED.amount_flag,
                        overall_flag   = EXCLUDED.overall_flag,
                        updated_at     = NOW()
                """, row)
            conn.commit()
            print(f"  {min(i + BATCH, total):,}/{total:,} 처리됨")

        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            "dart_dilution_events_validation_flags", mode, total,
            "ratio>1000%→FAIL, 100~1000%→WARN(CB/BW정상), NO_DATA+LOW→FAIL",
            f"처리전",
            f"FAIL={fail_cnt}, WARN={warn_cnt}, PASS={pass_cnt}",
            "build_script", RUN_ID,
        ))
        conn.commit()

    print(f"완료: {total:,}건 | FAIL={fail_cnt:,} WARN={warn_cnt:,} PASS={pass_cnt:,}")
    conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="전체 재빌드 (기본: 증분)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN]")
    main(full=args.full, dry_run=args.dry_run)
