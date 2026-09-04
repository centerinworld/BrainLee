#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from services.gemini import generate_text, is_configured

DB = '/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'
DOWNLOADS = Path('/Users/brainlee/Downloads')
SUMMARIES_DIR = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/reports/openai_mini_summaries')
TARGET_STOCKS = [
    '아이엠티','잉크테크','화신','마크로젠','에스에이엠티','아이센스',
    '한화오션','포인트모바일','빅솔론','에이엘티','위드텍',
]


@dataclass
class FileMeta:
    stock_name: str
    path: Path
    quarter_tag: Optional[str]


def qtag_from_name(name: str) -> Optional[str]:
    n = name.lower()
    # 2026_1Q, 26.1Q, 26_1q 등
    m = re.search(r'(20\d{2}|\d{2})[._\- ]?(\d)q', n)
    if m:
        yy = int(m.group(1))
        if yy < 100:
            yy += 2000
        return f'{yy}Q{int(m.group(2))}'
    # 25.4Q 같은 케이스
    m2 = re.search(r'(\d{2})[._\- ]?(\d)q', n)
    if m2:
        yy = 2000 + int(m2.group(1))
        return f'{yy}Q{int(m2.group(2))}'
    # 1분기 / 2026 1분기
    m3 = re.search(r'(20\d{2}|\d{2})[^0-9]?(1|2|3|4)분기', name)
    if m3:
        yy = int(m3.group(1))
        if yy < 100:
            yy += 2000
        return f'{yy}Q{int(m3.group(2))}'
    return None


def find_files_from_db() -> list[FileMeta]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.stock_name, f.file_path
        FROM detailed_analysis_posts p
        JOIN detailed_analysis_files f ON f.post_id=p.id
        WHERE f.file_type='xlsx'
        ORDER BY p.stock_name, f.id
        """
    ).fetchall()
    out: list[FileMeta] = []
    for r in rows:
        p = Path(r['file_path'])
        if p.exists():
            out.append(FileMeta(stock_name=r['stock_name'], path=p, quarter_tag=qtag_from_name(p.name)))
    return out


def build_compact_context(path: Path) -> str:
    try:
        xls = pd.ExcelFile(path)
    except Exception as e:
        return f'파일 로딩 실패: {e}'
    lines = [f'파일: {path.name}', f'시트수: {len(xls.sheet_names)}']
    for s in xls.sheet_names[:4]:
        try:
            df = pd.read_excel(path, sheet_name=s)
        except Exception:
            lines.append(f'- {s}: read fail')
            continue
        lines.append(f'- {s}: shape={df.shape[0]}x{df.shape[1]}')
        if df.empty:
            continue
        num = df.select_dtypes(include=['number'])
        if not num.empty:
            c = num.columns[0]
            v = num[c].dropna()
            if not v.empty:
                lines.append(f'  대표수치 {c}: 최근={float(v.iloc[-1]):,.2f}, 평균={float(v.mean()):,.2f}')
    return '\n'.join(lines)


def openai_mini_summarize(stock_name: str, file_name: str, context: str) -> str:
    if not is_configured():
        return (
            f"# {stock_name} 상세분석 (자동요약)\n\n"
            f"- 파일: {file_name}\n"
            "- GEMINI_API_KEY 미설정으로 로컬 요약 사용\n\n"
            f"## 추출 요약\n{context}\n"
        )

    prompt = (
        f"다음 엑셀 요약컨텍스트를 바탕으로 한국 주식 {stock_name} 분석 게시글을 만들어라.\n"
        "형식: 핵심포인트/실적추세/현금흐름체크/리스크/확인필요사항\n"
        "수치가 불충분하면 단정하지 말고 검증 필요로 명시.\n\n"
        f"[컨텍스트]\n{context}\n"
    )
    try:
        txt = generate_text(prompt, system_instruction='당신은 재무분석 리포트 작성 보조다.', temperature=0.2, max_output_tokens=1400, timeout=40)
        return f"# {stock_name} 상세분석 (Gemini Flash)\n\n- 파일: {file_name}\n\n{txt}\n"
    except Exception as e:
        return (
            f"# {stock_name} 상세분석 (자동요약-오류대체)\n\n"
            f"- 파일: {file_name}\n"
            f"- Gemini Flash 호출오류: {e}\n\n"
            f"## 추출 요약\n{context}\n"
        )


def latest_fin_q(conn: sqlite3.Connection, stock_code: Optional[str]) -> Optional[str]:
    if not stock_code:
        return None
    row = conn.execute(
        """
        SELECT year, quarter
        FROM financial_data
        WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC, quarter DESC
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if not row:
        return None
    return f"{row['year']}Q{row['quarter']}"


def find_stock_code(conn: sqlite3.Connection, stock_name: str) -> Optional[str]:
    row = conn.execute('SELECT stock_code FROM stock_universe WHERE stock_name=? LIMIT 1', (stock_name,)).fetchone()
    return row['stock_code'] if row else None


def upsert_post(conn: sqlite3.Connection, fm: FileMeta, body: str):
    stock_code = find_stock_code(conn, fm.stock_name)
    title = f"{fm.stock_name} 상세분석 - {fm.path.stem}"
    row = conn.execute(
        'SELECT id FROM detailed_analysis_posts WHERE stock_name=? AND title=? LIMIT 1',
        (fm.stock_name, title),
    ).fetchone()
    if row:
        pid = row['id']
        conn.execute(
            "UPDATE detailed_analysis_posts SET stock_code=?, content_md=?, source='openai_mini_auto', updated_at=datetime('now') WHERE id=?",
            (stock_code, body, pid),
        )
        conn.execute('DELETE FROM detailed_analysis_files WHERE post_id=?', (pid,))
    else:
        cur = conn.execute(
            "INSERT INTO detailed_analysis_posts(stock_code,stock_name,title,content_md,source) VALUES (?,?,?,?, 'openai_mini_auto')",
            (stock_code, fm.stock_name, title, body),
        )
        pid = cur.lastrowid

    conn.execute(
        "INSERT INTO detailed_analysis_files(post_id,file_name,file_path,file_type) VALUES (?,?,?,?)",
        (pid, fm.path.name, str(fm.path), 'xlsx'),
    )

    # OpenAI mini 분석 결과를 별도 md 파일로 저장해 UI에서 다운로드 가능하게 노출
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    safe_stock = re.sub(r'[^0-9A-Za-z가-힣_-]+', '_', fm.stock_name).strip('_') or 'stock'
    safe_stem = re.sub(r'[^0-9A-Za-z가-힣_-]+', '_', fm.path.stem).strip('_') or 'analysis'
    summary_name = f"{safe_stock}_{safe_stem}_openai_mini.md"
    summary_path = SUMMARIES_DIR / summary_name
    summary_path.write_text(body, encoding='utf-8')
    conn.execute(
        "INSERT INTO detailed_analysis_files(post_id,file_name,file_path,file_type) VALUES (?,?,?,?)",
        (pid, summary_name, str(summary_path), 'openai_md'),
    )

    # report_files 연관첨부
    rel = conn.execute(
        """
        SELECT file_name, file_path
        FROM report_files
        WHERE (stock_name=? OR (stock_code IS NOT NULL AND stock_code=?))
        ORDER BY COALESCE(report_date, created_at) DESC
        LIMIT 100
        """,
        (fm.stock_name, stock_code),
    ).fetchall()
    for rr in rel:
        fp = rr['file_path'] or ''
        ex = conn.execute('SELECT 1 FROM detailed_analysis_files WHERE post_id=? AND file_path=? LIMIT 1', (pid, fp)).fetchone()
        if ex:
            continue
        conn.execute(
            "INSERT INTO detailed_analysis_files(post_id,file_name,file_path,file_type) VALUES (?,?,?,?)",
            (pid, rr['file_name'] or Path(fp).name, fp, 'telegram'),
        )

    return pid, stock_code, latest_fin_q(conn, stock_code)


def main():
    files = find_files_from_db()
    if not files:
        print('No target xlsx files found.')
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    report = []
    for fm in files:
        ctx = build_compact_context(fm.path)
        body = openai_mini_summarize(fm.stock_name, fm.path.name, ctx)
        pid, code, dbq = upsert_post(conn, fm, body)
        stale = (fm.quarter_tag == '2026Q1' and dbq != '2026Q1')
        report.append({
            'stock': fm.stock_name,
            'file': fm.path.name,
            'file_q': fm.quarter_tag,
            'stock_code': code,
            'db_latest_q': dbq,
            'stale_26q1': stale,
            'post_id': pid,
        })

    conn.commit()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
