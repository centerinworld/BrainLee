"""가격 급변 감사의 규칙 기반 후처리 — 2026-09-28.

price_jump_audit는 원인이 확정되지 않은 큰 가격 변동을 모두 return_usable=0으로 둔다.
그중 "외부 원시세가 같은 비율을 확인했고, 가격제한폭 안이며, 주변에 자본행위·자본 공시·
거래정지 공백이 없는" 변동은 실제 시장 급등락(상·하한가 등)이다 — 이런 날을 계속
사용불가로 두면 그 종목을 보유한 모든 백테스트가 가격 감사에서 실패한다
(예: 091990 셀트리온헬스케어 2020-03-31 +30% 상한가).

apply_market_move_rule()은 감사 스크립트와 네이버 검증 스크립트가 끝날 때 호출된다
(네이버 스크립트는 승격 전에 return_usable을 전부 0으로 초기화하므로 둘 다에서 호출해야 한다).
"""
from __future__ import annotations

from datetime import date, timedelta

MARKET_MOVE_CLASS = "confirmed_market_move"
REVIEW_CLASSES = ("raw_source_confirmed_jump_review", "externally_confirmed_price_jump_review")
LIMIT_CHANGE_DATE = "2015-06-15"  # 가격제한폭 ±15% → ±30%
CAPITAL_KEYWORDS = ("분할", "병합", "무상증자", "유상증자", "감자", "상장폐지", "합병", "권리락", "거래정지")


def _band(event_date: str) -> tuple[float, float]:
    limit = 0.30 if event_date >= LIMIT_CHANGE_DATE else 0.15
    return 1 - limit - 0.003, 1 + limit + 0.003


def is_market_move(conn, row: dict) -> tuple[bool, str]:
    code, event_date = row["stock_code"], str(row["event_date"])[:10]
    ratio, public = row.get("price_ratio"), row.get("public_price_ratio")
    if ratio is None or public is None:
        return False, "no_public_raw_ratio"
    if abs(float(public) - float(ratio)) > 0.005 * abs(float(ratio)):
        return False, "public_raw_disagrees"
    lo, hi = _band(event_date)
    if not (lo <= float(ratio) <= hi):
        return False, "outside_daily_price_limit"
    prev = str(row.get("previous_date") or "")[:10]
    d = date.fromisoformat(event_date)
    if not prev or (d - date.fromisoformat(prev)).days > 7:
        return False, "gap_before_event(halt_or_listing)"
    near = conn.execute(
        """SELECT 1 FROM corporate_action_events WHERE stock_code=? AND event_date BETWEEN ? AND ?
             AND COALESCE(adjustment_status,'')<>'not_price_adjusting' LIMIT 1""",
        (code, (d - timedelta(days=25)).isoformat(), (d + timedelta(days=25)).isoformat()),
    ).fetchone()
    if near:
        return False, "corporate_action_within_25d"
    disclosure = conn.execute(
        "SELECT report_nm FROM dart_disclosures WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?",
        (code, (d - timedelta(days=10)).strftime("%Y%m%d"), (d + timedelta(days=3)).strftime("%Y%m%d")),
    ).fetchall()
    if any(any(k in str(r[0] or "") for k in CAPITAL_KEYWORDS) for r in disclosure):
        return False, "capital_disclosure_nearby"
    return True, "within_daily_limit+public_raw_confirms+no_capital_event"


def apply_market_move_rule(conn) -> dict:
    rows = conn.execute(
        f"""SELECT stock_code,event_date,previous_date,price_ratio,public_price_ratio,classification
            FROM price_jump_audit WHERE classification IN ({','.join('?' * len(REVIEW_CLASSES))})
               OR classification=?""",
        (*REVIEW_CLASSES, MARKET_MOVE_CLASS),
    ).fetchall()
    promoted = kept = 0
    for r in rows:
        row = dict(zip(("stock_code", "event_date", "previous_date", "price_ratio", "public_price_ratio",
                        "classification"), tuple(r)))
        ok, why = is_market_move(conn, row)
        if ok:
            conn.execute(
                """UPDATE price_jump_audit SET classification=?, return_usable=1,
                     evidence=CASE WHEN evidence LIKE '%[market_move_rule]%' THEN evidence
                                   ELSE COALESCE(evidence,'') || ' [market_move_rule] ' || ? END
                   WHERE stock_code=? AND event_date=?""",
                (MARKET_MOVE_CLASS, why, row["stock_code"], row["event_date"]),
            )
            promoted += 1
        elif row["classification"] == MARKET_MOVE_CLASS:
            # 새 증거(자본행위 등록 등)로 조건이 깨지면 다시 검토 대상으로 되돌린다.
            conn.execute(
                """UPDATE price_jump_audit SET classification='raw_source_confirmed_jump_review', return_usable=0,
                     evidence=COALESCE(evidence,'') || ' [market_move_rule revoked: ' || ? || ']'
                   WHERE stock_code=? AND event_date=?""",
                (why, row["stock_code"], row["event_date"]),
            )
            kept += 1
    conn.commit()
    return {"market_move_usable": promoted, "revoked": kept}
