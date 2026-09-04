#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "research_outputs"
DB = ROOT / "stock.db"


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def latest_full_date(c: sqlite3.Connection) -> str | None:
    row = c.execute(
        """
        SELECT date
        FROM price_history
        WHERE close > 0
        GROUP BY date
        HAVING COUNT(DISTINCT stock_code) >= 2000
        ORDER BY date DESC
        LIMIT 1
        """
    ).fetchone()
    return row["date"] if row else None


def recent_partition_checks(c: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rows = c.execute(
        """
        SELECT date, COUNT(DISTINCT stock_code) coverage
        FROM price_history
        WHERE date >= date('now', '-14 days') AND close > 0
        GROUP BY date
        ORDER BY date DESC
        """
    ).fetchall()
    for row in rows:
        coverage = int(row["coverage"] or 0)
        if coverage and coverage < 2000:
            out.append({
                "severity": "high",
                "page": "공통 주가 기반 페이지",
                "check": "partial_price_partition",
                "evidence": f"{row['date']} price_history coverage {coverage:,} stocks (<2,000)",
                "risk": "당일 부분 적재 행을 최신 기준일로 쓰면 일부 섹터/종목만 오늘 가격을 반영해 RS·등락률·신호가 왜곡됨",
                "fix_hint": "신호/랭킹 API는 latest_full_date 기준 또는 realtime validated overlay만 사용",
            })
    return out


def pct_return(c: sqlite3.Connection, code: str, end_date: str, days: int) -> float | None:
    start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = c.execute(
        """
        SELECT close
        FROM price_history
        WHERE stock_code=? AND date>=? AND date<=? AND close>0
        ORDER BY date
        """,
        (code, start, end_date),
    ).fetchall()
    if len(rows) < 3:
        return None
    first = float(rows[0]["close"])
    last = float(rows[-1]["close"])
    return (last / first - 1.0) * 100.0 if first > 0 else None


def audit_sector_rotation(c: sqlite3.Connection) -> list[dict[str, Any]]:
    from routes.sector_rotation import _compute_sector_leadership

    as_of = latest_full_date(c)
    if not as_of:
        return []
    payload = _compute_sector_leadership(c, as_of=as_of, months=36, top_n=3)
    findings = []
    for s in payload.get("sectors", []):
        stage = s.get("stage")
        rs4 = s.get("rs4w")
        rs12 = s.get("rs12w")
        if stage == "ENTRY_NOW" and ((rs4 is not None and rs4 < 0) or (rs12 is not None and rs12 < -5)):
            findings.append({
                "severity": "critical",
                "page": "섹터 지표/주도섹터 로테이션",
                "check": "entry_with_negative_rs",
                "sector": s.get("sector"),
                "evidence": f"stage={stage}, rs4w={rs4}, rs12w={rs12}, score={s.get('score')}",
                "risk": "가격 모멘텀 약세 섹터를 신규 진입으로 표시",
                "fix_hint": "ENTRY_NOW는 Leading 또는 충분한 단기 RS 회복 확인 후만 허용",
            })
    return findings


def audit_turnover_breakout(c: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    findings = []
    try:
        from routes.market_indicators import get_turnover_breakout_signals
        rows = get_turnover_breakout_signals(scan_limit=200, top_n=80)
    except Exception as exc:
        return [{
            "severity": "medium",
            "page": "시장지표/거래대금 돌파",
            "check": "endpoint_error",
            "evidence": str(exc),
            "risk": "페이지 신호 로드 실패 가능",
            "fix_hint": "API 예외 로그 확인",
        }]
    if not isinstance(rows, list):
        rows = rows.get("items") or rows.get("rows") or []
    for r in rows[:50]:
        code = str(r.get("stock_code") or r.get("code") or "")
        if not code:
            continue
        r20 = pct_return(c, code, as_of, 30)
        score = float(r.get("score") or 0)
        if score >= 65 and r20 is not None and r20 < -20:
            findings.append({
                "severity": "high",
                "page": "시장지표/거래대금 돌파",
                "check": "high_score_while_recent_crash",
                "stock_code": code,
                "stock_name": r.get("stock_name") or r.get("name"),
                "evidence": f"score={score}, approx_1m_return={r20:.1f}%",
                "risk": "거래량만 보고 급락 지속 종목을 돌파 후보로 오인",
                "fix_hint": "최근 20~30일 급락 필터 또는 MA20 회복 조건 추가",
            })
    return findings


def audit_tenbagger_candidates(c: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    findings = []
    try:
        from routes.tenbagger import get_latest_results
        rows = get_latest_results(limit=100)
    except Exception:
        try:
            rows = c.execute(
                """
                SELECT stock_code, stock_name, total_score, current_price, run_time
                FROM tenbagger_results
                WHERE run_time=(SELECT MAX(run_time) FROM tenbagger_results)
                ORDER BY total_score DESC
                LIMIT 100
                """
            ).fetchall()
            rows = [dict(r) for r in rows]
        except Exception as exc:
            return [{
                "severity": "medium",
                "page": "텐버거",
                "check": "endpoint_error",
                "evidence": str(exc),
                "risk": "텐버거 후보 페이지 로드 실패 가능",
                "fix_hint": "routes.tenbagger 결과 함수/DB 확인",
            }]
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("items") or rows.get("rows") or []
    for r in rows[:80]:
        code = str(r.get("stock_code") or r.get("code") or "")
        if not code:
            continue
        score = float(r.get("risk_adjusted_score") or r.get("total_score") or r.get("score") or 0)
        r60 = pct_return(c, code, as_of, 90)
        if score >= 70 and r60 is not None and r60 < -35:
            findings.append({
                "severity": "high",
                "page": "텐버거 프로젝트",
                "check": "high_score_while_3m_crash",
                "stock_code": code,
                "stock_name": r.get("stock_name") or r.get("name"),
                "evidence": f"risk_adjusted_score={score}, approx_3m_return={r60:.1f}%",
                "risk": "고득점 후보가 가격 붕괴 중일 수 있음",
                "fix_hint": "후보 표시에 가격확인/회피 배지 또는 최근 급락 감점 추가",
            })
    return findings


def audit_stock_rs_cache(c: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    findings = []
    cache = ROOT / "scratch" / "stock_analysis_rs_cache.json"
    if not cache.exists():
        return findings
    try:
        payload = json.loads(cache.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{
            "severity": "medium",
            "page": "국내종목 RS",
            "check": "cache_parse_error",
            "evidence": str(exc),
            "risk": "RS 페이지 캐시가 깨져 화면 신뢰 불가",
            "fix_hint": "RS precompute 재실행",
        }]
    generated = payload.get("dashboard_data", {}).get("data", {}).get("as_of") or payload.get("dashboard_data", {}).get("as_of")
    if generated and str(generated)[:10] > as_of:
        findings.append({
            "severity": "high",
            "page": "국내종목 RS",
            "check": "cache_after_full_price_date",
            "evidence": f"cache_as_of={generated}, latest_full_price_date={as_of}",
            "risk": "부분 적재일 기준 RS가 표시될 수 있음",
            "fix_hint": "RS 캐시도 latest_full_date 기준으로 계산",
        })
    return findings


def _http_json(path: str, timeout: int = 60) -> Any:
    url = f"http://127.0.0.1:8000{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _walk_candidate_rows(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    """API별로 다른 응답 모양을 stock candidate row 리스트로 평탄화."""
    if depth > 5 or payload is None:
        return []
    if isinstance(payload, list):
        out: list[dict[str, Any]] = []
        for item in payload:
            out.extend(_walk_candidate_rows(item, depth + 1))
        return out
    if not isinstance(payload, dict):
        return []

    code = _row_code(payload)
    if code:
        return [payload]

    out = []
    preferred_keys = (
        "stocks",
        "results",
        "items",
        "rows",
        "candidates",
        "buy_candidates",
        "watch_signals",
        "buy_signals",
        "recommendations",
        "data",
    )
    for key in preferred_keys:
        if key in payload:
            out.extend(_walk_candidate_rows(payload.get(key), depth + 1))
    return out


def _row_code(row: dict[str, Any]) -> str:
    code = (
        row.get("stock_code")
        or row.get("code")
        or row.get("ticker")
        or row.get("symbol")
        or row.get("종목코드")
    )
    if code is None:
        return ""
    code_s = str(code).strip()
    if code_s.isdigit() and len(code_s) <= 6:
        code_s = code_s.zfill(6)
    return code_s if len(code_s) == 6 and code_s.isdigit() else ""


def _row_name(row: dict[str, Any]) -> str:
    return str(
        row.get("stock_name")
        or row.get("name")
        or row.get("company_name")
        or row.get("종목명")
        or ""
    )


def _row_score(row: dict[str, Any]) -> float:
    keys = (
        "risk_adjusted_score",
        "total_score",
        "combined_score",
        "score",
        "trend_score",
        "v3_score",
        "ai_score",
        "surge_score",
        "signal_score",
        "rank_score",
    )
    vals = []
    for key in keys:
        try:
            if row.get(key) is not None:
                vals.append(float(row.get(key)))
        except Exception:
            continue
    return max(vals) if vals else 0.0


def _row_has_buy_signal(row: dict[str, Any]) -> bool:
    if row.get("buy_signal") is True:
        return True
    text = " ".join(
        str(row.get(k, ""))
        for k in ("signal", "trade_signal", "ai_signal", "action", "stage", "recommendation")
    ).upper()
    buy_tokens = ("BUY", "STRONG_BUY", "ENTRY_NOW", "매수", "강한매수", "진입")
    avoid_tokens = ("SELL", "매도", "회피", "AVOID", "RISK", "위험")
    return any(t in text for t in buy_tokens) and not any(t in text for t in avoid_tokens)


def _has_price_risk_fields(row: dict[str, Any]) -> bool:
    return any(
        row.get(k) is not None
        for k in ("price_risk", "price_risk_label", "price_risk_penalty", "risk_adjusted_score")
    )


def audit_candidate_price_risk(c: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    """후보/매수 신호 API 전반에서 가격 급락 보정 누락을 찾는다."""
    endpoints = [
        ("시그널/추세후보", "/api/signals/trend-candidates"),
        ("시그널/가치후보", "/api/signals/value-candidates"),
        ("시그널/수급주도", "/api/signals/combo-v2"),
        ("시그널/재무스크리너", "/api/signals/fin-screener"),
        ("시그널/고수익집중", "/api/signals/high-profit-candidates?limit=120&refresh=true"),
        ("시그널/트리거랭킹", "/api/signals/trigger-ranking"),
        ("시그널/V10 이익폭발", "/api/signals/v10-earnings-explosion"),
        ("시그널/V11 턴어라운드", "/api/signals/v11-turnaround"),
        ("시그널/V12 섹터메가트렌드", "/api/signals/v12-sector-megatrend"),
        ("시그널/키움조건식", "/api/signals/kiwoom-conditions?strategy=all&refresh=true"),
        ("전략센터/V-GC", "/api/trend/gc/recommendations"),
        ("전략센터/V-RECOVERY", "/api/trend/rec/recommendations"),
        ("전략센터/V18", "/api/trend/v18/recommendations"),
        ("전략센터/거래대금", "/api/trend/turnover/recommendations"),
        ("텐버거/커스텀필터", "/api/tenbagger/custom-filter?limit=120"),
        ("텐버거/저평가필터", "/api/tenbagger/undervalued-filter?limit=120"),
        ("텐버거/턴어라운드필터", "/api/tenbagger/turnaround-filter?limit=120"),
        ("텐버거/회복후보", "/api/tenbagger/recovery-candidates?limit=80"),
        ("텐버거/액션신호", "/api/tenbagger/action-signals?limit=80"),
        ("수주공시/신호", "/api/dart-contracts/signals?days=120&min_signal=2"),
        ("카페/종목매매신호", "/api/cafe-signals/stock-trade-signals?limit=100"),
        ("매수후보", "/api/buy-candidates"),
    ]
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for page, path in endpoints:
        try:
            payload = _http_json(path)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            findings.append({
                "severity": "medium",
                "page": page,
                "check": "endpoint_error",
                "evidence": f"{path}: {exc}",
                "risk": "후보/신호 페이지 로드 실패 가능",
                "fix_hint": "백엔드 실행 상태와 API 예외 로그 확인",
            })
            continue
        rows = _walk_candidate_rows(payload)
        for row in rows[:160]:
            code = _row_code(row)
            if not code:
                continue
            score = _row_score(row)
            buyish = _row_has_buy_signal(row) or score >= 70
            if not buyish:
                continue
            r30 = pct_return(c, code, as_of, 30)
            r90 = pct_return(c, code, as_of, 90)
            crash_1m = r30 is not None and r30 <= -20
            crash_3m = r90 is not None and r90 <= -35
            if not crash_1m and not crash_3m:
                continue
            has_price_risk = _has_price_risk_fields(row)
            severity = "high" if not has_price_risk else "low"
            check = "buy_signal_recent_crash_without_price_risk" if severity == "high" else "recent_crash_price_risk_guarded"
            risk = (
                "고점수 또는 매수 후보가 최근 급락 추세를 충분히 반영하지 못할 수 있음"
                if not has_price_risk
                else "가격위험 배지/감점은 존재하므로 실전 판단 시 관찰 항목으로 확인 필요"
            )
            fix_hint = (
                "가격위험 배지/감점(risk_adjusted_score) 또는 MA 회복 조건 추가"
                if not has_price_risk
                else "추가 조치 불필요. 필요 시 리포트에서 관찰 섹션으로 분리"
            )
            key = (page, check, code)
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "severity": severity,
                "page": page,
                "check": check,
                "stock_code": code,
                "stock_name": _row_name(row),
                "evidence": f"{path}, score={score:.1f}, 1m={r30:.1f}%/3m={r90:.1f}%",
                "risk": risk,
                "fix_hint": fix_hint,
            })
    return findings


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = conn()
    try:
        as_of = latest_full_date(c)
        findings = []
        findings.extend(recent_partition_checks(c))
        if as_of:
            findings.extend(audit_sector_rotation(c))
            findings.extend(audit_turnover_breakout(c, as_of))
            findings.extend(audit_tenbagger_candidates(c, as_of))
            findings.extend(audit_stock_rs_cache(c, as_of))
            findings.extend(audit_candidate_price_risk(c, as_of))
    finally:
        c.close()

    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda x: (severity_rank.get(x.get("severity"), 9), x.get("page", "")))

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_full_price_date": as_of,
        "summary": {
            "total_findings": len(findings),
            "critical": sum(1 for f in findings if f.get("severity") == "critical"),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
            "low": sum(1 for f in findings if f.get("severity") == "low"),
        },
        "findings": findings,
    }
    stamp = date.today().strftime("%Y%m%d")
    out_json = OUT_DIR / f"signal_page_logic_audit_{stamp}.json"
    out_md = OUT_DIR / f"signal_page_logic_audit_{stamp}.md"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 신호 페이지 로직 감사 — {date.today().isoformat()}",
        "",
        f"- 생성: `{payload['generated_at']}`",
        f"- 완전 적재 가격 기준일: `{as_of}`",
        f"- 요약: critical {payload['summary']['critical']} / high {payload['summary']['high']} / medium {payload['summary']['medium']} / low {payload['summary']['low']}",
        "",
        "|심각도|페이지|검사|대상|증거|위험|조치|",
        "|---|---|---|---|---|---|---|",
    ]
    for f in findings:
        target = f.get("sector") or f.get("stock_code") or "-"
        if f.get("stock_name"):
            target = f"{target} {f['stock_name']}"
        lines.append(
            f"|{f.get('severity')}|{f.get('page')}|`{f.get('check')}`|{target}|{f.get('evidence')}|{f.get('risk')}|{f.get('fix_hint')}|"
        )
    if not findings:
        lines.append("|none|-|-|-|-|-|-|")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
