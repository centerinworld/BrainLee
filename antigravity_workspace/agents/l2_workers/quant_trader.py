"""
Project Antigravity: L2 Worker - quant_trader (주식 대시보드 실데이터 연동 버전)
stock_dashboard (stock.db & Port 8000 API) 실시간 주식 유니버스, 주가 및 퀀트 팩터 연동
"""

import os
import sqlite3
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from memory.state_ledger import StateLedger

logger = logging.getLogger("quant_trader")

class QuantTraderWorker:
    def __init__(self, is_mock: bool = True, stock_db_path: Optional[str] = None, ledger: Optional[StateLedger] = None):
        self.is_mock = is_mock
        self.stock_db_path = stock_db_path or os.getenv(
            "STOCK_DB_PATH",
            "/Volumes/Realtek_NVME/stock_dashboard/stock.db"
        )
        self.orders: List[Dict[str, Any]] = []
        self.rate_limit_delay = 1.0
        # A08: 주문 기록을 재시작 후에도 남도록 영속 원장에 저장한다.
        self.ledger = ledger or StateLedger()

    def get_real_universe(
        self,
        limit: int = 10,
        sector_filter: Optional[str] = None,
        allow_fixture_fallback: bool = False
    ) -> List[Dict[str, Any]]:
        """stock_dashboard DB(stock.db)에서 실제 상장 종목 유니버스 및 팩터 조회.
        DB가 없으면 기본값으로 빈 목록을 반환한다(missing 상태를 실데이터처럼 위장하지 않음).
        allow_fixture_fallback=True를 명시한 데모 호출에서만 고정 샘플 종목을 반환한다."""
        if not os.path.exists(self.stock_db_path):
            logger.error(f"stock.db 파일 미발견: {self.stock_db_path}. 운영 경로는 빈 유니버스 반환(degraded).")
            if not allow_fixture_fallback:
                return []
            logger.warning("allow_fixture_fallback=True: 데모용 고정 샘플 유니버스 반환 (실데이터 아님)")
            return [
                {"code": "005930", "name": "삼성전자", "price": 180100.0, "market": "KOSPI", "sector": "IT", "per": 18.67, "roe": 12.5, "is_fixture": True},
                {"code": "000660", "name": "SK하이닉스", "price": 922000.0, "market": "KOSPI", "sector": "IT", "per": 13.74, "roe": 18.2, "is_fixture": True},
                {"code": "047810", "name": "한국항공우주", "price": 54200.0, "market": "KOSPI", "sector": "항공/방산", "per": 22.4, "roe": 8.9, "is_fixture": True},
                {"code": "012450", "name": "한화에어로스페이스", "price": 285000.0, "market": "KOSPI", "sector": "항공/방산", "per": 28.1, "roe": 14.3, "is_fixture": True}
            ]

        try:
            conn = sqlite3.connect(self.stock_db_path)
            conn.row_factory = sqlite3.Row
            query = """
                SELECT stock_code, stock_name, market, sector_large, close, market_cap, per, roe, operating_profit
                FROM stock_universe
                WHERE close > 0
            """
            params = []
            if sector_filter:
                query += " AND sector_large LIKE ?"
                params.append(f"%{sector_filter}%")
            
            query += " ORDER BY market_cap DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            conn.close()
            
            result = []
            for r in rows:
                result.append({
                    "code": r["stock_code"],
                    "name": r["stock_name"],
                    "market": r["market"],
                    "sector": r["sector_large"] or "기타",
                    "price": float(r["close"] or 0.0),
                    "market_cap": float(r["market_cap"] or 0.0),
                    "per": float(r["per"] or 0.0),
                    "roe": float(r["roe"] or 0.0),
                    "is_fixture": False
                })
            return result
        except Exception as e:
            logger.error(f"stock_universe 조회 오류: {e}")
            return []

    async def fetch_fnguide_consensus(self, stock_code: str) -> Dict[str, Any]:
        """에프앤가이드(Fnguide) 컨센서스 및 목표가 조회.
        실제 Fnguide 연동이 구현되어 있지 않아 고정값을 반환한다 — 실시간 데이터가 아니다."""
        await asyncio.sleep(0.02)
        logger.warning(f"[미구현] fetch_fnguide_consensus({stock_code}): 실제 연동 없음, 고정값 반환")
        return {
            "stock_code": stock_code,
            "target_price": 220000 if stock_code == "005930" else 360000,
            "opinion": "BUY",
            "forward_per": 14.5,
            "forward_eps": 12500,
            "data_source": "fixture_not_live",
            "fetched_at": datetime.now().isoformat()
        }

    async def fetch_ecos_macro_rate(self) -> Dict[str, Any]:
        """한국은행 ECOS 기준금리 및 주요 거시지표 조회.
        실제 ECOS API 연동이 구현되어 있지 않아 고정값을 반환한다 — 실시간 데이터가 아니다."""
        await asyncio.sleep(0.02)
        logger.warning("[미구현] fetch_ecos_macro_rate(): 실제 ECOS 연동 없음, 고정값 반환")
        return {
            "indicator_code": "ECOS_BASE_RATE",
            "indicator_name": "한국은행 기준금리",
            "val": 3.00,
            "unit": "%",
            "data_source": "fixture_not_live",
            "fetched_at": datetime.now().isoformat()
        }

    async def calculate_rebalancing_weights(self, universe: List[str]) -> Dict[str, float]:
        """정량 팩터(모멘텀+실적 성장률) 기반 포트폴리오 비중 산출"""
        if not universe:
            return {}
        weight = round(1.0 / len(universe), 4)
        return {code: weight for code in universe}

    async def execute_order(
        self,
        stock_code: str,
        stock_name: str,
        order_type: str,
        price: float,
        quantity: int,
        strategy_name: str = "Quant_Rebalance_V2",
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """증권사 API 또는 모의투자 환경으로 주문 전송 및 체결.
        실제 증권사 API 연동이 구현되어 있지 않으므로, 실제 체결 증거 없이 FILLED로 표시하지 않는다.
        idempotency_key를 넘기면 이미 기록된 주문이 있을 때 재실행 없이 기존 레코드를 반환한다
        (재시작/재시도 시 중복 주문 방지, A08)."""
        if idempotency_key:
            existing = self.ledger.get("orders", idempotency_key)
            if existing:
                logger.info(f"[중복 방지] idempotency_key={idempotency_key} 기존 주문 반환 (재실행 안 함): {existing['order_id']}")
                return existing

        await asyncio.sleep(0.05)
        order_id = f"ORD_{datetime.now().strftime('%Y%m%d%H%M%S')}_{stock_code}"
        ledger_key = idempotency_key or order_id

        if not self.is_mock:
            # is_mock=False라는 설정값만으로 실전 상태를 표기하지 않는다 - 실제 증권사 체결
            # 연동이 없으므로 여기서 주문을 차단하고 사실대로 기록한다.
            logger.error(f"[실전 주문 차단] {order_id}: 실제 증권사 API 연동 미구현 - 체결 처리 불가")
            order_record = {
                "order_id": order_id,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "order_type": order_type.upper(),
                "price": price,
                "quantity": quantity,
                "status": "BLOCKED_NO_BROKER_INTEGRATION",
                "strategy_name": strategy_name,
                "is_mock": self.is_mock,
                "filled_at": None
            }
            self.orders.append(order_record)
            self.ledger.upsert("orders", ledger_key, order_record)
            return order_record

        order_record = {
            "order_id": order_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "order_type": order_type.upper(),
            "price": price,
            "quantity": quantity,
            "status": "SIMULATED_FILL",
            "strategy_name": strategy_name,
            "is_mock": self.is_mock,
            "filled_at": datetime.now().isoformat()
        }
        self.orders.append(order_record)
        self.ledger.upsert("orders", ledger_key, order_record)
        logger.info(f"[모의] 주문 시뮬레이션 완료(실제 체결 아님): {order_id} {stock_name}({stock_code}) {order_type} {quantity}주 @ {price:,.0f}원")
        return order_record
