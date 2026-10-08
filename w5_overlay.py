"""W5 분포 오버레이 — 매트릭스 구간 수익률을 W5 무작위 12회 중앙값으로 바꾼다(2026-10-08 W6, REVIEW_PLAN §34-4 ①·§36-3 ②·§39).

import 시 DB 연결 등 부작용이 없다(routes.backtest는 import 때 init_backtest_db()로 DB에 연결하므로 헬퍼를 여기로 분리).
"""
from __future__ import annotations


def _latest_w5_label(conn):
    """적용할 W5 라벨 — 가장 최근 적재(created_at) 순. 라벨 문자열 정렬(MAX)은 시간 순서를 보장하지 않는다(§36-3 ②)."""
    try:
        if hasattr(conn, "_connection") and not conn.execute("SELECT to_regclass('strategy_w5_distribution')").fetchone()[0]:
            return None
        row = conn.execute("SELECT w5_label FROM strategy_w5_distribution "
                           "ORDER BY created_at DESC NULLS LAST, w5_label DESC LIMIT 1").fetchone()
    except Exception:
        return None
    return row[0] if row else None


def _w5_rows(conn, label):
    return conn.execute(
        "SELECT strategy, period_label, median_return_pct, q25_return_pct, min_return_pct, max_return_pct, "
        "mdd_median_pct, score_return_pct, n_random, order_note, data_fingerprint, representative_run_id "
        "FROM strategy_w5_distribution WHERE w5_label=?", (label,)).fetchall()


def _apply_w5_overlay(ordered: list, rows: list, label) -> None:
    """구간 수익률 = W5 중앙값, MDD = W5 MDD 중앙(있을 때). 이전 값은 pre_correction_*로 보존."""
    by = {}
    for r in rows:
        by.setdefault(str(r[0]), {})[str(r[1])] = r
    for strategy in ordered:
        per = by.get(strategy["strategy"])
        if not per:
            continue
        for lab, r in per.items():
            p_ = strategy["periods"].get(lab)
            if p_ is None:
                continue
            p_["pre_correction_return_pct"] = p_.get("total_return_pct")
            p_["pre_correction_mdd"] = p_.get("mdd")
            p_["pre_correction_run_id"] = p_.get("run_id")
            p_["total_return_pct"] = round(float(r[2]), 2) if r[2] is not None else None
            if r[6] is not None:
                p_["mdd"] = round(float(r[6]), 2)
            p_["w5"] = {"label": label, "median_return_pct": r[2], "q25_return_pct": r[3], "min_return_pct": r[4],
                        "max_return_pct": r[5], "mdd_median_pct": r[6], "score_return_pct": r[7], "n_random": r[8],
                        "order_note": r[9], "data_fingerprint": r[10], "representative_run_id": r[11]}
        strategy["w5_applied"] = label
