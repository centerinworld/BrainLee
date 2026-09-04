#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from quant_indicator_signal_engine import classify_signal


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
HORIZONS = (20, 60, 120)


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS macro_signal_backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            sector_name TEXT NOT NULL,
            direction_mode TEXT,
            event_count INTEGER DEFAULT 0,
            observation_count INTEGER DEFAULT 0,
            stock_count INTEGER DEFAULT 0,
            avg_ret_20d REAL,
            median_ret_20d REAL,
            hit_rate_20d REAL,
            avg_ret_60d REAL,
            median_ret_60d REAL,
            hit_rate_60d REAL,
            avg_ret_120d REAL,
            median_ret_120d REAL,
            hit_rate_120d REAL,
            avg_mdd_60d REAL,
            profit_factor_60d REAL,
            pass_flag INTEGER DEFAULT 0,
            promotion_status TEXT,
            criteria_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(run_id, indicator_key, sector_name)
        );
        CREATE INDEX IF NOT EXISTS idx_msbr_indicator_sector
            ON macro_signal_backtest_results(indicator_key, sector_name, created_at DESC);

        CREATE TABLE IF NOT EXISTS macro_signal_backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            signal_period TEXT,
            available_date TEXT,
            entry_date TEXT,
            entry_close REAL,
            ret_20d REAL,
            ret_60d REAL,
            ret_120d REAL,
            mdd_60d REAL,
            signal_type TEXT,
            signal_strength REAL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msbt_run
            ON macro_signal_backtest_trades(run_id, indicator_key, sector_name);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_msbt_signal_trade
            ON macro_signal_backtest_trades(run_id, indicator_key, sector_name, stock_code, signal_period, available_date);
        """
    )
    conn.commit()


def parse_period_available_date(period: str) -> str | None:
    if not period:
        return None
    period = str(period)
    try:
        if len(period) >= 10:
            dt = datetime.strptime(period[:10], "%Y-%m-%d") + timedelta(days=1)
        elif len(period) == 7:
            dt = datetime.strptime(period + "-01", "%Y-%m-%d") + timedelta(days=35)
        elif len(period) == 4:
            dt = datetime.strptime(period + "-01-01", "%Y-%m-%d") + timedelta(days=120)
        else:
            return None
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def load_series(conn: sqlite3.Connection, indicator_key: str, series_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT period, value
        FROM quant_major_indicator_series
        WHERE indicator_key=? AND series_name=? AND value IS NOT NULL
        ORDER BY period
        """,
        (indicator_key, series_name),
    ).fetchall()


def period_yoy_key(period: str) -> str | None:
    if not period or len(period) < 4:
        return None
    try:
        return f"{int(period[:4]) - 1}{period[4:]}"
    except ValueError:
        return None


def direction_to_light(signal_type: str, direction_mode: str | None) -> str:
    mode = direction_mode or "higher_is_good"
    if signal_type == "spike_up":
        if mode == "higher_is_good":
            return "green"
        if mode == "higher_is_bad":
            return "red"
        return "yellow"
    if signal_type == "spike_down":
        if mode == "higher_is_good":
            return "red"
        if mode == "higher_is_bad":
            return "green"
        return "yellow"
    return "gray"


# 2026-07-28 (Claude): docs/codex_handoff_fill_timing_artifact_recheck_20260728.md에서
# 확인된 것과 동일한 클래스의 체결시점 룩어헤드 방지 — available_date 이후 첫 가격행을
# 상한 없이 가져오면, 거래정지/데이터공백이 있는 종목은 훨씬 뒤 시점 가격에 조용히
# "체결"된 것처럼 계산될 수 있음(Codex가 research_strategy_overlay_expansion.py에서
# 발견한 버그와 동일 패턴). max_gap_days 초과 시 아예 관측치로 채택하지 않음.
MAX_ENTRY_GAP_DAYS = 10


def price_path(conn: sqlite3.Connection, stock_code: str, available_date: str, max_horizon: int,
                max_gap_days: int = MAX_ENTRY_GAP_DAYS) -> list[sqlite3.Row]:
    try:
        avail_dt = datetime.strptime(available_date[:10], "%Y-%m-%d")
    except ValueError:
        return []
    # Calendar upper bound prevents 60/120-trading-row returns from quietly
    # becoming much longer holding periods after suspensions or sparse history.
    upper_date = (avail_dt + timedelta(days=max_horizon * 2 + max_gap_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code=? AND date>=? AND date<=? AND close IS NOT NULL AND close>0
        ORDER BY date
        LIMIT ?
        """,
        (stock_code, available_date, upper_date, max_horizon + 1),
    ).fetchall()
    if not rows:
        return []
    try:
        gap = (datetime.strptime(str(rows[0]["date"])[:10], "%Y-%m-%d") - avail_dt).days
    except ValueError:
        return []
    if gap > max_gap_days:
        return []
    return rows


def pct(curr: float, base: float) -> float | None:
    if not base:
        return None
    return (curr / base - 1.0) * 100.0


def evaluate_path(path: list[sqlite3.Row]) -> dict | None:
    if len(path) <= max(HORIZONS):
        return None
    entry = float(path[0]["close"])
    if entry <= 0:
        return None
    out = {
        "entry_date": str(path[0]["date"])[:10],
        "entry_close": entry,
    }
    for h in HORIZONS:
        out[f"ret_{h}d"] = pct(float(path[h]["close"]), entry)
    lows = [pct(float(r["close"]), entry) for r in path[:61]]
    out["mdd_60d"] = min(v for v in lows if v is not None)
    return out


def summarize(values: list[dict]) -> dict:
    out: dict[str, float | int | None] = {"observation_count": len(values)}
    for h in HORIZONS:
        key = f"ret_{h}d"
        vals = [float(v[key]) for v in values if v.get(key) is not None and math.isfinite(float(v[key]))]
        out[f"avg_ret_{h}d"] = sum(vals) / len(vals) if vals else None
        out[f"median_ret_{h}d"] = median(vals) if vals else None
        out[f"hit_rate_{h}d"] = sum(1 for v in vals if v > 0) / len(vals) * 100.0 if vals else None
    mdds = [float(v["mdd_60d"]) for v in values if v.get("mdd_60d") is not None]
    out["avg_mdd_60d"] = sum(mdds) / len(mdds) if mdds else None
    vals60 = [float(v["ret_60d"]) for v in values if v.get("ret_60d") is not None]
    gains = sum(v for v in vals60 if v > 0)
    losses = abs(sum(v for v in vals60 if v < 0))
    out["profit_factor_60d"] = gains / losses if losses > 0 else (999.0 if gains > 0 else None)
    return out


# 2026-07-28 (Claude): docs/claude_handoff_codex_macro_quant_overfitting_20260728.md에서
# 확인된 과최적화 방지 — 학습(~2022)/검증(2023~) 분리 없이 전체기간 통계량만으로 promoted
# 처리하면 단일 레짐(예: COMM_COPPER×전력기기가 2025-06~2026-01 7개월간만 관측된 것)을
# "검증된 신호"로 착각하게 됨. 학습기간 관측치가 최소 기준 미달이거나 학습기간 방향이
# 검증기간과 반대면 promoted 후보에서 제외.
WALK_FORWARD_CUTOFF = "2023-01-01"
MIN_TRAIN_OBSERVATIONS = 5


def passes(summary: dict, event_count: int, stock_count: int, min_obs: int,
           train_summary: dict | None = None) -> tuple[bool, dict]:
    criteria = {
        "min_observations": min_obs,
        "min_events": 3,
        "min_stocks": 2,
        "avg_ret_60d_min": 3.0,
        "median_ret_60d_min": 0.0,
        "hit_rate_60d_min": 55.0,
        "profit_factor_60d_min": 1.3,
        "avg_mdd_60d_min": -25.0,
        "min_train_observations": MIN_TRAIN_OBSERVATIONS,
        "walk_forward_cutoff": WALK_FORWARD_CUTOFF,
    }
    ok = (
        int(summary.get("observation_count") or 0) >= criteria["min_observations"]
        and event_count >= criteria["min_events"]
        and stock_count >= criteria["min_stocks"]
        and float(summary.get("avg_ret_60d") or -999) >= criteria["avg_ret_60d_min"]
        and float(summary.get("median_ret_60d") or -999) >= criteria["median_ret_60d_min"]
        and float(summary.get("hit_rate_60d") or -999) >= criteria["hit_rate_60d_min"]
        and float(summary.get("profit_factor_60d") or -999) >= criteria["profit_factor_60d_min"]
        and float(summary.get("avg_mdd_60d") or -999) >= criteria["avg_mdd_60d_min"]
    )
    # 학습기간(2023년 이전) 검증 — 단일 레짐(전부 검증기간에만 존재) 승격 방지
    train_n = int((train_summary or {}).get("observation_count") or 0)
    train_avg60 = train_summary.get("avg_ret_60d") if train_summary else None
    walk_forward_ok = bool(
        train_n >= criteria["min_train_observations"]
        and train_avg60 is not None
        and train_avg60 > 0
    )
    criteria["train_observations"] = train_n
    criteria["train_avg_ret_60d"] = train_avg60
    criteria["walk_forward_ok"] = walk_forward_ok
    return (ok and walk_forward_ok), criteria


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest macro indicator candidate mappings and promote validated combinations.")
    parser.add_argument("--min-obs", type=int, default=30)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    run_id = f"macro_candidate_bt_{datetime.now():%Y%m%d_%H%M%S}"
    ts = now_ts()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_tables(conn)

    # 2026-07-28 (Claude): 기존 run들이 idempotent 정리 없이 누적돼 동일 이벤트가 재실행
    # 횟수만큼(관측 결과 3배) 중복 저장돼 있었음 — 매 실행은 전체 매크로 조합을 다시
    # 전수 스캔하므로, 이전 run들의 macro:% 관련 결과/거래는 정리하고 이번 run만 남긴다.
    conn.execute("DELETE FROM macro_signal_backtest_trades WHERE indicator_key LIKE 'macro:%'")
    conn.execute("DELETE FROM macro_signal_backtest_results WHERE indicator_key LIKE 'macro:%'")
    conn.commit()

    rules = {
        (r["indicator_key"], r["sector_name"]): r
        for r in conn.execute(
            "SELECT indicator_key, sector_name, direction_mode, note FROM indicator_sector_direction_rules WHERE indicator_key LIKE 'macro:%'"
        ).fetchall()
    }
    mappings = conn.execute(
        """
        SELECT m.stock_code, m.stock_name, m.sector_name, m.indicator_key, m.indicator_name,
               q.sector_name AS signal_sector
        FROM cafe_stock_indicator_mappings m
        JOIN cafe_quant_indicator_mappings q
          ON q.indicator_key=m.indicator_key AND q.sector_name=m.sector_name
        WHERE m.indicator_key LIKE 'macro:%'
          AND m.mapping_status IN ('candidate_macro_context', 'confirmed_macro_signal')
        ORDER BY m.indicator_key, m.sector_name, m.stock_code
        """
    ).fetchall()
    by_pair: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in mappings:
        by_pair.setdefault((row["indicator_key"], row["signal_sector"]), []).append(row)

    series_pairs = conn.execute(
        """
        SELECT DISTINCT s.indicator_key, s.series_name, c.epic_indicator_name
        FROM quant_major_indicator_series s
        JOIN quant_major_indicator_catalog c ON c.indicator_key=s.indicator_key
        WHERE s.indicator_key LIKE 'macro:%'
        ORDER BY s.indicator_key, s.series_name
        """
    ).fetchall()

    pair_events: dict[tuple[str, str], list[dict]] = {}
    for pair in series_pairs:
        rows = load_series(conn, pair["indicator_key"], pair["series_name"])
        if len(rows) < 14:
            continue
        by_period = {r["period"]: float(r["value"]) for r in rows}
        history: list[float] = []
        previous_value: float | None = None
        for row in rows:
            value = float(row["value"])
            yoy = by_period.get(period_yoy_key(row["period"]) or "")
            signal_type, strength, mom, yoy_pct, z = classify_signal(value, previous_value, yoy, history)
            history.append(value)
            previous_value = value
            if not signal_type:
                continue
            available_date = parse_period_available_date(row["period"])
            if not available_date:
                continue
            for indicator_key, sector_name in list(by_pair.keys()):
                if indicator_key != pair["indicator_key"]:
                    continue
                rule = rules.get((indicator_key, sector_name))
                light = direction_to_light(signal_type, rule["direction_mode"] if rule else None)
                if light != "green":
                    continue
                pair_events.setdefault((indicator_key, sector_name), []).append(
                    {
                        "indicator_name": pair["epic_indicator_name"],
                        "series_name": pair["series_name"],
                        "period": row["period"],
                        "available_date": available_date,
                        "signal_type": signal_type,
                        "signal_strength": float(strength or 0),
                        "direction_mode": rule["direction_mode"] if rule else None,
                    }
                )

    promoted_pairs: list[tuple[str, str]] = []
    result_count = 0
    trade_count = 0
    for pair_key, stocks in by_pair.items():
        events = pair_events.get(pair_key, [])
        if not events:
            continue
        observations: list[dict] = []
        seen_trade_keys: set[tuple[str, str, str]] = set()
        for event in events:
            for stock in stocks:
                tkey = (stock["stock_code"], event["period"], event["available_date"])
                if tkey in seen_trade_keys:
                    continue
                seen_trade_keys.add(tkey)
                path = price_path(conn, stock["stock_code"], event["available_date"], max(HORIZONS))
                evaluated = evaluate_path(path)
                if not evaluated:
                    continue
                record = {
                    **evaluated,
                    "indicator_key": pair_key[0],
                    "indicator_name": event.get("indicator_name") or pair_key[0],
                    "sector_name": pair_key[1],
                    "stock_code": stock["stock_code"],
                    "stock_name": stock["stock_name"],
                    "signal_period": event["period"],
                    "available_date": event["available_date"],
                    "signal_type": event["signal_type"],
                    "signal_strength": event["signal_strength"],
                }
                observations.append(record)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO macro_signal_backtest_trades
                    (run_id, indicator_key, sector_name, stock_code, stock_name, signal_period,
                     available_date, entry_date, entry_close, ret_20d, ret_60d, ret_120d,
                     mdd_60d, signal_type, signal_strength, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        pair_key[0],
                        pair_key[1],
                        stock["stock_code"],
                        stock["stock_name"],
                        record["signal_period"],
                        record["available_date"],
                        record["entry_date"],
                        record["entry_close"],
                        record["ret_20d"],
                        record["ret_60d"],
                        record["ret_120d"],
                        record["mdd_60d"],
                        record["signal_type"],
                        record["signal_strength"],
                        ts,
                    ),
                )
                trade_count += 1
        if not observations:
            continue
        summary = summarize(observations)
        stock_count = len({v["stock_code"] for v in observations})
        event_count = len({(e["period"], e["series_name"]) for e in events})
        # 2026-07-28 (Claude): 학습(~2022)/검증(2023~) 분리 — 단일 레짐만 관측된 조합을
        # "검증됨"으로 착각하지 않도록 train_summary를 passes()에 함께 전달.
        train_observations = [o for o in observations if o.get("entry_date") and o["entry_date"] < WALK_FORWARD_CUTOFF]
        train_summary = summarize(train_observations) if train_observations else {"observation_count": 0}
        pass_flag, criteria = passes(summary, event_count, stock_count, args.min_obs, train_summary)
        if pass_flag:
            promoted_pairs.append(pair_key)
        rule = rules.get(pair_key)
        conn.execute(
            """
            INSERT INTO macro_signal_backtest_results
            (run_id, indicator_key, indicator_name, sector_name, direction_mode,
             event_count, observation_count, stock_count,
             avg_ret_20d, median_ret_20d, hit_rate_20d,
             avg_ret_60d, median_ret_60d, hit_rate_60d,
             avg_ret_120d, median_ret_120d, hit_rate_120d,
             avg_mdd_60d, profit_factor_60d, pass_flag, promotion_status,
             criteria_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                pair_key[0],
                observations[0].get("indicator_name") or pair_key[0],
                pair_key[1],
                rule["direction_mode"] if rule else None,
                event_count,
                summary["observation_count"],
                stock_count,
                summary["avg_ret_20d"],
                summary["median_ret_20d"],
                summary["hit_rate_20d"],
                summary["avg_ret_60d"],
                summary["median_ret_60d"],
                summary["hit_rate_60d"],
                summary["avg_ret_120d"],
                summary["median_ret_120d"],
                summary["hit_rate_120d"],
                summary["avg_mdd_60d"],
                summary["profit_factor_60d"],
                1 if pass_flag else 0,
                "promoted" if pass_flag and args.promote else "passed_not_promoted" if pass_flag else "failed",
                json.dumps(criteria, ensure_ascii=False),
                ts,
            ),
        )
        result_count += 1

    if args.promote and promoted_pairs:
        for indicator_key, sector_name in promoted_pairs:
            conn.execute(
                """
                UPDATE cafe_stock_indicator_mappings
                SET mapping_status='confirmed_macro_signal',
                    importance_level='macro_backtested',
                    mapping_note='거시/퀀트 후보 중 가격 히스토리 백테스트 기준(학습/검증 분리 포함) 통과',
                    updated_at=?
                WHERE indicator_key=? AND sector_name=? AND mapping_status='candidate_macro_context'
                """,
                (ts, indicator_key, sector_name),
            )

    if args.promote:
        # 2026-07-28 (Claude): 이전 실행에서 confirmed_macro_signal로 승격됐던 페어가 이번
        # 재실행(학습/검증 분리 게이트 적용)에서 더 이상 통과하지 못하면 강등 — 과최적화로
        # 확인된 신호가 화면/다른 로직에 "확정됨"으로 계속 남아있는 것을 방지.
        promoted_set = set(promoted_pairs)
        all_pairs = list(by_pair.keys())
        demoted = 0
        for indicator_key, sector_name in all_pairs:
            if (indicator_key, sector_name) in promoted_set:
                continue
            cur = conn.execute(
                """SELECT COUNT(*) FROM cafe_stock_indicator_mappings
                   WHERE indicator_key=? AND sector_name=? AND mapping_status='confirmed_macro_signal'""",
                (indicator_key, sector_name),
            ).fetchone()[0]
            if cur:
                conn.execute(
                    """
                    UPDATE cafe_stock_indicator_mappings
                    SET mapping_status='candidate_macro_context',
                        importance_level='macro_backtested_rejected',
                        mapping_note='2026-07-28 학습/검증 분리 재검증에서 탈락(단일 레짐 관측 또는 통계 기준 미달) — 강등',
                        updated_at=?
                    WHERE indicator_key=? AND sector_name=? AND mapping_status='confirmed_macro_signal'
                    """,
                    (ts, indicator_key, sector_name),
                )
                demoted += 1

    conn.commit()
    print(
        json.dumps(
            {
                "run_id": run_id,
                "results": result_count,
                "trades": trade_count,
                "passed_pairs": len(promoted_pairs),
                "demoted_pairs": demoted if args.promote else None,
                "promoted": bool(args.promote),
                "db": str(DB_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
