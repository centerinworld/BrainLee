"""
📊 경제 지표 데이터베이스 관리 모듈
=====================================
SQLite 기반, 경제 지표 데이터의 저장/조회/분석 담당
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "economic_indicators.db"


def get_seoul_timezone():
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))


SEOUL = get_seoul_timezone()


def connect() -> sqlite3.Connection:
    """경제 지표 DB 연결"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """스키마 생성 - 테이블이 없으면 생성"""
    conn.executescript(
        """
        -- 경제 지표 마스터 테이블
        CREATE TABLE IF NOT EXISTS economic_indicators (
            indicator_code TEXT PRIMARY KEY,      -- 지표 코드 (예: 'EXCHANGE_USD', 'GDP_GROWTH')
            indicator_name TEXT NOT NULL,           -- 지표명 (예: '원/달러 환율')
            category TEXT NOT NULL,                 -- 분류 (exchange_rate, interest_rate, price, gdp, employment, stock, etc)
            source TEXT NOT NULL DEFAULT '',         -- 데이터 출처 (BOK, KOSIS, KRX, etc)
            unit TEXT NOT NULL DEFAULT '',           -- 단위 (원, %, 천명, etc)
            frequency TEXT NOT NULL DEFAULT 'D',     -- 수집 주기 (D:일, W:주, M:월, Q:분기, Y:년)
            description TEXT DEFAULT '',             -- 지표 설명
            is_active INTEGER NOT NULL DEFAULT 1,   -- 활성 여부
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours'))
        );

        -- 경제 지표 데이터 포인트 (시계열)
        CREATE TABLE IF NOT EXISTS indicator_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_code TEXT NOT NULL REFERENCES economic_indicators(indicator_code),
            date TEXT NOT NULL,                      -- 기준일 (YYYY-MM-DD)
            value REAL NOT NULL,                     -- 값
            previous_value REAL,                     -- 이전 값 (변화율 계산용)
            change_rate REAL,                        -- 변화율 (%)
            source_ref TEXT DEFAULT '',               -- 출처 참조 ID
            raw_json TEXT DEFAULT '',                 -- 원본 JSON 데이터
            fetched_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours')),
            UNIQUE(indicator_code, date)
        );

        -- 알림 임계치 설정
        CREATE TABLE IF NOT EXISTS alert_thresholds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_code TEXT NOT NULL REFERENCES economic_indicators(indicator_code),
            alert_type TEXT NOT NULL DEFAULT 'change_rate',  -- change_rate, absolute_value, compare
            operator TEXT NOT NULL DEFAULT '>',      -- >, <, >=, <=, ==
            threshold_value REAL NOT NULL,            -- 임계값
            message_template TEXT DEFAULT '',          -- 알림 메시지 템플릿
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours'))
        );

        -- 텔레그램 전송 로그
        CREATE TABLE IF NOT EXISTS telegram_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_code TEXT,
            message TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours')),
            status TEXT NOT NULL DEFAULT 'sent'       -- sent, failed
        );

        -- 수집 로그
        CREATE TABLE IF NOT EXISTS fetch_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            indicator_code TEXT,
            items_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'success',   -- success, failed
            error_message TEXT DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT (datetime('now', '+9 hours'))
        );

        CREATE INDEX IF NOT EXISTS idx_indicator_data_code_date 
            ON indicator_data(indicator_code, date DESC);
        CREATE INDEX IF NOT EXISTS idx_indicator_data_date 
            ON indicator_data(date DESC);
        CREATE INDEX IF NOT EXISTS idx_fetch_logs_source 
            ON fetch_logs(source, fetched_at DESC);
        """
    )
    conn.commit()


# ============================================================
# 지표 마스터 관리
# ============================================================

DEFAULT_INDICATORS: List[Dict[str, Any]] = [
    # ---- 환율 ----
    {
        "indicator_code": "EXCHANGE_USD",
        "indicator_name": "원/달러 환율",
        "category": "exchange_rate",
        "source": "BOK",
        "unit": "원",
        "frequency": "D",
        "description": "미국 달러화 대비 원화 환율 (매매기준율)",
    },
    {
        "indicator_code": "EXCHANGE_JPY",
        "indicator_name": "원/100엔 환율",
        "category": "exchange_rate",
        "source": "BOK",
        "unit": "원",
        "frequency": "D",
        "description": "일본 엔화(100엔) 대비 원화 환율",
    },
    {
        "indicator_code": "EXCHANGE_CNY",
        "indicator_name": "원/위안 환율",
        "category": "exchange_rate",
        "source": "BOK",
        "unit": "원",
        "frequency": "D",
        "description": "중국 위안화 대비 원화 환율",
    },
    {
        "indicator_code": "EXCHANGE_EUR",
        "indicator_name": "원/유로 환율",
        "category": "exchange_rate",
        "source": "BOK",
        "unit": "원",
        "frequency": "D",
        "description": "유로화 대비 원화 환율",
    },
    # ---- 금리 ----
    {
        "indicator_code": "BASE_RATE",
        "indicator_name": "한국은행 기준금리",
        "category": "interest_rate",
        "source": "BOK",
        "unit": "%",
        "frequency": "M",
        "description": "한국은행 기준금리 (연%, 종전)"
    },
    {
        "indicator_code": "BOND_YIELD_3Y",
        "indicator_name": "국고채 3년물 금리",
        "category": "interest_rate",
        "source": "BOK",
        "unit": "%",
        "frequency": "D",
        "description": "국고채 3년물 유통수익률"
    },
    {
        "indicator_code": "BOND_YIELD_10Y",
        "indicator_name": "국고채 10년물 금리",
        "category": "interest_rate",
        "source": "BOK",
        "unit": "%",
        "frequency": "D",
        "description": "국고채 10년물 유통수익률 (장기금리)"
    },
    {
        "indicator_code": "CALL_RATE",
        "indicator_name": "콜금리",
        "category": "interest_rate",
        "source": "BOK",
        "unit": "%",
        "frequency": "D",
        "description": "콜금리 (일별)"
    },
    # ---- 물가 ----
    {
        "indicator_code": "CPI_CHANGE",
        "indicator_name": "소비자물가상승률",
        "category": "price",
        "source": "BOK",
        "unit": "%",
        "frequency": "M",
        "description": "소비자물가지수 전년동월비"
    },
    {
        "indicator_code": "CORE_CPI",
        "indicator_name": "근원물가상승률",
        "category": "price",
        "source": "BOK",
        "unit": "%",
        "frequency": "M",
        "description": "농산물·석유류 제외 근원물가 전년동월비"
    },
    # ---- GDP ----
    {
        "indicator_code": "GDP_GROWTH",
        "indicator_name": "GDP 성장률",
        "category": "gdp",
        "source": "BOK",
        "unit": "%",
        "frequency": "Q",
        "description": "실질 GDP 전기대비 성장률 (계절조정)"
    },
    {
        "indicator_code": "GNI_PER_CAPITA",
        "indicator_name": "1인당 GNI",
        "category": "gdp",
        "source": "BOK",
        "unit": "달러",
        "frequency": "Q",
        "description": "1인당 국민총소득"
    },
    # ---- 고용 ----
    {
        "indicator_code": "UNEMPLOYMENT_RATE",
        "indicator_name": "실업률",
        "category": "employment",
        "source": "KOSIS",
        "unit": "%",
        "frequency": "M",
        "description": "전체 실업률 (계절조정)"
    },
    {
        "indicator_code": "EMPLOYMENT_RATE",
        "indicator_name": "고용률",
        "category": "employment",
        "source": "KOSIS",
        "unit": "%",
        "frequency": "M",
        "description": "15세 이상 고용률"
    },
    {
        "indicator_code": "YOUTH_UNEMPLOYMENT",
        "indicator_name": "청년 실업률",
        "category": "employment",
        "source": "KOSIS",
        "unit": "%",
        "frequency": "M",
        "description": "15-29세 청년 실업률"
    },
    # ---- 주식 ----
    {
        "indicator_code": "KOSPI",
        "indicator_name": "코스피 지수",
        "category": "stock",
        "source": "KRX",
        "unit": "P",
        "frequency": "D",
        "description": "코스피 종가지수"
    },
    {
        "indicator_code": "KOSDAQ",
        "indicator_name": "코스닥 지수",
        "category": "stock",
        "source": "KRX",
        "unit": "P",
        "frequency": "D",
        "description": "코스닥 종가지수"
    },
    # ---- 무역 ----
    {
        "indicator_code": "EXPORT_AMOUNT",
        "indicator_name": "월간 수출액",
        "category": "trade",
        "source": "BOK",
        "unit": "백만달러",
        "frequency": "M",
        "description": "월간 총 수출액"
    },
    {
        "indicator_code": "IMPORT_AMOUNT",
        "indicator_name": "월간 수입액",
        "category": "trade",
        "source": "BOK",
        "unit": "백만달러",
        "frequency": "M",
        "description": "월간 총 수입액"
    },
    {
        "indicator_code": "TRADE_BALANCE",
        "indicator_name": "무역수지",
        "category": "trade",
        "source": "BOK",
        "unit": "백만달러",
        "frequency": "M",
        "description": "월간 무역수지 (수출-수입)"
    },
    # ---- 외환보유액 ----
    {
        "indicator_code": "FOREIGN_RESERVE",
        "indicator_name": "외환보유액",
        "category": "external",
        "source": "BOK",
        "unit": "백만달러",
        "frequency": "M",
        "description": "한국은행 외환보유액"
    },
]


def init_default_indicators(conn: sqlite3.Connection) -> None:
    """기본 경제 지표 마스터 데이터 초기화"""
    for ind in DEFAULT_INDICATORS:
        conn.execute(
            """
            INSERT OR IGNORE INTO economic_indicators(
                indicator_code, indicator_name, category, source, unit, 
                frequency, description, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                ind["indicator_code"],
                ind["indicator_name"],
                ind["category"],
                ind["source"],
                ind["unit"],
                ind["frequency"],
                ind["description"],
            ),
        )
    conn.commit()


# ============================================================
# 데이터 저장/조회
# ============================================================


def save_indicator_data(
    conn: sqlite3.Connection,
    indicator_code: str,
    date: str,
    value: float,
    previous_value: Optional[float] = None,
    change_rate: Optional[float] = None,
    source_ref: str = "",
    raw_json: str = "",
) -> int:
    """지표 데이터 저장 (upsert)"""
    now = datetime.now(SEOUL).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO indicator_data(
            indicator_code, date, value, previous_value, change_rate,
            source_ref, raw_json, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(indicator_code, date) DO UPDATE SET
            value = excluded.value,
            previous_value = COALESCE(excluded.previous_value, indicator_data.previous_value),
            change_rate = COALESCE(excluded.change_rate, indicator_data.change_rate),
            source_ref = excluded.source_ref,
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
        """,
        (indicator_code, date, value, previous_value, change_rate, source_ref, raw_json, now),
    )
    conn.commit()
    return conn.total_changes


def get_latest_value(conn: sqlite3.Connection, indicator_code: str) -> Optional[Dict[str, Any]]:
    """최신 지표 데이터 조회"""
    row = conn.execute(
        """
        SELECT id, indicator_code, date, value, previous_value, change_rate, 
               source_ref, fetched_at
        FROM indicator_data
        WHERE indicator_code = ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (indicator_code,),
    ).fetchone()
    return dict(row) if row else None


def get_recent_values(
    conn: sqlite3.Connection, indicator_code: str, limit: int = 30
) -> List[Dict[str, Any]]:
    """최근 N개 데이터 조회"""
    rows = conn.execute(
        """
        SELECT id, indicator_code, date, value, previous_value, change_rate, 
               source_ref, fetched_at
        FROM indicator_data
        WHERE indicator_code = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (indicator_code, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_values_in_range(
    conn: sqlite3.Connection,
    indicator_code: str,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """특정 기간 데이터 조회"""
    rows = conn.execute(
        """
        SELECT id, indicator_code, date, value, previous_value, change_rate, 
               source_ref, fetched_at
        FROM indicator_data
        WHERE indicator_code = ? AND date BETWEEN ? AND ?
        ORDER BY date ASC
        """,
        (indicator_code, start_date, end_date),
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_indicators(
    conn: sqlite3.Connection, category: Optional[str] = None
) -> List[Dict[str, Any]]:
    """전체 지표 목록 조회"""
    if category:
        rows = conn.execute(
            """
            SELECT indicator_code, indicator_name, category, source, unit, 
                   frequency, description, is_active
            FROM economic_indicators
            WHERE is_active = 1 AND category = ?
            ORDER BY category, indicator_code
            """,
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT indicator_code, indicator_name, category, source, unit, 
                   frequency, description, is_active
            FROM economic_indicators
            WHERE is_active = 1
            ORDER BY category, indicator_code
            """,
        ).fetchall()
    return [dict(row) for row in rows]


def get_categories(conn: sqlite3.Connection) -> List[str]:
    """분류 목록 조회"""
    rows = conn.execute(
        """
        SELECT DISTINCT category FROM economic_indicators
        WHERE is_active = 1
        ORDER BY category
        """
    ).fetchall()
    return [row["category"] for row in rows]


def get_latest_values_all(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """모든 지표의 최신값 조회 (서브쿼리 활용)"""
    rows = conn.execute(
        """
        SELECT 
            ei.indicator_code,
            ei.indicator_name,
            ei.category,
            ei.source,
            ei.unit,
            ei.frequency,
            id.date,
            id.value,
            id.previous_value,
            id.change_rate,
            id.fetched_at
        FROM economic_indicators ei
        LEFT JOIN indicator_data id ON id.id = (
            SELECT id FROM indicator_data 
            WHERE indicator_code = ei.indicator_code 
            ORDER BY date DESC LIMIT 1
        )
        WHERE ei.is_active = 1
        ORDER BY ei.category, ei.indicator_code
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_recent_significant_changes(
    conn: sqlite3.Connection, days: int = 7, min_change_rate: float = 0.5
) -> List[Dict[str, Any]]:
    """최근 유의미한 변화가 있는 지표 조회"""
    cutoff = (datetime.now(SEOUL) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT 
            ei.indicator_code,
            ei.indicator_name,
            ei.category,
            ei.unit,
            id.date,
            id.value,
            id.previous_value,
            id.change_rate,
            id.fetched_at
        FROM indicator_data id
        JOIN economic_indicators ei ON ei.indicator_code = id.indicator_code
        WHERE id.date >= ?
          AND ei.is_active = 1
          AND (ABS(id.change_rate) >= ? OR id.change_rate IS NULL)
        ORDER BY ABS(COALESCE(id.change_rate, 0)) DESC
        """,
        (cutoff, min_change_rate),
    ).fetchall()
    return [dict(row) for row in rows]


# ============================================================
# 알림 임계치 관리
# ============================================================


DEFAULT_THRESHOLDS: List[Dict[str, Any]] = [
    {
        "indicator_code": "EXCHANGE_USD",
        "alert_type": "change_rate",
        "operator": ">",
        "threshold_value": 1.0,
        "message_template": "⚠️ 원/달러 환율이 전일대비 {change_rate:.1f}% 상승한 {value:.0f}원입니다.",
    },
    {
        "indicator_code": "EXCHANGE_USD",
        "alert_type": "absolute_value",
        "operator": ">",
        "threshold_value": 1400.0,
        "message_template": "🚨 원/달러 환율이 {value:.0f}원을 돌파했습니다! (심리적 저항선)",
    },
    {
        "indicator_code": "EXCHANGE_USD",
        "alert_type": "absolute_value",
        "operator": "<",
        "threshold_value": 1300.0,
        "message_template": "✅ 원/달러 환율이 {value:.0f}원으로 안정세입니다.",
    },
    {
        "indicator_code": "BASE_RATE",
        "alert_type": "absolute_value",
        "operator": ">",
        "threshold_value": 3.5,
        "message_template": "🏦 기준금리가 {value:.1f}%입니다. (고금리 지속)",
    },
    {
        "indicator_code": "BOND_YIELD_10Y",
        "alert_type": "change_rate",
        "operator": ">",
        "threshold_value": 5.0,
        "message_template": "📈 10년물 국고채 금리가 전일대비 {change_rate:.1f}% 변동한 {value:.2f}%입니다.",
    },
    {
        "indicator_code": "CPI_CHANGE",
        "alert_type": "absolute_value",
        "operator": ">",
        "threshold_value": 3.0,
        "message_template": "🔥 소비자물가 상승률이 {value:.1f}%입니다. (3% 초과)",
    },
    {
        "indicator_code": "KOSPI",
        "alert_type": "change_rate",
        "operator": ">",
        "threshold_value": 2.0,
        "message_template": "📊 코스피가 전일대비 {change_rate:.1f}% 변동한 {value:.0f}P입니다.",
    },
    {
        "indicator_code": "TRADE_BALANCE",
        "alert_type": "absolute_value",
        "operator": "<",
        "threshold_value": 0,
        "message_template": "⚠️ 무역수지가 적자입니다: {value:.0f}백만달러",
    },
    {
        "indicator_code": "UNEMPLOYMENT_RATE",
        "alert_type": "absolute_value",
        "operator": ">",
        "threshold_value": 4.0,
        "message_template": "📋 실업률이 {value:.1f}%입니다. (4% 초과)",
    },
]


def init_default_thresholds(conn: sqlite3.Connection) -> None:
    """기본 알림 임계치 초기화"""
    for th in DEFAULT_THRESHOLDS:
        conn.execute(
            """
            INSERT OR IGNORE INTO alert_thresholds(
                indicator_code, alert_type, operator, threshold_value, message_template, is_active
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                th["indicator_code"],
                th["alert_type"],
                th["operator"],
                th["threshold_value"],
                th["message_template"],
            ),
        )
    conn.commit()


def check_thresholds(
    conn: sqlite3.Connection, indicator_code: str, value: float, change_rate: Optional[float]
) -> List[Dict[str, Any]]:
    """임계치 조건 확인하여 트리거된 알림 반환"""
    thresholds = conn.execute(
        """
        SELECT id, indicator_code, alert_type, operator, threshold_value, message_template
        FROM alert_thresholds
        WHERE indicator_code = ? AND is_active = 1
        """,
        (indicator_code,),
    ).fetchall()

    triggered: List[Dict[str, Any]] = []
    ind_info = conn.execute(
        """
        SELECT indicator_name, unit FROM economic_indicators
        WHERE indicator_code = ?
        """,
        (indicator_code,),
    ).fetchone()

    for th in thresholds:
        triggered_flag = False
        if th["alert_type"] == "change_rate" and change_rate is not None:
            if th["operator"] == ">" and change_rate > th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == "<" and change_rate < th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == ">=" and change_rate >= th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == "<=" and change_rate <= th["threshold_value"]:
                triggered_flag = True
        elif th["alert_type"] == "absolute_value":
            if th["operator"] == ">" and value > th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == "<" and value < th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == ">=" and value >= th["threshold_value"]:
                triggered_flag = True
            elif th["operator"] == "<=" and value <= th["threshold_value"]:
                triggered_flag = True

        if triggered_flag:
            message = th["message_template"]
            message = message.replace("{value}", f"{value:.2f}")
            if change_rate is not None:
                message = message.replace("{change_rate}", f"{change_rate:.2f}")
            if ind_info:
                message = message.replace("{indicator_name}", ind_info["indicator_name"])
                message = message.replace("{unit}", ind_info["unit"])

            triggered.append({
                "threshold_id": th["id"],
                "indicator_code": indicator_code,
                "message": message,
            })

    return triggered


# ============================================================
# 로그 기록
# ============================================================


def log_telegram(conn: sqlite3.Connection, indicator_code: str, message: str, status: str = "sent") -> None:
    """텔레그램 전송 로그 기록"""
    conn.execute(
        """
        INSERT INTO telegram_logs(indicator_code, message, status)
        VALUES (?, ?, ?)
        """,
        (indicator_code, message, status),
    )
    conn.commit()


def log_fetch(
    conn: sqlite3.Connection,
    source: str,
    status: str = "success",
    indicator_code: str = "",
    items_count: int = 0,
    error_message: str = "",
) -> None:
    """데이터 수집 로그 기록"""
    conn.execute(
        """
        INSERT INTO fetch_logs(source, indicator_code, items_count, status, error_message)
        VALUES (?, ?, ?, ?, ?)
        """,
        (source, indicator_code, items_count, status, error_message),
    )
    conn.commit()


# ============================================================
# 보고서 생성
# ============================================================


def generate_market_summary(conn: sqlite3.Connection) -> str:
    """시장 요약 보고서 생성 (텔레그램 전송용)"""
    lines: List[str] = []
    lines.append("📊 <b>대한민국 주요 경제지표 요약</b>")
    lines.append(f"🕐 {datetime.now(SEOUL).strftime('%Y-%m-%d %H:%M')} 기준")
    lines.append("")

    values = get_latest_values_all(conn)
    
    categories_order = [
        ("exchange_rate", "💱 환율"),
        ("interest_rate", "🏦 금리"),
        ("price", "💰 물가"),
        ("stock", "📈 주식"),
        ("employment", "👔 고용"),
        ("trade", "🚢 무역"),
        ("gdp", "📈 국민소득"),
        ("external", "🌍 대외"),
    ]

    for cat_key, cat_title in categories_order:
        cat_values = [v for v in values if v["category"] == cat_key]
        if not cat_values:
            continue
        lines.append(f"\n<u>{cat_title}</u>")
        for v in cat_values:
            name = v["indicator_name"]
            val = v["value"]
            unit = v["unit"]
            chg = v["change_rate"]

            if val is None:
                continue

            # 값 포맷팅
            if unit == "%":
                val_str = f"{val:.2f}%"
            elif unit in ("P",):
                val_str = f"{val:.0f}P"
            elif unit in ("원",):
                val_str = f"{val:.0f}원"
            elif unit in ("백만달러", "달러"):
                val_str = f"{val:,.0f}{unit}"
            else:
                val_str = f"{val:.2f}{unit}"

            # 변화 표시
            change_str = ""
            if chg is not None:
                arrow = "▲" if chg > 0 else "▼" if chg < 0 else "→"
                change_str = f" ({arrow} {abs(chg):.2f}%)"

            lines.append(f"• {name}: <b>{val_str}</b>{change_str}")

    lines.append("")
    lines.append("💡 <i>변동사항이 있으면 개별 알림을 보내드립니다.</i>")

    return "\n".join(lines)


def generate_change_report(conn: sqlite3.Connection, days: int = 1) -> Optional[str]:
    """최근 변동 사항 보고서 생성"""
    values = get_recent_significant_changes(conn, days=days, min_change_rate=0.3)
    
    if not values:
        return None

    lines: List[str] = []
    lines.append("🔄 <b>주요 경제지표 변동사항</b>")
    lines.append(f"📆 최근 {days}일간 변동")
    lines.append("")

    for v in values:
        name = v["indicator_name"]
        val = v["value"]
        unit = v["unit"]
        chg = v["change_rate"]
        date = v["date"]

        if unit == "%":
            val_str = f"{val:.2f}%"
        elif unit in ("P",):
            val_str = f"{val:.0f}P"
        elif unit in ("원",):
            val_str = f"{val:.0f}원"
        elif unit in ("백만달러", "달러"):
            val_str = f"{val:,.0f}{unit}"
        else:
            val_str = f"{val:.2f}{unit}"

        if chg is not None:
            arrow = "🔺" if chg > 0 else "🔻" if chg < 0 else "➖"
            lines.append(f"{arrow} <b>{name}</b>: {val_str} ({chg:+.2f}%)")
        else:
            lines.append(f"• <b>{name}</b>: {val_str} (신규)")

    return "\n".join(lines)


# ============================================================
# DB 초기화
# ============================================================


def initialize_database() -> None:
    """데이터베이스 초기화 (스크립트 실행용)"""
    conn = connect()
    init_default_indicators(conn)
    init_default_thresholds(conn)
    
    # 이미 저장된 데이터 확인
    count = conn.execute("SELECT COUNT(*) FROM economic_indicators").fetchone()[0]
    print(f"✅ 경제지표 DB 초기화 완료: {count}개 지표 등록")
    
    conn.close()


if __name__ == "__main__":
    initialize_database()
