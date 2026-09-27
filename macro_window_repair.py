"""macro_window_repair.py — 매크로 시세(지수·금리·환율·원자재·VIX) 최근 구간 교체 (2026-09-27)

배경(2026-09-27 실측): 주요 지표 화면의 미국 10년물·원/달러·VIX 가 9/11(VIX 9/8)에서 멈췄다. 원인 두 가지.
  1) 저장된 마지막 날짜가 조회 창(5일)보다 오래되면 새 데이터와 저장분이 **한 날도 겹치지 않아** price_integrity.gate_price_batch 가
     `historical_batch_without_overlap` 으로 영구 격리했다(스스로 회복 불가).
  2) 겹치는 날이 있어도 저장분이 장중 스냅샷·중복값(예: ^TNX 9/10=9/11=4.918)이라 Yahoo 확정 종가와 0.5% 넘게 달라 `historical_overlap_basis_mismatch` 로 격리됐다.
이 모듈은 **비주식 매크로 심볼에 한해**, 새로 받은 최근 구간(기본 1개월)으로 저장분을 교체한다. 무결성 정책을 끄는 것이 아니라 아래 안전장치를 둔다:
  · 대상 심볼 화이트리스트(REPAIRABLE) — 주식·KOSPI/KOSDAQ(수급 컬럼이 있음)는 제외
  · 겹치는 날의 저장값 대비 새 종가 최대 편차가 심볼별 한도 이내여야 함(엉뚱한 시계열 방지)
  · 교체 전 원본을 price_history_fix_backup 에 백업(삽입은 old NULL) + data_fix_log 에 1행 기록(run_id 포함)
  · 쓰기 방어 트리거(app.price_basis_checked)는 이 함수가 검증을 마친 트랜잭션 안에서만 통과시킨다
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

REPAIRABLE = {"^IXIC", "^GSPC", "^VIX", "^TNX", "^TYX", "2YY=F", "10Y=F", "DX-Y.NYB", "GC=F", "CL=F", "USDKRW=X"}
MAX_DEV_PCT = {"^VIX": 100.0, "CL=F": 15.0, "GC=F": 10.0, "USDKRW=X": 5.0}
DEFAULT_MAX_DEV_PCT = 8.0


def replace_macro_window(db, symbol: str, prices: list, *, source: str = "yfinance realtime-macro") -> dict[str, Any]:
    """prices: schemas.PriceData 목록(날짜 오름차순 아님도 가능). 반환 {ok, replaced, inserted, reason}."""
    if symbol not in REPAIRABLE or not prices:
        return {"ok": False, "reason": "not_repairable"}
    rows = {}
    for p in prices:
        d = p.date.strftime("%Y-%m-%d") if hasattr(p.date, "strftime") else str(p.date)[:10]
        if p.close and p.close > 0:
            rows[d] = p
    if not rows:
        return {"ok": False, "reason": "no_valid_rows"}
    lo = min(rows)
    existing = {r[0]: r for r in db.execute(
        text("SELECT date, open, high, low, close, volume FROM price_history WHERE stock_code=:s AND date>=:lo"),
        {"s": symbol, "lo": lo}).fetchall()}

    limit = MAX_DEV_PCT.get(symbol, DEFAULT_MAX_DEV_PCT)
    worst = 0.0
    for d, old in existing.items():
        if d in rows and old[4] and float(old[4]) > 0:
            worst = max(worst, abs(float(rows[d].close) / float(old[4]) - 1) * 100)
    if worst > limit:
        logger.error(f"[MacroRepair] {symbol}: 겹침 구간 최대 편차 {worst:.2f}% > 한도 {limit}% — 교체 거부(시계열 불일치 의심)")
        return {"ok": False, "reason": f"deviation_{worst:.1f}pct_over_{limit}"}

    now = datetime.now()
    run_id = f"macro_window_{symbol}_{now:%Y%m%d_%H%M%S}"
    stamp = now.isoformat(timespec="seconds")
    replaced = inserted = 0
    try:
        db.execute(text("SELECT set_config('app.price_basis_checked','1',true)"))
        for d, p in sorted(rows.items()):
            old = existing.get(d)
            db.execute(text(
                "INSERT INTO price_history_fix_backup(run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,"
                "new_open,new_high,new_low,new_close,new_volume,reason,fixed_at) "
                "VALUES(:rid,:s,:d,:oo,:oh,:ol,:oc,:ov,:no,:nh,:nl,:nc,:nv,:why,:at)"),
                {"rid": run_id, "s": symbol, "d": d,
                 "oo": old[1] if old else None, "oh": old[2] if old else None, "ol": old[3] if old else None, "oc": old[4] if old else None, "ov": old[5] if old else None,
                 "no": p.open, "nh": p.high, "nl": p.low, "nc": p.close, "nv": p.volume,
                 "why": "macro_window_replace(stale_or_snapshot_rows)", "at": stamp})
            if old:
                db.execute(text("UPDATE price_history SET open=:o, high=:h, low=:l, close=:c, volume=:v WHERE stock_code=:s AND date=:d"),
                           {"o": p.open, "h": p.high, "l": p.low, "c": p.close, "v": p.volume, "s": symbol, "d": d})
                replaced += 1
            else:
                db.execute(text("INSERT INTO price_history(stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy) "
                                "VALUES(:s,:d,:o,:h,:l,:c,:v,0,0)"),
                           {"s": symbol, "d": d, "o": p.open, "h": p.high, "l": p.low, "c": p.close, "v": p.volume})
                inserted += 1
        db.execute(text(
            "INSERT INTO data_fix_log(id,fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) "
            "SELECT COALESCE(MAX(id),0)+1,:at,'price_history',:scope,:n,:rule,:old,:new,:src,:rid FROM data_fix_log"),
            {"at": stamp, "scope": f"{symbol} {lo}~{max(rows)}", "n": replaced + inserted, "rule": "macro_window_replace",
             "old": f"replaced {replaced} rows (max overlap dev {worst:.2f}%)", "new": f"inserted {inserted} new rows; latest {max(rows)} close={rows[max(rows)].close}",
             "src": source, "rid": run_id})
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info(f"[MacroRepair] {symbol}: 교체 {replaced} · 신규 {inserted} (최대 편차 {worst:.2f}%, run_id={run_id})")
    return {"ok": True, "replaced": replaced, "inserted": inserted, "max_dev_pct": round(worst, 2), "run_id": run_id, "latest": max(rows)}
