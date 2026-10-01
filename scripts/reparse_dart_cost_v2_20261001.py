"""
scripts/reparse_dart_cost_v2_20261001.py

dart_cost_quarterly의 parser_version='cost_v1' 행을 cost_v2 파서로 재파싱

- 체크포인트 자동 지원: 재실행 시 이미 cost_v2로 업데이트된 행은 쿼리에서 제외
- DART API 키 로테이션 (_fetch_document_with_key_rotation 사용)
- 46,532건 대상, API 속도에 따라 6~10시간 소요 예상

실행:
  python3 scripts/reparse_dart_cost_v2_20261001.py
  python3 scripts/reparse_dart_cost_v2_20261001.py --limit 1000  # 테스트
  python3 scripts/reparse_dart_cost_v2_20261001.py --dep-null-only  # NULL 우선
"""
from __future__ import annotations
import sys
import time
import hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db
from collectors.dart_cost_collector import _extract_cost_metrics, PARSER_VERSION
from collectors.dart_backlog_collector import _fetch_document_with_key_rotation

BATCH_COMMIT = 100
SLEEP_PER_CALL = 0.3  # DART API 부하 조절 (초)


def _load_targets(conn, dep_null_only: bool, limit: int) -> list:
    base_filter = "parser_version = 'cost_v1' AND source_rcept_no IS NOT NULL"
    if dep_null_only:
        base_filter += " AND depreciation_krw IS NULL"

    sql = f"""
        SELECT source_rcept_no, stock_code, fiscal_year, fiscal_quarter,
               report_type, source_report_nm, source_rcept_dt, depreciation_krw
        FROM dart_cost_quarterly
        WHERE {base_filter}
        ORDER BY
            CASE WHEN depreciation_krw IS NULL THEN 0 ELSE 1 END,
            fiscal_year DESC,
            stock_code
    """
    if limit:
        sql += f" LIMIT {limit}"

    return conn.execute(sql).fetchall()


def main(limit: int = 0, dep_null_only: bool = False) -> None:
    conn = connect_primary_db(timeout=600)
    conn.execute("SET statement_timeout = '30s'")

    print("재파싱 대상 로드 중...")
    targets = _load_targets(conn, dep_null_only, limit)
    total = len(targets)
    print(f"  대상: {total:,}건 (cost_v1{'+ dep_null' if dep_null_only else ''})")

    ok = no_text = no_metric = restored = errs = 0
    start = time.time()

    for i, row in enumerate(targets, start=1):
        rcept_no = row[0]
        sc = row[1]
        fy = int(row[2])
        fq = int(row[3])
        rt = row[4]
        rnm = row[5]
        rdt = row[6]
        old_dep = row[7]

        try:
            raw = _fetch_document_with_key_rotation(rcept_no)
            if not raw:
                no_text += 1
                # 텍스트 없음 → parser_version만 갱신해서 재시도 방지
                conn.execute(
                    "UPDATE dart_cost_quarterly SET parser_version=%s WHERE stock_code=%s AND fiscal_year=%s AND fiscal_quarter=%s AND report_type=%s",
                    ("cost_v2_no_text", sc, fy, fq, rt),
                )
                if i % BATCH_COMMIT == 0:
                    conn.commit()
                continue

            m = _extract_cost_metrics(raw)
            text_hash = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

            if old_dep is None and m.depreciation_krw is not None:
                restored += 1

            conn.execute("""
                UPDATE dart_cost_quarterly SET
                    material_cost_krw   = %s,
                    inventory_assets_krw= %s,
                    depreciation_krw    = %s,
                    confidence          = %s,
                    source_excerpt      = %s,
                    source_text_hash    = %s,
                    parser_version      = %s,
                    updated_at          = NOW()
                WHERE stock_code=%s AND fiscal_year=%s AND fiscal_quarter=%s AND report_type=%s
            """, (
                m.material_cost_krw, m.inventory_assets_krw, m.depreciation_krw,
                m.confidence, m.excerpt, text_hash, PARSER_VERSION,
                sc, fy, fq, rt,
            ))

            if m.material_cost_krw is not None or m.depreciation_krw is not None:
                # tenbagger 재계산 — 인라인 import (순환 import 방지)
                try:
                    from collectors.dart_cost_collector import _upsert_trigger_rows
                    _upsert_trigger_rows(conn, sc, fy, fq, rt)
                except Exception:
                    pass

            ok += 1

        except Exception as e:
            errs += 1
            if errs <= 10:
                print(f"  [ERR] {sc} {rcept_no}: {e}")

        if i % BATCH_COMMIT == 0:
            conn.commit()

        if i % 500 == 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate if rate > 0 else 0
            print(f"  [{i:,}/{total:,}] ok={ok} restored={restored} no_text={no_text} err={errs} "
                  f"| {rate:.1f}건/s, ETA={eta/60:.0f}분")

        time.sleep(SLEEP_PER_CALL)

    conn.commit()

    elapsed = time.time() - start
    print(f"\n=== 완료 ({elapsed/60:.1f}분) ===")
    print(f"  전체: {total:,}건")
    print(f"  성공: {ok:,}건")
    print(f"  복원(dep NULL→값): {restored:,}건")
    print(f"  텍스트 없음: {no_text:,}건")
    print(f"  오류: {errs:,}건")

    # 최종 상태 확인
    r = conn.execute("SELECT COUNT(*) FROM dart_cost_quarterly WHERE parser_version='cost_v1'").fetchone()
    print(f"  남은 cost_v1: {r[0]:,}건")
    r = conn.execute("SELECT COUNT(*) FROM dart_cost_quarterly WHERE depreciation_krw IS NULL AND parser_version='cost_v2'").fetchone()
    print(f"  cost_v2 + dep NULL (복원 불가): {r[0]:,}건")

    conn.close()
    print("완료.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="테스트용 건수 제한 (0=전체)")
    ap.add_argument("--dep-null-only", action="store_true", help="depreciation NULL인 행만 우선 처리")
    args = ap.parse_args()
    main(limit=args.limit, dep_null_only=args.dep_null_only)
