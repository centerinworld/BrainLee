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
    def __init__(
        self,
        is_mock: bool = True,
        stock_db_path: Optional[str] = None,
        postgres_url: Optional[str] = None,
        ledger: Optional[StateLedger] = None
    ):
        self.is_mock = is_mock
        # A10: stock_dashboard의 운영 지침(runtime/CLAUDE.md)은 "운영 기준 DB는 PostgreSQL이다.
        # stock.db는 레거시/오프라인 호환용 SQLite 스냅샷이며 운영 판단의 근거로 쓰지 않는다"고
        # 명시한다. PostgreSQL을 우선 사용하고, SQLite는 명시적 저하 폴백으로만 쓴다.
        # env_loader.load_unified_env()가 stock_dashboard/runtime/.env의 POSTGRES_DATABASE_URL을
        # 이미 로드해두므로 그것도 인식한다 (SQLAlchemy용 "+psycopg" 스킴은 psycopg2가 이해하지
        # 못하므로 일반 postgresql:// 형태로 정규화한다).
        raw_pg_url = postgres_url or os.getenv("STOCK_POSTGRES_URL") or os.getenv("POSTGRES_DATABASE_URL") or os.getenv("DATABASE_URL")
        self.postgres_url = raw_pg_url.replace("postgresql+psycopg://", "postgresql://") if raw_pg_url else None
        self.stock_db_path = stock_db_path or os.getenv(
            "STOCK_DB_PATH",
            "/Volumes/Realtek_NVME/stock_dashboard/stock.db"
        )
        self.orders: List[Dict[str, Any]] = []
        self.rate_limit_delay = 1.0
        # A08: 주문 기록을 재시작 후에도 남도록 영속 원장에 저장한다.
        self.ledger = ledger or StateLedger()

    def _get_universe_from_postgres(self, limit: int, sector_filter: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        """stock_dashboard의 운영 기준 DB(PostgreSQL)에서 조회. 연결 불가 시 None (호출자가 폴백 여부 결정)."""
        if not self.postgres_url:
            return None
        try:
            import psycopg2
            conn = psycopg2.connect(self.postgres_url, connect_timeout=3)
            cur = conn.cursor()
            # stock_universe는 base_date별 시계열(날짜마다 한 행)이므로 최신 base_date로 고정하지
            # 않으면 같은 종목이 오래된 날짜의 행과 함께 중복으로 섞여 나온다.
            query = """
                SELECT stock_code, stock_name, market, sector_large, close, market_cap, per, roe, base_date, updated_at
                FROM stock_universe
                WHERE close > 0 AND base_date = (SELECT MAX(base_date) FROM stock_universe)
            """
            params: List[Any] = []
            if sector_filter:
                query += " AND sector_large LIKE %s"
                params.append(f"%{sector_filter}%")
            query += " ORDER BY market_cap DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [
                {
                    "code": r[0], "name": r[1], "market": r[2], "sector": r[3] or "기타",
                    "price": float(r[4] or 0.0), "market_cap": float(r[5] or 0.0),
                    "per": float(r[6] or 0.0), "roe": float(r[7] or 0.0),
                    "snapshot_date": r[8], "updated_at": str(r[9]) if r[9] else None,
                    "is_fixture": False, "data_source": "postgres_operational"
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"[A10] PostgreSQL(운영 기준 DB) 조회 실패 ({e}) - 레거시 SQLite 스냅샷으로 저하 폴백")
            return None

    def get_real_universe(
        self,
        limit: int = 10,
        sector_filter: Optional[str] = None,
        allow_fixture_fallback: bool = False
    ) -> List[Dict[str, Any]]:
        """stock_dashboard 상장 종목 유니버스 및 팩터 조회.
        운영 기준 DB(PostgreSQL)를 우선 사용하고, 접속 불가 시에만 레거시 SQLite(stock.db)로
        저하 폴백한다 - stock_dashboard 자체 운영 지침이 stock.db를 "운영 판단에 쓰지 말 것"으로
        규정하므로, 두 경로 모두 결과에 data_source를 남겨 어느 쪽인지 항상 구분되게 한다.
        DB가 전부 없으면 기본값으로 빈 목록을 반환한다(missing 상태를 실데이터처럼 위장하지 않음).
        allow_fixture_fallback=True를 명시한 데모 호출에서만 고정 샘플 종목을 반환한다."""
        pg_result = self._get_universe_from_postgres(limit, sector_filter)
        if pg_result is not None:
            return pg_result

        if not os.path.exists(self.stock_db_path):
            logger.error(f"PostgreSQL도 불가하고 stock.db 파일도 미발견: {self.stock_db_path}. 빈 유니버스 반환(degraded).")
            if not allow_fixture_fallback:
                return []
            logger.warning("allow_fixture_fallback=True: 데모용 고정 샘플 유니버스 반환 (실데이터 아님)")
            return [
                {"code": "005930", "name": "삼성전자", "price": 180100.0, "market": "KOSPI", "sector": "IT", "per": 18.67, "roe": 12.5, "is_fixture": True, "data_source": "fixture_demo"},
                {"code": "000660", "name": "SK하이닉스", "price": 922000.0, "market": "KOSPI", "sector": "IT", "per": 13.74, "roe": 18.2, "is_fixture": True, "data_source": "fixture_demo"},
                {"code": "047810", "name": "한국항공우주", "price": 54200.0, "market": "KOSPI", "sector": "항공/방산", "per": 22.4, "roe": 8.9, "is_fixture": True, "data_source": "fixture_demo"},
                {"code": "012450", "name": "한화에어로스페이스", "price": 285000.0, "market": "KOSPI", "sector": "항공/방산", "per": 28.1, "roe": 14.3, "is_fixture": True, "data_source": "fixture_demo"}
            ]

        logger.warning(f"[A10] 레거시 SQLite 스냅샷({self.stock_db_path})으로 조회 - stock_dashboard 운영 지침상 운영 판단 근거로 사용 금지 대상")
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
                    "is_fixture": False,
                    "data_source": "legacy_sqlite_snapshot"
                })
            return result
        except Exception as e:
            logger.error(f"stock_universe(SQLite) 조회 오류: {e}")
            return []

    def _pg_conn(self):
        """운영 기준 DB(PostgreSQL) 연결. 불가하면 None (호출자가 저하 처리)."""
        if not self.postgres_url:
            return None
        try:
            import psycopg2
            return psycopg2.connect(self.postgres_url, connect_timeout=3)
        except Exception as e:
            logger.warning(f"PostgreSQL 연결 실패: {e}")
            return None

    async def fetch_fnguide_consensus(self, stock_code: str) -> Dict[str, Any]:
        """stock_dashboard가 이미 수집해 둔 애널리스트 컨센서스(consensus_targets)와
        추정실적(forward_estimates)을 조회한다. 두 테이블 모두 Fnguide/증권사 리포트에서
        수집된 실데이터이며, 이 워커가 직접 스크레이핑하지 않는다."""
        await asyncio.sleep(0.0)
        conn = self._pg_conn()
        if conn is None:
            logger.error(f"fetch_fnguide_consensus({stock_code}): PostgreSQL 불가, 데이터 반환 불가")
            return {
                "stock_code": stock_code, "target_price": None, "opinion": None,
                "forward_per": None, "forward_eps": None,
                "data_source": "unavailable_no_postgres",
                "fetched_at": datetime.now().isoformat()
            }
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT report_date, securities_firm, opinion, target_price
                FROM consensus_targets
                WHERE stock_code = %s
                ORDER BY report_date DESC
                LIMIT 1
                """,
                (stock_code,)
            )
            latest = cur.fetchone()

            cur.execute(
                """
                SELECT AVG(target_price), COUNT(DISTINCT securities_firm)
                FROM consensus_targets
                WHERE stock_code = %s AND report_date::date >= (
                    SELECT MAX(report_date::date) FROM consensus_targets WHERE stock_code = %s
                ) - INTERVAL '90 days'
                """,
                (stock_code, stock_code)
            )
            avg_row = cur.fetchone()

            cur.execute(
                """
                SELECT period, eps_원, per, opinion, estimate_date
                FROM forward_estimates
                WHERE stock_code = %s AND is_estimate = 1
                ORDER BY period ASC
                LIMIT 1
                """,
                (stock_code,)
            )
            forward = cur.fetchone()
            cur.close()
            conn.close()

            if latest is None and forward is None:
                logger.warning(f"fetch_fnguide_consensus({stock_code}): consensus_targets/forward_estimates에 데이터 없음")
                return {
                    "stock_code": stock_code, "target_price": None, "opinion": None,
                    "forward_per": None, "forward_eps": None,
                    "data_source": "no_data_in_db",
                    "fetched_at": datetime.now().isoformat()
                }

            return {
                "stock_code": stock_code,
                "report_date": latest[0] if latest else None,
                "securities_firm": latest[1] if latest else None,
                "target_price": float(latest[3]) if latest else None,
                "opinion": latest[2] if latest else None,
                "avg_target_price_90d": float(avg_row[0]) if avg_row and avg_row[0] is not None else None,
                "num_analysts_90d": int(avg_row[1]) if avg_row and avg_row[1] is not None else 0,
                "forward_period": forward[0] if forward else None,
                "forward_eps": float(forward[1]) if forward and forward[1] is not None else None,
                "forward_per": float(forward[2]) if forward and forward[2] is not None else None,
                "data_source": "postgres_consensus_targets+forward_estimates",
                "fetched_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"fetch_fnguide_consensus({stock_code}) 조회 오류: {e}")
            return {
                "stock_code": stock_code, "target_price": None, "opinion": None,
                "forward_per": None, "forward_eps": None,
                "data_source": "error",
                "fetched_at": datetime.now().isoformat()
            }

    async def fetch_ecos_macro_rate(self) -> Dict[str, Any]:
        """stock_dashboard가 이미 수집해 둔 global_macro_data(BOK ECOS/KOSIS/Fed 등 원천)에서
        기준금리 및 주요 거시지표 최신값을 조회한다."""
        await asyncio.sleep(0.0)
        conn = self._pg_conn()
        indicators = ["KR_BASE_RATE", "US_FED_RATE", "KR_USD_KRW", "US_10Y_YIELD", "KR_KOSPI"]
        if conn is None:
            logger.error("fetch_ecos_macro_rate(): PostgreSQL 불가, 데이터 반환 불가")
            return {
                "indicator_code": "KR_BASE_RATE", "val": None, "unit": None,
                "related": {}, "data_source": "unavailable_no_postgres",
                "fetched_at": datetime.now().isoformat()
            }
        try:
            cur = conn.cursor()
            related: Dict[str, Any] = {}
            for code in indicators:
                cur.execute(
                    """
                    SELECT date, value, prev_value, change_pct
                    FROM global_macro_data
                    WHERE indicator_code = %s
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (code,)
                )
                row = cur.fetchone()
                if row:
                    related[code] = {
                        "date": row[0], "value": row[1],
                        "prev_value": row[2], "change_pct": row[3]
                    }
            cur.close()
            conn.close()

            base = related.get("KR_BASE_RATE")
            if not related:
                logger.warning("fetch_ecos_macro_rate(): global_macro_data에 데이터 없음")
                return {
                    "indicator_code": "KR_BASE_RATE", "val": None, "unit": None,
                    "related": {}, "data_source": "no_data_in_db",
                    "fetched_at": datetime.now().isoformat()
                }

            return {
                "indicator_code": "KR_BASE_RATE",
                "indicator_name": "한국은행 기준금리",
                "val": base["value"] if base else None,
                "as_of": base["date"] if base else None,
                "unit": "%",
                "related": related,
                "data_source": "postgres_global_macro_data",
                "fetched_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"fetch_ecos_macro_rate() 조회 오류: {e}")
            return {
                "indicator_code": "KR_BASE_RATE", "val": None, "unit": None,
                "related": {}, "data_source": "error",
                "fetched_at": datetime.now().isoformat()
            }

    async def calculate_rebalancing_weights(self, universe: List[str]) -> Dict[str, float]:
        """정량 팩터(모멘텀+실적 성장률) 기반 포트폴리오 비중 산출.
        stock_dashboard가 이미 계산해 둔 strategy_feature_snapshot(120일 모멘텀)과
        forward_estimates(EPS 성장률)를 합성해 z-score 기반 비중을 산출한다.
        두 팩터 모두 없는 종목은 팩터 계산에서 제외하고 균등비중으로 채운다
        (조용히 가짜 팩터 값을 쓰지 않는다)."""
        await asyncio.sleep(0.0)
        if not universe:
            return {}

        conn = self._pg_conn()
        if conn is None:
            logger.warning("calculate_rebalancing_weights(): PostgreSQL 불가 - 균등비중으로 저하 폴백")
            weight = round(1.0 / len(universe), 4)
            return {code: weight for code in universe}

        momentum: Dict[str, float] = {}
        growth: Dict[str, float] = {}
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT ON (stock_code) stock_code, ret_120d
                FROM strategy_feature_snapshot
                WHERE stock_code = ANY(%s) AND ret_120d IS NOT NULL
                ORDER BY stock_code, snapshot_date DESC
                """,
                (universe,)
            )
            for code, ret_120d in cur.fetchall():
                momentum[code] = float(ret_120d)

            cur.execute(
                """
                SELECT DISTINCT ON (stock_code) stock_code, eps_growth_pct
                FROM forward_estimates
                WHERE stock_code = ANY(%s) AND is_estimate = 1 AND eps_growth_pct IS NOT NULL
                ORDER BY stock_code, period ASC
                """,
                (universe,)
            )
            for code, eps_growth_pct in cur.fetchall():
                growth[code] = float(eps_growth_pct)
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"calculate_rebalancing_weights() 팩터 조회 오류: {e} - 균등비중으로 저하 폴백")
            weight = round(1.0 / len(universe), 4)
            return {code: weight for code in universe}

        def _zscores(values: Dict[str, float]) -> Dict[str, float]:
            if len(values) < 2:
                return {k: 0.0 for k in values}
            vs = list(values.values())
            mean = sum(vs) / len(vs)
            variance = sum((v - mean) ** 2 for v in vs) / len(vs)
            std = variance ** 0.5
            if std == 0:
                return {k: 0.0 for k in values}
            return {k: (v - mean) / std for k, v in values.items()}

        mom_z = _zscores(momentum)
        growth_z = _zscores(growth)

        scored: Dict[str, float] = {}
        no_factor: List[str] = []
        for code in universe:
            has_mom = code in mom_z
            has_growth = code in growth_z
            if not has_mom and not has_growth:
                no_factor.append(code)
                continue
            # 둘 중 하나만 있으면 있는 팩터만 사용
            parts = [v for v in (mom_z.get(code), growth_z.get(code)) if v is not None]
            scored[code] = sum(parts) / len(parts)

        if no_factor:
            logger.warning(
                f"calculate_rebalancing_weights(): {len(no_factor)}/{len(universe)}개 종목 "
                f"팩터 데이터 없음(균등비중 처리): {no_factor}"
            )

        weights: Dict[str, float] = {}
        if scored:
            # 음수 스코어도 최소 비중을 갖도록 softmax로 양수화
            import math
            max_score = max(scored.values())
            exp_scores = {k: math.exp(v - max_score) for k, v in scored.items()}
            total_exp = sum(exp_scores.values())
            factor_pool_share = len(scored) / len(universe)
            for code, exp_v in exp_scores.items():
                weights[code] = round((exp_v / total_exp) * factor_pool_share, 6)

        if no_factor:
            equal_share = (1.0 - sum(weights.values())) / len(no_factor)
            for code in no_factor:
                weights[code] = round(equal_share, 6)

        return weights

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
