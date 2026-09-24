"""
presidential_email_dispatcher.py — 대통령급 일일 인텔리전스 PDF 및 JPG 인포그래픽 이메일 & 텔레그램 자동 발송기

[우선순위 원칙]
1. PDF 형식 최우선 첨부 (application/pdf)
2. JPG/PNG 고화질 이미지 인라인 엠베딩 및 보조 첨부
3. 텔레그램(@Going_To_Skybot)으로 고화질 인포그래픽 사진(sendPhoto) 및 원본 PDF(sendDocument) 전송
"""

import os
import sys
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication
from pathlib import Path
import datetime
import requests

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
REPORTS_DIR = WORKSPACE_ROOT / "presidential_reports"
sys.path.insert(0, str(WORKSPACE_ROOT))
import config

TARGET_EMAILS = [
    "hyojun22@koreaaero.com",  # KAI 공식 사내 이메일 (최우선 수신)
    getattr(config, "STOCKEASY_EMAIL", "") or "center.in.world@gmail.com"
]
TARGET_EMAILS = list(dict.fromkeys([e.strip() for e in TARGET_EMAILS if e.strip()]))

SMTP_SERVER = getattr(config, "SMTP_SERVER", "") or os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(getattr(config, "SMTP_PORT", 587) or 587)
SMTP_USER = getattr(config, "SMTP_USER", "") or os.getenv("SMTP_USER", "")
SMTP_PASSWORD = getattr(config, "SMTP_PASSWORD", "") or os.getenv("SMTP_PASSWORD", "")

TELEGRAM_BOT_TOKEN = getattr(config, "TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = getattr(config, "TELEGRAM_CHAT_ID", "")


def send_presidential_brief_email(report_data: dict, html_path: Path = None, pdf_path: Path = None, img_path: Path = None) -> bool:
    """BCG 전략 인텔리전스 A4 PDF 최우선 첨부 + JPG 인라인 엠베딩 이메일 발송"""
    title = report_data.get("title", "BCG 전략 인텔리전스 브리핑 (KAI & 자본시장 전략)")
    brief_type = report_data.get("brief_type", "DAILY")
    date_str = report_data.get("date_str", "")
    bluf = report_data.get("bluf", "")

    subject = f"📊 [BCG 전략 브리핑 PDF] {title} — {bluf[:35]}..."

    # Fallback paths if not provided
    if not pdf_path:
        candidate_pdf = REPORTS_DIR / f"pdb_infographic_{brief_type.lower()}_latest.pdf"
        if candidate_pdf.exists():
            pdf_path = candidate_pdf

    if not img_path:
        candidate_img = REPORTS_DIR / f"pdb_infographic_{brief_type.lower()}_latest.jpg"
        if candidate_img.exists():
            img_path = candidate_img

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"BCG 전략 인텔리전스 자문그룹 <{SMTP_USER or 'noreply@newsinfo.cloud'}>"
    msg["To"] = ", ".join(TARGET_EMAILS)

    # 1. HTML 본문 파트 (인라인 이미지 포함)
    alt_part = MIMEMultipart("related")
    
    html_body = f"""
    <div style="font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width:860px; margin:0 auto; padding:16px; background:#080d1a; color:#ffffff; border-radius:10px;">
      <div style="border-bottom:2px solid #10b981; padding-bottom:12px; margin-bottom:14px;">
        <span style="background:#10b981; color:#042f2e; font-size:11px; font-weight:800; padding:3px 8px; border-radius:4px;">◆ BCG STRATEGY BRIEF // A4 PDF 우선 첨부</span>
        <h2 style="color:#f8fafc; font-size:20px; margin:8px 0 4px 0;">{title}</h2>
        <div style="font-size:12px; color:#94a3b8;">보고일시: {date_str} | 수신: {', '.join(TARGET_EMAILS)}</div>
      </div>
      
      <div style="background:rgba(16, 185, 129, 0.12); border-left:4px solid #10b981; padding:12px; border-radius:4px; margin-bottom:16px;">
        <div style="font-size:11px; font-weight:800; color:#34d399;">💡 EXECUTIVE SUMMARY (핵심 전략 명제)</div>
        <div style="font-size:14px; font-weight:700; color:#ffffff; margin-top:4px;">"{bluf}"</div>
      </div>

      <div style="margin-bottom:16px; text-align:center;">
        <p style="font-size:12px; color:#94a3b8; margin-bottom:8px;">👇 [인포그래픽 1장 미리보기 — 정본 고화질 A4 PDF는 첨부파일을 확인하세요]</p>
        <img src="cid:pdb_infographic_img" alt="BCG Strategy 1-Page Infographic" style="width:100%; max-width:820px; border-radius:8px; border:1px solid #1e293b; box-shadow:0 8px 20px rgba(0,0,0,0.4);" />
      </div>

      <div style="border-top:1px solid #1e293b; padding-top:10px; font-size:11px; color:#64748b; text-align:center;">
        📁 A4 규격 원본 고화질 PDF가 상단 첨부파일에 포함되어 있습니다. | 웹 관제 센터: <a href="https://newsinfo.cloud/kai/#handoff" style="color:#34d399;">newsinfo.cloud/kai</a>
      </div>
    </div>
    """
    alt_part.attach(MIMEText(html_body, "html", "utf-8"))

    # 인라인 JPG 이미지 첨부 (cid:pdb_infographic_img)
    if img_path and img_path.exists():
        try:
            with open(img_path, "rb") as f:
                img_data = MIMEImage(f.read(), _subtype="jpeg")
                img_data.add_header("Content-ID", "<pdb_infographic_img>")
                img_data.add_header("Content-Disposition", "inline", filename=img_path.name)
                alt_part.attach(img_data)
        except Exception as e:
            print("⚠️ [Image Inline Error]", e)

    msg.attach(alt_part)

    # 2. [최우선] 원본 고화질 PDF 파일 첨부 (MIMEApplication)
    if pdf_path and pdf_path.exists():
        try:
            with open(pdf_path, "rb") as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
                pdf_attachment.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
                msg.attach(pdf_attachment)
                print(f"📎 [Email Dispatcher] Attached High-Res PDF: {pdf_path.name}")
        except Exception as e:
            print("⚠️ [PDF Attachment Error]", e)

    # 이메일 발송 실행
    email_sent = False
    if SMTP_USER and SMTP_PASSWORD:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=12) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
                print(f"📧 [Email Dispatcher] Sent PDF & Infographic to {', '.join(TARGET_EMAILS)} via SMTP!")
                email_sent = True
        except Exception as e:
            print(f"⚠️ [Email Dispatcher] SMTP error: {e}")
    else:
        outbox_file = REPORTS_DIR / f"outbox_email_pdf_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.eml"
        with open(outbox_file, "w", encoding="utf-8") as f:
            f.write(msg.as_string())
        print(f"📁 [Email Dispatcher] PDF & Infographic Email packaged to outbox: {outbox_file}")
        email_sent = True

    # 텔레그램 동시 푸시 (사진 + PDF 문서 전송)
    send_presidential_brief_telegram(report_data, pdf_path, img_path)
    return email_sent


def send_presidential_brief_telegram(report_data: dict, pdf_path: Path = None, img_path: Path = None) -> bool:
    """텔레그램(@Going_To_Skybot)으로 고화질 인포그래픽 사진 및 PDF 문서 직접 전송 (00:00~06:00 수면 시간 차단)"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    # 🌙 사용자 수면 시간(12시~06시 / 00:00~06:00 KST) 텔레그램 알림 자동 차단
    now = datetime.datetime.now()
    if 0 <= now.hour < 6:
        print(f"🌙 [Telegram Quiet Hours] 현재 시각({now.strftime('%H:%M')})은 수면 시간(00:00~06:00)입니다. 텔레그램 발송을 차단합니다. (이메일은 정상 발송됨)")
        return False

    title = report_data.get("title", "")
    date_str = report_data.get("date_str", "")
    bluf = report_data.get("bluf", "")
    risk_gauge = report_data.get("risk_gauge", "중립")
    risk_score = report_data.get("risk_gauge_score", 50)
    actions = report_data.get("strategic_actions", [])
    actions_text = "\n".join([f"  {idx+1}. {a}" for idx, a in enumerate(actions)])

    caption = (
        f"📊 <b>[BCG STRATEGIC INTELLIGENCE BRIEFING]</b>\n"
        f"<b>{title}</b>\n"
        f"🕒 보고일시: {date_str}\n"
        f"📈 <b>전략 변동성 지수:</b> <code>{risk_gauge} ({risk_score}점)</code>\n\n"
        f"💡 <b>EXECUTIVE SUMMARY (핵심 전략 명제):</b>\n"
        f"<i>\"{bluf}\"</i>\n\n"
        f"🎯 <b>C-Level 최고경영진 우선 실행 아젠다:</b>\n"
        f"{actions_text}\n\n"
        f"📩 <i>수신처: hyojun22@koreaaero.com A4 PDF 첨부 발송</i>\n"
        f"🌐 <i>관제 센터: https://newsinfo.cloud/kai/#handoff</i>"
    )

    # 📱 텔레그램 메시지는 딱 1개(Single Message)만 발송 (인포그래픽 이미지 + 핵심 캡션 통합)
    if img_path and img_path.exists():
        try:
            with open(img_path, "rb") as photo:
                r = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"photo": photo},
                    timeout=15
                )
                if r.ok:
                    print("📱 [Telegram Dispatcher] Sent Single Unified Infographic Photo successfully!")
                    return True
        except Exception as e:
            print("⚠️ [Telegram Photo Error]", e)

    # 이미지가 없을 경우에만 PDF 문서 단일 발송 (Fallback)
    if pdf_path and pdf_path.exists():
        try:
            with open(pdf_path, "rb") as doc:
                r = requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
                    files={"document": (pdf_path.name, doc, "application/pdf")},
                    timeout=20
                )
                if r.ok:
                    print(f"📄 [Telegram Dispatcher] Sent Single PDF Document ({pdf_path.name}) successfully!")
                    return True
        except Exception as e:
            print("⚠️ [Telegram Document Error]", e)

    return True


if __name__ == "__main__":
    latest_json = REPORTS_DIR / "pdb_morning_latest.json"
    latest_pdf = REPORTS_DIR / "pdb_infographic_morning_latest.pdf"
    latest_img = REPORTS_DIR / "pdb_infographic_morning_latest.jpg"

    if latest_json.exists():
        with open(latest_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        send_presidential_brief_email(data, None, latest_pdf, latest_img)
    else:
        print("No report to dispatch.")
