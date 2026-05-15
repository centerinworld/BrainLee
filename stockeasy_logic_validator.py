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
import re
from datetime import date, datetime, timedelta
from typing import Optional
from collections import Counter

sys.path.insert(0, "/Applications/stock_dashboard")
from notifier import send as send_telegram

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH      = "/Applications/stock_dashboard/stock.db"
TRACKER_PATH = "/Applications/stock_dashboard/stockeasy_logic_tracker.md"
PARAMS_PATH  = "/Applications/stock_dashboard/config/stockeasy_logic_params.json"
TUNING_LOG_PATH = "/Applications/stock_dashboard/config/stockeasy_logic_tuning_history.json"

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

DEFAULT_PARAMS = {
    "peak": {"min_score": 0.0, "max_candidates": 70, "min_mktcap_억": 0},
    "momentum": {"min_score": 0.0, "max_candidates": 120, "min_mktcap_억": 0},
    "value": {"min_score": 0.0, "max_candidates": 120, "min_mktcap_억": 0},
}


def _load_params() -> dict:
    if not os.path.exists(PARAMS_PATH):
        os.makedirs(os.path.dirname(PARAMS_PATH), exist_ok=True)
        with open(PARAMS_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_PARAMS, f, ensure_ascii=False, indent=2)
        return json.loads(json.dumps(DEFAULT_PARAMS))
    try:
        with open(PARAMS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        out = json.loads(json.dumps(DEFAULT_PARAMS))
        out.update(d if isinstance(d, dict) else {})
        return out
    except Exception:
        return json.loads(json.dumps(DEFAULT_PARAMS))


def _save_params(params: dict) -> None:
    os.makedirs(os.path.dirname(PARAMS_PATH), exist_ok=True)
    with open(PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)


def _append_tuning_log(entry: dict) -> None:
    logs = []
    if os.path.exists(TUNING_LOG_PATH):
        try:
            with open(TUNING_LOG_PATH, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    os.makedirs(os.path.dirname(TUNING_LOG_PATH), exist_ok=True)
    with open(TUNING_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(logs[-200:], f, ensure_ascii=False, indent=2)


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
    conn.row_factory = sqlite3.Row
    try:
        if strategy == "peak":
            rows = calc_stockeasy_trend_candidates(conn=conn) or []
        elif strategy == "value":
            # Value Easy는 실제 보유가 "저평가 단독"보다 재평가/추세 혼합형에 가깝다.
            # 따라서 Trend(재평가 촉매) + Value(저평가)를 합성 점수로 역추론한다.
            trows = calc_stockeasy_trend_candidates(conn=conn) or []
            vrows = calc_value_candidates(conn=conn) or []
            by_code = {}
            for r in trows:
                code = r.get("stock_code")
                if not code:
                    continue
                by_code.setdefault(code, {
                    "stock_code": code,
                    "stock_name": r.get("stock_name") or r.get("name"),
                    "sector": r.get("sector") or "",
                    "mktcap": r.get("mktcap") or 0,
                    "_trend": 0.0,
                    "_value": 0.0,
                })
                by_code[code]["_trend"] = max(by_code[code]["_trend"], float(r.get("score") or 0))
            for r in vrows:
                code = r.get("stock_code")
                if not code:
                    continue
                by_code.setdefault(code, {
                    "stock_code": code,
                    "stock_name": r.get("stock_name") or r.get("name"),
                    "sector": r.get("sector") or "",
                    "mktcap": r.get("mktcap") or 0,
                    "_trend": 0.0,
                    "_value": 0.0,
                })
                by_code[code]["_value"] = max(by_code[code]["_value"], float(r.get("score") or 0))
            rows = []
            for x in by_code.values():
                # 재평가형 특성: 추세 가중치를 더 높이고, 가치 점수는 보조로 반영
                score = x["_trend"] * 0.7 + x["_value"] * 1.4
                if x["_trend"] > 0:
                    score += 8
                rows.append({
                    "stock_code": x["stock_code"],
                    "stock_name": x["stock_name"],
                    "sector": x["sector"],
                    "mktcap": x["mktcap"],
                    "score": score,
                })
        elif strategy == "momentum":
            # Momentum Easy는 최근 실제 보유가 대형 주도주의 추세/상대강도 성격이 강함.
            # v10/v11 단독 대신 Trend + Earnings Momentum 합성으로 역추론.
            trows = calc_stockeasy_trend_candidates(conn=conn) or []
            r1 = calc_earnings_explosion(conn=conn) or []
            r2 = calc_turnaround_momentum(conn=conn) or []
            by_code = {}
            for r in trows:
                code = r.get("stock_code")
                if not code:
                    continue
                by_code.setdefault(code, {
                    "stock_code": code,
                    "stock_name": r.get("stock_name") or r.get("name"),
                    "sector": r.get("sector") or "",
                    "mktcap": r.get("mktcap") or 0,
                    "_trend": 0.0,
                    "_earn": 0.0,
                })
                by_code[code]["_trend"] = max(by_code[code]["_trend"], float(r.get("score") or 0))
            for r in (r1 + r2):
                code = r.get("stock_code")
                if not code:
                    continue
                by_code.setdefault(code, {
                    "stock_code": code,
                    "stock_name": r.get("stock_name") or r.get("name"),
                    "sector": r.get("sector") or "",
                    "mktcap": r.get("mktcap") or 0,
                    "_trend": 0.0,
                    "_earn": 0.0,
                })
                by_code[code]["_earn"] = max(by_code[code]["_earn"], float(r.get("score") or 0))
            rows = []
            for x in by_code.values():
                score = x["_trend"] * 1.25 + x["_earn"] * 0.7
                # 추세형 모멘텀 우선
                if x["_trend"] > 0:
                    score += 12
                rows.append({
                    "stock_code": x["stock_code"],
                    "stock_name": x["stock_name"],
                    "sector": x["sector"],
                    "mktcap": x["mktcap"],
                    "score": score,
                })
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
        sector = r.get("sector") or r.get("sector_large") or ""
        mktcap = (
            r.get("market_cap_억")
            or r.get("mktcap_억")
            or (round((r.get("mktcap") or 0) / 1e8) if r.get("mktcap") else 0)
        )
        out.append({
            "name": name,
            "code": code,
            "score": score,
            "sector": sector,
            "mktcap_억": mktcap or 0,
        })
    return out


def get_stockeasy_two_snapshots(strategy: str) -> tuple[dict, dict]:
    """해당 전략의 최신/직전 스냅샷 반환."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute("""
            SELECT analyzed_at, holdings_json, exits_json
            FROM stockeasy_analysis
            WHERE strategy=?
            ORDER BY id DESC
            LIMIT 2
        """, (strategy,)).fetchall()
    finally:
        conn.close()
    if not rows:
        return {}, {}

    def _to_payload(row):
        analyzed_at, hjson, ejson = row
        hs = json.loads(hjson) if hjson else []
        es = json.loads(ejson) if ejson else []
        return {"analyzed_at": analyzed_at, "holdings": hs, "exits": es}

    latest = _to_payload(rows[0])
    prev = _to_payload(rows[1]) if len(rows) > 1 else {}
    return latest, prev


def _calc_daily_delta(strategy: str) -> dict:
    latest, prev = get_stockeasy_two_snapshots(strategy)
    cur_h = {x.get("name") for x in (latest.get("holdings") or []) if x.get("name")}
    prv_h = {x.get("name") for x in (prev.get("holdings") or []) if x.get("name")}
    added = sorted(cur_h - prv_h)
    removed = sorted(prv_h - cur_h)
    exits_today = sorted({x.get("name") for x in (latest.get("exits") or []) if x.get("name")})
    return {
        "latest_at": latest.get("analyzed_at", ""),
        "prev_at": prev.get("analyzed_at", ""),
        "added": added,
        "removed": removed,
        "exits_today": exits_today,
    }


def _load_latest_stockeasy_reports(strategy: str) -> dict:
    """최신 스냅샷의 보유종목 리포트(research.summary.content_list) 요약."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("""
            SELECT analyzed_at, holdings_json
            FROM stockeasy_analysis
            WHERE strategy=?
            ORDER BY id DESC
            LIMIT 1
        """, (strategy,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"report_count": 0, "top_keywords": [], "top_sectors": []}

    analyzed_at, hjson = row
    holdings = json.loads(hjson or "[]")
    sectors = Counter()
    kw = Counter()
    total_reports = 0
    vocab = [
        "AI", "HBM", "수주", "방산", "조선", "반도체", "서버", "데이터센터",
        "흑자전환", "실적", "영업이익", "매출", "고마진", "MLCC", "FC-BGA",
        "저평가", "가치", "재평가", "모멘텀", "신고가", "돌파", "상승추세",
    ]
    for h in holdings:
        sec = h.get("sector")
        if sec:
            sectors[sec] += 1
        rs = (h.get("research") or {}).get("summary") or {}
        lines = rs.get("content_list") or []
        if lines:
            total_reports += 1
        txt = " ".join(str(x) for x in lines)
        txt_u = txt.upper()
        for t in vocab:
            hit = txt_u.count(t.upper())
            if hit:
                kw[t] += hit

    return {
        "analyzed_at": analyzed_at,
        "report_count": total_reports,
        "top_keywords": [k for k, _ in kw.most_common(8)],
        "top_sectors": [k for k, _ in sectors.most_common(5)],
    }


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
    params = _load_params().get(strategy, DEFAULT_PARAMS[strategy])
    ours_full_raw = get_our_candidates(strategy)
    min_score = float(params.get("min_score", 0.0))
    max_candidates = int(params.get("max_candidates", 120))
    min_mktcap = float(params.get("min_mktcap_억", 0) or 0)
    ours_full = [
        x for x in ours_full_raw
        if float(x.get("score") or 0) >= min_score
        and float(x.get("mktcap_억") or 0) >= min_mktcap
    ]
    ours_full = sorted(ours_full, key=lambda x: float(x.get("score") or 0), reverse=True)[:max_candidates]
    se_full, se_at = get_stockeasy_holdings(strategy)
    delta = _calc_daily_delta(strategy)
    report_profile = _load_latest_stockeasy_reports(strategy)

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
        "params_before": {
            "min_score": min_score,
            "max_candidates": max_candidates,
            "min_mktcap_억": min_mktcap,
        },
        "delta": delta,
        "report_profile": report_profile,
    }


def _infer_and_apply_adjustment(r: dict, all_params: dict) -> dict:
    """
    단순 규칙 기반 미세조정:
    - 신규편입 누락이 많으면 min_score 완화(하향)
    - 과추출/정밀도 저하면 min_score 강화(상향) 및 max_candidates 축소
    """
    s = r["strategy"]
    p = dict(all_params.get(s, DEFAULT_PARAMS[s]))
    before = dict(p)
    reasons = []

    added = set(r.get("delta", {}).get("added", []))
    only_se = set(r.get("only_se", []))
    only_ours_cnt = len(r.get("only_ours", []))
    rec = float(r.get("recall", 0))
    precision = float(r.get("precision", 0))
    rp = r.get("report_profile") or {}
    rep_kw = rp.get("top_keywords") or []

    missed_new = len(added & only_se)
    if missed_new >= 2 or (added and missed_new >= 1):
        p["min_score"] = max(0.0, float(p.get("min_score", 0.0)) - 1.0)
        p["max_candidates"] = min(160, int(p.get("max_candidates", 120)) + 10)
        reasons.append(f"신규편입 누락 {missed_new}건 → 진입 완화")
    elif precision < 20 and only_ours_cnt > 30:
        p["min_score"] = min(80.0, float(p.get("min_score", 0.0)) + 1.0)
        p["max_candidates"] = max(20, int(p.get("max_candidates", 120)) - 10)
        reasons.append(f"과추출 {only_ours_cnt}건/정밀도 {precision}% → 필터 강화")
    elif rec < 40 and len(only_se) >= 5:
        p["min_score"] = max(0.0, float(p.get("min_score", 0.0)) - 0.5)
        reasons.append(f"재현율 {rec}% 저조 → 점수 하한 완화")
    else:
        reasons.append("유의미한 패턴 변화 없음 → 파라미터 유지")

    # 섹터는 전략 제한/우대에 사용하지 않는다. (전략 원칙)
    if "preferred_sectors" in p:
        p.pop("preferred_sectors", None)
        reasons.append("섹터 우대/제한 파라미터 제거(전략 원칙 반영)")

    # 리포트에서 대형주 키워드(AI/HBM/서버/데이터센터)가 강하면 최소 시총 필터 강화
    largecap_kw = {"AI", "HBM", "서버", "데이터센터", "반도체"}
    if s in ("momentum", "value") and any(k in largecap_kw for k in rep_kw):
        p["min_mktcap_억"] = max(float(p.get("min_mktcap_억", 0) or 0), 5000.0)
        reasons.append("리포트 대형주/AI 키워드 반영 → 최소 시총 5,000억 적용")

    all_params[s] = p
    changed = (before != p)
    return {
        "strategy": s,
        "before": before,
        "after": p,
        "changed": changed,
        "reasons": reasons,
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
        rp = r.get("report_profile") or {}
        if rp.get("report_count"):
            lines.append(
                f"- 리포트 기반 요약: {rp.get('report_count')}개 본문 분석 / "
                f"핵심키워드 {', '.join((rp.get('top_keywords') or [])[:5])}"
            )

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
        adj = r.get("adjustment")
        if adj:
            lines.append(
                f"\n🔧 로직 조정: min_score {adj['before'].get('min_score')} → {adj['after'].get('min_score')}, "
                f"max_candidates {adj['before'].get('max_candidates')} → {adj['after'].get('max_candidates')}, "
                f"min_mktcap_억 {adj['before'].get('min_mktcap_억',0)} → {adj['after'].get('min_mktcap_억',0)}"
            )
            lines.append(f"   사유: {', '.join(adj.get('reasons', []))}")
        d = r.get("delta") or {}
        if d.get("added") or d.get("removed") or d.get("exits_today"):
            lines.append(
                f"\n🧭 당일 변동: 신규편입 {len(d.get('added', []))} / 이탈 {len(d.get('removed', []))} / 당일편출 {len(d.get('exits_today', []))}"
            )

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
        rp = r.get("report_profile") or {}
        if rp.get("report_count"):
            lines.append(f"  📚 리포트 {rp.get('report_count')}개 분석 · 키워드 {', '.join((rp.get('top_keywords') or [])[:4])}")
        d = r.get("delta") or {}
        lines.append(f"  🧭 신규편입 {len(d.get('added', []))} · 이탈 {len(d.get('removed', []))} · 당일편출 {len(d.get('exits_today', []))}")
        if d.get("added"):
            lines.append(f"    + {', '.join(d.get('added', [])[:4])}{' ...' if len(d.get('added', [])) > 4 else ''}")
        if d.get("removed"):
            lines.append(f"    - {', '.join(d.get('removed', [])[:4])}{' ...' if len(d.get('removed', [])) > 4 else ''}")
        if r["only_se"]:
            miss_short = ", ".join(r["only_se"][:5])
            tail = f" 외 {len(r['only_se'])-5}" if len(r["only_se"]) > 5 else ""
            lines.append(f"  ❌ 누락: {miss_short}{tail}")
        adj = r.get("adjustment")
        if adj:
            lines.append(
                f"  🔧 로직값: min_score {adj['before'].get('min_score')}→{adj['after'].get('min_score')}, "
                f"max {adj['before'].get('max_candidates')}→{adj['after'].get('max_candidates')}, "
                f"minCap {adj['before'].get('min_mktcap_억',0)}→{adj['after'].get('min_mktcap_억',0)}"
            )
            lines.append(f"  📌 조정사유: {', '.join(adj.get('reasons', []))}")
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
    params = _load_params()
    for s in strategies:
        logger.info(f"[{s}] 비교 시작...")
        r = compare(s)
        adj = _infer_and_apply_adjustment(r, params)
        r["adjustment"] = adj
        logger.info(f"[{s}] P={r['precision']}% R={r['recall']}% F1={r['f1']}% "
                    f"(우리 {r['our_count']} / SE {r['se_count']})")
        results.append(r)

    _save_params(params)
    _append_tuning_log({
        "date": today,
        "at": datetime.now().isoformat(timespec="seconds"),
        "results": [
            {
                "strategy": r["strategy"],
                "precision": r["precision"],
                "recall": r["recall"],
                "f1": r["f1"],
                "delta": r.get("delta", {}),
                "adjustment": r.get("adjustment", {}),
            } for r in results
        ]
    })

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
