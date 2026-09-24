"""
2026-09-08 신규: llm_hs_mapping_candidates 스테이징 테이블에서 안전 기준을 통과한
후보만 hs_code_company_map에 provisional로 승격한다.

승격 기준(전부 충족):
  1. is_sector_plausible: 증권/은행/보험/지주/홀딩스 등 물리적 상품을 만들 수
     없는 회사가 아님
  2. confidence >= --min-confidence(기본 0.8)
  3. has_hedge_language가 아님(reason에 "억지로 끼워맞춘" 티가 나는 회피성 표현 없음)
  4. mentions_subsidiary가 아님(reason이 실제로는 자회사/계열사 사업을 근거로 든
     경우 배제 — SK/GS/한화/효성/SK스퀘어처럼 이름에 "지주"가 없는 그룹 대표
     종목이 자회사 실적을 자기 것처럼 매칭당하는 사례 다수 확인)
  5. 이미 hs_code_company_map에 존재하는 (hs_code, stock_code) 쌍이 아님

기본은 --dry-run(무엇이 승격될지 목록만 출력) — 실제 반영은 --commit 필요.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_compat import connect_primary_db  # noqa: E402
from llm_hs_matcher import (  # noqa: E402
    HS_DB_PATH, has_hedge_language, is_sector_plausible, mentions_subsidiary,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-confidence", type=float, default=0.8)
    ap.add_argument("--commit", action="store_true", help="실제 hs_code_company_map에 반영")
    ap.add_argument("--source", default=None, help="특정 소스만(analyst_pdf/dart_filing)")
    args = ap.parse_args()

    hs_conn = sqlite3.connect(HS_DB_PATH, timeout=10)
    main_conn = connect_primary_db(timeout=30)

    where = "confidence >= ?"
    params = [args.min_confidence]
    if args.source:
        where += " AND source = ?"
        params.append(args.source)

    rows = hs_conn.execute(
        f"SELECT stock_code, stock_name, hs_code, confidence, reason, source, source_ref "
        f"FROM llm_hs_mapping_candidates WHERE {where}",
        params,
    ).fetchall()

    existing = {r[0] for r in hs_conn.execute("SELECT hs_code||'|'||stock_code FROM hs_code_company_map").fetchall()}
    existing = set(x[0] for x in [(r,) for r in existing])  # already a set of str

    # 2026-09-08 발견: report_files에 중국 상해거래소 상장사(중천과기, 600522)처럼
    # 우리 거래 대상(코스피/코스닥)이 아닌 종목코드가 섞여 있어, sector_large가
    # 아예 NULL이라 is_sector_plausible을 그냥 통과해버림 — stock_universe에 실제
    # 존재하는 국내 상장사인지 자체를 확인하는 필터를 추가한다.
    kr_universe = {
        r[0] for r in main_conn.execute(
            "SELECT stock_code FROM stock_universe WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')"
        ).fetchall()
    }

    plausible_cache: dict[str, bool] = {}
    promote, rejected_sector, rejected_hedge, rejected_sub, rejected_dup, rejected_foreign = [], [], [], [], [], []
    for sc, name, hs, conf, reason, source, source_ref in rows:
        key = f"{hs}|{sc}"
        if sc not in kr_universe:
            rejected_foreign.append((sc, name, hs, reason))
            continue
        if key in existing:
            rejected_dup.append((sc, name, hs))
            continue
        if sc not in plausible_cache:
            plausible_cache[sc] = is_sector_plausible(sc, main_conn)
        if not plausible_cache[sc]:
            rejected_sector.append((sc, name, hs, reason))
            continue
        if has_hedge_language(reason):
            rejected_hedge.append((sc, name, hs, reason))
            continue
        if mentions_subsidiary(reason, name):
            rejected_sub.append((sc, name, hs, reason))
            continue
        promote.append((sc, name, hs, conf, reason, source, source_ref))

    print(f"승격 대상: {len(promote)}건 / 섹터배제: {len(rejected_sector)}건 / "
          f"회피성표현배제: {len(rejected_hedge)}건 / 자회사귀속의심배제: {len(rejected_sub)}건 / "
          f"국내상장사아님배제: {len(rejected_foreign)}건 / 이미매핑됨: {len(rejected_dup)}건")
    print()
    print("=== 국내 상장사 아님 배제 ===")
    for sc, name, hs, reason in rejected_foreign:
        print(f"  {sc} {name} -> {hs} : {reason}")
    print()
    print("=== 섹터 배제 (지주/금융 등) ===")
    for sc, name, hs, reason in rejected_sector:
        print(f"  {sc} {name} -> {hs} : {reason}")
    print()
    print("=== 자회사 귀속 의심 배제 ===")
    for sc, name, hs, reason in rejected_sub:
        print(f"  {sc} {name} -> {hs} : {reason}")
    print()
    print("=== 회피성 표현 배제 (강제매칭 의심) ===")
    for sc, name, hs, reason in rejected_hedge:
        print(f"  {sc} {name} -> {hs} : {reason}")
    print()
    print("=== 승격 대상 목록 ===")
    for sc, name, hs, conf, reason, source, source_ref in promote:
        print(f"  {sc} {name} -> {hs} (conf={conf}, {source}) : {reason}")

    if args.commit:
        hs_names = dict(hs_conn.execute("SELECT hs_code, description FROM hs_codes").fetchall())
        n = 0
        for sc, name, hs, conf, reason, source, source_ref in promote:
            hs_conn.execute(
                """
                INSERT INTO hs_code_company_map
                    (hs_code, hs_name, stock_code, stock_name, sector_name, match_type,
                     confidence, mapping_status, note, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
                ON CONFLICT(hs_code, stock_code) DO NOTHING
                """,
                (hs, hs_names.get(hs, ""), sc, name, source, f"llm_{source}", conf, "provisional",
                 f"LLM({source}) 매칭 2026-09-08: {reason} [{source_ref}]"),
            )
            n += 1
        hs_conn.commit()
        print(f"\n실제 반영 완료: {n}건")
    else:
        print("\n(--dry-run 모드 — 실제 반영하려면 --commit 추가)")

    hs_conn.close()
    main_conn.close()


if __name__ == "__main__":
    main()
