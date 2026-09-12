"""
🤖 경제 지표 텔레그램 봇
=========================
텔레그램 명령어를 통해 경제지표를 조회할 수 있는 봇

명령어:
/start    - 봇 시작
/help     - 도움말
/summary  - 전체 경제지표 요약
/latest   - 최신 지표값 조회
/exchange - 환율 정보
/rate     - 금리 정보
/stock    - 주가 정보
/price    - 물가 정보
/trade    - 무역 정보
/alert    - 알림 임계치 설정 (준비중)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 상대 임포트를 위해 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent))

from economic_stats.eco_db import (
    SEOUL,
    connect as eco_connect,
    get_latest_values_all,
    get_recent_values,
    get_latest_value,
    get_all_indicators,
    get_categories,
)


def get_seoul_timezone():
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))


# ============================================================
# 텔레그램 설정
# ============================================================


def get_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def get_webhook_url() -> str:
    return os.environ.get("WEBHOOK_URL", "")


# ============================================================
# 응답 메시지 생성
# ============================================================


def format_indicator_value(v: Dict[str, Any], show_detail: bool = False) -> str:
    """지표값 포맷팅"""
    name = v.get("indicator_name", "")
    val = v.get("value")
    unit = v.get("unit", "")
    chg = v.get("change_rate")
    date = v.get("date", "")
    category = v.get("category", "")

    if val is None:
        return f"• {name}: 데이터 없음"

    # 값 포맷팅
    if unit == "%":
        val_str = f"{val:.2f}%"
    elif unit in ("P",):
        val_str = f"{val:.0f}P"
    elif unit in ("원",):
        val_str = f"{val:,.0f}원"
    elif unit in ("백만달러",):
        val_str = f"{val:,.0f}백만$"
    elif unit in ("달러",):
        val_str = f"${val:,.0f}"
    else:
        val_str = f"{val:.2f}{unit}"

    # 변화 표시
    change_str = ""
    if chg is not None:
        arrow = "▲" if chg > 0 else "▼" if chg < 0 else "→"
        change_str = f" ({arrow} {abs(chg):.2f}%)"

    detail = ""
    if show_detail and date:
        detail = f" ({date})"

    return f"• <b>{name}</b>: {val_str}{change_str}{detail}"


def build_summary_message() -> str:
    """전체 요약 메시지 생성"""
    conn = eco_connect()
    try:
        values = get_latest_values_all(conn)
    finally:
        conn.close()

    if not values:
        return "📊 경제 지표 데이터가 없습니다.\n데이터 수집을 먼저 실행해주세요."

    lines: List[str] = []
    lines.append("📊 <b>대한민국 주요 경제지표</b>")
    lines.append(f"🕐 {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M')} 기준\n")

    categories = [
        ("exchange_rate", "💱 환율"),
        ("interest_rate", "🏦 금리"),
        ("price", "💰 물가"),
        ("stock", "📈 주식"),
        ("employment", "👔 고용"),
        ("trade", "🚢 무역"),
        ("gdp", "📈 국민소득"),
        ("external", "🌍 대외/외환"),
    ]

    for cat_key, cat_title in categories:
        cat_values = [v for v in values if v["category"] == cat_key]
        if cat_values:
            lines.append(f"\n<u>{cat_title}</u>")
            for v in cat_values:
                formatted = format_indicator_value(v, show_detail=True)
                if formatted:
                    lines.append(formatted)

    lines.append("\n💡 /help - 명령어 도움말")
    return "\n".join(lines)


def build_category_message(category: str) -> Optional[str]:
    """특정 분류 지표 메시지 생성"""
    conn = eco_connect()
    try:
        values = get_latest_values_all(conn)
    finally:
        conn.close()

    cat_values = [v for v in values if v["category"] == category]
    if not cat_values:
        return None

    category_names = {
        "exchange_rate": "💱 환율 정보",
        "interest_rate": "🏦 금리 정보",
        "price": "💰 물가 정보",
        "stock": "📈 주식 정보",
        "employment": "👔 고용 정보",
        "trade": "🚢 무역 정보",
        "gdp": "📈 국민소득 정보",
        "external": "🌍 대외/외환 정보",
    }

    title = category_names.get(category, f"📊 {category}")

    lines = [f"<b>{title}</b>"]
    lines.append(f"🕐 {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M')} 기준\n")

    for v in cat_values:
        formatted = format_indicator_value(v, show_detail=True)
        if formatted:
            lines.append(formatted)

    return "\n".join(lines)


# ============================================================
# 웹훅 핸들러 (Flask/FastAPI 연동용)
# ============================================================


def handle_telegram_update(update: Dict[str, Any]) -> Optional[str]:
    """텔레그램 웹훅 업데이트 처리"""
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return None

    response_text = handle_command(text)

    if response_text:
        # 텔레그램 응답 전송
        token = get_bot_token()
        if token:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": chat_id,
                "text": response_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }).encode("utf-8")
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15):
                    pass
            except Exception as e:
                print(f"[ERROR] 텔레그램 응답 실패: {e}")

    return response_text


def handle_command(text: str) -> Optional[str]:
    """명령어 처리"""
    cmd = text.split()[0].lower()

    if cmd in ("/start", "/help"):
        return (
            "🤖 <b>경제 지표 봇</b>\n\n"
            "사용 가능한 명령어:\n"
            "/summary - 📊 전체 경제지표 요약\n"
            "/latest  - 📈 모든 최신 지표값\n"
            "/exchange - 💱 환율 정보\n"
            "/rate    - 🏦 금리 정보\n"
            "/stock   - 📈 주가 정보\n"
            "/price   - 💰 물가 정보\n"
            "/trade   - 🚢 무역 정보\n"
            "/employ  - 👔 고용 정보\n"
            "/help    - ℹ️ 도움말\n\n"
            "📌 정기 알림은每天早上 08:00 / 저녁 18:00에 전송됩니다."
        )

    if cmd in ("/summary", "/latest"):
        return build_summary_message()

    category_map = {
        "/exchange": "exchange_rate",
        "/rate": "interest_rate",
        "/stock": "stock",
        "/price": "price",
        "/trade": "trade",
        "/employ": "employment",
    }

    if cmd in category_map:
        msg = build_category_message(category_map[cmd])
        if msg:
            return msg
        return f"📭 {cmd} 관련 데이터가 아직 없습니다.\n데이터 수집을 먼저 실행해주세요."

    return None


# ============================================================
# 웹훅 설정/삭제 유틸리티
# ============================================================


def set_webhook(url: str) -> bool:
    """텔레그램 웹훅 설정"""
    token = get_bot_token()
    if not token:
        print("[ERROR] 봇 토큰이 설정되지 않음")
        return False

    api_url = f"https://api.telegram.org/bot{token}/setWebhook"
    payload = json.dumps({"url": url}).encode("utf-8")

    try:
        req = urllib.request.Request(
            api_url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                print(f"✅ 웹훅 설정 완료: {url}")
                return True
            else:
                print(f"[ERROR] 웹훅 설정 실패: {result}")
                return False
    except Exception as e:
        print(f"[ERROR] 웹훅 설정 실패: {e}")
        return False


def delete_webhook() -> bool:
    """텔레그램 웹훅 삭제"""
    token = get_bot_token()
    if not token:
        return False

    api_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        req = urllib.request.Request(api_url, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception:
        return False


# ============================================================
# 폴링 모드 (웹훅 없이 수동 실행)
# ============================================================


def run_polling():
    """폴링 방식으로 봇 실행 (로컬 개발용)"""
    token = get_bot_token()
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")
        print(".env 파일을 확인해주세요.")
        return

    delete_webhook()
    
    print("🤖 경제 지표 봇 시작 (폴링 모드)")
    print("Ctrl+C로 종료")
    print("=" * 40)

    last_update_id = 0
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates?offset={last_update_id + 1}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=35) as resp:
                data = json.loads(resp.read())

            for update in data.get("result", []):
                update_id = update.get("update_id", 0)
                if update_id > last_update_id:
                    last_update_id = update_id
                    handle_telegram_update(update)

        except KeyboardInterrupt:
            print("\n👋 봇 종료")
            break
        except Exception as e:
            print(f"[ERROR] 폴링 오류: {e}")
            import time
            time.sleep(5)


# ============================================================
# 메인
# ============================================================


if __name__ == "__main__":
    # .env 파일 로드
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.example")
    env_actual = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    
    for p in [env_actual, env_path]:
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key.strip()] = value.strip()

    token = get_bot_token()
    if not token:
        print("=" * 50)
        print("❌ 텔레그램 봇 토큰이 설정되지 않았습니다.")
        print("")
        print("1. .env 파일을 생성하고 다음 내용을 입력하세요:")
        print("   TELEGRAM_BOT_TOKEN=your_bot_token")
        print("   TELEGRAM_CHAT_ID=your_chat_id")
        print("")
        print("2. BotFather에서 봇 토큰을 발급받으세요:")
        print("   https://t.me/BotFather")
        print("")
        print("3. 봇을 시작한 후 /start 입력")
        print("=" * 50)
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "webhook":
        webhook_url = get_webhook_url()
        if webhook_url:
            set_webhook(webhook_url)
        else:
            print("[ERROR] WEBHOOK_URL이 설정되지 않음")
    else:
        run_polling()
