#!/usr/bin/env python3
"""
텐버거 헌터 아침 알림 (07:30)

- tenbagger_results 상위 15종목 + OpenAI mini TOP3 심층 분석
- 텔레그램 발송 + reports/tenbagger_alert_YYYYMMDD.md 저장

실행:
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python scripts/tenbagger_morning_alert.py
  /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python scripts/tenbagger_morning_alert.py --top 10 --ai-top 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_utils import STOCK_DB_PATH, connect_stock_db
from telegram_stock_dedup import filter_new as _filter_new_alerts, mark_sent as _mark_alert_sent
from services.gemini import generate_text, is_configured, model_name
from tenbagger_engine import (
    _fetch_extra_signals,
    _fetch_financials,
    _fetch_price_data,
    _fetch_supply,
    _passes_tenbagger_guardrails,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = str(STOCK_DB_PATH)
ALERT_NAMESPACE = "tenbagger_hunter_candidate"
REPORT_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/reports")
REPORT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)


# ── DB 조회 ──────────────────────────────────────────────────────────

def _load_top_candidates(limit: int) -> list[dict]:
    conn = connect_stock_db(timeout=60, row_factory=sqlite3.Row)
    rows = conn.execute("""
        SELECT tr.stock_code, tr.stock_name, tr.total_score AS score, tr.reasons, tr.run_time,
               su.sector_large, su.market, su.market_cap, su.per, su.pbr, su.roe
        FROM tenbagger_results tr
        LEFT JOIN stock_universe su ON su.stock_code = tr.stock_code
        WHERE tr.run_time = (SELECT MAX(run_time) FROM tenbagger_results)
        ORDER BY tr.total_score DESC
        LIMIT ?
    """, (limit,)).fetchall()
    items = [dict(r) for r in rows]
    filtered: list[dict] = []
    for item in items:
        try:
            price = _fetch_price_data(conn, item["stock_code"])
            fin = _fetch_financials(conn, item["stock_code"])
            supply = _fetch_supply(conn, item["stock_code"])
            extra = _fetch_extra_signals(conn, item["stock_code"])
            passed, _ = _passes_tenbagger_guardrails(price, fin, supply, item, extra)
            if passed:
                filtered.append(item)
        except Exception as e:
            log.warning("[알림필터] %s(%s) 검증 실패: %s", item.get("stock_name"), item.get("stock_code"), e)
    conn.close()
    return filtered


def _load_realtime_signals(stock_codes: list[str]) -> dict[str, list]:
    if not stock_codes:
        return {}
    conn = connect_stock_db(timeout=60, row_factory=sqlite3.Row)
    ph = ",".join("?" * len(stock_codes))
    rows = conn.execute(f"""
        SELECT stock_code, signal_type, detected_at
        FROM earnings_signals
        WHERE stock_code IN ({ph})
        ORDER BY detected_at DESC
    """, stock_codes).fetchall()
    conn.close()
    result: dict[str, list] = {}
    for r in rows:
        result.setdefault(r["stock_code"], []).append(r["signal_type"])
    return result


def _load_supply_snapshot(stock_code: str) -> tuple[float, float]:
    """최근 20일 기관/외국인 순매수 합계 (억원)"""
    conn = connect_stock_db(timeout=30)
    rows = conn.execute("""
        SELECT inst_net_buy_amt, frn_net_buy_amt
        FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 20
    """, (stock_code,)).fetchall()
    conn.close()
    inst = sum((r[0] or 0) for r in rows) / 100
    frn  = sum((r[1] or 0) for r in rows) / 100
    return inst, frn


# ── OpenAI mini TOP3 심층 분석 ───────────────────────────────────────

def _openai_mini_tenbagger_brief(stock_code: str, stock_name: str, score: int, reasons: str) -> str:
    """간략 버전 — 텔레그램용 200자 요약 분석"""
    if not is_configured():
        return ""

    # DB에서 최근 분기 실적
    conn = connect_stock_db(timeout=60, row_factory=sqlite3.Row)
    fin = conn.execute("""
        SELECT year, quarter, revenue, operating_profit, net_income
        FROM financial_data WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC, quarter DESC LIMIT 4
    """, (stock_code,)).fetchall()
    meta = conn.execute(
        "SELECT sector_large, market_cap, per, pbr, roe FROM stock_universe WHERE stock_code=?",
        (stock_code,)
    ).fetchone()
    disc = conn.execute(
        "SELECT rcept_dt, report_nm FROM dart_disclosures WHERE stock_code=? ORDER BY rcept_dt DESC LIMIT 3",
        (stock_code,)
    ).fetchall()
    conn.close()

    def _b(v): return f"{v/1e8:.0f}억" if v and abs(v) >= 1e8 else "-"
    fin_ctx = "\n".join([
        f"  {r['year']}Q{r['quarter']}: 매출 {_b(r['revenue'])}, 영업익 {_b(r['operating_profit'])}, 순익 {_b(r['net_income'])}"
        for r in fin
    ]) or "  데이터 없음"
    disc_ctx = " | ".join([f"[{r['rcept_dt']}]{r['report_nm']}" for r in disc]) or "없음"
    sector = meta["sector_large"] if meta else "-"
    mktcap = f"{meta['market_cap']:,}억" if meta and meta["market_cap"] else "-"
    per = meta["per"] or "-"
    pbr = meta["pbr"] or "-"
    roe = meta["roe"] or "-"

    prompt = f"""
한국 주식 투자 분석가. 아래 데이터로 {stock_name}({stock_code})을 텐버거 관점에서 분석해라.

기본: {sector}, 시총 {mktcap}, PER {per}, PBR {pbr}, ROE {roe}%
텐버거점수: {score}점 / 선정근거: {reasons}

최근 분기 실적:
{fin_ctx}

최근 공시: {disc_ctx}

형식 (반드시 아래 구조):
💡 핵심 한 줄: (20자 이내 핵심)
📈 성장 근거: (구체적 수치 포함 2문장)
⚡ 텐버거 트리거: (어떤 이벤트가 주가를 폭발시킬 수 있는지 1문장)
⚠️ 리스크: (가장 큰 위험 1가지)
"""
    try:
        return generate_text(
            prompt,
            system_instruction="한국 주식 텐버거 발굴 전문 애널리스트. 간결하고 핵심적으로 답변.",
            temperature=0.3,
            max_output_tokens=400,
            timeout=60,
        )
    except Exception as e:
        log.warning("[AI] %s 분석 실패: %s", stock_name, e)
    return ""


# ── 텔레그램 전송 ────────────────────────────────────────────────────

def _send_telegram(msg: str, parse_mode: str = "HTML") -> bool:
    try:
        from notifier import send
        return send(msg)
    except Exception as e:
        log.error("텔레그램 전송 실패: %s", e)
        return False


# ── 리포트 렌더링 ────────────────────────────────────────────────────

def _render_telegram(candidates: list[dict], signals: dict, ai_analyses: dict[str, str]) -> list[str]:
    """텔레그램 메시지를 여러 블록으로 분리 (4096자 제한)"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_time = candidates[0].get("run_time", "")[:16] if candidates else ""

    msgs = []

    # ── 메시지 1: 헤더 + 순위표 ──
    lines = [
        "🚀 <b>텐버거 헌터 — 오늘의 후보</b>",
        f"기준: {run_time} | 발송: {today}",
        f"총 {len(candidates)}종목",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        sc = c["stock_code"]; sn = c["stock_name"]
        score = c["score"] or 0
        sector = c.get("sector_large") or "-"
        mktcap = f"{c['market_cap']:,}억" if c.get("market_cap") else "-"
        per = c.get("per") or "-"; pbr = c.get("pbr") or "-"

        # 수급
        inst_20, frn_20 = _load_supply_snapshot(sc)
        supply_str = f"기관 {inst_20:+.0f}억/외인 {frn_20:+.0f}억"

        # 실시간 신호
        sigs = signals.get(sc, [])
        sig_str = " ".join([f"#{s}" for s in sigs[:2]]) if sigs else ""

        star = "⭐" if score >= 70 else ("🔥" if score >= 55 else "📌")
        lines.append(
            f"{star} {i:02d}. <b>{sn}</b>({sc}) [{sector}] {score}점"
        )
        lines.append(f"     시총 {mktcap} · PER {per} · PBR {pbr} · {supply_str} {sig_str}")

    msgs.append("\n".join(lines))

    # ── 메시지 2~4: TOP3 AI 심층 분석 ──
    for c in candidates[:3]:
        sc = c["stock_code"]; sn = c["stock_name"]; score = c["score"] or 0
        ai = ai_analyses.get(sc, "")
        if not ai:
            continue
        msg = (
            f"🔮 <b>{sn}({sc})</b> — 텐버거 심층 분석 [{score}점]\n\n"
            f"{ai}"
        )
        msgs.append(msg)

    return msgs


def _render_markdown(candidates: list[dict], signals: dict, ai_analyses: dict[str, str]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    run_time = candidates[0].get("run_time", "")[:16] if candidates else ""
    lines = [
        f"# 텐버거 헌터 아침 리포트 — {today}",
        f"> 발굴 기준: {run_time}  |  후보 {len(candidates)}종목",
        "",
        "## 후보 순위",
        "",
        "| # | 종목 | 코드 | 섹터 | 점수 | 시총 | PER | PBR | ROE | 수급(20일기관/외인) | 신호 |",
        "|---|------|------|------|------|------|-----|-----|-----|---------------------|------|",
    ]
    for i, c in enumerate(candidates, 1):
        inst_20, frn_20 = _load_supply_snapshot(c["stock_code"])
        sigs = " ".join(signals.get(c["stock_code"], []))
        lines.append(
            f"| {i} | {c['stock_name']} | {c['stock_code']} | {c.get('sector_large','-')} "
            f"| {c['score']} | {c.get('market_cap') or '-'} | {c.get('per') or '-'} | {c.get('pbr') or '-'} "
            f"| {c.get('roe') or '-'} | 기관{inst_20:+.0f}/외인{frn_20:+.0f}억 | {sigs} |"
        )

    lines += ["", "---", "", "## AI 심층 분석 (TOP3)", ""]
    for c in candidates[:3]:
        ai = ai_analyses.get(c["stock_code"], "")
        lines += [
            f"### {c['stock_name']}({c['stock_code']}) — {c['score']}점",
            "",
            c.get("reasons", ""),
            "",
            ai or "_분석 없음_",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


# ── DB 저장 / 신규 판별 ─────────────────────────────────────────────

def _get_prev_codes(today_str: str) -> set[str]:
    """어제(또는 가장 최근) 알림에 포함된 종목코드 반환"""
    conn = connect_stock_db(timeout=30)
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM tenbagger_daily_alerts
        WHERE alert_date < ?
    """, (today_str,)).fetchall()
    conn.close()
    return {r[0] for r in rows}


def _generate_best_reason(c: dict, signals: dict, ai_text: str) -> str:
    """왜 이 종목이 최고인지 요약 문장 생성"""
    parts = []
    score = c.get("score") or 0
    reasons_raw = c.get("reasons") or "[]"
    try:
        reasons = json.loads(reasons_raw) if isinstance(reasons_raw, str) else reasons_raw
    except Exception:
        reasons = []

    mktcap = c.get("market_cap")
    per = c.get("per"); pbr = c.get("pbr"); roe = c.get("roe")
    sector = c.get("sector_large", "-")

    # 스코어 레벨
    if score >= 75:
        parts.append(f"점수 {score}점 — 6개 분석축 대부분 강한 신호")
    elif score >= 60:
        parts.append(f"점수 {score}점 — 복수 축에서 성장 신호 포착")
    else:
        parts.append(f"점수 {score}점 — 일부 축에서 유의미한 신호")

    # 밸류에이션
    if pbr and pbr < 1.0:
        parts.append(f"PBR {pbr:.2f} (자산 대비 저평가)")
    if per and 0 < per < 15:
        parts.append(f"PER {per:.1f} (수익 대비 저평가)")
    if roe and roe >= 15:
        parts.append(f"ROE {roe:.1f}% (고수익성)")

    # 섹터
    if sector and sector != "-":
        parts.append(f"업종: {sector}")

    # 발굴 이유
    if reasons:
        key_reasons = [r for r in reasons[:3] if r]
        if key_reasons:
            parts.append("핵심근거: " + " / ".join(key_reasons))

    # AI 분석 첫 줄
    if ai_text:
        first_line = ai_text.split("\n")[0].replace("💡 핵심 한 줄:", "").strip()
        if first_line:
            parts.append(f"AI분석: {first_line}")

    return " | ".join(parts)


def _save_to_db(candidates: list[dict], signals: dict, ai_analyses: dict,
                today_str: str, prev_codes: set[str]):
    """tenbagger_daily_alerts에 저장"""
    conn = connect_stock_db(timeout=120, wal=True)
    saved = 0
    for c in candidates:
        sc = c["stock_code"]
        is_new = 0 if sc in prev_codes else 1
        ai_text = ai_analyses.get(sc, "")
        best_reason = _generate_best_reason(c, signals, ai_text)
        try:
            conn.execute("""
                INSERT INTO tenbagger_daily_alerts
                    (alert_date, stock_code, stock_name, total_score, reasons, is_new, best_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_date, stock_code) DO UPDATE SET
                    total_score=excluded.total_score,
                    reasons=excluded.reasons,
                    is_new=excluded.is_new,
                    best_reason=excluded.best_reason
            """, (today_str, sc, c["stock_name"], c.get("score"), c.get("reasons"), is_new, best_reason))
            saved += 1
        except Exception as e:
            log.warning("DB 저장 실패 %s: %s", sc, e)
    conn.commit()
    conn.close()
    log.info("DB 저장: %d건", saved)
    return saved


# ── 신규 종목 전용 텔레그램 ──────────────────────────────────────────

def _render_new_only_telegram(new_candidates: list[dict], signals: dict,
                               ai_analyses: dict, all_count: int) -> list[str]:
    """신규 진입 종목만 텔레그램 발송"""
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not new_candidates:
        log.info("신규 텐버거 후보 없음 — 텔레그램 발송 생략 (전체 %d종목 유지)", all_count)
        return []

    msgs = []
    lines = [
        "🚀 <b>텐버거 헌터 — 신규 진입 종목</b>",
        f"{today} | 신규 {len(new_candidates)}종목 / 전체 {all_count}종목",
        "",
    ]
    for i, c in enumerate(new_candidates, 1):
        sc = c["stock_code"]; sn = c["stock_name"]
        score = c.get("score") or 0
        sector = c.get("sector_large") or "-"
        per = c.get("per") or "-"; pbr = c.get("pbr") or "-"
        inst_20, frn_20 = _load_supply_snapshot(sc)
        best = c.get("best_reason", "")

        star = "⭐" if score >= 70 else "🔥"
        lines.append(f"{star} <b>{sn}</b>({sc}) [{sector}] {score}점")
        lines.append(f"   PER {per} · PBR {pbr} · 기관{inst_20:+.0f}/외인{frn_20:+.0f}억")
        if best:
            # best_reason 첫 부분만 표시
            short = " | ".join(best.split(" | ")[:3])
            lines.append(f"   📌 {short}")
        lines.append("")
    msgs.append("\n".join(lines))

    # TOP3 중 신규인 경우 AI 분석 추가
    for c in new_candidates[:2]:
        sc = c["stock_code"]; sn = c["stock_name"]; score = c.get("score") or 0
        ai = ai_analyses.get(sc, "")
        if ai:
            msgs.append(f"🔮 <b>{sn}({sc})</b> — 신규 심층분석 [{score}점]\n\n{ai}")

    return msgs


# ── 메인 ─────────────────────────────────────────────────────────────

def main(top: int = 15, ai_top: int = 3, new_only_telegram: bool = True):
    log.info("=== 텐버거 아침 알림 시작 ===")

    candidates = _load_top_candidates(top)
    if not candidates:
        log.warning("후보 종목 없음 — tenbagger_results 비어있음")
        return

    log.info("후보 %d종목 로드", len(candidates))
    today_str = datetime.now().strftime("%Y-%m-%d")
    prev_codes = _get_prev_codes(today_str)
    new_by_alert_state = _filter_new_alerts(candidates, ALERT_NAMESPACE)
    new_state_codes = {c["stock_code"] for c in new_by_alert_state}
    prev_codes |= {c["stock_code"] for c in candidates if c["stock_code"] not in new_state_codes}
    log.info("이전 알림 종목: %d개", len(prev_codes))

    # 실시간 신호
    codes = [c["stock_code"] for c in candidates]
    signals = _load_realtime_signals(codes)

    # OpenAI mini 분석 — TOP N (신규 우선)
    ai_analyses: dict[str, str] = {}
    new_codes = [c for c in candidates if c["stock_code"] not in prev_codes]
    ai_targets = new_codes[:ai_top] if new_codes else ([] if new_only_telegram else candidates[:ai_top])
    for c in ai_targets:
        sc = c["stock_code"]; sn = c["stock_name"]
        log.info("[AI] %s(%s) 분석 중…", sn, sc)
        ai = _openai_mini_tenbagger_brief(sc, sn, c["score"] or 0, c.get("reasons") or "")
        if ai:
            ai_analyses[sc] = ai
            try:
                conn = connect_stock_db(timeout=60, wal=True)
                conn.execute(
                    "INSERT OR REPLACE INTO tenbagger_ai_analysis"
                    "(stock_code, generated_at, score, reasons, ai_analysis, model)"
                    " VALUES(?,?,?,?,?,?)",
                    (sc, datetime.now().isoformat(), c["score"], c.get("reasons",""),
                     ai, model_name())
                )
                conn.commit()
                conn.close()
            except Exception as e:
                log.warning("AI 캐시 저장 실패: %s", e)

    # DB 저장 (best_reason 포함)
    _save_to_db(candidates, signals, ai_analyses, today_str, prev_codes)

    # 텔레그램 발송 — 신규 종목만
    new_candidates = [c for c in candidates if c["stock_code"] not in prev_codes]
    # best_reason 추가
    for c in new_candidates:
        ai_text = ai_analyses.get(c["stock_code"], "")
        c["best_reason"] = _generate_best_reason(c, signals, ai_text)

    if new_only_telegram:
        msgs = _render_new_only_telegram(new_candidates, signals, ai_analyses, len(candidates))
    else:
        msgs = _render_telegram(candidates, signals, ai_analyses)

    sent = 0
    for msg in msgs:
        if _send_telegram(msg):
            sent += 1
        else:
            print(msg)
    if sent and new_candidates:
        _mark_alert_sent(ALERT_NAMESPACE, new_candidates, payload_key="best_reason")
    log.info("텔레그램 %d/%d 블록 발송 (신규 %d종목)", sent, len(msgs), len(new_candidates))

    # 마크다운 저장
    md = _render_markdown(candidates, signals, ai_analyses)
    today_nosp = datetime.now().strftime("%Y%m%d")
    out_path = REPORT_DIR / f"tenbagger_alert_{today_nosp}.md"
    out_path.write_text(md, encoding="utf-8")
    log.info("리포트 저장: %s", out_path)

    log.info("=== 완료 ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--ai-top", type=int, default=3)
    parser.add_argument("--all", action="store_true", help="신규뿐 아니라 전체 종목 텔레그램 발송")
    args = parser.parse_args()
    main(top=args.top, ai_top=args.ai_top, new_only_telegram=not args.all)
