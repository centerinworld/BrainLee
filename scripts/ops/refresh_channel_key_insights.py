#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import argparse
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

import requests
from telethon import TelegramClient
from telethon.tl.functions.messages import CheckChatInviteRequest

sys.path.insert(0, "/Applications/stock_dashboard")
import config as cfg


DB_PATH = "/Applications/stock_dashboard/stock.db"
SESSION = os.getenv("TELEGRAM_SESSION_PATH", "/Applications/stock_dashboard/telegram_session_insight")
INVITE_LINK = "https://t.me/+Sx6sQ6wCwzE5MWM1"


def _load_env_file(path: str = "/Applications/stock_dashboard/.env") -> None:
    p = path
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            ln = line.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


@dataclass
class PostRow:
    id: int
    stock_code: str
    stock_name: str
    content_md: str


def _deepseek_summarize(stock_name: str, stock_code: str, channel_title: str, msgs: List[Tuple[int, str, str]]) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
    if not api_key:
        return (
            f"## 채널 Key Insight ({channel_title})\n\n"
            "- DEEPSEEK_API_KEY가 없어 자동 생성이 제한되었습니다.\n"
            "- 텔레그램 메시지 수집은 되었으나 요약 생성은 생략되었습니다.\n"
        )

    sample = msgs[:140]
    msg_block = "\n".join([f"[{d}] {t}" for _, d, t in sample if t])
    prompt = f"""
너는 주식 애널리스트다. 아래는 텔레그램 채널 '{channel_title}'에서 {stock_name}({stock_code}) 관련 메시지다.

반드시 아래 형식의 한국어 Markdown으로만 답해라:
## 채널 Key Insight ({channel_title})
### 1) 왜 이 종목을 소개/추천하는가
- 핵심 포인트 3~6개
### 2) 주담통화/현장/설비·CAPA 관련 정량 정보
- 연도별/분기별 수치가 있으면 표로 정리
- 예: 장비 도입 대수, 증설 라인 수, 수주량, 고객사 물량
- 수치가 불명확하면 '확인 필요'로 표기
### 3) 실적/밸류/수급 관점 해석
- 매출/이익/마진/밸류/수급/모멘텀 관점 요약
### 4) 리스크 및 검증 체크리스트
- 4~8개

중요 규칙:
- 메시지에 실제로 없는 내용은 추정이라고 명시
- 'API 키 없음' 같은 시스템 문구 금지
- 투자 관점에서 의사결정에 필요한 사실 중심으로 정리

[메시지 원문]
{msg_block}
"""
    try:
        res = requests.post(
            base_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "당신은 숫자/팩트 기반 주식 리서치 애널리스트다."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=90,
        )
        res.raise_for_status()
        data = res.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return (
            f"## 채널 Key Insight ({channel_title})\n\n"
            f"- DeepSeek 요약 생성 실패: {e}\n"
            "- 재시도 필요\n"
        )


def _merge_content(base_md: str, insight_md: str) -> str:
    base = (base_md or "").strip()
    # 기존 채널 인사이트 섹션 있으면 교체
    pat = re.compile(r"\n## 채널 Key Insight \([^)]+\)[\s\S]*$", re.MULTILINE)
    if pat.search(base):
        return pat.sub("\n" + insight_md.strip() + "\n", base).strip()
    if not base:
        return insight_md.strip()
    return (base + "\n\n" + insight_md.strip()).strip()


def _sanitize_insight_text(text: str) -> str:
    t = text or ""
    # 사용자 요청: 불필요 문구 제거
    drop_patterns = [
        r"파일명에 해당 종목이 언급되었으나[^\n]*",
        r"텍스트나 해석은 포함되지 않음",
        r"해석은 포함되지 않음",
    ]
    for p in drop_patterns:
        t = re.sub(p, "", t, flags=re.IGNORECASE)
    # 빈 줄 정리
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


async def _collect_stock_messages(channel, stock_name: str, stock_code: str, client: TelegramClient) -> List[Tuple[int, str, str]]:
    seen: Dict[int, Tuple[int, str, str]] = {}
    queries = [stock_name]
    if stock_code and stock_code.isdigit():
        queries.append(stock_code)

    for q in queries:
        async for m in client.iter_messages(channel, search=q, limit=220):
            text = (m.message or "").strip()
            if len(text) < 15:
                continue
            if m.id in seen:
                continue
            seen[m.id] = (m.id, m.date.strftime("%Y-%m-%d %H:%M"), text.replace("\n", " "))

    # Flow-based context:
    # 1) report_files에서 종목과 매핑된 메시지 id를 앵커로 잡고
    # 2) 앵커 전후 메시지를 함께 수집해 종목명 직접 미언급 문맥까지 포함
    anchors = []
    with sqlite3.connect(DB_PATH) as c2:
        c2.row_factory = sqlite3.Row
        rows = c2.execute(
            """
            SELECT message_id
            FROM report_files
            WHERE channel_id=? AND (
                (stock_code IS NOT NULL AND stock_code<>'' AND stock_code=?)
                OR (stock_name IS NOT NULL AND stock_name<>'' AND stock_name=?)
                OR (file_name LIKE '%' || ? || '%')
                OR (caption LIKE '%' || ? || '%')
            )
            ORDER BY COALESCE(report_date, created_at) DESC
            LIMIT 18
            """,
            (getattr(channel, "title", ""), stock_code or "", stock_name, stock_name, stock_name),
        ).fetchall()
        anchors = [int(r["message_id"]) for r in rows if r["message_id"]]

    for anchor in anchors:
        ids = list(range(max(1, anchor - 14), anchor + 15))
        msgs = await client.get_messages(channel, ids=ids)
        for m in msgs:
            if not m:
                continue
            text = (m.message or "").strip()
            if len(text) < 10:
                continue
            if m.id in seen:
                continue
            seen[m.id] = (m.id, m.date.strftime("%Y-%m-%d %H:%M"), text.replace("\n", " "))

    items = list(seen.values())
    items.sort(key=lambda x: x[0], reverse=True)
    return items


async def run(target_stock: str = "") -> None:
    _load_env_file()
    global SESSION
    SESSION = os.getenv("TELEGRAM_SESSION_PATH", SESSION)
    invite_hash = INVITE_LINK.split("+", 1)[1]
    api_id = int(getattr(cfg, "TELEGRAM_API_ID", 0))
    api_hash = getattr(cfg, "TELEGRAM_API_HASH", "")

    client = TelegramClient(SESSION, api_id, api_hash)
    await client.connect()
    chk = await client(CheckChatInviteRequest(invite_hash))
    channel = chk.chat
    channel_title = getattr(channel, "title", "invite_channel")

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")

    posts_all = [
        PostRow(r["id"], (r["stock_code"] or ""), r["stock_name"], (r["content_md"] or ""))
        for r in conn.execute(
            """
            SELECT id, stock_code, stock_name, content_md
            FROM detailed_analysis_posts
            WHERE source='telegram_invite_excel_auto'
            ORDER BY stock_name
            """
        ).fetchall()
    ]
    posts = posts_all
    if target_stock:
        posts = [p for p in posts_all if p.stock_name == target_stock]
    print(f"channel={channel_title} posts={len(posts)} target_stock={target_stock or '-'}")

    done = 0
    for p in posts:
        msgs = await _collect_stock_messages(channel, p.stock_name, p.stock_code, client)
        if not msgs:
            continue
        insight_md = _deepseek_summarize(p.stock_name, p.stock_code, channel_title, msgs)
        merged = _sanitize_insight_text(_merge_content(p.content_md, insight_md))
        updated_ok = False
        for _ in range(8):
            try:
                conn.execute(
                    "UPDATE detailed_analysis_posts SET content_md=?, updated_at=datetime('now') WHERE id=?",
                    (merged, p.id),
                )
                conn.commit()
                updated_ok = True
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    time.sleep(0.6)
                    continue
                raise
        if not updated_ok:
            continue
        done += 1
        if done % 5 == 0:
            print(f"updated={done}")

    await client.disconnect()
    conn.close()
    print(f"done={done}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="", help="특정 종목만 갱신 (예: 포인트모바일)")
    args = parser.parse_args()
    asyncio.run(run(target_stock=args.stock.strip()))
