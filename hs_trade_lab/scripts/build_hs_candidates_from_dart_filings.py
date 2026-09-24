"""
2026-09-08 신규: DART 사업보고서 원문("사업의 내용" 섹션)에서 제품 서술을 뽑아
DeepSeek으로 HS 카테고리와 매칭 — segment_revenue에는 있지만(2,561개사)
hs_code_company_map에는 없는 기업을 우선 대상으로 한다.

DART Open API(document.xml)로 사업보고서 원문 ZIP을 받아 "사업의 내용" 섹션 텍스트를
추출한다. 회사마다 문서 포맷이 제각각이라 완벽한 추출은 어려우므로, 실패해도
건너뛰고 다음 회사로 넘어가는 방식으로 설계.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from llm_hs_matcher import (  # noqa: E402
    HS_DB_PATH, ensure_staging_table, load_hs_taxonomy, match_text_to_hs, save_candidates,
)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def candidate_companies(conn, limit: int, only_unmapped: bool) -> list[tuple]:
    """segment_revenue에 있는 기업 중 hs_code_company_map에 없는 기업(매출 큰 순)."""
    hs_conn = sqlite3.connect(HS_DB_PATH, timeout=10)
    mapped = {r[0] for r in hs_conn.execute("SELECT DISTINCT stock_code FROM hs_code_company_map").fetchall()}
    hs_conn.close()

    rows = conn.execute(
        """
        SELECT stock_code, corp_code, MAX(revenue) as max_rev
        FROM segment_revenue
        WHERE corp_code IS NOT NULL AND stock_code IS NOT NULL
        GROUP BY stock_code, corp_code
        ORDER BY max_rev DESC
        """
    ).fetchall()
    if only_unmapped:
        rows = [r for r in rows if r[0] not in mapped]
    return rows[:limit] if limit else rows


def fetch_business_report_text(corp_code: str, timeout: float = 20.0) -> tuple[str, str] | None:
    """최신 사업보고서(A001)의 rcept_no와 '사업의 내용' 텍스트를 반환. 실패 시 None."""
    r = httpx.get(
        "https://opendart.fss.or.kr/api/list.json",
        params={
            "crtfc_key": config.DART_API_KEY,
            "corp_code": corp_code,
            "bgn_de": "20240101",
            "pblntf_detail_ty": "A001",
        },
        timeout=timeout,
    )
    if r.status_code != 200:
        return None
    data = r.json()
    if data.get("status") != "000":
        return None
    items = data.get("list") or []
    if not items:
        return None
    rcept_no = items[0]["rcept_no"]

    r2 = httpx.get(
        "https://opendart.fss.or.kr/api/document.xml",
        params={"crtfc_key": config.DART_API_KEY, "rcept_no": rcept_no},
        timeout=timeout,
    )
    if r2.status_code != 200:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(r2.content))
    except zipfile.BadZipFile:
        return None
    main_name = sorted(z.namelist(), key=len)[0]  # 보통 가장 짧은 이름이 본문
    raw = z.read(main_name).decode("utf-8", errors="ignore")

    text = extract_business_section(raw)
    return (rcept_no, text) if text else None


def extract_business_section(raw_xml: str) -> str:
    """'사업의 내용' 여러 등장 지점 중 실제 서술(목차가 아닌) 구간을 찾아 텍스트 반환."""
    idxs = [m.start() for m in re.finditer("사업의 내용", raw_xml)]
    best = ""
    for idx in idxs:
        chunk = raw_xml[idx:idx + 4000]
        text = WS_RE.sub(" ", TAG_RE.sub(" ", chunk)).strip()
        # 목차 항목은 "-----" 구분선과 짧은 소제목이 반복되는 패턴이라 실제 한글
        # 문장(어미 등)이 적음 — 실질 텍스트 길이로 걸러낸다
        korean_chars = len(re.findall(r"[가-힣]", text))
        if korean_chars > len(best):
            best = text
    return best[:5000]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="처리할 종목 수(파일럿 기본 20)")
    ap.add_argument("--only-unmapped", action="store_true", default=True)
    ap.add_argument("--include-mapped", dest="only_unmapped", action="store_false")
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args()

    conn = connect_primary_db(timeout=30)
    rows = candidate_companies(conn, args.limit, args.only_unmapped)
    canonical_names = dict(conn.execute("SELECT stock_code, stock_name FROM stock_universe").fetchall())
    print(json.dumps({"target_companies": len(rows)}, ensure_ascii=False))

    hs_conn = sqlite3.connect(HS_DB_PATH, timeout=10)
    ensure_staging_table(hs_conn)
    taxonomy = load_hs_taxonomy(hs_conn)
    mapped = {r[0] for r in hs_conn.execute("SELECT DISTINCT stock_code FROM hs_code_company_map").fetchall()}

    client, model = config.get_ai_client()
    if client is None:
        print("AI client not configured", file=sys.stderr)
        return 2

    ok = 0
    total_matches = 0
    errors = 0
    no_doc = 0
    for i, (sc, corp_code, max_rev) in enumerate(rows, 1):
        name = canonical_names.get(sc, sc)
        try:
            result = fetch_business_report_text(corp_code)
            if not result:
                no_doc += 1
                continue
            rcept_no, text = result
            if len(re.findall(r"[가-힣]", text)) < 200:
                no_doc += 1
                continue
            matches = match_text_to_hs(client, model, taxonomy, name, "DART 사업보고서 사업의 내용", text)
            if matches and "_error" in matches[0]:
                errors += 1
                print(f"  [{i}/{len(rows)}] {sc} {name} ERROR: {matches[0]['_error']}", file=sys.stderr)
                continue
            n = save_candidates(hs_conn, sc, name, "dart_filing", f"rcept_no:{rcept_no}", matches, sc in mapped)
            total_matches += n
            if n:
                ok += 1
                print(f"  [{i}/{len(rows)}] {sc} {name} -> {n}건 매칭: {[m['hs_code'] for m in matches]}")
        except Exception as exc:
            errors += 1
            print(f"  [{i}/{len(rows)}] {sc} {name} EXCEPTION: {exc}", file=sys.stderr)
        if args.sleep:
            time.sleep(args.sleep)

    hs_conn.close()
    conn.close()
    print(json.dumps({
        "processed": len(rows), "companies_with_matches": ok,
        "total_candidate_rows": total_matches, "errors": errors, "no_document": no_doc,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
