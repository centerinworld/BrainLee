"""
텔레그램 채널 모니터링 + OpenAI 요약 + 종목 추출
- 오전 9시 / 오후 9시 2회 수집, 같은 날로 합산 저장
- 일별 언급 횟수 DB 저장
실행: python3 telegram_monitor.py
"""

import asyncio
import sqlite3
import json
import os
from datetime import datetime, timedelta, date
from collections import Counter

from telethon import TelegramClient
import openai

# ──────────────────────────────────────────────
# ★ 설정값 입력 (여기만 수정하세요)
# ──────────────────────────────────────────────
# ── config.py에서 모든 설정값 로드 (환경변수 fallback) ──────────
import sys as _sys
_sys.path.insert(0, "/Applications/stock_dashboard")
import config as _cfg

TELEGRAM_API_ID   = int(getattr(_cfg, "TELEGRAM_API_ID",  0))
TELEGRAM_API_HASH = getattr(_cfg, "TELEGRAM_API_HASH", "")
TELEGRAM_SESSION  = "/Applications/stock_dashboard/telegram_session"
TELEGRAM_PHONE    = getattr(_cfg, "TELEGRAM_PHONE",    "")
OPENAI_API_KEY    = getattr(_cfg, "OPENAI_API_KEY",    "") or os.environ.get("OPENAI_API_KEY", "")
BOT_TOKEN         = getattr(_cfg, "TELEGRAM_BOT_TOKEN","")
TARGET_CHANNEL_ID = getattr(_cfg, "TELEGRAM_CHAT_ID",  "")

# DB에서 활성 채널 로드 (없으면 기본값 사용)
def _load_monitor_channels(db_path=None):
    try:
        import sqlite3 as _sl
        conn = _sl.connect(db_path or "/Applications/stock_dashboard/stock.db")
        rows = conn.execute(
            "SELECT channel_id FROM telegram_channels WHERE is_active=1 ORDER BY id"
        ).fetchall()
        conn.close()
        if rows:
            return ["@" + r[0].lstrip("@") for r in rows]
    except Exception as e:
        pass
    return ["@sunstudy1004", "@yigreport", "@DOC_POOL"]

MONITOR_CHANNELS = _load_monitor_channels()

DB_PATH = "/Applications/stock_dashboard/stock.db"
HOURS   = 12

# ──────────────────────────────────────────────
# DB 초기화
# ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel      TEXT,
            message_id   INTEGER,
            text         TEXT,
            date         TEXT,
            summary      TEXT,
            stocks       TEXT,
            collected_at TEXT,
            UNIQUE(channel, message_id)
        )
    """)
    # 핵심: 일별 종목 언급 횟수 (오전/오후 합산)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tg_daily_mentions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            mention_date TEXT,
            stock_name   TEXT,
            market       TEXT,
            mention_count INTEGER DEFAULT 0,
            UNIQUE(mention_date, stock_name)
        )
    """)
    conn.commit()
    conn.close()

# ──────────────────────────────────────────────
# OpenAI: 요약 + 종목 추출
# ──────────────────────────────────────────────
def analyze_messages(channel_name, messages):
    if not messages:
        return {"summary": "", "stocks": []}
    client   = openai.OpenAI(api_key=OPENAI_API_KEY)
    combined = "\n---\n".join(messages[:50])
    prompt = f"""아래는 텔레그램 주식 채널 '{channel_name}'의 최근 메시지들입니다.

{combined}

다음 두 가지를 JSON 형식으로 응답하세요:

1. summary: 채널 전체 내용을 3~5문장으로 요약 (한국어). 어떤 종목/섹터가 주목받는지, 주요 이슈와 투자 포인트 포함.

2. stocks: 언급된 코스피/코스닥 종목 목록. 각 항목 형식:
{{"name": "종목명", "market": "KOSPI 또는 KOSDAQ 또는 미확인", "reason": "이 종목이 언급된 이유와 투자 포인트를 3~4문장으로 설명"}}

JSON만 응답하고 다른 텍스트는 포함하지 마세요.
예시:
{{
  "summary": "반도체 AI 수요 증가로 수혜 기대감이 높다. 삼성전자 HBM4 양산 가속화로 엔비디아 공급망 편입 가능성이 제기됐다. 금융주는 금리 인하 기대감으로 순매수가 확대되는 추세다.",
  "stocks": [
    {{"name": "삼성전자", "market": "KOSPI", "reason": "HBM4 양산 일정 가속화로 엔비디아 공급망 편입 가능성이 높아졌다. AI 서버 수요 증가로 메모리 가격 반등이 예상되며 실적 개선 기대감이 반영되고 있다. 외국인 순매수가 3일 연속 이어지며 기술적 매수 신호도 나타나고 있다."}}
  ]
}}"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=3000,
        )
        text = response.choices[0].message.content.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[OpenAI 오류] {e}")
        return {"summary": "요약 실패", "stocks": []}

# ──────────────────────────────────────────────
# 텔레그램 봇 전송
# ──────────────────────────────────────────────
async def send_result(text):
    import aiohttp
    url    = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    async with aiohttp.ClientSession() as session:
        for chunk in chunks:
            await session.post(url, json={
                "chat_id": TARGET_CHANNEL_ID,
                "text": chunk,
                "parse_mode": "HTML",
            })
            await asyncio.sleep(0.5)

# ──────────────────────────────────────────────
# 일별 언급 횟수 저장 (오전/오후 누적 합산)
# ──────────────────────────────────────────────
def save_daily_mentions(today_str, all_stocks):
    counter    = Counter()
    market_map = {}
    for s in all_stocks:
        name = s.get("name", "").strip()
        if name:
            counter[name] += 1
            market_map[name] = s.get("market", "미확인")
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    for name, cnt in counter.items():
        cur.execute("""
            INSERT INTO tg_daily_mentions (mention_date, stock_name, market, mention_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mention_date, stock_name)
            DO UPDATE SET mention_count = mention_count + excluded.mention_count
        """, (today_str, name, market_map[name], cnt))
    conn.commit()
    conn.close()
    print(f"  → {len(counter)}개 종목 저장 완료 ({today_str})")

# ──────────────────────────────────────────────
# 리포트 생성
# ──────────────────────────────────────────────
def build_report(now, since, summaries, top_stocks, stock_market):
    session = "오전" if now.hour < 12 else "오후"
    lines   = [
        f"📊 <b>텔레그램 종목 분석 ({now.strftime('%m/%d')} {session})</b>",
        f"🕐 {since.strftime('%m/%d %H:%M')} ~ {now.strftime('%H:%M')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for cs in summaries:
        lines.append(f"<b>{cs['channel']}</b> ({cs['count']}개)")
        lines.append(f"📝 {cs['summary']}")
        if cs['stocks']:
            lines.append("")
            for s in cs['stocks'][:5]:
                mkt = s.get('market','미확인')
                tag = "🔵" if mkt=="KOSPI" else "🟢" if mkt=="KOSDAQ" else "⚪"
                lines.append(f"{tag} <b>{s['name']}</b> ({mkt})")
                reason = s.get('reason','')
                if reason:
                    lines.append(f"  └ {reason}")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>금일 언급 TOP {min(10, len(top_stocks))}</b>",
    ]
    for i, (name, cnt) in enumerate(top_stocks[:10], 1):
        mkt = stock_market.get(name, "미확인")
        tag = "🔵" if mkt == "KOSPI" else "🟢" if mkt == "KOSDAQ" else "⚪"
        lines.append(f"{i}. {tag} <b>{name}</b> {cnt}회 ({mkt})")
    return "\n".join(lines)

# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
async def collect_and_analyze():
    init_db()
    now       = datetime.now()
    since     = now - timedelta(hours=HOURS)
    today_str = date.today().isoformat()
    all_stocks = []

    client = TelegramClient(TELEGRAM_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start(phone=TELEGRAM_PHONE)

    print(f"\n{'='*50}")
    print(f"수집: {now.strftime('%Y-%m-%d %H:%M')} | 날짜키: {today_str}")
    print(f"{'='*50}")

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    channel_summaries = []

    for channel in MONITOR_CHANNELS:
        print(f"\n[{channel}] 수집 중...")
        try:
            entity   = await client.get_entity(channel)
            messages = []
            async for msg in client.iter_messages(entity, offset_date=now, reverse=False):
                if msg.date.replace(tzinfo=None) < since:
                    break
                if msg.text and len(msg.text.strip()) > 10:
                    messages.append(msg)

            print(f"  → {len(messages)}개 메시지")
            if not messages:
                continue

            texts = []
            for msg in messages:
                try:
                    cur.execute("""
                        INSERT OR IGNORE INTO telegram_messages
                        (channel, message_id, text, date, collected_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (channel, msg.id, msg.text,
                          msg.date.strftime('%Y-%m-%d %H:%M:%S'),
                          now.strftime('%Y-%m-%d %H:%M:%S')))
                    texts.append(msg.text)
                except Exception as e:
                    print(f"  DB 오류: {e}")
            conn.commit()

            print(f"  → OpenAI 분석 중...")
            result  = analyze_messages(channel, texts)
            summary = result.get("summary", "")
            stocks  = result.get("stocks", [])

            if messages:
                cur.execute("""
                    UPDATE telegram_messages SET summary=?, stocks=?
                    WHERE channel=? AND message_id=?
                """, (summary, json.dumps(stocks, ensure_ascii=False),
                      channel, messages[0].id))
            conn.commit()

            all_stocks.extend(stocks)
            channel_summaries.append({
                "channel": channel, "count": len(messages),
                "summary": summary, "stocks": stocks,
            })
            print(f"  → 완료 | 종목: {[s['name'] for s in stocks]}")

        except Exception as e:
            print(f"  [오류] {channel}: {e}")

    await client.disconnect()
    conn.close()

    # 일별 누적 저장
    save_daily_mentions(today_str, all_stocks)

    # 리포트 전송
    stock_counter = Counter()
    stock_market  = {}
    for s in all_stocks:
        name = s.get("name", "")
        if name:
            stock_counter[name] += 1
            stock_market[name]   = s.get("market", "미확인")

    top_stocks = stock_counter.most_common(20)
    report     = build_report(now, since, channel_summaries, top_stocks, stock_market)
    print("\n" + report)

    if BOT_TOKEN and TARGET_CHANNEL_ID:
        try:
            await send_result(report)
            print("\n✅ 텔레그램 전송 완료")
        except Exception as e:
            print(f"\n⚠️ 텔레그램 전송 실패: {e}")

    print("\n✅ 완료")

if __name__ == "__main__":
    asyncio.run(collect_and_analyze())
