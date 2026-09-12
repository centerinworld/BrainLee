#!/usr/bin/env python3
"""
daily_monitor.py — 매일 기사 수집·분류·텔레그램 브리핑 품질 자동 점검
결과를 텔레그램으로 전송한다.
"""

import sqlite3
import os
import json
import sys
import urllib.request
import re
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "data/ceo_briefing.db")

KST = timezone(timedelta(hours=9))


# ── 유틸 ────────────────────────────────────────────────────────────────────

def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def _has_kw(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)


# ── KAI 핵심 기사 여부 (DB-독립 간이 판별) ─────────────────────────────────

KAI_COMPANY_KW  = ["한국항공우주산업", "한국항공우주", "kai"]
KAI_PRODUCT_KW  = ["kf-21", "fa-50", "t-50", "수리온", "lah", "마린온", "cas500"]
KAI_BUSINESS_KW = ["수출", "수주", "양산", "전력화", "납품", "민영화", "지분", "인수", "m&a",
                   "경영", "대표", "ceo", "실적", "영업이익", "매출"]
HANWHA_DEFENSE_KW = ["한화에어로", "한화시스템", "한화오션", "한화디펜스"]
NON_KAI_NOISE  = ["삼성전자", "sk하이닉스", "kb국민", "현대건설", "에코프로",
                  "항비만", "여성ceo", "인서울", "코스피 지수선물", "대원전선",
                  "유림테크", "세일즈포스"]

AVIATION_DEFENSE_KW = ["전투기", "헬기", "무인기", "드론", "방산", "방위", "수출", "수주",
                       "미사일", "유도무기", "레이더", "항공기", "공군", "해군", "육군"]

def is_kai_relevant(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    if _has_kw(text, NON_KAI_NOISE):
        return False
    has_company  = _has_kw(title.lower(), KAI_COMPANY_KW)
    has_product  = _has_kw(title.lower(), KAI_PRODUCT_KW)
    has_business = _has_kw(text, KAI_BUSINESS_KW)
    # 제목에 KAI/제품명 + 방산/항공 키워드만 있어도 관련 기사로 인정
    has_defense  = _has_kw(text, AVIATION_DEFENSE_KW)
    if (has_company or has_product) and has_defense:
        return True
    return (has_company or has_product) and has_business


def is_hanwha_defense_relevant(title: str) -> bool:
    t = title.lower()
    # 방산 계열사 명시 OR 방산 키워드 포함이면 관련 기사
    return _has_kw(t, HANWHA_DEFENSE_KW) or _has_kw(t, AVIATION_DEFENSE_KW)


# ── 중복 감지 ────────────────────────────────────────────────────────────────

def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_duplicates_today(conn: sqlite3.Connection, today_utc: str) -> list[tuple[str, str]]:
    rows = conn.execute(
        """SELECT title, article_category FROM feed_items
           WHERE COALESCE(article_published_at,'') >= ? AND article_category != 'trash'
           ORDER BY article_published_at""",
        (today_utc,),
    ).fetchall()
    seen: list[str] = []
    dups: list[tuple[str, str]] = []
    for row in rows:
        title = row[0] or ""
        for prev in seen:
            if title_similarity(title, prev) >= 0.82:
                dups.append((prev, title))
                break
        else:
            seen.append(title)
    return dups


# ── 메인 점검 로직 ───────────────────────────────────────────────────────────

def run_monitor(dry_run: bool = False) -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    now_kst   = datetime.now(KST)
    today_str = now_kst.strftime("%Y%m%d")
    today_iso = now_kst.date().isoformat()

    # UTC 오늘 자정 기준 (기사 날짜 비교용)
    today_kst_midnight = datetime(now_kst.year, now_kst.month, now_kst.day, tzinfo=KST)
    today_utc = today_kst_midnight.astimezone(timezone.utc).isoformat()

    issues:  list[str] = []
    ok_list: list[str] = []

    # ── 1. 오늘 수집 기사 통계 ──────────────────────────────────────────────
    rows = conn.execute(
        """SELECT article_category, COUNT(*) as cnt FROM feed_items
           WHERE COALESCE(article_published_at,'') >= ?
           GROUP BY article_category""",
        (today_utc,),
    ).fetchall()
    counts = {r["article_category"]: r["cnt"] for r in rows}
    total  = sum(counts.values())

    kai_cnt = counts.get("kai", 0)
    hw_cnt  = counts.get("hanwha", 0)
    lig_cnt = counts.get("lig", 0)
    gov_cnt = counts.get("government", 0)
    spc_cnt = counts.get("space", 0)
    ref_cnt = counts.get("reference", 0)

    stats_line = (f"KAI {kai_cnt} / 정부 {gov_cnt} / 한화 {hw_cnt} / "
                  f"LIG {lig_cnt} / 우주 {spc_cnt} / 협력사 {ref_cnt}")

    if total < 3:
        issues.append(f"🚨 오늘 수집 기사 {total}건 — RSS 수집이 실행되지 않았을 가능성")
    else:
        ok_list.append(f"✅ 오늘 수집 {total}건 ({stats_line})")

    # ── 2. RSS 마지막 수집 시각 ──────────────────────────────────────────────
    for ftype in ("company", "competitor", "government"):
        val = get_setting(conn, f"rss_last_checked_{ftype}")
        if val:
            try:
                checked_dt = datetime.fromisoformat(val).astimezone(KST)
                hours_ago  = (now_kst - checked_dt).total_seconds() / 3600
                if hours_ago > 25:
                    issues.append(f"⚠️ [{ftype}] RSS 마지막 수집 {hours_ago:.0f}시간 전 — 수집 중단 가능성")
            except Exception:
                pass

    # ── 3. 텔레그램 브리핑 발송 확인 ────────────────────────────────────────
    weekday = now_kst.weekday()  # 0=월 … 6=일
    is_weekday = weekday < 5

    briefing_keys_expected = []
    if is_weekday:
        briefing_keys_expected = [
            (f"telegram_last_briefing_{today_str}_12", "12시"),
            (f"telegram_last_briefing_{today_str}_18", "18시"),
        ]
    else:
        briefing_keys_expected = [
            (f"telegram_last_briefing_{today_str}_18", "18시(주말)"),
        ]

    for key, label in briefing_keys_expected:
        expected_hour = int(label[:2])
        val = get_setting(conn, key)
        if now_kst.hour > expected_hour and not val:
            issues.append(f"🚨 {label} 텔레그램 브리핑 미발송")
        elif val:
            ok_list.append(f"✅ {label} 브리핑 발송 완료")

    # ── 4. 분류 품질 점검 ────────────────────────────────────────────────────
    today_articles = conn.execute(
        """SELECT id, title, summary, article_category, article_publisher, category_manual
           FROM feed_items
           WHERE COALESCE(article_published_at,'') >= ? AND category_manual = 0""",
        (today_utc,),
    ).fetchall()

    bad_kai: list[str] = []
    bad_hanwha: list[str] = []

    for r in today_articles:
        title   = r["title"] or ""
        summary = r["summary"] or ""
        cat     = r["article_category"]

        # kai인데 KAI 관련성 없는 기사
        if cat == "kai" and not is_kai_relevant(title, summary):
            bad_kai.append(title[:55])

        # hanwha인데 방산 계열사 아닌 기사
        if cat == "hanwha" and not is_hanwha_defense_relevant(title):
            if not _has_kw(title.lower(), ["한화에어로", "한화시스템", "한화오션"]):
                bad_hanwha.append(title[:55])

    if bad_kai:
        issues.append(f"⚠️ [KAI] 오분류 의심 {len(bad_kai)}건:")
        for t in bad_kai[:5]:
            issues.append(f"    · {t}")

    if bad_hanwha:
        issues.append(f"⚠️ [한화] 방산 무관 기사 {len(bad_hanwha)}건:")
        for t in bad_hanwha[:3]:
            issues.append(f"    · {t}")

    if not bad_kai and not bad_hanwha:
        ok_list.append("✅ 오늘 분류 품질 이상 없음")

    # ── 5. 중복 기사 감지 ────────────────────────────────────────────────────
    dups = find_duplicates_today(conn, today_utc)
    if dups:
        issues.append(f"⚠️ 중복 의심 기사 {len(dups)}쌍:")
        for a, b in dups[:3]:
            issues.append(f"    · {a[:40]} ↔ {b[:40]}")
    else:
        ok_list.append("✅ 중복 기사 없음")

    # ── 6. 오늘 텔레그램 발송된 기사 중 문제 기사 재확인 ──────────────────
    sent_today = conn.execute(
        """SELECT title, article_category FROM feed_items
           WHERE sent_briefing_at >= ? ORDER BY sent_briefing_at""",
        (today_utc,),
    ).fetchall()

    bad_sent = []
    for r in sent_today:
        title = r["title"] or ""
        cat   = r["article_category"]
        if cat == "kai" and not is_kai_relevant(title, ""):
            bad_sent.append(f"[{cat}] {title[:50]}")
        if cat == "trash":
            bad_sent.append(f"[trash→발송됨!] {title[:50]}")

    if bad_sent:
        issues.append(f"🚨 오늘 브리핑에 오분류 기사 포함 {len(bad_sent)}건:")
        for t in bad_sent[:5]:
            issues.append(f"    · {t}")

    # ── 7. 리포트 조립 ──────────────────────────────────────────────────────
    conn.close()

    header = f"🔍 <b>KAI 브리핑 일일 점검 ({today_iso})</b>"
    lines  = [header, ""]

    if ok_list:
        lines += ok_list

    if issues:
        lines += ["", "━━━━━ 🚨 이슈 감지 ━━━━━"]
        lines += issues
        lines += ["", "→ 확인 후 수동 조치 또는 Claude에 요청하세요."]
    else:
        lines += ["", "━━━━━ 이슈 없음 ✅ ━━━━━"]
        lines += ["모든 항목 정상입니다."]

    report = "\n".join(lines)
    return report


# ── 엔트리포인트 ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    report = run_monitor()
    print(report)
