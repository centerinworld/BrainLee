"""
validate_kiwoom_sector_flow_signal.py — ka10051(업종별투자자순매수) 신호 효용성 모니터링.

배경(2026-09-05/06): ka10051은 키움이 "오늘 시점" 스냅샷만 주는 API라 과거 날짜를
조회할 방법이 없고, 기존 stock_universe.sector_large(GICS식 11대분류)와 ka10051의
KRX 표준업종(21종)이 분류체계 자체가 달라 price_history로 과거를 재구성할 수도 없다.
따라서 "이 시그널이 쓸모 있는지"는 매일 쌓이는 실측 스냅샷으로만 검증 가능 —
이 스크립트는 그 실측 검증을 수행한다.

방법: 스냅샷은 하루 1회(평일 19:00, scheduler._loop_kiwoom_sector_flow) 적재되고,
각 스냅샷 자체에 그 날짜 기준 업종 등락률(flu_rt)이 함께 들어있다. 그러므로
"day T의 수급(frgnr_netprps+orgn_netprps)"과 "day T+1의 flu_rt(다음날 수익률)"를
연속된 두 스냅샷 사이에서 바로 비교할 수 있다 — 별도 수집 없이 기존 테이블만으로
walk-forward 검증이 가능하다.

judgement: 표본이 너무 적으면(day-pair 수 기준) "검증 근거 부족"이라고 명시하고
절대 성급한 채택/기각 판정을 내리지 않는다 — 이 프로젝트의 반복된 교훈(사전검증
lift가 실행가능 전략에 전이 안 됨)을 반영.

사용법:
    python3 scripts/validate_kiwoom_sector_flow_signal.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_utils import connect_stock_db  # noqa: E402

MIN_DAY_PAIRS_FOR_VERDICT = 15  # 이 미만이면 상관계수를 계산해도 "판정 보류"로만 보고


def _num(v) -> float | None:
    try:
        s = str(v).replace(",", "").strip()
        return float(s) if s not in ("", "None") else None
    except (TypeError, ValueError):
        return None


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    """외부 의존성(scipy/pandas) 없이 순수 파이썬으로 스피어만 상관계수 계산."""
    n = len(pairs)
    if n < 3:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    def rank(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    mean_rx, mean_ry = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((v - mean_rx) ** 2 for v in rx)
    var_y = sum((v - mean_ry) ** 2 for v in ry)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x * var_y) ** 0.5


def main() -> dict:
    conn = connect_stock_db(timeout=30)
    try:
        rows = conn.execute("""
            SELECT snapshot_at, market_type, sector_code, sector_name, raw_json
            FROM kiwoom_sector_investor_net_buy
            ORDER BY market_type, snapshot_at
        """).fetchall()
    finally:
        conn.close()

    # market_type -> trade_date(YYYY-MM-DD, snapshot_at 앞 10자리) -> sector_code -> parsed row
    by_market: dict[str, dict[str, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    for snapshot_at, market_type, sector_code, sector_name, raw_json in rows:
        trade_date = str(snapshot_at)[:10]
        d = json.loads(raw_json)
        by_market[market_type][trade_date][sector_code] = {
            "name": sector_name,
            "frgn": _num(d.get("frgnr_netprps")),
            "orgn": _num(d.get("orgn_netprps")),
            "flu_rt": _num(d.get("flu_rt")),
        }

    report: dict = {"markets": {}}
    for market_type, by_date in by_market.items():
        dates = sorted(by_date.keys())
        day_pairs = list(zip(dates[:-1], dates[1:]))
        flow_vs_next_return: list[tuple[float, float]] = []
        hit_count = 0
        total_count = 0

        for day_t, day_t1 in day_pairs:
            sectors_t, sectors_t1 = by_date[day_t], by_date[day_t1]
            common = set(sectors_t) & set(sectors_t1)
            # "종합(KOSPI/KOSDAQ)"·"대형주/중형주/소형주"류 지수성 행은 개별 업종
            # 수급 검증 대상이 아니므로 제외 (섹터 로테이션 후보군만 본다).
            common = {c for c in common if sectors_t[c]["name"] not in
                      ("종합(KOSPI)", "종합(KOSDAQ)", "대형주", "중형주", "소형주")}
            for code in common:
                flow = sectors_t[code]["frgn"]
                orgn = sectors_t[code]["orgn"]
                next_ret = sectors_t1[code]["flu_rt"]
                if flow is None or orgn is None or next_ret is None:
                    continue
                combined_flow = flow + orgn
                flow_vs_next_return.append((combined_flow, next_ret))
                total_count += 1
                if (combined_flow > 0) == (next_ret > 0):
                    hit_count += 1

        corr = _spearman(flow_vs_next_return)
        hit_rate = (hit_count / total_count * 100) if total_count else None
        market_report = {
            "days_collected": len(dates),
            "day_pairs_available": len(day_pairs),
            "sector_day_observations": total_count,
            "spearman_corr_flow_vs_next_return": round(corr, 4) if corr is not None else None,
            "same_direction_hit_rate_pct": round(hit_rate, 1) if hit_rate is not None else None,
            "verdict": None,
        }
        if len(day_pairs) < MIN_DAY_PAIRS_FOR_VERDICT:
            market_report["verdict"] = (
                f"판정 보류 — 연속 관측일 쌍 {len(day_pairs)}개 "
                f"(최소 {MIN_DAY_PAIRS_FOR_VERDICT}개 필요). 매일 19:00 수집이 계속 쌓이는 대로 재실행할 것."
            )
        elif corr is None:
            market_report["verdict"] = "판정 보류 — 상관계수 계산 불가(분산 0 또는 표본 부족)."
        else:
            market_report["verdict"] = (
                f"참고용 — 상관계수 {corr:+.3f}, 동일방향 적중률 {hit_rate:.1f}% "
                f"(n={total_count}). 채택/기각 결정은 실행 백테스트(signal_experiment_ledger) "
                f"전 단계 참고 자료로만 사용할 것."
            )
        report["markets"][market_type] = market_report

    return report


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
