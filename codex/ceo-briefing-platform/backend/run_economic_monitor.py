#!/usr/bin/env python3
"""
📊 대한민국 경제 지표 모니터링 시스템 - 메인 실행 스크립트
=============================================================

사용법:
    # 1. .env 파일 준비 (API 키 입력)
    cp .env.example .env   # 또는 직접 .env 파일 생성
    
    # 2. DB 초기화 (최초 1회)
    python run_economic_monitor.py init
    
    # 3. 데이터 수집 + 텔레그램 전송 1회 실행
    python run_economic_monitor.py run
    
    # 4. 스케줄러 데몬 실행 (60분 간격 수집)
    python run_economic_monitor.py daemon
    
    # 5. 텔레그램 봇 실행 (폴링 모드)
    python run_economic_monitor.py bot
    
    # 6. 일일 요약만 전송
    python run_economic_monitor.py daily
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def load_env():
    """.env 파일 로드"""
    # 여러 경로에서 .env 탐색
    env_paths = [
        Path(__file__).resolve().parent.parent / ".env",       # backend/../.env
        Path(__file__).resolve().parent.parent / ".env.example",  # 백업
        Path(__file__).resolve().parent / ".env",              # backend/.env
    ]
    
    env_file = None
    for p in env_paths:
        if p.exists():
            env_file = p
            break
    
    if not env_file:
        print("⚠️  .env 파일을 찾을 수 없습니다.")
        print("   프로젝트 루트(ceo-briefing-platform/)에 .env 파일을 생성해주세요.")
        print("   필요한 설정:")
        print("     TELEGRAM_BOT_TOKEN=봇토큰")
        print("     TELEGRAM_CHAT_ID=챗ID")
        print("     BOK_API_KEY=한국은행API키")
        print("     KOSIS_API_KEY=통계청API키 (선택)")
        return False

    print(f"📁 .env 파일 로드: {env_file}")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
    
    # 필수값 확인
    checks = {
        "TELEGRAM_BOT_TOKEN": "텔레그램 봇 토큰",
        "TELEGRAM_CHAT_ID": "텔레그램 채팅 ID",
        "BOK_API_KEY": "한국은행 ECOS API 키",
    }
    
    all_ok = True
    for key, desc in checks.items():
        if not os.environ.get(key):
            print(f"⚠️  {desc}({key})가 설정되지 않았습니다.")
            all_ok = False
    
    return all_ok


def print_banner():
    print("""
╔══════════════════════════════════════════════╗
║   📊 대한민국 경제 지표 모니터링 시스템      ║
║   Economic Indicator Monitoring System       ║
╚══════════════════════════════════════════════╝
    """)


def main():
    print_banner()
    
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    command = sys.argv[1]
    
    # .env 로드 (init 제외)
    if command != "init":
        load_env()
    
    # 경로 설정
    backend_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(backend_dir))
    
    if command == "init":
        # DB 초기화
        from economic_stats.eco_db import initialize_database
        initialize_database()
        print("\n✅ 경제지표 DB 초기화 완료!")
        print("   데이터베이스 위치: backend/data/economic_indicators.db")
        print("\n📋 다음 단계:")
        print("   1. python run_economic_monitor.py fetch  (데이터 수집)")
        print("   2. python run_economic_monitor.py run    (수집 + 알림)")
        
    elif command == "fetch":
        # 데이터 수집만
        from economic_stats.fetchers import fetch_all
        results = fetch_all()
        total = sum(results.values())
        print(f"\n✅ 데이터 수집 완료: 총 {total}건")
        
    elif command == "run":
        # 1회 수집 + 알림
        from economic_stats.scheduler import run_fetch_and_alert_once
        results = run_fetch_and_alert_once()
        total = sum(results.get("fetch", {}).values())
        print(f"\n✅ 실행 완료: 수집 {total}건, 알림 {results.get('alerts', 0)}건")
        
    elif command == "daemon":
        # 데몬 모드
        from economic_stats.scheduler import EconomicIndicatorScheduler
        scheduler = EconomicIndicatorScheduler(fetch_interval_minutes=60)
        scheduler.run_forever()
        
    elif command == "bot":
        # 텔레그램 봇 (폴링)
        from economic_bot import run_polling
        run_polling()
        
    elif command in ("daily", "summary"):
        # 일일 요약
        from economic_stats.scheduler import send_daily_summary, send_change_report
        from economic_stats.eco_db import connect as eco_connect
        conn = eco_connect()
        try:
            send_daily_summary(conn)
            send_change_report(conn, days=1)
        finally:
            conn.close()
        print("✅ 일일 요약 전송 완료")
        
    elif command == "webhook":
        # 웹훅 설정
        from economic_bot import set_webhook, delete_webhook
        webhook_url = os.environ.get("WEBHOOK_URL", "")
        if webhook_url:
            delete_webhook()
            set_webhook(webhook_url)
        else:
            print("❌ WEBHOOK_URL 환경변수가 설정되지 않음")
        
    else:
        print(f"❌ 알 수 없는 명령어: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
