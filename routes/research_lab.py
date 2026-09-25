"""routes/research_lab.py — 연구 산출물(research_outputs) 읽기 전용 API (2026-09-25)

  GET /api/research/factor-validation   Alphalens 팩터 IC(학습/검증 분할) + 공시 이벤트 스터디
  GET /api/research/quantstats          QuantStats 전략 성과 요약(곡선 출처 배지 포함)
  GET /api/research/strategy-decay      전략 성과 감쇠 감시(최근 3/6/12개월 vs 백테스트 기대 분포)
  GET /api/research/price-integrity     가격 무결성 현황(점프 감사 분류 집계·최근 복구 run·종가 공식 검증)

파일 읽기와 DB SELECT만 한다 — DB 쓰기·외부 호출 없음. 산출물이 없으면 items=[]와 note를 돌려준다.
"""
import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
OUT = Path(__file__).resolve().parents[1] / "research_outputs"


def _load(name: str):
    path = OUT / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/factor-validation")
def factor_validation():
    data = _load("alphalens_split_events_20260925.json")
    if data is None:
        return {"factors": [], "events": [], "note": "산출물 없음"}
    return {
        "factors": data.get("factors", []),
        "events": data.get("events", []),
        "source": "alphalens_split_events_20260925.json",
        "notes": [
            "학습(~2024-12)/검증(2025-01~) 분할. sign_kept=False면 검증 구간에서 부호가 뒤집힌 팩터(기간 특이).",
            "PER은 시점 정합 TTM 기준으로 재계산(기존 valuation_history.per 결함 정정). 폐지 종목 포함.",
        ],
    }


@router.get("/quantstats")
def quantstats_summary():
    data = _load("quantstats_summary_20260924.json")
    if data is None:
        return {"items": [], "note": "산출물 없음"}
    return {
        "items": data,
        "source": "quantstats_summary_20260924.json",
        "notes": ["engine 곡선만 정확한 평가곡선. reconstructed_periods>0이면 거래로그+가격 MTM 근사."],
    }


_CLASS_LABEL = {
    "quarantined_basis": "격리(소수점·기준 불명, 백테스트 제외)",
    "inactive_or_noncommon_review": "비활성·비보통주 검토",
    "corporate_action_pending_confirmation": "기업행위 추정(공시 확인 대기)",
    "coverage_gap_reviewed": "결측 구간 검토 완료(정지·폐지 등)",
    "corporate_action_share_count_evidence": "기업행위(발행주식수 증거)",
    "confirmed_corporate_action": "기업행위 확정(DART)",
    "raw_source_confirmed_jump_review": "원천 확인된 실제 급변",
    "unresolved_active_common": "미해결(활성 보통주)",
    "corporate_action_or_delisting_nearby": "기업행위·상폐 인근",
    "non_equity_symbol": "비주식 심볼",
    "invalid_ohlcv": "OHLCV 오류",
    "mixed_basis_or_price_corruption": "기준 혼재·가격 손상",
    "coverage_gap": "결측 구간(미검토)",
}


@router.get("/price-integrity")
def price_integrity():
    from db_compat import connect_primary_db
    conn = connect_primary_db(readonly=True)
    try:
        classes = [{"classification": r[0], "label": _CLASS_LABEL.get(r[0], r[0]), "count": int(r[1])}
                   for r in conn.execute("SELECT classification, count(*) FROM price_jump_audit GROUP BY 1 ORDER BY 2 DESC").fetchall()]
        fixes = [{"run_id": r[0], "rows": int(r[1]), "reason": r[2], "fixed_at": str(r[3])[:19]}
                 for r in conn.execute(
                     "SELECT run_id, count(*), max(reason), max(fixed_at) FROM price_history_fix_backup "
                     "GROUP BY 1 ORDER BY max(fixed_at) DESC LIMIT 12").fetchall()]
        verify = [{"checked_at": str(r[0])[:19], "trade_date": str(r[1]), "compared": r[2], "close_mismatch": r[3],
                   "mismatch_pct": float(r[4] or 0), "status": r[5]}
                  for r in conn.execute(
                      "SELECT checked_at, trade_date, compared, close_mismatch, mismatch_pct, status "
                      "FROM price_close_verify_log ORDER BY id DESC LIMIT 10").fetchall()]
        return {"classes": classes, "recent_fix_runs": fixes, "close_verify": verify,
                "notes": ["격리·검토 행은 수익률 계산에서 제외/가드됩니다. 미해결(활성 보통주)이 줄어들수록 백테스트 신뢰도가 올라갑니다."]}
    finally:
        conn.close()


@router.get("/strategy-decay")
def strategy_decay():
    from db_compat import connect_primary_db
    conn = connect_primary_db(readonly=True)
    try:
        last = conn.execute("SELECT MAX(check_date) FROM strategy_decay_check").fetchone()[0]
        if not last:
            return {"items": [], "note": "감시 결과 없음(월 1회 실행)"}
        rows = conn.execute(
            "SELECT strategy, win, backtest_strategy, recent_ret_pct, p05_pct, p50_pct, pctile, n_hist, flagged, note FROM strategy_decay_check "
            "WHERE check_date=? ORDER BY flagged DESC, strategy, win", (last,)).fetchall()
        return {"check_date": str(last), "items": [dict(zip(("strategy", "window", "backtest_strategy", "recent_ret_pct", "p05_pct", "p50_pct", "pctile",
                                                              "n_hist", "flagged", "note"), tuple(r))) for r in rows],
                "notes": ["가상 계좌 전체(1억원) 대비 수익률이라 현금 비중이 큰 계좌는 0에 가깝게 나온다. 기대 분포=같은 전략 백테스트의 같은 길이 롤링 수익률."]}
    except Exception as exc:  # table not created yet
        return {"items": [], "note": f"감시 결과 없음: {str(exc)[:80]}"}
    finally:
        conn.close()
