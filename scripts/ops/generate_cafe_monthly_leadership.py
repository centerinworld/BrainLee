#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
HS_DB_PATH = ROOT / "hs_trade_lab" / "data" / "hs_trade_lab.db"
QMI_PATH = ROOT / "scripts" / "ops" / "sync_quant_major_indicators.py"


def now_kst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_customs_specs() -> list[tuple]:
    spec = importlib.util.spec_from_file_location("qmi_specs", QMI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.CUSTOMS_SECTOR_QUANT_SPECS)


def init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cafe_monthly_sector_leadership (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            indicator_key TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            export_value_musd REAL,
            export_yoy_pct REAL,
            export_mom_pct REAL,
            unit_price_yoy_pct REAL,
            trade_balance_musd REAL,
            momentum_score REAL,
            rank_no INTEGER,
            source_detail TEXT,
            generated_at TEXT NOT NULL,
            UNIQUE(period, indicator_key)
        );

        CREATE TABLE IF NOT EXISTS cafe_monthly_hs_leadership (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            hs_code TEXT NOT NULL,
            hs_name TEXT,
            sector_name TEXT,
            export_value_usd REAL,
            export_yoy_pct REAL,
            export_mom_pct REAL,
            export_weight_kg REAL,
            export_unit_price REAL,
            unit_price_yoy_pct REAL,
            related_companies TEXT,
            matched_indicator_key TEXT,
            momentum_score REAL,
            rank_no INTEGER,
            generated_at TEXT NOT NULL,
            UNIQUE(period, hs_code)
        );

        CREATE TABLE IF NOT EXISTS cafe_monthly_generated_reports (
            period TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            sector_count INTEGER DEFAULT 0,
            hs_count INTEGER DEFAULT 0,
            generated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def pct(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or abs(base) < 1e-9:
        return None
    return (cur - base) / abs(base) * 100.0


def score_value(export_value: float | None, yoy: float | None, mom: float | None, unit_yoy: float | None) -> float:
    value_term = math.log10(max(float(export_value or 0), 0) + 1) * 4.0
    yoy_term = max(min(float(yoy or 0), 300), -100) * 0.45
    mom_term = max(min(float(mom or 0), 200), -100) * 0.18
    unit_term = max(min(float(unit_yoy or 0), 200), -100) * 0.12
    return round(value_term + yoy_term + mom_term + unit_term, 3)


def series_value(conn: sqlite3.Connection, indicator_key: str, period: str, suffix: str) -> float | None:
    row = conn.execute(
        """
        SELECT value FROM quant_major_indicator_series
        WHERE indicator_key=? AND period=? AND series_name LIKE ?
        ORDER BY series_name
        LIMIT 1
        """,
        (indicator_key, period, f"%_{suffix}"),
    ).fetchone()
    return float(row["value"]) if row and row["value"] is not None else None


def previous_month(period: str) -> str:
    y, m = map(int, period.split("-"))
    if m == 1:
        return f"{y-1}-12"
    return f"{y}-{m-1:02d}"


def previous_year(period: str) -> str:
    y, m = map(int, period.split("-"))
    return f"{y-1}-{m:02d}"


def related_companies(hs_conn: sqlite3.Connection, hs_code: str, limit: int = 6) -> list[dict]:
    rows = hs_conn.execute(
        """
        SELECT stock_code, stock_name, sector_name, confidence, mapping_status
        FROM hs_code_company_map
        WHERE ? LIKE hs_code || '%' OR hs_code LIKE ? || '%'
        ORDER BY confidence DESC, stock_name
        LIMIT ?
        """,
        (hs_code, hs_code, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def sector_for_hs(specs: list[tuple], hs_code: str) -> tuple[str, str]:
    best = ("", "")
    best_len = -1
    for indicator_key, _sub, name, label, prefixes, _notes in specs:
        for prefix in prefixes:
            if hs_code.startswith(prefix) and len(prefix) > best_len:
                best = (indicator_key, name)
                best_len = len(prefix)
    return best


def generate_sector_leaders(conn: sqlite3.Connection, specs: list[tuple], period: str) -> list[dict]:
    prev_m = previous_month(period)
    prev_y = previous_year(period)
    rows: list[dict] = []
    for indicator_key, _sub, name, label, _prefixes, notes in specs:
        export_cur = series_value(conn, indicator_key, period, "수출액")
        if export_cur is None or export_cur <= 0:
            continue
        export_prev_m = series_value(conn, indicator_key, prev_m, "수출액")
        export_prev_y = series_value(conn, indicator_key, prev_y, "수출액")
        unit_cur = series_value(conn, indicator_key, period, "수출단가")
        unit_prev_y = series_value(conn, indicator_key, prev_y, "수출단가")
        balance = series_value(conn, indicator_key, period, "무역수지")
        yoy = pct(export_cur, export_prev_y)
        mom = pct(export_cur, export_prev_m)
        unit_yoy = pct(unit_cur, unit_prev_y)
        score = score_value(export_cur, yoy, mom, unit_yoy)
        rows.append(
            {
                "period": period,
                "indicator_key": indicator_key,
                "sector_name": name.replace(" 수출입", ""),
                "export_value_musd": round(export_cur, 3),
                "export_yoy_pct": round(yoy, 2) if yoy is not None else None,
                "export_mom_pct": round(mom, 2) if mom is not None else None,
                "unit_price_yoy_pct": round(unit_yoy, 2) if unit_yoy is not None else None,
                "trade_balance_musd": round(balance, 3) if balance is not None else None,
                "momentum_score": score,
                "source_detail": notes,
            }
        )
    rows.sort(key=lambda r: (r["momentum_score"], r["export_value_musd"]), reverse=True)
    return rows[:20]


def generate_hs_leaders(hs_conn: sqlite3.Connection, specs: list[tuple], period: str) -> list[dict]:
    prev_m = previous_month(period)
    prev_y = previous_year(period)
    cur_rows = hs_conn.execute(
        """
        SELECT hs_code, MAX(hs_name) AS hs_name,
               SUM(export_value) AS export_value,
               SUM(export_weight) AS export_weight
        FROM customs_monthly_record
        WHERE period_ym=? AND endpoint='itemtrade'
        GROUP BY hs_code
        HAVING export_value >= 1000000
        """,
        (period,),
    ).fetchall()
    prev_m_map = {
        r["hs_code"]: r
        for r in hs_conn.execute(
            """
            SELECT hs_code, SUM(export_value) AS export_value, SUM(export_weight) AS export_weight
            FROM customs_monthly_record
            WHERE period_ym=? AND endpoint='itemtrade'
            GROUP BY hs_code
            """,
            (prev_m,),
        ).fetchall()
    }
    prev_y_map = {
        r["hs_code"]: r
        for r in hs_conn.execute(
            """
            SELECT hs_code, SUM(export_value) AS export_value, SUM(export_weight) AS export_weight
            FROM customs_monthly_record
            WHERE period_ym=? AND endpoint='itemtrade'
            GROUP BY hs_code
            """,
            (prev_y,),
        ).fetchall()
    }

    rows: list[dict] = []
    for r in cur_rows:
        hs_code = r["hs_code"]
        export_value = float(r["export_value"] or 0)
        export_weight = float(r["export_weight"] or 0)
        pm = prev_m_map.get(hs_code)
        py = prev_y_map.get(hs_code)
        yoy = pct(export_value, float(py["export_value"])) if py else None
        mom = pct(export_value, float(pm["export_value"])) if pm else None
        unit = export_value / export_weight if export_weight else None
        py_weight = float(py["export_weight"] or 0) if py else 0
        py_unit = float(py["export_value"] or 0) / py_weight if py and py_weight else None
        unit_yoy = pct(unit, py_unit)
        if yoy is None or yoy < 20:
            continue
        indicator_key, sector_name = sector_for_hs(specs, hs_code)
        companies = related_companies(hs_conn, hs_code)
        score = score_value(export_value / 1_000_000.0, yoy, mom, unit_yoy)
        rows.append(
            {
                "period": period,
                "hs_code": hs_code,
                "hs_name": r["hs_name"],
                "sector_name": sector_name.replace(" 수출입", "") if sector_name else "",
                "export_value_usd": round(export_value, 0),
                "export_yoy_pct": round(yoy, 2) if yoy is not None else None,
                "export_mom_pct": round(mom, 2) if mom is not None else None,
                "export_weight_kg": round(export_weight, 0),
                "export_unit_price": round(unit, 4) if unit is not None else None,
                "unit_price_yoy_pct": round(unit_yoy, 2) if unit_yoy is not None else None,
                "related_companies": companies,
                "matched_indicator_key": indicator_key,
                "momentum_score": score,
            }
        )
    rows.sort(key=lambda r: (r["momentum_score"], r["export_value_usd"]), reverse=True)
    return rows[:50]


def report_text(period: str, sectors: list[dict], hs_rows: list[dict]) -> tuple[str, str]:
    title = f"{period} 월별 주도 섹터/HS 코드 브리핑"
    top_sectors = sectors[:5]
    top_hs = hs_rows[:8]
    lines = [f"# {title}", ""]
    if top_sectors:
        first = top_sectors[0]
        lines.append(
            f"{period} 기준 주도 섹터는 {first['sector_name']}입니다. "
            f"수출액은 {first['export_value_musd']:,.1f}백만달러이고, "
            f"전년동월 대비 {first['export_yoy_pct']:+.1f}% 움직였습니다."
        )
        lines.append("")
        lines.append("## 주도 섹터")
        for i, r in enumerate(top_sectors, 1):
            unit_txt = "" if r.get("unit_price_yoy_pct") is None else f", 단가 YoY {r['unit_price_yoy_pct']:+.1f}%"
            lines.append(
                f"{i}. {r['sector_name']} - 수출 {r['export_value_musd']:,.1f}백만달러, "
                f"YoY {r['export_yoy_pct']:+.1f}%, MoM {r['export_mom_pct']:+.1f}%{unit_txt}"
            )
    if top_hs:
        lines.append("")
        lines.append("## 주도 HS code")
        for i, r in enumerate(top_hs, 1):
            comps = ", ".join(c["stock_name"] for c in r.get("related_companies", [])[:3]) or "매핑 검토 필요"
            lines.append(
                f"{i}. {r['hs_code']} {r['hs_name']} - 수출 {r['export_value_usd']/1_000_000:,.1f}백만달러, "
                f"YoY {r['export_yoy_pct']:+.1f}%, 관련: {comps}"
            )
    lines.append("")
    lines.append("해석 원칙: 과거 지표상회 글에서 반복된 업종 프레임을 기준으로 하되, 현재 월별 관세청 HS 데이터의 수출액·증가율·단가 변화를 우선 점수화했습니다.")
    return title, "\n".join(lines)


def upsert_results(conn: sqlite3.Connection, period: str, sectors: list[dict], hs_rows: list[dict]) -> None:
    ts = now_kst()
    for rank_no, r in enumerate(sectors, 1):
        conn.execute(
            """
            INSERT INTO cafe_monthly_sector_leadership
            (period, indicator_key, sector_name, export_value_musd, export_yoy_pct,
             export_mom_pct, unit_price_yoy_pct, trade_balance_musd, momentum_score,
             rank_no, source_detail, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period, indicator_key) DO UPDATE SET
                sector_name=excluded.sector_name,
                export_value_musd=excluded.export_value_musd,
                export_yoy_pct=excluded.export_yoy_pct,
                export_mom_pct=excluded.export_mom_pct,
                unit_price_yoy_pct=excluded.unit_price_yoy_pct,
                trade_balance_musd=excluded.trade_balance_musd,
                momentum_score=excluded.momentum_score,
                rank_no=excluded.rank_no,
                source_detail=excluded.source_detail,
                generated_at=excluded.generated_at
            """,
            (
                period,
                r["indicator_key"],
                r["sector_name"],
                r["export_value_musd"],
                r["export_yoy_pct"],
                r["export_mom_pct"],
                r["unit_price_yoy_pct"],
                r["trade_balance_musd"],
                r["momentum_score"],
                rank_no,
                r["source_detail"],
                ts,
            ),
        )
    for rank_no, r in enumerate(hs_rows, 1):
        conn.execute(
            """
            INSERT INTO cafe_monthly_hs_leadership
            (period, hs_code, hs_name, sector_name, export_value_usd, export_yoy_pct,
             export_mom_pct, export_weight_kg, export_unit_price, unit_price_yoy_pct,
             related_companies, matched_indicator_key, momentum_score, rank_no, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period, hs_code) DO UPDATE SET
                hs_name=excluded.hs_name,
                sector_name=excluded.sector_name,
                export_value_usd=excluded.export_value_usd,
                export_yoy_pct=excluded.export_yoy_pct,
                export_mom_pct=excluded.export_mom_pct,
                export_weight_kg=excluded.export_weight_kg,
                export_unit_price=excluded.export_unit_price,
                unit_price_yoy_pct=excluded.unit_price_yoy_pct,
                related_companies=excluded.related_companies,
                matched_indicator_key=excluded.matched_indicator_key,
                momentum_score=excluded.momentum_score,
                rank_no=excluded.rank_no,
                generated_at=excluded.generated_at
            """,
            (
                period,
                r["hs_code"],
                r["hs_name"],
                r["sector_name"],
                r["export_value_usd"],
                r["export_yoy_pct"],
                r["export_mom_pct"],
                r["export_weight_kg"],
                r["export_unit_price"],
                r["unit_price_yoy_pct"],
                json.dumps(r["related_companies"], ensure_ascii=False),
                r["matched_indicator_key"],
                r["momentum_score"],
                rank_no,
                ts,
            ),
        )
    title, summary = report_text(period, sectors, hs_rows)
    conn.execute(
        """
        INSERT INTO cafe_monthly_generated_reports
        (period, title, summary_text, sector_count, hs_count, generated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(period) DO UPDATE SET
            title=excluded.title,
            summary_text=excluded.summary_text,
            sector_count=excluded.sector_count,
            hs_count=excluded.hs_count,
            generated_at=excluded.generated_at
        """,
        (period, title, summary, len(sectors), len(hs_rows), ts),
    )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    hs_conn = sqlite3.connect(HS_DB_PATH)
    hs_conn.row_factory = sqlite3.Row
    init_tables(conn)
    specs = load_customs_specs()
    latest = conn.execute(
        "SELECT MAX(period) AS period FROM quant_major_indicator_series WHERE indicator_key LIKE 'public:23:%'"
    ).fetchone()["period"]
    sectors = generate_sector_leaders(conn, specs, latest)
    hs_rows = generate_hs_leaders(hs_conn, specs, latest)
    upsert_results(conn, latest, sectors, hs_rows)
    print(json.dumps({"period": latest, "sectors": len(sectors), "hs_codes": len(hs_rows)}, ensure_ascii=False))
    hs_conn.close()
    conn.close()


if __name__ == "__main__":
    main()
