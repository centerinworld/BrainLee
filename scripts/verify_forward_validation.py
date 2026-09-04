"""전략센터 라이브 가상매매(페이퍼트레이딩) 실측 데이터를 근거로
`forward_validation` 검증 아티팩트를 등록하는 스크립트.

2026-08-13: governance의 live_eligible 승격 조건이 rank>=forward_validated
(실측 전방검증)로 강화됐으나, 이 아티팩트를 채우는 파이프라인 자체가 없어서
어떤 전략도 도달 불가능한 상태였음. 이 스크립트는:
  1. 각 전략의 라이브 가상매매(peak_holding/peak_trade) 실적을 조회
  2. 최소 조건(운용기간/거래건수/파산적손실없음)을 확인
  3. 조건 충족 시에만 forward_validation 아티팩트를 passed=True로 등록
  4. 조건 미충족이면 passed=False로 등록(이유를 details_json에 기록) — 이는
     "아직 이르다"는 것을 정직하게 기록하는 것이지 실패로 간주하지 않는다.

⚠️ 최소 조건은 의도적으로 보수적으로 설정(운용 60일+, 완결거래 20건+, 계좌
전체 -30% 초과손실 없음) — 며칠치 데이터로 성급하게 통과시키지 않기 위함.
이 스크립트는 실행할 때마다 현재 실측 데이터를 재평가하므로, scheduler.py에
매일 1회 등록해 시간이 지나면 자동으로 조건을 충족하는지 재확인하도록 한다.

사용법:
    venv/bin/python3 scripts/verify_forward_validation.py
"""
from __future__ import annotations

import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db_compat
import config
from run_registry import register_artifact, registry as selected_registry, derive_status, DB_PATH

# backtest.py 전략 키 -> 현재 전략센터의 독립 가상계좌 키 매핑.
#
# 2026-08-25 정정(사용자 지적 반영): 원래 여기 있던 "sc_*"는 실제로 존재한 적 없는
# 계좌였다(2026-08-25 점검 결과 5개 전략 전부 첫 거래일자 NULL — 카운트다운 자체가
# 시작 안 함). 실제로 이미 운영 중인 v_gc(2026-07-09~)·v_contract_momentum(2026-08-09~)
# 계좌가 있는데도 "소급 편입 금지"를 이유로 무시하고 있었음.
#
# 재검토: point_in_time_verified 등급은 **백테스트(과거 구간 시뮬레이션의 시총 룩어헤드
# 편향)** 검증이다. 실시간 가상매매는 항상 "오늘 시점의 실제 데이터"만 사용하므로
# 애초에 룩어헤드가 성립하지 않는 별개 문제 — 백테스트가 나중에 검증됐다고 해서
# 이미 돌던 라이브 계좌의 과거 거래가 오염되는 것은 아니다. 따라서 기존 v_* 계좌를
# 그대로 사후검증 근거로 인정한다.
STRATEGY_TO_LIVE_KEY = {
    "contract_momentum": "v_contract_momentum",  # 2026-08-09~ 이미 운영 중
    "golden_cross": "v_gc",                       # 2026-07-09~ 이미 운영 중
    # sector_focus/v5/v10: 아직 실제 실행 인프라 자체가 없음(routes/trend.py에
    # 해당 없음) — 계좌가 생기기 전까지는 매핑하지 않는다(허위 PENDING 대신 미포함).
}

MIN_DAYS_LIVE = 60          # 최소 운용기간(일)
MIN_COMPLETED_TRADES = 20   # 최소 완결(매도) 거래건수
MAX_ACCOUNT_DRAWDOWN_PCT = -30.0  # 계좌 전체 허용 최대 손실률(초기자본 대비)


def _account_seed_krw(live_key: str, conn) -> float:
    # routes/trend.py의 각 전략 시드자본 관례(1억원)를 그대로 사용.
    # 콤보류(combo_*)는 이 스크립트 대상이 아님.
    return 100_000_000.0


def evaluate_strategy(strategy_key: str, live_key: str) -> dict:
    conn = db_compat.connect_primary_db(timeout=60)
    cur = conn.cursor()
    ph = "%s" if config.IS_POSTGRES else "?"

    cur.execute(f"SELECT MIN(entry_date) as first_entry FROM peak_holding WHERE strategy={ph}", (live_key,))
    row = cur.fetchone()
    first_entry = row["first_entry"] if row else None

    cur.execute(f"SELECT COUNT(*) as n FROM peak_trade WHERE strategy={ph} AND tx_type='sell'", (live_key,))
    completed_trades = cur.fetchone()["n"]

    # 계좌 평가액 = 시드자본 + 실현손익 누적(매도 시 profit) + 보유중 포지션 평가손익
    cur.execute(f"SELECT COALESCE(SUM(profit),0) as realized FROM peak_trade WHERE strategy={ph} AND tx_type='sell'", (live_key,))
    realized = float(cur.fetchone()["realized"] or 0)
    cur.execute(
        f"SELECT COALESCE(SUM((current_price-buy_price)*quantity),0) as unrealized "
        f"FROM peak_holding WHERE strategy={ph} AND is_active=1", (live_key,)
    )
    unrealized = float(cur.fetchone()["unrealized"] or 0)

    seed = _account_seed_krw(live_key, conn)
    account_value = seed + realized + unrealized
    account_dd_pct = (account_value - seed) / seed * 100 if seed else 0.0

    days_live = None
    if first_entry:
        try:
            fe = datetime.strptime(str(first_entry)[:10], "%Y-%m-%d").date()
            days_live = (date.today() - fe).days
        except ValueError:
            days_live = None

    reasons = []
    if days_live is None or days_live < MIN_DAYS_LIVE:
        reasons.append(f"운용기간 부족({days_live}일 < {MIN_DAYS_LIVE}일)")
    if completed_trades < MIN_COMPLETED_TRADES:
        reasons.append(f"완결거래 부족({completed_trades}건 < {MIN_COMPLETED_TRADES}건)")
    if account_dd_pct < MAX_ACCOUNT_DRAWDOWN_PCT:
        reasons.append(f"계좌손실 과다({account_dd_pct:.1f}% < {MAX_ACCOUNT_DRAWDOWN_PCT}%)")

    passed = not reasons
    details = {
        "live_key": live_key, "first_entry": str(first_entry) if first_entry else None,
        "days_live": days_live, "completed_trades": completed_trades,
        "realized_pnl_krw": round(realized), "unrealized_pnl_krw": round(unrealized),
        "account_value_krw": round(account_value), "account_return_pct": round(account_dd_pct, 2),
        "min_days_required": MIN_DAYS_LIVE, "min_trades_required": MIN_COMPLETED_TRADES,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "reasons": reasons,
    }
    conn.close()
    return {"passed": passed, "details": details}


def main() -> None:
    conn = db_compat.connect_primary_db(timeout=60)
    registry_rows = selected_registry(DB_PATH, report_type="strategy_center", include_verification=False)
    by_strategy = {}
    for row in registry_rows:
        by_strategy.setdefault(row["strategy"], row["run_hash"])

    for strategy_key, live_key in STRATEGY_TO_LIVE_KEY.items():
        suite_hash = by_strategy.get(strategy_key)
        if not suite_hash:
            print(f"[스킵] {strategy_key}: strategy_center에 등록된 run_hash 없음")
            continue
        result = evaluate_strategy(strategy_key, live_key)
        status = derive_status(conn, suite_hash)
        components = status.get("components", [])
        if not components:
            print(f"[스킵] {strategy_key}: suite 구성요소를 찾을 수 없음(단일 run일 가능성)")
            continue
        for comp in components:
            register_artifact(comp["run_hash"], "forward_validation", result["passed"], result["details"])
        verdict = "PASS" if result["passed"] else "PENDING"
        print(f"[{verdict}] {strategy_key} ({live_key}): {result['details']}")
    conn.close()


if __name__ == "__main__":
    main()
