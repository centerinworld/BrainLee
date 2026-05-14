"""
fill_hs_prefixes.py - A케이스(hs_prefixes_json 없는 580건)에 올바른 HS 코드를 연결하고
customs_monthly_record에서 confirmed 값을 채운다.

19개 고유 base product → HS prefix 수동 매핑 (customs HS 코드명 기반으로 검증된 코드들)
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"

# 제품명 키워드 → HS prefix 리스트 매핑
# 길고 구체적인 것을 먼저 배치 (매칭 우선순위)
PRODUCT_HS_RULES: list[tuple[list[str], list[str], str]] = [
    # (포함 키워드 목록, HS prefix 목록, 설명)
    # -- 복합 케이스 먼저 --
    (["디스컴", "범핑"], ["848620", "848640"], "디스컴(세척장비) + 웨이퍼 범핑"),
    (["습식 식각", "PR박리"], ["8486207000", "8486208420"], "습식식각/세척장비 + PR박리"),
    (["습식 식각", "세척", "PR박리"], ["8486207000", "8486208420"], "습식식각+PR박리"),
    # -- 단일 제품 --
    (["TC BONDER"], ["8486402010"], "TC Bonder (열압착 접합기)"),
    (["TC Bond"], ["8486402010"], "TC Bonder 변형"),
    (["전자집적회로", "디램"], ["854231", "8542321010"], "시스템반도체 + DRAM"),
    (["디램"], ["8542321010"], "DRAM 전자집적회로"),
    (["DRAM"], ["8542321010"], "DRAM"),
    (["블랭크마스크"], ["3701304000", "7006002000"],
     "블랭크마스크 (평판디스플레이용 + 반도체용)"),
    (["리드프레임"], ["8534002000"],
     "전자집적회로 리드프레임 (테이프형/리드프레임 기능 회로)"),
    (["과산화수소"], ["2847002000"],
     "과산화수소 (반도체 제조용)"),
    (["패턴결함", "오버레이"], ["9031809070", "9031809091", "9031491000", "9031494010"],
     "반도체 패턴결함 검사장비 + 오버레이 계측기"),
    (["패턴결함"], ["9031809070", "9031809091"],
     "반도체 패턴결함 검사장비"),
    (["오버레이계측"], ["9031491000", "9031494010"],
     "반도체 오버레이 계측기"),
    (["디스컴"], ["848620"],
     "반도체 웨이퍼 세척 장비 (디스컴)"),
    (["웨이퍼 세척"], ["848620"],
     "반도체 웨이퍼 세척 장비"),
    (["습식 식각"], ["8486207000"],
     "반도체 웨이퍼 습식 식각/세척 기계"),
    (["FPD", "이송"], ["8486403010", "8486403020"],
     "FPD 및 반도체 이송장치"),
    (["이송장치"], ["8486403010", "8486403020"],
     "반도체 이송·핸들링 장치"),
    (["이송 장치"], ["8486403010", "8486403020"],
     "반도체 이송·핸들링 장치"),
    (["측정, 검사"], ["9031419000", "9031499000"],
     "반도체 웨이퍼 소자 측정·검사 장비"),
    (["레이저마커"], ["8456119000"],
     "레이저마커 기기"),
    (["레이저 장비"], ["8456119000"],
     "반도체 레이저 장비"),
    (["증착장비"], ["8486204000"],
     "반도체 웨이퍼 증착장비 (CVD/PVD)"),
    (["증착 장비"], ["8486204000"],
     "반도체 웨이퍼 증착장비"),
    (["반도체 제조용 장비"], ["848620", "848640"],
     "반도체 제조용 장비 (전공정+후공정 대표 코드)"),
    (["실리콘 카바이드"], ["7017100000"],
     "반도체용 실리콘 카바이드 (쿼츠 기기)"),
]


def extract_product_part(title: str) -> str:
    """기업명: 제품명 형태에서 제품명만 추출"""
    if ":" in title:
        return title.split(":", 1)[1].strip()
    return title


def classify(title: str) -> list[str]:
    """product_title → HS prefix 리스트 반환. 매칭 없으면 []."""
    part = extract_product_part(title)
    for keywords, hs_codes, _ in PRODUCT_HS_RULES:
        if all(kw in part for kw in keywords):
            return hs_codes
    return []


def get_confirmed_values(
    conn: sqlite3.Connection, hs_prefixes: list[str]
) -> tuple[str, float, float]:
    """전국 집계 기준 최신 월 확정 수출/수입액 반환"""
    if not hs_prefixes:
        return "", 0.0, 0.0
    clauses = " OR ".join(["hs_code LIKE ?" for _ in hs_prefixes])
    params = [f"{p}%" for p in hs_prefixes]

    latest = conn.execute(
        f"""
        SELECT MAX(period_ym)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade'
          AND period_ym GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
          AND country_code='' AND sido_code=''
          AND ({clauses})
        """,
        params,
    ).fetchone()[0]

    if not latest:
        return "", 0.0, 0.0

    row = conn.execute(
        f"""
        SELECT SUM(export_value), SUM(import_value)
        FROM customs_monthly_record
        WHERE endpoint='itemtrade' AND period_ym=?
          AND country_code='' AND sido_code=''
          AND ({clauses})
        """,
        [latest, *params],
    ).fetchone()

    return latest, float(row[0] or 0), float(row[1] or 0)


def run(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    a_cases = conn.execute(
        """
        SELECT id, product_title
        FROM telegram_trade_card
        WHERE (hs_prefixes_json IS NULL OR hs_prefixes_json='[]' OR hs_prefixes_json='')
          AND (confirmed_latest_period_ym IS NULL OR confirmed_latest_period_ym='')
        """
    ).fetchall()

    print(f"A케이스 총 {len(a_cases)}건 처리 시작 (dry_run={dry_run})")

    classified = 0
    unclassified = 0
    unclassified_titles: set[str] = set()

    for row in a_cases:
        hs = classify(row["product_title"])
        if not hs:
            unclassified += 1
            base = extract_product_part(row["product_title"])
            # scope suffix 제거
            base = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
            base = re.sub(r"\s*\([^)]*\)\s*$", "", base).strip()
            unclassified_titles.add(base)
            continue

        latest, exp, imp = get_confirmed_values(conn, hs)

        if not dry_run:
            conn.execute(
                """
                UPDATE telegram_trade_card
                SET hs_prefixes_json          = ?,
                    confirmed_latest_period_ym = ?,
                    confirmed_export_value     = ?,
                    confirmed_import_value     = ?,
                    updated_at                 = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    json.dumps(hs, ensure_ascii=False),
                    latest,
                    exp,
                    imp,
                    row["id"],
                ),
            )
        classified += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"  매칭 성공: {classified}건")
    print(f"  매칭 실패: {unclassified}건")
    if unclassified_titles:
        print("\n  [미매칭 고유 제품명]")
        for t in sorted(unclassified_titles):
            print(f"    - {t}")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
