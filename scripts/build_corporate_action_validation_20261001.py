"""
scripts/build_corporate_action_validation_20261001.py

corporate_action_events_validation_flags 테이블 생성 + 전체 채우기

검사 항목:
  1. ratio_flag
     - share_ratio IS NULL AND factor_confirmed       → NULL_RATIO (FAIL)
     - share_ratio > 1000                             → EXTREME_RATIO_FAIL (FAIL)
     - share_ratio > 100                              → EXTREME_RATIO_WARN (WARN)
     - OK                                             → OK
  2. price_flag (external_price_verification JOIN)
     - agreement_class = 'three_way_disagreement'
       AND adjustment_status = 'factor_confirmed'     → PRICE_DISAGREE (WARN)
     - NO_EXT_DATA                                    → NO_EXT_DATA
     - OK                                             → OK
  3. conf_flag
     - confidence >= 0.7  → HIGH
     - confidence >= 0.5  → MED
     - else               → LOW (WARN)
  4. overall_flag
     - FAIL: NULL_RATIO, EXTREME_RATIO_FAIL
     - WARN: EXTREME_RATIO_WARN, PRICE_DISAGREE, conf_flag=LOW
     - PASS: 그 외

run_id: build_ca_vflags_20261001
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "build_ca_vflags_20261001"
BATCH = 2000


def _create_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corporate_action_events_validation_flags (
            event_id        BIGINT PRIMARY KEY,
            stock_code      TEXT,
            event_date      TEXT,
            event_type      TEXT,

            ratio_flag      TEXT,   -- OK / EXTREME_RATIO_WARN / EXTREME_RATIO_FAIL / NULL_RATIO
            price_flag      TEXT,   -- OK / PRICE_DISAGREE / NO_EXT_DATA
            conf_flag       TEXT,   -- HIGH / MED / LOW

            overall_flag    TEXT,   -- PASS / WARN / FAIL

            created_at      TIMESTAMP DEFAULT NOW(),
            updated_at      TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ca_vflags_overall
        ON corporate_action_events_validation_flags(overall_flag)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ca_vflags_stock
        ON corporate_action_events_validation_flags(stock_code)
    """)
    conn.commit()


def _ratio_flag(share_ratio, adjustment_status: str, bpf) -> str:
    if share_ratio is None:
        if adjustment_status == "factor_confirmed":
            if bpf is None:
                return "NULL_RATIO_NO_BPF"   # BPF도 없음 → 진짜 문제 (FAIL)
            return "NULL_RATIO_BPF_OK"       # BPF는 가격 역산으로 있음 → WARN
        return "OK"
    r = float(share_ratio)
    if r > 1000:
        if adjustment_status == "factor_confirmed":
            return "EXTREME_RATIO_FAIL"      # 가격 조정에 사용 + 극단값 → FAIL
        return "EXTREME_RATIO_WARN"          # not_price_adjusting → WARN
    if r > 100:
        return "EXTREME_RATIO_WARN"
    return "OK"


def _conf_flag(confidence) -> str:
    c = float(confidence or 0)
    if c >= 0.7:
        return "HIGH"
    elif c >= 0.5:
        return "MED"
    return "LOW"


def _overall(rf: str, pf: str, cf: str) -> str:
    if rf in ("NULL_RATIO_NO_BPF", "EXTREME_RATIO_FAIL"):
        return "FAIL"
    if rf in ("EXTREME_RATIO_WARN", "NULL_RATIO_BPF_OK") or pf == "PRICE_DISAGREE" or cf == "LOW":
        return "WARN"
    return "PASS"


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    _create_table(conn)
    print("테이블 준비 완료")

    # external_price_verification disagree 맵 로드
    print("external_price_verification 로드 중...")
    disagree_set: set[tuple] = set()
    for r in conn.execute("""
        SELECT stock_code, event_date
        FROM external_price_verification
        WHERE agreement_class = 'three_way_disagreement'
    """).fetchall():
        disagree_set.add((r[0], r[1]))

    print(f"  price disagreement: {len(disagree_set):,}건")

    # corporate_action_events 전체 처리
    total = conn.execute("SELECT COUNT(*) FROM corporate_action_events").fetchone()[0]
    print(f"처리 대상: {total:,}건")

    offset = 0
    processed = 0
    fail_cnt = warn_cnt = pass_cnt = 0

    while True:
        rows = conn.execute("""
            SELECT id, stock_code, event_date, event_type,
                   share_ratio, adjustment_status, confidence, backward_price_factor
            FROM corporate_action_events
            ORDER BY id
            LIMIT %s OFFSET %s
        """, (BATCH, offset)).fetchall()

        if not rows:
            break

        batch_data = []
        for eid, sc, edate, etype, ratio, status, conf, bpf in rows:
            rf = _ratio_flag(ratio, status or "", bpf)
            cf = _conf_flag(conf)

            # price flag: factor_confirmed + disagree 체크
            if status == "factor_confirmed":
                if (sc, edate) in disagree_set:
                    pf = "PRICE_DISAGREE"
                else:
                    pf = "OK"
            else:
                if (sc, edate) in disagree_set:
                    pf = "PRICE_DISAGREE"
                else:
                    pf = "NO_EXT_DATA"

            overall = _overall(rf, pf, cf)
            if overall == "FAIL":
                fail_cnt += 1
            elif overall == "WARN":
                warn_cnt += 1
            else:
                pass_cnt += 1

            batch_data.append((eid, sc, edate, etype, rf, pf, cf, overall))

        if not dry_run:
            for row in batch_data:
                conn.execute("""
                    INSERT INTO corporate_action_events_validation_flags
                        (event_id, stock_code, event_date, event_type,
                         ratio_flag, price_flag, conf_flag, overall_flag)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        ratio_flag   = EXCLUDED.ratio_flag,
                        price_flag   = EXCLUDED.price_flag,
                        conf_flag    = EXCLUDED.conf_flag,
                        overall_flag = EXCLUDED.overall_flag,
                        updated_at   = NOW()
                """, row)
            conn.commit()

        processed += len(rows)
        offset += BATCH
        print(f"  진행: {processed:,}/{total:,} (FAIL={fail_cnt}, WARN={warn_cnt}, PASS={pass_cnt})")

    print(f"\n=== 최종 결과 ===")
    print(f"전체: {processed:,}건")
    print(f"  PASS: {pass_cnt:,}건")
    print(f"  WARN: {warn_cnt:,}건")
    print(f"  FAIL: {fail_cnt:,}건")

    if not dry_run:
        dist = conn.execute("""
            SELECT ratio_flag, price_flag, conf_flag, overall_flag, COUNT(*)
            FROM corporate_action_events_validation_flags
            GROUP BY ratio_flag, price_flag, conf_flag, overall_flag
            ORDER BY overall_flag, COUNT(*) DESC
        """).fetchall()
        print("\n=== DB 분포 ===")
        for r in dist:
            print(f"  overall={r[3]:5s} ratio={str(r[0]):22s} price={str(r[1]):15s} conf={r[2]:4s} → {r[4]:,}")

        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            SELECT %s, %s, %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (SELECT 1 FROM data_fix_log WHERE run_id=%s)
        """, (
            "corporate_action_events_validation_flags",
            "신규 테이블 생성 + 전체 채우기",
            processed,
            "ratio 이상값 / 가격 불일치 / 파서 신뢰도",
            "없음(신규)",
            f"PASS={pass_cnt}, WARN={warn_cnt}, FAIL={fail_cnt}",
            "build_script",
            RUN_ID,
            RUN_ID,
        ))
        conn.commit()
        print(f"\ndata_fix_log 기록 완료 (run_id: {RUN_ID})")

    conn.close()
    print("완료.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN]")
    main(dry_run=args.dry_run)
