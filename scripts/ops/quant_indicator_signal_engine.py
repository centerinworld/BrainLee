#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
from datetime import datetime
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"


def now_kst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pct_change(curr: float | None, prev: float | None) -> float | None:
    if curr is None or prev is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def mean_std(vals: list[float]) -> tuple[float | None, float | None]:
    clean = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if len(clean) < 6:
        return None, None
    avg = sum(clean) / len(clean)
    var = sum((v - avg) ** 2 for v in clean) / max(1, len(clean) - 1)
    return avg, math.sqrt(var)


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quant_indicator_signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            series_name TEXT NOT NULL,
            period TEXT NOT NULL,
            value REAL,
            prev_value REAL,
            mom_pct REAL,
            yoy_pct REAL,
            z_score REAL,
            signal_type TEXT NOT NULL,
            signal_strength REAL DEFAULT 0,
            related_stocks TEXT,
            message TEXT,
            generated_at TEXT NOT NULL,
            telegram_sent INTEGER DEFAULT 0,
            UNIQUE(indicator_key, series_name, period, signal_type)
        );
        CREATE INDEX IF NOT EXISTS idx_qise_generated
            ON quant_indicator_signal_events(generated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_qise_indicator
            ON quant_indicator_signal_events(indicator_key, period DESC);
        """
    )
    conn.commit()


def period_yoy_key(period: str) -> str | None:
    if not period or len(period) < 4:
        return None
    try:
        year = int(period[:4]) - 1
    except ValueError:
        return None
    return f"{year}{period[4:]}"


def is_consecutive_period(previous: str | None, current: str | None) -> bool:
    if not previous or not current:
        return False
    if len(previous) == len(current) == 7:
        try:
            py, pm = map(int, previous.split("-"))
            cy, cm = map(int, current.split("-"))
            return cy * 12 + cm == py * 12 + pm + 1
        except ValueError:
            return False
    if len(previous) == len(current) == 10:
        try:
            gap = (datetime.strptime(current, "%Y-%m-%d") - datetime.strptime(previous, "%Y-%m-%d")).days
            return 1 <= gap <= 7
        except ValueError:
            return False
    if len(previous) == len(current) == 4 and previous.isdigit() and current.isdigit():
        return int(current) == int(previous) + 1
    return True


def load_indicator_rows(conn: sqlite3.Connection, indicator_key: str, series_name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT period, value, unit, source_name, source_detail, quality
        FROM quant_major_indicator_series
        WHERE indicator_key=? AND series_name=? AND value IS NOT NULL
        ORDER BY period
        """,
        (indicator_key, series_name),
    ).fetchall()


def related_stocks(conn: sqlite3.Connection, indicator_key: str, limit: int = 8) -> list[dict]:
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, sector_name, mention_count, confidence,
               evidence_terms, example_posts,
               revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
               exposure_basis, importance_level
               , mapping_status
        FROM cafe_stock_indicator_mappings
        WHERE indicator_key=?
          AND COALESCE(importance_level,'unknown') <> 'low'
        ORDER BY
          CASE importance_level
            WHEN 'high' THEN 0
            WHEN 'medium' THEN 1
            WHEN 'unknown_core_candidate' THEN 2
            WHEN 'unknown_cost_sensitive' THEN 3
            WHEN 'unknown' THEN 4
            ELSE 4
          END,
          mention_count DESC,
          COALESCE(revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct, 0) DESC,
          confidence DESC
        LIMIT ?
        """,
        (indicator_key, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence_terms"] = json.loads(d.get("evidence_terms") or "[]")
        except Exception:
            d["evidence_terms"] = []
        try:
            d["example_posts"] = json.loads(d.get("example_posts") or "[]")
        except Exception:
            d["example_posts"] = []
        out.append(d)
    return out


def classify_signal(value: float, prev_value: float | None, yoy_value: float | None, history: list[float]) -> tuple[str | None, float, float | None, float | None, float | None]:
    mom = pct_change(value, prev_value)
    yoy = pct_change(value, yoy_value)
    avg, std = mean_std(history[-36:])
    z = (value - avg) / std if avg is not None and std and std > 0 else None

    positive_score = 0.0
    negative_score = 0.0
    if z is not None:
        if z >= 2.0:
            positive_score += min(4.0, z)
        elif z <= -2.0:
            negative_score += min(4.0, abs(z))
    if mom is not None:
        if mom >= 30:
            positive_score += min(3.0, mom / 30)
        elif mom <= -30:
            negative_score += min(3.0, abs(mom) / 30)
    if yoy is not None:
        if yoy >= 30:
            positive_score += min(3.0, yoy / 35)
        elif yoy <= -30:
            negative_score += min(3.0, abs(yoy) / 35)

    if positive_score >= max(2.0, negative_score + 0.5):
        return "spike_up", round(positive_score, 3), mom, yoy, z
    if negative_score >= max(2.0, positive_score + 0.5):
        return "spike_down", round(negative_score, 3), mom, yoy, z
    return None, max(positive_score, negative_score), mom, yoy, z


def series_direction_mode(series_name: str) -> tuple[str, str]:
    text = series_name or ""
    higher_bad_terms = ("수입단가", "원재료", "원가", "비용", "금리", "재고")
    ambiguous_terms = ("수입액", "수입중량", "수입금액")
    higher_good_terms = ("수출", "판매", "매출", "무역수지", "출하", "단가")

    if any(term in text for term in higher_bad_terms):
        return "higher_is_bad", "수입단가/원가성 지표는 상승 시 비용 부담으로 해석합니다."
    if any(term in text for term in ambiguous_terms):
        return "ambiguous", "수입액 증가는 수요 증가와 비용 증가가 섞일 수 있어 주의 신호로 처리합니다."
    if any(term in text for term in higher_good_terms):
        return "higher_is_good", "수출/판매/무역수지 계열 지표는 상승을 우호적으로 해석합니다."
    return "higher_is_good", "기본값은 지표 상승을 우호적으로 보되 종목별 노출도 확인이 필요합니다."


def classify_traffic_light(
    value: float,
    prev_value: float | None,
    yoy_value: float | None,
    history: list[float],
    series_name: str,
) -> dict:
    signal_type, strength, mom, yoy, z = classify_signal(value, prev_value, yoy_value, history)
    direction_mode, direction_note = series_direction_mode(series_name)

    max_change = max(
        abs(v)
        for v in [
            mom if mom is not None else 0.0,
            yoy if yoy is not None else 0.0,
            (z * 15.0) if z is not None else 0.0,
        ]
    )

    if signal_type == "spike_up":
        if direction_mode == "higher_is_good":
            light, label, score = "green", "좋음", strength
            reason = "최신 발표값이 과거 대비 강하게 상승했습니다."
        elif direction_mode == "higher_is_bad":
            light, label, score = "red", "나쁨", strength
            reason = "비용성 지표가 강하게 상승했습니다."
        else:
            light, label, score = "yellow", "주의", strength
            reason = "지표가 강하게 상승했지만 수요/비용 해석이 섞일 수 있습니다."
    elif signal_type == "spike_down":
        if direction_mode == "higher_is_good":
            light, label, score = "red", "나쁨", strength
            reason = "성장성 지표가 과거 대비 강하게 하락했습니다."
        elif direction_mode == "higher_is_bad":
            light, label, score = "green", "좋음", strength
            reason = "비용성 지표가 강하게 하락했습니다."
        else:
            light, label, score = "yellow", "주의", strength
            reason = "지표가 강하게 하락했지만 수요/비용 해석이 섞일 수 있습니다."
    elif max_change >= 12:
        light, label, score = "yellow", "주의", round(max_change / 30.0, 3)
        reason = "방향성은 약하지만 변동폭이 커서 확인이 필요합니다."
    else:
        light, label, score = "gray", "중립", 0.0
        reason = "최신 발표값에서 의미 있는 급변은 감지되지 않았습니다."

    return {
        "traffic_light": light,
        "signal_label": label,
        "signal_type": signal_type,
        "signal_strength": round(float(score or 0.0), 3),
        "direction_mode": direction_mode,
        "direction_note": direction_note,
        "reason": reason,
        "mom_pct": mom,
        "yoy_pct": yoy,
        "z_score": z,
    }


def build_message(event: dict, stocks: list[dict]) -> str:
    arrow = "급등" if event["signal_type"] == "spike_up" else "급락"
    lines = [
        f"<b>퀀트 지표 {event.get('signal_label', '검토')} 신호</b>",
        f"<b>{html.escape(event['indicator_name'] or event['indicator_key'])}</b>",
        f"{html.escape(event['series_name'])} {arrow} / 기간 {event['period']} / 값 {event['value']:,.2f}",
    ]
    stats = []
    if event.get("mom_pct") is not None:
        stats.append(f"MoM {event['mom_pct']:+.1f}%")
    if event.get("yoy_pct") is not None:
        stats.append(f"YoY {event['yoy_pct']:+.1f}%")
    if event.get("z_score") is not None:
        stats.append(f"z {event['z_score']:+.2f}")
    if stats:
        lines.append(" · ".join(stats))
    if stocks:
        lines.append("")
        lines.append("<b>관련 종목 후보</b>")
        for s in stocks[:5]:
            terms = ", ".join(t.get("term", "") for t in s.get("evidence_terms", [])[:4] if t.get("term"))
            exposures = []
            for label, key in [("매출", "revenue_exposure_pct"), ("이익", "profit_exposure_pct"), ("원가", "cost_exposure_pct")]:
                if s.get(key) is not None:
                    exposures.append(f"{label} {float(s[key]):.1f}%")
            exposure_txt = " / ".join(exposures) if exposures else "비중 미공시"
            suffix = f" ({html.escape(terms)})" if terms else ""
            lines.append(
                f"- {html.escape(s['stock_name'])}({s['stock_code']}) "
                f"{html.escape(exposure_txt)} · 관련도 {s.get('mention_count', 0)}회{suffix}"
            )
    lines.append("")
    if stocks:
        lines.append("확인된 노출 비중 + 실제 지표 시계열 이상치 기반")
    else:
        lines.append("매크로/시장 레짐 지표 시계열 이상치 기반. 종목 매수 후보는 별도 노출도 매핑 확인 필요")
    return "\n".join(lines)


def run(limit_events: int = 30, send_telegram: bool = False) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_tables(conn)
    ts = now_kst()

    pairs = conn.execute(
        """
        SELECT DISTINCT s.indicator_key, s.series_name, c.epic_indicator_name
        FROM quant_major_indicator_series s
        LEFT JOIN quant_major_indicator_catalog c ON c.indicator_key=s.indicator_key
        WHERE s.value IS NOT NULL
          AND (
                s.indicator_key LIKE 'macro:%'
             OR EXISTS (
                    SELECT 1
                    FROM cafe_stock_indicator_mappings m
                    WHERE m.indicator_key=s.indicator_key
                )
          )
        ORDER BY s.indicator_key, s.series_name
        """
    ).fetchall()

    candidates = []
    for p in pairs:
        rows = load_indicator_rows(conn, p["indicator_key"], p["series_name"])
        if len(rows) < 8:
            continue
        latest = rows[-1]
        prev_candidate = rows[-2] if len(rows) >= 2 else None
        prev = prev_candidate if prev_candidate and is_consecutive_period(prev_candidate["period"], latest["period"]) else None
        yoy_key = period_yoy_key(latest["period"])
        yoy_row = next((r for r in reversed(rows[:-1]) if r["period"] == yoy_key), None) if yoy_key else None
        history = [float(r["value"]) for r in rows[:-1] if r["value"] is not None]
        signal_type, strength, mom, yoy, z = classify_signal(
            float(latest["value"]),
            float(prev["value"]) if prev and prev["value"] is not None else None,
            float(yoy_row["value"]) if yoy_row and yoy_row["value"] is not None else None,
            history,
        )
        if not signal_type:
            continue
        traffic = classify_traffic_light(
            float(latest["value"]),
            float(prev["value"]) if prev and prev["value"] is not None else None,
            float(yoy_row["value"]) if yoy_row and yoy_row["value"] is not None else None,
            history,
            p["series_name"],
        )
        stocks = related_stocks(conn, p["indicator_key"])
        allow_without_stocks = str(p["indicator_key"] or "").startswith("macro:")
        if not stocks and not allow_without_stocks:
            continue
        event = {
            "indicator_key": p["indicator_key"],
            "indicator_name": p["epic_indicator_name"] or p["indicator_key"],
            "series_name": p["series_name"],
            "period": latest["period"],
            "value": float(latest["value"]),
            "prev_value": float(prev["value"]) if prev and prev["value"] is not None else None,
            "mom_pct": mom,
            "yoy_pct": yoy,
            "z_score": z,
            "signal_type": signal_type,
            "signal_strength": strength,
            "related_stocks": stocks,
            "quality": latest["quality"],
            **traffic,
        }
        event["message"] = build_message(event, stocks)
        candidates.append(event)

    candidates.sort(key=lambda x: x["signal_strength"], reverse=True)
    selected = candidates[:limit_events]
    inserted = 0
    sent = 0
    for e in selected:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO quant_indicator_signal_events
            (indicator_key, indicator_name, series_name, period, value, prev_value,
             mom_pct, yoy_pct, z_score, signal_type, signal_strength, related_stocks,
             message, generated_at, telegram_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                e["indicator_key"],
                e["indicator_name"],
                e["series_name"],
                e["period"],
                e["value"],
                e["prev_value"],
                e["mom_pct"],
                e["yoy_pct"],
                e["z_score"],
                e["signal_type"],
                e["signal_strength"],
                json.dumps(e["related_stocks"], ensure_ascii=False),
                e["message"],
                ts,
            ),
        )
        if cur.rowcount:
            inserted += 1
            verified_stocks = [
                stock for stock in e["related_stocks"]
                if stock.get("importance_level") in {"high", "medium"}
                and (stock.get("revenue_exposure_pct") is not None or stock.get("profit_exposure_pct") is not None)
            ]
            telegram_eligible = e.get("traffic_light") == "green" and bool(verified_stocks)
            if send_telegram and telegram_eligible:
                try:
                    from notifier import load_history, send

                    load_history()
                    key = f"quant_indicator_{e['indicator_key']}_{e['series_name']}_{e['period']}_{e['signal_type']}"
                    verified_event = {**e, "related_stocks": verified_stocks}
                    verified_message = build_message(verified_event, verified_stocks)
                    if send(verified_message, key=key):
                        sent += 1
                        conn.execute(
                            """
                            UPDATE quant_indicator_signal_events
                            SET telegram_sent=1
                            WHERE indicator_key=? AND series_name=? AND period=? AND signal_type=?
                            """,
                            (e["indicator_key"], e["series_name"], e["period"], e["signal_type"]),
                        )
                except Exception as exc:
                    print(f"[WARN] telegram send failed {e['indicator_key']}: {exc}")

    conn.commit()
    conn.close()
    return {"checked_pairs": len(pairs), "signals": len(candidates), "inserted": inserted, "telegram_sent": sent}


def main() -> None:
    ap = argparse.ArgumentParser(description="Quant indicator anomaly → stock candidate signal engine")
    ap.add_argument("--limit-events", type=int, default=30)
    ap.add_argument("--send-telegram", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(limit_events=args.limit_events, send_telegram=args.send_telegram), ensure_ascii=False))


if __name__ == "__main__":
    main()
