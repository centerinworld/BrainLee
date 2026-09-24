"""
presidential_brief_scheduler.py — 대통령급 일일 2회 인텔리전스 보고서(PDB) 무인 정기 스케줄러

동작 주기:
1. 매일 오전 07:30 — 【모닝 전략 브리핑】 (미 증시/환율/유가/야간 지정학 ➔ 개장 전 PDB 1장 인포그래픽)
2. 매일 오후 18:00 — 【이브닝 마감 브리핑】 (KRX 마감/외인 수급/방산 수주 공시 ➔ 마감 PDB 1장 인포그래픽)
3. 수동 즉시 실행 CLI 지원: `python presidential_brief_scheduler.py run_now [MORNING|EVENING]`
"""

import os
import sys
import time
import datetime
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
sys.path.insert(0, str(WORKSPACE_ROOT))

from presidential_brief_engine import generate_presidential_daily_brief
from presidential_infographic_builder import render_and_save_infographic
from presidential_email_dispatcher import send_presidential_brief_email


def run_full_presidential_pipeline(brief_type: str = "MORNING"):
    """PDB 생성 ➔ 1장 인포그래픽 빌드 ➔ 이메일 & 텔레그램 발송 원스톱 실행"""
    print(f"\n👑 [Presidential Pipeline] Starting {brief_type} briefing at {datetime.datetime.now()}...")
    try:
        # 1. 보고서 데이터 생성
        report_data = generate_presidential_daily_brief(brief_type)
        
        # 2. 1장 인포그래픽 및 NotebookLM 소스북 빌드
        html_path, sourcebook_path, pdf_path, img_path = render_and_save_infographic(report_data, brief_type)
        
        # 3. 이메일 및 텔레그램 발송
        send_presidential_brief_email(report_data, html_path, pdf_path, img_path)
        
        print(f"🎉 [Presidential Pipeline] {brief_type} briefing completed successfully!\n")
        return {
            "status": "success",
            "brief_type": brief_type,
            "html_path": str(html_path),
            "sourcebook_path": str(sourcebook_path)
        }
    except Exception as e:
        print(f"❌ [Presidential Pipeline Error] {e}")
        return {"status": "error", "message": str(e)}


def scheduler_loop():
    """1일 2회 (07:30, 18:00) 정기 스케줄러 루프"""
    print("⏰ [PDB Scheduler] Daemon started. Monitoring for 07:30 (Morning) and 18:00 (Evening)...")
    last_run_date_morning = ""
    last_run_date_evening = ""

    while True:
        try:
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            hour = now.hour
            minute = now.minute

            # 1. 모닝 브리핑: 07:30 ~ 07:35
            if hour == 7 and minute >= 30 and last_run_date_morning != today_str:
                print(f"⏰ [PDB Scheduler] Triggering Morning Briefing for {today_str}...")
                run_full_presidential_pipeline("MORNING")
                last_run_date_morning = today_str

            # 2. 이브닝 마감 브리핑: 18:00 ~ 18:05
            if hour == 18 and minute >= 0 and last_run_date_evening != today_str:
                print(f"⏰ [PDB Scheduler] Triggering Evening Briefing for {today_str}...")
                run_full_presidential_pipeline("EVENING")
                last_run_date_evening = today_str

            time.sleep(30)  # 30초마다 체크
        except KeyboardInterrupt:
            print("PDB Scheduler interrupted.")
            break
        except Exception as e:
            print(f"Scheduler loop error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run_now":
        b_type = sys.argv[2].upper() if len(sys.argv) > 2 else "MORNING"
        run_full_presidential_pipeline(b_type)
    else:
        scheduler_loop()
