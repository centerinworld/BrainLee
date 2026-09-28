"""collectors/ibd_rs_collector.py — IBD RS (William O'Neil 원본 공식) 일별 계산

RS_Raw = 0.4*(C/C₆₃) + 0.2*(C/C₁₂₆) + 0.2*(C/C₁₈₉) + 0.2*(C/C₂₅₂)
  C = 당일 종가, C_n = n거래일 전 종가 (달력일 아님)

Cross-sectional percentile: PR = (B + E/2) / N × 100  → rs_score 1~99 (반올림)
  B = rs_raw가 더 낮은 종목 수, E = 동일 종목 수, N = 유니버스 전체 수

결과는 ibd_rs_daily 테이블(PostgreSQL)에 UPSERT.

유니버스 필터:
  - kind_stkcert_nm = '보통주' (우선주 제외)
  - secugrp_nm = '주권'       (ETF·ETN·리츠·외국주권 제외)
  - market_cap >= 1000 (억원)
  - listed_date: 상장 후 252거래일 이상 경과 (신규 상장 제외)
  - 종목명에 '스팩' 포함 → SPAC 제외
  - 해당 날짜 기준으로 253거래일 close 데이터가 실제로 있는 종목만
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ibd_rs_daily (
    date          TEXT    NOT NULL,
    stock_code    TEXT    NOT NULL,
    rs_raw        REAL    NOT NULL,
    rs_score      INTEGER NOT NULL,
    rs_rank       INTEGER NOT NULL,
    universe_size INTEGER NOT NULL,
    PRIMARY KEY (date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_ibd_rs_date      ON ibd_rs_daily (date);
CREATE INDEX IF NOT EXISTS idx_ibd_rs_score     ON ibd_rs_daily (date, rs_score DESC);
CREATE INDEX IF NOT EXISTS idx_ibd_rs_stockcode ON ibd_rs_daily (stock_code, date DESC);
"""


def _ensure_table(conn) -> None:
    cur = conn.cursor()
    for stmt in _CREATE_TABLE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)
    conn.commit()


def _fetch_universe_codes(conn) -> list[str]:
    """DB에서 유니버스 필터를 통과하는 종목코드 목록 반환."""
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code
        FROM stock_universe
        WHERE kind_stkcert_nm = '보통주'
          AND secugrp_nm = '주권'
          AND (market_cap IS NULL OR market_cap >= 1000)
          AND stock_name NOT LIKE '%스팩%'
          AND stock_name NOT LIKE '%SPAC%'
    """)
    return [r[0] for r in cur.fetchall()]


def _fetch_price_matrix(conn, codes: list[str], min_rows: int = 253) -> dict[str, list[float]]:
    """각 종목의 최근 253거래일 close 가격을 {code: [oldest…newest]} 형태로 반환.
    데이터가 min_rows 미만인 종목은 포함하지 않는다."""
    if not codes:
        return {}
    placeholders = ",".join(["%s"] * len(codes))
    cur = conn.cursor()
    cur.execute(f"""
        SELECT stock_code, date, close
        FROM price_history
        WHERE stock_code IN ({placeholders})
          AND close > 0
        ORDER BY stock_code, date
    """, codes)
    rows = cur.fetchall()

    from collections import defaultdict
    tmp: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        code, date, close = row[0], row[1], float(row[2])
        tmp[code].append((date, close))

    result: dict[str, list[float]] = {}
    for code, price_pairs in tmp.items():
        # 최신 253일만 사용
        recent = price_pairs[-min_rows:]
        if len(recent) >= min_rows:
            result[code] = [p[1] for p in recent]
    return result


def _calc_rs_raw(prices: list[float]) -> Optional[float]:
    """prices[-1] = 당일, prices[0] = 가장 오래된 날. len >= 253 필수."""
    c = prices[-1]
    c63  = prices[-64]   # 63거래일 전
    c126 = prices[-127]
    c189 = prices[-190]
    c252 = prices[-253]
    if 0 in (c63, c126, c189, c252):
        return None
    return 0.4 * (c / c63) + 0.2 * (c / c126) + 0.2 * (c / c189) + 0.2 * (c / c252)


def _calc_percentile_ranks(raw_scores: dict[str, float]) -> dict[str, tuple[float, int, int]]:
    """cross-sectional percentile 계산.
    반환: {code: (rs_raw, rs_score 1~99, rs_rank 1~N)}"""
    n = len(raw_scores)
    if n == 0:
        return {}

    sorted_codes = sorted(raw_scores, key=lambda c: raw_scores[c])
    sorted_vals  = [raw_scores[c] for c in sorted_codes]

    result: dict[str, tuple[float, int, int]] = {}
    for i, code in enumerate(sorted_codes):
        v = raw_scores[code]
        b = sum(1 for x in sorted_vals if x < v)
        e = sum(1 for x in sorted_vals if x == v)
        pr = (b + e / 2) / n * 100
        rs_score = max(1, min(99, round(pr)))
        # rs_rank: 1 = 최고 RS (내림차순 순위)
        rs_rank = n - i
        result[code] = (v, rs_score, rs_rank)

    return result


def compute_ibd_rs(target_date: Optional[str] = None) -> dict:
    """IBD RS를 계산해 ibd_rs_daily에 UPSERT하고 요약 결과를 반환.

    target_date: 'YYYY-MM-DD'. None이면 price_history의 최신 거래일 자동 사용.
    """
    import db_compat

    conn = db_compat.connect_primary_db()
    try:
        _ensure_table(conn)

        # 최신 거래일 결정
        cur = conn.cursor()
        if target_date:
            calc_date = target_date
        else:
            cur.execute("""
                SELECT MAX(date) FROM price_history
                WHERE stock_code NOT LIKE '^%'
                  AND close > 0
            """)
            calc_date = cur.fetchone()[0]
            if not calc_date:
                return {"error": "price_history에 데이터 없음"}

        logger.info(f"[IBD RS] 계산 날짜: {calc_date}")

        # 이미 계산된 경우 건너뜀
        cur.execute("SELECT COUNT(*) FROM ibd_rs_daily WHERE date = %s", (calc_date,))
        existing = cur.fetchone()[0]
        if existing > 0:
            logger.info(f"[IBD RS] {calc_date} 이미 계산됨 ({existing}건) — 스킵")
            return {"date": calc_date, "skipped": True, "universe_size": existing}

        # 유니버스 종목 로드
        universe_codes = _fetch_universe_codes(conn)
        logger.info(f"[IBD RS] 유니버스 후보: {len(universe_codes)}종목")

        # 가격 매트릭스 로드 (253거래일)
        price_matrix = _fetch_price_matrix(conn, universe_codes)
        logger.info(f"[IBD RS] 가격 데이터 충분 종목: {len(price_matrix)}종목")

        # RS_Raw 계산
        raw_scores: dict[str, float] = {}
        for code, prices in price_matrix.items():
            val = _calc_rs_raw(prices)
            if val is not None:
                raw_scores[code] = val

        logger.info(f"[IBD RS] RS_Raw 계산 완료: {len(raw_scores)}종목")

        # Percentile rank 계산
        ranked = _calc_percentile_ranks(raw_scores)
        universe_size = len(ranked)

        # Bulk UPSERT
        if ranked:
            cur.executemany(
                """
                INSERT INTO ibd_rs_daily (date, stock_code, rs_raw, rs_score, rs_rank, universe_size)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (date, stock_code) DO UPDATE
                    SET rs_raw = EXCLUDED.rs_raw,
                        rs_score = EXCLUDED.rs_score,
                        rs_rank = EXCLUDED.rs_rank,
                        universe_size = EXCLUDED.universe_size
                """,
                [
                    (calc_date, code, float(rs_raw), rs_score, rs_rank, universe_size)
                    for code, (rs_raw, rs_score, rs_rank) in ranked.items()
                ],
            )
            conn.commit()

        logger.info(f"[IBD RS] UPSERT 완료: {universe_size}건 → ibd_rs_daily")
        return {"date": calc_date, "universe_size": universe_size, "computed": True}

    except Exception as exc:
        logger.error(f"[IBD RS] compute 오류: {exc}", exc_info=True)
        conn.rollback()
        raise
    finally:
        conn.close()


def get_rs_score(conn, stock_code: str, date: str) -> Optional[int]:
    """단일 종목·날짜의 rs_score 반환. 없으면 None."""
    cur = conn.cursor()
    cur.execute(
        "SELECT rs_score FROM ibd_rs_daily WHERE stock_code = %s AND date <= %s ORDER BY date DESC LIMIT 1",
        (stock_code, date),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None
