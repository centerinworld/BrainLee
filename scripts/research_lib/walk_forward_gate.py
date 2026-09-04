"""공용 워크포워드(학습/검증 분리) 신호 검증 헬퍼.

2026-07-28 세션 배경: 같은 밤에만도 두 가지 사고가 있었음 —
①매크로지표×섹터 42개 조합 중 21개가 "학습/검증 분리 없이 전체기간 통계량만으로" promoted
  판정을 받아 단일 레짐(COMM_COPPER×전력기기 등) 관측을 재현가능한 신호로 착각.
②전략조합 최고기록 API가 "measurement window가 짧아 최근 조정을 덜 반영한 낡은 등록"을
  숫자가 높다는 이유만으로 "현재 최고"로 잘못 반환.
매번 스크립트마다 학습/검증 분리 로직을 새로 짜다 보니 `_date_ok()` 필터 누락 같은 실수가
반복됐음(scratch/combo_optimize_asof_20260728.py 1차 시도). 이 모듈은 그 반복을 없애기 위한
단일 공용 구현 — 향후 신호검증 스크립트는 이 모듈을 import해서 재사용할 것, 재구현 금지.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence


@dataclass
class ObservationSummary:
    n: int = 0
    avg_ret: float = None
    median_ret: float = None
    hit_rate_pct: float = None
    loss30_pct: float = None  # -30% 이하로 손실난 관측치 비율(꼬리위험)
    win_gt: float = 0.0       # "성공" 판정 임계값(기본 0% 초과)

    def to_dict(self) -> dict:
        return {
            "n": self.n, "avg_ret": self.avg_ret, "median_ret": self.median_ret,
            "hit_rate_pct": self.hit_rate_pct, "loss30_pct": self.loss30_pct,
        }


def summarize(returns: Sequence[float], win_gt: float = 0.0) -> ObservationSummary:
    """returns: 개별 관측치의 수익률 리스트(소수, 0.10=+10%). 빈 리스트면 n=0 반환(0 아님 — 판단불가와 구분)."""
    vals = [float(r) for r in returns if r is not None]
    if not vals:
        return ObservationSummary(n=0)
    n = len(vals)
    avg = sum(vals) / n
    sorted_vals = sorted(vals)
    mid = n // 2
    median = sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    hits = sum(1 for v in vals if v > win_gt)
    losses30 = sum(1 for v in vals if v <= -0.30)
    return ObservationSummary(
        n=n, avg_ret=avg, median_ret=median,
        hit_rate_pct=100.0 * hits / n, loss30_pct=100.0 * losses30 / n, win_gt=win_gt,
    )


@dataclass
class WalkForwardResult:
    train: ObservationSummary
    test: ObservationSummary
    direction_consistent: bool  # train avg 부호 == test avg 부호 (둘 다 0이 아닐 때)
    walk_forward_ok: bool       # 최소 학습표본 충족 + 방향 일치
    verdict: str                # "pass" | "fail_insufficient_train" | "fail_direction_flip" | "fail_no_test_data"
    reason: str

    def to_dict(self) -> dict:
        return {
            "train": self.train.to_dict(), "test": self.test.to_dict(),
            "direction_consistent": self.direction_consistent,
            "walk_forward_ok": self.walk_forward_ok,
            "verdict": self.verdict, "reason": self.reason,
        }


def evaluate(
    observations: Iterable[dict],
    date_key: str = "entry_date",
    return_key: str = "ret",
    train_cutoff: str = "2023-01-01",
    min_train_n: int = 10,
    min_test_n: int = 5,
    win_gt: float = 0.0,
) -> WalkForwardResult:
    """observations: [{date_key: 'YYYY-MM-DD', return_key: 0.12, ...}, ...] 형태의 리스트.
    train_cutoff 이전은 학습, 이후(포함)는 검증으로 자동 분리 — 호출부에서 직접 날짜 비교
    로직을 짜지 말고 반드시 이 함수를 통해 나눌 것(재구현 시 경계값 오프바이원 등 실수 반복 위험)."""
    train_rets, test_rets = [], []
    for obs in observations:
        d = str(obs.get(date_key) or "")[:10]
        r = obs.get(return_key)
        if not d or r is None:
            continue
        (train_rets if d < train_cutoff else test_rets).append(r)

    train = summarize(train_rets, win_gt=win_gt)
    test = summarize(test_rets, win_gt=win_gt)

    if train.n < min_train_n:
        return WalkForwardResult(
            train=train, test=test, direction_consistent=False, walk_forward_ok=False,
            verdict="fail_insufficient_train",
            reason=f"학습기간(< {train_cutoff}) 관측치 {train.n}건 < 최소 {min_train_n}건 — "
                    "단일 레짐/우연 관측일 위험이 커 검증 자체가 성립하지 않음.",
        )
    if test.n < min_test_n:
        return WalkForwardResult(
            train=train, test=test, direction_consistent=False, walk_forward_ok=False,
            verdict="fail_no_test_data",
            reason=f"검증기간(>= {train_cutoff}) 관측치 {test.n}건 < 최소 {min_test_n}건 — "
                    "아웃오브샘플 검증이 불가능.",
        )

    consistent = (train.avg_ret is not None and test.avg_ret is not None
                  and ((train.avg_ret > 0) == (test.avg_ret > 0)))
    if not consistent:
        return WalkForwardResult(
            train=train, test=test, direction_consistent=False, walk_forward_ok=False,
            verdict="fail_direction_flip",
            reason=f"학습기 평균수익 {train.avg_ret:+.1%} vs 검증기 평균수익 {test.avg_ret:+.1%} — "
                    "부호가 뒤집혀 재현 가능한 신호로 볼 수 없음(과최적화 의심).",
        )
    return WalkForwardResult(
        train=train, test=test, direction_consistent=True, walk_forward_ok=True,
        verdict="pass",
        reason=f"학습 {train.avg_ret:+.1%}(n={train.n}) → 검증 {test.avg_ret:+.1%}(n={test.n}), "
                "방향 일치 — 워크포워드 통과.",
    )


def max_gap_price_lookup(conn, stock_code: str, available_date: str, max_gap_days: int = 10,
                          price_table: str = "price_history"):
    """신호일 이후 첫 거래가능일 종가를 상한(max_gap_days) 내에서만 조회.
    2026-07-28 밤 두 스크립트(research_strategy_overlay_expansion.py,
    backtest_macro_indicator_candidates.py)에서 동일 클래스(상한 없는 'date>=신호일' 쿼리로
    거래정지/데이터공백 구간이 조용히 몇 년 뒤 가격에 체결되는) 버그가 각각 독립적으로
    발견됨 — 이후 신호검증 스크립트는 직접 SQL을 짜지 말고 이 함수를 재사용할 것."""
    row = conn.execute(
        f"SELECT date, close FROM {price_table} WHERE stock_code=? AND date>=? "
        "AND close IS NOT NULL AND close>0 ORDER BY date LIMIT 1",
        (stock_code, available_date),
    ).fetchone()
    if not row:
        return None
    price_date = str(row[0])[:10]
    gap_days = (
        __import__("datetime").date.fromisoformat(price_date)
        - __import__("datetime").date.fromisoformat(str(available_date)[:10])
    ).days
    if gap_days > max_gap_days:
        return None
    return {"date": price_date, "close": float(row[1]), "gap_days": gap_days}
