#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"


CAFE_CANDIDATES = [
    {
        "indicator_key": "cafe:11:4247",
        "sub": 4247,
        "name": "주택 착공 지표",
        "frequency": "Monthly",
        "unit": "호/건",
        "status": "ready_existing",
        "family": "cafe_housing_construction",
        "source": "국토교통 통계누리 주택건설 착공실적 월계",
        "exactness": "official_monthly_exact",
        "notes": "공식 전국 월별 착공 시계열. 2025~2026년 값은 잠정치.",
    },
    {
        "indicator_key": "cafe:11:3475",
        "sub": 3475,
        "name": "건설기성액/건설수주액",
        "frequency": "Monthly",
        "unit": "억원/지수",
        "status": "source_discontinued",
        "family": "cafe_construction_orders",
        "source": "지표상회 카페 지표 제안 + 통계청/KOSIS 후보",
        "exactness": "candidate_public_statistics",
        "notes": "건설기성액과 건설수주액을 함께 보아 건설사/건자재 수요 사이클을 판단.",
    },
    {
        "indicator_key": "cafe:11:4128",
        "sub": 4128,
        "name": "SK하이닉스 투자자 핵심 월간 지표",
        "frequency": "Monthly",
        "unit": "복합",
        "status": "new_collector_needed",
        "family": "cafe_hynix_memory_cycle",
        "source": "지표상회 카페 지표 제안 + DRAMeXchange/TrendForce/IR 후보",
        "exactness": "mixed_external_and_ir_candidate",
        "notes": "메모리 판가, HBM/DRAM/NAND 수요, 재고, 투자 계획 등 SK하이닉스 투자자용 복합 지표 후보.",
    },
    {
        "indicator_key": "cafe:11:2805",
        "sub": 2805,
        "name": "현대차/기아 출하 + 미국 중고차 지수",
        "frequency": "Monthly",
        "unit": "대/지수",
        "status": "partial_existing",
        "family": "cafe_auto_shipments_used_car",
        "source": "현대차 IR/KAMA/Kia America + Manheim Used Vehicle Value Index 후보",
        "exactness": "existing_shipments_plus_new_collector_needed",
        "notes": "현대차/기아 판매 지표는 일부 보유. 미국 중고차 가격지수 수집기 추가 필요.",
    },
    {
        "indicator_key": "cafe:11:2716",
        "sub": 2716,
        "name": "광고미디어 업종 지표",
        "frequency": "Monthly/Quarterly",
        "unit": "억원/지수",
        "status": "source_discontinued",
        "family": "cafe_ad_media",
        "source": "KOBACO 광고경기전망지수(KAI, 2026년 사업 종료)",
        "exactness": "official_source_discontinued",
        "notes": "KOBACO KAI는 2026년 1월 전망을 끝으로 종료. 활성 매수 신호에서 제외하고 대체 원천 검토.",
    },
    {
        "indicator_key": "cafe:11:2668",
        "sub": 2668,
        "name": "게임주 업황 지표",
        "frequency": "Monthly/Quarterly",
        "unit": "매출/순위/이용자수",
        "status": "partial_existing",
        "family": "cafe_game_sector",
        "source": "Steam official current-player API",
        "exactness": "partial_pc_platform_snapshot",
        "notes": "대표 상장 게임 4종의 PC 동접 스냅샷. 모바일/콘솔 및 매출을 대표하지 않음.",
    },
    {
        "indicator_key": "cafe:11:2650",
        "sub": 2650,
        "name": "영화관/IPTV VOD 지표",
        "frequency": "Monthly",
        "unit": "명/억원/건",
        "status": "partial_existing",
        "family": "cafe_cinema_iptv_vod",
        "source": "KOBIS/영화진흥위원회 + IPTV/ITSTAT 후보",
        "exactness": "iptv_partial_existing_cinema_new_collector_needed",
        "notes": "IPTV 가입자 수는 일부 보유. 영화관 관객수/매출과 IPTV VOD 이용 지표 수집 필요.",
    },
    {
        "indicator_key": "cafe:11:2645:regulation",
        "sub": 264501,
        "name": "중국/인도 배기가스 규제 이벤트",
        "frequency": "Event",
        "unit": "event",
        "status": "partial_existing",
        "family": "cafe_refining_regulation_event",
        "source": "지표상회 카페 지표 제안 + 정책/뉴스 이벤트 후보",
        "exactness": "event_monitor_candidate",
        "notes": "정유주 윤활기유 지표와 함께 볼 중국/인도 배기가스 규제 정책 이벤트 모니터.",
    },
    {
        "indicator_key": "cafe:34:7690",
        "sub": 7690,
        "name": "호텔/면세점/백화점 소비 지표",
        "frequency": "Monthly",
        "unit": "객실/매출/방문객",
        "status": "partial_existing",
        "family": "cafe_hotel_dutyfree_department_store",
        "source": "지표상회 카페 업종 지표 활용글 + 관광공사/면세점협회/KOSIS 후보",
        "exactness": "department_store_partial_existing",
        "notes": "백화점 카드 결제액은 일부 보유. 호텔 객실/외국인 관광객/면세점 매출 수집 필요.",
    },
    {
        "indicator_key": "cafe:34:7633",
        "sub": 7633,
        "name": "양돈/돈육 업황 지표",
        "frequency": "Monthly",
        "unit": "원/kg/두수",
        "status": "new_collector_needed",
        "family": "cafe_pork_hog_cycle",
        "source": "지표상회 카페 업종 지표 활용글 + 축산물품질평가원/농림축산식품부 후보",
        "exactness": "candidate_public_statistics",
        "notes": "팜스토리/우리손에프앤지 관련 돈육 가격, 사육두수, 사료 원가 지표 후보.",
    },
    {
        "indicator_key": "cafe:34:7616",
        "sub": 7616,
        "name": "항공 여객/운임/유류비 지표",
        "frequency": "Monthly",
        "unit": "명/원/달러",
        "status": "partial_existing",
        "family": "cafe_airline_traffic_fare_fuel",
        "source": "브렌트/WTI 월평균 항공유 원가 proxy + KAC 여객 API 권한 대기",
        "exactness": "partial_fuel_cost_proxy",
        "notes": "항공유 원가 proxy만 사용. KAC 여객 API는 현재 서비스키 403으로 미연결.",
    },
    {
        "indicator_key": "cafe:34:7611",
        "sub": 7611,
        "name": "통신 ARPU/가입자/5G 지표",
        "frequency": "Monthly/Quarterly",
        "unit": "명/원",
        "status": "new_collector_needed",
        "family": "cafe_telecom_arpu_subscriber",
        "source": "지표상회 카페 업종 지표 활용글 + 과기정통부/통신사 IR 후보",
        "exactness": "candidate_public_and_ir_statistics",
        "notes": "SKT/KT 관련 무선 가입자, 5G 가입자, ARPU, 해지율 지표 후보.",
    },
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    for item in CAFE_CANDIDATES:
        conn.execute(
            """
            INSERT INTO quant_major_indicator_catalog
            (indicator_key, epic_category_code, epic_sub_code, epic_indicator_name,
             frequency, base_unit, status, replacement_family, source_system,
             collector_path, exactness, priority, notes, category_id, enabled, updated_at)
            VALUES (?, 34, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'p2', ?, 'cafe_quant_candidates', 1, CURRENT_TIMESTAMP)
            ON CONFLICT(indicator_key) DO UPDATE SET
                epic_category_code=excluded.epic_category_code,
                epic_sub_code=excluded.epic_sub_code,
                epic_indicator_name=excluded.epic_indicator_name,
                frequency=excluded.frequency,
                base_unit=excluded.base_unit,
                status=excluded.status,
                replacement_family=excluded.replacement_family,
                source_system=excluded.source_system,
                collector_path=excluded.collector_path,
                exactness=excluded.exactness,
                priority=excluded.priority,
                notes=excluded.notes,
                category_id=excluded.category_id,
                enabled=excluded.enabled,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                item["indicator_key"],
                item["sub"],
                item["name"],
                item["frequency"],
                item["unit"],
                item["status"],
                item["family"],
                item["source"],
                "scripts/ops/sync_cafe_quant_indicator_candidates.py",
                item["exactness"],
                item["notes"],
            ),
        )
    conn.commit()
    print({"upserted": len(CAFE_CANDIDATES), "db": str(DB_PATH)})
    conn.close()


if __name__ == "__main__":
    main()
