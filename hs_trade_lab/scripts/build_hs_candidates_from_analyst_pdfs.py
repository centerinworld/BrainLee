"""
2026-09-08 신규: 애널리스트 리포트 PDF(`report_files`, 23,727건/1,456개사)에서
사업 설명을 뽑아 DeepSeek으로 HS 카테고리와 매칭 — hs_code_company_map 커버리지
확장(현재 404개사, 7.4%) 후보 생성. 결과는 스테이징 테이블(llm_hs_mapping_candidates)에만
저장하고 실제 hs_code_company_map 반영은 사람이 샘플 검토 후 별도로 진행한다.

기존 scripts/batch_extract_report_consensus.py(목표주가 추출용, OpenAI/Gemini 기반,
비용 문제로 비활성화됨)와는 목적이 다른 별도 파이프라인 — 재사용 가능한 부분
(candidate_reports 쿼리 패턴, pdfplumber 텍스트 추출)만 참고해 새로 작성.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from llm_hs_matcher import (  # noqa: E402
    HS_DB_PATH, ensure_staging_table, load_hs_taxonomy, match_text_to_hs, save_candidates,
)


def extract_pdf_text(file_path: Path, max_pages: int = 3) -> str:
    import pdfplumber

    texts = []
    with pdfplumber.open(str(file_path)) as pdf:
        for page in pdf.pages[:max_pages]:
            t = page.extract_text() or ""
            if t.strip():
                texts.append(t)
    return "\n".join(texts)[:6000]


def candidate_companies(conn, limit: int, only_unmapped: bool) -> list[tuple]:
    """HS매핑 안 된(또는 전체) 기업별 최신 리포트 후보 3건(파일 유실 대비 폴백용)."""
    hs_conn = sqlite3.connect(HS_DB_PATH, timeout=10)
    mapped = {r[0] for r in hs_conn.execute("SELECT DISTINCT stock_code FROM hs_code_company_map").fetchall()}
    hs_conn.close()

    rows = conn.execute(
        """
        SELECT stock_code, stock_name, file_path, report_date, id
        FROM (
            SELECT stock_code, stock_name, file_path, report_date, id,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY report_date DESC, id DESC) AS rn
            FROM report_files
            WHERE COALESCE(stock_code,'') != ''
              AND (LOWER(COALESCE(mime_type,'')) LIKE '%pdf%' OR LOWER(COALESCE(file_path,'')) LIKE '%.pdf')
        ) t
        WHERE rn <= 3
        ORDER BY stock_code, rn
        """
    ).fetchall()
    if only_unmapped:
        rows = [r for r in rows if r[0] not in mapped]

    # 종목별로 실제 존재하는 첫 파일만 채택
    by_stock: dict = {}
    for sc, name, fp, rdate, rid in rows:
        if sc in by_stock:
            continue
        if Path(fp).exists():
            by_stock[sc] = (sc, name, fp, rdate, rid)
    result = list(by_stock.values())
    result.sort(key=lambda r: r[3], reverse=True)
    return result[:limit] if limit else result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="처리할 종목 수(파일럿 기본 30)")
    ap.add_argument("--only-unmapped", action="store_true", default=True)
    ap.add_argument("--include-mapped", dest="only_unmapped", action="store_false")
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    conn = connect_primary_db(timeout=30)
    rows = candidate_companies(conn, args.limit, args.only_unmapped)
    print(json.dumps({"target_companies": len(rows)}, ensure_ascii=False))

    # 2026-09-08 수정: report_files.stock_name은 파일명 파싱 잔여물(증권사명 등)이
    # 섞여 깨진 경우가 많음(예: "오킨스전자［］ Hyundai+Moto") — 이 이름을 그대로
    # LLM에게 "이 회사가 맞는지 확인하라"는 프롬프트에 넣으면 모델이 이름 불일치로
    # 오인해 정상 매칭도 스스로 기각하는 부작용이 실측 확인됨(60개사 파일럿에서 매칭
    # 0건으로 급감). stock_universe의 정식 종목명으로 교체한다.
    canonical_names = dict(conn.execute("SELECT stock_code, stock_name FROM stock_universe").fetchall())

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
    for i, (sc, name, file_path, report_date, rid) in enumerate(rows, 1):
        fp = Path(file_path)
        if not fp.exists():
            continue
        clean_name = canonical_names.get(sc) or name or sc
        try:
            text = extract_pdf_text(fp)
            if not text.strip():
                continue
            matches = match_text_to_hs(client, model, taxonomy, clean_name, "애널리스트 리포트", text)
            if matches and "_error" in matches[0]:
                errors += 1
                print(f"  [{i}/{len(rows)}] {sc} {clean_name} ERROR: {matches[0]['_error']}", file=sys.stderr)
                continue
            n = save_candidates(hs_conn, sc, clean_name, "analyst_pdf", f"report_files:{rid}", matches, sc in mapped)
            total_matches += n
            if n:
                ok += 1
                print(f"  [{i}/{len(rows)}] {sc} {clean_name} -> {n}건 매칭: "
                      f"{[m['hs_code'] for m in matches]}")
        except Exception as exc:
            errors += 1
            print(f"  [{i}/{len(rows)}] {sc} {name} EXCEPTION: {exc}", file=sys.stderr)
        if args.sleep:
            time.sleep(args.sleep)

    hs_conn.close()
    conn.close()
    print(json.dumps({
        "processed": len(rows), "companies_with_matches": ok,
        "total_candidate_rows": total_matches, "errors": errors,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
