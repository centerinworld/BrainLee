#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "scratch" / "epic"


@dataclass
class Rule:
    match: str
    status: str
    replacement_family: str
    source_system: str
    collector_path: str
    exactness: str
    priority: str
    notes: str


RULES: list[Rule] = [
    Rule("투자자 예탁금", "ready_existing", "macro_liquidity", "한국은행 ECOS", "routes/market_indicators.py", "exact_or_close", "p1", "현재 ECOS/네이버 폴백 로직 존재. EPIC 대체 가능."),
    Rule("신용공여", "ready_existing", "macro_liquidity", "한국은행 ECOS", "routes/market_indicators.py", "exact_or_close", "p1", "고객예탁금과 동일 엔드포인트군으로 관리."),
    Rule("대차잔고", "ready_existing", "short_balance", "KRX/공공데이터", "public_data_collector.py", "exact_or_close", "p1", "이미 일별/장기 수집 및 UI 사용 중."),
    Rule("글로벌 자동차 판매", "new_collector_needed", "autos_sales", "KAMA/KAIDA/OEM IR", "-", "exact_target", "p1", "EPIC와 동일 레벨의 회사/국가 판매 데이터는 OEM/협회 월간 공시 수집기 필요."),
    Rule("한국 자동차 판매", "new_collector_needed", "autos_sales", "KAMA/KAIDA/OEM IR", "-", "exact_target", "p1", "현대차/기아/KGM/르노/GM 월간 판매 공시 기반 신규 수집기 필요."),
    Rule("시장 점유율", "ready_existing", "autos_share", "KAMA 회사별 월간 판매", "scripts/ops/sync_quant_major_indicators.py", "exact_if_sales_collected", "p1", "회사별 판매량 합계로 월별 시장점유율 계산 완료."),
    Rule("내수 판매: 모델별", "new_collector_needed", "autos_model_sales", "OEM IR/협회", "-", "exact_target", "p1", "모델별 테이블은 기존 DB에 없음. 제조사 월간 공지 파서 필요."),
    Rule("수출 판매: 모델별", "new_collector_needed", "autos_model_exports", "OEM IR/협회", "-", "exact_target", "p1", "모델별 수출판매는 OEM 공시/IR 기반 신규 수집 필요."),
    Rule("Steel Price", "new_collector_needed", "steel_price", "국내외 철강 시황원", "-", "exact_target", "p2", "현재 repo 내 직접 수집기 없음. 시세 소스 선정 필요."),
    Rule("후판가격", "new_collector_needed", "steel_price", "국내 철강시황/협회", "-", "exact_target", "p1", "조선 업황 핵심지표. 우선순위 높음."),
    Rule("Iron Ore Import Price", "new_collector_needed", "raw_material_price", "원자재 시황원", "-", "exact_target", "p2", "철강 선행지표. 별도 가격 소스 필요."),
    Rule("인터넷 쇼핑", "new_collector_needed", "retail_stats", "통계청/KOSIS", "-", "exact_target", "p2", "월간 소매/온라인쇼핑 통계 신규 수집기 필요."),
    Rule("모바일 쇼핑", "new_collector_needed", "retail_stats", "통계청/KOSIS", "-", "exact_target", "p2", "인터넷 쇼핑 통계와 동일 계열."),
    Rule("카드 결제액 추정치", "new_collector_needed", "card_spending", "카드사/공공지표", "-", "proxy_or_exact", "p2", "현재 repo 내 정확한 월별 카드 업종 지표 수집기 없음."),
    Rule("송출객", "new_collector_needed", "travel_demand", "여행사 IR/공시", "-", "exact_target", "p2", "모두투어/여행업 월간 영업지표 파서 필요."),
    Rule("관광 방문자", "new_collector_needed", "tourism_stats", "관광공사/KOSIS", "-", "exact_target", "p2", "공식 통계 기반 수집기 필요."),
    Rule("구독 서비스", "new_collector_needed", "consumer_spending", "카드/결제 통계", "-", "proxy_target", "p3", "정확한 EPIC 대체는 어려움. 카드 소비 프록시 우선."),
    Rule("유연탄 가격", "new_collector_needed", "energy_price", "원자재 시황원", "-", "exact_target", "p2", "에너지/발전 업황용 별도 소스 필요."),
    Rule("계통한계가격", "new_collector_needed", "power_price", "전력거래소", "-", "exact_target", "p2", "SMP 공식 소스 파서 필요."),
    Rule("Freight Index", "new_collector_needed", "shipping_index", "BDI 계열 시황원", "-", "exact_target", "p2", "조선/해운 업황 선행지표."),
    Rule("철도 여객", "new_collector_needed", "transport_usage", "공공데이터/국토부", "-", "exact_target", "p3", "노선별 철도 이용량 신규 수집 필요."),
    Rule("Macao: Gross Revenue from Gaming", "new_collector_needed", "casino_revenue", "마카오 공식/기업 IR", "-", "exact_target", "p2", "카지노 업황 핵심. 신규 수집기 필요."),
    Rule("파라다이스", "new_collector_needed", "company_monthly_kpi", "기업 IR", "-", "exact_target", "p2", "월 매출/드롭액/홀드율 공시 파서 필요."),
    Rule("GKL", "new_collector_needed", "company_monthly_kpi", "기업 IR", "-", "exact_target", "p2", "월 매출/입장객/드롭액/홀드율 공시 파서 필요."),
    Rule("드림타워", "new_collector_needed", "company_monthly_kpi", "기업 IR", "-", "exact_target", "p2", "제주드림타워 카지노 월간 운영지표 파서 필요."),
    Rule("IPTV 가입자 수", "new_collector_needed", "subscriber_stats", "과기부/기업 IR", "-", "exact_target", "p3", "통신 서비스 월간 가입자 수 별도 수집 필요."),
    Rule("어가추이", "new_collector_needed", "food_price", "수산 통계", "-", "exact_target", "p3", "음식료 수요지표. 현재 수집기 없음."),
    Rule("돼지고기 도매가격", "new_collector_needed", "food_price", "축산물 가격 공공소스", "-", "exact_target", "p3", "일별 가격 소스 필요."),
    Rule("베트남 의류", "ready_existing_partial", "customs_trade", "관세청 customs", "hs_trade_lab/scripts/daily_refresh.py", "proxy_close", "p2", "국가×품목 무역통계로 대체 가능하나 EPIC 정의와 품목 바구니 차이 검증 필요."),
    Rule("베트남 IT제품", "ready_existing_partial", "customs_trade", "관세청 customs", "hs_trade_lab/scripts/daily_refresh.py", "proxy_close", "p2", "국가×품목 무역통계로 대체 가능. HS 매핑 룰 설계 필요."),
    Rule("한국은행 기준금리", "ready_existing", "macro_rate", "한국은행 ECOS", "routes/market_indicators.py", "exact", "p1", "기존 수집/표시 로직 존재."),
    Rule("대중교통 이용현황", "new_collector_needed", "transport_usage", "공공데이터", "-", "exact_target", "p3", "월간 대중교통 이용량 신규 수집 필요."),
    Rule("지하철 노선별 이용현황", "new_collector_needed", "transport_usage", "공공데이터", "-", "exact_target", "p3", "노선별 상세 데이터 신규 수집 필요."),
]


def classify_indicator(name: str, category_name: str) -> dict:
    for rule in RULES:
        if rule.match in name:
            return {
                "status": rule.status,
                "replacement_family": rule.replacement_family,
                "source_system": rule.source_system,
                "collector_path": rule.collector_path,
                "exactness": rule.exactness,
                "priority": rule.priority,
                "notes": rule.notes,
            }

    # category-level fallbacks
    if category_name == "자동차 · 타이어":
        return {
            "status": "new_collector_needed",
            "replacement_family": "autos_sales",
            "source_system": "KAMA/KAIDA/OEM IR",
            "collector_path": "-",
            "exactness": "unknown",
            "priority": "p2",
            "notes": "자동차 카테고리 기본 fallback. 회사/모델 판매 데이터 수집기 필요.",
        }
    if category_name in {"철강", "에너지 · 정유화학", "조선"}:
        return {
            "status": "new_collector_needed",
            "replacement_family": "commodity_or_industry_price",
            "source_system": "시황/공공 원천",
            "collector_path": "-",
            "exactness": "unknown",
            "priority": "p2",
            "notes": "산업 시황 지표군. 공개 소스 재선정 필요.",
        }
    if category_name in {"금융"}:
        return {
            "status": "investigate_existing",
            "replacement_family": "macro_financial",
            "source_system": "ECOS/KRX/공공데이터",
            "collector_path": "-",
            "exactness": "unknown",
            "priority": "p2",
            "notes": "금융/유동성 지표군. ECOS/KRX로 대체 가능한지 추가 분류 필요.",
        }
    if category_name in {"유통 · 소비재 · 렌탈", "화장품", "패션 · 명품", "교육", "미디어 · 엔터테인먼트"}:
        return {
            "status": "new_collector_needed",
            "replacement_family": "consumption_or_company_kpi",
            "source_system": "KOSIS/카드통계/기업 IR",
            "collector_path": "-",
            "exactness": "unknown",
            "priority": "p3",
            "notes": "소비/엔터 계열 지표. 카드소비/IR/KOSIS로 분기 필요.",
        }
    return {
        "status": "manual_review",
        "replacement_family": "unclassified",
        "source_system": "-",
        "collector_path": "-",
        "exactness": "unknown",
        "priority": "p3",
        "notes": "키워드 규칙 미분류. 수동 검토 필요.",
    }


def init_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS epic_indicator_replacement_plan (
            id INTEGER PRIMARY KEY,
            category_code INTEGER NOT NULL,
            category_name TEXT,
            sub_code INTEGER NOT NULL,
            indicator_name TEXT NOT NULL,
            frequency TEXT,
            unit TEXT,
            latest_date TEXT,
            since_date TEXT,
            status TEXT NOT NULL,
            replacement_family TEXT NOT NULL,
            source_system TEXT,
            collector_path TEXT,
            exactness TEXT,
            priority TEXT,
            notes TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(category_code, sub_code, indicator_name)
        );
        """
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        init_table(conn)
        rows = conn.execute(
            """
            select
                category_code,
                max(category_name) as category_name,
                sub_code,
                indicator_name,
                max(frequency) as frequency,
                max(unit) as unit,
                max(latest_date) as latest_date,
                min(since_date) as since_date
            from forward_strategy_indicators
            group by category_code, sub_code, indicator_name
            order by category_code, sub_code, indicator_name
            """
        ).fetchall()

        plans = []
        for row in rows:
            classified = classify_indicator(row["indicator_name"], row["category_name"] or "")
            rec = {
                **dict(row),
                **classified,
            }
            plans.append(rec)
            conn.execute(
                """
                INSERT INTO epic_indicator_replacement_plan (
                    category_code, category_name, sub_code, indicator_name, frequency, unit, latest_date, since_date,
                    status, replacement_family, source_system, collector_path, exactness, priority, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(category_code, sub_code, indicator_name) DO UPDATE SET
                    category_name=excluded.category_name,
                    frequency=excluded.frequency,
                    unit=excluded.unit,
                    latest_date=excluded.latest_date,
                    since_date=excluded.since_date,
                    status=excluded.status,
                    replacement_family=excluded.replacement_family,
                    source_system=excluded.source_system,
                    collector_path=excluded.collector_path,
                    exactness=excluded.exactness,
                    priority=excluded.priority,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    rec["category_code"], rec["category_name"], rec["sub_code"], rec["indicator_name"], rec["frequency"],
                    rec["unit"], rec["latest_date"], rec["since_date"], rec["status"], rec["replacement_family"],
                    rec["source_system"], rec["collector_path"], rec["exactness"], rec["priority"], rec["notes"],
                ),
            )
        conn.commit()

        json_path = OUT_DIR / "epic_indicator_replacement_plan_20260606.json"
        csv_path = OUT_DIR / "epic_indicator_replacement_plan_20260606.csv"
        json_path.write_text(json.dumps(plans, ensure_ascii=False, indent=2), encoding="utf-8")
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(plans[0].keys()) if plans else [])
            writer.writeheader()
            writer.writerows(plans)

        summary = {}
        for p in plans:
            summary[p["status"]] = summary.get(p["status"], 0) + 1

        print(json.dumps({
            "ok": True,
            "rows": len(plans),
            "summary": summary,
            "json": str(json_path),
            "csv": str(csv_path),
        }, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
