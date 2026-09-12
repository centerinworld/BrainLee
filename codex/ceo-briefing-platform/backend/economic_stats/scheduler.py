"""
⏰ 경제 지표 수집/알림 스케줄러
=================================
주기적으로 데이터를 수집하고 텔레그램으로 알림 전송
"""

from __future__ import annotations

import json
import os
import time
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .eco_db import (
    SEOUL,
    connect as eco_connect,
    check_thresholds,
    generate_market_summary,
    generate_change_report,
    get_latest_values_all,
    get_recent_significant_changes,
    get_latest_value,
    log_telegram,
    initialize_database,
)
from .fetchers import fetch_all


# ============================================================
# 텔레그램 알림 전송
# ============================================================


def get_telegram_settings() -> tuple[str, str]:
    """환경변수에서 텔레그램 설정 로드"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def send_telegram_message(message: str, parse_mode: str = "HTML") -> bool:
    """텔레그램 메시지 전송"""
    token, chat_id = get_telegram_settings()
    if not token or not chat_id:
        print("[WARN] 텔레그램 설정이 되어있지 않습니다. .env 파일을 확인하세요.")
        return False

    # HTML 태그가 너무 길면 텔레그램이 막을 수 있으므로, 4000자 제한
    if len(message) > 4000:
        # 여러 메시지로 분할
        parts = []
        current = ""
        for line in message.split("\n"):
            if len(current) + len(line) + 1 > 3900:
                parts.append(current)
                current = line
            else:
                current += ("\n" + line) if current else line
        if current:
            parts.append(current)
        
        success = True
        for part in parts:
            if not _send_single_message(part, parse_mode):
                success = False
        return success
    
    return _send_single_message(message, parse_mode)


def _send_single_message(message: str, parse_mode: str = "HTML") -> bool:
    """단일 텔레그램 메시지 전송"""
    token, chat_id = get_telegram_settings()
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15):
            return True
    except urllib.error.HTTPError as e:
        print(f"[ERROR] 텔레그램 전송 실패 (HTTP {e.code}): {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"[ERROR] 텔레그램 전송 실패: {e}")
        return False


# ============================================================
# 알림 처리
# ============================================================


def check_and_send_alerts(conn: sqlite3.Connection) -> int:
    """임계치 기반 알림 확인 및 전송"""
    values = get_latest_values_all(conn)
    alert_count = 0
    
    for v in values:
        if v["value"] is None:
            continue
        
        indicator_code = v["indicator_code"]
        value = v["value"]
        change_rate = v["change_rate"]
        
        triggered = check_thresholds(conn, indicator_code, value, change_rate)
        for alert in triggered:
            message = alert["message"]
            sent = send_telegram_message(message)
            log_telegram(conn, indicator_code, message, "sent" if sent else "failed")
            if sent:
                alert_count += 1

    return alert_count


def send_daily_summary(conn: sqlite3.Connection) -> bool:
    """일일 경제 요약 전송"""
    summary = generate_market_summary(conn)
    sent = send_telegram_message(summary)
    if sent:
        log_telegram(conn, "", "DAILY_SUMMARY", "sent")
    return sent


def send_change_report(conn: sqlite3.Connection, days: int = 1) -> bool:
    """변동 사항 보고 전송"""
    report = generate_change_report(conn, days=days)
    if report is None:
        return False
    sent = send_telegram_message(report)
    if sent:
        log_telegram(conn, "", "CHANGE_REPORT", "sent")
    return sent


# ============================================================
# 스케줄러 메인 로직
# ============================================================


class EconomicIndicatorScheduler:
    """경제 지표 스케줄러"""

    def __init__(self, fetch_interval_minutes: int = 60):
        self.fetch_interval = timedelta(minutes=fetch_interval_minutes)
        self.last_fetch_time: Optional[datetime] = None
        self.last_alert_time: Optional[datetime] = None
        self.last_summary_date: Optional[str] = None
        self.running = True

    def run_once(self) -> Dict[str, Any]:
        """스케줄러 1회 실행"""
        conn = eco_connect()
        now = datetime.now(SEOUL)
        results: Dict[str, Any] = {
            "fetch_count": 0,
            "alerts_sent": 0,
            "summary_sent": False,
            "change_report_sent": False,
        }

        try:
            # 데이터 수집
            print(f"\n🔄 데이터 수집 시작... ({now.strftime('%H:%M')})")
            fetch_results = fetch_all(conn)
            results["fetch_count"] = sum(fetch_results.values())

            # 임계치 알림 확인
            print("🔔 알림 조건 확인...")
            alert_count = check_and_send_alerts(conn)
            results["alerts_sent"] = alert_count

            # 일일 요약 (오전 8시 이후 최초 1회)
            current_date = now.strftime("%Y-%m-%d")
            if current_date != self.last_summary_date and now.hour >= 8:
                print("📊 일일 경제 요약 전송...")
                results["summary_sent"] = send_daily_summary(conn)
                self.last_summary_date = current_date

                # 변동 리포트
                print("🔄 변동 리포트 전송...")
                results["change_report_sent"] = send_change_report(conn, days=1)

            # 저녁 브리핑 (18-20시 사이)
            if 18 <= now.hour <= 20 and current_date != getattr(self, "_evening_summary_date", None):
                print("🌆 저녁 경제 브리핑 전송...")
                sent = send_daily_summary(conn)
                if sent:
                    self._evening_summary_date = current_date
                    results["summary_sent"] = True

        except Exception as e:
            print(f"[ERROR] 스케줄러 실행 중 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            conn.close()

        return results

    def run_forever(self):
        """무한 루프 실행"""
        print("=" * 50)
        print("📊 경제 지표 모니터링 스케줄러 시작")
        print(f"🕐 {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⏱ 수집 주기: {self.fetch_interval.seconds // 60}분")
        print("=" * 50)

        while self.running:
            now = datetime.now(SEOUL)
            
            # 정해진 시간마다 수집 실행
            should_fetch = False
            if self.last_fetch_time is None:
                should_fetch = True
            elif now - self.last_fetch_time >= self.fetch_interval:
                should_fetch = True

            if should_fetch:
                try:
                    self.run_once()
                    self.last_fetch_time = now
                except Exception as e:
                    print(f"[ERROR] 수집 루프 오류: {e}")
                
                print(f"\n⏳ 다음 수집 대기 중... ({self.fetch_interval.seconds // 60}분 후)")
            
            # 60초 대기
            time.sleep(60)


# ============================================================
# 일회성 실행 함수
# ============================================================


def run_fetch_and_alert_once() -> Dict[str, Any]:
    """일회성 실행 (수동/크론용)"""
    conn = eco_connect()
    results: Dict[str, Any] = {"fetch": {}, "alerts": 0, "summary": False}
    
    try:
        results["fetch"] = fetch_all(conn)
        results["alerts"] = check_and_send_alerts(conn)
        results["summary"] = send_daily_summary(conn)
        send_change_report(conn, days=1)
    finally:
        conn.close()
    
    return results


# ============================================================
# 메인 실행
# ============================================================


if __name__ == "__main__":
    import sys
    
    # .env 파일 로드 (로컬 환경)
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()

    print("📊 경제 지표 모니터링 시스템")
    print("=" * 50)
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "init":
            # DB 초기화
            initialize_database()
            print("✅ DB 초기화 완료")
        elif command == "fetch":
            # 1회 수집
            results = run_fetch_and_alert_once()
            print(f"\n✅ 수집 완료: 총 {sum(results['fetch'].values())}건")
        elif command == "once":
            # 1회 수집 (대몬 없이)
            results = run_fetch_and_alert_once()
            print(f"\n✅ 완료")
        elif command == "daily":
            # 일일 요약만 전송
            conn = eco_connect()
            send_daily_summary(conn)
            send_change_report(conn, days=1)
            conn.close()
            print("✅ 일일 요약 전송 완료")
        else:
            print(f"사용법: python {sys.argv[0]} [init|fetch|once|daily|daemon]")
            print("  init   - DB 초기화")
            print("  fetch  - 데이터 수집만")
            print("  once   - 수집 + 알림 1회 실행")
            print("  daily  - 일일 요약 전송")
            print("  daemon - 데몬 모드 실행 (기본값)")
    else:
        # 데몬 모드
        scheduler = EconomicIndicatorScheduler(fetch_interval_minutes=60)
        scheduler.run_forever()
