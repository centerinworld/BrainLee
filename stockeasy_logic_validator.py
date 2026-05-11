"""
stockeasy_logic_validator.py — 스탁이지 vs 자체 로직 비교 학습기

역할:
  1. 오늘 우리 로직(SE Peak / SE Momentum / SE Value)이 추천하는 종목 추출
  2. stockeasy_analysis 최신 스냅샷에서 스탁이지 실제 보유 종목 추출
  3. 교집합·우리만·스탁이지만 비교 → Precision/Recall/F1 계산
  4. 결과를 stockeasy_logic_tracker.md에 append
  5. 텔레그램으로 일치율 리포트 전송

3전략 매핑:
  Peak Easy   ↔ SE Peak Trend v2 (calc_stockeasy_trend_candidates)
  모멘텀 Easy ↔ SE Momentum      (calc_earnings_explosion + calc_turnaround_momentum)
  벨류 Easy   ↔ SE Value         (calc_value_candidates)

실행:
  python3 stockeasy_logic_validator.py            — 오늘 비교 + 트래커 기록 + 텔레그램
  python3 stockeasy_logic_validator.py --no-telegram
  python3 stockeasy_logic_validator.py --strategy peak
"""

import argparse
import sqlite3
import json
import os
import sys
import logging
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, "/Applications/stock_dashboard")
from notifier import send as send_telegram

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH      = "/Applications/stock_dashboard/stock.db"
TRACKER_PATH = "/Applications/stock_dashboard/stockeasy_logic_tracker.md"

STRATEGY_LABELS = {
    "peak":     "Peak Easy",
    "momentum": "모멘텀 Easy",
    "value":    "벨류 Easy",
}
LOGIC_LABELS = {
    "peak":     "SE Peak Trend v2 (신고가/돌파+주도섹터+실적가속)",
    "momentum": "SE Momentum (이익폭발 + 흑자전환)",
    "value":    "SE Value (저평가 + 재평가 촉매)",
}


# ──────────────────────────────────────────────────────────
# 우리 로직 — 후보 종목 추출
# ──────────────────────────────────────────────────────────
def get_our_candidates(strategy: str) -> list:
    """전략별 자체 로직이 오늘 추천하는 종목 리스트.

    중요: 스탁이지 3전략은 서로 다른 전략이다. Peak 학습 결과를 Momentum/Value에
    섞지 않고, strategy 값으로 명확히 분기한다.
    """
    try:
        from signal_engine import (
            calc_stockeasy_trend_candidates,
            calc_value_candidates,
            calc_earnings_explosion,
            calc_turnaround_momentum,
        )
    except Exception as e:
        logger.error(f"signal_engine import 실패: {e}")
        return []

    conn = sqlite3.connect(DB_PATH)
    try:
        if strategy == "peak":
            rows = calc_stockeasy_trend_candidates(conn=conn) or []
        elif strategy == "value":
            rows = calc_value_candidates(conn=conn) or []
        elif strategy == "momentum":
            r1 = calc_earnings_explosion(conn=conn) or []
            r2 = calc_turnaround_momentum(conn=conn) or []
            seen = set()
            rows = []
            for r in (r1 + r2):
                code = r.get("stock_code")
                if code and code not in seen:
                    seen.add(code)
                    rows.append(r)
        else:
            rows = []
    except Exception as e:
        logger.error(f"{strategy} 후보 계산 오류: {e}")
        rows = []
    finally:
        conn.close()

    # 종목명 정규화 — stock_universe와 매칭
    out = []
    for r in rows:
        name = r.get("stock_name") or r.get("name") or ""
        code = r.get("stock_code") or ""
        score = r.get("score") or r.get("total_score") or 0
        out.append({"name": name, "code": code, "score": score})
    return out


# ──────────────────────────────────────────────────────────
# 스탁이지 — 실제 보유 종목
# ──────────────────────────────────────────────────────────
def get_stockeasy_holdings(strategy: str) -> tuple[list, str]:
    """stockeasy_analysis 최신 1건의 holdings_json 반환."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT analyzed_at, holdings_json FROM stockeasy_analysis
            WHERE strategy=? ORDER BY id DESC LIMIT 1
        """, (strategy,)).fetchone()
        if not row:
            return [], ""
        analyzed_at, hjson = row
        hs = json.loads(hjson) if hjson else []
        return [{"name": h["name"], "sector": h.get("sector", ""),
                 "hold_days": h.get("hold_days", 0),
                 "profit_pct": h.get("profit_pct", 0)} for h in hs], analyzed_at
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────
# 비교 / 통계
# ──────────────────────────────────────────────────────────
def compare(strategy: str) -> dict:
    """전략별 우리 로직 vs 스탁이지 비교."""
    ours_full     = get_our_candidates(strategy)
    se_full, se_at = get_stockeasy_holdings(strategy)

    ours_set = {x["name"] for x in ours_full if x["name"]}
    se_set   = {x["name"] for x in se_full   if x["name"]}

    intersect = ours_set & se_set
    only_ours = ours_set - se_set
    only_se   = se_set - ours_set

    # Precision/Recall/F1 — 스탁이지 보유를 정답(ground truth)로 봄
    precision = len(intersect) / len(ours_set) if ours_set else 0
    recall    = len(intersect) / len(se_set)   if se_set   else 0
    f1        = (2 * precision * recall / (precision + recall)) if (precision+recall) > 0 else 0

    return {
        "strategy":   strategy,
        "our_count":  len(ours_set),
        "se_count":   len(se_set),
        "intersect":  sorted(intersect),
        "only_ours":  sorted(only_ours),
        "only_se":    sorted(only_se),
        "precision":  round(precision * 100, 1),
        "recall":     round(recall * 100, 1),
        "f1":         round(f1 * 100, 1),
        "se_analyzed_at": se_at,
        "ours_full":  ours_full,
        "se_full":    se_full,
    }


# ──────────────────────────────────────────────────────────
# 트래커 마크다운 append
# ──────────────────────────────────────────────────────────
def append_to_tracker(results: list, today: str) -> None:
    """오늘 비교 결과를 stockeasy_logic_tracker.md 끝에 일별 섹션으로 추가."""
    if not os.path.exists(TRACKER_PATH):
        with open(TRACKER_PATH, "w", encoding="utf-8") as f:
            f.write(_initial_tracker_template())

    lines = [f"\n---\n\n## {today} — 일일 검증 리포트\n"]
    lines.append(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # 요약 테이블
    lines.append("\n### 일치율 요약\n")
    lines.append("| 전략 | 우리 후보 | 스탁이지 보유 | 교집합 | Precision | Recall | F1 |")
    lines.append("|------|-----------|---------------|--------|-----------|--------|----|")
    for r in results:
        lines.append(
            f"| {STRATEGY_LABELS[r['strategy']]} | {r['our_count']} | {r['se_count']} "
            f"| {len(r['intersect'])} | {r['precision']}% | {r['recall']}% | {r['f1']}% |"
        )

    # 전략별 상세
    for r in results:
        s     = r["strategy"]
        label = STRATEGY_LABELS[s]
        logic = LOGIC_LABELS[s]
        lines.append(f"\n### {label} ({logic})\n")
        lines.append(f"- 스탁이지 스냅샷: {r['se_analyzed_at']}")
        lines.append(f"- 우리 추천 {r['our_count']}종목 / 스탁이지 {r['se_count']}종목 / 교집합 {len(r['intersect'])}종목")
        lines.append(f"- **Precision {r['precision']}%** (우리 추천 중 스탁이지가 실제 보유 비율)")
        lines.append(f"- **Recall {r['recall']}%** (스탁이지 보유 중 우리가 잡아낸 비율)")

        if r["intersect"]:
            lines.append(f"\n✅ 교집합 ({len(r['intersect'])}): {', '.join(r['intersect'])}")
        if r["only_se"]:
            lines.append(f"\n❌ 스탁이지만 보유 — **우리 로직 실패** ({len(r['only_se'])}): {', '.join(r['only_se'])}")
            lines.append("  → 우리 로직이 못 잡아냄. 어떤 조건이 너무 엄격한지 검토 필요.")
        if r["only_ours"]:
            lines.append(f"\n⚠️ 우리만 추천 — **과추출** ({len(r['only_ours'])}): {', '.join(r['only_ours'][:30])}{'...' if len(r['only_ours'])>30 else ''}")
            lines.append("  → 스탁이지는 추가 필터를 적용 중. 우리 로직 좁힐 수 있는 추가 조건 탐색.")

        # 학습 힌트 — 자동 생성
        hint = _learning_hint(r)
        if hint:
            lines.append(f"\n💡 자동 학습 힌트: {hint}")

    with open(TRACKER_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info(f"[트래커] {TRACKER_PATH} 갱신 완료 (Precision/Recall 추가)")


def _learning_hint(r: dict) -> str:
    """비교 결과 기반 자동 학습 힌트."""
    p = r["precision"]
    rec = r["recall"]
    if r["our_count"] == 0:
        return "우리 로직 후보 0건 — 필터가 너무 엄격함. 임계값 완화 필요."
    if rec < 30 and r["se_count"] > 0:
        return f"Recall {rec}% — 스탁이지 보유 다수가 우리 필터에 걸림. 핵심 조건 재검토."
    if p < 20 and r["our_count"] > 30:
        return f"Precision {p}% & 후보 {r['our_count']}개 — 후보 과다. 추가 필터 필요."
    if p > 70 and rec > 50:
        return f"P/R 모두 양호 — 미세조정 단계. 누락 종목 {len(r['only_se'])}개의 공통 패턴 분석."
    return ""


def _initial_tracker_template() -> str:
    return """# Stockeasy 로직 학습 트래커

> **목표**: 우리 SE Peak / SE Momentum / SE Value 로직이 매일 스탁이지 3전략(Peak/모멘텀/벨류)과
> 100% 일치하도록 학습하고, 매일 결과를 누적 기록한다.
> 세 전략은 매수 철학이 다르므로 공통 로직으로 합치지 않는다.

## 매핑
| 스탁이지 전략 | 우리 로직 | 신호 함수 |
|---------------|----------|-----------|
| Peak Easy     | SE Peak Trend v2 | `calc_stockeasy_trend_candidates()` |
| 모멘텀 Easy   | SE Momentum | `calc_earnings_explosion() + calc_turnaround_momentum()` |
| 벨류 Easy     | SE Value | `calc_value_candidates()` |

## 학습 사이클
1. **매일 16:30** — `stockeasy_analyzer.py`로 스탁이지 보유/이탈 종목 수집
2. **매일 16:35** — `stockeasy_logic_validator.py`로 우리 로직 vs 스탁이지 비교
3. **트래커 append** — 일치율(Precision/Recall/F1) + 누락 종목/과추출 종목 기록
4. **주간 회고** — 일요일 09:00, 7일 평균 일치율 추이 확인 + 로직 조정안 제시

## KPI
- **F1 ≥ 80%** → 로직 안정화 단계 (미세조정 단계)
- **F1 ≥ 95%** → 사실상 일치 (목표)

## 일자별 검증 결과
"""


# ──────────────────────────────────────────────────────────
# 텔레그램 리포트
# ──────────────────────────────────────────────────────────
def send_report(results: list, today: str) -> None:
    if not results:
        return
    lines = [f"🧪 <b>스탁이지 로직 일치율 리포트</b>", f"📅 {today}", "━━━━━━━━━━━━━━━━━━━━━"]
    for r in results:
        s = r["strategy"]
        lines.append(f"\n<b>{STRATEGY_LABELS[s]}</b> — F1 <b>{r['f1']}%</b>")
        lines.append(f"  우리 {r['our_count']} / SE {r['se_count']} / 교집합 {len(r['intersect'])}")
        lines.append(f"  Precision {r['precision']}% · Recall {r['recall']}%")
        if r["only_se"]:
            miss_short = ", ".join(r["only_se"][:5])
            tail = f" 외 {len(r['only_se'])-5}" if len(r["only_se"]) > 5 else ""
            lines.append(f"  ❌ 누락: {miss_short}{tail}")
        hint = _learning_hint(r)
        if hint:
            lines.append(f"  💡 {hint}")

    msg = "\n".join(lines)
    if len(msg) > 3800:
        msg = msg[:3800] + "...(생략)"
    send_telegram(msg, key=f"se_logic_validator_{today}")
    logger.info("[텔레그램] 일치율 리포트 전송 완료")


# ──────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────
def run_validation(strategies: list, send_tg: bool = True) -> list:
    today = date.today().isoformat()
    results = []
    for s in strategies:
        logger.info(f"[{s}] 비교 시작...")
        r = compare(s)
        logger.info(f"[{s}] P={r['precision']}% R={r['recall']}% F1={r['f1']}% "
                    f"(우리 {r['our_count']} / SE {r['se_count']})")
        results.append(r)

    append_to_tracker(results, today)
    if send_tg:
        send_report(results, today)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["peak", "momentum", "value"], default=None)
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    strategies = [args.strategy] if args.strategy else ["peak", "momentum", "value"]
    run_validation(strategies, send_tg=not args.no_telegram)
