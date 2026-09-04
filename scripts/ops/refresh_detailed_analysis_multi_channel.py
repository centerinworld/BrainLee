#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from services.gemini import generate_text, is_configured

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
EXCLUDE_CHANNELS: set[str] = set()
SECTION_HEADER = "## 타채널 맥락 요약 (GPT mini)"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def _load_env(path: str = "/Volumes/Realtek_NVME/stock_dashboard/runtime/.env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _pick_stock_meta(conn: sqlite3.Connection, stock_code: str, stock_name: str) -> dict[str, str]:
    row = conn.execute(
        """
        SELECT market, sector_large, sector_mid
        FROM stock_universe
        WHERE stock_code=?
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    return {
        "market": (row["market"] if row else "") or "-",
        "sector_large": (row["sector_large"] if row else "") or "-",
        "sector_mid": (row["sector_mid"] if row else "") or "-",
        "stock_name": stock_name,
        "stock_code": stock_code,
    }


def _collect_channel_messages(conn: sqlite3.Connection, stock_name: str, stock_code: str, per_channel_limit: int = 120) -> dict[str, list[dict[str, str]]]:
    # 1) 직접 언급(anchor) 메시지 수집
    rows = conn.execute(
        """
        SELECT channel, message_id, date, text, stocks
        FROM telegram_messages
        WHERE (
            stocks LIKE '%' || ? || '%'
            OR text LIKE '%' || ? || '%'
            OR text LIKE '%' || ? || '%'
        )
        ORDER BY date DESC
        LIMIT 5000
        """,
        (stock_name, stock_name, stock_code),
    ).fetchall()

    # 2) anchor 주변 연속 맥락 메시지(동일 채널, 인접 message_id) 확장 수집
    # 텔레그램 글 흐름상 종목명이 없는 후속/선행 설명글을 포함시키기 위함
    anchor_by_channel: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        ch = (r["channel"] or "").strip() or "unknown"
        if ch in EXCLUDE_CHANNELS:
            continue
        try:
            anchor_by_channel[ch].append(int(r["message_id"] or 0))
        except Exception:
            continue

    ctx_rows: list[sqlite3.Row] = []
    for ch, mids in anchor_by_channel.items():
        mids = sorted(set(m for m in mids if m > 0))
        # 채널당 anchor 상위 80건만 확장해 과도한 토큰 사용 방지
        for mid in mids[:80]:
            lo = mid - 2
            hi = mid + 2
            ctx_rows.extend(
                conn.execute(
                    """
                    SELECT channel, message_id, date, text, stocks
                    FROM telegram_messages
                    WHERE channel=? AND message_id BETWEEN ? AND ?
                    ORDER BY message_id DESC
                    """,
                    (ch, lo, hi),
                ).fetchall()
            )

    merged_rows = list(rows) + ctx_rows
    # 중복 제거
    seen: set[tuple[str, int]] = set()
    by_channel: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in merged_rows:
        ch = (r["channel"] or "").strip() or "unknown"
        if ch in EXCLUDE_CHANNELS:
            continue
        try:
            mid = int(r["message_id"] or 0)
        except Exception:
            mid = 0
        if (ch, mid) in seen:
            continue
        seen.add((ch, mid))
        if len(by_channel[ch]) >= per_channel_limit:
            continue
        by_channel[ch].append(
            {
                "date": (r["date"] or "")[:19],
                "text": (r["text"] or "").replace("\n", " ").strip(),
                "message_id": str(r["message_id"] or ""),
            }
        )
    return by_channel


def _attach_related_files(conn: sqlite3.Connection, post_id: int, stock_name: str, stock_code: str) -> int:
    rows = conn.execute(
        """
        SELECT channel_id, file_name, file_path, mime_type, report_date, created_at
        FROM report_files
        WHERE (
            (stock_code IS NOT NULL AND stock_code<>'' AND stock_code=?)
            OR (stock_name IS NOT NULL AND stock_name<>'' AND stock_name=?)
            OR (file_name IS NOT NULL AND file_name LIKE '%' || ? || '%')
            OR (caption IS NOT NULL AND caption LIKE '%' || ? || '%')
        )
        ORDER BY COALESCE(report_date, created_at) DESC
        LIMIT 500
        """,
        (stock_code, stock_name, stock_name, stock_name),
    ).fetchall()

    added = 0
    for r in rows:
        ch = (r["channel_id"] or "").strip()
        if ch in EXCLUDE_CHANNELS:
            continue
        fp = (r["file_path"] or "").strip()
        if not fp:
            continue
        exists = conn.execute(
            "SELECT 1 FROM detailed_analysis_files WHERE post_id=? AND file_path=? LIMIT 1",
            (post_id, fp),
        ).fetchone()
        if exists:
            continue
        fn = (r["file_name"] or Path(fp).name or "telegram_file").strip()
        ftype = "telegram_other"
        conn.execute(
            """
            INSERT INTO detailed_analysis_files (post_id, file_name, file_path, file_type)
            VALUES (?, ?, ?, ?)
            """,
            (post_id, fn, fp, ftype),
        )
        added += 1
    return added


def _fallback_summary(meta: dict[str, str], by_channel: dict[str, list[dict[str, str]]]) -> str:
    lines = [
        SECTION_HEADER,
        "",
        f"- 분류: `{meta['market']}` / `{meta['sector_large']}` / `{meta['sector_mid']}`",
        f"- 종목: `{meta['stock_name']} ({meta['stock_code']})`",
        "",
        "### 채널별 흐름 요약",
    ]
    if not by_channel:
        lines.append("- 타채널 언급이 아직 없습니다.")
        return "\n".join(lines)

    for ch, msgs in sorted(by_channel.items(), key=lambda x: len(x[1]), reverse=True)[:8]:
        lines.append(f"- `{ch}`: {len(msgs)}건")
        for m in msgs[:3]:
            snippet = (m["text"] or "")[:140]
            lines.append(f"  - {m['date']} | {snippet}")
    lines += [
        "",
        "### 투자 관점 메모",
        "- 다건 반복 언급 채널의 메시지 흐름(연속 포인트)을 우선 추적",
        "- 수치(매출/증설/CAPA/수주) 언급은 다음 업데이트에서 별도 표로 누적",
    ]
    return "\n".join(lines)


def _openai_mini_summary(meta: dict[str, str], by_channel: dict[str, list[dict[str, str]]]) -> str:
    if not is_configured():
        return _fallback_summary(meta, by_channel)

    channel_blocks = []
    for ch, msgs in sorted(by_channel.items(), key=lambda x: len(x[1]), reverse=True)[:8]:
        # 메시지 맥락 보존: 최신순 12개
        sample = msgs[:12]
        txt = "\n".join([f"- {m['date']} | {m['text'][:220]}" for m in sample])
        channel_blocks.append(f"[채널] {ch}\n{txt}")
    payload_text = "\n\n".join(channel_blocks) if channel_blocks else "(메시지 없음)"

    prompt = f"""
아래 텔레그램 메시지를 바탕으로 종목별/섹터별로 맥락을 요약해줘.

종목: {meta['stock_name']} ({meta['stock_code']})
시장: {meta['market']}
섹터: {meta['sector_large']} / {meta['sector_mid']}

요구사항:
1) 글을 단편이 아니라 흐름으로 요약(초기 주장→후속 검증→최근 업데이트)
2) 종목/섹터 관점으로 분류
3) 투자판단에 필요한 수치/이벤트/리스크를 분리
4) 허위 추론 금지(불명확하면 '확인 필요')
5) 한국어 Markdown, 아래 형식 고정

형식:
## 타채널 맥락 요약 (GPT mini)
- 분류: 시장/섹터/종목

### 채널별 핵심 흐름
- 채널명: 흐름 요약

### 종목 관점 Key Insight
- 4~8개

### 섹터 관점 Key Insight
- 3~6개

### 투자 체크포인트
- 긍정 2~4개
- 리스크 2~4개
- 다음 확인사항 3개

[메시지 데이터]
{payload_text}
"""

    try:
        return generate_text(
            prompt,
            system_instruction="당신은 한국 주식 텔레그램 리서치 요약가다.",
            temperature=0.2,
            max_output_tokens=1800,
            timeout=90,
        )
    except Exception:
        return _fallback_summary(meta, by_channel)


def _merge_section(content_md: str, section_md: str) -> str:
    base = (content_md or "").strip()
    if not base:
        return section_md.strip()
    marker = f"\n{SECTION_HEADER}\n"
    idx = base.find(marker)
    if idx >= 0:
        return (base[:idx].rstrip() + "\n\n" + section_md.strip()).strip()
    return (base + "\n\n" + section_md.strip()).strip()


def run(target_stock: str = "", limit: int = 999) -> dict[str, int]:
    _load_env()
    with _conn() as conn:
        posts = conn.execute(
            """
            SELECT id, stock_code, stock_name, content_md, source
            FROM detailed_analysis_posts
            ORDER BY updated_at DESC
            """
        ).fetchall()
        if target_stock:
            posts = [p for p in posts if p["stock_name"] == target_stock or (p["stock_code"] or "") == target_stock]
        posts = posts[:limit]

        updated = 0
        attached = 0
        skipped = 0
        for p in posts:
            stock_name = (p["stock_name"] or "").strip()
            stock_code = (p["stock_code"] or "").strip()
            if not stock_name:
                skipped += 1
                continue

            meta = _pick_stock_meta(conn, stock_code, stock_name)
            by_channel = _collect_channel_messages(conn, stock_name, stock_code)
            section = _openai_mini_summary(meta, by_channel)
            merged = _merge_section(p["content_md"] or "", section)

            conn.execute(
                "UPDATE detailed_analysis_posts SET content_md=?, updated_at=datetime('now') WHERE id=?",
                (merged, p["id"]),
            )
            updated += 1

            # 타채널 첨부파일 자동 연결(다운로드 가능)
            attached += _attach_related_files(conn, p["id"], stock_name, stock_code)

        conn.commit()
        return {"processed": len(posts), "updated_posts": updated, "attached_files": attached, "skipped": skipped}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", default="", help="종목명 또는 종목코드 단일 실행")
    ap.add_argument("--limit", type=int, default=999, help="처리 최대 건수")
    args = ap.parse_args()
    out = run(target_stock=args.stock.strip(), limit=args.limit)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
