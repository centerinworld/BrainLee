#!/usr/bin/env python3
"""
scripts/ops/sync_us_daily_quotes_and_factors.py

미국 종목 전용 OHLCV 5년 일별 시세 적재 & 팩터 스냅샷 완전판 업데이트.

팩터 목록 (신 스키마):
  - 가격: price, high_52w, low_52w
  - 이동평균: ma5, ma20, ma60, ma200, above_200ma
  - 수익률: return_1m, return_3m, return_6m, return_1y
  - 기술: atr14, atr_stop_loss, atr_risk_pct, rs_score
  - 가치: graham_intrinsic, graham_discount, per, pbr, eps, bps
  - 재무: op_margin, roe, roa, revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy
  - 현금흐름: fcf_yield, debt_to_equity
  - 종합: total_score, system_action
"""

import os
import sys
import sqlite3
import math
import logging
import argparse
from datetime import datetime, timedelta

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(CURRENT_DIR))
sys.path.insert(0, PROJECT_ROOT)

import yfinance as yf
from db_utils import connect_stock_db

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("sync_us_daily")

DB_PATH = os.path.join(PROJECT_ROOT, "stock.db")

# S&P500 근사 3M 연평균 수익률 (RS 점수 기준선)
MARKET_3M_BASELINE = 3.5


def get_db_connection():
    """Open the configured primary store, never a side-copy of stock.db.

    The dashboard runs PostgreSQL as its primary database.  Direct sqlite3
    opens are transparently routed only inside the server process, which left
    manual collection runs and API reads on different stores.  Using the shared
    connector makes scheduled and operator-triggered collection identical.
    """
    return connect_stock_db(timeout=30, wal=True)


def _safe_float(val):
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _compute_atr14(closes: list) -> float | None:
    """단순 종가 차이 기반 ATR-14 (True Range 근사)."""
    if len(closes) < 15:
        return None
    tr_list = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - 14, len(closes))]
    return sum(tr_list) / len(tr_list) if tr_list else None


def _compute_system_action(total_score: float | None, above_200ma: int,
                           rs_score: float | None, return_3m: float | None,
                           graham_discount: float | None) -> str:
    """
    종합 점수 기반 매매 신호 결정.
    BUY  : score >= 70 AND 200MA 위 AND RS 양수
    HOLD : score >= 50 AND 200MA 위
    SELL : score < 40 OR 200MA 아래
    WATCH: 그 외
    """
    sc = total_score or 0.0
    rs = rs_score or 0.0
    ret3 = return_3m or 0.0

    if sc >= 70 and above_200ma == 1 and rs > 0:
        return "BUY"
    elif sc >= 70 and above_200ma == 1:
        return "HOLD"
    elif sc >= 50 and above_200ma == 1:
        return "HOLD"
    elif sc < 40 or (above_200ma == 0 and sc < 50):
        return "SELL"
    else:
        return "WATCH"


def _calc_total_score(row: dict) -> float:
    """
    퀀트 종합 점수 계산 (0~100).
    각 항목 점수를 합산하여 정규화.
    """
    score = 0.0

    # 수익률 (30점)
    r1m = row.get("return_1m") or 0
    r3m = row.get("return_3m") or 0
    r6m = row.get("return_6m") or 0
    r1y = row.get("return_1y") or 0
    if r1m > 0: score += 5
    if r3m > 5: score += 7
    elif r3m > 0: score += 4
    if r6m > 10: score += 9
    elif r6m > 0: score += 5
    if r1y > 20: score += 9
    elif r1y > 0: score += 5

    # 재무 품질 (30점)
    opm = row.get("op_margin") or 0
    roe = row.get("roe") or 0
    rev_g = row.get("revenue_growth_yoy") or 0
    if opm > 20: score += 10
    elif opm > 10: score += 6
    elif opm > 0: score += 3
    if roe > 15: score += 10
    elif roe > 8: score += 6
    elif roe > 0: score += 3
    if rev_g > 10: score += 10
    elif rev_g > 0: score += 5

    # 가치평가 (20점)
    per = row.get("per")
    pbr = row.get("pbr")
    fcf = row.get("fcf_yield") or 0
    if per is not None and 0 < per < 25: score += 7
    elif per is not None and 0 < per < 40: score += 4
    if pbr is not None and 0 < pbr < 3: score += 7
    elif pbr is not None and 0 < pbr < 6: score += 4
    if fcf > 3: score += 6
    elif fcf > 1: score += 3

    # 기술적 (20점)
    above = row.get("above_200ma") or 0
    rs = row.get("rs_score") or 0
    if above == 1: score += 10
    if rs > 5: score += 10
    elif rs > 0: score += 6

    return min(round(score, 2), 100.0)


def _load_target_tickers(cursor, stale_only: bool = False, stale_before: str | None = None, limit: int | None = None) -> list[str]:
    """Load US tickers for the daily quote job.

    `stale_only` is important operationally: if a full run stops midway, the latest
    date can show one or two symbols while most tickers remain stale. This option
    lets the scheduler/operator repair only stale rows without burning Yahoo calls
    on already-fresh symbols.

    The auto cutoff must be the most recent *broadly covered* date, not the global
    MAX(date). Using the global MAX is a self-defeating ratchet: whichever handful
    of tickers ran last is already at the newest date, so MAX equals that newest
    date, and `latest_date < MAX` then matches only the laggards — never the bulk
    that is sitting one day behind. Measured on 2026-08-08 the nightly job
    alternated 22s / 202s / 18s / 197s / 10s / 200s, i.e. it refreshed the ~3,400
    ticker bulk only every *other* day, leaving us_price_history (and the
    us_factor_snapshot BUY/SELL signals derived from it) one session stale on the
    off days. Anchoring on the broadly covered date and looking one day past it
    selects the bulk every run while still skipping tickers already ahead, so a
    half-finished run still repairs correctly on the next pass.
    """
    if stale_only:
        cutoff = stale_before
        if not cutoff:
            universe = cursor.execute(
                "SELECT COUNT(*) FROM us_stock_meta WHERE ticker IS NOT NULL AND ticker<>''"
            ).fetchone()
            universe = int(universe[0]) if universe and universe[0] else 0
            broad_min = max(50, int(universe * 0.30))
            # 2026-08-23: date(MAX(date), '+1 day')는 SQLite 전용 2-arg date() —
            # PostgreSQL 라우팅 하에서 "function date(text, unknown) does not exist"로
            # 실패하고 있었음. MAX(date)만 얻어 Python에서 +1일 계산.
            row = cursor.execute(
                """
                SELECT MAX(date)
                FROM (
                    SELECT date
                    FROM us_price_history
                    GROUP BY date
                    HAVING COUNT(DISTINCT ticker) >= ?
                )
                """,
                (broad_min,),
            ).fetchone()
            cutoff = None
            if row and row[0]:
                cutoff = (
                    datetime.strptime(str(row[0])[:10], "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
            if not cutoff:
                # No broadly covered date yet (fresh DB) — fall back to global MAX.
                row = cursor.execute("SELECT MAX(date) FROM us_price_history").fetchone()
                cutoff = row[0] if row else None
        if cutoff:
            rows = cursor.execute(
                """
                WITH latest AS (
                  SELECT ticker, MAX(date) AS latest_date
                  FROM us_price_history
                  GROUP BY ticker
                )
                SELECT m.ticker
                FROM us_stock_meta m
                LEFT JOIN latest l ON l.ticker=m.ticker
                WHERE m.ticker IS NOT NULL AND m.ticker<>''
                  AND (l.latest_date IS NULL OR l.latest_date < ?)
                ORDER BY COALESCE(m.market_cap,0) DESC, m.ticker
                """,
                (cutoff,),
            ).fetchall()
        else:
            rows = cursor.execute(
                "SELECT ticker FROM us_stock_meta WHERE ticker IS NOT NULL AND ticker<>'' ORDER BY COALESCE(market_cap,0) DESC, ticker"
            ).fetchall()
    else:
        rows = cursor.execute(
            "SELECT ticker FROM us_stock_meta WHERE ticker IS NOT NULL AND ticker<>'' ORDER BY COALESCE(market_cap,0) DESC, ticker"
        ).fetchall()
    tickers = [r[0] for r in rows]
    if limit:
        tickers = tickers[: max(1, int(limit))]
    return tickers


def sync_us_quotes_and_factors(batch_size: int = 100, stale_only: bool = False,
                               stale_before: str | None = None, limit: int | None = None,
                               tickers_override: list[str] | None = None,
                               history_period: str = "2y", dry_run: bool = False):
    conn = get_db_connection()
    cursor = conn.cursor()

    # OHLCV 컬럼 보장
    cursor.execute("PRAGMA table_info(us_price_history)")
    cols = {c[1] for c in cursor.fetchall()}
    for col in ["open", "high", "low"]:
        if col not in cols:
            try:
                cursor.execute(f"ALTER TABLE us_price_history ADD COLUMN {col} REAL")
            except Exception:
                pass
    conn.commit()

    if tickers_override:
        tickers = list(tickers_override)
    else:
        tickers = _load_target_tickers(cursor, stale_only=stale_only, stale_before=stale_before, limit=limit)
    conn.close()

    if not tickers:
        logger.info("수집 대상 미국 종목이 없습니다.")
        return {"target_count": 0, "processed_count": 0, "dry_run": dry_run}

    logger.info(
        f"총 {len(tickers):,}개 미국 종목 대상 OHLCV & 팩터 수집 시작 "
        f"(배치 크기: {batch_size}, 기간: {history_period}, "
        f"stale_only={stale_only}, stale_before={stale_before})"
    )
    if dry_run:
        logger.info("건조 실행: yfinance 호출 및 데이터베이스 쓰기를 수행하지 않습니다.")
        return {"target_count": len(tickers), "processed_count": 0, "dry_run": True}

    total_batches = math.ceil(len(tickers) / batch_size)
    processed_count = 0
    for b_idx in range(total_batches):
        batch_tickers = tickers[b_idx * batch_size: (b_idx + 1) * batch_size]
        logger.info(f"[{b_idx+1}/{total_batches}] 배치 다운로드 중... ({len(batch_tickers)}종목)")

        try:
            # Daily factors need at least 252 sessions for the 200-day average
            # and one-year return. Re-downloading five years for every ticker
            # made the daily run stop partway through the US universe.
            df_data = yf.download(batch_tickers, period=history_period, group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False)

            conn = get_db_connection()
            c = conn.cursor()

            for tk in batch_tickers:
                try:
                    if len(batch_tickers) == 1:
                        if hasattr(df_data, "columns") and getattr(df_data.columns, "nlevels", 1) > 1:
                            if tk not in df_data.columns.get_level_values(0):
                                continue
                            df_tk = df_data[tk]
                        else:
                            df_tk = df_data
                    else:
                        if tk not in df_data.columns.get_level_values(0):
                            continue
                        df_tk = df_data[tk]

                    df_clean = df_tk.dropna(subset=["Close"])
                    if df_clean.empty:
                        continue

                    # ── OHLCV 적재 ──────────────────────────────
                    rows_to_insert = []
                    for idx, row in df_clean.iterrows():
                        d_str = str(idx)[:10]
                        c_val = _safe_float(row.get("Close"))
                        v_val = int(row["Volume"]) if "Volume" in row and not math.isnan(float(row["Volume"] or "nan")) else None
                        o_val = _safe_float(row.get("Open"))
                        h_val = _safe_float(row.get("High"))
                        l_val = _safe_float(row.get("Low"))
                        if c_val and c_val > 0:
                            rows_to_insert.append((tk, d_str, o_val, h_val, l_val, c_val, v_val))

                    if rows_to_insert:
                        c.executemany("""
                            INSERT OR REPLACE INTO us_price_history (ticker, date, open, high, low, close, volume)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, rows_to_insert)

                    # ── 팩터 계산 ──────────────────────────────
                    closes_asc = [r[5] for r in rows_to_insert if r[5] is not None]
                    highs_asc  = [r[3] for r in rows_to_insert if r[3] is not None]
                    lows_asc   = [r[4] for r in rows_to_insert if r[4] is not None]

                    if len(closes_asc) < 5:
                        processed_count += 1
                        continue

                    curr_p = closes_asc[-1]
                    latest_price_date = rows_to_insert[-1][1]

                    # 이동평균
                    ma5   = sum(closes_asc[-5:]) / 5.0 if len(closes_asc) >= 5 else None
                    ma20  = sum(closes_asc[-20:]) / 20.0 if len(closes_asc) >= 20 else None
                    ma60  = sum(closes_asc[-60:]) / 60.0 if len(closes_asc) >= 60 else None
                    ma200 = sum(closes_asc[-200:]) / 200.0 if len(closes_asc) >= 200 else None
                    above_200ma = 1 if (ma200 and curr_p > ma200) else 0

                    # 52주 고저
                    w52 = closes_asc[-252:] if len(closes_asc) >= 252 else closes_asc
                    h52 = max(w52) if highs_asc else max(w52)
                    l52 = min(w52) if lows_asc else min(w52)

                    # 수익률
                    def _ret(n):
                        if len(closes_asc) > n and closes_asc[-n - 1] > 0:
                            return (closes_asc[-1] - closes_asc[-n - 1]) / closes_asc[-n - 1] * 100.0
                        return None

                    ret_1m = _ret(21)
                    ret_3m = _ret(63)
                    ret_6m = _ret(126)
                    ret_1y = _ret(252)

                    # ATR14
                    atr14 = _compute_atr14(closes_asc)
                    atr_stop = (curr_p - 2.0 * atr14) if atr14 else None
                    atr_risk = ((curr_p - atr_stop) / curr_p * 100.0) if atr_stop else None

                    # RS 점수 (3M 초과 수익률 vs 시장 기준선)
                    rs_score = None
                    if len(closes_asc) >= 63:
                        stock_3m = (closes_asc[-1] - closes_asc[-63]) / closes_asc[-63] * 100.0
                        rs_score = stock_3m - MARKET_3M_BASELINE

                    # 재무 데이터 (최신 연간 기준)
                    # 2026-07-29 수정: SEC XBRL이 매출(duration)과 순이익 등을 서로 다른 context
                    # end 날짜로 태깅하는 경우가 있어, 같은 회계연도가 period_end만 며칠 다른
                    # 2개 행(하나는 revenue 有, 하나는 revenue NULL)으로 쪼개져 저장되는 사례가
                    # 109개 티커에서 확인됨(예: AAPL). 기존 "ORDER BY period_end DESC LIMIT 1
                    # OFFSET 1"은 이 중복행을 "작년"으로 잘못 집어 net_income_growth_yoy가
                    # 전부 0.0%로, revenue_growth_yoy는 NULL로 나오는 버그를 유발했음(직접 검증
                    # 확인: AAPL 실제 NI YoY는 약 +19.5%인데 0.0%로 표시되던 중). revenue IS NOT
                    # NULL 조건 + "진짜 1년 전"(300~430일 전) 윈도우로 수정해 재발 방지.
                    fin = c.execute("""
                        SELECT eps, bps, opm, per, pbr, operating_income, revenue, net_income, equity, assets, liabilities, period_end
                        FROM us_financial_data
                        WHERE ticker=? AND period_type='annual' AND revenue IS NOT NULL
                        ORDER BY period_end DESC LIMIT 1
                    """, (tk,)).fetchone()

                    fin_prev = None
                    if fin:
                        fin_prev = c.execute("""
                            SELECT revenue, operating_income, net_income
                            FROM us_financial_data
                            WHERE ticker=? AND period_type='annual' AND revenue IS NOT NULL
                              AND julianday(?) - julianday(period_end) BETWEEN 300 AND 430
                            ORDER BY period_end DESC LIMIT 1
                        """, (tk, fin[-1])).fetchone()

                    eps_val = _safe_float(fin[0]) if fin else None
                    bps_val = _safe_float(fin[1]) if fin else None
                    opm_val = _safe_float(fin[2]) if fin else None
                    per_val = _safe_float(fin[3]) if fin else None
                    pbr_val = _safe_float(fin[4]) if fin else None
                    opi_val = _safe_float(fin[5]) if fin else None
                    rev_val = _safe_float(fin[6]) if fin else None
                    ni_val  = _safe_float(fin[7]) if fin else None
                    eq_val  = _safe_float(fin[8]) if fin else None
                    ast_val = _safe_float(fin[9]) if fin else None
                    lib_val = _safe_float(fin[10]) if fin else None

                    # YoY 성장률
                    rev_growth = None
                    opi_growth = None
                    ni_growth  = None
                    if fin_prev:
                        prev_rev = _safe_float(fin_prev[0])
                        prev_opi = _safe_float(fin_prev[1])
                        prev_ni  = _safe_float(fin_prev[2])
                        if prev_rev and prev_rev != 0 and rev_val:
                            rev_growth = (rev_val - prev_rev) / abs(prev_rev) * 100.0
                        if prev_opi and prev_opi != 0 and opi_val:
                            opi_growth = (opi_val - prev_opi) / abs(prev_opi) * 100.0
                        if prev_ni and prev_ni != 0 and ni_val:
                            ni_growth = (ni_val - prev_ni) / abs(prev_ni) * 100.0

                    # ROE / ROA
                    roe_val = (ni_val / eq_val * 100.0) if (ni_val and eq_val and eq_val != 0) else None
                    roa_val = (ni_val / ast_val * 100.0) if (ni_val and ast_val and ast_val != 0) else None

                    # D/E ratio
                    de_val = (lib_val / eq_val) if (lib_val and eq_val and eq_val > 0) else None

                    # FCF yield (현금흐름 테이블)
                    # 2026-07-29(2차): us_financial_data와 동일한 SEC XBRL period_end 중복행
                    # 패턴이 us_cashflow_data에도 존재(128개 티커, operating_cf/free_cf가
                    # NULL인 "쪼개진" 최신 duplicate 행이 먼저 뽑혀 fcf_yield가 계산 가능한데도
                    # NULL로 나오는 사례 확인) — free_cf IS NOT NULL 조건 추가로 진짜 값 있는
                    # 행을 우선 선택.
                    cf = c.execute("""
                        SELECT free_cf FROM us_cashflow_data
                        WHERE ticker=? AND period_type='annual' AND free_cf IS NOT NULL
                        ORDER BY period_end DESC LIMIT 1
                    """, (tk,)).fetchone()
                    fcf_val = _safe_float(cf[0]) if cf else None
                    meta = c.execute(
                        "SELECT market_cap, sector, industry FROM us_stock_meta WHERE ticker=?",
                        (tk,),
                    ).fetchone()
                    mcap_val = _safe_float(meta[0]) if meta else None
                    sector_val = meta[1] if meta else None
                    industry_val = meta[2] if meta else None
                    fcf_yield = (fcf_val / mcap_val * 100.0) if (fcf_val and mcap_val and mcap_val > 0) else None

                    # Graham 내재가치
                    graham_int = None
                    graham_disc = None
                    if eps_val and bps_val and eps_val > 0 and bps_val > 0:
                        graham_int = math.sqrt(22.5 * eps_val * bps_val)
                        if curr_p > 0 and graham_int > 0:
                            graham_disc = (graham_int - curr_p) / graham_int * 100.0

                    # 종합 점수
                    factor_row = {
                        "return_1m": ret_1m, "return_3m": ret_3m,
                        "return_6m": ret_6m, "return_1y": ret_1y,
                        "op_margin": opm_val, "roe": roe_val,
                        "revenue_growth_yoy": rev_growth,
                        "per": per_val, "pbr": pbr_val,
                        "fcf_yield": fcf_yield,
                        "above_200ma": above_200ma, "rs_score": rs_score,
                    }
                    total_score = _calc_total_score(factor_row)
                    system_action = _compute_system_action(total_score, above_200ma, rs_score, ret_3m, graham_disc)

                    c.execute("""
                        INSERT OR REPLACE INTO us_factor_snapshot
                        (ticker, as_of_date, market_cap, sector, industry,
                         price, high_52w, low_52w,
                         ma5, ma20, ma60, ma200, above_200ma,
                         return_1m, return_3m, return_6m, return_1y,
                         atr14, atr_stop_loss, atr_risk_pct,
                         rs_score,
                         graham_intrinsic, graham_discount,
                         eps, bps, per, pbr,
                         op_margin, roe, roa,
                         revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy,
                         fcf_yield, debt_to_equity,
                         total_score, system_action,
                         updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    """, (
                        tk, latest_price_date, mcap_val, sector_val, industry_val,
                        curr_p, h52, l52,
                        ma5, ma20, ma60, ma200, above_200ma,
                        ret_1m, ret_3m, ret_6m, ret_1y,
                        atr14, atr_stop, atr_risk,
                        rs_score,
                        graham_int, graham_disc,
                        eps_val, bps_val, per_val, pbr_val,
                        opm_val, roe_val, roa_val,
                        rev_growth, opi_growth, ni_growth,
                        fcf_yield, de_val,
                        total_score, system_action,
                    ))

                    processed_count += 1

                except Exception as e:
                    logger.debug(f"{tk} 적재 실패: {e}")
                    continue

            conn.commit()
            conn.close()

        except Exception as ex:
            logger.warning(f"배치 {b_idx+1} 처리 중 오류 발생: {ex}")

    logger.info(f"미국 종목 OHLCV 및 팩터 적재 완료! 총 {processed_count:,}개 종목 처리됨.")
    return {"target_count": len(tickers), "processed_count": processed_count, "dry_run": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync US daily quotes and factor snapshots")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--stale-only", action="store_true", help="Only refresh tickers whose latest price date is stale")
    parser.add_argument("--stale-before", default=None, help="Refresh tickers with latest price before this YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tickers", default=None, help="Comma-separated ticker list to refresh only these (bypasses stale/limit selection)")
    parser.add_argument(
        "--period",
        default=os.getenv("US_DAILY_QUOTES_HISTORY_PERIOD", "2y"),
        help="Yahoo history window used for daily factor refresh (default: 2y)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the target ticker count without downloading or writing data")
    args = parser.parse_args()
    sync_us_quotes_and_factors(
        batch_size=max(1, args.batch_size),
        stale_only=args.stale_only,
        stale_before=args.stale_before,
        limit=args.limit,
        tickers_override=[t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None,
        history_period=args.period,
        dry_run=args.dry_run,
    )
